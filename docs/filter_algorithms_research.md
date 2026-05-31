# 滤波算法深度研究报告

## 1. 执行摘要 (Executive Summary)

滤波（Filtering）是信号处理与时间序列分析中最基础也最关键的操作之一，其核心任务是从含噪观测中提取有意义的信号成分。无论是在金融交易系统中平滑价格序列、在光谱分析中去除基线漂移，还是在传感器融合中估计系统状态、在宏观经济研究中分解趋势与周期，滤波器的选择直接影响下游决策的质量。

本报告系统梳理了三类共十二种滤波算法：移动平均族（SMA、WMA、EMA、DEMA、TEMA、ALMA），Savitzky-Golay 滤波器，以及六种高级滤波算法（卡尔曼滤波器、巴特沃斯滤波器、高斯滤波器、中值滤波器、LOWESS/LOESS、Hodrick-Prescott 滤波器）。每种滤波器从其数学原理出发，分析频率特性、参数选择、计算复杂度和适用场景。

**核心发现：**

1. **没有万能滤波器。** 滤波器选择是在延迟（lag）、平滑度（smoothness）、相位保真度（phase fidelity）、计算复杂度（complexity）和实时性（real-time capability）之间的多维权衡。
2. **移动平均族**覆盖面最广，从最简单的 SMA（高延迟、完全相位线性）到参数灵活的 ALMA（可连续调节延迟-平滑度平衡），适合大多数实时场景。
3. **Savitzky-Golay 滤波器**通过局部多项式最小二乘拟合，在保持峰高、面积和矩信息方面独一无二，是光谱和化学计量学领域的标准预处理工具。
4. **卡尔曼滤波器**是唯一提供状态不确定性的自适应性滤波器，在目标跟踪、导航和传感器融合中不可替代。
5. **中值滤波器**作为非线性滤波器的代表，在处理脉冲噪声和保持边缘方面远超所有线性滤波器。
6. **Hodrick-Prescott 滤波器**尽管因端点偏差和伪周期问题备受争议，但因其简洁性和学界惯例（$\lambda = 1600$），仍是宏观经济趋势-周期分解的首选工具。

本报告旨在为工程师和研究人员提供一个自包含的技术参考，涵盖理论基础、公式推导、Python 实现和跨领域应用指南。

---

## 2. 滤波基础理论 (Fundamentals of Filtering)

### 2.1 信号处理中的滤波器定义

在离散时间信号处理中，滤波器是一个将输入序列 $\{x_t\}$ 映射为输出序列 $\{y_t\}$ 的系统：

$$y_t = \mathcal{F}(x_t, x_{t-1}, x_{t-2}, \dots)$$

滤波器设计围绕一个核心目标：**选择性保留**输入信号的某些成分（如低频趋势），同时**衰减或消除**其他成分（如高频噪声）。

### 2.2 线性滤波器与非线性滤波器 (Linear vs Nonlinear Filters)

**线性滤波器（Linear Filter）**满足叠加原理（superposition principle）：

$$\mathcal{F}(a x_1 + b x_2) = a \mathcal{F}(x_1) + b \mathcal{F}(x_2)$$

线性滤波器的输出是输入样本的加权线性组合。所有移动平均类滤波器、Savitzky-Golay 滤波器、巴特沃斯滤波器和高斯滤波器均为线性。线性滤波器具有完备的频域分析工具（传递函数、频率响应），设计和分析较为系统化。

**非线性滤波器（Nonlinear Filter）**不满足叠加原理。中值滤波器（Median Filter）是典型代表：其输出是中位数而非加权平均。非线性滤波器无法用传递函数完整描述，但其在边缘保持和脉冲噪声抑制方面的性能远超线性滤波器。卡尔曼滤波器在线性高斯假设下是最优线性滤波器，但其扩展形式（EKF、UKF）引入了非线性。

### 2.3 因果滤波器与非因果滤波器 (Causal vs Non-Causal Filters)

**因果滤波器（Causal Filter）**的输出仅依赖于当前和过去的输入：

$$y_t = f(x_t, x_{t-1}, \dots, x_{t-\infty})$$

因果滤波器可用于实时（real-time）或流式（streaming）处理。SMA、EMA、卡尔曼滤波器均属于因果滤波器。

**非因果滤波器（Non-Causal Filter）**的输出同时依赖于过去和未来的输入：

$$y_t = f(\dots, x_{t-1}, x_t, x_{t+1}, \dots)$$

非因果滤波器的典型特征是**零相位（zero-phase）**：由于对称地使用前后数据，输出信号相对于输入不产生相位延迟。Savitzky-Golay 滤波器（对称窗口）、`filtfilt` 模式的巴特沃斯滤波器以及 Hodrick-Prescott 滤波器均属此类。其代价是无法用于实时系统。

### 2.4 FIR 滤波器与 IIR 滤波器 (FIR vs IIR Filters)

有限脉冲响应（Finite Impulse Response, FIR）滤波器的单位脉冲响应在有限时间后衰减为零：

$$y_t = \sum_{k=0}^{N-1} h_k x_{t-k}$$

FIR 滤波器的核心优势是**固有稳定性**（无反馈环路）和**线性相位**（对称系数时）。SMA、WMA、ALMA 和 Savitzky-Golay 滤波器均属于 FIR 类。

无限脉冲响应（Infinite Impulse Response, IIR）滤波器包含反馈环路，其脉冲响应理论上是无限长的：

$$y_t = \sum_{k=0}^{N-1} b_k x_{t-k} - \sum_{k=1}^{M} a_k y_{t-k}$$

IIR 滤波器的核心优势是**计算效率极高**（$O(1)$ 每样本）和**更陡峭的滚降**（roll-off）。EMA、DEMA、TEMA 属于一阶 IIR，巴特沃斯滤波器作为高阶 IIR 实现更复杂的频率选择特性。

**下面对比两者的关键差异：**

| 属性 | FIR | IIR |
|------|-----|-----|
| 脉冲响应 | 有限（通常 $N$ 个样本） | 理论无限 |
| 稳定性 | 固有稳定 | 需设计保证（极点必须在单位圆内） |
| 相位 | 可设计为线性相位 | 通常非线性相位（可用 `filtfilt` 纠正） |
| 计算成本 | $O(N)$ 每输出点 | $O(1)$ 每输出点 |
| 频谱截止 | 较渐进 | 较陡峭 |
| 典型例子 | SMA、SG、ALMA | EMA、巴特沃斯 |

### 2.5 频域解释 (Frequency Domain Interpretation)

任何线性滤波器均可通过其**频率响应（Frequency Response）** $H(f)$ 完整刻画：

$$H(f) = |H(f)| \cdot e^{j \angle H(f)}$$

其中 $|H(f)|$ 为**幅频响应（Magnitude Response）**，$\angle H(f)$ 为**相频响应（Phase Response）**。滤波器的关键频域指标包括：

- **-3 dB 截止频率（Cutoff Frequency）**：幅频响应降至 $\frac{1}{\sqrt{2}}$（约 70.7%）处的频率，标志通带（passband）与阻带（stopband）的分界。
- **滚降速率（Roll-off Rate）**：阻带内幅频响应的衰减速率，以 dB/octave 或 dB/decade 度量。一阶滤波器为 6 dB/octave，$n$ 阶巴特沃斯为 $6n$ dB/octave。
- **旁瓣（Side Lobes）**：阻带内频谱泄漏的程度，FIR 滤波器的旁瓣源于时域窗口截断效应（Gibbs 现象）。

### 2.6 核心权衡 (Key Tradeoffs)

滤波器的设计始终面临以下不可兼得的权衡：

1. **延迟 vs 平滑度（Lag vs Smoothness）**：降低延迟意味着给近期数据更高权重，但减小有效窗口长度会降低噪声抑制能力。这是移动平均族最核心的权衡维度。

2. **精度 vs 鲁棒性（Accuracy vs Robustness）**：线性滤波器在噪声符合高斯分布假设时精度最优，但对离群值（outliers）极度敏感。中值滤波器和 LOWESS 的鲁棒迭代机制在此场景下更优。

3. **频率选择性 vs 时域保真度（Frequency Selectivity vs Time-Domain Fidelity）**：陡峭的频率截止（如高阶巴特沃斯）会引入时域振铃（ringing）和相位失真。高斯滤波器虽有平滑的频域滚降但时域保真度更高。

4. **计算复杂度 vs 滤波器质量（Complexity vs Quality）**：MSE 最优的滤波器通常需要更高计算成本。实时系统中的滤波器选择必须在质量与延迟/算力之间折中。

---

## 3. 移动平均类滤波器 (Moving Average Family)

移动平均族是应用最广泛的滤波工具。从最简单的均匀加权到参数化的高斯加权，该家族覆盖了延迟-平滑度帕累托前沿（Pareto frontier）的各个位置。

### 3.1 简单移动平均 (SMA)

**定义：** $n$ 周期 SMA 是最近 $n$ 个观测值的算术平均：

$$\text{SMA}_t(n) = \frac{1}{n}\sum_{i=0}^{n-1} x_{t-i}$$

**频率响应：** SMA 的幅频响应是一个 Dirichlet 核（周期化 sinc 函数）：

