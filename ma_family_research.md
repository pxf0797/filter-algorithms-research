# Moving Average Family and ALMA Filter: A Quantitative Survey

## 1. Simple Moving Average (SMA)

### 1.1 Definition

The Simple Moving Average is the canonical smoothing filter. At time $t$, the $n$-period SMA is the arithmetic mean of the most recent $n$ observations:

$$
\text{SMA}_t(n) = \frac{1}{n}\sum_{i=0}^{n-1} x_{t-i}
$$

### 1.2 Frequency Response

The SMA is a finite-impulse-response (FIR) filter with uniform weights $w_i = 1/n$. Its transfer function is:

$$
H_{\text{SMA}}(f) = \frac{1}{n}\frac{1 - e^{-j2\pi f n}}{1 - e^{-j2\pi f}} = e^{-j\pi f (n-1)} \frac{\sin(\pi f n)}{n \sin(\pi f)}
$$

The magnitude response is a Dirichlet kernel (periodic sinc):

$$
|H_{\text{SMA}}(f)| = \left|\frac{\sin(\pi f n)}{n \sin(\pi f)}\right|
$$

Key properties:
- **First null**: at $f = 1/n$ (normalized frequency). Signals with periodicity equal to the window length are completely removed.
- **Side lobes**: decay at approximately $6\text{ dB}/\text{octave}$. The first side lobe is at $-13\text{ dB}$ relative to the main lobe — modest attenuation that allows significant spectral leakage.
- **Stop-band attenuation**: poor; the sinc envelope decays only as $1/f$.

### 1.3 Phase Response and Lag

The SMA has a linear phase response with constant group delay:

$$
\tau_g = -\frac{1}{2\pi}\frac{d\angle H}{df} = \frac{n-1}{2}
$$

For practical purposes, $\tau_g \approx n/2$ samples. This means an SMA of length 20 lags the input by 10 bars — considerable for fast-moving signals.

### 1.4 Computational Aspects

SMA admits an $O(1)$ update via a circular buffer:

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

Memory: $O(n)$. Real-time viability: excellent (single-pass, constant time).

---

## 2. Weighted Moving Average (WMA)

### 2.1 Definition

WMA assigns distinct weights to each observation, typically decaying linearly toward older data:

$$
\text{WMA}_t(n) = \frac{2}{n(n+1)} \sum_{i=0}^{n-1} (n - i) \cdot x_{t-i}
$$

The denominator $\frac{n(n+1)}{2}$ normalizes the linearly-decreasing weights $w_i = n - i$.

### 2.2 Non-Linear Weighting

More aggressive schemes use quadratic or exponential weights:

$$
\text{WMA}_t^{\text{quad}}(n) = \frac{\sum_{i=0}^{n-1} (n-i)^2 \cdot x_{t-i}}{\sum_{i=0}^{n-1} (n-i)^2}
$$

The weight concentration ratio $R_w = w_0 / \sum w_i$ quantifies how much emphasis falls on the most recent bar:

| Scheme | $R_w$ (n=20) | Lag (samples) | Noise reduction |
|--------|-------------|---------------|-----------------|
| Linear | 0.095       | $\approx n/3$ | Moderate        |
| Quadratic | 0.154    | $\approx n/4$ | Low             |
| SMA (uniform) | 0.05  | $n/2$         | Best            |

### 2.3 Tradeoff

WMA reduces lag compared to SMA at the cost of worse stop-band attenuation. The effective noise reduction ratio $\text{NRR} = \sum w_i^2$ is larger for WMA than for SMA of equal length, meaning less noise suppression.

```python
def wma(series: np.ndarray, n: int) -> np.ndarray:
    weights = np.arange(n, 0, -1)
    weights = weights / weights.sum()
    # Convolve with same mode; valid gives only fully-overlapped points
    return np.convolve(series, weights, mode='valid')
```

---

## 3. Exponential Moving Average (EMA)

### 3.1 Definition

EMA is an infinite-impulse-response (IIR) filter with exponentially decaying memory:

$$
\text{EMA}_t = \alpha x_t + (1 - \alpha) \cdot \text{EMA}_{t-1}
$$

where $\alpha = 2 / (n + 1)$ for an equivalent length-$n$ EMA (so-called "span" formulation). Some practitioners use $\alpha = 1/n$ (half-life), which gives slower decay.

