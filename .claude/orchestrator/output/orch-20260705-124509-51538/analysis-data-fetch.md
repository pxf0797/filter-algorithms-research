# 回测数据获取与入库链路分析

## 概述

本文档分析 `filter_app` 中从 yfinance API 获取股票 K 线数据，经数据层处理，最终写入 SQLite 的完整流水线。链路涉及三个核心模块：

- `filter_app/services/data_loader.py` — 数据获取与展示层
- `filter_app/db.py` — SQLite 数据持久化层
- `filter_app/streamlit_app.py` — UI 调用层（`_cached_fetch_stock` / `_handle_initial_fetch` / `_load_chart_data`）

---

## 1. `_fetch_all_timeframes()` — 8 周期并行拉取

**文件:** `/Users/xfpan/claude/filter_research/filter_app/services/data_loader.py` 第 17-42 行

### 输入参数

| 参数 | 类型 | 含义 |
|------|------|------|
| `market` | `str` | 市场标识："A股(沪深)" / "港股 HK" / "美股 US" |
| `code` | `str` | 股票代码（如 "600519"、"AAPL"、"0700"） |

### 输出格式

返回 `Dict[str, Tuple[bool, Any]]`，key 为 8 个周期字符串，value 为 `(成功标志, 详情)`：
- 成功时：`(True, int)` — int 为该周期数据条数
- 失败时：`(False, str)` — str 为错误消息（截断 80 字符）

### 8 个周期的 force_period 配置

```python
tf_config = {
    "1分钟": ("7d",),   "5分钟": ("60d",),  "15分钟": ("60d",),
    "60分钟": ("730d",), "日线": ("max",),   "周线": ("max",),
    "月线": ("max",),    "季线": ("max",),
}
```

所有周期均通过 `force_period` 参数固定传给 `_fetch_stock`，n_pts 硬编码为 99999（表示尽量获取全量历史）。

### 并行策略

- `ThreadPoolExecutor(max_workers=8)`：8 个 worker 与 8 个周期一一对应，理论上同时发起所有请求。
- 内部函数 `_fetch_one(tf)` 对每个周期调用 `_fetch_stock(market, code, tf, 99999, force_period=...)`。
- 通过 `as_completed(futures)` 轮询完成结果，顺序无关。

### 错误处理

- 若 `_fetch_stock` 返回 `err` 或 `ohlc is None`，标记为失败并记录错误消息。
- `_fetch_stock` 内部抛出的任意 `Exception` 被 `try/except` 捕获，日志 warning 级别，错误消息截断 80 字符，该周期标记为失败。
- **若干周期失败不影响其他周期。** 调用方通过返回值的 `(ok, detail)` 字段判断各周期状态。

### 注意事项

- **无超时机制。** 若 yfinance 对某个周期请求挂起，对应线程会阻塞直到 TCP 超时（通常 30-120s），此时其他 7 个线程不受影响。
- **所有周期共享 n_pts=99999**，并未对分钟级周期限制请求量。分钟级周期（1m/5m/15m）由 period 限制（7d/60d），但 n_pts=99999 经 `_fetch_stock` 的 `period` 计算逻辑已被 `force_period` 覆盖，不参与计算。

---

## 2. `_fetch_stock()` — yfinance 核心获取

**文件:** `/Users/xfpan/claude/filter_research/filter_app/services/data_loader.py` 第 45-136 行

### 输入参数

| 参数 | 类型 | 默认值 | 含义 |
|------|------|--------|------|
| `market` | `str` | — | 市场标识 |
| `code` | `str` | — | 股票代码 |
| `tf` | `str` | — | 周期名称："1分钟" / "5分钟" / "15分钟" / "60分钟" / "日线" / "周线" / "月线" / "季线" |
| `n_pts` | `int` | — | 期望返回的数据点数（回测窗口大小） |
| `force_period` | `Optional[str]` | `None` | 强制指定 yfinance period 参数，跳过自动计算 |

### 输出格式

