"""
Tests for ParquetStore track_pnl_from_price — price-based PnL tracking.

Covers: entry freeze, holding PnL formula (long / short), no-position freeze,
PnL/filtered ratio constancy, all 8 curves (4 views x long/short) zero
tracking error, and backward compatibility.
"""

import numpy as np
import pytest
from filter.data.store import ParquetStore


# ===========================================================================
# Helpers
# ===========================================================================

def _make_view(t_val=0, filtered_val=100.0, long_pnl=100.0, short_pnl=100.0,
               trade_records=None):
    """Build a minimal view-data dict for _extract_row input."""
    return {
        "t": np.array([t_val]),
        "schmitt": {"sig": np.array([0]), "eps": np.array([0.0])},
        "filtered": np.array([filtered_val]),
        "long_pnl": np.array([long_pnl]),
        "short_pnl": np.array([short_pnl]),
        "long_mask": np.array([False]),
        "short_mask": np.array([False]),
        "trade_records": trade_records or [],
        "bs_markers": {"entry_markers": [], "exit_markers": []},
        "dates": [],
    }


def _stage(view_data, close=100.0):
    """Build a minimal stage_output dict with one view (v0_test)."""
    return {
        "ohlcv": {"close": float(close)},
        "views": {"v0_test": view_data},
    }


def _long_entry():
    """Trade record: active long position (entry_idx=0, exit_idx=None)."""
    return [{"type": "long", "entry_idx": 0, "exit_idx": None}]


def _short_entry():
    """Trade record: active short position (entry_idx=0, exit_idx=None)."""
    return [{"type": "short", "entry_idx": 0, "exit_idx": None}]


def _long_exit(reason="signal"):
    """Trade record: long position exited at bar 0."""
    return [{"type": "long", "entry_idx": 0, "exit_idx": 0,
             "exit_reason": reason}]


def _short_exit(reason="signal"):
    """Trade record: short position exited at bar 0."""
    return [{"type": "short", "entry_idx": 0, "exit_idx": 0,
             "exit_reason": reason}]


def _new_store(tmp_path, track=True):
    """Create a ParquetStore with one view config and call start_session()."""
    store = ParquetStore(
        output_dir=str(tmp_path / "store_out"),
        ticker="TEST",
        view_configs=[{}],
        track_pnl_from_price=track,
    )
    store.start_session()
    return store


def _extract(store, view_data, close=100.0):
    """Call _extract_row and return the row dict."""
    return store._extract_row(0, "2024-01-01T00:00:00", _stage(view_data, close))


# ===========================================================================
# Unit tests — entry behaviour
# ===========================================================================

class TestEntryFreeze:
    """Entry: PnL equals frozen capital, eliminating sliding-window jumps."""

    def test_long_entry_pnl_matches_frozen_capital(self, tmp_path):
        """Long entry: PnL == frozen capital (not strategy-computed PnL)."""
        store = _new_store(tmp_path, track=True)

        # Bar 0 — no position, establish frozen value
        row0 = _extract(store, _make_view(t_val=0, filtered_val=100.0,
                                          long_pnl=100.0))
        assert row0["v0_pnl_long"] == pytest.approx(100.0)

        # Bar 1 — enter long; strategy PnL=150 but tracking freezes at 100
        row1 = _extract(store, _make_view(t_val=1, filtered_val=105.0,
                                          long_pnl=150.0,
                                          trade_records=_long_entry()))
        assert row1["v0_pnl_long"] == pytest.approx(100.0), (
            f"Long entry: expected frozen 100.0, got {row1['v0_pnl_long']} "
            f"(strategy gave 150.0 — jump must be eliminated)"
        )

    def test_short_entry_pnl_matches_frozen_capital(self, tmp_path):
        """Short entry: PnL == frozen capital (not strategy-computed PnL)."""
        store = _new_store(tmp_path, track=True)

        row0 = _extract(store, _make_view(t_val=0, filtered_val=100.0,
                                          short_pnl=100.0))
        assert row0["v0_pnl_short"] == pytest.approx(100.0)

        row1 = _extract(store, _make_view(t_val=1, filtered_val=95.0,
                                          short_pnl=80.0,
                                          trade_records=_short_entry()))
        assert row1["v0_pnl_short"] == pytest.approx(100.0), (
            f"Short entry: expected frozen 100.0, got {row1['v0_pnl_short']} "
            f"(strategy gave 80.0 — jump must be eliminated)"
        )


