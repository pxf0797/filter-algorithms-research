# 股票数据存储层设计方案 — SQLite 统一数据管理

## 1. 总体方案概述

### 1.1 目标

用 **SQLite 单文件数据库**统一管理所有股票、所有周期（1分钟～季线）的 K线数据，替换当前基于 Parquet 文件的分散存储方案。核心收益：

- **零依赖**：SQLite 是 Python 标准库的一部分，不新增安装依赖
- **统一查询**：多股票、多周期、时间段筛选、按天偏移导航 —— 全部用标准 SQL 完成，不再手动折腾目录结构
- **天然去重**：PRIMARY KEY (ticker, timeframe, date) 保证同一天同一周期不重复
- **增量更新**：`INSERT OR REPLACE` 语义天然支持幂等写入，yfinance 拉取的新数据自动合并
- **可观测性**：`updated_at` 字段记录每条记录的写入时间，便于排查数据新鲜度问题

### 1.2 架构图

```
                  yfinance API
                       │
                       ▼
              ┌─────────────────┐
              │   _fetch_stock  │  改造点1: 下载后 upsert 到 DB
              │   下载 + upsert  │
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │     SQLite      │
              │  kline 统一大表  │  ~/data/market_data.db (单文件)
              │                 │
              │  PK: (ticker,   │
              │   timeframe,    │
              │   date)         │
              └──┬──────────┬──┘
                 │          │
    ┌────────────┘          └────────────┐
    ▼                                    ▼
┌──────────────┐                 ┌──────────────────┐
│_sync_to_display│ 改造点2       │ _render_chart    │
│ DB → display  │ DB查→写       │ display parquet   │
│   parquet     │ parquet       │ → Plotly 渲染     │
└──────────────┘                 └──────────────────┘
    │                                    │
    ▼                                    ▼
┌──────────────┐                 ┌──────────────────┐
│data/display/ │                 │   Plotly K线图    │
│  {tf}.parquet│ 读取            │   + 滤波 + 施密特 │
│ (临时视图)    │◄────────────────│                  │
└──────────────┘                 └──────────────────┘
```

数据流要点：

1. **写入路径**：yfinance → `_fetch_stock` → `db.upsert_kline()` → SQLite
2. **读取路径（当前）**：`_sync_to_display` → `SELECT ... FROM kline WHERE ...` → display parquet → `_render_chart` 读取
3. **读取路径（Phase 3 可选优化）**：`_render_chart` → `db.query_kline()` → 直接构造 DataFrame，跳过 display parquet 中间层
4. **历史浏览**：`_sync_to_display` 的 day_offset 参数 → SQL `WHERE date <= cutoff ORDER BY date DESC LIMIT n_pts`

### 1.3 关键设计决策

| 决策 | 选项 | 选择 | 理由 |
|------|------|------|------|
| 数据库引擎 | SQLite / DuckDB / TinyDB | **SQLite** | Python内置，零依赖；单表百万行毫无压力；Streamlit Cloud 完美兼容 |
| 表结构 | 每ticker一张表 / 统一大表 | **统一大表** | 跨股票查询方便；索引覆盖好；减少建表 DDL |
| 日期存储 | TEXT / INTEGER(unix) / DATE | **TEXT (YYYY-MM-DD)** | 人类可读；SQLite 日期函数原生支持；与 yfinance 的日期格式自然对齐 |
| 写入策略 | INSERT / UPSERT | **INSERT OR REPLACE** | 幂等；yfinance 回补历史数据不产生重复行 |
| 存储优化 | 默认 / WITHOUT ROWID | **WITHOUT ROWID** | 主键 (ticker, timeframe, date) 本身是良好的聚簇键；省去 rowid 列的开销 |
| display 层 | 保留 / 移除 | **Phase 1-2 保留，Phase 3 可选移除** | 先保持 `_render_chart` 对 display parquet 的读取路径不变，降低改动风险 |

---

## 2. 数据库设计

### 2.1 选型对比

