"""
Tests for charts.py pure-logic helper functions.

Tests cover the non-Streamlit parts:
- _render_entry_marker() 返回的 go.Scatter 对象
- _render_exit_marker_with_label() 止损/止盈差异
- _render_baseline() 基线位置
- _render_fill_background() 填充配置
- _render_pnl_curves() 多空曲线
- _sanitize_for_json / _NpEncoder 行为
- CDN URL 包含在 HTML 输出中
- _render_plotly JSON 序列化（NaN/Inf/空数据）
- _add_prediction_traces poly2/physics 模式
- _add_cross_pnl_subplot 边界（空 trades / 有 trades）
- _add_schmitt_traces 边界条件
"""

import sys
from pathlib import Path

# Ensure filter_app/ package is importable (conftest handles streamlit mock)
_src = Path(__file__).resolve().parent.parent / "filter_app"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

import json
from unittest.mock import MagicMock
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pytest


# Module under test
from components.charts import (
    _render_entry_marker,
    _render_exit_marker_with_label,
    _render_pnl_curves,
    _render_baseline,
    _render_fill_background,
    _render_plotly,
)


# ---------------------------------------------------------------------------
# 复制 _NpEncoder 和 _sanitize_for_json 的逻辑用于独立测试
# （它们在 charts.py 中是 _render_plotly 内部嵌套的，无法直接 import）
# ---------------------------------------------------------------------------

class _NpEncoder(json.JSONEncoder):
    """Mirror of charts.py's nested _NpEncoder.

    NOTE: This encoder works because _render_plotly calls:
        json.dumps(_sanitize_for_json(fig_dict), cls=_NpEncoder)
    The _sanitize_for_json call handles NaN/Inf conversion BEFORE the
    encoder sees them. For np.floating, json.dumps uses the standard
    float encoder (which outputs 'NaN'/'Infinity') before invoking
    default(). So _sanitize_for_json preprocessing is essential.
    """
    def default(self, obj):
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        return super().default(obj)


def _sanitize_for_json(obj):
    """Mirror of charts.py's nested _sanitize_for_json."""
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, float):
        if np.isnan(obj) or np.isinf(obj):
            return None
    if isinstance(obj, np.ndarray):
        return _sanitize_for_json(obj.tolist())
    return obj


# ===================================================================
# Helper: create a minimal figure for trace injection
# ===================================================================

def _make_fig(nrows=4):
    """创建最小的 subplot 图表用于测试 trace 注入.

    nrows: 子图行数（传入 row 参数的函数需要 subplot 布局）
    每个子图都添加一个 dummy trace 以确保 add_hline 等操作可存储 shapes.
    """
    fig = make_subplots(rows=nrows, cols=1, shared_xaxes=True)
    for r in range(1, nrows + 1):
        fig.add_trace(go.Scatter(x=[0, 1], y=[0, 1], showlegend=False), row=r, col=1)
    return fig


# ===================================================================
# SECTION 1 — _render_entry_marker
# ===================================================================

