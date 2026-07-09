"""
滤波器计算引擎 — 纯函数，无Streamlit依赖

包含：
- 施密特触发器计算
- 抛物线/多项式拟合预测
- 策略PnL计算
- 跨周期对齐与同向性判断
"""

import numpy as np
import pandas as pd
from scipy.signal import savgol_filter, butter, sosfiltfilt, medfilt
from scipy.ndimage import gaussian_filter1d
from statsmodels.nonparametric.smoothers_lowess import lowess
from pandas import DataFrame
from typing import Any, Dict, List, Optional, Tuple


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
    m = (window - 1) * offset               # Gaussian center (offset=0.85 → near right = past)
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
        多项式拟合阶数，若 ≥ window 则自动降为 window-1。

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
    dt = t[1] - t[0]
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
    归一化奈奎斯特频率为 0.5（基于 bar 索引 dt≈1, fs=1）。

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
    nyquist = 0.5  # stock: bar index dt≈1, fs=1, nyquist=0.5
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


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------
def compute_metrics(clean: np.ndarray, noisy: np.ndarray, filtered: np.ndarray) -> Dict[str, Any]:
    """计算 6 项滤波质量指标。

    指标包括：MSE、RMSE、MAE、信噪比提升 (SNR_imp)、
    滞后量 (lag via cross-correlation)、粗糙度 (roughness)。

    Parameters
    ----------
    clean : np.ndarray
        无噪声的参考信号（真值）。
    noisy : np.ndarray
        含噪声的原始信号。
    filtered : np.ndarray
        滤波后的信号。

    Returns
    -------
    Dict[str, Any]
        包含以下键的字典：
        - "mse" : float, 均方误差
        - "rmse" : float, 均方根误差
        - "mae" : float, 平均绝对误差
        - "snr_imp" : float, 滤波后信噪比提升 (dB)
        - "lag" : int, 滤波相对噪声信号的滞后量 (bar 数)
        - "roughness" : float, 滤波信号的二阶差分平方和
    """
    valid = ~np.isnan(filtered) & ~np.isnan(clean) & ~np.isnan(noisy)
    c, n, f = clean[valid], noisy[valid], filtered[valid]
    if len(c) < 3:
        return {
            "mse": np.nan, "rmse": np.nan, "mae": np.nan,
            "snr_imp": np.nan, "lag": 0, "roughness": np.nan,
        }

    residuals = f - c
    mse = float(np.mean(residuals ** 2))
    rmse = float(np.sqrt(mse))
    mae = float(np.mean(np.abs(residuals)))

    noise_var = float(np.var(n - c))
    err_var = float(np.var(residuals))
    snr_imp = 10 * np.log10(noise_var / err_var) if err_var > 1e-12 else 99.0

    # Lag via cross-correlation (peak of corr(filtered, noisy))
    crosscorr = np.correlate(f - np.mean(f), n - np.mean(n), mode="full")
    lag = int(np.argmax(crosscorr) - (len(f) - 1))

    # Roughness: sum of squared second differences
    roughness = float(np.sum(np.diff(f, 2) ** 2)) if len(f) > 2 else 0.0

    return {
        "mse": mse, "rmse": rmse, "mae": mae,
        "snr_imp": snr_imp, "lag": lag, "roughness": roughness,
    }


