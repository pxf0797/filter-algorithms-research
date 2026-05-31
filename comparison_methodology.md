# Quantitative Filter Comparison: Methodology and Metrics

> A rigorous framework for benchmarking and comparing digital signal filtering algorithms across accuracy, timing, smoothness, performance, and robustness dimensions.

---

## 1. Accuracy Metrics

Accuracy metrics quantify how well a filter's output approximates the true underlying signal. These require ground-truth data, which is available in synthetic benchmarks but must be estimated in production settings.

### 1.1 Mean Squared Error (MSE)

\[
\text{MSE} = \frac{1}{n}\sum_{i=1}^{n}(y_i - \hat{y}_i)^2
\]

- **Range**: \([0, \infty)\); lower is better.
- **Interpretation**: Penalizes large errors quadratically. Sensitive to outliers — a single bad point can dominate.
- **Use case**: Primary metric when large deviations are disproportionately harmful (e.g., safety-critical systems).

```python
import numpy as np

def mean_squared_error(y_true, y_pred):
    return np.mean((y_true - y_pred) ** 2)
```

### 1.2 Root Mean Squared Error (RMSE)

\[
\text{RMSE} = \sqrt{\frac{1}{n}\sum_{i=1}^{n}(y_i - \hat{y}_i)^2}
\]

- **Range**: \([0, \infty)\); lower is better.
- **Interpretation**: Same units as the original signal. If RMSE = 2.5 on a price series, the "typical" error magnitude is 2.5 units.
- **Note**: Still outlier-sensitive (inherits from MSE).

```python
def root_mean_squared_error(y_true, y_pred):
    return np.sqrt(mean_squared_error(y_true, y_pred))
```

### 1.3 Mean Absolute Error (MAE)

\[
\text{MAE} = \frac{1}{n}\sum_{i=1}^{n}|y_i - \hat{y}_i|
\]

- **Range**: \([0, \infty)\); lower is better.
- **Interpretation**: Linear penalty. More robust to outliers than MSE.
- **Tradeoff**: An optimizer minimizing MAE yields the conditional median, not the mean. This matters when residuals are asymmetric.

```python
def mean_absolute_error(y_true, y_pred):
    return np.mean(np.abs(y_true - y_pred))
```

### 1.4 SNR Improvement

\[
\text{SNR}_{\text{imp}} = 10 \log_{10}\left(\frac{\text{MSE}_{\text{input}}}{\text{MSE}_{\text{output}}}\right)
\]

- **Range**: \((-\infty, \infty)\); positive means improvement.
- **Interpretation**: Measured in dB. +6 dB means the filtered signal has half the error power of the raw input.
- **Use case**: Comparing filters across different noise regimes; normalizes by input quality.

```python
def snr_improvement(y_true, y_raw, y_filtered):
    mse_in = mean_squared_error(y_true, y_raw)
    mse_out = mean_squared_error(y_true, y_filtered)
    return 10 * np.log10(mse_in / mse_out)
```

### 1.5 Peak Signal-to-Noise Ratio (PSNR)

\[
\text{PSNR} = 10 \log_{10}\left(\frac{\text{max}(y)^2}{\text{MSE}}\right)
\]

- **Range**: \([0, \infty)\); higher is better.
- **Interpretation**: Ratio of peak signal power to error power. Directly comparable across signals with different amplitudes.
- **Use case**: Comparing filters applied to signals with very different dynamic ranges.

```python
def psnr(y_true, y_pred):
    mse_val = mean_squared_error(y_true, y_pred)
    peak = np.max(y_true) - np.min(y_true)
    return 10 * np.log10(peak ** 2 / mse_val)
```

### 1.6 Accuracy Metric Summary Table

| Metric | Range       | Outlier Robust? | Unit      | Best For                     |
|--------|-------------|----------------|-----------|------------------------------|
| MSE    | [0, inf)    | No             | squared   | Large-error critical systems |
| RMSE   | [0, inf)    | No             | original  | Interpretable error scale    |
| MAE    | [0, inf)    | Yes            | original  | Robust comparison            |
| SNR_imp| (-inf, inf) | No             | dB        | Normalized comparison        |
| PSNR   | [0, inf)    | No             | dB        | Cross-signal comparison      |

