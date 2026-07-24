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
        from data.loader import _ensure_tz_naive
        ts = pd.Timestamp("2026-07-03T14:47:00+08:00")
        result = _ensure_tz_naive(ts)
        assert result.tz is None
        assert result.hour == 14
        assert result.minute == 47

    def test_already_naive(self):
        from data.loader import _ensure_tz_naive
        ts = pd.Timestamp("2026-07-03T14:47:00")
        result = _ensure_tz_naive(ts)
        assert result.tz is None
        assert result == ts

    def test_string_input(self):
        from data.loader import _ensure_tz_naive
        result = _ensure_tz_naive("2026-07-03T14:47:00+08:00")
        # String has no tz attribute → returned as-is
        assert result == "2026-07-03T14:47:00+08:00"


# ---------------------------------------------------------------------------
# Test _get_tz_suffix
# ---------------------------------------------------------------------------

class TestGetTzSuffix:
    def test_positive_offset(self):
        from data.loader import _get_tz_suffix
        assert _get_tz_suffix("2026-07-03T14:45:00+08:00") == "+08:00"

    def test_utc_z(self):
        from data.loader import _get_tz_suffix
        assert _get_tz_suffix("2026-07-03T14:45:00Z") == "Z"

    def test_negative_offset(self):
        from data.loader import _get_tz_suffix
        assert _get_tz_suffix("2026-07-03T14:45:00-05:00") == "-05:00"

    def test_no_timezone(self):
        from data.loader import _get_tz_suffix
        assert _get_tz_suffix("2026-07-03T00:00:00") == ""

    def test_empty_string(self):
        from data.loader import _get_tz_suffix
        assert _get_tz_suffix("") == ""


# ---------------------------------------------------------------------------
# Test _format_synth_date
# ---------------------------------------------------------------------------

class TestFormatSynthDate:
    def test_minute_tf_keeps_tz(self):
        from data.loader import _format_synth_date
        cutoff = "2026-07-03T14:47:00+08:00"
        db_rows = [{"Date": "2026-07-03T10:00:00+08:00"}]
        result = _format_synth_date(cutoff, "60分钟", db_rows)
        assert result == "2026-07-03T14:47:00+08:00"

    def test_daily_tf_drops_tz(self):
        from data.loader import _format_synth_date
        cutoff = "2026-07-03T14:47:00+08:00"
        db_rows = [{"Date": "2026-07-02T00:00:00"}]
        result = _format_synth_date(cutoff, "日线", db_rows)
        assert result == "2026-07-03T14:47:00"
        assert "+" not in result

    def test_empty_db_rows(self):
        from data.loader import _format_synth_date
        cutoff = "2026-07-03T14:47:00+08:00"
        result = _format_synth_date(cutoff, "日线", [])
        assert result == "2026-07-03T14:47:00"
        assert "+" not in result


# ---------------------------------------------------------------------------
# Test _get_period_start_ts
# ---------------------------------------------------------------------------

