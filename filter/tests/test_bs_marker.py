"""
BS 仓位操作标识模块 — 单元测试
"""
import numpy as np
import pandas as pd
import pytest

from filter.engine.signals import (
    compute_bs_markers, _compute_from_trades_filtered, _compute_own_from_trades,
    _find_date_index,
    TF_LOWER, get_lower_tfs,
)


# ── helpers ──────────────────────────────────────────────────────────

def _make_dates(start="2024-01-01", periods=10, freq="D"):
    return pd.date_range(start, periods=periods, freq=freq)


def _make_schmitt(sig_values):
    return {"sig": np.array(sig_values)}


# ── _find_date_index ─────────────────────────────────────────────────

class TestFindDateIndex:

    def test_target_before_range_returns_zero(self):
        dates = _make_dates("2024-01-10", periods=5)
        result = _find_date_index(dates, pd.Timestamp("2024-01-01"))
        assert result == 0, (
            "Expected 0 when target_date is before all dates (browse mode "
            "fallback to first available bar)"
        )

    def test_target_after_range_returns_none(self):
        dates = _make_dates("2024-01-01", periods=5)
        result = _find_date_index(dates, pd.Timestamp("2024-01-10"))
        assert result is None, (
            "Expected None when target_date is after all dates (cannot "
            "cascade — marker should be skipped)"
        )

    def test_target_within_range(self):
        dates = _make_dates("2024-01-01", periods=5)
        result = _find_date_index(dates, pd.Timestamp("2024-01-03"))
        assert result == 2, (
            "Expected index of first bar whose date >= target_date"
        )

    def test_target_exact_match(self):
        dates = _make_dates("2024-01-01", periods=5)
        result = _find_date_index(dates, pd.Timestamp("2024-01-01"))
        assert result == 0, (
            "Expected index 0 when target_date equals the first bar"
        )

    def test_none_dates(self):
        result = _find_date_index(None, pd.Timestamp("2024-01-01"))
        assert result is None, "Expected None when dates is None"

    def test_empty_dates(self):
        result = _find_date_index(pd.DatetimeIndex([]), pd.Timestamp("2024-01-01"))
        assert result is None, "Expected None when dates is empty"

    def test_none_target(self):
        dates = _make_dates("2024-01-01", periods=5)
        result = _find_date_index(dates, None)
        assert result is None, "Expected None when target_date is None"

    def test_tz_naive_vs_aware_comparison(self):
        """Fix regression: tz-naive target vs tz-aware dates should work."""
        # Simulate 日线 (tz-naive) vs 60分钟 (tz-aware from HK exchange)
        dates_tz_aware = pd.date_range('2026-05-29 09:30', periods=50, freq='1h', tz='Asia/Hong_Kong')
        target_tz_naive = pd.Timestamp('2026-05-30')  # daily date within range, no tz

        result = _find_date_index(dates_tz_aware, target_tz_naive)
        # Should find the correct bar, NOT return None (which was the bug)
        assert result is not None, (
            f"tz-naive vs tz-aware comparison should work after fix, got None"
        )
        assert result >= 0, f"Expected valid index, got {result}"

    def test_tz_aware_target_vs_naive_dates(self):
        """Tz-aware target date vs tz-naive dates should work."""
        dates_naive = pd.date_range('2026-06-01', periods=30, freq='D')
        target_aware = pd.Timestamp('2026-06-15 14:30:00+08:00')

        result = _find_date_index(dates_naive, target_aware)
        assert result is not None, f"Expected valid index, got None"


# ── _compute_own_from_trades ─────────────────────────────────────────

