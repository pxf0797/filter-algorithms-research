# 回测模式完整设计规格

> **文档性质**：可执行设计规格，开发者逐章阅读后即可动手编码。
> **源文件**：基于 `filter_app/streamlit_app.py` (feature/backtest-analysis 分支)
> **上游输入**：T1 接口分析 + T2 数据模型 + T3 UI 设计

---

## 第 1 章：概述与目标

### 1.1 回测模式的核心价值

回测模式让用户站在历史的任意时刻"向前看"，模拟那个时刻真实可见的数据，验证滤波器/策略在当时的信号质量。

**与简单回看历史数据的本质区别**：
- 历史数据回看 = 事后诸葛亮，所有数据一次性可见，存在前视偏差
- 回测模式 = 站在 `bar_index` 时刻，严格屏蔽未来数据，每个 bar_index 的计算结果仅依赖该时刻可见的数据

### 1.2 与浏览模式的关系

| 维度 | 浏览模式 | 回测模式 |
|------|---------|---------|
| 图表渲染 | 2×2 网格（日线/60分钟/15分钟/5分钟） | 2×2 网格（完全相同） |
| K 线 + 滤波 | 全量数据显示 | 窗口切片（≤n_pts 根 bar），渲染方式一致 |
| 时间视角 | 始终看到最新数据（含 day_offset） | 站在历史的某个时刻往前看 |
| 数据窗口 | `day_offset` 控制起点，始终显示最新 n_pts 根 bar | `bar_index` 控制窗口终点，严格屏蔽 bar_index 之后的数据 |
| 高周期数据 | 从 Parquet 直读，最后一根可能已完成 | 可能需要合成未完成的高周期 bar |
| 预测显示 | 显示全部预测 | 只显示 bar_index 之前的预测对 |
| 时间导航 | day_offset + 前后按钮（侧边栏） | 隐藏 day_offset 控件；bar_index slider + 播放控件（主区域控制条） |
| 控制条 | 无 | ⏮ ◀ ▶/⏸ ▶▶ ⏭ + 速度选择 + 进度 slider |
| 滤波/触发计算 | `_compute_filters` + `_compute_schmitt_trigger`（全量计算） | 同一函数（数据子集不同但算法一致） |
| 数据源 | `data/display/{tf}.parquet` | 同一套 Parquet 文件 |

### 1.3 设计原则

**原则 1：满 bar 原则**
每个视图始终尝试显示 `n_pts` 根 bar。边界不足时显示实际数量（而非填充 NaN），因为"站在 bar_index 时刻"确实只能看到那么多历史。

**原则 2：数据源统一**
回测模式和浏览模式使用完全相同的 Parquet 数据源（`data/display/{tf}.parquet`），不引入额外的数据文件或格式。

**原则 3：纯视觉差异**
回测模式与浏览模式的区别仅在数据窗口截取方式，滤波计算、施密特触发、策略 PnL 等核心算法不变。`bar_index` 参数是唯一的模式分叉点。

**原则 4：单一时钟源**
所有 4 个视图共享同一个 `bar_index`（始终对应 min_tf 的第几根 bar），通过 `cutoff_date = min_tf_dates[bar_index]` 派生各自周期的截断位置，保证多视图时间一致性。

---

## 第 2 章：数据模型

### 2.1 窗口平移算法

#### 2.1.1 算法核心

```
输入:
  tf          : 目标周期 (来自 ALL_TFS 的中文名，如 "日线", "60分钟", "周线")
  bar_index   : 当前 min_tf bar 索引，整数 ∈ [0, total_bars-1]
  n_pts       : 窗口大小（bar 数量），来自对应视图的 cfg["n_pts"]
  min_tf_dates: min_tf 完整日期数组 (pd.DatetimeIndex)，已排序
  df          : tf 的完整 DataFrame，index 为 DatetimeIndex，已排序

输出:
  df_slice    : 窗口切片 DataFrame，长度 ≤ n_pts，含 is_synthesized 列
  meta        : 字典 {start_idx, end_idx, cutoff_idx, is_partial,
                      is_synthesized_last, cutoff_date, slice_length}
```

#### 2.1.2 伪代码

```
function window_slice(tf, bar_index, n_pts, min_tf_dates, df):
    // Step 1: 确定窗口终点锚点
    cutoff_date = min_tf_dates[bar_index]

    // Step 2: 在 tf 数据中找到 ≤ cutoff_date 的最大索引
    cutoff_idx = binary_search_le(df.index, cutoff_date)

    // Step 3: 若 cutoff_idx == -1，返回空切片
    if cutoff_idx == -1:
        return empty_dataframe(), {is_partial: True, slice_length: 0, ...}

    // Step 4: 计算窗口起点
    end_idx   = cutoff_idx
    start_idx = max(0, end_idx - n_pts + 1)

    // Step 5: 切片并标记
    df_slice = df.iloc[start_idx : end_idx + 1].copy()
    df_slice["is_synthesized"] = False
    is_partial = len(df_slice) < n_pts

    return df_slice, {start_idx, end_idx, cutoff_idx, is_partial, ...}
```

#### 2.1.3 边界场景完整表

| # | 场景 | 条件 | 日线行为 | 周线/月线/季线行为 | 处理策略 |
|---|------|------|---------|-------------------|---------|
| 1 | 回测起点 | `bar_index = 0` | cutoff_idx=0, start_idx=0, 长度=1, is_partial=true | 可能 cutoff_idx=-1，返回空切片 | UI 显示实际数据，左侧留空（方案 A） |
| 2 | 不充分历史 | `0 < bar_index < n_pts - 1` | cutoff_idx=bar_index, start_idx=0, 长度=bar_index+1, is_partial=true | cutoff_idx 可能 < n_pts-1，is_partial=true | 同方案 A，早期历史自然不足 |
| 3 | 恰好满窗口 | `bar_index = n_pts - 1` | end_idx=n_pts-1, start_idx=0, 长度=n_pts, is_partial=false | 取决于高周期总 bar 数 | 第一个"满窗口"时刻 |
| 4 | 常规中段 | `n_pts-1 < bar_index < total_bars-1` | start_idx=bar_index-n_pts+1, 长度=n_pts, is_partial=false | 高周期可能因为总 bar 数不足而 is_partial | 标准行为 |
| 5 | 回测终点 | `bar_index = total_bars - 1` | 等价于 day_offset=0 的浏览模式 | 等价于各周期 day_offset=0 | 终点行为与浏览模式一致 |
| 6 | 高周期总 bar 不足 | `tf total_bars < n_pts` | 不适用 | 任何 bar_index 下 is_partial 恒为 true | 显示全部可用 bar |
| 7 | 高周期数据尚未出现 | `cutoff_idx = -1` | 不适用 | 回测极早期，高周期无任何 bar ≤ cutoff_date | 返回空切片，图表显示空白或提示 |
| 8 | min_tf 数据为空 | `total_bars = 0` | 全部视图无数据 | 全部视图无数据 | 控制条显示 disabled 按钮 + 警告 |

#### 2.1.4 方案选型说明

针对边界场景 1-2 的部分窗口问题：
- **方案 A（推荐）**：显示实际 bar 数，左侧留空。保持时间轴真实性。
- **方案 B**：填充 NaN 使长度恒为 n_pts。制造虚假数据。
- **方案 C**：禁止数据不足时进入回测。限制可用范围。

**采用方案 A**。UI 层可通过 `is_partial` meta 信息在图表中给出视觉提示（如左侧灰色遮罩），但不影响数据计算。

