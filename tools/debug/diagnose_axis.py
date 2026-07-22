#!/usr/bin/env python3
"""
Axis diagnosis script for filter_research project.
Reproduces _render_chart's figure construction and checks all trace axis references.

Usage:
  cd /Users/xfpan/claude/filter_research
  PYTHONPATH=filter_app:$PYTHONPATH python3 diagnose_axis.py
"""

import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "filter_app"))

# ---- Mock streamlit before any project imports ----
import types
sm = types.ModuleType("streamlit")
class _Cache:
    def __call__(self, func=None, **kw):
        if func is not None:
            return func
        return lambda f: f
    def clear(self):
        pass
sm.cache_data = _Cache()
sm.cache_resource = _Cache()
sm.fragment = lambda f: f
sm.set_page_config = lambda **kw: None
sm.session_state = {}
sm.columns = lambda *a, **kw: [type("Col",(),{"caption":lambda s:None,"markdown":lambda s:None})() for _ in range(a[0])]
sm.caption = lambda s: None
sm.markdown = lambda s,**kw: None
sm.button = lambda *a,**kw: False
sm.checkbox = lambda *a,**kw: False
sm.selectbox = lambda *a,**kw: ""
sm.slider = lambda *a,**kw: 0
sm.expander = lambda *a,**kw: type("Exp",(),{"__enter__":lambda s:s,"__exit__":lambda s,*a:None})()
sm.warning = lambda s: None
sm.error = lambda s: None
sm.success = lambda s: None
sm.toast = lambda s: None
sm.spinner = lambda s: type("Sp",(),{"__enter__":lambda s:s,"__exit__":lambda s,*a:None})()
sm.rerun = lambda: None
sm.stop = lambda: None
sm.dataframe = lambda *a,**kw: None
sys.modules["streamlit"] = sm
# ---- end mock ----

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from services.filter_engine import (
    _schmitt_trigger, _find_all_pairs,
    _fit_physics_parabola, _compute_strategy_pnl,
    _compute_holding_masks,
)

from components.chart_builder import (
    _add_main_price_traces, _add_residual_traces,
    _add_schmitt_traces, _add_pnl_traces,
    _add_feedback_subplot, _determine_subplot_layout,
    _insert_feedback_row,
)

from components.charts import (
    _add_prediction_traces, _add_cross_pnl_subplot,
    _add_alignment_subplot,
)


# ====================================================================
# Helper: generate synthetic stock data
# ====================================================================
def generate_synthetic_data(n_bars=60):
    np.random.seed(42)
    t = np.arange(n_bars, dtype=float)
    returns = np.random.normal(0, 0.015, n_bars)
    price = 100 * np.exp(np.cumsum(returns))
    ohlc = pd.DataFrame({
        "Open": price * (1 + np.random.normal(0, 0.003, n_bars)),
        "High": price * (1 + np.abs(np.random.normal(0, 0.005, n_bars))),
        "Low":  price * (1 - np.abs(np.random.normal(0, 0.005, n_bars))),
        "Close": price,
    })
    return t, price, ohlc


# ====================================================================
# SMOOTH7 filter (from FILTERS registry, simplified)
# ====================================================================
def compute_savgol_filter(t, noisy, window=11, order=2):
    """Savitzky-Golay filter replicating filter_engine.apply_savgol."""
    if window % 2 == 0:
        window += 1
    window = min(window, len(noisy) - 1)
    from scipy.signal import savgol_filter
    return savgol_filter(noisy, window, order, mode="mirror")


