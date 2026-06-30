"""
多周期股票滤波分析工具 — 4视图独立配置, 施密特触发器 + 滤波对比

入口文件：页面布局 + session_state初始化 + st.fragment 包装
"""

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from loguru import logger
import streamlit as st
import numpy as np
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from config_db import (init_config_tables, list_presets, apply_preset,
                        save_preset, delete_preset, rename_preset,
                        get_history,
                        import_json_files_as_presets)
from db import (init_db, get_date_range, has_data,
                check_data_health, get_db_size_mb, snapshot_db, list_snapshots,
                restore_snapshot, prune_snapshots, clear_display_cache,
                checkpoint_wal, validate_db, compare_with_db, force_update_kline,
                DB_PATH)

# --- Import from new modules ---
from services.filter_engine import (
    FILTERS,
    _schmitt_trigger, _find_all_pairs,
    _fit_parabolic, _fit_physics_parabola,
    _compute_strategy_pnl, _align_pnl_to_current_tf, _compute_holding_masks,
)
from services.data_loader import (
    _fetch_all_timeframes, _fetch_stock, _sync_to_display,
)
from components.charts import (
    _render_plotly, _add_prediction_traces,
    _add_cross_pnl_subplot, _add_alignment_subplot,
)
from components.sidebar import (
    _render_params, ALL_TFS, DEFAULT_TFS, TF_HIERARCHY,
)
from state import AppState

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
    """Cached wrapper for yfinance data fetching. Clear cache to force refresh."""
    return _fetch_stock(market, code, tf, n_pts, force_period=force_period)


# =====================================================================
# 回测模式 — Phase 2: 窗口平移、多周期对齐、高周期合成、图表标注
# =====================================================================


def _truncate_arrays(t, noisy, ohlc, dates, bar_index):
    """回测模式：截断所有数组到 [:bar_index+1]。
    当 bar_index 为 None 时直接返回原数组（浏览模式）。
    返回 (t, noisy, ohlc, dates) —— 可能截断后的副本。

    注意：此函数保留以兼容测试。回测模式的渲染管线不再调用此函数，
    bar_index 仅用于视觉叠加（_add_backtest_overlay）。"""
    if bar_index is None:
        return t, noisy, ohlc, dates
    return t[:bar_index + 1], noisy[:bar_index + 1], ohlc.iloc[:bar_index + 1], dates[:bar_index + 1]


def _global_to_local_bar_index(dates, global_idx, min_tf_dates):
    """将全局 bar_index（最小周期刻度）映射为本视图的本地 bar_index。
    使用 np.searchsorted 实现 O(log n) 映射。
    取 dates 中 <= min_tf_dates[global_idx] 的最大索引。"""
    import numpy as np
    if global_idx >= len(min_tf_dates):
        return len(dates) - 1
    cutoff_time = min_tf_dates[global_idx]
    return int(np.searchsorted(dates, cutoff_time, side="right") - 1)


def _synthesize_higher_tf_bar(lower_ohlc_df, higher_tf_name):
    """从低周期 OHLC DataFrame 合成高周期最后一根 bar。
    合成规则：Open=首个Open, High=max(Highs), Low=min(Lows), Close=末个Close。
    仅当低周期数据量 >= 2 时合成，否则返回 None。
    返回 {"Open": float, "High": float, "Low": float, "Close": float} 或 None。"""
    if lower_ohlc_df is None or len(lower_ohlc_df) < 2:
        return None
    return {
        "Open": float(lower_ohlc_df["Open"].iloc[0]),
        "High": float(lower_ohlc_df["High"].max()),
        "Low": float(lower_ohlc_df["Low"].min()),
        "Close": float(lower_ohlc_df["Close"].iloc[-1]),
    }


def _add_backtest_overlay(fig, bar_index, total_bars, dates, tf):
    """在 Plotly Figure 上添加回测标注：
    1. 金色竖线 (line_color="gold", width=2.5) 标记 bar_index 位置
    2. 未来区域灰色半透明遮罩 (x0=bar_index 到 x1=total_bars-1)
    3. 右上角 "回测模式" 注解
    当 bar_index 为 None 时不做任何操作。"""
    if bar_index is None or total_bars == 0:
        return

    # 金色竖线标记当前 bar 位置
    fig.add_vline(
        x=bar_index,
        line_color="gold",
        line_width=2.5,
        layer="above",
    )

    # 未来区域灰色半透明遮罩
    fig.add_shape(
        type="rect",
        x0=bar_index,
        x1=total_bars - 1,
        y0=0,
        y1=1,
        xref="x",
        yref="paper",
        fillcolor="rgba(128, 128, 128, 0.25)",
        line=dict(width=0),
        layer="above",
    )

    # 右上角 "回测模式" 注解
    fig.add_annotation(
        x=total_bars - 1,
        y=1,
        xref="x",
        yref="paper",
        text="回测模式",
        showarrow=False,
        font=dict(size=11, color="gold"),
        xanchor="right",
        yanchor="top",
        opacity=0.7,
    )


# =====================================================================
# Chart rendering — sub-functions extracted from _render_chart
# =====================================================================


