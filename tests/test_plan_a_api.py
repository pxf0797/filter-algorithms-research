"""方案A 新API回归：_add_* 返回 data dict 而非修改 fig + go.Figure(data=,layout=) 构造合法性"""

import numpy as np
import pandas as pd
from plotly.subplots import make_subplots
from browse.app import (
    _add_main_price_traces, _add_residual_traces, _add_schmitt_traces,
    _add_pnl_traces, _add_feedback_subplot,
)
from browse.charts import (
    _add_prediction_traces, _add_cross_pnl_subplot, _add_alignment_subplot,
    _render_entry_marker, _render_exit_marker_with_label,
    _render_pnl_curves, _render_baseline, _draw_holding_bands,
)


def _ohlc(n):
    return pd.DataFrame({"Open": np.linspace(100,110,n), "High": np.linspace(101,111,n),
        "Low": np.linspace(99,109,n), "Close": np.linspace(100,110,n)})


# ==================== 1. 基础 trace-returning 函数 ====================

class TestMainPriceAndResidualReturnDicts:
    def test_main_price_returns_list_of_dicts(self):
        n=20; t=np.arange(n,dtype=float); noisy=np.linspace(100,110,n)
        traces = _add_main_price_traces(t, noisy, _ohlc(n), noisy+0.5, None,
                                        {"fc":"#00d4aa","_dual":False,"fc2":"#ff6b6b"}, mr=1)
        assert isinstance(traces, list) and len(traces) >= 2
        assert all(isinstance(d, dict) for d in traces)
        assert traces[0]["type"] == "candlestick"
        assert traces[1]["type"] == "scattergl"

    def test_residual_returns_acc_traces_shapes(self):
        n=30; t=np.arange(n,dtype=float); noisy=np.sin(t/3)+100
        acc, traces, shapes = _add_residual_traces(t, noisy+0.2, noisy, None,
                                                    {"fc":"#00d4aa"}, rr=2, vr=3)
        assert len(acc) == n                     # returned acceleration
        assert len(traces) == 2                  # 残差 + v
        assert len(shapes) == 2                  # 2 hlines
        assert all(isinstance(d, dict) for d in traces + shapes)

    def test_short_t_residual_returns_empty(self):
        """P0-3: _add_residual_traces now always returns (acc, traces, shapes) tuple."""
        acc, traces, shapes = _add_residual_traces(np.array([0.0]), np.array([100.0]),
            np.array([100.0]), None, {"fc":"#00d4aa"}, 2, 3)
        assert isinstance(acc, np.ndarray) and len(acc) == 0
        assert traces == []
        assert shapes == []


# ==================== 2. schmitt / pnl / feedback ====================

class TestSchmittReturnTuple:
    def test_schmitt_returns_traces_shapes(self):
        n=60; t=np.arange(n,dtype=float)
        sig = np.zeros(n, int); sig[20:30]=1; sig[45:55]=-1
        acc = np.random.randn(n)*0.1
        schmitt = {"eps": np.full(n,0.2), "sig": sig, "sigma_v": np.full(n,0.15)}
        traces, shapes = _add_schmitt_traces(t, schmitt, acc, [(20,45)], sar=3, ssr=4)
        assert isinstance(traces, list) and len(traces) >= 5  # ±ε/+/−/σ(v)/a/Sig/bands
        assert isinstance(shapes, list) and len(shapes) >= 1  # hline
        assert all(isinstance(d, dict) for d in traces)

    def test_schmitt_empty_pairs(self):
        n=30; t=np.arange(n,dtype=float)
        schmitt = {"eps": np.full(n,0.1), "sig": np.zeros(n,int),
                   "sigma_v": np.full(n,0.1)}
        traces, shapes = _add_schmitt_traces(t, schmitt, np.zeros(n), [], 3, 4)
        assert len(traces) >= 4  # no pair bands, still has base traces


class TestPnlReturnQuadruple:
    def test_pnl_returns_4_tuple(self):
        n=30; t=np.arange(n,dtype=float)
        lp = 100+0.3*t; sp = 100+0.1*t
        trades = [{"id":1,"type":"long","entry_idx":5,"exit_idx":15,
                   "return_pct":3.2,"exit_reason":"take_profit",
                   "entry_price":100,"exit_price":103}]
        traces, shapes, annotations, yaxes = _add_pnl_traces(t, lp, sp, trades, 1)
        assert isinstance(traces, list) and len(traces) >= 4       # curves + segment + fills
        assert len(shapes) >= 1 and len(annotations) >= 1          # hline + profit annot
        assert "yaxis1" in yaxes

    def test_pnl_annotations_regression(self):
        """回归: 方案A初版 fig.add_annotation 残留导致 NameError。现 annotations 已收集返回。"""
        n=30; t=np.arange(n,dtype=float)
        trades = [{"id":1,"type":"long","entry_idx":5,"exit_idx":15,
                   "return_pct":3.2,"exit_reason":"take_profit",
                   "entry_price":100,"exit_price":103}]
        _, _, annotations, _ = _add_pnl_traces(t, 100+0.3*t, 100+0.1*t, trades, 1)
        assert len(annotations) == 1
        assert "↑" in annotations[0]["text"]


class TestFeedbackSubplotReturnShapesYaxes:
    def test_feedback_returns_shapes_yaxes(self):
        n=30; t=np.arange(n,dtype=float)
        trades = [{"type":"long","entry_idx":3,"exit_idx":12},
                  {"type":"short","entry_idx":15,"exit_idx":25}]
        shapes, yaxes = _add_feedback_subplot(t, trades, row=5)
        assert isinstance(shapes, list) and isinstance(yaxes, dict)
        assert "yaxis5" in yaxes


