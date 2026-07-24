# 回测窗口数据加载链路分析

分析日期: 2026-07-05
源代码:
- `/Users/xfpan/claude/filter_research/filter/services/data_loader.py`
- `/Users/xfpan/claude/filter_research/filter/streamlit_app.py`
- `/Users/xfpan/claude/filter_research/filter/db.py`

---

## 1. 全链路数据流向图 (ASCII Art)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           main() [streamlit_app.py]                     │
│  _run_backtest_play() → 更新 bar_index / cutoff_date                    │
│  _render_chart_fragment() → dispatch to per-view render                 │
└──────────────────┬───────────────────────────────────────────────────────┘
                   │ pass (window_start, cutoff_date)
                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  _render_chart()                                                        │
│  Step1: _load_chart_data() ─── 入口点                                   │
│  Step2-11: 滤波 / 施密特 / PnL / Plotly 图表构建                        │
└──────────────────┬───────────────────────────────────────────────────────┘
                   │ window_start=bar_index, cutoff_date=YYYY-MM-DD
                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  _load_chart_data()                                                     │
│                                                                         │
│    window_start → None?                                                │
│    ┌─── YES ───────────────────────────┐   ┌─── NO ──────────────────┐  │
│    │ 浏览模式                           │   │ 回测模式                │  │
│    │ _sync_to_display(                  │   │ _sync_to_display(       │  │
│    │   n_pts=n_pts,                     │   │   n_pts=n_pts,          │  │
│    │   day_offset=day_offset,           │   │   cutoff_date=date)     │  │
│    │   cutoff_date=None)                │   │                         │  │
│    └──────────────┬─────────────────────┘   └────────┬────────────────┘  │
│                   │                                   │                  │
│                   ▼                                   ▼                  │
│    ┌──────────────────────────────┐  ┌──────────────────────────────┐   │
│    │ _sync_to_display (浏览模式)   │  │ _sync_to_display (回测模式)   │   │
│    │                              │  │                              │   │
│    │ query_kline(code, tf, n_pts, │  │ SELECT ... FROM kline        │   │
│    │   day_offset=day_offset)     │  │   WHERE ts <= cutoff_date    │   │
│    │ → DB: SELECT MAX(ts)         │  │   ORDER BY ts DESC LIMIT n   │   │
│    │ → DB: SELECT ... ts <= cutoff│  │ → rows.reverse() → ASC       │   │
│    │ → DESC LIMIT n_pts           │  │ → pd.DataFrame               │   │
│    │ → df.iloc[::-1] (reverse)    │  │ → df.to_parquet(tf.parquet)  │   │
│    │ → df.to_parquet(tf.parquet)  │  │                              │   │
│    └──────────────┬───────────────┘  └──────────────┬───────────────┘   │
│                   │                                   │                  │
│                   └──────────┬────────────────────────┘                  │
│                              ▼                                          │
│    ┌─────────────────────────────────────────────────────────────┐      │
│    │ 读取 parquet → pd.read_parquet("data/display/{tf}.parquet") │      │
│    │ → df.set_index("Date").sort_index()                        │      │
│    │ → t = np.arange(len(df))                                   │      │
│    │ → noisy = df["Close"].values                               │      │
│    │ → ohlc = df[["Open","High","Low","Close"]]                 │      │
│    │ → return (t, noisy, ohlc, ticker_code, df.index, None)     │      │
│    └─────────────────────────────────────────────────────────────┘      │
│                              │                                          │
│                              ▼                                          │
│    ←(回退) parquet 不存在/失败 → _cached_fetch_stock() API 回退         │
│                                                                         │
└──────────────────────────────────┬───────────────────────────────────────┘
                                   │ return  (t, noisy, ohlc, ticker_full, dates, err)
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  _render_chart() 下游链路                                                │
│                                                                         │
│  filtered, filtered2 = _compute_filters(noisy, t, cfg)                 │
│  schmitt = _compute_schmitt_trigger(filtered, t, cfg)                   │
│  all_pairs = _find_all_pairs(schmitt["sig"])                            │
│  pred_pairs = _compute_prediction_pairs(...)                            │
│  long_pnl, short_pnl = _compute_strategy_display(...)                  │
│                                                                         │
│  fig = make_subplots(...)                                               │
│  _add_main_price_traces(fig, t, noisy, ohlc, filtered, ...)            │
│  _add_residual_traces(fig, t, filtered, noisy, ...)                     │
│  _add_schmitt_traces(fig, t, schmitt, acc, all_pairs, ...)             │
│  _add_pnl_traces(fig, t, long_pnl, short_pnl, ...)                     │
│  _render_plotly(fig)                                                    │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 2. `_sync_to_display()` -- 核心数据同步函数