### 3.2 Impulse Response

Expanding recursively yields the impulse response:

$$
\text{EMA}_t = \alpha \sum_{k=0}^{\infty} (1-\alpha)^k x_{t-k}
$$

The weights decay geometrically: $w_k = \alpha(1-\alpha)^k$. The half-life (time for weight to drop by 50%) is:

$$
t_{1/2} = \frac{\ln 0.5}{\ln(1-\alpha)}
$$

### 3.3 Frequency Response

The transfer function is a first-order low-pass:

$$
H_{\text{EMA}}(z) = \frac{\alpha}{1 - (1-\alpha)z^{-1}}
$$

The $-3\text{dB}$ cutoff frequency is:

$$
f_c = \frac{1}{2\pi\Delta t} \arccos\left(\frac{\alpha^2 + 2\alpha - 2}{2\alpha - 2}\right) \approx \frac{\alpha}{2\pi\Delta t} \quad (\alpha \ll 1)
$$

The magnitude response rolls off at $6\text{ dB}/\text{octave}$ — a single pole, no stop-band notches.

### 3.4 Group Delay

The EMA's group delay is frequency-dependent (non-linear phase):

$$
\tau_g(f) = \frac{1-\alpha}{\alpha} \cdot \frac{1}{1 + \left(\frac{1-\alpha}{\alpha}\right)^2 \tan^2(\pi f)}
$$

At DC ($f=0$): $\tau_g(0) = (1-\alpha)/\alpha$. For $\alpha = 2/(n+1)$, this gives $\tau_g(0) \approx (n-1)/2$, matching SMA's lag for low frequencies. At higher frequencies, the delay is shorter — EMA reacts faster to recent changes than SMA.

### 3.5 Computational Advantages

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

- $O(1)$ per update
- $O(1)$ memory (single state variable)
- No buffering or window management
- Trivially vectorized with `.ewm()` in pandas

---

## 4. EMA Variants: DEMA, TEMA, and the Zero-Lag Goal

A fundamental limitation of SMA and EMA is phase lag. DEMA and TEMA attempt to recover by incorporating higher-order EMA compositions to cancel lag.

### 4.1 DEMA (Double EMA)

Define $E_1 = \text{EMA}(x)$ and $E_2 = \text{EMA}(E_1)$ (EMA-of-EMA). Then:

$$
\text{DEMA} = 2E_1 - E_2
$$

The transfer function is:

$$
H_{\text{DEMA}}(z) = 2H_{\text{EMA}}(z) - H_{\text{EMA}}^2(z) = 1 - (1 - H_{\text{EMA}}(z))^2
$$

This structure cancels the first-order lag term in the Taylor expansion of $H_{\text{EMA}}$, producing a filter with significantly reduced phase lag while maintaining the first-order roll-off.

### 4.2 TEMA (Triple EMA)

Extending the same pattern:

$$
\text{TEMA} = 3E_1 - 3E_2 + E_3
$$

where $E_3 = \text{EMA}(E_2) = \text{EMA}(\text{EMA}(\text{EMA}(x)))$. In transfer-function form:

$$
H_{\text{TEMA}}(z) = 3H_{\text{EMA}}(z) - 3H_{\text{EMA}}^2(z) + H_{\text{EMA}}^3(z) = 1 - (1 - H_{\text{EMA}}(z))^3
$$

### 4.3 General Form

The pattern generalizes to $k$th-order zero-lag approximations via the binomial series:

$$
\text{EMA}_k = 1 - (1 - H_{\text{EMA}})^k = \sum_{i=1}^{k} (-1)^{i-1} \binom{k}{i} H_{\text{EMA}}^i
$$

### 4.4 Caveats

- **Noise amplification**: DEMA/TEMA reduce lag but amplify high-frequency noise because the transfer function magnitude exceeds 1 at certain frequencies.
- **Overshoot**: The step response overshoots (DEMA ~13%, TEMA ~25%), which can produce false crossovers.
- **Not true zero-lag**: The label is aspirational; DEMA/TEMA reduce but don't eliminate lag, especially for low-frequency components.

