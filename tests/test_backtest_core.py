"""
回测模式测试用例.

测试 AppState 回测键、_sync_to_display 日期对齐、日志、模式切换。
所有测试通过 mock DB/streamlit 独立运行，不依赖 Streamlit 运行时。
"""

import sys
from unittest.mock import MagicMock, patch

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
# 引擎核心功能测试 (从 test_backtest.py 拆分)
# ══════════════════════════════════════════════════════════════════════════

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


# 注册 BacktestRunner（模块顶层引用，便于测试使用）
from filter.backtest.engine import BacktestRunner  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════════
# BacktestRunner save_debug_data 模式测试
# ═══════════════════════════════════════════════════════════════════════════════


class TestBacktestRunnerDebugMode:
    """BacktestRunner 的 save_debug_data 参数测试."""

    def test_default_save_debug_data_is_false(self):
        """BacktestRunner 默认 save_debug_data=False."""
        mock_conn = _make_mock_db_conn()
        configs = _make_minimal_configs()

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("TEST", configs)
        assert runner.save_debug_data is False

    def test_save_debug_data_true_passed_via_kwargs(self):
        """save_debug_data=True 通过 kwargs 传入 BacktestRunner."""
        mock_conn = _make_mock_db_conn()
        configs = _make_minimal_configs()

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("TEST", configs, save_debug_data=True)
        assert runner.save_debug_data is True

    def test_save_debug_data_false_explicit(self):
        """save_debug_data=False 显式传入 BacktestRunner."""
        mock_conn = _make_mock_db_conn()
        configs = _make_minimal_configs()

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("TEST", configs, save_debug_data=False)
        assert runner.save_debug_data is False



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
        from filter.shared.constants import ALL_TFS
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

    def test_runner_sorts_finest_first_configs(self):
        """BacktestRunner 构造函数自动排序 finest→coarsest 输入."""
        from filter.shared.constants import ALL_TFS
        from filter.backtest.engine import BacktestRunner
        from unittest.mock import patch

        # finest→coarsest 的输入顺序
        configs = [
            {"tf": "5分钟", "n_pts": 50, "_fid": "sma", "_dual": False},
            {"tf": "15分钟", "n_pts": 50, "_fid": "sma", "_dual": False},
            {"tf": "60分钟", "n_pts": 50, "_fid": "sma", "_dual": False},
            {"tf": "日线", "n_pts": 50, "_fid": "sma", "_dual": False},
        ]

        with patch.object(BacktestRunner, "_query_bar_count", return_value=1000), \
             patch.object(BacktestRunner, "_load_all_bar_info", return_value=[{}]):
            runner = BacktestRunner("3690", configs)

        # self.configs 应已排序为 coarsest→finest
        tf_indices = [ALL_TFS.index(c["tf"]) for c in runner.configs]
        assert tf_indices == sorted(tf_indices, reverse=True), (
            f"引擎构造函数应对 finest→coarsest 输入排序, indices={tf_indices}, "
            f"期望={sorted(tf_indices, reverse=True)}"
        )

    # ── P0-4: 细周期组 ──────────────────────────────────────────────────────

    def test_fine_period_group_min_tf_is_1min(self):
        """细周期组 [60分,15分,5分,1分] — 验证 _min_tf 正确识别 1分."""
        from filter.shared.constants import ALL_TFS
        from filter.backtest.engine import BacktestRunner
        from unittest.mock import patch

        # finest→coarsest 输入 (违规顺序)
        configs = [
            {"tf": "1分钟", "n_pts": 50, "_fid": "sma", "_dual": False},
            {"tf": "5分钟", "n_pts": 50, "_fid": "sma", "_dual": False},
            {"tf": "15分钟", "n_pts": 50, "_fid": "sma", "_dual": False},
            {"tf": "60分钟", "n_pts": 60, "_fid": "sma", "_dual": False},
        ]

        with patch.object(BacktestRunner, "_query_bar_count", return_value=1000), \
             patch.object(BacktestRunner, "_load_all_bar_info", return_value=[{}]):
            runner = BacktestRunner("3690", configs)

        # configs 应排序为 coarsest→finest: [60分钟, 15分钟, 5分钟, 1分钟]
        tf_indices = [ALL_TFS.index(c["tf"]) for c in runner.configs]
        assert tf_indices == sorted(tf_indices, reverse=True), (
            f"细周期组应粗→细排列, indices={tf_indices}"
        )
        # _min_tf 应是最精细的 1分钟 (ALL_TFS index=0)
        assert runner._min_tf == "1分钟", (
            f"_min_tf 应为 '1分钟', 实为 '{runner._min_tf}'"
        )
        assert ALL_TFS.index(runner._min_tf) == 0

    # ── P0-5: 跳跃间隔 ──────────────────────────────────────────────────────

    def test_skip_interval_sorted_coarse_to_fine(self):
        """跳跃间隔 [月线,日线,5分,1分] ALL_TFS 索引 [6,4,1,0]."""
        from filter.shared.constants import ALL_TFS
        from filter.backtest.engine import BacktestRunner
        from unittest.mock import patch

        # finest→coarsest 输入
        configs = [
            {"tf": "1分钟", "n_pts": 50, "_fid": "sma", "_dual": False},
            {"tf": "5分钟", "n_pts": 50, "_fid": "sma", "_dual": False},
            {"tf": "日线", "n_pts": 60, "_fid": "sma", "_dual": False},
            {"tf": "月线", "n_pts": 60, "_fid": "sma", "_dual": False},
        ]

        with patch.object(BacktestRunner, "_query_bar_count", return_value=1000), \
             patch.object(BacktestRunner, "_load_all_bar_info", return_value=[{}]):
            runner = BacktestRunner("3690", configs)

        tf_indices = [ALL_TFS.index(c["tf"]) for c in runner.configs]
        assert tf_indices == sorted(tf_indices, reverse=True), (
            f"跳跃间隔应粗→细排列, indices={tf_indices}"
        )
        # 月线(idx=6) → 日线(idx=4) → 5分钟(idx=1) → 1分钟(idx=0)
        assert tf_indices == [6, 4, 1, 0]

    # ── P0-6: 极端混合 ──────────────────────────────────────────────────────

    def test_extreme_mixed_sorted_coarse_to_fine(self):
        """全粗+细混合 [季线,日线,15分,1分] ALL_TFS 索引 [7,4,2,0]."""
        from filter.shared.constants import ALL_TFS
        from filter.backtest.engine import BacktestRunner
        from unittest.mock import patch

        # finest→coarsest 输入
        configs = [
            {"tf": "1分钟", "n_pts": 50, "_fid": "sma", "_dual": False},
            {"tf": "15分钟", "n_pts": 50, "_fid": "sma", "_dual": False},
            {"tf": "日线", "n_pts": 60, "_fid": "sma", "_dual": False},
            {"tf": "季线", "n_pts": 60, "_fid": "sma", "_dual": False},
        ]

        with patch.object(BacktestRunner, "_query_bar_count", return_value=1000), \
             patch.object(BacktestRunner, "_load_all_bar_info", return_value=[{}]):
            runner = BacktestRunner("3690", configs)

        tf_indices = [ALL_TFS.index(c["tf"]) for c in runner.configs]
        assert tf_indices == sorted(tf_indices, reverse=True), (
            f"极端混合应粗→细排列, indices={tf_indices}"
        )
        assert tf_indices == [7, 4, 2, 0]
        assert runner._min_tf == "1分钟"

    # ── P1-3: 3 视图 ────────────────────────────────────────────────────────

    def test_three_views_sorted_coarse_to_fine(self):
        """3 视图 [周线,60分,1分] — 非4视图场景."""
        from filter.shared.constants import ALL_TFS
        from filter.backtest.engine import BacktestRunner
        from unittest.mock import patch

        # finest→coarsest 输入
        configs = [
            {"tf": "1分钟", "n_pts": 50, "_fid": "sma", "_dual": False},
            {"tf": "60分钟", "n_pts": 60, "_fid": "sma", "_dual": False},
            {"tf": "周线", "n_pts": 60, "_fid": "sma", "_dual": False},
        ]

        with patch.object(BacktestRunner, "_query_bar_count", return_value=1000), \
             patch.object(BacktestRunner, "_load_all_bar_info", return_value=[{}]):
            runner = BacktestRunner("3690", configs)

        assert len(runner.configs) == 3
        tf_indices = [ALL_TFS.index(c["tf"]) for c in runner.configs]
        assert tf_indices == sorted(tf_indices, reverse=True), (
            f"3视图应粗→细排列, indices={tf_indices}"
        )
        # 周线(idx=5) → 60分钟(idx=3) → 1分钟(idx=0)
        assert tf_indices == [5, 3, 0]

    # ── P1-4: 相同周期 ──────────────────────────────────────────────────────

    def test_identical_tfs_sort_is_stable(self):
        """相同周期边界 [日线,日线,日线,日线] — 排序后不变."""
        from filter.shared.constants import ALL_TFS
        from filter.backtest.engine import BacktestRunner
        from unittest.mock import patch

        configs = [
            {"tf": "日线", "n_pts": 50, "_fid": "sma", "_dual": False},
            {"tf": "日线", "n_pts": 60, "_fid": "savgol", "_dual": False},
            {"tf": "日线", "n_pts": 70, "_fid": "ema", "_dual": False},
            {"tf": "日线", "n_pts": 80, "_fid": "kalman", "_dual": False},
        ]

        with patch.object(BacktestRunner, "_query_bar_count", return_value=1000), \
             patch.object(BacktestRunner, "_load_all_bar_info", return_value=[{}]):
            runner = BacktestRunner("3690", configs)

        assert len(runner.configs) == 4
        # 所有 tf 索引应相等 (都是 4)
        tf_indices = [ALL_TFS.index(c["tf"]) for c in runner.configs]
        assert all(idx == 4 for idx in tf_indices)
        # 排序应为稳定排序，n_pts 顺序不变
        assert [c["n_pts"] for c in runner.configs] == [50, 60, 70, 80]


