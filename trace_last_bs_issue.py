"""
Trace the last few BS markers on 日线 for ticker 3690 to investigate
"last BS point is abnormal — trend not ended but BS marked at the end".

Scenario: operating TF = 日线 (日线's own trade_records → BS markers)
"""

import sys, os, io
import sqlite3
import numpy as np
import pandas as pd
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from filter_app.services.filter_engine import (
    apply_ema, _schmitt_trigger, _find_all_pairs,
    _fit_physics_parabola, _compute_strategy_pnl,
    _compute_holding_masks,
)
from filter_app.services.bs_marker import compute_bs_markers

DB_PATH = "/Users/xfpan/claude/filter_research/data/market.db"
OUTPUT_DIR = os.path.expanduser("~/.claude/orchestrator/output/orch-20260712-154932-59146")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "last-bs-trace.txt")

# ── Params (matching 3690 日线 preset) ──
EWMA_SPAN = 60
K_EPS = 0.15
SIGMA_MIN = 0.05
EMA_SPAN = 10
N_EXTEND = 8
STOP_LOSS_PCT = 2.0


def load_kline(ticker, timeframe, limit=500, latest=True):
    conn = sqlite3.connect(DB_PATH)
    if latest:
        # Load most recent `limit` bars
        # First get the last N bars by descending then reverse
        df = pd.read_sql_query(
            """SELECT ts, open, high, low, close FROM (
                   SELECT * FROM kline
                   WHERE ticker=? AND timeframe=?
                   ORDER BY ts DESC LIMIT ?
               ) ORDER BY ts ASC""",
            conn, params=(ticker, timeframe, limit),
        )
    else:
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


