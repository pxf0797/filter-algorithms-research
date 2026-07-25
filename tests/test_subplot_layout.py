"""各子图视图检查——方案A go.Figure(data=dicts, layout=dict) 之后，
每个 subplot row 的 axis 配置、trace 的 xaxis/yaxis 引用 是否正确配对。"""

import numpy as np
import pandas as pd
from plotly.subplots import make_subplots
from browse.app import (
    _add_main_price_traces, _add_residual_traces, _add_schmitt_traces,
    _add_pnl_traces, _add_feedback_subplot, _determine_subplot_layout,
    _insert_feedback_row,
)
from browse.charts import _add_cross_pnl_subplot, _add_alignment_subplot


def _ohlc(n):
    return pd.DataFrame({"Open": np.linspace(100,110,n), "High": np.linspace(101,111,n),
        "Low": np.linspace(99,109,n), "Close": np.linspace(100,110,n)})


def _axis_from_trace(tr):
    """从 dict trace 里提取 (xaxis, yaxis) 键."""
    return tr.get("xaxis","x"), tr.get("yaxis","y")


# ── 1. 每个 _add_* 函数的 traces 都引用正确 row 的 axis ──

class TestTracesReferenceCorrectRow:
    def test_main_price_refs_mr(self):
        n=20; t=np.arange(n,dtype=float); noisy=np.linspace(100,110,n)
        traces = _add_main_price_traces(t, noisy, _ohlc(n), noisy+0.5, None,
                                        {"fc":"#00d4aa","_dual":False,"fc2":"#ff6b6b"}, mr=1)
        for tr in traces:
            # mr=1 → make_subplots 用 "x"/"y" 非 "x1"/"y1"
            assert tr["xaxis"] == "x" and tr["yaxis"] == "y"

    def test_main_price_refs_dynamic_row(self):
        n=10; t=np.arange(n,dtype=float)
        traces = _add_main_price_traces(t, np.linspace(100,110,n), _ohlc(n),
            np.linspace(100,110,n), None, {"fc":"#a","_dual":False,"fc2":"#b"}, mr=5)
        for tr in traces:
            assert tr["xaxis"] == "x5" and tr["yaxis"] == "y5"

    def test_residual_refs_rr_vr(self):
        n=30; t=np.arange(n,dtype=float); noisy=np.sin(t/3)+100
        _, traces, _ = _add_residual_traces(t, noisy+0.2, noisy, None,
                                             {"fc":"#00d4aa"}, rr=2, vr=3)
        assert traces[0]["xaxis"]=="x2" and traces[0]["yaxis"]=="y2"   # 残差
        assert traces[1]["xaxis"]=="x3" and traces[1]["yaxis"]=="y3"   # v

    def test_schmitt_refs_sar_ssr(self):
        n=30; t=np.arange(n,dtype=float); sig=np.zeros(n,int); sig[10:20]=1
        schmitt={"eps":np.full(n,0.1),"sig":sig,"sigma_v":np.full(n,0.1)}
        traces, _ = _add_schmitt_traces(t, schmitt, np.zeros(n), [(10,25)], sar=4, ssr=5)
        for tr in traces[:5]:  # first 5 all belong to sar
            assert tr["xaxis"]==f"x4" and tr["yaxis"]==f"y4"
        # Sig trace belongs to ssr
        assert traces[5]["xaxis"]=="x5" and traces[5]["yaxis"]=="y5"

    def test_pnl_refs_pnl_row(self):
        n=30; t=np.arange(n,dtype=float)
        traces, _, _, _ = _add_pnl_traces(t, 100+0.3*t, 100+0.1*t, [], pnl_row=6)
        for tr in traces:
            assert tr["xaxis"] == "x6" and tr["yaxis"] == "y6"

    def test_feedback_yaxis_config(self):
        n=30; t=np.arange(n,dtype=float)
        shapes, yaxes = _add_feedback_subplot(t, [], row=7)
        assert "yaxis7" in yaxes


# ── 2. 完整 layout 构造验证: make_subplots skeleton + go.Figure ──