### 2.2 高周期合成算法

#### 2.2.1 触发条件

高周期合成仅在以下条件**全部**满足时触发：

```
1. tf != min_tf                           // 仅高周期需要合成
2. slice_length > 0                       // 窗口非空
3. cutoff_date != get_period_boundary(tf, cutoff_date, min_tf_dates)
                                          // cutoff_date 不是该周期的边界日期
4. len(synth_data) > 0                    // 有新的低周期数据可合成
```

#### 2.2.2 周期边界定义

| 周期 | 理论边界规则 | 实际边界算法 |
|------|-------------|-------------|
| 日线 | 当日 | `actual_boundary = date` |
| 周线 | 本周五 | `calendar: date + (4 - date.weekday())` → 找 ≤ 此日期的最大交易日 |
| 月线 | 本月最后一天 | `calendar: last_day_of_month(date)` → 找 ≤ 此日期的最大交易日 |
| 季线 | 本季最后一天 | `calendar: last_day_of_quarter(date)` → 找 ≤ 此日期的最大交易日 |
| 年线 | 12/31 | `calendar: date.replace(month=12, day=31)` → 找 ≤ 此日期的最大交易日 |

**关键**：实际边界是"≤ 理论边界的最大交易日"，处理节假日的边界偏移。

#### 2.2.3 OHLC 合成规则

```
设 synth_data = lower_df[(lower_df.index > last_complete_date) & (lower_df.index <= cutoff_date)]

synthetic_bar = {
    "Open":   synth_data["Open"].iloc[0],      // 期间第一个交易日的开盘价
    "High":   synth_data["High"].max(),          // 期间最高价
    "Low":    synth_data["Low"].min(),           // 期间最低价
    "Close":  synth_data["Close"].iloc[-1],      // 期间最后一个交易日的收盘价
    "Volume": synth_data["Volume"].sum(),         // 期间成交量累加（如 Parquet 有此列）
    "date":   cutoff_date,                        // 以 cutoff_date 为 bar 的日期标签
    "is_synthesized": True                       // 合成标记
}
```

#### 2.2.4 边界切换：替换 vs 追加

| 情况 | 判断条件 | 操作 | 窗口长度变化 |
|------|---------|------|-------------|
| 同一周期内 | `belongs_to_same_period(tf, last_complete_date, cutoff_date) = True` | 替换 `df_slice.iloc[-1]` | 不变 |
| 新的不完整周期 | `belongs_to_same_period(tf, last_complete_date, cutoff_date) = False` | 追加到末尾，若超出 n_pts 则丢弃最早一根 | +1 或不变（trim 后） |

**同一周期内的示例**：周线视图，上一根完整周线是周一（本周第一天的日线），cutoff_date 是周三，则合成 bar 覆盖周一到周三，替换那根仅含周一的周线。

#### 2.2.5 `belongs_to_same_period` 判断规则

| tf | 判断逻辑 |
|----|---------|
| 周线 | `date1.isocalendar()[1] == date2.isocalendar()[1] AND date1.year == date2.year` |
| 月线 | `date1.year == date2.year AND date1.month == date2.month` |
| 季线 | `date1.year == date2.year AND (date1.month-1)//3 == (date2.month-1)//3` |
| 年线 | `date1.year == date2.year` |
| 其他 | `date1 == date2` |

### 2.3 4 视图窗口一致性保证

#### 2.3.1 一致性原理

所有视图共享同一个时间锚点：`cutoff_date = min_tf_dates[bar_index]`。

每个视图独立执行窗口平移算法，但都基于同一个 `cutoff_date`，因此：

- **时间一致性**：4 个视图的窗口终点对应同一个绝对时间点
- **粒度独立性**：每个视图以自己的周期频率展示数据
- **bar 数量独立性**：每个视图各自使用自己的 `n_pts`（来自 cfg）

#### 2.3.2 数学约束

对于任意两个视图 A (tf_a) 和 B (tf_b)，以及任意 bar_index：

```
cutoff_date = min_tf_dates[bar_index]

A 的窗口终点日期 ≤ cutoff_date，且是 tf_a 下 ≤ cutoff_date 的最大日期
B 的窗口终点日期 ≤ cutoff_date，且是 tf_b 下 ≤ cutoff_date 的最大日期
```

A 和 B 的窗口终点可能不是同一天（高周期可能滞后于低周期），这是正确的行为。

#### 2.3.3 数据完整性校验断言

每次 bar_index 变更后，对每个 tf 视图执行：

```python
df_slice, meta = _load_backtest_window(tf, bar_index, n_pts, min_tf_dates)
assert meta["slice_length"] <= n_pts
assert df_slice.index[-1] <= min_tf_dates[bar_index]
if meta["is_synthesized_last"]:
    assert df_slice["is_synthesized"].iloc[-1] == True  # 只有最后一根可能是合成的
    assert df_slice["is_synthesized"].iloc[:-1].sum() == 0  # 其他都不是
```

### 2.4 状态管理

#### 2.4.1 新增 session_state 键清单

> **注意**：以下键已在 `filter_app/state.py` 的 `SYSTEM_KEYS` 字典中声明，无需修改 `state.py`。本节描述这些键的完整语义和使用规范。

| 键名 | 类型 | 默认值 | 语义 |
|------|------|--------|------|
| `_cb_mode` | `bool` | `False` | 回测模式开关。True 时整个应用处于回测模式 |
| `_bar_index` | `int` | `0` | 当前回测位置。始终对应 min_tf 的 bar 索引 |
| `_is_playing` | `bool` | `False` | 自动播放状态。True 时 `_run_backtest_play` 循环推进 |
| `_play_speed` | `float` | `1.0` | 播放速度（秒/步）。控制每步的 time.sleep 延迟 |
| `_play_speed_label` | `str` | `"1x"` | 播放速度标签。对应速度选择器的选中值，用于 speed_map 查找 |
| `_min_tf` | `str` | `""` | 4 个视图中的最小周期名（如 "5分钟"） |
| `_min_tf_bar_count` | `int` | `0` | min_tf 的总 bar 数（= total_bars） |
| `_bt_data_cache` | `dict[str, DataFrame]` | `{}` | 各 TF 的完整 DataFrame 缓存。key=tf, value=从 Parquet 读取的 df |

#### 2.4.2 状态生命周期

**进入回测模式**（`_render_backtest_mode_switch` 中检测到切换）：

```
1. 遍历 4 个视图的 configs:
   a. _sync_to_display(ticker_code, tf, 0, n_pts)   // 确保 Parquet 最新
   b. 从 data/display/{tf}.parquet 读取全量 DataFrame
   c. 存入 _bt_data_cache[tf] = df
2. AppState.set("_bar_index", 0)
3. AppState.set("_is_playing", False)
4. (min_tf, bar_count) = _get_min_tf_and_count(configs, ticker_code)
5. AppState.set("_min_tf", min_tf)
6. AppState.set("_min_tf_bar_count", bar_count)
7. AppState.set("_cb_mode", True)
8. st.rerun()
```

**退出回测模式**：

```
1. AppState.set("_bt_data_cache", {})
2. AppState.set("_bar_index", 0)
3. AppState.set("_is_playing", False)
4. AppState.set("_min_tf", "")
5. AppState.set("_min_tf_bar_count", 0)
6. AppState.set("_cb_mode", False)
7. st.rerun()
```

**回测内部状态推进**（每次 bar_index 变更）：

```
bar_index 变更 → 窗口重算 → _bt_data_cache 不变（不回读文件）
bar_index 不变 → 直接使用缓存（零计算开销）
```

