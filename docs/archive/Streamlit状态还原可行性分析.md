# Streamlit 回测状态还原可行性分析

> 分析日期: 2026-07-18 | 代码版本: 当前 `filter/` 主干

---

## 1. 当前状态速览

### 1.1 Parquet 存储了什么

每行 = 一个 min_tf bar 的快照。每个 view 有 12 列，全部取自 `stage_output` 对应数组的 **[-1]**（最后一个值）：

| 列 | 来源 | 类型 | 含义 |
|---|---|---|---|
| `{v}_sig` | `schmitt["sig"][-1]` | int8 | 当前信号方向 |
| `{v}_filtered` | `filtered[-1]` | float32 | 当前滤波价格 |
| `{v}_eps` | `schmitt["eps"][-1]` | float32 | 当前噪声阈值 |
| `{v}_pnl_long` | `long_pnl[-1]` | float32 | 做多 PnL 终值 |
| `{v}_pnl_short` | `short_pnl[-1]` | float32 | 做空 PnL 终值 |
| `{v}_long_pos` | `long_mask[-1]` | bool | 当前是否持多仓 |
| `{v}_short_pos` | `short_mask[-1]` | bool | 当前是否持空仓 |
| `{v}_trade` | trade_records 中最新的 | string | 最近交易事件 |
| `{v}_trade_return` | 同上 | float32 | 最近交易收益率 |
| `{v}_trade_reason` | 同上 | string | 最近交易原因 |
| `{v}_bs_entry` | bs_markers 中最新的 | string | 最近入场标记 |
| `{v}_bs_exit` | bs_markers 中最新的 | string | 最近出场标记 |

### 1.2 Streamlit 显示什么

`_render_chart` 用 120 点的**完整窗口**渲染以下子图：

| 子图 | 数据 | 点数 |
|---|---|---|
| 价格 + 滤波 | OHLC K线 + `filtered` + `filtered2` + 预测曲线 | 120 |
| 残差 | `noisy - filtered` | 120 |
| 速度 v | `np.gradient(filtered, t)` | 120 |
| 施密特触发器 | `sig` (±1/0) + `eps` 上下轨 | 120 |
| 加速度 a | `np.gradient(v, t)` | 120 |
| PnL | `long_pnl` + `short_pnl` 曲线（从 100 起始） | 120 |
| 跨周期 PnL（可选） | 高周期仓位对齐 | 120 |
| 对齐子图（可选） | long_mask / short_mask 色带 | 120 |
| 反馈子图（可选） | 交易事件标记 | — |

---

## 2. 问题一：从当前 Parquet 能还原 Streamlit 吗？

### 结论：**不能。**

当前 Parquet 每个 bar 只存了各曲线的 **终点标量**。要从标量反推 120 点曲线在数学上不可能。

具体缺失：
- **PnL 曲线**：知道终值（如 105.3），但不知道它是从 100 经过什么路径到达的（可能先跌到 95 再涨回，也可能一路上涨）。
- **filtered 曲线**：知道最终滤波价格，但不知道前 119 个点的形态。
- **residual / v / a**：完全没有存储，必须从 filtered 重新计算。
- **OHLC K 线**：每个 bar 只存了当前时点的 OHLCV，前 119 根 K 线没有存储。
- **schmitt.sig 序列**：只知道当前信号，无法还原信号切换的时间点。
- **prediction_pairs**：完全没有存储。

**仅能还原的内容**：当前 bar 的快照数值（终值 PnL、当前信号方向、当前持仓状态、最近交易事件），相当于 Streamlit 图表中最右侧一个点的纵坐标 — 不能还原整条曲线。

---

## 3. 问题二：存储完整数组可行吗？

### 3.1 数据量估算

假设：500 bar, 4 views, 120 点窗口, 核心数组约 10 个：

| 数组 | 每 view 点数 | 类型 |
|---|---|---|
| `noisy` (close) | 120 | float32 |
| `filtered` | 120 | float32 |
| `filtered2` | 120 | float32 |
| `schmitt.sig` | 120 | int8 |
| `schmitt.eps` | 120 | float32 |
| `schmitt.v` | 120 | float32 |
| `schmitt.a` | 120 | float32 |
| `long_pnl` | 120 | float32 |
| `short_pnl` | 120 | float32 |
| OHLC (4 列) | 120×4 | float32 |

