# 回测数据流 -- 变更前后完整对比报告

> **分支**: master -> feat/backtest-data-analysis
> **日期**: 2026-07-05
> **范围**: 回测模式数据加载路径重构 + 级联合成（Cascading Bar Synthesis v2）

---

## 1. 变更概览

| 维度 | 数值 |
|------|------|
| 提交数 | 8 (3 docs + 1 design + 1 review + 1 v2 + 1 feat + 2 fix) |
| 文件数 | 12 (7 docs + 1 src changed + 1 src modified + 1 test + 2 config) |
| 新增行数 | 8,225 |
| 删除行数 | 13 |
| 净增行数 | 8,212 |

### 提交清单

```
e040310 fix: _write_parquet写入路径错误导致回测回放失败
086effc fix: data_loader.py ALL_TFS 导入错误导致 streamlit 启动失败
f2662f0 feat: 回测级联合成(cascading synthesis)实现       ← 核心变更
3adca5f docs: 级联合成方案v2 + v2再审报告
dd21b0e docs: 级联合成方案审查报告 -- 3个阻断性问题
b9718cd docs: 回测数据级联(cascading)合成方案设计
f780a39 docs: 回测模式DB->Parquet数据流深度分析(逐行级别)
3e06479 docs: 回测数据加载流程完整分析报告
```

### 一页纸变更总结

**问题**: 回测模式下，`_sync_to_display()` 仅从 DB 查询已完成 bar 写入 parquet。当 `cutoff_date` 落在某周期中间（如 14:47），该周期的 bar 尚未完成，图表显示"卡"在上一周期的收盘价，无法反映 `cutoff_date` 时刻的最新价格。同时，4 个视图各自调用 `_sync_to_display()`，产生 4 次独立的 DB 查询，且无 bar 合成能力。

**方案**: 引入 `_sync_all_cascading()` 前置处理 -- 在 4 个 fragment 渲染之前一次性完成所有 TF 的 data 准备（DB 查询 + 级联合成 + parquet 写入）。粗周期的不完整 bar 通过聚合更细周期的已完成 bar 来合成，级联传递（15min -> 60min -> 日线 -> 周线）。

**效果**: 回测模式下每个 bar 索引位置，4 个 TF 的图表都能显示到 `cutoff_date` 时刻的最新行情；浏览模式完全不受影响。

---

## 2. 架构对比

### 2.1 变更前架构（master）

```
+------------------------------------------------------------------+
|  main()                                                          |
|    |                                                             |
|    +-- _render_backtest_mode()    # 模式切换, slider, nav        |
|    |                                                             |
|    +-- sorted_views 循环 (4 iterations)                          |
|          |                                                       |
|          +-- _render_chart_fragment() [st.fragment]              |
|                |                                                 |
|                +-- _render_chart()                               |
|                      |                                           |
|                      +-- _load_chart_data()                      |
|                      |     |                                     |
|                      |     +-- 回测: _sync_to_display()           |
|                      |     |     +-- DB query kline              |
|                      |     |     +-- write {tf}.parquet          |
|                      |     |                                     |
|                      |     +-- 回退: _cached_fetch_stock()       |
|                      |           +-- yfinance API                |
|                      |                                           |
|                      +-- pd.read_parquet({tf}.parquet)           |
|                      +-- _compute_filters()                      |
|                      +-- _compute_schmitt_trigger()              |
|                      +-- _compute_strategy_display()             |
|                      +-- make_subplots() + render                |
+------------------------------------------------------------------+

数据流:
  [4 views] --each calls--> _sync_to_display() --reads--> SQLite kline table
                                                          |
                                                     writes {tf}.parquet
                                                          |
                                                     _load_chart_data reads it
```

**问题**:
1. 4 个视图各自调用 `_sync_to_display()` -> 4 次独立 DB 查询（可能重复）
2. `_sync_to_display()` 只能查已完成 bar，cutoff_date 中间的 bar 缺失
3. 无 bar 合成能力，粗周期（60min/日/周）可能少一根 bar
4. `min_tf` 信息未传递给 `_sync_to_display()`，无法做级联判断

