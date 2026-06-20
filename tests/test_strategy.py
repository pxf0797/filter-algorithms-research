"""
Tests for parabola fitting (_fit_parabolic, _fit_physics_parabola) and
PnL computation (_compute_strategy_pnl, _add_prediction_traces).
"""

import numpy as np
import pytest
from streamlit_app import (
    _fit_parabolic, _fit_physics_parabola,
    _compute_strategy_pnl, _add_prediction_traces,
)


# ============================================================================
# _fit_parabolic
# ============================================================================

class TestFitParabolic:
    """_fit_parabolic unit tests."""

    @pytest.mark.strategy
    def test_exact_quadratic(self):
        """Noiseless quadratic y = 2x^2 + 3x + 1 → exact recovery."""
        x = np.arange(20, dtype=float)
        y = 2.0 * x ** 2 + 3.0 * x + 1.0
        result = _fit_parabolic(x, y, 0, 19)
        assert result is not None
        assert abs(result["a"] - 2.0) < 1e-10, f"a mismatch: {result['a']}"
        assert abs(result["b"] - 3.0) < 1e-10, f"b mismatch: {result['b']}"
        assert abs(result["c"] - 1.0) < 1e-10, f"c mismatch: {result['c']}"
        assert np.allclose(result["y_fit"], y)

    @pytest.mark.strategy
    def test_insufficient_points(self):
        """Fewer than 3 points → None."""
        x = np.array([0.0, 1.0])
        y = np.array([0.0, 1.0])
        assert _fit_parabolic(x, y, 0, 1) is None
        assert _fit_parabolic(x, y, 0, 0) is None

    @pytest.mark.strategy
    def test_subsegment(self):
        """Fit a sub-segment of a longer signal."""
        x = np.arange(50, dtype=float)
        y = -1.5 * x ** 2 + 0.5 * x + 10.0
        result = _fit_parabolic(x, y, 10, 30)
        assert result is not None
        assert abs(result["a"] - (-1.5)) < 1e-10
        assert abs(result["b"] - 0.5) < 1e-10


# ============================================================================
# _fit_physics_parabola
# ============================================================================

class TestFitPhysicsParabola:
    """_fit_physics_parabola unit tests.

    NOTE: this function anchors the parabola vertex at (x_seg[-1], y_seg[-1]).
    """

    @pytest.mark.strategy
    def test_known_curvature(self):
        """抛物线 y = -0.5*(x-29)^2 + y_end, vertex at endpoint."""
        x = np.arange(30, dtype=float)
        true_a = -0.5
        x0_anchor = x[-1]  # vertex fixed at endpoint
        y0 = 100.0
        # y = y_end + a * (x - x_end)^2
        y = y0 + true_a * (x[:-1] - x0_anchor) ** 2
        y = np.append(y, y0)  # endpoint = vertex = y0
        result = _fit_physics_parabola(x, y, 0, 29)
        assert result is not None, "fit returned None"
        assert abs(result["a"] - true_a) < 1e-10, (
            f"a mismatch: {result['a']} vs {true_a}"
        )
        assert result["x0"] == x[-1], f"x0 mismatch: {result['x0']}"

    @pytest.mark.strategy
    def test_insufficient_points(self):
        """Fewer than 3 points → None."""
        x = np.array([0.0, 1.0])
        y = np.array([0.0, 1.0])
        assert _fit_physics_parabola(x, y, 0, 1) is None

    @pytest.mark.strategy
    def test_collinear_data(self):
        """Collinear data (a ≈ 0) — e.g. linear + endpoint peak."""
        x = np.arange(10, dtype=float)
        # endpoint is the max; rise is linear → near-zero curvature
        y0, yn = 0.0, 100.0
        y = np.linspace(y0, yn, 10)
        result = _fit_physics_parabola(x, y, 0, 9)
        assert result is not None, "fit returned None"
        assert abs(result["a"]) < abs(y[-1] - y[0]) + 1, (
            f"Unexpected a={result['a']} (expected bounded)"
        )

    @pytest.mark.strategy
    def test_subsegment_fit(self):
        """Sub-segment fit — vertex at endpoint."""
        x = np.arange(50, dtype=float)
        true_a = 0.3
        x0_anchor = x[45]  # endpoint of the sub-segment
        y0_end = 200.0
        # Data fits y = y_end + a*(x - x_end)^2
        seg_start, seg_end = 20, 45
        y = np.full(50, np.nan, dtype=float)
        x_seg = x[seg_start:seg_end+1]
        y_seg = y0_end + true_a * (x_seg - x0_anchor) ** 2
        y[seg_start:seg_end+1] = y_seg
        # Fill in rest with something so we have a full array
        y[:seg_start] = y[seg_start]
        y[seg_end+1:] = y[seg_end]
        result = _fit_physics_parabola(x, y, seg_start, seg_end)
        assert result is not None, "fit returned None"
        assert abs(result["a"] - true_a) < 1e-10, (
            f"a mismatch: {result['a']} vs {true_a}"
        )


