# 回测数据 CLI 存储方案设计 v3.3（已实现）

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
bar_index | bar_timestamp        | v0_sig | v1_sig | v2_sig | v3_sig
0         | 2024-01-02T09:35:00 | 0      | 0      | 0      | 1
1         | 2024-01-02T09:40:00 | 0      | 0      | 0      | 1
...
47        | 2024-01-02T13:25:00 | 0      | 0      | 1      | -1
48        | 2024-01-02T13:30:00 | 1      | 0      | 0      | 0
```

粗周期（日线）在同一日线 bar 内的 48 个 min_tf 行上值自然重复——这不是 ffill 造假，而是准确反映"该时刻该周期的信号状态"。因为日线信号在新日线 bar 到来前确实保持不变。

### 4.1.1 数据坐标系说明

Parquet 存储中存在两套索引体系，理解它们的区别是正确使用 BS 标记和 trade 列的前提。

**全局 bar_index**（Parquet 行索引）：
- 以 `_min_tf`（最细粒度周期）为基准，从 0 开始编号
- 所有视图共享同一个全局 bar_index——每一行对应一个 min_tf bar 时刻
- 这是 Parquet 文件的行索引，也是 `bar_index` 列的值

**视图窗口索引**（view_last_idx）：
- 每个视图内部的数据数组（`t`、`filtered`、`schmitt["sig"]` 等）在视图自己的周期上计算
- 粗周期视图的内部数组长度远小于全局 bar_index（日线视图每 48 个 min_tf bar 才增长 1）
- `view_last_idx = len(t_arr) - 1`，即视图内部数组的最后一个有效索引（0-based，范围 0..n_pts-1）
- **BS marker 的坐标系统和 trade 记录的 entry_idx/exit_idx 均使用视图窗口索引**，而非全局 bar_index

**为什么 BS marker 必须用视图窗口索引匹配**：

BS marker 由回测引擎在视图内部生成，其坐标 `(bar_idx, label, ...)` 中的 `bar_idx` 是**视图窗口内的相对位置**，并非全局 bar_index。例如，日线视图的第 5 个 bar 上出现 BS 入场标记，其 `bar_idx=4`（视图窗口索引），但此时全局 bar_index 可能已经是 `4 × 48 = 192`（假设 min_tf=5min）。

因此，在 `ParquetStore._extract_view_columns()` 和 `CSVBuilder._extract_view_columns()` 中，BS marker 和 trade 记录的匹配条件均为：

```python
view_last_idx = len(t_arr) - 1        # 视图窗口索引
if int(marker[0]) == view_last_idx:    # 非 bar_index
    ...