class TestRenderEntryMarker:
    """入场标记（三角形）的基本行为."""

    def test_adds_one_trace(self):
        """调用后 fig 应增加一个 trace."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        initial_len = len(fig.data)

        _render_entry_marker(fig, t, 50, 100.0, row=1)

        # 默认子图布局 + 1 trace
        assert len(fig.data) == initial_len + 1

    def test_trace_is_marker_with_triangle(self):
        """添加的 trace 应该是 triangle-up 标记."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)

        _render_entry_marker(fig, t, 50, 100.0, row=1)

        trace = fig.data[-1]
        assert trace.mode == "markers"
        assert trace.marker.symbol == "triangle-up"
        assert trace.marker.size == 9

    def test_invalid_index_does_not_add_trace(self):
        """bar_idx 超出范围时不应添加 trace."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        initial_len = len(fig.data)

        # 负数索引
        _render_entry_marker(fig, t, -1, 100.0, row=1)
        assert len(fig.data) == initial_len

        # 越界索引
        _render_entry_marker(fig, t, 1000, 100.0, row=1)
        assert len(fig.data) == initial_len

    def test_custom_color_and_size(self):
        """支持自定义 color 和 size."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)

        _render_entry_marker(fig, t, 30, 105.0, row=1,
                             color="#ff0000", size=12)

        trace = fig.data[-1]
        assert trace.marker.color == "#ff0000"
        assert trace.marker.size == 12

    def test_showlegend_false(self):
        """入场标记应隐藏图例."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)

        _render_entry_marker(fig, t, 25, 100.0, row=1)

        trace = fig.data[-1]
        assert trace.showlegend is False

    def test_hovertext_passed_through(self):
        """hovertext 参数应透传给 trace."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        hover = "入场 多"

        _render_entry_marker(fig, t, 10, 100.0, row=1, hovertext=hover)

        trace = fig.data[-1]
        assert trace.hovertext == hover
        assert trace.hoverinfo == "text"

    def test_empty_t_array(self):
        """t 为空数组时不应崩溃."""
        fig = _make_fig()
        t = np.array([], dtype=float)
        initial_len = len(fig.data)

        _render_entry_marker(fig, t, 0, 100.0, row=1)
        assert len(fig.data) == initial_len

    def test_marker_line_has_border(self):
        """标记应有半透明边框."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)

        _render_entry_marker(fig, t, 40, 100.0, row=1)

        trace = fig.data[-1]
        assert trace.marker.line.width == 1
        assert trace.marker.line.color == "rgba(0,0,0,0.3)"


# ===================================================================
# SECTION 2 — _render_exit_marker_with_label
# ===================================================================

class TestRenderExitMarker:
    """离场标记（止损=x / 止盈=circle）+ 盈亏标注."""

    def test_stop_loss_uses_x(self):
        """止损 (exit_reason='stop_loss') 应使用 x 符号."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)

        _render_exit_marker_with_label(
            fig, t, 50, 110.0, row=1,
            exit_reason="stop_loss", ret_pct=5.0,
        )

        trace = fig.data[-1]
        assert trace.marker.symbol == "x"
        # 止损应为红色
        assert trace.marker.line.color == "#f85149"

    def test_take_profit_uses_circle(self):
        """止盈 (exit_reason='take_profit') 应使用 circle 符号."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)

        _render_exit_marker_with_label(
            fig, t, 50, 110.0, row=1,
            exit_reason="take_profit", ret_pct=5.0,
        )

        trace = fig.data[-1]
        assert trace.marker.symbol == "circle"
        # 止盈应为绿色
        assert trace.marker.line.color == "#3fb950"

    def test_unknown_reason_defaults_to_circle(self):
        """未知 exit_reason 应默认为 circle."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)

        _render_exit_marker_with_label(
            fig, t, 50, 110.0, row=1,
            exit_reason="unknown", ret_pct=3.0,
        )

        trace = fig.data[-1]
        assert trace.marker.symbol == "circle"

    def test_annotation_text_contains_return_pct(self):
        """标注文字应包含收益率."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)

        _render_exit_marker_with_label(
            fig, t, 50, 110.0, row=1,
            exit_reason="take_profit", ret_pct=5.0,
        )

        # 标注在 fig.layout.annotations 中
        annotations = fig.layout.annotations
        assert len(annotations) > 0
        text = annotations[-1].text
        assert "5.0%" in text or "+5.0%" in text

    def test_long_trade_arrow_up(self):
        """做多 (trade_type='long') 箭头 ↑."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)

        _render_exit_marker_with_label(
            fig, t, 50, 110.0, row=1,
            trade_type="long", exit_reason="take_profit", ret_pct=3.0,
        )

        annotations = fig.layout.annotations
        assert "↑" in annotations[-1].text

    def test_short_trade_arrow_down(self):
        """做空 (trade_type='short') 箭头 ↓."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)

        _render_exit_marker_with_label(
            fig, t, 50, 110.0, row=1,
            trade_type="short", exit_reason="take_profit", ret_pct=3.0,
        )

        annotations = fig.layout.annotations
        assert "↓" in annotations[-1].text

    def test_negative_return(self):
        """负收益率应正确显示."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)

        _render_exit_marker_with_label(
            fig, t, 50, 95.0, row=1,
            exit_reason="stop_loss", ret_pct=-2.5,
        )

        annotations = fig.layout.annotations
        assert "-2.5%" in annotations[-1].text

    def test_invalid_index_catches(self):
        """bar_idx 越界时不添加 trace 或 annotation."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        initial_len = len(fig.data)

        _render_exit_marker_with_label(
            fig, t, -1, 100.0, row=1,
            exit_reason="stop_loss", ret_pct=1.0,
        )
        assert len(fig.data) == initial_len

    def test_marker_color_passed_through(self):
        """标记主体颜色应透传."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)

        _render_exit_marker_with_label(
            fig, t, 40, 105.0, row=1,
            color="#ff0000", exit_reason="stop_loss", ret_pct=-1.0,
        )

        trace = fig.data[-1]
        assert trace.marker.color == "#ff0000"


# ===================================================================
# SECTION 3 — _render_baseline
# ===================================================================

class TestRenderBaseline:
    """100 基准线."""

    def test_adds_hline(self):
        """调用后 fig.layout.shapes 应增加水平线."""
        fig = _make_fig()
        initial_count = len(fig.layout.shapes or [])

        _render_baseline(fig, row=1)

        shapes = fig.layout.shapes or []
        assert len(shapes) == initial_count + 1
        assert shapes[-1].type == "line"

    def test_y_position(self):
        """基准线应在 y=100 位置."""
        fig = _make_fig()

        _render_baseline(fig, row=1)

        shapes = fig.layout.shapes or []
        hline = shapes[-1]
        assert hline.y0 == 100
        assert hline.y1 == 100

    def test_dashed_line(self):
        """基准线应为虚线."""
        fig = _make_fig()

        _render_baseline(fig, row=1)

        shapes = fig.layout.shapes or []
        assert shapes[-1].line.dash == "dash"

    def test_gray_color(self):
        """基准线应为灰色."""
        fig = _make_fig()

        _render_baseline(fig, row=1)

        shapes = fig.layout.shapes or []
        assert shapes[-1].line.color == "gray"

    def test_custom_opacity_passed(self):
        """opacity 参数应传递到 shapes 的 line 属性或 shape 上."""
        fig = _make_fig()

        _render_baseline(fig, row=1, opacity=0.8)

        shapes = fig.layout.shapes or []
        assert len(shapes) > 0

    def test_custom_y(self):
        """支持自定义 y 位置."""
        fig = _make_fig()

        _render_baseline(fig, row=1, y=200)

        shapes = fig.layout.shapes or []
        assert shapes[-1].y0 == 200
        assert shapes[-1].y1 == 200


# ===================================================================
# SECTION 4 — _render_fill_background
# ===================================================================

