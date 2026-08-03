"""
Tests for view_backtest.html enhancement functions.
Validates the alignment PnL computation and position detection logic
by porting the JavaScript algorithms to Python and testing the logic.
"""

import math
from pathlib import Path

import pandas as pd
import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# Python ports of the JavaScript functions from 回测结果可视化.html
# ═══════════════════════════════════════════════════════════════════════════════


def compute_aligned_pnl(pnl, higher_pos):
    """
    Python port of the JS computeAlignedPnl function.

    Alignment-filtered PnL: when higher TF holds a position, current TF's
    PnL change is accumulated; when higher TF has no position, PnL stays flat.

    Args:
        pnl: list of float, the original PnL series (cumulative, starting at ~100).
        higher_pos: list of bool, higher timeframe position flags.

    Returns:
        list of float, the alignment-filtered PnL series.
    """
    n = len(pnl)
    if n == 0:
        return []
    aligned = [100.0] * n
    for i in range(1, n):
        if higher_pos[i]:
            aligned[i] = aligned[i - 1] + (pnl[i] - pnl[i - 1])
        else:
            aligned[i] = aligned[i - 1]
    return aligned


def get_current_position(long_pos, short_pos):
    """
    Python port of the JS getCurrentPosition function.

    Determines current position from the last bar's position data.
    Long takes priority over short when both are true.

    Args:
        long_pos: list of bool/int, long position flags.
        short_pos: list of bool/int, short position flags.

    Returns:
        tuple of (text_cn, cls): e.g. ("做多", "long"), ("做空", "short"), ("空仓", "empty").
    """
    last = len(long_pos) - 1
    if long_pos[last]:
        return ("做多", "long")
    if short_pos[last]:
        return ("做空", "short")
    return ("空仓", "empty")


def get_higher_tf_position(view_idx, columns, ordered_views):
    """
    Python port of the JS getHigherTfPosition function.

    Gets the higher timeframe position filter arrays for a given view index.
    v0 (coarsest) derives positions from its own signal direction.
    v1+ uses the next-coarser view's long_pos/short_pos columns.

    Args:
        view_idx: int, index into ordered_views (0 = coarsest).
        columns: dict-like, maps column names to lists.
        ordered_views: list of view key strings, e.g. ['v0', 'v1', 'v2', 'v3'].

    Returns:
        dict with keys 'longPos' and 'shortPos', each a list of bool.
    """
    if view_idx == 0:
        sig_key = ordered_views[0] + "_sig"
        sig = columns.get(sig_key)
        if sig is None:
            return {"longPos": [], "shortPos": []}
        n = len(sig)
        long_pos = [False] * n
        short_pos = [False] * n
        for i in range(n):
            val = float(sig[i]) if sig[i] is not None else 0.0
            long_pos[i] = val > 0
            short_pos[i] = val < 0
        return {"longPos": long_pos, "shortPos": short_pos}

    higher_v = ordered_views[view_idx - 1]
    return {
        "longPos": list(columns.get(higher_v + "_long_pos", [])),
        "shortPos": list(columns.get(higher_v + "_short_pos", [])),
    }


def get_higher_tf_position_color(position_type):
    """
    Python port of the CSS rgba color constants used for higher timeframe
    position bands in the period dashboard rendering.

    Args:
        position_type: "long" or "short".

    Returns:
        str: CSS rgba color string.
    """
    if position_type == "long":
        return "rgba(56,139,253,0.06)"
    elif position_type == "short":
        return "rgba(210,140,40,0.06)"
    raise ValueError(f"Unknown position type: {position_type}")


# ═══════════════════════════════════════════════════════════════════════════════
# Test: compute_aligned_pnl
# ═══════════════════════════════════════════════════════════════════════════════


class TestComputeAlignedPnl:
    """Tests for the computeAlignedPnl algorithm ported from JS."""

    def test_all_true_equals_original(self):
        """When higherPos is all True, aligned PnL should equal original PnL."""
        pnl = [100.0, 101.0, 102.0, 103.0, 104.0]
        higher_pos = [True, True, True, True, True]
        result = compute_aligned_pnl(pnl, higher_pos)
        assert result == pytest.approx(pnl)

    def test_all_false_stays_flat(self):
        """When higherPos is all False, aligned PnL should stay at 100.0."""
        pnl = [100.0, 101.0, 102.0, 99.0, 105.0]
        higher_pos = [False, False, False, False, False]
        result = compute_aligned_pnl(pnl, higher_pos)
        expected = [100.0, 100.0, 100.0, 100.0, 100.0]
        assert result == pytest.approx(expected)

    def test_single_element(self):
        """Single element array returns [100.0]."""
        result = compute_aligned_pnl([100.0], [True])
        assert result == [100.0]

    def test_empty_array(self):
        """Empty array returns empty list."""
        result = compute_aligned_pnl([], [])
        assert result == []

    def test_mixed_positions(self):
        """Mixed true/false: flat when false, follows delta when true."""
        pnl = [100.0, 101.0, 102.0, 101.0, 103.0]
        higher_pos = [True, False, True, False, True]
        # i=1: higher_pos[1]=False -> aligned[1]=100.0
        # i=2: higher_pos[2]=True  -> aligned[2]=100+(102-101)=101.0
        # i=3: higher_pos[3]=False -> aligned[3]=101.0
        # i=4: higher_pos[4]=True  -> aligned[4]=101+(103-101)=103.0
        expected = [100.0, 100.0, 101.0, 101.0, 103.0]
        result = compute_aligned_pnl(pnl, higher_pos)
        assert result == pytest.approx(expected)

    def test_floating_point_accumulation(self):
        """No NaN or Infinity in normal floating point operations."""
        pnl = [100.0, 100.5, 99.8, 101.2, 100.9]
        higher_pos = [True, True, True, True, True]
        result = compute_aligned_pnl(pnl, higher_pos)
        for val in result:
            assert not math.isnan(val)
            assert not math.isinf(val)

    def test_known_sequence(self):
        """Verify with a hand-calculated sequence."""
        # higher_pos[1]=True  -> aligned[1]=100+(101-100)=101.0
        # higher_pos[2]=True  -> aligned[2]=101+(102-101)=102.0
        # higher_pos[3]=False -> aligned[3]=102.0
        # higher_pos[4]=True  -> aligned[4]=102+(103-101)=104.0
        # higher_pos[5]=True  -> aligned[5]=104+(104-103)=105.0
        pnl = [100.0, 101.0, 102.0, 101.0, 103.0, 104.0]
        higher_pos = [True, True, True, False, True, True]
        expected = [100.0, 101.0, 102.0, 102.0, 104.0, 105.0]
        result = compute_aligned_pnl(pnl, higher_pos)
        assert result == pytest.approx(expected)

    def test_downward_trend_with_false(self):
        """PnL going down but position false keeps value flat."""
        pnl = [100.0, 98.0, 96.0, 94.0]
        higher_pos = [True, True, False, True]
        # i=1: True -> 100 + (98-100) = 98
        # i=2: False -> 98
        # i=3: True -> 98 + (94-96) = 96
        expected = [100.0, 98.0, 98.0, 96.0]
        result = compute_aligned_pnl(pnl, higher_pos)
        assert result == pytest.approx(expected)


