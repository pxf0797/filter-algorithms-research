"""
Property-based tests (hypothesis) for filter engine mathematical invariants.

Tests for filter/services/filter_engine.py core filter functions.
"""

import numpy as np
import pytest
from hypothesis import given, settings, strategies as st
from engine.filters import (
    apply_sma, apply_ema, apply_wma, apply_alma,
    apply_savgol, apply_kalman, apply_butterworth,
    apply_gaussian, apply_median, apply_lowess,
)
from engine.alignment import compute_metrics

# ---------------------------------------------------------------------------
# Strategy: generate realistic signal arrays for property testing
# ---------------------------------------------------------------------------


@st.composite
def signal_and_t(draw, min_len: int = 50, max_len: int = 500):
    """Generate a (signal, t) pair with valid numeric values."""
    n = draw(st.integers(min_value=min_len, max_value=max_len))
    signal = draw(
        st.lists(
            st.floats(min_value=-1000.0, max_value=1000.0, allow_nan=False),
            min_size=n, max_size=n,
        ).map(np.array)
    ).astype(np.float64)
    t = np.arange(n, dtype=np.float64)
    return signal, t


@st.composite
def constant_signal_and_t(draw, min_len: int = 50, max_len: int = 500):
    """Generate a constant-valued signal."""
    n = draw(st.integers(min_value=min_len, max_value=max_len))
    value = draw(st.floats(min_value=-1000.0, max_value=1000.0, allow_nan=False))
    signal = np.full(n, value, dtype=np.float64)
    t = np.arange(n, dtype=np.float64)
    return signal, t


# ---------------------------------------------------------------------------
# Property 1: Scaling invariance for linear filters
#   For linear filters: filter(N * signal) ≈ N * filter(signal)
#   within floating-point tolerance.
# ---------------------------------------------------------------------------

LINEAR_FILTER_SPECS = [
    # (name, func, kwargs)
    ("sma", apply_sma, {"window": 11}),
    ("wma", apply_wma, {"window": 11}),
    ("ema", apply_ema, {"span": 10}),
    ("gaussian", apply_gaussian, {"sigma": 3.0}),
    ("butterworth", apply_butterworth, {"order": 4, "cutoff": 10.0}),
]


@pytest.mark.filter
@pytest.mark.parametrize("name,func,kwargs", LINEAR_FILTER_SPECS)
@given(
    data=signal_and_t(min_len=80, max_len=300),
    scale=st.floats(min_value=0.5, max_value=5.0, allow_nan=False),
)
@settings(max_examples=30, deadline=3000)
def test_linear_scaling_invariance(name, func, kwargs, data, scale):
    """Linear filter: filter(N * input) ≈ N * filter(input)."""
    signal, t = data
    kwargs_copy = dict(kwargs)

    # Filter original
    filtered_original = func(signal, t, **kwargs_copy)

    # Filter scaled
    scaled_signal = signal * scale
    filtered_scaled = func(scaled_signal, t, **kwargs_copy)

    # Check: filtered_scaled ≈ scale * filtered_original
    # Skip edges where boundary effects dominate (SMA, WMA, etc.)
    margin = max(kwargs_copy.get("window", 10), kwargs_copy.get("span", 5), 10)
    interior = slice(margin, -margin) if len(signal) > 2 * margin else slice(None)

    expected = scale * filtered_original[interior]
    actual = filtered_scaled[interior]

    # Remove NaN regions (some filters produce NaN at boundaries)
    valid = ~np.isnan(expected) & ~np.isnan(actual)
    if not np.any(valid):
        pytest.skip("All interior values are NaN — boundary-dominated signal")

    assert np.allclose(actual[valid], expected[valid], rtol=1e-4, atol=1e-8), (
        f"{name}: scaling invariance violated. "
        f"max_diff={np.max(np.abs(actual[valid] - expected[valid])):.2e}"
    )


# ---------------------------------------------------------------------------
# Property 2: Constant signal preservation
#   A constant input should produce (approximately) the same constant output.
# ---------------------------------------------------------------------------

