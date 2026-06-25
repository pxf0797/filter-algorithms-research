# Sigma-Sampling Feasibility Report: Schmitt Trigger with EWMA Volatility

## 摘要

本报告分析施密特触发器（Schmitt Trigger）信号生成系统中的 **EWMA 波动率估计器**在短样本场景（20 bar）下的统计可靠性和执行可行性。

**核心结论**：在 20 bar 场景中，默认参数（`ewma_span=60`）因硬编码长度门槛 `n < ewma_span` 直接返回 `None`，不可执行。通过降低 `ewma_span` 至 15-20 可使代码执行，但代价是统计可靠性显著下降（RSE 16%-18%），信号误判风险升高。推荐在 20 bar 场景中使用参数组合 `{ewma_span=15, k_eps=0.12, sigma_min=0.08}`，并接受信号质量下降作为短样本的固有权衡。

---

## 1. 代码机制概述

### 1.1 数据依赖链

```
收盘价 (close) 
  → 滤波器 (filter) 
  → filtered (一阶差分: np.gradient(filtered, t)) → v
  → a (二阶差分: np.gradient(v, t))
  → σ(v) (EWMA 递归波动率) 
  → sig (施密特触发器)
```

关键中间量：
- **v**: 滤波器输出的一阶差分（速度），由 `np.gradient(filtered, t)` 计算
- **a**: v 的一阶差分（加速度），由 `np.gradient(v, t)` 计算
- **σ(v)**: v 的 EWMA 递归波动率估计
- **sig**: 基于 a 与 ε 比较的施密特触发信号

### 1.2 EWMA 波动率 σ(v)

递归定义：

$$
\begin{aligned}
\alpha &= \frac{2}{\text{ewma\_span} + 1} \\[4pt]
\mu_t(v) &= \alpha \cdot v_t + (1 - \alpha) \cdot \mu_{t-1}(v) \\[4pt]
\sigma^2_t(v) &= \alpha \cdot (v_t - \mu_t(v))^2 + (1 - \alpha) \cdot \sigma^2_{t-1}(v)
\end{aligned}
$$

初始条件：
- $\mu_0(v) = v_0$
- $\sigma_0(v) = 0.0$

**注意**：$\sigma_0 = 0$ 是一个有偏初始条件。对于递归波动率估计，波动率从零开始，需要经历约 $t_{95\%}$ 步才能收敛到真实水平。在此期间，$\sigma_t(v)$ 系统性低估真实波动率，导致迟滞带较窄，信号更容易触发。

### 1.3 施密特触发器 sig

$$
\begin{aligned}
\varepsilon_t &= k_{\text{eps}} \cdot \max(\sigma_t(v), \sigma_{\min}) \\[4pt]
\text{sig}_t &\in \{+1, -1, 0\} \quad \text{基于 } a_t \text{ 与 } \varepsilon_t \text{ 的符号比较}
\end{aligned}
$$

迟滞机制：
- 当 `a_t > +ε_t` 时，sig 翻转为 +1
- 当 `a_t < -ε_t` 时，sig 翻转为 -1
- 在 `[-ε_t, +ε_t]` 区间内保持前值

### 1.4 硬门槛

```python
if n < ewma_span:
    return None
```

默认 `ewma_span=60`，这意味着任何少于 60 bar 的输入数据直接返回无效信号。

### 1.5 UI 参数范围

| 参数 | 范围 | 默认 | 用途 |
|:--|:--:|:--:|:--|
| `ewma_span` | 10 - 120 | 60 | EWMA 有效窗口 |
| `k_eps` | 0.01 - 0.50 | 0.15 | 迟滞倍数 |
| `sigma_min` | 0.001 - 0.20 | 0.05 | 波动率地板 |
| `n_pts` | 20 - 300 | 120 | 获取 bar 数量 |

---

## 2. 数学分析

### 2.1 有效样本量 (ESS)

EWMA 波动率估计的有效样本量为：

$$
\text{ESS} = \sum_{k=0}^{\infty} \alpha(1-\alpha)^k \cdot \frac{1 - (1-\alpha)^{2k+2}}{1 - (1-\alpha)^2}
$$

简化后可得精确等式：

$$
\text{ESS} = \frac{2 - \alpha}{\alpha} = \text{ewma\_span}
$$

**结论**：EWMA 波动率估计的有效样本量精确等于 `ewma_span`。这一简洁结果意味着参数选择直接等价于统计可靠性选择。

### 2.2 收敛速度

半衰期（权重衰减50%）：

$$
t_{1/2} = \frac{\ln(0.5)}{\ln(1 - \alpha)}
$$

95% 收敛步数（权重累积至95%）：

$$
t_{95\%} = \frac{\ln(0.05)}{\ln(1 - \alpha)}
$$

### 2.3 估计精度

相对标准误差（RSE）近似：

$$
\text{RSE}(\hat{\sigma}) \approx \frac{1}{\sqrt{2 \cdot \text{ESS}}} \times 100\%
$$

### 2.4 各参数下的关键指标

| `ewma_span` | $\alpha$ | ESS | $t_{1/2}$ | $t_{95\%}$ | RSE($\hat{\sigma}$) |
|:--:|:--:|:--:|:--:|:--:|:--:|
| 60 | 0.0328 | 60 | 20.8 | 89.9 | 9.1% |
| 30 | 0.0645 | 30 | 10.4 | 45.1 | 12.9% |
| 20 | 0.0952 | 20 | 6.9 | 29.9 | 15.8% |
| 15 | 0.1250 | 15 | 5.2 | 22.4 | 18.3% |
| 10 | 0.1818 | 10 | 3.5 | 15.0 | 22.4% |

