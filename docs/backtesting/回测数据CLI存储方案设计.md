# 回测数据 CLI 存储方案设计 v3.1

## 1. 现状分析

当前 `EventRecorder` 仅存储摘要数据：尾部 5 个 bar 的关键值、各视图的信号计数与 PnL 终值。全量信号数组、逐 bar 的 PnL 曲线、交易明细在回测过程中计算后直接丢弃——回测结束后无法回溯任意时刻各周期的完整状态，也无法做离线分析或可视化对比。恢复这些数据只能重新跑回测。

## 2. 设计目标

1. **统一时间轴**：以最细粒度周期（`_min_tf`）的 bar_index 为行索引，每行是一个 min_tf bar 时刻的快照，所有周期的数据在同一行上对齐。
2. **全量决策数据落盘**：每步的信号、PnL、持仓、交易、BS 标记全部持久化，支持离线查询与可视化。
3. **CLI 可独立运行**：存储逻辑集成在现有 `backtest_cli.py` 中，不依赖 Streamlit UI。
4. **机器可读 + 人工可读双输出**：Parquet 供程序分析，CSV 供 Excel 打开观察，内容一致。

## 3. 格式选型

| 特性 | Parquet | CSV | JSONL |
|------|---------|-----|-------|
| 读取速度 | 列存，按列读取极快 | 全量扫描 | 全量扫描 |
| 文件体积 | 压缩后极小（ZSTD） | 无压缩，体积大 | 无压缩，体积大 |
| 类型系统 | 原生 schema，类型安全 | 无类型，全靠解析 | 弱类型 |
| DataFrame 生态 | pandas/polars 零开销加载 | 需推断类型 | 需解析 JSON |
| 人工可读 | 需要工具打开 | Excel 直接打开 | 文本编辑器可看 |

**选型决策**：Parquet 为主存储格式——列存、压缩、类型安全，pandas 一行 `read_parquet` 即可加载。CSV 为附带输出——满足人工 Excel 打开观察的需求（理由：非技术用户最熟悉的格式）。不选 JSONL——无类型、无压缩、体积大，两个格式已覆盖全部场景。

## 4. 数据组织

### 4.1 统一时间轴

以 `_min_tf` 的 bar_index 为行索引。每行是一个 min_tf bar 时刻的快照，**所有周期的数据在同一行上**。

示例：假设 min_tf=5min，日线 bar 在 5min 的第 0、48、96…行切换。

```
bar_index | bar_timestamp        | v0_日线_sig | v1_60min_sig | v2_15min_sig | v3_5min_sig
0         | 2024-01-02T09:35:00 | 0           | 0            | 0            | 1
1         | 2024-01-02T09:40:00 | 0           | 0            | 0            | 1
...
47        | 2024-01-02T13:25:00 | 0           | 0            | 1            | -1
48        | 2024-01-02T13:30:00 | 1           | 0            | 0            | 0
```

粗周期（日线）在同一日线 bar 内的 48 个 min_tf 行上值自然重复——这不是 ffill 造假，而是准确反映"该时刻该周期的信号状态"。因为日线信号在新日线 bar 到来前确实保持不变。

### 4.2 Schema 定义

**固定列（每行 2 列）**：

| 列名 | 类型 | 说明 |
|------|------|------|
| `bar_index` | int32 | min_tf 上的 bar 索引，从 0 开始 |
| `bar_timestamp` | str | bar 的 ISO 8601 时间戳（如 `2024-01-02T09:35:00`） |

**每视图列（4 个视图 v0-v3，每视图 12 列，共 48 列）**：

| 列名格式 | 类型 | 说明 |
|----------|------|------|
| `{view}_sig` | int8 | 施密特信号（-1=空头, 0=中性, 1=多头） |
| `{view}_filtered` | float32 | P0: 滤波价格，图表复现必需 |
| `{view}_eps` | float32 | P0: 自适应阈值，判断信号质量 |
| `{view}_pnl_long` | float32 | 做多累计 PnL（从 100 起始，100±盈亏） |
| `{view}_pnl_short` | float32 | 做空累计 PnL（从 100 起始，100±盈亏） |
| `{view}_long_pos` | bool | 是否在做多持仓中 |
| `{view}_short_pos` | bool | 是否在做空持仓中 |
| `{view}_trade` | str | 交易事件：`"entry_long"` / `"exit_long"` / `"entry_short"` / `"exit_short"` / `null` |
| `{view}_trade_return` | float32 | P1: 交易盈亏百分比 |
| `{view}_trade_reason` | str | P1: 离场原因(stop_loss/take_profit/signal_reverse) |
| `{view}_bs_entry` | str | BS 入场标记：`"B"` / `"S"` / `null` |
| `{view}_bs_exit` | str | BS 离场标记：`"B"` / `"S"` / `null` |

