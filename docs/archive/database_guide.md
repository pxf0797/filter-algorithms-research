# 数据库管理方案说明

## 1. 概述

### 1.1 目标

本项目使用 **SQLite 单文件数据库** 统一管理所有股票、所有周期（1分钟～季线）的 K 线数据。核心设计目标：

- **自动建表**：应用启动时 `init_db()` 自动创建表和索引，无需人工执行 DDL
- **统一管理**：多股票、多周期、时间段筛选、按天偏移导航 —— 全部用标准 SQL 完成
- **零依赖**：SQLite 是 Python 3 标准库的一部分，无需 `pip install` 任何额外包

### 1.2 架构图

```
                  yfinance API
                       |
                       v
              +-------------------+
              |   _fetch_stock    |  下载后 upsert_kline() 写入 DB
              |   下载 + upsert   |
              +--------+----------+
                       |
                       v
              +-------------------+
              |     SQLite        |
              |  kline 统一大表   |  data/market.db
              |                   |
              |  PK: (ticker,     |
              |   timeframe, ts)  |
              +---+----------- ---+
                  |          |
    +--------------+          +--------------+
    |                                          |
    v                                          v
+---------------------+            +-----------------------+
| _sync_to_display    |            | _render_chart         |
| DB 查询 -> 写       |            | display parquet       |
| display parquet     |            | -> Plotly 渲染        |
+----------+----------+            +----------^------------+
           |                                   |
           v                                   |
+---------------------+                        |
| data/display/       |  <-- 读取 ------------+
| {tf}.parquet        |
| (临时视图)           |
+---------------------+
```

### 1.3 数据流要点

1. **写入路径**：yfinance -> `_fetch_stock` -> `db.upsert_kline()` -> SQLite
2. **显示路径**：`_sync_to_display` (SELECT from kline) -> display parquet -> `_render_chart` 读取
3. **按天偏移**：`query_kline(n_pts, day_offset)` -> SQL `WHERE ts <= cutoff ORDER BY ts DESC LIMIT n_pts`
4. **首次加载**：检测 `has_data()` 为 False 时，自动 `_fetch_all_timeframes` 拉取全部 8 个周期

### 1.4 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 数据库引擎 | SQLite | Python 内置，零依赖；单表百万行无压力 |
| 表结构 | 统一大表 | 跨股票查询方便；索引覆盖好；减少 DDL |
| 时间戳存储 | TEXT (ISO 8601) | 人类可读；SQLite 日期函数原生支持；与 yfinance 格式对齐 |
| 写入策略 | INSERT OR IGNORE | 幂等；yfinance 回补历史数据不产生重复行；保留首次写入值 |
| 显示层 | 保留 display parquet | _render_chart 保持对 display parquet 的读取路径不变 |
| 数据库文件路径 | `data/market.db` | 与 data/display 目录同级 |

---

## 2. 数据库结构

### 2.1 kline 表结构

```sql
CREATE TABLE IF NOT EXISTS kline (
    ticker    TEXT NOT NULL,   -- 股票代码，如 'AAPL'、'600115'、'3690'
    timeframe TEXT NOT NULL,   -- 周期标识：'1分钟'、'5分钟'、'15分钟'、'60分钟'、'日线'、'周线'、'月线'、'季线'
    ts        TEXT NOT NULL,   -- 时间戳，ISO 8601 格式
                               --   日线及以上: 'YYYY-MM-DDTHH:MM:SS'
                               --   分钟线: 'YYYY-MM-DDTHH:MM:SS' (含时区偏移)
    open      REAL,           -- 开盘价
    high      REAL,           -- 最高价
    low       REAL,           -- 最低价
    close     REAL,           -- 收盘价
    volume    REAL,           -- 成交量
    PRIMARY KEY (ticker, timeframe, ts)
);
```

**字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| ticker | TEXT | 统一使用 yfinance 原始代码（如 `AAPL`、`600115`、`3690`），不区分市场。A 股 6 开头自动加 `.SS`，0/3 开头加 `.SZ`，港股补零加 `.HK` |
| timeframe | TEXT | 沿用中文命名，与 UI 下拉菜单值完全一致 |
| ts | TEXT | ISO 8601 格式时间戳。SQLite 的 `datetime()` 函数可直接操作。分钟级数据含时区偏移（如 `2026-06-12T15:30:00`） |
| open/high/low/close/volume | REAL | OHLCV 标准字段。注意 volume 是 REAL 而非 INTEGER，由 yfinance 返回的 pandas 类型决定 |

### 2.2 索引设计

```sql
CREATE INDEX IF NOT EXISTS idx_kline_lookup
    ON kline(ticker, timeframe, ts);
```

