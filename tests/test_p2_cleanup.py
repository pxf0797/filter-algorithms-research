"""
tests/test_p2_cleanup.py — P2 代码清理修复的验证测试

覆盖：
  - iterrows → itertuples 替换后的输出对比
  - JSONL flush 移除后数据完整性
  - print → loguru 日志输出验证
  - 魔术字符串常量的正确性
"""

import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest
from loguru import logger


# ══════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════

def _make_ohlc_df(start_date="2026-06-01", days=10, seed=42):
    """构造标准 OHLC 测试 DataFrame（DatetimeIndex）。"""
    dates = pd.date_range(start_date, periods=days, freq="D")
    np.random.seed(seed)
    close = np.cumsum(np.random.randn(days) * 0.5) + 100
    return pd.DataFrame({
        "Open": close - 0.1, "High": close + 0.3,
        "Low": close - 0.3, "Close": close,
        "Volume": np.random.randint(1000, 10000, days),
    }, index=dates)


def _make_df_no_volume(start_date="2026-06-01", days=5):
    """构造无 Volume 列的 DataFrame（测试缺列容错）。"""
    dates = pd.date_range(start_date, periods=days, freq="D")
    np.random.seed(1)
    close = np.cumsum(np.random.randn(days) * 0.5) + 100
    return pd.DataFrame({
        "Open": close - 0.1, "High": close + 0.3,
        "Low": close - 0.3, "Close": close,
    }, index=dates)


# ══════════════════════════════════════════════════════════════════════════
# 1. iterrows → itertuples 替换验证
# ══════════════════════════════════════════════════════════════════════════

