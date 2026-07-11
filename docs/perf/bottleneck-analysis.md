# filter_research 性能瓶颈分析（只读，据实定位）

分析对象：Streamlit + Plotly + SQLite 量化滤波研究应用。症状"数据显示慢"。
分析范围：单次交互（改参数 / 翻页 / 切 ticker）的浏览模式渲染主链路。
方法：通读 `streamlit_app.py` / `services/data_loader.py` / `db.py` / `components/charts.py` / `services/filter_engine.py`，逐条给代码证据，不改代码。

---

## 0. 结论速览（Top 3）

| # | 瓶颈 | 位置 | 预计收益 | 成本 |
|---|------|------|---------|------|
| 1 | `@st.fragment` **形同虚设**：交互控件全在侧栏/参数面板（fragment 之外），任何改动都整页 rerun，4 视图全量串行重算 | `streamlit_app.py:581` + `:1207` + `:1885` | **High** | Med |
| 2 | 计算链**零缓存**：滤波/施密特/预测/策略每次 rerun ×4 视图全量重算（作者自承 np.ndarray 不可哈希未缓存） | `streamlit_app.py:220/250/261/280` | **High** | Med |
| 3 | 浏览模式**每视图 parquet 写后立刻读回** + 每次查询新开 SQLite 连接 | `data_loader.py:219/225` + `streamlit_app.py:183/187` + `db.py:19` | Med-High | Low |

---

## 1. 单次交互调用链时序（浏览模式，改一个参数滑块）

改任意参数滑块 → 触发**整页 `main()` rerun**（非局部 fragment，见瓶颈#1）：

```
main()  [streamlit_app.py:1751]
│
├─ _get_db_connection()            [:1781] @cache_resource → 首次后 ~0        │ 低
├─ init_config_tables()            [:1782] config.db                          │ 低
├─ AppState.init_defaults()        [:1783]                                    │ 低
├─ 侧栏渲染（market/refresh/preset/health/validation 等）                       │ 低-中
│    · health/validation 在按钮后 → 不在每次 rerun 热路径 [:1081/:1102]        │ (0)
├─ _render_param_panels()          [:1819] 4 面板 × N 滑块 widget              │ 中(widget开销)
├─ _render_time_nav()→get_date_range() [:1234] 1 次 DB 查询/rerun             │ 低
│
└─ 2×2 网格循环 [:1885] for 4 views  ──►  每个 view 全量执行 _render_chart:    │ ★热点集中区
     │
     ├ Step1 _load_chart_data [:665]
     │   └ _sync_to_display [data_loader.py:219]
     │        ├ query_kline → get_conn(新连接+3×PRAGMA)+2×SELECT [db.py:148-181]│ 中  ×4
     │        └ df.to_parquet(...)               [data_loader.py:225]           │ 中  ×4 (写)
     │   └ pd.read_parquet(...)                  [streamlit_app.py:187]         │ 中  ×4 (读回)
     ├ Step4 _compute_filters [:691]  滤波 O(n)（Kalman 逐点 for，见下）        │ 低-中 ×4  未缓存
     ├ Step6 _compute_schmitt_trigger [:701] 2×np.gradient + O(n) 循环          │ 低   ×4  未缓存
     │        _find_all_pairs [:708]                                            │ 低   ×4
     ├ Step3 _align_pnl_to_current_tf [:683] 读高周期 session_state 再对齐       │ 低-中 ×3  重复
     ├ Step7 _compute_prediction_pairs [:711] 每 pair 一次 polyfit              │ 低-中 ×4  未缓存
     ├ Step8 _compute_strategy_display [:714] _compute_strategy_pnl 嵌套循环     │ 中   ×4  未缓存
     ├ Step10 make_subplots + ~20-30×add_trace [:745-780]                       │ 中   ×4
     └ Step11 _render_plotly [charts.py:19]
              ├ _sanitize_for_json 全量递归遍历 + json.dumps 二次遍历 [:48-62]   │ 中-高 ×4
              └ st.components.v1.html(iframe) [:204]                            │ ─
                   └►（浏览器）每 iframe 各自加载 Plotly.js ~3.5MB CDN + newPlot │ 高(体感) ×4
```

