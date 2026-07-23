"""Tests for pure chart-building and data-processing functions.

These functions have no Streamlit UI dependency (no st.* calls), so they can
be tested with numpy arrays and regular dicts.

The functions tested were extracted from streamlit_app.py into:
- components.chart_builder: _date_markers, _determine_subplot_layout
- components.data_processing: _compute_filters, _compute_schmitt_trigger,
  _compute_prediction_pairs
"""
import sys
from typing import Any, Dict
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from components.chart_builder import _date_markers, _determine_subplot_layout
from components.data_processing import (
    _compute_filters, _compute_schmitt_trigger, _compute_prediction_pairs,
)


# ====================================================================
# Fixtures
# ====================================================================

@pytest.fixture
def simple_noisy() -> np.ndarray:
    return np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], dtype=float)


@pytest.fixture
def sine_signal() -> tuple:
    np.random.seed(42)
    t = np.arange(100, dtype=float)
    signal = np.sin(t / 5.0) + np.random.randn(100) * 0.1
    return t, signal


# ====================================================================
# _date_markers
# ====================================================================

class TestDateMarkers:
    def test_empty_dates_returns_empty(self):
        pos, labels = _date_markers([], "日线")
        assert pos == []
        assert labels == []

    def test_none_dates_returns_empty(self):
        pos, labels = _date_markers(None, "日线")
        assert pos == []
        assert labels == []

    def test_intraday_markers_day_boundary(self):
        dates = pd.date_range("2026-06-01 09:30", periods=48, freq="h", tz="Asia/Hong_Kong")
        pos, labels = _date_markers(dates, "60分钟")
        assert len(pos) > 0
        assert len(pos) == len(labels)
        for lbl in labels:
            assert len(lbl) == 5
            assert lbl[2] == "/"

    def test_daily_markers_monday_boundary(self):
        dates = pd.date_range("2026-01-01", periods=120, freq="D")
        pos, labels = _date_markers(dates, "日线")
        assert len(pos) > 0
        # weekday: 0=Monday
        first_monday_idx = np.where(dates.weekday == 0)[0][0]
        assert pos[0] == first_monday_idx

    def test_weekly_markers_month_boundary(self):
        dates = pd.date_range("2026-01-01", periods=120, freq="D")
        pos, labels = _date_markers(dates, "周线")
        assert len(pos) > 0
        for idx in pos:
            if idx > 0:
                prev_month = dates[idx - 1].month
                curr_month = dates[idx].month
                assert curr_month != prev_month

    def test_monthly_markers_january(self):
        dates = pd.date_range("2026-01-01", periods=365, freq="D")
        pos, labels = _date_markers(dates, "月线")
        assert len(pos) > 0
        for idx in pos:
            assert dates[idx].month == 1
        for lbl in labels:
            assert len(lbl) == 4

    def test_quarterly_same_as_monthly(self):
        dates = pd.date_range("2026-01-01", periods=365, freq="D")
        pos_q, labels_q = _date_markers(dates, "季线")
        pos_m, labels_m = _date_markers(dates, "月线")
        assert pos_q == pos_m
        assert labels_q == labels_m

    def test_labels_are_strings_not_datetime(self):
        """Regression: _date_markers labels must be strings, not Timestamp/number objects."""
        dates = pd.date_range("2026-04-28", periods=90, freq="B")[:60]
        for tf in ("60分钟", "日线", "周线", "月线", "季线"):
            _, labels = _date_markers(dates, tf)
            for lbl in labels:
                assert isinstance(lbl, str), (
                    f"tf={tf}: label {lbl!r} is {type(lbl).__name__}, expected str"
                )
                assert not isinstance(lbl, pd.Timestamp), (
                    f"tf={tf}: label is Timestamp, expected str"
                )

    def test_monthly_and_quarterly_empty_for_april_july_range(self):
        """月中/季线在4-7月范围内应返回空（无1月）。"""
        dates = pd.date_range("2026-04-28", periods=90, freq="B")[:60]
        pos_m, labels_m = _date_markers(dates, "月线")
        pos_q, labels_q = _date_markers(dates, "季线")
        assert pos_m == []
        assert labels_m == []
        assert pos_q == []
        assert labels_q == []


# ====================================================================
# _compute_filters
# ====================================================================