返回 6 元组 `(t, close, ohlc, full, err, dates)`：

| 元素 | 类型 | 含义 |
|------|------|------|
| `t` | `Optional[np.ndarray]` | 时间轴索引 `[0, 1, 2, ..., n-1]`，float 类型 |
| `close` | `Optional[np.ndarray]` | 收盘价序列 |
| `ohlc` | `Optional[pd.DataFrame]` | OHLC DataFrame，包含 Open/High/Low/Close 列 |
| `full` | `Optional[str]` | 完整的 yfinance ticker 字符串（如 `"600519.SS"`） |
| `err` | `Optional[str]` | 错误消息（成功时为 `None`） |
| `dates` | `Optional[pd.DatetimeIndex]` | 日期索引 |

### 2.1 市场/ticker 映射规则

| 市场 | 条件 | 映射规则 | 示例 |
|------|------|----------|------|
| A股(沪深) | code[0] == "6"（上海） | code + ".SS" | "600519" -> "600519.SS" |
| A股(沪深) | code[0] != "6"（深圳） | code + ".SZ" | "000001" -> "000001.SZ" |
| 港股 HK | 任意 | code.zfill(4) + ".HK" | "700" -> "0700.HK" |
| 美股 US / 其他 | 任意 | code.upper() | "aapl" -> "AAPL" |

**边界条件：** 输入 code 为空或纯空格时，立刻返回 `(None, None, None, None, "Empty ticker code", None)`，**不发起任何网络请求**。

### 2.2 tf -> yfinance interval 映射

```python
tf_map = {
    "1分钟": "1m",  "5分钟": "5m",  "15分钟": "15m",  "60分钟": "1h",
    "日线": "1d",   "周线": "1wk",   "月线": "1mo",    "季线": "3mo",
}
```

映射表为硬编码字典，key 不存在时抛出 KeyError（不会静默失败）。

### 2.3 period 参数计算逻辑

当 `force_period` 非 None 时直接使用，否则按周期和 n_pts 动态计算：

**日线** — `wanted = max(n_pts * 2, 10)`

| wanted 范围 | period |
|------------|--------|
| <= 30 | "1mo" |
| <= 90 | "3mo" |
| <= 180 | "6mo" |
| <= 365 | "1y" |
| <= 730 | "2y" |
| <= 1825 | "5y" |
| <= 3650 | "10y" |
| > 3650 | "max" |

**周线** — `wanted = max(n_pts * 5, 52)`

| wanted 范围 | period |
|------------|--------|
| <= 52 | "1y" |
| <= 104 | "2y" |
| <= 260 | "5y" |
| <= 520 | "10y" |
| > 520 | "max" |

**月线** — `wanted = max(n_pts * 1.5, 12)`

| wanted 范围 | period |
|------------|--------|
| <= 12 | "1y" |
| <= 24 | "2y" |
| <= 60 | "5y" |
| <= 120 | "10y" |
| > 120 | "max" |

**季线** — 固定 `"max"`。

**分钟级周期**（1m/5m/15m/60m）不由 n_pts 决定，而是硬编码固定 period（见 `tf_config`），且通常通过 `force_period` 传入，所以上述自动计算逻辑对分钟级周期无效。

### 2.4 日线 Close 回退机制

**目的：** Yahoo Finance 日线 API 最后一个 bar 的 Close 可能在交易结束后尚未结算（为 NaN），而 Yahoo 周线 API 已经通过实时行情拿到了本周完整收盘价。此机制用周线 Close 回填日线最新 Close 值，解决 A/B 股收盘价 1-2 天延迟问题。

**触发条件：** `interval == "1d"` 且 `len(data) > 0` 且最后一行的 `Close` 列为 NaN。

**执行流程：**
1. 另发一次 `yf.download(full, period="5d", interval="1wk", progress=False)` 获取周线。
2. 若周线数据非空且 Close 非 NaN，将周线最后一条 Close 值写入日线 DataFrame 的最后一条 Close。
3. 若周线数据为空或周线 Close 也是 NaN，则不修改。
4. 若周线查询抛出异常，被 `try/except` 吞掉，仅 warning 日志。