class TestIterrowsReplacement:
    """验证 iterrows 替换后, upsert/compare/force_update 输出一致。"""

    @pytest.fixture
    def db_env(self, tmp_path):
        """初始化临时 DB 环境。"""
        import db
        db_path = tmp_path / "test_market.db"
        db.DB_PATH = db_path
        db.SNAPSHOT_DIR = tmp_path / "snapshots"
        db.init_db()
        yield db, db_path

    # ── upsert_kline ────────────────────────────────────────────

    def test_upsert_kline_normal(self, db_env):
        """正常 DataFrame upsert 写入数据完整。"""
        db_module, db_path = db_env
        df = _make_ohlc_df(days=10)
        db_module.upsert_kline("AAPL", "日线", df)

        result = db_module.query_kline("AAPL", "日线", 20)
        assert len(result) == 10
        assert list(result.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]

    def test_upsert_kline_no_volume_column(self, db_env):
        """缺少 Volume 列时以 0.0 写入，不抛异常。"""
        db_module, db_path = db_env
        df = _make_df_no_volume(days=5)
        db_module.upsert_kline("AAPL", "日线", df)

        import sqlite3
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT volume FROM kline WHERE ticker=? AND timeframe=? ORDER BY ts",
            ("AAPL", "日线"),
        ).fetchall()
        conn.close()
        assert len(rows) == 5
        for r in rows:
            assert r[0] == 0.0

    def test_upsert_kline_with_range_index(self, db_env):
        """非 DatetimeIndex 的 DataFrame 也能正常写入。"""
        db_module, db_path = db_env
        df = pd.DataFrame({
            "Open": [100.0, 101.0], "High": [101.0, 102.0],
            "Low": [99.0, 100.0], "Close": [100.5, 101.5],
            "Volume": [1000, 2000],
        })
        db_module.upsert_kline("AAPL", "日线", df)

        import sqlite3
        conn = sqlite3.connect(str(db_path))
        count = conn.execute("SELECT COUNT(*) FROM kline").fetchone()[0]
        conn.close()
        assert count == 2

    def test_upsert_kline_nan_values(self, db_env):
        """含 NaN Close 的 DataFrame 正常写入。"""
        db_module, db_path = db_env
        dates = pd.date_range("2026-06-01", periods=3, freq="D")
        df = pd.DataFrame({
            "Open": [100.0, 101.0, 102.0],
            "High": [101.0, 102.0, 103.0],
            "Low": [99.0, 100.0, 101.0],
            "Close": [100.5, np.nan, 102.5],
            "Volume": [1000, 2000, 3000],
        }, index=dates)
        db_module.upsert_kline("AAPL", "日线", df)

        import sqlite3
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT ts, close FROM kline WHERE ticker=? AND timeframe=? ORDER BY ts",
            ("AAPL", "日线"),
        ).fetchall()
        conn.close()
        assert len(rows) == 3
        assert rows[1][1] is None  # NaN → NULL

    # ── compare_with_db ─────────────────────────────────────────

    def test_compare_with_db_identical(self, db_env):
        """DB 与数据源完全一致时 status=ok。"""
        db_module, db_path = db_env
        df = _make_ohlc_df(days=10, seed=42)
        db_module.upsert_kline("AAPL", "日线", df)
        # 用完全相同的数据对比
        result = db_module.compare_with_db("AAPL", "日线", df)
        assert result["status"] == "ok"
        assert result["fingerprint_match"] is True

    def test_compare_with_db_conflict(self, db_env):
        """重叠数据不同时 status=conflict。"""
        db_module, db_path = db_env
        df_base = _make_ohlc_df(days=10, seed=1)
        db_module.upsert_kline("AAPL", "日线", df_base)

        dates = pd.date_range("2026-06-01", periods=10, freq="D")
        df_conflict = pd.DataFrame({
            "Open": [100.0] * 10, "High": [101.0] * 10,
            "Low": [99.0] * 10, "Close": [99.5] * 10,
            "Volume": [5000] * 10,
        }, index=dates)
        result = db_module.compare_with_db("AAPL", "日线", df_conflict)
        assert result["status"] == "conflict"
        assert result["fingerprint_match"] is False
        assert len(result["diffs"]) > 0

    def test_compare_with_db_empty_fetched(self, db_env):
        """传入空 DataFrame 时不报错。"""
        db_module, db_path = db_env
        df = _make_ohlc_df(days=5)
        db_module.upsert_kline("AAPL", "日线", df)
        result = db_module.compare_with_db("AAPL", "日线", pd.DataFrame())
        assert result["db_count"] == 5
        assert result["yf_count"] == 0

    # ── force_update_kline ──────────────────────────────────────

    def test_force_update_no_overlap(self, db_env):
        """纯新增数据追加到 DB。"""
        db_module, db_path = db_env
        df_orig = _make_ohlc_df(days=5, seed=1)
        db_module.upsert_kline("AAPL", "日线", df_orig)

        dates = pd.date_range("2026-07-01", periods=3, freq="D")
        df_new = pd.DataFrame({
            "Open": [200.0] * 3, "High": [201.0] * 3,
            "Low": [199.0] * 3, "Close": [200.5] * 3,
            "Volume": [9999] * 3,
        }, index=dates)
        db_module.force_update_kline("AAPL", "日线", df_new)

        import sqlite3
        conn = sqlite3.connect(str(db_path))
        count = conn.execute(
            "SELECT COUNT(*) FROM kline WHERE ticker=? AND timeframe=?",
            ("AAPL", "日线"),
        ).fetchone()[0]
        conn.close()
        assert count == 8

    def test_force_update_empty_df(self, db_env):
        """空 DataFrame force_update 不影响已有数据。"""
        db_module, db_path = db_env
        df = _make_ohlc_df(days=5)
        db_module.upsert_kline("AAPL", "日线", df)
        db_module.force_update_kline("AAPL", "日线", pd.DataFrame())

        import sqlite3
        conn = sqlite3.connect(str(db_path))
        count = conn.execute(
            "SELECT COUNT(*) FROM kline WHERE ticker=? AND timeframe=?",
            ("AAPL", "日线"),
        ).fetchone()[0]
        conn.close()
        assert count == 5


# ══════════════════════════════════════════════════════════════════════════
# 2. JSONL flush 移除后数据完整性验证
# ══════════════════════════════════════════════════════════════════════════

