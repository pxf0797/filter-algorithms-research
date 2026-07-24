"""
filter_app/db.py — SQLite 数据层
统一管理所有股票多周期K线数据。
"""

import os
import sqlite3
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
import pandas as pd
from loguru import logger

DB_PATH = Path(__file__).parent.parent / "data" / "market.db"
SNAPSHOT_DIR = DB_PATH.parent / "snapshots"


def get_conn() -> sqlite3.Connection:
    """获取数据库连接。

    以 WAL 模式打开 SQLite 连接，设置合理的同步与超时参数，
    并使用 ``sqlite3.Row`` 作为行工厂以支持列名访问。

    Returns
    -------
    sqlite3.Connection
        配置好的数据库连接对象。
    """
    logger.debug("Connecting to DB: {}", DB_PATH)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA mmap_size=268435456")      # 256MB mmap (P0-4)
    conn.execute("PRAGMA temp_store=MEMORY")          # temp tables in memory (P0-4)
    conn.execute("PRAGMA cache_size=-32768")          # 32MB page cache (P0-4)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库 schema。

    创建 ``kline`` 表及其索引（如不存在）。应用启动时调用一次。

    See Also
    --------
    get_conn : 获取数据库连接。
    """
    logger.debug("Initializing database schema")
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS kline (
                ticker    TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                ts        TEXT NOT NULL,
                open      REAL,
                high      REAL,
                low       REAL,
                close     REAL,
                volume    REAL,
                PRIMARY KEY (ticker, timeframe, ts)
            );
            CREATE INDEX IF NOT EXISTS idx_kline_lookup
                ON kline(ticker, timeframe, ts);
        """)


def upsert_kline(ticker: str, tf: str, df: pd.DataFrame):
    """批量写入或更新 K 线数据。

    根据数据库中该周期的最新日期，将数据分为历史 bar 与最新 bar：
    历史 bar 使用 ``INSERT OR IGNORE`` 避免重复，最新 bar 使用
    ``INSERT OR REPLACE`` 允许覆盖未完成 bar。

    Parameters
    ----------
    ticker : str
        股票代码。
    tf : str
        时间周期（如 ``"日线"``, ``"60分钟"``）。
    df : pd.DataFrame
        包含 ``Date``, ``Open``, ``High``, ``Low``, ``Close``, ``Volume``
        列的 Pandas DataFrame。
    """
    logger.debug("Upserting kline: ticker={}, tf={}, rows={}", ticker, tf, len(df))
    records = []
    for row in df.itertuples():
        ts = row.Index.isoformat() if hasattr(row.Index, "isoformat") else str(row.Index)
        vol = float(getattr(row, "Volume", 0))
        records.append((
            ticker, tf, ts,
            float(row.Open), float(row.High),
            float(row.Low), float(row.Close),
            vol if pd.notna(vol) else 0.0,
        ))
    with get_conn() as conn:
        # 找到该周期最新日期：历史bar用IGNORE（已完成），最新bar用REPLACE（可能未完成需更新）
        row = conn.execute(
            "SELECT MAX(ts) FROM kline WHERE ticker=? AND timeframe=?",
            (ticker, tf)).fetchone()
        last_ts = row[0] if (row and row[0]) else None

        if last_ts:
            history = [r for r in records if r[2] < last_ts]
            recent = [r for r in records if r[2] >= last_ts]
        else:
            history, recent = records, []

        if history:
            conn.executemany(
                "INSERT OR IGNORE INTO kline VALUES (?,?,?,?,?,?,?,?)", history)
            logger.debug("Inserted {} history rows for {}/{}", len(history), ticker, tf)
        for r in recent:
            conn.execute(
                "INSERT OR REPLACE INTO kline VALUES (?,?,?,?,?,?,?,?)", r)
        if recent:
            logger.debug("Upserted {} recent rows for {}/{}", len(recent), ticker, tf)