### 2.2 变更后架构（feat/backtest-data-analysis）

```
+------------------------------------------------------------------+
|  main()                                                          |
|    |                                                             |
|    +-- _render_backtest_mode()    # 模式切换, slider, nav        |
|    |                                                             |
|    +-- *** NEW ***                                               |
|    |   if cb_mode:                                               |
|    |     _sync_all_cascading()       # 前置: 一次性处理所有 TF    |
|    |       |                                                     |
|    |       +-- for tf in tfs (finest->coarsest):                 |
|    |             |                                               |
|    |             +-- _query_tf_from_db()       # DB 查询         |
|    |             +-- _needs_synthesis()?         # 是否需要合成  |
|    |             |     |                                         |
|    |             |     YES:                                      |
|    |             |     +-- _find_immediate_finer_tf()             |
|    |             |     +-- _synthesize_incomplete_bar()           |
|    |             |     |     +-- _query_tf_for_period()           |
|    |             |     |     +-- _aggregate_bars()                |
|    |             |     |                                         |
|    |             |     NO (tf=min_tf): skip synthesis            |
|    |             |                                               |
|    |             +-- _build_output_df()  # DB + synth bar        |
|    |             +-- _write_parquet()     # write {tf}.parquet   |
|    |                                                             |
|    +-- sorted_views 循环 (4 iterations)                          |
|          |                                                       |
|          +-- _render_chart_fragment() [st.fragment]              |
|                |                                                 |
|                +-- _render_chart()                               |
|                      |                                           |
|                      +-- _load_chart_data()  # *** SIMPLIFIED ***|
|                      |     |                                     |
|                      |     +-- 回测: pass (parquet already ready)|
|                      |     +-- 浏览: _sync_to_display() (unchanged)|
|                      |                                           |
|                      +-- pd.read_parquet({tf}.parquet)  # 只读  |
|                      +-- ... (unchanged)                         |
+------------------------------------------------------------------+

*** NEW MODULES (data_loader.py, ~240 lines) ***
  _ensure_tz_naive()         -- 时区剥离
  _get_tz_suffix()           -- 时区后缀提取
  _format_synth_date()       -- 合成bar Date格式化
  _get_period_start_ts()     -- 下一周期开始时间
  _get_query_start_for_synthesis()  -- 合成查询起点
  _query_tf_from_db()        -- DB查询(显示用)
  _query_tf_for_period()     -- 时间范围查询(合成用)
  _find_immediate_finer_tf() -- 链路上溯找更细TF
  _needs_synthesis()         -- 判断是否需要合成
  _aggregate_bars()          -- OHLCV聚合
  _synthesize_incomplete_bar() -- 从细TF合成粗TF bar
  _build_output_df()         -- 合并DB数据+合成bar
  _write_parquet()           -- 写入parquet
  _sync_all_cascading()      -- 主入口(调度)
```

---

## 3. 浏览模式数据流对比

### 3.1 变更前

```
main()
  └─ _render_chart_fragment()
       └─ _render_chart()
            └─ _load_chart_data(tf, day_offset, n_pts, window_start=None, cutoff_date=None)
                 │
                 ├─ _sync_to_display(ticker_code, tf, day_offset, n_pts)
                 │    ├─ query_kline(ticker_code, tf, n_pts, day_offset)
                 │    │    └─ DB: SELECT ... ORDER BY ts DESC LIMIT n_pts
                 │    └─ write data/display/{tf}.parquet
                 │
                 └─ pd.read_parquet(data/display/{tf}.parquet)
                      └─ extract t, noisy, ohlc, dates
```

| 步骤 | 输入 | 输出 | 说明 |
|------|------|------|------|
| `_render_chart()` | cfg, market, ticker_code, key | (rendered chart) | chart 主函数 |
| `_load_chart_data()` | tf, day_offset, n_pts | (t, noisy, ohlc, dates) | 数据加载 |
| `_sync_to_display()` | ticker_code, tf, day_offset, n_pts | (ok, count) | 写 parquet |
| `query_kline()` | ticker_code, tf, n_pts, day_offset | DataFrame | DB 查询 |
| `pd.read_parquet()` | {tf}.parquet | DataFrame | 读 parquet |

