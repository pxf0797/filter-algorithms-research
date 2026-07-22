# 数据记录缺口分析

> 审查日期: 2026-07-13
> 审查范围: EventRecorder / PipelineCapture / BacktestRunner 三系统的数据覆盖完整性
> 审查方法: 逐阶段源码对照 + 用户需求对照 + 追溯链完整性验证

---

## 1. 逐阶段覆盖矩阵

管道共 11 个逻辑阶段。下表逐阶段列出**实际产生的数据字段**（以 `backtest_core.py:357-424` `_compute_pipeline_for_view` 返回值为权威来源），与三个记录系统的覆盖情况对照。

### 阶段数据一览

| # | 阶段 | 产生的数据字段 | PipelineCapture (`_write_view`) | EventRecorder (`_record_view_step`) | BacktestRunner 返回 (`_compute_pipeline_for_view`) | 遗漏 |
|---|------|---------------|-------------------------------|-------------------------------------|---------------------------------------------------|------|
| 0 | 数据同步 | `t`, `noisy`, `ohlc`, `dates` (via `_load_window_data`) | **无条件跳过** (注释: "available in parquet") | **无** | **返回全部**: `t`, `dates`, `noisy`, `ohlc` | `noisy`(收盘价)在 EventRecorder 中完全缺失; `ohlc` 两个记录系统均不写; `t` 索引两个记录系统均不写 |
| 1 | 滤波计算 | `filtered`, `filtered2` | **captures**: `stage_03_filter.parquet` (t, filtered, filtered2) | **partial**: `filter_tail.jsonl` (仅 tail 5) | **返回**: `filtered`, `filtered2` | `filtered2` 在 EventRecorder 中完全缺失; 完整 `filtered` 数组在 EventRecorder 中仅存尾部 |
| 2 | 施密特触发器 | `v`, `a` (中间变量), `mu_v`, `sigma_v`, `eps`, `sig`, `dur` | **captures**: `stage_04_schmitt.parquet` (t, sig, v, a, eps, mu_v, sigma_v) | **设计为记录但实为 BUG**: 尝试读取 `view_data.get("sig")` 和 `view_data.get("eps")` — 但这些键不存在于 BacktestRunner 输出中(实际在 `schmitt.sig` / `schmitt.eps`)。结果 `sig_counts` 和 `eps_tail` 从未被写入 | **返回**: `schmitt` dict (含 mu_v, sigma_v, eps, sig, dur) — 但 **不含 `v`, `a`** | **P0 BUG**: EventRecorder `sig`/`eps` 键路径错误; `v`, `a`, `mu_v`, `sigma_v`, `dur` 在 EventRecorder 中全缺; `v`, `a` 在 BacktestRunner 返回值中也缺失 |
| 3 | 信号对查找 | `all_pairs`: list[(start_idx, end_idx)] | **captures**: `stage_05_pairs.json` (all_pairs + pair_count) | **partial**: `pair_count` only (不记录具体 pair 元组) | **返回**: `all_pairs` | EventRecorder 不记录具体 pair 起止点 |
| 4 | 预测曲线 | `prediction_pairs`: list[dict] (含 fit_result, fit_start, pair_end) | **跳过** (注释: "derivable from pairs + filtered") | **无** | **返回**: `prediction_pairs` | EventRecorder 完全缺失; PipelineCapture 刻意跳过 |
| 5 | 策略 PnL | `long_pnl`, `short_pnl` (np.ndarray), `trade_records`: list[dict] (id, type, entry_idx, exit_idx, entry_price, exit_price, return_pct, exit_reason) | **captures**: `stage_07_pnl.parquet` (t, pnl_long, pnl_short) + `stage_07_trades.json` (trade_records) | **partial**: `trade_summary.jsonl` (trade_count, long_count, short_count, win_rate) — 无明细, 无 PnL 数组 | **返回**: `long_pnl`, `short_pnl`, `trade_records` | EventRecorder: `long_pnl`/`short_pnl` 数组完全缺失; trade 明细完全缺失 |
| 6 | 跨周期对齐 | `higher_pnl`: dict (aligned_long, aligned_short, entry_markers, exit_markers) | **跳过** (注释: "duplicative") | **无** | **返回**: `higher_pnl` (在 `run()` 中附加) | EventRecorder 完全缺失 |
| 7 | 持仓掩码 | `long_mask`, `short_mask` (bool np.ndarray) | **captures**: `stage_09_masks.parquet` | **无** | **返回**: `long_mask`, `short_mask` (在 `run()` 中附加) | EventRecorder 完全缺失 |
| 8 | BS 标记 | `bs_markers`: dict (entry_markers: list[(bar_idx, "B"/"S", "green"/"red", date)]; exit_markers: list[(bar_idx, "B"/"S", "green"/"red", exit_reason, date)]) | **captures**: `stage_10_bs_markers.json` | **partial**: `events.jsonl` — 仅差异事件 (bs_added/bs_removed/bs_modified/bs_stable)，无全量快照 | **返回**: `bs_markers` | 无全量 BS 快照; 事件流丢失可能导致状态重建失败 |

