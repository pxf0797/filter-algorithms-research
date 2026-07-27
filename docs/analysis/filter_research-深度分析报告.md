# filter_research 综合研究分析报告

> **生成日期**: 2026-07-27
> **项目路径**: `/Users/xfpan/claude/filter_research`
> **分析范围**: 浏览模式 + 回测模式 + 数据层 + 测试套件 + 评估工具
> **目标读者**: 使用该工具研究单支股票的量化交易者

---

## 执行摘要

**filter_research** 是一套基于多时间帧滤波器和 Schmitt 触发器加速度信号的量化趋势分析工具，通过 Streamlit Web UI 和 CLI 双入口提供从数据获取、趋势研判、策略回测到绩效评估的完整链路。项目总代码量 57,328 行 Python，包含 2,413 个测试用例（已执行 760 个，0 失败），本地数据库存储 141,772 行 K 线数据覆盖 13 个标的。

**三项核心发现**：

1. **加速度信号优于价格交叉**。Schmitt 触发器不直接判断价格突破阈值，而是监测滤波曲线加速度 `a`（二阶导数）是否突破自适应死区（死区宽度 = `k_eps x EWMA波动率`），入场需 `v` 和 `a` 同向。这比传统均线交叉提前数根 bar 捕捉反转，同时自适应死区在震荡期自动放宽过滤噪声。

2. **10 滤波器 x 4 时间帧共识是可靠性锚点**。单一滤波器 + 单时间帧信号不可靠，但当 SMA/Kalman/Savitzky-Golay/Butterworth 等不同算法在同一时刻、同一方向同时出现加速度突破——且粗周期方向约束细周期——信号质量大幅提升。10 滤波器对比工具的 Pareto 前沿分析可将过拟合风险可视化。

3. **组合收益曲线 `combined_pnl = max(long, short)` 不可实盘实现**，策略不建模交易成本与滑点，30-bar 窗口统计无意义。实盘前必须选择单向（只做多或只做空），并预期实际收益打 6-7 折。

**对用户的核心建议**：用日线 Kalman + 60m SMA 做方向判断（粗周期约束），用 Schmitt 触发器在 15m 等入场帧捕捉加速度突破信号，严格按 13 步 Go/No-Go 检查清单裁决每一支标的，绝不在测试集上看到结果后回头调参。

---

## 第一章：工程架构总览

### 1.1 技术栈

| 层级 | 技术 | 用途 |
|------|------|------|
| 数据源 | yfinance 1.4.1 | 美股/港股/A股多周期 K 线拉取 |
| 持久层 | SQLite + WAL 模式 + 256MB mmap | 权威数据源，141K 行，0 空值 |
| 展示缓存 | Parquet (PyArrow) + 按月分区 | 列存储读取，Streamlit 快速渲染 |
| 计算引擎 | NumPy 2.2.4 + SciPy 1.17.1 + Numba 0.64.0 | 滤波器计算、Schmitt 触发器、16 项指标加速 |
| Web UI | Streamlit 1.57.0 + Plotly 6.7.0 | 4 视图网格、交互式图表、仪表盘 |
| CLI | argparse + loguru | 批量回测、断点续跑 |
| 测试 | pytest 9.0.2 | 2,413 测试（已执行 760） |

### 1.2 五包子域架构

```
filter_research/
├── filter/shared/          # 全局常量(ALL_TFS)、配置管理、会话状态
│   ├── constants.py        # 时间帧层次定义
│   ├── config.py           # 配置序列化
│   └── state.py            # AppState 会话状态
├── filter/data/            # 数据子域（yfinance→SQLite→Parquet 链路）
│   ├── fetcher.py (254行)   # yfinance 下载 + 清洗 + 日线延迟缓解
│   ├── db.py      (712行)   # SQLite 线程安全连接池 + CRUD
│   ├── synth.py   (656行)   # 级联合成（细→粗，时区感知）
│   ├── loader.py  (369行)   # Parquet 展示缓存 + 版本校验 + 分区路径
│   └── store.py   (1111行)  # 双缓存协调器（SQLite→Parquet 写入策略）
├── filter/engine/          # 计算引擎子域（纯函数，零UI依赖）
│   ├── filters.py (471行)   # 10 种滤波器 + FILTERS 注册表
│   ├── schmitt.py (309行)   # Schmitt 触发器核心 + 抛物线预测
│   ├── strategy.py(266行)   # 策略 PnL 双阶段模型
│   ├── alignment.py         # 跨周期 PnL 对齐
│   ├── signals.py           # 买卖标记生成
│   └── pipeline.py          # 管道编排（滤波→Schmitt→预测→策略）
├── filter/browse/          # Streamlit 浏览模式
│   ├── app.py     (943行)   # 4 视图网格入口 + 图表编排
│   ├── charts.py            # Plotly 多层子图渲染
│   ├── sidebar.py           # 侧边栏 UI 控件
│   └── chart_builder.py     # 图表构建器
└── filter/backtest/        # 回测模式（CLI + 仪表盘）
    ├── engine.py  (1145行)   # BacktestRunner 逐 bar 滑动窗口编排
    ├── cli.py     (598行)    # argparse CLI + 10 预设 + 断点续跑
    ├── metrics.py (217行)    # Numba 加速的 16 项金融指标
    ├── dashboard.py(495行)   # Plotly 交互式 HTML 仪表盘
    ├── recorder.py           # 事件记录与序列化
    └── catalog.py            # 回测结果目录管理
```

### 1.3 Web/CLI 统一引擎的设计亮点

**统一的 BacktestRunner**（`filter/backtest/engine.py::BacktestRunner`）是 Web 和 CLI 共享的计算核心。浏览模式的实时图表和回测模式的历史滑动窗口都调用同一套管道函数：

```python
# filter/engine/pipeline.py 提供3个核心函数供 Web 和 CLI 共同调用
from filter.engine.pipeline import compute_filters, compute_schmitt_trigger, compute_prediction_pairs
```

这保证了你在浏览模式看到的信号与回测模式计算的结果完全一致——不是"浏览看一套，回测算另一套"。

**设计要点**：
- `BacktestRunner.run(start_bar, end_bar, step_interval=1)` 逐 bar 推进滑动窗口，每个 bar 重建所有时间帧的数据视图（级联合成）
- 多视图使用 `ThreadPoolExecutor` 并行计算（`engine.py:run` 方法）
- EWMA 状态（`mu_v`, `sigma_v`, `final_state`）跨窗口传递，避免滑动窗口边界信号跳变
- 支持断点续跑：`checkpoint_interval=100` 参数每 100 bar 保存中间状态