**文件**: `/Users/xfpan/claude/filter_research/filter/services/data_loader.py:139-173`

这是数据从 SQLite 到 Parquet 缓存的桥梁。有两个截然不同的分支:

### 2.1 浏览模式 `cutoff_date=None` (第 165-173 行)

```
_sync_to_display(ticker_code, tf, day_offset, n_pts, cutoff_date=None)
```

1. 调用 `query_kline(ticker_code, tf, n_pts, day_offset=day_offset)`  → `db.py:86-133`
2. `query_kline()` 内部:
   - 先 `SELECT MAX(ts)` 获取最新日期
   - 如果有 `day_offset>0`, 计算 `datetime(MAX(ts), '-N days')` 作为截止
   - `SELECT ... WHERE ts <= cutoff ORDER BY ts DESC LIMIT n_pts`
   - 结果 `df.iloc[::-1]` 反转成时间升序
3. 检查 `len(df) < 5` → 返回 `(False, len(df))`
4. `df["Date"] = pd.to_datetime(df["Date"])`
5. 写入 `data/display/{tf}.parquet` (index=False)
6. 返回 `(True, len(df))`

**关键点**: 浏览模式的 parquet 始终包含的是**从最新 N 条数据窗口** (或 day_offset 偏移后的窗口)。parquet 只含最新窗口，不是全量数据。

### 2.2 回测模式 `cutoff_date=YYYY-MM-DD` (第 146-163 行)

```
_sync_to_display(ticker_code, tf, n_pts, day_offset=0, cutoff_date="2024-01-15")
```

1. 直接通过 `get_conn()` 执行原始 SQL，不使用 `query_kline()` 函数:
```python
SELECT ts, open, high, low, close, volume
FROM kline WHERE ticker=? AND timeframe=? AND ts <= ?
ORDER BY ts DESC LIMIT ?
```
2. 参数: `(ticker_code, tf, cutoff_date, n_pts)`
3. `rows.reverse()` -- 将 DESC 结果反转为时间升序
4. 构造 `pd.DataFrame(rows, columns=["Date","Open","High","Low","Close","Volume"])`
5. 写入 `data/display/{tf}.parquet` (index=False)
6. 返回 `(True, len(df))`

**关键差异**: 回测模式绕过了 `query_kline()` 函数，直接在数据层用原始 SQL 按 `ts <= cutoff_date` 过滤。这意味着:
- 查询条件更精确: `ts <= cutoff_date` 是按日期字符串比较
- 不需要 `day_offset` 概念 (回测时始终是 0)
- parquet 缓存仅包含截止日期前的数据窗口
- **parquet 是按时间范围**而不是按偏移量写入的

### 2.3 两个模式的重要设计差异

| 特性 | 浏览模式 | 回测模式 |
|------|---------|---------|
| DB 查询方式 | `query_kline()` 函数 | 原始 SQL |
| 日期过滤 | `ts <= MAX(ts) - day_offset` | `ts <= cutoff_date` |
| day_offset 支持 | 是 | 否 (固定 0) |
| 结果排序 | `DESC LIMIT` + `iloc[::-1]` | `DESC LIMIT` + `rows.reverse()` |
| parquet 包含 | 从最新(或偏移后)的 N 条 | cutoff_date 前的 N 条 |
| 数据不足阈值 | `< 5` | 无检查 (直接用 `if rows`) |

---

## 3. `_load_chart_data()` -- 图表数据加载

**文件**: `/Users/xfpan/claude/filter_research/filter/streamlit_app.py:115-172`

这是 `_render_chart()` → `_sync_to_display()` 之间的中间层。负责:
- 调用 `_sync_to_display()` 写入 Parquet
- 回退到 API (`_cached_fetch_stock`)
- Parquet 读取 → DataFrame → numpy 转换

### 3.1 回测模式分支 (第 121-134 行)

```python
if window_start is not None:
    ok, count = _sync_to_display(ticker_code, tf, n_pts=n_pts, cutoff_date=cutoff_date)
    if not ok:
        # fallback: API 直调 + 截断
        t, noisy, ohlc, ticker_full, dates, err = _cached_fetch_stock(...)
        if err is None and dates is not None and len(dates) > n_pts:
            t = t[-n_pts:]; noisy = noisy[-n_pts:]; ohlc = ohlc.iloc[-n_pts:]; dates = dates[-n_pts:]
        return ...
```

