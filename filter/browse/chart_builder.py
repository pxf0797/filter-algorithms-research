"""
图表构建模块 — 从 streamlit_app.py 提取的图表 trace/shape/annotation 构建函数

无 Streamlit 状态依赖，仅依赖 numpy + plotly，可独立单元测试。
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from filter.browse.charts import (
    _add_prediction_traces, _add_cross_pnl_subplot, _add_alignment_subplot,
    _draw_holding_bands, _add_bs_markers,
)
from filter.common.pnl_renderer import (
    PNL_BASELINE,
    compute_combined_pnl,
    make_pnl_long_trace,
    make_pnl_short_trace,
    make_pnl_baseline_shape,
    make_pnl_yaxis_config,
)
from filter.constants.colors import COLORS, COLORS_CB, get_colors


def _date_markers(dates, tf) -> tuple:
    """Compute positions and labels for vertical date markers on the chart.

    Generates marker positions (x-axis indices) and corresponding
    date-format labels based on the active timeframe.  Different
    timeframes trigger different spacing logic (e.g. daily boundaries
    for minute TFs, Mondays for daily, month boundaries for weekly).

    Parameters
    ----------
    dates : list of pandas.Timestamp or None
        Sorted date index for the current view.  If ``None`` or empty
        both return values are empty lists.
    tf : str
        Timeframe label used to decide marker granularity.  One of
        ``"1分钟"``, ``"5分钟"``, ``"15分钟"``, ``"60分钟"``, ``"日线"``,
        ``"周线"``, ``"月线"``, ``"季线"``.

    Returns
    -------
    tuple of (list, list)
        ``(positions, labels)`` where *positions* are integer x-axis
        indices and *labels* are formatted date strings (e.g. ``"01/15"``,
        ``"2024"``).
    """
    if dates is None or len(dates) == 0:
        return [], []
    positions, labels = [], []
    n = len(dates)
    if tf in ("1分钟", "5分钟", "15分钟", "60分钟"):
        prev_d = None
        for i, d in enumerate(dates):
            day = d.date() if hasattr(d, 'date') else pd.Timestamp(d).date()
            if prev_d is not None and day != prev_d:
                positions.append(i)
                labels.append(day.strftime("%m/%d"))
            prev_d = day
    elif tf == "日线":
        for i, d in enumerate(dates):
            if d.weekday() == 0:
                positions.append(i)
                labels.append(d.strftime("%m/%d"))
    elif tf == "周线":
        prev_m = None
        for i, d in enumerate(dates):
            m = d.month
            if prev_m is not None and m != prev_m:
                positions.append(i)
                labels.append(d.strftime("%m/%d"))
            prev_m = m
    elif tf == "月线":
        for i, d in enumerate(dates):
            if d.month == 1:
                positions.append(i)
                labels.append(d.strftime("%Y"))
    else:  # 季线
        for i, d in enumerate(dates):
            if d.month == 1:
                positions.append(i)
                labels.append(d.strftime("%Y"))
    return positions, labels


def _determine_subplot_layout(has_s, has_strategy, has_cross, has_alignment, _higher_tf) -> tuple:
    """Determine subplot layout dimensions based on enabled features.
    Returns (rows, rh, titles, mr, rr, vr, sar, ssr, ar, pnl_row, cross_row, align_row)."""
    if has_s:
        if has_strategy:
            if has_cross:
                if has_alignment:
                    rows = 8
                    rh = [0.48, 0.11, 0.06, 0.06, 0.08, 0.24, 0.05, 0.18]
                    titles: tuple[str, ...] = ("价格&滤波", "残差", "速度v", "a&±ε", "Sig_t", "PnL收益(%)", f"{_higher_tf}持仓状态", "同向性判断")
                    pnl_row = 6
                    cross_row = 7
                    align_row = 8
                else:
                    rows = 7
                    rh = [0.56, 0.11, 0.06, 0.06, 0.08, 0.27, 0.06]
                    titles = ("价格&滤波", "残差", "速度v", "a&±ε", "Sig_t", "PnL收益(%)", f"{_higher_tf}持仓状态")
                    pnl_row = 6
                    cross_row = 7
                    align_row = None
            else:
                rows = 6
                rh = [0.44, 0.11, 0.06, 0.06, 0.08, 0.375]
                titles = ("价格&滤波", "残差", "速度v", "a&±ε", "Sig_t", "PnL收益(%)")
                pnl_row = 6
                cross_row = None
                align_row = None
        else:
            rows = 5
            rh = [0.57, 0.14, 0.09, 0.09, 0.11]
            titles = ("价格&滤波", "残差", "速度v", "a&±ε", "Sig_t")
            pnl_row = None
            cross_row = None
            align_row = None
        return rows, rh, titles, 1, 2, 3, 4, 5, None, pnl_row, cross_row, align_row
    else:
        return 4, [0.61, 0.18, 0.10, 0.11], ("价格&滤波", "残差", "速度v", "加速度a"), \
               1, 2, 3, None, None, 4, None, None, None


def _insert_feedback_row(rows, rh, titles, pnl_row, cross_row, align_row):
    """在 pnl_row 下方插入「实际持仓过程」子图行；cross/align 行顺延。

    从 PnL 行高匀一部分给新行，保持总高不膨胀，不改动 _determine_subplot_layout
    的组合分支。Returns (rows, rh, titles, feedback_row, cross_row, align_row)。
    """
    feedback_row = pnl_row + 1
    rh = list(rh)
    fb_h = rh[pnl_row - 1] * 0.14
    rh[pnl_row - 1] = rh[pnl_row - 1] - fb_h
    rh.insert(pnl_row, fb_h)                       # 插到 PnL 行之后
    titles = list(titles)
    titles.insert(pnl_row, "实际持仓状态")          # 0-index=pnl_row 即 PnL 之后
    cross_row = cross_row + 1 if cross_row is not None else None
    align_row = align_row + 1 if align_row is not None else None
    return rows + 1, rh, tuple(titles), feedback_row, cross_row, align_row


def _add_main_price_traces(t, noisy, ohlc, filtered, filtered2, cfg, mr):
    """Return trace dicts for K-line, close, and filter lines.
    mr is always 1 in the current layout; row1 axis IDs are "x"/"y" (no number suffix)."""
    _ax = "x" if mr == 1 else f"x{mr}"
    _ay = "y" if mr == 1 else f"y{mr}"
    traces = [dict(type="candlestick", x=t,
        open=ohlc["Open"].values.ravel(), high=ohlc["High"].values.ravel(),
        low=ohlc["Low"].values.ravel(), close=ohlc["Close"].values.ravel(),
        name="K", increasing_line_color=COLORS["kline_increasing"],
        decreasing_line_color=COLORS["kline_decreasing"],
        showlegend=False, xaxis=_ax, yaxis=_ay),
        dict(type="scattergl", x=t, y=noisy, mode="lines", name="收盘",
            line=dict(color=COLORS["close_line"], width=1.0), xaxis=_ax, yaxis=_ay)]
    if not np.all(np.isnan(filtered)):
        traces.append(dict(type="scattergl", x=t, y=filtered, mode="lines",
            name="滤波", line=dict(color=cfg["fc"], width=2.0), xaxis=_ax, yaxis=_ay))
    if cfg["_dual"] and filtered2 is not None and not np.all(np.isnan(filtered2)):
        traces.append(dict(type="scattergl", x=t, y=filtered2, mode="lines",
            name="滤波2", line=dict(color=cfg["fc2"], width=2.0), xaxis=_ax, yaxis=_ay))
    return traces


def _add_residual_traces(t, filtered, noisy, filtered2, cfg, rr, vr):
    """Add residual, velocity, and acceleration traces to subplots. Returns (acc, traces, shapes)."""
    if len(t) < 2:
        return np.array([]), [], []
    if not np.all(np.isnan(filtered)):
        vel = np.gradient(filtered, t); acc = np.gradient(vel, t)
        return acc, [
            dict(type="scattergl", x=t, y=filtered - noisy, mode="lines", name="残差",
                line=dict(color=COLORS["residual"], width=1.0, dash="dot"),
                xaxis=f"x{rr}", yaxis=f"y{rr}"),
            dict(type="scattergl", x=t, y=vel, mode="lines", name="v",
                line=dict(color=cfg["fc"], width=1.5), xaxis=f"x{vr}", yaxis=f"y{vr}")], [
            dict(type="line", x0=0, x1=1, xref="paper", y0=0, y1=0, yref=f"y{rr}",
                line=dict(color="gray", dash="dash"), opacity=0.5),
            dict(type="line", x0=0, x1=1, xref="paper", y0=0, y1=0, yref=f"y{vr}",
                line=dict(color="gray", dash="dash"), opacity=0.5)]
    vel = np.gradient(filtered, t) if not np.all(np.isnan(filtered)) else np.zeros_like(t)
    return np.gradient(vel, t), [], []


def _add_schmitt_traces(t, schmitt, acc, all_pairs, sar, ssr):
    """Return (traces, shapes) for Schmitt trigger subplots."""
    eps = schmitt["eps"]
    sig = schmitt["sig"]
    _sar_x = f"x{sar}"; _sar_y = f"y{sar}"; _ssr_x = f"x{ssr}"; _ssr_y = f"y{ssr}"
    traces = [
        dict(type="scattergl", x=list(t) + list(t[::-1]), y=list(eps) + list(-eps[::-1]),
            fill="toself", fillcolor=COLORS["epsilon_band_fill"], line=dict(width=0),
            name="±ε", hoverinfo="skip", xaxis=_sar_x, yaxis=_sar_y),
        dict(type="scattergl", x=t, y=eps, mode="lines", name="+ε",
            line=dict(color=COLORS["epsilon"], width=0.8, dash="dash"), showlegend=False,
            xaxis=_sar_x, yaxis=_sar_y),
        dict(type="scattergl", x=t, y=-eps, mode="lines", name="-ε",
            line=dict(color=COLORS["epsilon"], width=0.8, dash="dash"),
            xaxis=_sar_x, yaxis=_sar_y),
        dict(type="scattergl", x=t, y=schmitt["sigma_v"], mode="lines", name="σ(v)",
            line=dict(color=COLORS["sigma_v"], width=1.0, dash="dot"),
            xaxis=_sar_x, yaxis=_sar_y),
        dict(type="scattergl", x=t, y=acc, mode="lines", name="a",
            line=dict(color=COLORS["acceleration"], width=1.5), xaxis=_sar_x, yaxis=_sar_y),
        dict(type="scattergl", x=t, y=sig.astype(float), mode="lines", name="Sig",
            line=dict(color=COLORS["signal"], width=2, shape="hv"),
            xaxis=_ssr_x, yaxis=_ssr_y)]
    for state, cl in [(1, COLORS["sig_long_fill"]), (-1, COLORS["sig_short_fill"])]:
        msk = sig == state
        if msk.any():
            traces.append(dict(type="scattergl", x=t[msk], y=np.where(msk, state, 0),
                mode="lines", line=dict(width=0), fill="tozeroy",
                fillcolor=cl, showlegend=False, hoverinfo="skip",
                xaxis=_ssr_x, yaxis=_ssr_y))
    for i, (p_start, p_end) in enumerate(all_pairs):
        direction = sig[p_end]
        y_lo, y_hi = (0, 1) if direction == 1 else (-1, 0)
        band_color = COLORS["sig_band_even"] if i % 2 == 0 else COLORS["sig_band_odd"]
        traces.append(dict(type="scattergl",
            x=[p_start, p_end, p_end, p_start],
            y=[y_hi, y_hi, y_lo, y_lo],
            fill="toself", fillcolor=band_color,
            mode="lines", line=dict(width=0),
            showlegend=False, hoverinfo="skip",
            xaxis=_ssr_x, yaxis=_ssr_y))
    shapes = [dict(type="line", x0=0, x1=1, xref="paper", y0=0, y1=0, yref=f"y{sar}",
        line=dict(color="gray", dash="solid"), opacity=0.3)]
    return traces, shapes


def _add_pnl_traces(t, long_pnl, short_pnl, trade_records, pnl_row, colorblind: bool = False):
    """Return (traces, shapes, annotations, yaxes) for PnL subplot.

    色盲模式下: 颜色改用 CUD 调色板 + 线型/标记双编码区分做多(实线+circle)与做空(虚线+triangle-down)。
    """
    colors = get_colors(colorblind)
    _pnl_x = f"x{pnl_row}"; _pnl_y = f"y{pnl_row}"
    _pnl_annotations = []  # collected annotations (replaces fig.add_annotation)
    # Trace merge: 逐笔交易段合并为 2 条 (多/空), NaN 分隔
    _l_seg: list[tuple[float, float]] = []
    _s_seg: list[tuple[float, float]] = []
    _l_entry_x: list[float] = []
    _l_entry_y: list[float] = []  # ▲ 入场标记
    _s_entry_x: list[float] = []
    _s_entry_y: list[float] = []
    _l_exit_sl_x: list[float] = []
    _l_exit_sl_y: list[float] = []  # ✕ 止损离场
    _l_exit_tp_x: list[float] = []
    _l_exit_tp_y: list[float] = []  # ○ 止盈离场
    _s_exit_sl_x: list[float] = []
    _s_exit_sl_y: list[float] = []
    _s_exit_tp_x: list[float] = []
    _s_exit_tp_y: list[float] = []
    for trade in trade_records:
        seg_t = t[trade["entry_idx"]:trade["exit_idx"] + 1]
        curve = long_pnl if trade["type"] == "long" else short_pnl
        seg_pnl = curve[trade["entry_idx"]:trade["exit_idx"] + 1]
        if trade["type"] == "long":
            _l_seg.extend(zip(seg_t, seg_pnl)); _l_seg.append((float('nan'), float('nan')))
            _l_entry_x.append(seg_t[0]); _l_entry_y.append(seg_pnl[0])
        else:
            _s_seg.extend(zip(seg_t, seg_pnl)); _s_seg.append((float('nan'), float('nan')))
            _s_entry_x.append(seg_t[0]); _s_entry_y.append(seg_pnl[0])
        if trade["exit_reason"] == "stop_loss":
            (aplat_x := _l_exit_sl_x if trade["type"]=="long" else _s_exit_sl_x).append(seg_t[-1])
            (aplat_y := _l_exit_sl_y if trade["type"]=="long" else _s_exit_sl_y).append(seg_pnl[-1])
        elif trade["exit_reason"] == "take_profit":
            (aplat_x := _l_exit_tp_x if trade["type"]=="long" else _s_exit_tp_x).append(seg_t[-1])
            (aplat_y := _l_exit_tp_y if trade["type"]=="long" else _s_exit_tp_y).append(seg_pnl[-1])
        if trade["exit_reason"] in ("stop_loss", "take_profit"):
            ret_pct = trade["return_pct"]
            label_color = colors["exit_sl"] if trade["exit_reason"] == "stop_loss" else colors["exit_tp"]
            arrow = "↑" if trade["type"] == "long" else "↓"
            _pnl_annotations.append(dict(x=seg_t[-1], y=seg_pnl[-1],
                text=f"{arrow}{ret_pct:+.1f}%", showarrow=False,
                font=dict(size=8, color=label_color), yshift=12,
                xref=f"x{pnl_row}", yref=f"y{pnl_row}"))
    # Collect all PnL traces, shapes, annotations into return values
    _pnl_traces = [
        make_pnl_long_trace(t, long_pnl, row=pnl_row, colorblind=colorblind),
        make_pnl_short_trace(t, short_pnl, row=pnl_row, colorblind=colorblind),
    ]
    if _l_seg:
        xs, ys = zip(*_l_seg)
        _pnl_traces.append(dict(type="scattergl", x=list(xs), y=list(ys), mode="lines",
            name="做多段", line=dict(color=colors["pnl_long"], width=3), showlegend=False,
            xaxis=_pnl_x, yaxis=_pnl_y))
    if _s_seg:
        xs, ys = zip(*_s_seg)
        _pnl_traces.append(dict(type="scattergl", x=list(xs), y=list(ys), mode="lines",
            name="做空段", line=dict(color=colors["pnl_short"], width=3), showlegend=False,
            xaxis=_pnl_x, yaxis=_pnl_y))
    def _mk(xs, ys, sym, clr):
        if xs: _pnl_traces.append(dict(type="scattergl", x=xs, y=ys, mode="markers",
            marker=dict(color=clr, symbol=sym, size=8), showlegend=False,
            xaxis=_pnl_x, yaxis=_pnl_y))
    _mk(_l_entry_x, _l_entry_y, "triangle-up", colors["pnl_long"])
    _mk(_s_entry_x, _s_entry_y, "triangle-up", colors["pnl_short"])
    _mk(_l_exit_sl_x, _l_exit_sl_y, "x", colors["exit_sl"])
    _mk(_s_exit_sl_x, _s_exit_sl_y, "x", colors["exit_sl"])
    _mk(_l_exit_tp_x, _l_exit_tp_y, "circle", colors["exit_tp"])
    _mk(_s_exit_tp_x, _s_exit_tp_y, "circle", colors["exit_tp"])
    y_max_l = max(float(np.nanmax(long_pnl)), PNL_BASELINE) * 1.02
    _pnl_traces.append(dict(type="scattergl",
        x=[t[0], t[-1], t[-1], t[0]], y=[PNL_BASELINE, PNL_BASELINE, y_max_l, y_max_l],
        fill="toself", fillcolor=colors["pnl_long_bg"],
        mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip",
        xaxis=_pnl_x, yaxis=_pnl_y))
    y_min_s = min(float(np.nanmin(short_pnl)), PNL_BASELINE) * 0.98
    _pnl_traces.append(dict(type="scattergl",
        x=[t[0], t[-1], t[-1], t[0]], y=[PNL_BASELINE, PNL_BASELINE, y_min_s, y_min_s],
        fill="toself", fillcolor=colors["pnl_short_bg"],
        mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip",
        xaxis=_pnl_x, yaxis=_pnl_y))
    _pnl_shapes = [make_pnl_baseline_shape(pnl_row)]
    _pnl_yaxes = make_pnl_yaxis_config(pnl_row)
    return _pnl_traces, _pnl_shapes, _pnl_annotations, _pnl_yaxes


def _add_feedback_subplot(t, trade_records, row):
    """返回 (shapes, yaxes) 用于实际持仓状态子图（绿=做多/红=做空/空白=不持）。"""
    n = len(t)
    long_mask = np.zeros(n, dtype=bool)
    short_mask = np.zeros(n, dtype=bool)
    for tr in trade_records:
        a = tr["entry_idx"]
        if a >= n: continue
        b = min(tr["exit_idx"], n - 1)
        (long_mask if tr["type"] == "long" else short_mask)[a:b + 1] = True
    return _draw_holding_bands(t, long_mask, short_mask, row)