def query_kline(ticker, tf, n_pts=120, day_offset=0, offset=None):
    """查询 K 线数据。

    支持两种模式：

    - **浏览模式**（``offset=None``）：取最新 ``n_pts`` 条，支持 ``day_offset``
      日期偏移，返回降序数据并最终翻转为升序。
    - **回测模式**（``offset=N``）：从第 N 条开始取 ``n_pts`` 条，按时间升序返回。

    Parameters
    ----------
    ticker : str
        股票代码。
    tf : str
        时间周期。
    n_pts : int, optional
        返回的记录条数（默认 120）。
    day_offset : int, optional
        以最新日期为基准往前偏移的天数（仅浏览模式），默认 0。
    offset : int or None, optional
        回测模式下的偏移量；``None`` 表示使用浏览模式。

    Returns
    -------
    pd.DataFrame
        包含 ``Date``, ``Open``, ``High``, ``Low``, ``Close``, ``Volume``
        列的 DataFrame。无数据时返回空 DataFrame。
    """
    logger.debug("Querying kline: ticker={}, tf={}, n_pts={}, day_offset={}, offset={}",
                 ticker, tf, n_pts, day_offset, offset)
    with get_conn() as conn:
        if offset is not None:
            rows = conn.execute(
                """SELECT ts, open, high, low, close, volume
                   FROM kline WHERE ticker=? AND timeframe=?
                   ORDER BY ts ASC LIMIT ? OFFSET ?""",
                (ticker, tf, n_pts, int(offset)),
            ).fetchall()
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
            return df

        row = conn.execute(
            "SELECT MAX(ts) FROM kline WHERE ticker=? AND timeframe=?",
            (ticker, tf),
        ).fetchone()
        if not row or not row[0]:
            return pd.DataFrame()

        if day_offset > 0:
            cutoff = conn.execute(
                "SELECT datetime(MAX(ts), ?) FROM kline WHERE ticker=? AND timeframe=?",
                (f"-{day_offset} days", ticker, tf),
            ).fetchone()[0]
        else:
            cutoff = row[0]

        rows = conn.execute(
            """SELECT ts, open, high, low, close, volume
               FROM kline WHERE ticker=? AND timeframe=? AND ts <= ?
               ORDER BY ts DESC LIMIT ?""",
            (ticker, tf, cutoff, n_pts),
        ).fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    df = df.iloc[::-1].reset_index(drop=True)
    return df


def get_date_range(ticker: str) -> Optional[Tuple[str, str]]:
    """获取指定股票的数据起止日期范围。

    查询该股票在所有时间周期上的最小和最大时间戳。

    Parameters
    ----------
    ticker : str
        股票代码。

    Returns
    -------
    Optional[Tuple[str, str]]
        形如 ``(start_date, end_date)`` 的元组，无数据时返回 ``None``。
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MIN(ts), MAX(ts) FROM kline WHERE ticker=?",
            (ticker,),
        ).fetchone()
    if row and row[0]:
        return (row[0], row[1])
    return None


def has_data(ticker: str) -> bool:
    """检查数据库中是否存在指定股票的数据。

    Parameters
    ----------
    ticker : str
        股票代码。

    Returns
    -------
    bool
        存在至少一条记录返回 ``True``，否则 ``False``。
    """
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM kline WHERE ticker=? LIMIT 1", (ticker,)).fetchone()
    return row is not None


def get_latest_date(ticker: str, tf: str) -> Optional[str]:
    """获取指定股票+周期的最新数据时间戳。

    Parameters
    ----------
    ticker : str
        股票代码。
    tf : str
        时间周期（如 ``"日线"``, ``"60分钟"``）。

    Returns
    -------
    Optional[str]
        最新时间戳字符串，无数据时返回 ``None``。
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(ts) FROM kline WHERE ticker=? AND timeframe=?",
            (ticker, tf),
        ).fetchone()
    return row[0] if (row and row[0]) else None


# ---------------------------------------------------------------------------
# 数据可靠性：健康检查 / 快照 / 导入导出
# ---------------------------------------------------------------------------

