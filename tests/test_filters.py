"""
Tests for all 10 filter functions and compute_metrics.

Fixtures are shared from tests/conftest.py.
"""

import sys
from unittest.mock import patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Import module under test
# ---------------------------------------------------------------------------
sys.path.insert(0, "streamlit")
# Silence streamlit warnings during import
with patch("streamlit.warning"):
    from streamlit_app import (
        apply_sma, apply_ema, apply_wma, apply_alma,
        apply_savgol, apply_kalman, apply_butterworth,
        apply_gaussian, apply_median, apply_lowess,
        compute_metrics,
    )

pytestmark = pytest.mark.filter


# ===================================================================
# SECTION 1 — Constant signal (all filters)
# ===================================================================

class TestConstantSignal:
    """每个滤波器对常量信号应返回≈常量值."""

    def test_sma_constant(self, constant_signal, time_index):
        t = time_index[:len(constant_signal)]
        result = apply_sma(constant_signal, t, window=11)
        # convolve(mode="same") has edge effects at boundaries;
        # interior should converge to 1.0
        interior = result[10:-10]
        assert np.allclose(interior, 1.0, atol=1e-6)

    def test_ema_constant(self, constant_signal, time_index):
        t = time_index[:len(constant_signal)]
        result = apply_ema(constant_signal, t, span=10)
        assert np.allclose(result, 1.0, atol=1e-6)

    def test_wma_constant(self, constant_signal, time_index):
        t = time_index[:len(constant_signal)]
        result = apply_wma(constant_signal, t, window=11)
        interior = result[10:-10]
        assert np.allclose(interior, 1.0, atol=1e-6)

    def test_alma_constant(self, constant_signal, time_index):
        t = time_index[:len(constant_signal)]
        result = apply_alma(constant_signal, t, window=11, offset=0.85, sigma=6.0)
        interior = result[10:-10]
        assert np.allclose(interior, 1.0, atol=1e-6)

    def test_savgol_constant(self, constant_signal, time_index):
        t = time_index[:len(constant_signal)]
        result = apply_savgol(constant_signal, t, window=11, order=2)
        assert np.allclose(result, 1.0, atol=1e-6)

    def test_kalman_constant(self, constant_signal, time_index):
        t = time_index[:len(constant_signal)]
        result = apply_kalman(constant_signal, t, Q=0.01, R=1.0)
        # Kalman converges to true value over time
        assert np.allclose(result[-20:], 1.0, atol=1e-3)

    def test_butterworth_constant(self, constant_signal, time_index):
        t = time_index[:len(constant_signal)]
        result = apply_butterworth(constant_signal, t, order=4, cutoff=10.0)
        assert np.allclose(result[10:-10], 1.0, atol=1e-6)

    def test_gaussian_constant(self, constant_signal, time_index):
        t = time_index[:len(constant_signal)]
        result = apply_gaussian(constant_signal, t, sigma=3.0)
        assert np.allclose(result, 1.0, atol=1e-6)

    def test_median_constant(self, constant_signal, time_index):
        t = time_index[:len(constant_signal)]
        result = apply_median(constant_signal, t, window=11)
        assert np.allclose(result, 1.0, atol=1e-6)

    def test_lowess_constant(self, constant_signal, time_index):
        t = time_index[:len(constant_signal)]
        result = apply_lowess(constant_signal, t, frac=0.1)
        assert np.allclose(result, 1.0, atol=1e-5)


# ===================================================================
# SECTION 2 — Noise reduction tests
# ===================================================================

def _mse(a, b):
    return float(np.mean((a - b) ** 2))


