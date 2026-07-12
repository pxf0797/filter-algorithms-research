"""
多周期股票滤波分析工具 — 4视图独立配置, 施密特触发器 + 滤波对比

入口文件：页面布局 + session_state初始化 + st.fragment 包装
"""

import hashlib
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from loguru import logger
import streamlit as st
import numpy as np
import pandas as pd


def _hash_array(arr):
    """Hash numpy array for @st.cache_data hash_funcs (bytes of contiguous copy)."""
    return hashlib.md5(np.ascontiguousarray(arr).data.tobytes()).hexdigest()
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from config_db import (init_config_tables, list_presets, apply_preset,
                        save_preset, delete_preset, rename_preset,
                        get_history,
                        import_json_files_as_presets, VIEW_PARAM_SPECS)
from db import (init_db, get_date_range, has_data,
                check_data_health, get_db_size_mb, snapshot_db, list_snapshots,
                restore_snapshot, prune_snapshots, clear_display_cache,
                checkpoint_wal, validate_db, compare_with_db, force_update_kline,
                DB_PATH)

# --- Import from new modules ---
from services.filter_engine import (
    FILTERS,
    _schmitt_trigger, _find_all_pairs,
    _fit_physics_parabola,
    _compute_strategy_pnl, _align_pnl_to_current_tf, _compute_holding_masks,
)
from services.data_loader import (
    _fetch_all_timeframes, _fetch_stock, _sync_to_display,
    _sync_all_cascading,
)
from components.charts import (
    _render_plotly, _add_prediction_traces,
    _add_cross_pnl_subplot, _add_alignment_subplot,
    _draw_holding_bands, _add_bs_markers,
)
from services.bs_marker import compute_bs_markers, get_lower_tfs
from components.sidebar import (
    _render_params, ALL_TFS, DEFAULT_TFS, TF_HIERARCHY,
)
from state import AppState
from backtest_logger import log_mode_switch, log_bar_navigation, log_data_load, log_error

# ---------------------------------------------------------------------------
# Page config (must be the first Streamlit command)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="滤波算法对比",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# Cached data-fetching wrapper (Streamlit cache over bare data_loader._fetch_stock)
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False, ttl=3600)
def _cached_fetch_stock(market, code, tf, n_pts, force_period=None):
    """Cached wrapper for yfinance data fetching.

    Wraps ``services.data_loader._fetch_stock`` with ``@st.cache_data``
    to avoid redundant API calls within the TTL window.  Clear the cache
    (via the refresh button or programmatically) to force a fresh fetch.

    Parameters
    ----------
    market : str
        Market identifier, e.g. ``"美股 US"``, ``"A股(沪深)"``, ``"港股 HK"``.
    code : str
        Ticker symbol, e.g. ``"AAPL"``, ``"600519"``.
    tf : str
        Timeframe label, e.g. ``"日线"``, ``"60分钟"``.
    n_pts : int
        Number of data points to request.
    force_period : str or None
        Override the auto-computed yfinance ``period`` parameter.

    Returns
    -------
    tuple
        ``(t, noisy, ohlc, ticker_full, dates, err)`` as produced by
        ``_fetch_stock``.
    """
    return _fetch_stock(market, code, tf, n_pts, force_period=force_period)


# =====================================================================
# Chart rendering — sub-functions extracted from _render_chart
# =====================================================================


def _date_markers(dates, tf) -> tuple[list, list]:
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


def _load_chart_data(market, ticker_code, tf, n_pts, window_start=None, cutoff_date=None) -> tuple:
    """Load chart data from display cache or fetch from API. Returns (t, noisy, ohlc, ticker_full, dates, err).

    浏览: window_start=None, cutoff_date=None
    回测: window_start=bar_index(窗口结束位置), cutoff_date=截止日期
    """
    if window_start is not None:
        # 回测模式: parquet 已由 _sync_all_cascading() 前置写入
        # 直接走下方 parquet 读取路径
        # ★ P1-4: parquet不存在时返回错误,不回退到yfinance(会返回最新数据破坏时间一致性)
        pass
        _is_backtest = True
    else:
        # 浏览模式：取最新 n_pts 条
        ok, count = _sync_to_display(ticker_code, tf, n_pts=n_pts)
        if not ok:
            # parquet 写入失败，直接走 API 回退
            return _cached_fetch_stock(market, ticker_code, tf, n_pts)
        _is_backtest = False
    display_path = Path(__file__).parent.parent / "data" / "display" / f"{tf}.parquet"
    err = None
    if display_path.exists():
        try:
            df = pd.read_parquet(display_path)
            if "Date" in df.columns and "Close" in df.columns and len(df) >= 2:
                df["Date"] = pd.to_datetime(df["Date"])
                df = df.set_index("Date").sort_index()

                # ★ 不再需要截断！parquet 已经是 n_pts 条
                if len(df) < 2:
                    err = f"{tf} 数据点不足 ({len(df)})"
                    return None, None, None, None, None, err

                t = np.arange(len(df), dtype=float)
                noisy = df["Close"].values.ravel()
                ohlc = df[["Open", "High", "Low", "Close"]] if all(c in df.columns for c in ["Open", "High", "Low"]) else pd.DataFrame({"Open": noisy, "High": noisy, "Low": noisy, "Close": noisy}, index=df.index)

                if window_start is not None:
                    try:
                        log_data_load(ticker_code, tf, len(df), cutoff_date or "", elapsed_ms=0)
                    except Exception as e:
                        logger.debug(f"回测日志写入失败: {e}")

                return t, noisy, ohlc, ticker_code, df.index, None
            else:
                err = "数据不足"
        except Exception as e:
            err = str(e)
    if err is not None:
        return None, None, None, None, None, err
    # ★ P1-4: 回测模式下不回退到yfinance(返回最新数据破坏时间一致性)
    if _is_backtest:
        return None, None, None, None, None, f"{tf} 数据未就绪，请先加载数据"
    return _cached_fetch_stock(market, ticker_code, tf, n_pts)


@st.cache_data(hash_funcs={np.ndarray: _hash_array}, show_spinner=False)
def _compute_filters(noisy, t, cfg) -> tuple[np.ndarray, np.ndarray | None]:
    """Compute primary and optional secondary filter. Returns (filtered, filtered2).
    Cached: reuses prior result when noisy/t/cfg unchanged (np.ndarray via _hash_array)."""
    sf = FILTERS.get(cfg["_fid"])
    if sf is None:
        logger.warning(f"Unknown filter_id '{cfg['_fid']}', skipping primary filter")
        filtered = np.full_like(noisy, np.nan)
        return filtered, None
    try:
        filtered = sf["func"](noisy, t, **cfg["pv"])
        filtered = np.asarray(filtered, dtype=float).ravel()
    except Exception as e:
        logger.error(f"Filter {cfg['_fid']} failed: {e}", exc_info=True)
        filtered = np.full_like(noisy, np.nan)
    filtered2 = None
    if cfg["_dual"] and cfg["_fid2"] and cfg["pv2"]:
        try:
            sf2 = FILTERS.get(cfg["_fid2"])
            if sf2 is None:
                logger.warning(f"Unknown filter_id2 '{cfg['_fid2']}', skipping secondary filter")
                filtered2 = np.full_like(noisy, np.nan)
            else:
                filtered2 = sf2["func"](noisy, t, **cfg["pv2"])
            filtered2 = np.asarray(filtered2, dtype=float).ravel()
        except Exception as e:
            logger.warning(f"Filter2 {cfg['_fid2']} failed: {e}")
            filtered2 = np.full_like(noisy, np.nan)
    return filtered, filtered2


@st.cache_data(hash_funcs={np.ndarray: _hash_array}, show_spinner=False)
def _compute_schmitt_trigger(filtered, t, cfg) -> dict | None:
    """Compute Schmitt trigger signal. Returns schmitt dict or None.
    Cached with _hash_array for filtered/t ndarray inputs."""
    if not cfg["show_sch"] or np.all(np.isnan(filtered)) or len(t) < 2:
        return None
    _v = np.gradient(filtered, t)
    _a = np.gradient(_v, t)
    logger.debug(f"Computing Schmitt trigger: ewma={cfg['ew']}, k_eps={cfg['ke']}, sigma_min={cfg['sm']}")
    return _schmitt_trigger(_v, _a, ewma_span=cfg["ew"], k_eps=cfg["ke"], sigma_min=cfg["sm"])


@st.cache_data(hash_funcs={np.ndarray: _hash_array}, show_spinner=False)
def _compute_prediction_pairs(t, filtered, schmitt, cfg, all_pairs) -> list:
    """Compute prediction curves for each pair. Returns list of pred_pairs dicts.
    Cached: pickle-serialize schmitt dict (window ≤300 rows, negligible overhead)."""
    if not cfg.get("show_pred") or schmitt is None:
        return []
    pred_pairs = []
    logger.debug(f"Computing prediction curves: {len(all_pairs)} pairs, mode={cfg.get('fit_mode')}")
    fit_func = _fit_physics_parabola
    for pair_start, pair_end in all_pairs:
        if pair_end - pair_start >= 3:
            fit_result = fit_func(t, filtered, pair_start, pair_end)
            if fit_result is not None:
                pred_pairs.append({
                    "fit_result": fit_result,
                    "fit_start": pair_start,
                    "pair_end": pair_end,
                })
    return pred_pairs