#### 2.4.3 关键不变量

1. `_cb_mode=True` 时 `_day_offset` 始终为 0
2. `_cb_mode=True` 时 `_min_tf` 非空
3. `0 <= _bar_index < _min_tf_bar_count`
4. `_bt_data_cache` 中的 key 集合 = 当前 4 视图的 tf 集合
5. 浏览模式下所有 `backtest_*` 键不存在或为默认值

### 2.5 数据流图

```
┌─────────────────────────────────────────────────────────────────────┐
│                         数据持久层                                   │
│  SQLite (filter_app/data/stock.db)                                  │
│    │ query_kline(code, tf, n_pts, day_offset)                       │
│    ▼                                                                │
│  Parquet (filter_research/data/display/{tf}.parquet)                │
│    列: Date, Open, High, Low, Close                                  │
│    每个 tf 一个文件，每次 _sync_to_display 覆盖写入                   │
└─────────────────────────────────────────────────────────────────────┘
                    │
                    │ 浏览模式: _load_chart_data (bar_index=None)
                    │   读取 Parquet → 返回全量数据
                    │
                    │ 回测模式: _load_chart_data (bar_index=int)
                    │   读取 Parquet → 窗口切片 + 高周期合成
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       数据加载层                                     │
│                                                                     │
│  _load_chart_data(market, ticker_code, tf, day_offset, n_pts,       │
│                   bar_index=None) → tuple                           │
│                                                                     │
│  分支 A (bar_index=None): 浏览模式                                  │
│    _sync_to_display → 读 Parquet → 验证 → 返回全量 (t, noisy, ohlc) │
│                                                                     │
│  分支 B (bar_index is not None): 回测模式                           │
│    _sync_to_display → 读 Parquet → 验证                              │
│      → _apply_backtest_window(df, ohlc, ticker_code,                │
│                                bar_index, n_pts, tf)                │
│        │                                                            │
│        ├── Phase 1: 窗口平移                                        │
│        │   cutoff_date = min_tf_dates[bar_index]                    │
│        │   cutoff_idx = binary_search_le(df.index, cutoff_date)     │
│        │   df_slice = df.iloc[start_idx : cutoff_idx+1]             │
│        │                                                            │
│        ├── Phase 2: 高周期合成（条件触发）                           │
│        │   if tf != min_tf AND not at boundary:                     │
│        │     synth_bar = synthesize_from_lower_tf(...)              │
│        │     替换或追加到 df_slice                                   │
│        │                                                            │
│        └── 返回截断后的 (t, noisy, ohlc, ticker_full, dates, None)  │
│                                                                     │
│  兜底: Parquet 不存在或数据不足 → _cached_fetch_stock(yfinance)     │
└─────────────────────────────────────────────────────────────────────┘
                    │
                    │ 返回 (t, noisy, ohlc, ticker_full, dates, err)
                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       计算 & 渲染层                                  │
│                                                                     │
│  _render_chart(market, ticker_code, cfg, key, compact, day_offset,  │
│                higher_pnl, bar_index)                                │
│                                                                     │
│  Step 1: _load_chart_data(..., bar_index=bar_index)                 │
│  Step 2: _date_markers(dates, tf)                                   │
│  Step 3: _align_pnl_to_current_tf (高周期 PnL 对齐)                  │
│  Step 4: _compute_filters(noisy, t, cfg)                             │
│  Step 5: Info captions                                              │
│  Step 6: _compute_schmitt_trigger + _find_all_pairs                 │
│  Step 7: _compute_prediction_pairs(..., bar_index=bar_index)        │
│           └── 回测模式下过滤 bar_index 之后的预测对 ← 消除前视偏差   │
│  Step 8: _compute_strategy_display                                  │
│  Step 9: Subplot layout                                             │
│  Step 10: make_subplots + traces                                    │
│  Step 11: _render_plotly(fig)                                       │
└─────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         UI 层                                        │
│                                                                     │
│  侧边栏:                                                            │
│    _render_backtest_mode_switch()  → radio: 浏览 / 回测              │
│    _render_backtest_status()       → 📍 bar N/T | 2024-01-15        │
│    _render_time_nav()              → 回测模式下 early return         │
│                                                                     │
│  主区域:                                                            │
│    _render_backtest_controls()     → 仅在 _cb_mode=True 时渲染       │
│      第一行: ⏮ ◀ ▶⏸ ▶▶ ⏭ 速度                                     │
│      第二行: 进度 slider                                            │
│    2x2 图表网格: 4 × _render_chart_fragment(..., bar_index=...)     │
│                                                                     │
│  播放循环:                                                          │
│    _run_backtest_play() → time.sleep → bar_index += 1 → st.rerun() │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 第 3 章：核心函数设计

### 3.1 `_load_backtest_window`

#### 3.1.1 函数签名

```python
def _load_backtest_window(
    tf: str,           # 目标周期名（ALL_TFS 中的中文名，如 "日线", "周线"）
    bar_index: int,    # 当前 min_tf bar 索引
    n_pts: int,        # 窗口大小（bar 数量）
    min_tf_dates: pd.DatetimeIndex,  # min_tf 的完整日期索引
) -> tuple[pd.DataFrame, dict]:
```

#### 3.1.2 输入规格

| 参数 | 类型 | 取值范围 | 来源 |
|------|------|---------|------|
| `tf` | `str` | `ALL_TFS` 中的任一值 | `cfg["tf"]` |
| `bar_index` | `int` | `[0, total_bars-1]` | `AppState.get("_bar_index")` |
| `n_pts` | `int` | 正整数（通常 50-500） | `cfg["n_pts"]` |
| `min_tf_dates` | `pd.DatetimeIndex` | 已排序，长度 = total_bars | `AppState.get("_bt_data_cache")[min_tf].index` |

#### 3.1.3 输出规格

```python
df_slice: pd.DataFrame
    # 列 = [Open, High, Low, Close, is_synthesized] + Volume(如存在)
    # 索引 = pd.DatetimeIndex
    # 长度 = min(n_pts, 可用 bar 数)
    # 排序：升序

meta: dict
    # {
    #     "start_idx":   int,     # 在完整 df 中的起始索引
    #     "end_idx":     int,     # 在完整 df 中的结束索引（不含合成）
    #     "cutoff_idx":  int,     # cutoff_date 在 df 中的位置，-1 表示不存在
    #     "cutoff_date": pd.Timestamp,  # 窗口时间锚点
    #     "is_partial":  bool,    # 切片长度 < n_pts
    #     "is_synthesized_last": bool,  # 最后一根 bar 是合成数据
    #     "slice_length": int,     # 切片实际长度
    # }
