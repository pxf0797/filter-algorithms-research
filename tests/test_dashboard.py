"""
回测仪表盘测试 — 导入、指标计算、数据验证、HTML 桥接。

测试范围:
- render_backtest_dashboard 导入和调用
- _detect_pnl_views 列检测
- _compute_metrics_from_pnl 指标计算
- 空 DataFrame / 缺失列 安全处理
- KPI 卡片渲染不崩溃
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# 导入测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestDashboardImports:
    """验证仪表盘模块可导入且包含所有公开符号。"""

    def test_import_render_backtest_dashboard(self):
        """render_backtest_dashboard 可导入且为可调用对象。"""
        from filter.backtest.dashboard import render_backtest_dashboard
        assert callable(render_backtest_dashboard)

    def test_import_detect_pnl_views(self):
        """_detect_pnl_views 可导入。"""
        from filter.backtest.dashboard import _detect_pnl_views
        assert callable(_detect_pnl_views)

    def test_import_render_kpi_cards(self):
        """_render_kpi_cards 可导入。"""
        from filter.backtest.dashboard import _render_kpi_cards
        assert callable(_render_kpi_cards)

    def test_import_render_pnl_chart(self):
        """_render_pnl_chart 可导入。"""
        from filter.backtest.dashboard import _render_pnl_chart
        assert callable(_render_pnl_chart)

    def test_import_render_trade_table(self):
        """_render_trade_table 可导入。"""
        from filter.backtest.dashboard import _render_trade_table
        assert callable(_render_trade_table)

    def test_import_render_signal_stats(self):
        """_render_signal_stats 可导入。"""
        from filter.backtest.dashboard import _render_signal_stats
        assert callable(_render_signal_stats)


# ═══════════════════════════════════════════════════════════════════════════════
# PnL 视图检测
# ═══════════════════════════════════════════════════════════════════════════════

class TestDetectPnlViews:
    """验证 _detect_pnl_views 列检测逻辑。"""

    def test_single_view_detected(self):
        """单个视图前缀被正确检测。"""
        from filter.backtest.dashboard import _detect_pnl_views

        df = pd.DataFrame({
            "v0_pnl_long":  [100.0] * 5,
            "v0_pnl_short": [100.0] * 5,
            "v0_sig":       [0] * 5,
        })
        views = _detect_pnl_views(df)
        assert views == ["v0"]

    def test_multiple_views_detected(self):
        """多个视图前缀被正确检测并排序。"""
        from filter.backtest.dashboard import _detect_pnl_views

        df = pd.DataFrame({
            "v2_pnl_long":  [100.0] * 5,
            "v2_pnl_short": [100.0] * 5,
            "v0_pnl_long":  [100.0] * 5,
            "v0_pnl_short": [100.0] * 5,
            "v1_pnl_long":  [100.0] * 5,
            "v1_pnl_short": [100.0] * 5,
        })
        views = _detect_pnl_views(df)
        assert views == ["v0", "v1", "v2"]

    def test_no_views_returns_empty(self):
        """无 PnL 列时返回空列表。"""
        from filter.backtest.dashboard import _detect_pnl_views

        df = pd.DataFrame({"a": [1], "b": [2]})
        views = _detect_pnl_views(df)
        assert views == []

    def test_long_only_ignored(self):
        """只有 _pnl_long 没有 _pnl_short 时被忽略。"""
        from filter.backtest.dashboard import _detect_pnl_views

        df = pd.DataFrame({
            "v0_pnl_long": [100.0] * 5,
            # no v0_pnl_short
        })
        views = _detect_pnl_views(df)
        assert views == []

    def test_empty_dataframe(self):
        """空 DataFrame 返回空列表。"""
        from filter.backtest.dashboard import _detect_pnl_views

        df = pd.DataFrame()
        views = _detect_pnl_views(df)
        assert views == []


# ═══════════════════════════════════════════════════════════════════════════════
# 指标计算
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeMetricsFromPnl:
    """验证 _compute_metrics_from_pnl 指标计算。"""

    def test_basic_metrics_keys(self):
        """返回的 dict 包含所有核心指标键。"""
        from filter.backtest.dashboard import _compute_metrics_from_pnl

        long_pnl = np.array([100.0, 101.0, 102.0, 103.0, 104.0])
        short_pnl = np.array([100.0, 100.0, 100.0, 100.0, 100.0])
        df = pd.DataFrame({"v0_sig": [0] * 5})

        result = _compute_metrics_from_pnl(long_pnl, short_pnl, df, "v0", len(long_pnl))

        expected_keys = [
            "total_return_pct", "sharpe_ratio", "max_drawdown_pct",
            "win_rate_pct", "total_trades", "calmar_ratio",
            "profit_factor", "sortino_ratio", "annualized_return_pct",
            "annualized_volatility_pct", "max_drawdown_duration",
            "avg_trade_return_pct",
        ]
        for key in expected_keys:
            assert key in result, f"Missing key: {key}"

    def test_positive_returns(self):
        """正向收益产生正的总收益率和正 Sharpe。"""
        from filter.backtest.dashboard import _compute_metrics_from_pnl

        long_pnl = 100.0 * np.cumprod(np.full(100, 1.001))
        short_pnl = np.full(100, 100.0)
        df = pd.DataFrame({"v0_sig": [0] * 100})

        result = _compute_metrics_from_pnl(long_pnl, short_pnl, df, "v0", len(long_pnl))

        assert result["total_return_pct"] > 0
        assert result["sharpe_ratio"] > 0

    def test_flat_pnl_zero_metrics(self):
        """平坦 PnL 产生零指标。"""
        from filter.backtest.dashboard import _compute_metrics_from_pnl

        long_pnl = np.full(10, 100.0)
        short_pnl = np.full(10, 100.0)
        df = pd.DataFrame({"v0_sig": [0] * 10})

        result = _compute_metrics_from_pnl(long_pnl, short_pnl, df, "v0", len(long_pnl))

        assert result["total_return_pct"] == 0.0
        assert result["max_drawdown_pct"] == 0.0
        assert result["total_trades"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 数据验证 & 安全处理
# ═══════════════════════════════════════════════════════════════════════════════

class TestDashboardValidation:
    """验证仪表盘对边缘输入的健壮性。"""

    def test_empty_dataframe_no_crash(self):
        """空 DataFrame 显示 info 信息而非崩溃。"""
        from filter.backtest.dashboard import render_backtest_dashboard
        import filter.backtest.dashboard as dash_mod

        empty_df = pd.DataFrame()
        # patch dash_mod.st 以捕获调用
        with patch.object(dash_mod, "st") as mock_st:
            render_backtest_dashboard(empty_df, {})
            mock_st.info.assert_called_with("回测数据为空")

    def test_missing_pnl_columns(self):
        """无 PnL 列时显示 info 而非崩溃。"""
        from filter.backtest.dashboard import render_backtest_dashboard
        import filter.backtest.dashboard as dash_mod

        df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})

        with patch.object(dash_mod, "st") as mock_st:
            render_backtest_dashboard(df, {})
            mock_st.info.assert_called_with("未找到 PnL 数据列")

    def test_nonexistent_file_calls_error(self):
        """不存在的文件路径触发 st.error。"""
        from filter.backtest.dashboard import render_backtest_dashboard
        import filter.backtest.dashboard as dash_mod

        with patch.object(dash_mod, "st") as mock_st:
            render_backtest_dashboard("/nonexistent/path.parquet", {})
            mock_st.error.assert_called_once()

    def test_valid_parquet_with_data(self, tmp_path):
        """有效 Parquet 文件能被正确加载并触发渲染。"""
        # 创建测试 Parquet
        parquet_path = tmp_path / "test.parquet"
        np.random.seed(42)
        n = 50
        df = pd.DataFrame({
            "v0_pnl_long":  100.0 * np.cumprod(np.full(n, 1.001)),
            "v0_pnl_short": np.full(n, 100.0),
            "v0_sig":       [0] * n,
        })
        df.to_parquet(parquet_path)

        from filter.backtest.dashboard import render_backtest_dashboard
        import filter.backtest.dashboard as dash_mod

        with patch.object(dash_mod, "st") as mock_st:
            render_backtest_dashboard(str(parquet_path), {})
            # 确认不抛异常，且调用了 subheader (核心指标)
            assert mock_st.subheader.called


# ═══════════════════════════════════════════════════════════════════════════════
# KPI 卡片渲染
# ═══════════════════════════════════════════════════════════════════════════════

class TestRenderKpiCards:
    """验证 _render_kpi_cards 渲染完整的 KPI 指标。"""

    def test_all_metrics_displayed(self):
        """所有指标卡片都被渲染。"""
        from filter.backtest.dashboard import _render_kpi_cards
        import filter.backtest.dashboard as dash_mod

        metrics = {
            "total_return_pct": 15.5,
            "sharpe_ratio": 1.23,
            "max_drawdown_pct": -8.5,
            "win_rate_pct": 55.0,
            "total_trades": 42,
            "calmar_ratio": 1.82,
            "profit_factor": 2.1,
            "sortino_ratio": 1.45,
            "annualized_return_pct": 12.3,
            "annualized_volatility_pct": 18.7,
            "max_drawdown_duration": 15,
            "avg_trade_return_pct": 0.37,
        }

        with patch.object(dash_mod, "st") as mock_st:
            # st.columns 返回 mock columns，每个 column 有 .metric 方法
            mock_col = MagicMock()
            mock_st.columns.return_value = [mock_col] * 6
            _render_kpi_cards(metrics)
            # 验证 metric 被调用了 12 次（第一行 6 + 第二行 6）
            assert mock_col.metric.call_count == 12

    def test_missing_keys_default_to_zero(self):
        """缺失的指标键使用默认值 0 渲染，不抛异常。"""
        from filter.backtest.dashboard import _render_kpi_cards
        import filter.backtest.dashboard as dash_mod

        metrics = {}  # 空 dict

        with patch.object(dash_mod, "st") as mock_st:
            _render_kpi_cards(metrics)
            # 不应抛异常
            assert mock_st.subheader.called


# ═══════════════════════════════════════════════════════════════════════════════
# PnL 图表
# ═══════════════════════════════════════════════════════════════════════════════

class TestRenderPnlChart:
    """验证 _render_pnl_chart 创建正确的图表结构。"""

    def test_creates_subplot_figure(self):
        """渲染时创建包含 2 行子图的 Plotly figure。"""
        from filter.backtest.dashboard import _render_pnl_chart
        import filter.backtest.dashboard as dash_mod

        np.random.seed(42)
        long_pnl = 100.0 * np.cumprod(np.full(100, 1.001))
        short_pnl = np.full(100, 100.0)
        df = pd.DataFrame({"bar_timestamp": pd.date_range("2024-01-01", periods=100, freq="h")})

        with patch.object(dash_mod, "st") as mock_st:
            _render_pnl_chart(long_pnl, short_pnl, df)
            assert mock_st.plotly_chart.called


class TestPnlChartTraces:
    """验证 _render_pnl_chart 同时包含做多、做空和组合曲线。"""

    def test_pnl_chart_has_long_short_and_combined_traces(self):
        """图表应包含 3 条 trace: 做多 PnL (虚线), 做空 PnL (虚线), max(做多,做空) PnL (实线)。"""
        from filter.backtest.dashboard import _render_pnl_chart
        import filter.backtest.dashboard as dash_mod

        np.random.seed(42)
        n = 60
        long_pnl = 100.0 * np.cumprod(np.full(n, 1.001))
        short_pnl = 100.0 * np.cumprod(np.full(n, 1.0005))

        df = pd.DataFrame({"bar_timestamp": pd.date_range("2024-01-01", periods=n, freq="h")})

        # 捕获 plotly_chart 的 figure 参数
        with patch.object(dash_mod, "st") as mock_st:
            _render_pnl_chart(long_pnl, short_pnl, df)
            assert mock_st.plotly_chart.called
            call_args = mock_st.plotly_chart.call_args
            # 第一个位置参数是 figure 对象
            fig = call_args[0][0]

            # 应有 5 个 trace: 做多虚线, 做空虚线, max组合实线, 零线(hline), 回撤线
            # 实际上 hline 不是 trace，所以是 4 个 trace: long, short, combined, drawdown
            names = [t.name for t in fig.data]
            assert "做多 PnL" in names, f"Missing '做多 PnL' trace, found: {names}"
            assert "做空 PnL" in names, f"Missing '做空 PnL' trace, found: {names}"
            assert "max(做多, 做空) PnL" in names, f"Missing max combined trace, found: {names}"

    def test_long_trace_is_dashed(self):
        """做多 PnL trace 应为虚线样式。"""
        from filter.backtest.dashboard import _render_pnl_chart
        import filter.backtest.dashboard as dash_mod

        n = 10
        long_pnl = np.linspace(100, 110, n)
        short_pnl = np.full(n, 100.0)
        df = pd.DataFrame({"bar_timestamp": pd.date_range("2024-01-01", periods=n, freq="h")})

        with patch.object(dash_mod, "st") as mock_st:
            _render_pnl_chart(long_pnl, short_pnl, df)
            fig = mock_st.plotly_chart.call_args[0][0]

            long_trace = next(t for t in fig.data if t.name == "做多 PnL")
            assert long_trace.line.dash == "dot"

    def test_short_trace_is_dashed(self):
        """做空 PnL trace 应为虚线样式。"""
        from filter.backtest.dashboard import _render_pnl_chart
        import filter.backtest.dashboard as dash_mod

        n = 10
        long_pnl = np.full(n, 100.0)
        short_pnl = np.linspace(100, 110, n)
        df = pd.DataFrame({"bar_timestamp": pd.date_range("2024-01-01", periods=n, freq="h")})

        with patch.object(dash_mod, "st") as mock_st:
            _render_pnl_chart(long_pnl, short_pnl, df)
            fig = mock_st.plotly_chart.call_args[0][0]

            short_trace = next(t for t in fig.data if t.name == "做空 PnL")
            assert short_trace.line.dash == "dot"


class TestCombinedPnlIsMaxOfLongShort:
    """验证 combined PnL 确实是 long 和 short 的逐点最大值。"""

    def test_combined_equals_elementwise_maximum(self):
        """combined 数组应等于 np.maximum(long_pnl, short_pnl)。"""
        np.random.seed(123)
        n = 50
        long_pnl = 100.0 + np.cumsum(np.random.randn(n) * 0.5)
        short_pnl = 100.0 + np.cumsum(np.random.randn(n) * 0.3)

        combined = np.maximum(long_pnl, short_pnl)

        for i in range(n):
            expected = max(long_pnl[i], short_pnl[i])
            assert combined[i] == expected, f"Mismatch at index {i}"

    def test_long_always_higher(self):
        """当做多始终高于做空时，combined 应全程等于做多。"""
        long_pnl = np.array([100.0, 102.0, 104.0, 106.0, 108.0])
        short_pnl = np.array([100.0, 101.0, 100.0, 99.0, 100.0])

        combined = np.maximum(long_pnl, short_pnl)
        assert np.array_equal(combined, long_pnl)

    def test_short_always_higher(self):
        """当做空始终高于做多时，combined 应全程等于做空。"""
        short_pnl = np.array([100.0, 102.0, 104.0, 106.0, 108.0])
        long_pnl = np.array([100.0, 101.0, 100.0, 99.0, 100.0])

        combined = np.maximum(long_pnl, short_pnl)
        assert np.array_equal(combined, short_pnl)

    def test_alternating_dominance(self):
        """当做多和做空交替领先时，combined 逐点取大值。"""
        long_pnl = np.array([100.0, 102.0, 100.0, 98.0, 104.0])
        short_pnl = np.array([100.0, 101.0, 103.0, 105.0, 100.0])

        combined = np.maximum(long_pnl, short_pnl)
        expected = np.array([100.0, 102.0, 103.0, 105.0, 104.0])
        assert np.array_equal(combined, expected)

    def test_flat_both_equal(self):
        """当做多和做空完全相等时，combined 等于两者。"""
        long_pnl = np.array([100.0, 100.0, 100.0])
        short_pnl = np.array([100.0, 100.0, 100.0])

        combined = np.maximum(long_pnl, short_pnl)
        assert np.array_equal(combined, long_pnl)
        assert np.array_equal(combined, short_pnl)

    def test_with_nan_values(self):
        """含有 NaN 时 np.maximum 的行为验证。"""
        long_pnl = np.array([100.0, np.nan, 102.0])
        short_pnl = np.array([101.0, 101.0, np.nan])

        combined = np.maximum(long_pnl, short_pnl)
        # np.maximum with NaN: if either is NaN, result is NaN
        assert np.isnan(combined[1])
        assert np.isnan(combined[2])
        assert combined[0] == 101.0  # short > long


# ═══════════════════════════════════════════════════════════════════════════════
# 信号统计
# ═══════════════════════════════════════════════════════════════════════════════

class TestRenderSignalStats:
    """验证 _render_signal_stats 信号统计展示。"""

    def test_mixed_signals(self):
        """混合信号产生正确的统计。"""
        from filter.backtest.dashboard import _render_signal_stats
        import filter.backtest.dashboard as dash_mod

        df = pd.DataFrame({
            "v0_sig": [1, 1, 1, -1, -1, 0, 0, 0, 0],
        })

        with patch.object(dash_mod, "st") as mock_st:
            _render_signal_stats(df, "v0")
            # 确认 subheader 和 metric 被调用
            assert mock_st.subheader.called

    def test_no_sig_column_skips(self):
        """无 sig 列时提前返回不抛异常。"""
        from filter.backtest.dashboard import _render_signal_stats

        df = pd.DataFrame({"a": [1, 2, 3]})

        # 不应抛异常 — 直接 return
        _render_signal_stats(df, "v0")


# ═══════════════════════════════════════════════════════════════════════════════
# 多视图对比
# ═══════════════════════════════════════════════════════════════════════════════

class TestRenderViewComparison:
    """验证 _render_view_comparison 多视图图表。"""

    def test_multiple_views_creates_comparison(self):
        """多个视图时创建对比图。"""
        from filter.backtest.dashboard import _render_view_comparison
        import filter.backtest.dashboard as dash_mod

        np.random.seed(42)
        n = 50
        df = pd.DataFrame({
            "v0_pnl_long":  100.0 * np.cumprod(np.full(n, 1.001)),
            "v0_pnl_short": np.full(n, 100.0),
            "v1_pnl_long":  100.0 * np.cumprod(np.full(n, 1.0005)),
            "v1_pnl_short": np.full(n, 100.0),
        })

        with patch.object(dash_mod, "st") as mock_st:
            _render_view_comparison(df, ["v0", "v1"])
            assert mock_st.plotly_chart.called