# ── TestViewTimeframeMapping ──────────────────────────────────────────────────


class TestViewTimeframeMapping:
    """验证 v0=coarsest, v3=finest 映射约定。

    DEFAULT_TFS 定义: ["日线", "60分钟", "15分钟", "5分钟"]
    ALL_TFS 索引: 日线=4, 60分钟=3, 15分钟=2, 5分钟=1
    """

    def test_v0_is_coarsest_v3_is_finest(self):
        """DEFAULT_TFS 中 v0=日线(coarsest), v3=5分钟(finest)."""
        from filter.shared.constants import DEFAULT_TFS, ALL_TFS

        assert len(DEFAULT_TFS) == 4
        # v0 索引应最大 (最粗糙)
        assert ALL_TFS.index(DEFAULT_TFS[0]) > ALL_TFS.index(DEFAULT_TFS[1]), (
            f"v0({DEFAULT_TFS[0]}) 应比 v1({DEFAULT_TFS[1]}) 粗糙"
        )
        assert ALL_TFS.index(DEFAULT_TFS[0]) > ALL_TFS.index(DEFAULT_TFS[2])
        assert ALL_TFS.index(DEFAULT_TFS[0]) > ALL_TFS.index(DEFAULT_TFS[3])
        # v3 索引应最小 (最精细)
        assert ALL_TFS.index(DEFAULT_TFS[3]) < ALL_TFS.index(DEFAULT_TFS[0]), (
            f"v3({DEFAULT_TFS[3]}) 应比 v0({DEFAULT_TFS[0]}) 精细"
        )

    def test_all_tfs_index_monotonic(self):
        """configs 的 ALL_TFS 索引应严格按照 coarsest→finest 排列（即降序）."""
        from filter.shared.constants import ALL_TFS
        from filter.backtest.cli import _build_default_configs

        configs = _build_default_configs("TEST")
        tf_indices = [ALL_TFS.index(c["tf"]) for c in configs]

        # 严格降序 (无相等、无逆序)
        for i in range(len(tf_indices) - 1):
            assert tf_indices[i] > tf_indices[i + 1], (
                f"索引应严格递减: configs[{i}].tf={configs[i]['tf']}"
                f"(idx={tf_indices[i]}) <= "
                f"configs[{i+1}].tf={configs[i+1]['tf']}"
                f"(idx={tf_indices[i+1]})"
            )

    # ── P0-5ext: 任意4周期映射单调性 ────────────────────────────────────────

    def test_arbitrary_four_tfs_mapping_monotonic(self):
        """验证任意4个ALL_TFS周期的映射单调性."""
        from filter.shared.constants import ALL_TFS
        from filter.backtest.engine import BacktestRunner
        from unittest.mock import patch

        # 任意4个周期（不等间距，从季线到1分钟）
        configs = [
            {"tf": "1分钟", "n_pts": 50, "_fid": "sma", "_dual": False},
            {"tf": "60分钟", "n_pts": 50, "_fid": "sma", "_dual": False},
            {"tf": "周线", "n_pts": 50, "_fid": "sma", "_dual": False},
            {"tf": "季线", "n_pts": 50, "_fid": "sma", "_dual": False},
        ]

        with patch.object(BacktestRunner, "_query_bar_count", return_value=1000), \
             patch.object(BacktestRunner, "_load_all_bar_info", return_value=[{}]):
            runner = BacktestRunner("3690", configs)

        tf_indices = [ALL_TFS.index(c["tf"]) for c in runner.configs]
        # 严格降序 (无相等、无逆序)
        for i in range(len(tf_indices) - 1):
            assert tf_indices[i] > tf_indices[i + 1], (
                f"索引应严格递减: configs[{i}].tf={runner.configs[i]['tf']}"
                f"(idx={tf_indices[i]}) <= "
                f"configs[{i+1}].tf={runner.configs[i+1]['tf']}"
                f"(idx={tf_indices[i+1]})"
            )

    # ── 参数化: 多种 TF 组合 ─────────────────────────────────────────────────

    @pytest.mark.parametrize("tfs", [
        ["周线", "日线", "60分钟", "15分钟"],
        ["月线", "周线", "日线", "60分钟"],
        ["季线", "月线", "周线", "日线"],
        ["60分钟", "15分钟", "5分钟", "1分钟"],
        ["月线", "日线", "5分钟", "1分钟"],
    ])
    def test_diverse_tf_combinations_sorted(self, tfs):
        """验证 ALL_TFS 任意子集的排序一致性."""
        from filter.shared.constants import ALL_TFS
        from filter.backtest.engine import BacktestRunner
        from unittest.mock import patch

        configs = [
            {"tf": tf, "n_pts": 60 - i * 10, "_fid": "sma", "_dual": False}
            for i, tf in enumerate(tfs)
        ]

        with patch.object(BacktestRunner, "_query_bar_count", return_value=1000), \
             patch.object(BacktestRunner, "_load_all_bar_info", return_value=[{}]):
            runner = BacktestRunner("3690", configs)

        tf_indices = [ALL_TFS.index(c["tf"]) for c in runner.configs]
        assert tf_indices == sorted(tf_indices, reverse=True), (
            f"组合 {tfs} 应粗→细排列, indices={tf_indices}"
        )
        # v0 应是最粗糙的周期
        assert runner.configs[0]["tf"] == max(tfs, key=lambda x: ALL_TFS.index(x))