```

#### 3.1.4 伪代码

```
function _load_backtest_window(tf, bar_index, n_pts, min_tf_dates):
    df = session_state["_bt_data_cache"][tf]
    min_tf = session_state["_min_tf"]

    // ── Phase 1: 窗口平移 ──
    cutoff_date = min_tf_dates[bar_index]
    cutoff_idx = binary_search_le(df.index, cutoff_date)

    if cutoff_idx == -1:
        return empty_df, {is_partial: True, slice_length: 0, cutoff_date, ...}

    end_idx   = cutoff_idx
    start_idx = max(0, end_idx - n_pts + 1)
    df_slice  = df.iloc[start_idx : end_idx + 1].copy()
    df_slice["is_synthesized"] = False
    slice_length = len(df_slice)
    is_partial   = slice_length < n_pts

    // ── Phase 2: 高周期合成 ──
    is_synthesized_last = False

    if tf != min_tf and slice_length > 0:
        boundary = get_period_boundary(tf, cutoff_date, min_tf_dates)

        if cutoff_date != boundary:
            last_complete_date = df_slice.index[-1]
            lower_df = session_state["_bt_data_cache"][min_tf]
            synth_data = lower_df[
                (lower_df.index > last_complete_date) &
                (lower_df.index <= cutoff_date)
            ]

            if len(synth_data) > 0:
                synthetic_bar = build_synthetic_bar(synth_data, cutoff_date)
                if belongs_to_same_period(tf, last_complete_date, cutoff_date):
                    df_slice.iloc[-1] = synthetic_bar  // 替换
                else:
                    df_slice = concat_and_trim(df_slice, synthetic_bar, n_pts)
                    slice_length = len(df_slice)
                is_synthesized_last = True

    // ── Phase 3: 组装输出 ──
    meta = {start_idx, end_idx, cutoff_idx, cutoff_date, is_partial,
            is_synthesized_last, slice_length}
    return df_slice, meta
```

#### 3.1.5 边界场景处理表

| 场景 | 输入条件 | 函数行为 | 输出特征 |
|------|---------|---------|---------|
| min_tf 无数据 | `len(min_tf_dates) == 0` | 不应调用此函数，上层守卫 | N/A |
| bar_index 越界 | `bar_index >= len(min_tf_dates)` | 不应调用，上层守卫 | N/A |
| tf 无数据 | `len(df) == 0` | cutoff_idx=-1，返回空切片 | `slice_length=0, is_partial=True` |
| cutoff_idx=-1 | 高周期数据尚未出现 | 返回空切片 | `slice_length=0, is_partial=True` |
| 部分窗口 | `slice_length < n_pts` | 正常返回，标记 is_partial | `is_partial=True` |
| 满窗口 | `slice_length == n_pts` | 正常返回 | `is_partial=False` |
| 需要合成 | `tf != min_tf AND cutoff_date != boundary` | 执行合成逻辑 | `is_synthesized_last=True` |
| 无需合成 | `tf == min_tf OR cutoff_date == boundary` | 跳过合成 | `is_synthesized_last=False` |
| synth_data 为空 | 无新低周期数据 | 跳过合成 | `is_synthesized_last=False` |
| 同周期替换 | `belongs_to_same_period = True` | 替换最后一行 | 长度不变 |
| 异周期追加 | `belongs_to_same_period = False` | 追加并 trim | 若满则丢弃最早一根 |

### 3.2 `_synthesize_higher_tf_bar`

#### 3.2.1 函数签名

```python
def _synthesize_higher_tf_bar(
    lower_df: pd.DataFrame,  # min_tf 的日线/分钟线数据
    tf_name: str,            # 目标高周期名
    last_complete_date: pd.Timestamp,  # 上一根完整 bar 的日期
    cutoff_date: pd.Timestamp,         # 当前窗口截止日期
) -> dict | None:
```

#### 3.2.2 输入规格

| 参数 | 类型 | 说明 |
|------|------|------|
| `lower_df` | `pd.DataFrame` | min_tf 数据，index=DatetimeIndex，列含 Open/High/Low/Close |
| `tf_name` | `str` | 目标高周期名（用于日志/错误提示，非逻辑依赖） |
| `last_complete_date` | `pd.Timestamp` | 上一根完整高周期 bar 的日期 |
| `cutoff_date` | `pd.Timestamp` | 当前时间锚点 |

#### 3.2.3 输出规格

```python
# 成功:
{
    "Open":   float,
    "High":   float,
    "Low":    float,
    "Close":  float,
    "Volume": int,
    "date":   pd.Timestamp,   # = cutoff_date
    "is_synthesized": True,
}

# 失败（无数据可合成）:
None
```

#### 3.2.4 OHLC 合成规则（重复强调）

```
Open  = synth_data["Open"].iloc[0]     # 第一个交易日的开盘价
High  = synth_data["High"].max()        # 期间最高价
Low   = synth_data["Low"].min()         # 期间最低价
Close = synth_data["Close"].iloc[-1]    # 最后一个交易日的收盘价
Volume = synth_data["Volume"].sum()     # 成交量累加
```

### 3.3 `_get_min_tf_and_count`

#### 3.3.1 函数签名

```python
def _get_min_tf_and_count(
    configs: list[dict],
    ticker_code: str,
) -> tuple[str, int]:
```

#### 3.3.2 输入规格

| 参数 | 类型 | 说明 |
|------|------|------|
| `configs` | `list[dict]` | 4 个视图的配置列表，每个含 `tf` 字段 |
| `ticker_code` | `str` | 股票代码（用于构建 Parquet 路径） |

#### 3.3.3 输出规格

```python
(min_tf: str, bar_count: int)

# min_tf: ALL_TFS 中索引最小的 tf（即最精细周期），如 "5分钟"
# bar_count: data/display/{min_tf}.parquet 的总行数
```

#### 3.3.4 伪代码

```
function _get_min_tf_and_count(configs, ticker_code):
    // Step 1: 找最小周期
    min_tf = min(configs, key=lambda c: ALL_TFS.index(c["tf"]))["tf"]

    // Step 2: 读 Parquet 获取总 bar 数
    parquet_path = data/display/{min_tf}.parquet
    df = pd.read_parquet(parquet_path)
    bar_count = len(df)

    return min_tf, bar_count
```

### 3.4 修改点清单

#### 3.4.1 需要修改的现有函数

| 文件 | 函数 | 当前行号范围 | 修改内容 | 改动量 |
|------|------|------------|---------|--------|
| `streamlit_app.py` | `_load_chart_data` | 195-224 | 分支 B 的回测窗口加载逻辑。确保 bar_index 非 None 时正确调用窗口平移 + 合成逻辑 | 中等（重构分支 B 内部实现） |
| `streamlit_app.py` | `_render_chart` | 596-749 | Step 7 中 `_compute_prediction_pairs(..., bar_index=bar_index)` 的前视偏差过滤 | 小（确保 bar_index 传递正确） |
| `streamlit_app.py` | `_compute_prediction_pairs` | 需确认行号 | 当 `bar_index is not None` 时，过滤掉 `t > bar_index` 的预测对 | 小（添加 if 分支） |
| `streamlit_app.py` | `_render_time_nav` | 1414-1464 | 已实现回测模式 early return。验证正确性 | 无（已实现） |
| `streamlit_app.py` | `main()` | 约 1650+ | 已实现 `_render_backtest_controls` 和 `_run_backtest_play` 的条件调用。验证正确性 | 无（已实现） |
| `state.py` | `SYSTEM_KEYS` | 28-50 | 已有所有回测相关键。验证完整性 | 无（已实现） |

#### 3.4.2 需要新增的函数

| 文件 | 函数名 | 功能 | 预估行数 |
|------|--------|------|---------|
| `streamlit_app.py` | `_load_backtest_window` | 整合窗口平移 + 高周期合成 | 60-80 |
| `streamlit_app.py` | `_synthesize_higher_tf_bar` | OHLC 合成单根高周期 bar | 25-35 |
| `streamlit_app.py` | `_get_min_tf_and_count` | 确定 min_tf 和 bar 总数 | 15-20 |
| `streamlit_app.py` | `_get_period_boundary` | 计算 tf 周期边界 | 25-35 |
| `streamlit_app.py` | `_belongs_to_same_period` | 判断两个日期是否属同一周期 | 15-20 |
| `streamlit_app.py` | `_binary_search_le` | 日期数组二分查找 | 10-15 |

#### 3.4.3 不需要修改的部分

- `_sync_to_display` — 回测和浏览模式共用同一数据同步逻辑
- `_compute_filters` — 输入数据格式不变
- `_compute_schmitt_trigger` — 输入数据格式不变
- `_find_all_pairs` — 输入数据格式不变
- `_render_param_panels` — 参数面板不受模式影响
- `_render_filter_selectors` — 滤波器选择不受模式影响
- `state.py` 的 `AppState` 类 — SYSTEM_KEYS 已完备

---

## 第 4 章：UI 交互设计

### 4.1 布局方案

#### 4.1.1 侧边栏布局

```
侧边栏:
  ├── _render_config_import()            # 导入配置
  ├── _render_market_ticker()            # 市场 + 股票代码
  ├── _handle_initial_fetch()            # 首次自动获取
  ├── _render_refresh_row()              # 刷新按钮 + 自动刷新
  ├── _render_preset_selector()          # 预设选择器
  ├── ─── 分隔线 ───
  ├── _render_health_check()             # 数据健康检查 (expander)
  ├── _render_data_validation()          # 数据校验 (expander)
  ├── ─── 分隔线 ───
  ├── _render_filter_selectors()         # 滤波器选择
  ├── _render_param_panels()             # 2×2 参数面板
  ├── ─── 分隔线 ───
  ├── _render_time_nav()
  │   浏览模式: 前后按钮 + day_step + 偏移量
  │   回测模式: 直接 return，隐藏全部 day_offset 控件  ← 条件渲染
  ├── ─── 分隔线 ───
  ├── _render_backtest_mode_switch()      # radio: ○ 浏览 ● 回测
  ├── _render_backtest_status()           # 📍 bar N/T | 日期
  ├── ─── 分隔线 ───
  ├── _render_db_backup()                 # 备份/恢复 (expander)
  ├── _render_export_config()             # 下载配置按钮
  ├── _render_config_history()            # 配置历史 (expander)
  └── _render_db_import_export()          # 数据库导入/导出 (expander)