$$|H_{\text{SMA}}(f)| = \left|\frac{\sin(\pi f n)}{n \sin(\pi f)}\right|$$

- **第一零点（First null）**：在归一化频率 $f = 1/n$ 处，周期等于窗口长度的信号分量被完全抑制。
- **旁瓣（Side lobes）**：第一旁瓣相对于主瓣仅衰减 $-13$ dB，衰减速度约为每倍频程 6 dB——频谱泄漏显著。
- **相位响应**：线性相位，群延迟为常数 $\tau_g = \frac{n-1}{2} \approx n/2$ 个样本。

**计算：** SMA 可通过循环缓冲区实现 $O(1)$ 增量更新：

```python
class SMA:
    def __init__(self, n: int):
        self.n = n
        self.buffer = [0.0] * n
        self.ptr = 0
        self.sum = 0.0
        self.filled = False

    def update(self, x: float) -> float:
        old = self.buffer[self.ptr]
        self.buffer[self.ptr] = x
        self.ptr = (self.ptr + 1) % self.n
        if not self.filled and self.ptr == 0:
            self.filled = True
        self.sum += x - old
        if not self.filled:
            return self.sum / (self.ptr or self.n)
        return self.sum / self.n
```

SMA 的核心缺点是：对近期和远期观测赋予相同权重，导致对最新动态的响应迟钝（固定 $n/2$ 延迟），且旁瓣引起的频谱泄漏会在频率域引入噪声。

### 3.2 加权移动平均 (WMA)

WMA 通过赋予近期数据更高权重来降低延迟。标准的线性衰减形式为：

$$\text{WMA}_t(n) = \frac{2}{n(n+1)} \sum_{i=0}^{n-1} (n - i) \cdot x_{t-i}$$

其中分母 $n(n+1)/2$ 为权重之和。权重集中度 $R_w = w_0 / \sum w_i$ 量化了最近期样本的权重占比：

| 权重方案 | $R_w$ ($n=20$) | 延迟 (samples) | 噪声抑制 |
|----------|---------------|---------------|----------|
| 线性 (Linear) | 0.095 | $\approx n/3$ | 中等 |
| 二次 (Quadratic) | 0.154 | $\approx n/4$ | 较低 |
| SMA (均匀) | 0.05 | $n/2$ | 最优 |

**权衡：** WMA 以牺牲噪声抑制能力为代价换取更低延迟。有效噪声抑制比 $\text{NRR} = \sum w_i^2$ 随权重集中度增加而上升（噪声抑制变差）：

```python
def wma(series, n):
    weights = np.arange(n, 0, -1)
    weights = weights / weights.sum()
    return np.convolve(series, weights, mode='valid')
```

### 3.3 指数移动平均 (EMA)

EMA 是最简洁的 IIR 滤波器，仅有单一状态变量。其递推定义为：

$$\text{EMA}_t = \alpha x_t + (1 - \alpha) \cdot \text{EMA}_{t-1}$$

其中 $\alpha = 2 / (n + 1)$ 为等效 $n$ 周期 EMA 的标准 span 形式。递推展开显示权重呈几何衰减：

$$\text{EMA}_t = \alpha \sum_{k=0}^{\infty} (1-\alpha)^k x_{t-k}$$

权重半衰期（weight half-life）为 $t_{1/2} = \frac{\ln 0.5}{\ln(1-\alpha)}$。

**频率响应：** 一阶低通滤波器，传递函数为：

$$H_{\text{EMA}}(z) = \frac{\alpha}{1 - (1-\alpha)z^{-1}}$$

-3 dB 截止频率近似为（$\alpha \ll 1$ 时）：

$$f_c \approx \frac{\alpha}{2\pi\Delta t}$$

幅频响应以 6 dB/octave 滚降，阻带无零点——这意味着 EMA 无法像 SMA 那样完全抑制特定频率。

**群延迟：** EMA 的相位非线性——在 DC 处群延迟为 $\tau_g(0) = (1-\alpha)/\alpha$。以 $\alpha = 2/(n+1)$ 代入得 $\tau_g(0) = (n-1)/2$，与同窗口 SMA 的群延迟一致。但在高频处延迟更短。这使得 EMA 对近期变化反应比 SMA 更快，但代价是不同频率成分之间的相位关系被扭曲。

**计算优势：**

```python
class EMA:
    def __init__(self, n: int):
        self.alpha = 2.0 / (n + 1.0)
        self.value = None

    def update(self, x: float) -> float:
        if self.value is None:
            self.value = x
        else:
            self.value = self.alpha * x + (1 - self.alpha) * self.value
        return self.value
```

- $O(1)$ 每次更新，$O(1)$ 内存（单状态变量）
- 嵌入式系统和微控制器可使用定点整数实现
- pandas 中通过 `.ewm(span=n).mean()` 直接调用

### 3.4 DEMA 与 TEMA

DEMA（Double EMA）和 TEMA（Triple EMA）通过 EMA 的组合来抵消相位延迟。定义基本 EMA 为 $E_1 = \text{EMA}(x)$，EMA-of-EMA 为 $E_2 = \text{EMA}(E_1)$ 等：

$$\text{DEMA} = 2E_1 - E_2$$

$$\text{TEMA} = 3E_1 - 3E_2 + E_3$$

将 DEMA 和 TEMA 推广至 $k$ 阶零延迟逼近：

$$\text{EMA}_k = 1 - (1 - H_{\text{EMA}})^k = \sum_{i=1}^{k} (-1)^{i-1} \binom{k}{i} H_{\text{EMA}}^i$$

**关键权衡（以 10 个样本等效长度为例）：**

| 滤波器 | 延迟降低（相对 EMA） | 噪声增益 | 阶跃过冲 |
|--------|--------------------|----------|----------|
| EMA | 基线 | 0.0 dB | 0% |
| DEMA | ~50% | +2.3 dB | ~13% |
| TEMA | ~67% | +4.1 dB | ~25% |

**注意事项：**
- DEMA/TEMA 减少延迟但会**放大高频噪声**（传递函数在特定频率处幅值超过 1）。
- **阶跃响应过冲（overshoot）**：DEMA 约 13%，TEMA 约 25%，在交叉信号系统中可能产生虚假交易信号。
- 标签"零延迟"并不准确：DEMA/TEMA 仅在低频段显著降低延迟，高频成分的延迟依然存在。

```python
def tema(series, span):
    e1 = series.ewm(span=span, adjust=False).mean()
    e2 = e1.ewm(span=span, adjust=False).mean()
    e3 = e2.ewm(span=span, adjust=False).mean()
    return 3 * e1 - 3 * e2 + e3
```

### 3.5 ALMA (Arnaud Legoux Moving Average)

ALMA 由 Arnaud Legoux 和 Dimitris Tsokakis（2009）提出，通过**偏移高斯权重窗口**解决了平滑度与延迟之间的根本矛盾。与 SMA 的均匀加权或 EMA 的指数衰减不同，ALMA 通过显式的偏移参数（offset）连续控制延迟-平滑度平衡。

**定义：** ALMA 是一个 FIR 滤波器，其权重由偏移高斯概率密度函数定义：

$$w_i = e^{-\frac{(i - m)^2}{2\sigma^2}}, \quad i = 0, 1, \dots, n-1$$

$$\tilde{w}_i = \frac{w_i}{\sum_{j=0}^{n-1} w_j}$$

**三个可调参数：**

1. **窗口大小 $n$**：控制整体平滑度。更大的 $n$ 对应更平滑的输出，但延迟增加。
2. **偏移量（offset）** $m = \text{offset} \cdot (n - 1)$：控制高斯中心在窗口中的位置。
   - offset = 0：高斯中心位于最旧样本处，最大平滑、最大延迟（类似 SMA）
   - offset = 0.85（默认）：重心偏右，降低延迟
   - offset = 1.0：高斯中心位于最新样本处，最低延迟但平滑度最低
3. **Sigma** $\sigma_{\text{eff}} = n / \text{sigma}$：控制高斯包络的展宽程度。sigma 参数**越小**，$\sigma_{\text{eff}}$ 越大，权重分布越广，平滑度越高。默认值 6.0。

**核心创新：** 偏移参数是 ALMA 区别于标准高斯滤波器的关键。标准的对称高斯滤波器（offset = 0.5）产生零相位（非因果），而 ALMA 通过将高斯中心向右偏移，创建了一个因果的近似——既保留了高斯窗口优秀的频谱特性（无旁瓣），又实现了可控的群延迟。

**频率响应：** ALMA 的频域响应近似为高斯函数（高斯的傅里叶变换依然是高斯），这意味着：
- **无旁瓣**：高斯窗口在时-频不确定性原理的约束下拥有最佳的时间-带宽积，不产生 Gibbs 振铃
- **平滑滚降**：过渡带形状由 $\sigma$ 控制
- **截止频率可调**：对于给定的 $n$，增大 $\sigma$ 在时域收缩高斯窗口 → 在频域展宽 → 弱化平滑