---

## 2. Lag / Timing Metrics

Lag is the systematic delay a filter introduces. In real-time applications (trading, control systems), lag is often the binding constraint.

### 2.1 Cross-Correlation Peak Shift

Compute the cross-correlation between input and output. The lag is the shift at which correlation is maximized:

\[
\hat{\tau} = \arg\max_{\tau} \sum_{i} x[i] \cdot y[i + \tau]
\]

- **Interpretation**: Positive value means the output lags the input.
- **Warning**: Only valid for stationary signals; meaningless for pure noise.

```python
def estimate_lag(signal_in, signal_out):
    corr = np.correlate(signal_in - np.mean(signal_in),
                        signal_out - np.mean(signal_out),
                        mode='full')
    lag = np.argmax(corr) - (len(signal_in) - 1)
    return lag
```

### 2.2 Phase Delay at Frequency f

From the filter's frequency response \(H(f)\):

\[
\phi(f) = \arg(H(f)) = \arctan\left(\frac{\text{Im}(H(f))}{\text{Re}(H(f))}\right)
\]

Phase delay in samples:

\[
\tau_p(f) = -\frac{\phi(f)}{2\pi f / f_s}
\]

- **Interpretation**: Each frequency component is delayed by a potentially different amount.
- **Constant vs. variable**: Linear-phase filters have constant group delay; nonlinear-phase filters distort waveform shape.

```python
def phase_delay(b, a, freqs, fs):
    """b, a: filter coefficients. freqs: array of frequencies in Hz."""
    _, h = signal.freqz(b, a, worN=freqs, fs=fs)
    phase = np.unwrap(np.angle(h))
    delays = -phase / (2 * np.pi * freqs / fs)
    return delays
```

### 2.3 Group Delay

\[
\tau_g(\omega) = -\frac{d\phi}{d\omega}
\]

- **Interpretation**: The delay of the signal's *envelope* (amplitude modulation), not the carrier. More relevant than phase delay for most applications.
- **Flat group delay** => all frequency components delayed equally => no waveform distortion.

```python
from scipy import signal

def group_delay(b, a, freqs, fs):
    _, h = signal.freqz(b, a, worN=freqs, fs=fs)
    phase = np.unwrap(np.angle(h))
    return -np.diff(phase) / np.diff(freqs) * (fs / (2 * np.pi))
```

### 2.4 Step Response Rise Time

Feed the filter a unit step input and measure:
- **Rise time**: samples from 10% to 90% of final value.
- **Settling time**: samples to stay within ±2% of final value.

```python
def step_response_metrics(b, a, n_samples=500):
    step = np.ones(n_samples)
    response = signal.lfilter(b, a, step)
    final = response[-1]
    # 10% to 90% rise time
    above_10 = np.where(response >= 0.1 * final)[0]
    above_90 = np.where(response >= 0.9 * final)[0]
    rise_time = above_90[0] - above_10[0] if len(above_10) and len(above_90) else np.nan
    # settling time within 2%
    settled = np.where(np.abs(response - final) > 0.02 * final)[0]
    settling_time = settled[-1] if len(settled) else 0
    return rise_time, settling_time
```

### 2.5 Zero-Crossing Detection

For signals that cross zero (e.g., oscillators, AC power):

\[
\tau_{\text{zc}} = \text{median}(z_{\text{out},i} - z_{\text{in},i})
\]

where \(z_{\text{in},i}\) and \(z_{\text{out},i}\) are the \(i\)-th zero-crossing indices of input and output.

```python
def zero_crossings(x):
    return np.where(np.diff(np.sign(x)))[0]

def zero_crossing_lag(signal_in, signal_out):
    zc_in = zero_crossings(signal_in)
    zc_out = zero_crossings(signal_out)
    min_len = min(len(zc_in), len(zc_out))
    return np.median(zc_out[:min_len] - zc_in[:min_len])
```

### 2.6 Lag Metric Summary