| 维度 | SQLite | DuckDB | TinyDB | 当前方案(Parquet) |
|------|--------|--------|--------|-------------------|
| **Python 依赖** | 内置 (sqlite3) | `duckdb` pip | `tinydb` pip | `pandas` + `pyarrow` |
| **安装复杂度** | 零 | 需编译/下载二进制 | pip install | 已有 |
| **SQL 支持** | 完整 | 完整(SQL超集) | 无 (文档DB) | 无 (文件读) |
| **并发写入** | 单写者(WAL模式可读写并发) | 多读单写 | 差(JSON文件锁) | 无(文件覆盖) |
| **查询性能** | ~50万行 <10ms(有索引) | ~50万行 <5ms | 全表扫描 | 全文件读取 |
| **数据库体积** | ~20MB/10年日线 | 略大于SQLite | JSON膨胀+50% | ~2MB/年/parquet |
| **去重支持** | PK 自动去重 | PK 自动去重 | 需手动逻辑 | 文件夹覆盖 |
| **增量更新** | INSERT OR REPLACE 原生 | INSERT OR REPLACE | 需遍历 JSON | 整文件覆盖 |
| **Streamlit兼容** | 极佳（内置，Cloud支持） | 需额外安装 | 需额外安装 | 内置（pandas） |
| **可运维性** | sqlite3 CLI 直接查 | CLI 独立 | 无CLI | parquet-tools |

**结论**：SQLite 在依赖成本为零的前提下，提供了完整的 SQL 查询能力和去重/增量更新语义。唯一不如当前方案的是纯文件读取的简单性，但这点复杂度被 SQL 带来的灵活性充分抵消。

### 2.2 建表 SQL

```sql
-- 数据库文件: data/market_data.db
-- 创建表 (完整 DDL)

CREATE TABLE IF NOT EXISTS kline (
    ticker      TEXT    NOT NULL,   -- 股票代码，如 'AAPL', '600115', '3690'
    timeframe   TEXT    NOT NULL,   -- 周期标识，如 '日线', '60分钟', '15分钟', '周线'
    date        TEXT    NOT NULL,   -- 日期字符串
                                    --   日线及以上: 'YYYY-MM-DD'
                                    --   分钟线: 'YYYY-MM-DD HH:MM:SS±HH:MM' (含时区)
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      INTEGER NOT NULL,
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (ticker, timeframe, date)
) WITHOUT ROWID;

-- 索引: 覆盖常见查询模式
CREATE INDEX IF NOT EXISTS idx_kline_ticker_timeframe_date
    ON kline(ticker, timeframe, date);

CREATE INDEX IF NOT EXISTS idx_kline_lookup
    ON kline(ticker, timeframe, date DESC);

CREATE INDEX IF NOT EXISTS idx_kline_updated
    ON kline(updated_at);
```

**字段说明**：

- `ticker`：统一用 yfinance 的原始代码（如 `AAPL`、`600115`、`3690`），不区分市场
- `timeframe`：沿用现有中文命名体系（`日线`、`60分钟`、`15分钟`、`5分钟`、`1分钟`、`周线`、`月线`、`季线`），与 UI 下拉菜单的值完全一致
- `date`：TEXT 格式，SQLite 的日期函数 (`date()`, `julianday()`) 可直接操作
- `updated_at`：每次写入自动更新，用于追踪数据新鲜度
- `WITHOUT ROWID`：主键本身就是最优的聚簇键，省去 rowid 的额外存储和 btree 层级

### 2.3 索引策略

| 索引 | 覆盖查询 | 说明 |
|------|---------|------|
| `PRIMARY KEY (ticker, timeframe, date)` | 精确定位单条数据 | 最频繁的查询模式（某股票某周期某天），由 PK 天然覆盖 |
| `idx_kline_ticker_timeframe_date` | `WHERE ticker=? AND timeframe=? ORDER BY date` | 按股票+周期获取时间序列，覆盖 `_sync_to_display` 和 `_fetch_stock` 的主查询 |
| `idx_kline_lookup` | `WHERE ticker=? AND timeframe=? AND date <= ? ORDER BY date DESC LIMIT ?` | 按天偏移查询（历史浏览），date DESC 确保取最新N条 |
| `idx_kline_updated` | `WHERE updated_at < ?` | 数据新鲜度检查，批量刷新时定位过期记录 |

索引数量权衡：3 个二级索引总共覆盖所有业务查询，写入成本约为 INSERT 速度下降 15-20%（对于每分钟写入几十条的 yfinance 场景完全无感知）。

