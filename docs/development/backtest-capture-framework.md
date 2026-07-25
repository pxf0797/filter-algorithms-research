# 回测数据采集系统 -- 宏观框架设计

> 版本: v1.1 | 日期: 2026-07-13 | 状态: MVP 设计阶段

---

## 1. 背景与目标

### 1.1 为什么需要这个系统

当前 Stock Filter Backtest System（Streamlit 前端 + Python 计算管道）存在一个核心缺陷：**回测过程中的中间状态无法完整追溯**。具体表现为：

- BS（Buy/Sell）标记在回测导航中发生非预期跳跃 -- 已验证案例中 filter 尾部 0.000342 的变化经 Schmitt Trigger -> eps -> pairs -> trades -> BS markers 管道级联放大 **645 倍**，导致额外 BS 标记出现
- 现有 `PipelineCapture` 系统采用 Snapshot 模式（每步全量保存），但事后需要 diff 工具才能发现变动，效率低下
- 管道中部分关键数据（`mu_v`, `sigma_v` 等 Schmitt 内部变量）未被捕获，根因分析链断裂

### 1.2 要解决的核心问题

**BS 跳跃根因定位**：当 BS 标记在回测导航中间步骤发生非预期变动时，能完整追溯"哪个输入变化 -> 哪个管道阶段放大 -> 最终产生/消除了哪个 BS 标记"。

### 1.3 设计原则

| 原则 | 含义 | 为什么 |
|------|------|--------|
| **极简** | 不设计未验证需求的功能 | 前期报告过度设计了 Snapshot 系统，用户明确要求"越简单越好，方向对了再优化" |
| **完整** | BS 变动追溯链不可断裂 | 缺少任一个阶段的输出，根因分析都无法进行 |
| **可追溯** | 每次 BS 变动都有事件记录，不过事后 diff | Event Sourcing 天然支持，避免逐对对比 |
| **零侵入** | 不修改现有 Streamlit 核心计算逻辑 | services/ 目录已零 Streamlit 依赖，改动风险低 |
| **面向开发者** | JSONL/Parquet 格式，pandas/jq/grep 直接分析 | 不引入数据库、不引入专用分析工具 |

---

## 2. 现状分析摘要

### 2.1 当前系统架构（简化）

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Streamlit UI Layer                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │ sidebar.py   │  │ _render_     │  │ _render_backtest_mode()  │  │
│  │ (87 st.* 调用)│  │ chart()      │  │ (nav/slider/play)        │  │
│  └──────────────┘  └──────┬───────┘  └──────────────────────────┘  │
│                           │ 调用                                     │
├───────────────────────────┼─────────────────────────────────────────┤
│              纯计算层 (零 Streamlit 依赖, ~83KB)                     │
│  ┌────────────────────────┼──────────────────────────────────────┐  │
│  │  services/filter_engine.py                                     │  │
│  │    10 个滤波器 → _schmitt_trigger() → _find_all_pairs()         │  │
│  │    → _fit_parabolic() → _compute_strategy_pnl()                │  │
│  │    → _compute_holding_masks()                                  │  │
│  ├────────────────────────────────────────────────────────────────┤  │
│  │  services/bs_marker.py: compute_bs_markers()                   │  │
│  │  services/data_loader.py: _sync_all_cascading()                │  │
│  │  services/pipeline_capture.py: PipelineCapture                 │  │
│  └────────────────────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────────────┤
│                        数据层                                        │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │ data/display/    │  │ data/config.db    │  │ pipeline_captures│  │
│  │ {tf}.parquet     │  │ data/market.db    │  │ (Snapshot 输出)  │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

**关键发现**：`services/` 目录下 `filter_engine.py` + `data_loader.py` + `bs_marker.py` + `pipeline_capture.py` 四个文件，已验证零 Streamlit 引用。计算管线高度模块化，剥离 UI 层后可直接被 CLI 复用。

### 2.2 核心不足（按严重性排序）

| # | 不足 | 严重性 | 影响 |
|---|------|--------|------|
| 1 | BS 变动无事件记录，只能事后 diff | **高** | 根因分析需要 1300 步逐对对比，效率极低 |
| 2 | 管道关键中间量未捕获（`mu_v`, `sigma_v`, `eps` 内部演化） | **高** | 级联放大分析链断裂，无法精确计算放大倍数 |
| 3 | 无输入数据窗口引用，复现依赖外部文件一致性 | 中 | Parquet 文件更新后历史记录失效 |
| 4 | Snapshot 每步 120KB 全量保存，90%+ 数据重复 | 中 | 完整回测 156MB，多次运行快速占满磁盘 |
| 5 | `_is_backtest` 局部变量在 `_load_chart_data()` line 183/190 两分支均赋值，line 223 在分支外引用 | 低 | 无运行时 bug，但两分支赋值分散降低了可读性 |

### 2.3 过度设计摘要

当前 `PipelineCapture` 系统中存在过度设计：每步全量存储（90%+ 数据重复）、完整 PnL 曲线（BS 分析不需要）、多 TF 视图同时记录（MVP 只需 operating TF）。这些已在 4.2 记录策略中按"分层记录、不全量"原则精简，不再赘述。

---

## 3. 宏观框架设计

### 3.1 模块划分与数据流

