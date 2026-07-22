"""
数据处理模块 — 数据获取、滤波计算、施密特触发、预测对计算

从 streamlit_app.py 提取的计算密集型函数。
包含 Streamlit 缓存装饰器 (@st.cache_data) 的数据获取和滤波计算。
"""

import hashlib
import numpy as np
import pandas as pd
import streamlit as st
from pathlib import Path
from loguru import logger

from services.data_loader import (
    _fetch_stock,
    _sync_to_display,
)
from services.filter_engine import (
    FILTERS,
    _schmitt_trigger,
    _find_all_pairs,
    _fit_physics_parabola,
)


def _hash_array(arr):
    """Hash numpy array for @st.cache_data hash_funcs (bytes of contiguous copy)."""
    return hashlib.md5(np.ascontiguousarray(arr).data.tobytes()).hexdigest()


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
    display_path = Path(__file__).parent.parent / "data" / "display" / ticker_code / f"{tf}.parquet"
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
                        from services.backtest_logger import log_data_load
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