### 1.4 关键数字一览

| 指标 | 值 |
|------|----|
| Python 总代码行数 | 57,328 |
| 测试用例总数 | 2,413 |
| 已执行测试 | 760 (全部通过) |
| 数据库总行数 | 141,772 |
| 数据库标的数 | 13 |
| 支持时间帧数 | 8 (1m/5m/15m/60m/1d/1wk/1mo/1Q) |
| 数据库空值数 | **0** |
| 回测历史 Session | 132 |
| 覆盖标的 | 5 (2382/3690/600115/AAPL/MSFT) |
| 滤波器数量 | 10 |
| 金融指标数量 | 16 |
| 预设配置数量 | 10 |
| 核心依赖数量 | 8 |

---

## 第二章：浏览模式 -- 实时趋势分析

浏览模式是研究单支股票的"驾驶舱"。通过 Streamlit 启动 4 视图网格，实时观察价格走势、滤波器轨迹、Schmitt 信号和策略 PnL。

### 2.1 启动命令

```bash
cd /Users/xfpan/claude/filter_research
streamlit run filter/browse/app.py
# 浏览器自动打开 http://localhost:8501
```

### 2.2 10 种滤波器对比

所有滤波器遵循统一签名 `filter_func(signal: np.ndarray, t: np.ndarray, **params) -> np.ndarray`，通过 `FILTERS` 注册表管理（`filter/engine/filters.py`）。

| 滤波器 | 原理 | 延迟(样本) | 适用场景 | 关键参数 |
|--------|------|-----------|---------|---------|
| **SMA** | 等权滑动窗口平均 | 5 | 长周期定大方向，简单可靠无过拟合 | `window` (奇数强制) |
| **EMA** | 指数衰减权重，最新数据权重最大 | 4 | 快速追踪近期变化，适合入场帧 | `span` (α=2/(span+1)) |
| **WMA** | 线性递增权重 | 5 | 折中方案，比SMA灵敏比EMA稳健 | `window` |
| **ALMA** | 高斯中心偏移，offset=0.85大幅降低延迟 | 16 | **最低延迟**，适合精确执行帧 | `window`, `offset`, `sigma` |
| **Savitzky-Golay** | 局部多项式拟合，保留转折形态 | 5 | 验证其他滤波器是否平滑掉了关键转折 | `window_length`, `polyorder` |
| **Kalman** | 状态空间估计，同时建模位置+速度 | 1 | **定大方向首选**，最稳健 | `Q`(过程噪声), `R`(观测噪声) |
| **Butterworth** | 频域低通滤波，零相位(sosfiltfilt) | 0 | 离线分析，无延迟但需全序列 | `order`, `cutoff` |
| **Gaussian** | 高斯核平滑(scipy.ndimage) | 0 | 通用平滑，物理直觉好 | `sigma` |
| **Median** | 中值滤波，异常值免疫 | 未测 | 排除毛刺假信号，排除极端值 | `kernel_size` |
| **LOWESS** | 局部加权散点平滑(statsmodels) | 未测 | 非线性稳健趋势，验证过拟合 | `frac`(数据比例) |

**延迟数据来源**：`tools/filter_comparison_tool.py` 阶跃响应法测量。SMA(5)/EMA(4)/WMA(5)/ALMA(16)/SavGol(5)/Kalman(1)/Butterworth(0)/Gaussian(0)。

**关键理解**：延迟不是越短越好。Kalman 延迟 1 但内部使用随机噪声初始化（每次运行略有差异）；Butterworth 延迟 0 但使用 `sosfiltfilt` 双向滤波，在全序列已知时才可计算，不适合实时流式场景。**实时盘中分析时优先 ALMA（确定性的最低延迟），离线历史分析时优先 Kalman。**

### 2.3 Schmitt 触发器：加速度突破 + 自适应死区

Schmitt 触发器是整个工具**最核心的创新点**，实现在 `filter/engine/schmitt.py` 的 `_schmitt_trigger()` 函数中（309 行，Numba 加速）。

**工作原理（三步）**：

**第一步：计算速度 v 和加速度 a**

```python
# 伪代码，实际在 pipeline.py 中计算
v  = np.gradient(filtered)     # 一阶导数 = 趋势速度
a  = np.gradient(v)            # 二阶导数 = 趋势加速度
```

**第二步：计算自适应死区**

```python
# schmitt.py:122-131
alpha = 2.0 / (ewma_span + 1)
sigma_v = sqrt(EWMA((v - EWMA(v))^2))   # v 的 EWMA 波动率
eps_t   = k_eps * max(sigma_v, sigma_min)  # 自适应死区
```

死区宽度随市场波动自动调整：高波动时放宽（避免被噪声震出），低波动时收窄（保持灵敏度）。`sigma_min` 是地板保护，防止死区过小时频繁触发。

**第三步：滞回状态机判断信号**

```python
# schmitt.py:138-165 (简化逻辑)
if state == 0:                    # 观望态 → 入场
    if a > eps AND v > 0:  state = +1   # 多头入场：a和v必须同向
    if a < -eps AND v < 0: state = -1   # 空头入场：a和v必须同向
elif state == +1:                 # 多头态 → 离场
    if a < -eps: state = 0        # 只需要a反转即可离场
elif state == -1:                 # 空头态 → 离场
    if a > eps: state = 0         # 只需要a反转即可离场
```

**关键设计决策**：

- **入场需 v 和 a 同向**：避免在趋势减速期（a已反转但v还在惯性方向）误入场。这过滤了"末班车"式的假信号。
- **离场只需 a 反转**：不需要等 v 也反转（那会晚好几根 bar）。加速度是价格变化的加速度，最先反映趋势力竭。
- **滞回特性**：经典的 Schmitt 触发器特性——上阈值（入场）和下阈值（离场实际上是 a 反号）不同，避免在阈值附近频繁翻转。

**参数调优指南**：

| 参数 | 数据范围 | 推荐粗周期值 | 推荐细周期值 | 调大效果 |
|------|---------|------------|------------|---------|
| `k_eps` | 0.5~3.0 | 2.0(日线) | 1.0(5m) | 死区更宽=信号更少更可靠 |
| `ewma_span` | 20~120 | 60 | 40 | 波动率估计更慢=死区更稳定 |
| `sigma_min` | 0.01~0.10 | 0.05 | 0.03 | 地板更高=防止死区过小 |

