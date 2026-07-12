"""
Trace script: debug why BS markers from 日线 do NOT cascade to 60分钟
for preset 3690_HK_2.

Loads real data from SQLite, runs the actual Schmitt trigger and BS marker
computation, and prints ALL intermediate values.
"""
import sys
sys.path.insert(0, '/Users/xfpan/claude/filter_research/filter_app')

import numpy as np
import pandas as pd
import sqlite3

from services.bs_marker import compute_bs_markers, _find_date_index, get_lower_tfs
from services.filter_engine import _schmitt_trigger, _find_all_pairs
from components.sidebar import TF_HIERARCHY

# ── Preset 3690_HK_2 params ──
# v2_tf=日线: k_e=0.15, sigma_min=0.05, ewma_span=60, n_pts=60
# v1_tf=60分钟: k_e=0.1, sigma_min=0.05, ewma_span=60, n_pts=60
# filter: savgol (window=13, order=4) + ema (span=10) dual

DB_PATH = "/Users/xfpan/claude/filter_research/data/market.db"

def load_kline(ticker, timeframe, limit=120):
    """Load kline data from SQLite — most recent bars first."""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT ts, open, high, low, close, volume FROM kline "
        "WHERE ticker=? AND timeframe=? ORDER BY ts DESC LIMIT ?",
        conn, params=(ticker, timeframe, limit)
    )
    conn.close()
    if df.empty:
        return None
    df = df.iloc[::-1].reset_index(drop=True)  # chronological order
    df['ts'] = pd.to_datetime(df['ts'])
    # Strip timezone to make tz-naive (日线 data is already tz-naive)
    if df['ts'].dt.tz is not None:
        df['ts'] = df['ts'].dt.tz_localize(None)
    return df

def compute_dashboard_view(close, dates, k_eps, sigma_min, ewma_span):
    """Run the full pipeline on one timeframe (savgol+ema dual filter, schmitt)."""
    from scipy.signal import savgol_filter
    from services.filter_engine import apply_ema

    n = len(close)
    t = np.arange(n, dtype=float)

    # Step A: Apply primary filter (savgol, window=13, order=4)
    filtered = savgol_filter(close, 13, 4)
    # Step B: Apply secondary filter (ema, span=10) on top of primary
    filtered2 = apply_ema(filtered, t, 10)

    # Step C: Use filtered2 for Schmitt trigger
    v = np.gradient(filtered2, t)
    a = np.gradient(v, t)

    schmitt = _schmitt_trigger(v, a, ewma_span=int(ewma_span),
                               k_eps=k_eps, sigma_min=sigma_min)
    all_pairs = _find_all_pairs(schmitt['sig']) if schmitt is not None else []

    return t, dates, filtered, filtered2, v, a, schmitt, all_pairs

# ── Load data ──
daily = load_kline("3690", "日线", 200)
m60   = load_kline("3690", "60分钟", 200)

if daily is None:
    print("ERROR: No 日线 data for ticker 3690")
    sys.exit(1)
if m60 is None:
    print("ERROR: No 60分钟 data for ticker 3690")
    sys.exit(1)

print("=" * 80)
print("PART 1: Data Overview")
print("=" * 80)
print(f"日线 bars: {len(daily)}")
print(f"日线 date range: {daily['ts'].iloc[0]} ~ {daily['ts'].iloc[-1]}")
print(f"日线 close range: {daily['close'].min():.2f} ~ {daily['close'].max():.2f}")

print(f"\n60分钟 bars: {len(m60)}")
print(f"60分钟 date range: {m60['ts'].iloc[0]} ~ {m60['ts'].iloc[-1]}")
print(f"60分钟 close range: {m60['close'].min():.2f} ~ {m60['close'].max():.2f}")

# ── Compute 日线 ──
print("\n" + "=" * 80)
print("PART 2: 日线 Schmitt Trigger")
print("=" * 80)

t_d, dates_d, filt_d, filt2_d, v_d, a_d, schmitt_d, all_pairs_d = \
    compute_dashboard_view(daily['close'].values, pd.DatetimeIndex(daily['ts']),
                           k_eps=0.15, sigma_min=0.05, ewma_span=60)

print(f"日线 schmitt sig unique: {np.unique(schmitt_d['sig'])}")
print(f"日线 schmitt sig counts: +1={np.sum(schmitt_d['sig']==1)}, -1={np.sum(schmitt_d['sig']==-1)}, 0={np.sum(schmitt_d['sig']==0)}")
print(f"日线 all_pairs: {all_pairs_d}")