def _compute_strategy_display(t, filtered, schmitt, all_pairs, pred_pairs, cfg, tf, dates) -> tuple:
    """Compute strategy PnL and display summary captions. Returns (long_pnl, short_pnl, trade_records)."""
    show_strategy = cfg.get("show_strategy", False)
    stop_loss_pct = cfg.get("stop_loss_pct", 2.0)
    long_pnl = short_pnl = None
    trade_records = []
    if show_strategy and schmitt is not None and len(pred_pairs) > 0:
        logger.debug(f"Computing strategy PnL: stop_loss={stop_loss_pct}%, n_extend={cfg.get('n_ext', 10)}")
        long_pnl, short_pnl, trade_records = _compute_strategy_pnl(
            t, filtered, schmitt["sig"], all_pairs, pred_pairs, stop_loss_pct,
            n_extend=cfg.get("n_ext", 10),
        )

    has_strategy = show_strategy and long_pnl is not None and len(trade_records) > 0
    if has_strategy and trade_records:
        c4, c5, c6 = st.columns(3)
        win_trades = sum(1 for tr in trade_records if tr["return_pct"] > 0)
        long_ret = long_pnl[-1] - 100.0
        short_ret = short_pnl[-1] - 100.0
        total_ret = long_ret + short_ret
        c4.caption(f"交易: {len(trade_records)}笔 | 胜率: {win_trades}/{len(trade_records)}")
        c5.caption(f"多: {long_ret:+.2f}% | 空: {short_ret:+.2f}% | 总和: {total_ret:+.2f}%")
        peak_l = np.maximum.accumulate(long_pnl)
        drawdown_l = (long_pnl - peak_l) / peak_l * 100
        max_dd_l = np.min(drawdown_l)
        peak_s = np.maximum.accumulate(short_pnl)
        drawdown_s = (short_pnl - peak_s) / peak_s * 100
        max_dd_s = np.min(drawdown_s)
        c6.caption(f"多DD: {max_dd_l:.2f}% | 空DD: {max_dd_s:.2f}%")
        st.session_state[f"_pnl_{tf}"] = {
            "dates": dates, "t": t,
            "long_pnl": long_pnl, "short_pnl": short_pnl,
            "trade_records": trade_records,
        }
    return long_pnl, short_pnl, trade_records


def _determine_subplot_layout(has_s, has_strategy, has_cross, has_alignment, _higher_tf) -> tuple:
    """Determine subplot layout dimensions based on enabled features.
    Returns (rows, rh, titles, mr, rr, vr, sar, ssr, ar, pnl_row, cross_row, align_row)."""
    if has_s:
        if has_strategy:
            if has_cross:
                if has_alignment:
                    rows = 8
                    rh = [0.48, 0.11, 0.06, 0.06, 0.08, 0.24, 0.05, 0.18]
                    titles = ("价格&滤波", "残差", "速度v", "a&±ε", "Sig_t", "PnL收益(%)", f"{_higher_tf}持仓状态", "同向性判断")
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
        name="K", increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
        showlegend=False, xaxis=_ax, yaxis=_ay),
        dict(type="scattergl", x=t, y=noisy, mode="lines", name="收盘",
            line=dict(color="#5f6c80", width=1.0), xaxis=_ax, yaxis=_ay)]
    if not np.all(np.isnan(filtered)):
        traces.append(dict(type="scattergl", x=t, y=filtered, mode="lines",
            name="滤波", line=dict(color=cfg["fc"], width=2.0), xaxis=_ax, yaxis=_ay))
    if cfg["_dual"] and filtered2 is not None and not np.all(np.isnan(filtered2)):
        traces.append(dict(type="scattergl", x=t, y=filtered2, mode="lines",
            name="滤波2", line=dict(color=cfg["fc2"], width=2.0), xaxis=_ax, yaxis=_ay))
    return traces


def _add_residual_traces(t, filtered, noisy, filtered2, cfg, rr, vr):
    """Add residual, velocity, and acceleration traces to subplots. Returns acceleration array."""
    if len(t) < 2:
        return np.array([])
    if not np.all(np.isnan(filtered)):
        vel = np.gradient(filtered, t); acc = np.gradient(vel, t)
        return acc, [
            dict(type="scattergl", x=t, y=filtered - noisy, mode="lines", name="残差",
                line=dict(color="#5f6c80", width=1.0, dash="dot"),
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
            fill="toself", fillcolor="rgba(128,128,128,0.06)", line=dict(width=0),
            name="±ε", hoverinfo="skip", xaxis=_sar_x, yaxis=_sar_y),
        dict(type="scattergl", x=t, y=eps, mode="lines", name="+ε",
            line=dict(color="#f85149", width=0.8, dash="dash"), showlegend=False,
            xaxis=_sar_x, yaxis=_sar_y),
        dict(type="scattergl", x=t, y=-eps, mode="lines", name="-ε",
            line=dict(color="#f85149", width=0.8, dash="dash"),
            xaxis=_sar_x, yaxis=_sar_y),
        dict(type="scattergl", x=t, y=schmitt["sigma_v"], mode="lines", name="σ(v)",
            line=dict(color="#a371f7", width=1.0, dash="dot"),
            xaxis=_sar_x, yaxis=_sar_y),
        dict(type="scattergl", x=t, y=acc, mode="lines", name="a",
            line=dict(color="#d2991d", width=1.5), xaxis=_sar_x, yaxis=_sar_y),
        dict(type="scattergl", x=t, y=sig.astype(float), mode="lines", name="Sig",
            line=dict(color="#58a6ff", width=2, shape="hv"),
            xaxis=_ssr_x, yaxis=_ssr_y)]
    for state, cl in [(1, "rgba(63,185,80,0.06)"), (-1, "rgba(248,81,73,0.06)")]:
        msk = sig == state
        if msk.any():
            traces.append(dict(type="scattergl", x=t[msk], y=np.where(msk, state, 0),
                mode="lines", line=dict(width=0), fill="tozeroy",
                fillcolor=cl, showlegend=False, hoverinfo="skip",
                xaxis=_ssr_x, yaxis=_ssr_y))
    for i, (p_start, p_end) in enumerate(all_pairs):
        direction = sig[p_end]
        y_lo, y_hi = (0, 1) if direction == 1 else (-1, 0)
        band_color = "rgba(88,166,255,0.10)" if i % 2 == 0 else "rgba(163,113,247,0.10)"
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


def _add_pnl_traces(t, long_pnl, short_pnl, trade_records, pnl_row):
    """Return (traces, shapes, yaxes) for PnL subplot."""
    _pnl_x = f"x{pnl_row}"; _pnl_y = f"y{pnl_row}"
    _pnl_annotations = []  # collected annotations (replaces fig.add_annotation)
    # Trace merge: 逐笔交易段合并为 2 条 (多/空), NaN 分隔
    _l_seg, _s_seg = [], []
    _l_entry_x, _l_entry_y = [], []  # ▲ 入场标记
    _s_entry_x, _s_entry_y = [], []
    _l_exit_sl_x,   _l_exit_sl_y   = [], []  # ✕ 止损离场
    _l_exit_tp_x,   _l_exit_tp_y   = [], []  # ○ 止盈离场
    _s_exit_sl_x,   _s_exit_sl_y   = [], []
    _s_exit_tp_x,   _s_exit_tp_y   = [], []
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
            label_color = "#f85149" if trade["exit_reason"] == "stop_loss" else "#3fb950"
            arrow = "↑" if trade["type"] == "long" else "↓"
            _pnl_annotations.append(dict(x=seg_t[-1], y=seg_pnl[-1],
                text=f"{arrow}{ret_pct:+.1f}%", showarrow=False,
                font=dict(size=8, color=label_color), yshift=12,
                xref=f"x{pnl_row}", yref=f"y{pnl_row}"))
    # Collect all PnL traces, shapes, annotations into return values
    _pnl_traces = [
        dict(type="scattergl", x=t, y=long_pnl, mode="lines", name="做多PnL",
            line=dict(color="#3fb950", width=1.5, dash="solid"),
            xaxis=_pnl_x, yaxis=_pnl_y),
        dict(type="scattergl", x=t, y=short_pnl, mode="lines", name="做空PnL",
            line=dict(color="#f85149", width=1.5, dash="solid"),
            xaxis=_pnl_x, yaxis=_pnl_y)]
    if _l_seg:
        xs, ys = zip(*_l_seg)
        _pnl_traces.append(dict(type="scattergl", x=list(xs), y=list(ys), mode="lines",
            name="做多段", line=dict(color="#3fb950", width=3), showlegend=False,
            xaxis=_pnl_x, yaxis=_pnl_y))
    if _s_seg:
        xs, ys = zip(*_s_seg)
        _pnl_traces.append(dict(type="scattergl", x=list(xs), y=list(ys), mode="lines",
            name="做空段", line=dict(color="#f85149", width=3), showlegend=False,
            xaxis=_pnl_x, yaxis=_pnl_y))
    def _mk(xs, ys, sym, clr):
        if xs: _pnl_traces.append(dict(type="scattergl", x=xs, y=ys, mode="markers",
            marker=dict(color=clr, symbol=sym, size=8), showlegend=False,
            xaxis=_pnl_x, yaxis=_pnl_y))
    _mk(_l_entry_x, _l_entry_y, "triangle-up", "#3fb950")
    _mk(_s_entry_x, _s_entry_y, "triangle-up", "#f85149")
    _mk(_l_exit_sl_x, _l_exit_sl_y, "x", "#f85149")
    _mk(_s_exit_sl_x, _s_exit_sl_y, "x", "#f85149")
    _mk(_l_exit_tp_x, _l_exit_tp_y, "circle", "#3fb950")
    _mk(_s_exit_tp_x, _s_exit_tp_y, "circle", "#3fb950")
    y_max_l = max(float(np.nanmax(long_pnl)), 100.0) * 1.02
    _pnl_traces.append(dict(type="scattergl",
        x=[t[0], t[-1], t[-1], t[0]], y=[100, 100, y_max_l, y_max_l],
        fill="toself", fillcolor="rgba(63,185,80,0.04)",
        mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip",
        xaxis=_pnl_x, yaxis=_pnl_y))
    y_min_s = min(float(np.nanmin(short_pnl)), 100.0) * 0.98
    _pnl_traces.append(dict(type="scattergl",
        x=[t[0], t[-1], t[-1], t[0]], y=[100, 100, y_min_s, y_min_s],
        fill="toself", fillcolor="rgba(248,81,73,0.04)",
        mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip",
        xaxis=_pnl_x, yaxis=_pnl_y))
    _pnl_shapes = [dict(type="line", x0=0, x1=1, xref="paper", y0=100, y1=100,
        yref=f"y{pnl_row}", line=dict(color="gray", dash="dash"), opacity=0.5)]
    _pnl_yaxes = {f"yaxis{pnl_row}": {"title_text": "PnL(%)", "ticksuffix": "%"}}
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


