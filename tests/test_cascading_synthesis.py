"""Tests for cascading bar synthesis — filter_app.services.data_loader"""

import pytest
import pandas as pd
import numpy as np

# conftest.py adds filter_app/ to sys.path, so imports are: services.data_loader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tz_aware_ts(s):
    """Parse to timezone-aware pd.Timestamp (HKT)."""
    return pd.Timestamp(s)


def _naive_ts(s):
    """Parse to tz-naive pd.Timestamp."""
    return pd.Timestamp(s).tz_localize(None)


# ---------------------------------------------------------------------------
# Test _ensure_tz_naive
# ---------------------------------------------------------------------------

class TestEnsureTzNaive:
    def test_tz_aware_to_naive(self):
        from services.data_loader import _ensure_tz_naive
        ts = pd.Timestamp("2026-07-03T14:47:00+08:00")
        result = _ensure_tz_naive(ts)
        assert result.tz is None
        assert result.hour == 14
        assert result.minute == 47

    def test_already_naive(self):
        from services.data_loader import _ensure_tz_naive
        ts = pd.Timestamp("2026-07-03T14:47:00")
        result = _ensure_tz_naive(ts)
        assert result.tz is None
        assert result == ts

    def test_string_input(self):
        from services.data_loader import _ensure_tz_naive
        result = _ensure_tz_naive("2026-07-03T14:47:00+08:00")
        # String has no tz attribute → returned as-is
        assert result == "2026-07-03T14:47:00+08:00"


# ---------------------------------------------------------------------------
# Test _get_tz_suffix
# ---------------------------------------------------------------------------

class TestGetTzSuffix:
    def test_positive_offset(self):
        from services.data_loader import _get_tz_suffix
        assert _get_tz_suffix("2026-07-03T14:45:00+08:00") == "+08:00"

    def test_utc_z(self):
        from services.data_loader import _get_tz_suffix
        assert _get_tz_suffix("2026-07-03T14:45:00Z") == "Z"

    def test_negative_offset(self):
        from services.data_loader import _get_tz_suffix
        assert _get_tz_suffix("2026-07-03T14:45:00-05:00") == "-05:00"

    def test_no_timezone(self):
        from services.data_loader import _get_tz_suffix
        assert _get_tz_suffix("2026-07-03T00:00:00") == ""

    def test_empty_string(self):
        from services.data_loader import _get_tz_suffix
        assert _get_tz_suffix("") == ""


# ---------------------------------------------------------------------------
# Test _format_synth_date
# ---------------------------------------------------------------------------

class TestFormatSynthDate:
    def test_minute_tf_keeps_tz(self):
        from services.data_loader import _format_synth_date
        cutoff = "2026-07-03T14:47:00+08:00"
        db_rows = [{"Date": "2026-07-03T10:00:00+08:00"}]
        result = _format_synth_date(cutoff, "60分钟", db_rows)
        assert result == "2026-07-03T14:47:00+08:00"

    def test_daily_tf_drops_tz(self):
        from services.data_loader import _format_synth_date
        cutoff = "2026-07-03T14:47:00+08:00"
        db_rows = [{"Date": "2026-07-02T00:00:00"}]
        result = _format_synth_date(cutoff, "日线", db_rows)
        assert result == "2026-07-03T14:47:00"
        assert "+" not in result

    def test_empty_db_rows(self):
        from services.data_loader import _format_synth_date
        cutoff = "2026-07-03T14:47:00+08:00"
        result = _format_synth_date(cutoff, "日线", [])
        assert result == "2026-07-03T14:47:00"
        assert "+" not in result


# ---------------------------------------------------------------------------
# Test _get_period_start_ts
# ---------------------------------------------------------------------------

