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

    def test_trade_exit_matching(self):
        """Trade with exit_idx=100 <= view_last_idx=119: match exit."""
        view_data = make_mock_view_data(n_pts=120, has_exit=True)
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

    def test_multiple_trades_last_wins(self):
        """Multiple trades: last matching trade by exit_idx overwrites."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 10, "exit_idx": 30,
             "return_pct": 1.0, "exit_reason": "first"},
            {"type": "short", "entry_idx": 40, "exit_idx": 80,
             "return_pct": 3.0, "exit_reason": "second"},
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] == "exit_short"  # second trade wins
        assert cols["v0_trade_return"] == pytest.approx(3.0)
        assert cols["v0_trade_reason"] == "second"

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

    def test_trade_exit_matching(self):
        """Exit-based trade match: return_pct and exit_reason extracted."""
        view_data = make_mock_view_data(n_pts=120, has_exit=True)
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

    def test_multiple_trades_last_wins_csv(self):
        """Multiple matching trades: last one wins via overwrite."""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 10, "exit_idx": 30,
             "return_pct": 1.0, "exit_reason": "a"},
            {"type": "short", "entry_idx": 40, "exit_idx": 80,
             "return_pct": 3.0, "exit_reason": "b"},
        ]
        cols = self._extract("v0_日线", view_data)
        assert cols["v0_trade"] == "exit_short"
        assert cols["v0_trade_return"] == pytest.approx(3.0)
        assert cols["v0_trade_reason"] == "b"

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
        """trade_records is None: trade_count is not set.

        NOTE: CSVBuilder does NOT guard against None with `or []` (unlike
        ParquetStore), so the trade-matching loop would crash on None.
        This test uses [] as the safe empty value.
        """
        view_data = make_mock_view_data()
        view_data["trade_records"] = []
        cols = self._extract("v0_日线", view_data)
        assert cols["v0_trade_count"] == 0


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
    """回归测试：entry_idx 此前用 == 导致永远无法匹配。

    Bug: 原始代码使用 ``if entry_idx == view_last_idx`` 来匹配 trade，
    但 entry_idx (pair_end) 几乎永远不会恰好落在 view_last_idx 上。
    修复方案：改用 ``<=``，并确保 exit 优先于 entry。
    """

    @staticmethod
    def _extract(view_data: dict, prefix: str = "v0") -> dict:
        return ParquetStore._extract_view_columns(prefix, view_data)

    def test_trade_entry_before_last_bar(self):
        """trade entry_idx 在 view_last_idx-5，应被匹配为 entry_long。"""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 114, "exit_idx": None}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] == "entry_long"
        assert np.isnan(cols["v0_trade_return"])
        assert cols["v0_trade_reason"] == ""

    def test_trade_exit_before_last_bar(self):
        """trade exit_idx 在 view_last_idx-3，应被匹配为 exit_long 并提取 return_pct。"""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 50, "exit_idx": 116,
             "return_pct": 3.5, "exit_reason": "take_profit"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] == "exit_long"
        assert cols["v0_trade_return"] == pytest.approx(3.5)
        assert cols["v0_trade_reason"] == "take_profit"

    def test_trade_entry_and_exit_both_match(self):
        """同一 trade 的 entry 和 exit 都 <= view_last_idx，应优先 exit。"""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "long", "entry_idx": 50, "exit_idx": 100,
             "return_pct": 2.0, "exit_reason": "stop_loss"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] == "exit_long"  # exit 优先
        assert cols["v0_trade_return"] == pytest.approx(2.0)
        assert cols["v0_trade_reason"] == "stop_loss"

    def test_trade_same_bar_entry_exit(self):
        """entry_idx == exit_idx，应优先 exit。"""
        view_data = make_mock_view_data(n_pts=120, has_entry=False, has_exit=False)
        view_data["trade_records"] = [
            {"type": "short", "entry_idx": 80, "exit_idx": 80,
             "return_pct": -1.5, "exit_reason": "stop_loss"}
        ]
        cols = self._extract(view_data)
        assert cols["v0_trade"] == "exit_short"  # exit 优先于同 bar 的 entry
        assert cols["v0_trade_return"] == pytest.approx(-1.5)
        assert cols["v0_trade_reason"] == "stop_loss"


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

    @pytest.mark.xfail(
        reason="CSVBuilder does not guard bs_markers=None (uses .get() on None)"
    )
    def test_bs_markers_none_crash(self):
        """bs_markers 为 None 时不应崩溃（已知缺陷：当前会 AttributeError）。"""
        view_data = make_mock_view_data(has_entry=False, has_exit=False)
        view_data["bs_markers"] = None
        cols = self._extract("v0_日线", view_data)
        assert cols["v0_bs_entry"] == "-"

    @pytest.mark.xfail(
        reason="CSVBuilder does not guard trade_records=None (iterates None)"
    )
    def test_trade_records_none_crash(self):
        """trade_records 为 None 时不应崩溃（已知缺陷：当前会 TypeError）。"""
        view_data = make_mock_view_data(has_entry=False, has_exit=False)
        view_data["trade_records"] = None
        cols = self._extract("v0_日线", view_data)
        assert cols["v0_trade"] == ""

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
