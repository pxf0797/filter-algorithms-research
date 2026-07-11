"""
Smoke tests for Layer 1 feedback subplot rendering & layout insertion
(_insert_feedback_row, _add_feedback_subplot, _contiguous_runs).
"""

import numpy as np
from plotly.subplots import make_subplots
from services.filter_engine import _compute_pnl_feedback_positions
from streamlit_app import (
    _insert_feedback_row, _add_feedback_subplot, _contiguous_runs,
)


def test_contiguous_runs():
    assert _contiguous_runs(np.array([1, 1, 0, 1, 1, 1, 0], dtype=bool)) == [(0, 1), (3, 5)]
    assert _contiguous_runs(np.array([0, 0], dtype=bool)) == []
    assert _contiguous_runs(np.array([1, 1], dtype=bool)) == [(0, 1)]


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
    assert t2[6] == "实际持仓过程(%)"          # 插在 PnL(索引5) 之后
    assert abs(sum(rh2) - sum(rh)) < 1e-9    # 总高不变


def test_add_feedback_subplot_no_error():
    n = 60
    x = np.arange(n)
    long_pnl = 100 + np.concatenate([
        np.linspace(0, 18, 30), np.linspace(18, 4, 15), np.linspace(4, 15, 15)])
    short_pnl = 100 + np.linspace(0, 6, n)
    fb = _compute_pnl_feedback_positions(long_pnl, short_pnl,
                                         dd_clear=0.08, dd_recover=0.03)
    assert len(fb["long_clears"]) > 0        # 合成数据确实触发清仓
    fig = make_subplots(rows=2, cols=1)
    before = len(fig.data)
    _add_feedback_subplot(fig, x, fb, long_pnl, short_pnl, row=2)
    assert len(fig.data) > before            # 加了曲线/标记 trace
    assert len(fig.layout.shapes) >= 1       # 底部状态带 shape