```

> **历史记录**：早期实现曾错误地使用全局 `bar_index` 匹配 BS marker，导致几乎所有 marker 都无法命中，BS 列全为空。v3.3 已修复为使用 `view_last_idx` 匹配。

### 4.2 Schema 定义

**固定列（每行 2 列）**：

| 列名 | 类型 | 说明 |
|------|------|------|
| `bar_index` | int32 | min_tf 上的 bar 索引，从 0 开始 |
| `bar_timestamp` | timestamp[ns] | bar 的纳秒精度时间戳（原生 Parquet TIMESTAMP 类型，DuckDB 下推过滤性能优于字符串） |

**每视图列（4 个视图 v0-v3，每视图 12 列，共 48 列）**：

| 列名格式 | 类型 | 含义 | 数据来源 |
|----------|------|------|----------|
| `{view}_sig` | int8 | 施密特触发器信号值：+1(做多)/-1(做空)/0(空仓) | `schmitt["sig"][-1]`，取数组最后一个元素；fallback `0` |
| `{view}_filtered` | float32 | 滤波后的价格（视图窗口最后一个值），图表复现必需 | `filtered[-1]`，取数组最后一个元素；fallback `NaN` |
| `{view}_eps` | float32 | 自适应阈值带宽（epsilon），值越大信号切换越不敏感、越稳定 | `schmitt["eps"][-1]`，取数组最后一个元素；fallback `NaN` |
| `{view}_pnl_long` | float32 | 做多累计收益率，初始值 100（100 表示起始，>100 盈利，<100 亏损） | `long_pnl[-1]`，取数组最后一个元素；fallback `NaN` |
| `{view}_pnl_short` | float32 | 做空累计收益率，初始值 100（100 表示起始，>100 盈利，<100 亏损） | `short_pnl[-1]`，取数组最后一个元素；fallback `NaN` |
| `{view}_long_pos` | bool | 当前 bar 是否在做多持仓中 | `long_mask[-1]` → `bool()`，取数组最后一个元素；fallback `False` |
| `{view}_short_pos` | bool | 当前 bar 是否在做空持仓中 | `short_mask[-1]` → `bool()`，取数组最后一个元素；fallback `False` |
| `{view}_trade` | str | 交易事件标记 | 遍历 `trade_records[]`，匹配 `exit_idx == view_last_idx` 时写 `exit_{type}`，匹配 `entry_idx == view_last_idx` 时写 `entry_{type}`（type 为 `long`/`short`），无匹配则为空字符串 |
| `{view}_trade_return` | float32 | 该笔交易的盈亏百分比（仅离场 bar 有值，入场 bar 为 NaN） | 同上匹配条件，取 `trade["return_pct"]`；fallback `NaN` |
| `{view}_trade_reason` | str | 离场原因 | 同上匹配条件，取 `trade["exit_reason"]`；取值：`stop_loss`/`take_profit`/`eod`/`signal_reverse`；fallback 空字符串 |
| `{view}_bs_entry` | str | BS 入场标记 | 遍历 `bs_markers["entry_markers"][]`（每项为 `(bar_idx, label, color, date)`），匹配 `m[0] == view_last_idx` 时取 `m[1]`；取值：`B`(买入)/`S`(卖出)/空字符串(无) |
| `{view}_bs_exit` | str | BS 离场标记 | 遍历 `bs_markers["exit_markers"][]`（每项为 `(bar_idx, label, color, exit_reason, date)`），匹配 `m[0] == view_last_idx` 时取 `m[1]`；取值：`B`(买入平仓)/`S`(卖出平仓)/空字符串(无) |

> **数据来源说明**：
> - `[-1]`：直接取数组最后一个元素（7 列），对应 `_last_float()`/`_last_int()`/`_last_scalar()` helper
> - `view_last_idx`：`len(t_arr) - 1`，即视图窗口内的最后一个索引（0-based），用于 BS marker 和 trade 记录的精确匹配（5 列）。详见 4.1.1 坐标系说明。
> - Parquet 类型使用 pyarrow 原生类型：`pa.int8()`, `pa.float32()`, `pa.bool_()`, `pa.string()`，确保跨工具兼容

> **列名设计决策**：列名使用纯 ASCII（如 `v0_sig` 而非 `v0_日线_sig`），确保 Parquet、pandas、DuckDB、BI 工具的最大兼容性。中文周期标签通过 `metadata.json` 中的 `view_labels` 字段映射（见 9.2 节）。

**视图前缀映射**：

| 视图 | 对应周期 | 前缀 | 示例列名 |
|------|----------|------|----------|
| v0 | 日线 | `v0` | `v0_sig`, `v0_pnl_long` |
| v1 | 60分钟 | `v1` | `v1_sig`, `v1_long_pos` |
| v2 | 15分钟 | `v2` | `v2_sig`, `v2_trade` |
| v3 | 5分钟（min_tf） | `v3` | `v3_sig`, `v3_bs_entry` |

总计：2 + 4 x 12 = **50 列**。

### 4.3 目录结构

```
{output_dir}/
  {TICKER}_{SESSION_ID}/
    part_0001.parquet       # 分段文件（回测进行中）
    part_0002.parquet
    ...
    backtest_result.parquet # 合并后的最终文件（回测完成后）
    backtest_result.csv     # CSV 导出副本
    metadata.json
