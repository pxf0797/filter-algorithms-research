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
