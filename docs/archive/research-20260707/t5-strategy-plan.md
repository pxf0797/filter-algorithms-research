# Progressive Strategy Improvement Plan

## Known Root Causes

These findings drive the entire plan. They come from prior analysis and are treated as established facts.

### P0 Issues

1. **Butterworth `filtfilt` causes global recomputation.** The zero-phase forward+backward filtering means approximately 95% of historical `Sig` values change on every bar advance. This is inherent to `filtfilt` -- it is a non-causal operation that reuses future data to compute past values, so every new bar rewrites history.

2. **`sigma_v` EWMA cascade.** The `_schmitt_trigger()` method uses EWMA recursion for `sigma_v`. A change at position K propagates to all `sigma_v[K:]`, `eps[K:]`, and `sig[K:]` values -- a cascade spanning 60+ bars.

3. **Schmitt trigger state machine is hysteretic.** Any `sig` flip cascades forward through all subsequent states, because the trigger's upper/lower band logic depends on the prior state. A single change at the edge ripples through the entire state sequence.

### P1 Issues

1. **Sentinel collision in PnL computation.** `_compute_strategy_pnl()` at lines 843-858 of `services/filter_engine.py` uses `100.0` as a sentinel for "not holding." If PnL returns exactly to 100.0 (a legitimate breakeven scenario), the forward-fill logic incorrectly treats it as a non-holding period.

2. **Stale cross-TF PnL in session state.** `session_state._pnl_{tf}` keys hold PnL data from higher timeframes. When the backtest advances, these older values are not invalidated, leading to stale PnL being displayed or used in calculations.

### Mitigating Facts

- SMA/EMA (causal filters) only affect the last 1-5 bars at the right edge. They do not cause historical rewrites.
- `_fit_parabolic` and `_find_all_pairs` are locally pure functions -- they compute from a fixed window and do not propagate changes.
- Data loader (`_sync_all_cascading`) is not a root cause. The instability originates in the signal chain, not in data ingestion.

---

## Level 0: Bug Fixes (Immediate, Low Risk)

These are unambiguous bugs with trivial fixes. They should ship first, before any architectural changes.

### L0.1: Sentinel Fix in `_compute_strategy_pnl()`

**File:** `services/filter_engine.py`

**Problem:** The sentinel value `100.0` is a legitimate PnL value. When a position returns exactly to entry (zero PnL), the forward-fill logic in lines 843-858 misinterprets it as "not holding" and carries forward the previous non-null PnL value.

**Fix:**
1. Replace the sentinel `100.0` with `np.nan` to represent "not holding."
2. Introduce a separate boolean mask (`is_holding`) that tracks whether a position was held at each bar, independent of the PnL value.
3. Forward-fill PnL only where `is_holding` is True.

**Estimated effort:** 5 lines changed. **Risk:** None. This is a pure bug fix.

### L0.2: Stale PnL Invalidation

**File:** `streamlit_app.py`

**Problem:** `session_state._pnl_{tf}` keys accumulate PnL data over time. When the backtest advances to a new bar, higher-timeframe PnL values (e.g., daily PnL when viewing 60-min data) are not refreshed, causing stale data to surface.

**Fix:**
1. On each backtest frame advance, clear or tag all `_pnl_*` session_state keys with a `cutoff_date`.
2. When reading PnL from session state, filter out entries older than the current backtest window.
3. Alternatively, regenerate higher-TF PnL on each advance if performance allows.

**Estimated effort:** 15 lines changed. **Risk:** Low. Add `cutoff_date` guard, verify PnL display updates correctly.

---

## Level 1: Signal Stability (Core Improvement)

This level addresses the P0 root causes directly. The goal is to reduce signal instability from "most bars change every advance" to "only the last 2-5 bars may change, and those are explicitly marked as pending."

### L1.1: Filter Guidance and Causal Defaults

**Files:** `services/filter_engine.py`, `components/sidebar.py`

**Problem:** Butterworth and LOWESS filters (non-causal) are available alongside SMA/EMA (causal) with no indication of the stability difference. Users unknowingly select Butterworth and experience signal churn.