# ---------------------------------------------------------------------------
# Schmitt Trigger computation (ref: 多周期趋势策略V2_优化4 §二, chart ④⑥)
# Inputs: v (velocity = d(filtered)/dt) as momentum, a (d²/dt²) as acceleration
# ---------------------------------------------------------------------------
def _schmitt_trigger(v: np.ndarray, a: np.ndarray, ewma_span: int = 60,
                     k_eps: float = 0.15, sigma_min: float = 0.05) -> Optional[Dict[str, Any]]:
    """Schmitt trigger: adaptive deadband on acceleration (a),
    with velocity (v) as direction constraint.

    - v → momentum (物理意义: 趋势速度, 类比文档 x_t)
    - a → acceleration (物理意义: 趋势加速, 类比文档 a_t)
    - ε_t = k_ε · max(σ_t(v), σ_min)  — 自适应死区基于 v 的波动率
    - Sig_t: a>ε AND v>0 → +1(多); a<-ε AND v<0 → -1(空); else 0(观望)

    Parameters
    ----------
    v : np.ndarray
        速度/动量信号（一阶导数）。
    a : np.ndarray
        加速度信号（二阶导数）。
    ewma_span : int, optional
        EWMA 波动率估计的跨度（默认 60）。
    k_eps : float, optional
        自适应死区系数（默认 0.15）。
    sigma_min : float, optional
        死区下限，防止死区过小（默认 0.05）。

    Returns
    -------
    Optional[Dict[str, Any]]
        包含 "mu_v"（EWMA均值）、"sigma_v"（EWMA标准差）、
        "eps"（自适应阈值）、"sig"（±1/0 信号）、"dur"（持续期数）的字典。
        数据不足时返回 None。
    """
    n = len(v)
    if n < ewma_span:
        return None

    # EWMA volatility of v → σ_t(v)
    alpha = 2.0 / (ewma_span + 1)
    mu_v = np.full(n, np.nan)
    sigma_v = np.full(n, np.nan)
    mu_v[0] = v[0]
    sigma_v[0] = 0.0
    for i in range(1, n):
        mu_v[i] = alpha * v[i] + (1 - alpha) * mu_v[i - 1]
        sigma_v[i] = np.sqrt(
            alpha * (v[i] - mu_v[i]) ** 2 + (1 - alpha) * sigma_v[i - 1] ** 2)

    # Adaptive deadband
    eps_t = k_eps * np.maximum(sigma_v, sigma_min)

    # Schmitt trigger with hysteresis
    sig_t = np.zeros(n, dtype=int)
    dur_t = np.zeros(n, dtype=int)
    current_state = 0
    current_dur = 0
    for i in range(n):
        if np.isnan(a[i]) or np.isnan(v[i]):
            sig_t[i] = current_state
            current_dur += 1
        else:
            if current_state == 0:
                if a[i] > eps_t[i] and v[i] > 0:
                    current_state = 1
                    current_dur = 1
                elif a[i] < -eps_t[i] and v[i] < 0:
                    current_state = -1
                    current_dur = 1
                else:
                    current_dur += 1
            elif current_state == 1:
                if a[i] < -eps_t[i]:
                    current_state = 0
                    current_dur = 1
                else:
                    current_dur += 1
            else:  # -1
                if a[i] > eps_t[i]:
                    current_state = 0
                    current_dur = 1
                else:
                    current_dur += 1
        sig_t[i] = current_state
        dur_t[i] = current_dur

    return {"mu_v": mu_v, "sigma_v": sigma_v,
            "eps": eps_t, "sig": sig_t, "dur": dur_t}


def _merge_segments(sig_t: np.ndarray) -> List[Tuple[int, int, int]]:
    """收集连续非零段，合并相邻同号段（中间观望区并入同号段）。

    Parameters
    ----------
    sig_t : np.ndarray
        Schmitt 信号序列 (+1/0/-1)，长度 n。

    Returns
    -------
    List[Tuple[int, int, int]]
        [(start_idx, end_idx, direction), ...]
        direction 为 +1（多）或 -1（空）。
        当 sig_t 长度 < 2 时返回空列表。
    """
    n = len(sig_t)
    if n < 2:
        return []

    # Step 1: 收集所有非零段
    segments = []
    i = 0
    while i < n:
        if sig_t[i] != 0:
            start = i
            val = sig_t[i]
            while i < n and sig_t[i] == val:
                i += 1
            end = i - 1
            segments.append((start, end, val))
        else:
            i += 1

    if not segments:
        return []

    # Step 2: 合并相邻同号段（+1,0,+1 → 一个连续段）
    merged = [segments[0]]
    for seg in segments[1:]:
        last = merged[-1]
        if seg[2] == last[2]:
            merged[-1] = (last[0], seg[1], seg[2])
        else:
            merged.append(seg)

    return merged


def _find_all_pairs(sig_t: np.ndarray) -> List[Tuple[int, int]]:
    """扫描 sig_t，找出窗口中所有多空切换对。

    规则：合并相邻同号段（+1,0,+1 → 一个连续段），异号配对。
    起始于首次入场边缘，经过中间的同向多次入场+观望，止于相反信号入口。

    Parameters
    ----------
    sig_t : np.ndarray
        施密特触发器输出信号（+1=多, -1=空, 0=观望）。

    Returns
    -------
    List[Tuple[int, int]]
        多空切换对列表 [(start_idx, end_idx), ...]，
        每个 pair 的起始和结束均为 index。
        无有效配对时返回空列表。
    """
    merged = _merge_segments(sig_t)
    if len(merged) < 2:
        return []

    # 相邻异号段配对 — 结束于相反信号的入口边缘
    pairs = []
    for j in range(len(merged) - 1):
        s1, e1, v1 = merged[j]
        s2, e2, v2 = merged[j + 1]
        if v1 != v2:  # 多→空 或 空→多
            pairs.append((s1, s2))  # 结束于相反信号的入口（边缘）

    return pairs