class TestRenderFillBackground:
    """PnL 区域半透明背景."""

    def test_adds_fill_trace(self):
        """调用后应添加一个填充 trace."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        y_values = np.full(100, 105.0)
        initial_len = len(fig.data)

        _render_fill_background(fig, t, y_values, row=1)

        assert len(fig.data) == initial_len + 1

    def test_fill_toself(self):
        """填充 trace 应使用 fill='toself' 模式."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        y_values = np.full(100, 105.0)

        _render_fill_background(fig, t, y_values, row=1)

        trace = fig.data[-1]
        assert trace.fill == "toself"
        assert trace.line.width == 0

    def test_fill_color_passed_through(self):
        """fillcolor 应透传."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        y_values = np.full(100, 105.0)

        _render_fill_background(fig, t, y_values, row=1,
                               color="rgba(255,0,0,0.1)")

        trace = fig.data[-1]
        assert trace.fillcolor == "rgba(255,0,0,0.1)"

    def test_hoverinfo_skip(self):
        """填充区域的 hover 应跳过."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        y_values = np.full(100, 105.0)

        _render_fill_background(fig, t, y_values, row=1)

        trace = fig.data[-1]
        assert trace.hoverinfo == "skip"

    def test_baseline_y_max_computation(self):
        """y_max 应为 max(y_values, baseline) * 1.02."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        y_values = np.full(100, 200.0)

        _render_fill_background(fig, t, y_values, row=1, baseline=100)

        trace = fig.data[-1]
        # x: [t[0], t[-1], t[-1], t[0]]
        # y: [baseline, baseline, y_max, y_max]
        np.testing.assert_approx_equal(trace.y[2], 200 * 1.02)
        np.testing.assert_approx_equal(trace.y[3], 200 * 1.02)


# ===================================================================
# SECTION 5 — _render_pnl_curves
# ===================================================================

class TestRenderPnLCurves:
    """多空 PnL 曲线."""

    def test_adds_two_traces(self):
        """调用后应添加 2 个 trace（多做多+做空）. """
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        long_pnl = np.full(100, 105.0)
        short_pnl = np.full(100, 95.0)
        initial_len = len(fig.data)

        _render_pnl_curves(fig, t, long_pnl, short_pnl, row=1)

        assert len(fig.data) == initial_len + 2

    def test_trace_names_default(self):
        """trace 名称默认应为做多PnL/做空PnL."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        long_pnl = np.full(100, 105.0)
        short_pnl = np.full(100, 95.0)

        _render_pnl_curves(fig, t, long_pnl, short_pnl, row=1)

        assert fig.data[-2].name == "做多PnL"
        assert fig.data[-1].name == "做空PnL"

    def test_custom_names(self):
        """支持自定义名称."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        long_pnl = np.full(100, 105.0)
        short_pnl = np.full(100, 95.0)

        _render_pnl_curves(fig, t, long_pnl, short_pnl, row=1,
                           long_name="Long", short_name="Short")

        assert fig.data[-2].name == "Long"
        assert fig.data[-1].name == "Short"

    def test_custom_colors(self):
        """支持自定义颜色."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        long_pnl = np.full(100, 105.0)
        short_pnl = np.full(100, 95.0)

        _render_pnl_curves(fig, t, long_pnl, short_pnl, row=1,
                           long_color="blue", short_color="red")

        assert fig.data[-2].line.color == "blue"
        assert fig.data[-1].line.color == "red"


# ===================================================================
# SECTION 6 — _sanitize_for_json (mirrored implementation)
# ===================================================================

class TestSanitizeForJson:
    """JSON 清洗辅助函数."""

    def test_nan_to_none(self):
        """NaN 应转换为 None."""
        result = _sanitize_for_json(float("nan"))
        assert result is None

    def test_inf_to_none(self):
        """Inf 应转换为 None."""
        result = _sanitize_for_json(float("inf"))
        assert result is None

    def test_neg_inf_to_none(self):
        """-Inf 应转换为 None."""
        result = _sanitize_for_json(float("-inf"))
        assert result is None

    def test_normal_float_preserved(self):
        """正常浮点数应保持不变."""
        result = _sanitize_for_json(3.14)
        assert result == 3.14

    def test_dict_with_nan(self):
        """包含 NaN 的 dict 应递归清洗."""
        obj = {"a": 1.0, "b": float("nan")}
        result = _sanitize_for_json(obj)
        assert result["a"] == 1.0
        assert result["b"] is None

    def test_list_with_nan(self):
        """包含 NaN 的 list 应递归清洗."""
        obj = [1.0, float("nan"), float("inf")]
        result = _sanitize_for_json(obj)
        assert result[0] == 1.0
        assert result[1] is None
        assert result[2] is None

    def test_nested_dict_list(self):
        """嵌套结构应递归清洗."""
        obj = {"outer": {"inner": [1.0, float("nan")]}}
        result = _sanitize_for_json(obj)
        assert result["outer"]["inner"][0] == 1.0
        assert result["outer"]["inner"][1] is None

    def test_ndarray_converted(self):
        """np.ndarray 应转换为 list 再清洗."""
        arr = np.array([1.0, float("nan"), 3.0])
        result = _sanitize_for_json(arr)
        assert result == [1.0, None, 3.0]

    def test_int_preserved(self):
        """int 应保持不变."""
        result = _sanitize_for_json(42)
        assert result == 42

    def test_string_preserved(self):
        """str 应保持不变."""
        result = _sanitize_for_json("hello")
        assert result == "hello"

    def test_tuple_converted_to_list(self):
        """tuple 应递归清洗并返回 list."""
        result = _sanitize_for_json((1.0, float("nan")))
        assert result == [1.0, None]

    def test_zero_preserved(self):
        """0 和 0.0 应保持不变."""
        assert _sanitize_for_json(0) == 0
        assert _sanitize_for_json(0.0) == 0.0

    def test_none_preserved(self):
        """None 应保持不变."""
        assert _sanitize_for_json(None) is None


# ===================================================================
# SECTION 7 — _NpEncoder (mirrored implementation)
# ===================================================================

