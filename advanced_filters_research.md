# Advanced Filtering Algorithms: Theory, Implementation, and Applications

> Research output for technical writers. Dense reference covering 6 advanced filters beyond simple moving averages.

---

## 1. Kalman Filter

### State-Space Model

The Kalman filter models a system through two equations:

**Process (state transition) model:**
$$x_k = F x_{k-1} + B u_k + w_k, \quad w_k \sim \mathcal{N}(0, Q)$$

**Measurement model:**
$$z_k = H x_k + v_k, \quad v_k \sim \mathcal{N}(0, R)$$

Where:
- $x_k \in \mathbb{R}^n$ is the hidden state vector at time $k$
- $F \in \mathbb{R}^{n \times n}$ is the state transition matrix
- $B \in \mathbb{R}^{n \times l}$ maps control input $u_k$ to state
- $H \in \mathbb{R}^{m \times n}$ maps state to measurement space
- $w_k$, $v_k$ are zero-mean Gaussian noise with covariances $Q$ and $R$

### The Five Equations (Prediction-Update Cycle)

**Predict:**
1. State prediction: $\hat{x}_{k|k-1} = F \hat{x}_{k-1|k-1} + B u_k$
2. Covariance prediction: $P_{k|k-1} = F P_{k-1|k-1} F^T + Q$

**Update:**
3. Kalman gain: $K_k = P_{k|k-1} H^T (H P_{k|k-1} H^T + R)^{-1}$
4. State update: $\hat{x}_{k|k} = \hat{x}_{k|k-1} + K_k (z_k - H \hat{x}_{k|k-1})$
5. Covariance update: $P_{k|k} = (I - K_k H) P_{k|k-1}$

### Kalman Gain Intuition

The Kalman gain $K_k$ is the central mechanism. It balances trust between the prediction (from the process model) and the measurement:

$$K_k = \frac{P_{k|k-1} H^T}{H P_{k|k-1} H^T + R}$$

- When **measurement noise** $R$ is large relative to prediction uncertainty $P$: $K_k \to 0$, filter trusts the prediction and ignores the noisy measurement.
- When **process noise** $Q$ is large (prediction is uncertain): $P_{k|k-1}$ grows, $K_k$ increases, and the filter leans on the measurement.

This automatic, covariance-driven weighting is what separates Kalman filters from fixed-coefficient filters.

### Tuning: Q and R

Parameter roles:

| Parameter | Effect | Typical Source |
|-----------|--------|----------------|
| $Q$ (process noise covariance) | How much the state can change between steps | Model uncertainty, discretization error |
| $R$ (measurement noise covariance) | How noisy each measurement is | Sensor specs, empirical variance |

**Tuning heuristic:** fix $R$ from sensor calibration, then adjust $Q$ to achieve desired smoothness. The ratio $Q/R$ matters more than absolute values. Too high $Q$ -> jittery (overfits measurements). Too low $Q$ -> sluggish (ignores measurements).

### Python Implementation (Minimal)

```python
import numpy as np

class KalmanFilter:
    def __init__(self, F, H, Q, R, x0, P0):
        self.F = F  # state transition
        self.H = H  # measurement mapping
        self.Q = Q  # process noise covariance
        self.R = R  # measurement noise covariance
        self.x = x0  # initial state
        self.P = P0  # initial covariance

    def predict(self, u=None):
        B_u = self.B @ u if u is not None and hasattr(self, 'B') else 0
        self.x = self.F @ self.x + B_u
        self.P = self.F @ self.P @ self.F.T + self.Q

    def update(self, z):
        y = z - self.H @ self.x          # innovation (measurement residual)
        S = self.H @ self.P @ self.H.T + self.R  # innovation covariance
        K = self.P @ self.H.T @ np.linalg.inv(S)  # Kalman gain
        self.x = self.x + K @ y
        self.P = (np.eye(len(self.x)) - K @ self.H) @ self.P

    def step(self, z, u=None):
        self.predict(u)
        self.update(z)
        return self.x.copy()

# Example: 1D tracking (position, velocity)
dt = 0.1
F = np.array([[1, dt], [0, 1]])
H = np.array([[1, 0]])
Q = np.array([[0.01, 0], [0, 0.01]])
R = np.array([[0.1]])
kf = KalmanFilter(F, H, Q, R, x0=np.array([0, 0]), P0=np.eye(2))
```