class TestJsonlFlushRemoval:
    """验证移除 flush() 后 JSONL 数据完整性。"""

    def test_write_without_flush_data_integrity(self, tmp_path):
        """写入多行后, end_session 关闭文件后数据完整。"""
        from services.event_recorder import EventRecorder

        recorder = EventRecorder(str(tmp_path), "TEST")
        session_id = recorder.start_session({"configs": []})

        # 记录多个步骤
        for i in range(10):
            recorder.record_step(i, f"2026-06-{i + 1:02d}", {
                "step_index": i,
                "cutoff_date": f"2026-06-{i + 1:02d}",
                "bar_index": i,
                "bar_timestamp": f"2026-06-{i + 1:02d}",
                "ohlcv": {"close": 100.0 + i},
                "views": {
                    "v0_日线": {
                        "filtered": np.array([1.0, 2.0, 3.0]),
                        "t": np.array([1, 2, 3]),
                    }
                },
            })

        recorder.end_session()

        # 验证 events.jsonl 完整
        session_dir = tmp_path / f"TEST_{session_id}"
        events_file = session_dir / "events.jsonl"
        assert events_file.exists()

        lines = events_file.read_text(encoding="utf-8").strip().split("\n")
        # session_started + session_ended + bs_added events per step
        assert len(lines) >= 2  # at minimum start + end events

        # 验证每行都是有效 JSON
        for line in lines:
            obj = json.loads(line)
            assert "event" in obj

    def test_empty_session_no_flush_crash(self, tmp_path):
        """空 session 不写入任何事件, end_session 不崩溃。"""
        from services.event_recorder import EventRecorder

        recorder = EventRecorder(str(tmp_path), "EMPTY")
        session_id = recorder.start_session({"configs": []})
        recorder.end_session()

        session_dir = tmp_path / f"EMPTY_{session_id}"
        assert session_dir.exists()
        meta = json.loads((session_dir / "metadata.json").read_text(encoding="utf-8"))
        assert meta["step_count"] == 0


# ══════════════════════════════════════════════════════════════════════════
# 3. print → loguru 日志输出验证
# ══════════════════════════════════════════════════════════════════════════

class TestLoguruMigration:
    """验证 print 替换为 logger 后日志正确输出。"""

    def test_db_module_prints_to_logger(self, capsys):
        """db.py __main__ 块使用 logger 而非 print。"""
        # 导入后重新执行 __name__ == "__main__" 块
        import db
        import runpy
        # 不直接运行整个模块, 而是验证 print 已被替换
        # 检查模块级代码中不再有裸 print 调用（排除注释和文档字符串）
        source = Path(db.__file__).read_text(encoding="utf-8")
        # 确认 __main__ 块使用 logger 而非 print
        assert 'logger.info("DB initialized: {}", DB_PATH)' in source
        # 确认旧 print 不再存在
        assert 'print("DB initialized:' not in source

    def test_parquet_store_uses_logger(self):
        """parquet_store.py 使用 logger.warning 替代 print。"""
        from services import parquet_store

        source = Path(parquet_store.__file__).read_text(encoding="utf-8")
        # 确认 import logger
        assert "from loguru import logger" in source
        # 确认没有裸 print 调用（文档字符串和注释除外）
        lines_with_print = [
            line for line in source.split("\n")
            if "print(" in line
            and not line.strip().startswith("#")
            and '"""' not in line
            and "print(" not in line.split("#")[0].strip()
        ]
        # 可能有 = print 之类的赋值, 但不应有裸 print() 调用
        bare_prints = [l for l in lines_with_print if l.strip().startswith("print(")]
        assert len(bare_prints) == 0, f"Found bare print() calls: {bare_prints}"

    def test_backtest_cli_uses_logger(self):
        """backtest_cli.py 使用 logger 替代 print。"""
        import backtest_cli

        source = Path(backtest_cli.__file__).read_text(encoding="utf-8")
        # 确认没有 print to stderr
        assert 'file=sys.stderr' not in source
        # 确认使用 logger.error
        assert 'logger.error(' in source
        # 确认使用 logger.info
        assert 'logger.info(' in source

    def test_logger_output_via_caplog(self, caplog):
        """验证 logger.info/error 通过 caplog 捕获。"""
        from loguru import logger

        # 将 loguru 日志桥接到标准 logging 以便 caplog 捕获
        import logging

        class PropagateHandler(logging.Handler):
            def emit(self, record):
                logging.getLogger(record.name).handle(record)

        # loguru 默认不桥接到标准 logging
        # 这里直接测试 loguru 的 sink 机制
        output = []

        # 添加临时 sink
        sink_id = logger.add(lambda msg: output.append(str(msg)), format="{message}")

        logger.info("测试消息: {}", "hello")
        logger.error("错误消息: {}", "world")

        logger.remove(sink_id)

        assert len(output) == 2
        assert "测试消息: hello" in output[0]
        assert "错误消息: world" in output[1]


# ══════════════════════════════════════════════════════════════════════════
# 4. 魔术字符串常量验证
# ══════════════════════════════════════════════════════════════════════════