# ====================================================================
# Core diagnostic builder
# ====================================================================
def build_and_diagnose(label, has_s, has_strategy, has_cross, has_alignment,
                       has_feedback, t, noisy, ohlc, filtered, filtered2,
                       schmitt, all_pairs, pred_pairs,
                       long_pnl, short_pnl, trade_records,
                       higher_pnl, _align_masks, cfg):
    """Replicate _render_chart Step 9-10 and diagnose axis references."""

    _higher_tf = "周线"

    rows, rh, titles, mr, rr, vr, sar, ssr, ar, pnl_row, cross_row, align_row = \
        _determine_subplot_layout(has_s, has_strategy, has_cross, has_alignment, _higher_tf)

    feedback_row = None
    if has_feedback and pnl_row is not None:
        rows_before = rows
        rows, rh, titles, feedback_row, cross_row, align_row = _insert_feedback_row(
            rows, rh, titles, pnl_row, cross_row, align_row)
        print(f"    _insert_feedback_row: rows {rows_before}->{rows}, "
              f"feedback_row={feedback_row}, cross_row={cross_row}, align_row={align_row}")

    print(f"  Layout: rows={rows}, rh={rh}")
    print(f"  mr={mr} rr={rr} vr={vr} sar={sar} ssr={ssr} ar={ar}")
    print(f"  pnl_row={pnl_row} feedback={feedback_row} cross={cross_row} align={align_row}")

    # --- Step 10: Build figure ---
    all_traces, all_shapes, all_annotations = [], [], []
    _layout_updates = {}
    acc = np.array([])

    # 1. Collect from _add_* functions
    all_traces += _add_main_price_traces(t, noisy, ohlc, filtered, filtered2, cfg, mr)

    for i, pp in enumerate(pred_pairs):
        all_traces += _add_prediction_traces(
            t, filtered, pp["fit_result"], pp["fit_start"], pp["pair_end"],
            row=mr, n_extend=cfg.get("n_ext", 10), show_legend=(i == 0))

    acc, tr, sh = _add_residual_traces(t, filtered, noisy, filtered2, cfg, rr, vr)
    all_traces += tr
    all_shapes += sh

    if has_s:
        tr, sh = _add_schmitt_traces(t, schmitt, acc, all_pairs, sar, ssr)
        all_traces += tr
        all_shapes += sh

    if has_strategy:
        tr, sh, an, ya = _add_pnl_traces(t, long_pnl, short_pnl, trade_records, pnl_row)
        all_traces += tr
        all_shapes += sh
        all_annotations += an
        _layout_updates.update(ya)

    if has_feedback and feedback_row is not None:
        sh, ya = _add_feedback_subplot(t, trade_records, feedback_row)
        all_shapes += sh
        _layout_updates.update(ya)

    if has_cross and higher_pnl is not None and cross_row is not None:
        sh, ya = _add_cross_pnl_subplot(t, higher_pnl, row=cross_row)
        all_shapes += sh
        _layout_updates.update(ya)

    if has_alignment and _align_masks is not None and align_row is not None:
        long_mask, short_mask = _align_masks
        tr, sh, an, ya = _add_alignment_subplot(
            t, long_pnl, short_pnl, trade_records,
            long_mask, short_mask, row=align_row)
        all_traces += tr
        all_shapes += sh
        all_annotations += an
        _layout_updates.update(ya)

    if ar is not None and not np.all(np.isnan(filtered)):
        all_traces.append(dict(type="scattergl", x=t, y=acc, mode="lines", name="a",
            line=dict(color="#ffa502", width=1.5), xaxis=f"x{ar}", yaxis=f"y{ar}"))
        all_shapes.append(dict(type="line", x0=0, x1=1, xref="paper", y0=0, y1=0,
            yref=f"y{ar}", line=dict(color="gray", dash="dash"), opacity=0.5))

    # 2. make_subplots skeleton
    _skeleton = make_subplots(rows=rows, cols=1, shared_xaxes=True,
        vertical_spacing=0.01, row_heights=rh, subplot_titles=titles)
    layout_dict = _skeleton.layout.to_plotly_json()

    # 3. shapes + annotations
    layout_dict["shapes"] = layout_dict.get("shapes", []) + all_shapes
    layout_dict["annotations"] = layout_dict.get("annotations", []) + all_annotations

    # 4. Axis customizations (matching _render_chart)
    layout_dict.setdefault("xaxis", {}).update(rangeslider_visible=False)
    for _r, _t in [(mr,"价格"),(rr,"残差"),(vr,"速度")]:
        _yk = "yaxis" if _r == 1 else f"yaxis{_r}"
        layout_dict.setdefault(_yk, {}).update(title_text=_t)
    if has_s:
        layout_dict.setdefault(f"yaxis{sar}", {}).update(title_text="a±ε")
        layout_dict.setdefault(f"yaxis{ssr}", {}).update(title_text="Sig",
            tickvals=[-1,0,1], ticktext=["空","观","多"], range=[-1.5,1.5])
    if ar is not None:
        layout_dict.setdefault(f"yaxis{ar}", {}).update(title_text="加速度")
    for k, v in _layout_updates.items():
        layout_dict.setdefault(k, {}).update(v)

    # 5. Build figure
    fig = go.Figure(data=all_traces, layout=layout_dict)

    # ============================================================
    # DIAGNOSE
    # ============================================================
    all_layout_axis_keys = set()
    for key in layout_dict:
        if key.startswith("xaxis") or key.startswith("yaxis"):
            all_layout_axis_keys.add(key)

    print(f"\n  Layout axis keys ({len(all_layout_axis_keys)}):")
    for k in sorted(all_layout_axis_keys):
        v = layout_dict.get(k, {})
        if isinstance(v, dict):
            dom = v.get("domain", "N/A")
            print(f"    {k}: domain={dom}")

    # Check every trace's axis references
    issues = []
    ok_count = 0
    print(f"\n  Trace axis check ({len(all_traces)} traces):")
    for i, tr in enumerate(all_traces):
        name = tr.get("name", f"trace_{i}")
        ttype = tr.get("type", "unknown")
        x_ax = tr.get("xaxis", None)
        y_ax = tr.get("yaxis", None)

        # Normalise axis ref to layout key
        x_key = f"xaxis{x_ax[1:]}" if x_ax and x_ax != "x" else "xaxis" if x_ax else None
        y_key = f"yaxis{y_ax[1:]}" if y_ax and y_ax != "y" else "yaxis" if y_ax else None

        x_ok = x_key in all_layout_axis_keys if x_key else True
        y_ok = y_key in all_layout_axis_keys if y_key else True

        if not x_ok or not y_ok:
            issues.append((i, name, ttype, x_ax, y_ax, x_key, y_key))
            print(f"    *** BAD *** [{i}] {name} ({ttype}) xaxis={x_ax}→{x_key} yaxis={y_ax}→{y_key}")
        else:
            ok_count += 1
            print(f"    OK [{i}] {name} ({ttype}) xaxis={x_ax} yaxis={y_ax}")

    return {
        "label": label,
        "rows": rows,
        "traces": len(all_traces),
        "ok": ok_count,
        "issues": issues,
        "layout_keys": all_layout_axis_keys,
        "layout_dict_snapshot": {k: dict(layout_dict[k]) if isinstance(layout_dict.get(k), dict) else layout_dict[k]
                                  for k in sorted(all_layout_axis_keys)},
        "fig": fig,
    }


