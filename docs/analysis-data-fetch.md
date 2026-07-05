# 数据获取与入库链路分析

分析日期: 2026-07-05
源代码:
- `/Users/xfpan/claude/filter_research/filter_app/services/data_loader.py`
- `/Users/xfpan/claude/filter_research/filter_app/db.py`

---

## 1. 全链路概览

```
yfinance API
    │
    ▼
_fetch_all_timeframes()  ─── ThreadPoolExecutor(max_workers=8)
    │                          8 个周期并行拉取
    ▼
_fetch_stock()  ─── 单个 (ticker, tf) 的完整流程
    │     │
    │     ├── yf.download(full, period, interval)   ← 拉取原始数据
    │     ├── 日线 Close 回退（周线补丁）              ← 数据修正
    │     ├── upsert_kline(code, tf, data)           ← 写入 SQLite
    │     └── query_kline(code, tf, n_pts)           ← 读回并返回
    │
    ▼
upsert_kline()  ─── 分段策略: 历史 IGNORE + 最新 REPLACE
```

---

## 2. `_fetch_all_timeframes()` — 并行拉取策略

**文件**: `data_loader.py:17-42`

### 2.1 8 周期配置

| 周期 | yfinance interval | force_period |
|------|-------------------|-------------|
| 1分钟 | `1m` | `7d` |
| 5分钟 | `5m` | `60d` |
| 15分钟 | `15m` | `60d` |
| 60分钟 | `1h` | `730d` |
| 日线 | `1d` | `max` |
| 周线 | `1wk` | `max` |
| 月线 | `1mo` | `max` |
| 季线 | `3mo` | `max` |

### 2.2 执行模型

```python
with ThreadPoolExecutor(max_workers=8) as exec:
    futures = {exec.submit(_fetch_one, tf): tf for tf in tf_config}
    for fut in as_completed(futures):
        tf, ok, detail = fut.result()
        results[tf] = (ok, detail)
```

- **8 个 worker 并行**: 所有周期同时拉取，不等待彼此
- **`as_completed` 收集**: 哪个先完成就先处理，不按顺序阻塞
- **返回值**: `Dict[str, Tuple[bool, Any]]` — 每个周期的成功/失败状态和详情
- **异常隔离**: 单个周期失败不影响其他周期，异常被 `logger.warning` 捕获

### 2.3 并行度分析

- yfinance 的 `download()` 是同步阻塞调用
- 8 线程并行意味着 8 个 HTTP 请求同时发出
- Yahoo Finance API 对并发有一定容忍度，8 线程在合理范围内
- 瓶颈在于网络延迟和数据量（分钟级数据量大）

---

## 3. `_fetch_stock()` — 单周期完整流程

**文件**: `data_loader.py:45-136`

### 3.1 代码拼接规则

```python
if market == "A股(沪深)":
    suffix = ".SS" if code[0] == "6" else ".SZ"    # 6开头→上交所, 其他→深交所
    full = code + suffix
elif market == "港股 HK":
    full = code.zfill(4) + ".HK"                    # 补零到4位
else:
    full = code.upper()                             # 美股直接大写
```

### 3.2 Period 动态计算

对于非分钟级周期，根据 `n_pts` 动态计算 `period` 参数，减少不必要的数据传输:

**日线**:
| n_pts 范围 | period |
|-----------|--------|
| <= 30 | `1mo` |
| <= 90 | `3mo` |
| <= 180 | `6mo` |
| <= 365 | `1y` |
| <= 730 | `2y` |
| <= 1825 | `5y` |
| <= 3650 | `10y` |
| > 3650 | `max` |

周线和月线有类似的阶梯映射。`force_period` 参数可覆盖此逻辑（用于全量拉取场景）。

### 3.3 日线 Close 回退机制

```python
if interval == "1d" and len(data) > 0:
    last_close = data["Close"].iloc[-1]
    if pd.isna(last_close):
        # 用周线 Close 回填日线最后一条
        w = yf.download(full, period="5d", interval="1wk", progress=False)
        w_close = w["Close"].iloc[-1]
        if not pd.isna(w_close):
            data.loc[data.index[-1], "Close"] = float(w_close)
```

**背景**: Yahoo 日线 API 最后一条 bar 的 Close 偶尔为 NaN（未结算），但周线 API 已通过实时行情计算出本周 Close。此机制解决了 A 股/港股收盘价 1-2 天延迟的问题。

### 3.4 数据清洗

```python
# 处理 MultiIndex 列（yfinance 在某些版本返回多级列名）
if isinstance(data.columns, pd.MultiIndex):
    data.columns = data.columns.droplevel(1)

# 去除 Close 为 NaN 的行
data = data[data["Close"].notna()]
```

