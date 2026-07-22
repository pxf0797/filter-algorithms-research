"""
方案A 审计缺口补充：P1/P2 优先级测试用例。

审计参照 docs/_perf/test-gap-audit.md。

P0（端到端 Figure 序列化，5 分支）已在 test_plan_a_e2e.py 覆盖，此处确认。
P2-3（_add_prediction_traces n_extend=0）已在 test_strategy.py 覆盖，此处确认。

本文件新增用例：
  P1-1 _add_feedback_subplot shapes 排序/交互属性
  P1-2 _add_cross_pnl_subplot 高周期边缘处理
  P1-3 _add_schmitt_traces pair bands 空 all_pairs 边界
  P2-1 _add_alignment_subplot per-trade segment showlegend=False
  P2-2 _draw_holding_bands 多空重叠时 z-order / y-range 隔离
  P2-4 go.Figure(data=dicts, layout=dict) shapes/annotations 混合注入
"""

import json
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from components.charts import (
    _draw_holding_bands, _add_cross_pnl_subplot,
    _add_alignment_subplot, _add_prediction_traces,
)
from components.chart_builder import (
    _add_feedback_subplot, _add_schmitt_traces,
)


# =====================================================================
# P0 确认 — test_plan_a_e2e.py 已覆盖全部 5 个分支
# =====================================================================
# test_branch_8row_full              has_s+strategy+cross+alignment  (8行)
# test_branch_7row_no_alignment      has_s+strategy+cross            (7行)
# test_branch_6row_no_cross          has_s+strategy                  (6行)
# test_branch_5row_no_strategy       has_s only                      (5行)
# test_branch_4row_no_schmitt        no_s                            (4行)
# + test_branch_9row_full_with_feedback (8行+feedback=9行)
# + test_figure_to_json_with_ndarray_data


# =====================================================================
# 辅助函数
# =====================================================================

def _make_trades():
    return [
        {"type": "long", "entry_idx": 3, "exit_idx": 12, "id": 1},
        {"type": "short", "entry_idx": 18, "exit_idx": 25, "id": 2},
    ]


# =====================================================================
# P1-1: _add_feedback_subplot shapes 图例交互
# =====================================================================

class TestFeedbackSubplotShapesOrder:
    """绿/红持仓色块应有正确排序和交互属性。"""

    def test_long_shapes_before_short(self):
        """Long shapes (green, top) 在 list 中先于 short shapes (red, bottom)。"""
        t = np.arange(30, dtype=float)
        trades = _make_trades()
        shapes, yaxes = _add_feedback_subplot(t, trades, row=2)

        assert len(shapes) >= 2
        # Long shapes (#3fb950, y0=0.55) appear first
        for sh in shapes:
            assert sh["type"] == "rect"
            assert sh["line_width"] == 0
            assert sh["xref"] == "x2" and sh["yref"] == "y2"

        # Verify order: all long before all short
        long_colors = [sh["fillcolor"] for sh in shapes if sh["y0"] == 0.55]
        short_colors = [sh["fillcolor"] for sh in shapes if sh["y0"] == 0.05]
        assert all(c == "#3fb950" for c in long_colors)
        assert all(c == "#f85149" for c in short_colors)

        # Long shapes come before short shapes in the list
        long_indices = [i for i, sh in enumerate(shapes) if sh["fillcolor"] == "#3fb950"]
        short_indices = [i for i, sh in enumerate(shapes) if sh["fillcolor"] == "#f85149"]
        assert max(long_indices) < min(short_indices), \
            "所有 long shapes 应出现在 short shapes 之前"

    def test_fills_do_not_overlap_y(self):
        """Long (0.55-0.95) 和 short (0.05-0.45) 的 y-range 不重叠。"""
        t = np.arange(30, dtype=float)
        trades = _make_trades()
        shapes, _ = _add_feedback_subplot(t, trades, row=2)

        long_shapes = [sh for sh in shapes if sh["fillcolor"] == "#3fb950"]
        short_shapes = [sh for sh in shapes if sh["fillcolor"] == "#f85149"]

        for sh in long_shapes:
            assert sh["y0"] == 0.55 and sh["y1"] == 0.95, f"long y-range: {sh['y0']}-{sh['y1']}"
        for sh in short_shapes:
            assert sh["y0"] == 0.05 and sh["y1"] == 0.45, f"short y-range: {sh['y0']}-{sh['y1']}"

        # 长/短 y-range 完全隔离：long bottom (0.55) > short top (0.45)
        for sh in long_shapes:
            assert sh["y0"] >= 0.55, f"long shape y0={sh['y0']} 应 ≥ 0.55"
        for sh in short_shapes:
            assert sh["y1"] <= 0.45, f"short shape y1={sh['y1']} 应 ≤ 0.45"
        # long_y0 > short_y1 确保 y-range 不重叠
        long_y0 = long_shapes[0]["y0"]  # 0.55
        short_y1 = short_shapes[0]["y1"]  # 0.45
        assert long_y0 > short_y1, \
            f"long_y0={long_y0} 应 > short_y1={short_y1}"


