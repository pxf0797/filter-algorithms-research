# Data Computation Test Cases

## Scope

Covering 10 filter algorithms, Schmitt Trigger, 2 parabolic fit methods, PnL backtesting engine, and time alignment module in `/Users/xfpan/claude/filter_research/filter_app/streamlit_app.py`.

---

### TC-DATA-01.1: SMA -- Constant Signal

- **Priority**: P0
- **Function**: `apply_sma` (line 38)
- **Input**: `signal = [5.0, 5.0, 5.0, 5.0, 5.0]`, `t = [0,1,2,3,4]`, `window=3` (auto-adjusted to 5, which is > len, clamped by `np.convolve(signal, kernel, mode="same")`). Test with `window=3` even.
  - Because `window % 2 == 0 => window += 1 => window=3`. Kernel = [1/3, 1/3, 1/3].
  - `np.convolve([5,5,5,5,5], [1/3,1/3,1/3], mode="same")`
- **Expected output**: `[5.0, 5.0, 5.0, 5.0, 5.0]` (constant input yields constant output)
  - Manual: conv result = `[5/3, 10/3, 5, 10/3, 5/3]` under `same` mode with zero padding. **Wait -- `np.convolve(..., mode="same")`** pads with zeros, so edge values will NOT be 5.0. The correct expectation depends on boundary behavior.
  - **Corrected expectation**: Center values = 5.0. Edge values approximate 5.0 (the `same` mode adds zero padding on one side). All values should be finite and near 5.0 (within float tolerance).
- **Verification**: `np.allclose(result, 5.0, atol=1e-10)` -- center elements; or simply `not np.any(np.isnan(result)) and result[2] == 5.0`.

---

### TC-DATA-01.2: SMA -- Window > Signal Length

- **Priority**: P1
- **Function**: `apply_sma` (line 38)
- **Input**: `signal = [1.0, 2.0, 3.0]`, `window=10`.
  - `window = min(window, len(signal))` => `window = 3` (auto odd: 3 stays 3)
  - Kernel = [1/3, 1/3, 1/3], convolve with mode="same"
- **Expected output**: Output length = 3, no NaN, no error.
- **Verification**: Script: `len(result) == 3 and not np.any(np.isnan(result))`.

---

### TC-DATA-01.3: EMA -- Constant Signal

- **Priority**: P0
- **Function**: `apply_ema` (line 46)
- **Input**: `signal = [10.0, 10.0, 10.0, 10.0, 10.0]`, `span=10`.
  - `DataFrame({"v": signal}).ewm(span=10, adjust=False).mean()`
- **Expected output**: `[10.0, 10.0, 10.0, 10.0, 10.0]` (constant signal, EMA decays to the mean).
  - First value = 10.0, each subsequent value = `alpha * 10.0 + (1-alpha) * 10.0 = 10.0`.
- **Verification**: `np.allclose(result, 10.0)`.

---

### TC-DATA-01.4: WMA -- Noisy Sinusoid Smoothing

- **Priority**: P1
- **Function**: `apply_wma` (line 51)
- **Input**: `signal = sin(2pi * 0.05 * t) + N(0, 0.5)` (100 points), `window=11`.
  - window is even (11 is already odd)
- **Expected output**: Output has:
  1. Same length as input (100 points).
  2. Zero NaN values.
  3. Lower variance than input (signal is smoothed).
  4. Roughness (sum of squared second differences) < input roughness.
- **Verification**: `len(result) == 100`, `np.var(result) < np.var(signal)`, `np.all(np.isfinite(result))`.

---

### TC-DATA-01.5: ALMA -- Window=1

- **Priority**: P1
- **Function**: `apply_alma` (line 60)
- **Input**: `signal = [1,5,3,8,2]`, `window=1`.
  - `window % 2 == 1` (1 is odd), `m = 0 * 0.85 = 0`, `s = 1/6 = 0.1666`, `i = [0]`, `weights = [exp(0)] = [1]`, `convolve(signal, [1], mode="same")`
- **Expected output**: Same as input: `[1, 5, 3, 8, 2]`.
- **Verification**: `np.array_equal(result, signal)`.

---

### TC-DATA-01.6: ALL Filters -- Empty Array

- **Priority**: P1
- **Functions**: All 10 filter functions.
- **Input**: `signal = np.array([])`, `t = np.array([])`.
  - Use default params for each filter.
- **Expected output**: Empty array (same shape as input) OR exception gracefully caught. At minimum, each function should not crash with a system-level error (segfault, etc.). Filters that internally use `t[1]-t[0]` (kalman) may raise `IndexError` -- that is acceptable behavior as long as the caller handles it.
- **Verification**: Script wraps each in try/except, no unhandled crashes. `try: result = f(signal, t, **params) except Exception: pass`.

---

### TC-DATA-01.7: ALL Filters -- All-NaN Signal