| 索引 | 覆盖查询 | 说明 |
|------|----------|------|
| PRIMARY KEY (ticker, timeframe, ts) | 精确定位单条记录 | 最频繁的查询模式（某股票某周期某时间），由主键天然覆盖 |
| idx_kline_lookup | WHERE ticker=? AND timeframe=? ORDER BY ts DESC LIMIT ? | 按股票+周期获取时间序列，覆盖 `_sync_to_display` 和 `_fetch_stock` 的主查询 |

实际只有 1 个二级索引，远少于设计文档中规划的 3 个，但已满足所有业务场景（~6 万行数据，查询 < 5ms）。

### 2.3 PRAGMA 配置

```python
conn.execute("PRAGMA journal_mode=WAL")    # WAL 模式：允许读写并发
conn.execute("PRAGMA synchronous=NORMAL")   # 平衡安全性与写入性能
conn.execute("PRAGMA busy_timeout=5000")    # 忙等待超时 5 秒
```

- **WAL (Write-Ahead Logging)**：允许一个写者与多个读者并发，Streamlit 多 session 场景更稳定
- **synchronous = NORMAL**：比默认 FULL 更快，比 OFF 更安全
- **busy_timeout = 5000**：遇到 `SQLITE_BUSY` 时自动重试，避免应用层捕获异常

---

## 3. 数据流

### 3.1 获取流程（首次加载）

```
检测 has_data(ticker) == False
    -> _fetch_all_timeframes(market, code)
        -> ThreadPoolExecutor(max_workers=8)
            -> 每个周期调用 _fetch_stock(market, code, tf, n_pts, force_period)
                -> yf.download() 获取原始 OHLCV
                -> upsert_kline(code, tf, data)  写入 SQLite
                -> query_kline(code, tf, n_pts)  从 DB 读回
                -> 返回 (t, close, ohlc, full, err, dates)
        -> 汇总 ok/fail 统计
    -> 记录到 session_state._fetched_ticker
```

**并行策略**：8 个周期同时抓取，互不依赖。`force_period` 固定各周期的获取区间：

| 周期 | 获取区间 |
|------|----------|
| 1分钟 | 7d |
| 5分钟 | 60d |
| 15分钟 | 60d |
| 60分钟 | 730d |
| 日线 | max |
| 周线 | max |
| 月线 | max |
| 季线 | max |

### 3.2 显示流程

```
_render_chart(market, ticker_code, cfg, key, day_offset)
    -> _sync_to_display(code, tf, day_offset, n_pts)
        -> query_kline(code, tf, n_pts, day_offset)
            -> SELECT MAX(ts) FROM kline 获取最新时间
            -> day_offset > 0: 计算 cutoff = MAX(ts) - N days
            -> SELECT ts,open,high,low,close,volume
               WHERE ticker=? AND timeframe=? AND ts <= cutoff
               ORDER BY ts DESC LIMIT n_pts
        -> 写入 data/display/{tf}.parquet
    -> 读取 display parquet
    -> 计算滤波、施密特触发器
    -> Plotly 渲染
```

### 3.3 刷新流程

```
点击"刷新数据"按钮：
    -> _fetch_stock.clear()  清除 Streamlit cache
    -> _fetch_all_timeframes(market, code)  重新拉取并 upsert

自动刷新（勾选"自动刷新"）：
    -> 定时器 interval 秒
    -> _fetch_stock.clear()
    -> _fetch_all_timeframes()
    -> time.sleep(remaining) + st.rerun()
```

### 3.4 前移/后移流程

```
day_offset 存储在 session_state._day_offset

"前移"按钮（向历史方向）：
    st.session_state._day_offset += step_days

"后移"按钮（向最新方向）：
    st.session_state._day_offset = max(0, cur - step_days)

"最新"按钮：
    st.session_state._day_offset = 0

每个视图渲染时传入 day_offset：
    _render_chart(..., day_offset=st.session_state._day_offset)
    -> _sync_to_display(code, tf, day_offset, n_pts)
        -> query_kline(code, tf, n_pts, day_offset=day_offset)
```

**偏移可用性判断**：
- 前移可用：`win_start > data_start`（窗口起点的估算日期 > 最早数据日期）
- 后移可用：`cur_offset > 0`（不在最新位置）

---

## 4. API 参考

### 4.1 `db.py` 模块

文件：`filter_app/db.py`

#### `init_db()`

应用启动时调用一次。创建 `data/market.db`（如不存在），执行建表 DDL。

```python
def init_db():
    """应用启动时调用一次。"""
```

**副作用**：创建 `data/` 目录下的 `market.db` 文件；初始化 WAL 日志文件（生成 `market.db-wal` 和 `market.db-shm`）。

---

#### `upsert_kline(ticker, tf, df)`

将 yfinance 返回的 DataFrame 批量写入 kline 表。幂等操作。