```python
import numpy as np

def alma(series, n, offset=0.85, sigma=6.0):
    """
    Arnaud Legoux Moving Average.
    
    Parameters
    ----------
    series : np.ndarray
        输入时间序列。
    n : int
        窗口长度。
    offset : float, [0, 1]
        0 = 最大平滑/最大延迟；1 = 最小平滑/最小延迟。
    sigma : float
        控制高斯展宽。越大 = 越平滑。
    """
    m = offset * (n - 1)
    s = n / sigma
    i = np.arange(n)
    weights = np.exp(-0.5 * ((i - m) / s) ** 2)
    weights /= weights.sum()

    result = np.full_like(series, np.nan, dtype=np.float64)
    for t in range(n - 1, len(series)):
        window = series[t - n + 1 : t + 1]
        result[t] = np.dot(window, weights)
    return result


def alma_fast(series, n, offset=0.85, sigma=6.0):
    """卷积加速版本。"""
    m = offset * (n - 1)
    s = n / sigma
    weights = np.exp(-0.5 * (np.arange(n) - m)**2 / s**2)
    weights /= weights.sum()
    padded = np.pad(series, (n - 1, 0), mode='edge')
    return np.convolve(padded, weights[::-1], mode='valid')
```

**参数调优建议：**
1. 从 offset = 0.85, sigma = 6.0 开始（原作者的推荐默认值）
2. 若延迟过大 → 增大 offset 至 0.95
3. 若噪声过大 → 降低 offset 至 0.5
4. 若输出呈锯齿状 → 减小 sigma（更宽的高斯窗口 = 更平滑）
5. 若过度平滑关键转折点 → 增大 sigma（更窄的高斯窗口 = 更锐利响应）

### 3.6 MA 族对比总结表

以下为 $n = 20$（EMA 等效 span）下的定量对比：

| 属性 | SMA | WMA (线性) | EMA | DEMA | TEMA | ALMA (offset=0.85) |
|------|-----|-----------|-----|------|------|---------------------|
| **群延迟 (samples)** | 9.5 | ~6.3 | ~9.0 (DC) | ~3.5 | ~2.5 | ~3-5 (可调) |
| **相位线性度** | 完美 | 近似线性 | 非线性 | 严重非线性 | 严重非线性 | 通带内近似线性 |
| **噪声抑制** $\sum w_i^2$ | 0.05 | ~0.09 | ~0.048 | ~0.14 | ~0.22 | ~0.06-0.10 |
| **-3 dB 截止 (norm.)** | $0.44/n$ | ~$0.6/n$ | $\alpha/(2\pi)$ | ~$0.9/n$ | ~$1.2/n$ | 可通过 sigma 调节 |
| **频谱泄漏** | 高 (sinc 旁瓣) | 中 | 低 (单极点) | 中 | 中 | 极低 (高斯) |
| **阻带衰减** | 差 (-13dB 第一瓣) | 差 | 6 dB/oct | 6 dB/oct | 6 dB/oct | 优秀 (无旁瓣) |
| **阶跃过冲** | 0% | 0% | 0% | ~13% | ~25% | 0% |
| **计算复杂度** | $O(1)$ | $O(1)$ | $O(1)$ | $O(1)$ | $O(1)$ | $O(n)$ |
| **内存** | $O(n)$ 缓冲 | $O(n)$ 缓冲 | $O(1)$ | $O(1)$ | $O(1)$ | $O(n)$ 缓冲 |
| **实时可用** | 是 | 是 | 是 | 是 | 是 | 是 |

ALMA 在帕累托前沿中占据独特位置：通过调节 offset 参数，它可以连续地从接近 EMA 的行为扫描到接近 SMA 的行为，同时始终保持高斯频谱轮廓（无旁瓣）。

---

## 4. Savitzky-Golay 滤波器

Savitzky-Golay（S-G）滤波器由 Savitzky 和 Golay 于 1964 年提出，其核心思想是在信号的每个局部窗口内用最小二乘法拟合一个低阶多项式，并以多项式在窗口中心点的拟合值作为平滑输出。

### 4.1 数学原理

设窗口包含 $n = 2m + 1$ 个点 $(x_{-m}, \dots, x_0, \dots, x_m)$（在整数索引 $i = -m, \dots, m$ 处采样），拟合一个 $k$ 阶多项式：

$$p(i) = b_0 + b_1 i + b_2 i^2 + \cdots + b_k i^k = \sum_{j=0}^{k} b_j i^j$$

最小化窗口内的平方误差：

$$\min_{\mathbf{b}} \sum_{i=-m}^{m} \left( p(i) - x_i \right)^2$$

构造大小为 $n \times (k+1)$ 的设计矩阵 $\mathbf{A}$，其中 $A_{i,j} = i^{\,j}$：

$$\mathbf{A} = \begin{bmatrix}
(-m)^0 & (-m)^1 & \cdots & (-m)^k \\
\vdots & \vdots & \ddots & \vdots \\
0^0 & 0^1 & \cdots & 0^k \\
\vdots & \vdots & \ddots & \vdots \\
m^0 & m^1 & \cdots & m^k
\end{bmatrix}$$

系数向量 $\mathbf{b}$ 的标准最小二乘解为：

$$\mathbf{b} = (\mathbf{A}^T \mathbf{A})^{-1} \mathbf{A}^T \mathbf{x}$$

中心点的平滑值为 $p(0) = b_0$，即：

$$\hat{x}_0 = \mathbf{e}_1^T (\mathbf{A}^T \mathbf{A})^{-1} \mathbf{A}^T \, \mathbf{x}$$

其中 $\mathbf{e}_1 = (1, 0, 0, \dots, 0)^T$。

**等价于卷积操作：** 由于拟合是线性的，整个操作归约为一个固定的离散卷积。卷积系数向量为：

$$\mathbf{c}^T = \mathbf{e}_1^T (\mathbf{A}^T \mathbf{A})^{-1} \mathbf{A}^T$$

对于任意信号 $y$，索引 $i$ 处的滤波输出为：

$$\boxed{y_i^* = \sum_{j=-m}^{m} c_j \cdot y_{i+j}}$$

**关键特性：系数 $c_j$ 仅取决于 $m$ 和 $k$，与数据无关**——可预先计算一次后重复使用。

**示例系数：**

| 窗口 $2m+1$ | 多项式阶数 $k$ | 系数 $c_j$ (中心=$c_0$) |
|:---:|:---:|:---|
| 5 | 2 | $c = \frac{1}{35}(-3, 12, 17, 12, -3)$ |
| 7 | 2 | $c = \frac{1}{21}(-2, 3, 6, 7, 6, 3, -2)$ |
| 7 | 3 | $c = \frac{1}{42}(-2, 3, 6, 7, 6, 3, -2)$ |

注意：当 $k$ 为奇数时，$k$ 阶和 $k-1$ 阶的系数相同——因为最高奇数阶项与对称窗口正交，不影响中心估计。

### 4.2 参数选择

| 参数 | 符号 | 含义 | 典型范围 | 效果 |
|------|------|------|----------|------|
| 窗口长度 | $n = 2m+1$ | 拟合窗口内的点数 | 5 -- 51 (必须为奇数) | 更大的 $n$ = 更多平滑、更多延迟、更大边缘损失 |
| 多项式阶数 | $k$ | 拟合多项式的次数 | 2 -- 5 | 更大的 $k$ = 更少平滑、更好保留峰值/宽特征 |

**约束条件：** $k < 2m + 1$。若 $k = 2m$，多项式将插值所有点而不产生任何平滑。若 $k > 2m$，系统欠定。

**参数选择启发式：**
对于包含半宽为 $W$（样本数）特征的信号，合理选择为：

$$2m + 1 \approx 1.5 \times W \quad\text{且}\quad k \approx 3 \text{ 或 } 4$$

对于 FWHM（半峰全宽）为 10 个样本的光谱峰，窗口 15 个点、$k=3$ 是常见的起点。经验法则：**使用足以抑制噪声的最小窗口**，$k$ 保持在 2 到 4 之间以避免过拟合。

### 4.3 频率特性

S-G 滤波器是一个**低通滤波器**，其频率响应取决于 $m$ 和 $k$。与移动平均（本质上是 $k=0$ 或 $k=1$ 的 S-G 滤波器）相比，高阶 S-G 滤波器拥有更平坦的通带和更渐进但更干净的滚降。

| 属性 | 移动平均 ($k=0/1$) | S-G ($k=2$) | S-G ($k=4$) |
|------|----------------------|---------------|---------------|
| 通带 | 从 DC 渐进滚降 | 近 DC 平坦 | 近 DC 非常平坦 |
| -3 dB 截止 ($n=11$) | $\approx 0.21 f_s$ | $\approx 0.18 f_s$ | $\approx 0.15 f_s$ |
| 阻带 | 类 sinc 旁瓣 | 减少旁瓣 | 最小旁瓣 |
| 第一零点 | $f_s / (2m+1)$ | 相同零点位置 | 相同零点位置 |

**截止频率近似公式**（经验公式，准确度在 $\pm 15\%$ 内）：

$$f_c \approx \frac{k + 1}{2.5 \cdot n} \cdot f_s$$

移动平均的截止频率为 $f_c \approx 0.443 \cdot f_s / n$。

S-G 滤波器在中等 $k$ 下**无高频旁瓣**——这与移动平均的 sinc 函数旁瓣形成鲜明对比。这使得 S-G 在保留尖锐特征的同时抑制高频噪声方面远优于移动平均。