class TestComputeOwnFromTrades:

    def test_trade_records_aligns_with_tongxiang_panduan(self):
        """BS标记入场点=trade_records的entry_idx(pair_end信号确认点)."""
        dates = pd.date_range('2026-01-05', periods=30, freq='B')
        # 模拟: all_pairs pair=(2,9), sig[9]=1(做多)
        # trade_records entry_idx=9 (pair_end, 信号确认点)
        trade_records = [
            {"type": "long", "entry_idx": 9, "exit_idx": 20,
             "return_pct": 15.0, "exit_reason": "take_profit"},
        ]
        result = _compute_own_from_trades(
            np.arange(30, dtype=float), dates, trade_records,
        )
        assert result["entry_markers"][0][0] == 9    # 对齐 trade_records entry_idx (pair_end)
        assert result["entry_markers"][0][1] == "B"  # green B for long

    def test_short_trade_sequence_S_then_B(self):
        """做空交易: 先S(红)入场, 后B(红)出场."""
        dates = pd.date_range('2026-01-05', periods=30, freq='B')
        trade_records = [
            {"type": "short", "entry_idx": 5, "exit_idx": 15,
             "return_pct": -3.0, "exit_reason": "stop_loss"},
        ]
        result = _compute_own_from_trades(
            np.arange(30, dtype=float), dates, trade_records,
        )
        assert result["entry_markers"][0][1] == "S"
        assert result["entry_markers"][0][2] == "red"
        assert result["exit_markers"][0][1] == "B"
        assert result["exit_markers"][0][2] == "red"

    def test_long_trade_sequence_B_then_S(self):
        """做多交易: 先B(绿)入场, 后S(绿)出场."""
        dates = pd.date_range('2026-01-05', periods=20, freq='B')
        trade_records = [
            {"type": "long", "entry_idx": 5, "exit_idx": 15,
             "return_pct": 10.0, "exit_reason": "take_profit"},
        ]
        result = _compute_own_from_trades(
            np.arange(20, dtype=float), dates, trade_records,
        )
        assert result["entry_markers"][0][1] == "B"
        assert result["entry_markers"][0][2] == "green"
        assert result["exit_markers"][0][1] == "S"
        assert result["exit_markers"][0][2] == "green"

    def test_eod_exit_suppressed_in_own_trades(self):
        """_compute_own_from_trades也抑制eod."""
        dates = pd.date_range('2026-01-05', periods=30, freq='B')
        trade_records = [
            {"type": "short", "entry_idx": 5, "exit_idx": 29, "return_pct": -3.0, "exit_reason": "eod"},
        ]
        result = _compute_own_from_trades(np.arange(30, dtype=float), dates, trade_records)
        assert len(result["entry_markers"]) == 1
        assert result["entry_markers"][0][1] == "S"
        assert len(result["exit_markers"]) == 0

    def test_empty_trade_records_returns_empty(self):
        """Empty trade_records → no markers."""
        dates = pd.date_range('2026-01-05', periods=10, freq='B')
        result = _compute_own_from_trades(np.arange(10), dates, [])
        assert result["entry_markers"] == []
        assert result["exit_markers"] == []

    def test_multiple_mixed_trades(self):
        """Long + short trades in same list → both generate markers."""
        dates = pd.date_range('2026-01-05', periods=30, freq='B')
        trade_records = [
            {"type": "long", "entry_idx": 3, "exit_idx": 8,
             "return_pct": 5.0, "exit_reason": "take_profit"},
            {"type": "short", "entry_idx": 12, "exit_idx": 20,
             "return_pct": -2.0, "exit_reason": "stop_loss"},
        ]
        result = _compute_own_from_trades(
            np.arange(30, dtype=float), dates, trade_records,
        )
        assert len(result["entry_markers"]) == 2
        assert len(result["exit_markers"]) == 2
        # long trade
        assert result["entry_markers"][0] == (3, "B", "green", dates[3])
        assert result["exit_markers"][0] == (8, "S", "green", "take_profit", dates[8])
        # short trade
        assert result["entry_markers"][1] == (12, "S", "red", dates[12])
        assert result["exit_markers"][1] == (20, "B", "red", "stop_loss", dates[20])


# ── _compute_from_trades_filtered ────────────────────────────────────