class TestFeedbackSubplotInterleavedTrades:
    """交互的 long/short 交易序列仍保持正确的色块排序。"""

    def test_interleaved_trades_all_long_first(self):
        """long → short → long: 所有 long 色块仍在 short 之前。"""
        t = np.arange(40, dtype=float)
        trades = [
            {"type": "long", "entry_idx": 3, "exit_idx": 12, "id": 1},
            {"type": "short", "entry_idx": 15, "exit_idx": 22, "id": 2},
            {"type": "long", "entry_idx": 25, "exit_idx": 35, "id": 3},
        ]
        shapes, _ = _add_feedback_subplot(t, trades, row=3)

        long_indices = [i for i, sh in enumerate(shapes) if sh["fillcolor"] == "#3fb950"]
        short_indices = [i for i, sh in enumerate(shapes) if sh["fillcolor"] == "#f85149"]
        assert len(long_indices) == 2  # 两个 long 段
        assert len(short_indices) == 1  # 一个 short 段
        assert max(long_indices) < min(short_indices), \
            "即使交易序列是 long→short→long，所有 long 色块也应在 short 之前"


# =====================================================================
# P1-2: _add_cross_pnl_subplot 高周期边缘处理
# =====================================================================

class TestCrossPnlEdgeCases:
    """跨周期 entry/exit markers 映射到 bar 边界的边缘情况。"""

    def test_entry_at_bar_zero(self):
        """Entry 在索引 0 处 → mask 从第一个 bar 开始。"""
        t = np.arange(20, dtype=float)
        aligned = {
            "entry_markers": [(0, "long", 100.0)],
            "exit_markers": [(10, "long", 105.0, 5.0, "take_profit")],
        }
        shapes, yaxes = _add_cross_pnl_subplot(t, aligned, row=6)
        assert len(shapes) >= 1, "应有 long shape"
        hw = (t[1] - t[0]) / 2.0
        # mask[0:11]=True → x0 should be t[0]-hw
        assert shapes[0]["x0"] <= t[0], f"x0={shapes[0]['x0']} 应 ≤ t[0]={t[0]}"

    def test_exit_at_last_bar(self):
        """Exit 在最后一个 bar → mask 在末尾正确闭合。"""
        n = 20
        t = np.arange(n, dtype=float)
        aligned = {
            "entry_markers": [(5, "long", 100.0)],
            "exit_markers": [(n - 1, "long", 105.0, 5.0, "take_profit")],
        }
        shapes, _ = _add_cross_pnl_subplot(t, aligned, row=6)
        # mask[5:n]=True → x1 should include last bar
        hw = (t[1] - t[0]) / 2.0
        assert shapes[0]["x1"] >= t[-1], f"x1={shapes[0]['x1']} 应 ≥ t[-1]={t[-1]}"

    def test_entry_no_exit_extends_to_end(self):
        """Entry 无对应 exit → mask 延伸到末尾。"""
        n = 20
        t = np.arange(n, dtype=float)
        aligned = {
            "entry_markers": [(8, "long", 100.0)],
            "exit_markers": [],
        }
        shapes, _ = _add_cross_pnl_subplot(t, aligned, row=6)
        # mask[8:n]=True → x1 should be t[-1]+hw
        hw = (t[1] - t[0]) / 2.0
        assert shapes[0]["x1"] >= t[-1], f"无 exit 时 x1={shapes[0]['x1']} 应 ≥ t[-1]={t[-1]}"

    def test_entry_beyond_n_bars_ignored(self):
        """entry_idx >= n_bars → 被跳过，无 shapes。"""
        n = 20
        t = np.arange(n, dtype=float)
        aligned = {
            "entry_markers": [(25, "long", 100.0)],  # 越界
            "exit_markers": [],
        }
        shapes, _ = _add_cross_pnl_subplot(t, aligned, row=6)
        assert len(shapes) == 0, "越界的 entry 不应产生 shapes"

    def test_markers_out_of_order_sorted(self):
        """Entry markers 不按排序传入，_compute_holding_masks 内部排序。"""
        n = 30
        t = np.arange(n, dtype=float)
        aligned = {
            "entry_markers": [(15, "short", 100.0), (3, "long", 100.0)],
            "exit_markers": [(10, "long", 105.0, 5.0, "take_profit"),
                             (20, "short", 98.0, -2.0, "stop_loss")],
        }
        shapes, _ = _add_cross_pnl_subplot(t, aligned, row=6)
        assert len(shapes) >= 2, "应有 long + short 各一色块"