def checkpoint_wal():
    """强制执行 WAL checkpoint。

    将 WAL 文件中的未提交数据全部写回主数据库文件，确保快照完整。

    See Also
    --------
    snapshot_db : 创建数据库快照前调用。
    """
    logger.debug("Checkpointing WAL")
    with get_conn() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def check_data_health(ticker=None):
    """检查数据健康状况并返回结构化报告。

    检查内容包括：行数、日期范围、空 close 数量、数据缺口检测
    （通过 SQL LAG 窗口函数）、以及日线数据的新鲜度（超过 7 天未更新视为过期）。

    Parameters
    ----------
    ticker : str or None, optional
        指定股票代码；``None`` 表示检查所有股票。

    Returns
    -------
    dict
        包含以下键的字典：

        - ``status`` : str — ``"ok"`` / ``"warn"`` / ``"error"``
        - ``summary`` : str — 汇总描述
        - ``details`` : list[dict] — 每只股票每个周期的检查明细
        - ``issues`` : list[str] — 所有问题的文本描述
    """
    logger.debug("Checking data health: ticker={}", ticker or "all")
    with get_conn() as conn:
        if ticker:
            tickers = [ticker]
        else:
            rows = conn.execute("SELECT DISTINCT ticker FROM kline").fetchall()
            tickers = [r[0] for r in rows]

        if not tickers:
            return {"status": "error", "summary": "数据库为空",
                    "details": [], "issues": ["没有任何数据"]}

        # Expected interval in days for gap detection (skip 周/月/季)
        interval_days = {
            "1分钟": 1/1440, "5分钟": 5/1440, "15分钟": 15/1440,
            "60分钟": 1/24, "日线": 1,
        }

        details = []
        issues = []
        warn_count = 0
        error_count = 0

        for t in tickers:
            tfs = conn.execute(
                "SELECT DISTINCT timeframe FROM kline WHERE ticker=? ORDER BY timeframe",
                (t,)).fetchall()

            for (tf,) in tfs:
                # Row count & date range
                r = conn.execute(
                    "SELECT COUNT(*), MIN(date(ts)), MAX(date(ts)), "
                    "SUM(CASE WHEN close IS NULL THEN 1 ELSE 0 END) "
                    "FROM kline WHERE ticker=? AND timeframe=?",
                    (t, tf)).fetchone()
                cnt, start_d, end_d, nulls = r[0], r[1], r[2], r[3]

                status = "✅ 正常"
                item_issues = []

                if cnt == 0:
                    status = "❌ 无数据"
                    error_count += 1
                    item_issues.append(f"{t} {tf}: 无数据")
                if nulls > 0:
                    item_issues.append(f"{t} {tf}: {nulls}处空值")
                    if "✅" in status:
                        status = f"⚠️ 有{nulls}处空值"
                        warn_count += 1

                # Gap detection for regular-interval timeframes
                gaps = 0
                if tf in interval_days:
                    threshold = interval_days[tf] * 3
                    gap_row = conn.execute(
                        "SELECT COUNT(*) FROM ("
                        " SELECT julianday(ts)-julianday(LAG(ts) OVER ("
                        "   PARTITION BY ticker, timeframe ORDER BY ts)) as gap "
                        " FROM kline WHERE ticker=? AND timeframe=?"
                        ") WHERE gap > ?",
                        (t, tf, threshold)).fetchone()
                    gaps = gap_row[0] if gap_row else 0
                    if gaps > 0:
                        item_issues.append(f"{t} {tf}: {gaps}处异常缺口")
                        if "✅" in status:
                            status = f"⚠️ 有{gaps}处缺口"
                            warn_count += 1

                # Staleness: daily bars older than 7 days
                if tf == "日线" and end_d:
                    stale = conn.execute(
                        "SELECT julianday('now') - julianday(?)", (end_d,)
                    ).fetchone()[0]
                    if stale > 7:
                        item_issues.append(f"{t} 日线: 最新数据{end_d}，已过期{int(stale)}天")
                        status = "⚠️ 数据过期"
                        warn_count += 1

                details.append({
                    "股票": t, "周期": tf, "行数": cnt,
                    "起始": start_d or "-", "最新": end_d or "-",
                    "空值": nulls, "缺口": gaps, "状态": status,
                })

                if item_issues:
                    issues.extend(item_issues)

        total_tickers = len(tickers)
        total_tfs = len(details)
        if error_count > 0:
            overall = "error"
            summary = f"{total_tickers}只股票{total_tfs}个周期，{error_count}个异常"
        elif warn_count > 0:
            overall = "warn"
            summary = f"{total_tickers}只股票{total_tfs}个周期，{warn_count}个需关注"
        else:
            overall = "ok"
            summary = f"{total_tickers}只股票{total_tfs}个周期，全部正常"

        return {"status": overall, "summary": summary,
                "details": details, "issues": issues}