def _get_min_tf_and_count(configs, ticker_code) -> tuple:
    """从4个视图的tf配置中确定最小周期（最精细）和总bar数。
    遍历configs中各视图的tf字段，取ALL_TFS中索引最小的（最精细的）为_min_tf。
    从 DB 查询该 tf 的全量 bar 数（不使用 parquet，parquet 只有窗口数据）。
    返回 (_min_tf, bar_count)。"""
    if not configs:
        return "", 0

    # 取索引最小的 tf（最精细周期）
    min_idx = len(ALL_TFS)
    min_tf = ""
    for cfg in configs:
        tf = cfg.get("tf", "")
        try:
            idx = ALL_TFS.index(tf)
            if idx < min_idx:
                min_idx = idx
                min_tf = tf
        except ValueError:
            continue

    if not min_tf:
        return "", 0

    # 从 DB 查询全量 bar 数（parquet 只含当前窗口数据，不能用作 slider 上限）
    try:
        from db import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM kline WHERE ticker=? AND timeframe=?",
                (ticker_code, min_tf),
            ).fetchone()
            bar_count = row[0] if row else 0
    except Exception:
        bar_count = 0

    return min_tf, bar_count


def _save_backtest_config(ticker_code, min_tf, bar_count, window_size):
    """保存回测配置到 JSON 文件。"""
    import json
    from datetime import datetime
    config_path = Path(__file__).parent.parent / "data" / "backtest_config.json"
    config = {
        "ticker": ticker_code,
        "min_tf": min_tf,
        "bar_count": bar_count,
        "window_size": window_size,
        "cached_at": datetime.now().isoformat(),
    }
    with open(config_path, "w") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def _load_backtest_config(ticker_code):
    """加载回测配置缓存。ticker匹配时返回配置，否则返回None。"""
    import json
    config_path = Path(__file__).parent.parent / "data" / "backtest_config.json"
    if config_path.exists():
        try:
            with open(config_path) as f:
                config = json.load(f)
            if config.get("ticker") == ticker_code:
                return config
        except Exception:
            pass
    return None


# =====================================================================
# Chart rendering (main figure builder)
# =====================================================================

@st.fragment
def _render_chart_fragment(market, ticker_code, cfg, key, compact=True, higher_pnl=None, window_start=None, cutoff_date=None) -> None:
    """Fragment wrapper around ``_render_chart`` for per-view independent re-rendering.

    The ``@st.fragment`` decorator enables each of the four chart views
    to re-render independently without triggering a full-page rerun.

    Parameters
    ----------
    market : str
        Market identifier.
    ticker_code : str
        Ticker symbol.
    cfg : dict
        Per-view configuration dict (timeframe, filter params, etc.).
    key : str
        Unique view key (e.g. ``"v0"``, ``"v1"``).
    compact : bool, default True
        If True, use a smaller chart height.
        Number of days to shift the window into the past.
    higher_pnl : dict or None
        Higher-timeframe PnL data from ``_align_pnl_to_current_tf``.
    window_start : int or None
        Backtest mode — window start bar index.
    cutoff_date : str or None
        Backtest mode — cutoff date string.

    Returns
    -------
    None
    """
    _render_chart(market, ticker_code, cfg, key, compact=compact, higher_pnl=higher_pnl, window_start=window_start, cutoff_date=cutoff_date)


def _render_chart(market, ticker_code, cfg, key, compact=True, higher_pnl=None, window_start=None, cutoff_date=None) -> None:
    """Fetch data and render the multi-subplot chart figure.

    This is the core chart builder.  It:
    1. Loads chart data (from Parquet cache or yfinance API).
    2. Computes primary (and optional secondary) filter output.
    3. Computes Schmitt trigger signal and pair segmentation.
    4. Computes prediction curves and strategy PnL.
    5. Builds a ``plotly`` figure with dynamic subplot layout.
    6. Renders the figure via ``_render_plotly``.

    Parameters
    ----------
    market : str
        Market identifier.
    ticker_code : str
        Ticker symbol.
    cfg : dict
        Per-view configuration dict containing at least ``"tf"``,
        ``"n_pts"``, ``"_fid"``, and display toggles.
    key : str
        Unique view key.
    compact : bool, default True
        If True, reduce chart height.
    higher_pnl : dict or None
        Higher-timeframe PnL data from ``_align_pnl_to_current_tf``.
        When non-None a cross-period PnL subplot is added.
    window_start : int or None
        Backtest mode — window start bar index for data loading.
    cutoff_date : str or None
        Backtest mode — cutoff date string for timeframe alignment.

    Returns
    -------
    None
    """
    tf = cfg["tf"]
    n_pts = cfg["n_pts"]
    logger.debug(f"Rendering chart: {ticker_code}/{tf} view={key} n_pts={n_pts}")

    # 查找紧邻高周期tf，尝试从session_state获取其PnL数据
    _higher_tf = TF_HIERARCHY.get(tf)
    _raw_higher = None
    if higher_pnl is None and _higher_tf is not None:
        _raw_higher = st.session_state.get(f"_pnl_{_higher_tf}")

    # ── Step 1: Load chart data ──
    t, noisy, ohlc, ticker_full, dates, err = _load_chart_data(market, ticker_code, tf, n_pts, window_start=window_start, cutoff_date=cutoff_date)
    if err is not None:
        if "数据点不足" in str(err):
            st.caption(f"⏳ {tf} 在回测日期前无足够数据")
            return
        st.error(err)
        return

    # ★ 防御性检查：即使没报错，数据也可能不足
    if t is None or len(t) < 2:
        st.caption(f"⚠️ {tf} 数据点不足 ({len(t) if t is not None else 0})，无法渲染")
        return

    # ── Step 2: Date markers ──
    marker_positions, marker_labels = _date_markers(dates, cfg["tf"])

    # ── Step 3: Align higher-period PnL ──
    if _raw_higher is not None and dates is not None:
        higher_pnl = _align_pnl_to_current_tf(
            _raw_higher["dates"], _raw_higher["long_pnl"], _raw_higher["short_pnl"],
            _raw_higher["trade_records"], dates,
        )
    elif higher_pnl is None:
        higher_pnl = None

    # ── Step 4: Compute filters ──
    filtered, filtered2 = _compute_filters(noisy, t, cfg)

    # ── Step 5: Info captions ──
    rough = float(np.sum(np.diff(filtered, 2) ** 2)) if len(filtered) > 2 else 0.0
    c1, c2, c3 = st.columns(3)
    c1.caption(f"{ticker_full}·{cfg['tf']}  |  ¥{noisy[-1]:.2f}")
    c2.caption(f"σ={noisy.std():.2f}  平滑={rough:.1f}")
    c3.caption(f"{len(t)} 点")

    # ── Step 6: Schmitt trigger ──
    schmitt = _compute_schmitt_trigger(filtered, t, cfg)
    if cfg["show_sch"] and schmitt is None and len(t) > 0:
        st.warning(f"⚠️ 施密特信号不可用：bar数({len(t)}) < N_EWMA({cfg['ew']})。"
                   f"请降低 N_EWMA 至 ≤{len(t)} 或增加数据点数(N)。")

    all_pairs = []
    if schmitt is not None:
        all_pairs = _find_all_pairs(schmitt["sig"])

    # ── Step 7: Prediction curves ──
    pred_pairs = _compute_prediction_pairs(t, filtered, schmitt, cfg, all_pairs)

    # ── Step 8: Strategy PnL ──
    long_pnl, short_pnl, trade_records = _compute_strategy_display(
        t, filtered, schmitt, all_pairs, pred_pairs, cfg, tf, dates)
    show_strategy = cfg.get("show_strategy", False)
    show_cross_pnl = cfg.get("show_cross_pnl", False)
    show_alignment = cfg.get("show_alignment", False)
    has_strategy = show_strategy and long_pnl is not None and len(trade_records) > 0

    show_pnl_feedback = cfg.get("show_pnl_feedback", False)
    has_feedback = has_strategy and show_pnl_feedback

    # ── Compute holding masks early (needed for both BS markers and alignment subplot) ──
    _align_masks = None
    if higher_pnl is not None:
        _align_masks = _compute_holding_masks(
            len(t), higher_pnl["entry_markers"], higher_pnl["exit_markers"])

    # ── Step 8.5: BS markers (仓位操作标识) ──
    _op_tf = st.session_state.get("operating_tf", "日线")
    _lower_tfs = st.session_state.get("_bs_lower_tfs", [])
    _show_bs = (tf == _op_tf) or (tf in _lower_tfs)
    bs_markers = None
    if _show_bs:
        _holding = _align_masks if _align_masks is not None else None
        bs_markers = compute_bs_markers(
            t, dates, schmitt, all_pairs, trade_records,
            tf, _op_tf, higher_bs=None,
            holding_masks=_holding,
        )
        st.session_state[f"_bs_{tf}"] = bs_markers

    # ── Step 9: Determine subplot layout ──
    has_s = schmitt is not None
    has_cross = (show_cross_pnl and higher_pnl is not None and
                 (len(higher_pnl.get("entry_markers", [])) > 0 or
                  len(higher_pnl.get("exit_markers", [])) > 0))
    has_alignment = (show_alignment and _align_masks is not None and
                     (_align_masks[0].any() or _align_masks[1].any()))

    rows, rh, titles, mr, rr, vr, sar, ssr, ar, pnl_row, cross_row, align_row = \
        _determine_subplot_layout(has_s, has_strategy, has_cross, has_alignment, _higher_tf)

    feedback_row = None
    if has_feedback and pnl_row is not None:
        rows, rh, titles, feedback_row, cross_row, align_row = _insert_feedback_row(
            rows, rh, titles, pnl_row, cross_row, align_row)

    # ── Step 10: Build figure (A: one-shot go.Figure from raw dicts) ──
    # 1. Collect ALL trace dicts, shapes, annotations from _add_* functions
    all_traces, all_shapes, all_annotations = [], [], []
    _layout_updates = {}  # yaxis config dicts merged later

    all_traces += _add_main_price_traces(t, noisy, ohlc, filtered, filtered2, cfg, mr)
    for i, pp in enumerate(pred_pairs):
        all_traces += _add_prediction_traces(t, filtered,
            pp["fit_result"], pp["fit_start"], pp["pair_end"], row=mr,
            n_extend=cfg.get("n_ext", 10), show_legend=(i == 0))
    acc, _tr, _sh = _add_residual_traces(t, filtered, noisy, filtered2, cfg, rr, vr)
    all_traces += _tr; all_shapes += _sh
    if has_s:
        _tr, _sh = _add_schmitt_traces(t, schmitt, acc, all_pairs, sar, ssr)
        all_traces += _tr; all_shapes += _sh
    if has_strategy:
        _tr, _sh, _an, _ya = _add_pnl_traces(t, long_pnl, short_pnl, trade_records, pnl_row)
        all_traces += _tr; all_shapes += _sh; all_annotations += _an; _layout_updates.update(_ya)
    if has_feedback and feedback_row is not None:
        _sh, _ya = _add_feedback_subplot(t, trade_records, feedback_row)
        all_shapes += _sh; _layout_updates.update(_ya)
    if has_cross and higher_pnl is not None and cross_row is not None:
        _sh, _ya = _add_cross_pnl_subplot(t, higher_pnl, row=cross_row)
        all_shapes += _sh; _layout_updates.update(_ya)
    if has_alignment and _align_masks is not None and align_row is not None:
        long_mask, short_mask = _align_masks
        _tr, _sh, _an, _ya = _add_alignment_subplot(t, long_pnl, short_pnl, trade_records,
            long_mask, short_mask, row=align_row)
        all_traces += _tr; all_shapes += _sh; all_annotations += _an
        _layout_updates.update(_ya)
    if ar is not None and not np.all(np.isnan(filtered)):
        all_traces.append(dict(type="scattergl", x=t, y=acc, mode="lines", name="a",
            line=dict(color="#ffa502", width=1.5), xaxis=f"x{ar}", yaxis=f"y{ar}"))
        all_shapes.append(dict(type="line", x0=0, x1=1, xref="paper", y0=0, y1=0,
            yref=f"y{ar}", line=dict(color="gray", dash="dash"), opacity=0.5))
    if bs_markers is not None:
        all_annotations += _add_bs_markers(t, ohlc, bs_markers)

    # 2. Get subplot layout skeleton from make_subplots (layout only, discard empty traces)
    _skeleton = make_subplots(rows=rows, cols=1, shared_xaxes=True,
        vertical_spacing=0.01, row_heights=rh, subplot_titles=titles)
    layout_dict = _skeleton.layout.to_plotly_json()

    # 3. Add shapes, annotations, and +epsilon crosshair line
    all_shapes.append(dict(type="line", x0=0, x1=0, y0=0, y1=1, xref="x", yref="paper",
        line=dict(color="rgba(200,200,200,0.4)", width=1, dash="dot"), visible=False))
    for pos in marker_positions:
        all_shapes.append(dict(type="line", x0=pos, x1=pos, yref="paper", y0=0, y1=1,
            line=dict(color="rgba(255,255,255,0.10)", width=0.8, dash="dot"), layer="below"))
    layout_dict["shapes"] = layout_dict.get("shapes", []) + all_shapes
    layout_dict["annotations"] = layout_dict.get("annotations", []) + all_annotations

    # 4. Final layout customizations (matching original make_subplots-based setup)
    fh = (620 if has_s else 420) if compact else (960 if has_s else 700)
    if has_cross: fh += 120
    if has_alignment: fh += 75
    layout_dict.update(template="plotly_dark", height=fh,
        margin=dict(l=10, r=10, t=25, b=10), hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
            font=dict(size=9)))
    # axis customizations
    layout_dict.setdefault(f"xaxis{rows}", {}).update(title_text="",
        tickvals=list(marker_positions), ticktext=list(marker_labels),
        tickfont=dict(size=9, color="#8b949e"))
    layout_dict.setdefault("xaxis", {}).update(rangeslider_visible=False)
    for _r, _t in [(mr,"价格"),(rr,"残差"),(vr,"速度")]:
        _yk = "yaxis" if _r == 1 else f"yaxis{_r}"
        layout_dict.setdefault(_yk, {}).update(title_text=_t)
    if has_s:
        layout_dict.setdefault(f"yaxis{sar}", {}).update(title_text="a±ε")
        layout_dict.setdefault(f"yaxis{ssr}", {}).update(title_text="Sig",
            tickvals=[-1,0,1], ticktext=["空","观","多"], range=[-1.5,1.5])
    if ar is not None:
        layout_dict.setdefault(f"yaxis{ar}", {}).update(title_text="加速度")
    # Merge yaxis updates from _add_* functions (e.g. PnL ticksuffix)
    for k, v in _layout_updates.items():
        layout_dict.setdefault(k, {}).update(v)

    # 5. ONE-SHOT Figure construction — NO Python Trace objects created
    fig = go.Figure(data=all_traces, layout=layout_dict)
    _render_plotly(fig, height=fh + 30, dates=dates)


