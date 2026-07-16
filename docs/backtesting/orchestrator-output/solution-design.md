# 回测数据 CLI 存储方案设计

> 版本: v1.0 | 日期: 2026-07-16 | 作者: 量化系统架构组
> 基于 T1（核心引擎分析）、T2（数据输出机制分析）、T3（存储最佳实践）三份研究报告

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

---

## 2. 设计目标

1. **全量存储**：按最小周期步长（`_min_tf`）存储 BacktestRunner 产生的全部数据，不做降采样或摘要
2. **CLI 独立运行**：不依赖 Streamlit UI、不需要浏览器、不需要 `PIPELINE_CAPTURE` 环境变量
3. **离线可分析**：输出数据可直接用于绘制 Streamlit 同级图表、运行离线回测评估
4. **向后兼容**：保留现有 `EventRecorder` 输出（CSV + JSONL），新格式为增量补充
5. **可扩展**：支持多 ticker 批量运行、支持跨 session 聚合分析

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
| 行业采用 | — | — | zipline/vnpy/Nautilus | 旧方案 |

**结论**：Parquet 在压缩率、查询性能、Schema 自描述三个维度全面领先。JSONL 适合事件流（非结构化/半结构化追加写入）。

### 3.2 推荐方案

| 格式 | 用途 | 理由 |
|------|------|------|
| **Parquet (ZSTD)** | 全量时序数据（bars、trades） | 列存、高压缩、谓词下推、Schema 自描述 |
| **JSONL** | 事件流（BS 变更、信号触发、pair 事件） | 追加写入、人类可读、适合事件溯源 |
| **JSON** | metadata（运行参数、schema 版本） | 单文件、人类可读、键值结构 |

**不推荐 HDF5**：生态收缩（pandas 已标记 PyTables 为可选依赖），且缺乏谓词下推能力。
**不推荐纯 CSV**：无 Schema 保证、无压缩、存在 ffill 前视偏差风险。

### 3.3 各格式职责分工

```
Parquet ──── 结构化时序数据，可高效查询、聚合、切片
  ├── bars.parquet     每 bar 的 OHLCV + 指标 + 信号 + PnL
  └── trades.parquet   每笔交易的完整明细

JSONL ──── 事件流，按时间追加，人类可读
  └── events.jsonl     合并现有 5 个 JSONL（BS/schmitt/signal/trade/pair）

JSON ──── 会话元数据
  └── metadata.json    运行参数 + schema 版本 + 统计摘要
```

---

## 4. 数据组织设计

### 4.1 目录结构

```
backtest_output/                          # 可配置根目录
├── AAPL/                                 # 按 ticker 分组
│   ├── 20260716_143052_d1/               # {date}_{time}_{preset}
│   │   ├── bars.parquet                  # ★ 每 bar 全量数据
│   │   ├── trades.parquet                # ★ 逐笔交易明细
│   │   ├── events.jsonl                  # 合并事件流
│   │   └── metadata.json                 # 运行元数据
│   │
│   └── 20260716_150830_h4/
│       ├── bars.parquet
│       ├── trades.parquet
│       ├── events.jsonl
│       └── metadata.json
│
├── TSLA/
│   └── 20260716_160000_d1/
│       └── ...
│
└── _batch_index.json                     # 批量运行索引（可选）
```

**设计决策**：
- Ticker 在第一层：方便按标的分组，直接 `ls AAPL/` 查看该标的所有 session
- Session 命名含 preset：`{date}_{time}_{preset}`，肉眼可辨回测参数
- 四个文件平铺在 session 目录：不按 timeframe 拆分子目录（单 session 通常单周期），若未来引入多周期同时回测再拆

### 4.2 文件命名规范

| 文件 | 命名 | 说明 |
|------|------|------|
| bars 数据 | `bars.parquet` | 固定名称，无需参数化 |
| trades 数据 | `trades.parquet` | 固定名称 |
| 事件流 | `events.jsonl` | 固定名称，合并 5 个旧 JSONL |
| 元数据 | `metadata.json` | 固定名称，内含 schema_version |
| 批量索引 | `_batch_index.json` | 根目录下，记录所有 session 路径和参数 |

### 4.3 核心 Schema 设计

#### 4.3.1 bars.parquet（每 bar 全量数据）

