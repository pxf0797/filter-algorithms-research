# 级联合成方案审查报告

> 审查人: 量化系统审查专家 | 审查日期: 2026-07-05
> 审查目标: `/Users/xfpan/claude/filter_research/docs/cascading-synthesis-design.md` (v1.0, 2324行)
> 对照代码: `data_loader.py`, `db.py`, `streamlit_app.py`

---

## 1. 时间格式冲突

### 1.1 `_get_period_start` 只返回日期的问题

**严重程度**: 中等 | **涉及**: 设计文档第 671-699 行 `_get_period_start()` 函数, 第 1550-1579 行 `_get_query_start_for_synthesis()` 函数

**问题描述**:

设计方案定义了两个不同的 `period_start` 函数，它们返回格式不一致：

| 函数 | 日线返回格式 | 60分钟返回格式 | 用途 |
|------|------------|--------------|------|
| `_get_period_start()` (设计第671行) | `"2026-07-03"` (只有日期) | `"2026-07-03T14:01:00"` (完整时间戳) | SQL WHERE 过滤 |
| `_get_query_start_for_synthesis()` (设计第1550行) | `pd.Timestamp("2026-07-03T00:00:00")` | `pd.Timestamp("2026-07-03T14:01:00")` | DataFrame 时间范围过滤 |

这两个函数返回不同格式用于不同场景，本身不是 bug。但设计方案在 `_synthesize_incomplete_bar()` 中使用了 `_get_query_start_for_synthesis()`（返回 `pd.Timestamp`），与 `cutoff_dt`（也是 `pd.Timestamp`）做比较，这是正确的。

**实际风险**：如果将来有人混淆这两个函数（比如在 SQL WHERE 中用 `_get_query_start_for_synthesis` 的返回值，或在 DataFrame 过滤中用 `_get_period_start` 的日期字符串），会产生隐蔽的 bug。

**修复建议**:
1. 将 `_get_period_start()` 重命名为 `_get_period_start_str()`，明确其为字符串格式
2. 或将两个函数合并，统一返回 `pd.Timestamp`，在 SQL 调用处转为字符串
3. 至少在两处函数文档中明确标注返回值类型和用途

---

### 1.2 合成 bar 的 Date 字段 tz-aware 与 DB 数据的 tz-naive 混合

**严重程度**: 严重 | **涉及**: 设计文档第 1609 行, `db.py` 第 55 行

**问题描述**:

DB 中不同 TF 的数据时间戳格式不一致：

- **分钟级数据** (1m/5m/15m/60m): yfinance 返回 timezone-aware Timestamp，`idx.isoformat()` 产生带时区的字符串，如 `"2026-07-03T14:00:00+08:00"`
- **日线及以上** (日线/周线/月线/季线): yfinance 返回 timezone-naive Timestamp，`idx.isoformat()` 产生无时区的字符串，如 `"2026-07-03T00:00:00"`

合成 bar 的代码（设计第 1609 行）将 `Date` 设为 `cutoff_date`（来自 DB 的 min_tf ts，如 `"2026-07-03T14:47:00+08:00"`），始终带时区。

当日线合成 bar 写入 parquet 后，`_load_chart_data()` 执行 `pd.to_datetime(df["Date"])`。此时 Date 列包含：

```
"2026-07-02T00:00:00"          ← DB 日线 bar (tz-naive)
"2026-07-03T14:47:00+08:00"    ← 合成日线 bar (tz-aware, +08:00)
```

`pd.to_datetime()` 处理混合时区列时行为不确定：
- 在 pandas >= 2.0 中，如果列中包含 tz-naive 和 tz-aware 混合值，`pd.to_datetime()` 会抛出 `TypeError` 或将所有值统一为 tz-naive（丢弃时区信息）
- 统一为 tz-naive 后，`"2026-07-03T14:47:00+08:00"` 被当作 `"2026-07-03T14:47:00"` (无时区)，而 `"2026-07-03T00:00:00"` 也是 `"2026-07-03T00:00:00"`，排序可能错乱

**修复建议**:
```python
# 在 _build_output_df() 或 _write_parquet() 中统一时区
# 方案A: 所有 bar 统一转为 tz-naive ISO 字符串（推荐，简单）
def _normalize_date(ts_str):
    """统一 Date 为无时区的 ISO 格式字符串"""
    dt = pd.Timestamp(ts_str)
    if dt.tz is not None:
        dt = dt.tz_convert(None)  # 转为本地时间但去除时区标记
    return dt.isoformat()

# 方案B: 所有 bar 统一转为 UTC ISO 字符串
# 方案C: 在 parquet 中 Date 列统一使用一种格式，读取时统一处理
```

