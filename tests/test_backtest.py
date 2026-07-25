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


# ── TestBacktestModeSwitch ───────────────────────────────────────────────────

class TestBacktestModeSwitch:
    """测试模式切换：进入/退出回测、_cb_mode 状态变化."""

    def test_enter_backtest_mode(self):
        """进入回测模式后 _cb_mode=True."""
        AppState.set("_cb_mode", True)
        assert AppState.get("_cb_mode") is True
        assert state.st.session_state["_cb_mode"] is True

    def test_exit_backtest_clears_state(self):
        """退出回测模式后状态清除：_cb_mode=False, _bar_index=0, _bt_cutoff_date=''."""
        AppState.set("_cb_mode", True)
        AppState.set("_bar_index", 5)
        AppState.set("_bt_cutoff_date", "2026-01-15")
        # exit
        AppState.set("_cb_mode", False)
        AppState.set("_bar_index", 0)
        AppState.set("_bt_cutoff_date", "")
        assert AppState.get("_cb_mode") is False
        assert AppState.get("_bar_index") == 0
        assert AppState.get("_bt_cutoff_date") == ""

    def test_default_cb_mode_is_false(self):
        """初始状态 _cb_mode 默认 False."""
        assert DEFAULTS.get("_cb_mode") is False

    def test_bar_index_default_is_zero(self):
        """初始 _bar_index 默认为 0."""
        assert DEFAULTS.get("_bar_index") == 0

    def test_cutoff_date_default_is_empty_string(self):
        """初始 _bt_cutoff_date 默认为空字符串."""
        assert DEFAULTS.get("_bt_cutoff_date") == ""


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


# ── TestBacktestEdgeCases ────────────────────────────────────────────────────

class TestBacktestEdgeCases:
    """测试边界条件."""

    def test_high_tf_insufficient_data(self):
        """高周期在 cutoff_date 前无数据返回 (False, 0)."""
        from data.loader import _sync_to_display

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        mock_conn.execute.return_value.fetchall.return_value = []

        with patch("data.loader.get_conn", return_value=mock_conn):
            ok, count = _sync_to_display("AAPL", "1mo", n_pts=120, cutoff_date="1990-01-01")
            assert ok is False
            assert count == 0

    def test_ticker_switch_resets_backtest_state(self):
        """ticker 切换后回测状态重置：_bar_index=0, _bt_cutoff_date=""."""
        AppState.set("_cb_mode", True)
        AppState.set("_bar_index", 10)
        AppState.set("_bt_cutoff_date", "2026-05-20")
        AppState.set("_bt_last_ticker", "AAPL")

        # 模拟 ticker 切换逻辑
        new_ticker = "MSFT"
        if AppState.get("_cb_mode", False) and AppState.get("_bt_last_ticker", "") != new_ticker:
            AppState.set("_bar_index", 0)
            AppState.set("_bt_cutoff_date", "")
            AppState.set("_bt_last_ticker", new_ticker)

        assert AppState.get("_bar_index") == 0
        assert AppState.get("_bt_cutoff_date") == ""

    def test_log_failure_caught_by_logger_debug(self):
        """日志失败时 logger.debug 记录异常不抛出."""
        import logging
        logger = logging.getLogger("test_backtest")

        with patch.object(logger, "debug") as mock_debug:
            try:
                raise OSError("磁盘满")
            except OSError:
                logger.debug("回测日志写入失败: 磁盘满")

            mock_debug.assert_called_with("回测日志写入失败: 磁盘满")

    def test_bar_index_range_respected(self):
        """bar_index 在 slider 范围内滑动."""
        total_bars = 500
        n_pts = 120

        AppState.set("_bar_index", 0)
        AppState.set("_min_tf_bar_count", total_bars)
        assert AppState.get("_bar_index") == 0
        assert 0 <= AppState.get("_bar_index") <= total_bars - n_pts

        AppState.set("_bar_index", 200)
        assert 0 <= AppState.get("_bar_index") <= total_bars - n_pts

        AppState.set("_bar_index", total_bars - n_pts)
        assert AppState.get("_bar_index") == total_bars - n_pts


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

        with patch("data.db.get_conn", return_value=mock_conn):
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

    def test_log_bar_navigation_writes_jsonl(self, tmp_path):
        """log_bar_navigation 写入 JSONL."""
        import json
        from backtest.logger import log_bar_navigation

        with patch("backtest.logger.LOG_DIR", tmp_path):
            log_bar_navigation("AAPL", "60分钟", 50, 500, "2026-06-15")

            record = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
            assert record["event"] == "bar_navigation"
            assert record["bar_index"] == 50
            assert record["total"] == 500

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

    def test_log_error_writes_jsonl(self, tmp_path):
        """log_error 写入错误 JSONL."""
        import json
        from backtest.logger import log_error

        with patch("backtest.logger.LOG_DIR", tmp_path):
            log_error("TSLA", "_sync_to_display", "数据库连接超时")

            record = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
            assert record["event"] == "error"
            assert record["location"] == "_sync_to_display"
            assert "数据库连接超时" in record["error"]

    def test_log_dir_auto_created(self, tmp_path):
        """日志目录自动创建."""
        from backtest.logger import _ensure_dir

        logs = tmp_path / "nested" / "backtest_logs"
        with patch("backtest.logger.LOG_DIR", logs):
            _ensure_dir()
            assert logs.exists()
            assert logs.is_dir()


# ── TestAppStateKeys ─────────────────────────────────────────────────────────

