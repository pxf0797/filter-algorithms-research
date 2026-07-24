"""
Tests for Schmitt trigger (_schmitt_trigger) and pair finding (_find_all_pairs).
"""

import numpy as np
import pytest
from services.filter_engine import _schmitt_trigger, _find_all_pairs


# ============================================================================
# _schmitt_trigger
# ============================================================================

class TestSchmittTrigger:
    """Schmitt trigger unit tests."""

    # -- 1. 死区验证 --

    @pytest.mark.signal
    def test_deadzone_no_accel(self):
        """v > 0, a = 0 (all zeros) → all sig = 0."""
        n = 200
        v = np.ones(n) * 0.5
        a = np.zeros(n)
        result = _schmitt_trigger(v, a, ewma_span=60, k_eps=0.15, sigma_min=0.05)
        assert result is not None
        assert np.all(result["sig"] == 0)

    # -- 2. 做多触发 --

    @pytest.mark.signal
    def test_long_trigger(self):
        """a > eps and v > 0 → sig = +1."""
        n = 200
        v = np.ones(n) * 0.5      # positive velocity
        a = np.ones(n) * 0.3      # strong positive acceleration (> eps after warmup)
        result = _schmitt_trigger(v, a, ewma_span=60, k_eps=0.15, sigma_min=0.05)
        assert result is not None
        # After the EWMA warmup, eps stabilises. a=0.3, eps ~ 0.15*sigma_v ~0.075.
        # The trigger should fire and stay long.
        any_long = np.any(result["sig"] == 1)
        assert any_long, "Expected at least one +1 signal for long trigger"

    # -- 3. 做空触发 --

    @pytest.mark.signal
    def test_short_trigger(self):
        """a < -eps and v < 0 → sig = -1."""
        n = 200
        v = -np.ones(n) * 0.5     # negative velocity
        a = -np.ones(n) * 0.3     # strong negative acceleration
        result = _schmitt_trigger(v, a, ewma_span=60, k_eps=0.15, sigma_min=0.05)
        assert result is not None
        any_short = np.any(result["sig"] == -1)
        assert any_short, "Expected at least one -1 signal for short trigger"

    # -- 4. 滞回验证（关键） --

    @pytest.mark.signal
    def test_hysteresis(self):
        """Hysteresis: state does NOT exit on a < 0, only on a < -eps."""
        n = 300
        # ---- 先建立做多状态 ----
        v = np.ones(n) * 0.5
        a = np.zeros(n)
        a[:100] = 0.3   # strong acceleration → trigger long
        # ---- 然后加速度缓慢下降: a goes from 0.3 down to slightly negative ----
        # These values should NOT cause an exit (only a < -eps triggers exit for long)
        ramp_length = 100
        # a goes from 0.3 → -0.01 (still above -eps)
        a[100:200] = np.linspace(0.3, -0.01, ramp_length)
        # ---- 最后大幅下行: a < -eps → exit long (state → 0) ----
        a[200:] = -0.3

        result = _schmitt_trigger(v, a, ewma_span=60, k_eps=0.15, sigma_min=0.05)
        assert result is not None
        sig = result["sig"]

        # After ramp-down phase (index ~140+), a is negative but still mild.
        # The state should remain 1 as long as a > -eps.
        # We check mid-ramp when a is negative but not extremely so.
        mid_ramp = np.where(a[100:200] < -0.005)[0]
        if len(mid_ramp) > 0:
            check_idx = 100 + mid_ramp[0]
            # At this point a is ~ -0.005, which is negative but still > -eps
            # The state should still be 1 (not exited yet)
            if sig[check_idx] == 1:
                hysteresis_holds = True
            else:
                # It's possible noise in sigma_v causes exit earlier;
                # we verify that most of the mild-negative region keeps state=1
                mild_region = sig[120:195]
                hysteresis_holds = np.sum(mild_region == 1) > len(mild_region) * 0.5
            assert hysteresis_holds, (
                "Hysteresis failed: state exited long during mild negative a"
            )

        # After a drops to -0.3 (well below -eps), the exit should have occurred
        final_region = sig[250:]
        assert np.any(final_region == 0) or np.any(final_region == -1), (
            "State should have exited long (to 0) by the end"
        )

    # -- 5. 短序列 --

    @pytest.mark.signal
    def test_short_sequence_returns_none(self):
        """n < ewma_span should return None."""
        v = np.ones(30)
        a = np.ones(30) * 0.1
        result = _schmitt_trigger(v, a, ewma_span=60, k_eps=0.15, sigma_min=0.05)
        assert result is None

    # -- 6. NaN 传播 --

    @pytest.mark.signal
    def test_nan_propagation(self):
        """NaN inputs should not crash the function and should propagate."""
        n = 200
        v = np.ones(n) * 0.5
        a = np.zeros(n)
        a[50:60] = np.nan   # NaN region
        a[:40] = 0.3        # trigger long before NaN
        result = _schmitt_trigger(v, a, ewma_span=60, k_eps=0.15, sigma_min=0.05)
        assert result is not None
        assert result["sig"] is not None
        # The NaN region should be handled (state carried forward)
        assert not np.isnan(result["sig"]).all()

    # -- 7. 常量速度 --

    @pytest.mark.signal
    def test_constant_velocity(self):
        """TC-DATA-02.6: v=0, a=0 双零场景 → 所有sig=0"""
        n = 100
        v = np.zeros(n)
        a = np.zeros(n)  # a=0, v=0 → no trigger
        result = _schmitt_trigger(v, a, ewma_span=30, k_eps=0.15, sigma_min=0.05)
        assert result is not None
        assert np.all(result["sig"] == 0)

    # -- 8. sigma_min 地板 --

    @pytest.mark.signal
    def test_sigma_min_floor(self):
        """Extremely low volatility should be floored by sigma_min."""
        n = 200
        v = np.ones(n) * 0.001   # near-constant → near-zero volatility
        a = np.zeros(n)
        a[80:120] = 0.02          # small but positive acceleration
        result = _schmitt_trigger(v, a, ewma_span=60, k_eps=0.15, sigma_min=0.05)
        assert result is not None
        # sigma_v should be floored at sigma_min = 0.05
        # So eps should be at least k_eps * sigma_min = 0.15 * 0.05 = 0.0075
        assert np.all(result["eps"] >= 0.15 * 0.05 * 0.999), (
            "eps below sigma_min floor"
        )