### 跨阶段遗漏

| # | 数据类型 | PipelineCapture | EventRecorder | 说明 |
|---|---------|----------------|---------------|------|
| C1 | `cutoff_date` (每步时间戳) | **captures**: `index.json` per step | **partial**: 仅 `filter_tail.jsonl` 有; `schmitt_snapshot.jsonl` 和 `trade_summary.jsonl` 均无 `cutoff_date` | 无法将 schmitt/trade 事件关联到具体 bar 时间 |
| C2 | Bar 索引 `step_index` | **captures**: `index.json` per step | **captures**: 所有事件均有 `step` 字段 | OK |
| C3 | 视图标识 `view_name` | **captures**: 目录名 | **captures**: 所有事件均有 `view` 字段 | OK |

---

## 2. 缺口清单（按严重度排序）

### P0 — 必须修复（阻断因果分析或功能实际不工作）

| # | 缺口 | 缺失数据 | 影响 | 修复建议 |
|---|------|---------|------|---------|
| **P0-1** | **EventRecorder `sig`/`eps` 键路径 BUG** | `sig_counts` 和 `eps_tail` 从未被写入 `schmitt_snapshot.jsonl` | `_record_view_step` 第 321-322 行执行 `view_data.get("sig")` 和 `view_data.get("eps")`，但 BacktestRunner 返回的结构中 `sig` 和 `eps` 嵌套在 `view_data["schmitt"]["sig"]` 和 `view_data["schmitt"]["eps"]` 中。这两个 `get()` 始终返回 `None`。条件 `sig is not None or eps is not None or all_pairs is not None` 仅因 `all_pairs` 非空才为 True，因此快照记录仅含 `pair_count`，从未包含过 `sig_counts` 或 `eps_tail`。 | 修改 `_record_view_step`，从 `view_data.get("schmitt", {})` 中提取 `sig` 和 `eps`：`schmitt = view_data.get("schmitt", {}); sig = schmitt.get("sig"); eps = schmitt.get("eps")` |
| **P0-2** | **EventRecorder 完全不记录收盘价 `noisy`** | 每步各周期的原始收盘价序列 | 用户明确要求"股票的不同周期收盘价"每步记录。当前 `noisy` 仅存在于 `data/display/{tf}.parquet` 文件中(会被下一步覆盖)，EventRecorder 完全不记录。无法验证滤波是否在真实价格上运行，无法复现价格→filter 的因果链。 | 新增 JSONL 流 `price_snapshot.jsonl`，记录每步各周期的 `noisy` 数组(或 tail N 值 + 最新值)；或扩展现有流增加 `noisy_tail` 字段 |
| **P0-3** | **EventRecorder 仅记录 `filter_tail(5)`，缺失完整 `filtered` 数组** | 完整滤波价格序列 | 用户要求追踪"filter变化"。5 个尾部值无法还原滤波曲线的演变过程。当 BS 变动由 filter 形态变化触发时，无法从记录中定位是哪些 bar 的 filter 值变化导致了级联效应。 | 新增 JSONL 流 `filter_snapshot.jsonl` 记录完整 filtered 数组；或至少扩大 tail 到足够长度(建议 20+)，并增加 `filtered_stats` (min/max/mean/std) |
| **P0-4** | **`v` 和 `a` 在 BacktestRunner 返回值中缺失** | 速度/加速度中间变量 | `_compute_schmitt_trigger` (backtest_core.py:506-507) 计算了 `v` 和 `a` 但只传给 `_schmitt_trigger`，不包含在返回的 `schmitt` dict 中。这导致：(1) EventRecorder 无法记录 `v` 和 `a` (即使用户修复了 P0-1，也只能拿到 `sig` 和 `eps`); (2) PipelineCapture 在 `_render_chart` 中**重复计算** `v` 和 `a` (streamlit_app.py:864-865)，造成不一致风险。 | 扩展 `_schmitt_trigger` 返回值或修改 `_compute_schmitt_trigger` 使返回值包含 `v` 和 `a`；同时扩展 `_compute_pipeline_for_view` 的返回 dict |
| **P0-5** | **EventRecorder 和 PipelineCapture 是两条独立路径，无统一记录** | 无单一真实来源 | CLI 回测 (`backtest_cli.py`) 仅使用 EventRecorder；Streamlit 回测 (`streamlit_app.py`) 仅使用 PipelineCapture。两套系统的记录粒度和格式完全不同：(a) CLI 路径缺失 PipelineCapture 的所有 Parquet+JSON 详情; (b) Streamlit 路径缺失 EventRecorder 的事件流(BS 变动、filter tail、schmitt snapshot、trade summary)。用户无法在两种模式下获得一致的调试数据。 | **方案 A**: 在 BacktestRunner.run() 中统一调用两个记录器; **方案 B**: 将 EventRecorder 的 JSONL 流能力合并到 PipelineCapture 中，统一为一套记录系统 |

