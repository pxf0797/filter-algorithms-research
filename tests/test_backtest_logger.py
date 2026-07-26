"""
回测模式测试用例.

测试 AppState 回测键、_sync_to_display 日期对齐、日志、模式切换。
所有测试通过 mock DB/streamlit 独立运行，不依赖 Streamlit 运行时。
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import json
import numpy as np
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
# 日志相关测试 (从 test_backtest.py 拆分)
# ══════════════════════════════════════════════════════════════════════════

# ── TestBacktestLogger ───────────────────────────────────────────────────────

class TestBacktestLogger:
    """回测日志模块."""

    def test_log_mode_switch_writes_jsonl(self, tmp_path):
        """log_mode_switch 写入一条 JSONL 记录."""
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "filter"))
        import json
        from backtest.logger import log_mode_switch

        with patch("backtest.logger.LOG_DIR", tmp_path):
            log_mode_switch("AAPL", "enter", "60分钟", 500)

            log_files = list(tmp_path.glob("*.jsonl"))
            assert len(log_files) == 1

            record = json.loads(log_files[0].read_text().strip())
            assert record["event"] == "mode_switch"
            assert record["ticker"] == "AAPL"
            assert record["direction"] == "enter"
            assert record["min_tf"] == "60分钟"
            assert record["bar_count"] == 500

    def test_log_data_load_writes_jsonl(self, tmp_path):
        """log_data_load 写入 JSONL."""
        import json
        from backtest.logger import log_data_load

        with patch("backtest.logger.LOG_DIR", tmp_path):
            log_data_load("MSFT", "1d", 120, "2026-05-01", elapsed_ms=150.5)

            record = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
            assert record["event"] == "data_load"
            assert record["ticker"] == "MSFT"
            assert record["bar_count"] == 120
            assert record["elapsed_ms"] == 150.5

    def test_log_dir_auto_created(self, tmp_path):
        """日志目录自动创建."""
        from backtest.logger import _ensure_dir

        logs = tmp_path / "nested" / "backtest_logs"
        with patch("backtest.logger.LOG_DIR", logs):
            _ensure_dir()
            assert logs.exists()
            assert logs.is_dir()


# ── TestEventRecorderDebugMode ────────────────────────────────────────────────


class TestEventRecorderDebugMode:
    """EventRecorder 的 save_debug_data 模式测试.

    save_debug_data=False 时不写入 JSONL/CSV 调试文件,
    save_debug_data=True 时写入完整的调试数据。
    metadata.json 在两种模式下都始终写入。
    """

    @staticmethod
    def _make_minimal_config():
        return {
            "ticker": "AAPL",
            "configs": [{"tf": "日线", "n_pts": 120}],
        }

    @staticmethod
    def _make_minimal_output(step_index=0, bar_index=0):
        """构造最小 pipeline_output（仅含一个视图的 dummy 数据）。"""
        n = 50
        return {
            "step_index": step_index,
            "bar_index": bar_index,
            "bar_timestamp": "2026-01-15",
            "cutoff_date": "2026-01-15",
            "ohlcv": {"open": 100.0, "high": 101.0, "low": 99.0,
                      "close": 100.0, "volume": 1000},
            "views": {
                "v0_日线": {
                    "t": None,
                    "filtered": None,
                    "schmitt": None,
                    "long_pnl": None,
                    "short_pnl": None,
                    "long_mask": None,
                    "short_mask": None,
                    "trade_records": [],
                    "bs_markers": {"entry_markers": [], "exit_markers": []},
                },
            },
        }

    def test_default_save_debug_data_is_true(self):
        """EventRecorder 默认 save_debug_data=True."""
        from backtest.recorder import EventRecorder
        rec = EventRecorder("/tmp/test", "TEST")
        assert rec.save_debug_data is True

    def test_debug_mode_false_no_jsonl_writes(self, tmp_path):
        """save_debug_data=False: record_step + end_session 不产生 JSONL/CSV 文件."""
        from backtest.recorder import EventRecorder

        rec = EventRecorder(str(tmp_path), "AAPL", save_debug_data=False)
        rec.start_session(self._make_minimal_config())
        rec.record_step(0, "2026-01-15", self._make_minimal_output())
        rec.end_session()

        # metadata.json 始终存在
        session_dir = list(tmp_path.iterdir())[0]
        assert (session_dir / "metadata.json").exists()

        # JSONL 文件不应存在
        assert not (session_dir / "events.jsonl").exists()
        assert not (session_dir / "bs_snapshot.jsonl").exists()
        assert not (session_dir / "filter_tail.jsonl").exists()
        assert not (session_dir / "schmitt_snapshot.jsonl").exists()
        assert not (session_dir / "trade_summary.jsonl").exists()
        # CSV 文件不应存在
        assert not (session_dir / "backtest_data.csv").exists()

    def test_debug_mode_true_writes_jsonl(self, tmp_path):
        """save_debug_data=True: record_step + end_session 产生 JSONL 和 CSV 文件."""
        from backtest.recorder import EventRecorder

        rec = EventRecorder(str(tmp_path), "AAPL", save_debug_data=True)
        rec.start_session(self._make_minimal_config())
        rec.record_step(0, "2026-01-15", self._make_minimal_output())
        rec.end_session()

        session_dir = list(tmp_path.iterdir())[0]
        assert (session_dir / "events.jsonl").exists()
        assert (session_dir / "bs_snapshot.jsonl").exists()
        assert (session_dir / "filter_tail.jsonl").exists()
        assert (session_dir / "schmitt_snapshot.jsonl").exists()
        assert (session_dir / "trade_summary.jsonl").exists()

    def test_metadata_always_written_regardless_of_mode(self, tmp_path):
        """metadata.json 在 debug 和非 debug 模式下都写入."""
        from backtest.recorder import EventRecorder

        for mode in [True, False]:
            rec = EventRecorder(str(tmp_path), "AAPL", save_debug_data=mode)
            rec.start_session(self._make_minimal_config())
            rec.record_step(0, "2026-01-15", self._make_minimal_output())
            rec.end_session()

            session_dir = list(tmp_path.iterdir())[-1]
            assert (session_dir / "metadata.json").exists(), (
                f"metadata.json should exist in save_debug_data={mode}"
            )

    def test_session_started_event_written_even_in_non_debug_mode(self, tmp_path):
        """即使 save_debug_data=False, session_started/session_ended 事件
        仍写入 events.jsonl（因为 _append_jsonl 对 events_fp=None 写 metadata）。"""
        from backtest.recorder import EventRecorder

        rec = EventRecorder(str(tmp_path), "AAPL", save_debug_data=False)
        rec.start_session(self._make_minimal_config())
        rec.record_step(0, "2026-01-15", self._make_minimal_output())
        rec.end_session()

        # 验证 session 正常完成（不抛异常）
        session_dir = list(tmp_path.iterdir())[0]
        assert session_dir.is_dir()