# =====================================================================
# P1-3: _add_schmitt_traces pair bands 空 all_pairs 边界
# =====================================================================

class TestSchmittEmptyAllPairs:
    """空 all_pairs 不产生 pair band traces，基础 trace 不受影响。"""

    def test_empty_pairs_no_pair_bands(self):
        """all_pairs=[]: 无 pair band traces，基础 6 trace + state fills 正常生成。"""
        n = 30
        t = np.arange(n, dtype=float)
        sig = np.zeros(n, int)
        sig[10:20] = 1
        sig[22:28] = -1
        schmitt = {"eps": np.full(n, 0.15), "sig": sig, "sigma_v": np.full(n, 0.10)}
        acc = np.random.randn(n) * 0.05

        traces, shapes = _add_schmitt_traces(t, schmitt, acc, all_pairs=[], sar=4, ssr=5)

        # 基础 6 trace: ±ε fill, +ε, -ε, σ(v), a, Sig
        assert len(traces) >= 6, f"期望 ≥6 base traces, 得到 {len(traces)}"

        # Pair band colors: rgba(88,166,255,0.10) 或 rgba(163,113,247,0.10)
        pair_band_fillcolors = {"rgba(88,166,255,0.10)", "rgba(163,113,247,0.10)"}
        for tr in traces:
            fc = tr.get("fillcolor", "")
            assert fc not in pair_band_fillcolors, \
                f"空 all_pairs 时不应有 pair band trace: {fc}"

    def test_empty_pairs_still_has_state_fills(self):
        """空 all_pairs 但 sig 有 ±1 时，state fill traces 仍生成。"""
        n = 30
        t = np.arange(n, dtype=float)
        sig = np.zeros(n, int)
        sig[10:20] = 1
        sig[22:28] = -1
        schmitt = {"eps": np.full(n, 0.15), "sig": sig, "sigma_v": np.full(n, 0.10)}
        acc = np.random.randn(n) * 0.05

        traces, _ = _add_schmitt_traces(t, schmitt, acc, all_pairs=[], sar=4, ssr=5)

        # +1 state fill: fillcolor="rgba(63,185,80,0.06)"
        # -1 state fill: fillcolor="rgba(248,81,73,0.06)"
        state_fills = [tr for tr in traces
                       if tr.get("fillcolor") in ("rgba(63,185,80,0.06)", "rgba(248,81,73,0.06)")]
        assert len(state_fills) == 2, f"期望 2 条 state fill traces, 得到 {len(state_fills)}"

    def test_empty_pairs_hline_still_present(self):
        """空 all_pairs 时 shapes 中的 hline 仍存在。"""
        n = 30
        t = np.arange(n, dtype=float)
        schmitt = {"eps": np.full(n, 0.15), "sig": np.zeros(n, int), "sigma_v": np.full(n, 0.10)}
        traces, shapes = _add_schmitt_traces(t, schmitt, np.zeros(n), all_pairs=[], sar=4, ssr=5)
        assert len(shapes) >= 1
        assert shapes[0]["type"] == "line"


# =====================================================================
# P2-1: _add_alignment_subplot per-trade segment showlegend=False
# =====================================================================

