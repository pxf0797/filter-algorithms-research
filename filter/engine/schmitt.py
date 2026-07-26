"""
Schmitt trigger computation and signal pairing functions.

Contains:
- Schmitt trigger core (numba-accelerated)
- Schmitt trigger adaptive deadband computation
- Signal pair finding
- Parabolic / physics parabola fitting for prediction curves
"""

import numpy as np
import pandas as pd
from typing import Any, Dict, List, Optional, Tuple

try:
    from numba import jit
    HAS_NUMBA = True
except ImportError:
    def jit(*args, **kwargs):
        return lambda f: f
    HAS_NUMBA = False


# ---------------------------------------------------------------------------
# Schmitt Trigger numba-accelerated core
# ---------------------------------------------------------------------------

@jit(nopython=True, cache=True)
def _schmitt_core(price: np.ndarray, upper: np.ndarray, lower: np.ndarray,
                  state: int = 0) -> np.ndarray:
    """numba-accelerated Schmitt trigger core loop.

    Classic two-threshold hysteresis state machine:
    - price > upper -> state = 1 (on)
    - price < lower -> state = 0 (off)
    - otherwise state stays unchanged.

    Parameters
    ----------
    price : np.ndarray
        Input price/signal sequence.
    upper : np.ndarray
        Upper threshold array (same length as price).
    lower : np.ndarray
        Lower threshold array (same length as price).
    state : int
        Initial state (0 or 1).

    Returns
    -------
    np.ndarray
        State sequence (0 or 1), same length as price.
    """
    out = np.empty_like(price)
    for i in range(len(price)):
        if price[i] > upper[i]:
            state = 1
        elif price[i] < lower[i]:
            state = 0
        out[i] = state
    return out


# ---------------------------------------------------------------------------
# Schmitt Trigger computation (ref: 多周期趋势策略V2_优化4 §二, chart ④⑥)
# Inputs: v (velocity = d(filtered)/dt) as momentum, a (d²/dt²) as acceleration
# ---------------------------------------------------------------------------
def _schmitt_trigger(v: np.ndarray, a: np.ndarray, ewma_span: int = 60,
                     k_eps: float = 0.15, sigma_min: float = 0.05,
                     init_mu: Optional[float] = None,
                     init_sigma: Optional[float] = None,
                     init_state: int = 0, init_dur: int = 0) -> Optional[Dict[str, Any]]:
    """Schmitt trigger: adaptive deadband on acceleration (a),
    with velocity (v) as direction constraint.

    - v -> momentum (物理意义: 趋势速度, 类比文档 x_t)
    - a -> acceleration (物理意义: 趋势加速, 类比文档 a_t)
    - epsilon_t = k_epsilon . max(sigma_t(v), sigma_min)  -- 自适应死区基于 v 的波动率
    - Sig_t: a>epsilon AND v>0 -> +1(多); a<-epsilon AND v<0 -> -1(空); else 0(观望)

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
    init_mu : Optional[float], optional
        跨窗口 EWMA 均值初始值；为 None 则用 v[0] 初始化。
    init_sigma : Optional[float], optional
        跨窗口 EWMA 标准差初始值；为 None 则用 0.0 初始化。
    init_state : int, optional
        跨窗口施密特状态初始值（默认 0）。
    init_dur : int, optional
        跨窗口施密特持续期数初始值（默认 0）。

    Returns
    -------
    Optional[Dict[str, Any]]
        包含 "mu_v"（EWMA均值）、"sigma_v"（EWMA标准差）、
        "eps"（自适应阈值）、"sig"（±1/0 信号）、"dur"（持续期数）、
        "final_mu"（末态均值）、"final_sigma"（末态标准差）、
        "final_state"（末态施密特状态）、"final_dur"（末态持续期数）的字典。
        数据不足时返回 None。
    """
    n = len(v)
    if n < ewma_span:
        return None

    # EWMA volatility of v -> sigma_t(v) -- vectorized via pandas ewm (C impl)
    # P1-11: replace for-loop with pd.Series.ewm for 5-15x speedup
    alpha = 2.0 / (ewma_span + 1)
    init_mu_val = init_mu if init_mu is not None and init_sigma is not None else v[0]
    init_sigma_val = init_sigma if init_mu is not None and init_sigma is not None else 0.0

    s = pd.Series(v)
    mu_v = s.ewm(alpha=alpha, adjust=False).mean().values
    sigma_v = np.sqrt(
        pd.Series((v - mu_v) ** 2).ewm(alpha=alpha, adjust=False).mean().values
    )
    # Override initial values to match legacy explicit init behavior
    mu_v[0] = init_mu_val
    sigma_v[0] = init_sigma_val

    # Adaptive deadband
    eps_t = k_eps * np.maximum(sigma_v, sigma_min)

    # Schmitt trigger with hysteresis
    sig_t = np.zeros(n, dtype=int)
    dur_t = np.zeros(n, dtype=int)
    current_state = init_state
    current_dur = init_dur
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
            "eps": eps_t, "sig": sig_t, "dur": dur_t,
            "final_mu": float(mu_v[-1]),
            "final_sigma": float(sigma_v[-1]),
            "final_state": int(current_state),
            "final_dur": int(current_dur)}


