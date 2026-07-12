#!/usr/bin/env python3
"""Analyze captured backtest pipeline data.

Usage:
    python tools/analyze_captured_backtest.py --session-dir <path> [options]

Options:
    --session-dir PATH      Path to capture session directory (required)
    --step-range N M        Analyze steps N to M (inclusive, default: all)
    --view VIEW             View name to analyze (default: all views)
    --output-dir PATH       Output directory for reports (default: session_dir/analysis/)
    --diff-threshold FLOAT  Minimum change to flag as significant (default: 0.0)
    --format TEXT|JSON      Output format (default: TEXT)

Dependencies: numpy, pandas (standard library + project deps)
"""

import argparse, json, sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

# ── Stage file registry: key → (filename, loader) ──
_STAGES = [
    ("raw",        "stage_01_raw.parquet",     "parquet"),
    ("markers",    "stage_02_markers.json",     "json"),
    ("filter",     "stage_03_filter.parquet",   "parquet"),
    ("schmitt",    "stage_04_schmitt.parquet",  "parquet"),
    ("pairs",      "stage_05_pairs.json",       "json"),
    ("prediction", "stage_06_prediction.json",  "json"),
    ("pnl",        "stage_07_pnl.parquet",      "parquet"),
    ("trades",     "stage_07_trades.json",      "json"),
    ("higher_pnl", "stage_08_higher_pnl.json",  "json"),
    ("masks",      "stage_09_masks.parquet",    "parquet"),
    ("bs_markers", "stage_10_bs_markers.json",  "json"),
]


@dataclass
class StepDiff:
    """Diff between two consecutive steps for a single view."""
    step_a: int; step_b: int; cutoff_a: str; cutoff_b: str; view: str
    filter_max_diff: float = 0.0; filter_mean_diff: float = 0.0
    filter_affected_indices: list = field(default_factory=list)
    schmitt_eps_max_diff: float = 0.0; schmitt_eps_mean_diff: float = 0.0
    schmitt_eps_affected_indices: list = field(default_factory=list)
    schmitt_sig_flips: list = field(default_factory=list)
    trade_count_a: int = 0; trade_count_b: int = 0
    trade_changes: list = field(default_factory=list)
    mask_changes: list = field(default_factory=list)
    bs_entry_count_a: int = 0; bs_entry_count_b: int = 0
    bs_exit_count_a: int = 0; bs_exit_count_b: int = 0
    bs_marker_changes: list = field(default_factory=list)
    cascade_amplification: Optional[dict] = None

    @property
    def is_stable(self) -> bool:
        """Stable = no downstream structural changes (sig flips, trade/BS changes)."""
        return (len(self.schmitt_sig_flips) == 0 and len(self.trade_changes) == 0
                and len(self.bs_marker_changes) == 0)

    @property
    def has_structural_jump(self) -> bool:
        return len(self.trade_changes) > 0 or len(self.bs_marker_changes) > 0

    @property
    def bs_delta(self) -> int:
        return self.bs_entry_count_b - self.bs_entry_count_a


# ══════════════════════════════════════════════════════════════════════════
# Loading
# ══════════════════════════════════════════════════════════════════════════

def _try_load(path: Path, kind: str) -> Any:
    """Load JSON or parquet, returning empty on error/missing."""
    try:
        if not path.exists():
            return {} if kind == "json" else pd.DataFrame()
        if kind == "json":
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return pd.read_parquet(path)
    except Exception:
        return {} if kind == "json" else pd.DataFrame()


def load_session_metadata(session_dir: str) -> dict:
    """Parse metadata.json; returns {} if missing."""
    return _try_load(Path(session_dir) / "metadata.json", "json")


def load_step_index(step_dir: Path) -> dict:
    """Load step-level index.json."""
    return _try_load(step_dir / "index.json", "json")


def load_step_view(view_dir: Path) -> dict:
    """Load all stage files for one view. Missing files → empty dict/DataFrame."""
    return {key: _try_load(view_dir / fname, kind) for key, fname, kind in _STAGES}