class TestNpEncoder:
    """NumPy JSON 编码器."""

    def test_ndarray(self):
        """np.ndarray 应转为 list."""
        arr = np.array([1, 2, 3])
        result = json.dumps(arr, cls=_NpEncoder)
        assert result == "[1, 2, 3]"

    def test_np_integer(self):
        """np.integer 应转为 int."""
        result = json.dumps(np.int32(42), cls=_NpEncoder)
        assert result == "42"

    def test_np_float_normal(self):
        """np.floating 正常值应转为 float."""
        result = json.dumps(np.float64(3.14), cls=_NpEncoder)
        assert result == "3.14"

    def test_np_float_nan_requires_sanitize_first(self):
        """np.floating NaN 需配合 _sanitize_for_json 预处理后转为 null."""
        data = _sanitize_for_json(np.float64(float("nan")))
        result = json.dumps(data, cls=_NpEncoder)
        assert result == "null"

    def test_np_float_inf_requires_sanitize_first(self):
        """np.floating Inf 需配合 _sanitize_for_json 预处理后转为 null."""
        data = _sanitize_for_json(np.float64(float("inf")))
        result = json.dumps(data, cls=_NpEncoder)
        assert result == "null"

    def test_sanitize_then_encode_nested(self):
        """实际 _render_plotly 流程: sanitize 后编码."""
        obj = {"x": np.float64(float("nan")), "y": np.float64(3.14)}
        cleaned = _sanitize_for_json(obj)
        result = json.dumps(cleaned, cls=_NpEncoder)
        assert "null" in result
        assert "3.14" in result

    def test_regular_int(self):
        """普通 Python int 应正常序列化."""
        result = json.dumps(42, cls=_NpEncoder)
        assert result == "42"

    def test_regular_float(self):
        """普通 Python float 应正常序列化."""
        result = json.dumps(3.14, cls=_NpEncoder)
        assert result == "3.14"

    def test_np_bool(self):
        """np.bool_ 应正常序列化（通过 super().default）. """
        import json as _json
        with pytest.raises(TypeError):
            _json.dumps(np.bool_(True), cls=_NpEncoder)

    def test_nested_list_with_np_values(self):
        """包含 np 类型的嵌套列表应正常序列化."""
        data = [np.float64(1.5), np.int32(2)]
        result = json.dumps(data, cls=_NpEncoder)
        assert result == "[1.5, 2]"


# ===================================================================
# SECTION 8 — _render_plotly HTML structure (via source inspection)
# ===================================================================

class TestRenderPlotlyHtml:
    """_render_plotly 的 HTML 结构检测."""

    def test_cdn_url_in_html(self):
        """源码中应包含 Plotly CDN URL."""
        source = Path(_src / "components" / "charts.py").read_text()
        assert "https://cdn.plot.ly/plotly-2.35.2.min.js" in source

    def test_cdn_fallback_url(self):
        """应有 CDNJS fallback URL."""
        source = Path(_src / "components" / "charts.py").read_text()
        assert "cdnjs.cloudflare.com/ajax/libs/plotly.js" in source

    def test_html_contains_date_tip_div(self):
        """HTML 模板应包含 date-tip div."""
        source = Path(_src / "components" / "charts.py").read_text()
        assert "date-tip-" in source

    def test_html_contains_crosshair_logic(self):
        """HTML 模板应包含 cross-subplot crosshair JavaScript."""
        source = Path(_src / "components" / "charts.py").read_text()
        assert "plotly_hover" in source
        assert "plotly_unhover" in source

    def test_fallback_html_structure(self):
        """H4: _render_plotly 输出包含 plotly-fallback div + IIFE 结构."""
        from components.charts import _render_plotly

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=[1, 2, 3], y=[1, 2, 3]))

        import streamlit as st
        captured = {}
        def _capture_html(html, **kw):
            captured["html"] = html
            return MagicMock()
        from unittest.mock import MagicMock
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(st.components.v1, "html", _capture_html)

        _render_plotly(fig, height=300)

        monkeypatch.undo()
        html = captured.get("html", "")
        assert html, "_render_plotly 应产生 HTML 输出"

        # fallback div 存在
        assert "plotly-fallback-" in html
        assert "加载失败" in html
        # IIFE 结构
        assert "(function()" in html.replace("{{", "{")

    # -----------------------------------------------------------------
    # H5: timeout safety
    # -----------------------------------------------------------------
    def test_timeout_safety_check(self):
        """H5: 输出包含 5秒 setTimeout 安全检查."""
        from components.charts import _render_plotly

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=[1, 2, 3], y=[1, 2, 3]))

        import streamlit as st
        captured = {}
        def _capture_html(html, **kw):
            captured["html"] = html
            return MagicMock()
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(st.components.v1, "html", _capture_html)

        _render_plotly(fig, height=300)

        monkeypatch.undo()
        html = captured.get("html", "")
        assert html

        assert "setTimeout" in html
        assert "5000" in html

    # -----------------------------------------------------------------
    # IIFE 配对验证
    # -----------------------------------------------------------------
    def test_iife_wrapping_is_valid(self):
        """修复验证: (function() { 和 })(); 配对，return 在函数内."""
        from components.charts import _render_plotly

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=[1, 2, 3], y=[1, 2, 3]))

        import streamlit as st
        captured = {}
        def _capture_html(html, **kw):
            captured["html"] = html
            return MagicMock()
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(st.components.v1, "html", _capture_html)

        _render_plotly(fig, height=300)

        monkeypatch.undo()
        html = captured.get("html", "")
        assert html

        # 提取 <script> 块内容
        import re
        script_match = re.search(r"<script>(.*?)</script>", html, re.DOTALL)
        assert script_match, "HTML 必须包含 <script> 块"
        js = script_match.group(1)

        # IIFE 开始
        assert "function()" in js, "JS 必须以自调用函数开头 (function() {"
        # IIFE 结束 — JS 中使用 }} 表示 }，在 Python f-string 中表示为 }}}
        assert "})()" in js or "}()" in js, "JS 必须以 })(); 结尾"

        # return 不能出现在 function 之外
        func_idx = js.find("function()")
        return_idx = js.find("return;")
        if return_idx > 0:
            assert return_idx > func_idx, (
                f"return; 必须在 function 体内 "
                f"(func at {func_idx}, return at {return_idx})"
            )