- **Priority**: P1
- **Functions**: All 10 filter functions.
- **Input**: `signal = np.full(10, np.nan)`, `t = np.arange(10, dtype=float)`.
- **Expected output**: All functions should complete without throwing uncaught exception. NaN propagation is expected. Some filters (EMA, SMA, WMA) return NaN for all outputs (convolution of NaNs). Kalman may produce finite values after the first step since initiation uses `signal[0]` (NaN). This is acceptable -- the key requirement is no crash.
- **Verification**: Script: `try: result = f(signal, t, **params) except: fail`. Result may contain NaN.

---

### TC-DATA-01.8: Kalman -- Constant Signal, dt Extraction Robustness

- **Priority**: P2
- **Function**: `apply_kalman` (line 81)
- **Input**: `signal = [5.0, 0.5, ...]` -- the RNG seed test: constant velocity = 0, measurement = 5.0 with noise.
  - Constant signal `signal = np.full(50, 5.0)`, `t = np.arange(50, dtype=float)`.
  - Q=0.01, R=0.1. dt = 1.0 (from t[1]-t[0]).
- **Expected output**: With no process noise and constant measurements, Kalman gain should converge, and filtered output should approach 5.0. All values finite.
- **Verification**: `np.all(np.isfinite(result))`, `abs(result[-1] - 5.0) < 0.1`.

---

### TC-DATA-01.9: Butterworth -- Cutoff at Nyquist

- **Priority**: P2
- **Function**: `apply_butterworth` (line 106)
- **Input**: `signal = sin(2pi * 0.05 * t)` (100 points), `order=4`, `cutoff=50.0` (>= nyquist=0.5).
  - Line 109-110: `if cutoff >= nyquist: cutoff = nyquist * 0.99` => cutoff = 0.495.
- **Expected output**: No crash. Filtered output has same length as input. All values finite.
- **Verification**: `len(result) == len(signal)`, `np.all(np.isfinite(result))`.

---

### TC-DATA-01.10: Median -- Window=3 (minimal)

- **Priority**: P1
- **Function**: `apply_median` (line 120)
- **Input**: `signal = [1, 100, 2]` (impulse at center), `window=3` (auto odd, stays 3).
- **Expected output**: Median of [1, 100, 2] = 2. The impulse is completely removed. Output should be `[~1, 2, ~2]` depending on scipy.medfilt edge behavior.
- **Verification**: `result[1] == 2.0` (center element is median-filtered).

---

### TC-DATA-02.1: Schmitt Trigger -- v>0, a=0 (All Zero in Deadband)

- **Priority**: P0
- **Function**: `_schmitt_trigger` (line 577)
- **Input**:
  ```python
  n = 100
  v = np.ones(n) * 0.1           # constant positive velocity
  a = np.zeros(n)                # zero acceleration
  ewma_span = 20
  k_eps = 0.15
  sigma_min = 0.05
  ```
- **Expected output**:
  - `sig_t` all zeros (no entry: acceleration is within deadband, since `|a_i| = 0 < eps_t = k_eps * max(sigma_v, sigma_min)`).
  - `eps_t >= 0.0075` everywhere (from `k_eps * sigma_min = 0.15 * 0.05 = 0.0075`).
  - All arrays have length `n`.
- **Reasoning**: With v constant, sigma_v -> 0, so eps_t = k_eps * sigma_min = 0.0075. a=0 < 0.0075, so deadband holds. Both conditions (a>eps AND v>0) and (a<-eps AND v<0) are false.
- **Verification**: `np.all(schmitt["sig"] == 0)`.

---

### TC-DATA-02.2: Schmitt Trigger -- Acceleration > Threshold, v>0 (Long Trigger)

- **Priority**: P0
- **Function**: `_schmitt_trigger` (line 577)
- **Input**:
  ```python
  n = 100
  v = np.ones(n) * 0.1           # positive velocity
  a = np.ones(n) * 0.1           # positive acceleration above deadband
  ewma_span = 20
  k_eps = 0.15
  sigma_min = 0.05
  ```
- **Expected output**:
  - `sig_t[0]` = 0 (start state = 0, need time for EWMA to stabilize).
  - After EWMA stabilizes: sigma_v -> 0, so eps_t -> sigma_min * k_eps = 0.0075.
  - `a > eps_t` AND `v > 0` => state transitions to +1.
  - Eventually `sig_t[i] == 1` for all i after trigger time.
- **Verification**: `np.any(schmitt["sig"] == 1)` and the first occurrence is followed by sustained +1 (no premature flip to 0 since a never goes negative).

---

### TC-DATA-02.3: Schmitt Trigger -- Hysteresis Validation (Long to Neutral)

- **Priority**: P0
- **Function**: `_schmitt_trigger` (line 577)
- **Input**:
  ```python
  n = 150
  # Phase 1: trigger long (50 points)
  # Phase 2: acceleration drops to slightly negative, should NOT immediately exit
  # Phase 3: acceleration drops below -eps, should exit
  v = np.ones(n) * 0.1
  a = np.zeros(n)
  a[20:70] = 0.1      # acceleration > eps -> trigger long
  a[70:100] = -0.005  # slightly negative but still |a| < eps -> stay in +1 (hysteresis)
  a[100:130] = -0.02  # a < -eps -> exit to 0
  a[130:] = 0.0
  ewma_span = 20; k_eps = 0.15; sigma_min = 0.05
  ```