k_eps 从粗到细递减的原则：`日线 2.0 → 60m 1.5 → 15m 1.2 → 5m 1.0`。

### 2.4 4 视图网格与跨周期分析

4 个视图各自独立配置（时间帧 + 滤波器 + Schmitt 参数），形成从宏观到微观的决策金字塔：

```
┌─────────────────────┬──────────────────────┐
│ 视图1: 日线 (1d)     │ 视图2: 60分 (60m)    │
│ 滤波器: Kalman       │ 滤波器: SMA(20)       │
│ k_eps: 2.0           │ k_eps: 1.5            │
│ 作用: 定战略方向      │ 作用: 验证趋势健康度   │
├─────────────────────┼──────────────────────┤
│ 视图3: 15分 (15m)    │ 视图4: 5分 (5m)      │
│ 滤波器: EMA(12)       │ 滤波器: ALMA          │
│ k_eps: 1.2           │ k_eps: 1.0            │
│ 作用: 等入场信号      │ 作用: 精确执行         │
└─────────────────────┴──────────────────────┘
```

**跨周期功能**（实现在 `alignment.py` + `app.py` 图表渲染层）：

1. **跨周期 PnL 对齐**：粗周期的 PnL 事件标记映射到细周期子图，粗→细前向填充。比对两周期交易信号在时间轴上的一致性。
2. **同向性过滤**：粗周期持仓时，细周期同向 PnL 才计入子图，反向区域置灰。这模拟了"大周期定方向，小周期只在同向时交易"的真实约束。

### 2.5 实战：三步快速判断趋势状态

**步骤 1（30 秒）：裸 K 线观察**

在 browse 模式选择日线，滤波器选"None"，只需回答三个问题：
- 当前价格在过去 1 年的什么位置？（高位/中位/低位）
- 有明显的上升/下降/横盘吗？（肉眼可辨）
- 日均波幅大概多少？

**No-Go 标准**：如果肉眼完全看不出趋势特征（随机游走形态），不适合趋势跟踪策略。

**步骤 2（2 分钟）：4 滤波器快速扫描**

4 个视图都设为日线，滤波器分别为 SMA(20)/Kalman(Q=0.01,R=0.1)/LOWESS/Savitzky-Golay(15,3)：

- 4 个滤波器方向是否大致一致？（至少 3/4 同向）
- 不一致 → 震荡市，放弃或等待。

**步骤 3（5 分钟）：4 视图多周期研判**

按 2.4 节的布局设置，开启 Schmitt 触发器。观察：
- 日线滤波器斜率在过去 30 bar 是否持续单向？
- Schmitt 信号密度：每 10-20 bar 一个 = 健康；每 3-5 bar 翻转 = 震荡。
- 60m 与日线方向一致性 > 80%？

---

## 第三章：回测模式 -- 策略验证引擎

### 3.1 BacktestRunner 完整执行流程

`filter/backtest/engine.py::BacktestRunner` (1145 行) 是回测的核心编排器，零 Streamlit 依赖，Web 和 CLI 共用。

**执行流程（每个 bar 索引 i）**：

```
for i in range(start_bar, end_bar, step_interval):
    ┌─ 1. 数据同步 ─────────────────────────────────┐
    │ _sync_all_cascading():                         │
    │   从 DB 读最细粒度原始数据                       │
    │   synth.py 级联合成所有粗粒度时间帧              │
    │   时区感知转换                                  │
    ├─ 2. 窗口加载 ─────────────────────────────────┤
    │ load_display_cache():                           │
    │   从 Parquet 缓存读取 [i-n_pts, i] 滑动窗口     │
    │   若无缓存则回退 DB 查询                        │
    ├─ 3. 管道计算 ─────────────────────────────────┤
    │ compute_filters(filtered, t, filter_id, params) │
    │   → 10种滤波器任选，返回 smoothed 序列           │
    │ compute_schmitt_trigger(v, a, ewma_span, ...)   │
    │   → EWMA波动率→自适应死区→滞回状态机→±1/0信号   │
    │ compute_prediction_pairs(filtered, sig_t, ...)   │
    │   → 物理抛物线拟合当前点为顶点，预测回落路径     │
    │ _compute_strategy_pnl(...)                      │
    │   → 双阶段：保护期(止损+止盈)+趋势跟踪期(仅止盈) │
    ├─ 4. 跨视图分发 (ThreadPoolExecutor) ───────────┤
    │   对每个视图配置重复步骤3，并行计算               │
    ├─ 5. 事件记录 ─────────────────────────────────┤
    │ EventRecorder 收集所有中间输出                   │
    │ 周期 checkpoint_interval=100 保存 checkpoint    │
    └────────────────────────────────────────────────┘
```

**关键代码片段**（engine.py 滑动窗口推进）：

```python
# engine.py: BacktestRunner.run()
for i in range(start_bar, end_bar + 1, step_interval):
    start = max(0, i - n_pts + 1)
    window = df.iloc[start : i + 1]
    # 对每个视图独立运行管道
    with ThreadPoolExecutor(max_workers=len(configs)) as executor:
        futures = {executor.submit(_run_pipeline, cfg, window): cfg for cfg in configs}
```

**EWMA 状态跨窗口传递**：Schmitt 触发器的 EWMA 均值和标准差（`final_mu`, `final_sigma`）以及施密特状态（`final_state`, `final_dur`）从一个滑动窗口传递到下一个。这避免了在每个新窗口开头重新初始化 EWMA 导致的边界信号跳变。

**断点续跑**：`--resume PATH` 参数从 checkpoint 文件恢复状态继续运行。

### 3.2 CLI 参数体系与预设配置

**CLI 命令**（`filter/backtest/cli.py`，598 行）：

```bash
python -m filter.backtest_cli --ticker 03690.HK --preset 3690_HK_DP
```

**完整参数列表**：

| 参数 | 必需 | 说明 |
|------|------|------|
| `--ticker` | 是 | 股票代码（如 `03690.HK`, `AAPL`） |
| `--preset` | 二选一 | 预设配置名称（从 config_db 加载） |
| `--config-file` | 二选一 | JSON 配置文件路径 |
| `--start-bar` | 否 | 起始 bar 索引（默认 0） |
| `--end-bar` | 否 | 结束 bar 索引（默认数据末尾） |
| `--step-interval` | 否 | 步进间隔（默认 1，变步长回测不可靠） |
| `--resume` | 否 | 从 checkpoint 恢复 |
| `--checkpoint-interval` | 否 | checkpoint 间隔 bar 数（默认 100） |
| `--view-filter` | 否 | 只跑指定视图（如 `v0_日线`） |
| `--output-dir` | 否 | 输出目录 |
| `--no-save-data` | 否 | 不保存回测数据 |
| `--quiet` | 否 | 静默模式 |
| `--debug` | 否 | 调试模式 |

