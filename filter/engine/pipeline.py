"""
统一管线计算 — Web 和 CLI 共用，零 Streamlit 依赖

提供：
- compute_filters: 主线+副线滤波
- compute_schmitt_trigger: 施密特触发器计算
- compute_prediction_pairs: 预测曲线计算
"""

import numpy as np
from typing import Optional, Tuple, Dict, Any
from filter.engine.filters import FILTERS
from filter.engine.schmitt import _schmitt_trigger, _fit_physics_parabola


def compute_filters(
    noisy: np.ndarray,
    t: np.ndarray,
    cfg: dict,
) -> Tuple[np.ndarray, Optional[np.ndarray]]:
    """计算主线滤波与可选的副线滤波。纯函数，无缓存。

    Parameters
    ----------
    noisy : np.ndarray
        原始价格序列。
    t : np.ndarray
        时间索引。
    cfg : dict
        视图配置，需含 ``_fid``, ``pv``；可选 ``_dual``, ``_fid2``, ``pv2``。

    Returns
    -------
    Tuple[np.ndarray, Optional[np.ndarray]]
        ``(filtered, filtered2)`` — 主线滤波结果与副线结果（可能为 None）。
    """
    sf = FILTERS.get(cfg["_fid"])
    if sf is None:
        return np.full_like(noisy, np.nan), None

    try:
        filtered = sf["func"](noisy, t, **cfg["pv"])
        filtered = np.asarray(filtered, dtype=float).ravel()
    except Exception:
        filtered = np.full_like(noisy, np.nan)

    filtered2: Optional[np.ndarray] = None
    if cfg.get("_dual") and cfg.get("_fid2") and cfg.get("pv2"):
        try:
            sf2 = FILTERS.get(cfg["_fid2"])
            if sf2 is not None:
                filtered2 = sf2["func"](noisy, t, **cfg["pv2"])
                filtered2 = np.asarray(filtered2, dtype=float).ravel()
        except Exception:
            filtered2 = np.full_like(noisy, np.nan)

    return filtered, filtered2


def compute_schmitt_trigger(
    filtered: np.ndarray,
    t: np.ndarray,
    cfg: dict,
    *,
    init_mu: Optional[float] = None,
    init_sigma: Optional[float] = None,
    init_state: int = 0,
    init_dur: int = 0,
) -> Optional[Dict[str, Any]]:
    """计算施密特触发器信号。纯函数。

    Parameters
    ----------
    filtered : np.ndarray
        滤波价格序列。
    t : np.ndarray
        时间索引。
    cfg : dict
        视图配置，需含 ``show_sch``, ``ew``, ``ke``, ``sm``。
    init_mu : Optional[float], optional
        跨窗口 EWMA 均值初始值（回测连续模式用）。
    init_sigma : Optional[float], optional
        跨窗口 EWMA 标准差初始值（回测连续模式用）。
    init_state : int, optional
        跨窗口施密特状态初始值（回测连续模式用，默认 0）。
    init_dur : int, optional
        跨窗口施密特持续期数初始值（回测连续模式用，默认 0）。

    Returns
    -------
    Optional[Dict[str, Any]]
        施密特触发器输出字典；数据不足时返回 None。
    """
    if not cfg.get("show_sch") or np.all(np.isnan(filtered)) or len(t) < 2:
        return None

    v = np.gradient(filtered, t)
    a = np.gradient(v, t)
    result = _schmitt_trigger(
        v, a,
        ewma_span=cfg.get("ew", 60),
        k_eps=cfg.get("ke", 0.15),
        sigma_min=cfg.get("sm", 0.05),
        init_mu=init_mu,
        init_sigma=init_sigma,
        init_state=init_state,
        init_dur=init_dur,
    )
    if result is not None:
        result["v"] = v
        result["a"] = a
    return result


def compute_prediction_pairs(
    t: np.ndarray,
    filtered: np.ndarray,
    schmitt: Optional[dict],
    cfg: dict,
    all_pairs: list,
) -> list:
    """计算每对多空信号的预测曲线。纯函数。

    Parameters
    ----------
    t : np.ndarray
        时间索引。
    filtered : np.ndarray
        滤波价格序列。
    schmitt : Optional[dict]
        施密特触发器输出。
    cfg : dict
        视图配置，需含 ``show_pred``。
    all_pairs : list
        多空切换对列表。

    Returns
    -------
    list[dict]
        预测曲线数据列表。
    """
    if not cfg.get("show_pred") or schmitt is None:
        return []

    pred_pairs = []
    for pair_start, pair_end in all_pairs:
        if pair_end - pair_start >= 3:
            fit_result = _fit_physics_parabola(t, filtered, pair_start, pair_end)
            if fit_result is not None:
                pred_pairs.append({
                    "fit_result": fit_result,
                    "fit_start": pair_start,
                    "pair_end": pair_end,
                })
    return pred_pairs