def main():
    print("=" * 90)
    print("  TRACE: Last BS Markers Investigation — 3690 日线")
    print("=" * 90)
    print()
    print(f"Ticker: 3690 | Operating TF: 日线 (日线 is the operating TF)")
    print(f"Schmitt: ewma_span={EWMA_SPAN}, k_eps={K_EPS}, sigma_min={SIGMA_MIN}")
    print(f"Filter: EMA(span={EMA_SPAN})")
    print(f"Strategy: stop_loss={STOP_LOSS_PCT}%, n_extend={N_EXTEND}")
    print()

    # ═══════════════════════════════════════════════════════════
    # STEP 1: Load data
    # ═══════════════════════════════════════════════════════════
    print("=" * 90)
    print("STEP 1: Data Loading")
    print("=" * 90)

    df = load_kline("3690", "日线", limit=200, latest=True)
    print(f"日线: {len(df)} bars, [{str(df.index[0])[:10]} .. {str(df.index[-1])[:10]}]")
    print()

    # ═══════════════════════════════════════════════════════════
    # STEP 2: Run pipeline on 日线
    # ═══════════════════════════════════════════════════════════
    print("=" * 90)
    print("STEP 2: Run Full Pipeline on 日线")
    print("=" * 90)

    n = len(df)
    t = np.arange(n, dtype=float)
    noisy = df["close"].values.astype(float)
    dates = pd.DatetimeIndex(df.index)

    filtered = apply_ema(noisy, t, span=EMA_SPAN)

    v = np.gradient(filtered, t)
    a = np.gradient(v, t)
    schmitt = _schmitt_trigger(v, a, ewma_span=EWMA_SPAN, k_eps=K_EPS, sigma_min=SIGMA_MIN)

    if schmitt is None:
        print("ERROR: 日线 Schmitt trigger returned None (not enough bars)")
        return

    all_pairs = _find_all_pairs(schmitt["sig"])
    print(f"all_pairs: {len(all_pairs)}")
    for i, (ps, pe) in enumerate(all_pairs):
        sig_dir = "LONG" if schmitt["sig"][pe] == 1 else "SHORT"
        print(f"  pair[{i}] start={ps} end={pe} dir={sig_dir} "
              f"[{str(dates[ps])[:10]} → {str(dates[pe])[:10]}]")

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

    print(f"\ntrade_records: {len(trade_records)}")
    print()

    # ═══════════════════════════════════════════════════════════
    # STEP 3: Print ALL trade_records
    # ═══════════════════════════════════════════════════════════
    print("=" * 90)
    print("STEP 3: ALL Trade Records")
    print("=" * 90)
    for tr in trade_records:
        entry_date = dates[tr["entry_idx"]] if tr["entry_idx"] < n else "?"
        exit_date = dates[tr["exit_idx"]] if tr["exit_idx"] < n else "?"
        print(f"  id={tr['id']:2d} {tr['type']:5s} "
              f"entry_idx={tr['entry_idx']:4d} exit_idx={tr['exit_idx']:4d} "
              f"ret={tr['return_pct']:+7.2f}% reason={tr['exit_reason']:12s} "
              f"[{str(entry_date)[:10]} → {str(exit_date)[:10]}]")
    print()

    # ═══════════════════════════════════════════════════════════
    # STEP 4: Focus on LAST 5 trades — detailed analysis
    # ═══════════════════════════════════════════════════════════
    print("=" * 90)
    print("STEP 4: LAST 5 TRADES — Detailed Analysis")
    print("=" * 90)

    last_trades = trade_records[-5:] if len(trade_records) >= 5 else trade_records
    for tr in last_trades:
        print(f"\n  --- Trade id={tr['id']} ---")
        print(f"  type:        {tr['type']}")
        print(f"  entry_idx:   {tr['entry_idx']}")
        print(f"  exit_idx:    {tr['exit_idx']}")
        print(f"  return_pct:  {tr['return_pct']:+.2f}%")
        print(f"  exit_reason: {tr['exit_reason']}")

        entry_date = dates[tr["entry_idx"]] if tr["entry_idx"] < n else None
        exit_date = dates[tr["exit_idx"]] if tr["exit_idx"] < n else None
        print(f"  entry_date:  {str(entry_date)[:10]}")
        print(f"  exit_date:   {str(exit_date)[:10]}")

        # Schmitt sig at exit bar
        sig_at_exit = schmitt["sig"][tr["exit_idx"]]
        sig_desc = {1: "+1 (LONG)", -1: "-1 (SHORT)", 0: "0 (观望)"}
        print(f"  Schmitt sig at exit bar [{tr['exit_idx']}]: {sig_desc.get(sig_at_exit, str(sig_at_exit))}")

        # Schmitt sig one bar before exit (to check if it just flipped)
        if tr["exit_idx"] > 0:
            sig_before = schmitt["sig"][tr["exit_idx"] - 1]
            print(f"  Schmitt sig at bar before exit [{tr['exit_idx']-1}]: {sig_desc.get(sig_before, str(sig_before))}")

        # Schmitt sig at last bar (end of data)
        sig_end = schmitt["sig"][-1]
        print(f"  Schmitt sig at END of data [bar {n-1}]: {sig_desc.get(sig_end, str(sig_end))}")

        # Check: if exit_reason is eod AND sig is same as entry direction
        is_long = tr["type"] == "long"
        trend_continues = (is_long and sig_at_exit == 1) or (not is_long and sig_at_exit == -1)

        if tr["exit_reason"] == "eod":
            print(f"  ** This is an EOD (End Of Data) exit — forced close at data boundary **")
            if trend_continues:
                print(f"  ⚠️  WARNING: Trend NOT ended! Sig still {sig_desc[sig_at_exit]} at exit bar.")
                print(f"      BS exit marker at bar {tr['exit_idx']} is MISLEADING — the trade was")
                print(f"      only closed because data ended, not because the trend reversed.")
            else:
                print(f"  Trend appears to have ended (sig flipped or neutral). OK.")
        else:
            if trend_continues and sig_at_exit != 0:
                # This shouldn't normally happen for take_profit/stop_loss
                pass

    print()

    # ═══════════════════════════════════════════════════════════
    # STEP 5: BS Markers computation (日线 as operating TF)
    # ═══════════════════════════════════════════════════════════
    print("=" * 90)
    print("STEP 5: BS Markers — 日线 as Operating TF")
    print("=" * 90)
    print("(holding_masks=None because 日线 IS the operating TF — no higher TF)")
    print()

    # compute_bs_markers with holding_masks=None (日线 is operating TF, no higher tf)
    bs_markers = compute_bs_markers(
        t, dates, schmitt, all_pairs, trade_records,
        tf="日线", operating_tf="日线", higher_bs=None,
        holding_masks=None
    )

    print(f"Entry markers: {len(bs_markers['entry_markers'])}")
    for m in bs_markers['entry_markers']:
        bar, label, color, date = m
        print(f"  ENTRY bar={bar:4d} {label}({color:5s}) date={str(date)[:10]}")

    print(f"\nExit markers: {len(bs_markers['exit_markers'])}")
    for m in bs_markers['exit_markers']:
        bar, label, color, reason, date = m
        print(f"  EXIT  bar={bar:4d} {label}({color:5s}) reason={reason:12s} date={str(date)[:10]}")

    # ═══════════════════════════════════════════════════════════
    # STEP 6: Last 5 BS Markers
    # ═══════════════════════════════════════════════════════════
    print()
    print("=" * 90)
    print("STEP 6: Last 5 BS Markers")
    print("=" * 90)

    print("\nEntry markers (last 5):")
    for m in bs_markers['entry_markers'][-5:]:
        bar, label, color, date = m
        sig_val = schmitt["sig"][bar] if bar < n else "?"
        print(f"  ENTRY bar={bar:4d} {label}({color:5s}) schmitt_sig={sig_val} date={str(date)[:10]}")

    print("\nExit markers (last 5):")
    for m in bs_markers['exit_markers'][-5:]:
        bar, label, color, reason, date = m
        sig_val = schmitt["sig"][bar] if bar < n else "?"
        print(f"  EXIT  bar={bar:4d} {label}({color:5s}) reason={reason:12s} schmitt_sig={sig_val} date={str(date)[:10]}")

    # ═══════════════════════════════════════════════════════════
    # STEP 7: Schmitt signal at the last few bars
    # ═══════════════════════════════════════════════════════════
    print()
    print("=" * 90)
    print("STEP 7: Schmitt Signal Trace — Last 20 bars")
    print("=" * 90)
    print()
    print(f"{'bar':>5s} {'date':>12s} {'sig':>4s} {'close':>8s} {'filtered':>8s} {'a(accel)':>10s} {'eps':>10s}")
    print("-" * 65)
    for i in range(max(0, n - 20), n):
        sig_val = schmitt["sig"][i]
        sig_str = {1: " +1", -1: " -1", 0: "  0"}.get(sig_val, f"{sig_val:3d}")
        print(f"  {i:4d} {str(dates[i])[:10]} {sig_str:>4s} "
              f"{noisy[i]:8.2f} {filtered[i]:8.2f} "
              f"{a[i]:10.4f} {schmitt['eps'][i]:10.4f}")

    # ═══════════════════════════════════════════════════════════
    # STEP 8: Analysis — Last trade & last BS exit marker
    # ═══════════════════════════════════════════════════════════
    print()
    print("=" * 90)
    print("STEP 8: ANALYSIS — Root Cause Investigation")
    print("=" * 90)
    print()

    last_trade = trade_records[-1] if trade_records else None
    if last_trade:
        print(f"LAST TRADE: id={last_trade['id']}, type={last_trade['type']}, "
              f"entry={last_trade['entry_idx']}, exit={last_trade['exit_idx']}, "
              f"reason={last_trade['exit_reason']}, ret={last_trade['return_pct']:+.2f}%")

        exit_idx = last_trade["exit_idx"]
        is_long = last_trade["type"] == "long"
        sig_at_exit = schmitt["sig"][exit_idx]
        sig_at_end = schmitt["sig"][-1]

        print(f"\nQ1: Is the last exit marker from an 'eod' trade?")
        if last_trade["exit_reason"] == "eod":
            print(f"    YES — exit_reason is 'eod' (forced close at end of data, bar {exit_idx})")
        else:
            print(f"    NO — exit_reason is '{last_trade['exit_reason']}'")

        print(f"\nQ2: Does the Schmitt signal indicate trend is still ongoing at exit bar?")
        if is_long and sig_at_exit == 1:
            print(f"    YES — sig={sig_at_exit} at exit bar {exit_idx}, LONG trend still active")
        elif not is_long and sig_at_exit == -1:
            print(f"    YES — sig={sig_at_exit} at exit bar {exit_idx}, SHORT trend still active")
        elif sig_at_exit == 0:
            print(f"    NO — sig=0 (观望/neutral), trend has ended")
        else:
            print(f"    NO — sig flipped to opposite direction ({sig_at_exit})")

        print(f"\nQ3: What is the Schmitt signal at the very last bar of data?")
        sig_end_desc = {1: "+1 (LONG 趋势进行中)", -1: "-1 (SHORT 趋势进行中)", 0: "0 (观望/无趋势)"}
        print(f"    bar[{n-1}] sig={sig_at_end}: {sig_end_desc.get(sig_at_end, str(sig_at_end))}")

        # Check if exit event actually triggered a sig flip
        was_flipped = False
        if is_long and sig_at_exit == -1:
            was_flipped = True
        elif not is_long and sig_at_exit == 1:
            was_flipped = True

        print(f"\nQ4: Was the exit triggered by a signal flip (take_profit) or eod?")
        if last_trade["exit_reason"] == "take_profit":
            print(f"    take_profit — sig flipped at bar {exit_idx}")
            if was_flipped:
                print(f"    Confirmed: sig flipped to opposite direction.")
        elif last_trade["exit_reason"] == "stop_loss":
            print(f"    stop_loss — price hit stop at bar {exit_idx}")
        elif last_trade["exit_reason"] == "eod":
            print(f"    eod — forced close at end of data, sig = {sig_at_exit}")
            if is_long and sig_at_exit == 1:
                print(f"    ⚠️  This is the BUG: Long trend still active (sig=+1) but")
                print(f"        trade was forced closed at bar {exit_idx} (end of data).")
                print(f"        The BS exit marker S(green) at bar {exit_idx} falsely suggests")
                print(f"        the long trend ended here, when it was just data ending.")
            elif not is_long and sig_at_exit == -1:
                print(f"    ⚠️  This is the BUG: Short trend still active (sig=-1) but")
                print(f"        trade was forced closed at bar {exit_idx} (end of data).")
                print(f"        The BS exit marker B(red) at bar {exit_idx} falsely suggests")
                print(f"        the short trend ended here, when it was just data ending.")

        print(f"\nQ5: Should this exit BS marker be shown?")
        if last_trade["exit_reason"] == "eod" and (
            (is_long and sig_at_exit == 1) or (not is_long and sig_at_exit == -1)
        ):
            print(f"    RECOMMENDATION: NO — the eod exit marker should be SUPPRESSED")
            print(f"    when the trend signal has not reversed. The position is still")
            print(f"    'open' from the Schmitt trigger's perspective. Showing a BS exit")
            print(f"    marker here is misleading to traders.")
        else:
            print(f"    YES — the exit was triggered by a legitimate signal (price flip or stop).")

    # ═══════════════════════════════════════════════════════════
    # STEP 9: Summary of all trades with eod + trend mismatch
    # ═══════════════════════════════════════════════════════════
    print()
    print("=" * 90)
    print("STEP 9: All EOD Trades — Trend Status Check")
    print("=" * 90)
    eod_issues = []
    for tr in trade_records:
        if tr["exit_reason"] == "eod":
            is_long = tr["type"] == "long"
            sig_at_exit = schmitt["sig"][tr["exit_idx"]]
            trend_ok = (is_long and sig_at_exit == 1) or (not is_long and sig_at_exit == -1)
            eod_issues.append((tr, trend_ok, sig_at_exit))

    if eod_issues:
        for tr, trend_ok, sig in eod_issues:
            status = "⚠️ MISLEADING" if trend_ok else "OK (trend ended)"
            d_entry = str(dates[tr["entry_idx"]])[:10]
            d_exit = str(dates[tr["exit_idx"]])[:10]
            print(f"  id={tr['id']:2d} {tr['type']:5s} [{d_entry} → {d_exit}] "
                  f"sig_at_exit={sig:2d} ret={tr['return_pct']:+.2f}% → {status}")
    else:
        print("  No EOD trades found.")

    # ═══════════════════════════════════════════════════════════
    # STEP 10: Show what the last Schmitt pair looks like (unfinished)
    # ═══════════════════════════════════════════════════════════
    print()
    print("=" * 90)
    print("STEP 10: Last Schmitt Pair Status")
    print("=" * 90)
    if all_pairs:
        last_pair = all_pairs[-1]
        ps, pe = last_pair
        sig_dir = "LONG(做多)" if schmitt["sig"][pe] == 1 else "SHORT(做空)"
        print(f"  Last pair: entry bar={ps}, exit(flip) bar={pe}, dir={sig_dir}")
        print(f"  entry date: {str(dates[ps])[:10]}")
        print(f"  exit date:  {str(dates[pe])[:10]}")
        print(f"  Last bar:   {str(dates[-1])[:10]}")
        final_sig = schmitt["sig"][-1]
        if final_sig == 0:
            print(f"  Status: Trend ENDED — sig=0 at last bar")
        elif final_sig == 1:
            print(f"  Status: LONG trend ACTIVE at last bar — awaiting next pair")
        elif final_sig == -1:
            print(f"  Status: SHORT trend ACTIVE at last bar — awaiting next pair")
    print()


if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)

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