**10 个预设配置**：分"单滤波"和"双滤波"两类，覆盖港股/A股/美股。每个预设包含完整的 4 视图参数（滤波器类型/参数/Schmitt k_eps/止损%/保护期 bar 数）。

### 3.3 策略 PnL 的双阶段模型

实现在 `filter/engine/strategy.py::_compute_strategy_pnl()`（266 行），采用分段混合方案：

**阶段 1：保护期**（入场后第 1 到 `n_extend` 根 bar）

```python
# strategy.py:112-130 (简化逻辑)
if i <= protect_end:
    if has_prediction:
        stop_hit = price < pred_val * (1 - stop_loss_pct/100)  # 止损基于预测轨道
    else:
        stop_hit = price < entry_price * (1 - stop_loss_pct/100)  # 止损基于入场价
    # 同时检查信号反转止盈
```

- 止损：基于物理抛物线预测轨道或固定百分比
- 止盈：信号反转（Schmitt sig_t 反向）
- 保护期长度 = `n_extend` 参数（预测延伸点数）

**阶段 2：趋势跟踪期**（保护期结束后到离场）

```python
# strategy.py:133-140
# 仅在信号反转时止盈，让利润奔跑
if is_long and sig_t[i] == -1:   exit("take_profit")
if is_short and sig_t[i] == 1:   exit("take_profit")
```

- 无止损保护：信任趋势
- 仅信号反转止盈：不预设盈利目标

**物理抛物线预测**（`schmitt.py` 抛物线拟合函数）：

不同于传统自由 3 参数抛物线，本工具锚定当前点为顶点，仅拟合曲率 `a`。这大幅减少了自由度（从 3 个到 1 个），使预测更稳健。抛物线方程：

```
f(x) = a*(x - x0)^2 + y0     # x0,y0 为当前点坐标（锚定为顶点）
```

预测提供对称回落路径参考，用于保护期阶段判断止损触发，而非趋势跟踪期的盈利目标。

### 3.4 关键局限与应对建议

**局限 1：无交易成本/滑点建模**

`strategy.py` 中的 PnL 计算直接使用滤波价格（`entry_price = filtered[entry_idx]`），不扣除手续费和滑点。实盘中尤其港股印花税 0.13%（买卖双边）+ 平台费，高频策略影响显著。

**应对**：实盘预期收益 = 回测收益 x 0.6~0.7。聚焦日线和 60m 级别策略（交易频率低，成本影响小），避免依赖 5m/15m 高频信号。

**局限 2：combined_pnl = max(long, short) 不可实盘**

```python
# common/pnl_renderer.py: compute_combined_pnl()
combined_pnl = np.maximum(long_pnl, short_pnl)  # 取每个时点多空最优
```

这意味着回测报告的 "combined" 曲线假设你**同时**在做多和做空两条独立的持仓，且取较优的那个。这在实盘中不可能——你不可能在同一支标的上同时持有多头和空头头寸（除非用期权组合）。

**应对**：关注 `long_pnl` 和 `short_pnl` 各自的指标，不要使用 `combined` 做决策。实盘中明确选择只做多或只做空。

**局限 3：窗口大小与统计意义**

30-bar 窗口的 Sharpe/Sortino 等指标完全没有统计意义。测试报告明确指出"需 >= 500 bar"。2 年日线数据（约 500 个交易日）是最低要求。

**应对**：CLI 回测时使用 `--start-bar` 和 `--end-bar` 确保窗口 >= 500 bar。仪表盘中忽略短窗口的指标值。

**局限 4：粗周期信号投影伪相关**

当粗周期（如日线）的 PnL 事件通过跨周期对齐映射到细周期（如 5m）时，日线级别的稀疏信号在 5m 大量 bar 上产生幻觉式的"相关"。

**应对**：跨周期对齐视图仅作参考，不以细周期信号密度评估粗周期的效果。

---

## 第四章：CLI 数据读取 -- 数据基础设施

### 4.1 yfinance -> SQLite -> Parquet 完整数据链路

数据从 yfinance API 到用户屏幕经历了三个环节：

```
yfinance API ──fetcher.py──> SQLite (market.db) ──store.py──> Parquet (display/)
                                    │                              │
                                    │ query_kline()                │ load_display_cache()
                                    ▼                              ▼
                              命令行/批量查询                   Streamlit UI 渲染
```

**环节 1：fetcher.py (254 行) -- yfinance 下载与清洗**

```python
# fetcher.py:191-206 — 日线 Close 延迟缓解
# 港股/A股日线数据通常延迟1天。fetcher尝试用周线API回退：
# 如果日线最新 bar 与周线最新 bar 不同日，用周线最后一天修正日线
```

处理了 8 个时间帧（1m/5m/15m/60m/1d/1wk/1mo/1Q），使用 `yfinance.download()` 的 `interval` 参数。

**写入策略**：
- 历史 bar（非最新）→ `INSERT OR IGNORE`（不覆盖已有数据）
- 最新 bar → `INSERT OR REPLACE`（允许覆盖修正延迟数据）

**环节 2：db.py (712 行) -- SQLite 数据层**

```python
# db.py:55-59 — 线程安全连接池
conn = sqlite3.connect(current_path)
conn.execute("PRAGMA journal_mode=WAL")          # 写不阻塞读
conn.execute("PRAGMA synchronous=NORMAL")         # 平衡安全与性能
conn.execute("PRAGMA busy_timeout=5000")          # 5秒忙碌等待
conn.execute("PRAGMA mmap_size=268435456")         # 256MB mmap
conn.execute("PRAGMA temp_store=MEMORY")           # 临时表放内存
```

**关键设计决策**：
- `threading.local()` 存储连接（线程安全）。最初的单例连接因 `check_same_thread=True` 在线程池（ThreadPoolExecutor）中写入失败，数据未持久化到 DB。git 历史记录了这一修复（`db.py` comment: "B38 singleton was not thread-safe"）。
- 单一 `kline` 表设计：`(ticker, timestamp, interval)` 联合索引。

**环节 3：store.py (1111 行) + loader.py (369 行) -- Parquet 展示缓存**

