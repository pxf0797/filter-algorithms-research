#!/usr/bin/env node
/**
 * verify_filters.js — Unit tests for filter functions extracted from index.html
 *
 * Extracts the core filter implementations and tests them with known inputs.
 * Run: node verify_filters.js
 */

// ============================================================
// Helper (matching index.html)
// ============================================================
function ensureOdd(v) {
  const n = Math.round(v);
  return n % 2 === 0 ? n + 1 : n;
}

// ============================================================
// Filter Implementations (from index.html)
// ============================================================

// 1. SMA
function smaFilter(signal, params) {
  const w = ensureOdd(params.window);
  const half = (w - 1) / 2;
  const n = signal.length;
  const result = new Array(n);
  for (let i = 0; i < n; i++) {
    if (i < half || i >= n - half) { result[i] = NaN; continue; }
    let sum = 0;
    for (let j = -half; j <= half; j++) sum += signal[i + j];
    result[i] = sum / w;
  }
  return result;
}

// 2. EMA
function emaFilter(signal, params) {
  const span = Math.max(2, Math.round(params.span));
  const alpha = 2 / (span + 1);
  const n = signal.length;
  const result = new Array(n);
  result[0] = signal[0];
  for (let i = 1; i < n; i++) {
    result[i] = alpha * signal[i] + (1 - alpha) * result[i - 1];
  }
  return result;
}

// 3. WMA
function wmaFilter(signal, params) {
  const w = ensureOdd(params.window);
  const half = (w - 1) / 2;
  const n = signal.length;
  const weights = new Array(w);
  for (let j = 0; j < w; j++) weights[j] = j + 1;
  const wSum = w * (w + 1) / 2;
  const result = new Array(n);
  for (let i = 0; i < n; i++) {
    if (i < half || i >= n - half) { result[i] = NaN; continue; }
    let sum = 0;
    for (let j = 0; j < w; j++) sum += signal[i - half + j] * weights[j];
    result[i] = sum / wSum;
  }
  return result;
}

// 4. ALMA
function almaFilter(signal, params) {
  const w = ensureOdd(params.window);
  const offset = Math.min(1, Math.max(0, params.offset));
  const sigmaVal = Math.max(0.5, params.sigma);
  const n = signal.length;
  const center = (w - 1) * (1 - offset);
  const sigma = w / sigmaVal;
  const weights = new Array(w);
  let weightSum = 0;
  for (let j = 0; j < w; j++) {
    weights[j] = Math.exp(-0.5 * Math.pow((j - center) / sigma, 2));
    weightSum += weights[j];
  }
  for (let j = 0; j < w; j++) weights[j] /= weightSum;
  const half = (w - 1) / 2;
  const result = new Array(n);
  for (let i = 0; i < n; i++) {
    if (i < half || i >= n - half) { result[i] = NaN; continue; }
    let sum = 0;
    for (let j = 0; j < w; j++) sum += signal[i - half + j] * weights[j];
    result[i] = sum;
  }
  return result;
}