def _date_markers(dates, tf) -> tuple[list, list]:
    """Return (positions, labels) for vertical date markers."""
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


def _get_next_lower_tf(tf):
    """返回比 tf 更精细一级的周期。如 _get_next_lower_tf('日线') → '60分钟'"""
    from components.sidebar import ALL_TFS
    idx = ALL_TFS.index(tf)
    if idx > 0:
        return ALL_TFS[idx - 1]
    return None


def _load_chart_data(market, ticker_code, tf, day_offset, n_pts, bar_index=None) -> tuple:
    """Load chart data from display cache or fetch from API. Returns (t, noisy, ohlc, ticker_full, dates, err).

    bar_index 参数仅保留签名兼容性，内部不使用。
    回测模式和浏览模式使用完全相同的加载逻辑。"""
    # 回测模式和浏览模式使用相同的加载逻辑
    # bar_index 参数仅保留签名兼容性，内部不使用
    _sync_to_display(ticker_code, tf, day_offset, n_pts)
    display_path = Path(__file__).parent.parent / "data" / "display" / f"{tf}.parquet"
    err = None
    if display_path.exists():
        try:
            df = pd.read_parquet(display_path)
            if "Date" in df.columns and "Close" in df.columns and len(df) >= 5:
                df["Date"] = pd.to_datetime(df["Date"])
                df = df.set_index("Date").sort_index()
                t = np.arange(len(df), dtype=float)
                noisy = df["Close"].values.ravel()
                ohlc = df[["Open", "High", "Low", "Close"]] if all(c in df.columns for c in ["Open", "High", "Low"]) else pd.DataFrame({"Open": noisy, "High": noisy, "Low": noisy, "Close": noisy}, index=df.index)
                return t, noisy, ohlc, ticker_code, df.index, None
            else:
                err = "数据不足"
        except Exception as e:
            err = str(e)
    if err is not None:
        return None, None, None, None, None, err
    return _cached_fetch_stock(market, ticker_code, tf, n_pts)


def _read_display_parquet(display_path, ticker_code) -> tuple:
    """从 display parquet 读取数据并返回 (t, noisy, ohlc, ticker_full, dates, err)。"""
    try:
        df = pd.read_parquet(display_path)
        if "Date" in df.columns and "Close" in df.columns and len(df) >= 5:
            df["Date"] = pd.to_datetime(df["Date"])
            df = df.set_index("Date").sort_index()
            t = np.arange(len(df), dtype=float)
            noisy = df["Close"].values.ravel()
            ohlc = df[["Open", "High", "Low", "Close"]] if all(c in df.columns for c in ["Open", "High", "Low"]) else pd.DataFrame({"Open": noisy, "High": noisy, "Low": noisy, "Close": noisy}, index=df.index)
            return t, noisy, ohlc, ticker_code, df.index, None
        return None, None, None, None, None, "数据不足"
    except Exception as e:
        return None, None, None, None, None, str(e)


def _compute_filters(noisy, t, cfg) -> tuple[np.ndarray, np.ndarray | None]:
    """Compute primary and optional secondary filter. Returns (filtered, filtered2).
    Note: Not cached via @st.cache_data because params include unhashable np.ndarray."""
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


def _compute_schmitt_trigger(filtered, t, cfg) -> dict | None:
    """Compute Schmitt trigger signal. Returns schmitt dict or None.
    Note: Not cached via @st.cache_data because params include unhashable np.ndarray."""
    if not cfg["show_sch"] or np.all(np.isnan(filtered)) or len(t) < 2:
        return None
    _v = np.gradient(filtered, t)
    _a = np.gradient(_v, t)
    logger.debug(f"Computing Schmitt trigger: ewma={cfg['ew']}, k_eps={cfg['ke']}, sigma_min={cfg['sm']}")
    return _schmitt_trigger(_v, _a, ewma_span=cfg["ew"], k_eps=cfg["ke"], sigma_min=cfg["sm"])