```python
# loader.py:29-55 — 按月时间分区
# 路径格式: data/display/{ticker}/YYYY/MM/{ticker}_{tf}.parquet
def _partitioned_path(base_dir, ticker, tf, dt=None):
    return f"{base_dir}/{ticker}/{dt:%Y}/{dt:%m}/{ticker}_{tf}.parquet"
```

**为什么需要两层缓存？**

| | SQLite | Parquet |
|------|--------|---------|
| 定位 | 权威数据源（source of truth） | 展示缓存（display cache） |
| 读取方式 | 逐行（row-based） | 列存储（columnar） |
| 用途 | CLI 回测、精确查询 | Streamlit 图表快速渲染 |
| 同步策略 | fetcher 直接写入 | store.py 定期从 SQLite 同步 |
| 分区 | 不分区（单文件） | 按月分区（YYYY/MM/） |

**关键问题**：测试报告中指出某些 env 变量（如 `YFINANCE_TIMEOUT`、`DB_PATH`）未被代码读取——它们的值硬编码在源文件中。这导致在不同环境（Docker vs 本地）可能需要修改源码才能切换数据路径。

### 4.2 级联合成与时区处理

实现在 `filter/data/synth.py`（656 行）。

**级联合成原理**：最细粒度（如 1m）从 DB 直接读取；较粗粒度（如 5m）由细粒度合成而非重新从 DB 读取。这节省了存储空间（只需存最细粒度）并保证了一致性（粗粒度=细粒度精确聚合）。

```python
# synth.py 核心逻辑（简化）：
# 5m 从 1m 合成：resample('5T').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'})
# 15m 从 5m 或 1m 合成
# 60m 从 15m 或 5m 合成
# 日线 从 60m 合成
```

**时区处理**：港股（`Asia/Hong_Kong`, UTC+8）、美股（`America/New_York`, UTC-5/-4）、A股（`Asia/Shanghai`, UTC+8）的跨时区转换在级联合成中正确处理。当数据跨越夏令时切换边界时，合成逻辑保持时间轴连续性。

### 4.3 数据质量

来自测试报告（`test-report.md`）：

| 维度 | 状态 | 详情 |
|------|------|------|
| 总行数 | 141,772 | `SELECT COUNT(*) FROM kline` |
| 标的数 | 13 | 覆盖港股/A股/美股 |
| 空值 | **0** | 所有时间帧所有标的 |
| 查询延迟 | 7.7ms | `SELECT COUNT(*)` 平均 |
| 数据修复能力 | 有 | `fetcher.py` 的 `INSERT OR REPLACE` 最新 bar 机制 |
| 日线延迟 | 已缓解 | 周线 API 回退机制（fetcher.py:191-206） |

---

## 第五章：回测结果分析 -- 绩效评估体系

### 5.1 16 项金融指标解读

实现在 `filter/backtest/metrics.py` (217 行)，Numba `@njit(cache=True)` 加速核心计算。

**第一优先（决定策略生死）**：

| 指标 | 公式 | 合格线 | 优异线 | 说明与局限 |
|------|------|--------|--------|-----------|
| Sharpe Ratio | `(Rp-Rf)/σp` | > 1.0 | > 2.0 | 年化。假设收益率正态分布（实际是肥尾），30-bar 窗口无意义 |
| Max Drawdown | `min((V-peak)/peak)` | < 20% | < 10% | Numba O(n) 单次遍历（`_compute_drawdown_metrics`） |
| Max DD Duration | 水下连续 bar 数 | < 100 | < 50 | 同函数计算，与 MaxDD 合并为一次遍历 |
| Calmar Ratio | `Rp/max_dd` | > 0.5 | > 1.0 | 综合年化收益和最大回撤 |

**第二优先（辅助判断）**：

| 指标 | 合格线 | 说明 |
|------|--------|------|
| Sortino Ratio | > 1.5 | 只用下行波动 σ_down（比 Sharpe 更真实） |
| Win Rate | > 45% | 趋势跟踪胜率不高是正常的（靠盈亏比赚钱） |
| Profit Factor | > 1.5 | 总盈利/总亏损 |
| Annual Return | - | 必须结合 MaxDD 看。20% 收益但 30% 回撤不如 10% 收益 5% 回撤 |

**第三优先（细查）**：

| 指标 | 关注点 |
|------|--------|
| Avg Win / Avg Loss | 理想 > 1.5:1 |
| Total Trades | 交易次数少则统计不可靠 |
| Avg Trade Return | 单笔平均收益 |
| Annual Volatility | 年化波动率 |

**Numba 加速的关键指标**（`metrics.py:25-72`）：

```python
@njit(cache=True)
def _compute_drawdown_metrics(pnl):
    """单次 O(n) 遍历同时计算 MaxDD 和 MaxDD Duration"""
    peak = pnl[0]; max_dd = 0.0; max_dd_dur = 0; current_dur = 0
    for i in range(len(pnl)):
        if pnl[i] > peak: peak = pnl[i]
        dd = (pnl[i] - peak) / peak if peak != 0 else 0.0
        if dd < max_dd: max_dd = dd
        if dd < 0.0:
            current_dur += 1
            if current_dur > max_dd_dur: max_dd_dur = current_dur
        else: current_dur = 0
    return max_dd, max_dd_dur
```

### 5.2 交互式 HTML 仪表盘

实现在 `filter/backtest/dashboard.py` (495 行)，基于 Plotly.js 的 8 个可视化章节：

1. **KPI 卡片**：Sharpe/Sortino/Calmar/MaxDD/WinRate 等核心指标一行展示
2. **PnL 曲线**：多/空/组合三条曲线叠加，交易标记（三角形入场/圆形止盈/叉号止损）
3. **回撤曲线**：水下区域蓝色填充，标注最大回撤点和持续时间
4. **滚动指标**：滚动 Sharpe、滚动 MaxDD 随时间变化（检测指标稳定性）
5. **信号对比**：不同滤波器在同一时间轴的信号重叠热力图
6. **持仓热力图**：月度/季度收益分布，标识策略的季节性表现
7. **交易明细表**：逐笔交易列表（入场价/离场价/收益率/离场原因）
8. **10 滤波器对比**：Pareto 前沿分析（见下方）

**跨子图缩放同步**：8 个章节的 x 轴（时间轴）联动缩放——拖动任一章节的时间范围，其他章节同步缩放到同一范围。这允许你快速从 PnL 异常段（章节 2）缩放到对应的回撤段（章节 3）再查看该时段的信号（章节 5）。

**拖放上传**：仪表盘支持拖拽 `.parquet` 文件直接上传渲染，无需 CLI 启动。

### 5.3 10 滤波器对比工具

