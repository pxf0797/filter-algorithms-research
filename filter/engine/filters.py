"""
滤波器计算引擎 — 纯函数，无Streamlit依赖

包含：
- 10种滤波算法实现
- 卡尔曼滤波 (numba-accelerated)
- FILTERS 注册表
"""

import numpy as np
import pandas as pd
from scipy.signal import savgol_filter, butter, sosfiltfilt, medfilt
from scipy.ndimage import gaussian_filter1d
from statsmodels.nonparametric.smoothers_lowess import lowess
from pandas import DataFrame
from typing import Any, Dict

try:
    from numba import jit
    HAS_NUMBA = True
except ImportError:
    def jit(*args, **kwargs):
        return lambda f: f
    HAS_NUMBA = False


# ---------------------------------------------------------------------------
# Filter implementations (scipy / numpy only)
# All accept (signal, t, ...) signature so they can be called uniformly as
#   filter_func(noisy, t, **param_values)
# ---------------------------------------------------------------------------

def apply_sma(signal: np.ndarray, t: np.ndarray, window: int) -> np.ndarray:
    """简单移动平均 (Simple Moving Average).

    Parameters
    ----------
    signal : np.ndarray
        输入信号序列。
    t : np.ndarray
        时间索引（仅用于统一接口签名，未使用）。
    window : int
        移动平均窗口大小（若为偶数则自动 +1）。

    Returns
    -------
    np.ndarray
        平滑后的信号序列，长度与输入相同。
    """
    if window % 2 == 0:
        window += 1
    kernel = np.ones(window) / window
    return np.convolve(signal, kernel, mode="same")


def apply_ema(signal: np.ndarray, t: np.ndarray, span: int) -> np.ndarray:
    """指数移动平均 (Exponential Moving Average) via pandas ewm.

    Parameters
    ----------
    signal : np.ndarray
        输入信号序列。
    t : np.ndarray
        时间索引（仅用于统一接口签名，未使用）。
    span : int
        EMA 跨度（衰减因子 = 2/(span+1)）。

    Returns
    -------
    np.ndarray
        指数加权平滑后的信号序列，长度与输入相同。
    """
    return DataFrame({"v": signal}).ewm(span=span, adjust=False).mean().values.flatten()


def apply_wma(signal: np.ndarray, t: np.ndarray, window: int) -> np.ndarray:
    """加权移动平均 (Weighted Moving Average).

    权重线性递增，最新数据权重最大。

    Parameters
    ----------
    signal : np.ndarray
        输入信号序列。
    t : np.ndarray
        时间索引（仅用于统一接口签名，未使用）。
    window : int
        加权窗口大小（若为偶数则自动 +1）。

    Returns
    -------
    np.ndarray
        加权平滑后的信号序列，长度与输入相同。
    """
    if window % 2 == 0:
        window += 1
    weights = np.arange(1, window + 1)
    weights = weights / weights.sum()
    return np.convolve(signal, weights, mode="same")


def apply_alma(signal: np.ndarray, t: np.ndarray, window: int, offset: float, sigma: float) -> np.ndarray:
    """Arnaud Legoux 移动平均 (ALMA).

    使用高斯加权窗口，通过 offset 控制滤波延迟，
    sigma 控制高斯核的宽度。

    Parameters
    ----------
    signal : np.ndarray
        输入信号序列。
    t : np.ndarray
        时间索引（仅用于统一接口签名，未使用）。
    window : int
        窗口大小（若为偶数则自动 +1）。
    offset : float
        高斯核中心偏移量，范围 [0, 1]；0.85 表示靠近右端（延迟小）。
    sigma : float
        高斯核标准差，越大则平滑越强。

    Returns
    -------
    np.ndarray
        ALMA 平滑后的信号序列，长度与输入相同。
    """
    if window % 2 == 0:
        window += 1
    m = (window - 1) * offset               # Gaussian center (offset=0.85 -> near right = past)
    s = window / sigma if sigma > 0 else 1.0
    i = np.arange(window)
    weights = np.exp(-0.5 * ((i - m) / s) ** 2)
    weights /= weights.sum()
    return np.convolve(signal, weights, mode="same")


def apply_savgol(signal: np.ndarray, t: np.ndarray, window: int, order: int) -> np.ndarray:
    """Savitzky-Golay 滤波 (多项式平滑).

    对滑动窗口内的数据做局部多项式拟合，适合保留信号高频细节。

    Parameters
    ----------
    signal : np.ndarray
        输入信号序列。
    t : np.ndarray
        时间索引（仅用于统一接口签名，未使用）。
    window : int
        窗口大小（若为偶数则自动 +1），必须大于 order。
    order : int
        多项式拟合阶数，若 >= window 则自动降为 window-1。

    Returns
    -------
    np.ndarray
        Savitzky-Golay 平滑后的信号序列，长度与输入相同。
    """
    if window % 2 == 0:
        window += 1
    if order >= window:
        order = window - 1
    return savgol_filter(signal, window, order)


# ---------------------------------------------------------------------------
# Kalman filter numba-accelerated core
# ---------------------------------------------------------------------------