| Metric               | Unit     | Stationary Required? | Interpretation              |
|----------------------|----------|---------------------|-----------------------------|
| Cross-correlation    | samples  | Yes                 | Bulk delay estimate         |
| Phase delay          | samples  | No                  | Per-frequency delay         |
| Group delay          | samples  | No                  | Envelope delay              |
| Step rise time       | samples  | No                  | Transient response speed    |
| Zero-crossing lag    | samples  | No                  | Causal, easy to compute     |

---

## 3. Smoothness Metrics

Smoothness quantifies how much the filter suppresses high-frequency variation. A "too smooth" filter overfits; "not smooth enough" underfits.

### 3.1 Roughness Penalty (Second Difference)

\[
R = \sum_{i=2}^{n-1} \left(\hat{y}_{i-1} - 2\hat{y}_i + \hat{y}_{i+1}\right)^2
\]

- **Range**: \([0, \infty)\); lower means smoother.
- **Interpretation**: Sum of squared discrete second derivatives. Zero means perfectly linear output.
- **Use case**: Comparing smoothness between filters of different orders.

```python
def roughness(y):
    d2 = np.diff(y, n=2)
    return np.sum(d2 ** 2)
```

### 3.2 Residual Autocorrelation (Lag-1)

\[
r_1 = \frac{\sum_{i=1}^{n-1} (e_i - \bar{e})(e_{i+1} - \bar{e})}{\sum_{i=1}^{n} (e_i - \bar{e})^2}, \quad e_i = y_i - \hat{y}_i
\]

- **Range**: \([-1, 1]\).
- **Interpretation**: Positive values indicate the filter missed low-frequency structure (colored residuals). Values near zero suggest uncorrelated residuals (ideal filter).
- **Caveat**: A filter that simply memorizes the data would have zero residual autocorrelation but terrible generalization.

```python
def residual_autocorr(y_true, y_pred):
    resid = y_true - y_pred
    return np.corrcoef(resid[:-1], resid[1:])[0, 1]
```

### 3.3 Effective Degrees of Freedom (EDoF)

For linear filters (the output is a linear combination of inputs), the EDoF equals the trace of the hat matrix:

\[
\text{EDoF} = \text{Tr}(H), \quad \hat{y} = H y
\]

- **Interpretation**: EDoF ranges from 1 (perfectly smooth, single-parameter model) to \(n\) (no smoothing at all).
- **Use case**: Comparing filters on a common scale. A moving average of window \(w\) has \(\text{EDoF} \approx n/w\).
- **General filters**: For nonlinear filters, use Monte Carlo estimation: inject white noise and compare input/output variance pointwise.

```python
def effective_dof_linear(H):
    """H is the n x n influence (hat) matrix."""
    return np.trace(H)

def effective_dof_monte_carlo(filter_func, n_points=1000, n_trials=200):
    """For nonlinear filters: inject noise, measure variance ratio."""
    noise = np.random.randn(n_trials, n_points)
    in_var = np.var(noise, axis=0)
    out_var = np.var(np.array([filter_func(n) for n in noise]), axis=0)
    return np.sum(out_var / in_var)
```

### 3.4 Variance Reduction Ratio

\[
\text{VR} = 1 - \frac{\text{Var}(y - \hat{y})}{\text{Var}(y)}
\]

- **Range**: \((-\infty, 1]\); higher means more smoothing.
- **Interpretation**: Fraction of input variance removed. 0.9 means 90% of variance was suppressed.
- **Caveat**: Removes *all* variance, including signal. Must be paired with accuracy metrics.

```python
def variance_reduction(y_true, y_pred):
    return 1 - np.var(y_true - y_pred) / np.var(y_true)
```

---

## 4. Computational Performance

### 4.1 Time Complexity

| Filter Type        | Per-Point Cost | Total (n points) | Notes                          |
|--------------------|---------------|-------------------|--------------------------------|
| Simple MA / EMA    | \(O(1)\)      | \(O(n)\)         | Recursive update               |
| FIR (fixed order)  | \(O(M)\)      | \(O(nM)\)        | M = number of taps             |
| IIR                | \(O(1)\)      | \(O(n)\)         | State-dependent, must be sequential |
| SG (batch)         | \(O(1)\)      | \(O(nM)\)        | For window size M              |
| Kalman (full)      | \(O(k^3)\)    | \(O(nk^3)\)       | k = state dimension            |
| Wavelet denoising  | \(O(n)\)      | \(O(n)\)         | Requires full signal           |