实现在 `tools/filter_comparison_tool.py`，提供独立于主应用的滤波器性能对比：

**5 种合成测试信号**（每种 1000-2000 样本）：
- Sinusoid (SNR 3.2 dB)
- Step (SNR 4.5 dB)
- Trend+Seasonal (SNR 10.2 dB)
- Impulse (SNR -2.8 dB) — 负 SNR，滤波器在此信号上表现差异最大
- Chirp (SNR 13.4 dB)

**6 个评估维度**：
1. 信号保持度（滤波输出与干净信号的相关性）
2. 噪声衰减（dB）
3. 阶跃响应延迟（样本数）
4. 边缘保持度（对突变信号的保留能力）
5. 平滑度（输出二阶差分平方和）
6. 计算复杂度（执行时间）

**Pareto 前沿分析**：在两个相互制约的维度（如延迟 vs 平滑度）上绘制所有滤波器的 Pareto 前沿——在前沿上的滤波器（无法在不牺牲另一维度的前提下改进当前维度）被视为非支配最优。

**随机性问题**：工具使用 `np.random` 生成噪声但**未设置固定种子**。每次运行的 SNR 和延迟数值略有变化。建议在调用前加 `np.random.seed(0)`，运行 3 次取中位数。

### 5.4 实战结果解读框架

**好的策略长什么样**：

- PnL 曲线是**阶梯状上升**（平台+跳跃交替）——不是直线上升
- 盈利来自**多滤波器共识**信号（2+ 视图同时出现同向信号）
- **不同时间段的收益均匀分布**——而非集中在某个月
- **盈亏比 > 1.5**，即便胜率只有 45-50%

**坏的策略（红旗标志）长什么样**——任一触发即放弃：

| 红旗 | 含义 |
|------|------|
| 训练集 Sharpe > 2.0，验证集 < 0.5 | 严重过拟合 |
| 测试集 MaxDD > 训练集 x 2 | 参数不稳定 |
| Win Rate > 70% 但盈亏比 < 0.5 | 高频小盈覆盖偶尔大亏 |
| 任何单笔交易贡献 > 30% 总利润 | 运气主导 |
| 去掉最好一笔后总 PnL 变负 | 收益来自单次事件 |

**运气 vs 技能的判别**（综合 5 项）：

- **大概率技能**：训练/验证/测试 Sharpe 递减但在合格线以上 + 4 帧至少 3 帧 PnL 为正 + 多滤波器共识盈利 > 独立信号盈利 + 盈亏比 > 1.5
- **大概率运气**：去掉最佳交易后 PnL 变负 + 某个时间帧业绩远超其他 + 参数微调结果剧烈变化 + 训练/测试集表现反向

---

## 第六章：实例测试报告

### 6.1 测试环境与结果

来自 `test-report.md`（2026-07-27 执行）：

| 项目 | 值 |
|------|----|
| OS | macOS Darwin 25.3.0 |
| Python | 3.12.6 |
| 依赖 | pandas 2.3.3, numpy 2.2.4, streamlit 1.57.0, plotly 6.7.0, yfinance 1.4.1, scipy 1.17.1, numba 0.64.0, pytest 9.0.2 |
| 数据库 | SQLite 单表 `kline`，141,772 行，WAL 模式 |

**分批执行结果**：

| 批次 | 测试数 | 耗时 | 通过 | 覆盖模块 |
|------|--------|------|------|---------|
| 第1批: 核心+配置 | 376 | 22.41s | 全通过 | filters, config, db, module_structure, constants, metrics, ci |
| 第2批: 引擎+信号 | 167 | 11.09s | 全通过 | engine, signals, pipeline_capture, pipeline_unification |
| 第3批: 回测 | 217 | 21.28s | 全通过 | backtest_catalog, logger, core, cutoff, cli, reproducibility |
| **合计** | **760** | **~55s** | **0 失败** | |

**警告汇总（非错误）**：

1. `FutureWarning` (backtest_logger): pandas `replace` 向下转型行为变更 → mock 数据触发变化则正常
2. `RuntimeWarning` (filters): 全 NaN 信号均值计算、单点 lowess 除法 → 边界测试预期行为
3. `UserWarning` (filters): median kernel_size 超出信号长度时的零填充 → 预期行为

**未运行的 1,653 个测试**：包括 Streamlit UI 测试、快照测试、集成/e2e 测试、性能基准测试、图表测试、并发测试等。这些需要更长的运行时间或特定运行时环境（Streamlit server 等）。

### 6.2 随机性来源与可重复性建议

| 来源 | 位置 | 风险等级 | 影响 | 建议 |
|------|------|---------|------|------|
| Kalman 滤波器 | `engine/filters.py` | **中** | 每次运行信号边界偏移 ±1-2 bar | 运行 5 次，≥4 次一致的信号标记为"可靠" |
| 过滤器对比工具 | `tools/filter_comparison_tool.py` | **中** | 噪声信号 SNR 每次略有差异 | 加 `np.random.seed(0)`，运行 3 次取中位数 |
| yfinance 拉取 | `data/fetcher.py` | 低 | 历史数据理论上确定，API 可能返回微小差异 | 使用本地 DB 缓存回测时不受影响 |
| numba JIT | 多处 | 低 | 首次慢（~1s），后续稳定（已缓存） | 不影响可重复性 |

**可重复性最佳实践**：

```bash
# 1. Kalman 滤波器：运行 5 次取共识
for run in {1..5}; do
    python -m filter.backtest_cli --ticker AAPL --preset AAPL_US --output-dir "results/run_${run}"
done

# 2. 统计 5 次结果的均值和标准差
python -c "
import json, glob
sharpes = [json.load(open(f+'/metrics.json')).get('sharpe',0) for f in sorted(glob.glob('results/run_*'))]
print(f'Mean: {sum(sharpes)/len(sharpes):.3f}, Std: {(sum((s-sum(sharpes)/len(sharpes))**2 for s in sharpes)/len(sharpes))**0.5:.3f}')
"
# 如果 Std/Mean > 30% → 随机性主导，结果不可靠
```

---

## 第七章：单支股票深度研究方法论（核心）

### 7.1 研究哲学

**核心理念：多时间帧共识 + 多滤波器验证 + 加速度信号过滤。**

传统均线交叉/MACD 有两个致命缺陷：(1) 滞后性——价格走了一大段才出信号；(2) 震荡市频繁假突破。filter_research 绕过这些问题的方式：