ALL_FILTER_SPECS = [
    ("sma", apply_sma, {"window": 21}),
    ("ema", apply_ema, {"span": 15}),
    ("wma", apply_wma, {"window": 21}),
    ("alma", apply_alma, {"window": 21, "offset": 0.85, "sigma": 6.0}),
    ("savgol", apply_savgol, {"window": 21, "order": 2}),
    ("gaussian", apply_gaussian, {"sigma": 5.0}),
    ("median", apply_median, {"window": 15}),
    ("butterworth", apply_butterworth, {"order": 4, "cutoff": 10.0}),
    ("lowess", apply_lowess, {"frac": 0.15}),
]


@pytest.mark.filter
@pytest.mark.parametrize("name,func,kwargs", ALL_FILTER_SPECS)
@given(
    data=constant_signal_and_t(min_len=100, max_len=300),
)
@settings(max_examples=20, deadline=5000)
def test_constant_signal_preservation(name, func, kwargs, data):
    """All filters: constant input → constant output (within tolerance)."""
    signal, t = data
    const_val = float(signal[0])
    kwargs_copy = dict(kwargs)

    filtered = func(signal, t, **kwargs_copy)

    # Skip edges (2*window) where transient effects dominate
    window = kwargs_copy.get("window", kwargs_copy.get("span", 5))
    if name in ("ema",):
        margin = max(window * 3, 30)  # EMA needs longer warmup
    elif name == "kalman":
        margin = max(window * 2, 20)
    elif name == "lowess":
        margin = max(window * 2, 20)
    else:
        margin = max(window * 2, 30)

    interior = filtered[margin:-margin] if len(filtered) > 2 * margin else filtered
    valid = ~np.isnan(interior)

    if not np.any(valid):
        pytest.skip("All interior values are NaN")

    deviations = np.abs(interior[valid] - const_val)
    max_dev = np.max(deviations)

    # Some filters have numeric drift; allow generous tolerance
    tolerance = 1e-10 * max(abs(const_val), 1.0) + 1e-8
    assert max_dev <= tolerance, (
        f"{name}: constant signal not preserved. "
        f"max_deviation={max_dev:.2e}, tolerance={tolerance:.2e}"
    )


# ---------------------------------------------------------------------------
# Property 3: Time-reversal symmetry for symmetric filters
#   For symmetric convolution filters: reverse(input) filtered then reversed
#   should equal filter(input). Applies to SMA (odd window), WMA, Gaussian.
# ---------------------------------------------------------------------------

SYMMETRIC_FILTER_SPECS = [
    ("sma", apply_sma, {"window": 11}),
    ("gaussian", apply_gaussian, {"sigma": 3.0}),
]


@pytest.mark.filter
@pytest.mark.parametrize("name,func,kwargs", SYMMETRIC_FILTER_SPECS)
@given(data=signal_and_t(min_len=100, max_len=300))
@settings(max_examples=30, deadline=3000)
def test_time_reversal_symmetry(name, func, kwargs, data):
    """Symmetric filter: rev(filter(rev(x))) ≈ filter(x)."""
    signal, t = data
    kwargs_copy = dict(kwargs)

    # Forward filter
    forward = func(signal, t, **kwargs_copy)

    # Reverse → filter → reverse
    reversed_signal = signal[::-1]
    rev_filtered = func(reversed_signal, t, **kwargs_copy)
    rev_then_forward = rev_filtered[::-1]

    # Compare interiors (skip boundary artifacts)
    window = kwargs_copy.get("window", 5)
    margin = max(window * 3, 15)
    interior = slice(margin, -margin) if len(signal) > 2 * margin else slice(None)

    fwd_interior = forward[interior]
    rev_interior = rev_then_forward[interior]

    valid = ~np.isnan(fwd_interior) & ~np.isnan(rev_interior)
    if not np.any(valid):
        pytest.skip("All interior values are NaN")

    assert np.allclose(fwd_interior[valid], rev_interior[valid], rtol=1e-3, atol=1e-6), (
        f"{name}: time-reversal symmetry violated. "
        f"max_diff={np.max(np.abs(fwd_interior[valid] - rev_interior[valid])):.2e}"
    )


# ---------------------------------------------------------------------------
# Property 4: compute_metrics — perfect filter yields ideal metrics
# ---------------------------------------------------------------------------

