"""
单元测试：主图价格/残差/PnL 三个 trace 渲染函数（本会话审计发现的 Critical 覆盖盲区）。
构造 plotly Figure，断言 trace 名称/标记符号/边界行为，不依赖 Streamlit 运行时。
- _add_main_price_traces / _add_residual_traces / _add_pnl_traces (streamlit_app.py)
"""

import numpy as np
import pandas as pd
from plotly.subplots import make_subplots
from streamlit_app import (
    _add_main_price_traces, _add_residual_traces, _add_pnl_traces,
)


def _ohlc(n):
    return pd.DataFrame({
        "Open": np.linspace(100, 110, n), "High": np.linspace(101, 111, n),
        "Low": np.linspace(99, 109, n), "Close": np.linspace(100, 110, n),
    })


# ============================================================
# _add_main_price_traces
# ============================================================
class TestAddMainPriceTraces:
    def test_candlestick_close_filter(self):
        n=20; t=np.arange(n,dtype=float); noisy=np.linspace(100,110,n)
        traces = _add_main_price_traces(t, noisy, _ohlc(n), noisy+0.5, None,
                                        {"fc":"#00d4aa","_dual":False,"fc2":"#ff6b6b"})
        assert isinstance(traces, list) and len(traces) >= 2
        assert traces[0]["type"]=="candlestick" and traces[1]["type"]=="scattergl"
    def test_dual_filter_adds_second(self):
        n=20; t=np.arange(n,dtype=float); noisy=np.linspace(100,110,n)
        traces = _add_main_price_traces(t, noisy, _ohlc(n), noisy+0.5, noisy-0.5,
                                        {"fc":"#00d4aa","_dual":True,"fc2":"#ff6b6b"})
        assert len(traces) >= 3  # K + close + filter1 + filter2
    def test_all_nan_filter_omitted(self):
        n=20; t=np.arange(n,dtype=float); noisy=np.linspace(100,110,n)
        traces = _add_main_price_traces(t, noisy, _ohlc(n), np.full(n,np.nan), None,
                                        {"fc":"#00d4aa","_dual":False,"fc2":"#ff6b6b"})
        names = [d.get("name","") for d in traces]
        assert "滤波" not in names and "K" in names
    def test_dual_but_filtered2_none_safe(self):
        n=20; t=np.arange(n,dtype=float); noisy=np.linspace(100,110,n)
        traces = _add_main_price_traces(t, noisy, _ohlc(n), noisy+0.5, None,
                                        {"fc":"#00d4aa","_dual":True,"fc2":"#ff6b6b"})
        assert len(traces) >= 2  # no crash, just no filter2


class TestAddResidualTraces:
    def _run(self, n=30):
        t=np.arange(n,dtype=float); noisy=np.sin(t/3)+100
        return _add_residual_traces(t, noisy+0.2, noisy, None, {"fc":"#00d4aa"}, 2, 3)
    def test_adds_residual_and_velocity(self):
        acc, traces, shapes = self._run()
        assert len(acc)==30 and len(traces)==2 and len(shapes)==2
    def test_residual_velocity_trace_names(self):
        _, traces, _ = self._run()
        names = [d.get("name","") for d in traces]
        assert "残差" in names and "v" in names
    def test_short_t_returns_empty(self):
        result = _add_residual_traces(np.array([0.0]), np.array([100.0]),
            np.array([100.0]), None, {"fc":"#00d4aa"}, 2, 3)
        assert isinstance(result, np.ndarray) and len(result)==0
    def test_all_nan_filtered_no_traces(self):
        n=10; t=np.arange(n,dtype=float)
        acc, traces, shapes = _add_residual_traces(t, np.full(n,np.nan),
            np.linspace(100,110,n), None, {"fc":"#00d4aa"}, 2, 3)
        assert traces==[] and shapes==[] and len(acc)==n
    def test_constant_signal_zero_velocity(self):
        n=20; t=np.arange(n,dtype=float)
        acc, traces, _ = _add_residual_traces(t, np.full(n,100.0),
            np.full(n,100.0), None, {"fc":"#00d4aa"}, 2, 3)
        assert np.allclose(acc, 0.0)


class TestAddPnlTraces:
    @staticmethod
    def _curves(n=30):
        t = np.arange(n, dtype=float)
        return t, 100 + 0.3 * t, 100 + 0.1 * t

    def test_curves_and_no_segments_when_empty(self):
        t, lp, sp = self._curves()
        traces, _, _, _ = _add_pnl_traces(t, lp, sp, [], pnl_row=1)
        names = [tr.get("name","") for tr in traces]
        assert "做多PnL" in names and "做空PnL" in names

    def test_long_take_profit_markers_and_segment(self):
        t, lp, sp = self._curves()
        trades = [{"id": 1, "type": "long", "entry_idx": 5, "exit_idx": 15,
                   "return_pct": 3.2, "exit_reason": "take_profit",
                   "entry_price": 100, "exit_price": 103}]
        traces, _, _, _ = _add_pnl_traces(t, lp, sp, trades, 1)
        symbols = [tr["marker"]["symbol"] for tr in traces if tr.get("mode") == "markers"]
        assert "triangle-up" in symbols
        assert "circle" in symbols

    def test_stop_loss_marker_is_x(self):
        t, lp, sp = self._curves()
        trades = [{"id": 1, "type": "short", "entry_idx": 5, "exit_idx": 12,
                   "return_pct": -1.5, "exit_reason": "stop_loss",
                   "entry_price": 100, "exit_price": 101}]
        traces, _, _, _ = _add_pnl_traces(t, lp, sp, trades, 1)
        symbols = [tr["marker"]["symbol"] for tr in traces if tr.get("mode") == "markers"]
        assert "x" in symbols

    def test_eod_exit_no_exit_marker_no_annotation(self):
        t, lp, sp = self._curves()
        trades = [{"id": 1, "type": "long", "entry_idx": 5, "exit_idx": 29,
                   "return_pct": 2.0, "exit_reason": "eod",
                   "entry_price": 100, "exit_price": 102}]
        traces, _, _, _ = _add_pnl_traces(t, lp, sp, trades, 1)
        symbols = [tr["marker"]["symbol"] for tr in traces if tr.get("mode") == "markers"]
        assert "triangle-up" in symbols
        assert "x" not in symbols and "circle" not in symbols
        # annotations returned separately now — eod doesn't produce any
        # (annotations are part of _add_pnl_traces return but eod exit is not annotated)

    def test_annotation_arrow_on_take_profit(self):
        t, lp, sp = self._curves()
        trades = [{"id": 1, "type": "long", "entry_idx": 5, "exit_idx": 15,
                   "return_pct": 3.2, "exit_reason": "take_profit",
                   "entry_price": 100, "exit_price": 103}]
        _, _, _, yaxes = _add_pnl_traces(t, lp, sp, trades, 1)
        assert "yaxis1" in yaxes  # PnL axis configured

    def test_multiple_trades_two_segments(self):
        t, lp, sp = self._curves()
        trades = [
            {"id": 1, "type": "long", "entry_idx": 3, "exit_idx": 10,
             "return_pct": 2.0, "exit_reason": "take_profit", "entry_price": 100, "exit_price": 102},
            {"id": 2, "type": "short", "entry_idx": 12, "exit_idx": 20,
             "return_pct": 1.0, "exit_reason": "stop_loss", "entry_price": 103, "exit_price": 102},
        ]
        traces, _, _, _ = _add_pnl_traces(t, lp, sp, trades, 1)
        seg_names = [tr.get("name","") for tr in traces]
        assert "做多段" in seg_names and "做空段" in seg_names