每行 = 一个 `(view_index, bar_index)` 组合。是最细粒度的时序数据。

```
Schema Version: 1

┌─────────────────┬──────────┬──────────────────────────────────────┐
│ 列名             │ 类型      │ 说明                                 │
├─────────────────┼──────────┼──────────────────────────────────────┤
│ view_index       │ int16    │ 视图编号 (0, 1, 2, ...)             │
│ bar_index        │ int32    │ bar 序号（按 _min_tf）              │
│ timestamp        │ datetime64[ns] │ 实际时间戳（来自 dates）      │
│ open             │ float32  │ 开盘价                               │
│ high             │ float32  │ 最高价                               │
│ low              │ float32  │ 最低价                               │
│ close            │ float32  │ 收盘价（即 noisy）                   │
│ volume           │ float64  │ 成交量                               │
│ filtered         │ float32  │ 主滤波器输出                         │
│ filtered2        │ float32  │ 副滤波器输出                         │
│ schmitt_sig      │ float32  │ 施密特触发器信号                     │
│ schmitt_eps      │ float32  │ 施密特触发器 eps                     │
│ schmitt_sigma_v  │ float32  │ 施密特触发器 sigma_v                 │
│ schmitt_v        │ float32  │ 施密特触发器 v                       │
│ schmitt_a        │ float32  │ 施密特触发器 a                       │
│ long_pnl         │ float32  │ 多头 PnL 累积值                      │
│ short_pnl        │ float32  │ 空头 PnL 累积值                      │
│ higher_pnl       │ float32  │ 高周期对齐 PnL                       │
│ long_mask        │ bool     │ 多头持仓掩码                         │
│ short_mask       │ bool     │ 空头持仓掩码                         │
│ pair_id          │ int32   │ 所属 prediction pair ID（-1 表示无）  │
│ bs_label         │ int8    │ BS 标签（-1=卖, 0=无, 1=买）         │
│ bs_count         │ int16   │ BS 信号计数（该 bar）                 │
│ trade_active     │ bool    │ 该 bar 是否处于持仓中                  │
└─────────────────┴──────────┴──────────────────────────────────────┘

分区策略：按 view_index 分区（每视图一个 row group）
排序键： (view_index, bar_index)
压缩： ZSTD level=3
```

**设计理由**：
- `view_index` + `bar_index` 作为复合排序键，支持按视图切片和按时间范围过滤
- 使用 float32（非 float64）减少 50% 存储，回测精度足够
- `pair_id` 关联 prediction_pairs，`-1` 表示不在任何 pair 中
- trade_active 是派生字段（由 long_mask | short_mask 计算），冗余存储以加速查询

#### 4.3.2 trades.parquet（逐笔交易明细）

每行 = 一笔完整的交易（从 entry 到 exit）。

```
Schema Version: 1

┌──────────────────┬──────────┬──────────────────────────────────┐
│ 列名              │ 类型      │ 说明                            │
├──────────────────┼──────────┼──────────────────────────────────┤
│ trade_id          │ int32   │ 交易序号（全局递增）              │
│ view_index        │ int16   │ 所属视图                         │
│ side              │ str     │ "long" / "short"                  │
│ entry_bar         │ int32   │ 入场 bar_index                    │
│ exit_bar          │ int32   │ 出场 bar_index                    │
│ entry_time        │ datetime64[ns] │ 入场时间戳               │
│ exit_time         │ datetime64[ns] │ 出场时间戳               │
│ entry_price       │ float32 │ 入场价格                          │
│ exit_price        │ float32 │ 出场价格                          │
│ pnl               │ float32 │ 交易盈亏                          │
│ pnl_pct           │ float32 │ 交易盈亏百分比                    │
│ hold_bars         │ int32   │ 持仓 bar 数                      │
│ trade_type        │ str     │ 交易类型（schmitt/prediction/stop）│
│ pair_id           │ int32   │ 关联 prediction pair（-1 表示无） │
└──────────────────┴──────────┴──────────────────────────────────┘

排序键： (view_index, entry_bar)
压缩： ZSTD level=3
```

**数据来源**：从 `trade_records` 提取，补全 entry/exit bar、price、pnl 字段（这些当前仅存统计，需在 BacktestRunner 中透传全量数据给存储层）。