class TestComputeFromTradesFiltered:
    def test_long_trade_in_long_mask_kept(self):
        """Long trade entry in long_mask → BS markers."""
        dates = pd.date_range('2026-01-05', periods=30, freq='B')
        trade_records = [
            {"type": "long", "entry_idx": 10, "exit_idx": 20, "return_pct": 5.0, "exit_reason": "take_profit"},
        ]
        long_mask = np.zeros(30, dtype=bool); long_mask[8:22] = True
        short_mask = np.zeros(30, dtype=bool)
        result = _compute_from_trades_filtered(np.arange(30, dtype=float), dates, trade_records, (long_mask, short_mask))
        assert len(result["entry_markers"]) == 1
        assert result["entry_markers"][0][1] == "B"
        assert result["exit_markers"][0][1] == "S"

    def test_trade_outside_mask_filtered_out(self):
        """Trade entry outside mask → no markers."""
        dates = pd.date_range('2026-01-05', periods=30, freq='B')
        trade_records = [
            {"type": "short", "entry_idx": 5, "exit_idx": 15, "return_pct": -2.0, "exit_reason": "stop_loss"},
        ]
        short_mask = np.zeros(30, dtype=bool); short_mask[16:25] = True  # entry=5 not in mask
        long_mask = np.zeros(30, dtype=bool)
        result = _compute_from_trades_filtered(np.arange(30, dtype=float), dates, trade_records, (long_mask, short_mask))
        assert len(result["entry_markers"]) == 0

    def test_short_trade_sequence_S_then_B(self):
        """Short sequence: S(red) entry, B(red) exit."""
        dates = pd.date_range('2026-01-05', periods=30, freq='B')
        trade_records = [
            {"type": "short", "entry_idx": 10, "exit_idx": 20, "return_pct": -3.0, "exit_reason": "stop_loss"},
        ]
        short_mask = np.ones(30, dtype=bool); long_mask = np.zeros(30, dtype=bool)
        result = _compute_from_trades_filtered(np.arange(30, dtype=float), dates, trade_records, (long_mask, short_mask))
        assert result["entry_markers"][0][1] == "S" and result["entry_markers"][0][2] == "red"
        assert result["exit_markers"][0][1] == "B" and result["exit_markers"][0][2] == "red"

    def test_eod_exit_suppressed_in_filtered(self):
        """eod出场不标BS, 入场仍标."""
        dates = pd.date_range('2026-01-05', periods=30, freq='B')
        trade_records = [
            {"type": "long", "entry_idx": 10, "exit_idx": 29, "return_pct": 5.0, "exit_reason": "eod"},
        ]
        long_mask = np.ones(30, dtype=bool); short_mask = np.zeros(30, dtype=bool)
        result = _compute_from_trades_filtered(np.arange(30, dtype=float), dates, trade_records, (long_mask, short_mask))
        assert len(result["entry_markers"]) == 1, "eod entry should still be marked"
        assert len(result["exit_markers"]) == 0, "eod exit should be suppressed"

    def test_non_eod_exit_still_marked(self):
        """非eod出场(take_profit/stop_loss)正常标BS."""
        dates = pd.date_range('2026-01-05', periods=30, freq='B')
        trade_records = [
            {"type": "long", "entry_idx": 10, "exit_idx": 20, "return_pct": 5.0, "exit_reason": "take_profit"},
        ]
        long_mask = np.ones(30, dtype=bool); short_mask = np.zeros(30, dtype=bool)
        result = _compute_from_trades_filtered(np.arange(30, dtype=float), dates, trade_records, (long_mask, short_mask))
        assert len(result["entry_markers"]) == 1
        assert len(result["exit_markers"]) == 1, "non-eod exit should be marked"

    def test_empty_trade_records_returns_empty(self):
        """Empty trade_records → no markers, regardless of masks."""
        dates = pd.date_range('2026-01-05', periods=10, freq='B')
        long_mask = np.ones(10, dtype=bool)
        short_mask = np.ones(10, dtype=bool)
        result = _compute_from_trades_filtered(
            np.arange(10, dtype=float), dates, [], (long_mask, short_mask),
        )
        assert result["entry_markers"] == []
        assert result["exit_markers"] == []

    def test_multiple_trades_mixed_filter(self):
        """Only trades whose entry_idx falls in the correct mask produce markers."""
        dates = pd.date_range('2026-01-05', periods=30, freq='B')
        # long trade entry=5 (in long_mask), short trade entry=15 (NOT in short_mask)
        trade_records = [
            {"type": "long", "entry_idx": 5, "exit_idx": 10,
             "return_pct": 3.0, "exit_reason": "take_profit"},
            {"type": "short", "entry_idx": 15, "exit_idx": 25,
             "return_pct": -5.0, "exit_reason": "stop_loss"},
        ]
        long_mask = np.zeros(30, dtype=bool); long_mask[3:12] = True    # covers entry=5
        short_mask = np.zeros(30, dtype=bool); short_mask[18:28] = True  # does NOT cover entry=15
        result = _compute_from_trades_filtered(
            np.arange(30, dtype=float), dates, trade_records, (long_mask, short_mask),
        )
        # Only the long trade passes the filter
        assert len(result["entry_markers"]) == 1
        assert result["entry_markers"][0][1] == "B"
        assert result["entry_markers"][0][2] == "green"

    def test_entry_idx_beyond_mask_length_skipped(self):
        """entry_idx >= len(mask) → trade is skipped gracefully."""
        dates = pd.date_range('2026-01-05', periods=30, freq='B')
        trade_records = [
            {"type": "long", "entry_idx": 10, "exit_idx": 20,
             "return_pct": 5.0, "exit_reason": "take_profit"},
        ]
        # mask shorter than entry_idx
        long_mask = np.ones(5, dtype=bool)  # length=5, entry_idx=10 not valid
        short_mask = np.zeros(5, dtype=bool)
        result = _compute_from_trades_filtered(
            np.arange(30, dtype=float), dates, trade_records, (long_mask, short_mask),
        )
        # Trade should be skipped — no crash, no markers
        assert len(result["entry_markers"]) == 0
        assert len(result["exit_markers"]) == 0


# ── compute_bs_markers ───────────────────────────────────────────────

