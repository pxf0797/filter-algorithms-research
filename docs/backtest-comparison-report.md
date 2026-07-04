# 浏览模式 vs 回测模式 — 完整差异报告

> 分支: feat/backtest-minimal | 日期: 2026-07-04 | Phase1 重构后
> 仓库: pxf0797/filter-algorithms-research

---

## 一、数据读取 (Data Loading)

### 1.1 入口函数对比

`_load_chart_data` (streamlit_app.py:115) 是两种模式的统一入口。唯一的分叉点在 window_start 参数：

| 维度 | 浏览模式 | 回测模式 |
|------|---------|---------|
| 调用参数 | `bar_index=None, window_start=None` | `bar_index=N, window_start=N` |
| 数据同步调用 | `_sync_to_display(ticker, tf, day_offset, n_pts)` | `_sync_to_display(ticker, tf, n_pts=n_pts, window_start=N)` |
| day_offset 作用 | **生效** — 控制取多少天之前的数据 | **被忽略** — window_start 优先 |
| 数据来源 | `data/display/{tf}.parquet` | `data/display/{tf}.parquet` |
| 截断逻辑 | **无** (parquet 已是 n_pts 条) | **无** (parquet 已是 n_pts 条) |
| 返回数据量 | 始终 n_pts 条 | 始终 n_pts 条 |

**签名** (`streamlit_app.py:115`):
```python
def _load_chart_data(market, ticker_code, tf, day_offset, n_pts,
                     bar_index=None, window_start=None) -> tuple:
```

**分叉逻辑** (`streamlit_app.py:121-126`):
```python
if window_start is not None:
    _sync_to_display(ticker_code, tf, n_pts=n_pts, window_start=window_start)
else:
    _sync_to_display(ticker_code, tf, day_offset=day_offset, n_pts=n_pts)
```

### 1.2 `_sync_to_display` 对比

函数位置: `services/data_loader.py:139`

| 维度 | 浏览模式 (`window_start=None`) | 回测模式 (`window_start=N`) |
|------|-------------------------------|---------------------------|
| 数据获取方式 | 调用 `query_kline()` (带 day_offset) | 直接 SQL 查 DB (OFFSET) |
| SQL 查询 | `ts <= cutoff ORDER BY ts DESC LIMIT n_pts` | `ORDER BY ts ASC LIMIT n_pts OFFSET N` |
| 排序方向 | DESC (取最新) | ASC (按位置顺序) |
| day_offset 作用 | 通过 SQL `datetime(MAX(ts), '-N days')` 前移截止日期 | **不使用** |
| force_full 参数 | 无 | 无 |
| 写入量 | n_pts 条 | n_pts 条 |
| 写入文件 | `data/display/{tf}.parquet` | `data/display/{tf}.parquet` |
| 返回值 | `(bool, int)` — 成功标志 + 行数 | `(bool, int)` — 成功标志 + 行数 |

**浏览模式 SQL** (`db.py:97-101`):
```sql
SELECT ts, open, high, low, close, volume
FROM kline WHERE ticker=? AND timeframe=? AND ts <= ?
ORDER BY ts DESC LIMIT ?
```

**回测模式 SQL** (`data_loader.py:151-154`):
```sql
SELECT ts, open, high, low, close, volume
FROM kline WHERE ticker=? AND timeframe=?
ORDER BY ts ASC LIMIT ? OFFSET ?
```

**Phase1 关键改进**: 两种模式写入 parquet 的都是精确 n_pts 条数据，`_load_chart_data` 不再需要后续截断。