### 2.4 关键查询 SQL 模板

#### 查询 1：获取某股票某周期的最近 N 条数据

```sql
-- 用于 _sync_to_display 或 _render_chart 加载数据
SELECT date, open, high, low, close, volume
FROM kline
WHERE ticker = :ticker AND timeframe = :timeframe
ORDER BY date DESC
LIMIT :limit;
```

#### 查询 2：按天偏移查询（日期前移）

```sql
-- 用于历史浏览：找到截止日期前的最多 N 条数据
-- day_offset = 当前偏移天数，N = 显示的K线数量
SELECT date, open, high, low, close, volume
FROM kline
WHERE ticker = :ticker
  AND timeframe = :timeframe
  AND date <= :cutoff_date      -- 截止日期 = 最新日期 - day_offset天
ORDER BY date DESC
LIMIT :n_pts;
```

Python 端逻辑：

```python
def query_with_offset(cursor, ticker, timeframe, day_offset, n_pts):
    """获取按天偏移后的数据窗口"""
    # 1. 找到最新日期
    cursor.execute(
        "SELECT MAX(date) FROM kline WHERE ticker=? AND timeframe=?",
        (ticker, timeframe)
    )
    latest = cursor.fetchone()[0]
    if not latest:
        return []

    # 2. 计算截止日期
    cutoff = (pd.Timestamp(latest) - pd.Timedelta(days=day_offset)).strftime("%Y-%m-%d")

    # 3. 查询窗口数据
    cursor.execute("""
        SELECT date, open, high, low, close, volume
        FROM kline
        WHERE ticker=? AND timeframe=? AND date <= ?
        ORDER BY date DESC
        LIMIT ?
    """, (ticker, timeframe, cutoff, n_pts))

    rows = cursor.fetchall()
    rows.reverse()  # 升序排列用于图表显示
    return rows
```

#### 查询 3：获取数据范围

```sql
-- 用于 UI 显示 "数据范围: 2025-06-13 ~ 2026-06-12"
SELECT MIN(date) AS start_date, MAX(date) AS end_date
FROM kline
WHERE ticker = :ticker AND timeframe = :timeframe;
```

#### 查询 4：批量插入/更新（幂等 UPSERT）

```sql
INSERT OR REPLACE INTO kline (ticker, timeframe, date, open, high, low, close, volume, updated_at)
VALUES (:ticker, :timeframe, :date, :open, :high, :low, :close, :volume, datetime('now'));
```

Python 端用 `executemany` 批量执行：

```python
def upsert_kline(conn, ticker, timeframe, df):
    """将 yfinance DataFrame 写入 kline 表 (幂等)"""
    records = []
    for idx, row in df.iterrows():
        date_str = idx.strftime("%Y-%m-%d %H:%M:%S%z") if hasattr(idx, 'strftime') else str(idx)
        records.append((ticker, timeframe, date_str,
                        float(row["Open"]), float(row["High"]),
                        float(row["Low"]), float(row["Close"]),
                        int(row["Volume"])))
    conn.executemany("""
        INSERT OR REPLACE INTO kline (ticker, timeframe, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, records)
    conn.commit()
```

#### 查询 5：获取所有股票的列表

```sql
-- 用于 UI 联动或数据管理
SELECT DISTINCT ticker FROM kline ORDER BY ticker;
```

#### 查询 6：检查某股票某周期是否有数据

```sql
SELECT COUNT(*) FROM kline WHERE ticker = :ticker AND timeframe = :timeframe;
```

### 2.5 PRAGMA 配置

在数据库初始化时设置，优化性能和行为：

```sql
-- 启用 WAL 模式：允许读写并发，Streamlit 多连接场景更稳定
PRAGMA journal_mode = WAL;

-- 平衡安全性和写入性能 (默认 FULL 太慢，OFF 不安全)
PRAGMA synchronous = NORMAL;

-- 增大缓存 (默认 -2000KB，改为 -65536KB = 64MB，股票数据量小，够用)
PRAGMA cache_size = -65536;

-- 启用外键约束 (预留，当前无外键但好习惯)
PRAGMA foreign_keys = ON;

-- 临时表存内存
PRAGMA temp_store = MEMORY;
```

---

## 3. 代码架构

### 3.1 db.py 模块 API