---

### 1.3 `pd.Timestamp` tz-aware vs tz-naive 比较会抛出 TypeError

**严重程度**: 严重 (阻断性 bug) | **涉及**: 设计文档第 1462-1479 行 `_needs_synthesis()`, 第 1582-1615 行 `_synthesize_incomplete_bar()`

**问题描述**:

`_needs_synthesis()` 函数中：

```python
last_ts = pd.Timestamp(db_rows[-1]["Date"])   # 来自DB，可能是tz-naive或tz-aware
period_start = _get_period_start_ts(last_ts, tf)  # _get_period_start_ts内部调用
                                                     # .normalize() → tz-naive
cutoff_dt = pd.Timestamp(cutoff_date)           # 来自DB min_tf ts → tz-aware (+08:00)
return cutoff_dt >= period_start
```

`_get_period_start_ts()` 的所有分支都通过 `.replace()` 或 `.normalize()` 返回 **tz-naive** 的 `pd.Timestamp`（因为这些方法会丢弃时区信息）。

而 `cutoff_date` 来自 `_get_bar_date_from_db()`（`streamlit_app.py:1128`），它从 kline 表读取 ts 字段。对于分钟级数据，ts 包含时区（如 `"2026-07-03T14:47:00+08:00"`），`pd.Timestamp()` 解析后是 **tz-aware** 的。

**在 pandas 中，tz-aware Timestamp 和 tz-naive Timestamp 比较会抛出 `TypeError`**:
```
TypeError: Cannot compare tz-naive and tz-aware timestamps
```

这会直接导致 `_needs_synthesis()` 崩溃，阻断整个级联合成流程。

`_synthesize_incomplete_bar()` 中同样的问题：
```python
prev_dt = pd.to_datetime(prev_data["Date"])  # mixed tz
cutoff_dt = pd.Timestamp(cutoff_date)         # tz-aware (+08:00)
mask = (prev_dt >= query_start) & (prev_dt <= cutoff_dt)  # 可能TypeError
```

**修复建议**:
```python
# 在 _needs_synthesis() 中:
def _needs_synthesis(tf, db_rows, cutoff_date):
    last_ts = pd.Timestamp(db_rows[-1]["Date"])
    period_start = _get_period_start_ts(last_ts, tf)
    cutoff_dt = pd.Timestamp(cutoff_date)

    # ★ 统一为 tz-naive 后再比较
    if cutoff_dt.tz is not None:
        cutoff_dt = cutoff_dt.tz_localize(None)
    if period_start.tz is not None:
        period_start = period_start.tz_localize(None)

    return cutoff_dt >= period_start

# 或者在 _get_period_start_ts() 中保留时区:
# 将 cutoff_date 的时区传递给函数，让其返回相同 tz 的 Timestamp
```

**更根本的修复**: 在 `_get_period_start_ts()` 中保留输入 `ts` 的时区信息：
```python
def _get_period_start_ts(ts, tf):
    ts = pd.Timestamp(ts)
    tz = ts.tz  # 保存时区

    if tf == "60分钟":
        result = (ts + pd.Timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    elif tf == "日线":
        result = (ts + pd.Timedelta(days=1)).normalize()
    # ...

    # 恢复时区
    if tz is not None:
        result = result.tz_localize(tz)
    return result
```

---

### 1.4 cutoff_date 在字符串比较中的行为

**严重程度**: 轻微 | **涉及**: `_query_tf_from_db()` 设计第 1438-1459 行, `_sync_to_display()` `data_loader.py:148-155`

**问题描述**:

`_query_tf_from_db()` 使用 SQL `WHERE ts <= ?` 做字符串比较。DB 中不同 TF 的 ts 格式不一致（分钟级带时区、日线无时区），但 `cutoff_date` 来自 min_tf（始终带时区，如 `"2026-07-03T14:47:00+08:00"`）。

字符串比较 `"2026-07-02T00:00:00" <= "2026-07-03T14:47:00+08:00"` → **True**（因为 `'2' < '3'` 在位置8处）—— 恰好正确。