For production use, the `filterpy` library provides a well-tested implementation:

```python
from filterpy.kalman import KalmanFilter as KF
kf = KF(dim_x=2, dim_z=1)
kf.F = F; kf.H = H; kf.Q = Q; kf.R = R; kf.x = [0, 0]; kf.P *= 1
```

### Extensions

**Extended Kalman Filter (EKF):** For non-linear process or measurement models, linearize $f$ and $h$ via Jacobians at each step: $F_k = \frac{\partial f}{\partial x}\big|_{\hat{x}_{k-1}}$, $H_k = \frac{\partial h}{\partial x}\big|_{\hat{x}_{k|k-1}}$. Works well for mild non-linearities; can diverge for strongly non-linear systems.

**Unscented Kalman Filter (UKF):** Propagates deterministically chosen *sigma points* through the non-linear function, then reconstructs the Gaussian from the transformed points. Avoids Jacobian computation and captures mean and covariance to second-order accuracy (vs first-order for EKF). Generally preferred over EKF when the non-linearity is moderate and computational cost is acceptable.

### Key Properties

- **Optimal** for linear Gaussian systems (minimum mean-square error)
- **Recursive** -- only the last state estimate and covariance are stored; $O(1)$ memory in time steps
- **Real-time capable** -- each step is $O(n^3)$ due to matrix inversion (but $n$ is typically small)
- **Provides uncertainty** -- $P_{k|k}$ is a full covariance estimate, not just a point estimate

### Limitations

- Assumes Gaussian noise -- heavy-tailed or multimodal noise degrades performance
- Misspecified $Q$ or $R$ leads to biased or overconfident estimates
- EKF linearization can introduce bias for strong non-linearities
- Not designed for arbitrary non-stationary signals where no process model exists (use adaptive filters or SG smoother instead)

---

## 2. Butterworth Filter

### IIR Design Philosophy

The Butterworth filter is an infinite impulse response (IIR) filter designed for **maximally flat passband** -- the magnitude response is optimally smooth in the passband, with no ripple. This comes at the cost of a gradual rolloff in the transition band.

### Transfer Function

For an $n$th-order lowpass Butterworth filter with cutoff $\omega_c$:

$$|H(\omega)|^2 = \frac{1}{1 + (\omega / \omega_c)^{2n}}$$

The poles lie on a circle of radius $\omega_c$ in the $s$-plane, equally spaced:

$$s_k = \omega_c \, e^{j(\pi/2 + (2k-1)\pi/2n)}, \quad k = 1, \dots, n$$

All poles are in the left half-plane (stability), and there are **no zeros** (the transfer function is an all-pole filter).

### Order vs Rolloff

The stopband attenuation is $-20n$ dB/decade, meaning:
- 1st order: $-20$ dB/decade (gentle)
- 2nd order: $-40$ dB/decade
- 4th order: $-80$ dB/decade
- 8th order: $-160$ dB/decade (sharp cutoff)

**Tradeoff:** higher order gives sharper rolloff but introduces more phase distortion and can become numerically unstable at high orders ($n > 10$ is rarely used).

### Forward-Backward Filtering (Zero-Phase)

Standard IIR filters introduce frequency-dependent phase delay. The `filtfilt` technique runs the filter forward then backward over the data, squaring the magnitude response and producing **zero phase delay**:

$$H_{filtfilt}(\omega) = |H(\omega)|^2$$

