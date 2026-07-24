"""
Property-based tests (hypothesis) for filter engine mathematical invariants.

Tests for filter_app/services/filter_engine.py core filter functions.
"""

import numpy as np
import pytest
from hypothesis import given, settings, strategies as st
from engine.filters import (
    apply_sma, apply_ema, apply_wma, apply_alma,
    apply_savgol, apply_kalman, apply_butterworth,
    apply_gaussian, apply_median, apply_lowess,
    compute_metrics,
)

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