def _compute_prediction_pairs(t, filtered, schmitt, cfg, all_pairs, bar_index=None) -> list:
    """Compute prediction curves for each pair. Returns list of pred_pairs dicts.
    回测模式下（bar_index 非 None）过滤 pair_end > bar_index 的预测对，防止前视偏差。"""
    if not cfg.get("show_pred") or schmitt is None:
        return []
    pred_pairs = []
    logger.debug(f"Computing prediction curves: {len(all_pairs)} pairs, mode={cfg.get('fit_mode')}")
    fit_func = _fit_physics_parabola if cfg.get("fit_mode") == "parabola" else _fit_parabolic
    for pair_start, pair_end in all_pairs:
        if pair_end - pair_start >= 3:
            # 回测模式：跳过使用未来信号的预测对
            if bar_index is not None and pair_end > bar_index:
                continue
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
                    rh = [0.24, 0.11, 0.12, 0.12, 0.16, 0.24, 0.15, 0.12]
                    titles = ("价格&滤波", "残差", "速度v", "a&±ε", "Sig_t", "PnL收益(%)", f"{_higher_tf}PnL参考", "同向性判断")
                    pnl_row = 6
                    cross_row = 7
                    align_row = 8
                else:
                    rows = 7
                    rh = [0.24, 0.11, 0.12, 0.12, 0.16, 0.27, 0.18]
                    titles = ("价格&滤波", "残差", "速度v", "a&±ε", "Sig_t", "PnL收益(%)", f"{_higher_tf}PnL参考")
                    pnl_row = 6
                    cross_row = 7
                    align_row = None
            else:
                rows = 6
                rh = [0.24, 0.11, 0.12, 0.12, 0.16, 0.375]
                titles = ("价格&滤波", "残差", "速度v", "a&±ε", "Sig_t", "PnL收益(%)")
                pnl_row = 6
                cross_row = None
                align_row = None
        else:
            rows = 5
            rh = [0.28, 0.14, 0.18, 0.18, 0.22]
            titles = ("价格&滤波", "残差", "速度v", "a&±ε", "Sig_t")
            pnl_row = None
            cross_row = None
            align_row = None
        return rows, rh, titles, 1, 2, 3, 4, 5, None, pnl_row, cross_row, align_row
    else:
        return 4, [0.40, 0.18, 0.20, 0.22], ("价格&滤波", "残差", "速度v", "加速度a"), \
               1, 2, 3, None, None, 4, None, None, None


def _add_main_price_traces(fig, t, noisy, ohlc, filtered, filtered2, cfg) -> None:
    """Add K-line, close price, and filter lines to the main price subplot."""
    fig.add_trace(go.Candlestick(x=t, open=ohlc["Open"].values.ravel(),
        high=ohlc["High"].values.ravel(), low=ohlc["Low"].values.ravel(),
        close=ohlc["Close"].values.ravel(), name="K",
        increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
        showlegend=False), row=1, col=1)
    fig.add_trace(go.Scatter(x=t, y=noisy, mode="lines", name="收盘",
        line=dict(color="#5f6c80", width=1.0)), row=1, col=1)
    if not np.all(np.isnan(filtered)):
        fig.add_trace(go.Scatter(x=t, y=filtered, mode="lines", name="滤波",
            line=dict(color=cfg["fc"], width=2.0)), row=1, col=1)
    if cfg["_dual"] and filtered2 is not None and not np.all(np.isnan(filtered2)):
        fig.add_trace(go.Scatter(x=t, y=filtered2, mode="lines", name="滤波2",
            line=dict(color=cfg["fc2"], width=2.0)), row=1, col=1)


def _add_residual_traces(fig, t, filtered, noisy, filtered2, cfg, rr, vr) -> np.ndarray:
    """Add residual, velocity, and acceleration traces to subplots. Returns acceleration array."""
    if len(t) < 2:
        return np.zeros_like(t)
    if not np.all(np.isnan(filtered)):
        fig.add_trace(go.Scatter(x=t, y=filtered - noisy, mode="lines", name="残差",
            line=dict(color="#5f6c80", width=1.0, dash="dot")), row=rr, col=1)
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5, row=rr, col=1)
        vel = np.gradient(filtered, t)
        acc = np.gradient(vel, t)
        fig.add_trace(go.Scatter(x=t, y=vel, mode="lines", name="v",
            line=dict(color=cfg["fc"], width=1.5)), row=vr, col=1)
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5, row=vr, col=1)
        return acc
    vel = np.gradient(filtered, t) if not np.all(np.isnan(filtered)) else np.zeros_like(t)
    return np.gradient(vel, t)


def _add_schmitt_traces(fig, t, schmitt, acc, all_pairs, sar, ssr) -> None:
    """Add Schmitt trigger traces: eps bands, sigma_v, acceleration, Sig signal, pair bands."""
    eps = schmitt["eps"]
    sig = schmitt["sig"]
    fig.add_trace(go.Scatter(x=list(t) + list(t[::-1]), y=list(eps) + list(-eps[::-1]),
        fill="toself", fillcolor="rgba(128,128,128,0.06)", line=dict(width=0),
        name="±ε", hoverinfo="skip"), row=sar, col=1)
    fig.add_trace(go.Scatter(x=t, y=eps, mode="lines", name="+ε",
        line=dict(color="#f85149", width=0.8, dash="dash"), showlegend=False), row=sar, col=1)
    fig.add_trace(go.Scatter(x=t, y=-eps, mode="lines", name="-ε",
        line=dict(color="#f85149", width=0.8, dash="dash")), row=sar, col=1)
    fig.add_trace(go.Scatter(x=t, y=schmitt["sigma_v"], mode="lines", name="σ(v)",
        line=dict(color="#a371f7", width=1.0, dash="dot")), row=sar, col=1)
    fig.add_trace(go.Scatter(x=t, y=acc, mode="lines", name="a",
        line=dict(color="#d2991d", width=1.5)), row=sar, col=1)
    fig.add_hline(y=0, line_dash="solid", line_color="gray", opacity=0.3, row=sar, col=1)
    fig.add_trace(go.Scatter(x=t, y=sig.astype(float), mode="lines", name="Sig",
        line=dict(color="#58a6ff", width=2, shape="hv")), row=ssr, col=1)
    for state, cl in [(1, "rgba(63,185,80,0.06)"), (-1, "rgba(248,81,73,0.06)")]:
        msk = sig == state
        if msk.any():
            fig.add_trace(go.Scatter(x=t[msk], y=np.where(msk, state, 0),
                mode="lines", line=dict(width=0), fill="tozeroy",
                fillcolor=cl, showlegend=False, hoverinfo="skip"), row=ssr, col=1)
    for i, (p_start, p_end) in enumerate(all_pairs):
        direction = sig[p_end]
        y_lo, y_hi = (0, 1) if direction == 1 else (-1, 0)
        band_color = "rgba(88,166,255,0.10)" if i % 2 == 0 else "rgba(163,113,247,0.10)"
        fig.add_trace(go.Scatter(
            x=[p_start, p_end, p_end, p_start],
            y=[y_hi, y_hi, y_lo, y_lo],
            fill="toself", fillcolor=band_color,
            mode="lines", line=dict(width=0),
            showlegend=False, hoverinfo="skip",
        ), row=ssr, col=1)