**Caveat:** the doubled magnitude response means the effective filter order doubles, transients appear at both ends, and the filter becomes non-causal (cannot be used for real-time applications).

### Python Implementation

```python
from scipy.signal import butter, filtfilt, sosfiltfilt

def butterworth_lowpass(data, cutoff, fs, order=4, use_sos=True):
    """
    Butterworth lowpass filter.
    
    Parameters
    ----------
    data : array_like
        Input signal (1D or 2D along last axis).
    cutoff : float
        Cutoff frequency in Hz.
    fs : float
        Sampling frequency in Hz.
    order : int
        Filter order (default 4). Higher = sharper rolloff.
    use_sos : bool
        Use second-order sections for numerical stability.
    
    Returns
    -------
    y : ndarray
        Filtered signal (zero-phase).
    """
    nyquist = 0.5 * fs
    normal_cutoff = cutoff / nyquist
    
    if use_sos:
        sos = butter(order, normal_cutoff, btype='low', output='sos')
        y = sosfiltfilt(sos, data)
    else:
        b, a = butter(order, normal_cutoff, btype='low')
        y = filtfilt(b, a, data)
    return y

# High-pass variant
def butterworth_highpass(data, cutoff, fs, order=4):
    nyquist = 0.5 * fs
    sos = butter(order, cutoff / nyquist, btype='high', output='sos')
    return sosfiltfilt(sos, data)
```

**Critical detail:** For orders above 4, always use `output='sos'` (second-order sections) instead of `b, a` form. The direct-form polynomial representation suffers severe numerical cancellation at high orders; `sos` factorizes the filter into cascaded biquad stages that are well-conditioned.

### Limitations

