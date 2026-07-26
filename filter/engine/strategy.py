"""
Strategy PnL computation and position holding masks.

Contains:
- Strategy PnL calculation based on Schmitt trigger signals and prediction curves
- Position holding mask computation for cross-period visualization
"""

import numpy as np
import pandas as pd
from typing import Any, Dict, List, Tuple


def _compute_strategy_pnl(
    t: np.ndarray, filtered: np.ndarray, sig_t: np.ndarray,
    all_pairs: List[Tuple[int, int]], pred_pairs: List[Dict[str, Any]],
    stop_loss_pct: float, n_extend: int = 10,
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

    # 空数据保护
    if len(all_pairs) == 0 or len(pred_pairs) == 0:
        return long_pnl, short_pnl, []

    # 建立 pred_pairs 索引: pair_end -> pred_data
    pred_map = {}
    for pp in pred_pairs:
        pred_map[pp["pair_end"]] = pp

    trade_records = []
    trade_id = 0
    last_long_exit = -1
    last_short_exit = -1

    for pair_start, pair_end in all_pairs:
        # ---- 判断交易方向（仅凭 Sig 反转方向；入场不再依赖抛物线预测）----
        # P0 fix: 入场索引改为 pair_start（首个信号段的持仓也会被交易）
        v2 = sig_t[pair_start]
        is_long = (v2 == 1)
        is_short = (v2 == -1)
        if not is_long and not is_short:
            continue

        # ---- 入场 ----
        entry_idx = pair_start
        entry_price = filtered[entry_idx]
        if np.isnan(entry_price) or entry_price <= 0:
            continue

        # ---- 预测轨道（可选）：仅用于保护期止损参考；无预测则回退相对入场价固定% ----
        pp = pred_map.get(pair_end)
        a = b = c = x0 = None
        if pp is not None:
            fit_result = pp["fit_result"]
            a, b, c = fit_result["a"], fit_result["b"], fit_result["c"]
            x0 = fit_result.get("x0", None)

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
                if a is not None:
                    # 有预测：相对预测价轨道
                    pred_val = np.polyval((a, b, c), i - x0) if x0 is not None \
                        else np.polyval((a, b, c), i)
                else:
                    # 无预测：回退到相对入场价固定%
                    pred_val = entry_price

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

        # 未触发任何离场条件 -> 持有到数据末尾
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
        # P1-2: numpy向量化切片替代逐bar for循环（O(hold_bars) per trade）
        idx_slice = slice(entry_idx, exit_idx + 1)
        prices = filtered[idx_slice]
        valid = ~(np.isnan(prices) | (prices <= 0))
        if is_long:
            # 做多：持仓期间曲线随价格变动
            old_vals = long_pnl[idx_slice].copy()
            if np.any(valid):
                unrealized = np.zeros(len(prices))
                unrealized[valid] = (prices[valid] - entry_price) / entry_price
                new_vals = long_capital * (1 + unrealized)
                long_pnl[idx_slice] = np.where(valid, new_vals, old_vals)
            # 更新做多已实现本金
            long_capital *= (1 + trade_return)
            last_long_exit = exit_idx
        else:
            # 做空：持仓期间曲线随价格变动
            old_vals = short_pnl[idx_slice].copy()
            if np.any(valid):
                unrealized = np.zeros(len(prices))
                unrealized[valid] = (entry_price - prices[valid]) / entry_price
                new_vals = short_capital * (1 + unrealized)
                short_pnl[idx_slice] = np.where(valid, new_vals, old_vals)
            # 更新做空已实现本金
            short_capital *= (1 + trade_return)
            last_short_exit = exit_idx

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

    # ---- P1-2: 尾部一次性填充（替代循环内逐笔 O(n) 覆写） ----
    # 只有最后一笔交易的尾部需要保留；前续交易的尾部随后续交易持仓期间价格覆盖
    if last_long_exit >= 0 and last_long_exit + 1 < n:
        long_pnl[last_long_exit + 1:] = long_capital
    if last_short_exit >= 0 and last_short_exit + 1 < n:
        short_pnl[last_short_exit + 1:] = short_capital

    # ---- 前向填充：非持仓期维持上一个值不变（水平直线） ----
    # P1-12: vectorized ffill via pandas (C impl, 5-15x speedup)
    # Value 100.0 represents non-holding period -> replace with NaN, ffill, then fill back to 100.0
    long_series = pd.Series(long_pnl).replace(100.0, np.nan).ffill().fillna(100.0)
    long_pnl = long_series.values
    short_series = pd.Series(short_pnl).replace(100.0, np.nan).ffill().fillna(100.0)
    short_pnl = short_series.values

    return long_pnl, short_pnl, trade_records


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
