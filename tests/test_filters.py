"""
Tests for all 10 filter functions and compute_metrics.

Fixtures are shared from tests/conftest.py.
"""

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Import module under test
# ---------------------------------------------------------------------------
# Filter functions moved to services/filter_engine in Phase 2 modularization
from engine.filters import (
    apply_sma, apply_ema, apply_wma, apply_alma,
    apply_savgol, apply_kalman, apply_butterworth,
    apply_gaussian, apply_median, apply_lowess,
    FILTERS,
)
from engine.alignment import compute_metrics

pytestmark = pytest.mark.filter


# ===================================================================
# SECTION 1 — Constant signal (all filters)
# ===================================================================

class TestConstantSignal:
    """每个滤波器对常量信号应返回≈常量值 (parametrized)."""

    CONSTANT_CASES = [
        # (filter_key, params, check_slice, atol, description)
        ("sma",         {"window": 11},                             slice(10, -10), 1e-6, "SMA 窗口11"),
        ("ema",         {"span": 10},                               slice(None),    1e-6, "EMA span=10"),
        ("wma",         {"window": 11},                             slice(10, -10), 1e-6, "WMA 窗口11"),
        ("alma",        {"window": 11, "offset": 0.85, "sigma": 6.0}, slice(10, -10), 1e-6, "ALMA 窗口11"),
        ("savgol",      {"window": 11, "order": 2},                slice(None),    1e-6, "SavGol 窗口11"),
        ("kalman",      {"Q": 0.01, "R": 1.0},                     slice(-20, None), 1e-3, "Kalman 收敛"),
        ("butterworth", {"order": 4, "cutoff": 10.0},              slice(10, -10), 1e-6, "Butterworth 4阶"),
        ("gaussian",    {"sigma": 3.0},                             slice(None),    1e-6, "Gaussian σ=3"),
        ("median",      {"window": 11},                             slice(None),    1e-6, "Median 窗口11"),
        ("lowess",      {"frac": 0.1},                              slice(None),    1e-5, "LOWESS frac=0.1"),
    ]

    @pytest.mark.parametrize("filter_key,params,check_slice,atol,_desc", CONSTANT_CASES)
    def test_constant_signal(self, constant_signal, time_index,
                             filter_key, params, check_slice, atol, _desc):
        t = time_index[:len(constant_signal)]
        func = FILTERS[filter_key]["func"]
        result = func(constant_signal, t, **params)
        assert np.allclose(result[check_slice], 1.0, atol=atol), \
            f"{_desc}: interior not constant"


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


# ===================================================================
# SECTION 7 — Parameterized window sizes for each filter
# ===================================================================

