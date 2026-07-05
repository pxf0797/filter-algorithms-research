"""Tests for intraday bar synthesis (1min/5min/15min/60min)."""
import sys
import os
import pytest
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'filter_app'))

from services.data_loader import _get_period_end, _get_period_start, ALL_TFS


class TestPeriodEndMinute:
    """Test _get_period_end for minute-level TFs."""

    def test_1min(self):
        ts = pd.Timestamp("2026-04-23 10:30:00")
        result = _get_period_end(ts, "1分钟")
        assert result == pd.Timestamp("2026-04-23 10:31:00")

    def test_5min_boundary(self):
        ts = pd.Timestamp("2026-04-23 10:30:00")
        result = _get_period_end(ts, "5分钟")
        assert result == pd.Timestamp("2026-04-23 10:35:00")

    def test_5min_cross_hour(self):
        ts = pd.Timestamp("2026-04-23 10:55:00")
        result = _get_period_end(ts, "5分钟")
        assert result == pd.Timestamp("2026-04-23 11:00:00")

    def test_15min(self):
        ts = pd.Timestamp("2026-04-23 10:30:00")
        result = _get_period_end(ts, "15分钟")
        assert result == pd.Timestamp("2026-04-23 10:45:00")

    def test_15min_cross_hour(self):
        ts = pd.Timestamp("2026-04-23 10:50:00")
        result = _get_period_end(ts, "15分钟")
        assert result == pd.Timestamp("2026-04-23 11:00:00")

    def test_60min(self):
        ts = pd.Timestamp("2026-04-23 10:00:00")
        result = _get_period_end(ts, "60分钟")
        assert result == pd.Timestamp("2026-04-23 11:00:00")


class TestPeriodStartMinute:
    """Test _get_period_start for minute-level TFs."""

    def test_5min(self):
        ts = pd.Timestamp("2026-04-23 10:30:00")
        result = _get_period_start(ts, "5分钟")
        assert result == "2026-04-23"  # date-only string

    def test_60min(self):
        ts = pd.Timestamp("2026-04-23 10:00:00")
        result = _get_period_start(ts, "60分钟")
        assert result == "2026-04-23"


class TestSynthesisCondition:
    """Test synthesis condition for minute-level TFs."""

    def test_15min_from_5min_should_synthesize(self):
        """min_tf=5分钟 → ALL_TFS.index(15分钟)=2 > ALL_TFS.index(5分钟)=1 → True"""
        assert ALL_TFS.index("15分钟") > ALL_TFS.index("5分钟")

    def test_60min_from_15min_should_synthesize(self):
        """min_tf=15分钟 → ALL_TFS.index(60分钟)=3 > ALL_TFS.index(15分钟)=2 → True"""
        assert ALL_TFS.index("60分钟") > ALL_TFS.index("15分钟")

    def test_5min_from_5min_should_not_synthesize(self):
        """min_tf=5分钟 → ALL_TFS.index(5分钟)=1 > ALL_TFS.index(5分钟)=1 → False"""
        assert not (ALL_TFS.index("5分钟") > ALL_TFS.index("5分钟"))

    def test_60min_from_daily_should_not_synthesize(self):
        """min_tf=日线(4) → 60分钟(3) < 4 → False (不在范围内)"""
        assert not (ALL_TFS.index("60分钟") > ALL_TFS.index("日线"))

    def test_finer_tf_calculation(self):
        """finer_tf = ALL_TFS[ALL_TFS.index(tf) - 1]"""
        assert ALL_TFS[ALL_TFS.index("15分钟") - 1] == "5分钟"
        assert ALL_TFS[ALL_TFS.index("60分钟") - 1] == "15分钟"
        assert ALL_TFS[ALL_TFS.index("5分钟") - 1] == "1分钟"