# ===========================================================================
# Unit tests — holding behaviour
# ===========================================================================

class TestHoldingFormula:
    """Holding: PnL precisely tracks filtered price changes."""

    def test_long_holding_pnl_follows_filtered(self, tmp_path):
        """Long: PnL = entry_capital * filtered / entry_filtered."""
        store = _new_store(tmp_path, track=True)

        _extract(store, _make_view(t_val=0, filtered_val=100.0))

        # Enter long at filtered=100, frozen=100
        row1 = _extract(store, _make_view(t_val=1, filtered_val=100.0,
                                          trade_records=_long_entry()))
        assert row1["v0_pnl_long"] == pytest.approx(100.0)

        # Holding: filtered rises to 110
        row2 = _extract(store, _make_view(t_val=2, filtered_val=110.0,
                                          trade_records=_long_entry()))
        expected = 100.0 * 110.0 / 100.0  # = 110.0
        assert row2["v0_pnl_long"] == pytest.approx(expected), (
            f"Long holding (filtered=110): expected {expected}, "
            f"got {row2['v0_pnl_long']}"
        )

        # Holding: filtered drops to 95
        row3 = _extract(store, _make_view(t_val=3, filtered_val=95.0,
                                          trade_records=_long_entry()))
        expected = 100.0 * 95.0 / 100.0  # = 95.0
        assert row3["v0_pnl_long"] == pytest.approx(expected), (
            f"Long holding (filtered=95): expected {expected}, "
            f"got {row3['v0_pnl_long']}"
        )

    def test_short_holding_pnl_inverse_follows_filtered(self, tmp_path):
        """Short: PnL = entry_capital * (2 - filtered / entry_filtered)."""
        store = _new_store(tmp_path, track=True)

        _extract(store, _make_view(t_val=0, filtered_val=100.0))

        # Enter short at filtered=100, frozen=100
        row1 = _extract(store, _make_view(t_val=1, filtered_val=100.0,
                                          trade_records=_short_entry()))
        assert row1["v0_pnl_short"] == pytest.approx(100.0)

        # Holding: filtered drops to 90 (short profits)
        row2 = _extract(store, _make_view(t_val=2, filtered_val=90.0,
                                          trade_records=_short_entry()))
        expected = 100.0 * (2.0 - 90.0 / 100.0)  # = 110.0
        assert row2["v0_pnl_short"] == pytest.approx(expected), (
            f"Short holding (filtered=90): expected {expected}, "
            f"got {row2['v0_pnl_short']}"
        )

        # Holding: filtered rises to 110 (short loses)
        row3 = _extract(store, _make_view(t_val=3, filtered_val=110.0,
                                          trade_records=_short_entry()))
        expected = 100.0 * (2.0 - 110.0 / 100.0)  # = 90.0
        assert row3["v0_pnl_short"] == pytest.approx(expected), (
            f"Short holding (filtered=110): expected {expected}, "
            f"got {row3['v0_pnl_short']}"
        )


# ===========================================================================
# Unit tests — no-position freeze
# ===========================================================================