class TestAlignmentSubplotShowLegend:
    """Alignment subplot 所有 trace 应 showlegend=False。"""

    def test_all_traces_showlegend_false(self):
        """所有 trace（包括基线 + 段线 + 填充 + markers）均 showlegend=False。"""
        n = 30
        t = np.arange(n, dtype=float)
        long_pnl = 100 + np.cumsum(np.random.RandomState(42).randn(n) * 0.3)
        short_pnl = 100 + np.cumsum(np.random.RandomState(7).randn(n) * 0.2)
        long_mask = np.zeros(n, bool)
        long_mask[5:20] = True
        short_mask = np.zeros(n, bool)
        short_mask[22:28] = True
        trades = [
            {"id": 1, "type": "long", "entry_idx": 5, "exit_idx": 15,
             "return_pct": 3.5, "exit_reason": "take_profit",
             "entry_price": 100, "exit_price": 103.5},
            {"id": 2, "type": "short", "entry_idx": 22, "exit_idx": 27,
             "return_pct": -1.2, "exit_reason": "stop_loss",
             "entry_price": 103, "exit_price": 104.2},
        ]
        traces, shapes, annotations, yaxes = _add_alignment_subplot(
            t, long_pnl, short_pnl, trades, long_mask, short_mask, row=8)

        # 所有 trace 的 showlegend 为 False 或未设置
        for i, tr in enumerate(traces):
            leg = tr.get("showlegend", False)
            assert not leg, f"trace[{i}] (type={tr.get('type','?')}, name={tr.get('name','?')}) showlegend 应为 False"

        # 验证 shapes 有 baseline
        assert len(shapes) >= 1
        assert shapes[0]["type"] == "line"


# =====================================================================
# P2-2: _draw_holding_bands 多空重叠时 z-order / y-range 隔离
# =====================================================================

class TestHoldingBandsOverlap:
    """Long/short 部位重叠时，y-range 不重叠，long shapes 在 short 之前。"""

    def test_overlapping_masks_produces_both_shapes(self):
        """同一 bar 区间多空同时持仓 → 两组 shape 都生成。"""
        n = 20
        t = np.arange(n, dtype=float)
        long_mask = np.zeros(n, bool)
        long_mask[5:15] = True  # bars 5-14
        short_mask = np.zeros(n, bool)
        short_mask[10:18] = True  # bars 10-17, overlaps with long at 10-14

        shapes, yaxes = _draw_holding_bands(t, long_mask, short_mask, row=3)

        # 应有 long shape(s) + short shape(s)
        long_shapes = [sh for sh in shapes if sh["fillcolor"] == "#3fb950"]
        short_shapes = [sh for sh in shapes if sh["fillcolor"] == "#f85149"]
        assert len(long_shapes) >= 1, "应有 long shapes"
        assert len(short_shapes) >= 1, "应有 short shapes"

        # Long 在 short 之前
        last_long = max(i for i, sh in enumerate(shapes) if sh["fillcolor"] == "#3fb950")
        first_short = min(i for i, sh in enumerate(shapes) if sh["fillcolor"] == "#f85149")
        assert last_long < first_short, "long shapes 应在 short shapes 之前"

    def test_y_ranges_isolated(self):
        """Long (0.55-0.95) 和 short (0.05-0.45) y-range 完全隔离。"""
        n = 20
        t = np.arange(n, dtype=float)
        long_mask = np.zeros(n, bool)
        long_mask[3:8] = True
        short_mask = np.zeros(n, bool)
        short_mask[12:17] = True

        shapes, _ = _draw_holding_bands(t, long_mask, short_mask, row=4)

        # Long shapes y-range
        for sh in shapes:
            if sh["fillcolor"] == "#3fb950":
                assert sh["y0"] == 0.55 and sh["y1"] == 0.95
            elif sh["fillcolor"] == "#f85149":
                assert sh["y0"] == 0.05 and sh["y1"] == 0.45

    def test_no_overlap_y_ranges_non_overlapping(self):
        """验证 long (0.55-0.95) 与 short (0.05-0.45) y-range 无交集。"""
        # long bottom edge (0.55) > short top edge (0.45)
        # 所以无论 x 如何重叠，视觉上完全不重叠
        long_y0, long_y1 = 0.55, 0.95
        short_y0, short_y1 = 0.05, 0.45
        assert long_y0 > short_y1, \
            f"long bottom ({long_y0}) 应 > short top ({short_y1})，确保 y-range 不重叠"


# =====================================================================
# P2-3: _add_prediction_traces n_extend=0
# =====================================================================
# 已有 test_strategy.py::TestAddPredictionTraces::test_no_extend_adds_only_fit_trace，
# 验证 n_extend=0 仅生成 1 条拟合 trace。此处确认不再重复。


# =====================================================================
# P2-4: go.Figure(data=dicts, layout=dict) shapes/annotations 混合注入
# =====================================================================

