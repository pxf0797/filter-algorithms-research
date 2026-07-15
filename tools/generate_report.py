#!/usr/bin/env python3
"""生成回测数据演示 HTML 报告"""

import json
import pandas as pd
from collections import Counter
from pathlib import Path

SESSION = "/Users/xfpan/claude/filter_research/data/backtest_output/3690_20260715-224117-3690"
OUTPUT = Path("/Users/xfpan/.claude/orchestrator/output/orch-20260716-000614-30612/demo-report.html")

# ── 读取数据 ──────────────────────────────────────────────

df = pd.read_csv(f"{SESSION}/backtest_data.csv")

with open(f"{SESSION}/events.jsonl") as f:
    events = [json.loads(l) for l in f if l.strip()]

# 过滤掉 session_started/ended 用于 BS 分析
bs_events = [e for e in events if e["event"].startswith("bs_")]

with open(f"{SESSION}/trade_summary.jsonl") as f:
    trade_summaries = [json.loads(l) for l in f if l.strip()]


# ── 数据处理 ──────────────────────────────────────────────

VIEWS = ["v0", "v1", "v2", "v3"]
VIEW_LABELS = {"v0": "v0 1分钟", "v1": "v1 5分钟", "v2": "v2 15分钟", "v3": "v3 60分钟"}
VIEW_COLORS = {"v0": "#f59e0b", "v1": "#10b981", "v2": "#3b82f6", "v3": "#ef4444"}

# 1. 过滤趋势数据
filter_data = []
for _, row in df.iterrows():
    point = {"bar_index": int(row["bar_index"]), "timestamp": str(row["bar_timestamp"])}
    for v in VIEWS:
        fc = f"{v}_filtered"
        val = row[fc]
        point[v] = float(val) if pd.notna(val) else None
    filter_data.append(point)

# 2. Sig 数据
sig_data = []
for _, row in df.iterrows():
    point = {"bar_index": int(row["bar_index"])}
    for v in VIEWS:
        sc = f"{v}_sig"
        point[v] = int(row[sc]) if pd.notna(row[sc]) else 0
    sig_data.append(point)

# 3. BS 事件统计
event_counts = Counter(e["event"] for e in bs_events)
event_by_view = {}
for e in bs_events:
    view = e.get("view", "unknown")
    event_by_view.setdefault(view, Counter())[e["event"]] += 1

# 4. BS 事件时间线（按 bar_index 聚合）
bs_timeline = {}
for e in bs_events:
    bar_idx = e.get("bar_idx", 0)
    bs_timeline.setdefault(bar_idx, {"bs_added": 0, "bs_modified": 0, "bs_removed": 0, "bs_stable": 0})
    bs_timeline[bar_idx][e["event"]] += 1

# 5. 交易摘要
trade_data = []
for ts in trade_summaries:
    trade_data.append({
        "step": ts["step"],
        "view": ts["view"].replace("_", " "),
        "trade_count": ts["trade_count"],
        "long_count": ts["long_count"],
        "short_count": ts["short_count"],
        "win_rate": ts["win_rate"],
        "completed_trades": ts["completed_trades"],
    })

# 6. 各视图汇总
view_summary = {}
for v in VIEWS:
    view_summary[v] = {
        "sig_dist": df[f"{v}_sig"].value_counts().to_dict(),
        "sig_dur_mean": round(float(df[f"{v}_sig_dur"].mean()), 2),
        "sig_dur_values": sorted([int(x) for x in df[f"{v}_sig_dur"].unique()]),
        "entry_label": df[f"{v}_bs_entry_label"].value_counts().to_dict(),
        "exit_label": df[f"{v}_bs_exit_label"].value_counts().to_dict(),
        "filter_min": round(float(df[f"{v}_filtered"].min()), 4),
        "filter_max": round(float(df[f"{v}_filtered"].max()), 4),
        "filter_changes": int((df[f"{v}_filtered"].notna() & (df[f"{v}_filtered"] != df[f"{v}_filtered"].shift())).sum()),
        "trade_count_mean": round(float(df[f"{v}_trade_count"].mean()), 1),
        "pair_count_mean": round(float(df[f"{v}_pair_count"].mean()), 1),
    }

# 7. 会话信息
csv_start = str(df['bar_timestamp'].iloc[0])[:16].replace("T", " ")
csv_end = str(df['bar_timestamp'].iloc[-1])[:16].replace("T", " ")