- 当 `_sync_to_display()` 返回 `(False, 0)` 时 (DB 无该时间区间数据)
- API 回退路径包含截断到 n_pts 的逻辑 (用于浏览模式也有)
- **回退只发生在 `_sync_to_display` 失败时**, 正常情况走 parquet 读取

### 3.2 浏览模式分支 (第 135-140 行)

```python
else:
    ok, count = _sync_to_display(ticker_code, tf, day_offset=day_offset, n_pts=n_pts)
    if not ok:
        return _cached_fetch_stock(market, ticker_code, tf, n_pts)
```

- 浏览模式失败后直接返回 API 结果, 没有 n_pts 截断逻辑
- API 回退是**完整返回**, 因为 `_fetch_stock()` 内已做了 LIMIT n_pts

### 3.3 Parquet 读取与转换 (第 141-171 行)

```python
display_path = Path(__file__).parent.parent / "data" / "display" / f"{tf}.parquet"
df = pd.read_parquet(display_path)
df["Date"] = pd.to_datetime(df["Date"])
df = df.set_index("Date").sort_index()

t = np.arange(len(df), dtype=float)                    # [0, 1, 2, ..., n-1]
noisy = df["Close"].values.ravel()                      # float64 array
ohlc = df[["Open", "High", "Low", "Close"]]             # DataFrame
```

**核心转换步骤**:
1. `pd.read_parquet()` → DataFrame with columns: Date, Open, High, Low, Close, Volume
2. `pd.to_datetime(df["Date"])` → 日期类型标准化
3. `set_index("Date").sort_index()` → 时间索引升序排列
4. `t = np.arange(len(df), dtype=float)` → x 轴坐标 (整数索引, 不是时间戳!)
5. `noisy = df["Close"].values.ravel()` → 收盘价序列
6. `ohlc = df[["Open","High","Low","Close"]]` → K 线原始数据

**返回值结构** (第 165 行):
```python
return (t, noisy, ohlc, ticker_code, df.index, None)
#  t: ndarray(float)           — x 轴索引 [0..n-1]
#  noisy: ndarray(float)       — 收盘价
#  ohlc: DataFrame             — Open/High/Low/Close 四列
#  ticker_code: str            — 股票代码
#  df.index: DatetimeIndex     — 时间索引 (用于日期标记)
#  err: None                   — 无错误
```

**注意**: `t` 是简单的整数索引 `arange(len(df))`，不是时间戳。图表上的日期标记靠 `_date_markers()` 从 `df.index` 提取位置和标签。这意味着 x 轴是均匀索引而非真实时间轴。

---

## 4. 回测模式核心状态机

### 4.1 模式切换 (`_render_backtest_mode`, 第 1267-1363 行)

**浏览 → 回测** 转换流程:

```
1. _load_backtest_config(ticker_code) → 尝试从 data/backtest_config.json 加载缓存
2. 缓存命中: 取 cached["min_tf"], cached["bar_count"]
3. 缓存未命中: _get_min_tf_and_count(configs, ticker_code) → 从 DB 查询
4. AppState 设置:
   - _min_tf = 最精细周期 (如 "5分钟")
   - _min_tf_bar_count = DB 中该周期总 bar 数
   - _bar_index = bar_count (默认在末尾)
   - st.session_state._bar_index = bar_count (同步 widget key)
5. min_n_pts = min(cfg["n_pts"] for cfg in configs if cfg["tf"] == min_tf)
6. 如果没有缓存: _save_backtest_config() 写入 JSON
7. cutoff_date = _get_bar_date_from_db(ticker_code, min_tf, bar_count-1)
8. st.toast("回测模式已启用")
```

**回测 → 浏览** 转换流程:

```
1. 捕获退出前的回测参数 (_exit_min_tf, _exit_bar_count)
2. 清除回测状态:
   - _bar_index = 0
   - _bt_cutoff_date = ""
   - _min_tf = ""
   - _min_tf_bar_count = 0
3. AppState._cb_mode = False
4. log_mode_switch(ticker_code, "exit", ...)
5. st.rerun()
```

### 4.2 `_get_min_tf_and_count()` (第 427-463 行)

```python
def _get_min_tf_and_count(configs, ticker_code) -> tuple:
```

- **确定最精细周期**: 遍历 4 个视图的 `tf` 配置, 取 `ALL_TFS` 列表中索引最小的
  - `ALL_TFS = ["1分钟","5分钟","15分钟","60分钟","日线","周线","月线","季线"]`
  - 例如: 视图为 ["日线", "60分钟", "周线", "月线"], 最精细是 "60分钟"