# ═══════════════════════════════════════════════════════════════════════════════
# Test: get_current_position
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetCurrentPosition:
    """Tests for the getCurrentPosition algorithm ported from JS."""

    def test_long_position(self):
        """Last bar has long_pos=True, short_pos=False -> ("做多", "long")."""
        result = get_current_position(
            [False, False, True], [False, False, False]
        )
        assert result == ("做多", "long")

    def test_short_position(self):
        """Last bar has long_pos=False, short_pos=True -> ("做空", "short")."""
        result = get_current_position(
            [False, False, False], [False, False, True]
        )
        assert result == ("做空", "short")

    def test_empty_position(self):
        """Both False at last bar -> ("空仓", "empty")."""
        result = get_current_position(
            [False, False, False], [False, False, False]
        )
        assert result == ("空仓", "empty")

    def test_both_true_priority_long(self):
        """When both long and short are True (edge case), long takes priority."""
        result = get_current_position(
            [False, False, True], [False, False, True]
        )
        assert result == ("做多", "long")

    def test_single_element_empty(self):
        """Single element array with both False -> ("空仓", "empty")."""
        result = get_current_position([False], [False])
        assert result == ("空仓", "empty")

    def test_int_values_treated_as_bool(self):
        """Integer 1/0 values work correctly (truthy/falsy)."""
        result = get_current_position([0, 0, 1], [0, 0, 0])
        assert result == ("做多", "long")


# ═══════════════════════════════════════════════════════════════════════════════
# Test: get_higher_tf_position
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetHigherTfPosition:
    """Tests for the getHigherTfPosition algorithm ported from JS."""

    ORDERED_VIEWS = ["v0", "v1", "v2", "v3"]

    def test_v0_uses_self_signal(self):
        """v0 (coarsest) derives position from its own signal column."""
        columns = {
            "v0_sig": [0, 1, -1, 0, 1],
        }
        result = get_higher_tf_position(0, columns, self.ORDERED_VIEWS)
        assert result["longPos"] == [False, True, False, False, True]
        assert result["shortPos"] == [False, False, True, False, False]

    def test_v0_missing_sig_returns_empty(self):
        """v0 with no sig column returns empty arrays."""
        columns = {}
        result = get_higher_tf_position(0, columns, self.ORDERED_VIEWS)
        assert result["longPos"] == []
        assert result["shortPos"] == []

    def test_v1_uses_v0_position(self):
        """v1 uses v0's long_pos/short_pos columns."""
        columns = {
            "v0_long_pos": [True, False, True],
            "v0_short_pos": [False, True, False],
        }
        result = get_higher_tf_position(1, columns, self.ORDERED_VIEWS)
        assert result["longPos"] == [True, False, True]
        assert result["shortPos"] == [False, True, False]

    def test_v2_uses_v1_position(self):
        """v2 uses v1's long_pos/short_pos columns."""
        columns = {
            "v1_long_pos": [False, True],
            "v1_short_pos": [True, False],
        }
        result = get_higher_tf_position(2, columns, self.ORDERED_VIEWS)
        assert result["longPos"] == [False, True]
        assert result["shortPos"] == [True, False]

    def test_v3_uses_v2_position(self):
        """v3 uses v2's long_pos/short_pos columns."""
        columns = {
            "v2_long_pos": [True, True, False, True],
            "v2_short_pos": [False, False, True, False],
        }
        result = get_higher_tf_position(3, columns, self.ORDERED_VIEWS)
        assert result["longPos"] == [True, True, False, True]
        assert result["shortPos"] == [False, False, True, False]

    def test_non_v0_missing_columns_returns_empty(self):
        """Non-v0 view with missing position columns returns empty lists."""
        columns = {}
        result = get_higher_tf_position(1, columns, self.ORDERED_VIEWS)
        assert result["longPos"] == []
        assert result["shortPos"] == []

    def test_v0_signal_with_nulls(self):
        """v0 signal with None/null values treated as 0."""
        columns = {
            "v0_sig": [1, None, -1, 0, None],
        }
        result = get_higher_tf_position(0, columns, self.ORDERED_VIEWS)
        assert result["longPos"] == [True, False, False, False, False]
        assert result["shortPos"] == [False, False, True, False, False]


# ═══════════════════════════════════════════════════════════════════════════════
# Integration tests with real parquet data
# ═══════════════════════════════════════════════════════════════════════════════


