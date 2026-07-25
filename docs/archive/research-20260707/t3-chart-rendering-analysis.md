# 图表渲染中 Signal/PnL 的数据来源与动态更新机制分析

> 分析范围：`filter/streamlit_app.py`, `filter/components/charts.py`, `filter/services/filter_engine.py`, `filter/services/data_loader.py`, `filter/state.py`

---

## 目录

1. [_render_chart() 11-Step 逐步分析](#1-_render_chart-11-step)
2. [charts.py 可视化函数分析](#2-chartspy)
3. [动态更新机制分析](#3-动态更新机制)
4. [数据来源标注总表](#4-数据来源标注总表)
5. ["上一帧与下一帧信号不同"的渲染环节](#5-可能导致信号跳变的渲染环节)

---

## 1. _render_chart() 11-Step 逐步分析

函数位置: `streamlit_app.py:580-763`

### 架构概览

```
_render_chart_fragment (@st.fragment, line 545)
  └── _render_chart (line 580)
```

- `@st.fragment` 使得 4 个视图（v0..v3）可独立重渲染，不会触发全局 rerun。
- 但回测模式下 `st.rerun()` 触发的是全局 rerun，所有视图都会重算。

### Step 1: `_load_chart_data()` (line 163-216)

**数据来源标注**: parquet 文件 `data/display/{tf}.parquet`

#### 浏览模式 (window_start=None, cutoff_date=None)

```
调用链:
_load_chart_data()
  → _sync_to_display(ticker_code, tf, day_offset, n_pts)  [写 parquet]
    → query_kline(ticker_code, tf, n_pts, day_offset)       [读 DB: kline 表]
    → df.to_parquet(display/{tf}.parquet)                    [写 parquet]
  → pd.read_parquet(display/{tf}.parquet)                   [读 parquet]
  → 返回 (t, noisy, ohlc, ticker_full, dates, err)
```

- `_sync_to_display` 失败时回退到 `_cached_fetch_stock()`，直调 yfinance API（绕过 parquet）。
- `day_offset` 控制向后偏移天数（历史回溯），由侧边栏时间导航按钮控制。

#### 回测模式 (window_start != None, cutoff_date != None)

```
调用链:
main() 中前置调用 _sync_all_cascading()        [一次性写入所有 TF 的 parquet]
  → 每个 TF: DB 查询 → 判断是否需要 K 线合成 → 聚合 → 写 parquet
_load_chart_data()
  → pd.read_parquet(display/{tf}.parquet)        [读已写入的 parquet]
  → window_start 参数仅用于日志记录, 不参与数据截断 (注释: "不再需要截断！parquet 已经是 n_pts 条")
```

**关键差异**:
- 浏览模式：parquet 写入发生在 `_load_chart_data` 内部。
- 回测模式：parquet 由 `main()` 中前置的 `_sync_all_cascading()` 写入（line 1830），`_load_chart_data` 只读不写。
- 回测模式 parquet 不存在时，返回错误，不回退到 yfinance（注释 P1-4：防止破坏时间一致性）。

**防御性检查** (line 638-640):
```python
if t is None or len(t) < 2:
    st.caption("数据点不足, 无法渲染")
    return
```

**`cutoff_date` 的作用**:
- 回测 sql: `WHERE ts <= cutoff_date ORDER BY ts DESC LIMIT n_pts` — 取截止日期前的最近 n_pts 条。
- 通过 `_sync_all_cascading` 的级联合成逻辑，利用细粒度周期合成粗粒度周期的未完成 K 线。

**`window_start` 的作用**:
- 等于 `_bar_index`，仅用于日志记录（`log_data_load`）。
- **不参与**数据截断逻辑 — parquet 已由 `_sync_all_cascading` 预先处理。

### Step 2: `_date_markers()` — 日期刻度标记 (line 100-161)

**数据来源**: 实时计算

对日期索引取等间距位置作为 x 轴刻度。非核心逻辑，跳过详述。

### Step 3: 高周期 PnL 对齐 (line 645-652)

**数据来源标注**: `session_state._pnl_{_higher_tf}` (来自上一渲染周期)

```python
_higher_tf = TF_HIERARCHY.get(tf)          # e.g., "日线" → "周线"
_raw_higher = st.session_state.get(f"_pnl_{_higher_tf}")
if _raw_higher is not None:
    higher_pnl = _align_pnl_to_current_tf(
        _raw_higher["dates"], _raw_higher["long_pnl"], 
        _raw_higher["short_pnl"], _raw_higher["trade_records"], 
        dates,
    )
```

TF_HIERARCHY 定义 (`sidebar.py:100-104`):
```
1分钟 → 5分钟 → 15分钟 → 60分钟 → 日线 → 周线 → 月线 → 季线 → None
```

`_align_pnl_to_current_tf` (filter_engine.py:867-972) 做前向填充对齐：
- 对当前周期的每个 bar，找 `≤ bar时间戳` 的高周期最近 bar。
- 入场/离场 marker 映射到当前周期最近的 bar index。
- 时区处理：`_normalize_dates` 去时区，按挂钟时间对齐。

### Step 4: `_compute_filters()` (line 219-246)

**数据来源标注**: 实时计算 (np.ndarray 参数，不可缓存)

```python
sf = FILTERS.get(cfg["_fid"])
filtered = sf["func"](noisy, t, **cfg["pv"])
```

**FILTERS 字典** (filter_engine.py:304-368):

| filter_id | 名称 | 函数 | 关键参数 | 边界效应特征 |
|-----------|------|------|----------|------------|
| `sma` | 简单移动平均 | `apply_sma` | window | 前 window-1 个点为 NaN；滑动窗口截断 |
| `ema` | 指数移动平均 | `apply_ema` | span | 初始值等于第一个数据点；收敛需要 ~span 个点 |
| `wma` | 加权移动平均 | `apply_wma` | window | 类似 SMA，前 window-1 个点 NaN |
| `alma` | ALMA | `apply_alma` | window, offset, sigma | 前 window-1 个点 NaN |
| `savgol` | Savitzky-Golay | `apply_savgol` | window, order | **前 window//2 和后 window//2 个点受窗口边缘效应影响**，端点拟合不稳定 |
| `kalman` | 卡尔曼滤波 | `apply_kalman` | Q, R | 初始值等于第一个数据点；收敛需要 ~20-50 个点；新数据持续更新状态估计 |
| `butterworth` | 巴特沃斯 | `apply_butterworth` | order, cutoff | 零相位 (sosfiltfilt)，整体平滑；边界由双边滤波处理，无单边边缘效应 |
| `gaussian` | 高斯滤波 | `apply_gaussian` | sigma | 边界由 scipy 的 `mode='reflect'` 处理，两端有镜像填充 |
| `median` | 中值滤波 | `apply_median` | window | 前 window//2 和后 window//2 个点受窗口影响 |
| `lowess` | LOWESS | `apply_lowess` | frac | **每个点使用局部加权回归**，边界仅使用单侧邻域点，两端可靠性低于中间 |

**最关键的边界效应**:
- **Savitzky-Golay**: 窗口边界 (window//2) 个点处的拟合不可靠。回测中窗口右边界不断推进，右端 ~10 个点的滤波值在每帧可能不同。
- **Lowess**: 局部加权，右端数据点的权重非对称分布，新数据点加入会改变右端 ~frac*N 个点的拟合值。
- **Kalman**: 状态估计持续更新，右端滤波值随新观测进入而调整。
- **Butterworth**: 零相位滤波在添加新点后整个序列都重新计算，但两端变化最大。
- **SMA/EMA/WMA**: 滚动窗口型，只有右端 1-2 个点变化。

### Step 5: Info Captions (line 658-662)

**数据来源**: 实时计算

显示 ticker、最新价格、标准差、粗糙度、数据点数。

### Step 6: `_compute_schmitt_trigger()` (line 249-257)

**数据来源标注**: 实时计算 (np.ndarray 参数不可缓存)

```python
_v = np.gradient(filtered, t)     # 速度 = filtered 的一阶导数
_a = np.gradient(_v, t)           # 加速度 = filtered 的二阶导数
schmitt = _schmitt_trigger(_v, _a, ewma_span, k_eps, sigma_min)
```

**施密特触发器算法** (filter_engine.py:434-517):

```
输入: v(速度), a(加速度), ewma_span, k_eps, sigma_min

Step 1: EWMA 波动率估计 σ_t(v)
  alpha = 2/(ewma_span+1)
  mu_v[0] = v[0], sigma_v[0] = 0
  递推: mu_v[i] = alpha*v[i] + (1-alpha)*mu_v[i-1]
       sigma_v[i] = sqrt(alpha*(v[i]-mu_v[i])² + (1-alpha)*sigma_v[i-1]²)

Step 2: 自适应死区 ε_t = k_eps * max(σ_t(v), sigma_min)

Step 3: 滞回状态机
  状态 0 (观望):
    a > ε AND v > 0  →  +1 (多)
    a < -ε AND v < 0 →  -1 (空)
  状态 +1 (多):
    a < -ε           →   0 (退出)
  状态 -1 (空):
    a > ε            →   0 (退出)
```

**np.gradient 的边界效应**:
- `np.gradient` 使用二阶中心差分，两端使用前向/后向一阶差分。
- `_v[0]` 和 `_v[-1]` 精度低于中间点。
- `_a[0]`, `_a[1]`, `_a[-2]`, `_a[-1]` 精度低于中间点。
- **回测推进时，最后 2-3 个 bar 的 (v, a, sigma_v, eps, sig) 都会重新计算。**

**EWMA 的预热效应**:
- 需要 ~ewma_span 个点才能稳定。若 `len(t) < ewma_span`，函数返回 None。
- 短窗口 (< ewma_span) 下 sigma_v 偏小，导致 eps 等于 sigma_min 常量。

### Step 7: `_compute_prediction_pairs()` (line 260-276)

**数据来源标注**: 实时计算

```python
for pair_start, pair_end in all_pairs:
    if pair_end - pair_start >= 3:
        fit_result = fit_func(t, filtered, pair_start, pair_end)
```

- 支持两种拟合模式：`parabola`（物理抛物线 `_fit_physics_parabola`）和 `parabolic`（普通抛物线 `_fit_parabolic`）。
- 预测曲线**覆盖到价格子图 (row=1)** 上，分为：
  - 橙色实线 (拟合段: `pair_start` 到 `pair_end`)
  - 紫色虚线 (预测段: `pair_end` 到 `pair_end + n_extend`)
- `n_extend` 从 `cfg.get("n_ext", 10)` 取值，默认延申 10 个 bar。

### Step 8: `_compute_strategy_display()` (line 279-313)

**数据来源标注**: 实时计算；结果存入 `session_state._pnl_{tf}`

```python
long_pnl, short_pnl, trade_records = _compute_strategy_pnl(
    t, filtered, schmitt["sig"], all_pairs, pred_pairs, stop_loss_pct, n_extend
)
# 结果存入 session_state
st.session_state[f"_pnl_{tf}"] = {
    "dates": dates, "t": t,
    "long_pnl": long_pnl, "short_pnl": short_pnl,
    "trade_records": trade_records,
}
```

**PnL 计算逻辑** (filter_engine.py:649-860):

```
1. 初始化 long_pnl/short_pnl = 全 100.0
2. 遍历 all_pairs + pred_pairs:
   a. 判断交易方向: v2 == +1 AND pred_up → long; v2 == -1 AND NOT pred_up → short
   b. 入场点 = pair_end
   c. 出场扫描（分段混合）:
      - 预测保护期 [entry+1, entry+N_ext]: 止损 + 止盈双重检查
      - 趋势跟踪期 [entry+N_ext+1, ...]: 仅 Sig 反转止盈
   d. 填充持仓期间 PnL 曲线
   e. 记录 trade_records: id/type/entry_idx/exit_idx/return_pct/exit_reason
3. 前向填充非持仓期（水平直线）
4. 返回 (long_pnl, short_pnl, trade_records)
```

**跨周期 PnL 传递机制**:
```
高周期 (如周线) _render_chart()
  → _compute_strategy_display()
    → st.session_state["_pnl_周线"] = {dates, long_pnl, short_pnl, trade_records}

低周期 (如日线) _render_chart()
  → Step 3: _raw_higher = st.session_state.get("_pnl_周线")
  → _align_pnl_to_current_tf(周线_dates, 周线_long_pnl, 周线_short_pnl, 周线_trades, 日线_dates)
  → 结果存入 higher_pnl 变量
```

**注意**: 只有 PnL **写入** session_state 的操作发生在 Step 8。读取发生在下一个视图的 Step 3。因此视图渲染顺序很重要——`main()` 按 TF 降序排序 (`ALL_TFS.index` 升序 → 从细到粗？不，是 `reverse=True`)。

**实际上**: `sorted_views = sorted(enumerate(configs), key=lambda x: ALL_TFS.index(x[1]["tf"]), reverse=True)`

这意味着 **粗粒度周期（如周线）先渲染**，细粒度周期（如日线）后渲染。这确保了：渲染周线时写入 `_pnl_周线`，渲染日线时已可读到 `_pnl_周线`。

### Step 9-10: 子图布局与构建 (line 316-353, 699-735)

**8 种布局组合**（由 `has_s`, `has_strategy`, `has_cross`, `has_alignment` 决定）:

| sch | strategy | cross | align | rows | 子图列表 |
|-----|----------|-------|-------|------|---------|
| 0 | - | - | - | 4 | 价格、残差、速度v、加速度a |
| 1 | 0 | - | - | 5 | + a±ε、Sig |
| 1 | 1 | 0 | - | 6 | + PnL收益(%) |
| 1 | 1 | 1 | 0 | 7 | + 高周期PnL参考 |
| 1 | 1 | 1 | 1 | 8 | + 同向性判断 |

**row 索引分配**:
- mr=1: 价格&滤波 (始终)
- rr=2: 残差
- vr=3: 速度 v
- sar=4/None: a±ε 死区
- ssr=5/None: Sig 信号
- ar=4/None (无 sch 时): 加速度 a
- pnl_row=6/None: PnL 收益
- cross_row=7/None: 高周期 PnL 参考
- align_row=8/None: 同向性判断

### Step 11: 最终布局 (line 736-763)

- **颜色主题**: `plotly_dark`
- **高度计算**: 基础 420/620 (compact)，加 120 (cross PnL)，加 75 (alignment)
- **X 轴**: 日期刻度标记，tickfont 9px
- **Y 轴**: 各子图独立 label
- **Sig 子图**: tickvals=[-1, 0, 1], ticktext=["空", "观", "多"], range=[-1.5, 1.5]

---

## 2. charts.py 可视化函数分析

### `_render_plotly()` (line 17-202)

**数据来源标注**: 实时传入的 Plotly Figure 对象

**渲染机制**:
1. 将 Plotly Figure 序列化为 JSON (`fig_dict`)，处理 numpy 数组转换为 list。
2. 递归 `_sanitize_for_json` 将 NaN/Inf 替换为 None。
3. 生成独立 HTML 页面，嵌入 Plotly.js CDN (2.35.2，带 fallback)。
4. 通过 `st.components.v1.html()` 渲染。

**JavaScript 交互层**:
- 跨子图十字线：`plotly_hover` 事件 + `Plotly.relayout` 移动各子图的 `shapes`。
- 日期提示 (date-tip)：hover 时显示最近 x 坐标对应的日期。
- 45ms 节流 (THROTTLE_MS) 防抖。
- 5s 超时回退：Plotly.js 加载失败时显示错误信息。

**dates 参数**: 存入 `fig_dict["layout"]["_dates"]`，供 JS 层日期提示使用。

### `_add_prediction_traces()` (line 208-256)

- 拟合段：橙色实线 (`#f0a040`), row=mr (价格子图)
- 预测段：紫色虚线 (`#a371f7`), 从 pair_end 延申 n_extend 个 bar
- 残差：row=mr+1 (残差子图), 朝向判定：向上预测→红色，向下预测→绿色

### `_add_cross_pnl_subplot()` (line 351-397)

**入场标记 (entry_markers)**: 金色三角 (▲, `triangle-up`)

含义：高周期在对应 bar 位置**开仓**（多或空）。hovertext 显示 `"高周期入场 多/空"`

**离场标记 (exit_markers)**: 金色标记 + 盈亏标注

- 止损：× 符号，红色边框
- 止盈：O 符号，绿色边框
- 标注文字：箭头方向 + 盈亏百分比 (e.g., `↑+3.2%`)

**参考线**:
- 做多 PnL：绿色虚线 (`#3fb950`, dash='dot')
- 做空 PnL：红色点线 (`#f85149`, dash='dot')
- 100 基准线

### `_add_alignment_subplot()` (line 399-461)

**同向性判断逻辑**:
- `long_mask[i] == True`: 高周期做多持仓中 → 低周期的 PnL 变化被 sample（累乘）
- `long_mask[i] == False`: 高周期非持仓 → 低周期 PnL 被 hold（水平线）
- 同理 `short_mask` 对应做空持仓

**视觉效果**:
- 高周期持仓期间，低周期 PnL 的涨跌被放大（粗线 3px）
- 非持仓期间为水平直线（1.5px）
- 入场标记仅在持仓开始时 (mask[entry_i]==True)
- 离场标记仅在持仓结束时 (mask[exit_i]==True)

### `_add_schmitt_traces()` (streamlit_app.py:391-425)

**填充色和配对色带**:
- `±ε` 包络：灰色半透明 (`rgba(128,128,128,0.06)`)
- Sig=+1 区域：绿色半透明填充 (`rgba(63,185,80,0.06)`)
- Sig=-1 区域：红色半透明填充 (`rgba(248,81,73,0.06)`)
- Pair 色带：交替蓝紫 (`rgba(88,166,255,0.10)` / `rgba(163,113,247,0.10)`)

### `_add_pnl_traces()` (streamlit_app.py:428-468)

- 做多 PnL 曲线：绿色实线 (`#3fb950`)
- 做空 PnL 曲线：红色实线 (`#f85149`)
- 每笔交易分段高亮 (3px 粗线)
- 入场标记：三角 (`triangle-up`)
- 止损离场：× 符号，红色，标注 `↑/↓-X.X%`
- 止盈离场：O 符号，绿色，标注 `↑/↓+X.X%`
- 区域背景：绿/红半透明上下填充

---

## 3. 动态更新机制分析

### 3.1 回测模式 "前进一个 bar" 的完整流程

```
用户点击 ▶ (⏵) 按钮
  → st.session_state._bar_index = bar_index + 1
  → _update_cutoff_and_rerun()
    → cutoff_date = _get_bar_date_from_db(ticker, min_tf, bar_index - 1)
    → AppState.set("_bt_cutoff_date", cutoff_date)
    → st.rerun()
```

**st.rerun() 后的执行流程**:

```
main() 入口
  ├── _run_backtest_play() → False (非播放模式)
  ├── _get_db_connection() [cache_resource, 命中]
  ├── init_config_tables()
  ├── AppState.init_defaults() [已存在, 跳过]
  ├── Sidebar 渲染 (市场选择、参数面板等)
  ├── cb_mode = True
  ├── window_start = st.session_state.get("_bar_index")
  ├── cutoff_date = AppState.get("_bt_cutoff_date")
  ├── ▸ _sync_all_cascading(ticker_code, tfs, cutoff_date, min_tf, n_pts)
  │     [一次性重写所有 TF 的 parquet 文件]
  │     ├── 每个 TF: DB 查询 → K 线合成判断 → 聚合 → 写 parquet
  │     └── 返回 {tf: ok/not_ok}
  │
  └── 2×2 图表网格 (TF 降序: 粗→细)
       ├── 周线 _render_chart_fragment()
       │    ├── _load_chart_data: 读 parquet → 新数据窗口
       │    ├── _compute_filters: 重新计算 → 右端值改变
       │    ├── _compute_schmitt_trigger: 重新计算 → sig 右端改变
       │    ├── _compute_prediction_pairs: 重新拟合
       │    ├── _compute_strategy_display: 重新计算 PnL
       │    │   └── st.session_state["_pnl_周线"] = {...}  ← 写入
       │    └── 构建 + 渲染图表 (11 steps)
       │
       ├── 日线 _render_chart_fragment()
       │    ├── _load_chart_data: 读 parquet
       │    ├── ... (同上)
       │    ├── Step 3: higher_pnl = st.session_state.get("_pnl_周线")  ← 读周线 PnL
       │    ├── _compute_strategy_display
       │    │   └── st.session_state["_pnl_日线"] = {...}  ← 写入
       │    └── 构建 + 渲染
       │
       ├── 60分钟 ... (读 _pnl_日线)
       └── 15分钟 ... (读 _pnl_60分钟)
```

### 3.2 重算范围总表

下表标注了前进一个 bar 后**必定重算**的环节和**可能不变**的环节：

| 环节 | 重算? | 原因 |
|------|------|------|
| `_sync_all_cascading` (parquet 写入) | **是** | cutoff_date 改变 → SQL 查询结果不同 |
| `_load_chart_data` (parquet 读取) | **是** | parquet 文件内容已更新 |
| `_compute_filters` | **是** | 输入 noisy 变化 → 所有滤波值重算 |
| `_compute_schmitt_trigger` | **是** | filtered 变化 → v/a/eps/sig 重算 |
| `_compute_prediction_pairs` | **是** | 可能触发新的 pair, 信号右端变化 |
| `_compute_strategy_display` | **是** | 所有 PnL 曲线和 trade_records 重算 |
| `session_state._pnl_*` 写入 | **是** | 每次覆盖 |
| Plotly Figure 构建 | **是** | 所有 trace 从零构建 |
| `_render_plotly` (HTML 输出) | **是** | Figure 对象变化 |
| `_cached_fetch_stock` (yfinance) | **否** | 仅在浏览模式 parquet 写失败时触发，且 TTL=3600s |

### 3.3 缓存机制

| 缓存装饰器 | 位置 | 内容 | 失效条件 |
|-----------|------|------|---------|
| `@st.cache_data(ttl=3600)` | streamlit_app.py:65 | `_cached_fetch_stock()` — yfinance API 调用结果 | TTL 超时 (1h) 或手动 `st.cache_data.clear()` |
| `@st.cache_resource` | streamlit_app.py:767 | `_get_db_connection()` — DB 连接初始化 | 应用重启 |
| `@st.cache_data(ttl=3600)` | streamlit_app.py:820 | `_stock_name()` — 股票名称查询 | TTL 超时 |

**未缓存的计算** (注释明确说明 "Not cached via @st.cache_data because params include unhashable np.ndarray"):
- `_compute_filters` — 包含 ndarray 参数
- `_compute_schmitt_trigger` — 包含 ndarray 参数

**实际效果**: 回测每前进一个 bar，除 yfinance API 调用外，**所有计算全部重新执行**。

### 3.4 session_state 跨渲染周期数据

| Key | 类型 | 写入位置 | 读取位置 | 含义 |
|-----|------|---------|---------|------|
| `_pnl_{tf}` | dict | `_compute_strategy_display()` | `_render_chart()` Step 3 | 各周期 PnL 曲线 + 交易记录 |
| `_bar_index` | int | 回测导航按钮 / `_run_backtest_play` | main() 获取 `window_start` | 当前窗口结束 bar 位置 |
| `_cb_mode` | bool | `_render_backtest_mode()` | main() 多处 | 是否回测模式 |
| `_bt_cutoff_date` | str | `_update_cutoff_and_rerun()` | main() + `_sync_all_cascading` | 回测截止日期 |
| `_min_tf` | str | `_render_backtest_mode()` | 多处 | 当前最细粒度周期 |
| `_min_tf_bar_count` | int | `_render_backtest_mode()` | `_render_backtest_nav()` | 最小周期总 bar 数 |
| `_is_playing` | bool | 播放按钮 / `_run_backtest_play` | 多处 | 是否自动播放中 |
| `_play_speed` | float | 速度选择器 | `main()` 末尾 sleep 计算 | 播放速度倍率 |
| `_day_offset` | int | `_render_time_nav()` | `_load_chart_data()` | 浏览模式时间偏移 |
| `v{i}_{param}` | various | `ViewState.set()` / sidebar widgets | `ViewState.get()` / `build_cfg()` | 视图参数 |
| `_bt_slider_pos` | int | Slider widget | `_on_slider_change()` | Slider 值（独立于 _bar_index） |

### 3.5 `@st.fragment` 的作用

`_render_chart_fragment` 被 `@st.fragment` 装饰 (line 545):
- 每个视图（v0..v3）可独立重渲染，不触发其他视图或侧边栏的重渲染。
- **回测模式下**: `st.rerun()` 是全局调用（发生在 main 尾部或 `_update_cutoff_and_rerun`），会触发**所有**视图的 fragment 重渲染。所以 fragment 在回测中失去了局部性优势，4 个视图全部重算。
- **浏览模式下**: sidebar widget 变化只触发对应 widget 的 rerun，fragment 仍然独立。

### 3.6 自动播放机制

`_run_backtest_play()` (line 1373-1412):
```
若 _is_playing == True AND bar_index < total:
  1. st.session_state._bar_index += 1 (在 widget 渲染前)
  2. 更新 _bt_cutoff_date
  3. 返回 True

main() 末尾 (line 1864):
  if need_rerun:
    time.sleep(1.0 / speed)
    st.rerun()
```

播放期间 slider 被替换为 progress bar (line 1523-1525)，避免 widget key 与 `_run_backtest_play` 的递增冲突。

---

## 4. 数据来源标注总表

每个渲染步骤的数据来源：

| Step | 函数 | 数据来源 | 存储介质 |
|------|------|---------|---------|
| 1 | `_load_chart_data` | t, noisy, ohlc, dates | parquet (data/display/{tf}.parquet), 根源：DB kline 表 |
| 2 | `_date_markers` | dates | 实时计算 |
| 3 | 高周期 PnL 对齐 | `st.session_state._pnl_{higher_tf}` | `session_state` (上一帧写入) |
| 4 | `_compute_filters` | filtered, filtered2 | 实时计算 (输入: parquet 数据) |
| 5 | Info captions | noisy, filtered | 实时计算 |
| 6 | `_compute_schmitt_trigger` | schmitt 字典 | 实时计算 (输入: filtered) |
| 7 | `_compute_prediction_pairs` | pred_pairs | 实时计算 (输入: filtered, schmitt) |
| 8 | `_compute_strategy_display` | long_pnl, short_pnl, trades | 实时计算；结果写入 `session_state._pnl_{tf}` |
| 9 | `_determine_subplot_layout` | 布局参数 | 实时计算 (由 bool 开关决定) |
| 10 | Plotly Figure 构建 | fig 对象 | 实时构建 (所有 trace 从零添加) |
| 11 | `_render_plotly` | HTML 输出 | 实时渲染 (Figure JSON → HTML) |

---

## 5. 可能导致"上一帧与下一帧信号不同"的渲染环节

以下是回测中前进一个 bar 后，导致 **"上一帧看到的信号（如 Sig 值、PnL 数值）与下一帧不同"** 的关键环节，按影响程度排序：

### A. 滤波边界效应 (影响程度: 高)

**根本原因**: 新数据点进入窗口右端，旧数据点从左端移出，滤波器的端点行为导致**右端 2-20 个点的滤波值变化**。

| 滤波器 | 右端受影响范围 | 机制 |
|--------|-------------|------|
| Savitzky-Golay | ~window/2 (~10 个点) | 滑动窗口多项式拟合在边界不稳定 |
| Lowess | ~frac*N (~12 个点 for frac=0.1, N=120) | 局部加权，新点改变邻域权重分布 |
| Butterworth | 整个序列 (但两端最大) | 零相位滤波 (sosfiltfilt) 的双向处理 |
| Kalman | 1 个点后收敛 | 状态估计对最新观测的响应 |
| EMA | 1-2 个点 | 指数衰减 |
| SMA/WMA | 1 个点 | 新值替换滑动窗口最旧值 |

**影响链路**: filtered 变化 → v (gradient) 变化 → a (gradient) 变化 → sigma_v (EWMA) 变化 → eps 变化 → sig_t 变化

### B. np.gradient 端点近似 (影响程度: 中)

`np.gradient` 在右端使用一阶后向差分而非中心差分：
```python
# 中心差分: (f[i+1] - f[i-1]) / (t[i+1] - t[i-1])  ← 精度 O(h²)
# 后向差分: (f[-1] - f[-2]) / (t[-1] - t[-2])       ← 精度 O(h)
```

-- 最后 1 个 bar 的 v 精度为 O(h)，中间点为 O(h²)
-- 最后 2 个 bar 的 a 精度为 O(h)，中间点为 O(h²)
-- 回测推进时，上一帧的右端变为当前帧的中间区域，精度提升 → 信号值可能翻转

### C. PnL 曲线末尾值变化 (影响程度: 中)

`_compute_strategy_pnl` 中 PnL 曲线基于 filtered 价格计算：
- 新数据可能导致已有的交易记录的离场点变化（Sig 信号变化触发止盈）
- PnL 曲线末尾值 (最后一个 bar) 随新数据改变
- 新数据可能触发新交易对（all_pairs 增加或变化）

### D. Schmitt Trigger 滞回状态 (影响程度: 中)

滞回状态机的 `current_state` 从过去继承：
- 信号右端 epsilon 值变化可能导致中部某个 bar 的 sig 翻转
- 这个翻转会通过滞回传播到后续所有 bar
- **一个 bar 的 sig 变化可导致整条 sig_t 序列的 Cascade**

### E. Prediction Curve 拟合变化 (影响程度: 低-中)

`all_pairs` 可能变化 (pair 起止点或数量变化)：
- 新的 pair → 新的拟合曲线
- 拟合参数 (a, b, c) 变化 → 预测方向可能翻转
- 预测方向翻转 → 交易方向判定翻转 (long/short)
- `pred_pairs` 列表的 pair_end → PnL 计算中 `pred_map` 的 key 变化

### F. 跨周期 PnL 传递 (影响程度: 低)

`_compute_strategy_display` 按条件将 PnL 写入 session_state：
```python
if has_strategy and trade_records:  # 注意: 需要 trade_records 非空
    st.session_state[f"_pnl_{tf}"] = {...}
```

**潜在问题**: 如果某帧 `has_strategy` 为 True 但 `trade_records` 为空（如窗口内无交易机会），则**不写入** session_state。下一个视图读到的仍是旧值。

### G. _sync_all_cascading 的 K 线合成 (影响程度: 低)

回测模式下 cutoff_date 改变：
- 级联合成可能产生或不产生合成 bar
- 某帧有合成 bar（多一条），下一帧没有（正好到完整周期边界）
- 数据条数变化直接影响滤波/Schmitt/PnL 的全部计算

---

## 附录: 关键代码路径索引

| 组件 | 文件 | 行号 |
|------|------|------|
| `_render_chart()` | streamlit_app.py | 580-763 |
| `_load_chart_data()` | streamlit_app.py | 163-216 |
| `_compute_filters()` | streamlit_app.py | 219-246 |
| `_compute_schmitt_trigger()` | streamlit_app.py | 249-257 |
| `_compute_prediction_pairs()` | streamlit_app.py | 260-276 |
| `_compute_strategy_display()` | streamlit_app.py | 279-313 |
| `_determine_subplot_layout()` | streamlit_app.py | 316-353 |
| `_add_main_price_traces()` | streamlit_app.py | 356-370 |
| `_add_residual_traces()` | streamlit_app.py | 373-388 |
| `_add_schmitt_traces()` | streamlit_app.py | 391-425 |
| `_add_pnl_traces()` | streamlit_app.py | 428-468 |
| `_render_chart_fragment()` | streamlit_app.py | 546-577 |
| `_update_cutoff_and_rerun()` | streamlit_app.py | 1250-1275 |
| `_on_slider_change()` | streamlit_app.py | 1278-1306 |
| `_render_backtest_nav()` | streamlit_app.py | 1309-1370 |
| `_run_backtest_play()` | streamlit_app.py | 1373-1412 |
| `_render_backtest_mode()` | streamlit_app.py | 1415-1538 |
| `main()` | streamlit_app.py | 1710-1868 |
| `_cached_fetch_stock()` | streamlit_app.py | 66-92 |
| FILTERS 注册表 | filter_engine.py | 304-368 |
| `_schmitt_trigger()` | filter_engine.py | 434-517 |
| `_find_all_pairs()` | filter_engine.py | 520-578 |
| `_fit_parabolic()` | filter_engine.py | 579-607 |
| `_fit_physics_parabola()` | filter_engine.py | 608-648 |
| `_compute_strategy_pnl()` | filter_engine.py | 649-860 |
| `_align_pnl_to_current_tf()` | filter_engine.py | 867-972 |
| `_compute_holding_masks()` | filter_engine.py | 975-1020 |
| `_render_plotly()` | charts.py | 17-202 |
| `_add_prediction_traces()` | charts.py | 208-256 |
| `_render_entry_marker()` | charts.py | 262-275 |
| `_render_exit_marker_with_label()` | charts.py | 278-307 |
| `_render_pnl_curves()` | charts.py | 310-326 |
| `_render_baseline()` | charts.py | 329-333 |
| `_render_fill_background()` | charts.py | 335-345 |
| `_add_cross_pnl_subplot()` | charts.py | 351-397 |
| `_add_alignment_subplot()` | charts.py | 399-461 |
| `_sync_to_display()` | data_loader.py | 174-226 |
| `_sync_all_cascading()` | data_loader.py | 735-816 |
| `AppState` | state.py | 96-181 |
| `TF_HIERARCHY` | sidebar.py | 100-104 |
| `ALL_TFS` | sidebar.py | 13 |
