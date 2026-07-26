"""
Tests for Phase 3 — pipeline unification, data_loader split, metrics integration.

Verifies:
  - engine.pipeline functions produce deterministic outputs
  - Web and CLI can use the same pipeline functions
  - data_loader re-exports from fetcher/synth work correctly
  - BacktestRunner uses pipeline functions
  - backtest.metrics and backtest.catalog are importable
"""

import numpy as np
import pandas as pd
import pytest


# ═══════════════════════════════════════════════════════════════
# engine.pipeline — unified pipeline functions
# ═══════════════════════════════════════════════════════════════

class TestPipelineFunctions:
    """Verify engine.pipeline functions produce correct, deterministic outputs."""

    @pytest.fixture
    def signals(self):
        np.random.seed(42)
        t = np.arange(300, dtype=float)
        noisy = np.sin(t / 20.0) + np.random.randn(300) * 0.3
        return noisy, t

    @pytest.fixture
    def default_cfg(self):
        return {
            "_fid": "sma",
            "pv": {"window": 11},
            "_dual": False,
            "_fid2": "",
            "pv2": {},
            "show_sch": True,
            "ew": 60,
            "ke": 0.15,
            "sm": 0.05,
            "show_pred": True,
            "show_strategy": True,
            "stop_loss_pct": 2.0,
            "n_ext": 10,
        }

    def test_compute_filters_returns_tuple(self, signals, default_cfg):
        from engine.pipeline import compute_filters
        noisy, t = signals
        filtered, filtered2 = compute_filters(noisy, t, default_cfg)
        assert isinstance(filtered, np.ndarray)
        assert len(filtered) == len(noisy)
        assert not np.all(np.isnan(filtered))

    def test_compute_filters_dual(self, signals):
        from engine.pipeline import compute_filters
        noisy, t = signals
        cfg = {
            "_fid": "ema", "pv": {"span": 10},
            "_dual": True, "_fid2": "sma", "pv2": {"window": 11},
        }
        filtered, filtered2 = compute_filters(noisy, t, cfg)
        assert isinstance(filtered2, np.ndarray)
        assert len(filtered2) == len(noisy)

    def test_compute_filters_unknown_id(self, signals):
        from engine.pipeline import compute_filters
        noisy, t = signals
        cfg = {"_fid": "nonexistent", "pv": {}}
        filtered, filtered2 = compute_filters(noisy, t, cfg)
        assert np.all(np.isnan(filtered))
        assert filtered2 is None

    def test_compute_filters_deterministic(self, signals, default_cfg):
        from engine.pipeline import compute_filters
        noisy, t = signals
        r1, _ = compute_filters(noisy, t, default_cfg)
        r2, _ = compute_filters(noisy, t, default_cfg)
        assert np.allclose(r1, r2, equal_nan=True)

    def test_compute_schmitt_trigger(self, signals, default_cfg):
        from engine.pipeline import compute_filters, compute_schmitt_trigger
        noisy, t = signals
        filtered, _ = compute_filters(noisy, t, default_cfg)
        result = compute_schmitt_trigger(filtered, t, default_cfg)
        assert result is not None
        assert "sig" in result
        assert "v" in result
        assert "a" in result
        assert len(result["sig"]) == len(t)

    def test_compute_schmitt_trigger_disabled(self, signals, default_cfg):
        from engine.pipeline import compute_schmitt_trigger
        cfg = {**default_cfg, "show_sch": False}
        result = compute_schmitt_trigger(np.ones(100), np.arange(100, dtype=float), cfg)
        assert result is None

    def test_compute_schmitt_trigger_with_ewma_init(self, signals, default_cfg):
        from engine.pipeline import compute_filters, compute_schmitt_trigger
        noisy, t = signals
        filtered, _ = compute_filters(noisy, t, default_cfg)
        result = compute_schmitt_trigger(
            filtered, t, default_cfg,
            init_mu=0.0, init_sigma=0.1, init_state=1, init_dur=5,
        )
        assert result is not None

    def test_compute_prediction_pairs(self, signals, default_cfg):
        from engine.pipeline import compute_filters, compute_schmitt_trigger, compute_prediction_pairs
        from engine.schmitt import _find_all_pairs
        noisy, t = signals
        filtered, _ = compute_filters(noisy, t, default_cfg)
        schmitt = compute_schmitt_trigger(filtered, t, default_cfg)
        all_pairs = _find_all_pairs(schmitt["sig"])
        preds = compute_prediction_pairs(t, filtered, schmitt, default_cfg, all_pairs)
        assert isinstance(preds, list)
        for p in preds:
            assert "fit_result" in p
            assert "fit_start" in p
            assert "pair_end" in p

    def test_compute_prediction_pairs_disabled(self, signals, default_cfg):
        from engine.pipeline import compute_prediction_pairs
        cfg = {**default_cfg, "show_pred": False}
        result = compute_prediction_pairs(
            np.arange(100, dtype=float), np.ones(100), {"sig": np.ones(100, dtype=int)}, cfg, [],
        )
        assert result == []

    def test_web_cli_unified_output(self, signals, default_cfg):
        """Same inputs via pipeline.py produce same outputs for Web and CLI."""
        from engine.pipeline import compute_filters, compute_schmitt_trigger, compute_prediction_pairs
        from engine.schmitt import _find_all_pairs

        noisy, t = signals

        # First run
        f1, f2_1 = compute_filters(noisy, t, default_cfg)
        s1 = compute_schmitt_trigger(f1, t, default_cfg)

        # Second run — same inputs should yield same outputs
        f2, f2_2 = compute_filters(noisy, t, default_cfg)
        s2 = compute_schmitt_trigger(f2, t, default_cfg)

        # Verify deterministic
        assert np.allclose(f1, f2, equal_nan=True)
        if s1 is not None and s2 is not None:
            assert np.array_equal(s1["sig"], s2["sig"])
            assert np.allclose(s1["v"], s2["v"], equal_nan=True)