- **查询总 bar 数**: `SELECT COUNT(*) FROM kline WHERE ticker=? AND timeframe=?`
  - 直接从 DB 查, 而不是从 parquet (parquet 只有窗口数据)
- **为什么是全量 bar 数**: slider 的上限是 `total_bars`, 需要知道 DB 中该周期总共有多少条记录

### 4.3 `bar_index` / `cutoff_date` 的关系

```python
# bar_index 是回测窗口的"结束位置"索引 (从 1 开始)
# cutoff_date 是 bar_index-1 位置 bar 的时间戳

def _get_bar_date_from_db(ticker_code, tf, bar_index):
    # SELECT ts FROM kline ... ORDER BY ts ASC LIMIT 1 OFFSET ?
    # bar_index-1: 因为 bar_index 从 1 开始, OFFSET 从 0 开始
    ...
```

**关键等式**:
- `bar_index` = 窗口在 min_tf 时间轴上的结束位置
- `cutoff_date` = `bar_index - 1` 位置 bar 的 `ts` 值
- `win_start_display = max(1, bar_index - min_n_pts + 1)` — 窗口起始显示位置
- slider 范围: `[min_n_pts, total_bars]`

**导航按钮逻辑**:
| 按钮 | 操作 | 边界条件 |
|------|------|---------|
| `⏮` | `_bar_index = min_n_pts` | 窗口能显示的最早位置 |
| `◀` | `_bar_index = max(min_n_pts, bar_index-1)` | 不能 < min_n_pts |
| `▶` | `_bar_index = min(total_bars, bar_index+1)` | 不能 > total_bars |
| `⏭` | `_bar_index = total_bars` | 末尾 |

每次导航后调用 `_update_cutoff_and_rerun()`:
1. 同步 `st.session_state._bt_slider_pos = bar_index`
2. 从 DB 查询 `_get_bar_date_from_db(ticker, min_tf, bar_index - 1)`
3. 更新 `AppState._bt_cutoff_date`
4. `st.rerun()`

### 4.4 `backtest_config.json` 缓存机制

```python
# 文件: data/backtest_config.json
# 结构:
{
    "ticker": "AAPL",
    "min_tf": "60分钟",
    "bar_count": 1234,
    "window_size": 120,
    "cached_at": "2026-07-05T12:00:00"
}
```

- **写入时机**: 从浏览首次切换到回测时 (缓存未命中)
- **读取时机**: 切换到回测时优先读缓存 + ticker 切换时也读缓存
- **匹配条件**: `config.get("ticker") == ticker_code` — 严格 ticker 匹配
- **目的**: 避免每次切换回测都重新遍历配置和查询 DB 计数
- **限制**: 不验证 bar_count 是否最新 (ticker 不变时复用), 需要手动刷新

### 4.5 Slider 导航与自动播放的协作

**Slug 机制**: Slider 使用独立 widget key `_bt_slider_pos` + `on_change=_on_slider_change`, 避免与程序状态 `_bar_index` 直接绑定导致的 Streamlit widget 异常。

**_on_slider_change()** (第 1145-1158 行):
- 播放期间跳过 (`_is_playing=True`)
- 从 Widget Key 读取 `st.session_state._bt_slider_pos`
- 同步到 `st.session_state._bar_index`
- 更新 `cutoff_date`

**_run_backtest_play()** (第 1225-1264 行):
- 在 `main()` 第一行调用 (widget 渲染前修改 key)
- 安全守卫: `_cb_mode=False` 或 `total<=0` 或 `bar_index>=total` → 停止播放
- 前进: `st.session_state._bar_index = bar_index + 1`
- 更新 cutoff_date: `_get_bar_date_from_db(ticker, min_tf, bar_index)`
- 返回 `True` → `main()` 末尾 `time.sleep(1.0/speed)` + `st.rerun()`

**播放时进度显示** (第 1348-1350 行):
```python
progress = (bar_index - min_n_pts) / max(1, total_bars - min_n_pts)
st.sidebar.progress(progress, text=f"播放中... {bar_index}/{total_bars}")
```

**非播放时 Slider** (第 1356-1361 行):
```python
st.sidebar.slider("窗口结束位置", min_n_pts, total_bars,
    value=bar_index, key="_bt_slider_pos", on_change=_on_slider_change)
```

**设计约束**: 播放期间不能用 Slider (因为 `st.session_state._bar_index` 每秒被 `_run_backtest_play` 递增, 而 Slider 的 `on_change` 冲突)。播放时隐藏 Slider, 改用 `st.progress` 进度条。

