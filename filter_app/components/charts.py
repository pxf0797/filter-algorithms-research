"""
图表渲染模块 — Plotly图表构建、HTML渲染、跨周期PnL子图

依赖: filter_engine (纯计算)、Streamlit (仅_st_import)
"""

import json
import uuid
import numpy as np
import streamlit as st
import plotly.graph_objects as go


# ---------------------------------------------------------------------------
# Plotly cross-subplot crosshair helper
# ---------------------------------------------------------------------------
def _render_plotly(fig, height=750, dates=None):
    """Render Plotly chart with cross-subplot crosshair (no value tooltip)."""
    fig_dict = {"data": [], "layout": fig.layout.to_plotly_json()}
    if dates is not None:
        # Store date strings in layout for JS tooltip
        if hasattr(dates[0], 'strftime'):
            fig_dict["layout"]["_dates"] = [d.strftime("%Y-%m-%d %H:%M") for d in dates]
        else:
            fig_dict["layout"]["_dates"] = [str(d) for d in dates]
    for trace in fig.data:
        tr = trace.to_plotly_json()
        if isinstance(tr.get("x"), dict) and "bdata" in tr["x"]:
            tr["x"] = trace.x.tolist()
        if isinstance(tr.get("y"), dict) and "bdata" in tr["y"]:
            tr["y"] = trace.y.tolist()
        fig_dict["data"].append(tr)

    class _NpEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                if np.isnan(obj) or np.isinf(obj):
                    return None
                return float(obj)
            return super().default(obj)

    def _sanitize_for_json(obj):
        """递归替换 NaN/Inf 为 None (JSON null)。"""
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

    figure_json = json.dumps(_sanitize_for_json(fig_dict), cls=_NpEncoder)
    div_id = f"plot-{uuid.uuid4().hex[:8]}"

    html = """<!DOCTYPE html>
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
var _fallbackEl = document.getElementById('plotly-fallback-{div_id}');
if (typeof Plotly === 'undefined') {{
    _fallbackEl.style.display = 'block';
    document.getElementById('{div_id}').style.display = 'none';
    return;
}} else if (window._plotlyCdnFailed) {{
    // 从CDNJS fallback成功加载，清除标记
    delete window._plotlyCdnFailed;
}}
var figure = {figure_json};
    var config = {{
        responsive: true,
        displayModeBar: true,
        displaylogo: false,
        modeBarButtonsToRemove: ['lasso2d', 'select2d']
    }};
    Plotly.newPlot('{div_id}', figure.data, figure.layout, config).then(function(gd) {{
        var _lastXv = -Infinity, _pending = false, _pendingXv = null, _THROTTLE_MS = 45;
        function _nearestIdx(arr, xv) {{
            var lo = 0, hi = arr.length - 1;
            if (xv <= arr[lo]) return lo;
            if (xv >= arr[hi]) return hi;
            while (lo < hi - 1) {{ var mid = (lo + hi) >> 1; if (arr[mid] <= xv) lo = mid; else hi = mid; }}
            return (xv - arr[lo] <= arr[hi] - xv) ? lo : hi;
        }}
        var _shapeKeys = [];
        var shapes = gd.layout.shapes || [];
        for (var i = 0; i < shapes.length; i++) {{
            if (shapes[i].yref === 'paper' || shapes[i].yref === 'y domain') {{
                _shapeKeys.push({{ x0: 'shapes[' + i + '].x0', x1: 'shapes[' + i + '].x1', vis: 'shapes[' + i + '].visible' }});
            }}
        }}
        var _xArr0 = gd.data[0].x;
        var _dates = gd.layout._dates;
        var _hasDates = _dates && _dates.length > 0;
        var _tip = document.getElementById('date-tip-{div_id}');
        var _dateCache = '';

        function _apply(xv) {{
            _pending = false;
            var u = {{}};
            for (var i = 0; i < _shapeKeys.length; i++) {{ var k = _shapeKeys[i]; u[k.x0] = xv; u[k.x1] = xv; u[k.vis] = true; }}
            Plotly.relayout(gd, u);
            _lastXv = xv;
            if (_hasDates && _xArr0 && _xArr0.length > 0) {{
                var idx = _nearestIdx(_xArr0, xv);
                _dateCache = (idx < _dates.length) ? _dates[idx] : '';
                if (_dateCache) {{
                    _tip.textContent = _dateCache;
                    _tip.style.display = 'block';
                }} else {{
                    _tip.style.display = 'none';
                }}
            }}
        }}

        gd.on('plotly_hover', function(evt) {{
            if (!evt.points || evt.points.length === 0) return;
            var xv = evt.points[0].x;
            if (xv === _lastXv) return;
            if (_pending) {{ _pendingXv = xv; }}
            else {{
                _pending = true; _pendingXv = null;
                _apply(xv);
                setTimeout(function() {{
                    if (_pendingXv !== null && _pendingXv !== _lastXv) _apply(_pendingXv);
                    else _pending = false;
                }}, _THROTTLE_MS);
            }}
        }});

        gd.on('plotly_unhover', function() {{
            _pending = false; _pendingXv = null; _lastXv = -Infinity;
            var u = {{}};
            for (var i = 0; i < _shapeKeys.length; i++) {{ u[_shapeKeys[i].vis] = false; }}
            Plotly.relayout(gd, u);
            _tip.style.display = 'none';
            _dateCache = '';
        }});

        document.getElementById('{div_id}').addEventListener('mousemove', function(e) {{
            if (_tip.style.display === 'block') {{
                var tx = e.clientX + 16;
                var tw = _tip.offsetWidth || 100;
                if (tx + tw > window.innerWidth - 10) tx = e.clientX - tw - 16;
                if (tx < 5) tx = 5;
                _tip.style.left = tx + 'px';
                _tip.style.top = (e.clientY - 28) + 'px';
            }}
        }});
    }});
    // Safety check: if Plotly still not loaded after 5s, show fallback
    setTimeout(function() {{
        if (typeof Plotly === 'undefined') {{
            _fallbackEl.style.display = 'block';
            document.getElementById('{div_id}').style.display = 'none';
        }}
    }}, 5000);
}})();
</script>
</body>
</html>""".format(div_id=div_id, figure_json=figure_json)

    return st.components.v1.html(html, height=height)


