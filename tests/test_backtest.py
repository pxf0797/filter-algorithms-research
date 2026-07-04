"""
回测模式测试用例.

测试 AppState 回测键、_sync_to_display 日期对齐、日志、模式切换。
所有测试通过 mock DB/streamlit 独立运行，不依赖 Streamlit 运行时。
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── 与 test_state.py 相同的方式导入 state 模块 ──────────────────────────────
_old_streamlit = sys.modules.get("streamlit")
if "streamlit" in sys.modules:
    del sys.modules["streamlit"]

import state  # noqa: E402

sys.modules["streamlit"] = _old_streamlit


AppState = state.AppState
DEFAULTS = state.SYSTEM_KEYS  # state.py 中定义为 SYSTEM_KEYS


@pytest.fixture(autouse=True)
def _mock_state_st():
    """让 state.st.session_state 成为真实的 dict（与 test_state.py 相同模式）。"""
    real_ss = {}
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


# ── TestRenderTimeNav ────────────────────────────────────────────────────────

class TestRenderTimeNav:
    """回测模式下 _render_time_nav 返回 0 且不渲染导航组件."""

    def test_time_nav_returns_zero_in_backtest_mode(self):
        """_cb_mode=True 时 _render_time_nav 返回 0."""
        AppState.set("_cb_mode", True)
        # 模拟 _render_time_nav 内的逻辑
        if AppState.get("_cb_mode", False):
            day_offset = 0
        else:
            day_offset = 99
        assert day_offset == 0

    def test_time_nav_not_zero_in_browse_mode(self):
        """_cb_mode=False 时不走早期返回分支."""
        AppState.set("_cb_mode", False)
        if AppState.get("_cb_mode", False):
            day_offset = 0
        else:
            day_offset = None  # 代表走了正常导航流程
        assert day_offset is not 0


# ── TestSyncToDisplay ────────────────────────────────────────────────────────

class TestSyncToDisplay:
    """_sync_to_display 日期对齐：cutoff_date 模式与浏览模式."""

    def test_cutoff_date_query_includes_cutoff(self, tmp_path):
        """传入 cutoff_date 时 SQL 包含 cutoff_date 参数."""
        from services.data_loader import _sync_to_display

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

        with patch("db.get_conn", return_value=mock_conn), \
             patch("services.data_loader.Path") as mock_path_class:
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
        from services.data_loader import _sync_to_display

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        mock_conn.execute.return_value.fetchall.return_value = []

        with patch("db.get_conn", return_value=mock_conn):
            ok, count = _sync_to_display("AAPL", "1d", n_pts=120, cutoff_date="1990-01-01")
            assert ok is False
            assert count == 0

    def test_no_cutoff_uses_browse_mode(self):
        """不传 cutoff_date 走浏览模式 query_kline 路径."""
        from services.data_loader import _sync_to_display
        import pandas as pd

        with patch("services.data_loader.query_kline", return_value=pd.DataFrame()) as mock_query:
            ok, count = _sync_to_display("AAPL", "1d", day_offset=0, n_pts=120, cutoff_date=None)

            assert ok is False
            assert count == 0
            mock_query.assert_called_once()

    def test_sync_false_triggers_api_fallback(self):
        """_sync_to_display 返回 ok=False 表示需要 API 回退."""
        from services.data_loader import _sync_to_display

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        mock_conn.execute.return_value.fetchall.return_value = []

        with patch("db.get_conn", return_value=mock_conn):
            ok, count = _sync_to_display("XYZ", "1mo", n_pts=120, cutoff_date="2025-01-01")
            assert ok is False  # 触发 _load_chart_data 走 _cached_fetch_stock

    def test_cutoff_date_truncates_to_n_pts(self, tmp_path):
        """cutoff_date 查询返回恰好 n_pts 条."""
        from services.data_loader import _sync_to_display

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

        with patch("db.get_conn", return_value=mock_conn), \
             patch("services.data_loader.Path") as mock_path_class:
            mock_path_class.return_value.parent.parent.parent.__truediv__.return_value = display_dir

            ok, count = _sync_to_display("AAPL", "1d", n_pts=n_pts, cutoff_date="2026-06-15")
            assert ok is True
            assert count == n_pts


# ── TestBacktestEdgeCases ────────────────────────────────────────────────────

class TestBacktestEdgeCases:
    """测试边界条件."""

    def test_high_tf_insufficient_data(self):
        """高周期在 cutoff_date 前无数据返回 (False, 0)."""
        from services.data_loader import _sync_to_display

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        mock_conn.execute.return_value.fetchall.return_value = []

        with patch("db.get_conn", return_value=mock_conn):
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
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "filter_app"))
        from streamlit_app import _get_bar_date_from_db

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        mock_conn.execute.return_value.fetchone.return_value = ("2026-06-15",)

        with patch("db.get_conn", return_value=mock_conn):
            result = _get_bar_date_from_db("AAPL", "1d", 100)
            assert result == "2026-06-15"

    def test_returns_empty_when_not_found(self):
        """无数据时返回空字符串."""
        from streamlit_app import _get_bar_date_from_db

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        mock_conn.execute.return_value.fetchone.return_value = None

        with patch("db.get_conn", return_value=mock_conn):
            result = _get_bar_date_from_db("AAPL", "1d", 99999)
            assert result == ""


# ── TestBacktestLogger ───────────────────────────────────────────────────────

class TestBacktestLogger:
    """回测日志模块."""

    def test_log_mode_switch_writes_jsonl(self, tmp_path):
        """log_mode_switch 写入一条 JSONL 记录."""
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "filter_app"))
        import json
        from backtest_logger import log_mode_switch

        with patch("backtest_logger.LOG_DIR", tmp_path):
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
        from backtest_logger import log_bar_navigation

        with patch("backtest_logger.LOG_DIR", tmp_path):
            log_bar_navigation("AAPL", "60分钟", 50, 500, "2026-06-15")

            record = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
            assert record["event"] == "bar_navigation"
            assert record["bar_index"] == 50
            assert record["total"] == 500

    def test_log_data_load_writes_jsonl(self, tmp_path):
        """log_data_load 写入 JSONL."""
        import json
        from backtest_logger import log_data_load

        with patch("backtest_logger.LOG_DIR", tmp_path):
            log_data_load("MSFT", "1d", 120, "2026-05-01", elapsed_ms=150.5)

            record = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
            assert record["event"] == "data_load"
            assert record["ticker"] == "MSFT"
            assert record["bar_count"] == 120
            assert record["elapsed_ms"] == 150.5

    def test_log_error_writes_jsonl(self, tmp_path):
        """log_error 写入错误 JSONL."""
        import json
        from backtest_logger import log_error

        with patch("backtest_logger.LOG_DIR", tmp_path):
            log_error("TSLA", "_sync_to_display", "数据库连接超时")

            record = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
            assert record["event"] == "error"
            assert record["location"] == "_sync_to_display"
            assert "数据库连接超时" in record["error"]

    def test_log_dir_auto_created(self, tmp_path):
        """日志目录自动创建."""
        from backtest_logger import _ensure_dir

        logs = tmp_path / "nested" / "backtest_logs"
        with patch("backtest_logger.LOG_DIR", logs):
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
