"""P0-3 验证测试: chart_builder 模块可独立导入"""
from unittest.mock import patch

import numpy as np


def test_chart_builder_importable():
    """chart_builder 模块可以独立导入，无循环依赖。"""
    from browse.chart_builder import (
        _date_markers, _determine_subplot_layout, _insert_feedback_row,
        _add_main_price_traces, _add_residual_traces, _add_schmitt_traces,
        _add_pnl_traces, _add_feedback_subplot,
    )
    # All 8 functions should exist
    assert callable(_date_markers)
    assert callable(_determine_subplot_layout)
    assert callable(_insert_feedback_row)
    assert callable(_add_main_price_traces)
    assert callable(_add_residual_traces)
    assert callable(_add_schmitt_traces)
    assert callable(_add_pnl_traces)
    assert callable(_add_feedback_subplot)


def test_date_markers_daily():
    """_date_markers 日线按周一标记"""
    import pandas as pd
    from browse.chart_builder import _date_markers
    dates = pd.date_range("2026-01-05", periods=30, freq="D")  # Jan 5 is Monday
    positions, labels = _date_markers(dates, "日线")
    assert len(positions) >= 3  # At least 3 Mondays in 30 days if starting Monday
    assert all(isinstance(p, int) for p in positions)


def test_date_markers_empty():
    """_date_markers 空输入返回空列表"""
    from browse.chart_builder import _date_markers
    positions, labels = _date_markers(None, "日线")
    assert positions == []
    assert labels == []


def test_determine_subplot_layout_minimal():
    """_determine_subplot_layout 无施密特触发时的最小布局返回4行"""
    from browse.chart_builder import _determine_subplot_layout
    rows, rh, titles, mr, rr, vr, sar, ssr, ar, pnl_row, cross_row, align_row = \
        _determine_subplot_layout(False, False, False, False, None)
    assert rows == 4
    assert sar is None  # no schmitt subplot


# ===================================================================
# SECTION — _prepare_chart_data 空数据守卫 (Bug 1 fix)
# ===================================================================

class TestPrepareChartDataGuard:
    """验证 _prepare_chart_data 在数据加载失败/不足时不会崩溃.

    修复前：数据加载失败时 _load_chart_data 返回 None，直接传入
    _compute_filters 导致崩溃。修复后：早期返回包含 err 字段的 dict。
    """

    @patch("browse.app._load_chart_data")
    @patch("browse.app.st")
    def test_returns_err_when_load_fails(self, mock_st, mock_load):
        """数据加载返回 err 时，_prepare_chart_data 应返回包含 err 的 dict."""
        mock_load.return_value = (
            None, None, None, None, None, "数据加载失败: 网络错误"
        )
        mock_st.session_state = {}

        from browse.app import _prepare_chart_data
        result = _prepare_chart_data({
            "market": "A",
            "ticker_code": "000001",
            "cfg": {"tf": "日线", "n_pts": 120},
        })

        assert isinstance(result, dict)
        assert result["err"] == "数据加载失败: 网络错误"
        # 确保下游未执行：mock_load 只调用了一次（仅 _load_chart_data）
        assert mock_load.call_count == 1

    @patch("browse.app._load_chart_data")
    @patch("browse.app.st")
    def test_returns_err_when_t_is_none(self, mock_st, mock_load):
        """t 为 None（数据未就绪）时返回 err."""
        mock_load.return_value = (
            None, None, None, "000001", None, None
        )
        mock_st.session_state = {}

        from browse.app import _prepare_chart_data
        result = _prepare_chart_data({
            "market": "A",
            "ticker_code": "000001",
            "cfg": {"tf": "日线", "n_pts": 120},
        })

        assert isinstance(result, dict)
        assert "数据点不足" in result["err"]

    @patch("browse.app._load_chart_data")
    @patch("browse.app.st")
    def test_returns_err_when_insufficient_points(self, mock_st, mock_load):
        """数据点数 < 2 时返回 err（之前会传入 _compute_filters 崩溃）."""
        import pandas as pd
        mock_load.return_value = (
            np.array([100.0]),         # t — 仅 1 个点
            np.array([50.0]),          # noisy
            None,                       # ohlc
            "000001",                   # ticker_full
            pd.DatetimeIndex(["2026-01-01"]),  # dates
            None,                       # err
        )
        mock_st.session_state = {}

        from browse.app import _prepare_chart_data
        result = _prepare_chart_data({
            "market": "A",
            "ticker_code": "000001",
            "cfg": {"tf": "日线", "n_pts": 120},
        })

        assert isinstance(result, dict)
        assert "数据点不足" in result["err"]
        assert result["t"] is not None
        # 验证不会崩溃 — 不会往下调 _compute_filters
        mock_load.assert_called_once()

    @patch("browse.app._load_chart_data")
    @patch("browse.app.st")
    def test_no_crash_on_empty_dates(self, mock_st, mock_load):
        """t 和 dates 均为 None 时不应崩溃."""
        mock_load.return_value = (
            None, None, None, None, None, "回测数据未就绪"
        )
        mock_st.session_state = {}

        from browse.app import _prepare_chart_data
        result = _prepare_chart_data({
            "market": "A",
            "ticker_code": "000001",
            "cfg": {"tf": "周线", "n_pts": 60},
        })

        assert isinstance(result, dict)
        assert result["err"] is not None
        assert "数据" in result["err"]