```

#### 4.1.2 主区域布局

```
主区域:
  ┌─ _render_backtest_controls()  ← 仅 _cb_mode=True 时渲染
  │   第一行: ⏮  │  ◀  ▶/⏸  ▶▶  │  ⏭  速度选择
  │   第二行: ▬▬▬▬▬●▬▬▬▬▬ 进度 slider
  └─ 2×2 图表网格
       for row in [0, 1]:
         for col in [0, 1]:
           _render_chart_fragment(market, ticker_code, cfg, key, ...,
                                  bar_index=bar_index)
```

### 4.2 控制条规格

#### 4.2.1 按钮规格表

| 列 | 控件 | 图标 | Widget | 绑定动作 | Disabled 条件 |
|----|------|------|--------|---------|---------------|
| 1 | 跳到开头 | ⏮ | `st.button` | `bar_index = 0; st.rerun()` | 无 |
| 2 | 后退一步 | ◀ | `st.button` | `bar_index = max(0, bar_index-1); st.rerun()` | `bar_index <= 0` |
| 3 | 播放/暂停 | ▶ / ⏸ | `st.button` | 切换 `_is_playing`; `st.rerun()` | 无 |
| 4 | 前进一步 | ▶▶ | `st.button` | `bar_index = min(total-1, bar_index+1); st.rerun()` | `bar_index >= total_bars - 1` |
| 5 | 跳到最新 | ⏭ | `st.button` | `bar_index = total_bars - 1; st.rerun()` | 无 |
| 6 | 速度选择 | — | `st.selectbox` | 写入 `_play_speed_label` + `_play_speed`（不 rerun） | 无 |

> **播放按钮状态切换**：按钮文案和图标根据 `_is_playing` 动态切换：`False` 时显示 ▶，`True` 时显示 ⏸。

#### 4.2.2 速度映射表

| 标签 | 值 (秒/步) | selectbox index |
|------|-----------|-----------------|
| `0.25x` | 4.0 | 0 |
| `0.5x` | 2.0 | 1 |
| `1x` | 1.0 | 2（默认） |
| `2x` | 0.5 | 3 |
| `5x` | 0.2 | 4 |
| `10x` | 0.1 | 5 |

#### 4.2.3 进度 Slider 规格

| 属性 | 值 |
|------|-----|
| Widget | `st.slider` |
| label | `"进度"`, `label_visibility="collapsed"` |
| range | `(0, total_bars - 1)` |
| value | `bar_index` |
| key | `"_bt_progress_slider"` |
| on_change | ① 设置 `_bar_index = new_value` ② 若 `_is_playing` 则暂停 ③ `st.rerun()` |

#### 4.2.4 空数据守卫

当 `total_bars == 0` 时：
- 渲染 6 个 disabled button（占位框架）
- 上方显示 `st.warning("回测数据未就绪：{min_tf} display 缓存不存在。请先切换回浏览模式加载数据。")`

### 4.3 状态显示

#### 4.3.1 显示格式

```
📍 bar {bar_index + 1}/{total_bars}
📅 {formatted_date}
▶ 播放中  （播放时追加，绿色：:green[▶ 播放中]）
```

#### 4.3.2 日期格式化逻辑

```python
idx = _bt_data_cache[_min_tf].index[bar_index]
if isinstance(idx, pd.Timestamp):
    formatted_date = idx.strftime("%Y-%m-%d %H:%M")
elif hasattr(idx, "date"):
    formatted_date = str(idx.date())
else:
    formatted_date = str(idx)
```

#### 4.3.3 空状态

- `_cb_mode=False`：不渲染
- `total_bars == 0`：`📍 bar 1/0`

### 4.4 播放循环

#### 4.4.1 入口条件

```python
def _run_backtest_play():
    if not AppState.get("_cb_mode", False):
        return
    if not AppState.get("_is_playing", False):
        return
    # ... 执行播放逻辑
```

#### 4.4.2 执行流程

```
1. bar_index = AppState.get("_bar_index")
2. total_bars = AppState.get("_min_tf_bar_count")
3. if bar_index >= total_bars - 1:
       AppState.set("_is_playing", False); return   // 自动暂停
4. speed_label = AppState.get("_play_speed_label", "1x")
5. delay = speed_map[speed_label]
6. time.sleep(delay)
7. AppState.set("_bar_index", bar_index + 1)
8. st.rerun()
```

#### 4.4.3 设计约束

- **纯 Streamlit 驱动**：不依赖额外线程或 asyncio，每次 rerun 是独立的函数调用
- **到达末尾自动暂停**：`bar_index >= total_bars - 1` 时 `_is_playing = False`
- **拖拽 slider 自动暂停**：进度 slider 的 on_change 回调中设置 `_is_playing = False`
- **速度切换不中断**：仅写入状态变量，不触发 rerun，下个 cycle 自动生效

### 4.5 模式切换

#### 4.5.1 控件规格

```python
st.sidebar.radio(
    "模式",
    options=["浏览模式", "回测模式"],
    horizontal=True,
    key="_bt_mode_radio"
)
```

#### 4.5.2 切换到回测模式

```
1. st.spinner("加载回测数据...")
2. 遍历 4 个 configs:
   a. _sync_to_display(ticker_code, tf, 0, n_pts)
   b. pd.read_parquet(f"data/display/{tf}.parquet")
   c. _bt_data_cache[tf] = df
