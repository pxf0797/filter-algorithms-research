# 回测数据 CLI 存储方案设计

> 版本: v2.0 | 日期: 2026-07-16 | 作者: 量化系统架构组
> 基于 T1（核心引擎分析）、T2（数据输出机制分析）、T3（存储最佳实践）三份研究报告
>
> v2.0 重大变更：存储范围从"全量管道数据"收窄为"信号→交易决策链路"数据。
> K 线(OHLCV)、收盘价序列(noisy)、滤波数据(filtered/filtered2)、残差等不再存储。
> 详见第 1.4 节和用户明确指示。

---

## 1. 现状分析

### 1.1 现有架构

当前回测系统由三层构成：

```
┌──────────────────────────────────────────────┐
│                  Streamlit UI                 │
│  (streamlit_app.py)                           │
│  参数输入 → 触发回测 → 内存图表渲染            │
│  PIPELINE_CAPTURE=1 → PipelineCapture 写入磁盘 │
└──────────────────┬───────────────────────────┘
                   │
┌──────────────────▼───────────────────────────┐
│              BacktestRunner                    │
│  run() 按 _min_tf 步长逐 bar 循环              │
│  每步产出：dates, noisy, filtered, filtered2,  │
│  schmitt, prediction_pairs, long_pnl,          │
│  short_pnl, higher_pnl, masks, trade_records   │
└──────────────────┬───────────────────────────┘
                   │
┌──────────────────▼───────────────────────────┐
│              EventRecorder                     │
│  事件摘要器：仅存统计量（尾部3-5值）、事件计数   │
│  不存全量数组数据                              │
│  调用 CSVBuilder 写 CSV + 5 个 JSONL            │
└──────────────────┬───────────────────────────┘
                   │
                   ▼
        {ticker}_{session_id}/
        ├── data.csv          ← ffill() 后写入，存在前视偏差
        ├── bs_events.jsonl
        ├── schmitt_events.jsonl
        ├── signal_events.jsonl
        ├── trade_events.jsonl
        ├── pair_events.jsonl
        └── metadata.json
```

**关键实体职责：**

| 实体 | 职责 | 运行方式 |
|------|------|----------|
| `BacktestRunner.run()` | 逐 bar 执行 pipeline，产出全量数据 | Streamlit UI 触发 |
| `EventRecorder` | **事件摘要器**：从全量数据中提取摘要统计 | 回测过程中调用 |
| `CSVBuilder` | 按 bar_index 累积，end_session 时 ffill 后写 CSV | EventRecorder 驱动 |
| `PipelineCapture` | 全量数据捕获（Parquet + JSON），**仅 Streamlit 模式可用** | `PIPELINE_CAPTURE=1` 环境变量控制 |
| `backtest_cli.py` | CLI 入口，参数完整但**无数据持久化逻辑** | 命令行独立运行 |

### 1.2 数据流全景

```
BacktestRunner.run() 每步产出
  │
  ├── dates (DatetimeIndex)          ──→ EventRecorder: ❌ 不存
  ├── noisy (收盘价序列)              ──→ EventRecorder: ⚠️ 仅尾部3-5值
  ├── filtered (滤波序列)             ──→ EventRecorder: ⚠️ 仅尾部3-5值
  ├── filtered2 (副滤波器输出)        ──→ EventRecorder: ❌ 不存
  ├── schmitt["sig"] (信号序列)       ──→ EventRecorder: ⚠️ 仅尾部3-5值
  ├── schmitt["eps","sigma_v","v","a"]──→ EventRecorder: ⚠️ 仅尾部3-5值
  ├── prediction_pairs (预测抛物线)   ──→ EventRecorder: ❌ 不存
  ├── long_pnl / short_pnl (PnL曲线)  ──→ EventRecorder: ❌ 不存
  ├── higher_pnl (高周期对齐PnL)      ──→ EventRecorder: ❌ 不存
  ├── long_mask / short_mask (持仓掩码)──→ EventRecorder: ❌ 不存
  ├── all_pairs (pair起止索引)        ──→ EventRecorder: ⚠️ 仅存数量
  ├── trade_records (逐笔交易明细)    ──→ EventRecorder: ⚠️ 仅存统计
  │
  └── PipelineCapture (仅 Streamlit) ──→ ✅ 全量 Parquet+JSON
```

### 1.3 缺口总结

