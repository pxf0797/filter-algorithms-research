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
# 周期层级定义（与 components/sidebar.py 保持一致）
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


def _sync_to_display(ticker_code: str, tf: str, day_offset: int = 0, n_pts: int = 120,
                     cutoff_date: Optional[str] = None) -> Tuple[bool, int]:
    """同步数据到 display parquet。

    cutoff_date=None: 浏览模式，取最新 n_pts 条（支持 day_offset 日期偏移）
    cutoff_date=YYYY-MM-DD: 回测模式，取截止到 cutoff_date 的最后 n_pts 条（日期对齐）
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
        if rows:
            rows.reverse()  # DESC → ASC
            df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
            display_dir = Path(__file__).parent.parent.parent / "data" / "display"
            display_dir.mkdir(parents=True, exist_ok=True)
            df.to_parquet(display_dir / f"{tf}.parquet", index=False)
            return True, len(df)
        return False, 0

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


# ═══════════════════════════════════════════════════════════════
# Cascading Bar Synthesis (v2) — 回测级联合成
# ═══════════════════════════════════════════════════════════════

import re as _re
from typing import Optional as _Optional
from datetime import timezone as _dt_timezone, timedelta as _dt_timedelta

def _offset_to_tz(offset_str: str):
    """Convert a UTC offset string like '-04:00' or '+08:00' to a datetime.timezone."""
    if not offset_str or offset_str == 'Z':
        return _dt_timezone.utc
    sign = 1 if offset_str[0] == '+' else -1
    h, m = map(int, offset_str[1:].split(':'))
    return _dt_timezone(_dt_timedelta(hours=sign * h, minutes=sign * m))

def _ensure_tz_naive(ts):
    """Strip timezone from pd.Timestamp, keeping wall-clock time unchanged."""
    if hasattr(ts, 'tz') and ts.tz is not None:
        return ts.tz_localize(None)
    return ts

def _get_tz_suffix(ts_str: str) -> str:
    """Extract timezone suffix from ISO timestamp string. Returns '+08:00', 'Z', or ''."""
    if not ts_str:
        return ""
    if '+' in ts_str:
        return ts_str[ts_str.index('+'):]
    if ts_str.endswith('Z'):
        return 'Z'
    m = _re.search(r'-\d{2}:\d{2}$', ts_str)
    return m.group(0) if m else ''

def _format_synth_date(cutoff_date: str, tf: str, db_rows: list) -> str:
    """Format synthesized bar's Date to match this TF's DB native format.
    Minute TFs keep timezone suffix; daily+ TFs drop timezone."""
    dt = pd.Timestamp(cutoff_date)
    if dt.tz is not None:
        dt = dt.tz_localize(None)
    base = dt.isoformat()
    if db_rows:
        tz = _get_tz_suffix(db_rows[0].get("Date", ""))
        if tz:
            return base + tz
    return base

def _get_period_start_ts(ts, tf: str):
    """Compute the start of the NEXT period after the last completed bar.
    ts MUST be tz-naive pd.Timestamp.
    Minute TFs: +1 minute. Daily/Weekly/Monthly/Quarterly: +1 day."""
    ts = _ensure_tz_naive(pd.Timestamp(ts))
    if tf in ("5分钟", "15分钟", "60分钟"):
        return ts + pd.Timedelta(minutes=1)
    elif tf in ("日线", "周线", "月线", "季线"):
        return (ts + pd.Timedelta(days=1)).normalize()
    else:
        return ts + pd.Timedelta(minutes=1)

def _get_query_start_for_synthesis(last_completed_ts, tf: str):
    """Return the synthesis query start timestamp (= last complete bar's ts).

    The synthesis window is [last_ts, cutoff].  For minute TFs separated by
    overnight/weekend gaps the query may pull in bars from the previous completed
    period — those bars are from an already-closed period and their impact is
    negligible (at most ~30 min of data).  The key correctness fix is in
    _needs_synthesis (``cutoff_dt > last_ts``, not ``>= period_start``).
    """
    return _ensure_tz_naive(pd.Timestamp(last_completed_ts))

def _query_tf_from_db(ticker_code: str, tf: str, cutoff_date: str, n_pts: int) -> list:
    """Query last n_pts completed bars for tf <= cutoff_date. Returns list[dict] in ASCENDING time order."""
    from db import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT ts, open, high, low, close, volume
               FROM kline WHERE ticker=? AND timeframe=? AND ts <= ?
               ORDER BY ts DESC LIMIT ?""",
            (ticker_code, tf, cutoff_date, n_pts),
        ).fetchall()
    if not rows:
        return []
    return [
        {"Date": r[0], "Open": r[1], "High": r[2], "Low": r[3], "Close": r[4], "Volume": r[5]}
        for r in reversed(rows)
    ]