# BS最早和最晚日期
bs_dates = sorted(e["date"][:10] for e in bs_events if "date" in e)
bs_date_range = f"{bs_dates[0]} ~ {bs_dates[-1]}" if bs_dates else "N/A"

session_info = {
    "ticker": "3690",
    "bars": len(df),
    "bar_range": f"{int(df['bar_index'].min())} - {int(df['bar_index'].max())}",
    "csv_time_range": f"{csv_start} ~ {csv_end}",
    "bs_date_range": bs_date_range,
    "total_bs_events": len(bs_events),
    "views": len(VIEWS),
    "columns": len(df.columns),
}


# ── 生成 HTML ──────────────────────────────────────────────

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>3690 回测报告</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, "SF Mono", "Menlo", "Consolas", monospace;
    background: #0d1117;
    color: #c9d1d9;
    padding: 24px;
    line-height: 1.5;
}}
h1 {{ font-size: 24px; color: #f0f6fc; margin-bottom: 4px; }}
h2 {{ font-size: 18px; color: #e6edf3; margin: 32px 0 12px; padding-bottom: 6px; border-bottom: 1px solid #30363d; }}
.subtitle {{ color: #8b949e; font-size: 13px; margin-bottom: 24px; }}

/* 概览卡片 */
.card-row {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 12px;
    margin-bottom: 24px;
}}
.card {{
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 14px 16px;
}}
.card .label {{ color: #8b949e; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }}
.card .value {{ color: #f0f6fc; font-size: 22px; font-weight: 600; margin-top: 4px; }}
.card .detail {{ color: #7d8590; font-size: 12px; margin-top: 2px; }}

/* 视图概览表 */
.view-grid {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin-bottom: 24px;
}}
.view-card {{
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 14px;
    border-top: 3px solid var(--vc);
}}
.view-card .vname {{ color: #f0f6fc; font-size: 15px; font-weight: 600; margin-bottom: 10px; }}
.view-card .vrow {{ display: flex; justify-content: space-between; font-size: 12px; padding: 3px 0; }}
.view-card .vrow .vk {{ color: #8b949e; }}
.view-card .vrow .vv {{ color: #c9d1d9; text-align: right; }}

/* 图表容器 */
.chart-wrap {{
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 16px;
    margin-bottom: 20px;
}}
.chart-wrap canvas {{ max-height: 360px; }}

/* 双列布局 */
.two-col {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
}}

/* 事件统计 */
.stat-grid {{
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 10px;
    margin-bottom: 16px;
}}
.stat-item {{
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 10px 14px;
    text-align: center;
}}
.stat-item .sn {{ color: #8b949e; font-size: 11px; }}
.stat-item .sv {{ color: #f0f6fc; font-size: 20px; font-weight: 600; }}

/* 事件标签颜色 */
.ev-added {{ color: #3fb950; }}
.ev-modified {{ color: #d29922; }}
.ev-removed {{ color: #f85149; }}
.ev-stable {{ color: #8b949e; }}

/* 响应式 */
@media (max-width: 900px) {{
    .view-grid {{ grid-template-columns: repeat(2, 1fr); }}
    .two-col {{ grid-template-columns: 1fr; }}
    .stat-grid {{ grid-template-columns: repeat(3, 1fr); }}
}}
</style>
</head>
<body>

<h1>3690 回测报告</h1>
<p class="subtitle">
    CSV数据: {session_info['csv_time_range']} &nbsp;|&nbsp;
    BS数据跨度: {session_info['bs_date_range']} &nbsp;|&nbsp;
    {session_info['bars']} bars ({session_info['bar_range']}) &nbsp;|&nbsp;
    {session_info['views']} 视图 &nbsp;|&nbsp;
    {session_info['columns']} 列
</p>

<!-- ── 板块一：概览仪表盘 ── -->
<h2>概览仪表盘</h2>

<div class="card-row">
    <div class="card">
        <div class="label">BS 总事件</div>
        <div class="value">{session_info['total_bs_events']}</div>
        <div class="detail">added / modified / removed / stable</div>
    </div>
    <div class="card">
        <div class="label">交易对</div>
        <div class="value">9-11</div>
        <div class="detail">pair_count 范围</div>
    </div>
    <div class="card">
        <div class="label">数据行数</div>
        <div class="value">{session_info['bars']}</div>
        <div class="detail">1分钟 bar</div>
    </div>
    <div class="card">
        <div class="label">JSONL 事件</div>
        <div class="value">{len(events)}</div>
        <div class="detail">含 session 事件</div>
    </div>
</div>

<div class="stat-grid" style="margin-bottom: 24px;">
    <div class="stat-item">
        <div class="sn">bs_added</div>
        <div class="sv ev-added">{event_counts.get('bs_added', 0)}</div>
    </div>
    <div class="stat-item">
        <div class="sn">bs_modified</div>
        <div class="sv ev-modified">{event_counts.get('bs_modified', 0)}</div>
    </div>
    <div class="stat-item">
        <div class="sn">bs_removed</div>
        <div class="sv ev-removed">{event_counts.get('bs_removed', 0)}</div>
    </div>
    <div class="stat-item">
        <div class="sn">bs_stable</div>
        <div class="sv ev-stable">{event_counts.get('bs_stable', 0)}</div>
    </div>
    <div class="stat-item">
        <div class="sn">总计</div>
        <div class="sv">{sum(event_counts.values())}</div>
    </div>
</div>

<!-- ── 视图对比卡片 ── -->
<div class="view-grid">
"""
for v in VIEWS:
    vs = view_summary[v]
    sig_str = ", ".join(f"{k}:{int(v)}" for k, v in sorted(vs["sig_dist"].items()))
    html += f"""
    <div class="view-card" style="--vc: {VIEW_COLORS[v]};">
        <div class="vname" style="color: {VIEW_COLORS[v]};">{VIEW_LABELS[v]}</div>
        <div class="vrow"><span class="vk">sig 分布</span><span class="vv">{sig_str}</span></div>
        <div class="vrow"><span class="vk">filter 范围</span><span class="vv">{vs['filter_min']} ~ {vs['filter_max']}</span></div>
        <div class="vrow"><span class="vk">filter 变动</span><span class="vv">{vs['filter_changes']}/{session_info['bars']}</span></div>
        <div class="vrow"><span class="vk">BS entry</span><span class="vv">{vs['entry_label']}</span></div>
        <div class="vrow"><span class="vk">BS exit</span><span class="vv">{vs['exit_label']}</span></div>
        <div class="vrow"><span class="vk">sig 持续(均值)</span><span class="vv">{vs['sig_dur_mean']} bars</span></div>
        <div class="vrow"><span class="vk">交易/配对</span><span class="vv">{vs['trade_count_mean']} / {vs['pair_count_mean']}</span></div>
    </div>"""

html += """
</div>

<!-- ── 板块二：多视图过滤趋势 ── -->
<h2>多视图过滤趋势</h2>
<div class="chart-wrap">
    <canvas id="filterChart"></canvas>
</div>

<!-- ── 板块三：信号分布 ── -->
<h2>信号(sig)分布</h2>
<div class="two-col">
    <div class="chart-wrap">
        <canvas id="sigBarChart"></canvas>
    </div>
    <div class="chart-wrap">
        <canvas id="sigTimeline"></canvas>
    </div>
</div>

<!-- ── 板块四：BS 事件分析 ── -->
<h2>BS 事件时间线</h2>
<div class="chart-wrap">
    <canvas id="bsTimelineChart"></canvas>
</div>

<div class="two-col">
    <div class="chart-wrap">
        <canvas id="bsEventPieChart"></canvas>
    </div>
    <div class="chart-wrap">
        <canvas id="bsViewChart"></canvas>
    </div>
</div>

<!-- ── 板块五：交易摘要 ── -->
<h2>交易摘要趋势</h2>
<div class="chart-wrap">
    <canvas id="tradeChart"></canvas>
</div>

<script>
// ── 内嵌数据 ──
const filterData = """ + json.dumps(filter_data) + """;
const sigData = """ + json.dumps(sig_data) + """;
const bsTimeline = """ + json.dumps(bs_timeline) + """;
const tradeData = """ + json.dumps(trade_data) + """;
const eventByView = """ + json.dumps(event_by_view) + """;
const eventCounts = """ + json.dumps(event_counts) + """;

// ── 公共配置 ──
Chart.defaults.color = '#8b949e';
Chart.defaults.borderColor = '#30363d';
Chart.defaults.font.family = '-apple-system, "SF Mono", Menlo, Consolas, monospace';
Chart.defaults.font.size = 11;

const barIndices = filterData.map(d => d.bar_index);
const colors = { v0: '#f59e0b', v1: '#10b981', v2: '#3b82f6', v3: '#ef4444' };
const views = ['v0', 'v1', 'v2', 'v3'];

// ── 板块二：过滤趋势 ──
new Chart(document.getElementById('filterChart'), {
    type: 'line',
    data: {
        labels: barIndices,
        datasets: views.map(v => ({
            label: v + ' (1m/5m/15m/60m)'.split('/')[views.indexOf(v)],
            data: filterData.map(d => d[v]),
            borderColor: colors[v],
            backgroundColor: 'transparent',
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0.1,
        }))
    },
    options: {
        responsive: true,
        interaction: { mode: 'index', intersect: false },
        plugins: {
            title: { display: true, text: '各视图过滤值随时间变化', color: '#e6edf3', font: { size: 14 } },
            tooltip: { callbacks: { label: ctx => ctx.dataset.label + ': ' + ctx.parsed.y.toFixed(4) } }
        },
        scales: {
            x: { title: { display: true, text: 'bar_index', color: '#8b949e' } },
            y: { title: { display: true, text: 'filtered 值', color: '#8b949e' }, ticks: { callback: v => v.toFixed(2) } }
        }
    }
});

// ── 板块三：Sig 分布柱状图 ──
const sigAgg = { '-1': [], '0': [], '1': [] };
views.forEach(v => {
    const dist = {};
    sigData.forEach(d => { dist[d[v]] = (dist[d[v]] || 0) + 1; });
    [-1, 0, 1].forEach(s => sigAgg[String(s)].push(dist[s] || 0));
});

new Chart(document.getElementById('sigBarChart'), {
    type: 'bar',
    data: {
        labels: views.map(v => v + ' (' + '1m/5m/15m/60m'.split('/')[views.indexOf(v)] + ')'),
        datasets: [
            { label: 'sig = -1', data: sigAgg['-1'], backgroundColor: '#f85149' },
            { label: 'sig = 0', data: sigAgg['0'], backgroundColor: '#8b949e' },
            { label: 'sig = 1', data: sigAgg['1'], backgroundColor: '#3fb950' },
        ]
    },
    options: {
        responsive: true,
        plugins: {
            title: { display: true, text: '各视图 sig 分布', color: '#e6edf3', font: { size: 14 } },
        },
        scales: {
            x: { stacked: true },
            y: { stacked: true, title: { display: true, text: 'bar 计数' } }
        }
    }
});

// ── Sig 时间线（每视图独立折线） ──
new Chart(document.getElementById('sigTimeline'), {
    type: 'line',
    data: {
        labels: barIndices,
        datasets: views.map((v, vi) => ({
            label: v,
            data: sigData.map(d => d[v] + vi * 3),
            borderColor: colors[v],
            backgroundColor: 'transparent',
            borderWidth: 1.5,
            pointRadius: 0,
            stepped: true,
        }))
    },
    options: {
        responsive: true,
        plugins: {
            title: { display: true, text: 'sig 状态时间线（垂直偏移便于对比）', color: '#e6edf3', font: { size: 14 } },
            tooltip: {
                callbacks: {
                    label: ctx => {
                        const vi = views.indexOf(ctx.dataset.label);
                        const raw = ctx.parsed.y - vi * 3;
                        return ctx.dataset.label + ': sig=' + raw;
                    }
                }
            }
        },
        scales: {
            x: { title: { display: true, text: 'bar_index', color: '#8b949e' } },
            y: {
                ticks: {
                    callback: v => {
                        const vi = Math.round(v / 3);
                        return views[vi] || '';
                    },
                    stepSize: 3,
                },
                title: { display: true, text: '视图 (sig: -1/0/1)', color: '#8b949e' }
            }
        }
    }
});

// ── 板块四：BS 事件时间线 ──
const bsBarIndices = Object.keys(bsTimeline).map(Number).sort((a,b) => a - b);
const evTypes = ['bs_added', 'bs_modified', 'bs_removed', 'bs_stable'];
const evColors = { bs_added: '#3fb950', bs_modified: '#d29922', bs_removed: '#f85149', bs_stable: '#8b949e' };

new Chart(document.getElementById('bsTimelineChart'), {
    type: 'bar',
    data: {
        labels: bsBarIndices,
        datasets: evTypes.map(et => ({
            label: et.replace('bs_', ''),
            data: bsBarIndices.map(bi => bsTimeline[bi] ? (bsTimeline[bi][et] || 0) : 0),
            backgroundColor: evColors[et],
        }))
    },
    options: {
        responsive: true,
        plugins: {
            title: { display: true, text: 'BS 事件按 bar_index 分布（所有视图合计）', color: '#e6edf3', font: { size: 14 } },
        },
        scales: {
            x: { stacked: true, title: { display: true, text: 'bar_index' } },
            y: { stacked: true, title: { display: true, text: '事件数' } }
        }
    }
});

// ── BS 事件饼图 ──
new Chart(document.getElementById('bsEventPieChart'), {
    type: 'doughnut',
    data: {
        labels: ['bs_added', 'bs_modified', 'bs_removed', 'bs_stable'],
        datasets: [{
            data: evTypes.map(et => eventCounts[et] || 0),
            backgroundColor: evTypes.map(et => evColors[et]),
        }]
    },
    options: {
        responsive: true,
        plugins: {
            title: { display: true, text: 'BS 事件类型分布', color: '#e6edf3', font: { size: 14 } },
        }
    }
});

// ── 按视图 BS 事件 ──
const evViewLabels = Object.keys(eventByView).sort();
const evViewDatasets = evTypes.map(et => ({
    label: et.replace('bs_', ''),
    data: evViewLabels.map(v => (eventByView[v] && eventByView[v][et]) ? eventByView[v][et] : 0),
    backgroundColor: evColors[et],
}));

new Chart(document.getElementById('bsViewChart'), {
    type: 'bar',
    data: {
        labels: evViewLabels.map(v => v.replace('_', ' ')),
        datasets: evViewDatasets,
    },
    options: {
        responsive: true,
        plugins: {
            title: { display: true, text: 'BS 事件按视图分布', color: '#e6edf3', font: { size: 14 } },
        },
        scales: {
            x: { stacked: true },
            y: { stacked: true, title: { display: true, text: '事件数' } }
        }
    }
});

// ── 板块五：交易摘要（取 v3 的数据作为示例） ──
const v3Trades = tradeData.filter(t => t.view === 'v3 60分钟');
new Chart(document.getElementById('tradeChart'), {
    type: 'line',
    data: {
        labels: v3Trades.map(t => t.step),
        datasets: [
            {
                label: 'trade_count',
                data: v3Trades.map(t => t.trade_count),
                borderColor: '#3b82f6',
                backgroundColor: 'transparent',
                borderWidth: 2,
                pointRadius: 3,
                yAxisID: 'y',
            },
            {
                label: 'long_count',
                data: v3Trades.map(t => t.long_count),
                borderColor: '#3fb950',
                backgroundColor: 'transparent',
                borderWidth: 1.5,
                pointRadius: 2,
                borderDash: [5, 5],
                yAxisID: 'y',
            },
            {
                label: 'short_count',
                data: v3Trades.map(t => t.short_count),
                borderColor: '#f85149',
                backgroundColor: 'transparent',
                borderWidth: 1.5,
                pointRadius: 2,
                borderDash: [5, 5],
                yAxisID: 'y',
            },
            {
                label: 'win_rate',
                data: v3Trades.map(t => t.win_rate * 100),
                borderColor: '#d29922',
                backgroundColor: 'transparent',
                borderWidth: 2,
                pointRadius: 3,
                yAxisID: 'y1',
            },
        ]
    },
    options: {
        responsive: true,
        interaction: { mode: 'index', intersect: false },
        plugins: {
            title: { display: true, text: '交易摘要趋势 (v3 60分钟)', color: '#e6edf3', font: { size: 14 } },
        },
        scales: {
            x: { title: { display: true, text: 'step' } },
            y: {
                type: 'linear',
                position: 'left',
                title: { display: true, text: '交易笔数' },
                min: 0,
            },
            y1: {
                type: 'linear',
                position: 'right',
                title: { display: true, text: '胜率 %' },
                min: 0,
                max: 100,
                grid: { drawOnChartArea: false },
            }
        }
    }
});

</script>

</body>
</html>
"""

# ── 写入文件 ──
OUTPUT.write_text(html, encoding="utf-8")
print(f"报告已生成: {OUTPUT}")
print(f"文件大小: {OUTPUT.stat().st_size / 1024:.1f} KB")