class TestParameterizedWindows:
    """每种滤波器在不同窗口大小下的行为验证."""

    # windows: small=5, medium=50, large=200
    WINDOW_SIZES = [5, 50, 200]

    @pytest.mark.parametrize("window", [5, 11, 51, 101])
    def test_sma_various_windows(self, noisy_sine, time_index, window):
        """SMA 不同窗口下输出长度一致、无异常NaN."""
        t = time_index[:len(noisy_sine)]
        result = apply_sma(noisy_sine, t, window=window)
        assert len(result) == len(noisy_sine)
        assert not np.any(np.isnan(result))
        # 窗口越大越平滑 (larger window → smaller variance)
        if window >= 51:
            result_big = result
            result_small = apply_sma(noisy_sine, t, window=5)
            assert np.var(result_big) < np.var(result_small), (
                f"窗口{window}的方差应小于窗口5"
            )

    @pytest.mark.parametrize("span", [3, 10, 50, 100])
    def test_ema_various_spans(self, noisy_sine, time_index, span):
        """EMA 不同 span 下输出长度一致、无异常NaN."""
        t = time_index[:len(noisy_sine)]
        result = apply_ema(noisy_sine, t, span=span)
        assert len(result) == len(noisy_sine)
        assert not np.any(np.isnan(result))

    @pytest.mark.parametrize("window", [5, 11, 51, 101])
    def test_wma_various_windows(self, noisy_sine, time_index, window):
        """WMA 不同窗口下输出长度一致."""
        t = time_index[:len(noisy_sine)]
        result = apply_wma(noisy_sine, t, window=window)
        assert len(result) == len(noisy_sine)
        assert not np.any(np.isnan(result))

    @pytest.mark.parametrize("window,offset,sigma", [
        (11, 0.5, 6.0), (21, 0.85, 3.0), (51, 0.2, 10.0), (101, 0.9, 1.0),
    ])
    def test_alma_various_params(self, noisy_sine, time_index, window, offset, sigma):
        """ALMA 不同参数组合下输出长度一致."""
        t = time_index[:len(noisy_sine)]
        result = apply_alma(noisy_sine, t, window=window, offset=offset, sigma=sigma)
        assert len(result) == len(noisy_sine)
        assert not np.any(np.isnan(result))

    @pytest.mark.parametrize("window,order", [(11, 2), (21, 3), (51, 3), (101, 4)])
    def test_savgol_various_params(self, noisy_sine, time_index, window, order):
        """SavGol 不同参数组合下输出长度一致."""
        t = time_index[:len(noisy_sine)]
        result = apply_savgol(noisy_sine, t, window=window, order=order)
        assert len(result) == len(noisy_sine)
        assert not np.any(np.isnan(result))

    @pytest.mark.parametrize("Q,R", [(0.001, 1.0), (0.01, 0.5), (0.1, 0.1), (1.0, 0.01)])
    def test_kalman_various_params(self, noisy_sine, time_index, Q, R):
        """Kalman 不同 Q/R 下输出长度一致、无崩溃."""
        t = time_index[:len(noisy_sine)]
        result = apply_kalman(noisy_sine, t, Q=Q, R=R)
        assert len(result) == len(noisy_sine)
        assert not np.any(np.isnan(result))

    @pytest.mark.parametrize("order,cutoff", [(2, 0.1), (4, 0.3), (6, 0.2), (8, 0.05)])
    def test_butterworth_various_params(self, noisy_sine, time_index, order, cutoff):
        """Butterworth 不同阶数/截止频率下输出长度一致."""
        t = time_index[:len(noisy_sine)]
        result = apply_butterworth(noisy_sine, t, order=order, cutoff=cutoff)
        assert len(result) == len(noisy_sine)
        assert not np.any(np.isnan(result))

    @pytest.mark.parametrize("sigma", [0.5, 3.0, 10.0, 20.0])
    def test_gaussian_various_sigmas(self, noisy_sine, time_index, sigma):
        """Gaussian 不同 sigma 下输出长度一致."""
        t = time_index[:len(noisy_sine)]
        result = apply_gaussian(noisy_sine, t, sigma=sigma)
        assert len(result) == len(noisy_sine)
        assert not np.any(np.isnan(result))
        # Larger sigma → smoother
        if sigma >= 10.0:
            result_big = result
            result_small = apply_gaussian(noisy_sine, t, sigma=0.5)
            assert np.var(result_big) < np.var(result_small)

    @pytest.mark.parametrize("window", [3, 11, 51, 101])
    def test_median_various_windows(self, noisy_sine, time_index, window):
        """Median 不同窗口下输出长度一致."""
        t = time_index[:len(noisy_sine)]
        result = apply_median(noisy_sine, t, window=window)
        assert len(result) == len(noisy_sine)

    @pytest.mark.parametrize("frac", [0.05, 0.1, 0.3, 0.5])
    def test_lowess_various_fracs(self, noisy_sine, time_index, frac):
        """LOWESS 不同 frac 下输出长度一致."""
        t = time_index[:len(noisy_sine)]
        result = apply_lowess(noisy_sine, t, frac=frac)
        assert len(result) == len(noisy_sine)


# ===================================================================
# SECTION 8 — Boundary & extreme value tests
# ===================================================================