**Fix:**
1. In `components/sidebar.py`, add a visual warning (unicode warning icon with tooltip) when Butterworth or LOWESS is selected. Text: "Non-causal filter -- historical signals may change as new data arrives."
2. Change the default filter to SMA (period=20) for backtest mode.
3. Optionally add a `lfilter` (causal, forward-only) mode for Butterworth, which avoids the zero-phase problem at the cost of phase lag. This gives users a Butterworth option that does not rewrite history.

**Estimated effort:** 30 lines changed. **Expected impact:** 10x stability gain for users who follow the guidance and switch to causal filters.

### L1.2: Edge Bar Locking

**Files:** `services/filter_engine.py` (`_schmitt_trigger`), `components/charts.py`

**Problem:** Every bar advance recomputes the entire Schmitt trigger state sequence. The rightmost N bars are inherently unstable because they depend on incomplete filter output (ringing, boundary effects). Historical bars should be locked once they leave the edge zone.

**Fix:**
1. Define an edge zone of N bars at the rightmost end of the data, where N defaults to `filter_window / 2`.
2. In `_schmitt_trigger`, mark Sig values for bars within the edge zone as `pending` (distinct from +1/0/-1).
3. Once a bar's index moves outside the edge zone (i.e., more than N new bars have arrived after it), lock its Sig value permanently.
4. In `components/charts.py`, render pending Sig values with a distinct visual style (dashed line, lighter color, or annotation) so users understand which signals are provisional.

**Implementation approach:** Store a `_locked_sig` array that is appended to when bars exit the edge zone. The active Sig array is `_locked_sig` concatenated with the pending edge Sig values. On each advance, compare the new edge Sig values against the previously locked ones and only update if they are still within the edge zone.

**Estimated effort:** 80 lines changed. **Expected impact:** UX transformed -- users see stable historical signals and explicitly provisional edge signals, eliminating confusion about "why did my past signals change?"

### L1.3: Signal Confirmation

**Files:** `services/filter_engine.py`

**Problem:** The Schmitt trigger produces instantaneous flips. A single bar of opposite direction can flip the state, producing false signals from noise.

**Fix:**
1. Add a `confirm_bars` parameter to `_schmitt_trigger` (default=2).
2. Before committing a Sig direction change, require the new direction to persist for `confirm_bars` consecutive bars.
3. During the confirmation window, mark the signal as `pending_confirm` (distinct from the edge-locking `pending`).

**Implementation approach:** Maintain a counter for the current candidate direction. When the raw Schmitt output differs from the committed Sig, increment the counter. When it reaches `confirm_bars`, commit the flip. If it reverts before reaching `confirm_bars`, reset the counter.

**Estimated effort:** 40 lines changed. **Expected impact:** 30-50% fewer false signals from single-bar noise.

---

## Level 2: Multi-TF Coordination

These improvements use the known higher-timeframe context to filter or score lower-timeframe signals. They assume Level 1 stability work is complete.

### L2.1: Higher-TF Gating

**Files:** `services/filter_engine.py` (new `_apply_tf_constraint` method), `streamlit_app.py`

**Description:** When daily Sig=+1 (bullish), suppress short signals on 60-min (set to 0). When daily Sig=-1 (bearish), suppress long signals. This enforces "trade with the trend."

**Configurable modes:**
- **Strict:** Short signals on 60-min are forced to 0 when daily Sig=+1. Long signals forced to 0 when daily Sig=-1.
- **Loose:** Short signals on 60-min are reduced in weight (e.g., position size halved) rather than suppressed entirely.

**Implementation approach:** After computing Sig on the lower timeframe, call `_apply_tf_constraint(lower_sig, higher_sig, mode)`. This is a post-processing step, not a modification to the filter pipeline itself.

**Estimated effort:** 80 lines changed (new method + UI toggle + wiring). **Risk:** Medium. Requires careful testing of edge cases (what if higher-TF data is not yet available for the most recent bars?).

### L2.2: Alignment Scoring

**Files:** `services/filter_engine.py`

**Description:** For each trade signal, compute an alignment score (0.0-1.0) representing what fraction of higher timeframes agree with the signal direction. Use existing `_compute_holding_masks` infrastructure.

