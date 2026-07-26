"""
Performance regression benchmarks for filter critical paths.

Uses pytest-benchmark (benchmark fixture).
Run: python -m pytest tests/test_benchmark.py --benchmark-only
"""

import numpy as np
import pandas as pd
import pytest
from engine.filters import (
    apply_sma, apply_ema, apply_wma, apply_alma,
    apply_savgol, apply_kalman, apply_butterworth,
    apply_gaussian, apply_median, apply_lowess,
)
from engine.alignment import compute_metrics
from engine.schmitt import _schmitt_trigger, _find_all_pairs, _fit_parabolic, _fit_physics_parabola

# ---------------------------------------------------------------------------
# Test data (shared across benchmarks)
# ---------------------------------------------------------------------------

# Simulate realistic price data: ~2000 bars (approx 2 years of daily data)
np.random.seed(42)
N_BARS = 2000
_t = np.arange(N_BARS, dtype=np.float64)
_PRICE_LONG = np.cumsum(np.random.randn(N_BARS) * 2.0) + 200.0
_PRICE_SHORT = np.sin(np.linspace(0, 20 * np.pi, N_BARS)) * 50 + 100


# ============================================================================
# Filter computation benchmarks
# ============================================================================

FILTER_BENCH_SPECS = [
    ("sma", apply_sma, {"window": 21}),
    ("ema", apply_ema, {"span": 10}),
    ("wma", apply_wma, {"window": 21}),
    ("alma", apply_alma, {"window": 21, "offset": 0.85, "sigma": 6.0}),
    ("savgol", apply_savgol, {"window": 21, "order": 2}),
    ("kalman", apply_kalman, {"Q": 0.01, "R": 1.0}),
    ("butterworth", apply_butterworth, {"order": 4, "cutoff": 10.0}),
    ("gaussian", apply_gaussian, {"sigma": 3.0}),
    ("median", apply_median, {"window": 15}),
    ("lowess", apply_lowess, {"frac": 0.1}),
]


class TestFilterPerformance:
    """Benchmark each filter on 2000-bar data."""

    @pytest.mark.benchmark
    @pytest.mark.filter
    @pytest.mark.parametrize("name,func,kwargs", FILTER_BENCH_SPECS)
    def test_filter_2000_bars(self, name, func, kwargs, benchmark):
        """Benchmark a single filter call on 2000 data points."""
        kwargs_copy = dict(kwargs)
        benchmark(func, _PRICE_LONG, _t, **kwargs_copy)


# ============================================================================
# Schmitt trigger benchmark
# ============================================================================

class TestSchmittTriggerPerformance:
    """Benchmark Schmitt trigger computation (critical path for strategy)."""

    @pytest.mark.benchmark
    @pytest.mark.signal
    def test_schmitt_trigger_2000_bars(self, benchmark):
        """Benchmark Schmitt trigger on 2000 bars of v/a data."""
        v = np.random.RandomState(42).randn(N_BARS) * 5.0
        a = np.random.RandomState(43).randn(N_BARS) * 2.0
        benchmark(_schmitt_trigger, v, a, ewma_span=60, k_eps=0.15, sigma_min=0.05)

    @pytest.mark.benchmark
    @pytest.mark.signal
    def test_find_all_pairs_2000_bars(self, benchmark):
        """Benchmark pair finding on Schmitt trigger output."""
        # Generate typical sig_t: mostly 0, some +1/-1 segments
        sig_t = np.zeros(N_BARS, dtype=int)
        # Add alternating long/short segments
        for start in range(0, N_BARS - 50, 60):
            sig_t[start:start + 25] = 1
            sig_t[start + 30:start + 55] = -1
        benchmark(_find_all_pairs, sig_t)


# ============================================================================
# Metrics computation benchmark
# ============================================================================

class TestMetricsPerformance:
    """Benchmark compute_metrics on typical data sizes."""

    @pytest.mark.benchmark
    @pytest.mark.filter
    def test_compute_metrics_2000_bars(self, benchmark):
        """Benchmark metrics computation on 2000 data points."""
        np.random.seed(42)
        clean = _PRICE_LONG
        noisy = clean + np.random.randn(N_BARS) * 5.0
        filtered = apply_sma(noisy, _t, window=21)
        benchmark(compute_metrics, clean, noisy, filtered)


# ============================================================================
# Parabolic fit benchmark
# ============================================================================

class TestFitPerformance:
    """Benchmark parabolic fitting (used in strategy prediction)."""

    @pytest.mark.benchmark
    @pytest.mark.strategy
    def test_fit_parabolic_100_bars(self, benchmark):
        """Benchmark quadratic polynomial fit on 100-bar segment."""
        seg_len = 100
        x = np.arange(seg_len, dtype=np.float64)
        y = np.random.RandomState(42).randn(seg_len) * 10 + 100
        benchmark(_fit_parabolic, x, y, 0, seg_len - 1)

    @pytest.mark.benchmark
    @pytest.mark.strategy
    def test_fit_physics_parabola_100_bars(self, benchmark):
        """Benchmark physics parabola fit on 100-bar segment."""
        seg_len = 100
        x = np.arange(seg_len, dtype=np.float64)
        y = np.random.RandomState(42).randn(seg_len) * 10 + 100
        benchmark(_fit_physics_parabola, x, y, 0, seg_len - 1)


# ============================================================================
# Batch / data loading benchmarks
# ============================================================================

class TestBatchProcessing:
    """Benchmark batch operations on multiple filter calls."""

    @pytest.mark.benchmark
    @pytest.mark.filter
    def test_batch_filter_10x(self, benchmark):
        """Benchmark running 10 different filter configs in sequence."""
        configs = [
            (apply_sma, {"window": 11}),
            (apply_sma, {"window": 51}),
            (apply_ema, {"span": 10}),
            (apply_ema, {"span": 50}),
            (apply_wma, {"window": 21}),
            (apply_alma, {"window": 21, "offset": 0.85, "sigma": 6.0}),
            (apply_savgol, {"window": 21, "order": 2}),
            (apply_gaussian, {"sigma": 3.0}),
            (apply_gaussian, {"sigma": 10.0}),
            (apply_median, {"window": 15}),
        ]

        def batch_run():
            for func, kwargs in configs:
                func(_PRICE_LONG, _t, **kwargs)

        benchmark(batch_run)

    @pytest.mark.benchmark
    @pytest.mark.filter
    def test_compute_metrics_batch_50x(self, benchmark):
        """Benchmark 50 sequential metrics computations."""
        np.random.seed(42)
        clean = _PRICE_LONG

        def batch_metrics():
            for i in range(50):
                noisy = clean + np.random.randn(N_BARS) * 5.0
                filtered = apply_sma(noisy, _t, window=21)
                compute_metrics(clean, noisy, filtered)

        benchmark(batch_metrics)

    @pytest.mark.benchmark
    @pytest.mark.signal
    def test_data_loading_simulated_2000_bars(self, benchmark):
        """Benchmark simulated data generation (proxy for data loading path)."""
        def load_simulate():
            np.random.seed(42)
            bars = 2000
            noise = np.random.randn(bars) * 50
            trend = np.linspace(0, 200, bars)
            signal = trend + noise
            return signal

        result = benchmark(load_simulate)
        assert len(result) == 2000