### 4.2 Wall-Clock Benchmarking

```python
import time

def benchmark_filter(filter_func, data, n_trials=100, n_warmup=10):
    # Warm-up (JIT compilation, cache warm)
    for _ in range(n_warmup):
        filter_func(data.copy())

    timings = []
    for _ in range(n_trials):
        start = time.perf_counter()
        filter_func(data.copy())
        end = time.perf_counter()
        timings.append(end - start)

    timings = np.sort(timings)
    return {
        'mean': np.mean(timings),
        'median': np.median(timings),
        'p5': timings[int(0.05 * n_trials)],
        'p95': timings[int(0.95 * n_trials)],
        'min': timings[0],
        'max': timings[-1],
        'std': np.std(timings),
    }
```

**Key protocol rules:**
1. Always copy data per trial to avoid cache effects.
2. Use `time.perf_counter()` (not `time.time()`) for sub-millisecond precision.
3. Report percentiles (p5, p50, p95), not just mean — GC pauses or OS scheduling create heavy tails.
4. Warm-up runs (10-50 iterations) to stabilize JIT/CPU caches.

### 4.3 Memory Footprint

| Filter Type   | Additional Memory            | Streaming? |
|---------------|------------------------------|------------|
| MA (window w) | \(O(w)\)                     | Yes        |
| EMA           | \(O(1)\) (2 state vars)      | Yes        |
| FIR (M taps)  | \(O(M)\)                     | Yes        |
| IIR (order p) | \(O(p)\)                     | Yes        |
| Savitzky-Golay| \(O(w)\)                     | Yes        |
| Kalman        | \(O(k^2)\) for covariance    | Yes        |
| Wavelet       | \(O(n)\) full signal         | No         |
| FFT-based     | \(O(n)\) full signal         | No         |

### 4.4 Causal vs. Non-Causal

- **Causal filter**: Output depends only on current and past inputs. Required for real-time (streaming) use.
- **Non-causal (acausal) filter**: Output depends on future inputs. Requires look-ahead (latency = window/2 for symmetric filters).
- **Zero-phase filtering**: Apply forward then backward (`scipy.signal.filtfilt`). Group delay = 0 at all frequencies, but unusable in real-time.

```python
# Causal: scipy.signal.lfilter(b, a, x)
# Non-causal / zero-phase: scipy.signal.filtfilt(b, a, x)
```

---

## 5. Robustness Metrics

### 5.1 Sensitivity to Parameter Misspecification

Sweep the primary parameter of each filter and measure accuracy degradation. Report the "sensitivity slope":

\[
S_p = \left|\frac{\partial \text{MSE}}{\partial p}\right|_{p_0} \approx \frac{|\text{MSE}(p_0 + \Delta) - \text{MSE}(p_0 - \Delta)|}{2\Delta}
\]

```python
def param_sensitivity(filter_factory, param_name, param_values, y_true, y_raw):
    errors = []
    for val in param_values:
        filt = filter_factory(**{param_name: val})
        y_pred = filt(y_raw)
        errors.append(mean_squared_error(y_true, y_pred))
    # Normalized sensitivity at midpoint
    mid = len(param_values) // 2
    slope = (errors[mid + 1] - errors[mid - 1]) / (param_values[mid + 1] - param_values[mid - 1])
    return slope / errors[mid]  # relative sensitivity
```

### 5.2 Breakdown Point (Outlier Robustness)

The maximum fraction of arbitrarily bad outliers a filter can tolerate before producing arbitrarily bad output.

| Filter      | Breakdown Point | Notes                                 |
|-------------|----------------|---------------------------------------|
| MA / FIR    | 0%             | Single outlier corrupts window/2 outputs |
| EMA         | 0%             | Permanent memory of outlier           |
| Median      | 50%            | Maximum possible                      |
| Huber       | ~50%           | Depends on tuning constant            |
| Kalman      | 0%             | Gaussian assumption violated          |
| Wavelet     | ~0-10%         | Depends on threshold rule             |