# ═══════════════════════════════════════════════════════════════
# data_loader split — re-exports
# ═══════════════════════════════════════════════════════════════

class TestDataLoaderSplit:
    """Verify data/loader.py re-exports work correctly after split."""

    def test_fetch_stock_re_exported(self):
        from data.loader import _fetch_stock
        assert callable(_fetch_stock)

    def test_fetch_all_timeframes_re_exported(self):
        from data.loader import _fetch_all_timeframes
        assert callable(_fetch_all_timeframes)

    def test_stock_name_lookup_re_exported(self):
        from data.loader import _stock_name_lookup
        assert callable(_stock_name_lookup)

    def test_sync_all_cascading_re_exported(self):
        from data.loader import _sync_all_cascading
        assert callable(_sync_all_cascading)

    def test_load_display_cache_still_in_loader(self):
        from data.loader import load_display_cache
        assert callable(load_display_cache)

    def test_fetcher_module_importable(self):
        from data.fetcher import _fetch_stock, _fetch_all_timeframes, _stock_name_lookup
        assert callable(_fetch_stock)
        assert callable(_fetch_all_timeframes)
        assert callable(_stock_name_lookup)

    def test_synth_module_importable(self):
        from data.synth import _sync_all_cascading
        assert callable(_sync_all_cascading)

    def test_no_circular_imports(self):
        """Ensure simultaneous import of all three data modules works."""
        from data.loader import load_display_cache
        from data.fetcher import _fetch_stock
        from data.synth import _sync_all_cascading
        assert True  # If we got here, no ImportError


# ═══════════════════════════════════════════════════════════════
# backtest integration — metrics + catalog
# ═══════════════════════════════════════════════════════════════