def _find_current_half_pair(
    sig_t: np.ndarray,
    merged: List[Tuple[int, int, int]],
    edge_width: int = 3,
) -> Optional[Dict[str, int]]:
    """识别窗口最右侧的半边多空对。

    半边 = merged 最后一段（方向非零时）。它"有起点、无终点"。
    若最后一段方向为 0（即窗口末尾处于观望），则返回 None。

    Parameters
    ----------
    sig_t : np.ndarray
        Schmitt 信号序列。
    merged : List[Tuple[int, int, int]]
        _merge_segments 的返回值。
    edge_width : int, optional
        边缘区宽度（bar 数），起点距右边缘 <= edge_width 时视为未锁定，
        默认 3。

    Returns
    -------
    Optional[Dict[str, int]]
        None 无有效半边（全为 0 或末尾在观望）；
        Dict:
            - "start" : 半边起点 bar 索引
            - "current_idx" : 当前帧索引（= len(sig_t) - 1）
            - "direction" : +1 多 / -1 空
            - "is_locked" : 起点是否已锁定（距右边缘 > edge_width）
            - "edge_distance" : 起点距右边缘的 bar 数
    """
    n = len(sig_t)
    if n < 1:
        return None
    current_idx = n - 1

    if not merged or sig_t[current_idx] == 0:
        return None

    last_seg = merged[-1]
    start_idx = int(last_seg[0])
    direction = int(last_seg[2])
    edge_distance = current_idx - start_idx

    return {
        "start": start_idx,
        "current_idx": current_idx,
        "direction": direction,
        "is_locked": edge_distance > edge_width,
        "edge_distance": edge_distance,
    }


def _get_higher_tf_direction(
    higher_sig: Optional[np.ndarray],
    edge_width: int = 3,
) -> int:
    """获取高周期（C）当前方向 — 最右侧非零 sign。

    从右向左扫描 higher_sig，返回第一个非零值。
    与 _find_current_half_pair 不同，此函数不要求半边锁定，
    只要存在非零 signal 即返回其方向。

    Parameters
    ----------
    higher_sig : Optional[np.ndarray]
        C 周期的 Schmitt 信号序列。None 表示 C 不可用。
    edge_width : int, optional
        保留参数，未使用（兼容接口），默认 3。

    Returns
    -------
    int
        +1（多）, -1（空）, 0（C 不可用或全零）。
    """
    if higher_sig is None or len(higher_sig) == 0:
        return 0
    for i in range(len(higher_sig) - 1, -1, -1):
        if higher_sig[i] != 0:
            return int(higher_sig[i])
    return 0


def _fit_parabolic(x: np.ndarray, y: np.ndarray, start: int, end: int) -> Optional[Dict[str, Any]]:
    """对 y[start:end+1] 做二次多项式拟合。

    Parameters
    ----------
    x : np.ndarray
        时间索引序列。
    y : np.ndarray
        原始值序列（如滤波价格）。
    start : int
        拟合起始索引。
    end : int
        拟合结束索引（含）。

    Returns
    -------
    Optional[Dict[str, Any]]
        包含二次系数 "a"、"b"、"c" 及拟合值 "y_fit" 的字典。
        数据点少于 3 个时返回 None。
    """
    x_seg = x[start:end + 1]
    y_seg = y[start:end + 1]
    if len(x_seg) < 3:
        return None
    coeffs = np.polyfit(x_seg, y_seg, 2)
    y_fit = np.polyval(coeffs, x_seg)
    return {"a": coeffs[0], "b": coeffs[1], "c": coeffs[2], "y_fit": y_fit}


def _fit_physics_parabola(x: np.ndarray, y: np.ndarray, start: int, end: int) -> Optional[Dict[str, Any]]:
    """抛物线拟合 — 锚定对终点为顶点，y = a·(x-x₀)² + y₀。

    顶点 (x₀,y₀) = (x[end], y[end]) 固定，仅拟合曲率 a。
    预测段 = 抛物线右半（与左半对称）。

    Parameters
    ----------
    x : np.ndarray
        时间索引序列。
    y : np.ndarray
        原始值序列（如滤波价格）。
    start : int
        拟合起始索引。
    end : int
        拟合结束索引（含），同时也是抛物线顶点位置。

    Returns
    -------
    Optional[Dict[str, Any]]
        包含曲率 "a"、系数 "b"(恒为0)、顶点"c"(y₀)、
        拟合值 "y_fit"、顶点索引 "x0" 的字典。
        数据不足或分母太小无法求解时返回 None。
    """
    x_seg = x[start:end + 1]
    y_seg = y[start:end + 1]
    if len(x_seg) < 3:
        return None
    x0 = x_seg[-1]   # 顶点 x（对终点）
    y0 = y_seg[-1]   # 顶点 y（实际滤波价，固定）
    dt = x_seg - x0  # ≤0（左半段）
    dt_sq = dt ** 2
    dy = y_seg - y0
    denom = np.sum(dt_sq ** 2)
    if denom < 1e-12:
        return None
    a = np.sum(dt_sq * dy) / denom  # 最小二乘求曲率
    y_fit = y0 + a * dt_sq
    return {"a": a, "b": 0.0, "c": y0, "y_fit": y_fit, "x0": x0}


