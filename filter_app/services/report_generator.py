"""
回测分析报告自动生成器。

读取回测 session 目录下的 JSONL + CSV 文件，生成一个零外部依赖的
单文件 HTML 报告。所有 CSS/JS/数据内嵌，不引用任何 CDN。

Usage::

    from filter_app.services.report_generator import ReportGenerator
    path = ReportGenerator.generate("data/backtest_output/3690_xxx")
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# JavaScript: 微型图表库 (~120 行)
# ---------------------------------------------------------------------------

_CHART_JS = r"""
function MiniChart(canvasId) {
  var c = document.getElementById(canvasId);
  if (!c) return;
  var dpr = window.devicePixelRatio || 1;
  var rect = c.getBoundingClientRect();
  c.width = rect.width * dpr; c.height = rect.height * dpr;
  var ctx = c.getContext('2d');
  ctx.scale(dpr, dpr);
  var W = rect.width, H = rect.height;
  var P = { top: 6, right: 10, bottom: 20, left: 40 };
  var PW = W - P.left - P.right, PH = H - P.top - P.bottom;
  function xs(len) { return function(i) { return P.left + (i / Math.max(1, len - 1)) * PW; }; }
  function grid(minV, maxV, n) {
    ctx.strokeStyle = '#2a2a3e'; ctx.lineWidth = 0.5;
    ctx.fillStyle = '#888'; ctx.font = '10px monospace'; ctx.textAlign = 'right';
    var r = maxV - minV || 1;
    for (var i = 0; i <= n; i++) {
      var y = P.top + (i / n) * PH;
      var val = maxV - (i / n) * r;
      ctx.beginPath(); ctx.moveTo(P.left, y); ctx.lineTo(W - P.right, y); ctx.stroke();
      ctx.fillText(val.toFixed(val < 1 ? 4 : 1), P.left - 4, y + 3);
    }
  }
  function axes(labels) {
    ctx.strokeStyle = '#555'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(P.left, P.top); ctx.lineTo(P.left, H - P.bottom);
    ctx.lineTo(W - P.right, H - P.bottom); ctx.stroke();
    if (labels && labels.length) {
      ctx.fillStyle = '#888'; ctx.font = '9px monospace'; ctx.textAlign = 'center';
      var step = Math.max(1, Math.floor(labels.length / 8));
      var xf = xs(labels.length);
      for (var i = 0; i < labels.length; i += step) ctx.fillText(labels[i], xf(i), H - P.bottom + 12);
    }
  }
  function line(series, color, minV, maxV) {
    if (!series || series.length < 2) return;
    ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.beginPath();
    var xf = xs(series.length), r = maxV - minV || 1;
    for (var i = 0; i < series.length; i++) {
      var x = xf(i), y = P.top + ((maxV - series[i]) / r) * PH;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }
  function area(series, color, minV, maxV, base) {
    if (!series || series.length < 2) return;
    ctx.fillStyle = color; ctx.beginPath();
    var xf = xs(series.length), r = maxV - minV || 1;
    var bY = P.top + ((maxV - base) / r) * PH;
    for (var i = 0; i < series.length; i++) {
      var x = xf(i), y = P.top + ((maxV - series[i]) / r) * PH;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    for (var i = series.length - 1; i >= 0; i--) ctx.lineTo(xf(i), bY);
    ctx.closePath(); ctx.fill();
  }
  function vlines(indices, minV, maxV, dataLen, color) {
    if (!indices || !indices.length) return;
    ctx.strokeStyle = color || '#ff4444'; ctx.lineWidth = 0.8;
    ctx.setLineDash([3, 3]); var xf = xs(dataLen);
    for (var i = 0; i < indices.length; i++) {
      ctx.beginPath(); ctx.moveTo(xf(indices[i]), P.top);
      ctx.lineTo(xf(indices[i]), H - P.bottom); ctx.stroke();
    }
    ctx.setLineDash([]);
  }
  this.grid = grid; this.axes = axes; this.line = line;
  this.area = area; this.vlines = vlines;
}
"""


# ---------------------------------------------------------------------------
# ReportGenerator
# ---------------------------------------------------------------------------

class ReportGenerator:
    """回测分析报告生成器。"""

    @staticmethod
    def generate(session_dir: str) -> str:
        """生成分析报告，返回报告文件路径。"""
        sd = Path(session_dir)
        if not sd.is_dir():
            raise FileNotFoundError(f"Session 目录不存在: {session_dir}")

        # 1. 加载原始数据
        meta = _load_json(sd / "metadata.json")
        events = _load_jsonl(sd / "events.jsonl")
        filter_tails = _load_jsonl(sd / "filter_tail.jsonl")
        schmitt_snaps = _load_jsonl(sd / "schmitt_snapshot.jsonl")
        bs_snaps = _load_jsonl(sd / "bs_snapshot.jsonl")
        csv_rows, csv_headers = _load_csv(sd / "backtest_data.csv")

        # 2. 元信息
        config = meta.get("config", {})
        ticker = meta.get("ticker", "?")
        preset = config.get("preset", "?")
        start_bar = config.get("start_bar", "?")
        end_bar = config.get("end_bar", "?")
        step_count = meta.get("step_count", 0)
        view_columns: dict[str, str] = meta.get("view_columns", {})

        start_dt = _parse_iso(meta.get("start_time", ""))
        end_dt = _parse_iso(meta.get("end_time", ""))
        time_range = (
            f"{start_dt.strftime('%H:%M')} ~ {end_dt.strftime('%H:%M')}"
            if start_dt and end_dt else "?"
        )

        price_start, price_end, price_pct = _price_change(csv_rows)

        # 3. 确定主视图 (BS 事件最多的)
        view_events: dict[str, int] = defaultdict(int)
        for e in events:
            if e.get("event") in ("bs_added", "bs_removed", "bs_modified"):
                view_events[e.get("view", "")] += 1
        primary_view = max(view_events, key=view_events.get) if view_events else ""

        def _view_label(vn: str) -> str:
            tf = view_columns.get(vn.split("_")[0], "")
            return f"{vn}({tf})" if tf else vn

        # 4. BS 变动检测
        bs_jumps, bs_steps = _build_bs_jumps(bs_snaps, events, primary_view, filter_tails, schmitt_snaps)

        # 5. BS 演化 (紧凑格式：百分比段)
        bs_evo_rows = _build_bs_evo_compact(bs_snaps, primary_view)

        # 6. 趋势数据 (紧凑数组)
        chart_json = _build_chart_json(filter_tails, schmitt_snaps, primary_view, bs_steps, bs_jumps)

        # 7. CSV 表格
        table_cols, table_rows = _build_table_data(csv_headers, csv_rows, view_columns)

        # 8. 渲染 HTML
        html = _render_html(
            ticker=ticker, preset=preset,
            start_bar=start_bar, end_bar=end_bar,
            step_count=step_count, time_range=time_range,
            price_start=price_start, price_end=price_end, price_pct=price_pct,
            primary_label=_view_label(primary_view),
            bs_jumps=bs_jumps, bs_jump_count=len(bs_jumps),
            bs_evo_rows=bs_evo_rows,
            chart_json=chart_json,
            table_cols=table_cols, table_rows=table_rows,
        )

        report_path = sd / "report.html"
        report_path.write_text(html, encoding="utf-8")
        return str(report_path)


# ======================================================================
# Data loading
# ======================================================================

def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    if not path.exists():
        return records
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def _load_csv(path: Path) -> tuple[list[dict], list[str]]:
    if not path.exists():
        return [], []
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader], list(reader.fieldnames or [])


# ======================================================================
# Utilities
# ======================================================================

def _parse_iso(s: str):
    if not s:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(s[:19])
    except Exception:
        return None


def _price_change(rows: list[dict]) -> tuple[str, str, str]:
    if not rows:
        return "?", "?", "?"
    try:
        p0 = float(rows[0].get("close", 0))
        p1 = float(rows[-1].get("close", 0))
        pct = (p1 - p0) / p0 * 100 if p0 else 0
        return f"{p0:.2f}", f"{p1:.2f}", f"{pct:+.1f}%"
    except (ValueError, TypeError):
        return "?", "?", "?"


def _short_date(date_str: str) -> str:
    """ISO date to MMDD."""
    if not date_str:
        return ""
    try:
        d = date_str.split("T")[0]
        parts = d.split("-")
        if len(parts) >= 3:
            return parts[1] + parts[2]
    except Exception:
        pass
    return date_str[:8]


def _r4(v: float) -> float:
    """Round to 4 decimal places for compact JSON."""
    return round(v, 4)


# ======================================================================
# BS 变动检测
# ======================================================================

def _build_bs_jumps(
    bs_snaps: list[dict],
    events: list[dict],
    view: str,
    filter_tails: list[dict],
    schmitt_snaps: list[dict],
) -> tuple[list[dict], list[int]]:
    """返回 (jumps, steps)。jump 包含 filter delta, eps delta, sig flip 信息。"""

    snap_by_step: dict[int, dict] = {}
    for s in bs_snaps:
        if s.get("view") == view:
            snap_by_step[s["step"]] = s

    steps = sorted(snap_by_step.keys())
    step_counts = {s: len(snap_by_step[s].get("entry", [])) + len(snap_by_step[s].get("exit", []))
                   for s in steps}

    # filter_last per step
    fl_by_step: dict[int, float] = {}
    for ft in filter_tails:
        if ft.get("view") == view:
            tail = ft.get("tail", [])
            if tail:
                fl_by_step[ft["step"]] = tail[-1]

    # eps_tail per step
    eps_by_step: dict[int, list] = {}
    sig_by_step: dict[int, dict] = {}
    for ss in schmitt_snaps:
        if ss.get("view") == view:
            eps_by_step[ss["step"]] = ss.get("eps_tail", [])
            sig_by_step[ss["step"]] = ss.get("sig_counts", {})

    # 按 step 分组事件
    evt_by_step: dict[int, list[dict]] = defaultdict(list)
    for e in events:
        if e.get("view") == view and e["event"] in ("bs_added", "bs_removed", "bs_modified"):
            evt_by_step[e["step"]].append(e)

    jumps: list[dict] = []
    for i in range(1, len(steps)):
        prev = steps[i - 1]
        curr = steps[i]
        delta = step_counts[curr] - step_counts[prev]
        if delta != 0:
            # filter delta
            f_prev = fl_by_step.get(prev)
            f_curr = fl_by_step.get(curr)
            f_delta = _r4(f_curr - f_prev) if (f_prev is not None and f_curr is not None) else 0

            # eps delta
            e_prev_list = eps_by_step.get(prev, [])
            e_curr_list = eps_by_step.get(curr, [])
            e_prev = e_prev_list[-1] if e_prev_list else 0
            e_curr = e_curr_list[-1] if e_curr_list else 0
            eps_delta = _r4(e_curr - e_prev) if (e_prev_list and e_curr_list) else 0
            eps_ratio = _r4(e_curr / e_prev) if (e_prev_list and e_curr_list and e_prev) else 0

            # sig flip
            s_prev = sig_by_step.get(prev, {})
            s_curr = sig_by_step.get(curr, {})
            sig_changed = (s_prev != s_curr)

            # step events
            step_evts = evt_by_step.get(curr, [])
            adds = [e for e in step_evts if e["event"] == "bs_added"]
            rems = [e for e in step_evts if e["event"] == "bs_removed"]

            jumps.append({
                "prev": prev, "curr": curr,
                "delta": delta,
                "prev_cnt": step_counts[prev], "curr_cnt": step_counts[curr],
                "f_delta": f_delta,
                "e_prev": _r4(e_prev) if e_prev_list else 0,
                "e_curr": _r4(e_curr) if e_curr_list else 0,
                "eps_ratio": eps_ratio,
                "sig_changed": sig_changed,
                "adds": adds, "rems": rems,
            })

    return jumps, steps


# ======================================================================
# BS 演化 (紧凑格式)
# ======================================================================

def _build_bs_evo_compact(bs_snaps: list[dict], view: str) -> list[dict]:
    """构建紧凑的 BS 演化行数据。每行用百分比段表示存在区间。"""

    snap_by_step: dict[int, dict] = {}
    for s in bs_snaps:
        if s.get("view") == view:
            snap_by_step[s["step"]] = s

    steps = sorted(snap_by_step.keys())
    if not steps:
        return []

    total = len(steps)

    # 收集所有唯一 marker: (type, label, date)
    all_markers: dict[tuple, dict] = {}
    for step in steps:
        snap = snap_by_step[step]
        for m in snap.get("entry", []):
            key = ("entry", str(m["label"]), str(m.get("date", "")))
            if key not in all_markers:
                all_markers[key] = {
                    "type": "entry", "label": str(m["label"]),
                    "date_short": _short_date(m.get("date", "")),
                    "bar_idx": str(m.get("bar_idx", "")),
                }
        for m in snap.get("exit", []):
            key = ("exit", str(m["label"]), str(m.get("date", "")))
            if key not in all_markers:
                all_markers[key] = {
                    "type": "exit", "label": str(m["label"]),
                    "date_short": _short_date(m.get("date", "")),
                    "bar_idx": str(m.get("bar_idx", "")),
                }

    if not all_markers:
        return []

    # 跟踪每个 marker 在每个 step 是否存在
    presence: dict[tuple, dict[int, bool]] = {k: {s: False for s in steps} for k in all_markers}
    for step in steps:
        snap = snap_by_step[step]
        present_set: set[tuple] = set()
        for m in snap.get("entry", []):
            present_set.add(("entry", str(m["label"]), str(m.get("date", ""))))
        for m in snap.get("exit", []):
            present_set.add(("exit", str(m["label"]), str(m.get("date", ""))))
        for k in all_markers:
            presence[k][step] = k in present_set

    # 按首次出现排序
    def _first(k):
        for s in steps:
            if presence[k][s]:
                return s
        return steps[-1] + 1

    sorted_keys = sorted(all_markers.keys(), key=_first)

    rows: list[dict] = []
    for idx, key in enumerate(sorted_keys):
        info = all_markers[key]
        pres = presence[key]

        # 合并为连续段
        segments: list[tuple[int, int]] = []
        seg_start = None
        for s in steps:
            if pres[s]:
                if seg_start is None:
                    seg_start = s
            else:
                if seg_start is not None:
                    segments.append((seg_start, s - 1))
                    seg_start = None
        if seg_start is not None:
            segments.append((seg_start, steps[-1]))

        # 转为百分比区间 (0-100)
        seg_pcts = []
        for a, b in segments:
            p0 = round(a / max(1, total - 1) * 100, 1)
            p1 = round((b + 1) / total * 100, 1)
            seg_pcts.append({"left": p0, "right": p1})

        # 状态文本
        if len(segments) == 1:
            a, b = segments[0]
            if a == steps[0] and b >= steps[-1]:
                status = "全程存在"
            elif b >= steps[-1]:
                status = f"出现@step{a}"
            elif a == steps[0]:
                status = f"消失@step{b + 1}"
            else:
                status = f"step{a}–{b + 1}"
        else:
            parts = [f"step{a}–{b + 1}" if a != b else f"step{a}" for a, b in segments]
            status = ", ".join(parts)

        rows.append({
            "id": idx + 1,
            "label": info["label"],
            "label_class": "b" if info["label"] == "B" else "s",
            "type": info["type"],
            "date_short": info["date_short"],
            "bar_idx": info["bar_idx"],
            "segments": seg_pcts,
            "status": status,
        })

    return rows


# ======================================================================
# 图表 JSON (紧凑)
# ======================================================================

def _build_chart_json(
    filter_tails: list[dict],
    schmitt_snaps: list[dict],
    view: str,
    steps: list[int],
    jumps: list[dict],
) -> str:
    """构建紧凑的图表 JSON 数据。用小数组而非对象列表。"""

    # filter_last per step
    fl_map: dict[int, float] = {}
    for ft in filter_tails:
        if ft.get("view") == view:
            tail = ft.get("tail", [])
            if tail:
                fl_map[ft["step"]] = _r4(tail[-1])

    # schmitt data per step
    pc_map: dict[int, int] = {}
    s1_map: dict[int, int] = {}
    s0_map: dict[int, int] = {}
    sm1_map: dict[int, int] = {}
    for ss in schmitt_snaps:
        if ss.get("view") == view:
            s = ss["step"]
            pc_map[s] = ss.get("pair_count", 0)
            sc = ss.get("sig_counts", {})
            s1_map[s] = sc.get("1", 0)
            s0_map[s] = sc.get("0", 0)
            sm1_map[s] = sc.get("-1", 0)

    fl = [fl_map.get(s, 0) for s in steps]
    pc = [pc_map.get(s, 0) for s in steps]
    s1 = [s1_map.get(s, 0) for s in steps]
    s0 = [s0_map.get(s, 0) for s in steps]
    sm1 = [sm1_map.get(s, 0) for s in steps]
    jp = [j["prev"] for j in jumps]  # jump positions as step indices

    data = {
        "st": steps,     # steps
        "fl": fl,        # filter_last values
        "pc": pc,        # pair_counts
        "s1": s1, "s0": s0, "sm1": sm1,  # sig distribution
        "jp": jp,        # jump positions
    }

    # 使用紧凑 JSON 序列化
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


# ======================================================================
# CSV 表格
# ======================================================================

def _build_table_data(
    headers: list[str],
    rows: list[dict],
    view_columns: dict[str, str],
) -> tuple[list[str], list[dict]]:
    """截取关键列。"""
    if not headers or not rows:
        return [], []

    # 基础列
    base = ["bar_index", "bar_timestamp", "close"]
    # 视图前缀
    vps = sorted(set(v.split("_")[0] for v in view_columns))

    # 核心后缀
    core_suffixes = [
        "filtered", "sig", "eps", "pair_count", "trade_count",
        "bs_entry_count", "bs_exit_count",
    ]

    selected: list[str] = []
    for h in headers:
        if h in base:
            selected.append(h)
        else:
            for vp in vps:
                for sfx in core_suffixes:
                    if h == f"{vp}_{sfx}":
                        selected.append(h)
                        break

    if not selected:
        return headers, rows

    slim_rows = [{k: row.get(k, "") for k in selected} for row in rows]
    return selected, slim_rows


# ======================================================================
# HTML 渲染
# ======================================================================

def _render_html(
    ticker: str, preset: str,
    start_bar: Any, end_bar: Any, step_count: int,
    time_range: str, price_start: str, price_end: str, price_pct: str,
    primary_label: str,
    bs_jumps: list[dict], bs_jump_count: int,
    bs_evo_rows: list[dict],
    chart_json: str,
    table_cols: list[str], table_rows: list[dict],
) -> str:

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>回测报告 — {ticker} | {preset}</title>
<style>
{_CSS}
</style>
</head>
<body>
<div class="c">
  <div class="s">
    <h1>回测分析报告</h1>
    <div class="tm"><span class="mi"><b>{ticker}</b></span><span class="ms">|</span><span class="mi">{preset}</span><span class="ms">|</span><span class="mi">bar {start_bar}–{end_bar}</span><span class="ms">|</span><span class="mi">{step_count} 步</span></div>
    <div class="tm"><span class="mi">{time_range}</span><span class="ms">|</span><span class="mi">价格 {price_start}→{price_end}</span><span class="mi pc">{price_pct}</span></div>
    <div class="tm"><span class="mi">主视图: {primary_label}</span></div>
  </div>

  {_render_bs_jumps_html(bs_jumps, bs_jump_count)}

  {_render_bs_evo_html(bs_evo_rows)}

  <div class="s">
    <h2>关键指标趋势 <span class="bd">红色虚线=结构性跳跃点</span></h2>
    <div class="cg">
      <div class="cc"><h3>Filter 尾部值</h3><div class="cw"><canvas id="cf"></canvas></div></div>
      <div class="cc"><h3>Pair 数量</h3><div class="cw"><canvas id="cp"></canvas></div></div>
      <div class="cc"><h3>Sig 分布</h3><div class="cw"><canvas id="cs"></canvas></div><div class="sl"><span style="color:#ff6b6b">sig=1</span> <span style="color:#aaa">sig=0</span> <span style="color:#00d4aa">sig=-1</span></div></div>
    </div>
  </div>

  {_render_data_table_html(table_cols, table_rows)}
</div>

<script>
{_CHART_JS}
(function(){{
  var D = {chart_json};
  if (!D.st || !D.st.length) return;
  var N = D.st.length;

  // Chart 1: Filter tail
  (function(){{
    var c = new MiniChart('cf'); if (!c) return;
    var v = D.fl, mn = Math.min.apply(null, v), mx = Math.max.apply(null, v);
    var pad = (mx - mn) * 0.1 || 0.1;
    c.grid(mn - pad, mx + pad, 5);
    c.line(v, '#00d4aa', mn - pad, mx + pad);
    c.axes(D.st);
    c.vlines(D.jp, mn - pad, mx + pad, N, '#ff4444');
  }})();

  // Chart 2: Pair count
  (function(){{
    var c = new MiniChart('cp'); if (!c) return;
    var v = D.pc, mn = Math.min.apply(null, v), mx = Math.max.apply(null, v);
    var pad = Math.max(1, (mx - mn) * 0.15);
    mn = Math.max(0, mn - pad); mx += pad;
    c.grid(mn, mx, 5);
    c.line(v, '#ffaa00', mn, mx);
    c.axes(D.st);
    c.vlines(D.jp, mn, mx, N, '#ff4444');
  }})();

  // Chart 3: Sig distribution (stacked)
  (function(){{
    var c = new MiniChart('cs'); if (!c) return;
    var lo = D.sm1, mid = D.sm1.map(function(v,i){{return v + D.s0[i];}});
    var hi = D.sm1.map(function(v,i){{return v + D.s0[i] + D.s1[i];}});
    var mx = Math.max.apply(null, hi) * 1.1 || 1;
    c.grid(0, mx, 5);
    c.area(hi, 'rgba(255,107,107,0.25)', 0, mx, 0);
    c.area(mid, 'rgba(150,150,150,0.2)', 0, mx, 0);
    c.area(lo, 'rgba(0,212,170,0.25)', 0, mx, 0);
    c.line(hi, '#ff6b6b', 0, mx);
    c.line(mid, '#aaa', 0, mx);
    c.line(lo, '#00d4aa', 0, mx);
    c.axes(D.st);
    c.vlines(D.jp, 0, mx, N, '#ff4444');
  }})();

  // Table sort/filter
  var tbl = document.getElementById('dt');
  if (tbl) {{
    var ths = tbl.querySelectorAll('th.srt');
    ths.forEach(function(th, i) {{
      th.addEventListener('click', function() {{ sortTbl(tbl, i); }});
    }});
    var ft = document.getElementById('tf');
    if (ft) {{
      ft.addEventListener('input', function() {{
        var q = this.value.toLowerCase();
        tbl.querySelectorAll('tbody tr').forEach(function(r) {{
          r.style.display = r.textContent.toLowerCase().includes(q) ? '' : 'none';
        }});
      }});
    }}
  }}
  function sortTbl(tbl, ci) {{
    var tb = tbl.querySelector('tbody');
    var rs = Array.from(tb.querySelectorAll('tr'));
    var asc = tbl.getAttribute('data-s') !== ci + ':a';
    rs.sort(function(a, b) {{
      var va = a.children[ci].textContent.trim();
      var vb = b.children[ci].textContent.trim();
      var na = parseFloat(va), nb = parseFloat(vb);
      if (!isNaN(na) && !isNaN(nb)) return asc ? na - nb : nb - na;
      return asc ? va.localeCompare(vb) : vb.localeCompare(va);
    }});
    tbl.setAttribute('data-s', ci + (asc ? ':a' : ':d'));
    rs.forEach(function(r) {{ tb.appendChild(r); }});
  }}
}})();
</script>
</body>
</html>"""


# ======================================================================
# HTML 片段
# ======================================================================

def _render_bs_jumps_html(jumps: list[dict], count: int) -> str:
    if not jumps:
        return '<div class="s"><h2>BS 变动检测</h2><p class="inf">未发现结构性跳跃点。</p></div>'

    rows = ""
    for j in jumps:
        d = j["delta"]
        delta_str = f"+{d}" if d > 0 else str(d)
        pos_cls = "pos" if d > 0 else "neg"

        details = f'BS {j["prev_cnt"]}→{j["curr_cnt"]} | filterΔ={j["f_delta"]}'
        if j.get("eps_ratio") and j["eps_ratio"] > 1:
            details += f' | eps×{j["eps_ratio"]:.1f}'
        if j["sig_changed"]:
            details += " | sig flip"

        # 新增/移除的 marker 标签
        labels = []
        for a in j.get("adds", []):
            labels.append(f'+{a.get("label","")}@{_short_date(a.get("date",""))}')
        for r in j.get("rems", []):
            labels.append(f'-{r.get("label","")}@{_short_date(r.get("date",""))}')
        if labels:
            details += " | " + " ".join(labels[:4])  # 最多显示4个
            if len(labels) > 4:
                details += f" +{len(labels) - 4} more"

        rows += f"<tr><td class='jst'>s{j['prev']}→s{j['curr']}</td><td class='jdt {pos_cls}'>BS {delta_str}</td><td class='jde'>{details}</td></tr>"

    return f"""<div class="s">
  <h2>BS 变动检测 <span class="bd">发现 {count} 个结构性跳跃点</span></h2>
  <div class="tw"><table class="jt"><thead><tr><th>Step</th><th>变化</th><th>详情</th></tr></thead><tbody>{rows}</tbody></table></div>
</div>"""


def _render_bs_evo_html(rows: list[dict]) -> str:
    if not rows:
        return '<div class="s"><h2>BS 演化图</h2><p class="inf">无 BS marker 数据。</p></div>'

    html_rows = ""
    for m in rows:
        cls = m["label_class"]  # "b" or "s"
        # 构建百分比色块
        bar_parts = ""
        prev_right = 0.0
        for seg in m["segments"]:
            gap = seg["left"] - prev_right
            if gap > 0.5:
                bar_parts += f'<div class="ebg" style="flex:{_pct_str(gap)}"></div>'
            bar_parts += f'<div class="eb{cls}" style="flex:{_pct_str(seg["right"] - seg["left"])}"></div>'
            prev_right = seg["right"]

        html_rows += (
            f"<tr><td class='bid'>BS#{m['id']}</td>"
            f"<td class='blb l{cls}'>{m['label']}@{m['date_short']}</td>"
            f"<td class='btp'>{m['type']}</td>"
            f"<td class='bbr'>{m['bar_idx']}</td>"
            f"<td class='bro'>{bar_parts}</td>"
            f"<td class='bst'>{m['status']}</td></tr>"
        )

    return f"""<div class="s">
  <h2>BS 演化图</h2>
  <div class="tw"><table class="et"><thead><tr><th>ID</th><th>Label</th><th>类型</th><th>Bar</th><th class="ehd">存在区间 (step →)</th><th>状态</th></tr></thead><tbody>{html_rows}</tbody></table></div>
  <div class="el"><span class="ld lb"></span> B(买入) <span class="ld ls"></span> S(卖出)</div>
</div>"""


def _pct_str(v: float) -> str:
    """百分比值，最小0.5避免消失。"""
    return f"{max(v, 0.5):.1f}"


def _render_data_table_html(cols: list[str], rows: list[dict]) -> str:
    if not cols:
        return '<div class="s"><h2>完整数据表</h2><p class="inf">无 CSV 数据。</p></div>'

    # 简化列名
    short = {}
    for c in cols:
        parts = c.split("_", 2)
        if len(parts) >= 3 and parts[-1] in ("bs_entry_count", "bs_exit_count"):
            short[c] = parts[0] + "_" + parts[-1][:5]  # e.g. v3_bs_en
        elif len(parts) >= 2:
            short[c] = c
        else:
            short[c] = c

    hdr = "".join(f'<th class="srt">{short.get(c, c)}</th>' for c in cols)

    # 最多显示 100 行
    disp = rows[-100:] if len(rows) > 100 else rows
    body = ""
    for row in disp:
        cells = "".join(f"<td>{_fmt_cell(row.get(c, ''))}</td>" for c in cols)
        body += f"<tr>{cells}</tr>"

    trunc = f' <span class="bd">显示最近 {len(disp)} / 共 {len(rows)} 行</span>' if len(rows) > 100 else ""

    return f"""<div class="s">
  <h2>完整数据表{trunc}</h2>
  <input type="text" id="tf" class="tfi" placeholder="筛选...">
  <div class="tw"><table id="dt" class="dtbl" data-s=""><thead><tr>{hdr}</tr></thead><tbody>{body}</tbody></table></div>
</div>"""


def _fmt_cell(val: Any) -> str:
    if val is None:
        return "-"
    s = str(val)
    try:
        f = float(s)
        if f == 0:
            return "0"
        if abs(f) < 0.001:
            return f"{f:.6f}"
        if abs(f) >= 10000:
            return f"{f:.2f}"
        return f"{f:.4g}"
    except (ValueError, TypeError):
        pass
    return s if len(s) < 20 else s[:19] + "..."


# ======================================================================
# CSS (压缩过的暗色主题)
# ======================================================================

_CSS = """\
:root{--bg:#0d1117;--bc:#161b22;--bd:#30363d;--tx:#e6edf3;--td:#8b949e;--gr:#00d4aa;--rd:#ff6b6b;--or:#ffaa00}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:13px;line-height:1.5}
.c{max-width:1200px;margin:0 auto;padding:16px}
.s{background:var(--bc);border:1px solid var(--bd);border-radius:8px;padding:16px;margin-bottom:16px}
h1{font-size:20px;margin-bottom:6px;color:var(--gr)}
h2{font-size:15px;margin-bottom:10px}
h3{font-size:12px;margin-bottom:4px;color:var(--td)}
.tm{display:flex;flex-wrap:wrap;gap:4px;align-items:center;margin-bottom:2px}
.mi{color:var(--td)} .ms{color:var(--bd)}
.pc{color:var(--gr);font-weight:600;margin-left:4px}
.bd{display:inline-block;background:rgba(255,170,0,0.15);color:var(--or);border-radius:4px;padding:1px 6px;font-size:11px;font-weight:normal;margin-left:4px}
.inf{color:var(--td);padding:8px 0}
.tw{overflow-x:auto}
/* jump table */
.jt{width:100%;border-collapse:collapse}
.jt th,.jt td{padding:5px 10px;text-align:left;border-bottom:1px solid var(--bd);font-size:12px}
.jt th{color:var(--td);font-weight:600}
.jst{font-family:monospace;white-space:nowrap}
.jdt{font-weight:600} .jdt.pos{color:var(--gr)} .jdt.neg{color:var(--rd)}
.jde{color:var(--td);font-size:11px;max-width:500px;word-break:break-all}
/* bs evolution */
.et{width:100%;border-collapse:collapse}
.et th,.et td{padding:2px 4px;border-bottom:1px solid var(--bd);font-size:11px;white-space:nowrap}
.et th{color:var(--td);font-weight:600}
.bid{color:var(--td);font-family:monospace;width:44px}
.blb{font-weight:600} .blb.lb{color:var(--gr)} .blb.ls{color:var(--rd)}
.btp{color:var(--td);font-size:10px}
.bbr{font-family:monospace;color:var(--td)}
.bst{color:var(--td);font-size:10px}
.ehd{min-width:180px}
.bro{display:flex;gap:1px;min-width:180px;height:14px;align-items:stretch;border-radius:2px;overflow:hidden}
.ebg{background:transparent}
.ebb{background:var(--gr);border-radius:1px}
.ebs{background:var(--rd);border-radius:1px}
.el{margin-top:6px;font-size:11px;color:var(--td)}
.ld{display:inline-block;width:10px;height:10px;border-radius:2px;vertical-align:middle;margin-right:3px}
.lb{background:var(--gr)} .ls{background:var(--rd)}
/* charts */
.cg{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}
.cc{background:var(--bg);border-radius:6px;padding:10px}
.cw{position:relative;width:100%}
.cw canvas{display:block;width:100%;height:180px}
.sl{text-align:center;font-size:10px;margin-top:2px}
/* data table */
.tfi{width:100%;max-width:260px;padding:5px 8px;margin-bottom:6px;background:var(--bg);border:1px solid var(--bd);border-radius:4px;color:var(--tx);font-size:12px}
.tfi::placeholder{color:var(--td)}
.dtbl{width:100%;border-collapse:collapse;font-size:10px}
.dtbl th,.dtbl td{padding:3px 6px;text-align:right;border-bottom:1px solid var(--bd);white-space:nowrap}
.dtbl th{color:var(--td);font-weight:600;cursor:pointer;user-select:none;position:sticky;top:0;background:var(--bc);z-index:1}
.dtbl th:hover{color:var(--tx)}
.dtbl td{color:var(--tx);font-family:'SF Mono','Fira Code',monospace}
.dtbl tr:hover td{background:rgba(255,255,255,0.03)}
/* print */
@media print{
  body{background:#fff;color:#000;font-size:10px}
  .s{background:#fff;border:1px solid #ccc;break-inside:avoid}
  h1{color:#000}
  .ebb,.lb{background:#00aa88!important;-webkit-print-color-adjust:exact;print-color-adjust:exact}
  .ebs,.ls{background:#ee4444!important;-webkit-print-color-adjust:exact;print-color-adjust:exact}
  .c{max-width:100%;padding:0}
  .tfi{display:none}
}
@media(max-width:768px){
  .c{padding:8px} .s{padding:10px}
  .cg{grid-template-columns:1fr} h1{font-size:17px}
}
"""