但 `"2026-07-03T00:00:00" <= "2026-07-03T14:47:00+08:00"` → **True**（`'0' < '1'` 在位置11处）—— 也正确。

然而 `"2026-07-03T14:47:00+08:00" <= "2026-07-03T14:47:00+08:00"` → **True** —— 正确。

在 SQLite 中 ISO 8601 字符串的字典序等于时间序，所以字符串比较能正确工作。**但前提是所有 ts 格式一致**。如果某条数据格式异常（如缺少前导零），字符串比较结果将不可预测。

**修复建议**: 当前代码路径恰好安全，但建议在文档中备注此假设。如果未来数据源变更（如引入非 ISO 8601 格式的时间戳），需同步修改比较逻辑。

---

## 2. 逻辑纰漏

### 2.1 `_get_period_start_ts()` 对周线的计算错误

**严重程度**: 严重 (逻辑错误) | **涉及**: 设计文档第 1525-1529 行

**问题描述**:

```python
elif tf == "周线":
    days_ahead = 4 - ts.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return (ts + pd.Timedelta(days=days_ahead)).normalize()
```

此代码计算的是**下一个周五**（下一个周线的**结束**时间），而非**下一个周期的开始**时间。

**具体场景**:
- `last_ts` = "2026-07-03"（周五，本周的周线 bar）
- `days_ahead = 4 - 4 = 0` → `days_ahead = 7`
- 返回 `"2026-07-10"`（下一个周五）—— 这是下周的**结束**日期

**后果**: `_needs_synthesis()` 中：
```python
cutoff_dt = "2026-07-07"  (周二)
period_start = "2026-07-10"  (下周五)
cutoff_dt >= period_start? → False → 不合成！
```

但 07-07 显然已经进入了新的周线周期（07-03 之后），应该触发合成。**这会导致周线（以及依赖周线的月线/季线）在新一周的任何时间都不合成**，直到 cutoff 日期 >= 下周五。

同理，如果周线是级联链路中的一环（如日线→周线→月线），月线合成也会受影响。

**修复**:
```python
elif tf == "周线":
    # 下一个周期的开始 = 当前周结束的下一天
    return (ts + pd.Timedelta(days=1)).normalize()
```

同时，`_get_period_start()`（设计第 682-683 行）对周线返回 `(ts + 1 day).strftime("%Y-%m-%d")`，这个是正确的，与修复后的 `_get_period_start_ts()` 一致。

---

### 2.2 `_get_period_start_ts()` 对月线/季线的语义正确但注释误导

**严重程度**: 轻微 | **涉及**: 设计文档第 1531-1544 行

**问题描述**:

```python
elif tf == "月线":
    if ts.month == 12:
        next_month = pd.Timestamp(year=ts.year + 1, month=1, day=1)
    else:
        next_month = pd.Timestamp(year=ts.year, month=ts.month + 1, day=1)
    # 返回月末最后一天+1 = 下月1日
    return next_month
```

注释 "返回月末最后一天+1 = 下月1日" 是正确的——对于 `last_ts = "2026-06-30"`（6月最后一天），返回 `2026-07-01`（下月1日），这是下一个周期的**开始**。逻辑正确。

但变量名 `next_month` 容易让人误解为"下个月的月末"。建议重命名为 `next_month_start` 并更新注释。

季线类似——变量名 `q_end_month` 误导（它实际上是季度结束月份，但函数用它计算下一季度开始月份），逻辑本身正确。

---

### 2.3 级联传递: `prev_data` 的窗口大小与合成数据需求的潜在不匹配

**严重程度**: 中等 | **涉及**: 设计文档第 271-341 行 `_sync_all_cascading()`, 第 397-413 行 `_build_output_df()`

**问题描述**:

设计方案中 `prev_data` 始终是经过 `_build_output_df()` 截断到 `n_pts`（默认120条）的 DataFrame。对于某些合成场景，120 条可能不够覆盖所需的时间范围：

**场景: 月线从日线合成**
- 需要从 `last_completed_monthly_ts + 1天` 到 `cutoff_date` 的所有日线 bar
- 如果 cutoff_date 是某月 25 号，且 last_completed_monthly_ts 是上月最后一天，则需要约 25 条日线 bar
- 120 条日线 bar 覆盖约 6 个月，足够 ✓