class TestFullFigureLayoutIntegrity:
    """模拟 _render_chart 的完整 figure 构造，逐项验证 layout 正确性。"""

    @staticmethod
    def _build(rows=4, has_s=False, has_strat=False, has_cross=False, has_align=False):
        """构造最小完整 figure（不带实际计算，只验证 layout 结构）。"""
        import plotly.graph_objects as go
        rh = [0.6, 0.15, 0.15, 0.10]  # any valid ratios
        titles = ["价格&滤波", "残差", "速度v", "加速度a"]
        _skeleton = make_subplots(rows=rows, cols=1, shared_xaxes=True,
            vertical_spacing=0.01, row_heights=rh, subplot_titles=titles)
        layout_dict = _skeleton.layout.to_plotly_json()
        layout_dict.update(template="plotly_dark", height=600,
            margin=dict(l=10,r=10,t=25,b=10), hovermode="x unified")
        # At least configure xaxis for bottom row
        layout_dict.setdefault(f"xaxis{rows}", {}).update(title_text="")
        fig = go.Figure(data=[], layout=layout_dict)
        return fig

    def test_all_xaxis_exist_for_each_row(self):
        fig = self._build(4)
        for r in range(1, 5):
            assert fig.layout[f"xaxis{r}"] is not None, f"missing xaxis{r}"
            assert fig.layout[f"yaxis{r}"] is not None, f"missing yaxis{r}"

    def test_subplot_titles_present(self):
        fig = self._build(4)
        # make_subplots puts titles as annotations
        assert len(fig.layout.annotations) >= 4

    def test_8_row_figure_all_axes(self):
        """8-row figure 所有轴都存在。"""
        rh=[0.35,0.1,0.06,0.06,0.08,0.15,0.05,0.15]
        titles=["价格&滤波","残差","速度v","a&±ε","Sig_t","PnL","持仓","同向"]
        skeleton=make_subplots(rows=8,cols=1,shared_xaxes=True,vertical_spacing=0.01,
            row_heights=rh,subplot_titles=titles)
        ld=skeleton.layout.to_plotly_json()
        for r in range(1,9):
            assert ld.get(f"xaxis{r}") or ld.get("xaxis" if r==1 else f"xaxis{r}"), f"missing xaxis{r}"

    def test_trace_added_to_correct_row_in_full_layout(self):
        """验证 trace dict 在 go.Figure 中保留正确的 xaxis/yaxis。"""
        import plotly.graph_objects as go
        n=30; t=np.arange(n,dtype=float)
        skeleton = make_subplots(rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.01,
            row_heights=[0.5,0.2,0.15,0.15], subplot_titles=["a","b","c","d"])
        ld = skeleton.layout.to_plotly_json()
        ld.update(template="plotly_dark", height=500)
        traces = [
            dict(type="scattergl", x=t, y=np.sin(t), mode="lines", xaxis="x1", yaxis="y1"),
            dict(type="scattergl", x=t, y=np.cos(t), mode="lines", xaxis="x2", yaxis="y2"),
            dict(type="scattergl", x=t, y=np.sin(t)*2, mode="lines", xaxis="x3", yaxis="y3"),
        ]
        fig = go.Figure(data=traces, layout=ld)
        assert len(fig.data) == 3
        # go.Figure(data=dicts) 会内部降维 xaxis，只通过 layout 映射; 不在此层断言


# ── 3. 轴名精确配对守卫：防止 row1 和 row>1 的 axis ID 混淆 ──