- **Expected output**:
  1. `sig_t[i] == 0` for i in [0, ~some index after 20] (deadband, or state=0 waiting for trigger).
  2. `sig_t[i] == 1` for the long phase.
  3. `sig_t[i] == 1` persists during Phase 2 (a=-0.005, which is NOT less than -eps). This is the **hysteresis** -- the trigger does NOT flip when a barely crosses zero below threshold.
  4. `sig_t[i] == 0` after Phase 3 exit threshold is crossed.
  5. Digging into the exact boundary: From line 622, when `current_state == 1`, the transition to 0 requires `a[i] < -eps_t[i]`. If `a=-0.005` and `eps=0.0075`, then `-0.005 > -0.0075`, so condition is false -- we stay in +1. This confirms hysteresis.
- **Verification**: `schmitt["sig"][80] == 1` (still long when a is -0.005,|a|<eps). Then later `schmitt["sig"][120] == 0` (exited when a=-0.02 < -eps).

---

### TC-DATA-02.4: Schmitt Trigger -- n < ewma_span Returns None

- **Priority**: P1
- **Function**: `_schmitt_trigger` (line 577)
- **Input**: `v = np.array([0.1, 0.2])`, `a = np.array([0.01, 0.02])`, `ewma_span=10`.
  - `n = 2 < ewma_span = 10` => returns None (line 587-588).
- **Expected output**: `return None` exactly.
- **Verification**: `result is None`.

---

### TC-DATA-02.5: Schmitt Trigger -- NaN Values in v/a

- **Priority**: P2
- **Function**: `_schmitt_trigger` (line 577)
- **Input**: `v` with one NaN, `a` all zero, sufficient length.
  ```python
  n = 100
  v = np.ones(n) * 0.1
  a = np.zeros(n)
  v[50] = np.nan   # NaN mid-series
  a[50] = np.nan   # NaN mid-series
  ```
- **Expected output**: When `a[i]` or `v[i]` is NaN, line 610: `sig_t[i] = current_state` (hold state). The signal should maintain the previous state instead of flipping spuriously. No crash.
- **Verification**: `schmitt["sig"][50] == schmitt["sig"][49]` (state held through NaN), `np.any(np.isnan(schmitt["sigma_v"]))` (sigma_v may propagate NaN) -- acceptable.

---

### TC-DATA-02.6: Schmitt Trigger -- v=0, a=0 (Stationary)

- **Priority**: P1
- **Function**: `_schmitt_trigger` (line 577)
- **Input**: `v = np.zeros(100)`, `a = np.zeros(100)`.
  - `sigma_v[0] = 0.0`. EWMA of constant zero = 0. So `eps_t = k_eps * max(0, sigma_min) = k_eps * sigma_min`.
- **Expected output**: All sig_t = 0. No false signals from stationary data.
- **Verification**: `np.all(schmitt["sig"] == 0)`.

---

### TC-DATA-03.1: _fit_parabolic -- Known Parabola

- **Priority**: P0
- **Function**: `_fit_parabolic` (line 684)
- **Input**:
  ```python
  x = np.array([0, 1, 2, 3, 4], dtype=float)
  y = 2 * x**2 - 3 * x + 5   # known coefficients: a=2, b=-3, c=5
  start, end = 0, 4
  ```
- **Expected output**:
  - `a == 2.0`, `b == -3.0`, `c == 5.0` (within float tolerance).
  - `y_fit` exactly equals `y` (noiseless data, polynomial degree matches).
- **Verification**: `np.isclose(result["a"], 2.0)` and `np.allclose(result["y_fit"], y)`.

---

### TC-DATA-03.2: _fit_parabolic -- Fewer Than 3 Points Returns None

- **Priority**: P1
- **Function**: `_fit_parabolic` (line 684)
- **Input**: `x = np.array([0, 1])`, `y = np.array([5, 7])`, `start=0, end=1`.
  - `len(x_seg) = 2 < 3` => returns None.
- **Expected output**: `None`.
- **Verification**: `result is None`.

---

### TC-DATA-03.3: _fit_physics_parabola -- Known Parabola Anchored at Endpoint

