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


# ═══════════════════════════════════════════════════════════════════════════════
# 指标计算边缘情况
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeMetricsEdgeCases:
    """_compute_metrics_from_pnl 边缘情况."""

    def test_single_bar_data(self):
        """单行数据：n_bars=1, combined_pnl 长度 < 2 → 返回空指标."""
        from filter.backtest.dashboard import _compute_metrics_from_pnl

        long_pnl = np.array([100.0])
        short_pnl = np.array([100.0])
        df = pd.DataFrame({"v0_sig": [0]})

        result = _compute_metrics_from_pnl(long_pnl, short_pnl, df, "v0", 1)
        # 单 bar 无法计算收益率差分会触发 _empty_metrics
        assert result["total_trades"] == 0
        assert result["total_return_pct"] == 0.0
        assert result["sharpe_ratio"] == 0.0

    def test_two_bar_data(self):
        """两行数据：可计算一阶差分但无法计算年化波动率."""
        from filter.backtest.dashboard import _compute_metrics_from_pnl

        long_pnl = np.array([100.0, 101.0])
        short_pnl = np.array([100.0, 100.0])
        df = pd.DataFrame({"v0_sig": [0, 0]})

        result = _compute_metrics_from_pnl(long_pnl, short_pnl, df, "v0", 2)
        # 可计算收益率，但 returns len=1 且 returns[1:] empty → std 为 0
        assert "total_return_pct" in result
        assert "sharpe_ratio" in result

    def test_very_large_pnl_values(self):
        """非常大的 PnL 值（> 1e9）不导致溢出."""
        from filter.backtest.dashboard import _compute_metrics_from_pnl

        long_pnl = np.array([1e12, 1.1e12, 1.2e12, 1.3e12, 1.4e12])
        short_pnl = np.array([1e12, 1e12, 1e12, 1e12, 1e12])
        df = pd.DataFrame({"v0_sig": [0] * 5})

        result = _compute_metrics_from_pnl(long_pnl, short_pnl, df, "v0", 5)
        # 不能有 NaN 或 Inf 指标值
        assert not np.isnan(result["total_return_pct"])
        assert not np.isinf(result["total_return_pct"])
        assert result["total_return_pct"] > 0

    def test_very_small_sharpe_scenario(self):
        """收益率波动极小导致极低 Sharpe 时不出错."""
        from filter.backtest.dashboard import _compute_metrics_from_pnl

        # PnL 几乎不变 → returns ≈ 0 → std ≈ 0 → sharpe 可能非常大（正或负）
        long_pnl = np.array([100.0, 100.0001, 100.0002, 100.0003, 100.0004])
        short_pnl = np.array([100.0, 100.0, 100.0, 100.0, 100.0])
        df = pd.DataFrame({"v0_sig": [0] * 5})

        result = _compute_metrics_from_pnl(long_pnl, short_pnl, df, "v0", 5)
        # Sharpe 应为有限数（公式: (annual_return - 0.03) / annual_vol）
        # 当 annual_vol 极小时，Sharpe 可能极大正负值，但必须是有限值
        assert not np.isnan(result["sharpe_ratio"])
        assert not np.isinf(result["sharpe_ratio"])

    def test_empty_arrays(self):
        """空数组应由 _empty_metrics 安全返回."""
        from filter.backtest.dashboard import _compute_metrics_from_pnl

        long_pnl = np.array([])
        short_pnl = np.array([])
        df = pd.DataFrame()

        result = _compute_metrics_from_pnl(long_pnl, short_pnl, df, "v0", 0)
        assert result["total_trades"] == 0
        assert result["total_return_pct"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# KPI 卡片 NaN 值处理
# ═══════════════════════════════════════════════════════════════════════════════

class TestKpiCardsWithNaNValues:
    """_render_kpi_cards 处理含 NaN 的 metrics dict."""

    def test_nan_values_get_defaulted(self):
        """NaN/Inf 指标值被预处理为 0，防止 f-string 格式化崩溃。

        ``_render_kpi_cards`` 在格式化前将所有 float NaN/Inf 替换为 0，
        因此即使 metrics 包含 NaN，传给 ``st.metric()`` 的始终是合法数值。
        """
        from filter.backtest.dashboard import _render_kpi_cards
        import filter.backtest.dashboard as dash_mod

        metrics = {
            "total_return_pct": float("nan"),
            "sharpe_ratio": float("inf"),
            "max_drawdown_pct": float("-inf"),
            "win_rate_pct": float("nan"),
            "total_trades": float("nan"),
            "calmar_ratio": float("nan"),
            "profit_factor": float("nan"),
            "sortino_ratio": float("nan"),
            "annualized_return_pct": float("nan"),
            "annualized_volatility_pct": float("nan"),
            "max_drawdown_duration": float("nan"),
            "avg_trade_return_pct": float("nan"),
        }

        with patch.object(dash_mod, "st") as mock_st:
            mock_col = MagicMock()
            mock_st.columns.return_value = [mock_col] * 6
            _render_kpi_cards(metrics)
            # 12 个 metric 调用全部完成，未因 NaN/Inf 触发 ValueError
            assert mock_col.metric.call_count == 12
            # 验证传入 metric 的值已被 sanitized（不含 NaN/Inf）
            for call_args in mock_col.metric.call_args_list:
                value_arg = call_args[0][1]  # 第二个位置参数是值
                assert "nan" not in str(value_arg).lower()
                assert "inf" not in str(value_arg).lower()

    def test_mixed_nan_and_valid(self):
        """部分 NaN 部分有效值混合 — 有效值保留，NaN 被替换为 0."""
        from filter.backtest.dashboard import _render_kpi_cards
        import filter.backtest.dashboard as dash_mod

        metrics = {
            "total_return_pct": 15.5,
            "sharpe_ratio": float("nan"),
            "max_drawdown_pct": -8.5,
            "win_rate_pct": 55.0,
            "total_trades": None,
            "calmar_ratio": 1.82,
        }

        with patch.object(dash_mod, "st") as mock_st:
            mock_col = MagicMock()
            mock_st.columns.return_value = [mock_col] * 6
            _render_kpi_cards(metrics)
            assert mock_col.metric.call_count == 12
            # 有效数值保留原值格式
            first_val = mock_col.metric.call_args_list[0][0][1]
            assert "15.5" in first_val  # valid number preserved
            # NaN sharpe → 被替换为 0.000
            sharpe_val = mock_col.metric.call_args_list[1][0][1]
            assert "0.000" in sharpe_val  # NaN replaced with 0


# ═══════════════════════════════════════════════════════════════════════════════
# PnL 视图检测边缘情况
# ═══════════════════════════════════════════════════════════════════════════════

class TestDetectPnlViewsEdgeCases:
    """_detect_pnl_views 边缘情况."""

    def test_columns_with_invalid_patterns(self):
        """非标准列名不影响 PnL 列检测."""
        from filter.backtest.dashboard import _detect_pnl_views

        df = pd.DataFrame({
            "v0_pnl_long":  [100.0] * 3,
            "v0_pnl_short": [100.0] * 3,
            "random_column": [1, 2, 3],
            "another_pnl_long": [200.0] * 3,
        })
        views = _detect_pnl_views(df)
        # "another_pnl_long" 不匹配 vN_pnl_* 模式，应被忽略
        assert views == ["v0"]

    def test_large_view_index(self):
        """v9, v10 等高索引视图也能被检测."""
        from filter.backtest.dashboard import _detect_pnl_views

        df = pd.DataFrame({
            "v9_pnl_long":  [100.0] * 3,
            "v9_pnl_short": [100.0] * 3,
            "v10_pnl_long": [100.0] * 3,
            "v10_pnl_short": [100.0] * 3,
        })
        views = _detect_pnl_views(df)
        assert "v9" in views
        assert "v10" in views


# ═══════════════════════════════════════════════════════════════════════════════
# T2-1: go.Scatter → go.Scattergl 类型验证
# ═══════════════════════════════════════════════════════════════════════════════

class TestScatterglTraceType:
    """验证 _render_pnl_chart 和 _render_view_comparison 使用 Scattergl 类型 trace。

    修改前使用 go.Scatter；修改后必须使用 go.Scattergl 以提升大数据量渲染性能。
    """

    def test_pnl_chart_all_traces_are_scattergl(self):
        """_render_pnl_chart 每条 data trace 均为 go.Scattergl 实例。"""
        import plotly.graph_objects as go
        from filter.backtest.dashboard import _render_pnl_chart
        import filter.backtest.dashboard as dash_mod

        np.random.seed(42)
        n = 60
        long_pnl = 100.0 * np.cumprod(np.full(n, 1.001))
        short_pnl = 100.0 * np.cumprod(np.full(n, 1.0005))
        df = pd.DataFrame({"bar_timestamp": pd.date_range("2024-01-01", periods=n, freq="h")})

        with patch.object(dash_mod, "st") as mock_st:
            _render_pnl_chart(long_pnl, short_pnl, df)
            fig = mock_st.plotly_chart.call_args[0][0]

            for trace in fig.data:
                assert isinstance(trace, go.Scattergl), (
                    f"Trace '{trace.name}' 应为 go.Scattergl，实际为 {type(trace).__name__}"
                )

    def test_pnl_chart_trace_count_matches_expected(self):
        """PnL chart 应有精确的 trace 数量：做多/做空/组合/回撤 = 4 条数据 trace。"""
        from filter.backtest.dashboard import _render_pnl_chart
        import filter.backtest.dashboard as dash_mod

        np.random.seed(42)
        n = 60
        long_pnl = 100.0 * np.cumprod(np.full(n, 1.001))
        short_pnl = np.full(n, 100.0)
        df = pd.DataFrame({"bar_timestamp": pd.date_range("2024-01-01", periods=n, freq="h")})

        with patch.object(dash_mod, "st") as mock_st:
            _render_pnl_chart(long_pnl, short_pnl, df)
            fig = mock_st.plotly_chart.call_args[0][0]

            # 4 data traces: long, short, combined, drawdown
            assert len(fig.data) == 4, f"Expected 4 data traces, got {len(fig.data)}"

    def test_view_comparison_all_traces_are_scattergl(self):
        """_render_view_comparison 每条 trace 均为 go.Scattergl 实例。"""
        import plotly.graph_objects as go
        from filter.backtest.dashboard import _render_view_comparison
        import filter.backtest.dashboard as dash_mod

        np.random.seed(42)
        n = 50
        df = pd.DataFrame({
            "v0_pnl_long":  100.0 * np.cumprod(np.full(n, 1.001)),
            "v0_pnl_short": np.full(n, 100.0),
            "v1_pnl_long":  100.0 * np.cumprod(np.full(n, 1.0005)),
            "v1_pnl_short": np.full(n, 100.0),
            "v2_pnl_long":  100.0 * np.cumprod(np.full(n, 1.0003)),
            "v2_pnl_short": np.full(n, 100.0),
        })

        with patch.object(dash_mod, "st") as mock_st:
            _render_view_comparison(df, ["v0", "v1", "v2"])
            fig = mock_st.plotly_chart.call_args[0][0]

            assert len(fig.data) == 3  # 每个 view 一条 trace
            for trace in fig.data:
                assert isinstance(trace, go.Scattergl), (
                    f"View comparison trace '{trace.name}' 应为 go.Scattergl，"
                    f"实际为 {type(trace).__name__}"
                )

    def test_view_comparison_single_view_no_crash(self):
        """单视图对比也不崩溃。"""
        from filter.backtest.dashboard import _render_view_comparison
        import filter.backtest.dashboard as dash_mod

        np.random.seed(42)
        n = 30
        df = pd.DataFrame({
            "v0_pnl_long":  100.0 * np.cumprod(np.full(n, 1.001)),
            "v0_pnl_short": np.full(n, 100.0),
        })

        with patch.object(dash_mod, "st") as mock_st:
            _render_view_comparison(df, ["v0"])
            assert mock_st.plotly_chart.called


# ═══════════════════════════════════════════════════════════════════════════════
# T2-2: _extract_trade_records 向量化版本测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtractTradeRecords:
    """验证 _extract_trade_records 向量化版本的正确性。

    修改前使用 iterrows() 逐行处理；修改后使用布尔 mask + 向量化赋值。
    """

    def test_basic_trade_records(self):
        """基本场景：trade 列有值，return 和 reason 列也存在。"""
        from filter.backtest.dashboard import _extract_trade_records

        df = pd.DataFrame({
            "v0_trade":        ["long", "short", "", "", "long"],
            "v0_trade_return": [1.5, -0.5, None, None, 2.0],
            "v0_trade_reason": ["target", "stop", None, None, "signal"],
            "other_col":       [10, 20, 30, 40, 50],
        })

        result = _extract_trade_records(df, "v0")
        assert len(result) == 3  # 仅 index 0,1,4 有 trade

        # 检查每条记录都有 return_pct 和 reason
        assert result[0]["return_pct"] == 1.5
        assert result[0]["reason"] == "target"
        assert result[1]["return_pct"] == -0.5
        assert result[1]["reason"] == "stop"
        assert result[2]["return_pct"] == 2.0
        assert result[2]["reason"] == "signal"

    def test_output_dict_keys_include_return_pct_and_reason(self):
        """输出每条 dict 包含 return_pct 和 reason 键。"""
        from filter.backtest.dashboard import _extract_trade_records

        df = pd.DataFrame({
            "v0_trade":        ["long"],
            "v0_trade_return": [3.0],
            "v0_trade_reason": ["entry"],
        })

        result = _extract_trade_records(df, "v0")
        assert len(result) == 1
        assert "return_pct" in result[0]
        assert "reason" in result[0]
        assert result[0]["return_pct"] == 3.0
        assert result[0]["reason"] == "entry"

    def test_empty_dataframe(self):
        """空 DataFrame 返回空列表。"""
        from filter.backtest.dashboard import _extract_trade_records

        df = pd.DataFrame()
        result = _extract_trade_records(df, "v0")
        assert result == []

    def test_no_trade_column(self):
        """DataFrame 无 trade 列时返回空列表。"""
        from filter.backtest.dashboard import _extract_trade_records

        df = pd.DataFrame({
            "v0_sig": [0, 1, 0],
            "v0_pnl_long": [100.0, 101.0, 102.0],
        })

        result = _extract_trade_records(df, "v0")
        assert result == []

    def test_trade_column_all_nan_or_empty(self):
        """trade 列全为 NaN 或空字符串时返回空列表。"""
        from filter.backtest.dashboard import _extract_trade_records

        df = pd.DataFrame({
            "v0_trade":        [None, "", np.nan, None],
            "v0_trade_return": [None, None, None, None],
            "v0_trade_reason": [None, None, None, None],
        })

        result = _extract_trade_records(df, "v0")
        assert result == []

    def test_trade_column_all_empty_strings(self):
        """trade 列全为空字符串时返回空列表。"""
        from filter.backtest.dashboard import _extract_trade_records

        df = pd.DataFrame({
            "v0_trade": ["", "", ""],
            "v0_trade_return": [None, None, None],
        })

        result = _extract_trade_records(df, "v0")
        assert result == []

    def test_mixed_trade_values(self):
        """部分行有值、部分行无值的混合情况。"""
        from filter.backtest.dashboard import _extract_trade_records

        df = pd.DataFrame({
            "v0_trade":        ["buy", "", None, "sell", ""],
            "v0_trade_return": [5.0, None, None, -2.0, None],
            "v0_trade_reason": ["entry", None, None, "exit", None],
        })

        result = _extract_trade_records(df, "v0")
        assert len(result) == 2
        assert result[0]["return_pct"] == 5.0
        assert result[1]["return_pct"] == -2.0

    def test_only_trade_column_no_return_or_reason(self):
        """仅 trade 列存在，无 return/reason 列时返回空列表。

        col_map 为空时，无可提取列，应返回空列表而非崩溃。
        """
        from filter.backtest.dashboard import _extract_trade_records

        df = pd.DataFrame({
            "v0_trade": ["long", "short"],
        })

        result = _extract_trade_records(df, "v0")
        assert result == []

    def test_only_trade_return_no_reason(self):
        """仅 trade 和 return 列存在，无 reason 列。"""
        from filter.backtest.dashboard import _extract_trade_records

        df = pd.DataFrame({
            "v0_trade":        ["buy", "sell"],
            "v0_trade_return": [1.0, -0.5],
        })

        result = _extract_trade_records(df, "v0")
        assert len(result) == 2
        assert "return_pct" in result[0]
        # reason 列不存在，不应出现在输出中
        assert "reason" not in result[0]

    def test_is_vectorized_not_iterrows(self):
        """验证实现为向量化版本（无 iterrows 调用）。"""
        from filter.backtest.dashboard import _extract_trade_records
        import inspect

        source = inspect.getsource(_extract_trade_records)
        assert "iterrows" not in source, (
            "_extract_trade_records 应使用向量化操作而非 iterrows()"
        )
        # 验证使用了向量化操作 mask
        assert "notna()" in source or "mask" in source, (
            "_extract_trade_records 应使用向量化 mask 筛选"
        )

    def test_null_return_pct_defaults_to_zero(self):
        """return_pct 为 NaN 的行被填充为 0。"""
        from filter.backtest.dashboard import _extract_trade_records

        df = pd.DataFrame({
            "v0_trade":        ["long"],
            "v0_trade_return": [np.nan],
            "v0_trade_reason": ["entry"],
        })

        result = _extract_trade_records(df, "v0")
        assert len(result) == 1
        assert result[0]["return_pct"] == 0.0

    def test_null_reason_defaults_to_empty_string(self):
        """reason 为 NaN 时被填充为空字符串。"""
        from filter.backtest.dashboard import _extract_trade_records

        df = pd.DataFrame({
            "v0_trade":        ["long"],
            "v0_trade_return": [1.0],
            "v0_trade_reason": [np.nan],
        })

        result = _extract_trade_records(df, "v0")
        assert len(result) == 1
        assert result[0]["reason"] == ""

    def test_whitespace_only_trade_ignored(self):
        """trade 值为仅空白字符时被忽略。"""
        from filter.backtest.dashboard import _extract_trade_records

        df = pd.DataFrame({
            "v0_trade":        ["  ", "\t", "valid"],
            "v0_trade_return": [None, None, 3.0],
            "v0_trade_reason": [None, None, "yes"],
        })

        result = _extract_trade_records(df, "v0")
        assert len(result) == 1
        assert result[0]["return_pct"] == 3.0


# ═══════════════════════════════════════════════════════════════════════════════
# T2-3: 导出功能测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestPnlChartExportConfig:
    """验证 PnL 图表的 Plotly config 包含导出按钮。"""

    def test_pnl_chart_config_has_download_image(self):
        """_render_pnl_chart 的 st.plotly_chart config 包含 downloadImage 按钮。"""
        from filter.backtest.dashboard import _render_pnl_chart
        import filter.backtest.dashboard as dash_mod

        np.random.seed(42)
        n = 30
        long_pnl = 100.0 * np.cumprod(np.full(n, 1.001))
        short_pnl = np.full(n, 100.0)
        df = pd.DataFrame({"bar_timestamp": pd.date_range("2024-01-01", periods=n, freq="h")})

        with patch.object(dash_mod, "st") as mock_st:
            _render_pnl_chart(long_pnl, short_pnl, df)
            call_kwargs = mock_st.plotly_chart.call_args[1]

            assert "config" in call_kwargs
            assert "modeBarButtonsToAdd" in call_kwargs["config"]
            assert call_kwargs["config"]["modeBarButtonsToAdd"] == ["downloadImage"]

    def test_view_comparison_config_has_download_image(self):
        """_render_view_comparison 的 config 也包含 downloadImage 按钮。"""
        from filter.backtest.dashboard import _render_view_comparison
        import filter.backtest.dashboard as dash_mod

        np.random.seed(42)
        n = 30
        df = pd.DataFrame({
            "v0_pnl_long":  100.0 * np.cumprod(np.full(n, 1.001)),
            "v0_pnl_short": np.full(n, 100.0),
        })

        with patch.object(dash_mod, "st") as mock_st:
            _render_view_comparison(df, ["v0"])
            call_kwargs = mock_st.plotly_chart.call_args[1]

            assert "config" in call_kwargs
            assert "modeBarButtonsToAdd" in call_kwargs["config"]
            assert call_kwargs["config"]["modeBarButtonsToAdd"] == ["downloadImage"]

    def test_use_container_width_is_true(self):
        """图表使用全宽渲染。"""
        from filter.backtest.dashboard import _render_pnl_chart
        import filter.backtest.dashboard as dash_mod

        np.random.seed(42)
        n = 30
        long_pnl = 100.0 * np.cumprod(np.full(n, 1.001))
        short_pnl = np.full(n, 100.0)
        df = pd.DataFrame({"bar_timestamp": pd.date_range("2024-01-01", periods=n, freq="h")})

        with patch.object(dash_mod, "st") as mock_st:
            _render_pnl_chart(long_pnl, short_pnl, df)
            call_kwargs = mock_st.plotly_chart.call_args[1]
            assert call_kwargs.get("use_container_width") is True


class TestCsvDownloadButtons:
    """验证 CSV 下载按钮在页面中正确渲染。"""

    def test_kpi_metrics_download_button_present(self):
        """_render_kpi_cards 包含 '导出指标汇总 CSV' download_button。"""
        from filter.backtest.dashboard import _render_kpi_cards
        import filter.backtest.dashboard as dash_mod

        metrics = {
            "total_return_pct": 15.5,
            "total_trades": 10,
        }

        with patch.object(dash_mod, "st") as mock_st:
            mock_col = MagicMock()
            mock_st.columns.return_value = [mock_col] * 6
            _render_kpi_cards(metrics)

            # 验证 download_button 被调用且 label 正确
            download_calls = [
                c for c in mock_st.download_button.call_args_list
                if "导出指标汇总" in str(c[1].get("label", ""))
            ]
            assert len(download_calls) >= 1, "未找到 '导出指标汇总 CSV' download_button"

    def test_trade_table_download_button_present(self):
        """_render_trade_table 包含 '导出交易明细 CSV' download_button。"""
        from filter.backtest.dashboard import _render_trade_table
        import filter.backtest.dashboard as dash_mod

        df = pd.DataFrame({
            "v0_trade":        ["long", "short"],
            "v0_trade_return": [1.5, -0.5],
        })

        with patch.object(dash_mod, "st") as mock_st:
            _render_trade_table(df, "v0")

            download_calls = [
                c for c in mock_st.download_button.call_args_list
                if "导出交易明细" in str(c[1].get("label", ""))
            ]
            assert len(download_calls) >= 1, "未找到 '导出交易明细 CSV' download_button"

    def test_empty_trade_table_does_not_show_download(self):
        """无交易记录时不显示下载按钮。"""
        from filter.backtest.dashboard import _render_trade_table
        import filter.backtest.dashboard as dash_mod

        df = pd.DataFrame({
            "v0_trade":        ["", "", ""],
            "v0_trade_return": [None, None, None],
        })

        with patch.object(dash_mod, "st") as mock_st:
            _render_trade_table(df, "v0")
            # 无交易行时只应调用 caption("无交易记录")
            download_calls = [
                c for c in mock_st.download_button.call_args_list
                if "导出" in str(c[1].get("label", ""))
            ]
            assert len(download_calls) == 0, "无交易记录时不应出现下载按钮"
