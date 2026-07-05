"""Tests for backtest boundary bar synthesis logic.

Covers three functions from services.data_loader:
  - _get_period_end(ts, tf)      — next period boundary timestamp
  - _get_period_start(ts, tf)    — data query start point for high-TF synthesis
  - _sync_to_display synthesis condition — ALL_TFS.index(tf) > ALL_TFS.index(min_tf)

These are pure functions with no DB/Streamlit dependency.
"""
import sys
import os
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'filter_app'))

from services.data_loader import _get_period_end, _get_period_start, ALL_TFS


# =========================================================================
# _get_period_end — 周期结束时间边界计算
# =========================================================================

class TestGetPeriodEnd:
    """Test _get_period_end() — given a timestamp, find the next
    period-closing timestamp for the specified timeframe.

    Verifies weekday anchoring (周线→周五), month-end (月线), quarter-end
    (季线), and all simpler TF boundaries (日线, 60分钟, 15分钟, 5分钟).
    """

    # ── 周线 ──────────────────────────────────────────────────────────

    def test_weekly_midweek(self):
        """周三 → 本周五"""
        ts = pd.Timestamp("2024-03-13")  # Wednesday
        result = _get_period_end(ts, "周线")
        assert result == pd.Timestamp("2024-03-15")  # Friday

    def test_weekly_friday(self):
        """周五 → 下周五"""
        ts = pd.Timestamp("2024-03-15")  # Friday
        result = _get_period_end(ts, "周线")
        assert result == pd.Timestamp("2024-03-22")  # next Friday

    def test_weekly_saturday(self):
        """周六 → 下周五"""
        ts = pd.Timestamp("2024-03-16")  # Saturday
        result = _get_period_end(ts, "周线")
        assert result == pd.Timestamp("2024-03-22")  # next Friday

    def test_weekly_sunday(self):
        """周日 → 下周五"""
        ts = pd.Timestamp("2024-03-17")  # Sunday
        result = _get_period_end(ts, "周线")
        assert result == pd.Timestamp("2024-03-22")  # next Friday

    def test_weekly_monday(self):
        """周一 → 本周五"""
        ts = pd.Timestamp("2024-03-11")  # Monday (weekday=0)
        result = _get_period_end(ts, "周线")
        assert result == pd.Timestamp("2024-03-15")

    # ── 月线 ──────────────────────────────────────────────────────────

    def test_monthly_midmonth(self):
        """月中 → 当月月末"""
        ts = pd.Timestamp("2024-03-15")
        result = _get_period_end(ts, "月线")
        assert result == pd.Timestamp("2024-03-31")

    def test_monthly_eoy(self):
        """12月 → 12月31日（跨年）"""
        ts = pd.Timestamp("2024-12-15")
        result = _get_period_end(ts, "月线")
        assert result == pd.Timestamp("2024-12-31")

    def test_monthly_feb_leap(self):
        """2月（闰年）→ 2月29日"""
        ts = pd.Timestamp("2024-02-15")  # leap year
        result = _get_period_end(ts, "月线")
        assert result == pd.Timestamp("2024-02-29")

    def test_monthly_feb_nonleap(self):
        """2月（非闰年）→ 2月28日"""
        ts = pd.Timestamp("2023-02-15")  # non-leap
        result = _get_period_end(ts, "月线")
        assert result == pd.Timestamp("2023-02-28")

    def test_monthly_jan_31(self):
        """1月最后一天 → 1月31日"""
        ts = pd.Timestamp("2024-01-31")
        result = _get_period_end(ts, "月线")
        assert result == pd.Timestamp("2024-01-31")

    # ── 季线 ──────────────────────────────────────────────────────────

    # ╔══════════════════════════════════════════════════════════════════╗
    # ║  季线 BUG 集群 — `_get_period_end` 的季线分支                   ║
    # ║                                                                ║
    # ║  根因: next_q_end = pd.Timestamp(year, month=q_end_month,       ║
    # ║        day=1) - 1 day                                         ║
    # ║  这给出的是 q_end_month 的「前一月」末, 而非 q_end_month 末。   ║
    # ║  修复应为:                                                      ║
    # ║   next_q_end = pd.Timestamp(year, month=q_end_month + 1,       ║
    # ║                 day=1) - 1 day                                 ║
    # ║  (另需处理 month=12 时 month+1=13 的跨年问题)                    ║
    # ╚══════════════════════════════════════════════════════════════════╝

    def test_quarterly_mid(self):
        """2月15日（Q1中）→ 2024-03-31（Q1末）"""
        ts = pd.Timestamp("2024-02-15")
        result = _get_period_end(ts, "季线")
        assert result == pd.Timestamp("2024-03-31")

    def test_quarterly_q4_eoy(self):
        """11月15日（Q4中）→ 2024-12-31（Q4末，跨年）"""
        ts = pd.Timestamp("2024-11-15")
        result = _get_period_end(ts, "季线")
        assert result == pd.Timestamp("2024-12-31")

    def test_quarterly_q2_boundary(self):
        """4月1日（Q2首日）→ 2024-06-30（Q2末）"""
        ts = pd.Timestamp("2024-04-01")
        result = _get_period_end(ts, "季线")
        assert result == pd.Timestamp("2024-06-30")

    def test_quarterly_q1_early(self):
        """1月1日 → 2024-03-31（Q1末）"""
        ts = pd.Timestamp("2024-01-01")
        result = _get_period_end(ts, "季线")
        assert result == pd.Timestamp("2024-03-31")

    def test_quarterly_q3_early(self):
        """7月1日 → 2024-09-30（Q3末）"""
        ts = pd.Timestamp("2024-07-01")
        result = _get_period_end(ts, "季线")
        assert result == pd.Timestamp("2024-09-30")

    # ── 日线 ──────────────────────────────────────────────────────────

    def test_daily(self):
        """当天 → 次日凌晨"""
        ts = pd.Timestamp("2024-03-13")
        result = _get_period_end(ts, "日线")
        assert result == pd.Timestamp("2024-03-14")

    def test_daily_eoy(self):
        """12月31日 → 次年1月1日（跨年）"""
        ts = pd.Timestamp("2024-12-31")
        result = _get_period_end(ts, "日线")
        assert result == pd.Timestamp("2025-01-01")

    # ── 分钟线 ────────────────────────────────────────────────────────

    def test_60min(self):
        """10:30 → 11:00"""
        ts = pd.Timestamp("2024-03-13 10:30")
        result = _get_period_end(ts, "60分钟")
        assert result == pd.Timestamp("2024-03-13 11:00")

    def test_60min_boundary(self):
        """11:00 → 12:00（恰好整点）"""
        ts = pd.Timestamp("2024-03-13 11:00")
        result = _get_period_end(ts, "60分钟")
        assert result == pd.Timestamp("2024-03-13 12:00")

    def test_15min(self):
        """10:10 → 10:15"""
        ts = pd.Timestamp("2024-03-13 10:10")
        result = _get_period_end(ts, "15分钟")
        assert result == pd.Timestamp("2024-03-13 10:15")

    # ╔══════════════════════════════════════════════════════════════════╗
    # ║  分钟线跨小时 BUG — minute_ceil >= 60 处理逻辑                   ║
    # ║                                                                ║
    # ║  根因: minute_ceil=60 时执行                                   ║
    # ║   ts = ts.ceil("1h") + pd.Timedelta(hours=1)                   ║
    # ║  即先向上取整到整点再 +1 小时 → 多跳了一小时。                   ║
    # ║  正确应为 ts.ceil("1h") 后 minute_ceil=0 直接 replace。         ║
    # ╚══════════════════════════════════════════════════════════════════╝

    def test_15min_cross_hour(self):
        """10:50 → 11:00（下一个15分钟边界，跨小时正确）"""
        ts = pd.Timestamp("2024-03-13 10:50")
        result = _get_period_end(ts, "15分钟")
        assert result == pd.Timestamp("2024-03-13 11:00")

    def test_5min(self):
        """10:03 → 10:05"""
        ts = pd.Timestamp("2024-03-13 10:03")
        result = _get_period_end(ts, "5分钟")
        assert result == pd.Timestamp("2024-03-13 10:05")

    def test_5min_cross_hour(self):
        """10:58 → 11:00（下一个5分钟边界，跨小时正确）"""
        ts = pd.Timestamp("2024-03-13 10:58")
        result = _get_period_end(ts, "5分钟")
        assert result == pd.Timestamp("2024-03-13 11:00")

    def test_5min_exact_boundary(self):
        """10:05 → 10:10（恰好在上一周期结束边界）"""
        ts = pd.Timestamp("2024-03-13 10:05")
        result = _get_period_end(ts, "5分钟")
        assert result == pd.Timestamp("2024-03-13 10:10")

    # ── 未知周期（降级） ──────────────────────────────────────────────

    def test_unknown_tf_returns_ts_unchanged(self):
        """未知周期 → 返回原 ts（代码降级路径）"""
        ts = pd.Timestamp("2024-03-13")
        result = _get_period_end(ts, "年线")
        assert result == ts