def _query_tf_for_period(ticker_code: str, tf: str, period_start: str, period_end: str) -> list:
    """Query ALL bars for tf in [period_start, period_end] (for synthesis, no n_pts limit).
    Returns list[dict] in ASCENDING time order."""
    from db import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT ts, open, high, low, close, volume
               FROM kline WHERE ticker=? AND timeframe=?
               AND ts >= ? AND ts <= ?
               ORDER BY ts ASC""",
            (ticker_code, tf, period_start, period_end),
        ).fetchall()
    return [
        {"Date": r[0], "Open": r[1], "High": r[2], "Low": r[3], "Close": r[4], "Volume": r[5]}
        for r in rows
    ]

def _find_immediate_finer_tf(tf: str, tfs: list) -> _Optional[str]:
    """Find the immediately finer TF from the available tfs list."""
    current_idx = ALL_TFS.index(tf)
    for candidate_tf in reversed(tfs):
        if ALL_TFS.index(candidate_tf) < current_idx:
            return candidate_tf
    return None

def _needs_synthesis(tf: str, db_rows: list, cutoff_date: str) -> bool:
    """Check if cutoff_date is after the last complete bar, requiring bar synthesis.

    The original logic checked ``cutoff >= period_start`` (start of the *next* period),
    which works for minute-level TFs (periods are minutes apart) but fails for daily+
    TFs.  Example: last daily bar at 2026-07-02 00:00, cutoff at 2026-07-02 15:45.
    The next daily period starts at 2026-07-03 00:00, so 15:45 >= next‑midnight is
    False → synthesis never triggers, even though we have hours of intraday data
    after the last complete daily bar.

    Fixed to ``cutoff > last_ts``: if there is *any* data beyond the timestamp of
    the last complete bar we should synthesize a partial bar, regardless of whether
    a full period boundary has been crossed."""
    if not db_rows:
        return False
    last_ts = _ensure_tz_naive(pd.Timestamp(db_rows[-1]["Date"]))
    cutoff_dt = _ensure_tz_naive(pd.Timestamp(cutoff_date))
    return cutoff_dt >= last_ts

def _aggregate_bars(finer_bars: list, synth_date: str) -> dict:
    """Aggregate finer-TF bars into one coarser-TF bar. O=first, H=max, L=min, C=last, V=sum."""
    df = pd.DataFrame(finer_bars)
    return {
        "Date": synth_date,
        "Open": float(df["Open"].iloc[0]),
        "High": float(df["High"].max()),
        "Low": float(df["Low"].min()),
        "Close": float(df["Close"].iloc[-1]),
        "Volume": float(df["Volume"].sum()),
    }

def _synthesize_incomplete_bar(target_tf: str, db_rows: list, cutoff_date: str,
                                ticker_code: str, finer_tf: str,
                                finer_synth_bar: _Optional[dict]) -> _Optional[dict]:
    """Synthesize one incomplete bar for target_tf from finer_tf data.
    Queries DB for finer_tf bars in the synthesis period, then aggregates."""
    if not db_rows:
        return None

    last_ts = _ensure_tz_naive(pd.Timestamp(db_rows[-1]["Date"]))
    query_start = _get_query_start_for_synthesis(last_ts, target_tf)
    cutoff_dt = _ensure_tz_naive(pd.Timestamp(cutoff_date))

    # Daily synthesis from 60min: start from market open 09:30
    actual_start = query_start
    if target_tf == "日线" and finer_tf == "60分钟":
        try:
            market_open = query_start.replace(hour=9, minute=30, second=0, microsecond=0)
            if query_start < market_open < cutoff_dt:
                actual_start = market_open
        except Exception:
            pass

    # ★ BUGFIX v3: Proper timezone conversion for cross-timezone synthesis.
    # v1/v2 blindly stripped & re-appended tz offset strings, which breaks when
    # min_tf and finer_tf are in different timezones (e.g. A-stock +08:00 vs
    # US stock -04:00). "14:45+08:00" stripped to "14:45" then suffixed with
    # "-04:00" yields "14:45-04:00", a completely different absolute time.
    # Fix: use pd.Timestamp.tz_convert() to do a real timezone conversion,
    # then format the result in finer_tf's timezone for the SQL query.
    finer_sample = _query_tf_from_db(ticker_code, finer_tf, cutoff_date, 1)
    finer_tz = _get_tz_suffix(finer_sample[0]["Date"]) if finer_sample else ""

    if finer_tz:
        # finer_tf has timezone → convert cutoff & actual_start to that tz
        tz_obj = _offset_to_tz(finer_tz)
        cutoff_ts = pd.Timestamp(cutoff_date)                      # tz-aware
        cutoff_in_finer = cutoff_ts.tz_convert(tz_obj)             # real conversion
        actual_in_finer = actual_start.tz_localize(tz_obj)         # naive → tz-aware
        period_start_str = actual_in_finer.isoformat()
        period_end_str = cutoff_in_finer.isoformat()
    else:
        # finer_tf has no timezone (daily+) → use tz-naive strings as-is
        period_start_str = actual_start.isoformat()
        period_end_str = cutoff_dt.isoformat()

    finer_db_bars = _query_tf_for_period(ticker_code, finer_tf, period_start_str, period_end_str)

    # Merge finer_tf's synthesized bar if within range.
    # When the synth bar falls after the last finer-DB bar it represents a more
    # up-to-date (partial) view of the same period — **replace** the DB bar so
    # we don't aggregate competing OHLC values for the same period.
    all_finer_bars = list(finer_db_bars)
    if finer_synth_bar is not None:
        try:
            synth_ts = _ensure_tz_naive(pd.Timestamp(finer_synth_bar["Date"]))
            if actual_start <= synth_ts <= cutoff_dt:
                if all_finer_bars:
                    last_db_ts = _ensure_tz_naive(pd.Timestamp(all_finer_bars[-1]["Date"]))
                    if synth_ts > last_db_ts:
                        all_finer_bars[-1] = finer_synth_bar   # replace same-period DB bar
                    else:
                        existing_ts = {_ensure_tz_naive(pd.Timestamp(b["Date"])) for b in all_finer_bars}
                        if synth_ts not in existing_ts:
                            all_finer_bars.append(finer_synth_bar)
                            all_finer_bars.sort(key=lambda b: _ensure_tz_naive(pd.Timestamp(b["Date"])))
                else:
                    all_finer_bars.append(finer_synth_bar)
        except Exception:
            pass

    # ★ Cross-period filter: the query window [last_ts, cutoff] can span
    # overnight / weekend / month-end gaps and pull in bars from already-
    # completed periods.  For each target TF we compute the start of the
    # *current* (incomplete) period that contains cutoff and drop any
    # finer-TF bar whose date falls before it.
    if target_tf in ("1分钟", "5分钟", "15分钟", "60分钟"):
        # minute TFs  →  keep only bars on the same calendar day as cutoff
        period_start_str = cutoff_dt.strftime("%Y-%m-%d")  # "2026-07-02"
        all_finer_bars = [b for b in all_finer_bars
                          if b["Date"][:10] == period_start_str]
    elif target_tf == "日线":
        # daily bar  →  same day
        period_start_str = cutoff_dt.strftime("%Y-%m-%d")
        all_finer_bars = [b for b in all_finer_bars
                          if b["Date"][:10] == period_start_str]
    elif target_tf == "周线":
        # current week starts on the most recent Monday
        week_start = cutoff_dt - pd.Timedelta(days=cutoff_dt.weekday())
        period_start_str = week_start.strftime("%Y-%m-%d")
        all_finer_bars = [b for b in all_finer_bars
                          if b["Date"][:10] >= period_start_str]
    elif target_tf == "月线":
        period_start_str = cutoff_dt.strftime("%Y-%m") + "-01"
        all_finer_bars = [b for b in all_finer_bars
                          if b["Date"][:10] >= period_start_str]
    elif target_tf == "季线":
        q = (cutoff_dt.month - 1) // 3
        q_start = cutoff_dt.replace(month=q * 3 + 1, day=1)
        period_start_str = q_start.strftime("%Y-%m-%d")
        all_finer_bars = [b for b in all_finer_bars
                          if b["Date"][:10] >= period_start_str]
    # (1分钟 falls through to the minute-TF branch above)

    if len(all_finer_bars) == 0:
        return None

    synth_date = _format_synth_date(cutoff_date, target_tf, db_rows)
    return _aggregate_bars(all_finer_bars, synth_date)

def _build_output_df(db_rows: list, synthesized_bar: _Optional[dict], n_pts: int) -> pd.DataFrame:
    """Merge DB rows + optional synthesized bar, return DataFrame with exactly n_pts rows.

    When a synthesized bar is present it **replaces** the last DB row (they
    represent the same period — the DB row is a retrospectively-complete bar
    downloaded after market close, the synth row is the partial view up to
    ``cutoff_date``).  Appending both would keep future knowledge in the
    backtest window.
    """
    data = list(db_rows)
    if synthesized_bar is not None:
        if data:
            data[-1] = synthesized_bar   # replace, not append
        else:
            data.append(synthesized_bar)
    if len(data) > n_pts:
        data = data[-n_pts:]
    return pd.DataFrame(data, columns=["Date", "Open", "High", "Low", "Close", "Volume"])

def _write_parquet(tf: str, df: pd.DataFrame) -> bool:
    """Write DataFrame to data/display/{tf}.parquet. Returns True on success."""
    try:
        display_dir = Path(__file__).parent.parent.parent / "data" / "display"
        display_dir.mkdir(parents=True, exist_ok=True)
        df.to_parquet(display_dir / f"{tf}.parquet", index=False)
        return True
    except Exception as e:
        logger.warning(f"Failed to write parquet for {tf}: {e}")
        return False

def _sync_all_cascading(ticker_code: str, tfs: list, cutoff_date: str,
                         min_tf: str, n_pts: int = 120) -> dict:
    """Cascading synthesis main entry: process TFs finest→coarsest, synthesize incomplete bars.

    Args:
        n_pts: int or dict[str,int]. If int, applied to all TFs. If dict, per-TF n_pts.

    Returns {tf: bool} — True if parquet written successfully."""
    results: dict = {}
    synth_cache: dict = {}

    for tf in tfs:
        # Resolve per-TF n_pts
        tf_n_pts = n_pts[tf] if isinstance(n_pts, dict) else n_pts

        db_rows = _query_tf_from_db(ticker_code, tf, cutoff_date, tf_n_pts)
        if not db_rows:
            logger.debug(f"[cascading] {tf}: no DB data, skip")
            results[tf] = False
            # ★ P1-3 fix: still cache empty entry so coarser TFs can find a finer TF
            synth_cache[tf] = {"synth_bar": None, "db_sample_ts": ""}
            continue

        needs_synth = (
            tf != min_tf
            and _needs_synthesis(tf, db_rows, cutoff_date)
        )

        synthesized_bar = None
        if needs_synth:
            finer_tf = _find_immediate_finer_tf(tf, tfs)
            if finer_tf and finer_tf in synth_cache:
                synthesized_bar = _synthesize_incomplete_bar(
                    target_tf=tf,
                    db_rows=db_rows,
                    cutoff_date=cutoff_date,
                    ticker_code=ticker_code,
                    finer_tf=finer_tf,
                    finer_synth_bar=synth_cache[finer_tf].get("synth_bar"),
                )
                if synthesized_bar:
                    logger.debug(
                        f"[cascading] {tf}: synth bar Date={synthesized_bar['Date']}, "
                        f"O={synthesized_bar['Open']:.2f} C={synthesized_bar['Close']:.2f}"
                    )
            else:
                logger.debug(f"[cascading] {tf}: no finer_tf ({finer_tf}) in cache, skip synth")

        combined = _build_output_df(db_rows, synthesized_bar, tf_n_pts)
        ok = _write_parquet(tf, combined)
        results[tf] = ok

        synth_cache[tf] = {
            "synth_bar": synthesized_bar,
            "db_sample_ts": db_rows[0]["Date"] if db_rows else "",
        }

    # ★ P1-2: log summary so caller can check
    success_count = sum(1 for v in results.values() if v)
    if success_count < len(tfs):
        logger.warning(f"[cascading] partial success: {success_count}/{len(tfs)} TFs written")
    else:
        logger.debug(f"[cascading] done: all {success_count} TFs written")
    return results