| Filter | Lag reduction vs EMA | Noise gain (10-bar equivalent) |
|--------|---------------------|-------------------------------|
| EMA    | baseline            | 0.0 dB                        |
| DEMA   | ~50%                | +2.3 dB                       |
| TEMA   | ~67%                | +4.1 dB                       |

```python
def tema(series: pd.Series, span: int) -> pd.Series:
    e1 = series.ewm(span=span, adjust=False).mean()
    e2 = e1.ewm(span=span, adjust=False).mean()
    e3 = e2.ewm(span=span, adjust=False).mean()
    return 3 * e1 - 3 * e2 + e3
```

---

## 5. ALMA (Arnaud Legoux Moving Average)

### 5.1 Motivation

ALMA, introduced by Arnaud Legoux and Dimitris Tsokakis (2009), addresses the fundamental tension between smoothness and lag by using a **shifted Gaussian weight window**. Unlike SMA (uniform lag), WMA (linear lag decay), or EMA (exponential lag decay), ALMA allows continuous control over the balance via an explicit offset parameter.

### 5.2 Definition

ALMA is a finite-impulse-response (FIR) filter with Gaussian-distributed weights that are asymmetrically centered within the window:

$$
\text{ALMA}_t = \sum_{i=0}^{n-1} w_i \cdot x_{t - (n-1-i)}
$$

The weights are defined by a Gaussian (normal) probability density function:

$$
w_i = e^{-\frac{(i - m)^2}{2\sigma^2}}, \quad i = 0, 1, \dots, n-1
$$

followed by normalization: $\tilde{w}_i = w_i / \sum_{j=0}^{n-1} w_j$.

### 5.3 Parameters

The filter has three tunable parameters:

**1. Window size $n$** — number of taps (length of the FIR filter). Controls the overall smoothness. Larger $n$ = smoother output, more lag, greater noise reduction.

**2. Offset $m$** — shifts the Gaussian center away from the window's rightmost edge:

$$
m = \text{offset} \cdot (n - 1)
$$

The `offset` parameter is conventionally expressed on $[0, 1]$:
- $m = 0 \cdot (n-1) = 0$: Gaussian centered at the **oldest** sample. Equivalent to a centered Gaussian filter on the reversed series — maximum smoothness, maximum lag (comparable to SMA).
- $m = 0.85 \cdot (n-1)$ (typical default): Gaussian centered 85% of the way through the window. Assigns higher weight to recent observations — reduced lag.
- $m = 1.0 \cdot (n-1)$: Gaussian centered at the **newest** sample. Minimal lag but minimal smoothing.

**3. Sigma $\sigma$** — standard deviation of the Gaussian envelope, controlling the spread:

$$
\sigma = \frac{n}{\text{sigma}}
$$

where `sigma` is a user-specified parameter (typical default: 6.0). A larger `sigma` value spreads the Gaussian wider, smoothing more; a smaller value concentrates weights near the center, reducing smoothness.

### 5.4 Key Innovation: The Offset Parameter

The offset is ALMA's distinguishing feature. In a standard (symmetric) Gaussian filter, weights decay equally in both directions from the center, producing linear phase (like SMA). By offsetting the center toward the right, ALMA:

1. Assigns larger weights to more recent observations — reducing lag.
2. Still smoothly decays weights to the left — preserving the Gaussian bell shape and its excellent frequency-domain properties.
3. Avoids the abrupt truncation of SMA or the geometric discontinuity of EMA.