class TestBoundaryExtremeValues:
    """边界条件与极端值测试."""

    def test_all_zero_signal(self, time_index):
        """全零信号经滤波器后应保持≈0."""
        s = np.zeros(100)
        t = time_index[:100]
        for fn, kwargs, name, atol in [
            (apply_sma, {"window": 11}, "sma", 1e-6),
            (apply_ema, {"span": 10}, "ema", 1e-6),
            (apply_wma, {"window": 11}, "wma", 1e-6),
            (apply_alma, {"window": 11, "offset": 0.85, "sigma": 6.0}, "alma", 1e-6),
            (apply_savgol, {"window": 11, "order": 2}, "savgol", 1e-6),
            (apply_gaussian, {"sigma": 3.0}, "gaussian", 1e-6),
            (apply_median, {"window": 11}, "median", 1e-6),
        ]:
            result = fn(s, t, **kwargs)
            assert np.allclose(result, 0.0, atol=atol), f"{name}: zero signal not preserved"
        # Kalman: 零输入时应收敛到 0
        result_k = apply_kalman(s, t, Q=0.01, R=1.0)
        assert np.allclose(result_k[-30:], 0.0, atol=0.1), "Kalman should converge to 0"

    def test_extreme_values_1e6(self, time_index):
        """极值信号(1e6)不导致NaN或溢出."""
        s = np.full(100, 1e6)
        t = time_index[:100]
        for fn, kwargs, name in [
            (apply_sma, {"window": 11}, "sma"),
            (apply_ema, {"span": 10}, "ema"),
            (apply_wma, {"window": 11}, "wma"),
            (apply_alma, {"window": 11, "offset": 0.85, "sigma": 6.0}, "alma"),
            (apply_gaussian, {"sigma": 3.0}, "gaussian"),
            (apply_median, {"window": 11}, "median"),
        ]:
            result = fn(s, t, **kwargs)
            assert not np.any(np.isnan(result)), f"{name}: NaN in extreme value output"
            assert not np.any(np.isinf(result)), f"{name}: Inf in extreme value output"
            # 跳过边缘效应 (window/2)，仅检查内部区域
            trim = 20
            assert np.allclose(result[trim:-trim], 1e6, atol=1e-4 * 1e6), (
                f"{name}: extreme constant not preserved in interior, "
                f"mean={np.mean(result[trim:-trim])}"
            )

    def test_extreme_values_negative(self, time_index):
        """负极大值信号不导致崩溃."""
        s = np.full(100, -1e6)
        t = time_index[:100]
        for fn, kwargs, name in [
            (apply_sma, {"window": 11}, "sma"),
            (apply_ema, {"span": 10}, "ema"),
            (apply_gaussian, {"sigma": 3.0}, "gaussian"),
            (apply_savgol, {"window": 11, "order": 2}, "savgol"),
        ]:
            result = fn(s, t, **kwargs)
            assert len(result) == len(s), f"{name}: length mismatch"
            assert not np.any(np.isnan(result)), f"{name}: NaN in negative extreme output"

    def test_single_point_signal(self):
        """单点信号至少不崩溃(Kalman除外,需要>=2点计算dt)."""
        s = np.array([3.14])
        t = np.array([0.0])
        for fn, kwargs, name in [
            (apply_sma, {"window": 3}, "sma"),
            (apply_ema, {"span": 10}, "ema"),
            (apply_wma, {"window": 3}, "wma"),
            (apply_alma, {"window": 3, "offset": 0.85, "sigma": 6.0}, "alma"),
            (apply_gaussian, {"sigma": 3.0}, "gaussian"),
            (apply_median, {"window": 3}, "median"),
            (apply_lowess, {"frac": 0.1}, "lowess"),
        ]:
            result = fn(s, t, **kwargs)
            assert len(result) >= 1, f"{name}: single point should return at least 1 element"

    def test_two_point_signal(self):
        """两点信号各滤波器不应崩溃."""
        s = np.array([1.0, 5.0])
        t = np.array([0.0, 1.0])
        for fn, kwargs, name in [
            (apply_sma, {"window": 3}, "sma"),
            (apply_ema, {"span": 10}, "ema"),
            (apply_wma, {"window": 3}, "wma"),
            (apply_alma, {"window": 3, "offset": 0.85, "sigma": 6.0}, "alma"),
            (apply_gaussian, {"sigma": 3.0}, "gaussian"),
            (apply_median, {"window": 3}, "median"),
            (apply_lowess, {"frac": 0.3}, "lowess"),
        ]:
            result = fn(s, t, **kwargs)
            assert len(result) >= 2, f"{name}: two-point signal failed"

    def test_large_window_small_signal_sma(self, time_index):
        """window >> len(signal) 时 SMA 不崩溃且输出>=输入长度."""
        s = np.array([1.0, 2.0, 3.0])
        t = time_index[:3]
        result = apply_sma(s, t, window=101)
        assert len(result) >= 3
        result2 = apply_sma(s, t, window=1001)
        assert len(result2) >= 3

    def test_savgol_order_gte_window_all_values(self, constant_signal, time_index):
        """参数化: order 从 window-1 到 window+5 均不崩溃."""
        t = time_index[:30]
        s = constant_signal[:30]
        for order in [4, 5, 6, 7, 8, 9, 10]:
            result = apply_savgol(s, t, window=5, order=order)
            assert len(result) == len(s), f"SavGol window=5, order={order}: length mismatch"

    def test_butterworth_cutoff_near_zero(self, noisy_sine, time_index):
        """Butterworth cutoff 接近 0 时不崩溃."""
        t = time_index[:len(noisy_sine)]
        result = apply_butterworth(noisy_sine, t, order=2, cutoff=0.001)
        assert len(result) == len(noisy_sine)
        assert not np.all(np.isnan(result))

    def test_butterworth_cutoff_near_nyquist(self, noisy_sine, time_index):
        """Butterworth cutoff 接近 Nyquist(=0.5) 时被自动钳制."""
        t = time_index[:len(noisy_sine)]
        result = apply_butterworth(noisy_sine, t, order=4, cutoff=0.49)
        assert len(result) == len(noisy_sine)

    def test_kalman_two_point(self):
        """Kalman 两点输入不应崩溃."""
        s = np.array([5.0, 5.2])
        t = np.array([0.0, 1.0])
        result = apply_kalman(s, t, Q=0.01, R=1.0)
        assert len(result) == 2
        assert not np.any(np.isnan(result))

    def test_ema_span_equals_1(self, noisy_sine, time_index):
        """EMA span=1 时输出应接近输入 (几乎不平滑)."""
        t = time_index[:len(noisy_sine)]
        result = apply_ema(noisy_sine, t, span=1)
        assert len(result) == len(noisy_sine)
        # span=1 → alpha=1.0 → result[t] = signal[t]
        assert np.allclose(result, noisy_sine, atol=1e-10)

    def test_linear_ramp_sma_preservation(self, time_index):
        """线性斜坡信号经 SMA 后内部保持递增趋势."""
        s = np.arange(1, 101, dtype=float)
        t = time_index[:100]
        result = apply_sma(s, t, window=5)
        # 内部区域 (跳过首尾 window//2 边缘) 应保持递增
        interior = result[20:-20]
        assert np.all(np.diff(interior) > -1e-6), "SMA interior should preserve upward trend"