```python
def upsert_kline(ticker: str, tf: str, df: pd.DataFrame):
```

**参数**：
- `ticker`: 股票代码（原始代码，如 `AAPL`）
- `tf`: 周期中文名（如 `日线`）
- `df`: 包含 `Open, High, Low, Close, Volume` 列的 DataFrame，index 为 DatetimeIndex

**写入策略**：`INSERT OR IGNORE` — 如果 `(ticker, timeframe, ts)` 组合已存在，跳过不报错。这意味着首次写入的数据不会被后续拉取覆盖。

**时间戳处理**：
```python
ts = idx.isoformat() if hasattr(idx, "isoformat") else str(idx)
```

**Volume 处理**：
```python
float(row.get("Volume", 0)) if pd.notna(row.get("Volume", 0)) else 0.0
```

---

#### `query_kline(ticker, tf, n_pts, day_offset)`

从 kline 表查询数据，返回 DataFrame。

```python
def query_kline(ticker: str, tf: str, n_pts: int, day_offset: int = 0) -> pd.DataFrame
```

**参数**：
- `ticker`: 股票代码
- `tf`: 周期
- `n_pts`: 返回最大行数
- `day_offset`: 偏移天数。0 表示最新数据，> 0 表示向前偏移

**返回值**：DataFrame，列名为 `["Date", "Open", "High", "Low", "Close", "Volume"]`，按时间升序排列。

**查询逻辑**：
1. 查找该股票该周期的最大 `ts`
2. 如果 `day_offset > 0`，计算截止时间：`MAX(ts) - N days`
3. 查询 `ts <= cutoff` 的前 `n_pts` 条记录
4. 对结果集逆序（DESC -> ASC），.reset_index(drop=True)

**空结果**：返回空的 `pd.DataFrame()`。

---

#### `get_date_range(ticker)`

返回该股票所有周期覆盖的数据起止日期。

```python
def get_date_range(ticker: str) -> Optional[Tuple[str, str]]
```

**返回值**：`(最早ts, 最晚ts)` 的 tuple，无数据时返回 `None`。注意返回的是整个数据库级别的起止时间（不限周期），用于 UI 展示数据范围。

---

#### `has_data(ticker)`

快速检查某股票是否有数据。

```python
def has_data(ticker: str) -> bool
```

内部执行 `SELECT 1 FROM kline WHERE ticker=? LIMIT 1`，利用索引快速判断。

---

## 5. 使用操作

### 5.1 如何添加新股票

在侧边栏输入股票代码并选择市场即可。首次输入时自动检测：
- 无历史数据 -> 自动拉取全部 8 个周期
- 有历史数据（`has_data()` 为 True）-> 直接读取已有数据，不重新拉取

```python
# streamlit_app.py 第 914-923 行
if ticker_code and ticker_code != st.session_state._fetched_ticker:
    if not has_data(ticker_code):
        # 自动获取全部周期
        results = _fetch_all_timeframes(market, ticker_code)
```

### 5.2 如何刷新数据

有两种方式：

1. **手动刷新**：点击侧边栏的"刷新数据"按钮。清除 `_fetch_stock` 的 Streamlit cache，重新拉取全部周期数据并 upsert。

2. **自动刷新**：勾选"自动刷新"，设置刷新间隔（10~600 秒）。底层用 `time.sleep` + `st.rerun()` 循环。

### 5.3 如何查看数据库状态

命令行查询：

```bash
# 连接到数据库
sqlite3 data/market.db

# 查看总行数
SELECT COUNT(*) FROM kline;

# 查看每只股票的每个周期数据量
SELECT ticker, timeframe, COUNT(*), MIN(ts), MAX(ts)
FROM kline
GROUP BY ticker, timeframe
ORDER BY ticker, timeframe;

# 查看所有股票
SELECT DISTINCT ticker FROM kline;
```

UI 侧边栏底部显示当前数据范围和偏移天数。

### 5.4 前移/后移操作

在侧边栏的"时间窗口"区域：
1. 选择"移动步长"（1~365 天）
2. 点击"前移"按钮向历史方向翻页
3. 点击"后移"按钮向最新方向回退
4. 点击"最新"按钮回到最新数据

**前移/后移按钮的 disabled 状态自动计算**：
- 前移：基于估算的窗口起始日期与数据库最早日期比较
- 后移：`day_offset == 0` 时禁用

---

## 6. 数据管理

### 6.1 数据库文件位置

```
filter_research/
  data/
    market.db           # SQLite 数据库文件（主文件）
    market.db-wal       # WAL 日志文件（运行时生成）
    market.db-shm       # WAL 共享内存文件（运行时生成）
    display/            # 显示缓存目录
      日线.parquet
      60分钟.parquet
      15分钟.parquet
      5分钟.parquet
```