# ====================================================================
# MAIN
# ====================================================================
def main():
    n_bars = 60
    t, noisy, ohlc = generate_synthetic_data(n_bars)

    # Filter
    filtered = compute_savgol_filter(t, noisy, window=11, order=2)
    filtered2 = None

    # Config (minimal — only what the _add_* functions actually read)
    cfg = {
        "fc": "#00d4aa", "fc2": "#ff6b6b", "_dual": False,
        "show_sch": True, "show_strategy": True,
        "show_cross_pnl": True, "show_alignment": True,
        "show_pnl_feedback": True, "n_ext": 10,
    }

    # --- Compute Schmitt trigger & strategy (the complex path) ---
    _v = np.gradient(filtered, t)
    _a = np.gradient(_v, t)
    schmitt = _schmitt_trigger(_v, _a, ewma_span=30, k_eps=0.15, sigma_min=0.05)
    has_s = schmitt is not None
    print(f"Schmitt trigger computed: has_s={has_s}, keys={list(schmitt.keys())}")

    all_pairs = _find_all_pairs(schmitt["sig"]) if has_s else []
    print(f"  pairs: {len(all_pairs)}")

    pred_pairs = []
    for pair_start, pair_end in all_pairs:
        if pair_end - pair_start >= 3:
            fit_result = _fit_physics_parabola(t, filtered, pair_start, pair_end)
            if fit_result is not None:
                pred_pairs.append({
                    "fit_result": fit_result,
                    "fit_start": pair_start,
                    "pair_end": pair_end,
                })
    print(f"  pred_pairs: {len(pred_pairs)}")

    long_pnl = short_pnl = None
    trade_records = []
    if len(pred_pairs) > 0:
        long_pnl, short_pnl, trade_records = _compute_strategy_pnl(
            t, filtered, schmitt["sig"], all_pairs, pred_pairs,
            stop_loss_pct=2.0, n_extend=10)
    has_strategy = long_pnl is not None and len(trade_records) > 0
    print(f"  has_strategy={has_strategy}, trades={len(trade_records)}")

    # --- Higher-period PnL (mock) ---
    higher_pnl = {
        "entry_markers": [(5, "long", 100.0), (20, "short", 100.0), (35, "long", 100.0)],
        "exit_markers":  [(15, "long", 105.0, 5.0, "take_profit"),
                          (30, "short", 95.0, -5.0, "stop_loss"),
                          (50, "long", 110.0, 10.0, "take_profit")],
    }
    _align_masks = None
    has_alignment = (True and has_strategy and long_pnl is not None)
    if has_alignment:
        _align_masks = _compute_holding_masks(
            len(t), higher_pnl["entry_markers"], higher_pnl["exit_markers"])
        has_alignment = _align_masks is not None and (
            _align_masks[0].any() or _align_masks[1].any())

    has_cross = True
    has_feedback = has_strategy

    # ===========================================================
    # TEST CASE 1: Full 8-row (has_s + strategy + cross + align + feedback)
    # ===========================================================
    print("\n" + "=" * 70)
    print("TEST CASE 1: Full config (has_s + strategy + cross + align + feedback)")
    print("=" * 70)

    r1 = build_and_diagnose(
        "full", has_s, has_strategy, has_cross, has_alignment, has_feedback,
        t, noisy, ohlc, filtered, filtered2,
        schmitt, all_pairs, pred_pairs,
        long_pnl, short_pnl, trade_records,
        higher_pnl, _align_masks, cfg,
    )

    # ===========================================================
    # TEST CASE 2: Without feedback row
    # ===========================================================
    print("\n" + "=" * 70)
    print("TEST CASE 2: Full config WITHOUT feedback row")
    print("=" * 70)

    r2 = build_and_diagnose(
        "no_feedback", has_s, has_strategy, has_cross, has_alignment, False,
        t, noisy, ohlc, filtered, filtered2,
        schmitt, all_pairs, pred_pairs,
        long_pnl, short_pnl, trade_records,
        higher_pnl, _align_masks, cfg,
    )

    # ===========================================================
    # TEST CASE 3: Without strategy (no cross/align/feedback)
    # ===========================================================
    print("\n" + "=" * 70)
    print("TEST CASE 3: has_s only (no strategy/cross/align/feedback)")
    print("=" * 70)

    r3 = build_and_diagnose(
        "schmitt_only", has_s, False, False, False, False,
        t, noisy, ohlc, filtered, filtered2,
        schmitt, all_pairs, pred_pairs,
        None, None, [],
        None, None, cfg,
    )

    # ===========================================================
    # TEST CASE 4: Without Schmitt (4-row layout)
    # ===========================================================
    print("\n" + "=" * 70)
    print("TEST CASE 4: No Schmitt (4-row: price+residual+velocity+acceleration)")
    print("=" * 70)

    r4 = build_and_diagnose(
        "no_schmitt", False, False, False, False, False,
        t, noisy, ohlc, filtered, filtered2,
        None, [], [], None, None, [],
        None, None, cfg,
    )

    # ===========================================================
    # SUMMARY
    # ===========================================================
    results = [r1, r2, r3, r4]
    total_issues = sum(len(r["issues"]) for r in results)
    total_traces = sum(r["traces"] for r in results)
    total_ok = sum(r["ok"] for r in results)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for r in results:
        status = "*** ISSUES ***" if r["issues"] else "ALL OK"
        print(f"  {r['label']}: {r['traces']} traces, {r['ok']} ok, "
              f"{len(r['issues'])} bad  → {status}")
        for iss in r["issues"]:
            print(f"    Trace [{iss[0]}] '{iss[1]}' ({iss[2]}): xaxis={iss[3]}→{iss[5]} yaxis={iss[4]}→{iss[6]}")

    print(f"\n  TOTAL: {total_traces} traces, {total_ok} OK, {total_issues} BAD")

    # Also check yaxis domains for the full case
    print("\n" + "=" * 70)
    print("YAXIS DOMAIN ANALYSIS (full case)")
    print("=" * 70)
    ld = r1["layout_dict_snapshot"]
    for k in sorted(ld):
        if k.startswith("yaxis"):
            dom = ld[k].get("domain", "N/A")
            tt = ld[k].get("title_text", "")
            print(f"  {k}: domain={dom}  title={tt}")

    # Save HTML for visual inspection
    r1["fig"].write_html("/tmp/axis_diag_full.html")
    r2["fig"].write_html("/tmp/axis_diag_no_feedback.html")
    r3["fig"].write_html("/tmp/axis_diag_schmitt_only.html")
    r4["fig"].write_html("/tmp/axis_diag_no_schmitt.html")
    print("\nHTML figures saved to /tmp/axis_diag_*.html")

    return results


if __name__ == "__main__":
    results = main()