def _add_pnl_traces(fig, t, long_pnl, short_pnl, trade_records, pnl_row) -> None:
    """Add PnL curves, individual trade segments, markers, and annotations."""
    fig.add_trace(go.Scatter(x=t, y=long_pnl, mode="lines", name="做多PnL",
        line=dict(color="#3fb950", width=1.5, dash="solid")), row=pnl_row, col=1)
    fig.add_trace(go.Scatter(x=t, y=short_pnl, mode="lines", name="做空PnL",
        line=dict(color="#f85149", width=1.5, dash="solid")), row=pnl_row, col=1)
    for trade in trade_records:
        seg_t = t[trade["entry_idx"]:trade["exit_idx"] + 1]
        curve = long_pnl if trade["type"] == "long" else short_pnl
        seg_pnl = curve[trade["entry_idx"]:trade["exit_idx"] + 1]
        is_long = trade["type"] == "long"
        color = "#3fb950" if is_long else "#f85149"
        label_prefix = "多" if is_long else "空"
        fig.add_trace(go.Scatter(x=seg_t, y=seg_pnl, mode="lines",
            name=f"{label_prefix}#{trade['id']}",
            line=dict(color=color, width=3), showlegend=False), row=pnl_row, col=1)
        fig.add_trace(go.Scatter(x=[seg_t[0]], y=[seg_pnl[0]], mode="markers",
            marker=dict(color=color, symbol="triangle-up", size=8),
            showlegend=False), row=pnl_row, col=1)
        if trade["exit_reason"] in ("stop_loss", "take_profit"):
            exit_marker = "x" if trade["exit_reason"] == "stop_loss" else "circle"
            exit_color = "#f85149" if trade["exit_reason"] == "stop_loss" else "#3fb950"
            fig.add_trace(go.Scatter(x=[seg_t[-1]], y=[seg_pnl[-1]], mode="markers",
                marker=dict(color=exit_color, symbol=exit_marker, size=8),
                showlegend=False), row=pnl_row, col=1)
            ret_pct = trade["return_pct"]
            label_color = "#f85149" if trade["exit_reason"] == "stop_loss" else "#3fb950"
            arrow = "↑" if trade["type"] == "long" else "↓"
            fig.add_annotation(x=seg_t[-1], y=seg_pnl[-1], text=f"{arrow}{ret_pct:+.1f}%",
                showarrow=False, font=dict(size=8, color=label_color), yshift=12,
                row=pnl_row, col=1)
    fig.add_hline(y=100, line_dash="dash", line_color="gray", opacity=0.5, row=pnl_row, col=1)
    y_max_l = max(float(np.nanmax(long_pnl)), 100.0) * 1.02
    fig.add_trace(go.Scatter(x=[t[0], t[-1], t[-1], t[0]],
        y=[100, 100, y_max_l, y_max_l], fill="toself", fillcolor="rgba(63,185,80,0.04)",
        mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip"), row=pnl_row, col=1)
    y_min_s = min(float(np.nanmin(short_pnl)), 100.0) * 0.98
    fig.add_trace(go.Scatter(x=[t[0], t[-1], t[-1], t[0]],
        y=[100, 100, y_min_s, y_min_s], fill="toself", fillcolor="rgba(248,81,73,0.04)",
        mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip"), row=pnl_row, col=1)
    fig.update_yaxes(title_text="PnL(%)", row=pnl_row, col=1, ticksuffix="%")


# =====================================================================
# Chart rendering (main figure builder)
# =====================================================================

@st.fragment
def _render_chart_fragment(market, ticker_code, cfg, key, compact=True, day_offset=0, higher_pnl=None, bar_index=None) -> None:
    """Fragment wrapper for _render_chart — enables per-view independent re-rendering.
    bar_index: 回测模式下传递的 playhead 位置，仅用于视觉叠加。"""
    _render_chart(market, ticker_code, cfg, key, compact=compact, day_offset=day_offset, higher_pnl=higher_pnl, bar_index=bar_index)


def _render_chart(market, ticker_code, cfg, key, compact=True, day_offset=0, higher_pnl=None, bar_index=None) -> None:
    """Fetch data + render multi-subplot figure from config.
    优先从本地 Parquet 读取；day_offset=向历史前移N天（各周期独立对齐）。
    higher_pnl: 高周期PnL数据（来自 _align_pnl_to_current_tf 的输出），非空时新增row 7子图。"""
    tf = cfg["tf"]
    n_pts = cfg["n_pts"]
    logger.debug(f"Rendering chart: {ticker_code}/{tf} view={key} n_pts={n_pts}")

    # 查找紧邻高周期tf，尝试从session_state获取其PnL数据
    _higher_tf = TF_HIERARCHY.get(tf)
    _raw_higher = None
    if higher_pnl is None and _higher_tf is not None:
        _raw_higher = st.session_state.get(f"_pnl_{_higher_tf}")

    # ── Step 1: Load chart data ──
    # bar_index 仅保留签名兼容，数据加载与浏览模式完全一致
    t, noisy, ohlc, ticker_full, dates, err = _load_chart_data(market, ticker_code, tf, day_offset, n_pts, bar_index=bar_index)
    if err is not None:
        st.error(err)
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
        st.caption(f"⚠️ 施密特信号不可用：bar数({len(t)}) < N_EWMA({cfg['ew']})。"
                   f"请降低 N_EWMA 至 ≤{len(t)} 或增加数据点数(N)。")

    all_pairs = []
    if schmitt is not None:
        all_pairs = _find_all_pairs(schmitt["sig"])

    # ── Step 7: Prediction curves ──
    pred_pairs = _compute_prediction_pairs(t, filtered, schmitt, cfg, all_pairs, bar_index=bar_index)

    # ── Step 8: Strategy PnL ──
    long_pnl, short_pnl, trade_records = _compute_strategy_display(
        t, filtered, schmitt, all_pairs, pred_pairs, cfg, tf, dates)
    show_strategy = cfg.get("show_strategy", False)
    show_cross_pnl = cfg.get("show_cross_pnl", False)
    show_alignment = cfg.get("show_alignment", False)
    has_strategy = show_strategy and long_pnl is not None and len(trade_records) > 0

    # ── Step 9: Determine subplot layout ──
    has_s = schmitt is not None
    has_cross = (show_cross_pnl and higher_pnl is not None and
                 (len(higher_pnl.get("entry_markers", [])) > 0 or
                  len(higher_pnl.get("exit_markers", [])) > 0))
    _align_masks = None
    if show_alignment and has_cross and has_strategy and long_pnl is not None:
        _align_masks = _compute_holding_masks(
            len(t), higher_pnl["entry_markers"], higher_pnl["exit_markers"])
    has_alignment = (_align_masks is not None and
                     (_align_masks[0].any() or _align_masks[1].any()))

    rows, rh, titles, mr, rr, vr, sar, ssr, ar, pnl_row, cross_row, align_row = \
        _determine_subplot_layout(has_s, has_strategy, has_cross, has_alignment, _higher_tf)

    # ── Step 10: Build figure ──
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True,
                        vertical_spacing=0.01, row_heights=rh, subplot_titles=titles)

    _add_main_price_traces(fig, t, noisy, ohlc, filtered, filtered2, cfg)

    for i, pp in enumerate(pred_pairs):
        _add_prediction_traces(fig, t, filtered,
                               pp["fit_result"], pp["fit_start"],
                               pp["pair_end"], row=mr,
                               n_extend=cfg.get("n_ext", 10),
                               show_legend=(i == 0))

    acc = _add_residual_traces(fig, t, filtered, noisy, filtered2, cfg, rr, vr)

    if has_s:
        _add_schmitt_traces(fig, t, schmitt, acc, all_pairs, sar, ssr)

    if has_strategy:
        _add_pnl_traces(fig, t, long_pnl, short_pnl, trade_records, pnl_row)

    if has_cross and higher_pnl is not None and cross_row is not None:
        _add_cross_pnl_subplot(fig, t, higher_pnl, row=cross_row, higher_tf=_higher_tf)
        fig.update_yaxes(title_text=f"{_higher_tf}(%)", row=cross_row, col=1, ticksuffix="%")

    if has_alignment and _align_masks is not None and align_row is not None:
        long_mask, short_mask = _align_masks
        _add_alignment_subplot(fig, t, long_pnl, short_pnl, trade_records,
                               long_mask, short_mask, row=align_row)
        fig.update_yaxes(title_text="同向(%)", row=align_row, col=1, ticksuffix="%")

    if ar is not None and not np.all(np.isnan(filtered)):
        fig.add_trace(go.Scatter(x=t, y=acc, mode="lines", name="a",
            line=dict(color="#ffa502", width=1.5)), row=ar, col=1)
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5, row=ar, col=1)

    # ── Step 11: Final layout ──
    fig.add_shape(type="line", x0=0, x1=0, y0=0, y1=1, xref="x", yref="paper",
                   line=dict(color="rgba(200,200,200,0.4)", width=1, dash="dot"), visible=False)
    for pos in marker_positions:
        fig.add_vline(x=pos, line=dict(color="rgba(255,255,255,0.10)", width=0.8, dash="dot"),
                       layer="below")
    fh = (620 if has_s else 420) if compact else (960 if has_s else 700)
    if has_cross:
        fh += 120
    if has_alignment:
        fh += 75
    fig.update_layout(template="plotly_dark", height=fh,
        margin=dict(l=10, r=10, t=25, b=10), hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=9)))
    fig.update_xaxes(title_text="", row=rows, col=1,
                      tickvals=marker_positions, ticktext=marker_labels,
                      tickfont=dict(size=9, color="#8b949e"))
    fig.update_xaxes(rangeslider_visible=False, row=1, col=1)
    fig.update_yaxes(title_text="价格", row=mr, col=1)
    fig.update_yaxes(title_text="残差", row=rr, col=1)
    fig.update_yaxes(title_text="速度", row=vr, col=1)
    if has_s:
        fig.update_yaxes(title_text="a±ε", row=sar, col=1)
        fig.update_yaxes(title_text="Sig", row=ssr, col=1,
                          tickvals=[-1, 0, 1], ticktext=["空", "观", "多"], range=[-1.5, 1.5])
    if ar is not None:
        fig.update_yaxes(title_text="加速度", row=ar, col=1)

    # 回测模式：添加标注（bar_index 直接映射到数据位置）
    if bar_index is not None:
        _add_backtest_overlay(fig, bar_index, len(t), dates, cfg.get("tf", ""))

    _render_plotly(fig, height=fh + 30, dates=dates)