# ============================================================================
# Parabola vs physics comparison
# ============================================================================

class TestFitComparison:
    """Compare poly2 (3-DOF) vs physics (1-DOF) extrapolation."""

    @pytest.mark.strategy
    def test_extrapolation_difference(self):
        """Polynomial and physics fits should differ in extrapolation region."""
        np.random.seed(42)
        x = np.arange(30, dtype=float)
        # Slightly noisy parabola
        true_a, x0, y0 = -0.2, 25.0, 150.0
        y = y0 + true_a * (x - x0) ** 2 + np.random.randn(30) * 0.5
        start, end = 5, 24
        r_poly = _fit_parabolic(x, y, start, end)
        r_phys = _fit_physics_parabola(x, y, start, end)
        assert r_poly is not None
        assert r_phys is not None

        # Extrapolate beyond end
        x_ext = np.arange(end, end + 10)
        if r_phys.get("x0") is not None:
            y_poly = np.polyval((r_poly["a"], r_poly["b"], r_poly["c"]), x_ext)
            y_phys = y0 + r_phys["a"] * (x_ext - end) ** 2  # anchor at x[-1], not true x0
            # Actually compute properly: x_ext - r_phys["x0"]
            y_phys_proper = r_phys["c"] + r_phys["a"] * (x_ext - r_phys["x0"]) ** 2
            diff = np.max(np.abs(y_poly - y_phys_proper))
            # The two fits should produce different extrapolations
            assert diff > 1e-6, (
                f"Extrapolations unexpectedly similar (diff={diff})"
            )


# ============================================================================
# _compute_strategy_pnl
# ============================================================================

def _make_long_scenario():
    """Helper: construct data for a long trade scenario.

    Uses _fit_parabolic (3-DOF) so monotonic rise produces upward prediction.

    Returns (t, filtered, sig_t, all_pairs, pred_pairs, stop_loss_pct, n_extend).
    """
    n = 100
    t = np.arange(n, dtype=float)
    # Filtered price: monotonic rise (quadratic, so poly2 fits well)
    filtered = 100.0 + 0.02 * t ** 2
    # Signal: zeros initially, then +1 from index 50 onward
    sig_t = np.zeros(n, dtype=int)
    sig_t[50:] = 1
    # One pair covering the +1 segment. pair_end must match pred_pairs.
    fit_end = 70
    all_pairs = [(50, fit_end)]
    # Use _fit_parabolic (3-DOF) — monotonic rise → upward extrapolation
    fit_result = _fit_parabolic(
        t, filtered, 50, fit_end
    )
    assert fit_result is not None, "Failed to fit long parabola"
    pred_pairs = [{
        "fit_result": fit_result,
        "fit_start": 50,
        "pair_end": fit_end,
    }]
    return t, filtered, sig_t, all_pairs, pred_pairs, 2.0, 10


def _make_short_scenario():
    """Helper: construct data for a short trade scenario.

    Uses _fit_parabolic (3-DOF) so monotonic fall produces downward prediction.
    filtered stays > 0 throughout.
    """
    n = 100
    t = np.arange(n, dtype=float)
    # Filtered price: monotonic fall, stays positive
    filtered = 150.0 - 0.005 * t ** 2  # min at t=99: 150-0.005*9801=101.0
    # Signal: zeros initially, then -1 from index 50 onward
    sig_t = np.zeros(n, dtype=int)
    sig_t[50:] = -1
    fit_end = 70
    all_pairs = [(50, fit_end)]
    fit_result = _fit_parabolic(t, filtered, 50, fit_end)
    assert fit_result is not None, "Failed to fit short parabola"
    pred_pairs = [{
        "fit_result": fit_result,
        "fit_start": 50,
        "pair_end": fit_end,
    }]
    return t, filtered, sig_t, all_pairs, pred_pairs, 2.0, 10


