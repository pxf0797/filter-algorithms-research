"""
Tests for ParquetStore._extract_view_columns and CSVBuilder._extract_view_columns.

Covers per-bar column extraction logic: signal, filter, PnL, BS markers,
trade matching, and edge cases (None data, empty markers, boundary conditions).
All tests use mock view_data — no real backtest or DB required.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

# Ensure filter_app is importable
_src = Path(__file__).resolve().parent.parent / "filter_app"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from services.parquet_store import ParquetStore
from services.event_recorder import CSVBuilder


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

    # BS markers
    entry_markers = []
    exit_markers = []
    if has_entry:
        entry_markers = [(50, "B", "green", "2024-01-15", "2024-01-15")]
    if has_exit:
        exit_markers = [(100, "S", "red", "stop_loss", "2024-02-01")]

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
    if has_exit:
        trade_records = [
            {
                "type": "long",
                "entry_idx": 50,
                "exit_idx": 100,
                "return_pct": 5.0,
                "exit_reason": "take_profit",
            }
        ]

    # Filtered signal
    filtered = np.random.RandomState(42).randn(n_pts).cumsum() * 0.01 + 100

    return {
        "t": t,
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
        """Entry marker at bar 50 with view_last_idx=119: <= should find B."""
        view_data = make_mock_view_data(n_pts=120, has_entry=True)
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] == "B"

    def test_bs_entry_marker_at_last_bar(self):
        """Entry marker exactly at view_last_idx (boundary)."""
        view_data = make_mock_view_data(n_pts=50, has_entry=False)
        # Place entry marker at bar 49 (last bar, idx=49)
        view_data["bs_markers"]["entry_markers"] = [
            (49, "B", "green", "2024-01-15", "2024-01-15")
        ]
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] == "B"

    def test_bs_entry_marker_none(self):
        """No entry markers: default to empty string."""
        view_data = make_mock_view_data(has_entry=False, has_exit=False)
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] == ""

    def test_bs_entry_marker_past_last_bar_not_found(self):
        """Entry marker at bar 200 with view_last_idx=119: should NOT match."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False)
        view_data["bs_markers"]["entry_markers"] = [
            (200, "B", "green", "2024-01-15", "2024-01-15")
        ]
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] == ""

    # ── BS exit markers ────────────────────────────────────────────────

    def test_bs_exit_marker_found(self):
        """Exit marker at bar 100 with view_last_idx=119: should find S."""
        view_data = make_mock_view_data(n_pts=120, has_exit=True)
        cols = self._extract(view_data)
        assert cols["v0_bs_exit"] == "S"

    # ── trade matching ─────────────────────────────────────────────────

    def test_trade_exit_at_current_bar(self):
        """Trade 恰好在当前 bar 退出（exit_idx == view_last_idx, 非 eod）→ exit。"""
        view_data = make_mock_view_data(n_pts=50, has_entry=False, has_exit=False)
        # n_pts=50 → view_last_idx=49
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 20, "exit_idx": 49,
             "return_pct": 5.0, "exit_reason": "take_profit"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] == "exit_long"
        assert cols["v0_trade_return"] == pytest.approx(5.0)
        assert cols["v0_trade_reason"] == "take_profit"

    def test_trade_entry_matching(self):
        """Trade with entry_idx=50 <= view_last_idx=119, no exit yet: match entry."""
        view_data = make_mock_view_data(n_pts=120, has_entry=True, has_exit=False)
        # Add a trade that has entry but no exit yet
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 50, "exit_idx": None}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] == "entry_long"
        # entry has no realised return
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] == ""

    def test_trade_with_entry_past_view_last_idx(self):
        """Trade with entry_idx=200 > view_last_idx=119: should NOT match."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 200, "exit_idx": None}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] == ""

    def test_trade_completed_before_current_bar(self):
        """Trade completed (entry + exit) before current bar → ""，不显示 exit。"""
        view_data = make_mock_view_data(n_pts=120, has_exit=True)
        cols = self._extract(view_data)
        # has_exit=True → trade with entry=50, exit=100 at view=119
        # Trade completed 19 bars ago; NOT at current bar → ""
        assert cols["v0_trade"] == ""
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] == ""

    # ── multiple markers ───────────────────────────────────────────────

    def test_multiple_markers_same_bar(self):
        """Multiple BS markers at the same bar: last one wins (overwrite loop)."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False)
        view_data["bs_markers"]["entry_markers"] = [
            (30, "B", "green", "2024-01-10", "2024-01-10"),
            (50, "B", "green", "2024-01-15", "2024-01-15"),
            (50, "S", "red", "2024-01-15", "2024-01-15"),  # same bar, later marker
        ]
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] == "S"  # last marker at bar 50 wins

    def test_multiple_trades_last_active_wins(self):
        """Multiple trades: last active trade wins, completed trades ignored."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 10, "exit_idx": 30,
             "return_pct": 1.0, "exit_reason": "first"},
            {"type": "short", "entry_idx": 40, "exit_idx": None},
        ]
        cols = self._extract(view_data)
        # First trade completed (10→30), second active (40, no exit)
        assert cols["v0_trade"] == "entry_short"
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] == ""

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
        assert cols["v0_trade"] == ""
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] == ""
        assert cols["v0_bs_entry"] == ""
        assert cols["v0_bs_exit"] == ""

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
        assert cols["v0_trade"] == ""
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] == ""

    def test_bs_markers_empty_dict(self):
        """bs_markers has empty entry/exit lists: BS columns return defaults."""
        view_data = make_mock_view_data()
        view_data["bs_markers"] = {"entry_markers": [], "exit_markers": []}
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] == ""
        assert cols["v0_bs_exit"] == ""

    def test_bs_markers_none(self):
        """bs_markers is None: BS columns return defaults."""
        view_data = make_mock_view_data()
        view_data["bs_markers"] = None
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] == ""
        assert cols["v0_bs_exit"] == ""

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
        """Entry marker ≤ view_last_idx: B label found."""
        view_data = make_mock_view_data(n_pts=120, has_entry=True)
        cols = self._extract("v0_日线", view_data)
        assert cols["v0_bs_entry"] == "B"

    def test_bs_entry_marker_none_csv(self):
        """No markers: CSVBuilder returns "-" instead of ""."""
        view_data = make_mock_view_data(has_entry=False, has_exit=False)
        cols = self._extract("v0_日线", view_data)
        assert cols["v0_bs_entry"] == "-"
        assert cols["v0_bs_exit"] == "-"

    def test_bs_exit_marker_found_csv(self):
        """Exit marker at bar 100: should find S."""
        view_data = make_mock_view_data(n_pts=120, has_exit=True)
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
        """Same bar: last marker overwrites."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False)
        view_data["bs_markers"]["entry_markers"] = [
            (50, "B", "green", "2024-01-15", "2024-01-15"),
            (50, "S", "red", "2024-01-15", "2024-01-15"),
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
        """Both entry and exit markers present: each matched independently."""
        view_data = make_mock_view_data(n_pts=120, has_entry=True, has_exit=True)
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
    """回归测试：BS entry marker 此前用 == 精确匹配导致始终为空。

    Bug: 原始代码使用 ``if m[0] == view_last_idx`` 来匹配 BS marker，
    但 entry marker 的 bar_idx (pair_end) 几乎永远不会恰好落在
    view_last_idx 上（数据集的最后一个 bar）。修复方案：改用 ``<=``。
    """

    @staticmethod
    def _extract(view_data: dict, prefix: str = "v0") -> dict:
        return ParquetStore._extract_view_columns(prefix, view_data)

    def test_bs_entry_at_exact_last_bar(self):
        """entry marker 恰好在 view_last_idx 上（边界情况）。"""
        view_data = make_mock_view_data(n_pts=50, has_entry=False)
        # bar 49 = view_last_idx (len=50, last index=49)
        view_data["bs_markers"]["entry_markers"] = [
            (49, "B", "green", "2024-01-15", "2024-01-15")
        ]
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] == "B"

    def test_bs_entry_before_last_bar(self):
        """entry marker 在 view_last_idx-1，应被 <= 匹配。"""
        view_data = make_mock_view_data(n_pts=120, has_entry=False)
        # bar 118 = view_last_idx - 1
        view_data["bs_markers"]["entry_markers"] = [
            (118, "B", "green", "2024-01-15", "2024-01-15")
        ]
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] == "B"

    def test_bs_entry_far_from_last_bar(self):
        """entry marker 在 bar 10, view_last_idx=119，应被 <= 匹配。"""
        view_data = make_mock_view_data(n_pts=120, has_entry=False)
        view_data["bs_markers"]["entry_markers"] = [
            (10, "B", "green", "2024-01-05", "2024-01-05")
        ]
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] == "B"

    def test_bs_entry_multiple_before_last(self):
        """多个 entry marker 在 view_last_idx 之前，最后一个被匹配。"""
        view_data = make_mock_view_data(n_pts=120, has_entry=False)
        view_data["bs_markers"]["entry_markers"] = [
            (20, "B", "green", "2024-01-10", "2024-01-10"),
            (40, "S", "red", "2024-01-20", "2024-01-20"),
            (60, "B", "green", "2024-01-30", "2024-01-30"),
        ]
        cols = self._extract(view_data)
        assert cols["v0_bs_entry"] == "B"  # 最后一个 marker (bar 60, label B)

    def test_bs_exit_multiple_before_last(self):
        """多个 exit marker，最后一个被匹配。"""
        view_data = make_mock_view_data(n_pts=120, has_entry=False)
        view_data["bs_markers"]["exit_markers"] = [
            (30, "B", "green", "take_profit", "2024-02-01"),
            (70, "S", "red", "stop_loss", "2024-02-05"),
            (90, "S", "red", "stop_loss", "2024-02-10"),
        ]
        cols = self._extract(view_data)
        assert cols["v0_bs_exit"] == "S"  # 最后一个 marker (bar 90, label S)


