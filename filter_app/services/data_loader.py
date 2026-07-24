"""
数据加载模块 — 负责yfinance数据获取、K线查询/缓存、数据健康检查

无Streamlit依赖，仅基础库 + db模块
"""

import json
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from loguru import logger
from typing import Any, Dict, Optional, Tuple
from db import upsert_kline, query_kline, get_latest_date
from constants import ALL_TFS

# 模块级缓存：避免逐 bar 重复写入相同的 parquet 数据
# key = (ticker_code, cutoff_date, n_pts_hash) → last results dict
_synth_cache_state: dict = {}


# ═══════════════════════════════════════════════════════════════
# Time‑Based Parquet Partitioning  — YYYY/MM directory structure
# ═══════════════════════════════════════════════════════════════

from datetime import datetime
import os as _os


def _partitioned_path(base_dir, ticker: str, tf: str, dt=None):
    """返回时间分区路径: ``{base_dir}/{ticker}/{YYYY}/{MM}/{ticker}_{tf}.parquet``。

    Parameters
    ----------
    base_dir : str or Path
        根目录，通常为 ``data/display``。
    ticker : str
        股票代码。
    tf : str
        周期名称。
    dt : datetime, optional
        分区时间戳，默认为当前时间。

    Returns
    -------
    str
        分区文件路径字符串。
    """
    if dt is None:
        dt = datetime.now()
    return _os.path.join(
        str(base_dir), ticker,
        f"{dt.year:04d}", f"{dt.month:02d}",
        f"{ticker}_{tf}.parquet",
    )


def _resolve_read_path(base_dir, ticker: str, tf: str):
    """读取时先查新分区路径，不存在则扫描历史分区，最后回退旧平铺路径。

    扫描顺序：
      1. 当前月份的分区路径
      2. 任意历史月份的分区路径（遍历 ticker 目录）
      3. 旧平铺路径 ``{ticker}/{tf}.parquet``
      4. 均不存在时返回当前月份分区路径（供调用方判断 ``exists()``）

    Parameters
    ----------
    base_dir : str or Path
        根目录。
    ticker : str
        股票代码。
    tf : str
        周期名称。

    Returns
    -------
    str
        存在的文件路径，或首选分区路径（均不存在时）。
    """
    base_str = str(base_dir)

    # 1. 当前月份分区
    new_path = _partitioned_path(base_str, ticker, tf)
    if _os.path.exists(new_path):
        return new_path

    # 2. 扫描历史分区
    ticker_dir = _os.path.join(base_str, ticker)
    if _os.path.isdir(ticker_dir):
        target = f"{ticker}_{tf}.parquet"
        for root, _dirs, files in _os.walk(ticker_dir):
            if target in files:
                return _os.path.join(root, target)

    # 3. 旧平铺路径
    old_path = _os.path.join(base_str, ticker, f"{tf}.parquet")
    if _os.path.exists(old_path):
        return old_path

    # 4. 不存在，返回首选新路径
    return new_path


def _scan_partitions_for_range(base_dir, ticker: str, tf: str,
                                start_dt, end_dt):
    """按时间范围扫描匹配的分区文件路径列表。

    遍历 ``{base_dir}/{ticker}/`` 下的 ``YYYY/MM`` 子目录，若其年份-月份
    落在 ``[start_dt, end_dt]`` 范围内，则收集对应的 parquet 文件路径。

    Parameters
    ----------
    base_dir : str or Path
        根目录。
    ticker : str
        股票代码。
    tf : str
        周期名称。
    start_dt : datetime
        起始时间（含）。
    end_dt : datetime
        结束时间（含）。

    Returns
    -------
    list[str]
        匹配的分区文件路径列表，按路径排序。
    """
    ticker_dir = _os.path.join(str(base_dir), ticker)
    if not _os.path.isdir(ticker_dir):
        return []

    target = f"{ticker}_{tf}.parquet"
    matched = []
    for root, _dirs, files in _os.walk(ticker_dir):
        if target not in files:
            continue
        # root relative to ticker_dir, e.g. "2026/07"
        rel = _os.path.relpath(root, ticker_dir)
        parts = rel.replace("\\", "/").split("/")
        if len(parts) >= 2:
            try:
                y, m = int(parts[0]), int(parts[1])
                month_start = datetime(y, m, 1)
                # month_end = first day of next month
                if m == 12:
                    month_end = datetime(y + 1, 1, 1)
                else:
                    month_end = datetime(y, m + 1, 1)
                # overlap check: [month_start, month_end) overlaps [start_dt, end_dt]
                if month_start < end_dt and month_end > start_dt:
                    matched.append(_os.path.join(root, target))
            except (ValueError, IndexError):
                continue
    return sorted(matched)