# ==================== 3. charts.py helper 返回签名（回归bug修复） ====================

class TestEntryExitMarkerSignatures:
    def test_entry_marker_no_fig_param(self):
        """回归: batch脚本漏改 _render_entry_marker 签名, 导致 'missing row' TypeError。"""
        t=np.arange(50,dtype=float)
        tr = _render_entry_marker(t, 25, 100.0, row=2, color="#ff0", size=10)
        assert isinstance(tr, dict)
        assert tr["type"] == "scattergl" and tr["mode"] == "markers"

    def test_entry_marker_invalid_idx_returns_none(self):
        tr = _render_entry_marker(np.arange(50,dtype=float), 500, 100.0, row=1)
        assert tr is None

    def test_exit_marker_no_fig_param(self):
        t=np.arange(50,dtype=float)
        tr, ann = _render_exit_marker_with_label(t, 30, 105.0, row=2,
            exit_reason="stop_loss", ret_pct=-2.5)
        assert isinstance(tr, dict) and tr["marker"]["symbol"] == "x"
        assert isinstance(ann, dict) and "-2.5%" in ann["text"]

    def test_exit_marker_returns_none_none_invalid(self):
        tr, ann = _render_exit_marker_with_label(np.arange(50,dtype=float), -1, 100.0, row=1)
        assert tr is None and ann is None


# ==================== 4. go.Figure(data=dicts, layout=dict) 构造合法性 ====================

class TestGoFigureConstruction:
    def test_figure_from_raw_dicts(self):
        """go.Figure(data=dicts, layout=dict) 不报错，不创建 Python Trace 对象。"""
        import plotly.graph_objects as go
        traces = [dict(type="scattergl", x=[1,2,3], y=[4,5,6], mode="lines",
                       name="test", xaxis="x", yaxis="y")]
        layout = dict(template="plotly_dark", height=400,
                      xaxis=dict(title_text="x"), yaxis=dict(title_text="y"),
                      shapes=[dict(type="line", x0=0,x1=1,xref="paper",y0=0,y1=0,yref="y",
                                   line=dict(color="gray"))],
                      annotations=[dict(x=2,y=5,text="test",xref="x",yref="y",
                                        showarrow=False)])
        fig = go.Figure(data=traces, layout=layout)
        assert fig is not None
        assert len(fig.data) > 0

    def test_all_trace_types_serialize(self):
        """scattergl + candlestick mixed dicts serialize without error."""
        import plotly.graph_objects as go
        traces = [
            dict(type="candlestick", x=[0,1,2], open=[100,101,102],
                 high=[103,104,105], low=[99,98,97], close=[101,102,103],
                 xaxis="x", yaxis="y"),
            dict(type="scattergl", x=[0,1,2], y=[101,102,103],
                 mode="lines", xaxis="x", yaxis="y"),
            dict(type="scattergl", x=[0,1,2], y=[100,100,100],
                 mode="lines", line=dict(width=0), fill="toself",
                 fillcolor="rgba(0,0,0,0.04)", xaxis="x", yaxis="y"),
        ]
        layout = dict(template="plotly_dark")
        fig = go.Figure(data=traces, layout=layout)
        js = fig.to_json()
        assert len(js) > 1000  # successfully serialized


# ==================== 5. holding_bands / cross_pnl / alignment ====================

class TestHoldingBands:
    def test_draw_holding_bands_returns_shapes_yaxes(self):
        n=20; t=np.arange(n,dtype=float)
        long_mask=np.zeros(n,bool); long_mask[3:10]=True
        short_mask=np.zeros(n,bool)
        shapes, yaxes = _draw_holding_bands(t, long_mask, short_mask, row=5)
        assert len(shapes) >= 1
        assert "yaxis5" in yaxes

    def test_cross_pnl_returns_shapes_yaxes(self):
        n=20; t=np.arange(n,dtype=float)
        aligned = {"entry_markers": [(3,"long",100.0)],
                   "exit_markers": [(12,"long",105.0,5.0,"take_profit")]}
        shapes, yaxes = _add_cross_pnl_subplot(t, aligned, row=6)
        assert len(shapes) >= 1
        assert "yaxis6" in yaxes

    def test_alignment_returns_4_tuple(self):
        n=30; t=np.arange(n,dtype=float)
        long_pnl = 100+0.2*t; short_pnl = 100+0.05*t
        long_mask = np.zeros(n,bool); long_mask[5:20]=True  # cover entry 5→exit 15
        short_mask = np.zeros(n,bool); short_mask[18:25]=True
        trades = [{"type":"long","entry_idx":5,"exit_idx":15,"id":1,
                   "return_pct":3.0,"exit_reason":"take_profit",
                   "entry_price":100,"exit_price":103}]
        traces, shapes, annotations, yaxes = _add_alignment_subplot(
            t, long_pnl, short_pnl, trades, long_mask, short_mask, row=7)
        assert isinstance(traces, list) and len(traces) >= 3  # curves + segment + fills
        assert len(shapes) >= 1       # baseline
        assert "yaxis7" in yaxes


class TestPnlCurvesReturnList:
    def test_pnl_curves_returns_2_dicts(self):
        n=20; t=np.arange(n,dtype=float)
        traces = _render_pnl_curves(t, 100+0.3*t, 100+0.1*t, row=3)
        assert len(traces) == 2 and isinstance(traces[0], dict)

    def test_baseline_returns_shape_dict(self):
        sh = _render_baseline(row=3, y=100)
        assert isinstance(sh, dict) and sh["type"] == "line"