3. AppState.set("_bar_index", 0)
4. AppState.set("_is_playing", False)
5. min_tf, bar_count = _get_min_tf_and_count(configs, ticker_code)
6. AppState.set("_min_tf", min_tf)
7. AppState.set("_min_tf_bar_count", bar_count)
8. AppState.set("_cb_mode", True)
9. st.toast(f"回测模式已启用  最小周期: {min_tf} ({bar_count} bars)")
10. st.rerun()
```

#### 4.5.3 切换到浏览模式

```
1. AppState.set("_bt_data_cache", {})
2. AppState.set("_bar_index", 0)
3. AppState.set("_is_playing", False)
4. AppState.set("_min_tf", "")
5. AppState.set("_min_tf_bar_count", 0)
6. AppState.set("_cb_mode", False)
7. st.rerun()
```

#### 4.5.4 与 day_offset 的共存

浏览模式的 `_render_time_nav` 在回测模式下的行为：

```python
# _render_time_nav 内（已实现）
if AppState.get("_cb_mode", False):
    AppState.set("_day_offset", 0)
    return 0  # early return，不渲染任何时间导航控件
```

**回测模式下隐藏的控件**：day_step selectbox、前移/后移/最新按钮、偏移量/数据范围 caption。

**设计理由**：回测模式的时间导航由控制条的 `bar_index` 按钮 + slider 完全替代。两套导航系统同时显示会造成用户困惑。`day_offset` 在回测模式下固定为 0。

---

## 第 5 章：实现路线图

### Phase 1: 数据模型 + 窗口平移（核心）

**目标**：实现窗口平移算法，`bar_index` 变更时 4 个视图正确截取历史窗口。

**文件清单**：

| 文件 | 操作 | 内容 |
|------|------|------|
| `filter_app/streamlit_app.py` | 新增函数 | `_binary_search_le(arr, target) -> int` |
| `filter_app/streamlit_app.py` | 新增函数 | `_get_min_tf_and_count(configs, ticker_code) -> tuple[str, int]` |
| `filter_app/streamlit_app.py` | 新增函数 | `_load_backtest_window(tf, bar_index, n_pts, min_tf_dates) -> tuple[DataFrame, dict]` (Phase 1 仅窗口平移部分) |
| `filter_app/streamlit_app.py` | 修改函数 | `_load_chart_data` — 分支 B 调用 `_load_backtest_window`；bar_index 非 None 时从 `_bt_data_cache` 取数据而非每次读 Parquet |

**函数清单**：

| 函数名 | 类型 | 预估行数 |
|--------|------|---------|
| `_binary_search_le` | 新增 | 12 |
| `_get_min_tf_and_count` | 新增 | 18 |
| `_load_backtest_window` | 新增（Phase 1 简化版） | 45 |
| `_load_chart_data` | 修改分支 B | 15（改动） |

**预估改动量**：新增约 75 行，修改约 15 行。

**验证标准**：

1. 切换到回测模式后，`_bt_data_cache` 包含 4 个 tf 的完整 DataFrame
2. `_min_tf` 和 `_min_tf_bar_count` 正确设置
3. `bar_index=0` 时，日线视图显示 1 根 bar（部分窗口），其他高周期视图可能为空
4. `bar_index=n_pts-1` 时，日线视图恰好满窗口（n_pts 根 bar）
5. `bar_index=n_pts-1` 到 `bar_index=total_bars-1` 之间，日线视图始终满窗口
6. 同一个 `bar_index` 下，4 个视图的窗口终点对应同一个时间点
7. 点击前进/后退按钮，窗口正确平移
8. 拖拽进度 slider，窗口正确跳转

### Phase 2: 高周期合成

**目标**：实现高周期未完成 bar 的 OHLC 合成。

**文件清单**：

| 文件 | 操作 | 内容 |
|------|------|------|
| `filter_app/streamlit_app.py` | 新增函数 | `_get_period_boundary(tf, date, all_trading_dates) -> pd.Timestamp` |
| `filter_app/streamlit_app.py` | 新增函数 | `_calendar_period_end(tf, date) -> pd.Timestamp` |
| `filter_app/streamlit_app.py` | 新增函数 | `_belongs_to_same_period(tf, date1, date2) -> bool` |
| `filter_app/streamlit_app.py` | 新增函数 | `_synthesize_higher_tf_bar(lower_df, tf_name, last_complete_date, cutoff_date) -> dict | None` |
| `filter_app/streamlit_app.py` | 修改函数 | `_load_backtest_window` — 添加 Phase 2（高周期合成逻辑） |

**函数清单**：

| 函数名 | 类型 | 预估行数 |
|--------|------|---------|
| `_get_period_boundary` | 新增 | 30 |
| `_calendar_period_end` | 新增 | 25 |
| `_belongs_to_same_period` | 新增 | 20 |
| `_synthesize_higher_tf_bar` | 新增 | 30 |
| `_load_backtest_window` | 扩展 Phase 2 | 35（新增） |

**预估改动量**：新增约 140 行。

**验证标准**：

1. 周线视图的 cutoff_date 为周三时，最后一根周线标记 `is_synthesized=True`
2. 周线视图的 cutoff_date 为周五（且是交易日）时，最后一根周线 `is_synthesized=False`
3. 合成 bar 的 OHLC 值：O=第一个交易日的开盘价，H=期间最高价，L=期间最低价，C=最后一个交易日的收盘价
4. 同一周期内推进，合成 bar 替换而非追加（长度不变）
5. 跨周期推进，合成 bar 追加（长度+1，若满则 trim）
6. 日线视图（min_tf）永不触发合成
7. 节假日周期边界正确处理（如周五是假日，周线边界变为周四）
8. synth_data 为空时不合成（非交易日场景）

### Phase 3: UI 控件

**目标**：验证并完善回测模式的全部 UI 交互。

**文件清单**：

| 文件 | 操作 | 内容 |
|------|------|------|
| `filter_app/streamlit_app.py` | 验证 | `_render_backtest_mode_switch` — 确保模式切换逻辑正确 |
| `filter_app/streamlit_app.py` | 验证 | `_render_backtest_controls` — 确保控制条渲染和 callback 正确 |
| `filter_app/streamlit_app.py` | 验证 | `_render_backtest_status` — 确保状态显示格式正确 |
| `filter_app/streamlit_app.py` | 验证 | `_run_backtest_play` — 确保播放循环逻辑正确 |
| `filter_app/streamlit_app.py` | 验证 | `_render_time_nav` — 确保回测模式下 early return |
| `filter_app/streamlit_app.py` | 修改 | `_render_chart` — 确保 `bar_index` 传递到 `_compute_prediction_pairs` |
| `filter_app/streamlit_app.py` | 修改 | `_compute_prediction_pairs` — 回测模式下过滤未来预测对 |
| `filter_app/state.py` | 验证 | `SYSTEM_KEYS` — 确认所有回测键已定义 |

**函数清单**：

| 函数名 | 类型 | 预估行数 |
|--------|------|---------|
| `_compute_prediction_pairs` | 修改 | 5（添加 bar_index 条件分支） |
| 其余函数 | 验证 | 无新增代码 |

**预估改动量**：修改约 5-10 行。

**验证标准**：

1. 浏览模式和回测模式切换顺畅（radio 点击 → spinner → toast → rerender）
2. 切换到回测模式后，侧边栏的时间导航控件（前移/后移/最新/day_step）完全不显示
3. 回测控制条正常渲染：6 个按钮 + 进度 slider，总数 `total_bars` 正确显示
4. 点击 ⏮ 跳到 bar_index=0，点击 ⏭ 跳到 bar_index=total_bars-1
5. 点击 ◀ 后退 1，点击 ▶▶ 前进 1；到达边界时按钮 disabled
6. 状态显示正确：`📍 bar 3/250 | 2024-01-15`
7. 播放循环：点击 ▶ 后自动前进，速度切换生效，到达末尾自动暂停
8. 拖拽 slider 时若正在播放则自动暂停
9. total_bars=0 时显示 disabled 按钮 + 警告
10. 切换回浏览模式后，回测状态全部清除，时间导航恢复

### Phase 4: 集成 + 测试

**目标**：端到端验证，确保浏览模式不受影响。

**文件清单**：

| 文件 | 操作 | 内容 |
|------|------|------|
| `filter_app/streamlit_app.py` | 集成 | 确保所有修改点协调工作 |
| `tests/` | 新增 | 单元测试和集成测试（详见第 6 章） |

**预估改动量**：测试代码约 200-300 行。

**验证标准**：

1. 全部单元测试通过（见 6.1）
2. 全部集成测试通过（见 6.2）
3. 回归测试：浏览模式功能不受影响（见 6.3）
4. 手动验证清单全部通过（见 6.4）

---

## 第 6 章：验证方案

### 6.1 单元测试

每个新增/修改函数必须有对应的单元测试。

#### 6.1.1 `_binary_search_le`

| # | 测试用例 | 输入 | 期望输出 |
|---|---------|------|---------|
| 1 | 空数组 | `arr=[], target=任意` | `-1` |
| 2 | target < 所有值 | `arr=[10,20,30], target=5` | `-1` |
| 3 | target = 第一个值 | `arr=[10,20,30], target=10` | `0` |
| 4 | target = 最后一个值 | `arr=[10,20,30], target=30` | `2` |
| 5 | target 在中间 | `arr=[10,20,30], target=15` | `0` |
| 6 | target > 所有值 | `arr=[10,20,30], target=50` | `2` |
| 7 | 单元素等于 | `arr=[42], target=42` | `0` |
| 8 | 单元素小于 | `arr=[42], target=10` | `-1` |
| 9 | 单元素大于 | `arr=[42], target=100` | `0` |
| 10 | 重复值 | `arr=[10,20,20,30], target=20` | `2`（最大索引） |

#### 6.1.2 `_load_backtest_window`

| # | 测试用例 | 条件 | 期望输出 |
|---|---------|------|---------|
| 1 | bar_index=0, n_pts=100, min_tf=日线 | 日线有 500 根 bar | slice_length=1, is_partial=True, is_synthesized_last=False |
| 2 | bar_index=n_pts-1, n_pts=100 | 日线有 500 根 bar | slice_length=100, is_partial=False |
| 3 | bar_index=250, n_pts=100 | 日线有 500 根 bar | slice_length=100, is_partial=False, start_idx=151, end_idx=250 |
| 4 | bar_index=total-1, n_pts=100 | 日线有 500 根 bar | slice_length=100, is_partial=False |
| 5 | tf 数据为空 | `len(df)=0` | slice_length=0, is_partial=True |
| 6 | tf 数据全部在 cutoff_date 之后 | cutoff_idx=-1 | slice_length=0, is_partial=True |
| 7 | 周线需要合成（周三） | tf=周线, cutoff_date=周三 | is_synthesized_last=True, 最后一根 is_synthesized=True |
| 8 | 周线不需要合成（周五） | tf=周线, cutoff_date=周五 | is_synthesized_last=False |
| 9 | 周线同周期替换 | 推进 1 天仍在同一周 | 长度不变，最后一根被替换 |
| 10 | 周线跨周期追加 | 从周五推进到下一周周一 | 长度+1（若满则 trim） |
| 11 | 季线总 bar 不足 | 季线仅 24 根, n_pts=100 | slice_length=24（或含合成=25）, is_partial=True |

#### 6.1.3 `_get_min_tf_and_count`

| # | 测试用例 | 输入 | 期望输出 |
|---|---------|------|---------|
| 1 | 标准 4 视图 | [日线, 60分钟, 15分钟, 5分钟] | min_tf="5分钟", bar_count=从 Parquet 读取 |
| 2 | 只有日线和周线 | [日线, 周线] | min_tf="日线" |
| 3 | 单视图 | [15分钟] | min_tf="15分钟" |
| 4 | 包含 1分钟 | [日线, 1分钟] | min_tf="1分钟" |

#### 6.1.4 `_synthesize_higher_tf_bar`

| # | 测试用例 | 条件 | 期望输出 |
|---|---------|------|---------|
| 1 | 正常合成（3 天数据） | synth_data 有 3 行 | 返回 dict: O=第1行O, H=max(H), L=min(L), C=第3行C |
| 2 | 单日合成 | synth_data 有 1 行 | O=H=L=C=该行Close, is_synthesized=True |
| 3 | 无数据 | synth_data 为空 | 返回 None |
| 4 | 值校验 | 手工构造数据 | High ≥ Low, High ≥ Open, High ≥ Close, Low ≤ Open, Low ≤ Close |

#### 6.1.5 `_get_period_boundary`

| # | 测试用例 | 条件 | 期望输出 |
|---|---------|------|---------|
| 1 | 周线周三 | tf=周线, date=周三 | 本周五（若周五是交易日） |
| 2 | 周线周五 | tf=周线, date=周五 | 本周五（自身） |
| 3 | 月线月中 | tf=月线, date=6/15 | 6/30（若 6/30 是交易日） |
| 4 | 月线月末 | tf=月线, date=6/30 | 6/30（自身） |
| 5 | 周五是假日 | tf=周线, date=周三, 周五假日 | 周四 |
| 6 | 月线 2 月 | tf=月线, date=2/15 | 2/28 或 2/29 |

### 6.2 集成测试

#### 6.2.1 端到端回测流程

| # | 测试流程 | 验证点 |
|---|---------|--------|
| 1 | 浏览模式 → 点击回测 radio → 等待加载 | `_cb_mode=True`; `_bt_data_cache` 含 4 个 tf; `_min_tf` 非空; `_min_tf_bar_count > 0`; toast 显示 |
| 2 | 点击 ▶ 播放 | bar_index 自动递增; 图表窗口跟随移动; 状态显示实时更新 |
| 3 | 播放中点击 ⏸ | 播放停止; `_is_playing=False`; bar_index 保持在当前位置 |
| 4 | 播放中拖拽 slider | 播放自动暂停; bar_index 跳转到拖拽位置 |
| 5 | 到达末尾 | 自动暂停; ▶ 按钮可用（可重新播放从头开始? 不，末尾停止） |
| 6 | 点击 ◀ 后退 | bar_index 减 1; 窗口左移; 图表更新 |
| 7 | 点击 ⏮ 跳到开头 | bar_index=0; 部分窗口; `is_partial=True` |
| 8 | 点击 ⏭ 跳到最新 | bar_index=total_bars-1; 满窗口; 等同于浏览模式 day_offset=0 |
| 9 | 更改速度 → 继续播放 | 新速度在下一个 cycle 生效 |
| 10 | 回测模式 → 切换回浏览模式 | `_cb_mode=False`; 回测键清除; 时间导航恢复; 图表显示全量数据 |
| 11 | 浏览模式 → 修改 day_offset → 切换回测 | `day_offset` 不影响回测; 回测始终 day_offset=0 |

#### 6.2.2 多视图一致性

| # | 测试流程 | 验证点 |
|---|---------|--------|
| 1 | 4 视图配置 [日线, 60分钟, 15分钟, 5分钟]; 推进 bar_index | 4 个图表的窗口终点对应同一个绝对时间点 |
| 2 | 日线视图满窗口后 | 4 个视图的 K 线走势同步（同一时间段的趋势一致） |
| 3 | 跨周期 PnL 对齐 | 高周期 PnL 子图与主图时间对齐 |

### 6.3 回归测试

确保浏览模式功能不受回测模式影响。

| # | 测试项 | 验证方法 |
|---|--------|---------|
| 1 | _load_chart_data (bar_index=None) 正常工作 | 切换到浏览模式，检查图表是否正常加载 |
| 2 | day_offset 功能正常 | 使用前后按钮调整 day_offset，确认数据窗口正确偏移 |
| 3 | 自动刷新正常工作 | 开启自动刷新，等待几个周期确认 |
| 4 | 参数调整正常 | 修改滤波器参数，确认图表实时更新 |
| 5 | 预设导入/导出正常 | 导入配置预设，确认参数面板更新 |
| 6 | 双滤波模式正常 | 开启双滤波，确认两条滤波线都显示 |
| 7 | 跨周期 PnL 参考正常 | 确认 PnL 子图显示正确 |
| 8 | 施密特触发正常 | 确认买卖点标记显示正确 |
| 9 | 预测曲线正常 | 确认预测对显示正确（浏览模式下显示全部预测） |
| 10 | 数据健康检查正常 | 打开 health check expander 确认数据状态正确 |

### 6.4 手动验证清单

开发者在 `bar_index` 的几个关键位置手动验证：

| # | bar_index | 检查项 | 预期结果 |
|---|-----------|--------|---------|
| 1 | 0 | 日线视图 bar 数 | 1 根（或少数几根），左侧留空 |
| 2 | 0 | 周线/月线/季线视图 | 可能为空或少量 bar |
| 3 | 0 | 状态显示 | `📍 bar 1/{total} | {最早日期}` |
| 4 | n_pts - 1 | 日线视图 bar 数 | 恰好 n_pts 根 |
| 5 | n_pts - 1 | is_partial | False（日线） |
| 6 | 500（中间） | 日线窗口范围 | 从 bar_index-99 到 bar_index，100 根 |
| 7 | 500（中间） | 高周期最后一根 | 若不在周期边界，显示合成标记 |
| 8 | total - 1 | 日线视图 | 最后 100 根日线，最新数据 |
| 9 | total - 1 | 浏览模式对比 | 切换到浏览模式 day_offset=0，K 线应相同 |
| 10 | 播放中 | 前视偏差 | 预测曲线不超过 bar_index 位置 |

---

## 附录 A：现有接口参考

### A.1 关键常量

```python
# filter_app/components/sidebar.py

