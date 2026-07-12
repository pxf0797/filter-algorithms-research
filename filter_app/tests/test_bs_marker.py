"""
BS 仓位操作标识模块 — 单元测试
"""
import numpy as np
import pandas as pd
import pytest

from services.bs_marker import (
    compute_bs_markers, _compute_own_from_trades, _compute_own_from_alignment,
    _compute_own_from_pairs, _find_date_index,
    _compute_cascade_markers,
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


# ── _compute_own_from_pairs ──────────────────────────────────────────

class TestComputeOwnMarkers:

    def test_long_entry_and_exit(self):
        """Long schmitt pair → green B (entry) + green S (exit)."""
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        schmitt = _make_schmitt([0, 0, 0, 1, 1, 0, 0, 0, 0, 0])
        all_pairs = [(3, 4)]

        result = _compute_own_from_pairs(t, dates, schmitt, all_pairs)

        assert len(result["entry_markers"]) == 1, "Long pair should produce 1 entry"
        assert result["entry_markers"][0] == (
            3, "B", "green", dates[3],
        ), "Long entry → green B at pair_start bar"
        assert len(result["exit_markers"]) == 1, "Long pair should produce 1 exit"
        assert result["exit_markers"][0] == (
            4, "S", "green", "pair_end", dates[4],
        ), "Long exit → green S at pair_end bar"

    def test_short_entry_and_exit(self):
        """Short schmitt pair → red S (entry) + red B (exit)."""
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        schmitt = _make_schmitt([0, 0, 0, -1, -1, 0, 0, 0, 0, 0])
        all_pairs = [(3, 4)]

        result = _compute_own_from_pairs(t, dates, schmitt, all_pairs)

        assert len(result["entry_markers"]) == 1
        assert result["entry_markers"][0] == (3, "S", "red", dates[3])
        assert len(result["exit_markers"]) == 1
        assert result["exit_markers"][0] == (4, "B", "red", "pair_end", dates[4])

    def test_multiple_pairs(self):
        """Multiple schmitt pairs produce correct entry/exit markers."""
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        schmitt = _make_schmitt([0, 1, 1, 0, -1, -1, 0, 0, 0, 0])
        all_pairs = [(1, 2), (4, 5)]      # long at 1-2, short at 4-5

        result = _compute_own_from_pairs(t, dates, schmitt, all_pairs)

        assert len(result["entry_markers"]) == 2
        assert result["entry_markers"][0] == (1, "B", "green", dates[1])
        assert result["entry_markers"][1] == (4, "S", "red", dates[4])
        assert len(result["exit_markers"]) == 2
        assert result["exit_markers"][0] == (2, "S", "green", "pair_end", dates[2])
        assert result["exit_markers"][1] == (5, "B", "red", "pair_end", dates[5])

    def test_no_schmitt_returns_empty(self):
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        all_pairs = [(3, 4)]

        result = _compute_own_from_pairs(t, dates, None, all_pairs)

        assert result["entry_markers"] == []
        assert result["exit_markers"] == []

    def test_empty_pairs_returns_empty(self):
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        schmitt = _make_schmitt([0, 0, 0, 1, 1, 0, 0, 0, 0, 0])

        result = _compute_own_from_pairs(t, dates, schmitt, [])

        assert result["entry_markers"] == []
        assert result["exit_markers"] == []

    def test_short_trade_sequence_is_S_then_B(self):
        """做空: 先S(红)入场, 后B(红)出场 — from all_pairs."""
        dates = pd.date_range('2026-01-05', periods=20, freq='B')
        t = np.arange(20, dtype=float)
        schmitt = _make_schmitt([0, 0, 0, 0, 0, -1, -1, -1, -1, -1,
                                  -1, -1, -1, -1, -1, -1, 0, 0, 0, 0])
        all_pairs = [(5, 15)]
        result = _compute_own_from_pairs(t, dates, schmitt, all_pairs)
        assert result["entry_markers"][0][1] == "S"
        assert result["entry_markers"][0][2] == "red"
        assert result["exit_markers"][0][1] == "B"
        assert result["exit_markers"][0][2] == "red"

    def test_long_trade_sequence_is_B_then_S(self):
        """做多: 先B(绿)入场, 后S(绿)出场 — from all_pairs."""
        dates = pd.date_range('2026-01-05', periods=20, freq='B')
        t = np.arange(20, dtype=float)
        schmitt = _make_schmitt([0, 0, 0, 0, 0, 1, 1, 1, 1, 1,
                                  1, 1, 1, 1, 1, 1, 0, 0, 0, 0])
        all_pairs = [(5, 15)]
        result = _compute_own_from_pairs(t, dates, schmitt, all_pairs)
        assert result["entry_markers"][0][1] == "B"
        assert result["entry_markers"][0][2] == "green"
        assert result["exit_markers"][0][1] == "S"
        assert result["exit_markers"][0][2] == "green"

    def test_pair_end_out_of_bounds_skipped(self):
        """pair_end >= len(sig) should be skipped."""
        dates = _make_dates("2024-01-01", periods=5)
        t = np.arange(5)
        schmitt = _make_schmitt([1, 1, 1, 1, 1])
        all_pairs = [(0, 99)]              # end far beyond sig

        result = _compute_own_from_pairs(t, dates, schmitt, all_pairs)

        assert result["entry_markers"] == []
        assert result["exit_markers"] == []

    def test_trade_records_primary_all_pairs_fallback(self):
        """trade_records非空→从交易生成; 为空→回退all_pairs."""
        dates = pd.date_range('2026-01-05', periods=30, freq='B')
        sig = np.array([0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, -1, -1, -1,
                        -1, -1, -1, -1, -1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        schmitt = {'sig': sig}
        all_pairs = [(2, 9), (12, 19)]

        # With trade_records → from trades (1 entry + 1 exit)
        trade_records = [
            {"type": "long", "entry_idx": 3, "exit_idx": 8,
             "return_pct": 2.0, "exit_reason": "take_profit"},
        ]
        result = _compute_own_from_trades(
            np.arange(30, dtype=float), dates, trade_records,
        )
        assert len(result["entry_markers"]) == 1
        assert result["entry_markers"][0][0] == 3   # trade entry_idx, not pair_start=2
        assert result["exit_markers"][0][0] == 8    # trade exit_idx, not pair_end=9
        assert result["exit_markers"][0][3] == "take_profit"

        # Without trade_records → fallback to all_pairs (2 entries + 2 exits)
        result2 = _compute_own_from_pairs(
            np.arange(30, dtype=float), dates, schmitt, all_pairs,
        )
        assert len(result2["entry_markers"]) == 2
        assert result2["entry_markers"][0][0] == 2   # pair_start

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


# ── _compute_own_from_alignment ──────────────────────────────────────

class TestComputeOwnFromAlignment:

    def test_long_aligned_entry_creates_green_B(self):
        """同向性判断long entry → 绿B."""
        dates = pd.date_range('2026-01-05', periods=30, freq='B')
        aligned = {
            "entry_markers": [(10, "long", 100.0, dates[10])],
            "exit_markers": [(20, "long", 112.0, 12.0, "take_profit", dates[20])],
        }
        result = _compute_own_from_alignment(np.arange(30, dtype=float), dates, aligned)
        assert result["entry_markers"][0][1] == "B"
        assert result["entry_markers"][0][2] == "green"
        assert result["exit_markers"][0][1] == "S"
        assert result["exit_markers"][0][2] == "green"

    def test_short_aligned_entry_creates_red_S(self):
        """同向性判断short entry → 红S."""
        dates = pd.date_range('2026-01-05', periods=30, freq='B')
        aligned = {
            "entry_markers": [(5, "short", 100.0, dates[5])],
            "exit_markers": [(15, "short", 95.0, -5.0, "stop_loss", dates[15])],
        }
        result = _compute_own_from_alignment(np.arange(30, dtype=float), dates, aligned)
        assert result["entry_markers"][0][1] == "S"
        assert result["entry_markers"][0][2] == "red"
        assert result["exit_markers"][0][1] == "B"
        assert result["exit_markers"][0][2] == "red"


# ── _compute_cascade_markers ─────────────────────────────────────────

class TestCascadeMarkers:

    def test_entry_cascade_same_direction(self):
        """Higher B entry cascades to first same-direction long pair in lower TF."""
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        schmitt = _make_schmitt([0, 0, 0, 1, 1, 0, 0, 0, 0, 0])
        all_pairs = [(3, 4)]
        trade_records = []
        higher_bs = {
            "entry_markers": [(0, "B", "green", pd.Timestamp("2024-01-03"))],
            "exit_markers": [],
        }

        result = _compute_cascade_markers(
            t, dates, schmitt, all_pairs, trade_records, higher_bs,
        )

        assert len(result["entry_markers"]) == 1, (
            "Should cascade green B entry to the matching long pair"
        )
        assert result["entry_markers"][0] == (3, "B", "green", dates[3])

    def test_entry_cascade_short(self):
        """Higher S (short) entry cascades to first same-direction short pair."""
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        schmitt = _make_schmitt([0, 0, 0, -1, -1, 0, 0, 0, 0, 0])
        all_pairs = [(3, 4)]
        trade_records = []
        higher_bs = {
            "entry_markers": [(0, "S", "red", pd.Timestamp("2024-01-03"))],
            "exit_markers": [],
        }

        result = _compute_cascade_markers(
            t, dates, schmitt, all_pairs, trade_records, higher_bs,
        )

        assert len(result["entry_markers"]) == 1
        assert result["entry_markers"][0] == (3, "S", "red", dates[3])

    def test_entry_cascade_wrong_direction_skipped(self):
        """Higher B entry, but lower TF only has short pairs — nothing cascaded."""
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        schmitt = _make_schmitt([0, 0, 0, -1, -1, 0, 0, 0, 0, 0])
        all_pairs = [(3, 4)]               # short pair
        trade_records = []
        higher_bs = {
            "entry_markers": [(0, "B", "green", pd.Timestamp("2024-01-03"))],
            "exit_markers": [],
        }

        result = _compute_cascade_markers(
            t, dates, schmitt, all_pairs, trade_records, higher_bs,
        )

        assert result["entry_markers"] == [], (
            "Should skip cascade when no same-direction pair exists"
        )

    def test_pair_end_exit_cascade(self):
        """Higher pair_end exit (green S) cascades to first long pair exit in lower TF."""
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        schmitt = _make_schmitt([0, 0, 0, 1, 1, 0, 0, 0, 0, 0])
        all_pairs = [(3, 4)]
        trade_records = []
        higher_bs = {
            "entry_markers": [],
            "exit_markers": [
                (5, "S", "green", "pair_end", pd.Timestamp("2024-01-03")),
            ],
        }

        result = _compute_cascade_markers(
            t, dates, schmitt, all_pairs, trade_records, higher_bs,
        )

        assert len(result["exit_markers"]) == 1
        assert result["exit_markers"][0][:4] == (4, "S", "green", "pair_end")

    def test_stop_loss_exit_cascade_immediate(self):
        """Stop-loss exit cascades immediately at the matching bar (no pair wait)."""
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        schmitt = _make_schmitt([0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        all_pairs = []
        trade_records = []
        higher_bs = {
            "entry_markers": [],
            "exit_markers": [
                (5, "S", "green", "stop_loss", pd.Timestamp("2024-01-03")),
            ],
        }

        result = _compute_cascade_markers(
            t, dates, schmitt, all_pairs, trade_records, higher_bs,
        )

        assert len(result["exit_markers"]) == 1
        assert result["exit_markers"][0] == (
            2, "S", "green", "stop_loss", dates[2],
        ), "Stop-loss should mark immediately at the date-aligned bar"

    def test_early_date_browse_mode(self):
        """Higher TF date before lower TF range → fallback to bar 0."""
        dates = _make_dates("2024-01-10", periods=10)
        t = np.arange(10)
        schmitt = _make_schmitt([1, 1, 0, 0, 0, 0, 0, 0, 0, 0])
        all_pairs = [(0, 1)]               # long pair at bar 0-1
        trade_records = []
        higher_bs = {
            "entry_markers": [
                (0, "B", "green", pd.Timestamp("2024-01-01")),
            ],
            "exit_markers": [],
        }

        result = _compute_cascade_markers(
            t, dates, schmitt, all_pairs, trade_records, higher_bs,
        )

        assert len(result["entry_markers"]) == 1, (
            "Should cascade from bar 0 when higher TF date precedes local data "
            "(browse mode fallback)"
        )
        assert result["entry_markers"][0] == (0, "B", "green", dates[0])

    def test_late_date_skipped(self):
        """Higher TF date after lower TF range → marker skipped."""
        dates = _make_dates("2024-01-01", periods=5)
        t = np.arange(5)
        schmitt = _make_schmitt([0, 0, 0, 0, 0])
        all_pairs = []
        trade_records = []
        higher_bs = {
            "entry_markers": [
                (0, "B", "green", pd.Timestamp("2024-01-10")),
            ],
            "exit_markers": [],
        }

        result = _compute_cascade_markers(
            t, dates, schmitt, all_pairs, trade_records, higher_bs,
        )

        assert result["entry_markers"] == [], (
            "Should skip cascade when higher TF date is beyond local data range"
        )

    def test_none_higher_date_skipped(self):
        """Higher marker with h_date=None is skipped."""
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        schmitt = _make_schmitt([0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        all_pairs = []
        trade_records = []
        higher_bs = {
            "entry_markers": [(0, "B", "green", None)],
            "exit_markers": [],
        }

        result = _compute_cascade_markers(
            t, dates, schmitt, all_pairs, trade_records, higher_bs,
        )

        assert result["entry_markers"] == []

    def test_empty_higher_bs_short_circuits(self):
        """Empty entry/exit marker lists produce empty result."""
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        schmitt = _make_schmitt([0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        all_pairs = []
        trade_records = []
        higher_bs = {"entry_markers": [], "exit_markers": []}

        result = _compute_cascade_markers(
            t, dates, schmitt, all_pairs, trade_records, higher_bs,
        )

        assert result["entry_markers"] == []
        assert result["exit_markers"] == []

    def test_none_schmitt_returns_empty(self):
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        all_pairs = []
        trade_records = []
        higher_bs = {
            "entry_markers": [(0, "B", "green", pd.Timestamp("2024-01-03"))],
            "exit_markers": [],
        }

        result = _compute_cascade_markers(
            t, dates, None, all_pairs, trade_records, higher_bs,
        )

        assert result["entry_markers"] == []
        assert result["exit_markers"] == []


# ── compute_bs_markers ───────────────────────────────────────────────

class TestComputeBsMarkers:

    def test_operating_tf_uses_own_markers(self):
        """tf == operating_tf → delegates to _compute_own_from_pairs."""
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        schmitt = _make_schmitt([0, 0, 0, 1, 1, 0, 0, 0, 0, 0])
        all_pairs = [(3, 4)]
        trade_records = []

        result = compute_bs_markers(
            t, dates, schmitt, all_pairs, trade_records,
            tf="日线", operating_tf="日线", higher_bs=None,
        )

        assert len(result["entry_markers"]) == 1
        assert result["entry_markers"][0] == (3, "B", "green", dates[3])

    def test_operating_tf_uses_aligned_markers(self):
        """操作周期有aligned_markers时优先使用同向性判断数据."""
        dates = pd.date_range('2026-01-05', periods=30, freq='B')
        aligned = {
            "entry_markers": [(10, "long", 100.0, dates[10])],
            "exit_markers": [],
        }
        result = compute_bs_markers(
            np.arange(30, dtype=float), dates, None, [], [],
            '日线', '日线', aligned_markers=aligned)
        assert result["entry_markers"][0][1] == "B"

    def test_lower_tf_uses_cascade(self):
        """tf < operating_tf with higher_bs → delegates to _compute_cascade_markers."""
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        schmitt = _make_schmitt([0, 0, 0, 1, 1, 0, 0, 0, 0, 0])
        all_pairs = [(3, 4)]
        trade_records = []
        higher_bs = {
            "entry_markers": [(0, "B", "green", pd.Timestamp("2024-01-03"))],
            "exit_markers": [],
        }

        result = compute_bs_markers(
            t, dates, schmitt, all_pairs, trade_records,
            tf="60分钟", operating_tf="日线", higher_bs=higher_bs,
        )

        assert len(result["entry_markers"]) == 1

    def test_higher_tf_returns_empty(self):
        """tf > operating_tf → no BS markers (empty)."""
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        schmitt = _make_schmitt([0, 0, 0, 1, 1, 0, 0, 0, 0, 0])
        all_pairs = [(3, 4)]
        trade_records = []

        result = compute_bs_markers(
            t, dates, schmitt, all_pairs, trade_records,
            tf="周线", operating_tf="日线", higher_bs=None,
        )

        assert result["entry_markers"] == []
        assert result["exit_markers"] == []

    def test_lower_tf_no_higher_bs_returns_empty(self):
        """tf < operating_tf but higher_bs is None → empty."""
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        schmitt = _make_schmitt([0, 0, 0, 1, 1, 0, 0, 0, 0, 0])
        all_pairs = [(3, 4)]
        trade_records = []

        result = compute_bs_markers(
            t, dates, schmitt, all_pairs, trade_records,
            tf="60分钟", operating_tf="日线", higher_bs=None,
        )

        assert result["entry_markers"] == []
        assert result["exit_markers"] == []

    def test_lower_tf_empty_higher_bs_returns_empty(self):
        """tf < operating_tf but higher_bs has no markers → empty."""
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        schmitt = _make_schmitt([0, 0, 0, 1, 1, 0, 0, 0, 0, 0])
        all_pairs = [(3, 4)]
        trade_records = []
        higher_bs = {"entry_markers": [], "exit_markers": []}

        result = compute_bs_markers(
            t, dates, schmitt, all_pairs, trade_records,
            tf="60分钟", operating_tf="日线", higher_bs=higher_bs,
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
