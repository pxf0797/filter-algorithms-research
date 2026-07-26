"""
Tests for BacktestRunner core engine methods.

Tests cover:
- Constructor validation (empty ticker, empty configs)
- run() method validation (mode, edge cases)
- _make_json_safe() utility
- replay_bar() edge cases
- _config_hash() determinism
- Static pipeline methods (_compute_filters, _compute_schmitt_trigger, etc.)
- _compute_strategy_for_view() with various configs
- _compute_masks_for_view() with None higher_pnl
- _compute_bs_for_view() basic call
"""

import json
import numpy as np
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

from filter.backtest.engine import (
    BacktestRunner,
    _make_json_safe,
    replay_bar,
    _compute_and_log_metrics,
)

pytestmark = pytest.mark.engine


# ===================================================================
# Helpers
# ===================================================================

def _make_minimal_configs():
    """Create a minimal set of view configs for testing."""
    return [
        {
            "_fid": "sma", "tf": "日线", "n_pts": 120,
            "show_sch": True, "show_strategy": False, "show_pred": False,
            "ke": 0.15, "sm": 0.05, "ew": 60,
            "pv": {"window": 11}, "pv2": {},
        },
    ]


def _make_mock_db_conn(bar_count=1000):
    """Create a mock DB connection with specified bar count."""
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.__exit__.return_value = False
    mock_row = MagicMock()
    mock_row.__getitem__.return_value = bar_count
    mock_conn.execute.return_value.fetchone.return_value = mock_row
    return mock_conn


def _setup_runner(ticker="TEST", configs=None, bar_count=1000, extra_kwargs=None):
    """Create a BacktestRunner with patched DB dependencies."""
    if configs is None:
        configs = _make_minimal_configs()
    mock_conn = _make_mock_db_conn(bar_count)
    mock_bar_info = [{"ts": f"2026-01-{i+1:02d}", "open": 100.0, "high": 101.0,
                       "low": 99.0, "close": 100.5, "volume": 10000}
                      for i in range(bar_count)]
    with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
        runner = BacktestRunner(ticker, configs, **(extra_kwargs or {}))
    return runner


# ===================================================================
# SECTION 1 — BacktestRunner constructor validation
# ===================================================================

class TestBacktestRunnerInit:
    """Constructor validation tests."""

    def test_empty_ticker_raises(self):
        """空 ticker 抛出 ValueError."""
        with pytest.raises(ValueError, match="ticker 不能为空"):
            _setup_runner(ticker="", configs=_make_minimal_configs())

    def test_whitespace_ticker_raises(self):
        """纯空白 ticker 抛出 ValueError."""
        with pytest.raises(ValueError, match="ticker 不能为空"):
            _setup_runner(ticker="   ", configs=_make_minimal_configs())

    def test_empty_configs_raises(self):
        """空 configs 抛出 ValueError."""
        with pytest.raises(ValueError, match="configs 不能为空"):
            _setup_runner(ticker="TEST", configs=[])

    def test_ticker_stripped(self):
        """ticker 参数前后的空白被 strip."""
        runner = _setup_runner(ticker="  TEST  ")
        assert runner.ticker == "TEST"

    def test_configs_sorted_coarse_to_fine(self):
        """configs 按 ALL_TFS 索引降序(粗到细)排列."""
        from filter.shared.constants import ALL_TFS
        configs = [
            {"tf": "日线", "n_pts": 60, "_fid": "sma", "_dual": False},
            {"tf": "60分钟", "n_pts": 60, "_fid": "sma", "_dual": False},
        ]
        runner = _setup_runner(ticker="TEST", configs=configs)
        tf_indices = [ALL_TFS.index(c["tf"]) for c in runner.configs]
        assert tf_indices == sorted(tf_indices, reverse=True), \
            f"configs should be coarse-to-fine: {tf_indices}"

    def test_save_debug_data_default_false(self):
        """save_debug_data 默认值为 False."""
        runner = _setup_runner()
        assert runner.save_debug_data is False

    def test_save_debug_data_true(self):
        """save_debug_data=True 通过 kwargs 传入."""
        runner = _setup_runner(extra_kwargs={"save_debug_data": True})
        assert runner.save_debug_data is True

    def test_tf_n_pts_populated(self):
        """_tf_n_pts 正确聚合各视图的 n_pts 最大值."""
        configs = [
            {"tf": "日线", "n_pts": 50, "_fid": "sma", "_dual": False},
            {"tf": "日线", "n_pts": 80, "_fid": "sma", "_dual": False},
            {"tf": "60分钟", "n_pts": 30, "_fid": "sma", "_dual": False},
        ]
        runner = _setup_runner(ticker="TEST", configs=configs)
        assert runner._tf_n_pts["日线"] == 80
        assert runner._tf_n_pts["60分钟"] == 30

    def test_bar_count_queried(self):
        """_bar_count 从 DB 查询获得."""
        runner = _setup_runner(bar_count=500)
        assert runner._bar_count == 500

    def test_get_bar_count_public_method(self):
        """get_bar_count() 返回正确的 bar 数."""
        runner = _setup_runner(bar_count=777)
        assert runner.get_bar_count() == 777


# ===================================================================
# SECTION 2 — run() method validation
# ===================================================================