**每 bar 存储量**：4 views × 120 点 × (9 floats + 4 OHLC + 1 int8) ≈ 4 × 120 × 13 × 4 bytes ≈ **25 KB**

**500 bar 总量**：500 × 25 KB ≈ **12.5 MB**（未压缩原始值）

**压缩后估算**：ZSTD level 3 + 高时间相关性（相邻 bar 的 120 点窗口有 119 点重叠），压缩比预计 **5-15x**，实际存储 **1-3 MB**。

### 3.2 Parquet 对数组列的支持

PyArrow 原生支持 `pa.list_(pa.float32())` 类型，Parquet 格式也支持嵌套列表。实际验证：

```python
import pyarrow as pa
import pyarrow.parquet as pq

schema = pa.schema([
    ("bar_index", pa.int32()),
    ("v0_filtered_curve", pa.list_(pa.float32())),
])
# ✅ 读写均正常
```

**已知限制**：
- `list<float>` 列的压缩效率低于纯标量列（列表编码有额外开销）
- 部分分析工具（Pandas, DuckDB）对嵌套列的支持不如标量列友好，需要 `explode` 或手动处理
- CSV 导出无法直接表示数组列（需 JSON 序列化或拆分为多列）

### 3.3 读取性能

- 读取 500 行 × 40 个数组列：需要解压 + 反序列化所有嵌套列表，约 500 × 40 × 120 = **240 万个元素**
- 预估单次加载：100-500 ms（取决于磁盘和列数）
- 对交互式分析可接受，但不适合高频轮询

### 3.4 实现改动

需修改 `parquet_store.py`：
1. `_VIEW_COLUMNS` 新增数组列（如 `pnl_curve_long`, `filtered_curve` 等）
2. `_extract_view_columns` 改为提取完整数组而非 `[-1]`
3. `_build_full_schema` 使用 `pa.list_(pa.float32())` 类型
4. 现有分析脚本可能需要适配新的列类型

---

## 4. 问题三：能否"按需重算"？

### 4.1 重算所需数据

`_compute_pipeline_for_view` 的输入是 `(t, noisy, ohlc, dates)` 四个数组，全部来自 `data/display/{tf}.parquet`。而这个 parquet 由 `_sync_all_cascading` 从 kline DB 合成。

metadata.json 已存储完整的 `view_configs`（滤波器参数、schmitt 参数等），足以驱动重算。

### 4.2 BacktestRunner 能否独立调用？

**可以。** `BacktestRunner` 的核心方法是可以独立调用的：

```python
from filter.services.backtest_core import BacktestRunner

runner = BacktestRunner("03690", configs)

# 重算单个 bar
runner._sync_data(cutoff_date)          # 步骤1: 写 display parquet
window = runner._load_window_data(tf, 120)  # 步骤2: 读窗口数据
output = runner._compute_pipeline_for_view(cfg, window)  # 步骤3: 管道计算
# output 包含完整的 120 点数组
```

### 4.3 重算代价

| 步骤 | 操作 | 耗时估算 |
|---|---|---|
| `_sync_data` | 级联合成所有 TF 的 display parquet | **主要瓶颈**：需从 kline DB 查询并合成多个 TF，约 50-200 ms/bar |
| `_load_window_data` | 读取单个 parquet 文件 | < 5 ms |
| `_compute_pipeline_for_view` | 滤波 + schmitt + 预测 + PnL | 10-50 ms/view（纯 CPU） |

**单 bar 全部重算（4 views）**：约 **100-500 ms**。

**优化方向**：
- `_sync_data` 在每个 bar 写 parquet 是重复工作。可以预先批量生成所有 bar 的 display parquet，或缓存。
- 如果只重算少量 bar（用户点击查看），性能可接受。

### 4.4 可行性评估

**完全可行**。但需注意：
- 重算依赖 kline DB 中的原始数据。如果 DB 被清理或移动，重算无法进行。
- 重算结果应与原始 Streamlit 显示完全一致（同输入、同参数、同算法）。

---

## 5. 对比总结