### 1.3 数据源链路

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          浏览模式 (Browse Mode)                           │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  yfinance API ──► _fetch_stock() ──► upsert_kline() ──► SQLite (kline)  │
│                                                                          │
│  用户操作 ──► _load_chart_data()                                         │
│                  │                                                       │
│                  ▼                                                       │
│              _sync_to_display(ticker, tf, day_offset, n_pts)             │
│                  │                                                       │
│                  ▼                                                       │
│              query_kline(ticker, tf, n_pts, day_offset)                  │
│                  │ SQL: ts <= cutoff ORDER BY ts DESC LIMIT n_pts        │
│                  ▼                                                       │
│              data/display/{tf}.parquet  ←── 写入 n_pts 条                │
│                  │                                                       │
│                  ▼                                                       │
│              pd.read_parquet() ──► 返回 (t, noisy, ohlc, ...)            │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│                          回测模式 (Backtest Mode)                         │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  yfinance API ──► _fetch_stock() ──► upsert_kline() ──► SQLite (kline)  │
│                                                                          │
│  用户拖动 slider ──► _render_backtest_mode()                             │
│                        │                                                 │
│                        ▼                                                 │
│                    AppState.set("_bar_index", N)                          │
│                        │                                                 │
│                        ▼  (st.rerun)                                     │
│                        │                                                 │
│  _load_chart_data(ticker, tf, ..., window_start=N)                       │
│      │                                                                   │
│      ▼                                                                   │
│  _sync_to_display(ticker, tf, n_pts=n_pts, window_start=N)               │
│      │                                                                   │
│      ▼                                                                   │
│  直接 SQL: ORDER BY ts ASC LIMIT n_pts OFFSET N                          │
│      │                                                                   │
│      ▼                                                                   │
│  data/display/{tf}.parquet  ←── 写入 n_pts 条                            │
│      │                                                                   │
│      ▼                                                                   │
│  pd.read_parquet() ──► 返回 (t, noisy, ohlc, ...)                        │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

**关键差异**: 浏览模式走 `query_kline()` (带 day_offset 日期偏移)，回测模式走直接 SQL (带 OFFSET 位置偏移)。

### 1.4 Parquet 文件内容对比

| 维度 | 浏览 parquet | 回测 parquet |
|------|-------------|-------------|
| 行数 | n_pts 条 (如 120) | n_pts 条 (如 120) |
| 时间位置 | 最新 N 条 (受 day_offset 偏移) | 从 DB 第 window_start 条开始的 N 条 |
| 列 | Date, Open, High, Low, Close, Volume | Date, Open, High, Low, Close, Volume |
| 排序 | 时间升序 (query_kline 内部做了 `iloc[::-1]`) | 时间升序 (SQL ORDER BY ts ASC) |
| 4 视图是否共享 | **各 TF 独立文件** (各自取最新 N 条) | **各 TF 独立文件** (各自按 offset 取 N 条) |

### 1.5 修复方案 (待实施)

#### 修复 1-1: 统一 SQL 查询路径 [Minor]

**现状**: 浏览模式走 `query_kline()` (db.py:97-116)，回测模式走 `_sync_to_display` 内联 SQL (data_loader.py:143-148)，两套不同的 SQL 代码路径。

**方案**: 在 `query_kline` 中增加 `offset` 参数:
```python
def query_kline(ticker, tf, n_pts=120, day_offset=0, offset=None):
    """
    offset=None: 取最新 n_pts 条 (浏览模式, 支持 day_offset 日期偏移)
    offset=N:    从第 N 条开始取 n_pts 条 (回测模式, 窗口滑动)
    """
    with get_conn() as conn:
        if offset is not None:
            rows = conn.execute(
                """SELECT ts, open, high, low, close, volume
                   FROM kline WHERE ticker=? AND timeframe=?
                   ORDER BY ts ASC LIMIT ? OFFSET ?""",
                (ticker, tf, n_pts, offset),
            ).fetchall()
        else:
            # 原有 day_offset 逻辑
            ...
```

**影响**: `_sync_to_display` 回测分支删除内联SQL, 改为调用 `query_kline(offset=window_start)`。两处 SQL 合并为一处。

---

#### 修复 1-2: 统一参数语义 [Minor]

**现状**: 浏览用 `day_offset` (天数偏移), 回测用 `window_start` (行号偏移), 两个参数在 `_sync_to_display` 和 `_load_chart_data` 中并存但互斥。

**方案**: `_load_chart_data` 只保留一个定位参数 `window_start`:
- 浏览模式: `window_start=None`, 内部计算为"最新N条"
- 回测模式: `window_start=N`, 内部转为 DB OFFSET

`day_offset` 回归为浏览模式的独立功能（时间窗口导航），不传递到 `_sync_to_display`。