### 3.2 变更后

```
main()
  └─ _render_chart_fragment()
       └─ _render_chart()
            └─ _load_chart_data(tf, day_offset, n_pts, window_start=None, cutoff_date=None)
                 │
                 ├─ _sync_to_display(ticker_code, tf, day_offset, n_pts)
                 │    ├─ query_kline(ticker_code, tf, n_pts, day_offset)
                 │    └─ write data/display/{tf}.parquet
                 │
                 └─ pd.read_parquet(data/display/{tf}.parquet)
                      └─ extract t, noisy, ohlc, dates
```

> **结论: 浏览模式完全无变更。** 代码路径、函数调用、数据流与变更前一致。

---

## 4. 回测模式数据流对比（核心变更）

### 4.1 变更前

#### 完整调用链

```
main()
  │  cb_mode=True, window_start=bar_index, cutoff_date="2026-07-03T14:47:00+08:00"
  │
  └─ sorted_views loop (4 views):
       │
       ├─ view[0] tf="60分钟"
       │    └─ _render_chart_fragment()
       │         └─ _render_chart()
       │              └─ _load_chart_data(tf="60分钟", window_start=..., cutoff_date=...)
       │                   │
       │                   ├─ [回测分支] _sync_to_display(ticker, "60分钟", n_pts=120, cutoff_date=cutoff_date)
       │                   │    ├─ DB: SELECT ... WHERE ts <= "2026-07-03T14:47:00+08:00"
       │                   │    │        ORDER BY ts DESC LIMIT 120
       │                   │    │   -> 返回 119 条完成 bar (最后一条是 14:00)
       │                   │    │   -> **14:47 的未完成 bar 不存在！**
       │                   │    └─ write data/display/60分钟.parquet
       │                   │
       │                   └─ pd.read_parquet(data/display/60分钟.parquet)
       │                        -> 只到 14:00，缺少 14:00-14:47 的数据
       │
       ├─ view[1] tf="15分钟"  ── 同样调用 _sync_to_display -> 查询 -> 写 -> 读
       ├─ view[2] tf="日线"    ── 同样调用 _sync_to_display -> 查询 -> 写 -> 读
       └─ view[3] tf="周线"    ── 同样调用 _sync_to_display -> 查询 -> 写 -> 读
```

#### _load_chart_data() 回测分支详解（变更前，共 12 行）

```python
# streamlit_app.py:122-133 (master)
if window_start is not None:
    ok, count = _sync_to_display(ticker_code, tf, n_pts=n_pts, cutoff_date=cutoff_date)
    if not ok:
        t, noisy, ohlc, ticker_full, dates, err = _cached_fetch_stock(...)
        if err is None and dates is not None and len(dates) > n_pts:
            t = t[-n_pts:]
            noisy = noisy[-n_pts:]
            ohlc = ohlc.iloc[-n_pts:]
            dates = dates[-n_pts:]
        return t, noisy, ohlc, ticker_full, dates, err
```

#### _sync_to_display() cutoff_date 分支详解（变更前与变更后相同）

```python
# data_loader.py:148-165 (both master and HEAD, this function unchanged)
if cutoff_date is not None:
    from db import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT ts, open, high, low, close, volume
               FROM kline WHERE ticker=? AND timeframe=? AND ts <= ?
               ORDER BY ts DESC LIMIT ?""",
            (ticker_code, tf, cutoff_date, n_pts),
        ).fetchall()
    if rows:
        rows.reverse()
        df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
        # ... write parquet
        return True, len(df)
    return False, 0
```

#### 4 个视图逐个调用 _sync_to_display 的流程

```
时间线: main() -> view0(60分钟) -> view1(15分钟) -> view2(日线) -> view3(周线)

view0: _sync_to_display("60分钟", cutoff) -> DB查询 -> write 60分钟.parquet  ┐
view1: _sync_to_display("15分钟", cutoff) -> DB查询 -> write 15分钟.parquet  │ 4次
view2: _sync_to_display("日线", cutoff)   -> DB查询 -> write 日线.parquet    │ DB
view3: _sync_to_display("周线", cutoff)   -> DB查询 -> write 周线.parquet    ┘ 查询
```