```
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 1: 数据加载层 (Data Loader)                                   │
│  ┌─────────────┐  ┌──────────────────┐  ┌────────────────────┐     │
│  │ Parquet 读取 │  │ 级联数据同步      │  │ 缺失 bar 合成      │     │
│  │ (已有)       │  │ _sync_all_cascading│  │ _synthesize_        │     │
│  │             │  │ (已有)            │  │ incomplete_bar (已有)│     │
│  └─────────────┘  └──────────────────┘  └────────────────────┘     │
│  [S1] 输出窗口数据 (不记录)  [S2] Date Markers (不记录)               │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 2: 管道计算层 (Pipeline Core) 🎯 MVP 核心                     │
│                                                                     │
│  data/display/{tf}.parquet ──→ [S1] Window Data (不记录)            │
│       │                                                             │
│  [S3] Filters ────────→ filter_tail.jsonl (tail 10 pts)             │
│       │                                                             │
│  [S4] Schmitt ────────→ schmitt_snapshot.jsonl (增量摘要)            │
│       │                                                             │
│  [S5] Pairs ──────────→ (含在 S4 中)                                 │
│       │                                                             │
│  [S6] Predictions ────→ 不记录（可从 pairs+filtered 推导）           │
│       │                                                             │
│  [S7] Trades/PnL ─────→ trade_summary.jsonl (摘要)                  │
│       │                                                             │
│  [S8] Higher TF PnL ──→ 不记录（可交叉引用）                         │
│       │                                                             │
│  [S9] Holding Masks ──→ 不记录（可从 trades+BS 推导）                │
│       │                                                             │
│  [S10] BS Markers ────→ events.jsonl ★核心（Event Sourcing）        │
│       │                                                             │
│  [S11] Display ───────→ 不记录（图表渲染）                            │
│                                                                     │
│  纯 Python 函数，零 UI 依赖，可被 CLI 和 Streamlit 共同调用           │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 3: 数据记录层 (Data Recorder) 🎯 MVP 核心                      │
│  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────┐     │
│  │ Event Recorder   │  │ Tail Recorder    │  │ Snapshot       │     │
│  │ (BS 变动事件)     │  │ (filter/schmitt  │  │ Recorder       │     │
│  │ → events.jsonl   │  │  尾部摘要)        │  │ (关键步全量)    │     │
│  │                  │  │ → *_tail.jsonl   │  │ → steps/       │     │
│  └──────────────────┘  └──────────────────┘  └───────────────┘     │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 4: 分析工具层 (Analysis Tools)                                │
│  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────┐     │
│  │ BS Trace         │  │ Cascade Analyzer │  │ Replay Tool    │     │
│  │ (事件流查询)       │  │ (放大倍数计算)    │  │ (状态重建)      │     │
│  └──────────────────┘  └──────────────────┘  └───────────────┘     │
└─────────────────────────────────────────────────────────────────────┘
```

**数据流原则**：每个阶段输入来自上一阶段输出；原始 OHLC 是唯一外部输入，通过 `data_manifest.json` 引用而非复制；BS 变动事件仅在有变化时写入；所有记录通过 bar 索引自然关联。

### 3.2 存储布局

```
backtest_output/
└── {ticker}_{session_id}/                 # 例如 03690.HK_20260713-143022
    ├── metadata.json                      # 会话元数据 + 配置快照
    │   {
    │     "ticker": "03690.HK",
    │     "session_id": "20260713-143022",
    │     "config": {"operating_tf": "日线", "min_tf": "60分钟", ...}
    │   }
    │
    ├── data_manifest.json                 # 🎯 输入数据引用（不复制原始数据）
    │   {
    │     "data_sources": {
    │       "日线":  "data/display/日线.parquet",
    │       "60分钟": "data/display/60分钟.parquet"
    │     },
    │     "n_pts": {"日线": 250, "60分钟": 500}
    │   }
    │
    ├── events.jsonl                       # 🎯 BS 变动事件流（核心输出）
    │   {"step":0,  "event":"init", ...}
    │   {"step":23, "event":"bs_added", "marker":{...}, "trigger":"...", ...}
    │   {"step":23, "event":"cascade", "amplification":645, ...}
    │
    ├── filter_tail.jsonl                  # 🎯 Filter 尾部追踪（每步 10 floats）
    │   {"step":0,  "cutoff":"2025-06-01", "filtered_tail":[12.34, ...]}
    │
    ├── schmitt_snapshot.jsonl             # 🎯 Schmitt 关键指标（仅变化时写入）
    │   {"step":23, "sig_flips":[...], "eps_tail_change":0.002, "pair_count_delta":1}
    │
    ├── trade_summary.jsonl                # 🎯 Trade 摘要
    │   {"step":23, "trade_count":4, "trade_delta":"+1 short", "new_trades":[...]}
    │
    └── steps/                             # 可选：关键步骤全量快照
        ├── 000000/                        # Step 0 -- 初始状态快照
        │   ├── index.json
        │   └── v0_日线/
        │       ├── stage_03_filter.parquet
        │       ├── stage_04_schmitt.parquet
        │       └── stage_10_bs_markers.json
        └── 000023/                        # Step 23 -- structural jump 快照
            └── ...
```

**与现有 `data/display/{tf}.parquet` 的关系**：
- `data/display/` 是**原始价格数据源**，由 `_sync_all_cascading()` 及 `_sync_to_display()` 写入。文件命名为 `{tf}.parquet`（如 `日线.parquet`），不含 ticker，每次回测运行覆盖写入
- `backtest_output/` 是**管道输出记录**，为新系统产出，完全独立于 `data/display/`
- 两者通过 `data_manifest.json` 中的路径引用关联，不复制数据
- Parquet 文件已被全局 `.gitignore` 中的 `*.parquet` 规则排除，不纳入版本控制

---

## 4. 数据记录方案

### 4.1 输入数据：不额外记录，用引用替代

**记录**：`data_manifest.json`（数据源路径 + 窗口参数）

**不记录**：完整 OHLC 数组、bar 日期序列、各周期原始价格

**为什么**：
- 原始数据已持久化在 `data/display/{tf}.parquet` 中
- 通过 `ticker` + `cutoff_date` + `n_pts` 可精确重建任意步骤的输入窗口
- 复制原始数据会导致存储量放大（1300 步 x 4 视图 x OHLC 数组），且数据一致性问题（parquet 更新后历史输出与存储的输入副本不匹配）

**如何关联**：
```
BS Marker (bar_index=118, type="entry")
  → 所在视图: v0_日线
  → data_manifest.json 中查找该视图数据源: "data/display/日线.parquet"
  → 读取该 parquet 中 bar_index=[window_start..118] 的原始 OHLC
```

### 4.2 管道输出：分层记录，不全量