---

## 5. 从窗口到图表: 完整渲染流水线

### 5.1 回测模式下的全链路示例 (AAPL, 60分钟 视图)

```
main() ─┐
        ├─ _run_backtest_play() → bar_index=800, cutoff_date="2024-06-15"
        │
        ├─ _render_chart_fragment(market, ticker_code, cfg=v0, 
        │      window_start=800, cutoff_date="2024-06-15")
        │   └─ _render_chart()
        │       └─ _load_chart_data(tf="60分钟", n_pts=120, 
        │              window_start=800, cutoff_date="2024-06-15")
        │
        │          1. _sync_to_display("AAPL", "60分钟", 
        │               n_pts=120, cutoff_date="2024-06-15")
        │             → SQL: SELECT ... WHERE ticker='AAPL' AND timeframe='60分钟' 
        │                     AND ts <= '2024-06-15' ORDER BY ts DESC LIMIT 120
        │             → rows.reverse()
        │             → pd.DataFrame → data/display/60分钟.parquet
        │
        │          2. pd.read_parquet("data/display/60分钟.parquet")
        │             → df.set_index("Date").sort_index()
        │             → t = arange(120), noisy = Close[0..119]
        │             → ohlc = df[["Open","High","Low","Close"]]
        │             → return (t, noisy, ohlc, "AAPL", dates, None)
        │
        └─ _render_chart() 继续:
              → _compute_filters(noisy, t, cfg)       → filtered, filtered2
              → _compute_schmitt_trigger(filtered, t)  → schmitt dict
              → _find_all_pairs(schmitt["sig"])         → all_pairs
              → _compute_prediction_pairs(...)          → pred_pairs
              → _compute_strategy_display(...)          → long_pnl, short_pnl
              → make_subplots(rows, ...)
              → [add traces] → _render_plotly(fig)
```

### 5.2 性能关键点

| 阶段 | 操作 | 性能特征 |
|------|------|---------|
| `_sync_to_display` 回测 | SQL 查询 + parquet 写 | DB I/O + 文件 I/O |
| `_sync_to_display` 浏览 | `query_kline()` + parquet 写 | DB I/O + 文件 I/O |
| Parquet 读 | `pd.read_parquet` | 文件 I/O, mmap 优化 |
| 滤波计算 | numpy 数组运算 | CPU 密集 |
| Plotly 渲染 | 前端 SVG/Canvas | 渲染引擎 |

**每次 slider 移动 / 导航按钮点击**, 整个 4 视图的链路完整执行一次。回测模式下每个视图都是独立的 `st.fragment` (第 502 行 `@st.fragment` 装饰器), 支持独立 rerun。

---

## 6. 潜在问题与边界情况

### 6.1 时区与日期对齐

回测模式中 `ts <= cutoff_date` 是字符串比较。如果 DB 中 `ts` 列存储的是含时区的时间戳 (如 `"2024-06-15 16:00:00+08:00"`), 而 `cutoff_date` 是无时区的日期字符串 (如 `"2024-06-15"`), SQLite 的字符串比较可能产生意外结果。

### 6.2 不同视图的时间线不一致

- `min_tf` 确定最精细周期, 但 `cutoff_date` 是从 `min_tf` 表查询的
- 其他视图 (粗周期) 使用同一个 `cutoff_date` 过滤: `ts <= cutoff_date`
- 这可能导致粗周期的窗口大小与 n_pts 不完全一致 (因为 `cutoff_date` 对粗周期可能只包含很少的 bar)

### 6.3 Parquet 竞争条件

多个视图同时写入 `data/display/{tf}.parquet` 时可能存在竞争。由于 4 个视图通常使用不同的 `tf`, 理论上不会冲突。但浏览/回测模式快速切换时, 同一 `tf` 的 parquet 可能被覆盖。

### 6.4 缓存一致性

- `backtest_config.json` 不验证数据新鲜度
- ticker 不变时, 即使 DB 有新的数据插入, bar_count 也不会自动更新
- 需要手动切换回浏览再切回回测才能刷新

### 6.5 Plotly x 轴的非时间性

- `t = np.arange(len(df), dtype=float)` — x 轴是整数索引, 不是时间戳
- 日期标签通过 `_date_markers()` 从 `dates` 提取位置 (`_date_markers` 根据 tf 类型有不同的标记策略)
- 这意味着非均匀时间间隔的 bar 在图上显示为均匀间距