# ═══════════════════════════════════════════════════════════════
# Display Cache Versioning — checksum 校验防脏读
# ═══════════════════════════════════════════════════════════════

def _version_path(parquet_path: Path) -> Path:
    """返回 parquet 文件对应的版本标记文件路径 (.version.json)。"""
    return parquet_path.with_suffix(".version.json")


def _compute_version(parquet_path: Path) -> Dict[str, Any]:
    """计算 display 缓存的版本标记：文件 mtime + 数据行数。

    Parameters
    ----------
    parquet_path : Path
        parquet 文件路径。

    Returns
    -------
    dict
        ``{"mtime": float, "rows": int}``。
    """
    mtime = parquet_path.stat().st_mtime
    df = pd.read_parquet(parquet_path)
    return {"mtime": mtime, "rows": len(df)}


def _save_version(parquet_path: Path) -> None:
    """写入 parquet 后保存版本标记到 ``{parquet}.version.json``。

    Parameters
    ----------
    parquet_path : Path
        已写入的 parquet 文件路径。
    """
    try:
        version = _compute_version(parquet_path)
        vp = _version_path(parquet_path)
        vp.write_text(json.dumps(version))
    except Exception as e:
        logger.warning(f"Failed to save version for {parquet_path}: {e}")


def _is_cache_valid(parquet_path: Path) -> bool:
    """检查 display 缓存的版本标记是否与当前文件状态一致。

    Parameters
    ----------
    parquet_path : Path
        parquet 文件路径。

    Returns
    -------
    bool
        版本一致返回 True，否则返回 False。
    """
    vp = _version_path(parquet_path)
    if not parquet_path.exists():
        return False
    if not vp.exists():
        return False
    try:
        stored = json.loads(vp.read_text())
        current = _compute_version(parquet_path)
        return stored == current
    except Exception:
        return False


def _invalidate_cache(parquet_path: Path) -> None:
    """删除 display 缓存文件及其版本标记。

    Parameters
    ----------
    parquet_path : Path
        parquet 文件路径。
    """
    for p in (parquet_path, _version_path(parquet_path)):
        try:
            if p.exists():
                p.unlink()
        except Exception as e:
            logger.warning(f"Failed to delete {p}: {e}")


def load_display_cache(ticker_code: str, tf: str) -> Optional[pd.DataFrame]:
    """带版本校验的 display 缓存读取。

    优先查找时间分区路径，不存在则回退旧平铺路径。
    读取前比较 checksum（mtime + 行数），不匹配则删除缓存文件并返回 ``None``，
    由调用方触发数据刷新。

    Parameters
    ----------
    ticker_code : str
        股票代码。
    tf : str
        周期名称。

    Returns
    -------
    Optional[pd.DataFrame]
        缓存有效时返回 DataFrame，无效时返回 ``None``。
    """
    display_dir = Path(__file__).parent.parent.parent / "data" / "display"
    resolved = _resolve_read_path(str(display_dir), ticker_code, tf)
    display_path = Path(resolved)
    if not display_path.exists():
        return None
    if not _is_cache_valid(display_path):
        logger.debug(f"Display cache invalid (version mismatch): {display_path}")
        _invalidate_cache(display_path)
        return None
    try:
        return pd.read_parquet(display_path)
    except Exception as e:
        logger.warning(f"Failed to read display cache {display_path}: {e}")
        return None


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
                 force_period: Optional[str] = None) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], Optional[pd.DataFrame], Optional[str], Optional[str], Optional[pd.DatetimeIndex]]:
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


def _sync_to_display(ticker_code: str, tf: str, n_pts: int = 120,
                     cutoff_date: Optional[str] = None) -> Tuple[bool, int]:
    """同步数据到 display parquet 文件。

    浏览模式（cutoff_date=None）：取最新 n_pts 条。
    回测模式（cutoff_date=YYYY-MM-DD）：取截止到 cutoff_date 的最后 n_pts 条，日期对齐。

    Parameters
    ----------
    ticker_code : str
        股票代码。
    tf : str
        周期名称。
        日期偏移天数，用于浏览模式。
    n_pts : int, default 120
        需要的数据点数。
    cutoff_date : Optional[str], default None
        回测截止日期，格式 "YYYY-MM-DD"。为 None 时使用浏览模式。

    Returns
    -------
    Tuple[bool, int]
        (是否成功, 写入的数据条数)。
    """
    display_base = Path(__file__).parent.parent.parent / "data" / "display" / ticker_code
    display_base.mkdir(parents=True, exist_ok=True)

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
            df.to_parquet(display_base / f"{tf}.parquet", index=False)
            _save_version(display_base / f"{tf}.parquet")
            return True, len(df)
        return False, 0

    # 浏览模式：原有逻辑（n_pts 窗口）
    df = query_kline(ticker_code, tf, n_pts, day_offset=0)
    if len(df) < 5:
        return False, len(df)
    df["Date"] = pd.to_datetime(df["Date"])
    df.to_parquet(display_base / f"{tf}.parquet", index=False)
    _save_version(display_base / f"{tf}.parquet")
    return True, len(df)


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