# ---------------------------------------------------------------------------
# Prediction traces on chart
# ---------------------------------------------------------------------------
def _add_prediction_traces(fig, t, filtered, fit_result, fit_start, pair_end, row,
                          n_extend=10, show_legend=True):
    """在 price 子图上添加预测曲线 + 残差子图上的拟合残差。"""
    name = "预测曲线"
    fit_color = "#f0a040"   # 橙色
    pred_color = "#a371f7"  # 紫色
    a, b, c = fit_result["a"], fit_result["b"], fit_result["c"]

    # 拟合段 — 橙色实线
    x_fit = t[fit_start:pair_end + 1]
    y_fit = fit_result["y_fit"]
    fig.add_trace(go.Scatter(
        x=x_fit, y=y_fit,
        mode="lines", name=f"{name}(拟合)",
        line=dict(color=fit_color, width=2),
        legendgroup=name,
        showlegend=show_legend,
    ), row=row, col=1)

    # 前向延伸 — 紫色虚线
    y_ext = None
    if n_extend > 0:
        x_ext = np.arange(pair_end, pair_end + n_extend)
        x0 = fit_result.get("x0", None)
        if x0 is not None:
            y_ext = np.polyval((a, b, c), x_ext - x0)
        else:
            y_ext = np.polyval((a, b, c), x_ext)
        fig.add_trace(go.Scatter(
            x=x_ext, y=y_ext,
            mode="lines", name=f"{name}(预测)",
            line=dict(color=pred_color, width=2, dash="dash"),
            legendgroup=name,
            showlegend=show_legend,
        ), row=row, col=1)

    # 残差子图 — 前向预测段与最后已知滤波价格的残差
    if y_ext is not None and n_extend > 0:
        baseline = filtered[pair_end]
        residual = y_ext - baseline
        upward = y_ext[-1] > y_ext[0]
        res_color = "#f85149" if upward else "#3fb950"
        fig.add_trace(go.Scatter(
            x=x_ext, y=residual,
            mode="lines", name=f"{name}(残差)",
            line=dict(color=res_color, width=1.5, dash="dot"),
            legendgroup=name,
            showlegend=show_legend,
        ), row=row + 1, col=1)