class TestGetPeriodStartTs:
    def test_1min(self):
        from services.data_loader import _get_period_start_ts
        ts = _naive_ts("2026-07-03T14:30:00")
        result = _get_period_start_ts(ts, "1分钟")
        assert result == pd.Timestamp("2026-07-03T14:31:00")

    def test_5min(self):
        from services.data_loader import _get_period_start_ts
        ts = _naive_ts("2026-07-03T14:55:00")
        result = _get_period_start_ts(ts, "5分钟")
        assert result == pd.Timestamp("2026-07-03T14:56:00")

    def test_15min(self):
        from services.data_loader import _get_period_start_ts
        ts = _naive_ts("2026-07-03T14:15:00")
        result = _get_period_start_ts(ts, "15分钟")
        assert result == pd.Timestamp("2026-07-03T14:16:00")

    def test_60min(self):
        from services.data_loader import _get_period_start_ts
        ts = _naive_ts("2026-07-03T14:00:00")
        result = _get_period_start_ts(ts, "60分钟")
        assert result == pd.Timestamp("2026-07-03T14:01:00")

    def test_daily(self):
        from services.data_loader import _get_period_start_ts
        ts = _naive_ts("2026-07-02T00:00:00")
        result = _get_period_start_ts(ts, "日线")
        assert result == pd.Timestamp("2026-07-03T00:00:00")

    def test_weekly(self):
        from services.data_loader import _get_period_start_ts
        ts = _naive_ts("2026-07-03T00:00:00")  # Friday
        result = _get_period_start_ts(ts, "周线")
        # +1 day, normalized = Saturday
        assert result == pd.Timestamp("2026-07-04T00:00:00")

    def test_monthly(self):
        from services.data_loader import _get_period_start_ts
        ts = _naive_ts("2026-06-30T00:00:00")  # end of June
        result = _get_period_start_ts(ts, "月线")
        assert result == pd.Timestamp("2026-07-01T00:00:00")

    def test_quarterly(self):
        from services.data_loader import _get_period_start_ts
        ts = _naive_ts("2026-06-30T00:00:00")  # Q2 end
        result = _get_period_start_ts(ts, "季线")
        assert result == pd.Timestamp("2026-07-01T00:00:00")

    def test_monthly_cross_year(self):
        from services.data_loader import _get_period_start_ts
        ts = _naive_ts("2026-12-31T00:00:00")
        result = _get_period_start_ts(ts, "月线")
        assert result == pd.Timestamp("2027-01-01T00:00:00")


# ---------------------------------------------------------------------------
# Test _needs_synthesis
# ---------------------------------------------------------------------------

class TestNeedsSynthesis:
    def test_cutoff_after_last_ts__returns_true(self):
        """60min: last=14:00, cutoff=14:45 → 14:45 > 14:00 → True"""
        from services.data_loader import _needs_synthesis
        db_rows = [{"Date": "2026-07-03T14:00:00+08:00"}]
        assert _needs_synthesis("60分钟", db_rows, "2026-07-03T14:45:00+08:00") == True

    def test_cutoff_equal_last_ts__returns_false(self):
        """cutoff == last_ts → no new data → False"""
        from services.data_loader import _needs_synthesis
        db_rows = [{"Date": "2026-07-03T14:00:00+08:00"}]
        assert _needs_synthesis("60分钟", db_rows, "2026-07-03T14:00:00+08:00") == False

    def test_empty_rows__returns_false(self):
        from services.data_loader import _needs_synthesis
        assert _needs_synthesis("60分钟", [], "2026-07-03T14:45:00+08:00") == False

    def test_daily_same_day__cutoff_after_midnight__returns_true(self):
        """★ 关键回归: 日线 last=7/2 00:00, cutoff=7/2 15:45 → 15:45 > 00:00 → True"""
        from services.data_loader import _needs_synthesis
        db_rows = [{"Date": "2026-07-02T00:00:00"}]
        assert _needs_synthesis("日线", db_rows, "2026-07-02T15:45:00-04:00") == True

    def test_weekly__returns_true(self):
        """周线 last=6/29(Mon), cutoff=7/2(Thu) → True"""
        from services.data_loader import _needs_synthesis
        db_rows = [{"Date": "2026-06-29T00:00:00"}]
        assert _needs_synthesis("周线", db_rows, "2026-07-02T15:45:00-04:00") == True


# ---------------------------------------------------------------------------
# Test _get_query_start_for_synthesis
# ---------------------------------------------------------------------------