### 5.3 Noise Sweep Stability

```python
def noise_sweep(filter_func, y_true, noise_levels, n_trials=50):
    results = {'noise_level': [], 'mean_mse': [], 'mse_std': []}
    for sigma in noise_levels:
        mses = []
        for _ in range(n_trials):
            noise = np.random.randn(len(y_true)) * sigma
            y_pred = filter_func(y_true + noise)
            mses.append(mean_squared_error(y_true, y_pred))
        results['noise_level'].append(sigma)
        results['mean_mse'].append(np.mean(mses))
        results['mse_std'].append(np.std(mses))
    return results
```

The ideal filter has MSE scaling linearly with noise variance. A filter that degrades super-linearly has structural fragility.

### 5.4 Edge Effect Magnitude

Filter boundary artifacts are measured by error at the start and end of the signal:

\[
E_{\text{edge}} = \frac{1}{2k}\sum_{i=1}^{k} \left(|y_i - \hat{y}_i| + |y_{n-i+1} - \hat{y}_{n-i+1}|\right)
\]

where \(k\) is the edge region (typically 5-10% of signal length). Compare against interior error:

\[
E_{\text{interior}} = \frac{1}{n - 2k}\sum_{i=k+1}^{n-k} |y_i - \hat{y}_i|
\]

The edge-to-interior ratio \(E_{\text{edge}} / E_{\text{interior}}\) quantifies boundary handling quality. Values > 2 indicate severe edge artifacts.

```python
def edge_error_ratio(y_true, y_pred, edge_frac=0.05):
    n = len(y_true)
    k = max(1, int(n * edge_frac))
    edge_err = np.mean(np.abs(y_true[:k] - y_pred[:k]))
    edge_err += np.mean(np.abs(y_true[-k:] - y_pred[-k:]))
    edge_err /= 2
    interior_err = np.mean(np.abs(y_true[k:-k] - y_pred[k:-k]))
    return edge_err / interior_err if interior_err > 0 else np.inf
```

---

## 6. Test Signal Suite Design

A robust benchmark requires multiple signal archetypes, each testing a different aspect of filter behavior.

### 6.1 Sinusoidal + AWGN

\[
y(t) = A \sin(2\pi f t) + \mathcal{N}(0, \sigma^2)
\]

- **Purpose**: Controlled frequency content. Test attenuation at known frequency.
- **Sweep**: Vary SNR from 0 dB to 30 dB.

```python
def make_sinusoid(n=1000, freq=0.05, fs=1.0, snr_db=10.0):
    t = np.arange(n) / fs
    signal = np.sin(2 * np.pi * freq * t)
    noise_power = np.var(signal) / (10 ** (snr_db / 10))
    noise = np.random.randn(n) * np.sqrt(noise_power)
    return signal, signal + noise
```

### 6.2 Step / Edge Signal

\[
y(t) = A \cdot \mathbb{1}(t > t_0)
\]

- **Purpose**: Test transient response, overshoot, ringing, rise time.
- **Variants**: Single step at midpoint; multiple steps; ramp (gradual transition).

```python
def make_step(n=1000, step_pos=0.5, rise_samples=0):
    t = np.arange(n)
    step = np.zeros(n)
    step[int(step_pos * n):] = 1.0
    if rise_samples > 0:
        step = np.convolve(step, np.ones(rise_samples) / rise_samples, mode='same')
    return step, step  # y_true = y_clean
```

### 6.3 Trend + Seasonality + Noise (Realistic Time-Series)

\[
y(t) = \underbrace{\alpha t}_{\text{trend}} + \underbrace{\beta \sin(2\pi t / T)}_{\text{seasonal}} + \underbrace{\gamma \cdot \text{AR}(1)}_{\text{autocorrelated noise}}
\]

- **Purpose**: Mimic real-world data (financial, sensor, environmental).
- **Challenge**: AR(1) noise tests whether the filter can distinguish signal from colored noise.

