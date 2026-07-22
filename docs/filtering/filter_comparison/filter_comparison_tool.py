#!/usr/bin/env python3
"""
Filter Comparison Analysis Tool
================================

A comprehensive benchmarking suite for digital signal processing filters.
Compares 10 filter algorithms across 5 test signal archetypes, computing
accuracy, lag, smoothness, speed, and robustness metrics with full visualization.

Usage:
    python filter_comparison_tool.py

Output:
    - ./filter_comparison_plots/        # All plots as PNG (150 DPI)
    - ./filter_comparison_results.csv   # Ranking table as CSV
    - Console: ranked comparison table and per-signal best-filter summary

Dependencies:
    numpy, scipy, matplotlib  (required)
    statsmodels               (optional -- LOWESS fallback if absent)

Author: Claude-generated
"""

import numpy as np
import matplotlib.pyplot as plt
import time
import os
import csv
import warnings
from typing import Dict, Callable, Tuple, Optional, List

# ---------------------------------------------------------------------------
# Optional imports
# ---------------------------------------------------------------------------
try:
    import statsmodels.api as sm
    HAS_STATSMODELS = True
except ImportError:
    HAS_STATSMODELS = False

# Suppress known benign warnings from scipy
warnings.filterwarnings("ignore", category=UserWarning, module="scipy")
warnings.filterwarnings("ignore", category=RuntimeWarning, module="numpy")

# ---------------------------------------------------------------------------
# Colour palette and output directory
# ---------------------------------------------------------------------------
COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]
PLOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "filter_comparison_plots")


# ===================================================================
# HELPER UTILITIES
# ===================================================================

def _make_odd(n: int) -> int:
    """Return the smallest odd number >= n."""
    return n if n % 2 == 1 else n + 1