**场景: 季线从月线合成**
- 需要从 `last_completed_quarterly_ts + 1月` 到 `cutoff_date` 的所有月线 bar
- 最多需要 3 条月线 bar（一个季度）
- 120 条月线 bar 覆盖 10 年，远超需求 ✓

**场景: 日线从 60 分钟合成（A股）**
- 120 条 60 分钟 bar ≈ 约 17 个交易日（每天 7 条）— 足够 ✓
- 但如果 ticker 有 extended hours 数据（美股），每天可能有 13+ 条 60 分钟 bar，120 条只覆盖 9 个交易日 — 仍然足够 ✓

**结论**: 当前 `n_pts=120` 对所有实际场景都足够。但设计方案对此没有任何讨论或断言。建议添加：

```python
# 断言: n_pts 必须 >= 一个完整周期的更细 TF bar 数
# 对于最坏情况（月线从日线合成），需要约 23 个交易日的数据
assert n_pts >= 23, f"n_pts={n_pts} may not cover a full month of daily bars"
```

或在文档中明确说明此假设。

---

### 2.4 `_needs_synthesis()` 中的 `pd.Timestamp` 解析对日线数据可能失败

**严重程度**: 中等 | **涉及**: 设计文档第 1475 行

**问题描述**:

```python
last_ts = pd.Timestamp(db_rows[-1]["Date"])
```

`db_rows[-1]["Date"]` 对于日线及以上 TF，可能存储为 `"2026-07-03"`（仅有日期）或 `"2026-07-03T00:00:00"`（日期+零时刻）。`pd.Timestamp()` 能正确解析两种格式。

但对于某些 ticker/市场，日线 ts 可能存储为 `"2026-07-03 00:00:00"`（空格而非 T 分隔符），`pd.Timestamp()` 也能解析，但后续的字符串比较和时区处理可能出问题。

**当前风险低**，因为 `idx.isoformat()` 始终使用 `T` 分隔符。但建议在 `_needs_synthesis()` 中增加容错：

```python
last_ts = pd.Timestamp(db_rows[-1]["Date"])
if last_ts is pd.NaT:
    logger.warning(f"Cannot parse last_ts: {db_rows[-1]['Date']}")
    return False
```

---

### 2.5 合成 bar 时间戳使用 `cutoff_date` 导致的语义问题

**严重程度**: 中等 | **涉及**: 设计文档第 145-167 行（挑战B讨论）, 第 1609 行

**问题描述**:

设计方案决定合成 bar 的 `Date` 使用 `cutoff_date`（而非 `period_end`）。这在级联合成中是正确的技术选择（确保下一级能过滤到合成 bar），但带来两个语义/显示问题：

**问题 A: 同一 TF 下 bar 的时间戳不遵循统一约定**

正常 60 分钟 bar: ts = "10:00", "11:00", "14:00"（整点）
合成 60 分钟 bar: ts = "14:47"（非整点，cutoff 时刻）

在同一个 60 分钟 parquet 中，部分 bar 的 ts 是整点，部分是任意时刻。这在以下场景可能造成混淆：
- 图表 x 轴刻度不均匀
- 后续处理代码如果假设 60 分钟 bar 的 ts 总是在整点，可能出错

**问题 B: 日线 bar 的 ts 格式不一致**

正常日线 bar: ts = "2026-07-02"（仅有日期）
合成日线 bar: ts = "2026-07-03T14:47:00+08:00"（完整日期时间+时区）

这在 parquet Date 列中造成格式混合（见 1.2）。

**修复建议**: 在文档中明确声明"合成 bar 的时间戳可以不遵循该 TF 的正常时间戳约定"，并检查后续处理代码是否对 ts 格式有假设。建议在合成 bar 上增加标记字段（如 `is_synthetic: True`），避免后续代码误判。

---

## 3. 边界条件缺口

### 3.1 cutoff_date 恰好等于 `period_start` 时的条件判断

**严重程度**: 轻微 | **涉及**: 设计文档第 1479 行 `_needs_synthesis()`

**问题描述**:

```python
return cutoff_dt >= period_start
```

使用 `>=` 意味着当 cutoff_date 等于 period_start 时，触发合成。例如：
- 60分钟: last_ts="14:00", period_start="15:00", cutoff="15:00" → 触发合成
- 但此时 DB 中可能有 15:00 的完成 bar（如果刚好刷新了数据）

如果 DB 中有 15:00 的 bar，`db_rows[-1]["Date"]` 就是 "15:00" 而非 "14:00"，此时 `period_start` 会是 "16:00"，不会误合成。OK。