- **Priority**: P0
- **Function**: `_fit_physics_parabola` (line 696)
- **Input**:
  ```python
  x = np.array([0, 1, 2, 3, 4], dtype=float)
  y = 2 * (x - 4)**2 + 10    # vertex at x=4, y=10. y = 2x^2 -16x + 42
  start, end = 0, 4
  ```
  - `x0 = 4.0` (endpoint), `y0 = 10.0` (endpoint value).
  - `dt = [-4, -3, -2, -1, 0]`, `dt_sq = [16, 9, 4, 1, 0]`.
  - `dy = y - y0 = [42-10, 20-10, 10-10, 10-10, 0] = [32, 10, 0, 0, 0]`.
  - `dt_sq_squared = [256, 81, 16, 1, 0]`, `sum = 354`.
  - `dt_sq * dy = [16*32, 9*10, 4*0, 1*0, 0*0] = [512, 90, 0, 0, 0]`, `sum = 602`.
  - `a = 602 / 354 = 1.70056...` (approximately).
- **Expected output**: `a ~ 1.701`, `b = 0.0`, `c = 10.0`, `x0 = 4.0`. `y_fit` approximates the original parabola. Because this method anchors the endpoint as vertex, the fit is constrained and may not recover the exact `a=2`.
- **Verification**: `result is not None`, `np.isclose(result["x0"], 4.0)`, `np.isclose(result["c"], 10.0)`, `np.allclose(result["y_fit"][:3], y[:3], atol=1.0)` (approximate match far from vertex).

---

### TC-DATA-03.4: _fit_physics_parabola -- Collinear Data (denom -> 0)

- **Priority**: P1
- **Function**: `_fit_physics_parabola` (line 696)
- **Input**:
  ```python
  x = np.array([0, 1, 2], dtype=float)
  y = np.array([5, 5, 5])   # constant signal
  start, end = 0, 2
  ```
  - `x0 = 2.0`, `y0 = 5.0`. `dt = [-2, -1, 0]`, `dt_sq = [4, 1, 0]`, `dt_sq_sq = [16, 1, 0]`, `sum = 17`.
  - `dy = [0, 0, 0]`. `dt_sq * dy = [0, 0, 0]` => `a = 0 / 17 = 0`.
  - `denom = 17 > 1e-12` -- not zero. For actual collinear data where dt_sq values are all the same (impossible for distinct x). But if `dt_sq_sq` sums to something small: e.g., if there are only 2 unique x values left after clamping. With >= 3 distinct x, denom > 0.
  - A harder test: use collinear y = mx + b, endpoint in the middle so dt != 0 and dy != 0 but parabola has zero curvature. Expected `a ~ 0`.
- **Expected output**: `a` near 0 (flat parabola), `y_fit` approximates linear trend. Not None.
- **Verification**: `result is not None`, `np.isclose(result["a"], 0.0, atol=1e-10)`.

---

### TC-DATA-03.5: Comparison of physics vs poly2 on Same Data

- **Priority**: P2
- **Functions**: `_fit_parabolic` vs `_fit_physics_parabola` (lines 684, 696)
- **Input**:
  ```python
  x = np.array([0, 1, 2, 3, 4, 5], dtype=float)
  # Upward-opening parabola with vertex at x=2
  y = 0.5 * (x - 2)**2 + 3  # = 0.5x^2 -2x + 5
  start, end = 0, 5
  ```
- **Expected behavior**:
  - `_fit_parabolic` (poly2, 3-dof): fits a=0.5, b=-2.0, c=5.0 exactly (noiseless data of correct degree).
  - `_fit_physics_parabola` (physics, 1-dof): anchors vertex at `(x=5, y=7.5)`. Since the true vertex is at x=2, the fit is constrained and will produce a different parabola. `y_fit` will be different from the original, especially near the endpoint.
  - The physics method's prediction (extrapolation beyond x=5) will be symmetric about x=5, which differs from the poly2 extrapolation.
- **Verification**: Compare the two `y_fit` arrays -- they differ. Also compare extrapolation direction: for data whose true vertex is in the middle, extrapolating rightward from the endpoint may give different directional predictions between the two methods.

---

### TC-DATA-04.1: PnL -- Empty pairs Returns Initial Value

- **Priority**: P0
- **Function**: `_compute_strategy_pnl` (line 771)
- **Input**:
  ```python
  t = np.arange(5)
  filtered = np.array([100.0, 101.0, 102.0, 101.0, 100.0])
  sig_t = np.array([0, 0, 0, 0, 0])
  all_pairs = []        # no trading pairs
  pred_pairs = []
  stop_loss_pct = 2.0
  n_extend = 5
  ```
- **Expected output**: `long_pnl = [100, 100, 100, 100, 100]`, `short_pnl = [100, 100, 100, 100, 100]`, `trade_records = []`.
  - Line 803: `if len(all_pairs) == 0 or len(pred_pairs) == 0: return long_pnl, short_pnl, []`.
- **Verification**: `np.all(long_pnl == 100.0)`, `np.all(short_pnl == 100.0)`, `len(trade_records) == 0`.

---

### TC-DATA-04.2: PnL -- Known Long Trade with Take Profit

