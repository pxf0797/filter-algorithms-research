"""
数据加载模块 — 负责yfinance数据获取、K线查询/缓存、数据健康检查

无Streamlit依赖，仅基础库 + db模块
"""

import numpy as np
import pandas as pd
import yfinance as yf
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from loguru import logger
from typing import Any, Dict, Optional, Tuple
from db import upsert_kline, query_kline

# 周期规范定义（与 components/sidebar.py 保持一致，此处无需 Streamlit 依赖）
ALL_TFS = ["1分钟", "5分钟", "15分钟", "60分钟", "日线", "周线", "月线", "季线"]


def _fetch_all_timeframes(market: str, code: str) -> Dict[str, Tuple[bool, Any]]:
    """获取某股票全部8个周期的数据，并行写入DB。返回成功/失败统计。"""
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
                 force_period: Optional[str] = None) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], Optional[pd.DataFrame], Optional[str], Optional[str], Optional[pd.DatetimeIndex]]:
    """从yfinance获取股票数据并写入DB。返回 (t, close, ohlc, full, err, dates)。"""
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


def _get_period_end(ts, tf):
    """计算给定 ts 之后的下一个周期结束时间戳。

    参数:
        ts: pd.Timestamp 或可转换的时间字符串
        tf: 周期名称（如 "日线", "周线", "月线" 等）

    返回:
        pd.Timestamp — 下一个周期结束时间
    """
    ts = pd.Timestamp(ts)

    if tf == "5分钟":
        current_minutes = ts.hour * 60 + ts.minute
        next_boundary = ((current_minutes // 5) + 1) * 5
        next_hour = next_boundary // 60
        next_minute = next_boundary % 60
        if next_hour >= 24:
            return (ts + pd.Timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return ts.replace(hour=next_hour, minute=next_minute, second=0, microsecond=0)

    elif tf == "15分钟":
        current_minutes = ts.hour * 60 + ts.minute
        next_boundary = ((current_minutes // 15) + 1) * 15
        next_hour = next_boundary // 60
        next_minute = next_boundary % 60
        if next_hour >= 24:
            return (ts + pd.Timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return ts.replace(hour=next_hour, minute=next_minute, second=0, microsecond=0)

    elif tf == "60分钟":
        return (ts + pd.Timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

    elif tf == "日线":
        return (ts + pd.Timedelta(days=1)).normalize()

    elif tf == "周线":
        days_ahead = 4 - ts.weekday()  # weekday()=4 是周五
        if days_ahead <= 0:
            days_ahead += 7
        return (ts + pd.Timedelta(days=days_ahead)).normalize()

    elif tf == "月线":
        if ts.month == 12:
            next_month = pd.Timestamp(year=ts.year + 1, month=1, day=1)
        else:
            next_month = pd.Timestamp(year=ts.year, month=ts.month + 1, day=1)
        return next_month - pd.Timedelta(days=1)

    elif tf == "季线":
        q_end_month = ((ts.month - 1) // 3 + 1) * 3
        if q_end_month == 12:
            next_month = pd.Timestamp(year=ts.year + 1, month=1, day=1)
        else:
            next_month = pd.Timestamp(year=ts.year, month=q_end_month + 1, day=1)
        return next_month - pd.Timedelta(days=1)

    else:
        return ts


def _get_period_start(last_completed_ts, tf):
    """计算不完整 bar 的数据查询起点。

    返回的起点时间（ISO 字符串）作为 min_tf 数据查询的 ts >= ? 参数。

    参数:
        last_completed_ts: pd.Timestamp 或可转换的时间字符串
        tf: 高周期名称

    返回:
        str — "YYYY-MM-DD" 格式的日期字符串
    """
    ts = pd.Timestamp(last_completed_ts)

    if tf == "5分钟":
        start = ts + pd.Timedelta(minutes=1)
    elif tf == "15分钟":
        start = ts + pd.Timedelta(minutes=1)
    elif tf == "60分钟":
        start = ts + pd.Timedelta(minutes=1)
    elif tf == "日线":
        start = ts + pd.Timedelta(days=1)
    elif tf == "周线":
        start = ts + pd.Timedelta(days=1)
    elif tf == "月线":
        if ts.month == 12:
            start = pd.Timestamp(year=ts.year + 1, month=1, day=1)
        else:
            start = pd.Timestamp(year=ts.year, month=ts.month + 1, day=1)
    elif tf == "季线":
        # 下个季度起始月: ((ts.month - 1) // 3 + 1) * 3 + 1
        next_q_start_month = ((ts.month - 1) // 3 + 1) * 3 + 1
        if next_q_start_month > 12:
            start = pd.Timestamp(year=ts.year + 1, month=1, day=1)
        else:
            start = pd.Timestamp(year=ts.year, month=next_q_start_month, day=1)
    else:
        start = ts + pd.Timedelta(days=1)

    return start.strftime("%Y-%m-%d")


def _get_this_period_start(ts, tf):
    """返回 ts 所属周期的起点（与 _get_period_start 不同，后者返回下个周期的起点）。

    用于 REPLACE 场景：当 DB 中最后一条 bar 的 ts 处于进行中的周期时，
    需要从 min_tf 重新合成该周期的 bar。
    """
    ts = pd.Timestamp(ts)

    if tf == "5分钟":
        current_minutes = ts.hour * 60 + ts.minute
        boundary = (current_minutes // 5) * 5
        return ts.replace(hour=boundary // 60, minute=boundary % 60, second=0, microsecond=0)
    elif tf == "15分钟":
        current_minutes = ts.hour * 60 + ts.minute
        boundary = (current_minutes // 15) * 15
        return ts.replace(hour=boundary // 60, minute=boundary % 60, second=0, microsecond=0)
    elif tf == "60分钟":
        return ts.replace(minute=0, second=0, microsecond=0)
    elif tf == "日线":
        return ts.normalize()
    elif tf == "周线":
        return (ts - pd.Timedelta(days=ts.weekday())).normalize()
    elif tf == "月线":
        return ts.replace(day=1).normalize()
    elif tf == "季线":
        q_start_month = ((ts.month - 1) // 3) * 3 + 1
        return pd.Timestamp(year=ts.year, month=q_start_month, day=1)
    else:
        return ts


def _sync_to_display(ticker_code: str, tf: str, day_offset: int = 0, n_pts: int = 120,
                     cutoff_date: Optional[str] = None,
                     min_tf: Optional[str] = None) -> Tuple[bool, int]:
    """同步数据到 display parquet。

    cutoff_date=None: 浏览模式，取最新 n_pts 条（支持 day_offset 日期偏移）
    cutoff_date=YYYY-MM-DD: 回测模式，取截止到 cutoff_date 的最后 n_pts 条（日期对齐）
    min_tf: 回测模式下的最小周期 TF，用于聚合高周期不完整 bar
    """
    if cutoff_date is not None:
        # 回测模式：查询截止到 cutoff_date 的最后 n_pts 条，按日期对齐
        from db import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT ts, open, high, low, close, volume
                   FROM kline WHERE ticker=? AND timeframe=? AND ts <= ?
                   ORDER BY ts DESC LIMIT ?""",
                (ticker_code, tf, cutoff_date, n_pts),
            ).fetchall()

        if not rows:
            return False, 0

        # ── 合成高周期不完整 bar ──
        synthesized_bar = None
        replace_last = False
        if min_tf and cutoff_date and ALL_TFS.index(tf) > ALL_TFS.index(min_tf):
            last_bar_ts = pd.Timestamp(rows[0][0])
            cutoff_dt = pd.Timestamp(cutoff_date)
            next_period_start = pd.Timestamp(_get_period_start(last_bar_ts, tf))

            try:
                if cutoff_dt < next_period_start:
                    # SCENARIO B (REPLACE): DB最后一条bar处于进行中的周期
                    # → 用 min_tf 数据重新合成该周期的 bar，替换 rows[0]
                    period_start = _get_this_period_start(last_bar_ts, tf)
                    period_end = last_bar_ts  # bar 的 ts 就是周期结束时间
                    replace_last = True
                else:
                    # SCENARIO A (APPEND): DB最后一条bar已完成
                    # → 检查是否有不完整的下一个 bar
                    period_end = _get_period_end(last_bar_ts, tf)
                    if cutoff_dt >= period_end:
                        period_start = None  # 不触发合成
                    else:
                        period_start = _get_period_start(last_bar_ts, tf)

                if period_start is not None:
                    period_start_str = period_start.strftime("%Y-%m-%d %H:%M:%S") if hasattr(period_start, 'strftime') else str(period_start)
                    with get_conn() as inner_conn:
                        min_rows = inner_conn.execute(
                            """SELECT open, high, low, close, volume
                               FROM kline
                               WHERE ticker=? AND timeframe=? AND ts >= ? AND ts <= ?
                               ORDER BY ts ASC""",
                            (ticker_code, min_tf, period_start_str, cutoff_date),
                        ).fetchall()
                    if min_rows:
                        opens = [r[0] for r in min_rows]
                        highs = [r[1] for r in min_rows]
                        lows  = [r[2] for r in min_rows]
                        closes = [r[3] for r in min_rows]
                        volumes = [r[4] for r in min_rows]
                        # 统一使用 ISO 格式的 Date，避免 parquet 读取崩溃
                        synthesized_bar = {
                            "Date": period_end.strftime("%Y-%m-%dT%H:%M:%S") if hasattr(period_end, 'strftime') else str(period_end),
                            "Open": float(opens[0]),
                            "High": float(max(highs)),
                            "Low": float(min(lows)),
                            "Close": float(closes[-1]),
                            "Volume": float(sum(volumes)),
                        }
                        logger.debug(f"Synthesized {tf} bar for {ticker_code} at {cutoff_date}: "
                                     f"O={opens[0]:.2f} H={max(highs):.2f} L={min(lows):.2f} "
                                     f"C={closes[-1]:.2f} V={sum(volumes):.0f} "
                                     f"mode={'REPLACE' if replace_last else 'APPEND'}")
            except Exception as e:
                logger.debug(f"Synthesis failed for {ticker_code}/{tf}: {e}")
                synthesized_bar = None
                replace_last = False

        # 构建 DataFrame，处理合成 bar 追加与截断
        if synthesized_bar is not None:
            # rows 是 DESC 结果，需反转
            data = []
            for r in reversed(rows):
                data.append({
                    "Date": r[0], "Open": r[1], "High": r[2],
                    "Low": r[3], "Close": r[4], "Volume": r[5],
                })
            if replace_last:
                # 替换最后一条（最新的原始 bar）为合成版本
                data[-1] = synthesized_bar
            else:
                # 追加到末尾
                data.append(synthesized_bar)
            # 裁剪到 n_pts 条（去掉最早的）
            if len(data) > n_pts:
                data = data[-n_pts:]
            df = pd.DataFrame(data)
        else:
            rows = list(reversed(rows))
            if len(rows) > n_pts:
                rows = rows[-n_pts:]
            df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])

        display_dir = Path(__file__).parent.parent.parent / "data" / "display"
        display_dir.mkdir(parents=True, exist_ok=True)
        df.to_parquet(display_dir / f"{tf}.parquet", index=False)
        return True, len(df)

    # 浏览模式：原有逻辑（n_pts 窗口）
    df = query_kline(ticker_code, tf, n_pts, day_offset=day_offset)
    if len(df) < 5:
        return False, len(df)
    df["Date"] = pd.to_datetime(df["Date"])
    display_dir = Path(__file__).parent.parent.parent / "data" / "display"
    display_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(display_dir / f"{tf}.parquet", index=False)
    return True, len(df)


def _stock_name_lookup(market: str, code: str) -> str:
    """查询股票名称。"""
    if not code or not code.strip():
        return ""
    try:
        if market == "A股(沪深)":
            full = code + (".SS" if code[0] == "6" else ".SZ")
            full = code + (".SS" if code[0] == "6" else ".SZ")
        elif market == "港股 HK":
            full = code.zfill(4) + ".HK"
        else:
            full = code.upper()
        return yf.Ticker(full).info.get("longName") or ""
    except Exception as e:
        logger.debug(f"Stock name lookup failed for {full}: {e}")
        return ""