# ---------------------------------------------------------------------------
# Shared PnL rendering helpers
# ---------------------------------------------------------------------------
def _render_entry_marker(fig, t, bar_idx, pnl_val, row, col=1,
                         color="#d2991d", size=9, hovertext=""):
    """统一的入场标记（三角形）。"""
    if not (0 <= bar_idx < len(t)):
        return
    fig.add_trace(go.Scatter(
        x=[t[bar_idx]], y=[pnl_val],
        mode="markers",
        marker=dict(color=color, symbol="triangle-up", size=size,
                    line=dict(width=1, color="rgba(0,0,0,0.3)")),
        showlegend=False,
        hovertext=hovertext,
        hoverinfo="text",
    ), row=row, col=col)


def _render_exit_marker_with_label(fig, t, bar_idx, pnl_val, row, col=1,
                                   color="#d2991d", trade_type="long",
                                   exit_reason="", ret_pct=0.0,
                                   hovertext=""):
    """统一的离场标记（止损=x / 止盈=circle）+ 盈亏标注。"""
    if not (0 <= bar_idx < len(t)):
        return
    is_sl = exit_reason == "stop_loss"
    sym = "x" if is_sl else "circle"
    ec = "#f85149" if is_sl else "#3fb950"
    fig.add_trace(go.Scatter(
        x=[t[bar_idx]], y=[pnl_val],
        mode="markers",
        marker=dict(color=color, symbol=sym, size=9,
                    line=dict(width=1, color=ec)),
        showlegend=False,
        hovertext=hovertext,
        hoverinfo="text",
    ), row=row, col=col)

    label_color = "#f85149" if is_sl else "#3fb950"
    arrow = "↑" if trade_type == "long" else "↓"
    fig.add_annotation(
        x=t[bar_idx], y=pnl_val,
        text=f"{arrow}{ret_pct:+.1f}%",
        showarrow=False,
        font=dict(size=8, color=label_color),
        yshift=12,
        row=row, col=col,
    )


def _render_pnl_curves(fig, t, long_filtered, short_filtered, row, col=1,
                       long_color="#3fb950", short_color="#f85149",
                       long_name="做多PnL", short_name="做空PnL",
                       show_legend=False):
    """为子图渲染橙色/绿色PnL基线曲线。"""
    fig.add_trace(go.Scatter(
        x=t, y=long_filtered,
        mode="lines", name=long_name,
        line=dict(color=long_color, width=1.5, dash="solid"),
        showlegend=show_legend,
    ), row=row, col=col)
    fig.add_trace(go.Scatter(
        x=t, y=short_filtered,
        mode="lines", name=short_name,
        line=dict(color=short_color, width=1.5, dash="solid"),
        showlegend=show_legend,
    ), row=row, col=col)


def _render_baseline(fig, row, col=1, y=100, opacity=0.5):
    """渲染100基准线。"""
    fig.add_hline(y=y, line_dash="dash", line_color="gray",
                  opacity=opacity, row=row, col=col)


def _render_fill_background(fig, t, y_values, row, col=1,
                            color="rgba(63,185,80,0.04)", baseline=100):
    """渲染PnL区域半透明背景。"""
    y_max = max(float(np.nanmax(y_values)), baseline) * 1.02
    fig.add_trace(go.Scatter(
        x=[t[0], t[-1], t[-1], t[0]],
        y=[baseline, baseline, y_max, y_max],
        fill="toself", fillcolor=color,
        mode="lines", line=dict(width=0),
        showlegend=False, hoverinfo="skip",
    ), row=row, col=col)