- **Priority**: P0
- **Function**: `_compute_strategy_pnl` (line 771)
- **Input**: Build a scenario where:
  1. A long pair exists (sig_t = 1 at pair_end).
  2. The prediction is upward (y_pred[-1] > y_pred[0]).
  3. Price goes up by 5%, then sig_t flips to -1 (take profit exit).
  ```python
  t = np.arange(20)
  filtered = np.array([100 + i*0.5 for i in range(20)])  # steady uptrend
  sig_t = np.array([0]*5 + [1]*10 + [-1]*5)  # long from index 5, flip at 15
  all_pairs = [(5, 15)]   # pair start=5, end=15
  # pred_pairs: simulate upward prediction
  pred_pairs = [{
      "fit_result": {"a": 0.01, "b": 0.5, "c": 102.5, "x0": 15.0},
      "fit_start": 5,
      "pair_end": 15,
  }]
  stop_loss_pct = 10.0  # wide, won't trigger
  n_extend = 5
  ```
  - Entry at index 15, entry_price = filtered[15] = 107.5.
  - Exit at index 15 too (because sig_t[15] = -1, checked at line 880: `if is_long and sig_t[i] == -1`). Actually `scan_end = n-1 = 19`, loop starts `entry_idx + 1 = 16`. At i=16, `sig_t[16] = -1` => exit at 16.
  - trade_return = (filtered[16] - 107.5) / 107.5 = (108.0 - 107.5) / 107.5 = 0.4651%.
  - long_pnl[15] = 100 (entry), long_pnl[16] = 100 * (1 + 0.004651) = 100.465.
- **Expected output**:
  - 1 trade record: type "long", exit_reason "take_profit".
  - long_pnl[15:] > 100, short_pnl all 100.
  - trade records contain the correct entry/exit indices and return_pct.
- **Verification**: `len(trade_records) == 1`, `trade_records[0]["type"] == "long"`, `trade_records[0]["exit_reason"] == "take_profit"`, `trade_records[0]["return_pct"] > 0`.

---

### TC-DATA-04.3: PnL -- Stop Loss Trigger in Protection Period

- **Priority**: P1
- **Function**: `_compute_strategy_pnl` (line 771)
- **Input**:
  ```python
  t = np.arange(20)
  # Price drops sharply after entry
  filtered = np.array([100 + i*0.5 for i in range(15)] + [105, 80, 75, 70, 65])
  sig_t = np.array([0]*5 + [1]*5 + [-1]*10)  # long pair end at 10... adjust
  # Simplified: pair ends at 10, entry_price = filtered[10], then price plummets
  # Let me be precise:
  filtered2 = np.array([100.0, 100.5, 101.0, 101.5, 102.0, 102.5, 103.0, 103.5, 104.0, 104.5,
                        105.0,  # entry at index 10
                         85.0, 85.0, 85.0, 85.0, 85.0, 85.0, 85.0, 85.0, 85.0])
  sig_t2 = np.array([0]*5 + [1]*5 + [1, -1, -1, -1, -1, -1, -1, -1, -1, -1])
  all_pairs2 = [(5, 10)]   # pair covers indices 5-10
  pred_pairs2 = [{
      "fit_result": {"a": 0.0, "b": 1.0, "c": 95.0, "x0": 10.0},
      "fit_start": 5,
      "pair_end": 10,
  }]
  stop_loss_pct = 5.0  # 5% stop loss
  n_extend = 3
  ```
  - Entry at index 10, price = 105.0. Protection period ends at 10 + 3 = 13.
  - At i=11, price=85. Check stop: `stop_hit = cur_price < pred_val * (1 - 5/100)`. pred_val at i=11 when x0=10 is `0.0*(1)^2 + 1.0*(1) + 95 = 96`. Check: `85 < 96 * 0.95 = 91.2` => True => stop loss triggered at index 11.
  - exit_reason = "stop_loss".
  - trade_return = (85 - 105) / 105 = -19.0476%.
- **Expected output**: 1 trade record, exit_reason "stop_loss", exit_idx=11, return_pct negative.
- **Verification**: `trade_records[0]["exit_reason"] == "stop_loss"`, `trade_records[0]["exit_idx"] == 11`.

---

### TC-DATA-04.4: PnL -- Sequence of Trades (Long then Short)

- **Priority**: P2
- **Function**: `_compute_strategy_pnl` (line 771)
- **Input**: Two consecutive pairs: long then short. Verify that short_capital is independent of long_pnl.
  ```python
  n = 30
  t = np.arange(n)
  filtered = np.concatenate([np.linspace(100, 110, 10), np.linspace(110, 105, 10), np.linspace(105, 115, 10)])
  sig_t = np.array([0]*3 + [1]*7 + [0]*3 + [-1]*7 + [0]*10)
  all_pairs = [(3, 10), (13, 20)]
  pred_pairs = [
      {"fit_result": {"a": 0, "b": 0.5, "c": 97.5, "x0": 10.0}, "fit_start": 3, "pair_end": 10},
      {"fit_result": {"a": 0, "b": -0.3, "c": 108.0, "x0": 20.0}, "fit_start": 13, "pair_end": 20},
  ]
  stop_loss_pct = 10.0
  n_extend = 5
  ```
  - Trade 1 (long at 10): entry_price = filtered[10] = 110.
  - Trade 2 (short at 20): entry_price = filtered[20] = 105. Short_capital starts at 100, independent of long_pnl's result.
  - If long trade earned +x%, long_capital = 100*(1+x). Short_capital is still 100.