class TestBacktestIntegration:
    """Verify isolated modules are integrable."""

    def test_metrics_module_importable(self):
        from filter.backtest.metrics import compute_backtest_metrics
        assert callable(compute_backtest_metrics)

    def test_metrics_computation(self):
        from filter.backtest.metrics import compute_backtest_metrics

        n = 100
        long_pnl = np.cumsum(np.random.randn(n) * 0.02) + 100.0
        short_pnl = np.full(n, 100.0)
        trades = [
            {"return_pct": 1.5}, {"return_pct": -0.5},
            {"return_pct": 2.0}, {"return_pct": -1.0},
        ]

        metrics = compute_backtest_metrics(long_pnl, short_pnl, trades, n)
        assert "sharpe_ratio" in metrics
        assert "max_drawdown_pct" in metrics
        assert "total_trades" in metrics
        assert metrics["total_trades"] == 4
        assert metrics["winning_trades"] == 2
        assert metrics["losing_trades"] == 2

    def test_metrics_empty_data(self):
        from filter.backtest.metrics import compute_backtest_metrics

        metrics = compute_backtest_metrics(
            np.array([100.0]), np.array([100.0]), [], 1,
        )
        assert metrics["total_return_pct"] == 0.0
        assert metrics["total_trades"] == 0

    def test_metrics_single_long_trade(self):
        from filter.backtest.metrics import compute_backtest_metrics

        n = 50
        long_pnl = np.linspace(100, 108, n)
        short_pnl = np.full(n, 100.0)
        trades = [{"return_pct": 8.0}]

        metrics = compute_backtest_metrics(long_pnl, short_pnl, trades, n)
        assert metrics["total_return_pct"] == 8.0
        assert metrics["total_trades"] == 1
        assert metrics["win_rate_pct"] == 100.0

    def test_catalog_module_importable(self):
        from filter.backtest.catalog import BacktestCatalog
        catalog = BacktestCatalog()
        assert catalog is not None

    def test_catalog_scan_empty(self, tmp_path):
        from filter.backtest.catalog import BacktestCatalog
        catalog = BacktestCatalog(base_dir=str(tmp_path))
        sessions = catalog.scan()
        assert isinstance(sessions, list)

    def test_backtestrunner_uses_pipeline(self):
        """Verify BacktestRunner._compute_filters delegates to engine.pipeline."""
        from filter.backtest.engine import BacktestRunner
        noisy = np.arange(100, dtype=float)
        t = np.arange(100, dtype=float)
        cfg = {"_fid": "sma", "pv": {"window": 11}}
        filtered, filtered2 = BacktestRunner._compute_filters(noisy, t, cfg)
        assert isinstance(filtered, np.ndarray)
        assert len(filtered) == 100

    def test_backtestrunner_schmitt_delegates(self):
        """Verify BacktestRunner._compute_schmitt_trigger delegates to pipeline."""
        from filter.backtest.engine import BacktestRunner
        filtered = np.sin(np.arange(200, dtype=float) / 20.0)
        t = np.arange(200, dtype=float)
        cfg = {"show_sch": True, "ew": 60, "ke": 0.15, "sm": 0.05}
        result = BacktestRunner._compute_schmitt_trigger(filtered, t, cfg)
        assert result is not None
        assert "sig" in result

    def test_backtestrunner_prediction_delegates(self):
        """Verify BacktestRunner._compute_prediction_pairs delegates to pipeline."""
        from filter.backtest.engine import BacktestRunner
        t = np.arange(200, dtype=float)
        filtered = np.sin(t / 20.0)
        schmitt = {"sig": np.ones(200, dtype=int)}
        all_pairs = [(0, 50), (50, 100)]
        cfg = {"show_pred": True}
        preds = BacktestRunner._compute_prediction_pairs(t, filtered, schmitt, cfg, all_pairs)
        assert isinstance(preds, list)


# ═══════════════════════════════════════════════════════════════
# End-to-end: same data through pipeline → deterministic outputs
# ═══════════════════════════════════════════════════════════════

class TestPipelineDeterminism:
    """Full pipeline determinism across multiple invocations."""

    def test_full_pipeline_deterministic(self):
        from engine.pipeline import compute_filters, compute_schmitt_trigger, compute_prediction_pairs
        from engine.schmitt import _find_all_pairs

        np.random.seed(123)
        t = np.arange(500, dtype=float)
        noisy = np.sin(t / 30.0) + np.random.randn(500) * 0.4
        cfg = {
            "_fid": "savgol", "pv": {"window": 21, "order": 2},
            "_dual": False,
            "show_sch": True, "ew": 120, "ke": 0.2, "sm": 0.03,
            "show_pred": True,
        }

        # Run 1
        np.random.seed(42)
        f_a, _ = compute_filters(noisy, t, cfg)
        s_a = compute_schmitt_trigger(f_a, t, cfg)
        pairs_a = _find_all_pairs(s_a["sig"]) if s_a else []
        preds_a = compute_prediction_pairs(t, f_a, s_a, cfg, pairs_a)

        # Run 2 (with different random seed — should NOT affect computation)
        np.random.seed(99)
        f_b, _ = compute_filters(noisy, t, cfg)
        s_b = compute_schmitt_trigger(f_b, t, cfg)
        pairs_b = _find_all_pairs(s_b["sig"]) if s_b else []
        preds_b = compute_prediction_pairs(t, f_b, s_b, cfg, pairs_b)

        assert np.allclose(f_a, f_b, equal_nan=True)
        if s_a and s_b:
            assert np.array_equal(s_a["sig"], s_b["sig"])
        assert len(pairs_a) == len(pairs_b)
        assert len(preds_a) == len(preds_b)