```

- `{output_dir}` — 由 `--output-dir` 指定，默认 `./backtest_output/`
- `{TICKER}` — 股票代码，如 `AAPL`
- `{SESSION_ID}` — 会话标识，格式 `YYYYMMDD-HHMMSS-{TICKER}`（如 `20240716-143052-AAPL`）
- `part_NNNN.parquet` — 分段 Parquet 文件（每次 flush 写入一个），`end_session` 时合并为 `backtest_result.parquet` 并删除分段
- `metadata.json` — 会话元信息（见 9.2 节 schema 版本化）

> **实际实现差异**：目录结构由原设计的 `{output_dir}/{TICKER}/{SESSION_ID}/` 两级嵌套简化为 `{output_dir}/{TICKER}_{SESSION_ID}/` 单级平铺——ticker 已包含在目录名中，无需额外嵌套层级。

### 4.4 写入机制

`BacktestRunner.run()` 的每个 bar 步，在各视图计算完成后，将当前步结果组装为一行，追加写入 Parquet。

**粗周期视图的处理**：在非更新 bar 上（如 5min bar 不在日线切换点），写入与上一步相同的值。这由 `BacktestRunner` 自然保证——日线视图的信号/持仓在日线 bar 切换前不变，每次步进时读取的就是当前值。

**实现方式**：
1. `BacktestRunner` 每步将各视图计算结果收集为 dict
2. 调用 `ParquetStore.write_row(row_dict)` 追加一行
3. `ParquetStore` 内部维护一个行缓冲区（默认 100 行），满后批量写入（避免每行触发一次 I/O）
4. **定时 flush**：每 30 秒强制刷盘一次（基于 `time.time()` 检查），防止长时回测中途中断导致缓冲区数据丢失
5. `run()` 结束后调用 `ParquetStore.flush()` 写入剩余行、导出 CSV、关闭文件

**写入可靠性**：
- **原子写入**：先写 `.tmp` 临时文件（如 `backtest_result.parquet.tmp`），写入完成并验证后通过 `os.rename` 原子重命名为正式文件名。避免写入中途崩溃导致 Parquet 文件损坏，确保读取方永远看不到半成品文件。
- **定时 flush**：每 30 秒强制刷盘，与行缓冲区满触发形成双重保障——短回测靠缓冲满触发，长回测靠定时触发，确保中断时数据损失不超过 30 秒。

**CSV 生成时机**：Parquet 写入完成后，用 pandas 读取并 `to_csv` 导出，不逐行写 CSV。

## 5. CLI 参数完整说明

### 5.1 全部参数

```bash
python -m filter_app.backtest_cli --help
```

| 参数 | 类型 | 默认值 | 含义 | 示例 |
|------|------|--------|------|------|
| `--ticker` | str | **必填** | 股票代码 | `--ticker AAPL`、`--ticker 03690.HK` |
| `--preset` | str | 无 | 预设配置名称（从 config_db 加载，与 `--config-file` 互斥） | `--preset 3690_HK_DP` |
| `--config-file` | str | 无 | JSON 配置文件路径（与 `--preset` 互斥） | `--config-file my_config.json` |
| `--start-bar` | int | 最小 n_pts | 起始 bar 索引（min_tf 周期），用于跳过预热期 | `--start-bar 500` |
| `--end-bar` | int | 总 bar 数 | 结束 bar 索引（不包含），用于限制回测范围 | `--end-bar 1000` |
| `--output-dir` | str | `./backtest_output/` | EventRecorder 和 ParquetStore 的输出根目录 | `--output-dir ./my_results` |
| `--save-data` | flag | `False` | 启用 Parquet + CSV 全量数据存储（调用 ParquetStore） | `--save-data` |
| `--step-interval` | int | `1` | 步进间隔，>1 时跳 bar 运行（加速调试） | `--step-interval 5` |
| `--view-filter` | str | 无 | 只运行指定视图（格式 `v{i}_{周期}`），默认运行全部 4 个视图 | `--view-filter v0_日线` |
| `--quiet` | flag | `False` | 静默模式，只打印开始和结束信息，不逐 bar 输出进度 | `--quiet` |

### 5.2 使用示例

```bash
# 基础用法：用预设配置回测并保存全量数据
python -m filter_app.backtest_cli --ticker 03690.HK --preset 3690_HK_DP --save-data

# 用 JSON 配置文件回测
python -m filter_app.backtest_cli --ticker AAPL --config-file my_config.json --save-data

# 限制 bar 范围（跳过预热期，加速调试）
python -m filter_app.backtest_cli --ticker 03690.HK --preset 3690_HK_DP \
  --start-bar 500 --end-bar 1000 --quiet

# 只运行单个视图
python -m filter_app.backtest_cli --ticker 03690.HK --preset 3690_HK_DP \
  --view-filter v0_日线 --save-data

# 指定输出目录 + 跳 bar 加速
python -m filter_app.backtest_cli --ticker AAPL --preset 3690_HK_DP \
  --save-data --output-dir ./my_results --step-interval 5

# 批量回测（shell 循环）
for ticker in AAPL GOOGL MSFT; do
  python -m filter_app.backtest_cli --ticker $ticker --config-file my_config.json --save-data --quiet
done
```

### 5.3 读取示例

```python
import pandas as pd

# 加载全量数据（注意实际目录结构为 {output_dir}/{TICKER}_{SESSION_ID}/）
df = pd.read_parquet("backtest_output/AAPL_20240716-143052-AAPL/backtest_result.parquet")

