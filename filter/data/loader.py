"""
数据加载模块 — display parquet 缓存读写 + 对外委托

从原 ~1147 行拆分为:
- fetcher.py: yfinance 下载 + 清洗
- synth.py: 级联合成
- loader.py (本文件): 公共加载 API + 版本校验 + 分区路径

向后兼容: re-export fetcher 和 synth 的公开函数。
"""

import json
from datetime import datetime
from pathlib import Path
import os as _os

import pandas as pd
import pyarrow.parquet as pq
from loguru import logger
from typing import Any, Dict, Optional, Tuple
from filter.data.db import get_conn, query_kline


# ═══════════════════════════════════════════════════════════════
# Time‑Based Parquet Partitioning  — YYYY/MM directory structure
# ═══════════════════════════════════════════════════════════════


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
        rel = _os.path.relpath(root, ticker_dir)
        parts = rel.replace("\\", "/").split("/")
        if len(parts) >= 2:
            try:
                y, m = int(parts[0]), int(parts[1])
                month_start = datetime(y, m, 1)
                if m == 12:
                    month_end = datetime(y + 1, 1, 1)
                else:
                    month_end = datetime(y, m + 1, 1)
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
    pf = pq.ParquetFile(parquet_path)
    nrows = pf.metadata.num_rows
    return {"mtime": mtime, "rows": nrows}


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


# ═══════════════════════════════════════════════════════════════
# Display Sync
# ═══════════════════════════════════════════════════════════════

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
    n_pts : int, default 120
        需要的数据点数。
    cutoff_date : Optional[str], default None
        回测截止日期，格式 "YYYY-MM-DD"。为 None 时使用浏览模式。

    Returns
    -------
    Tuple[bool, int]
        (是否成功, 写入的数据条数)。
    """
    display_root = Path(__file__).parent.parent.parent / "data" / "display"
    now = datetime.now()
    partitioned = (
        display_root / ticker_code / f"{now.year:04d}" / f"{now.month:02d}"
        / f"{ticker_code}_{tf}.parquet"
    )

    if cutoff_date is not None:
        # 回测模式：查询截止到 cutoff_date 的最后 n_pts 条，按日期对齐
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
            partitioned.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(partitioned, index=False)
            _save_version(partitioned)
            return True, len(df)
        return False, 0

    # 浏览模式：原有逻辑（n_pts 窗口）
    df = query_kline(ticker_code, tf, n_pts, day_offset=0)
    if len(df) < 5:
        return False, len(df)
    df["Date"] = pd.to_datetime(df["Date"])
    partitioned.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(partitioned, index=False)
    _save_version(partitioned)
    return True, len(df)


# ═══════════════════════════════════════════════════════════════
# 向后兼容 — 委托给拆分的子模块
# ═══════════════════════════════════════════════════════════════

from filter.data.fetcher import (
    _fetch_stock,
    _fetch_all_timeframes,
    fetch_incremental,
    _stock_name_lookup,
)

from filter.data.synth import (
    _sync_all_cascading,
    _write_parquet,
    # Re-export cascade helper functions for backward compatibility
    _ensure_tz_naive,
    _get_tz_suffix,
    _format_synth_date,
    _get_period_start_ts,
    _get_query_start_for_synthesis,
    _offset_to_tz,
    _needs_synthesis,
    _aggregate_bars,
    _find_immediate_finer_tf,
    _build_output_df,
    _synthesize_incomplete_bar,
    _query_tf_from_db,
    _query_tf_for_period,
)