### P1 — 应该修复（影响分析深度）

| # | 缺口 | 缺失数据 | 影响 | 修复建议 |
|---|------|---------|------|---------|
| **P1-1** | **EventRecorder 缺失 `mu_v` 和 `sigma_v`** | EWMA 速度和波动率统计量 | `mu_v` 和 `sigma_v` 是自适应阈值 `eps` 的计算基础。无法从记录中验证 `eps` 是否合理。PipelineCapture 有但 EventRecorder 无。 | 在 `schmitt_snapshot.jsonl` 中增加 `mu_v_tail` 和 `sigma_v_tail` 字段 |
| **P1-2** | **EventRecorder 缺失 `v` 和 `a`** | 速度和加速度原始序列 | 施密特触发器的直接输入。sig 翻转为 +1 需要 `a > eps AND v > 0`——没有 `v` 和 `a` 无法判断 sig 翻转是由 a 驱动还是 v 驱动。 | 修复 P0-4 后，在 EventRecorder 中增加 `v_tail` / `a_tail` |
| **P1-3** | **EventRecorder 仅记录 `pair_count`，不记录具体 `all_pairs` 元组** | 多空切换对的起止 bar 索引 | Pairs 是连接 sig → trade 的关键结构。没有 pair 的 (start_idx, end_idx)，无法判断某笔 trade 对应哪个信号段，无法验证 trade_records 是否正确。 | 将 `all_pairs` 完整写入 `schmitt_snapshot.jsonl`(对于大量 pairs 的视图，可用 `pairs_summary`: [(start, end, direction), ...]) |
| **P1-4** | **EventRecorder 仅记录 trade 摘要，缺失明细** | 每笔交易: entry_idx, exit_idx, entry_price, exit_price, return_pct, exit_reason | Trade 是 BS 标记的直接来源。没有明细无法验证：(1) BS marker 的 bar_idx 是否与 trade entry_idx 匹配; (2) 止损是否在正确的价格触发; (3) 收益率计算是否正确。 | 新增 `trade_detail.jsonl` 流，完整记录每步的 trade_records；或扩展 `trade_summary.jsonl` 增加 `trades` 数组字段 |
| **P1-5** | **EventRecorder 缺失 `long_pnl` / `short_pnl` 数组** | 策略收益曲线 | PnL 是策略的最终输出。没有 PnL 数据无法验证策略表现。`trade_summary` 中的 win_rate 是基于 PnL 计算的，但没有原始数据无法交叉验证。 | 新增 `pnl_snapshot.jsonl` 或在 `trade_summary.jsonl` 中增加 `pnl_stats`(last_value, max_drawdown, total_return) |
| **P1-6** | **EventRecorder 缺失 `long_mask` / `short_mask`** | 跨周期同向性过滤的持仓区间 | Mask 决定了哪些 trade 被过滤、哪些 BS marker 被生成。没有 mask，无法理解为什么某些 signals 产生的 trade 被标记为 BS 而另一些没有。这直接阻断跨周期因果分析。 | 在 `schmitt_snapshot.jsonl` 中增加 `mask_stats`(long_bars, short_bars)；或新增 `mask_snapshot.jsonl` |
| **P1-7** | **EventRecorder BS 事件无全量快照，仅增量差异** | 完整的 BS marker 状态 | Event Sourcing 模式要求可以重放事件重建状态。当前 `events.jsonl` 记录 bs_added/removed/modified/stable_count，但：(1) `bs_stable` 仅含计数不含 marker 列表; (2) `bs_modified` 不含修改前的旧值; (3) 如果丢失一个事件(如崩溃后重启)，无法重建完整 BS 状态。 | 每 N 步(如每 10 步)写入一次 `bs_full_snapshot` 事件(含完整的 entry_markers + exit_markers)；或在 session 开始时记录初始状态 |
| **P1-8** | **EventRecorder `higher_pnl` 完全缺失** | 高周期 PnL 对齐到当前周期的结果 | 跨周期 PnL 对齐是 BS marker 生成的核心步骤(操作周期的 BS 受高周期 PnL 的 holding_masks 过滤)。缺失此数据无法追踪跨周期过滤逻辑。 | 新增 `cross_period.jsonl` 记录 aligned entry_markers 和 exit_markers |
| **P1-9** | **`schmitt_snapshot.jsonl` 和 `trade_summary.jsonl` 缺失 `cutoff_date`** | 事件发生的时间戳 | `filter_tail.jsonl` 有 `cutoff_date`，但 `schmitt_snapshot.jsonl` 和 `trade_summary.jsonl` 只有 `step` 索引。事后分析时需手动关联 step→date 映射(仅存在于 `metadata.json` 或 `filter_tail.jsonl` 中)。 | 统一在所有事件流中增加 `cutoff_date` 字段 |
| **P1-10** | **PipelineCapture 缺失 `noisy` 和 `ohlc` 写入** | 原始价格数据 | `PipelineStageData` 定义了 `noisy` 和 `ohlc` 槽位，`_render_chart` 也填充了这些值(streamlit_app.py:869)，但 `_write_view` 不写入任何 stage 文件。注释说 stage_01 在 parquet 中，但 parquet 在步骤间会被覆盖。 | 至少写入 `stage_01_raw.parquet` (含 t, noisy, ohlc) 确保每步数据可追溯 |
| **P1-11** | **EventRecorder 缺失 `filtered2`(副线滤波)** | 双滤波器模式的第二条滤波曲线 | 双滤波器模式 (`_dual=True`) 时副线结果完全不被记录。无法分析主副线的相互作用。 | 在 `filter_tail.jsonl` 中增加 `filtered2_tail` 字段 |
| **P1-12** | **EventRecorder 不记录 `schmitt["dur"]`** | 信号持续期数 | `dur` 反映信号的稳定性——长持续期的信号比频繁翻转的信号更可靠。对判断信号质量有价值。 | 在 `schmitt_snapshot.jsonl` 中增加 `dur_stats` 或 `dur_tail` |