相对开销标注：**高**=每交互重复且体量大；**中**=每交互 ×4 累积可观；**低**=绝对量小。
数据量事实（`data/`）：`market.db` 21MB，但每视图查询受 `LIMIT n_pts`（滑块 20–300，默认 120，`sidebar.py:146`）+ 索引 `idx_kline_lookup`（`db.py:62`）约束，display parquet 实测仅 ~6KB/~120 行。**故绝对数据量小，瓶颈是"结构性重复"而非"单次体量"**。

---

## 2. 瓶颈清单（逐条证据）

### 瓶颈 #1 — `@st.fragment` 无效隔离，整页 rerun 全量重算 4 视图  【收益 High / 成本 Med】

- **位置**：`streamlit_app.py:581` `_render_chart_fragment` / `:1207` `_render_param_panels` / `:1885` 网格循环。
- **原因**：`st.fragment` 只有当**控件位于 fragment 内部**时才能局部 rerun。此处 fragment 内的 `_render_chart` **只有 `st.caption/columns/error` 和一个 Plotly HTML 组件，无任何输入控件**（`_render_chart` 通篇无 `st.slider/selectbox/button`）。而真正的交互控件——参数滑块（`_render_param_panels`→`_render_params` `sidebar.py:107`）、ticker/翻页/切周期——全部在 fragment **之外**。
- **代码证据**：
  - `:1819` `configs = _render_param_panels(...)`（滑块在此，非 fragment）→ `:1885` 循环里才把 `cfg` 传进 `_render_chart_fragment`。
  - 因此改任一滑块 = 触发 `main()` 整页 rerun = `:1885` 循环把 4 个 fragment **全部重新执行一遍**（fragment 在整页 run 中是完整执行的，隔离仅对"内部控件触发"生效）。
- **后果**：4 视图 × 完整链路（取数+滤波+施密特+预测+策略+建图+序列化）**每次交互全量串行**。fragment 装饰器在当前布局下几乎零收益。
- **修复方向**：把每视图的参数控件移入对应 fragment（各视图自带 widget 才能真正局部 rerun）；或对"仅改单一视图参数"只重算该视图。
- **验证**：`main()` 首行与末行、每个 `_render_chart` 首尾打 `time.perf_counter`，改 v0 的一个滑块，观察是否 4 个 `_render_chart` 都被计时（当前会全部触发）。

### 瓶颈 #2 — 计算链零缓存，每 rerun ×4 全量重算  【收益 High / 成本 Med】

- **位置**：`streamlit_app.py:220 _compute_filters` / `:250 _compute_schmitt_trigger` / `:261 _compute_prediction_pairs` / `:280 _compute_strategy_display`。
- **原因**：四个纯计算函数均**未加 `@st.cache_data`**。作者在注释中明确自承原因：
  - `:222` `"Not cached via @st.cache_data because params include unhashable np.ndarray."`
  - `:252` 同上。
- **对比**：仅取数层 `_cached_fetch_stock`（`:66`）与股票名 `_stock_name`（`:866`）用了 `@st.cache_data`；`_get_db_connection`（`:813`）用 `@st.cache_resource`（但名不副实，见#3）。**计算最重的一段反而全裸奔**。
- **重算内容**（每视图每次）：
  - 滤波：`filter_engine.py` 内如 Kalman `:184 for i in range(n)` 逐点迭代（2×2 矩阵运算），n≤300；
  - 施密特：`:255-256` 两次 `np.gradient` + `_schmitt_trigger`（`:474/:487` 两段 O(n) 循环）；
  - 预测：`:268` 每 pair 一次 `np.polyfit`（`:603`）；
  - 策略：`_compute_strategy_pnl`（`:649`）含 `:738/:797/:810/:837/:845` 多段嵌套循环，O(n·pairs)。
- **后果**：即便数据与参数未变（如仅切换到另一个视图导致整页 rerun），4 视图计算链仍全量重跑。
- **修复方向**：把纯计算拆为 `@st.cache_data` 函数，入参用可哈希标量 +（对 `noisy` 传其 `hashlib`/`bytes` 或 `(ticker,tf,n_pts,cutoff)` 作为 key 而非 ndarray 本体）。收益随重算频率线性放大。
- **验证**：在四个 `_compute_*` 首尾 `perf_counter` 累加，打印单视图与 4 视图合计；对比"改无关参数"前后是否仍重算。

### 瓶颈 #3 — 浏览模式 parquet 写→读回往返 + 每查询新建连接  【收益 Med-High / 成本 Low】