class TestGetPeriodStartTs:
    def test_1min(self):
        from data.loader import _get_period_start_ts
        ts = _naive_ts("2026-07-03T14:30:00")
        result = _get_period_start_ts(ts, "1分钟")
        assert result == pd.Timestamp("2026-07-03T14:31:00")

    def test_5min(self):
        from data.loader import _get_period_start_ts
        ts = _naive_ts("2026-07-03T14:55:00")
        result = _get_period_start_ts(ts, "5分钟")
        assert result == pd.Timestamp("2026-07-03T14:56:00")

    def test_15min(self):
        from data.loader import _get_period_start_ts
        ts = _naive_ts("2026-07-03T14:15:00")
        result = _get_period_start_ts(ts, "15分钟")
        assert result == pd.Timestamp("2026-07-03T14:16:00")

    def test_60min(self):
        from data.loader import _get_period_start_ts
        ts = _naive_ts("2026-07-03T14:00:00")
        result = _get_period_start_ts(ts, "60分钟")
        assert result == pd.Timestamp("2026-07-03T14:01:00")

    def test_daily(self):
        from data.loader import _get_period_start_ts
        ts = _naive_ts("2026-07-02T00:00:00")
        result = _get_period_start_ts(ts, "日线")
        assert result == pd.Timestamp("2026-07-03T00:00:00")

    def test_weekly(self):
        from data.loader import _get_period_start_ts
        ts = _naive_ts("2026-07-03T00:00:00")  # Friday
        result = _get_period_start_ts(ts, "周线")
        # +1 day, normalized = Saturday
        assert result == pd.Timestamp("2026-07-04T00:00:00")

    def test_monthly(self):
        from data.loader import _get_period_start_ts
        ts = _naive_ts("2026-06-30T00:00:00")  # end of June
        result = _get_period_start_ts(ts, "月线")
        assert result == pd.Timestamp("2026-07-01T00:00:00")

    def test_quarterly(self):
        from data.loader import _get_period_start_ts
        ts = _naive_ts("2026-06-30T00:00:00")  # Q2 end
        result = _get_period_start_ts(ts, "季线")
        assert result == pd.Timestamp("2026-07-01T00:00:00")

    def test_monthly_cross_year(self):
        from data.loader import _get_period_start_ts
        ts = _naive_ts("2026-12-31T00:00:00")
        result = _get_period_start_ts(ts, "月线")
        assert result == pd.Timestamp("2027-01-01T00:00:00")


# ---------------------------------------------------------------------------
# Test _needs_synthesis
# ---------------------------------------------------------------------------

class TestNeedsSynthesis:
    def test_cutoff_after_last_ts__returns_true(self):
        """60min: last=14:00, cutoff=14:45 → 14:45 > 14:00 → True"""
        from data.loader import _needs_synthesis
        db_rows = [{"Date": "2026-07-03T14:00:00+08:00"}]
        assert _needs_synthesis("60分钟", db_rows, "2026-07-03T14:45:00+08:00") == True

    def test_cutoff_equal_last_ts__returns_true(self):
        """cutoff == last_ts → 边界场景, DB bar是'未来数据'需替换 → True"""
        from data.loader import _needs_synthesis
        db_rows = [{"Date": "2026-07-03T14:00:00+08:00"}]
        assert _needs_synthesis("60分钟", db_rows, "2026-07-03T14:00:00+08:00") == True

    def test_empty_rows__returns_false(self):
        from data.loader import _needs_synthesis
        assert _needs_synthesis("60分钟", [], "2026-07-03T14:45:00+08:00") == False

    def test_daily_same_day__cutoff_after_midnight__returns_true(self):
        """★ 关键回归: 日线 last=7/2 00:00, cutoff=7/2 15:45 → 15:45 > 00:00 → True"""
        from data.loader import _needs_synthesis
        db_rows = [{"Date": "2026-07-02T00:00:00"}]
        assert _needs_synthesis("日线", db_rows, "2026-07-02T15:45:00-04:00") == True

    def test_weekly__returns_true(self):
        """周线 last=6/29(Mon), cutoff=7/2(Thu) → True"""
        from data.loader import _needs_synthesis
        db_rows = [{"Date": "2026-06-29T00:00:00"}]
        assert _needs_synthesis("周线", db_rows, "2026-07-02T15:45:00-04:00") == True


# ---------------------------------------------------------------------------
# Test _get_query_start_for_synthesis
# ---------------------------------------------------------------------------

class TestGetQueryStartForSynthesis:
    def test_60min_returns_last_ts(self):
        from data.loader import _get_query_start_for_synthesis
        import pandas as pd
        last_ts = pd.Timestamp("2026-07-03T14:00:00")
        result = _get_query_start_for_synthesis(last_ts, "60分钟")
        assert result == last_ts

    def test_daily_returns_last_ts(self):
        """★ 关键回归: 日线返回last_ts而非次日"""
        from data.loader import _get_query_start_for_synthesis
        import pandas as pd
        last_ts = pd.Timestamp("2026-07-02T00:00:00")
        result = _get_query_start_for_synthesis(last_ts, "日线")
        assert result == last_ts  # 不是次日!

    def test_weekly_returns_last_ts(self):
        from data.loader import _get_query_start_for_synthesis
        import pandas as pd
        last_ts = pd.Timestamp("2026-06-29T00:00:00")
        result = _get_query_start_for_synthesis(last_ts, "周线")
        assert result == last_ts