#### 4.3.3 events.jsonl（合并事件流）

每行 = 一个事件。将现有 5 个 JSONL 合并为单文件，用 `event_type` 区分。

```
每行 JSON 结构：
{
  "event_type": "bs_change | schmitt_change | signal_trigger | trade_open | trade_close | pair_start | pair_end",
  "view_index": 0,
  "bar_index": 1243,
  "timestamp": "2026-07-16T14:35:00",
  "data": {
    // 按 event_type 不同的 payload
    // bs_change:  {"old_label": 0, "new_label": 1, "count": 3}
    // schmitt_change: {"sig": 0.85, "eps": 0.02, ...}
    // trade_open:  {"side": "long", "entry_price": 150.25, "trade_id": 42}
    // trade_close: {"side": "long", "exit_price": 152.10, "pnl": 1.85, "trade_id": 42}
    // pair_start:  {"pair_id": 5, "start_bar": 1200}
    // pair_end:    {"pair_id": 5, "end_bar": 1350}
  }
}
```

**合并理由**：
- 5 个独立 JSONL 导致按时间顺序读取需要多路归并，复杂度高
- 单 JSONL 追加写入时自然按时间排序，读取时直接 `cat events.jsonl | jq` 即可
- `event_type` 字段提供与旧格式等价的信息，下游可按类型过滤

#### 4.3.4 metadata.json（运行元数据）

```json
{
  "schema_version": "1.0.0",
  "bars_schema_version": 1,
  "trades_schema_version": 1,
  "events_schema_version": 1,
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
    "min_tf": "5min",
    "step_interval": 1,
    "view_filter": null
  },
  "statistics": {
    "total_bars": 5000,
    "total_views": 8,
    "total_trades": 142,
    "long_trades": 71,
    "short_trades": 71,
    "win_rate": 0.58,
    "total_pnl": 12450.5,
    "sharpe_ratio": 1.82
  },
  "files": {
    "bars_parquet_size": "12.3 MB",
    "trades_parquet_size": "45 KB",
    "events_jsonl_size": "1.2 MB",
    "bars_parquet_rows": 40000,
    "trades_parquet_rows": 142
  }
}
```

---

## 5. CLI 增强方案

### 5.1 新增参数

在现有 `backtest_cli.py` 参数基础上增加以下参数：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--save-data` | flag | False | 启用全量数据持久化 |
| `--save-format` | choice | `parquet` | 全量数据格式：`parquet`, `parquet+csv` |
| `--save-events` | flag | True | 是否保存事件流 JSONL |
| `--save-trades` | flag | True | 是否保存交易明细 |
| `--output-root` | path | `./backtest_output` | 数据输出根目录 |
| `--no-metadata` | flag | False | 跳过 metadata.json 写入 |
| `--schema-version` | int | 1 | 强制指定 schema 版本（用于降级兼容） |

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

### 5.2 输出格式选项

| 模式 | 命令 | 输出内容 |
|------|------|----------|
| **仅回测（默认）** | `python backtest_cli.py --ticker AAPL --preset d1` | 终端输出，无文件 |
| **全量 Parquet** | 添加 `--save-data` | bars.parquet + trades.parquet + events.jsonl + metadata.json |
| **Parquet + CSV（兼容）** | 添加 `--save-data --save-format parquet+csv` | 上述 + 旧 data.csv（供下游工具过渡期使用） |
| **仅事件流** | `--save-data --no-trades` | bars.parquet + events.jsonl + metadata.json（省略 trades） |

### 5.3 使用示例

```bash
# 单标的回测 + 全量存储
python backtest_cli.py \
  --ticker AAPL \
  --preset d1 \
  --start-bar 0 \
  --end-bar 5000 \
  --save-data \
  --output-root ./backtest_output

# 输出：
# backtest_output/AAPL/20260716_143052_d1/
#   ├── bars.parquet      (12.3 MB, 40000 rows)
#   ├── trades.parquet    (45 KB, 142 rows)
#   ├── events.jsonl      (1.2 MB)
#   └── metadata.json     (512 B)

# 批量多标的（shell 循环）
for ticker in AAPL TSLA MSFT NVDA; do
  python backtest_cli.py \
    --ticker $ticker \
    --preset d1 \
    --save-data \
    --output-root ./batch_output &
done
wait