// 5. Savitzky-Golay
function savgolCoeffs(windowLen, order) {
  const m = (windowLen - 1) / 2;
  const x = new Array(windowLen);
  for (let i = 0; i < windowLen; i++) x[i] = i - m;

  const rows = windowLen, cols = order + 1;
  const X = new Array(rows);
  for (let i = 0; i < rows; i++) {
    X[i] = new Array(cols);
    for (let j = 0; j < cols; j++) X[i][j] = Math.pow(x[i], j);
  }

  function matMul(A, B) {
    const ar = A.length, ac = A[0].length, bc = B[0].length;
    const C = new Array(ar);
    for (let i = 0; i < ar; i++) {
      C[i] = new Array(bc).fill(0);
      for (let k = 0; k < ac; k++) {
        const aik = A[i][k];
        if (aik === 0) continue;
        for (let j = 0; j < bc; j++) C[i][j] += aik * B[k][j];
      }
    }
    return C;
  }
  function matT(A) {
    const r = A.length, c = A[0].length;
    const T = new Array(c);
    for (let j = 0; j < c; j++) {
      T[j] = new Array(r);
      for (let i = 0; i < r; i++) T[j][i] = A[i][j];
    }
    return T;
  }

  const Xt = matT(X);
  const XtX = matMul(Xt, X);

  const size = cols;
  const inv = new Array(size);
  for (let i = 0; i < size; i++) {
    inv[i] = new Array(size).fill(0);
    inv[i][i] = 1;
  }
  const aug = new Array(size);
  for (let i = 0; i < size; i++) {
    aug[i] = XtX[i].slice();
    for (let j = 0; j < size; j++) aug[i][j + size] = inv[i][j];
  }

  for (let col = 0; col < size; col++) {
    let maxRow = col;
    for (let row = col + 1; row < size; row++) {
      if (Math.abs(aug[row][col]) > Math.abs(aug[maxRow][col])) maxRow = row;
    }
    if (maxRow !== col) { const tmp = aug[col]; aug[col] = aug[maxRow]; aug[maxRow] = tmp; }
    const pivot = aug[col][col];
    if (Math.abs(pivot) < 1e-15) return null;
    for (let j = col; j < 2 * size; j++) aug[col][j] /= pivot;
    for (let row = 0; row < size; row++) {
      if (row === col) continue;
      const factor = aug[row][col];
      for (let j = col; j < 2 * size; j++) aug[row][j] -= factor * aug[col][j];
    }
  }

  const XtXinv = new Array(size);
  for (let i = 0; i < size; i++) XtXinv[i] = aug[i].slice(size);

  const pinv = matMul(XtXinv, Xt);
  return pinv[0];
}

function savgolFilter(signal, params) {
  const w = ensureOdd(params.window);
  const order = Math.min(5, Math.max(2, Math.round(params.order)));
  if (order >= w) return signal.map(() => NaN);
  const coeffs = savgolCoeffs(w, order);
  if (!coeffs) return signal.map(() => NaN);
  const half = (w - 1) / 2;
  const n = signal.length;
  const result = new Array(n);
  for (let i = 0; i < n; i++) {
    if (i < half || i >= n - half) { result[i] = NaN; continue; }
    let sum = 0;
    for (let j = 0; j < w; j++) sum += signal[i - half + j] * coeffs[j];
    result[i] = sum;
  }
  return result;
}

// 6. Kalman
function kalmanFilter(signal, params) {
  const R = Math.max(0.001, params.R);
  const Q = Math.max(0.0001, params.Q);
  const n = signal.length;
  const result = new Array(n);
  if (n === 0) return result;
  let x = signal[0];
  let dx = 0;
  let P = [[1, 0], [0, 1]];

  const H = [1, 0];

  function mat2Mul(A, B) {
    return [
      [A[0][0]*B[0][0] + A[0][1]*B[1][0], A[0][0]*B[0][1] + A[0][1]*B[1][1]],
      [A[1][0]*B[0][0] + A[1][1]*B[1][0], A[1][0]*B[0][1] + A[1][1]*B[1][1]]
    ];
  }
  function mat2Add(A, B) {
    return [[A[0][0]+B[0][0], A[0][1]+B[0][1]], [A[1][0]+B[1][0], A[1][1]+B[1][1]]];
  }

  result[0] = signal[0];
  for (let i = 1; i < n; i++) {
    const dt = 1;
    const F = [[1, dt], [0, 1]];
    const Ft = [[1, 0], [dt, 1]];

    x = x + dx * dt;
    let FP = mat2Mul(F, P);
    let FPFT = mat2Mul(FP, Ft);
    const Qm = [[Q * dt*dt*dt / 3, Q * dt*dt / 2], [Q * dt*dt / 2, Q * dt]];
    P = mat2Add(FPFT, Qm);

    const HP = [P[0][0] * H[0] + P[0][1] * H[1], P[1][0] * H[0] + P[1][1] * H[1]];
    const S = HP[0] * H[0] + HP[1] * H[1] + R;
    const K = [HP[0] / S, HP[1] / S];

    const innovation = signal[i] - x;
    x = x + K[0] * innovation;
    dx = dx + K[1] * innovation;

    const KH = [[K[0] * H[0], K[0] * H[1]], [K[1] * H[0], K[1] * H[1]]];
    const IminusKH = [[1 - KH[0][0], -KH[0][1]], [-KH[1][0], 1 - KH[1][1]]];
    P = mat2Mul(IminusKH, P);

    result[i] = x;
  }
  return result;
}