class TestNoPositionFreeze:
    """No position: PnL stays frozen at last active value."""

    def test_no_position_pnl_remains_frozen(self, tmp_path):
        """After exit, PnL freezes at the last holding value."""
        store = _new_store(tmp_path, track=True)

        _extract(store, _make_view(t_val=0, filtered_val=100.0))
        _extract(store, _make_view(t_val=1, filtered_val=100.0,
                                   trade_records=_long_entry()))
        row2 = _extract(store, _make_view(t_val=2, filtered_val=120.0,
                                          trade_records=_long_entry()))
        last_active_pnl = row2["v0_pnl_long"]  # = 100 * 120/100 = 120

        # Exit position
        row3 = _extract(store, _make_view(t_val=3, filtered_val=90.0,
                                          long_pnl=100.0,
                                          trade_records=_long_exit()))
        assert row3["v0_pnl_long"] == pytest.approx(last_active_pnl), (
            f"After exit: PnL should freeze at {last_active_pnl}, "
            f"got {row3['v0_pnl_long']}"
        )

        # Still no position
        row4 = _extract(store, _make_view(t_val=4, filtered_val=150.0,
                                          long_pnl=80.0))
        assert row4["v0_pnl_long"] == pytest.approx(last_active_pnl), (
            f"Still no pos: PnL should stay at {last_active_pnl}, "
            f"got {row4['v0_pnl_long']}"
        )

    def test_multiple_roundtrips_freeze_correctly(self, tmp_path):
        """Multiple long -> flat -> short rounds freeze correctly each time."""
        store = _new_store(tmp_path, track=True)

        # Round 1: long
        _extract(store, _make_view(t_val=0, filtered_val=100.0))
        _extract(store, _make_view(t_val=1, filtered_val=100.0,
                                   trade_records=_long_entry()))
        row = _extract(store, _make_view(t_val=2, filtered_val=110.0,
                                         trade_records=_long_entry()))
        long_final = row["v0_pnl_long"]  # = 110.0

        row_exit = _extract(store, _make_view(t_val=3, filtered_val=115.0,
                                              trade_records=_long_exit()))
        assert row_exit["v0_pnl_long"] == pytest.approx(long_final)

        # Round 2: short
        _extract(store, _make_view(t_val=4, filtered_val=115.0,
                                   trade_records=_short_entry()))
        row = _extract(store, _make_view(t_val=5, filtered_val=100.0,
                                         trade_records=_short_entry()))
        short_final = row["v0_pnl_short"]
        expected_short = 100.0 * (2.0 - 100.0 / 115.0)
        assert short_final == pytest.approx(expected_short)

        row_exit = _extract(store, _make_view(t_val=6, filtered_val=95.0,
                                              trade_records=_short_exit()))
        assert row_exit["v0_pnl_short"] == pytest.approx(short_final)


# ===========================================================================
# Unit tests — ratio constancy
# ===========================================================================

