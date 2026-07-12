"""
Trace script: THREE-WAY COMPARISON
  A) PnL (trade_records) — strategy trades from _compute_strategy_pnl
  B) 同向性判断 (alignment) — holding masks from higher TF aligned to current TF
  C) Current BS markers — what _compute_own_markers produces from trade_records

Scenario: 周线 as higher TF, 日线 as operating TF
The key question: are BS markers driven by PnL (A/C) or by alignment (B)?
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
from services.bs_marker import _compute_own_markers

DB_PATH = "/Users/xfpan/claude/filter_research/data/market.db"
OUTPUT_DIR = os.path.expanduser("~/.claude/orchestrator/output/orch-20260712-131503-64689")
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


def run_pipeline_full(df, ewma_span=EWMA_SPAN, k_eps=K_EPS, sigma_min=SIGMA_MIN):
    """Run full pipeline including strategy PnL for one timeframe."""
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

    # ── C) BS markers as currently computed (from trade_records) ──
    bs_from_pnl = _compute_own_markers(t, dates, schmitt, all_pairs, trade_records)

    # ── Also compute BS markers from all_pairs only (fallback path) for comparison ──
    bs_from_pairs = _compute_own_markers(t, dates, schmitt, all_pairs, [])

    return {
        "t": t, "dates": dates, "filtered": filtered, "schmitt": schmitt,
        "all_pairs": all_pairs, "trade_records": trade_records,
        "long_pnl": long_pnl, "short_pnl": short_pnl,
        "bs_from_pnl": bs_from_pnl,
        "bs_from_pairs": bs_from_pairs,
    }


def find_contiguous_runs(mask):
    """Find contiguous True runs in a boolean array."""
    runs = []
    in_run = False
    start = None
    for i, val in enumerate(mask):
        if val and not in_run:
            start = i
            in_run = True
        elif not val and in_run:
            runs.append((start, i - 1))
            in_run = False
    if in_run:
        runs.append((start, len(mask) - 1))
    return runs


def main():
    print("=" * 90)
    print("  THREE-WAY COMPARISON: PnL vs 同向性判断 vs BS标记")
    print("=" * 90)
    print()
    print("Ticker: 3690  |  Higher TF: 周线  |  Current (operating) TF: 日线")
    print(f"Schmitt: ewma_span={EWMA_SPAN}, k_eps={K_EPS}, sigma_min={SIGMA_MIN}")
    print(f"Filter: EMA(span={EMA_SPAN}), stop_loss={STOP_LOSS_PCT}%, n_extend={N_EXTEND}")
    print()
    print("QUESTION: Are 日线 BS markers driven by PnL (trade_records) or by 同向性判断?")
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
    # STEP 2: Run pipeline on 周线 (higher TF)
    # ═══════════════════════════════════════════════════════════
    print("=" * 90)
    print("STEP 2: Run Pipeline on 周线 (Higher TF)")
    print("=" * 90)

    wl = run_pipeline_full(df_weekly)
    if wl is None:
        print("ERROR: 周线 Schmitt trigger returned None")
        return

    print(f"周线 all_pairs:  {len(wl['all_pairs'])}")
    print(f"周线 trade_records (PnL): {len(wl['trade_records'])}")
    print()

    print("周线 trade_records (PnL):")
    for tr in wl["trade_records"]:
        entry_date = wl["dates"][tr["entry_idx"]] if tr["entry_idx"] < len(wl["dates"]) else "?"
        exit_date = wl["dates"][tr["exit_idx"]] if tr["exit_idx"] < len(wl["dates"]) else "?"
        print(f"  id={tr['id']} {tr['type']:5s} entry_bar={tr['entry_idx']:4d} exit_bar={tr['exit_idx']:4d} "
              f"ret={tr['return_pct']:+.2f}% reason={tr['exit_reason']:12s} "
              f"[{str(entry_date)[:10]} -> {str(exit_date)[:10]}]")
    print()

    # ═══════════════════════════════════════════════════════════
    # STEP 3: Run pipeline on 日线 (current TF)
    # ═══════════════════════════════════════════════════════════
    print("=" * 90)
    print("STEP 3: Run Pipeline on 日线 (Current TF)")
    print("=" * 90)

    dl = run_pipeline_full(df_daily)
    if dl is None:
        print("ERROR: 日线 Schmitt trigger returned None")
        return

    print(f"日线 all_pairs:  {len(dl['all_pairs'])}")
    print(f"日线 trade_records (PnL): {len(dl['trade_records'])}")
    print()

    # ═══════════════════════════════════════════════════════════
    # SOURCE A: PnL (trade_records) for 日线
    # ═══════════════════════════════════════════════════════════
    print("-" * 90)
    print("SOURCE A: PnL (trade_records) — 日线 strategy trades from _compute_strategy_pnl")
    print("-" * 90)
    for tr in dl["trade_records"]:
        entry_date = dl["dates"][tr["entry_idx"]] if tr["entry_idx"] < len(dl["dates"]) else "?"
        exit_date = dl["dates"][tr["exit_idx"]] if tr["exit_idx"] < len(dl["dates"]) else "?"
        print(f"  id={tr['id']} {tr['type']:5s} entry_bar={tr['entry_idx']:4d} exit_bar={tr['exit_idx']:4d} "
              f"ret={tr['return_pct']:+.2f}% reason={tr['exit_reason']:12s} "
              f"[{str(entry_date)[:10]} -> {str(exit_date)[:10]}]")
    print()

    # ═══════════════════════════════════════════════════════════
    # SOURCE B: 同向性判断 (alignment from 周线 -> 日线)
    # ═══════════════════════════════════════════════════════════
    print("-" * 90)
    print("SOURCE B: 同向性判断 (Alignment) — 周线 trades aligned to 日线")
    print("-" * 90)

    aligned = _align_pnl_to_current_tf(
        wl["dates"], wl["long_pnl"], wl["short_pnl"],
        wl["trade_records"], dl["dates"],
    )

    print(f"Aligned entry markers: {len(aligned['entry_markers'])}")
    for idx, (bar, ttype, pnl_val) in enumerate(aligned["entry_markers"]):
        d = dl["dates"][bar] if bar < len(dl["dates"]) else "?"
        print(f"  [{idx}] 日线bar={bar:4d} type={ttype:5s} pnl={pnl_val:.2f} {str(d)[:10]}")

    print(f"\nAligned exit markers: {len(aligned['exit_markers'])}")
    for idx, (bar, ttype, pnl_val, ret_pct, reason) in enumerate(aligned["exit_markers"]):
        d = dl["dates"][bar] if bar < len(dl["dates"]) else "?"
        print(f"  [{idx}] 日线bar={bar:4d} type={ttype:5s} pnl={pnl_val:.2f} ret={ret_pct:+.2f}% "
              f"reason={reason} {str(d)[:10]}")

    # Compute holding masks
    long_mask, short_mask = _compute_holding_masks(
        len(dl["t"]), aligned["entry_markers"], aligned["exit_markers"]
    )

    long_runs = find_contiguous_runs(long_mask)
    short_runs = find_contiguous_runs(short_mask)

    print(f"\nLong mask periods ({len(long_runs)} runs, {long_mask.sum()} bars total):")
    for s, e in long_runs:
        ds = dl["dates"][s] if s < len(dl["dates"]) else "?"
        de = dl["dates"][e] if e < len(dl["dates"]) else "?"
        print(f"  bars [{s:4d}:{e:4d}]  [{str(ds)[:10]} : {str(de)[:10]}]")

    print(f"\nShort mask periods ({len(short_runs)} runs, {short_mask.sum()} bars total):")
    for s, e in short_runs:
        ds = dl["dates"][s] if s < len(dl["dates"]) else "?"
        de = dl["dates"][e] if e < len(dl["dates"]) else "?"
        print(f"  bars [{s:4d}:{e:4d}]  [{str(ds)[:10]} : {str(de)[:10]}]")

    print(f"\nTotal alignment coverage: {long_mask.sum()} long + {short_mask.sum()} short "
          f"= {long_mask.sum() + short_mask.sum()} / {len(long_mask)} bars "
          f"({(long_mask.sum()+short_mask.sum())/len(long_mask)*100:.1f}%)")
    print()

    # ═══════════════════════════════════════════════════════════
    # SOURCE C: Current BS markers (from _compute_own_markers with trade_records)
    # ═══════════════════════════════════════════════════════════
    print("-" * 90)
    print("SOURCE C: Current BS Markers — from _compute_own_markers(trade_records)")
    print("-" * 90)

    bs_c = dl["bs_from_pnl"]

    print(f"Entry markers: {len(bs_c['entry_markers'])}")
    for idx, (bar, label, color, date) in enumerate(bs_c["entry_markers"]):
        in_long = bool(long_mask[bar]) if bar < len(long_mask) else False
        in_short = bool(short_mask[bar]) if bar < len(short_mask) else False
        aligned_ok = (label == "B" and in_long) or (label == "S" and in_short)
        source = "同向确认" if aligned_ok else "仅PnL"
        print(f"  [{idx}] bar={bar:4d} {label}({color:5s}) {str(date)[:10]} | "
              f"long={str(in_long):5s} short={str(in_short):5s} | {source}")

    print(f"\nExit markers: {len(bs_c['exit_markers'])}")
    for idx, item in enumerate(bs_c["exit_markers"]):
        bar, label, color, xtype, date = item
        in_long = bool(long_mask[bar]) if bar < len(long_mask) else False
        in_short = bool(short_mask[bar]) if bar < len(short_mask) else False
        # exit: S(green) = exit from long; B(red) = exit from short
        aligned_ok = (label == "S" and in_long) or (label == "B" and in_short)
        source = "同向确认" if aligned_ok else "仅PnL"
        print(f"  [{idx}] bar={bar:4d} {label}({color:5s}) {xtype:10s} {str(date)[:10]} | "
              f"long={str(in_long):5s} short={str(in_short):5s} | {source}")
    print()

    # ═══════════════════════════════════════════════════════════
    # STEP 6: THREE-WAY DETAILED COMPARISON TABLE
    # ═══════════════════════════════════════════════════════════
    print("=" * 90)
    print("THREE-WAY DETAILED COMPARISON")
    print("=" * 90)
    print()
    print(f"{'Bar':>4s} {'Date':>12s} {'Price':>8s} {'Sig':>3s} {'A_PnL':>6s} {'B_Align':>8s} {'C_BS':>6s} {'Match':>6s}")
    print("-" * 70)

    for i in range(len(dl["t"])):
        bar = i
        date_str = str(dl["dates"][i])[:10] if i < len(dl["dates"]) else "?"
        price = dl["filtered"][i]
        sig_val = dl["schmitt"]["sig"][i]

        # A: Is this bar within a PnL trade (日线's own strategy trades)?
        in_pnl_trade = False
        pnl_type = ""
        for tr in dl["trade_records"]:
            if tr["entry_idx"] <= i <= tr["exit_idx"]:
                in_pnl_trade = True
                pnl_type = tr["type"][:1].upper()  # L or S
                break

        # B: Is this bar within 同向性判断 alignment?
        in_align = bool(long_mask[i]) or bool(short_mask[i])
        align_type = ""
        if bool(long_mask[i]):
            align_type = "L"
        elif bool(short_mask[i]):
            align_type = "S"

        # C: Is this bar a BS marker?
        bs_label = ""
        for (b, lbl, col, d) in bs_c["entry_markers"]:
            if b == i:
                bs_label = f"E-{lbl}"
                break
        for (b, lbl, col, xt, d) in bs_c["exit_markers"]:
            if b == i:
                bs_label = f"X-{lbl}"
                break

        if in_pnl_trade or in_align or bs_label:
            a_str = pnl_type if in_pnl_trade else "·"
            b_str = align_type if in_align else "·"
            c_str = bs_label if bs_label else "·"

            # Match: does A agree with B? does C agree with B?
            match_ab = "OK" if (a_str == b_str) or (a_str == "·" and b_str == "·") else "DIFF"
            match_cb = "OK" if (
                (c_str.startswith("E-") or c_str.startswith("X-")) and b_str != "·"
            ) or (c_str == "·" and b_str == "·") else (
                "DIFF" if c_str != "·" and b_str == "·" else "·"
            )

            print(f"{bar:4d} {date_str:>12s} {price:8.2f} {sig_val:3d} "
                  f"{a_str:>6s} {b_str:>8s} {c_str:>6s} {match_cb:>6s}")
    print()

    # ═══════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════
    print("=" * 90)
    print("SUMMARY")
    print("=" * 90)

    pnl_entry_count = len(dl["trade_records"])
    bs_entry_count = len(bs_c["entry_markers"])
    bs_exit_count = len(bs_c["exit_markers"])

    # Count BS markers that align with 同向性判断
    aligned_entries = 0
    for bar, label, color, date in bs_c["entry_markers"]:
        in_long = bool(long_mask[bar]) if bar < len(long_mask) else False
        in_short = bool(short_mask[bar]) if bar < len(short_mask) else False
        if (label == "B" and in_long) or (label == "S" and in_short):
            aligned_entries += 1

    aligned_exits = 0
    for bar, label, color, xtype, date in bs_c["exit_markers"]:
        in_long = bool(long_mask[bar]) if bar < len(long_mask) else False
        in_short = bool(short_mask[bar]) if bar < len(short_mask) else False
        if (label == "S" and in_long) or (label == "B" and in_short):
            aligned_exits += 1

    print(f"  SOURCE A — 日线 PnL trades:              {pnl_entry_count}")
    print(f"  SOURCE B — 同向性判断 long periods:      {len(long_runs)}")
    print(f"  SOURCE B — 同向性判断 short periods:     {len(short_runs)}")
    print(f"  SOURCE B — alignment coverage:           {long_mask.sum() + short_mask.sum()} / {len(long_mask)} bars")
    print(f"  SOURCE C — BS entry markers:             {bs_entry_count}")
    print(f"  SOURCE C — BS exit markers:              {bs_exit_count}")
    print()
    print(f"  BS entries confirmed by 同向性判断:       {aligned_entries} / {bs_entry_count}"
          + (f" = {aligned_entries/bs_entry_count*100:.1f}%" if bs_entry_count > 0 else ""))
    print(f"  BS exits confirmed by 同向性判断:         {aligned_exits} / {bs_exit_count}"
          + (f" = {aligned_exits/bs_exit_count*100:.1f}%" if bs_exit_count > 0 else ""))
    total_bs = bs_entry_count + bs_exit_count
    total_aligned = aligned_entries + aligned_exits
    total_misaligned = total_bs - total_aligned
    print(f"  BS markers showing ONLY PnL (misaligned): {total_misaligned} / {total_bs}"
          + (f" = {total_misaligned/total_bs*100:.1f}%" if total_bs > 0 else ""))
    print()

    # ═══════════════════════════════════════════════════════════
    # CONCLUSION
    # ═══════════════════════════════════════════════════════════
    print("=" * 90)
    print("CONCLUSION")
    print("=" * 90)
    print()

    # Check: are SOURCE C BS markers identical to SOURCE A PnL trades?
    # Both come from the same trade_records
    print("Comparison: SOURCE C (BS markers) vs SOURCE A (PnL trades)")
    print("  _compute_own_markers generates BS markers directly from trade_records.")
    print(f"  Number of PnL trades: {pnl_entry_count}")
    print(f"  Number of BS entry markers: {bs_entry_count}")
    print(f"  → SOURCE C entries == SOURCE A count? {pnl_entry_count == bs_entry_count}")
    print()

    if total_bs > 0 and total_aligned < total_bs:
        print("FINDING: Some BS markers fall OUTSIDE 同向性判断 alignment periods.")
        print("This means BS markers are showing PnL-only trades that are NOT confirmed")
        print("by the higher TF (周线) alignment direction.")
        print()
        print("ROOT CAUSE: _compute_own_markers uses trade_records (from _compute_strategy_pnl)")
        print("which are the CURRENT TF's own strategy trades. These trades may occur when")
        print("the higher TF has NO position (同向性判断 = 0), leading to BS markers that")
        print("are unconfirmed by the higher timeframe trend.")
        print()
    else:
        print("All BS markers fall within 同向性判断 alignment periods.")
        print("The PnL trades are already confirmed by higher TF alignment.")
        print()

    print("WHAT BS MARKERS SHOULD USE:")
    print("  BS markers on the operating TF should ideally derive from 同向性判断")
    print("  (alignment from higher TF), not from the current TF's own PnL trades.")
    print("  This ensures BS markers only appear when the higher TF confirms the direction.")
    print()

    # Show what 同向性判断-based markers WOULD look like
    print("-" * 90)
    print("同向性判断-based entry markers (from 周线 aligned to 日线):")
    for em in aligned["entry_markers"]:
        bar, ttype, pnl_val = em
        d = dl["dates"][bar] if bar < len(dl["dates"]) else "?"
        label = "B" if ttype == "long" else "S"
        color = "green" if ttype == "long" else "red"
        print(f"  bar={bar:4d} {label}({color:5s}) type={ttype:5s} {str(d)[:10]}")

    print("\n同向性判断-based exit markers:")
    for em in aligned["exit_markers"]:
        bar, ttype, pnl_val, ret_pct, reason = em
        d = dl["dates"][bar] if bar < len(dl["dates"]) else "?"
        label = "S" if ttype == "long" else "B"
        color = "green" if ttype == "long" else "red"
        print(f"  bar={bar:4d} {label}({color:5s}) type={ttype:5s} ret={ret_pct:+.2f}% "
              f"reason={reason} {str(d)[:10]}")
    print()

    # ── Additional: compare with BS from all_pairs (fallback path) ──
    print("-" * 90)
    print("BONUS: BS markers from all_pairs only (fallback, no trade_records):")
    bs_pairs = dl["bs_from_pairs"]
    print(f"  Entry markers: {len(bs_pairs['entry_markers'])}")
    for idx, (bar, label, color, date) in enumerate(bs_pairs["entry_markers"]):
        in_long = bool(long_mask[bar]) if bar < len(long_mask) else False
        in_short = bool(short_mask[bar]) if bar < len(short_mask) else False
        aligned_ok = (label == "B" and in_long) or (label == "S" and in_short)
        print(f"    [{idx}] bar={bar:4d} {label}({color:5s}) {str(date)[:10]} "
              f"long={str(in_long):5s} short={str(in_short):5s} "
              f"{'ALIGNED' if aligned_ok else 'MISALIGNED'}")
    print(f"  Exit markers: {len(bs_pairs['exit_markers'])}")
    for idx, item in enumerate(bs_pairs["exit_markers"]):
        bar, label, color, xtype, date = item
        in_long = bool(long_mask[bar]) if bar < len(long_mask) else False
        in_short = bool(short_mask[bar]) if bar < len(short_mask) else False
        aligned_ok = (label == "S" and in_long) or (label == "B" and in_short)
        print(f"    [{idx}] bar={bar:4d} {label}({color:5s}) {xtype} {str(date)[:10]} "
              f"long={str(in_long):5s} short={str(in_short):5s} "
              f"{'ALIGNED' if aligned_ok else 'MISALIGNED'}")


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