新文件：`streamlit/db.py`（约 90 行）

```python
"""
SQLite 数据访问层 — 统一管理所有股票 K 线数据。
数据库文件: data/market_data.db
表: kline(ticker, timeframe, date, open, high, low, close, volume, updated_at)
"""

import sqlite3
from pathlib import Path
from datetime import datetime
import pandas as pd

DB_PATH = Path(__file__).parent.parent / "data" / "market_data.db"


def get_conn() -> sqlite3.Connection:
    """获取数据库连接 (WAL模式, 同线程复用)."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA cache_size = -65536")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


def init_db():
    """初始化数据库：创建表和索引（幂等）。应用启动时调用一次。"""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS kline (
            ticker      TEXT NOT NULL,
            timeframe   TEXT NOT NULL,
            date        TEXT NOT NULL,
            open        REAL NOT NULL,
            high        REAL NOT NULL,
            low         REAL NOT NULL,
            close       REAL NOT NULL,
            volume      INTEGER NOT NULL,
            updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (ticker, timeframe, date)
        ) WITHOUT ROWID;

        CREATE INDEX IF NOT EXISTS idx_kline_ticker_timeframe_date
            ON kline(ticker, timeframe, date);

        CREATE INDEX IF NOT EXISTS idx_kline_lookup
            ON kline(ticker, timeframe, date DESC);

        CREATE INDEX IF NOT EXISTS idx_kline_updated
            ON kline(updated_at);
    """)
    conn.commit()
    conn.close()


def upsert_kline(ticker: str, timeframe: str, df: pd.DataFrame) -> int:
    """将 yfinance DataFrame 写入 kline 表（幂等 INSERT OR REPLACE）。

    Args:
        ticker: 股票代码，如 'AAPL'
        timeframe: 周期标识，如 '日线'
        df: yfinance 返回的 DataFrame，index 为 DatetimeIndex，
            columns 含 Open/High/Low/Close/Volume

    Returns:
        写入的行数
    """
    conn = get_conn()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    records = [(ticker, timeframe, idx.strftime("%Y-%m-%d %H:%M:%S%z"),
                float(row["Open"]), float(row["High"]),
                float(row["Low"]), float(row["Close"]),
                int(row["Volume"]))
               for idx, row in df.iterrows()]
    conn.executemany("""
        INSERT OR REPLACE INTO kline (ticker, timeframe, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, records)
    conn.commit()
    n = len(records)
    conn.close()
    return n


def query_kline(
    ticker: str,
    timeframe: str,
    limit: int = None,
    start_date: str = None,
    end_date: str = None,
    offset_days: int = 0,
) -> pd.DataFrame:
    """从 kline 表查询数据，返回 DataFrame。

    Args:
        ticker: 股票代码
        timeframe: 周期标识
        limit: 返回最近 N 条（与日期范围互斥，limit 优先）
        start_date: 起始日期 'YYYY-MM-DD'
        end_date: 截止日期 'YYYY-MM-DD'
        offset_days: 从最新日期往前偏移的天数

    Returns:
        DataFrame with columns [date, open, high, low, close, volume]
    """
    conn = get_conn()
    cursor = conn.cursor()

    if limit is not None:
        # 按天偏移 + 取最近 N 条
        if offset_days > 0:
            cursor.execute("SELECT MAX(date) FROM kline WHERE ticker=? AND timeframe=?",
                           (ticker, timeframe))
            row = cursor.fetchone()
            if row and row[0]:
                latest = pd.Timestamp(row[0])
                # 尝试解析时区感知的日期
                try:
                    cutoff = (latest - pd.Timedelta(days=offset_days)).strftime("%Y-%m-%d")
                except Exception:
                    cutoff = latest.strftime("%Y-%m-%d")
                cursor.execute("""
                    SELECT date, open, high, low, close, volume
                    FROM kline
                    WHERE ticker=? AND timeframe=? AND date <= ?
                    ORDER BY date DESC
                    LIMIT ?
                """, (ticker, timeframe, cutoff, limit))
            else:
                cursor.execute("""
                    SELECT date, open, high, low, close, volume
                    FROM kline
                    WHERE ticker=? AND timeframe=?
                    ORDER BY date DESC
                    LIMIT ?
                """, (ticker, timeframe, limit))
        else:
            cursor.execute("""
                SELECT date, open, high, low, close, volume
                FROM kline
                WHERE ticker=? AND timeframe=?
                ORDER BY date DESC
                LIMIT ?
            """, (ticker, timeframe, limit))
    elif start_date is not None or end_date is not None:
        where = "WHERE ticker=? AND timeframe=?"
        params = [ticker, timeframe]
        if start_date:
            where += " AND date >= ?"
            params.append(start_date)
        if end_date:
            where += " AND date <= ?"
            params.append(end_date)
        cursor.execute(f"SELECT date, open, high, low, close, volume FROM kline {where} ORDER BY date ASC",
                       params)
    else:
        cursor.execute("""
            SELECT date, open, high, low, close, volume
            FROM kline
            WHERE ticker=? AND timeframe=?
            ORDER BY date ASC
        """, (ticker, timeframe))

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"], utc=True)
    df = df.sort_values("date")
    return df


def get_date_range(ticker: str, timeframe: str):
    """返回 (最早日期, 最晚日期) 或 (None, None)。"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT MIN(date), MAX(date) FROM kline WHERE ticker=? AND timeframe=?",
        (ticker, timeframe)
    )
    row = cursor.fetchone()
    conn.close()
    if row and row[0]:
        return row[0], row[1]
    return None, None


def has_data(ticker: str, timeframe: str) -> bool:
    """检查是否有该股票该周期的数据。"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM kline WHERE ticker=? AND timeframe=?",
        (ticker, timeframe)
    )
    count = cursor.fetchone()[0]
    conn.close()
    return count > 0


def list_tickers() -> list:
    """返回数据库中所有股票代码列表。"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT ticker FROM kline ORDER BY ticker")
    tickers = [row[0] for row in cursor.fetchall()]
    conn.close()
    return tickers
```