class TestGetQueryStartForSynthesis:
    def test_60min_returns_last_ts(self):
        from services.data_loader import _get_query_start_for_synthesis
        import pandas as pd
        last_ts = pd.Timestamp("2026-07-03T14:00:00")
        result = _get_query_start_for_synthesis(last_ts, "60分钟")
        assert result == last_ts

    def test_daily_returns_last_ts(self):
        """★ 关键回归: 日线返回last_ts而非次日"""
        from services.data_loader import _get_query_start_for_synthesis
        import pandas as pd
        last_ts = pd.Timestamp("2026-07-02T00:00:00")
        result = _get_query_start_for_synthesis(last_ts, "日线")
        assert result == last_ts  # 不是次日!

    def test_weekly_returns_last_ts(self):
        from services.data_loader import _get_query_start_for_synthesis
        import pandas as pd
        last_ts = pd.Timestamp("2026-06-29T00:00:00")
        result = _get_query_start_for_synthesis(last_ts, "周线")
        assert result == last_ts


# ---------------------------------------------------------------------------
# Test _offset_to_tz
# ---------------------------------------------------------------------------

class TestOffsetToTz:
    def test_positive_offset(self):
        from services.data_loader import _offset_to_tz
        from datetime import timezone, timedelta
        tz = _offset_to_tz("+08:00")
        assert tz.utcoffset(None) == timedelta(hours=8)

    def test_negative_offset(self):
        from services.data_loader import _offset_to_tz
        from datetime import timezone, timedelta
        tz = _offset_to_tz("-04:00")
        assert tz.utcoffset(None) == timedelta(hours=-4)

    def test_utc_z(self):
        from services.data_loader import _offset_to_tz
        from datetime import timezone, timedelta
        tz = _offset_to_tz("Z")
        assert tz.utcoffset(None) == timedelta(0)

    def test_empty_string(self):
        from services.data_loader import _offset_to_tz
        from datetime import timezone, timedelta
        tz = _offset_to_tz("")
        assert tz.utcoffset(None) == timedelta(0)


# ---------------------------------------------------------------------------
# Test _needs_synthesis regression (关键回归)
# ---------------------------------------------------------------------------

class TestNeedsSynthesisRegression:
    """回归测试: 确保所有TF的合成判定在典型场景下正确"""

    @pytest.mark.parametrize("tf,last_date,cutoff,expected", [
        # 分钟级: cutoff在同一周期内
        ("15分钟", "2026-07-03T14:30:00+08:00", "2026-07-03T14:45:00+08:00", True),
        ("60分钟", "2026-07-03T14:00:00+08:00", "2026-07-03T14:45:00+08:00", True),
        # 日线: cutoff在同一天 ← 之前为False的bug
        ("日线", "2026-07-02T00:00:00", "2026-07-02T15:45:00-04:00", True),
        # 周线
        ("周线", "2026-06-29T00:00:00", "2026-07-02T15:45:00-04:00", True),
        # 月线
        ("月线", "2026-07-01T00:00:00", "2026-07-02T15:45:00-04:00", True),
        # cutoff == last → 不需要合成
        ("60分钟", "2026-07-03T14:00:00+08:00", "2026-07-03T14:00:00+08:00", False),
        ("日线", "2026-07-02T00:00:00", "2026-07-02T00:00:00", False),
    ])
    def test_needs_synthesis(self, tf, last_date, cutoff, expected):
        from services.data_loader import _needs_synthesis
        db_rows = [{"Date": last_date}]
        assert _needs_synthesis(tf, db_rows, cutoff) == expected


# ---------------------------------------------------------------------------
# Test _aggregate_bars
# ---------------------------------------------------------------------------