class TestRatioConstancy:
    """During a position, the tracking formula produces constant ratios."""

    def test_long_pnl_filtered_ratio_constant(self, tmp_path):
        """Long: PnL / filtered is constant throughout a position."""
        store = _new_store(tmp_path, track=True)
        _extract(store, _make_view(t_val=0, filtered_val=100.0))
        _extract(store, _make_view(t_val=1, filtered_val=100.0,
                                   trade_records=_long_entry()))

        ratios = []
        for i, f_val in enumerate([105.0, 110.0, 95.0, 120.0, 88.0], start=2):
            row = _extract(store, _make_view(t_val=i, filtered_val=f_val,
                                             trade_records=_long_entry()))
            ratios.append(row["v0_pnl_long"] / f_val)

        assert np.allclose(ratios, ratios[0], rtol=1e-12), (
            f"Long PnL/filtered ratios should be constant: {ratios}"
        )
        assert ratios[0] == pytest.approx(1.0)  # entry_cap/entry_filt = 1

    def test_short_modified_ratio_constant(self, tmp_path):
        """Short: (2*entry_cap - PnL) / filtered is constant."""
        store = _new_store(tmp_path, track=True)
        _extract(store, _make_view(t_val=0, filtered_val=100.0))
        _extract(store, _make_view(t_val=1, filtered_val=100.0,
                                   trade_records=_short_entry()))

        entry_cap, entry_filt = 100.0, 100.0
        ratios = []
        for i, f_val in enumerate([90.0, 85.0, 105.0, 115.0, 78.0], start=2):
            row = _extract(store, _make_view(t_val=i, filtered_val=f_val,
                                             trade_records=_short_entry()))
            modified = (2.0 * entry_cap - row["v0_pnl_short"]) / f_val
            ratios.append(modified)

        assert np.allclose(ratios, ratios[0], rtol=1e-12), (
            f"Short modified ratios should be constant: {ratios}"
        )
        assert ratios[0] == pytest.approx(entry_cap / entry_filt)

    def test_different_entry_prices(self, tmp_path):
        """Ratio constancy holds with non-100 entry prices and capital."""
        store = _new_store(tmp_path, track=True)

        # Build up to a frozen value != 100 via a prior long round-trip
        _extract(store, _make_view(t_val=0, filtered_val=150.0,
                                   long_pnl=150.0))
        _extract(store, _make_view(t_val=1, filtered_val=180.0,
                                   trade_records=_long_entry()))
        _extract(store, _make_view(t_val=2, filtered_val=200.0,
                                   trade_records=_long_entry()))
        row_exit = _extract(store, _make_view(t_val=3, filtered_val=210.0,
                                              trade_records=_long_exit()))
        frozen_val = row_exit["v0_pnl_long"]  # = 150 * 200/180

        # Re-enter long at filtered=210
        _extract(store, _make_view(t_val=4, filtered_val=210.0,
                                   trade_records=_long_entry()))

        ratios = []
        for i, f_val in enumerate([220.0, 230.0, 200.0], start=5):
            row = _extract(store, _make_view(t_val=i, filtered_val=f_val,
                                             trade_records=_long_entry()))
            ratios.append(row["v0_pnl_long"] / f_val)

        assert np.allclose(ratios, ratios[0], rtol=1e-12), (
            f"Ratios should be constant: {ratios}"
        )
        assert ratios[0] == pytest.approx(frozen_val / 210.0, rel=1e-10)


# ===========================================================================
# Integration test — 8 curves (4 views x long/short) zero tracking error
# ===========================================================================