class TestComputeBsMarkers:

    def test_with_holding_masks_and_trades(self):
        """holding_masks + trade_records → filtered path."""
        dates = pd.date_range('2026-01-05', periods=10, freq='B')
        t = np.arange(10, dtype=float)
        schmitt = _make_schmitt([0, 0, 0, 1, 1, 0, 0, 0, 0, 0])

        trade_records = [
            {"type": "long", "entry_idx": 3, "exit_idx": 6,
             "return_pct": 5.0, "exit_reason": "take_profit"},
            {"type": "short", "entry_idx": 5, "exit_idx": 8,
             "return_pct": -2.0, "exit_reason": "stop_loss"},
        ]
        long_mask = np.zeros(10, dtype=bool)
        long_mask[3:7] = True       # entry_idx=3 in mask
        short_mask = np.zeros(10, dtype=bool)  # entry_idx=5 not in mask

        result = compute_bs_markers(
            t, dates, schmitt, [], trade_records,
            tf="日线", operating_tf="日线",
            holding_masks=(long_mask, short_mask),
        )
        assert len(result["entry_markers"]) == 1
        assert result["entry_markers"][0][1] == "B"
        assert result["entry_markers"][0][2] == "green"

    def test_with_trades_only_no_masks(self):
        """trade_records, no holding_masks → unfiltered fallback."""
        dates = pd.date_range('2026-01-05', periods=10, freq='B')
        t = np.arange(10)
        schmitt = _make_schmitt([0, 0, 0, 1, 1, 0, 0, 0, 0, 0])
        trade_records = [
            {"type": "long", "entry_idx": 3, "exit_idx": 4,
             "return_pct": 2.0, "exit_reason": "take_profit"},
        ]

        result = compute_bs_markers(
            t, dates, schmitt, [], trade_records,
            tf="日线", operating_tf="日线",
        )

        assert len(result["entry_markers"]) == 1
        assert result["entry_markers"][0][1] == "B"
        assert result["entry_markers"][0][2] == "green"

    def test_with_neither_returns_empty(self):
        """No trade_records, no holding_masks → empty."""
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        schmitt = _make_schmitt([0, 0, 0, 1, 1, 0, 0, 0, 0, 0])

        result = compute_bs_markers(
            t, dates, schmitt, [], [],
            tf="日线", operating_tf="日线",
        )

        assert result["entry_markers"] == []
        assert result["exit_markers"] == []


# ── get_lower_tfs ────────────────────────────────────────────────────

class TestGetLowerTfs:

    def test_daily_chain(self):
        assert get_lower_tfs("日线") == ["60分钟", "15分钟", "5分钟", "1分钟"]

    def test_15min_chain(self):
        assert get_lower_tfs("15分钟") == ["5分钟", "1分钟"]

    def test_lowest_tf_returns_empty(self):
        assert get_lower_tfs("1分钟") == []

    def test_top_tf_chain(self):
        assert get_lower_tfs("季线") == [
            "月线", "周线", "日线", "60分钟", "15分钟", "5分钟", "1分钟",
        ]

    def test_unknown_tf_returns_empty(self):
        """TF not in TF_LOWER should return empty list."""
        assert get_lower_tfs("不存在的周期") == []


# ── TF_LOWER hierarchy ───────────────────────────────────────────────

class TestTFHierarchy:

    def test_tf_lower_is_inverse_of_hierarchy(self):
        """TF_LOWER maps each higher TF to its immediate lower TF,
        creating a complete chain from top to bottom."""
        assert TF_LOWER["季线"] == "月线"
        assert TF_LOWER["月线"] == "周线"
        assert TF_LOWER["周线"] == "日线"
        assert TF_LOWER["日线"] == "60分钟"
        assert TF_LOWER["60分钟"] == "15分钟"
        assert TF_LOWER["15分钟"] == "5分钟"
        assert TF_LOWER["5分钟"] == "1分钟"
        assert TF_LOWER["1分钟"] is None, "Lowest TF should map to None"

    def test_chain_is_complete(self):
        chain = ["季线", "月线", "周线", "日线", "60分钟", "15分钟", "5分钟", "1分钟"]
        for i in range(len(chain) - 1):
            assert TF_LOWER[chain[i]] == chain[i + 1], (
                f"{chain[i]} should map to {chain[i + 1]}"
            )

    def test_get_lower_tfs_matches_chain(self):
        """get_lower_tfs for any TF should match a sub-slice of the chain."""
        chain = ["季线", "月线", "周线", "日线", "60分钟", "15分钟", "5分钟", "1分钟"]
        for i, tf in enumerate(chain):
            expected = chain[i + 1:]
            assert get_lower_tfs(tf) == expected, f"Wrong lower TFs for {tf}"

    def test_no_cycles(self):
        """TF_LOWER should contain no cycles (walkable to None for every key)."""
        visited = set()
        for tf in TF_LOWER:
            current = tf
            path = []
            while current is not None and current not in visited:
                path.append(current)
                visited.add(current)
                current = TF_LOWER.get(current)
                assert len(path) <= len(TF_LOWER), (
                    f"Cycle detected in TF_LOWER starting from {tf}"
                )