def _offset_to_tz(offset_str: str) -> _dt_timezone:
    """将 UTC 偏移字符串转换为 datetime.timezone 对象。

    Parameters
    ----------
    offset_str : str
        UTC 偏移字符串，如 '-04:00'、'+08:00' 或 'Z'。

    Returns
    -------
    datetime.timezone
        对应的时区对象。
    """
    if not offset_str or offset_str == 'Z':
        return _dt_timezone.utc
    sign = 1 if offset_str[0] == '+' else -1
    h, m = map(int, offset_str[1:].split(':'))
    return _dt_timezone(_dt_timedelta(hours=sign * h, minutes=sign * m))

def _ensure_tz_naive(ts) -> pd.Timestamp:
    """去除 pd.Timestamp 的时区信息，保持挂钟时间不变。

    Parameters
    ----------
    ts : pd.Timestamp
        可能带时区的 Timestamp 对象。

    Returns
    -------
    pd.Timestamp
        不带时区的 Timestamp 对象（若输入无时区则原样返回）。
    """
    if hasattr(ts, 'tz') and ts.tz is not None:
        return ts.tz_localize(None)
    return ts

def _get_tz_suffix(ts_str: str) -> str:
    """从 ISO 时间戳字符串中提取时区后缀。

    Parameters
    ----------
    ts_str : str
        ISO 格式的时间戳字符串。

    Returns
    -------
    str
        时区后缀，如 '+08:00'、'Z'，无时区时返回空字符串。
    """
    if not ts_str:
        return ""
    if '+' in ts_str:
        return ts_str[ts_str.index('+'):]
    if ts_str.endswith('Z'):
        return 'Z'
    m = _re.search(r'-\d{2}:\d{2}$', ts_str)
    return m.group(0) if m else ''

def _format_synth_date(cutoff_date: str, tf: str, db_rows: list) -> str:
    """格式化合成 K 线的日期字符串，使其与对应周期的 DB 存储格式一致。

    分钟级周期保留时区后缀，日线及以上周期去除时区。

    Parameters
    ----------
    cutoff_date : str
        回测截止日期字符串。
    tf : str
        周期名称。
    db_rows : list
        DB 查询结果行列表，用于参考日期格式。

    Returns
    -------
    str
        格式化后的日期字符串。
    """
    dt = pd.Timestamp(cutoff_date)
    if dt.tz is not None:
        dt = dt.tz_localize(None)
    base = dt.isoformat()
    if db_rows:
        tz = _get_tz_suffix(db_rows[0].get("Date", ""))
        if tz:
            return base + tz
    return base

def _get_period_start_ts(ts, tf: str) -> pd.Timestamp:
    """计算上一个完整 K 线之后的下一个周期的起始时间。

    ts 必须是不带时区的 pd.Timestamp。
    分钟级周期：+1 分钟。日线及以上周期：+1 天。

    Parameters
    ----------
    ts : pd.Timestamp
        上一个完整 K 线的时间戳（必须 tz-naive）。
    tf : str
        周期名称。

    Returns
    -------
    pd.Timestamp
        下一个周期的起始时间戳。
    """
    ts = _ensure_tz_naive(pd.Timestamp(ts))
    if tf in ("5分钟", "15分钟", "60分钟"):
        return ts + pd.Timedelta(minutes=1)
    elif tf in ("日线", "周线", "月线", "季线"):
        return (ts + pd.Timedelta(days=1)).normalize()
    else:
        return ts + pd.Timedelta(minutes=1)