if all_pairs_d:
    print(f"\n日线 pair details:")
    for ps, pe in all_pairs_d:
        direction = schmitt_d['sig'][pe]
        dir_label = "多(+1)" if direction == 1 else "空(-1)"
        ps_date = dates_d[ps] if ps < len(dates_d) else "N/A"
        pe_date = dates_d[pe] if pe < len(dates_d) else "N/A"
        print(f"  [{ps:4d}→{pe:3d}] {dir_label}  entry_date={ps_date}  exit_date={pe_date}")

# ── Compute 日线 BS markers ──
print("\n" + "=" * 80)
print("PART 3: 日线 BS Markers (own)")
print("=" * 80)

bs_daily = compute_bs_markers(t_d, dates_d, schmitt_d, all_pairs_d, [],
                              '日线', '日线')

print(f"日线 Entry markers ({len(bs_daily['entry_markers'])}):")
for em in bs_daily['entry_markers']:
    bar_idx, label, color, date = em
    print(f"  bar={bar_idx:4d}  label={label}  color={color}  date={date}")

print(f"\n日线 Exit markers ({len(bs_daily['exit_markers'])}):")
for ex in bs_daily['exit_markers']:
    bar_idx, label, color, exit_type, date = ex
    print(f"  bar={bar_idx:4d}  label={label}  color={color}  type={exit_type}  date={date}")

# ── Compute 60分钟 ──
print("\n" + "=" * 80)
print("PART 4: 60分钟 Schmitt Trigger")
print("=" * 80)

n_m60 = 120
t_60, dates_60, filt_60, filt2_60, v_60, a_60, schmitt_60, all_pairs_60 = \
    compute_dashboard_view(m60['close'].values[:n_m60],
                           pd.DatetimeIndex(m60['ts'][:n_m60]),
                           k_eps=0.1, sigma_min=0.05, ewma_span=60)

print(f"60分鐘 schmitt sig unique: {np.unique(schmitt_60['sig'])}")
print(f"60分鐘 schmitt sig counts: +1={np.sum(schmitt_60['sig']==1)}, -1={np.sum(schmitt_60['sig']==-1)}, 0={np.sum(schmitt_60['sig']==0)}")
print(f"60分鐘 all_pairs: {all_pairs_60}")

if all_pairs_60:
    print(f"\n60分鐘 pair details:")
    for ps, pe in all_pairs_60:
        direction = schmitt_60['sig'][pe]
        dir_label = "多(+1)" if direction == 1 else "空(-1)"
        ps_date = dates_60[ps] if ps < len(dates_60) else "N/A"
        pe_date = dates_60[pe] if pe < len(dates_60) else "N/A"
        print(f"  [{ps:4d}→{pe:3d}] {dir_label}  entry_date={ps_date}  exit_date={pe_date}")

# ── Key: Check the date overlap ──
print("\n" + "=" * 80)
print("PART 5: Date Alignment Check")
print("=" * 80)
print(f"日线 date range: {dates_d[0]} ~ {dates_d[-1]}")
print(f"60分鐘 date range: {dates_60[0]} ~ {dates_60[-1]}")
print(f"Do they overlap? 日线[-1]={dates_d[-1]} vs 60m[0]={dates_60[0]}")

# Check each daily entry marker
print(f"\n60分鐘 first 5 dates: {list(dates_60[:5])}")
print(f"60分鐘 last 5 dates: {list(dates_60[-5:])}")

# ── CHECK: _find_date_index for each daily entry ──
print("\n" + "=" * 80)
print("PART 6: _find_date_index Trace")
print("=" * 80)

for em in bs_daily['entry_markers']:
    bar_idx, label, color, date = em
    print(f"\n日线 Entry: bar={bar_idx}  {label}({color})  date={date}")
    if date is None:
        print("  → SKIP: date is None")
        continue
    start_bar = _find_date_index(dates_60, date)
    print(f"  _find_date_index(dates_60, {date}) = {start_bar}")
    if start_bar is None:
        print(f"  → SKIP: start_bar is None (entry date after all 60m data)")
        continue
    if start_bar >= len(dates_60):
        print(f"  → SKIP: start_bar={start_bar} >= len(dates_60)={len(dates_60)}")
        continue
    print(f"  60m date at start_bar: {dates_60[start_bar]}")