### 3.5 写入 + 读回

```python
# 全量写入 SQLite
upsert_kline(code, tf, data)

# 从 DB 返回最后 n_pts 条
result_df = query_kline(code, tf, n_pts, day_offset=0)
```

**设计考量**: 不是直接返回 yfinance 的 DataFrame，而是写入后再从 DB 读回。好处是:
- 确保调用方拿到的数据与 DB 中一致
- 利用 `query_kline` 的排序和截断逻辑
- 返回格式统一（Date, Open, High, Low, Close, Volume）

---

## 4. `upsert_kline()` — 分段写入策略

**文件**: `db.py:50-83`

### 4.1 核心逻辑

```
新数据到达 → 按 ts 与 DB 中 MAX(ts) 比较
    │
    ├── ts < MAX(ts): 历史 bar → INSERT OR IGNORE（不覆盖已完成的历史数据）
    │
    └── ts >= MAX(ts): 最新 bar → INSERT OR REPLACE（可能未完成，需更新）
```

### 4.2 实现细节

```python
# 找到该周期最新日期
last_ts = conn.execute(
    "SELECT MAX(ts) FROM kline WHERE ticker=? AND timeframe=?", ...)

# 分段
history = [r for r in records if r[2] < last_ts]   # 严格小于
recent  = [r for r in records if r[2] >= last_ts]  # 大于等于

# 批量写入
if history:
    conn.executemany("INSERT OR IGNORE INTO kline ...", history)  # 不覆盖
for r in recent:
    conn.execute("INSERT OR REPLACE INTO kline ...", r)            # 可覆盖
```

### 4.3 设计意图

| 场景 | 策略 | 原因 |
|------|------|------|
| 历史 bar（已完成） | `INSERT OR IGNORE` | 历史数据不会变化，避免重复写入 |
| 最新 bar（可能未完成） | `INSERT OR REPLACE` | 当前 bar 的 OHLC 在盘中持续更新 |
| 空 DB（首次写入） | 全部走 history 分支 | `last_ts=None`，所有数据作为历史写入 |

### 4.4 边界情况

- **数据修正**: 如果 yfinance 修正了历史数据（如拆股调整），`INSERT OR IGNORE` 不会更新。需要 `force_update_kline()` 手动处理
- **跨周期一致性**: 不同周期的 `MAX(ts)` 独立计算，互不干扰
- **时间戳格式**: `ts` 使用 `idx.isoformat()` 转换，依赖 pandas Timestamp 的 ISO 格式

---

## 5. `query_kline()` — 查询接口

**文件**: `db.py:86-133`

### 5.1 双模式

**浏览模式** (`offset=None`):
```sql
-- 获取最新日期
SELECT MAX(ts) FROM kline WHERE ticker=? AND timeframe=?
-- 支持 day_offset 日期偏移
SELECT datetime(MAX(ts), '-N days') ...
-- 查询窗口
SELECT ... WHERE ticker=? AND timeframe=? AND ts <= ?
ORDER BY ts DESC LIMIT ?
-- Python: df.iloc[::-1] 反转为升序
```

**回测模式** (`offset=N`):
```sql
SELECT ... WHERE ticker=? AND timeframe=?
ORDER BY ts ASC LIMIT ? OFFSET ?
-- 直接按偏移量取窗口，不需要日期过滤
```

### 5.2 day_offset 机制

`day_offset` 用于浏览模式下的"回看"功能:
- `day_offset=0`: 取最新 N 条
- `day_offset=5`: 取 5 天前为截止的 N 条（查看历史某个时刻的图表）
- 实现: `SELECT datetime(MAX(ts), '-5 days')` 计算截止日期

---

## 6. 性能特征与潜在问题

### 6.1 性能瓶颈

| 阶段 | 操作 | 瓶颈 |
|------|------|------|
| yfinance download | HTTP 请求 | 网络延迟（秒级） |
| upsert_kline | SQLite 批量写入 | I/O（毫秒级，WAL 模式优化） |
| query_kline | SQLite 查询 | I/O（毫秒级，有索引） |

### 6.2 潜在问题

1. **yfinance 限流**: 8 线程并行可能触发 Yahoo 的临时限流，但当前未观察到
2. **数据量增长**: 分钟级数据量大（1分钟周期每天 240 条，7 天 = 1680 条），长期累积可能影响 DB 性能
3. **历史数据修正**: `INSERT OR IGNORE` 意味着历史数据一旦写入就不再更新，如遇拆股调整等需要手动修复
4. **时区处理**: yfinance 返回的时间戳带时区，`isoformat()` 转换后存储在 TEXT 列，字符串比较可能受时区影响