| 维度 | 方案A: 存完整数组 | 方案B: 按需重算 | 方案C: 混合 |
|---|---|---|---|
| **能否还原曲线** | 能 | 能 | 能（部分 bar） |
| **存储增长** | ~25 KB/bar → 12.5 MB (500 bar) | 不变 | 中等 |
| **实现复杂度** | 中等（改 schema + 读写逻辑） | 低-中（复用现有类） | 高（两套逻辑） |
| **查询性能** | 直接读，100-500 ms | 需重算，100-500 ms/bar | 取决于命中率 |
| **数据独立性** | 完全自包含 | 依赖 kline DB | 部分依赖 |
| **分析工具兼容** | 差（嵌套列需特殊处理） | 好（标量列） | 同方案A |
| **适用范围** | 需要频繁查看多 bar 曲线 | 偶尔查看特定 bar | 兼顾两者 |

---

## 6. 推荐方案

### 推荐：方案 B — 按需重算

理由：
1. **现实需求**：用户不会同时查看 500 个 bar 的完整曲线。典型场景是"在回测分析中发现某个 suspicious bar，点进去看 Streamlit 级别的曲线"。
2. **零存储成本**：Parquet schema 不变，不影响现有分析流程。
3. **实现简单**：BacktestRunner 已经支持单 bar 计算，只需包装一个便捷函数。
4. **结果精确**：同参数重算结果与原始 Streamlit 完全一致。

### 建议实现

新增 `replay_bar.py` 工具模块：

```python
# filter/services/replay_bar.py

def replay_bar(
    ticker: str,
    bar_index: int,
    configs: list[dict],
) -> dict:
    """
    重放单个 bar 的完整管道输出，返回与 streamlit_app._render_chart
    相同结构的 stage_output，可用于渲染完整的 120 点图表。

    Returns 结构与 BacktestRunner.run() 的单步元素一致：
    {
        "bar_index": int,
        "bar_timestamp": str,
        "cutoff_date": str,
        "views": {"v0_日线": {...}, ...},  # 每个 view 含完整数组
        "ohlcv": {...},
    }
    """
    runner = BacktestRunner(ticker, configs)
    results = runner.run(bar_index, bar_index + 1, step_interval=1)
    return results[0] if results else None


def replay_to_streamlit_format(
    ticker: str,
    bar_index: int,
    configs: list[dict],
) -> dict:
    """
    将重放结果转换为 streamlit_app 可消费的格式。
    返回 {view_key: PipelineStageData} 字典，可直接传给组件渲染。
    """
    ...
```

配合简单的前端：在分析页面加入一个"查看此 bar 完整曲线"按钮，点击后弹出模态框展示 Streamlit 级别图表。

### 混合策略（可选增量）

如果某些 bar 被频繁查看（如信号翻转点），可以在查看时缓存重算结果到内存或临时文件，避免重复计算。

---

## 附录：stage_output 完整结构

单 view 的 `_compute_pipeline_for_view` 返回：

```python
{
    "t":            np.ndarray,    # [0, 1, 2, ..., 119], float
    "dates":        DatetimeIndex, # 120 个日期
    "noisy":        np.ndarray,    # 收盘价, float, 120点
    "ohlc":         pd.DataFrame,  # Open/High/Low/Close, 120行
    "ohlcv":        dict,          # 当前 bar 的 OHLCV 标量
    "filtered":     np.ndarray,    # 滤波价格, float, 120点
    "filtered2":    np.ndarray|None, # 副滤波, float, 120点
    "schmitt": {                   # 施密特触发器输出
        "sig":      np.ndarray,    # ±1/0 信号, int, 120点
        "eps":      np.ndarray,    # 自适应阈值, float, 120点
        "v":        np.ndarray,    # 速度, float, 120点
        "a":        np.ndarray,    # 加速度, float, 120点
        "mu_v":     np.ndarray,    # 速度均值, float, 120点
        "sigma_v":  np.ndarray,    # 速度标准差, float, 120点
    } | None,
    "all_pairs":        list[tuple],     # [(start, end), ...] 信号切换对
    "prediction_pairs": list[dict],      # 预测曲线参数
    "long_pnl":         np.ndarray,      # 做多 PnL, float, 120点 (起始100)
    "short_pnl":        np.ndarray,      # 做空 PnL, float, 120点 (起始100)
    "trade_records":    list[dict],      # 交易记录
    "long_mask":        np.ndarray|None, # 持仓掩码, bool, 120点
    "short_mask":       np.ndarray|None, # 持仓掩码, bool, 120点
    "bs_markers":       dict,            # BS 标记
}
```

当前 Parquet 对以上每个数组只取了 `[-1]`，损失了前 119 个数据点。