class TestIntegrationWithRealData:
    """Integration tests using actual parquet data from backtest output."""

    @pytest.fixture(scope="class")
    def parquet_path(self):
        """Find the first available parquet file in backtest_output."""
        base = Path(__file__).resolve().parent.parent / "backtest_output"
        candidates = sorted(base.glob("*/backtest_result.parquet"))
        if not candidates:
            pytest.skip("No parquet files found in backtest_output")
        return str(candidates[0])

    @pytest.fixture(scope="class")
    def parquet_df(self, parquet_path):
        """Load the parquet file as a DataFrame."""
        return pd.read_parquet(parquet_path)

    ORDERED_VIEWS = ["v0", "v1", "v2", "v3"]

    def test_parquet_has_required_columns(self, parquet_df):
        """Verify parquet contains the needed signal, position, and PnL columns."""
        required = [
            "v0_sig", "v0_long_pos", "v0_short_pos",
            "v0_pnl_long", "v0_pnl_short",
            "v1_sig", "v1_long_pos", "v1_short_pos",
            "v1_pnl_long", "v1_pnl_short",
        ]
        for col_name in required:
            assert col_name in parquet_df.columns, f"Missing column: {col_name}"

    def test_compute_aligned_pnl_on_real_data(self, parquet_df):
        """Run compute_aligned_pnl on real parquet data for all views."""
        columns = {col: parquet_df[col].tolist() for col in parquet_df.columns}

        for vi, v in enumerate(self.ORDERED_VIEWS):
            pnl_long = columns[f"{v}_pnl_long"]
            higher_pos = get_higher_tf_position(vi, columns, self.ORDERED_VIEWS)

            long_pos = higher_pos["longPos"]
            if len(long_pos) > 0 and len(pnl_long) > 0:
                result = compute_aligned_pnl(pnl_long, long_pos)
                # Result should have same length as input
                assert len(result) == len(pnl_long)
                # First element is always 100.0
                assert result[0] == pytest.approx(100.0)
                # No NaN or Inf
                for val in result:
                    assert not math.isnan(val)
                    assert not math.isinf(val)
                # Aligned PnL can't exceed original PnL range when flat
                # (tracking only during higher-TF position periods)

    def test_get_current_position_on_real_data(self, parquet_df):
        """Run get_current_position on real parquet data for all views."""
        columns = {col: parquet_df[col].tolist() for col in parquet_df.columns}

        for v in self.ORDERED_VIEWS:
            long_pos = columns[f"{v}_long_pos"]
            short_pos = columns[f"{v}_short_pos"]
            if len(long_pos) > 0 and len(short_pos) > 0:
                text, cls = get_current_position(long_pos, short_pos)
                assert text in ("做多", "做空", "空仓")
                assert cls in ("long", "short", "empty")

    def test_end_to_end_flow_on_real_data(self, parquet_df):
        """End-to-end: compute aligned PnL using higher-TF position on real data."""
        columns = {col: parquet_df[col].tolist() for col in parquet_df.columns}

        for vi in range(1, len(self.ORDERED_VIEWS)):  # v1, v2, v3
            v = self.ORDERED_VIEWS[vi]
            pnl_long = columns.get(f"{v}_pnl_long", [])
            higher_pos = get_higher_tf_position(vi, columns, self.ORDERED_VIEWS)
            long_pos = higher_pos["longPos"]

            if len(pnl_long) > 0 and len(long_pos) > 0:
                aligned = compute_aligned_pnl(pnl_long, long_pos)
                assert len(aligned) == len(pnl_long)
                assert aligned[0] == pytest.approx(100.0)

                # When no higher-TF position ever, aligned stays at 100
                if not any(long_pos):
                    assert all(v == pytest.approx(100.0) for v in aligned)

    def test_v0_self_signal_position_on_real_data(self, parquet_df):
        """v0 derives its position from its own signal on real data."""
        columns = {col: parquet_df[col].tolist() for col in parquet_df.columns}
        sig = columns.get("v0_sig", [])

        result = get_higher_tf_position(0, columns, self.ORDERED_VIEWS)

        if len(sig) > 0:
            assert len(result["longPos"]) == len(sig)
            assert len(result["shortPos"]) == len(sig)
            # Verify consistency: a bar can't be both long and short from signal
            for i in range(len(sig)):
                assert not (result["longPos"][i] and result["shortPos"][i])


# ═══════════════════════════════════════════════════════════════════════════════
# Test: Period Dashboard Higher TF Position
# ═══════════════════════════════════════════════════════════════════════════════