1. **不直接用价格交叉，用加速度突破**。Schmitt 触发器监测的是价格二阶导数 `a` 是否突破自适应死区。死区宽度 = `k_eps x EWMA波动率`——高波动时自宽、低波动时收窄。
2. **不是单一滤波器，是 10 滤波器共识**。多个异质性算法（移动平均/状态空间/多项式拟合/频域滤波）在同一时刻给出同向加速度突破，可信度远超单滤波器。
3. **不是单时间帧，是 4 视图跨周期验证**。日线定方向（做多还是做空），60 分线找节奏（趋势是否健康），15 分线等入场（信号共振），5 分线精确执行。粗周期约束细周期，不是反过来。

**一句话**：用粗周期定战略（方向），用多滤波器求共识（可靠性），用加速度信号定战术（时机）。

### 7.2 完整 4 Phase 研究流程

#### Phase 1: 数据准备与初步观察

**目标**：建立对股票"性格"的基本认知。

**命令**：

```bash
# 数据量检查
python -c "
from filter.data.loader import load_display_cache
import pandas as pd
# 或直接查 DB
import sqlite3
conn = sqlite3.connect('data/market.db')
df = pd.read_sql(\"SELECT COUNT(*) as cnt, interval FROM kline WHERE ticker='3690.HK' GROUP BY interval\", conn)
print(df)
"
```

**Go 标准**：日线 >= 500 bar（约 2 年），15 分钟 >= 10,000 bar。
**No-Go**：数据不足 500 bar → 换标的或接受结果不可靠。

#### Phase 2: 多时间帧趋势研判

**目标**：确定主趋势方向和强度。

**UI 操作**：在 browse 模式设置 4 视图网格。

**推荐参数**：

| 视图 | 时间帧 | 滤波器 | k_eps | 作用 |
|------|--------|--------|-------|------|
| v0 | 1d | Kalman(Q=0.01,R=0.1) | 2.0 | 定战略方向 |
| v1 | 60m | SMA(20) | 1.5 | 验证趋势健康度 |
| v2 | 15m | EMA(12) | 1.2 | 等待入场信号 |
| v3 | 5m | ALMA | 1.0 | 精确执行（可选） |

**趋势有效 vs 震荡的判断标准**：

| 指标 | 趋势有效 | 震荡无方向 |
|------|---------|-----------|
| 日线滤波器斜率 | 持续单向 30+ bar | 频繁上下交替 |
| Schmitt 信号密度 | 稀疏而持续（每 10-20 bar 一个） | 密集而频繁反转（每 3-5 bar 翻转） |
| 60m 与日线一致性 | >= 80% | 经常矛盾 |

#### Phase 3: 策略回测验证

**目标**：验证信号在历史上是否真的能赚钱。

**命令**（CLI 模式）：

```bash
# 训练集（最近 500 bar 之前的全部历史）
python -m filter.backtest_cli \
  --ticker 03690.HK --preset 3690_HK_DP \
  --start-bar 0 --end-bar 1150 \
  --output-dir results/train

# 验证集
python -m filter.backtest_cli \
  --ticker 03690.HK --preset 3690_HK_DP \
  --start-bar 1150 --end-bar 1540 \
  --output-dir results/val

# 测试集（参数已固定，只跑一次！）
python -m filter.backtest_cli \
  --ticker 03690.HK --preset 3690_HK_DP \
  --start-bar 1540 --end-bar 1923 \
  --output-dir results/test
```

**避免过拟合三原则**：

1. **固定大部分参数，只调关键 2-3 个**：滤波器类型指定、时间帧组合固定、止损逻辑类型固定。只调 `k_eps` 和滤波器内部 1 个参数（如 Kalman 的 Q/R）。
2. **网格搜索在训练集上做**：k_eps(5 值) x Q(3 值) x R(3 值) = 45 组。选验证集上 Sharpe 最高的那组。
3. **测试集只跑一次**：参数确定后在测试集上运行唯一一次。看到结果后绝不回去调参——这是最致命的过拟合。

#### Phase 4: 结果分析与决策

**KPI 解读顺序**（在 HTML 仪表盘中）：

1. KPI 卡片（3 秒）：Sharpe > 1.0? MaxDD < 20%? → 不满足直接放弃
2. PnL 曲线形状：阶梯状 = 健康；靠少数几笔垂直拉升 = 运气
3. 信号对比：盈利来自多滤波器共识还是独立信号？
4. 持仓热力图：收益是否集中在某个时间段？
5. 交易分析：最大连续亏损几笔？
6. 10 滤波器对比：当前滤波器排名前 3 且前 5 彼此接近 = 稳健

**Go/No-Go 标准**：
- **Go**：满足 Phase 1-4 中所有 Go 标准，无红旗标志触发
- **观望**：大部分满足，1-2 个临界值
- **No-Go**：任一 No-Go 被触发且无法解释

### 7.3 实战检查清单（13 步）

```
[ ] Step 1: 数据检查 → Go: 日线 >= 500 bar
[ ] Step 2: 裸 K 线观察 → Go: 趋势可辨识
[ ] Step 3: 快速滤波器扫描 → Go: 至少 3/4 滤波器方向一致
[ ] Step 4: 多时间帧研判 → Go: 粗周期方向一致 + 细周期有同向信号
[ ] Step 5: 确定核心参数 → Go: 能产生清晰信号
[ ] Step 6: 训练集回测 → Go: Sharpe > 1.0, MaxDD < 20%
[ ] Step 7: 验证集回测 → Go: Sharpe > 训练集 x 0.6
[ ] Step 8: 多滤波器交叉验证 → Go: 当前滤波器排名前 3
[ ] Step 9: Kalman 多次验证 → Go: 5 次中 >= 4 次 Sharpe > 0.8
[ ] Step 10: 测试集最终验证 → Go: Sharpe > 0.5, MaxDD < 训练集 x 1.5
[ ] Step 11: 仪表盘深度解读 → Go: 无红旗标志
[ ] Step 12: 跨周期一致性 → Go: >= 3/4 时间帧 PnL 为正
[ ] Step 13: 最终决策 → Go: 满足全部 Go 标准
```

### 7.4 随机性陷阱纪律

**必须固定的参数（不允许调优）**：

| 参数 | 固定值 | 原因 |
|------|--------|------|
| 时间帧组合 | [1d, 60m, 15m, 5m] | 4 视图是策略逻辑基础 |
| bar 步进间隔 | 1 | 不等于 1 的回测是自欺欺人 |
| 数据集划分 | 60%/20%/20% | 标准时序划分 |
| 最小 bar 数 | 500 | 低于 500 统计无意义 |