| 阶段 | 输出 | 记录策略 | 理由 |
|------|------|---------|------|
| S1 | Raw Data | **不记录** | 从 Parquet 重建 |
| S2 | Date Markers | **不记录** | 可从 dates 推导 |
| S3 | Filter | **仅记录 tail（最后 10 点）** | 级联放大起点，10 points x 1300 steps ≈ 100KB |
| S4 | Schmitt | **增量摘要**（sig 翻转 + eps 变化 + pair_count） | BS 变动的直接上游，只在变化时写入 |
| S5 | Pairs | 含在 S4 摘要中 | pair 数量变化即结构性信号 |
| S6 | Prediction Curves | **不记录** | 可从 pairs + filtered 推导 |
| S7 | Trades/PnL | **摘要**（trade_count + 关键字段） | 交易变化是 BS 变化的结构性信号 |
| S8 | Higher TF PnL | **不记录** | 可交叉引用高 TF 的 S7 |
| S9 | Holding Masks | **不记录** | 可从 trades + BS markers 推导 |
| S10 | BS Markers | **Event Sourcing**（增量事件） | 核心需求，变动天然可追溯 |
| S11 | Display | **不记录** | 图表渲染，非数据 |

**新增捕获项（当前 PipelineCapture 遗漏）**：
- `mu_v`, `sigma_v`：Schmitt 内部变量，决定自适应死区 eps 的计算，是 BS 敏感性分析的根因参数 [已确认：`_schmitt_trigger()` 返回字典中包含这些变量，当前可访问但 PipelineCapture 未记录]
- `eps` 尾部演化序列：当前只捕获 eps 的最终快照值，需要追踪其随 step 推进的演化过程
- `dur`：信号持续期，Schmitt 触发器输出字典中已有，用于区分稳定信号和噪声抖动
- OHLCV（开高低收量）：min_tf 原始 bar 数据，BS 信号分析的基础价格上下文
- `pair_split`/`pair_merge` 事件：信号对的结构性变化是 BS 跳跃的常见原因

> 详见 §5（CSV 统一输出格式）和 §7（补充数据项）。

### 4.3 BS 变动追踪 -- Event Sourcing（核心创新）

