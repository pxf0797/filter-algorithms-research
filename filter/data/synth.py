"""
级联合成模块 — 回测级联 K 线合成

从 ``data/loader.py`` 拆分，处理从细粒度周期合成粗粒度周期 K 线的逻辑。
与 Parquet 显示缓存写入配合工作。
"""

import re as _re
import numpy as np
import pandas as pd
from datetime import datetime, timezone as _dt_timezone, timedelta as _dt_timedelta
from functools import lru_cache as _lru_cache
from pathlib import Path
from loguru import logger
from typing import Optional as _Optional

import json as _json

from filter.shared.constants import ALL_TFS

# 模块级缓存：避免逐 bar 重复写入相同的 parquet 数据
# key = (ticker_code, cutoff_date, n_pts_hash) → last results dict
_synth_cache_state: dict = {}


# ═══════════════════════════════════════════════════════════════
# 时区工具
# ═══════════════════════════════════════════════════════════════

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
    修正位于 _needs_synthesis（使用 ``cutoff_dt > last_ts`` 而非 ``>= period_start``）。

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


# ═══════════════════════════════════════════════════════════════
# DB 查询
# ═══════════════════════════════════════════════════════════════

@_lru_cache(maxsize=128)
def _query_tf_from_db(ticker_code: str, tf: str, cutoff_date: str, n_pts: int) -> list:
    """查询指定周期在截止日期前的最近 n_pts 条已完成 K 线（LRU 缓存）。

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
    from data.db import get_conn
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


@_lru_cache(maxsize=128)
def _query_tf_for_period(ticker_code: str, tf: str, period_start: str, period_end: str) -> list:
    """查询指定周期在时间区间 [period_start, period_end] 内的所有 K 线（LRU 缓存）。

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
    from data.db import get_conn
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


def clear_query_cache():
    """清除 DB 查询的 LRU 缓存。

    用于测试确保缓存状态独立，以及数据库更新后强制重新查询。
    """
    _query_tf_from_db.cache_clear()
    _query_tf_for_period.cache_clear()


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


# ═══════════════════════════════════════════════════════════════
# 合成逻辑
# ═══════════════════════════════════════════════════════════════

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

    # Proper timezone conversion for cross-timezone synthesis.
    finer_sample = _query_tf_from_db(ticker_code, finer_tf, cutoff_date, 1)
    finer_tz = _get_tz_suffix(finer_sample[0]["Date"]) if finer_sample else ""

    if finer_tz:
        tz_obj = _offset_to_tz(finer_tz)
        cutoff_ts = pd.Timestamp(cutoff_date)
        cutoff_in_finer = cutoff_ts.tz_convert(tz_obj)
        actual_in_finer = actual_start.tz_localize(tz_obj)
        period_start_str = actual_in_finer.isoformat()
        period_end_str = cutoff_in_finer.isoformat()
    else:
        period_start_str = actual_start.isoformat()
        period_end_str = cutoff_dt.isoformat()

    finer_db_bars = _query_tf_for_period(ticker_code, finer_tf, period_start_str, period_end_str)

    # Merge finer_tf's synthesized bar if within range.
    all_finer_bars = list(finer_db_bars)
    if finer_synth_bar is not None:
        try:
            synth_ts = _ensure_tz_naive(pd.Timestamp(finer_synth_bar["Date"]))
            if actual_start <= synth_ts <= cutoff_dt:
                if all_finer_bars:
                    last_db_ts = _ensure_tz_naive(pd.Timestamp(all_finer_bars[-1]["Date"]))
                    if synth_ts >= last_db_ts:
                        all_finer_bars[-1] = finer_synth_bar
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

    # Cross-period filter
    if target_tf in ("1分钟", "5分钟", "15分钟", "60分钟"):
        period_start_str = cutoff_dt.strftime("%Y-%m-%d")
        all_finer_bars = [b for b in all_finer_bars
                          if b["Date"][:10] == period_start_str]
    elif target_tf == "日线":
        period_start_str = cutoff_dt.strftime("%Y-%m-%d")
        all_finer_bars = [b for b in all_finer_bars
                          if b["Date"][:10] == period_start_str]
    elif target_tf == "周线":
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

    if len(all_finer_bars) == 0:
        return None

    synth_date = _format_synth_date(cutoff_date, target_tf, db_rows)
    return _aggregate_bars(all_finer_bars, synth_date)


def _build_output_df(db_rows: list, synthesized_bar: _Optional[dict], n_pts: int,
                     truncate: bool = True) -> pd.DataFrame:
    """合并 DB 行与可选的合成 K 线，返回 DataFrame。

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
        返回 DataFrame 的最大行数（仅当 truncate=True 时生效）。
    truncate : bool
        是否截断到 n_pts 行。回测预同步模式设 False 以存储全量数据。

    Returns
    -------
    pd.DataFrame
        包含 Date/Open/High/Low/Close/Volume 列的 DataFrame。
    """
    data = list(db_rows)
    if synthesized_bar is not None:
        if data:
            data[-1] = synthesized_bar
        else:
            data.append(synthesized_bar)
    if truncate and len(data) > n_pts:
        data = data[-n_pts:]
    return pd.DataFrame(data, columns=["Date", "Open", "High", "Low", "Close", "Volume"])