**视图前缀映射**（列名示例）：

| 视图 | 对应周期 | 前缀 | 示例列名 |
|------|----------|------|----------|
| v0 | 日线 | `v0_日线` | `v0_日线_sig`, `v0_日线_pnl_long` |
| v1 | 60分钟 | `v1_60min` | `v1_60min_sig`, `v1_60min_long_pos` |
| v2 | 15分钟 | `v2_15min` | `v2_15min_sig`, `v2_15min_trade` |
| v3 | 5分钟（min_tf） | `v3_5min` | `v3_5min_sig`, `v3_5min_bs_entry` |

总计：2 + 4 x 12 = **50 列**。

### 4.3 目录结构

```
{output_dir}/
  {TICKER}/
    {SESSION_ID}/
      backtest_result.parquet
      backtest_result.csv
      metadata.json
```

- `{output_dir}` — 由 `--output-dir` 指定，默认 `./backtest_results`
- `{TICKER}` — 股票代码，如 `AAPL`
- `{SESSION_ID}` — 会话标识，格式 `YYYYMMDD-HHMMSS` 或 UUID
- `metadata.json` — 会话元信息（见 7.2 节 schema 版本化）

### 4.4 写入机制

`BacktestRunner.run()` 的每个 bar 步，在各视图计算完成后，将当前步结果组装为一行，追加写入 Parquet。

**粗周期视图的处理**：在非更新 bar 上（如 5min bar 不在日线切换点），写入与上一步相同的值。这由 `BacktestRunner` 自然保证——日线视图的信号/持仓在日线 bar 切换前不变，每次步进时读取的就是当前值。

**实现方式**：
1. `BacktestRunner` 每步将各视图计算结果收集为 dict
2. 调用 `ParquetStore.write_row(row_dict)` 追加一行
3. `ParquetStore` 内部维护一个行缓冲区（如 1000 行），满后批量写入（避免每行触发一次 I/O）
4. **定时 flush**：每 30 秒强制刷盘一次（基于 `time.time()` 检查），防止长时回测中途中断导致缓冲区数据丢失
5. `run()` 结束后调用 `ParquetStore.flush()` 写入剩余行、导出 CSV、关闭文件

**写入可靠性**：
- **原子写入**：先写 `.tmp` 临时文件（如 `backtest_result.parquet.tmp`），写入完成并验证后通过 `os.rename` 原子重命名为正式文件名。避免写入中途崩溃导致 Parquet 文件损坏，确保读取方永远看不到半成品文件。
- **定时 flush**：每 30 秒强制刷盘，与行缓冲区满触发形成双重保障——短回测靠缓冲满触发，长回测靠定时触发，确保中断时数据损失不超过 30 秒。

**CSV 生成时机**：Parquet 写入完成后，用 pandas 读取并 `to_csv` 导出，不逐行写 CSV。

## 5. CLI 增强方案

### 5.1 新增参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--save-data` | flag | `False` | 启用 Parquet + CSV 存储 |
| `--output-dir` | str | `./backtest_results` | 输出根目录（已有参数，复用） |

### 5.2 使用示例

```bash
# 基础用法：回测并保存全量数据
python backtest_cli.py --ticker AAPL --start 2024-01-01 --end 2024-12-31 --save-data

# 指定输出目录
python backtest_cli.py --ticker AAPL --start 2024-01-01 --end 2024-12-31 \
  --save-data --output-dir ./my_results

# 批量回测（shell 循环）
for ticker in AAPL GOOGL MSFT; do
  python backtest_cli.py --ticker $ticker --start 2024-01-01 --end 2024-12-31 --save-data
done
```

### 5.3 读取示例

```python
import pandas as pd

# 加载全量数据
df = pd.read_parquet("backtest_results/AAPL/20240716-143052/backtest_result.parquet")

# 查看日线信号切换点
daily_switches = df[df["v0_日线_sig"] != df["v0_日线_sig"].shift(1)]

# 查看某视图的交易事件
trades = df[df["v2_15min_trade"].notna()][["bar_index", "bar_timestamp", "v2_15min_trade"]]

# 绘制 PnL 曲线
df[["bar_timestamp", "v0_日线_pnl_long", "v1_60min_pnl_long"]].plot(x="bar_timestamp")
```

## 6. 实施路径

### 6.1 最小改动方案

新增一个 `ParquetStore` 类，**不修改** `EventRecorder` 现有行为（保持向后兼容）。改动仅涉及 2 个文件：

| 文件 | 改动 | 理由 |
|------|------|------|
| 新增 `parquet_store.py` | 实现 `ParquetStore` 类 | 独立模块，职责单一 |
| 修改 `backtest_cli.py` | 新增 `--save-data` 参数，run 中集成 `ParquetStore` | 最小侵入 |