@jit(nopython=True, cache=True)
def _kalman_core(signal: np.ndarray, dt: float, Q: float, R: float) -> np.ndarray:
    """numba-accelerated 1D constant-velocity Kalman filter core loop.

    State: [position, velocity]. Constant-velocity model with
    scalar position observation.

    Parameters
    ----------
    signal : np.ndarray
        Input observation signal (position measurements).
    dt : float
        Time step between observations.
    Q : float
        Process noise covariance magnitude.
    R : float
        Measurement noise covariance.

    Returns
    -------
    np.ndarray
        Filtered position sequence, same length as signal.
    """
    n = len(signal)
    x = np.zeros(2)
    x[0] = signal[0]
    x[1] = 0.0
    P = np.zeros((2, 2))
    P[0, 0] = 0.1
    P[1, 1] = 0.1
    F = np.zeros((2, 2))
    F[0, 0] = 1.0
    F[0, 1] = dt
    F[1, 0] = 0.0
    F[1, 1] = 1.0
    result = np.zeros(n)

    for i in range(n):
        # Predict
        x_new = np.zeros(2)
        x_new[0] = F[0, 0] * x[0] + F[0, 1] * x[1]
        x_new[1] = F[1, 0] * x[0] + F[1, 1] * x[1]
        x[0] = x_new[0]
        x[1] = x_new[1]

        P_new = np.zeros((2, 2))
        for r_idx in range(2):
            for c_idx in range(2):
                s = 0.0
                for k in range(2):
                    s += F[r_idx, k] * P[k, c_idx]
                P_new[r_idx, c_idx] = s
        P_mid = np.zeros((2, 2))
        for r_idx in range(2):
            for c_idx in range(2):
                s = 0.0
                for k in range(2):
                    s += P_new[r_idx, k] * F[c_idx, k]
                P_mid[r_idx, c_idx] = s

        # Process noise
        P_mid[0, 0] += Q * dt ** 4 / 4.0
        P_mid[0, 1] += Q * dt ** 3 / 2.0
        P_mid[1, 0] += Q * dt ** 3 / 2.0
        P_mid[1, 1] += Q * dt ** 2

        P_old = P

        # Update (scalar observation)
        y = signal[i] - x[0]
        S = P_mid[0, 0] + R
        K = np.zeros(2)
        K[0] = P_mid[0, 0] / S
        K[1] = P_mid[1, 0] / S
        x[0] = x[0] + K[0] * y
        x[1] = x[1] + K[1] * y

        P = np.zeros((2, 2))
        # P = P_mid - K * K^T * S
        P[0, 0] = P_mid[0, 0] - K[0] * K[0] * S
        P[0, 1] = P_mid[0, 1] - K[0] * K[1] * S
        P[1, 0] = P_mid[1, 0] - K[1] * K[0] * S
        P[1, 1] = P_mid[1, 1] - K[1] * K[1] * S

        result[i] = x[0]

    return result


def apply_kalman(signal: np.ndarray, t: np.ndarray, Q: float, R: float) -> np.ndarray:
    """1D 恒定速度卡尔曼滤波.

    状态向量为 [position, velocity]，使用恒定速度模型进行预测-更新迭代。

    Parameters
    ----------
    signal : np.ndarray
        输入观测信号序列（位置观测量）。
    t : np.ndarray
        均匀时间索引，用于计算时间步长 dt。
    Q : float
        过程噪声协方差，控制模型不确定性；越大则滤波对观测响应越快。
    R : float
        测量噪声协方差，控制观测不确定性；越大则滤波越平滑。

    Returns
    -------
    np.ndarray
        卡尔曼滤波后的信号序列，长度与输入相同。
    """
    dt = float(t[1] - t[0])
    if HAS_NUMBA:
        return _kalman_core(signal, dt, Q, R)

    # Pure Python / NumPy fallback
    n = len(signal)
    x = np.array([signal[0], 0.0])     # [position, velocity]
    P = np.eye(2) * 0.1
    F = np.array([[1, dt], [0, 1]])     # state transition
    result = np.zeros(n)
    for i in range(n):
        # Predict
        x = F @ x
        P = F @ P @ F.T + np.array([
            [Q * dt ** 4 / 4, Q * dt ** 3 / 2],
            [Q * dt ** 3 / 2, Q * dt ** 2],
        ])
        # Update (scalar observation)
        y = signal[i] - x[0]             # innovation
        S = P[0, 0] + R                  # innovation covariance
        K = np.array([P[0, 0] / S, P[1, 0] / S])  # Kalman gain
        x = x + K * y
        P = P - np.outer(K, K) * S
        result[i] = x[0]
    return result