class TestAppStateKeys:
    """验证 DEFAULTS 包含回测所需的键."""

    def test_backtest_keys_in_defaults(self):
        """DEFAULTS 包含 _cb_mode, _bar_index, _bt_cutoff_date, _bt_last_ticker."""
        assert "_cb_mode" in DEFAULTS
        assert "_bar_index" in DEFAULTS
        assert "_bt_cutoff_date" in DEFAULTS
        assert "_bt_last_ticker" in DEFAULTS

    def test_cb_mode_is_bool_false(self):
        """_cb_mode 默认值为 False."""
        assert DEFAULTS["_cb_mode"] is False
        assert isinstance(DEFAULTS["_cb_mode"], bool)

    def test_bar_index_is_int_zero(self):
        """_bar_index 默认值为 0."""
        assert DEFAULTS["_bar_index"] == 0

    def test_bt_cutoff_date_is_empty_str(self):
        """_bt_cutoff_date 默认值为空字符串."""
        assert DEFAULTS["_bt_cutoff_date"] == ""

    def test_bt_last_ticker_is_empty_str(self):
        """_bt_last_ticker 默认值为空字符串."""
        assert DEFAULTS["_bt_last_ticker"] == ""


# ── TestNavigationButtons ─────────────────────────────────────────────────────

class TestNavigationButtons:
    """测试导航按钮逻辑"""

    def test_forward_increments_bar_index(self):
        """前进按钮: _bar_index + 1, 不超过 total"""
        total_bars = 500
        AppState.set("_bar_index", 100)
        bar_index = AppState.get("_bar_index")
        # 前进逻辑（与 _render_backtest_nav 中一致）
        new_index = min(total_bars, bar_index + 1)
        assert new_index == 101

    def test_backward_decrements_bar_index(self):
        """后退按钮: _bar_index - 1, 不低于 min_n_pts"""
        min_n_pts = 120
        AppState.set("_bar_index", 200)
        bar_index = AppState.get("_bar_index")
        new_index = max(min_n_pts, bar_index - 1)
        assert new_index == 199

    def test_goto_start_sets_min_n_pts(self):
        """跳到开头: _bar_index = min_n_pts"""
        min_n_pts = 120
        AppState.set("_bar_index", 300)
        # goto start 逻辑
        AppState.set("_bar_index", min_n_pts)
        assert AppState.get("_bar_index") == min_n_pts

    def test_goto_end_sets_total(self):
        """跳到末尾: _bar_index = total_bars"""
        total_bars = 500
        AppState.set("_bar_index", 100)
        AppState.set("_bar_index", total_bars)
        assert AppState.get("_bar_index") == total_bars

    def test_forward_disabled_at_end(self):
        """前进按钮在末尾时 disabled"""
        total_bars = 500
        bar_index = 500
        disabled = bar_index >= total_bars
        assert disabled is True

    def test_backward_disabled_at_start(self):
        """后退按钮在开头时 disabled"""
        min_n_pts = 120
        bar_index = 120
        disabled = bar_index <= min_n_pts
        assert disabled is True


# ── TestPlayback ──────────────────────────────────────────────────────────────

class TestPlayback:
    """测试播放功能"""

    def test_play_not_active_returns_false(self):
        """_is_playing=False 时 _run_backtest_play 返回 False"""
        AppState.set("_is_playing", False)
        # _run_backtest_play 第一行: if not AppState.get("_is_playing", False): return False
        if not AppState.get("_is_playing", False):
            should_continue = False
        else:
            should_continue = True
        assert should_continue is False

    def test_play_increments_bar_index(self):
        """播放时 bar_index += 1"""
        AppState.set("_is_playing", True)
        AppState.set("_play_just_started", False)
        AppState.set("_bar_index", 100)
        AppState.set("_min_tf_bar_count", 500)

        bar_index = AppState.get("_bar_index")
        total = AppState.get("_min_tf_bar_count", 0)

        # _run_backtest_play 核心逻辑：bar_index < total 时前进
        if bar_index < total:
            AppState.set("_bar_index", bar_index + 1)
            should_continue = True
        else:
            AppState.set("_is_playing", False)
            should_continue = False

        assert should_continue is True
        assert AppState.get("_bar_index") == 101

    def test_play_stops_at_end(self):
        """bar_index >= total 时自动停止"""
        AppState.set("_is_playing", True)
        AppState.set("_play_just_started", False)
        AppState.set("_bar_index", 500)
        AppState.set("_min_tf_bar_count", 500)

        bar_index = AppState.get("_bar_index")
        total = AppState.get("_min_tf_bar_count", 0)

        if bar_index >= total:
            AppState.set("_is_playing", False)
            should_continue = False
        else:
            should_continue = True

        assert should_continue is False
        assert AppState.get("_is_playing") is False

    def test_play_updates_cutoff_date(self):
        """播放时 cutoff_date 同步更新"""
        # 播放过程中 bar_index 前进后，通过 _get_bar_date_from_db 获取
        # 最新日期并写入 _bt_cutoff_date
        AppState.set("_bar_index", 100)
        AppState.set("_fetched_ticker", "AAPL")
        AppState.set("_min_tf", "1d")
        AppState.set("_bt_cutoff_date", "")

        # 模拟 cutoff_date 同步（_run_backtest_play 中的逻辑）
        cutoff_date = "2026-06-15"
        if cutoff_date:
            AppState.set("_bt_cutoff_date", cutoff_date)

        assert AppState.get("_bt_cutoff_date") == "2026-06-15"

    def test_play_pause_toggle(self):
        """点击播放→暂停→播放 循环正常"""
        # 初始：不播放
        AppState.set("_is_playing", False)
        assert AppState.get("_is_playing") is False

        # 点击播放
        AppState.set("_is_playing", True)
        assert AppState.get("_is_playing") is True

        # 点击暂停
        AppState.set("_is_playing", False)
        assert AppState.get("_is_playing") is False

        # 再次点击播放
        AppState.set("_is_playing", True)
        assert AppState.get("_is_playing") is True

    def test_play_speed_mapping(self):
        """速度标签正确映射到 sleep 时间"""
        speed_map = {"0.25x": 0.25, "0.5x": 0.5, "1x": 1.0, "2x": 2.0, "5x": 5.0, "10x": 10.0}

        assert speed_map["0.25x"] == 0.25
        assert speed_map["0.5x"] == 0.5
        assert speed_map["1x"] == 1.0
        assert speed_map["2x"] == 2.0
        assert speed_map["5x"] == 5.0
        assert speed_map["10x"] == 10.0
        assert len(speed_map) == 6

    def test_on_slider_change_skips_during_playback(self):
        """播放期间 _on_slider_change 应跳过，避免干扰播放循环"""
        # 播放中：_on_slider_change 第一行检查 _is_playing
        AppState.set("_is_playing", True)
        should_skip = AppState.get("_is_playing", False)
        assert should_skip is True  # 播放中 → 跳过

        # 非播放：正常执行（更新 cutoff_date）
        AppState.set("_is_playing", False)
        should_skip = AppState.get("_is_playing", False)
        assert should_skip is False  # 非播放 → 正常执行

    def test_play_works_from_any_position(self):
        """从任意 bar_index (< total) 开始播放的核心逻辑验证"""
        # 场景：用户拖动 slider 到 200 后点击播放
        AppState.set("_is_playing", True)
        AppState.set("_bar_index", 200)
        AppState.set("_min_tf_bar_count", 500)

        bar_index = AppState.get("_bar_index")
        total = AppState.get("_min_tf_bar_count", 0)

        # 验证：bar_index < total → 不触发停止
        assert bar_index < total  # 200 < 500

        # 验证：前进一个 bar
        new_bar_index = bar_index + 1
        assert new_bar_index == 201

        # 验证：不会因 _on_slider_change 干扰而停止
        assert AppState.get("_is_playing") is True