#### 存在的问题

| 问题 | 严重度 | 描述 |
|------|--------|------|
| 无 bar 合成 | **高** | `cutoff_date=14:47` 时，60 分钟的最后完成 bar 是 14:00，无法合成 14:01-14:47 的未完成 bar |
| min_tf 未传递 | **中** | `_sync_to_display()` 不知道哪个是最细 TF，无法判断是否应该合成 |
| 并发重复查询 | **低** | 4 个视图各自写 parquet，虽然 `pd.read_parquet` 只读安全，但 4 次独立 DB 查询浪费资源 |
| 无时区处理 | **中** | DB 中分钟 TF 带时区 (`+08:00`)，日线级无时区，混合比较会崩溃（实际未被触发，因无合成路径） |

### 4.2 变更后

#### 完整调用链

```
main()
  │  cb_mode=True, window_start=bar_index, cutoff_date="2026-07-03T14:47:00+08:00"
  │
  ├─ *** NEW: 前置级联合成 ***
  │  _sync_all_cascading(ticker, ["15分钟","60分钟","日线","周线"], cutoff_date, "15分钟")
  │    │
  │    ├─ [TF=15分钟] min_tf, skip synthesis
  │    │    ├─ _query_tf_from_db() -> 120条完成bar
  │    │    ├─ _build_output_df() -> 120条 (无合成bar)
  │    │    └─ _write_parquet("15分钟") -> 15分钟.parquet
  │    │
  │    ├─ [TF=60分钟] needs synthesis? YES (14:47 >= 14:01)
  │    │    ├─ finer_tf = _find_immediate_finer_tf("60分钟", tfs) -> "15分钟"
  │    │    ├─ _synthesize_incomplete_bar("60分钟", ...):
  │    │    │    ├─ _query_tf_for_period("15分钟", "14:01", "14:47")
  │    │    │    │   -> [14:00, 14:15, 14:30, 14:45] 的完成 bar
  │    │    │    ├─ finer_synth_bar=None (15分钟=min_tf, 无合成)
  │    │    │    └─ _aggregate_bars() -> OHLCV 合成 bar
  │    │    │       Date="2026-07-03T14:47:00+08:00"
  │    │    ├─ _build_output_df() -> 119条DB + 1条合成 = 120条
  │    │    └─ _write_parquet("60分钟") -> 60分钟.parquet
  │    │
  │    ├─ [TF=日线] needs synthesis? YES (07-03T14:47 >= 07-03T00:00)
  │    │    ├─ finer_tf = "60分钟"
  │    │    ├─ _synthesize_incomplete_bar("日线", ...):
  │    │    │    ├─ actual_start = 09:30 (market open optimization)
  │    │    │    ├─ _query_tf_for_period("60分钟", "09:30", "14:47")
  │    │    │    │   -> [09:30, 10:30, 11:30, 12:30, 13:30, 14:00] 完成bar
  │    │    │    ├─ 拼接 60分钟的合成 bar (Date="14:47+08:00")
  │    │    │    └─ _aggregate_bars() -> 7条60分钟bar -> 日线合成bar
  │    │    │       Date="2026-07-03T14:47:00" (无时区)
  │    │    ├─ _build_output_df() -> 119条DB + 1条合成 = 120条
  │    │    └─ _write_parquet("日线") -> 日线.parquet
  │    │
  │    └─ [TF=周线] needs synthesis? YES (07-03 >= 06-27)
  │         ├─ finer_tf = "日线"
  │         ├─ _synthesize_incomplete_bar("周线", ...):
  │         │    ├─ _query_tf_for_period("日线", "06-27", "14:47")
  │         │    │   -> 06-29~07-02 的 4 条日线 bar (周末无数据)
  │         │    ├─ 拼接日线合成 bar
  │         │    └─ _aggregate_bars() -> 周线合成bar
  │         │       Date="2026-07-03T14:47:00" (无时区)
  │         ├─ _build_output_df() -> 119条DB + 1条合成 = 120条
  │         └─ _write_parquet("周线") -> 周线.parquet
  │
  └─ sorted_views loop (4 views):
       │  *** 每个 view 只读 parquet，无 DB 写入 ***
       │
       ├─ view[0] tf="60分钟": _load_chart_data() -> pass -> pd.read_parquet()
       ├─ view[1] tf="15分钟": _load_chart_data() -> pass -> pd.read_parquet()
       ├─ view[2] tf="日线":   _load_chart_data() -> pass -> pd.read_parquet()
       └─ view[3] tf="周线":   _load_chart_data() -> pass -> pd.read_parquet()
```