@given(
    n=st.integers(min_value=50, max_value=300),
    noise_scale=st.floats(min_value=0.0, max_value=50.0),
)
@settings(max_examples=30, deadline=2000)
def test_compute_metrics_perfect_filter(n, noise_scale):
    """When filtered == clean, metrics should indicate perfect filtering."""
    t = np.arange(n, dtype=np.float64)
    clean = np.sin(t / 10.0) * 100 + 500
    noisy = clean + np.random.RandomState(42).randn(n) * noise_scale
    filtered = clean.copy()  # perfect filter

    metrics = compute_metrics(clean, noisy, filtered)

    # Perfect filter: MSE/RMSE/MAE ≈ 0
    assert metrics["mse"] < 1e-10, f"MSE should be ≈0, got {metrics['mse']:.2e}"
    assert metrics["rmse"] < 1e-5, f"RMSE should be ≈0, got {metrics['rmse']:.2e}"
    assert metrics["mae"] < 1e-5, f"MAE should be ≈0, got {metrics['mae']:.2e}"

    # SNR improvement should be positive (unless input was already noiseless)
    if noise_scale > 1e-6:
        assert metrics["snr_imp"] > 0, f"SNR improvement should be positive, got {metrics['snr_imp']:.2f}"

    # Lag is between filtered and noisy; with random noise, peak should be near 0
    assert abs(metrics["lag"]) <= 2, (
        f"Lag should be near 0 for perfect filter with random noise, got {metrics['lag']}"
    )


# ---------------------------------------------------------------------------
# Property 5: SMA double application — smoothness monotonicity
#   var(sma(sma(x))) <= var(sma(x))  (double filtering never adds noise)
# ---------------------------------------------------------------------------


@given(data=signal_and_t(min_len=100, max_len=300))
@settings(max_examples=100, deadline=2000)
def test_sma_double_application_smoother(data):
    """Double SMA is at least as smooth as single SMA."""
    signal, t = data
    window = 11
    once = apply_sma(signal, t, window=window)
    twice = apply_sma(once, t, window=window)

    margin = window * 3
    once_int = once[margin:-margin]
    twice_int = twice[margin:-margin]

    valid = ~np.isnan(once_int) & ~np.isnan(twice_int)
    if not np.any(valid) or len(once_int[valid]) < 10:
        pytest.skip("Not enough valid interior values")

    assert np.var(twice_int[valid]) <= np.var(once_int[valid]) * 1.01, (
        f"SMA double application should not increase variance. "
        f"var_once={np.var(once_int[valid]):.4f}, var_twice={np.var(twice_int[valid]):.4f}"
    )


# ---------------------------------------------------------------------------
# Property 6: SMA exactly recovers a linear ramp
#   For a symmetric convolution with normalized uniform weights on a line,
#   the average of neighbors around any point equals the point itself.
# ---------------------------------------------------------------------------


@given(
    n=st.integers(min_value=80, max_value=300),
    start=st.floats(min_value=-500.0, max_value=500.0),
    slope=st.floats(min_value=-50.0, max_value=50.0),
)
@settings(max_examples=100, deadline=2000)
def test_sma_linear_ramp_exact(n, start, slope):
    """SMA exactly recovers a linear ramp signal in the interior."""
    t = np.arange(n, dtype=np.float64)
    signal = start + slope * t
    window = 21

    result = apply_sma(signal, t, window=window)
    margin = window
    interior = slice(margin, -margin)

    if len(signal) <= 2 * margin + 5:
        pytest.skip("Signal too short for interior check")

    assert np.allclose(result[interior], signal[interior], atol=1e-10), (
        f"SMA should exactly recover linear ramp. "
        f"max_diff={np.max(np.abs(result[interior] - signal[interior])):.2e}"
    )


# ---------------------------------------------------------------------------
# Property 7: WMA preserves linearity on ramp (output has near-zero 2nd diff)
#   A linear filter applied to a linear function remains linear.
# ---------------------------------------------------------------------------


