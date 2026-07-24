"""
数据拉取模块 — yfinance 下载 + 清洗

从 ``data/loader.py`` 拆分，独立维护 yfinance 数据获取逻辑。
"""

import numpy as np
import pandas as pd
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed
from loguru import logger
from typing import Any, Dict, Optional, Tuple
from filter.data.db import upsert_kline, query_kline, get_latest_date


def fetch_incremental(market: str, code: str, tf: str, n_pts: int = 120,
                      force_period: Optional[str] = None):
    """增量拉取：检查DB中已有最新日期，仅拉取其后数据。避免全量重复下载。

    DB有数据时从 last_date 开始增量拉取；无数据时回退到全量拉取。

    Parameters
    ----------
    market : str
        市场标识，如 "A股(沪深)"、"港股 HK" 等。
    code : str
        股票代码。
    tf : str
        周期名称，如 "日线"、"60分钟" 等。
    n_pts : int
        需要返回的数据点数。
    force_period : Optional[str]
        强制指定 yfinance 的 period 参数。

    Returns
    -------
    Tuple
        与 _fetch_stock 相同的返回元组。
    """
    return _fetch_stock(market, code, tf, n_pts, force_period=force_period, incremental=True)


def _fetch_all_timeframes(market: str, code: str) -> Dict[str, Tuple[bool, Any]]:
    """获取某股票全部8个周期的数据，并行写入DB。返回成功/失败统计。

    Parameters
    ----------
    market : str
        市场标识，如 "A股(沪深)"、"港股 HK" 等。
    code : str
        股票代码。

    Returns
    -------
    Dict[str, Tuple[bool, Any]]
        键为周期名称，值为 (是否成功, 详情信息) 的元组。
    """
    tf_config = {
        "1分钟": ("7d",), "5分钟": ("60d",), "15分钟": ("60d",),
        "60分钟": ("730d",), "日线": ("max",), "周线": ("max",),
        "月线": ("max",), "季线": ("max",),
    }

    def _fetch_one(tf: str) -> Tuple[str, bool, Any]:
        force_period = tf_config[tf][0]
        try:
            t, close, ohlc, full, err, dates = _fetch_stock(market, code, tf, 99999, force_period=force_period)
            if err or ohlc is None:
                return tf, False, err or "无数据"
            return tf, True, len(ohlc)
        except Exception as e:
            logger.warning(f"Fetch {market}/{code}/{tf} failed: {e}")
            return tf, False, str(e)[:80]

    results = {}
    with ThreadPoolExecutor(max_workers=8) as exec:
        futures = {exec.submit(_fetch_one, tf): tf for tf in tf_config}
        for fut in as_completed(futures):
            tf, ok, detail = fut.result()
            results[tf] = (ok, detail)
    return results