class TestComputeFilters:
    def test_unknown_filter_id(self):
        noisy = np.arange(10, dtype=float)
        t = np.arange(10, dtype=float)
        cfg: Dict[str, Any] = {"_fid": "nonexistent", "pv": {}, "_dual": False}
        filtered, filtered2 = _compute_filters(noisy, t, cfg)
        assert np.all(np.isnan(filtered))
        assert filtered2 is None

    def test_sma_filter(self, simple_noisy):
        t = np.arange(6, dtype=float)
        cfg: Dict[str, Any] = {"_fid": "sma", "pv": {"window": 3}, "_dual": False}
        filtered, filtered2 = _compute_filters(simple_noisy, t, cfg)
        assert filtered2 is None
        assert not np.all(np.isnan(filtered))
        assert filtered[0] == pytest.approx(1.0, abs=1e-10)
        assert filtered[1] == pytest.approx(2.0, abs=1e-10)

    def test_dual_filter(self, simple_noisy):
        t = np.arange(6, dtype=float)
        cfg: Dict[str, Any] = {
            "_fid": "sma", "pv": {"window": 3},
            "_dual": True, "_fid2": "ema", "pv2": {"span": 3},
        }
        filtered, filtered2 = _compute_filters(simple_noisy, t, cfg)
        assert filtered2 is not None
        assert not np.all(np.isnan(filtered2))

    def test_filtered_is_float_raveled(self, simple_noisy):
        t = np.arange(6, dtype=float)
        cfg: Dict[str, Any] = {"_fid": "sma", "pv": {"window": 3}, "_dual": False}
        filtered, _ = _compute_filters(simple_noisy, t, cfg)
        assert filtered.dtype == float
        assert filtered.ndim == 1


# ====================================================================
# _compute_schmitt_trigger
# ====================================================================

class TestComputeSchmittTrigger:
    def test_disabled_when_show_sch_false(self):
        t = np.arange(50, dtype=float)
        filtered = np.sin(t / 5.0)
        cfg: Dict[str, Any] = {"show_sch": False}
        result = _compute_schmitt_trigger(filtered, t, cfg)
        assert result is None

    def test_disabled_when_all_nan(self):
        t = np.arange(50, dtype=float)
        filtered = np.full(50, np.nan)
        cfg: Dict[str, Any] = {"show_sch": True}
        result = _compute_schmitt_trigger(filtered, t, cfg)
        assert result is None

    def test_returns_schmitt_dict(self, sine_signal):
        t, filtered = sine_signal
        cfg: Dict[str, Any] = {"show_sch": True, "ew": 60, "ke": 0.15, "sm": 0.05}
        result = _compute_schmitt_trigger(filtered, t, cfg)
        assert result is not None
        for key in ("mu_v", "sigma_v", "eps", "sig", "dur"):
            assert key in result
        assert len(result["sig"]) == 100

    def test_tight_params_more_nonzero(self, sine_signal):
        t, filtered = sine_signal
        tight_cfg = {"show_sch": True, "ew": 60, "ke": 0.01, "sm": 0.001}
        loose_cfg = {"show_sch": True, "ew": 60, "ke": 5.0, "sm": 5.0}
        tight_result = _compute_schmitt_trigger(filtered, t, tight_cfg)
        loose_result = _compute_schmitt_trigger(filtered, t, loose_cfg)
        assert tight_result is not None
        assert loose_result is not None
        tight_nonzero = np.sum(np.abs(tight_result["sig"]) > 0)
        loose_nonzero = np.sum(np.abs(loose_result["sig"]) > 0)
        assert tight_nonzero >= loose_nonzero


# ====================================================================
# _compute_prediction_pairs
# ====================================================================

class TestComputePredictionPairs:
    def test_empty_when_show_pred_false(self):
        t = np.arange(50, dtype=float)
        filtered = np.sin(t / 5.0)
        cfg: Dict[str, Any] = {"show_pred": False}
        result = _compute_prediction_pairs(
            t, filtered, {"sig": None}, cfg, [(0, 10)]
        )
        assert result == []

    def test_empty_when_schmitt_none(self):
        t = np.arange(50, dtype=float)
        filtered = np.sin(t / 5.0)
        cfg: Dict[str, Any] = {"show_pred": True}
        result = _compute_prediction_pairs(t, filtered, None, cfg, [(0, 10)])
        assert result == []

    def test_parabolic_fit_on_valid_pairs(self):
        np.random.seed(42)
        t = np.arange(100, dtype=float)
        filtered = np.sin(t / 5.0) + np.random.randn(100) * 0.1
        sig = np.array([1]*20 + [0]*10 + [-1]*20 + [0]*10 + [1]*20 + [0]*20, dtype=int)
        schmitt = {"sig": sig}
        cfg: Dict[str, Any] = {"show_pred": True, "fit_mode": "parabola"}
        all_pairs = [(0, 19), (30, 49)]
        result = _compute_prediction_pairs(t, filtered, schmitt, cfg, all_pairs)
        assert len(result) > 0
        for r in result:
            assert "fit_result" in r
            assert "fit_start" in r
            assert "pair_end" in r

    def test_pair_too_short_skipped(self):
        t = np.arange(50, dtype=float)
        filtered = np.sin(t / 5.0)
        schmitt = {"sig": np.array([1]*5 + [-1]*45, dtype=int)}
        cfg: Dict[str, Any] = {"show_pred": True, "fit_mode": "parabola"}
        all_pairs = [(0, 2)]
        result = _compute_prediction_pairs(t, filtered, schmitt, cfg, all_pairs)
        assert result == []


