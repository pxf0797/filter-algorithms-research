"""
BS 仓位操作标识模块 — 单元测试
"""
import numpy as np
import pandas as pd
import pytest

from services.bs_marker import (
    _find_date_index,
    _compute_own_markers,
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
        """A stop-loss trade record produces an extra exit marker."""
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        schmitt = _make_schmitt([0, 0, 0, 1, 1, 1, 1, 0, 0, 0])
        all_pairs = [(3, 6)]             # long pair entry at 3, exit at 6
        trade_records = [
            {"exit_reason": "stop_loss", "exit_idx": 5, "type": "long"},
        ]

        result = _compute_own_markers(t, dates, schmitt, all_pairs, trade_records)

        assert len(result["exit_markers"]) == 2, (
            "Should have 2 exits: one pair_end + one stop_loss"
        )
        stop_loss_exits = [e for e in result["exit_markers"] if e[3] == "stop_loss"]
        assert len(stop_loss_exits) == 1
        assert stop_loss_exits[0] == (
            5, "S", "green", "stop_loss", dates[5],
        ), "Long stop_loss → green S at exit_idx"

    def test_short_stop_loss_exit(self):
        """Short stop-loss produces a red B exit marker."""
        dates = _make_dates("2024-01-01", periods=10)
        t = np.arange(10)
        schmitt = _make_schmitt([0, 0, 0, -1, -1, 0, 0, 0, 0, 0])
        all_pairs = [(3, 6)]
        trade_records = [
            {"exit_reason": "stop_loss", "exit_idx": 5, "type": "short"},
        ]

        result = _compute_own_markers(t, dates, schmitt, all_pairs, trade_records)

        stop_loss_exits = [e for e in result["exit_markers"] if e[3] == "stop_loss"]
        assert len(stop_loss_exits) == 1
        assert stop_loss_exits[0][:4] == (5, "B", "red", "stop_loss")

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