def _fetch_stock(market: str, code: str, tf: str, n_pts: int,
                 force_period: Optional[str] = None,
                 incremental: bool = False) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], Optional[pd.DataFrame], Optional[str], Optional[str], Optional[pd.DatetimeIndex]]:
    """从yfinance获取股票数据并写入DB。

    Parameters
    ----------
    market : str
        市场标识，如 "A股(沪深)"、"港股 HK" 等。
    code : str
        股票代码。
    tf : str
        周期名称，如 "日线"、"60分钟" 等。
    n_pts : int
        需要返回的数据点数。
    force_period : Optional[str]
        强制指定 yfinance 的 period 参数，覆盖自动计算。
    incremental : bool
        增量模式：检查DB最新日期，仅拉取其后数据。DB无数据时回退全量拉取。

    Returns
    -------
    Tuple[Optional[np.ndarray], Optional[np.ndarray], Optional[pd.DataFrame], Optional[str], Optional[str], Optional[pd.DatetimeIndex]]
        (t, close, ohlc, full, err, dates) — 时间索引数组、收盘价数组、OHLC DataFrame、
        完整代码、错误信息（成功时为None）、日期索引。
    """
    if not code or not code.strip():
        return None, None, None, None, "Empty ticker code", None
    if market == "A股(沪深)":
        suffix = ".SS" if code[0] == "6" else ".SZ"
        full = code + suffix
    elif market == "港股 HK":
        full = code.zfill(4) + ".HK"
    else:
        full = code.upper()

    tf_map = {"1分钟": "1m", "5分钟": "5m", "15分钟": "15m", "60分钟": "1h",
               "日线": "1d", "周线": "1wk", "月线": "1mo", "季线": "3mo"}
    interval = tf_map[tf]
    if force_period:
        period = force_period
    elif tf == "1分钟":
        period = "7d"
    elif tf in ("5分钟", "15分钟"):
        period = "60d"
    elif tf == "60分钟":
        period = "60d"
    elif tf == "日线":
        wanted = max(n_pts * 2, 10)
        if wanted <= 30:      period = "1mo"
        elif wanted <= 90:    period = "3mo"
        elif wanted <= 180:   period = "6mo"
        elif wanted <= 365:   period = "1y"
        elif wanted <= 730:   period = "2y"
        elif wanted <= 1825:  period = "5y"
        elif wanted <= 3650:  period = "10y"
        else:                 period = "max"
    elif tf == "周线":
        wanted = max(n_pts * 5, 52)
        if wanted <= 52:      period = "1y"
        elif wanted <= 104:   period = "2y"
        elif wanted <= 260:   period = "5y"
        elif wanted <= 520:   period = "10y"
        else:                 period = "max"
    elif tf == "月线":
        wanted = max(n_pts * 1.5, 12)
        if wanted <= 12:      period = "1y"
        elif wanted <= 24:    period = "2y"
        elif wanted <= 60:    period = "5y"
        elif wanted <= 120:   period = "10y"
        else:                 period = "max"
    else:  # 季线
        period = "max"

    # ── 增量路径：DB有数据时从 last_date 开始，否则全量 ──
    if incremental:
        last_date = get_latest_date(code, tf)
        if last_date:
            start_str = last_date[:10]  # 取日期部分 "YYYY-MM-DD"
            data = yf.download(full, start=start_str, interval=interval, progress=False)
        else:
            data = yf.download(full, period=period, interval=interval, progress=False)
    else:
        data = yf.download(full, period=period, interval=interval, progress=False)
    if data.empty:
        return None, None, None, full, f"无数据: {full}", None

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.droplevel(1)

    # ── 日线 Close 回退：Yahoo 日线 API 最后一条 bar 的 Close 偶尔未结算(nan)，
    #    但 Yahoo 周线 API 已通过实时行情计算出本周 Close。
    #    用周线 Close 回填日线，解决 A 股/港股收盘价 1-2 天延迟问题。
    if interval == "1d" and len(data) > 0:
        last_close = data["Close"].iloc[-1]
        if pd.isna(last_close):
            try:
                w = yf.download(full, period="5d", interval="1wk", progress=False)
                if len(w) > 0:
                    if isinstance(w.columns, pd.MultiIndex):
                        w.columns = w.columns.droplevel(1)
                    w_close = w["Close"].iloc[-1]
                    if not pd.isna(w_close):
                        data.loc[data.index[-1], "Close"] = float(w_close)
            except Exception as e:
                logger.warning(f"Weekly close fallback failed for {full}: {e}")

    data = data[data["Close"].notna()]

    # ── 全量写入 SQLite ──
    try:
        upsert_kline(code, tf, data)
    except Exception as e:
        logger.error(f"Kline upsert failed for {code}/{tf}: {e}", exc_info=True)

    # 从DB返回最后 n_pts 条
    result_df = query_kline(code, tf, n_pts, day_offset=0)
    n = len(result_df)
    if n == 0:
        return None, None, None, full, "写入成功但查询失败", None
    close = result_df["Close"].values.ravel()
    dates = pd.to_datetime(result_df["Date"])
    result_ohlc = result_df if "Open" in result_df.columns else pd.DataFrame({"Open":close,"High":close,"Low":close,"Close":close}, index=dates)
    return np.arange(n, dtype=float), close, result_ohlc, full, None, dates


def _stock_name_lookup(market: str, code: str) -> str:
    """通过 yfinance 查询股票名称。

    Parameters
    ----------
    market : str
        市场标识，如 "A股(沪深)"、"港股 HK" 等。
    code : str
        股票代码。

    Returns
    -------
    str
        股票名称，查询失败时返回空字符串。
    """
    if not code or not code.strip():
        return ""
    try:
        if market == "A股(沪深)":
            full = code + (".SS" if code[0] == "6" else ".SZ")
        elif market == "港股 HK":
            full = code.zfill(4) + ".HK"
        else:
            full = code.upper()
        return yf.Ticker(full).info.get("longName") or ""
    except Exception as e:
        logger.debug(f"Stock name lookup failed for {full}: {e}")
        return ""