# ====================================================================
# _determine_subplot_layout
# ====================================================================

class TestDetermineSubplotLayout:
    def test_minimal_no_features(self):
        rows, rh, titles, mr, rr, vr, sar, ssr, ar, pnl_row, cross_row, align_row = (
            _determine_subplot_layout(False, False, False, False, "周线")
        )
        assert rows == 4
        assert len(rh) == 4
        assert len(titles) == 4
        assert mr == 1 and rr == 2 and vr == 3 and ar == 4
        assert sar is None and ssr is None
        assert pnl_row is None and cross_row is None and align_row is None

    def test_with_schmitt_no_strategy(self):
        rows, rh, titles, mr, rr, vr, sar, ssr, ar, pnl_row, cross_row, align_row = (
            _determine_subplot_layout(True, False, False, False, "周线")
        )
        assert rows == 5
        assert mr == 1 and rr == 2 and vr == 3 and sar == 4 and ssr == 5
        assert ar is None

    def test_full_layout_all_features(self):
        rows, rh, titles, mr, rr, vr, sar, ssr, ar, pnl_row, cross_row, align_row = (
            _determine_subplot_layout(True, True, True, True, "周线")
        )
        assert rows == 8
        assert pnl_row == 6 and cross_row == 7 and align_row == 8
        assert "同向性判断" in titles

    def test_cross_pnl_no_alignment(self):
        rows, rh, titles, mr, rr, vr, sar, ssr, ar, pnl_row, cross_row, align_row = (
            _determine_subplot_layout(True, True, True, False, "周线")
        )
        assert rows == 7
        assert pnl_row == 6 and cross_row == 7
        assert align_row is None

    def test_strategy_no_cross_no_alignment(self):
        rows, rh, titles, mr, rr, vr, sar, ssr, ar, pnl_row, cross_row, align_row = (
            _determine_subplot_layout(True, True, False, False, "60分钟")
        )
        assert rows == 6
        assert pnl_row == 6
        assert cross_row is None and align_row is None
        # Strategy without cross_pnl: "PnL收益(%)" title but no higher_tf reference
        assert "PnL" in titles[-1]

    def test_no_schmitt_ar_is_fourth(self):
        rows, rh, titles, mr, rr, vr, sar, ssr, ar, pnl_row, cross_row, align_row = (
            _determine_subplot_layout(False, False, False, False, "日线")
        )
        assert ar == 4
        assert sar is None and ssr is None


# ====================================================================
# X-axis tickmode regression tests
# ====================================================================

class TestTickmodeArray:
    """Verify `tickmode='array'` is set when `tickvals`/`ticktext` are used.

    Regression: after the chart_builder/streamlit_app split, the x-axis
    configuration set ``tickvals`` and ``ticktext`` but did not set
    ``tickmode='array'``.  In Plotly.js the default ``tickmode='auto'``
    ignores ``tickvals``/``ticktext`` and auto-generates numeric ticks,
    causing dates to display as bar indices instead of date strings.
    """

    def test_streamlit_app_source_has_tickmode_array_for_xaxis(self):
        """streamlit_app.py must include tickmode=\"array\" alongside tickvals."""
        from pathlib import Path
        import streamlit_app  # noqa: F401
        source = Path(streamlit_app.__file__).read_text()
        # Find the xaxis tick configuration block
        assert 'tickmode="array"' in source or "tickmode='array'" in source, (
            "streamlit_app.py must set tickmode='array' on the x-axis "
            "alongside tickvals/ticktext"
        )

    def test_streamlit_app_source_has_tickmode_array_for_sig_axis(self):
        """streamlit_app.py must include tickmode=\"array\" on Sig y-axis."""
        from pathlib import Path
        import streamlit_app  # noqa: F401
        source = Path(streamlit_app.__file__).read_text()
        assert 'tickmode="array"' in source or "tickmode='array'" in source, (
            "streamlit_app.py must set tickmode='array' on the Sig y-axis "
            "alongside tickvals/ticktext"
        )


# ====================================================================
# Import path regression tests (backtest_panel import fix)
# ====================================================================

class TestBacktestPanelImport:
    """Verify the import path fix for backtest_panel in streamlit_app.py."""

    def test_new_import_succeeds(self):
        """`from components.backtest_panel import ...` succeeds (new path)."""
        from components.backtest_panel import render_backtest_panel, run_backtest_play
        assert callable(render_backtest_panel)
        assert callable(run_backtest_play)

    def test_new_import_path_in_source(self):
        """streamlit_app.py uses the correct `from components.backtest_panel` import."""
        from pathlib import Path
        import streamlit_app  # noqa: E402 — needed for __file__
        source = Path(streamlit_app.__file__).read_text()
        assert "from components.backtest_panel import" in source, (
            "streamlit_app.py should import from components.backtest_panel"
        )
        assert "from filter_app.components.backtest_panel import" not in source, (
            "streamlit_app.py should NOT use old import path "
            "filter_app.components.backtest_panel"
        )