**回填后：** `data = data[data["Close"].notna()]` 过滤掉所有 Close 为 NaN 的行。因此即使回填失败，至少 NaN 行会被删除。

**边界条件：**
- 非日线周期（周线、月线等）不走此逻辑。
- 周线回填本身的 DataFrame 也可能是 MultiIndex 列，同样需要 `droplevel(1)` flatten。

### 2.5 数据写入与返回

1. **全量写入 SQLite：** 原始 yfinance DataFrame（经过 Close 回退和 NaN 过滤后）调用 `upsert_kline(code, tf, data)` 全部写入 DB。
   - 若 upsert 抛出异常，仅 error 日志，不阻断流程。
2. **从 DB 查询返回：** 调用 `query_kline(code, tf, n_pts, day_offset=0)` 从 DB 中取回最后 n_pts 条。
   - 若查询结果为空且 upsert 已执行，err 为 `"写入成功但查询失败"`。
3. **数据格式转换：**
   - `close = result_df["Close"].values.ravel()`
   - `dates = pd.to_datetime(result_df["Date"])`
   - `t = np.arange(n, dtype=float)` — 等间隔整数索引，不是真实时间戳。
   - `result_ohlc` 取 OHLC 四列；若 `Open` 列不存在，用 Close 填充所有 OHLC 列。

### 2.6 yfinance 返回数据处理

- **空数据：** `data.empty` 为 True 时返回错误 `"无数据: {full}"`。
- **MultiIndex 列：** yfinance 为多股票下载时会返回 MultiIndex 列名（如 `("Close", "AAPL")`），通过 `data.columns = data.columns.droplevel(1)` 降维。
- **异常传播：** yfinance 本身的网络异常（ConnectionError 等）**不捕获**，直接向上层传播。

---

## 3. `_sync_to_display()` — 数据同步到 Parquet 缓存

**文件:** `/Users/xfpan/claude/filter_research/filter_app/services/data_loader.py` 第 139-173 行

### 输入参数

| 参数 | 类型 | 默认值 | 含义 |
|------|------|--------|------|
| `ticker_code` | `str` | — | 股票代码 |
| `tf` | `str` | — | 周期 |
| `day_offset` | `int` | 0 | 日期偏移天数（仅浏览模式） |
| `n_pts` | `int` | 120 | 数据点数 |
| `cutoff_date` | `Optional[str]` | None | 回测截止日期 "YYYY-MM-DD" |

### 两种模式

**浏览模式（cutoff_date=None）：**
- 调用 `query_kline(ticker_code, tf, n_pts, day_offset=day_offset)` 获取最新 n_pts 条。
- 不足 5 条时返回 `(False, len(df))`，不写入文件。

**回测模式（cutoff_date 非 None）：**
- 直接通过 SQL 查询 `kline` 表：`WHERE ticker=? AND timeframe=? AND ts <= ? ORDER BY ts DESC LIMIT ?`。
- 返回结果 `reverse()` 为 ASC 顺序后写入 parquet。
- 不使用 `day_offset` 参数。
- 无数据时返回 `(False, 0)`。

### 输出

写入 `data/display/{tf}.parquet`。返回 `(ok: bool, count: int)`。

---

## 4. `upsert_kline()` — 分段 Upsert 策略

**文件:** `/Users/xfpan/claude/filter_research/filter_app/db.py` 第 50-83 行

### 输入参数

| 参数 | 类型 | 含义 |
|------|------|------|
| `ticker` | `str` | 股票代码 |
| `tf` | `str` | 周期 |
| `df` | `pd.DataFrame` | 包含 Date(Index), Open, High, Low, Close, Volume 列的 DataFrame |

### 核心策略：历史 IGNORE + 最新 REPLACE

