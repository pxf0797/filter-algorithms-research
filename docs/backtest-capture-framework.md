# 回测数据采集系统 -- 宏观框架设计

> 版本: v1.0 | 日期: 2026-07-13 | 状态: MVP 设计阶段

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

**关键发现**：整个 `services/` 目录（~83KB 核心计算代码）已验证 **零 Streamlit 引用**（`grep -c 'st\.' services/*.py` 全部返回 0）。计算管线高度模块化，剥离 UI 层后可直接被 CLI 复用。

### 2.2 核心不足（按严重性排序）

| # | 不足 | 严重性 | 影响 |
|---|------|--------|------|
| 1 | BS 变动无事件记录，只能事后 diff | **高** | 根因分析需要 1300 步逐对对比，效率极低 |
| 2 | 管道关键中间量未捕获（`mu_v`, `sigma_v`, `eps` 内部演化） | **高** | 级联放大分析链断裂，无法精确计算放大倍数 |
| 3 | 无输入数据窗口引用，复现依赖外部文件一致性 | 中 | Parquet 文件更新后历史记录失效 |
| 4 | Snapshot 每步 120KB 全量保存，90%+ 数据重复 | 中 | 完整回测 156MB，多次运行快速占满磁盘 |
| 5 | `_is_backtest` 变量在 `_load_chart_data()` line 183/191 存在代码异味 | 低 | 变量在条件分支中赋值但在分支外使用，可读性差 |

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
    │       "日线":  "data/display/03690.HK_1d.parquet",
    │       "60分钟": "data/display/03690.HK_60m.parquet"
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
- `data/display/` 是**原始价格数据源**，由 DB 同步写入，已通过 `data_manifest.json` 引用
- `backtest_output/` 是**管道输出记录**，为新系统产出，完全独立于 `data/display/`
- 两者通过 `data_manifest.json` 中的路径引用关联，不复制数据
- Parquet 文件已加入 `.gitignore`（全局 `*.parquet` 规则），不纳入版本控制

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
  → data_manifest.json 中查找该视图数据源: "data/display/03690.HK_1d.parquet"
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
- `pair_split`/`pair_merge` 事件：信号对的结构性变化是 BS 跳跃的常见原因

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
    if step_index == 0:
        emit("init", markers=current_bs)
        return
    current_set = {(m["bar"], m["type"], m["dir"]) for m in current_bs}
    prev_set    = {(m["bar"], m["type"], m["dir"]) for m in prev_bs}
    for m in current_set - prev_set:
        emit("bs_added", marker=m, step=step_index)
    for m in prev_set - current_set:
        emit("bs_removed", marker=m, step=step_index)
    # moved: 检测同 trade_id 不同 bar_index 的情况
```

---

## 5. CLI 化方案

### 5.1 可行性结论：高

**核心依据**（基于代码级分析）：

1. **services/ 目录 ~83KB 代码零 Streamlit 依赖** -- 已通过 `grep` 验证，10 个滤波器、Schmitt 触发器、信号对检测、抛物线预测、策略 PnL、跨周期对齐、BS 标记 -- 全部为纯 Python 函数
2. **`_render_chart()` 计算管线高度模块化** -- 10+ 子步骤各自独立，唯一 Streamlit 耦合在结果展示层（`st.caption`, `st.warning`, `st.components.v1.html()`）
3. **回测播放本质是线性循环** -- 当前 `_run_backtest_play()` 通过 `st.session_state._bar_index = bar_index + 1` 推进，由 `main()` 循环驱动 `st.rerun()`，CLI 中简化为 `for bar_index in range(start, end+1): compute_and_record(bar_index)`
4. **PipelineStageData 定义已就绪** -- 每个 bar 的中间输出结构已定义，可直接序列化

### 5.2 CLI 接口设计

```bash
# 最小可用命令
python -m filter_app.backtest_cli \
    --ticker 03690.HK \
    --market "港股 HK" \
    --preset 3690_HK_2 \
    --start-bar 0 \
    --end-bar 100 \
    --output-dir ./backtest_output/

# 完整参数
python -m filter_app.backtest_cli \
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

### 5.3 与 Streamlit 的关系：并行存在，非替代

```
Streamlit UI                     CLI
─────────────────────────────    ───────────────────────────
用途: 参数探索、可视化调试        用途: 批量回测、数据采集、CI
交互: 图表 + 导航 + 实时反馈     交互: 命令行 + 文件输出
何时用: 开发新策略、调参数        何时用: 运行完整回测、采集 BS 数据
```

**不推荐替代**的理由：Streamlit 的滤波曲线、Schmitt 信号、BS 标记图表是参数调优的核心工具，CLI 无法替代这种交互式可视化探索。

**代码共享方式**：`BacktestRunner` 作为共享计算核心，被 Streamlit 和 CLI 共同调用（而非各自重复实现）。Streamlit 保留其交互式图表渲染层，CLI 复用相同的 `services/` 计算管线 + `BacktestRunner`，仅替换数据展示方式（文件输出替代 st.* 调用）。两者共享 `services/` 目录的纯计算函数，无代码分叉风险。