def _discover_views(session_dir: Path) -> list[str]:
    """Return sorted view names found under the first step directory."""
    steps_dir = session_dir / "steps"
    if not steps_dir.exists():
        return []
    first = next((d for d in sorted(steps_dir.iterdir()) if d.is_dir()), None)
    return sorted(d.name for d in first.iterdir() if d.is_dir()) if first else []


def _discover_step_range(session_dir: Path) -> tuple[int, int]:
    """Return (min_step, max_step) from steps/ directory listing."""
    steps_dir = session_dir / "steps"
    if not steps_dir.exists():
        return 0, -1
    nums = []
    for d in steps_dir.iterdir():
        if d.is_dir():
            try: nums.append(int(d.name))
            except ValueError: pass
    return (min(nums), max(nums)) if nums else (0, -1)


# ══════════════════════════════════════════════════════════════════════════
# Step Diff
# ══════════════════════════════════════════════════════════════════════════

def _col(df: pd.DataFrame, name: str) -> np.ndarray:
    """Safely extract column as numpy array, or empty array."""
    if df.empty or name not in df.columns:
        return np.array([])
    return df[name].to_numpy()


def _arr_diff(a: np.ndarray, b: np.ndarray, threshold: float) -> dict:
    """{max_diff, mean_diff, affected_indices} for two 1-D arrays."""
    if len(a) == 0 or len(b) == 0:
        return {"max_diff": 0.0, "mean_diff": 0.0, "affected_indices": []}
    n = min(len(a), len(b))
    d = np.abs(a[:n].astype(float) - b[:n].astype(float))
    md, ad = float(np.max(d)), float(np.mean(d))
    aff = np.where(d > max(threshold, 0))[0].tolist() if md > 0 else []
    return {"max_diff": md, "mean_diff": ad, "affected_indices": aff}


def _compact_ranges(ix: list[int]) -> str:
    """Compact sorted ints: [4,5,6,10,12] → \"[4-6,10,12]\"."""
    if not ix: return "[]"
    parts, s, e = [], ix[0], ix[0]
    for i in ix[1:]:
        if i == e + 1: e = i
        else:
            parts.append(f"{s}" if s == e else f"{s}-{e}")
            s = e = i
    parts.append(f"{s}" if s == e else f"{s}-{e}")
    return "[" + ",".join(parts) + "]"


def _compare_trades(ta: list, tb: list) -> tuple[int, int, list[str]]:
    """Compare trade record lists; returns (cnt_a, cnt_b, change_msgs)."""
    ia = {t["id"]: t for t in (ta or []) if "id" in t}
    ib = {t["id"]: t for t in (tb or []) if "id" in t}
    changes = []
    for tid in sorted(set(ia) - set(ib)):
        changes.append(f"trade #{tid} ({ia[tid].get('type','?')}) DISAPPEARED")
    for tid in sorted(set(ib) - set(ia)):
        changes.append(f"trade #{tid} ({ib[tid].get('type','?')}) APPEARED")
    for tid in sorted(set(ia) & set(ib)):
        diffs = [f"{k}: {ia[tid].get(k)} -> {ib[tid].get(k)}"
                 for k in ["entry_idx","exit_idx","entry_price","exit_price","return_pct","exit_reason"]
                 if ia[tid].get(k) != ib[tid].get(k)]
        if diffs:
            changes.append(f"trade #{tid} ({ia[tid].get('type','?')}): " + ", ".join(diffs))
    return len(ta or []), len(tb or []), changes


def _compare_bs_markers(ea: list, xa: list, eb: list, xb: list) -> dict:
    """Compare BS markers; entry=[idx,dir,color,date], exit=[idx,dir,color,reason,date]."""
    ek = lambda lst, kind: {(str(m[3 if kind=="entry" else 4]), kind) for m in (lst or []) if len(m) >= (4 if kind=="entry" else 5)}
    changes = []
    for kind, aa, bb in [("entry", ea, eb), ("exit", xa, xb)]:
        disp = ek(aa, kind) - ek(bb, kind)
        appr = ek(bb, kind) - ek(aa, kind)
        for date, _ in sorted(disp):
            m = next((x for x in (aa or []) if str(x[3 if kind=="entry" else 4]) == date), ["?","?"])
            changes.append(f"{kind.title()} {m[1]}@{date[:10]} DISAPPEARED")
        for date, _ in sorted(appr):
            m = next((x for x in (bb or []) if str(x[3 if kind=="entry" else 4]) == date), ["?","?"])
            changes.append(f"{kind.title()} {m[1]}@{date[:10]} APPEARED")
    return {"entry_count_a": len(ea or []), "entry_count_b": len(eb or []),
            "exit_count_a": len(xa or []), "exit_count_b": len(xb or []), "changes": changes}