### P2 — 可选（锦上添花）

| # | 缺口 | 缺失数据 | 影响 | 修复建议 |
|---|------|---------|------|---------|
| **P2-1** | **PipelineCapture 跳过 `prediction_pairs`** | 预测曲线数据 | 注释说明"derivable from pairs + filtered"。但重新推导需要重跑拟合算法，不如直接记录。 | 如果存储空间允许，写入 `stage_06_prediction.parquet` |
| **P2-2** | **PipelineCapture 跳过 `higher_pnl`** | 跨周期对齐详情 | 注释说明"duplicative"——可从高周期 stage_07 对齐得到。但需要额外的对齐逻辑。 | 可选写入 `stage_08_higher_pnl.parquet` 避免事后重算 |
| **P2-3** | **EventRecorder 不记录 `schmitt["mu_v"]`/`schmitt["sigma_v"]` 全量** | EWMA 统计量完整序列 | P1-1 建议记录 tail，但如果要完整验证 EWMA 计算，需要完整序列。受限于 JSONL 行大小(每行建议 < 1MB)，全量记录不现实。 | 保持 tail 方案；或记录统计摘要(min/max/mean/final) |
| **P2-4** | **无 session 级别的统计摘要** | 如: 总信号翻转次数、总交易数、最终 PnL、最大回撤 | `end_session()` 时仅记录 step_count 和 end_time，不产生任何策略统计摘要。事后分析需重放全部事件。 | 在 `end_session()` 中写入 `summary.json` 含全局统计 |