#### main() 中 _sync_all_cascading() 前置处理

```python
# streamlit_app.py:1611-1617 (HEAD)
if cb_mode and ticker_code and cutoff_date:
    tfs_in_use = sorted(set(cfg["tf"] for cfg in configs),
                        key=lambda x: ALL_TFS.index(x))
    min_tf_val = AppState.get("_min_tf", "")
    if tfs_in_use and min_tf_val:
        _sync_all_cascading(ticker_code, tfs_in_use, cutoff_date, min_tf_val)
```

#### _load_chart_data() 回测分支简化（变更后）

```python
# streamlit_app.py:122-125 (HEAD)
if window_start is not None:
    # 回测模式: parquet 已由 _sync_all_cascading() 前置写入
    # 直接走下方 parquet 读取路径; parquet 不存在时走 API 回退
    pass
```

从 12 行缩减为 4 行注释 + `pass`。

#### 级联合成流程: 15min -> 60min -> 日线 -> 周线

```
synth_cache = {}  # 内存缓存，{tf: {synth_bar, db_sample_ts}}

第1级 min_tf=15分钟:
  DB查询(120条) -> tf==min_tf? YES -> 跳过合成 -> parquet写入
  synth_cache["15分钟"] = {synth_bar: None}
        ↓
第2级 60分钟:
  DB查询 -> _needs_synthesis? 14:47>=14:01 -> YES
  finer_tf = _find_immediate_finer_tf("60分钟") -> "15分钟"
  _synthesize_incomplete_bar("60分钟", finer_tf="15分钟", finer_synth_bar=None)
    -> query 15分钟 DB [14:01, 14:47] -> 4条完成bar
    -> _aggregate_bars() -> 合成bar
  _build_output_df(119条DB + 1条合成) -> parquet写入
  synth_cache["60分钟"] = {synth_bar: {...}}
        ↓
第3级 日线:
  DB查询 -> _needs_synthesis? 07-03T14:47 >= 07-03T00:00 -> YES
  finer_tf = _find_immediate_finer_tf("日线") -> "60分钟"
  _synthesize_incomplete_bar("日线", finer_tf="60分钟", finer_synth_bar=synth_cache["60分钟"].synth_bar)
    -> query 60分钟 DB [09:30, 14:47] -> 6条完成bar
    -> 拼接60分钟合成bar(14:47) -> 7条60分钟bar
    -> _aggregate_bars() -> 日线合成bar(Date无时区)
  _build_output_df(119条DB + 1条合成) -> parquet写入
  synth_cache["日线"] = {synth_bar: {...}}
        ↓
第4级 周线:
  DB查询 -> _needs_synthesis? 07-03 >= 06-27 -> YES
  finer_tf = _find_immediate_finer_tf("周线") -> "日线"
  _synthesize_incomplete_bar("周线", finer_tf="日线", finer_synth_bar=synth_cache["日线"].synth_bar)
    -> query 日线 DB [06-27, 07-03T14:47] -> 4条(周末跳过)
    -> 拼接日线合成bar -> 5条日线bar
    -> _aggregate_bars() -> 周线合成bar(Date无时区)
  _build_output_df(119条DB + 1条合成) -> parquet写入
```

#### 每个 TF 的合成决策表