@given(
    n=st.integers(min_value=80, max_value=300),
    start=st.floats(min_value=-500.0, max_value=500.0),
    slope=st.floats(min_value=-50.0, max_value=50.0),
)
@settings(max_examples=100, deadline=2000)
def test_wma_ramp_linearity(n, start, slope):
    """WMA applied to linear ramp preserves linearity (near-zero second diff)."""
    t = np.arange(n, dtype=np.float64)
    signal = start + slope * t
    window = 21

    result = apply_wma(signal, t, window=window)
    margin = window
    interior = slice(margin, -margin)

    if len(signal) <= 2 * margin + 5:
        pytest.skip("Signal too short for interior check")

    # Second difference of a linear function should be zero
    second_diff = np.diff(result[interior], n=2)
    valid = ~np.isnan(second_diff)
    if not np.any(valid):
        pytest.skip("All interior values are NaN")

    assert np.allclose(second_diff[valid], 0.0, atol=1e-6), (
        f"WMA should preserve linearity. "
        f"max_2nd_diff={np.max(np.abs(second_diff[valid])):.2e}"
    )


# ---------------------------------------------------------------------------
# Property 7: EMA translation invariance
#   ema(x + c, span) ≈ ema(x, span) + c   (EMA is a linear operator)
# ---------------------------------------------------------------------------


@given(
    data=signal_and_t(min_len=100, max_len=300),
    offset=st.floats(min_value=-500.0, max_value=500.0),
)
@settings(max_examples=100, deadline=2000)
def test_ema_translation_invariance(data, offset):
    """EMA is translation-invariant: ema(x + c) ≈ ema(x) + c."""
    signal, t = data
    span = 15

    filtered = apply_ema(signal, t, span=span)
    shifted_signal = signal + offset
    filtered_shifted = apply_ema(shifted_signal, t, span=span)

    # After warmup period (3 * span), EMA converges
    warmup = max(span * 3, 30)
    if len(signal) <= warmup + 10:
        pytest.skip("Signal too short for warmup")

    interior = slice(warmup, None)
    expected = filtered[interior] + offset
    actual = filtered_shifted[interior]

    valid = ~np.isnan(expected) & ~np.isnan(actual)
    if not np.any(valid):
        pytest.skip("All interior values are NaN")

    assert np.allclose(actual[valid], expected[valid], rtol=1e-4, atol=1e-8), (
        f"EMA translation invariance violated. "
        f"max_diff={np.max(np.abs(actual[valid] - expected[valid])):.2e}"
    )


# ---------------------------------------------------------------------------
# Property 8: Kalman variance reduction
#   var(kalman(x)) < var(x) for noisy signals with reasonable Q/R
# ---------------------------------------------------------------------------


@st.composite
def noisy_sine_signal(draw, min_len=100, max_len=300):
    """Generate a noisy sine wave with known clean signal."""
    n = draw(st.integers(min_value=min_len, max_value=max_len))
    amplitude = draw(st.floats(min_value=10.0, max_value=200.0))
    noise_std = draw(st.floats(min_value=1.0, max_value=20.0))
    t = np.arange(n, dtype=np.float64)
    clean = amplitude * np.sin(t / 20.0)
    rng = np.random.RandomState(draw(st.integers(min_value=1, max_value=9999)))
    noisy = clean + rng.randn(n) * noise_std
    return noisy, t


@given(data=noisy_sine_signal(min_len=120, max_len=300))
@settings(max_examples=80, deadline=3000)
def test_kalman_variance_reduction(data):
    """Kalman filter should reduce variance on noisy signal (reasonable Q/R)."""
    signal, t = data
    Q, R = 0.01, 0.5

    filtered = apply_kalman(signal, t, Q=Q, R=R)

    # Skip initial convergence transient
    skip = 20
    input_trim = signal[skip:]
    output_trim = filtered[skip:]

    valid = ~np.isnan(output_trim)
    if not np.any(valid):
        pytest.skip("All output values are NaN")

    # Kalman with reasonable R should reduce variance
    input_var = np.var(input_trim[valid])
    output_var = np.var(output_trim[valid])
    assert output_var <= input_var * 1.05, (
        f"Kalman should reduce variance. "
        f"input_var={input_var:.4f}, output_var={output_var:.4f}"
    )


