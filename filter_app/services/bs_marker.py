"""
BS 仓位操作标识计算模块

为指定周期计算 K 线图上的 B（做多入场/平空出场）/ S（做空入场/平多出场）标记。
支持从操作周期向低一级周期的级联传播。

标记规则（颜色跟随持仓方向）：
  做多入场 — 绿 B    做空入场 — 红 S
  平多出场 — 绿 S    平空出场 — 红 B
"""

import numpy as np
import pandas as pd

# 周期层级反向映射（高 → 低），用于 BS 标记级联
TF_LOWER = {
    "季线": "月线", "月线": "周线", "周线": "日线",
    "日线": "60分钟", "60分钟": "15分钟", "15分钟": "5分钟",
    "5分钟": "1分钟", "1分钟": None,
}


def _find_date_index(dates, target_date):
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


def _compute_own_markers(t, dates, schmitt, all_pairs, trade_records):
    """从策略交易记录生成 BS 标记（优先），回退到 Schmitt 原始对。"""
    entry = []
    exit_ = []
    n_dates = len(dates) if dates is not None else 0

    if trade_records:
        # 优先：从实际策略交易记录生成标记
        for trade in trade_records:
            is_long = trade["type"] == "long"
            entry_idx = trade["entry_idx"]
            exit_idx = trade["exit_idx"]
            exit_reason = trade.get("exit_reason", "")

            d_entry = dates[entry_idx] if entry_idx < n_dates else None
            d_exit = dates[exit_idx] if exit_idx < n_dates else None

            if is_long:
                # 做多: 先B(绿) 后S(绿)
                entry.append((int(entry_idx), "B", "green", d_entry))
                exit_.append((int(exit_idx), "S", "green", exit_reason, d_exit))
            else:
                # 做空: 先S(红) 后B(红)
                entry.append((int(entry_idx), "S", "red", d_entry))
                exit_.append((int(exit_idx), "B", "red", exit_reason, d_exit))

    elif schmitt is not None and all_pairs:
        # 回退：从 Schmitt 原始对生成标记（策略未启用时）
        sig = schmitt["sig"]
        for pair_start, pair_end in all_pairs:
            if pair_end >= len(sig):
                continue
            direction = sig[pair_end]
            d_entry = dates[pair_start] if pair_start < n_dates else None
            d_exit = dates[pair_end] if pair_end < n_dates else None

            if direction == 1:
                entry.append((int(pair_start), "B", "green", d_entry))
                exit_.append((int(pair_end), "S", "green", "pair_end", d_exit))
            elif direction == -1:
                entry.append((int(pair_start), "S", "red", d_entry))
                exit_.append((int(pair_end), "B", "red", "pair_end", d_exit))

    return {"entry_markers": entry, "exit_markers": exit_}


def _compute_own_markers_filtered(t, dates, schmitt, all_pairs, trade_records,
                                   lower_schmitt_data):
    """Compute BS markers for the operating TF, filtered by lower-TF
    same-direction confirmation.

    For each trade record on the operating TF:
      1. Extract direction D, entry_date, exit_date
      2. Scan lower TF's Schmitt pairs
      3. Find the first lower-TF pair where:
         a. pair_start_date >= entry_date    (confirmation after signal)
         b. pair_start_date <= exit_date     (confirmation within trade window)
         c. sig[pair_end] == D               (same direction)
      4. If confirmed → add BS entry+exit markers
      5. If not confirmed → skip this trade (no BS marker)

    Fallback: If lower_schmitt_data is None or empty → unfiltered
              (same as _compute_own_markers).

    Parameters
    ----------
    t : np.ndarray
        Bar index array.
    dates : pd.DatetimeIndex
        Bar dates.
    schmitt : dict or None
        Schmitt trigger output.
    all_pairs : list[(int, int)]
        Schmitt signal pairs.
    trade_records : list[dict]
        Strategy trade records.
    lower_schmitt_data : dict or None
        Lower TF's Schmitt data with keys "all_pairs", "sig", "dates".
        When None or empty, falls back to unfiltered markers.

    Returns
    -------
    dict
        {"entry_markers": [...], "exit_markers": [...]}
    """
    entry = []
    exit_ = []
    n_dates = len(dates) if dates is not None else 0

    if not trade_records:
        # No strategy trades → fall back to Schmitt pairs (unfiltered)
        return _compute_own_markers(t, dates, schmitt, all_pairs, trade_records)

    # ── Extract lower-TF data ──
    if lower_schmitt_data is None:
        return _compute_own_markers(t, dates, schmitt, all_pairs, trade_records)

    lower_pairs = lower_schmitt_data.get("all_pairs", [])
    lower_sig = lower_schmitt_data.get("sig", None)
    lower_dates = lower_schmitt_data.get("dates", None)

    # ── Guard: no lower-TF Schmitt data → unfiltered ──
    if lower_sig is None or lower_dates is None or len(lower_pairs) == 0:
        return _compute_own_markers(t, dates, schmitt, all_pairs, trade_records)

    # ── Normalize timezone (same approach as _find_date_index) ──
    def _normalize(d):
        ts = pd.Timestamp(d)
        if ts.tz is not None:
            ts = ts.tz_localize(None)
        return ts

    # Build tz-naive lower dates for safe comparison
    lower_dates_norm = [d if isinstance(d, pd.Timestamp) else pd.Timestamp(d)
                        for d in lower_dates]
    lower_dates_clean = []
    for d in lower_dates_norm:
        if d.tz is not None:
            lower_dates_clean.append(d.tz_localize(None))
        else:
            lower_dates_clean.append(d)

    # ── For each trade, check lower-TF confirmation ──
    for trade in trade_records:
        is_long = trade["type"] == "long"
        entry_idx = trade["entry_idx"]
        exit_idx = trade["exit_idx"]
        exit_reason = trade.get("exit_reason", "")
        direction = 1 if is_long else -1

        # Get trade date range (tz-naive for comparison)
        trade_entry_date = _normalize(dates[entry_idx]) if entry_idx < n_dates else None
        trade_exit_date = _normalize(dates[exit_idx]) if exit_idx < n_dates else None

        if trade_entry_date is None:
            continue

        # ── Scan lower-TF pairs for same-direction confirmation ──
        confirmed = False
        for p_start, p_end in lower_pairs:
            if p_start >= len(lower_sig) or p_end >= len(lower_sig):
                continue
            lower_start_date = lower_dates_clean[p_start] if p_start < len(lower_dates_clean) else None
            if lower_start_date is None:
                continue

            # Must be at or after the trade entry (confirmation after signal)
            if lower_start_date < trade_entry_date:
                continue
            # Must be within the trade window
            if trade_exit_date is not None and lower_start_date > trade_exit_date:
                continue

            # Same-direction check
            if lower_sig[p_end] == direction:
                confirmed = True
                break

        if confirmed:
            d_entry = dates[entry_idx] if entry_idx < n_dates else None
            d_exit = dates[exit_idx] if exit_idx < n_dates else None

            if is_long:
                entry.append((int(entry_idx), "B", "green", d_entry))
                exit_.append((int(exit_idx), "S", "green", exit_reason, d_exit))
            else:
                entry.append((int(entry_idx), "S", "red", d_entry))
                exit_.append((int(exit_idx), "B", "red", exit_reason, d_exit))

    return {"entry_markers": entry, "exit_markers": exit_}