# 查看日线信号切换点
daily_switches = df[df["v0_sig"] != df["v0_sig"].shift(1)]

# 查看某视图的交易事件
trades = df[df["v2_trade"].notna()][["bar_index", "bar_timestamp", "v2_trade"]]

# 绘制 PnL 曲线
df[["bar_timestamp", "v0_pnl_long", "v1_pnl_long"]].plot(x="bar_timestamp")
```

## 6. 实施细节

### 6.1 ParquetStore 类接口概览

`ParquetStore` 与 `EventRecorder` 平级——两者都是 CLI 循环中的"消费者"，互不依赖，可独立启用/禁用。

```python
class ParquetStore:
    def __init__(self, output_dir: str, ticker: str, view_configs: list[dict],
                 *, buffer_size: int = 100, flush_interval_secs: float = 30.0,
                 compression: str = "zstd", compression_level: int = 3)

    def start_session(self) -> str          # 创建 session 目录 + 初始 metadata.json，返回 session_id
    def append_row(self, result: dict)       # 从 result 提取一行 → 缓冲；达到阈值自动 flush
    def flush(self)                          # buffer → pd.DataFrame → pa.Table → 追加 row_group
    def end_session(self)                    # 最终 flush + 关闭 writer + 写 CSV 副本 + 更新 metadata
```

**生命周期**（在 CLI `main()` 中调用）：

```
store = ParquetStore(args.output_dir, args.ticker, configs)
session_id = store.start_session()
for result in runner.run(start, end, step):
    store.append_row(result)
store.end_session()
```

### 6.2 列提取规则

从 `BacktestRunner.run()` 返回的 `result` 字典中提取 50 列，分两类：

**A. 取 `[-1]` 的列（直接取数组最后一个元素）**：

| 列 | 来源键路径 | 类型 | Fallback |
|----|-----------|------|----------|
| `{v}_sig` | `schmitt["sig"][-1]` | int8 | `0` |
| `{v}_filtered` | `filtered[-1]` | float32 | `NaN` |
| `{v}_eps` | `schmitt["eps"][-1]` | float32 | `NaN` |
| `{v}_pnl_long` | `long_pnl[-1]` | float32 | `100.0` |
| `{v}_pnl_short` | `short_pnl[-1]` | float32 | `100.0` |
| `{v}_long_pos` | `long_mask[-1]` | bool | `False` |
| `{v}_short_pos` | `short_mask[-1]` | bool | `False` |

**B. 需遍历查找的列（匹配 `view_last_idx` 或 `bar_index`）**：

| 列 | 来源 | 匹配条件 | Fallback |
|----|------|---------|----------|
| `{v}_trade` | `trade_records[].type` | `exit_idx == view_last_idx` | `""` |
| `{v}_trade_return` | `trade_records[].return_pct` | 同上 | `NaN` |
| `{v}_trade_reason` | `trade_records[].exit_reason` | 同上 | `""` |
| `{v}_bs_entry` | `bs_markers["entry_markers"][][1]` | `m[0] == view_last_idx` | `""` |
| `{v}_bs_exit` | `bs_markers["exit_markers"][][1]` | `m[0] == view_last_idx` | `""` |

> 详细提取伪代码见细化文档 `column-mapping.md`。

### 6.3 分段写入策略

**Flush 触发条件**（双条件，在 `append_row()` 中内联检查，无线程）：

1. 缓冲区行数 >= `buffer_size`（默认 100 行）
2. 距上次 flush 超过 `flush_interval_secs`（默认 30 秒）

**分段文件命名**：每次 flush 写入一个独立的 Parquet 分段文件，而非追加同一个文件的 row_group。这样崩溃时只有最后一个分段文件可能不完整：

```
{output_dir}/{ticker}_{session_id}/
├── part_0001.parquet    # bar 0~99
├── part_0002.parquet    # bar 100~199
├── ...
├── part_NNNN.parquet    # 最后一段
├── data.csv             # end_session 时从所有 part 合并导出
└── metadata.json
```

**单文件导出**：`end_session()` 中用 `pq.read_table("part_*.parquet")` 读取所有分段并合并写入最终 `data.parquet`，同时导出 CSV 副本。

**为什么分段而非追加 row_group**：pyarrow `ParquetWriter` 不支持 reopen 追加；保持一个 writer 长连接则在进程崩溃时 footer 缺失导致整个文件不可读。分段文件天然支持崩溃恢复——已关闭的分段文件是完整的。

### 6.4 集成点

`BacktestRunner.run()` **零变更**。`ParquetStore` 在 CLI `main()` 中与 `EventRecorder` 平级集成：

```python
# backtest_cli.py main() 中
store = None
if args.save_data:
    store = ParquetStore(args.output_dir, args.ticker, configs)
    store.start_session()