// 7. Butterworth
function butterworthFilter(signal, params) {
  const order = Math.max(1, Math.min(8, Math.round(params.order)));
  const cutoff = Math.max(0.5, Math.min(49, params.cutoff));
  const sampleRate = 100;
  const w0 = Math.tan(Math.PI * cutoff / sampleRate);
  const a = (1 - w0) / (1 + w0);
  const n = signal.length;
  if (n < 2) return signal.slice();

  let result = signal.slice();
  for (let k = 0; k < order; k++) {
    const inp = result.slice();
    result[0] = inp[0];
    for (let i = 1; i < n; i++) {
      result[i] = a * result[i - 1] + (1 - a) * (inp[i] + inp[i - 1]) / 2;
    }
  }

  let rev = new Array(n);
  for (let i = 0; i < n; i++) rev[i] = result[n - 1 - i];
  for (let k = 0; k < order; k++) {
    const inp = rev.slice();
    rev[0] = inp[0];
    for (let i = 1; i < n; i++) {
      rev[i] = a * rev[i - 1] + (1 - a) * (inp[i] + inp[i - 1]) / 2;
    }
  }

  for (let i = 0; i < n; i++) result[i] = rev[n - 1 - i];
  return result;
}

// 8. Gaussian
function gaussianFilter(signal, params) {
  const sigma = Math.max(0.1, params.sigma);
  const radius = Math.ceil(3 * sigma);
  const kernelSize = 2 * radius + 1;
  const kernel = new Array(kernelSize);
  let kSum = 0;
  for (let j = 0; j < kernelSize; j++) {
    const d = j - radius;
    kernel[j] = Math.exp(-0.5 * (d * d) / (sigma * sigma));
    kSum += kernel[j];
  }
  for (let j = 0; j < kernelSize; j++) kernel[j] /= kSum;
  const n = signal.length;
  const result = new Array(n);
  for (let i = 0; i < n; i++) {
    if (i < radius || i >= n - radius) { result[i] = NaN; continue; }
    let sum = 0;
    for (let j = 0; j < kernelSize; j++) sum += signal[i - radius + j] * kernel[j];
    result[i] = sum;
  }
  return result;
}

// 9. Median
function medianFilter(signal, params) {
  const w = ensureOdd(params.window);
  const half = (w - 1) / 2;
  const n = signal.length;
  const result = new Array(n);
  const buf = new Array(w);
  for (let i = 0; i < n; i++) {
    if (i < half || i >= n - half) { result[i] = NaN; continue; }
    for (let j = 0; j < w; j++) buf[j] = signal[i - half + j];
    buf.sort((a, b) => a - b);
    result[i] = buf[half];
  }
  return result;
}