def _compute_cascade_markers(t, dates, schmitt, all_pairs,
                              trade_records, higher_bs):
    """从高一级周期的 BS 标记级联计算本级标记。

    入场：等本级同向 Schmitt 信号出现后标记
    出场（pair_end）：找本级对应 pair 结束位置标记
    出场（stop_loss）：立即在对应时间位置标记
    """
    entry = []
    exit_ = []

    if schmitt is None:
        return {"entry_markers": entry, "exit_markers": exit_}

    sig = schmitt["sig"]
    n_dates = len(dates) if dates is not None else 0
    higher_entries = higher_bs.get("entry_markers", [])
    higher_exits = higher_bs.get("exit_markers", [])

    # ── 级联入场 ──
    for h_idx, h_label, h_color, h_date in higher_entries:
        if h_date is None:
            continue
        start_bar = _find_date_index(dates, h_date)
        if start_bar is None:
            continue

        # h_label="B" → 做多 → expected_dir=1; h_label="S" → 做空 → expected_dir=-1
        expected_dir = 1 if h_label == "B" else -1

        for pair_start, pair_end in all_pairs:
            if pair_start >= start_bar and pair_end < len(sig):
                if sig[pair_end] == expected_dir:
                    label = "B" if expected_dir == 1 else "S"
                    color = "green" if expected_dir == 1 else "red"
                    d = dates[pair_start] if pair_start < n_dates else None
                    entry.append((int(pair_start), label, color, d))
                    break

    # ── 级联出场 ──
    for h_exit in higher_exits:
        h_idx, h_label, h_color, h_exit_type, h_date = h_exit
        if h_date is None:
            continue
        start_bar = _find_date_index(dates, h_date)
        if start_bar is None:
            continue

        if h_exit_type == "stop_loss":
            # 偏离退出：立即在对应时间位置标记
            if start_bar < n_dates:
                exit_.append((int(start_bar), h_label, h_color, "stop_loss", dates[start_bar]))
        else:
            # 多空对结束：「S ← 平多」→ 找 long pair 结束；「B ← 平空」→ 找 short pair 结束
            expected_dir = 1 if h_label == "S" else -1

            for pair_start, pair_end in all_pairs:
                if pair_start >= start_bar and pair_end < len(sig):
                    if sig[pair_end] == expected_dir:
                        d = dates[pair_end] if pair_end < n_dates else None
                        exit_.append((int(pair_end), h_label, h_color, "pair_end", d))
                        break

    return {"entry_markers": entry, "exit_markers": exit_}


def compute_bs_markers(t, dates, schmitt, all_pairs, trade_records,
                        tf, operating_tf, higher_bs=None,
                        lower_schmitt=None):
    """为单个视图计算 BS 标记。

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
        用户选择的操作周期。仅此周期及更低周期显示 BS 标记。
    higher_bs : dict or None
        紧邻高一级周期的 BS 标记（用于级联）。操作周期本身为 None。
    lower_schmitt : dict or None
        低一级周期的 Schmitt 数据，格式为
        ``{"all_pairs": [...], "sig": np.ndarray, "dates": pd.DatetimeIndex}``。
        仅用于操作周期的同向判断过滤；非操作周期忽略此参数。

    Returns
    -------
    dict
        {"entry_markers": [...], "exit_markers": [...]}
    """
    if tf == operating_tf:
        if lower_schmitt is not None:
            return _compute_own_markers_filtered(
                t, dates, schmitt, all_pairs, trade_records,
                lower_schmitt)
        else:
            return _compute_own_markers(t, dates, schmitt, all_pairs, trade_records)
    elif higher_bs is not None and (higher_bs.get("entry_markers") or higher_bs.get("exit_markers")):
        return _compute_cascade_markers(t, dates, schmitt, all_pairs,
                                         trade_records, higher_bs)
    else:
        return {"entry_markers": [], "exit_markers": []}


def get_lower_tfs(operating_tf):
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