**影响**: 函数签名更简洁, 参数语义清晰。

---

#### 修复 1-3: 回退路径补全截断 [Minor]

**现状**: 当 parquet 不存在时，回退到 `_cached_fetch_stock` (yfinance API), 但回测模式没有在此路径上应用 `window_start` 截断，浏览模式同理没有应用 `day_offset`。

**方案**: 在 `_load_chart_data` 的 API 回退分支 (line 163) 增加窗口截断:
```python
# API 回退路径也应用截断
t, noisy, ohlc, ticker_full, dates, err = _cached_fetch_stock(...)
if err is None and window_start is not None and dates is not None:
    # 回测模式: 截取窗口
    start = min(window_start, len(dates))
    end = min(start + n_pts, len(dates))
    t = t[start:end]
    noisy = noisy[start:end]
    ...
return t, noisy, ohlc, ticker_full, dates, err
```

**影响**: API 回退路径也正确截断, 浏览和回测行为一致。

---

## 二、数据显示 (Data Display)

### 2.1 数据量对比

| 维度 | 浏览模式 | 回测模式 | Phase1 状态 |
|------|---------|---------|------------|
| 每个视图数据量 | 始终 n_pts 条 | 始终 n_pts 条 | **已统一** |
| 数据截断 | 不截断 (parquet已含精确条数) | 不截断 (parquet已含精确条数) | **已统一** |

### 2.2 时间范围对比

| 维度 | 浏览模式 | 回测模式 |
|------|---------|---------|
| 时间范围 | DB 最新 N 条 (受 day_offset 偏移) | 窗口 slider 对应的 DB 中 N 条 |
| 4 视图时间对齐 | **不对齐** — 各 TF 独立取最新 N 天数据 | **共享 window_start** — 各 TF 独立查 N 条，但起始位置一致 |
| 跨 TF 比较 | 可能看到不同时间段的数据 | Slider 位置一致，但各 TF 的 N 条覆盖时间段不同 |

**注意**: 回测模式下 4 个视图共享同一个 `window_start` 值，但对不同 TF 来说，`window_start=100` 的含义不同（日线的第 100 条 vs 周线的第 100 条，时间跨度不同）。

### 2.3 4 视图一致性

| 维度 | 浏览模式 | 回测模式 |
|------|---------|---------|
| 数据获取方式 | 各自独立调 `query_kline` 取最新 N 条 | 各自独立调 SQL OFFSET 取窗口 N 条 |
| 时间对齐 | 大致对齐 (都是最新) | 索引位置对齐 (都是 window_start 位置) |
| 数据独立 | 完全独立 | 完全独立 |

### 2.4 图表渲染管线 (模式无关)

| 函数 | 位置 | 是否区分模式 | 说明 |
|------|------|------------|------|
| `_render_chart_fragment` | streamlit_app.py:486 | 否 | 仅透传 `bar_index`, `window_start` |
| `_render_chart` | streamlit_app.py:493 | 否 | 接收 `bar_index`, `window_start` 但不使用，透传给 `_load_chart_data` |
| `_compute_filters` | streamlit_app.py:161 | 否 | 纯数学计算，模式无关 |
| `_compute_schmitt_trigger` | streamlit_app.py:191 | 否 | 纯数学计算，模式无关 |
| `_compute_prediction_pairs` | streamlit_app.py:202 | 否 | 纯数学计算，模式无关 |
| `_compute_strategy_display` | streamlit_app.py:221 | 否 | 纯数学计算，模式无关 |
| `_date_markers` | streamlit_app.py:75 | 否 | 纯数据处理，模式无关 |
| `_determine_subplot_layout` | streamlit_app.py:258 | 否 | 布局计算，模式无关 |

**结论**: 图表渲染管线完全不感知模式差异。模式差异完全隔离在数据加载层 `_load_chart_data` → `_sync_to_display`。

---

## 三、数据显示设置 (Display Settings / Controls)

### 3.1 侧边栏控件对比