# =====================================================================
@st.cache_resource
def _cached_conn():
    """Cached SQLite connection (reused across reruns, avoids new conn+PRAGMA each query)."""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


@st.cache_resource
def _get_db_connection() -> sqlite3.Connection:
    """Get cached database connection, with schema init on first access."""
    logger.debug("Initializing database connection (cache miss)")
    init_db()
    return _cached_conn()


# =====================================================================
# Sidebar sections — extracted from main() for readability
# =====================================================================


def _handle_pending_apply() -> None:
    """Apply pending preset params from session_state."""
    if AppState.has("_pending_apply_params"):
        params = AppState.pop("_pending_apply_params")
        if params is None:
            return
        for k, v in params.items():
            AppState.set(k, v)
        AppState.set("_import_data", "preset")


def _render_config_import() -> None:
    """Render config file uploader and handle import."""
    uploaded = st.sidebar.file_uploader("导入配置", type=["json"], key="config_import",
                                         label_visibility="collapsed")
    if uploaded is not None:
        raw = uploaded.read()
        file_hash = hashlib.md5(raw).hexdigest()
        if AppState.get("_import_data") != file_hash:
            try:
                config = json.loads(raw)
                for k, v in config.items():
                    AppState.set(k, v)
                AppState.set("_import_data", file_hash)
                logger.info(f"Config imported: {len(config)} keys")
                st.sidebar.success("配置已加载")
            except Exception as e:
                logger.error(f"Config import failed: {e}", exc_info=True)
                st.sidebar.error(f"导入失败: {e}")


def _render_market_ticker() -> tuple:
    """Render market radio + ticker input. Returns (market, ticker_code)."""
    market = st.sidebar.radio("市场", ["美股 US", "A股(沪深)", "港股 HK"],
                               horizontal=True, key="market")
    c1, c2 = st.sidebar.columns([1, 1])
    with c1:
        ticker_code = st.text_input("股票代码", value="AAPL", key="ticker").strip()
    with c2:
        if ticker_code:
            @st.cache_data(show_spinner=False, ttl=3600)
            def _stock_name(mkt, code) -> str:
                if not code or not code.strip():
                    return ""
                try:
                    if mkt == "A股(沪深)":
                        full = code + (".SS" if code[0] == "6" else ".SZ")
                    elif mkt == "港股 HK":
                        full = code.zfill(4) + ".HK"
                    else:
                        full = code.upper()
                    return yf.Ticker(full).info.get("longName") or ""
                except Exception as e:
                    logger.debug(f"Stock name lookup failed for {full}: {e}")
                    return ""
            name = _stock_name(market, ticker_code)
            if name:
                st.caption(f"📌 {name}")
    return market, ticker_code


def _handle_initial_fetch(market, ticker_code) -> None:
    """Auto-fetch all timeframes on first load."""
    if not AppState.has("_fetched_ticker"):
        AppState.set("_fetched_ticker", "")
    if ticker_code and ticker_code != AppState.get("_fetched_ticker"):
        if not has_data(ticker_code):
            with st.spinner(f"首次获取 {ticker_code} 全部周期数据..."):
                results = _fetch_all_timeframes(market, ticker_code)
                ok = sum(1 for ok, _ in results.values() if ok)
                logger.info(f"Initial fetch: {ticker_code} — {ok}/8 timeframes loaded")
                if ok > 0:
                    st.sidebar.success(f"已获取 {ok}/8 个周期")
        AppState.set("_fetched_ticker", ticker_code)


def _render_refresh_row(market, ticker_code) -> tuple:
    """Render refresh button + auto-refresh checkbox. Returns (auto_refresh, interval)."""
    c_refresh, c_auto = st.sidebar.columns([1, 1.2])
    auto_refresh = False
    interval = 60
    with c_refresh:
        if st.button("刷新数据", use_container_width=True):
            _cached_fetch_stock.clear()
            with st.spinner("正在获取全部周期..."):
                results = _fetch_all_timeframes(market, ticker_code)
            ok = sum(1 for r_ok, _ in results.values() if r_ok)
            fail = sum(1 for r_ok, _ in results.values() if not r_ok)
            if ok > 0:
                st.sidebar.success(f"✅ 获取成功 {ok}/8 个周期")
            if fail > 0:
                for tf, (r_ok, detail) in results.items():
                    if not r_ok:
                        st.sidebar.warning(f"❌ {tf}: {detail}")
    with c_auto:
        auto_refresh = st.checkbox("自动刷新", value=False, key="auto_refresh")
    if auto_refresh:
        interval = st.sidebar.slider("刷新间隔(秒)", 10, 600, 60, 10, key="refresh_interval")
    return auto_refresh, interval