# ── CRITICAL: Cascade check ──
print("\n" + "=" * 80)
print("PART 7: CASCADE TRACE (Simulating _compute_cascade_markers logic)")
print("=" * 80)

sig_60 = schmitt_60['sig']

for em in bs_daily['entry_markers']:
    bar_idx, label, color, date = em
    print(f"\n{'─'*60}")
    print(f"日线 Entry: bar={bar_idx}  {label}({color})  date={date}")
    if date is None:
        print("  SKIP: date is None")
        continue
    start_bar = _find_date_index(dates_60, date)
    if start_bar is None:
        print(f"  SKIP: start_bar is None")
        continue

    expected_dir = 1 if label == 'B' else -1
    print(f"  start_bar={start_bar}, looking for direction={expected_dir}")
    print(f"  60m all_pairs: {all_pairs_60}")

    found = False
    for ps, pe in all_pairs_60:
        print(f"    Checking pair ({ps},{pe}): ps>=start_bar? {ps >= start_bar} | pe<len(sig)? {pe < len(sig_60)} | sig[pe]={sig_60[pe]} target={expected_dir}")
        if ps >= start_bar and pe < len(sig_60):
            if sig_60[pe] == expected_dir:
                print(f"    → MATCH! pair ({ps},{pe}) direction={sig_60[pe]}")
                found = True
                break
            else:
                print(f"    → WRONG DIR: sig[pe]={sig_60[pe]} != {expected_dir}")
    if not found:
        print(f"  ★ NO MATCH FOUND for this entry!")

# ── Also check what happens when we compute cascade ──
print("\n" + "=" * 80)
print("PART 8: Actual compute_bs_markers Cascade for 60分鐘")
print("=" * 80)

bs_60 = compute_bs_markers(t_60, dates_60, schmitt_60, all_pairs_60, [],
                           '60分钟', '日线', higher_bs=bs_daily)

print(f"60分鐘 Cascade Entry markers ({len(bs_60['entry_markers'])}):")
for em in bs_60['entry_markers']:
    bar_idx, label, color, date = em
    print(f"  bar={bar_idx:4d}  label={label}  color={color}  date={date}")

print(f"\n60分鐘 Cascade Exit markers ({len(bs_60['exit_markers'])}):")
for ex in bs_60['exit_markers']:
    bar_idx, label, color, exit_type, date = ex
    print(f"  bar={bar_idx:4d}  label={label}  color={color}  type={exit_type}  date={date}")

if not bs_60['entry_markers'] and bs_daily['entry_markers']:
    print("\n⚠️ CASCADE FAILED: 日线 has entries but 60分鐘 has none!")

# ── Check: does _find_date_index work correctly? ──
print("\n" + "=" * 80)
print("PART 9: Deeper _find_date_index Debug")
print("=" * 80)

# The cascade logic: for each higher entry, find start_bar.
# Then for each lower pair, if pair_start >= start_bar and sig[pair_end] == expected_dir → match.
# If NO pair has pair_start >= start_bar, OR no pair with the right direction → no cascade.

# Key question: are the 60m dates AFTER the daily dates?
# Let's check the last daily bar dates vs 60m dates
print(f"日线 last 10 dates: {list(dates_d[-10:])}")
print(f"60分鐘 first 10 dates: {list(dates_60[:10])}")
print(f"60分鐘 last 10 dates: {list(dates_60[-10:])}")

# Check: are daily entry dates within 60m range?
for em in bs_daily['entry_markers']:
    bar_idx, label, color, date = em
    if date is None:
        continue
    dd = pd.Timestamp(date)
    d60_first = dates_60[0]
    d60_last = dates_60[-1]
    in_range = dd >= d60_first and dd <= d60_last
    print(f"Entry {label}({color}) date={date}: in 60m range? {in_range}")
    if in_range:
        start_bar = _find_date_index(dates_60, dd)
        print(f"  _find_date_index returns: {start_bar}")
        if start_bar is not None:
            # Check how many 60m pairs start after this bar
            later_pairs = [(ps, pe) for ps, pe in all_pairs_60 if ps >= start_bar]
            print(f"  60m pairs after start_bar: {len(later_pairs)}")
            for ps, pe in later_pairs:
                print(f"    pair ({ps},{pe}) dir={sig_60[pe]}")