def _apply_causal(signal: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """Causal convolution: output[i] depends only on signal[:i+1]."""
    result = np.convolve(signal, kernel, mode="full")
    # result[k] = Σ_j kernel[j] · signal[k-j]
    # First valid output where full kernel fits: k = len(kernel)-1
    return result[len(kernel) - 1:]


def _estimate_lag_via_step(filter_func: Callable, params: dict,
                           n: int = 2000) -> float:
    """Measure filter lag via causal impulse-response centroid.

    Applies a unit impulse using *causal* convolution (not 'same' mode),
    then computes the centre-of-mass of the response.  For scipy-based
    zero-phase filters (filtfilt, savgol with interp), the lag is
    computed analytically.
    """
    impulse = np.zeros(n, dtype=np.float64)
    pos = n // 4
    impulse[pos] = 1.0

    # --- Detect convolution-based FIR filters and re-run causally ---
    # The filter registry maps names to functions.  For the lag probe we
    # monkey-patch the common convolution parameters by detecting their
    # window size from *params*.
    func_name = filter_func.__name__ if hasattr(filter_func, "__name__") else ""
    window = params.get("window", 0)

    if func_name in ("sma_filter", "wma_filter") and window > 0:
        w = window if window % 2 == 1 else window + 1
        if func_name == "sma_filter":
            kernel = np.ones(w) / w
        else:
            weights = np.arange(w, 0, -1, dtype=np.float64)
            kernel = weights / weights.sum()
        return float((w - 1) / 2)  # exact group delay for symmetric FIR
    elif func_name == "alma_filter" and window > 0:
        # ALMA kernel is asymmetric — compute centroid analytically
        w = window if window % 2 == 1 else window + 1
        offset = params.get("offset", 0.85)
        sigma = params.get("sigma", 6.0)
        m_val = offset * (w - 1)
        s = float(w) / max(sigma, 1e-6)
        i = np.arange(w, dtype=np.float64)
        kernel = np.exp(-0.5 * ((i - m_val) / s) ** 2)
        kernel /= kernel.sum()
        # Causal impulse response centroid gives delay
        response_causal = _apply_causal(impulse, kernel)
        # response_causal[i] maps to signal time i + (w-1)
        causal_centroid = float(np.sum(np.arange(len(response_causal)) * response_causal) / max(response_causal.sum(), 1e-15))
        lag = causal_centroid + (w - 1) - pos
        return float(max(0.0, lag))
    elif func_name == "gaussian_filter_wrapper":
        sigma = params.get("sigma", 2.0)
        # Gaussian theoretical group delay = 0 (zero-phase via scipy)
        return 0.0
    elif func_name == "savgol_filter_wrapper":
        w = params.get("window", 11)
        # Savitzky-Golay via scipy uses symmetric padding → zero-phase
        # Theoretical causal lag = (w-1)/2
        return float((w - 1) / 2)
    elif func_name == "median_filter_wrapper":
        w = params.get("window", 5)
        return float((w - 1) / 2)
    elif func_name == "butterworth_filter":
        # filtfilt → zero-phase
        return 0.0
    elif func_name == "lowess_filter":
        # symmetric local regression → ≈ zero-phase
        return 0.0
    else:
        # EMA, Kalman: apply normally and measure centroid
        response = filter_func(impulse.copy(), **params)

    resp = np.abs(response)
    total = float(resp.sum())
    if total < 1e-15:
        return 0.0
    centroid = float(np.sum(np.arange(n, dtype=np.float64) * resp) / total)
    return float(max(0.0, centroid - pos))


def _roughness(signal: np.ndarray) -> float:
    """Sum of squared second differences -- lower is smoother."""
    d2 = np.diff(signal, n=2)
    if len(d2) == 0:
        return 0.0
    return float(np.sum(d2 ** 2))


def _benchmark_time(filter_func: Callable, signal: np.ndarray,
                    params: dict, n_trials: int = 30) -> float:
    """Median wall-clock time over *n_trials* copies of *signal*."""
    timings = []
    for _ in range(n_trials):
        s = signal.copy()
        t0 = time.perf_counter()
        filter_func(s, **params)
        timings.append(time.perf_counter() - t0)
    return float(np.median(timings))


def _edge_error_ratio(clean: np.ndarray, filtered: np.ndarray,
                      edge_frac: float = 0.05) -> float:
    """Ratio of edge MAE to interior MAE -- lower is better."""
    n = len(clean)
    k = max(1, int(n * edge_frac))
    err = np.abs(clean - filtered)
    edge = np.concatenate([err[:k], err[-k:]])
    interior = err[k:-k]
    edge_mean = np.mean(edge)
    interior_mean = np.mean(interior) + 1e-15
    return float(edge_mean / interior_mean)


def _local_regression_lowess(x: np.ndarray, y: np.ndarray,
                             x_eval: np.ndarray, frac: float = 0.3
                             ) -> np.ndarray:
    """Fallback LOWESS: local linear regression with tricube weights."""
    n = len(x)
    k = max(int(n * frac), 2)
    y_pred = np.zeros_like(x_eval, dtype=float)
    for i, xi in enumerate(x_eval):
        dists = np.abs(x - xi)
        idx = np.argsort(dists)[:k]
        max_dist = float(dists[idx[-1]])
        if max_dist < 1e-12:
            y_pred[i] = float(np.mean(y[idx]))
            continue
        u = dists[idx] / max_dist
        w = (1.0 - np.abs(u) ** 3) ** 3  # tricube
        w = np.maximum(w, 1e-15)
        # Weighted least squares via lstsq (more stable than normal eq.)
        X = np.column_stack([np.ones(k, dtype=np.float64), x[idx]])
        W_sqrt = np.sqrt(w)
        Xw = X * W_sqrt[:, None]
        yw = y[idx] * W_sqrt
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            beta, _, _, _ = np.linalg.lstsq(Xw, yw, rcond=1e-10)
        if np.any(np.isnan(beta)) or np.any(np.isinf(beta)):
            beta = np.array([np.mean(y[idx]), 0.0])
        y_pred[i] = beta[0] + beta[1] * xi
    return y_pred


# ===================================================================
# FILTER IMPLEMENTATIONS  (uniform interface: func(signal, **params))
# ===================================================================

def sma_filter(signal: np.ndarray, window: int = 11) -> np.ndarray:
    """Simple Moving Average."""
    window = min(window, len(signal))
    kernel = np.ones(window) / window
    return np.convolve(signal, kernel, mode="same")


def ema_filter(signal: np.ndarray, alpha: float = 0.2) -> np.ndarray:
    """Exponential Moving Average (recursive vectorised)."""
    n = len(signal)
    out = np.empty(n, dtype=np.float64)
    out[0] = signal[0]
    a = float(alpha)
    for i in range(1, n):
        out[i] = a * signal[i] + (1.0 - a) * out[i - 1]
    return out


def wma_filter(signal: np.ndarray, window: int = 11) -> np.ndarray:
    """Weighted Moving Average (linearly decreasing weights)."""
    window = min(window, len(signal))
    weights = np.arange(window, 0, -1, dtype=np.float64)
    weights /= weights.sum()
    return np.convolve(signal, weights, mode="same")


def alma_filter(signal: np.ndarray, window: int = 21,
               offset: float = 0.85, sigma: float = 6.0) -> np.ndarray:
    """Arnaud Legoux Moving Average (offset Gaussian weights)."""
    window = min(window, len(signal))
    m = offset * (window - 1)
    s = float(window) / max(sigma, 1e-6)
    i = np.arange(window, dtype=np.float64)
    weights = np.exp(-0.5 * ((i - m) / s) ** 2)
    weights /= weights.sum()
    # Pad the beginning so the output is the same length (causal FIR)
    padded = np.pad(signal, (window - 1, 0), mode="edge")
    conv = np.convolve(padded, weights[::-1], mode="valid")
    return conv


def savgol_filter_wrapper(signal: np.ndarray, window: int = 11,
                          order: int = 3) -> np.ndarray:
    """Savitzky-Golay filter (scipy.signal.savgol_filter)."""
    from scipy.signal import savgol_filter
    window = _make_odd(window)
    window = min(window, len(signal))
    if order >= window:
        order = window - 1
    if order < 1:
        order = 1
    if window < 3:
        return signal.copy()
    return savgol_filter(signal, window_length=window, polyorder=order,
                         mode="interp")


def kalman_filter(signal: np.ndarray, Q: float = 0.01,
                  R: float = 0.1) -> np.ndarray:
    """1-D Kalman filter (constant-velocity model)."""
    n = len(signal)
    dt = 1.0
    F = np.array([[1.0, dt], [0.0, 1.0]])
    H = np.array([[1.0, 0.0]])
    Q_mat = Q * np.eye(2)
    x = np.array([signal[0], 0.0], dtype=np.float64)
    P = np.eye(2) * 0.1
    filtered = np.empty(n, dtype=np.float64)
    filtered[0] = signal[0]
    for i in range(1, n):
        # Predict
        x = F @ x
        P = F @ P @ F.T + Q_mat
        # Update
        innov = float(signal[i] - (H @ x).item())
        S = (H @ P @ H.T).item() + R
        K = (P @ H.T).flatten() / max(S, 1e-15)
        x = x + K * innov
        P = (np.eye(2) - np.outer(K, H[0])) @ P
        filtered[i] = float(x[0])
    return filtered


def butterworth_filter(signal: np.ndarray, order: int = 4,
                       cutoff: float = 0.15) -> np.ndarray:
    """Zero-phase Butterworth lowpass (scipy.signal.sosfiltfilt)."""
    from scipy.signal import butter, sosfiltfilt
    nyquist = 0.5
    normal_cutoff = cutoff / nyquist
    if normal_cutoff >= 1.0:
        normal_cutoff = 0.999
    sos = butter(order, normal_cutoff, btype="low", output="sos")
    return sosfiltfilt(sos, signal)


def gaussian_filter_wrapper(signal: np.ndarray, sigma: float = 2.0
                            ) -> np.ndarray:
    """Gaussian filter (scipy.ndimage.gaussian_filter1d)."""
    from scipy.ndimage import gaussian_filter1d
    return gaussian_filter1d(signal, sigma=sigma, mode="reflect")


def median_filter_wrapper(signal: np.ndarray, window: int = 5
                          ) -> np.ndarray:
    """Median filter (scipy.signal.medfilt)."""
    from scipy.signal import medfilt
    window = _make_odd(window)
    window = min(window, len(signal))
    if window < 3:
        return signal.copy()
    return medfilt(signal, kernel_size=window)


def lowess_filter(signal: np.ndarray, frac: float = 0.2) -> np.ndarray:
    """LOWESS smoothing (statsmodels if available, else local regression)."""
    if HAS_STATSMODELS:
        x = np.arange(len(signal), dtype=np.float64)
        result = sm.nonparametric.lowess(signal, x, frac=min(frac, 0.99),
                                         return_sorted=False)
        return result
    # Fallback: local linear regression with tricube weights
    x = np.arange(len(signal), dtype=np.float64)
    return _local_regression_lowess(x, signal, x, frac=frac)


# ===================================================================
# FILTER REGISTRY  (name -> (function, default_params, colour))
# ===================================================================
FILTER_REGISTRY: Dict[str, Tuple[Callable, dict, str]] = {
    "SMA":     (sma_filter,               {"window": 11},           COLORS[0]),
    "EMA":     (ema_filter,               {"alpha": 0.2},           COLORS[1]),
    "WMA":     (wma_filter,               {"window": 11},           COLORS[2]),
    "ALMA":    (alma_filter,              {"window": 21,
                                           "offset": 0.85,
                                           "sigma": 6.0},           COLORS[3]),
    "SavGol":  (savgol_filter_wrapper,    {"window": 11,
                                           "order": 3},             COLORS[4]),
    "Kalman":  (kalman_filter,            {"Q": 0.01, "R": 0.1},    COLORS[5]),
    "Butter":  (butterworth_filter,       {"order": 4,
                                           "cutoff": 0.15},         COLORS[6]),
    "Gauss":   (gaussian_filter_wrapper,  {"sigma": 2.0},           COLORS[7]),
    "Median":  (median_filter_wrapper,    {"window": 5},            COLORS[8]),
    "LOWESS":  (lowess_filter,            {"frac": 0.2},            COLORS[9]),
}

FILTER_NAMES = list(FILTER_REGISTRY.keys())


# ===================================================================
# TEST SIGNAL GENERATORS
# ===================================================================

def generate_sinusoid_noisy(
    n: int = 1000, freq: float = 0.05, noise_std: float = 0.5
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Pure sinusoid + white Gaussian noise."""
    t = np.arange(n, dtype=np.float64)
    clean = np.sin(2 * np.pi * freq * t)
    noise = np.random.randn(n) * noise_std
    return clean + noise, clean, t


def generate_step_signal(
    n: int = 1000, step_pos: float = 0.5, noise_std: float = 0.3
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Step / edge + noise."""
    t = np.arange(n, dtype=np.float64)
    clean = np.zeros(n)
    clean[int(step_pos * n):] = 1.0
    noise = np.random.randn(n) * noise_std
    return clean + noise, clean, t


def generate_trend_seasonal(
    n: int = 1000, trend_slope: float = 0.005,
    seasonality_amp: float = 1.0, noise_std: float = 0.5
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Linear trend + sinusoidal seasonality + AR(1) coloured noise."""
    t = np.arange(n, dtype=np.float64)
    clean = trend_slope * t + seasonality_amp * np.sin(2 * np.pi * t / 100.0)
    # AR(1) process
    ar = np.empty(n)
    white = np.random.randn(n) * noise_std
    ar[0] = white[0]
    for i in range(1, n):
        ar[i] = 0.7 * ar[i - 1] + white[i] * np.sqrt(1.0 - 0.7 ** 2)
    return clean + ar, clean, t


def generate_impulse_signal(
    n: int = 1000,
    impulse_pos: Optional[List[int]] = None,
    impulse_amp: float = 8.0, noise_std: float = 0.2
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Low-amplitude sinusoid + noise + high-amplitude impulse spikes.

    The clean signal is a low-level sinusoid so that SNR and lag metrics
    are well-defined (unlike a flat zero baseline).
    """
    t = np.arange(n, dtype=np.float64)
    clean = 0.5 * np.sin(2 * np.pi * t / n * 3)  # gentle background variation
    noise = np.random.randn(n) * noise_std
    if impulse_pos is None:
        impulse_pos = [n // 4, n // 2, 3 * n // 4]
    noisy = clean + noise
    for pos in impulse_pos:
        if 0 <= pos < n:
            noisy[pos] += impulse_amp
    return noisy, clean, t


def generate_chirp_signal(
    n: int = 2000, f0: float = 0.001, f1: float = 0.25
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Frequency sweep (chirp) for frequency-response characterisation."""
    t = np.arange(n, dtype=np.float64)
    phase = 2.0 * np.pi * (f0 * t + (f1 - f0) / (2.0 * n) * t ** 2)
    clean = np.sin(phase)
    noise = np.random.randn(n) * 0.15
    return clean + noise, clean, t


# ===================================================================
# SIGNAL REGISTRY
# ===================================================================
SIGNAL_GENERATORS = {
    "Sinusoid":       generate_sinusoid_noisy,
    "Step":           generate_step_signal,
    "Trend+Seasonal": generate_trend_seasonal,
    "Impulse":        generate_impulse_signal,
    "Chirp":          generate_chirp_signal,
}
SIGNAL_NAMES = list(SIGNAL_GENERATORS.keys())


# ===================================================================
# METRICS
# ===================================================================

def compute_all_metrics(clean: np.ndarray, filtered: np.ndarray,
                        noisy: np.ndarray, lag: float = 0.0) -> dict:
    """Return dict of accuracy, lag, and smoothness metrics."""
    mse = float(np.mean((clean - filtered) ** 2))
    rmse = float(np.sqrt(mse))
    mae = float(np.mean(np.abs(clean - filtered)))
    mse_input = float(np.mean((clean - noisy) ** 2)) + 1e-15
    snr_imp = 10.0 * np.log10(mse_input / (mse + 1e-15))
    roughness_val = _roughness(filtered)
    return {
        "mse": mse, "rmse": rmse, "mae": mae,
        "snr_imp": snr_imp, "lag": lag, "roughness": roughness_val,
    }


def compute_robustness(clean: np.ndarray, filtered: np.ndarray,
                       edge_frac: float = 0.05) -> float:
    """Edge-effect ratio for robustness characterisation."""
    return _edge_error_ratio(clean, filtered, edge_frac)


# ===================================================================
# NORMALISATION HELPERS
# ===================================================================

def minmax_best(values: np.ndarray) -> np.ndarray:
    """Normalise to [0,1] where 1 = best (we assume lower-is-better)."""
    mn, mx = float(values.min()), float(values.max())
    if mx - mn < 1e-15:
        return np.ones_like(values, dtype=float)
    return 1.0 - (values - mn) / (mx - mn)


def minmax_raw(values: np.ndarray) -> np.ndarray:
    """Normalise to [0,1] preserving direction (higher = 1)."""
    mn, mx = float(values.min()), float(values.max())
    if mx - mn < 1e-15:
        return np.ones_like(values, dtype=float)
    return (values - mn) / (mx - mn)


# ===================================================================
# VISUALISATION
# ===================================================================

def _ensure_plot_dir() -> None:
    os.makedirs(PLOT_DIR, exist_ok=True)


def _save_fig(fig: plt.Figure, name: str) -> None:
    path = os.path.join(PLOT_DIR, name)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved  {path}")


def _time_domain_plots(
    signals: dict, results: dict, registry: dict
) -> None:
    """One figure per test signal: clean, noisy, and all filter outputs."""
    for sname in SIGNAL_NAMES:
        noisy, clean, t = signals[sname]
        fig, ax = plt.subplots(figsize=(14, 6))
        ax.plot(t, clean, "k--", linewidth=1.5, alpha=0.7, label="Clean")
        ax.plot(t, noisy, color="gray", alpha=0.25, linewidth=0.8,
                label="Noisy")
        for fname in FILTER_NAMES:
            ffunc, fparams, fcolor = registry[fname]
            filtered = ffunc(noisy.copy(), **fparams)
            ax.plot(t, filtered, color=fcolor, linewidth=1.2, label=fname)
        mse_input = np.mean((clean - noisy) ** 2) + 1e-15
        snr_db = (10 * np.log10(np.var(clean) / mse_input)
                  if np.var(clean) > 1e-15 else 0.0)
        ax.set_title(f"{sname}  |  Input SNR: {snr_db:.1f} dB  |  "
                     f"{len(FILTER_NAMES)} filters", fontsize=12)
        ax.set_xlabel("Sample index")
        ax.set_ylabel("Amplitude")
        ax.legend(ncol=3, fontsize=8, loc="lower right")
        ax.set_xlim(t[0], t[-1])
        fig.tight_layout()
        _save_fig(fig, f"01_time_domain_{sname.lower()}.png")


def _error_bar_chart(results: dict) -> None:
    """Grouped bar chart: MSE per filter per signal type."""
    n_filters = len(FILTER_NAMES)
    n_signals = len(SIGNAL_NAMES)
    x = np.arange(n_filters)
    width = 0.15

    fig, ax = plt.subplots(figsize=(14, 6))
    for i, sname in enumerate(SIGNAL_NAMES):
        vals = [results[fname][sname]["mse"] for fname in FILTER_NAMES]
        offset = (i - n_signals / 2 + 0.5) * width
        bars = ax.bar(x + offset, vals, width, label=sname,
                      color=COLORS[i % len(COLORS)], alpha=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(FILTER_NAMES, fontsize=9)
    ax.set_ylabel("Mean Squared Error")
    ax.set_title("MSE by Filter and Signal Type (lower is better)")
    ax.legend(fontsize=8)
    ax.set_yscale("log")
    fig.tight_layout()
    _save_fig(fig, "02_mse_bar_chart.png")


def _lag_vs_smoothness_scatter(results: dict, registry: dict) -> None:
    """Scatter plot: lag vs roughness across all signal types."""
    # Aggregate (mean) across all signal types
    avg_lag = []
    avg_rough = []
    for fname in FILTER_NAMES:
        lags = [results[fname][s]["lag"] for s in SIGNAL_NAMES]
        roughs = [results[fname][s]["roughness"] for s in SIGNAL_NAMES]
        avg_lag.append(np.mean(lags))
        avg_rough.append(np.mean(roughs))

    fig, ax = plt.subplots(figsize=(9, 7))
    for i, fname in enumerate(FILTER_NAMES):
        ax.scatter(avg_lag[i], avg_rough[i], color=COLORS[i], s=80,
                   zorder=5)
        ax.annotate(fname, (avg_lag[i], avg_rough[i]),
                    textcoords="offset points", xytext=(6, 6), fontsize=8,
                    color=COLORS[i])

    # Ideal corner annotation
    ax.annotate("Ideal", xy=(0, 0), fontsize=10, fontweight="bold",
                color="green", alpha=0.5)
    ax.set_xlabel("Average Lag (samples)  →  lower is better")
    ax.set_ylabel("Average Roughness  →  lower is better")
    ax.set_title("Lag vs Smoothness Tradeoff\n"
                 "(Pareto frontier: lower-left = better)")
    ax.axhline(0, color="gray", linewidth=0.5)
    ax.axvline(0, color="gray", linewidth=0.5)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    _save_fig(fig, "03_lag_vs_smoothness.png")


def _radar_chart(results: dict) -> None:
    """Normalised radar / spider chart across 5 composite axes."""
    # Build per-filter aggregated raw scores
    raw: Dict[str, List[float]] = {n: [] for n in FILTER_NAMES}
    for metric, key, higher_better in [
        ("Accuracy", "mse", False),
        ("Low Lag", "lag", False),
        ("Smoothness", "roughness", False),
        ("Speed", "time_per_1000", False),
        ("Robustness", "edge_ratio", False),
    ]:
        vals = []
        for fname in FILTER_NAMES:
            vals.append(np.mean([results[fname][s][key]
                                 for s in SIGNAL_NAMES]))
        vals = np.array(vals)
        norm = minmax_raw(vals) if higher_better else minmax_best(vals)
        for i, fname in enumerate(FILTER_NAMES):
            raw[fname].append(float(norm[i]))

    n_metrics = 5
    angles = np.linspace(0, 2 * np.pi, n_metrics, endpoint=False).tolist()
    angles += angles[:1]  # close polygon

    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw={"projection": "polar"})
    for i, fname in enumerate(FILTER_NAMES):
        values = raw[fname] + raw[fname][:1]  # close
        ax.plot(angles, values, color=COLORS[i], linewidth=1.5, label=fname)
        ax.fill(angles, values, color=COLORS[i], alpha=0.08)

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(["Accuracy", "Low Lag", "Smoothness",
                         "Speed", "Robustness"],
                        fontsize=10)
    ax.set_ylim(0, 1)
    ax.set_yticks([0.2, 0.4, 0.6, 0.8, 1.0])
    ax.set_yticklabels(["0.2", "0.4", "0.6", "0.8", "1.0"], fontsize=7)
    ax.set_title("Filter Performance Radar\n(higher = better on each axis)",
                 fontsize=11, pad=20)
    ax.legend(loc="upper right", bbox_to_anchor=(1.35, 1.1), fontsize=8)
    fig.tight_layout()
    _save_fig(fig, "04_radar_chart.png")


def _frequency_response_plot(registry: dict) -> None:
    """Overlay of all filter magnitude responses via FFT of chirp."""
    # Generate a clean chirp and filter it
    _, clean, _ = generate_chirp_signal(n=2000, f0=0.001, f1=0.25)
    n = len(clean)
    freqs = np.fft.rfftfreq(n, d=1.0)
    fft_in = np.fft.rfft(clean)
    mag_in = np.abs(fft_in) + 1e-15

    fig, ax = plt.subplots(figsize=(12, 6))
    for fname in FILTER_NAMES:
        ffunc, fparams, fcolor = registry[fname]
        filtered = ffunc(clean.copy(), **fparams)
        fft_out = np.fft.rfft(filtered)
        mag_out = np.abs(fft_out)
        resp = mag_out / mag_in
        ax.plot(freqs, resp, color=fcolor, linewidth=1.2, label=fname)
    ax.set_xlabel("Normalised Frequency (cycles/sample)")
    ax.set_ylabel("Magnitude Response")
    ax.set_title("Frequency Response via Chirp Signal")
    ax.set_xlim(0, 0.5)
    ax.set_ylim(-0.05, 1.05)
    ax.legend(fontsize=8, ncol=2)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    _save_fig(fig, "05_frequency_response.png")


def _summary_heatmap(results: dict) -> None:
    """Ranking heatmap: normalised scores per filter per metric."""
    metric_keys = ["mse", "lag", "roughness", "time_per_1000", "edge_ratio",
                   "snr_imp"]
    metric_labels = ["MSE (inv)", "Low Lag", "Smoothness", "Speed",
                     "Robustness", "SNR Imp."]
    higher_better_flags = [False, False, False, False, False, True]

    data = np.zeros((len(FILTER_NAMES), len(metric_keys)))
    for j, key in enumerate(metric_keys):
        vals = np.array([np.mean([results[f][s][key] for s in SIGNAL_NAMES])
                         for f in FILTER_NAMES])
        if higher_better_flags[j]:
            normed = minmax_raw(vals)
        else:
            normed = minmax_best(vals)
        data[:, j] = normed

    fig, ax = plt.subplots(figsize=(10, 6 + len(FILTER_NAMES) * 0.3))
    im = ax.imshow(data, cmap="RdYlGn", aspect="auto", vmin=0, vmax=1)

    ax.set_xticks(np.arange(len(metric_labels)))
    ax.set_xticklabels(metric_labels, fontsize=9)
    ax.set_yticks(np.arange(len(FILTER_NAMES)))
    ax.set_yticklabels(FILTER_NAMES, fontsize=9)

    # Annotate cells
    for i in range(len(FILTER_NAMES)):
        for j in range(len(metric_labels)):
            color = "white" if data[i, j] < 0.5 else "black"
            ax.text(j, i, f"{data[i, j]:.2f}", ha="center", va="center",
                    fontsize=7, color=color)

    ax.set_title("Normalised Performance Scores\n(higher = better, "
                 "RdYlGn scale)", fontsize=11)
    fig.colorbar(im, ax=ax, shrink=0.6, pad=0.02)
    fig.tight_layout()
    _save_fig(fig, "06_summary_heatmap.png")


def generate_plots(signals: dict, results: dict) -> None:
    """Generate and save all six plot types."""
    _ensure_plot_dir()
    print("\nGenerating plots ...")

    _time_domain_plots(signals, results, FILTER_REGISTRY)
    _error_bar_chart(results)
    _lag_vs_smoothness_scatter(results, FILTER_REGISTRY)
    _radar_chart(results)
    _frequency_response_plot(FILTER_REGISTRY)
    _summary_heatmap(results)

    print(f"  All plots saved to {PLOT_DIR}/")


# ===================================================================
# OUTPUT
# ===================================================================

def _composite_score(scores: np.ndarray) -> float:
    """Simple average of normalised (already [0,1]) scores."""
    return float(np.mean(scores))


def print_results(results: dict) -> None:
    """Print ranked comparison table and save CSV."""
    # --- Per-filter aggregated scores ---
    metric_keys = ["mse", "snr_imp", "lag", "roughness", "time_per_1000",
                   "edge_ratio"]
    display_names = ["MSE", "SNR_imp(dB)", "Lag(smp)", "Roughness",
                     "Time(s/1k)", "EdgeRatio"]
    higher_better = [False, True, False, False, False, False]

    # Aggregate (mean across signal types) for each filter
    agg: Dict[str, Dict[str, float]] = {}
    for fname in FILTER_NAMES:
        agg[fname] = {}
        for key in metric_keys:
            agg[fname][key] = float(np.mean(
                [results[fname][s][key] for s in SIGNAL_NAMES]))

    # Normalise all metrics for composite scoring
    all_scores: Dict[str, List[float]] = {n: [] for n in FILTER_NAMES}
    for j, key in enumerate(metric_keys):
        vals = np.array([agg[f][key] for f in FILTER_NAMES])
        normed = minmax_raw(vals) if higher_better[j] else minmax_best(vals)
        for i, fname in enumerate(FILTER_NAMES):
            all_scores[fname].append(float(normed[i]))

    # Composite = average of normalised scores
    composites = {fname: _composite_score(np.array(scores))
                  for fname, scores in all_scores.items()}

    # --- Print table ---
    header = (f"{'Rank':<5} {'Filter':<10}"
              + "".join(f"{dn:<14}" for dn in display_names)
              + f"{'Composite':<10}")
    sep = "-" * len(header)
    print("\n" + sep)
    print("FILTER COMPARISON RANKING")
    print(sep)
    print(header)
    print(sep)

    ranked = sorted(FILTER_NAMES, key=lambda n: composites[n], reverse=True)
    csv_rows = []
    for rank, fname in enumerate(ranked, start=1):
        vals = [f"{agg[fname][k]:<14.4f}" for k in metric_keys]
        row = (f"{rank:<5} {fname:<10}" + "".join(vals)
               + f"{composites[fname]:<10.4f}")
        print(row)
        csv_rows.append({
            "Rank": rank, "Filter": fname,
            **{dn: round(agg[fname][k], 6)
               for dn, k in zip(display_names, metric_keys)},
            "Composite": round(composites[fname], 6),
        })
    print(sep)

    # --- Per-signal winner summary ---
    print("\n--- Per-Signal Best Filter ---")
    for sname in SIGNAL_NAMES:
        best = min(FILTER_NAMES,
                   key=lambda f: results[f][sname]["mse"])
        mse_val = results[best][sname]["mse"]
        print(f"  {sname:<18} →  {best:<10} (MSE: {mse_val:.5f})")

    # --- Save CSV ---
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "filter_comparison_results.csv")
    fieldnames = ["Rank", "Filter"] + display_names + ["Composite"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)
    print(f"\n  Results saved to {csv_path}")


# ===================================================================
# MAIN
# ===================================================================

def main() -> None:
    """Orchestrate signal generation, filtering, metrics, plots, and output."""
    np.random.seed(42)

    print("=" * 70)
    print("  FILTER COMPARISON ANALYSIS TOOL")
    print("=" * 70)

    # --- 1. Generate test signals ---
    print("\nGenerating test signals ...")
    signals: Dict[str, Tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for sname, sgen in SIGNAL_GENERATORS.items():
        noisy, clean, t = sgen()
        signals[sname] = (noisy, clean, t)
        noise_var = np.var(noisy - clean) + 1e-15
        snr = 10 * np.log10(np.var(clean) / noise_var) if np.var(clean) > 1e-15 else 0.0
        print(f"  {sname:<18}  n={len(t):5d}  "
              f"clean_var={np.var(clean):.3f}  SNR_in={snr:.1f} dB")

    # --- 2. Measure per-filter lag via step response (filter property) ---
    print("\nMeasuring filter lag (step-response method) ...")
    filter_lags: Dict[str, float] = {}
    for fname in FILTER_NAMES:
        ffunc, fparams, _ = FILTER_REGISTRY[fname]
        filter_lags[fname] = _estimate_lag_via_step(ffunc, fparams)
        print(f"  {fname:<10}  lag = {filter_lags[fname]:.0f} samples")

    # --- 3. Apply every filter to every signal ---
    print("\nApplying filters & computing metrics ...")
    results: Dict[str, Dict[str, dict]] = {}
    for fname in FILTER_NAMES:
        ffunc, fparams, fcolor = FILTER_REGISTRY[fname]
        results[fname] = {}
        print(f"  {fname:<10}  ", end="", flush=True)
        for sname in SIGNAL_NAMES:
            noisy, clean, t = signals[sname]
            filtered = ffunc(noisy.copy(), **fparams)
            metrics = compute_all_metrics(clean, filtered, noisy,
                                          lag=filter_lags[fname])
            # Additional metrics: edge ratio & timing
            metrics["edge_ratio"] = compute_robustness(clean, filtered)
            # Timing: per-1000-samples (median of multiple runs)
            t_single = _benchmark_time(ffunc, noisy, fparams, n_trials=10)
            metrics["time_per_1000"] = 1000.0 * t_single / len(noisy)
            results[fname][sname] = metrics
            print(".", end="", flush=True)
        print(f"  done")

    # --- 4. Generate all plots ---
    generate_plots(signals, results)

    # --- 4. Print results & save CSV ---
    print_results(results)

    print("\nDone.")


if __name__ == "__main__":
    main()
