# data_loader.py 数据流与级联合成深度分析

> 文件: `/Users/xfpan/claude/filter_research/filter/services/data_loader.py`
> 分析日期: 2026-07-07

---

## 目录

1. [数据流全景图](#1-数据流全景图)
2. [_fetch_stock 逐行分析](#2-_fetch_stock-逐行分析)
3. [_sync_to_display 逐行分析](#3-_sync_to_display-逐行分析)
4. [_sync_all_cascading 级联合成核心分析](#4-_sync_all_cascading-级联合成核心分析)
5. [_fetch_all_timeframes 分析](#5-_fetch_all_timeframes-分析)
6. [缓存层与一致性分析](#6-缓存层与一致性分析)
7. [时间一致性问题标注](#7-时间一致性问题标注)
8. [复杂度分析](#8-复杂度分析)

---

## 1. 数据流全景图

### 1.1 ASCII 数据流图

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          DATA PIPELINE                                    │
│                                                                           │
│  ┌─────────────┐                                                          │
│  │  yfinance   │  (外部 API)                                               │
│  │  yf.download │                                                          │
│  └──────┬──────┘                                                          │
│         │ period + interval                                                │
│         ▼                                                                 │
│  ┌─────────────────────────────────────────────────────────────┐         │
│  │  _fetch_stock(market, code, tf, n_pts, force_period)       │         │
│  │  ┌──────────────────────────────────────────────────────┐  │         │
│  │  │ 1. ticker拼接 (SS/SZ/HK)                              │  │         │
│  │  │ 2. period自动计算 (n_pts → period)                     │  │         │
│  │  │ 3. yf.download(full, period, interval)                │  │         │
│  │  │ 4. MultiIndex columns flatten                        │  │         │
│  │  │ 5. 日线 Close NaN 回退 (周线 fallback)                │  │         │
│  │  │ 6. dropna(subset=["Close"])                          │  │         │
│  │  │ 7. upsert_kline(code, tf, data) → SQLite             │  │         │
│  │  │ 8. query_kline(code, tf, n_pts) → 返回 n_pts 条     │  │         │
│  │  └──────────────────────────────────────────────────────┘  │         │
│  └────────────────────────┬────────────────────────────────────┘         │
│                           │                                               │
│              ┌────────────┴────────────┐                                  │
│              │                         │                                  │
│     [浏览模式 browse]           [回测模式 backtest]                         │
│              │                         │                                  │
│              ▼                         ▼                                  │
│  ┌──────────────────────┐  ┌──────────────────────────────────┐          │
│  │ _sync_to_display()   │  │ _sync_all_cascading()             │          │
│  │                      │  │                                   │          │
│  │ query_kline(ticker,  │  │ for tf in tfs (细→粗):            │          │
│  │   tf, n_pts,         │  │   1. _query_tf_from_db(           │          │
│  │   day_offset)        │  │        ticker,tf,cutoff,n_pts)    │          │
│  │        │             │  │   2. _needs_synthesis(tf,         │          │
│  │        ▼             │  │        db_rows, cutoff)            │          │
│  │  DB → parquet        │  │      ├─ tf == min_tf → skip      │          │
│  │  data/display/{tf}.  │  │      └─ else →                    │          │
│  │  parquet             │  │         _find_immediate_finer_tf  │          │
│  │                      │  │         _synthesize_incomplete_   │          │
│  └──────────┬───────────┘  │           bar()                   │          │
│             │              │              │                     │          │
│             │              │              ├─ 时区转换 v3 Bugfix  │          │
│             │              │              ├─ DB 查询细粒度 K线   │          │
│             │              │              ├─ 合并 finer synth   │          │
│             │              │              ├─ 跨周期过滤           │          │
│             │              │              └─ _aggregate_bars()   │          │
│             │              │   3. _build_output_df(db_rows,     │          │
│             │              │        synth_bar, n_pts)           │          │
│             │              │   4. _write_parquet(tf, df)        │          │
│             │              │   5. synth_cache[tf] = {...}       │          │
│             │              │       (传递给下一个更粗周期)         │          │
│             └──────┬───────┘              │                     │
│                    │                      │                     │
│                    └──────────┬───────────┘                     │
│                               ▼                                 │
│                    ┌─────────────────────┐                      │
│                    │ data/display/       │                      │
│                    │   {tf}.parquet      │  ← 缓存层             │
│                    └──────────┬──────────┘                      │
│                               │                                  │
│                               ▼                                  │
│                    ┌─────────────────────┐                      │
│                    │ _load_chart_data()  │                      │
│                    │ pd.read_parquet()   │                      │
│                    │ → (t, noisy, ohlc,  │                      │
│                    │    ticker_full,     │                      │
│                    │    dates, err)      │                      │
│                    └──────────┬──────────┘                      │
│                               │                                  │
│                               ▼                                  │
│                    ┌─────────────────────┐                      │
│                    │ session_state       │  ← Streamlit 缓存     │
│                    │   (图表数据)         │                      │
│                    └──────────┬──────────┘                      │
│                               │                                  │
│                               ▼                                  │
│                    ┌─────────────────────┐                      │
│                    │ _render_chart_      │                      │
│                    │   fragment()        │  ← 图表渲染            │
│                    └─────────────────────┘                      │
└──────────────────────────────────────────────────────────────────────────┘
```

### 1.2 浏览模式 vs 回测模式数据路径差异

| 步骤 | 浏览模式 | 回测模式 |
|------|----------|----------|
| 数据源 | DB (kline表) | DB (kline表) |
| 查询方式 | `query_kline(ticker, tf, n_pts, day_offset)` | `_query_tf_from_db(ticker, tf, cutoff_date, n_pts)` |
| 日期截断 | day_offset 偏移最新日期 | cutoff_date 严格截断 |
| 合成操作 | **无** — DB中每根K线是完整bar | **有** — 最后一个bar可能不完整，需从细粒度合成 |
| parquet 写入 | `_sync_to_display()` 逐周期 | `_sync_all_cascading()` 一次性级联 |
| parquet 写入时机 | 每次图表渲染前 | 回测推进一个bar前（一次性写入所有TF） |
| 回退策略 | parquet 失败 → yfinance API 重新拉取 | parquet 失败 → 返回错误，**不回退**到 yfinance |
| 复杂度 | O(1) per TF | O(TF数 * n_pts) — 取决于级联深度 |

---

## 2. _fetch_stock 逐行分析

### 2.1 函数签名

```python
def _fetch_stock(market, code, tf, n_pts, force_period=None)
    -> Tuple[t, noisy, ohlc, ticker_full, dates, err]
```

### 2.2 执行流程详解

#### 步骤 1: 股票代码拼接 (行 83-91)

```
market="A股(沪深)", code首字符为'6' → code + ".SS"  (上海)
market="A股(沪深)", code首字符非'6' → code + ".SZ"  (深圳)
market="港股 HK"             → code.zfill(4) + ".HK"
其他                         → code.upper()
```

#### 步骤 2: period 与 interval 映射 (行 93-129)

**interval 映射** (yfinance 的 interval 参数):

| 中文周期 | yfinance interval |
|----------|-------------------|
| 1分钟 | `1m` |
| 5分钟 | `5m` |
| 15分钟 | `15m` |
| 60分钟 | `1h` |
| 日线 | `1d` |
| 周线 | `1wk` |
| 月线 | `1mo` |
| 季线 | `3mo` |

**period 自动计算** (yfinance 的 period 参数):

| 周期 | 计算逻辑 | 示例: n_pts=120 |
|------|----------|-----------------|
| 1分钟 | 固定 `7d` | `7d` |
| 5分钟 | 固定 `60d` | `60d` |
| 15分钟 | 固定 `60d` | `60d` |
| 60分钟 | 固定 `60d` | `60d` |
| 日线 | `wanted = max(n_pts*2, 10)`, 按阶梯映射 | wanted=240 → `2y` |
| 周线 | `wanted = max(n_pts*5, 52)`, 按阶梯映射 | wanted=600 → `10y` |
| 月线 | `wanted = max(n_pts*1.5, 12)`, 按阶梯映射 | wanted=180 → `max` |
| 季线 | 固定 `max` | `max` |

> **设计意图**: period 比实际需求大（如日线 `n_pts*2`），确保 yfinance 返回足够多的数据，再通过 DB 查询截取精确的 n_pts 条。

#### 步骤 3: yfinance API 调用 (行 131-133)

```python
data = yf.download(full, period=period, interval=interval, progress=False)
```

- `progress=False` 避免控制台输出
- 返回空 DataFrame 时直接报错

#### 步骤 4: 数据清洗 (行 135-155)

1. **MultiIndex columns flatten** (行 135-136): yfinance 有时返回 `(Price, Ticker)` 多层列索引，flatten 为单层
2. **日线 Close NaN 回退** (行 141-153): **仅日线触发**。当最后一条日线 Close 为 NaN 时（Yahoo 日线 API 延迟结算），用周线 API 的 Close 回填。这是针对 A 股/港股收盘价 1-2 天延迟的 workaround
3. **dropna** (行 155): 删除 Close 为 NaN 的行

#### 步骤 5: DB 写入 (行 158-161)

调用 `upsert_kline(code, tf, data)` 全量写入 SQLite。

**upsert_kline 的智能策略** (db.py 行 67-115):
- 找到该周期最新日期 `MAX(ts)`
- **历史 bar** (`ts < last_ts`): `INSERT OR IGNORE` — 已完成的 bar 不覆盖
- **最新 bar** (`ts >= last_ts`): `INSERT OR REPLACE` — 最新 bar 可能未完成，允许覆盖
- 这保证了历史数据稳定，同时当前未完成 bar 可以被后续刷新更新

#### 步骤 6: DB 查询返回 (行 164-171)

```python
result_df = query_kline(code, tf, n_pts, day_offset=0)
```
从 DB 取最后 n_pts 条，返回：
- `t`: `np.arange(n)` — 整数索引
- `noisy`: `Close.values.ravel()` — 收盘价一维数组
- `ohlc`: 包含 Open/High/Low/Close 的 DataFrame
- `ticker_full`: yfinance 格式的完整代码（如 "600000.SS"）
- `dates`: `pd.DatetimeIndex`
- `err`: 成功时为 None

---

## 3. _sync_to_display 逐行分析

### 3.1 函数签名

```python
def _sync_to_display(ticker_code, tf, day_offset=0, n_pts=120, cutoff_date=None)
    -> Tuple[bool, int]
```

### 3.2 两种模式对比

#### 浏览模式 (cutoff_date=None)

**流程** (行 219-226):

```
query_kline(ticker_code, tf, n_pts, day_offset=day_offset)
  │
  ├─ 1. SELECT MAX(ts) FROM kline → 找最新日期
  ├─ 2. day_offset > 0 → datetime(MAX(ts), '-{day_offset} days') → cutoff
  ├─ 3. SELECT ... WHERE ts <= cutoff ORDER BY ts DESC LIMIT n_pts
  └─ 4. 返回升序 DataFrame
       │
       ▼
  df["Date"] = pd.to_datetime(df["Date"])
  df.to_parquet(display/{tf}.parquet)
```

**day_offset 语义**: 从最新数据日期往前偏移 day_offset 天，取该日期之前的 n_pts 条数据。例如 day_offset=5 表示取 5 天前的最后 120 条。

#### 回测模式 (cutoff_date="YYYY-MM-DD")

**流程** (行 199-216):

```python
conn.execute(
    """SELECT ts, open, high, low, close, volume
       FROM kline WHERE ticker=? AND timeframe=? AND ts <= ?
       ORDER BY ts DESC LIMIT ?""",
    (ticker_code, tf, cutoff_date, n_pts),
)
rows.reverse()  # DESC → ASC
df.to_parquet(display/{tf}.parquet)
```

**关键区别**:
- 回测模式直接裸 SQL 查询，不经过 `query_kline()`
- 用 `cutoff_date` 做日期截断，不是 `day_offset`
- 结果反转后写入 parquet

### 3.3 缓存策略

| 条件 | 行为 |
|------|------|
| parquet 文件存在 | **不重新写入**。`_sync_to_display` 的调用方 `_load_chart_data` 先写后读，但如果 parquet 已存在则跳过 |
| `len(df) < 5` | 返回 `(False, len(df))`，触发 yfinance API 回退（仅浏览模式） |
| 正常 | 写入 parquet，返回 `(True, len(df))` |

> **实际上每次调用的行为**: `_sync_to_display` 每次被调用都会覆盖写入 parquet。真正的"缓存读取"在 `_load_chart_data` 层: 浏览模式每次 rerun 都会重新 `_sync_to_display` → 写入 parquet → 读取 parquet；回测模式则是 `_sync_all_cascading` 一次性写入后，`_load_chart_data` 只做读取。

---

## 4. _sync_all_cascading 级联合成核心分析

### 4.1 函数签名

```python
def _sync_all_cascading(ticker_code, tfs, cutoff_date, min_tf, n_pts=120) -> dict
```

### 4.2 参数说明

| 参数 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `ticker_code` | str | 股票代码 | `"AAPL"` |
| `tfs` | list[str] | 需要处理的周期列表，按**从细到粗**排列 | `["60分钟", "日线", "周线"]` |
| `cutoff_date` | str | 回测截止日期 | `"2026-07-03T14:47:00+08:00"` |
| `min_tf` | str | 最细粒度周期（不做合成） | `"60分钟"` |
| `n_pts` | int/dict | 每周期数据点数，dict 时 key=周期名 | `120` 或 `{"60分钟":120, "日线":120}` |

### 4.3 完整执行流程分解

```
输入: tfs = ["60分钟", "日线", "周线"], cutoff_date = "2026-07-03T14:47:00+08:00", min_tf = "60分钟"

Iteration 1: tf = "60分钟" (min_tf)
  ├─ _query_tf_from_db("AAPL", "60分钟", cutoff, 120)
  │   → [{"Date":"2026-07-03T14:00:00+08:00", O=100, H=102, L=99, C=101, V=1000}, ...]
  ├─ needs_synth = False (tf == min_tf, skip synthesis)
  ├─ _build_output_df(db_rows, None, 120) → 120 rows
  ├─ _write_parquet("60分钟", df) → True
  └─ synth_cache["60分钟"] = {"synth_bar": None, "db_sample_ts": "2026-07-03T14:00:00+08:00"}

Iteration 2: tf = "日线"
  ├─ _query_tf_from_db("AAPL", "日线", cutoff, 120)
  │   → [{"Date":"2026-07-01T00:00:00", ...}, {"Date":"2026-07-02T00:00:00", ...}]
  ├─ _needs_synthesis("日线", db_rows, cutoff)
  │   last_ts = 2026-07-02T00:00:00
  │   cutoff_dt = 2026-07-03T14:47:00+08:00
  │   → 14:47 > 00:00 → True ✓
  ├─ _find_immediate_finer_tf("日线", tfs) → "60分钟"
  ├─ _synthesize_incomplete_bar(
  │     target_tf="日线", db_rows, cutoff,
  │     ticker_code="AAPL", finer_tf="60分钟",
  │     finer_synth_bar=None  // 60分钟是 min_tf, 没有合成bar
  │   )
  │   ├─ last_ts = 2026-07-02T00:00:00
  │   ├─ query_start = 2026-07-02T00:00:00
  │   ├─ 日线合成从60分钟: market_open = 09:30 (仅当日线+finer=60分钟触发)
  │   │   query_start(07-02 00:00) < market_open(07-03 09:30) < cutoff(07-03 14:47)
  │   │   → actual_start = 2026-07-03T09:30:00
  │   ├─ ★ 时区转换 v3:
  │   │   finer_tz = "+08:00" (从60分钟DB采样)
  │   │   cutoff_in_finer = cutoff.tz_convert(+08:00)
  │   │   actual_in_finer = actual_start.tz_localize(+08:00)
  │   ├─ _query_tf_for_period("AAPL", "60分钟", "2026-07-03T09:30:00+08:00", "2026-07-03T14:47:00+08:00")
  │   │   → [{"Date":"2026-07-03T09:30:00+08:00", O=100,H=103,L=99,C=103,V=2000},
  │   │      {"Date":"2026-07-03T14:00:00+08:00", O=103,H=106,L=102,C=105,V=3000}]
  │   ├─ 跨周期过滤: target_tf="日线"
  │   │   → 同日过滤: Date[:10] == "2026-07-03"
  │   │   → 两条都保留 (同一天)
  │   ├─ _aggregate_bars(finer_bars, synth_date)
  │   │   → O=100, H=106, L=99, C=105, V=5000
  │   └─ 返回 {Date, Open, High, Low, Close, Volume}
  ├─ _build_output_df(db_rows, synthesized_bar, 120)
  │   → 替换最后一条DB行 (07-02) 为合成bar → 仍为120行
  ├─ _write_parquet("日线", df) → True
  └─ synth_cache["日线"] = {"synth_bar": {O=100,C=105,...}, "db_sample_ts": "..."}

Iteration 3: tf = "周线"
  ├─ _query_tf_from_db("AAPL", "周线", cutoff, 120)
  │   → [{"Date":"2026-06-26T00:00:00", ...}]  (上周五的周线)
  ├─ _needs_synthesis("周线", db_rows, cutoff)
  │   last_ts = 2026-06-26T00:00:00
  │   cutoff_dt = 2026-07-03T14:47:00+08:00
  │   → 14:47 > 00:00 → True ✓
  ├─ _find_immediate_finer_tf("周线", tfs) → "日线"
  ├─ _synthesize_incomplete_bar(
  │     target_tf="周线", ..., finer_tf="日线",
  │     finer_synth_bar = synth_cache["日线"]["synth_bar"]  ← 级联!
  │   )
  │   ├─ 从DB查询细粒度: _query_tf_for_period("日线", ...)
  │   │   → [{"Date":"2026-06-29","O":97,"C":98}, {"Date":"2026-06-30","O":98,"C":99},
  │   │      {"Date":"2026-07-01","O":99,"C":100}, {"Date":"2026-07-02","O":100,"C":101}]
  │   ├─ 合并 finer_synth_bar (日线的合成bar):
  │   │   synth_ts = 2026-07-03T14:47 在 [actual_start, cutoff] 范围内
  │   │   替换最后一条DB日线bar (07-02) 为合成日线bar
  │   ├─ 跨周期过滤: target_tf="周线"
  │   │   cutoff_dt = 2026-07-03 (周四), week_start = 06-29 (周一)
  │   │   → 过滤 Date[:10] >= "2026-06-29"
  │   │   → 所有4条日线bar + 合成日线bar 都在本周，全部保留
  │   ├─ _aggregate_bars(...)
  │   │   → O=97, H=..., L=..., C=105 (来自合成日线的Close), V=...
  │   └─ 返回合成周线bar
  ├─ _build_output_df(db_rows, synthesized_bar, 120)
  │   → 替换最后一条DB周线行 (06-26) 为合成周线bar
  └─ synth_cache["周线"] = {"synth_bar": {...}, ...}
```

### 4.4 关键子函数详解

#### _needs_synthesis(tf, db_rows, cutoff_date) — 合成判定

```
修复前 (Bug): 检查 cutoff >= period_start
  - 日线: period_start = 次日00:00 → 同日15:45 < 次日00:00 → 永远不合成 ✗

修复后 (v2): 检查 cutoff_dt >= last_ts
  - 日线: last=07-02 00:00, cutoff=07-02 15:45 → 15:45 >= 00:00 → 合成 ✓
  - 边界: 当 cutoff == last_ts 时也返回 True (DB bar 是收盘后完整版，需替换为部分视图)
```

**核心思想**: 只要截止时间在最后一个完整 K 线之后（含等于），就需要合成。不要求跨过完整周期边界。

#### _synthesize_incomplete_bar() — 单周期合成

**9 个子步骤**:

1. **计算查询窗口**: `query_start = last_ts`, `cutoff_dt = cutoff_date`
2. **日线特殊处理**: 从 60 分钟合成日线时，query_start 调整为当日 09:30（A股开盘时间）
3. **时区转换 v3 Bugfix**: 真正的 `tz_convert()` 替代字符串拼接
4. **查询细粒度 DB**: `_query_tf_for_period(ticker, finer_tf, start, end)`
5. **合并 finer synth bar**: 细粒度的合成 bar 如在范围内则替换/追加
6. **跨周期过滤**: 按目标周期类型过滤只保留当前周期的 fine bar
7. **聚合**: `_aggregate_bars()` → OHLCV
8. **格式化日期**: `_format_synth_date()` → 保持与周期一致的日期格式
9. **返回** 合成 bar dict 或 None

#### 时区转换 v3 Bugfix (行 593-614)

**问题**: v1/v2 采用字符串拼接时区偏移，"14:45+08:00" 去时区 + 加 "-04:00" = "14:45-04:00"，这是完全不同的绝对时间。

**修复**: 使用 `pd.Timestamp.tz_convert()` 做真正的时区转换，将 cutoff 和 actual_start 转到 finer_tf 的时区后再查询。

#### 跨周期过滤 (行 641-672)

| 目标周期 | 过滤规则 | 示例 (cutoff=7/3 Thu) |
|----------|----------|----------------------|
| 分钟级 | 同日 `Date[:10] == cutoff日期` | 只保留 7/3 的 bar |
| 日线 | 同日 `Date[:10] == cutoff日期` | 只保留 7/3 的 bar |
| 周线 | `Date >= 本周一` | 6/29(Mon) 起 |
| 月线 | `Date >= 本月1日` | 7/1 起 |
| 季线 | `Date >= 本季首日` (Q3 = 7/1) | 7/1 起 |

**作用**: 防止查询窗口 [last_ts, cutoff] 跨周末/月底/季末时拉入前一完整周期的 bar。

### 4.5 级联合成的数据传播

```
min_tf(60分钟) DB bars     →  无合成, 直接写入 parquet
        │
        │ (DB bars 作为 finer 数据源)
        ▼
日线 DB bars              →  合成 日线 bar (从60分钟聚合)
        │                   synth_cache["日线"].synth_bar = {O=100,C=105,...}
        │
        │ (DB bars + synth_cache["日线"].synth_bar)
        ▼
周线 DB bars              →  合成 周线 bar (从日线DB+ 日线合成bar 聚合)
        │                   synth_cache["周线"].synth_bar = {...}
        │
        ▼
月线 DB bars              →  合成 月线 bar (从日线DB+ 日线合成bar 聚合)
        │
        ▼
季线 DB bars              →  合成 季线 bar (从日线DB+ 日线合成bar 聚合)
```

**级联语义**: 周线合成时使用日线数据（含日线合成 bar → 含当天的部分视图），保证了周线的 Close 精确等于日线合成 bar 的 Close。测试验证了 `日线.Close == 周线.Close` 的传播一致性。

### 4.6 关于不完整 bar 的处理

**问题**: 如果粗粒度周期的最后一个 bar 不完整（例如周三时周线只有周一到周三的数据），如何处理？

**答案**: 这正是 `_needs_synthesis` 要解决的问题。

1. **DB 中有上周五的完整周线 bar** (Date=06-26, Close 是周五收盘价)
2. **cutoff 在周三** (07-03 14:47)，cutoff > 上周五的 bar 的 last_ts
3. **触发合成**: 从日线数据（本周一到周三的日线 bar + 周三当天的合成日线 bar）聚合出本周的部分周线 bar
4. **合成 bar 的 Close** = 最后一条日线 bar(或合成日线) 的 Close = 周三 14:47 的最新价
5. **替代 DB 中上周五的 bar**: `_build_output_df` 的 `data[-1] = synthesized_bar` 逻辑

**这就是级联合成的核心价值**: 在回测的任意时刻，所有周期看到的"最新 bar"都是截止到 cutoff_date 的部分视图，不会包含未来数据。

### 4.7 返回值结构

```python
results: dict[str, bool]
# {"60分钟": True, "日线": True, "周线": True, "月线": True}
```

- 键: 周期名称
- 值: parquet 是否写入成功
- 如果所有 TF 都失败，调用方 `streamlit_app.py` 会显示 sidebar 警告

---

## 5. _fetch_all_timeframes 分析

### 5.1 执行模型

```python
with ThreadPoolExecutor(max_workers=8) as exec:
    futures = {exec.submit(_fetch_one, tf): tf for tf in tf_config}
    for fut in as_completed(futures):
        tf, ok, detail = fut.result()
        results[tf] = (ok, detail)
```

- **8 线程并行**: 一次性提交所有 8 个周期的 yfinance 请求
- **非阻塞收集**: `as_completed()` 谁先完成先收集谁
- **force_period 覆盖**: 分钟级周期用固定 period（7d/60d），日线及以上用 "max"
- **n_pts=99999**: 传入极大值是为了让 period 计算走 `max` 分支，实际返回由 DB `query_kline` 截取

### 5.2 各周期 force_period 配置

```python
tf_config = {
    "1分钟": ("7d",), "5分钟": ("60d",), "15分钟": ("60d",),
    "60分钟": ("730d",), "日线": ("max",), "周线": ("max",),
    "月线": ("max",), "季线": ("max",),
}
```

**注意**: 60 分钟用 `730d` (约 2 年)，不同于浏览模式的自动 `60d`。这是为了确保首次拉取时有足够的分钟级历史数据。

### 5.3 数据写入 DB 流程

每次 `_fetch_stock` 内部都调用 `upsert_kline()`。`_fetch_all_timeframes` 并行调用 8 次 `_fetch_stock`，每次独立写入 DB。

**并发安全**: SQLite 在 WAL 模式下支持多读单写，8 个线程同时 upsert 可能触发 `SQLITE_BUSY`。`busy_timeout=5000` (5 秒) 提供一定容错，但不是完美的并发写入方案。实际场景中 8 个线程不太可能同时完成 DB 写入，风险较低。

---

## 6. 缓存层与一致性分析

### 6.1 缓存层次架构

```
Layer 1: yfinance HTTP 响应 (外部, 非受控)
    │
    ▼
Layer 2: SQLite DB (data/market.db) — 持久化缓存
    ├─ 表: kline(ticker, timeframe, ts, open, high, low, close, volume)
    ├─ 索引: idx_kline_lookup(ticker, timeframe, ts)
    ├─ WAL 模式, busy_timeout=5000ms
    └─ upsert 策略: 历史 bar INSERT OR IGNORE, 最新 bar INSERT OR REPLACE
         │
         ▼
Layer 3: Parquet 文件 (data/display/{tf}.parquet) — 展示层缓存
    ├─ 浏览模式: 每次 rerun 重写
    ├─ 回测模式: _sync_all_cascading 一次性写入后不再重写
    └─ clear_display_cache() 清除所有 parquet 文件
         │
         ▼
Layer 4: Streamlit session_state — 内存缓存
    ├─ _cached_fetch_stock: @st.cache_data 装饰的函数缓存
    ├─ 图表数据在 _load_chart_data 中加载到 session_state
    └─ 切换股票时 clear() 清除
         │
         ▼
Layer 5: 图表渲染 (Plotly) — 最终展示
```

### 6.2 回测模式下的 parquet 读写时机

```
回测推进一个 bar:
  │
  ├─ 1. _sync_all_cascading() 被调用
  │      ├─ 遍历所有在用 TF (从 streamlit_app.py line 1830)
  │      ├─ 对每个 TF: DB查询 → 判断合成 → 聚合 → 写入 parquet
  │      └─ 所有 TF 的 parquet 一次性全部更新
  │
  ├─ 2. 每个视图的 _load_chart_data() 被调用
  │      └─ pd.read_parquet(display/{tf}.parquet)
  │         (window_start != None → 回测模式, 直接读 parquet)
  │
  └─ 3. 图表渲染
```

**关键**: 回测前进一个 bar 时，`_sync_all_cascading` **每次都会重新执行**（从 `streamlit_app.py` line 1830 看，没有缓存判断）。每次都会：
- 重新从 DB 查询
- 重新判断是否需要合成
- 重新聚合（如果需要）
- 重新写入 parquet

写入的数据窗口: 每个周期 cutoff_date 之前的最后 `n_pts` 条（n_pts 由各视图配置决定）。

### 6.3 浏览模式下的 parquet 读写时机

```
浏览模式每次 rerun:
  │
  ├─ _sync_to_display() 被调用 (每视图每周期)
  │   ├─ query_kline(ticker, tf, n_pts, day_offset) → 从 DB 取
  │   └─ df.to_parquet(display/{tf}.parquet) → 覆盖写入
  │
  └─ _load_chart_data() 读 parquet
      └─ 失败则回退到 _cached_fetch_stock → yfinance API
```

---

## 7. 时间一致性问题标注

### 7.1 已发现并修复的问题

| # | 问题 | 位置 | 严重性 | 修复状态 |
|---|------|------|--------|----------|
| 1 | **日线合成永不触发 bug** | `_needs_synthesis` | 高 | 已修复 (v2) |
|   | 旧逻辑检查 `cutoff >= period_start`，日线的 period_start 是次日 00:00，同日 15:45 < 次日 00:00 为 False | 行 490-519 | | `cutoff_dt >= last_ts` |
| 2 | **时区字符串拼接 bug** | `_synthesize_incomplete_bar` | 高 | 已修复 (v3) |
|   | v1/v2 直接 strip+append 时区偏移，"14:45+08:00" → "14:45-04:00" 是不同绝对时间 | 行 593-614 | | `tz_convert()` |
| 3 | **跨周期数据泄漏** | `_synthesize_incomplete_bar` | 中 | 已修复 |
|   | 查询窗口 [last_ts, cutoff] 跨周末拉入前一周期 bar | 行 641-672 | | 跨周期过滤 |

### 7.2 潜在时间一致性问题（标注）

#### 问题 A: 多周期 cutoff_date 对齐不一致

**位置**: `_sync_all_cascading` 与 `_synthesize_incomplete_bar`

**描述**: 所有周期共享同一个 `cutoff_date`，但不同周期的"最后一根完整 bar"语义不同：
- 60 分钟的 last_ts = `2026-07-03T14:00:00+08:00`
- 日线的 last_ts = `2026-07-02T00:00:00` (或 `2026-07-03T00:00:00` 如果 DB 中已有当日日线)
- 周线的 last_ts = `2026-06-26T00:00:00` (上周五)

所有这些周期都使用同一个 cutoff_date `2026-07-03T14:47:00+08:00`。**这是正确的设计**——cutoff_date 代表"回测走到的时间点"，各周期从这个时间点向前看 n_pts 条数据。一致性取决于 `_needs_synthesis` 是否正确判断每个周期是否需要合成。

**风险等级**: 低。当前 `cutoff_dt >= last_ts` 的判断对所有周期一致。

#### 问题 B: 日线 last_ts 可能包含"当日已完成日线"

**位置**: `_query_tf_from_db` 查询条件 `ts <= cutoff_date`

**描述**: 如果 DB 中已有 `2026-07-03T00:00:00` 的日线 bar（例如当日收盘后），且 cutoff_date 也是 7/3 但时间更晚（如 `14:47`），`query_tf_from_db` 会返回这条 bar 作为 `db_rows[-1]`。

此时 `_needs_synthesis`: `cutoff_dt(14:47) >= last_ts(00:00)` → True，触发合成。合成 bar 替换这个 DB 日线 bar。

**但这里有个微妙问题**: DB 中的 `07-03 00:00` 日线 bar 是 yfinance 当日收盘后的完整 bar（含 09:30-15:00 全部数据），但 cutoff 在 14:47，合成 bar 只含 09:30-14:47 的部分数据。**合成 bar 正确替换了 DB bar，避免了未来数据泄漏。**

**风险等级**: 已通过 `_build_output_df` 的 `data[-1] = synthesized_bar` 正确处理。

#### 问题 C: 浏览模式与回测模式的 parquet 混合

**位置**: `_sync_to_display` 和 `_sync_all_cascading` 都写入 `data/display/{tf}.parquet`

**描述**: 两个函数都写入相同的 parquet 文件路径。如果在回测模式后切回浏览模式，parquet 文件仍然是上次回测写入的内容（截断到 cutoff_date）。浏览模式的 `_sync_to_display` 会立即覆盖。

**风险等级**: 低。`_load_chart_data` 在浏览模式下始终先调 `_sync_to_display` 再读 parquet，保证了覆盖。

#### 问题 D: DB 写入与 parquet 读取之间的时间窗口

**位置**: `_fetch_stock` 写入 DB → `_sync_to_display`/`_sync_all_cascading` 读取 DB → 写入 parquet → `_load_chart_data` 读取 parquet

**描述**: 在 `_fetch_all_timeframes` 并行写入 DB 的过程中，如果某个周期的 upsert 慢了，其他周期的 `_sync_all_cascading` 可能读到不完整的数据。

**但在当前代码流程中**: `_fetch_all_timeframes` 是数据首次加载时调用，`_sync_all_cascading` 是回测推进时调用，两者不在同一时刻执行。且 `_sync_all_cascading` 不等待任何写入，它直接从 DB 读取已存在的数据。

**风险等级**: 低。两个操作不在同一代码路径中同时执行。

#### 问题 E: min_tf 的 DB bar 可能也是"不完整"的

**位置**: `_sync_all_cascading` 行 777: `tf != min_tf`

**描述**: min_tf 不做合成。但如果 min_tf 是 `60分钟`，且 cutoff 在 `14:47`，DB 中可能已有 `14:00` 的 bar 和 `15:00` 的 bar。`14:00` bar 是完整的（已过 15:00），但 `15:00` bar 可能不存在（还没到 15:00）或正在形成中。min_tf 直接从 DB 取数据，不进行任何合成。

**这意味着**: min_tf 看到的最后一条 bar 可能是已完成的 bar（如果 cutoff 时间还没到下一个 bar 的开始），也可能是 yfinance 实时推送的未完成 bar 的 DB 记录。

**风险等级**: 低。对于分钟级周期，频率足够高，bar 的完成状态对回测影响很小。

### 7.3 时间一致性总结

| 维度 | 状态 | 说明 |
|------|------|------|
| 各周期 cutoff 对齐 | 一致 | 所有周期共享同一个 cutoff_date |
| 合成 bar 替换 DB bar | 正确 | `_build_output_df` 的 `data[-1] = synthesized_bar` |
| 跨周期数据泄漏 | 已防护 | 跨周期过滤（同日/本周/本月/本季） |
| 时区转换 | 正确 v3 | `tz_convert()` 替代字符串拼接 |
| 级联 Close 传播 | 已验证 | 测试确认 日线.Close == 周线.Close |
| 回退到 yfinance 的时间一致性 | 已防护 | 回测模式下 `_is_backtest=True` 时禁止 API 回退 |

---

## 8. 复杂度分析

### 8.1 各步骤时间复杂度

| 步骤 | 复杂度 | 说明 |
|------|--------|------|
| **yfinance API 调用** | O(1) | 单次 HTTP 请求，与 n_pts 无关 |
| **_fetch_stock 的 period 计算** | O(1) | if-elif 阶梯判断 |
| **upsert_kline** | O(n) | n = yfinance 返回的 bar 数，每条 INSERT OR IGNORE/REPLACE |
| **query_kline (浏览模式)** | O(n_pts) | ORDER BY DESC LIMIT n_pts，SQLite 索引扫描 |
| **_sync_to_display (浏览模式)** | O(n_pts) | query_kline + DataFrame 构造 + parquet 写入 |
| **_sync_to_display (回测模式)** | O(n_pts) | 裸 SQL + 反转 + parquet 写入 |
| **_sync_all_cascading** | O(k * (n_pts + m)) | k=TF数, n_pts=每条周期查询量, m=细粒度bar数 |
| **_needs_synthesis** | O(1) | 只比较时间戳 |
| **_synthesize_incomplete_bar** | O(m + f) | m=DB查询细粒度bar, f=跨周期过滤 |
| **_aggregate_bars** | O(f) | pandas max/min/sum 向量化, f=过滤后bar数 |
| **_build_output_df** | O(n_pts) | DataFrame 构造 + 尾部截断 |
| **_write_parquet** | O(n_pts) | 文件 I/O |
| **_find_immediate_finer_tf** | O(k) | 在 ALL_TFS 中线性查找 |
| **_fetch_all_timeframes** | O(8 * T_fetch) | 8 线程并行, T_fetch 单次 yfinance 请求时间 |
| **_load_chart_data** | O(n_pts) | parquet 读取 + DataFrame 操作 |

### 8.2 级联合成最坏情况

假设 `tfs = ["1分钟", "5分钟", "15分钟", "60分钟", "日线", "周线", "月线", "季线"]`，每个周期取 120 条:

- **DB 查询**: 8 * 120 条 SQL 查询 → O(960)
- **合成链**: 日线从 60 分钟合成 (最多 8 根 60 分钟 bar)，周线从日线合成 (最多 5 根日线 bar)，月线从日线合成 (最多 ~22 根日线 bar)，季线从日线合成 (最多 ~66 根日线 bar)
- **总计聚合操作**: ~100 条 bar 的 max/min/sum → 可忽略不计

**整体时间复杂度**: O(k * n_pts)，其中 k 是周期数，n_pts 是每条周期的数据点数。对 8 周期 * 120 点，毫秒级完成。

---

## 附录: 关键函数调用链

```
streamlit_app.main()
  │
  ├─ [数据首次加载]
  │   └─ _fetch_all_timeframes(market, ticker)
  │       └─ ThreadPoolExecutor(8 workers)
  │           └─ _fetch_stock(market, code, tf, 99999, force_period)
  │               ├─ yf.download(full, period, interval)
  │               ├─ upsert_kline(code, tf, data)
  │               └─ query_kline(code, tf, n_pts, day_offset=0)
  │
  ├─ [浏览模式渲染]
  │   └─ _load_chart_data(market, ticker_code, tf, day_offset, n_pts)
  │       ├─ _sync_to_display(ticker_code, tf, day_offset, n_pts)
  │       │   ├─ query_kline(ticker_code, tf, n_pts, day_offset)
  │       │   └─ df.to_parquet(display/{tf}.parquet)
  │       └─ pd.read_parquet(display/{tf}.parquet)
  │
  └─ [回测模式渲染]
      ├─ _sync_all_cascading(ticker_code, tfs_in_use, cutoff_date, min_tf, n_pts)
      │   └─ for tf in tfs (细→粗):
      │       ├─ _query_tf_from_db(ticker, tf, cutoff, n_pts)
      │       ├─ _needs_synthesis(tf, db_rows, cutoff)
      │       ├─ [if needs_synth]:
      │       │   ├─ _find_immediate_finer_tf(tf, tfs)
      │       │   └─ _synthesize_incomplete_bar(...)
      │       │       ├─ _query_tf_for_period(ticker, finer_tf, start, end)
      │       │       ├─ [merge finer_synth_bar]
      │       │       ├─ [跨周期过滤]
      │       │       └─ _aggregate_bars(finer_bars, synth_date)
      │       ├─ _build_output_df(db_rows, synthesized_bar, n_pts)
      │       └─ _write_parquet(tf, df)
      └─ _load_chart_data(market, ticker_code, tf, ..., cutoff_date)
          └─ pd.read_parquet(display/{tf}.parquet)
```