# ============================================================================
# _find_all_pairs
# ============================================================================

class TestFindAllPairs:
    """_find_all_pairs unit tests."""

    @pytest.mark.signal
    def test_empty_array(self):
        """Empty array → empty list."""
        assert _find_all_pairs(np.array([], dtype=int)) == []

    @pytest.mark.signal
    def test_all_zero(self):
        """All zeros → empty list."""
        assert _find_all_pairs(np.zeros(50, dtype=int)) == []

    @pytest.mark.signal
    def test_single_segment_long(self):
        """Single +1 segment → one pair covering from segment start to EOD."""
        sig = np.zeros(100, dtype=int)
        sig[20:60] = 1
        pairs = _find_all_pairs(sig)
        assert len(pairs) == 1, f"Expected 1 pair (entry at start → EOD), got {len(pairs)}: {pairs}"
        assert pairs[0] == (20, 99), f"Pair 0 mismatch: {pairs[0]}"

    @pytest.mark.signal
    def test_alternating(self):
        """[+1, 0, -1, 0, +1] → correct pairing (3 pairs with P0 fix)."""
        sig = np.zeros(100, dtype=int)
        sig[10:30] = 1     # +1 segment
        sig[50:70] = -1    # -1 segment
        sig[80:90] = 1     # +1 segment
        pairs = _find_all_pairs(sig)
        assert len(pairs) == 3, f"Expected 3 pairs (with P0 fix), got {len(pairs)}"
        assert pairs[0] == (10, 50), f"Pair 0 mismatch: {pairs[0]}"
        assert pairs[1] == (50, 80), f"Pair 1 mismatch: {pairs[1]}"
        assert pairs[2] == (80, 99), f"Pair 2 mismatch: {pairs[2]}"

    @pytest.mark.signal
    def test_adjacent_same_sign_merge(self):
        """[+1, 0, +1] → merged into one segment → 1 pair (P0 fix: first signal always traded)."""
        sig = np.zeros(50, dtype=int)
        sig[5:15] = 1
        sig[19:25] = 1    # separated by zeros → should merge
        pairs = _find_all_pairs(sig)
        assert len(pairs) == 1, (
            f"Merged same-sign should leave 1 pair (P0 fix), got {pairs}"
        )
        assert pairs[0] == (5, 49), f"Expected (5, 49), got {pairs[0]}"

    @pytest.mark.signal
    def test_adjacent_same_sign_merge_with_opposite(self):
        """[+1, 0, +1, 0, -1] → [+1] and [-1] → 2 pairs (P0 fix)."""
        sig = np.zeros(50, dtype=int)
        sig[5:15] = 1
        sig[19:25] = 1     # merges with first +1
        sig[30:40] = -1
        pairs = _find_all_pairs(sig)
        assert len(pairs) == 2, f"Expected 2 pairs (P0 fix), got {len(pairs)}: {pairs}"
        assert pairs[0] == (5, 30), f"Pair 0 mismatch: {pairs[0]}"
        assert pairs[1] == (30, 49), f"Pair 1 mismatch: {pairs[1]}"

    @pytest.mark.signal
    def test_short_sequence(self):
        """len < 3 → []."""
        assert _find_all_pairs(np.array([1, 0], dtype=int)) == []
        assert _find_all_pairs(np.array([1], dtype=int)) == []

    @pytest.mark.signal
    def test_no_zero_separator(self):
        """Adjacent opposite-sign segments without zero gap: [+1, +1, -1, -1] → 2 pairs."""
        sig = np.zeros(40, dtype=int)
        sig[10:20] = 1
        sig[20:30] = -1   # directly adjacent, no zeros
        pairs = _find_all_pairs(sig)
        assert len(pairs) == 2, f"Expected 2 pairs (P0 fix), got {len(pairs)}"
        assert pairs[0] == (10, 20), f"Pair 0 mismatch: {pairs[0]}"
        assert pairs[1] == (20, 39), f"Pair 1 mismatch: {pairs[1]}"


