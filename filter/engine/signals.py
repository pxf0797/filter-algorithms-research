"""
BS 仓位操作标识计算模块

为指定周期计算 K 线图上的 B（做多入场/平空出场）/ S（做空入场/平多出场）标记。
支持从操作周期向低一级周期的级联传播。

标记规则（颜色跟随持仓方向）：
  做多入场 — 绿 B    做空入场 — 红 S
  平多出场 — 绿 S    平空出场 — 红 B
"""

from typing import Optional

import pandas as pd

from filter.shared.constants import TF_LOWER


def _find_date_index(dates, target_date) -> Optional[int]:
    """在 dates 数组中查找第一个 >= target_date 的 bar 索引。

    Parameters
    ----------
    dates : pd.DatetimeIndex or None
    target_date : pd.Timestamp or str or None

    Returns
    -------
    int or None
        0 if target_date is before all dates (start from available data).
        None if target_date is after all dates (cannot cascade, skip marker).
        Index of first bar with date >= target_date otherwise.
    """
    if dates is None or len(dates) == 0 or target_date is None:
        return None
    try:
        target_ts = pd.Timestamp(target_date)
        # Normalize timezone: strip tz to avoid TypeError when comparing
        # tz-naive (日线/周线/月线) with tz-aware (分钟线 from HK exchange)
        if target_ts.tz is not None:
            target_ts = target_ts.tz_localize(None)
        # Convert to DatetimeIndex and normalize tz
        dates_idx = pd.DatetimeIndex(dates)
        if dates_idx.tz is not None:
            dates_idx = dates_idx.tz_localize(None)
        first_date = dates_idx[0]
        last_date = dates_idx[-1]
        if target_ts < first_date:
            return 0
        if target_ts > last_date:
            return None
        for i, d in enumerate(dates_idx):
            if d >= target_ts:
                return i
        return None
    except Exception:
        return None


def _compute_own_from_trades(t, dates, trade_records) -> dict:
    """从本周期 trade_records 生成 BS 标记（回退：顶周期无更高参考时使用）。

    trade_records 非空时从中生成标记（入场=entry_idx, 出场=exit_idx）。
    """
    entry = []
    exit_ = []
    n_dates = len(dates) if dates is not None else 0

    for trade in trade_records:
        is_long = trade["type"] == "long"
        entry_idx = trade["entry_idx"]
        exit_idx = trade["exit_idx"]
        exit_reason = trade.get("exit_reason", "")

        d_entry = dates[entry_idx] if entry_idx < n_dates else None
        d_exit = dates[exit_idx] if exit_idx < n_dates else None

        if is_long:
            # 做多: B(绿)入场, S(绿)出场
            entry.append((int(entry_idx), "B", "green", d_entry))
            if exit_reason != "eod":
                exit_.append((int(exit_idx), "S", "green", exit_reason, d_exit))
        else:
            # 做空: S(红)入场, B(红)出场
            entry.append((int(entry_idx), "S", "red", d_entry))
            if exit_reason != "eod":
                exit_.append((int(exit_idx), "B", "red", exit_reason, d_exit))

    return {"entry_markers": entry, "exit_markers": exit_}


def _compute_from_trades_filtered(t, dates, trade_records, holding_masks) -> dict:
    """从 trade_records 生成 BS 标记，经 holding_masks 过滤。

    只有 entry_idx 落在对应方向 mask 内的交易才标 BS。
    这是操作周期的主路径（同向性判断过滤后的 PnL）。

    Parameters
    ----------
    holding_masks : (long_mask, short_mask) — np.ndarray[bool] 各长度=len(t)
    """
    entry = []
    exit_ = []
    n_dates = len(dates) if dates is not None else 0
    long_mask, short_mask = holding_masks

    for trade in trade_records:
        is_long = trade["type"] == "long"
        entry_idx = trade["entry_idx"]
        exit_idx = trade["exit_idx"]
        exit_reason = trade.get("exit_reason", "")

        mask = long_mask if is_long else short_mask

        # 入场：entry_idx 必须在 mask 内
        if entry_idx >= len(mask) or not mask[entry_idx]:
            continue

        d_entry = dates[entry_idx] if entry_idx < n_dates else None
        d_exit = dates[exit_idx] if exit_idx < n_dates else None

        if is_long:
            entry.append((int(entry_idx), "B", "green", d_entry))
            if exit_reason != "eod":
                exit_.append((int(exit_idx), "S", "green", exit_reason, d_exit))
        else:
            entry.append((int(entry_idx), "S", "red", d_entry))
            if exit_reason != "eod":
                exit_.append((int(exit_idx), "B", "red", exit_reason, d_exit))

    return {"entry_markers": entry, "exit_markers": exit_}


