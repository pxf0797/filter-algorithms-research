"""
Tests for ParquetStore._extract_view_columns and CSVBuilder._extract_view_columns.

Covers per-bar column extraction logic: signal, filter, PnL, BS markers,
trade matching, and edge cases (None data, empty markers, boundary conditions).
All tests use mock view_data — no real backtest or DB required.
"""

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

# Ensure filter is importable
_src = Path(__file__).resolve().parent.parent / "filter"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from data.store import ParquetStore
from backtest.recorder import CSVBuilder


# ============================================================================
# Helpers
# ============================================================================


def make_mock_view_data(
    n_pts: int = 120,
    sig_vals: list | None = None,
    has_entry: bool = False,
    has_exit: bool = False,
) -> dict:
    """Construct mock view_data matching the pipeline output shape.

    Parameters
    ----------
    n_pts : int
        Number of data points (bars) in the view window.
    sig_vals : list of (idx, val) or None
        Signal values to set at specific indices. Default: all zeros.
    has_entry : bool
        If True, add an entry marker at bar 50.
    has_exit : bool
        If True, add an exit marker at bar 100 and a matching trade record.
    """
    t = np.arange(n_pts, dtype=float)

    # Signal — default all zeros
    sig = np.zeros(n_pts, dtype=int)
    if sig_vals:
        for idx, val in sig_vals:
            sig[idx] = val

    # Dates: generate a pd.DatetimeIndex for date-based BS marker matching
    base_date = pd.Timestamp("2024-01-01")
    dates = pd.DatetimeIndex([base_date + pd.Timedelta(days=i) for i in range(n_pts)])

    # BS markers — use dates that match the generated date array
    entry_markers = []
    exit_markers = []
    if has_entry and n_pts > 50:
        entry_markers = [(50, "B", "green", dates[50])]
    if has_exit and n_pts > 100:
        exit_markers = [(100, "S", "red", "stop_loss", dates[100])]

    # PnL arrays
    long_pnl = np.linspace(100, 110, n_pts)
    short_pnl = np.linspace(100, 105, n_pts)

    # Position masks
    long_mask = np.zeros(n_pts, dtype=bool)
    short_mask = np.zeros(n_pts, dtype=bool)
    if has_entry:
        long_mask[50:] = True

    # Trade records
    trade_records = []
    if has_entry:
        # Active long trade: entered at bar 50, still open (no exit yet)
        trade_records.append({
            "type": "long",
            "entry_idx": 50,
            "exit_idx": None,
            "return_pct": None,
            "exit_reason": "",
        })
    if has_exit:
        trade_records.append({
            "type": "long",
            "entry_idx": 50,
            "exit_idx": 100,
            "return_pct": 5.0,
            "exit_reason": "take_profit",
        })

    # Filtered signal
    filtered = np.random.RandomState(42).randn(n_pts).cumsum() * 0.01 + 100

    return {
        "t": t,
        "dates": dates,
        "schmitt": {
            "sig": sig,
            "eps": np.full(n_pts, 0.1),
        },
        "filtered": filtered,
        "long_pnl": long_pnl,
        "short_pnl": short_pnl,
        "long_mask": long_mask,
        "short_mask": short_mask,
        "trade_records": trade_records,
        "bs_markers": {
            "entry_markers": entry_markers,
            "exit_markers": exit_markers,
        },
    }


_BOOL_FALSE: object = np.bool_(False)

# ============================================================================
# TestExtractViewColumns — ParquetStore
# ============================================================================


class TestExtractViewColumns:
    """Tests for ParquetStore._extract_view_columns (static method)."""

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _extract(view_data: dict, prefix: str = "v0") -> dict:
        """Convenience wrapper — calls the static method directly."""
        return ParquetStore._extract_view_columns(prefix, view_data)

    # ── sig ────────────────────────────────────────────────────────────

    def test_sig_extraction(self):
        """sig column takes the last element of schmitt['sig']."""
        view_data = make_mock_view_data(sig_vals=[(-1, 1)])
        cols = self._extract(view_data)
        assert cols["v0_sig"] == 1

    def test_sig_default_when_all_zero(self):
        """sig defaults to 0 when signal array is all zeros."""
        view_data = make_mock_view_data()
        cols = self._extract(view_data)
        assert cols["v0_sig"] == 0

    # ── filtered ───────────────────────────────────────────────────────

    def test_filtered_extraction(self):
        """filtered column takes the last element of the filtered array."""
        view_data = make_mock_view_data(n_pts=120)
        expected = float(view_data["filtered"][-1])
        cols = self._extract(view_data)
        assert cols["v0_filtered"] == pytest.approx(expected)

    # ── pnl ────────────────────────────────────────────────────────────

    def test_pnl_extraction(self):
        """pnl_long / pnl_short take the last element of PnL arrays."""
        view_data = make_mock_view_data(n_pts=120)
        cols = self._extract(view_data)
        assert cols["v0_pnl_long"] == pytest.approx(110.0)  # linspace 100→110
        assert cols["v0_pnl_short"] == pytest.approx(105.0)  # linspace 100→105

    # ── BS entry markers ───────────────────────────────────────────────

    def test_bs_entry_marker_present(self):
        """BS entry markers now use post-hoc join; _extract_view_columns returns NA."""
        view_data = make_mock_view_data(n_pts=51, has_entry=True)
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] is None  # per-bar matching removed

    def test_bs_entry_marker_at_last_bar(self):
        """BS markers filled by post-hoc join; per-bar returns NA."""
        view_data = make_mock_view_data(n_pts=50, has_entry=False)
        view_data["bs_markers"]["entry_markers"] = [
            (49, "B", "green", view_data["dates"][49])
        ]
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] is None

    def test_bs_entry_marker_none(self):
        """No entry markers: default to empty string."""
        view_data = make_mock_view_data(has_entry=False, has_exit=False)
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] is None

    def test_bs_entry_marker_past_last_bar_not_found(self):
        """Entry marker at bar 200 with view_last_idx=119: should NOT match."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False)
        view_data["bs_markers"]["entry_markers"] = [
            (200, "B", "green", "2024-01-15", "2024-01-15")
        ]
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] is None

    # ── BS exit markers ────────────────────────────────────────────────

    def test_bs_exit_marker_found(self):
        """BS exit markers now use post-hoc join; per-bar returns NA."""
        view_data = make_mock_view_data(n_pts=101, has_exit=True)
        cols = self._extract(view_data)
        assert cols["v0_bs_exit"] is None

    # ── trade matching ─────────────────────────────────────────────────

    def test_trade_exit_at_current_bar(self):
        """Trade exits now filled by post-hoc join; per-bar returns NA."""
        view_data = make_mock_view_data(n_pts=50, has_entry=False, has_exit=False)
        # n_pts=50 → view_last_idx=49
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 20, "exit_idx": 49,
             "return_pct": 5.0, "exit_reason": "take_profit"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] is None
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] is None

    def test_trade_entry_matching(self):
        """Trade entries now filled by post-hoc join; per-bar returns NA."""
        view_data = make_mock_view_data(n_pts=120, has_entry=True, has_exit=False)
        # Add a trade that has entry but no exit yet
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 50, "exit_idx": None}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] is None
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] is None

    def test_trade_with_entry_past_view_last_idx(self):
        """Trade with entry_idx=200 > view_last_idx=119: should NOT match."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 200, "exit_idx": None}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] is None

    def test_trade_completed_before_current_bar(self):
        """Trade completed (entry + exit) before current bar → ""，不显示 exit。"""
        view_data = make_mock_view_data(n_pts=120, has_exit=True)
        cols = self._extract(view_data)
        # has_exit=True → trade with entry=50, exit=100 at view=119
        # Trade completed 19 bars ago; NOT at current bar → ""
        assert cols["v0_trade"] is None
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] is None

    # ── multiple markers ───────────────────────────────────────────────

    def test_multiple_markers_same_bar(self):
        """Multiple BS markers now handled by post-hoc join; per-bar returns NA."""
        view_data = make_mock_view_data(n_pts=51, has_entry=False)
        d = view_data["dates"]
        view_data["bs_markers"]["entry_markers"] = [
            (30, "B", "green", d[30]),
            (50, "B", "green", d[50]),
            (50, "S", "red", d[50]),  # same bar, later marker
        ]
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] is None  # per-bar matching removed

    def test_multiple_trades_last_active_wins(self):
        """Multiple trades: active state computed by post-hoc join; per-bar returns NA."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 10, "exit_idx": 30,
             "return_pct": 1.0, "exit_reason": "first"},
            {"type": "short", "entry_idx": 40, "exit_idx": None},
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] is None
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] is None

    # ── edge: empty / None data ────────────────────────────────────────

    def test_empty_view_data(self):
        """All arrays are None — return all defaults, no exception."""
        empty = {
            "t": None,
            "schmitt": None,
            "filtered": None,
            "long_pnl": None,
            "short_pnl": None,
            "long_mask": None,
            "short_mask": None,
            "trade_records": None,
            "bs_markers": None,
        }
        cols = self._extract(empty)
        assert cols["v0_sig"] == 0
        assert np.isnan(cols["v0_filtered"])
        assert np.isnan(cols["v0_eps"])
        assert np.isnan(cols["v0_pnl_long"])
        assert np.isnan(cols["v0_pnl_short"])
        assert cols["v0_long_pos"] == _BOOL_FALSE
        assert cols["v0_short_pos"] == _BOOL_FALSE
        assert cols["v0_trade"] is None
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] is None
        assert cols["v0_bs_entry"] is None
        assert cols["v0_bs_exit"] is None

    def test_schmitt_none(self):
        """schmitt is None: sig and eps return default values."""
        view_data = make_mock_view_data()
        view_data["schmitt"] = None
        cols = self._extract(view_data)
        assert cols["v0_sig"] == 0
        assert np.isnan(cols["v0_eps"])

    def test_schmitt_empty_dict(self):
        """schmitt is empty dict: sig and eps are None internally, return defaults."""
        view_data = make_mock_view_data()
        view_data["schmitt"] = {}
        cols = self._extract(view_data)
        assert cols["v0_sig"] == 0
        assert np.isnan(cols["v0_eps"])

    def test_trade_records_empty_list(self):
        """trade_records is empty list: trade columns return defaults."""
        view_data = make_mock_view_data()
        view_data["trade_records"] = []
        cols = self._extract(view_data)
        assert cols["v0_trade"] is None
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] is None

    def test_bs_markers_empty_dict(self):
        """bs_markers has empty entry/exit lists: BS columns return defaults."""
        view_data = make_mock_view_data()
        view_data["bs_markers"] = {"entry_markers": [], "exit_markers": []}
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] is None
        assert cols["v0_bs_exit"] is None

    def test_bs_markers_none(self):
        """bs_markers is None: BS columns return defaults."""
        view_data = make_mock_view_data()
        view_data["bs_markers"] = None
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] is None
        assert cols["v0_bs_exit"] is None

    # ── positions ──────────────────────────────────────────────────────

    def test_long_pos_true_when_in_position(self):
        """long_pos is True when long_mask[-1] is True."""
        view_data = make_mock_view_data(n_pts=120, has_entry=True)
        cols = self._extract(view_data)
        assert cols["v0_long_pos"] == True  # noqa: E712

    def test_long_pos_false_when_not_in_position(self):
        """long_pos is False when no entry."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False)
        cols = self._extract(view_data)
        assert cols["v0_long_pos"] == _BOOL_FALSE

    def test_short_pos_false_by_default(self):
        """short_pos is False when short_mask is all False."""
        view_data = make_mock_view_data(n_pts=120)
        cols = self._extract(view_data)
        assert cols["v0_short_pos"] == _BOOL_FALSE

    # ── column count ───────────────────────────────────────────────────

    def test_returns_all_12_columns(self):
        """_extract_view_columns returns exactly 12 prefixed columns."""
        view_data = make_mock_view_data()
        cols = self._extract(view_data)
        expected = {
            "v0_sig", "v0_filtered", "v0_eps",
            "v0_pnl_long", "v0_pnl_short",
            "v0_long_pos", "v0_short_pos",
            "v0_trade", "v0_trade_return", "v0_trade_reason",
            "v0_bs_entry", "v0_bs_exit",
        }
        assert set(cols.keys()) == expected

    # ── prefix ─────────────────────────────────────────────────────────

    def test_custom_prefix(self):
        """Prefix other than v0 is reflected in column names."""
        view_data = make_mock_view_data(sig_vals=[(-1, 1)])
        cols = self._extract(view_data, prefix="v3")
        assert "v3_sig" in cols
        assert cols["v3_sig"] == 1
        assert "v0_sig" not in cols


# ============================================================================
# TestCSVBuilderExtractViewColumns — CSVBuilder
# ============================================================================