| 控件 | 渲染函数 | 位置 | 浏览可见 | 回测可见 | 回测下是否生效 |
|------|---------|------|---------|---------|-------------|
| 模式 radio (`_bt_mode_radio`) | `_render_backtest_mode` | streamlit_app.py:1117 | Yes | Yes | Yes |
| 时间窗口导航 (前移/后移/最新) | `_render_time_nav` | streamlit_app.py:1054 | Yes | Yes | **No — 失效但不隐藏** |
| 移动步长 (day_step) | `_render_time_nav` | streamlit_app.py:1060 | Yes | Yes | **No — 失效但不隐藏** |
| 已偏移天数显示 | `_render_time_nav` | streamlit_app.py:1093 | Yes | Yes | **No — 显示但值无意义** |
| 窗口位置 slider | `_render_backtest_mode` | streamlit_app.py:1197 | No | Yes | Yes |
| 窗口时间范围显示 | `_render_backtest_mode` | streamlit_app.py:1191 | No | Yes | Yes |
| n_pts (每视图) | `_render_params` | — | Yes | Yes | Yes |
| 滤波器选择 (global_f) | `_render_filter_selectors` | — | Yes | Yes | Yes |

**已知问题**: 回测模式下 `_render_time_nav` 控件的 "前移/后移/最新" 按钮和 `day_offset`/`day_step` 仍可见但失效（day_offset 被 window_start 覆盖）。这是问题 #3 (Major)。

### 3.2 Slider 行为对比

| 维度 | 浏览模式 | 回测模式 |
|------|---------|---------|
| 有无 slider | **无** — 使用前移/后移按钮 | **有** — `_bt_bar_slider` (streamlit_app.py:1197) |
| 范围 | N/A | `[0, max(0, total_bars - window_size)]` |
| 窗口大小 | N/A | min_tf 对应的 n_pts (默认 120) |
| 步长 | N/A | 1 |
| 拖动触发 | N/A | `st.rerun()` + 更新 `_bar_index`, `_bt_cutoff_date` |
| bar_index=0 含义 | N/A | 历史最早 bar |
| bar_index=末尾 含义 | N/A | 历史最晚 window_size 条（含最新数据） |

### 3.3 配置文件

**`data/backtest_config.json`**:
```json
{
  "ticker": "AAPL",
  "min_tf": "日线",
  "bar_count": 2520,
  "window_size": 120,
  "cached_at": "2026-07-04T10:12:00"
}
```

- **保存时机**: 切换到回测模式时 (`_render_backtest_mode` streamlit_app.py:1136)
- **加载时机**: 通过 `_load_backtest_config()` (streamlit_app.py:468) 定义，但目前 **未被主动调用**
- **作用**: 记录上次回测的状态，可用于恢复

---

## 四、状态管理 (State Management)

### 4.1 状态键对比表

| Key | 类型 | 默认值 | 浏览模式 | 回测模式 | 定义位置 |
|-----|------|--------|---------|---------|---------|
| `_cb_mode` | bool | False | False | True | state.py:43 |
| `_bar_index` | int | 0 | 不使用 | 窗口起始位置 | state.py:44 |
| `_bt_cutoff_date` | str | "" | 不使用 | 窗口末尾 bar 日期 | state.py:45 |
| `_min_tf` | str | "" | 不使用 | 4 视图中最精细周期 | state.py:46 |
| `_min_tf_bar_count` | int | 0 | 不使用 | min_tf 总 bar 数 | state.py:47 |
| `_day_offset` | int | 0 | 时间窗口偏移天数 | **透传但被忽略** | state.py:32 |
| `_fetched_ticker` | str | "" | 确认为当前 ticker | 确认为当前 ticker | state.py:31 |

**SYSTEM_KEYS 回测相关条目** (`state.py:42-47`):
```python
"_cb_mode": False,
"_bar_index": 0,
"_bt_cutoff_date": "",
"_min_tf": "",
"_min_tf_bar_count": 0,
```

### 4.2 模式切换时的状态转换

**进入回测模式** (`streamlit_app.py:1124-1149`):

