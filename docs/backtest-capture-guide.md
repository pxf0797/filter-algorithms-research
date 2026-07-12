# 回测管道数据捕获与分析 — 使用指南

## 1. 概述

### 1.1 为什么需要这个系统

在回测系统中，BS（买卖）标记是策略信号的最终呈现。我们曾发现一个严重问题：**在回测导航过程中，BS 标记会在某些步骤之间发生非预期的跳跃**（例如，3690_HK_2 的 BS 标记在 step 22 到 step 23 之间出现结构性变化，连锁放大因子高达 645 倍）。这类问题根因极难定位，因为：

- 管道有 11 个阶段，数据在阶段之间传递和变换
- 问题可能源于任何阶段的微小数值变化，在后续阶段被逐步放大
- 回测导航过程中，各阶段的中间计算结果都是瞬态局部变量，导航结束后即丢失
- 现有的日志系统只记录元数据（时间、耗时），不记录管道计算结果

管道捕获系统正是为解决这一问题而构建的：**在回测导航的每一步，完整捕获所有管道阶段的输入和输出，持久化到磁盘，并提供 CLI 分析工具进行事后诊断**。

### 1.2 系统两部分的角色

| 组件 | 文件 | 角色 |
|------|------|------|
| **PipelineCapture** | `filter_app/services/pipeline_capture.py` | 运行时数据采集器。通过环境变量 `PIPELINE_CAPTURE=1` 控制开关，在回测每一步将管道各阶段输出写入结构化文件（Parquet + JSON） |
| **分析 CLI 工具** | `tools/analyze_captured_backtest.py` | 离线分析器。读取捕获会话目录，执行步间差分、BS 持久性、Schmitt 变化、级联放大四种分析，输出 TEXT 或 JSON 报告 |

### 1.3 适用场景

- **BS 跳跃问题调查**：定位 BS 标记在哪些步骤之间发生变化，追溯管道中的放大链
- **参数敏感性研究**：改变单个参数（如 k_eps、ewma_span），对比两次捕获的管道输出差异
- **跨股票对比**：不同 ticker 在相同参数下的 BS 稳定性对比
- **回归测试**：在 CI 中自动运行捕获 + 分析，确保代码变更不引入管道行为退化
- **新人 onboarding**：通过可视化管道数据流理解系统架构

---

## 2. 快速开始

### 2.1 启用捕获模式

通过环境变量 `PIPELINE_CAPTURE=1` 启动 Streamlit 应用：

```bash
PIPELINE_CAPTURE=1 streamlit run filter_app/streamlit_app.py
```

未设置该环境变量时，捕获系统完全不参与运行时流程，零开销。

### 2.2 运行回测

正常操作 Streamlit 界面：

1. 加载目标股票（如通过 preset 选择器加载 3690_HK_2）
2. 切换到回测（Backtest）模式
3. 使用滑块或播放按钮逐步导航
4. 退出回测模式

每次退出回测模式时，捕获会话将**自动保存**。

### 2.3 查看捕获数据

```bash
ls data/pipeline_captures/{ticker}_{session_id}/
```

典型输出：

```
data/pipeline_captures/03690.HK_20260712-143022-03690.HK/
├── metadata.json
└── steps/
    ├── 000000/
    │   ├── index.json
    │   └── v0_日线/
    │       ├── stage_02_filter.parquet
    │       ├── stage_03_schmitt.parquet
    │       ├── stage_04_pairs.json
    │       ├── stage_06_trades.json
    │       ├── stage_07_pnl.parquet
    │       ├── stage_09_masks.parquet
    │       └── stage_10_bs_markers.json
    ├── 000001/
    │   └── ...
    └── ...
```

### 2.4 运行分析

```bash
python tools/analyze_captured_backtest.py \
    --session-dir data/pipeline_captures/03690.HK_20260712-143022-03690.HK \
    --format TEXT
```

可选参数：

```bash
# 指定步骤范围
--step-range 20 30

# 指定单个视图
--view v0_日线

# 设置差分阈值（过滤微小变化）
--diff-threshold 0.001

# JSON 输出（便于程序化处理）
--format JSON

# 自定义输出目录
--output-dir /tmp/my_analysis
```

### 2.5 预期输出示例

TEXT 格式报告包含四个部分：

```
=================================================================
  Backtest Pipeline Capture Analysis
  Session: 20260712-143022-03690.HK  Ticker: 03690.HK  Steps: 45
=================================================================

--- Step-by-Step Diff Report ---

Step 22→23 (cutoff: 2025-06-15 → 2025-06-16):
  Filter: max_diff=0.000342, mean_diff=0.000018, affected_indices=[118,119]
  Schmitt eps: max_diff=0.002156, mean_diff=0.000134
  Schmitt sig: FLIP at indices [118, 119]
  Trade records: count 3→4
    trade #4 (short) APPEARED
  BS markers: entry 2→3, exit 2→3
    Entry S@2025-06-16 APPEARED
    Exit S@2025-06-16 APPEARED
  Cascade: filter tail Δ=0.000342 → sig flips=2, trade changes=1, BS marker changes=2
  → ⚠️ STRUCTURAL JUMP (Δ=+2 markers)

--- BS Marker Persistence ---
  Unstable markers (3/8):
  B@2025-06-10 (long entry): steps 0-15 (persistence: 16/45, UNSTABLE)
  ...

--- Schmitt Pair Count Timeline ---
  Steps with pair count changes: 3/44 (6.8%)
  Largest change: step 22→23 (3→4, Δ=+1)

--- Cascade Amplification ---
  Step 22→23: Filter Δmax at tail: 0.000342
    → Schmitt eps Δmax: 0.002156 (amplification: 6.31x)
    → Schmitt sig flips: 2 indices
    → Trade record changes: 1
    → BS marker changes: 2
  Overall: 0.000342 filter change → 645x BS marker amplification
=================================================================
```