- **位置**：写 `data_loader.py:219-225`（`query_kline` 出 DataFrame → `df.to_parquet`）；读 `streamlit_app.py:183-187`（`pd.read_parquet` 同一文件）。连接 `db.py:19 get_conn`。
- **原因（I/O 往返）**：浏览模式下 `_load_chart_data`（`:178`）先调 `_sync_to_display` 把 DB 查询结果**写成 parquet**，紧接着 `:187` **又把这个 parquet 读回**。数据本已在内存 DataFrame 里，却绕了一圈磁盘。
  - 证据：`_sync_to_display` 浏览分支 `data_loader.py:219 query_kline(...)` → `:225 df.to_parquet(...)`；返回后 `streamlit_app.py:185 if display_path.exists(): :187 df = pd.read_parquet(display_path)`。
  - **每视图各一次写+一次读**，4 视图 = 4 写 + 4 读 / 交互。
- **原因（连接 & 共享文件隐患）**：
  - `get_conn`（`db.py:31-35`）**每次调用新建 sqlite 连接并执行 3 条 PRAGMA**（`journal_mode/synchronous/busy_timeout`）。`query_kline`（`:148`）、`_sync_to_display` 回测分支（`data_loader.py:202`）等每次都 `with get_conn()`。而 `@st.cache_resource _get_db_connection`（`streamlit_app.py:813`）**只调 `init_db()` 返回 `True`，并未缓存任何连接**——命名误导，实际零复用。
  - parquet 路径 `data/display/{tf}.parquet` **仅按 tf 命名，不含 ticker**（`streamlit_app.py:183` / `data_loader.py:214/225`）。同 tf 的多视图会互相覆盖同一文件，属重复写 + 潜在数据串味隐患。
- **后果**：纯开销的磁盘往返 + 反复建连（PRAGMA）叠加 ×4。DB 查询本身有索引 + LIMIT 很快，往返与建连才是浪费。
- **修复方向**：浏览模式让 `_load_chart_data` 直接用 `query_kline` 的 DataFrame，跳过 parquet；连接改为进程级复用（真正缓存一个连接或连接池）。
- **验证**：分别对 `to_parquet`、`read_parquet`、`get_conn` 打点计时求和 ×4；或临时改为直接返回内存 DataFrame 做 A/B 计时对比。

### 瓶颈 #4 — `_render_plotly` 序列化二次遍历 + 4×iframe 各自加载 Plotly.js CDN  【收益 Med / 成本 Med】

- **位置**：`components/charts.py:19 _render_plotly`，序列化 `:48-62`，HTML/CDN `:65-70`，`:204 st.components.v1.html`。
- **原因（序列化双遍历）**：`:62 json.dumps(_sanitize_for_json(fig_dict), cls=_NpEncoder)`。`_sanitize_for_json`（`:48`）先**整棵 fig_dict 递归遍历**一遍替换 NaN/Inf 并把 ndarray `.tolist()` 后**逐元素再递归**；随后 `json.dumps` 携 `_NpEncoder`（`:36`，同样处理 NaN/ndarray）**再整树遍历一遍**。两次全量遍历，且 `_NpEncoder` 已能处理 NaN/ndarray，`_sanitize_for_json` 存在功能重叠。
- **原因（trace 体量 & 客户端）**：
  - 单图 trace 数多：主图 Candlestick+收盘+滤波+滤波2+每 pair 预测（`streamlit_app.py:377-388`、`:750`），残差/速度、施密特 5+ trace（`:414-431`，其中 `:414` `list(t)+list(t[::-1])` 把点数翻倍构造填充带）、PnL 分段 + 逐段 marker（`:449-469`）、cross/alignment/feedback 子图。布局最多 8 行（`_determine_subplot_layout :324`）。粗算 15–30 trace/图。
  - **客户端致命项**：`st.components.v1.html` 每次生成一个 **iframe**，HTML 里 `:69` 硬编码 `<script src="https://cdn.plot.ly/plotly-2.35.2.min.js">`。**4 视图 = 4 个 iframe，各自独立拉取 ~3.5MB Plotly.js 并各自 `Plotly.newPlot`**，无共享、无本地打包。这是"数据显示慢"体感的主要客户端来源（尤其首屏/网络抖动时）。
