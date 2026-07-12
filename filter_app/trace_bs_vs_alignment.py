"""
Trace script: Compare BS markers (_compute_own_markers + _compute_cascade_markers)
against 同向性判断 (alignment masks from _compute_holding_masks).

Scenario: operating TF = 周线, viewing 日线
- BS markers for 日线 are cascaded from 周线's own BS markers
- Alignment masks for 日线 come from 周线's trade_records via _align_pnl_to_current_tf

The key test: does a cascaded BS marker on 日线 fall within the corresponding alignment mask?
"""

import sys
import os
import sqlite3
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.filter_engine import (
    apply_ema, _schmitt_trigger, _find_all_pairs,
    _fit_physics_parabola, _compute_strategy_pnl,
    _align_pnl_to_current_tf, _compute_holding_masks,
)
from services.bs_marker import _compute_own_markers, _compute_cascade_markers

DB_PATH = "/Users/xfpan/claude/filter_research/data/market.db"
OUTPUT_DIR = os.path.expanduser("~/.claude/orchestrator/output/orch-20260712-125752-45108")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "comparison.txt")

# ── App defaults ──
EWMA_SPAN = 60
K_EPS = 0.15
SIGMA_MIN = 0.05
EMA_SPAN = 10
N_EXTEND = 8
STOP_LOSS_PCT = 2.0


def load_kline(ticker, timeframe, limit=500):
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        """SELECT ts, open, high, low, close FROM kline
           WHERE ticker=? AND timeframe=?
           ORDER BY ts ASC LIMIT ?""",
        conn, params=(ticker, timeframe, limit),
    )
    conn.close()
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.set_index("ts")
    return df


def run_pipeline(df, ewma_span=EWMA_SPAN, k_eps=K_EPS, sigma_min=SIGMA_MIN):
    """Run full pipeline for one timeframe."""
    n = len(df)
    t = np.arange(n, dtype=float)
    noisy = df["close"].values.astype(float)
    dates = pd.DatetimeIndex(df.index)

    filtered = apply_ema(noisy, t, span=EMA_SPAN)

    v = np.gradient(filtered, t)
    a = np.gradient(v, t)
    schmitt = _schmitt_trigger(v, a, ewma_span=ewma_span, k_eps=k_eps, sigma_min=sigma_min)

    if schmitt is None:
        return None

    all_pairs = _find_all_pairs(schmitt["sig"])

    pred_pairs = []
    for pair_start, pair_end in all_pairs:
        if pair_end - pair_start >= 3:
            fit_result = _fit_physics_parabola(t, filtered, pair_start, pair_end)
            if fit_result is not None:
                pred_pairs.append({
                    "fit_result": fit_result, "fit_start": pair_start, "pair_end": pair_end,
                })

    long_pnl, short_pnl, trade_records = _compute_strategy_pnl(
        t, filtered, schmitt["sig"], all_pairs, pred_pairs,
        STOP_LOSS_PCT, n_extend=N_EXTEND,
    )

    bs = _compute_own_markers(t, dates, schmitt, all_pairs, [])

    return {
        "t": t, "dates": dates, "filtered": filtered, "schmitt": schmitt,
        "all_pairs": all_pairs, "trade_records": trade_records,
        "long_pnl": long_pnl, "short_pnl": short_pnl,
        "bs": bs,
    }


def compute_run_lengths(mask):
    runs = []
    in_run = False
    start = None
    for i, val in enumerate(mask):
        if val and not in_run:
            start = i; in_run = True
        elif not val and in_run:
            runs.append((start, i - 1)); in_run = False
    if in_run:
        runs.append((start, len(mask) - 1))
    return runs