但如果 DB 中还没有 15:00 的 bar（可能是数据延迟），此时合成一个 bar 然后 DB 数据更新后又出现真正的 15:00 bar → parquet 中可能出现两个重叠时间段的 bar。但由于每次 rerun 都会重新执行 `_sync_all_cascading()`，下一次 rerun 时 DB 中已有 15:00 bar，合成被跳过。OK。

**结论**: 行为正确，但建议显式注释此边界。

---

### 3.2 刚切换 ticker 时 `prev_data` 为空

**严重程度**: 中等 | **涉及**: 设计文档第 286-341 行

**问题描述**:

设计方案中 `_sync_all_cascading()` 的循环逻辑：
```python
prev_data = None
for tf in processing_order:
    db_rows = _query_tf_from_db(...)
    if not db_rows:
        prev_data = None  # 当前TF无数据
        continue
    needs_synth = (tf != min_tf and prev_data is not None and ...)
```

当切到新 ticker 时：
1. `_sync_all_cascading()` 在 `main()` 的 fragment 之前执行
2. 如果新 ticker 数据尚未加载，所有 TF 的 `_query_tf_from_db()` 返回 `[]`
3. 所有 `results[tf] = False`
4. `_load_chart_data()` 中 parquet 不存在 → 回退到 `_cached_fetch_stock()` → 从 API 获取

此时的回退路径是正确的，但回退获取的是"当前最新"数据，而非 cutoff_date 对应的历史数据。用户会在回测 slider 位置看到错误的时间点数据。

**修复建议**: 在 `main()` 的级联合成调用前增加数据就绪检查：
```python
if cb_mode and ticker_code and cutoff_date and min_tf:
    if not has_data(ticker_code):
        st.warning("回测数据未就绪，请先在浏览模式加载数据")
    else:
        _sync_all_cascading(...)
```

---

### 3.3 股市开盘时间未考虑

**严重程度**: 中等 | **涉及**: 设计文档第 531-533 行

**问题描述**:

设计方案在 3.4.1 节提到"当前不做特殊处理：有什么 bar 用什么 bar 合成"，这是合理的 pragmatic 选择。但有两个具体场景值得注意：

**场景: 日线从 60 分钟合成的 period_start**

对于美股，日线 period_start 设置为 `last_completed_daily_ts + 1 天 = "2026-07-03T00:00:00"`。但 60 分钟数据中，最早可能只有 09:30 的 bar（盘前如果有 extended hours 则更早）。查询 `Date >= "2026-07-03T00:00:00"` 会包含所有当天 60 分钟 bar，这没问题。

但对于 A 股，如果 DB 中 60 分钟数据包含集合竞价时段（09:15-09:25）的特殊 bar，或者午休时段（11:30-13:00）没有 bar，合成日线的 O/H/L/C/V 仍然是正确的（因为聚合规则使用实际存在的 bar）。

**场景: 隔夜 gap 对合成日线 Open 的影响**

如果 DB 中当天第一条 60 分钟 bar 的 Open 已经包含了隔夜 gap，合成日线的 Open 就正确反映了实际开盘价。但如果数据源中 60 分钟 bar 的 Open 是前一日收盘价调整后的，合成日线 Open 可能有偏差。

**建议**: 在文档中添加交易时段假设的说明，明确"合成结果精确度受限于更细 TF 的数据覆盖范围，不额外填充非交易时段数据"。

---

### 3.4 如果 `min_tf` 是 1 分钟或 5 分钟

**严重程度**: 轻微 | **涉及**: 设计文档第 297-301 行

**问题描述**:

当前设计假设 60 分钟以下的所有分钟 TF 都已在 DB 中完成。但如果用户配置 `min_tf = "1分钟"`：
- 1 分钟本身不合成（`tf == min_tf`）
- 5 分钟可能需要在 `tfs` 列表中，但如果视图中没有 5 分钟，级联链路是: 1分钟 → 60分钟 → 日线 → ...
- 60分钟合成需要从 1分钟 DataFrame 中选取大量 bar（最多 60 条），性能 OK

如果用户配置 `min_tf = "5分钟"` 但视图中有 15 分钟：
- 15 分钟需要合成，但 prev_data 是 5 分钟数据
- 设计方案的 `_get_period_start_ts` 和 `_get_query_start_for_synthesis` 都支持 5 分钟和 15 分钟 TF ✓