| 数据项 | 是否存储 | 存储形式 | 缺失影响 |
|--------|----------|----------|----------|
| `dates` (时间轴) | ❌ 不存 | — | 无法在离线分析中还原时间序列 |
| `noisy` (收盘价) | ⚠️ 部分 | 尾部 3-5 个值 | 无法回放完整价格历史 |
| `filtered` (滤波序列) | ⚠️ 部分 | 尾部 3-5 个值 | 无法验证滤波器效果 |
| `filtered2` (副滤波器) | ❌ 不存 | — | 副滤波器输出完全丢失 |
| `schmitt["sig"]` | ⚠️ 部分 | 尾部 3-5 个值 | 无法回溯信号变化全过程 |
| `schmitt["eps/sigma_v/v/a"]` | ⚠️ 部分 | 尾部 3-5 个值 | 无法分析施密特触发器中间状态 |
| `prediction_pairs` | ❌ 不存 | — | 预测抛物线数据完全丢失 |
| `long_pnl / short_pnl` | ❌ 不存 | — | 无法在外部还原 PnL 曲线图（~120 元素/视图） |
| `higher_pnl` | ❌ 不存 | — | 高周期对齐 PnL 丢失 |
| `long_mask / short_mask` | ❌ 不存 | — | 无法还原持仓状态时序 |
| `all_pairs` 详情 | ⚠️ 部分 | 仅存数量统计 | 不知道具体 pair 的起止 bar |
| `trade_records` 详情 | ⚠️ 部分 | 仅存统计 | 不知道每笔交易的 entry/exit bar/price/pnl |

**核心问题：EventRecorder 是摘要器，不是全量记录器。CLI 模式无 PipelineCapture 等价物。**

### 1.4 存储范围修正（v2.0 重大变更）

多周期回测场景下，不同周期的 K 线数据时间戳不一致，**只有最新时刻（cutoff_date）能对齐**。因此存储范围收窄为**信号→交易的决策链路数据**，不存储原始行情和滤波中间结果。

存储决策对照表（基于 `BacktestRunner._compute_pipeline_for_view` 产出）：

| 数据 | 来源 | 存储 | 文件 |
|------|------|------|------|
| `schmitt["sig"]` | 信号数组(+1/0/-1) | ✅ | signals.parquet |
| `schmitt["eps"]` | 自适应阈值 | ✅ | signals.parquet |
| `schmitt["sigma_v"]` | 速度波动率 | ✅ | signals.parquet |
| `schmitt["v"]` | 速度 | ✅ | signals.parquet |
| `schmitt["a"]` | 加速度 | ✅ | signals.parquet |
| `schmitt["mu_v"]` | EWMA 均值 | ✅ | signals.parquet |
| `schmitt["dur"]` | 信号持续期 | ✅ | signals.parquet |
| `all_pairs` | 多空信号对起止索引 `[(start, end), ...]` | ✅ | signals.parquet (JSON 列) |
| `prediction_pairs` | 预测抛物线 `[{fit_result, fit_start, pair_end}, ...]` | ✅ | signals.parquet (JSON 列) |
| `long_pnl` | 做多 PnL 曲线 | ✅ | signals.parquet |
| `short_pnl` | 做空 PnL 曲线 | ✅ | signals.parquet |
| `long_mask` | 做多持仓掩码 | ✅ | signals.parquet |
| `short_mask` | 做空持仓掩码 | ✅ | signals.parquet |
| `higher_pnl` | 高周期对齐 PnL（`{dates, long_pnl, short_pnl, trade_records}`） | ✅ | signals.parquet |
| `trade_records` | 逐笔交易明细 | ✅ | trades.parquet |
| BS markers (entry/exit) | 买卖标记 | ✅ | bs_markers.parquet |
| `t` | bar 索引数组（np.arange） | ❌ | —（可从 `bar_index` 列推导） |
| `dates` | DatetimeIndex 时间轴 | ❌ | —（可从 `bar_timestamp` 列还原） |
| `noisy` | 原始收盘价序列 | ❌ | —（不属于决策链路） |
| `ohlc` / `ohlcv` | K 线数据 | ❌ | —（不属于决策链路） |
| `filtered` | 滤波后价格 | ❌ | —（中间结果，不存） |
| `filtered2` | 副滤波器输出 | ❌ | —（中间结果，不存） |

**设计原则**：存储的是"回测系统做出的决策以及决策的结果"，而不是"做出决策所用的全部原始输入"。

---

## 2. 设计目标

1. **决策链路完整存储**：覆盖 schmitt 信号 → all_pairs → pred_pairs → PnL → trade_records → BS markers 全链路，不做降采样或摘要
2. **CLI 独立运行**：不依赖 Streamlit UI、不需要浏览器、不需要 `PIPELINE_CAPTURE` 环境变量
3. **离线可分析**：输出数据可直接用于绘制 Streamlit 同级图表、运行离线回测评估
4. **每周期独立**：由于多周期数据时间轴不同（日线与 60 分钟的 bar 不等价），每周期独立存储，不做 ffill 合并
5. **向后兼容**：保留现有 `EventRecorder` 输出（CSV + JSONL），新格式为增量补充
6. **可扩展**：支持多 ticker 批量运行、支持跨 session 聚合分析

---

## 3. 存储格式选型

### 3.1 格式对比