# ---------------------------------------------------------------------------
# Property 9: Butterworth output sanity — no NaN, no Inf
# ---------------------------------------------------------------------------


@given(data=signal_and_t(min_len=100, max_len=300))
@settings(max_examples=150, deadline=2000)
def test_butterworth_no_nan_inf(data):
    """Butterworth filter output should contain no NaN or Inf values."""
    signal, t = data
    filtered = apply_butterworth(signal, t, order=4, cutoff=0.2)
    assert not np.any(np.isnan(filtered)), "Butterworth output contains NaN"
    assert not np.any(np.isinf(filtered)), "Butterworth output contains Inf"
    assert len(filtered) == len(signal), "Butterworth output length mismatch"


# ---------------------------------------------------------------------------
# Property 10: Gaussian sigma monotonicity
#   Larger sigma → smaller variance (more smoothing)
# ---------------------------------------------------------------------------


@given(data=noisy_sine_signal(min_len=100, max_len=300))
@settings(max_examples=100, deadline=2000)
def test_gaussian_sigma_smoothness(data):
    """Gaussian: larger sigma produces smoother output (smaller variance)."""
    signal, t = data
    result_small = apply_gaussian(signal, t, sigma=1.0)
    result_large = apply_gaussian(signal, t, sigma=10.0)

    # Interior region (avoid edge effects from gaussian_filter1d)
    margin = 30
    interior = slice(margin, -margin)

    valid = ~np.isnan(result_small[interior]) & ~np.isnan(result_large[interior])
    if not np.any(valid):
        pytest.skip("All interior values are NaN")

    var_small = np.var(result_small[interior][valid])
    var_large = np.var(result_large[interior][valid])

    assert var_large <= var_small * 1.05, (
        f"Larger sigma should produce smoother (smaller variance) output. "
        f"var_sigma1={var_small:.4f}, var_sigma10={var_large:.4f}"
    )


# ---------------------------------------------------------------------------
# Property 11: Savitzky-Golay symmetry preservation
#   Symmetric input → symmetric output (within tolerance)
# ---------------------------------------------------------------------------


@st.composite
def symmetric_signal(draw, min_len=100, max_len=300):
    """Generate a signal that is symmetric about its midpoint."""
    n = draw(st.integers(min_value=min_len, max_value=max_len))
    half = n // 2
    # First half + optional middle element + mirror of first half
    first = draw(
        st.lists(
            st.floats(min_value=-1000.0, max_value=1000.0, allow_nan=False),
            min_size=half, max_size=half,
        ).map(np.array)
    ).astype(np.float64)
    if n % 2 == 0:
        signal = np.concatenate([first, first[::-1]])
    else:
        middle = np.array([draw(st.floats(min_value=-1000.0, max_value=1000.0, allow_nan=False))], dtype=np.float64)
        signal = np.concatenate([first, middle, first[::-1]])
    t = np.arange(n, dtype=np.float64)
    return signal, t


@given(data=symmetric_signal(min_len=100, max_len=300))
@settings(max_examples=80, deadline=2000)
def test_savgol_symmetric_preservation(data):
    """SavGol: symmetric input should produce symmetric output."""
    signal, t = data
    window, order = 21, 3
    filtered = apply_savgol(signal, t, window=window, order=order)

    # Compare first half vs reversed second half around center
    n = len(filtered)
    margin = window
    if n <= 2 * margin + 10:
        pytest.skip("Signal too short for symmetry check")

    interior = filtered[margin:-margin]
    mid = len(interior) // 2
    left = interior[:mid]
    right = interior[mid:][::-1]
    # Trim to same length
    min_len = min(len(left), len(right))
    left = left[:min_len]
    right = right[:min_len]

    valid = ~np.isnan(left) & ~np.isnan(right)
    if not np.any(valid):
        pytest.skip("All values are NaN")

    assert np.allclose(left[valid], right[valid], rtol=1e-3, atol=1e-6), (
        f"SavGol symmetry violated. "
        f"max_diff={np.max(np.abs(left[valid] - right[valid])):.2e}"
    )


