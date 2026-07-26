"""
Cross-period PnL alignment and filter quality metrics.

Contains:
- Cross-period PnL time-alignment (higher timeframe -> current timeframe)
- Filter quality metrics computation (MSE, RMSE, MAE, SNR, lag, roughness)
"""

import numpy as np
import pandas as pd
from typing import Any, Dict, List


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
            # tz-aware -> 保持本地时间字面值，去掉时区标记
            result = result.tz_localize(None)
        return np.array(result, dtype="datetime64[ns]")

    hd = _normalize_dates(higher_dates)
    cd = _normalize_dates(current_dates)

    # 对当前周期的每个bar，找 <= 该时间戳的最近高周期bar（前向填充）
    # P0-1: np.searchsorted 替代 O(n*m) 逐 bar 布尔扫描
    j_indices = np.searchsorted(hd, cd, side="right") - 1
    valid = (j_indices >= 0) & (j_indices < len(hd))
    aligned_long[valid] = higher_pnl_long[j_indices[valid]]
    aligned_short[valid] = higher_pnl_short[j_indices[valid]]

    # 映射交易事件到当前周期bar index
    for trade in higher_trades:
        entry_j = trade["entry_idx"]
        exit_j = trade["exit_idx"]
        if entry_j >= len(hd) or exit_j >= len(hd):
            continue
        entry_time = hd[entry_j]
        exit_time = hd[exit_j]

        # 找到当前周期中 <= entry_time 的最近bar
        # 若开仓在当前窗口起点之前，但仓位延续进窗口(exit >= 窗口起点)，则从
        # 窗口起点(bar0)开始显示，把当前周期起始点包含在内(与 eod 右延续镜像)。
        # P0-2: np.searchsorted 替代 np.where 布尔扫描
        entry_bar = int(np.searchsorted(cd, entry_time, side="right")) - 1
        if entry_bar < 0:
            if n > 0 and exit_time >= cd[0]:
                entry_bar = 0
            else:
                entry_bar = None
        if entry_bar is not None:
            pnl_at_entry = aligned_long[entry_bar] if trade["type"] == "long" else aligned_short[entry_bar]
            entry_markers.append((entry_bar, trade["type"], pnl_at_entry if not np.isnan(pnl_at_entry) else 100.0))

        # 离场：<= exit_time 的最近bar
        # eod = 高周期该仓位未真正结束(跑到数据末端被强制平仓)，低周期应延续到
        #       最新bar(右边缘)，而非停在高周期末bar对应的较早位置(半边多空对)。
        # P0-2: np.searchsorted 替代 np.where 布尔扫描
        exit_reason = trade.get("exit_reason", "")
        if exit_reason == "eod":
            exit_bar = n - 1
        else:
            exit_bar = int(np.searchsorted(cd, exit_time, side="right")) - 1
            if exit_bar < 0:
                exit_bar = None
        if exit_bar is not None:
            pnl_at_exit = aligned_long[exit_bar] if trade["type"] == "long" else aligned_short[exit_bar]
            exit_markers.append((exit_bar, trade["type"],
                                 pnl_at_exit if not np.isnan(pnl_at_exit) else 100.0,
                                 trade.get("return_pct", 0.0),
                                 exit_reason))

    return {
        "aligned_long": aligned_long,
        "aligned_short": aligned_short,
        "entry_markers": entry_markers,
        "exit_markers": exit_markers,
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