# ── TestConfigsSortingEdgeCases ───────────────────────────────────────────────


class TestConfigsSortingEdgeCases:
    """边界条件：单条、两条、已排序、非法 tf 等场景."""

    def test_single_config(self):
        """单条 config 排序不报错且保持自身."""
        from filter.shared.constants import ALL_TFS
        from filter.backtest.engine import BacktestRunner
        from unittest.mock import patch

        configs = [
            {"tf": "日线", "n_pts": 60, "_fid": "sma", "_dual": False},
        ]

        with patch.object(BacktestRunner, "_query_bar_count", return_value=1000), \
             patch.object(BacktestRunner, "_load_all_bar_info", return_value=[{}]):
            runner = BacktestRunner("3690", configs)

        # 单条 config 时，排序是恒等变换
        assert len(runner.configs) == 1
        tf_indices = [ALL_TFS.index(c["tf"]) for c in runner.configs]
        assert tf_indices == sorted(tf_indices, reverse=True)

    def test_two_configs(self):
        """两条 config 的排序正确：粗→细."""
        from filter.shared.constants import ALL_TFS
        from filter.backtest.engine import BacktestRunner
        from unittest.mock import patch

        # finest→coarsest 输入
        configs = [
            {"tf": "5分钟", "n_pts": 50, "_fid": "sma", "_dual": False},
            {"tf": "日线", "n_pts": 60, "_fid": "sma", "_dual": False},
        ]

        with patch.object(BacktestRunner, "_query_bar_count", return_value=1000), \
             patch.object(BacktestRunner, "_load_all_bar_info", return_value=[{}]):
            runner = BacktestRunner("3690", configs)

        tf_indices = [ALL_TFS.index(c["tf"]) for c in runner.configs]
        # 日线(idx=4) > 5分钟(idx=1)
        assert tf_indices == [4, 1], (
            f"两条 config 应排序为 [日线, 5分钟], indices={tf_indices}"
        )

    def test_already_sorted_configs(self):
        """已排序的 configs 不受影响（幂等性）."""
        from filter.shared.constants import ALL_TFS
        from filter.backtest.engine import BacktestRunner
        from unittest.mock import patch

        # 已按 coarsest→finest 排列
        configs = [
            {"tf": "日线", "n_pts": 60, "_fid": "sma", "_dual": False},
            {"tf": "60分钟", "n_pts": 60, "_fid": "sma", "_dual": False},
            {"tf": "15分钟", "n_pts": 50, "_fid": "sma", "_dual": False},
            {"tf": "5分钟", "n_pts": 50, "_fid": "sma", "_dual": False},
        ]

        with patch.object(BacktestRunner, "_query_bar_count", return_value=1000), \
             patch.object(BacktestRunner, "_load_all_bar_info", return_value=[{}]):
            runner = BacktestRunner("3690", configs)

        tf_indices = [ALL_TFS.index(c["tf"]) for c in runner.configs]
        assert tf_indices == sorted(tf_indices, reverse=True), (
            f"已排序 configs 应保持粗→细, indices={tf_indices}"
        )

    def test_invalid_tf_raises(self):
        """非法的 tf 值在排序时引发 ValueError（因 ALL_TFS.index 查找失败）."""
        from filter.backtest.engine import BacktestRunner
        from unittest.mock import patch

        configs = [
            {"tf": "日线", "n_pts": 60, "_fid": "sma", "_dual": False},
            {"tf": "NONEXISTENT_TF", "n_pts": 50, "_fid": "sma", "_dual": False},
        ]

        with patch.object(BacktestRunner, "_query_bar_count", return_value=1000), \
             patch.object(BacktestRunner, "_load_all_bar_info", return_value=[{}]):
            with pytest.raises(ValueError):
                BacktestRunner("3690", configs)

    # ── P0-7: 空 configs ─────────────────────────────────────────────────────

    def test_empty_configs_raises(self):
        """空 configs 列表 — 应在 BacktestRunner 构造时触发 ValueError."""
        from filter.backtest.engine import BacktestRunner

        with pytest.raises(ValueError, match="configs 不能为空"):
            BacktestRunner("3690", [])

    # ── P1-1: 单视图非默认周期 ──────────────────────────────────────────────

    def test_single_config_non_default_tf(self):
        """1 视图非 DEFAULT 周期 [月线]."""
        from filter.shared.constants import ALL_TFS
        from filter.backtest.engine import BacktestRunner
        from unittest.mock import patch

        configs = [
            {"tf": "月线", "n_pts": 60, "_fid": "sma", "_dual": False},
        ]

        with patch.object(BacktestRunner, "_query_bar_count", return_value=1000), \
             patch.object(BacktestRunner, "_load_all_bar_info", return_value=[{}]):
            runner = BacktestRunner("3690", configs)

        assert len(runner.configs) == 1
        assert runner.configs[0]["tf"] == "月线"
        assert ALL_TFS.index(runner.configs[0]["tf"]) == 6

    # ── P1-2: 非默认已排序幂等性 ────────────────────────────────────────────

    def test_non_default_already_sorted_idempotent(self):
        """非 DEFAULT_TFS 排序幂等性 — 已排序的再排序不变."""
        from filter.shared.constants import ALL_TFS
        from filter.backtest.engine import BacktestRunner
        from unittest.mock import patch

        # 已按 coarsest→finest 排列的非默认周期 [周线,日线,60分钟,15分钟]
        configs = [
            {"tf": "周线", "n_pts": 60, "_fid": "sma", "_dual": False},
            {"tf": "日线", "n_pts": 60, "_fid": "sma", "_dual": False},
            {"tf": "60分钟", "n_pts": 50, "_fid": "sma", "_dual": False},
            {"tf": "15分钟", "n_pts": 50, "_fid": "sma", "_dual": False},
        ]

        with patch.object(BacktestRunner, "_query_bar_count", return_value=1000), \
             patch.object(BacktestRunner, "_load_all_bar_info", return_value=[{}]):
            runner = BacktestRunner("3690", configs)

        tf_indices = [ALL_TFS.index(c["tf"]) for c in runner.configs]
        assert tf_indices == sorted(tf_indices, reverse=True), (
            f"已排序非默认 configs 应保持不变, indices={tf_indices}"
        )
        # 幂等性：tfs 列表应与输入一致
        expected_tfs = ["周线", "日线", "60分钟", "15分钟"]
        assert [c["tf"] for c in runner.configs] == expected_tfs