```
1. _get_min_tf_and_count(configs, ticker_code)
   ├─► 遍历 4 视图，找 ALL_TFS 索引最小的 tf
   └─► SELECT COUNT(*) FROM kline WHERE ticker=? AND timeframe=?

2. AppState.set("_min_tf", min_tf)
3. AppState.set("_min_tf_bar_count", bar_count)
4. AppState.set("_bar_index", 0)

5. 查找 min_tf 对应视图的 n_pts 作为 window_size
6. _save_backtest_config(ticker_code, min_tf, bar_count, window_size)

7. _get_bar_date_from_db(ticker_code, min_tf, 0)
   └─► SELECT ts FROM kline ... ORDER BY ts ASC LIMIT 1 OFFSET 0
   └─► AppState.set("_bt_cutoff_date", cutoff_date)

8. log_mode_switch(ticker_code, "enter", min_tf, bar_count)
9. AppState.set("_cb_mode", True)
10. st.rerun()
```

**退出回测模式** (`streamlit_app.py:1150-1165`):

```
1. 捕获退出前的 _min_tf, _min_tf_bar_count

2. AppState.set("_bar_index", 0)        ← 重置
3. AppState.set("_bt_cutoff_date", "")  ← 清空
4. AppState.set("_min_tf", "")          ← 清空
5. AppState.set("_min_tf_bar_count", 0) ← 清空

6. log_mode_switch(ticker_code, "exit", ...)
7. AppState.set("_cb_mode", False)
8. st.rerun()
```

**注意**: `_day_offset` 在模式切换时 **不会被重置**。在浏览模式下 `_day_offset=10`，切换到回测再切回来，`_day_offset` 仍是 10。这是问题 #6 (Minor)。

### 4.3 Ticker 切换时的状态处理

当前 ticker 切换逻辑在 `_handle_initial_fetch` (`streamlit_app.py:719`):

```python
if ticker_code and ticker_code != AppState.get("_fetched_ticker"):
    # 只做首次数据抓取
    ...
    AppState.set("_fetched_ticker", ticker_code)
```

**当前行为**: ticker 切换时 **不回撤任何回测状态键**。如果用户在回测模式下切换 ticker：
- `_cb_mode` 仍为 True
- `_bar_index`, `_min_tf`, `_min_tf_bar_count` 是旧 ticker 的值
- slider 可能显示无效范围
- 数据加载可能失败（新 ticker 的 bar_count 不同）

这是问题 #2 (Major)。

---

## 五、边界条件 & 异常处理

### 5.1 边界场景对比表

| 场景 | 浏览模式行为 | 回测模式行为 | 级别 |
|------|------------|------------|------|
| **parquet 文件不存在** | `_load_chart_data` 返回 err=None, 走 `_cached_fetch_stock` fallback (streamlit_app.py:158) | 同左 — 走相同 fallback 路径 | OK |
| **DB 为空 (total_bars=0)** | `_render_time_nav` 显示空日期范围 | Slider 不渲染, 显示 toast "数据未就绪" (streamlit_app.py:1145) | OK |
| **len(df) < 2** | `_load_chart_data` 返回 err="数据点不足" (streamlit_app.py:138) | 同左 | OK |
| **bar_index=0** | N/A | 窗口在最旧位置, cutoff_date=DB第(window_size-1)条日期 | OK |
| **bar_index=末尾** | N/A | `max_start = max(0, total_bars - window_size)`, slider 最大值受限 | OK |
| **ticker 切换** | 重新 fetch 数据 | **回测状态键未重置** — slider 可能越界 | Major |
| **高周期数据不足** | len(df)可能 < n_pts → 报错 | 同左 — len(df) 检查统一 | Minor |
| **day_offset 导致无数据** | `query_kline` 返回空 df | N/A (回测不用 day_offset) | OK |
| **回测模式下 day_offset 控件** | N/A | **可见但失效** — 前移/后移/步长控件不隐藏 | Major |
| **window_size > total_bars** | N/A | `max_start = max(0, ...)` → 0，slider 锁定在 0 | OK |
| **_min_tf 为空 (无有效 tf)** | N/A | `_get_min_tf_and_count` 返回 `("", 0)` → slider 不渲染 | OK |

### 5.2 异常处理汇总