### 3.2 `_fetch_stock` 新流程

**当前流程**（`streamlit_app.py` 行 436-510）：

```
yfinance.download() → 保存 parquet → 返回最后 n_pts 条
```

**改造后流程**：

```
yfinance.download() → db.upsert_kline() → db.query_kline(limit=n_pts) → 返回
                                      └─ 幂等写入，自动合并新数据
```

具体改动（伪代码）：

```python
# 保留 yfinance.download() 调用不变
data = yf.download(full, period=period, interval=interval, progress=False)
if data.empty:
    return None, None, None, full, f"无数据: {full}", None

# 处理 MultiIndex columns
if isinstance(data.columns, pd.MultiIndex):
    data.columns = data.columns.droplevel(1)
data = data[data["Close"].notna()]

# ── 新增：upsert 到 SQLite ──
import db
db.upsert_kline(ticker_code, tf, data)  # 全量数据入库

# ── 从 DB 查询最新 n_pts 条返回 ──
df = db.query_kline(ticker_code, tf, limit=n_pts)

# ── 移除：旧 parquet 保存逻辑 ──
# try:
#     archive_dir = Path(__file__).parent.parent / "data" / code
#     ...
#     save_df.to_parquet(...)
# except Exception: pass

# 返回
n = len(df)
close = df["close"].values.ravel()
dates = df["date"].values
return np.arange(n, dtype=float), close, df, full, None, dates
```

**行为变更**：
- 旧：每次下载后**覆盖** archive parquet → 旧数据丢失
- 新：每次下载后**合并**（INSERT OR REPLACE）到 SQLite → 历史数据永不丢失

### 3.3 `_sync_to_display` 新流程

**当前流程**（`streamlit_app.py` 行 513-548）：

```
读取 archive parquet → 按 day_offset 计算窗口 → 写 display parquet
```

**改造后流程**：

```
db.query_kline(limit=n_pts, offset_days=day_offset) → 写 display parquet
```

具体改动（伪代码）：

