# _sync_to_display() 从数据库到 parquet 的完整数据流深度分析

> 分析基准版本：基于 `feature/backtest-bar-synthesis` 分支（包含 bar 合成逻辑），同时标注当前 `feat/backtest-data-analysis` 分支的差异。
>
> 分析日期：2026-07-05
> 分析师：研究员

---

## 目录

1. [回测模式入口 — SQL 查询与参数溯源](#1-回测模式入口--sql-查询与参数溯源)
2. [时区后缀提取](#2-时区后缀提取)
3. [分钟级 bar 合成算法](#3-分钟级-bar-合成算法)
4. [DataFrame 构建与截断](#4-dataframe-构建与截断)
5. [Parquet 写入](#5-parquet-写入)
6. [_load_chart_data() 的 parquet 消费](#6-_load_chart_data-的-parquet-消费)
7. [数据流 ASCII 图](#7-数据流-ascii-图)
8. [边界条件与缺陷分析](#8-边界条件与缺陷分析)

---

## 1. 回测模式入口 — SQL 查询与参数溯源

### 1.1 调用链全景

```
渲染管线:
  main()
    → _render_chart_fragment()          [streamlit_app.py:502]
      → _render_chart()                  [streamlit_app.py:509]
        → _load_chart_data()             [streamlit_app.py:526]
          → _sync_to_display()           [streamlit_app.py:123-125] / data_loader.py:245
```

`cutoff_date` 的取值链路：

```
回测模式切换:
  _render_backtest_mode()                [streamlit_app.py:1267]
    → 设置 AppState["_bt_cutoff_date"]   [streamlit_app.py:1303]

导航按钮:
  按钮(如 "◀"):                          [streamlit_app.py:1175-1178]
    → _update_cutoff_and_rerun()          [streamlit_app.py:1131]
      → _get_bar_date_from_db()           [streamlit_app.py:1120]
      → AppState.set("_bt_cutoff_date", cutoff_date)

Slider 拖动:
  _on_slider_change():                   [streamlit_app.py:1145]
    → _get_bar_date_from_db()            [streamlit_app.py:1156]

自动播放:
  _run_backtest_play():                  [streamlit_app.py:1225]
    → _get_bar_date_from_db()            [streamlit_app.py:1260]
    → AppState.set("_bt_cutoff_date", cutoff_date)

最终传递给 _load_chart_data():
  cutoff_date = AppState.get("_bt_cutoff_date", "")  [streamlit_app.py:1615]
```

### 1.2 cutoff_date 的计算方式

`_get_bar_date_from_db()` 查询指定 bar_index 位置的 `ts` 值（以 `min_tf` 为查询基准）：

```python
# streamlit_app.py:1120-1128
def _get_bar_date_from_db(ticker_code, tf, bar_index):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT ts FROM kline WHERE ticker=? AND timeframe=? ORDER BY ts ASC LIMIT 1 OFFSET ?",
            (ticker_code, tf, bar_index),
        ).fetchone()
    return row[0] if row else ""
```

关键点：
- **bar_index 以 `min_tf`（最小周期）为基准**。例如，如果 4 个视图分别使用日线、60分钟、15分钟、5分钟，则 `min_tf` = "5分钟"。
- `bar_index` 表示"窗口结束位置"：当 `bar_index = total_bars` 时，表示窗口在最末尾（最新数据）。
- `cutoff_date = DB 中第 (bar_index - 1) 条 bar 的 ts`：这意味着窗口包含 **截止到 bar_index-1 位置** 的数据。
- `ORDER BY ts ASC LIMIT 1 OFFSET bar_index - 1`：取升序排列中的第 `bar_index-1` 条记录的 `ts`。

### 1.3 SQL 查询 — 为什么不走 query_kline()

```python
# data_loader.py:251-257 (backtest-bar-synthesis 分支)
rows = conn.execute(
    """SELECT ts, open, high, low, close, volume
       FROM kline WHERE ticker=? AND timeframe=? AND ts <= ?
       ORDER BY ts DESC LIMIT ?""",
    (ticker_code, tf, cutoff_date, n_pts),
).fetchall()
```

原因分析：

1. **`query_kline()` 不具备 `cutoff_date` 筛选能力**。`query_kline()`（db.py:86）只支持 `day_offset`（按天偏移）或 `offset`（按行偏移），不支持按截止日期查询。回测模式需要"截至某日期的最后 N 条"，这是 `query_kline()` 接口无法表达的语义。

2. **DESC vs ASC 设计**：`ORDER BY ts DESC` 的意图是"先拿到最新的 n_pts 条"，然后再 `reversed(rows)` 得到升序序列。这是 SQL 优化技巧——SQLite 无法对 `LIMIT` 子句应用 `ORDER BY ASC` 的索引优化方式，而 `DESC` 可以利用复合索引 `idx_kline_lookup(ticker, timeframe, ts)` 的降序遍历。

3. **`query_kline()` 的浏览模式也使用 DESC 查询**（db.py:121-126），也用了同样的 `SELECT ... ORDER BY ts DESC LIMIT n_pts` + 手动反转 `.iloc[::-1]` 模式。所以这个模式在代码库中是一贯的。

### 1.4 `ORDER BY ts DESC LIMIT n_pts` 的含义

```
SELECT ts, open, high, low, close, volume
FROM kline
WHERE ticker=? AND timeframe=? AND ts <= ?
ORDER BY ts DESC    -- 时间倒序：最新的在最前面
LIMIT ?             -- 取 n_pts 条
```

- **DESC 原因**：SQLite 的 `LIMIT` + `ORDER BY` 组合在 `DESC` 时可以利用索引直接取出最新的 N 行，而不需要先扫描所有匹配行再排序取最后 N 行。这是常见的 SQL 分页优化模式。
- **效果**：返回 `[(最新ts, ...), (次新ts, ...), ..., (最旧ts, ...)]`，即时间从新到旧的降序排列。
- **后续处理**：`reversed(rows)`（data_loader.py:316-317）反转为升序。

### 1.5 返回的数据结构

`rows` 是一个 `list[sqlite3.Row]`。通过 `conn.execute().fetchall()` 返回的是 SQLite Row 对象列表。

每个 `row` 的可迭代行为类似一个元组：

```python
row[0]  # ts       — ISO 格式时间字符串，如 "2024-01-15T09:30:00" 或 "2024-01-15 09:30:00+08:00"
row[1]  # open     — float
row[2]  # high     — float
row[3]  # low      — float
row[4]  # close    — float
row[5]  # volume   — float
```

单个 row 可以通过 `tuple(row)` 转换为元组，也可以通过 `row[列名]` 按列名访问（因为 `get_conn()` 设置了 `conn.row_factory = sqlite3.Row`）。

---

## 2. 时区后缀提取

### 2.1 代码位置（仅 `feature/intraday-bar-synthesis` 分支）

```python
# data_loader.py:259-265
tz_suffix = ""
if rows:
    first_ts = rows[0][0]
    if "+" in str(first_ts):
        tz_suffix = str(first_ts)[str(first_ts).index("+"):]
    elif str(first_ts).endswith("Z"):
        tz_suffix = "Z"
```

### 2.2 为什么需要提取时区后缀？

合成 bar 的时间戳（`next_end`）是用 `pd.Timestamp` 计算得出的，其 `strftime()` 输出是 **不带时区的** ISO 格式（如 `"2024-01-15T10:00:00"`）。

而数据库中已存储的 bar 的时间戳 **可能带时区后缀**。当合成 bar 被追加到已有数据中时，如果不补齐时区后缀，会导致：
- DataFrame 中 `Date` 列的数据类型不一致（部分带时区、部分不带）
- parquet 序列化/反序列化时可能引发时区推断错误
- 后续 `pd.to_datetime()` 处理时可能产生不一致的时区转换

### 2.3 `+` vs `Z` 两种格式的来源

| 格式 | 来源 | 示例 |
|------|------|------|
| `+HH:MM` | yfinance 通过不同数据源返回时，ISO 8601 偏移格式 | `"2024-01-15 14:30:00+08:00"` |
| `Z` | UTC 零时区标识 | `"2024-01-15T06:30:00Z"` |

这两种格式的差异取决于：
- yfinance 内部数据源的返回格式（由 yfinance 库根据数据来源自动决定）
- 股票所属市场（A 股经常出现 `+08:00`，美股可能出现 `Z`）

代码通过扫描第一条记录（最新一条 bar）的 ts 字符串决定时区后缀，并假设**同一个 ticker+tf 的所有记录使用统一的时区格式**。这是一个合理的简化假设。

### 2.4 tz_suffix 在后续合成 bar 中的用途

```python
# data_loader.py:299
synthesized_bar = {
    "Date": next_end.strftime("%Y-%m-%dT%H:%M:%S") + tz_suffix,
    ...
}
```

`tz_suffix` 被拼接到合成 bar 的 `Date` 字段末尾，确保其格式与数据库中的已有数据一致。这样整个 DataFrame 的 Date 列就具有统一的时区表示。

**注意**：在 `feature/backtest-bar-synthesis` 分支中，时区后缀逻辑被简化了（甚至可能不存在），它使用 `hasattr(next_end, 'strftime')` 来判断：

```python
# backtest-bar-synthesis 分支: data_loader.py:298
"Date": next_end.strftime("%Y-%m-%dT%H:%M:%S") if hasattr(next_end, 'strftime') else str(next_end),
```

这表示该分支不处理时区后缀，而是直接用 `strftime` 生成无时区的 ISO 字符串。

---

## 3. 分钟级 bar 合成算法

这是整个函数最核心的部分。下面逐层分析。

### 3.1 触发条件

```python
# data_loader.py:269-271 (backtest-bar-synthesis)
if min_tf and cutoff_date and ALL_TFS.index(tf) > ALL_TFS.index(min_tf):
```

`min_tf` 的定义来自 `_get_min_tf_and_count()`：

```python
# streamlit_app.py:427-463
def _get_min_tf_and_count(configs, ticker_code):
    # 遍历 configs 中各视图的 tf 字段，取 ALL_TFS 中索引最小的（最精细的）
    min_idx = len(ALL_TFS)
    for cfg in configs:
        tf = cfg.get("tf", "")
        try:
            idx = ALL_TFS.index(tf)
            if idx < min_idx:
                min_idx = idx
                min_tf = tf
        except ValueError:
            continue
    return min_tf, bar_count
```

其中 `ALL_TFS = ["1分钟","5分钟","15分钟","60分钟","日线","周线","月线","季线"]`（sidebar.py:13）。

**索引值**：

| TF | 索引 |
|----|------|
| 1分钟 | 0 |
| 5分钟 | 1 |
| 15分钟 | 2 |
| 60分钟 | 3 |
| 日线 | 4 |
| 周线 | 5 |
| 月线 | 6 |
| 季线 | 7 |

**触发条件逐项分析**：

| 条件 | 含义 | 反例 |
|------|------|------|
| `min_tf` 非空 | 回测模式已计算出最小周期 | 浏览模式直接跳过 |
| `cutoff_date` 非空 | 有截止日期 | 同上 |
| `ALL_TFS.index(tf) > ALL_TFS.index(min_tf)` | 当前 tf 比 min_tf 粗 | 如果 min_tf=5分钟，tf=1分钟 或 5分钟 时跳过 |
| `tf in ("5分钟", "15分钟", "60分钟")` | 仅对分钟级粗周期进行合成 | 日线及以上不做合成 |

**合成范围**：`min_tf=1分钟` 可以合成 5分钟/15分钟/60分钟。`min_tf=5分钟` 只能合成 15分钟/60分钟。`min_tf=60分钟` 只能合成 日线（但日线不在合成范围内，所以实际上不合成任何周期）。

**"为什么只对 5/15/60 分钟做合成？1分钟为什么不需要？"**

这个问题核心在于：1分钟是 `ALL_TFS` 中索引最小的 TF（index=0），所以 `ALL_TFS.index(1分钟) > ALL_TFS.index(min_tf)` 永远不会为真 — 不存在比1分钟更细的周期。因此 1分钟 bar 永远不会被合成。

但更重要的是 **1分钟 bar 不需要合成**：1分钟总是直接从数据库查询（DESC LIMIT n_pts），因为不存在更细粒度的数据源来"拼凑"不完整的 1分钟 bar。

### 3.2 `_get_period_end()` — 计算下一个周期边界

```python
# data_loader.py:142-175
def _get_period_end(ts, tf):
    """计算给定 ts 之后的下一个周期结束时间戳（仅分钟级TF）。"""
    ts = pd.Timestamp(ts)
    if tf == "1分钟":
        return ts + pd.Timedelta(minutes=1)
    elif tf == "5分钟":
        current_minutes = ts.hour * 60 + ts.minute
        next_boundary = ((current_minutes // 5) + 1) * 5
        next_hour = next_boundary // 60
        next_minute = next_boundary % 60
        if next_hour >= 24:
            return (ts + pd.Timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return ts.replace(hour=next_hour, minute=next_minute, second=0, microsecond=0)
    elif tf == "15分钟":
        # ... 同理，刻度为15分钟
    elif tf == "60分钟":
        return (ts + pd.Timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    else:
        return ts  # 非分钟TF返回原值
```

**各 TF 的计算逻辑**：

| TF | 算法 | 示例 |
|----|------|------|
| 1分钟 | ts + 1 分钟 | 09:30:45 -> 09:31:45 |
| 5分钟 | 计算下一个 5 分钟边界 | 09:32 -> 09:35；09:48 -> 09:50 |
| 15分钟 | 计算下一个 15 分钟边界 | 09:32 -> 09:45；09:48 -> 10:00 |
| 60分钟 | ts + 1 小时，分钟归零 | 09:32 -> 10:00 |
| 其他 | 返回原值（不处理） | — |

**跨天处理**：对于 5 分钟和 15 分钟 TF，如果 `next_hour >= 24`，则自动推到次日 00:00。这解决了交易日尾盘的边界问题。

**设计演进的考量**：`_get_period_end()` 的参数类型是 `ts`（字符串），但函数体将它转为 `pd.Timestamp`。这意味着即使入参是数据库中的 ISO 时间戳，也会被 panda 解析。这是**隐式类型转换**，如果入参是 `sqlite3.Row[0]` 的字符串格式，转换正常工作；如果入参已经是 `pd.Timestamp` 或 `datetime`，也能正常工作。

### 3.3 判断是否需要合成

```python
# data_loader.py:274 (backtest-bar-synthesis)
next_end = _get_period_end(last_completed_ts, tf)
cutoff_dt = pd.Timestamp(cutoff_date)
if cutoff_dt < next_end:
    # ... 需要合成
```

**`cutoff_dt < next_end` 在检查什么？**

这本质上是在检查：**截止日期是否落在了当前周期的非完整段内？**

举例说明：

假设：
- `tf` = "5分钟"，`last_completed_ts` = "2024-01-15 09:30:00"（最近一条已完成 5 分钟 bar 的结束时间）
- `_get_period_end("2024-01-15 09:30:00", "5分钟")` = "2024-01-15 09:35:00"（下一个 5 分钟周期结束时间）
- 如果 `cutoff_date` = "2024-01-15 09:32:00"

此时 `cutoff_dt (09:32) < next_end (09:35)` 为 **True**，说明 cutoff 落在当前周期中间。数据库中的最新 bar（last_completed_ts）只包含到 09:30 的数据，而 09:30-09:35 之间的数据还没有被写入 database（因为该 5 分钟周期尚未完整结束）。

这时需要从更细的周期（1分钟）中查询 09:30-09:32 之间的 1 分钟 bar，合成一条"不完整的 5 分钟 bar"。

相反，如果 `cutoff_date` = "2024-01-15 09:35:00" 或更晚，则 `cutoff_dt >= next_end`，说明截止日期已经到达或超过了当前周期的完整边界，数据库中的该周期数据已经是完整的，不需要合成。

### 3.4 `finer_tf` 选择

```python
# data_loader.py:278 (backtest-bar-synthesis)
finer_idx = ALL_TFS.index(tf) - 1
finer_tf = ALL_TFS[finer_idx]
```

**为什么只用紧邻的更细一级？**

| tf | ALL_TFS.index | finer_idx | finer_tf |
|----|---------------|-----------|----------|
| 5分钟 | 1 | 0 | 1分钟 |
| 15分钟 | 2 | 1 | 5分钟 |
| 60分钟 | 3 | 2 | 15分钟 |

设计理由：

1. **数据完整性**：紧邻的更细一级 TF 包含足够的粒度来构建粗周期 bar。例如，5 条 1 分钟 bar 可以精确合成 1 条 5 分钟 bar（O=第一条的 O, H=所有 H 的最大值, L=所有 L 的最小值, C=最后一条的 C, V=累计值）。

2. **查询效率**：只用紧邻一级的数据，查询量最小。如果用 1 分钟数据合成 60 分钟 bar，需要查询 60 条记录而非 4 条 15 分钟 bar。

3. **逐级合成是理论最优**：5分钟由 1 分钟合成，15 分钟由 5 分钟合成，60 分钟由 15 分钟合成。这保证了每一级合成都有完整的数据源。

**潜在问题**：如果 `finer_tf` 在数据库中也没有完整数据（例如，1分钟数据缺失导致 5 分钟合成失败），那么合成会跳过（`synthesized_bar = None`），使用降级策略。

### 3.5 `_get_period_start()` — 查询起点计算

```python
# data_loader.py:203-210
def _get_period_start(ts, tf):
    """返回不完整bar的数据查询起点（ISO日期字符串）。"""
    ts = pd.Timestamp(ts)
    if tf in ("1分钟", "5分钟", "15分钟", "60分钟"):
        start = ts + pd.Timedelta(minutes=1)
    else:
        start = ts + pd.Timedelta(days=1)
    return start.strftime("%Y-%m-%d")
```

**语义**：给定最后一条已完成 bar 的 end_ts，返回"从什么时间点开始查询更细粒度的数据"。

对于分钟级 TF，起点 = `last_completed_ts + 1 分钟`。这是因为数据库中的 `last_completed_ts` 已经是该 bar 的结束时间戳，所以下一条更细粒度的数据应该从 1 分钟后开始。

对于日线及以上 TF，起点 = `ts + 1 天`。

**注意**：`backtest-bar-synthesis` 分支对此有一个补丁：

```python
# data_loader.py:282-286
if period_start > cutoff_date:
    period_start = pd.Timestamp(last_completed_ts).strftime("%Y-%m-%d")
```

这是当 `_get_period_start` 返回的起点超过了 `cutoff_date` 时的保护逻辑。这种情况发生在日线及以上的 TF 上：当天日期已经是最新的记录，`_get_period_start` 会返回"明天"，但 cutoff 还在今天。此时将 `period_start` 回退到 `last_completed_ts` 所在日期，确保能查询到今天内的数据。

**不过这里可能存在一个逻辑矛盾**：日线及以上 TF 不在合成范围内（`tf in ("5分钟", "15分钟", "60分钟")` 限定了只有分钟级 TF 才触发合成），所以 `_get_period_start` 的 `start = ts + pd.Timedelta(days=1)` 分支实际上在合成路径中不可达。这个补丁更像是防御性编程。

### 3.6 聚合逻辑

```python
# data_loader.py:288-302
min_rows = inner_conn.execute(
    """SELECT open, high, low, close, volume
       FROM kline
       WHERE ticker=? AND timeframe=? AND ts >= ? AND ts <= ?
       ORDER BY ts ASC""",
    (ticker_code, min_tf, period_start, cutoff_date),
).fetchall()

if min_rows:
    opens = [r[0] for r in min_rows]
    highs = [r[1] for r in min_rows]
    lows  = [r[2] for r in min_rows]
    closes = [r[3] for r in min_rows]
    volumes = [r[4] for r in min_rows]
    synthesized_bar = {
        "Date": next_end.strftime("%Y-%m-%dT%H:%M:%S") + tz_suffix,
        "Open": float(opens[0]),
        "High": float(max(highs)),
        "Low": float(min(lows)),
        "Close": float(closes[-1]),
        "Volume": float(sum(volumes)),
    }
```

**聚合规则**：

| 字段 | 公式 | 说明 |
|------|------|------|
| Date | `next_end.strftime(...)` | 当前周期（粗周期）的下一个边界时间 |
| Open | `opens[0]` | 更细一级数据的第一条开盘价 |
| High | `max(highs)` | 所有高价的**最大值** |
| Low | `min(lows)` | 所有低价的**最小值** |
| Close | `closes[-1]` | 更细一级数据的最后一条收盘价 |
| Volume | `sum(volumes)` | 所有成交量的**累加值** |

这是**标准的 OHLC 聚合规则**，与交易所合成 bar 的算法一致。

**示例**：用 1 分钟 bar 合成 5 分钟不完整 bar

| 时间 | Open | High | Low | Close | Volume |
|------|------|------|-----|-------|--------|
| 09:31 | 100.0 | 100.5 | 99.8 | 100.2 | 1000 |
| 09:32 | 100.2 | 100.8 | 100.1 | 100.7 | 800 |
| (09:33-09:35 尚未到) | | | | | |

合成结果：
```
O=100.0, H=100.8, L=99.8, C=100.7, V=1800
Date="2024-01-15T09:35:00"   ← 5分钟周期结束时间
```

### 3.7 synthesized_bar 字典结构

```python
synthesized_bar = {
    "Date": str,    # ISO 格式时间戳，如 "2024-01-15T09:35:00+08:00"
    "Open": float,  # 开盘价
    "High": float,  # 最高价
    "Low": float,   # 最低价
    "Close": float, # 收盘价
    "Volume": float # 成交量
}
```

每个字段的格式对应 parquet 中该列的 dtypes：
- `Date`：字符串（object 类型），后续在 `_load_chart_data()` 中用 `pd.to_datetime()` 转换
- `Open/High/Low/Close/Volume`：float64

### 3.8 错误处理与降级策略

```python
# data_loader.py:304-307
except Exception as e:
    logger.debug(f"Synthesis failed for {ticker_code}/{tf}: {e}")
    synthesized_bar = None
```

**try/except 的范围**：覆盖从 `last_completed_ts = rows[0][0]` 到合成完成的整个逻辑块。

**可能的失败原因**：
1. `rows[0][0]` 取出的是不可解析为时间戳的字符串 → `pd.Timestamp()` 抛出异常
2. `_get_period_end()` 计算边界时遇到非预期格式 → 同上
3. `min_rows = inner_conn.execute(...)` 查询失败（连接断开等）→ SQLite 异常
4. `opens[0]` 等字段包含 NULL → `float(NULL)` 在 Python 中可行但在某些情况下异常

**降级策略**：任何异常发生时，`synthesized_bar = None`。后续代码在构建 DataFrame 时检查 `synthesized_bar is not None`，如果为 None 则走"无合成 bar"的常规路径。

**日志级别**：使用 `logger.debug`，说明这被视为正常降级而非错误。生产环境中 debug 日志默认关闭，因此异常不会产生噪音。

### 3.9 总结：合成触发路径图

```
tf 是 5/15/60 分钟之一？           ──否──→ 不合成
   │
   ├─是
   │
   min_tf 比 tf 更精细（索引更小）？  ──否──→ 不合成
   │                                      （同级别或更粗
   ├─是                                    不需要合成）
   │
   cutoff_dt 在周期边界之内？
   （即当前 tf 的最新 bar 之后
   还有未关闭的周期）                    ──否──→ 不合成
   │                                           （数据已完成
   ├─是                                            不需要合成）
   │
   从 finer_tf 查询成功？             ──否──→ synthesized_bar=None → 常规路径
   │
   └─是 → 合成一条 OHLCV bar

最终: 合成bar被追加到结果列表末尾
```

---

## 4. DataFrame 构建与截断

### 4.1 两个分支

#### 分支 1：有合成 bar

```python
# data_loader.py:315-323 (backtest-bar-synthesis)
if synthesized_bar is not None:
    data = []
    for r in reversed(rows):
        data.append({
            "Date": r[0], "Open": r[1], "High": r[2],
            "Low": r[3], "Close": r[4], "Volume": r[5],
        })
    data.append(synthesized_bar)
    if len(data) > n_pts:
        data = data[-n_pts:]
    df = pd.DataFrame(data)
```

**`reversed(rows)`**：`rows` 是 `SELECT ... ORDER BY ts DESC LIMIT n_pts` 的结果，所以顺序是最新→最旧。`reversed()` 将其反转为**升序**（最旧→最新）。

**数据格式**：由于需要追加 `synthesized_bar`（它是一个 dict），这里的构建方式不是直接使用 `pd.DataFrame(rows_list, columns=...)`，而是通过逐行构建 dict 列表。这使得两种数据结构（SQLite Row 和 dict）可以共存。

**`reversed(rows)` 的数据类型**：每个 `r` 是 `sqlite3.Row`，可以通过 `r[0]` 按下标访问。但不支持 `r["Date"]` 这种列名访问（虽然 `sqlite3.Row` 支持 `r["ts"]`）。所以用 `r[0]` 等下标。

**截断**：`data[-n_pts:]` — 如果合成后数据超过 `n_pts` 条，从末尾取 `n_pts` 条（即丢弃最旧的数据）。这确保输出总是 `<= n_pts`。

#### 分支 2：无合成 bar

```python
# data_loader.py:325-328 (backtest-bar-synthesis)
rows_list = list(reversed(rows))
if len(rows_list) > n_pts:
    rows_list = rows_list[-n_pts:]
df = pd.DataFrame(rows_list, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
```

**`list(reversed(rows))`**：将 `sqlite3.Row` 对象的反转迭代器物化为列表。`sqlite3.Row` 的迭代按列顺序 yield 值，因此 `list(reversed(rows))` 中的每个元素是 `(ts, open, high, low, close, volume)` 的元组。

**注意**：`list(reversed(rows))` 与上面的 `data.append(...)` 分支的行为一致——都是先将数据反转为升序。但在无合成 bar 的情况下，`rows_list` 中的每个元素已经是 tuple（`sqlite3.Row` 可迭代产生 tuple-like 行为）。

**columns 列名映射**：

```python
df = pd.DataFrame(rows_list, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
```

`SELECT` 子句 `SELECT ts, open, high, low, close, volume` 中的列名被映射为：

| SQL 列 | DataFrame 列 |
|--------|--------------|
| `ts` | `Date` |
| `open` | `Open` |
| `high` | `High` |
| `low` | `Low` |
| `close` | `Close` |
| `volume` | `Volume` |

其中 `ts` -> `Date` 的命名变化是核心映射。

### 4.2 DataFrame 的最终状态

无论走哪个分支，最终 `df` 的状态：

```
df.columns = ["Date", "Open", "High", "Low", "Close", "Volume"]
df.dtypes:
  Date     object    (字符串或 Timestamp)
  Open     float64
  High     float64
  Low      float64
  Close    float64
  Volume   float64
len(df) <= n_pts     (截断保证不超过 n_pts)
df 的时间顺序         (升序：最旧→最新)
```

---

## 5. Parquet 写入

### 5.1 写入路径

```python
# data_loader.py:330-332 (backtest-bar-synthesis)
display_dir = Path(__file__).parent.parent.parent / "data" / "display"
display_dir.mkdir(parents=True, exist_ok=True)
df.to_parquet(display_dir / f"{tf}.parquet", index=False)
```

**路径解析**：

```
Path(__file__)                           = filter_app/services/data_loader.py
.parent                                  = filter_app/services/
.parent.parent                           = filter_app/
.parent.parent.parent                    = <project_root>/
最终路径: <project_root>/data/display/{tf}.parquet
```

例如：`/Users/xfpan/claude/filter_research/data/display/日线.parquet`

### 5.2 `index=False` 的原因

`df.index` 是默认的 `RangeIndex`（0, 1, 2, ...），没有任何业务意义。不写入 index 可以：
1. **节省磁盘空间**：避免存储一个无意义的整数列
2. **防止读取时的歧义**：如果写入 index，读取时 `pd.read_parquet()` 会默认使用该列作为 index。如果 index 列被命名为未预期的名称（如 `__index_level_0__`），会导致后续 `df["Date"]` 行为不符合预期。

### 5.3 浏览模式和回测模式写入的是同一个文件

```python
# data_loader.py:335-343 (回测路径写入 display/{tf}.parquet)
df.to_parquet(display_dir / f"{tf}.parquet", index=False)

# data_loader.py:365-368 (浏览路径写入 display/{tf}.parquet)
df.to_parquet(display_dir / f"{tf}.parquet", index=False)
```

**影响分析**：

| 方面 | 说明 |
|------|------|
| **共享文件** | 是的，浏览和回测模式写入同一个 `data/display/{tf}.parquet` 文件 |
| **覆盖写入** | 每次 `_sync_to_display()` 被调用，都会覆盖该文件。不存在并发写入问题（Streamlit 单线程） |
| **浏览模式的潜在影响** | 如果用户先浏览日线（parquet 包含最新120条），然后切换到回测模式并拖动 slider，parquet 被截断为历史数据。再切回浏览模式时，parquet 内容可能不是最新的。但浏览模式的 `_load_chart_data()` 在读取 parquet 后会检查数据量，如果需要会通过 `_cached_fetch_stock` 回退到 API |
| **回测模式的依赖** | 回测模式依赖 parquet 作为数据源，因为 `_cached_fetch_stock` 走的是 API 回退路径（streamlit_app.py:126），数据量可能与数据库不一致 |

**关键代码**：在 `_load_chart_data()` 中，**回测分支也尝试读取 parquet**（streamlit_app.py:141-165），因此回测模式实际上依赖 parquet 文件。如果 parquet 写入成功（`ok=True`），则走 parquet 读取路径；如果写入失败（`ok=False`），则走 API 回退路径。

**浏览模式**：浏览模式也用相同的路径读取 parquet。所以如果先运行回测，再切换到浏览模式（并且没有重新加载数据），浏览模式看到的是回测模式最后一次写入的 parquet 文件内容。

**实际影响**：由于两个模式共享同一个 parquet 文件，**浏览模式和回测模式之间切换时，如果用户不手动刷新数据，会看到之前模式最后写入的数据**。这是设计决定的（共享缓存可以减少 API 调用），但在模式切换时需要清除或刷新。

**`clear_display_cache()` 函数**（db.py:348-356）提供了清除所有 parquet 文件的能力。这在回测退出时或数据刷新时被调用：

```python
def clear_display_cache():
    """Delete all .parquet files in data/display/."""
    display_dir = DB_PATH.parent / "display"
    if display_dir.exists():
        for f in display_dir.glob("*.parquet"):
            try:
                f.unlink()
            except OSError:
                pass
```

---

## 6. _load_chart_data() 的 parquet 消费

### 6.1 函数签名与调用

```python
# streamlit_app.py:115
def _load_chart_data(market, ticker_code, tf, day_offset, n_pts,
                     window_start=None, cutoff_date=None) -> tuple:
```

**返回**：`(t, noisy, ohlc, ticker_full, dates, err)`

### 6.2 回测分支（streamlit_app.py:121-134）

```python
# streamlit_app.py:121-134
if window_start is not None:
    # 回测模式：按 cutoff_date 日期对齐
    ok, count = _sync_to_display(ticker_code, tf, n_pts=n_pts, cutoff_date=cutoff_date)
    if not ok:
        # parquet 写入失败，直接走 API 回退
        t, noisy, ohlc, ticker_full, dates, err = _cached_fetch_stock(market, ticker_code, tf, n_pts)
        # API 回退路径：截断到 n_pts
        if err is None and dates is not None and len(dates) > n_pts:
            t = t[-n_pts:]
            noisy = noisy[-n_pts:]
            if hasattr(ohlc, 'iloc'):
                ohlc = ohlc.iloc[-n_pts:]
            dates = dates[-n_pts:]
        return t, noisy, ohlc, ticker_full, dates, err
```

**逻辑**：
1. 先调用 `_sync_to_display()` 将数据写入 parquet
2. 如果 parquet 写入成功（`ok=True`），**不直接返回**，而是**继续执行到下面的 parquet 读取代码**
3. 如果 parquet 写入失败（`ok=False`），走 API 回退路径，截断到 `n_pts`，然后 `return`

**注意**：回退路径的截断使用 `[-n_pts:]`，但 `_cached_fetch_stock` 请求的是 `n_pts` 条数据，截断通常是 no-op（除非 API 返回了多于请求的数据）。这主要是防御性编程。

**`hasattr(ohlc, 'iloc')`**：检查 `ohlc` 是否是 pandas DataFrame（`iloc` 是 DataFrame 的属性），以确保 `.iloc[-n_pts:]` 可以正常工作。

### 6.3 浏览分支（streamlit_app.py:135-139）

```python
# streamlit_app.py:135-139
else:
    # 浏览模式：取最新 n_pts 条
    ok, count = _sync_to_display(ticker_code, tf, day_offset=day_offset, n_pts=n_pts)
    if not ok:
        # parquet 写入失败，直接走 API 回退
        return _cached_fetch_stock(market, ticker_code, tf, n_pts)
```

与回测分支的区别：
- 不回退到 API 时的截断（因为 parquet 已包含正确的 n_pts 条）
- 直接返回 `_cached_fetch_stock()` 的结果，不再执行 parquet 读取

### 6.4 Parquet 读取（streamlit_app.py:141-172）

```python
# streamlit_app.py:141-172
display_path = Path(__file__).parent.parent / "data" / "display" / f"{tf}.parquet"
err = None
if display_path.exists():
    try:
        df = pd.read_parquet(display_path)
        if "Date" in df.columns and "Close" in df.columns and len(df) >= 2:
            df["Date"] = pd.to_datetime(df["Date"])
            df = df.set_index("Date").sort_index()

            # ★ 不再需要截断！parquet 已经是 n_pts 条
            if len(df) < 2:
                err = f"{tf} 数据点不足 ({len(df)})"
                return None, None, None, None, None, err

            t = np.arange(len(df), dtype=float)
            noisy = df["Close"].values.ravel()
            ohlc = df[["Open", "High", "Low", "Close"]] if all(c in df.columns for c in ["Open", "High", "Low"]) else pd.DataFrame(...)

            if window_start is not None:
                try:
                    log_data_load(ticker_code, tf, len(df), cutoff_date or "", elapsed_ms=0)
                except Exception as e:
                    logger.debug(f"回测日志写入失败: {e}")

            return t, noisy, ohlc, ticker_code, df.index, None
        else:
            err = "数据不足"
    except Exception as e:
        err = str(e)
if err is not None:
    return None, None, None, None, None, err
return _cached_fetch_stock(market, ticker_code, tf, n_pts)
```

**逐行分析**：

```python
display_path = Path(__file__).parent.parent / "data" / "display" / f"{tf}.parquet"
```
- `Path(__file__)` = `filter_app/streamlit_app.py`
- `.parent` = `filter_app/`
- `.parent.parent` = `<project_root>/`
- 与 `_sync_to_display()` 中的 `Path(__file__).parent.parent.parent / "data" / ...` 指向的是**同一个目录**

```python
df = pd.read_parquet(display_path)
```
**数据类型**：
- `Date`：object（parquet 中存储为字符串）
- `Open/High/Low/Close/Volume`：float64

```python
df["Date"] = pd.to_datetime(df["Date"])
```
- 将 `Date` 列从 `object`（字符串）转换为 `datetime64[ns]`。如果没有 this 转换，后续日期标记（`_date_markers()`）中的 `hasattr(d, 'date')` 会失败。

```python
df = df.set_index("Date").sort_index()
```
- **为什么要设置 index？** 为了后续可通过 `df.index` 访问 DateTimeIndex（传递给 `_date_markers` 等函数）。同时也使得 `ohlc = df[["Open", "High", "Low", "Close"]]` 的 index 是 DateTimeIndex。
- **`sort_index()`**：虽然 parquet 存储时已经是升序（`_sync_to_display()` 中的 `reversed(rows)` 保证了），但作为防御性编程进行排序。

```python
t = np.arange(len(df), dtype=float)
```
- **为什么用整数索引而非时间戳？**
  1. **滤波算法要求**：`_compute_filters()` 中的滤波函数（如 savgol）需要均匀采样的 x 坐标。时间戳分布不均匀（周末、假日缺失），会导致滤波计算结果失真。
  2. **绘图需求**：Plotly 的 candlestick 图表使用整数索引可以获得均匀的 x 轴间距，避免非交易时段出现空白。
  3. **Schmitt 触发器的相容性**：梯度计算 `np.gradient(filtered, t)` 需要均匀的 t。如果 t 是时间戳，梯度计算会包含时间间隔信息，导致不同周期的梯度值不可比。
  4. **回溯一致性**：回测中不同的 cutoff_date 会产生不同长度的 t（从 `np.arange(len(df))`），但索引总是从 0 开始的连续整数。这使得算法行为在不同时间窗口之间保持一致。

```python
noisy = df["Close"].values.ravel()
```
- `.values` 返回 NumPy 数组（`ndarray`）
- `.ravel()` 确保是一维扁平数组
- `noisy` 的 dtype 为 `float64`

```python
ohlc = df[["Open", "High", "Low", "Close"]]
```
- `ohlc` 是一个 `pd.DataFrame`，包含 4 列，index 是 `DatetimeIndex`

```python
if window_start is not None:
    log_data_load(ticker_code, tf, len(df), cutoff_date or "", elapsed_ms=0)
```
- 回测模式下写入日志：记录此次数据加载的 ticker、tf、点数、cutoff_date

**返回值**：
```python
return t, noisy, ohlc, ticker_code, df.index, None
```
- `t`: `ndarray(float64)` — 整数索引 [0, 1, 2, ..., n-1]
- `noisy`: `ndarray(float64)` — 收盘价序列
- `ohlc`: `pd.DataFrame` — OHLC 四列数据
- `ticker_code`: `str` — 股票代码
- `df.index`: `DatetimeIndex` — 日期索引（用于显示）
- `err`: `None` — 表示成功

### 6.5 返回值的消费路径

```python
# streamlit_app.py:526
t, noisy, ohlc, ticker_full, dates, err = _load_chart_data(...)
```

下游消费路径：

| 返回值 | 消费位置 | 用途 |
|--------|----------|------|
| `t`（ndarray） | `_compute_filters()` [line 552] | 滤波算法的 x 轴输入 |
| `t`（ndarray） | `_add_main_price_traces()` [line 314-318] | Plotly 图表的 x 轴 (candlestick + scatter) |
| `t`（ndarray） | `_add_schmitt_traces()` [line 349-381] | Schmitt 信号绘图 |
| `t`（ndarray） | `_add_pnl_traces()` [line 386-424] | PnL 绘图 |
| `noisy`（ndarray） | `_compute_filters()` [line 184] | 滤波函数的 y 轴输入（原始收盘价） |
| `noisy`（ndarray） | `_add_main_price_traces()` [line 319] | 收盘价曲线 |
| `ohlc`（DataFrame） | `_add_main_price_traces()` [line 314-316] | K 线图 |
| `ticker_full`（str） | captions [line 557] | 显示股票名称·周期 |
| `dates`（DatetimeIndex） | `_date_markers()` [line 540] | 日期标记计算 |
| `dates`（DatetimeIndex） | `_render_plotly()` [line 660] | HTML 交互式日期提示 |
| `err`（str/None） | error display [line 528-531] | 错误提示 |

---

## 7. 数据流 ASCII 图

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. 触发路径（cutoff_date 溯源）                                  │
│                                                                  │
│ streamlit_app.py                                                 │
│   main() [line 1536]                                             │
│     │                                                            │
│     ├─ _run_backtest_play() [line 1537]                          │
│     │     └─ bar_index + 1                                       │
│     │        └─ _get_bar_date_from_db(ticker, min_tf, bar_index) │
│     │             → cutoff_date (str: "2024-01-15")              │
│     │                                                            │
│     └─ Pass 2: 渲染图表 [line 1611-1631]                         │
│           │                                                      │
│           ├─ window_start = _bar_index  (int)                    │
│           └─ cutoff_date = "_bt_cutoff_date" (str/None)          │
│                │                                                 │
│                ▼                                                 │
│           _render_chart_fragment() [line 502]                    │
│                │                                                 │
│                ▼                                                 │
│           _render_chart() [line 509]                             │
│                │                                                 │
│                ▼                                                 │
│           _load_chart_data(market, ticker_code, tf,              │
│               day_offset, n_pts,                                 │
│               window_start=window_start,                         │
│               cutoff_date=cutoff_date)  [line 526]               │
└─────────────────────────────────────────────────────────────────┘

                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. _load_chart_data() — 回测分支 [streamlit_app.py:121-172]     │
│                                                                  │
│   window_start is not None?                                      │
│      │                                                          │
│      ├─ YES (回测)                                              │
│      │    │                                                      │
│      │    ▼                                                      │
│      │   _sync_to_display(ticker_code, tf,                      │
│      │       n_pts=n_pts, cutoff_date=cutoff_date)              │
│      │    │                                                      │
│      │    ├─ ok=True, count=N  → 继续到 parquet 读取            │
│      │    │                                                     │
│      │    └─ ok=False → API 回退 + n_pts截断 → return           │
│      │                                                          │
│      └─ NO (浏览)                                               │
│           │                                                     │
│           ▼                                                     │
│          _sync_to_display(ticker_code, tf,                      │
│              day_offset=day_offset, n_pts=n_pts)                │
│           │                                                     │
│           ├─ ok=True, count=N  → 继续到 parquet 读取            │
│           │                                                     │
│           └─ ok=False → API 回退 return                         │
│                                                                  │
│   ↓ (ok=True 时继续执行)                                         │
│                                                                  │
│   df = pd.read_parquet(data/display/{tf}.parquet)               │
│   df["Date"] = pd.to_datetime(df["Date"])                       │
│   df = df.set_index("Date").sort_index()                        │
│   t = np.arange(len(df), dtype=float)                           │
│   noisy = df["Close"].values.ravel()                            │
│   ohlc = df[["Open","High","Low","Close"]]                     │
│   return t, noisy, ohlc, ticker_code, df.index, None            │
└─────────────────────────────────────────────────────────────────┘

                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. _sync_to_display() — 回测/浏览 [data_loader.py:245-332]     │
│                                                                  │
│   输入:                                                          │
│     ticker_code: str (如 "AAPL")                                 │
│     tf: str (如 "5分钟")                                         │
│     n_pts: int (default 120)                                    │
│     cutoff_date: str or None                                     │
│     min_tf: str or None                                          │
│                                                                  │
│   cutoff_date is not None?                                       │
│      │                                                           │
│      ├─ YES (回测) ——— 步骤 4 进入                             │
│      │                                                           │
│      └─ NO (浏览)                                                │
│           │                                                      │
│           ▼                                                      │
│           df = query_kline(ticker_code, tf, n_pts, day_offset)   │
│           df["Date"] = pd.to_datetime(df["Date"])                │
│           df.to_parquet(data/display/{tf}.parquet, index=False)  │
│           return True, len(df)                                   │
└─────────────────────────────────────────────────────────────────┘

                         │
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 4. 回测模式 — SQL 查询 [data_loader.py:251-257]                     │
│                                                                      │
│   conn.execute("""                                                   │
│     SELECT ts, open, high, low, close, volume                        │
│     FROM kline                                                       │
│     WHERE ticker=? AND timeframe=? AND ts <= ?                       │
│     ORDER BY ts DESC                                                 │
│     LIMIT ?                                                          │
│   """, (ticker_code, tf, cutoff_date, n_pts)).fetchall()             │
│                                                                      │
│   输入: SQLite DB (market.db)                                        │
│   输出: list[sqlite3.Row]  — 每行 = (ts, open, high, low, close, vol)│
│   排序: DESC (最新→最旧)                                              │
│   长度: ≤ n_pts                                                      │
│                                                                      │
│   if not rows: return False, 0                                       │
└──────────────────────────────────────────────────────────────────────┘

                         │
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 5. Bar 合成（仅回测·特定分支）[data_loader.py:259-307]              │
│                                                                      │
│   tz_suffix = 从 first_ts 提取时区后缀                               │
│   (rows[0][0] 中的 "+HH:MM" 或 "Z")                                  │
│                                                                      │
│   min_tf and tf in ("5分钟","15分钟","60分钟")                        │
│   and ALL_TFS.index(tf) > ALL_TFS.index(min_tf)?                     │
│      │                                                                │
│      ├─ YES                                                          │
│      │   │                                                           │
│      │   ▼                                                           │
│      │   last_completed_ts = rows[0][0]  (最新bar时间戳)             │
│      │   next_end = _get_period_end(last_completed_ts, tf)           │
│      │   cutoff_dt < next_end?                                       │
│      │      │                                                        │
│      │      ├─ YES → 需要合成                                        │
│      │      │   │                                                     │
│      │      │   ▼                                                     │
│      │      │   finer_tf = ALL_TFS[ALL_TFS.index(tf)-1]              │
│      │      │   (5分钟→1分钟, 15分钟→5分钟, 60分钟→15分钟)            │
│      │      │   period_start = _get_period_start(last_completed_ts)  │
│      │      │                                                        │
│      │      │   查询 finer_tf 数据:                                   │
│      │      │   conn.execute("""                                     │
│      │      │     SELECT open, high, low, close, volume              │
│      │      │     FROM kline                                         │
│      │      │     WHERE ticker=? AND timeframe=?                      │
│      │      │       AND ts >= ? AND ts <= ?                          │
│      │      │     ORDER BY ts ASC                                    │
│      │      │   """, (ticker_code, finer_tf, period_start, cutoff))  │
│      │      │                                                        │
│      │      │   if min_rows:                                         │
│      │      │      synthesized_bar = {                               │
│      │      │        "Date": next_end.strftime + tz_suffix,          │
│      │      │        "Open": opens[0],                               │
│      │      │        "High": max(highs),                             │
│      │      │        "Low": min(lows),                               │
│      │      │        "Close": closes[-1],                            │
│      │      │        "Volume": sum(volumes),                         │
│      │      │      }                                                 │
│      │      │                                                        │
│      │      └─ NO → synthesized_bar = None                          │
│      │                                                                │
│      └─ NO → synthesized_bar = None                                 │
│                                                                       │
│   try/except: 任何异常 → synthesized_bar = None                     │
└──────────────────────────────────────────────────────────────────────┘

                         │
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 6. DataFrame 构建 [data_loader.py:315-328]                          │
│                                                                      │
│   synthesized_bar is not None?                                       │
│      │                                                               │
│      ├─ YES                                                         │
│      │   data = []                                                   │
│      │   for r in reversed(rows):       ← DESC rows → ASC 顺序      │
│      │       data.append({"Date":r[0], "Open":r[1], ...})           │
│      │   data.append(synthesized_bar)   ← 追加合成 bar 到末尾       │
│      │   if len(data) > n_pts:                                       │
│      │       data = data[-n_pts:]       ← 截断到 n_pts (丢弃旧的)   │
│      │   df = pd.DataFrame(data)                                      │
│      │                                                               │
│      │   数据流:                                                     │
│      │     Tuple → Dict[] → pd.DataFrame                            │
│      │     [最新, ..., 最旧] → 反转 → [最旧, ..., 最新] + [合成bar]  │
│      │                                                               │
│      └─ NO                                                          │
│          rows_list = list(reversed(rows))   ← DESC → ASC             │
│          if len(rows_list) > n_pts:                                  │
│              rows_list = rows_list[-n_pts:]  ← 截断                  │
│          df = pd.DataFrame(rows_list,                                │
│              columns=["Date","Open","High","Low","Close","Volume"])  │
│                                                                      │
│          数据流:                                                     │
│             sqlite3.Row → tuple (反转) → pd.DataFrame               │
│                                                                      │
│   df 最终状态:                                                       │
│     Date: object (字符串，含 tz_suffix)                              │
│     Open/High/Low/Close/Volume: float64                              │
│     行数: min(n_pts, 实际数据行数)                                    │
│     排序: ASC (最旧→最新)                                             │
└──────────────────────────────────────────────────────────────────────┘

                         │
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 7. Parquet 写入 [data_loader.py:330-332]                            │
│                                                                      │
│   路径: <project_root>/data/display/{tf}.parquet                    │
│   选项: index=False                                                  │
│   模式: 每次覆盖写入 (单线程无竞争)                                   │
│                                                                      │
│   格式转换:                                                          │
│     pd.DataFrame → Apache Parquet (压缩, 列式存储)                   │
│                                                                      │
│   return True, len(df)                                               │
└──────────────────────────────────────────────────────────────────────┘

                         │
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 8. Parquet 消费 [streamlit_app.py:141-165]                          │
│                                                                      │
│   pd.read_parquet() → DataFrame                                      │
│       │                                                              │
│       ▼                                                              │
│   df["Date"] = pd.to_datetime(df["Date"])                           │
│       │  object → datetime64[ns]                                     │
│       ▼                                                              │
│   df = df.set_index("Date").sort_index()                             │
│       │  → DatetimeIndex 作为 index                                  │
│       ▼                                                              │
│   t = np.arange(len(df), dtype=float)                                │
│       │  → ndarray([0., 1., 2., ..., n-1.])                         │
│       ▼                                                              │
│   noisy = df["Close"].values.ravel()                                 │
│       │  → ndarray(float64)                                          │
│       ▼                                                              │
│   ohlc = df[["Open","High","Low","Close"]]                          │
│       │  → DataFrame with DatetimeIndex, 4 cols float64              │
│       ▼                                                              │
│   dates = df.index                                                    │
│       │  → DatetimeIndex                                              │
│       ▼                                                              │
│   return (t, noisy, ohlc, ticker_code, df.index, None)               │
└──────────────────────────────────────────────────────────────────────┘

                         │
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 9. 消费者 — _render_chart() [streamlit_app.py:526-660]             │
│                                                                      │
│   t (ndarray float64) ──┬──→ _compute_filters() — 滤波算法 x轴      │
│                         ├──→ _add_main_price_traces() — 图表 x轴    │
│                         ├──→ _compute_schmitt_trigger()             │
│                         ├──→ _add_schmitt_traces()                  │
│                         └──→ _add_pnl_traces()                      │
│                                                                      │
│   noisy (ndarray) ──────┬──→ _compute_filters() — 滤波算法 y轴      │
│                         └──→ _add_main_price_traces() — 收盘价曲线  │
│                                                                      │
│   ohlc (DataFrame) ────────→ _add_main_price_traces() — K线图       │
│                                                                      │
│   dates (DatetimeIndex) ─┬──→ _date_markers() — 日期刻度            │
│                          └──→ _render_plotly() — 交互日期提示        │
│                                                                      │
│   ticker_full (str) ────────→ st.caption() — 标题显示               │
└──────────────────────────────────────────────────────────────────────┘
```

### 完整格式转换链

```
SQLite Row → Tuple → Dict[] → pd.DataFrame → Parquet → pd.DataFrame → ndarray/DataFrame/Index

格式转换点:
  1. conn.execute().fetchall()     → list[sqlite3.Row]
  2. reversed(rows)                → 可迭代的 Row (DESC→ASC)
  3. list(reversed(rows))          → list[tuple]  (Row 迭代产生 tuple-like)
  4. pd.DataFrame(..., columns=...) → pd.DataFrame (结构化数据)
  5. df.to_parquet()               → Apache Parquet 二进制文件
  6. pd.read_parquet()             → pd.DataFrame (反序列化)
  7. df.set_index("Date")          → pd.DataFrame (DatetimeIndex)
  8. df["Close"].values.ravel()    → ndarray (NumPy 数组)
  9. df[["Open","High","Low","Close"]] → DataFrame (子集)
 10. df.index                      → DatetimeIndex
 11. np.arange(len(df))            → ndarray (整数索引)
```

---

## 8. 边界条件与缺陷分析

### 8.1 已知缺陷

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| 1 | `min_tf` 未传递给 `_sync_to_display()` | `feat/backtest-data-analysis` 分支的 `streamlit_app.py:123` | Bar 合成逻辑无法触发。`_sync_to_display()` 中的合成条件 `if min_tf and ...` 永远为假，因为 `min_tf` 参数默认为 `None`。 |
| 2 | `_get_period_start()` 的日线分支在合成路径中不可达 | data_loader.py:207-209 | 防御性代码但实际不会被执行，因为 `tf in ("5分钟","15分钟","60分钟")` 限定了只有分钟级 TF 触发合成 |
| 3 | `hasattr(next_end, 'strftime')` 的保护判断 | backtest-bar-synthesis:data_loader.py:298 | 如果 `next_end` 不是 `pd.Timestamp`（而是字符串 `str`），则 `strftime` 不可用，回退到 `str(next_end)`。但 `next_end` 在分钟级 TF 中始终是 `pd.Timestamp`（因为 `_get_period_end` 在非分钟级分支返回 `ts` 本身，可能是字符串），所以这个分支仅在非分钟级 TF 的异常路径中触发。 |

### 8.2 边界条件检查

| 场景 | 行为 | 正确性 |
|------|------|--------|
| `cutoff_date` 在数据范围最末尾（`bar_index = total_bars`） | `cutoff_date` 是最新 bar 的前一条，窗口包含完整的最后 n_pts 条 | 正确 |
| `cutoff_date` 在数据范围最开头 | `ORDER BY ts DESC LIMIT n_pts` 可能取到不足 n_pts 条 | 正常降级（不足5条时返回 False） |
| `n_pts` 大于数据库中的实际行数 | 只返回实际存在的行数（DESC LIMIT 取全量） | 正确 |
| 合成时 `finer_tf` 查询不到数据（`min_rows` 为空） | `synthesized_bar = None`，走无合成路径 | 正确（降级） |
| 合成时 `period_start > cutoff_date` | `backtest-bar-synthesis` 分支有保护逻辑：回退到 `last_completed_ts`所在日期 | 正确（但有缺陷见上） |
| 多个视图使用不同的 TF | 每个视图独立调用 `_sync_to_display()`，独立 parquet 文件 | 正确（共享 display 目录但文件名不同） |
| 从回测切换回浏览后 parquet 内容过期 | 浏览模式读取过期的 parquet 文件 | 潜在问题：需手动清除缓存或 `clear_display_cache()` |

### 8.3 性能考量

| 操作 | 时间复杂度 | 数据量 |
|------|-----------|--------|
| `ORDER BY ts DESC LIMIT n_pts` | O(log N) 利用索引（B-tree 降序遍历） | N=总行数（~数百万） |
| `reversed(rows)` | O(n_pts) | n_pts ≤ 300 |
| DataFrame 构建 | O(n_pts) | n_pts ≤ 300 |
| Parquet 写入 | O(n_pts) + 压缩开销 | n_pts ≤ 300 |
| Parquet 读取 | O(n_pts) | n_pts ≤ 300 |

整体性能依赖 SQLite 索引 `idx_kline_lookup(ticker, timeframe, ts)` 的效率。`DESC LIMIT n_pts` 是经过优化的模式——SQLite 可以利用索引的降序遍历直接取出最新的 N 行，无需全表扫描。

### 8.4 设计决策总结

| 决策 | 选择 | 替代方案 | 理由 |
|------|------|----------|------|
| 数据传递 | Parquet 文件 | 直接内存传递 / Redis 缓存 | Streamlit @st.cache_data 的序列化兼容性 + 跨会话缓存 |
| SQL 查询方式 | `conn.execute()` 直查 | `query_kline()` 封装 | `query_kline()` 不支持 `ts <= cutoff_date` 筛选 |
| 排序方式 | DESC + 手动反转 | ASC 直接查询 | 利用索引的降序遍历，性能更优 |
| X 轴 | `np.arange(len(df))` | 时间戳 | 滤波算法要求均匀采样 |
| Bar 合成 | 只合成不完整 bar | 预计算所有 bar 写入 DB | 保持 DB 是"真实数据"的唯一来源 |
| 合成分辨率 | 紧邻更细一级 TF | 直接用 1 分钟合成所有级别 | 查询效率更高，数据一致性更好 |
| Parquet index | `index=False` | `index=True` | 避免无意义 RangeIndex 占用存储 |

---

## 附录 A: 关键文件与行号速查

| 函数 | 文件 | 行号 |
|------|------|------|
| `_sync_to_display()` | `data_loader.py` | 245-332 (backtest-bar-synthesis) |
| `_get_period_end()` | `data_loader.py` | 142-175 |
| `_get_period_start()` | `data_loader.py` | 203-210 |
| `_fetch_all_timeframes()` | `data_loader.py` | 61-99 |
| `_fetch_stock()` | `data_loader.py` | 101-193 |
| `_load_chart_data()` | `streamlit_app.py` | 115-172 |
| `_render_chart()` | `streamlit_app.py` | 509-660 |
| `_get_min_tf_and_count()` | `streamlit_app.py` | 427-463 |
| `_get_bar_date_from_db()` | `streamlit_app.py` | 1120-1128 |
| `_run_backtest_play()` | `streamlit_app.py` | 1225-1264 |
| `_render_backtest_mode()` | `streamlit_app.py` | 1267-1363 |
| `query_kline()` | `db.py` | 86-133 |
| `get_conn()` | `db.py` | 19-26 |
| `clear_display_cache()` | `db.py` | 348-356 |
| `ALL_TFS` | `sidebar.py` | 13 |
| `TF_HIERARCHY` | `sidebar.py` | 48-52 |