class TestAll8Curves:
    """Synthetic data: verify all 8 curves (v0-v3 x long/short) match formula."""

    @staticmethod
    def _make_multi_view(bar_idx, filtered_vals, trade_records_by_view):
        """Build multi-view stage_output for one bar."""
        views = {}
        for prefix in ["v0", "v1", "v2", "v3"]:
            f_val = filtered_vals.get(prefix, 100.0)
            trades = trade_records_by_view.get(prefix, [])
            views[f"{prefix}_test"] = _make_view(
                t_val=bar_idx,
                filtered_val=f_val,
                trade_records=trades,
            )
        return {"ohlcv": {"close": float(filtered_vals.get("v0", 100.0))},
                "views": views}

    def test_all_8_curves_zero_tracking_error(self, tmp_path):
        """4 views x (long + short) = 8 curves, every PnL matches formula.

        Positions:
        - v0 long:  bars 5-13  (entry bar 5,  entry_filtered=100.0)
        - v1 short: bars 5-13  (entry bar 5,  entry_filtered=100.0)
        - v2 long:  bars 8-13  (entry bar 8,  entry_filtered=100.8)
        - v3 short: bars 10-13 (entry bar 10, entry_filtered=99.2)

        All entries start from frozen capital = 100.0.
        """
        store = ParquetStore(
            output_dir=str(tmp_path / "store_out"),
            ticker="TEST",
            view_configs=[{}, {}, {}, {}],
            track_pnl_from_price=True,
        )
        store.start_session()

        # Phase 1: no position (bars 0-4), all filtered=100
        for bar in range(5):
            store._extract_row(bar, f"2024-01-{bar+1:02d}T00:00:00",
                               self._make_multi_view(bar,
                                   {"v0": 100.0, "v1": 100.0,
                                    "v2": 100.0, "v3": 100.0}, {}))

        # Pre-computed entry parameters.
        # bar=5->i=0, bar=8->i=3, bar=10->i=5
        frozen_cap = 100.0
        entries = {
            # key: (entry_bar, entry_filtered_at_that_bar) or None
            "v0_pnl_long":  (5, 100.0),    # f_traj["v0"][0]
            "v0_pnl_short": None,
            "v1_pnl_long":  None,
            "v1_pnl_short": (5, 100.0),    # f_traj["v1"][0]
            "v2_pnl_long":  (8, 102.4),    # f_traj["v2"][3]
            "v2_pnl_short": None,
            "v3_pnl_long":  None,
            "v3_pnl_short": (10, 96.0),    # f_traj["v3"][5]
        }

        long_rec = [{"type": "long", "entry_idx": 0, "exit_idx": None}]
        short_rec = [{"type": "short", "entry_idx": 0, "exit_idx": None}]
        long_exit = [{"type": "long", "entry_idx": 0, "exit_idx": 0,
                      "exit_reason": "signal"}]
        short_exit = [{"type": "short", "entry_idx": 0, "exit_idx": 0,
                       "exit_reason": "signal"}]

        f_traj = {
            "v0": [100.0, 101.5, 103.0, 102.0, 105.0, 107.0, 106.0,
                   108.0, 110.0, 109.0],
            "v1": [100.0, 99.0, 97.5, 98.0, 96.0, 94.5, 95.0,
                   93.0, 91.0, 92.0],
            "v2": [100.0, 100.8, 101.6, 102.4, 103.2, 104.0, 104.8,
                   105.6, 106.4, 107.2],
            "v3": [100.0, 99.2, 98.4, 97.6, 96.8, 96.0, 95.2,
                   94.4, 93.6, 92.8],
        }

        verified = 0

        for i, bar in enumerate(range(5, 15)):
            trades = {}
            if 5 <= bar < 14:
                trades["v0"] = long_rec
                trades["v1"] = short_rec
            elif bar == 14:
                trades["v0"] = long_exit
                trades["v1"] = short_exit
            if 8 <= bar < 14:
                trades["v2"] = long_rec
            elif bar == 14:
                trades["v2"] = long_exit
            if 10 <= bar < 14:
                trades["v3"] = short_rec
            elif bar == 14:
                trades["v3"] = short_exit

            fv = {k: v[i] for k, v in f_traj.items()}
            row = store._extract_row(
                bar, f"2024-01-{bar+1:02d}T00:00:00",
                self._make_multi_view(bar, fv, trades))

            # Verify each of the 8 curves
            for prefix in ["v0", "v1", "v2", "v3"]:
                for direction, is_short in [("pnl_long", False),
                                             ("pnl_short", True)]:
                    col = f"{prefix}_{direction}"
                    pos_col = (f"{prefix}_long_pos" if not is_short
                               else f"{prefix}_short_pos")
                    f_key = f"{prefix}_{direction}"
                    einfo = entries[f_key]
                    has_pos = bool(row.get(pos_col, False))

                    if not has_pos:
                        continue

                    if einfo is None:
                        pytest.fail(
                            f"Bar {bar} {col}: unexpected position "
                            f"(entry not configured for {f_key})"
                        )

                    entry_bar, entry_filt = einfo
                    if bar == entry_bar:
                        assert row[col] == pytest.approx(frozen_cap), (
                            f"Bar {bar} {col} entry: expected "
                            f"{frozen_cap}, got {row[col]}"
                        )
                    elif bar > entry_bar:
                        if is_short:
                            expected = frozen_cap * (
                                2.0 - fv[prefix] / entry_filt
                            )
                        else:
                            expected = frozen_cap * (
                                fv[prefix] / entry_filt
                            )
                        assert row[col] == pytest.approx(expected, rel=1e-10), (
                            f"Bar {bar} {col}: expected {expected}, "
                            f"got {row[col]} "
                            f"(cap={frozen_cap}, filt={fv[prefix]}, "
                            f"entry_filt={entry_filt})"
                        )
                        verified += 1

        # v0 long: bars 6-13 (8), v1 short: bars 6-13 (8)
        # v2 long: bars 9-13 (5), v3 short: bars 11-13 (3) = 24 total
        assert verified == 24, (
            f"Expected 24 holding-bar verifications, got {verified}"
        )