| 维度 | CSV | JSONL | Parquet (ZSTD) | HDF5 |
|------|-----|-------|----------------|------|
| 压缩率（相对 CSV） | 1x | 0.8x | **0.1x ~ 0.2x** | 0.3x ~ 0.5x |
| 写入速度 | 中 | 中 | 高 | 高 |
| 列裁剪读取 | 不支持 | 不支持 | **支持** | 支持 |
| 谓词下推过滤 | 不支持 | 不支持 | **支持** | 部分 |
| 嵌套结构支持 | 差 | 好 | **好** | 好 |
| 生态工具 | 通用 | 通用 | Pandas/DuckDB/Polars | 科学计算 |
| Schema 自描述 | 无 | 无 | **有（内置）** | 有 |
| 人类可读性 | **高（Excel）** | 中 | 低（需工具） | 低 |
| 分析代码首选 | 否 | 否 | **是** | 否 |

### 3.2 推荐方案

| 格式 | 用途 | 理由 |
|------|------|------|
| **Parquet (ZSTD compression_level=3)** | 主要分析格式：signals、trades、bs_markers | 列存、高压缩、谓词下推、Schema 自描述 |
| **CSV** | 同步输出的伴生副本，仅供人工快速查看（如 Excel 打开） | 不用工具即可直观浏览，但分析代码**只用 Parquet** |
| **JSONL** | 事件流（BS 变更事件） | 追加写入、人类可读、适合事件溯源 |
| **JSON** | metadata（运行参数、schema 版本） | 单文件、人类可读、键值结构 |

**重要约定**：CSV 是 Parquet 的"只读视图"。分析代码（Python/DuckDB/SQL）只读 Parquet。CSV 仅用于人在 Excel 中快速扫一眼数据。两者数值完全一致。

**不推荐 HDF5**：生态收缩（pandas 已标记 PyTables 为可选依赖），且缺乏谓词下推能力。

### 3.3 各格式职责分工

```
Parquet (分析主格式) ──── 结构化决策链路数据，可高效查询、聚合、切片
  ├── {timeframe}.parquet   每视图每 bar 的信号 → PnL 全链路（每周期独立文件）
  ├── trades.parquet         所有周期合并的逐笔交易明细
  └── bs_markers.parquet     所有周期合并的 BS 买卖标记

CSV (观察副本) ──── 与 Parquet 同目录，列和值一致
  └── {timeframe}.csv        与 {timeframe}.parquet 同步输出

JSONL ──── 事件流，按时间追加，人类可读
  └── events.jsonl           BS 变更事件流（保留现有格式）

JSON ──── 会话元数据
  └── metadata.json          运行参数 + schema 版本 + 统计摘要
```

---

## 4. 数据组织设计

### 4.1 目录结构

```
backtest_results/                          # 可配置根目录
├── {TICKER}/                              # 按 ticker 分组
│   └── {SESSION_ID}/                      # {date}_{time}_{preset}
│       ├── metadata.json                  # 运行元数据
│       ├── signals/                       # 信号→PnL 数据（每周期独立）
│       │   ├── 日线.parquet               # 日线视图数据
│       │   ├── 日线.csv                   # 同步 CSV（仅观察用）
│       │   ├── 60分钟.parquet             # 60分钟视图数据
│       │   ├── 60分钟.csv
│       │   ├── 15分钟.parquet
│       │   ├── 15分钟.csv
│       │   └── ...
│       ├── trades.parquet                 # 所有周期合并的交易明细
│       ├── bs_markers.parquet             # 所有周期合并的 BS 标记
│       └── events.jsonl                   # BS 变更事件流
```

**设计决策**：
- Ticker 在第一层：方便按标的分组，直接 `ls {TICKER}/` 查看该标的所有 session
- Session 命名含 preset：`{date}_{time}_{preset}`，肉眼可辨回测参数
- signals 子目录：每周期一个 Parquet + 同名 CSV，多周期不合并（时间轴不同）
- trades 和 bs_markers 合并存储：交易数远少于 bar 数，合并不造成性能问题，且方便跨视图对比
- CSV 与 Parquet 同目录同名：方便对照，`open {timeframe}.csv` 即可在 Excel 中查看

### 4.2 文件命名规范

| 文件 | 命名 | 说明 |
|------|------|------|
| 信号数据 | `signals/{timeframe}.parquet` | 周期名作文件名，如 `日线.parquet` |
| 信号 CSV | `signals/{timeframe}.csv` | 同目录同名，扩展名不同 |
| 交易明细 | `trades.parquet` | 固定名称，所有周期合并 |
| BS 标记 | `bs_markers.parquet` | 固定名称，所有周期合并 |
| 事件流 | `events.jsonl` | 固定名称 |
| 元数据 | `metadata.json` | 固定名称，内含 schema_version |

### 4.3 核心 Schema 设计

#### 4.3.1 signals/{timeframe}.parquet（每 bar 一行，每周期每 session 一个文件）

每行 = min_tf 上的一个 bar。每视图一组列，列名格式 `{view_prefix}_{field}`。