class TestNoiseReduction:
    """含噪正弦→滤波后MSE应显著小于原始MSE."""

    def test_savgol_denoise(self, noisy_sine, clean_sine, time_index):
        result = apply_savgol(noisy_sine, time_index, window=21, order=3)
        orig_mse = _mse(clean_sine, noisy_sine)
        filt_mse = _mse(clean_sine, result)
        assert filt_mse < orig_mse * 0.5, (
            f"Savgol: orig_mse={orig_mse:.4f}, filt_mse={filt_mse:.4f}"
        )

    def test_gaussian_denoise(self, noisy_sine, clean_sine, time_index):
        """Gaussian滤波应降低MSE（低噪声情况下适度改善）. """
        result = apply_gaussian(noisy_sine, time_index, sigma=2.0)
        orig_mse = _mse(clean_sine, noisy_sine)
        filt_mse = _mse(clean_sine, result)
        # With low-noise sine, Gaussian provides modest improvement
        assert filt_mse < orig_mse, (
            f"Gaussian: orig_mse={orig_mse:.4f}, filt_mse={filt_mse:.4f}"
        )

    def test_kalman_denoise(self, noisy_sine, clean_sine, time_index):
        """Kalman滤波应降低MSE（低噪声Kalman需要精细调参）. """
        result = apply_kalman(noisy_sine, time_index, Q=0.1, R=0.5)
        orig_mse = _mse(clean_sine, noisy_sine)
        filt_mse = _mse(clean_sine, result)
        assert filt_mse < orig_mse, (
            f"Kalman: orig_mse={orig_mse:.4f}, filt_mse={filt_mse:.4f}"
        )

    def test_butterworth_denoise(self, noisy_sine, clean_sine, time_index):
        """Butterworth低通滤波应降低MSE. """
        # cutoff < nyquist (0.5) is needed for actual lowpass effect
        result = apply_butterworth(noisy_sine, time_index, order=4, cutoff=0.3)
        orig_mse = _mse(clean_sine, noisy_sine)
        filt_mse = _mse(clean_sine, result)
        assert filt_mse < orig_mse, (
            f"Butterworth: orig_mse={orig_mse:.4f}, filt_mse={filt_mse:.4f}"
        )

    def test_wma_denoise(self, noisy_sine, clean_sine, time_index):
        """TC-DATA-01.4: WMA对含噪正弦波的平滑效果"""
        result = apply_wma(noisy_sine, time_index, window=3)
        orig_mse = np.mean((noisy_sine - clean_sine)**2)
        result_mse = np.mean((result - clean_sine)**2)
        assert result_mse < orig_mse, f"WMA should reduce noise: {result_mse} >= {orig_mse}"

    def test_butterworth_nyquist_clamp(self, noisy_sine, time_index):
        """TC-DATA-01.9: Butterworth cutoff>=Nyquist时自动钳制"""
        # cutoff=0.5 (Nyquist) 不应崩溃
        result = apply_butterworth(noisy_sine, time_index, order=4, cutoff=0.5)
        assert len(result) == len(noisy_sine)
        assert not np.all(np.isnan(result))

    def test_median_impulse_removal(self):
        """TC-DATA-01.10: Median window=3 脉冲去除"""
        signal = np.array([1.0, 100.0, 2.0, 1.0, 2.0], dtype=float)
        result = apply_median(signal, np.arange(len(signal)), window=3)
        # 中值滤波后 100 应被平滑
        assert result[1] < 50, f"Median should suppress impulse, got {result[1]}"


# ===================================================================
# SECTION 3 — Edge cases
# ===================================================================