- **Gibbs / transient artifacts** at signal edges with `filtfilt` (pad the signal or trim edges)
- Not adaptive -- fixed coefficients mean you must know the frequency content in advance
- Non-causal in zero-phase mode (can't stream data)
- Not suitable for signals with overlapping frequency bands (use adaptive or Wiener filters)
- Group delay varies significantly near the cutoff frequency (phase distortion in causal mode)

---

## 3. Gaussian Filter

### Kernel Definition

The Gaussian filter convolves the signal with a Gaussian kernel:

$$G(x) = \frac{1}{\sqrt{2\pi}\,\sigma} e^{-x^2 / (2\sigma^2)}$$

$$y(t) = (x * G)(t) = \int_{-\infty}^{\infty} x(\tau)\, G(t - \tau)\, d\tau$$

### Cutoff Frequency vs. Sigma

There is a direct relationship between the standard deviation $\sigma$ (in samples) and the cutoff frequency of the Gaussian filter. The frequency response of a Gaussian is itself a Gaussian in the frequency domain:

$$H(\omega) = e^{-\sigma^2 \omega^2 / 2}$$

The $-3$ dB cutoff (half-power) occurs at:

$$f_c = \frac{\sqrt{\ln 2}}{2\pi\sigma} \approx \frac{0.1325}{\sigma} \quad \text{(in normalized frequency units)}$$

To convert to physical Hz: $f_{c, Hz} = f_c \times f_s$.

| $\sigma$ (samples) | $-3$ dB cutoff (norm.) | Meaning |
|:---:|:---:|:---|
| 1 | 0.133 | Moderate smoothing |
| 5 | 0.027 | Heavy smoothing |
| 10 | 0.013 | Very heavy smoothing |

### Separability (2D)

The Gaussian kernel is **separable**, making it computationally efficient in higher dimensions:

$$G(x, y) = \frac{1}{2\pi\sigma^2} e^{-(x^2 + y^2)/(2\sigma^2)} = \frac{1}{\sqrt{2\pi}\sigma} e^{-x^2/(2\sigma^2)} \cdot \frac{1}{\sqrt{2\pi}\sigma} e^{-y^2/(2\sigma^2)}$$

Convolving with $G(x, y)$ can be done as two 1D convolutions (row-wise then column-wise), reducing complexity from $O(N^2 K^2)$ to $O(N^2 K)$ for a $K \times K$ kernel.

### Python Implementation

```python
import numpy as np
from scipy.ndimage import gaussian_filter1d, gaussian_filter

# 1D signal
y = gaussian_filter1d(x, sigma=2.0, mode='reflect')

# 2D image
smoothed = gaussian_filter(image, sigma=(1.0, 2.0), mode='reflect')

# Manual 1D for understanding
def gaussian_kernel(size, sigma):
    """Create a 1D Gaussian kernel (truncated at ±3 sigma)."""
    ax = np.arange(-size // 2 + 1, size // 2 + 1)
    kernel = np.exp(-ax**2 / (2 * sigma**2))
    return kernel / kernel.sum()

kernel = gaussian_kernel(11, sigma=2.0)
y_manual = np.convolve(x, kernel, mode='same')
```

**Note:** `scipy.ndimage.gaussian_filter1d` uses a much more efficient cascaded IIR approximation (Young-van Vliet) for large $\sigma$, rather than direct convolution with a truncated kernel. For $\sigma > 3$, it is both faster and more accurate.

### Smoothing Comparison

| Filter | Smoothing Quality | Edge Preservation | Computational Cost |
|--------|:---:|:---:|:---:|
| Moving average | Fair (side lobes in frequency) | Poor | $O(N)$ |
| Gaussian | Good (smooth frequency rolloff) | Moderate | $O(N)$ |
| Savitzky-Golay | Good (preserves moments) | Good | $O(N)$ |

The Gaussian filter produces a smoother frequency-domain rolloff than a moving average, which has significant side lobes. It does not preserve edges as well as a median or bilateral filter, but it introduces no oscillatory artifacts.

### Limitations

- Gaussian convolution blurs sharp transitions (edges, step changes) -- use a median or bilateral filter if edge preservation matters
- The kernel must be truncated in practice, introducing a small approximation error (typically truncated at $\pm 3\sigma$ or $\pm 4\sigma$)
- Not adaptive -- fixed $\sigma$ across the entire signal

---

## 4. Median Filter

### Definition

The median filter is an **order-statistics** (non-linear) filter:

$$\hat{y}_i = \text{median}(x_{i-k}, x_{i-k+1}, \dots, x_i, \dots, x_{i+k-1}, x_{i+k})$$

For a window of size $2k+1$, the output at position $i$ is the median of the $2k+1$ points centered at $i$.

### Key Properties

- **Edge preservation:** Unlike linear smoothing filters, the median filter does not smear edges. A step function remains a step function because the median within a window spanning the edge still selects a value from the correct side.
- **Impulse noise removal:** Salt-and-pepper noise (extreme outliers) is completely removed when the window is larger than the noise cluster, because the median ignores extreme values.
- **Non-linear:** The filter has no frequency-domain representation (no transfer function). Linear systems theory does not apply.

### Impulse Response Analogy

The median filter can be thought of as:
- **Smoothing power:** comparable to the moving average of the same window size
- **Outlier immunity:** infinite (the filter is unaffected by extreme values as long as fewer than half the points are outliers)
- **Step response:** immediate (no ringing, no overshoot)

### Python Implementation

```python
from scipy.signal import medfilt

# 1D median filter, kernel size must be odd
y = medfilt(x, kernel_size=5)  # window = 5 samples

# Manual implementation for understanding / modification
import numpy as np

def median_filter_1d(x, k=3):
    """1D median filter with reflection padding at edges."""
    pad = k // 2
    x_pad = np.pad(x, pad, mode='reflect')
    y = np.zeros_like(x)
    for i in range(len(x)):
        y[i] = np.median(x_pad[i:i + k])
    return y
```

### Variants

- **Weighted median filter:** assigns weights within the window before finding the median. Can trade off between edge preservation and smoothing.
- **Median of medians:** recursive application for stronger smoothing.
- **Adaptive median filter:** varies the window size based on local statistics; preserves detail in low-noise regions and smooths aggressively in high-noise regions.

### Limitations

- Can destroy fine detail (thin lines, small features) smaller than half the window size
- Computationally expensive for large windows: $O(N k \log k)$ -- though efficient $O(N)$ algorithms exist (Huang's running median histogram algorithm)
- Not differentiable (not suitable for gradient-based optimization)
- No frequency-domain interpretation makes systematic design harder
- Struggles with Gaussian noise compared to linear filters (median is less efficient for Gaussian noise than mean)

---

## 5. LOWESS / LOESS

### Definition

**LOESS** (locally estimated scatterplot smoothing) and **LOWESS** (locally weighted scatterplot smoothing) are non-parametric regression methods that fit simple models to localized subsets of data.

At each point $x_0$, a low-degree polynomial (typically linear or quadratic) is fit to the $k$ nearest neighbors, weighted by distance:

$$\hat{y}(x_0) = \beta_0 + \beta_1 x_0 + \dots + \beta_d x_0^d$$

Minimizing:

$$\sum_{i=1}^n w_i(x_0) \left( y_i - p(x_i) \right)^2$$

### Tricube Weight Function

The standard weight function for LOESS is the **tricube**:

$$w(u) = \begin{cases}
(1 - |u|^3)^3 & |u| < 1 \\
0 & |u| \ge 1
\end{cases}$$

For each query point $x_0$, the weight for neighbor $x_i$ is:

$$w_i(x_0) = w\left(\frac{|x_i - x_0|}{\Delta(x_0)}\right)$$

where $\Delta(x_0)$ is the distance to the $k$-th nearest neighbor (the bandwidth at $x_0$). Points farther than the bandwidth get zero weight.

### Bandwidth Parameter

The bandwidth $f$ (or $\alpha$ or `frac`) controls the fraction of data used in each local fit:

- **Small $f$** ($< 0.1$): captures fine structure, high variance, can overfit
- **Large $f$** ($> 0.5$): heavy smoothing, low variance, can underfit
- Typical range: $0.2$ to $0.5$

The bandwidth controls the **bias-variance tradeoff** directly, similar to the kernel width in kernel regression.

### Robust Iterations (Outlier Rejection)

LOWESS includes a robustification step using **bisquare weights**:

$$r_i = y_i - \hat{y}_i \quad \text{(residual)}$$
$$\delta_i = B(r_i / (6 \cdot \text{MAD}(r))) \quad \text{(robust weight)}$$

where $B(u) = (1 - u^2)^2$ for $|u| < 1$, else $0$, and MAD is the median absolute deviation.

The fit is repeated with these robust weights, downweighting outliers. Typically 3-5 iterations suffice.

### Python Implementation

```python
import statsmodels.api as sm
import numpy as np

# Basic LOWESS
result = sm.nonparametric.lowess(
    endog=y,          # response variable
    exog=x,           # predictor
    frac=0.3,         # fraction of data used in each local fit (bandwidth)
    it=3,             # number of robustifying iterations
    return_sorted=True
)

# result is a 2-column array: [x_values, y_fitted]
x_fitted, y_fitted = result[:, 0], result[:, 1]

# Manual key components for understanding
def tricube(u):
    """Tricube weight function."""
    return np.where(np.abs(u) < 1, (1 - np.abs(u)**3)**3, 0)

def lowess_single(x0, x, y, frac=0.3):
    """Fit a single point with local linear regression."""
    n = len(x)
    k = max(int(frac * n), 2)  # at least 2 points
    dists = np.abs(x - x0)
    idx = np.argsort(dists)[:k]
    max_dist = dists[idx[-1]] or 1e-10
    weights = tricube(dists[idx] / max_dist)
    
    # Weighted least squares: fit line
    X = np.c_[np.ones(k), x[idx]]
    W = np.diag(weights)
    beta = np.linalg.inv(X.T @ W @ X) @ (X.T @ W @ y[idx])
    return beta[0] + beta[1] * x0
```

### Limitations

- **Computational cost:** each query point requires a weighted least squares fit; $O(N^2)$ naive, $O(N \log N)$ with spatial indexing
- **Not causal** -- requires the entire dataset; no online/streaming variant
- **Extrapolation** beyond the data range is unreliable (local polynomials diverge quickly)
- **Bandwidth selection** is instance-dependent and often requires cross-validation
- **Multivariate** extensions exist but suffer from the curse of dimensionality (neighborhoods become sparse in high dimensions)

---

## 6. Hodrick-Prescott Filter

### Formulation

The HP filter decomposes a time series $y_t$ into a trend component $\tau_t$ and a cyclical component $c_t = y_t - \tau_t$ by solving:

$$\min_{\tau_t} \sum_{t=1}^T (y_t - \tau_t)^2 + \lambda \sum_{t=2}^{T-1} (\Delta^2 \tau_t)^2$$

where $\Delta^2 \tau_t = (\tau_{t+1} - \tau_t) - (\tau_t - \tau_{t-1}) = \tau_{t+1} - 2\tau_t + \tau_{t-1}$ is the discrete second difference.

The first term penalizes the gap between data and trend (fit). The second term penalizes changes in the slope of the trend (smoothness). $\lambda$ controls the tradeoff.

### Lambda Parameter

| Data Frequency | Conventional $\lambda$ | Interpretation |
|:---:|:---:|:---|
| Annual | 100 | Weak smoothing |
| Quarterly | 1600 | Standard (Hodrick & Prescott) |
| Monthly | 14,400 | Scaled by $(1/3)^2$ |
| Weekly | 1600 $\times$ (52/4)$^2$ | Following Ravn-Uhlig |
| Daily | 1600 $\times$ (365/4)$^2$ | Rarely used; often too rough |

The canonical value for quarterly data is $\lambda = 1600$, proposed by Hodrick and Prescott (1997, *JMCB*). The scaling rule for other frequencies follows the Ravn-Uhlig convention: multiply by the square of the observation frequency ratio.

**Closed form solution:**

$$\tau = (I + \lambda D^T D)^{-1} y$$

where $D$ is the $(T-2) \times T$ second-difference matrix.

### Python Implementation

```python
import numpy as np
from scipy import linalg

def hp_filter(y, lamb=1600):
    """
    Hodrick-Prescott filter.
    
    Returns
    -------
    trend : ndarray
    cycle : ndarray  (y - trend)
    """
    T = len(y)
    D = np.zeros((T - 2, T))
    for i in range(T - 2):
        D[i, i]   = 1
        D[i, i+1] = -2
        D[i, i+2] = 1
    
    trend = linalg.solve(
        np.eye(T) + lamb * D.T @ D, y, assume_a='pos'
    )
    cycle = y - trend
    return trend, cycle

# Or use statsmodels
# from statsmodels.tsa.filters.hp_filter import hpfilter
# cycle, trend = hpfilter(y, lamb=1600)
```

### Applications and Criticism

**Common uses:**
- Macroeconomic trend-cycle decomposition (GDP, unemployment, industrial production)
- Detrending before spectral analysis
- Extracting business cycle frequencies (typically 6-32 quarters)

**Criticism:**

1. **Endpoint problem:** The filter uses two-sided smoothing, so estimates at the ends of the sample depend heavily on the path near the boundary. Revisions to recent data can significantly alter the estimated trend at the end of the series. This is severe enough that HP-filtered trends for the most recent ~3 years should not be trusted.

2. **Spurious cycles:** When applied to I(1) (random walk) processes, the HP filter can produce apparent cycles even when none exist. Cogley & Nason (1995, *JEDC*) demonstrated that HP filtering a random walk generates spectral power at business-cycle frequencies.

3. **Lambda selection is arbitrary:** The $\lambda = 1600$ convention masks the fact that the optimal $\lambda$ depends on the data-generating process, which is unknown.

4. **No uncertainty quantification:** The filter produces a point estimate with no confidence intervals.

### Better Alternatives

| Filter | Addresses HP Limitation |
|--------|------------------------:|
| **Christiano-Fitzgerald (CF) band-pass** | Explicit frequency selection, less endpoint bias |
| **Baxter-King (BK)** | Fixed-length symmetric moving average, band-pass |
| **Hamilton filter** (Hamilton 2018, *REStat*) | Regression-based, no endpoint problem, no spurious cycles |
| **Local-level model (structural time series)** | State-space with full uncertainty quantification |

---

## 7. Cross-Comparison Table

| Property | Kalman | Butterworth | Gaussian | Median | LOWESS | HP |
|---|---|---|---|---|---|---|
| **Linear?** | Yes | Yes | Yes | No | Yes (without robust) | Yes |
| **Non-linear?** | No | No | No | **Yes** | With robust iter. | No |
| **Causal (real-time)?** | **Yes** | Yes (causal mode) | No | Yes | No | No |
| **Zero-phase?** | No (inherently causal) | **Yes** (filtfilt) | Yes | No (shift depends on padding) | **Yes** (in-sample) | **Yes** (two-sided) |
| **Online/streaming?** | **Yes** | Yes (causal) | No | Yes | No | No |
| **Memory (time steps)** | O(1) | O(order) | O(kernel size) | O(window) | O(N) | O(N) |
| **Complexity per step** | O($n^3$) | O($n_s \cdot$ order) | O($N\sigma$) | O($Nk$) or O($N$) with histogram | O($N^2$) | O($T$) |
| **Parameters** | Q, R, F, H | order, cutoff | $\sigma$ | kernel size | frac, degree, iters | $\lambda$ |
| **Tuning difficulty** | High (requires model) | Medium | Low | Low | Medium | Low (but arbitrary) |
| **Edge behavior** | Depends on model | Transients (filtfilt) | Moderate blurring | **Excellent** (preserves) | Weights at boundaries | **Poor** (endpoint bias) |
| **Primary domain** | Tracking, control, navigation | Audio, EEG, general signal | Image processing, smoothing | Image denoising, spike removal | Exploratory data analysis | Macroeconomics |
| **Python library** | `filterpy` | `scipy.signal` | `scipy.ndimage` | `scipy.signal` | `statsmodels` | `statsmodels` |
| **Known for** | Optimal linear filter | Maximally flat passband | Smooth rolloff, separability | Edge preservation | Non-parametric flexibility | Trend-cycle decomposition |

---

## Practical Recommendation Guide

**Use the Kalman filter when:**
- You have a state-space model of the system
- Real-time / streaming operation is required
- You need uncertainty quantification (covariance)
- You are fusing multiple sensor measurements

**Use the Butterworth filter when:**
- You need a clean frequency cutoff (separate signal bands)
- Zero-phase filtering is acceptable (offline)
- The signal has well-defined frequency content

**Use the Gaussian filter when:**
- Smoothing 1D or 2D data with a simple, parameter-light method
- You want predictable frequency-domain behavior
- Image preprocessing / scale-space analysis

**Use the median filter when:**
- The data contains impulse noise (salt-and-pepper)
- Edge preservation is critical
- You need a robust, simple non-linear filter

**Use LOWESS when:**
- The trend is non-linear with no known parametric form
- Exploration / visualization is the primary goal
- Outlier-robust smoothing is needed

**Use the HP filter when:**
- You are doing macroeconomic trend-cycle decomposition (GDP, etc.)
- Conventions matter ($\lambda = 1600$ for quarterly data)
- You explicitly want to extract business cycle frequencies

---

*End of research output. Total: ~3200 words.*