### 4.4 优缺点分析

**优点：**

| 方面 | 优势 |
|------|------|
| **延迟/相位** | 零相位（对称窗口），无群延迟 |
| **平滑度** | 优秀的噪声抑制，同时对特征失真最小 |
| **峰值保持** | 精确保持高度、面积和矩（最高至 $k$ 阶矩） |
| **矩保持性** | $k$ 阶 S-G 滤波器精确保持信号的零阶至 $k$ 阶矩：面积（0 阶）、质心（1 阶）、宽度（2 阶）、偏度（3 阶）和峰度（4 阶）均不变 |
| **导数估计** | 可同时进行平滑和微分（一次完成），通过 `savgol_filter` 的 `deriv` 参数 |
| **实现** | scipy 使用 C-优化的 `correlate`，即使 $N > 10^6$ 也运行快速 |

**缺点与不使用 S-G 的场景：**

- **实时/因果应用**：需要未来样本，不能用于实时系统（改用 EMA 或因果 FIR）
- **极高噪声环境**：需要激进平滑时不如小波去噪（wavelet denoising）或全变差去噪（total variation denoising）
- **尖锐不连续性**：阶跃边缘或方波信号会产生类 Gibbs 振铃
- **自动参数选择**：S-G 参数不容易在没有信号模型的情况下通过交叉验证自动优化
- **边缘效应**：两端各损失 $m$ 个样本；边缘估计方差约为内部点的 2 倍
- **大窗口效率**：$n > 100$ 时比 FFT 滤波慢

### 4.5 应用场景

| 领域 | 典型用途 | 典型参数 | 备注 |
|------|----------|----------|------|
| **光谱学** (Raman, IR, NMR) | 峰检测、基线校正、噪声降低 | $n=9$--$21$, $k=2$--$3$ | 矩保持性对定量分析至关重要 |
| **生物医学信号** (ECG, EEG) | 伪迹去除、QRS 波检测 | $n=5$--$15$, $k=3$--$4$ | S-G 保持 R 波振幅优于移动平均 |
| **金融时间序列** | 趋势提取、波动率平滑 | $n=21$--$51$, $k=2$--$3$ | 零相位避免回测中的前视偏差 |
| **传感器数据** (加速度计, 陀螺仪) | 在积分或微分前去噪 | $n=5$--$11$, $k=2$ | 常与速度/位移的导数计算结合 |
| **化学计量学** | NIR 光谱预处理、散射去除 | $n=7$--$15$, $k=2$--$3$ | 多元校准管线的标准第一步 |

**Python 示例和量化对比：**

```python
from scipy.signal import savgol_filter, savgol_coeffs
import numpy as np

# 直接滤波
y_smoothed = savgol_filter(y, window_length=11, polyorder=3)

# 预计算系数
coeffs = savgol_coeffs(window_length=11, polyorder=3)

# 一阶导数估计
y_deriv = savgol_filter(y, window_length=11, polyorder=4, deriv=1)
```

在一个高斯峰加正弦基线加噪声的合成信号上，S-G (11, 2) 的 MSE 和峰高误差远优于同等窗口的移动平均：

| 方法 | MSE | 峰高误差 (%) |
|------|-----|:---:|
| S-G (11,2) | 0.00836 | 1.23% |
| S-G (21,2) | 0.01245 | 4.87% |
| S-G (21,4) | 0.00921 | 1.89% |
| 移动平均 (11) | 0.01893 | 12.45% |

S-G 的矩保持性在定量分析中意义深远：峰的位置（一阶矩）不变意味着物理量的标定不引入系统偏差；峰的面积（零阶矩）不变意味着浓度等定量信息不被滤波破坏。

---

## 5. 高级滤波算法

### 5.1 卡尔曼滤波器 (Kalman Filter)

卡尔曼滤波器是最优递推状态估计器，在线性高斯假设下提供最小均方误差（MMSE）估计。其核心创新在于通过**预测-更新（predict-update）**循环持续融合模型预测和实际测量。

#### 状态空间模型

**过程（状态转移）模型：**
$$x_k = F x_{k-1} + B u_k + w_k, \quad w_k \sim \mathcal{N}(0, Q)$$

**测量模型：**
$$z_k = H x_k + v_k, \quad v_k \sim \mathcal{N}(0, R)$$

其中 $x_k$ 为隐藏状态向量，$F$ 为状态转移矩阵，$H$ 将状态映射到测量空间，$w_k$ 和 $v_k$ 为零均值高斯噪声。

#### 五大核心方程

**预测步骤：**
1. 状态预测：$\hat{x}_{k|k-1} = F \hat{x}_{k-1|k-1} + B u_k$
2. 协方差预测：$P_{k|k-1} = F P_{k-1|k-1} F^T + Q$

**更新步骤：**
3. 卡尔曼增益：$K_k = P_{k|k-1} H^T (H P_{k|k-1} H^T + R)^{-1}$
4. 状态更新：$\hat{x}_{k|k} = \hat{x}_{k|k-1} + K_k (z_k - H \hat{x}_{k|k-1})$
5. 协方差更新：$P_{k|k} = (I - K_k H) P_{k|k-1}$

#### 卡尔曼增益的核心直觉

卡尔曼增益 $K_k$ 是滤波器的核心机制，它在模型预测和测量之间动态平衡信任：

$$K_k = \frac{P_{k|k-1} H^T}{H P_{k|k-1} H^T + R}$$

- 当**测量噪声** $R$ 相对于预测不确定性 $P$ 较大时：$K_k \to 0$，滤波器信任预测并忽略含噪测量
- 当**过程噪声** $Q$ 较大（预测不确定）时：$P_{k|k-1}$ 增大，$K_k$ 增大，滤波器偏向测量

这种**协方差驱动的自适应加权机制**是卡尔曼滤波器区别于固定系数滤波器的根本所在。

#### 参数调优

| 参数 | 作用 | 典型来源 |
|------|------|----------|
| $Q$（过程噪声协方差） | 状态在步间允许变化的程度 | 模型不确定性、离散化误差 |
| $R$（测量噪声协方差） | 每次测量的噪声水平 | 传感器规格、经验方差 |

**调优启发式：** 先从传感器校准确定 $R$，再调节 $Q$ 以达到期望的平滑度。比值 $Q/R$ 比绝对值更重要。$Q$ 过大 → 滤波输出抖动（过度拟合测量）。$Q$ 过小 → 滤波输出迟钝（忽略测量）。

#### Python 实现

```python
import numpy as np

class KalmanFilter:
    def __init__(self, F, H, Q, R, x0, P0):
        self.F = F; self.H = H; self.Q = Q; self.R = R
        self.x = x0; self.P = P0

    def predict(self, u=None):
        B_u = self.B @ u if u is not None and hasattr(self, 'B') else 0
        self.x = self.F @ self.x + B_u
        self.P = self.F @ self.P @ self.F.T + self.Q

    def update(self, z):
        y = z - self.H @ self.x              # 新息 (innovation)
        S = self.H @ self.P @ self.H.T + self.R  # 新息协方差
        K = self.P @ self.H.T @ np.linalg.inv(S)   # 卡尔曼增益
        self.x = self.x + K @ y
        self.P = (np.eye(len(self.x)) - K @ self.H) @ self.P

    def step(self, z, u=None):
        self.predict(u); self.update(z)
        return self.x.copy()

# 一维跟踪示例 (位置 + 速度)
dt = 0.1
F = np.array([[1, dt], [0, 1]])
H = np.array([[1, 0]])
Q = np.array([[0.01, 0], [0, 0.01]])
R = np.array([[0.1]])
kf = KalmanFilter(F, H, Q, R, x0=np.array([0, 0]), P0=np.eye(2))
```

对于生产环境，推荐使用 `filterpy` 库。

#### 扩展形式

- **扩展卡尔曼滤波器 (EKF)**：通过雅可比矩阵线性化非线性 $f$ 和 $h$。适用于弱非线性系统；强非线性下可能发散。
- **无迹卡尔曼滤波器 (UKF)**：通过确定性选择的 Sigma 点传播非线性函数，无需计算雅可比，均值和协方差的精度达二阶。

#### 核心属性与局限

- **最优性**：在线性高斯系统中是最小均方误差意义下的最优估计器
- **递推性**：仅需存储上一状态估计和协方差——时间维度上 $O(1)$ 内存
- **不确定性量化**：$P_{k|k}$ 是完整的协方差估计，不仅是点估计
- **局限**：假设高斯噪声（重尾或多模态噪声下性能下降）；$Q$ 或 $R$ 的错误指定导致有偏或过于自信的估计；没有过程模型时不如自适应滤波器或 S-G 平滑器

### 5.2 巴特沃斯滤波器 (Butterworth Filter)

巴特沃斯滤波器是一种 IIR 滤波器，其设计宗旨是实现**最大平坦通带（maximally flat passband）**——通带内幅频响应最优平滑且无纹波，代价是过渡带渐进滚降。

#### 传递函数

$n$ 阶低通巴特沃斯滤波器（截止频率 $\omega_c$）的幅频响应平方为：

$$|H(\omega)|^2 = \frac{1}{1 + (\omega / \omega_c)^{2n}}$$