# =====================================================================
@st.cache_resource
def _get_db_connection() -> bool:
    """Get database connection (cached across all sessions)."""
    logger.debug("Initializing database connection (cache miss)")
    init_db()
    return True


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


# =====================================================================
# 回测模式 — Phase 1 基础函数
# =====================================================================


def _get_min_tf_and_count(configs, ticker_code) -> tuple:
    """从4个视图的tf配置中确定最小周期（最精细）和总bar数。
    遍历configs中各视图的tf字段，取ALL_TFS中索引最小的（最精细的）为_min_tf。
    返回 (_min_tf, bar_count)。"""
    from components.sidebar import ALL_TFS

    if not configs:
        return "", 0

    # 取索引最小的 tf（最精细周期）
    min_idx = len(ALL_TFS)  # 初始化为一个比任何有效索引都大的值
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

    # 加载该 tf 的数据获取总 bar 数
    try:
        from pathlib import Path
        display_path = Path(__file__).parent.parent / "data" / "display" / f"{min_tf}.parquet"
        if display_path.exists():
            df = pd.read_parquet(display_path)
            bar_count = len(df)
        else:
            bar_count = 0
    except Exception:
        bar_count = 0

    return min_tf, bar_count


def _render_backtest_mode_switch(market, ticker_code, configs) -> None:
    """侧边栏回测模式切换 radio。切换时触发数据预加载/清除。"""
    from services.data_loader import _sync_to_display
    from pathlib import Path

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
            # 切换到回测模式：加载全量数据到 _bt_data_cache
            with st.spinner("加载回测数据..."):
                cache = {}
                for cfg in configs:
                    tf = cfg["tf"]
                    n_pts = cfg["n_pts"]
                    try:
                        _sync_to_display(ticker_code, tf, 0, n_pts)
                        display_path = Path(__file__).parent.parent / "data" / "display" / f"{tf}.parquet"
                        if display_path.exists():
                            df = pd.read_parquet(display_path)
                            if "Date" in df.columns:
                                df["Date"] = pd.to_datetime(df["Date"])
                                df = df.set_index("Date").sort_index()
                            cache[tf] = df
                    except Exception as e:
                        logger.warning(f"回测数据加载失败 {tf}: {e}")
                AppState.set("_bt_data_cache", cache)
                AppState.set("_bar_index", 0)
                AppState.set("_is_playing", False)
                # 计算并保存最小周期信息
                min_tf, bar_count = _get_min_tf_and_count(configs, ticker_code)
                AppState.set("_min_tf", min_tf)
                AppState.set("_min_tf_bar_count", bar_count)
                st.toast(f"回测模式已启用  最小周期: {min_tf} ({bar_count} bars)")
        else:
            # 切换回浏览模式：清除回测状态
            AppState.set("_bt_data_cache", {})
            AppState.set("_bar_index", 0)
            AppState.set("_is_playing", False)
            AppState.set("_min_tf", "")
            AppState.set("_min_tf_bar_count", 0)

        AppState.set("_cb_mode", new_cb_mode)
        st.rerun()