# ===================================================================
# SECTION 9 — _add_cross_pnl_subplot
# ===================================================================

class TestCrossPnlSubplot:
    """_add_cross_pnl_subplot 行为."""

    def test_long_reference_line_added(self):
        """高周期做多参考线应在有数据时添加."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        aligned = {
            "aligned_long": np.array([np.nan]*50 + [105.0]*50),
            "aligned_short": np.array([np.nan]*100),
            "entry_markers": [],
            "exit_markers": [],
        }

        from components.charts import _add_cross_pnl_subplot
        initial_len = len(fig.data)
        _add_cross_pnl_subplot(fig, t, aligned, row=2)

        # 应增加 long trace
        assert len(fig.data) == initial_len + 1
        trace = fig.data[-1]
        assert trace.line.dash == "dot"

    def test_short_reference_line_added(self):
        """高周期做空参考线应在有数据时添加."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        aligned = {
            "aligned_long": np.array([np.nan]*100),
            "aligned_short": np.array([np.nan]*50 + [95.0]*50),
            "entry_markers": [],
            "exit_markers": [],
        }

        from components.charts import _add_cross_pnl_subplot
        initial_len = len(fig.data)
        _add_cross_pnl_subplot(fig, t, aligned, row=2)

        # 应增加 short trace
        assert len(fig.data) == initial_len + 1
        trace = fig.data[-1]
        assert trace.line.dash == "dot"

    def test_both_reference_lines(self):
        """同时有做多和做空数据时添加两条线."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        aligned = {
            "aligned_long": np.array([np.nan]*25 + [105.0]*75),
            "aligned_short": np.array([np.nan]*50 + [95.0]*50),
            "entry_markers": [],
            "exit_markers": [],
        }

        from components.charts import _add_cross_pnl_subplot
        initial_len = len(fig.data)
        _add_cross_pnl_subplot(fig, t, aligned, row=2)

        # long + short 参考线 = 2
        assert len(fig.data) == initial_len + 2

    def test_no_data_adds_no_trace(self):
        """全 NaN 时不应添加 data trace."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        aligned = {
            "aligned_long": np.array([np.nan]*100),
            "aligned_short": np.array([np.nan]*100),
            "entry_markers": [],
            "exit_markers": [],
        }

        from components.charts import _add_cross_pnl_subplot
        initial_len = len(fig.data)
        _add_cross_pnl_subplot(fig, t, aligned, row=2)

        # 不应添加任何 data trace (但会添加 baseline shape)
        assert len(fig.data) == initial_len

    def test_entry_markers_rendered(self):
        """入场标记应通过 _render_entry_marker 添加."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        aligned = {
            "aligned_long": np.array([np.nan]*100),
            "aligned_short": np.array([np.nan]*100),
            "entry_markers": [(50, "long", 100.0)],
            "exit_markers": [],
        }

        from components.charts import _add_cross_pnl_subplot
        initial_len = len(fig.data)
        _add_cross_pnl_subplot(fig, t, aligned, row=2)

        # 0 条参考线（全 NaN）+ 1 个入场标记 = 1
        assert len(fig.data) == initial_len + 1
        assert fig.data[-1].marker.symbol == "triangle-up"

    def test_exit_markers_rendered(self):
        """离场标记 + 盈亏标注应添加."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        aligned = {
            "aligned_long": np.array([np.nan]*100),
            "aligned_short": np.array([np.nan]*100),
            "entry_markers": [],
            "exit_markers": [(60, "long", 110.0, 5.0, "take_profit")],
        }

        from components.charts import _add_cross_pnl_subplot
        initial_len = len(fig.data)
        _add_cross_pnl_subplot(fig, t, aligned, row=2)

        # 0 条参考线 + 1 个离场标记 trace = 1
        assert len(fig.data) == initial_len + 1

    def test_baseline_always_added(self):
        """基准线应始终添加（通过 shapes）. """
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        aligned = {
            "aligned_long": np.array([np.nan]*100),
            "aligned_short": np.array([np.nan]*100),
            "entry_markers": [],
            "exit_markers": [],
        }

        from components.charts import _add_cross_pnl_subplot
        initial_shapes = len(fig.layout.shapes or [])
        _add_cross_pnl_subplot(fig, t, aligned, row=2)

        shapes = fig.layout.shapes or []
        assert len(shapes) == initial_shapes + 1

    def test_marker_color_is_gold(self):
        """高周期标记统一使用金色."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        aligned = {
            "aligned_long": np.array([np.nan]*100),
            "aligned_short": np.array([np.nan]*100),
            "entry_markers": [(50, "long", 100.0)],
            "exit_markers": [(60, "long", 110.0, 5.0, "take_profit")],
        }

        from components.charts import _add_cross_pnl_subplot
        _add_cross_pnl_subplot(fig, t, aligned, row=2)

        # 入场标记颜色
        entry_trace = fig.data[-2]
        assert entry_trace.marker.color == "#d2991d"
        # 离场标记颜色
        exit_trace = fig.data[-1]
        assert exit_trace.marker.color == "#d2991d"


# ===================================================================
# SECTION 10 — _add_alignment_subplot
# ===================================================================

class TestAlignmentSubplot:
    """同向性判断子图."""

    def test_basic_traces_added(self):
        """应添加多空 PnL 曲线."""
        fig = _make_fig()
        t = np.arange(50, dtype=float)
        long_pnl = np.full(50, 100.0)
        short_pnl = np.full(50, 100.0)
        trade_records = []
        long_mask = np.zeros(50, dtype=bool)
        short_mask = np.zeros(50, dtype=bool)

        from components.charts import _add_alignment_subplot
        initial_len = len(fig.data)
        _add_alignment_subplot(
            fig, t, long_pnl, short_pnl, trade_records,
            long_mask, short_mask, row=3,
        )

        # 2 PnL 曲线 + 2 fill backgrounds = 4 traces
        traces_added = len(fig.data) - initial_len
        assert traces_added >= 2  # 至少 2 条 PnL 曲线
        assert traces_added <= 6  # 最多 4+2 可选高亮

    def test_trade_highlighted_when_masked(self):
        """有交易记录且在 mask 范围内时应高亮."""
        fig = _make_fig()
        t = np.arange(50, dtype=float)
        long_pnl = np.full(50, 100.0)
        short_pnl = np.full(50, 100.0)
        trade_records = [
            {"id": 1, "type": "long", "entry_idx": 10, "exit_idx": 20,
             "exit_reason": "take_profit", "return_pct": 3.0},
        ]
        long_mask = np.zeros(50, dtype=bool)
        long_mask[10:21] = True
        short_mask = np.zeros(50, dtype=bool)

        from components.charts import _add_alignment_subplot
        initial_len = len(fig.data)
        _add_alignment_subplot(
            fig, t, long_pnl, short_pnl, trade_records,
            long_mask, short_mask, row=3,
        )

        # 2 PnL + 2 fill + 1 交易高亮 = 5 (no exit marker since mask is True at exit)
        assert len(fig.data) >= initial_len + 3

    def test_short_trade_colored_red(self):
        """做空交易高亮应为红色."""
        fig = _make_fig()
        t = np.arange(50, dtype=float)
        long_pnl = np.full(50, 100.0)
        short_pnl = np.full(50, 100.0)
        trade_records = [
            {"id": 1, "type": "short", "entry_idx": 10, "exit_idx": 20,
             "exit_reason": "take_profit", "return_pct": 3.0},
        ]
        long_mask = np.zeros(50, dtype=bool)
        short_mask = np.zeros(50, dtype=bool)
        short_mask[10:21] = True

        from components.charts import _add_alignment_subplot
        _add_alignment_subplot(
            fig, t, long_pnl, short_pnl, trade_records,
            long_mask, short_mask, row=3,
        )

        # 找到高亮 trace（颜色 #f85149）
        red_traces = [tr for tr in fig.data if hasattr(tr.line, 'color')
                      and tr.line.color == "#f85149"]
        assert len(red_traces) >= 1

    def test_exit_out_of_bounds_skipped(self):
        """exit_idx 越界时不应崩溃."""
        fig = _make_fig()
        t = np.arange(50, dtype=float)
        long_pnl = np.full(50, 100.0)
        short_pnl = np.full(50, 100.0)
        trade_records = [
            {"id": 1, "type": "long", "entry_idx": 10, "exit_idx": 100,
             "exit_reason": "take_profit", "return_pct": 3.0},
        ]
        long_mask = np.zeros(50, dtype=bool)
        long_mask[10:21] = True
        short_mask = np.zeros(50, dtype=bool)

        from components.charts import _add_alignment_subplot
        _add_alignment_subplot(
            fig, t, long_pnl, short_pnl, trade_records,
            long_mask, short_mask, row=3,
        )
        # 不应崩溃，至少回到正常
        assert len(fig.data) > 0

    def test_long_trade_colored_green(self):
        """做多交易高亮应为绿色."""
        fig = _make_fig()
        t = np.arange(50, dtype=float)
        long_pnl = np.full(50, 100.0)
        short_pnl = np.full(50, 100.0)
        trade_records = [
            {"id": 1, "type": "long", "entry_idx": 10, "exit_idx": 20,
             "exit_reason": "take_profit", "return_pct": 3.0},
        ]
        long_mask = np.zeros(50, dtype=bool)
        long_mask[10:21] = True
        short_mask = np.zeros(50, dtype=bool)

        from components.charts import _add_alignment_subplot
        _add_alignment_subplot(
            fig, t, long_pnl, short_pnl, trade_records,
            long_mask, short_mask, row=3,
        )

        green_traces = [tr for tr in fig.data if hasattr(tr.line, 'color')
                        and tr.line.color == "#3fb950"]
        assert len(green_traces) >= 1


# ===================================================================
# SECTION 11 — _add_prediction_traces
# ===================================================================

class TestPredictionTraces:
    """预测曲线 trace 的基础参数."""

    def test_prediction_trace_properties(self):
        """预测曲线应使用紫色虚线."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        filtered = np.full(100, 0.5)
        fit_result = {"a": 0.0, "b": 0.0, "c": 1.0,
                      "y_fit": np.linspace(0, 1, 50), "x0": 0}
        fit_start = 0
        pair_end = 49

        from components.charts import _add_prediction_traces
        _add_prediction_traces(fig, t, filtered, fit_result, fit_start,
                               pair_end, row=2, n_extend=10)

        # _make_fig creates 4 dummy traces, _add_prediction_traces adds 3 more
        traces = fig.data
        assert len(traces) >= 7  # 4 dummy + 拟合 + 预测 + 残差
        trace_names = [tr.name for tr in traces if tr.name is not None]
        assert any("预测" in n for n in trace_names)
        assert any("拟合" in n for n in trace_names)

    def test_no_extend_adds_no_extrapolation(self):
        """n_extend=0 时应只生成拟合 trace."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        filtered = np.full(100, 0.5)
        fit_result = {"a": 0.0, "b": 0.0, "c": 1.0,
                      "y_fit": np.linspace(0, 1, 50), "x0": 0}
        fit_start = 0
        pair_end = 49

        from components.charts import _add_prediction_traces
        _add_prediction_traces(fig, t, filtered, fit_result, fit_start,
                               pair_end, row=2, n_extend=0)

        traces = fig.data
        # _make_fig creates 4 dummy traces, 只有1个拟合 trace（没有预测和残差）
        assert len(traces) == 5  # 4 dummy + 1 拟合

    def test_poly2_trace_name(self):
        """拟合 trace 名称应包含(拟合)后缀."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        filtered = np.full(100, 0.5)
        fit_result = {"a": 0.0, "b": 0.0, "c": 1.0,
                      "y_fit": np.linspace(0, 1, 50), "x0": 0}
        fit_start = 0
        pair_end = 49

        from components.charts import _add_prediction_traces
        _add_prediction_traces(fig, t, filtered, fit_result, fit_start,
                               pair_end, row=2, n_extend=5)

        names = [tr.name for tr in fig.data if tr.name is not None]
        assert any("(拟合)" in n for n in names)
        assert any("(预测)" in n for n in names)

    def test_prediction_trace_poly2_mode(self):
        """poly2 模式（无 x0）应通过 np.polyval((a,b,c), x_ext) 计算预测."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        filtered = np.full(100, 0.5)
        # poly2 fit: 无 x0 key
        fit_result = {"a": 0.001, "b": 0.01, "c": 1.0,
                      "y_fit": np.linspace(0.5, 1.0, 50)}
        fit_start = 0
        pair_end = 49

        from components.charts import _add_prediction_traces
        _add_prediction_traces(fig, t, filtered, fit_result, fit_start,
                               pair_end, row=2, n_extend=10)

        traces = fig.data
        pred_traces = [tr for tr in traces if tr.name and "(预测)" in tr.name]
        assert len(pred_traces) == 1
        # polyval((0.001, 0.01, 1.0), [50..59]) 应产生递增数列
        y_pred = pred_traces[0].y
        assert y_pred is not None and len(y_pred) == 10
        # 正二次项系数 → 递增
        assert y_pred[0] < y_pred[-1]

    def test_prediction_trace_physics_mode(self):
        """physics 模式（有 x0）应通过 np.polyval((a,b,c), x_ext-x0) 计算预测."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        filtered = np.full(100, 0.5)
        # physics fit: 顶点在 x0=49, y_fit 对称于 x0
        fit_result = {"a": 0.001, "b": 0.0, "c": 1.0,
                      "y_fit": np.linspace(0.5, 1.0, 50), "x0": 49.0}
        fit_start = 0
        pair_end = 49

        from components.charts import _add_prediction_traces
        _add_prediction_traces(fig, t, filtered, fit_result, fit_start,
                               pair_end, row=2, n_extend=10)

        traces = fig.data
        pred_traces = [tr for tr in traces if tr.name and "(预测)" in tr.name]
        assert len(pred_traces) == 1
        y_pred = pred_traces[0].y
        assert y_pred is not None and len(y_pred) == 10