# ---------------------------------------------------------------------------
# Property 12: Savitzky-Golay exact polynomial preservation
#   SavGol(order=k) exactly preserves polynomials of degree ≤ k
# ---------------------------------------------------------------------------


@given(
    n=st.integers(min_value=50, max_value=200),
    a=st.floats(min_value=-10.0, max_value=10.0),
    b=st.floats(min_value=-10.0, max_value=10.0),
    c=st.floats(min_value=-10.0, max_value=10.0),
)
@settings(max_examples=100, deadline=2000)
def test_savgol_quadratic_exact(n, a, b, c):
    """SavGol(order=2) exactly preserves quadratic polynomials."""
    t = np.arange(n, dtype=np.float64)
    # Pure quadratic: y = a*x^2 + b*x + c
    signal = a * t**2 + b * t + c
    filtered = apply_savgol(signal, t, window=21, order=2)

    # SavGol(order=2) should exactly reproduce quadratic in interior
    margin = 15
    valid = ~np.isnan(filtered[margin:-margin])
    if not np.any(valid):
        pytest.skip("All interior values are NaN")

    assert np.allclose(filtered[margin:-margin][valid], signal[margin:-margin][valid], atol=1e-6), (
        f"SavGol(order=2) should exactly preserve quadratic. "
        f"max_diff={np.max(np.abs(filtered[margin:-margin][valid] - signal[margin:-margin][valid])):.2e}"
    )


# ---------------------------------------------------------------------------
# Property 13: Median filter step edge preservation
#   Step signal edges should be preserved by median filter
# ---------------------------------------------------------------------------


@given(
    n=st.integers(min_value=30, max_value=100),
    low_val=st.floats(min_value=-100.0, max_value=0.0),
    high_val=st.floats(min_value=1.0, max_value=100.0),
    step_pos=st.floats(min_value=0.4, max_value=0.6),
)
@settings(max_examples=100, deadline=2000)
def test_median_step_edge_preservation(n, low_val, high_val, step_pos):
    """Median filter preserves step edges."""
    t = np.arange(n, dtype=np.float64)
    split = int(n * step_pos)
    signal = np.concatenate([
        np.full(split, low_val),
        np.full(n - split, high_val),
    ]).astype(np.float64)

    filtered = apply_median(signal, t, window=5)

    margin = 5
    # Left plateau: should be close to low_val
    left_check = slice(margin, split - margin)
    if left_check.stop > left_check.start:
        assert np.allclose(filtered[left_check], low_val, atol=1e-8), (
            f"Median: left plateau not preserved. "
            f"expected={low_val}, got={filtered[left_check]}"
        )

    # Right plateau: should be close to high_val
    right_check = slice(split + margin, -margin)
    if right_check.stop > right_check.start:
        assert np.allclose(filtered[right_check], high_val, atol=1e-8), (
            f"Median: right plateau not preserved. "
            f"expected={high_val}, got={filtered[right_check]}"
        )


# ---------------------------------------------------------------------------
# Property 14: Median filter impulse suppression
#   Isolated spike in constant signal → suppressed toward constant
# ---------------------------------------------------------------------------


@given(
    n=st.integers(min_value=50, max_value=150),
    base_val=st.floats(min_value=-500.0, max_value=500.0),
    spike_val=st.floats(min_value=500.0, max_value=5000.0),
)
@settings(max_examples=100, deadline=2000)
def test_median_impulse_suppression(n, base_val, spike_val):
    """Median filter suppresses isolated impulses in constant signal."""
    # Spike must differ meaningfully from base
    if abs(spike_val - base_val) < 1.0:
        pytest.skip("Spike value too close to base value")

    t = np.arange(n, dtype=np.float64)
    signal = np.full(n, base_val, dtype=np.float64)

    # Place a single spike in the middle
    spike_idx = n // 2
    signal[spike_idx] = spike_val

    filtered = apply_median(signal, t, window=5)

    # At the spike position, median(filter_size=5) should pick the median of
    # [base, base, spike, base, base] = base  (since base appears twice or more)
    # With window=5, 4 of 5 values are base_val, so median = base_val
    assert abs(filtered[spike_idx] - base_val) < abs(spike_val - base_val) * 0.1, (
        f"Median should suppress spike. "
        f"base={base_val}, spike={spike_val}, filtered_at_spike={filtered[spike_idx]:.4f}"
    )