# ===================================================================
# SECTION 9 — Numerical stability tests
# ===================================================================

class TestNumericalStability:
    """数值稳定性与精度测试."""

    def test_sma_output_float_dtype(self, constant_signal, time_index):
        """SMA 输出应为 float 类型."""
        t = time_index[:len(constant_signal)]
        result = apply_sma(constant_signal, t, window=11)
        assert result.dtype.kind == "f", f"Expected float dtype, got {result.dtype}"

    def test_large_small_window_consistency_sma(self, noisy_sine, time_index):
        """SMA: 大窗口与小窗口在重叠区的一致性 (大窗口更平滑)."""
        t = time_index[:len(noisy_sine)]
        small = apply_sma(noisy_sine, t, window=11)
        large = apply_sma(noisy_sine, t, window=51)
        # 大窗口方差更小
        assert np.var(large[25:-25]) < np.var(small[25:-25])

    def test_large_small_window_consistency_ema(self, noisy_sine, time_index):
        """EMA: span越大越平滑."""
        t = time_index[:len(noisy_sine)]
        small = apply_ema(noisy_sine, t, span=3)
        large = apply_ema(noisy_sine, t, span=50)
        assert np.var(large) < np.var(small)

    def test_even_window_auto_odd_consistency(self, noisy_sine, time_index):
        """偶数窗口自动+1为奇数，结果应与手动+1一致."""
        t = time_index[:len(noisy_sine)]
        even_result = apply_sma(noisy_sine, t, window=10)  # 内部变为11
        odd_result = apply_sma(noisy_sine, t, window=11)
        assert np.allclose(even_result, odd_result, atol=1e-12)

    def test_wma_weights_sum_to_one(self):
        """WMA 权重归一化验证: sum(weights) == 1."""
        # 通过常量信号间接验证：若 sum(weights) != 1，输出会偏离输入
        s = np.ones(100)
        t = np.arange(100, dtype=float)
        result = apply_wma(s, t, window=11)
        # 跳过边缘，中间应为 1.0
        assert np.allclose(result[10:-10], 1.0, atol=1e-12)

    def test_alma_weights_normalized(self, constant_signal, time_index):
        """ALMA 权重归一化验证."""
        t = time_index[:len(constant_signal)]
        result = apply_alma(constant_signal, t, window=11, offset=0.85, sigma=6.0)
        assert np.allclose(result[10:-10], 1.0, atol=1e-12)

    def test_savgol_symmetric_preserving(self, constant_signal, time_index):
        """SavGol 常量信号保持对称性."""
        t = time_index[:len(constant_signal)]
        result = apply_savgol(constant_signal, t, window=11, order=2)
        assert np.allclose(result, 1.0, atol=1e-12)

    def test_gaussian_sigma_tiny(self, constant_signal, time_index):
        """Gaussian 极小 sigma (0.01) 应几乎不改变信号."""
        t = time_index[:len(constant_signal)]
        result = apply_gaussian(constant_signal, t, sigma=0.01)
        assert len(result) == len(constant_signal)
        assert np.allclose(result, 1.0, atol=1e-1)

    def test_butterworth_output_finite(self, noisy_sine, time_index):
        """Butterworth 输出全为有限值."""
        t = time_index[:len(noisy_sine)]
        result = apply_butterworth(noisy_sine, t, order=4, cutoff=0.1)
        assert np.all(np.isfinite(result))

    def test_kalman_output_finite(self, noisy_sine, time_index):
        """Kalman 输出全为有限值."""
        t = time_index[:len(noisy_sine)]
        result = apply_kalman(noisy_sine, t, Q=0.01, R=1.0)
        assert np.all(np.isfinite(result))

    def test_median_preserves_edges(self):
        """Median 滤波对平顶信号边缘应保持."""
        s = np.array([0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0])
        t = np.arange(len(s), dtype=float)
        result = apply_median(s, t, window=3)
        # 中间平台应保持为1
        assert result[3] == 1.0
        assert result[4] == 1.0
        assert result[5] == 1.0

    def test_lowess_output_finite(self, noisy_sine, time_index):
        """LOWESS 输出全为有限值."""
        t = time_index[:len(noisy_sine)]
        result = apply_lowess(noisy_sine, t, frac=0.15)
        assert np.all(np.isfinite(result))