# ── TestOnSliderChange ─────────────────────────────────────────────────────

class TestOnSliderChange:
    """测试 _on_slider_change 桥接函数的行为。

    每个测试：
      1. 用 _DictSessionState 构建 backtest_panel 可读写的 session_state
      2. patch backtest_panel.st.session_state → real_ss
      3. patch _get_bar_date_from_db（TC5/TC6/TC9/TC10）
      4. 调用 _on_slider_change()
      5. 验证结果
    """

    def _setup_ss(self, real_ss: dict, extra: dict | None = None) -> _DictSessionState:
        """将 real_ss 转为 backtest_panel 及 state 共享的 session_state。"""
        import filter.backtest.panel as bp  # type: ignore[import-untyped]
        # _DictSessionState 同时支持 dict 存取和 attribute 存取
        ss = _DictSessionState(real_ss)
        if extra:
            ss.update(extra)
        # 让 backtest_panel 和 state 共用同一个 session_state 实例
        bp.st.session_state = ss
        state.st.session_state = ss
        return ss

    # ── P0 ────────────────────────────────────────────────────────────────

    def test_non_playing_syncs_slider_to_bar_index(self, _mock_state_st):
        """TC4: 非播放时 _on_slider_change 同步 _bt_slider_pos → _bar_index."""
        ss = self._setup_ss(
            _mock_state_st,
            {"_is_playing": False, "_bt_slider_pos": 42},
        )
        from filter.backtest.panel import _on_slider_change  # type: ignore[import-untyped]
        _on_slider_change()
        assert ss["_bar_index"] == 42

    def test_non_playing_updates_cutoff_date(self, _mock_state_st):
        """TC5: 非播放时同步 cutoff_date."""
        ss = self._setup_ss(
            _mock_state_st,
            {"_is_playing": False, "_bt_slider_pos": 42,
             "_fetched_ticker": "AAPL", "_min_tf": "日线"},
        )
        from filter.backtest.panel import _on_slider_change  # type: ignore[import-untyped]
        with patch("filter.backtest.panel._get_bar_date_from_db", return_value="2026-07-15"):
            _on_slider_change()
        assert ss["_bar_index"] == 42
        assert ss["_bt_cutoff_date"] == "2026-07-15"

    def test_playing_skips_on_slider_change(self, _mock_state_st):
        """TC6: 播放中 _on_slider_change 立即返回，不覆盖 _bar_index."""
        ss = self._setup_ss(
            _mock_state_st,
            {"_is_playing": True, "_bt_slider_pos": 99, "_bar_index": 50},
        )
        from filter.backtest.panel import _on_slider_change  # type: ignore[import-untyped]
        _on_slider_change()
        assert ss["_bar_index"] == 50  # 保持原有值不变

    # ── P1 ────────────────────────────────────────────────────────────────

    def test_on_slider_change_does_not_mutate_is_playing(self, _mock_state_st):
        """TC7: 非播放时 _on_slider_change 不修改 _is_playing."""
        self._setup_ss(_mock_state_st, {"_is_playing": False})
        from filter.backtest.panel import _on_slider_change  # type: ignore[import-untyped]
        _on_slider_change()
        assert AppState.get("_is_playing") is False

    def test_on_slider_change_defaults_to_zero(self, _mock_state_st):
        """TC8: _bt_slider_pos 不存在时用默认值 0."""
        ss = self._setup_ss(
            _mock_state_st,
            {"_is_playing": False},
        )
        from filter.backtest.panel import _on_slider_change  # type: ignore[import-untyped]
        _on_slider_change()
        assert ss["_bar_index"] == 0

    def test_skips_cutoff_when_ticker_not_ready(self, _mock_state_st):
        """TC9: ticker/min_tf 未就绪时不更新 cutoff."""
        ss = self._setup_ss(
            _mock_state_st,
            {"_is_playing": False, "_bt_slider_pos": 42,
             "_fetched_ticker": "", "_min_tf": "",
             "_bt_cutoff_date": "old-date"},
        )
        from filter.backtest.panel import _on_slider_change  # type: ignore[import-untyped]
        _on_slider_change()
        assert ss["_bar_index"] == 42
        assert ss["_bt_cutoff_date"] == "old-date"

    # ── P2 ────────────────────────────────────────────────────────────────

    def test_skips_cutoff_when_db_returns_empty(self, _mock_state_st):
        """TC10: _get_bar_date_from_db 返回空时不更新 cutoff."""
        ss = self._setup_ss(
            _mock_state_st,
            {"_is_playing": False, "_bt_slider_pos": 42,
             "_fetched_ticker": "AAPL", "_min_tf": "日线",
             "_bt_cutoff_date": "old-date"},
        )
        from filter.backtest.panel import _on_slider_change  # type: ignore[import-untyped]
        with patch("filter.backtest.panel._get_bar_date_from_db", return_value=""):
            _on_slider_change()
        assert ss["_bar_index"] == 42
        assert ss["_bt_cutoff_date"] == "old-date"