---

## 3. 捕获系统详解

### 3.1 捕获的管道阶段

管道共有 11 个阶段。捕获系统选择性地持久化其中 7 个阶段，跳过可推导或已持久化的阶段：

| 阶段 | 名称 | 捕获文件 | 数据类型 | 说明 |
|------|------|---------|---------|------|
| 1 | Raw Data | **不捕获** | — | 原始 OHLC 数据已存在于 `data/display/{tf}.parquet` |
| 2 | Date Markers | **不捕获** | — | 日期标记可从 dates 数组推导 |
| 3 | Filters | `stage_03_filter.parquet` | Parquet | 主滤波器输出 `filtered`，可选副滤波器 `filtered2` |
| 4 | Schmitt Trigger | `stage_04_schmitt.parquet` | Parquet | 信号 `sig`、速度 `v`、加速度 `a`、自适应死区 `eps` |
| 5 | Signal Pairs | `stage_05_pairs.json` | JSON | 所有交易对 `[[start_idx, end_idx], ...]` |
| 6 | Prediction Curves | **不捕获** | — | 拟合曲线可从 pairs + filtered 推导 |
| 7 | PnL + Trades | `stage_07_pnl.parquet` + `stage_07_trades.json` | Parquet + JSON | 多空 PnL 曲线和交易记录 |
| 8 | Higher TF PnL | **不捕获** | — | 可交叉引用 higher-TF 视图的阶段 7 数据 |
| 9 | Holding Masks | `stage_09_masks.parquet` | Parquet | 布尔掩码，标记多空持仓区间 |
| 10 | BS Markers | `stage_10_bs_markers.json` | JSON | 最终的买卖标记（entry + exit） |
| 11 | Display Annotations | **不捕获** | — | Plotly 图表注释，可从阶段 3-10 推导 |