// 10. LOWESS
function lowessFilter(signal, params) {
  const frac = Math.min(1, Math.max(0.02, params.frac));
  const n = signal.length;
  const result = new Array(n);
  const nNeighbors = Math.max(2, Math.round(frac * n));

  for (let i = 0; i < n; i++) {
    const dist = new Array(n);
    for (let j = 0; j < n; j++) dist[j] = { idx: j, d: Math.abs(j - i) };
    dist.sort((a, b) => a.d - b.d);
    const neighbors = dist.slice(0, nNeighbors);
    const maxDist = neighbors[neighbors.length - 1].d;
    if (maxDist === 0) {
      result[i] = signal[i];
      continue;
    }

    const xVals = new Array(nNeighbors);
    const yVals = new Array(nNeighbors);
    const wts = new Array(nNeighbors);
    for (let k = 0; k < nNeighbors; k++) {
      const d = neighbors[k].d / maxDist;
      wts[k] = Math.pow(1 - Math.pow(Math.abs(d), 3), 3);
      xVals[k] = neighbors[k].idx;
      yVals[k] = signal[neighbors[k].idx];
    }

    let S0 = 0, S1 = 0, S2 = 0, T0 = 0, T1 = 0;
    for (let k = 0; k < nNeighbors; k++) {
      const w = wts[k];
      const xk = xVals[k];
      const yk = yVals[k];
      S0 += w;
      S1 += w * xk;
      S2 += w * xk * xk;
      T0 += w * yk;
      T1 += w * xk * yk;
    }
    const den = S0 * S2 - S1 * S1;
    if (Math.abs(den) < 1e-15) {
      result[i] = signal[i];
      continue;
    }
    const aCoeff = (S2 * T0 - S1 * T1) / den;
    const bCoeff = (S0 * T1 - S1 * T0) / den;
    result[i] = aCoeff + bCoeff * i;
  }

  return result;
}

// ============================================================
// Test Framework
// ============================================================
let passed = 0;
let failed = 0;
const EPSILON = 1e-10;

function arraysMatch(a, b, tol) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const ai = a[i], bi = b[i];
    if (Number.isNaN(ai) && Number.isNaN(bi)) continue;
    if (Math.abs(ai - bi) > tol) return false;
  }
  return true;
}

function assert(condition, msg) {
  if (condition) {
    console.log(`  PASS: ${msg}`);
    passed++;
  } else {
    console.log(`  FAIL: ${msg}`);
    failed++;
  }
}

function assertArray(actual, expected, msg, tol) {
  tol = tol || EPSILON;
  if (arraysMatch(actual, expected, tol)) {
    console.log(`  PASS: ${msg}`);
    passed++;
  } else {
    console.log(`  FAIL: ${msg}`);
    console.log(`    Expected: ${JSON.stringify(expected)}`);
    console.log(`    Actual:   ${JSON.stringify(actual)}`);
    failed++;
  }
}

function summary() {
  console.log(`\n=== Results: ${passed} passed, ${failed} failed ===`);
  return failed === 0;
}

// ============================================================
// Tests
// ============================================================

// --- SMA Tests ---
console.log('\n--- SMA Tests ---');

// Constant signal: SMA(window=3) should return NaN,1,1,1,...,1,NaN for 10-element constant signal of 1s
const CONST_10 = [1,1,1,1,1,1,1,1,1,1];
const sma3_const = smaFilter(CONST_10, { window: 3 });
assertArray(sma3_const,
  [NaN, 1, 1, 1, 1, 1, 1, 1, 1, NaN],
  'SMA(3) on constant [1]*10 → all 1s, NaN at edges',
  1e-10);

// Linear ramp [0,1,2,3,4,5,6,7,8,9] with SMA(3)
const RAMP = [0,1,2,3,4,5,6,7,8,9];
const sma3_ramp = smaFilter(RAMP, { window: 3 });
const expectedSma3 = [NaN, 1, 2, 3, 4, 5, 6, 7, 8, NaN];
assertArray(sma3_ramp, expectedSma3,
  'SMA(3) on linear ramp [0..9] → [NaN, 1, 2, ..., 8, NaN]',
  1e-10);

// SMA(window=1) → ensureOdd makes it window=1, half=0, so all values should be the input itself
const sma1 = smaFilter(RAMP, { window: 1 });
assertArray(sma1, RAMP, 'SMA(1) is identity', 1e-10);

// --- EMA Tests ---
console.log('\n--- EMA Tests ---');