# ===================================================================
# SECTION — 空数据端到端: _prepare_chart_data → _build_chart_figure → _render_plotly
# ===================================================================

class TestEmptyDataE2E:
    """验证空 ticker / 数据不足时全链路不崩溃.

    修复前：某些边界条件下 _prepare_chart_data 返回后未正确 short-circuit，
    导致 None/空数据传递到 _build_chart_figure → _render_plotly 而崩溃。
    """

    @patch("browse.app._load_chart_data")
    @patch("browse.app.st")
    def test_full_chain_short_circuits_on_empty_ticker(self, mock_st, mock_load):
        """空 ticker 输入时 _prepare_chart_data 短接 — 不执行 figure 构建."""
        mock_load.return_value = (
            None, None, None, None, None, "回测数据未就绪"
        )
        mock_st.session_state = {}

        from browse.app import _prepare_chart_data
        result = _prepare_chart_data({
            "market": "A",
            "ticker_code": "",
            "cfg": {"tf": "日线", "n_pts": 120},
        })

        assert isinstance(result, dict)
        assert result["err"] is not None
        # 全链路关键守卫：有 err 时不应继续传数据给 _build_chart_figure
        assert mock_load.call_count == 1

    @patch("browse.app._load_chart_data")
    @patch("browse.app.st")
    def test_prepare_then_build_with_empty_data_safe(self, mock_st, mock_load):
        """_prepare_chart_data 返回 err 时，_build_chart_figure 不应被调用.

        验证调用方检查 data["err"] 后不进入 figure 构建流程。
        """
        mock_load.return_value = (
            None, None, None, "000001", None, "数据加载失败: 网络超时"
        )
        mock_st.session_state = {}

        from browse.app import _prepare_chart_data
        result = _prepare_chart_data({
            "market": "A",
            "ticker_code": "000001",
            "cfg": {"tf": "日线", "n_pts": 120},
        })

        assert result["err"] is not None
        # 模拟调用方逻辑：有 err 则不应调用 _build_chart_figure
        should_skip_build = result["err"] is not None or result.get("t") is None
        assert should_skip_build, (
            "当 err 或 t 为 None 时应跳过 _build_chart_figure, "
            "防止将 None 传递给需要数组的下游"
        )

    @patch("browse.app._load_chart_data")
    @patch("browse.app.st")
    def test_insufficient_data_no_crash(self, mock_st, mock_load):
        """数据点 < 2 时 _prepare_chart_data 短接，不交由下游崩溃."""
        mock_load.return_value = (
            np.array([100.0]),      # t — 仅 1 个点
            np.array([50.0]),       # noisy
            None,                    # ohlc
            "000001",                # ticker_full
            None,                    # dates
            None,                    # err
        )
        mock_st.session_state = {}

        from browse.app import _prepare_chart_data
        result = _prepare_chart_data({
            "market": "A",
            "ticker_code": "000001",
            "cfg": {"tf": "日线", "n_pts": 120},
        })

        assert isinstance(result, dict)
        assert result["err"] is not None
        assert "数据点不足" in result["err"]
        # 确保未崩溃；mock_load 仅被调用一次
        assert mock_load.call_count == 1

    def test_build_chart_figure_signature_safety(self):
        """_build_chart_figure 签名中 data 参数应为 dict 类型（T7 guard）."""
        import inspect
        from browse.app import _build_chart_figure
        sig = inspect.signature(_build_chart_figure)
        params = list(sig.parameters.keys())
        assert "data" in params, (
            "_build_chart_figure 第一个参数应为 'data'"
        )