def get_db_size_mb():
    """获取数据库文件大小（兆字节）。

    Returns
    -------
    float
        文件大小，以 MB 为单位。文件不存在或无法访问时返回 ``0.0``。
    """
    try:
        return os.path.getsize(str(DB_PATH)) / (1024 * 1024)
    except OSError:
        return 0.0


def validate_db(db_path=None):
    """验证数据库文件是否包含 ``kline`` 表。

    以只读方式打开数据库，检查 ``sqlite_master`` 中是否存在 ``kline`` 表定义。

    Parameters
    ----------
    db_path : str or None, optional
        数据库文件路径；``None`` 时使用默认路径。

    Returns
    -------
    Tuple[bool, str]
        形如 ``(is_valid, error_message)`` 的二元组：
        当表存在时返回 ``(True, "")``，否则返回 ``(False, 原因)``。
    """
    path = db_path or str(DB_PATH)
    try:
        logger.debug("Validating DB: {}", path)
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='kline'"
            ).fetchone()
        if not row:
            return False, "缺少 kline 表"
        return True, ""
    except sqlite3.DatabaseError as e:
        logger.error("DB validation failed: {}", e)
        return False, f"无效的 SQLite 文件: {e}"


def snapshot_db():
    """创建带时间戳的数据库快照。

    先执行 WAL checkpoint 确保数据完整，然后将 ``market.db`` 复制到
    快照目录，文件名为 ``market_YYYYmmdd_HHMMSS.db``。

    Returns
    -------
    str
        快照文件的绝对路径。
    """
    checkpoint_wal()
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = SNAPSHOT_DIR / f"market_{ts}.db"
    shutil.copy2(str(DB_PATH), str(dest))
    return str(dest)


def list_snapshots():
    """列出所有快照文件，按时间倒序排列。

    Returns
    -------
    list[Tuple[str, float, float, str]]
        元素为 ``(path, mtime, size_mb, label)`` 的列表，按修改时间
        从新到旧排序。无快照目录时返回空列表。
    """
    if not SNAPSHOT_DIR.exists():
        return []
    files = sorted(
        SNAPSHOT_DIR.glob("market_*.db"),
        key=lambda f: f.stat().st_mtime, reverse=True)
    result = []
    for f in files:
        st = f.stat()
        ts = datetime.fromtimestamp(st.st_mtime).strftime("%m/%d %H:%M")
        size_mb = st.st_size / (1024 * 1024)
        label = f"{f.name} ({size_mb:.1f}MB, {ts})"
        result.append((str(f), st.st_mtime, size_mb, label))
    return result


def restore_snapshot(snapshot_path):
    """从快照文件恢复数据库。

    将快照文件复制为 ``market.db``，然后清理残留的 WAL 和 SHM 文件。

    Parameters
    ----------
    snapshot_path : str
        快照文件的路径。
    """
    shutil.copy2(str(snapshot_path), str(DB_PATH))
    for suffix in ["-wal", "-shm"]:
        p = str(DB_PATH) + suffix
        if os.path.exists(p):
            os.remove(p)


def prune_snapshots(max_keep=5):
    """清理旧快照，仅保留最新的 N 份。

    Parameters
    ----------
    max_keep : int, optional
        保留的快照数量上限，默认 5。
    """
    snapshots = list_snapshots()
    for path_str, _, _, _ in snapshots[max_keep:]:
        try:
            os.remove(path_str)
        except OSError:
            pass


def clear_display_cache():
    """清除显示缓存。

    删除 ``data/display/`` 目录下的所有 ``.parquet`` 文件（含 ticker 子目录）。
    """
    display_dir = DB_PATH.parent / "display"
    if display_dir.exists():
        for f in display_dir.glob("**/*.parquet"):
            try:
                f.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 数据校验：DB vs 数据源 对比 & 更新
# ---------------------------------------------------------------------------