try:
    results = runner.run(start, end, args.step_interval)
    for step_idx, output in enumerate(results):
        recorder.record_step(step_idx, output["cutoff_date"], output)
        if store is not None:
            store.append_row(output)
finally:
    recorder.end_session()
    if store is not None:
        store.end_session()
```

**设计理由**：
- **开闭原则**：`run()` 是纯计算函数，不引入存储依赖
- **对称设计**：`EventRecorder` 和 `ParquetStore` 都是 CLI 层的"消费者"，地位平等
- **可选启用**：通过 `--save-data` flag 控制，不启用时零开销

### 6.5 依赖

新增显式依赖 `pyarrow>=14.0.0`。pandas 已间接依赖 pyarrow（`read_parquet` 默认引擎），新增不引入额外依赖链。`time`、`os`、`json`、`pathlib` 均为标准库。

---

## 7. 实施路径

### 7.1 最小改动方案

新增一个 `ParquetStore` 类，**不修改** `EventRecorder` 现有行为（保持向后兼容）。改动涉及以下文件：

| 文件 | 改动 | 状态 |
|------|------|------|
| 新增 `parquet_store.py` | 实现 `ParquetStore` 类（分段写入、原子 flush、CSV 导出、metadata 管理） | ✅ 已实现 |
| 修改 `backtest_cli.py` | 新增 `--save-data` 参数，run 循环中集成 `ParquetStore.append_row()` | ✅ 已实现 |
| 新增 `view_backtest.py` | 可视化工具：Parquet 读取 + HTML 嵌入 + 浏览器打开（含 HTTP 服务器模式） | ✅ 已实现 |
| `event_recorder.py` | CSVBuilder 扩展：新增 pnl/long_pos/short_pos/trade/trade_return/trade_reason 列 | ✅ 已实现 |

### 7.2 ParquetStore 接口

```python
class ParquetStore:
    def __init__(self, output_dir: str, ticker: str, session_id: str,
                 schema: dict, buffer_size: int = 100)
    def write_row(self, row: dict) -> None       # 追加一行到缓冲区
    def flush(self) -> None                       # 刷缓冲区 + 写 CSV + 写 metadata
    def close(self) -> None                       # 清理资源
```

### 7.3 BacktestRunner 集成点

在 `BacktestRunner.run()` 的循环体内，每步计算完成后：

```python
if self.parquet_store:
    self.parquet_store.write_row({
        "bar_index": bar_index,
        "bar_timestamp": bar_timestamp_ns,  # np.datetime64[ns]
        "v0_sig": views[0].signal,
        "v0_pnl_long": views[0].pnl_long,
        # ... 其余 48 列
    })