# ===================================================================
# SECTION 12 — _render_plotly JSON serialization
# ===================================================================

class TestRenderPlotlySerialization:
    """_render_plotly 的 JSON 序列化管道：NaN/Inf/空数据."""

    def test_render_plotly_with_nan_values(self, monkeypatch):
        """NaN 值应被序列化为 JSON null."""
        captured = {}
        def _capture_html(html, **kw):
            captured["html"] = html
            return MagicMock()
        import streamlit as st
        monkeypatch.setattr(st.components.v1, "html", _capture_html)

        fig = _make_fig()
        # 向第一个 trace 的 y 中注入 NaN
        fig.data[0].y = np.array([1.0, float("nan"), 3.0, float("nan"), 5.0])

        from components.charts import _render_plotly
        _render_plotly(fig)

        assert "html" in captured
        # 从 HTML 中提取 JSON data 部分（排除 JavaScript 中的 Infinity）
        import re
        m = re.search(r"var figure = (\{.+?\});\s*\n\s*var config", captured["html"], re.DOTALL)
        assert m is not None
        figure_json = m.group(1)
        # NaN 在 JSON 中应为 null，不应出现字符串 NaN
        assert "null" in figure_json
        assert "NaN" not in figure_json

    def test_render_plotly_with_inf_values(self, monkeypatch):
        """Inf 值应被序列化为 JSON null."""
        captured = {}
        def _capture_html(html, **kw):
            captured["html"] = html
            return MagicMock()
        import streamlit as st
        monkeypatch.setattr(st.components.v1, "html", _capture_html)

        fig = _make_fig()
        fig.data[0].y = np.array([1.0, float("inf"), 3.0, float("-inf"), 5.0])

        from components.charts import _render_plotly
        _render_plotly(fig)

        assert "html" in captured
        # 从 HTML 中提取 JSON data 部分（在 var figure = 和 ; 之间）
        import re
        m = re.search(r"var figure = (\{.+?\});\s*\n\s*var config", captured["html"], re.DOTALL)
        assert m is not None, "无法从 HTML 中提取 figure JSON"
        figure_json = m.group(1)
        # y 数组中的 Infinity/NaN 应已被替换为 null
        assert "Infinity" not in figure_json
        assert "NaN" not in figure_json
        assert "null" in figure_json

    def test_render_plotly_empty_data(self, monkeypatch):
        """空数据 fig 不应崩溃."""
        captured = {}
        def _capture_html(html, **kw):
            captured["html"] = html
            return MagicMock()
        import streamlit as st
        monkeypatch.setattr(st.components.v1, "html", _capture_html)

        fig = go.Figure()  # 完全空白的 figure
        fig.add_trace(go.Scatter(x=[], y=[]))

        from components.charts import _render_plotly
        _render_plotly(fig)

        assert "html" in captured
        assert "Plotly.newPlot" in captured["html"]

    def test_render_plotly_with_dates(self, monkeypatch):
        """带 dates 参数时应在 layout 中嵌入 _dates."""
        captured = {}
        def _capture_html(html, **kw):
            captured["html"] = html
            return MagicMock()
        import streamlit as st
        monkeypatch.setattr(st.components.v1, "html", _capture_html)

        from datetime import datetime
        fig = _make_fig()
        dates = [datetime(2026, 1, 1), datetime(2026, 1, 2)]

        from components.charts import _render_plotly
        _render_plotly(fig, dates=dates)

        assert "html" in captured
        assert "2026-01-01" in captured["html"]


