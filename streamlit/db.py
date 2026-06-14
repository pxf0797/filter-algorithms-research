"""
streamlit/db.py — SQLite 数据层
统一管理所有股票多周期K线数据。
"""

import sqlite3
from pathlib import Path
from typing import Optional, Tuple
import pandas as pd

DB_PATH = Path(__file__).parent.parent / "data" / "market.db"


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
        conn.executemany(
            """INSERT OR IGNORE INTO kline
               (ticker, timeframe, ts, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            records,
        )


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


if __name__ == "__main__":
    init_db()
    print("DB initialized:", DB_PATH)
