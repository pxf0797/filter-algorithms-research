"""
BS 仓位操作标识模块 — 单元测试
"""
import numpy as np
import pandas as pd
import pytest

from services.bs_marker import (
    _find_date_index,
    _compute_own_markers,
    _compute_own_markers_filtered,
    _compute_cascade_markers,
    compute_bs_markers,
    TF_LOWER,
    get_lower_tfs,
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


# ── _compute_own_markers ─────────────────────────────────────────────

class TestComputeOwnMarkers:

    def test_long_entry_and_exit(self):
        """Long schmitt pair → green B (entry) + green S (exit)."""
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        schmitt = _make_schmitt([0, 0, 0, 1, 1, 0, 0, 0, 0, 0])
        all_pairs = [(3, 4)]
        trade_records = []

        result = _compute_own_markers(t, dates, schmitt, all_pairs, trade_records)

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
        trade_records = []

        result = _compute_own_markers(t, dates, schmitt, all_pairs, trade_records)

        assert len(result["entry_markers"]) == 1
        assert result["entry_markers"][0] == (3, "S", "red", dates[3])
        assert len(result["exit_markers"]) == 1
        assert result["exit_markers"][0] == (4, "B", "red", "pair_end", dates[4])

    def test_stop_loss_exit(self):
        """Long trade with stop_loss exit — markers from trade_records."""
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        schmitt = _make_schmitt([0, 0, 0, 1, 1, 1, 1, 0, 0, 0])
        all_pairs = [(3, 6)]
        trade_records = [
            {"type": "long", "entry_idx": 2, "exit_idx": 7,
             "return_pct": 3.5, "exit_reason": "stop_loss"},
        ]

        result = _compute_own_markers(t, dates, schmitt, all_pairs, trade_records)

        # trade_records non-empty → markers come from trades, not all_pairs
        assert len(result["entry_markers"]) == 1
        assert result["entry_markers"][0] == (2, "B", "green", dates[2])
        assert len(result["exit_markers"]) == 1
        assert result["exit_markers"][0] == (
            7, "S", "green", "stop_loss", dates[7],
        ), "Long stop_loss → green S at exit_idx"

    def test_short_stop_loss_exit(self):
        """Short trade with stop_loss exit — markers from trade_records."""
        dates = _make_dates("2024-01-01", periods=20)
        t = np.arange(20)
        schmitt = _make_schmitt([0] * 20)
        all_pairs = [(3, 6)]
        trade_records = [
            {"type": "short", "entry_idx": 12, "exit_idx": 18,
             "return_pct": 2.1, "exit_reason": "stop_loss"},
        ]

        result = _compute_own_markers(t, dates, schmitt, all_pairs, trade_records)

        # trade_records non-empty → markers come from trades, not all_pairs
        assert len(result["entry_markers"]) == 1
        assert result["entry_markers"][0] == (12, "S", "red", dates[12])
        assert len(result["exit_markers"]) == 1
        assert result["exit_markers"][0][:4] == (18, "B", "red", "stop_loss")

    def test_multiple_pairs(self):
        """Multiple schmitt pairs produce correct entry/exit markers."""
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        schmitt = _make_schmitt([0, 1, 1, 0, -1, -1, 0, 0, 0, 0])
        all_pairs = [(1, 2), (4, 5)]      # long at 1-2, short at 4-5
        trade_records = []

        result = _compute_own_markers(t, dates, schmitt, all_pairs, trade_records)

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
        trade_records = []

        result = _compute_own_markers(t, dates, None, all_pairs, trade_records)

        assert result["entry_markers"] == []
        assert result["exit_markers"] == []

    def test_empty_pairs_returns_empty(self):
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        schmitt = _make_schmitt([0, 0, 0, 1, 1, 0, 0, 0, 0, 0])
        trade_records = []

        result = _compute_own_markers(t, dates, schmitt, [], trade_records)

        assert result["entry_markers"] == []
        assert result["exit_markers"] == []

    def test_trade_records_take_priority_over_pairs(self):
        """trade_records 非空时，标记来自交易记录而非 all_pairs."""
        dates = pd.date_range('2026-01-05', periods=20, freq='B')
        schmitt = {'sig': np.array([0,0,1,1,1,1,1,1,1,1,1,0,-1,-1,-1,-1,-1,-1,-1,-1])}
        all_pairs = [(2, 9), (12, 19)]  # 2 pairs
        trade_records = [
            {"type": "long", "entry_idx": 3, "exit_idx": 7, "return_pct": 2.0, "exit_reason": "take_profit"},
        ]  # only 1 trade

        result = _compute_own_markers(np.arange(20, dtype=float), dates, schmitt, all_pairs, trade_records)
        # Should have exactly 1 entry + 1 exit (from trade_records), not 2+2 (from all_pairs)
        assert len(result["entry_markers"]) == 1
        assert len(result["exit_markers"]) == 1
        assert result["entry_markers"][0][1] == "B"  # long entry
        assert result["entry_markers"][0][2] == "green"
        assert result["exit_markers"][0][3] == "take_profit"

    def test_short_trade_sequence_is_S_then_B(self):
        """做空: 先S(红)入场, 后B(红)出场."""
        dates = pd.date_range('2026-01-05', periods=20, freq='B')
        schmitt = {'sig': np.zeros(20)}
        trade_records = [
            {"type": "short", "entry_idx": 5, "exit_idx": 15, "return_pct": 3.0, "exit_reason": "pair_end"},
        ]
        result = _compute_own_markers(np.arange(20, dtype=float), dates, schmitt, [], trade_records)
        assert result["entry_markers"][0][1] == "S"
        assert result["entry_markers"][0][2] == "red"
        assert result["exit_markers"][0][1] == "B"
        assert result["exit_markers"][0][2] == "red"

    def test_long_trade_sequence_is_B_then_S(self):
        """做多: 先B(绿)入场, 后S(绿)出场."""
        dates = pd.date_range('2026-01-05', periods=20, freq='B')
        trade_records = [
            {"type": "long", "entry_idx": 5, "exit_idx": 15, "return_pct": 3.0, "exit_reason": "pair_end"},
        ]
        result = _compute_own_markers(np.arange(20, dtype=float), dates, None, [], trade_records)
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
        trade_records = []

        result = _compute_own_markers(t, dates, schmitt, all_pairs, trade_records)

        assert result["entry_markers"] == []
        assert result["exit_markers"] == []


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
        """tf == operating_tf → delegates to _compute_own_markers."""
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


# ── _compute_own_markers_filtered (via compute_bs_markers) ────────────

class TestComputeOwnMarkersFiltered:

    def test_operating_tf_confirmed_by_lower_schmitt(self):
        """操作周期交易被低一级同向确认 → 应标 BS."""
        dates = pd.date_range('2026-01-05', periods=20, freq='B')
        schmitt = {'sig': np.zeros(20)}
        trade_records = [
            {"type": "long", "entry_idx": 3, "exit_idx": 10,
             "return_pct": 3.0, "exit_reason": "pair_end"},
        ]
        # Lower TF has a confirming long pair within the trade window
        lower_dates = pd.date_range('2026-01-08', periods=10, freq='B')
        lower_sig = np.zeros(10); lower_sig[2:7] = 1  # long pair at bars 2-6
        lower_schmitt = {
            "all_pairs": [(2, 6)],
            "sig": lower_sig,
            "dates": lower_dates,
        }
        result = compute_bs_markers(
            np.arange(20, dtype=float), dates, schmitt, [],
            trade_records, '日线', '日线', lower_schmitt=lower_schmitt)
        assert len(result["entry_markers"]) == 1, (
            "Confirmed trade should have entry marker")
        assert result["entry_markers"][0][1] == "B"

    def test_operating_tf_not_confirmed_no_markers(self):
        """操作周期交易未被低一级同向确认 → 不标 BS."""
        dates = pd.date_range('2026-01-05', periods=20, freq='B')
        trade_records = [
            {"type": "long", "entry_idx": 3, "exit_idx": 10,
             "return_pct": 3.0, "exit_reason": "pair_end"},
        ]
        # Lower TF has only SHORT pairs (opposite direction)
        lower_dates = pd.date_range('2026-01-08', periods=10, freq='B')
        lower_sig = np.zeros(10); lower_sig[2:7] = -1  # SHORT pair
        lower_schmitt = {
            "all_pairs": [(2, 6)],
            "sig": lower_sig,
            "dates": lower_dates,
        }
        result = compute_bs_markers(
            np.arange(20, dtype=float), dates, None, [],
            trade_records, '日线', '日线', lower_schmitt=lower_schmitt)
        assert len(result["entry_markers"]) == 0, (
            "Unconfirmed trade should have NO markers")

    def test_operating_tf_no_lower_schmitt_unfiltered(self):
        """无低一级 Schmitt 数据 → 回退到不过滤（全部标记）."""
        dates = pd.date_range('2026-01-05', periods=20, freq='B')
        trade_records = [
            {"type": "short", "entry_idx": 5, "exit_idx": 15,
             "return_pct": -2.0, "exit_reason": "stop_loss"},
        ]
        result = compute_bs_markers(
            np.arange(20, dtype=float), dates, None, [],
            trade_records, '日线', '日线', lower_schmitt=None)
        assert len(result["entry_markers"]) == 1, (
            "Without lower schmitt, all trades should be marked")
        assert result["entry_markers"][0][1] == "S"

    def test_operating_tf_lower_pair_starts_before_trade_skipped(self):
        """低一级 pair 在交易窗口之前开始 → 不确认."""
        dates = pd.date_range('2026-01-10', periods=20, freq='B')
        trade_records = [
            {"type": "long", "entry_idx": 5, "exit_idx": 15,
             "return_pct": 3.0, "exit_reason": "pair_end"},
        ]
        # Lower TF pair starts BEFORE the trade entry date
        lower_dates = pd.date_range('2026-01-01', periods=10, freq='B')
        lower_sig = np.ones(10); lower_sig[0:5] = 1
        lower_schmitt = {
            "all_pairs": [(1, 4)],  # starts 2026-01-02, ends before trade
            "sig": lower_sig,
            "dates": lower_dates,
        }
        result = compute_bs_markers(
            np.arange(20, dtype=float), dates, None, [],
            trade_records, '日线', '日线', lower_schmitt=lower_schmitt)
        # Lower pair dates are before trade window → not confirmed
        assert len(result["entry_markers"]) == 0

    def test_cascade_unchanged_by_lower_schmitt(self):
        """lower_schmitt 参数不影响级联逻辑."""
        dates = pd.date_range('2026-01-05', periods=20, freq='B')
        sig = np.array([0,0,1,1,1,1,1,1,1,1,1,0,-1,-1,-1,-1,-1,-1,-1,-1])
        schmitt = {'sig': sig}
        higher_bs = {
            "entry_markers": [(2, "B", "green", pd.Timestamp('2026-01-07'))],
            "exit_markers": [],
        }
        # Pass lower_schmitt but we're NOT the operating TF → should be ignored
        result = compute_bs_markers(
            np.arange(20, dtype=float), dates, schmitt, [(2,9)],
            [], '60分钟', '日线', higher_bs=higher_bs,
            lower_schmitt={"all_pairs": [], "sig": np.array([]), "dates": None})
        # Cascade should still find the matching pair
        assert len(result["entry_markers"]) >= 1


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