// EMA with alpha=1 (span=1, but span min is 2, so span=2 => alpha=2/3)
// Actually, alpha=1.0 means span=1, but span is clamped to min=2, alpha=2/3
// So to get exact tracking, we need to understand the clamping
// With span=2 → alpha=2/3, not 1.0
// Let's test: EMA with span=100 (very small alpha, very smooth)
const ema_span100 = emaFilter(CONST_10, { span: 100 });
assertArray(ema_span100, CONST_10,
  'EMA(span=100) on constant signal → all 1.0',
  1e-10);

// EMA starting value equals first signal value
assert(Math.abs(ema_span100[0] - 1) < 1e-10,
  'EMA(span=100) first value = first signal value');

// --- WMA Tests ---
console.log('\n--- WMA Tests ---');

// WMA(3) on constant → all 1s
const wma3_const = wmaFilter(CONST_10, { window: 3 });
assertArray(wma3_const,
  [NaN, 1, 1, 1, 1, 1, 1, 1, 1, NaN],
  'WMA(3) on constant [1]*10 → all 1s, NaN at edges',
  1e-10);

// WMA(3) on linear ramp:
// weights: [1,2,3], wSum=6, half=1
// i=1: [0*1 + 1*2 + 2*3]/6 = 8/6 = 1.333...
// i=2: [1*1 + 2*2 + 3*3]/6 = 14/6 = 2.333...
const wma3_ramp = wmaFilter(RAMP, { window: 3 });
assert(Math.abs(wma3_ramp[1] - (0*1 + 1*2 + 2*3)/6) < 1e-10,
  'WMA(3) on ramp[0,1,2] at i=1 → 8/6 = 1.333...');
assert(Math.abs(wma3_ramp[2] - (1*1 + 2*2 + 3*3)/6) < 1e-10,
  'WMA(3) on ramp[1,2,3] at i=2 → 14/6 = 2.333...');

// --- ALMA Tests ---
console.log('\n--- ALMA Tests ---');

// ALMA on constant → constant (weights sum to 1)
const alma_const = almaFilter(CONST_10, { window: 5, offset: 0.85, sigma: 6 });
for (let i = 2; i < 8; i++) {
  assert(Math.abs(alma_const[i] - 1) < 1e-10,
    `ALMA on constant at index ${i} → 1.0`);
}

// --- Savitzky-Golay Tests ---
console.log('\n--- Savitzky-Golay Tests ---');

// Savgol on constant → constant
const savgol_const = savgolFilter(CONST_10, { window: 5, order: 3 });
for (let i = 2; i < 8; i++) {
  assert(Math.abs(savgol_const[i] - 1) < 1e-10,
    `Savitzky-Golay on constant at index ${i} → 1.0`);
}

// Savgol on linear ramp: a degree-1 polynomial should be exactly reconstructed by order>=1
const savgol_ramp = savgolFilter(RAMP, { window: 5, order: 2 });
for (let i = 2; i < 8; i++) {
  assert(Math.abs(savgol_ramp[i] - RAMP[i]) < 1e-10,
    `Savitzky-Golay(order=2) on linear ramp at index ${i} → ${RAMP[i]}`);
}

// --- Kalman Tests ---
console.log('\n--- Kalman Tests ---');

// Kalman on constant → should converge to constant
const kalman_const = kalmanFilter(CONST_10, { R: 0.001, Q: 0.0001 });
assert(Math.abs(kalman_const[0] - 1) < 1e-10, 'Kalman first value = signal[0]');
// Last few values should be near 1.0
assert(Math.abs(kalman_const[9] - 1) < 0.01, 'Kalman converges to 1.0 (within 0.01)');

// --- Butterworth Tests ---
console.log('\n--- Butterworth Tests ---');

// Butterworth on constant → constant
const butter_const = butterworthFilter(CONST_10, { order: 2, cutoff: 10 });
for (let i = 0; i < 10; i++) {
  assert(Math.abs(butter_const[i] - 1) < 0.01,
    `Butterworth on constant at index ${i} → ~1.0`);
}