**建议**: 在测试中覆盖 `min_tf="1分钟"` 和 `min_tf="5分钟"` 场景。

---

### 3.5 周线 `_get_period_start` 跨年边界

**严重程度**: 轻微 (测试覆盖问题) | **涉及**: 设计文档第 682-683 行

**问题描述**:

```
last_ts = "2026-12-31" (周四), tf = "周线"
_get_period_start(ts, tf) = (2026-12-31 + 1天).strftime("%Y-%m-%d") = "2027-01-01"
```

结果 `"2027-01-01"` 是正确的（下一天），但这天是周五（元旦），可能不是交易日。用 `"2027-01-01"` 去查日线数据，DB 中没有这一天的 bar → 合成的周线 bar 将缺少这一天，这恰好是正确行为。

**建议**: 在边界测试中包含跨年场景。

---

## 4. 兼容性风险

### 4.1 回测模式下 `_load_chart_data()` 无降级路径当 parquet 不存在

**严重程度**: 中等 | **涉及**: `streamlit_app.py` 第 121-136 行, 设计文档第 455-481 行

**问题描述**:

设计方案修改后的 `_load_chart_data()`：
```python
if window_start is not None:
    # 回测模式: parquet已由_sync_all_cascading()准备好
    pass  # 直接跳到parquet读取
else:
    ok, count = _sync_to_display(...)  # 浏览模式

# 共用parquet读取
display_path = ...
if display_path.exists():
    # 读取parquet
else:
    # err stays None → 回退到 _cached_fetch_stock()
```

**风险**: 如果 `_sync_all_cascading()` 对某个 TF 写入失败（`results[tf] = False`），parquet 不存在，`_load_chart_data()` 会回退到 `_cached_fetch_stock()`。但 `_cached_fetch_stock()` 获取的是"当前最新"API 数据，而非 cutoff_date 对应的历史数据。在回测模式下，这会显示错误时间点的数据。

**修复建议**:
```python
if window_start is not None:
    # 回测模式: 尝试读取parquet
    display_path = ...
    if not display_path.exists():
        # parquet未就绪，尝试降级到 _sync_to_display()
        ok, count = _sync_to_display(ticker_code, tf, n_pts=n_pts,
                                      cutoff_date=cutoff_date)
        if not ok:
            # 最终降级: 显示空图表而非错误数据
            return None, None, None, None, None, f"{tf} 数据未就绪"
```

---

### 4.2 `_sync_to_display()` 回测分支被绕过但未删除

**严重程度**: 轻微 | **涉及**: 设计文档第 1660-1705 行

**问题描述**:

设计方案中 `_sync_to_display()` 的简化版本保留了回测分支（`cutoff_date is not None`），但在新的 `_load_chart_data()` 中，回测模式不再调用 `_sync_to_display()`。这意味着 `_sync_to_display()` 的回测分支成为死代码。

**风险**: 
- 如果将来有人重构又把回测模式的调用加回来，会走旧的单级合成路径（而非级联合成），产生不一致
- 死代码增加维护困惑

**修复建议**: 
- 方案A: 从 `_sync_to_display()` 中移除回测分支，保留纯浏览模式
- 方案B: 保留回测分支作为 `_sync_all_cascading()` 的降级路径（见 4.1）

---

### 4.3 `ALL_TFS` 常量在 `data_loader.py` 和 `sidebar.py` 中重复定义

**严重程度**: 轻微 | **涉及**: 设计文档第 1797-1818 行

**问题描述**:

设计方案建议在 `data_loader.py` 中独立定义 `ALL_TFS` 以避免从 UI 模块导入。这引入了一个"两份真理来源"的风险。设计方案在文档中提到了单元测试来验证一致性，但测试可能不会在每次代码修改后运行。

**修复建议**: 将 `ALL_TFS` 提取到 `filter_app/constants.py`（或类似共享模块），两个模块都从此处导入。

---

### 4.4 浏览模式与回测模式共享 parquet 文件

**严重程度**: 轻微 | **涉及**: 设计文档第 416-425 行

**问题描述**:

浏览模式和回测模式都写入 `data/display/{tf}.parquet`。如果用户快速在浏览模式和回测模式之间切换，可能读到对方写入的数据。

