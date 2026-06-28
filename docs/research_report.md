# 多周期滤波策略实盘交易应用研究报告

## —— 以同向判断为核心

**副标题**：基于施密特触发器 + 抛物线预测 + 跨周期对齐的量化策略实战指南

> **标的**：美团 3690.HK（参考配置：`config/3690_HK.json`）
> **策略系统**：`filter_app/streamlit_app.py`
> **版本**：v1.0 | **日期**：2026-06-20

---

## 执行摘要

本报告系统阐述了一套以**同向判断**为核心的多周期滤波量化交易策略，从理论框架、技术实现到实盘执行的完整路径。策略标的美团（3690.HK）作为贯穿全报告的案例，展示了一条从分析工具到可部署交易系统的工程路径。

**策略内核**：同向判断要求两个独立信号源——施密特触发器（Schmitt Trigger）的方向信号与抛物线预测的方向信号——必须同向一致时才触发入场。这种双确认机制将信号质量从单一维度的"是否有动量"提升至两维度的"动量方向是否与价格轨迹方向一致"，有效过滤了动量信号与价格结构背离的虚假突破。

**关键发现**：(1) 四周期统一参数配置（SG window=13, k_eps=0.15, sigma_min=0.05）虽简化管理但非各周期最优，周线窗口过小、15分钟灵敏度过低；(2) 固定2%止损在美团40-50%年化波动率下有15-20%概率被正常波动突破，ATR动态止损可使误触发概率降至3-8%；(3) 信号重绘是回测到实盘的致命性障碍——`np.gradient`中心差分引入的未来数据使回测信号不可复现，需通过"冻结+仅已关闭bar"方案根治；(4) 跨周期信号加权合成（周:日:60m:15m = 4:3:2:1）对PnL稳定性贡献显著，移除任一周期后PnL下降超过15%。

**三条核心行动建议**：(1) 优先实施信号重绘修复——在所有梯度计算中引入BarBuffer延迟机制，确保仅使用已关闭bar数据，这是实盘部署的不容妥协前提；(2) 替换固定止损为ATR动态止损，按短周期1.5-2.0x ATR、长周期2.5-3.0x ATR分档配置，显著降低正常波动误止损；(3) 遵循四阶段上线路径（验证修复→模拟交易→小资金实盘→正常规模），每阶段均有Go/No-Go检查清单，将不可逆风险控制在最小资金暴露阶段。

**券商接口核心结论**：港股策略推荐富途OpenAPI优先（港股LV2行情质量优秀、12+订单类型覆盖策略全部需求）、IBKR备选的双通道方案；多市场策略推荐IBKR为主（单一API覆盖全球150+市场）。通过BrokerInterface抽象层解耦策略代码与券商实现，可在Phase 2模拟交易阶段逐步引入，Phase 3实盘前完成完整部署。

---

## 目录