def compute_step_diff(
    step_a: dict, step_b: dict, meta_a: dict, meta_b: dict, threshold: float = 0.0,
) -> StepDiff:
    """Compare two loaded step-views across all pipeline stages."""
    view = (meta_a.get("views_captured") or ["?"])[0]
    diff = StepDiff(
        step_a=meta_a.get("step_index", -1), step_b=meta_b.get("step_index", -1),
        cutoff_a=str(meta_a.get("cutoff_date", "?")), cutoff_b=str(meta_b.get("cutoff_date", "?")),
        view=view,
    )
    # Stage 03: Filter
    fa, fb = _col(step_a.get("filter", pd.DataFrame()), "filtered"), _col(step_b.get("filter", pd.DataFrame()), "filtered")
    fd = _arr_diff(fa, fb, threshold)
    diff.filter_max_diff, diff.filter_mean_diff, diff.filter_affected_indices = fd["max_diff"], fd["mean_diff"], fd["affected_indices"]

    # Stage 04: Schmitt eps & sig
    ea = _col(step_a.get("schmitt", pd.DataFrame()), "eps"); eb = _col(step_b.get("schmitt", pd.DataFrame()), "eps")
    ed = _arr_diff(ea, eb, threshold)
    diff.schmitt_eps_max_diff, diff.schmitt_eps_mean_diff, diff.schmitt_eps_affected_indices = ed["max_diff"], ed["mean_diff"], ed["affected_indices"]

    sa = _col(step_a.get("schmitt", pd.DataFrame()), "sig").astype(int)
    sb = _col(step_b.get("schmitt", pd.DataFrame()), "sig").astype(int)
    n = min(len(sa), len(sb))
    diff.schmitt_sig_flips = np.where(sa[:n] != sb[:n])[0].tolist() if n > 0 else []

    # Stage 07: Trade records
    ta = step_a.get("trades", {}).get("trade_records", [])
    tb = step_b.get("trades", {}).get("trade_records", [])
    diff.trade_count_a, diff.trade_count_b, diff.trade_changes = _compare_trades(ta, tb)

    # Stage 09: Holding masks
    mf = step_a.get("masks", pd.DataFrame()); mt = step_b.get("masks", pd.DataFrame())
    for name in ["long_mask", "short_mask"]:
        ma, mb = _col(mf, name).astype(bool), _col(mt, name).astype(bool)
        if len(ma) > 0 and len(mb) > 0:
            ix = np.where(ma[:min(len(ma),len(mb))] != mb[:min(len(ma),len(mb))])[0]
            if len(ix) > 0:
                diff.mask_changes.append(f"{name} shifted at {_compact_ranges(ix.tolist())}")

    # Stage 10: BS Markers
    bs_a, bs_b = step_a.get("bs_markers", {}), step_b.get("bs_markers", {})
    bc = _compare_bs_markers(bs_a.get("entry_markers",[]), bs_a.get("exit_markers",[]),
                              bs_b.get("entry_markers",[]), bs_b.get("exit_markers",[]))
    diff.bs_entry_count_a, diff.bs_entry_count_b = bc["entry_count_a"], bc["entry_count_b"]
    diff.bs_exit_count_a, diff.bs_exit_count_b = bc["exit_count_a"], bc["exit_count_b"]
    diff.bs_marker_changes = bc["changes"]

    # Cascade amplification
    if diff.filter_max_diff > 0 and diff.filter_affected_indices:
        nf = max(len(fa), len(fb))
        tail = [i for i in diff.filter_affected_indices if nf > 0 and i >= int(nf * 0.9)]
        if tail:
            ca = {"tail_filter_max_diff": diff.filter_max_diff, "tail_affected_count": len(tail),
                  "schmitt_eps_max_diff": diff.schmitt_eps_max_diff,
                  "schmitt_sig_flips_count": len(diff.schmitt_sig_flips),
                  "trade_change_count": len(diff.trade_changes),
                  "bs_marker_change_count": len(diff.bs_marker_changes)}
            if diff.schmitt_eps_max_diff > 0:
                ca["eps_amplification"] = round(diff.schmitt_eps_max_diff / diff.filter_max_diff, 2)
            if diff.bs_delta != 0:
                ca["bs_marker_amplification"] = round(abs(diff.bs_delta) / diff.filter_max_diff, 0)
            diff.cascade_amplification = ca

    return diff