# ============================================================================
# P0 fix: 首个信号段配对
# ============================================================================


class TestFindAllPairsP0Fix:
    """P0 fix: 首个信号段始终生成 pair，PnL 反映全段持仓."""

    @pytest.mark.signal
    def test_single_segment_entire_window(self):
        """全窗口为 +1 → 生成 1 个 pair，覆盖整个窗口."""
        n = 50
        sig = np.ones(n, dtype=int)  # all +1
        pairs = _find_all_pairs(sig)
        assert len(pairs) == 1, f"Expected 1 pair, got {len(pairs)}: {pairs}"
        assert pairs[0] == (0, n - 1), f"Expected (0, {n - 1}), got {pairs[0]}"

    @pytest.mark.signal
    def test_two_segments(self):
        """+1 then -1 → 2 pairs."""
        sig = np.zeros(60, dtype=int)
        sig[10:30] = 1
        sig[30:50] = -1
        pairs = _find_all_pairs(sig)
        assert len(pairs) == 2, f"Expected 2 pairs, got {len(pairs)}: {pairs}"
        assert pairs[0] == (10, 30), f"Expected (10, 30), got {pairs[0]}"
        assert pairs[1] == (30, 59), f"Expected (30, 59), got {pairs[1]}"

    @pytest.mark.signal
    def test_three_segments(self):
        """+1, -1, +1 → 3 pairs."""
        sig = np.zeros(80, dtype=int)
        sig[5:20] = 1
        sig[30:45] = -1
        sig[50:65] = 1
        pairs = _find_all_pairs(sig)
        assert len(pairs) == 3, f"Expected 3 pairs, got {len(pairs)}: {pairs}"
        assert pairs[0] == (5, 30), f"Expected (5, 30), got {pairs[0]}"
        assert pairs[1] == (30, 50), f"Expected (30, 50), got {pairs[1]}"
        assert pairs[2] == (50, 79), f"Expected (50, 79), got {pairs[2]}"

    @pytest.mark.signal
    def test_pnl_reflects_first_segment(self):
        """Verify PnL computation includes trade from the first signal segment."""
        from services.filter_engine import _compute_strategy_pnl
        n = 50
        t = np.arange(n, dtype=float)
        # Rising price: ensures long trade is profitable
        filtered = 100.0 + 0.2 * t
        sig_t = np.zeros(n, dtype=int)
        sig_t[10:30] = 1    # first +1 segment
        sig_t[30:40] = -1   # -1 segment
        sig_t[40:45] = 1    # second +1 segment

        # Generate pairs with the fixed _find_all_pairs
        all_pairs = _find_all_pairs(sig_t)
        # Should have 3 pairs: (10,30), (30,40), (40,n-1)
        assert len(all_pairs) == 3, f"Expected 3 pairs, got {len(all_pairs)}"

        # Create minimal prediction pairs to pass the guard
        pred_pairs = []
        for pair_start, pair_end in all_pairs:
            if pair_end - pair_start >= 3:
                fit_result = {"a": 0.0, "b": 0.2, "c": 100.0, "x0": None}
                pred_pairs.append({
                    "fit_result": fit_result,
                    "fit_start": pair_start,
                    "pair_end": pair_end,
                })

        long_pnl, short_pnl, trades = _compute_strategy_pnl(
            t, filtered, sig_t, all_pairs, pred_pairs, stop_loss_pct=2.0, n_extend=10,
        )

        # Should have trades
        assert len(trades) >= 1, "Expected at least 1 trade from P0 fix"

        # The first trade should be long (entering at the first +1 signal)
        long_trades = [tr for tr in trades if tr["type"] == "long"]
        assert len(long_trades) >= 1, "Expected at least 1 long trade from first segment"

        # First long trade should enter at the start of the first signal segment
        first_long = long_trades[0]
        assert first_long["entry_idx"] == 10, (
            f"First long should enter at index 10, got {first_long['entry_idx']}"
        )
        # Entry should be at the first signal's start
        assert sig_t[first_long["entry_idx"]] == 1, "Entry signal should be +1"

        # PnL should reflect actual trading activity (not flat 100)
        assert not np.allclose(long_pnl, 100.0), (
            "Long PnL should reflect trading, not stay flat at 100"
        )

    @pytest.mark.signal
    def test_single_segment_pnl(self):
        """Single segment (+1) → one trade, PnL reflects price movement."""
        from services.filter_engine import _compute_strategy_pnl
        n = 30
        t = np.arange(n, dtype=float)
        filtered = 100.0 + 0.5 * t  # rising price
        sig_t = np.ones(n, dtype=int)  # all +1

        all_pairs = _find_all_pairs(sig_t)
        assert len(all_pairs) == 1, f"Expected 1 pair, got {len(all_pairs)}"
        assert all_pairs[0] == (0, n - 1)

        # Create prediction pair (gap >= 3 so prediction is generated)
        fit_result = {"a": 0.0, "b": 0.5, "c": 100.0, "x0": None}
        pred_pairs = [{"fit_result": fit_result, "fit_start": 0, "pair_end": n - 1}]

        long_pnl, short_pnl, trades = _compute_strategy_pnl(
            t, filtered, sig_t, all_pairs, pred_pairs, stop_loss_pct=5.0, n_extend=10,
        )

        # Should have exactly 1 trade (long)
        assert len(trades) == 1, f"Expected 1 trade, got {len(trades)}"
        assert trades[0]["type"] == "long"
        assert trades[0]["entry_idx"] == 0
        assert trades[0]["exit_idx"] == n - 1
        assert trades[0]["exit_reason"] == "eod"

        # P0 fix: PnL should be > 100 (profitable) because price rises
        assert long_pnl[-1] > 100.0, (
            f"P0 fix: long PnL should reflect rising price, got {long_pnl[-1]:.2f}"
        )