**Implementation approach:** For each bar where Sig is non-zero on the trading timeframe, look up the Sig values on all higher timeframes. Score = (number of higher TFs with matching sign) / (total number of higher TFs). Display the score alongside trade signals in the UI.

**Estimated effort:** 50 lines changed. **Expected impact:** Users can filter trades by alignment quality, reducing counter-trend entries.

---

## Level 3: Risk Management (Future Iteration)

These features build on the stable signal foundation from Levels 1-2. Not scoped for immediate implementation.

### L3.1: ATR-Based Dynamic Stop Loss

Complement the existing prediction-based stop with an ATR-based dynamic stop. The wider of the two stops governs. ATR period defaults to 14.

### L3.2: Signal Stability-Based Position Sizing

Use the signal confirmation count and alignment score from L1.3 and L2.2 to scale position size. Higher confidence = larger position within risk limits.

### L3.3: Batch Entry/Exit

Instead of entering/exiting the full position on a single signal flip, scale in/out over multiple bars. Reduces slippage and noise sensitivity.

---

## Level 4: Validation Framework (Future Iteration)

Production-grade validation that enables systematic strategy improvement.

### L4.1: Performance Panel

Display Sharpe ratio, Calmar ratio, max drawdown, win rate, profit factor, and average trade duration. Auto-compute on each backtest run.

### L4.2: Train/Test Split

Split historical data into train (earlier 70%) and test (later 30%) periods. Optimize parameters on train, validate on test. Report both in-sample and out-of-sample metrics.

### L4.3: Parameter Sensitivity Heatmap

For each tunable parameter (filter period, Schmitt band width, confirm_bars, etc.), compute PnL across a grid of values and render as a heatmap. Helps users understand which parameters drive performance and which are insensitive.

---

## MVP Scope

The minimum viable improvement targets L0 + L1.1 + L1.2:

- **L0.1:** Sentinel fix (5 lines)
- **L0.2:** Stale PnL fix (15 lines)
- **L1.1:** Filter guidance + causal defaults (30 lines)
- **L1.2:** Edge bar locking (80 lines)

**Total:** ~130 lines changed across 4 files.  
**Expected outcome:** Signal instability reduced from "most bars change on every advance" to "only the last 2-5 bars may change, and those are explicitly marked as pending."

---

## Implementation Priority

| Priority | Level | Description | Effort | Impact |
|----------|-------|-------------|--------|--------|
| Now | L0.1 | Sentinel fix | 5 lines | Bug eliminated |
| Now | L0.2 | Stale PnL invalidation | 15 lines | Bug eliminated |
| Now | L1.1 | Filter guidance + causal defaults | 30 lines | 10x stability gain |
| Next | L1.2 | Edge bar locking | 80 lines | UX transformed |
| Next | L1.3 | Signal confirmation | 40 lines | 30-50% fewer false signals |
| Later | L2.1 | Higher-TF gating | 80 lines | Smarter entries |
| Later | L2.2 | Alignment scoring | 50 lines | Smarter entries |
| Future | L3.x | Risk management | 200+ lines | Production readiness |
| Future | L4.x | Validation framework | 200+ lines | Production readiness |

---

## File Map

| File | Levels Touched |
|------|---------------|
| `services/filter_engine.py` | L0.1, L1.1, L1.2, L1.3, L2.1, L2.2, L3.x, L4.x |
| `components/sidebar.py` | L1.1 |
| `components/charts.py` | L1.2 |
| `streamlit_app.py` | L0.2, L2.1 |

---

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Edge locking L1.2 could mask genuine signal changes if the edge zone is too wide | Default N = filter_window/2; make N configurable; always show pending zone visually |
| Signal confirmation L1.3 increases entry/exit latency | Default confirm_bars=2 (small); make configurable; document trade-off |
| Higher-TF gating L2.1 could suppress valid counter-trend trades | Loose mode halves position instead of suppressing; strict mode is opt-in |
| `lfilter` mode for Butterworth introduces phase lag | Document clearly; position as a trade-off option, not a default |