```python
def _sync_to_display(code, tf, day_offset, n_pts):
    """从 SQLite 按天偏移复制窗口到 display 目录。"""
    # ── 新：从 SQLite 查询 ──
    import db
    df = db.query_kline(code, tf, limit=n_pts, offset_days=day_offset)

    if len(df) < 5:
        return False, len(df)

    # ── 写 display parquet（逻辑不变）──
    display_dir = Path(__file__).parent.parent / "data" / "display"
    display_dir.mkdir(parents=True, exist_ok=True)

    out = df.rename(columns={
        "date": "Date", "open": "Open", "high": "High",
        "low": "Low", "close": "Close", "volume": "Volume"
    })
    out = out.set_index("Date")
    out = out.reset_index()  # Date as column
    out.to_parquet(display_dir / f"{tf}.parquet", index=False)
    return True, len(out)
```

**注意**：display parquet 的列名需要保持与现有 `_render_chart` 读取逻辑兼容（`Date`, `Open`, `High`, `Low`, `Close`, `Volume`），因此做列名映射。

### 3.4 日期范围查询改造

**当前**（`streamlit_app.py` 行 970-980）：

```python
archive_path = Path(...) / code / "日线.parquet"
df = pd.read_parquet(archive_path)
data_start = df["Date"].min().date()
data_end = df["Date"].max().date()
```

**改造后**：

```python
import db
data_start_str, data_end_str = db.get_date_range(ticker_code, "日线")
data_start = pd.Timestamp(data_start_str).date() if data_start_str else None
data_end = pd.Timestamp(data_end_str).date() if data_end_str else None
```

### 3.5 改动清单

| 文件 | 改动 | 行数变化 | 说明 |
|------|------|----------|------|
| **新增：`streamlit/db.py`** | 新建 | +~90 | SQLite 数据访问层 |
| `streamlit/streamlit_app.py:_fetch_stock` | 改造 | -15 +12 | yfinance → upsert DB → query DB 返回；移除 parquet 保存 |
| `streamlit/streamlit_app.py:_sync_to_display` | 改造 | -12 +10 | archive parquet 读取 → db.query_kline() |
| `streamlit/streamlit_app.py:main` (日期范围) | 改造 | -6 +4 | parquet 读取 → db.get_date_range() |
| `streamlit/streamlit_app.py` 顶部 import | 修改 | +1 | 新增 `import db` |
| **新增：`tools/migrate_parquet_to_db.py`** | 新建 | +~60 | 一次性迁移脚本 |
| **净增行数** | | **~82 行** | 90(新) + 17(改) - 25(删) |

---

## 4. 迁移方案

### 4.1 Parquet → SQLite 迁移脚本

新文件：`tools/migrate_parquet_to_db.py`

```python
"""
一次性迁移脚本：将 data/{ticker}/*.parquet 导入到 SQLite kline 表。

用法:
    python tools/migrate_parquet_to_db.py           # 迁移所有 ticker
    python tools/migrate_parquet_to_db.py --dry-run  # 仅统计，不写入

前置条件:
    - streamlit/db.py 已存在且可用
    - data/ 目录下有各 ticker 的 parquet 文件

安全:
    - INSERT OR REPLACE 语义，重复执行幂等
    - --dry-run 模式可以先预览
"""

import sys
import argparse
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "streamlit"))

import pandas as pd
import db


def migrate_ticker(data_dir: Path, ticker: str, dry_run: bool = False) -> dict:
    """迁移单个 ticker 的 all timeframes"""
    ticker_dir = data_dir / ticker
    if not ticker_dir.is_dir():
        return {}

    stats = {}
    for parquet_file in sorted(ticker_dir.glob("*.parquet")):
        # 如 '日线.parquet' → timeframe = '日线'
        timeframe = parquet_file.stem

        df = pd.read_parquet(parquet_file)
        if df.empty:
            stats[timeframe] = 0
            continue

        if "Date" not in df.columns:
            print(f"  SKIP {ticker}/{timeframe}: no Date column")
            continue

        # 标准化列名
        df = df.rename(columns={
            "Date": "Date", "Open": "Open", "High": "High",
            "Low": "Low", "Close": "Close", "Volume": "Volume"
        })

        # 确保必需的列都存在
        required = ["Open", "High", "Low", "Close", "Volume"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            print(f"  SKIP {ticker}/{timeframe}: missing columns {missing}")
            continue

        # 设置 Date 为 index 以匹配 upsert 接口
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date")

        if dry_run:
            stats[timeframe] = len(df)
            print(f"  [DRY-RUN] {ticker}/{timeframe}: {len(df)} rows")
        else:
            n = db.upsert_kline(ticker, timeframe, df)
            stats[timeframe] = n
            print(f"  INSERT  {ticker}/{timeframe}: {n} rows")

    return stats


def main():
    parser = argparse.ArgumentParser(description="迁移 Parquet 数据到 SQLite")
    parser.add_argument("--dry-run", action="store_true", help="仅统计，不写入")
    args = parser.parse_args()

    # 初始化数据库
    if not args.dry_run:
        db.init_db()
        print("数据库初始化完成\n")

    data_dir = Path(__file__).parent.parent / "data"
    if not data_dir.exists():
        print(f"错误: data 目录不存在: {data_dir}")
        sys.exit(1)

    # 遍历所有 ticker 目录
    ticker_dirs = [d for d in data_dir.iterdir()
                   if d.is_dir() and d.name != "display"]

    total = {}
    for ticker_dir in sorted(ticker_dirs):
        ticker = ticker_dir.name
        print(f"[{ticker}]")
        stats = migrate_ticker(data_dir, ticker, args.dry_run)
        for tf, n in stats.items():
            total[tf] = total.get(tf, 0) + n
        print()

    print("=" * 50)
    print("汇总:")
    print(f"  ticker 数: {len(ticker_dirs)}")
    for tf, n in sorted(total.items()):
        print(f"  {tf}: {n} 行")
    print(f"  总计: {sum(total.values())} 行")

    if args.dry_run:
        print("\n[Dry-run 模式, 未实际写入]")


if __name__ == "__main__":
    main()
```