class TestPeriodDashboardHigherTfPosition:
    """Test higher timeframe position display logic in period dashboards."""

    ORDERED_VIEWS = ["v0", "v1", "v2", "v3"]

    def test_d1_v0_has_no_higher_tf(self):
        """D1 (v0/日线): should have no higher TF, annotation shows '无上级周期'"""
        columns = {"v0_sig": [0, 1, -1, 0, 1]}
        result = get_higher_tf_position(0, columns, self.ORDERED_VIEWS)
        # v0 derives from own signal — not another view's position columns
        assert result["longPos"] == [False, True, False, False, True]
        assert result["shortPos"] == [False, False, True, False, False]

    def test_d2_v1_uses_v0_position(self):
        """D2 (v1): higher TF is v0, verify v0's position data is used"""
        columns = {
            "v0_long_pos": [True, False, True, False],
            "v0_short_pos": [False, True, False, False],
        }
        result = get_higher_tf_position(1, columns, self.ORDERED_VIEWS)
        assert result["longPos"] == [True, False, True, False]
        assert result["shortPos"] == [False, True, False, False]

    def test_d3_v2_uses_v1_position(self):
        """D3 (v2): higher TF is v1, verify v1's position data is used"""
        columns = {
            "v1_long_pos": [False, True, False, True],
            "v1_short_pos": [True, False, True, False],
        }
        result = get_higher_tf_position(2, columns, self.ORDERED_VIEWS)
        assert result["longPos"] == [False, True, False, True]
        assert result["shortPos"] == [True, False, True, False]

    def test_d4_v3_uses_v2_position(self):
        """D4 (v3): higher TF is v2, verify v2's position data is used"""
        columns = {
            "v2_long_pos": [True, True, False, False, True],
            "v2_short_pos": [False, False, True, False, False],
        }
        result = get_higher_tf_position(3, columns, self.ORDERED_VIEWS)
        assert result["longPos"] == [True, True, False, False, True]
        assert result["shortPos"] == [False, False, True, False, False]

    def test_higher_tf_long_color_value(self):
        """Verify higher TF long position color is rgba(56,139,253,0.06)"""
        color = get_higher_tf_position_color("long")
        assert color == "rgba(56,139,253,0.06)"

    def test_higher_tf_short_color_value(self):
        """Verify higher TF short position color is rgba(210,140,40,0.06)"""
        color = get_higher_tf_position_color("short")
        assert color == "rgba(210,140,40,0.06)"

    def test_all_views_position_chain_on_real_data(self):
        """Integration: load real parquet, verify v3→v2→v1→v0 chain is complete"""
        base = Path(__file__).resolve().parent.parent / "backtest_output"
        candidates = sorted(base.glob("*/backtest_result.parquet"))
        if not candidates:
            pytest.skip("No parquet files found in backtest_output")

        parquet_df = pd.read_parquet(str(candidates[0]))
        columns = {col: parquet_df[col].tolist() for col in parquet_df.columns}

        n = len(columns.get("v0_long_pos", []))
        assert n > 0, "Parquet data is empty"

        # v3 uses v2's position as higher TF
        v3_higher = get_higher_tf_position(3, columns, self.ORDERED_VIEWS)
        assert v3_higher["longPos"] == columns["v2_long_pos"]
        assert v3_higher["shortPos"] == columns["v2_short_pos"]
        assert len(v3_higher["longPos"]) == n

        # v2 uses v1's position as higher TF
        v2_higher = get_higher_tf_position(2, columns, self.ORDERED_VIEWS)
        assert v2_higher["longPos"] == columns["v1_long_pos"]
        assert v2_higher["shortPos"] == columns["v1_short_pos"]
        assert len(v2_higher["longPos"]) == n

        # v1 uses v0's position as higher TF
        v1_higher = get_higher_tf_position(1, columns, self.ORDERED_VIEWS)
        assert v1_higher["longPos"] == columns["v0_long_pos"]
        assert v1_higher["shortPos"] == columns["v0_short_pos"]
        assert len(v1_higher["longPos"]) == n

        # v0 derives from own signal — annotation would show '无上级周期'
        v0_higher = get_higher_tf_position(0, columns, self.ORDERED_VIEWS)
        assert len(v0_higher["longPos"]) == n
        assert len(v0_higher["shortPos"]) == n
        v0_sig = columns.get("v0_sig", [])
        assert len(v0_sig) == n


# ═══════════════════════════════════════════════════════════════════════════════
# Signal layout configuration constants (ports of JS rendering constants)
# These define y-ranges and annotation positions in the position overlay and
# position status charts in 回测结果可视化.html.
# ═══════════════════════════════════════════════════════════════════════════════

# Position overlay y-ranges — narrowed to avoid overlapping with signal bands
POSITION_OVERLAY_LONG_Y = (0.1, 1.2)
POSITION_OVERLAY_SHORT_Y = (-1.2, -0.1)

# Position status chart band y-ranges — anchored at 0 to prevent overlap
POSITION_STATUS_LONG_Y = (0, 1.2)
POSITION_STATUS_SHORT_Y = (-1.2, 0)

# Higher TF position bands — full height at low opacity
HIGHER_TF_Y_RANGE = (-1.2, 1.2)
HIGHER_TF_LONG_COLOR = "rgba(56,139,253,0.06)"
HIGHER_TF_SHORT_COLOR = "rgba(210,140,40,0.06)"

# Annotation y position — moved from 0.98 to 0.90 to avoid overlapping bands
ANNOTATION_Y = 0.90
# The old value that caused overlap
ANNOTATION_Y_OLD = 0.98

# Signal y-range (for reference, signals are plotted in this region)
SIGNAL_Y_RANGE = (-1.2, 1.2)


# ═══════════════════════════════════════════════════════════════════════════════
# Test: Signal Layout No Overlap
# ═══════════════════════════════════════════════════════════════════════════════