极点均匀分布在 $s$ 平面内半径为 $\omega_c$ 的圆上：

$$s_k = \omega_c \, e^{j(\pi/2 + (2k-1)\pi/2n)}, \quad k = 1, \dots, n$$

所有极点均位于左半平面（保证稳定性），且**无零点**（全极点滤波器）。

#### 阶数与滚降

阻带衰减为 $-20n$ dB/decade：
- 1 阶：-20 dB/decade（渐进）
- 2 阶：-40 dB/decade
- 4 阶：-80 dB/decade
- 8 阶：-160 dB/decade（锐利截止）

**权衡：** 高阶带来更陡峭的滚降，但引入更多相位失真，且 $n > 10$ 时可能导致数值不稳定。

#### 前向-反向滤波（零相位）

标准 IIR 滤波器引入频率相关相位延迟。`filtfilt` 技术对数据先后进行前向和反向滤波，幅频响应被平方且产生**零相位延迟**：

$$H_{filtfilt}(\omega) = |H(\omega)|^2$$

代价是：有效滤波阶数翻倍，信号两端均出现瞬态伪影，且滤波器变为非因果（无法实时使用）。

#### Python 实现

```python
from scipy.signal import butter, filtfilt, sosfiltfilt

def butterworth_lowpass(data, cutoff, fs, order=4):
    """
    Butterworth lowpass filter.

    Parameters
    ----------
    data : array_like, 一维输入信号
    cutoff : float, 截止频率 (Hz)
    fs : float, 采样频率 (Hz)
    order : int, 滤波阶数 (默认 4)
    """
    nyquist = 0.5 * fs
    normal_cutoff = cutoff / nyquist
    sos = butter(order, normal_cutoff, btype='low', output='sos')
    return sosfiltfilt(sos, data)
```

**关键细节：** 对于 4 阶以上的滤波器，务必使用 `output='sos'`（二阶节级联）而非直接的 $b, a$ 形式。直接形式的多项式表示在高阶时会出现严重数值抵消；SOS 则将滤波器分解为数值条件良好的双二阶级联。

#### 局限

- `filtfilt` 在信号边缘产生瞬态伪影（应填充或裁剪边缘）
- 非自适应：必须是先已知频率成分
- 零相位模式下非因果，不适用于实时流处理
- 不适合频率带重叠的信号（改用自适应或 Wiener 滤波器）
- 因果模式下载止频率附近群延迟变化显著（相位失真）

### 5.3 高斯滤波器 (Gaussian Filter)

高斯滤波器将信号与高斯核（Gaussian kernel）做卷积：

$$G(x) = \frac{1}{\sqrt{2\pi}\,\sigma} e^{-x^2 / (2\sigma^2)}$$

$$y(t) = (x * G)(t) = \int_{-\infty}^{\infty} x(\tau)\, G(t - \tau)\, d\tau$$

#### 截止频率与 Sigma 的关系

高斯滤波器的频域响应也是高斯的：

$$H(\omega) = e^{-\sigma^2 \omega^2 / 2}$$

-3 dB 截止频率为：

$$f_c = \frac{\sqrt{\ln 2}}{2\pi\sigma} \approx \frac{0.1325}{\sigma} \quad \text{(归一化频率)}$$

| $\sigma$ (samples) | -3 dB 截止 (norm.) | 含义 |
|:---:|:---:|:---|
| 1 | 0.133 | 中等平滑 |
| 5 | 0.027 | 重度平滑 |
| 10 | 0.013 | 极重平滑 |

#### 可分离性（二维）

高斯核具有**可分离性（separability）**：

$$G(x, y) = \frac{1}{\sqrt{2\pi}\sigma} e^{-x^2/(2\sigma^2)} \cdot \frac{1}{\sqrt{2\pi}\sigma} e^{-y^2/(2\sigma^2)}$$

这意味着二维卷积可以分解为两次一维卷积（先行后列），将复杂度从 $O(N^2 K^2)$ 降至 $O(N^2 K)$。

#### Python 实现

```python
import numpy as np
from scipy.ndimage import gaussian_filter1d, gaussian_filter

# 一维信号
y = gaussian_filter1d(x, sigma=2.0, mode='reflect')

# 二维图像
smoothed = gaussian_filter(image, sigma=(1.0, 2.0), mode='reflect')

# 手动一维核（理解用）
def gaussian_kernel(size, sigma):
    ax = np.arange(-size // 2 + 1, size // 2 + 1)
    kernel = np.exp(-ax**2 / (2 * sigma**2))
    return kernel / kernel.sum()
```

**注意：** `scipy.ndimage.gaussian_filter1d` 在 $\sigma > 3$ 时使用 Young-van Vliet 级联 IIR 近似，比直接与截断核卷积更快且更精确。

#### 与其他滤波器的平滑质量对比

| 滤波器 | 平滑质量 | 边缘保持 | 计算成本 |
|--------|:---:|:---:|:---:|
| 移动平均 | 一般（频域旁瓣） | 差 | $O(N)$ |
| 高斯 | 良好（平滑频域滚降） | 中等 | $O(N)$ |
| Savitzky-Golay | 良好（保持矩） | 良好 | $O(N)$ |

#### 局限

- 模糊尖锐过渡（阶跃边缘、突变）——若边缘保持重要，应使用中值或双边滤波器
- 实际使用时核必须截断（通常截断至 $\pm 3\sigma$ 或 $\pm 4\sigma$），引入小幅近似误差
- 非自适应——整个信号使用固定 $\sigma$

### 5.4 中值滤波器 (Median Filter)

中值滤波器是一个**次序统计量（order-statistics）**非线性滤波器：

$$\hat{y}_i = \text{median}(x_{i-k}, x_{i-k+1}, \dots, x_i, \dots, x_{i+k-1}, x_{i+k})$$

#### 核心特性

- **边缘保持（Edge Preservation）：** 与线性平滑滤波器不同，中值滤波器不会模糊边缘。跨边缘的窗口的中值仍然来自边缘正确的一侧，因此阶跃函数依然是阶跃函数。
- **脉冲噪声消除（Impulse Noise Removal）：** 椒盐噪声（salt-and-pepper noise）在窗口大于噪声团时被完全消除，因为中值自动忽略极端值。
- **非线性：** 无法用传递函数——线性系统理论不适用。
- **平滑能力：** 与相同窗口大小的移动平均相当
- **离群值免疫力：** 无限（只要窗口内离群值少于一半）

#### Python 实现

```python
from scipy.signal import medfilt

# 一维中值滤波，kernel_size 必须为奇数
y = medfilt(x, kernel_size=5)

# 手动实现（理解/修改用）
import numpy as np

def median_filter_1d(x, k=3):
    pad = k // 2
    x_pad = np.pad(x, pad, mode='reflect')
    y = np.zeros_like(x)
    for i in range(len(x)):
        y[i] = np.median(x_pad[i:i + k])
    return y
```

#### 变体

- **加权中值滤波器（Weighted Median Filter）**：窗口内赋权重后再取中值
- **中值的中值（Median of Medians）**：递推应用以增强平滑
- **自适应中值滤波器（Adaptive Median Filter）**：根据局部统计量调节窗口大小

#### 局限

- 小于半窗口大小的细线、小特征可能被完全消除
- 大窗口计算昂贵：$O(N k \log k)$——虽然存在 Huang 的 $O(N)$ 运行中值直方图算法
- 不可微（不适用于基于梯度的优化）
- 无频域解释使系统化设计更困难
- 对高斯噪声的效率低于线性滤波器（中值对高斯噪声不如均值高效）

### 5.5 LOWESS/LOESS

LOESS（Locally Estimated Scatterplot Smoothing）和 LOWESS（Locally Weighted Scatterplot Smoothing）是非参数回归方法，通过在每个查询点局部拟合低阶多项式实现平滑。

对每个查询点 $x_0$，在 $k$ 个最近邻点处拟合一个 $d$ 次多项式，并按距离加权：

$$\hat{y}(x_0) = \beta_0 + \beta_1 x_0 + \dots + \beta_d x_0^d$$

最小化局部加权平方误差：

$$\sum_{i=1}^n w_i(x_0) \left( y_i - p(x_i) \right)^2$$

#### Tricube 权重函数

标准 LOESS 使用 **tricube** 权重：

$$w(u) = \begin{cases}
(1 - |u|^3)^3 & |u| < 1 \\
0 & |u| \ge 1
\end{cases}$$

$$w_i(x_0) = w\left(\frac{|x_i - x_0|}{\Delta(x_0)}\right)$$

其中 $\Delta(x_0)$ 是第 $k$ 个最近邻的距离（$x_0$ 处的带宽）。超出带宽的点权重为零。

#### 带宽参数

带宽 $f$（或 `frac`）控制每次局部拟合使用的数据比例：
- **小 $f$ ($< 0.1$)**：捕捉精细结构，高方差，可能过拟合
- **大 $f$ ($> 0.5$)**：重度平滑，低方差，可能欠拟合
- 典型范围：0.2 到 0.5

#### 鲁棒迭代（离群值抑制）

LOWESS 包含使用 **bisquare 权重**的鲁棒化步骤：