# ══════════════════════════════════════════════════════════════════════════
# BS Marker Persistence
# ══════════════════════════════════════════════════════════════════════════

def analyze_bs_stability(session_dir: str, step_range: tuple, view: str) -> dict:
    """Track BS markers across steps; returns {markers, total_steps, unstable_count}."""
    steps_dir = Path(session_dir) / "steps"
    start, end = step_range
    marker_steps: dict = defaultdict(set)   # (date, type) -> {step indices}
    marker_info: dict = {}

    for si in range(start, end + 1):
        bs = _try_load(steps_dir / f"{si:06d}" / view / "stage_10_bs_markers.json", "json")
        if not bs: continue
        for em in bs.get("entry_markers", []):
            if len(em) < 4: continue
            key = (str(em[3]), "entry")
            marker_steps[key].add(si)
            if key not in marker_info:
                marker_info[key] = {"date": str(em[3]), "direction": str(em[1]), "type": "entry",
                                     "bar_idx": int(em[0]), "color": str(em[2]) if len(em) > 2 else "?"}
        for xm in bs.get("exit_markers", []):
            if len(xm) < 5: continue
            key = (str(xm[4]), "exit")
            marker_steps[key].add(si)
            if key not in marker_info:
                marker_info[key] = {"date": str(xm[4]), "direction": str(xm[1]), "type": "exit",
                                     "bar_idx": int(xm[0]), "color": str(xm[2]) if len(xm) > 2 else "?",
                                     "exit_reason": str(xm[3]) if len(xm) > 3 else "?"}

    total = end - start + 1
    records = sorted(({**marker_info[k], "first_seen_step": min(v), "last_seen_step": max(v),
                        "persistence": len(v), "unstable": len(v) < total} for k, v in marker_steps.items()),
                     key=lambda r: (r["date"], r["type"]))
    return {"markers": records, "total_steps": total, "unstable_count": sum(1 for r in records if r["unstable"])}


# ══════════════════════════════════════════════════════════════════════════
# Schmitt Signal Stability
# ══════════════════════════════════════════════════════════════════════════

def analyze_schmitt_changes(session_dir: str, step_range: tuple, view: str) -> dict:
    """Track pair count across steps; returns {changes, pair_counts, total_changed_steps, total_steps}."""
    steps_dir = Path(session_dir) / "steps"
    start, end = step_range
    changes, prev = [], None
    for si in range(start, end + 1):
        pairs = _try_load(steps_dir / f"{si:06d}" / view / "stage_05_pairs.json", "json")
        if not pairs: continue
        cnt = pairs.get("pair_count", len(pairs.get("all_pairs", [])))
        if prev is not None and cnt != prev:
            changes.append({"step": si, "prev_count": prev, "new_count": cnt, "delta": cnt - prev})
        prev = cnt
    return {"changes": changes, "total_changed_steps": len(changes), "total_steps": end - start + 1}


# ══════════════════════════════════════════════════════════════════════════
# Report Generation
# ══════════════════════════════════════════════════════════════════════════

def _sd(s: str) -> str:
    """Extract YYYY-MM-DD from ISO string."""
    return s[:10] if s and len(s) >= 10 else (s or "?")