class TestEdgeCases:
    """边界条件测试."""

    def test_empty_array_raises(self):
        """空的numpy数组应引发异常而非静默返回垃圾值."""
        s = np.array([])
        t = np.array([])
        for fn, args in [
            (apply_sma, (s, t, 5)),
            (apply_wma, (s, t, 5)),
            (apply_alma, (s, t, 5, 0.85, 6.0)),
            (apply_savgol, (s, t, 5, 2)),
            (apply_kalman, (s, t, 0.01, 1.0)),
            (apply_butterworth, (s, t, 4, 10.0)),
        ]:
            with pytest.raises((ValueError, IndexError)):
                fn(*args)

        # EMA / Gaussian / Median / LOWESS return empty array for empty input
        for fn, name in [(apply_ema, "ema"), (apply_gaussian, "gaussian"),
                          (apply_median, "median")]:
            result = fn(s, t, *([3] if name == "median" else [10] if name == "ema" else [3.0]))
            assert len(result) == 0, f"{name} should return empty array"

    def test_all_nan(self, time_index):
        """全NaN输入应产生全NaN输出（或至少不崩溃）. """
        s = np.full(100, np.nan)
        t = time_index[:100]

        # SMA, WMA, ALMA 等基于卷积的滤波器不会传播NaN
        for fn, args, name in [
            (apply_sma, (s, t, 5), "sma"),
            (apply_ema, (s, t, 10), "ema"),
            (apply_wma, (s, t, 5), "wma"),
            (apply_alma, (s, t, 5, 0.85, 6.0), "alma"),
            (apply_gaussian, (s, t, 3.0), "gaussian"),
            (apply_lowess, (s, t, 0.1), "lowess"),
        ]:
            result = fn(*args)
            assert np.all(np.isnan(result)), f"{name} should produce all-NaN output"

        # median: scipy.signal.medfilt 内部零填充，前 floor(window/2) 个点为 0
        result_m = apply_median(s, t, 5)
        assert len(result_m) == 100
        # 前2个点受零填充影响为0，其余应为NaN
        assert result_m[0] == 0.0 and result_m[1] == 0.0
        assert np.all(np.isnan(result_m[2:]))

    def test_window_1(self, constant_signal, time_index):
        """window=1 时输出应等于输入."""
        t = time_index[:len(constant_signal)]
        for fn, kwargs in [
            (apply_sma, {"window": 1}),
            (apply_wma, {"window": 1}),
            (apply_alma, {"window": 1, "offset": 0.85, "sigma": 6.0}),
            (apply_median, {"window": 1}),
        ]:
            result = fn(constant_signal, t, **kwargs)
            assert np.allclose(result, 1.0, atol=1e-6), f"{fn.__name__}: window=1 should preserve input"

    def test_large_window_vs_signal_length(self, constant_signal, time_index):
        """window > len(signal) 时不崩溃; convolve-based 滤波会返回更长数组."""
        t = time_index[:len(constant_signal)]
        for fn, args in [
            (apply_sma, (constant_signal, t, 200)),
            (apply_wma, (constant_signal, t, 200)),
            (apply_alma, (constant_signal, t, 200, 0.85, 6.0)),
            (apply_median, (constant_signal, t, 200)),
        ]:
            result = fn(*args)
            # convolve(mode="same") with kernel>signal returns signal+kernel-1
            assert len(result) >= len(constant_signal), f"{fn.__name__} should not shrink"

        # Savgol with window > len → 抛出异常
        with pytest.raises(ValueError):
            apply_savgol(constant_signal, t, 200, 2)


# ===================================================================
# SECTION 4 — Savgol special behaviour
# ===================================================================

class TestSavgolSpecial:
    """Savitzky-Golay 滤波器特有规则."""

    def test_even_window_auto_odd(self, constant_signal, time_index):
        """偶数window应自动+1变为奇数."""
        t = time_index[:len(constant_signal)]
        result = apply_savgol(constant_signal, t, window=10, order=2)
        assert len(result) == len(constant_signal)
        # 如果内部未处理偶数窗口，savgol_filter会报错
        assert np.allclose(result, 1.0, atol=1e-6)

    def test_order_gte_window_auto_reduce(self, constant_signal, time_index):
        """order >= window 时应自动降阶至 window-1."""
        t = time_index[:len(constant_signal)]
        # window=5, order=5 → 内部应降为 order=4 (window-1)
        result = apply_savgol(constant_signal, t, window=5, order=5)
        assert len(result) == len(constant_signal)
        assert np.allclose(result, 1.0, atol=1e-6)

    def test_denoise_with_even_window(self, noisy_sine, clean_sine, time_index):
        """传入偶数window应仍能正常降噪."""
        result = apply_savgol(noisy_sine, time_index, window=20, order=3)
        orig_mse = _mse(clean_sine, noisy_sine)
        filt_mse = _mse(clean_sine, result)
        assert filt_mse < orig_mse * 0.5