```
Schema Version: 1
压缩：ZSTD level=3
排序键：bar_index

┌──────────────────────┬──────────┬──────────────────────────────────────────┐
│ 列名                  │ 类型      │ 说明                                     │
├──────────────────────┼──────────┼──────────────────────────────────────────┤
│ 固定列（每文件一组）   │          │                                          │
│ bar_index            │ int32    │ min_tf 上的 bar 索引                       │
│ bar_timestamp        │ str      │ ISO 时间戳 (来自 dates[bar_index])         │
│ cutoff_date          │ str      │ 回测截止日期（本步的 cutoff_date）           │
├──────────────────────┼──────────┼──────────────────────────────────────────┤
│ {view}_sig           │ int8     │ 信号值 (-1=空, 0=观望, 1=多)               │
│ {view}_eps           │ float32  │ 自适应阈值                                 │
│ {view}_sigma_v       │ float32  │ 速度波动率 (EWMA std)                       │
│ {view}_v             │ float32  │ 速度 (一阶导数)                             │
│ {view}_a             │ float32  │ 加速度 (二阶导数)                           │
│ {view}_mu_v          │ float32  │ EWMA 速度均值                              │
│ {view}_sig_dur       │ int16    │ 信号持续期（自上次状态切换以来的 bar 数）    │
│ {view}_pair_count    │ int16    │ 当前信号对数量 (len(all_pairs))              │
│ {view}_all_pairs     │ str      │ all_pairs JSON 序列化 `[[s1,e1],[s2,e2],...]`│
│ {view}_pred_pairs    │ str      │ pred_pairs JSON 序列化（含拟合系数）         │
│ {view}_pnl_long      │ float32  │ 做多 PnL 累积值                             │
│ {view}_pnl_short     │ float32  │ 做空 PnL 累积值                             │
│ {view}_long_mask     │ bool     │ 做多持仓掩码 (True=持仓中)                   │
│ {view}_short_mask    │ bool     │ 做空持仓掩码 (True=持仓中)                   │
│ {view}_higher_pnl_long │ float32│ 高周期对齐做多 PnL (NaN=无高周期或无对齐值) │
│ {view}_higher_pnl_short│ float32│ 高周期对齐做空 PnL                          │
└──────────────────────┴──────────┴──────────────────────────────────────────┘
```

**view 命名示例**：`v0_日线`、`v1_60分钟`、`v2_15分钟`（即 `v{view_index}_{tf}`）。

**数据来源**（对照 `BacktestRunner._compute_pipeline_for_view` 产出）：

| 列 | 来源 |
|----|------|
| `{view}_sig, _eps, _sigma_v, _v, _a, _mu_v, _sig_dur` | `schmitt` dict |
| `{view}_pair_count, _all_pairs` | `all_pairs` list |
| `{view}_pred_pairs` | `prediction_pairs` list |
| `{view}_pnl_long, _pnl_short` | `long_pnl`, `short_pnl` arrays |
| `{view}_long_mask, _short_mask` | `long_mask`, `short_mask` arrays |
| `{view}_higher_pnl_long, _higher_pnl_short` | 通过 `_align_pnl_to_current_tf` 对齐计算：源数据为 `higher_pnl["long_pnl"]` / `higher_pnl["short_pnl"]`（高周期 PnL，bar 数与当前 TF 不同），需对齐到当前 TF 的 bar 数后存储 |

**设计要点**：
- `all_pairs` 和 `pred_pairs` 用 JSON 字符串存储：它们是变长列表，不适合展开为固定列。JSON 列可由 `pd.read_parquet` 读取后用 `json.loads` 解析。
- `pair_count` 冗余存储：避免解析 JSON 才能知道有几个 pair，加速过滤查询（如 `WHERE v0_日线_pair_count > 0`）。
- `higher_pnl_*` 可为 NaN：顶周期视图无高周期数据时为 NaN；低周期视图中，高周期数据在当前 bar 无对齐值时也为 NaN。
- 同一 TF 可能有多个视图（如日线 SMA 和日线 EMA）：各自有独立的列组，列前缀不同。
- 各周期信号截止点不同，不做 ffill 合并：各自保留本周期视角下的真实数据。

#### 4.3.2 trades.parquet（每笔交易一行，所有视图合并）

数据来源：`_compute_strategy_pnl` 返回的 `trade_records` 列表，每条记录包含 id/type/entry_idx/exit_idx/entry_price/exit_price/return_pct/exit_reason。