def _compute_from_pairs(t, dates, schmitt, all_pairs) -> dict:
    """从 Schmitt 信号对直接生成 BS 标记（无 trade_records 时的回退路径）。

    根据 ``sig_t[pair_start]`` 方向决定标记类型和颜色：
    - sig=+1（做多）→ B(绿)入场, S(绿)出场
    - sig=-1（做空）→ S(红)入场, B(红)出场

    最后一段信号（pair_end == len(t)-1）不出场标记（等价于 eod 延续）。
    """
    entry = []
    exit_ = []
    n = len(t)
    n_dates = len(dates) if dates is not None else 0
    sig_t = schmitt.get("sig") if schmitt is not None else None
    if sig_t is None or len(all_pairs) == 0:
        return {"entry_markers": [], "exit_markers": []}

    for pair_start, pair_end in all_pairs:
        direction = sig_t[pair_start]
        if direction not in (1, -1):
            continue

        is_long = direction == 1
        d_entry = dates[pair_start] if pair_start < n_dates else None

        if is_long:
            entry.append((int(pair_start), "B", "green", d_entry))
        else:
            entry.append((int(pair_start), "S", "red", d_entry))

        # 最后一段信号（延伸到数据末尾）不出场标记
        if pair_end >= n - 1:
            continue

        d_exit = dates[pair_end] if pair_end < n_dates else None
        if is_long:
            exit_.append((int(pair_end), "S", "green", d_exit))
        else:
            exit_.append((int(pair_end), "B", "red", d_exit))

    return {"entry_markers": entry, "exit_markers": exit_}


def compute_bs_markers(t, dates, schmitt, all_pairs, trade_records,
                        tf, operating_tf, higher_bs=None,
                        holding_masks=None) -> dict:
    """为单个视图计算 BS 标记。

    统一逻辑：所有周期使用相同的过滤逻辑。
    有 holding_masks + trade_records → 过滤后的标记
    仅有 trade_records → 未过滤的标记
    都没有 trade_records 但有 schmitt 信号对 → 从信号对直接生成

    Parameters
    ----------
    t : np.ndarray
        Bar 索引数组。
    dates : pd.DatetimeIndex
        Bar 日期。
    schmitt : dict or None
        Schmitt 触发器输出（含 ``sig`` 数组）。
    all_pairs : list[(int, int)]
        Schmitt 信号对列表。
    trade_records : list[dict]
        策略交易记录。
    tf : str
        当前视图的周期。
    operating_tf : str
        用户选择的操作周期。
    higher_bs : dict or None
        保留参数以兼容旧调用方（不再使用）。
    holding_masks : tuple or None
        (long_mask, short_mask) from _compute_holding_masks。
        用于过滤 trade_records，只保留 entry_idx 落在同向 mask 内的交易。

    Returns
    -------
    dict
        {"entry_markers": [...], "exit_markers": [...]}
    """
    # UNIFIED: all levels use same filtered-trades logic
    if holding_masks is not None and trade_records:
        return _compute_from_trades_filtered(t, dates, trade_records, holding_masks)
    elif trade_records:
        return _compute_own_from_trades(t, dates, trade_records)
    elif schmitt is not None and all_pairs:
        # 回退：无 trade_records 时从 schmitt 信号对直接生成 BS 标记
        # 确保 BS 标注不依赖 show_strategy 开关
        return _compute_from_pairs(t, dates, schmitt, all_pairs)
    else:
        return {"entry_markers": [], "exit_markers": []}


def get_lower_tfs(operating_tf) -> list[str]:
    """获取操作周期以下的所有周期（级联链）。

    Parameters
    ----------
    operating_tf : str

    Returns
    -------
    list[str]
    """
    result = []
    tf = TF_LOWER.get(operating_tf)
    while tf is not None:
        result.append(tf)
        tf = TF_LOWER.get(tf)
    return result