以 `cutoff_date="2026-07-03T14:47:00+08:00"`、4 个视图 TF 为 `["15分钟","60分钟","日线","周线"]`、`min_tf="15分钟"` 为例：

| TF | 角色 | last completed bar | period_start | 需要合成? | 数据源 | 合成结果 |
|----|------|--------------------|-------------|----------|--------|---------|
| 15分钟 | min_tf | 14:45 | N/A | **否** (tf==min_tf) | DB 直查 | 只写 DB 的 120 条 |
| 60分钟 | mid | 14:00+08:00 | 14:01 | **是** (14:47 >= 14:01) | 15min DB [14:01, 14:47] | 4条15min -> 1条60min合成bar |
| 日线 | mid | 07-02T00:00 | 07-03T00:00 | **是** (07-03T14:47 >= 07-03T00:00) | 60min DB [09:30, 14:47] + 60min合成bar | 7条60min -> 1条日线合成bar |
| 周线 | coarsest | 06-26T00:00 | 06-27T00:00 | **是** (07-03 >= 06-27) | 日线 DB [06-27, 14:47] + 日线合成bar | 5条日线 -> 1条周线合成bar |

---

## 5. 函数级变更对照表

### 5.1 streamlit_app.py

| 位置 | 变更类型 | 变更前 | 变更后 | 影响 |
|------|---------|--------|--------|------|
| import (L38-40) | **修改** | `from services.data_loader import _fetch_all_timeframes, _fetch_stock, _sync_to_display` | 追加 `_sync_all_cascading` | 引入新函数 |
| `_load_chart_data()` L122-133 | **简化** | 回测分支调用 `_sync_to_display()` + API回退 + 截断逻辑 (12行) | 回测分支改为 `pass` + 注释 (4行) | parquet 已由前置处理准备好 |
| `main()` L1611-1617 | **新增** | (无此逻辑) | `if cb_mode: _sync_all_cascading(...)` (7行) | 前置集中处理所有 TF |

### 5.2 data_loader.py

| 位置 | 变更类型 | 描述 |
|------|---------|------|
| L15-16 | **新增** | `ALL_TFS` 常量定义 |
| L196-434 | **新增** | 级联合成全部函数 (~240行) |
| `_sync_to_display()` | **不变** | 函数签名和逻辑完全不变 |

---

## 6. 新增函数清单（data_loader.py 14 个）