---

## 3. 用户需求覆盖检查

> 对照用户原始需求逐条检查

| # | 用户需求 | 当前覆盖状态 | 判定 |
|---|---------|-------------|------|
| R1 | "股票的不同周期收盘价" — 每步记录 | **不满足**。`noisy`(收盘价)仅在 `data/display/{tf}.parquet` 中(每步覆盖)，不在 EventRecorder 任何 JSONL 流中。PipelineCapture 的 `PipelineStageData` 有 `noisy` 槽位但 `_write_view` 不写。 | **GAP — P0-2** |
| R2 | "每个bar前进之后按时间顺序进行记录" — 完整时间线 | **大部分满足但不一致**。`filter_tail.jsonl` 有 `cutoff_date`；但 `schmitt_snapshot.jsonl` 和 `trade_summary.jsonl` 缺少 `cutoff_date`，仅有 `step` 索引。时间线需要跨文件关联。 | **GAP — P1-9** |
| R3 | "输出的BS状态" — BS变动前后状态是否完整 | **不满足**。仅记录增量差异(added/removed/modified/stable_count)，不记录完整 BS 快照。bs_modified 不含修改前的旧值。无法从事件流重建任意步骤的完整 BS 状态。 | **GAP — P1-7** |
| R4 | "BS在实际运行的过程中有变动有什么样的方式来记录" — Event Sourcing 覆盖所有变动类型 | **部分满足但缺失关键类型**。EventRecorder 覆盖了 BS marker 级别的 added/removed/modified/stable 四种变动。但缺失了 BS 变动**原因**的记录——即导致 BS 变动的上游信号变化(filter→sig→pair→trade→BS)没有被记录为事件。无法回答"为什么这个 BS marker 出现了？" | **GAP — P0-1, P1-2, P1-3, P1-6** |

---

## 4. 追溯链完整性验证

模拟场景: **新 bar 到达 → filter 变化 → eps 变化 → sig 翻转 → pair 变化 → trade 变化 → BS 变化**

### 因果链逐环节检查

```
Bar(t+1) → noisy[-1] = 新收盘价
    ↓
filtered[-1] 变化 (滤波输出更新)
    ↓
v[-1] = gradient(filtered) 变化
a[-1] = gradient(v) 变化
    ↓
eps[-1] = k_eps * max(sigma_v, sigma_min) 更新
    ↓
sig[-1] 翻转: a > eps AND v > 0 → sig=+1
    ↓
all_pairs 新增一项 (start, end) — 新多空对
    ↓
_compute_strategy_pnl → 新 trade_record (entry at pair_end, ...)
    ↓
compute_bs_markers → 增添绿 B entry_marker
```