def _render_preset_selector(market, ticker_code) -> None:
    """Render preset selector, action buttons, and confirmation flows."""
    st.sidebar.markdown("---")
    search_query = st.sidebar.text_input("🔍 搜索配置", key="preset_search",
                                          placeholder="输入股票代码或名称…")
    all_presets = list_presets()
    if search_query.strip():
        q = search_query.strip().lower()
        presets = [p for p in all_presets if
                   q in p["name"].lower() or
                   q in p.get("description", "").lower()]
    else:
        presets = all_presets
    preset_labels = ["(不选择)"] + [p["name"] for p in presets]
    preset_map = {p["name"]: p for p in presets}

    _hash = hashlib.md5("|".join(preset_labels).encode()).hexdigest()[:8]
    selected_label = st.sidebar.selectbox("📋 配置方案", preset_labels,
                                          key=f"preset_sel_{_hash}")
    selected_preset = preset_map.get(selected_label)

    if selected_preset is None:
        return  # 用户尚未选择预设，不渲染任何操作

    if selected_preset:
        p = selected_preset
        st.sidebar.caption(f"💡 {p['description']}")
        c1, c2, c3, c4 = st.sidebar.columns([1.2, 1, 1, 0.8])
        with c1:
            if st.button("✅ 应用", key="apply_preset", use_container_width=True):
                params = apply_preset(p["preset_id"])
                if params:
                    logger.info(f"Preset applied: {p['name']} ({len(params)} params)")
                    AppState.set("_pending_apply_params", params)
                    st.toast(f"已应用: {p['name']}")
                    st.rerun()
        with c2:
            if st.button("📝 更新", key="update_preset_btn", use_container_width=True):
                AppState.set("_preset_action", "update")
                AppState.set("_preset_action_id", p["preset_id"])
        with c3:
            if st.button("✏️ 重命名", key="rename_preset_btn", use_container_width=True):
                AppState.set("_preset_action", "rename")
                AppState.set("_preset_action_id", p["preset_id"])
        with c4:
            if st.button("🗑️ 删除", key="delete_preset_btn", use_container_width=True):
                AppState.set("_preset_action", "delete")
                AppState.set("_preset_action_id", p["preset_id"])

    _action = AppState.get("_preset_action")
    _action_id = AppState.get("_preset_action_id")
    if _action and _action_id is not None:
        target = next((p for p in presets if p["preset_id"] == _action_id), None)
        if target is None:
            AppState.pop("_preset_action")
            AppState.pop("_preset_action_id")
            st.rerun()
        elif _action == "update":
            st.sidebar.warning(f"将当前参数覆盖到 **{target['name']}**？")
            st.sidebar.caption("这会将当前所有参数写入该预设。")
            cc1, cc2 = st.sidebar.columns(2)
            with cc1:
                if st.button("确认覆盖", key="update_confirm_btn", use_container_width=True):
                    from config_db import collect_current_params
                    import json as _json
                    params = collect_current_params()
                    save_preset(target["name"],
                                _json.dumps(params, ensure_ascii=False),
                                description=target.get("description", ""),
                                category=target.get("category", "通用"))
                    st.toast(f"已更新: {target['name']}")
                    AppState.pop("_preset_action")
                    AppState.pop("_preset_action_id")
                    st.rerun()
            with cc2:
                if st.button("取消", key="update_cancel_btn", use_container_width=True):
                    AppState.pop("_preset_action")
                    AppState.pop("_preset_action_id")
                    st.rerun()
        elif _action == "rename":
            st.sidebar.caption(f"重命名 **{target['name']}**")
            new_name = st.sidebar.text_input("新名称", value=target["name"],
                                             key="rename_input_val")
            cc1, cc2 = st.sidebar.columns(2)
            with cc1:
                if st.button("确认重命名", key="rename_confirm_btn", use_container_width=True):
                    if new_name.strip() and new_name.strip() != target["name"]:
                        rename_preset(target["preset_id"], new_name.strip())
                        st.toast(f"已重命名: {target['name']} → {new_name.strip()}")
                        AppState.pop("_preset_action")
                        AppState.pop("_preset_action_id")
                        st.rerun()
                    elif new_name.strip() == target["name"]:
                        st.warning("名称未变化")
                    else:
                        st.error("名称不能为空")
            with cc2:
                if st.button("取消", key="rename_cancel_btn", use_container_width=True):
                    AppState.pop("_preset_action")
                    AppState.pop("_preset_action_id")
                    st.rerun()
        elif _action == "delete":
            st.sidebar.error(f"确认删除 **{target['name']}**？此操作不可恢复。")
            cc1, cc2 = st.sidebar.columns(2)
            with cc1:
                if st.button("确认删除", key="delete_confirm_btn", use_container_width=True):
                    delete_preset(target["preset_id"])
                    st.toast(f"已删除: {target['name']}")
                    AppState.pop("_preset_action")
                    AppState.pop("_preset_action_id")
                    st.rerun()
            with cc2:
                if st.button("取消", key="delete_cancel_btn", use_container_width=True):
                    AppState.pop("_preset_action")
                    AppState.pop("_preset_action_id")
                    st.rerun()

    # Save as preset
    with st.sidebar.expander("💾 保存 / 另存为预设", expanded=False):
        if not AppState.has("_last_sel_name"):
            AppState.set("_last_sel_name", "")
        curr_sel_name = selected_preset["name"] if selected_preset else ""
        if AppState.get("_last_sel_name") != curr_sel_name:
            AppState.set("new_preset_name", (curr_sel_name + "_副本" if curr_sel_name else ""))
            AppState.set("_last_sel_name", curr_sel_name)
        new_name = st.text_input("预设名称", key="new_preset_name", placeholder="如: 我的港股配置")
        new_desc = st.text_input("描述(可选)", key="new_preset_desc", placeholder="港股·短线·savgol")
        if AppState.pop("_pending_reset_overwrite", False):
            AppState.set("overwrite_preset", False)
        overwrite = False
        if selected_preset:
            overwrite = st.checkbox(f"覆盖「{selected_preset['name']}」", key="overwrite_preset")
        if st.button("💾 保存", key="save_preset_btn", use_container_width=True):
            if new_name.strip():
                from config_db import collect_current_params
                import json as _json
                params = collect_current_params()
                target_name = (selected_preset["name"] if overwrite and selected_preset else new_name.strip())
                cat = (selected_preset.get("category", "通用") if overwrite and selected_preset else "通用")
                logger.info(f"Preset saved: {target_name} (overwrite={overwrite})")
                save_preset(target_name,
                            _json.dumps(params, ensure_ascii=False),
                            description=(new_desc.strip() if not overwrite else selected_preset.get("description", "")),
                            category=cat)
                st.toast(f"已保存: {target_name}")
                AppState.set("_pending_reset_overwrite", True)
                st.rerun()
            else:
                st.error("请输入预设名称")


def _render_health_check(ticker_code) -> None:
    """Render data health check expander section."""
    with st.sidebar.expander("🩺 数据健康检查", expanded=False):
        if st.button("运行检查", key="health_btn", use_container_width=True) and ticker_code:
            with st.spinner("检查数据完整性..."):
                logger.debug(f"Running data health check for {ticker_code}")
                report = check_data_health(ticker_code)
            status = report.get("status", "ok")
            color_map = {"ok": "green", "warn": "orange", "error": "red"}
            color = color_map.get(status, "gray")
            st.markdown(f"**:{color}[状态: {report.get('summary', status)}]**")
            if report.get("issues"):
                for issue in report["issues"]:
                    st.warning(issue)
            if report.get("details"):
                detail_df = pd.DataFrame(report["details"])
                st.dataframe(detail_df, use_container_width=True, hide_index=True,
                             height=min(35 * len(detail_df) + 38, 300))


def _render_data_validation(market, ticker_code) -> None:
    """Render DB vs source data validation expander section."""
    with st.sidebar.expander("📋 数据校验", expanded=False):
        st.caption("对比数据库与 yfinance 全部周期，发现历史数据修正")
        if st.button("校验全部周期", key="val_btn", use_container_width=True) and ticker_code:
            logger.debug(f"Validating all timeframes for {ticker_code}")
            if market == "A股(沪深)":
                if not ticker_code.strip():
                    st.warning("股票代码不能为空")
                    return
                full_code = ticker_code + (".SS" if ticker_code[0] == "6" else ".SZ")
            elif market == "港股 HK":
                full_code = ticker_code.zfill(4) + ".HK"
            else:
                full_code = ticker_code.upper()

            rows = []
            has_conflict = False
            has_update = False
            TF_INTERVAL = {"1分钟": ("1m", "7d"), "5分钟": ("5m", "60d"), "15分钟": ("15m", "60d"),
                           "60分钟": ("1h", "730d"), "日线": ("1d", "max"), "周线": ("1wk", "max"),
                           "月线": ("1mo", "max"), "季线": ("3mo", "max")}
            for tf in ALL_TFS:
                interval, period = TF_INTERVAL[tf]
                with st.spinner(f"校验 {tf} ..."):
                    try:
                        data = yf.download(full_code, period=period, interval=interval, progress=False)
                        if data.empty or len(data[data["Close"].notna()]) < 5:
                            rows.append({"周期": tf, "DB": "-", "yf": "-", "重叠": "-",
                                         "指纹": "⚠️ 数据不足", "仅DB": "-", "仅yf": "-", "操作": ""})
                            continue
                        data = data[data["Close"].notna()]
                        report = compare_with_db(ticker_code, tf, data)
                    except Exception as e:
                        rows.append({"周期": tf, "DB": "-", "yf": "-", "重叠": "-",
                                     "指纹": f"❌ {str(e)[:30]}", "仅DB": "-", "仅yf": "-", "操作": ""})
                        continue
                    db_c = report["db_count"]
                    yf_c = report["yf_count"]
                    fp = "✅" if report["fingerprint_match"] else "❌"
                    status = report["status"]
                    if status == "conflict":
                        has_conflict = True
                    elif status == "update_available":
                        has_update = True
                    rows.append({
                        "周期": tf, "DB": db_c, "yf": yf_c,
                        "重叠": report["overlap_count"], "指纹": fp,
                        "仅DB": report["only_db"], "仅yf": report["only_yf"], "操作": status,
                    })

            if rows:
                import pandas as _pd
                df = _pd.DataFrame(rows)

                def _row_style(r):
                    s = r["差异"]
                    if s == "conflict":
                        return ["background-color: #fff3cd"] * len(r)
                    elif s == "update_available":
                        return ["background-color: #d4edda"] * len(r)
                    return [""] * len(r)

                df_display = df.rename(columns={"操作": "差异"})
                df_display["差异"] = df_display["差异"].replace({
                    "conflict": "⚠️ 数据冲突", "update_available": "有新数据", "ok": "✅ 一致",
                })
                styled = df_display.style.apply(_row_style, axis=1)
                st.dataframe(styled, use_container_width=True, hide_index=True,
                             height=min(35 * len(df) + 38, 350))
                st.caption("🟡 黄底 = 历史数据被修正 | 🟢 绿底 = 有新增数据")
                if has_conflict or has_update:
                    if st.button("⚠️ 更新全部有差异的周期", key="force_update_all", use_container_width=True):
                        updated = 0
                        for _, r in df.iterrows():
                            s = r["操作"]
                            if s in ("conflict", "update_available"):
                                tf = r["周期"]
                                try:
                                    interval, period = TF_INTERVAL[tf]
                                    data = yf.download(full_code, period=period, interval=interval, progress=False)
                                    if not data.empty:
                                        data = data[data["Close"].notna()]
                                        force_update_kline(ticker_code, tf, data)
                                        updated += 1
                                except Exception as e:
                                    logger.warning(f"Force update {tf} failed: {e}")
                        if updated > 0:
                            _cached_fetch_stock.clear()
                            clear_display_cache()
                            st.success(f"已更新 {updated} 个周期，页面将刷新")
                            time.sleep(0.5)
                            st.rerun()
                        else:
                            st.warning("没有周期被更新")