# ===========================================================================
# Backward compatibility
# ===========================================================================

class TestBackwardCompatibility:
    """Without track_pnl_from_price, behaviour is freeze-only (unchanged)."""

    def test_default_no_tracking(self, tmp_path):
        """Default (track_pnl_from_price=False): PnL freezes but no price track."""
        store = ParquetStore(
            output_dir=str(tmp_path / "store_out"),
            ticker="TEST",
            view_configs=[{}],
        )
        store.start_session()

        # No position
        row0 = _extract(store, _make_view(t_val=0, filtered_val=100.0,
                                          long_pnl=100.0))
        assert row0["v0_pnl_long"] == pytest.approx(100.0)

        # Enter long — strategy PnL=105, old behaviour follows strategy
        row1 = _extract(store, _make_view(t_val=1, filtered_val=105.0,
                                          long_pnl=105.0,
                                          trade_records=_long_entry()))
        assert row1["v0_pnl_long"] == pytest.approx(105.0), (
            f"Without tracking: should follow strategy PnL 105.0, "
            f"got {row1['v0_pnl_long']}"
        )

        # Holding — strategy PnL=110
        row2 = _extract(store, _make_view(t_val=2, filtered_val=110.0,
                                          long_pnl=110.0,
                                          trade_records=_long_entry()))
        assert row2["v0_pnl_long"] == pytest.approx(110.0)

        # Exit — freezes at last value from holding period
        row3 = _extract(store, _make_view(t_val=3, filtered_val=95.0,
                                          long_pnl=100.0,
                                          trade_records=_long_exit()))
        assert row3["v0_pnl_long"] == pytest.approx(110.0), (
            f"Without tracking after exit: PnL should freeze at 110.0, "
            f"got {row3['v0_pnl_long']}"
        )

    def test_explicit_false_same_as_default(self, tmp_path):
        """Explicit track_pnl_from_price=False matches default behaviour."""
        store1 = ParquetStore(
            output_dir=str(tmp_path / "store1"),
            ticker="T1", view_configs=[{}],
        )
        store2 = ParquetStore(
            output_dir=str(tmp_path / "store2"),
            ticker="T2", view_configs=[{}],
            track_pnl_from_price=False,
        )
        store1.start_session()
        store2.start_session()

        view = _make_view(t_val=1, filtered_val=105.0, long_pnl=105.0,
                          trade_records=_long_entry())
        r1 = _extract(store1, view)
        r2 = _extract(store2, view)
        assert r1["v0_pnl_long"] == pytest.approx(r2["v0_pnl_long"]), (
            f"Default vs explicit False should match: "
            f"{r1['v0_pnl_long']} vs {r2['v0_pnl_long']}"
        )

    def test_short_without_tracking(self, tmp_path):
        """Without tracking, short freeze works correctly."""
        store = ParquetStore(
            output_dir=str(tmp_path / "store_out"),
            ticker="TEST", view_configs=[{}],
        )
        store.start_session()

        _extract(store, _make_view(t_val=0, filtered_val=100.0))
        _extract(store, _make_view(t_val=1, filtered_val=100.0,
                                   short_pnl=100.0,
                                   trade_records=_short_entry()))
        row = _extract(store, _make_view(t_val=2, filtered_val=90.0,
                                         short_pnl=110.0,
                                         trade_records=_short_entry()))
        assert row["v0_pnl_short"] == pytest.approx(110.0)

        row_exit = _extract(store, _make_view(t_val=3, filtered_val=85.0,
                                              short_pnl=100.0,
                                              trade_records=_short_exit()))
        assert row_exit["v0_pnl_short"] == pytest.approx(110.0)
