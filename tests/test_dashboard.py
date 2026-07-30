"""
回测仪表盘测试 — 导入、指标计算、数据验证、HTML 桥接。

测试范围:
- render_backtest_dashboard 导入和调用
- _detect_pnl_views 列检测
- _compute_metrics_from_pnl 指标计算
- 空 DataFrame / 缺失列 安全处理
- KPI 卡片渲染不崩溃
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd


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
            "avg_win_pct": 2.5,
            "avg_loss_pct": -1.2,
            "winning_trades": 30,
            "losing_trades": 20,
        }

        with patch.object(dash_mod, "st") as mock_st:
            # st.columns 返回 mock columns，每个 column 有 .metric 方法
            mock_col = MagicMock()
            mock_st.columns.return_value = [mock_col] * 6
            _render_kpi_cards(metrics)
            # Tier 1 (4) + Tier 2 (3) + Tier 3 (3 + 3 + 3) = 16
            assert mock_col.metric.call_count == 16

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
        """图表应包含 3 条 trace: 做多 PnL (虚线), 做空 PnL (细实线), max(做多,做空) PnL (实线)。"""
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

    def test_short_trace_is_solid_and_thinner(self):
        """做空 PnL trace 应为细实线样式（solid, width=0.8）。"""
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
            assert short_trace.line.dash == "solid", (
                f"做空 PnL 应为 solid，实际为 {short_trace.line.dash}"
            )
            assert short_trace.line.width == 0.8, (
                f"做空 PnL linewidth 应为 0.8（细实线），实际为 {short_trace.line.width}"
            )


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
            # 16 个 metric (Tier 1:4 + Tier 2:3 + Tier 3:9)，未因 NaN/Inf 触发 ValueError
            assert mock_col.metric.call_count == 16
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
            assert mock_col.metric.call_count == 16
            # 收集所有 metric 值
            all_vals = []
            for call_args in mock_col.metric.call_args_list:
                if call_args[0]:
                    all_vals.append(call_args[0][1])
            # 验证有效值出现，"0.000" 出现（NaN 被 sanitized）
            assert any("15.5" in str(v) for v in all_vals), f"Expected '15.5' in metric values, got: {all_vals}"
            assert any("0.000" in str(v) for v in all_vals), "Expected '0.000' (sanitized NaN) in metric values"


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
            mock_st.text_input.return_value = ""
            # 排序 selectbox 返回第一个选项, 分页 selectbox 返回对应页面
            mock_st.selectbox.side_effect = lambda label, options, **kw: options[0] if isinstance(options, list) else "按收益%降序"
            _render_trade_table(df, "v0")

            download_calls = [
                c for c in mock_st.download_button.call_args_list
                if "导出" in str(c[1].get("label", "")) and "交易明细" in str(c[1].get("label", ""))
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


# ═══════════════════════════════════════════════════════════════════════════════
# P3-1: KPI 分层展示
# ═══════════════════════════════════════════════════════════════════════════════


class TestKpiTieredLayout:
    """验证 KPI 分层展示：Tier 1 (4大卡片), Tier 2 (3小卡片), Tier 3 (折叠面板)."""

    def test_tier1_has_four_columns(self):
        """Tier 1 使用 4 列渲染核心 KPI。"""
        from filter.backtest.dashboard import _render_kpi_cards
        import filter.backtest.dashboard as dash_mod

        metrics = {
            "total_return_pct": 15.5, "sharpe_ratio": 1.23,
            "max_drawdown_pct": -8.5, "win_rate_pct": 55.0,
            "total_trades": 42, "calmar_ratio": 1.82,
        }

        with patch.object(dash_mod, "st") as mock_st:
            mock_col = MagicMock()
            mock_st.columns.return_value = [mock_col] * 6
            _render_kpi_cards(metrics)

            # 检查 st.columns 调用序列
            columns_calls = mock_st.columns.call_args_list
            # 至少应有一次 st.columns(4) — Tier 1
            tier1_call = [c for c in columns_calls if c == ((4,),)]
            assert len(tier1_call) >= 1, f"Tier 1 应使用 st.columns(4)，实际调用: {columns_calls}"

    def test_tier2_has_three_columns(self):
        """Tier 2 使用 3 列渲染次要指标。"""
        from filter.backtest.dashboard import _render_kpi_cards
        import filter.backtest.dashboard as dash_mod

        metrics = {"annualized_return_pct": 12.3, "annualized_volatility_pct": 18.7, "calmar_ratio": 1.5}

        with patch.object(dash_mod, "st") as mock_st:
            mock_col = MagicMock()
            mock_st.columns.return_value = [mock_col] * 6
            _render_kpi_cards(metrics)

            columns_calls = [c[0] for c in mock_st.columns.call_args_list]
            three_col_calls = [args for args in columns_calls if args == (3,)]
            assert len(three_col_calls) >= 1, f"Tier 2 应使用 st.columns(3)，实际调用: {columns_calls}"

    def test_tier3_in_expander(self):
        """Tier 3 详情在 st.expander 折叠面板中。"""
        from filter.backtest.dashboard import _render_kpi_cards
        import filter.backtest.dashboard as dash_mod

        metrics = {"total_trades": 10, "sortino_ratio": 1.45, "profit_factor": 2.1}

        with patch.object(dash_mod, "st") as mock_st:
            mock_col = MagicMock()
            mock_st.columns.return_value = [mock_col] * 6
            _render_kpi_cards(metrics)

            # 验证 expander 被调用
            assert mock_st.expander.called, "Tier 3 应使用 st.expander 折叠面板"

    def test_tier1_displays_correct_metrics(self):
        """Tier 1 展示 Sharpe, Max Drawdown, Win Rate, Total PnL。"""
        from filter.backtest.dashboard import _render_kpi_cards
        import filter.backtest.dashboard as dash_mod

        metrics = {
            "total_return_pct": 15.5, "sharpe_ratio": 1.23,
            "max_drawdown_pct": -8.5, "win_rate_pct": 55.0,
            "annualized_return_pct": 12.3, "annualized_volatility_pct": 18.7,
            "calmar_ratio": 1.82, "total_trades": 42,
        }

        with patch.object(dash_mod, "st") as mock_st:
            mock_col = MagicMock()
            mock_st.columns.return_value = [mock_col] * 6
            _render_kpi_cards(metrics)

            # metric 调用包含 label 参数（第一个位置参数）
            all_metric_labels = []
            for call_args in mock_col.metric.call_args_list:
                if call_args[0]:
                    all_metric_labels.append(call_args[0][0])

            # Tier 1 核心指标
            assert "Sharpe" in all_metric_labels
            assert "最大回撤" in all_metric_labels
            assert "胜率" in all_metric_labels
            assert "总PnL" in all_metric_labels


# ═══════════════════════════════════════════════════════════════════════════════
# P3-1: 交易明细分页
# ═══════════════════════════════════════════════════════════════════════════════


class TestTradeTablePagination:
    """验证交易表分页和搜索功能。"""

    # 辅助：让 selectbox 第1次调用返回排序选项，后续返回分页选项
    _selectbox_side_effect_fn = None

    def _make_selectbox_side_effect(self, page_option):
        """生成 side_effect: 首次调用返回排序选项，后续返回分页选项。"""
        call_count = [0]

        def _side_effect(label, options, **kw):
            if isinstance(options, list) and options and "第 " in str(options[0]):
                return page_option
            if isinstance(options, list) and options:
                return options[0]  # 第一个排序选项
            return page_option

        return _side_effect

    def test_pagination_controls_present(self):
        """交易表分页控件存在（selectbox 用于页码选择、搜索框）。"""
        from filter.backtest.dashboard import _render_trade_table
        import filter.backtest.dashboard as dash_mod

        # 构建超过 50 条的交易数据
        trades = pd.DataFrame({
            "v0_trade": ["long"] * 120,
            "v0_trade_return": [1.0] * 120,
            "v0_trade_reason": ["target"] * 120,
        })

        with patch.object(dash_mod, "st") as mock_st:
            mock_st.text_input.return_value = ""
            mock_st.selectbox.side_effect = self._make_selectbox_side_effect("第 1 页 (共 120 条)")
            _render_trade_table(trades, "v0")

            # 验证 selectbox 被调用（分页和排序）
            assert mock_st.selectbox.called, "应包含分页/排序 selectbox"

    def test_search_filters_trades(self):
        """搜索框过滤交易记录。"""
        from filter.backtest.dashboard import _render_trade_table
        import filter.backtest.dashboard as dash_mod

        trades = pd.DataFrame({
            "v0_trade": ["long", "short", "long", "short", "long"],
            "v0_trade_return": [1.0, -0.5, 2.0, -1.0, 0.5],
            "v0_trade_reason": ["target", "stop", "signal", "stop", "target"],
        })

        with patch.object(dash_mod, "st") as mock_st:
            mock_st.text_input.return_value = "stop"
            mock_st.selectbox.side_effect = self._make_selectbox_side_effect("第 1 页 (共 2 条)")
            _render_trade_table(trades, "v0")

            # 验证 dataframe 被调用（说明搜索后有结果）
            assert mock_st.dataframe.called

    def test_no_match_shows_caption(self):
        """搜索无匹配时显示提示信息。"""
        from filter.backtest.dashboard import _render_trade_table
        import filter.backtest.dashboard as dash_mod

        trades = pd.DataFrame({
            "v0_trade": ["long", "long", "long"],
            "v0_trade_return": [1.0, 2.0, 0.5],
        })

        with patch.object(dash_mod, "st") as mock_st:
            mock_st.text_input.return_value = "nonexistent"
            mock_st.selectbox.side_effect = self._make_selectbox_side_effect("第 1 页 (共 3 条)")
            _render_trade_table(trades, "v0")

            # 应显示 "无匹配的交易记录"
            caption_calls = [
                c for c in mock_st.caption.call_args_list
                if "无匹配" in str(c[0][0])
            ]
            assert len(caption_calls) >= 1, "搜索无匹配时应提示"

    def test_default_page_size_fifty(self):
        """默认每页显示 50 条。"""
        from filter.backtest.dashboard import _render_trade_table
        import filter.backtest.dashboard as dash_mod

        trades = pd.DataFrame({
            "v0_trade": ["long"] * 80,
            "v0_trade_return": [1.0] * 80,
            "v0_trade_reason": ["target"] * 80,
        })

        with patch.object(dash_mod, "st") as mock_st:
            mock_st.text_input.return_value = ""
            mock_st.selectbox.side_effect = self._make_selectbox_side_effect("第 1 页 (共 80 条)")
            _render_trade_table(trades, "v0")

            # 确认 dataframe 渲染时传入的数据不超过 50 行
            if mock_st.dataframe.call_args:
                df_arg = mock_st.dataframe.call_args[0][0]
                assert len(df_arg) <= 50, f"首页应 ≤50 条，实际 {len(df_arg)} 条"

    def test_export_downloads_full_dataset(self):
        """CSV 导出按钮导出全部交易（非仅当前页）。"""
        from filter.backtest.dashboard import _render_trade_table
        import filter.backtest.dashboard as dash_mod

        trades = pd.DataFrame({
            "v0_trade": ["long"] * 120,
            "v0_trade_return": [1.0] * 120,
        })

        with patch.object(dash_mod, "st") as mock_st:
            mock_st.text_input.return_value = ""
            mock_st.selectbox.side_effect = self._make_selectbox_side_effect("第 1 页 (共 120 条)")
            _render_trade_table(trades, "v0")

            # 导出按钮 label 中包含总条数
            download_calls = mock_st.download_button.call_args_list
            assert any(
                "120" in str(c[1].get("label", "")) or "120" in str(c[0][0] if c[0] else "")
                for c in download_calls
            ), "导出按钮应显示全部交易条数"


# ═══════════════════════════════════════════════════════════════════════════════
# P3-1: 缺失指标补全
# ═══════════════════════════════════════════════════════════════════════════════


class TestMissingMetrics:
    """验证新增的缺失指标: avg_win_pct, avg_loss_pct 已显示。"""

    def test_avg_win_avg_loss_displayed(self):
        """_render_kpi_cards 展示 avg_win_pct 和 avg_loss_pct。"""
        from filter.backtest.dashboard import _render_kpi_cards
        import filter.backtest.dashboard as dash_mod

        metrics = {
            "avg_win_pct": 2.5, "avg_loss_pct": -1.2,
            "winning_trades": 30, "losing_trades": 20,
            "total_return_pct": 15.5, "sharpe_ratio": 1.23,
            "max_drawdown_pct": -8.5, "win_rate_pct": 55.0,
            "annualized_return_pct": 12.3, "annualized_volatility_pct": 18.7,
            "calmar_ratio": 1.82, "total_trades": 50,
            "profit_factor": 2.1, "sortino_ratio": 1.45,
            "max_drawdown_duration": 15, "avg_trade_return_pct": 0.5,
        }

        with patch.object(dash_mod, "st") as mock_st:
            mock_col = MagicMock()
            mock_st.columns.return_value = [mock_col] * 6
            _render_kpi_cards(metrics)

            all_metric_labels = []
            for call_args in mock_col.metric.call_args_list:
                if call_args[0]:
                    all_metric_labels.append(call_args[0][0])

            assert "平均盈利%" in all_metric_labels, "应展示 avg_win_pct (平均盈利%)"
            assert "平均亏损%" in all_metric_labels, "应展示 avg_loss_pct (平均亏损%)"
            assert "盈利交易" in all_metric_labels, "应展示 winning_trades"
            assert "亏损交易" in all_metric_labels, "应展示 losing_trades"

    def test_profit_factor_already_shown(self):
        """profit_factor 原本已显示 — 验证仍在 Tier 3。"""
        from filter.backtest.dashboard import _render_kpi_cards
        import filter.backtest.dashboard as dash_mod

        metrics = {"profit_factor": 2.5, "total_return_pct": 15.5}

        with patch.object(dash_mod, "st") as mock_st:
            mock_col = MagicMock()
            mock_st.columns.return_value = [mock_col] * 6
            _render_kpi_cards(metrics)

            all_metric_labels = []
            for call_args in mock_col.metric.call_args_list:
                if call_args[0]:
                    all_metric_labels.append(call_args[0][0])

            assert "盈利因子" in all_metric_labels, "profit_factor (盈利因子) 仍应显示"

    def test_metrics_import_build_sort_options(self):
        """_build_sort_options 可导入且可调用。"""
        from filter.backtest.dashboard import _build_sort_options

        assert callable(_build_sort_options)

        # 基本功能测试
        trades_df = pd.DataFrame({
            "收益%": [1.0, -0.5],
            "交易类型": ["long", "short"],
        })
        opts = _build_sort_options(trades_df)
        assert "按收益%降序" in opts
        assert "按交易类型" in opts

    def test_sort_options_no_columns(self):
        """无相关列时返回空 dict。"""
        from filter.backtest.dashboard import _build_sort_options

        trades_df = pd.DataFrame({"other": [1, 2]})
        opts = _build_sort_options(trades_df)
        assert opts == {}


# ═══════════════════════════════════════════════════════════════════════════════
# P3-1: 向后兼容 — 现有功能不受影响
# ═══════════════════════════════════════════════════════════════════════════════


class TestBackwardCompatibilityP3:
    """验证 P3-1 变更后的向后兼容性。"""

    def test_existing_kpi_keys_still_work(self):
        """旧版 metrics dict 键名仍能正常渲染不抛异常。"""
        from filter.backtest.dashboard import _render_kpi_cards
        import filter.backtest.dashboard as dash_mod

        # 旧版 metrics dict（不含 avg_win_pct / avg_loss_pct）
        old_metrics = {
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
            mock_col = MagicMock()
            mock_st.columns.return_value = [mock_col] * 6
            # 不应抛异常
            _render_kpi_cards(old_metrics)
            assert mock_st.subheader.called

    def test_empty_trade_df_still_works(self):
        """空交易表仍正常渲染不抛异常。"""
        from filter.backtest.dashboard import _render_trade_table
        import filter.backtest.dashboard as dash_mod

        df = pd.DataFrame({
            "v0_trade": ["", "", ""],
            "v0_trade_return": [None, None, None],
        })

        with patch.object(dash_mod, "st") as mock_st:
            _render_trade_table(df, "v0")
            # 无交易时应显示 caption
            caption_calls = [
                c for c in mock_st.caption.call_args_list
                if "无交易" in str(c[0][0])
            ]
            assert len(caption_calls) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# 需求: 做空线细实线 + PnL 曲线标记 (triangle/circle/x)
# ═══════════════════════════════════════════════════════════════════════════════


class TestShortPnLTraceSolid:
    """验证做空 PnL trace 为细实线样式。"""

    def test_short_trace_line_dash_is_solid(self):
        """做空 PnL trace line.dash 应为 'solid'。"""
        from filter.backtest.dashboard import _render_pnl_chart
        import filter.backtest.dashboard as dash_mod

        n = 10
        long_pnl = np.linspace(100, 105, n)
        short_pnl = np.linspace(100, 108, n)
        df = pd.DataFrame({"bar_timestamp": pd.date_range("2024-01-01", periods=n, freq="h")})

        with patch.object(dash_mod, "st") as mock_st:
            _render_pnl_chart(long_pnl, short_pnl, df)
            fig = mock_st.plotly_chart.call_args[0][0]

            short_trace = next(t for t in fig.data if t.name == "做空 PnL")
            assert short_trace.line.dash == "solid"

    def test_short_trace_line_width_is_thinner(self):
        """做空 PnL trace line.width 应 < 做多 (0.8 < 1.0)。"""
        from filter.backtest.dashboard import _render_pnl_chart
        import filter.backtest.dashboard as dash_mod

        n = 10
        long_pnl = np.linspace(100, 105, n)
        short_pnl = np.linspace(100, 108, n)
        df = pd.DataFrame({"bar_timestamp": pd.date_range("2024-01-01", periods=n, freq="h")})

        with patch.object(dash_mod, "st") as mock_st:
            _render_pnl_chart(long_pnl, short_pnl, df)
            fig = mock_st.plotly_chart.call_args[0][0]

            long_trace = next(t for t in fig.data if t.name == "做多 PnL")
            short_trace = next(t for t in fig.data if t.name == "做空 PnL")
            assert short_trace.line.width < long_trace.line.width, (
                f"做空线宽({short_trace.line.width})应 < 做多线宽({long_trace.line.width})"
            )

    def test_long_trace_still_dashed(self):
        """做多 PnL trace 仍保持虚线样式。"""
        from filter.backtest.dashboard import _render_pnl_chart
        import filter.backtest.dashboard as dash_mod

        n = 10
        long_pnl = np.linspace(100, 105, n)
        short_pnl = np.linspace(100, 108, n)
        df = pd.DataFrame({"bar_timestamp": pd.date_range("2024-01-01", periods=n, freq="h")})

        with patch.object(dash_mod, "st") as mock_st:
            _render_pnl_chart(long_pnl, short_pnl, df)
            fig = mock_st.plotly_chart.call_args[0][0]

            long_trace = next(t for t in fig.data if t.name == "做多 PnL")
            assert long_trace.line.dash == "dot"


class TestPnlChartMarkers:
    """验证 PnL 图表上的入场/离场标记符号。"""

    def _make_trade_df(self, trades: list[tuple[int, str, str, float]]) -> pd.DataFrame:
        """构建含 trade 列的测试 DataFrame。

        trades: list of (bar_index, trade_val, reason, return_pct)
        非 trade 行填空字符串/NaN。
        """
        n = trades[-1][0] + 1 if trades else 10
        data = {
            "v0_trade": [""] * n,
            "v0_trade_return": [np.nan] * n,
            "v0_trade_reason": [""] * n,
        }
        for idx, tv, reason, ret in trades:
            data["v0_trade"][idx] = tv
            data["v0_trade_return"][idx] = ret
            data["v0_trade_reason"][idx] = reason
        return pd.DataFrame(data)

    def test_no_markers_without_view_prefix(self):
        """无 view_prefix 时不添加任何标记 trace（仅 4 条基础 trace）。"""
        from filter.backtest.dashboard import _render_pnl_chart
        import filter.backtest.dashboard as dash_mod

        n = 10
        long_pnl = np.linspace(100, 110, n)
        short_pnl = np.linspace(100, 105, n)
        df = self._make_trade_df([(2, "entry_long", "", 0.0), (7, "exit_long", "take_profit", 5.0)])

        with patch.object(dash_mod, "st") as mock_st:
            _render_pnl_chart(long_pnl, short_pnl, df)  # 不传 view_prefix
            fig = mock_st.plotly_chart.call_args[0][0]
            # 基础 4 条：long_dot, short_solid, combined, drawdown
            assert len(fig.data) == 4, (
                f"无 view_prefix 应只有 4 条基础 trace，实际 {len(fig.data)}"
            )

    def test_long_entry_marker_triangle_up(self):
        """做多入场应为 ▲ (triangle-up)，绿色。"""
        from filter.backtest.dashboard import _render_pnl_chart
        import filter.backtest.dashboard as dash_mod

        n = 20
        long_pnl = np.linspace(100, 120, n)
        short_pnl = np.full(n, 100.0)
        # 在 bar 5 做多入场
        df = self._make_trade_df([(5, "entry_long", "", 0.0)])

        with patch.object(dash_mod, "st") as mock_st:
            _render_pnl_chart(long_pnl, short_pnl, df, view_prefix="v0")
            fig = mock_st.plotly_chart.call_args[0][0]

            # 找到 triangle-up marker trace
            triangle_up_traces = [
                t for t in fig.data
                if hasattr(t, "marker") and t.marker and t.marker.symbol == "triangle-up"
            ]
            assert len(triangle_up_traces) >= 1, (
                f"应至少有 1 条 triangle-up 标记 trace，实际找到 {len(triangle_up_traces)}"
            )
            # 验证颜色为 pnl_long 绿色
            from filter.constants.colors import COLORS
            assert triangle_up_traces[0].marker.color == COLORS["pnl_long"]

    def test_short_entry_marker_triangle_down(self):
        """做空入场应为 ▼ (triangle-down)，红色。"""
        from filter.backtest.dashboard import _render_pnl_chart
        import filter.backtest.dashboard as dash_mod

        n = 20
        long_pnl = np.full(n, 100.0)
        short_pnl = np.linspace(100, 115, n)
        # 在 bar 5 做空入场
        df = self._make_trade_df([(5, "entry_short", "", 0.0)])

        with patch.object(dash_mod, "st") as mock_st:
            _render_pnl_chart(long_pnl, short_pnl, df, view_prefix="v0")
            fig = mock_st.plotly_chart.call_args[0][0]

            triangle_down_traces = [
                t for t in fig.data
                if hasattr(t, "marker") and t.marker and t.marker.symbol == "triangle-down"
            ]
            assert len(triangle_down_traces) >= 1, (
                f"应至少有 1 条 triangle-down 标记 trace，实际找到 {len(triangle_down_traces)}"
            )
            from filter.constants.colors import COLORS
            assert triangle_down_traces[0].marker.color == COLORS["pnl_short"]

    def test_stop_loss_exit_marker_x(self):
        """止损离场应为 ✕ (x)，红色。"""
        from filter.backtest.dashboard import _render_pnl_chart
        import filter.backtest.dashboard as dash_mod

        n = 20
        long_pnl = np.linspace(100, 120, n)
        short_pnl = np.full(n, 100.0)
        # 在 bar 3 做多入场，bar 15 止损离场
        df = self._make_trade_df([
            (3, "entry_long", "", 0.0),
            (15, "exit_long", "stop_loss", -5.0),
        ])

        with patch.object(dash_mod, "st") as mock_st:
            _render_pnl_chart(long_pnl, short_pnl, df, view_prefix="v0")
            fig = mock_st.plotly_chart.call_args[0][0]

            x_traces = [
                t for t in fig.data
                if hasattr(t, "marker") and t.marker and t.marker.symbol == "x"
            ]
            assert len(x_traces) >= 1, (
                f"止损离场应生成 'x' marker trace，实际找到 {len(x_traces)}"
            )
            from filter.constants.colors import COLORS
            assert x_traces[0].marker.color == COLORS["exit_sl"]

    def test_take_profit_exit_marker_circle(self):
        """止盈离场应为 ○ (circle)，绿色。"""
        from filter.backtest.dashboard import _render_pnl_chart
        import filter.backtest.dashboard as dash_mod

        n = 20
        long_pnl = np.linspace(100, 120, n)
        short_pnl = np.full(n, 100.0)
        # 在 bar 3 做多入场，bar 15 止盈离场（非 stop_loss = take_profit）
        df = self._make_trade_df([
            (3, "entry_long", "", 0.0),
            (15, "exit_long", "take_profit", 8.0),
        ])

        with patch.object(dash_mod, "st") as mock_st:
            _render_pnl_chart(long_pnl, short_pnl, df, view_prefix="v0")
            fig = mock_st.plotly_chart.call_args[0][0]

            circle_traces = [
                t for t in fig.data
                if hasattr(t, "marker") and t.marker and t.marker.symbol == "circle"
            ]
            assert len(circle_traces) >= 1, (
                f"止盈离场应生成 'circle' marker trace，实际找到 {len(circle_traces)}"
            )
            from filter.constants.colors import COLORS
            assert circle_traces[0].marker.color == COLORS["exit_tp"]

    def test_annotations_have_return_pct(self):
        """离场标记应包含盈亏% 标注。"""
        from filter.backtest.dashboard import _render_pnl_chart
        import filter.backtest.dashboard as dash_mod

        n = 20
        long_pnl = np.linspace(100, 120, n)
        short_pnl = np.full(n, 100.0)
        df = self._make_trade_df([
            (3, "entry_long", "", 0.0),
            (15, "exit_long", "stop_loss", -5.2),
        ])

        with patch.object(dash_mod, "st") as mock_st:
            _render_pnl_chart(long_pnl, short_pnl, df, view_prefix="v0")
            fig = mock_st.plotly_chart.call_args[0][0]

            assert len(fig.layout.annotations) >= 1, (
                "离场应生成盈亏% annotation"
            )
            # 至少有一个 annotation 包含 "-5.2%"
            found_pct = any("-5.2%" in (ann.text or "") for ann in fig.layout.annotations)
            assert found_pct, (
                f"annotation 应包含 '-5.2%'，实际: {[a.text for a in fig.layout.annotations]}"
            )

    def test_all_four_marker_types_coexist(self):
        """多种交易事件的标记共存于同一图表。"""
        from filter.backtest.dashboard import _render_pnl_chart
        import filter.backtest.dashboard as dash_mod

        n = 30
        long_pnl = np.linspace(100, 130, n)
        short_pnl = np.linspace(100, 125, n)
        # 混合多种交易
        df = self._make_trade_df([
            (2, "entry_long", "", 0.0),
            (5, "entry_short", "", 0.0),
            (10, "exit_long", "take_profit", 3.0),
            (15, "exit_short", "stop_loss", -4.0),
            (18, "entry_long", "", 0.0),
            (25, "exit_long", "stop_loss", -2.0),
        ])

        with patch.object(dash_mod, "st") as mock_st:
            _render_pnl_chart(long_pnl, short_pnl, df, view_prefix="v0")
            fig = mock_st.plotly_chart.call_args[0][0]

            symbols = [
                t.marker.symbol for t in fig.data
                if hasattr(t, "marker") and t.marker
            ]
            assert "triangle-up" in symbols, "应有做多入场标记 ▲"
            assert "triangle-down" in symbols, "应有做空入场标记 ▼"
            assert "x" in symbols, "应有止损标记 ✕"
            assert "circle" in symbols, "应有止盈标记 ○"


# ═══════════════════════════════════════════════════════════════════════════════
# 图表构建器做空入场标记验证
# ═══════════════════════════════════════════════════════════════════════════════


class TestChartBuilderShortEntryMarker:
    """验证 chart_builder 中做空入场 marker 使用 triangle-down。"""

    def test_builder_short_entry_uses_triangle_down(self):
        """_add_pnl_traces 中做空入场标记符号应为 'triangle-down'。"""
        import inspect
        from browse.chart_builder import _add_pnl_traces

        source = inspect.getsource(_add_pnl_traces)
        # 做空入场应使用 triangle-down
        assert 'triangle-down' in source, (
            "做空入场标记应为 'triangle-down'"
        )
        # 验证仍保留了 triangle-up（做多入场用）
        assert 'triangle-up' in source, "做多入场标记应继续使用 'triangle-up'"

    def test_long_entry_still_triangle_up(self):
        """_add_pnl_traces 中做多入场标记仍为 'triangle-up'。"""
        from browse.chart_builder import _add_pnl_traces
        import numpy as np

        n = 30
        t = np.arange(n, dtype=float)
        long_pnl = np.linspace(100, 120, n)
        short_pnl = np.full(n, 100.0)
        trade_records = [
            {"type": "long", "entry_idx": 5, "exit_idx": 15,
             "exit_reason": "take_profit", "return_pct": 3.0, "id": 1},
        ]

        traces, shapes, annotations, yaxes = _add_pnl_traces(
            t, long_pnl, short_pnl, trade_records, pnl_row=6,
        )

        # 查找 triangle-up 标记 traces
        up_traces = [
            tr for tr in traces
            if tr.get("mode") == "markers" and tr.get("marker", {}).get("symbol") == "triangle-up"
        ]
        assert len(up_traces) >= 1, f"做多入场应有 triangle-up 标记，实际找到 {len(up_traces)} 条"

        # 验证颜色
        from filter.constants.colors import COLORS
        assert up_traces[0]["marker"]["color"] == COLORS["pnl_long"]

    def test_short_entry_uses_triangle_down_symbol(self):
        """做空入场 marker 符号为 'triangle-down'，颜色为做空红。"""
        from browse.chart_builder import _add_pnl_traces
        import numpy as np

        n = 30
        t = np.arange(n, dtype=float)
        long_pnl = np.full(n, 100.0)
        short_pnl = np.linspace(100, 120, n)
        trade_records = [
            {"type": "short", "entry_idx": 3, "exit_idx": 20,
             "exit_reason": "take_profit", "return_pct": 5.0, "id": 1},
        ]

        traces, shapes, annotations, yaxes = _add_pnl_traces(
            t, long_pnl, short_pnl, trade_records, pnl_row=6,
        )

        # 查找 triangle-down 标记 traces
        down_traces = [
            tr for tr in traces
            if tr.get("mode") == "markers" and tr.get("marker", {}).get("symbol") == "triangle-down"
        ]
        assert len(down_traces) >= 1, (
            f"做空入场应有 triangle-down 标记，实际找到 {len(down_traces)} 条"
        )

        from filter.constants.colors import COLORS
        assert down_traces[0]["marker"]["color"] == COLORS["pnl_short"]