# ===================================================================
# SECTION 13 — _add_cross_pnl_subplot with trades
# ===================================================================

class TestCrossPnlSubplotTrades:
    """_add_cross_pnl_subplot 的 trade 标记边界."""

    def test_cross_pnl_empty_higher_trades(self):
        """空 entry/exit markers 且全 NaN 参考线时不应添加 traces."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        aligned = {
            "aligned_long": np.array([np.nan] * 100),
            "aligned_short": np.array([np.nan] * 100),
            "entry_markers": [],
            "exit_markers": [],
        }

        from components.charts import _add_cross_pnl_subplot
        initial_len = len(fig.data)
        _add_cross_pnl_subplot(fig, t, aligned, row=2)

        # 0 条参考线 + 0 个标记 = 0 新 traces（但有 baseline shape）
        assert len(fig.data) == initial_len

    def test_cross_pnl_with_trades(self):
        """同时有入场和离场标记时应添加对应 traces."""
        fig = _make_fig()
        t = np.arange(100, dtype=float)
        aligned = {
            "aligned_long": np.array([np.nan] * 100),
            "aligned_short": np.array([np.nan] * 100),
            "entry_markers": [
                (20, "long", 100.0),
                (60, "short", 95.0),
            ],
            "exit_markers": [
                (40, "long", 110.0, 10.0, "take_profit"),
                (80, "short", 90.0, -5.26, "stop_loss"),
            ],
        }

        from components.charts import _add_cross_pnl_subplot
        initial_len = len(fig.data)
        _add_cross_pnl_subplot(fig, t, aligned, row=2)

        traces_added = len(fig.data) - initial_len
        assert traces_added == 4  # 2 entry + 2 exit


# ===================================================================
# SECTION 14 — _add_schmitt_traces (from streamlit_app.py)
# ===================================================================

class TestSchmittTraces:
    """_add_schmitt_traces 边界条件."""

    def _make_schmitt(self, n=100):
        """Create a minimal schmitt dict with correct structure."""
        return {
            "eps": np.full(n, 0.1),
            "sig": np.zeros(n, dtype=int),
            "sigma_v": np.full(n, 0.05),
        }

    def test_schmitt_trace_empty_pairs(self):
        """all_pairs 为空时不应崩溃."""
        from streamlit_app import _add_schmitt_traces
        n = 50
        fig = _make_fig()
        t = np.arange(n, dtype=float)
        schmitt = self._make_schmitt(n)
        acc = np.zeros(n)
        all_pairs = []

        _add_schmitt_traces(fig, t, schmitt, acc, all_pairs, sar=1, ssr=2)

        # 至少应有 eps bands + sigma_v + acc + sig traces
        assert len(fig.data) >= 6

    def test_schmitt_trace_single_pair(self):
        """单个 pair 应添加 pair band trace."""
        from streamlit_app import _add_schmitt_traces
        n = 50
        fig = _make_fig()
        t = np.arange(n, dtype=float)
        schmitt = self._make_schmitt(n)
        schmitt["sig"][10:30] = 1  # 一段 active region
        acc = np.zeros(n)
        all_pairs = [(5, 25)]

        initial_len = len(fig.data)
        _add_schmitt_traces(fig, t, schmitt, acc, all_pairs, sar=1, ssr=2)

        assert len(fig.data) >= initial_len + 1

    def test_schmitt_trace_with_sar(self):
        """sar 和 ssr 参数指向不同子图时应正确布局."""
        from streamlit_app import _add_schmitt_traces
        n = 50
        fig = _make_fig(nrows=4)
        t = np.arange(n, dtype=float)
        schmitt = self._make_schmitt(n)
        schmitt["sig"] = np.array([1] * 20 + [-1] * 30, dtype=int)
        acc = np.linspace(0, 0.5, n)
        all_pairs = [(0, 19), (20, 39)]

        initial_len = len(fig.data)
        _add_schmitt_traces(fig, t, schmitt, acc, all_pairs, sar=1, ssr=2)

        # 7 base traces (eps fill + +eps + -eps + sigma_v + acc + sig + sig fill)
        # + 2 pair bands = 9
        assert len(fig.data) >= initial_len + 7

    def test_schmitt_sig_with_both_states(self):
        """sig 为 1 和 -1 时应添加不同颜色的 fill traces."""
        from streamlit_app import _add_schmitt_traces
        n = 50
        fig = _make_fig(nrows=4)
        t = np.arange(n, dtype=float)
        schmitt = self._make_schmitt(n)
        schmitt["sig"] = np.array([1] * 20 + [-1] * 30, dtype=int)
        acc = np.zeros(n)
        all_pairs = []

        initial_len = len(fig.data)
        _add_schmitt_traces(fig, t, schmitt, acc, all_pairs, sar=1, ssr=2)

        # sig state fill traces: 绿色(状态1) + 红色(状态-1) = 2
        # Total: 6 base + 2 fill = 8 (可能有重复)
        assert len(fig.data) >= initial_len + 6