class TestAggregateBars:
    def test_ohlcv_aggregation(self):
        from services.data_loader import _aggregate_bars
        bars = [
            {"Date": "2026-07-03T10:00:00", "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5, "Volume": 1000},
            {"Date": "2026-07-03T10:15:00", "Open": 100.5, "High": 102.0, "Low": 100.0, "Close": 101.5, "Volume": 2000},
            {"Date": "2026-07-03T10:30:00", "Open": 101.5, "High": 103.0, "Low": 100.5, "Close": 102.0, "Volume": 1500},
        ]
        result = _aggregate_bars(bars, "2026-07-03T10:30:00")
        assert result["Open"] == 100.0       # first bar's Open
        assert result["High"] == 103.0       # max High
        assert result["Low"] == 99.0         # min Low
        assert result["Close"] == 102.0      # last bar's Close
        assert result["Volume"] == 4500.0    # sum Volume

    def test_single_bar(self):
        from services.data_loader import _aggregate_bars
        bar = {"Date": "2026-07-03T10:00:00", "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5, "Volume": 1000}
        result = _aggregate_bars([bar], "2026-07-03T10:00:00")
        assert result["Open"] == 100.0
        assert result["High"] == 101.0
        assert result["Low"] == 99.0
        assert result["Close"] == 100.5
        assert result["Volume"] == 1000.0
        assert result["Date"] == "2026-07-03T10:00:00"


# ---------------------------------------------------------------------------
# Test _find_immediate_finer_tf
# ---------------------------------------------------------------------------

class TestFindImmediateFinerTf:
    def test_daily_finds_60min(self):
        from services.data_loader import _find_immediate_finer_tf
        tfs = ["15分钟", "60分钟", "日线", "周线"]
        assert _find_immediate_finer_tf("日线", tfs) == "60分钟"

    def test_weekly_finds_daily(self):
        from services.data_loader import _find_immediate_finer_tf
        tfs = ["15分钟", "60分钟", "日线", "周线"]
        assert _find_immediate_finer_tf("周线", tfs) == "日线"

    def test_finest_returns_none(self):
        from services.data_loader import _find_immediate_finer_tf
        tfs = ["15分钟", "60分钟", "日线"]
        assert _find_immediate_finer_tf("15分钟", tfs) is None

    def test_skip_missing_tf(self):
        from services.data_loader import _find_immediate_finer_tf
        tfs = ["60分钟", "日线"]  # no 周线
        assert _find_immediate_finer_tf("日线", tfs) == "60分钟"


# ---------------------------------------------------------------------------
# Test _build_output_df
# ---------------------------------------------------------------------------

class TestBuildOutputDf:
    def test_db_only(self):
        from services.data_loader import _build_output_df
        db_rows = [
            {"Date": "2026-07-01T00:00:00", "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5, "Volume": 1000},
            {"Date": "2026-07-02T00:00:00", "Open": 100.5, "High": 102.0, "Low": 100.0, "Close": 101.5, "Volume": 2000},
        ]
        df = _build_output_df(db_rows, None, 120)
        assert len(df) == 2
        assert df["Close"].iloc[-1] == 101.5
        assert "Date" in df.columns

    def test_with_synth_bar(self):
        from services.data_loader import _build_output_df
        db_rows = [
            {"Date": "2026-07-01T00:00:00", "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5, "Volume": 1000},
        ]
        synth = {"Date": "2026-07-03T14:47:00", "Open": 101.0, "High": 103.0, "Low": 100.5, "Close": 102.0, "Volume": 3000}
        df = _build_output_df(db_rows, synth, 120)
        assert len(df) == 2
        assert df["Close"].iloc[-1] == 102.0

    def test_truncation(self):
        from services.data_loader import _build_output_df
        db_rows = [{"Date": f"2026-07-{i:02d}T00:00:00", "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1000} for i in range(1, 11)]
        n_pts = 5
        df = _build_output_df(db_rows, None, n_pts)
        assert len(df) == n_pts
        # Should keep last n_pts entries
        assert df["Date"].iloc[0] == "2026-07-06T00:00:00"
        assert df["Date"].iloc[-1] == "2026-07-10T00:00:00"


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------

class TestModule:
    """Smoke tests ensuring cascading synthesis symbols can be imported."""

    def test_module_imports(self):
        from services import data_loader
        assert hasattr(data_loader, "_ensure_tz_naive")
        assert hasattr(data_loader, "_get_tz_suffix")
        assert hasattr(data_loader, "_format_synth_date")
        assert hasattr(data_loader, "_get_period_start_ts")
        assert hasattr(data_loader, "_get_query_start_for_synthesis")
        assert hasattr(data_loader, "_needs_synthesis")
        assert hasattr(data_loader, "_aggregate_bars")
        assert hasattr(data_loader, "_find_immediate_finer_tf")
        assert hasattr(data_loader, "_build_output_df")
        assert hasattr(data_loader, "_synthesize_incomplete_bar")
        assert hasattr(data_loader, "_sync_all_cascading")