| 异常点 | 处理方式 | 文件:行号 |
|--------|---------|----------|
| DB COUNT 查询失败 | `except Exception: bar_count = 0` | streamlit_app.py:446-447 |
| parquet 读取失败 | `except Exception: err = str(e)` | streamlit_app.py:154-155 |
| `_get_bar_date_from_db` 无结果 | `return row[0] if row else ""` | streamlit_app.py:1107 |
| display parquet 不存在 | 走 `_cached_fetch_stock` fallback | streamlit_app.py:158 |
| logger 调用失败 | 所有 log_* 调用均被 `try/except: pass` 包裹 | streamlit_app.py:147-149 |
| tz_localize 无时区数据 | **未防御** — 假设数据带时区 | services/ — 问题 #7 |

---

## 六、差异总结

### 6.1 Phase1 重构后已统一的点

| # | 统一项 | 说明 |
|---|--------|------|
| 1 | **数据截断** | 两种模式在 `_load_chart_data` 中都不再截断数据，parquet 已包含精确 n_pts 条 |
| 2 | **数据量** | 两个模式每个视图始终返回 n_pts 条数据 (之前回测可能多取) |
| 3 | **force_full 每帧重写** | 之前回测每次渲染重写 parquet，Phase1 修复；现在仅在 slider 拖动时写入 |
| 4 | **图表渲染管线** | `_render_chart`, `_compute_filters`, `_compute_schmitt_trigger` 等完全模式无关 |
| 5 | **parquet 写入** | 两种模式都通过 `_sync_to_display` → parquet 写入，`_load_chart_data` 统一读取路径 |
| 6 | **错误处理** | `len(df) < 2` 的检查两种模式共享 |

### 6.2 仍存在的差异（设计意图 — 合理差异）

| # | 差异项 | 浏览模式 | 回测模式 | 设计意图 |
|---|--------|---------|---------|---------|
| 1 | **数据查询 SQL** | `query_kline()` → `ORDER BY ts DESC LIMIT n_pts` | 直接 SQL → `ORDER BY ts ASC LIMIT n_pts OFFSET N` | 浏览取"最新N条"，回测取"第N条开始的N条" |
| 2 | **时间控制方式** | `day_offset` (按天数偏移) | `window_start` (按 bar 索引偏移) | 粒度不同：浏览按天，回测按 bar |
| 3 | **侧边栏控件** | 前移/后移按钮 + 步长选择 | 窗口位置 slider | 交互模式不同 |
| 4 | **跨 TF 对齐方式** | 各自独立取最新数据 | 共享 window_start (索引位置对齐) | 回测需要时间一致性 |
| 5 | **数据源路径** | 经 `query_kline()` 封装 | 直接 SQL (绕过 query_kline) | query_kline 不支持 OFFSET 参数 |
| 6 | **日志记录** | 无专用日志 | log_mode_switch, log_bar_navigation, log_data_load | 回测需要审计追踪 |
| 7 | **配置文件** | 无 | `backtest_config.json` (保存/恢复回测状态) | 回测需要状态持久化 |
| 8 | **退出时 parquet 清除** | 保留 display parquet | 退出时 parquet 可能瞬时不一致 | 回测离开后残留回测窗口的数据，下次刷新后覆盖 |

### 6.3 已知问题清单（含严重度 + 修复状态）