1. **记录构建：** 遍历 DataFrame 的每一行，将 DatetimeIndex 转换为 ISO 8601 格式字符串作为 `ts` 列，构建 `(ticker, tf, ts, open, high, low, close, volume)` 元组列表。

2. **确定分割点：** 查询 DB 中该 (ticker, tf) 的最新时间戳 `MAX(ts)`：
   - 若 `last_ts` 存在，将记录分为：
     - **历史记录**（`history`）：`ts < last_ts` — 已完成的 bar，不应被覆盖。
     - **最新记录**（`recent`）：`ts >= last_ts` — 可能包含未完成的 bar，允许更新。
   - 若 `last_ts` 为 None（首次写入），所有记录为 `history`，`recent` 为空。

3. **写入：**
   - 历史记录：`INSERT OR IGNORE` — 即使有相同 ts 的行，也保留 DB 中已有的值（保护历史完整性）。
   - 最新记录：逐条执行 `INSERT OR REPLACE` — 允许用新值覆盖相同 ts 的 bar。
   - 若无历史记录，不执行 history 写入。

### 边界条件

- **空 DataFrame：** 构造 records 列表为空，后续查询 `MAX(ts)` 可能返回 None，最后 `history` 和 `recent` 均为空列表，无任何写入。
- **Volume 缺失或 NaN：** `float(row.get("Volume", 0)) if pd.notna(row.get("Volume", 0)) else 0.0` — 缺失或 NaN 时填充 0.0。
- **非 DatetimeIndex：** `idx.isoformat()` 调用检查：若 idx 有 `isoformat` 方法（DatetimeIndex/Timestamp）则使用，否则直接 `str(idx)`。
- **同 ticker 不同 tf 数据互不干扰。** ticker+timeframe+ts 三列联合主键确保隔离。

---

## 5. `query_kline()` — 三种查询模式

**文件:** `/Users/xfpan/claude/filter_research/filter_app/db.py` 第 86-133 行

### 输入参数

| 参数 | 类型 | 默认值 | 含义 |
|------|------|--------|------|
| `ticker` | `str` | — | 股票代码 |
| `tf` | `str` | — | 周期 |
| `n_pts` | `int` | 120 | 返回数据点数上限 |
| `day_offset` | `int` | 0 | 日期偏移天数（仅浏览模式生效） |
| `offset` | `Optional[int]` | None | 行偏移量（回测模式，按时间 ASC 偏移） |

### 三种模式

**模式 1: offset=None + day_offset=0（浏览默认，取最新）**

```sql
SELECT MAX(ts) FROM kline WHERE ticker=? AND timeframe=?  -- 获取最新时间戳
-- 然后：
SELECT ... WHERE ticker=? AND timeframe=? AND ts <= ?
ORDER BY ts DESC LIMIT ?
```

- 以 `MAX(ts)` 为截止点，取最新的 n_pts 条。
- 若 MAX(ts) 不存在（空表），返回空 DataFrame。
- 结果在 Python 层面 `df.iloc[::-1].reset_index(drop=True)` 反转回 ASC 顺序。

**模式 2: offset=None + day_offset>0（时间窗口偏移，浏览历史）**

```sql
SELECT datetime(MAX(ts), ?) FROM kline WHERE ticker=? AND timeframe=?
-- ? = "-{day_offset} days"，即从最新时间向后回退 day_offset 天
-- 然后以该时间为截止点取最新 n_pts 条
```

- 适用于"查看 N 天前的数据"场景。
- 例如 day_offset=10 表示"从 10 天前开始取最近 n_pts 条"。

**模式 3: offset=N（回测窗口滑动）**

```sql
SELECT ... WHERE ticker=? AND timeframe=?
ORDER BY ts ASC LIMIT ? OFFSET ?
```

- 按时间升序，从第 N 条开始取 n_pts 条。
- 返回即为 ASC 顺序，不需要反转。
- 由回测引擎驱动，实现"历史时间窗口滑动"。

### 输出格式