# ---------------------------------------------------------------------------
# Cross-period PnL reference subplot
# ---------------------------------------------------------------------------
def _add_cross_pnl_subplot(fig, t, aligned, row, higher_tf=""):
    """在指定row添加高周期PnL参考子图（事件标记+参考线+盈亏标注）。"""
    color_long = "#3fb950"
    color_short = "#f85149"
    marker_color = "#d2991d"  # 金色，高周期标记统一色
    tf_label = higher_tf or "高周期"

    # 高周期做多PnL参考线（虚线）
    mask_long = ~np.isnan(aligned["aligned_long"])
    if mask_long.any():
        fig.add_trace(go.Scatter(
            x=t[mask_long], y=aligned["aligned_long"][mask_long],
            mode="lines", name=f"{tf_label}多",
            line=dict(color=color_long, width=1.2, dash="dot"),
            showlegend=False,
        ), row=row, col=1)

    # 高周期做空PnL参考线（点线）
    mask_short = ~np.isnan(aligned["aligned_short"])
    if mask_short.any():
        fig.add_trace(go.Scatter(
            x=t[mask_short], y=aligned["aligned_short"][mask_short],
            mode="lines", name=f"{tf_label}空",
            line=dict(color=color_short, width=1.2, dash="dot"),
            showlegend=False,
        ), row=row, col=1)

    # 入场标记（▲三角形）
    for bar_idx, trade_type, pnl_val in aligned["entry_markers"]:
        _render_entry_marker(
            fig, t, bar_idx, pnl_val, row,
            color=marker_color, size=9,
            hovertext=f"{tf_label}入场 {'多' if trade_type == 'long' else '空'}",
        )

    # 离场标记 + 盈亏标注
    for bar_idx, trade_type, pnl_val, ret_pct, exit_reason in aligned["exit_markers"]:
        _render_exit_marker_with_label(
            fig, t, bar_idx, pnl_val, row,
            color=marker_color, trade_type=trade_type,
            exit_reason=exit_reason, ret_pct=ret_pct,
            hovertext=f"{tf_label}离场 {'多' if trade_type == 'long' else '空'} | {ret_pct:+.2f}%",
        )

    # 100基准线
    _render_baseline(fig, row, opacity=0.4)


def _add_alignment_subplot(fig, t, long_pnl, short_pnl, trade_records,
                           long_mask, short_mask, row):
    """同向性判断子图：高周期持仓时sample，非持仓时hold。"""
    n = len(t)

    long_filtered = np.full(n, 100.0)
    for i in range(1, n):
        if long_mask[i] and long_pnl[i - 1] != 0:
            long_filtered[i] = long_filtered[i - 1] * (long_pnl[i] / long_pnl[i - 1])
        else:
            long_filtered[i] = long_filtered[i - 1]

    short_filtered = np.full(n, 100.0)
    for i in range(1, n):
        if short_mask[i] and short_pnl[i - 1] != 0:
            short_filtered[i] = short_filtered[i - 1] * (short_pnl[i] / short_pnl[i - 1])
        else:
            short_filtered[i] = short_filtered[i - 1]

    _render_pnl_curves(fig, t, long_filtered, short_filtered, row)

    for trade in trade_records:
        is_long = trade["type"] == "long"
        mask = long_mask if is_long else short_mask
        entry_i = trade["entry_idx"]
        exit_i = trade["exit_idx"]
        if entry_i >= n or exit_i >= n:
            continue
        seg_range = slice(entry_i, exit_i + 1)
        if not mask[seg_range].any():
            continue
        curve = long_filtered if is_long else short_filtered
        seg_t = t[seg_range]
        seg_pnl = curve[seg_range]
        color = "#3fb950" if is_long else "#f85149"

        fig.add_trace(go.Scatter(
            x=seg_t, y=seg_pnl,
            mode="lines",
            name=f"{'多' if is_long else '空'}#{trade['id']}",
            line=dict(color=color, width=3),
            showlegend=False,
        ), row=row, col=1)

        if mask[entry_i]:
            _render_entry_marker(
                fig, seg_t, 0, seg_pnl[0], row,
                color=color, size=8,
            )

        if trade["exit_reason"] in ("stop_loss", "take_profit") and mask[exit_i]:
            _render_exit_marker_with_label(
                fig, seg_t, -1, seg_pnl[-1], row,
                color=color, trade_type=trade["type"],
                exit_reason=trade["exit_reason"], ret_pct=trade["return_pct"],
            )

    _render_fill_background(fig, t, long_filtered, row,
                            color="rgba(63,185,80,0.04)", baseline=100)
    _render_fill_background(fig, t, short_filtered, row,
                            color="rgba(248,81,73,0.04)", baseline=100)

    _render_baseline(fig, row, opacity=0.5)