| # | 严重度 | 问题 | 位置 | 修复状态 |
|---|--------|------|------|---------|
| 1 | ~~Major~~ | ~~force_full 每次渲染重写 parquet~~ | data_loader.py | **Phase1 已修复** |
| 2 | **Major** | **ticker 切换时回测状态不重置** — 在回测模式下切换到不同 ticker，`_cb_mode=True`、`_bar_index`、`_min_tf_bar_count` 等状态键不变，slider 可能越界或数据异常 | streamlit_app.py:719-731, 1110-1214 | **待修复** |
| 3 | **Major** | **回测下 day_offset/n_pts 控件可见但失效** — `_render_time_nav` 的 "前移/后移/最新" 按钮和步长选择器在回测模式下仍然显示但不生效 | streamlit_app.py:1054-1096 | **待修复** |
| 4 | **Minor** | **数据管道不统一（两套 SQL）** — 浏览和回测走不同的 SQL 路径 | data_loader.py, db.py | **方案已确定，待实施** — 修复 1-1: `query_kline` 增加 offset 参数，统一 SQL 查询路径 |
| 5 | **Minor** | **高周期 bar_index 小时无数据报错** — 当 min_tf 周期的 bar_count 很小时，slider 范围计算可能出错 | streamlit_app.py:1197-1200 | **待修复** |
| 6 | **Minor** | **退出回测时 `_day_offset` 不被重置** — 退出回测模式时 `_day_offset` 保留切出前的值，不会重置为 0 | streamlit_app.py:1150-1165 | **待修复** |
| 7 | **Minor** | **`_sync_to_display` 返回值被忽略** — 调用处未检查返回的 `(success, row_count)`，写入失败无法感知 | data_loader.py:139, streamlit_app.py:122-126 | **待修复** |
| 8 | **Minor** | **退出回测时 parquet 瞬时不一致** — 退出回测回到浏览模式时，parquet 中仍是回测窗口的数据，下次 `_load_chart_data` 调用 `_sync_to_display` 才覆盖 | data_loader.py:139, streamlit_app.py:1150-1165 | **待修复** |
| 9 | **Minor** | **参数语义不统一** — 浏览用 `day_offset` (天数偏移), 回测用 `window_start` (行号偏移), 两个参数并存但互斥 | streamlit_app.py:115-126 | **方案已确定，待实施** — 修复 1-2: `_load_chart_data` 只保留 `window_start` |
| 10 | **Minor** | **API 回退路径不截断** — parquet 不存在时回退到 yfinance API，两个模式都没有在此路径上应用窗口截断 | streamlit_app.py:158-163 | **方案已确定，待实施** — 修复 1-3: API 回退分支增加窗口截断 |

### 6.4 代码位置索引

| 功能 | 文件 | 行号 |
|------|------|------|
| `_load_chart_data` (数据加载入口) | streamlit_app.py | 115-158 |
| `_render_backtest_mode` (回测 UI) | streamlit_app.py | 1110-1214 |
| `_render_time_nav` (时间导航) | streamlit_app.py | 1054-1096 |
| `_get_min_tf_and_count` (最小周期计算) | streamlit_app.py | 413-449 |
| `_get_bar_date_from_db` (bar 日期查询) | streamlit_app.py | 1099-1107 |
| `_save_backtest_config` (保存配置) | streamlit_app.py | 452-465 |
| `_load_backtest_config` (加载配置) | streamlit_app.py | 468-478 |
| `_sync_to_display` (数据同步) | services/data_loader.py | 139-172 |
| `query_kline` (K线查询) | services/db.py | 86-117 |
| `get_conn` (DB 连接) | services/db.py | 19-26 |
| `kline` 表结构 | services/db.py | 34-41 |
| `SYSTEM_KEYS` (回测状态键) | services/state.py | 42-47 |
| `AppState.init_defaults` | services/state.py | 106-112 |
| `log_mode_switch` | services/backtest_logger.py | 32-41 |
| `log_bar_navigation` | services/backtest_logger.py | 44-54 |
| `log_data_load` | services/backtest_logger.py | 57-66 |
| `main` (渲染调度) | streamlit_app.py | 1386-1473 |

---

## 七、分析过程记录

本报告经过以下分析阶段逐步构建：

| 阶段 | 内容 | 产出 |
|------|------|------|
| **T14** | 逐行对比两种模式的完整代码路径 | 初次发现 force_full 缺陷、数据截断差异、SQL 路径不同等问题 |
| **T15** | 5 维度系统化分析 + 问题分类 | 发现 8 个已知问题（含严重度）、设计意图 vs 实现缺陷分类、5 维度完整对比表 |
| **T18** | Phase1 重构后回归对比 | 确认 force_full 修复、数据量统一、渲染管线统一；标记仍存在的合理差异 |
| **本报告** | 合并 T14→T15→T18 的完整输出 | 统一差异报告，含完整数据流图、问题清单、代码索引 |

### 分析维度覆盖

本次分析覆盖了 5 个完整维度：