```
Schema Version: 1
压缩：ZSTD level=3
排序键：(view, entry_bar)

┌──────────────────┬──────────┬──────────────────────────────────────────┐
│ 列名              │ 类型      │ 说明                                     │
├──────────────────┼──────────┼──────────────────────────────────────────┤
│ trade_id          │ int32    │ 交易序号（全局递增，含 view 前缀信息）     │
│ view              │ str      │ 所属视图，如 "v0_日线"                     │
│ type              │ str      │ "long" / "short"                          │
│ entry_bar         │ int32    │ 入场 bar 索引（对应 min_tf）               │
│ exit_bar          │ int32    │ 离场 bar 索引（对应 min_tf）               │
│ entry_price       │ float32  │ 入场价格                                  │
│ exit_price        │ float32  │ 出场价格                                  │
│ return_pct        │ float32  │ 交易收益率（百分比，如 2.5 表示 2.5%）     │
│ exit_reason       │ str      │ "stop_loss" / "take_profit" / "eod"       │
└──────────────────┴──────────┴──────────────────────────────────────────┘
```

**trade_id 生成规则**：`{view_index} * 100000 + 原始_trade_id`，保证全局唯一。

**设计要点**：
- `exit_reason = "eod"` 表示持仓到数据末尾被迫平仓（非正常止盈/止损），分析时可区分完整交易和被截断交易。
- `view` 列区分不同视图的交易，支持 `WHERE view = 'v0_日线'` 过滤。
- 交易日线/60分钟等不同周期的交易 bar 索引均相对于 min_tf，可直接对比。

#### 4.3.3 bs_markers.parquet（每个 BS 标记一行，所有视图合并）

数据来源：`compute_bs_markers` 返回的 `entry_markers` 和 `exit_markers` 列表。

entry_marker 格式：`(bar_idx, "B"/"S", "green"/"red", date)`
exit_marker 格式：`(bar_idx, "B"/"S", "green"/"red", exit_reason, date)`

```
Schema Version: 1
压缩：ZSTD level=3
排序键：(view, bar_idx)

┌──────────────────┬──────────┬──────────────────────────────────────────┐
│ 列名              │ 类型      │ 说明                                     │
├──────────────────┼──────────┼──────────────────────────────────────────┤
│ view              │ str      │ 所属视图，如 "v0_日线"                     │
│ type              │ str      │ "entry" / "exit"                          │
│ label             │ str      │ "B" / "S"                                 │
│ color             │ str      │ "green" / "red"                           │
│ bar_idx           │ int32    │ 标记所在 bar 索引                          │
│ date              │ str      │ 标记日期（ISO 字符串）                     │
│ exit_reason       │ str      │ 离场原因，仅 exit_marker 有值；entry 为 null│
└──────────────────┴──────────┴──────────────────────────────────────────┘
```

**颜色语义（跟随持仓方向）**：
- 绿 B = 做多入场，绿 S = 平多出场
- 红 S = 做空入场，红 B = 平空出场

#### 4.3.4 events.jsonl（BS 变更事件流）

保留现有格式，每行一个 BS 变更事件。不做合并（已有 event_type 区分）。

```
每行 JSON 结构：
{
  "event_type": "bs_change",
  "view_index": 0,
  "bar_index": 1243,
  "timestamp": "2026-07-16T14:35:00",
  "data": {
    "old_label": 0,
    "new_label": 1,
    "count": 3
  }
}
```

#### 4.3.5 metadata.json（运行元数据）

```json
{
  "schema_version": "2.0.0",
  "signals_schema_version": 1,
  "trades_schema_version": 1,
  "bs_markers_schema_version": 1,
  "session": {
    "session_id": "20260716_143052_d1",
    "ticker": "AAPL",
    "preset": "d1",
    "config_file": "/path/to/config.yaml",
    "created_at": "2026-07-16T14:30:52"
  },
  "parameters": {
    "start_bar": 0,
    "end_bar": 5000,
    "min_tf": "5分钟",
    "step_interval": 1,
    "view_filter": null
  },
  "views": {
    "v0_日线": {
      "tf": "日线",
      "n_pts": 120,
      "filter_id": "sma",
      "stop_loss_pct": 2.0
    }
  },
  "statistics": {
    "total_bars": 5000,
    "total_views": 8,
    "total_trades": 142,
    "by_view": {
      "v0_日线": {"long_trades": 15, "short_trades": 12, "win_rate": 0.58}
    }
  },
  "files": {
    "signals/日线.parquet": {"rows": 5000, "size_mb": 1.2},
    "signals/60分钟.parquet": {"rows": 12000, "size_mb": 2.8},
    "trades.parquet": {"rows": 142, "size_mb": 0.05},
    "bs_markers.parquet": {"rows": 284, "size_mb": 0.02},
    "events.jsonl": {"size_mb": 1.2}
  }
}
```

---

## 5. CLI 增强方案

### 5.1 新增参数