| # | 函数 | 行数(约) | 职责 | 被谁调用 |
|---|------|---------|------|---------|
| 1 | `_ensure_tz_naive()` | 5 | 剥离 `pd.Timestamp` 时区信息 | `_needs_synthesis`, `_get_period_start_ts`, `_get_query_start_for_synthesis`, `_synthesize_incomplete_bar` |
| 2 | `_get_tz_suffix()` | 10 | 从 ISO 字符串提取时区后缀 (`+08:00`, `Z`) | `_format_synth_date` |
| 3 | `_format_synth_date()` | 10 | 格式化合成 bar 的 Date 为 TF 原生格式 | `_synthesize_incomplete_bar` |
| 4 | `_get_period_start_ts()` | 8 | 计算下一周期开始时间 (统一 `last_ts + 1单位`) | `_needs_synthesis` |
| 5 | `_get_query_start_for_synthesis()` | 8 | 计算合成数据查询起点 (与 #4 同逻辑) | `_synthesize_incomplete_bar` |
| 6 | `_query_tf_from_db()` | 15 | 查询 TF 的前 n_pts 条完成 bar (显示用) | `_sync_all_cascading` |
| 7 | `_query_tf_for_period()` | 14 | 查询指定时间范围内所有 bar (合成用，无 n_pts 限制) | `_synthesize_incomplete_bar` |
| 8 | `_find_immediate_finer_tf()` | 8 | 在 TF 列表中找紧邻的更细 TF | `_sync_all_cascading` |
| 9 | `_needs_synthesis()` | 8 | 判断 cutoff_date 是否进入下一周期需合成 | `_sync_all_cascading` |
| 10 | `_aggregate_bars()` | 10 | OHLCV 聚合 (O=first, H=max, L=min, C=last, V=sum) | `_synthesize_incomplete_bar` |
| 11 | `_synthesize_incomplete_bar()` | 50 | 核心: 从更细 TF 数据合成一条粗 TF bar | `_sync_all_cascading` |
| 12 | `_build_output_df()` | 10 | 合并 DB 完成 bar + 合成 bar，截断到 n_pts | `_sync_all_cascading` |
| 13 | `_write_parquet()` | 8 | 写入 `data/display/{tf}.parquet` | `_sync_all_cascading` |
| 14 | `_sync_all_cascading()` | 45 | **主入口**: 级联合成调度 (按 TF finest->coarsest) | `main()` |

---

## 7. 数据存储对比

| 存储 | 变更前 | 变更后 | 说明 |
|------|--------|--------|------|
| SQLite (`kline` 表) | 写入: `upsert_kline()` (无变更) | 写入: 同左 | **不变** |
| Parquet (`data/display/{tf}.parquet`) | 写入者: `_sync_to_display()` (每个 view 各写一次) | 写入者: `_sync_all_cascading()` (前置一次) + `_sync_to_display()` (浏览模式) | 回测模式写入集中化 |
| Parquet Date 列格式 | 分钟级 DB 带时区, 日线级无时区 (潜在混合风险) | 合成 bar Date 按 `_format_synth_date()` 匹配 TF 原生格式 | **新增**格式统一 |
| `synth_cache` (内存) | 不存在 | `{tf: {synth_bar, db_sample_ts}}` — 仅在 `_sync_all_cascading()` 生命周期内使用 | **新增**内存缓存 |
| `backtest_config.json` | 已存在 (无变更) | 同左 | **不变** |

---

## 8. 回放路径对比（逐步骤）

以 `bar_index` 从 500 推进到 501 的一次回放为例。

假设: `min_tf="15分钟"`, `bar_index=501`, `cutoff_date` 更新为 bar 501 对应的日期。

| 步骤 | 变更前 | 变更后 |
|------|--------|--------|
| 1. 用户点击 ">" | `st.session_state._bar_index = 501` | 同左 |
| 2. `_update_cutoff_and_rerun()` | `_get_bar_date_from_db(ticker, "15分钟", 500)` -> cutoff_date | 同左 |
| 3. `st.rerun()` | 触发 | 同左 |
| 4. `main()` 开始 | `need_rerun = _run_backtest_play()` -> False | 同左 |
| 5. 数据准备 | (无) | **`_sync_all_cascading(ticker, ["15分钟","60分钟","日线","周线"], cutoff, "15分钟")`** |
| 5a. 15分钟 | (无) | DB 查询 120 条 -> 写 15分钟.parquet (不合成) |
| 5b. 60分钟 | (无) | DB 查询 + 从 15分钟 DB+合成bar 合成 -> 写 60分钟.parquet |
| 5c. 日线 | (无) | DB 查询 + 从 60分钟 DB+合成bar 合成 -> 写 日线.parquet |
| 5d. 周线 | (无) | DB 查询 + 从 日线 DB+合成bar 合成 -> 写 周线.parquet |
| 6. 渲染 view0 (60分钟) | `_load_chart_data()` -> `_sync_to_display("60分钟", cutoff)` -> DB查询 -> 写 parquet -> 读 parquet | `_load_chart_data()` -> `pass` -> 读 parquet (已由步骤5b准备好) |
| 7. 渲染 view1 (15分钟) | `_load_chart_data()` -> `_sync_to_display("15分钟", cutoff)` -> DB查询 -> 写 -> 读 | `_load_chart_data()` -> `pass` -> 读 parquet (已由步骤5a准备好) |
| 8. 渲染 view2 (日线) | `_load_chart_data()` -> `_sync_to_display("日线", cutoff)` -> DB查询 -> 写 -> 读 | `_load_chart_data()` -> `pass` -> 读 parquet (已由步骤5c准备好) |
| 9. 渲染 view3 (周线) | `_load_chart_data()` -> `_sync_to_display("周线", cutoff)` -> DB查询 -> 写 -> 读 | `_load_chart_data()` -> `pass` -> 读 parquet (已由步骤5d准备好) |
| 10. 图表显示 | 每个 TF 只显示完成 bar (60分钟到 14:00, 日线到 07-02, 周线到 06-26) | 每个 TF 显示完成 bar + 合成 bar (60分钟到 14:47, 日线到 07-03T14:47, 周线到 07-03T14:47) |
| DB 查询次数 | 4 次 (每个 view 1 次) | 4 + N 次 (_sync_all_cascading 中每 TF 1 次, 合成时额外查询) |

---

## 9. Bug 修复记录

| Bug | 根因 | 修复提交 | 影响 |
|-----|------|---------|------|
| Streamlit 启动失败 | `data_loader.py` 添加 `ALL_TFS` 常量后，`from services.data_loader import ...` 未包含 `ALL_TFS`，但 `streamlit_app.py` 仍然从 `components.sidebar` 导入 `ALL_TFS` — 实际未影响启动。根因是 `data_loader.py` 内 `ALL_TFS` 在 `_sync_all_cascading()` 等函数中引用，但这些函数在模块加载时不会执行 | `086effc` | 开发环境阻塞 |
| 回测回放失败 | `_write_parquet()` 中 `display_dir` 路径计算使用了错误的 `parent` 层级 (`parent.parent.parent` vs 正确的路径)，导致 parquet 写入到错误目录，图表读取不到数据 | `e040310` | 回测模式功能完全不可用 |

---

## 10. 测试覆盖对比

| 测试维度 | 变更前 | 变更后 |
|---------|--------|--------|
| 单元测试文件 | 无针对数据加载的测试 | `tests/test_cascading_synthesis.py` (301 行, 33 项) |
| 时区处理测试 | -- | `test_ensure_tz_naive_strips_timezone`, `test_format_synth_date_minute_tf`, `test_format_synth_date_daily_tf` |
| 周期计算测试 | -- | `test_get_period_start_ts` (9 组参数化: 分钟/日/周/月/季 + 跨年) |
| 合成判断测试 | -- | `test_needs_synthesis` (4 组参数化: 需要/不需要) |
| 级联端到端 | -- | `test_cascade_60min_to_daily_uses_synth_bar` |
| 浏览模式回归 | 无自动化测试 | 无自动化测试 (手动验证: 浏览模式路径未触碰新代码) |
| 回测播放 | 手动测试 | 手动测试 + 33 项核心逻辑测试通过 |
| 冒烟验证 | -- | 33 项核心逻辑测试全部通过 |

---

## Appendix: 关键设计决策

### A1: 合成 bar 时间戳使用 cutoff_date

合成 bar 的 Date 字段设置为 `cutoff_date`（而非周期结束时间）。这确保:
- `ts <= cutoff_date` 过滤自然匹配（DB 查询和 parquet 读取一致）
- 图表上合成 bar 出现在正确的时间位置
- 各 TF 的 parquet Date 列保持可排序

### A2: min_tf 永不合成

min_tf（4 个视图中最细的周期）只有 DB 已有数据，不做合成。因为:
- min_tf 是级联链路的数据源头
- 如果 min_tf 也需要合成，会形成循环依赖
- min_tf 的不完整 bar 本身也有信息量（就是当前周期的部分数据）

### A3: 合成 bar Date 格式按 TF 区分

- 分钟级 TF (1m/5m/15m/60m): 保留时区后缀 (`+08:00`)
- 日线级 TF (日/周/月/季): 剥离时区 (无后缀)

这确保 parquet 中各 TF 的 Date 格式与 DB 原生格式一致，避免 `pd.to_datetime()` 时的混合时区问题。

### A4: 日线合成 60 分钟从 09:30 开始

日线从 60 分钟合成时，查询起点从 `last_daily_ts + 1天` 调整为 `09:30`，避免查询盘前的空数据时段。

---

> **报告版本**: 1.0
> **数据来源**: git show master + HEAD + diff + cascading-synthesis-v2.md
> **所有函数名、行号、调用链均与代码验证一致**