1. **数据读取** — `_sync_to_display` 的两种 SQL 路径、parquet 写入量、数据源链路
2. **数据显示** — 数据量、时间范围、4 视图一致性、图表渲染管线
3. **数据显示设置** — 侧边栏控件、slider 行为、配置文件
4. **状态管理** — 状态键对比、模式切换转换、ticker 切换处理
5. **边界条件 & 异常处理** — parquet 不存在、DB 为空、len(df)<2、bar_index 边界、tz_localize 防御

---

## 附录: 完整数据流对比图

```
═══════════════════════════════════════════════════════════════════════════════
                      浏览模式 (Browse Mode) vs 回测模式 (Backtest Mode)
                              完整数据流对比
═══════════════════════════════════════════════════════════════════════════════

┌─ 上游数据 ─────────────────────────────────────────────────────────────────┐
│                                                                             │
│  [两者相同]                                                                 │
│  yfinance API ──► _fetch_stock() ──► upsert_kline() ──► SQLite kline 表     │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
┌─ 浏览模式 ────────────────────┐   ┌─ 回测模式 ────────────────────┐
│                                │   │                                │
│  用户操作:                      │   │  用户拖动 slider:              │
│  前移/后移按钮, day_step 选择   │   │  _bt_bar_slider               │
│      │                         │   │      │                         │
│      ▼                         │   │      ▼                         │
│  _load_chart_data(             │   │  _load_chart_data(             │
│    day_offset=N,               │   │    window_start=N,             │
│    window_start=None)          │   │    day_offset=忽略)             │
│      │                         │   │      │                         │
│      ▼                         │   │      ▼                         │
│  _sync_to_display(             │   │  _sync_to_display(             │
│    day_offset, n_pts)          │   │    n_pts, window_start)        │
│      │                         │   │      │                         │
│      ▼                         │   │      ▼                         │
│  query_kline()                 │   │  直接 SQL:                     │
│  SELECT ... FROM kline         │   │  SELECT ... FROM kline         │
│  WHERE ts <= cutoff            │   │  ORDER BY ts ASC               │
│  ORDER BY ts DESC              │   │  LIMIT n_pts OFFSET N          │
│  LIMIT n_pts                   │   │      │                         │
│      │                         │   │      ▼                         │
│      ▼                         │   │  iloc[::-1] (时间升序)         │
│  iloc[::-1] (时间升序)         │   │      │                         │
│      │                         │   │                                │
│      ▼                         │   │      ▼                         │
│  [共同路径]                     │   │  [共同路径]                     │
│  to_parquet(data/display/)     │   │  to_parquet(data/display/)     │
│      │                         │   │      │                         │
│      ▼                         │   │      ▼                         │
│  pd.read_parquet()             │   │  pd.read_parquet()             │
│      │                         │   │      │                         │
│      ▼                         │   │      ▼                         │
│  len(df) < 2 检查              │   │  len(df) < 2 检查              │
│      │                         │   │      │                         │
│      ▼                         │   │      ▼                         │
└──────┬─────────────────────────┘   └──────┬─────────────────────────┘
       │                                    │
       └──────────────┬─────────────────────┘
                      ▼
┌─ 下游渲染 (两者相同) ───────────────────────────────────────────────────────┐
│                                                                             │
│  _compute_filters() ──► _compute_schmitt_trigger()                          │
│      ──► _compute_prediction_pairs() ──► _compute_strategy_display()        │
│      ──► plotly Figure ──► st.plotly_chart()                                │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

═══════════════════════════════════════════════════════════════════════════════
                              核心差异点 (3 处)
═══════════════════════════════════════════════════════════════════════════════

  ┌──────────────────────┬─────────────────────┬──────────────────────┐
  │       维度           │      浏览模式        │      回测模式         │
  ├──────────────────────┼─────────────────────┼──────────────────────┤
  │ SQL 查询             │ query_kline() 封装   │ 直接 SQL (不同路径)   │
  │ 时间控制             │ day_offset (天数)    │ window_start (bar索引) │
  │ UI 控件              │ 前移/后移按钮        │ slider               │
  └──────────────────────┴─────────────────────┴──────────────────────┘
```