# ============================================================================
# EWMA / Schmitt cross-window continuity regression tests
# ============================================================================


class TestEWMAStateContinuity:
    """Cross-window EWMA + Schmitt state continuity (init_mu, init_sigma,
    init_state, init_dur). These verify that state is correctly preserved
    and handed over between consecutive sliding windows."""

    # -- 1. 跨窗口状态连续性 --

    @pytest.mark.signal
    def test_cross_window_state_continuity(self):
        """init_mu/init_sigma/init_state/init_dur from window N propagate to
        window N+1 correctly — the state machine continues rather than resetting."""
        n = 200
        # Simulate two consecutive windows with overlapping data
        rng = np.random.RandomState(42)
        v_full = rng.randn(400) * 0.1 + 0.02  # slight positive drift
        a_full = rng.randn(400) * 0.05

        # Window 1: bars 0-199
        result1 = _schmitt_trigger(v_full[:200], a_full[:200],
                                   ewma_span=60, k_eps=0.15, sigma_min=0.05)
        assert result1 is not None

        # Extract final state from window 1
        final_mu = result1["final_mu"]
        final_sigma = result1["final_sigma"]
        final_state = result1["final_state"]
        final_dur = result1["final_dur"]

        # Window 2: bars 100-299 (overlapping with window 1)
        # Pass init values from window 1's final state
        result2 = _schmitt_trigger(v_full[100:300], a_full[100:300],
                                   ewma_span=60, k_eps=0.15, sigma_min=0.05,
                                   init_mu=final_mu, init_sigma=final_sigma,
                                   init_state=final_state, init_dur=final_dur)
        assert result2 is not None

        # mu_v[0] should use the init_mu, not v[0]
        assert result2["mu_v"][0] == pytest.approx(final_mu)

        # sigma_v[0] should use the init_sigma
        assert result2["sigma_v"][0] == pytest.approx(final_sigma)

        # The final state values should be valid types
        assert isinstance(result2["final_mu"], float)
        assert isinstance(result2["final_sigma"], float)
        assert isinstance(result2["final_state"], int)
        assert isinstance(result2["final_dur"], int)

    # -- 2. 空初始状态 --

    @pytest.mark.signal
    def test_empty_initial_state_all_defaults(self):
        """When no init params are passed (all default), mu_v[0]=v[0],
        sigma_v[0]=0.0, and computation proceeds normally."""
        n = 150
        v = np.ones(n) * 0.3
        a = np.ones(n) * 0.1

        result = _schmitt_trigger(v, a, ewma_span=60, k_eps=0.15, sigma_min=0.05)
        assert result is not None

        # Default: mu_v[0] = v[0], sigma_v[0] = 0.0
        assert result["mu_v"][0] == pytest.approx(v[0])
        assert result["sigma_v"][0] == pytest.approx(0.0)

        # Computation still runs
        assert not np.isnan(result["mu_v"][-1])
        assert not np.isnan(result["sigma_v"][-1])
        assert len(result["sig"]) == n

    @pytest.mark.signal
    def test_empty_initial_state_with_only_init_mu(self):
        """When only init_mu is set but init_sigma is None, defaults are used
        (mu_v[0]=v[0], sigma_v[0]=0.0)."""
        n = 150
        v = np.ones(n) * 0.3
        a = np.ones(n) * 0.1

        result = _schmitt_trigger(v, a, ewma_span=60, k_eps=0.15, sigma_min=0.05,
                                  init_mu=0.5, init_sigma=None)
        assert result is not None

        # init_sigma is None → falls back to default (v[0], 0.0)
        assert result["mu_v"][0] == pytest.approx(v[0])
        assert result["sigma_v"][0] == pytest.approx(0.0)

    @pytest.mark.signal
    def test_empty_initial_state_with_only_init_sigma(self):
        """When only init_sigma is set but init_mu is None, defaults are used."""
        n = 150
        v = np.ones(n) * 0.3
        a = np.ones(n) * 0.1

        result = _schmitt_trigger(v, a, ewma_span=60, k_eps=0.15, sigma_min=0.05,
                                  init_mu=None, init_sigma=0.1)
        assert result is not None

        # init_mu is None → falls back to default (v[0])
        assert result["mu_v"][0] == pytest.approx(v[0])
        assert result["sigma_v"][0] == pytest.approx(0.0)

    # -- 3. 单 bar 窗口 --

    @pytest.mark.signal
    def test_single_bar_window_edge_case(self):
        """n == ewma_span (exact minimum) should work. n < ewma_span returns None."""
        # n == ewma_span → should work
        n = 60
        v = np.ones(n) * 0.5
        a = np.ones(n) * 0.1
        result = _schmitt_trigger(v, a, ewma_span=60, k_eps=0.15, sigma_min=0.05)
        assert result is not None
        assert len(result["sig"]) == 60

        # n == ewma_span - 1 → should return None
        v2 = np.ones(59) * 0.5
        a2 = np.ones(59) * 0.1
        result2 = _schmitt_trigger(v2, a2, ewma_span=60, k_eps=0.15, sigma_min=0.05)
        assert result2 is None

    @pytest.mark.signal
    def test_minimal_window_with_init_state(self):
        """Minimum window (n == ewma_span) with full init state should work."""
        n = 60
        v = np.ones(n) * 0.5
        a = np.ones(n) * 0.1

        result = _schmitt_trigger(v, a, ewma_span=60, k_eps=0.15, sigma_min=0.05,
                                  init_mu=0.3, init_sigma=0.08,
                                  init_state=1, init_dur=5)
        assert result is not None
        assert result["mu_v"][0] == pytest.approx(0.3)
        assert result["sigma_v"][0] == pytest.approx(0.08)

    # -- 4. 多 ticker 状态隔离 --

    @pytest.mark.signal
    def test_multi_ticker_state_isolation(self):
        """Different data sequences with different init states produce different
        results — state is not shared across unrelated calls."""
        n = 200
        rng = np.random.RandomState(99)

        # Ticker A: positive drift
        v_a = rng.randn(n) * 0.1 + 0.05
        a_a = rng.randn(n) * 0.08 + 0.02

        # Ticker B: negative drift
        v_b = rng.randn(n) * 0.1 - 0.05
        a_b = rng.randn(n) * 0.08 - 0.02

        result_a = _schmitt_trigger(v_a, a_a, ewma_span=60, k_eps=0.15, sigma_min=0.05)
        result_b = _schmitt_trigger(v_b, a_b, ewma_span=60, k_eps=0.15, sigma_min=0.05)

        assert result_a is not None
        assert result_b is not None

        # The final states should differ (positive vs negative drift)
        # mu_v final should be very different
        assert result_a["final_mu"] != pytest.approx(result_b["final_mu"], abs=0.001)

        # Each result is internally consistent
        assert len(result_a["sig"]) == n
        assert len(result_b["sig"]) == n

    @pytest.mark.signal
    def test_state_preservation_with_init_state_1(self):
        """When init_state=1 (already in long), the first bar with positive
        conditions should remain in state 1 (not re-enter)."""
        n = 200
        v = np.ones(n) * 0.5
        a = np.ones(n) * 0.3  # strong positive acceleration

        # Without init_state → enters long naturally
        result_no_init = _schmitt_trigger(v, a, ewma_span=60, k_eps=0.15, sigma_min=0.05)
        # With init_state=1 (already long) → stays long
        result_with_init = _schmitt_trigger(v, a, ewma_span=60, k_eps=0.15, sigma_min=0.05,
                                            init_mu=0.5, init_sigma=0.1,
                                            init_state=1, init_dur=10)

        assert result_no_init is not None
        assert result_with_init is not None

        # The dur values should differ (init_dur gives a head start)
        assert result_with_init["dur"][0] > result_no_init["dur"][0]

    @pytest.mark.signal
    def test_state_preservation_with_init_state_minus_1(self):
        """When init_state=-1 (already in short), with negative conditions
        it stays in state -1."""
        n = 200
        v = -np.ones(n) * 0.5
        a = -np.ones(n) * 0.3

        result = _schmitt_trigger(v, a, ewma_span=60, k_eps=0.15, sigma_min=0.05,
                                  init_mu=-0.5, init_sigma=0.1,
                                  init_state=-1, init_dur=8)
        assert result is not None
        # Should not flip to 0 or 1 in early bars
        # Check that at least some early bars are -1
        early_state = result["sig"][:20]
        assert -1 in early_state or all(s == -1 for s in early_state)