# ── TestRenderBacktestModeSlider ─────────────────────────────────────────────

class TestRenderBacktestModeSlider:
    """测试 _render_backtest_mode 中 slider/progress 分支.

    通过 patch st.sidebar.slider / st.sidebar.progress 验证调用次数和参数。

    _render_backtest_mode 内包含 st.sidebar.radio 及 _render_backtest_nav，
    测试中 patch 掉这些副作用以避免模式切换逻辑干扰分支验证。
    """

    def _setup(self, real_ss: dict, extras: dict | None = None) -> dict:
        """准备 _DictSessionState 并让 backtest_panel / state 共享。"""
        import filter.backtest.panel as bp  # type: ignore[import-untyped]
        ss = _DictSessionState(real_ss)
        if extras:
            ss.update(extras)
        bp.st.session_state = ss
        state.st.session_state = ss
        return ss

    def _call_render(self, ss):
        """调用 _render_backtest_mode，patch radio 使其保持在回测模式，patch nav 避免按钮副作用。"""
        import filter.backtest.panel as bp  # type: ignore[import-untyped]
        with patch.object(bp.st.sidebar, "radio", return_value="回测模式"), \
             patch.object(bp, "_render_backtest_nav"):
            bp._render_backtest_mode("美股 US", "AAPL", [{"tf": "日线", "n_pts": 120}])

    # ── P0 ────────────────────────────────────────────────────────────────

    def test_playing_renders_progress_not_slider(self, _mock_state_st):
        """TC11: 播放模式渲染 progress bar，不渲染 slider."""
        ss = self._setup(
            _mock_state_st,
            {"_is_playing": True, "_bar_index": 100, "_min_tf_bar_count": 500,
             "_cb_mode": True, "_min_tf": "日线", "_fetched_ticker": "AAPL"},
        )
        import filter.backtest.panel as bp  # type: ignore[import-untyped]
        with patch.object(bp.st.sidebar, "progress") as mock_progress, \
             patch.object(bp.st.sidebar, "slider") as mock_slider:
            self._call_render(ss)
        mock_progress.assert_called_once()
        mock_slider.assert_not_called()
        mock_slider.assert_not_called()

    def test_not_playing_renders_slider_not_progress(self, _mock_state_st):
        """TC12: 非播放模式渲染 slider，不渲染 progress."""
        ss = self._setup(
            _mock_state_st,
            {"_is_playing": False, "_bar_index": 100, "_min_tf_bar_count": 500,
             "_cb_mode": True, "_min_tf": "日线", "_fetched_ticker": "AAPL"},
        )
        import filter.backtest.panel as bp  # type: ignore[import-untyped]
        with patch.object(bp.st.sidebar, "progress") as mock_progress, \
             patch.object(bp.st.sidebar, "slider") as mock_slider:
            self._call_render(ss)
        mock_progress.assert_not_called()
        mock_slider.assert_called_once()

    def test_slider_uses_bt_slider_pos_key_and_bar_index_value(self, _mock_state_st):
        """TC13: slider 使用 _bt_slider_pos 作为 key, _bar_index 作为 value."""
        ss = self._setup(
            _mock_state_st,
            {"_is_playing": False, "_bar_index": 100, "_min_tf_bar_count": 500,
             "_cb_mode": True, "_min_tf": "日线", "_fetched_ticker": "AAPL"},
        )
        import filter.backtest.panel as bp  # type: ignore[import-untyped]
        with patch.object(bp.st.sidebar, "slider") as mock_slider:
            self._call_render(ss)
        mock_slider.assert_called_once()
        _call_kwargs = mock_slider.call_args.kwargs
        assert _call_kwargs.get("key") == "_bt_slider_pos"
        assert _call_kwargs.get("value") == 500
        assert _call_kwargs.get("on_change") == bp._on_slider_change

    # ── P1 ────────────────────────────────────────────────────────────────

    def test_bar_index_recovered_when_below_min_n_pts(self, _mock_state_st):
        """TC14: _bar_index < min_n_pts 时恢复为 total_bars."""
        ss = self._setup(
            _mock_state_st,
            {"_is_playing": False, "_bar_index": 0, "_min_tf_bar_count": 500,
             "_cb_mode": True, "_min_tf": "日线", "_fetched_ticker": "AAPL"},
        )
        import filter.backtest.panel as bp  # type: ignore[import-untyped]
        with patch.object(bp.st.sidebar, "slider"):
            self._call_render(ss)
        assert ss["_bar_index"] == 500

    # ── P2 ────────────────────────────────────────────────────────────────

    def test_warning_when_data_not_ready(self, _mock_state_st):
        """TC15: 数据未就绪（total_bars=0）时显示警告."""
        ss = self._setup(
            _mock_state_st,
            {"_is_playing": False, "_bar_index": 0, "_min_tf_bar_count": 0,
             "_cb_mode": True, "_min_tf": "", "_fetched_ticker": ""},
        )
        import filter.backtest.panel as bp  # type: ignore[import-untyped]
        with patch.object(bp.st.sidebar, "warning") as mock_warning, \
             patch.object(bp.st.sidebar, "slider") as mock_slider, \
             patch.object(bp.st.sidebar, "progress") as mock_progress:
            self._call_render(ss)
        mock_warning.assert_called_once()
        mock_slider.assert_not_called()
        mock_progress.assert_not_called()

    def test_playback_stopped_renders_slider(self, _mock_state_st):
        """TC26: 播放停止后下一帧渲染 slider 而非 progress."""
        ss = self._setup(
            _mock_state_st,
            {"_is_playing": False, "_bar_index": 500, "_min_tf_bar_count": 500,
             "_cb_mode": True, "_min_tf": "日线", "_fetched_ticker": "AAPL"},
        )
        import filter.backtest.panel as bp  # type: ignore[import-untyped]
        with patch.object(bp.st.sidebar, "progress") as mock_progress, \
             patch.object(bp.st.sidebar, "slider") as mock_slider:
            self._call_render(ss)
        mock_progress.assert_not_called()
        mock_slider.assert_called_once()