# ---------------------------------------------------------------------------
# Property 15: LOWESS output length equals input length
# ---------------------------------------------------------------------------


@given(data=signal_and_t(min_len=50, max_len=200))
@settings(max_examples=80, deadline=5000)
def test_lowess_length_preserved(data):
    """LOWESS output should have same length as input."""
    signal, t = data
    filtered = apply_lowess(signal, t, frac=0.15)
    assert len(filtered) == len(signal), (
        f"LOWESS length mismatch: input={len(signal)}, output={len(filtered)}"
    )


# ---------------------------------------------------------------------------
# Property 16: SMA window monotonicity — larger window → smoother
# ---------------------------------------------------------------------------


@given(data=noisy_sine_signal(min_len=100, max_len=300))
@settings(max_examples=100, deadline=2000)
def test_sma_window_smoothness(data):
    """SMA: larger window produces smoother output (smaller variance)."""
    signal, t = data
    result_small = apply_sma(signal, t, window=5)
    result_large = apply_sma(signal, t, window=51)

    margin = 60
    interior = slice(margin, -margin)

    valid = ~np.isnan(result_small[interior]) & ~np.isnan(result_large[interior])
    if not np.any(valid):
        pytest.skip("All interior values are NaN")

    assert np.var(result_large[interior][valid]) <= np.var(result_small[interior][valid]) * 1.05, (
        f"SMA larger window should be smoother. "
        f"var_w5={np.var(result_small[interior][valid]):.4f}, "
        f"var_w51={np.var(result_large[interior][valid]):.4f}"
    )


# ---------------------------------------------------------------------------
# Property 17: EMA span monotonicity — larger span → smoother
# ---------------------------------------------------------------------------


@given(data=noisy_sine_signal(min_len=100, max_len=300))
@settings(max_examples=100, deadline=2000)
def test_ema_span_smoothness(data):
    """EMA: larger span produces smoother output (smaller variance)."""
    signal, t = data
    span_small, span_large = 5, 50

    result_small = apply_ema(signal, t, span=span_small)
    result_large = apply_ema(signal, t, span=span_large)

    # Skip warmup for both
    warmup = max(span_large * 2, 60)
    if len(signal) <= warmup + 10:
        pytest.skip("Signal too short for warmup")

    interior = slice(warmup, None)
    valid = ~np.isnan(result_small[interior]) & ~np.isnan(result_large[interior])
    if not np.any(valid):
        pytest.skip("All interior values are NaN")

    var_small = np.var(result_small[interior][valid])
    var_large = np.var(result_large[interior][valid])

    assert var_large <= var_small * 1.05, (
        f"EMA larger span should be smoother. "
        f"var_span5={var_small:.6e}, var_span50={var_large:.6e}"
    )


# ---------------------------------------------------------------------------
# Property 18: Gaussian translation invariance
#   gaussian(x + c) ≈ gaussian(x) + c  (Gaussian is a linear filter)
# ---------------------------------------------------------------------------


@given(
    data=signal_and_t(min_len=100, max_len=300),
    offset=st.floats(min_value=-500.0, max_value=500.0),
)
@settings(max_examples=100, deadline=2000)
def test_gaussian_translation_invariance(data, offset):
    """Gaussian filter is translation-invariant: gaussian(x + c) ≈ gaussian(x) + c."""
    signal, t = data
    sigma = 3.0

    filtered = apply_gaussian(signal, t, sigma=sigma)
    shifted_signal = signal + offset
    filtered_shifted = apply_gaussian(shifted_signal, t, sigma=sigma)

    margin = 15
    interior = slice(margin, -margin)

    expected = filtered[interior] + offset
    actual = filtered_shifted[interior]

    valid = ~np.isnan(expected) & ~np.isnan(actual)
    if not np.any(valid):
        pytest.skip("All interior values are NaN")

    assert np.allclose(actual[valid], expected[valid], rtol=1e-4, atol=1e-8), (
        f"Gaussian translation invariance violated. "
        f"max_diff={np.max(np.abs(actual[valid] - expected[valid])):.2e}"
    )


