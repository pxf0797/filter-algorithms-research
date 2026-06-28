"""Tests for streamlit_app.py pure functions.

These functions have no Streamlit UI dependency (no st.* calls), so they can
be tested with numpy arrays and regular dicts.

IMPORTANT: conftest.py mocks streamlit at import time.  This test file removes
the mock before importing streamlit_app, then restores it after (same pattern
as test_state.py).
"""
import sys
from typing import Any, Dict
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Remove the conftest mock so streamlit_app can import as a real module
# conftest already added streamlit/ dir to sys.path, so the namespace package
# resolves to our project dir (not site-packages).
# ---------------------------------------------------------------------------
_old_streamlit = sys.modules.pop("streamlit", None)

import streamlit_app  # noqa: E402

sys.modules["streamlit"] = _old_streamlit


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
        pos, labels = streamlit_app._date_markers([], "日线")
        assert pos == []
        assert labels == []

    def test_none_dates_returns_empty(self):
        pos, labels = streamlit_app._date_markers(None, "日线")
        assert pos == []
        assert labels == []

    def test_intraday_markers_day_boundary(self):
        dates = pd.date_range("2026-06-01 09:30", periods=48, freq="h", tz="Asia/Hong_Kong")
        pos, labels = streamlit_app._date_markers(dates, "60分钟")
        assert len(pos) > 0
        assert len(pos) == len(labels)
        for lbl in labels:
            assert len(lbl) == 5
            assert lbl[2] == "/"

    def test_daily_markers_monday_boundary(self):
        dates = pd.date_range("2026-01-01", periods=120, freq="D")
        pos, labels = streamlit_app._date_markers(dates, "日线")
        assert len(pos) > 0
        # weekday: 0=Monday
        first_monday_idx = np.where(dates.weekday == 0)[0][0]
        assert pos[0] == first_monday_idx

    def test_weekly_markers_month_boundary(self):
        dates = pd.date_range("2026-01-01", periods=120, freq="D")
        pos, labels = streamlit_app._date_markers(dates, "周线")
        assert len(pos) > 0
        for idx in pos:
            if idx > 0:
                prev_month = dates[idx - 1].month
                curr_month = dates[idx].month
                assert curr_month != prev_month

    def test_monthly_markers_january(self):
        dates = pd.date_range("2026-01-01", periods=365, freq="D")
        pos, labels = streamlit_app._date_markers(dates, "月线")
        assert len(pos) > 0
        for idx in pos:
            assert dates[idx].month == 1
        for lbl in labels:
            assert len(lbl) == 4

    def test_quarterly_same_as_monthly(self):
        dates = pd.date_range("2026-01-01", periods=365, freq="D")
        pos_q, labels_q = streamlit_app._date_markers(dates, "季线")
        pos_m, labels_m = streamlit_app._date_markers(dates, "月线")
        assert pos_q == pos_m
        assert labels_q == labels_m


# ====================================================================
# _compute_filters
# ====================================================================