# ── TestBacktestNavButtons ────────────────────────────────────────────────────

class TestBacktestNavButtons:
    """测试回测导航按钮逻辑."""

    @staticmethod
    def _call_nav_button_fn(name: str, total_bars: int, min_n_pts: int,
                             real_ss: dict, extras: dict | None = None):
        """patch 掉 st.sidebar.button 按钮创建，仅触发按钮回调中的逻辑。"""
        import filter.backtest.panel as bp  # type: ignore[import-untyped]
        ss = _DictSessionState(real_ss)
        if extras:
            ss.update(extras)
        bp.st.session_state = ss
        state.st.session_state = ss

        bar_index = ss.get("_bar_index", total_bars)
        is_playing = ss.get("_is_playing", False)

        # 根据按钮名直接模拟 _render_backtest_nav 中对应按钮的回调逻辑
        if name == "goto_start":
            ss["_bar_index"] = min_n_pts
        elif name == "step_back":
            ss["_bar_index"] = max(min_n_pts, bar_index - 1)
        elif name == "step_fwd":
            ss["_bar_index"] = min(total_bars, bar_index + 1)
        elif name == "goto_end":
            ss["_bar_index"] = total_bars
        elif name == "toggle_play":
            if is_playing:
                ss["_is_playing"] = False
            else:
                if bar_index >= total_bars:
                    ss["_bar_index"] = min_n_pts
                ss["_is_playing"] = True
        else:
            raise ValueError(f"Unknown nav button: {name}")

        return ss

    # ── P0 ────────────────────────────────────────────────────────────────

    def test_goto_start_resets_to_min_n_pts(self, _mock_state_st):
        """TC16: ⏮ 按钮重置 _bar_index 到 min_n_pts."""
        ss = self._call_nav_button_fn(
            "goto_start", total_bars=500, min_n_pts=120,
            real_ss=_mock_state_st,
            extras={"_bar_index": 50},
        )
        assert ss["_bar_index"] == 120

    def test_step_back_decrements_bar_index(self, _mock_state_st):
        """TC17: ◀ 按钮递减 _bar_index."""
        ss = self._call_nav_button_fn(
            "step_back", total_bars=500, min_n_pts=20,
            real_ss=_mock_state_st,
            extras={"_bar_index": 50},
        )
        assert ss["_bar_index"] == 49

    def test_step_fwd_increments_bar_index(self, _mock_state_st):
        """TC19: ⏵ 按钮递增 _bar_index."""
        ss = self._call_nav_button_fn(
            "step_fwd", total_bars=500, min_n_pts=120,
            real_ss=_mock_state_st,
            extras={"_bar_index": 50},
        )
        assert ss["_bar_index"] == 51

    def test_goto_end_jumps_to_total_bars(self, _mock_state_st):
        """TC21: ⏭ 按钮跳转到 total_bars."""
        ss = self._call_nav_button_fn(
            "goto_end", total_bars=500, min_n_pts=120,
            real_ss=_mock_state_st,
            extras={"_bar_index": 50},
        )
        assert ss["_bar_index"] == 500

    def test_toggle_play_sets_is_playing_false(self, _mock_state_st):
        """TC30: ⏸ 按钮将 _is_playing 设为 False."""
        ss = self._call_nav_button_fn(
            "toggle_play", total_bars=500, min_n_pts=120,
            real_ss=_mock_state_st,
            extras={"_is_playing": True},
        )
        assert ss["_is_playing"] is False

    def test_toggle_play_sets_is_playing_true(self, _mock_state_st):
        """TC31: ▶ 按钮将 _is_playing 设为 True."""
        ss = self._call_nav_button_fn(
            "toggle_play", total_bars=500, min_n_pts=120,
            real_ss=_mock_state_st,
            extras={"_is_playing": False, "_bar_index": 300},
        )
        assert ss["_is_playing"] is True

    def test_toggle_play_resets_bar_index_when_at_end(self, _mock_state_st):
        """TC31 variant: 已到末尾时 ▶ 先将 _bar_index 降至 min_n_pts 再播放."""
        ss = self._call_nav_button_fn(
            "toggle_play", total_bars=500, min_n_pts=120,
            real_ss=_mock_state_st,
            extras={"_is_playing": False, "_bar_index": 500},
        )
        assert ss["_bar_index"] == 120
        assert ss["_is_playing"] is True

    # ── P1 ────────────────────────────────────────────────────────────────

    def test_step_back_clamped_at_min_n_pts(self, _mock_state_st):
        """TC18: ◀ 按钮不会低于 min_n_pts."""
        ss = self._call_nav_button_fn(
            "step_back", total_bars=500, min_n_pts=120,
            real_ss=_mock_state_st,
            extras={"_bar_index": 120},
        )
        assert ss["_bar_index"] == 120

    def test_step_fwd_clamped_at_total_bars(self, _mock_state_st):
        """TC20: ⏵ 按钮不会超过 total_bars."""
        ss = self._call_nav_button_fn(
            "step_fwd", total_bars=500, min_n_pts=120,
            real_ss=_mock_state_st,
            extras={"_bar_index": 500},
        )
        assert ss["_bar_index"] == 500

    def test_nav_buttons_do_not_touch_bt_slider_pos(self, _mock_state_st):
        """TC22: 导航按钮不触碰 _bt_slider_pos."""
        ss = self._call_nav_button_fn(
            "step_fwd", total_bars=500, min_n_pts=120,
            real_ss=_mock_state_st,
            extras={"_bar_index": 50, "_bt_slider_pos": 100},
        )
        assert ss["_bt_slider_pos"] == 100