在现有 `backtest_cli.py` 参数基础上增加以下参数：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--save-data` | flag | False | 启用决策链路数据持久化 |
| `--save-csv` | flag | True | 是否同步输出 CSV 观察副本（`--save-data` 开启时生效） |
| `--save-events` | flag | True | 是否保存事件流 JSONL |
| `--output-root` | path | `./backtest_results` | 数据输出根目录 |
| `--no-metadata` | flag | False | 跳过 metadata.json 写入 |

**现有参数保持不变**，向后完全兼容：

| 现有参数 | 说明 |
|----------|------|
| `--ticker` | 股票代码（必填） |
| `--preset` | 周期预设（如 d1, h4） |
| `--config-file` | 配置文件路径 |
| `--start-bar` / `--end-bar` | bar 范围 |
| `--output-dir` | 旧格式输出目录（保留兼容） |
| `--step-interval` | 步长间隔 |
| `--view-filter` | 视图过滤器 |
| `--quiet` | 静默模式 |

### 5.2 输出模式

| 模式 | 命令 | 输出内容 |
|------|------|----------|
| **仅回测（默认）** | `python backtest_cli.py --ticker AAPL --preset d1` | 终端输出，无文件 |
| **完整 Parquet** | 添加 `--save-data` | signals/*.parquet + trades.parquet + bs_markers.parquet + events.jsonl + metadata.json |
| **Parquet + CSV** | `--save-data`（`--save-csv` 默认开） | 上述 + signals/*.csv |
| **不含事件流** | `--save-data --no-events` | 仅 parquet 文件（不含 events.jsonl） |

### 5.3 使用示例

```bash
# 单标的回测 + Parquet + CSV
python backtest_cli.py \
  --ticker AAPL \
  --preset d1 \
  --start-bar 0 \
  --end-bar 5000 \
  --save-data \
  --output-root ./backtest_results

# 输出：
# backtest_results/AAPL/20260716_143052_d1/
#   ├── metadata.json
#   ├── signals/
#   │   ├── 日线.parquet       (1.2 MB, 5000 rows)
#   │   ├── 日线.csv           (1.8 MB, 同步副本)
#   │   ├── 60分钟.parquet     (2.8 MB, 12000 rows)
#   │   ├── 60分钟.csv
#   │   └── ...
#   ├── trades.parquet          (50 KB, 142 rows)
#   ├── bs_markers.parquet      (20 KB, 284 rows)
#   └── events.jsonl            (1.2 MB)

# 批量多标的（shell 循环）
for ticker in AAPL TSLA MSFT NVDA; do
  python backtest_cli.py \
    --ticker $ticker \
    --preset d1 \
    --save-data \
    --output-root ./batch_results &
done
wait

# 读取分析（Python + Pandas）
import pandas as pd

# 读取某周期的信号数据
df = pd.read_parquet('backtest_results/AAPL/20260716_143052_d1/signals/日线.parquet')

# 筛选有信号的 bar
active = df[df['v0_日线_sig'] != 0]

# 读取交易明细
trades = pd.read_parquet('backtest_results/AAPL/20260716_143052_d1/trades.parquet')

# DuckDB 跨 session 聚合
# SELECT view, COUNT(*) as trade_count, AVG(return_pct) as avg_return
# FROM read_parquet('backtest_results/*/*/trades.parquet')
# GROUP BY view
```

---

## 6. 实施路径

### Phase 1：补全决策数据（最小改动）

**目标**：在 BacktestRunner 中提取信号→交易决策链路数据，CLI 模式可写出完整 JSON。

**改动点**：
1. `BacktestRunner.run()` 新增 `collect_decisions` 参数，为 True 时从每步 pipeline 输出中提取决策链路数据到结构化 dict（排除 noisy/filtered/filtered2/ohlcv/dates）
2. 新增 `DecisionStore` 类，接收决策 dict 并累积到内存 buffer
3. CLI 模式 `--save-data` 时调用 `DecisionStore.collect()`，回测结束时序列化为 JSON

**输出**：
```
{ticker}_{session_id}/
  ├── decisions_full.json     # 临时 JSON（待 Phase 2 迁移到 Parquet）
  └── metadata.json
```

**验证标准**：
- 对比同一 ticker/preset 的 `PIPELINE_CAPTURE=1` 输出，决策链路字段（schmitt/PnL/trades）数值完全一致
- `decisions_full.json` 不包含任何 noisy/filtered/filtered2/ohlcv 数据
- 每个视图的数据独立分组，可按 view 键逐个校验

**注意**：JSON 是过渡格式。100 只股票 x 10 年 x 8 周期 = 约 80GB JSON（未压缩），仅供验证，不用于生产。

---

### Phase 2：优化格式（Parquet 迁移）

**目标**：将决策数据从 JSON 迁移到 Parquet，按每周期独立文件，附带 CSV 观察副本。

**改动点**：
1. 新增 `ParquetStore` 类，封装 pyarrow 写入逻辑
2. `--save-data` 默认输出格式改为 Parquet
3. 按 TF 独立写入 `signals/{timeframe}.parquet`，列名含 view 前缀
4. `trades.parquet` 和 `bs_markers.parquet` 所有视图合并写入
5. `events.jsonl` 追加写入
6. `--save-csv` 控制是否同步输出 `signals/{timeframe}.csv`
7. 删除 Phase 1 的 JSON 输出逻辑

**输出**：
```
{ticker}_{session_id}/
  ├── signals/
  │   ├── 日线.parquet
  │   ├── 日线.csv
  │   ├── 60分钟.parquet
  │   ├── 60分钟.csv
  │   └── ...
  ├── trades.parquet
  ├── bs_markers.parquet
  ├── events.jsonl
  └── metadata.json