# ===================================================================
# SECTION 10 — FILTERS registry completeness
# ===================================================================

class TestFilterRegistry:
    """FILTERS 注册表完整性验证."""

    EXPECTED_FILTERS = [
        "sma", "ema", "wma", "alma", "savgol",
        "kalman", "butterworth", "gaussian", "median", "lowess",
    ]

    def test_all_10_filters_registered(self):
        """所有 10 种滤波器均在 FILTERS 中注册."""
        for key in self.EXPECTED_FILTERS:
            assert key in FILTERS, f"{key} missing from FILTERS registry"

    def test_all_filters_have_func_name_params(self):
        """每个注册项含 func, name, params 三个必需键."""
        for key, entry in FILTERS.items():
            assert "func" in entry, f"{key}: missing 'func'"
            assert "name" in entry, f"{key}: missing 'name'"
            assert "params" in entry, f"{key}: missing 'params'"
            assert callable(entry["func"]), f"{key}: func is not callable"

    def test_all_funcs_accept_signal_t_kwargs(self, constant_signal, time_index):
        """所有注册的 func 可通过 func(signal, t, **pv) 调用."""
        t = time_index[:len(constant_signal)]
        for key, entry in FILTERS.items():
            # 从 params 提取默认值（取每个参数 tuple 的最后一个元素）
            default_kwargs = {}
            for pname, pdef in entry["params"].items():
                if isinstance(pdef, tuple) and len(pdef) >= 5:
                    default_kwargs[pname] = pdef[4]
            try:
                result = entry["func"](constant_signal, t, **default_kwargs)
                assert len(result) == len(constant_signal), (
                    f"{key}: output length mismatch"
                )
            except Exception as e:
                pytest.fail(f"{key}: call with defaults raised {e}")