def _get_query_start_for_synthesis(last_completed_ts, tf: str):
    """返回合成查询的起始时间戳（即最后一个完整 K 线的时间戳）。

    合成窗口为 [last_ts, cutoff]。对于分钟级周期，跨夜/周末间隔可能导致查询
    拉入前一完整周期的数据，但其影响可忽略（最多约 30 分钟数据）。核心正确性
    修正位于 _needs_synthesis（使用 `cutoff_dt > last_ts` 而非 `>= period_start`）。

    Parameters
    ----------
    last_completed_ts : pd.Timestamp
        最后一个完整 K 线的时间戳。
    tf : str
        周期名称。

    Returns
    -------
    pd.Timestamp
        不带时区的查询起始时间戳。
    """
    return _ensure_tz_naive(pd.Timestamp(last_completed_ts))

def _query_tf_from_db(ticker_code: str, tf: str, cutoff_date: str, n_pts: int) -> list:
    """查询指定周期在截止日期前的最近 n_pts 条已完成 K 线。

    Parameters
    ----------
    ticker_code : str
        股票代码。
    tf : str
        周期名称。
    cutoff_date : str
        截止日期字符串。
    n_pts : int
        需要的数据条数。

    Returns
    -------
    list
        dict 列表，每项包含 Date/Open/High/Low/Close/Volume 字段，按时间升序排列。
    """
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
    """查询指定周期在时间区间 [period_start, period_end] 内的所有 K 线（用于合成，无 n_pts 限制）。

    Parameters
    ----------
    ticker_code : str
        股票代码。
    tf : str
        周期名称。
    period_start : str
        区间起始时间字符串。
    period_end : str
        区间结束时间字符串。

    Returns
    -------
    list
        dict 列表，每项包含 Date/Open/High/Low/Close/Volume 字段，按时间升序排列。
    """
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
    """从可用的周期列表中查找紧邻的更细粒度周期。

    Parameters
    ----------
    tf : str
        当前周期名称。
    tfs : list
        可用的周期名称列表。

    Returns
    -------
    Optional[str]
        紧邻的更细粒度周期名称，若不存在则返回 None。
    """
    current_idx = ALL_TFS.index(tf)
    for candidate_tf in reversed(tfs):
        if ALL_TFS.index(candidate_tf) < current_idx:
            return candidate_tf
    return None

def _needs_synthesis(tf: str, db_rows: list, cutoff_date: str) -> bool:
    """判断 cutoff_date 是否在最后一条完整 K 线之后，从而需要合成。

    原逻辑检查 ``cutoff >= period_start``（下一个周期的起点），这对分钟级周期有效
    但对日线及以上周期失效。例如：最后日线 K 线在 2026-07-02 00:00，cutoff 在
    2026-07-02 15:45。下一个日周期从 2026-07-03 00:00 开始，因此 15:45 >= 次日零点
    为 False → 合成永远不会触发，即使最后日线后已有数小时的日内数据。

    修正为 ``cutoff > last_ts``：只要最后完整 K 线的时间戳之后有任何数据，就应合成
    一根部分 K 线，无论是否跨过完整的周期边界。

    Parameters
    ----------
    tf : str
        周期名称。
    db_rows : list
        DB 查询结果行列表。
    cutoff_date : str
        回测截止日期字符串。

    Returns
    -------
    bool
        是否需要合成。
    """
    if not db_rows:
        return False
    last_ts = _ensure_tz_naive(pd.Timestamp(db_rows[-1]["Date"]))
    cutoff_dt = _ensure_tz_naive(pd.Timestamp(cutoff_date))
    return cutoff_dt >= last_ts