# ===================================================================
# SECTION 5 — Kalman special behaviour
# ===================================================================

class TestKalmanSpecial:
    """卡尔曼滤波器特有规则."""

    def test_extreme_q_does_not_crash(self, noisy_sine, time_index):
        """极端Q值不应崩溃."""
        result = apply_kalman(noisy_sine, time_index, Q=1e-6, R=1.0)
        assert len(result) == len(noisy_sine)
        assert not np.all(np.isnan(result))

        result2 = apply_kalman(noisy_sine, time_index, Q=1e6, R=1.0)
        assert len(result2) == len(noisy_sine)
        assert not np.all(np.isnan(result2))

    def test_extreme_r_does_not_crash(self, noisy_sine, time_index):
        """极端R值不应崩溃."""
        result = apply_kalman(noisy_sine, time_index, Q=0.01, R=1e-6)
        assert len(result) == len(noisy_sine)
        assert not np.all(np.isnan(result))

        result2 = apply_kalman(noisy_sine, time_index, Q=0.01, R=1e6)
        assert len(result2) == len(noisy_sine)
        assert not np.all(np.isnan(result2))

    def test_constant_signal_convergence(self, constant_signal, time_index):
        """常量信号下Kalman应收敛至真实值."""
        t = time_index[:len(constant_signal)]
        result = apply_kalman(constant_signal, t, Q=0.001, R=0.1)
        assert np.allclose(result[-30:], 1.0, atol=1e-2)


# ===================================================================
# SECTION 6 — compute_metrics
# ===================================================================

class TestComputeMetrics:
    """compute_metrics 测试."""

    def test_perfect_fit(self):
        """完美拟合: filtered == clean → SNR=99, MSE=0."""
        s = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        metrics = compute_metrics(s, s + 0.5, s)
        assert metrics["snr_imp"] == 99.0
        assert metrics["mse"] == 0.0
        assert metrics["rmse"] == 0.0
        assert metrics["mae"] == 0.0

    def test_less_than_3_valid_points(self):
        """少于3个有效点应返回NaN."""
        s = np.array([1.0, np.nan, np.nan])
        metrics = compute_metrics(s, s, s)
        assert np.isnan(metrics["mse"])
        assert np.isnan(metrics["rmse"])
        assert np.isnan(metrics["mae"])
        assert np.isnan(metrics["snr_imp"])
        assert metrics["lag"] == 0

    def test_known_offset(self):
        """已知偏移: filtered = clean + 1.0 → 可预测的MSE."""
        clean = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
        filtered = clean + 1.0
        noisy = clean.astype(float)
        metrics = compute_metrics(clean, noisy, filtered)
        assert metrics["mse"] == 1.0
        assert np.isclose(metrics["rmse"], 1.0)
        assert metrics["mae"] == 1.0

    def test_snr_improvement_monotonic(self):
        """更好滤波(更接近clean)应获得更高SNR."""
        np.random.seed(0)
        clean = np.sin(np.linspace(0, 4 * np.pi, 100))
        noisy = clean + np.random.randn(100) * 0.5

        # 好滤波（小偏差）
        good = apply_gaussian(noisy, np.arange(100), sigma=5.0)
        # 差滤波（大偏差）- 直接用原始噪声
        bad = noisy

        m_good = compute_metrics(clean, noisy, good)
        m_bad = compute_metrics(clean, noisy, bad)
        assert m_good["snr_imp"] > m_bad["snr_imp"]
        assert m_good["mse"] < m_bad["mse"]


# Helper for Section 2
def _mse(a, b):
    return float(np.mean((a - b) ** 2))