- **Expected output**: 2 trade records. short_pnl does not incorporate long_trade returns (independent PnL traces per strategy doc).
- **Verification**: `len(trade_records) == 2`, `long_pnl[20] != short_pnl[20]` generally (they are independent).

---

### TC-DATA-04.5: PnL -- Extreme Stop Loss (0.5% and 1000%)

- **Priority**: P2
- **Function**: `_compute_strategy_pnl` (line 771)
- **Input**: Same as TC-DATA-04.2 but with `stop_loss_pct=0.5` (very tight).
  - The stop check: `stop_hit = cur_price < pred_val * (1 - 0.5/100) = pred_val * 0.995`.
  - With a price uptrend and prediction following closely, the stop may trigger on normal fluctuations.
- **Expected output**: Either the stop triggers very quickly (tight stop), or the trade exits normally. The function should not crash with extreme stop_loss values.
  - Also test `stop_loss_pct=1000.0` -- effectively infinite, trade should never stop-loss.
- **Verification**: No crash. `exit_reason` reflects whatever actually triggered first.

---

### TC-DATA-05.1: Time Alignment -- HKT Intraday + Naive Daily

- **Priority**: P0
- **Function**: `_align_pnl_to_current_tf` (line 969)
- **Input**:
  ```python
  # HKT intraday data (tz-aware)
  hkt_dates = pd.DatetimeIndex([
      "2024-01-02 09:30:00", "2024-01-02 10:00:00",
      "2024-01-03 09:30:00", "2024-01-03 10:00:00",
  ], tz="Asia/Hong_Kong")
  higher_pnl_long = np.array([100.0, 100.5, 101.0, 102.0])
  higher_pnl_short = np.array([100.0, 100.0, 99.5, 99.0])
  higher_trades = [
      {"entry_idx": 0, "exit_idx": 2, "type": "long", "return_pct": 1.0, "exit_reason": "take_profit"},
  ]
  
  # Naive daily data (no timezone)
  daily_dates = pd.DatetimeIndex([
      "2024-01-02", "2024-01-03", "2024-01-04",
  ])
  ```
- **Expected output**:
  - `_normalize_dates` (lines 1000-1005) strips tz from HKT data: `["2024-01-02 09:30", "2024-01-02 10:00", "2024-01-03 09:30", "2024-01-03 10:00"]`.
  - Daily dates: `["2024-01-02", "2024-01-03", "2024-01-04"]`.
  - For i=0 (daily "2024-01-02"): `mask = hd <= cd[0]` => first 2 HKT points. `j = max where mask = 1` => j=1. `aligned_long[0] = 100.5`, `aligned_short[0] = 100.0`.
  - For i=1 (daily "2024-01-03"): `mask = hd <= cd[1]` => all 4 points. j=3. `aligned_long[1] = 102.0`, `aligned_short[1] = 99.0`.
  - For i=2 (daily "2024-01-04"): no mask (no HKT point <= Jan 4), skip. `aligned_long[2] = NaN`, `aligned_short[2] = NaN`.
  - Entry marker: entry_j=0, entry_time = "2024-01-02 09:30". `entry_mask = cd <= entry_time` => only "2024-01-02" <= "2024-01-02 09:30" (datetime comparison). `entry_bar = 0`.
  - Exit marker: exit_j=2, exit_time = "2024-01-03 09:30". `exit_mask = cd <= exit_time` => "2024-01-02" and "2024-01-03". `exit_bar = 1`.
- **Verification**:
  - `aligned_long[0] == 100.5`, `aligned_long[1] == 102.0`, `np.isnan(aligned_long[2])`.
  - `entry_markers[0] == (0, "long", 100.5)`.
  - `exit_markers[0][0] == 1` (exit bar index), `exit_markers[0][1] == "long"`.

---

### TC-DATA-05.2: Time Alignment -- No Temporal Overlap

- **Priority**: P1
- **Function**: `_align_pnl_to_current_tf` (line 969)
- **Input**:
  ```python
  higher_dates = pd.DatetimeIndex(["2020-01-01", "2020-01-02"])  # old data
  higher_pnl_long = np.array([100.0, 101.0])
  higher_pnl_short = np.array([100.0, 99.0])
  higher_trades = [{"entry_idx": 0, "exit_idx": 1, "type": "long", "return_pct": 1.0, "exit_reason": "take_profit"}]
  
  current_dates = pd.DatetimeIndex(["2024-01-01", "2024-01-02"])  # new data, no overlap
  ```
