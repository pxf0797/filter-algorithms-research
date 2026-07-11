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
        n = 20
        t = np.arange(n, dtype=float)
        noisy = np.linspace(100, 110, n)
        cfg = {"fc": "#00d4aa", "_dual": False, "fc2": "#ff6b6b"}
        fig = make_subplots(rows=1, cols=1)
        _add_main_price_traces(fig, t, noisy, _ohlc(n), noisy + 0.5, None, cfg)
        names = [tr.name for tr in fig.data]
        assert "K" in names and "收盘" in names and "滤波" in names
        assert "滤波2" not in names

    def test_dual_filter_adds_second(self):
        n = 20
        t = np.arange(n, dtype=float)
        noisy = np.linspace(100, 110, n)
        cfg = {"fc": "#00d4aa", "_dual": True, "fc2": "#ff6b6b"}
        fig = make_subplots(rows=1, cols=1)
        _add_main_price_traces(fig, t, noisy, _ohlc(n), noisy + 0.5, noisy - 0.5, cfg)
        assert "滤波2" in [tr.name for tr in fig.data]

    def test_all_nan_filter_omitted(self):
        n = 20
        t = np.arange(n, dtype=float)
        noisy = np.linspace(100, 110, n)
        cfg = {"fc": "#00d4aa", "_dual": False, "fc2": "#ff6b6b"}
        fig = make_subplots(rows=1, cols=1)
        _add_main_price_traces(fig, t, noisy, _ohlc(n), np.full(n, np.nan), None, cfg)
        names = [tr.name for tr in fig.data]
        assert "滤波" not in names
        assert "K" in names and "收盘" in names

    def test_dual_but_filtered2_none_safe(self):
        n = 20
        t = np.arange(n, dtype=float)
        noisy = np.linspace(100, 110, n)
        cfg = {"fc": "#00d4aa", "_dual": True, "fc2": "#ff6b6b"}
        fig = make_subplots(rows=1, cols=1)
        _add_main_price_traces(fig, t, noisy, _ohlc(n), noisy + 0.5, None, cfg)  # 不崩
        assert "滤波2" not in [tr.name for tr in fig.data]


# ============================================================
# _add_residual_traces
# ============================================================
class TestAddResidualTraces:
    def test_adds_residual_and_velocity(self):
        n = 30
        t = np.arange(n, dtype=float)
        noisy = np.sin(t / 3) + 100
        acc = _add_residual_traces(
            make_subplots(rows=3, cols=1), t, noisy + 0.2, noisy, None,
            {"fc": "#00d4aa"}, rr=2, vr=3)
        assert len(acc) == n

    def test_residual_velocity_trace_names(self):
        n = 30
        t = np.arange(n, dtype=float)
        noisy = np.sin(t / 3) + 100
        fig = make_subplots(rows=3, cols=1)
        _add_residual_traces(fig, t, noisy + 0.2, noisy, None, {"fc": "#00d4aa"}, 2, 3)
        names = [tr.name for tr in fig.data]
        assert "残差" in names and "v" in names

    def test_short_t_returns_empty(self):
        fig = make_subplots(rows=3, cols=1)
        acc = _add_residual_traces(fig, np.array([0.0]), np.array([100.0]),
                                   np.array([100.0]), None, {"fc": "#00d4aa"}, 2, 3)
        assert len(acc) == 0
        assert len(fig.data) == 0

    def test_all_nan_filtered_no_traces(self):
        n = 10
        t = np.arange(n, dtype=float)
        fig = make_subplots(rows=3, cols=1)
        acc = _add_residual_traces(fig, t, np.full(n, np.nan), np.linspace(100, 110, n),
                                   None, {"fc": "#00d4aa"}, 2, 3)
        assert len(fig.data) == 0     # 无残差/速度 trace
        assert len(acc) == n          # 返回 gradient(zeros) 长度 n

    def test_constant_signal_zero_velocity(self):
        n = 20
        t = np.arange(n, dtype=float)
        acc = _add_residual_traces(make_subplots(rows=3, cols=1), t, np.full(n, 100.0),
                                   np.full(n, 100.0), None, {"fc": "#00d4aa"}, 2, 3)
        assert np.allclose(acc, 0.0)  # 常数 → 速度/加速度 ≈ 0