def generate_summary_report(session_dir: str, diffs: list, bs_stability: dict,
                            schmitt_stability: dict, fmt: str = "TEXT") -> str:
    """Produce final report in TEXT or JSON format."""
    if fmt.upper() == "JSON":
        return _json_report(session_dir, diffs, bs_stability, schmitt_stability)
    return _text_report(session_dir, diffs, bs_stability, schmitt_stability)


def _text_report(session_dir: str, diffs: list, bs_stab: dict, sc: dict) -> str:
    meta = load_session_metadata(session_dir)
    sid = meta.get("session_id", Path(session_dir).name)
    L, S = [], "=" * 65
    L += [S, f"  Backtest Pipeline Capture Analysis",
          f"  Session: {sid}  Ticker: {meta.get('ticker','?')}  Steps: {meta.get('step_count',len(diffs)+1)}", S, ""]

    # ── Step-by-Step Diff ──
    L.append("--- Step-by-Step Diff Report ---")
    for d in diffs:
        L.append(f"\nStep {d.step_a}→{d.step_b} (cutoff: {_sd(d.cutoff_a)} → {_sd(d.cutoff_b)}):")
        # Filter
        L.append("  Filter: NO CHANGE" if d.filter_max_diff == 0 else
                 f"  Filter: max_diff={d.filter_max_diff:.6f}, mean_diff={d.filter_mean_diff:.6f}, affected_indices={d.filter_affected_indices}")
        # Schmitt eps
        L.append("  Schmitt eps: NO CHANGE" if d.schmitt_eps_max_diff == 0 else
                 f"  Schmitt eps: max_diff={d.schmitt_eps_max_diff:.6f}, mean_diff={d.schmitt_eps_mean_diff:.6f}")
        # Schmitt sig
        L.append("  Schmitt sig: NO CHANGE" if not d.schmitt_sig_flips else
                 f"  Schmitt sig: FLIP at indices {d.schmitt_sig_flips}")
        # Trade records
        if d.trade_count_a == d.trade_count_b and not d.trade_changes:
            L.append(f"  Trade records: count {d.trade_count_a}→{d.trade_count_b}, NO CHANGE")
        else:
            L.append(f"  Trade records: count {d.trade_count_a}→{d.trade_count_b}")
            for c in d.trade_changes: L.append(f"    {c}")
        # Masks
        if d.mask_changes:
            for m in d.mask_changes: L.append(f"  Holding masks: {m}")
        else:
            L.append("  Holding masks: NO CHANGE")
        # BS markers
        no_bs_change = (d.bs_entry_count_a == d.bs_entry_count_b and d.bs_exit_count_a == d.bs_exit_count_b
                        and not d.bs_marker_changes)
        L.append(f"  BS markers: entry {d.bs_entry_count_a}→{d.bs_entry_count_b}, exit {d.bs_exit_count_a}→{d.bs_exit_count_b}, NO CHANGE"
                 if no_bs_change else
                 f"  BS markers: entry {d.bs_entry_count_a}→{d.bs_entry_count_b}, exit {d.bs_exit_count_a}→{d.bs_exit_count_b}")
        if not no_bs_change:
            for c in d.bs_marker_changes: L.append(f"    {c}")
        # Cascade
        if d.cascade_amplification:
            ca = d.cascade_amplification
            L.append(f"  Cascade: filter tail Δ={ca['tail_filter_max_diff']:.6f} → sig flips={ca['schmitt_sig_flips_count']}, trade changes={ca['trade_change_count']}, BS marker changes={ca['bs_marker_change_count']}")
        # Tag
        if d.has_structural_jump:
            L.append(f"  → ⚠️ STRUCTURAL JUMP (Δ={d.bs_delta:+d} markers)")
        elif not d.is_stable:
            L.append(f"  → SIGNAL FLIP ({len(d.schmitt_sig_flips)} indices changed)")
        else:
            L.append("  → STABLE")

    # ── BS Marker Persistence ──
    L += ["", "--- BS Marker Persistence ---"]
    markers = bs_stab.get("markers", [])
    unstable = [m for m in markers if m["unstable"]]
    if not markers:
        L.append("  (no BS markers found)")
    elif not unstable:
        L.append(f"  All {len(markers)} markers stable across all {bs_stab.get('total_steps',0)} steps.")
    else:
        L.append(f"  Unstable markers ({len(unstable)}/{len(markers)}):")
        for m in unstable:
            df = "long" if m["direction"] == "B" else "short"
            L.append(f"  {m['direction']}@{_sd(m['date'])} ({df} {m['type']}): steps {m['first_seen_step']}-{m['last_seen_step']} (persistence: {m['persistence']}/{bs_stab['total_steps']}, UNSTABLE)")

    # ── Schmitt Pair Count Timeline ──
    L += ["", "--- Schmitt Pair Count Timeline ---"]
    ch = sc.get("changes", [])
    if not ch:
        L.append(f"  Pair count stable across all {sc.get('total_steps',0)} steps.")
    else:
        pct = len(ch) / max(sc.get("total_steps", 1), 1) * 100
        L.append(f"  Steps with pair count changes: {len(ch)}/{sc.get('total_steps',0)} ({pct:.1f}%)")
        big = max(ch, key=lambda c: abs(c["delta"]))
        L.append(f"  Largest change: step {big['step']-1}→{big['step']} ({big['prev_count']}→{big['new_count']}, Δ={big['delta']:+d})")
        for c in ch:
            L.append(f"  Step {c['step']-1}→{c['step']}: {c['prev_count']}→{c['new_count']} (Δ={c['delta']:+d})")

    # ── Cascade Amplification ──
    L += ["", "--- Cascade Amplification ---"]
    cd = [d for d in diffs if d.cascade_amplification]
    if not cd:
        L.append("  No cascade events detected (no filter tail changes above threshold).")
    else:
        for d in cd:
            ca = d.cascade_amplification
            L.append(f"\n  Step {d.step_a}→{d.step_b}: Filter Δmax at tail: {ca['tail_filter_max_diff']:.6f}")
            if "eps_amplification" in ca:
                L.append(f"    → Schmitt eps Δmax: {ca['schmitt_eps_max_diff']:.6f} (amplification: {ca['eps_amplification']}x)")
            if ca["schmitt_sig_flips_count"] > 0:
                L.append(f"    → Schmitt sig flips: {ca['schmitt_sig_flips_count']} indices")
            if ca["trade_change_count"] > 0:
                L.append(f"    → Trade record changes: {ca['trade_change_count']}")
            if ca["bs_marker_change_count"] > 0:
                L.append(f"    → BS marker changes: {ca['bs_marker_change_count']}")
            if "bs_marker_amplification" in ca:
                L.append(f"  Overall: {ca['tail_filter_max_diff']:.6f} filter change → {abs(ca['bs_marker_amplification']):.0f}x BS marker amplification")

    L += ["", S]
    return "\n".join(L)