# =========================================================================
# _get_period_start — 高周期合成时的数据查询起点
# =========================================================================

class TestGetPeriodStart:
    """Test _get_period_start() — given the last completed bar's timestamp,
    return the ISO date string from which min-TF data should start being
    queried.

    The start point is the day *after* the last completed bar for daily/weekly,
    or the first day of the next month/quarter for 月线/季线.
    """

    def test_weekly(self):
        """上周五收盘 → 次日（周六）"""  # 实际代码返回 ts+1day
        ts = pd.Timestamp("2024-03-08")  # Friday
        result = _get_period_start(ts, "周线")
        assert result == "2024-03-09"

    def test_weekly_friday_end_of_month(self):
        """月末周五收盘 → 次月1日（跨月边界）"""
        ts = pd.Timestamp("2024-05-31")  # Friday
        result = _get_period_start(ts, "周线")
        assert result == "2024-06-01"

    def test_weekly_eoy(self):
        """年末周三收盘 → 次年1月1日（跨年）"""
        ts = pd.Timestamp("2024-12-25")  # Wednesday
        result = _get_period_start(ts, "周线")
        assert result == "2024-12-26"

    def test_monthly(self):
        """2月末 → 3月1日"""
        ts = pd.Timestamp("2024-02-29")
        result = _get_period_start(ts, "月线")
        assert result == "2024-03-01"

    def test_monthly_december(self):
        """12月末 → 次年1月1日（跨年）"""
        ts = pd.Timestamp("2024-12-31")
        result = _get_period_start(ts, "月线")
        assert result == "2025-01-01"

    def test_monthly_midmonth(self):
        """月中某日 → 次月1日"""
        ts = pd.Timestamp("2024-06-15")
        result = _get_period_start(ts, "月线")
        assert result == "2024-07-01"

    def test_quarterly(self):
        """Q1末 3月31日 → 2024-04-01（Q2首日）"""
        ts = pd.Timestamp("2024-03-31")
        result = _get_period_start(ts, "季线")
        assert result == "2024-04-01"

    def test_quarterly_q2_end(self):
        """Q2末 6月30日 → 2024-07-01（Q3首日）"""
        ts = pd.Timestamp("2024-06-30")
        result = _get_period_start(ts, "季线")
        assert result == "2024-07-01"

    def test_quarterly_q4_eoy(self):
        """Q4末 12月31日 → 2025-01-01（次年Q1首日，跨年）"""
        ts = pd.Timestamp("2024-12-31")
        result = _get_period_start(ts, "季线")
        assert result == "2025-01-01"

    def test_daily(self):
        """当天 → 次日"""
        ts = pd.Timestamp("2024-03-13")
        result = _get_period_start(ts, "日线")
        assert result == "2024-03-14"

    def test_daily_eoy(self):
        """12月31日 → 次年1月1日"""
        ts = pd.Timestamp("2024-12-31")
        result = _get_period_start(ts, "日线")
        assert result == "2025-01-01"

    def test_60min(self):
        """60分钟 → ts+1min，但 strftime(%Y-%m-%d) 丢弃时间部分

        _get_period_start 对所有分钟周期都 return ts+1min → strftime("%Y-%m-%d")
        所以即使时间部分变了，输出也只含日期。
        """
        ts = pd.Timestamp("2024-03-13 10:30")
        result = _get_period_start(ts, "60分钟")
        assert result == "2024-03-13"

    def test_unknown_tf_fallback(self):
        """未知周期 → ts + 1 day（降级路径）"""
        ts = pd.Timestamp("2024-03-13")
        result = _get_period_start(ts, "年线")
        assert result == "2024-03-14"