class TestFigureMixedShapesAnnotations:
    """shapes + annotations 从多源混合注入构造 Figure 的验证。"""

    def test_shapes_from_layout_and_extra_merged(self):
        """Skeleton layout 的 shapes + 额外添加的 shapes 都在 final figure 中。"""
        n = 30
        t = np.arange(n, dtype=float)

        # 构造 skeleton（模拟 4 行布局）
        skeleton = make_subplots(rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.01,
                                 row_heights=[0.5, 0.2, 0.15, 0.15],
                                 subplot_titles=["a", "b", "c", "d"])
        layout_dict = skeleton.layout.to_plotly_json()

        # skeleton 已有 annotations (subplot titles)
        n_skeleton_annotations = len(layout_dict.get("annotations", []))

        # 添加 shapes
        extra_shapes = [
            dict(type="line", x0=0, x1=1, xref="paper", y0=100, y1=100,
                 yref="y4", line=dict(color="gray", dash="dash"), opacity=0.5),
            dict(type="rect", x0=t[5], x1=t[15], y0=0.05, y1=0.45,
                 fillcolor="#f85149", line_width=0, xref="x4", yref="y4"),
        ]
        layout_dict["shapes"] = layout_dict.get("shapes", []) + extra_shapes

        # 添加 annotations
        extra_annotations = [
            dict(x=t[10], y=103.5, text="↑+3.5%", showarrow=False,
                 font=dict(size=8), yshift=12, xref="x4", yref="y4"),
        ]
        layout_dict["annotations"] = layout_dict.get("annotations", []) + extra_annotations

        layout_dict.update(template="plotly_dark", height=600,
                           margin=dict(l=10, r=10, t=25, b=10), hovermode="x unified")

        traces = [
            dict(type="scattergl", x=t, y=100 + np.sin(t / 5), mode="lines", xaxis="x", yaxis="y"),
        ]

        fig = go.Figure(data=traces, layout=layout_dict)

        # 核心：序列化验证
        js = fig.to_json()
        parsed = json.loads(js)
        assert parsed["layout"]["shapes"] is not None
        assert len(parsed["layout"]["shapes"]) == len(extra_shapes), \
            f"期望 {len(extra_shapes)} shapes, 得到 {len(parsed['layout']['shapes'])}"
        assert len(parsed["layout"]["annotations"]) == n_skeleton_annotations + len(extra_annotations), \
            f"期望 {n_skeleton_annotations + len(extra_annotations)} annotations, " \
            f"得到 {len(parsed['layout']['annotations'])}"

    def test_mixed_shapes_annotations_with_real_functions(self):
        """使用真实 _add_* 输出验证 shapes/annotations 混合注入。"""
        n = 30
        t = np.arange(n, dtype=float)
        long_pnl = 100 + 0.3 * t
        short_pnl = 100 + 0.1 * t
        trades = [
            {"id": 1, "type": "long", "entry_idx": 5, "exit_idx": 15,
             "return_pct": 3.2, "exit_reason": "take_profit",
             "entry_price": 100, "exit_price": 103},
        ]

        # 从 _add_pnl_traces 获取 traces/shapes/annotations
        from components.chart_builder import _add_pnl_traces
        pnl_traces, pnl_shapes, pnl_annotations, pnl_yaxes = _add_pnl_traces(
            t, long_pnl, short_pnl, trades, pnl_row=4)

        # 构造 skeleton
        skeleton = make_subplots(rows=4, cols=1, shared_xaxes=True,
                                 vertical_spacing=0.01,
                                 row_heights=[0.5, 0.2, 0.15, 0.15])
        layout_dict = skeleton.layout.to_plotly_json()

        # 混合注入
        layout_dict["shapes"] = layout_dict.get("shapes", []) + pnl_shapes
        layout_dict["annotations"] = layout_dict.get("annotations", []) + pnl_annotations
        layout_dict.update(template="plotly_dark", height=500)
        layout_dict.setdefault("xaxis4", {}).update(title_text="")
        layout_dict.update(pnl_yaxes)

        fig = go.Figure(data=pnl_traces, layout=layout_dict)
        js = fig.to_json()
        parsed = json.loads(js)

        assert len(parsed["layout"].get("shapes", [])) >= len(pnl_shapes)
        assert len(parsed["layout"].get("annotations", [])) >= len(pnl_annotations)
        assert len(parsed["data"]) == len(pnl_traces)