def _render_filter_selectors() -> tuple:
    """Render filter selector widgets. Returns (filter_id, dual, filter_id2)."""
    filter_id = st.sidebar.selectbox("滤波器", list(FILTERS.keys()),
        format_func=lambda x: FILTERS.get(x, {}).get("name", x), key="global_f")
    dual = st.sidebar.checkbox("双滤波对比", value=False, key="global_dual")
    filter_id2 = None
    if dual:
        filter_id2 = st.sidebar.selectbox("滤波器 2", list(FILTERS.keys()),
            format_func=lambda x: FILTERS.get(x, {}).get("name", x), key="global_f2")
    return filter_id, dual, filter_id2


def _render_operating_tf_selector() -> str:
    """Render operating timeframe selector for BS markers.
    Positioned next to stock code. Options based on current view timeframes."""
    # Collect TFs from the 4 views (use defaults for first load)
    view_tfs = []
    for i in range(4):
        tf = st.session_state.get(f"v{i}_tf", DEFAULT_TFS[i])
        if tf not in view_tfs:
            view_tfs.append(tf)
    # Sort by hierarchy (high→low)
    view_tfs.sort(key=lambda x: ALL_TFS.index(x), reverse=True)

    operating_tf = st.sidebar.selectbox(
        "🎯 操作周期",
        view_tfs,
        index=0,  # default to highest TF
        key="operating_tf",
        help="仅此周期及更低周期显示 BS 买卖标记",
    )
    return operating_tf


def _render_param_panels(filter_id, dual, filter_id2) -> list:
    """Render 2x2 parameter panels. Returns list of config dicts."""
    configs = []
    for row_idx in range(2):
        c1, c2 = st.columns(2)
        for col_idx, col in enumerate([c1, c2]):
            i = row_idx * 2 + col_idx
            with col:
                tf_label = st.session_state.get(f"v{i}_tf", DEFAULT_TFS[i])
                st.caption(f"视图{i + 1} · {tf_label}")
                cfg = _render_params(f"v{i}", filter_id, dual, filter_id2, DEFAULT_TFS[i])
                configs.append(cfg)
    return configs




def _get_bar_date_from_db(ticker_code, tf, bar_index):
    """Query the date string for a given bar index from the database.

    Parameters
    ----------
    ticker_code : str
        Ticker symbol to query.
    tf : str
        Timeframe label (e.g. ``"日线"``).
    bar_index : int
        Zero-based row offset (``OFFSET`` in the SQL query).

    Returns
    -------
    str
        Date string from ``kline.ts`` for the bar at the requested
        offset, or an empty string if no row is found.
    """
    from db import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT ts FROM kline WHERE ticker=? AND timeframe=? ORDER BY ts ASC LIMIT 1 OFFSET ?",
            (ticker_code, tf, bar_index),
        ).fetchone()
    return row[0] if row else ""


def _update_cutoff_and_rerun():
    """Update ``cutoff_date`` from the current bar index and trigger a rerun.

    Called when the user clicks a backtest navigation button (⏮ ◀ ▶ ⏭).
    Reads ``_bar_index`` from ``st.session_state``, looks up the
    corresponding date in the DB for the minimum timeframe, writes it
    to ``AppState._bt_cutoff_date``, and calls ``st.rerun()``.

    Parameters
    ----------
    None — reads from ``st.session_state`` and ``AppState``.

    Returns
    -------
    None
    """
    ticker = AppState.get("_fetched_ticker", "")
    min_tf = AppState.get("_min_tf", "")
    bar_index = st.session_state.get("_bar_index", 0)
    # 同步 widget key，确保 slider 手柄跟随导航按钮移动
    st.session_state._bt_slider_pos = bar_index
    if min_tf and ticker:
        cutoff_date = _get_bar_date_from_db(ticker, min_tf, bar_index - 1)
        if cutoff_date:
            AppState.set("_bt_cutoff_date", cutoff_date)
    st.rerun()


def _on_slider_change():
    """Synchronise slider position to bar index and update cutoff date.

    Streamlit ``on_change`` callback for the backtest slider widget.
    Reads ``_bt_slider_pos`` from ``st.session_state``, writes it to
    ``_bar_index``, and queries the DB for the corresponding cutoff date.

    Skipped during auto-play (``_is_playing`` is ``True``) because
    ``_run_backtest_play`` handles the progression independently.

    Parameters
    ----------
    None — reads from ``st.session_state`` and ``AppState``.

    Returns
    -------
    None
    """
    if AppState.get("_is_playing", False):
        return  # 播放中，避免 on_change 自动 rerun 干扰播放循环
    # 从 Slider Widget Key 读取当前值，同步到程序状态
    slider_val = st.session_state.get("_bt_slider_pos", 0)
    st.session_state._bar_index = slider_val
    ticker = AppState.get("_fetched_ticker", "")
    min_tf = AppState.get("_min_tf", "")
    if min_tf and ticker:
        cutoff_date = _get_bar_date_from_db(ticker, min_tf, slider_val - 1)
        if cutoff_date:
            AppState.set("_bt_cutoff_date", cutoff_date)


def _render_backtest_nav(total_bars, min_n_pts):
    """渲染回测导航按钮：⏮ ◀ ▶/⏸ ⏵ ⏭ + 速度"""
    bar_index = st.session_state.get("_bar_index", total_bars)
    is_playing = AppState.get("_is_playing", False)

    col_nav = st.sidebar.columns([1, 1, 1, 1, 1, 2])

    with col_nav[0]:
        if st.button("⏮", key="_bt_goto_start", use_container_width=True,
                     help=f"跳到开头 (bar {min_n_pts})"):
            st.session_state._bar_index = min_n_pts
            _update_cutoff_and_rerun()

    with col_nav[1]:
        if st.button("◀", key="_bt_step_back", use_container_width=True,
                     disabled=bar_index <= min_n_pts, help="后退一个 bar"):
            st.session_state._bar_index = max(min_n_pts, bar_index - 1)
            _update_cutoff_and_rerun()

    with col_nav[2]:
        label = "⏸" if is_playing else "▶"
        help_text = "暂停" if is_playing else "播放"
        if st.button(label, key="_bt_toggle_play", use_container_width=True, help=help_text):
            if is_playing:
                AppState.set("_is_playing", False)
                st.rerun()
            else:
                # 如果已到末尾，从头开始播放
                if bar_index >= total_bars:
                    st.session_state._bar_index = min_n_pts
                # ★ 在任何位置开始播放时，都预更新 cutoff_date
                ticker_v = AppState.get("_fetched_ticker", "")
                min_tf_v = AppState.get("_min_tf", "")
                if min_tf_v and ticker_v:
                    cur = st.session_state.get("_bar_index", total_bars)
                    d = _get_bar_date_from_db(ticker_v, min_tf_v, cur - 1)
                    if d:
                        AppState.set("_bt_cutoff_date", d)
                AppState.set("_is_playing", True)
                st.rerun()

    with col_nav[3]:
        if st.button("⏵", key="_bt_step_fwd", use_container_width=True,
                     disabled=bar_index >= total_bars, help="前进一个 bar"):
            st.session_state._bar_index = min(total_bars, bar_index + 1)
            _update_cutoff_and_rerun()

    with col_nav[4]:
        if st.button("⏭", key="_bt_goto_end", use_container_width=True,
                     help=f"跳到末尾 (bar {total_bars})"):
            st.session_state._bar_index = total_bars
            _update_cutoff_and_rerun()

    with col_nav[5]:
        speeds = ["0.25x", "0.5x", "1x", "2x", "5x", "10x"]
        speed_map = {"0.25x": 0.25, "0.5x": 0.5, "1x": 1.0, "2x": 2.0, "5x": 5.0, "10x": 10.0}
        current = AppState.get("_play_speed_label", "1x")
        idx = speeds.index(current) if current in speeds else 2
        new_speed = st.selectbox("速度", speeds, index=idx, key="_bt_speed", label_visibility="collapsed")
        if new_speed != current:
            AppState.set("_play_speed_label", new_speed)
            AppState.set("_play_speed", speed_map[new_speed])


def _run_backtest_play():
    """回测自动播放 — 只更新窗口位置，不调用 rerun。

    返回 True 表示需要 main() 末尾 sleep + rerun 触发下一步。
    播放按钮只需设置 _is_playing=True 再 st.rerun()，本函数自动处理前进。
    """
    if not AppState.get("_is_playing", False):
        return False

    # 安全守卫：不在回测模式或 ticker 未就绪时停止播放
    if not AppState.get("_cb_mode", False):
        AppState.set("_is_playing", False)
        return False

    bar_index = st.session_state.get("_bar_index", 0)
    total = AppState.get("_min_tf_bar_count", 0)

    # 安全守卫：bar 总数异常时停止播放
    if total <= 0:
        logger.debug(f"回测播放停止: total={total}, 数据未就绪")
        AppState.set("_is_playing", False)
        return False

    if bar_index >= total:
        logger.debug(f"回测播放停止: bar_index={bar_index} >= total={total}")
        AppState.set("_is_playing", False)
        return False

    # 前进一个 bar（在 slider widget 渲染之前，Streamlit 允许修改 widget key）
    st.session_state._bar_index = bar_index + 1

    # 同步更新 cutoff_date
    ticker = AppState.get("_fetched_ticker", "")
    min_tf = AppState.get("_min_tf", "")
    if min_tf and ticker:
        cutoff_date = _get_bar_date_from_db(ticker, min_tf, bar_index)
        if cutoff_date:
            AppState.set("_bt_cutoff_date", cutoff_date)

    return True


