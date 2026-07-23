"""P0-3 验证测试: chart_builder 模块可独立导入"""
import importlib
import numpy as np


def test_chart_builder_importable():
    """chart_builder 模块可以独立导入，无循环依赖。"""
    from chart_builder import (
        _date_markers, _determine_subplot_layout, _insert_feedback_row,
        _add_main_price_traces, _add_residual_traces, _add_schmitt_traces,
        _add_pnl_traces, _add_feedback_subplot,
    )
    # All 8 functions should exist
    assert callable(_date_markers)
    assert callable(_determine_subplot_layout)
    assert callable(_insert_feedback_row)
    assert callable(_add_main_price_traces)
    assert callable(_add_residual_traces)
    assert callable(_add_schmitt_traces)
    assert callable(_add_pnl_traces)
    assert callable(_add_feedback_subplot)


def test_date_markers_daily():
    """_date_markers 日线按周一标记"""
    import pandas as pd
    from chart_builder import _date_markers
    dates = pd.date_range("2026-01-05", periods=30, freq="D")  # Jan 5 is Monday
    positions, labels = _date_markers(dates, "日线")
    assert len(positions) >= 3  # At least 3 Mondays in 30 days if starting Monday
    assert all(isinstance(p, int) for p in positions)


def test_date_markers_empty():
    """_date_markers 空输入返回空列表"""
    from chart_builder import _date_markers
    positions, labels = _date_markers(None, "日线")
    assert positions == []
    assert labels == []


def test_determine_subplot_layout_minimal():
    """_determine_subplot_layout 无施密特触发时的最小布局返回4行"""
    from chart_builder import _determine_subplot_layout
    rows, rh, titles, mr, rr, vr, sar, ssr, ar, pnl_row, cross_row, align_row = \
        _determine_subplot_layout(False, False, False, False, None)
    assert rows == 4
    assert sar is None  # no schmitt subplot