### 5.4 最小实现路径

**改动量**: 3 个新文件 (~800 行) + 对现有文件 ~50 行修改

| 步骤 | 产出 | 改动量 |
|------|------|--------|
| Step 1: 抽取回测核心 | `filter_app/backtest_core.py` | ~400 行新文件，`state.py` +30 行修改 |
| Step 2: CLI 入口 | `filter_app/backtest_cli.py` | ~200 行新文件 |
| Step 3: 事件记录 | `services/event_recorder.py` | ~200 行新文件 |
| Step 4: 兼容性验证 | 无新文件 | ~20 行可选修改 |

**Step 1 详情**：
```
class BacktestRunner:
    def run_single_bar(bar_index, cutoff_date) -> PipelineStageData:
        1. _sync_all_cascading()     → 数据准备
        2. for each view (v0-v3):
             a. _load_chart_data()   → 窗口数据
             b. _compute_filters()   → 滤波
             c. _compute_schmitt_trigger() → 信号
             d. _find_all_pairs()    → 信号对
             e. _compute_prediction_pairs() → 预测
             f. _compute_strategy_pnl() → PnL
             g. _compute_holding_masks()  → 持仓
             h. compute_bs_markers() → BS 标记
        3. 返回完整 PipelineStageData
    def run_range(start, end) -> None:
        for i in range(start, end+1):
            data = self.run_single_bar(i, get_cutoff_date(i))
            self.recorder.record(data, i)
```

**风险与缓解**：

| 风险 | 缓解 |
|------|------|
| `_load_chart_data()` 中 `_is_backtest` 作用域 bug | Step 1 修复 |
| `AppState` 的 `st.session_state` 依赖 | 添加内置 dict fallback（已有 `if st is None` 守卫） |
| `@st.cache_data` 在 CLI 中失效 | 移除装饰器，每步独立计算（CLI 顺序执行不依赖缓存） |
| Parquet 并发访问（Streamlit + CLI 同时运行） | 文档注明不支持并行 |

---

## 6. 实现路径

### Phase 1: MVP 🎯

**目标**: 能运行 CLI 回测，产生 BS 变动事件流，能回答"BS 在哪步变了、怎么变的"

| # | 功能 | 预估工作量 | 产出 |
|---|------|-----------|------|
| 🎯 1 | `BacktestRunner` 类 -- 从 `_render_chart()` 抽取纯计算管线 | 1-2 天 | `filter_app/backtest_core.py` |
| 🎯 2 | `AppState` dict fallback -- CLI 中替代 `st.session_state` | 0.5 天 | `state.py` 改动 |
| 🎯 3 | CLI 入口 -- argparse 参数解析 + 配置加载 | 0.5 天 | `filter_app/backtest_cli.py` |
| 🎯 4 | Event Recorder -- BS 变动事件生成 + events.jsonl 写入 | 0.5 天 | `services/event_recorder.py` |
| 🎯 5 | Filter Tail Recorder -- filter_tail.jsonl 写入 | 0.5 天 | 同上文件 |
| 🎯 6 | 端到端验证 -- `3690_HK_2` 预设，重现 645x 级联放大 | 1 天 | 分析脚本 |

**MVP 不包含**:
- 多视图同时记录（只追踪 operating TF）
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

## 7. GitHub 管理策略

### 7.1 仓库现状

| 项目 | 详情 |
|------|------|
| 仓库 | `pxf0797/filter-algorithms-research` |
| 当前分支 | `investigate-bs-jump-3690-HK-2` |
| 默认分支 | `master` |
| 跟踪文件 | 172 个 |

### 7.2 变更追踪

- **框架设计文档**：纳入 `docs/` 目录，作为项目架构文档的一部分
- **新增代码**（`backtest_core.py`, `backtest_cli.py`, `event_recorder.py`）：纳入 `filter_app/` 目录，正常跟踪
- **回测输出**（`backtest_output/`）：加入 `.gitignore`，不纳入版本控制
- **分支策略**：功能开发在独立分支进行，合并至 `master` 前通过 PR 审查

### 7.3 大文件管理

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
| S4 | Schmitt | `_compute_schmitt_trigger()` | filtered, params | sig, v, a, eps | 增量摘要 |
| S5 | Signal Pairs | `_find_all_pairs()` | sig | all_pairs | 含在 S4 |
| S6 | Predictions | `_compute_prediction_pairs()` | filtered, all_pairs | prediction_pairs | 不记录 |
| S7 | Strategy PnL | `_compute_strategy_display()` | t, filtered, sig, pairs, pred | pnl, trade_records | 摘要 |
| S8 | Higher TF PnL | `_align_pnl_to_current_tf()` | higher_pnl, t | aligned_pnl | 不记录 |
| S9 | Holding Masks | `_compute_holding_masks()` | n_bars, entry, exit markers | long_mask, short_mask | 不记录 |
| S10 | BS Markers | `compute_bs_markers()` | t, dates, schmitt, pairs, trades | entry/exit markers | **Event Sourcing** |
| S11 | Display | `_add_bs_markers()` | markers | Plotly annotations | 不记录 |