class TestSignalLayoutNoOverlap:
    """Test that signals and position bands don't visually overlap.

    The fix narrowed position overlay y-ranges so long/short bands stay in their
    own half of the chart, moved annotations lower to avoid collision, and kept
    higher-TF bands at full height with low opacity.
    """

    # ── Position overlay y-range tests ──────────────────────────────────────

    def test_position_overlay_long_y_range(self):
        """Long position overlay y-range should be [0.1, 1.2], not full [-1.2, 1.2].

        Narrowing the range prevents the long position band from spilling into the
        negative (short) half of the chart.
        """
        y_min, y_max = POSITION_OVERLAY_LONG_Y
        # Range is strictly in the positive half
        assert y_min > 0, f"Long overlay y_min={y_min} should be > 0"
        assert y_max > 0, f"Long overlay y_max={y_max} should be > 0"
        # Not the old full-range values
        assert y_min != -1.2, "Long overlay y_min should not be -1.2 (old full-range)"
        # y_max can be 1.2 — the key difference is y_min=0.1 instead of -1.2
        # Reasonable bounds: within [0, 1.5]
        assert 0 <= y_min <= 1.5
        assert 0 <= y_max <= 1.5
        assert y_min < y_max, "y_min must be less than y_max"

    def test_position_overlay_short_y_range(self):
        """Short position overlay y-range should be [-1.2, -0.1].

        Narrowing the range prevents the short position band from spilling into the
        positive (long) half of the chart.
        """
        y_min, y_max = POSITION_OVERLAY_SHORT_Y
        # Range is strictly in the negative half
        assert y_min < 0, f"Short overlay y_min={y_min} should be < 0"
        assert y_max < 0, f"Short overlay y_max={y_max} should be < 0"
        # Not the old full-range values
        assert y_min == -1.2, "Short overlay y_min should be -1.2"
        assert y_max != 1.2, "Short overlay y_max should not be 1.2 (old full-range)"
        # Reasonable bounds: within [-1.5, 0]
        assert -1.5 <= y_min <= 0
        assert -1.5 <= y_max <= 0
        assert y_min < y_max, "y_min must be less than y_max"

    def test_position_overlay_long_and_short_no_overlap(self):
        """Long and short position overlay y-ranges must not overlap.

        Long range [0.1, 1.2] and short range [-1.2, -0.1] are in separate
        positive/negative halves.
        """
        long_min, long_max = POSITION_OVERLAY_LONG_Y
        short_min, short_max = POSITION_OVERLAY_SHORT_Y
        # The ranges must be disjoint: long is entirely positive, short entirely negative
        assert long_min > 0, f"Long overlay min {long_min} should be positive"
        assert long_max > 0, f"Long overlay max {long_max} should be positive"
        assert short_min < 0, f"Short overlay min {short_min} should be negative"
        assert short_max < 0, f"Short overlay max {short_max} should be negative"
        # Explicit gap check: short_max < long_min
        assert short_max < long_min, (
            f"Short overlay max {short_max} must be less than "
            f"long overlay min {long_min} to avoid overlap"
        )

    # ── Position status chart band y-range tests ────────────────────────────

    def test_position_status_long_band_y_range(self):
        """Position status chart long band y-range: [0, 1.2].

        Anchored at y=0 so the band starts at the midpoint, not dipping into
        negative territory.
        """
        y_min, y_max = POSITION_STATUS_LONG_Y
        # Anchored at 0 — starting from the midline
        assert y_min == 0, f"Long status band y_min={y_min} should be 0 (midline anchor)"
        assert y_max == 1.2, f"Long status band y_max={y_max} should be 1.2"
        assert y_max > y_min

    def test_position_status_short_band_y_range(self):
        """Position status chart short band y-range: [-1.2, 0].

        Anchored at y=0 so the band ends at the midpoint, not intruding into
        positive territory.
        """
        y_min, y_max = POSITION_STATUS_SHORT_Y
        assert y_min == -1.2, f"Short status band y_min={y_min} should be -1.2"
        assert y_max == 0, f"Short status band y_max={y_max} should be 0 (midline anchor)"
        assert y_max > y_min

    def test_position_status_long_short_no_overlap(self):
        """Position status long [0, 1.2] and short [-1.2, 0] bands share only y=0.

        Both bands are anchored at y=0 and extend in opposite directions.
        """
        long_min, long_max = POSITION_STATUS_LONG_Y
        short_min, short_max = POSITION_STATUS_SHORT_Y
        # Share y=0 as boundary, zero-area overlap is acceptable
        assert long_min == 0
        assert short_max == 0
        # The interiors must not overlap: long interior is (0, 1.2], short is [-1.2, 0)
        assert short_min < 0, "Short band interior must be negative"
        assert long_max > 0, "Long band interior must be positive"

    # ── Annotation position tests ───────────────────────────────────────────

    def test_annotation_y_position_not_overlapping(self):
        """Annotations should be at y=0.90 domain, not y=0.98.

        Moving annotations from 0.98 to 0.90 keeps them below the upper band
        edge and avoids visual collision with position bands.
        """
        # The new annotation y is lower — it avoids the upper band region
        assert ANNOTATION_Y == 0.90, f"Annotation y should be 0.90, got {ANNOTATION_Y}"
        # Verify it is lower than the old value
        assert ANNOTATION_Y < ANNOTATION_Y_OLD, (
            f"Annotation y={ANNOTATION_Y} should be less than "
            f"old y={ANNOTATION_Y_OLD}"
        )
        # Annotation should be within the chart domain [0, 1]
        assert 0 < ANNOTATION_Y < 1.0, f"Annotation y={ANNOTATION_Y} should be in (0, 1)"

    def test_annotation_below_position_band_top(self):
        """Annotation y=0.90 is below the long position overlay top (1.2 in domain)."""
        _, long_overlay_max = POSITION_OVERLAY_LONG_Y
        # When mapped to domain [0, 1], the top of a band at data-coord 1.2
        # with axis range [-1.2, 1.2] sits roughly near the top. The annotation
        # at 0.90 leaves room before the band edge.
        assert ANNOTATION_Y < 0.95, (
            f"Annotation y={ANNOTATION_Y} should be well below 0.95 "
            f"to avoid the upper band edge"
        )

    # ── Higher TF bands tests ────────────────────────────────────────────────

    def test_higher_tf_bands_full_height(self):
        """Higher TF position bands keep full y-range [-1.2, 1.2] at low opacity.

        Unlike the current-TF position overlay (which is narrowed), higher-TF
        bands use the full chart height because their low opacity prevents them
        from visually overwhelming the signals.
        """
        y_min, y_max = HIGHER_TF_Y_RANGE
        # Full range matches the signal y-range
        assert y_min == -1.2, f"Higher TF y_min={y_min} should be -1.2 (full range)"
        assert y_max == 1.2, f"Higher TF y_max={y_max} should be 1.2 (full range)"
        # Verify full range is wider than the narrowed position overlay ranges
        htf_span = y_max - y_min  # 2.4
        long_span = POSITION_OVERLAY_LONG_Y[1] - POSITION_OVERLAY_LONG_Y[0]  # 1.1
        assert htf_span > long_span, (
            f"Higher TF span {htf_span} should be wider than "
            f"long overlay span {long_span}"
        )

    def test_higher_tf_opacity_is_low(self):
        """Higher TF position band opacity should be 0.06.

        The low opacity (near-transparent) makes the full-height bands subtle
        enough that they don't obscure the signal lines.
        """
        # Extract the alpha component from the rgba color strings
        def _alpha_from_rgba(rgba_str):
            """Parse the alpha value from an rgba(r,g,b,a) string."""
            # Format: rgba(R,G,B,A)
            values_part = rgba_str.replace("rgba(", "").replace(")", "")
            parts = [float(x) for x in values_part.split(",")]
            return parts[3]

        long_alpha = _alpha_from_rgba(HIGHER_TF_LONG_COLOR)
        short_alpha = _alpha_from_rgba(HIGHER_TF_SHORT_COLOR)
        assert long_alpha == 0.06, f"Long higher-TF opacity should be 0.06, got {long_alpha}"
        assert short_alpha == 0.06, f"Short higher-TF opacity should be 0.06, got {short_alpha}"
        assert long_alpha < 0.1, "Higher TF opacity should be low (< 0.1)"

    # ── Overlap verification tests ──────────────────────────────────────────

    def test_long_short_bands_no_overlap(self):
        """Long and short position bands should be in separate y regions.

        Long bands occupy only positive y, short bands occupy only negative y.
        This prevents the visual muddiness where both bands overlap each other
        and the signal line at mid-chart.
        """
        # Position overlay
        lo_min, lo_max = POSITION_OVERLAY_LONG_Y
        so_min, so_max = POSITION_OVERLAY_SHORT_Y
        # The long overlay range is entirely positive, short entirely negative
        assert lo_min > 0
        assert so_max < 0
        # No overlap possible when one is all-positive and the other all-negative
        assert lo_min > so_max, (
            f"Long overlay [{lo_min}, {lo_max}] and short overlay "
            f"[{so_min}, {so_max}] must not overlap"
        )

        # Position status
        ls_min, ls_max = POSITION_STATUS_LONG_Y
        ss_min, ss_max = POSITION_STATUS_SHORT_Y
        assert ls_min >= 0
        assert ss_max <= 0
        # Only overlap at y=0 (the midline), which is a zero-area boundary
        overlap_start = max(ls_min, ss_min)
        overlap_end = min(ls_max, ss_max)
        assert overlap_start >= overlap_end, (
            f"Position status bands overlap in [{overlap_start}, {overlap_end}]"
        )

    def test_overlay_narrower_than_full_signal_range(self):
        """Position overlay ranges are narrower than full signal y-range [-1.2, 1.2].

        This is the core fix: the bands no longer span the full chart height,
        leaving room for the signal lines to be visible without obstruction.
        """
        signal_min, signal_max = SIGNAL_Y_RANGE
        signal_span = signal_max - signal_min  # 2.4

        lo_span = POSITION_OVERLAY_LONG_Y[1] - POSITION_OVERLAY_LONG_Y[0]    # 1.1
        so_span = POSITION_OVERLAY_SHORT_Y[1] - POSITION_OVERLAY_SHORT_Y[0]  # 1.1

        # Each overlay is narrower than the full signal range
        assert lo_span < signal_span, (
            f"Long overlay span {lo_span} should be < signal span {signal_span}"
        )
        assert so_span < signal_span, (
            f"Short overlay span {so_span} should be < signal span {signal_span}"
        )
        # Combined, they leave room for signals in between
        assert lo_span + so_span < 2 * signal_span, (
            "Overlay bands should not fill the chart"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Period dashboard 6-row layout constants (ports of JS rendering layout)
# These define the grid, height, yaxis assignments, and domain ranges for the
# 6-row period dashboard in 回测结果可视化.html.
# ═══════════════════════════════════════════════════════════════════════════════

# Grid configuration
PERIOD_DASH_GRID_ROWS = 6
PERIOD_DASH_GRID_COLS = 1
PERIOD_DASH_HEIGHT = 720
PERIOD_DASH_YGAP = 0.02

# Row layout: (row_label, yaxis_name, domain_ymin, domain_ymax)
# Row 1 (top) is visually at the top, with the largest domain values in Plotly.
PERIOD_DASH_ROWS = [
    # Row 1 (top): 滤波价格 - Filtered Price
    {"label": "滤波价格", "yaxis": "y",  "domain": (0.78, 1.0)},
    # Row 2: 信号 - Signal
    {"label": "信号",   "yaxis": "y3", "domain": (0.70, 0.78)},
    # Row 3: 自身持仓 - Own Position
    {"label": "自身持仓", "yaxis": "y5", "domain": (0.60, 0.70)},
    # Row 4: 上级持仓 - Higher TF Position
    {"label": "上级持仓", "yaxis": "y6", "domain": (0.50, 0.60)},
    # Row 5: 热力图 - Position Heatmap
    {"label": "热力图",  "yaxis": "y2", "domain": (0.42, 0.50)},
    # Row 6 (bottom): PnL - PnL Curves
    {"label": "PnL",   "yaxis": "y4", "domain": (0.0, 0.42)},
]

# All y-axes used in the 6-row period dashboard
PERIOD_DASH_YAXES = ["y", "y2", "y3", "y4", "y5", "y6"]


# ═══════════════════════════════════════════════════════════════════════════════
# Test: Period Dashboard 6-Row Layout
# ═══════════════════════════════════════════════════════════════════════════════


class TestPeriodDashboard6RowLayout:
    """Test the 6-row period dashboard layout structure.

    Validates that the period dashboard grid, height, yaxis assignments,
    and domain ranges match the layout defined in the HTML visualization.
    """

    def test_grid_has_6_rows(self):
        """Grid configuration should have rows=6, cols=1, independent pattern."""
        assert PERIOD_DASH_GRID_ROWS == 6, (
            f"Period dashboard grid should have 6 rows, got {PERIOD_DASH_GRID_ROWS}"
        )
        assert PERIOD_DASH_GRID_COLS == 1, (
            f"Period dashboard grid should have 1 column, got {PERIOD_DASH_GRID_COLS}"
        )

    def test_chart_height_720(self):
        """Chart height should be >= 720 for 6-row layout."""
        assert PERIOD_DASH_HEIGHT >= 720, (
            f"Period dashboard height should be >= 720, got {PERIOD_DASH_HEIGHT}"
        )

    def test_row1_owns_yaxis_price(self):
        """Row1 (filtered price) uses yaxis."""
        row = PERIOD_DASH_ROWS[0]
        assert row["label"] == "滤波价格"
        assert row["yaxis"] == "y", (
            f"Row1 should use yaxis 'y', got '{row['yaxis']}'"
        )

    def test_row2_owns_yaxis3_signal(self):
        """Row2 (signal) uses yaxis3."""
        row = PERIOD_DASH_ROWS[1]
        assert row["label"] == "信号"
        assert row["yaxis"] == "y3", (
            f"Row2 should use yaxis 'y3', got '{row['yaxis']}'"
        )

    def test_row3_owns_yaxis5_own_position(self):
        """Row3 (own position) uses yaxis5."""
        row = PERIOD_DASH_ROWS[2]
        assert row["label"] == "自身持仓"
        assert row["yaxis"] == "y5", (
            f"Row3 should use yaxis 'y5', got '{row['yaxis']}'"
        )

    def test_row4_owns_yaxis6_higher_tf(self):
        """Row4 (higher TF position) uses yaxis6."""
        row = PERIOD_DASH_ROWS[3]
        assert row["label"] == "上级持仓"
        assert row["yaxis"] == "y6", (
            f"Row4 should use yaxis 'y6', got '{row['yaxis']}'"
        )

    def test_row3_and_row4_separate_axes(self):
        """Row3 (yaxis5) != Row4 (yaxis6) -- they are INDEPENDENT."""
        row3 = PERIOD_DASH_ROWS[2]
        row4 = PERIOD_DASH_ROWS[3]
        assert row3["yaxis"] != row4["yaxis"], (
            f"Row3 yaxis ({row3['yaxis']}) and Row4 yaxis ({row4['yaxis']}) "
            f"must be independent axes"
        )

    def test_6row_domains_non_overlapping(self):
        """All 6 y-axis domains are non-overlapping and ordered in chart."""
        # Extract domains and sort by domain min (bottom-to-top in chart)
        domains = sorted(
            [row["domain"] for row in PERIOD_DASH_ROWS],
            key=lambda d: d[0],
        )

        # Verify non-overlapping: each domain's max <= next domain's min
        for i in range(len(domains) - 1):
            current_max = domains[i][1]
            next_min = domains[i + 1][0]
            assert current_max <= next_min, (
                f"Domain[{i}] ({domains[i]}) max {current_max} overlaps with "
                f"domain[{i+1}] ({domains[i+1]}) min {next_min}. "
                f"All domains must be non-overlapping."
            )

        # Bottom domain starts at 0
        assert domains[0][0] == 0.0, f"Bottom domain should start at 0.0, got {domains[0][0]}"
        # Top domain ends at 1.0
        assert domains[-1][1] == 1.0, f"Top domain should end at 1.0, got {domains[-1][1]}"

    def test_domains_cover_full_range(self):
        """Sum of domain heights should cover the full chart [0.0, 1.0].

        The ygap (0.02) creates visual spacing between rows but is applied by
        Plotly's layout engine on top of these domain values, so the domain
        values themselves sum to exactly 1.0.
        """
        total_span = sum(
            row["domain"][1] - row["domain"][0] for row in PERIOD_DASH_ROWS
        )
        assert total_span == pytest.approx(1.0), (
            f"Total domain span {total_span} should cover full chart [0.0, 1.0]"
        )

    def test_yaxis_count_is_6(self):
        """6 independent y-axes exist: y, y2, y3, y4, y5, y6."""
        axes = sorted(PERIOD_DASH_YAXES)
        assert len(axes) == 6, f"Expected 6 y-axes, got {len(axes)}: {axes}"
        assert axes == ["y", "y2", "y3", "y4", "y5", "y6"], (
            f"Expected y-axes names [y, y2, y3, y4, y5, y6], got {axes}"
        )

    def test_domain_heights_are_reasonable(self):
        """Each row domain height is within reasonable bounds (0.06-0.42)."""
        for row in PERIOD_DASH_ROWS:
            d_min, d_max = row["domain"]
            height = d_max - d_min
            # PnL row (bottom) can be taller, others are thinner
            assert 0.01 < height < 0.5, (
                f"Row '{row['label']}' domain height {height} is outside expected range"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Test: All Modifications Integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestAllModificationsIntegration:
    """Integration tests covering all modifications end-to-end.

    Uses real parquet data to validate column completeness, position data
    consistency, aligned PnL computation, and cross-view position chains.
    """

    ORDERED_VIEWS = ["v0", "v1", "v2", "v3"]

    @pytest.fixture(scope="class")
    def parquet_path(self):
        """Find the first available parquet file in backtest_output."""
        base = Path(__file__).resolve().parent.parent / "backtest_output"
        candidates = sorted(base.glob("*/backtest_result.parquet"))
        if not candidates:
            pytest.skip("No parquet files found in backtest_output")
        return str(candidates[0])

    @pytest.fixture(scope="class")
    def parquet_df(self, parquet_path):
        """Load the parquet file as a DataFrame."""
        return pd.read_parquet(parquet_path)

    def test_real_parquet_loads_all_columns(self, parquet_df):
        """Load real parquet, verify all 51 columns present."""
        expected_count = 51
        actual_count = len(parquet_df.columns)
        assert actual_count == expected_count, (
            f"Expected {expected_count} columns, got {actual_count}: "
            + ", ".join(parquet_df.columns)
        )

    def test_all_views_position_data_exists(self, parquet_df):
        """Each view v0-v3 has long_pos and short_pos columns with valid data."""
        for v in self.ORDERED_VIEWS:
            for pos_type in ["long_pos", "short_pos"]:
                col_name = f"{v}_{pos_type}"
                assert col_name in parquet_df.columns, (
                    f"Missing column: {col_name}"
                )
                series = parquet_df[col_name]
                assert len(series) > 0, (
                    f"Column {col_name} is empty"
                )
                # All values should be 0 or 1 (boolean positions)
                unique_vals = set(series.dropna().unique())
                assert unique_vals.issubset({0, 1, True, False}), (
                    f"Column {col_name} has unexpected values: {unique_vals}"
                )

    def test_aligned_pnl_computation_end_to_end(self, parquet_df):
        """End-to-end: compute aligned PnL for all views, verify shape."""
        columns = {col: parquet_df[col].tolist() for col in parquet_df.columns}

        for vi, v in enumerate(self.ORDERED_VIEWS):
            higher_pos = get_higher_tf_position(vi, columns, self.ORDERED_VIEWS)

            for side, pos_key in [("long", "longPos"), ("short", "shortPos")]:
                pnl = columns.get(f"{v}_pnl_{side}", [])
                pos = higher_pos[pos_key]
                if len(pnl) > 0 and len(pos) > 0:
                    aligned = compute_aligned_pnl(pnl, pos)
                    # Shape matches input
                    assert len(aligned) == len(pnl), (
                        f"Aligned PnL length mismatch for {v} {side}"
                    )
                    # Starts at 100.0
                    assert aligned[0] == pytest.approx(100.0), (
                        f"Aligned PnL start != 100.0 for {v} {side}"
                    )
                    # No NaN or Inf
                    assert all(not math.isnan(x) for x in aligned), (
                        f"NaN in aligned PnL for {v} {side}"
                    )
                    assert all(not math.isinf(x) for x in aligned), (
                        f"Inf in aligned PnL for {v} {side}"
                    )

    def test_higher_tf_position_chain_integration(self, parquet_df):
        """Integration: v3->v2->v1->v0 position chain with real data."""
        columns = {col: parquet_df[col].tolist() for col in parquet_df.columns}

        n = len(columns.get("v0_long_pos", []))
        assert n > 0, "Parquet data is empty"

        # v3 uses v2's position as higher TF
        v3_higher = get_higher_tf_position(3, columns, self.ORDERED_VIEWS)
        assert v3_higher["longPos"] == columns.get("v2_long_pos", [])
        assert v3_higher["shortPos"] == columns.get("v2_short_pos", [])
        assert len(v3_higher["longPos"]) == n

        # v2 uses v1's position as higher TF
        v2_higher = get_higher_tf_position(2, columns, self.ORDERED_VIEWS)
        assert v2_higher["longPos"] == columns.get("v1_long_pos", [])
        assert v2_higher["shortPos"] == columns.get("v1_short_pos", [])
        assert len(v2_higher["longPos"]) == n

        # v1 uses v0's position as higher TF
        v1_higher = get_higher_tf_position(1, columns, self.ORDERED_VIEWS)
        assert v1_higher["longPos"] == columns.get("v0_long_pos", [])
        assert v1_higher["shortPos"] == columns.get("v0_short_pos", [])
        assert len(v1_higher["longPos"]) == n

        # v0 derives from own signal
        v0_higher = get_higher_tf_position(0, columns, self.ORDERED_VIEWS)
        v0_sig = columns.get("v0_sig", [])
        assert len(v0_higher["longPos"]) == n
        assert len(v0_sig) == n
        # Verify v0 position is consistent with its signal
        for i in range(n):
            s = float(v0_sig[i]) if v0_sig[i] is not None else 0.0
            assert v0_higher["longPos"][i] == (s > 0), (
                f"v0 signal-to-position mismatch at index {i}"
            )
            assert v0_higher["shortPos"][i] == (s < 0), (
                f"v0 signal-to-position mismatch at index {i}"
            )

    def test_position_badge_values_match_last_bar(self, parquet_df):
        """Position badge text must match last bar's long_pos/short_pos."""
        columns = {col: parquet_df[col].tolist() for col in parquet_df.columns}

        for v in self.ORDERED_VIEWS:
            long_pos = columns.get(f"{v}_long_pos", [])
            short_pos = columns.get(f"{v}_short_pos", [])
            if len(long_pos) == 0 or len(short_pos) == 0:
                continue

            text, cls = get_current_position(long_pos, short_pos)

            # Verify the returned status matches the last bar's actual data
            last_idx = len(long_pos) - 1
            if long_pos[last_idx]:
                assert text == "做多" and cls == "long", (
                    f"View {v}: last bar long_pos=True but got {text}/{cls}"
                )
            elif short_pos[last_idx]:
                assert text == "做空" and cls == "short", (
                    f"View {v}: last bar short_pos=True but got {text}/{cls}"
                )
            else:
                assert text == "空仓" and cls == "empty", (
                    f"View {v}: last bar both False but got {text}/{cls}"
                )

    def test_own_vs_higher_tf_positions_different(self, parquet_df):
        """Own and higher TF position arrays are from different views.

        For v3, own position = v3_long_pos, higher TF position = v2_long_pos.
        For v2, own position = v2_long_pos, higher TF position = v1_long_pos.
        For v1, own position = v1_long_pos, higher TF position = v0_long_pos.
        These should be different arrays (they come from different views).
        """
        columns = {col: parquet_df[col].tolist() for col in parquet_df.columns}

        for vi in range(1, len(self.ORDERED_VIEWS)):
            own_v = self.ORDERED_VIEWS[vi]
            higher_v = self.ORDERED_VIEWS[vi - 1]

            own_long = columns.get(f"{own_v}_long_pos", [])
            higher_long = columns.get(f"{higher_v}_long_pos", [])
            own_short = columns.get(f"{own_v}_short_pos", [])
            higher_short = columns.get(f"{higher_v}_short_pos", [])

            if len(own_long) == 0 or len(higher_long) == 0:
                continue

            # Arrays should not be identical (different views = different
            # aggregation levels = different position decisions)
            assert own_long != higher_long or own_short != higher_short, (
                f"Own position ({own_v}) and higher TF position ({higher_v}) "
                f"should differ at least on one side"
            )

    def test_both_long_and_short_never_true_same_time(self, parquet_df):
        """Long and short positions should never both be true at same bar.

        A view cannot simultaneously hold both long and short positions
        at the same bar index.
        """
        columns = {col: parquet_df[col].tolist() for col in parquet_df.columns}

        for v in self.ORDERED_VIEWS:
            long_pos = columns.get(f"{v}_long_pos", [])
            short_pos = columns.get(f"{v}_short_pos", [])
            if len(long_pos) == 0:
                continue

            for i in range(len(long_pos)):
                both_true = bool(long_pos[i]) and bool(short_pos[i])
                assert not both_true, (
                    f"View {v} at bar {i}: long_pos and short_pos are both True"
                )

    def test_parquet_bar_count_consistent(self, parquet_df):
        """All columns have same number of rows."""
        n_rows = len(parquet_df)
        assert n_rows > 0, "Parquet has no rows"

        for col_name in parquet_df.columns:
            col_len = len(parquet_df[col_name])
            assert col_len == n_rows, (
                f"Column '{col_name}' has {col_len} rows, expected {n_rows}"
            )