# =========================================================================
# _sync_to_display 的合成条件判断
# =========================================================================

class TestSynthesisCondition:
    """Test the synthesis conditional in _sync_to_display():

        ALL_TFS.index(tf) > ALL_TFS.index(min_tf)

    The condition only triggers when the display timeframe (tf) is coarser
    (higher index) than the min_tf. If tf == min_tf or tf is finer, no
    synthesis occurs, meaning the incomplete boundary bar will not be
    assembled from sub-bars.
    """

    def test_should_synthesize(self):
        """min_tf=日线(idx=4), tf=周线(idx=5) → True（周线粗于日线）"""
        min_tf = "日线"
        tf = "周线"
        assert ALL_TFS.index(tf) > ALL_TFS.index(min_tf)

    def test_should_not_synthesize_same_tf(self):
        """min_tf=日线, tf=日线 → False（同周期无需合成）"""
        min_tf = "日线"
        tf = "日线"
        assert not (ALL_TFS.index(tf) > ALL_TFS.index(min_tf))

    def test_should_not_synthesize_finer_tf(self):
        """min_tf=日线(idx=4), tf=60分钟(idx=3) → False（tf 更细）"""
        min_tf = "日线"
        tf = "60分钟"
        assert not (ALL_TFS.index(tf) > ALL_TFS.index(min_tf))

    def test_should_synthesize_monthly_from_daily(self):
        """min_tf=日线(idx=4), tf=月线(idx=6) → True"""
        min_tf = "日线"
        tf = "月线"
        assert ALL_TFS.index(tf) > ALL_TFS.index(min_tf)

    def test_should_synthesize_quarterly_from_daily(self):
        """min_tf=日线(idx=4), tf=季线(idx=7) → True"""
        min_tf = "日线"
        tf = "季线"
        assert ALL_TFS.index(tf) > ALL_TFS.index(min_tf)

    def test_should_synthesize_monthly_from_weekly(self):
        """min_tf=周线(idx=5), tf=月线(idx=6) → True"""
        min_tf = "周线"
        tf = "月线"
        assert ALL_TFS.index(tf) > ALL_TFS.index(min_tf)

    def test_should_not_synthesize_from_finer_intraday(self):
        """min_tf=5分钟(idx=1), tf=60分钟(idx=3) → True（60分钟粗于5分钟）"""
        min_tf = "5分钟"
        tf = "60分钟"
        assert ALL_TFS.index(tf) > ALL_TFS.index(min_tf)

    def test_accurate_index_positions(self):
        """验证 ALL_TFS 索引位置与预期一致"""
        expected = {
            "1分钟": 0, "5分钟": 1, "15分钟": 2,
            "60分钟": 3, "日线": 4, "周线": 5,
            "月线": 6, "季线": 7,
        }
        for tf, idx in expected.items():
            assert ALL_TFS.index(tf) == idx, (
                f"{tf} 的索引应为 {idx}，实际为 {ALL_TFS.index(tf)}"
            )