```

**验证标准**：
- 所有 `.parquet` 可被 `pandas.read_parquet()` 正常读取
- CSV 与 Parquet 数值完全一致（round-trip 验证：`pd.read_csv().equals(pd.read_parquet())`）
- 压缩后文件大小 < 同数据 CSV 的 20%
- 按 `{view}_sig != 0` 过滤查询 < 100ms（DuckDB，SSD 环境）
- 数据与 Phase 1 JSON 输出完全一致

---

### Phase 3：高级特性

**3a. Schema 版本化**

- metadata.json 中写入 `schema_version` 及各子文件 schema 版本字段
- `ParquetStore` 写入时在 Parquet 文件的 key-value metadata 中嵌入版本号
- 读取时检查版本，不匹配时输出警告（不阻断，因为旧版本通常仍可读）
- Schema 变更规则：
  - 新增列（信号扩展）：minor 版本号 +1，向后兼容
  - 删除/重命名列：major 版本号 +1，读取器需适配

**3b. 增量回测模式**

- `--incremental` 参数：追加写入同一 session 的数据文件
- 使用 Parquet 的 append 模式，校验 schema 一致性
- 适用场景：中断恢复、分批回测

**3c. DuckDB 跨 Session 分析视图**

- 提供 `CREATE VIEW` SQL 脚本，将分散的 `.parquet` 文件聚合为逻辑视图
- 示例：
  ```sql
  CREATE VIEW all_signals AS
  SELECT *, filename as tf
  FROM read_parquet('backtest_results/*/*/signals/*.parquet',
                    union_by_name=true, filename=true);

  CREATE VIEW all_trades AS
  SELECT *
  FROM read_parquet('backtest_results/*/*/trades.parquet',
                    union_by_name=true);
  ```

**验证标准**：
- Schema 版本不匹配时读取器发出明确警告
- 增量写入 3 次后数据完整性校验通过
- DuckDB 跨 session 聚合查询返回正确结果

---

## 7. 风险与注意事项

### 7.1 存储范围边界

**已明确不存的数据**：noisy（收盘价）、filtered（滤波值）、filtered2（副滤波）、ohlcv（K线）、dates（DatetimeIndex 数组）。这些数据属于"输入侧"和"中间结果"，不属于"决策链路"。

**需要时可以还原的**：
- `bar_index` 可替代 `t`（np.arange）
- `bar_timestamp` 可还原时间轴
- K 线数据可以从 DB 重新查询（给定 ticker + 时间范围）

### 7.2 多周期间对齐问题

**问题**：多周期回测中，不同周期的 K 线时间戳不一致。日线的 bar 与 60 分钟的 bar 不是 1:1 对应关系。

**解决方案**：
- 每周期独立一个 Parquet 文件（`signals/{timeframe}.parquet`），不做 ffill 合并
- 每个文件中的 `bar_index` 是 min_tf 上的索引，不同周期的同一 bar_index 对应同一时间点
- 分析时如需跨周期对比，通过 `bar_index` + `bar_timestamp` JOIN

### 7.3 all_pairs 和 pred_pairs 的 JSON 序列化

**问题**：`all_pairs` 是 `[(int, int), ...]` 列表，`pred_pairs` 是嵌套 dict 列表，不适合展开为固定列。

**解决方案**：
- 用 `json.dumps()` 序列化为 JSON 字符串存入单列
- 读取后用 `json.loads()` 还原
- 冗余存储 `pair_count` 列，避免解析 JSON 才能过滤

**风险**：JSON 列不支持谓词下推，按 pair 内容过滤需全表扫描。**缓解**：pair 粒度查询通过 `pair_count` 初筛 + Python 侧解析过滤。

### 7.4 Schema 版本化策略

| 变更类型 | 版本号变化 | 兼容性 | 示例 |
|----------|-----------|--------|------|
| 新增信号列 | minor +1 | 向后兼容 | 新增 `{view}_sigma_a` 列 |
| 列重命名 | major +1 | 不兼容 | `{view}_sig_dur` → `{view}_duration` |
| 列删除 | major +1 | 不兼容 | 移除视图列组 |
| 类型变更 | major +1 | 不兼容 | int16 → int32 |
| 新增 event_type | minor +1 | 向后兼容 | events.jsonl 新增类型 |

**存储位置**：
- Parquet: 文件级 key-value metadata (`b"schema_version": b"1"`)
- metadata.json: 顶级字段 `"schema_version": "2.0.0"`
- events.jsonl: 首行写入 schema 声明事件

### 7.5 向后兼容

| 现有行为 | 兼容策略 |
|----------|----------|
| `--output-dir` 参数 | 保留，指向旧 CSV/JSONL 输出目录。`--save-data` 时新旧同时输出 |
| `EventRecorder` CSV + 5 JSONL | 不变。新输出为额外增量，不影响现有文件 |
| Streamlit `PIPELINE_CAPTURE` | 不变。CLI Parquet 输出和 Streamlit PipelineCapture 并存 |
| 无 `--save-data` 时 | 行为与当前 CLI 完全一致（仅终端输出） |

**过渡期建议**：
- Phase 1-2 期间：新旧格式并行输出，下游可逐步迁移
- Phase 3 之后：标记旧 CSV 输出为 deprecated，2 个大版本后移除

### 7.6 性能考量

| 场景 | 预估 | 缓解措施 |
|------|------|----------|
| 100 标的批量回测 | 5-8 GB Parquet | 按 ticker 分目录，可并行写入 |
| signals.parquet 写入 | ~20MB/s（HDD）| 每视图列组独立，按 row group 批量 flush |
| DuckDB 跨 session 查询 | < 1s（5GB 扫描）| Parquet 谓词下推 + 列裁剪，仅读所需列 |
| CSV 同步输出 | ~30MB/s | 仅追加写入，无查询需求 |
| JSONL 事件追加 | ~5MB/s | 缓冲写入（每 1000 事件 flush 一次） |
| 内存占用 | ~50MB（单 session buffer）| 按 view 逐视图处理后释放 |

**数据量预估（100 标的 x 10 年 x 8 周期，仅决策链路数据）**：

| 文件 | 单 Session | x100 标的 | 压缩后 |
|------|-----------|-----------|--------|
| signals/*.parquet (8 周期) | ~25 MB（CSV）→ ~5 MB | ~500 MB | Parquet ZSTD |
| trades.parquet | ~0.5 MB | ~50 MB | — |
| bs_markers.parquet | ~0.2 MB | ~20 MB | — |
| events.jsonl | ~10 MB | ~1 GB | — |
| **合计** | ~35 MB（CSV）→ ~15 MB | **~5-8 GB** | — |

**单 session** Parquet 输出约 15MB（对比 v1 方案全量 25MB 和 CSV 130MB）。
**批量运行** 100 标的约 5-8GB，普通 SSD 完全可承受，无需引入分布式存储。

与 v1 方案对比：v1 存储了 OHLCV、filtered 等原始数据，单 session 约 25MB Parquet。v2 收窄到决策链路后，存储量减少约 40%（15MB vs 25MB），且数据更聚焦于分析目标。

---

## 附录 A：与现有架构的关系

```
                          CLI 模式                        Streamlit 模式
                             │                                  │
              ┌──────────────┼──────────────┐    ┌──────────────┼──────────────┐
              │    --save-data               │    │  PIPELINE_CAPTURE=1         │
              ▼                              │    ▼                            │
         DecisionStore + ParquetStore         │  PipelineCapture                │
         (新增)                               │  (已有)                         │
              │                              │    │                            │
              ▼                              │    ▼                            │
   signals/*.parquet + trades.parquet         │  pipeline_*.parquet             │
   + bs_markers.parquet + events.jsonl        │  + pipeline_*.json              │
   + signals/*.csv (观察副本)                  │    │                            │
              │                              │    │                            │
              └──────────────┬───────────────┘    └──────────────┬─────────────┘
                             │                                   │
                             └───────────────┬───────────────────┘
                                             │
                                    EventRecorder (已有，不变)
                                             │
                                    data.csv + 5x JSONL
                                    + metadata.json (旧格式)
```

CLI 新增的存储层与 Streamlit 的 PipelineCapture **平行互补**，底层都从 BacktestRunner 获取数据。关键差异：
- PipelineCapture 捕获**全量管道数据**（含 noisy/filtered/ohlcv）
- CLI 新存储只写**决策链路数据**（信号→交易），更聚焦、更小、更适合离线分析

## 附录 B：v1.0 → v2.0 变更对照

| 方面 | v1.0 | v2.0 |
|------|------|------|
| 存储范围 | 全量管道数据（含 OHLCV、filtered） | 仅决策链路（信号→交易） |
| bars.parquet | 每 bar OHLCV + 指标 + 信号 | 改名为 signals/*.parquet，不含 OHLCV/filtered |
| 多周期处理 | 单文件，view_index 分区 | 每周期独立 Parquet + CSV |
| CSV 角色 | 旧格式 data.csv（ffill 有前视偏差） | 观察副本，与 Parquet 同目录、数值一致 |
| 数据量（100 标的） | ~10-16 GB | ~5-8 GB |
| 存储重点 | "能存的全存" | "只存决策链路，输入侧从 DB 复现" |