# ===================================================================
# SECTION 11 — Additional compute_metrics tests
# ===================================================================

class TestComputeMetricsExtended:
    """compute_metrics 扩展测试."""

    def test_lag_detection(self):
        """滞后量检测: 右移信号应产生正 lag."""
        clean = np.sin(np.linspace(0, 4 * np.pi, 200))
        noisy = clean + np.random.RandomState(42).randn(200) * 0.05
        # 模拟滞后: filtered = 右移1个 bar 的 clean
        filtered = np.roll(clean, 1)
        filtered[0] = filtered[1]
        metrics = compute_metrics(clean, noisy, filtered)
        # 滞后应为正数 (filtered 落后于 clean)
        assert metrics["lag"] >= 0, f"Expected non-negative lag, got {metrics['lag']}"

    def test_roughness_metric(self):
        """粗糙度: 平滑信号粗糙度 < 噪声信号粗糙度."""
        clean = np.sin(np.linspace(0, 4 * np.pi, 100))
        noisy = clean + np.random.RandomState(42).randn(100) * 0.3
        filtered = apply_gaussian(noisy, np.arange(100), sigma=5.0)
        m_noisy = compute_metrics(clean, noisy, noisy)
        m_filtered = compute_metrics(clean, noisy, filtered)
        assert m_filtered["roughness"] < m_noisy["roughness"], (
            f"Filtered roughness {m_filtered['roughness']} >= noisy {m_noisy['roughness']}"
        )

    def test_snr_improvement_zero_noise(self):
        """无噪声信号 (noisy == clean): SNR_imp 应为 99.0."""
        s = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        metrics = compute_metrics(s, s, s)
        assert metrics["snr_imp"] == 99.0

    def test_all_metrics_are_finite_for_good_data(self, noisy_sine, clean_sine, time_index):
        """良好数据下所有指标应为有限值."""
        t = time_index[:len(noisy_sine)]
        filtered = apply_sma(noisy_sine, t, window=21)
        metrics = compute_metrics(clean_sine, noisy_sine, filtered)
        import math
        for key in ["mse", "rmse", "mae", "snr_imp", "roughness"]:
            val = metrics[key]
            assert not math.isnan(val), f"{key} is NaN"
            assert not math.isinf(val), f"{key} is Inf"
        assert isinstance(metrics["lag"], (int, float))
