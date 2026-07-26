"""端到端测试：方案A go.Figure(data=dicts, layout=dict) 合格性验证。

使用真实 _add_* 函数输出构造 Figure，覆盖全部 5 个 _determine_subplot_layout 分支，
验证 fig.to_json() 序列化不报错。最复杂 8 行布局必须覆盖。
"""

import json
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots


# =====================================================================
# 辅助: 生成测试数据
# =====================================================================

def _ohlc(n):
    import pandas as pd
    return pd.DataFrame({"Open": np.linspace(100, 110, n),
                         "High": np.linspace(101, 111, n),
                         "Low": np.linspace(99, 109, n),
                         "Close": np.linspace(100, 110, n)})


def _make_data(n=60):
    """生成 60 点测试数据：时间轴 + 带噪信号 + OHLC."""
    t = np.arange(n, dtype=float)
    noisy = 100 + np.sin(t / 5.0) * 3 + np.random.RandomState(42).randn(n) * 0.3
    filtered = 100 + np.sin(t / 5.0) * 2.8  # 干净拟合
    filtered2 = filtered + np.sin(t / 3.0) * 0.5  # 第二滤波
    ohlc = _ohlc(n)
    return t, noisy, filtered, filtered2, ohlc


def _make_schmitt(n=60):
    """生成施密特信号."""
    sig = np.zeros(n, dtype=int)
    sig[12:30] = 1
    sig[38:55] = -1
    return {
        "eps": np.full(n, 0.15),
        "sig": sig,
        "sigma_v": np.full(n, 0.10),
    }


def _make_trades():
    """生成测试交易记录."""
    return [
        {"id": 1, "type": "long", "entry_idx": 12, "exit_idx": 30,
         "return_pct": 3.5, "exit_reason": "take_profit",
         "entry_price": 100, "exit_price": 103.5},
        {"id": 2, "type": "short", "entry_idx": 38, "exit_idx": 52,
         "return_pct": -1.2, "exit_reason": "stop_loss",
         "entry_price": 103, "exit_price": 104.2},
    ]


def _make_higher_pnl(n=60):
    """生成高周期 PnL 数据（用于 cross_pnl/alignment）。"""
    np.random.seed(1)
    n_higher = 15
    higher_t = np.linspace(0, n - 1, n_higher)
    higher_lp = 100 + np.cumsum(np.random.randn(n_higher) * 0.5)
    higher_sp = 100 + np.cumsum(np.random.randn(n_higher) * 0.3)
    return {
        "dates": higher_t,
        "long_pnl": higher_lp,
        "short_pnl": higher_sp,
        "trade_records": [],
        "entry_markers": [(12, "long", 102.0), (38, "short", 101.0)],
        "exit_markers": [(28, "long", 105.0, 3.0, "take_profit"),
                         (50, "short", 100.0, -1.0, "stop_loss")],
    }


# =====================================================================
# 方案A 端到端测试: 模拟 _render_chart 的 trace/shape/annotation 收集
# =====================================================================