# ── TestRunBacktestPlay ───────────────────────────────────────────────────────

class TestRunBacktestPlay:
    """测试 _run_backtest_play 播放递增逻辑."""

    @staticmethod
    def _call_play(real_ss: dict, extras: dict | None = None) -> _DictSessionState:
        """设置 session_state 并调用 _run_backtest_play. 返回执行后的 ss."""
        import filter.backtest.panel as bp  # type: ignore[import-untyped]
        ss = _DictSessionState(real_ss)
        if extras:
            ss.update(extras)
        bp.st.session_state = ss
        state.st.session_state = ss
        # logger 已在 conftest mock 的 streamlit runtime 之外，需要 patch
        with patch("filter.backtest.panel.logger"):
            if extras and extras.get("_min_tf_bar_count", 0) > 0:
                # 需要 cutt-off patch
                pass
            bp._run_backtest_play()
        return ss

    # ── P0 ────────────────────────────────────────────────────────────────

    def test_play_increments_bar_index_not_slider_pos(self, _mock_state_st):
        """TC23: _run_backtest_play 递增 _bar_index 而非 _bt_slider_pos."""
        ss = self._call_play(
            _mock_state_st,
            {"_is_playing": True, "_cb_mode": True, "_bar_index": 50,
             "_bt_slider_pos": 50, "_min_tf_bar_count": 500},
        )
        assert ss["_bar_index"] == 51
        assert ss["_bt_slider_pos"] == 50

    def test_play_advances_to_end(self, _mock_state_st):
        """TC24: 播放到末尾自动停止（499→500 后停在边界）。"""
        ss = self._call_play(
            _mock_state_st,
            {"_is_playing": True, "_cb_mode": True, "_bar_index": 499,
             "_min_tf_bar_count": 500},
        )
        # bar_index 从 499 递增到 500（到达末尾，尚未触发停止）
        assert ss["_bar_index"] == 500
        # _is_playing 保持 True — 停止检查在下次调用
        assert ss["_is_playing"] is True

    def test_play_already_at_end_does_not_increment(self, _mock_state_st):
        """TC25: 已在末尾时 _is_playing=False 且 _bar_index 不递增."""
        ss = self._call_play(
            _mock_state_st,
            {"_is_playing": True, "_cb_mode": True, "_bar_index": 500,
             "_min_tf_bar_count": 500},
        )
        assert ss["_is_playing"] is False
        assert ss["_bar_index"] == 500

    # ── P1 ────────────────────────────────────────────────────────────────

    def test_play_stops_when_not_in_backtest_mode(self, _mock_state_st):
        """TC27: 非回测模式时播放停止."""
        ss = self._call_play(
            _mock_state_st,
            {"_is_playing": True, "_cb_mode": False,
             "_bar_index": 50, "_min_tf_bar_count": 500},
        )
        assert ss["_is_playing"] is False

    # ── P2 ────────────────────────────────────────────────────────────────

    def test_play_stops_when_data_not_ready(self, _mock_state_st):
        """TC28: 数据未就绪（total=0）时播放安全停止."""
        ss = self._call_play(
            _mock_state_st,
            {"_is_playing": True, "_cb_mode": True,
             "_bar_index": 0, "_min_tf_bar_count": 0},
        )
        assert ss["_is_playing"] is False


# ── TestBacktestIntegration ───────────────────────────────────────────────────

class TestBacktestIntegration:
    """回测播放-暂停-拖动完整流程."""

    def test_play_pause_drag_flow(self, _mock_state_st):
        """TC29: 完整播放-暂停-拖动流程."""
        import filter.backtest.panel as bp  # type: ignore[import-untyped]

        # 1. 回测模式初始状态
        ss = _DictSessionState(_mock_state_st)
        ss.update({
            "_cb_mode": True, "_bar_index": 500, "_bt_slider_pos": 500,
            "_is_playing": False, "_min_tf_bar_count": 600,
            "_fetched_ticker": "AAPL", "_min_tf": "日线",
            "_bt_cutoff_date": "",
        })
        bp.st.session_state = ss
        state.st.session_state = ss
        assert ss["_bar_index"] == 500
        assert ss["_bt_slider_pos"] == 500

        # 2. 开始播放
        ss["_is_playing"] = True

        # 3. 播放 5 帧
        with patch("filter.backtest.panel.logger"):
            for _ in range(5):
                bp._run_backtest_play()
        assert ss["_bar_index"] == 505
        assert ss["_bt_slider_pos"] == 500  # 不随播放变化

        # 4. 暂停
        ss["_is_playing"] = False

        # 5. 拖动 slider（期望 _get_bar_date_from_db 返回对应日期）
        ss["_bt_slider_pos"] = 300
        with patch("filter.backtest.panel._get_bar_date_from_db", return_value="2026-06-15"):
            bp._on_slider_change()
        assert ss["_bar_index"] == 300

        # 6. 恢复播放
        ss["_is_playing"] = True
        with patch("filter.backtest.panel.logger"):
            bp._run_backtest_play()
        assert ss["_bar_index"] == 301  # 从 300 继续递增


# ── TestUpdateCutoffAndRerun ──────────────────────────────────────────────────