// --- Gaussian Tests ---
console.log('\n--- Gaussian Tests ---');

// Gaussian on constant → constant (sigma=1 => radius=3, so NaN at i<3 and i>6)
const gauss_const = gaussianFilter(CONST_10, { sigma: 1.0 });
assert(Number.isNaN(gauss_const[0]) && Number.isNaN(gauss_const[1]) && Number.isNaN(gauss_const[2]),
  'Gaussian(sigma=1) edges are NaN (radius=3)');
for (let i = 3; i < 7; i++) {
  assert(Math.abs(gauss_const[i] - 1) < 1e-10,
    `Gaussian on constant at index ${i} → 1.0`);
}

// --- Median Tests ---
console.log('\n--- Median Tests ---');

// Median on constant → constant
const med3_const = medianFilter(CONST_10, { window: 3 });
assertArray(med3_const,
  [NaN, 1, 1, 1, 1, 1, 1, 1, 1, NaN],
  'Median(3) on constant [1]*10 → all 1s, NaN at edges',
  1e-10);

// Median should remove isolated spike: [0,0,0,100,0,0,0] with window=3
const SPIKE = [0, 0, 0, 100, 0, 0, 0];
const med3_spike = medianFilter(SPIKE, { window: 3 });
// i=3 (value 100): window = [0, 100, 0], sorted = [0,0,100], median = 0
assert(Math.abs(med3_spike[3] - 0) < 1e-10,
  'Median(3) on isolated spike → spike removed (0)');

// Median preserves step edges (window=3)
const STEP_SMALL = [1, 1, 1, 5, 5, 5, 5];
const med3_step = medianFilter(STEP_SMALL, { window: 3 });
assert(Math.abs(med3_step[3] - 5) < 1e-10,
  'Median(3) on step preserves edge at transition');

// --- LOWESS Tests ---
console.log('\n--- LOWESS Tests ---');

// LOWESS on constant → constant
const lowess_const = lowessFilter(CONST_10, { frac: 0.3 });
for (let i = 0; i < 10; i++) {
  assert(Math.abs(lowess_const[i] - 1) < 1e-10,
    `LOWESS on constant at index ${i} → 1.0`);
}

// LOWESS on linear ramp → roughly linear
const lowess_ramp = lowessFilter(RAMP, { frac: 0.3 });
for (let i = 0; i < 10; i++) {
  assert(Math.abs(lowess_ramp[i] - RAMP[i]) < 0.5,
    `LOWESS on linear ramp at index ${i} → approximately ${RAMP[i]}`);
}

// ============================================================
// Structural tests: test with very large signal for stability
// ============================================================
console.log('\n--- Stability Tests ---');

// Large constant signal
const LARGE = new Array(1000).fill(5);
const sma_large = smaFilter(LARGE, { window: 21 });
let allValid = true;
for (let i = 10; i < 990; i++) {
  if (Math.abs(sma_large[i] - 5) > 1e-10) { allValid = false; break; }
}
assert(allValid, 'SMA(21) on large constant signal → all 5.0 in valid region');

const gauss_large = gaussianFilter(LARGE, { sigma: 3 });
allValid = true;
for (let i = 9; i < 991; i++) {
  if (Math.abs(gauss_large[i] - 5) > 1e-10) { allValid = false; break; }
}
assert(allValid, 'Gaussian(sigma=3) on large constant signal → all 5.0 in valid region');

const med_large = medianFilter(LARGE, { window: 11 });
allValid = true;
for (let i = 5; i < 995; i++) {
  if (Math.abs(med_large[i] - 5) > 1e-10) { allValid = false; break; }
}
assert(allValid, 'Median(11) on large constant signal → all 5.0 in valid region');

// ============================================================
// Print Summary
// ============================================================
const allPassed = summary();
process.exit(allPassed ? 0 : 1);
