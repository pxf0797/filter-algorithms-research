"""
streamlit/db.py — SQLite 数据层
统一管理所有股票多周期K线数据。
"""

import os
import sqlite3
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
import pandas as pd

DB_PATH = Path(__file__).parent.parent / "data" / "market.db"
SNAPSHOT_DIR = DB_PATH.parent / "snapshots"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """应用启动时调用一次。"""
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
    """批量 upsert。df需包含 Date,Open,High,Low,Close,Volume 列。"""
    records = []
    for idx, row in df.iterrows():
        ts = idx.isoformat() if hasattr(idx, "isoformat") else str(idx)
        records.append((
            ticker, tf, ts,
            float(row["Open"]), float(row["High"]),
            float(row["Low"]), float(row["Close"]),
            float(row.get("Volume", 0)) if pd.notna(row.get("Volume", 0)) else 0.0,
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
        for r in recent:
            conn.execute(
                "INSERT OR REPLACE INTO kline VALUES (?,?,?,?,?,?,?,?)", r)


def query_kline(ticker: str, tf: str, n_pts: int, day_offset: int = 0) -> pd.DataFrame:
    """查询K线。day_offset>0=前移N天，0=最新。"""
    with get_conn() as conn:
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
    """返回该股票所有周期的数据起止日期。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MIN(ts), MAX(ts) FROM kline WHERE ticker=?",
            (ticker,),
        ).fetchone()
    if row and row[0]:
        return (row[0], row[1])
    return None


def has_data(ticker: str) -> bool:
    """检查是否有该股票任何数据。"""
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM kline WHERE ticker=? LIMIT 1", (ticker,)).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# 数据可靠性：健康检查 / 快照 / 导入导出
# ---------------------------------------------------------------------------

def checkpoint_wal():
    """Force WAL checkpoint so all data is in the main DB file."""
    with get_conn() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def check_data_health(ticker=None):
    """Return structured health report for one ticker or all tickers.

    Checks: row count, date range, NULL close count, gap detection (via SQL
    LAG window), and staleness for daily bars (>7 days without new data).

    Returns dict with keys: status, summary, details, issues.
    """
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
    """Return DB file size in megabytes."""
    try:
        return os.path.getsize(str(DB_PATH)) / (1024 * 1024)
    except OSError:
        return 0.0


def validate_db(db_path=None):
    """Check that a DB file has the kline table. Returns (bool, error_msg)."""
    path = db_path or str(DB_PATH)
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='kline'"
        ).fetchone()
        conn.close()
        if not row:
            return False, "缺少 kline 表"
        return True, ""
    except sqlite3.DatabaseError as e:
        return False, f"无效的 SQLite 文件: {e}"


def snapshot_db():
    """Create timestamped copy of market.db. Returns path."""
    checkpoint_wal()
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = SNAPSHOT_DIR / f"market_{ts}.db"
    shutil.copy2(str(DB_PATH), str(dest))
    return str(dest)


def list_snapshots():
    """Return [(path_str, mtime, size_mb, label), ...] newest first."""
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
    """Restore DB from snapshot. Delete WAL/SHM afterwards."""
    shutil.copy2(str(snapshot_path), str(DB_PATH))
    for suffix in ["-wal", "-shm"]:
        p = str(DB_PATH) + suffix
        if os.path.exists(p):
            os.remove(p)


def prune_snapshots(max_keep=5):
    """Keep only the most recent N snapshots, delete the rest."""
    snapshots = list_snapshots()
    for path_str, _, _, _ in snapshots[max_keep:]:
        try:
            os.remove(path_str)
        except OSError:
            pass


def clear_display_cache():
    """Delete all .parquet files in data/display/."""
    display_dir = DB_PATH.parent / "display"
    if display_dir.exists():
        for f in display_dir.glob("*.parquet"):
            try:
                f.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 数据校验：DB vs 数据源 对比 & 更新
# ---------------------------------------------------------------------------

def compare_with_db(ticker, tf, df_fetched):
    """Compare a freshly-fetched DataFrame against DB for one (ticker, tf).

    df_fetched: yfinance DataFrame with DatetimeIndex, columns Open/High/Low/Close/Volume.

    Returns dict:
        db_count, yf_count, overlap_count,
        db_start, db_end, yf_start, yf_end,
        fingerprint_match: bool,
        diffs: [(ts, db_close, yf_close), ...]  # overlapping timestamps with diff > 1e-6
        only_db: int, only_yf: int,
        status: 'ok' | 'update_available' | 'conflict'
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
    for idx, row in df_fetched.iterrows():
        ts = idx.isoformat() if hasattr(idx, "isoformat") else str(idx)
        close_val = float(row["Close"].iloc[0]) if hasattr(row["Close"], "iloc") else float(row["Close"])
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
    """Replace DB data for overlapping timestamps with fetched data.

    Strategy: DELETE rows where ts is in the fetched DataFrame, then INSERT all
    fetched rows. This updates corrected historical bars AND adds new bars.
    Non-overlapping historical bars (only in DB) are preserved.
    """
    records = []
    for idx, row in df.iterrows():
        ts = idx.isoformat() if hasattr(idx, "isoformat") else str(idx)
        records.append((ts,))

    with get_conn() as conn:
        # Delete overlapping timestamps
        for (ts,) in records:
            conn.execute(
                "DELETE FROM kline WHERE ticker=? AND timeframe=? AND ts=?",
                (ticker, tf, ts),
            )

    # Now use normal upsert to insert all fetched rows
    upsert_kline(ticker, tf, df)


if __name__ == "__main__":
    init_db()
    print("DB initialized:", DB_PATH)