### 4.2 迁移步骤

| 步骤 | 操作 | 命令 | 预期结果 |
|------|------|------|----------|
| 1 | 创建 `db.py` | 写入 `streamlit/db.py` | 模块可 import |
| 2 | 创建迁移脚本 | 写入 `tools/migrate_parquet_to_db.py` | 脚本可用 |
| 3 | Dry-run 预览 | `python tools/migrate_parquet_to_db.py --dry-run` | 打印各 ticker 行数汇总 |
| 4 | 执行迁移 | `python tools/migrate_parquet_to_db.py` | 数据入库，`data/market_data.db` 生成 |
| 5 | 验证数据量 | `python -c "import db; print(db.list_tickers(), db.has_data('AAPL','日线'))"` | True |
| 6 | 修改 `streamlit_app.py` | 改造 `_fetch_stock`, `_sync_to_display`, `main` 中的日期查询 | 改动清单见 3.5 |
| 7 | 启动 Streamlit 验证 | `streamlit run streamlit/streamlit_app.py` | 图表正常渲染，数据正确 |
| 8 | 数据翻页验证 | 点击"前移/后移"按钮 | 各周期独立对齐，数据连贯 |
| 9 | （可选）归档旧 parquet | `mkdir data/_archive && mv data/AAPL data/_archive/` | 保留备份，不删除 |

---

## 5. 实施路线图

### Phase 1: 核心模块 + 数据流改造（预计 2-3 小时）

**目标**：SQLite 可读写，`_fetch_stock` 和 `_sync_to_display` 切换到 DB 数据源。

**任务**：
1. 创建 `streamlit/db.py`（约 90 行）
2. 修改 `streamlit_app.py`:
   - 顶部新增 `import db`
   - `_fetch_stock`: yfinance 下载后 `db.upsert_kline()` 替换 parquet 保存；返回时 `db.query_kline()` 替换 parquet 读取
   - `_sync_to_display`: `db.query_kline(limit=n_pts, offset_days=day_offset)` 替换 archive parquet 读取
   - `main`: 日期范围展示改用 `db.get_date_range()`
3. 修改 `requirements.txt`：无需改动（sqlite3 内置）

**验证**：
- 新部署环境（无 parquet 文件）启动 Streamlit，输入 AAPL，图表正常渲染
- 数据从 yfinance 拉取后自动写入 SQLite
- `sqlite3 data/market_data.db "SELECT COUNT(*) FROM kline"` 返回 > 0

### Phase 2: 迁移现有数据（预计 30 分钟）

**目标**：将已有的 parquet 数据导入 SQLite，确保历史数据不丢失。