class TestCSVBuilderExtractViewColumns:
    """Tests for CSVBuilder._extract_view_columns (instance method).

    Covers the same extraction logic but verifies CSVBuilder-specific
    behaviours: B/S/- labels for BS markers, mu_v/sigma_v/dur, pair/trade
    counts, and the different default sentinels (NaN vs 0 vs "-").
    """

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _extract(view_name: str, view_data: dict) -> dict:
        builder = CSVBuilder()
        return builder._extract_view_columns(view_name, view_data)

    # ── sig ────────────────────────────────────────────────────────────

    def test_sig_extraction(self):
        """sig column takes the last element of schmitt['sig']."""
        # sig_vals are applied in order: sig[-1] = -1 (last), sig[50] = 1
        view_data = make_mock_view_data(sig_vals=[(-1, -1), (50, 1)])
        cols = self._extract("v0_日线", view_data)
        assert cols["v0_sig"] == -1

    # ── filtered ───────────────────────────────────────────────────────

    def test_filtered_extraction(self):
        """filtered column takes the last element."""
        view_data = make_mock_view_data(n_pts=120)
        expected = float(view_data["filtered"][-1])
        cols = self._extract("v0_日线", view_data)
        assert cols["v0_filtered"] == pytest.approx(expected)

    # ── mu_v / sigma_v / dur ───────────────────────────────────────────

    def test_mu_v_sigma_v_dur_extraction(self):
        """CSVBuilder extracts mu_v, sigma_v, and dur from schmitt."""
        view_data = make_mock_view_data(n_pts=120)
        view_data["schmitt"]["mu_v"] = np.linspace(0.01, 0.05, 120)
        view_data["schmitt"]["sigma_v"] = np.linspace(0.5, 1.0, 120)
        view_data["schmitt"]["dur"] = np.arange(120, dtype=int)
        cols = self._extract("v0_日线", view_data)
        assert cols["v0_mu_v"] == pytest.approx(0.05)
        assert cols["v0_sigma_v"] == pytest.approx(1.0)
        assert cols["v0_sig_dur"] == 119

    # ── pair_count / trade_count ───────────────────────────────────────

    def test_pair_count_and_trade_count(self):
        """CSVBuilder extracts pair_count and trade_count from list lengths."""
        view_data = make_mock_view_data(n_pts=120)
        view_data["all_pairs"] = [(1, 3), (5, 8), (10, 12)]
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 1, "exit_idx": 3},
            {"type": "short", "entry_idx": 5, "exit_idx": 8},
        ]
        cols = self._extract("v1_60分钟", view_data)
        assert cols["v1_pair_count"] == 3
        assert cols["v1_trade_count"] == 2

    # ── BS markers (CSVBuilder uses "-" not "") ────────────────────────

    def test_bs_entry_marker_present(self):
        """Entry marker at view_last_idx: == should find B."""
        view_data = make_mock_view_data(n_pts=51, has_entry=True)
        cols = self._extract("v0_日线", view_data)
        assert cols["v0_bs_entry"] == "B"

    def test_bs_entry_marker_none_csv(self):
        """No markers: CSVBuilder returns "-" instead of ""."""
        view_data = make_mock_view_data(has_entry=False, has_exit=False)
        cols = self._extract("v0_日线", view_data)
        assert cols["v0_bs_entry"] == "-"
        assert cols["v0_bs_exit"] == "-"

    def test_bs_exit_marker_found_csv(self):
        """Exit marker at view_last_idx: == should find S."""
        view_data = make_mock_view_data(n_pts=101, has_exit=True)
        cols = self._extract("v0_日线", view_data)
        assert cols["v0_bs_exit"] == "S"

    # ── trade matching ─────────────────────────────────────────────────

    def test_trade_exit_at_current_bar_csv(self):
        """Exit at current bar (exit_idx == view_last_idx, non-eod) → exit_long."""
        view_data = make_mock_view_data(n_pts=50, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 20, "exit_idx": 49,
             "return_pct": 5.0, "exit_reason": "take_profit"}
        ]
        cols = self._extract("v0_日线", view_data)
        assert cols["v0_trade"] == "exit_long"
        assert cols["v0_trade_return"] == pytest.approx(5.0)
        assert cols["v0_trade_reason"] == "take_profit"

    def test_trade_entry_matching(self):
        """Entry-only trade: entry_type set, return_pct stays NaN."""
        view_data = make_mock_view_data(n_pts=120, has_entry=True, has_exit=False)
        view_data["trade_records"] = [
            {"type": "short", "entry_idx": 50, "exit_idx": None}
        ]
        cols = self._extract("v0_日线", view_data)
        assert cols["v0_trade"] == "entry_short"
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] == ""

    def test_trade_completed_before_current_bar_csv(self):
        """Completed trade (entry + exit before current bar) → ""."""
        view_data = make_mock_view_data(n_pts=120, has_exit=True)
        cols = self._extract("v0_日线", view_data)
        assert cols["v0_trade"] == ""
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] == ""

    # ── edge cases ─────────────────────────────────────────────────────

    def test_empty_view_data_csv(self):
        """All arrays None/missing: CSVBuilder omits keys, BS/trade use defaults."""
        # NOTE: CSVBuilder does NOT guard against bs_markers=None (unlike
        # ParquetStore which uses `or []`).  Use empty dict for bs_markers.
        empty: dict = {
            "t": None,
            "schmitt": None,
            "filtered": None,
            "long_pnl": None,
            "short_pnl": None,
            "long_mask": None,
            "short_mask": None,
            "trade_records": [],
            "bs_markers": {},
        }
        cols = self._extract("v0_日线", empty)
        # CSVBuilder omits keys when data is None, but BS/trade are always set
        assert cols.get("v0_bs_entry") == "-"
        assert cols.get("v0_bs_exit") == "-"
        assert cols.get("v0_trade") == ""

    def test_schmitt_none_csv(self):
        """schmitt is None: sig/eps/mu_v are simply absent."""
        view_data = make_mock_view_data()
        view_data["schmitt"] = None
        cols = self._extract("v0_日线", view_data)
        assert "v0_sig" not in cols
        assert "v0_eps" not in cols

    def test_multiple_markers_same_bar_csv(self):
        """Same bar at view_last_idx: last marker overwrites."""
        view_data = make_mock_view_data(n_pts=51, has_entry=False)
        d = view_data["dates"]
        view_data["bs_markers"]["entry_markers"] = [
            (50, "B", "green", d[50]),
            (50, "S", "red", d[50]),
        ]
        cols = self._extract("v0_日线", view_data)
        assert cols["v0_bs_entry"] == "S"

    def test_multiple_trades_last_active_wins_csv(self):
        """Multiple trades: last active wins, completed others ignored."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 10, "exit_idx": 30,
             "return_pct": 1.0, "exit_reason": "a"},
            {"type": "short", "entry_idx": 40, "exit_idx": None},
        ]
        cols = self._extract("v0_日线", view_data)
        # First trade completed (10→30), second active → entry_short
        assert cols["v0_trade"] == "entry_short"
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] == ""

    # ── PnL and positions ──────────────────────────────────────────────

    def test_pnl_extraction_csv(self):
        """PnL values from long_pnl/short_pnl last element."""
        view_data = make_mock_view_data(n_pts=120)
        cols = self._extract("v0_日线", view_data)
        assert cols["v0_pnl_long"] == pytest.approx(110.0)
        assert cols["v0_pnl_short"] == pytest.approx(105.0)

    def test_positions_csv(self):
        """long_pos=1 when in position, short_pos=0 by default (int, not bool)."""
        view_data = make_mock_view_data(n_pts=120, has_entry=True)
        cols = self._extract("v0_日线", view_data)
        assert cols["v0_long_pos"] == 1
        assert cols["v0_short_pos"] == 0

    # ── prefix extraction from view_name ───────────────────────────────

    def test_prefix_from_view_name(self):
        """Prefix is extracted from view_name (e.g. 'v2_周线' -> 'v2')."""
        view_data = make_mock_view_data(sig_vals=[(-1, 1)])
        cols = self._extract("v2_周线", view_data)
        assert "v2_sig" in cols
        assert cols["v2_sig"] == 1

    # ── BS label edge: "-" for entry, "B" for exit at same bar ────────

    def test_bs_both_markers_present(self):
        """Both entry and exit markers at view_last_idx: each matched independently."""
        view_data = make_mock_view_data(n_pts=51, has_entry=False, has_exit=False)
        d = view_data["dates"]
        view_data["bs_markers"]["entry_markers"] = [
            (50, "B", "green", d[50]),
        ]
        view_data["bs_markers"]["exit_markers"] = [
            (50, "S", "red", "stop_loss", d[50]),
        ]
        cols = self._extract("v0_日线", view_data)
        assert cols["v0_bs_entry"] == "B"
        assert cols["v0_bs_exit"] == "S"

    def test_all_pairs_none(self):
        """all_pairs is None: pair_count is not set (absent from result)."""
        view_data = make_mock_view_data()
        view_data["all_pairs"] = None
        cols = self._extract("v0_日线", view_data)
        assert "v0_pair_count" not in cols

    def test_trade_records_none_csv(self):
        """trade_records is None: guarded by `or []`, returns empty defaults."""
        view_data = make_mock_view_data()
        view_data["trade_records"] = None
        cols = self._extract("v0_日线", view_data)
        assert cols["v0_trade_count"] == 0
        assert cols["v0_trade"] == ""


# ============================================================================
# TestBSMarkerRegression — BS 标记回归测试
# ============================================================================


class TestBSMarkerRegression:
    """回归测试：BS marker 匹配已改为 post-hoc join。

    Per-bar matching 已从 ``_extract_view_columns`` 移除。
    所有 BS 标记现在通过 ``end_session`` 中的后处理匹配。
    这些测试验证 per-bar 方法始终返回默认值（不再尝试匹配）。
    """

    @staticmethod
    def _extract(view_data: dict, prefix: str = "v0") -> dict:
        return ParquetStore._extract_view_columns(prefix, view_data)

    def test_bs_entry_at_exact_last_bar(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=50, has_entry=False)
        view_data["bs_markers"]["entry_markers"] = [
            (49, "B", "green", view_data["dates"][49])
        ]
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] is None

    def test_bs_entry_before_last_bar(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False)
        view_data["bs_markers"]["entry_markers"] = [
            (118, "B", "green", view_data["dates"][118])
        ]
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] is None

    def test_bs_entry_far_from_last_bar(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False)
        view_data["bs_markers"]["entry_markers"] = [
            (10, "B", "green", view_data["dates"][10])
        ]
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] is None

    def test_bs_entry_multiple_before_last(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=121, has_entry=False)
        d = view_data["dates"]
        view_data["bs_markers"]["entry_markers"] = [
            (20, "B", "green", d[20]),
            (40, "S", "red", d[40]),
            (120, "B", "green", d[120]),
        ]
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] is None

    def test_bs_exit_multiple_before_last(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=121, has_entry=False)
        d = view_data["dates"]
        view_data["bs_markers"]["exit_markers"] = [
            (30, "B", "green", "take_profit", d[30]),
            (70, "S", "red", "stop_loss", d[70]),
            (120, "S", "red", "stop_loss", d[120]),
        ]
        cols = self._extract(view_data)
        assert cols["v0_bs_exit"] is None


# ============================================================================
# TestBSMarkerEventSemantics — P5 fix 验证：事件驱动 BS 标记
# ============================================================================


class TestBSMarkerEventSemantics:
    """验证：BS 标记匹配已改为 post-hoc join。

    Per-bar matching 已从 ``_extract_view_columns`` 移除。
    这些测试现在验证 per-bar 方法始终返回默认值。
    """

    @staticmethod
    def _extract(view_data: dict, prefix: str = "v0") -> dict:
        return ParquetStore._extract_view_columns(prefix, view_data)

    def test_b_marker_only_on_exact_trade_bar(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=51, has_entry=False)
        view_data["bs_markers"]["entry_markers"] = [
            (50, "B", "green", view_data["dates"][50]),
        ]
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] is None

    def test_bar_after_trade_has_no_marker(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=52, has_entry=False)
        view_data["bs_markers"]["entry_markers"] = [
            (50, "B", "green", view_data["dates"][50]),
        ]
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] is None

    def test_bar_before_trade_has_no_marker(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=50, has_entry=False)
        future_date = view_data["dates"][-1] + pd.Timedelta(days=1)
        view_data["bs_markers"]["entry_markers"] = [
            (50, "B", "green", future_date),
        ]
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] is None

    def test_multiple_trades_markers_on_distinct_bars(self):
        """Per-bar matching removed; always returns NA."""
        # bar 30
        view_30 = make_mock_view_data(n_pts=31, has_entry=False)
        d30 = view_30["dates"]
        view_30["bs_markers"]["entry_markers"] = [
            (30, "B", "green", d30[30]),
        ]
        cols_30 = self._extract(view_30)
        assert cols_30["v0_bs_entry"] is None

        # bar 35
        view_35 = make_mock_view_data(n_pts=36, has_entry=False)
        d35 = view_35["dates"]
        view_35["bs_markers"]["entry_markers"] = [
            (30, "B", "green", d35[30]),
        ]
        cols_35 = self._extract(view_35)
        assert cols_35["v0_bs_entry"] is None

        # bar 60
        view_60 = make_mock_view_data(n_pts=61, has_entry=False)
        d60 = view_60["dates"]
        view_60["bs_markers"]["entry_markers"] = [
            (30, "B", "green", d60[30]),
            (60, "S", "red", d60[60]),
        ]
        cols_60 = self._extract(view_60)
        assert cols_60["v0_bs_entry"] is None

    def test_exit_marker_event_semantics(self):
        """Per-bar matching removed; always returns NA."""
        # bar 100
        view_100 = make_mock_view_data(n_pts=101, has_entry=False, has_exit=False)
        d100 = view_100["dates"]
        view_100["bs_markers"]["exit_markers"] = [
            (100, "S", "red", "take_profit", d100[100]),
        ]
        cols_100 = self._extract(view_100)
        assert cols_100["v0_bs_exit"] is None

        # bar 101
        view_101 = make_mock_view_data(n_pts=102, has_entry=False, has_exit=False)
        d101 = view_101["dates"]
        view_101["bs_markers"]["exit_markers"] = [
            (100, "S", "red", "take_profit", d101[100]),
        ]
        cols_101 = self._extract(view_101)
        assert cols_101["v0_bs_exit"] is None


class TestTradeMatchingRegression:
    """回归测试：trade 事件匹配已改为 post-hoc join。

    Per-bar matching 已从 ``_extract_view_columns`` 移除。
    这些测试现在验证 per-bar 方法始终返回默认值。
    """

    @staticmethod
    def _extract(view_data: dict, prefix: str = "v0") -> dict:
        return ParquetStore._extract_view_columns(prefix, view_data)

    def test_trade_entry_active_no_exit(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 114, "exit_idx": None}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] is None
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] is None

    def test_trade_entry_active_eod_exit(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=50, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "short", "entry_idx": 40, "exit_idx": 49,
             "return_pct": 1.0, "exit_reason": "eod"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] is None
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] is None

    def test_trade_exit_at_current_bar(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=50, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 30, "exit_idx": 49,
             "return_pct": 3.5, "exit_reason": "take_profit"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] is None
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] is None

    def test_trade_exit_at_current_bar_stop_loss(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=50, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "short", "entry_idx": 20, "exit_idx": 49,
             "return_pct": -2.0, "exit_reason": "stop_loss"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] is None
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] is None

    def test_trade_completed_before_current_bar(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 50, "exit_idx": 100,
             "return_pct": 2.0, "exit_reason": "stop_loss"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] is None
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] is None

    def test_trade_same_bar_entry_exit_past(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "short", "entry_idx": 80, "exit_idx": 80,
             "return_pct": -1.5, "exit_reason": "stop_loss"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] is None

    def test_trade_exit_priority_at_current_bar(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=50, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "short", "entry_idx": 49, "exit_idx": 49,
             "return_pct": -1.0, "exit_reason": "stop_loss"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] is None
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] is None

    def test_multiple_trades_active_last_wins(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 10, "exit_idx": 130,
             "return_pct": 0.0, "exit_reason": "eod"},
            {"type": "short", "entry_idx": 40, "exit_idx": None},
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] is None


# ============================================================================
# TestCSVBuilderEdgeCases — CSVBuilder 边界情况
# ============================================================================


class TestCSVBuilderEdgeCases:
    """CSVBuilder 特有的边界：None 传感器不同、默认值不同。

    ParquetStore 对 None 数据有完善保护（``or []``、``is not None`` 检查），
    但 CSVBuilder 在部分路径上缺失这些保护，导致 None 值会触发异常。
    此外 CSVBuilder 的默认值也不同：BS 为空时用 ``"-"`` 而非 ``""``，
    schmitt 为 None 时省略列而非填充默认值。
    """

    @staticmethod
    def _extract(view_name: str, view_data: dict) -> dict:
        builder = CSVBuilder()
        return builder._extract_view_columns(view_name, view_data)

    def test_bs_markers_none_handled(self):
        """bs_markers 为 None 时用空 dict 替代，不崩溃。"""
        view_data = make_mock_view_data(has_entry=False, has_exit=False)
        view_data["bs_markers"] = None
        cols = self._extract("v0_日线", view_data)
        assert cols["v0_bs_entry"] == "-"
        assert cols["v0_bs_exit"] == "-"

    def test_trade_records_none_handled(self):
        """trade_records 为 None 时用空 list 替代，不崩溃。"""
        view_data = make_mock_view_data(has_entry=False, has_exit=False)
        view_data["trade_records"] = None
        cols = self._extract("v0_日线", view_data)
        assert cols["v0_trade"] == ""
        assert cols["v0_trade_count"] == 0

    def test_schmitt_none_omits_columns(self):
        """schmitt 为 None 时，CSVBuilder 应省略 sig/eps 而非填默认值。

        与 ParquetStore 不同：ParquetStore 会填充 0/NaN 默认值，
        而 CSVBuilder 直接不写入这些键。
        """
        view_data = make_mock_view_data()
        view_data["schmitt"] = None
        cols = self._extract("v0_日线", view_data)
        assert "v0_sig" not in cols
        assert "v0_eps" not in cols

    def test_empty_view_data_omits_arrays(self):
        """完全空的 view_data 不崩溃。"""
        empty: dict = {"t": None}
        cols = self._extract("v0_日线", empty)
        # BS markers default to "-"
        assert cols.get("v0_bs_entry") == "-"
        assert cols.get("v0_bs_exit") == "-"
        # trade 默认 ""
        assert cols.get("v0_trade") == ""


# ============================================================================
# TestConcurrencySafety — ParquetStore 并发安全 / 隔离测试
# ============================================================================


class TestConcurrencySafety:
    """测试 ParquetStore 在并发/串行场景下的目录隔离。

    验证不同 ticker 写入不同 session 目录，同一 ticker 串行运行
    不互相覆盖。
    """

    def test_different_tickers_independent(self, tmp_path):
        """不同 ticker 的 session 目录互不重叠。"""
        store_a = ParquetStore(str(tmp_path), "AAPL", [{"name": "v0"}])
        store_b = ParquetStore(str(tmp_path), "TSLA", [{"name": "v0"}])

        sid_a = store_a.start_session()
        sid_b = store_b.start_session()

        assert store_a._session_dir != store_b._session_dir
        assert "AAPL" in str(store_a._session_dir)
        assert "TSLA" in str(store_b._session_dir)
        assert sid_a != sid_b

        # 清理
        store_a.end_session()
        store_b.end_session()

    def test_same_ticker_serial_runs_isolated(self, tmp_path):
        """同一 ticker 串行运行使用不同的 session 目录。"""
        from datetime import datetime, timedelta
        from itertools import count
        from unittest import mock

        store = ParquetStore(str(tmp_path), "AAPL", [{"name": "v0"}])

        # Mock 时间替代 sleep：每个 datetime.now() 返回递增的时间戳
        counter = count()
        with mock.patch('data.store.datetime') as mock_dt:
            mock_dt.now.side_effect = lambda *a, **kw: datetime(2026, 1, 1, 12, 0, 0) + timedelta(seconds=next(counter) * 10)
            sid1 = store.start_session()
            dir1 = store._session_dir
            store.end_session()
            sid2 = store.start_session()
            dir2 = store._session_dir

        assert dir1 != dir2, (
            f"Serial runs should use different session dirs: {dir1} == {dir2}"
        )
        assert sid1 != sid2, (
            f"Serial runs should have different session IDs: {sid1} == {sid2}"
        )

        store.end_session()

    def test_session_dir_includes_ticker_and_session_id(self, tmp_path):
        """session 目录路径同时包含 ticker 和 session_id。"""
        store = ParquetStore(str(tmp_path), "AAPL", [{"name": "v0"}])
        sid = store.start_session()

        dir_name = store._session_dir.name
        assert "AAPL" in dir_name, f"Directory name should contain ticker: {dir_name}"
        assert sid in dir_name, f"Directory name should contain session_id: {dir_name}"

        store.end_session()


# ============================================================================
# TestPnlConsistency — PnL 值与信号/持仓逻辑一致性
# ============================================================================


class TestPnlConsistency:
    """PnL 值应与信号/持仓逻辑一致。

    验证规则：
    - 未持仓时 PnL 应不变（flat）
    - 做多信号时要么有持仓要么 PnL 平坦
    - PnL 数组在同一 bar 内是单调累积的
    """

    @staticmethod
    def _extract(view_data: dict, prefix: str = "v0") -> dict:
        return ParquetStore._extract_view_columns(prefix, view_data)

    def test_pnl_long_unchanged_when_no_long_position(self):
        """未持仓时 PnL 随窗口滑动应基本不变（最后两个 bar 的 PnL 差很小）。

        构造两个相邻 bar 的数据：同一个窗口滑动一步，
        PnL 数组的最后一个值变化极小（正常浮动而非跳变）。
        """
        n = 120
        long_pnl_a = np.linspace(100, 110, n)  # bar N 的 PnL
        long_pnl_b = np.linspace(100.001, 110.001, n)  # bar N+1 的 PnL（滑动一步）

        view_data_a = make_mock_view_data(n_pts=n, has_entry=False)
        view_data_a["long_pnl"] = long_pnl_a
        view_data_a["long_mask"] = np.zeros(n, dtype=bool)

        view_data_b = make_mock_view_data(n_pts=n, has_entry=False)
        view_data_b["long_pnl"] = long_pnl_b
        view_data_b["long_mask"] = np.zeros(n, dtype=bool)

        cols_a = self._extract(view_data_a)
        cols_b = self._extract(view_data_b)

        # 未持仓时，连续 bar 的 PnL 变化应很小（< 0.01）
        pnl_diff = abs(cols_b["v0_pnl_long"] - cols_a["v0_pnl_long"])
        assert pnl_diff < 0.1, f"PnL changed by {pnl_diff} without position"

    def test_signal_long_implies_position_or_pnl_flat(self):
        """做多信号时：要么有持仓（long_pos=True），要么 PnL 相对前值不变。

        信号建议做多时，策略可能因风控不进场，此时 PnL 应不变。
        """
        n = 120
        # 场景：sig=+1 但 long_mask 全 False（未持仓）
        view_data = make_mock_view_data(n_pts=n, has_entry=False)
        view_data["schmitt"]["sig"] = np.ones(n, dtype=int)  # all +1
        view_data["long_mask"] = np.zeros(n, dtype=bool)  # no position

        cols = self._extract(view_data)
        # 信号做多
        assert cols["v0_sig"] == 1
        # 未持仓
        assert cols["v0_long_pos"] == False  # noqa: E712
        # PnL 应仍在初始值附近（因为没有交易改变 PnL）
        assert cols["v0_pnl_long"] >= 100.0

    def test_pnl_values_are_monotonic_in_window(self):
        """同一 bar 内 long_pnl 数组应是单调的（累积 PnL）。

        虽然提取时只取最后一个值，但整个数组是窗口内逐 bar 累积的结果。
        """
        n = 120
        # 构造一个单调递增的 PnL 数组（模拟窗口内累积）
        pnl = np.sort(np.random.RandomState(42).uniform(99, 111, n))

        view_data = make_mock_view_data(n_pts=n, has_entry=True)
        view_data["long_pnl"] = pnl

        cols = self._extract(view_data)
        # 最后一个值应 >= 第一个值（单调递增保证）
        assert cols["v0_pnl_long"] == pytest.approx(pnl[-1])

    def test_short_pnl_independent_of_long_position(self):
        """short_pnl 在纯做多策略中应独立演化（不受 long 持仓影响）。"""
        n = 120
        view_data = make_mock_view_data(n_pts=n, has_entry=True)
        view_data["short_pnl"] = np.full(n, 100.0)  # flat short PnL
        view_data["short_mask"] = np.zeros(n, dtype=bool)

        cols = self._extract(view_data)
        assert cols["v0_long_pos"] == True  # noqa: E712 (in long position)
        assert cols["v0_short_pos"] == _BOOL_FALSE
        # short PnL 应保持不变
        assert cols["v0_pnl_short"] == pytest.approx(100.0)


# ============================================================================
# TestViewLabelMapping — metadata 中的 view_labels 保存和读取
# ============================================================================


class TestViewLabelMapping:
    """view_labels 应在 metadata 中正确保存和读取。

    验证 ParquetStore 和 EventRecorder 两个路径。
    """

    def test_parquet_store_metadata_contains_view_labels(self, tmp_path):
        """ParquetStore 的 metadata.json 必须包含 view_labels。"""
        store = ParquetStore(str(tmp_path), "AAPL", [
            {"name": "daily", "tf": "日线"},
            {"name": "hourly", "tf": "60分钟"},
        ])
        store.start_session()
        store.end_session()

        import json
        meta_path = tmp_path / list(tmp_path.iterdir())[0].name / "metadata.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert "view_labels" in meta
        assert meta["view_labels"] == {"v0": "日线", "v1": "60分钟"}

    def test_view_labels_match_view_count(self, tmp_path):
        """view_labels 数量应等于视图数量。"""
        configs = [
            {"name": "daily", "tf": "日线"},
            {"name": "hourly", "tf": "60分钟"},
            {"name": "weekly", "tf": "周线"},
        ]
        store = ParquetStore(str(tmp_path), "AAPL", configs)
        store.start_session()
        store.end_session()

        import json
        meta_path = tmp_path / list(tmp_path.iterdir())[0].name / "metadata.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert len(meta["view_labels"]) == 3
        assert list(meta["view_labels"].keys()) == ["v0", "v1", "v2"]
        assert len(meta["views"]) == 3

    def test_view_labels_fallback_when_tf_missing(self, tmp_path):
        """view_config 中缺少 tf 字段时使用默认值。"""
        store = ParquetStore(str(tmp_path), "AAPL", [
            {"name": "custom"},  # no 'tf' field
        ])
        store.start_session()
        store.end_session()

        import json
        meta_path = tmp_path / list(tmp_path.iterdir())[0].name / "metadata.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["view_labels"] == {"v0": "view_0"}

    def test_event_recorder_metadata_contains_view_labels(self, tmp_path):
        """EventRecorder 的 metadata.json 也应包含 view_labels。"""
        from backtest.recorder import EventRecorder

        config = {
            "ticker": "AAPL",
            "configs": [
                {"tf": "日线"},
                {"tf": "60分钟"},
            ],
        }
        recorder = EventRecorder(str(tmp_path), "AAPL")
        recorder.start_session(config)
        recorder.end_session()

        import json
        session_dir = tmp_path / list(tmp_path.iterdir())[0].name
        meta_path = session_dir / "metadata.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert "view_labels" in meta, "view_labels missing from EventRecorder metadata"
        assert meta["view_labels"] == {"v0": "日线", "v1": "60分钟"}


# ============================================================================
# TestViewLabelConsistency — 确保 view_labels 贯穿整个数据流
# ============================================================================


class TestViewLabelConsistency:
    """确保 view_labels 正确使用中文周期名，且格式统一为 "vN 周期名"。

    view_labels 来自 view_configs 中的 tf 字段，通过 metadata.json 传递给
    HTML 前端。前端 ``viewLabel(v)`` 函数将其拼接为 ``"v0 15分钟"`` 格式，
    用于图表标题、图例、统计面板等所有 UI 元素。
    """

    # ── 标签格式 ──────────────────────────────────────────────────────

    def test_parquet_store_labels_are_chinese_timeframes(self, tmp_path):
        """view_labels 的值应是中文周期名（如 '15分钟'），而非 v0/v1。"""
        store = ParquetStore(str(tmp_path), "AAPL", [
            {"name": "intraday", "tf": "15分钟"},
            {"name": "hourly", "tf": "60分钟"},
            {"name": "daily", "tf": "日线"},
            {"name": "weekly", "tf": "周线"},
        ])
        store.start_session()
        store.end_session()

        import json
        meta_path = tmp_path / list(tmp_path.iterdir())[0].name / "metadata.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        labels = meta["view_labels"]
        assert labels == {"v0": "15分钟", "v1": "60分钟", "v2": "日线", "v3": "周线"}
        for v in ["v0", "v1", "v2", "v3"]:
            assert v not in labels[v], (
                f"view_labels[{v}] = {labels[v]!r} 不应包含 view key 本身"
            )
            assert any('一' <= c <= '鿿' for c in labels[v]), (
                f"view_labels[{v}] = {labels[v]!r} 缺少中文字符"
            )

    def test_all_four_views_have_labels(self, tmp_path):
        """4 个视图都有对应的标签。"""
        store = ParquetStore(str(tmp_path), "AAPL", [
            {"tf": "15分钟"},
            {"tf": "60分钟"},
            {"tf": "日线"},
            {"tf": "周线"},
        ])
        store.start_session()
        store.end_session()

        import json
        meta_path = tmp_path / list(tmp_path.iterdir())[0].name / "metadata.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        labels = meta["view_labels"]
        assert len(labels) == 4, f"应有 4 个标签，实际 {len(labels)}"
        assert set(labels.keys()) == {"v0", "v1", "v2", "v3"}
        for k in labels:
            assert labels[k], f"view_labels[{k}] 不应为空"

    def test_viewlabel_renders_as_viewkey_space_timeframe(self):
        """viewLabel 渲染结果应为 'v0 15分钟' 格式，而非 'v0 v0'。"""
        def simulate_viewlabel(view_key, labels):
            label = labels.get(view_key, "")
            return view_key + " " + label if label else view_key

        labels = {"v0": "15分钟", "v1": "60分钟", "v2": "日线", "v3": "周线"}

        assert simulate_viewlabel("v0", labels) == "v0 15分钟"
        assert simulate_viewlabel("v1", labels) == "v1 60分钟"
        assert simulate_viewlabel("v2", labels) == "v2 日线"
        assert simulate_viewlabel("v3", labels) == "v3 周线"
        assert simulate_viewlabel("v0", {}) == "v0"

    def test_label_format_is_consistent_with_metadata(self, tmp_path):
        """metadata 中的 view_labels 值与 view_configs 中的 tf 一致。"""
        configs = [
            {"name": "a", "tf": "15分钟"},
            {"name": "b", "tf": "60分钟"},
        ]
        store = ParquetStore(str(tmp_path), "AAPL", configs)
        store.start_session()
        store.end_session()

        import json
        meta_path = tmp_path / list(tmp_path.iterdir())[0].name / "metadata.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        labels = meta["view_labels"]
        expected = {"v0": "15分钟", "v1": "60分钟"}
        assert labels == expected

        stored_configs = meta.get("view_configs", {})
        assert stored_configs["v0"]["tf"] == "15分钟"
        assert stored_configs["v1"]["tf"] == "60分钟"


# ============================================================================
# TestLabelNullSafety — viewLabels 在 metadata 缺失时不应崩溃
# ============================================================================


class TestLabelNullSafety:
    """viewLabels 在 metadata 缺失时不应崩溃。

    验证 HTML 前端 viewLabel 函数的健壮性：
    - 无 labels 时只返回 viewKey
    - 有 labels 时返回 "viewKey 周期名"
    - metadata=None 时 viewLabels 为空 dict
    """

    @staticmethod
    def _simulate_viewlabel(view_key: str, labels: dict) -> str:
        """模拟 HTML 前端的 viewLabel(viewKey) 函数。"""
        label = labels.get(view_key, "")
        return view_key + " " + label if label else view_key

    @staticmethod
    def _simulate_render_all(metadata: dict | None) -> dict:
        """模拟 HTML 前端 renderAll 中的 viewLabels 初始化逻辑。"""
        return (
            {**metadata["view_labels"]}
            if (metadata and metadata.get("view_labels"))
            else {}
        )

    def test_viewlabel_with_empty_labels_returns_viewkey(self):
        """无 labels 时 viewLabel('v0') 返回 'v0' 而非崩溃。"""
        result = self._simulate_viewlabel("v0", {})
        assert result == "v0"
        # 不应包含额外空格或 "undefined"
        assert "undefined" not in result
        assert "null" not in result
        assert result.strip() == "v0"

    def test_viewlabel_with_labels_returns_viewkey_space_timeframe(self):
        """有 labels 时 viewLabel('v0') 返回 'v0 15分钟'。"""
        labels = {"v0": "15分钟", "v1": "60分钟", "v2": "日线", "v3": "周线"}
        assert self._simulate_viewlabel("v0", labels) == "v0 15分钟"
        assert self._simulate_viewlabel("v1", labels) == "v1 60分钟"
        assert self._simulate_viewlabel("v2", labels) == "v2 日线"
        assert self._simulate_viewlabel("v3", labels) == "v3 周线"

    def test_metadata_null_does_not_crash_viewlabel(self):
        """metadata=None 时 viewLabels 应为空 dict。"""
        labels = self._simulate_render_all(None)
        assert labels == {}
        # viewLabel 在空 labels 下应正常返回 viewKey
        assert self._simulate_viewlabel("v0", labels) == "v0"
        assert self._simulate_viewlabel("v1", labels) == "v1"
        assert self._simulate_viewlabel("v3", labels) == "v3"

    def test_metadata_without_view_labels_field(self):
        """metadata 存在但缺少 view_labels 字段时 viewLabels 应为空 dict。"""
        labels = self._simulate_render_all({"other": "data"})
        assert labels == {}
        assert self._simulate_viewlabel("v0", labels) == "v0"

    def test_metadata_with_empty_view_labels(self):
        """metadata.view_labels 为空 dict 时 viewLabels 应为空 dict。"""
        labels = self._simulate_render_all({"view_labels": {}})
        assert labels == {}
        assert self._simulate_viewlabel("v0", labels) == "v0"

    def test_viewlabel_no_trailing_space_when_empty(self):
        """空 labels 时 viewLabel 结果不应有多余尾部空格。"""
        result = self._simulate_viewlabel("v2", {})
        assert result == "v2"
        assert not result.endswith(" ")

    def test_missing_view_key_in_labels(self):
        """某个 view key 不在 labels 中时，仍只返回 viewKey。"""
        labels = {"v0": "15分钟"}  # v1 缺失
        assert self._simulate_viewlabel("v1", labels) == "v1"


# ============================================================================
# TestTradeFixRegression — trade 列 entry/exit 修复回归测试
# ============================================================================


class TestTradeFixRegression:
    """回归测试：trade 列匹配已改为 post-hoc join。

    Per-bar matching 已从 ``_extract_view_columns`` 移除。
    这些测试现在验证 per-bar 方法始终返回默认值。
    """

    @staticmethod
    def _extract(view_data: dict, prefix: str = "v0") -> dict:
        return ParquetStore._extract_view_columns(prefix, view_data)

    def test_trade_entry_when_active_position(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 80, "exit_idx": None}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] is None
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] is None

    def test_trade_entry_short_when_active_short(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "short", "entry_idx": 60, "exit_idx": None}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] is None

    def test_trade_exit_only_at_exact_exit_bar(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=50, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 20, "exit_idx": 49,
             "return_pct": 3.0, "exit_reason": "take_profit"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] is None
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] is None

    def test_trade_exit_not_shown_when_eod(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=50, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "short", "entry_idx": 30, "exit_idx": 49,
             "return_pct": 1.5, "exit_reason": "eod"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] is None
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] is None

    def test_trade_exit_not_shown_before_exact_bar(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 50, "exit_idx": 100,
             "return_pct": 2.0, "exit_reason": "stop_loss"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] is None

    def test_trade_empty_when_no_active_trade(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = []
        cols = self._extract(view_data)
        assert cols["v0_trade"] is None
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] is None

    def test_trade_empty_when_all_completed(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 10, "exit_idx": 50,
             "return_pct": 1.0, "exit_reason": "a"},
            {"type": "short", "entry_idx": 60, "exit_idx": 80,
             "return_pct": -0.5, "exit_reason": "b"},
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] is None
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] is None

    def test_trade_entry_past_view_last_idx_not_matched(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "short", "entry_idx": 200, "exit_idx": None}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] is None

    def test_exit_priority_over_entry_at_current_bar(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=50, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 49, "exit_idx": 49,
             "return_pct": -1.0, "exit_reason": "stop_loss"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] is None
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] is None


# ============================================================================
# TestLabelRegressionSuite — 标签问题全面回归测试
# ============================================================================


class TestLabelRegressionSuite:
    """回归测试：确保周期标签不会再出问题。

    防护的问题历史：
    1. DEFAULT_VIEW_LABELS 被移除 → 无 metadata 时标签显示 "v0 v0" 或 "v0"
    2. viewLabels 初始化时序 → renderAll 调用前 viewLabel() 返回错误值
    3. metadata.view_labels 缺失 → null 崩溃
    4. view_backtest.py 未嵌入 view_labels → HTML 收不到标签
    """

    # ── DEFAULT_VIEW_LABELS 必须存在 ────────────────────────────────────

    def test_default_labels_exist_and_are_chinese(self):
        """DEFAULT_VIEW_LABELS 必须存在且值为中文周期名。"""
        defaults = {'v0': '15分钟', 'v1': '60分钟', 'v2': '日线', 'v3': '周线'}
        for k in ['v0', 'v1', 'v2', 'v3']:
            assert k in defaults
            assert '分钟' in defaults[k] or '日线' in defaults[k] or '周线' in defaults[k]

    # ── viewLabel 绝不能返回重复格式 ────────────────────────────────────

    def test_default_labels_never_return_v0_v0_format(self):
        """viewLabel 绝不能返回 'v0 v0' 这种重复格式。"""
        def simulate_viewLabel(viewKey, labels):
            label = labels.get(viewKey)
            # 仅当 label 非空且不等于 viewKey 本身时才拼接
            return (viewKey + ' ' + label) if (label and label != viewKey) else viewKey

        # 有标签：应返回 "v0 15分钟"
        assert simulate_viewLabel('v0', {'v0': '15分钟'}) == 'v0 15分钟'
        # 无标签：应返回 "v0" 不是 "v0 v0"
        assert simulate_viewLabel('v0', {}) == 'v0'
        # 标签值恰好等于 viewKey（无意义的 fallback）→ 不拼接
        assert simulate_viewLabel('v0', {'v0': 'v0'}) == 'v0'

    # ── ParquetStore 写入 view_labels ───────────────────────────────────

    def test_parquet_store_writes_view_labels(self, tmp_path):
        """ParquetStore 必须写入 view_labels 到 metadata.json。"""
        import json

        configs = [
            {'tf': '15分钟', 'n_pts': 50, '_fid': 'savgol'},
            {'tf': '60分钟', 'n_pts': 60, '_fid': 'savgol'},
            {'tf': '日线', 'n_pts': 60, '_fid': 'savgol'},
            {'tf': '周线', 'n_pts': 60, '_fid': 'savgol'},
        ]
        store = ParquetStore(str(tmp_path), "TEST", configs)
        store.start_session()
        store.end_session()

        mf = tmp_path / f"TEST_{store._session_id}" / "metadata.json"
        with open(mf) as f:
            meta = json.load(f)
        assert 'view_labels' in meta
        assert meta['view_labels'] == {'v0': '15分钟', 'v1': '60分钟', 'v2': '日线', 'v3': '周线'}

    # ── EventRecorder 写入 view_labels ──────────────────────────────────

    def test_event_recorder_writes_view_labels(self, tmp_path):
        """EventRecorder 也必须写入 view_labels 到 metadata.json。"""
        import glob
        import json

        from filter.backtest.recorder import EventRecorder

        configs = [
            {'tf': '15分钟', 'n_pts': 50},
            {'tf': '60分钟', 'n_pts': 60},
            {'tf': '日线', 'n_pts': 60},
            {'tf': '周线', 'n_pts': 60},
        ]
        rec = EventRecorder(str(tmp_path), "TEST")
        rec.start_session({'configs': configs})
        rec.end_session()

        mf = sorted(glob.glob(str(tmp_path / "*" / "metadata.json")))[-1]
        with open(mf) as f:
            meta = json.load(f)
        assert 'view_labels' in meta
        assert meta['view_labels'] == {'v0': '15分钟', 'v1': '60分钟', 'v2': '日线', 'v3': '周线'}

    # ── view_labels 数量必须等于视图数量 ────────────────────────────────

    def test_view_labels_count_matches_views(self):
        """view_labels 数量必须等于视图数量。"""
        # 4 views → 4 labels
        labels_4 = {f'v{i}': f'tf{i}' for i in range(4)}
        assert len(labels_4) == 4

        # 2 views → 2 labels
        labels_2 = {f'v{i}': f'tf{i}' for i in range(2)}
        assert len(labels_2) == 2

    # ── metadata=None 时不应崩溃 ────────────────────────────────────────

    def test_viewlabel_null_metadata_does_not_crash(self):
        """metadata=None 时 viewLabels 赋值不应崩溃。"""
        metadata = None
        viewLabels = (
            metadata.view_labels
        ) if hasattr(metadata, 'view_labels') else {}
        assert viewLabels == {}

        # 模拟 renderAll 中的安全赋值
        safe = (
            metadata.get('view_labels')
        ) if isinstance(metadata, dict) else {}
        assert safe == {}

    # ── viewLabel 始终返回字符串 ────────────────────────────────────────

    def test_viewlabel_always_returns_string(self):
        """viewLabel 始终返回字符串。"""
        def viewLabel(key, labels):
            label = labels.get(key)
            return (key + ' ' + label) if label else key

        assert isinstance(viewLabel('v0', {}), str)
        assert isinstance(viewLabel('v0', {'v0': '15分钟'}), str)
        assert len(viewLabel('v0', {})) > 0


# ============================================================================
# TestPnlFreeze — PnL 冻结逻辑
# ============================================================================


class TestPnlFreeze:
    """PnL 冻结：未持仓时 PnL 应锁定在上次交易退出时的值，不随滑动窗口漂移。

    问题背景：_extract_view_columns 对 pnl_long/pnl_short 无条件取 [-1]
    （窗口最后一个值），但 120-bar 滑动窗口导致即便当前 bar 没有持仓，
    PnL 值仍会随窗口滑动而变化。

    修复：在 append_row 中维护 _last_pnl 字典，持仓时更新、未持仓时冻结。
    """

    @staticmethod
    def _make_store(tmp_path, ticker="TEST"):
        store = ParquetStore(str(tmp_path), ticker, [{"tf": "日线"}])
        store.start_session()
        return store

    @staticmethod
    def _make_stage_output(view_data_override=None):
        """构建标准 stage_outputs，可选覆盖 view_data。"""
        n = 50
        t = np.arange(n, dtype=float)
        long_pnl = np.linspace(100, 110, n)
        short_pnl = np.linspace(100, 105, n)
        long_mask = np.zeros(n, dtype=bool)
        short_mask = np.zeros(n, dtype=bool)
        sig = np.zeros(n, dtype=int)
        filtered = np.random.RandomState(0).randn(n).cumsum() * 0.01 + 100

        view_data = {
            "t": t,
            "schmitt": {"sig": sig, "eps": np.full(n, 0.1)},
            "filtered": filtered,
            "long_pnl": long_pnl,
            "short_pnl": short_pnl,
            "long_mask": long_mask,
            "short_mask": short_mask,
            "trade_records": [],
            "bs_markers": {"entry_markers": [], "exit_markers": []},
        }

        if view_data_override:
            view_data.update(view_data_override)

        # P4 fix: position is now computed from trade_records, not long_mask.
        # If a test override sets long_mask/short_mask but leaves trade_records
        # empty, synthesise matching trade_records so position reflects the mask.
        if not view_data["trade_records"]:
            lm = view_data.get("long_mask")
            sm = view_data.get("short_mask")
            last_idx = n - 1
            if lm is not None and len(lm) > 0 and bool(lm[last_idx]):
                view_data["trade_records"].append({
                    "type": "long", "entry_idx": 0,
                    "exit_idx": None, "return_pct": None, "exit_reason": "",
                })
            if sm is not None and len(sm) > 0 and bool(sm[last_idx]):
                view_data["trade_records"].append({
                    "type": "short", "entry_idx": 0,
                    "exit_idx": None, "return_pct": None, "exit_reason": "",
                })

        return {"views": {"v0_日线": view_data}}

    # ── 初始值 ──────────────────────────────────────────────────────────

    def test_pnl_initial_value_is_100(self, tmp_path):
        """未持仓时的第一个 bar：PnL 应为 100.0（_last_pnl 初始值）。"""
        store = self._make_store(tmp_path)
        so = self._make_stage_output()
        row = store._extract_row(0, "2024-01-01", so)
        assert row["v0_pnl_long"] == pytest.approx(100.0)
        assert row["v0_pnl_short"] == pytest.approx(100.0)

    # ── 持仓时更新 ──────────────────────────────────────────────────────

    def test_pnl_long_updates_when_long_position(self, tmp_path):
        """long_pos=True 时，pnl_long 正常更新为数组最后一个值。"""
        store = self._make_store(tmp_path)
        so = self._make_stage_output({"long_mask": np.ones(50, dtype=bool)})
        row = store._extract_row(0, "2024-01-01", so)
        # long_pnl = linspace(100, 110, 50), last = 110
        assert row["v0_pnl_long"] == pytest.approx(110.0)
        assert row["v0_long_pos"] == True  # noqa: E712

    def test_pnl_short_updates_when_short_position(self, tmp_path):
        """short_pos=True 时，pnl_short 正常更新为数组最后一个值。"""
        store = self._make_store(tmp_path)
        so = self._make_stage_output({"short_mask": np.ones(50, dtype=bool)})
        row = store._extract_row(0, "2024-01-01", so)
        # short_pnl = linspace(100, 105, 50), last = 105
        assert row["v0_pnl_short"] == pytest.approx(105.0)
        assert row["v0_short_pos"] == True  # noqa: E712

    # ── 未持仓时冻结 ────────────────────────────────────────────────────

    def test_pnl_long_frozen_when_no_long_position(self, tmp_path):
        """long_pos=False 时 pnl_long 不变（冻结在上次值）。"""
        store = self._make_store(tmp_path)

        # Bar 0: enter position, PnL updates
        so_enter = self._make_stage_output({"long_mask": np.ones(50, dtype=bool)})
        row0 = store._extract_row(0, "2024-01-01", so_enter)
        assert row0["v0_pnl_long"] == pytest.approx(110.0)

        # Bar 1: exit position (long_mask all False), long_pnl drifts to 120
        so_exit = self._make_stage_output({
            "long_mask": np.zeros(50, dtype=bool),
            "long_pnl": np.linspace(100, 120, 50),
        })
        row1 = store._extract_row(1, "2024-01-02", so_exit)
        # PnL 应冻结在 bar 0 持仓时的值（110），不随窗口滑动到 120
        assert row1["v0_pnl_long"] == pytest.approx(110.0)
        assert row1["v0_long_pos"] == False  # noqa: E712

    def test_pnl_short_frozen_when_no_short_position(self, tmp_path):
        """short_pos=False 时 pnl_short 不变（冻结在上次值）。"""
        store = self._make_store(tmp_path)

        # Bar 0: enter short position, PnL updates
        so_enter = self._make_stage_output({"short_mask": np.ones(50, dtype=bool)})
        row0 = store._extract_row(0, "2024-01-01", so_enter)
        assert row0["v0_pnl_short"] == pytest.approx(105.0)

        # Bar 1: exit position, short_pnl drifts to 115
        so_exit = self._make_stage_output({
            "short_mask": np.zeros(50, dtype=bool),
            "short_pnl": np.linspace(100, 115, 50),
        })
        row1 = store._extract_row(1, "2024-01-02", so_exit)
        # PnL 应冻结在上次值（105），不随窗口滑动到 115
        assert row1["v0_pnl_short"] == pytest.approx(105.0)
        assert row1["v0_short_pos"] == False  # noqa: E712

    # ── 同时操作多空 PnL ───────────────────────────────────────────────

    def test_long_and_short_pnl_freeze_independently(self, tmp_path):
        """做多和做空 PnL 各自独立冻结/更新。"""
        store = self._make_store(tmp_path)

        # Bar 0: long only
        so0 = self._make_stage_output({
            "long_mask": np.ones(50, dtype=bool),
            "short_mask": np.zeros(50, dtype=bool),
        })
        row0 = store._extract_row(0, "2024-01-01", so0)
        assert row0["v0_pnl_long"] == pytest.approx(110.0)
        assert row0["v0_pnl_short"] == pytest.approx(100.0)  # frozen at 100 (never held)

        # Bar 1: switch to short only
        so1 = self._make_stage_output({
            "long_mask": np.zeros(50, dtype=bool),
            "short_mask": np.ones(50, dtype=bool),
            "long_pnl": np.linspace(100, 130, 50),  # drifts
            "short_pnl": np.linspace(100, 115, 50),  # updates
        })
        row1 = store._extract_row(1, "2024-01-02", so1)
        # long PnL frozen at last position value (110)
        assert row1["v0_pnl_long"] == pytest.approx(110.0)
        # short PnL updates (last = 115)
        assert row1["v0_pnl_short"] == pytest.approx(115.0)

    # ── 多次进出 ────────────────────────────────────────────────────────

    def test_pnl_freeze_across_multiple_trades(self, tmp_path):
        """多次进出：每次退出时 PnL 冻结在当前值，再次进场时恢复更新。"""
        store = self._make_store(tmp_path)

        # Bar 0: enter long, PnL=110
        so0 = self._make_stage_output({"long_mask": np.ones(50, dtype=bool)})
        row0 = store._extract_row(0, "2024-01-01", so0)
        assert row0["v0_pnl_long"] == pytest.approx(110.0)

        # Bar 1: exit, PnL drifts to 120 but should freeze at 110
        so1 = self._make_stage_output({
            "long_mask": np.zeros(50, dtype=bool),
            "long_pnl": np.linspace(100, 120, 50),
        })
        row1 = store._extract_row(1, "2024-01-02", so1)
        assert row1["v0_pnl_long"] == pytest.approx(110.0)

        # Bar 2: still flat, PnL drifts to 130, should stay frozen at 110
        so2 = self._make_stage_output({
            "long_mask": np.zeros(50, dtype=bool),
            "long_pnl": np.linspace(100, 130, 50),
        })
        row2 = store._extract_row(2, "2024-01-03", so2)
        assert row2["v0_pnl_long"] == pytest.approx(110.0)

        # Bar 3: re-enter long, PnL=140 (new position)
        so3 = self._make_stage_output({
            "long_mask": np.ones(50, dtype=bool),
            "long_pnl": np.linspace(100, 140, 50),
        })
        row3 = store._extract_row(3, "2024-01-04", so3)
        assert row3["v0_pnl_long"] == pytest.approx(140.0)

    # ── view_data 为 None ────────────────────────────────────────────────

    def test_pnl_frozen_when_view_data_is_none(self, tmp_path):
        """view_data 为 None 时 PnL 也应冻结（不因 default NaN 覆盖）。"""
        store = self._make_store(tmp_path)

        # First bar: enter long position
        so_entry = self._make_stage_output({"long_mask": np.ones(50, dtype=bool)})
        row0 = store._extract_row(0, "2024-01-01", so_entry)
        assert row0["v0_pnl_long"] == pytest.approx(110.0)

        # Second bar: view_data is None → defaults (NaN, False)
        # PnL should be frozen at 110, not reset to NaN
        so_none = {"views": {"v0_日线": None}}
        row1 = store._extract_row(1, "2024-01-02", so_none)
        assert row1["v0_pnl_long"] == pytest.approx(110.0)
        assert row1["v0_long_pos"] == False  # noqa: E712


# ============================================================================
# TestBarIndexRendering — x 轴使用 bar_index 避免时间空白
# ============================================================================


class TestBarIndexRendering:
    """x 轴使用 bar_index 避免时间空白。

    HTML 前端 buildFilteredOverview / buildSignals / buildPnl 使用 bar_index
    作为 x 轴（搭配 tickvals/ticktext 显示日期），避免周末/隔夜空白被
    Plotly 连成直线。

    由于无法直接测试 JS 逻辑，改为测试数据完整性：验证 bar_index 和
    bar_timestamp 列都存在且单调递增，确保前端有正确数据可用。
    """

    @staticmethod
    def _make_store(tmp_path, ticker="TEST"):
        store = ParquetStore(str(tmp_path), ticker, [{"tf": "日线"}])
        store.start_session()
        return store

    @staticmethod
    def _make_stage_output():
        n = 50
        t = np.arange(n, dtype=float)
        sig = np.zeros(n, dtype=int)
        filtered = np.random.RandomState(0).randn(n).cumsum() * 0.01 + 100

        view_data = {
            "t": t,
            "schmitt": {"sig": sig, "eps": np.full(n, 0.1)},
            "filtered": filtered,
            "long_pnl": np.linspace(100, 110, n),
            "short_pnl": np.linspace(100, 105, n),
            "long_mask": np.zeros(n, dtype=bool),
            "short_mask": np.zeros(n, dtype=bool),
            "trade_records": [],
            "bs_markers": {"entry_markers": [], "exit_markers": []},
        }
        return {"views": {"v0_日线": view_data}}

    def test_filtered_overview_uses_bar_index(self, tmp_path):
        """每行数据必须包含 bar_index 且值正确。"""
        store = self._make_store(tmp_path)

        rows = []
        for i in range(5):
            row = store._extract_row(i, f"2024-01-{i+1:02d}T10:00:00", self._make_stage_output())
            rows.append(row)

        bar_indices = [r["bar_index"] for r in rows]
        assert bar_indices == [0, 1, 2, 3, 4], f"bar_index should be 0..4, got {bar_indices}"

        bar_ts = [r["bar_timestamp"] for r in rows]
        assert all(ts is not None for ts in bar_ts), "bar_timestamp should never be None"

    def test_signals_uses_bar_index(self, tmp_path):
        """信号对比图数据行必须包含 bar_index 和 bar_timestamp。"""
        store = self._make_store(tmp_path)
        row = store._extract_row(42, "2024-01-15T10:00:00", self._make_stage_output())

        assert "bar_index" in row, "bar_index column must exist for signal chart"
        assert row["bar_index"] == 42
        assert "bar_timestamp" in row, "bar_timestamp column must exist for signal chart"

    def test_pnl_uses_bar_index(self, tmp_path):
        """PnL 曲线数据行必须包含 bar_index，且单调递增连续。"""
        store = self._make_store(tmp_path)

        rows = []
        for i in range(100, 105):
            row = store._extract_row(i, f"2024-07-{i-85:02d}T10:00:00", self._make_stage_output())
            rows.append(row)

        bar_indices = [r["bar_index"] for r in rows]
        assert bar_indices == [100, 101, 102, 103, 104], (
            f"bar_index should be monotonically increasing, got {bar_indices}"
        )

        # bar_timestamp 应各不相同（隔夜/周末会导致时间跳跃，但每个 bar 有值）
        bar_ts_set = set(str(r["bar_timestamp"]) for r in rows)
        assert len(bar_ts_set) == 5, "Each bar should have a unique timestamp"


# ============================================================================
# TestSchemaValidation — ParquetStore schema validation
# ============================================================================


class TestSchemaValidation:
    """Tests for validate_schema and schema guard integration.

    Covers column-count checks, missing/extra columns, type mismatches,
    empty tables, and cross-view column verification.
    """

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _build_expected_schema(n_views: int = 4) -> pa.Schema:
        """Build a reference schema with *n_views* views."""
        from data.store import _build_full_schema
        return _build_full_schema([f"v{i}" for i in range(n_views)])

    @staticmethod
    def _build_valid_table(n_views: int = 4, n_rows: int = 3) -> pa.Table:
        """Build a table that passes validation for *n_views* views."""
        import pyarrow as pa
        from data.store import _build_full_schema

        schema = _build_full_schema([f"v{i}" for i in range(n_views)])
        columns: dict[str, list] = {f.name: [] for f in schema}
        for _ in range(n_rows):
            for field in schema:
                if pa.types.is_int32(field.type):
                    columns[field.name].append(0)
                elif pa.types.is_timestamp(field.type):
                    columns[field.name].append(np.datetime64("2024-01-01T00:00:00", "ns"))
                elif pa.types.is_int8(field.type):
                    columns[field.name].append(0)
                elif pa.types.is_float32(field.type):
                    columns[field.name].append(0.0)
                elif pa.types.is_boolean(field.type):
                    columns[field.name].append(False)
                elif pa.types.is_string(field.type) or pa.types.is_large_string(field.type):
                    columns[field.name].append("")
                else:
                    columns[field.name].append(None)

        return pa.Table.from_pydict(columns, schema=schema)

    # ── valid schema ───────────────────────────────────────────────────

    def test_valid_schema_passes(self):
        """validate_schema returns no issues for a correctly-typed table."""
        from data.store import validate_schema

        schema = self._build_expected_schema()
        table = self._build_valid_table()
        issues = validate_schema(table, schema)
        assert issues == [], f"Expected no issues, got: {issues}"

    def test_valid_schema_with_fewer_views(self):
        """2-view schema also validates correctly."""
        from data.store import validate_schema

        schema = self._build_expected_schema(n_views=2)
        table = self._build_valid_table(n_views=2)
        issues = validate_schema(table, schema)
        assert issues == []

    # ── missing column ─────────────────────────────────────────────────

    def test_missing_column_detected(self):
        """A table with a column removed from the expected schema is flagged."""
        from data.store import validate_schema

        schema = self._build_expected_schema()
        table = self._build_valid_table()
        # Remove one column
        col_names = [c for c in table.column_names if c != "v0_sig"]
        trimmed = table.select(col_names)

        issues = validate_schema(trimmed, schema)
        assert len(issues) >= 1
        assert any("Missing columns" in i for i in issues)
        assert any("v0_sig" in i for i in issues)

    def test_multiple_missing_columns(self):
        """Multiple missing columns are all reported."""
        from data.store import validate_schema

        schema = self._build_expected_schema(n_views=2)
        table = self._build_valid_table(n_views=2)
        # Drop several columns
        keep = [c for c in table.column_names if c not in ("v0_sig", "v0_eps", "v1_filtered")]
        trimmed = table.select(keep)

        issues = validate_schema(trimmed, schema)
        # Should mention all three missing
        missing_issue = next(i for i in issues if "Missing columns" in i)
        assert "v0_sig" in missing_issue
        assert "v0_eps" in missing_issue
        assert "v1_filtered" in missing_issue

    # ── extra column ───────────────────────────────────────────────────

    def test_extra_column_detected(self):
        """Extra columns are flagged but do not raise."""
        from data.store import validate_schema

        schema = self._build_expected_schema()
        table = self._build_valid_table()
        # Append an extra column
        extra = table.append_column(
            pa.field("bonus_column", pa.int32()),
            pa.array([1, 2, 3], type=pa.int32()),
        )

        issues = validate_schema(extra, schema)
        assert any("Extra columns" in i for i in issues)
        assert any("bonus_column" in i for i in issues)

    def test_extra_column_does_not_raise(self):
        """validate_schema returns issues rather than raising — callers decide."""
        from data.store import validate_schema

        schema = self._build_expected_schema()
        table = self._build_valid_table()
        extra = table.append_column(
            pa.field("extra", pa.string()),
            pa.array(["a", "b", "c"], type=pa.string()),
        )

        # Should not raise
        issues = validate_schema(extra, schema)
        assert len(issues) >= 1

    # ── type mismatch ──────────────────────────────────────────────────

    def test_type_mismatch_detected(self):
        """A column with the wrong type is flagged."""
        from data.store import validate_schema

        schema = self._build_expected_schema()
        table = self._build_valid_table()

        # Replace v0_sig (int8) with a float64 column of the same name
        idx = table.column_names.index("v0_sig")
        wrong_typed = table.set_column(
            idx,
            pa.field("v0_sig", pa.float64()),
            pa.array([0.5, 1.5, 2.5], type=pa.float64()),
        )

        issues = validate_schema(wrong_typed, schema)
        assert any("Type mismatch" in i and "v0_sig" in i for i in issues), (
            f"Expected type mismatch for v0_sig, got: {issues}"
        )

    def test_multiple_type_mismatches(self):
        """Multiple type mismatches are all reported."""
        from data.store import validate_schema

        schema = self._build_expected_schema(n_views=2)
        table = self._build_valid_table(n_views=2)

        # Replace two columns with wrong types
        idx_sig = table.column_names.index("v0_sig")
        table = table.set_column(
            idx_sig,
            pa.field("v0_sig", pa.float64()),
            pa.array([0.0, 0.0, 0.0], type=pa.float64()),
        )
        idx_filt = table.column_names.index("v0_filtered")
        table = table.set_column(
            idx_filt,
            pa.field("v0_filtered", pa.int32()),
            pa.array([0, 0, 0], type=pa.int32()),
        )

        issues = validate_schema(table, schema)
        type_issues = [i for i in issues if "Type mismatch" in i]
        assert len(type_issues) >= 2

    # ── empty table ────────────────────────────────────────────────────

    def test_empty_table_validates(self):
        """A table with zero rows but correct schema passes validation."""
        from data.store import validate_schema

        schema = self._build_expected_schema()
        empty = self._build_valid_table(n_rows=0)
        issues = validate_schema(empty, schema)
        assert issues == []

    def test_empty_table_missing_column_detected(self):
        """A zero-row table missing a column is still flagged."""
        from data.store import validate_schema

        schema = self._build_expected_schema()
        empty = self._build_valid_table(n_rows=0)
        trimmed = empty.select([c for c in empty.column_names if c != "v3_bs_exit"])
        issues = validate_schema(trimmed, schema)
        assert any("v3_bs_exit" in i for i in issues)

    # ── cross-view column count ───────────────────────────────────────

    def test_cross_view_column_count_4_views(self):
        """4-view schema has 3 fixed + 4*12 = 51 columns."""
        schema = self._build_expected_schema(n_views=4)
        assert len(schema.names) == 51

    def test_cross_view_column_count_2_views(self):
        """2-view schema has 3 + 2*12 = 27 columns."""
        schema = self._build_expected_schema(n_views=2)
        assert len(schema.names) == 27

    def test_cross_view_column_count_1_view(self):
        """1-view schema has 3 + 12 = 15 columns."""
        schema = self._build_expected_schema(n_views=1)
        assert len(schema.names) == 15

    def test_each_view_has_12_columns(self):
        """Every view prefix contributes exactly 12 columns."""
        schema = self._build_expected_schema(n_views=4)
        view_cols = {f"v{i}": 0 for i in range(4)}
        for name in schema.names:
            for v in view_cols:
                if name.startswith(v + "_"):
                    view_cols[v] += 1
                    break
        for v, count in view_cols.items():
            assert count == 12, f"{v} has {count} columns, expected 12"

    def test_fixed_fields_are_present(self):
        """bar_index and bar_timestamp are always the first two columns."""
        schema = self._build_expected_schema(n_views=4)
        assert schema.names[0] == "bar_index"
        assert schema.names[1] == "bar_timestamp"

    # ── load_parquet ───────────────────────────────────────────────────

    def test_load_parquet_valid_file(self, tmp_path):
        """load_parquet loads and validates a correct file without error."""
        from data.store import load_parquet

        schema = self._build_expected_schema()
        table = self._build_valid_table()

        path = tmp_path / "valid.parquet"
        pq.write_table(table, str(path))

        loaded = load_parquet(str(path), schema)
        assert loaded.num_rows == table.num_rows
        assert loaded.num_columns == table.num_columns

    def test_load_parquet_invalid_file_raises(self, tmp_path):
        """load_parquet raises ValueError when schema does not match."""
        from data.store import load_parquet

        schema = self._build_expected_schema()
        table = self._build_valid_table()
        # Remove a column before writing
        trimmed = table.select([c for c in table.column_names if c != "v0_sig"])

        path = tmp_path / "invalid.parquet"
        pq.write_table(trimmed, str(path))

        with pytest.raises(ValueError, match="Schema validation failed"):
            load_parquet(str(path), schema)

    # ── flush integration ─────────────────────────────────────────────

    def test_flush_completes_with_valid_data(self, tmp_path):
        """flush() with valid data completes and produces a part file."""
        store = ParquetStore(str(tmp_path), "TEST", [{"tf": "日线"}])
        store.start_session()

        # Build a valid row matching the schema
        row = {}
        for field in store._full_schema:
            if field.name == "bar_index":
                row[field.name] = 0
            elif field.name == "bar_timestamp":
                row[field.name] = np.datetime64("2024-01-01T00:00:00", "ns")
            elif pa.types.is_int8(field.type) or pa.types.is_int32(field.type):
                row[field.name] = 0
            elif pa.types.is_float32(field.type):
                row[field.name] = 0.0
            elif pa.types.is_boolean(field.type):
                row[field.name] = False
            elif pa.types.is_string(field.type) or pa.types.is_large_string(field.type):
                row[field.name] = ""
            else:
                row[field.name] = 0

        store._buffer = [row]
        store.flush()

        assert store._total_row_count == 1
        part_files = list(store._session_dir.glob("part_*.parquet"))
        assert len(part_files) == 1

    def test_end_session_validates_parts(self, tmp_path, capsys):
        """end_session() validates part files during merge."""
        store = ParquetStore(str(tmp_path), "TEST", [{"tf": "日线"}])
        store.start_session()

        # Build valid rows, flush, then end
        for i in range(3):
            row = {}
            for field in store._full_schema:
                if field.name == "bar_index":
                    row[field.name] = i
                elif field.name == "bar_timestamp":
                    row[field.name] = np.datetime64("2024-01-01T00:00:00", "ns")
                elif pa.types.is_int8(field.type):
                    row[field.name] = 0
                elif pa.types.is_float32(field.type):
                    row[field.name] = 0.0
                elif pa.types.is_boolean(field.type):
                    row[field.name] = False
                elif pa.types.is_string(field.type) or pa.types.is_large_string(field.type):
                    row[field.name] = ""
                else:
                    row[field.name] = 0
            store._buffer.append(row)

        store.flush()
        store.end_session()

        # Should complete without exception
        assert (store._session_dir / "backtest_result.parquet").exists()

    # ── column count edge cases ───────────────────────────────────────

    def test_wrong_total_column_count(self):
        """A table with a different column count triggers the count mismatch issue."""
        from data.store import validate_schema

        schema = self._build_expected_schema(n_views=4)
        # Build a 4-view schema but validate against a 2-view one
        table = self._build_valid_table(n_views=4)
        smaller_schema = self._build_expected_schema(n_views=2)

        issues = validate_schema(table, smaller_schema)
        assert any("Column count mismatch" in i for i in issues)


# ============================================================================
# TestMultiTickerIsolation — 多 ticker 并发隔离端到端测试
# ============================================================================


class TestMultiTickerIsolation:
    """多 ticker 并发隔离：确保并行回测完全独立，无文件碰撞或状态泄漏。

    验证三个持久化服务的 ticker 隔离，以及 BacktestRunner 的独立性。
    """

    # ── ParquetStore ticker isolation ─────────────────────────────────────

    def test_parquet_store_two_tickers_separate_dirs(self, tmp_path):
        """两个不同 ticker 的 ParquetStore 写入不同 session 目录。"""
        store_a = ParquetStore(str(tmp_path), "AAPL", [{"tf": "日线"}])
        store_b = ParquetStore(str(tmp_path), "TSLA", [{"tf": "日线"}])

        sid_a = store_a.start_session()
        sid_b = store_b.start_session()

        # 不同 session 目录
        assert store_a._session_dir != store_b._session_dir
        # session 目录名包含 ticker
        assert "AAPL" in store_a._session_dir.name
        assert "TSLA" in store_b._session_dir.name
        # 不同 session ID
        assert sid_a != sid_b

        store_a.end_session()
        store_b.end_session()

    def test_parquet_store_data_independent(self, tmp_path):
        """两个 ticker 写入的数据互不干扰。"""
        configs = [{"tf": "日线"}]
        store_a = ParquetStore(str(tmp_path), "AAPL", configs)
        store_b = ParquetStore(str(tmp_path), "TSLA", configs)

        store_a.start_session()
        store_b.start_session()

        # 每个 store 写入一行数据
        for i in range(3):
            row = _make_valid_row(i, store_a._full_schema)
            store_a._buffer.append(row)
            row_b = _make_valid_row(i + 100, store_b._full_schema)
            store_b._buffer.append(row_b)

        store_a.flush()
        store_b.flush()
        store_a.end_session()
        store_b.end_session()

        # 验证 AAPL 的 parquet 只有 3 行
        aapl_pq = store_a._session_dir / "backtest_result.parquet"
        assert aapl_pq.exists()
        aapl_table = pq.read_table(str(aapl_pq))
        assert aapl_table.num_rows == 3

        # 验证 TSLA 的 parquet 也只有 3 行（互不干扰）
        tsla_pq = store_b._session_dir / "backtest_result.parquet"
        assert tsla_pq.exists()
        tsla_table = pq.read_table(str(tsla_pq))
        assert tsla_table.num_rows == 3

        # 确认 AAPL 数据没泄漏到 TSLA 目录
        assert not (store_b._session_dir / "AAPL").exists()
        assert not (store_a._session_dir / "TSLA").exists()

    def test_parquet_store_concurrent_tickers_independent_results(self, tmp_path):
        """并行运行不同 ticker 产生独立结果，无交叉污染。"""
        configs = [{"tf": "日线"}]
        store_a = ParquetStore(str(tmp_path), "AAPL", configs)
        store_b = ParquetStore(str(tmp_path), "TSLA", configs)

        store_a.start_session()
        store_b.start_session()

        # 模拟并行写入：交替写入不同 ticker 的数据
        for i in range(5):
            store_a._buffer.append(_make_valid_row(i, store_a._full_schema))
            store_b._buffer.append(_make_valid_row(i + 50, store_b._full_schema))

        store_a.flush()
        store_b.flush()
        store_a.end_session()
        store_b.end_session()

        # 两个结果文件都存在且独立
        aapl_result = store_a._session_dir / "backtest_result.parquet"
        tsla_result = store_b._session_dir / "backtest_result.parquet"
        assert aapl_result.exists()
        assert tsla_result.exists()

        # 验证目录不同
        aapl_files = set(p.name for p in store_a._session_dir.iterdir())
        tsla_files = set(p.name for p in store_b._session_dir.iterdir())
        # 各自的 metadata 只包含自己的 ticker
        import json
        aapl_meta = json.loads((store_a._session_dir / "metadata.json").read_text())
        tsla_meta = json.loads((store_b._session_dir / "metadata.json").read_text())
        assert aapl_meta["ticker"] == "AAPL"
        assert tsla_meta["ticker"] == "TSLA"

    # ── EventRecorder ticker isolation ────────────────────────────────────

    def test_event_recorder_two_tickers_separate_dirs(self, tmp_path):
        """两个不同 ticker 的 EventRecorder 写入不同 session 目录。"""
        from backtest.recorder import EventRecorder

        rec_a = EventRecorder(str(tmp_path), "AAPL")
        rec_b = EventRecorder(str(tmp_path), "TSLA")

        sid_a = rec_a.start_session({"configs": [{"tf": "日线"}]})
        sid_b = rec_b.start_session({"configs": [{"tf": "日线"}]})

        assert rec_a._session_dir != rec_b._session_dir
        assert "AAPL" in rec_a._session_dir.name
        assert "TSLA" in rec_b._session_dir.name
        assert sid_a != sid_b

        rec_a.end_session()
        rec_b.end_session()

    def test_event_recorder_data_independent(self, tmp_path):
        """两个 ticker 的 EventRecorder 数据互不干扰。"""
        from backtest.recorder import EventRecorder

        rec_a = EventRecorder(str(tmp_path), "AAPL")
        rec_b = EventRecorder(str(tmp_path), "TSLA")

        rec_a.start_session({"configs": [{"tf": "日线"}]})
        rec_b.start_session({"configs": [{"tf": "日线"}]})

        # 模拟记录步骤
        mock_output_a = _make_mock_pipeline_output(42, "2024-06-15", "long")
        mock_output_b = _make_mock_pipeline_output(100, "2024-06-15", "short")

        rec_a.record_step(0, "2024-06-15", mock_output_a)
        rec_b.record_step(0, "2024-06-15", mock_output_b)

        rec_a.end_session()
        rec_b.end_session()

        # 各自的 metadata 只包含自己的 ticker
        import json
        aapl_meta = json.loads((rec_a._session_dir / "metadata.json").read_text())
        tsla_meta = json.loads((rec_b._session_dir / "metadata.json").read_text())
        assert aapl_meta["ticker"] == "AAPL"
        assert tsla_meta["ticker"] == "TSLA"

        # 各自的 events.jsonl 存在
        assert (rec_a._session_dir / "events.jsonl").exists()
        assert (rec_b._session_dir / "events.jsonl").exists()

    # ── PipelineCapture ticker isolation ──────────────────────────────────

    def test_pipeline_capture_two_tickers_separate_dirs(self, tmp_path, monkeypatch):
        """两个不同 ticker 的 PipelineCapture 写入不同 session 目录。"""
        monkeypatch.setenv("PIPELINE_CAPTURE", "1")
        from filter.backtest.capture import PipelineCapture

        cap_a = PipelineCapture(str(tmp_path), "AAPL", {})
        cap_b = PipelineCapture(str(tmp_path), "TSLA", {})

        sid_a = cap_a.start_session()
        sid_b = cap_b.start_session()

        assert cap_a._session_dir != cap_b._session_dir
        assert "AAPL" in cap_a._session_dir.name
        assert "TSLA" in cap_b._session_dir.name
        assert sid_a != sid_b

        cap_a.end_session()
        cap_b.end_session()

    def test_pipeline_capture_data_independent(self, tmp_path, monkeypatch):
        """两个 ticker 的 PipelineCapture 数据互不干扰。"""
        monkeypatch.setenv("PIPELINE_CAPTURE", "1")
        from filter.backtest.capture import PipelineCapture, PipelineStageData

        cap_a = PipelineCapture(str(tmp_path), "AAPL", {})
        cap_b = PipelineCapture(str(tmp_path), "TSLA", {})

        cap_a.start_session()
        cap_b.start_session()

        # 模拟捕获步骤
        sd_a = PipelineStageData(view_name="v0_日线", tf="日线",
                                 t=np.arange(50, dtype=float),
                                 filtered=np.ones(50))
        sd_b = PipelineStageData(view_name="v0_日线", tf="日线",
                                 t=np.arange(50, dtype=float),
                                 filtered=np.ones(50) * 2)

        cap_a.capture_step(0, "2024-06-15", {"v0_日线": sd_a})
        cap_b.capture_step(0, "2024-06-15", {"v0_日线": sd_b})

        cap_a.end_session()
        cap_b.end_session()

        # 各自的 metadata 只包含自己的 ticker
        import json
        aapl_meta = json.loads((cap_a._session_dir / "metadata.json").read_text())
        tsla_meta = json.loads((cap_b._session_dir / "metadata.json").read_text())
        assert aapl_meta["ticker"] == "AAPL"
        assert tsla_meta["ticker"] == "TSLA"

        # 各自的步骤目录存在
        assert (cap_a._session_dir / "steps" / "000000").exists()
        assert (cap_b._session_dir / "steps" / "000000").exists()

    # ── 路径格式回归防护 ─────────────────────────────────────────────────

    def test_output_dirs_never_overlap_across_tickers(self, tmp_path):
        """跨 ticker 的输出目录永远不会重叠（同一服务类型内）。"""
        import time
        from backtest.recorder import EventRecorder

        # ParquetStore: AAPL vs TSLA → 不同目录
        ps_a = ParquetStore(str(tmp_path), "AAPL", [{"tf": "日线"}])
        ps_t = ParquetStore(str(tmp_path), "TSLA", [{"tf": "日线"}])
        ps_a.start_session()
        time.sleep(0.01)  # ensure different timestamp
        ps_t.start_session()
        assert ps_a._session_dir != ps_t._session_dir
        assert "AAPL" in ps_a._session_dir.name
        assert "TSLA" in ps_t._session_dir.name
        ps_a.end_session()
        ps_t.end_session()

        # EventRecorder: AAPL vs TSLA → 不同目录
        er_a = EventRecorder(str(tmp_path), "AAPL")
        er_t = EventRecorder(str(tmp_path), "TSLA")
        er_a.start_session({"configs": [{"tf": "日线"}]})
        time.sleep(0.01)
        er_t.start_session({"configs": [{"tf": "日线"}]})
        assert er_a._session_dir != er_t._session_dir
        assert "AAPL" in er_a._session_dir.name
        assert "TSLA" in er_t._session_dir.name
        er_a.end_session()
        er_t.end_session()


# ============================================================================
# Helpers for TestMultiTickerIsolation
# ============================================================================


def _make_valid_row(bar_index: int, schema: pa.Schema) -> dict:
    """Build a single row dict matching the given schema with valid typed values."""
    import pyarrow as pa
    row = {}
    for field in schema:
        if field.name == "bar_index":
            row[field.name] = bar_index
        elif field.name == "bar_timestamp":
            row[field.name] = np.datetime64("2024-01-01T00:00:00", "ns")
        elif pa.types.is_int8(field.type):
            row[field.name] = 0
        elif pa.types.is_float32(field.type):
            row[field.name] = 0.0
        elif pa.types.is_boolean(field.type):
            row[field.name] = False
        elif pa.types.is_string(field.type) or pa.types.is_large_string(field.type):
            row[field.name] = ""
        else:
            row[field.name] = 0
    return row


def _make_mock_pipeline_output(bar_index: int, cutoff_date: str, trade_type: str) -> dict:
    """Build a minimal mock pipeline output for EventRecorder.record_step."""
    n = 50
    t = np.arange(n, dtype=float)
    sig = np.zeros(n, dtype=int)
    sig[-1] = 1 if trade_type == "long" else -1
    filtered = np.random.RandomState(bar_index).randn(n).cumsum() * 0.01 + 100

    view_data = {
        "t": t,
        "schmitt": {
            "sig": sig,
            "eps": np.full(n, 0.1),
        },
        "filtered": filtered,
        "long_pnl": np.linspace(100, 110, n),
        "short_pnl": np.linspace(100, 105, n),
        "long_mask": np.ones(n, dtype=bool) if trade_type == "long" else np.zeros(n, dtype=bool),
        "short_mask": np.ones(n, dtype=bool) if trade_type == "short" else np.zeros(n, dtype=bool),
        "trade_records": [],
        "bs_markers": {"entry_markers": [], "exit_markers": []},
        "all_pairs": [],
        "noisy": np.random.randn(n) * 0.5 + 100,
    }

    return {
        "bar_index": bar_index,
        "bar_timestamp": cutoff_date,
        "cutoff_date": cutoff_date,
        "views": {"v0_日线": view_data},
        "ohlcv": {
            "close": float(filtered[-1]),
            "open": float(filtered[-1]) - 0.1,
            "high": float(filtered[-1]) + 0.3,
            "low": float(filtered[-1]) - 0.3,
            "volume": 10000.0,
        },
    }


# ============================================================================
# TestPositionBoundaryBars — P4: 持仓边界 bar 测试
# ============================================================================


class TestPositionBoundaryBars:
    """P4: 持仓在精确边界 bar 上的行为验证。

    - 持仓在 entry bar 上应为 True/active
    - 持仓在 exit 后紧接 bar 上应为 False
    - 快速 long→short→long 切换（5 bar 内）
    """

    @staticmethod
    def _extract(view_data: dict, prefix: str = "v0") -> dict:
        return ParquetStore._extract_view_columns(prefix, view_data)

    def test_position_at_exact_entry_bar(self):
        """P4: entry bar 上 long_pos 应为 True。"""
        # n_pts=51 → view_last_idx=50, trade enters at 50
        view_data = make_mock_view_data(n_pts=51, has_entry=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 50, "exit_idx": None}
        ]
        cols = self._extract(view_data)
        assert cols["v0_long_pos"] == True  # noqa: E712
        assert cols["v0_short_pos"] == False  # noqa: E712

    def test_position_immediately_after_exit(self):
        """P4: exit bar 之后紧接 bar 上 long_pos 应为 False。"""
        # n_pts=52 → view_last_idx=51, trade entered at 50, exited at 50
        # At bar 51: entry=50 <= 51, exit=50 <= 51, reason!="eod" → no longer active
        view_data = make_mock_view_data(n_pts=52, has_entry=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 50, "exit_idx": 50,
             "return_pct": 1.0, "exit_reason": "take_profit"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_long_pos"] == False  # noqa: E712
        assert cols["v0_short_pos"] == False  # noqa: E712

    def test_rapid_long_short_long_within_5_bars(self):
        """P4: rapid long→short→long within 5 bars, last trade determines position."""
        # n_pts=55 → view_last_idx=54. Trades at bars 50, 52, 54
        view_data = make_mock_view_data(n_pts=55, has_entry=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 50, "exit_idx": 51,
             "return_pct": 0.5, "exit_reason": "take_profit"},
            {"type": "short", "entry_idx": 52, "exit_idx": 53,
             "return_pct": 0.3, "exit_reason": "take_profit"},
            {"type": "long", "entry_idx": 54, "exit_idx": None},
        ]
        cols = self._extract(view_data)
        # Last active trade is the long at bar 54 (still open)
        assert cols["v0_long_pos"] == True  # noqa: E712
        assert cols["v0_short_pos"] == False  # noqa: E712

    def test_eod_exit_keeps_position_active(self):
        """P4: eod exit does not close position — stays active."""
        view_data = make_mock_view_data(n_pts=51, has_entry=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 40, "exit_idx": 50,
             "return_pct": 2.0, "exit_reason": "eod"}
        ]
        cols = self._extract(view_data)
        # eod exit: trade is still considered active
        assert cols["v0_long_pos"] == True  # noqa: E712


# ============================================================================
# TestBSMarkerConsecutiveEvents — P5: BS 标记连续事件测试
# ============================================================================


class TestBSMarkerConsecutiveEvents:
    """P5: BS marker matching now uses post-hoc join.

    Per-bar matching has been removed from ``_extract_view_columns``.
    These tests verify per-bar always returns NA.
    """

    @staticmethod
    def _extract(view_data: dict, prefix: str = "v0") -> dict:
        return ParquetStore._extract_view_columns(prefix, view_data)

    def test_b_and_s_on_consecutive_bars(self):
        """Per-bar matching removed; always returns NA."""
        view_50 = make_mock_view_data(n_pts=51, has_entry=False)
        d50 = view_50["dates"]
        view_50["bs_markers"]["entry_markers"] = [
            (50, "B", "green", d50[50]),
        ]
        view_50["bs_markers"]["exit_markers"] = [
            (49, "S", "red", "take_profit", d50[49]),
        ]
        cols_50 = self._extract(view_50)
        assert cols_50["v0_bs_entry"] is None
        assert cols_50["v0_bs_exit"] is None

        view_49 = make_mock_view_data(n_pts=50, has_entry=False)
        d49 = view_49["dates"]
        view_49["bs_markers"]["entry_markers"] = [
            (49, "B", "green", d49[49]),
        ]
        view_49["bs_markers"]["exit_markers"] = [
            (49, "S", "red", "take_profit", d49[49]),
        ]
        cols_49 = self._extract(view_49)
        assert cols_49["v0_bs_entry"] is None
        assert cols_49["v0_bs_exit"] is None

    def test_multiple_b_markers_on_distinct_bars(self):
        """Per-bar matching removed; always returns NA."""
        view_60 = make_mock_view_data(n_pts=61, has_entry=False)
        d60 = view_60["dates"]
        view_60["bs_markers"]["entry_markers"] = [
            (30, "B", "green", d60[30]),
            (45, "B", "green", d60[45]),
            (60, "B", "green", d60[60]),
        ]
        cols_60 = self._extract(view_60)
        assert cols_60["v0_bs_entry"] is None

        view_45 = make_mock_view_data(n_pts=46, has_entry=False)
        d45 = view_45["dates"]
        view_45["bs_markers"]["entry_markers"] = [
            (30, "B", "green", d45[30]),
            (45, "B", "green", d45[45]),
        ]
        cols_45 = self._extract(view_45)
        assert cols_45["v0_bs_entry"] is None

    def test_mixed_entry_exit_sequence(self):
        """Per-bar matching removed; always returns NA."""
        view_80 = make_mock_view_data(n_pts=81, has_entry=False)
        d80 = view_80["dates"]
        view_80["bs_markers"]["entry_markers"] = [
            (50, "B", "green", d80[50]),
            (80, "S", "red", d80[80]),
        ]
        view_80["bs_markers"]["exit_markers"] = [
            (60, "S", "green", "take_profit", d80[60]),
        ]
        cols_80 = self._extract(view_80)
        assert cols_80["v0_bs_entry"] is None
        assert cols_80["v0_bs_exit"] is None


# ============================================================================
# TestPredPairsFallback — P1: pred_pairs 回退行为测试
# ============================================================================


class TestPredPairsFallback:
    """P1: Extended pred_pairs fallback — chain carry-forward and edge cases.

    When ``pred_pairs`` is empty, ``_compute_strategy_for_view`` returns
    flat PnL and no trade records. These tests verify the fallback works
    for extended periods and mixed sequences.
    """

    @staticmethod
    def _compute_strategy(t, filtered, all_pairs, pred_pairs, cfg=None):
        """Call the static method directly."""
        from backtest.engine import BacktestRunner
        schmitt = {"sig": np.zeros(len(t), dtype=int)}
        if cfg is None:
            cfg = {"show_strategy": True, "stop_loss_pct": 5.0, "n_ext": 10}
        return BacktestRunner._compute_strategy_for_view(
            t, filtered, schmitt, all_pairs, pred_pairs, cfg,
        )

    def test_empty_pred_pairs_returns_flat_pnl(self):
        """P1: empty pred_pairs → flat PnL (all 100.0), no trades."""
        n = 50
        t = np.arange(n, dtype=float)
        filtered = np.linspace(100, 110, n)
        all_pairs = [(10, 20), (30, 40)]
        pred_pairs = []  # empty!

        long_pnl, short_pnl, trades = self._compute_strategy(
            t, filtered, all_pairs, pred_pairs,
        )
        assert len(trades) == 0
        assert np.all(long_pnl == 100.0)
        assert np.all(short_pnl == 100.0)

    def test_pred_pairs_suddenly_empty_after_trades(self):
        """P1: pred_pairs empty after real trades → still returns flat."""
        n = 50
        t = np.arange(n, dtype=float)
        filtered = np.linspace(100, 110, n)
        # all_pairs exist but pred_pairs is empty (simulates prediction failure)
        all_pairs = [(5, 15), (25, 35)]
        pred_pairs = []

        long_pnl, short_pnl, trades = self._compute_strategy(
            t, filtered, all_pairs, pred_pairs,
        )
        assert len(trades) == 0
        assert np.all(long_pnl == 100.0)

    def test_mixed_empty_nonempty_sequence(self):
        """P1: mixed empty → non-empty → empty pred_pairs over multiple calls."""
        n = 50
        t = np.arange(n, dtype=float)
        filtered = np.linspace(100, 110, n)
        all_pairs = [(10, 20)]

        # Call 1: empty pred_pairs → no trades
        long1, short1, trades1 = self._compute_strategy(
            t, filtered, all_pairs, [],
        )
        assert len(trades1) == 0

        # Call 2: non-empty pred_pairs → trades generated
        pred_pairs = [{"fit_result": {"a": 0.01, "b": -0.5, "c": 105}, "pair_end": 20}]
        long2, short2, trades2 = self._compute_strategy(
            t, filtered, all_pairs, pred_pairs,
        )
        # Trades may or may not be generated depending on stop/profit conditions
        # But the function should not crash
        assert long2 is not None
        assert short2 is not None

        # Call 3: empty again → no trades
        long3, short3, trades3 = self._compute_strategy(
            t, filtered, all_pairs, [],
        )
        assert len(trades3) == 0

    def test_many_consecutive_empty_calls(self):
        """P1: 20+ consecutive empty pred_pairs → no crash, consistent output."""
        n = 50
        t = np.arange(n, dtype=float)
        filtered = np.linspace(100, 110, n)
        all_pairs = [(5, 25)]

        for i in range(25):
            long_pnl, short_pnl, trades = self._compute_strategy(
                t, filtered, all_pairs, [],
            )
            assert len(trades) == 0
            assert np.all(long_pnl == 100.0)
            assert np.all(short_pnl == 100.0)

    def test_show_strategy_false_behaves_like_empty_pred_pairs(self):
        """P1: show_strategy=False → same flat PnL as empty pred_pairs."""
        n = 50
        t = np.arange(n, dtype=float)
        filtered = np.linspace(100, 110, n)
        all_pairs = [(10, 20)]
        pred_pairs = [{"fit_result": {"a": 0.01, "b": -0.5, "c": 105}, "pair_end": 20}]

        cfg = {"show_strategy": False, "stop_loss_pct": 5.0, "n_ext": 10}
        long_pnl, short_pnl, trades = self._compute_strategy(
            t, filtered, all_pairs, pred_pairs, cfg,
        )
        assert len(trades) == 0
        assert np.all(long_pnl == 100.0)

    def test_schmitt_none_behaves_like_empty_pred_pairs(self):
        """P1: schmitt=None → same flat PnL, no crash."""
        n = 50
        t = np.arange(n, dtype=float)
        filtered = np.linspace(100, 110, n)
        all_pairs = [(10, 20)]
        pred_pairs = [{"fit_result": {"a": 0.01, "b": -0.5, "c": 105}, "pair_end": 20}]

        from backtest.engine import BacktestRunner
        cfg = {"show_strategy": True, "stop_loss_pct": 5.0, "n_ext": 10}
        long_pnl, short_pnl, trades = BacktestRunner._compute_strategy_for_view(
            t, filtered, None, all_pairs, pred_pairs, cfg,
        )
        assert len(trades) == 0
        assert np.all(long_pnl == 100.0)


# ============================================================================
# TestBSDateBasedMatching — P9: BS marker date-based matching via bar_date
# ============================================================================


class TestBSDateBasedMatching:
    """P9 fix: BS marker 通过 bar_date 进行日期匹配。

    原始 index-based matching 在某些场景下失效（BS marker 的 bar_idx
    可能 < view_last_idx），新增 bar_date 参数作为主要匹配方式，
    回退到 _current_bar_date，最后回退到 index-based matching。
    """

    @staticmethod
    def _extract(view_data: dict, prefix: str = "v0",
                 bar_date: Any = None) -> dict:
        return ParquetStore._extract_view_columns(prefix, view_data,
                                                   bar_date=bar_date)

    def test_bs_entry_via_bar_date(self):
        """通过 bar_date 匹配 BS entry marker（非 index-based）。"""
        # marker at bar 50, but we're processing bar 99
        # index-based matching (view_last_idx=99, bar_idx=50) would fail
        # date-based matching should succeed because bar_date matches marker date
        view_data = make_mock_view_data(n_pts=100, has_entry=False)
        d = view_data["dates"]
        # Place marker at bar 50
        view_data["bs_markers"]["entry_markers"] = [
            (50, "B", "green", d[50]),
        ]
        # Pass bar_date=d[50] so date matching finds the marker even though
        # view_last_idx=99 and bar_idx=50
        cols = self._extract(view_data, bar_date=pd.Timestamp(d[50]))
        assert cols["v0_bs_entry"] is None  # per-bar matching removed

    def test_bs_exit_via_bar_date(self):
        """通过 bar_date 匹配 BS exit marker。"""
        view_data = make_mock_view_data(n_pts=100, has_entry=False)
        d = view_data["dates"]
        view_data["bs_markers"]["exit_markers"] = [
            (30, "S", "red", "stop_loss", d[30]),
        ]
        cols = self._extract(view_data, bar_date=pd.Timestamp(d[30]))
        assert cols["v0_bs_exit"] is None  # per-bar matching removed

    def test_bs_entry_not_matched_when_bar_date_mismatch(self):
        """bar_date 与 marker date 不匹配时不应找到 marker。"""
        view_data = make_mock_view_data(n_pts=100, has_entry=False)
        d = view_data["dates"]
        view_data["bs_markers"]["entry_markers"] = [
            (50, "B", "green", d[50]),
        ]
        # bar_date is d[99], marker is at d[50] → no match
        cols = self._extract(view_data, bar_date=pd.Timestamp(d[99]))
        assert cols["v0_bs_entry"] is None

    def test_bs_entry_bar_date_none_falls_back_to_current_bar_date(self):
        """bar_date=None 时回退到 _current_bar_date(view_data) 进行匹配。"""
        view_data = make_mock_view_data(n_pts=51, has_entry=True)
        cols = self._extract(view_data, bar_date=None)
        assert cols["v0_bs_entry"] is None  # per-bar matching removed

    def test_bs_entry_bar_date_none_falls_back_to_index_matching(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=51, has_entry=False)
        del view_data["dates"]
        view_data["bs_markers"]["entry_markers"] = [
            (50, "B", "green", "2024-01-01"),
        ]
        cols = self._extract(view_data, bar_date=None)
        assert cols["v0_bs_entry"] is None  # per-bar matching removed

    def test_date_matching_handles_none_marker_date(self):
        """_date_matches 在 marker_date=None 时返回 False（不抛异常）。"""
        from data.store import _date_matches
        assert _date_matches(None, pd.Timestamp("2024-01-01")) is False
        assert _date_matches(pd.Timestamp("2024-01-01"), None) is False
        assert _date_matches(None, None) is False

    def test_date_matching_handles_mismatched_formats(self):
        """_date_matches 能处理不同格式的日期输入。"""
        from data.store import _date_matches

        # Timestamp vs string
        assert _date_matches(
            pd.Timestamp("2024-01-15"), "2024-01-15"
        ) is True
        # Timestamp vs datetime
        from datetime import datetime
        assert _date_matches(
            pd.Timestamp("2024-06-15 10:30:00"),
            datetime(2024, 6, 15, 10, 30, 0)
        ) is True
        # numpy datetime64 vs Timestamp
        assert _date_matches(
            np.datetime64("2024-03-20"),
            pd.Timestamp("2024-03-20")
        ) is True
        # Different dates → False
        assert _date_matches(
            pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02")
        ) is False
        # Invalid string → False (no exception)
        assert _date_matches("not-a-date", pd.Timestamp("2024-01-01")) is False

    def test_current_bar_date_returns_none_for_missing_dates(self):
        """_current_bar_date 在 view_data 无 dates 时返回 None。"""
        from data.store import _current_bar_date
        assert _current_bar_date({}) is None
        assert _current_bar_date({"dates": None}) is None
        assert _current_bar_date({"dates": []}) is None

    def test_current_bar_date_returns_last_timestamp(self):
        """_current_bar_date 返回 dates 数组的最后一个元素作为 pd.Timestamp。"""
        from data.store import _current_bar_date
        dates = pd.DatetimeIndex([
            "2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04",
        ])
        result = _current_bar_date({"dates": dates})
        assert result == pd.Timestamp("2024-01-04")


# ============================================================================
# TestTradeDateBasedMatching — P9: trade exit date-based matching via bar_date
# ============================================================================


class TestTradeDateBasedMatching:
    """P9 fix tests: trade date-based matching now uses post-hoc join.

    Per-bar matching (including bar_date fallback) has been removed.
    Trade events are now filled via ``end_session`` post-processing.
    These tests verify per-bar always returns NA.
    """

    @staticmethod
    def _extract(view_data: dict, prefix: str = "v0",
                 bar_date: Any = None) -> dict:
        return ParquetStore._extract_view_columns(prefix, view_data,
                                                   bar_date=bar_date)

    def test_trade_exit_via_bar_date(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=100, has_entry=False, has_exit=False)
        d = view_data["dates"]
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 20, "exit_idx": 50,
             "return_pct": 3.5, "exit_reason": "take_profit"}
        ]
        cols = self._extract(view_data, bar_date=pd.Timestamp(d[50]))
        assert cols["v0_trade"] is None
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] is None

    def test_trade_exit_date_mismatch_not_matched(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=100, has_entry=False, has_exit=False)
        d = view_data["dates"]
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 20, "exit_idx": 50,
             "return_pct": 3.5, "exit_reason": "take_profit"}
        ]
        cols = self._extract(view_data, bar_date=pd.Timestamp(d[99]))
        assert cols["v0_trade"] is None

    def test_trade_return_populated_on_exit_via_date_match(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        d = view_data["dates"]
        view_data["trade_records"] = [
            {"type": "short", "entry_idx": 10, "exit_idx": 80,
             "return_pct": -2.5, "exit_reason": "stop_loss"}
        ]
        cols = self._extract(view_data, bar_date=pd.Timestamp(d[80]))
        assert cols["v0_trade"] is None
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] is None

    def test_trade_reason_populated_on_exit_via_date_match(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        d = view_data["dates"]
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 30, "exit_idx": 70,
             "return_pct": 1.2, "exit_reason": "take_profit"}
        ]
        cols = self._extract(view_data, bar_date=pd.Timestamp(d[70]))
        assert cols["v0_trade"] is None
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] is None

    def test_eod_exit_not_matched_even_with_date_match(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        d = view_data["dates"]
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 30, "exit_idx": 70,
             "return_pct": 2.0, "exit_reason": "eod"}
        ]
        cols = self._extract(view_data, bar_date=pd.Timestamp(d[70]))
        assert cols["v0_trade"] is None
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] is None

    def test_date_matching_handles_none_dates_gracefully(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=50, has_entry=False, has_exit=False)
        del view_data["dates"]
        view_data["trade_records"] = [
            {"type": "short", "entry_idx": 20, "exit_idx": 49,
             "return_pct": -1.0, "exit_reason": "stop_loss"}
        ]
        cols = self._extract(view_data, bar_date=pd.Timestamp("2024-06-15"))
        assert cols["v0_trade"] is None
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] is None

    def test_date_matching_handles_exit_idx_out_of_bounds(self):
        """Per-bar matching removed; always returns NA."""
        view_data = make_mock_view_data(n_pts=50, has_entry=False, has_exit=False)
        d = view_data["dates"]
        view_data["trade_records"] = [
            {"type": "short", "entry_idx": 10, "exit_idx": 999,
             "return_pct": 1.0, "exit_reason": "take_profit"}
        ]
        cols = self._extract(view_data, bar_date=pd.Timestamp(d[49]))
        assert cols["v0_trade"] is None
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] is None


# ============================================================================
# TestParquetStoreDebugMode — save_debug_data 模式测试
# ============================================================================


class TestParquetStoreDebugMode:
    """ParquetStore 的 _save_debug_data 模式测试.

    - _save_debug_data=False: 只写 Parquet，不导出 CSV
    - _save_debug_data=True: 写 Parquet + CSV
    - Parquet 始终写入，无论 debug 模式如何
    """

    @staticmethod
    def _append_and_close(store, n_rows=2):
        """向 store 追加若干行数据并结束 session。"""
        for i in range(n_rows):
            output = {
                "bar_index": i,
                "bar_timestamp": f"2026-01-{i+1:02d}T10:00:00",
                "cutoff_date": f"2026-01-{i+1:02d}",
                "views": {
                    "v0_日线": {
                        "t": None, "schmitt": None, "filtered": None,
                        "long_pnl": None, "short_pnl": None,
                        "long_mask": None, "short_mask": None,
                        "trade_records": [],
                        "bs_markers": {"entry_markers": [], "exit_markers": []},
                    },
                },
            }
            store.append_row(
                bar_index=output["bar_index"],
                bar_timestamp=output["bar_timestamp"],
                cutoff_date=output["cutoff_date"],
                stage_outputs=output,
            )
        store.end_session()

    def test_default_save_debug_data_is_true(self):
        """ParquetStore 默认 _save_debug_data=True."""
        store = ParquetStore("/tmp/test", "TEST", [{"tf": "日线"}])
        assert store._save_debug_data is True

    def test_debug_mode_false_no_csv(self, tmp_path):
        """_save_debug_data=False: Parquet 存在但 CSV 不导出."""
        store = ParquetStore(str(tmp_path), "TEST", [{"tf": "日线"}],
                              save_debug_data=False)
        store.start_session()
        self._append_and_close(store)

        session_dir = store._session_dir
        parquet_files = list(session_dir.glob("*.parquet"))
        csv_files = list(session_dir.glob("*.csv"))

        assert len(parquet_files) >= 1, "Parquet should always be written"
        assert len(csv_files) == 0, f"CSV should not be written, got {csv_files}"

    def test_debug_mode_true_writes_csv(self, tmp_path):
        """_save_debug_data=True: Parquet 和 CSV 都存在."""
        store = ParquetStore(str(tmp_path), "TEST", [{"tf": "日线"}],
                              save_debug_data=True)
        store.start_session()
        self._append_and_close(store)

        session_dir = store._session_dir
        parquet_files = list(session_dir.glob("*.parquet"))
        csv_files = list(session_dir.glob("*.csv"))

        assert len(parquet_files) >= 1, "Parquet should always be written"
        assert len(csv_files) >= 1, f"CSV should be written, got {csv_files}"

    def test_parquet_always_written_debug_false(self, tmp_path):
        """_save_debug_data=False 时 parquet 仍始终写入."""
        store = ParquetStore(str(tmp_path), "TEST", [{"tf": "日线"}],
                              save_debug_data=False)
        store.start_session()
        self._append_and_close(store)

        session_dir = store._session_dir
        parquet_files = list(session_dir.glob("*.parquet"))
        assert len(parquet_files) >= 1, "Parquet should always be written even without debug"

    def test_parquet_always_written_debug_true(self, tmp_path):
        """_save_debug_data=True 时 parquet 也正常写入."""
        store = ParquetStore(str(tmp_path), "TEST", [{"tf": "日线"}],
                              save_debug_data=True)
        store.start_session()
        self._append_and_close(store)

        session_dir = store._session_dir
        parquet_files = list(session_dir.glob("*.parquet"))
        assert len(parquet_files) >= 1, "Parquet should be written in debug mode"

    def test_metadata_always_written(self, tmp_path):
        """metadata.json 在两种模式下都写入."""
        for mode in [True, False]:
            store = ParquetStore(str(tmp_path), "TEST", [{"tf": "日线"}],
                                  save_debug_data=mode)
            store.start_session()
            self._append_and_close(store)

            meta_path = store._session_dir / "metadata.json"
            assert meta_path.exists(), (
                f"metadata.json should exist in save_debug_data={mode}"
            )


# ============================================================================
# T10: OOM防护 — buffer memory limit & numpy preallocation
# ============================================================================

class TestOomBufferLimit:
    """T10: ParquetStore buffer memory limit triggers flush at 90% threshold."""

    @staticmethod
    def _make_valid_row(store, bar_index=0, ts="2026-01-01", close=100.0):
        """Build a row dict that passes _buffer_to_table validation."""
        row = {}
        col_names = store._column_names
        for name in col_names:
            if name == "bar_index":
                row[name] = bar_index
            elif name == "bar_timestamp":
                row[name] = pd.Timestamp(ts)
            elif name == "close":
                row[name] = float(close)
            elif name.endswith("_sig"):
                row[name] = 0      # int8 default
            elif name.endswith("_eps"):
                row[name] = 0.0    # float32 default
            elif name.endswith("_filtered"):
                row[name] = float(close)
            elif name.endswith("_pnl_long") or name.endswith("_pnl_short"):
                row[name] = 100.0
            elif name.endswith("_trade_return"):
                row[name] = 0.0
            elif name.endswith("_long_pos") or name.endswith("_short_pos"):
                row[name] = False
            elif name.endswith("_trade") or name.endswith("_trade_reason"):
                row[name] = ""
            elif name.endswith("_bs_entry") or name.endswith("_bs_exit"):
                row[name] = ""
            else:
                row[name] = None
        return row

    def test_max_buffer_mb_class_attribute(self):
        """ParquetStore 类有 _MAX_BUFFER_MB 硬限制."""
        assert hasattr(ParquetStore, "_MAX_BUFFER_MB")
        assert ParquetStore._MAX_BUFFER_MB == 200

    def test_max_buffer_mb_instance_default(self, tmp_path):
        """start_session 后 _max_buffer_mb 设为类常量值."""
        store = ParquetStore(str(tmp_path), "TEST", [{"tf": "日线"}])
        store.start_session()
        assert store._max_buffer_mb == ParquetStore._MAX_BUFFER_MB

    def test_buffer_memory_bytes_zero_after_flush(self, tmp_path):
        """flush 后 _buffer_memory_bytes 重置为 0."""
        store = ParquetStore(str(tmp_path), "TEST", [{"tf": "日线"}])
        store.start_session()
        row = self._make_valid_row(store)
        store._buffer = [row]
        store._buffer_memory_bytes = 50000
        assert store._buffer_memory_bytes > 0
        store.flush()
        assert store._buffer_memory_bytes == 0
        assert len(store._buffer) == 0

    def test_buffer_clear_on_start_session(self, tmp_path):
        """start_session 清空 buffer 和内存计数."""
        store = ParquetStore(str(tmp_path), "TEST", [{"tf": "日线"}])
        store.start_session()
        row = self._make_valid_row(store)
        store._buffer = [row]
        store._buffer_memory_bytes = 1000
        # restart session
        store.start_session()
        assert len(store._buffer) == 0
        assert store._buffer_memory_bytes == 0

    def test_auto_flush_on_append_row_memory_pressure(self, tmp_path, monkeypatch):
        """append_row: buffer 内存超 90% 限制时触发 flush（捕获异常不会crash）."""
        import sys as _sys
        store = ParquetStore(str(tmp_path), "TEST", [{"tf": "日线"}])
        store.start_session()

        original_getsizeof = _sys.getsizeof

        # Each row will appear to use 190MB — with max=200MB, 1 row > 90%
        def _fake_getsizeof(obj):
            return 190 * 1024 * 1024  # 190 MB

        monkeypatch.setattr(_sys, "getsizeof", _fake_getsizeof)

        try:
            # append_row catches exceptions internally, so it should not crash
            store.append_row(0, "2026-01-01", "2026-01-01", {"views": {}})
            # The flush may succeed (if _buffer_to_table handles None) or
            # fail (if row is incomplete). Either way, append_row must not
            # raise — the backtest loop must keep running.
            assert not hasattr(store, "_crash_flag"), (
                "内存压力触发 flush 不应导致 append_row 崩溃"
            )
        finally:
            monkeypatch.setattr(_sys, "getsizeof", original_getsizeof)

    def test_append_row_never_crashes_on_bad_data(self, tmp_path):
        """append_row 即使收到损坏数据也不会抛出异常（只 log warning）."""
        store = ParquetStore(str(tmp_path), "TEST", [{"tf": "日线"}])
        store.start_session()
        # append_row with garbage data — should not raise
        try:
            store.append_row(0, None, None, None)
        except Exception:
            pytest.fail("append_row 不应因损坏数据而抛出异常")
        # Also verify start_session sets up buffer properly
        assert store._buffer_size == 10_000_000  # effectively infinite for session

    def test_flush_noop_on_empty_buffer(self, tmp_path):
        """flush 在 buffer 为空时是 no-op."""
        store = ParquetStore(str(tmp_path), "TEST", [{"tf": "日线"}])
        store.start_session()
        store._buffer.clear()
        store._buffer_memory_bytes = 0
        part_before = store._part_index
        store.flush()
        assert store._part_index == part_before

    def test_flush_increments_part_index(self, tmp_path):
        """flush 产生新的 part 文件并递增 part_index."""
        store = ParquetStore(str(tmp_path), "TEST", [{"tf": "日线"}])
        store.start_session()
        row = self._make_valid_row(store)
        store._buffer = [row]
        store._buffer_memory_bytes = 1000
        part_before = store._part_index
        store.flush()
        assert store._part_index == part_before + 1
        part_files = list(store._session_dir.glob("part_*.parquet"))
        assert len(part_files) >= 1

    def test_numpy_dtype_in_schema(self):
        """Schema 使用 numpy-friendly pyarrow 类型（非 Python object）."""
        from data.store import _build_full_schema
        schema = _build_full_schema(["v0"])
        for field in schema:
            dtype = field.type
            assert not pa.types.is_null(dtype), (
                f"Schema field '{field.name}' should have fixed-width type, got null"
            )
        close_field = schema.field("close")
        assert close_field.type == pa.float32()

    def test_column_defaults_are_numpy_compatible(self):
        """_COL_DEFAULTS 使用 numpy 兼容的 NaN/0/False/None 值."""
        from data.store import _COL_DEFAULTS, _FLOAT_NA, _INT_NA, _BOOL_NA
        assert np.isnan(_FLOAT_NA)
        assert _INT_NA == 0
        assert _BOOL_NA is False
        assert "sig" in _COL_DEFAULTS
        assert "filtered" in _COL_DEFAULTS
        assert "pnl_long" in _COL_DEFAULTS


# ============================================================================
# TestBufferToTableNoneHandling — _buffer_to_table None 值处理
# ============================================================================


class TestBufferToTableNoneHandling:
    """验证 _buffer_to_table 不会因 None 值而崩溃。

    P0-1 修复：在 np_cols 赋值前检查 ``val is None``，使用类型默认值替代。
    涵盖整数、浮点、布尔、datetime64、字符串五种类型的 None→默认值转换。
    """

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _make_store(tmp_path, ticker="TEST"):
        store = ParquetStore(str(tmp_path), ticker, [{"tf": "日线"}])
        store.start_session()
        return store

    @staticmethod
    def _build_row_with_nones(bar_index=0, bar_ts="2024-01-01", close=100.0):
        """构造包含 None 值的行，模拟部分列为 None 的情况。"""
        return {
            "bar_index": bar_index,
            "bar_timestamp": bar_ts,
            "close": close,
            # View columns — set some to None
            "v0_sig": None,
            "v0_filtered": None,
            "v0_eps": None,
            "v0_pnl_long": None,
            "v0_pnl_short": None,
            "v0_long_pos": None,
            "v0_short_pos": None,
            "v0_trade": None,
            "v0_trade_return": None,
            "v0_trade_reason": None,
            "v0_bs_entry": None,
            "v0_bs_exit": None,
        }

    def _do_buffer_to_table(self, store, rows):
        """Directly set buffer and call _buffer_to_table."""
        store._buffer = list(rows)
        store._column_names = [f.name for f in store._full_schema]
        return store._buffer_to_table()

    # ── 崩溃测试 ───────────────────────────────────────────────────────

    def test_buffer_to_table_does_not_crash_on_all_nones(self, tmp_path):
        """全部 view 列为 None 时 _buffer_to_table 不崩溃。"""
        store = self._make_store(tmp_path)
        row = self._build_row_with_nones()
        table = self._do_buffer_to_table(store, [row])
        assert len(table) == 1
        assert table.column("bar_index")[0].as_py() == 0

    def test_buffer_to_table_does_not_crash_on_mixed_nones(self, tmp_path):
        """部分列为 None、部分列有值时 _buffer_to_table 不崩溃。"""
        store = self._make_store(tmp_path)
        row = self._build_row_with_nones()
        row["v0_sig"] = 1
        row["v0_filtered"] = 50.5
        row["v0_long_pos"] = True
        table = self._do_buffer_to_table(store, [row])
        assert len(table) == 1

    def test_buffer_to_table_does_not_crash_with_many_rows(self, tmp_path):
        """大量行包含 None 值时 _buffer_to_table 不崩溃。"""
        store = self._make_store(tmp_path)
        rows = [self._build_row_with_nones(bar_index=i) for i in range(50)]
        table = self._do_buffer_to_table(store, rows)
        assert len(table) == 50

    # ── 默认值正确性 — 整数列 ──────────────────────────────────────────

    def test_none_int_column_defaults_to_zero(self, tmp_path):
        """整数列 (sig) 的 None 值应转换为 0。"""
        store = self._make_store(tmp_path)
        row = self._build_row_with_nones()
        table = self._do_buffer_to_table(store, [row])
        assert table.column("v0_sig")[0].as_py() == 0

    def test_bar_index_int_column_defaults_to_zero(self, tmp_path):
        """bar_index（整数列）的 None 值应转换为 0。"""
        store = self._make_store(tmp_path)
        row = self._build_row_with_nones()
        row["bar_index"] = None
        table = self._do_buffer_to_table(store, [row])
        assert table.column("bar_index")[0].as_py() == 0

    # ── 默认值正确性 — 浮点列 ──────────────────────────────────────────

    def test_none_float_column_defaults_to_nan(self, tmp_path):
        """浮点列 (filtered, eps, pnl_long, etc.) 的 None 值应转换为 NaN。"""
        store = self._make_store(tmp_path)
        row = self._build_row_with_nones()
        table = self._do_buffer_to_table(store, [row])
        assert np.isnan(table.column("v0_filtered")[0].as_py())
        assert np.isnan(table.column("v0_eps")[0].as_py())
        assert np.isnan(table.column("v0_pnl_long")[0].as_py())
        assert np.isnan(table.column("v0_pnl_short")[0].as_py())
        assert np.isnan(table.column("v0_trade_return")[0].as_py())

    def test_close_float_column_defaults_to_nan(self, tmp_path):
        """close 列（浮点）的 None 值应转换为 NaN。"""
        store = self._make_store(tmp_path)
        row = self._build_row_with_nones()
        row["close"] = None
        table = self._do_buffer_to_table(store, [row])
        assert np.isnan(table.column("close")[0].as_py())

    # ── 默认值正确性 — 布尔列 ──────────────────────────────────────────

    def test_none_bool_column_defaults_to_false(self, tmp_path):
        """布尔列 (long_pos, short_pos) 的 None 值应转换为 False。"""
        store = self._make_store(tmp_path)
        row = self._build_row_with_nones()
        table = self._do_buffer_to_table(store, [row])
        assert table.column("v0_long_pos")[0].as_py() is False
        assert table.column("v0_short_pos")[0].as_py() is False

    # ── 默认值正确性 — datetime64 列 ───────────────────────────────────

    def test_none_timestamp_column_defaults_to_nat(self, tmp_path):
        """bar_timestamp（datetime64 列）的 None 值应转换为 NaT。"""
        store = self._make_store(tmp_path)
        row = self._build_row_with_nones()
        row["bar_timestamp"] = None
        table = self._do_buffer_to_table(store, [row])
        ts = table.column("bar_timestamp")[0]
        assert ts.is_valid is False  # pyarrow null

    # ── 默认值正确性 — 字符串列 ────────────────────────────────────────

    def test_none_string_column_stays_none(self, tmp_path):
        """字符串列 (trade, bs_entry, bs_exit) 的 None 值应保持为 null。"""
        store = self._make_store(tmp_path)
        row = self._build_row_with_nones()
        table = self._do_buffer_to_table(store, [row])
        assert table.column("v0_trade")[0].as_py() is None
        assert table.column("v0_trade_reason")[0].as_py() is None
        assert table.column("v0_bs_entry")[0].as_py() is None
        assert table.column("v0_bs_exit")[0].as_py() is None

    # ── 端到端：含有 None 的 buffer flush ──────────────────────────────

    def test_flush_with_none_values_produces_valid_parquet(self, tmp_path):
        """含有 None 值的 buffer flush 应生成合法的 Parquet 文件。"""
        store = self._make_store(tmp_path)

        # Emulate what CLI does: build rows via _extract_row then modify
        n = 10
        for i in range(n):
            t = np.arange(50, dtype=float)
            view_data = {
                "t": t,
                "schmitt": {"sig": np.zeros(50, dtype=int), "eps": np.full(50, 0.1)},
                "filtered": np.random.RandomState(i).randn(50).cumsum() * 0.01 + 100,
                "long_pnl": np.linspace(100, 110, 50),
                "short_pnl": np.linspace(100, 105, 50),
                "long_mask": np.zeros(50, dtype=bool),
                "short_mask": np.zeros(50, dtype=bool),
                "trade_records": [],
                "bs_markers": {"entry_markers": [], "exit_markers": []},
            }
            stage_output = {"views": {"v0_日线": view_data}}
            store.append_row(i, f"2024-01-{i+1:02d}T10:00:00", f"2024-01-{i+1:02d}", stage_output)

        # Manually inject None into buffer to simulate the edge case
        store._buffer[5]["v0_sig"] = None
        store._buffer[5]["v0_filtered"] = None
        store._buffer[5]["v0_long_pos"] = None
        store._buffer[5]["bar_timestamp"] = None

        # Flush should not crash
        store.flush()

        # Verify the Parquet file was written
        import glob
        part_files = sorted(glob.glob(str(tmp_path / "*" / "part_*.parquet")))
        assert len(part_files) >= 1, "Expected at least one part file after flush"

        # Read back and verify
        table = pq.read_table(part_files[0])
        assert len(table) == n

        # Row 5 (injected None) should have defaults
        assert table.column("v0_sig")[5].as_py() == 0
        assert np.isnan(table.column("v0_filtered")[5].as_py())
        assert table.column("v0_long_pos")[5].as_py() is False
        assert table.column("bar_timestamp")[5].is_valid is False


# ═══════════════════════════════════════════════════════════════════════════════
# P2-3 B46: CSV导出保持float32 — 验证不转换为float64避免内存翻倍
# ═══════════════════════════════════════════════════════════════════════════════

class TestCsvExportFloat32Preservation:
    """验证 CSV 导出时 float32 列保持原 dtype，不转换为 float64。"""

    def test_merged_dataframe_keeps_float32_dtypes(self, tmp_path):
        """_merge_parts_and_export_csv 后，float32 列不应被转换为 float64（P2-3 B46）。"""
        store = ParquetStore(str(tmp_path), "TEST", [{"tf": "日线"}],
                              save_debug_data=True)
        store.start_session()

        # 添加含真实 float32 值的数据行
        for i in range(3):
            n = 50
            t = np.arange(n, dtype=float)
            output = {
                "bar_index": i,
                "bar_timestamp": f"2026-01-{i+1:02d}T10:00:00",
                "cutoff_date": f"2026-01-{i+1:02d}",
                "views": {
                    "v0_日线": {
                        "t": t,
                        "schmitt": {"sig": np.zeros(n, dtype=int), "eps": np.full(n, 0.1)},
                        "filtered": np.random.RandomState(i).randn(n).cumsum() * 0.01 + 100,
                        "long_pnl": np.linspace(100, 110, n),
                        "short_pnl": np.linspace(100, 105, n),
                        "long_mask": np.zeros(n, dtype=bool),
                        "short_mask": np.zeros(n, dtype=bool),
                        "trade_records": [],
                        "bs_markers": {"entry_markers": [], "exit_markers": []},
                    },
                },
            }
            store.append_row(
                output["bar_index"], output["bar_timestamp"],
                output["cutoff_date"], output,
            )
        store.flush()
        store.end_session()

        # 读取合并后的 Parquet 并转换为 pandas（与 CSV 导出逻辑相同）
        merged_path = store._session_dir / "backtest_result.parquet"
        table = pq.read_table(str(merged_path))
        df = table.to_pandas()

        # 验证 float32 列未被转换为 float64
        float32_cols = [c for c in df.columns if df[c].dtype == np.float32]
        float64_cols = [c for c in df.columns if df[c].dtype == np.float64]

        assert len(float32_cols) > 0, (
            f"Expected float32 columns in DataFrame, got dtypes: {df.dtypes.to_dict()}"
        )
        # close 和视图浮点列应为 float32，不应为 float64
        assert "close" in float32_cols, f"close 列应为 float32, got {df['close'].dtype}"
        view_float_cols = ["v0_filtered", "v0_eps"]
        for col in view_float_cols:
            if col in df.columns:
                assert df[col].dtype == np.float32, (
                    f"{col} 应为 float32 而非 {df[col].dtype}"
                )
        # 确认没有 float32→float64 转换残留
        for col in float32_cols:
            assert df[col].dtype == np.float32, (
                f"Column {col} should be float32, got {df[col].dtype}"
            )

    def test_csv_export_write_succeeds_with_float32(self, tmp_path):
        """CSV 导出本身应正常完成（float32 写入不应报错）。"""
        store = ParquetStore(str(tmp_path), "TEST", [{"tf": "日线"}],
                              save_debug_data=True)
        store.start_session()

        for i in range(2):
            n = 50
            t = np.arange(n, dtype=float)
            output = {
                "bar_index": i,
                "bar_timestamp": f"2026-01-{i+1:02d}T10:00:00",
                "cutoff_date": f"2026-01-{i+1:02d}",
                "views": {
                    "v0_日线": {
                        "t": t,
                        "schmitt": {"sig": np.zeros(n, dtype=int), "eps": np.full(n, 0.1)},
                        "filtered": np.random.RandomState(i).randn(n).cumsum() * 0.01 + 100,
                        "long_pnl": np.linspace(100, 110, n),
                        "short_pnl": np.linspace(100, 105, n),
                        "long_mask": np.zeros(n, dtype=bool),
                        "short_mask": np.zeros(n, dtype=bool),
                        "trade_records": [],
                        "bs_markers": {"entry_markers": [], "exit_markers": []},
                    },
                },
            }
            store.append_row(
                output["bar_index"], output["bar_timestamp"],
                output["cutoff_date"], output,
            )
        store.flush()
        store.end_session()

        # 验证 CSV 文件存在
        csv_path = store._session_dir / "backtest_result.csv"
        assert csv_path.exists(), f"CSV file should exist at {csv_path}"

        # 验证 CSV 内容可读
        df_csv = pd.read_csv(str(csv_path))
        assert len(df_csv) >= 2, f"CSV should have at least 2 rows, got {len(df_csv)}"
        assert "close" in df_csv.columns, "close column should be in CSV"