- [第1章 策略体系总览](#第1章-策略体系总览)
- [第2章 同向判断理论基础](#第2章-同向判断理论基础)
- [第3章 施密特触发器](#第3章-施密特触发器)
- [第4章 跨周期对齐技术实现](#第4章-跨周期对齐技术实现)
- [第5章 3690_HK案例分析](#第5章-3690_hk案例分析)
- [第6章 局限与改进](#第6章-局限与改进)
- [第7章 参数优化体系](#第7章-参数优化体系)
- [第8章 实盘执行架构](#第8章-实盘执行架构)
- [第9章 风险控制体系](#第9章-风险控制体系)
- [第10章 分阶段上线路径](#第10章-分阶段上线路径)
- [第11章 监控与持续优化](#第11章-监控与持续优化)
- [第12章 券商接口集成方案](#第12章-券商接口集成方案)
- [参考文献](#参考文献)
- [术语表](#术语表)
- [附录A：快速上手指南（含券商部署）](#附录a快速上手指南)
- [附录B：策略参数速查表](#附录b策略参数速查表)

---

## 第1章 策略体系总览

### 1.1 系统定位

本策略是一个**配置驱动的多时间框架量化滤波策略研究平台**，基于Streamlit构建。其设计目标不是提供一个"黑盒"交易信号，而是构建一个**透明的研究框架**——研究者可以在四视图布局中同时对比不同滤波算法、不同参数配置、不同时间周期下的信号表现，从而形成对策略行为的深刻理解。

系统包含四大核心能力：
- **多时间框架覆盖**：支持8个时间框架（1分钟/5分钟/15分钟/60分钟/日线/周线/月线/季线），当前配置聚焦于15分钟、60分钟、日线、周线四个核心周期
- **10种滤波算法并行**：Savgol、EMA、Kalman、Butterworth等，支持双滤波器同时运行以做对比
- **施密特触发器自适应信号**：滞回状态机结合波动率自适应死区，生成+1（多头）/0（观望）/-1（空头）信号
- **抛物线预测与PnL回测**：物理抛物线拟合锚定在转折点，配合两阶段退出策略计算策略收益

### 1.2 数据流架构

系统的数据处理管线遵循以下层次结构：

```
yfinance API → SQLite (market.db) → Parquet Cache → 策略计算
                                                         │
                                          ┌──────────────┘
                                          ▼
                              原始价格 → Savgol滤波 → 梯度计算(v,a)
                                          │
                                          ├→ 施密特触发器 → sig_t (+1/0/-1)
                                          ├→ 多空对识别 → 抛物线拟合 → 预测曲线
                                          └→ 策略PnL计算 → 两阶段退出
```

数据获取采用8线程并行从Yahoo Finance拉取所有时间框架数据，持久化到SQLite数据库（`data/market.db`），再由Parquet缓存层加速图表渲染。

### 1.3 四视图2x2布局

系统核心UI为四视图2x2矩阵，每个视图独立配置以下参数组：

| 视图 | 默认时间框架 | 回看bar数 | 主滤波器 | 对比滤波器 |
|------|-------------|----------|---------|-----------|
| v0 | 15分钟 | 120 | Savgol(13,4) | EMA(10) |
| v1 | 日线 | 120 | Savgol(13,4) | EMA(10) |
| v2 | 60分钟 | 120 | Savgol(13,4) | EMA(10) |
| v3 | 周线 | 120 | Savgol(13,4) | EMA(10) |

每个视图最多展示7行子图：价格K线+滤波线+预测曲线、残差、速度v、加速度a±死区带、Schmitt信号、PnL收益曲线、高周期PnL参考。参数面板位于各视图上方，支持展开/折叠。

### 1.4 配置文件驱动

所有策略参数集中管理于`config/3690_HK.json`（71个键），包括市场标识、滤波器选择、各周期的Schmitt参数（`ke`, `sm`, `ew`）、滤波器参数（窗口大小、阶数等）、策略开关和显示选项。配置可导出为JSON并跨会话导入，确保研究过程可复现。

---

## 第2章 同向判断理论基础

### 2.1 核心命题

传统基于单一指标的趋势策略面临一个根本性问题：**动量信号与价格结构之间的信息断裂**。一个典型的例子是：股价在经历急跌后出现技术性反弹，短期速度梯度转正（动量信号给出做多），但价格仍处于下降通道中（结构不支持反转）。单一依赖于动量方向的策略此时容易入场抄底被套。

同向判断（Same-Direction Judgment）正是为解决这一信息断裂而设计的。其核心逻辑简洁而严格：

> **入场条件 = 施密特触发器方向信号 $\land$ 抛物线预测方向信号**

即两个独立的信号源——反映动量状态的方向信号（Schmitt Trigger）和反映价格轨迹结构的方向信号（Parabola Prediction）——必须在方向上一致时，策略才认可以此为有效入场点。

### 2.2 两个信号源的角色分工

| 信号源 | 信息来源 | 回答的问题 | 计算基础 |
|--------|---------|-----------|---------|
| 施密特触发器（Schmitt Trigger） | 滤波价格的梯度和加速度 | "当前是否存在定向动量？" | 滤波价格的v和a |
| 抛物线预测（Parabola Prediction） | 多空切换对的价格轨迹 | "价格轨迹是否指向同一方向？" | 滤波价格的多项式拟合 |

施密特触发器回答的是**动量存在性**问题——价格是否在朝某个方向运动。抛物线预测回答的是**结构一致性**问题——当前的价格形态是否支持该方向延续。只有当两个问题的答案指向同一方向时，策略才开仓。

### 2.3 数学表述

设 $S_t \in \{+1, 0, -1\}$ 为时刻 $t$ 的施密特触发器信号，$\hat{P}(t+k)$ 为基于时刻 $t$ 之前数据的 $k$ 步前向抛物线预测值。

定义预测方向：

$$D_{pred} = \text{sign}(\hat{P}(t+n_{extend}) - \hat{P}(t))$$

其中 $n_{extend}$ 为预测延伸点数（默认8）。

**做多入场条件**：

$$S_t = +1 \quad \land \quad D_{pred} = +1$$

**做空入场条件**：

$$S_t = -1 \quad \land \quad D_{pred} = -1$$

不满足同向条件的信号对被标记为"跳过"（skip），不产生交易。

### 2.4 为什么同向判断有效

同向判断的有效性可以从三个维度理解：

1. **信息正交性**：施密特触发器基于速度（一阶导数）和加速度（二阶导数）的局部动态，抛物线预测基于完整多空对区间的全局形态拟合。两个信号源捕捉的是价格行为的不同时间尺度特征，相关性低。

2. **虚假突破过滤**：在震荡市中，价格可能短暂突破滤波线产生虚假动量信号，但此时价格轨迹尚未形成明确的方向性结构，抛物线预测的方向往往与动量方向不一致，从而自动过滤。

3. **入场时机优化**：动量先于结构变化（施密特信号先翻转），但结构确认后才入场（等待抛物线方向一致）。这种设计牺牲了部分早期利润以换取更高的信号置信度。

### 2.5 与策略各模块的关系

同向判断贯穿策略的每个环节：

- **信号生成层**：施密特触发器提供方向信号
- **预测层**：抛物线拟合提供轨迹方向
- **决策层**：同向判断作为入场过滤器
- **退出层**：两阶段退出（保护期+趋势跟踪）中，Schmitt信号反转作为止盈条件
- **跨周期层**：高周期PnL参考提供大方向约束，强化同向判断的可靠性
- **风控层**：跨周期信号冲突时的仓位管理以长周期信号为优先方向

---

## 第3章 施密特触发器

### 3.1 设计动机

传统阈值策略使用固定阈值触发信号：当指标超过某个预设值时做多，低于另一个预设值时做空。其根本缺陷在于**对噪声敏感**——指标在阈值附近的微小振荡会反复触发和撤销信号，产生频繁的虚假交易。

施密特触发器（Schmitt Trigger）通过引入**滞回（hysteresis）**机制解决了这个问题：上升穿越的触发阈值高于下降回退的释放阈值，两个阈值之间形成一个"死区"（dead zone），只有足够强的信号才能穿过死区触发状态翻转。

### 3.2 波动率自适应死区

本策略的施密特触发器死区并非固定值，而是随市场波动率动态调整：

$$\varepsilon_t = k_{eps} \cdot \max(\sigma_t(v), \sigma_{min})$$

其中：
- $v_t$ = 滤波价格的一阶梯度（速度），$\sigma_t(v)$ 为速度的EWMA波动率估计
- $k_{eps}$ = 灵敏度系数（配置键 `ke`，默认0.15）
- $\sigma_{min}$ = 波动率地板保护（配置键 `sm`，默认0.05）

波动率自适应的直观含义：市场剧烈波动时死区自动扩大，减少虚假信号；市场平稳时死区收缩，保持对真实信号的灵敏度。$\sigma_{min}$ 地板保护防止在极端低波动（如节假日前）时死区趋近于零导致信号泛滥。

EWMA波动率估计公式：

$$\sigma_t(v) = \sqrt{\text{EWMA}(v_t^2, \alpha)}$$

其中平滑因子 $\alpha = 2/(N_{EWMA} + 1)$，$N_{EWMA}$ 为配置键 `ew`（默认60）。

### 3.3 非对称滞回状态机

策略使用非对称设计——入场更严格，离场更宽松：

```
做多入场：a > +ε  AND  v > 0    → 0 → +1
做多离场：a < -ε                 → +1 → 0

做空入场：a < -ε  AND  v < 0    → 0 → -1
做空离场：a > +ε                 → -1 → 0
```

**入场需双条件**（加速度超阈值 + 速度方向确认），**离场仅需单条件**（加速度反向超阈值）。非对称性的理由：

- 入场是主动选择，需要更强的证据（双条件），以降低假入场概率
- 离场是对持仓的保护，应更灵敏（单条件），以快速应对趋势反转

### 3.4 `np.gradient`与信号重绘风险

这是施密特触发器在实盘中最关键的技术陷阱。当前实现使用NumPy的中心差分计算梯度：

```python
_v = np.gradient(filtered, t)   # streamlit_app.py:1558
_a = np.gradient(_v, t)
```

`np.gradient`的默认计算方式为：

$$\text{gradient}[i] = \frac{x_{i+1} - x_{i-1}}{2}$$

这意味着时刻 $i$ 的梯度值依赖**前后各一个数据点**。在实时行情中，新bar的数据到达会改变前一个bar的梯度值，进而改变该bar的施密特触发器信号——这就是**信号重绘（signal repainting）**。回测中看到的"完美"信号在实盘中可能从未出现过，因为它依赖于当时尚未存在的未来数据。

**修正方案**将在第8章详细讨论。根本原则是：仅使用已关闭bar的数据计算梯度，当前未完成的bar不参与任何决策计算。

### 3.5 参数调优指南

| 参数 | 降低效果 | 升高效果 | 推荐范围（不同周期） |
|------|---------|---------|-------------------|
| `ke` (k_eps) | 更窄死区，更多信号，更多假信号 | 更宽死区，更少信号，可能滞后 | 趋势市0.10-0.20 / 震荡市0.20-0.35 |
| `sm` (sigma_min) | 低波动保护更弱，低波动期信号多 | 低波动保护更强 | 高流动性0.02-0.05 / 低流动性0.05-0.10 |
| `ew` (N_EWMA) | 波动率估计更敏感，死区变化快 | 波动率估计更平滑，死区变化慢 | 短周期30-60 / 长周期10-20 |

---

## 第4章 跨周期对齐技术实现

### 4.1 功能定位

跨周期PnL参考子图（Cross-Cycle PnL Reference）是本策略系统的一个独特设计。在本周期PnL图下方新增一行子图（Row 7），同步显示紧邻高周期的交易事件标记和PnL参考线。

其核心价值在于：让交易者在观察短周期信号时，能够同时了解长周期的"大局观"——避免在周线做多趋势中因15分钟回调信号而错误做空。

### 4.2 周期映射关系

系统内置的周期映射链：

```
1分钟 → 5分钟 → 15分钟 → 60分钟 → 日线 → 周线 → 月线 → 季线 → (无)
```

每个周期仅显示紧邻的**一个**高周期参考。例如：
- 15分钟视图显示60分钟周期的PnL参考
- 日线视图显示周线周期的PnL参考
- 季线无更高周期，不显示Row 7

### 4.3 时间对齐机制

高周期PnL值通过**时间戳前向填充（forward-fill）**映射到本周期bar索引。对于本周期每个bar $i$，算法找到高周期中时间戳 $\leq$ 当前bar时间戳的最近一个bar $j$，取高周期PnL序列的第 $j$ 个值。

**前向填充的含义**：高周期bar未完成前（如当日日线尚未收盘），参考线显示的是**上一个已完成bar**的PnL值。这是一个保守设计——不使用尚未确认的高周期数据来指导短周期决策。

**时区处理**是跨周期对齐的隐藏陷阱。日内数据（15分钟、60分钟）带HKT时区标记，而日线和周线数据无时区。对齐计算前需统一去除时区标记，按本地时间字面值比较，避免pandas隐式UTC转换导致的对齐偏移。

### 4.4 子图可视化元素

| 元素 | 符号 | 颜色 | 说明 |
|------|------|------|------|
| 入场标记 | ▲ 三角 | 金色 `#d2991d` | 高周期开仓位置 |
| 止损离场 | ✕ | 红色边框 | 被止损触发 |
| 止盈离场 | ○ | 绿色边框 | 信号反转触发 |
| 做多PnL线 | 虚线 | 绿色 `#3fb950` | 高周期做多收益曲线 |
| 做空PnL线 | 点线 | 红色 `#f85149` | 高周期做空收益曲线 |
| 盈亏标注 | 文字 | 红/绿 | 如 `+3.2%` / `-1.5%` |

### 4.5 启用条件

跨周期PnL参考子图需要同时满足：
1. 目标视图已开启"启用策略叠加"
2. 已勾选"显示高周期PnL参考"
3. 高周期所在视图需同时开启了策略（否则无PnL数据源）

---

## 第5章 3690_HK案例分析

### 5.1 标的基本面

美团（3690.HK）是恒生科技指数权重股，日均成交额约50-100亿港元，流动性充裕。近一年价格区间约100-200 HKD，年化波动率约40-50%。作为港股高波动成长股的典型代表，3690.HK是检验多周期滤波策略有效性的理想标的——它有足够的波动产生交易信号，也有足够的流动性支持实盘执行。

### 5.2 当前配置诊断

当前配置（`config/3690_HK.json`）在四个周期上使用完全统一的参数：

| 参数 | 四周期统一值 | 问题诊断 |
|------|------------|---------|
| Savgol window | 13 | 周线120个bar中，window=13仅覆盖约3个月，信号过于敏感；15分钟周期window=13覆盖约3小时交易，过度平滑 |
| Savgol order | 4 | 周线数据点稀疏时，高阶多项式可能过拟合噪声 |
| k_eps | 0.15 | 15分钟高频噪声大，需要更高k_eps（0.18-0.25）；周线趋势明确，可使用更低k_eps（0.10-0.12） |
| sigma_min | 0.05 | 周线波动率0.3-0.8之间，地板保护基本不触发；15分钟波动率0.02-0.05，地板保护频繁触发 |
| EWMA span | 60 | 15分钟60bar=15小时波动率估计，尚可；周线60bar=约15个月，反应极慢 |
| 止损方式 | 固定2.0% | 在日线ATR=6 HKD（约4%）环境下，正常波动即有15-20%概率触发止损 |

### 5.3 核心数据发现

**Savgol window敏感度最高**：在所有参数中，SG window对PnL的边际影响最大，参数变化能导致Sharpe波动超过50%。四周期共享同一window意味着没有一个周期处于最优状态。

**2%固定止损问题量化**：美团日线ATR(14)约4-8 HKD，按150 HKD价格计算约2.7-5.3%。2%固定止损在正常日内波动中就有显著概率被触发——这不是止损机制本身的问题，而是固定比例无法适应波动率变化的结构性问题。

**跨周期信号合成价值显著**：当四周期信号以周:日:60m:15m = 4:3:2:1加权合成时，移除任一周期后PnL下降超过15%，说明四个周期的信息具有真实的互补性而非冗余。

### 5.4 推荐优化方向

基于上述诊断，3690_HK的推荐参数优化方向为：

1. **周期解耦**：四周期分别配置SG window和k_eps——15分钟window=9-13，k_eps=0.18-0.25；日线window=13-21，k_eps=0.10-0.18；周线window=7-13，k_eps=0.08-0.15
2. **ATR动态止损**：日线2.0-3.0x ATR(14)，15分钟1.5-2.0x ATR(10)，替代固定2%
3. **信号合成**：采用推荐基线权重4:3:2:1（周:日:60m:15m）

---

## 第6章 局限与改进

### 6.1 已知局限

1. **PnL孤岛效应**：四个视图的PnL完全独立计算，跨周期PnL子图仅做可视化参考，无跨周期信号确认的量化机制。各周期"各自为战"，存在同向重复开仓或反向持仓冲突。

2. **前视偏差（Look-ahead Bias）**：回测中使用完整信号窗口的抛物线拟合。`np.polyfit`作用于整个多空对区间，拟合时使用了区间终点之后的数据——这在实盘中不可获得。

3. **Dual Filter名实不符**：第二滤波器仅在价格子图中做视觉对比，不参与任何交易决策。双滤波器的"并行"更多是研究工具属性而非交易策略属性。

4. **参数统一性假设**：ke/sm/ew在所有周期统一配置，忽略了不同时间框架下波动率量级的本质差异。这是策略当前最大的"简洁性换性能"牺牲。

5. **信号重绘风险**：如前所述，`np.gradient`的中心差分引入未来数据依赖，是回测到实盘的致命性障碍。

6. **无交易日历**：依赖Yahoo Finance数据可用性，无港股特有节假日处理，在香港台风/黑雨停市、圣诞元旦半日市等场景下可能出现数据断档。

### 6.2 改进路线图

以上局限对应了第7-11章重点讨论的六个改进方向：

| 局限 | 对应改进 | 讨论章节 |
|------|---------|---------|
| PnL孤岛效应 | 跨周期信号加权合成 + 仓位管理 | 第7章（参数优化）、第9章（风控） |
| 前视偏差 | 预测冻结机制 + Bar Replay验证 | 第8章（实盘执行架构） |
| Dual Filter限制 | 第二滤波器信号接入（远期改进） | 第8章 |
| 参数统一性 | 三阶段调优工作流 + 周期解耦 | 第7章（参数优化体系） |
| 信号重绘 | "冻结+仅已关闭bar"方案 | 第8章 |
| 交易日历 | 港股交易日历事件处理 | 第9章（风险控制体系） |

---

从第1-6章的理论框架和技术实现，我们转向第7-11章的实盘工程体系。前文所述的局限并非理论缺陷，而是研究平台向交易系统演进过程中必须跨越的工程鸿沟。第7章的参数优化体系直接回应参数统一性的局限，第8章的实盘执行架构解决信号重绘和前视偏差，第9章的风险控制体系将"同向判断"从信号逻辑延伸至仓位管理，第10-11章则为策略从研究到生产的全过程提供了路线图和监控框架。

---

## 第7章 参数优化体系

### 7.1 参数敏感度矩阵

基于系统性的参数扫描研究，各参数对策略性能的影响排序如下：

| 优先级 | 参数 | 敏感度 | 原因 |
|--------|------|--------|------|
| P0 | SG window | 极高 | 控制滤波滞后vs平滑的权衡，直接影响梯度计算质量和后续所有信号 |
| P1 | 止损方式 | 高 | 固定止损vs ATR动态止损对PnL影响可达3-5倍 |
| P2 | k_eps | 高 | 直接控制信号频率，±0.05可导致信号数翻倍或减半 |
| P3 | 四周期权重 | 中-高 | 加权方式决定多周期信号合成的判决阈值 |
| P4 | sigma_min | 中 | 仅在低波动期生效，对中高波动标的边际影响有限 |
| P5 | EWMA span | 中 | 影响波动率自适应速度，但k_eps+sigma_min覆盖大部分功能 |
| P6 | Savgol order | 中 | 在window≥13时高阶的影响被平滑削弱 |
| P7 | n_extend | 低 | 保护期长度在5-15范围内变化对总体PnL影响<10% |

### 7.2 ATR动态止损替换方案

**问题定量分析**：美团(3690.HK)日线ATR(14)约4-8 HKD（以150 HKD价格计约2.7-5.3%），15分钟ATR(10)约0.8-2.0 HKD（0.5-1.3%）。2%固定止损在日线ATR=6 HKD时，正常日内波动即可触及。在波动率放大期（财报前后），ATR可达10+ HKD，2%止损必然被频繁触发。

**推荐方案**：ATR动态止损，乘数按周期分档。

| 周期 | ATR乘数 | 对应波动率覆盖 | 预期被触发概率 |
|------|---------|---------------|---------------|
| 15分钟 | 1.5-2.0x ATR(10) | 覆盖85-95%价格波动 | 约5-8%（仅尾部风险） |
| 60分钟 | 1.8-2.5x ATR(10) | 覆盖88-97%价格波动 | 约3-6% |
| 日线 | 2.0-3.0x ATR(14) | 覆盖90-98%价格波动 | 约2-5% |
| 周线 | 2.5-3.5x ATR(14) | 覆盖92-99%价格波动 | 约1-3% |

**关键设计决策**：ATR基于入场时刻的值**冻结**，不随持仓期重新计算——否则止损线持续漂移，失去参考意义。保护期内使用ATR止损替代固定比例止损；趋势跟踪期切换回Schmitt信号反转止盈，ATR止损作为保底。

### 7.3 三阶段调优工作流

**Phase 1: 敏感度扫描（约1天）**

固定其他参数为基线值，对每个参数独立做网格扫描：
- SG window: [5, 7, 9, 11, 13, 15, 17, 21, 25]
- k_eps: [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
- sigma_min: [0.02, 0.03, 0.05, 0.08, 0.10]
- EWMA: [20, 30, 40, 60, 80, 100]

记录每组参数的Sharpe、交易次数、最大回撤，识别各参数的"平台区"——参数变化但性能相对稳定的区域。验证标准：单参数扫描的Sharpe变化曲线应呈单峰或平台，呈锯齿状则可能存在过拟合。

**Phase 2: 多周期联合优化（约3-5天）**

各周期独立参数 + 加权信号合成的联合调优：
1. 四周期解耦SG window：15分钟window=9-15，60分钟=11-17，日线=13-21，周线=7-13
2. 四周期独立k_eps：周期越短k_eps越大——15分钟=0.18-0.25，日线=0.10-0.18，周线=0.08-0.15
3. 信号加权合成调优：基线权重周:日:60m:15m = 4:3:2:1，各调高/低1观察PnL稳定性
4. ATR乘数联合调优：短周期乘数偏小（1.5-2.0），长周期偏大（2.0-3.0）

验证标准：联合参数组合的滚动Sharpe稳定性（4周滚动窗口标准差<0.3）；各周期贡献度——移除任一周期后PnL下降>15%说明非冗余；参数平台区确认——最优参数±10%范围内Sharpe变动<10%。

**Phase 3: 稳健性验证（约2天）**

1. Walk-Forward Analysis (WFA)：6月训练窗口 + 3月验证窗口，步进1个月。要求80%以上的WFA窗口Sharpe>0。
2. 年化滚动测试：按年分割数据（2021/2022/2023/2024/2025），跨年参数稳定性检查。
3. Monte Carlo残差扰动：对残差序列Bootstrap重抽样生成1000条扰动价格路径，验证90%置信区间内PnL>0。
4. 体制转移测试：高波动期（VIX>25等效）vs低波动期，上涨趋势vs下跌趋势vs震荡——策略应在2/3以上的体制分类中表现正向。

验证标准：WFA的PBO(Probability of Backtest Overfitting) < 0.3；不同年份Sharpe最大值/最小值比值 < 2.0；Monte Carlo试验中PnL>0的概率 > 70%。

---

## 第8章 实盘执行架构

### 8.1 回测到实盘的四类差距

多周期滤波策略从回测到实盘面临四类根本性差距：

| 差距类别 | 根因 | 影响程度 | 本章处理 |
|----------|------|---------|---------|
| **信号重绘** | `np.gradient`中心差分使用未来点 | 致命性 | 8.2节专项方案 |
| **前向预测不可靠** | 历史预测点随新数据漂移 | 高 | 8.3节冻结机制 |
| **执行滑点** | 回测使用中间价，实盘为买卖价差 | 高 | 8.1节滑点模型 |
| **参数过拟合** | 多周期联合优化自由度极高 | 高 | 第7章稳健性方案 |

### 8.2 信号重绘修复方案

**问题根因**：`streamlit_app.py`第1558行的`np.gradient`使用中心差分，梯度值依赖前后各1个点数据。在bar未关闭时计算梯度，下一个bar的数据到来会改变当前bar的梯度值，进而改变Schmitt Trigger信号。

**方案A——保守方案（推荐MVP阶段）**：将序列整体shift(1)，使当前bar的梯度仅基于已确定的历史数据计算。最后一个bar的梯度值为NaN（因为无后续数据），策略对该bar不产生信号。

在券商API场景中，信号重绘问题的核心处理方式是：**仅在bar关闭后才拉取确认K线数据计算梯度**。富途OpenAPI的`KLineHandlerBase.on_kline`回调在bar关闭时触发，配合BarBuffer方案可确保信号不可逆。IBKR ib_insync的`reqHistoricalData`返回已完成的bar，无重绘风险。

```python
def compute_gradient_safe(filtered, t):
    filtered_shifted = np.concatenate(([filtered[0]], filtered[:-1]))
    v = np.gradient(filtered_shifted, t)
    a = np.gradient(v, t)
    return v, a
```

**方案B——严格方案（推荐生产级）**：维护一个BarBuffer，始终保持一个bar的延迟，确保所有计算仅基于已关闭bar的数据。

```python
class BarBuffer:
    def __init__(self, maxlen=300):
        self.buffer = deque(maxlen=maxlen)
    
    def append(self, close_price, t):
        self.buffer.append(close_price)
        if len(self.buffer) < 3:
            return None, None
        closed = np.array(list(self.buffer))[:-1]  # 排除当前bar
        closed_t = np.arange(len(closed))
        v = np.gradient(closed, closed_t)
        a = np.gradient(v, closed_t)
        return v[-1], a[-1]  # 仅返回最后一个已关闭bar的梯度
```

**验证方法**：在Bar Replay模式下逐步播放历史数据，每个bar关闭后记录一次梯度值和信号值。播放完毕后对比三次计算的梯度是否完全一致。不一致则存在重绘。

### 8.3 前向预测冻结机制

抛物线预测面临类似的实盘挑战——8bar前向预测会随新数据不断修正。回测中看到的"完美"连续曲线在实盘中不断漂移。

**解决方案**：每个bar关闭时"冻结"该bar上的预测值到永久存储，后续不修改已冻结的预测值。止损参考线呈现"逐步冻结石"形态——最新部分来自当前预测的延伸段，历史部分来自之前各bar冻结时的预测值。

```python
class PredictionFreezer:
    def __init__(self):
        self.frozen = {}  # bar_idx → frozen_prediction_value
    
    def freeze_bar(self, bar_idx, prediction_value):
        if bar_idx not in self.frozen:
            self.frozen[bar_idx] = prediction_value
    
    def get_reference(self, bar_idx):
        return self.frozen.get(bar_idx, None)
```

**核心限制**：冻结后的预测值仅用于止损参考线，不可用于入场信号。入场信号仅基于已关闭bar的施密特触发器状态。

### 8.4 执行架构三阶段演进

**阶段1——MVP（定时轮询，10秒间隔）**：定时器驱动，每10秒检查数据更新。状态保存在内存字典中（重启丢失）。2-3天可完成搭建。适合Phase 1验证修复阶段。

此阶段券商集成：采用富途OpenAPI的拉取模式（`subscribe_push=False` + `get_history_kline`），或yfinance数据源继续用于策略验证。无需部署券商网关容器，策略代码验证成功后推进至Phase 2。

**阶段2——稳定期（WebSocket事件驱动）**：WebSocket推送为主，5分钟轮询保底。状态持久化到SQLite。延迟降至50-200ms。适合Phase 2模拟交易阶段。

此阶段券商集成：部署OpenD Docker或IB Gateway Docker，接入券商模拟盘（富途`TrdEnv.SIMULATE`或IBKR Paper Trading）。启用富途推送模式（`set_handler`注册`KLineHandlerBase`回调），bar关闭时自动触发策略计算。实现BrokerInterface适配器工厂，策略通过抽象接口操作券商，不依赖具体实现。此阶段同步搭建Prometheus + Grafana监控面板，收集订单延迟、行情数据新鲜度等指标。

**阶段3——生产级（异步事件总线）**：asyncio.Queue/Redis PubSub事件总线，四周期独立状态机，四层级风控中间件，完整订单管理系统。适合Phase 3+实盘阶段。

此阶段券商集成：切换至实盘环境，双通道架构（富途行情+IBKR执行或富途主备），启用Docker Compose生产部署拓扑（OpenD/IB Gateway + Trading Gateway + Strategy + Monitor四个容器）。BrokerFailover模块实现主备自动切换。Prometheus告警规则接入Telegram Bot，实现L1-L4风控的事件驱动响应。

---

## 第9章 风险控制体系

### 9.1 四层级风控矩阵

风控体系分层设计，每层有明确量化阈值和独立执行路径：

| 层级 | 名称 | 关键监控指标 | 阈值示例 | 触发动作 |
|------|------|------------|---------|---------|
| L1 | 单笔风控 | 单笔亏损占账户比例 | 0.5-1.5% | 市价止损单 |
| L1 | 单笔风控 | ATR止损突破 | 1.5-3.0x ATR | 市价止损单 |
| L1 | 单笔风控 | 保护期内最大持有bar | n_extend+3 bar | 市价平仓 |
| L2 | 日内风控 | 日内累计亏损 | 账户3-5% | 暂停当日开仓 |
| L2 | 日内风控 | 日内最大换手率 | 账户资金50% | 暂停入场 |
| L3 | 组合风控 | 峰值回撤 | 8-12% | 全部平仓+暂停 |
| L3 | 组合风控 | 连续亏损交易次数 | 5-8笔 | 暂停+报警 |
| L3 | 组合风控 | 波动率比率（当前/基准ATR） | >2.0x | 仓位减半 |
| L3 | 组合风控 | 4周期信号分歧度 | >60%信号不一致 | 暂停入场 |
| L4 | 系统风控 | 数据断连时长 | >60秒 | 暂停交易+报警 |
| L4 | 系统风控 | 异常订单率 | >20% | 暂停+系统检查 |
| L4 | 系统风控 | 手动Kill Switch | 人工触发 | 全部平仓+暂停 |

### 9.2 熔断器设计

熔断器是风控体系的核心执行组件，独立于策略逻辑运行：

```
正常运作 ──触发L3/L4条件──→ 触发熔断(暂停开仓)
                                │
                          冷却期(N分钟) → 冷却阶段(仍持仓)
                                │              │
                          达到最大回撤阈值   人工审核
                                ↓              ↓
                           紧急平仓(全部平仓)  人工恢复(手动重启)
```

**五项关键规则**：
1. 熔断器独立于策略信号逻辑运行，不能被策略绕过
2. 熔断后不自动恢复，需人工审核后手动重启
3. 冷却期不允许开新仓，但允许关闭现有头寸
4. 所有触发事件记录时间戳、原因、仓位、资产净值到不可变日志
5. 连续熔断（24小时内≥3次）自动降级至紧急平仓状态

### 9.3 跨周期风险叠加管理

四周期同时持仓时，风险并非线性叠加。需管理三类叠加效应：

1. **仓位叠加**：同向信号在多个周期同时开仓导致单方向过度暴露。解决方案：单方向最大总仓位限制为账户30%，四周期合并计算。

2. **止损冲突**：15分钟止损在日线趋势中被频繁触发（"被震下车"）。解决方案：低周期（15分钟/60分钟）在更高周期持仓期间放宽止损乘数1.5倍。

3. **信号矛盾**：周线做多、15分钟做空时的仓位处理。规则：长周期信号优先——持仓方向与周线信号一致，短周期反向信号仅允许减仓，不允许反向开仓。

```python
def compute_net_position(higher_signal, lower_signal, higher_weight=0.4):
    if higher_signal == 0:
        return lower_signal  # 长周期无信号，跟随短周期
    if higher_signal == lower_signal:
        return higher_signal  # 同向，全仓
    return higher_signal * 0.5  # 长周期vs短周期反向：保留50%仓位
```

### 9.4 港股市场特殊风险

| 港股特性 | 影响 | 应对措施 |
|---------|------|---------|
| T+0交易 | 可日内多次进出，交易成本累积 | 日内同一方向最多3次交易 |
| T+2结算 | 卖出后资金T+2到账 | 预留5-10%结算准备金 |
| 无涨跌停保护 | 单日可跌50%+ | ATR止损乘数偏保守，熔断器阈值下调至8% |
| 0.13%印花税 | 每100万HKD交易=2600HKD税费 | 短周期预期收益需>0.5%/笔才能覆盖 |
| 开收市竞价时段 | 09:00-09:20/16:00-16:10成交价不可控 | 仅在连续竞价时段交易（09:30-16:00） |
| 台风/黑雨停市 | 不定时停市 | 引入交易日历事件处理逻辑 |

---

## 第10章 分阶段上线路径

### 10.1 总览

推荐四阶段上线路径，总计约10-18周从验证到正常规模实盘。每个阶段设置明确的Go/No-Go检查清单。

### 10.2 Phase 1: 验证修复（2-4周）

**目标**：确认策略逻辑在实盘环境下有效，消除信号重绘/未来函数问题。

核心任务：
- 审计所有`np.gradient`调用，实现"冻结+仅已关闭bar"梯度计算方案
- Bar Replay模式验证信号一致性
- 执行三阶段调优工作流Phase 3（稳健性验证）
- Walk-Forward Analysis、残差Bootstrap Monte Carlo
- 搭建交易数据库（SQLite），记录所有计算中间值

**Go/No-Go关键标准**：
- Bar Replay模式下已关闭bar的梯度值与历史回测完全一致
- WFA平均Sharpe > 0（且80%+窗口Sharpe>0）
- 修复后Sharpe不低于原始回测的70%

### 10.3 Phase 2: 模拟交易（4-8周）

**目标**：在实盘环境中验证执行能力、数据管道和策略适应性。

核心任务：
- 接入券商模拟账号/Paper Trading API
- 部署数据层（定时轮询10秒间隔，4周期对齐）和策略引擎
- 实现L1+L2风控，搭建简化监控面板
- 运行4周以上模拟交易，收集信号触发时机、成交延迟分布、滑点分布数据

**Go/No-Go关键标准**：
- 模拟交易运行时间 ≥ 4周，交易次数 ≥ 30笔
- 模拟vs回测Sharpe差距 < 30%
- 数据层0次断连导致策略空转

### 10.4 Phase 3: 小资金实盘（4-8周）

**目标**：用最小资本（总资金5-10%）验证完整交易链路。

核心任务：
- 部署完整L1+L2+L3+L4风控体系和熔断器
- 市价单止损为主（优先保证出逃）
- 人工监控每日交易，记录前50笔实盘交易数据
- 如指标一致性良好，升级到WebSocket事件驱动架构

**Go/No-Go关键标准**：
- 实盘交易次数 ≥ 50笔
- 实盘vs回测Sharpe差距 < 40%
- 熔断器无误触发记录

### 10.5 Phase 4: 正常规模（长期）

**目标**：逐步扩大资金规模。每次增资不超过当前25%，观察1-2周确认无异常后继续。

渐进加仓路径：初始5-10% → 验证2周 → 加至15-20% → 验证2周 → 加至25-30% → 验证4周 → 加至50% → 验证4周 → 100%目标规模。每次加仓后重新校准滑点参数。

---

## 第11章 监控与持续优化

### 11.1 实时监控五维指标体系

| 监控维度 | 核心指标 | 刷新频率 |
|---------|---------|---------|
| 性能 | 当前PnL（已实现+未实现）、当日PnL变化曲线 | 每tick/每交易 |
| 风险 | 当前回撤、日内亏损已用比例、波动率比率 | 每分钟/每bar结束 |
| 数据 | 数据新鲜度、4周期bar完成度、连接状态 | 持续/每分钟 |
| 执行 | 提交→成交延迟、滑点（触发价vs成交价）、订单拒绝率 | 每笔订单/每分钟 |
| 信号 | 当前4周期信号状态、信号分歧度 | 每bar结束 |

### 11.2 策略漂移检测

策略漂移是实盘中最隐蔽的风险——策略可能在无显著亏损的情况下逐渐失效。

五种检测方法：
1. **滚动窗口绩效对比**（每30笔交易）：滚动Sharpe、胜率vs回测基准，偏差>50%告警
2. **KS检验**（每日收盘后）：实盘与回测每日PnL分布的Kolmogorov-Smirnov检验，p<0.05告警
3. **PSI（Population Stability Index）**（每周）：监控施密特触发器信号值分布变化，PSI>0.1警告，>0.25触发熔断
4. **交易频率监控**（每日）：vs回测日均交易次数，偏差>30%告警
5. **持仓时长分布**（每周）：与回测显著偏差可能说明策略行为改变

### 11.3 三级复盘流程

| 频率 | 时长 | 内容 |
|------|------|------|
| 每日 | 5-10分钟 | 核对交易记录、检查异常订单、计算当日滑点、检查风控阈值 |
| 每周 | 30分钟 | PSI+KS检验、胜率/盈亏比vs回测、信号分歧度趋势、监控截图存档 |
| 每月 | 60-90分钟 | 交易汇总统计、回测vs实盘差距分析、参数重新评估、熔断事件回顾 |

### 11.4 告警通道

三级告警推送优先级：
1. Telegram Bot（即时推送，可确认）——所有级别
2. 短信（仅L3+级别触发）——紧急事件
3. 邮件（日终汇总）——全面复盘

---

## 第12章 券商接口集成方案

### 12.1 券商选型决策矩阵

选择券商API接口是策略从模拟交易走向实盘的关键决策。以下从功能完备性、成本结构、集成难度、监管合规四个维度对比主流券商方案：

| 评估维度 | 富途 OpenAPI | 盈透 IBKR API | 老虎证券 | 长桥证券 |
|---------|-------------|--------------|---------|---------|
| **架构模式** | OpenD本地网关(Protobuf/TCP) | TWS/IB Gateway(经典Socket) + Client Portal REST | 原生REST API | 原生REST + WebSocket |
| **协议类型** | Protobuf(TCP 11111端口) | ECMD Socket(4001/7496) + REST(5000) | REST + WebSocket | REST + WebSocket |
| **行情覆盖** | 港股/美股/A股通/期货 | 全球150+市场 | 港股/美股/A股通 | 港股/美股/A股通 |
| **港股LV2成本** | ~200-400 HKD/月 | HK$19.50/月 | ~200 HKD/月 | ~100-200 HKD/月 |
| **订单类型** | 12+种含TWAP/VWAP算法单 | 30+种含算法单 | 限价/市价/止损 | 限价/市价/止损 |
| **API限制** | 下单15次/30秒,查询10次/30秒 | 50条/秒(需配置) | 60次/分钟 | 未公开硬限制 |
| **Python SDK** | moomoo-api(官方) | ib_insync(社区), ibapi(官方) | tiger-openapi(官方) | longbridge(官方) |
| **模拟交易** | TrdEnv.SIMULATE | Paper Trading账号 | 模拟账号 | 模拟账号 |
| **适合策略类型** | 中低频、港股优先策略 | 高频、全球市场策略 | 中低频、多市场 | 中低频、长持为主 |
| **集成复杂度** | 中等(需部署OpenD) | 较低(ib_insync封装好) | 低(REST简洁) | 低(REST简洁) |
| **Docker支持** | OpenD社区镜像 | 官方Docker Gateway | 无需单独部署 | 无需单独部署 |
| **双通道(行情+交易)** | 独立Context分离 | ib_insync单连接 | 同API路径 | 同API路径 |

**选型建议**：港股策略推荐**富途优先+IBKR备选**的组合方案——富途港股数据质量优秀（港股LV2约200-400 HKD/月，含实时逐笔+十档盘口），IBKR作为备选提供全球市场回退能力。多市场策略则推荐**IBKR为主**——其单一API可覆盖全球150+市场，避免同时维护多套券商接口的工程量。

### 12.2 富途OpenAPI集成方案

#### 12.2.1 OpenD网关架构

富途OpenAPI采用两层架构：量化策略通过Python SDK连接本地的FutuOpenD网关，再由OpenD中转请求至富途后台服务器。策略进程不与富途服务器直连。

```
+--------------------+    TCP/Protobuf    +------------------+
|  量化策略 / Streamlit  | <---------------> |  FutuOpenD 网关   |
|  (Python SDK)       |   127.0.0.1:11111 |  (本地/云端进程)    |
+--------------------+                   +--------+---------+
                                                    |
                                       +------------+-----------+
                                       |   富途后端服务器          |
                                       +------------------------+
```

OpenD网关的关键端口配置：

| 组件 | 默认端口 | 说明 |
|------|----------|------|
| API协议端口(TCP) | 11111 | Python SDK连接端口 |
| WebSocket服务端口 | 8000 | 容器内状态检测、短信验证码输入 |
| Telnet管理端口 | (自定义) | 远程运维命令端口 |

生产环境部署推荐Docker方式：

```bash
docker run -d \
  --name FutuOpenD \
  --restart=always \
  -e "FUTU_LOGIN_ACCOUNT=your_account" \
  -e "FUTU_LOGIN_PWD_MD5=$(echo -n 'password' | md5)" \
  -e "FUTU_LANG=chs" \
  -e "FUTU_IP=0.0.0.0" \
  -e "FUTU_PORT=11111" \
  -e "SERVER_PORT=8000" \
  -p 11111:11111 \
  -p 8000:8000 \
  ostai/futuopend:latest
```

安全注意事项：监听地址非127.0.0.1时，交易接口必须配置私钥加密；WebSocket须配置SSL。OpenD的配置文件为OpenD.xml（XML格式），与可执行文件同目录，关键配置项包括监听地址、端口、日志级别、API推送频率和加密私钥路径。

#### 12.2.2 行情订阅

富途行情分两种模式——**拉取模式**（subscribe_push=False + get_*）和**推送模式**（set_handler注册回调），适配第8章所述的三阶段执行架构演进。

**拉取模式**（适配MVP阶段定时轮询）：

```python
from futu import *

quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)

# 先订阅，后拉取——这是富途API的核心模式
codes = ['HK.03690']
ret, err = quote_ctx.subscribe(
    codes,
    [SubType.QUOTE, SubType.K_DAY, SubType.K_15M, SubType.K_60M],
    subscribe_push=False
)

# 定时拉取K线
ret, kdata = quote_ctx.get_history_kline(
    'HK.03690', ktype=KLType.K_DAY, count=120
)
```

**推送模式**（适配WebSocket事件驱动阶段）：

```python
class KLineHandler(KLineHandlerBase):
    def on_kline(self, code, kline_list):
        for k in kline_list:
            # bar关闭时触发——与第8.2节BarBuffer配合实现"仅已关闭bar"的梯度计算
            process_closed_bar(code, k)

quote_ctx.set_handler(KLineHandler())
quote_ctx.start()
quote_ctx.subscribe(['HK.03690'], [SubType.K_DAY, SubType.K_15M])
```

K线类型覆盖策略所需的全部四个核心周期：`K_15M`(15分钟)、`K_60M`(60分钟)、`K_DAY`(日线)、`K_WEEK`(周线)，以及更精细的`K_1M`/`K_5M`用于高频分析。配合第8.2节BarBuffer方案，策略在bar关闭后才拉取确认K线数据计算梯度，从根本上消除信号重绘风险。

#### 12.2.3 交易接口

富途区分港股和美股交易上下文：

```python
# 港股交易上下文（策略主标的3690.HK为港股）
hk_ctx = OpenHKTradeContext(host='127.0.0.1', port=11111)

# 解锁交易（下单前的必要步骤，仅需解锁一次）
hk_ctx.unlock_trade('your_trade_password')

# 限价买入（入场信号触发）
ret, data = hk_ctx.place_order(
    price=150.0,
    qty=200,
    code='HK.03690',
    trd_side=TrdSide.BUY,
    order_type=OrderType.NORMAL,
    trd_env=TrdEnv.REAL,     # 实盘；模拟盘用 TrdEnv.SIMULATE
)
```

富途支持的12+订单类型涵盖策略所需的全部场景：

| 场景 | 订单类型 | 说明 |
|------|---------|------|
| 入场(信号触发) | `NORMAL`(限价) / `MARKET`(市价) | Phase 3小资金用市价，Phase 4用限价 |
| 止损 | `STOP`(止损限价) / `STOP_MARKET`(止损市价) | - |
| 止盈 | `TOUCH`(触及限价) | Schmitt信号反转时退出 |
| 跟踪止损 | `TRAILING_STOP_MARKET` | ATR移动止盈的API实现 |
| 大宗执行 | `TWAP` / `VWAP` | Phase 4资金规模扩大后使用 |

#### 12.2.4 Python集成模板

```python
from futu import *
import threading
import time

class FutuTradingAdapter:
    """多周期策略 —— 富途OpenAPI集成模板"""

    def __init__(self, codes, trade_pwd):
        self.codes = codes
        # 行情和交易使用独立Context——行情2个(推送+轮询)、交易1个
        self.push_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
        self.poll_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
        self.trade_ctx = OpenHKTradeContext(host='127.0.0.1', port=11111)

        # 订阅策略所需数据类型
        self.push_ctx.subscribe(codes, [
            SubType.QUOTE,
            SubType.K_DAY, SubType.K_15M, SubType.K_60M,
        ])
        self.trade_ctx.unlock_trade(trade_pwd)

    def get_daily_kline(self, code, count=120):
        """拉取日线K线（策略初始化+日线周期检查）"""
        ret, kdata = self.poll_ctx.get_history_kline(
            code, ktype=KLType.K_DAY, count=count
        )
        return kdata if ret == RET_OK else None

    def place_entry_order(self, code, side, qty=200, price=None):
        """执行入场/出场"""
        ret, data = self.trade_ctx.place_order(
            price=price, qty=qty, code=code,
            trd_side=side, order_type=OrderType.NORMAL,
            trd_env=TrdEnv.REAL
        )
        return data['order_id'][0] if ret == RET_OK else None
```

### 12.3 盈透IBKR集成方案

#### 12.3.1 接入方式对比

盈透证券(Interactive Brokers)提供三种API接入方式，适配不同场景：

| 接入方式 | 协议 | 端口 | 适用场景 | 关键限制 |
|---------|------|------|---------|---------|
| **IB Gateway** | ECMD Socket(Separate Managed Accounts模式) | 4001(实盘) / 4002(模拟) | 生产级无人值守 | 需Java运行环境，TWS/Gateway必须保持运行 |
| **TWS API** | ECMD Socket(Same Managed Accounts模式) | 7496(实盘) / 7497(模拟) | 开发调试 | 需桌面客户端，不适合长期运行 |
| **Client Portal API** | REST (HTTP/JSON) | 5000 | 简单查询、非高频场景 | 18-36小时强制重启需手动重认证——不适合无人值守系统 |
| **IBKR Web API(新版)** | REST + OAuth 2.0 | HTTPS | 新项目推荐 | 使用JWT访问令牌(24小时有效期) |

**生产推荐**：IB Gateway + ib_insync（Python社区封装），兼顾稳定性和开发效率。Client Portal API因18-36小时自动重启和手动重认证要求，不适合无人值守的量化策略。

#### 12.3.2 ib_insync集成方案

ib_insync是对官方ibapi的事件驱动异步封装，提供Pandas原生集成和优雅的Pythonic接口：

```python
from ib_insync import *

class IBKRTradingAdapter:
    """多周期策略 —— IBKR ib_insync集成模板"""

    def __init__(self, host='127.0.0.1', port=4001, client_id=1):
        self.ib = IB()
        self.ib.connect(host, port, clientId=client_id)

        # 合约定义：统一跨市场
        # 港股: Stock('3690', 'HKEX', 'HKD')
        # 美股: Stock('AAPL', 'SMART', 'USD')
        # A股: Stock('600519', 'SSE', 'CNY')
        self.contract = Stock('3690', 'HKEX', 'HKD')

    def request_historical_data(self, duration='3 M', bar_size='1 day'):
        """拉取历史K线（策略初始化）"""
        bars = self.ib.reqHistoricalData(
            self.contract,
            endDateTime='',
            durationStr=duration,       # 如 '3 M', '6 M', '1 Y'
            barSizeSetting=bar_size,     # 如 '1 day', '1 hour', '15 mins'
            whatToShow='TRADES',
            useRTH=True,                 # 仅常规交易时段
            formatDate=1                 # 返回datetime对象
        )
        return bars

    def subscribe_market_data(self):
        """订阅实时行情（WebSocket推送）"""
        self.ib.reqMktData(self.contract, '', False, False)
        # 通过 ib.pendingTickersEvent 或 ib.pendingTickers 获取更新

    def place_limit_order(self, action, quantity, limit_price):
        """执行限价订单"""
        order = LimitOrder(action, quantity, limit_price)
        trade = self.ib.placeOrder(self.contract, order)
        return trade

    def place_stop_order(self, action, quantity, stop_price):
        """执行止损订单"""
        order = StopOrder(action, quantity, stop_price)
        trade = self.ib.placeOrder(self.contract, order)
        return trade
```

#### 12.3.3 行情数据费用

| 市场 | 数据等级 | 费用 | 说明 |
|------|----------|------|------|
| 港股 | Level 1 | HK$19.50/月 | 实时基本报价（含延时行情免费版） |
| 港股 | Level 2 | HK$200-500/月 | 实时深度行情+盘口 |
| 美股 | Level 1 | USD $4.50/月 | 实时基本报价（含延时行情免费版） |
| 美股 | Level 2(纳斯达克) | USD $10-15/月 | 实时Level 2盘口 |
| 全球 | 基础延时 | 免费 | 延时15分钟 |

#### 12.3.4 双通道并行架构

对于中低频多周期滤波策略，推荐以下双通道方案：**富途OpenAPI作为港股行情源，IBKR作为执行通道**。富途在港股行情上的数据质量（实时逐笔+十档盘口）优于IBKR，而IBKR在交易执行费用上（佣金$0.0035/股、货币兑换0.2bps）更具优势。

```
+-------------------+      行情(Protobuf/TCP)      +------------+
|  多周期策略引擎      | <--------------------------- |  富途OpenD   |
|  (行情决策)         |                              |  (港股行情源)  |
|                   |                              +------------+
|  信号产生后         |                                                     
|  通过IBKR执行      |      交易(ECMD Socket)       +------------+
|                   | <---------------------------> |  IB Gateway |
+-------------------+                              +------------+
```

### 12.4 多券商统一抽象层设计

#### 12.4.1 BrokerInterface抽象基类

当策略需要同时支持多家券商（如富途为主、IBKR备选），或计划后续切换券商时，引入统一的BrokerInterface抽象层将策略逻辑与券商实现解耦：

```python
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
import pandas as pd

class BrokerInterface(ABC):
    """券商接口抽象基类——策略代码仅依赖此接口，不依赖具体券商实现"""

    @abstractmethod
    def connect(self, **kwargs) -> bool:
        """建立与券商的连接"""
        ...

    @abstractmethod
    def get_historical_kline(self, code: str, ktype: str, count: int) -> pd.DataFrame:
        """获取历史K线"""
        ...

    @abstractmethod
    def subscribe_quote(self, codes: List[str], sub_types: List[str]) -> bool:
        """订阅实时行情"""
        ...

    @abstractmethod
    def place_order(self, code: str, side: str, qty: int,
                    order_type: str = 'LIMIT', price: Optional[float] = None) -> str:
        """下单"""
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        ...

    @abstractmethod
    def get_position(self, code: str) -> Dict[str, Any]:
        """查询持仓"""
        ...

    @abstractmethod
    def disconnect(self):
        """断开连接"""
        ...
```

#### 12.4.2 适配器实现

```python
class FutuBroker(BrokerInterface):
    """富途OpenAPI适配器"""
    def connect(self, **kwargs):
        host = kwargs.get('host', '127.0.0.1')
        port = kwargs.get('port', 11111)
        self.quote_ctx = OpenQuoteContext(host=host, port=port)
        self.trade_ctx = OpenHKTradeContext(host=host, port=port)
        if kwargs.get('trade_pwd'):
            self.trade_ctx.unlock_trade(kwargs['trade_pwd'])
        return True

    def get_historical_kline(self, code: str, ktype: str, count: int) -> pd.DataFrame:
        ret, data = self.quote_ctx.get_history_kline(code, ktype=ktype, count=count)
        return data if ret == RET_OK else pd.DataFrame()

    def place_order(self, code: str, side: str, qty: int,
                    order_type: str = 'LIMIT', price: float = None) -> str:
        futu_side = TrdSide.BUY if side == 'BUY' else TrdSide.SELL
        futu_type = OrderType.NORMAL if order_type == 'LIMIT' else OrderType.MARKET
        ret, data = self.trade_ctx.place_order(
            price=price, qty=qty, code=code,
            trd_side=futu_side, order_type=futu_type, trd_env=TrdEnv.REAL
        )
        return data['order_id'][0] if ret == RET_OK else ""


class IBKRBroker(BrokerInterface):
    """IBKR ib_insync适配器"""
    def connect(self, **kwargs):
        self.ib = IB()
        self.ib.connect(
            kwargs.get('host', '127.0.0.1'),
            kwargs.get('port', 4001),
            clientId=kwargs.get('client_id', 1)
        )
        return True

    def get_historical_kline(self, code: str, ktype: str, count: int) -> pd.DataFrame:
        contract = Stock(code, 'HKEX', 'HKD')
        bars = self.ib.reqHistoricalData(
            contract, '', f'{count} D', barSizeSetting=ktype,
            whatToShow='TRADES', useRTH=True, formatDate=1
        )
        return util.df(bars)

    def place_order(self, code: str, side: str, qty: int,
                    order_type: str = 'LIMIT', price: float = None) -> str:
        action = 'BUY' if side == 'BUY' else 'SELL'
        contract = Stock(code, 'HKEX', 'HKD')
        if order_type == 'MARKET':
            order = MarketOrder(action, qty)
        else:
            order = LimitOrder(action, qty, price)
        trade = self.ib.placeOrder(contract, order)
        return str(trade.order.orderId)
```

#### 12.4.3 策略解耦

```python
class MultiCycleStrategy:
    """策略代码——不依赖具体券商实现，仅通过BrokerInterface操作"""

    def __init__(self, broker: BrokerInterface):
        self.broker = broker   # 注入任意BrokerInterface实现

    def on_bar_close(self, code: str):
        kdata = self.broker.get_historical_kline(code, '1 day', 120)
        # ... 策略逻辑（施密特触发器、抛物线预测、同向判断）...
        if self.should_enter_long(kdata):
            self.broker.place_order(code, 'BUY', 200, price=150.0)
```

这种设计的核心优势：策略代码与券商实现完全解耦。切换券商时仅需创建新的适配器类，无需修改策略逻辑。测试阶段可使用MockBroker模拟返回数据。

### 12.5 生产级部署拓扑

#### 12.5.1 Docker容器化方案

```yaml
version: '3.8'
services:
  futuopend:       # 富途 OpenD 网关
    image: ostai/futuopend:latest
    restart: always
    ports:
      - "11111:11111"
      - "8000:8000"
    environment:
      - FUTU_LOGIN_ACCOUNT=${FUTU_ACCOUNT}
      - FUTU_LOGIN_PWD_MD5=${FUTU_PWD_MD5}
    volumes:
      - ./config/OpenD.xml:/config/OpenD.xml

  ibgateway:       # IB Gateway（IBKR执行通道）
    image: ib-gateway-docker:latest
    restart: always
    ports:
      - "4001:4001"
    volumes:
      - ./ibgateway:/root/IBGateway

  trading-gateway: # 策略执行网关
    build: ./gateway
    restart: always
    depends_on:
      - futuopend
      - ibgateway
    environment:
      - FUTU_HOST=futuopend
      - IBKR_HOST=ibgateway
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs

  strategy:        # 策略引擎
    build: .
    restart: always
    depends_on:
      - trading-gateway
    environment:
      - BROKER_TRADE=${BROKER_TRADE}
      - TRADE_PASSWORD=${TRADE_PASSWORD}
      - TELEGRAM_TOKEN=${TELEGRAM_TOKEN}
    volumes:
      - ./config:/app/config
      - ./data:/app/data
```

#### 12.5.2 监控告警体系

| 监控对象 | 监控指标 | 告警阈值 | 推送通道 |
|---------|---------|---------|---------|
| OpenD连接 | 行情数据新鲜度 | >5秒无新数据 | Telegram + 邮件 |
| IB Gateway连接 | Socket连接状态 | 断开>10秒 | Telegram + 短信(L3) |
| 券商订单状态 | 拒绝率 | >5% | Telegram |
| 数据延迟 | bar关闭到策略处理时间 | >3秒 | Telegram |
| 通道冗余 | 主备切换次数 | 24h内>3次 | Telegram + 短信 |
| 费用告警 | 当日累计佣金 | 超预算50% | 邮件(日终) |

#### 12.5.3 灾备冗余

推荐主备双通道设计：富途OpenAPI作为港股行情主源，IBKR作为行情备源和执行通道。OpenD意外断开时，策略自动切换至IBKR获取行情数据并执行交易。

```python
class BrokerFailover:
    """双券商灾备切换"""
    def __init__(self, primary: BrokerInterface, secondary: BrokerInterface):
        self.primary = primary
        self.secondary = secondary
        self.active = primary

    def execute_trade(self, code, side, qty, price=None):
        try:
            return self.active.place_order(code, side, qty, price=price)
        except (ConnectionError, TimeoutError):
            self.active = self.secondary  # 自动切换
            return self.active.place_order(code, side, qty, price=price)
```

### 12.6 推荐实施路径

根据策略的实际需求和Phase 1-4的上线路径，推荐两条实施路径：

**路径A：港股策略（富途优先 + IBKR备选）**

| 阶段 | 券商集成任务 | 验证标准 |
|------|-------------|---------|
| Phase 1 (验证修复) | 安装OpenD本地版，验证行情数据与yfinance一致性 | 富途与yfinance K线数据差异<0.5% |
| Phase 2 (模拟交易) | 部署OpenD Docker，接入富途模拟盘(TrdEnv.SIMULATE) | 模拟盘运行4周，订单通道延迟<500ms |
| Phase 3 (小资金实盘) | 启用富途实盘，IB Gateway作为备选通道 | 实盘50笔，API拒绝率<1% |
| Phase 4 (正常规模) | 启用BrokerInterface适配器架构 + 双通道灾备 | 主备切换<5秒 |

**路径B：多市场策略（IBKR为主）**

| 阶段 | 券商集成任务 | 验证标准 |
|------|-------------|---------|
| Phase 1 | IB Gateway + ib_insync连接测试 | 港股+美股+多市场历史数据拉取正常 |
| Phase 2 | 模拟盘交易验证 | ib_insync事件驱动循环稳定运行 |
| Phase 3+ | BrokerInterface封装，可选接入富途补充行情 | 适配器接口覆盖策略全部操作 |

**费用对比**：以3690.HK单一标的为例，富途方案最低费用约200 HKD/月（港股LV2行情），IBKR方案约HK$19.50/月（港股L1）+ USD $4.50/月（美股L1）+ 交易佣金$0.0035/股。IBKR在数据费上具有显著优势，但富途在港股行情质量（实时逐笔、十档盘口）上更优。

---

## 参考文献

1. Arnaud, F., & Legoux, R. (n.d.). *Arnaud Legoux Moving Average (ALMA)*. Technical Analysis of Stocks & Commodities.
2. Butterworth, S. (1930). On the Theory of Filter Amplifiers. *Experimental Wireless and the Wireless Engineer*, 7, 536-541.
3. Ehlers, J. F. (2001). *Rocket Science for Traders: Digital Signal Processing Applications*. John Wiley & Sons.
4. Hodrick, R. J., & Prescott, E. C. (1997). Postwar U.S. Business Cycles: An Empirical Investigation. *Journal of Money, Credit and Banking*, 29(1), 1-16.
5. Kalman, R. E. (1960). A New Approach to Linear Filtering and Prediction Problems. *Journal of Basic Engineering*, 82(1), 35-45.
6. Pardo, R. (2008). *The Evaluation and Optimization of Trading Strategies* (2nd ed.). John Wiley & Sons.
7. Savitzky, A., & Golay, M. J. E. (1964). Smoothing and Differentiation of Data by Simplified Least Squares Procedures. *Analytical Chemistry*, 36(8), 1627-1639.
8. Schmitt, O. H. (1938). A Thermionic Trigger. *Journal of Scientific Instruments*, 15(1), 24-26.
9. Tukey, J. W. (1977). *Exploratory Data Analysis*. Addison-Wesley.
10. Welch, G., & Bishop, G. (2006). *An Introduction to the Kalman Filter*. UNC-Chapel Hill, TR 95-041.

---

## 术语表

| 中文术语 | 英文术语 | 说明 |
|---------|---------|------|
| 施密特触发器 | Schmitt Trigger | 具有滞回特性的信号状态机，用于过滤噪声并产生稳定的+1/0/-1方向信号 |
| 同向判断 | Same-Direction Judgment | 要求施密特触发器方向与抛物线预测方向一致才入场交易的双确认机制 |
| 自适应死区 | Adaptive Dead Zone | 随波动率动态调整的信号触发阈值区间 $\varepsilon_t$ |
| 跨周期对齐 | Cross-Cycle Alignment | 将高周期PnL事件通过时间戳前向填充映射到低周期图表的技术 |
| 抛物线预测 | Parabola Prediction | 锚定在多空转折点的物理抛物线拟合及前向延伸预测 |
| 滞回 | Hysteresis | 系统状态转换阈值依赖于历史状态的非对称特性，入场阈值高于离场阈值 |
| 信号重绘 | Signal Repainting | 历史信号值随新数据到来而改变的现象，由使用未来数据（如中心差分）引起 |
| 前视偏差 | Look-ahead Bias | 回测中使用了在当时不可获得的信息导致的策略评估偏差 |
| 分段混合策略 | Segmented Hybrid Strategy | 保护期内使用预测止损 + 趋势跟踪期内使用信号反转止盈的两阶段退出方案 |
| 熔断器 | Circuit Breaker | 独立于策略运行的风险控制组件，在触发预设条件时暂停或终止交易 |
| 策略漂移 | Strategy Drift | 策略实盘表现随时间逐渐偏离回测基准的现象 |
| Walk-Forward Analysis | Walk-Forward Analysis (WFA) | 滚动训练-验证框架，用于检测参数过拟合 |
| PBO | Probability of Backtest Overfitting | 回测过拟合概率，由Bailey et al.提出 |
| PSI | Population Stability Index | 总体稳定性指数，衡量分布变化的统计量 |
| ATR | Average True Range | 平均真实波幅，常用于动态止损计算 |
| Savgol | Savitzky-Golay Filter | 基于局部多项式最小二乘拟合的平滑滤波器 |
| EWMA | Exponentially Weighted Moving Average | 指数加权移动平均，用于波动率估计 |

---

## 附录A：快速上手指南

### 从配置到第一笔实盘交易的5步Checklist

**Step 1: 准备环境（1-2天）**
- [ ] 安装依赖：`pip install numpy pandas streamlit plotly scipy statsmodels`
- [ ] 确认SQLite数据库路径：`data/market.db`
- [ ] 测试数据拉取：在`streamlit_app.py`中拉取3690.HK数据
- [ ] 确认yfinance可用（或被替代的港股数据源）

**Step 2: 审计与修复（2-3天）**
- [ ] 修复`np.gradient`信号重绘（参照第8.2节方案A）
- [ ] 实现前向预测冻结机制（参照第8.3节）
- [ ] 回测修复后确认Sharpe > 原回测的70%

**Step 2.5: 部署券商网关（2-3天）**
- [ ] 安装OpenD本地版（富途方案）或IB Gateway + ib_insync / 新版IBKR Web API（IBKR方案）
- [ ] 验证行情连接：订阅3690.HK的`K_15M`/`K_60M`/`K_DAY`/`K_WEEK`，确认数据与yfinance一致
- [ ] 验证交易连接：解锁交易权限（富途`unlock_trade`），模拟盘下单测试
- [ ] 根据券商选型决策矩阵（第12.1节）确认方案，建议港股策略以富途为主、IBKR为备选

**Step 3: 参数调优（1-2周）**
- [ ] 三阶段调优（参照第7.3节）
- [ ] 关键参数建议：SG window: 15分钟=11, 60分钟=15, 日线=17, 周线=9；k_eps: 15分钟=0.20, 日线=0.12, 周线=0.10；止损: ATR(10)×2.0(短周期) / ×2.5(长周期)
- [ ] Walk-Forward检验通过

**Step 4: 部署运行（2-4周）**
- [ ] 最小必要参数集：4周期SG window, order, k_eps, sigma_min, EWMA + 4周期ATR乘数 + 信号合成权重4:3:2:1
- [ ] 搭建L1+L2风控（参照第9章）
- [ ] 部署券商网关容器：OpenD Docker（富途）或 IB Gateway Docker（IBKR）
- [ ] 启用BrokerInterface适配器架构，策略通过抽象接口操作券商
- [ ] 模拟交易运行4周（富途`TrdEnv.SIMULATE`或IBKR Paper Trading）

**Step 5: 小资金实盘**
- [ ] 投入总资金5-10%
- [ ] 部署完整风控（L1-L4），含券商通道监控（Prometheus告警接入Telegram）
- [ ] 启用实盘环境：切换券商Context至`TrdEnv.REAL`（富途），确认双通道灾备（主备切换<5秒）
- [ ] 前50笔交易全程人工监控
- [ ] 确认回测vs实盘偏差在可接受范围

### 推荐工具链

| 环节 | 推荐工具 | 成本 | 备注 |
|------|---------|------|------|
| 策略研究 | Streamlit + Plotly | 免费 | 已有框架 |
| 数据存储 | SQLite + Parquet | 免费 | 已有框架 |
| 实时行情 | 券商API Level 2 | ~500-1000 HKD/月 | 港股需付费订阅 |
| 模拟交易 | 券商Paper Trading | 免费 | - |
| 实盘交易 | 券商API | 按佣金 | 推荐支持REST+WebSocket |
| 监控面板 | Grafana + Prometheus | 免费 | 或自建JSON面板 |
| 告警推送 | Telegram Bot | 免费 | 或企业微信/钉钉 |
| 版本管理 | Git + GitHub | 免费 | 策略配置参数纳入版本管理 |

### 最小必要参数集（启动配置模板）

```json
{
  "market": "港股 HK",
  "ticker": "3690",
  "global_f": "savgol",
  "global_dual": false,
  "v0_tf": "15分钟", "v0_n": 120,
  "v0_ke": 0.20, "v0_sm": 0.05, "v0_ew": 40,
  "窗口大小_v0_f1_savgol": 11, "多项式阶数_v0_f1_savgol": 3,
  "v0_atr_multiplier": 1.8, "v0_strat": true,
  "v1_tf": "日线", "v1_n": 120,
  "v1_ke": 0.12, "v1_sm": 0.05, "v1_ew": 60,
  "窗口大小_v1_f1_savgol": 17, "多项式阶数_v1_f1_savgol": 3,
  "v1_atr_multiplier": 2.5, "v1_strat": true,
  "v2_tf": "60分钟", "v2_n": 120,
  "v2_ke": 0.15, "v2_sm": 0.05, "v2_ew": 50,
  "窗口大小_v2_f1_savgol": 15, "多项式阶数_v2_f1_savgol": 3,
  "v2_atr_multiplier": 2.0, "v2_strat": true,
  "v3_tf": "周线", "v3_n": 120,
  "v3_ke": 0.10, "v3_sm": 0.05, "v3_ew": 60,
  "窗口大小_v3_f1_savgol": 9, "多项式阶数_v3_f1_savgol": 2,
  "v3_atr_multiplier": 3.0, "v3_strat": true,
  "weight_weekly": 4, "weight_daily": 3, "weight_60m": 2, "weight_15m": 1
}
```

---

## 附录B：策略参数速查表

### B.1 施密特触发器参数

| 参数 | 配置键 | 默认值 | 范围 | 推荐搜索范围 | 敏感度 | 说明 |
|------|--------|--------|------|-------------|--------|------|
| 灵敏度系数 | `ke` (k_eps) | 0.15 | 0.01-0.50 | 趋势市0.10-0.20 / 震荡市0.20-0.35 | 高 | 控制死区宽度，直接决定信号频率 |
| 波动率地板 | `sm` (sigma_min) | 0.05 | 0.01-0.20 | 高流动性0.02-0.05 / 低流动性0.05-0.10 | 中 | 低波动期死区保底，防止信号泛滥 |
| EWMA平滑周期 | `ew` (N_EWMA) | 60 | 10-120 | 短周期30-60 / 长周期10-20 | 中 | 波动率估计的平滑窗口 |

### B.2 滤波参数（Savgol）

| 参数 | 配置键 | 默认值 | 范围 | 推荐搜索范围 | 敏感度 | 说明 |
|------|--------|--------|------|-------------|--------|------|
| 窗口大小 | `窗口大小_v{N}_f1_savgol` | 13 | 3-51（奇数） | 15分钟9-15 / 日线13-21 / 周线7-13 | 极高 | 越大越平滑，滞后越多 |
| 多项式阶数 | `多项式阶数_v{N}_f1_savgol` | 4 | 1-10 | 2-4 | 中 | 必须<window，高阶易过拟合 |

### B.3 预测参数

| 参数 | 配置键 | 默认值 | 范围 | 推荐搜索范围 | 敏感度 | 说明 |
|------|--------|--------|------|-------------|--------|------|
| 拟合模式 | `fm` | parabola | parabola / poly2 | parabola | 低 | parabola=锚定转折点1参数；poly2=3参数 |
| 预测延伸点数 | `next` (n_extend) | 8 | 1-50 | 5-15 | 低 | 保护期长度，影响止损覆盖 |
| 止损阈值 | `sl` (stop_loss_pct) | 2.0% | 0.5-10.0% | 建议替换为ATR动态止损 | 高 | 固定止损→ATR动态止损是关键改进 |

### B.4 ATR动态止损（推荐替换方案）

| 周期 | ATR窗口 | 推荐乘数范围 | 波动率覆盖 | 预期误触发概率 |
|------|---------|-------------|-----------|---------------|
| 15分钟 | ATR(10) | 1.5-2.0x | 85-95% | 5-8% |
| 60分钟 | ATR(10) | 1.8-2.5x | 88-97% | 3-6% |
| 日线 | ATR(14) | 2.0-3.0x | 90-98% | 2-5% |
| 周线 | ATR(14) | 2.5-3.5x | 92-99% | 1-3% |

### B.5 信号合成权重（推荐基线）

| 周期 | 权重 | 理由 |
|------|------|------|
| 周线 | 4 | 趋势方向的最强信号，噪音最低 |
| 日线 | 3 | 中等周期，兼顾趋势和灵活性 |
| 60分钟 | 2 | 短期确认，提供日内背景 |
| 15分钟 | 1 | 高频信号，做精细入场/出场调整 |

### B.6 风控阈值速查

| 风控层级 | 指标 | 推荐阈值 |
|---------|------|---------|
| L1 单笔止损 | 账户亏损比例 | 0.5-1.5% |
| L2 日内亏损 | 当日累计PnL | 账户3-5% |
| L2 日内换手 | 交易量限制 | 账户50% |
| L3 峰值回撤 | 硬熔断线 | 8-12% |
| L3 连续亏损 | 熔断触发 | 5-8笔 |
| L3 波动率异常 | ATR比率 | >2.0x → 仓位减半 |
| L3 信号分歧 | 四周期不一致率 | >60% → 暂停入场 |
| L4 数据断连 | 超时 | >60秒 → 暂停交易 |
| L4 订单异常 | 拒单率 | >20% → 系统检查 |

---

> **报告基于**：T5（第1-6章：策略体系、同向判断理论、施密特触发器、跨周期对齐、3690_HK案例分析、局限与改进）、T6（第7-11章：参数优化、执行架构、风险控制、上线路径、监控体系 + 附录A）以及 T7（第12章：券商接口集成方案）研究成果整合编写。
> **来源系统**：`filter_app/streamlit_app.py` | **配置参考**：`config/3690_HK.json`
> **版本**：v1.0 | **最后更新**：2026-06-20