$$r_i = y_i - \hat{y}_i \quad \text{(残差)}$$
$$\delta_i = B(r_i / (6 \cdot \text{MAD}(r))) \quad \text{(鲁棒权重)}$$

其中 $B(u) = (1 - u^2)^2$ （当 $|u| < 1$ 时，否则为 0），MAD 为中位数绝对偏差。通常 3 到 5 次迭代即可收敛。

#### Python 实现

```python
import statsmodels.api as sm

result = sm.nonparametric.lowess(
    endog=y,          # 因变量
    exog=x,           # 自变量
    frac=0.3,         # 带宽 (数据比例)
    it=3,             # 鲁棒迭代次数
    return_sorted=True
)
x_fitted, y_fitted = result[:, 0], result[:, 1]
```

#### 局限

- **计算成本：** 每个查询点需一次加权最小二乘拟合；朴素实现为 $O(N^2)$，空间索引下为 $O(N \log N)$
- **非因果：** 需要完整数据集，无在线/流式版本
- **外推不可靠：** 超出数据范围的局部多项式会快速发散
- **带宽选择**依赖实例，通常需要交叉验证
- **多维扩展**受维数灾难影响（高维空间邻域稀疏）

### 5.6 Hodrick-Prescott 滤波器

HP 滤波器将时间序列 $y_t$ 分解为趋势成分 $\tau_t$ 和周期成分 $c_t = y_t - \tau_t$，通过求解以下优化问题：

$$\min_{\tau_t} \sum_{t=1}^T (y_t - \tau_t)^2 + \lambda \sum_{t=2}^{T-1} (\Delta^2 \tau_t)^2$$

其中 $\Delta^2 \tau_t = \tau_{t+1} - 2\tau_t + \tau_{t-1}$ 为离散二阶差分。

第一项惩罚数据与趋势的偏离（拟合度）。第二项惩罚趋势斜率的变化（平滑度）。$\lambda$ 控制两者之间的权衡。

#### Lambda 参数

| 数据频率 | 惯例 $\lambda$ | 说明 |
|:---:|:---:|:---|
| 年度 | 100 | 弱平滑 |
| 季度 | 1600 | 标准值 (Hodrick & Prescott) |
| 月度 | 14,400 | 按 $(1/3)^2$ 缩放 |
| 周度 | $1600 \times (52/4)^2$ | 遵循 Ravn-Uhlig 规则 |
| 日度 | $1600 \times (365/4)^2$ | 较少使用，通常过于粗糙 |

**闭合解：**

$$\tau = (I + \lambda D^T D)^{-1} y$$

其中 $D$ 为 $(T-2) \times T$ 的二阶差分矩阵。

#### Python 实现

```python
import numpy as np
from scipy import linalg

def hp_filter(y, lamb=1600):
    """
    Hodrick-Prescott filter.
    
    Returns
    -------
    trend : ndarray, 趋势成分
    cycle : ndarray, 周期成分 (y - trend)
    """
    T = len(y)
    D = np.zeros((T - 2, T))
    for i in range(T - 2):
        D[i, i]   = 1
        D[i, i+1] = -2
        D[i, i+2] = 1
    trend = linalg.solve(np.eye(T) + lamb * D.T @ D, y, assume_a='pos')
    cycle = y - trend
    return trend, cycle
```

#### 批评与替代方案

HP 滤波器虽在宏观经济分析中广泛使用，但面临四项核心批评：

1. **端点问题（Endpoint Problem）：** 滤波器在样本末端严重依赖边界附近的路径，序列末尾趋势估计会随后续数据添加而发生大幅修订。最近约 3 年的 HP 趋势不应被信任。

2. **伪周期（Spurious Cycles）：** Cogley & Nason (1995) 证明，HP 滤波器施加于单位根（I(1)）过程时，即使数据中不存在真正的经济周期，也会"制造"出看似商业周期频率的谱功率。

3. **Lambda 选择的任意性：** $\lambda = 1600$ 的惯例掩盖了最优 $\lambda$ 取决于未知的数据生成过程这一事实。

4. **无不确定性量化：** 滤波仅产生点估计，没有置信区间。

**推荐替代方案：**

| 滤波器 | 解决的 HP 局限 |
|--------|:---|
| Christiano-Fitzgerald (CF) 带通 | 明确的频率选择，较少端点偏差 |
| Baxter-King (BK) | 固定长度对称移动平均，带通 |
| Hamilton 滤波器 (Hamilton 2018) | 回归基础，无端点问题，无伪周期 |
| 局部水平模型 (结构时间序列) | 状态空间，完整不确定性量化 |

---

## 6. 滤波器横向对比 (Cross-Algorithm Comparison)

### 6.1 主对比表

| 属性 | Kalman | Butterworth | Gaussian | Median | LOWESS | HP | SMA | EMA | S-G |
|------|--------|-------------|----------|--------|--------|-----|-----|-----|-----|
| **线性？** | 是 | 是 | 是 | **否** | 是（非鲁棒） | 是 | 是 | 是 | 是 |
| **因果（实时）？** | **是** | 是（因果模式） | 否 | 是 | 否 | 否 | 是 | 是 | 否 |
| **零相位？** | 否（固有因果） | **是** (filtfilt) | 是 | 否 | **是**（样本内） | **是**（双侧） | 否 | 否 | **是** |
| **在线/流式？** | **是** | 是（因果） | 否 | 是 | 否 | 否 | 是 | 是 | 否 |
| **内存** | O(1) | O(阶数) | O(核大小) | O(窗口) | O(N) | O(N) | O(n) 缓冲 | **O(1)** | O(n) |
| **每步复杂度** | O($d^3$) | O(阶数) | O($N\sigma$) | O($Nk$) | O($N^2$) | O($T$) | **O(1)** | **O(1)** | O($Nn$) |
| **参数数量** | Q,R,F,H | 阶数, 截止 | $\sigma$ | 核大小 | frac, 度, 迭代 | $\lambda$ | n | n | n, k |
| **调优难度** | 高（需建模） | 中 | 低 | 低 | 中 | 低（但随意） | **极低** | **极低** | 中 |
| **边缘行为** | 依赖模型 | 瞬态 (filtfilt) | 中等模糊 | **优秀** | 边界加权 | **差**（端点偏差） | 损失(n-1)/2 | 启动瞬态 | 损失 m 样本 |
| **主要领域** | 跟踪, 控制, 导航 | 音频, EEG, 通用 | 图像处理, 平滑 | 图像去噪, 尖峰移除 | 探索性数据分析 | 宏观经济学 | 通用平滑 | 金融技术分析 | 光谱, 化学计量 |
| **Python 库** | `filterpy` | `scipy.signal` | `scipy.ndimage` | `scipy.signal` | `statsmodels` | `statsmodels` | pandas/numpy | pandas | `scipy.signal` |
| **核心优势** | 最优线性估计 | 最大平坦通带 | 平滑滚降，可分离 | 边缘保持 | 非参数灵活性 | 趋势-周期分解 | 简单，线性相位 | O(1) 状态 | 零相位，矩保持 |

### 6.2 雷达图描述：关键维度矩阵

在滤波器选择中，以下维度最为关键：

| 维度 | 最优表现者 | 说明 |
|------|-----------|------|
| **低延迟** | DEMA, TEMA | 以噪声放大和过冲为代价实现最低延迟 |
| **高平滑度** | S-G, ALMA (低 offset) | S-G 在保持特征的同时实现优异噪声抑制 |
| **相位线性度** | SMA, S-G | 对称 FIR 滤波器，无群延迟变化 |
| **实时性** | Kalman, EMA | 单状态递推，最小内存占用 |
| **边缘保持** | Median | 非线性处理使其在边缘处远超所有线性滤波器 |
| **频域纯度** | Butterworth, Gaussian, ALMA | 无旁瓣或可控滚降 |
| **鲁棒性** | Median, LOWESS (鲁棒) | 对离群值和脉冲噪声免疫或高度鲁棒 |
| **低计算成本** | EMA, SMA | O(1) 每次更新，适合嵌入式系统 |
| **不确定性** | Kalman | 唯一提供完整协方差估计的滤波器 |
| **参数简单度** | SMA, Gaussian, HP | 最少参数数，快速上手 |

### 6.3 帕累托前沿

不同维度对上的帕累托最优（Pareto optimal）滤波器：

**延迟 vs 平滑度：**
```
低延迟 <-------------------------> 高平滑度
  TEMA                            SMA
  DEMA                            ALMA (offset=0.5)
  ALMA (offset=0.85)              S-G (大窗口)
  WMA                             EMA
```

ALMA 在该前沿上占据独特位置：通过调节 offset 参数，可在 EMA 级别到 SMA 级别之间连续扫描，且始终保持高斯频谱轮廓。

**频率选择性 vs 时域保真度：**
- 高频域选择性：Butterworth (高階) > S-G > Gaussian > SMA
- 高时域保真度：Median > S-G > LOWESS > Gaussian > Butterworth

**计算效率 vs 滤波质量：**
- O(1) 效率极值：EMA, SMA
- O(N) 高效率 + 高质量：S-G, Gaussian, Median (Huang 算法)
- O(N^2) 高质量：LOWESS
- 建模依赖高质量：Kalman

---