# 读取分析（Python/DuckDB）
# import duckdb
# duckdb.sql("""
#   SELECT view_index, timestamp, close, long_pnl
#   FROM read_parquet('backtest_output/AAPL/*/bars.parquet')
#   WHERE bar_index BETWEEN 1000 AND 2000
# """)
```

---

## 6. 实施路径

### Phase 1：补全数据（最小改动）

**目标**：在 BacktestRunner 中透传全量数据到存储层，CLI 模式可写出完整 JSON。

**改动点**：
1. `BacktestRunner.run()` 新增 `collect_data` 参数，为 True 时累积全量数据到 dict
2. `EventRecorder` 新增 `record_full_bar()` 方法，接收全量 dict 并追加到内存 buffer
3. CLI 模式 `--save-data` 时调用 `record_full_bar()`，回测结束时序列化为 JSON

**输出**：
```
{ticker}_{session_id}/
  ├── bars_full.json       # 临时 JSON（待 Phase 2 迁移到 Parquet）
  └── metadata.json
```

**验证标准**：
- 对比同一 ticker/preset 的 `PIPELINE_CAPTURE=1` 输出与 CLI `--save-data` 输出，数值完全一致
- `bars_full.json` 包含 T1 报告中所有"缺失"字段

**注意**：JSON 是过渡格式。100 只股票 × 10 年 × 8 周期 = 约 80GB JSON（未压缩），仅供验证，不用于生产。

---

### Phase 2：优化格式（Parquet 迁移）

**目标**：将全量数据从 JSON 迁移到 Parquet，实现高压缩和快速查询。

**改动点**：
1. 新增 `ParquetStore` 类，封装 pyarrow 写入逻辑
2. `--save-data` 默认输出格式改为 Parquet
3. 按 view_index 分区写入，每 N 个 bar flush 一次 row group
4. events.jsonl 追加写入（合并 5 个旧 JSONL）
5. 删除 Phase 1 的 JSON 输出逻辑

**输出**：
```
{ticker}_{session_id}/
  ├── bars.parquet
  ├── trades.parquet
  ├── events.jsonl
  └── metadata.json