ALL_TFS = ["1分钟", "5分钟", "15分钟", "60分钟", "日线", "周线", "月线", "季线"]
DEFAULT_TFS = ["日线", "60分钟", "15分钟", "5分钟"]

TF_HIERARCHY = {
    "1分钟": "5分钟", "5分钟": "15分钟", "15分钟": "60分钟",
    "60分钟": "日线", "日线": "周线", "周线": "月线",
    "月线": "季线", "季线": None,
}
```

### A.2 关键函数签名汇总

```python
# data_loader.py
def _sync_to_display(code: str, tf: str, day_offset: int, n_pts: int) -> Tuple[bool, int]

# streamlit_app.py
def _load_chart_data(market, ticker_code, tf, day_offset, n_pts, bar_index=None) -> tuple
def _render_chart(market, ticker_code, cfg, key, compact=True, day_offset=0, higher_pnl=None, bar_index=None) -> None
def _render_param_panels(filter_id, dual, filter_id2) -> list  # 返回 configs
def _render_time_nav(total_bars, default_day_offset) -> int
def _render_backtest_mode_switch() -> None
def _render_backtest_controls() -> None
def _render_backtest_status() -> None
def _run_backtest_play() -> None

# state.py
class AppState:
    @staticmethod
    def get(key: str, default: Any = None) -> Any
    @staticmethod
    def set(key: str, value: Any) -> None
    @staticmethod
    def set_many(items: Dict[str, Any]) -> None
