# 效率优化方案（供核对，未实现）

> 目标：解决"数据显示太慢"。本方案据 3 份并行调查报告综合（同目录：
> [`bottleneck-analysis.md`](bottleneck-analysis.md)、[`parquet-to-ram-feasibility.md`](parquet-to-ram-feasibility.md)、
> [`streamlit-plotly-best-practices.md`](streamlit-plotly-best-practices.md)），关键论断已 grep 核验。
> **本文只给方案，不实现**——请逐项核对后再决定做哪些、按什么顺序。

---

## 0. 一句话结论

"显示慢"的根因是 **结构性重复：一次交互 → 整页 rerun → 4 视图串行 × 全量重算 × 零计算缓存**，叠加 **客户端 4 个 iframe 各自加载 ~3.5MB Plotly.js + 30 处 SVG 渲染**。
**不是数据量**（每视图 ≤300 行、parquet 仅 ~6KB），**也不是 parquet 文件**（IO <5ms）。
→ 你问的 **parquet→RAM 收益很小**，是"顺带清理"而非提速主力。真正的提速在：**停止无谓的 4 视图重算 + 计算缓存 + 渲染换 WebGL/本地 Plotly.js**。

---

## 1. 瓶颈全景（据实，已核验）

单次交互（改一个滑块）的调用链：

```
改任一参数(滑块在 sidebar, 在 fragment 外)
  → 整页 rerun (@st.fragment 名存实亡: L581 装饰 _render_chart_fragment,
                但触发 widget 在 fragment 外 → 无隔离)
  → 4 视图 v0~v3 串行, 每视图完整重跑:
       取数: parquet 写→读回往返(data_loader:225 / streamlit_app:187)
             + get_conn 每查询新建连接+3×PRAGMA
             (_get_db_connection@cache_resource L814 只返回 bool, 不缓存连接)
       计算: _compute_filters/schmitt/pred/strategy 全量重算, 零 @st.cache_data
             (L222/L252 注释自承 "np.ndarray 不可哈希未缓存")
       渲染: _render_plotly 自建HTML, _sanitize_for_json + json.dumps 双遍历
             + 30 处 go.Scatter(SVG, Scattergl=0)
  → 客户端 4 个 iframe 各自加载 ~3.5MB Plotly.js CDN
```

**关键事实**：`market.db` 21MB，但每视图 `LIMIT n_pts`(≤300)+索引，parquet ~6KB/~120 行 → 瓶颈是 **"重复 ×4 × 无缓存"**，非单次体量。数据 `fetch` 已有缓存（`_cached_fetch_stock` L66 `@st.cache_data`），但 **per-view 计算未缓存**。

---

## 2. 优化项（按 ROI 排序）