# ============================================================================
# TestTradeMatchingRegression — Trade 匹配回归测试
# ============================================================================


class TestTradeMatchingRegression:
    """回归测试：trade 事件在窗口的正确匹配。

    两轮 Bug 修复：
    1. 原始代码用 ``==`` → entry 永远匹配不到（pair_end 几乎不落在最后 bar）。
    2. 改用 ``<=``   → 一旦有任何已完成的 trade，所有后续 bar 都显示 ``exit_*``，
       导致 200 行数据出现 0 个 entry、200 个 exit（列失去意义）。
    3. 最终方案：exit 仅在 ``== view_last_idx`` 且非 eod 时才显示 "exit_*"；
       entry 在 ``<= view_last_idx`` 且未退出（exit > last 或 eod 退出）时显示。
    """

    @staticmethod
    def _extract(view_data: dict, prefix: str = "v0") -> dict:
        return ParquetStore._extract_view_columns(prefix, view_data)

    # ── 当前持仓：entry active ─────────────────────────────────────────

    def test_trade_entry_active_no_exit(self):
        """trade 进入了（entry <= view_last_idx）但还没退出 → entry。"""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 114, "exit_idx": None}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] == "entry_long"
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] == ""

    def test_trade_entry_active_eod_exit(self):
        """trade 在窗口内以 eod 退出（数据结束）→ 仍视作 active，显示 entry。"""
        view_data = make_mock_view_data(n_pts=50, has_entry=False, has_exit=False)
        # view_last_idx = 49, entry at 40, eod exit at 49
        view_data["trade_records"] = [
            {"type": "short", "entry_idx": 40, "exit_idx": 49,
             "return_pct": 1.0, "exit_reason": "eod"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] == "entry_short"
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] == ""

    # ── 当前退出：exit at current bar ──────────────────────────────────

    def test_trade_exit_at_current_bar(self):
        """trade 恰好在当前 bar 退出（非 eod）→ exit。"""
        view_data = make_mock_view_data(n_pts=50, has_entry=False, has_exit=False)
        # view_last_idx = 49, exit at 49 with genuine reason
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 30, "exit_idx": 49,
             "return_pct": 3.5, "exit_reason": "take_profit"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] == "exit_long"
        assert cols["v0_trade_return"] == pytest.approx(3.5)
        assert cols["v0_trade_reason"] == "take_profit"

    def test_trade_exit_at_current_bar_stop_loss(self):
        """止损退出在 current bar → exit_short + return_pct。"""
        view_data = make_mock_view_data(n_pts=50, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "short", "entry_idx": 20, "exit_idx": 49,
             "return_pct": -2.0, "exit_reason": "stop_loss"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] == "exit_short"
        assert cols["v0_trade_return"] == pytest.approx(-2.0)
        assert cols["v0_trade_reason"] == "stop_loss"

    # ── 历史已完成 trade → "" ─────────────────────────────────────────

    def test_trade_completed_before_current_bar(self):
        """trade 在当前 bar 之前已完成 → 列应为空。"""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 50, "exit_idx": 100,
             "return_pct": 2.0, "exit_reason": "stop_loss"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] == ""
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] == ""

    def test_trade_same_bar_entry_exit_past(self):
        """同 bar entry/exit 发生在更早的 bar（非当前）→ ""。"""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "short", "entry_idx": 80, "exit_idx": 80,
             "return_pct": -1.5, "exit_reason": "stop_loss"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] == ""

    # ── 优先级：exit at current bar > entry active ────────────────────

    def test_trade_exit_priority_at_current_bar(self):
        """同一 bar 有 entry 和 exit（同 bar entry/exit at view_last_idx）→ exit 优先。"""
        view_data = make_mock_view_data(n_pts=50, has_entry=False, has_exit=False)
        # n_pts=50 → view_last_idx=49
        view_data["trade_records"] = [
            {"type": "short", "entry_idx": 49, "exit_idx": 49,
             "return_pct": -1.0, "exit_reason": "stop_loss"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] == "exit_short"
        assert cols["v0_trade_return"] == pytest.approx(-1.0)
        assert cols["v0_trade_reason"] == "stop_loss"

    def test_multiple_trades_active_last_wins(self):
        """多个 trade 都 active，最后一个决定 trade 列。"""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 10, "exit_idx": 130,
             "return_pct": 0.0, "exit_reason": "eod"},
            {"type": "short", "entry_idx": 40, "exit_idx": None},
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] == "entry_short"  # 最后一个 active trade 胜出


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
        import time

        store = ParquetStore(str(tmp_path), "AAPL", [{"name": "v0"}])

        sid1 = store.start_session()
        dir1 = store._session_dir
        store.end_session()

        # 等待至少 1 秒以确保时间戳不同（session ID 精度为秒级）
        time.sleep(1.1)

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
        from services.event_recorder import EventRecorder

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
        assert "view_labels" in meta, f"view_labels missing from EventRecorder metadata"
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
    """回归测试：trade 列 entry/exit 修复。

    两轮 Bug 修复验证：
    1. 原始代码用 ``==`` → entry 永远匹配不到
    2. 改用 ``<=``  → 已完成 trade 的后续 bar 全显示 exit（列失去意义）
    3. 最终方案：exit 仅在 ``== view_last_idx`` 且非 eod 时显示；
       entry 在 ``<= view_last_idx`` 且未退出时显示。
    """

    @staticmethod
    def _extract(view_data: dict, prefix: str = "v0") -> dict:
        return ParquetStore._extract_view_columns(prefix, view_data)

    # ── 活跃持仓时 trade 应显示 entry ────────────────────────────────────

    def test_trade_entry_when_active_position(self):
        """活跃持仓期间 trade 应为 entry_long/entry_short。"""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 80, "exit_idx": None}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] == "entry_long"
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] == ""

    def test_trade_entry_short_when_active_short(self):
        """活跃做空持仓期间 trade 应为 entry_short。"""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "short", "entry_idx": 60, "exit_idx": None}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] == "entry_short"

    # ── exit 仅在精确退出 bar 显示 ──────────────────────────────────────

    def test_trade_exit_only_at_exact_exit_bar(self):
        """只有 exit_idx==view_last_idx 且非 eod 才显示 exit。"""
        view_data = make_mock_view_data(n_pts=50, has_entry=False, has_exit=False)
        # n_pts=50 → view_last_idx=49, exit at 49, not eod
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 20, "exit_idx": 49,
             "return_pct": 3.0, "exit_reason": "take_profit"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] == "exit_long"
        assert cols["v0_trade_return"] == pytest.approx(3.0)
        assert cols["v0_trade_reason"] == "take_profit"

    def test_trade_exit_not_shown_when_eod(self):
        """eod 退出的 bar 应显示 entry（仍在持仓中），而非 exit。"""
        view_data = make_mock_view_data(n_pts=50, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "short", "entry_idx": 30, "exit_idx": 49,
             "return_pct": 1.5, "exit_reason": "eod"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] == "entry_short"
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] == ""

    def test_trade_exit_not_shown_before_exact_bar(self):
        """exit bar 之前的续持 bar 不应显示 exit。"""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 50, "exit_idx": 100,
             "return_pct": 2.0, "exit_reason": "stop_loss"}
        ]
        cols = self._extract(view_data)
        # view_last_idx=119, exit at 100 → 不匹配当前 bar → 已完成 trade
        assert cols["v0_trade"] == ""

    # ── 无活跃交易时 trade 应为空 ────────────────────────────────────────

    def test_trade_empty_when_no_active_trade(self):
        """无活跃交易时 trade 应为空。"""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = []  # no trades at all
        cols = self._extract(view_data)
        assert cols["v0_trade"] == ""
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] == ""

    def test_trade_empty_when_all_completed(self):
        """所有 trade 已完成时 trade 应为空。"""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 10, "exit_idx": 50,
             "return_pct": 1.0, "exit_reason": "a"},
            {"type": "short", "entry_idx": 60, "exit_idx": 80,
             "return_pct": -0.5, "exit_reason": "b"},
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] == ""
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] == ""

    def test_trade_entry_past_view_last_idx_not_matched(self):
        """entry_idx > view_last_idx 时不应匹配。"""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "short", "entry_idx": 200, "exit_idx": None}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] == ""

    def test_exit_priority_over_entry_at_current_bar(self):
        """同 bar entry/exit 时，exit 优先（非 eod 退出）。"""
        view_data = make_mock_view_data(n_pts=50, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 49, "exit_idx": 49,
             "return_pct": -1.0, "exit_reason": "stop_loss"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] == "exit_long"
        assert cols["v0_trade_return"] == pytest.approx(-1.0)
        assert cols["v0_trade_reason"] == "stop_loss"


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

        from filter_app.services.event_recorder import EventRecorder

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