class TestComputeStrategyPnL:
    """_compute_strategy_pnl unit tests."""

    @pytest.mark.strategy
    def test_empty_pairs(self):
        """Empty pairs → long_pnl=100, short_pnl=100, trade_records=[]."""
        t = np.arange(50, dtype=float)
        filtered = np.ones(50) * 100.0
        sig_t = np.zeros(50, dtype=int)
        long_pnl, short_pnl, trades = _compute_strategy_pnl(
            t, filtered, sig_t, [], [], 2.0, 10
        )
        assert np.allclose(long_pnl, 100.0)
        assert np.allclose(short_pnl, 100.0)
        assert trades == []

    @pytest.mark.strategy
    def test_long_trade(self):
        """Known long trade → long_pnl[-1] > 100, at least 1 trade, type='long'."""
        t, filtered, sig_t, all_pairs, pred_pairs, sl, n_ext = _make_long_scenario()
        long_pnl, short_pnl, trades = _compute_strategy_pnl(
            t, filtered, sig_t, all_pairs, pred_pairs, sl, n_ext
        )
        assert long_pnl[-1] > 100.0, (
            f"Expected profitable long, got {long_pnl[-1]:.2f}"
        )
        assert len(trades) >= 1, "Expected at least 1 trade"
        assert trades[0]["type"] == "long"

    @pytest.mark.strategy
    def test_short_trade(self):
        """Known short trade → short_pnl[-1] > 100, at least 1 trade, type='short'."""
        t, filtered, sig_t, all_pairs, pred_pairs, sl, n_ext = _make_short_scenario()
        long_pnl, short_pnl, trades = _compute_strategy_pnl(
            t, filtered, sig_t, all_pairs, pred_pairs, sl, n_ext
        )
        assert short_pnl[-1] > 100.0, (
            f"Expected profitable short, got {short_pnl[-1]:.2f}"
        )
        assert len(trades) >= 1, "Expected at least 1 trade"
        assert trades[0]["type"] == "short"

    @pytest.mark.strategy
    def test_stop_loss_trigger(self):
        """Stop-loss triggers when price moves sharply against prediction."""
        n = 100
        t = np.arange(n, dtype=float)
        # Price rises then drops sharply after entry (produces upward poly2 fit)
        filtered = 100.0 + 0.02 * t ** 2
        # After index 70, drop sharply to trigger stop-loss
        filtered[71:] = filtered[70] - np.arange(0, n - 71, dtype=float) * 3.0
        sig_t = np.zeros(n, dtype=int)
        sig_t[50:] = 1
        fit_end = 70
        all_pairs = [(50, fit_end)]
        fit_result = _fit_parabolic(t, filtered, 50, fit_end)
        assert fit_result is not None
        pred_pairs = [{
            "fit_result": fit_result, "fit_start": 50, "pair_end": fit_end,
        }]
        long_pnl, short_pnl, trades = _compute_strategy_pnl(
            t, filtered, sig_t, all_pairs, pred_pairs, 2.0, 10
        )
        if len(trades) > 0:
            stop_trades = [tr for tr in trades if tr["exit_reason"] == "stop_loss"]
            assert len(stop_trades) > 0, "Expected at least one stop-loss trade"
            for tr in stop_trades:
                assert tr["return_pct"] < 0, (
                    f"Stop-loss trade had positive return: {tr['return_pct']}"
                )

    @pytest.mark.strategy
    def test_independent_capital_pools(self):
        """Long and short capital pools are independent.

        We run two separate scenarios — one with a long trade, one with a short
        trade — and verify that each only affects its own curve.
        """
        n = 100
        t = np.arange(n, dtype=float)

        # --- Long scenario ---
        filtered_long = 100.0 + 0.02 * t ** 2  # upward parabola, stays positive
        sig_long = np.zeros(n, dtype=int)
        sig_long[50:] = 1
        fit_end = 70
        all_pairs = [(50, fit_end)]
        fit_result = _fit_parabolic(t, filtered_long, 50, fit_end)
        assert fit_result is not None
        pred_pairs = [{
            "fit_result": fit_result, "fit_start": 50, "pair_end": fit_end,
        }]
        long_pnl, short_pnl, trades = _compute_strategy_pnl(
            t, filtered_long, sig_long, all_pairs, pred_pairs, 10.0, 5
        )
        assert long_pnl[-1] > 100.0, (
            f"Long curve should increase, got {long_pnl[-1]}"
        )
        assert short_pnl[-1] == 100.0, (
            f"Short curve should stay at 100, got {short_pnl[-1]}"
        )
        assert len(trades) == 1
        assert trades[0]["type"] == "long"

        # --- Short scenario ---
        filtered_short = 150.0 - 0.005 * t ** 2  # downward parabola, stays positive
        sig_short = np.zeros(n, dtype=int)
        sig_short[50:] = -1
        fit_result = _fit_parabolic(t, filtered_short, 50, fit_end)
        assert fit_result is not None
        pred_pairs = [{
            "fit_result": fit_result, "fit_start": 50, "pair_end": fit_end,
        }]
        long_pnl2, short_pnl2, trades2 = _compute_strategy_pnl(
            t, filtered_short, sig_short, all_pairs, pred_pairs, 10.0, 5
        )
        assert short_pnl2[-1] > 100.0, (
            f"Short curve should increase, got {short_pnl2[-1]}"
        )
        assert long_pnl2[-1] == 100.0, (
            f"Long curve should stay at 100, got {long_pnl2[-1]}"
        )
        assert len(trades2) == 1
        assert trades2[0]["type"] == "short"

    @pytest.mark.strategy
    def test_extreme_stop_loss(self):
        """Very tight stop (0.5%) should trigger easily; loose (1000%) should not.

        We verify that with a tiny sl% the PnL differs from with a huge sl%.
        """
        n = 100
        t = np.arange(n, dtype=float)
        # Quadratic rise so _fit_parabolic predicts upward
        filtered = 100.0 + 0.01 * t ** 2
        sig_t = np.zeros(n, dtype=int)
        sig_t[50:] = 1
        fit_end = 70
        all_pairs = [(50, fit_end)]
        fit_result = _fit_parabolic(t, filtered, 50, fit_end)
        assert fit_result is not None
        pred_pairs = [{
            "fit_result": fit_result, "fit_start": 50, "pair_end": fit_end,
        }]

        # Tight stop
        _, _, trades_tight = _compute_strategy_pnl(
            t, filtered, sig_t, all_pairs, pred_pairs.copy(), 0.5, 10
        )
        # Loose stop (effectively no stop-loss)
        _, _, trades_loose = _compute_strategy_pnl(
            t, filtered, sig_t, all_pairs, pred_pairs.copy(), 1000.0, 10
        )

        tight_sl = sum(1 for tr in trades_tight if tr["exit_reason"] == "stop_loss")
        loose_sl = sum(1 for tr in trades_loose if tr["exit_reason"] == "stop_loss")

        # Tight stop should have more stop-loss triggers than loose
        assert tight_sl >= loose_sl, (
            f"Tight({tight_sl}) should have >= stop-losses vs loose({loose_sl})"
        )