- **后果**：服务端 JSON 双遍历 ×4；客户端 4 份 Plotly.js 下载 + 4 次 newPlot 布局。
- **修复方向**：删掉 `_sanitize_for_json`，仅靠 `_NpEncoder`（单次遍历）；或改用 `st.plotly_chart`（Streamlit 内建复用 plotly.js，避免 4 份 CDN）；至少把 Plotly.js 本地打包/单次加载。
- **验证**：`_render_plotly` 内对 `_sanitize_for_json`、`json.dumps`、`len(figure_json)` 分别打点/打印字节数；浏览器 DevTools Network 看 plotly.js 是否被请求 4 次。

### 次要观察（低优先，列出不展开）

- 跨周期对齐依赖排序：视图按 tf 降序渲染（`:1883-1884`）以便低周期读高周期 `_pnl_{tf}` session_state（`:662/:683`）。这形成**视图间串行依赖**，天然阻碍并行化，且每个低周期视图都重跑一次 `_align_pnl_to_current_tf`（`filter_engine.py:858`，含 O(n) 循环）。
- `get_date_range`（`:1234`）每次浏览 rerun 一次额外 DB 查询（新建连接），小但可并入#3 一起省。
- 自动刷新/回测播放路径 `time.sleep()+st.rerun()`（`:1907`、`:1549`）会周期性触发整个上述链路——放大#1/#2/#3/#4 的累计成本。

---

## 3. 按 收益 × 成本 的总排序（先做性价比高的）

| 排名 | 瓶颈 | 收益 | 成本 | 性价比 | 一句话动作 |
|------|------|------|------|--------|-----------|
| 1 | #3 parquet 写读往返 + 连接复用 | Med-High | **Low** | ★★★★★ | 浏览模式直接用 `query_kline` 的 DataFrame，跳过 parquet；缓存/复用连接 |
| 2 | #2 计算链加缓存 | **High** | Med | ★★★★ | `_compute_filters/schmitt/pred/strategy` 拆成 `@st.cache_data`，key 用标量+数据指纹 |
| 3 | #1 fragment 有效化 | **High** | Med | ★★★★ | 把参数控件下沉进各视图 fragment，实现真·局部 rerun，改 1 视图只重算 1 视图 |
| 4 | #4 渲染序列化 & 单份 plotly.js | Med | Med | ★★★ | 删 `_sanitize_for_json` 走单遍 `_NpEncoder`；改 `st.plotly_chart` 或本地打包 plotly.js |

排序说明：#3 成本最低、立竿见影，先做；#2 收益最高（重算频率放大），需设计哈希 key；#1 是"每次都全量 ×4"的根因，收益高但涉及布局重构；#4 兼顾服务端与客户端，独立可做。#1 与 #2 有协同——先#2 让"必然发生的重算"变便宜，再#1 减少"重算次数"。

---

## 4. 快速验证方法（量测确认，任选）

1. **perf_counter 打点（最直接）**：在 `main()` 首尾、`_render_chart` 首尾、`_load_chart_data` / 四个 `_compute_*` / `_render_plotly` 内 `_sanitize_for_json`+`json.dumps` 处各加：
   ```python
   import time; _t0 = time.perf_counter(); ...; logger.info(f"[perf] <段名> {(time.perf_counter()-_t0)*1000:.1f}ms")
   ```
   改一个滑块，看：(a) 4 个 `_render_chart` 是否都被计时（验证#1）；(b) `_compute_*` 是否每次重算（验证#2）；(c) `to_parquet`+`read_parquet` 往返耗时（验证#3）；(d) 序列化耗时与 `len(figure_json)` 字节（验证#4）。
2. **Streamlit rerun 计数**：在 `main()` 首行 `logger.info("RERUN")`，交互几次数日志行数，确认"局部 vs 整页"。
3. **DB 计时**：包一层 `get_conn` 计数器/计时，统计单次交互连接次数与查询耗时（验证#3 建连开销）。
4. **客户端**：浏览器 DevTools → Network 过滤 `plotly`，确认单次渲染是否发起 4 次 ~3.5MB 请求（验证#4 客户端项）。
5. **A/B**：临时把浏览模式 `_load_chart_data` 改为直接返回 `query_kline` DataFrame（不落 parquet），对比总耗时，量化 #3 收益。
6. **cProfile/py-spy**：`py-spy top --pid <streamlit_pid>` 或 `cProfile` 包住一次交互，看 `to_parquet/read_parquet/polyfit/_sanitize_for_json/json.dumps` 的累计占比。

---

*全部条目均可对应到具体 `文件:行`。未运行 profiler，相对开销为基于代码结构（重复次数 × 单次体量）的估计，绝对耗时需按第 4 节实测确认。*