> 方案对比详见 [附录 B Q1](#b-关键决策记录)。核心结论：Event Sourcing + 首步 Snapshot，存储量从 ~156 MB 降至 ~5-8 MB，BS 变动可直接 grep 定位。

**推荐**: **Event Sourcing + 首步 Snapshot**。理由：BS 变动追溯是核心需求，Snapshot 需要事后 diff 正是当前痛点。实现复杂度仅增加 ~50 行对比代码，存储量减少 30 倍。首步 Snapshot 提供初始状态，后续步骤增量记录即可。

**事件类型定义**：

| 事件 | 触发条件 | 携带数据 |
|------|---------|---------|
| `init` | Step 0 | 初始 BS 状态（完整 entry/exit markers） |
| `bs_added` | 新 bar 导致新增 BS 标记 | marker 详情 + 触发原因 + step_index |
| `bs_removed` | 数据变化导致 BS 标记消失 | marker 详情 + 消失原因 + step_index |
| `bs_moved` | BS 标记 bar_index 位移 | 旧/新 bar_index + 位移量 |
| `pair_split` | 信号对分裂为多个 | 受影响范围 + pair 数量变化 |
| `pair_merge` | 多个信号对合并 | 受影响范围 + pair 数量变化 |
| `cascade` | 级联放大事件（可选，聚合型） | filter_tail_delta, eps_amplification, sig_flips, total_amplification |

**与 Snapshot 的配合**：
- Step 0：写入完整 Snapshot（作为重放起点）
- 每步：写入增量事件到 `events.jsonl`
- Structural jump 步骤：可选写入完整 Snapshot 到 `steps/{step_index}/`（作为检查点，非必须）
- 需要查看任意步的完整 BS 状态时：从最近 Snapshot 重放事件至目标步骤

**事件生成算法**（伪代码）：

```python
def capture_bs_events(step_index, current_bs, prev_bs, events_file):
    """当前 BS 格式: {"entry_markers": [(bar_idx, "B"/"S", "green"/"red", date), ...],
                      "exit_markers":  [(bar_idx, "B"/"S", "green"/"red", exit_reason, date), ...]}"""
    if step_index == 0:
        emit("init", markers=current_bs)
        return
    # 按 (bar_idx, label, color) 三元组比较，忽略 date/exit_reason 字段
    def _key(markers, kind):
        return {(m[0], m[1], m[2]) for m in markers.get(f"{kind}_markers", [])}
    cur_entry = _key(current_bs, "entry")
    prev_entry = _key(prev_bs, "entry")
    cur_exit  = _key(current_bs, "exit")
    prev_exit  = _key(prev_bs, "exit")
    for m in cur_entry - prev_entry:
        emit("bs_added", marker=m, kind="entry", step=step_index)
    for m in prev_entry - cur_entry:
        emit("bs_removed", marker=m, kind="entry", step=step_index)
    for m in cur_exit - prev_exit:
        emit("bs_added", marker=m, kind="exit", step=step_index)
    for m in prev_exit - cur_exit:
        emit("bs_removed", marker=m, kind="exit", step=step_index)
```

---

## 5. CSV 统一输出格式

### 5.1 定位：主要分析格式

JSONL（events.jsonl 等）保留用于**事件流分析**（grep/jq 定位 BS 变动时刻），CSV 作为**主要分析格式**，提供完整的逐 bar 时间序列视图。两者的关系：

| 维度 | JSONL（事件流） | CSV（时间序列） |
|------|----------------|-----------------|
| 用途 | 定位 BS 变动事件、级联放大事件 | 逐 bar 状态演进、跨周期对比、统计分析 |
| 粒度 | 事件（仅变化时写入） | 逐 bar（每个 min_tf bar 一行） |
| 分析工具 | grep, jq, tail | pandas, Excel, Plotly |
| 内容 | 事件 + 触发原因 + 增量 delta | 每个 bar 上所有视图的完整状态快照 |

### 5.2 CSV 结构

每行 = 最小周期（min_tf）的 1 个 bar。每列 = 一个指标。

列分组如下（视图数量由配置决定，以 4 视图为例）：

```
┌─ 基础列（7 列）──────────────────────────────────┐
│ bar_index, bar_timestamp, close, open, high,     │
│ low, volume                                       │
├─ 日线视图 (v0) ──────────────────────────────────┤
│ v0_filtered, v0_sig, v0_eps, v0_mu_v,            │
│ v0_sigma_v, v0_pair_count, v0_trade_count,       │
│ v0_bs_entry, v0_bs_exit                          │
├─ 60分钟视图 (v1) ────────────────────────────────┤
│ v1_filtered, v1_sig, v1_eps, v1_mu_v,            │
│ v1_sigma_v, v1_pair_count, v1_trade_count,       │
│ v1_bs_entry, v1_bs_exit                          │
├─ 15分钟视图 (v2) ────────────────────────────────┤
│ v2_filtered, v2_sig, v2_eps, v2_mu_v,            │
│ v2_sigma_v, v2_pair_count, v2_trade_count,       │
│ v2_bs_entry, v2_bs_exit                          │
├─ 5分钟视图 (v3) ─────────────────────────────────┤
│ v3_filtered, v3_sig, v3_eps, v3_mu_v,            │
│ v3_sigma_v, v3_pair_count, v3_trade_count,       │
│ v3_bs_entry, v3_bs_exit                          │
└──────────────────────────────────────────────────┘
```

每视图 9 列。总列数：7（基础）+ 4 × 9（视图）= 43 列。

**各列含义**：

| 列 | 来源阶段 | 类型 | 说明 |
|----|---------|------|------|
| `bar_index` | min_tf data | int | min_tf 的 bar 序号，从 0 开始，CSV 主键 |
| `bar_timestamp` | kline 表 `ts` 字段 | datetime/str | 该 bar 的真实开盘时刻（非 cutoff_date），ISO 8601 格式 |
| `close` | min_tf OHLC | float | 该 bar 的收盘价 |
| `open` | min_tf OHLC | float | 该 bar 的开盘价 |
| `high` | min_tf OHLC | float | 该 bar 的最高价 |
| `low` | min_tf OHLC | float | 该 bar 的最低价 |
| `volume` | min_tf OHLC | float | 该 bar 的成交量 |
| `v{i}_filtered` | S3 Filter | float | 滤波后的信号值 |
| `v{i}_sig` | S4 Schmitt | int | Schmitt 触发器输出：-1（做空）/ 0（中性）/ +1（做多） |
| `v{i}_eps` | S4 Schmitt | float | 自适应死区阈值，决定信号切换的灵敏度 |
| `v{i}_mu_v` | S4 Schmitt | float | 速度均值，Schmitt 内部变量，用于计算 eps |
| `v{i}_sigma_v` | S4 Schmitt | float | 速度标准差，Schmitt 内部变量，用于计算 eps |
| `v{i}_pair_count` | S5 Pairs | int | 当前活跃的信号对数量 |
| `v{i}_trade_count` | S7 Trades | int | 当前活跃的交易数量 |
| `v{i}_bs_entry` | S10 BS | str | 入场标记：`B`（买入）/ `S`（卖空）/ `-`（无） |
| `v{i}_bs_exit` | S10 BS | str | 离场标记：`B`（买入平仓）/ `S`（卖空平仓）/ `-`（无） |

### 5.3 关键设计决策

**决策 1：为什么以最小周期为行粒度？**

min_tf 是回测导航的锚定周期（见 §3.1 架构图中 `min TF = 回测导航的锚定周期`）。用户要求的"逐 bar 演进"以 min_tf 步进，每一行对应一次 `run_step()` 调用。如果以粗周期（如日线）为行粒度，则无法观察日内的信号演化过程 -- BS 跳跃可能发生在两个日线 bar 之间而非 bar 边界上，导致根因只能定位到"某天之内"而无法精确到具体时刻，定位精度从分钟级退化到天级。

**决策 2：粗周期数据如何填充？**

采用**前向填充（forward fill）**策略。理由：

- min_tf bar（如 5 分钟）推进时，粗周期（如日线）的计算结果只在粗周期 bar 结束时更新
- 例如：日线 bar #5 在 09:30 开盘，当日所有 5 分钟 bar（09:35, 09:40, ..., 16:00）都使用同一个 v0_filtered 值（来自以 bar #5 为右边界的那次计算）。直到次日日线 bar #6 的第一个 5 分钟 bar，v0_filtered 才更新为新的值
- 这准确反映了回测的实际计算逻辑：在每个 min_tf 步骤，粗周期视图的值就是最近一次粗周期 bar 的计算结果
- 不采用"仅对齐行有值，其他留空"：留空的 NaN 会阻断 pandas 的 `diff()`、`rolling()`、`corr()` 等时间序列操作，分析时需要额外 `fillna(method='ffill')`，增加使用门槛
- 不采用"只在对齐行写值，依赖下游工具填充"：CSV 作为自包含格式，不应要求使用者了解填充规则

实现方式：CSVBuilder 在 `accumulate()` 时，对每个视图独立跟踪"当前有效值"。粗周期视图（v0, v1, v2）的值在对应 bar 计算完成后才更新；min_tf 视图（v3）每行都更新。写入新行时，所有视图列填入各自的当前有效值，无需事后填充。

**决策 3：bar_timestamp 从哪里获取？**

从 kline 表查询：`SELECT ts FROM kline WHERE ticker=? AND timeframe=? ORDER BY ts`。使用 kline 表的 `ts` 字段而非 cutoff_date 的理由：

- `ts` 是 bar 的真实开盘时刻（如 `2025-06-01 09:35:00`），是一个物理事实
- `cutoff_date` 是回测逻辑概念（"可见数据到哪天为止"），由 `date_of(bar_index - 1)` 推算得出，可能因非交易日、停牌、数据缺失等原因偏离真实时间轴
- BS 信号需要关联到真实时间轴才能与回测图表、外部行情数据对齐

**决策 4：OHLCV 为什么必须记录？**

- BS 信号发生在特定价格水平上 -- 一个 B 标记在 bar 高点 $12.50 和低点 $11.80 有完全不同的含义（前者是追高，后者是抄底）
- 分析 BS 信号质量时需要确认信号是否出现在极端价位（如顶部买入）。仅有收盘价无法判断 bar 内部的波动范围
- 成交量异常放大常伴随 BS 标记出现（放量突破/放量反转），是判断信号可靠性的重要辅助指标
- 原始数据虽然在 `data/display/{tf}.parquet` 中，但 CSV 作为主要分析格式，自包含的 OHLCV 避免了分析时需要跨文件关联的麻烦

**决策 5：BS 状态如何编码？**

采用 **`B`/`S`/`-` 三字符编码**，`bs_entry` 和 `bs_exit` 各占一列。不采用数字编码或完整 JSON。

理由：
- `B`/`S`/`-` 在任何工具（文本编辑器、Excel、pandas、Numbers）中直接可读，无需查阅编码表
- 数字编码（+1/0/-1 或 1/0/-1）存在严重歧义：一个 `+1` 是做多入场还是做空离场？需要额外上下文判断，容易出错
- 完整 marker JSON（如 `{"bar_idx":118,"label":"B","color":"green","date":"2025-06-15","exit_reason":"trailing_stop"}`）塞入 CSV 单元格会导致列宽爆炸、pandas 读取后每个单元格需要 `json.loads()` 解析、Excel 中完全不可读
- `bs_entry` 和 `bs_exit` 分两列，支持独立过滤：`df[df['v0_bs_entry'] == 'B']` 筛选所有买入入场，`df[df['v0_bs_exit'] != '-']` 筛选有离场标记的行
- 需要完整 marker 元数据（date, color, exit_reason）时，从 events.jsonl 按 (bar_index, view) 交叉引用

### 5.4 CSVBuilder 接口

```
CSVBuilder(views: list[str], min_tf: str)

    accumulate(bar_index: int, bar_ts: datetime, ohlcv: dict,
               stage_data: PipelineStageData) -> None
        # 在内存 dict 中累积一行数据
        # 对每个 view：如果该 bar 该 view 有新计算值 → 更新该 view 的"当前有效值"
        #             如果该 bar 该 view 无新计算值 → 使用该 view 的"当前有效值"（前向填充）
        # 列值按固定顺序存入内存

    write(filepath: str) -> None
        # 将内存 dict 转换为 pandas DataFrame，按 bar_index 排序，一次性写 CSV
        # 列顺序：基础列 → v0 列 → v1 列 → v2 列 → v3 列
        # CSV 写入参数：index=False, 日期列使用 ISO 8601 格式
```

---

## 6. Bar 级时间序列设计

### 6.1 Bar 索引体系

最小周期 `bar_index` 作为 CSV 的主键和时间序列索引。其他周期的 bar 通过日期对齐：

| 周期 | bar_index 含义 | 与 min_tf 的对齐方式 |
|------|---------------|---------------------|
| min_tf（如 5 分钟） | 全局唯一，从 0 自增 | 自身，无需对齐 |
| 15 分钟（v2） | 15 分钟 bar 序号 | `bar_timestamp` 的 15 分钟边界对齐 |
| 60 分钟（v1） | 60 分钟 bar 序号 | `bar_timestamp` 的小时边界对齐 |
| 日线（v0） | 日线 bar 序号 | `bar_timestamp.date()` 对齐 |

对齐逻辑由 `_sync_all_cascading()` 中的数据同步机制保证 -- 该函数确保所有 TF 的 bar 边界一致。CSVBuilder **不重复实现对齐逻辑**，仅消费已对齐的数据：

- `_sync_all_cascading()` 负责将各 TF 的 kline 数据按日期对齐到统一的 bar 轴上
- `run_step()` 在每个 bar 上对所有视图执行计算管线，产出的 `PipelineStageData` 已经是该 bar 时刻各视图的一致状态
- CSVBuilder 的 `accumulate()` 只需要按视图维护"当前有效值"并写入即可

### 6.2 时间戳获取

从 kline 表获取 bar_timestamp。kline 表是系统中持久化 bar 数据的 SQLite 表，`ts` 字段存储每个 bar 的开盘时刻。

**数据获取方式**：

```
接口: _get_bar_timestamp(ticker: str, timeframe: str, bar_index: int) -> datetime

查询: SELECT ts FROM kline
       WHERE ticker=? AND timeframe=?
       ORDER BY ts
       LIMIT 1 OFFSET ?
```

**注意事项**：
- 使用 `ORDER BY ts` 确保序号稳定，不依赖 rowid
- `bar_index` 从 0 开始，`OFFSET bar_index` 直接定位
- kline 表的 `timeframe` 字段值与系统内部命名一致（如 `"5分钟"`、`"日线"`）
- CSV 输出时，bar_timestamp 格式化为 ISO 8601 字符串（如 `2025-06-01T09:35:00`）
- 若 kline 表不存在该 bar_index（数据缺失），写入空字符串，非阻断性错误

### 6.3 数据累积策略

回测过程中按 bar_index 在内存 dict 中逐行累积，`run()` 方法结束时用 pandas 一次性写入 CSV。

**为什么不采用逐行追加写入？**

- CSV 追加写入不可随机访问 -- 前向填充要求粗周期视图的值可能在未来的 bar 才知道，但写入时该行已经固化为文件内容
- 内存 dict 支持状态管理 -- CSVBuilder 内部维护每个视图的当前有效值，写入新行时直接读取即可
- I/O 效率 -- 逐行 fsync 增加系统调用开销，1300 行 × 每个 bar 一次 fsync 显著拖慢回测速度；批量写入一次 I/O 完成

**累积流程**：

```
run() 开始
  → CSVBuilder() 初始化，创建空 dict
  → for bar_index in range(start, end+1):
      data = run_step(bar_index, ...)
      csv_builder.accumulate(bar_index, bar_ts, ohlcv, data)
        内部逻辑：
          1. 读取 min_tf 的 OHLCV + bar_ts
          2. 遍历 views：若该 bar 该 view 有新计算值 → 更新 current_values[view]
          3. 将 current_values 所有视图的值扁平化为一行，存入 rows[bar_index]
  → csv_builder.write(path)
      → pd.DataFrame.from_dict(rows, orient='index')
      → .sort_index()
      → .to_csv(path, index=False)
```

### 6.4 内存估算

| 场景 | bar 数 | 列数 | 单值大小 | 总内存 |
|------|--------|------|---------|--------|
| 典型（500 bar 窗口） | 500 | 43 | ~8 bytes（float64）+ 少量 str | ~200 KB |
| 较大（1300 bar） | 1,300 | 43 | 同上 | ~500 KB |
| 极端（5000 bar） | 5,000 | 43 | 同上 | ~2 MB |

即使 5000 bar 的极端场景也仅 ~2 MB，完全在内存可接受范围内。**无需分块、无需数据库缓存、无需磁盘暂存**。

---

## 7. 补充数据项

基于对现有系统管道阶段的深入分析（见附录 C 管道阶段完整清单），以下数据项在之前的设计中被遗漏，应在 CSV 中补充记录：

| 数据项 | 优先级 | 来源阶段 | 理由 |
|--------|:------:|---------|------|
| OHLCV（开/高/低/收/量） | **P0** | S1 Raw Data | BS 信号分析的基础上下文。仅有收盘价无法判断信号是否出现在极端价位，成交量异常是信号可靠性的重要辅助指标。已在 §5.2 基础列中纳入。 |
| `bar_timestamp`（真实时刻） | **P0** | kline 表 `ts` 字段 | 用户明确要求。替代模糊的 bar_index 作为时间轴锚点，BS 信号需要关联到真实时间才能与回测图表对齐。已在 §5.2 基础列中纳入。 |
| `dur`（信号持续期） | **P1** | S4 Schmitt | Schmitt 触发器输出字典中的 `dur` 字段，表示当前信号方向（多/空/中性）已持续的 bar 数。长持续期（dur >= 5）的信号更稳定，短持续期（dur <= 2）的信号可能是 Schmitt 抖动噪声。过滤 `dur < 3` 的信号可有效排除假信号。 |
| 跨周期对齐指示 | **P2** | 推导值 | 标记当前 min_tf bar 在粗周期中的位置（如"日线 bar #3 的第 12 个 5 分钟 bar"）。用于分析跨周期信号一致性 -- 如日线做多但 5 分钟做空时的信号冲突检测。 |

**优先级定义**：

- **P0（MVP 必须）**：缺少则 CSV 无法作为独立分析格式。OHLCV 和 bar_timestamp 已在 §5.2 的基础列中纳入。
- **P1（强烈建议）**：对信号质量分析有显著价值，数据源已就绪（Schmitt 输出字典中已有 `dur` 字段），实现成本极低（仅需在 Schmitt 列组中增加一列）。建议 MVP 阶段一并实现。
- **P2（按需添加）**：特定分析场景有用，但非核心功能，可在 Phase 2 或按需添加。

**`dur` 的 CSV 列设计**：

在每视图列组中，`v{i}_sig_dur`（int 类型）紧邻 `v{i}_sig` 放置。例如 v0 列组：

```
v0_filtered, v0_sig, v0_sig_dur, v0_eps, v0_mu_v, v0_sigma_v, ...
```

列数影响：4 视图 × 1 列 = 4 列，总列数从 43 增至 47。内存影响可忽略（+4 × 8 bytes = 32 bytes/行）。

---

## 8. CLI 化方案

### 8.1 可行性结论：高

**核心依据**（基于代码级分析）：

1. **services/ 目录 ~83KB 代码零 Streamlit 依赖** -- 已通过 `grep` 验证，10 个滤波器、Schmitt 触发器、信号对检测、抛物线预测、策略 PnL、跨周期对齐、BS 标记 -- 全部为纯 Python 函数
2. **`_render_chart()` 计算管线高度模块化** -- 10+ 子步骤各自独立，唯一 Streamlit 耦合在结果展示层（`st.caption`, `st.warning`）和 `@st.cache_data` 装饰器（可剥离）
3. **回测播放本质是线性循环** -- 当前 `_run_backtest_play()` 通过 `st.session_state._bar_index = bar_index + 1` 推进，由 `main()` 循环驱动 `st.rerun()`，CLI 中简化为 `for bar_index in range(start_bar, end_bar+1): runner.run_step(bar_index, cutoff_date)`
4. **PipelineStageData 定义已就绪** -- 每个 bar 的中间输出结构已在 `pipeline_capture.py` 中定义，可直接复用

### 8.2 CLI 接口设计

```bash
# 最小可用命令
python -m filter.backtest_cli \
    --ticker 03690.HK \
    --market "港股 HK" \
    --preset 3690_HK_2 \
    --start-bar 0 \
    --end-bar 100 \
    --output-dir ./backtest_output/

# 完整参数
python -m filter.backtest_cli \
    --ticker AAPL \
    --market "美股 US" \
    --preset default \
    --start-date 2025-01-01 \
    --end-date 2025-06-30 \
    --operating-tf 日线 \
    --output-dir ./backtest_output/ \
    --log-level DEBUG
```

**参数矩阵**：

| 参数 | 必需 | 默认值 | 说明 |
|------|:----:|--------|------|
| `--ticker` | 是 | - | 股票代码 |
| `--market` | 是 | - | 市场标识 |
| `--preset` | 否 | - | 预设方案名（与 `--config-file` 二选一） |
| `--config-file` | 否 | - | JSON 配置文件路径 |
| `--start-bar` | 否 | 0 | bar 索引起始（与 `--start-date` 二选一） |
| `--end-bar` | 否 | 最后 bar | bar 索引结束 |
| `--start-date` | 否 | - | 日期范围起始 |
| `--end-date` | 否 | - | 日期范围结束 |
| `--operating-tf` | 否 | 取自预设 | 操作周期（BS 标记基准） |
| `--output-dir` | 否 | `./backtest_output/` | 输出目录 |
| `--log-level` | 否 | INFO | 日志级别 |

> **`--preset` 与 `--config-file` 都不提供时**：从 `config.db` 读取默认预设（与 Streamlit 行为一致）。两者都提供时 `--config-file` 优先。推荐使用 `argparse.add_mutually_exclusive_group()` 实现。

### 8.3 与 Streamlit 的关系：并行存在，非替代

```
Streamlit UI                     CLI
─────────────────────────────    ───────────────────────────
用途: 参数探索、可视化调试        用途: 批量回测、数据采集、CI
交互: 图表 + 导航 + 实时反馈     交互: 命令行 + 文件输出
何时用: 开发新策略、调参数        何时用: 运行完整回测、采集 BS 数据
```

**不推荐替代**的理由：Streamlit 的滤波曲线、Schmitt 信号、BS 标记图表是参数调优的核心工具，CLI 无法替代这种交互式可视化探索。

**代码共享方式**：`BacktestRunner` 作为共享计算核心，被 Streamlit 和 CLI 共同调用（而非各自重复实现）。Streamlit 保留其交互式图表渲染层，CLI 复用相同的 `services/` 计算管线 + `BacktestRunner`，仅替换数据展示方式（文件输出替代 st.* 调用）。两者共享 `services/` 目录的纯计算函数，无代码分叉风险。

### 8.4 最小实现路径

**改动量**: 3 个新文件 (~800 行) + 对现有文件 ~50 行修改

| 步骤 | 产出 | 改动量 |
|------|------|--------|
| Step 1: 抽取回测核心 | `filter/backtest_core.py` | ~400 行新文件，`state.py` +30 行修改 |
| Step 2: CLI 入口 | `filter/backtest_cli.py` | ~200 行新文件 |
| Step 3: 事件记录 | `services/event_recorder.py` | ~200 行新文件 |
| Step 4: 兼容性验证 | 无新文件 | ~20 行可选修改 |

**Step 1 详情**：
```
class BacktestRunner:
    def run_step(bar_index: int, cutoff_date: str) -> PipelineStageData:
        1. bar_ts = _get_bar_timestamp(min_tf, bar_index)  # 从 kline 表查询 ts 字段
        2. _sync_all_cascading()              → 数据准备
        3. for each view (v0-v3):
             a. _load_chart_data()            → 窗口数据  (streamlit_app.py, 需提取)
             b. _compute_filters()            → 滤波       (streamlit_app.py, 含 @st.cache_data)
             c. v,g = np.gradient(filtered,t)  → 速度/加速度
             d. _schmitt_trigger(v, a, ...)   → 施密特信号 (filter_engine.py)
             e. _find_all_pairs(sig)          → 信号对     (filter_engine.py)
             f. _fit_physics_parabola()       → 抛物线预测 (filter_engine.py)
             g. _compute_strategy_pnl()       → 策略 PnL   (filter_engine.py)
             h. _compute_holding_masks()      → 持仓掩码   (filter_engine.py)
             i. compute_bs_markers()          → BS 标记    (bs_marker.py)
        4. 收集各阶段输出 → PipelineStageData（含 bar_ts）
    def run(start_bar: int, end_bar: int) -> None:
        csv_builder = CSVBuilder(views=["v0", "v1", "v2", "v3"])
        for i in range(start_bar, end_bar+1):
            data = self.run_step(i, _get_bar_date_from_db(min_tf, i-1))
            self.recorder.record(data, i)
            csv_builder.accumulate(i, data)              # 内存 dict 累积
        csv_builder.write(f"{output_dir}/unified_output.csv")  # 一次性写入
```

**风险与缓解**：

| 风险 | 缓解 |
|------|------|
| `_load_chart_data()` 中 `_is_backtest` 局部变量在两分支赋值后于分支外引用 | 无运行时 bug，但提取到 BacktestRunner 时自然消除 |
| `AppState` 依赖 `st.session_state` | `state.py` 已有 `if st is None: return` 守卫，CLI 不导入 streamlit 时 `get()` 返回默认值、`set()` 静默跳过 |
| `@st.cache_data` 在 CLI 中不可用 | CLI 中每步独立计算，不使用缓存装饰器 |
| `data/display/{tf}.parquet` 每次覆盖写入 | CLI 单进程运行，无并发冲突 |

---

## 9. 实现路径

### Phase 1: MVP 🎯

**目标**: 能运行 CLI 回测，产生 BS 变动事件流 + CSV 统一输出，能回答"BS 在哪步变了、怎么变的"

| # | 功能 | 预估工作量 | 产出 |
|---|------|-----------|------|
| 🎯 1 | `BacktestRunner` 类 -- 从 `_render_chart()` 抽取纯计算管线；每步返回 `bar_timestamp`（从 kline 表 `ts` 字段获取） | 1-2 天 | `filter/backtest_core.py` |
| 🎯 2 | `AppState` dict fallback -- CLI 中替代 `st.session_state` | 0.5 天 | `state.py` 改动 |
| 🎯 3 | CLI 入口 -- argparse 参数解析 + 配置加载 | 0.5 天 | `filter/backtest_cli.py` |
| 🎯 4 | Event Recorder + CSVBuilder -- BS 变动事件生成 + events.jsonl 写入 + CSV 内存累积（end_session 时 pandas 一次性写入） | 1 天 | `services/event_recorder.py` |
| 🎯 5 | Filter Tail Recorder -- filter_tail.jsonl 写入 | 0.5 天 | 同上文件 |
| 🎯 6 | 端到端验证 -- `3690_HK_2` 预设，重现 645x 级联放大 | 1 天 | 分析脚本 |
| 🎯 7 | CSV 测试用例 -- 列完整性验证、行数验证（与 min_tf bar 数一致）、bar_timestamp 单调递增验证、前向填充正确性验证 | 0.5 天 | `tests/test_csv_output.py` |

**MVP 不包含**:
- schmitt_snapshot.jsonl / trade_summary.jsonl（Phase 2）
- 图表输出（`--output-charts` 选项）
- 完整 steps/ 快照存储
- CI 集成

### Phase 2: 方向性展望

以下为 MVP 验证后可能的方向，非已规划阶段，实际优先级取决于 MVP 暴露的真实瓶颈：

- **Schmitt snapshot + Trade summary 记录**：若 MVP 验证 BS 变动与 schmitt/trade 变化强关联，补充这两项以完成级联放大分析
- **`--output-charts` 支持**：若需要批量为多个 ticker 生成回测图表，添加 Plotly HTML 静态输出
- **级联放大自动计算**：若 645x 案例重现成功，封装 cascade 事件生成逻辑为可复用工具

---

## 10. GitHub 管理策略

### 10.1 仓库现状

| 项目 | 详情 |
|------|------|
| 仓库 | `pxf0797/filter-algorithms-research` |
| 当前分支 | `investigate-bs-jump-3690-HK-2` |
| 默认分支 | `master` |
| 跟踪文件 | 172 个 |

### 10.2 变更追踪

- **框架设计文档**：纳入 `docs/` 目录，作为项目架构文档的一部分
- **新增代码**（`backtest_core.py`, `backtest_cli.py`, `event_recorder.py`）：纳入 `filter/` 目录，正常跟踪
- **回测输出**（`backtest_output/`）：加入 `.gitignore`，不纳入版本控制
- **分支策略**：功能开发在独立分支进行，合并至 `master` 前通过 PR 审查

### 10.3 大文件管理

| 文件类型 | 策略 | 原因 |
|---------|------|------|
| `*.parquet` | 全局 `.gitignore`，不跟踪 | 已在 `.gitignore` 中配置；数据文件非代码 |
| `*.db`, `*.sqlite3` | 全局 `.gitignore`，不跟踪 | 同上 |
| `backtest_output/` | 目录级 `.gitignore`，不跟踪 | 回测产物，可重建 |
| `config/` | `.gitignore` 排除（含敏感配置） | 已在规则中 |

**对本次项目的影响**：框架设计文档和新增代码正常提交；回测输出数据不提交。

---

## 附录

### A: 术语表

| 术语 | 全称 | 说明 |
|------|------|------|
| BS | Buy/Sell | 买卖标记，回测图表上的交易信号标注 |
| TF | Timeframe | 时间周期（日线、60分钟、15分钟、5分钟等） |
| operating TF | 操作周期 | BS 标记生成的基准周期 |
| min TF | 最小周期 | 回测导航的锚定周期 |
| Schmitt Trigger | 施密特触发器 | 将滤波信号转为离散交易信号 (-1/0/+1) 的算法 |
| eps | 自适应死区 | Schmitt 触发器的动态阈值，决定信号切换的灵敏度 |
| Signal Pair | 信号对 | 连续同向 Schmitt 信号构成的区间 |
| Cascade Amplification | 级联放大 | 输入微小变化经管道逐级放大产生输出显著变化的现象 |
| Event Sourcing | 事件溯源 | 只记录状态变化事件（增量），通过重放事件重建状态 |
| Snapshot | 快照 | 某时刻的完整状态副本（全量） |
| JSONL | JSON Lines | 每行一个独立 JSON 对象，支持流式追加和逐行解析 |
| Structural Jump | 结构性跳变 | 管道输出发生质变（如新增/消失 BS 标记）的步骤 |
| cutoff_date | 截断日期 | 回测窗口的右边界，模拟"该时刻可见的数据范围" |

### B: 关键决策记录

**Q1: 为什么选 Event Sourcing 而不是 Snapshot?**

| 对比 | Snapshot | Event Sourcing |
|------|----------|----------------|
| BS 变动查询 | 事后 diff 1300 对数据 | `grep bs_added events.jsonl` 直接定位 |
| 存储量 | 156 MB（90%+ 重复） | 5-8 MB（只存变化） |
| 分析效率 | 需要编写 diff 脚本 | shell 命令即可 |
| 实现复杂度 | 低（已实现） | 中（~50 行对比逻辑） |

选择 Event Sourcing 的核心原因：**BS 变动分析本身就是"找变化"**，Event Sourcing 把"变化"作为一等公民记录，而 Snapshot 把"变化"隐藏在"状态"中需要事后挖掘。前者更匹配问题本质。

**Q2: 主事件流用 JSONL，何时用 Parquet?**

> 存储布局中 events.jsonl、filter_tail.jsonl、schmitt_snapshot.jsonl、trade_summary.jsonl 全部使用 JSONL。Parquet 仅用于 steps/ 目录下的可选数值快照。

**JSONL（主事件流）**：人类可读、支持流式追加（`>> events.jsonl`）、可直接用 `grep`/`jq`/`tail` 分析。每行一个独立 JSON 对象，天然适合事件流记录。BS 变动事件、filter 尾部、schmitt 变化量均为小体积结构化记录，JSONL 开销可忽略。

**Parquet（可选快照）**：仅当需要保存某步的完整数值数组（filter 全量、schmitt 全量）时使用。数值密集型数据在 Parquet 的列式存储 + Snappy 压缩下空间和读取速度远超 JSON。

**Q3: 为什么 Event Log 用 JSONL 而不是 SQLite?**

- SQLite 增加一个数据库依赖，违反"极简"原则
- 文件系统就是最好的索引 -- step 目录天然分区
- JSONL 支持流式追加（`>> events.jsonl`），不需要事务管理
- `grep`/`jq`/`tail` 等标准工具即可分析，不需要 SQL
- 当前数据规模（单次回测 < 10MB）文件方案完全够用

### C: 管道阶段完整清单

| # | 阶段名 | 函数 | 输入 | 输出 | 记录策略 |
|---|--------|------|------|------|---------|
| S1 | Raw Data | `_load_chart_data()` | Parquet 文件 | t, noisy, ohlc, dates | 不记录 |
| S2 | Date Markers | `_date_markers()` | t, dates | marker_positions, labels | 不记录 |
| S3 | Filters | `_compute_filters()` | noisy, config | filtered, [filtered2] | Tail (10 pts) |
| S4 | Schmitt | `_compute_schmitt_trigger()` | filtered, params | sig, v, a, eps, dur, mu_v, sigma_v | 增量摘要（JSONL）/ 逐 bar 全量（CSV） |
| S5 | Signal Pairs | `_find_all_pairs()` | sig | all_pairs | 含在 S4 |
| S6 | Predictions | `_compute_prediction_pairs()` | filtered, all_pairs | prediction_pairs | 不记录 |
| S7 | Strategy PnL | `_compute_strategy_pnl()` | t, filtered, sig, pairs, pred | pnl, trade_records | 摘要 |
| S8 | Higher TF PnL | `_align_pnl_to_current_tf()` | higher_pnl, t | aligned_pnl | 不记录 |
| S9 | Holding Masks | `_compute_holding_masks()` | n_bars, entry, exit markers | long_mask, short_mask | 不记录 |
| S10 | BS Markers | `compute_bs_markers()` | t, dates, schmitt, pairs, trades | entry/exit markers | **Event Sourcing** |
| S11 | Display | `_add_bs_markers()` (components/charts.py) | markers | Plotly annotations | 不记录 |
