"""
Smoke tests for holding-state bands & layout insertion.

- 共享绘制 _draw_holding_bands / _contiguous_runs 在 components.charts
- 当前周期"实际持仓状态" _add_feedback_subplot 与布局 _insert_feedback_row 在 streamlit_app

持仓状态直接来自实际成交区间(entry→exit)，无 PnL 反馈门控。
"""

import numpy as np
from plotly.subplots import make_subplots
from components.charts import _contiguous_runs, _draw_holding_bands
from streamlit_app import _insert_feedback_row, _add_feedback_subplot


def test_contiguous_runs():
    assert _contiguous_runs(np.array([1, 1, 0, 1, 1, 1, 0], dtype=bool)) == [(0, 1), (3, 5)]
    assert _contiguous_runs(np.array([0, 0], dtype=bool)) == []
    assert _contiguous_runs(np.array([1, 1], dtype=bool)) == [(0, 1)]


def test_draw_holding_bands_shapes():
    """给定多空掩码 → 各画一个色块 shape，不加 data trace。"""
    n = 30
    x = np.arange(n)
    long_mask = np.zeros(n, dtype=bool); long_mask[5:12] = True
    short_mask = np.zeros(n, dtype=bool); short_mask[18:25] = True
    fig = make_subplots(rows=2, cols=1)
    d0 = len(fig.data)
    _draw_holding_bands(x, long_mask, short_mask, row=2)
    assert len(fig.data) == d0                    # 只加 shapes
    assert len(fig.layout.shapes) >= 2            # 多空各一色块


def test_draw_holding_bands_empty():
    n = 10
    fig = make_subplots(rows=2, cols=1)
    _draw_holding_bands(np.arange(n), np.zeros(n, bool), np.zeros(n, bool), row=2)
    assert len(fig.layout.shapes) == 0


def test_insert_feedback_row_shifts_rows():
    rows = 7
    rh = [0.24, 0.11, 0.12, 0.12, 0.16, 0.27, 0.18]
    titles = ("a", "b", "c", "d", "e", "PnL", "cross")
    r2, rh2, t2, fb_row, cr2, ar2 = _insert_feedback_row(
        rows, rh, titles, pnl_row=6, cross_row=7, align_row=None)
    assert r2 == 8
    assert fb_row == 7                       # 紧跟 PnL(6) 之后
    assert cr2 == 8                          # cross 顺延
    assert ar2 is None
    assert len(rh2) == 8 and len(t2) == 8
    assert t2[6] == "实际持仓状态"             # 插在 PnL(索引5) 之后
    assert abs(sum(rh2) - sum(rh)) < 1e-9    # 总高不变


def test_add_feedback_subplot_state_only():
    n = 60
    x = np.arange(n)
    trade_records = [
        {"type": "long", "entry_idx": 3, "exit_idx": 20},
        {"type": "short", "entry_idx": 25, "exit_idx": 40},
        {"type": "long", "entry_idx": 45, "exit_idx": 58},
    ]
    fig = make_subplots(rows=2, cols=1)
    _add_feedback_subplot(fig, x, trade_records, row=2)
    assert len(fig.layout.shapes) >= 2       # 至少多空各一个持仓色块


def test_add_feedback_subplot_empty_trades():
    x = np.arange(10)
    fig = make_subplots(rows=2, cols=1)
    _add_feedback_subplot(fig, x, [], row=2)   # 无成交不报错，无色块
    assert len(fig.layout.shapes) == 0