返回 `pd.DataFrame`，列名为 `["Date", "Open", "High", "Low", "Close", "Volume"]`。
- 无数据时返回空的 `pd.DataFrame()`（可通过 `.empty` 判断）。

---

## 6. SQLite 数据库结构

### 连接配置

- **文件路径：** `filter_app/data/market.db`
- **PRAGMA 设置：**
  - `journal_mode=WAL` — 写前日志，提高并发读写性能
  - `synchronous=NORMAL` — 平衡性能与数据安全
  - `busy_timeout=5000` — 等待锁的最长时间（5 秒）

### kline 表结构

```sql
CREATE TABLE IF NOT EXISTS kline (
    ticker    TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ts        TEXT NOT NULL,         -- ISO 8601 格式时间戳
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL,
    volume    REAL,
    PRIMARY KEY (ticker, timeframe, ts)
);
CREATE INDEX IF NOT EXISTS idx_kline_lookup
    ON kline(ticker, timeframe, ts);
```

`ts` 字段以 **ISO 8601 文本格式**存储（如 `"2026-07-04"` 或 `"2026-07-04T09:30:00"`），而非 Unix 时间戳。这使得 SQLite 的 `date()` 和 `datetime()` 函数可用。

---

## 7. 完整数据流向图

```
                    +---------------------+
                    |  streamlit_app.py   |
                    |  (UI 层)            |
                    +----------+----------+
                               |
                 1. 首次加载 / 刷新 / 自动刷新
                               |
                               v
               +---------------+---------------+
               |  _cached_fetch_stock()       |
               |  @st.cache_data(ttl=3600)   |
               |  透明缓存 1 小时             |
               +---------------+---------------+
                               |
                  传入: market, code, tf, n_pts
                  可选: force_period
                               |
                               v
               +---------------+---------------+
               |  _fetch_all_timeframes()     |
               |  (8 周期并行拉取)            |
               |  ThreadPoolExecutor(8)       |
               +-------+-------+-------+------+
                       |       |       |
            8 个 _fetch_stock 任务并行
                       |       |       |
                       v       v       v
            +----------+-------+-------+----------+
            |  _fetch_stock(market, code, tf,     |
            |               n_pts, force_period)  |
            +----------+-------+-------+----------+
                       |       |       |
               1. ticker 拼接          |
               2. tf -> interval 映射  |
               3. period 动态计算      |
                       |               |
                       v               |
            +----------+-------+       |
            |  yf.download()   |       |
            |  (Yahoo Finance) |       |
            +-------+----------+       |
                    |                  |
            (可选)  |  日线 Close NaN?
                    |  是 → 周线回填
                    |  否 → 跳过
                    |                  |
                    v                  |
            +----------+-------+       |
            |  filter NaN Close |      |
            +-------+----------+       |
                    |                  |
                    v                  |
            +----------+-------+       |
            |  upsert_kline()   |<-----+
            |  (全量写入 SQLite)|
            +-------+----------+
                    |
          +---------+---------+
          |         |         |
          v         v         v
    +-----+---+ +---+-----+ +--+------+
    | History | | Recent   | |         |
    | INSERT  | | INSERT   | | SQLite  |
    | OR      | | OR       | | kline   |
    | IGNORE  | | REPLACE  | | 表      |
    +---------+ +----------+ +---------+
                    |
                    v
            +----------+-------+
            |  query_kline()   |
            |  (从 DB 取回     |
            |   n_pts 条)      |
            +-------+----------+
                    |
                    v
            +----------+-------+
            |  返回 6 元组     |
            |  (t, close, ohlc,|
            |   full, err,dates)|
            +------------------+
                    |
                    v
            +----------+-------+
            |  _sync_to_display|
            |  (写入 parquet   |
            |   缓存)          |
            +-------+----------+
                    |
                    v
            data/display/{tf}.parquet
                    |
                    v
            +----------+-------+
            |  图表渲染         |
            |  _load_chart_data|
            |  → _render_chart |
            +------------------+

=== 回测模式额外路径 ===

            +----------+-------+
            |  回测引擎驱动     |
            |  offset 递增      |
            +-------+----------+
                    |
                    v
            +----------+-------+
            |  query_kline()   |
            |  offset=N 模式    |
            |  ORDER BY ts ASC |
            |  LIMIT ? OFFSET ?|
            +-------+----------+
                    |
                    v
            +----------+-------+
            |  _sync_to_display|
            |  cutoff_date 模式 |
            |  ts <= #{date}   |
            +-------+----------+
```