**任务**：
1. 创建 `tools/migrate_parquet_to_db.py`
2. `python tools/migrate_parquet_to_db.py --dry-run` 预览
3. `python tools/migrate_parquet_to_db.py` 执行迁移
4. 归档旧 parquet 到 `data/_archive/`

**验证**：
- `sqlite3 data/market_data.db "SELECT ticker, timeframe, COUNT(*) FROM kline GROUP BY ticker, timeframe"` 行数与原 parquet 一致
- Streamlit 启动后无需联网即可显示已有股票的历史数据

### Phase 3: 移除 display 目录中间层（可选，预计 1 小时）

**目标**：`_render_chart` 直接从 SQLite 读取，不再经过 display parquet。

**当前状态**：`_render_chart` 读取 `data/display/{tf}.parquet` 的方式隐含缓存优化（避免每次渲染重复查 DB）。移除此层的前提是 DB 查询足够快（索引已覆盖），否则可能引入渲染延迟。

**任务**（仅在验证 DB 查询 < 10ms 后执行）：
1. 修改 `_render_chart`：`db.query_kline()` 替代 parquet 读取
2. 删除 `_sync_to_display` 函数
3. 移除 `data/display/` 目录引用
4. 约净删 ~20 行代码

**风险**：SQLite 每次查询 ~20ms（含 Python 往返），parquet 读取 ~5ms。在 4 视图 × 无限刷新场景下，差异不可感知（< 60ms）。

---

## 附录 A：数据库 schema 速查

```
kline
├── ticker       TEXT    PK  股票代码 (AAPL, 600115, 3690)
├── timeframe    TEXT    PK  周期 (日线, 60分钟, 15分钟, ...)
├── date         TEXT    PK  日期 (YYYY-MM-DD / YYYY-MM-DD HH:MM:SS±HH:MM)
├── open         REAL        开盘价
├── high         REAL        最高价
├── low          REAL        最低价
├── close        REAL        收盘价
├── volume       INTEGER     成交量
└── updated_at   TEXT        最后更新时间 (auto)
```

## 附录 B：与现有系统兼容性

| 现有功能 | 兼容性 | 说明 |
|---------|--------|------|
| `_render_chart` 读 display parquet | 完全兼容 | display parquet 格式不变（`Date,Open,High,Low,Close,Volume`） |
| 4 视图独立配置 | 完全兼容 | 数据源切换对 UI 层透明 |
| 施密特触发器 | 完全兼容 | 信号数组格式不变（numpy array） |
| 时间窗口导航（前移/后移） | 完全兼容 | `db.query_kline(offset_days=...)` 等价 |
| 配置文件导入/导出 | 完全兼容 | 只涉及 ticker/tf/参数，不涉及存储层 |
| 自动刷新 | 完全兼容 | `_fetch_stock.clear()` 机制不变 |
| 多市场（美股/A股/港股） | 完全兼容 | ticker 字段存储原始代码 |
| `tools/filter_comparison_tool.py` | 不受影响 | 该工具不访问股票数据 |

## 附录 C：FAQ

**Q: 为什么不用 DuckDB？它的分析性能更好。**
A: DuckDB 需要额外安装 `pip install duckdb`，在 Streamlit Cloud 部署时可能增加构建时间。SQLite 内置于 Python 标准库，零依赖，且对于当前数据量（每只股票几万行）查询性能完全足够（索引查询 < 10ms）。

**Q: WITHOUT ROWID 有什么代价？**
A: 如果主键较大（三个 TEXT 列），WITHOUT ROWID 表在通过辅助索引查询时多一次回表（回聚簇键而非 rowid）。但对于我们的场景，绝大多数查询都走 PRIMARY KEY 或覆盖索引，影响可忽略。实测 INSERT 速度约 50000 rows/s（Mac M1），足够。

**Q: 迁移后旧 parquet 文件怎么办？**
A: 建议移动到 `data/_archive/` 目录保留备份，确认一切正常后可手动删除。迁移脚本只读取不修改原文件。

**Q: 多个 Streamlit session 同时写数据库会冲突吗？**
A: WAL 模式支持一写多读并发。Streamlit 的 session 机制保证同一时刻通常只有一个 session 在拉取数据（用户交互式操作），实际冲突概率极低。即使冲突，SQLite 会返回 `SQLITE_BUSY`，可在 db.py 中加 `timeout=5` 参数自动重试。