当前状态（2026-06-14 数据）：
- DB 文件大小：约 11 MB
- 总行数：62,259 行
- 股票数：3 只（AAPL、600115、3690）
- 最早数据：AAPL 周线 1980-12-08
- 最新数据：各分钟级周期到 2026-06-12

### 6.2 如何备份

```bash
# 安全复制（需确保应用不在写入中）
cp data/market.db backups/market_20260614.db

# 使用 SQLite 的 backup API
sqlite3 data/market.db ".backup backups/market_backup.db"
```

### 6.3 如何导出

```bash
# 导出为 CSV
sqlite3 data/market.db -header -csv "SELECT * FROM kline WHERE ticker='AAPL' AND timeframe='日线'" > aapl_daily.csv

# 导出整个数据库为 SQL 脚本
sqlite3 data/market.db .dump > market_dump.sql

# 查询特定股票的总记录数
sqlite3 data/market.db "SELECT ticker, COUNT(*) FROM kline GROUP BY ticker"
```

### 6.4 数据量预估

| 周期 | 每只股票年数据量 | 说明 |
|------|-----------------|------|
| 1分钟 | ~60,000 行/年 | 1 年约 250 交易日 x 240 根 |
| 5分钟 | ~12,000 行/年 | |
| 15分钟 | ~4,000 行/年 | |
| 60分钟 | ~1,000 行/年 | |
| 日线 | ~250 行/年 | |
| 周线 | ~52 行/年 | |
| 月线 | ~12 行/年 | |
| 季线 | ~4 行/年 | |

以 AAPL 为例（1980~2026，约 46 年）：
- 日线：11,467 行
- 周线：2,375 行
- 全部周期总计约 30,000 行/股票

**预估**：10 只股票全周期约 60 万行，DB 文件大小约 50~100 MB，SQLite 完全无压力。

---

## 7. 常见问题

### 7.1 为什么部分周期数据少？

不同周期的 yfinance 数据可用性不同：
- **1 分钟 / 5 分钟 / 15 分钟**：yfinance 仅返回最近 60 天的数据，所以数据量有限
- **60 分钟**：可返回约 2 年数据
- **日线 / 周线 / 月线 / 季线**：返回全部历史，数据最完整

这是 yfinance API 的限制，非数据库问题。

### 7.2 INSERT OR IGNORE 机制

当前使用 `INSERT OR IGNORE` 而非设计文档中的 `INSERT OR REPLACE`：
- `INSERT OR IGNORE`：主键冲突时**跳过**，保留首次写入的数据
- `INSERT OR REPLACE`：主键冲突时**覆盖**为新数据

选择 `IGNORE` 的原因：yfinance 返回的数据稳定回填，无需重复覆盖。首次下载的结果即为最终版本。但如果后续拉取返回了修正过的数据（如复权调整），`IGNORE` 会导致旧数据残留。

**如果希望强制覆盖旧数据**，可将 `upsert_kline()` 中的 `INSERT OR IGNORE` 改为 `INSERT OR REPLACE`。

### 7.3 并发安全

- **WAL 模式**：支持一个写者与多个读者并发
- **Streamlit 场景**：用户交互式操作，同一时刻通常只有一个 session 在写
- **busy_timeout=5000**：写冲突时自动等待 5 秒重试
- **潜在风险**：自动刷新与手动刷新几乎同时触发时可能导致 `SQLITE_BUSY`。当前 `upsert_kline` 和 `query_kline` 每次创建新连接（`with get_conn()`），连接用完自动关闭，减少锁持有时间

### 7.4 数据库与设计文档的差异

当前实现 vs `database_design.md`：

| 项目 | 设计文档 | 当前实现 | 说明 |
|------|----------|----------|------|
| 时间戳字段名 | `date` | `ts` | 分钟级数据含时分秒，`ts` 更准确 |
| NULL 约束 | open/high/low/close/volume NOT NULL | 均为可空 (REAL) | 灵活性更高 |
| volume 类型 | INTEGER NOT NULL | REAL | 与 yfinance 的 pandas 类型对齐 |
| 存储优化 | WITHOUT ROWID | 普通表 | 实现更简单，无性能差异 |
| INSERT 策略 | INSERT OR REPLACE | INSERT OR IGNORE | 跳过而非覆盖，语义更安全 |
| 二级索引数 | 3 个 | 1 个 | 一个综合索引已覆盖所有查询 |
| 总行数估算 | 约 50 万行/10 只 | 实际 6.2 万行/3 只 | 合理范围内 |
| DB 路径 | data/market_data.db | data/market.db | 实际部署路径 |

这些差异可以接受。当前实现更简洁，功能完全满足需求。如果未来需要调整 INSERT 策略，修改 `upsert_kline()` 中的 SQL 语句即可。