```

循环结束后调用 `self.parquet_store.flush()`。

### 7.4 文件大小估算

假设一个回测 session 有 10,000 个 min_tf bar（约 2 个月 5min 数据）：

- Parquet（ZSTD level=3）：50 列 x 10000 行，大量重复值（粗周期 + bool 列），预计 **~800KB-3MB**
- CSV：相同数据无压缩，预计 **~5-8MB**

### 7.5 验证标准

| 验证项 | 状态 |
|--------|------|
| Parquet 文件可用 `pd.read_parquet()` 加载且 schema 正确 | ✅ 已验证 |
| CSV 内容与 Parquet 一致（`pd.read_csv` vs `pd.read_parquet` 对比通过） | ✅ 已验证 |
| `metadata.json` 包含 session_id、ticker、时间范围、schema_version | ✅ 已验证 |
| `--save-data` 不传时，行为与现有完全一致（零影响） | ✅ 已验证 |
| BS marker 坐标匹配正确（使用 view_last_idx 而非 bar_index） | ✅ 已验证（v3.3 修复） |
| 分段文件在 `end_session` 后正确合并为 `backtest_result.parquet` 并清理 | ✅ 已验证 |
| 崩溃恢复：自动跳过损坏的分段文件 | ⏳ 待完善（当前仅清理 `.tmp` 残留） |

---

## 8. 边缘情况处理

以下 7 个场景从细化研究中提取，评级均为 🔴 必须处理。

### 8.1 中断恢复

**策略**：以 Parquet 实际行数为真相源，metadata.json 为缓存。

- 启动时遍历 output_dir，删除所有 `.tmp` 后缀的残留文件
- 对每个 `.parquet` 文件调用 `pq.read_metadata()` 校验 footer 完整性；无效的直接删除
- 加载 `metadata.json`，比较 `row_count` 与 parquet 实际行数；不一致时以 parquet 为准
- metadata.json 使用原子写入：先写 `.tmp`，再 `os.replace()` 原子 rename
- 分段文件设计（见 6.3 节）天然支持崩溃恢复——已关闭的分段文件是完整的

### 8.2 空视图 / 计算失败

| 场景 | 行为 |
|------|------|
| `filter_engine` 抛异常 | **中断回测**，打印完整 traceback + 视图名 + bar_index。核心计算异常不应被静默吞掉 |
| `compute()` 返回空 DataFrame（预热期） | 该视图 12 列填各自 Fallback 值（`0`/`NaN`/`False`/`""`），打印 WARNING 继续 |
| 视图配置错误 | **启动时校验**，回测开始前就检测并报错退出 |

### 8.3 列类型兼容

统一使用 pyarrow 引擎，明确定义 schema：

- `int8` → `pa.int8()`；`bool` → `pa.bool_()`；`float32` → `pa.float32()`；`str` → `pa.string()`
- `bar_timestamp` → `pa.timestamp("ns")`
- **禁止 fastparquet**（bool 列可能被读成 `object`，导致 `sum()` 等操作报错）
- `requirements.txt` 只列 `pyarrow>=14.0.0`，不列 `fastparquet`

### 8.4 时间戳格式

使用 Parquet 原生 `TIMESTAMP` 类型（`pa.timestamp("ns")`），纳秒精度。

- **不推荐字符串**：每次 DuckDB 查询需隐式 `strptime()` 转换，100 万行查询多耗时数秒
- **不推荐 Unix timestamp 数值**：单位不统一（秒/毫秒/纳秒），跨语言容易出错
- **Parquet TIMESTAMP**：类型安全、读写自动转换、DuckDB 谓词下推性能最优、跨语言标准统一

### 8.5 列名特殊字符

列名全部使用 ASCII（如 `v0_sig` 而非 `v0_日线_sig`）。

- 中文列名在 DuckDB 中需双引号引用（`"v0_日线_sig"`），体验差
- 部分 BI 工具（Tableau、Power BI）不支持中文列名
- pandas `.query()` 不支持中文列名（`df.query('v0_日线_sig == 1')` 报 SyntaxError）
- `metadata.json` 中通过 `view_labels` 提供中文映射（见 9.2 节）

### 8.6 Parquet 追加限制

**问题**：pyarrow `ParquetWriter` 的 footer 只在 `close()` 时写入。进程崩溃 → footer 缺失 → 整个文件不可读。且不支持 reopen 追加。

**方案**：分段文件（见 6.3 节）。每 `buffer_size` 行写一个独立 `.parquet` 文件，崩溃时最多丢失最后一个分段。最终导出时用 `pq.read_table("part_*.parquet")` 合并。

**避免**：每 bar 一个 row_group —— row_group 应有合理大小（>=100 行），否则元数据膨胀严重。

### 8.7 metadata 一致性

**原则**：Parquet 是真相源，metadata.json 是辅助缓存。

写入顺序：
```
1. 写入 part_NNNN.parquet（flush）
2. fsync 落盘
3. 写入 metadata.json.tmp
4. fsync 落盘
5. os.replace(.tmp, metadata.json)  ← 原子 rename
```

读取端校验：比较 `metadata.json` 中的 `row_count` 与所有 part 文件的 `pq.read_metadata().num_rows` 之和，不一致时以 parquet 为准。

metadata.json 中记录 `status` 字段：`"running"`（回测中）/ `"completed"`（正常结束）/ `"crashed"`（异常）。读取时发现 `status: "running"` 即知回测未正常结束。

### 8.8 CSVBuilder 修复记录（v3.3）

v3.3 中对 `EventRecorder.CSVBuilder` 进行了以下修复和扩展：

**BS 坐标系 bug 修复**：
- **问题**：早期实现使用全局 `bar_index` 匹配 BS marker 的坐标，导致几乎所有 marker 都无法命中（BS 列全为 `-`）
- **根因**：BS marker 的 `bar_idx` 是视图窗口内的相对索引（0..n_pts-1），而非全局 min_tf bar 序号
- **修复**：将匹配条件从 `int(m[0]) == bar_index` 改为 `int(m[0]) == view_last_idx`（其中 `view_last_idx = len(t_arr) - 1`）
- **影响范围**：`CSVBuilder._extract_view_columns()` 和 `ParquetStore._extract_view_columns()` 两处同步修复

**新增 6 列/视图（v3.3）**：

| 新增列 | 类型 | 含义 |
|--------|------|------|
| `{prefix}_pnl_long` | float | 做多累计 PnL 终值 |
| `{prefix}_pnl_short` | float | 做空累计 PnL 终值 |
| `{prefix}_long_pos` | int (0/1) | 是否在做多持仓中 |
| `{prefix}_short_pos` | int (0/1) | 是否在做空持仓中 |
| `{prefix}_trade` | str | 交易事件（`entry_long`/`exit_long`/`entry_short`/`exit_short`） |
| `{prefix}_trade_return` | float | 交易盈亏百分比 |
| `{prefix}_trade_reason` | str | 离场原因 |

**CSV 列数变化**：每视图从 11 列增至 17 列，总列数从 47 增至 73（含 5 列 OHLCV 基础数据 + 4 视图 x 17 列）。

> **注意**：CSVBuilder（EventRecorder 产出）与 ParquetStore 的列集不完全一致。CSVBuilder 额外包含 OHLCV 基础列（`close`/`open`/`high`/`low`/`volume`）和 Schmitt 内部状态列（`mu_v`/`sigma_v`/`sig_dur`/`pair_count`/`trade_count`），而 ParquetStore 只存储 50 列核心分析数据。两者定位不同：CSV 为人眼观察设计（包含行情上下文和调试信息），Parquet 为程序分析设计（只含必要的决策数据）。

---

## 9. 风险与注意事项

### 9.1 粗周期重复数据

粗周期信号在多个 min_tf 行上重复，这是正确行为而非缺陷。但在分析时需注意：不能对重复行直接做统计聚合（如 `value_counts` 会虚高）。正确做法是先取每个周期的切换点（`shift` 判断变化）再做统计。

### 9.2 Schema 版本化

`metadata.json` 内容：

```json
{
  "format_version": "1.0",
  "schema_version": "3.3",
  "status": "completed",
  "session_id": "20240716-143052",
  "ticker": "AAPL",
  "min_tf": "5min",
  "views": ["v0", "v1", "v2", "v3"],
  "view_labels": {
    "v0": "日线",
    "v1": "60分钟",
    "v2": "15分钟",
    "v3": "5分钟"
  },
  "start_time": "2024-01-02T09:30:00",
  "end_time": "2024-12-31T16:00:00",
  "bar_count": 10000,
  "parquet_row_count": 10000,
  "parquet_compression": "zstd",
  "parquet_compression_level": 3,
  "created_at": "2024-07-16T14:30:52",
  "column_names": ["bar_index", "bar_timestamp", "v0_sig", "v0_filtered", "..."],
  "view_configs": {
    "v0": {
      "filter_type": "kalman",
      "filter_params": {"delta": 1e-5, "R": 0.01},
      "schmitt_params": {"threshold_up": 0.5, "threshold_down": -0.5},
      "strategy_params": {"stop_loss": 0.05, "take_profit": 0.10}
    },
    "v1": {
      "filter_type": "butterworth",
      "filter_params": {"order": 4, "cutoff": 0.1},
      "schmitt_params": {"threshold_up": 0.3, "threshold_down": -0.3},
      "strategy_params": {"stop_loss": 0.03, "take_profit": 0.06}
    },
    "v2": {
      "filter_type": "kalman",
      "filter_params": {"delta": 1e-4, "R": 0.1},
      "schmitt_params": {"threshold_up": 0.2, "threshold_down": -0.2},
      "strategy_params": {"stop_loss": 0.02, "take_profit": 0.04}
    },
    "v3": {
      "filter_type": "ema",
      "filter_params": {"span": 20},
      "schmitt_params": {"threshold_up": 0.15, "threshold_down": -0.15},
      "strategy_params": {"stop_loss": 0.01, "take_profit": 0.02}
    }
  }
}
```

**新增字段说明（v3.3）**：
- `view_labels`：ASCII 列名到中文标签的映射，供下游 UI/分析工具展示中文名称
- `status`：会话状态标记（`"running"` / `"completed"` / `"crashed"`），读取方可据此判断数据完整性
- `parquet_row_count`：Parquet 实际行数（真相源），与 `bar_count` 对比可检测一致性
- `column_names`：全量列名列表，供动态 schema 读取
- `view_configs` 的键从 `v0_日线` 简化为 `v0`，与列名前缀一致

**必须存储 `view_configs`**：包含每个视图的滤波器类型及参数（`filter_type` + `filter_params`）、施密特参数（`schmitt_params`）和策略参数（`strategy_params`）。这些配置是回测结果可复现的前提——缺少任意一项，后续重新加载数据时无法还原图表或验证策略行为。

未来 schema 变更时递增 `schema_version`，读取方根据版本号选择解析逻辑。

### 9.3 内存与性能

- 行缓冲区（默认 100 行）限制内存占用；10 万行 session 也仅 ~1000 次 flush
- ZSTD compression_level=3 是速度与压缩率的平衡点（pandas 默认 level=1，3 略慢但体积更优）
- 如果 session 极长（>50 万 bar），考虑降级为只存 Parquet，不生成 CSV（CSV 写入会显著变慢）

### 9.4 向后兼容

- `--save-data` 默认 `False`，不影响现有 CLI 使用方式
- `EventRecorder` 保持不变，不修改其接口或行为
- 两个输出文件 + metadata.json 是纯增量，不覆盖任何已有文件

---

## 10. 可视化工具

### 10.1 view_backtest.py

`tools/view_backtest.py` 是一个独立的 Python 脚本，用于在浏览器中可视化回测 Parquet 数据。支持三种使用模式：

**模式 1：直接指定 Parquet 文件**

```bash
python tools/view_backtest.py backtest_output/AAPL_20240716-143052-AAPL/backtest_result.parquet
```

流程：读取 Parquet → 序列化为 JSON → 嵌入 HTML 模板 → 在浏览器中打开。自动探测同目录下的 `metadata.json` 以获取 `view_labels`（中文周期标签映射）。

**模式 2：自动找最新结果**

```bash
python tools/view_backtest.py --latest ./backtest_output
```

在指定目录下按文件修改时间排序，自动选择最新的 `backtest_result.parquet`。

**模式 3：HTTP 服务器模式（浏览器拖入）**

```bash
python tools/view_backtest.py --serve          # 默认端口 8899
python tools/view_backtest.py --serve --port 9090
```

启动本地 HTTP 服务器，浏览器自动打开页面。用户可将 `.parquet` 文件拖入页面进行可视化——无需命令行操作。服务器提供 `/api/parse` 端点解析上传的 Parquet 文件并返回 column-major JSON。

**可选参数**：

| 参数 | 含义 |
|------|------|
| `-m, --metadata` | 手动指定 metadata.json 路径 |
| `-t, --template` | 自定义 HTML 模板路径（默认 `docs/backtesting/回测结果可视化.html`） |

### 10.2 数据传递方式

`view_backtest.py` 使用 **column-major JSON 嵌入** 方式将数据传递给前端：

1. 用 pandas 读取 Parquet 文件
2. 将 DataFrame 转换为 `{列名: [值数组]}` 格式（column-major）
3. 时间戳列转换为 ISO 8601 字符串
4. NaN/Inf 值统一替换为 `null`
5. 注入到 HTML 模板的 `<script>` 标签中（`window.BACKTEST_DATA`）
6. HTML 通过 `file://` 协议或 HTTP 服务器在浏览器中打开

column-major 格式使前端 JavaScript 可以直接按列访问数据（如绘制 K 线图只需 `data.close` 和 `data.bar_timestamp`），无需逐行解包。

### 10.3 前端依赖

HTML 模板（`docs/backtesting/回测结果可视化.html`）自行管理前端依赖（如 Plotly.js、数据表格库等），`view_backtest.py` 只负责数据提取和注入，不引入额外 Python 依赖。

---

## 附录：变更记录

| 版本 | 日期 | 变更内容 |
|------|------|----------|
| v3.1 | 2024-07 | 初始设计：Parquet + CSV 双输出、分段写入、原子 flush |
| v3.2 | 2024-07 | 新增 `view_labels`、`status`、`column_names` 到 metadata.json；细化边缘情况 |
| v3.3 | 2024-07 | **实现完成**。修复 BS 坐标系 bug（bar_index → view_last_idx）；CSVBuilder 新增 6 列/视图（47→73 列）；补全 CLI 参数文档；新增可视化工具章节；新增坐标系说明；目录结构从两级嵌套简化为单级平铺；buffer_size 从设计 1000 调整为实际 100；schema_version 更新到 3.3 |