def _run_half_pair_strategy(
    t: np.ndarray,
    filtered: np.ndarray,
    sig_t: np.ndarray,
    half_pair: Optional[Dict[str, int]],
    higher_dir: int,
    pred_pairs: List[Dict[str, Any]],
    params: Optional[Dict[str, Any]] = None,
    n_extend: int = 10,
) -> List[Dict[str, Any]]:
    """执行半边多空对策略：入场判定 + 离场扫描。

    仅当 half_pair 非 None 且方向与 higher_dir 同向时才入场。
    离场按优先级：① sig_t 连续反向（take_profit）> ② 趋势偏离（trend_deviation）。

    Parameters
    ----------
    t : np.ndarray
        时间索引。
    filtered : np.ndarray
        滤波价格。
    sig_t : np.ndarray
        B 周期 Schmitt 信号。
    half_pair : Optional[Dict[str, int]]
        _find_current_half_pair 返回值（B 周期）。None 表示无活跃半边。
    higher_dir : int
        _get_higher_tf_direction 返回值（C 周期方向）。
    pred_pairs : List[Dict[str, Any]]
        预测曲线列表（含 fit_result/pair_end）。
    params : Optional[Dict[str, Any]], optional
        策略参数字典，支持的 key:
        - "N_confirm" : int, 反向连续 bar 数才确认反转，默认 2
        - "MAX_DEV_PCT" : float, 趋势偏离阈值 (%)，默认 4.0
        - "enable_gating" : bool, 是否启用 C 周期门控，默认 True
        为 None 时使用默认值。
    n_extend : int, optional
        预测延伸点数（预留，默认 10）。

    Returns
    -------
    List[Dict[str, Any]]
        交易信号列表，每项：
        - "entry_idx" : int, 入场 bar 索引
        - "exit_idx" : int, 离场 bar 索引
        - "direction" : int, +1 做多 / -1 做空
        - "entry_price" : float, 入场价格
        - "exit_price" : float, 离场价格
        - "return_pct" : float, 收益率（百分比数值）
        - "exit_reason" : str, "take_profit"|"trend_deviation"|"eod"
        无有效入场机会时返回空列表。
    """
    if half_pair is None:
        return []

    if params is None:
        params = {"N_confirm": 2, "MAX_DEV_PCT": 4.0, "enable_gating": True}

    direction = half_pair["direction"]
    if direction == 0:
        return []  # 无方向半边不产生交易信号
    entry_idx = half_pair["start"]
    n = len(t)

    if entry_idx < 0 or entry_idx >= n:
        return []

    entry_price = filtered[entry_idx]
    if np.isnan(entry_price) or entry_price <= 0:
        return []

    # C 周期方向门控
    enable_gating = params.get("enable_gating", True)
    if enable_gating and higher_dir != direction:
        return []

    # ── 离场扫描 ──
    N_confirm = params.get("N_confirm", 2)
    MAX_DEV_PCT = params.get("MAX_DEV_PCT", 4.0)
    is_long = direction == 1
    opposite_dir = -direction

    # 取最新的预测曲线用于偏离检查
    fit_result = None
    if pred_pairs:
        latest_pred = pred_pairs[-1]
        fit_result = latest_pred.get("fit_result")
        if fit_result is not None:
            a_val = fit_result.get("a")
            b_val = fit_result.get("b", 0.0)
            c_val = fit_result.get("c")
            x0 = fit_result.get("x0")

    exit_idx = None
    exit_reason = "eod"
    opposite_count = 0

    for i in range(entry_idx + 1, n):
        cur_price = filtered[i]
        if np.isnan(cur_price) or cur_price <= 0:
            continue

        # Exit ①: sig_t 连续反向 >= N_confirm
        if sig_t[i] == opposite_dir:
            opposite_count += 1
            if opposite_count >= N_confirm:
                exit_idx = i
                exit_reason = "take_profit"
                break
        else:
            opposite_count = 0

        # Exit ②: 趋势偏离（不利方向 > MAX_DEV_PCT）
        if fit_result is not None:
            if x0 is not None:
                pred_val = np.polyval((a_val, b_val, c_val), i - x0)
            else:
                pred_val = np.polyval((a_val, b_val, c_val), i)

            if not (np.isnan(pred_val) or pred_val <= 0):
                if is_long:
                    trend_broken = cur_price < pred_val * (1 - MAX_DEV_PCT / 100.0)
                else:
                    trend_broken = cur_price > pred_val * (1 + MAX_DEV_PCT / 100.0)

                if trend_broken:
                    exit_idx = i
                    exit_reason = "trend_deviation"
                    break

    if exit_idx is None:
        exit_idx = n - 1

    exit_price = filtered[exit_idx]
    if np.isnan(exit_price) or exit_price <= 0:
        return []

    if is_long:
        trade_return = (exit_price - entry_price) / entry_price
    else:
        trade_return = (entry_price - exit_price) / entry_price

    return [{
        "entry_idx": int(entry_idx),
        "exit_idx": int(exit_idx),
        "direction": direction,
        "entry_price": float(entry_price),
        "exit_price": float(exit_price),
        "return_pct": float(trade_return * 100),
        "exit_reason": exit_reason,
    }]


