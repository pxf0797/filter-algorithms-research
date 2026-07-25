# Streamlit + Plotly 性能最佳实践 — 对照 filter_research 现状

> 调研日期: 2026-07-11
> 文档来源: Context7 检索 Streamlit 官方文档 + Plotly.py 官方文档 + plotly-resampler
> 目标: 对照 filter_research 代码现状，给出可落地的性能优化建议

---

## 一、Streamlit 官方最佳实践要点

### 1.1 `@st.cache_data` vs `@st.cache_resource`

| 维度 | `@st.cache_data` | `@st.cache_resource` |
|------|-------------------|-----------------------|
| 适用场景 | 返回序列化数据（DataFrame、np.array、str、int、list） | 返回非序列化资源（DB连接、ML模型、类实例） |
| 每次调用 | 创建数据副本（安全防突变） | 返回同一实例 |
| 推荐用途 | `pd.read_csv()`、API请求、NumPy运算 | `init_db()`、`load_model()` |

**关键点**:
- `@st.cache_data` 适用于所有返回序列化数据的函数,自动处理哈希
- NumPy 数组需要 `hash_funcs={np.ndarray: str}` 或更高阶的自定义哈希函数来正确处理
- `ttl` 参数设置过期时间,适合定时刷新的场景

### 1.2 `@st.fragment` — 局部重跑

- `@st.fragment` 装饰的函数内的 widget 交互**只触发该片段重跑**,不触发整页 rerun
- 片段内的 `st.rerun()` 可以手动触发**完整脚本 rerun**（当需要全局状态同步时）
- 对 `st.caption`、`st.columns`、`st.container` 等输出元素不会触发 rerun
- 所有**交互式 widget**（`st.button`、`st.slider`、`st.selectbox`、`st.checkbox`）在 fragment 内部触发 fragment-scoped rerun

**典型模式**: 将数据和计算逻辑放入 `@st.cache_data`,将 UI 交互放入 `@st.fragment`,二者结合实现增量更新。

### 1.3 `st.session_state` 缓存

- 跨 rerun 持久化计算结果,避免重新计算
- `on_click` / `on_change` 回调在 rerun 前执行,可在此修改 session_state
- `st.form_submit_button(on_click=...)` 回调模式: 回调中读 `st.session_state` 获取提交值

### 1.4 Widget 全量重算问题

- 所有 widget 的 `on_change` 会在用户交互时立即触发
- 无缓存的 widget 绑定参数变化 → 全部下游逻辑重新运行
- 缓存的正确分层: **数据获取 → 计算逻辑 → UI 渲染**逐层缓存,上层的参数变化不会穿透到全缓存层

---

## 二、Plotly 官方最佳实践要点

### 2.1 WebGL (Scattergl) — 大图渲染

| Trace 类型 | 渲染方式 | 适用场景 | 最大数据量 |
|------------|----------|---------|-----------|
| `go.Scatter` | SVG (CPU) | <10K 点,需要精确矢量 | 约 10万 点变慢 |
| `go.Scattergl` | WebGL (GPU) | >10K 点,大量 trace | 约 100万 点流畅 |
| `go.Candlestick` | SVG (CPU) | K线图 | <5000 K线 |

- `Scattergl` 使用 WebGL GPU 加速,渲染 100 万点仍保持交互流畅
- 支持 `mode='lines'`、`mode='markers'`、`mode='lines+markers'`,与 Scatter API 高度一致
- 不适合填充（`fill='toself'`）和复杂文本标记,这类场景仍需 `go.Scatter`

### 2.2 Trace 数量控制

- **渲染性能瓶颈**: 总数据点 × trace 数量 × 子图数量
- 每个 trace 在 Plotly.js 内部有独立的对象管理开销
- 50 个以上 trace 即使数据点少也会明显变慢
- 合并 trace 策略: 用 `NaN` 分隔不连续线段,合并为单一 trace 而非 N 个独立 trace