- **Expected output**: `aligned_long = [NaN, NaN]`, `aligned_short = [NaN, NaN]`, empty markers.
  - Mask check: for i=0, `hd <= cd[0]` => `"2020-01-01" <= "2024-01-01"` => True. Wait, this DOES match. The comparison works because 2020 < 2024.
  - For a true no-overlap test:
  ```python
  higher_dates = pd.DatetimeIndex(["2024-06-01", "2024-06-02"])
  current_dates = pd.DatetimeIndex(["2024-01-01"])
  ```
  - i=0: `mask = "2024-06-01" <= "2024-01-01"` => False. No mask, skip. aligned = NaN.
- **Verification**: All aligned values are NaN, markers are empty.

---

### TC-DATA-05.3: Time Alignment -- Higher PnL Shorter than Current

- **Priority**: P2
- **Function**: `_align_pnl_to_current_tf` (line 969)
- **Input**: higher_dates = 2 points, current_dates = 10 points. The higher PnL only covers the first 2 days of a 10-day window.
- **Expected output**: First 2 positions get PnL values, remaining 8 positions get NaN (no mask found for later dates). `entry_markers`/`exit_markers` should be within bounds.
- **Verification**: `np.sum(~np.isnan(aligned_long)) <= len(higher_dates)` (at most 2 non-NaN entries).

---

### TC-DATA-06.1: _find_all_pairs -- Empty Signal

- **Priority**: P1
- **Function**: `_find_all_pairs` (line 638)
- **Input**: `sig_t = np.array([])`.
  - Line 643: `n = 0 < 3` => returns `[]`.
- **Expected output**: `[]`.
- **Verification**: `result == []`.

---

### TC-DATA-06.2: _find_all_pairs -- No Non-Zero Entries

- **Priority**: P1
- **Function**: `_find_all_pairs` (line 638)
- **Input**: `sig_t = np.zeros(10)`.
- **Expected output**: `[]` (no segments to pair).
- **Verification**: `result == []`.

---

### TC-DATA-06.3: _find_all_pairs -- Single Segment (Unpaired)

- **Priority**: P1
- **Function**: `_find_all_pairs` (line 638)
- **Input**: `sig_t = np.array([0, 0, 1, 1, 1, 0, 0])`.
  - Single segment of +1 from index 2-4. Only 1 segment, line 661: `if len(segments) < 2: return []`.
- **Expected output**: `[]`.
- **Verification**: `result == []`.

---

### TC-DATA-06.4: _find_all_pairs -- Long-Short Pair (Sparse Signal)

- **Priority**: P1
- **Function**: `_find_all_pairs` (line 638)
- **Input**: `sig_t = np.array([0,1,0,0,-1,0])`.
  - Segments: [(1,1,+1), (4,4,-1)]. 2 segments, v1 != v2, so pair = [(1, 4)].
- **Expected output**: `[(1, 4)]`.
- **Verification**: `result == [(1, 4)]`.

---

### TC-DATA-06.5: _find_all_pairs -- Merging Adjacent Same-Sign Segments

- **Priority**: P1
- **Function**: `_find_all_pairs` (line 638)
- **Input**: `sig_t = np.array([0,1,0,1,0,0,-1])`.
  - Segments: [(1,1,+1), (3,3,+1), (6,6,-1)].
  - Merged: [(1,3,+1), (6,6,-1)] (first two +1 segments merged with neutral gap).
  - Pair: [(1, 6)] (opposite signs).
- **Expected output**: `[(1, 6)]`.
- **Verification**: `result == [(1, 6)]`.

---

### TC-DATA-06.6: _fit_parabolic -- Collinear Data

- **Priority**: P2
- **Function**: `_fit_parabolic` (line 684)
- **Input**: `x = [0,1,2,3,4]`, `y = [1,2,3,4,5]` (perfectly collinear, y = x + 1).
  - `np.polyfit(x, y, 2)` => `a ~ 0, b ~ 1, c ~ 1`.
- **Expected output**: `a` near 0 (quadratic coefficient), `b` near 1, `c` near 1. `y_fit` reproduces y.
- **Verification**: `np.isclose(result["a"], 0.0, atol=1e-10)`, `np.isclose(result["b"], 1.0)`, `np.isclose(result["c"], 1.0)`.

---

### TC-DATA-06.7: _compute_strategy_pnl -- All-NaN Filtered

- **Priority**: P2
- **Function**: `_compute_strategy_pnl` (line 771)
- **Input**:
  ```python
  t = np.arange(20)
  filtered = np.full(20, np.nan)
  sig_t = np.array([0]*5 + [1]*5 + [-1]*10)  # has pairs
  all_pairs = [(5, 10)]   # long pair
  pred_pairs = [{"fit_result": {"a": 0.1, "b": 1.0, "c": 100.0, "x0": 10.0}, "fit_start": 5, "pair_end": 10}]
  stop_loss_pct = 2.0
  n_extend = 5
  ```
  - entry_price = filtered[10] = NaN => line 845-846: `if np.isnan(entry_price) or entry_price <= 0: continue`.