**必须多次运行取平均的操作**：

1. Kalman 信号生成 → 5 次，标记 >= 4 次一致的为可靠信号
2. 10 滤波器对比 → 3 次，取中位数
3. 物理抛物线预测 → 3 次，差异 > 5% 则预测不可靠

---

## 附录 A：关键命令速查

### 数据查询
```bash
# 启动浏览模式
cd /Users/xfpan/claude/filter_research && streamlit run filter/browse/app.py

# 查看 DB 中某标的的数据范围
python -c "
import sqlite3, pandas as pd
conn = sqlite3.connect('data/market.db')
print(pd.read_sql(\"SELECT COUNT(*), MIN(datetime), MAX(datetime) FROM kline WHERE ticker='3690.HK'\", conn))
"

# 查看各时间帧 bar 数
python -c "
import sqlite3, pandas as pd
conn = sqlite3.connect('data/market.db')
df = pd.read_sql(\"SELECT interval, COUNT(*) as cnt FROM kline WHERE ticker='3690.HK' GROUP BY interval\", conn)
print(df.to_string())
"
```

### 回测
```bash
# 基础回测
python -m filter.backtest_cli --ticker 03690.HK --preset 3690_HK_DP

# 指定 bar 范围回测
python -m filter.backtest_cli --ticker 03690.HK --preset 3690_HK_DP \
  --start-bar 500 --end-bar 1500

# 只跑日线视图
python -m filter.backtest_cli --ticker 03690.HK --preset 3690_HK_DP \
  --view-filter v0_日线

# 从 checkpoint 恢复
python -m filter.backtest_cli --ticker 03690.HK --preset 3690_HK_DP \
  --resume results/checkpoint_500.json
```

### Kalman 多次运行验证
```bash
for run in {1..5}; do
    python -m filter.backtest_cli --ticker AAPL --preset AAPL_US \
      --output-dir "results/kalman_run_${run}" --quiet
done

# 汇总 5 次运行的 Sharpe
python -c "
import json, glob
sharpes = []
for f in sorted(glob.glob('results/kalman_run_*/metrics.json')):
    d = json.load(open(f))
    s = d.get('sharpe', 0)
    sharpes.append(s)
    print(f'{f}: Sharpe={s:.3f}')
mean = sum(sharpes)/len(sharpes)
std = (sum((s-mean)**2 for s in sharpes)/len(sharpes))**0.5
print(f'Mean: {mean:.3f}, Std: {std:.3f}, CV: {std/abs(mean)*100:.1f}%')
# CV > 30% → 随机性主导
"
```

### 回测结果对比
```bash
python -c "
import json
a = json.load(open('results/train/metrics.json'))
b = json.load(open('results/test/metrics.json'))
print(f'训练集: Sharpe={a[\"sharpe\"]:.2f}, MaxDD={a[\"max_dd\"]:.2%}, Calmar={a[\"calmar\"]:.2f}')
print(f'测试集: Sharpe={b[\"sharpe\"]:.2f}, MaxDD={b[\"max_dd\"]:.2%}, Calmar={b[\"calmar\"]:.2f}')
print(f'衰减: {(1-b[\"sharpe\"]/a[\"sharpe\"])*100:.0f}%')
"
```

### 测试运行
```bash
# 核心测试
pytest tests/ --ignore=tests/test_app_ui.py -x

# 回测专项测试
pytest tests/test_backtest_*.py -v

# 过滤器专项测试
pytest tests/test_filters.py tests/test_signals.py -v
```

---

## 附录 B：文件索引

| 文件 | 行数 | 功能 |
|------|------|------|
| `filter/browse/app.py` | 943 | Streamlit 4 视图网格入口 |
| `filter/backtest/engine.py` | 1,145 | BacktestRunner 逐 bar 滑动窗口 |
| `filter/data/store.py` | 1,111 | SQLite→Parquet 双缓存协调 |
| `filter/data/db.py` | 712 | SQLite 线程安全连接池 |
| `filter/data/synth.py` | 656 | 级联合成（细→粗） |
| `filter/backtest/cli.py` | 598 | argparse CLI + 预设管理 |
| `filter/backtest/dashboard.py` | 495 | Plotly 交互式仪表盘 |
| `filter/engine/filters.py` | 471 | 10 种滤波器 + FILTERS 注册表 |
| `filter/data/loader.py` | 369 | Parquet 展示缓存加载 |
| `filter/engine/schmitt.py` | 309 | Schmitt 触发器 + 抛物线预测 |
| `filter/engine/strategy.py` | 266 | 策略 PnL 双阶段模型 |
| `filter/data/fetcher.py` | 254 | yfinance 下载 + 清洗 |
| `filter/backtest/metrics.py` | 217 | Numba 加速 16 项指标 |
| `tools/filter_comparison_tool.py` | - | 10 滤波器 Pareto 对比 |
| `tools/view_backtest.py` | 501 | 回测结果文件查看器 |

---

## 附录 C：已知局限清单

| # | 局限 | 位置 | 影响等级 | 应对 |
|---|------|------|---------|------|
| 1 | 无交易成本/滑点建模 | `strategy.py` PnL 计算 | **高** | 实盘预期打 6-7 折，聚焦日线级别策略 |
| 2 | `combined_pnl = max(long,short)` | `common/pnl_renderer.py` | **高** | 只看 long/short 各自指标，不依赖 combined |
| 3 | 30-bar 窗口统计无意义 | 仪表盘指标显示 | **中** | 窗口 >= 500 bar |
| 4 | Kalman 随机初始化不可重复 | `engine/filters.py` | **中** | 5 次运行取共识信号 |
| 5 | 对比工具无随机种子 | `tools/filter_comparison_tool.py` | **中** | 运行 3 次取中位数 |
| 6 | 粗周期信号在细周期投影伪相关 | `alignment.py` | **低** | 跨周期对齐仅作参考 |
| 7 | env 变量未被代码读取 | `fetcher.py`/`db.py` 硬编码路径 | **低** | 切换环境需修改源码 |
| 8 | Optuna 优化已移至 feat 分支 | `feat/optimization-analysis` | **低** | 如需要可从分支合并 |
| 9 | 无形式化统计显著性检验 | 全局 | **中** | 依赖多滤波器共识和 Kalman 多次运行作为替代验证 |

---

> **报告维护**：本报告基于 filter_research 项目 2026-07-27 状态生成。工具版本更新或新增功能后，需重新审视相关章节的有效性。方法论章节应随工具演进同步更新。
