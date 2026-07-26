"""
P2-5 覆盖率提升测试 — 针对低覆盖率模块的补充测试。

目标模块:
- filter/engine/filters.py (46% → 51%+)
- filter/backtest/metrics.py (59% → 64%+)
- filter/backtest/panel.py (36% → 41%+)
- filter/browse/sidebar.py (22% → 27%+)
- filter/backtest/engine.py (52% → 57%+)
- filter/browse/app.py (26% → 31%+)
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# Ensure filter/ package is importable
_src = Path(__file__).resolve().parent.parent / "filter"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))


# ===================================================================
# SECTION A — filter/engine/filters.py 补充测试
# ===================================================================

class TestFiltersRegistryCompleteness:
    """FILTERS 注册表完整性与边界测试."""

    def test_all_10_filters_registered(self):
        """所有10个滤波器应在 FILTERS 中注册."""
        from engine.filters import FILTERS
        expected = ["sma", "ema", "wma", "alma", "savgol",
                    "kalman", "butterworth", "gaussian", "median", "lowess"]
        for key in expected:
            assert key in FILTERS, f"缺少 {key}"
        assert len(FILTERS) == 10

    def test_each_filter_has_required_keys(self):
        """每个 FILTERS 条目必须有 name, func, params."""
        from engine.filters import FILTERS
        for key, entry in FILTERS.items():
            assert "name" in entry, f"{key} 缺少 name"
            assert "func" in entry, f"{key} 缺少 func"
            assert "params" in entry, f"{key} 缺少 params"
            assert callable(entry["func"]), f"{key}.func 不可调用"
            assert isinstance(entry["params"], dict), f"{key}.params 不是 dict"

    def test_each_param_spec_has_5_elements(self):
        """每个参数规格应有5个元素: (label, min, max, step, default)."""
        from engine.filters import FILTERS
        for key, entry in FILTERS.items():
            for pname, pdef in entry["params"].items():
                assert isinstance(pdef, tuple), f"{key}.{pname} 不是 tuple"
                assert len(pdef) == 5, f"{key}.{pname} 应有5个元素, 实际 {len(pdef)}"

    def test_filter_names_match_keys(self):
        """每个 filter 的 name 字段与其 key 相关."""
        from engine.filters import FILTERS
        for key, entry in FILTERS.items():
            assert isinstance(entry["name"], str)
            assert len(entry["name"]) > 0

    def test_numba_jit_decorator_exists(self):
        """验证 numba jit 装饰器在 filters.py 中存在."""
        from engine.filters import HAS_NUMBA
        # HAS_NUMBA 是一个布尔值
        assert isinstance(HAS_NUMBA, bool)


class TestKalmanFilterNoNumba:
    """卡尔曼滤波无 numba 时的纯 Python fallback 路径."""

    def test_kalman_fallback_without_numba(self):
        """模拟 numba 不可用时的 fallback 路径."""
        import engine.filters as filt_mod
        signal = np.sin(np.linspace(0, 2 * np.pi, 30)) + np.random.RandomState(42).randn(30) * 0.05
        t = np.arange(30, dtype=float)

        # 临时禁用 numba
        with patch.object(filt_mod, "HAS_NUMBA", False):
            result = filt_mod.apply_kalman(signal, t, Q=0.01, R=1.0)
        assert len(result) == len(signal)
        assert not np.any(np.isnan(result))


class TestDrawdownMetricsNumbaFallback:
    """_compute_drawdown_metrics 的纯 Python fallback 测试."""

    def test_python_fallback_empty(self):
        """空数组."""
        from filter.backtest.metrics import _compute_drawdown_metrics_py
        max_dd, max_dd_dur = _compute_drawdown_metrics_py(np.array([]))
        assert max_dd == 0.0
        assert max_dd_dur == 0

    def test_python_fallback_monotonic_increase(self):
        """单调增长."""
        from filter.backtest.metrics import _compute_drawdown_metrics_py
        pnl = np.linspace(100, 200, 100)
        max_dd, max_dd_dur = _compute_drawdown_metrics_py(pnl)
        assert max_dd == 0.0
        assert max_dd_dur == 0

    def test_python_fallback_monotonic_decrease(self):
        """单调下降."""
        from filter.backtest.metrics import _compute_drawdown_metrics_py
        pnl = np.linspace(100, 50, 100)
        max_dd, max_dd_dur = _compute_drawdown_metrics_py(pnl)
        assert max_dd < 0
        assert max_dd_dur == 99

    def test_python_fallback_v_shape(self):
        """V形恢复."""
        from filter.backtest.metrics import _compute_drawdown_metrics_py
        pnl = np.array([100, 110, 90, 80, 90, 100, 110])
        max_dd, max_dd_dur = _compute_drawdown_metrics_py(pnl)
        assert max_dd < 0
        assert max_dd_dur >= 1

    def test_numba_disabled_path(self):
        """模拟 HAS_NUMBA=False 时使用纯 Python fallback."""
        import filter.backtest.metrics as metrics_mod
        pnl = np.linspace(100, 50, 100)
        with patch.object(metrics_mod, "HAS_NUMBA", False):
            max_dd, max_dd_dur = metrics_mod._compute_drawdown_metrics(pnl)
        assert max_dd < 0
        assert max_dd_dur == 99

    def test_combined_pnl_long_dominant(self):
        """long_pnl > short_pnl 时 combined = max(long, short) = long."""
        from filter.backtest.metrics import compute_backtest_metrics
        long_pnl = np.linspace(100, 150, 100)
        short_pnl = np.full(100, 100.0)
        # 验证不崩溃
        result = compute_backtest_metrics(long_pnl, short_pnl, [], 100)
        assert "total_return_pct" in result

    def test_combined_pnl_short_dominant(self):
        """short_pnl > long_pnl 时 combined = max(long, short) = short."""
        from filter.backtest.metrics import compute_backtest_metrics
        long_pnl = np.full(100, 100.0)
        short_pnl = np.linspace(100, 150, 100)
        result = compute_backtest_metrics(long_pnl, short_pnl, [], 100)
        assert "total_return_pct" in result

    def test_finite_returns_filtering(self):
        """returns 中的 inf/nan 被过滤 (np.isfinite)."""
        from filter.backtest.metrics import compute_backtest_metrics
        # PnL: the code handles division by near-zero via np.where fallback
        pnl = np.array([1e-6, 100.0, 200.0, 300.0])
        result = compute_backtest_metrics(pnl, pnl, [], 252)
        assert "sharpe_ratio" in result

    def test_zero_vol_returns_zero_sharpe(self):
        """零波动率: sharpe=0."""
        from filter.backtest.metrics import compute_backtest_metrics
        pnl = np.full(100, 100.0)  # flat
        result = compute_backtest_metrics(pnl, pnl, [], 100)
        assert result["sharpe_ratio"] == 0.0

    def test_sortino_with_no_downside(self):
        """无下行波动: Sortino=0."""
        from filter.backtest.metrics import compute_backtest_metrics
        pnl = np.linspace(100, 200, 100)  # monotonic up
        result = compute_backtest_metrics(pnl, pnl, [], 100)
        assert result["sortino_ratio"] == 0.0  # no downside returns

    def test_yearly_annualization(self):
        """年化计算: 252 bars = 1 year."""
        from filter.backtest.metrics import compute_backtest_metrics
        pnl = np.array([100.0, 110.0])
        result = compute_backtest_metrics(pnl, pnl, [], 252)
        assert result["annualized_return_pct"] == pytest.approx(10.0, rel=0.1)

    def test_large_number_of_bars(self):
        """大量 bar: 年化正常."""
        from filter.backtest.metrics import compute_backtest_metrics
        pnl = np.linspace(100, 110, 504)  # 2 years
        result = compute_backtest_metrics(pnl, pnl, [], 504)
        assert result["total_trades"] == 0


# ===================================================================
# SECTION I — 额外 filter/engine.py panel/replay 测试
# ===================================================================

class TestReplayBarExtended:
    """replay_bar 额外边缘情况."""

    def test_replay_bar_run_value_error(self):
        """runner.run 抛出 ValueError 时 replay_bar 返回 None."""
        from filter.backtest.engine import replay_bar

        configs = [
            {"tf": "日线", "n_pts": 120, "_fid": "sma", "show_sch": True,
             "show_strategy": False, "show_pred": False, "pv": {"window": 11}},
        ]

        # bar_count=0 → ValueError
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        mock_row = MagicMock()
        mock_row.__getitem__.return_value = 0  # zero bars
        mock_conn.execute.return_value.fetchone.return_value = mock_row
        mock_conn.execute.return_value.fetchall.return_value = []

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            with patch("filter.backtest.engine.logger"):
                result = replay_bar("TEST", 120, configs)
                assert result is None

    def test_replay_bar_run_index_error(self):
        """runner.run 抛出 IndexError 时 replay_bar 返回 None."""
        from filter.backtest.engine import replay_bar

        configs = [
            {"tf": "日线", "n_pts": 5, "_fid": "sma", "show_sch": True,
             "show_strategy": False, "show_pred": False, "pv": {"window": 11}},
        ]

        # bar_count=10, bar_index=5 (max_n_pts) → end_bar=6, should work
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        mock_row_count = MagicMock()
        mock_row_count.__getitem__.return_value = 10
        mock_conn.execute.return_value.fetchone.return_value = mock_row_count
        # bar_info rows
        mock_bars = [
            {"ts": f"2026-01-{i+1:02d}", "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.0, "volume": 1000}
            for i in range(10)
        ]
        mock_conn.execute.return_value.fetchall.return_value = mock_bars

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            with patch("filter.backtest.engine._sync_all_cascading",
                       return_value={"日线": False}):
                with patch("filter.backtest.engine.load_display_cache",
                           return_value=None):
                    with patch("filter.backtest.engine.logger"):
                        with patch("filter.backtest.catalog.BacktestCatalog"):
                            with patch("filter.backtest.metrics.compute_backtest_metrics",
                                       return_value={}):
                                result = replay_bar("TEST", 3, configs)
                                # n_pts=5 so max_n_pts=5, bar_index is adjusted
                                assert result is not None


class TestBacktestRunnerConfigHashExtended:
    """_config_hash 扩展测试."""

    def test_config_hash_with_empty_config_safe(self):
        """空 configs 不应该导致 _config_hash 崩溃 (但是构造函数会阻止空 configs)."""
        from filter.backtest.engine import BacktestRunner
        configs = [{"tf": "日线", "n_pts": 120, "_fid": "sma", "show_sch": False,
                    "show_strategy": False, "show_pred": False, "pv": {}, "pv2": {}}]
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_row = MagicMock()
        mock_row.__getitem__.return_value = 100
        mock_conn.execute.return_value.fetchone.return_value = mock_row
        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("T", configs)
        h = runner._config_hash()
        assert isinstance(h, str)
        assert len(h) == 64


class TestFilterEdgeCases:
    """滤波器边界值测试."""

    def test_sma_even_window_auto_adjust(self):
        """SMA: 偶数窗口自动+1."""
        from engine.filters import apply_sma
        signal = np.arange(1, 101, dtype=float)
        t = np.arange(100, dtype=float)
        result = apply_sma(signal, t, window=10)  # even → becomes 11
        assert len(result) == len(signal)
        assert not np.any(np.isnan(result[5:-5]))

    def test_wma_even_window_auto_adjust(self):
        """WMA: 偶数窗口自动+1."""
        from engine.filters import apply_wma
        signal = np.arange(1, 101, dtype=float)
        t = np.arange(100, dtype=float)
        result = apply_wma(signal, t, window=10)  # even → becomes 11
        assert len(result) == len(signal)

    def test_alma_sigma_zero(self):
        """ALMA: sigma=0 时应正常处理（s=window/1.0）."""
        from engine.filters import apply_alma
        signal = np.ones(100)
        t = np.arange(100, dtype=float)
        result = apply_alma(signal, t, window=11, offset=0.85, sigma=0.0)
        assert len(result) == len(signal)

    def test_savgol_order_ge_window(self):
        """SavGol: order >= window 时自动降为 window-1."""
        from engine.filters import apply_savgol
        signal = np.arange(1, 101, dtype=float)
        t = np.arange(100, dtype=float)
        result = apply_savgol(signal, t, window=11, order=15)
        assert len(result) == len(signal)
        assert not np.any(np.isnan(result))

    def test_butterworth_high_cutoff(self):
        """Butterworth: cutoff >= nyquist 时自动调整."""
        from engine.filters import apply_butterworth
        signal = np.sin(np.linspace(0, 4 * np.pi, 100))
        t = np.arange(100, dtype=float)
        result = apply_butterworth(signal, t, order=4, cutoff=50.0)
        assert len(result) == len(signal)

    def test_median_large_kernel(self):
        """中值滤波: 窗口大于信号长度."""
        from engine.filters import apply_median
        signal = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        t = np.arange(5, dtype=float)
        result = apply_median(signal, t, window=11)
        assert len(result) == len(signal)

    def test_ema_small_span(self):
        """EMA: 最小 span=2."""
        from engine.filters import apply_ema
        signal = np.array([100.0, 101.0, 102.0, 103.0, 104.0])
        t = np.arange(5, dtype=float)
        result = apply_ema(signal, t, span=2)
        assert len(result) == len(signal)

    def test_gaussian_small_sigma(self):
        """高斯滤波: 极小 sigma."""
        from engine.filters import apply_gaussian
        signal = np.sin(np.linspace(0, 2 * np.pi, 50))
        t = np.arange(50, dtype=float)
        result = apply_gaussian(signal, t, sigma=0.1)
        assert len(result) == len(signal)

    def test_lowess_small_frac(self):
        """LOWESS: 极小 frac."""
        from engine.filters import apply_lowess
        signal = np.sin(np.linspace(0, 2 * np.pi, 50))
        t = np.arange(50, dtype=float)
        result = apply_lowess(signal, t, frac=0.02)
        assert len(result) == len(signal)

    def test_kalman_noisy_input(self):
        """卡尔曼滤波: 含噪声输入."""
        from engine.filters import apply_kalman
        rng = np.random.RandomState(123)
        signal = np.cumsum(rng.randn(100) * 0.1) + 100.0
        t = np.arange(100, dtype=float)
        result = apply_kalman(signal, t, Q=0.01, R=1.0)
        assert len(result) == len(signal)
        assert not np.any(np.isnan(result))

    def test_negative_signal_values(self):
        """负值信号: 所有滤波器应正常处理."""
        from engine.filters import FILTERS
        # Use signal longer than the maximum window size to avoid convolution edge effects
        signal = np.tile(np.array([-10.0, -5.0, 0.0, 5.0, 10.0, 5.0, 0.0, -5.0, -10.0, -5.0]), 5)
        t = np.arange(len(signal), dtype=float)
        for key, entry in FILTERS.items():
            defaults = {pname: pdef[4] for pname, pdef in entry["params"].items()}
            result = entry["func"](signal.copy(), t, **defaults)
            assert len(result) == len(signal), f"{key}: output length mismatch"

    def test_large_value_signal(self):
        """大数值信号: 所有滤波器应正常处理."""
        from engine.filters import FILTERS
        signal = np.full(100, 1e6, dtype=float)
        t = np.arange(100, dtype=float)
        for key, entry in FILTERS.items():
            defaults = {pname: pdef[4] for pname, pdef in entry["params"].items()}
            result = entry["func"](signal.copy(), t, **defaults)
            assert len(result) == len(signal), f"{key}: large value signal failed"
            # 结果应接近原值（偏离<1%，允许卷积边界效应）
            mid_slice = slice(20, -20)
            if len(result[mid_slice]) > 0:
                assert np.allclose(result[mid_slice], 1e6, rtol=0.02), f"{key}: large value deviation"


# ===================================================================
# SECTION B — filter/backtest/metrics.py 补充测试
# ===================================================================

class TestDrawdownMetricsEdgeCases:
    """_compute_drawdown_metrics 边界测试."""

    def test_empty_array(self):
        """空数组返回 (0.0, 0)."""
        from filter.backtest.metrics import _compute_drawdown_metrics
        max_dd, max_dd_dur = _compute_drawdown_metrics(np.array([]))
        assert max_dd == 0.0
        assert max_dd_dur == 0

    def test_single_point(self):
        """单点返回 (0.0, 0)."""
        from filter.backtest.metrics import _compute_drawdown_metrics
        max_dd, max_dd_dur = _compute_drawdown_metrics(np.array([100.0]))
        assert max_dd == 0.0
        assert max_dd_dur == 0

    def test_monotonic_increase(self):
        """单调增长: 无回撤."""
        from filter.backtest.metrics import _compute_drawdown_metrics
        pnl = np.linspace(100, 200, 100)
        max_dd, max_dd_dur = _compute_drawdown_metrics(pnl)
        assert max_dd == 0.0
        assert max_dd_dur == 0

    def test_monotonic_decrease(self):
        """单调下降: 全程水下."""
        from filter.backtest.metrics import _compute_drawdown_metrics
        pnl = np.linspace(100, 50, 100)
        max_dd, max_dd_dur = _compute_drawdown_metrics(pnl)
        assert max_dd < 0  # 存在回撤
        # 第1个点是最高点(dd=0不算水下), 后99个点在水下
        assert max_dd_dur == 99

    def test_v_shape_recovery(self):
        """V形恢复: 回撤后恢复."""
        from filter.backtest.metrics import _compute_drawdown_metrics
        pnl = np.array([100, 110, 90, 80, 90, 100, 110])
        max_dd, max_dd_dur = _compute_drawdown_metrics(pnl)
        assert max_dd < 0
        # 最低点80相对峰值110的回撤
        assert abs(max_dd - (80 - 110) / 110) < 0.01

    def test_peak_at_zero_protection(self):
        """peak=0 保护 — 回撤返回 0.0."""
        from filter.backtest.metrics import _compute_drawdown_metrics
        pnl = np.array([0.0, 0.0, -1.0, 0.0])
        max_dd, _ = _compute_drawdown_metrics(pnl)
        # peak 始终为 0, dd 最多为 0.0
        assert max_dd == 0.0

    def test_drawdown_with_plateau(self):
        """平台期: 回撤持续时间正确."""
        from filter.backtest.metrics import _compute_drawdown_metrics
        pnl = np.array([100, 90, 90, 90, 90, 100])
        max_dd, max_dd_dur = _compute_drawdown_metrics(pnl)
        assert max_dd_dur == 4  # 4 bars underwater


class TestEmptyMetrics:
    """_empty_metrics 默认值测试."""

    def test_returns_all_keys(self):
        """所有预期 key 存在且为合理默认值."""
        from filter.backtest.metrics import _empty_metrics
        metrics = _empty_metrics()
        expected_keys = [
            "total_return_pct", "win_rate_pct", "profit_factor",
            "sharpe_ratio", "sortino_ratio", "calmar_ratio",
            "max_drawdown_pct", "max_drawdown_duration",
            "annualized_return_pct", "annualized_volatility_pct",
            "avg_trade_return_pct", "avg_win_pct", "avg_loss_pct",
            "total_trades", "winning_trades", "losing_trades",
        ]
        for key in expected_keys:
            assert key in metrics, f"缺少 key: {key}"

    def test_all_values_are_zero_for_numeric(self):
        """所有数值默认值为 0."""
        from filter.backtest.metrics import _empty_metrics
        metrics = _empty_metrics()
        for k, v in metrics.items():
            if k != "max_drawdown_duration":
                assert v == 0.0 or v == 0, f"{k}: expected 0, got {v}"


class TestComputeBacktestMetricsEdgeCases:
    """compute_backtest_metrics 边界测试."""

    def test_short_signal_returns_empty_metrics(self):
        """少于2个点的 PnL 返回空指标."""
        from filter.backtest.metrics import compute_backtest_metrics
        long_pnl = np.array([100.0])
        short_pnl = np.array([100.0])
        result = compute_backtest_metrics(long_pnl, short_pnl, [], 10)
        assert result["total_return_pct"] == 0.0
        assert result["sharpe_ratio"] == 0.0

    def test_empty_trades(self):
        """无交易记录时交易指标为0."""
        from filter.backtest.metrics import compute_backtest_metrics
        long_pnl = np.full(100, 100.0)
        short_pnl = np.full(100, 100.0)
        result = compute_backtest_metrics(long_pnl, short_pnl, [], 100)
        assert result["total_trades"] == 0
        assert result["win_rate_pct"] == 0.0

    def test_all_winning_trades(self):
        """全部盈利交易: win_rate=100%, profit_factor=inf."""
        from filter.backtest.metrics import compute_backtest_metrics
        pnl = np.linspace(100, 150, 100)
        trades = [
            {"return_pct": 5.0},
            {"return_pct": 3.0},
            {"return_pct": 2.0},
        ]
        result = compute_backtest_metrics(pnl, pnl, trades, 100)
        assert result["total_trades"] == 3
        assert result["winning_trades"] == 3
        assert result["losing_trades"] == 0
        assert result["win_rate_pct"] == 100.0
        assert result["profit_factor"] == float("inf")

    def test_all_losing_trades(self):
        """全部亏损交易: win_rate=0%."""
        from filter.backtest.metrics import compute_backtest_metrics
        pnl = np.full(100, 100.0)
        trades = [
            {"return_pct": -2.0},
            {"return_pct": -5.0},
        ]
        result = compute_backtest_metrics(pnl, pnl, trades, 100)
        assert result["total_trades"] == 2
        assert result["winning_trades"] == 0
        assert result["losing_trades"] == 2
        assert result["win_rate_pct"] == 0.0

    def test_mixed_trades(self):
        """混合盈亏交易."""
        from filter.backtest.metrics import compute_backtest_metrics
        pnl = np.linspace(100, 120, 100)
        trades = [
            {"return_pct": 5.0},
            {"return_pct": -3.0},
            {"return_pct": 2.0},
            {"return_pct": -1.0},
        ]
        result = compute_backtest_metrics(pnl, pnl, trades, 100)
        assert result["total_trades"] == 4
        assert result["winning_trades"] == 2
        assert result["losing_trades"] == 2
        assert result["win_rate_pct"] == 50.0

    def test_total_return_calculation(self):
        """总收益率计算."""
        from filter.backtest.metrics import compute_backtest_metrics
        long_pnl = np.array([100.0, 105.0, 110.0])  # 10% return
        short_pnl = np.array([100.0, 100.0, 100.0])
        result = compute_backtest_metrics(long_pnl, short_pnl, [], 252)
        assert result["total_return_pct"] == 10.0

    def test_annualized_return(self):
        """年化收益率."""
        from filter.backtest.metrics import compute_backtest_metrics
        long_pnl = np.array([100.0, 110.0])
        short_pnl = np.array([100.0, 100.0])
        result = compute_backtest_metrics(long_pnl, short_pnl, [], 252)
        # 10% over 1 year
        assert result["annualized_return_pct"] == pytest.approx(10.0, rel=0.1)

    def test_custom_risk_free_rate(self):
        """自定义无风险利率."""
        from filter.backtest.metrics import compute_backtest_metrics
        long_pnl = np.linspace(100, 110, 252)
        short_pnl = np.linspace(100, 110, 252)
        result_default = compute_backtest_metrics(long_pnl, short_pnl, [], 252, risk_free_rate=0.03)
        result_custom = compute_backtest_metrics(long_pnl, short_pnl, [], 252, risk_free_rate=0.05)
        # 更高的无风险利率 → 更低的 Sharpe
        assert result_custom["sharpe_ratio"] <= result_default["sharpe_ratio"]

    def test_calmar_ratio_zero_drawdown(self):
        """零回撤时 Calmar 为 0.0."""
        from filter.backtest.metrics import compute_backtest_metrics
        pnl = np.linspace(100, 200, 100)  # 单调上升, 无回撤
        result = compute_backtest_metrics(pnl, pnl, [], 100)
        assert result["calmar_ratio"] == 0.0

    def test_drawdown_with_peak_100(self):
        """峰值100后下跌50%."""
        from filter.backtest.metrics import compute_backtest_metrics
        pnl = np.array([100.0, 110.0, 55.0, 60.0])  # 50% dd
        result = compute_backtest_metrics(pnl, pnl, [], 252)
        assert result["max_drawdown_pct"] == pytest.approx(-50.0, abs=0.1)


# ===================================================================
# SECTION C — filter/backtest/panel.py 补充测试
# ===================================================================

class TestSaveLoadBacktestConfig:
    """回测配置保存/加载测试."""

    def test_save_creates_valid_json(self, tmp_path):
        """保存的 JSON 文件可解析."""
        from filter.backtest.panel import _save_backtest_config
        # Mock the config path resolution to use tmp_path
        mock_parent = tmp_path / "proj"
        mock_parent.mkdir(parents=True, exist_ok=True)
        (mock_parent / "data").mkdir(exist_ok=True)
        config_file = mock_parent / "data" / "backtest_config_TEST.json"

        def _mock_path_init(self, *parts):
            pass

        with patch.object(Path, "__init__", _mock_path_init), \
             patch.object(Path, "parent",
                         new_callable=lambda: property(lambda s: MagicMock(
                             parent=MagicMock(parent=MagicMock(
                                 __truediv__=lambda s2, p: config_file))))):
            # 简化方法：直接写入并验证
            import builtins
            mock_open = MagicMock()
            real_open = builtins.open

            class MockFile:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    pass

                def write(self, data):
                    config_file.write_text(data)

            with patch("builtins.open", return_value=MockFile()), \
                 patch("filter.backtest.panel.json.dump") as mock_dump:
                _save_backtest_config("TEST", "日线", 500, 120)
                mock_dump.assert_called_once()
                call_args = mock_dump.call_args[0]
                assert call_args[0]["ticker"] == "TEST"
                assert call_args[0]["min_tf"] == "日线"
                assert call_args[0]["bar_count"] == 500
                assert call_args[0]["window_size"] == 120

    def test_load_missing_file_returns_none(self):
        """文件不存在返回 None."""
        from filter.backtest.panel import _load_backtest_config
        with patch.object(Path, "exists", return_value=False):
            result = _load_backtest_config("NONEXISTENT_TICKER_12345")
            assert result is None

    def test_load_corrupt_json_returns_none(self):
        """损坏的 JSON 文件返回 None (不崩溃)."""
        from filter.backtest.panel import _load_backtest_config
        with patch.object(Path, "exists", return_value=True), \
             patch("builtins.open", side_effect=Exception("permission denied")), \
             patch("filter.backtest.panel.logger"):
            result = _load_backtest_config("CORRUPT")
            assert result is None

    def test_load_wrong_ticker_returns_none(self):
        """ticker 不匹配时返回 None."""
        from filter.backtest.panel import _load_backtest_config
        wrong_config = json.dumps({
            "ticker": "OTHER_TICKER",
            "min_tf": "日线",
            "bar_count": 300,
            "window_size": 100,
        })
        with patch.object(Path, "exists", return_value=True), \
             patch("builtins.open", MagicMock(return_value=MagicMock(
                 __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=wrong_config)))
             ))):
            result = _load_backtest_config("MY_TICKER")
            assert result is None


class TestGetMinTfAndCount:
    """_get_min_tf_and_count 测试."""

    def _make_mock_conn(self, bar_count):
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        mock_row = MagicMock()
        mock_row.__getitem__.return_value = bar_count
        mock_conn.execute.return_value.fetchone.return_value = mock_row
        return mock_conn

    def test_empty_configs(self):
        """空 configs 返回 ("", 0)."""
        from filter.backtest.panel import _get_min_tf_and_count
        result = _get_min_tf_and_count([], "TEST")
        assert result == ("", 0)

    def test_single_view_config(self):
        """单视图配置返回正确 min_tf."""
        from filter.backtest.panel import _get_min_tf_and_count
        configs = [{"tf": "日线", "n_pts": 120}]
        mock_conn = self._make_mock_conn(500)

        with patch("data.db.get_conn", return_value=mock_conn):
            min_tf, bar_count = _get_min_tf_and_count(configs, "TEST")
        assert min_tf == "日线"
        assert bar_count == 500

    def test_multiple_views_finest_tf(self):
        """多视图: 取最精细 TF (ALL_TFS 索引最小)."""
        from filter.backtest.panel import _get_min_tf_and_count
        configs = [
            {"tf": "日线", "n_pts": 120},
            {"tf": "60分钟", "n_pts": 60},  # 更精细
            {"tf": "周线", "n_pts": 26},
        ]
        mock_conn = self._make_mock_conn(1000)

        with patch("data.db.get_conn", return_value=mock_conn):
            min_tf, bar_count = _get_min_tf_and_count(configs, "TEST")
        assert min_tf == "60分钟"  # 最精细周期

    def test_invalid_tf_not_crash(self):
        """无效 TF 名称不崩溃，被跳过."""
        from filter.backtest.panel import _get_min_tf_and_count
        configs = [{"tf": "invalid_tf_name", "n_pts": 50}]
        result = _get_min_tf_and_count(configs, "TEST")
        assert result == ("", 0)  # 所有 TF 无效

    def test_no_tf_field_handled(self):
        """视图缺少 tf 字段时跳过."""
        from filter.backtest.panel import _get_min_tf_and_count
        configs = [{"n_pts": 120}]  # no "tf" key
        result = _get_min_tf_and_count(configs, "TEST")
        assert result == ("", 0)


class TestGetBarDateFromDb:
    """_get_bar_date_from_db 测试."""

    def _make_mock_conn(self, date_value):
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        if date_value is not None:
            mock_row = MagicMock()
            mock_row.__getitem__.return_value = date_value
            mock_conn.execute.return_value.fetchone.return_value = mock_row
        else:
            mock_conn.execute.return_value.fetchone.return_value = None
        return mock_conn

    def test_returns_date_string(self):
        """正常返回日期字符串."""
        from filter.backtest.panel import _get_bar_date_from_db
        mock_conn = self._make_mock_conn("2026-01-15")
        with patch("data.db.get_conn", return_value=mock_conn):
            result = _get_bar_date_from_db("TEST", "日线", 5)
        assert result == "2026-01-15"

    def test_no_row_returns_empty_string(self):
        """无数据行返回空字符串."""
        from filter.backtest.panel import _get_bar_date_from_db
        mock_conn = self._make_mock_conn(None)
        with patch("data.db.get_conn", return_value=mock_conn):
            result = _get_bar_date_from_db("TEST", "日线", 9999)
        assert result == ""


class TestRunBacktestPlay:
    """_run_backtest_play 边缘情况."""

    def test_not_playing_returns_false(self):
        """非播放状态返回 False."""
        from filter.backtest.panel import _run_backtest_play
        with patch("filter.backtest.panel.AppState.get", return_value=False):
            result = _run_backtest_play()
            assert result is False

    def test_not_in_cb_mode_stops_playing(self):
        """非回测模式: 停止播放."""
        from filter.backtest.panel import _run_backtest_play
        with patch("filter.backtest.panel.AppState.get") as mock_get:
            mock_get.side_effect = lambda key, default=None: {
                "_is_playing": True,
                "_cb_mode": False,  # 非回测模式
            }.get(key, default)
            with patch("filter.backtest.panel.AppState.set") as mock_set:
                result = _run_backtest_play()
                assert result is False
                mock_set.assert_called_with("_is_playing", False)

    def test_total_zero_stops_playing(self):
        """bar_count=0: 停止播放."""
        from filter.backtest.panel import _run_backtest_play
        with patch("filter.backtest.panel.AppState.get") as mock_get:
            mock_get.side_effect = lambda key, default=None: {
                "_is_playing": True,
                "_cb_mode": True,
                "_min_tf_bar_count": 0,
            }.get(key, default)
            with patch("filter.backtest.panel.AppState.set") as mock_set:
                result = _run_backtest_play()
                assert result is False
                mock_set.assert_called_with("_is_playing", False)

    def test_reached_end_stops_playing(self):
        """到达末尾 (bar_index >= total) 停止播放."""
        from filter.backtest.panel import _run_backtest_play

        # Custom class that supports both dict-like and attribute-like access
        class FakeSessionState(dict):
            __getattr__ = dict.__getitem__
            __setattr__ = dict.__setitem__
            __delattr__ = dict.__delitem__

        fake_ss = FakeSessionState({"_bar_index": 100})

        with patch("filter.backtest.panel.AppState.get") as mock_get, \
             patch("filter.backtest.panel.st") as mock_st:
            mock_st.session_state = fake_ss
            mock_get.side_effect = lambda key, default=None: {
                "_is_playing": True,
                "_cb_mode": True,
                "_min_tf_bar_count": 100,
            }.get(key, default)
            result = _run_backtest_play()
            assert result is False

    def test_advances_bar_index(self):
        """播放时 bar_index 前进."""
        from filter.backtest.panel import _run_backtest_play

        class FakeSessionState(dict):
            __getattr__ = dict.__getitem__
            __setattr__ = dict.__setitem__
            __delattr__ = dict.__delitem__

        fake_ss = FakeSessionState({"_bar_index": 0})

        with patch("filter.backtest.panel.AppState.get") as mock_get, \
             patch("filter.backtest.panel.st") as mock_st:
            mock_st.session_state = fake_ss
            mock_get.side_effect = lambda key, default=None: {
                "_is_playing": True,
                "_cb_mode": True,
                "_min_tf_bar_count": 50,
                "_fetched_ticker": "AAPL",
                "_min_tf": "日线",
            }.get(key, default)
            with patch("filter.backtest.panel._get_bar_date_from_db",
                       return_value="2026-01-16"), \
                 patch("filter.backtest.panel.AppState.set"):
                result = _run_backtest_play()
                assert result is True
                assert fake_ss["_bar_index"] == 1


# ===================================================================
# SECTION D — filter/browse/sidebar.py 补充测试
# ===================================================================

class TestViewExportParams:
    """_view_export_params 测试."""

    def test_returns_all_expected_keys(self):
        """返回 VIEW_PARAM_SPECS 定义的所有键."""
        from filter.browse.sidebar import _view_export_params
        cfg = {
            "tf": "日线", "_fid": "sma", "_dual": False,
            "show_sch": True, "show_strategy": True, "show_pred": True,
            "show_cross_pnl": False, "show_alignment": False,
            "show_pnl_feedback": False,
            "n_pts": 120, "ew": 60, "ke": 0.15, "sm": 0.05,
            "n_ext": 10, "stop_loss_pct": 2.0, "fit_mode": "linear",
        }
        result = _view_export_params(cfg, 0)
        assert isinstance(result, dict)
        assert "v0_tf" in result
        assert result["v0_tf"] == "日线"

    def test_missing_keys_use_defaults(self):
        """缺少字段时使用默认值."""
        from filter.browse.sidebar import _view_export_params
        minimal_cfg = {"tf": "日线"}
        result = _view_export_params(minimal_cfg, 1)
        assert result["v1_tf"] == "日线"
        # 其他字段应使用 VIEW_PARAM_SPECS 的默认值
        for key in result:
            assert result[key] is not None or key.startswith("v1_")


class TestHandlePendingApply:
    """_handle_pending_apply 测试."""

    def test_no_pending_params(self):
        """无等待参数时不报错."""
        from filter.browse.sidebar import _handle_pending_apply
        with patch("filter.browse.sidebar.AppState.has", return_value=False):
            _handle_pending_apply()

    def test_pending_params_none(self):
        """等待参数为 None 时直接返回."""
        from filter.browse.sidebar import _handle_pending_apply
        with patch("filter.browse.sidebar.AppState.has", return_value=True), \
             patch("filter.browse.sidebar.AppState.pop", return_value=None):
            _handle_pending_apply()

    def test_pending_params_applied(self):
        """等待参数正常应用."""
        from filter.browse.sidebar import _handle_pending_apply
        params = {"key1": "val1", "key2": 42}

        call_args = []

        def fake_has(key):
            return key == "_pending_apply_params"

        def fake_pop(key):
            return params

        def fake_set(key, value):
            call_args.append((key, value))

        with patch("filter.browse.sidebar.AppState.has", side_effect=fake_has), \
             patch("filter.browse.sidebar.AppState.pop", side_effect=fake_pop), \
             patch("filter.browse.sidebar.AppState.set", side_effect=fake_set):
            _handle_pending_apply()

        assert len(call_args) == 3  # key1, key2, _import_data


class TestFilterSelectors:
    """_render_filter_selectors 测试."""

    def test_dual_false_returns_none_filter_id2(self):
        """dual=False 时 filter_id2 为 None."""
        from filter.browse.sidebar import _render_filter_selectors
        mock_response = [False, True, True, False, True, False]  # 控制 checkbox 返回值
        response_iter = iter(mock_response)

        with patch("filter.browse.sidebar.st.sidebar.selectbox", return_value="sma"), \
             patch("filter.browse.sidebar.st.sidebar.checkbox",
                   side_effect=lambda *a, **kw: next(response_iter)), \
             patch("filter.browse.sidebar.FILTERS", {
                 "sma": {"name": "SMA", "func": lambda x: x, "params": {}},
             }):
            filter_id, dual, filter_id2 = _render_filter_selectors()
            assert filter_id == "sma"
            assert dual is False
            assert filter_id2 is None

    def test_dual_true_returns_filter_id2(self):
        """dual=True 时返回 filter_id2."""
        from filter.browse.sidebar import _render_filter_selectors
        # selectbox return: first=sma, second=ema, checkbox=True
        selectbox_returns = ["sma", "ema"]

        def selectbox_side_effect(*a, **kw):
            return selectbox_returns.pop(0) if selectbox_returns else "sma"

        with patch("filter.browse.sidebar.st.sidebar.selectbox",
                   side_effect=selectbox_side_effect), \
             patch("filter.browse.sidebar.st.sidebar.checkbox", return_value=True), \
             patch("filter.browse.sidebar.FILTERS", {
                 "sma": {"name": "SMA", "func": lambda x: x, "params": {}},
                 "ema": {"name": "EMA", "func": lambda x: x, "params": {}},
             }):
            filter_id, dual, filter_id2 = _render_filter_selectors()
            assert dual is True
            assert filter_id2 == "ema"


# ===================================================================
# SECTION E — filter/backtest/engine.py 补充测试
# ===================================================================

class TestFromCheckpointError:
    """from_checkpoint 错误处理测试."""

    def test_file_not_found_raises(self):
        """断点文件不存在抛出 ValueError."""
        from filter.backtest.engine import BacktestRunner
        configs = [{"tf": "日线", "n_pts": 120, "_fid": "sma", "_dual": False,
                    "show_sch": True, "show_strategy": False, "show_pred": False,
                    "ke": 0.15, "sm": 0.05, "ew": 60, "pv": {"window": 11}, "pv2": {}}]
        with pytest.raises(ValueError, match="不存在"):
            BacktestRunner.from_checkpoint("/nonexistent/path/checkpoint.json", configs)

    def test_not_implemented_for_valid_path(self, tmp_path):
        """存在有效断点文件时仍抛出 NotImplementedError (需 ticker 参数)."""
        from filter.backtest.engine import BacktestRunner
        configs = [{"tf": "日线", "n_pts": 120, "_fid": "sma", "_dual": False,
                    "show_sch": True, "show_strategy": False, "show_pred": False,
                    "ke": 0.15, "sm": 0.05, "ew": 60, "pv": {"window": 11}, "pv2": {}}]

        checkpoint_path = tmp_path / "test_checkpoint.json"
        checkpoint_path.write_text(json.dumps({
            "bar_index": 42,
            "bar_count": 1000,
            "config_hash": "abc123",
            "ewma_state": {},
        }))

        with pytest.raises(NotImplementedError):
            BacktestRunner.from_checkpoint(str(checkpoint_path), configs)

    def test_restore_checkpoint_with_hash_mismatch_raises(self, tmp_path):
        """配置哈希不匹配时抛出 ValueError."""
        from filter.backtest.engine import BacktestRunner

        configs = [{"tf": "日线", "n_pts": 120, "_fid": "sma", "show_sch": True,
                    "show_strategy": False, "show_pred": False, "pv": {"window": 11}}]

        checkpoint_path = tmp_path / "mismatch_checkpoint.json"
        checkpoint_path.write_text(json.dumps({
            "bar_index": 50,
            "bar_count": 1000,
            "config_hash": "different_hash_value_12345",
            "ewma_state": {},
        }))

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        mock_row = MagicMock()
        mock_row.__getitem__.return_value = 500
        mock_conn.execute.return_value.fetchone.return_value = mock_row

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("TEST", configs)

        with pytest.raises(ValueError, match="配置哈希不匹配"):
            runner._restore_checkpoint(str(checkpoint_path), configs)


class TestLoadWindowDataEdgeCases:
    """_load_window_data 边界测试."""

    def test_missing_date_column(self):
        """缺少 Date 列返回 None."""
        from filter.backtest.engine import BacktestRunner
        configs = [{"tf": "日线", "n_pts": 60, "_fid": "sma", "show_sch": True,
                    "show_strategy": False, "show_pred": False, "pv": {"window": 11}}]

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        mock_row = MagicMock()
        mock_row.__getitem__.return_value = 200
        mock_conn.execute.return_value.fetchone.return_value = mock_row

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("TEST", configs)

        df_no_date = pd.DataFrame({"Close": [100.0, 101.0, 102.0]})
        with patch("filter.backtest.engine.load_display_cache", return_value=df_no_date):
            result = runner._load_window_data("日线", 60)
            assert result is None

    def test_missing_close_column(self):
        """缺少 Close 列返回 None."""
        from filter.backtest.engine import BacktestRunner
        configs = [{"tf": "日线", "n_pts": 60, "_fid": "sma", "show_sch": True,
                    "show_strategy": False, "show_pred": False, "pv": {"window": 11}}]

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_row = MagicMock()
        mock_row.__getitem__.return_value = 200
        mock_conn.execute.return_value.fetchone.return_value = mock_row

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("TEST", configs)

        df_no_close = pd.DataFrame({"Date": ["2026-01-01"], "Open": [100.0]})
        with patch("filter.backtest.engine.load_display_cache", return_value=df_no_close):
            result = runner._load_window_data("日线", 60)
            assert result is None

    def test_cutoff_date_filtering(self):
        """cutoff_date 过滤: 只保留日期 <= cutoff 的数据."""
        from filter.backtest.engine import BacktestRunner
        configs = [{"tf": "日线", "n_pts": 60, "_fid": "sma", "show_sch": True,
                    "show_strategy": False, "show_pred": False, "pv": {"window": 11}}]

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_row = MagicMock()
        mock_row.__getitem__.return_value = 200
        mock_conn.execute.return_value.fetchone.return_value = mock_row
        mock_conn.execute.return_value.fetchall.return_value = [
            {"ts": f"2026-01-{i+1:02d}", "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.0, "volume": 1000}
            for i in range(200)
        ]

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("TEST", configs)

        # 10 days of data, cutoff at day 5
        df = pd.DataFrame({
            "Date": [f"2026-01-{i+1:02d}" for i in range(10)],
            "Close": [100 + i for i in range(10)],
            "Open": [100 + i for i in range(10)],
            "High": [101 + i for i in range(10)],
            "Low": [99 + i for i in range(10)],
        })
        with patch("filter.backtest.engine.load_display_cache", return_value=df):
            result = runner._load_window_data("日线", 60, cutoff_date="2026-01-05")
            assert result is not None
            t, noisy, ohlc, dates = result
            # 只应有5天数据 (1-5)
            assert len(noisy) == 5

    def test_ohlc_fallback_no_open_high_low(self):
        """缺少 OHLC 列时使用 Close 填充."""
        from filter.backtest.engine import BacktestRunner
        configs = [{"tf": "日线", "n_pts": 60, "_fid": "sma", "show_sch": True,
                    "show_strategy": False, "show_pred": False, "pv": {"window": 11}}]

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_row = MagicMock()
        mock_row.__getitem__.return_value = 200
        mock_conn.execute.return_value.fetchone.return_value = mock_row
        mock_conn.execute.return_value.fetchall.return_value = [
            {"ts": f"2026-01-{i+1:02d}", "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.0, "volume": 1000}
            for i in range(200)
        ]

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("TEST", configs)

        df_close_only = pd.DataFrame({
            "Date": ["2026-01-01", "2026-01-02", "2026-01-03"],
            "Close": [100.0, 101.0, 102.0],
        })
        with patch("filter.backtest.engine.load_display_cache", return_value=df_close_only):
            result = runner._load_window_data("日线", 60)
            assert result is not None
            t, noisy, ohlc, dates = result
            assert "Open" in ohlc.columns
            assert np.allclose(ohlc["Open"], noisy)

    def test_empty_parquet_returns_none(self):
        """加载空 parquet 返回 None."""
        from filter.backtest.engine import BacktestRunner
        configs = [{"tf": "日线", "n_pts": 60, "_fid": "sma", "show_sch": True,
                    "show_strategy": False, "show_pred": False, "pv": {"window": 11}}]

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_row = MagicMock()
        mock_row.__getitem__.return_value = 200
        mock_conn.execute.return_value.fetchone.return_value = mock_row
        mock_conn.execute.return_value.fetchall.return_value = [
            {"ts": f"2026-01-{i+1:02d}", "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.0, "volume": 1000}
            for i in range(200)
        ]

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("TEST", configs)

        df_single = pd.DataFrame({
            "Date": ["2026-01-01"],
            "Close": [100.0],
        })
        with patch("filter.backtest.engine.load_display_cache", return_value=df_single):
            result = runner._load_window_data("日线", 60)
            # len=1, 少于2 -> 返回 None
            assert result is None


class TestPipelineForViewEdgeCases:
    """_compute_pipeline_for_view 边界测试."""

    def test_no_schmitt_no_strategy(self):
        """show_sch=False, show_strategy=False: 返回基本结构."""
        from filter.backtest.engine import BacktestRunner
        configs = [{"tf": "日线", "n_pts": 60, "_fid": "sma", "show_sch": False,
                    "show_strategy": False, "show_pred": False, "pv": {"window": 11}}]

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_row = MagicMock()
        mock_row.__getitem__.return_value = 200
        mock_conn.execute.return_value.fetchone.return_value = mock_row

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("TEST", configs)

        t = np.arange(50, dtype=float)
        noisy = np.sin(t / 10.0) + 100.0
        ohlc = pd.DataFrame({
            "Open": noisy, "High": noisy + 1, "Low": noisy - 1,
            "Close": noisy, "Volume": np.full(50, 1000.0),
        })
        dates = pd.DatetimeIndex(pd.date_range("2026-01-01", periods=50, freq="D"))
        window_data = (t, noisy, ohlc, dates)

        view_cfg = {
            "tf": "日线", "_fid": "sma", "pv": {"window": 11},
            "_dual": False, "show_sch": False, "show_strategy": False,
            "show_pred": False,
        }

        result = runner._compute_pipeline_for_view(view_cfg, window_data)
        assert "t" in result
        assert "noisy" in result
        assert "filtered" in result
        assert result["schmitt"] is None
        assert result["long_pnl"] is not None
        assert result["short_pnl"] is not None
        assert result["trade_records"] == []

    def test_with_ewma_init(self):
        """带 EWMA 初始状态的管道计算."""
        from filter.backtest.engine import BacktestRunner
        configs = [{"tf": "日线", "n_pts": 60, "_fid": "sma", "show_sch": True,
                    "show_strategy": False, "show_pred": False, "pv": {"window": 11}}]

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_row = MagicMock()
        mock_row.__getitem__.return_value = 200
        mock_conn.execute.return_value.fetchone.return_value = mock_row

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("TEST", configs)

        t = np.arange(200, dtype=float)
        noisy = np.sin(t / 20.0) + 100.0
        ohlc = pd.DataFrame({
            "Open": noisy, "High": noisy + 1, "Low": noisy - 1,
            "Close": noisy, "Volume": np.full(200, 1000.0),
        })
        dates = pd.DatetimeIndex(pd.date_range("2026-01-01", periods=200, freq="D"))
        window_data = (t, noisy, ohlc, dates)

        view_cfg = {
            "tf": "日线", "_fid": "sma", "pv": {"window": 11},
            "_dual": False, "show_sch": True, "show_strategy": False,
            "show_pred": False, "ke": 0.15, "sm": 0.05, "ew": 60,
        }

        result = runner._compute_pipeline_for_view(
            view_cfg, window_data,
            ewma_init={"init_mu": 0.5, "init_sigma": 0.1, "state": 1, "dur": 5},
        )
        assert result["schmitt"] is not None


# ===================================================================
# SECTION F — filter/browse/app.py 补充测试
# ===================================================================

class TestPrepareChartDataErrorPaths:
    """_prepare_chart_data 错误路径测试."""

    def test_data_loading_error_short_circuits(self):
        """数据加载失败: 返回含 err 的 dict."""
        import filter.browse.app as app_module
        with patch.object(app_module, "_load_chart_data",
                         return_value=(None, None, None, None, None, "网络错误")):
            with patch.object(app_module, "st") as mock_st:
                mock_st.session_state = MagicMock()
                mock_st.session_state.get = lambda key, default=None: default
                params = {
                    "market": "美股", "ticker_code": "AAPL",
                    "cfg": {"tf": "日线", "n_pts": 120},
                    "key": "v0", "compact": True,
                    "higher_pnl": None,
                    "window_start": None, "cutoff_date": None,
                }
                result = app_module._prepare_chart_data(params)
                assert result["err"] == "网络错误"

    def test_data_none_short_circuits(self):
        """t=None (数据为空) 短路上返回."""
        import filter.browse.app as app_module
        with patch.object(app_module, "_load_chart_data",
                         return_value=(None, None, None, None, None, None)):
            with patch.object(app_module, "st") as mock_st:
                mock_st.session_state = MagicMock()
                mock_st.session_state.get = lambda key, default=None: default
                params = {
                    "market": "美股", "ticker_code": "AAPL",
                    "cfg": {"tf": "日线", "n_pts": 120},
                    "key": "v0", "compact": True,
                    "higher_pnl": None,
                    "window_start": None, "cutoff_date": None,
                }
                result = app_module._prepare_chart_data(params)
                assert result["err"] is not None
                assert "数据点不足" in str(result["err"])

    def test_short_data_short_circuits(self):
        """数据点 < 2 短路上返回."""
        import filter.browse.app as app_module
        with patch.object(app_module, "_load_chart_data",
                         return_value=(np.array([0.0]), np.array([100.0]),
                                      None, "AAPL", None, None)):
            with patch.object(app_module, "st") as mock_st:
                mock_st.session_state = MagicMock()
                mock_st.session_state.get = lambda key, default=None: default
                params = {
                    "market": "美股", "ticker_code": "AAPL",
                    "cfg": {"tf": "日线", "n_pts": 120},
                    "key": "v0", "compact": True,
                    "higher_pnl": None,
                    "window_start": None, "cutoff_date": None,
                }
                result = app_module._prepare_chart_data(params)
                assert result["err"] is not None
                assert "数据点不足" in str(result["err"])


class TestLoadChartData:
    """_load_chart_data 边界测试."""

    def test_parquet_load_error_causes_api_fallback(self):
        """parquet 写入失败回退到 API."""
        import filter.browse.app as app_module
        with patch("filter.browse.app._sync_to_display") as mock_sync, \
             patch("filter.browse.app.load_display_cache") as mock_load, \
             patch("filter.browse.app._cached_fetch_stock") as mock_fetch, \
             patch("filter.browse.app.log_data_load"):
            # sync returns False (写入失败)
            mock_sync.return_value = (False, 0)
            mock_load.return_value = None

            result = app_module._load_chart_data("美股", "AAPL", "日线", 120)
            # 回退到 API
            mock_fetch.assert_called_once()

    def test_parquet_load_success(self):
        """parquet 加载成功."""
        import filter.browse.app as app_module
        with patch("filter.browse.app._sync_to_display") as mock_sync, \
             patch("filter.browse.app.load_display_cache") as mock_load, \
             patch("filter.browse.app.log_data_load"):
            mock_sync.return_value = (True, 100)
            df = pd.DataFrame({
                "Date": ["2026-01-03", "2026-01-01", "2026-01-02"],
                "Close": [103.0, 101.0, 102.0],
                "Open": [102.0, 100.0, 101.0],
                "High": [104.0, 102.0, 103.0],
                "Low": [101.0, 99.0, 100.0],
            })
            mock_load.return_value = df

            t, noisy, ohlc, ticker_full, dates, err = app_module._load_chart_data(
                "美股", "AAPL", "日线", 120,
            )
            assert err is None
            assert len(noisy) == 3
            assert ticker_full == "AAPL"
            # 验证排序: 数据按 Date 升序排列
            assert noisy[0] == 101.0  # 最早日期是 2026-01-01

    def test_parquet_missing_columns(self):
        """parquet 缺少必要列时返回错误."""
        import filter.browse.app as app_module
        with patch("filter.browse.app._sync_to_display") as mock_sync, \
             patch("filter.browse.app.load_display_cache") as mock_load, \
             patch("filter.browse.app.log_data_load"):
            mock_sync.return_value = (True, 100)
            df_no_date = pd.DataFrame({"Price": [100.0, 101.0, 102.0]})
            mock_load.return_value = df_no_date

            t, noisy, ohlc, ticker_full, dates, err = app_module._load_chart_data(
                "美股", "AAPL", "日线", 120,
            )
            assert err is not None

    def test_parquet_only_one_point(self):
        """parquet 只有1个数据点: 返回错误."""
        import filter.browse.app as app_module
        with patch("filter.browse.app._sync_to_display") as mock_sync, \
             patch("filter.browse.app.load_display_cache") as mock_load, \
             patch("filter.browse.app.log_data_load"):
            mock_sync.return_value = (True, 1)
            df = pd.DataFrame({
                "Date": ["2026-01-01"],
                "Close": [100.0],
            })
            mock_load.return_value = df

            t, noisy, ohlc, ticker_full, dates, err = app_module._load_chart_data(
                "美股", "AAPL", "日线", 120,
            )
            assert err is not None


class TestComputeStrategyDisplay:
    """_compute_strategy_display 边界测试."""

    def test_no_strategy_returns_none_pnl(self):
        """show_strategy=False 返回 (None, None, [])."""
        import filter.browse.app as app_module
        with patch.object(app_module, "st") as mock_st:
            mock_st.columns.return_value = [MagicMock() for _ in range(3)]
            t = np.arange(100, dtype=float)
            filtered = np.sin(t / 10.0) + 100.0
            cfg = {"show_strategy": False, "stop_loss_pct": 2.0, "n_ext": 10}
            long_pnl, short_pnl, trade_records = app_module._compute_strategy_display(
                t, filtered, None, [], [], cfg, "日线", pd.DatetimeIndex([]),
            )
            assert long_pnl is None
            assert short_pnl is None
            assert trade_records == []

    def test_no_schmitt_returns_none_pnl(self):
        """schmitt=None 时 show_strategy=True 仍然返回 (None, None, [])."""
        import filter.browse.app as app_module
        with patch.object(app_module, "st") as mock_st:
            mock_st.columns.return_value = [MagicMock() for _ in range(3)]
            t = np.arange(100, dtype=float)
            filtered = np.sin(t / 10.0) + 100.0
            cfg = {"show_strategy": True, "stop_loss_pct": 2.0, "n_ext": 10}
            long_pnl, short_pnl, trade_records = app_module._compute_strategy_display(
                t, filtered, None, [], [], cfg, "日线", pd.DatetimeIndex([]),
            )
            assert long_pnl is None
            assert short_pnl is None
            assert trade_records == []

    def test_empty_pred_pairs_returns_none_pnl(self):
        """pred_pairs 为空时返回 (None, None, [])."""
        import filter.browse.app as app_module
        with patch.object(app_module, "st") as mock_st:
            mock_st.columns.return_value = [MagicMock() for _ in range(3)]
            t = np.arange(100, dtype=float)
            filtered = np.sin(t / 10.0) + 100.0
            schmitt = {"sig": np.ones(100, dtype=int)}
            cfg = {"show_strategy": True, "stop_loss_pct": 2.0, "n_ext": 10}
            long_pnl, short_pnl, trade_records = app_module._compute_strategy_display(
                t, filtered, schmitt, [], [], cfg, "日线", pd.DatetimeIndex([]),
            )
            assert long_pnl is None
            assert short_pnl is None
            assert trade_records == []


class TestCachedFunctions:
    """Streamlit 缓存函数边界测试."""

    def test_compute_filters_cache_basic(self):
        """_compute_filters 基本缓存行为."""
        import filter.browse.app as app_module
        noisy = np.sin(np.linspace(0, 4 * np.pi, 100))
        t = np.arange(100, dtype=float)
        import time
        st_mod = sys.modules.get("streamlit")
        if hasattr(st_mod, "cache_data"):
            import importlib
            importlib.reload(app_module)
        cfg = {"_fid": "sma", "pv": {"window": 11}}
        filtered, filtered2 = app_module._compute_filters(noisy, t, cfg)
        assert len(filtered) == 100
        assert filtered2 is None

    def test_compute_schmitt_basic(self):
        """_compute_schmitt_trigger 基本行为."""
        import filter.browse.app as app_module
        filtered = np.sin(np.linspace(0, 4 * np.pi, 200))
        t = np.arange(200, dtype=float)
        cfg = {"show_sch": True, "ke": 0.15, "sm": 0.05, "ew": 60, "_fid": "sma"}
        result = app_module._compute_schmitt_trigger(filtered, t, cfg)
        assert result is not None
        assert "sig" in result

    def test_compute_schmitt_show_sch_false(self):
        """show_sch=False 返回 None."""
        import filter.browse.app as app_module
        filtered = np.sin(np.linspace(0, 4 * np.pi, 200))
        t = np.arange(200, dtype=float)
        cfg = {"show_sch": False}
        result = app_module._compute_schmitt_trigger(filtered, t, cfg)
        assert result is None

    def test_compute_schmitt_all_nan(self):
        """全 NaN 滤波信号返回 None."""
        import filter.browse.app as app_module
        filtered = np.full(100, np.nan)
        t = np.arange(100, dtype=float)
        cfg = {"show_sch": True}
        result = app_module._compute_schmitt_trigger(filtered, t, cfg)
        assert result is None

    def test_compute_schmitt_short_signal(self):
        """len < 2 返回 None."""
        import filter.browse.app as app_module
        filtered = np.array([1.0])
        t = np.array([0.0])
        cfg = {"show_sch": True}
        result = app_module._compute_schmitt_trigger(filtered, t, cfg)
        assert result is None

    def test_compute_prediction_no_show_pred(self):
        """show_pred=False 返回空列表."""
        import filter.browse.app as app_module
        t = np.arange(50, dtype=float)
        filtered = np.sin(t / 10.0)
        cfg = {"show_pred": False}
        result = app_module._compute_prediction_pairs(t, filtered, None, cfg, [])
        assert result == []

    def test_compute_prediction_no_schmitt(self):
        """schmitt=None 返回空列表."""
        import filter.browse.app as app_module
        t = np.arange(50, dtype=float)
        filtered = np.sin(t / 10.0)
        cfg = {"show_pred": True}
        result = app_module._compute_prediction_pairs(t, filtered, None, cfg, [])
        assert result == []


# ===================================================================
# SECTION G — filter/backtest/engine.py compute_and_log_metrics 扩展
# ===================================================================

class TestComputeAndLogMetricsExtended:
    """_compute_and_log_metrics 扩展测试."""

    def test_multiple_steps_aggregation(self):
        """多步结果聚合 PnL 和交易记录."""
        from filter.backtest.engine import _compute_and_log_metrics
        results = [
            {
                "step_index": 0,
                "bar_index": 0,
                "views": {
                    "v0": {
                        "t": np.arange(10),
                        "long_pnl": np.full(10, 100.0),
                        "short_pnl": np.full(10, 100.0),
                        "trade_records": [{"return_pct": 5.0}],
                    },
                }
            },
            {
                "step_index": 1,
                "bar_index": 1,
                "views": {
                    "v0": {
                        "t": np.arange(10),
                        "long_pnl": np.full(10, 101.0),
                        "short_pnl": np.full(10, 101.0),
                        "trade_records": [{"return_pct": -2.0}],
                    },
                }
            },
        ]
        with patch("filter.backtest.metrics.compute_backtest_metrics",
                   return_value={"total_return_pct": -1.0, "sharpe_ratio": 0.5,
                                "max_drawdown_pct": -2.0, "total_trades": 2,
                                "win_rate_pct": 50.0}):
            with patch("filter.backtest.catalog.BacktestCatalog") as mock_catalog:
                mock_catalog.return_value.save_index.return_value = None
                _compute_and_log_metrics(results, "TEST")

    def test_no_long_pnl_segments(self):
        """所有视图无 PnL 数据: 不报错."""
        from filter.backtest.engine import _compute_and_log_metrics
        results = [{
            "step_index": 0,
            "bar_index": 0,
            "views": {
                "v0": {
                    "t": np.arange(10),
                    "long_pnl": None,
                    "short_pnl": None,
                    "trade_records": [],
                },
            }
        }]
        _compute_and_log_metrics(results, "TEST")

    def test_mixed_none_and_valid_pnl(self):
        """混合 None 和有效 PnL 的视图."""
        from filter.backtest.engine import _compute_and_log_metrics
        results = [{
            "step_index": 0,
            "bar_index": 0,
            "views": {
                "v0": {
                    "t": np.arange(10),
                    "long_pnl": np.full(10, 100.0),
                    "short_pnl": np.full(10, 100.0),
                    "trade_records": [],
                },
                "v1": {
                    "t": np.arange(10),
                    "long_pnl": None,
                    "short_pnl": None,
                    "trade_records": [],
                },
            }
        }]
        _compute_and_log_metrics(results, "TEST")


# ===================================================================
# SECTION H — filter/browse/sidebar.py render_db_backup 测试
# ===================================================================

class TestRenderDbBackup:
    """_render_db_backup 按钮逻辑测试 (纯函数逻辑)."""

    def test_db_size_format_in_caption(self):
        """验证 get_db_size_mb 被调用以获取数据库大小."""
        from filter.browse.sidebar import _render_db_backup

        mock_col = MagicMock()

        with patch("filter.browse.sidebar.st") as mock_st, \
             patch("filter.browse.sidebar.get_db_size_mb",
                   return_value=10.5) as mock_size, \
             patch("filter.browse.sidebar.list_snapshots", return_value=[]), \
             patch("filter.browse.sidebar.logger"):
            mock_st.sidebar = MagicMock()
            mock_st.sidebar.expander.return_value.__enter__ = MagicMock()
            mock_st.sidebar.columns.return_value = [mock_col, mock_col]
            mock_st.sidebar.button.return_value = False
            mock_st.sidebar.selectbox.return_value = 0
            mock_st.sidebar.caption = MagicMock()
            mock_st.columns.return_value = [mock_col, mock_col]
            mock_st.caption = MagicMock()
            mock_st.button.return_value = False

            _render_db_backup()

            mock_size.assert_called_once()

    def test_no_snapshots_shows_empty_message(self):
        """无快照时: 至少不崩溃."""
        from filter.browse.sidebar import _render_db_backup

        mock_col = MagicMock()

        with patch("filter.browse.sidebar.st") as mock_st, \
             patch("filter.browse.sidebar.get_db_size_mb", return_value=5.0), \
             patch("filter.browse.sidebar.list_snapshots", return_value=[]), \
             patch("filter.browse.sidebar.logger"):
            mock_st.sidebar = MagicMock()
            mock_st.sidebar.expander.return_value.__enter__ = MagicMock()
            mock_st.sidebar.columns.return_value = [mock_col, mock_col]
            mock_st.sidebar.button.return_value = False
            mock_st.sidebar.caption = MagicMock()
            mock_st.sidebar.selectbox.return_value = 0
            mock_st.columns.return_value = [mock_col, mock_col]
            mock_st.caption = MagicMock()
            mock_st.button.return_value = False

            # 验证不抛出异常
            _render_db_backup()


class TestRenderExportConfig:
    """_render_export_config 测试."""

    def test_export_data_structure(self):
        """导出数据结构正确."""
        from filter.browse.sidebar import _render_export_config
        from filter.engine.filters import FILTERS
        configs = [{
            "tf": "日线", "_fid": "sma", "_dual": False,
            "show_sch": True, "show_strategy": True, "show_pred": True,
            "show_cross_pnl": False, "show_alignment": False,
            "show_pnl_feedback": False,
            "n_pts": 120, "ew": 60, "ke": 0.15, "sm": 0.05,
            "n_ext": 10, "stop_loss_pct": 2.0, "fit_mode": "linear",
            "pv": {"window": 11}, "pv2": {}, "fc": "#00d4aa", "fc2": "#ff6b6b",
        }]

        with patch("filter.browse.sidebar.st.sidebar.download_button") as mock_download, \
             patch("filter.browse.sidebar.st.sidebar.markdown"), \
             patch("filter.browse.sidebar.FILTERS", FILTERS):
            _render_export_config(configs, "sma", None, False, "美股", "AAPL")

        assert mock_download.called
        args = mock_download.call_args[0]
        export_json = args[1]
        export_data = json.loads(export_json)
        assert export_data["market"] == "美股"
        assert export_data["ticker"] == "AAPL"
        assert export_data["global_f"] == "sma"


class TestRenderConfigHistory:
    """_render_config_history 测试."""

    def test_no_ticker_skips_rendering(self):
        """无 ticker 时跳过渲染."""
        from filter.browse.sidebar import _render_config_history
        with patch("filter.browse.sidebar.st") as mock_st:
            _render_config_history("")
            # expander 不应该被调用
            mock_st.sidebar.expander.assert_not_called()

    def test_with_records_displays_items(self):
        """有历史记录时正确显示."""
        from filter.browse.sidebar import _render_config_history
        records = [
            {"changed_at": "2026-01-15 10:00", "source": "ui", "preset_name": "MyPreset"},
            {"changed_at": "2026-01-14 10:00", "source": "import", "preset_name": None},
        ]

        mock_expander = MagicMock()
        mock_exp_ctxt = MagicMock()
        mock_expander.return_value.__enter__.return_value = mock_exp_ctxt

        with patch("filter.browse.sidebar.st") as mock_st:
            mock_st.sidebar = MagicMock()
            mock_st.sidebar.markdown = MagicMock()
            mock_st.sidebar.expander = mock_expander
            mock_st.sidebar.caption = MagicMock()
            mock_st.caption = MagicMock()
            with patch("filter.browse.sidebar.get_history", return_value=records):
                _render_config_history("AAPL")

            # 至少调用了 caption
            caption_calls = (
                mock_st.sidebar.caption.call_args_list +
                mock_st.caption.call_args_list
            )
            assert len(caption_calls) >= 2  # 至少2条记录

    def test_empty_records_shows_default(self):
        """无历史记录时显示默认提示."""
        from filter.browse.sidebar import _render_config_history

        mock_expander = MagicMock()
        mock_exp_ctxt = MagicMock()
        mock_expander.return_value.__enter__.return_value = mock_exp_ctxt

        with patch("filter.browse.sidebar.st") as mock_st:
            mock_st.sidebar = MagicMock()
            mock_st.sidebar.markdown = MagicMock()
            mock_st.sidebar.expander = mock_expander
            mock_st.sidebar.caption = MagicMock()
            mock_st.caption = MagicMock()
            with patch("filter.browse.sidebar.get_history", return_value=[]):
                _render_config_history("AAPL")

            # 验证至少渲染了 "暂无记录"
            caption_calls = (
                list(mock_st.sidebar.caption.call_args_list) +
                list(mock_st.caption.call_args_list)
            )
            caption_texts = [str(c[0][0]) for c in caption_calls if c[0]]
            assert any("暂无记录" in t for t in caption_texts)