The resulting filter has **near-linear phase in the passband** (unlike EMA) with **controllable group delay** (unlike SMA's fixed $n/2$).

### 5.5 Frequency Response

ALMA's frequency response is approximately Gaussian in the frequency domain (the Fourier transform of a Gaussian is a Gaussian). This means:

- **No side lobes**: Gaussian windows have the best time-bandwidth product (within the uncertainty principle bounds). No Gibbs phenomenon ringing.
- **Smooth roll-off**: Transition band shape is controlled by $\sigma$.
- **Controllable cutoff**: For a given $n$, increasing $\sigma$ narrows the Gaussian in time → widens it in frequency → less smoothing.

### 5.6 Relationship to Standard Gaussian Filter

An $n$-tap symmetric Gaussian filter (offset = 0.5) has weights:

$$
w_i^{\text{sym}} = e^{-\frac{(i - \frac{n-1}{2})^2}{2\sigma^2}}
$$

This is zero-phase when applied bidirectionally (forward-backward filtering). ALMA with offset = 0.85 is a **causal approximation** of the Gaussian filter with reduced phase lag. The offset parameter effectively creates a minimum-phase-like behavior while preserving the Gaussian envelope's spectral purity.

### 5.7 Implementation

```python
import numpy as np

def alma(series: np.ndarray, n: int, offset: float = 0.85, sigma: float = 6.0) -> np.ndarray:
    """
    Arnaud Legoux Moving Average

    Parameters
    ----------
    series : np.ndarray, shape (T,)
        Input time series.
    n : int
        Window length (number of taps).
    offset : float, default 0.85
        Controls lag-smoothness tradeoff on [0, 1].
        0 = max smoothing/max lag; 1 = min smoothing/min lag.
    sigma : float, default 6.0
        Controls Gaussian spread. Larger = wider = smoother.
    """
    m = offset * (n - 1)
    s = n / sigma
    i = np.arange(n)
    weights = np.exp(-0.5 * ((i - m) / s) ** 2)
    weights /= weights.sum()

    result = np.full_like(series, np.nan, dtype=np.float64)
    for t in range(n - 1, len(series)):
        window = series[t - n + 1 : t + 1]
        # reverse window so weights align (oldest * w_0, newest * w_{n-1})
        result[t] = np.dot(window[::-1], weights)  # or np.dot(window, weights[::-1])
    return result
```

For production use, a convolution-based implementation avoids the Python loop:

```python
def alma_fast(series: np.ndarray, n: int, offset: float = 0.85, sigma: float = 6.0) -> np.ndarray:
    m = offset * (n - 1)
    s = n / sigma
    weights = np.exp(-0.5 * (np.arange(n) - m)**2 / s**2)
    weights /= weights.sum()
    # Convolve with 'same' to preserve length; pad to handle edges properly
    padded = np.pad(series, (n - 1, 0), mode='edge')
    return np.convolve(padded, weights[::-1], mode='valid')
```

---

## 6. Cross-Comparison Table

The table below provides a quantitative comparison for $n = 20$ (equivalent span for EMA):

| Property | SMA | WMA (linear) | EMA | DEMA | TEMA | ALMA (n=20, offset=0.85) |
|---|---|---|---|---|---|---|
| **Group delay (samples)** | 9.5 | ~6.3 | ~9.0 (at DC) | ~3.5 | ~2.5 | ~3-5 (tunable) |
| **Phase linearity** | Perfect | Near-linear | Non-linear | Severely non-linear | Severely non-linear | Near-linear in passband |
| **Noise reduction** $\sum w_i^2$ | $1/n = 0.05$ | ~0.09 | $\alpha/2 \approx 0.048$ | ~0.14 | ~0.22 | ~0.06-0.10 (depends on sigma) |
| **-3dB cutoff (norm. freq.)** | $0.44/n$ | ~$0.6/n$ | $\alpha/(2\pi)$ | ~$0.9/n$ | ~$1.2/n$ | Tunable via sigma |
| **Spectral leakage** | High (sinc sidelobes) | Medium | Low (single pole) | Medium | Medium | Very low (Gaussian) |
| **Stop-band attenuation** | Poor (-13dB 1st lobe) | Poor | 6 dB/oct | 6 dB/oct | 6 dB/oct | Excellent (no sidelobes) |
| **Overshoot on step** | 0% | 0% | 0% | ~13% | ~25% | 0% (depends on offset) |
| **Computational complexity** | $O(1)$ per point | $O(1)$ per point | $O(1)$ per point | $O(1)$ per point | $O(1)$ per point | $O(n)$ per point (or $O(1)$ with rolling update) |
| **Memory** | $O(n)$ buffer | $O(n)$ buffer | $O(1)$ scalar | $O(1)$ scalars | $O(1)$ scalars | $O(n)$ buffer |
| **Real-time ready** | Yes | Yes | Yes | Yes | Yes | Yes |
| **Pandas method** | `.rolling(n).mean()` | `.rolling(n).apply(wma)` | `.ewm(span=n)` | Custom | Custom | Custom |

### 6.1 Lag-Smoothness Pareto Frontier

Plotting the group delay against the effective noise reduction for $n=20$ across all variants reveals a Pareto frontier:

```
Low Lag <------------> High Smoothness
  TEMA                     SMA
  DEMA                     ALMA (low offset)
  ALMA (high offset)       EMA
  WMA
```

ALMA occupies a unique position on the frontier: by adjusting offset, it can sweep continuously from near-EMA-like behavior to near-SMA-like behavior, all while maintaining the Gaussian spectral profile.

---

## 7. Practical Guidance

### 7.1 Decision Matrix

| Signal characteristics | Recommended filter | Rationale |
|---|---|---|
| **Slow trend, low noise** (e.g., monthly economic indicators) | SMA | Simplicity, linear phase, zero overshoot. Lag is irrelevant at low frequency. |
| **Slow trend, high noise** | ALMA with low offset (~0.5) | Gaussian window suppresses noise best; low offset ensures smoothness. |
| **Fast trend, low noise** (e.g., high-frequency trading signals) | TEMA or DEMA | Minimal lag; noise is tolerable because signal is already clean. |
| **Fast trend, high noise** | ALMA with medium offset (~0.85) | Best tradeoff: reduced lag + Gaussian noise suppression. |
| **Mean-reversion signals** | SMA or ALMA (offset=0.5) | Linear/near-linear phase preserves timing of zero-crossings. |
| **Trend following, entry timing** | ALMA (offset=0.85-0.95) | Reduced lag catches entries earlier than SMA; no DEMA/TEMA overshoot. |
| **Embedded system / microcontroller** | EMA | Single scalar state, no dynamic allocation, integer math possible. |
| **Real-time streaming, minimal latency** | DEMA or TEMA | Lowest group delay; acceptable for short-window use. |
| **Filter bank / multi-resolution analysis** | ALMA with varied offset | Consistent Gaussian spectral shape across scales. |

### 7.2 Parameter Tuning Heuristics for ALMA

1. **Start with offset = 0.85, sigma = 6.0** (original authors' recommendation).
2. **Window size $n$**: Use the dominant cycle period (e.g., via dominant cycle estimation or ACF). For daily data, $n = 20$ (one month) is a common starting point.
3. **Offset tuning**:
   - If the filter is too laggy → increase offset toward 0.95.
   - If the filter is too noisy → decrease offset toward 0.5.
4. **Sigma tuning**:
   - If the output looks jagged → increase sigma (spreads weights wider).
   - If the output oversmooths important inflection points → decrease sigma.
5. **Rule of thumb**: ALMA with offset=0.85 and sigma=6 has approximately the same lag as a WMA of length $n/2$ but with significantly better noise attenuation.

### 7.3 Pitfalls

- **Over-optimization**: ALMA's three parameters make it easy to curve-fit historical data. Validate out-of-sample.
- **Gaussian truncation**: For small $n$, the Gaussian tails are chopped by the finite window, introducing spectral leakage. Ensure $n \gg 2\sigma$ for the Gaussian to be well-approximated.
- **ALMA vs DEMA/TEMA**: ALMA preserves phase linearity (important for timing-dependent strategies). DEMA/TEMA sacrifice phase linearity for lower lag but introduce overshoot that can trigger false signals in crossover systems.
- **Startup transient**: Like all FIR filters, ALMA requires $n$ samples before producing valid output. In streaming applications, pad initialization or accept NaN for the first $n-1$ points.

---

## 8. Summary

The Moving Average family spans a spectrum from the simplest uniform-average filter (SMA) through weighted variations (WMA, EMA) to higher-order compositions (DEMA, TEMA) and, finally, the parametrically flexible Gaussian-weighted ALMA. Each member occupies a distinct point on the lag-smoothness-complexity Pareto frontier:

- **SMA** anchors the smoothness end with perfect phase linearity but fixed $n/2$ lag.
- **WMA and EMA** offer moderate lag reduction at modest complexity cost but introduce non-linear phase.
- **DEMA/TEMA** pursue aggressive lag reduction at the cost of noise amplification and step-response overshoot.
- **ALMA** provides the most flexible tradeoff via its offset parameter, combining Gaussian spectral purity (no sidelobes) with continuously tunable group delay, making it the preferred choice when both smoothness and low lag are required and computational budget permits $O(n)$ FIR filtering.