# ---------------------------------------------------------------------------
# Test _offset_to_tz
# ---------------------------------------------------------------------------

class TestOffsetToTz:
    def test_positive_offset(self):
        from data.loader import _offset_to_tz
        from datetime import timezone, timedelta
        tz = _offset_to_tz("+08:00")
        assert tz.utcoffset(None) == timedelta(hours=8)

    def test_negative_offset(self):
        from data.loader import _offset_to_tz
        from datetime import timezone, timedelta
        tz = _offset_to_tz("-04:00")
        assert tz.utcoffset(None) == timedelta(hours=-4)

    def test_utc_z(self):
        from data.loader import _offset_to_tz
        from datetime import timezone, timedelta
        tz = _offset_to_tz("Z")
        assert tz.utcoffset(None) == timedelta(0)

    def test_empty_string(self):
        from data.loader import _offset_to_tz
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
        # cutoff == last → 边界场景需合成(DB bar是收盘后完整版)
        ("60分钟", "2026-07-03T14:00:00+08:00", "2026-07-03T14:00:00+08:00", True),
        ("日线", "2026-07-02T00:00:00", "2026-07-02T00:00:00", True),
    ])
    def test_needs_synthesis(self, tf, last_date, cutoff, expected):
        from data.loader import _needs_synthesis
        db_rows = [{"Date": last_date}]
        assert _needs_synthesis(tf, db_rows, cutoff) == expected


# ---------------------------------------------------------------------------
# Test _aggregate_bars
# ---------------------------------------------------------------------------

class TestAggregateBars:
    def test_ohlcv_aggregation(self):
        from data.loader import _aggregate_bars
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
        from data.loader import _aggregate_bars
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
        from data.loader import _find_immediate_finer_tf
        tfs = ["15分钟", "60分钟", "日线", "周线"]
        assert _find_immediate_finer_tf("日线", tfs) == "60分钟"

    def test_weekly_finds_daily(self):
        from data.loader import _find_immediate_finer_tf
        tfs = ["15分钟", "60分钟", "日线", "周线"]
        assert _find_immediate_finer_tf("周线", tfs) == "日线"

    def test_finest_returns_none(self):
        from data.loader import _find_immediate_finer_tf
        tfs = ["15分钟", "60分钟", "日线"]
        assert _find_immediate_finer_tf("15分钟", tfs) is None

    def test_skip_missing_tf(self):
        from data.loader import _find_immediate_finer_tf
        tfs = ["60分钟", "日线"]  # no 周线
        assert _find_immediate_finer_tf("日线", tfs) == "60分钟"


# ---------------------------------------------------------------------------
# Test _build_output_df
# ---------------------------------------------------------------------------