def compare_with_db(ticker, tf, df_fetched):
    """对比新拉取的数据与数据库中已有数据。

    对重叠时间戳计算 MD5 指纹来判断一致性，并列出 Close 差值超过
    ``1e-6`` 的明细差异。

    Parameters
    ----------
    ticker : str
        股票代码。
    tf : str
        时间周期。
    df_fetched : pd.DataFrame
        新拉取的 yfinance DataFrame，需包含 ``DatetimeIndex`` 及
        ``Open`` / ``High`` / ``Low`` / ``Close`` / ``Volume`` 列。

    Returns
    -------
    dict
        包含以下键的字典：

        - ``status`` : str — ``"ok"`` / ``"update_available"`` / ``"conflict"``
        - ``db_count`` : int — 数据库中记录数
        - ``yf_count`` : int — 拉取数据记录数
        - ``overlap_count`` : int — 重叠时间戳数
        - ``db_start`` / ``db_end`` : str — 数据库起止日期
        - ``yf_start`` / ``yf_end`` : str — 拉取数据起止日期
        - ``fingerprint_match`` : bool — 重叠部分指纹是否一致
        - ``diffs`` : list[Tuple[str, float, float]] — 差异明细
        - ``only_db`` : int — 仅在数据库中存在的时间戳数
        - ``only_yf`` : int — 仅在拉取数据中存在的时间戳数
    """
    import hashlib as _hashlib

    with get_conn() as conn:
        db_rows = conn.execute(
            "SELECT ts, close FROM kline WHERE ticker=? AND timeframe=? ORDER BY ts",
            (ticker, tf),
        ).fetchall()

    db_dict = {r[0]: r[1] for r in db_rows}
    db_ts = set(db_dict.keys())

    yf_dict = {}
    for row in df_fetched.itertuples():
        ts = row.Index.isoformat() if hasattr(row.Index, "isoformat") else str(row.Index)
        close_val = float(row.Close)
        yf_dict[ts] = close_val
    yf_ts = set(yf_dict.keys())

    common = sorted(db_ts & yf_ts)

    # Fingerprint over overlapping timestamps
    fp_db = _hashlib.md5(
        "".join(f"{ts}:{db_dict[ts]:.4f}" for ts in common).encode()
    ).hexdigest()
    fp_yf = _hashlib.md5(
        "".join(f"{ts}:{yf_dict[ts]:.4f}" for ts in common).encode()
    ).hexdigest()

    # Detailed diffs on overlapping timestamps
    diffs = []
    for ts in common:
        delta = abs(db_dict[ts] - yf_dict[ts])
        if delta > 1e-6:
            diffs.append((ts, round(db_dict[ts], 4), round(yf_dict[ts], 4)))

    # Status
    if fp_db == fp_yf and len(yf_ts - db_ts) == 0:
        status = "ok"
    elif fp_db == fp_yf:
        status = "update_available"
    else:
        status = "conflict"

    # Date range strings
    db_start = min(db_ts)[:10] if db_ts else "-"
    db_end = max(db_ts)[:10] if db_ts else "-"
    yf_start = min(yf_ts)[:10] if yf_ts else "-"
    yf_end = max(yf_ts)[:10] if yf_ts else "-"

    return {
        "status": status,
        "db_count": len(db_rows), "yf_count": len(yf_dict),
        "overlap_count": len(common),
        "db_start": db_start, "db_end": db_end,
        "yf_start": yf_start, "yf_end": yf_end,
        "fingerprint_match": fp_db == fp_yf,
        "diffs": diffs,
        "only_db": len(db_ts - yf_ts),
        "only_yf": len(yf_ts - db_ts),
    }


def force_update_kline(ticker, tf, df):
    """强制更新 K 线数据。

    策略：先删除拉取数据中存在的时间戳对应的 DB 记录，再通过
    ``upsert_kline`` 重新插入。这样可以同时更新已修正的历史 bar
    和新增 bar，同时保留数据库中独有的历史 bar。

    Parameters
    ----------
    ticker : str
        股票代码。
    tf : str
        时间周期。
    df : pd.DataFrame
        包含 ``DatetimeIndex`` 及标准 OHLCV 列的 DataFrame。
    """
    records = []
    for row in df.itertuples():
        ts = row.Index.isoformat() if hasattr(row.Index, "isoformat") else str(row.Index)
        records.append((ts,))

    with get_conn() as conn:
        # Delete overlapping timestamps — batch via executemany (P0-8)
        conn.executemany(
            "DELETE FROM kline WHERE ticker=? AND timeframe=? AND ts=?",
            [(ticker, tf, ts) for (ts,) in records],
        )

    # Now use normal upsert to insert all fetched rows
    upsert_kline(ticker, tf, df)


if __name__ == "__main__":
    init_db()
    logger.info("DB initialized: {}", DB_PATH)