def _json_report(session_dir: str, diffs: list, bs_stab: dict, sc: dict) -> str:
    meta = load_session_metadata(session_dir)
    def _dd(d):
        r = {"step_a": d.step_a, "step_b": d.step_b, "cutoff_a": d.cutoff_a, "cutoff_b": d.cutoff_b,
             "view": d.view,
             "filter": {"max_diff": d.filter_max_diff, "mean_diff": d.filter_mean_diff, "affected_indices": d.filter_affected_indices},
             "schmitt_eps": {"max_diff": d.schmitt_eps_max_diff, "mean_diff": d.schmitt_eps_mean_diff, "affected_indices": d.schmitt_eps_affected_indices},
             "schmitt_sig": {"flips": d.schmitt_sig_flips},
             "trade_records": {"count_a": d.trade_count_a, "count_b": d.trade_count_b, "changes": d.trade_changes},
             "holding_masks": {"changes": d.mask_changes},
             "bs_markers": {"entry_count_a": d.bs_entry_count_a, "entry_count_b": d.bs_entry_count_b,
                            "exit_count_a": d.bs_exit_count_a, "exit_count_b": d.bs_exit_count_b, "changes": d.bs_marker_changes},
             "is_stable": d.is_stable, "has_structural_jump": bool(d.bs_entry_count_a != d.bs_entry_count_b or d.bs_marker_changes),
             "bs_delta": d.bs_delta}
        if d.cascade_amplification: r["cascade_amplification"] = d.cascade_amplification
        return r
    report = {"session": {"session_id": meta.get("session_id", Path(session_dir).name),
                          "ticker": meta.get("ticker","?"), "step_count": meta.get("step_count",0),
                          "session_dir": str(session_dir)},
              "step_diffs": [_dd(d) for d in diffs],
              "bs_stability": bs_stab,
              "schmitt_stability": {"changes": sc.get("changes",[]),
                                    "total_changed_steps": sc.get("total_changed_steps",0),
                                    "total_steps": sc.get("total_steps",0)}}
    return json.dumps(report, indent=2, ensure_ascii=False, default=str)