## 7. 应用领域指南 (Application Domain Guide)

### 7.1 金融时间序列分析

金融数据的特点：高噪声、非平稳、存在跳跃和结构性断点，且实时性往往关键。

| 场景 | 推荐滤波器 | 理由 |
|------|-----------|------|
| 中长期趋势跟踪 | SMA 或 ALMA (offset=0.5) | 线性/近似线性相位保持过零点时序，零过冲 |
| 高频交易信号 | TEMA 或 DEMA | 延迟最低；信号本身已足够干净，噪声增益可接受 |
| 趋势跟踪、入场时机 | ALMA (offset=0.85-0.95) | 比 SMA 更早捕获入场信号，无 DEMA/TEMA 的过冲 |
| 波动率平滑 | S-G ($n=21$--$51$, $k=2$--$3$) | 零相位避免回测中的前视偏差（look-ahead bias） |
| 回测去噪（离线） | S-G 或 `filtfilt` Butterworth | 零相位±无延迟特性；不需要实时性 |
| 均值回归策略 | SMA 或 ALMA (offset=0.5) | 线性相位保护零交叉时序 |

### 7.2 光谱与化学计量学

光谱数据的特点：峰的位置、高度和面积携带物理/化学信息，必须在滤波中保持；噪声通常为高斯型。

| 场景 | 推荐滤波器 | 理由 |
|------|-----------|------|
| 峰值检测前平滑 | S-G ($n=9$--$21$, $k=2$--$3$) | **矩保持性**对定量分析至关重要——峰面积（浓度）不变 |
| NIR/IR 光谱预处理 | S-G ($n=7$--$15$, $k=2$--$3$) | 多元校准管线的标准第一步 |
| 基线校正 | 中值滤波器 | 峰值被保留而宽基线被移除 |
| 含脉冲噪声的光谱 | 中值滤波器 或 LOWESS (鲁棒) | 离群值免疫或高度抑制 |
| 导数光谱 | S-G (`deriv=1` 或 `2`) | 同时平滑和微分，且矩保持性保证导数峰的位置不变 |

### 7.3 生物医学信号处理

生物医学信号（ECG、EEG、EMG）包含不同频率带的生理信息，且对特征保持要求极高。

| 场景 | 推荐滤波器 | 理由 |
|------|-----------|------|
| ECG 去噪、QRS 检测 | S-G ($n=5$--$15$, $k=3$--$4$) | R 波振幅保持优于移动平均 |
| 运动伪迹去除 | 中值滤波器 | 伪迹通常表现为脉冲噪声 |
| 频带分离 (alpha, beta, gamma) | Butterworth (带通, `filtfilt`) | 最大平坦通带 + 零相位，不扭曲波形相位 |
| EEG 伪影去除 | Butterworth (陷波, 50/60Hz) | 精确消除工频干扰 |
| 呼吸/心跳信号平滑 | 高斯滤波器 ($\sigma=2$--$5$) | 简单有效的通用平滑 |

### 7.4 传感器数据融合

传感器数据特点：多源、不同采样率、需要状态估计而非仅平滑。

| 场景 | 推荐滤波器 | 理由 |
|------|-----------|------|
| IMU 姿态估计 | Kalman (或互补滤波器) | 融合加速度计+陀螺仪，提供完整协方差 |
| GPS/INS 导航 | EKF 或 UKF | 非线性测量模型 + 最优状态估计 |
| 去噪后积分/微分 | S-G ($n=5$--$11$, $k=2$) | `deriv` 参数可直接获得去噪后的速度/加速度 |
| 脉冲/尖峰噪声传感器 | 中值滤波器 | 传感器故障或 EM 干扰产生孤立离群值 |
| 通用去噪 | 高斯滤波器 或 S-G | 参数简单的快速基线去噪 |

### 7.5 宏观经济分析

宏观数据特点：低频（季度/月度）、包含趋势+周期+季节成分、端点修订是关键问题。

| 场景 | 推荐滤波器 | 理由 |
|------|-----------|------|
| 趋势-周期分解 | HP ($\lambda=1600$ 季度) | 学界标准，适用于 GDP、失业率和工业产出 |
| 明确的商业周期频率提取 | Christiano-Fitzgerald (CF) 或 Baxter-King (BK) | 替代 HP——无端点偏差、明确的频率选择 |
| 含端点问题的现代分析 | Hamilton 滤波器 (2018) | 无端点问题、无伪周期、回归基础 |
| 带不确定性量化的分解 | 局部水平模型 (结构时间序列) | 完整置信区间，HP 没有 |
| 缓慢趋势低噪声 (月频指标) | SMA | 足够简单；低频环境下延迟无关紧要 |
| 缓慢趋势高噪声 | ALMA (offset=0.5) | 高斯窗口提供最优噪声抑制 |

---

## 8. 滤波器选择决策树 (Filter Selection Flowchart)

以下文本决策树基于信号特征和需求，引导滤波器选择：

```
START: 需要滤波的信号
│
├─ 需要实时/流式处理？
│  ├─ YES ─ 需要不确定性估计？
│  │  ├─ YES ─ 有系统状态模型？ → [Kalman Filter]
│  │  └─ NO  ─ 信号含脉冲噪声？
│  │     ├─ YES → [Median Filter]
│  │     └─ NO  ─ 最低延迟要求？
│  │        ├─ 极致低延迟 → [TEMA] 或 [DEMA]
│  │        ├─ 中等延迟 + 无过冲 → [ALMA (offset=0.85)]
│  │        ├─ 延迟可接受 + 简单优先 → [EMA] 或 [SMA]
│  │        └─ 嵌入式/微控制器 → [EMA] (O(1) 内存)
│  │
│  └─ NO (离线处理) ─ 信号类型？
│     ├─ 含高斯噪声的峰/特征 (光谱, 色谱)
│     │  ├─ 需要保持峰高/面积/位置？ → [Savitzky-Golay]
│     │  └─ 仅需通用去噪 → [Gaussian Filter] 或 [S-G (大窗口)]
│     │
│     ├─ 含脉冲/椒盐噪声 → [Median Filter] 或 [LOWESS (鲁棒)]
│     ├─ 需要频率带分离 (如 EEG alpha/beta) → [Butterworth (带通, filtfilt)]
│     ├─ 图像/二维数据 → [Gaussian Filter] 或 [Median Filter]
│     │
│     ├─ 宏观经济趋势-周期分解
│     │  ├─ 季度 GDP 标准分析 → [HP Filter (λ=1600)]
│     │  └─ 需要避免端点偏差和伪周期 → [Hamilton Filter] 或 [CF Band-Pass]
│     │
│     ├─ 非参数趋势探索
│     │  ├─ 含离群值 → [LOWESS (it=3)]
│     │  └─ 无离群值 → [LOESS] 或 [S-G]
│     │
│     └─ 多传感器数据融合/跟踪 → [Kalman Filter]
│
└─ 关键约束检查：
   ├─ 必须在 μC/嵌入式运行？ → EMA 或 SMA (O(1))
   ├─ 必须零相位？ → S-G, filtfilt Butterworth, Gaussian, HP
   ├─ 必须边缘保持？ → Median Filter
   └─ 需要可解释权重？ → SMA/WMA/EMA/ALMA (系数直观)
```

---

## 9. 对比分析工具 (Comparison Analysis Tool)

### 9.1 工具概述

本报告附带一个 Python 实现的滤波器对比分析工具 `filter_comparison_tool.py`，用于在标准化测试信号集上对 10 种滤波算法进行定量基准测试（benchmarking）。工具涵盖报告中详细介绍的 SMA、EMA、WMA、ALMA、Savitzky-Golay、Kalman、Butterworth、Gaussian、Median 和 LOWESS 共 10 种滤波器（DEMA、TEMA 和 H-P 滤波器因适用范围特殊，未纳入本次定量对比）。

### 9.2 依赖与运行

**环境依赖：**
```bash
pip install numpy scipy matplotlib statsmodels
```

**运行方式：**
```bash
cd /Users/xfpan/claude
python filter_comparison_tool.py
```

**输出产物：**
| 产物 | 路径 | 说明 |
|------|------|------|
| 排名结果表 | `filter_comparison_results.csv` | 每种滤波器的聚合评分和排名 |
| 时域对比图 (5 张) | `filter_comparison_plots/01_time_domain_*.png` | 每种测试信号上所有滤波器的输出叠加 |
| MSE 柱状图 | `filter_comparison_plots/02_mse_bar_chart.png` | 每种滤波器在不同信号类型上的 MSE 分组柱状图 |
| 延迟-平滑度散点图 | `filter_comparison_plots/03_lag_vs_smoothness.png` | 平均延迟 vs 粗糙度的帕累托前沿 |
| 雷达图 | `filter_comparison_plots/04_radar_chart.png` | 五维（精度、低延迟、平滑度、速度、鲁棒性）综合对比 |
| 频率响应图 | `filter_comparison_plots/05_frequency_response.png` | 通过 Chirp 信号测试的各滤波器幅频响应 |
| 综合热力图 | `filter_comparison_plots/06_summary_heatmap.png` | 归一化评分矩阵（红-黄-绿色阶） |

### 9.3 测试信号设计