### 2.3 序列化优化

- 所有 Plotly figure 在渲染前必须 JSON 序列化
- **安装 `orjson`** (`pip install orjson`) 可获得 5-10x 的序列化加速
- Plotly 自动检测并使用 orjson,无需代码改动

### 2.4 大型时间序列 — plotly-resampler

- [plotly-resampler](https://github.com/predict-idlab/plotly-resampler) 适配大时间序列
- 在缩放/平移时**动态聚合数据**,只发送当前视口的代表性子集到浏览器
- 聚合器: MinMaxLTTB（兼顾视觉保真度和速度）、EveryNthPoint（最高速）、LogLTTB（对数轴）
- 示例: 5000 万点 → 1000 点（缩放时）+ 500 点（固定显示）
- `FigureResampler` 包裹 Plotly figure,自动管理聚合回调

### 2.5 `st.plotly_chart` vs 自定义 HTML

- `st.plotly_chart` 使用 Streamlit 内置的 websocket 传输序列化后的 figure JSON,由前端自动渲染
- 自定义 `st.components.v1.html` 生成完全独立的 iframe,Plotly.js 需从 CDN 加载
- `st.plotly_chart` 优势: 不需要管理 Plotly.js CDN 加载,与 Streamlit 事件系统集成,支持 `use_container_width`
- 自定义 HTML 优势: 可嵌入任意 JS（如自建 crosshair）,完全控制 DOM/样式
- 序列化开销层面两者无本质差异——`st.plotly_chart` 也是 JSON 序列化后传输

---

## 三、filter_research 现状对照

### 3.1 Streamlit 缓存使用

| 当前做法 | 位置 | 状态 |
|---------|------|------|
| `@st.cache_data` on `_cached_fetch_stock` | `streamlit_app.py:66` | ✅ 正确使用了 `show_spinner=False, ttl=3600` |
| `@st.cache_data` on `_stock_name` | `streamlit_app.py:866` | ✅ 适合 yfinance Ticker.info 缓存 |
| `@st.cache_resource` on `_get_db_connection` | `streamlit_app.py:813` | ✅ 正确用于 DB 连接 |
| `@st.fragment` on `_render_chart_fragment` | `streamlit_app.py:581` | ⚠️ 有装饰但无实际收益(详见下) |
| `_compute_filters` 无缓存 | `streamlit_app.py:220` | ❌ 每次 rerun 重新计算滤波 |
| `_compute_schmitt_trigger` 无缓存 | `streamlit_app.py:250` | ❌ 每次 rerun 重新计算 |
| `_compute_prediction_pairs` 无缓存 | `streamlit_app.py:261` | ❌ 每次 rerun 重新计算 |
| `_compute_strategy_display` 无缓存 | `streamlit_app.py:280` | ❌ 每次 rerun 重新计算 |

### 3.2 `@st.fragment` 实际收益分析

**代码结构**:
```
main()                          ← 整页 rerun
├── _render_param_panels()      ← sidebar widget 触发整页 rerun
└── 2x2 grid
    └── _render_chart_fragment  ← @st.fragment
        └── _render_chart()     ← 只有 st.caption() / st.components,无输入 widget
```

**判断: `@st.fragment` 当前无实际优化收益。**

原因:
1. `_render_chart_fragment` 内部没有交互式 widget（`st.button`、`st.slider` 等）——触发 rerun 的唯一方式是 fragment 外部（sidebar）的 widget 交互
2. 外部 widget 交互触发 `main()` 整页 rerun,此时 fragment 也会被重新执行
3. 回测播放模式下,`main()` 中 `st.rerun()` 触发整页 rerun,4个 fragment 全部重建

`@st.fragment` 只有在以下场景才有收益: **片段内部有用户可交互的 widget,且这些交互不需要同步到整页其他部分**。当前代码不符合此条件。

### 3.3 Plotly 渲染分析

#### `_render_plotly` 自建 HTML 渲染

- 文件: `components/charts.py:19`
- 方案: 手工序列化 `fig.to_plotly_json()` → JSON → CDN 加载 Plotly.js 2.35.2 → `st.components.v1.html`
- 额外逻辑: 跨子图 crosshair（自定义 JS）+ NaN/Inf 清理（`_sanitize_for_json`）+ NP encoder

**对比 `st.plotly_chart`** 的取舍:

| 维度 | 自建 HTML | `st.plotly_chart` |
|------|-----------|-------------------|
| 序列化 | 自定 encoder + sanitize | 内置,支持 orjson 加速 |
| Plotly.js | CDN 加载(~2MB) | Streamlit 前端内置 |
| Crosshair | 自建 JS(200行) | 需第三方 or 自建 |
| iframe | 完整独立 HTML | Streamlit 管理的 iframe |
| 灵活性 | 完全控制 | 受 Streamlit 渲染器约束 |

核心取舍: 自建 crosshair 是保留自定义渲染的唯一理由。`_sanitize_for_json` 在 JSON 序列化过程中有递归开销。

#### Trace 类型全用 `go.Scatter(SVG)`

**所有图表 trace 都是 `go.Scatter`（SVG 渲染）,没有任何 `go.Scattergl` 使用。**

#### 单视图 Trace 数量估计

以最复杂的配置（show_sch + show_strategy + show_cross + show_alignment = 8 行子图）为例:

| 来源 | trace 数量 | 说明 |
|------|-----------|------|
| K线 | 1 | `go.Candlestick`（SVG 必须） |
| 收盘价 | 1 | `go.Scatter` |
| 滤波(1~2) | 1-2 | `go.Scatter` |
| 预测曲线 | 2-3 × N 对 | N=预测段数,通常 5-20,即 10-60 |
| 残差 | 1 | `go.Scatter` |
| 速度 | 1 | `go.Scatter` |
| 加速度 | 1 | `go.Scatter` |
| ±ε band | 1(fill) | `go.Scatter` fill |
| ±ε 线 | 2 | `go.Scatter` |
| σ(v) | 1 | `go.Scatter` |
| a(施密特) | 1 | `go.Scatter` |
| Sig | 1 | `go.Scatter` |
| Sig fill | 1-2 | `go.Scatter` fill |
| Pair bands | N 对 | `go.Scatter` fill,通常 5-20 |
| PnL 长/短线 | 2 | `go.Scatter` |
| 单笔交易段 | N 笔 | `go.Scatter`,通常 10-50 |
| 入场标记 | N 笔 | `go.Scatter` markers |
| 离场标记 | N 笔 | `go.Scatter` markers |
| PnL 填充 | 2 | `go.Scatter` fill |
| 高周期持仓 | 2 | shapes(非 trace) |
| 同向性曲线 | N 笔 + markers | `go.Scatter` |
| 实际持仓 | 2 bands | shapes(非 trace) |

**单视图总计: 40-150+ 个 SVG trace（含 shapes 则更多）。**

4 个视图同时渲染时,页面需要管理 **160-600+ 个 SVG trace**——这是性能瓶颈的核心来源。

### 3.4 差距汇总表

| 最佳实践 | 当前状态 | 差距程度 |
|---------|---------|---------|
| `go.Scattergl` 替代 `go.Scatter` | 100% go.Scatter(SVG) | **严重** |
| Trace 合并（NaN 间隔） | 每段独立 trace | **严重** |
| 计算缓存（filter/schmitt/prediction） | 无缓存 | **高** |
| Fragment 实际利用率 | 装饰但无 widget 在内 | **中** |
| orjson 加速 | 未安装 | **中** |
| plotly-resampler（大数据集） | 未使用 | **低-中** |
| `st.plotly_chart` vs 自建 HTML | 自建（合理） | 低（保留） |
| 缓存 NumPy 计算 | `_compute_filters` 未缓存 | 中 |

---

## 四、可落地建议清单（按收益排序）

### [P0] 建议 1: go.Scatter → go.Scattergl 迁移

**改什么**:
将以下 `go.Scatter` 改为 `go.Scattergl`（代码位置见下方）:

| 文件 | 函数 | 行号 | trace 类型 | 适合 Scattergl? |
|------|------|------|-----------|-----------------|
| `streamlit_app.py` | `_add_main_price_traces` | 382-389 | 收盘价/滤波线 | ✅ 纯线条 |
| `streamlit_app.py` | `_add_residual_traces` | 397-403 | 残差/速度 | ✅ 纯线条 |
| `streamlit_app.py` | `_add_schmitt_traces` | 414-427 | ε/a/Sig/σ(v) | ✅ 纯线条(Sig 的 hv shape 需测试) |
| `streamlit_app.py` | `_add_pnl_traces` | 449-486 | PnL 曲线/段/marker | ✅ 线和标记 |
| `streamlit_app.py` | `_add_alignment_subplot` | 429-435 | 同向曲线 | ✅ 纯线条 |
| `components/charts.py` | `_render_pnl_curves` | 317-328 | PnL 基准线 | ✅ 纯线条 |
| `components/charts.py` | `_add_prediction_traces` | 221-258 | 预测曲线/残差 | ⚠️ 需测试 fill |
| `components/charts.py` | `_render_entry_marker/_render_exit_marker` | 269-298 | 标记点 | ✅ markers |

**必须保留 `go.Scatter` 的场景**:
- `go.Candlestick`（K线）— 无 WebGL 替代
- `fill="toself"` 的填充区域（±ε band、pair bands、Sig fill、PnL fill）
- Annotation（text 标记）

**预期收益**:
- 单视图 trace 渲染延迟降低 **60-80%**（SVG→WebGL GPU）
- 4 视图同时渲染时交互流畅度显著提升
- 数据点越多提升越明显（1000+ 点/图的场景加速 5-10x）

**风险**:
- `Scattergl` 不支持 `shape="hv"`（Sig 信号线）— 需保留为 Scatter 或改用 step 模式
- `Scattergl` 对 fill 支持有限 — 填充区域保留 Scatter
- 个别样式差异可能影响 visual QA
- 修正方法: 逐 trace 替换,不需保留 Scatter 的才改,保留需要 SVG 特性的

**改造方案（最小化风险）**:
1. 先替换纯线条 trace（收盘价、滤波线、残差、速度、PnL曲线、预测线）
2. 标记点（entry/exit marker）可换 Scattergl
3. 填充区域（bands/fill）保留 Scatter
4. Sig 信号先测试 Scattergl 的 step 模式兼容性,不行则保留 Scatter

**实现难度**: 低。API 几乎完全相同,主要是类名替换。

---

### [P1] 建议 2: 计算缓存 — 增加 filter/output/schmitt/prediction 缓存

**改什么**:

**(a) `_compute_filters` 增加 `@st.cache_data`**

```python
# 现状: streamlit_app.py:220 — 无缓存
def _compute_filters(noisy, t, cfg):
    ...

# 建议: 增加缓存
@st.cache_data(hash_funcs={np.ndarray: lambda arr: hash(arr.tobytes())})
def _cached_compute_filters(noisy_bytes, t_bytes, cfg_hash):
    """Cache filter output by input hash."""
    noisy = np.frombuffer(noisy_bytes)
    t = np.frombuffer(t_bytes)
    ...
```

问题: cfg 包含 `np.ndarray` 参数 → 直接 hash 不可行。替代方案:
- 用 `cfg` 的快照 key（`f"{cfg['_fid']}_{cfg['pv']}"` 的序列化字符串 + noisy 的 `tobytes()` hash）
- 或使用 `st.cache_data(hash_funcs={np.ndarray: hash_from_bytes})`

**(b) `_compute_schmitt_trigger` / `_compute_prediction_pairs` 按输入哈希缓存**

**(c) PnL 结果缓存**: 基于 filter_output + schmitt + pairs 的确定性输入

**预期收益**:
- 同一次数据加载、参数不变时,多次 rerun 跳过全部重算
- 消除 100-500ms 的重复计算（取决于 n_pts 和 pair 数量）
- 在时间窗口偏移（day_offset）场景下,同组参数不重复计算

**风险**:
- 需要处理 **确定性输入 → 输出哈希**的映射
- 缓存键设计稍复杂（np.array bytes + 参数字典 JSON）
- 缓存未命中时需回退到即时计算
- 调试缓存一致性问题（stale data 可能比 rerun 慢更隐蔽）

---

### [P2] 建议 3: Trace 合并 — 单笔交易段用 NaN 间隔合并

**改什么**:

`_add_pnl_traces`（`streamlit_app.py:447-487`）当前每笔交易生成独立的 `go.Scatter` trace:
```python
for trade in trade_records:
    seg_t = t[trade["entry_idx"]:trade["exit_idx"] + 1]
    seg_pnl = curve[...]
    fig.add_trace(go.Scatter(x=seg_t, y=seg_pnl, ...), row=pnl_row, col=1)
    fig.add_trace(go.Scatter(x=[seg_t[0]], y=[seg_pnl[0]], ..., markers), ...)
```

建议改为: 合并同类型的 trace,用 `np.nan` 分隔不连续段:
```python
# 合并所有多头段为单一 trace（类似 Scattergl）
all_x, all_y = [], []
for seg in long_segments:
    all_x.extend([seg.x[0], None, ...])  # nan 分隔
    all_y.extend([seg.y[0], None, ...])
fig.add_trace(go.Scattergl(x=all_x, y=all_y, mode="lines", ...))
```

**同样适用于**:
- `_add_alignment_subplot` 中的单笔曲线段（合并同向性判断段）
- `_add_main_price_traces` 中的多个滤波 trace（已独立,不合并但个数少）
- `_add_schmitt_traces` 中的 pair bands（已用 fill 合并,较好）

**预期收益**:
- N 笔交易 → 2 个 trace（多头合并 + 空头合并）,从 20-100 降至 2
- Plotly.js 内部对象数减少 90%+
- marker 和 annotation 也按需合并

**风险**:
- NaN 间隔方式需验证 `Scattergl` 的线段分隔行为
- 合并后 hover/tooltip 行为变化（单 trace 无法区分段身份）
- 图例条目减少为 2 条（可能不需要区分每笔交易）

---

### [P3] 建议 4: 安装 orjson

**改什么**:
```bash
pip install orjson
```

**不需要改代码**,Plotly 自动检测 orjson 并在 `to_plotly_json()` 和 `json.dumps()` 中使用。

**预期收益**:
- JSON 序列化加速 **5-10x**
- 单次 `_render_plotly` 的序列化时间从 ~100ms 降至 ~10-20ms（取决于数据量）
- 零代码改动,纯依赖安装

**风险**: 无

---

### [P4] 建议 5: 让 `@st.fragment` 真正发挥效用

**方案 A — 在 fragment 内放置控制 widget**:
将各视图的"展开/折叠"按钮或视图级开关移至 `_render_chart_fragment` 内部,使其交互只触发该 fragment 重跑:
```python
@st.fragment
def _render_chart_fragment(market, ticker_code, cfg, key, ...):
    # 视图级展开/折叠放入 fragment
    show_details = st.checkbox("显示详细", value=True, key=f"{key}_details")
    _render_chart(market, ticker_code, cfg, key, show_details=show_details, ...)
```

**方案 B — Fragment + cache_data 配合**:
将数据加载和过滤计算移至 `main()` 等可缓存位置,让 fragment 只处理渲染:
```python
# main() 中缓存计算结果
for cfg in configs:
    filtered[vi] = _cached_compute_filters(noisy, t, cfg)

# fragment 只处理渲染（输出）
@st.fragment
def _render_chart_fragment(filtered, cfg, key):
    _render_chart(..., filtered, ...)
```

**预期收益**:
- 参数不变时,各 fragment 的过滤器重算被缓存跳过
- fragment 内部的 widget 交互只触发局部渲染,不拖累其他 3 个视图

**风险**:
- 方案 A: 需要评估 widget 摆放位置是否影响 UX
- 方案 B: 需要设计 filter 缓存,复杂性增加

**当前回合**：
由于 `_render_chart_fragment` 内部没有 widget,且 `main()` 的侧边栏 widget 总是触发整页 rerun,`@st.fragment` 的隔离优势暂未发挥。建议先不做重构,先完成 P0-P3 后评估是否需要。

---

### [P5] 建议 6: 评估 plotly-resampler 引入

**改什么**:
对于数据点超过 2000 的视图（如 60 分钟 / 日线长时间跨度）,引入 `figure_resampler` 包裹 figure。

```python
from plotly_resampler import FigureResampler

# 用 resampler 包裹 figure
fig = FigureResampler(
    make_subplots(rows=rows, cols=1, ...),
    default_n_shown_samples=1000,
)

# 添加 trace 时传入全量数据
fig.add_trace(go.Scattergl(name="滤波"), hf_x=t, hf_y=filtered, row=1, col=1)
```

**但需要**:
- 处理 `_render_plotly` 的自定义 HTML 渲染（resampler 的 `show_dash()` 模式可能与自建 HTML 不兼容）
- 评估是否只对数据量大的视图启用

**预期收益**:
- 日线 2000+K 线渲染时,每次操作只传送 ~1000 点而非全量
- 缩放时自动重新聚合,视觉上看起来仍然是全量数据

**风险**:
- 与 `_render_plotly` 的自定义 crosshair 可能冲突
- `FigureResampler` 需要 Dash 或 Jupyter 环境,Streamlit 集成需适配
- 增加依赖和调试复杂度
- 优先级低,建议先观察 P0-P3 后的性能再决定

---

## 五、总结优先级矩阵

| 优先级 | 建议 | 收益预期 | 风险 | 改动量 |
|--------|------|---------|------|--------|
| **P0** | `go.Scattergl` 替换 | 最高 — 渲染延迟降 60-80% | 低 — API 兼容,逐步替换 | 约 20 行改类名 |
| **P1** | 计算缓存(cache_data) | 高 — 消除重复 100-500ms | 中 — hash 键设计需谨慎 | 约 50 行 |
| **P2** | Trace 合并 | 高 — trace 数降 90%+ | 中 — hover/图例行为变化 | 约 30 行 |
| **P3** | 安装 orjson | 中 — 序列化加速 5-10x | 无 | pip install |
| **P4** | Fragment 实际利用 | 中 — 减少整页 rerun | 低 — 需调整结构 | 约 20 行 |
| **P5** | plotly-resampler | 低-中 — 大数据集专用 | 高 — 兼容性和复杂度 | 约 100 行 |

---

## 六、关键文件路径

- `/Users/xfpan/claude/filter_research/filter/components/charts.py` — 自建 Plotly HTML 渲染、PnL 渲染函数
- `/Users/xfpan/claude/filter_research/filter/streamlit_app.py` — 主应用: fragment 装饰、filter 计算、trace 构建、缓存装饰
- `/Users/xfpan/claude/filter_research/filter/components/sidebar.py` — 参数面板 widget,决定 rerun 触发源
- `/Users/xfpan/claude/filter_research/filter/state.py` — session_state 管理

## 七、数据参考

- 当前 `go.Scatter` 出现次数: **30 处**（`streamlit_app.py` 27 处 + `components/charts.py` 8 处,含重复调用）
- 单视图最大 trace 数: 约 40-150+（全功能配置 + 20 笔交易）
- 4 视图同时渲染: 约 160-600+ SVG traces
- 单视图子图行数: 4-8 行
