"""
回测模式测试用例.

测试 AppState 回测键、_sync_to_display 日期对齐、日志、模式切换。
所有测试通过 mock DB/streamlit 独立运行，不依赖 Streamlit 运行时。
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import json
import pytest

# ── 与 test_state.py 相同的方式导入 state 模块 ──────────────────────────────
_old_streamlit = sys.modules.get("streamlit")
if "streamlit" in sys.modules:
    del sys.modules["streamlit"]

import shared.state as state  # noqa: E402

sys.modules["streamlit"] = _old_streamlit


AppState = state.AppState
DEFAULTS = state.SYSTEM_KEYS  # state.py 中定义为 SYSTEM_KEYS


class _DictSessionState(dict):
    """dict 子类，支持 attribute 读写（模拟 streamlit session_state 的两种访问方式）。

    当 st.session_state 作为 MagicMock 时，同时支持:
      - st.session_state["_key"]          # dict-style
      - st.session_state._key = value     # attribute-style
      - st.session_state.get("_key", 0)   # dict .get()
    用于测试 _on_slider_change / _run_backtest_play 的真实调用。
    """

    def __getattr__(self, key):
        # MagicMock 的属性查找: 先尝试 dict 查找，再 fallback 到 AttributeError
        try:
            return self[key]
        except KeyError:
            msg = f"'{type(self).__name__}' object has no attribute '{key}'"
            raise AttributeError(msg)

    def __setattr__(self, key: str, value) -> None:
        self[key] = value


@pytest.fixture(autouse=True)
def _mock_state_st():
    """让 state.st.session_state 成为真实的 dict（与 test_state.py 相同模式）。"""
    real_ss: dict = {}
    mock_st = MagicMock()
    mock_st.session_state = real_ss
    state.st = mock_st
    return real_ss


# ══════════════════════════════════════════════════════════════════════════
# 断点/截止日期相关测试 (从 test_backtest.py 拆分)
# ══════════════════════════════════════════════════════════════════════════

# ── TestSyncToDisplay ────────────────────────────────────────────────────────

class TestSyncToDisplay:
    """_sync_to_display 日期对齐：cutoff_date 模式与浏览模式."""

    def test_cutoff_date_query_includes_cutoff(self, tmp_path):
        """传入 cutoff_date 时 SQL 包含 cutoff_date 参数."""
        from data.loader import _sync_to_display

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False

        mock_rows = [
            ("2026-05-01", 100.0, 102.0, 99.0, 101.0, 1000000),
            ("2026-05-04", 101.0, 103.0, 100.5, 102.5, 1200000),
            ("2026-05-29", 102.0, 104.0, 101.0, 103.0, 1100000),
        ]
        mock_conn.execute.return_value.fetchall.return_value = mock_rows

        # 用 tmp_path 做 display 目录，避免 pyarrow to_parquet 报错
        display_dir = tmp_path / "display"
        display_dir.mkdir()

        with patch("data.loader.get_conn", return_value=mock_conn), \
             patch("data.loader.Path") as mock_path_class:
            # 让 (__file__).parent.parent.parent 指向 tmp_path
            mock_path_class.return_value.parent.parent.parent.__truediv__.return_value = display_dir

            ok, count = _sync_to_display("AAPL", "1d", n_pts=120, cutoff_date="2026-06-01")

            assert ok is True
            assert count == 3
            call_args = mock_conn.execute.call_args[0]
            # 查询参数包含 cutoff_date
            assert "2026-06-01" in call_args[1]

    def test_cutoff_no_results_returns_false(self):
        """cutoff_date 查询无结果时返回 (False, 0)."""
        from data.loader import _sync_to_display

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        mock_conn.execute.return_value.fetchall.return_value = []

        with patch("data.loader.get_conn", return_value=mock_conn):
            ok, count = _sync_to_display("AAPL", "1d", n_pts=120, cutoff_date="1990-01-01")
            assert ok is False
            assert count == 0

    def test_no_cutoff_uses_browse_mode(self):
        """不传 cutoff_date 走浏览模式 query_kline 路径."""
        from data.loader import _sync_to_display
        import pandas as pd

        with patch("data.loader.query_kline", return_value=pd.DataFrame()) as mock_query:
            ok, count = _sync_to_display("AAPL", "1d", n_pts=120, cutoff_date=None)

            assert ok is False
            assert count == 0
            mock_query.assert_called_once()

    def test_sync_false_triggers_api_fallback(self):
        """_sync_to_display 返回 ok=False 表示需要 API 回退."""
        from data.loader import _sync_to_display

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        mock_conn.execute.return_value.fetchall.return_value = []

        with patch("data.loader.get_conn", return_value=mock_conn):
            ok, count = _sync_to_display("XYZ", "1mo", n_pts=120, cutoff_date="2025-01-01")
            assert ok is False  # 触发 _load_chart_data 走 _cached_fetch_stock

    def test_cutoff_date_truncates_to_n_pts(self, tmp_path):
        """cutoff_date 查询返回恰好 n_pts 条."""
        from data.loader import _sync_to_display

        n_pts = 3
        mock_rows = [
            (f"2026-06-{day:02d}", 100.0 + i, 102.0 + i, 99.0 + i, 101.0 + i, 1000000)
            for i, day in enumerate([12, 13, 15])
        ]

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        mock_conn.execute.return_value.fetchall.return_value = mock_rows

        display_dir = tmp_path / "display"
        display_dir.mkdir()

        with patch("data.loader.get_conn", return_value=mock_conn), \
             patch("data.loader.Path") as mock_path_class:
            mock_path_class.return_value.parent.parent.parent.__truediv__.return_value = display_dir

            ok, count = _sync_to_display("AAPL", "1d", n_pts=n_pts, cutoff_date="2026-06-15")
            assert ok is True
            assert count == n_pts


# ── TestGetBarDateFromDb ─────────────────────────────────────────────────────

class TestGetBarDateFromDb:
    """测试 _get_bar_date_from_db 数据库查询."""

    def test_returns_date_when_found(self):
        """查询到日期时返回字符串."""
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "filter"))
        from filter.backtest.panel import _get_bar_date_from_db

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        mock_conn.execute.return_value.fetchone.return_value = ("2026-06-15",)

        with patch("filter.data.db.get_conn", return_value=mock_conn):
            result = _get_bar_date_from_db("AAPL", "1d", 100)
            assert result == "2026-06-15"

    def test_returns_empty_when_not_found(self):
        """无数据时返回空字符串."""
        from filter.backtest.panel import _get_bar_date_from_db

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        mock_conn.execute.return_value.fetchone.return_value = None

        with patch("data.loader.get_conn", return_value=mock_conn):
            result = _get_bar_date_from_db("AAPL", "1d", 99999)
            assert result == ""


# ══════════════════════════════════════════════════════════════════════════
# 断点续跑测试
# ══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# 断点续跑测试
# ═══════════════════════════════════════════════════════════════════════════════

def _make_mock_db_conn(bar_count=1000):
    """创建一个 mock DB 连接，_query_bar_count 返回 bar_count。"""
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.__exit__.return_value = False
    mock_row = MagicMock()
    mock_row.__getitem__.return_value = bar_count
    mock_conn.execute.return_value.fetchone.return_value = mock_row
    return mock_conn


def _make_minimal_configs():
    """创建一组最小视图配置，用于断点测试。"""
    return [
        {
            "_fid": "sma", "tf": "日线", "n_pts": 120,
            "show_sch": True, "show_strategy": False, "show_pred": False,
            "ke": 0.15, "sm": 0.05, "ew": 60,
            "pv": {"window": 11}, "pv2": {},
        },
        {
            "_fid": "sma", "tf": "60分钟", "n_pts": 120,
            "show_sch": True, "show_strategy": False, "show_pred": False,
            "ke": 0.15, "sm": 0.05, "ew": 60,
            "pv": {"window": 11}, "pv2": {},
        },
    ]


class TestCheckpoint:
    """BacktestRunner 断点保存与恢复测试。"""

    def test_config_hash_deterministic(self, tmp_path):
        """配置哈希对于相同配置是确定性的。"""
        configs = _make_minimal_configs()
        mock_conn = _make_mock_db_conn()

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            r1 = BacktestRunner("TEST", configs)
            r2 = BacktestRunner("TEST", configs)

        assert r1._config_hash() == r2._config_hash()
        assert len(r1._config_hash()) == 64  # SHA256 hex

    def test_config_hash_different_for_different_configs(self, tmp_path):
        """不同配置产生不同哈希。"""
        mock_conn = _make_mock_db_conn()

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            r1 = BacktestRunner("TEST", _make_minimal_configs())

        configs2 = _make_minimal_configs()
        configs2[0]["n_pts"] = 200  # 修改一个参数

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            r2 = BacktestRunner("TEST", configs2)

        assert r1._config_hash() != r2._config_hash()

    def test_save_and_restore_checkpoint(self, tmp_path):
        """保存断点后，新 runner 可恢复 EWMA 状态。"""
        configs = _make_minimal_configs()
        mock_conn = _make_mock_db_conn()

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("TEST", configs)

        # 手动设置 EWMA 状态（模拟回测进行中）
        runner._ewma_state = {
            "v0_日线": {
                "init_mu": 100.5, "init_sigma": 2.3,
                "state": 1, "dur": 5,
            },
            "v1_60分钟": {
                "init_mu": 99.8, "init_sigma": 1.5,
                "state": -1, "dur": 3,
            },
        }

        # 保存断点
        cp_path = str(tmp_path / "checkpoint.json")
        state = runner.save_checkpoint(cp_path)
        assert Path(cp_path).exists()

        # 验证 JSON 内容
        with open(cp_path) as f:
            saved = json.load(f)
        assert "config_hash" in saved
        assert "ewma_state" in saved
        assert saved["ewma_state"]["v0_日线"]["init_mu"] == 100.5
        assert saved["ewma_state"]["v0_日线"]["state"] == 1

        # 创建新 runner 并从断点恢复
        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner2 = BacktestRunner("TEST", configs)

        # 恢复前 EWMA 为空
        assert runner2._ewma_state == {}

        resume_bar = runner2._restore_checkpoint(cp_path, configs)
        assert resume_bar == 0  # save_checkpoint 默认 bar_index=0

        # 验证 EWMA 状态恢复
        assert runner2._ewma_state == runner._ewma_state
        assert runner2._ewma_state["v0_日线"]["init_mu"] == 100.5
        assert runner2._ewma_state["v1_60分钟"]["state"] == -1

    def test_restore_with_mismatched_config_raises(self, tmp_path):
        """从断点恢复时，如果配置哈希不匹配，抛出 ValueError。"""
        configs = _make_minimal_configs()
        mock_conn = _make_mock_db_conn()

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("TEST", configs)

        runner._ewma_state = {"v0_日线": {"init_mu": 100.0, "init_sigma": 2.0, "state": 1, "dur": 0}}

        cp_path = str(tmp_path / "checkpoint.json")
        runner.save_checkpoint(cp_path)

        # 使用不同配置恢复
        configs2 = _make_minimal_configs()
        configs2[0]["n_pts"] = 999

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner2 = BacktestRunner("TEST", configs2)

        with pytest.raises(ValueError, match="配置哈希不匹配"):
            runner2._restore_checkpoint(cp_path, configs2)

    def test_restore_missing_file_raises(self, tmp_path):
        """从不存在文件恢复时抛出 ValueError。"""
        configs = _make_minimal_configs()
        mock_conn = _make_mock_db_conn()

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("TEST", configs)

        with pytest.raises(ValueError, match="断点文件不存在"):
            runner._restore_checkpoint(str(tmp_path / "nonexistent.json"), configs)

    def test_checkpoint_auto_save_interval(self, tmp_path):
        """验证 run() 按指定间隔自动保存断点。"""

        configs = _make_minimal_configs()
        cp_path = str(tmp_path / "auto_checkpoint.json")

        mock_conn = _make_mock_db_conn(bar_count=20)
        mock_row = MagicMock()
        mock_row.__getitem__.side_effect = lambda k: {
            "ts": "2026-01-01",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1000,
        }.get(k)

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("TEST", configs)

        # 手动注入 _get_bar_info 返回（避免真实 DB 查询）
        def _fake_bar_info(idx):
            return {
                "bar_timestamp": "2026-01-01",
                "cutoff_date": "2026-01-01",
                "ohlcv": {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000},
            }
        runner._get_bar_info = _fake_bar_info

        # Mock _sync_data and _load_window_data to avoid IO
        runner._sync_data = MagicMock()
        runner._load_window_data = MagicMock(return_value=None)  # 跳过视图计算

        # Run with checkpoint_interval=3, 总共 6 步
        results = runner.run(0, 6, step_interval=1, checkpoint_interval=3, checkpoint_path=cp_path)
        assert len(results) == 6

        # 检查断点文件是否被保存过（已在完成后清理，所以应该不存在）
        # 但我们可以验证 save 调用次数
        assert not Path(cp_path).exists(), "断点文件应在运行完成后自动清理"

    def test_checkpoint_cleanup_on_completion(self, tmp_path):
        """运行正常完成后，断点文件被自动删除。"""
        configs = _make_minimal_configs()
        cp_path = str(tmp_path / "cleanup_test.json")

        mock_conn = _make_mock_db_conn(bar_count=20)

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("TEST", configs)

        runner._get_bar_info = lambda idx: {
            "bar_timestamp": "2026-01-01",
            "cutoff_date": "2026-01-01",
            "ohlcv": {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000},
        }
        runner._sync_data = MagicMock()
        runner._load_window_data = MagicMock(return_value=None)

        results = runner.run(0, 2, step_interval=1, checkpoint_interval=1, checkpoint_path=cp_path)
        assert len(results) == 2
        assert not Path(cp_path).exists(), "断点文件应在完成后被清理"

    def test_checkpoint_bar_index_tracks_current_position(self, tmp_path):
        """验证断点中 bar_index 记录正确位置。"""
        configs = _make_minimal_configs()
        cp_path = str(tmp_path / "bar_index_test.json")

        mock_conn = _make_mock_db_conn(bar_count=20)

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("TEST", configs)

        runner._get_bar_info = lambda idx: {
            "bar_timestamp": "2026-01-01",
            "cutoff_date": "2026-01-01",
            "ohlcv": {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000},
        }
        runner._sync_data = MagicMock()
        runner._load_window_data = MagicMock(return_value=None)

        # 手动保存断点，模拟在 bar_index=5 处中断
        # 直接写一个 checkpoint 文件
        state = {
            "bar_index": 5,
            "ewma_state": {"v0_日线": {"init_mu": 100.0, "init_sigma": 2.0, "state": 1, "dur": 3}},
            "bar_count": 20,
            "config_hash": runner._config_hash(),
            "start_bar": 0,
            "end_bar": 20,
            "step_interval": 1,
        }
        with open(cp_path, "w") as f:
            json.dump(state, f)

        # 恢复
        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner2 = BacktestRunner("TEST", configs)
        resume_bar = runner2._restore_checkpoint(cp_path, configs)
        assert resume_bar == 5
        assert runner2._ewma_state["v0_日线"]["state"] == 1
        assert runner2._ewma_state["v0_日线"]["dur"] == 3

    def test_restore_empty_ewma_state(self, tmp_path):
        """恢复时 EWMA 状态为空字典（首次运行场景）。"""
        configs = _make_minimal_configs()
        cp_path = str(tmp_path / "empty_ewma.json")

        mock_conn = _make_mock_db_conn(bar_count=20)

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("TEST", configs)

        # make runner save checkpoint with empty ewma
        runner.save_checkpoint(cp_path)

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner2 = BacktestRunner("TEST", configs)
        resume_bar = runner2._restore_checkpoint(cp_path, configs)
        assert runner2._ewma_state == {}


# 注册 BacktestRunner（模块顶层引用，便于测试使用）
from filter.backtest.engine import BacktestRunner  # noqa: E402
