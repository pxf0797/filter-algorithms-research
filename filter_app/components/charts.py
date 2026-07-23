"""
图表渲染模块 — Plotly图表构建、HTML渲染、跨周期PnL子图

依赖: filter_engine (纯计算)、Streamlit (仅_st_import)
"""

import json
import uuid
from pathlib import Path
import numpy as np
import streamlit as st
import plotly.graph_objects as go

from services.filter_engine import _compute_holding_masks


# ---------------------------------------------------------------------------
# Plotly cross-subplot crosshair helper
# ---------------------------------------------------------------------------
def _render_plotly(fig, height=750, dates=None) -> None:
    """Render Plotly chart with cross-subplot crosshair (no value tooltip)."""
    from plotly.utils import PlotlyJSONEncoder

    # Serialize manually instead of fig.to_json():
    #  1. _dates is injected into layout (not supported by fig.to_json output)
    #  2. fig.to_json() bdata-encodes x-arrays as base64 which changes the
    #     JS-side array type and breaks the _nearestIdx binary search
    fig_dict = {"data": [], "layout": fig.layout.to_plotly_json()}

    if dates is not None:
        date_strs = [d.strftime("%Y-%m-%d %H:%M") if hasattr(d, 'strftime') else str(d)
                     for d in dates]
        fig_dict["layout"]["_dates"] = date_strs

    for trace in fig.data:
        tr = trace.to_plotly_json()
        # Convert numpy x to plain list so JS crosshair gets a regular Array
        if isinstance(tr.get("x"), np.ndarray):
            tr["x"] = tr["x"].tolist()
        fig_dict["data"].append(tr)

    figure_json = json.dumps(fig_dict, cls=PlotlyJSONEncoder)
    div_id = f"plot-{uuid.uuid4().hex[:8]}"

    # Load crosshair JS from file (extracted from inline template)
    _js_path = Path(__file__).parent.parent / "static" / "charts.js"
    _js_code = _js_path.read_text(encoding="utf-8")
    _js_code = _js_code.replace("__DIV_ID__", div_id)
    _js_code = _js_code.replace("__FIGURE_JSON__", figure_json)

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"
    onerror="this.onerror=null;this.src='https://cdnjs.cloudflare.com/ajax/libs/plotly.js/2.35.2/plotly.min.js';window._plotlyCdnFailed=true"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ width: 100%; height: 100%; overflow: hidden; }}
#{div_id} {{ width: 100%; height: 100%; }}
g.hovertext {{ visibility: hidden !important; }}
.spikeline {{ visibility: hidden !important; }}
#date-tip-{div_id} {{
    display: none;
    position: fixed;
    z-index: 9999;
    background: rgba(30,30,44,0.94);
    color: #c0c0c0;
    padding: 4px 8px;
    border-radius: 4px;
    font-family: monospace;
    font-size: 11px;
    pointer-events: none;
    white-space: nowrap;
}}
</style>
</head>
<body>
<div id="{div_id}"></div>
<div id="date-tip-{div_id}"></div>
<div id="plotly-fallback-{div_id}" style="display:none;padding:2rem;text-align:center;color:#888">
  <p>Plotly.js 加载失败</p>
  <p>请检查网络连接或联系管理员</p>