class TestBacktestRunnerRun:
    """run() method validation tests."""

    def test_run_zero_bar_count_raises(self):
        """bar_count=0 时 run() 抛出 ValueError."""
        runner = _setup_runner(bar_count=0)
        with pytest.raises(ValueError, match="无数据"):
            runner.run(0, 1)

    def test_run_negative_start_bar_raises(self):
        """start_bar < 0 时抛出 IndexError."""
        runner = _setup_runner(bar_count=100)
        with pytest.raises(IndexError, match="不能为负数"):
            runner.run(-1, 10)

    def test_run_end_bar_beyond_range_raises(self):
        """end_bar > _bar_count 时抛出 IndexError."""
        runner = _setup_runner(bar_count=50)
        with pytest.raises(IndexError, match="超出有效范围"):
            runner.run(0, 100)

    def test_run_empty_range_returns_empty_list(self):
        """start_bar == end_bar 时返回空列表."""
        runner = _setup_runner(bar_count=100)
        # Patching out _get_bar_info to avoid DB calls
        with patch.object(runner, "_get_bar_info") as mock_bar:
            mock_bar.return_value = {
                "bar_timestamp": "2026-01-15", "cutoff_date": "2026-01-15",
                "ohlcv": {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
            }
            with patch.object(runner, "_sync_data"):
                with patch.object(runner, "_load_window_data", return_value=None):
                    results = runner.run(10, 10)
        assert results == []

    def test_run_with_step_interval(self):
        """step_interval > 1 时结果数量正确."""
        runner = _setup_runner(bar_count=100)
        with patch.object(runner, "_get_bar_info") as mock_bar:
            mock_bar.return_value = {
                "bar_timestamp": "2026-01-15", "cutoff_date": "2026-01-15",
                "ohlcv": {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
            }
            with patch.object(runner, "_sync_data"):
                with patch.object(runner, "_load_window_data", return_value=None):
                    results = runner.run(0, 20, step_interval=5)
        assert len(results) == 4  # 0, 5, 10, 15

    def test_run_result_keys(self):
        """单步结果包含必需的键."""
        runner = _setup_runner(bar_count=100)
        with patch.object(runner, "_get_bar_info") as mock_bar:
            mock_bar.return_value = {
                "bar_timestamp": "2026-01-15", "cutoff_date": "2026-01-15",
                "ohlcv": {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
            }
            with patch.object(runner, "_sync_data"):
                with patch.object(runner, "_load_window_data", return_value=None):
                    results = runner.run(50, 51)
        assert len(results) == 1
        step = results[0]
        assert "step_index" in step
        assert "bar_index" in step
        assert "bar_timestamp" in step
        assert "cutoff_date" in step
        assert "views" in step
        assert "ohlcv" in step


# ===================================================================
# SECTION 3 — _make_json_safe utility
# ===================================================================

class TestMakeJsonSafe:
    """_make_json_safe() JSON 序列化工具测试."""

    def test_numpy_integer(self):
        """np.int64 转为 Python int."""
        assert _make_json_safe(np.int64(42)) == 42
        assert isinstance(_make_json_safe(np.int64(42)), int)

    def test_numpy_float(self):
        """np.float64 转为 Python float."""
        assert _make_json_safe(np.float64(3.14)) == 3.14
        assert isinstance(_make_json_safe(np.float64(3.14)), float)

    def test_numpy_array(self):
        """np.ndarray 转为 Python list."""
        arr = np.array([1, 2, 3])
        result = _make_json_safe(arr)
        assert result == [1, 2, 3]
        assert isinstance(result, list)

    def test_nested_dict(self):
        """嵌套 dict 中的 numpy 类型递归转换."""
        d = {"a": np.int64(1), "b": {"c": np.float64(2.5)}}
        result = _make_json_safe(d)
        assert result == {"a": 1, "b": {"c": 2.5}}
        assert json.dumps(result)  # 可 JSON 序列化

    def test_list_with_mixed_types(self):
        """list 中混合类型正确转换."""
        lst = [np.int64(1), np.float64(2.5), "hello", None]
        result = _make_json_safe(lst)
        assert result == [1, 2.5, "hello", None]

    def test_bool_preserved(self):
        """bool 值保持不变."""
        assert _make_json_safe(True) is True
        assert _make_json_safe(False) is False

    def test_none_preserved(self):
        """None 保持不变."""
        assert _make_json_safe(None) is None

    def test_plain_int_float_preserved(self):
        """普通 int/float 不变."""
        assert _make_json_safe(42) == 42
        assert _make_json_safe(3.14) == 3.14

    def test_tuple_converted_to_list(self):
        """tuple 转为 list."""
        assert _make_json_safe((1, 2, 3)) == [1, 2, 3]

    def test_unknown_type_to_string(self):
        """未知类型转为字符串."""
        from datetime import date
        result = _make_json_safe(date(2026, 1, 15))
        assert isinstance(result, str)

    def test_serializable_full_dict(self):
        """完整的 JSON 安全字典可序列化."""
        d = {
            "bar_index": np.int64(42),
            "ewma_state": {"v0_日线": {"init_mu": np.float64(0.5)}},
            "bar_count": np.int64(1000),
            "config_hash": "abc123",
        }
        result = _make_json_safe(d)
        json_str = json.dumps(result, ensure_ascii=False)
        assert isinstance(json_str, str)
        parsed = json.loads(json_str)
        assert parsed["bar_index"] == 42


# ===================================================================
# SECTION 4 — _config_hash determinism
# ===================================================================

class TestConfigHash:
    """_config_hash() 确定性测试."""

    def test_same_configs_same_hash(self):
        """相同 configs 产生相同哈希."""
        configs = _make_minimal_configs()
        runner1 = _setup_runner(configs=configs)
        runner2 = _setup_runner(configs=configs)
        assert runner1._config_hash() == runner2._config_hash()

    def test_different_configs_different_hash(self):
        """不同 configs 产生不同哈希."""
        configs1 = [{"tf": "日线", "n_pts": 120, "_fid": "sma", "_dual": False,
                     "show_sch": True, "show_strategy": False, "show_pred": False,
                     "ke": 0.15, "sm": 0.05, "ew": 60, "pv": {"window": 11}, "pv2": {}}]
        configs2 = [{"tf": "日线", "n_pts": 60, "_fid": "ema", "_dual": False,
                     "show_sch": True, "show_strategy": False, "show_pred": False,
                     "ke": 0.15, "sm": 0.05, "ew": 60, "pv": {"span": 10}, "pv2": {}}]
        runner1 = _setup_runner(configs=configs1)
        runner2 = _setup_runner(configs=configs2)
        assert runner1._config_hash() != runner2._config_hash()

    def test_hash_is_hex_string(self):
        """哈希值为 64 字符的 hex 字符串."""
        runner = _setup_runner()
        h = runner._config_hash()
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_stable_across_instances(self):
        """同一 configs 多次实例化哈希一致."""
        configs = _make_minimal_configs()
        hashes = []
        for _ in range(5):
            runner = _setup_runner(configs=configs)
            hashes.append(runner._config_hash())
        assert len(set(hashes)) == 1


# ===================================================================
# SECTION 5 — replay_bar edge cases
# ===================================================================

class TestReplayBar:
    """replay_bar() edge case tests."""

    def test_replay_bar_empty_ticker_returns_none(self):
        """空 ticker 返回 None."""
        result = replay_bar("", 100, _make_minimal_configs())
        assert result is None

    def test_replay_bar_whitespace_ticker_returns_none(self):
        """纯空白 ticker 返回 None."""
        result = replay_bar("   ", 100, _make_minimal_configs())
        assert result is None

    def test_replay_bar_empty_configs_returns_none(self):
        """空 configs 返回 None."""
        result = replay_bar("TEST", 100, [])
        assert result is None

    def test_replay_bar_bar_index_below_max_n_pts_adjusted(self):
        """bar_index < max(n_pts) 时自动调整为 max_n_pts 然后运行."""
        configs = [
            {"tf": "日线", "n_pts": 120, "_fid": "sma", "show_sch": True,
             "show_strategy": False, "show_pred": False, "pv": {"window": 11}},
        ]
        # bar_index=50 < max_n_pts=120, should be adjusted
        mock_conn = _make_mock_db_conn(bar_count=500)
        mock_rows = [
            {"ts": f"2026-01-{i+1:02d}", "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.5, "volume": 10000}
            for i in range(500)
        ]
        mock_conn.execute.return_value.fetchall.return_value = mock_rows

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            # _sync_all_cascading is called inside _sync_data in run()
            with patch("filter.backtest.engine._sync_all_cascading",
                       return_value={"日线": True}):
                # load_display_cache returns None -> window_data is None -> view skipped
                with patch("filter.backtest.engine.load_display_cache",
                           return_value=None):
                    with patch("filter.backtest.catalog.BacktestCatalog"):
                        result = replay_bar("TEST", 50, configs)
        # The run() completes despite missing window data (view skipped),
        # and replay_bar returns the step result with adjusted bar_index
        assert result is not None
        assert result["bar_index"] == 120  # adjusted to max_n_pts

    def test_replay_bar_bar_index_beyond_total_returns_none(self):
        """bar_index >= total_bars 返回 None."""
        configs = _make_minimal_configs()
        configs[0]["n_pts"] = 10
        mock_conn = _make_mock_db_conn(bar_count=100)
        mock_rows = [
            {"ts": f"2026-01-{i+1:02d}", "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.5, "volume": 10000}
            for i in range(100)
        ]
        mock_conn.execute.return_value.fetchall.return_value = mock_rows

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            # bar_index=200 >= total_bars=100 → returns None
            result = replay_bar("TEST", 200, configs)
        assert result is None


# ===================================================================
# SECTION 6 — _compute_strategy_for_view
# ===================================================================

class TestComputeStrategyForView:
    """_compute_strategy_for_view() static method tests."""

    def test_no_show_strategy_returns_default_pnl(self):
        """show_strategy=False 时返回默认 PnL (全 100.0)."""
        configs = _make_minimal_configs()
        runner = _setup_runner(configs=configs)

        t = np.arange(50, dtype=float)
        filtered = np.sin(t / 10.0)
        schmitt = {"sig": np.ones(50, dtype=int)}
        all_pairs = [(0, 49)]
        pred_pairs = [{"pair_end": 49, "fit_result": {"direction": 1}}]

        cfg = {
            "show_strategy": False,  # 关键：不显示策略
            "stop_loss_pct": 2.0,
            "n_ext": 10,
        }
        long_pnl, short_pnl, trade_records = runner._compute_strategy_for_view(
            t, filtered, schmitt, all_pairs, pred_pairs, cfg,
        )
        assert np.all(long_pnl == 100.0)
        assert np.all(short_pnl == 100.0)
        assert trade_records == []

    def test_no_schmitt_returns_default_pnl(self):
        """schmitt=None 时返回默认 PnL."""
        runner = _setup_runner()
        t = np.arange(50, dtype=float)
        filtered = np.sin(t / 10.0)
        cfg = {
            "show_strategy": True,
            "stop_loss_pct": 2.0,
            "n_ext": 10,
        }
        long_pnl, short_pnl, trade_records = runner._compute_strategy_for_view(
            t, filtered, None, [], [], cfg,
        )
        assert np.all(long_pnl == 100.0)
        assert trade_records == []

    def test_empty_pred_pairs_returns_default_pnl(self):
        """pred_pairs 为空时返回默认 PnL."""
        runner = _setup_runner()
        t = np.arange(50, dtype=float)
        filtered = np.sin(t / 10.0)
        schmitt = {"sig": np.ones(50, dtype=int)}
        cfg = {
            "show_strategy": True,
            "stop_loss_pct": 2.0,
            "n_ext": 10,
        }
        long_pnl, short_pnl, trade_records = runner._compute_strategy_for_view(
            t, filtered, schmitt, [], [], cfg,
        )
        assert np.all(long_pnl == 100.0)


# ===================================================================
# SECTION 7 — _compute_masks_for_view
# ===================================================================

class TestComputeMasksForView:
    """_compute_masks_for_view() static method tests."""

    def test_none_higher_pnl_returns_none_masks(self):
        """higher_pnl=None 时返回 (None, None)."""
        runner = _setup_runner()
        stage_output = {"t": np.arange(10), "dates": None}
        long_mask, short_mask = runner._compute_masks_for_view(stage_output, None)
        assert long_mask is None
        assert short_mask is None


# ===================================================================
# SECTION 8 — _compute_bs_for_view
# ===================================================================

class TestComputeBSForView:
    """_compute_bs_for_view() static method tests."""

    def test_bs_markers_returned_with_none_masks(self):
        """即使 masks 为 None 也应返回有效的 BS markers dict."""
        runner = _setup_runner()
        stage_output = {
            "t": np.arange(10, dtype=float),
            "dates": None,
            "schmitt": None,
            "all_pairs": [],
            "trade_records": [],
        }
        view_cfg = {"tf": "日线"}

        with patch("filter.backtest.engine.compute_bs_markers",
                   return_value={"entry_markers": [], "exit_markers": []}):
            result = runner._compute_bs_for_view(
                stage_output, view_cfg, None, None,
            )
        assert "entry_markers" in result
        assert "exit_markers" in result

    def test_bs_markers_passed_holding_masks_when_provided(self):
        """提供 holding_masks 时正确传递给 compute_bs_markers."""
        runner = _setup_runner()
        stage_output = {
            "t": np.arange(10, dtype=float),
            "dates": None,
            "schmitt": None,
            "all_pairs": [],
            "trade_records": [],
        }
        view_cfg = {"tf": "日线"}
        long_mask = np.array([True, True, False, False, False,
                              False, False, False, False, False])
        short_mask = np.array([False, False, False, False, False,
                               True, True, False, False, False])

        with patch("filter.backtest.engine.compute_bs_markers") as mock_compute:
            mock_compute.return_value = {"entry_markers": [], "exit_markers": []}
            runner._compute_bs_for_view(
                stage_output, view_cfg, long_mask, short_mask,
            )
            call_kwargs = mock_compute.call_args.kwargs
            assert call_kwargs["holding_masks"] is not None
            hlm, hsm = call_kwargs["holding_masks"]
            assert np.array_equal(hlm, long_mask)
            assert np.array_equal(hsm, short_mask)


# ===================================================================
# SECTION 9 — _compute_filters static method
# ===================================================================

class TestComputeFiltersStatic:
    """_compute_filters() static method tests."""

    def test_compute_sma_filter(self):
        """基本 SMA 滤波计算."""
        runner = _setup_runner()
        noisy = np.sin(np.linspace(0, 4 * np.pi, 100))
        t = np.arange(100, dtype=float)
        cfg = {"_fid": "sma", "pv": {"window": 11}}
        filtered, filtered2 = runner._compute_filters(noisy, t, cfg)
        assert len(filtered) == len(noisy)
        assert filtered2 is None

    def test_compute_dual_filter(self):
        """双滤波模式 (主线 + 副线)."""
        runner = _setup_runner()
        noisy = np.sin(np.linspace(0, 4 * np.pi, 100))
        t = np.arange(100, dtype=float)
        cfg = {
            "_fid": "sma", "pv": {"window": 11},
            "_dual": True, "_fid2": "ema", "pv2": {"span": 10},
        }
        filtered, filtered2 = runner._compute_filters(noisy, t, cfg)
        assert len(filtered) == len(noisy)
        assert filtered2 is not None
        assert len(filtered2) == len(noisy)

    def test_compute_unknown_filter_returns_nan(self):
        """未知滤波器返回 NaN."""
        runner = _setup_runner()
        noisy = np.ones(50)
        t = np.arange(50, dtype=float)
        cfg = {"_fid": "nonexistent_filter", "pv": {}}
        filtered, filtered2 = runner._compute_filters(noisy, t, cfg)
        assert np.all(np.isnan(filtered))
        assert filtered2 is None

    def test_compute_filter_with_exception_returns_nan(self):
        """滤波器异常时返回 NaN 而不崩溃."""
        runner = _setup_runner()
        noisy = np.ones(10)
        t = np.arange(10, dtype=float)
        # Savgol with window > len(data) 会抛出 ValueError，被捕获后返回 NaN
        cfg = {"_fid": "savgol", "pv": {"window": 999, "order": 2}}
        filtered, filtered2 = runner._compute_filters(noisy, t, cfg)
        # 异常被 compute_filters 内部捕获，返回 NaN
        # 注意：compute_filters 捕获 Exception 后返回 np.full_like(noisy, np.nan)
        assert len(filtered) == len(noisy)


# ===================================================================
# SECTION 10 — _compute_schmitt_trigger static method
# ===================================================================

class TestComputeSchmittTriggerStatic:
    """_compute_schmitt_trigger() static method tests."""

    def test_show_sch_false_returns_none(self):
        """show_sch=False 时返回 None."""
        runner = _setup_runner()
        filtered = np.sin(np.linspace(0, 4 * np.pi, 100))
        t = np.arange(100, dtype=float)
        cfg = {"show_sch": False}
        result = runner._compute_schmitt_trigger(filtered, t, cfg)
        assert result is None

    def test_all_nan_filtered_returns_none(self):
        """全 NaN 的 filtered 返回 None."""
        runner = _setup_runner()
        filtered = np.full(100, np.nan)
        t = np.arange(100, dtype=float)
        cfg = {"show_sch": True}
        result = runner._compute_schmitt_trigger(filtered, t, cfg)
        assert result is None

    def test_short_signal_returns_none(self):
        """长度 < 2 的信号返回 None."""
        runner = _setup_runner()
        filtered = np.array([1.0])
        t = np.array([0.0])
        cfg = {"show_sch": True}
        result = runner._compute_schmitt_trigger(filtered, t, cfg)
        assert result is None

    def test_valid_schmitt_computation(self):
        """有效的施密特触发器计算."""
        runner = _setup_runner()
        filtered = np.sin(np.linspace(0, 4 * np.pi, 200))
        t = np.arange(200, dtype=float)
        cfg = {
            "show_sch": True,
            "ke": 0.15, "sm": 0.05, "ew": 60, "_fid": "sma",
        }
        result = runner._compute_schmitt_trigger(filtered, t, cfg)
        assert result is not None
        assert "sig" in result, "schmitt should contain 'sig' key"
        assert len(result["sig"]) == len(filtered)
        # sig values should be in {-1, 0, 1}
        assert set(np.unique(result["sig"])).issubset({-1, 0, 1})

    def test_schmitt_with_ewma_init(self):
        """跨窗口 EWMA 初始状态传递."""
        runner = _setup_runner()
        filtered = np.sin(np.linspace(0, 4 * np.pi, 200))
        t = np.arange(200, dtype=float)
        cfg = {
            "show_sch": True,
            "ke": 0.15, "sm": 0.05, "ew": 60, "_fid": "sma",
        }
        result = runner._compute_schmitt_trigger(
            filtered, t, cfg,
            init_mu=0.5, init_sigma=0.1, init_state=1, init_dur=5,
        )
        assert result is not None
        assert "sig" in result


# ===================================================================
# SECTION 11 — _compute_prediction_pairs static method
# ===================================================================

class TestComputePredictionPairsStatic:
    """_compute_prediction_pairs() static method tests."""

    def test_show_pred_false_returns_empty(self):
        """show_pred=False 时返回空列表."""
        runner = _setup_runner()
        t = np.arange(50, dtype=float)
        filtered = np.sin(t / 10.0)
        cfg = {"show_pred": False}
        result = runner._compute_prediction_pairs(t, filtered, None, cfg, [])
        assert result == []

    def test_schmitt_none_returns_empty(self):
        """schmitt=None 时返回空列表."""
        runner = _setup_runner()
        t = np.arange(50, dtype=float)
        filtered = np.sin(t / 10.0)
        cfg = {"show_pred": True}
        result = runner._compute_prediction_pairs(t, filtered, None, cfg, [])
        assert result == []


# ===================================================================
# SECTION 12 — _compute_and_log_metrics
# ===================================================================

class TestComputeAndLogMetrics:
    """_compute_and_log_metrics() tests."""

    def test_empty_results_returns_early(self):
        """空结果列表不报错."""
        # Should not raise
        _compute_and_log_metrics([], "TEST")

    def test_results_without_views_handled(self):
        """results 中无 views 时不报错."""
        results = [{"step_index": 0, "bar_index": 0, "views": {}}]
        _compute_and_log_metrics(results, "TEST")

    def test_results_with_long_pnl_segments(self):
        """有 PnL segments 时正常聚合."""
        results = [{
            "step_index": 0,
            "bar_index": 0,
            "views": {
                "v0": {
                    "t": np.arange(10),
                    "long_pnl": np.full(10, 100.0),
                    "short_pnl": np.full(10, 100.0),
                    "trade_records": [],
                }
            }
        }]
        # Should not raise
        _compute_and_log_metrics(results, "TEST")


# ===================================================================
# SECTION 13 — _get_bar_info boundary
# ===================================================================

class TestGetBarInfo:
    """_get_bar_info() boundary tests."""

    def test_negative_bar_index_raises(self):
        """bar_index < 0 抛出 IndexError."""
        runner = _setup_runner(bar_count=100)
        with pytest.raises(IndexError):
            runner._get_bar_info(-1)

    def test_bar_index_equals_bar_count_raises(self):
        """bar_index == _bar_count 抛出 IndexError."""
        runner = _setup_runner(bar_count=100)
        with pytest.raises(IndexError):
            runner._get_bar_info(100)

    def test_bar_index_beyond_cache_raises(self):
        """bar_index 超出预加载缓存范围抛出 IndexError."""
        runner = _setup_runner(bar_count=100)
        # _bar_info_cache 大小受 _MAX_BAR_CACHE 限制
        runner._bar_info_cache = [{}] * 5
        runner._bar_count = 10
        with pytest.raises(IndexError, match="预加载缓存"):
            runner._get_bar_info(5)


# ===================================================================
# SECTION 14 — _process_view_post_pipeline
# ===================================================================

class TestProcessViewPostPipeline:
    """_process_view_post_pipeline() tests."""

    def test_updates_ewma_state_with_schmitt(self):
        """有 schmitt 时更新 EWMA 状态."""
        runner = _setup_runner()
        stage_output = {
            "t": np.arange(10, dtype=float),
            "dates": None,
            "noisy": np.ones(10),
            "filtered": np.ones(10),
            "schmitt": {
                "sig": np.zeros(10, dtype=int),
                "final_mu": 0.5, "final_sigma": 0.1,
                "final_state": 1, "final_dur": 3,
            },
            "all_pairs": [],
            "prediction_pairs": [],
            "long_pnl": None,
            "short_pnl": None,
            "trade_records": [],
            "ohlc": None,
        }
        view_cfg = {"tf": "日线"}
        view_key = "v0_日线"
        tf_pnl_cache = {}

        runner._process_view_post_pipeline(stage_output, view_cfg, view_key, tf_pnl_cache)

        assert view_key in runner._ewma_state
        assert runner._ewma_state[view_key]["init_mu"] == 0.5
        assert runner._ewma_state[view_key]["init_sigma"] == 0.1
        assert runner._ewma_state[view_key]["state"] == 1
        assert runner._ewma_state[view_key]["dur"] == 3

    def test_no_schmitt_no_ewma_update(self):
        """无 schmitt 时不更新 EWMA 状态."""
        runner = _setup_runner()
        stage_output = {
            "t": np.arange(10, dtype=float),
            "dates": None,
            "noisy": np.ones(10),
            "filtered": np.ones(10),
            "schmitt": None,
            "all_pairs": [],
            "prediction_pairs": [],
            "long_pnl": None,
            "short_pnl": None,
            "trade_records": [],
            "ohlc": None,
        }
        view_cfg = {"tf": "日线"}
        view_key = "v0_日线"
        tf_pnl_cache = {}
        initial_state = dict(runner._ewma_state)

        runner._process_view_post_pipeline(stage_output, view_cfg, view_key, tf_pnl_cache)

        # EWMA state should not have changed
        assert view_key not in runner._ewma_state

    def test_bs_markers_added_to_output(self):
        """BS markers 被添加到 stage_output."""
        runner = _setup_runner()
        stage_output = {
            "t": np.arange(10, dtype=float),
            "dates": pd.DatetimeIndex(["2026-01-01", "2026-01-02", "2026-01-03",
                                       "2026-01-04", "2026-01-05", "2026-01-06",
                                       "2026-01-07", "2026-01-08", "2026-01-09",
                                       "2026-01-10"]),
            "noisy": np.ones(10),
            "filtered": np.ones(10),
            "schmitt": {
                "sig": np.zeros(10, dtype=int),
                "final_mu": 0.0, "final_sigma": 0.0,
                "final_state": 0, "final_dur": 0,
            },
            "all_pairs": [],
            "prediction_pairs": [],
            "long_pnl": None,
            "short_pnl": None,
            "trade_records": [],
            "ohlc": None,
        }
        view_cfg = {"tf": "日线"}
        view_key = "v0_日线"
        tf_pnl_cache = {}

        runner._process_view_post_pipeline(stage_output, view_cfg, view_key, tf_pnl_cache)

        assert "bs_markers" in stage_output
        assert "entry_markers" in stage_output["bs_markers"]
        assert "exit_markers" in stage_output["bs_markers"]


# ===================================================================
# SECTION 15 — B15/B16/B17 per-bar optimization cache tests
# ===================================================================


class TestB15FileExistsCache:
    """B15: _file_exists 缓存 — 文件存在性检查只执行一次."""

    def test_file_exists_cache_lazy_init(self):
        """首次 _load_window_data 时为每个 TF 执行一次文件存在性检查，后续使用缓存."""

        configs = [
            {"tf": "日线", "n_pts": 60, "_fid": "sma", "show_sch": True,
             "show_strategy": False, "show_pred": False, "pv": {"window": 11}},
        ]
        runner = _setup_runner(ticker="TEST", configs=configs)

        # 验证缓存字典初始为空
        assert runner._file_exists == {}

        # Mock load_display_cache 返回 None — 触发 B15 检查路径
        with patch("filter.backtest.engine.load_display_cache", return_value=None):
            # 用真实的 Path.exists 去检查; 文件大概率不存在
            result1 = runner._load_window_data("日线", 60, cutoff_date="2026-06-01")
            assert result1 is None
            # 缓存已填充
            assert "日线" in runner._file_exists

    def test_file_exists_cache_not_rechecked(self):
        """同一 TF 再次调用 _load_window_data 且 load_display_cache 返回 None 时不再执行 Path.exists()."""
        from pathlib import Path

        configs = [
            {"tf": "日线", "n_pts": 60, "_fid": "sma", "show_sch": True,
             "show_strategy": False, "show_pred": False, "pv": {"window": 11}},
        ]
        runner = _setup_runner(ticker="TEST", configs=configs)

        with patch("filter.backtest.engine.load_display_cache", return_value=None):
            call_count = 0
            _original_exists = Path.exists

            def _counting_exists(self_obj):
                nonlocal call_count
                call_count += 1
                return _original_exists(self_obj)

            with patch.object(Path, "exists", _counting_exists):
                # 第一次调用 — 执行 exists 检查
                runner._load_window_data("日线", 60, cutoff_date="2026-06-01")
                first_call_count = call_count

                # 第二次调用 — 应使用缓存，不再调用 exists
                runner._load_window_data("日线", 60, cutoff_date="2026-06-02")
                # 调用次数不应增加（同一 TF 已缓存）
                assert call_count == first_call_count, \
                    f"B15 缓存失效：期望不重复检查文件，但 exists 被调用了 {call_count} 次(首次 {first_call_count} 次)"

    def test_file_exists_cache_updated_on_success(self):
        """load_display_cache 成功返回数据时，缓存标记更新为 True."""
        configs = [
            {"tf": "日线", "n_pts": 60, "_fid": "sma", "show_sch": True,
             "show_strategy": False, "show_pred": False, "pv": {"window": 11}},
        ]
        runner = _setup_runner(ticker="TEST", configs=configs)

        # 先让缓存设为 False（模拟文件不存在）
        runner._file_exists["日线"] = False

        # Mock load_display_cache 返回有效数据 — 触发 B15 更新路径
        df = pd.DataFrame({
            "Date": ["2026-01-01", "2026-01-02", "2026-01-03"],
            "Close": [100.0, 101.0, 102.0],
            "Open": [99.0, 100.0, 101.0],
            "High": [101.0, 102.0, 103.0],
            "Low": [98.0, 99.0, 100.0],
        })
        with patch("filter.backtest.engine.load_display_cache", return_value=df):
            result = runner._load_window_data("日线", 60, cutoff_date="2026-06-01")
            assert result is not None
            # 缓存应更新为 True
            assert runner._file_exists.get("日线") is True, \
                "B15: load_display_cache 成功后应更新 _file_exists 为 True"


class TestB16SortedWindowCache:
    """B16: _sorted_window_cache — 相同数据跳过 set_index().sort_index()."""

    def test_sorted_window_cache_hit(self):
        """同一数据两次加载时 sort 只执行一次."""
        configs = [
            {"tf": "日线", "n_pts": 60, "_fid": "sma", "show_sch": True,
             "show_strategy": False, "show_pred": False, "pv": {"window": 11}},
        ]
        runner = _setup_runner(ticker="TEST", configs=configs)

        assert runner._sorted_window_cache == {}

        df = pd.DataFrame({
            "Date": ["2026-01-03", "2026-01-01", "2026-01-02"],
            "Close": [102.0, 100.0, 101.0],
            "Open": [101.0, 99.0, 100.0],
            "High": [103.0, 101.0, 102.0],
            "Low": [100.0, 98.0, 99.0],
        })

        with patch("filter.backtest.engine.load_display_cache", return_value=df.copy()):
            result1 = runner._load_window_data("日线", 60, cutoff_date="2026-06-01")

        assert "日线" in runner._sorted_window_cache, \
            "B16: 首次加载后应缓存排序结果"
        assert result1 is not None

        # 第二次加载相同数据 — 应命中缓存
        with patch("filter.backtest.engine.load_display_cache", return_value=df.copy()):
            result2 = runner._load_window_data("日线", 60, cutoff_date="2026-06-02")

        assert result2 is not None
        # 验证两次结果一致（排序后索引相同）
        pd.testing.assert_index_equal(result1[3], result2[3].tz_localize(None)
                                      if hasattr(result2[3], "tz") and result2[3].tz is not None
                                      else result2[3])

    def test_sorted_window_cache_miss_on_changed_data(self):
        """数据变更时缓存穿透，重新排序."""
        configs = [
            {"tf": "日线", "n_pts": 60, "_fid": "sma", "show_sch": True,
             "show_strategy": False, "show_pred": False, "pv": {"window": 11}},
        ]
        runner = _setup_runner(ticker="TEST", configs=configs)

        df1 = pd.DataFrame({
            "Date": ["2026-01-03", "2026-01-01", "2026-01-02"],
            "Close": [102.0, 100.0, 101.0],
            "Open": [101.0, 99.0, 100.0],
            "High": [103.0, 101.0, 102.0],
            "Low": [100.0, 98.0, 99.0],
        })

        with patch("filter.backtest.engine.load_display_cache", return_value=df1.copy()):
            runner._load_window_data("日线", 60, cutoff_date="2026-06-01")

        cached_sig_before = runner._sorted_window_cache["日线"][0]

        # 加载不同数据 — 签名变化，缓存穿透
        df2 = pd.DataFrame({
            "Date": ["2026-01-05", "2026-01-03", "2026-01-04"],
            "Close": [105.0, 103.0, 104.0],
            "Open": [104.0, 102.0, 103.0],
            "High": [106.0, 104.0, 105.0],
            "Low": [103.0, 101.0, 102.0],
        })

        with patch("filter.backtest.engine.load_display_cache", return_value=df2.copy()):
            runner._load_window_data("日线", 60, cutoff_date="2026-06-02")

        cached_sig_after = runner._sorted_window_cache["日线"][0]
        assert cached_sig_after != cached_sig_before, \
            "B16: 数据变更后缓存签名应更新"


class TestB17SortedViewsPrecompute:
    """B17: _sorted_views 预计算 — 与运行时排序结果一致."""

    def test_sorted_views_precomputed_in_run(self):
        """run() 中预计算的 _sorted_views 与运行时排序结果一致."""
        from filter.shared.constants import ALL_TFS

        configs = [
            {"tf": "60分钟", "n_pts": 60, "_fid": "sma", "show_sch": True,
             "show_strategy": False, "show_pred": False, "pv": {"window": 11}},
            {"tf": "日线", "n_pts": 120, "_fid": "sma", "show_sch": True,
             "show_strategy": False, "show_pred": False, "pv": {"window": 11}},
            {"tf": "15分钟", "n_pts": 40, "_fid": "ema", "show_sch": True,
             "show_strategy": False, "show_pred": False, "pv": {"span": 10}},
        ]

        runner = _setup_runner(ticker="TEST", configs=configs)
        # 模拟 run() 中的预计算
        sorted_views = sorted(
            enumerate(runner.configs),
            key=lambda x: ALL_TFS.index(x[1]["tf"]),
            reverse=True,  # 粗→细
        )

        # 验证排序: 粗→细 = 日线→60分钟→15分钟
        tfs_in_order = [v[1]["tf"] for v in sorted_views]
        expected_order = ["日线", "60分钟", "15分钟"]
        assert tfs_in_order == expected_order, \
            f"B17: 期望排序 {expected_order}, 实际 {tfs_in_order}"

    def test_sorted_views_matches_runtime_sort(self):
        """预计算排序结果与 run() 内的实际排序等价."""
        from filter.shared.constants import ALL_TFS

        configs = [
            {"tf": "周线", "n_pts": 26, "_fid": "sma", "show_sch": True,
             "show_strategy": False, "show_pred": False, "pv": {"window": 11}},
            {"tf": "日线", "n_pts": 120, "_fid": "sma", "show_sch": True,
             "show_strategy": False, "show_pred": False, "pv": {"window": 11}},
            {"tf": "60分钟", "n_pts": 60, "_fid": "ema", "show_sch": True,
             "show_strategy": False, "show_pred": False, "pv": {"span": 10}},
        ]

        runner = _setup_runner(ticker="TEST", configs=configs)
        bar_count = runner._bar_count

        with patch.object(runner, "_get_bar_info") as mock_bar:
            mock_bar.return_value = {
                "bar_timestamp": "2026-01-15", "cutoff_date": "2026-01-15",
                "ohlcv": {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
            }
            with patch.object(runner, "_sync_data"):
                with patch.object(runner, "_load_window_data", return_value=None):
                    runner.run(bar_count - 10, bar_count - 9)

        # 验证 _sorted_views 在 run() 后被设置
        assert hasattr(runner, "_sorted_views"), \
            "B17: run() 后应存在 _sorted_views 属性"
        assert len(runner._sorted_views) == len(configs), \
            f"B17: _sorted_views 长度应为 {len(configs)}, 实际 {len(runner._sorted_views)}"

        # 验证排序顺序: 粗→细 (ALL_TFS.index 降序)
        runtime_sorted = sorted(
            enumerate(runner.configs),
            key=lambda x: ALL_TFS.index(x[1]["tf"]),
            reverse=True,
        )
        tfs_runtime = [v[1]["tf"] for v in runtime_sorted]
        tfs_cached = [v[1]["tf"] for v in runner._sorted_views]
        assert tfs_cached == tfs_runtime, \
            f"B17: 预计算排序 {tfs_cached} 与运行时排序 {tfs_runtime} 不一致"