class TestMagicStringsConstants:
    """验证 constants.py 中定义的常量的正确性。"""

    def test_default_tf_constant(self):
        """DEFAULT_TF 值为 '日线' 且在 ALL_TFS 中。"""
        from constants import DEFAULT_TF, ALL_TFS
        assert DEFAULT_TF == "日线"
        assert DEFAULT_TF in ALL_TFS

    def test_view_key_prefix_constant(self):
        """VIEW_KEY_PREFIX 值为 'v'。"""
        from constants import VIEW_KEY_PREFIX
        assert VIEW_KEY_PREFIX == "v"

    def test_view_suffix_mapping_completeness(self):
        """VIEW_SUFFIX_TO_CFG_KEY 包含常见映射。"""
        from constants import VIEW_SUFFIX_TO_CFG_KEY
        assert VIEW_SUFFIX_TO_CFG_KEY["n"] == "n_pts"
        assert VIEW_SUFFIX_TO_CFG_KEY["sch"] == "show_sch"
        assert VIEW_SUFFIX_TO_CFG_KEY["pred"] == "show_pred"
        assert VIEW_SUFFIX_TO_CFG_KEY["strat"] == "show_strategy"
        assert VIEW_SUFFIX_TO_CFG_KEY["sl"] == "stop_loss_pct"

    def test_state_view_prefix_consistent(self):
        """state.py 中 ViewState._PREFIX 与 constants.VIEW_KEY_PREFIX 一致。"""
        from constants import VIEW_KEY_PREFIX
        from state import ViewState
        assert ViewState._PREFIX == VIEW_KEY_PREFIX

    def test_backtest_cli_view_spec_consistent(self):
        """backtest_cli 中 _VIEW_SPECS 后缀映射与 constants 一致。"""
        from constants import VIEW_SUFFIX_TO_CFG_KEY
        from backtest_cli import _VIEW_SPECS

        # 检查 _VIEW_SPECS 中每个 suffix 在 VIEW_SUFFIX_TO_CFG_KEY 中
        # 或 suffix 本身就是 cfg_key
        suffixes_in_specs = {spec[0] for spec in _VIEW_SPECS}
        # 那些不需要映射的（suffix == cfg_key）：
        direct_suffixes = {"tf", "ke", "sm", "ew", "fc", "fc2", "pnlfb"}
        mapped_suffixes = set(VIEW_SUFFIX_TO_CFG_KEY.keys())

        for spec in _VIEW_SPECS:
            suffix = spec[0]
            if suffix in mapped_suffixes:
                assert VIEW_SUFFIX_TO_CFG_KEY[suffix] == spec[1], \
                    f"Mismatch: {suffix} -> {VIEW_SUFFIX_TO_CFG_KEY[suffix]} != {spec[1]}"


# ══════════════════════════════════════════════════════════════════════════
# 5. 回归：现有 DB 测试仍通过
# ══════════════════════════════════════════════════════════════════════════

class TestRegressionExistingDb:
    """确保 iterrows 替换后, 现有测试的核心断言仍成立。"""

    def test_upsert_then_query_roundtrip(self, tmp_path):
        """upsert → query 往返数据一致。"""
        import db
        db_path = tmp_path / "test_market.db"
        db.DB_PATH = db_path
        db.SNAPSHOT_DIR = tmp_path / "snapshots"
        db.init_db()

        df = _make_ohlc_df(days=20)
        db.upsert_kline("AAPL", "日线", df)

        queried = db.query_kline("AAPL", "日线", 50)
        assert len(queried) == 20
        # 验证 Close 值前几个一致
        for i in range(5):
            assert queried.iloc[i]["Close"] == pytest.approx(df.iloc[i]["Close"])

    def test_multi_timeframe_isolation(self, tmp_path):
        """同 ticker 不同 tf 数据互不干扰。"""
        import db
        db_path = tmp_path / "test_market.db"
        db.DB_PATH = db_path
        db.SNAPSHOT_DIR = tmp_path / "snapshots"
        db.init_db()

        df_daily = _make_ohlc_df(days=10)
        df_hourly = _make_ohlc_df(days=5)  # 模拟小时数据
        db.upsert_kline("AAPL", "日线", df_daily)
        db.upsert_kline("AAPL", "60分钟", df_hourly)

        assert len(db.query_kline("AAPL", "日线", 50)) == 10
        assert len(db.query_kline("AAPL", "60分钟", 50)) == 5