</div>
<script>
{_js_code}
</script>
</body>
</html>"""

    return st.components.v1.html(html, height=height)


# ---------------------------------------------------------------------------
# Prediction traces on chart
# ---------------------------------------------------------------------------
def _add_prediction_traces(t, filtered, fit_result, fit_start, pair_end, row,
                          n_extend=10, show_legend=True):
    """在 price 子图上添加预测曲线 + 残差子图上的拟合残差。"""
    name = "预测曲线"
    fit_color = "#f0a040"   # 橙色
    pred_color = "#a371f7"  # 紫色
    a, b, c = fit_result["a"], fit_result["b"], fit_result["c"]

    # ★ row=1 的 axis id 是 "x"/"y"（无数字后缀），不是 "x1"/"y1"
    _ax = "x" if row == 1 else f"x{row}"
    _ay = "y" if row == 1 else f"y{row}"
    # 拟合段 — 橙色实线
    x_fit = t[fit_start:pair_end + 1]; y_fit = fit_result["y_fit"]
    traces = [dict(type="scattergl", x=x_fit, y=y_fit,
        mode="lines", name=f"{name}(拟合)",
        line=dict(color=fit_color, width=2),
        legendgroup=name, showlegend=show_legend,
        xaxis=_ax, yaxis=_ay)]

    # 前向延伸 — 紫色虚线
    y_ext = None; _ax_r1 = f"x{row+1}"; _ay_r1 = f"y{row+1}"
    if n_extend > 0:
        x_ext = np.arange(pair_end, pair_end + n_extend)
        x0 = fit_result.get("x0", None)
        y_ext = np.polyval((a, b, c), x_ext - x0) if x0 is not None else np.polyval((a, b, c), x_ext)
        traces.append(dict(type="scattergl", x=x_ext, y=y_ext,
            mode="lines", name=f"{name}(预测)",
            line=dict(color=pred_color, width=2, dash="dash"),
            legendgroup=name, showlegend=show_legend,
            xaxis=_ax, yaxis=_ay))

    # 残差子图 — 前向预测段与最后已知滤波价格的残差
    if y_ext is not None and n_extend > 0:
        baseline = filtered[pair_end]; residual = y_ext - baseline
        upward = y_ext[-1] > y_ext[0]
        res_color = "#f85149" if upward else "#3fb950"
        traces.append(dict(type="scattergl", x=x_ext, y=residual,
            mode="lines", name=f"{name}(残差)",
            line=dict(color=res_color, width=1.5, dash="dot"),
            legendgroup=name, showlegend=show_legend,
            xaxis=_ax_r1, yaxis=_ay_r1))
    return traces


# ---------------------------------------------------------------------------
# Shared PnL rendering helpers
# ---------------------------------------------------------------------------
def _render_entry_marker(t, bar_idx, pnl_val, row, col=1,
                         color="#d2991d", size=9, hovertext=""):
    """统一的入场标记（三角形）。"""
    if not (0 <= bar_idx < len(t)):
        return None
    return dict(type="scattergl", x=[t[bar_idx]], y=[pnl_val],
        mode="markers",
        marker=dict(color=color, symbol="triangle-up", size=size,
                    line=dict(width=1, color="rgba(0,0,0,0.3)")),
        showlegend=False, hovertext=hovertext, hoverinfo="text",
        xaxis=f"x{row}", yaxis=f"y{row}")


def _render_exit_marker_with_label(t, bar_idx, pnl_val, row, col=1,
                                   color="#d2991d", trade_type="long",
                                   exit_reason="", ret_pct=0.0,
                                   hovertext="") -> None:
    """统一的离场标记（止损=x / 止盈=circle）+ 盈亏标注。"""
    if not (0 <= bar_idx < len(t)):
        return None, None
    is_sl = exit_reason == "stop_loss"
    sym = "x" if is_sl else "circle"; ec = "#f85149" if is_sl else "#3fb950"
    trace = dict(type="scattergl", x=[t[bar_idx]], y=[pnl_val],
        mode="markers",
        marker=dict(color=color, symbol=sym, size=9, line=dict(width=1, color=ec)),
        showlegend=False, hovertext=hovertext, hoverinfo="text",
        xaxis=f"x{row}", yaxis=f"y{row}")
    label_color = "#f85149" if is_sl else "#3fb950"
    arrow = "↑" if trade_type == "long" else "↓"
    annotation = dict(x=t[bar_idx], y=pnl_val,
        text=f"{arrow}{ret_pct:+.1f}%",
        showarrow=False, font=dict(size=8, color=label_color),
        yshift=12, xref=f"x{row}", yref=f"y{row}")
    return trace, annotation


def _render_pnl_curves(t, long_filtered, short_filtered, row, col=1,
                       long_color="#3fb950", short_color="#f85149",
                       long_name="做多PnL", short_name="做空PnL",
                       show_legend=False):
    """Return 2 trace dicts for PnL baseline curves."""
    _ax = f"x{row}"; _ay = f"y{row}"
    return [dict(type="scattergl", x=t, y=long_filtered, mode="lines", name=long_name,
        line=dict(color=long_color, width=1.5, dash="solid"),
        showlegend=show_legend, xaxis=_ax, yaxis=_ay),
        dict(type="scattergl", x=t, y=short_filtered, mode="lines", name=short_name,
        line=dict(color=short_color, width=1.5, dash="solid"),
        showlegend=show_legend, xaxis=_ax, yaxis=_ay)]


def _render_baseline(row, col=1, y=100, opacity=0.5):
    """Return hline shape dict for PnL baseline."""
    return dict(type="line", x0=0, x1=1, xref="paper", y0=y, y1=y,
        yref=f"y{row}", line=dict(color="gray", dash="dash"), opacity=opacity)


def _render_fill_background(t, y_values, row, col=1,
                            color="rgba(63,185,80,0.04)", baseline=100):
    """Return fill background trace dict."""
    y_max = max(float(np.nanmax(y_values)), baseline) * 1.02
    return dict(type="scattergl",
        x=[t[0], t[-1], t[-1], t[0]], y=[baseline, baseline, y_max, y_max],
        fill="toself", fillcolor=color, mode="lines", line=dict(width=0),
        showlegend=False, hoverinfo="skip", xaxis=f"x{row}", yaxis=f"y{row}")


# ---------------------------------------------------------------------------
# Cross-period PnL reference subplot
# ---------------------------------------------------------------------------
def _contiguous_runs(mask):
    """返回布尔数组中连续 True 段的 (start, end) 闭区间列表。"""
    runs, s = [], None
    for i, v in enumerate(mask):
        if v and s is None:
            s = i
        elif (not v) and s is not None:
            runs.append((s, i - 1)); s = None
    if s is not None:
        runs.append((s, len(mask) - 1))
    return runs


def _draw_holding_bands(t, long_mask, short_mask, row):
    """Return (shapes, yaxes) for holding-state color block subplot."""
    n = len(t); hw = (t[1] - t[0]) / 2.0 if n > 1 else 0.5
    shapes = []
    for a, b in _contiguous_runs(long_mask):
        shapes.append(dict(type="rect", x0=t[a]-hw, x1=t[b]+hw, y0=0.55, y1=0.95,
            fillcolor="#3fb950", line_width=0, xref=f"x{row}", yref=f"y{row}"))
    for a, b in _contiguous_runs(short_mask):
        shapes.append(dict(type="rect", x0=t[a]-hw, x1=t[b]+hw, y0=0.05, y1=0.45,
            fillcolor="#f85149", line_width=0, xref=f"x{row}", yref=f"y{row}"))
    yaxes = {f"yaxis{row}": {"range": [0,1], "tickvals": [0.25,0.75],
        "ticktext": ["做空","做多"], "showgrid": False}}
    return shapes, yaxes


def _add_cross_pnl_subplot(t, aligned, row, higher_tf=""):
    """Return (shapes, yaxes) for higher-period holding-state subplot."""
    long_mask, short_mask = _compute_holding_masks(
        len(t), aligned["entry_markers"], aligned["exit_markers"])
    return _draw_holding_bands(t, long_mask, short_mask, row)


def _add_alignment_subplot(t, long_pnl, short_pnl, trade_records,
                           long_mask, short_mask, row):
    """Return (traces, shapes, annotations, yaxes) for alignment subplot."""
    n = len(t)
    long_filtered = np.full(n, 100.0)
    for i in range(1, n):
        if long_mask[i] and long_pnl[i - 1] != 0:
            long_filtered[i] = long_filtered[i - 1] * (long_pnl[i] / long_pnl[i - 1])
        else: long_filtered[i] = long_filtered[i - 1]
    short_filtered = np.full(n, 100.0)
    for i in range(1, n):
        if short_mask[i] and short_pnl[i - 1] != 0:
            short_filtered[i] = short_filtered[i - 1] * (short_pnl[i] / short_pnl[i - 1])
        else: short_filtered[i] = short_filtered[i - 1]

    traces = _render_pnl_curves(t, long_filtered, short_filtered, row)
    annotations = []
    for trade in trade_records:
        is_long = trade["type"] == "long"
        mask = long_mask if is_long else short_mask
        entry_i = trade["entry_idx"]; exit_i = trade["exit_idx"]
        if entry_i >= n or exit_i >= n: continue
        seg_range = slice(entry_i, exit_i + 1)
        if not mask[seg_range].any(): continue
        curve = long_filtered if is_long else short_filtered
        seg_t = t[seg_range]; seg_pnl = curve[seg_range]
        color = "#3fb950" if is_long else "#f85149"
        traces.append(dict(type="scattergl", x=seg_t, y=seg_pnl, mode="lines",
            name=f"{'多' if is_long else '空'}#{trade['id']}",
            line=dict(color=color, width=3), showlegend=False,
            xaxis=f"x{row}", yaxis=f"y{row}"))
        if mask[entry_i]:
            tr = _render_entry_marker(seg_t, 0, seg_pnl[0], row, color=color, size=8)
            if tr: traces.append(tr)
        if trade["exit_reason"] in ("stop_loss", "take_profit") and mask[exit_i]:
            tr, ann = _render_exit_marker_with_label(seg_t, -1, seg_pnl[-1], row,
                color=color, trade_type=trade["type"],
                exit_reason=trade["exit_reason"], ret_pct=trade["return_pct"])
            if tr: traces.append(tr)
            if ann: annotations.append(ann)

    traces.append(_render_fill_background(t, long_filtered, row, color="rgba(63,185,80,0.04)", baseline=100))
    traces.append(_render_fill_background(t, short_filtered, row, color="rgba(248,81,73,0.04)", baseline=100))
    shapes = [_render_baseline(row, opacity=0.5)]
    yaxes = {f"yaxis{row}": {"title_text": "同向(%)", "ticksuffix": "%"}}
    return traces, shapes, annotations, yaxes


# ---------------------------------------------------------------------------
# BS 仓位操作标识 — K 线主图上的买卖标记
# ---------------------------------------------------------------------------

def _make_bs_annotation(x, y, text, color):
    """构建单个 B/S 标记的 Plotly annotation dict。

    Parameters
    ----------
    x : float — bar 索引
    y : float — 价格位置
    text : str — "B" 或 "S"
    color : str — "green" 或 "red"
    """
    bg = "#2ea043" if color == "green" else "#f85149"
    return dict(
        x=x, y=y, text=f"<b>{text}</b>", showarrow=False,
        font=dict(color="#ffffff", size=9, family="Arial"),
        bgcolor=bg, borderpad=2,
        xref="x", yref="y",
    )


def _add_bs_markers(t, ohlc, bs_markers):
    """为 K 线主图生成 B/S 标记的 Plotly annotation 列表。

    入场标记放在 K 线 low 下方，出场标记放在 high 上方。

    Parameters
    ----------
    t : np.ndarray — bar 索引
    ohlc : pd.DataFrame — 含 Open/High/Low/Close 列
    bs_markers : dict or None — compute_bs_markers 的返回值

    Returns
    -------
    list[dict] — Plotly annotation dicts
    """
    annotations = []
    if bs_markers is None:
        return annotations

    n = len(t)
    high_vals = ohlc["High"].values.ravel()
    low_vals = ohlc["Low"].values.ravel()

    # 入场标记 — K 线下方
    for item in bs_markers.get("entry_markers", []):
        bar_idx = item[0]
        label, color = item[1], item[2]
        if bar_idx < n:
            y_pos = low_vals[bar_idx] * 0.997
            annotations.append(_make_bs_annotation(t[bar_idx], y_pos, label, color))

    # 出场标记 — K 线上方
    for item in bs_markers.get("exit_markers", []):
        bar_idx = item[0]
        label, color = item[1], item[2]
        if bar_idx < n:
            y_pos = high_vals[bar_idx] * 1.003
            annotations.append(_make_bs_annotation(t[bar_idx], y_pos, label, color))

    return annotations