# ============================================================
# _add_pnl_traces
# ============================================================
class TestAddPnlTraces:
    @staticmethod
    def _curves(n=30):
        t = np.arange(n, dtype=float)
        return t, 100 + 0.3 * t, 100 + 0.1 * t

    def test_curves_and_no_segments_when_empty(self):
        t, lp, sp = self._curves()
        fig = make_subplots(rows=1, cols=1)
        _add_pnl_traces(fig, t, lp, sp, [], pnl_row=1)
        names = [tr.name for tr in fig.data]
        assert "做多PnL" in names and "做空PnL" in names
        assert not any(nm and "#" in nm for nm in names)  # 无逐笔段

    def test_long_take_profit_markers_and_segment(self):
        t, lp, sp = self._curves()
        trades = [{"id": 1, "type": "long", "entry_idx": 5, "exit_idx": 15,
                   "return_pct": 3.2, "exit_reason": "take_profit",
                   "entry_price": 100, "exit_price": 103}]
        fig = make_subplots(rows=1, cols=1)
        _add_pnl_traces(fig, t, lp, sp, trades, 1)
        symbols = [tr.marker.symbol for tr in fig.data if tr.mode == "markers"]
        assert "triangle-up" in symbols       # 入场
        assert "circle" in symbols            # 止盈离场
        assert any(tr.name == "多#1" for tr in fig.data)

    def test_stop_loss_marker_is_x(self):
        t, lp, sp = self._curves()
        trades = [{"id": 1, "type": "short", "entry_idx": 5, "exit_idx": 12,
                   "return_pct": -1.5, "exit_reason": "stop_loss",
                   "entry_price": 100, "exit_price": 101}]
        fig = make_subplots(rows=1, cols=1)
        _add_pnl_traces(fig, t, lp, sp, trades, 1)
        symbols = [tr.marker.symbol for tr in fig.data if tr.mode == "markers"]
        assert "x" in symbols                 # 止损用 x

    def test_eod_exit_no_exit_marker_no_annotation(self):
        t, lp, sp = self._curves()
        trades = [{"id": 1, "type": "long", "entry_idx": 5, "exit_idx": 29,
                   "return_pct": 2.0, "exit_reason": "eod",
                   "entry_price": 100, "exit_price": 102}]
        fig = make_subplots(rows=1, cols=1)
        _add_pnl_traces(fig, t, lp, sp, trades, 1)
        symbols = [tr.marker.symbol for tr in fig.data if tr.mode == "markers"]
        assert "triangle-up" in symbols       # 有入场
        assert "x" not in symbols and "circle" not in symbols  # eod 不画离场标记
        assert len(fig.layout.annotations) == 0                # 无收益标注

    def test_annotation_arrow_on_take_profit(self):
        t, lp, sp = self._curves()
        trades = [{"id": 1, "type": "long", "entry_idx": 5, "exit_idx": 15,
                   "return_pct": 3.2, "exit_reason": "take_profit",
                   "entry_price": 100, "exit_price": 103}]
        fig = make_subplots(rows=1, cols=1)
        _add_pnl_traces(fig, t, lp, sp, trades, 1)
        assert len(fig.layout.annotations) == 1
        assert "↑" in fig.layout.annotations[0].text   # 做多用 ↑

    def test_multiple_trades_two_segments(self):
        t, lp, sp = self._curves()
        trades = [
            {"id": 1, "type": "long", "entry_idx": 3, "exit_idx": 10,
             "return_pct": 2.0, "exit_reason": "take_profit", "entry_price": 100, "exit_price": 102},
            {"id": 2, "type": "short", "entry_idx": 12, "exit_idx": 20,
             "return_pct": 1.0, "exit_reason": "stop_loss", "entry_price": 103, "exit_price": 102},
        ]
        fig = make_subplots(rows=1, cols=1)
        _add_pnl_traces(fig, t, lp, sp, trades, 1)
        seg_names = [tr.name for tr in fig.data if tr.name and "#" in tr.name]
        assert "多#1" in seg_names and "空#2" in seg_names