### 6.2 ParquetStore 接口

```python
class ParquetStore:
    def __init__(self, output_dir: str, ticker: str, session_id: str,
                 schema: dict, buffer_size: int = 1000)
    def write_row(self, row: dict) -> None       # 追加一行到缓冲区
    def flush(self) -> None                       # 刷缓冲区 + 写 CSV + 写 metadata
    def close(self) -> None                       # 清理资源
```

### 6.3 BacktestRunner 集成点

在 `BacktestRunner.run()` 的循环体内，每步计算完成后：

```python
if self.parquet_store:
    self.parquet_store.write_row({
        "bar_index": bar_index,
        "bar_timestamp": bar_timestamp,
        "v0_日线_sig": views[0].signal,
        "v0_日线_pnl_long": views[0].pnl_long,
        # ... 其余 48 列
    })
```

循环结束后调用 `self.parquet_store.flush()`。

### 6.4 文件大小估算

假设一个回测 session 有 10,000 个 min_tf bar（约 2 个月 5min 数据）：

- Parquet（ZSTD level=3）：50 列 x 10000 行，大量重复值（粗周期 + bool 列），预计 **~800KB-3MB**
- CSV：相同数据无压缩，预计 **~5-8MB**

### 6.5 验证标准

- Parquet 文件可用 `pd.read_parquet()` 加载且 schema 正确
- CSV 内容与 Parquet 一致（`pd.read_csv` vs `pd.read_parquet` 对比通过）
- `metadata.json` 包含 session_id、ticker、时间范围、schema_version
- `--save-data` 不传时，行为与现有完全一致（零影响）

## 7. 风险与注意事项

### 7.1 粗周期重复数据

粗周期信号在多个 min_tf 行上重复，这是正确行为而非缺陷。但在分析时需注意：不能对重复行直接做统计聚合（如 `value_counts` 会虚高）。正确做法是先取每个周期的切换点（`shift` 判断变化）再做统计。

### 7.2 Schema 版本化

`metadata.json` 内容：

```json
{
  "schema_version": "3.1",
  "session_id": "20240716-143052",
  "ticker": "AAPL",
  "min_tf": "5min",
  "views": ["日线", "60min", "15min", "5min"],
  "start_time": "2024-01-02T09:30:00",
  "end_time": "2024-12-31T16:00:00",
  "bar_count": 10000,
  "created_at": "2024-07-16T14:30:52",
  "view_configs": {
    "v0_日线": {
      "filter_type": "kalman",
      "filter_params": {"delta": 1e-5, "R": 0.01},
      "schmitt_params": {"threshold_up": 0.5, "threshold_down": -0.5},
      "strategy_params": {"stop_loss": 0.05, "take_profit": 0.10}
    },
    "v1_60min": {
      "filter_type": "butterworth",
      "filter_params": {"order": 4, "cutoff": 0.1},
      "schmitt_params": {"threshold_up": 0.3, "threshold_down": -0.3},
      "strategy_params": {"stop_loss": 0.03, "take_profit": 0.06}
    },
    "v2_15min": {
      "filter_type": "kalman",
      "filter_params": {"delta": 1e-4, "R": 0.1},
      "schmitt_params": {"threshold_up": 0.2, "threshold_down": -0.2},
      "strategy_params": {"stop_loss": 0.02, "take_profit": 0.04}
    },
    "v3_5min": {
      "filter_type": "ema",
      "filter_params": {"span": 20},
      "schmitt_params": {"threshold_up": 0.15, "threshold_down": -0.15},
      "strategy_params": {"stop_loss": 0.01, "take_profit": 0.02}
    }
  }
}
```

**必须存储 `view_configs`**：包含每个视图的滤波器类型及参数（`filter_type` + `filter_params`）、施密特参数（`schmitt_params`）和策略参数（`strategy_params`）。这些配置是回测结果可复现的前提——缺少任意一项，后续重新加载数据时无法还原图表或验证策略行为。

未来 schema 变更时递增 `schema_version`，读取方根据版本号选择解析逻辑。

### 7.3 内存与性能

- 行缓冲区（默认 1000 行）限制内存占用；10 万行 session 也仅 ~100 次 flush
- ZSTD compression_level=3 是速度与压缩率的平衡点（pandas 默认 level=1，3 略慢但体积更优）
- 如果 session 极长（>50 万 bar），考虑降级为只存 Parquet，不生成 CSV（CSV 写入会显著变慢）

### 7.4 向后兼容

- `--save-data` 默认 `False`，不影响现有 CLI 使用方式
- `EventRecorder` 保持不变，不修改其接口或行为
- 两个输出文件 + metadata.json 是纯增量，不覆盖任何已有文件