def _aggregate_bars(finer_bars: list, synth_date: str) -> dict:
    """将细粒度周期的多条 K 线聚合成一条粗粒度周期的 K 线。

    O=第一条的开盘价, H=最高价的最大值, L=最低价的最小值, C=最后一条的收盘价, V=成交量求和。

    Parameters
    ----------
    finer_bars : list
        细粒度周期的 K 线 dict 列表。
    synth_date : str
        合成 K 线的日期字符串。

    Returns
    -------
    dict
        包含 Date/Open/High/Low/Close/Volume 字段的字典。
    """
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
    """从更细粒度周期的数据合成目标周期的一条未完成 K 线。

    从 DB 查询合成时间窗口内的细粒度 K 线，然后聚合为粗粒度 K 线。
    处理跨时区的时区转换（v3 BUGFIX），以及跨周期边界的数据过滤。

    Parameters
    ----------
    target_tf : str
        目标周期名称（需合成的粗粒度周期）。
    db_rows : list
        目标周期的 DB 查询结果行列表。
    cutoff_date : str
        回测截止日期字符串。
    ticker_code : str
        股票代码。
    finer_tf : str
        更细粒度周期名称。
    finer_synth_bar : Optional[dict]
        细粒度周期的已合成 K 线（若有）。

    Returns
    -------
    Optional[dict]
        合成的 K 线字典（含 Date/Open/High/Low/Close/Volume），无可合成数据时返回 None。
    """
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
        except Exception as e:
            logger.error(f"Failed to determine market_open for {target_tf}/{finer_tf}: {e}")
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
                    if synth_ts >= last_db_ts:
                        all_finer_bars[-1] = finer_synth_bar   # replace same-period DB bar
                    else:
                        existing_ts = {_ensure_tz_naive(pd.Timestamp(b["Date"])) for b in all_finer_bars}
                        if synth_ts not in existing_ts:
                            all_finer_bars.append(finer_synth_bar)
                            all_finer_bars.sort(key=lambda b: _ensure_tz_naive(pd.Timestamp(b["Date"])))
                else:
                    all_finer_bars.append(finer_synth_bar)
        except Exception as e:
            logger.error(f"Failed to merge synth_bar for {ticker_code}/{target_tf}: {e}")
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
    """合并 DB 行与可选的合成 K 线，返回恰好 n_pts 行的 DataFrame。

    当合成 K 线存在时，它会**替换**最后一条 DB 行（二者代表同一周期——DB 行是收盘后
    下载的复盘完整 K 线，合成行是截止到 cutoff_date 的部分视图）。同时追加两者会
    在回测窗口中引入未来信息。

    Parameters
    ----------
    db_rows : list
        DB 查询结果行列表，每项为 dict 格式。
    synthesized_bar : Optional[dict]
        合成的 K 线字典，为 None 时不替换。
    n_pts : int
        返回 DataFrame 的最大行数。

    Returns
    -------
    pd.DataFrame
        包含 Date/Open/High/Low/Close/Volume 列的 DataFrame。
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

def _write_parquet(tf: str, df: pd.DataFrame, ticker_code: str = "") -> bool:
    """将 DataFrame 写入 data/display/{ticker_code}/{tf}.parquet 文件。

    Parameters
    ----------
    tf : str
        周期名称，用于确定文件名。
    df : pd.DataFrame
        要写入的 DataFrame 数据。
    ticker_code : str
        股票代码，用于隔离不同 ticker 的显示缓存文件。

    Returns
    -------
    bool
        写入成功返回 True，失败返回 False。
    """
    try:
        display_dir = (
            Path(__file__).parent.parent.parent / "data" / "display" / ticker_code
        )
        display_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = display_dir / f"{tf}.parquet"
        df.to_parquet(parquet_path, index=False)
        _save_version(parquet_path)
        return True
    except Exception as e:
        logger.warning(f"Failed to write parquet for {tf}: {e}")
        return False

def _sync_all_cascading(ticker_code: str, tfs: list, cutoff_date: str,
                         min_tf: str, n_pts: int = 120) -> dict:
    """级联合成主入口：从细到粗处理周期列表，为每个周期合成未完成 K 线。

    处理流程：按周期列表从细到粗遍历，对每个周期从 DB 获取数据，判断是否需要合成，
    如需要则利用更细粒度周期的数据进行 K 线合成，最终写入 parquet 文件。

    Parameters
    ----------
    ticker_code : str
        股票代码。
    tfs : list
        要处理的周期名称列表（按从细到粗顺序）。
    cutoff_date : str
        回测截止日期字符串。
    min_tf : str
        最细粒度周期名称（该周期不做合成）。
    n_pts : int or dict[str, int], default 120
        每个周期需要的数据点数。若为 int 则统一应用于所有周期；
        若为 dict，则键为周期名、值为对应的 n_pts。

    Returns
    -------
    dict
        键为周期名称，值为 bool（parquet 文件是否写入成功）。
    """
    results: dict = {}
    synth_cache: dict = {}

    # P0-1: module-level cache — skip re-synthesis when cutoff_date + n_pts unchanged
    _n_pts_repr = tuple(n_pts[tf] if isinstance(n_pts, dict) else n_pts
                        for tf in tfs) if isinstance(n_pts, dict) else str(n_pts)
    cache_key = (ticker_code, cutoff_date, _n_pts_repr)
    if cache_key == _synth_cache_state.get("last_key"):
        logger.debug(f"[cascading] cache hit for {ticker_code} @ {cutoff_date}")
        return dict(_synth_cache_state.get("last_result", {}))

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
        ok = _write_parquet(tf, combined, ticker_code)
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

    # P0-1: save cache state for next bar iteration
    if success_count == len(tfs):
        _synth_cache_state["last_key"] = cache_key
        _synth_cache_state["last_result"] = dict(results)
    return results