def _render_backtest_mode(market, ticker_code, configs) -> None:
    """Render the backtest mode toggle and window-position slider in the sidebar.

    Provides a radio button to switch between browse and backtest modes.
    When switching to backtest mode it:
    - Determines the minimum timeframe from the active configs.
    - Queries the total bar count from the DB (or loads a cached config).
    - Initialises ``_bar_index`` and ``_bt_cutoff_date``.
    When switching back to browse mode it clears all backtest-related
    state.

    While in backtest mode it renders navigation buttons (via
    ``_render_backtest_nav``) and a slider (or progress bar during
    auto-play) for the window end position.

    Parameters
    ----------
    market : str
        Market identifier (``"美股 US"``, ``"A股(沪深)"``, ``"港股 HK"``).
    ticker_code : str
        Ticker symbol, e.g. ``"AAPL"``.
    configs : list of dict
        List of per-view configuration dicts, each containing at least
        ``"tf"`` and ``"n_pts"`` keys.

    Returns
    -------
    None
    """
    st.sidebar.markdown("---")
    st.sidebar.caption("🔬 回测模式")

    mode_options = ["浏览模式", "回测模式"]
    mode_index = 1 if AppState.get("_cb_mode", False) else 0
    selected = st.sidebar.radio("模式", mode_options, horizontal=True,
                                 index=mode_index, key="_bt_mode_radio")

    new_cb_mode = (selected == "回测模式")
    old_cb_mode = AppState.get("_cb_mode", False)

    if new_cb_mode != old_cb_mode:
        if new_cb_mode:
            # 切换到回测模式：先尝试从缓存加载配置
            cached = _load_backtest_config(ticker_code)
            if cached:
                min_tf = cached["min_tf"]
                bar_count = cached["bar_count"]
            else:
                # 缓存未命中，从 DB 查询
                min_tf, bar_count = _get_min_tf_and_count(configs, ticker_code)
            AppState.set("_min_tf", min_tf)
            AppState.set("_min_tf_bar_count", bar_count)
            # bar_index = 窗口结束位置，默认在末尾
            AppState.set("_bar_index", bar_count)
            st.session_state._bar_index = bar_count
            # 取 min_tf 视图中最小的 n_pts 作为 slider 范围下限
            min_n_pts = min((cfg["n_pts"] for cfg in configs if cfg["tf"] == min_tf), default=120)
            if not cached:
                _save_backtest_config(ticker_code, min_tf, bar_count, min_n_pts)
            # 初始化 cutoff_date：从 DB 查询 bar_index 位置（最后一条 bar）的日期
            if bar_count > 0 and min_tf:
                cutoff_date = _get_bar_date_from_db(ticker_code, min_tf, bar_count - 1)
                if cutoff_date:
                    AppState.set("_bt_cutoff_date", cutoff_date)
            if bar_count > 0:
                st.toast(f"回测模式已启用  最小周期: {min_tf} ({bar_count} bars)")
            else:
                st.toast("回测模式已启用  数据未就绪")
            try:
                log_mode_switch(ticker_code, "enter", min_tf, bar_count)
            except Exception as e:
                logger.debug(f"回测日志写入失败: {e}")
        else:
            # 切换回浏览模式：清除回测状态
            # 捕获退出前的回测参数（必须在清除前读取）
            _exit_min_tf = AppState.get("_min_tf", "")
            _exit_bar_count = AppState.get("_min_tf_bar_count", 0)
            AppState.set("_bar_index", 0)
            st.session_state._bar_index = 0
            AppState.set("_bt_cutoff_date", "")
            AppState.set("_min_tf", "")
            AppState.set("_min_tf_bar_count", 0)
            try:
                log_mode_switch(ticker_code, "exit", _exit_min_tf, _exit_bar_count)
            except Exception as e:
                logger.debug(f"回测日志写入失败: {e}")

        AppState.set("_cb_mode", new_cb_mode)
        st.rerun()

    # 回测模式下显示窗口位置信息
    if AppState.get("_cb_mode", False):
        bar_index = st.session_state.get("_bar_index", 0)
        total_bars = AppState.get("_min_tf_bar_count", 0)
        min_tf = AppState.get("_min_tf", "")
        # 取 min_tf 视图中最小的 n_pts 作为 slider 范围下限
        min_n_pts = min((cfg["n_pts"] for cfg in configs if cfg["tf"] == min_tf), default=120)

        if total_bars > 0:
            # ── 导航按钮 ──
            _render_backtest_nav(total_bars, min_n_pts)

            # 显示当前窗口信息
            win_start_display = max(1, bar_index - min_n_pts + 1)
            st.sidebar.caption(f"📊 显示 bar {win_start_display} ~ {bar_index} / {total_bars}")

            # 窗口位置进度 indicator
            # 播放期间用进度条替代 Slider（避免 Widget Key 与 _run_backtest_play 的递增冲突）
            if AppState.get("_is_playing", False):
                progress = (bar_index - min_n_pts) / max(1, total_bars - min_n_pts)
                st.sidebar.progress(progress, text=f"播放中... {bar_index}/{total_bars}")
            else:
                # 非播放时：Slider 用独立 key，on_change 同步到 _bar_index
                # 确保 _bar_index 在 slider 有效范围内（防止退出回测后残留 0 值）
                if st.session_state.get("_bar_index", 0) < min_n_pts:
                    st.session_state._bar_index = total_bars
                st.sidebar.slider(
                    "窗口结束位置", min_n_pts, total_bars,
                    value=bar_index,
                    key="_bt_slider_pos",
                    on_change=_on_slider_change,
                )
        else:
            st.sidebar.warning("回测数据未就绪，请先在浏览模式加载数据")


def _render_db_backup() -> None:
    """Render DB backup/restore expander section."""
    with st.sidebar.expander("💾 数据备份与恢复", expanded=False):
        db_size = get_db_size_mb()
        logger.debug(f"DB status: {DB_PATH.name} ({db_size:.1f} MB)")
        st.caption(f"数据库: {DB_PATH.name} ({db_size:.1f} MB)")
        c_s1, c_s2 = st.columns([1, 1])
        with c_s1:
            if st.button("创建备份", key="snap_btn", use_container_width=True):
                try:
                    path = snapshot_db()
                    prune_snapshots(max_keep=5)
                    logger.info(f"Snapshot created: {Path(path).name}")
                    st.success(f"已创建: {Path(path).name}")
                except Exception as e:
                    logger.error(f"Snapshot failed: {e}", exc_info=True)
                    st.error(f"备份失败: {e}")
        snapshots = list_snapshots()
        with c_s2:
            snap_count = len(snapshots)
            st.caption(f"共 {snap_count} 个备份" if snap_count else "暂无备份")
        if snapshots:
            snap_labels = [s[3] for s in snapshots]
            selected_idx = st.selectbox("选择备份", range(len(snap_labels)),
                                        format_func=lambda i: snap_labels[i], key="restore_select")
            c_r1, c_r2 = st.columns([1, 1])
            with c_r1:
                if st.button("恢复到此备份", key="restore_btn", use_container_width=True):
                    try:
                        restore_snapshot(snapshots[selected_idx][0])
                        _cached_fetch_stock.clear()
                        clear_display_cache()
                        AppState.set("_fetched_ticker", "")
                        logger.info(f"Snapshot restored: {snap_labels[selected_idx]}")
                        st.success("已恢复，页面将刷新")
                        time.sleep(0.5)
                        st.rerun()
                    except Exception as e:
                        logger.error(f"Snapshot restore failed: {e}", exc_info=True)
                        st.error(f"恢复失败: {e}")
            with c_r2:
                if st.button("删除此备份", key="del_snap_btn", use_container_width=True):
                    try:
                        os.remove(snapshots[selected_idx][0])
                        logger.info(f"Snapshot deleted: {snap_labels[selected_idx]}")
                        st.rerun()
                    except Exception as e:
                        logger.warning(f"Snapshot deletion failed: {e}")
                        st.error(f"删除失败: {e}")


def _view_export_params(cfg, i) -> dict:
    """按单一真源 VIEW_PARAM_SPECS 从单个视图 cfg 构建导出键值。

    export(JSON) 与测试共用此函数，参数增删只跟随 VIEW_PARAM_SPECS，不会漏。
    """
    return {f"v{i}_{suffix}": cfg.get(cfg_key, default)
            for suffix, cfg_key, default in VIEW_PARAM_SPECS}


def _render_export_config(configs, filter_id, filter_id2, dual, market, ticker_code) -> None:
    """Render config export download button."""
    st.sidebar.markdown("---")
    export_data = {
        "market": market, "ticker": ticker_code,
        "global_f": filter_id, "global_dual": dual, "global_f2": filter_id2,
    }
    for i, cfg in enumerate(configs):
        export_data.update(_view_export_params(cfg, i))
        f1 = FILTERS.get(filter_id, {})
        for pname, pval in cfg.get("pv", {}).items():
            label = f1["params"].get(pname, (pname,))[0]
            export_data[f"{label}_v{i}_f1_{filter_id}"] = pval
        f2 = FILTERS.get(filter_id2, {}) if filter_id2 else {}
        for pname, pval in cfg.get("pv2", {}).items():
            label = f2["params"].get(pname, (pname,))[0]
            export_data[f"{label}_v{i}_f2_{filter_id2}"] = pval
    st.sidebar.download_button("导出配置", json.dumps(export_data, ensure_ascii=False, indent=2),
        file_name="filter_config.json", mime="application/json",
        use_container_width=True)


def _render_config_history(ticker_code) -> None:
    """Render config history expander section."""
    if ticker_code:
        st.sidebar.markdown("---")
        with st.sidebar.expander("📜 配置历史", expanded=False):
            records = get_history(ticker_code, variant="single", limit=10)
            if records:
                for rec in records:
                    source_icon = {"ui": "✏️", "import": "📥", "preset_apply": "📋", "rollback": "↩️"}
                    icon = source_icon.get(rec.get("source", ""), "📌")
                    st.caption(f"{icon} {rec['changed_at']} — {rec.get('preset_name') or rec['source']}")
            else:
                st.caption("暂无记录")