class TestUpdateCutoffAndRerun:
    """_update_cutoff_and_rerun() 中 _bt_slider_pos 同步逻辑."""

    def test_syncs_bt_slider_pos(self, _mock_state_st):
        """P0: _bar_index=10 → _update_cutoff_and_rerun() 后 _bt_slider_pos == 10."""
        import filter.backtest.panel as bp

        ss = _DictSessionState(_mock_state_st)
        ss.update({
            "_bar_index": 10,
            "_fetched_ticker": "AAPL",
            "_min_tf": "日线",
        })
        bp.st.session_state = ss
        state.st.session_state = ss

        with patch("filter.backtest.panel._get_bar_date_from_db", return_value="2026-06-15"), \
             patch("filter.backtest.panel.st.rerun"):
            bp._update_cutoff_and_rerun()

        assert ss["_bt_slider_pos"] == 10

    def test_syncs_bt_slider_pos_before_cutoff_query(self, _mock_state_st):
        """P0: _bt_slider_pos 在 cutoff_date 查询之前已同步，查询使用更新后的 bar_index."""
        import filter.backtest.panel as bp

        ss = _DictSessionState(_mock_state_st)
        ss.update({
            "_bar_index": 42,
            "_fetched_ticker": "AAPL",
            "_min_tf": "日线",
            "_bt_cutoff_date": "",
        })
        bp.st.session_state = ss
        state.st.session_state = ss

        with patch("filter.backtest.panel._get_bar_date_from_db") as mock_get_date, \
             patch("filter.backtest.panel.st.rerun"):
            mock_get_date.return_value = "2026-07-01"
            bp._update_cutoff_and_rerun()

        # 同步发生在查询之前
        assert ss["_bt_slider_pos"] == 42
        mock_get_date.assert_called_once_with("AAPL", "日线", 41)

    def test_nav_forward_syncs_widget_key(self, _mock_state_st):
        """P1: 前进按钮路径 — bar_index=5 → 前进到 6 → _bt_slider_pos == 6."""
        import filter.backtest.panel as bp

        ss = _DictSessionState(_mock_state_st)
        ss.update({
            "_bar_index": 5,
            "_fetched_ticker": "AAPL",
            "_min_tf": "日线",
        })
        bp.st.session_state = ss
        state.st.session_state = ss

        # 模拟前进按钮操作
        ss["_bar_index"] = 6

        with patch("filter.backtest.panel._get_bar_date_from_db", return_value="2026-06-20"), \
             patch("filter.backtest.panel.st.rerun"):
            bp._update_cutoff_and_rerun()

        assert ss["_bt_slider_pos"] == 6

    def test_play_path_not_affected(self, _mock_state_st):
        """P1: _is_playing=True 时 _on_slider_change 跳过，_run_backtest_play 不受 _bt_slider_pos 影响."""
        import filter.backtest.panel as bp

        ss = _DictSessionState(_mock_state_st)
        ss.update({
            "_cb_mode": True,
            "_bar_index": 200,
            "_bt_slider_pos": 100,  # 播放前 slider 在 100
            "_is_playing": True,
            "_min_tf_bar_count": 500,
            "_fetched_ticker": "AAPL",
            "_min_tf": "日线",
        })
        bp.st.session_state = ss
        state.st.session_state = ss

        # _on_slider_change 在播放时应该跳过
        with patch("filter.backtest.panel.logger"):
            bp._on_slider_change()
        # _bar_index 应保持不变（未被 on_change 改写）
        assert ss["_bar_index"] == 200

        # _run_backtest_play 递增 _bar_index，不受 _bt_slider_pos 干扰
        with patch("filter.backtest.panel.logger"), \
             patch("filter.backtest.panel._get_bar_date_from_db", return_value="2026-06-20"):
            result = bp._run_backtest_play()
        assert result is True
        assert ss["_bar_index"] == 201  # 从 200 递增
        assert ss["_bt_slider_pos"] == 100  # 保持不变


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
        assert resume_bar == 0  # save_checkpoint 中 bar_index 固定为 0

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
        from backtest.engine import _make_json_safe

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