def apply_butterworth(signal: np.ndarray, t: np.ndarray, order: int, cutoff: float) -> np.ndarray:
    """巴特沃斯低通滤波 (零相位).

    使用 sosfiltfilt 实现零相位滤波（无延迟偏移），
    归一化奈奎斯特频率为 0.5（基于 bar 索引 dt~1, fs=1）。

    Parameters
    ----------
    signal : np.ndarray
        输入信号序列。
    t : np.ndarray
        时间索引（仅用于统一接口签名，未使用）。
    order : int
        滤波器阶数。
    cutoff : float
        截止频率 (Hz)，不能超过奈奎斯特频率 0.5。

    Returns
    -------
    np.ndarray
        巴特沃斯滤波后的信号序列，长度与输入相同。
    """
    nyquist = 0.5  # stock: bar index dt~1, fs=1, nyquist=0.5
    if cutoff >= nyquist:
        cutoff = nyquist * 0.99
    sos = butter(order, cutoff / nyquist, btype="low", output="sos")
    return sosfiltfilt(sos, signal)


def apply_gaussian(signal: np.ndarray, t: np.ndarray, sigma: float) -> np.ndarray:
    """高斯滤波 (scipy.ndimage).

    使用高斯核对信号做卷积平滑。

    Parameters
    ----------
    signal : np.ndarray
        输入信号序列。
    t : np.ndarray
        时间索引（仅用于统一接口签名，未使用）。
    sigma : float
        高斯核标准差；越大则平滑越强。

    Returns
    -------
    np.ndarray
        高斯平滑后的信号序列，长度与输入相同。
    """
    return gaussian_filter1d(signal, sigma)


def apply_median(signal: np.ndarray, t: np.ndarray, window: int) -> np.ndarray:
    """中值滤波.

    用窗口内中值替代中心点值，适合去除椒盐噪声。

    Parameters
    ----------
    signal : np.ndarray
        输入信号序列。
    t : np.ndarray
        时间索引（仅用于统一接口签名，未使用）。
    window : int
        窗口大小（若为偶数则自动 +1）。

    Returns
    -------
    np.ndarray
        中值滤波后的信号序列，长度与输入相同。
    """
    if window % 2 == 0:
        window += 1
    return medfilt(signal, kernel_size=window)


def apply_lowess(signal: np.ndarray, t: np.ndarray, frac: float) -> np.ndarray:
    """LOWESS 局部加权回归平滑.

    对每个点执行局部加权线性回归，适合非线性趋势的稳健平滑。

    Parameters
    ----------
    signal : np.ndarray
        输入信号序列。
    t : np.ndarray
        时间索引（用于局部回归的距离计算）。
    frac : float
        每个局部回归使用的数据比例，范围 (0, 1]；越大则越平滑。

    Returns
    -------
    np.ndarray
        LOWESS 平滑后的信号序列，长度与输入相同。
    """
    result = lowess(signal, t, frac=frac, return_sorted=False)
    # return_sorted=False returns a 1-D array of smoothed y-values
    return result


# ---------------------------------------------------------------------------
# Filter registry
# Each entry: (name, function, {param_name: (label, min, max, step, default)})
# ---------------------------------------------------------------------------
FILTERS = {
    "sma": {
        "name": "简单移动平均 (SMA)",
        "func": apply_sma,
        "params": {"window": ("窗口大小", 3, 101, 2, 11)},
    },
    "ema": {
        "name": "指数移动平均 (EMA)",
        "func": apply_ema,
        "params": {"span": ("跨度", 2, 100, 1, 10)},
    },
    "wma": {
        "name": "加权移动平均 (WMA)",
        "func": apply_wma,
        "params": {"window": ("窗口大小", 3, 101, 2, 11)},
    },
    "alma": {
        "name": "Arnaud Legoux 移动平均 (ALMA)",
        "func": apply_alma,
        "params": {
            "window": ("窗口大小", 3, 101, 2, 21),
            "offset": ("偏移量", 0.0, 1.0, 0.01, 0.85),
            "sigma": ("标准差", 1.0, 20.0, 0.1, 6.0),
        },
    },
    "savgol": {
        "name": "Savitzky-Golay 滤波",
        "func": apply_savgol,
        "params": {
            "window": ("窗口大小", 5, 101, 2, 21),
            "order": ("多项式阶数", 1, 5, 1, 2),
        },
    },
    "kalman": {
        "name": "卡尔曼滤波",
        "func": apply_kalman,
        "params": {
            "Q": ("过程噪声 Q", 0.001, 1.0, 0.001, 0.01),
            "R": ("测量噪声 R", 0.01, 10.0, 0.01, 1.0),
        },
    },
    "butterworth": {
        "name": "巴特沃斯低通滤波",
        "func": apply_butterworth,
        "params": {
            "order": ("滤波器阶数", 1, 8, 1, 4),
            "cutoff": ("截止频率 (Hz)", 1.0, 45.0, 0.5, 10.0),
        },
    },
    "gaussian": {
        "name": "高斯滤波",
        "func": apply_gaussian,
        "params": {"sigma": ("标准差 Sigma", 0.5, 20.0, 0.1, 3.0)},
    },
    "median": {
        "name": "中值滤波",
        "func": apply_median,
        "params": {"window": ("窗口大小", 3, 101, 2, 5)},
    },
    "lowess": {
        "name": "LOWESS 平滑",
        "func": apply_lowess,
        "params": {"frac": ("平滑比例", 0.01, 0.5, 0.01, 0.1)},
    },
}