| # | 优先级 | 项 | 现状/证据 | 方案 | 预期收益 | 风险 | 工作量 | 需回测验证? |
|---|---|---|---|---|---|---|---|---|
| **1** | **P0** | **Scatter→Scattergl** | 30 处 `go.Scatter`(SVG)、`Scattergl`=0；纯线/点 | 纯线/标记换 `go.Scattergl`(WebGL)；`fill`/`annotation` 保留 Scatter | 渲染延迟 **↓60-80%**，4 视图同渲最明显 | 极低（WebGL 细节差异） | 小(~20 行改类名) | 否 |
| **2** | **P0** | **计算缓存** | `_compute_filters/schmitt/pred/strategy`(L220/250/261/280) 每 rerun×4 全量重算，无缓存 | `@st.cache_data(hash_funcs={np.ndarray:...})` 按 数据指纹+参数 缓存 | 消除 **100–500ms×4** 重复计算，命中即瞬时 | 中（np 数组 hash 正确性/内存膨胀/失效时机） | 中 | **是**（回测 cutoff 变化须正确失效、不串味） |
| **3** | **P0/P1** | **停止整页重算(fragment 作用域)** | `@st.fragment`(L581) 装饰图，但参数滑块在 sidebar(fragment 外)→ 改参数整页 rerun 4 视图 | 把参数控件收进各视图 fragment 内，使单视图交互只重跑该视图 | 改一个参数从"4 视图全量"→"1 视图" | 中大（UI 布局重构，改动面大） | 中大 | 是（确保不误伤其他视图） |
| **4** | **P1** | **Plotly.js 本地化/单加载** | 4 个 iframe 各自 CDN 加载 ~3.5MB | 本地打包 Plotly.js / 复用单实例；或评估迁 `st.plotly_chart` | 首屏/切换客户端体感大幅↓ | 中（自建 HTML 的**跨子图十字光标**功能须保住） | 中 | 否 |
| **5** | **P1** | **DB 连接复用** | `get_conn` 每查询新建连接+3×PRAGMA；`_get_db_connection`@cache_resource(L814) 只返 bool | 真正用 `@st.cache_resource` 缓存连接/连接池 | 每查询省建连+PRAGMA | 中（SQLite 跨 rerun 复用线程安全 check_same_thread） | 小 | 轻 |
| **6** | **P2** | **PnL trace 合并** | 逐笔独立 `go.Scatter`(10–50 个) | NaN 间隔合并为 多头/空头 各 1 条 | Plotly.js 对象开销 **↓90%+** | 低（hover 行为略变） | 中 | 否（纯显示） |
| **7** | **P2** | **parquet→RAM（你问的 b）** | `_sync_to_display` 写 parquet 再读回，IO <5ms | `_sync_to_display`/`_load_chart_data` 函数化返回 DataFrame，存 `session_state`（存 `df.copy()` 防突变污染）；`clear_display_cache` 改清 session key | **收益 Low**（省 ~5ms/次 + 简化数据流），**非提速主力** | 中（突变污染→copy 规避；回测时间一致性→键含 cutoff；多 tab 天然隔离） | 中 | **是**（回测"无未来信息"约束等效保证） |

---

## 3. 建议实施顺序

1. **先 P0-1（Scattergl）**：风险最低、见效快、独立可回退——先拿这个立竿见影。
2. **再 P0-2（计算缓存）**：最大提速点，但要设计好 np 数组 hash 与失效。
3. **P1-4 / P1-5（Plotly.js 本地化 + 连接复用）**：客户端体感 + 后端小优化。
4. **P0/P1-3（fragment 重构）**：改动面最大，放缓，且最好先做 §4 量测确认收益。
5. **P2-6 / P2-7（trace 合并 / parquet→RAM）**：收尾清理。

每步独立、可验证、可回退。

## 4. 先量测再动手（强烈建议）

不要凭感觉优化。先在 `_render_chart` 各阶段加 `time.perf_counter` 打点（取数/滤波/施密特/预测/策略/渲染各段耗时 ×4 视图），或用 Streamlit profiler，**量出真实占比**，再按实测 ROI 排序动手。这样也能在每项做完后有前后对比数字。

## 5. 请你核对的关键决策点

1. **P0-2 vs P0/P1-3**：先只做**计算缓存**（改动小、提速大），暂不动 **fragment 重构**（改动大）？还是两个一起上？
2. **P1-4**：接受从"自建 HTML 渲染"迁到 `st.plotly_chart`（可能损失**跨子图十字光标**）？还是保留自建、只做 **Plotly.js 本地化**？
3. **parquet→RAM（你的问题 b）**：确认它是"**顺带清理 + 简化数据流**"而非主要提速目标？（实测 IO<5ms，提速可忽略）
4. **量测优先**：是否先让我加一版 `perf_counter` 打点、量出各段真实耗时，再据实定最终顺序？

---

> 三份详细调查报告见同目录 `bottleneck-analysis.md` / `parquet-to-ram-feasibility.md` / `streamlit-plotly-best-practices.md`。
> 确定要做哪些后，我再逐项实现 + 加前后量测对比。