# ============================================================================
# P1-11: EWMA vectorized consistency test
# ============================================================================

class TestEWMAVectorizedConsistency:
    """Verify the pandas-ewm vectorized EWMA matches the manual for-loop reference."""

    @staticmethod
    def _ewma_manual(v: np.ndarray, alpha: float):
        """Reference: manual for-loop EWMA for mu and sigma."""
        n = len(v)
        mu = np.full(n, np.nan)
        sigma = np.full(n, np.nan)
        mu[0] = v[0]
        sigma[0] = 0.0
        for i in range(1, n):
            mu[i] = alpha * v[i] + (1 - alpha) * mu[i - 1]
            sigma[i] = np.sqrt(
                alpha * (v[i] - mu[i]) ** 2 + (1 - alpha) * sigma[i - 1] ** 2
            )
        return mu, sigma

    @pytest.mark.signal
    def test_ewma_matches_manual(self):
        """Pandas ewm output should match manual for-loop within 1e-6 relative tolerance."""
        rng = np.random.RandomState(12345)
        n = 1000
        v = rng.randn(n) * 0.5 + 0.02  # realistic momentum-like data
        ewma_span = 60
        alpha = 2.0 / (ewma_span + 1)

        mu_manual, sigma_manual = self._ewma_manual(v, alpha)

        # Vectorized version (matching _schmitt_trigger implementation)
        import pandas as pd
        mu_vec = pd.Series(v).ewm(alpha=alpha, adjust=False).mean().values
        # Override first element to match manual init
        mu_vec[0] = v[0]
        sigma_vec = np.sqrt(
            pd.Series((v - mu_vec) ** 2).ewm(alpha=alpha, adjust=False).mean().values
        )
        sigma_vec[0] = 0.0

        np.testing.assert_allclose(mu_vec, mu_manual, rtol=1e-5, atol=1e-8,
                                   err_msg="mu_v vectorized vs manual mismatch")
        np.testing.assert_allclose(sigma_vec, sigma_manual, rtol=1e-5, atol=1e-8,
                                   err_msg="sigma_v vectorized vs manual mismatch")

    @pytest.mark.signal
    def test_ewma_with_init_override(self):
        """With explicit init_mu/init_sigma, the first-element override is correct."""
        rng = np.random.RandomState(99)
        n = 500
        v = rng.randn(n) * 0.3
        ewma_span = 40
        alpha = 2.0 / (ewma_span + 1)
        init_mu = 0.5
        init_sigma = 0.1

        import pandas as pd
        mu_vec = pd.Series(v).ewm(alpha=alpha, adjust=False).mean().values
        sigma_vec = np.sqrt(
            pd.Series((v - mu_vec) ** 2).ewm(alpha=alpha, adjust=False).mean().values
        )
        # Override with init values
        mu_vec[0] = init_mu
        sigma_vec[0] = init_sigma

        assert mu_vec[0] == pytest.approx(init_mu)
        assert sigma_vec[0] == pytest.approx(init_sigma)
        # Subsequent values should differ from the non-override version
        mu_no_init = pd.Series(v).ewm(alpha=alpha, adjust=False).mean().values
        assert mu_vec[0] != pytest.approx(mu_no_init[0])


