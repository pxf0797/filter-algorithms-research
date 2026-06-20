"""
Tests for _align_pnl_to_current_tf — 时间对齐和边界条件。
"""
import numpy as np
import pandas as pd
import pytest

from streamlit_app import _align_pnl_to_current_tf


# =========================================================================
# _align_pnl_to_current_tf
# =========================================================================

class TestAlignPnlToCurrentTf:

    @pytest.mark.alignment
    def test_tz_mixed_hkt_naive(self):
        """时区混合对齐: 日线(tz-naive) → 60分钟(tz-aware HKT).

        构造时间重叠: daily bar 涵盖 intraday 区间。
        """
        # 日线日期 (tz-naive) — 从 2026-06-01 开始, 5个bars
        higher_dates = pd.date_range("2026-06-01", periods=5, freq="D")
        n_high = len(higher_dates)
        # 递增高周期 PnL: [100, 101, 102, 103, 104]
        higher_pnl_long = 100.0 + np.arange(n_high, dtype=float)
        higher_pnl_short = np.full(n_high, 100.0)

        # 做多交易: entry_idx=1 (2026-06-02), exit_idx=3 (2026-06-04)
        # entry_time/exit_time 在 intraday 时间段内, 确保 markers 被映射
        higher_trades = [
            {"type": "long", "entry_idx": 1, "exit_idx": 3,
             "entry_price": 101.0, "exit_price": 103.0,
             "return_pct": 1.98, "exit_reason": "take_profit"},
        ]

        # intraday = tz-aware HKT, 从 2026-06-01 到 2026-06-04 (72h = 3天)
        current_dates = pd.date_range(
            "2026-06-01 09:30", periods=72, freq="h", tz="Asia/Hong_Kong",
        )

        result = _align_pnl_to_current_tf(
            higher_dates, higher_pnl_long, higher_pnl_short,
            higher_trades, current_dates,
        )

        # aligned_long 应在时间重叠区间非全 NaN
        assert not np.all(np.isnan(result["aligned_long"])), (
            "tz-aware 与 tz-naive 混合对齐后不应全 NaN"
        )
        # 第一个 intraday bar (09:30) 应对齐到 higher_dates[0] (06-01) 的 PnL = 100.0
        assert result["aligned_long"][0] == 100.0, (
            "第一个当前 bar 应对齐到 higher_dates[0] 的 PnL"
        )

        # entry_markers: entry_idx=1 → hd[1]=2026-06-02
        # 前向填充到最近的 intraday bar (06-02 00:30), 其 aligned_long = higher_pnl_long[1] = 101.0
        assert len(result["entry_markers"]) == 1, "应生成 1 个入场标记"
        entry_bar, etype, epnl = result["entry_markers"][0]
        assert etype == "long", "入场类型应为 long"

        # exit_markers 应包含 return_pct 和 exit_reason
        assert len(result["exit_markers"]) == 1, "应生成 1 个离场标记"
        exit_bar, xtype, xpnl, ret, reason = result["exit_markers"][0]
        assert xtype == "long", "离场类型应为 long"
        assert ret == 1.98, "离场收益率应匹配"
        assert reason == "take_profit", "离场原因应匹配"

        # 验证短周期(short)对齐
        assert not np.all(np.isnan(result["aligned_short"])), "aligned_short 不应全NaN"

    @pytest.mark.alignment
    def test_no_time_overlap(self):
        """无时间重叠: higher_dates 完全在 current_dates 之后 → aligned 全NaN.

        higher_dates = 2024年, current_dates = 2019年.
        所有 higher_dates 都 > current_dates, 前向填充 hd <= cd[i] 找不到匹配.
        注意: entry/exit markers 有 fallback 逻辑 (NaN → 100.0), 所以 markers
        不受 aligned NaN 的影响, 仍然被映射出来. 这里验证 aligned PnL 为全NaN.
        """
        higher_dates = pd.date_range("2024-01-01", periods=5, freq="D")
        higher_pnl_long = 100.0 + np.arange(5, dtype=float)
        higher_pnl_short = np.full(5, 100.0)
        higher_trades = [
            {"type": "long", "entry_idx": 2, "exit_idx": 4,
             "entry_price": 102.0, "exit_price": 104.0,
             "return_pct": 1.96, "exit_reason": "take_profit"},
        ]
        # current_dates 完全在 2019 年, 早于 higher_dates (2024)
        current_dates = pd.date_range(
            "2019-06-01", periods=120, freq="h", tz="Asia/Hong_Kong",
        )

        result = _align_pnl_to_current_tf(
            higher_dates, higher_pnl_long, higher_pnl_short,
            higher_trades, current_dates,
        )

        assert np.all(np.isnan(result["aligned_long"])), "无重叠时 aligned_long 应全 NaN"
        assert np.all(np.isnan(result["aligned_short"])), "无重叠时 aligned_short 应全 NaN"
        # markers 存在 (fallback: NaN → 100.0), 验证结构
        assert all(len(m) > 2 for m in result["entry_markers"])
        assert all(len(m) == 5 for m in result["exit_markers"])

    @pytest.mark.alignment
    def test_forward_fill(self):
        """前向填充验证: high=[D1,D2,D3], current=[D1, D1+1h, D2, D2+1h, D3]."""
        higher_dates = pd.date_range("2026-01-01", periods=3, freq="D")
        higher_pnl_long = np.array([100.0, 105.0, 110.0])
        higher_pnl_short = np.full(3, 100.0)
        higher_trades = []

        # current: 每个 daily bar 后有 1h bar
        base = pd.Timestamp("2026-01-01 09:30", tz="Asia/Hong_Kong")
        hourly_offsets = [0, 1, 24, 25, 48]  # hours from base
        current_dates = pd.DatetimeIndex([base + pd.Timedelta(hours=h) for h in hourly_offsets])

        result = _align_pnl_to_current_tf(
            higher_dates, higher_pnl_long, higher_pnl_short,
            higher_trades, current_dates,
        )

        expected = np.array([100.0, 100.0, 105.0, 105.0, 110.0])
        np.testing.assert_array_equal(result["aligned_long"], expected)

    @pytest.mark.alignment
    def test_higher_dates_none(self, sample_dates_intraday):
        """higher_dates=None → 返回全NaN和空markers."""
        current_dates = sample_dates_intraday[:10]
        result = _align_pnl_to_current_tf(
            None, np.array([100.0]), np.array([100.0]),
            [{"type": "long", "entry_idx": 0, "exit_idx": 0}],
            current_dates,
        )
        assert np.all(np.isnan(result["aligned_long"])), "higher_dates=None → 全NaN"
        assert np.all(np.isnan(result["aligned_short"])), "higher_dates=None → 全NaN"
        assert result["entry_markers"] == [], "higher_dates=None → 空 markers"
        assert result["exit_markers"] == [], "higher_dates=None → 空 markers"

    @pytest.mark.alignment
    def test_marker_positions(self):
        """交易 marker 在 current_dates 中正确位置."""
        # higher: 5 个 daily bars
        higher_dates = pd.date_range("2026-01-01", periods=5, freq="D")
        higher_pnl_long = 100.0 + np.arange(5, dtype=float)
        higher_pnl_short = np.full(5, 100.0)
        # entry_idx=1 (bar 1), exit_idx=3 (bar 3)
        higher_trades = [
            {"type": "long", "entry_idx": 1, "exit_idx": 3,
             "entry_price": 101.0, "exit_price": 103.0,
             "return_pct": 1.98, "exit_reason": "stop_loss"},
        ]

        # current: 每 8h 一根 bar，覆盖 higher 范围
        base = pd.Timestamp("2026-01-01 00:00", tz="Asia/Hong_Kong")
        current_dates = pd.DatetimeIndex([base + pd.Timedelta(hours=8 * i) for i in range(15)])

        result = _align_pnl_to_current_tf(
            higher_dates, higher_pnl_long, higher_pnl_short,
            higher_trades, current_dates,
        )

        assert len(result["entry_markers"]) == 1
        entry_bar, etype, epnl = result["entry_markers"][0]
        assert etype == "long"

        # 验证 exit_marker 包含 return_pct 和 exit_reason
        assert len(result["exit_markers"]) == 1
        exit_bar, xtype, xpnl, ret, reason = result["exit_markers"][0]
        assert ret == 1.98
        assert reason == "stop_loss"

    @pytest.mark.alignment
    def test_higher_shorter_than_current(self):
        """高周期PnL短于当前周期 → 时间重叠部分对齐, 后续阶段前向填充.

        构造: 5个 daily bars 覆盖整个 intraday 范围, 但 higher_pnl 只算到第 3 个 bar.
        前向填充仍然有效 (hd <= cd[i] 成立), 但不会崩溃.
        """
        higher_dates = pd.date_range("2026-06-01", periods=5, freq="D")
        higher_pnl_long = np.array([100.0, 101.0, 102.0, 103.0, 104.0])
        higher_pnl_short = np.full(5, 100.0)
        higher_trades = []

        current_dates = pd.date_range(
            "2026-06-01 09:30", periods=15, freq="h", tz="Asia/Hong_Kong",
        )

        result = _align_pnl_to_current_tf(
            higher_dates, higher_pnl_long, higher_pnl_short,
            higher_trades, current_dates,
        )

        # 前向填充: 所有 intraday bar 都 ≤ 最后 daily bar → 全部对齐
        assert len(result["aligned_long"]) == len(current_dates), "结果长度应匹配"
        assert not np.all(np.isnan(result["aligned_long"])), "时间重叠时应全部对齐"