```

### A.3 Parquet 文件规范

```
路径: {project_root}/data/display/{tf}.parquet
列:   Date, Open, High, Low, Close
写入: index=False（每次覆盖）
读取后处理: pd.to_datetime() → set_index("Date") → sort_index()
```

### A.4 现有 session_state 键（回测相关）

已在 `state.py` 的 `SYSTEM_KEYS` 中定义：

| Key | Default | 说明 |
|-----|---------|------|
| `_cb_mode` | `False` | 回测模式开关 |
| `_bar_index` | `0` | 当前 bar 位置 |
| `_is_playing` | `False` | 自动播放中 |
| `_play_speed` | `0.5` | 播放速度（秒/步） |
| `_min_tf` | `""` | 4 视图中最小周期 |
| `_min_tf_bar_count` | `0` | 最小周期总 bar 数 |
| `_bt_data_cache` | `{}` | 回测全量数据缓存 |

注意：T2 文档中的 `backtest_*` 前缀键名（如 `backtest_bar_index`）在 T1/T3 的实际代码中使用 `_bar_index` 等简写。本规格以实际代码为准。

---

## 附录 B：术语表

| 术语 | 定义 |
|------|------|
| **min_tf** | 4 个视图中最精细的周期。回测以 min_tf bar 为时间刻度。通过 `ALL_TFS.index(tf)` 最小的 tf 确定 |
| **bar_index** | 当前回测位置，整数 ∈ [0, total_bars-1]，表示 min_tf 的第几根 bar |
| **total_bars** | min_tf 数据的总 bar 数，= `len(_bt_data_cache[min_tf])` |
| **n_pts** | 每个视图始终显示的 bar 数量（满 bar 原则），来自 `cfg["n_pts"]` |
| **cutoff_date** | `min_tf_dates[bar_index]`，窗口终点的绝对时间锚点。所有视图共享此值 |
| **df(tf)** | 周期 tf 的完整 DataFrame，从 Parquet 读取，按 DatetimeIndex 排序 |
| **df_slice** | 窗口切片结果，长度 ≤ n_pts。含 `is_synthesized` 列标记合成 bar |
| **period_boundary(tf, date)** | date 所属 tf 周期的结束边界（实际交易日），用于判断是否需要合成 |
| **is_synthesized** | DataFrame 列（bool），标记该 bar 是否为实时合成数据。仅在内存中的 df_slice 存在，Parquet 文件中无此列 |
| **is_partial** | meta 字段（bool），标记窗口是否未满 n_pts（部分窗口） |
| **前视偏差** | 在回测中使用 bar_index 之后的未来数据做决策的偏差。回测模式通过 bar_index 参数严格屏蔽未来数据 |
| **浏览模式** | 应用默认模式，始终查看最新数据，支持 day_offset 历史偏移 |
| **回测模式** | 站在历史某个时刻往前看，严格屏蔽未来数据 |
| **高周期合成** | 当高周期（周/月/季/年）的最后一根 bar 未完成时，从低周期数据实时计算 OHLC |
| **满 bar 原则** | 每个视图始终尝试展示 n_pts 根 bar，边界不足时显示实际数量 |

---

> **文档版本**: v1.0
> **生成日期**: 2026-07-01
> **上游来源**: T1 接口分析 (t1-interfaces.md) + T2 数据模型 (t2-data-model.md) + T3 UI 设计 (t3-ui-design.md)