class TestComputeFilters:
    def test_unknown_filter_id(self):
        noisy = np.arange(10, dtype=float)
        t = np.arange(10, dtype=float)
        cfg: Dict[str, Any] = {"_fid": "nonexistent", "pv": {}, "_dual": False}
        filtered, filtered2 = streamlit_app._compute_filters(noisy, t, cfg)
        assert np.all(np.isnan(filtered))
        assert filtered2 is None

    def test_sma_filter(self, simple_noisy):
        t = np.arange(6, dtype=float)
        cfg: Dict[str, Any] = {"_fid": "sma", "pv": {"window": 3}, "_dual": False}
        filtered, filtered2 = streamlit_app._compute_filters(simple_noisy, t, cfg)
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
        filtered, filtered2 = streamlit_app._compute_filters(simple_noisy, t, cfg)
        assert filtered2 is not None
        assert not np.all(np.isnan(filtered2))

    def test_filtered_is_float_raveled(self, simple_noisy):
        t = np.arange(6, dtype=float)
        cfg: Dict[str, Any] = {"_fid": "sma", "pv": {"window": 3}, "_dual": False}
        filtered, _ = streamlit_app._compute_filters(simple_noisy, t, cfg)
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
        result = streamlit_app._compute_schmitt_trigger(filtered, t, cfg)
        assert result is None

    def test_disabled_when_all_nan(self):
        t = np.arange(50, dtype=float)
        filtered = np.full(50, np.nan)
        cfg: Dict[str, Any] = {"show_sch": True}
        result = streamlit_app._compute_schmitt_trigger(filtered, t, cfg)
        assert result is None

    def test_returns_schmitt_dict(self, sine_signal):
        t, filtered = sine_signal
        cfg: Dict[str, Any] = {"show_sch": True, "ew": 60, "ke": 0.15, "sm": 0.05}
        result = streamlit_app._compute_schmitt_trigger(filtered, t, cfg)
        assert result is not None
        for key in ("mu_v", "sigma_v", "eps", "sig", "dur"):
            assert key in result
        assert len(result["sig"]) == 100

    def test_tight_params_more_nonzero(self, sine_signal):
        t, filtered = sine_signal
        tight_cfg = {"show_sch": True, "ew": 60, "ke": 0.01, "sm": 0.001}
        loose_cfg = {"show_sch": True, "ew": 60, "ke": 5.0, "sm": 5.0}
        tight_result = streamlit_app._compute_schmitt_trigger(filtered, t, tight_cfg)
        loose_result = streamlit_app._compute_schmitt_trigger(filtered, t, loose_cfg)
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
        result = streamlit_app._compute_prediction_pairs(
            t, filtered, {"sig": None}, cfg, [(0, 10)]
        )
        assert result == []

    def test_empty_when_schmitt_none(self):
        t = np.arange(50, dtype=float)
        filtered = np.sin(t / 5.0)
        cfg: Dict[str, Any] = {"show_pred": True}
        result = streamlit_app._compute_prediction_pairs(t, filtered, None, cfg, [(0, 10)])
        assert result == []

    def test_parabolic_fit_on_valid_pairs(self):
        np.random.seed(42)
        t = np.arange(100, dtype=float)
        filtered = np.sin(t / 5.0) + np.random.randn(100) * 0.1
        sig = np.array([1]*20 + [0]*10 + [-1]*20 + [0]*10 + [1]*20 + [0]*20, dtype=int)
        schmitt = {"sig": sig}
        cfg: Dict[str, Any] = {"show_pred": True, "fit_mode": "parabola"}
        all_pairs = [(0, 19), (30, 49)]
        result = streamlit_app._compute_prediction_pairs(t, filtered, schmitt, cfg, all_pairs)
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
        result = streamlit_app._compute_prediction_pairs(t, filtered, schmitt, cfg, all_pairs)
        assert result == []


# ====================================================================
# _determine_subplot_layout
# ====================================================================

class TestDetermineSubplotLayout:
    def test_minimal_no_features(self):
        rows, rh, titles, mr, rr, vr, sar, ssr, ar, pnl_row, cross_row, align_row = (
            streamlit_app._determine_subplot_layout(False, False, False, False, "周线")
        )
        assert rows == 4
        assert len(rh) == 4
        assert len(titles) == 4
        assert mr == 1 and rr == 2 and vr == 3 and ar == 4
        assert sar is None and ssr is None
        assert pnl_row is None and cross_row is None and align_row is None

    def test_with_schmitt_no_strategy(self):
        rows, rh, titles, mr, rr, vr, sar, ssr, ar, pnl_row, cross_row, align_row = (
            streamlit_app._determine_subplot_layout(True, False, False, False, "周线")
        )
        assert rows == 5
        assert mr == 1 and rr == 2 and vr == 3 and sar == 4 and ssr == 5
        assert ar is None

    def test_full_layout_all_features(self):
        rows, rh, titles, mr, rr, vr, sar, ssr, ar, pnl_row, cross_row, align_row = (
            streamlit_app._determine_subplot_layout(True, True, True, True, "周线")
        )
        assert rows == 8
        assert pnl_row == 6 and cross_row == 7 and align_row == 8
        assert "同向性判断" in titles

    def test_cross_pnl_no_alignment(self):
        rows, rh, titles, mr, rr, vr, sar, ssr, ar, pnl_row, cross_row, align_row = (
            streamlit_app._determine_subplot_layout(True, True, True, False, "周线")
        )
        assert rows == 7
        assert pnl_row == 6 and cross_row == 7
        assert align_row is None

    def test_strategy_no_cross_no_alignment(self):
        rows, rh, titles, mr, rr, vr, sar, ssr, ar, pnl_row, cross_row, align_row = (
            streamlit_app._determine_subplot_layout(True, True, False, False, "60分钟")
        )
        assert rows == 6
        assert pnl_row == 6
        assert cross_row is None and align_row is None
        # Strategy without cross_pnl: "PnL收益(%)" title but no higher_tf reference
        assert "PnL" in titles[-1]

    def test_no_schmitt_ar_is_fourth(self):
        rows, rh, titles, mr, rr, vr, sar, ssr, ar, pnl_row, cross_row, align_row = (
            streamlit_app._determine_subplot_layout(False, False, False, False, "日线")
        )
        assert ar == 4
        assert sar is None and ssr is None