```

**验证标准**：
- `bars.parquet` 可被 `pandas.read_parquet()` 和 `duckdb.read_parquet()` 正常读取
- 压缩后文件大小 < CSV 的 20%
- 按 `view_index` 过滤查询 < 100ms（DuckDB，SSD 环境）
- 数据与 Phase 1 JSON 输出完全一致（round-trip 验证）

---

### Phase 3：高级特性

**3a. Schema 版本化**

- metadata.json 中写入 `schema_version` 字段
- `ParquetStore` 写入时在 Parquet 文件的 metadata 中嵌入 schema 版本
- 读取时检查版本，不匹配时输出警告（不阻断，因为旧版本通常仍可读）
- Schema 变更规则：
  - 新增列：minor 版本号 +1，向后兼容
  - 删除/重命名列：major 版本号 +1，读取器需适配

**3b. 增量回测模式**

- `--incremental` 参数：追加写入同一 session 的 bars.parquet
- 使用 Parquet 的 `append` 模式，校验 schema 一致性
- 适用场景：中断恢复、分批回测

**3c. DuckDB 跨 Session 分析视图**

- 提供 `CREATE VIEW` SQL 脚本，将分散的 `.parquet` 文件聚合为逻辑视图
- 示例：
  ```sql
  CREATE VIEW all_bars AS
  SELECT * FROM read_parquet('backtest_output/*/*/bars.parquet',
                              union_by_name=true);
  ```

**验证标准**：
- Schema 版本不匹配时读取器发出明确警告
- 增量写入 3 次后数据完整性校验通过
- DuckDB 跨 session 聚合查询返回正确结果

---

## 7. 风险与注意事项

### 7.1 ffill 前视偏差处理

**问题**：CSVBuilder 的 `ffill()` 将粗周期（日线）值前向填充到所有子日线 bar，导致日线收盘价出现在当天 9:30 的 bar 上。

**解决方案**：
- bars.parquet **不使用 ffill**：每个 bar 存储该 bar 的真实值，缺失值保持为 NaN
- 读取侧需要前向填充时，由分析代码显式调用 `.ffill()`（按 view_index 分组后），而非存储时隐式填充
- 在 metadata.json 中记录 `ffill_applied: false` 明确标注

**设计原则**：存储原始数据，填充逻辑在读取侧按需执行。

### 7.2 Schema 版本化策略

| 变更类型 | 版本号变化 | 兼容性 | 示例 |
|----------|-----------|--------|------|
| 新增列 | minor +1 | 向后兼容 | 新增 `ema_20` 列 |
| 列重命名 | major +1 | 不兼容 | `noisy` → `close_price` |
| 列删除 | major +1 | 不兼容 | 移除 `prediction_pairs` |
| 类型变更 | major +1 | 不兼容 | float32 → float64 |
| 新增 event_type | minor +1 | 向后兼容 | events.jsonl 新增类型 |

**存储位置**：
- Parquet: 文件级 key-value metadata (`b"schema_version": b"1"`)
- metadata.json: 顶级字段 `"schema_version": "1.0.0"`
- events.jsonl: 首行写入 schema 声明事件

### 7.3 向后兼容

| 现有行为 | 兼容策略 |
|----------|----------|
| `--output-dir` 参数 | 保留，指向旧 CSV/JSONL 输出目录。`--save-data` 时新旧同时输出 |
| `EventRecorder` CSV + 5 JSONL | 不变。新输出为额外增量，不影响现有文件 |
| Streamlit `PIPELINE_CAPTURE` | 不变。CLI Parquet 输出和 Streamlit PipelineCapture 并存 |
| 无 `--save-data` 时 | 行为与当前 CLI 完全一致（仅终端输出） |

**过渡期建议**：
- Phase 1-2 期间：新旧格式并行输出，下游可逐步迁移
- Phase 3 之后：标记旧 CSV 输出为 deprecated，2 个大版本后移除

### 7.4 性能考量

| 场景 | 预估 | 缓解措施 |
|------|------|----------|
| 100 标的批量回测 | 10-16GB Parquet | 按 ticker 分目录，可并行写入 |
| bars.parquet 写入 | ~20MB/s（HDD）| 按 row group 批量 flush，减少 IO |
| DuckDB 跨 session 查询 | < 1s（10GB 扫描）| Parquet 谓词下推 + 列裁剪，仅读所需列 |
| JSONL 事件追加 | ~5MB/s | 缓冲写入（每 1000 事件 flush 一次） |
| 内存占用 | ~50MB（单 session buffer）| 按 view_index 逐视图处理后释放 |

**数据量预估（100 标的 × 10 年 × 8 周期）**：

| 文件 | 单 Session | ×100 标的 | 压缩后 |
|------|-----------|-----------|--------|
| bars.parquet | ~120 MB（CSV）→ ~15 MB | ~1.5 GB | Parquet ZSTD |
| trades.parquet | ~0.5 MB | ~50 MB | — |
| events.jsonl | ~10 MB | ~1 GB | — |
| **合计** | ~130 MB（CSV）→ ~25 MB | **~10-16 GB** | — |

**单 session** Parquet 输出约 25MB（对比 CSV 130MB），满足本地开发和分析需求。
**批量运行** 100 标的约 10-16GB，普通 SSD 完全可承受，无需引入分布式存储。

---

## 附录：与现有架构的关系

```
                          CLI 模式                        Streamlit 模式
                             │                                  │
              ┌──────────────┼──────────────┐    ┌──────────────┼──────────────┐
              │    --save-data               │    │  PIPELINE_CAPTURE=1         │
              ▼                              │    ▼                            │
        ParquetStore                         │  PipelineCapture                │
        (新增)                               │  (已有)                         │
              │                              │    │                            │
              ▼                              │    ▼                            │
   bars.parquet + trades.parquet             │  pipeline_*.parquet             │
   + events.jsonl + metadata.json            │  + pipeline_*.json              │
              │                              │    │                            │
              └──────────────┬───────────────┘    └──────────────┬─────────────┘
                             │                                   │
                             └───────────────┬───────────────────┘
                                             │
                                    EventRecorder (已有，不变)
                                             │
                                    data.csv + 5× JSONL
                                    + metadata.json (旧格式)
```

CLI 新增的 ParquetStore 与 Streamlit 的 PipelineCapture **平行互补**，底层都从 BacktestRunner 获取全量数据。EventRecorder 保持原有行为不变。