class TestPlanAE2eFigureConstruction:
    """真实 _add_* 输出 → go.Figure(data=, layout=) → fig.to_json() 不报错。

    覆盖全部 5 个 _determine_subplot_layout 分支。
    """

    # ---- 分支 1: has_s+strategy+cross+alignment (8行，最复杂) ----
    def _build_e2e_figure(self, has_s=True, has_strategy=True,
                          has_cross=True, has_alignment=True,
                          has_feedback=False):
        """模拟 _render_chart 的 figure 构造流程，返回 fig + 元信息。"""
        n = 60
        t, noisy, filtered, filtered2, ohlc = _make_data(n)

        # 导入所有 _add_* 函数
        from browse.app import (
            _add_main_price_traces, _add_residual_traces, _add_schmitt_traces,
            _add_pnl_traces, _add_feedback_subplot, _determine_subplot_layout,
            _insert_feedback_row,
        )
        from browse.charts import (
            _add_prediction_traces, _add_cross_pnl_subplot,
            _add_alignment_subplot,
        )
        from engine.strategy import _compute_holding_masks

        # 准备数据
        schmitt = _make_schmitt(n) if has_s else None
        all_pairs = [(12, 28), (38, 50)] if has_s else []
        trades = _make_trades()
        long_pnl = 100 + np.cumsum(np.random.RandomState(0).randn(n) * 0.3)
        short_pnl = 100 + np.cumsum(np.random.RandomState(1).randn(n) * 0.2)

        # 获取布局参数
        rows, rh, titles, mr, rr, vr, sar, ssr, ar, pnl_row, cross_row, align_row = \
            _determine_subplot_layout(has_s, has_strategy, has_cross, has_alignment, "日线")

        # 可选的 feedback 行插入
        feedback_row = None
        if has_feedback and pnl_row is not None:
            rows, rh, titles, feedback_row, cross_row, align_row = _insert_feedback_row(
                rows, rh, titles, pnl_row, cross_row, align_row)

        # 收集所有 traces/shapes/annotations
        all_traces, all_shapes, all_annotations = [], [], []
        _layout_updates = {}

        # mr 一定为 1（所有分支都返回 mr=1），但 trace 的 axis 应该为 "x"/"y"
        tr = _add_main_price_traces(t, noisy, ohlc, filtered, filtered2,
                                    {"fc": "#00d4aa", "_dual": False, "fc2": "#ff6b6b"}, mr)
        all_traces += tr

        # 预测曲线（模拟一组预测对）
        # 用简化的 fit_result 代替真实 _compute_prediction_pairs
        for pair_start, pair_end in all_pairs:
            y_fit = np.polyval((0.0, 1.0, 100), t[pair_start:pair_end + 1] - pair_start)
            fit_result = {"a": 0.0, "b": 1.0, "c": 100.0, "y_fit": y_fit, "x0": float(pair_start)}
            tr = _add_prediction_traces(
                t, filtered, fit_result, pair_start, pair_end,
                row=mr, n_extend=10, show_legend=True)
            all_traces += tr

        acc, _tr, _sh = _add_residual_traces(t, filtered, noisy, filtered2,
                                              {"fc": "#00d4aa"}, rr, vr)
        all_traces += _tr
        all_shapes += _sh

        if has_s:
            _tr, _sh = _add_schmitt_traces(t, schmitt, acc, all_pairs, sar, ssr)
            all_traces += _tr
            all_shapes += _sh

        if has_strategy:
            _tr, _sh, _an, _ya = _add_pnl_traces(t, long_pnl, short_pnl, trades, pnl_row)
            all_traces += _tr
            all_shapes += _sh
            all_annotations += _an
            _layout_updates.update(_ya)

        if has_feedback and feedback_row is not None:
            _sh, _ya = _add_feedback_subplot(t, trades, feedback_row)
            all_shapes += _sh
            _layout_updates.update(_ya)

        if has_cross:
            higher_pnl = _make_higher_pnl(n)
            _sh, _ya = _add_cross_pnl_subplot(t, higher_pnl, row=cross_row)
            all_shapes += _sh
            _layout_updates.update(_ya)

        if has_alignment:
            higher_pnl = _make_higher_pnl(n)
            long_mask, short_mask = _compute_holding_masks(
                n, higher_pnl["entry_markers"], higher_pnl["exit_markers"])
            _tr, _sh, _an, _ya = _add_alignment_subplot(
                t, long_pnl, short_pnl, trades, long_mask, short_mask, row=align_row)
            all_traces += _tr
            all_shapes += _sh
            all_annotations += _an
            _layout_updates.update(_ya)

        if ar is not None and not np.all(np.isnan(filtered)):
            all_traces.append(dict(type="scattergl", x=t, y=acc, mode="lines",
                                   name="a", line=dict(color="#ffa502", width=1.5),
                                   xaxis=f"x{ar}", yaxis=f"y{ar}"))
            all_shapes.append(dict(type="line", x0=0, x1=1, xref="paper", y0=0, y1=0,
                                   yref=f"y{ar}", line=dict(color="gray", dash="dash"),
                                   opacity=0.5))

        # 构造 layout — 模拟 _render_chart
        _skeleton = make_subplots(rows=rows, cols=1, shared_xaxes=True,
                                  vertical_spacing=0.01, row_heights=rh,
                                  subplot_titles=titles)
        layout_dict = _skeleton.layout.to_plotly_json()

        # shapes + annotations
        layout_dict["shapes"] = layout_dict.get("shapes", []) + all_shapes
        layout_dict["annotations"] = layout_dict.get("annotations", []) + all_annotations

        # 基本 layout 设置（简化版，足够验证序列化）
        layout_dict.update(template="plotly_dark", height=800,
                           margin=dict(l=10, r=10, t=25, b=10),
                           hovermode="x unified")
        layout_dict.setdefault("xaxis", {}).update(rangeslider_visible=False)
        for k, v in _layout_updates.items():
            layout_dict.setdefault(k, {}).update(v)

        # 5. 一次性 Figure 构造
        fig = go.Figure(data=all_traces, layout=layout_dict)

        return fig, {
            "rows": rows,
            "n_traces": len(all_traces),
            "n_shapes": len(all_shapes),
            "n_annotations": len(all_annotations),
            "has_s": has_s,
            "has_strategy": has_strategy,
            "has_cross": has_cross,
            "has_alignment": has_alignment,
            "has_feedback": has_feedback,
        }

    # ----------------------------------------------------------------
    #  分支 1: has_s+strategy+cross+alignment (8行，最复杂)
    # ----------------------------------------------------------------
    def test_branch_8row_full(self):
        """8行布局：全部功能开启，验证序列化。"""
        fig, meta = self._build_e2e_figure(
            has_s=True, has_strategy=True, has_cross=True, has_alignment=True)
        assert meta["rows"] == 8, f"期望8行，得到{meta['rows']}"
        assert meta["n_traces"] > 0
        assert meta["n_shapes"] > 0
        assert meta["n_annotations"] > 0
        # 核心：序列化不报错
        js = fig.to_json()
        data = json.loads(js)
        assert len(data["data"]) == meta["n_traces"]
        assert len(data["layout"].get("shapes", [])) >= meta["n_shapes"]
        assert len(data["layout"].get("annotations", [])) >= meta["n_annotations"]

    # ----------------------------------------------------------------
    #  分支 2: has_s+strategy+cross (7行，无 alignment)
    # ----------------------------------------------------------------
    def test_branch_7row_no_alignment(self):
        """7行布局：无 alignment。"""
        fig, meta = self._build_e2e_figure(
            has_s=True, has_strategy=True, has_cross=True, has_alignment=False)
        assert meta["rows"] == 7
        assert meta["n_traces"] > 0
        fig.to_json()  # 不报错即通过

    # ----------------------------------------------------------------
    #  分支 3: has_s+strategy (6行，无 cross/alignment)
    # ----------------------------------------------------------------
    def test_branch_6row_no_cross(self):
        """6行布局：无 cross/alignment。"""
        fig, meta = self._build_e2e_figure(
            has_s=True, has_strategy=True, has_cross=False, has_alignment=False)
        assert meta["rows"] == 6
        # PnL subplot 仍产生 annotations（来自 trades 的 take_profit/stop_loss 标注）
        assert meta["n_annotations"] >= 1
        fig.to_json()

    # ----------------------------------------------------------------
    #  分支 4: has_s only (5行，无 strategy)
    # ----------------------------------------------------------------
    def test_branch_5row_no_strategy(self):
        """5行布局：仅施密特，无策略。"""
        fig, meta = self._build_e2e_figure(
            has_s=True, has_strategy=False, has_cross=False, has_alignment=False)
        assert meta["rows"] == 5
        fig.to_json()

    # ----------------------------------------------------------------
    #  分支 5: no_s (4行，无施密特)
    # ----------------------------------------------------------------
    def test_branch_4row_no_schmitt(self):
        """4行布局：无施密特。"""
        fig, meta = self._build_e2e_figure(
            has_s=False, has_strategy=False, has_cross=False, has_alignment=False)
        assert meta["rows"] == 4
        fig.to_json()

    # ----------------------------------------------------------------
    #  分支 1 + feedback_row (9行，最复杂变体)
    # ----------------------------------------------------------------
    def test_branch_9row_full_with_feedback(self):
        """9行布局：8行布局 + feedback 行。最复杂的可行布局。"""
        fig, meta = self._build_e2e_figure(
            has_s=True, has_strategy=True, has_cross=True, has_alignment=True,
            has_feedback=True)
        assert meta["rows"] == 9, f"期望9行（8+feedback），得到{meta['rows']}"
        assert meta["n_shapes"] > 0
        fig.to_json()  # 序列化不报错

    # ----------------------------------------------------------------
    #  序列化健壮性：np.ndarray/np.floating/NaN 不崩溃
    # ----------------------------------------------------------------
    def test_figure_to_json_with_ndarray_data(self):
        """_add_* 输出含 np 类型后 to_json() 仍工作。"""
        from browse.app import _add_main_price_traces
        n = 20
        t = np.arange(n, dtype=float)
        noisy = np.linspace(100, 110, n)
        traces = _add_main_price_traces(t, noisy, _ohlc(n), noisy + 0.5, None,
                                        {"fc": "#00d4aa", "_dual": False, "fc2": "#ff6b6b"}, mr=1)
        # 构造最小 layout
        layout = go.Layout(template="plotly_dark")
        fig = go.Figure(data=traces, layout=layout)
        js = fig.to_json()
        assert len(js) > 100