def _write_parquet(tf: str, df: pd.DataFrame, ticker_code: str = "", *, output_dir: str | None = None) -> bool:
    """将 DataFrame 写入时间分区 parquet 文件。

    写入 ``data/display/{ticker_code}/{YYYY}/{MM}/{ticker_code}_{tf}.parquet``。

    Parameters
    ----------
    tf : str
        周期名称，用于确定文件名。
    df : pd.DataFrame
        要写入的 DataFrame 数据。
    ticker_code : str
        股票代码，用于隔离不同 ticker 的显示缓存文件。
    output_dir : str | None
        测试用可选参数。指定输出根目录，替代 ``__file__`` 推导的路径。
        生产代码不应传递此参数。

    Returns
    -------
    bool
        写入成功返回 True，失败返回 False。
    """
    try:
        if output_dir is not None:
            display_root = Path(output_dir) / "data" / "display"
        else:
            display_root = Path(__file__).parent.parent.parent / "data" / "display"
        now = datetime.now()
        parquet_path = (
            display_root / ticker_code / f"{now.year:04d}" / f"{now.month:02d}"
            / f"{ticker_code}_{tf}.parquet"
        )
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        # Normalise Date column to tz-naive strings so pd.to_datetime can parse
        # consistently on read (mixed tz-aware / naive rows break format inference).
        if "Date" in df.columns and len(df) > 0:
            try:
                df = df.copy()
                df["Date"] = df["Date"].astype(str).str.replace(
                    r"[+-]\d{2}:\d{2}$", "", regex=True
                ).str.rstrip("Z")
            except Exception:
                pass
        df.to_parquet(parquet_path, index=False)
        # Inline version save (avoid circular import from data.loader)
        try:
            _mtime = parquet_path.stat().st_mtime
            _n_rows = len(df)
            _vp = parquet_path.with_suffix(".version.json")
            _vp.write_text(_json.dumps({"mtime": _mtime, "rows": _n_rows}))
        except Exception:
            pass
        return True
    except Exception as e:
        logger.warning(f"Failed to write parquet for {tf}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# 级联合成主入口
# ═══════════════════════════════════════════════════════════════

def _sync_all_cascading(ticker_code: str, tfs: list, cutoff_date: str,
                         min_tf: str, n_pts: int = 120,
                         sync_all: bool = False) -> dict:
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
    sync_all : bool, default False
        是否预同步全量数据（回测模式用）。为 True 时从 DB 拉取所有历史数据且不
        在 _build_output_df 中截断，由下游按 bar 自行窗口化。

    Returns
    -------
    dict
        键为周期名称，值为 bool（parquet 文件是否写入成功）。
    """
    results: dict = {}
    synth_cache: dict = {}

    # P0-1: module-level cache — skip re-synthesis when cutoff_date + n_pts unchanged
    # In sync_all mode, bypass cache because the output differs (full vs truncated).
    _n_pts_repr = tuple(n_pts[tf] if isinstance(n_pts, dict) else n_pts
                        for tf in tfs) if isinstance(n_pts, dict) else str(n_pts)
    cache_key = (ticker_code, cutoff_date, _n_pts_repr)
    if not sync_all and cache_key == _synth_cache_state.get("last_key"):
        logger.debug(f"[cascading] cache hit for {ticker_code} @ {cutoff_date}")
        return dict(_synth_cache_state.get("last_result", {}))

    # In sync_all mode, use a large fetch limit to pull all historical bars
    _FETCH_LIMIT = 100_000 if sync_all else None

    for tf in tfs:
        # Resolve per-TF n_pts
        tf_n_pts = n_pts[tf] if isinstance(n_pts, dict) else n_pts
        # sync_all: fetch all bars, not just n_pts
        fetch_limit = _FETCH_LIMIT if sync_all else tf_n_pts

        db_rows = _query_tf_from_db(ticker_code, tf, cutoff_date, fetch_limit)
        if not db_rows:
            logger.debug(f"[cascading] {tf}: no DB data, skip")
            results[tf] = False
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

        combined = _build_output_df(db_rows, synthesized_bar, tf_n_pts,
                                    truncate=not sync_all)
        ok = _write_parquet(tf, combined, ticker_code)
        results[tf] = ok

        synth_cache[tf] = {
            "synth_bar": synthesized_bar,
            "db_sample_ts": db_rows[0]["Date"] if db_rows else "",
        }

    # P1-2: log summary
    success_count = sum(1 for v in results.values() if v)
    if success_count < len(tfs):
        logger.warning(f"[cascading] partial success: {success_count}/{len(tfs)} TFs written")
    else:
        logger.debug(f"[cascading] done: all {success_count} TFs written")

    # P0-1: save cache state
    if success_count == len(tfs):
        _synth_cache_state["last_key"] = cache_key
        _synth_cache_state["last_result"] = dict(results)
    return results