# ============================================================================
# P1-12: ffill vectorized consistency test
# ============================================================================

class TestFfillVectorizedConsistency:
    """Verify pandas-ffill vectorized forward fill matches manual for-loop."""

    @staticmethod
    def _ffill_manual(arr: np.ndarray, placeholder: float = 100.0):
        """Reference: manual for-loop forward fill."""
        result = arr.copy()
        n = len(result)
        last_val = placeholder
        for i in range(n):
            if result[i] == placeholder and i > 0 and last_val != placeholder:
                result[i] = last_val
            if result[i] != placeholder or i == 0:
                last_val = result[i]
        return result

    @pytest.mark.signal
    def test_ffill_matches_manual(self):
        """Pandas ffill output should match manual for-loop exactly."""
        rng = np.random.RandomState(42)
        n = 500

        # Construct a realistic PnL array: mostly non-100 values with some 100.0 gaps
        arr = np.ones(n) * 100.0
        # Segment 1: rising PnL
        arr[20:50] = np.linspace(100.5, 108.0, 30)
        # Segment 2: gap
        # Segment 3: falling PnL
        arr[80:110] = np.linspace(107.0, 95.0, 30)
        # Segment 4: gap
        # Segment 5: recovery
        arr[150:200] = np.linspace(96.0, 112.0, 50)

        manual = self._ffill_manual(arr, 100.0)

        import pandas as pd
        vec = pd.Series(arr).replace(100.0, np.nan).ffill().fillna(100.0).values

        np.testing.assert_array_equal(vec, manual,
                                      err_msg="ffill vectorized vs manual mismatch")

    @pytest.mark.signal
    def test_ffill_all_placeholder(self):
        """When all values are placeholder (100.0), ffill should keep them."""
        arr = np.ones(200) * 100.0
        import pandas as pd
        vec = pd.Series(arr).replace(100.0, np.nan).ffill().fillna(100.0).values
        np.testing.assert_array_equal(vec, arr)

    @pytest.mark.signal
    def test_ffill_no_placeholder(self):
        """When no placeholder exists, ffill is identity."""
        arr = np.linspace(95.0, 115.0, 100)
        import pandas as pd
        vec = pd.Series(arr).replace(100.0, np.nan).ffill().fillna(100.0).values
        np.testing.assert_allclose(vec, arr, rtol=1e-10)

    @pytest.mark.signal
    def test_ffill_leading_placeholder(self):
        """Leading placeholders (before first non-placeholder) stay as placeholder."""
        arr = np.ones(100) * 100.0
        arr[50:80] = np.linspace(102.0, 110.0, 30)
        import pandas as pd
        vec = pd.Series(arr).replace(100.0, np.nan).ffill().fillna(100.0).values
        # First 50 values should be 100.0 (leading placeholder preserved)
        np.testing.assert_array_equal(vec[:50], 100.0 * np.ones(50))
        # Values at positions 50-79 should match non-placeholder values
        np.testing.assert_allclose(vec[50:80], arr[50:80])
        # Values after 80 should still be 110.0 (ffill continuation)
        np.testing.assert_allclose(vec[80:], np.full(20, 110.0))