def _render_backtest_status() -> None:
    """侧边栏紧凑状态显示：当前bar时间 + bar_index/total + 播放状态。"""
    if not AppState.get("_cb_mode", False):
        return

    bar_index = AppState.get("_bar_index", 0)
    total_bars = AppState.get("_min_tf_bar_count", 0)
    min_tf = AppState.get("_min_tf", "")
    is_playing = AppState.get("_is_playing", False)

    # 尝试从缓存中获取当前 bar 的时间
    cache = AppState.get("_bt_data_cache", {})
    current_date = ""
    if min_tf and min_tf in cache and total_bars > 0:
        df = cache[min_tf]
        if bar_index < len(df):
            idx = df.index[bar_index]
            current_date = str(idx.date()) if hasattr(idx, "date") else str(idx)

    label = f"📍 bar {bar_index + 1}/{total_bars}"
    if current_date:
        label += f" | {current_date}"
    if is_playing:
        label += " | :green[▶ 播放中]"
    st.sidebar.caption(label)


def _render_backtest_controls() -> None:
    """回测控制条：导航按钮 + 速度选择 + 进度 slider。
    布局：2x2 图表上方，一行 st.columns 排列。
    仅 _cb_mode=True 时调用。"""
    if not AppState.get("_cb_mode", False):
        return

    bar_index = AppState.get("_bar_index", 0)
    total_bars = AppState.get("_min_tf_bar_count", 0)
    if total_bars == 0:
        st.caption("回测数据未就绪")
        return

    is_playing = AppState.get("_is_playing", False)

    # 速度选择（单独一行 + 为了紧凑放在导航行之前或之后；这里放在导航行最后）
    # 使用 6 列布局：⏮ ◀ ▶/⏸ ▶▶ ⏭  速度
    # 但为了进度 slider 需要额外一行
    col_nav = st.columns([1, 1, 1, 1, 1, 2])

    with col_nav[0]:
        if st.button("⏮", key="_bt_goto_start", use_container_width=True, help="跳到开头"):
            AppState.set("_bar_index", 0)
            st.rerun()

    with col_nav[1]:
        disabled = bar_index <= 0
        if st.button("◀", key="_bt_step_back", use_container_width=True,
                     disabled=disabled, help="后退一个 bar"):
            AppState.set("_bar_index", max(0, bar_index - 1))
            st.rerun()

    with col_nav[2]:
        btn_label = "⏸" if is_playing else "▶"
        btn_help = "暂停" if is_playing else "播放"
        if st.button(btn_label, key="_bt_toggle_play", use_container_width=True, help=btn_help):
            AppState.set("_is_playing", not is_playing)
            st.rerun()

    with col_nav[3]:
        disabled = bar_index >= total_bars - 1
        if st.button("▶▶", key="_bt_step_fwd", use_container_width=True,
                     disabled=disabled, help="前进一个 bar"):
            AppState.set("_bar_index", min(total_bars - 1, bar_index + 1))
            st.rerun()

    with col_nav[4]:
        if st.button("⏭", key="_bt_goto_end", use_container_width=True, help="跳到最新"):
            AppState.set("_bar_index", total_bars - 1)
            st.rerun()

    with col_nav[5]:
        speed_label = AppState.get("_play_speed_label", "1x")
        speed_map = {
            "0.25x": 0.25, "0.5x": 0.5, "1x": 1.0,
            "2x": 2.0, "5x": 5.0, "10x": 10.0,
        }
        # 速度选择器 label 需反向查找当前索引
        speed_options = list(speed_map.keys())
        current_idx = speed_options.index(speed_label) if speed_label in speed_options else 2
        new_speed_label = st.selectbox(
            "速度", speed_options, index=current_idx,
            key="_bt_speed_select", label_visibility="collapsed",
        )
        if new_speed_label != speed_label:
            AppState.set("_play_speed_label", new_speed_label)
            AppState.set("_play_speed", speed_map[new_speed_label])

    # 进度 slider（第二行）
    new_bar_index = st.slider(
        "进度", 0, total_bars - 1, bar_index,
        key="_bt_progress_slider", label_visibility="collapsed",
    )
    if new_bar_index != bar_index:
        AppState.set("_bar_index", new_bar_index)
        if is_playing:
            AppState.set("_is_playing", False)  # 拖拽进度条时自动暂停
        st.rerun()