# ---------------------------------------------------------------------------
# Property 19: ALMA offset affects lag
#   ALMA with offset≈0 (center-weighted) has more lag than offset≈1 (right-weighted)
# ---------------------------------------------------------------------------


@given(
    n=st.integers(min_value=100, max_value=300),
    start=st.floats(min_value=-500.0, max_value=500.0),
    slope=st.floats(min_value=0.5, max_value=30.0),
)
@settings(max_examples=80, deadline=2000)
def test_alma_offset_affects_lag(n, start, slope):
    """ALMA: different offsets produce predictably different outputs on ramp."""
    t = np.arange(n, dtype=np.float64)
    signal = start + slope * t

    # offset=0.01: Gaussian center shifted left in kernel → right in signal → recent-weighted → less lag
    result_less_lag = apply_alma(signal, t, window=21, offset=0.01, sigma=6.0)
    # offset=0.99: Gaussian center shifted right in kernel → left in signal → old-weighted → more lag
    result_more_lag = apply_alma(signal, t, window=21, offset=0.99, sigma=6.0)

    margin = 30
    interior = slice(margin, -margin)

    valid = ~np.isnan(result_less_lag[interior]) & ~np.isnan(result_more_lag[interior])
    if not np.any(valid):
        pytest.skip("All interior values are NaN")

    # On increasing ramp, less lag → closer to signal (larger values)
    diff = result_less_lag[interior][valid] - result_more_lag[interior][valid]
    proportion = np.mean(diff > 1e-8)
    assert proportion > 0.7, (
        f"ALMA offsets should produce different outputs on ramp. "
        f"proportion less_lag > more_lag: {proportion:.3f}, mean_diff={np.mean(diff):.4f}"
    )


# ---------------------------------------------------------------------------
# Property 20: Linear filter additivity
#   For linear filters: f(a + b) ≈ f(a) + f(b)
# ---------------------------------------------------------------------------


@st.composite
def two_signals_and_t(draw, min_len=100, max_len=300):
    """Generate two independent signals and t."""
    n = draw(st.integers(min_value=min_len, max_value=max_len))
    s1 = draw(
        st.lists(
            st.floats(min_value=-500.0, max_value=500.0, allow_nan=False),
            min_size=n, max_size=n,
        ).map(np.array)
    ).astype(np.float64)
    s2 = draw(
        st.lists(
            st.floats(min_value=-500.0, max_value=500.0, allow_nan=False),
            min_size=n, max_size=n,
        ).map(np.array)
    ).astype(np.float64)
    t = np.arange(n, dtype=np.float64)
    return s1, s2, t


ADDITIVITY_FILTERS = [
    ("sma", apply_sma, {"window": 11}),
    ("gaussian", apply_gaussian, {"sigma": 3.0}),
]


@pytest.mark.filter
@pytest.mark.parametrize("name,func,kwargs", ADDITIVITY_FILTERS)
@given(data=two_signals_and_t(min_len=100, max_len=250))
@settings(max_examples=60, deadline=2000)
def test_linear_filter_additivity(name, func, kwargs, data):
    """Linear filter: f(a + b) ≈ f(a) + f(b)."""
    s1, s2, t = data
    kwargs_copy = dict(kwargs)

    # Filter of sum
    filtered_sum = func(s1 + s2, t, **kwargs_copy)

    # Sum of filters
    filtered_a = func(s1, t, **kwargs_copy)
    filtered_b = func(s2, t, **kwargs_copy)
    sum_of_filters = filtered_a + filtered_b

    margin = 20
    interior = slice(margin, -margin)

    expected = filtered_sum[interior]
    actual = sum_of_filters[interior]

    valid = ~np.isnan(expected) & ~np.isnan(actual)
    if not np.any(valid):
        pytest.skip("All interior values are NaN")

    assert np.allclose(actual[valid], expected[valid], rtol=1e-3, atol=1e-6), (
        f"{name}: additivity violated. "
        f"max_diff={np.max(np.abs(actual[valid] - expected[valid])):.2e}"
    )