---

## 3. 20 bar 可行性结论

### 3.1 分层判断

| 层次 | 约束 | 准则 | 最小 `ewma_span` |
|:--|:--|:--|:--:|
| **A - 代码执行** | $n \geq \text{ewma\_span}$ | 硬编码门槛 | $\leq 20$ |
| **B - 统计可靠性** | $\text{RSE} < 20\%$ | $\text{ESS} \geq 13$ | $\leq 20$ (临界) |
| **C - 信号可靠性** | 初始化偏差 + 迟滞锁定 | 无法量化硬门槛 | 越小风险越高 |

### 3.2 各参数方案在 20 bar 下的综合评估

| `ewma_span` | 代码执行 | RSE | 统计质量 | 初始化偏差 | 综合结论 |
|:--:|:--:|:--:|:--:|:--:|:--:|
| **60 (默认)** | ❌ | N/A | N/A | N/A | 不可行 |
| **30** | ❌ | 12.9% | 良好 | 显著 ($n < t_{95\%}=45$) | 不可行 |
| **20** | ✅ | 15.8% | 可接受 | 中等 ($n < t_{95\%}=30$) | 临界可用 |
| **15** | ✅ | 18.3% | 勉强 | 较小 ($n \approx t_{95\%}=22$) | 可行（有警告） |
| **10** | ✅ | 22.4% | 差 | 小 ($n > t_{95\%}=15$) | 不可靠 |

### 3.3 最终结论

> **20 bar 场景：通过调整参数可使代码执行并获得有意义的信号，但统计可靠性已接近或处于不可接受区域。**

- **默认参数 (ewma_span=60)**: 直接返回 None，不适用
- **推荐参数**: `ewma_span=15, k_eps=0.12, sigma_min=0.08`
- **信号质量预期**: RSE≈18%，意味着波动率估计的变异系数接近 1/5，信号阈值 ($\varepsilon_t$) 的稳定性较差
- **初始化偏差**: $\sigma_0=0$ 导致前 10-15 bar 的波动率系统性低估，实际有效样本更少

---

## 4. 参数调整建议

### 4.1 20 bar 推荐配置

```
ewma_span = 15
k_eps     = 0.12   （降低迟滞倍数，补偿较紧的迟滞带）
sigma_min = 0.08   （提高波动率地板，补偿初始化阶段的低估）
```

调整逻辑：
1. **`ewma_span = 15`**：满足 $n \geq \text{span}$ 的硬门槛，且 $n \approx t_{95\%}$，收敛基本完成
2. **`k_eps = 0.12`**：默认 0.15 是基于 60 span 校准的。当 span 降至 15 后，$\sigma_t(v)$ 的波动更大，降低 $k_{\text{eps}}$ 可部分抵消过度敏感
3. **`sigma_min = 0.08`**：提高地板值可缓解 $\sigma_0=0$ 初始化的低估效应，尤其在前 10 bar

### 4.2 可选改进方向（不修改代码）

若无法修改实现，可通过预处理改善信号质量：

1. **数据填充**：如果可能，在 20 bar 之前拼接历史数据作为 EWMA 的 warm-up，计算 sig 时从第 15 bar 开始输出
2. **信号后处理**：对输出的 sig 序列加时间衰减权重，前 10 个信号权重减半
3. **多时间尺度**：同时计算 20 bar 和 60 bar 的信号，仅在两者一致时触发

### 4.3 设计建议（可修改实现时）

1. **增加 warm-up 参数**：让调用方指定冷启动丢弃的 bar 数（默认等于 ewma_span），而非硬编码为 `n < ewma_span`
2. **初始化偏置校正**：改用 $\sigma_0(v) = \text{std}(v_{1:k})$ 而非 $\sigma_0=0$
3. **迟滞带自适应**：根据实际有效样本量动态调整 $k_{\text{eps}}$
4. **输出置信度**：额外返回 RSE 估计值，供下游决策层判断信号可信度

---

## 5. 总结决策树

```
输入 n=20 bar
├─ ewma_span=60 → ❌ 代码拒绝 (n < span)
├─ ewma_span=30 → ❌ 代码拒绝 (n < span)
├─ ewma_span=20 → ✅ 执行, RSE=16%, 初始化偏差中等
│                 ⚠️ 统计质量临界可用, 信号有一定参考价值
├─ ewma_span=15 → ✅ 执行, RSE=18%, 初始化偏差较小
│                 ✅ 推荐方案, 配合调整 k_eps 和 sigma_min
└─ ewma_span=10 → ✅ 执行, RSE=22%, 无初始化偏差问题
                  ❌ 统计质量太差, 信号不可靠
```

---

## 6. 附录：关键代码引用

文件路径：`/Users/xfpan/claude/filter_research/streamlit/streamlit_app.py`

| 功能 | 说明 |
|:--|:--|
| `_schmitt_trigger()` | 施密特触发器主函数，包含 EWMA 波动率 + 迟滞信号 |
| `alpha = 2 / (ewma_span + 1)` | EWMA 衰减因子定义 |
| `sigma_sq = alpha * diff_sq + (1 - alpha) * sigma_sq` | EWMA 递归方差更新 |
| `eps = k_eps * max(sigma, sigma_min)` | 迟滞阈值 |
| `if n < ewma_span: return None` | 硬门槛，默认 n < 60 不输出 |
| `mu = alpha * v + (1 - alpha) * mu` | EWMA 均值递归更新 |
| `v = np.gradient(filtered, t)` | 一阶差分（速度） |
| `a = np.gradient(v, t)` | 二阶差分（加速度） |