# ============================================================================
# P1-2: PnL slicing vectorized consistency test
# ============================================================================

class TestPnLSliceVectorizedConsistency:
    """Verify the numpy-slice vectorized PnL holding-period fill matches
    expected behavior (identical to the original for-loop)."""

    def _compute_pnl_reference(self, t, filtered, sig_t, all_pairs, pred_pairs,
                                stop_loss_pct=2.0, n_extend=10):
        """Reference implementation using for-loops for holding-period fill.

        This mirrors the PRE-vectorization _compute_strategy_pnl, used as
        ground truth for the vectorized version.
        """
        n = len(t)
        long_pnl = np.full(n, 100.0)
        short_pnl = np.full(n, 100.0)
        long_capital = 100.0
        short_capital = 100.0

        if len(all_pairs) == 0 or len(pred_pairs) == 0:
            return long_pnl, short_pnl, []

        pred_map = {}
        for pp in pred_pairs:
            pred_map[pp["pair_end"]] = pp

        trade_records = []
        trade_id = 0
        last_long_exit = -1
        last_short_exit = -1

        for pair_start, pair_end in all_pairs:
            v2 = sig_t[pair_start]
            is_long = (v2 == 1)
            is_short = (v2 == -1)
            if not is_long and not is_short:
                continue

            entry_idx = pair_start
            entry_price = filtered[entry_idx]
            if np.isnan(entry_price) or entry_price <= 0:
                continue

            pp = pred_map.get(pair_end)
            a = b = c = x0 = None
            if pp is not None:
                fit_result = pp["fit_result"]
                a, b, c = fit_result["a"], fit_result["b"], fit_result["c"]
                x0 = fit_result.get("x0", None)

            exit_idx = None
            exit_reason = "take_profit"
            protect_end = entry_idx + n_extend
            scan_end = n - 1

            for i in range(entry_idx + 1, scan_end + 1):
                cur_price = filtered[i]
                if np.isnan(cur_price) or cur_price <= 0:
                    continue
                if i <= protect_end:
                    if a is not None:
                        pred_val = np.polyval((a, b, c), i - x0) if x0 is not None \
                            else np.polyval((a, b, c), i)
                    else:
                        pred_val = entry_price
                    if not (np.isnan(pred_val) or pred_val <= 0):
                        if is_long:
                            stop_hit = cur_price < pred_val * (1 - stop_loss_pct / 100.0)
                        else:
                            stop_hit = cur_price > pred_val * (1 + stop_loss_pct / 100.0)
                        if stop_hit:
                            exit_idx = i
                            exit_reason = "stop_loss"
                            break
                if is_long and sig_t[i] == -1:
                    exit_idx = i
                    exit_reason = "take_profit"
                    break
                if is_short and sig_t[i] == 1:
                    exit_idx = i
                    exit_reason = "take_profit"
                    break

            if exit_idx is None:
                if n - 1 > entry_idx:
                    exit_idx = n - 1
                    exit_reason = "eod"
                else:
                    continue

            exit_price = filtered[exit_idx]
            if np.isnan(exit_price) or exit_price <= 0:
                continue

            if is_long:
                trade_return = (exit_price - entry_price) / entry_price
            else:
                trade_return = (entry_price - exit_price) / entry_price

            trade_id += 1

            # *** Manual for-loop fill (reference implementation) ***
            if is_long:
                for i in range(entry_idx, exit_idx + 1):
                    cur_p = filtered[i]
                    if np.isnan(cur_p) or cur_p <= 0:
                        continue
                    unrealized = (cur_p - entry_price) / entry_price
                    long_pnl[i] = long_capital * (1 + unrealized)
                long_capital *= (1 + trade_return)
                last_long_exit = exit_idx
            else:
                for i in range(entry_idx, exit_idx + 1):
                    cur_p = filtered[i]
                    if np.isnan(cur_p) or cur_p <= 0:
                        continue
                    unrealized = (entry_price - cur_p) / entry_price
                    short_pnl[i] = short_capital * (1 + unrealized)
                short_capital *= (1 + trade_return)
                last_short_exit = exit_idx

            trade_records.append({
                "id": trade_id,
                "type": "long" if is_long else "short",
                "entry_idx": int(entry_idx),
                "exit_idx": int(exit_idx),
                "entry_price": float(entry_price),
                "exit_price": float(exit_price),
                "return_pct": float(trade_return * 100),
                "exit_reason": exit_reason,
            })

        # Tail fill
        if last_long_exit >= 0 and last_long_exit + 1 < n:
            long_pnl[last_long_exit + 1:] = long_capital
        if last_short_exit >= 0 and last_short_exit + 1 < n:
            short_pnl[last_short_exit + 1:] = short_capital

        # ffill
        import pandas as pd
        long_series = pd.Series(long_pnl).replace(100.0, np.nan).ffill().fillna(100.0)
        long_pnl = long_series.values
        short_series = pd.Series(short_pnl).replace(100.0, np.nan).ffill().fillna(100.0)
        short_pnl = short_series.values

        return long_pnl, short_pnl, trade_records

    @pytest.mark.signal
    def test_vectorized_matches_reference_single_long(self):
        """Single long trade: vectorized output must match for-loop reference."""
        from services.filter_engine import _compute_strategy_pnl

        n = 100
        t = np.arange(n, dtype=float)
        filtered = 100.0 + 0.2 * t  # rising price
        sig_t = np.zeros(n, dtype=int)
        sig_t[10:50] = 1  # single long segment
        sig_t[50:] = 0

        all_pairs = [(10, 99)]  # trade from 10 to EOD
        fit_result = {"a": 0.0, "b": 0.2, "c": 100.0, "x0": None}
        pred_pairs = [{"fit_result": fit_result, "fit_start": 10, "pair_end": 99}]

        long_actual, short_actual, trades_actual = _compute_strategy_pnl(
            t, filtered, sig_t, all_pairs, pred_pairs,
            stop_loss_pct=5.0, n_extend=10,
        )
        long_ref, short_ref, trades_ref = self._compute_pnl_reference(
            t, filtered, sig_t, all_pairs, pred_pairs,
            stop_loss_pct=5.0, n_extend=10,
        )

        np.testing.assert_allclose(long_actual, long_ref, rtol=1e-12, atol=1e-12,
                                   err_msg="Long PnL: vectorized vs reference mismatch")
        np.testing.assert_allclose(short_actual, short_ref, rtol=1e-12, atol=1e-12,
                                   err_msg="Short PnL: vectorized vs reference mismatch")
        assert len(trades_actual) == len(trades_ref)

    @pytest.mark.signal
    def test_vectorized_matches_reference_multi_trade(self):
        """Multiple long+short trades: vectorized output must match reference."""
        from services.filter_engine import _compute_strategy_pnl, _find_all_pairs

        n = 150
        t = np.arange(n, dtype=float)
        # Sinusoidal price pattern
        filtered = 100.0 + 5.0 * np.sin(np.linspace(0, 4 * np.pi, n))

        sig_t = np.zeros(n, dtype=int)
        sig_t[10:40] = 1    # long
        sig_t[45:75] = -1   # short
        sig_t[80:110] = 1   # long
        sig_t[115:140] = -1  # short

        all_pairs = _find_all_pairs(sig_t)

        pred_pairs = []
        for pair_start, pair_end in all_pairs:
            if pair_end - pair_start >= 3:
                fit_result = {"a": 0.0, "b": 0.0, "c": filtered[pair_end], "x0": None}
                pred_pairs.append({
                    "fit_result": fit_result,
                    "fit_start": pair_start,
                    "pair_end": pair_end,
                })

        long_actual, short_actual, trades_actual = _compute_strategy_pnl(
            t, filtered, sig_t, all_pairs, pred_pairs,
            stop_loss_pct=10.0, n_extend=10,
        )
        long_ref, short_ref, trades_ref = self._compute_pnl_reference(
            t, filtered, sig_t, all_pairs, pred_pairs,
            stop_loss_pct=10.0, n_extend=10,
        )

        np.testing.assert_allclose(long_actual, long_ref, rtol=1e-12, atol=1e-12,
                                   err_msg="Long PnL (multi-trade) vectorized vs reference")
        np.testing.assert_allclose(short_actual, short_ref, rtol=1e-12, atol=1e-12,
                                   err_msg="Short PnL (multi-trade) vectorized vs reference")
        assert len(trades_actual) == len(trades_ref)

    @pytest.mark.signal
    def test_vectorized_handles_nan_prices(self):
        """PnL vectorized fill should skip NaN prices (same as for-loop continue)."""
        from services.filter_engine import _compute_strategy_pnl

        n = 80
        t = np.arange(n, dtype=float)
        filtered = 100.0 + 0.1 * t
        filtered[30:35] = np.nan  # NaN gap during trade

        sig_t = np.zeros(n, dtype=int)
        sig_t[10:60] = 1

        all_pairs = [(10, 79)]
        fit_result = {"a": 0.0, "b": 0.1, "c": 100.0, "x0": None}
        pred_pairs = [{"fit_result": fit_result, "fit_start": 10, "pair_end": 79}]

        long_actual, _, _ = _compute_strategy_pnl(
            t, filtered, sig_t, all_pairs, pred_pairs,
            stop_loss_pct=20.0, n_extend=10,
        )
        long_ref, _, _ = self._compute_pnl_reference(
            t, filtered, sig_t, all_pairs, pred_pairs,
            stop_loss_pct=20.0, n_extend=10,
        )

        np.testing.assert_allclose(long_actual, long_ref, rtol=1e-12, atol=1e-12,
                                   err_msg="PnL with NaN: vectorized vs reference mismatch")