def _render_db_import_export() -> None:
    """Render DB import/export expander section."""
    with st.sidebar.expander("📦 数据库导入/导出", expanded=False):
        st.caption("导出整个数据库到文件，可在其他设备导入")
        try:
            checkpoint_wal()
        except Exception as e:
            logger.debug(f"Checkpoint WAL failed (non-critical): {e}")
        try:
            db_bytes = DB_PATH.read_bytes()
            logger.debug(f"DB export: {len(db_bytes) / 1024 / 1024:.1f} MB")
            st.download_button("导出数据库", db_bytes, file_name="market.db",
                mime="application/octet-stream", use_container_width=True,
                help=f"文件大小: {len(db_bytes) / 1024 / 1024:.1f} MB")
        except Exception as e:
            logger.error(f"DB export failed: {e}", exc_info=True)
            st.error(f"导出失败: {e}")
        uploaded_db = st.file_uploader("导入数据库", type=["db"], key="db_import", label_visibility="collapsed")
        if uploaded_db is not None:
            raw = uploaded_db.read()
            file_hash = hashlib.md5(raw).hexdigest()
            if AppState.get("_db_import_hash") != file_hash:
                with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                    tmp.write(raw)
                    tmp_path = tmp.name
                valid, err_msg = validate_db(tmp_path)
                try:
                    os.remove(tmp_path)
                except Exception as e:
                    logger.debug(f"Temp file cleanup failed: {e}")
                if not valid:
                    st.error(f"无效的数据库文件: {err_msg}")
                else:
                    DB_PATH.write_bytes(raw)
                    for suffix in ["-wal", "-shm"]:
                        p = str(DB_PATH) + suffix
                        if os.path.exists(p):
                            os.remove(p)
                    _cached_fetch_stock.clear()
                    clear_display_cache()
                    AppState.set("_fetched_ticker", "")
                    AppState.set("_db_import_hash", file_hash)
                    logger.info(f"DB imported: {len(raw) / 1024 / 1024:.1f} MB")
                    st.success(f"数据库已导入 ({len(raw) / 1024 / 1024:.1f} MB)，页面将刷新")
                    time.sleep(0.5)
                    st.rerun()


def _run_auto_refresh(market, ticker_code, auto_refresh, interval) -> None:
    """Execute auto-refresh if enabled — sleep full interval, then refresh once.

    设计: time.sleep(interval) 阻塞等待，不产生中间 rerun。
    页面仅在 sleep 前渲染一次 caption，到周期后才 rerun 刷新数据。
    """
    if not auto_refresh:
        return

    st.caption(f"⏱️ {interval}s 后自动刷新")
    time.sleep(interval)

    logger.info(f"Auto-refresh triggered for {ticker_code} (interval={interval}s)")
    _cached_fetch_stock.clear()
    _fetch_all_timeframes(market, ticker_code)
    st.rerun()


# =====================================================================
def main() -> None:
    """Application entry point — orchestrates the full Streamlit page layout.

    Execution order:
    1. **Backtest auto-play** — advance the window position if playing
       (must happen before any widget rendering).
    2. **Initialise** — DB connection, config tables, session state,
       one-off JSON preset import.
    3. **Sidebar (top-down)** — config import, market/ticker, initial
       fetch, refresh, preset selector, health check, data validation,
       filter selectors, 2x2 parameter panels, time navigation,
       backtest mode toggle, DB backup/restore.
    4. **Charts (2x2 grid)** — sorted by timeframe descending; each
       cell renders a ``_render_chart_fragment``.  In backtest mode a
       cascading data sync (``_sync_all_cascading``) runs first.
    5. **Footer sidebar** — export config, config history, DB import/export.
    6. **Auto-refresh** — sleep and rerun if enabled.
    7. **Backtest play loop** — sleep + rerun for the next frame.

    Parameters
    ----------
    None — reads all state from ``st.session_state`` and ``AppState``.

    Returns
    -------
    None
    """
    # ── 回测自动播放：更新窗口位置（必须在 widget 渲染之前修改 key）──
    need_rerun = _run_backtest_play()
    logger.info("App started")
    _get_db_connection()
    init_config_tables()
    AppState.init_defaults()
    if not AppState.get("_config_initialized"):
        import_json_files_as_presets()
        AppState.set("_config_initialized", True)
    st.sidebar.title("多周期股票滤波分析")

    # ── Config import ──
    if not AppState.has("_import_data"):
        AppState.set("_import_data", None)
    _handle_pending_apply()
    _render_config_import()

    # ── Market ──
    market = st.sidebar.radio("市场", ["美股 US", "A股(沪深)", "港股 HK"],
                               horizontal=True, key="market")

    # ── Stock code + Operating TF on the same row ──
    c1, c2 = st.sidebar.columns([1, 1])
    with c1:
        ticker_code = st.text_input("股票代码", value="AAPL", key="ticker").strip()
    with c2:
        view_tfs = []
        for i in range(4):
            tf = st.session_state.get(f"v{i}_tf", DEFAULT_TFS[i])
            if tf not in view_tfs:
                view_tfs.append(tf)
        view_tfs.sort(key=lambda x: ALL_TFS.index(x), reverse=True)
        operating_tf = st.selectbox(
            "🎯 操作周期", view_tfs, index=0,
            key="operating_tf",
        )

    # ── Stock name lookup ──
    if ticker_code:
        @st.cache_data(show_spinner=False, ttl=3600)
        def _stock_name(mkt, code) -> str:
            if not code or not code.strip():
                return ""
            try:
                if mkt == "A股(沪深)":
                    full = code + (".SS" if code[0] == "6" else ".SZ")
                elif mkt == "港股 HK":
                    full = code.zfill(4) + ".HK"
                else:
                    full = code.upper()
                return yf.Ticker(full).info.get("longName") or ""
            except Exception as e:
                logger.debug(f"Stock name lookup failed for {full}: {e}")
                return ""
        name = _stock_name(market, ticker_code)
        if name:
            st.sidebar.caption(f"📌 {name}")

    # ── Initial fetch ──
    _handle_initial_fetch(market, ticker_code)

    # ── Refresh row ──
    auto_refresh, interval = _render_refresh_row(market, ticker_code)

    # ── Preset selector ──
    _render_preset_selector(market, ticker_code)

    st.sidebar.markdown("---")

    # ── Data health check ──
    _render_health_check(ticker_code)

    # ── Data validation ──
    _render_data_validation(market, ticker_code)

    # ── Filter selectors ──
    filter_id, dual, filter_id2 = _render_filter_selectors()

    # ── Pass 1: 2x2 parameter panels ──
    configs = _render_param_panels(filter_id, dual, filter_id2)

    # Compute visible lower TFs for BS marker cascade
    _view_tfs = set(cfg["tf"] for cfg in configs)
    _all_lower = get_lower_tfs(operating_tf)
    _visible_lower_tfs = [tf for tf in _all_lower if tf in _view_tfs]
    st.session_state["_bs_lower_tfs"] = _visible_lower_tfs

    # ticker 切换后，若处于回测模式则刷新回测状态（需在 _render_backtest_mode 前）
    _prev_bt_ticker = AppState.get("_bt_last_ticker", "")
    if AppState.get("_cb_mode", False) and ticker_code and ticker_code != _prev_bt_ticker:
        # 先尝试从缓存加载配置
        cached = _load_backtest_config(ticker_code)
        if cached:
            min_tf = cached["min_tf"]
            bar_count = cached["bar_count"]
        else:
            min_tf, bar_count = _get_min_tf_and_count(configs, ticker_code)
        if min_tf and bar_count > 0:
            AppState.set("_min_tf", min_tf)
            AppState.set("_min_tf_bar_count", bar_count)
            AppState.set("_bar_index", bar_count)
            st.session_state._bar_index = bar_count
            min_n_pts = min((cfg["n_pts"] for cfg in configs if cfg["tf"] == min_tf), default=120)
            if not cached:
                _save_backtest_config(ticker_code, min_tf, bar_count, min_n_pts)
            cutoff_date = _get_bar_date_from_db(ticker_code, min_tf, bar_count - 1)
            if cutoff_date:
                AppState.set("_bt_cutoff_date", cutoff_date)
        AppState.set("_bt_last_ticker", ticker_code)

    # ── Time window navigation ──

    # ── 回测模式切换 ──
    _render_backtest_mode(market, ticker_code, configs)

    # ── DB backup/restore ──
    _render_db_backup()

    # ── Pass 2: 2x2 chart views ──
    cb_mode = AppState.get("_cb_mode", False)
    if cb_mode:
        window_start = st.session_state.get("_bar_index", 0)
        cutoff_date = AppState.get("_bt_cutoff_date", "")
    else:
        window_start = None
        cutoff_date = None

    # ── 回测模式: 前置级联合成（一次性写入所有TF的parquet）──
    if cb_mode and ticker_code and cutoff_date:
        tfs_in_use = sorted(set(cfg["tf"] for cfg in configs),
                            key=lambda x: ALL_TFS.index(x))
        min_tf_val = AppState.get("_min_tf", "")
        if tfs_in_use and min_tf_val:
            # ★ P1-1: build per-TF n_pts dict from view configs
            tf_n_pts = {cfg["tf"]: cfg["n_pts"] for cfg in configs}
            # ★ P1-2: check return value, warn on partial failure
            bt_results = _sync_all_cascading(ticker_code, tfs_in_use, cutoff_date,
                                              min_tf_val, n_pts=tf_n_pts)
            failed_tfs = [tf for tf, ok in bt_results.items() if not ok]
            if failed_tfs:
                logger.warning(f"Backtest cascading failed for: {failed_tfs}")
            if len(failed_tfs) == len(tfs_in_use):
                st.sidebar.warning("回测数据加载失败，请检查数据库")

    grid_cols = []
    for row_idx in range(2):
        c1, c2 = st.columns(2)
        grid_cols.append((c1, c2))
    sorted_views = sorted(enumerate(configs),
                          key=lambda x: ALL_TFS.index(x[1]["tf"]), reverse=True)
    for orig_i, cfg in sorted_views:
        row_idx = orig_i // 2
        col_idx = orig_i % 2
        with grid_cols[row_idx][col_idx]:
            _render_chart_fragment(market, ticker_code, cfg, f"v{orig_i}", compact=True,
                                   window_start=window_start, cutoff_date=cutoff_date)

    # ── Export config ──
    _render_export_config(configs, filter_id, filter_id2, dual, market, ticker_code)

    # ── Config history ──
    _render_config_history(ticker_code)

    # ── DB import/export ──
    _render_db_import_export()

    # ── Auto-refresh ──
    _run_auto_refresh(market, ticker_code, auto_refresh, interval)

    # ── 回测自动播放：图表渲染完成后 sleep + rerun 触发下一步 ──
    if need_rerun:
        speed = AppState.get("_play_speed", 1.0)
        time.sleep(1.0 / speed)
        st.rerun()


if __name__ == "__main__":
    main()