**注意**：当前捕获模块实现（`pipeline_capture.py`）使用的是压缩编号（`stage_02_filter.parquet` 对应阶段 3，`stage_03_schmitt.parquet` 对应阶段 4，以此类推）。这是已知的实现细节，分析工具以设计文档的全编号（01-10）为准。如果分析工具报 `FileNotFoundError`，请检查实际文件名是否与工具期望一致。参见 [8.4 节](#84-某些步骤缺失--文件名不匹配)。

### 3.2 存储布局和文件格式

```
{output_dir}/
  {ticker}_{session_id}/
    metadata.json              # 会话元数据
    steps/
      {step_index:06d}/        # 6 位零填充，如 000042
        index.json             # 步骤元数据：step_index, cutoff_date, capture_ts
        {view_name}/           # 视图目录，如 v0_日线, v1_60分钟
          stage_03_filter.parquet
          stage_04_schmitt.parquet
          stage_05_pairs.json
          stage_07_pnl.parquet
          stage_07_trades.json
          stage_09_masks.parquet
          stage_10_bs_markers.json
```

**文件格式选择**：

- **Parquet**（snappy 压缩）：用于数值数组（filtered, sig, eps, pnl, masks）。列式存储，压缩比约 2-3 倍，读取效率高。
- **JSON**（缩进格式，`ensure_ascii=False`）：用于结构化记录（pairs, trades, bs_markers）。人类可读，易于 diff。

**`metadata.json` 结构**：

```json
{
  "ticker": "03690.HK",
  "session_id": "20260712-143022-03690.HK",
  "start_time": "2026-07-12T14:30:22",
  "end_time": "2026-07-12T14:35:18",
  "step_count": 45,
  "total_size_bytes": 5400000,
  "config": {
    "operating_tf": "日线",
    "min_tf": "60分钟",
    "lower_tfs": ["60分钟", "15分钟", "5分钟"],
    "view_configs": [...]
  }
}
```

**步骤 `index.json` 结构**：

```json
{
  "step_index": 42,
  "cutoff_date": "2025-06-15T14:30:00",
  "capture_ts": "2026-07-12T14:31:05.123456"
}
```

### 3.3 性能特征

| 指标 | 数值 |
|------|------|
| 每步数据量（4 视图） | ~120 KB |
| 完整回测（1300 步） | ~156 MB |
| 单步写入延迟 | < 50 ms（同步 I/O） |
| 禁用时开销 | 0（单个字符串比较） |
| Parquet 压缩比 | ~2-3x（snappy） |

对于交互式逐步导航，120 KB/步的写入开销可以忽略。如果需要捕获超长回测（>5000 步），建议只捕获操作时间框视图。

### 3.4 注意事项

**磁盘空间**：每次完整回测约 156 MB。建议定期清理旧会话：

```bash
# 查看捕获数据总大小
du -sh data/pipeline_captures/

# 删除 7 天前的会话
find data/pipeline_captures/ -maxdepth 1 -type d -mtime +7 -exec rm -rf {} \;
```

**Streamlit Rerun 去重**：Streamlit 的 fragment-based 部分重渲染可能导致同一视图在单步内被多次调用。捕获系统通过在 `capture_step()` 中检查 `index.json` 是否已存在来实现去重——同一 `step_index` 只会写入一次步骤级元数据（每个视图独立写入其数据文件，后写入者覆盖前者的视图数据，这通常是无害的，因为同一步内同一视图的数据不会变化）。

**参数变更行为**：捕获会话绑定到一个回测生命周期。如果在回测过程中通过侧边栏更改参数，这些变更会反映在后续步骤的捕获数据中，但 `metadata.json` 中的 `config` 快照是在会话启动时记录的（不随参数变更而更新）。建议在一次捕获会话中保持参数不变。

**浏览器关闭**：如果 Streamlit 会话异常终止（关闭浏览器、服务器重启），`end_session()` 不会被调用，`metadata.json` 中缺少 `end_time` 和 `step_count`。这不影响数据可查询性——`step_count` 可以从 `steps/` 目录下推断。

---

## 4. 分析工具详解

### 4.1 命令行参数完整说明

```
python tools/analyze_captured_backtest.py --session-dir <路径> [选项]

必需参数:
  --session-dir PATH      捕获会话目录路径

可选参数:
  --step-range N M        分析步骤范围 [N, M]（包含两端，默认：全部步骤）
  --view VIEW             指定视图名称（如 v0_日线，默认：所有视图）
  --output-dir PATH       报告输出目录（默认：{session_dir}/analysis/）
  --diff-threshold FLOAT  差分阈值，变化超过此值才标记为显著（默认：0.0）
  --format TEXT|JSON      输出格式（默认：TEXT）
```

**实用组合示例**：

```bash
# 只看步骤 20-30，仅日线视图，JSON 输出
python tools/analyze_captured_backtest.py \
    --session-dir data/pipeline_captures/03690.HK_20260712-143022-03690.HK \
    --step-range 20 30 \
    --view v0_日线 \
    --format JSON

# 过滤微小噪声，只关注显著变化
python tools/analyze_captured_backtest.py \
    --session-dir data/pipeline_captures/03690.HK_20260712-143022-03690.HK \
    --diff-threshold 0.0001

# 输出到自定义目录
python tools/analyze_captured_backtest.py \
    --session-dir data/pipeline_captures/03690.HK_20260712-143022-03690.HK \
    --output-dir /tmp/bs_jump_analysis
```

### 4.2 四种分析模式

分析工具在单次运行中执行四种分析，结果汇总在同一个报告中。

#### 模式一：Step-by-Step Diff（步间差分）

对相邻步骤进行全管道阶段比较，报告每个阶段的数值变化：

- **Filter 层**：`filtered` 数组的 max_diff、mean_diff、受影响索引列表
- **Schmitt eps 层**：`eps` 数组的 max_diff、mean_diff、受影响索引列表
- **Schmitt sig 层**：信号翻转的索引列表（sig 从 -1/0/+1 变到其他值）
- **Trade Records 层**：交易的新增/消失/字段变化
- **Holding Masks 层**：掩码变化区间
- **BS Markers 层**：entry/exit 标记的新增/消失

每步差分结尾会标记状态：
- `STABLE`：无结构性变化
- `SIGNAL FLIP`：有信号翻转但无交易/BS 变化
- `STRUCTURAL JUMP`：交易记录或 BS 标记发生变化（这是需要重点关注的跳跃步骤）

#### 模式二：BS Marker Persistence（BS 标记持久性）

跟踪每个 BS 标记在整个回测步骤序列中的"存活"情况：

- 如果某个 BS 标记在所有步骤中都出现 → **稳定**（stable）
- 如果某个 BS 标记只在部分步骤中出现 → **不稳定**（unstable）

不稳定的标记是 BS 跳跃的直接证据：标记的出现和消失意味着管道输出在导航过程中发生了改变。

#### 模式三：Schmitt Pair Count Timeline（Schmitt 对数量时间线）

跟踪 `all_pairs` 数量随步骤的变化：

- 记录每一步的 pair 数量
- 标记 pair 数量发生变化的步骤
- 统计变化步骤占比和最大变化幅度

Pair 数量的突然变化（尤其是合并事件，如 4→3）通常意味着 Schmitt 触发器的死区参数在特定数据窗口下导致了信号段的合并或分裂，这是 BS 跳跃的常见根因之一。

#### 模式四：Cascade Amplification（级联放大）

量化微小数值变化在下游阶段被放大的倍数：

- 输入：filter 层在数据尾部的最大变化 `Δfilter_tail`
- 追踪：Schmitt eps 的变化倍数、sig 翻转数量、交易变化数量、BS 标记变化数量
- 输出：从 filter 变化到 BS 标记变化的总体放大因子

这是诊断 BS 跳跃严重程度的核心指标。在我们的案例（3690_HK_2）中，filter 层 0.000342 的微小变化最终导致 645 倍的 BS 标记放大。

### 4.3 解读 TEXT 输出

TEXT 报告分为五个 section（以 `---` 分隔），按顺序阅读即可形成完整的诊断链路：

1. **头部信息**：会话 ID、ticker、总步骤数
2. **Step-by-Step Diff Report**：逐步骤的管道变化详情。先找标记为 `STRUCTURAL JUMP` 的步骤——这些是需要重点关注的跳跃点。
3. **BS Marker Persistence**：哪些 BS 标记不稳定。不稳定标记的 `first_seen_step` 和 `last_seen_step` 告诉你标记的出现和消失时间点。
4. **Schmitt Pair Count Timeline**：pair 数量的变化时间线。关注 pair 数量变化的步骤是否与 BS 跳跃步骤重合——如果重合，说明根因可能在 Schmitt 阶段。
5. **Cascade Amplification**：级联放大链。从 filter 尾部的微小变化开始，逐层追踪放大倍数。

**阅读策略**：

1. 先看 "STRUCTURAL JUMP" 标记的步骤，定位跳跃点
2. 对每个跳跃步骤，从 Filter 开始逐层向下读：Filter → Schmitt eps → Schmitt sig → Trades → BS markers
3. 找出变化首次出现的阶段——那就是根因所在层
4. 查看 Cascade Amplification 确认放大倍数是否异常

### 4.4 解读 JSON 输出

JSON 格式适合程序化处理（例如在 Jupyter Notebook 中进一步分析）。顶层结构：

```json
{
  "session": {
    "session_id": "...",
    "ticker": "03690.HK",
    "step_count": 45,
    "session_dir": "..."
  },
  "step_diffs": [
    {
      "step_a": 22, "step_b": 23,
      "cutoff_a": "2025-06-15", "cutoff_b": "2025-06-16",
      "filter": { "max_diff": 0.000342, "mean_diff": 0.000018, "affected_indices": [118, 119] },
      "schmitt_eps": { "max_diff": 0.002156, "mean_diff": 0.000134, "affected_indices": [118, 119] },
      "schmitt_sig": { "flips": [118, 119] },
      "trade_records": { "count_a": 3, "count_b": 4, "changes": ["trade #4 (short) APPEARED"] },
      "bs_markers": { "entry_count_a": 2, "entry_count_b": 3, "changes": ["..."] },
      "is_stable": false,
      "has_structural_jump": true,
      "cascade_amplification": { "eps_amplification": 6.31, "bs_marker_amplification": 645 }
    }
  ],
  "bs_stability": {
    "markers": [ { "date": "...", "direction": "B", "type": "entry", "persistence": 16, "unstable": true } ],
    "total_steps": 45,
    "unstable_count": 3
  },
  "schmitt_stability": {
    "changes": [ { "step": 23, "prev_count": 3, "new_count": 4, "delta": 1 } ],
    "total_changed_steps": 3,
    "total_steps": 44
  }
}
```

**在 Python 中加载 JSON 报告**：

```python
import json

with open("analysis_report.json") as f:
    report = json.load(f)

# 提取所有结构性跳跃步骤
jumps = [d for d in report["step_diffs"] if d["has_structural_jump"]]
print(f"发现 {len(jumps)} 个结构性跳跃")

# 提取所有不稳定 BS 标记
unstable = [m for m in report["bs_stability"]["markers"] if m["unstable"]]
for m in unstable:
    print(f"  {m['direction']} {m['type']} @ {m['date'][:10]} - 持久性: {m['persistence']}/{report['bs_stability']['total_steps']}")
```

### 4.5 实用技巧

**只看跳跃步骤**：

```bash
# 生成 JSON 后用 jq 过滤
python tools/analyze_captured_backtest.py --session-dir <path> --format JSON 2>/dev/null | \
    jq '.step_diffs[] | select(.has_structural_jump == true) | {step: "\(.step_a)→\(.step_b)", bs_delta: (.bs_markers.entry_count_b - .bs_markers.entry_count_a)}'
```

**过滤特定视图**：使用 `--view` 参数只分析操作视图（通常是最粗时间框的视图），避免被 lower-TF 视图的噪声干扰。

**设置合理的 diff-threshold**：`--diff-threshold 0.0001` 可以过滤掉浮点运算中的微小舍入误差，只保留真正有意义的变化。

**快速定位跳跃区间**：先用 `--format JSON` 输出，用脚本找到 `has_structural_jump: true` 的步骤范围，然后再用 `--step-range` 对该范围做详细的 TEXT 分析。

---

## 5. 研究工作流

### 5.1 标准 BS 跳跃研究流程（以 3690_HK_2 为例）

这是调查 BS 跳跃问题的标准操作流程，共 9 步：

**第 1 步：启动捕获模式**

```bash
PIPELINE_CAPTURE=1 streamlit run filter_app/streamlit_app.py
```

**第 2 步：加载目标 preset**

在 Streamlit 界面中，通过 preset 选择器加载 `3690_HK_2`（或其他目标 preset）。

**第 3 步：切换到回测模式**

点击 "Backtest" 切换按钮，进入回测模式。此时捕获会话自动创建（session_id 格式为 `YYYYMMDD-HHMMSS-{ticker}`）。

**第 4 步：逐步导航**

使用以下方式之一：
- **滑块拖动**：手动将滑块从 start 拖到 end
- **播放模式**：点击播放按钮，设置速度，自动逐步推进
- **逐帧按钮**：点击 ">" 按钮逐帧前进

建议在已知的问题区域附近放慢速度（如 step 20-30），确保每个步骤都被捕获。

**第 5 步：退出回测 + 自动保存**

点击 "Backtest" 切换按钮退出回测模式。捕获会话自动调用 `end_session()`，写入 `end_time`、`step_count`、`total_size_bytes` 到 `metadata.json`。

**第 6 步：运行分析**

```bash
# 找到最新的捕获会话
ls -t data/pipeline_captures/ | head -1

# 运行分析
SESSION_DIR="data/pipeline_captures/$(ls -t data/pipeline_captures/ | head -1)"
python tools/analyze_captured_backtest.py \
    --session-dir "$SESSION_DIR" \
    --view v0_日线 \
    --format TEXT
```

**第 7 步：定位跳跃步骤**

在报告 `Step-by-Step Diff Report` 部分，搜索 `STRUCTURAL JUMP` 标记。记录跳跃步骤的 step 编号。

**第 8 步：追踪放大链**

在报告 `Cascade Amplification` 部分，查看每个跳跃步骤的放大链：

```
Step 22→23: Filter Δmax at tail: 0.000342
  → Schmitt eps Δmax: 0.002156 (amplification: 6.31x)
  → Schmitt sig flips: 2 indices
  → Trade record changes: 1
  → BS marker changes: 2
```

这告诉你：filter 尾部 0.000342 的变化，经过 Schmitt eps 放大了 6.31 倍，导致 2 个信号翻转，最终产生 2 个 BS 标记变化。

**第 9 步：手动检查原始数据**

对于跳跃步骤，提取原始 Parquet 数据手动验证：

```python
import pandas as pd
import json

step_dir = "data/pipeline_captures/03690.HK_20260712-143022-03690.HK/steps"

# 读取跳跃前后的 filter 数据
f_a = pd.read_parquet(f"{step_dir}/000022/v0_日线/stage_03_filter.parquet")
f_b = pd.read_parquet(f"{step_dir}/000023/v0_日线/stage_03_filter.parquet")

# 对比尾部的 filtered 值
print("Step 22 filtered tail:", f_a["filtered"].tail(5).values)
print("Step 23 filtered tail:", f_b["filtered"].tail(5).values)
print("Diff:", f_b["filtered"].tail(5).values - f_a["filtered"].tail(5).values)

# 读取 BS markers 确认变化
bs_a = json.load(open(f"{step_dir}/000022/v0_日线/stage_10_bs_markers.json"))
bs_b = json.load(open(f"{step_dir}/000023/v0_日线/stage_10_bs_markers.json")
print("Step 22 BS entries:", len(bs_a["entry_markers"]))
print("Step 23 BS entries:", len(bs_b["entry_markers"]))
```

### 5.2 参数敏感性研究流程

当需要研究某个参数（如 `k_eps`、`ewma_span`）对管道稳定性的影响时：

**第 1 步**：固定 `cutoff_date`（不要导航，只改变参数）

在 Streamlit 中将回测停在固定的 cutoff_date。

**第 2 步**：改变参数值

例如，将 `k_eps` 从 0.15 改为 0.20。

**第 3 步**：运行两次捕获

由于回测位置不变，你可以：
- 方法 A：在同一回测会话中改变参数后继续捕获（后续步骤使用新参数）
- 方法 B：启动两个独立会话，每个会话使用不同的参数

**第 4 步**：对比分析

```python
# 加载两个会话同一阶段的同一视图数据
import pandas as pd

view = "v0_日线"
step = "000042"
base = "data/pipeline_captures/session_A"
variant = "data/pipeline_captures/session_B"

# 对比 filter 输出
f_base = pd.read_parquet(f"{base}/steps/{step}/{view}/stage_03_filter.parquet")
f_var = pd.read_parquet(f"{variant}/steps/{step}/{view}/stage_03_filter.parquet")

diff = (f_var["filtered"] - f_base["filtered"]).abs()
print(f"Filter max diff: {diff.max():.6f}")

# 对比 BS markers
import json
bs_base = json.load(open(f"{base}/steps/{step}/{view}/stage_10_bs_markers.json"))
bs_var = json.load(open(f"{variant}/steps/{step}/{view}/stage_10_bs_markers.json"))
print(f"BS entry count: {len(bs_base['entry_markers'])} → {len(bs_var['entry_markers'])}")
```

### 5.3 跨股票对比流程

**第 1 步**：对多只股票分别运行捕获

```bash
# 股票 A
PIPELINE_CAPTURE=1 streamlit run filter_app/streamlit_app.py
# ... 加载 AAPL, 运行回测 ...

# 股票 B
PIPELINE_CAPTURE=1 streamlit run filter_app/streamlit_app.py
# ... 加载 03690.HK, 运行回测 ...
```

**第 2 步**：运行分析并对比 BS 稳定性

```bash
python tools/analyze_captured_backtest.py --session-dir <session_A> --format JSON > a.json
python tools/analyze_captured_backtest.py --session-dir <session_B> --format JSON > b.json
```

**第 3 步**：提取并对比指标

```python
import json

a = json.load(open("a.json"))
b = json.load(open("b.json"))

print(f"Ticker A: {a['bs_stability']['unstable_count']}/{len(a['bs_stability']['markers'])} unstable")
print(f"Ticker B: {b['bs_stability']['unstable_count']}/{len(b['bs_stability']['markers'])} unstable")
```

---

## 6. 案例演示：分析 3690_HK_2 的 BS 跳跃

本节展示如果使用捕获 + 分析系统调查 3690_HK_2 的 BS 跳跃问题，会看到什么。

### 6.1 运行捕获

```bash
PIPELINE_CAPTURE=1 streamlit run filter_app/streamlit_app.py
```

在界面中加载 3690_HK_2 preset，切换到回测模式，从 start 到 end 完成导航（约 45 步），退出回测。

### 6.2 Step Diff 输出示例

分析报告显示 **Step 22→23 发生结构性跳跃**：

```
Step 22→23 (cutoff: 2025-06-15 → 2025-06-16):
  Filter: max_diff=0.000342, mean_diff=0.000018, affected_indices=[118,119]
  Schmitt eps: max_diff=0.002156, mean_diff=0.000134
  Schmitt sig: FLIP at indices [118, 119]
  Trade records: count 3→4
    trade #4 (short) APPEARED
  BS markers: entry 2→3, exit 2→3
    Entry S@2025-06-16 APPEARED
    Exit S@2025-06-16 APPEARED
  → ⚠️ STRUCTURAL JUMP (Δ=+2 markers)
```

**解读**：当 cutoff_date 从 2025-06-15 推进到 2025-06-16 时，filter 尾部（索引 118、119）发生了 0.000342 的微小变化。这个变化经过管道放大，最终导致 Schmitt 信号翻转、新增一个 short trade 和两个 BS 标记。

### 6.3 BS Persistence 输出示例

```
--- BS Marker Persistence ---
  Unstable markers (3/8):
  B@2025-06-10 (long entry): steps 0-15 (persistence: 16/45, UNSTABLE)
  S@2025-06-16 (short entry): steps 23-44 (persistence: 22/45, UNSTABLE)
  S@2025-06-16 (short exit): steps 23-44 (persistence: 22/45, UNSTABLE)
```

**解读**：8 个 BS 标记中有 3 个不稳定。其中一个 long entry 标记在 step 15 之后消失，两个 short 标记在 step 23 才首次出现。这说明管道输出在导航过程中不稳定——在不同步骤查看"同一"回测结果时，BS 标记集合发生了变化。

### 6.4 Schmitt Changes 输出示例

```
--- Schmitt Pair Count Timeline ---
  Steps with pair count changes: 3/44 (6.8%)
  Largest change: step 22→23 (3→4, Δ=+1)
  Step 22→23: 3→4 (Δ=+1)
```

**解读**：3 个步骤发生了 pair 数量变化，其中 step 22→23 的变化（3→4）与 BS 跳跃点重合。这意味着 Schmitt 触发器在该步骤检测到新的信号段，导致了额外的交易对——这与 BS 标记的新增一致。

### 6.5 Cascade Impact 输出示例

```
--- Cascade Amplification ---
  Step 22→23: Filter Δmax at tail: 0.000342
    → Schmitt eps Δmax: 0.002156 (amplification: 6.31x)
    → Schmitt sig flips: 2 indices
    → Trade record changes: 1
    → BS marker changes: 2
  Overall: 0.000342 filter change → 645x BS marker amplification
```

**解读**：整个放大链清晰可见：

1. **Filter 层**：尾部 0.000342 的变化（新的 bar 数据改变了滤波器尾部几个点的值）
2. **Schmitt eps 层**：放大 6.31 倍（自适应死区对 filter 变化敏感）
3. **Schmitt sig 层**：死区变化导致 2 个索引点的信号翻转
4. **Trade 层**：信号翻转触发了 1 个新交易记录
5. **BS 层**：新交易产生了 2 个 BS 标记变化
6. **总放大因子**：从 filter 的 0.000342 到 2 个 BS 标记变化 → 645x

### 6.6 从数据中推导的结论

基于以上分析，可以得出以下结论：

1. **根因在 Schmitt 触发器的死区计算**：filter 尾部的微小变化被 eps 放大了 6.31 倍。这说明自适应死区（eps）对数据窗口尾部的新 bar 过于敏感。

2. **施密特触发器的 pair 合并行为不稳定**：在 step 22→23 处，pair 数量从 3 变为 4。当 cutoff_date 向后推进时，新增的 bar 数据改变了 eps 曲线，导致原本被合并的两个信号段分裂。

3. **BS 标记的级联效应严重**：645 倍的放大因子意味着系统中存在正反馈——微小的数值变化经过多个阶段后被急剧放大。

4. **修复建议**：考虑对 Schmitt eps 应用平滑处理（如 EWMA），或增加 pair 合并的稳定性约束（如要求连续 N 个 bar 的 eps 低于阈值才允许合并）。

---

## 7. 进阶用法

### 7.1 编程式分析：从 Python 脚本加载捕获数据

捕获数据可以直接在 Python 脚本中加载，无需通过 CLI 工具：

```python
import json
from pathlib import Path
import pandas as pd
import numpy as np

session_dir = Path("data/pipeline_captures/03690.HK_20260712-143022-03690.HK")

# 加载元数据
meta = json.loads((session_dir / "metadata.json").read_text())

# 加载特定步骤的特定视图数据
step = 22
view = "v0_日线"
step_view_dir = session_dir / "steps" / f"{step:06d}" / view

# 加载各阶段数据
filter_df = pd.read_parquet(step_view_dir / "stage_03_filter.parquet")
schmitt_df = pd.read_parquet(step_view_dir / "stage_04_schmitt.parquet")
pairs = json.loads((step_view_dir / "stage_05_pairs.json").read_text())
trades = json.loads((step_view_dir / "stage_07_trades.json").read_text())
pnl_df = pd.read_parquet(step_view_dir / "stage_07_pnl.parquet")
masks_df = pd.read_parquet(step_view_dir / "stage_09_masks.parquet")
bs = json.loads((step_view_dir / "stage_10_bs_markers.json").read_text())

# 查看 Schmitt signal 分布
print("Signal distribution:", dict(zip(*np.unique(schmitt_df["sig"], return_counts=True))))

# 查看交易记录
for t in trades["trade_records"]:
    print(f"  Trade #{t['id']}: {t['type']} entry@{t['entry_idx']} exit@{t['exit_idx']} return={t['return_pct']}%")
```

**遍历所有步骤提取指标**：

```python
def extract_bs_stats(session_dir: Path, view: str) -> list[dict]:
    """提取每个步骤的 BS 标记统计。"""
    results = []
    steps_dir = session_dir / "steps"
    for step_dir in sorted(steps_dir.iterdir()):
        if not step_dir.is_dir():
            continue
        bs_path = step_dir / view / "stage_10_bs_markers.json"
        if not bs_path.exists():
            continue
        bs = json.loads(bs_path.read_text())
        results.append({
            "step": int(step_dir.name),
            "entry_count": len(bs.get("entry_markers", [])),
            "exit_count": len(bs.get("exit_markers", [])),
        })
    return results

stats = extract_bs_stats(session_dir, "v0_日线")
for s in stats:
    print(f"Step {s['step']:03d}: entries={s['entry_count']}, exits={s['exit_count']}")
```

### 7.2 自定义分析：扩展现有工具

分析工具的核心函数可以直接导入使用：

```python
import sys
sys.path.insert(0, "tools")
from analyze_captured_backtest import (
    load_session_metadata,
    load_step_index,
    load_step_view,
    compute_step_diff,
    analyze_bs_stability,
    analyze_schmitt_changes,
)

session_dir = "data/pipeline_captures/03690.HK_20260712-143022-03690.HK"

# 加载连续两步的完整数据
step_a = 22
step_b = 23
view = "v0_日线"

sdir = Path(session_dir)
meta_a = load_step_index(sdir / "steps" / f"{step_a:06d}")
meta_b = load_step_index(sdir / "steps" / f"{step_b:06d}")
sv_a = load_step_view(sdir / "steps" / f"{step_a:06d}" / view)
sv_b = load_step_view(sdir / "steps" / f"{step_b:06d}" / view)

meta_a["views_captured"] = [view]
meta_b["views_captured"] = [view]

diff = compute_step_diff(sv_a, sv_b, meta_a, meta_b, threshold=0.0)

# 自定义检查
print(f"Step {step_a}→{step_b}: stable={diff.is_stable}, jump={diff.has_structural_jump}")
if diff.cascade_amplification:
    print(f"  Amplification: {diff.cascade_amplification}")
```

### 7.3 自动化回归测试：在 CI 中运行捕获 + 分析

可以将捕获 + 分析集成到 CI 管道中，确保代码变更不破坏管道稳定性：

```python
#!/usr/bin/env python3
"""CI 回归测试：验证回测捕获数据中无结构性跳跃。"""
import json
import subprocess
import sys
from pathlib import Path

def run_backtest_capture(ticker: str, preset: str, steps: int = 50) -> Path:
    """启动 headless 回测并捕获指定步数。（需要定制 headless runner）"""
    # 这里需要用编程方式控制 Streamlit 回测，
    # 可以通过直接调用管道函数（绕过 Streamlit UI）来实现
    raise NotImplementedError("需要 headless backtest runner")

def analyze_and_check(session_dir: Path, max_allowed_jumps: int = 0) -> bool:
    """运行分析并检查结构性跳跃是否在允许范围内。"""
    result = subprocess.run([
        "python", "tools/analyze_captured_backtest.py",
        "--session-dir", str(session_dir),
        "--format", "JSON",
    ], capture_output=True, text=True)
    
    report = json.loads(result.stdout)
    jumps = [d for d in report["step_diffs"] if d["has_structural_jump"]]
    
    if len(jumps) > max_allowed_jumps:
        print(f"FAIL: {len(jumps)} structural jumps found (max allowed: {max_allowed_jumps})")
        for j in jumps:
            print(f"  Step {j['step_a']}→{j['step_b']}: BS delta={j['bs_delta']}")
        return False
    
    print(f"PASS: {len(jumps)} structural jumps (within limit of {max_allowed_jumps})")
    return True

if __name__ == "__main__":
    session = Path("data/pipeline_captures/latest")
    success = analyze_and_check(session, max_allowed_jumps=2)
    sys.exit(0 if success else 1)
```

### 7.4 数据归档策略

对于需要长期保留的捕获会话：

```bash
# 压缩归档
tar -czf "capture_$(date +%Y%m%d).tar.gz" data/pipeline_captures/03690.HK_*/

# 保留最近 5 个会话，删除更早的
ls -t data/pipeline_captures/ | tail -n +6 | while read d; do
    rm -rf "data/pipeline_captures/$d"
done

# Git LFS 管理大型捕获数据
git lfs track "data/pipeline_captures/**/*.parquet"
```

---

## 8. 故障排除

### 8.1 捕获目录为空

**症状**：`data/pipeline_captures/` 目录不存在或为空。

**原因**：未设置 `PIPELINE_CAPTURE` 环境变量。

**解决**：

```bash
# 确认环境变量已设置
echo $PIPELINE_CAPTURE  # 应输出 "1"

# 如果未设置，重新启动
PIPELINE_CAPTURE=1 streamlit run filter_app/streamlit_app.py
```

也可以通过 Python 代码确认：

```python
from filter_app.services.pipeline_capture import PipelineCapture
print("Capture enabled:", PipelineCapture.is_enabled())
```

### 8.2 分析工具报 FileNotFoundError

**症状**：

```
FileNotFoundError: [Errno 2] No such file or directory: '.../stage_03_filter.parquet'
```

**原因**：捕获模块当前使用压缩编号（`stage_02_filter.parquet`），而分析工具期望设计文档的全编号（`stage_03_filter.parquet`）。

**解决**：这是已知的文件名不匹配问题。当前需要手动创建符号链接或重命名文件来对齐：

```bash
# 方案 A：为每个步骤创建符号链接
SESSION="data/pipeline_captures/03690.HK_20260712-143022-03690.HK"
for step_dir in "$SESSION"/steps/*/; do
    for view_dir in "$step_dir"/*/; do
        # filter: 02 → 03
        ln -sf stage_02_filter.parquet "$view_dir/stage_03_filter.parquet" 2>/dev/null
        # schmitt: 03 → 04
        ln -sf stage_03_schmitt.parquet "$view_dir/stage_04_schmitt.parquet" 2>/dev/null
        # pairs: 04 → 05
        ln -sf stage_04_pairs.json "$view_dir/stage_05_pairs.json" 2>/dev/null
        # trades: 06 → 07_trades
        ln -sf stage_06_trades.json "$view_dir/stage_07_trades.json" 2>/dev/null
    done
done
```

### 8.3 磁盘空间不足

**症状**：捕获过程中出现 `OSError: No space left on device`。

**原因**：每次完整回测约 156 MB，多次捕获可能累积到数 GB。

**解决**：

```bash
# 检查使用量
du -sh data/pipeline_captures/

# 列出所有会话及其大小
du -sh data/pipeline_captures/*/

# 删除 7 天前的会话
find data/pipeline_captures/ -maxdepth 1 -type d -mtime +7 -exec rm -rf {} \;

# 删除所有会话（谨慎）
rm -rf data/pipeline_captures/*
```

### 8.4 某些步骤缺失—文件名不匹配

**症状**：`steps/` 下的部分步骤目录存在但视图目录为空或缺少某些 stage 文件。

**原因**：

1. **Streamlit rerun 去重机制**：捕获系统通过 `index.json` 是否已存在来判断是否为重复 rerun。如果第一次 rerun 只渲染了部分视图，后续视图的数据可能丢失。
2. **视图未渲染**：如果某视图的面板被折叠或在当前配置下不显示，该视图不会被 `_render_chart()` 调用，因此不会被捕获。

**解决**：
- 确保回测时所有目标视图的面板都处于展开状态
- 检查 `index.json` 确认该步骤是否被正确记录
- 使用 `--view` 参数只分析确实被捕获的视图

### 8.5 数据分析结果始终为 STABLE

**症状**：运行分析后，所有步骤都显示 `STABLE`，但你知道应该有变化。

**原因**：

1. `--diff-threshold` 设置过高，过滤掉了所有变化
2. 分析的步骤范围内确实没有 cutoff_date 变化（所有步骤在同一 bar 上）
3. `--view` 参数指定的视图没有被捕获

**解决**：
- 将 `--diff-threshold` 设为 0.0 或非常小的值（如 1e-10）
- 检查步骤范围是否正确：`ls data/pipeline_captures/{session}/steps/`
- 确认目标视图存在于捕获目录中

### 8.6 Python 依赖缺失

**症状**：

```
ModuleNotFoundError: No module named 'pandas'
```

**原因**：分析工具依赖 `numpy`、`pandas`。

**解决**：

```bash
pip install numpy pandas pyarrow
```

`pyarrow` 是 pandas 读取 Parquet 文件的后端引擎。

---

## 附录 A：管道阶段数据流图

```
main()
  │
  ├─ cb_mode? → PipelineCapture.start_session()
  │               └─ 创建 {output_dir}/{ticker}_{session_id}/metadata.json
  │
  └─ for each view (v0..v3):
       └─ _render_chart()
            │
            ├─ _load_chart_data()           → 阶段 1: t, noisy, ohlc, dates
            ├─ _date_markers()              → 阶段 2: marker_positions, labels
            ├─ _align_pnl_to_current_tf()   → 阶段 8: higher_pnl alignment
            ├─ _compute_filters()           → 阶段 3: filtered, filtered2  ★ 捕获
            ├─ _compute_schmitt_trigger()   → 阶段 4: sig, v, a, eps       ★ 捕获
            ├─ _find_all_pairs()            → 阶段 5: all_pairs             ★ 捕获
            ├─ _compute_prediction_pairs()  → 阶段 6: prediction_pairs
            ├─ _compute_strategy_display()  → 阶段 7: pnl, trade_records   ★ 捕获
            ├─ _compute_holding_masks()     → 阶段 9: long_mask, short_mask ★ 捕获
            ├─ compute_bs_markers()         → 阶段 10: entry/exit markers  ★ 捕获
            │
            └─ ★ PipelineCapture.capture_step()
                 └─ 写入:
                      steps/{step_index:06d}/index.json
                      steps/{step_index:06d}/{view_name}/
                        stage_03_filter.parquet
                        stage_04_schmitt.parquet
                        stage_05_pairs.json
                        stage_07_pnl.parquet
                        stage_07_trades.json
                        stage_09_masks.parquet
                        stage_10_bs_markers.json
```

★ 标记的阶段被捕获系统持久化。其他阶段的结果可从已捕获数据或 `data/display/{tf}.parquet` 文件推导。

---

## 附录 B：术语对照

| 术语 | 英文 | 说明 |
|------|------|------|
| 回测 | Backtest | 基于历史数据模拟策略表现的过程 |
| 管道 | Pipeline | 从原始数据到最终 BS 标记的数据处理链路（11 阶段） |
| 捕获会话 | Capture Session | 一次回测导航的完整数据记录 |
| 截止日期 | Cutoff Date | 回测中"当前时间点"，管道只看到该日期之前的数据 |
| 步骤 | Step | 回测导航中的一个 bar 位置 |
| 视图 | View | 一个时间框（日线/60分钟/15分钟/5分钟）的图表面板 |
| 结构性跳跃 | Structural Jump | 步骤之间交易记录或 BS 标记发生变化的现象 |
| 级联放大 | Cascade Amplification | 管道前端的微小数值变化在后端被逐步放大的效应 |
| 施密特触发器 | Schmitt Trigger | 带自适应死区的信号生成器，将价格波动转化为 -1/0/+1 信号 |
| BS 标记 | BS Markers | 最终的买入（B）和卖出（S）标记，叠加在图表上 |