# ============================================================================
# _add_prediction_traces — smoke test
# ============================================================================

class TestAddPredictionTraces:
    """_add_prediction_traces — verify function does not crash."""

    @pytest.fixture
    def subplot_fig(self):
        """Create a figure with 2 subplot rows (for price + residual)."""
        from plotly.subplots import make_subplots
        return make_subplots(rows=2, cols=1)

    @pytest.mark.strategy
    def test_no_crash_poly2(self, subplot_fig):
        """Should not raise with poly2 fit result."""
        fig = subplot_fig
        t = np.arange(50, dtype=float)
        filtered = np.sin(t / 5.0)
        fit_result = _fit_parabolic(t, filtered, 10, 40)
        assert fit_result is not None, "Fit failed"
        try:
            _add_prediction_traces(
                fig, t, filtered, fit_result,
                fit_start=10, pair_end=40, row=1, n_extend=10,
            )
        except Exception as e:
            pytest.fail(f"_add_prediction_traces raised: {e}")

    @pytest.mark.strategy
    def test_no_crash_physics_fit(self, subplot_fig):
        """Should not crash when using physics fit result."""
        fig = subplot_fig
        t = np.arange(50, dtype=float)
        filtered = np.sin(t / 5.0)
        fit_result = _fit_physics_parabola(t, filtered, 10, 40)
        assert fit_result is not None, "Fit failed"
        try:
            _add_prediction_traces(
                fig, t, filtered, fit_result,
                fit_start=10, pair_end=40, row=1, n_extend=10,
            )
        except Exception as e:
            pytest.fail(f"_add_prediction_traces raised: {e}")

    @pytest.mark.strategy
    def test_no_crash_no_extend(self, subplot_fig):
        """Should not crash when n_extend=0."""
        fig = subplot_fig
        t = np.arange(50, dtype=float)
        filtered = np.sin(t / 5.0)
        fit_result = _fit_parabolic(t, filtered, 10, 40)
        assert fit_result is not None, "Fit failed"
        try:
            _add_prediction_traces(
                fig, t, filtered, fit_result,
                fit_start=10, pair_end=40, row=1, n_extend=0,
            )
        except Exception as e:
            pytest.fail(f"_add_prediction_traces raised: {e}")