**当前保护**: 浏览模式通过 `day_offset` 控制窗口，回测模式通过 `cutoff_date` 控制窗口。两个模式写入的内容不同（不同时间范围的数据），但文件名相同。快速切换时可能有一瞬间读到错误数据。

**修复建议**: 使用不同的文件名前缀区分模式：
```python
# 浏览模式: data/display/browse_{tf}.parquet
# 回测模式: data/display/backtest_{tf}.parquet
```
或者在读取前检查 parquet 中的 Date 范围是否与当前模式匹配。

---

## 5. 综合评估

### 严重程度统计

| 严重程度 | 数量 | 问题编号 |
|---------|------|---------|
| **严重** (阻断性) | 3 | 1.2 (混合时区), 1.3 (tz-aware vs tz-naive TypeError), 2.1 (周线周期计算错误) |
| **中等** (可能出错) | 6 | 1.1 (函数命名混淆), 2.3 (窗口大小), 2.4 (解析容错), 2.5 (语义问题), 3.2 (空数据降级), 4.1 (无降级路径) |
| **轻微** (边界条件) | 7 | 1.4, 2.2, 3.1, 3.3, 3.4, 3.5, 4.2, 4.3, 4.4 |

### 必须修复才能工作的问题

1. **时区比较 TypeError (1.3)**: `_needs_synthesis()` 和 `_synthesize_incomplete_bar()` 中 tz-aware 与 tz-naive Timestamp 的比较会直接抛异常。修复: 统一所有 Timestamp 为同一时区（建议 tz-naive）。

2. **周线周期计算 (2.1)**: `_get_period_start_ts()` 对周线返回下一个周五（period END）而非 period START，导致周线合成永远不触发。修复: 改为 `(ts + 1 day).normalize()`。

3. **混合时区 parquet (1.2)**: 日线 parquet 中正常 bar (tz-naive) 与合成 bar (tz-aware) 混合，`pd.to_datetime()` 行为不确定。修复: 在 `_build_output_df()` 中统一 Date 格式。

### 可以工作的部分

- 级联合成的总体架构（内存传递 A2 方案）设计合理
- 分钟级合成逻辑（60分钟从5/15分钟）正确
- `_get_query_start_for_synthesis()` 的逻辑正确
- 月线/季线的 `_get_period_start_ts()` 实现正确
- 浏览模式代码路径完全不受影响
- `_build_output_df()` 截断和合并逻辑正确
- 同一 TF 多视图去重逻辑正确

### 建议的修复优先级

```
P0 (立即修复，否则无法运行):
  1. 修复 _get_period_start_ts 周线逻辑 (2.1)
  2. 修复 tz-aware vs tz-naive TypeError (1.3)
  3. 统一 parquet Date 格式 (1.2)

P1 (Phase 3 前修复，否则可能出错):
  4. 添加 _load_chart_data 降级路径 (4.1)
  5. 合并/重命名两个 period_start 函数 (1.1)
  6. 添加数据就绪检查 (3.2)

P2 (Phase 4 前修复，改善健壮性):
  7. 提取 ALL_TFS 到共享模块 (4.3)
  8. 添加合成 bar 标记字段 (2.5)
  9. 区分浏览/回测 parquet 文件 (4.4)
  10. 补充跨年/跨月边界测试 (3.5)
```

---

### 附录: 关键时间格式对照表

| 位置 | 格式 | 时区 | 示例 |
|------|------|------|------|
| DB kline.ts (分钟级) | ISO 8601 str | **有** (+08:00 或 Z) | `2026-07-03T14:00:00+08:00` |
| DB kline.ts (日线+) | ISO 8601 str | **无** | `2026-07-03T00:00:00` |
| cutoff_date | ISO 8601 str | **有** (继承自 min_tf) | `2026-07-03T14:47:00+08:00` |
| `_get_period_start_ts` 返回 | pd.Timestamp | **无** | `Timestamp('2026-07-03T00:00:00')` |
| 合成 bar Date (设计) | str = cutoff_date | **有** | `"2026-07-03T14:47:00+08:00"` |
| `_get_period_start` 返回 (分钟) | str | **无** | `"2026-07-03T14:01:00"` |
| `_get_period_start` 返回 (日线+) | str (仅日期) | **无** | `"2026-07-03"` |

**核心矛盾**: 来自 min_tf 的 `cutoff_date` 和 `_get_period_start_ts` 的返回值时区不一致，且 parquet 中不同 TF 的 Date 格式混合。