def main():
    print("=" * 90)
    print("  TRACE: BS Cascade Markers vs 同向性判断 (Alignment Masks)")
    print("=" * 90)
    print()
    print(f"Ticker: 3690 | Operating TF: 周线 | Sub-TF: 日线")
    print(f"Schmitt: ewma_span={EWMA_SPAN}, k_eps={K_EPS}, sigma_min={SIGMA_MIN}")
    print(f"Filter: EMA(span={EMA_SPAN}), stop_loss={STOP_LOSS_PCT}%, n_extend={N_EXTEND}")
    print()

    # ═══════════════════════════════════════════════════════════
    # STEP 1: Load data
    # ═══════════════════════════════════════════════════════════
    print("=" * 90)
    print("STEP 1: Data Loading")
    print("=" * 90)

    df_weekly = load_kline("3690", "周线", limit=200)
    df_daily = load_kline("3690", "日线", limit=120)

    # Filter 日线 to only bars within 周线's date range (needed for alignment)
    w_start = df_weekly.index[0]
    w_end = df_weekly.index[-1]
    df_daily = df_daily[(df_daily.index >= w_start) & (df_daily.index <= w_end)]

    print(f"周线: {len(df_weekly)} bars, [{str(df_weekly.index[0])[:10]} .. {str(df_weekly.index[-1])[:10]}]")
    print(f"日线: {len(df_daily)} bars, [{str(df_daily.index[0])[:10]} .. {str(df_daily.index[-1])[:10]}]")
    print()

    # ═══════════════════════════════════════════════════════════
    # STEP 2: Run pipeline on 周线 (operating TF)
    # ═══════════════════════════════════════════════════════════
    print("=" * 90)
    print("STEP 2: Run Pipeline on 周线 (Operating TF)")
    print("=" * 90)

    wl = run_pipeline(df_weekly)
    if wl is None:
        print("ERROR: 周线 Schmitt trigger returned None (not enough bars for ewma_span)")
        return

    print(f"周线 all_pairs: {len(wl['all_pairs'])}")
    print(f"周线 trade_records: {len(wl['trade_records'])}")
    print(f"周线 BS entry markers: {len(wl['bs']['entry_markers'])}")
    print(f"周线 BS exit markers: {len(wl['bs']['exit_markers'])}")
    print()

    # Print 周线 BS markers
    print("周线 BS markers (own):")
    for idx, (bar, label, color, date) in enumerate(wl["bs"]["entry_markers"]):
        print(f"  entry[{idx}] bar={bar} {label}({color}) {str(date)[:10]}")
    for idx, item in enumerate(wl["bs"]["exit_markers"]):
        bar, label, color, xtype, date = item
        print(f"  exit[{idx}]  bar={bar} {label}({color}) {xtype} {str(date)[:10]}")

    # Print 周线 trade records
    print("\n周线 trade_records:")
    for tr in wl["trade_records"]:
        entry_date = wl["dates"][tr["entry_idx"]] if tr["entry_idx"] < len(wl["dates"]) else "?"
        exit_date = wl["dates"][tr["exit_idx"]] if tr["exit_idx"] < len(wl["dates"]) else "?"
        print(f"  id={tr['id']} {tr['type']:5s} entry_bar={tr['entry_idx']} exit_bar={tr['exit_idx']} "
              f"ret={tr['return_pct']:+.2f}% reason={tr['exit_reason']} "
              f"[{str(entry_date)[:10]} → {str(exit_date)[:10]}]")
    print()

    # ═══════════════════════════════════════════════════════════
    # STEP 3: Run pipeline on 日线 (sub-TF)
    # ═══════════════════════════════════════════════════════════
    print("=" * 90)
    print("STEP 3: Run Pipeline on 日线 (Sub-TF)")
    print("=" * 90)

    dl = run_pipeline(df_daily)
    if dl is None:
        print("ERROR: 日线 Schmitt trigger returned None")
        return

    print(f"日线 all_pairs: {len(dl['all_pairs'])}")
    print(f"日线 trade_records: {len(dl['trade_records'])}")
    print()

    # Print 日线's own BS markers (for reference)
    print("日线 BS markers (own — computed from 日线's own all_pairs):")
    for idx, (bar, label, color, date) in enumerate(dl["bs"]["entry_markers"]):
        print(f"  entry[{idx}] bar={bar} {label}({color}) {str(date)[:10]}")
    for idx, item in enumerate(dl["bs"]["exit_markers"]):
        bar, label, color, xtype, date = item
        print(f"  exit[{idx}]  bar={bar} {label}({color}) {xtype} {str(date)[:10]}")
    print()

    # ═══════════════════════════════════════════════════════════
    # STEP 4: Compute 日线 BS cascade markers from 周线
    # ═══════════════════════════════════════════════════════════
    print("=" * 90)
    print("STEP 4: Compute 日线 BS Cascade Markers from 周线")
    print("=" * 90)

    cascade_daily = _compute_cascade_markers(
        dl["t"], dl["dates"], dl["schmitt"], dl["all_pairs"],
        dl["trade_records"], wl["bs"]  # higher_bs = 周线's own BS markers
    )

    print(f"Cascade entry markers: {len(cascade_daily['entry_markers'])}")
    for idx, (bar, label, color, date) in enumerate(cascade_daily["entry_markers"]):
        print(f"  entry[{idx}] bar={bar} {label}({color}) {str(date)[:10]}")
    print(f"Cascade exit markers: {len(cascade_daily['exit_markers'])}")
    for idx, item in enumerate(cascade_daily["exit_markers"]):
        bar, label, color, xtype, date = item
        print(f"  exit[{idx}]  bar={bar} {label}({color}) {xtype} {str(date)[:10]}")
    print()

    # ═══════════════════════════════════════════════════════════
    # STEP 5: Compute 同向性判断 (alignment masks)
    # ═══════════════════════════════════════════════════════════
    print("=" * 90)
    print("STEP 5: Compute 同向性判断 (Alignment Masks)")
    print("=" * 90)

    # Align 周线 PnL → 日线 bars
    aligned = _align_pnl_to_current_tf(
        wl["dates"], wl["long_pnl"], wl["short_pnl"],
        wl["trade_records"], dl["dates"],
    )

    print(f"Aligned entry markers: {len(aligned['entry_markers'])}")
    for idx, (bar, ttype, pnl_val) in enumerate(aligned["entry_markers"]):
        d = dl["dates"][bar] if bar < len(dl["dates"]) else "?"
        print(f"  [{idx}] 日线bar={bar} type={ttype} pnl={pnl_val:.2f} {str(d)[:10]}")

    print(f"\nAligned exit markers: {len(aligned['exit_markers'])}")
    for idx, (bar, ttype, pnl_val, ret_pct, reason) in enumerate(aligned["exit_markers"]):
        d = dl["dates"][bar] if bar < len(dl["dates"]) else "?"
        print(f"  [{idx}] 日线bar={bar} type={ttype} pnl={pnl_val:.2f} ret={ret_pct:+.2f}% "
              f"reason={reason} {str(d)[:10]}")

    # Compute holding masks
    long_mask, short_mask = _compute_holding_masks(
        len(dl["t"]), aligned["entry_markers"], aligned["exit_markers"]
    )

    long_periods = compute_run_lengths(long_mask)
    short_periods = compute_run_lengths(short_mask)

    print(f"\nLong mask periods: {len(long_periods)}")
    for s, e in long_periods:
        ds = dl["dates"][s] if s < len(dl["dates"]) else "?"
        de = dl["dates"][e] if e < len(dl["dates"]) else "?"
        print(f"  bars [{s:4d}:{e:4d}]  [{str(ds)[:10]} : {str(de)[:10]}]")
    print(f"Short mask periods: {len(short_periods)}")
    for s, e in short_periods:
        ds = dl["dates"][s] if s < len(dl["dates"]) else "?"
        de = dl["dates"][e] if e < len(dl["dates"]) else "?"
        print(f"  bars [{s:4d}:{e:4d}]  [{str(ds)[:10]} : {str(de)[:10]}]")
    print()

    # ═══════════════════════════════════════════════════════════
    # STEP 6: COMPARISON
    # ═══════════════════════════════════════════════════════════
    print("=" * 90)
    print("STEP 6: COMPARISON — Cascade BS Markers vs 同向性判断")
    print("=" * 90)
    print()
    print("Legend: B(green)=Long entry  S(red)=Short entry  S(green)=Long exit  B(red)=Short exit")
    print("Alignment: green B should appear only in long_mask periods,")
    print("           red S should appear only in short_mask periods.")
    print()

    # Entry markers
    print("--- Cascade Entry Markers vs Alignment ---")
    misaligned_entries = 0
    for idx, (bar, label, color, date) in enumerate(cascade_daily["entry_markers"]):
        in_long = bool(long_mask[bar]) if bar < len(long_mask) else False
        in_short = bool(short_mask[bar]) if bar < len(short_mask) else False

        if label == "B":  # green = long entry
            aligned_ok = in_long
        else:  # S red = short entry
            aligned_ok = in_short

        status = "ALIGNED  " if aligned_ok else "MISALIGNED"
        if not aligned_ok:
            misaligned_entries += 1
        print(f"  [{idx:2d}] bar={bar:4d} {label}({color:5s}) {str(date)[:10]}  "
              f"long={in_long!s:5s} short={in_short!s:5s}  >> {status}")

    # Exit markers
    print("\n--- Cascade Exit Markers vs Alignment ---")
    misaligned_exits = 0
    for idx, item in enumerate(cascade_daily["exit_markers"]):
        bar, label, color, xtype, date = item
        in_long = bool(long_mask[bar]) if bar < len(long_mask) else False
        in_short = bool(short_mask[bar]) if bar < len(short_mask) else False

        if label == "S":  # green S = exit from long
            aligned_ok = in_long
        else:  # B red = exit from short
            aligned_ok = in_short

        status = "ALIGNED  " if aligned_ok else "MISALIGNED"
        if not aligned_ok:
            misaligned_exits += 1
        print(f"  [{idx:2d}] bar={bar:4d} {label}({color:5s}) {xtype:8s} {str(date)[:10]}  "
              f"long={in_long!s:5s} short={in_short!s:5s}  >> {status}")

    # ═══════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════
    print("\n" + "=" * 90)
    print("SUMMARY")
    print("=" * 90)
    print(f"周线 all_pairs:         {len(wl['all_pairs'])}")
    print(f"周线 trade_records:     {len(wl['trade_records'])}")
    print(f"周线 own BS entries:    {len(wl['bs']['entry_markers'])}")
    print(f"周线 own BS exits:      {len(wl['bs']['exit_markers'])}")
    print(f"日线 all_pairs:         {len(dl['all_pairs'])}")
    print(f"日线 cascade entries:   {len(cascade_daily['entry_markers'])}")
    print(f"日线 cascade exits:     {len(cascade_daily['exit_markers'])}")
    print(f"Long mask periods:      {len(long_periods)}")
    print(f"Short mask periods:     {len(short_periods)}")

    total_entries = len(cascade_daily['entry_markers'])
    total_exits = len(cascade_daily['exit_markers'])
    total_bs = total_entries + total_exits
    total_misaligned = misaligned_entries + misaligned_exits
    if total_entries > 0:
        print(f"Misaligned entries:     {misaligned_entries}/{total_entries} = {misaligned_entries/total_entries*100:.1f}%")
    if total_exits > 0:
        print(f"Misaligned exits:       {misaligned_exits}/{total_exits} = {misaligned_exits/total_exits*100:.1f}%")
    if total_bs > 0:
        print(f"Total misalignment:     {total_misaligned}/{total_bs} = {total_misaligned/total_bs*100:.1f}%")

    # ═══════════════════════════════════════════════════════════
    # DEEP DIVE: Compare 周线 BS marker timing vs trade_record timing
    # ═══════════════════════════════════════════════════════════
    print("\n" + "=" * 90)
    print("DEEP DIVE: 周线 BS mark timing vs 周线 trade_record timing")
    print("(Misalignment between these two is the ROOT CAUSE of cascade misalignment)")
    print("=" * 90)

    print("\n周线 BS markers (from all_pairs):")
    for idx, (bar, label, color, date) in enumerate(wl["bs"]["entry_markers"]):
        print(f"  entry[{idx}] bar={bar} {label}({color}) {str(date)[:10]}")
    for idx, item in enumerate(wl["bs"]["exit_markers"]):
        bar, label, color, xtype, date = item
        print(f"  exit[{idx}]  bar={bar} {label}({color}) {xtype} {str(date)[:10]}")

    print("\n周线 trade_records (from strategy):")
    for tr in wl["trade_records"]:
        d_entry = wl["dates"][tr["entry_idx"]] if tr["entry_idx"] < len(wl["dates"]) else "?"
        d_exit = wl["dates"][tr["exit_idx"]] if tr["exit_idx"] < len(wl["dates"]) else "?"
        print(f"  id={tr['id']} {tr['type']:5s} entry_bar={tr['entry_idx']:4d} exit_bar={tr['exit_idx']:4d} "
              f"[{str(d_entry)[:10]} → {str(d_exit)[:10]}] "
              f"ret={tr['return_pct']:+.2f}% reason={tr['exit_reason']}")

    # Find mismatches
    print("\nComparing BS markers vs trade_records:")
    for pair_idx, (pair_start, pair_end) in enumerate(wl["all_pairs"]):
        sig_val = wl["schmitt"]["sig"][pair_end]
        direction = "LONG" if sig_val == 1 else "SHORT"
        bs_entry = (pair_start, "B" if sig_val == 1 else "S", "green" if sig_val == 1 else "red")
        bs_exit = (pair_end, "S" if sig_val == 1 else "B", "green" if sig_val == 1 else "red")

        # Find corresponding trade record
        matching_tr = None
        for tr in wl["trade_records"]:
            tr_type = tr["type"]
            if (direction == "LONG" and tr_type == "long") or (direction == "SHORT" and tr_type == "short"):
                # Check if this trade overlaps with the pair
                if tr["entry_idx"] <= pair_end and tr["exit_idx"] >= pair_start:
                    matching_tr = tr
                    break

        if matching_tr:
            bs_entry_bar = bs_entry[0]
            bs_exit_bar = bs_exit[0]
            tr_entry_bar = matching_tr["entry_idx"]
            tr_exit_bar = matching_tr["exit_idx"]
            entry_diff = bs_entry_bar - tr_entry_bar
            exit_diff = bs_exit_bar - tr_exit_bar
            print(f"  pair[{pair_idx}] {direction:5s} BS entry={bs_entry_bar:4d} exit={bs_exit_bar:4d}  "
                  f"TR entry={tr_entry_bar:4d} exit={tr_exit_bar:4d}  "
                  f"diff entry={entry_diff:+d} exit={exit_diff:+d}  "
                  f"{'SAME' if entry_diff == 0 and exit_diff == 0 else 'DIFFER!'}")
        else:
            print(f"  pair[{pair_idx}] {direction:5s} BS entry={bs_entry[0]:4d} exit={bs_exit[0]:4d}  "
                  f"TR: NO MATCH")


if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    import io
    from contextlib import redirect_stdout

    f = io.StringIO()
    with redirect_stdout(f):
        try:
            main()
        except Exception as e:
            print(f"\nERROR: {e}")
            import traceback
            traceback.print_exc()
    output = f.getvalue()

    with open(OUTPUT_FILE, "w") as fh:
        fh.write(output)

    print(output)
    print(f"\nOutput written to: {OUTPUT_FILE}")