# ══════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════

def main() -> None:
    p = argparse.ArgumentParser(description="Analyze captured backtest pipeline data.")
    p.add_argument("--session-dir", required=True, help="Path to capture session directory")
    p.add_argument("--step-range", nargs=2, type=int, default=None, help="Steps N to M (inclusive)")
    p.add_argument("--view", default=None, help="View name (default: all)")
    p.add_argument("--output-dir", default=None, help="Output directory (default: session_dir/analysis/)")
    p.add_argument("--diff-threshold", type=float, default=0.0, help="Min abs change to flag (default: 0.0)")
    p.add_argument("--format", choices=["TEXT","JSON"], default="TEXT", help="Output format")
    args = p.parse_args()

    sdir = Path(args.session_dir)
    if not sdir.exists() or not (sdir / "steps").exists():
        print(f"Error: invalid session directory: {sdir}", file=sys.stderr); sys.exit(1)

    mn, mx = _discover_step_range(sdir)
    if mx < mn: print("Error: no step directories found", file=sys.stderr); sys.exit(1)
    start, end = args.step_range or (mn, mx)
    start, end = max(start, mn), min(end, mx)
    if start >= end: print("Error: need at least 2 steps for comparison", file=sys.stderr); sys.exit(1)

    views = [args.view] if args.view else _discover_views(sdir)
    if not views: print("Error: no views found", file=sys.stderr); sys.exit(1)

    out = sys.stderr  # progress to stderr, report to stdout
    print(f"Session: {sdir}\nSteps: {start} to {end} ({end-start+1} steps)\nViews: {views}\nThreshold: {args.diff_threshold}\n", file=out)

    parts = []
    for view in views:
        print(f"Analyzing view: {view} ...", file=out)
        metas, svs = [], []
        for si in range(start, end + 1):
            sd = sdir / "steps" / f"{si:06d}"
            metas.append(load_step_index(sd))
            svs.append(load_step_view(sd / view))

        diffs = []
        for i in range(len(svs) - 1):
            if not svs[i] or not svs[i+1]: continue
            metas[i]["views_captured"] = metas[i+1]["views_captured"] = [view]
            d = compute_step_diff(svs[i], svs[i+1], metas[i], metas[i+1], threshold=args.diff_threshold)
            if not d.is_stable or args.diff_threshold == 0.0:
                diffs.append(d)

        bs = analyze_bs_stability(str(sdir), (start, end), view)
        sc = analyze_schmitt_changes(str(sdir), (start, end), view)
        parts.append((view, generate_summary_report(str(sdir), diffs, bs, sc, fmt=args.format)))

    if args.format.upper() == "JSON":
        # Combine multi-view into a single JSON object
        if len(parts) == 1:
            report = parts[0][1]
        else:
            report = json.dumps({view: json.loads(r) for view, r in parts}, indent=2, ensure_ascii=False)
    else:
        report = "\n\n".join(f"# View: {view}\n{r}" for view, r in parts)
    out_dir = Path(args.output_dir) if args.output_dir else sdir / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = "json" if args.format.upper() == "JSON" else "txt"
    out_path = out_dir / f"analysis_report.{ext}"
    out_path.write_text(report, encoding="utf-8")
    print(f"\nReport written to: {out_path}\n", file=out)
    if args.format.upper() == "JSON":
        print(report)  # clean JSON to stdout for piping
    else:
        print(report)


if __name__ == "__main__":
    main()