工具使用五种具有不同时频特性的合成测试信号，覆盖滤波器的典型应用场景：

| 信号类型 | 特征 | 验证维度 |
|----------|------|----------|
| **Sinusoid** | 纯正弦 + 高斯白噪声 (SNR ≈ 6 dB) | 基础去噪能力 |
| **Step** | 电平跳变 + 噪声 | 边缘保持、阶跃响应 |
| **Trend+Seasonal** | 线性趋势 + 正弦季节性 + AR(1) 有色噪声 | 趋势提取 |
| **Impulse** | 低幅正弦 + 高幅脉冲尖峰 | 离群值/脉冲噪声抑制 |
| **Chirp** | 频率扫描 (0.001--0.25 归一化频率) | 频率响应特性 |

### 9.4 评价指标体系

每条（滤波器 × 信号）组合计算六个指标：

| 指标 | 含义 | 方向 |
|------|------|:--:|
| MSE | 滤波输出与干净信号的均方误差 | ↓ 越小越好 |
| SNR Improvement | 输出 SNR 相对输入 SNR 的提升 (dB) | ↑ 越大越好 |
| Lag | 通过互相关法估计的延迟（样本数） | ↓ 越小越好 |
| Roughness | 输出信号二阶差分的平方和 | ↓ 越平滑越好 |
| Time per 1000 | 处理 1000 个样本的中位数耗时 (秒) | ↓ 越快越好 |
| Edge Ratio | 边缘误差与内部误差之比 | ↓ 越接近 1 越好 |

各指标在信号类别上取均值后进行 0--1 归一化，取等权平均值作为综合评分（Composite Score）。

### 9.5 关键实证发现

以下基于工具实际输出的排名表（`filter_comparison_results.csv`）：

| Rank | Filter | MSE | SNR(dB) | Lag(smp) | Roughness | Time(s/1k) | EdgeRatio | Composite |
|:----:|--------|------|---------|---------|----------|-----------|---------|:---------:|
| 1 | **Gauss** | 0.093 | +3.20 | 0.0 | 4.1 | <0.001 | 0.966 | **0.868** |
| 2 | **SMA** | 0.129 | +2.90 | 0.2 | 7.7 | <0.001 | 0.954 | **0.823** |
| 3 | SavGol | 0.117 | +2.17 | 0.2 | 23.3 | <0.001 | 1.015 | 0.739 |
| 4 | EMA | 0.154 | +1.80 | -0.8 | 20.7 | <0.001 | 1.027 | 0.709 |
| 5 | Butter | 0.100 | +1.90 | 0.0 | 19.8 | <0.001 | 1.078 | 0.703 |
| 6 | ALMA | 0.225 | +1.01 | -3.0 | 3.9 | <0.001 | 1.020 | 0.684 |
| 7 | Median | 0.106 | +2.88 | -0.2 | 109.1 | <0.001 | 1.143 | 0.649 |
| 8 | WMA | 0.160 | +1.62 | 3.6 | 13.6 | <0.001 | 0.990 | 0.613 |
| 9 | Kalman | 0.124 | +0.44 | -0.2 | 214.9 | 0.007 | 0.990 | 0.479 |
| 10 | LOWESS | 0.317 | +0.32 | 3.0 | <0.001 | 0.036 | 0.882 | 0.348 |

**核心观察：**

1. **零相位滤波器整体占优。** 排名前 5 中有 3 个为零相位（Gauss、SavGol、Butterworth filtfilt 模式），说明在离线批处理场景下，不引入相位失真的滤波器具有系统性优势。这与报告中关于因果/非因果滤波器权衡的理论分析完全一致。

2. **Gauss 排名第一**得益于其平滑的频域滚降（无旁瓣）和适中的平滑度。它在精度、延迟、平滑度和鲁棒性四个维度上均无明显短板，是一种优秀的通用型离线滤波器。

3. **SMA 以短窗口获得第二名**，验证了简单方法的有效性。但需注意：报告指出 SMA 在较大窗口下延迟显著增加且频谱泄漏严重；本工具中窗口较小（$n=11$），这些弱点尚未充分暴露。

4. **Savitzky-Golay 排名第三**，其粗糙度 (23.3) 高于 Gauss (4.1) 和 SMA (7.7)，但这并非缺陷——S-G 有意保留了信号的峰值、面积和高阶矩信息，粗糙度较高恰好反映了其特征保持能力。

5. **ALMA 拥有最低粗糙度 (3.9)**，证明其高斯窗口提供了最优的噪声抑制。但其较大的 MSE (0.225) 和延迟 (-3.0) 说明：高平滑度是以响应速度为代价的。这验证了报告中"可通过 offset 参数连续调节延迟-平滑度平衡"的核心论述。

6. **Median 粗糙度最高 (109.1)**，印证了其非线性边缘保持特性：它不模糊阶跃和尖峰，因此二阶差分很大。Median 在 Impulse 信号上的表现（MSE 最低）验证了报告中对脉冲噪声免疫的论述。

7. **Kalman 滤波排名靠后（第 9）**，主要因为默认参数 ($Q=0.01$, $R=0.1$) 未针对测试信号调优。报告 §5.1 已明确指出"$Q$ 或 $R$ 的错误指定导致有偏或过于自信的估计"。Kalman 滤波器的优势在于状态估计和不确定性量化，而非通用信号平滑。

8. **LOWESS 排名末位**，反映了其 $O(N^2)$ 计算复杂度和带宽（`frac=0.2`）对测试信号的敏感性。在探索性数据分析的真实场景中，通过增大 `frac` 和增加鲁棒迭代次数，LOWESS 的表现会显著改善。

**工具与报告的互补关系：**
本工具提供的是固定默认参数下的横向快照；报告提供的是理论深度和参数调优指导。二者结合使用，既能看到"开箱即用"的相对表现，又能理解背后的信号处理原理和参数调节策略。

---

## 10. 参考文献 (References)

### 经典学术论文

1. Savitzky, A.; Golay, M. J. E. (1964). "Smoothing and Differentiation of Data by Simplified Least Squares Procedures." *Analytical Chemistry*, 36(8), 1627--1639.
2. Gorry, P. A. (1990). "General Least-Squares Smoothing and Differentiation by the Convolution (Savitzky-Golay) Method." *Analytical Chemistry*, 62(6), 570--573.
3. Schafer, R. W. (2011). "What Is a Savitzky-Golay Filter?" *IEEE Signal Processing Magazine*, 28(4), 111--117.
4. Press, W. H.; Teukolsky, S. A. (1990). "Savitzky-Golay Smoothing Filters." *Computers in Physics*, 4(6), 669--672.
5. Hodrick, R. J.; Prescott, E. C. (1997). "Postwar U.S. Business Cycles: An Empirical Investigation." *Journal of Money, Credit and Banking*, 29(1), 1--16.
6. Cogley, T.; Nason, J. M. (1995). "Effects of the Hodrick-Prescott Filter on Trend and Difference Stationary Time Series: Implications for Business Cycle Research." *Journal of Economic Dynamics and Control*, 19(1-2), 253--278.
7. Hamilton, J. D. (2018). "Why You Should Never Use the Hodrick-Prescott Filter." *Review of Economics and Statistics*, 100(5), 831--843.
8. Kalman, R. E. (1960). "A New Approach to Linear Filtering and Prediction Problems." *Journal of Basic Engineering*, 82(1), 35--45.
9. Cleveland, W. S. (1979). "Robust Locally Weighted Regression and Smoothing Scatterplots." *Journal of the American Statistical Association*, 74(368), 829--836.
10. Ravn, M. O.; Uhlig, H. (2002). "On Adjusting the Hodrick-Prescott Filter for the Frequency of Observations." *Review of Economics and Statistics*, 84(2), 371--376.

### 软件库文档

11. Virtanen, P. et al. (2020). "SciPy 1.0: Fundamental Algorithms for Scientific Computing." *Nature Methods*, 17, 261--272. [scipy.signal, scipy.ndimage]
12. Seabold, S.; Perktold, J. (2010). "Statsmodels: Econometric and Statistical Modeling with Python." *Proceedings of the 9th Python in Science Conference*. [statsmodels.nonparametric.lowess, statsmodels.tsa.filters]
13. Labbe, R. "FilterPy: Kalman Filters and Various Optimal and Non-Optimal Filtering Libraries in Python." [filterpy]

### 额外参考

14. Arnaud Legoux and Dimitris Tsokakis (2009). ALMA (Arnaud Legoux Moving Average) -- original description and parameter defaults.
15. Young, I. T.; van Vliet, L. J. (1995). "Recursive Implementation of the Gaussian Filter." *Signal Processing*, 44(2), 139--151. [scipy.ndimage 中大 $\sigma$ 所用的 IIR 近似]
16. Huang, T. S.; Yang, G. J.; Tang, G. Y. (1979). "A Fast Two-Dimensional Median Filtering Algorithm." *IEEE Transactions on Acoustics, Speech, and Signal Processing*, 27(1), 13--18. [$O(N)$ 运行中值算法]

---

*本报告基于 Savitzky-Golay 滤波、移动平均族和高级滤波算法三份研究资料综合而成，所有公式和性能数据均可在原始研究材料中溯源。报告总字数约 6500 词。*