| 环节 | EventRecorder 可追溯? | PipelineCapture 可追溯? | 结论 |
|------|----------------------|------------------------|------|
| Bar → `noisy[-1]` | **不可** — `noisy` 不记录 | **不可** — `noisy` 不写入文件(仅内存) | **断裂** |
| `noisy` → `filtered` | **部分** — `filter_tail(5)` | **可** — `stage_03_filter.parquet` 含完整 filtered | **EventRecorder 弱** |
| `filtered` → `v`, `a` | **不可** — `v`, `a` 不记录 | **可** — `stage_04_schmitt.parquet` | **EventRecorder 断裂** |
| `v` → `mu_v`, `sigma_v` | **不可** — mu_v, sigma_v 不记录 | **可** — stage_04 含 mu_v, sigma_v | **EventRecorder 断裂** |
| `mu_v`, `sigma_v` → `eps` | **BUG** — 代码尝试读但键路径错误 | **可** — stage_04 含 eps | **EventRecorder 断裂 (P0-1)** |
| `a`, `v`, `eps` → `sig` | **BUG** — 同上，sig 键路径错误 | **可** — stage_04 含 sig | **EventRecorder 断裂 (P0-1)** |
| `sig` → `all_pairs` | **部分** — 仅有 pair_count，无具体 pair | **可** — `stage_05_pairs.json` | **EventRecorder 弱** |
| `all_pairs` → `trade_records` | **部分** — 仅有 trade 摘要 | **可** — `stage_07_trades.json` 含明细 | **EventRecorder 弱** |
| `trade_records` → `bs_markers` | **部分** — 仅有增量事件 | **可** — `stage_10_bs_markers.json` | **EventRecorder 弱** |

**结论**: 在 **PipelineCapture 路径**(Streamlit 模式)下，9 个环节中 7 个完整可追溯(缺少 noisy 和 prediction_pairs)。在 **EventRecorder 路径**(CLI 模式)下，9 个环节中 **0 个完整可追溯**——每个环节都至少缺失关键数据。BS 变动因果链在 CLI 回测中**完全不可追踪**。

### 补充: 跨周期场景

当存在高周期 PnL 过滤时，还有额外环节:

```
higher_tf long_pnl/short_pnl → _align_pnl_to_current_tf
    → entry_markers / exit_markers (aligned)
        → _compute_holding_masks → long_mask / short_mask
            → compute_bs_markers (过滤: only trades with entry_idx in mask)
```

| 环节 | EventRecorder 可追溯? | PipelineCapture 可追溯? |
|------|----------------------|------------------------|
| higher PnL → aligned markers | **不可** | **不可**(刻意跳过) |
| aligned markers → masks | **不可** | **可** — stage_09_masks |
| masks → filtered BS | **不可** | **可** — 结合 stage_09 + stage_10 |

跨周期过滤的因果链在 EventRecorder 中**完全不可追踪**；在 PipelineCapture 中**基本可追踪**(仅 higher_pnl 对齐细节缺失)。

---

## 5. 推荐修复优先级

### 第一批: 阻断性修复 (P0)

1. **P0-1: 修复 EventRecorder `sig`/`eps` 键路径 BUG**
   - 文件: `filter_app/services/event_recorder.py` 第 321-322 行
   - 改动: `schmitt = view_data.get("schmitt", {}); sig = schmitt.get("sig"); eps = schmitt.get("eps")`
   - 预计: 2 行改动，零风险

2. **P0-4: BacktestRunner 返回 `v` 和 `a`**
   - 文件: `filter_app/services/backtest_core.py` `_compute_schmitt_trigger` (line 481-513) 和 `_compute_pipeline_for_view` (line 411-424)
   - 改动: 在 schmitt dict 中增加 `"v": v, "a": a` 字段
   - 影响: PipelineCapture 可删除 `_render_chart` 中的重复计算(lines 864-865); EventRecorder 获得 `v`/`a` 数据源

3. **P0-2: EventRecorder 增加 `noisy` 收盘价记录**
   - 新增 JSONL 流: `price_snapshot.jsonl`
   - 字段: step, view, cutoff_date, noisy_tail(5), noisy_latest
   - 或扩展 `filter_tail.jsonl` 增加 `noisy_tail` 字段

4. **P0-3: EventRecorder 扩大 `filtered` 记录范围**
   - 当前仅 tail(5)，建议至少 tail(20) + stats
   - 或新增 `filter_snapshot.jsonl` 记录完整数组

5. **P0-5: 统一 EventRecorder 和 PipelineCapture**
   - 方案 A (推荐): 在 `BacktestRunner.run()` 中同时调用两套记录器; Streamlit 模式也启用 EventRecorder
   - 方案 B: 将 EventRecorder 的 JSONL 能力合并进 PipelineCapture，统一为一套系统

### 第二批: 分析增强 (P1)