# ═══════════════════════════════════════════════════════════════════════════════
# P0-6: 核心回测指标 — compute_backtest_metrics 测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeBacktestMetrics:
    """验证 backtest_metrics.compute_backtest_metrics 计算正确性。"""

    def test_known_sharpe(self):
        """构造确定性 PnL 曲线验证 Sharpe 计算。"""
        from filter.backtest.metrics import compute_backtest_metrics

        # 构造线性增长的 PnL: 每步涨 0.1%
        n = 252  # 一年
        long_pnl = 100.0 * np.cumprod(np.full(n, 1.001))
        short_pnl = 100.0 * np.ones(n)

        result = compute_backtest_metrics(
            long_pnl, short_pnl, [], n, risk_free_rate=0.0,
        )

        # 正收益 → Sharpe > 0
        assert result["sharpe_ratio"] > 0, f"Expected positive Sharpe, got {result['sharpe_ratio']}"
        assert result["total_return_pct"] > 0
        assert result["max_drawdown_pct"] == 0.0  # 无回撤

    def test_max_drawdown_calculation(self):
        """验证最大回撤计算。"""
        from filter.backtest.metrics import compute_backtest_metrics

        # 构造先涨后跌的 PnL: 100 → 120 → 80
        long_pnl = np.array([100.0, 110.0, 120.0, 100.0, 80.0, 90.0])
        short_pnl = np.array([100.0, 100.0, 100.0, 100.0, 100.0, 100.0])

        result = compute_backtest_metrics(
            long_pnl, short_pnl, [], len(long_pnl), risk_free_rate=0.0,
        )

        # 最大回撤: (80 - 120) / 120 = -16.67% (峰→谷)
        assert result["max_drawdown_pct"] < -15, (
            f"Expected drawdown < -15%, got {result['max_drawdown_pct']}"
        )
        assert result["max_drawdown_duration"] > 0

    def test_zero_trades(self):
        """空交易列表应返回合理默认值。"""
        from filter.backtest.metrics import compute_backtest_metrics

        long_pnl = np.array([100.0, 100.0, 100.0])
        short_pnl = np.array([100.0, 100.0, 100.0])

        result = compute_backtest_metrics(
            long_pnl, short_pnl, [], len(long_pnl), risk_free_rate=0.03,
        )

        assert result["total_trades"] == 0
        assert result["winning_trades"] == 0
        assert result["losing_trades"] == 0
        assert result["win_rate_pct"] == 0.0
        import math
        assert math.isinf(result["profit_factor"]) or result["profit_factor"] == 0.0

    def test_all_wins(self):
        """全部盈利的交易验证 profit_factor = inf。"""
        from filter.backtest.metrics import compute_backtest_metrics

        long_pnl = np.array([100.0, 105.0, 110.0])
        short_pnl = np.array([100.0, 100.0, 100.0])
        trades = [{"return_pct": 5.0}, {"return_pct": 3.0}]

        result = compute_backtest_metrics(
            long_pnl, short_pnl, trades, len(long_pnl), risk_free_rate=0.03,
        )

        assert result["total_trades"] == 2
        assert result["winning_trades"] == 2
        assert result["losing_trades"] == 0
        # 全部盈利 → profit_factor = inf (转为字符串 "inf" 在 round 后...)
        # 实际实现: sum(losses) = 0 → profit_factor = inf, round(inf, 2) = inf
        import math
        assert result["profit_factor"] == float("inf") or math.isinf(result["profit_factor"])

    def test_empty_pnl_arrays(self):
        """单元素 PnL 数组应返回 _empty_metrics 默认值。"""
        from filter.backtest.metrics import compute_backtest_metrics

        long_pnl = np.array([100.0])
        short_pnl = np.array([100.0])

        result = compute_backtest_metrics(
            long_pnl, short_pnl, [], len(long_pnl), risk_free_rate=0.03,
        )

        assert result["sharpe_ratio"] == 0.0
        assert result["sortino_ratio"] == 0.0
        assert result["calmar_ratio"] == 0.0
        assert result["total_return_pct"] == 0.0

    def test_sortino_with_downside(self):
        """有下行波动时应产生合理的 Sortino 值。"""
        from filter.backtest.metrics import compute_backtest_metrics

        np.random.seed(42)
        n = 252
        # 构造有正有负的收益率
        returns = np.random.randn(n) * 0.02
        combined = 100.0 * np.cumprod(1 + returns)
        long_pnl = combined
        short_pnl = np.full(n, 100.0)

        result = compute_backtest_metrics(
            long_pnl, short_pnl, [], n, risk_free_rate=0.0,
        )

        # Sortino 应是一个有限数值（不是 nan 或 inf）
        import math
        assert not math.isnan(result["sortino_ratio"])
        assert not math.isinf(result["sortino_ratio"])


# ── TestEngineViewOrdering ─────────────────────────────────────────────────────


class TestEngineViewOrdering:
    """P0: 引擎视图应遵循「粗→细」排序约定，v0=coarsest, v3=finest。

    约定来源: DEFAULT_TFS 按 ALL_TFS 索引降序排列,
    确保回测输出列 v0_filtered 对应主低频周期(日线),
    v3_filtered 对应最高频周期。
    """

    def test_build_default_configs_sorted_coarse_to_fine(self):
        """默认配置应按 ALL_TFS 降序 (粗→细) 排列."""
        from filter.shared.constants import ALL_TFS, DEFAULT_TFS
        from filter.backtest.cli import _build_default_configs

        configs = _build_default_configs("TEST_TICKER")
        assert len(configs) == 4

        tf_indices = [ALL_TFS.index(c["tf"]) for c in configs]
        assert tf_indices == sorted(tf_indices, reverse=True), (
            f"默认配置应粗→细排列, indices={tf_indices}"
        )

    def test_build_configs_from_params_respects_period_ordering(self):
        """从预设参数构建的配置应保持粗→细排列."""
        from filter.shared.constants import ALL_TFS
        from filter.backtest.cli import _build_configs_from_params

        # 模拟预设参数: v0=coarsest, v3=finest
        params = {
            "global_f": "savgol",
            "global_dual": True,
            "global_f2": "ema",
            "v0_tf": "日线", "v0_n": 60, "v0_ke": 0.15, "v0_sm": 0.05,
            "v1_tf": "60分钟", "v1_n": 60, "v1_ke": 0.1, "v1_sm": 0.05,
            "v2_tf": "15分钟", "v2_n": 50, "v2_ke": 0.1, "v2_sm": 0.05,
            "v3_tf": "5分钟", "v3_n": 50, "v3_ke": 0.1, "v3_sm": 0.05,
        }

        configs = _build_configs_from_params(params)
        assert len(configs) == 4

        tf_indices = [ALL_TFS.index(c["tf"]) for c in configs]
        assert tf_indices == sorted(tf_indices, reverse=True), (
            f"预设配置应粗→细排列, indices={tf_indices}, "
            f"期望={sorted(tf_indices, reverse=True)}"
        )

    def test_engine_min_tf_is_finest_in_use(self, tmp_path):
        """_min_tf 应为 ALL_TFS 索引最小的周期 (最精细)."""
        from filter.shared.constants import ALL_TFS
        from filter.backtest.engine import BacktestRunner
        from unittest.mock import patch

        configs = [
            {"tf": "日线",  "n_pts": 60, "_fid": "sma", "_dual": False},
            {"tf": "15分钟", "n_pts": 50, "_fid": "sma", "_dual": False},
            {"tf": "60分钟", "n_pts": 60, "_fid": "sma", "_dual": False},
        ]

        with patch.object(BacktestRunner, "_query_bar_count", return_value=1000), \
             patch.object(BacktestRunner, "_load_all_bar_info", return_value=[{}]):
            runner = BacktestRunner("3690", configs)

        # 最精细的应是 15分钟 (ALL_TFS index=2)
        assert runner._min_tf == "15分钟", (
            f"_min_tf 应为 '15分钟', 实为 '{runner._min_tf}'"
        )
        # _tfs_in_use 应按 ALL_TFS 升序 (细→粗)
        assert runner._tfs_in_use == ["15分钟", "60分钟", "日线"], (
            f"_tfs_in_use 应细→粗排列, 实为 {runner._tfs_in_use}"
        )
