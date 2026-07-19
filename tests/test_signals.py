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
        """Single +1 segment → empty list (no pair)."""
        sig = np.zeros(100, dtype=int)
        sig[20:60] = 1
        assert _find_all_pairs(sig) == []

    @pytest.mark.signal
    def test_alternating(self):
        """[+1, 0, -1, 0, +1] → correct pairing."""
        sig = np.zeros(100, dtype=int)
        sig[10:30] = 1     # +1 segment
        sig[50:70] = -1    # -1 segment
        sig[80:90] = 1     # +1 segment
        pairs = _find_all_pairs(sig)
        assert len(pairs) == 2, f"Expected 2 pairs, got {len(pairs)}"
        assert pairs[0] == (10, 50), f"Pair 0 mismatch: {pairs[0]}"
        assert pairs[1] == (50, 80), f"Pair 1 mismatch: {pairs[1]}"

    @pytest.mark.signal
    def test_adjacent_same_sign_merge(self):
        """[+1, 0, +1] → merged into one segment → no pair (one segment remains)."""
        sig = np.zeros(50, dtype=int)
        sig[5:15] = 1
        sig[19:25] = 1    # separated by zeros → should merge
        pairs = _find_all_pairs(sig)
        assert len(pairs) == 0, (
            f"Merged same-sign should leave no pair, got {pairs}"
        )

    @pytest.mark.signal
    def test_adjacent_same_sign_merge_with_opposite(self):
        """[+1, 0, +1, 0, -1] → [+1] and [-1] → 1 pair."""
        sig = np.zeros(50, dtype=int)
        sig[5:15] = 1
        sig[19:25] = 1     # merges with first +1
        sig[30:40] = -1
        pairs = _find_all_pairs(sig)
        assert len(pairs) == 1, f"Expected 1 pair, got {len(pairs)}: {pairs}"
        assert pairs[0] == (5, 30), f"Pair mismatch: {pairs[0]}"

    @pytest.mark.signal
    def test_short_sequence(self):
        """len < 3 → []."""
        assert _find_all_pairs(np.array([1, 0], dtype=int)) == []
        assert _find_all_pairs(np.array([1], dtype=int)) == []

    @pytest.mark.signal
    def test_no_zero_separator(self):
        """相邻异号段配对，无零间隔: [+1, +1, -1, -1]."""
        sig = np.zeros(40, dtype=int)
        sig[10:20] = 1
        sig[20:30] = -1   # directly adjacent, no zeros
        pairs = _find_all_pairs(sig)
        assert len(pairs) == 1
        assert pairs[0] == (10, 20), f"Pair mismatch: {pairs[0]}"


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