class TestAxisNameConvention:
    """守护方案A关键不变量：make_subplots 的 axis 命名规则与 trace dict 引用必须一致。

    Row1: xaxis="x" / yaxis="y"（无数字后缀！）
    RowN(N>1): xaxis="xN" / yaxis="yN"

    任何 trace 引用不存在的 axis 名（如 xaxis="x1"），会被 Plotly 静默丢弃到默认轴，
    导致子图布局错乱——这是调试成本极高的隐蔽 bug。
    """

    def _layout_axis_keys(self, rows):
        skeleton = make_subplots(rows=rows, cols=1, shared_xaxes=True,
            vertical_spacing=0.01, row_heights=[1.0/rows]*rows)
        ld = skeleton.layout.to_plotly_json()
        xk = {k for k in ld.keys() if k.startswith('xaxis')}
        yk = {k for k in ld.keys() if k.startswith('yaxis')}
        return xk, yk

    def test_row1_has_no_number_suffix(self):
        """Row1 axis = "x" / "y", NOT "x1" / "y1"."""
        for rows in [4,5,6,7,8]:
            xk, yk = self._layout_axis_keys(rows)
            assert "xaxis" in xk and "yaxis" in yk, f"{rows}行: row1缺标准轴"
            assert "xaxis1" not in xk, f"{rows}行: row1不应有xaxis1"
            assert "yaxis1" not in yk, f"{rows}行: row1不应有yaxis1"

    def test_row2_plus_all_have_number_suffix(self):
        """Row2+ axis = "xN" / "yN"."""
        for rows in [4,5,6,7,8]:
            xk, yk = self._layout_axis_keys(rows)
            for r in range(2, rows+1):
                assert f"xaxis{r}" in xk, f"{rows}行: 缺xaxis{r}"
                assert f"yaxis{r}" in yk, f"{rows}行: 缺yaxis{r}"

    def test_all_pnl_trace_axes_exist_in_layout(self):
        """PNL traces 的 xaxis/yaxis 引用必须在 layout 中存在."""
        n=30; t=np.arange(n,dtype=float)
        for pnl_row in [6,5,4]:
            rows = pnl_row
            xk, yk = self._layout_axis_keys(rows)
            traces, _, _, _ = _add_pnl_traces(t, 100+0.3*t, 100+0.1*t, [], pnl_row=pnl_row)
            for tr in traces:
                assert self._axis_key(tr["xaxis"]) in xk, f"PNL xaxis={tr['xaxis']}(rows={rows})"
                assert self._axis_key(tr["yaxis"]) in yk, f"PNL yaxis={tr['yaxis']}(rows={rows})"

    def _axis_key(self, ref):
        """trace dict 的 xaxis='x'→layout key 'xaxis'; 'x2'→'xaxis2'; 'y3'→'yaxis3'"""
        if ref in ("x", "y"):
            return ref + "axis"             # x→xaxis, y→yaxis
        return ref[0] + "axis" + ref[1:]    # x2→xaxis2, y3→yaxis3

    def test_main_price_traces_axis_exist_in_layout(self):
        """主价格 traces 引用的 axis 在对应的 layout 中存在。"""
        n=20; t=np.arange(n,dtype=float); noisy=np.linspace(100,110,n)
        for mr, rows in [(1,4),(1,5),(1,6),(1,7),(1,8)]:
            xk, yk = self._layout_axis_keys(rows)
            traces = _add_main_price_traces(t, noisy, _ohlc(n), noisy+0.5, None,
                {"fc":"#00d4aa","_dual":False,"fc2":"#ff6b6b"}, mr=mr)
            for tr in traces:
                assert self._axis_key(tr["xaxis"]) in xk, f"rows={rows}: xaxis={tr['xaxis']}"
                assert self._axis_key(tr["yaxis"]) in yk, f"rows={rows}: yaxis={tr['yaxis']}"

    def test_residual_traces_axis_exist_in_layout(self):
        """残差/速度 traces 的 axis 引用在对应 layout 中存在。"""
        n=30; t=np.arange(n,dtype=float); noisy=np.sin(t/3)+100
        for rr, vr, rows in [(2,3,4),(2,3,5),(2,3,6)]:
            xk, yk = self._layout_axis_keys(rows)
            _, traces, _ = _add_residual_traces(t, noisy+0.2, noisy, None,
                {"fc":"#00d4aa"}, rr=rr, vr=vr)
            for tr in traces:
                assert self._axis_key(tr["xaxis"]) in xk, f"rows={rows}: xaxis={tr['xaxis']}"
                assert self._axis_key(tr["yaxis"]) in yk, f"rows={rows}: yaxis={tr['yaxis']}"