class TestBuildOutputDf:
    def test_db_only(self):
        from data.loader import _build_output_df
        db_rows = [
            {"Date": "2026-07-01T00:00:00", "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5, "Volume": 1000},
            {"Date": "2026-07-02T00:00:00", "Open": 100.5, "High": 102.0, "Low": 100.0, "Close": 101.5, "Volume": 2000},
        ]
        df = _build_output_df(db_rows, None, 120)
        assert len(df) == 2
        assert df["Close"].iloc[-1] == 101.5
        assert "Date" in df.columns

    def test_with_synth_bar(self):
        """合成bar替换最后一条DB bar, 而非追加 — 提交 9e3283d 回归"""
        from data.loader import _build_output_df
        db_rows = [
            {"Date": "2026-07-01T00:00:00", "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5, "Volume": 1000},
            {"Date": "2026-07-02T00:00:00", "Open": 100.5, "High": 102.0, "Low": 100.0, "Close": 101.5, "Volume": 2000},
        ]
        synth = {"Date": "2026-07-02T15:45:00", "Open": 101.0, "High": 103.0, "Low": 100.5, "Close": 102.0, "Volume": 3000}
        df = _build_output_df(db_rows, synth, 120)
        # 2 DB bars + 1 synth → replace last DB bar → still 2 bars
        assert len(df) == 2
        # 合成bar的Close
        assert df["Close"].iloc[-1] == 102.0
        # 第一条DB bar保持不变
        assert df["Close"].iloc[0] == 100.5
        # 第二条DB bar(7/2)已被合成bar替换 — Date变了
        assert df["Date"].iloc[-1] == "2026-07-02T15:45:00"

    def test_synth_only_no_db(self):
        """无DB数据仅合成bar — 边界场景: 正常返回单行DataFrame"""
        from data.loader import _build_output_df
        synth = {"Date": "2026-07-02T15:45:00", "Open": 101.0, "High": 103.0, "Low": 100.5, "Close": 102.0, "Volume": 3000}
        df = _build_output_df([], synth, 120)
        assert len(df) == 1
        assert df["Close"].iloc[0] == 102.0
        assert df["Date"].iloc[0] == "2026-07-02T15:45:00"

    def test_truncation(self):
        from data.loader import _build_output_df
        db_rows = [{"Date": f"2026-07-{i:02d}T00:00:00", "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1000} for i in range(1, 11)]
        n_pts = 5
        df = _build_output_df(db_rows, None, n_pts)
        assert len(df) == n_pts
        # Should keep last n_pts entries
        assert df["Date"].iloc[0] == "2026-07-06T00:00:00"
        assert df["Date"].iloc[-1] == "2026-07-10T00:00:00"


# ---------------------------------------------------------------------------
# TestCrossPeriodFilter — 跨周期过滤 (提交 6087986 + 2b4e2b7)
# ---------------------------------------------------------------------------

class TestCrossPeriodFilter:
    """跨周期过滤: 确保各TF只使用当前周期内的finer bar。

    _synthesize_incomplete_bar 内部在聚合前按 target_tf 过滤 all_finer_bars:
    - 分钟/日线: 同日(str[:10] == cutoff所在日期)
    - 周线:    >= 本周一
    - 月线:    >= 本月1日
    - 季线:    >= 本季首日
    """

    def test_minute_tf_same_day_filter(self):
        """60分钟合成: 排除不同日的15分钟bar — 提交 6087986"""
        from data.loader import _synthesize_incomplete_bar
        from unittest.mock import patch

        db_rows = [{"Date": "2026-07-03T14:00:00+08:00", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000}]
        cutoff_date = "2026-07-03T14:47:00+08:00"

        mock_finer_bars = [
            {"Date": "2026-07-02T14:45:00+08:00", "Open": 90, "High": 91, "Low": 89, "Close": 90.5, "Volume": 500},   # 7/2 — 应被过滤
            {"Date": "2026-07-03T14:15:00+08:00", "Open": 100, "High": 102, "Low": 99, "Close": 101, "Volume": 1000},  # 7/3 — 保留
            {"Date": "2026-07-03T14:30:00+08:00", "Open": 101, "High": 103, "Low": 100, "Close": 102, "Volume": 1500}, # 7/3 — 保留
        ]

        with patch('services.data_loader._query_tf_for_period', return_value=mock_finer_bars), \
             patch('services.data_loader._query_tf_from_db', return_value=[{"Date": "2026-07-03T14:15:00+08:00"}]):
            result = _synthesize_incomplete_bar("60分钟", db_rows, cutoff_date, "AAPL", "15分钟", None)

        assert result is not None
        assert result["Open"] == 100.0          # 第一条保留bar的Open
        assert result["Close"] == 102.0         # 最后一条保留bar的Close
        assert result["Volume"] == 2500.0       # 只含7/3的bar (1000+1500)

    def test_daily_same_day_filter(self):
        """日线合成: 只保留同日60分钟bar — 提交 2b4e2b7"""
        from data.loader import _synthesize_incomplete_bar
        from unittest.mock import patch

        db_rows = [{"Date": "2026-07-02T00:00:00", "Open": 100, "High": 105, "Low": 99, "Close": 104, "Volume": 20000}]
        cutoff_date = "2026-07-03T14:47:00+08:00"

        mock_finer_bars = [
            {"Date": "2026-07-02T14:00:00+08:00", "Open": 95, "High": 96, "Low": 94, "Close": 95.5, "Volume": 1000},   # 7/2 — 过滤
            {"Date": "2026-07-03T09:30:00+08:00", "Open": 104, "High": 106, "Low": 103, "Close": 105, "Volume": 2000},  # 7/3 — 保留
            {"Date": "2026-07-03T10:30:00+08:00", "Open": 105, "High": 108, "Low": 104, "Close": 107, "Volume": 3000},  # 7/3 — 保留
        ]

        with patch('services.data_loader._query_tf_for_period', return_value=mock_finer_bars), \
             patch('services.data_loader._query_tf_from_db', return_value=[{"Date": "2026-07-03T10:30:00+08:00"}]):
            result = _synthesize_incomplete_bar("日线", db_rows, cutoff_date, "AAPL", "60分钟", None)

        assert result is not None
        assert result["Open"] == 104.0          # 第一条7/3 bar的Open
        assert result["Close"] == 107.0         # 最后一条7/3 bar的Close
        assert result["Volume"] == 5000.0       # 只含7/3 bar (2000+3000)

    def test_weekly_filter(self):
        """周线合成: 只保留本周一起的日线bar — 提交 2b4e2b7"""
        from data.loader import _synthesize_incomplete_bar
        from unittest.mock import patch

        # 2026-07-02 = Thursday → week_start = Monday 2026-06-29
        db_rows = [{"Date": "2026-06-26T00:00:00", "Open": 95, "High": 100, "Low": 94, "Close": 99, "Volume": 100000}]
        cutoff_date = "2026-07-02T14:47:00-04:00"

        mock_finer_bars = [
            {"Date": "2026-06-26T00:00:00", "Open": 96, "High": 97, "Low": 95, "Close": 96.5, "Volume": 5000},   # 上周五 — 过滤
            {"Date": "2026-06-29T00:00:00", "Open": 99, "High": 101, "Low": 98, "Close": 100, "Volume": 6000},   # 周一 — 保留
            {"Date": "2026-06-30T00:00:00", "Open": 100, "High": 103, "Low": 99, "Close": 102, "Volume": 7000},  # 周二 — 保留
            {"Date": "2026-07-01T00:00:00", "Open": 102, "High": 105, "Low": 101, "Close": 104, "Volume": 8000},  # 周三 — 保留
            {"Date": "2026-07-02T00:00:00", "Open": 104, "High": 106, "Low": 103, "Close": 105, "Volume": 9000},  # 周四 — 保留
        ]

        with patch('services.data_loader._query_tf_for_period', return_value=mock_finer_bars), \
             patch('services.data_loader._query_tf_from_db', return_value=[{"Date": "2026-07-02T00:00:00"}]):
            result = _synthesize_incomplete_bar("周线", db_rows, cutoff_date, "AAPL", "日线", None)

        assert result is not None
        assert result["Open"] == 99.0           # 第一条保留bar (6/29)
        assert result["Close"] == 105.0         # 最后一条保留bar (7/2)
        assert result["Volume"] == 30000.0      # 只含本周bar (6000+7000+8000+9000)

    def test_monthly_filter(self):
        """月线合成: 只保留本月起的bar — 提交 2b4e2b7"""
        from data.loader import _synthesize_incomplete_bar
        from unittest.mock import patch

        db_rows = [{"Date": "2026-06-30T00:00:00", "Open": 100, "High": 110, "Low": 99, "Close": 108, "Volume": 500000}]
        cutoff_date = "2026-07-15T14:47:00-04:00"

        mock_finer_bars = [
            {"Date": "2026-06-28T00:00:00", "Open": 106, "High": 107, "Low": 105, "Close": 106.5, "Volume": 3000},  # 6月 — 过滤
            {"Date": "2026-06-29T00:00:00", "Open": 107, "High": 108, "Low": 106, "Close": 107.5, "Volume": 4000},  # 6月 — 过滤
            {"Date": "2026-06-30T00:00:00", "Open": 108, "High": 109, "Low": 107, "Close": 108, "Volume": 5000},    # 6月 — 过滤
            {"Date": "2026-07-01T00:00:00", "Open": 108, "High": 112, "Low": 107, "Close": 111, "Volume": 6000},    # 7月 — 保留
            {"Date": "2026-07-02T00:00:00", "Open": 111, "High": 113, "Low": 110, "Close": 112, "Volume": 7000},    # 7月 — 保留
        ]

        with patch('services.data_loader._query_tf_for_period', return_value=mock_finer_bars), \
             patch('services.data_loader._query_tf_from_db', return_value=[{"Date": "2026-07-15T00:00:00"}]):
            result = _synthesize_incomplete_bar("月线", db_rows, cutoff_date, "AAPL", "日线", None)

        assert result is not None
        assert result["Open"] == 108.0          # 第一条7月bar
        assert result["Close"] == 112.0         # 最后一条7月bar
        assert result["Volume"] == 13000.0      # 只含7月bar (6000+7000)

    def test_quarterly_filter(self):
        """季线合成: 只保留本季起的bar (Q3 = 7月1日起) — 提交 2b4e2b7"""
        from data.loader import _synthesize_incomplete_bar
        from unittest.mock import patch

        db_rows = [{"Date": "2026-06-30T00:00:00", "Open": 90, "High": 100, "Low": 89, "Close": 99, "Volume": 2000000}]
        cutoff_date = "2026-07-15T14:47:00-04:00"

        mock_finer_bars = [
            {"Date": "2026-06-28T00:00:00", "Open": 97, "High": 98, "Low": 96, "Close": 97.5, "Volume": 2000},   # Q2 — 过滤
            {"Date": "2026-07-01T00:00:00", "Open": 99, "High": 102, "Low": 98, "Close": 101, "Volume": 3000},   # Q3 — 保留
            {"Date": "2026-07-02T00:00:00", "Open": 101, "High": 104, "Low": 100, "Close": 103, "Volume": 4000},  # Q3 — 保留
        ]

        with patch('services.data_loader._query_tf_for_period', return_value=mock_finer_bars), \
             patch('services.data_loader._query_tf_from_db', return_value=[{"Date": "2026-07-15T00:00:00"}]):
            result = _synthesize_incomplete_bar("季线", db_rows, cutoff_date, "AAPL", "日线", None)

        assert result is not None
        assert result["Open"] == 99.0           # 第一条Q3 bar
        assert result["Close"] == 103.0         # 最后一条Q3 bar
        assert result["Volume"] == 7000.0       # 只含Q3 bar (3000+4000)


# ---------------------------------------------------------------------------
# TestBoundarySynthesis — 边界合成 (提交 6087986)
# ---------------------------------------------------------------------------

class TestBoundarySynthesis:
    """边界场景: cutoff == last_ts 时 _needs_synthesis 仍返回 True,
    且 _build_output_df 中合成bar替换DB最后bar。"""

    def test_needs_synthesis_boundary_60min(self):
        """60分钟: cutoff==14:00, DB有14:00完整bar → 需要合成(替换DB bar)"""
        from data.loader import _needs_synthesis
        db_rows = [{"Date": "2026-07-03T14:00:00+08:00"}]
        assert _needs_synthesis("60分钟", db_rows, "2026-07-03T14:00:00+08:00") == True

    def test_needs_synthesis_boundary_daily(self):
        """日线: cutoff==7/2 00:00, DB有7/2完整bar → 需要合成"""
        from data.loader import _needs_synthesis
        db_rows = [{"Date": "2026-07-02T00:00:00"}]
        assert _needs_synthesis("日线", db_rows, "2026-07-02T00:00:00") == True

    def test_synth_replaces_db_at_boundary(self):
        """_build_output_df: cutoff==last_ts时合成bar替换DB bar (相同Date)"""
        from data.loader import _build_output_df
        db_rows = [
            {"Date": "2026-07-01T00:00:00", "Open": 100, "High": 101, "Low": 99, "Close": 100.5, "Volume": 1000},
            {"Date": "2026-07-02T00:00:00", "Open": 100.5, "High": 102, "Low": 100, "Close": 101.5, "Volume": 2000},
        ]
        # 合成bar的Date == 最后一条DB bar的Date (边界)
        synth = {"Date": "2026-07-02T00:00:00", "Open": 101, "High": 103, "Low": 100.5, "Close": 102, "Volume": 3000}
        df = _build_output_df(db_rows, synth, 120)
        assert len(df) == 2                     # replace, not append
        assert df["Close"].iloc[-1] == 102.0    # synth bar's Close
        assert df["Date"].iloc[-1] == "2026-07-02T00:00:00"


# ---------------------------------------------------------------------------
# TestCascadeEndToEnd — 端到端Close一致性
# ---------------------------------------------------------------------------

class TestCascadeEndToEnd:
    """端到端: 验证多级级联合成中Close值正确传播"""

    def test_close_consistency_60min_to_weekly(self):
        """60分钟→日线→周线: 合成bar的Close值级联一致"""
        from data.loader import _sync_all_cascading
        from unittest.mock import patch

        tfs = ["60分钟", "日线", "周线"]
        cutoff = "2026-07-03T14:47:00+08:00"

        # 捕获各TF的最终输出DataFrame
        written = {}

        def capture_write(tf, df, ticker_code=""):
            written[tf] = df.copy()
            return True

        # ── Mock DB queries ──
        def q_db(ticker, tf, cutoff_date, n_pts):
            if tf == "60分钟":
                return [
                    {"Date": "2026-07-03T14:00:00+08:00", "Open": 100, "High": 102, "Low": 99, "Close": 101, "Volume": 1000},
                ]
            elif tf == "日线":
                return [
                    {"Date": "2026-07-01T00:00:00", "Open": 98, "High": 100, "Low": 97, "Close": 99, "Volume": 5000},
                    {"Date": "2026-07-02T00:00:00", "Open": 99, "High": 105, "Low": 98, "Close": 104, "Volume": 8000},
                ]
            elif tf == "周线":
                return [
                    {"Date": "2026-06-26T00:00:00", "Open": 95, "High": 100, "Low": 94, "Close": 99, "Volume": 20000},
                ]
            return []

        def q_period(ticker, tf, ps, pe):
            if tf == "60分钟":
                return [
                    {"Date": "2026-07-03T09:30:00+08:00", "Open": 100, "High": 103, "Low": 99, "Close": 103, "Volume": 2000},
                    {"Date": "2026-07-03T14:00:00+08:00", "Open": 103, "High": 106, "Low": 102, "Close": 105, "Volume": 3000},
                ]
            elif tf == "日线":
                return [
                    {"Date": "2026-06-29T00:00:00", "Open": 97, "High": 99, "Low": 96, "Close": 98, "Volume": 3000},
                    {"Date": "2026-06-30T00:00:00", "Open": 98, "High": 100, "Low": 97, "Close": 99, "Volume": 4000},
                    {"Date": "2026-07-01T00:00:00", "Open": 99, "High": 101, "Low": 98, "Close": 100, "Volume": 3500},
                    {"Date": "2026-07-02T00:00:00", "Open": 100, "High": 102, "Low": 99, "Close": 101, "Volume": 4500},
                ]
            return []

        with patch('services.data_loader._write_parquet', side_effect=capture_write), \
             patch('services.data_loader._query_tf_from_db', side_effect=q_db), \
             patch('services.data_loader._query_tf_for_period', side_effect=q_period):
            results = _sync_all_cascading("AAPL", tfs, cutoff, "60分钟", 120)

        # 三个TF均写入成功
        assert results == {"60分钟": True, "日线": True, "周线": True}
        assert len(written) == 3

        # 60分钟 = min_tf → 不合成, Close = 最后DB bar
        assert written["60分钟"]["Close"].iloc[-1] == 101.0

        # 日线: 从60分钟合成, Close = 最后一条60分钟bar(当日)的Close = 105
        assert written["日线"]["Close"].iloc[-1] == 105.0

        # 周线: 级联使用日线合成bar, Close应传播一致
        assert written["周线"]["Close"].iloc[-1] == 105.0

        # ★ 核心断言: 日线和周线的Close一致 (级联传播)
        assert written["日线"]["Close"].iloc[-1] == written["周线"]["Close"].iloc[-1]


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