---

## 8. 接口约定汇总

### 8.1 data_loader -> db 接口

| 调用方 | 被调用 | 约定 |
|--------|--------|------|
| `_fetch_stock` | `upsert_kline(code, tf, data)` | data 是 yfinance 原始 DataFrame（DatetimeIndex, OHLCV 列），含所有 fetched bars |
| `_fetch_stock` | `query_kline(code, tf, n_pts, day_offset=0)` | n_pts 通常等于 UI 窗口大小；day_offset=0 表示最新 |
| `_sync_to_display` | `query_kline(ticker_code, tf, n_pts, day_offset)` | 浏览模式调用 |
| `_sync_to_display` | `get_conn()` / 原始 SQL | 回测模式直接执行 SQL |

### 8.2 streamlit_app -> data_loader 接口

| 调用方 | 被调用 | 说明 |
|--------|--------|------|
| `_handle_initial_fetch` | `_fetch_all_timeframes(market, ticker_code)` | 首次加载触发 |
| `_render_refresh_row` | `_fetch_all_timeframes(market, ticker_code)` | 用户点击"刷新数据" |
| `_run_auto_refresh` | `_fetch_all_timeframes(market, ticker_code)` | 自动刷新 |
| `_load_chart_data` | `_sync_to_display(ticker_code, tf, ...)` | 图表数据加载 |
| `_cached_fetch_stock` | `_fetch_stock(market, code, tf, n_pts, ...)` | 备用直连路径 |

### 8.3 关键约定

- `_fetch_stock` 的 **null 传播策略**：即使 upsert_kline 抛出异常，依然走 `query_kline` 返回数据（前提是 DB 中已有历史数据）。
- **数据格式一致性：** 无论经过 `_sync_to_display` 的 parquet 路径还是 `_fetch_stock` 直连路径，最终给图表的 DataFrame 均需满足 `Date` 列为 DatetimeIndex，OHLC 列为 float。
- **错误传递方式：** `err` 为 `None` 表示成功，否则为字符串描述；调用方根据 `err` 是非 None 判断是否显示数据和报警。
- **查询结果方向：** `query_kline` 在浏览模式返回为时间 ASC 顺序（Python 层反转），回测模式（offset）直接 ASC 顺序，调用方无需关心方向问题。

---

## 9. 边界条件与异常路径汇总

| 场景 | 处理方式 | 影响 |
|------|----------|------|
| 空 code | 立即返回 error，不请求 | 上游空字段校验 |
| yfinance 网络异常 | 向上传播（不捕获） | 调用方需处理异常 |
| yfinance 返回空 DataFrame | 返回 `"无数据"` error | 图表不渲染 |
| upsert_kline 异常 | error 日志，继续执行 | 数据可能未写入 DB |
| query_kline 返回空 | 返回 `"写入成功但查询失败"` error | 需手动刷新 |
| 日线 Close NaN | 周线回填（可能失败） | 回填失败则删除 NaN 行 |
| 分钟级数据超量 | period 限制（7d/60d） | 仅获取近期数据 |
| offset 超出总行数 | 返回空 DataFrame | 图表无数据显示 |
| day_offset 超出数据范围 | 返回空 DataFrame | 同上 |
| upsert 传入空 DataFrame | 无任何写入 | 不影响已有数据 |
| _sync_to_display 不足 5 条 | 返回 False，不写入 parquet | 图表走备用 API 路径 |
| WAL 模式下多连接并发 | SQLite WAL + busy_timeout=5000 | 读写互不阻塞 |
