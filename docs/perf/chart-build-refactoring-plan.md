# 构图时间大幅缩减 —— 重构方案（供讨论，未实施）

> 现状：构图（make_subplots + 逐 add_trace）占单次交互 **92-99%** 耗时（185-1378ms），
> 计算/IO 已优化至 1-2ms。瓶颈确认为 **Plotly Python API 的逐对象构造开销**，
> 随 bar 数和 trace 数线性增长（~29 add_*/视图 × 4 视图 = 116 个 add）。

## 根因

Plotly Python 的 `make_subplots` + 逐 `add_trace` 每调用一次就构造一个 Python 对象
（Scattergl / Shape / Hline / Annotation），然后序列化为 JSON。这个 Python 层是纯开销，
与数据量无关——Plotly 内部 `_to_plotly_json` 遍历每个 Trace 的每对 (x,y) 做 `base64` 编码
再 `json.dumps`。

## 方案（按改动面排序）

### 方案 A：`go.Figure(data=dicts, layout=dict)` 批量构造（推荐）

**做法**：不再调 `make_subplots` + 逐 `add_trace`。把所有 trace 的 raw dict 列表 + layout dict
一次性传入 `go.Figure(data=..., layout=...)`。跳过 Python 对象构造层，Plotly 内部直接序列化 dict。

- 改动：重构 `_render_chart` 的 figure 构建段（~60 行），各 `_add_*` 函数改为返回 dict 列表而非 add_trace 到 fig。
- 预期收益：构图 185-1378ms → **50-200ms**（减少 Python 对象分配 + 双遍历）
- 风险：中等。需要精确匹配 plotly schema（子图 row/col 的 `xaxis`/`yaxis` key 需要手工指定）。`fill` / `annotation` / `hline` 等需确认 dict 格式兼容。
- 工作量：2-3 天（重构 + 调通所有 panel + 更新测试）
- 可回退：git revert（两种构造方式对外部行为无影响）

### 方案 B：保留 `make_subplots`，`add_traces` 批量追加

**做法**：用 `fig.add_traces(list_of_dicts)`（注意末尾的 s，一次加多个 trace dict），而非逐个 `fig.add_trace(dict)`。Plotly 内部分批序列化，减少部分 Python 开销。

- 改动：较小。仅重构 `_add_*` 函数返回 dict 列表，在 `_render_chart` 统调 `fig.add_traces(...)`。
- 预期收益：构图 **降 40-60%**（Python 对象构造减少，但 JSON 双遍历仍在）
- 风险：低。`add_traces` 是官方 API，纯递增优化。
- 工作量：1 天
- 可回退：git revert

### 方案 C：`_render_plotly` 去自建 HTML + `json.dumps` 双遍历

**做法**：当前 `_render_plotly` 先 `fig.to_plotly_json()` 再 `_sanitize_for_json`（逐 key 递归），
再 `json.dumps`。实际上 Plotly 已在 `fig.to_json()` 内建序列化。可以直接用 `fig.to_json()`
传递，省掉一轮 `_sanitize_for_json` 遍历。

- 改动：`_render_plotly` 替换序列化路径
- 预期收益：渲染段 4-6ms → **1-2ms**（但这不是主力）
- 风险：低（`_sanitize_for_json` 仅处理 NaN/Inf，`fig.to_json()` 自带处理）
- 工作量：小（~5 行）

### 方案 D：单 `go.Figure` × 4 并列（省 4 个 iframe + Plotly.js）

**做法**：当前 4 视图各一个 `_render_plotly(fig)` → 各产一个 iframe → 各加载 ~3.5MB Plotly.js CDN。
改为一个大 Figure（4 列或 4 组子图），客户端只加载一次 Plotly.js，渲染一把。跨子图十字光标走 Plotly 内置 hovermode。

- 改动：大（整个 app 布局从 4×独立改为单 Figure，`st.fragment` 需重设计）
- 预期收益：客户端体感 **大幅**（去掉 3 × 3.5MB + 3 个 iframe 渲染）
- 风险：高（跨子图十字光标要重写，交互复杂度上升）
- 工作量：大（重新设计整个 UI）

## 推荐路径

1. **先做 B（add_traces 批量）**：风险最小、收益可以直接量测验证，且铺路给 A。
2. 在 B 基础上，如有余力做 **A（批量 Figure 构造）**或只保留 B 看实测收益够不够。
3. **C（去双遍历）**顺手做，不花多少时间。
4. **D（单 Figure）**作为远期目标，讨论后再定。

## 验证方式

复用现有 `⏱ 构图...ms` 打点，每步前后对比。跑全量 pytest 保证无行为回归。

---

> 推荐先做 B + C（1 天，预期构图降 40-60%），看实测效果再决定是否推进 A。