- **Expected output**: No trades. long_pnl = short_pnl = all 100.0. (Front-fill loop: last_val=100 stays 100 throughout because long_pnl always equals 100).
- **Verification**: `np.all(long_pnl == 100.0)`, `len(trade_records) == 0`.

---

### TC-DATA-06.8: _align_pnl_to_current_tf -- Empty higher_dates

- **Priority**: P1
- **Function**: `_align_pnl_to_current_tf` (line 969)
- **Input**:
  ```python
  higher_dates = pd.DatetimeIndex([])
  higher_pnl_long = np.array([])
  higher_pnl_short = np.array([])
  higher_trades = []
  current_dates = pd.DatetimeIndex(["2024-01-01", "2024-01-02"])
  ```
- **Expected output**: `aligned_long = [NaN, NaN]`, `aligned_short = [NaN, NaN]`, empty markers.
  - Line 994: `if higher_dates is None or len(higher_dates) == 0: return {...}`.
- **Verification**: `np.all(np.isnan(result["aligned_long"]))`, `len(result["entry_markers"]) == 0`.

---

## Summary Table

| TC-ID | Category | Function | Priority | Key Boundary |
|-------|----------|----------|----------|-------------|
| TC-DATA-01.1 | Filter | apply_sma | P0 | Constant signal |
| TC-DATA-01.2 | Filter | apply_sma | P1 | Window > len |
| TC-DATA-01.3 | Filter | apply_ema | P0 | Constant signal |
| TC-DATA-01.4 | Filter | apply_wma | P1 | Noisy sinusoid smoothing |
| TC-DATA-01.5 | Filter | apply_alma | P1 | Window=1 |
| TC-DATA-01.6 | Filter | All | P1 | Empty array |
| TC-DATA-01.7 | Filter | All | P1 | All-NaN signal |
| TC-DATA-01.8 | Filter | apply_kalman | P2 | Constant signal convergence |
| TC-DATA-01.9 | Filter | apply_butterworth | P2 | Cutoff at Nyquist |
| TC-DATA-01.10 | Filter | apply_median | P1 | Window=3 impulse removal |
| TC-DATA-02.1 | Schmitt | _schmitt_trigger | P0 | v>0, a=0 (deadband) |
| TC-DATA-02.2 | Schmitt | _schmitt_trigger | P0 | a>eps, v>0 (trigger long) |
| TC-DATA-02.3 | Schmitt | _schmitt_trigger | P0 | Hysteresis validation |
| TC-DATA-02.4 | Schmitt | _schmitt_trigger | P1 | n < ewma_span |
| TC-DATA-02.5 | Schmitt | _schmitt_trigger | P2 | NaN in v/a |
| TC-DATA-02.6 | Schmitt | _schmitt_trigger | P1 | v=0, a=0 (stationary) |
| TC-DATA-03.1 | Fit | _fit_parabolic | P0 | Known parabola |
| TC-DATA-03.2 | Fit | _fit_parabolic | P1 | <3 points |
| TC-DATA-03.3 | Fit | _fit_physics_parabola | P0 | Known parabola anchored |
| TC-DATA-03.4 | Fit | _fit_physics_parabola | P1 | Collinear data |
| TC-DATA-03.5 | Fit | Both | P2 | Cross-method comparison |
| TC-DATA-04.1 | PnL | _compute_strategy_pnl | P0 | Empty pairs |
| TC-DATA-04.2 | PnL | _compute_strategy_pnl | P0 | Known long trade, take profit |
| TC-DATA-04.3 | PnL | _compute_strategy_pnl | P1 | Stop loss in protection period |
| TC-DATA-04.4 | PnL | _compute_strategy_pnl | P2 | Sequence of trades, independent capital |
| TC-DATA-04.5 | PnL | _compute_strategy_pnl | P2 | Extreme stop loss values |
| TC-DATA-05.1 | Align | _align_pnl_to_current_tf | P0 | HKT tz-aware + naive daily |
| TC-DATA-05.2 | Align | _align_pnl_to_current_tf | P1 | No temporal overlap |
| TC-DATA-05.3 | Align | _align_pnl_to_current_tf | P2 | Higher PnL shorter than current |
| TC-DATA-06.1 | Boundary | _find_all_pairs | P1 | Empty signal |
| TC-DATA-06.2 | Boundary | _find_all_pairs | P1 | All zeros |
| TC-DATA-06.3 | Boundary | _find_all_pairs | P1 | Single segment |
| TC-DATA-06.4 | Boundary | _find_all_pairs | P1 | Long-short sparse pair |
| TC-DATA-06.5 | Boundary | _find_all_pairs | P1 | Merging same-sign segments |
| TC-DATA-06.6 | Boundary | _fit_parabolic | P2 | Collinear data |
| TC-DATA-06.7 | Boundary | _compute_strategy_pnl | P2 | All-NaN filtered |
| TC-DATA-06.8 | Boundary | _align_pnl_to_current_tf | P1 | Empty higher_dates |