6. **P1-3: all_pairs 完整记录** — 扩展 `schmitt_snapshot.jsonl`
7. **P1-4: trade 明细记录** — 新增 `trade_detail.jsonl`
8. **P1-5: PnL 数组记录** — 新增 `pnl_snapshot.jsonl`
9. **P1-6: holding masks 记录** — 扩展 `schmitt_snapshot.jsonl`
10. **P1-7: BS 全量快照** — 每 N 步写一次 `bs_full_snapshot`
11. **P1-9: 统一 cutoff_date** — 在所有事件中增加字段
12. **P1-1, P1-2: mu_v, sigma_v, v, a 记录** — 扩展 `schmitt_snapshot.jsonl`
13. **P1-10: PipelineCapture 写入 noisy/ohlc** — 新增 stage_01_raw.parquet

### 第三批: 锦上添花 (P2)

14. **P2-1: prediction_pairs 写入**
15. **P2-3: EWMA 统计量全量/统计摘要**
16. **P2-4: session 级别策略统计摘要**

---

## 附录 A: 关键代码位置索引

| 文件 | 行号 | 内容 |
|------|------|------|
| `event_recorder.py` | 97-155 | `record_step()` — 入口，遍历 views |
| `event_recorder.py` | 290-348 | `_record_view_step()` — **BUG 所在: 321-322** |
| `event_recorder.py` | 202-284 | `_compare_bs_markers()` — BS 差异比较 |
| `backtest_core.py` | 357-424 | `_compute_pipeline_for_view()` — 返回值定义(权威来源) |
| `backtest_core.py` | 101-226 | `run()` — 主循环，组装每步输出 |
| `backtest_core.py` | 481-513 | `_compute_schmitt_trigger()` — 计算 v, a 但不返回 |
| `pipeline_capture.py` | 232-286 | `_write_view()` — 决定哪些 stage 写入 |
| `filter_engine.py` | 434-517 | `_schmitt_trigger()` — 返回值结构 |
| `filter_engine.py` | 649-851 | `_compute_strategy_pnl()` — trade_records 结构 |
| `filter_engine.py` | 858-978 | `_align_pnl_to_current_tf()` — 跨周期对齐 |
| `filter_engine.py` | 981-1026 | `_compute_holding_masks()` — 掩码计算 |
| `bs_marker.py` | 138-181 | `compute_bs_markers()` — BS 标记生成 |
| `streamlit_app.py` | 863-883 | PipelineCapture 数据填充 |
| `streamlit_app.py` | 2072-2095 | PipelineCapture 每步 flush |
| `backtest_cli.py` | 472-502 | EventRecorder CLI 使用 |

## 附录 B: BacktestRunner 返回值完整结构

```python
# BacktestRunner.run() 返回 list[dict]，每项:
{
    "step_index": int,        # bar 索引
    "cutoff_date": str,       # ISO 日期
    "views": {
        "v0_日线": {          # _compute_pipeline_for_view 的返回值
            "t": ndarray,            # P0-4 缺失 v, a
            "dates": DatetimeIndex,
            "noisy": ndarray,        # 收盘价 — P0-2 建议记录
            "ohlc": DataFrame,
            "filtered": ndarray,     # P0-3 建议扩大记录
            "filtered2": ndarray|None,
            "schmitt": {             # P0-1 BUG: EventRecorder 不访问此键
                "mu_v": ndarray,
                "sigma_v": ndarray,
                "eps": ndarray,
                "sig": ndarray,      # P0-1: EventRecorder 在顶层查找
                "dur": ndarray,
            },
            "all_pairs": list[tuple],  # P1-3 建议完整记录
            "prediction_pairs": list[dict],
            "long_pnl": ndarray,       # P1-5 建议记录
            "short_pnl": ndarray,      # P1-5 建议记录
            "trade_records": list[dict], # P1-4 建议完整记录
            # run() 中附加:
            "higher_pnl": dict|None,    # P1-8 建议记录
            "long_mask": ndarray|None,  # P1-6 建议记录
            "short_mask": ndarray|None, # P1-6 建议记录
            "bs_markers": dict,         # P1-7 建议全量快照
        },
        "v1_60分钟": {...},
        ...
    }
}
```