def _run_backtest_play() -> None:
    """回测自动播放：time.sleep + st.rerun 循环。
    仅在 _cb_mode=True 且 _is_playing=True 时执行。
    到达末尾自动停止并设置 _is_playing=False。"""
    if not AppState.get("_cb_mode", False):
        return
    if not AppState.get("_is_playing", False):
        return

    bar_index = AppState.get("_bar_index", 0)
    total = AppState.get("_min_tf_bar_count", 0)
    if total == 0:
        return
    if bar_index >= total - 1:
        AppState.set("_is_playing", False)  # 到达末尾，自动停止
        return

    # 速度映射：speed_label → 延迟（秒/步）
    speed_label = AppState.get("_play_speed_label", "1x")
    speed_map = {
        "0.25x": 4.0, "0.5x": 2.0, "1x": 1.0,
        "2x": 0.5, "5x": 0.2, "10x": 0.1,
    }
    delay = speed_map.get(speed_label, 1.0)

    time.sleep(delay)
    AppState.set("_bar_index", bar_index + 1)
    st.rerun()


def _render_time_nav(configs, ticker_code) -> int:
    """Render time window navigation. Returns day_offset.
    回测模式下隐藏 day_offset 控件，返回 day_offset=0。"""
    st.sidebar.markdown("---")
    st.sidebar.caption("⏪ 时间窗口（按天移动）")

    # 回测模式：隐藏 day_offset 控件，回测导航由 _render_backtest_controls 提供
    if AppState.get("_cb_mode", False):
        AppState.set("_day_offset", 0)
        return 0

    # 浏览模式：保持现有功能不变
    if not AppState.has("_day_offset"):
        AppState.set("_day_offset", 0)
    step_days = st.sidebar.selectbox("移动步长", [1, 3, 5, 10, 20, 30, 60, 90, 180, 365],
                                      index=4, key="day_step",
                                      format_func=lambda x: f"{x}天")
    data_start = data_end = None
    date_range = get_date_range(ticker_code)
    if date_range:
        data_start = pd.Timestamp(date_range[0][:10]).date()
        data_end = pd.Timestamp(date_range[1][:10]).date()
    cur_offset = AppState.get("_day_offset", 0)
    n_pts = configs[0]["n_pts"] if configs else 120
    if data_end:
        win_end = data_end - pd.Timedelta(days=cur_offset)
        win_start = win_end - pd.Timedelta(days=n_pts * 2)
        has_older = data_start and win_start > data_start
        has_newer = cur_offset > 0
    else:
        has_older = True
        has_newer = cur_offset > 0
    c_prev, c_next, c_home = st.sidebar.columns([1, 1, 0.8])
    with c_prev:
        disabled = not has_older
        if st.button("◀ 前移", key="day_prev", use_container_width=True, disabled=disabled,
                     help="无更早数据" if disabled else f"前移{step_days}天"):
            AppState.set("_day_offset", AppState.get("_day_offset", 0) + step_days)
    with c_next:
        disabled = not has_newer
        if st.button("后移 ▶", key="day_next", use_container_width=True, disabled=disabled,
                     help="已是最新" if disabled else f"后移{step_days}天"):
            AppState.set("_day_offset", max(0, AppState.get("_day_offset", 0) - step_days))
    with c_home:
        if st.button("最新", key="day_home", use_container_width=True, disabled=cur_offset == 0,
                     help="已是最新"):
            AppState.set("_day_offset", 0)
    st.sidebar.caption(f"已偏移: {cur_offset} 天")
    if data_start and data_end:
        st.sidebar.caption(f"数据范围: {data_start} ~ {data_end}")
    return AppState.get("_day_offset", 0)


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