```python
def make_trend_seasonal(n=1000, trend=0.001, seasonal_amp=1.0, period=100, ar_coeff=0.7):
    t = np.arange(n)
    signal = trend * t + seasonal_amp * np.sin(2 * np.pi * t / period)
    ar_noise = np.zeros(n)
    white = np.random.randn(n) * 0.5
    for i in range(1, n):
        ar_noise[i] = ar_coeff * ar_noise[i-1] + white[i]
    return signal, signal + ar_noise
```

### 6.4 Spike / Impulse Signal

\[
y(t) = \text{base}(t) + \sum_j A_j \cdot \delta(t - t_j)
\]

- **Purpose**: Test outlier robustness and impulse response. Inject 1-5 spikes at random positions with amplitude 5-20 sigma.
- **Metric**: How far does one spike propagate in the output?

```python
def make_impulse(n=1000, n_spikes=3, spike_amplitude=10.0):
    base = np.random.randn(n) * 0.5  # low background noise
    signal = base.copy()  # true signal has no spikes
    positions = np.random.choice(n, n_spikes, replace=False)
    noisy = base.copy()
    noisy[positions] += spike_amplitude
    return signal, noisy
```

### 6.5 Chirp Signal (Frequency Sweep)

\[
y(t) = \sin\left(2\pi (f_0 t + \frac{f_1 - f_0}{2T} t^2)\right) + \mathcal{N}(0, \sigma^2)
\]

- **Purpose**: Characterize full frequency response in a single test. The filter's output reveals attenuation and phase shift as a function of frequency.
- **Visualization**: Plot spectrogram of input and output side by side.

```python
def make_chirp(n=1000, f0=0.001, f1=0.2, fs=1.0):
    t = np.arange(n) / fs
    instantaneous_f = f0 + (f1 - f0) * t / (n / fs)
    signal = np.sin(2 * np.pi * (f0 * t + (f1 - f0) / (2 * (n / fs)) * t ** 2))
    noise = np.random.randn(n) * 0.2
    return signal, signal + noise
```

---

## 7. Comparison Framework

### 7.1 Radar / Spider Chart Design

Each axis represents a normalized metric. Normalization is critical for meaningful comparison:

**Normalization strategies** (choose one per axis):
1. **Min-max**: \(x' = (x - x_{\min}) / (x_{\max} - x_{\min})\); sensitive to extreme values.
2. **Z-score**: \(x' = (x - \mu) / \sigma\); standardizes against the population of filters.
3. **Reference-based**: \(x' = x / x_{\text{reference}}\); intuitive (ratio to baseline filter).

**Recommended axis layout** (6 axes in order):

| Axis             | Metric                          | Direction | Normalization        |
|------------------|---------------------------------|-----------|----------------------|
| Accuracy         | MSE (inverse: 1 - MSE/MSE_max)  | Higher=better | Min-max        |
| Lag              | 1 - (lag / max_lag)             | Higher=better | Min-max        |
| Smoothness       | 1 - (roughness / max_roughness) | Higher=better | Min-max        |
| Speed            | 1 - (time / max_time)           | Higher=better | Min-max        |
| Robustness       | 1 - (edge_ratio / max_ratio)    | Higher=better | Min-max        |
| Memory           | 1 - (mem / max_mem)             | Higher=better | Min-max        |

```python
def radar_scores(filter_results, metrics):
    """filter_results: dict[filter_name, dict[metric_name, value]]"""
    names = list(filter_results.keys())
    scores = {name: [] for name in names}
    for metric in metrics:
        values = np.array([filter_results[n][metric] for n in names])
        # Min-max for 'higher is better'; invert if needed
        if metric in ('accuracy', 'speed', 'robustness'):
            normalized = (values - values.min()) / (values.max() - values.min() + 1e-10)
        else:
            normalized = 1 - (values - values.min()) / (values.max() - values.min() + 1e-10)
        for i, name in enumerate(names):
            scores[name].append(normalized[i])
    return scores
```

### 7.2 Weighted Scoring per Application Domain

Assign weights \(w_1, \dots, w_6\) summing to 1:

\[
\text{Total Score}_k = \sum_{j=1}^{6} w_j \cdot s_{k,j}
\]

| Domain            | Accuracy | Lag  | Smooth | Speed | Robust | Mem  |
|-------------------|----------|------|--------|-------|--------|------|
| Financial trading | 0.20     | 0.35 | 0.10   | 0.15  | 0.10   | 0.10 |
| Biomedical        | 0.35     | 0.05 | 0.25   | 0.05  | 0.20   | 0.10 |
| Audio processing  | 0.25     | 0.10 | 0.15   | 0.20  | 0.10   | 0.20 |
| Industrial control| 0.15     | 0.30 | 0.10   | 0.20  | 0.20   | 0.05 |
| General purpose   | 0.25     | 0.15 | 0.20   | 0.15  | 0.15   | 0.10 |

### 7.3 Rank Aggregation

When no single metric dominates, aggregate rankings across all metrics to find the best overall filter.

**Borda Count**: Each filter gets (N - rank) points per metric. Sum across metrics. Highest total wins.

\[
\text{Borda}_k = \sum_{j=1}^{m} (N - r_{k,j})
\]

**Copeland's Method**: Pairwise comparison. Filter \(a\) gets +1 for each metric where it beats filter \(b\), -1 for each loss. Sum across all opponents. Highest total wins.

```python
def borda_count(scores_dict):
    """scores_dict: dict[metric_name, list of (filter_name, score)]"""
    n_filters = len(next(iter(scores_dict.values())))
    borda = {name: 0 for name, _ in scores_dict[next(iter(scores_dict))]}
    for metric, ranked in scores_dict.items():
        ranked_sorted = sorted(ranked, key=lambda x: x[1], reverse=True)
        for rank, (name, _) in enumerate(ranked_sorted):
            borda[name] += (n_filters - rank)
    return sorted(borda.items(), key=lambda x: x[1], reverse=True)

def copeland_method(metric_results):
    """metric_results: dict[filter_name, dict[metric_name, value]]"""
    names = list(metric_results.keys())
    metrics = list(metric_results[names[0]].keys())
    copeland = {name: 0 for name in names}
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            if i >= j:
                continue
            wins_a = sum(metric_results[a][m] > metric_results[b][m] for m in metrics)
            wins_b = len(metrics) - wins_a
            if wins_a > wins_b:
                copeland[a] += 1
            elif wins_b > wins_a:
                copeland[b] += 1
    return sorted(copeland.items(), key=lambda x: x[1], reverse=True)
```

---

## 8. Statistical Rigor

### 8.1 Multiple Noise Realizations

Single-trial comparisons are unreliable. For each test signal and noise level:

\[
\text{MSE}_{\text{reported}} = \frac{1}{R}\sum_{r=1}^{R} \text{MSE}_r
\]

where \(R \geq 30\) for reasonable confidence intervals.

```python
def multi_trial_compare(filters, y_true, signal_fn, n_trials=50, noise_params=None):
    """Run R trials for each filter and return mean + CI."""
    results = {name: [] for name in filters}
    for _ in range(n_trials):
        _, y_noisy = signal_fn(**(noise_params or {}))
        for name, filt in filters.items():
            y_pred = filt(y_noisy.copy())
            results[name].append(mean_squared_error(y_true, y_pred))
    summary = {}
    for name, vals in results.items():
        arr = np.array(vals)
        summary[name] = {
            'mean': np.mean(arr),
            'ci_low': np.percentile(arr, 2.5),
            'ci_high': np.percentile(arr, 97.5),
            'std': np.std(arr)
        }
    return summary
```

### 8.2 Confidence Intervals on Metrics

For any metric \(\theta\), report:

\[
\hat{\theta} \pm z_{\alpha/2} \cdot \frac{\sigma_\theta}{\sqrt{R}}
\]

where \(R\) is the number of trials and \(\sigma_\theta\) is the sample standard deviation. Use bootstrap for metrics without closed-form variance (e.g., group delay at a specific frequency):

```python
def bootstrap_ci(filter_func, y_true, y_noisy, metric_func, n_bootstrap=1000, alpha=0.05):
    """Bootstrap confidence interval for any metric."""
    n = len(y_true)
    metrics = []
    for _ in range(n_bootstrap):
        idx = np.random.choice(n, n, replace=True)
        y_pred = filter_func(y_noisy[idx])  # bootstrap resample
        metrics.append(metric_func(y_true[idx], y_pred))
    metrics = np.sort(metrics)
    lower = metrics[int(n_bootstrap * alpha / 2)]
    upper = metrics[int(n_bootstrap * (1 - alpha / 2))]
    return lower, upper, np.mean(metrics)
```

### 8.3 Cross-Validation for Parameter Tuning

**Time-series cross-validation** (not standard k-fold, which leaks future into past):

```python
from sklearn.model_selection import TimeSeriesSplit

def tune_filter_cv(filter_factory, param_grid, y, n_splits=5):
    tscv = TimeSeriesSplit(n_splits=n_splits)
    best_param = None
    best_score = np.inf

    for param in param_grid:
        scores = []
        for train_idx, val_idx in tscv.split(y):
            # For filters: fit on train (estimate parameters), evaluate on val
            filt = filter_factory(**param)
            y_train, y_val = y[train_idx], y[val_idx]
            # Apply filter to val using state from end of train
            # Simplified: apply to full signal, measure only on val
            y_pred_full = filt(y.copy())
            scores.append(mean_squared_error(y_val, y_pred_full[val_idx]))
        avg_score = np.mean(scores)
        if avg_score < best_score:
            best_score = avg_score
            best_param = param

    return best_param, best_score
```

**Important**: For time-series filtering, the train/val split must respect temporal order. Never shuffle. For causal filters, the filter is applied sequentially, so the validation set inherently uses information from the training set via the filter state -- this is correct behavior (the filter learned from history to predict the present).

### 8.4 Statistical Significance Testing

When comparing two filters \(A\) and \(B\) across \(R\) trials:

**Paired t-test** (on metric differences \(d_i = \text{MSE}_{A,i} - \text{MSE}_{B,i}\)):

\[
t = \frac{\bar{d}}{\sigma_d / \sqrt{R}}, \quad H_0: \bar{d} = 0
\]

**Wilcoxon signed-rank test**: Non-parametric alternative, preferred when distributions are not normal (common with MSE).

```python
from scipy import stats

def compare_filters(scores_a, scores_b):
    """scores_a, scores_b: arrays of metric values across trials."""
    t_stat, t_pval = stats.ttest_rel(scores_a, scores_b)
    w_stat, w_pval = stats.wilcoxon(scores_a, scores_b)
    return {
        't_test': {'statistic': t_stat, 'p_value': t_pval},
        'wilcoxon': {'statistic': w_stat, 'p_value': w_pval},
        'mean_diff': np.mean(scores_a - scores_b),
        'effect_size_cohens_d': (np.mean(scores_a) - np.mean(scores_b)) / np.std(np.concatenate([scores_a, scores_b]))
    }
```

**Minimum detectable effect size** (for planning how many trials are needed):

\[
R \geq \left(\frac{z_{\alpha/2} + z_\beta}{\delta}\right)^2 \sigma^2
\]

where \(\delta\) is the minimum meaningful difference and \(\sigma^2\) is the expected variance.

---

## Quick Reference: Recommended Benchmark Protocol

1. **Generate** 5 signal archetypes (sinusoid, step, trend-seasonal, impulse, chirp) at 3 SNR levels each (0, 10, 20 dB).
2. **For each condition**, run \(R = 50\) trials with independent noise realizations.
3. **Compute** all metrics (MSE, MAE, lag, roughness, edge ratio, runtime) per trial.
4. **Aggregate** per filter: mean and 95% CI per metric per condition.
5. **Normalize** metrics across filters within each condition.
6. **Rank** using Borda count or Copeland's method.
7. **Report**: radar chart, score table, statistical significance matrix.

This yields a defensible, reproducible, and interpretable filter comparison that separates signal-processing quality from implementation artifacts.