def _find_all_pairs(sig_t: np.ndarray) -> List[Tuple[int, int]]:
    """扫描 sig_t，找出窗口中所有多空切换对。

    规则：合并相邻同号段（+1,0,+1 -> 一个连续段），异号配对。
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
    n = len(sig_t)
    if n < 3:
        return []

    # Step 1: 收集所有非零段
    segments = []  # [(start, end, val), ...]
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

    if len(segments) == 0:
        return []

    # Step 2: 合并相邻同号段（+1,0,+1 -> 一个连续多头段）
    merged = [segments[0]]
    for seg in segments[1:]:
        last = merged[-1]
        if seg[2] == last[2]:  # 同号 -> 合并（含中间观望区）
            merged[-1] = (last[0], seg[1], seg[2])
        else:
            merged.append(seg)

    # Step 3: 相邻异号段配对 -- 结束于相反信号的入口边缘
    pairs = []
    for j in range(len(merged) - 1):
        s1, e1, v1 = merged[j]
        s2, e2, v2 = merged[j + 1]
        if v1 != v2:  # 多->空 或 空->多
            pairs.append((s1, s2))  # 结束于相反信号的入口（边缘）

    # Step 4: 最后一个信号段的配对（覆盖到数据末尾）
    # P0 fix: 首个信号段及最后一程始终生成 pair，避免信号窗口两端的持仓被丢弃
    if len(merged) >= 1:
        s_last = merged[-1][0]
        if s_last < n - 1:  # 至少有 1 根 bar 的持有空间
            pairs.append((s_last, n - 1))

    return pairs


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
    """抛物线拟合 -- 锚定对终点为顶点，y = a.(x-x0)² + y0。

    顶点 (x0,y0) = (x[end], y[end]) 固定，仅拟合曲率 a。
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
        包含曲率 "a"、系数 "b"(恒为0)、顶点"c"(y0)、
        拟合值 "y_fit"、顶点索引 "x0" 的字典。
        数据不足或分母太小无法求解时返回 None。
    """
    x_seg = x[start:end + 1]
    y_seg = y[start:end + 1]
    if len(x_seg) < 3:
        return None
    x0 = x_seg[-1]   # 顶点 x（对终点）
    y0 = y_seg[-1]   # 顶点 y（实际滤波价，固定）
    dt = x_seg - x0  # <=0（左半段）
    dt_sq = dt ** 2
    dy = y_seg - y0
    denom = np.sum(dt_sq ** 2)
    if denom < 1e-12:
        return None
    a = np.sum(dt_sq * dy) / denom  # 最小二乘求曲率
    y_fit = y0 + a * dt_sq
    return {"a": a, "b": 0.0, "c": y0, "y_fit": y_fit, "x0": x0}