def _render_export_config(configs, filter_id, filter_id2, dual, market, ticker_code) -> None:
    """Render config export download button."""
    st.sidebar.markdown("---")
    export_data = {
        "market": market, "ticker": ticker_code,
        "global_f": filter_id, "global_dual": dual, "global_f2": filter_id2,
    }
    for i, cfg in enumerate(configs):
        export_data[f"v{i}_tf"] = cfg["tf"]
        export_data[f"v{i}_n"] = cfg["n_pts"]
        export_data[f"v{i}_sch"] = cfg["show_sch"]
        export_data[f"v{i}_pred"] = cfg["show_pred"]
        export_data[f"v{i}_ke"] = cfg["ke"]
        export_data[f"v{i}_sm"] = cfg["sm"]
        export_data[f"v{i}_ew"] = cfg["ew"]
        export_data[f"v{i}_fm"] = cfg["fit_mode"]
        export_data[f"v{i}_next"] = cfg["n_ext"]
        export_data[f"v{i}_fc"] = cfg["fc"]
        export_data[f"v{i}_fc2"] = cfg["fc2"]
        export_data[f"v{i}_strat"] = cfg.get("show_strategy", False)
        export_data[f"v{i}_sl"] = cfg.get("stop_loss_pct", 2.0)
        export_data[f"v{i}_cross_pnl"] = cfg.get("show_cross_pnl", False)
        export_data[f"v{i}_align"] = cfg.get("show_alignment", False)
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

    # ── Market & ticker ──
    market, ticker_code = _render_market_ticker()

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

    # ── Time window navigation ──
    day_offset = _render_time_nav(configs, ticker_code)

    # ── 回测模式切换 — Phase 1 ──
    _render_backtest_mode_switch(market, ticker_code, configs)
    _render_backtest_status()

    # ── DB backup/restore ──
    _render_db_backup()

    # ── 回测控制条（在 2x2 图表上方）— Phase 3 ──
    cb_mode = AppState.get("_cb_mode", False)
    bar_index = AppState.get("_bar_index", None) if cb_mode else None
    if cb_mode:
        _render_backtest_controls()

    # ── Pass 2: 2x2 chart views ──
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
                                   day_offset=day_offset, bar_index=bar_index)

    # ── Export config ──
    _render_export_config(configs, filter_id, filter_id2, dual, market, ticker_code)

    # ── Config history ──
    _render_config_history(ticker_code)

    # ── DB import/export ──
    _render_db_import_export()

    # ── 回测播放循环（在 auto-refresh 之前）— Phase 3 ──
    _run_backtest_play()

    # ── Auto-refresh ──
    _run_auto_refresh(market, ticker_code, auto_refresh, interval)


if __name__ == "__main__":
    main()