def _compute_strategy_pnl(
    t: np.ndarray, filtered: np.ndarray, sig_t: np.ndarray,
    all_pairs: List[Tuple[int, int]], pred_pairs: List[Dict[str, Any]],
    stop_loss_pct: float, n_extend: int = 10,
    strategy_mode: Optional[str] = None,
    trade_signals: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[np.ndarray, np.ndarray, List[Dict[str, Any]]]:
    """基于施密特触发器信号和预测曲线方向计算策略PnL。

    返回两条独立曲线：
    - long_pnl: 做多收益曲线，非持仓期水平直线
    - short_pnl: 做空收益曲线，非持仓期水平直线

    Parameters
    ----------
    t : np.ndarray
        时间索引。
    filtered : np.ndarray
        滤波价格。
    sig_t : np.ndarray
        施密特信号（+1=多, -1=空, 0=观望）。
    all_pairs : List[Tuple[int, int]]
        多空切换对列表 [(start, end), ...]。
    pred_pairs : List[Dict[str, Any]]
        预测曲线数据列表，每项包含 "pair_end" 和 "fit_result"。
    stop_loss_pct : float
        止损阈值百分比（如 2.0 表示 2%）。
    n_extend : int, optional
        预测延伸点数（默认 10）。
    strategy_mode : Optional[str], optional
        None=现有逻辑（向后兼容），"half_pair"=半边策略模式，默认 None。
    trade_signals : Optional[List[Dict[str, Any]]], optional
        strategy_mode="half_pair" 时传入 _run_half_pair_strategy 的返回值。
        为 None 或空列表时退化为空结果。

    Returns
    -------
    long_pnl : np.ndarray
        做多 PnL 曲线，长度 len(t)，初始值 100。
    short_pnl : np.ndarray
        做空 PnL 曲线，长度 len(t)，初始值 100。
    trade_records : List[Dict[str, Any]]
        交易记录列表，每项包含 id/type/entry_idx/exit_idx/
        entry_price/exit_price/return_pct/exit_reason。
    """
    n = len(t)

    # 初始化两条独立曲线
    long_pnl = np.full(n, 100.0)
    short_pnl = np.full(n, 100.0)

    long_capital = 100.0   # 做多已实现本金
    short_capital = 100.0  # 做空已实现本金

    trade_records = []
    trade_id = 0

    # ── 半边策略模式 ──
    if strategy_mode == "half_pair":
        if trade_signals:
            for sig in trade_signals:
                direction = sig["direction"]
                entry_idx = sig["entry_idx"]
                exit_idx = sig["exit_idx"]
                exit_reason = sig.get("exit_reason", "eod")

                if entry_idx < 0 or exit_idx >= n or exit_idx < entry_idx:
                    continue
                entry_price = filtered[entry_idx]
                exit_price = filtered[exit_idx]
                if np.isnan(entry_price) or entry_price <= 0 or np.isnan(exit_price) or exit_price <= 0:
                    continue

                is_long = direction == 1

                if is_long:
                    trade_return = (exit_price - entry_price) / entry_price
                else:
                    trade_return = (entry_price - exit_price) / entry_price

                trade_id += 1

                if is_long:
                    for i in range(entry_idx, exit_idx + 1):
                        cur_p = filtered[i]
                        if np.isnan(cur_p) or cur_p <= 0:
                            continue
                        unrealized = (cur_p - entry_price) / entry_price
                        long_pnl[i] = long_capital * (1 + unrealized)
                    long_capital *= (1 + trade_return)
                    for i in range(exit_idx + 1, n):
                        long_pnl[i] = long_capital
                else:
                    for i in range(entry_idx, exit_idx + 1):
                        cur_p = filtered[i]
                        if np.isnan(cur_p) or cur_p <= 0:
                            continue
                        unrealized = (entry_price - cur_p) / entry_price
                        short_pnl[i] = short_capital * (1 + unrealized)
                    short_capital *= (1 + trade_return)
                    for i in range(exit_idx + 1, n):
                        short_pnl[i] = short_capital

                trade_records.append({
                    "id": trade_id,
                    "type": "long" if is_long else "short",
                    "entry_idx": int(entry_idx),
                    "exit_idx": int(exit_idx),
                    "entry_price": float(entry_price),
                    "exit_price": float(exit_price),
                    "return_pct": float(trade_return * 100),
                    "exit_reason": exit_reason,
                })

            # 前向填充：非持仓期维持上一个值不变
            last_val = 100.0
            for i in range(n):
                if long_pnl[i] == 100.0 and i > 0 and last_val != 100.0:
                    long_pnl[i] = last_val
                if long_pnl[i] != 100.0 or (i == 0):
                    last_val = long_pnl[i]
            last_val = 100.0
            for i in range(n):
                if short_pnl[i] == 100.0 and i > 0 and last_val != 100.0:
                    short_pnl[i] = last_val
                if short_pnl[i] != 100.0 or (i == 0):
                    last_val = short_pnl[i]

            return long_pnl, short_pnl, trade_records

        return long_pnl, short_pnl, []

    # 空数据保护
    if len(all_pairs) == 0 or len(pred_pairs) == 0:
        return long_pnl, short_pnl, []

    # 建立 pred_pairs 索引: pair_end → pred_data
    pred_map = {}
    for pp in pred_pairs:
        pred_map[pp["pair_end"]] = pp

    for pair_start, pair_end in all_pairs:
        if pair_end not in pred_map:
            continue
        pp = pred_map[pair_end]
        fit_result = pp["fit_result"]

        # ---- 计算外推预测方向 ----
        a, b, c = fit_result["a"], fit_result["b"], fit_result["c"]
        x0 = fit_result.get("x0", None)

        x_pred = np.arange(pair_end, pair_end + n_extend)
        if x0 is not None:
            y_pred = np.polyval((a, b, c), x_pred - x0)
        else:
            y_pred = np.polyval((a, b, c), x_pred)

        if len(y_pred) < 2:
            continue
        pred_up = y_pred[-1] > y_pred[0]

        # ---- 判断交易方向 ----
        v2 = sig_t[pair_end]
        is_long = (v2 == 1 and pred_up)
        is_short = (v2 == -1 and not pred_up)

        if not is_long and not is_short:
            continue

        # ---- 入场 ----
        entry_idx = pair_end
        entry_price = filtered[entry_idx]
        if np.isnan(entry_price) or entry_price <= 0:
            continue

        # ---- 扫描：分段混合（方案D） ----
        # 预测保护期 [entry+1, entry+N_ext]：止损 + 止盈 双重保护
        # 趋势跟踪期 [entry+N_ext+1, ...]：仅 Sig 止盈，让利润奔跑
        exit_idx = None
        exit_reason = "take_profit"  # 默认止盈（趋势跟踪期触发或被max_hold截断）
        protect_end = entry_idx + n_extend  # 预测保护期终点
        scan_end = n - 1  # 默认扫描到数据末尾

        for i in range(entry_idx + 1, scan_end + 1):
            cur_price = filtered[i]
            if np.isnan(cur_price) or cur_price <= 0:
                continue

            # 预测保护期内：检查止损
            if i <= protect_end:
                if x0 is not None:
                    pred_val = np.polyval((a, b, c), i - x0)
                else:
                    pred_val = np.polyval((a, b, c), i)

                if not (np.isnan(pred_val) or pred_val <= 0):
                    if is_long:
                        stop_hit = cur_price < pred_val * (1 - stop_loss_pct / 100.0)
                    else:
                        stop_hit = cur_price > pred_val * (1 + stop_loss_pct / 100.0)

                    if stop_hit:
                        exit_idx = i
                        exit_reason = "stop_loss"
                        break

            # 全程：检查止盈（Sig 反转）
            if is_long and sig_t[i] == -1:
                exit_idx = i
                exit_reason = "take_profit"
                break
            if is_short and sig_t[i] == 1:
                exit_idx = i
                exit_reason = "take_profit"
                break

        # 未触发任何离场条件 → 持有到数据末尾
        if exit_idx is None:
            if n - 1 > entry_idx:
                exit_idx = n - 1
                exit_reason = "eod"  # end of data
            else:
                continue

        exit_price = filtered[exit_idx]
        if np.isnan(exit_price) or exit_price <= 0:
            continue

        # ---- 计算收益率 ----
        if is_long:
            trade_return = (exit_price - entry_price) / entry_price
        else:
            trade_return = (entry_price - exit_price) / entry_price

        trade_id += 1

        # ---- 填充持仓期间的PnL曲线 ----
        if is_long:
            # 做多：持仓期间曲线随价格变动
            for i in range(entry_idx, exit_idx + 1):
                cur_p = filtered[i]
                if np.isnan(cur_p) or cur_p <= 0:
                    continue
                unrealized = (cur_p - entry_price) / entry_price
                long_pnl[i] = long_capital * (1 + unrealized)
            # 更新做多已实现本金
            long_capital *= (1 + trade_return)
            # 离场后到数据末尾先填充已实现值（后续交易会覆盖）
            for i in range(exit_idx + 1, n):
                long_pnl[i] = long_capital
        else:
            # 做空：持仓期间曲线随价格变动
            for i in range(entry_idx, exit_idx + 1):
                cur_p = filtered[i]
                if np.isnan(cur_p) or cur_p <= 0:
                    continue
                unrealized = (entry_price - cur_p) / entry_price
                short_pnl[i] = short_capital * (1 + unrealized)
            # 更新做空已实现本金
            short_capital *= (1 + trade_return)
            # 离场后填充已实现值
            for i in range(exit_idx + 1, n):
                short_pnl[i] = short_capital

        # ---- 记录交易 ----
        trade_records.append({
            "id": trade_id,
            "type": "long" if is_long else "short",
            "entry_idx": int(entry_idx),
            "exit_idx": int(exit_idx),
            "entry_price": float(entry_price),
            "exit_price": float(exit_price),
            "return_pct": float(trade_return * 100),
            "exit_reason": exit_reason,
        })

    # ---- 前向填充：非持仓期维持上一个值不变（水平直线） ----
    # 做多曲线
    last_val = 100.0
    for i in range(n):
        if long_pnl[i] == 100.0 and i > 0 and last_val != 100.0:
            long_pnl[i] = last_val
        if long_pnl[i] != 100.0 or (i == 0):
            last_val = long_pnl[i]

    # 做空曲线
    last_val = 100.0
    for i in range(n):
        if short_pnl[i] == 100.0 and i > 0 and last_val != 100.0:
            short_pnl[i] = last_val
        if short_pnl[i] != 100.0 or (i == 0):
            last_val = short_pnl[i]

    return long_pnl, short_pnl, trade_records


# ---------------------------------------------------------------------------
# Cross-period PnL alignment helpers
# ---------------------------------------------------------------------------

def _align_pnl_to_current_tf(
    higher_dates: pd.DatetimeIndex, higher_pnl_long: np.ndarray,
    higher_pnl_short: np.ndarray, higher_trades: List[Dict[str, Any]],
    current_dates: pd.DatetimeIndex,
) -> Dict[str, Any]:
    """将高周期PnL数据按时间戳前向填充对齐到当前周期时间轴。

    Parameters
    ----------
    higher_dates : pd.DatetimeIndex
        高周期的日期索引。
    higher_pnl_long : np.ndarray
        高周期做多 PnL 曲线。
    higher_pnl_short : np.ndarray
        高周期做空 PnL 曲线。
    higher_trades : List[Dict[str, Any]]
        高周期交易记录列表，每项含 entry_idx/exit_idx/return_pct/type/exit_reason。
    current_dates : pd.DatetimeIndex
        当前周期的日期索引。

    Returns
    -------
    Dict[str, Any]
        包含以下键的字典：
        - "aligned_long" : np.ndarray, 对齐后的做多 PnL 曲线
        - "aligned_short" : np.ndarray, 对齐后的做空 PnL 曲线
        - "entry_markers" : list[(bar_idx, trade_type, pnl_val)]
        - "exit_markers" : list[(bar_idx, trade_type, pnl_val, return_pct, exit_reason)]
    """
    n = len(current_dates)
    aligned_long = np.full(n, np.nan)
    aligned_short = np.full(n, np.nan)
    entry_markers = []
    exit_markers = []

    if higher_dates is None or len(higher_dates) == 0:
        return {"aligned_long": aligned_long, "aligned_short": aligned_short,
                "entry_markers": entry_markers, "exit_markers": exit_markers}

    # 统一时区：日内数据带时区(HKT)，日线/周线无时区
    # np.datetime64 无法直接比较 tz-aware 和 tz-naive，需先归一化
    def _normalize_dates(dates: pd.DatetimeIndex) -> np.ndarray:
        """去掉时区信息，按各自字面值比较（日期级对齐）。

        Parameters
        ----------
        dates : pd.DatetimeIndex
            输入日期索引，可能带时区 (tz-aware) 或不带时区 (tz-naive)。

        Returns
        -------
        np.ndarray
            去掉时区后的 datetime64[ns] 数组。
        """
        result = pd.DatetimeIndex(dates)
        if result.tz is not None:
            # tz-aware → 保持本地时间字面值，去掉时区标记
            result = result.tz_localize(None)
        return np.array(result, dtype="datetime64[ns]")

    hd = _normalize_dates(higher_dates)
    cd = _normalize_dates(current_dates)

    # 对当前周期的每个bar，找 ≤ 该时间戳的最近高周期bar（前向填充）
    for i in range(n):
        mask = hd <= cd[i]
        if not mask.any():
            continue
        j = int(np.argmax(mask))  # 最后一个True的位置...
        # argmax on boolean array returns first True. We want last True.
        j = np.max(np.where(mask)[0])
        aligned_long[i] = higher_pnl_long[j]
        aligned_short[i] = higher_pnl_short[j]

    # 映射交易事件到当前周期bar index
    for trade in higher_trades:
        entry_j = trade["entry_idx"]
        exit_j = trade["exit_idx"]
        if entry_j >= len(hd) or exit_j >= len(hd):
            continue
        entry_time = hd[entry_j]
        exit_time = hd[exit_j]

        # 找到当前周期中 ≤ entry_time 的最近bar
        entry_mask = cd <= entry_time
        if entry_mask.any():
            entry_bar = int(np.max(np.where(entry_mask)[0]))
            pnl_at_entry = aligned_long[entry_bar] if trade["type"] == "long" else aligned_short[entry_bar]
            entry_markers.append((entry_bar, trade["type"], pnl_at_entry if not np.isnan(pnl_at_entry) else 100.0))

        # 离场：≤ exit_time 的最近bar
        exit_mask = cd <= exit_time
        if exit_mask.any():
            exit_bar = int(np.max(np.where(exit_mask)[0]))
            pnl_at_exit = aligned_long[exit_bar] if trade["type"] == "long" else aligned_short[exit_bar]
            exit_markers.append((exit_bar, trade["type"],
                                 pnl_at_exit if not np.isnan(pnl_at_exit) else 100.0,
                                 trade.get("return_pct", 0.0),
                                 trade.get("exit_reason", "")))

    return {
        "aligned_long": aligned_long,
        "aligned_short": aligned_short,
        "entry_markers": entry_markers,
        "exit_markers": exit_markers,
    }


def _compute_holding_masks(
    n_bars: int, entry_markers: List[Tuple[int, str, float]],
    exit_markers: List[Tuple[int, str, float, float, str]],
) -> Tuple[np.ndarray, np.ndarray]:
    """从高周期入场/离场marker计算持仓区间掩码。

    Parameters
    ----------
    n_bars : int
        当前周期的 bar 数量。
    entry_markers : List[Tuple[int, str, float]]
        入场标记列表：[(bar_idx, trade_type, pnl_val), ...]。
    exit_markers : List[Tuple[int, str, float, float, str]]
        离场标记列表：[(bar_idx, trade_type, pnl_val, return_pct, exit_reason), ...]。

    Returns
    -------
    long_mask : np.ndarray
        bool 数组，标记高周期做多持仓区间。
    short_mask : np.ndarray
        bool 数组，标记高周期做空持仓区间。
    """
    long_mask = np.zeros(n_bars, dtype=bool)
    short_mask = np.zeros(n_bars, dtype=bool)

    # 按类型分组，按bar_idx排序
    long_entries = sorted([b for b, t, _ in entry_markers if t == "long"])
    short_entries = sorted([b for b, t, _ in entry_markers if t == "short"])
    long_exits = sorted([b for b, t, _, _, _ in exit_markers if t == "long"])
    short_exits = sorted([b for b, t, _, _, _ in exit_markers if t == "short"])

    # 配对：每个entry找下一个同类型exit（时间上最近的）
    for e_bar in long_entries:
        # 找 > e_bar 的第一个exit
        later_exits = [x for x in long_exits if x > e_bar]
        x_bar = later_exits[0] if later_exits else n_bars - 1
        if e_bar < n_bars:
            long_mask[e_bar:min(x_bar + 1, n_bars)] = True

    for e_bar in short_entries:
        later_exits = [x for x in short_exits if x > e_bar]
        x_bar = later_exits[0] if later_exits else n_bars - 1
        if e_bar < n_bars:
            short_mask[e_bar:min(x_bar + 1, n_bars)] = True

    return long_mask, short_mask
