"""
T1 修改测试：engine.py `_compute_and_log_metrics` 全时序 PnL 聚合 &
`save_checkpoint` bar_index 参数.

验证多 step/多 view 聚合正确性，以及断点 bar_index 字段。
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ── 确保 filter/ 可导入 (conftest.py 也会做，这里保险) ──
_src = Path(__file__).resolve().parent.parent / "filter"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))


# ═══════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════

def _make_step_result(step_idx: int, views_pnl: list[dict]) -> dict:
    """构造单步结果 dict，包含指定 views 的 PnL 数据。

    Parameters
    ----------
    step_idx : int
        步索引。
    views_pnl : list[dict]
        每个元素为 {"long_pnl": np.ndarray, "short_pnl": np.ndarray,
        "trade_records": list, "t_len": int}
    """
    views = {}
    for vi, vp in enumerate(views_pnl):
        lp = vp["long_pnl"]
        t_len = vp.get("t_len", len(lp) if lp is not None else 0)
        views[f"v{vi}_tf"] = {
            "t": np.arange(t_len, dtype=float),
            "long_pnl": lp,
            "short_pnl": vp["short_pnl"],
            "trade_records": vp.get("trade_records", []),
        }
    return {
        "step_index": step_idx,
        "bar_index": step_idx,
        "bar_timestamp": "2026-01-15",
        "cutoff_date": "2026-01-15",
        "views": views,
        "ohlcv": {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000},
    }


def _make_mock_db_conn(bar_count=500):
    """创建 mock DB 连接。"""
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.__exit__.return_value = False
    mock_row = MagicMock()
    mock_row.__getitem__.return_value = bar_count
    mock_conn.execute.return_value.fetchone.return_value = mock_row
    return mock_conn


def _make_minimal_configs():
    """最小视图配置。"""
    return [
        {
            "_fid": "sma", "tf": "日线", "n_pts": 120,
            "show_sch": False, "show_strategy": False, "show_pred": False,
            "ke": 0.15, "sm": 0.05, "ew": 60,
            "pv": {"window": 11}, "pv2": {},
        },
    ]


# ═══════════════════════════════════════════════════════════════════════════
# Part A-1: _compute_and_log_metrics — 全时序 PnL 聚合测试
# ═══════════════════════════════════════════════════════════════════════════

class TestComputeAndLogMetricsAggregation:
    """验证 _compute_and_log_metrics 的多 step × 多 view 聚合行为。"""

    def test_single_step_single_view(self):
        """单 step 单 view 场景：聚合后 PnL = 原始 PnL（回归测试）。"""
        from filter.backtest.engine import _compute_and_log_metrics

        long_pnl = np.array([100.0, 101.0, 102.0, 103.0])
        short_pnl = np.array([100.0, 100.0, 100.0, 100.0])
        results = [_make_step_result(0, [
            {"long_pnl": long_pnl, "short_pnl": short_pnl, "t_len": 4},
        ])]

        with patch("filter.backtest.engine.logger") as mock_logger, \
             patch("filter.backtest.metrics.compute_backtest_metrics") as mock_compute:
            mock_compute.return_value = {
                "total_return_pct": 3.0, "sharpe_ratio": 1.5,
                "max_drawdown_pct": 0.0, "total_trades": 0, "win_rate_pct": 0.0,
            }
            _compute_and_log_metrics(results, "TEST")

        # 验证 compute_backtest_metrics 被调用
        mock_compute.assert_called_once()
        call_args = mock_compute.call_args[0]
        # 聚合后的 PnL 应等于原始值
        assert np.array_equal(call_args[0], long_pnl)
        assert np.array_equal(call_args[1], short_pnl)
        # total_bars 应为 4
        assert call_args[3] == 4

    def test_multi_step_single_view_aggregation(self):
        """多 step 单 view 场景：metrics 基于所有 step 的 PnL 拼接计算。"""
        from filter.backtest.engine import _compute_and_log_metrics

        # Step 0: 3 bar PnL [100, 101, 102]
        step0_long = np.array([100.0, 101.0, 102.0])
        step0_short = np.array([100.0, 100.0, 100.0])

        # Step 1: 3 bar PnL [102, 103, 104] (延续)
        step1_long = np.array([102.0, 103.0, 104.0])
        step1_short = np.array([100.0, 100.0, 100.0])

        results = [
            _make_step_result(0, [
                {"long_pnl": step0_long, "short_pnl": step0_short, "t_len": 3},
            ]),
            _make_step_result(1, [
                {"long_pnl": step1_long, "short_pnl": step1_short, "t_len": 3},
            ]),
        ]

        with patch("filter.backtest.engine.logger"), \
             patch("filter.backtest.metrics.compute_backtest_metrics") as mock_compute:
            mock_compute.return_value = {
                "total_return_pct": 4.0, "sharpe_ratio": 2.0,
                "max_drawdown_pct": 0.0, "total_trades": 0, "win_rate_pct": 0.0,
            }
            _compute_and_log_metrics(results, "TEST")

        call_args = mock_compute.call_args[0]
        # 聚合 long_pnl = [100, 101, 102, 102, 103, 104]
        expected_long = np.concatenate([step0_long, step1_long])
        assert np.array_equal(call_args[0], expected_long), (
            f"Expected {expected_long}, got {call_args[0]}"
        )
        # 总 bar 数 = 3 + 3 = 6
        assert call_args[3] == 6

    def test_single_step_multi_view_aggregation(self):
        """单 step 多 view 场景：metrics 基于所有 view 的 PnL 拼接计算。"""
        from filter.backtest.engine import _compute_and_log_metrics

        view0_long = np.array([100.0, 101.0, 102.0])
        view0_short = np.array([100.0, 100.0, 100.0])
        view1_long = np.array([200.0, 201.0, 202.0])
        view1_short = np.array([200.0, 200.0, 200.0])

        results = [_make_step_result(0, [
            {"long_pnl": view0_long, "short_pnl": view0_short, "t_len": 3},
            {"long_pnl": view1_long, "short_pnl": view1_short, "t_len": 3},
        ])]

        with patch("filter.backtest.engine.logger"), \
             patch("filter.backtest.metrics.compute_backtest_metrics") as mock_compute:
            mock_compute.return_value = {
                "total_return_pct": 2.0, "sharpe_ratio": 0.5,
                "max_drawdown_pct": 0.0, "total_trades": 0, "win_rate_pct": 0.0,
            }
            _compute_and_log_metrics(results, "TEST")

        call_args = mock_compute.call_args[0]
        # 聚合 long_pnl = [100,101,102, 200,201,202]
        expected_long = np.concatenate([view0_long, view1_long])
        assert np.array_equal(call_args[0], expected_long), (
            f"Expected {expected_long}, got {call_args[0]}"
        )
        assert call_args[3] == 6

    def test_multi_step_multi_view_aggregation(self):
        """多 step 多 view 场景：所有 step × view 的 PnL 全部拼接。"""
        from filter.backtest.engine import _compute_and_log_metrics

        # Step 0: 2 views
        s0_v0_long = np.array([100.0, 101.0])
        s0_v0_short = np.full(2, 100.0)
        s0_v1_long = np.array([200.0, 201.0])
        s0_v1_short = np.full(2, 200.0)

        # Step 1: 2 views
        s1_v0_long = np.array([102.0, 103.0])
        s1_v0_short = np.full(2, 100.0)
        s1_v1_long = np.array([202.0, 203.0])
        s1_v1_short = np.full(2, 200.0)

        results = [
            _make_step_result(0, [
                {"long_pnl": s0_v0_long, "short_pnl": s0_v0_short, "t_len": 2},
                {"long_pnl": s0_v1_long, "short_pnl": s0_v1_short, "t_len": 2},
            ]),
            _make_step_result(1, [
                {"long_pnl": s1_v0_long, "short_pnl": s1_v0_short, "t_len": 2},
                {"long_pnl": s1_v1_long, "short_pnl": s1_v1_short, "t_len": 2},
            ]),
        ]

        with patch("filter.backtest.engine.logger"), \
             patch("filter.backtest.metrics.compute_backtest_metrics") as mock_compute:
            mock_compute.return_value = {
                "total_return_pct": 103.0, "sharpe_ratio": 3.0,
                "max_drawdown_pct": 0.0, "total_trades": 0, "win_rate_pct": 0.0,
            }
            _compute_and_log_metrics(results, "TEST")

        call_args = mock_compute.call_args[0]
        expected_long = np.concatenate([
            s0_v0_long, s0_v1_long, s1_v0_long, s1_v1_long,
        ])
        assert np.array_equal(call_args[0], expected_long), (
            f"Expected length {len(expected_long)}, got {len(call_args[0])}"
        )
        # total_bars = 2+2+2+2 = 8
        assert call_args[3] == 8

    def test_aggregated_pnl_length_equals_sum_of_segments(self):
        """聚合后 PnL 序列长度 = 所有 step × view 的 t 数组长度之和。"""
        from filter.backtest.engine import _compute_and_log_metrics

        segments = []
        total_len = 0
        step_results = []
        for si in range(3):  # 3 steps
            views = []
            for vi in range(2):  # 2 views
                n = (si + 1) * (vi + 1) * 5  # varying lengths
                lp = np.arange(100, 100 + n, dtype=float)
                sp = np.full(n, 100.0)
                segments.append(lp)
                total_len += n
                views.append({"long_pnl": lp, "short_pnl": sp, "t_len": n})
            step_results.append(_make_step_result(si, views))

        with patch("filter.backtest.engine.logger"), \
             patch("filter.backtest.metrics.compute_backtest_metrics") as mock_compute:
            mock_compute.return_value = {
                "total_return_pct": 10.0, "sharpe_ratio": 2.0,
                "max_drawdown_pct": -5.0, "total_trades": 5, "win_rate_pct": 60.0,
            }
            _compute_and_log_metrics(step_results, "TEST")

        call_args = mock_compute.call_args[0]
        assert len(call_args[0]) == total_len, (
            f"Aggregated PnL length {len(call_args[0])} != total segments {total_len}"
        )

    def test_aggregated_short_pnl_length_equals_long(self):
        """聚合后 short_pnl 长度必须与 long_pnl 长度一致。"""
        from filter.backtest.engine import _compute_and_log_metrics

        results = [
            _make_step_result(0, [
                {"long_pnl": np.array([100.0, 101.0]), "short_pnl": np.array([100.0, 100.0]), "t_len": 2},
            ]),
            _make_step_result(1, [
                {"long_pnl": np.array([102.0, 103.0, 104.0]), "short_pnl": np.array([100.0, 100.0, 100.0]), "t_len": 3},
            ]),
        ]

        with patch("filter.backtest.engine.logger"), \
             patch("filter.backtest.metrics.compute_backtest_metrics") as mock_compute:
            mock_compute.return_value = {
                "total_return_pct": 4.0, "sharpe_ratio": 1.0,
                "max_drawdown_pct": 0.0, "total_trades": 0, "win_rate_pct": 0.0,
            }
            _compute_and_log_metrics(results, "TEST")

        call_args = mock_compute.call_args[0]
        assert len(call_args[0]) == len(call_args[1]), (
            f"long_pnl length {len(call_args[0])} != short_pnl length {len(call_args[1])}"
        )

    def test_trade_records_aggregated_across_steps_and_views(self):
        """多 step × 多 view 场景：所有 trade_records 被合并。"""
        from filter.backtest.engine import _compute_and_log_metrics

        tr0 = [{"return_pct": 1.0}]
        tr1 = [{"return_pct": 2.0}, {"return_pct": -1.0}]
        tr2 = [{"return_pct": 0.5}]

        results = [
            _make_step_result(0, [
                {"long_pnl": np.array([100.0, 101.0]), "short_pnl": np.full(2, 100.0),
                 "trade_records": tr0, "t_len": 2},
            ]),
            _make_step_result(1, [
                {"long_pnl": np.array([100.0, 101.0]), "short_pnl": np.full(2, 100.0),
                 "trade_records": tr1, "t_len": 2},
                {"long_pnl": np.array([100.0, 101.0]), "short_pnl": np.full(2, 100.0),
                 "trade_records": tr2, "t_len": 2},
            ]),
        ]

        with patch("filter.backtest.engine.logger"), \
             patch("filter.backtest.metrics.compute_backtest_metrics") as mock_compute:
            mock_compute.return_value = {
                "total_return_pct": 2.5, "sharpe_ratio": 1.0,
                "max_drawdown_pct": 0.0, "total_trades": 4, "win_rate_pct": 75.0,
            }
            _compute_and_log_metrics(results, "TEST")

        call_args = mock_compute.call_args[0]
        all_records = call_args[2]
        assert len(all_records) == 4  # 1 + 2 + 1

    def test_empty_results_noop(self):
        """空 results 列表不抛异常、不调用 compute。"""
        from filter.backtest.engine import _compute_and_log_metrics

        with patch("filter.backtest.metrics.compute_backtest_metrics") as mock_compute:
            _compute_and_log_metrics([], "TEST")
            mock_compute.assert_not_called()

    def test_results_with_no_pnl_views_skipped(self):
        """所有 view 都无 PnL（long_pnl=None）时跳过计算。"""
        from filter.backtest.engine import _compute_and_log_metrics

        results = [_make_step_result(0, [
            {"long_pnl": None, "short_pnl": None, "t_len": 0},
            {"long_pnl": None, "short_pnl": None, "t_len": 0},
        ])]

        with patch("filter.backtest.metrics.compute_backtest_metrics") as mock_compute:
            _compute_and_log_metrics(results, "TEST")
            mock_compute.assert_not_called()

    def test_mixed_pnl_some_none_views(self):
        """部分 view PnL 为 None 时只聚合有效的。"""
        from filter.backtest.engine import _compute_and_log_metrics

        valid_long = np.array([100.0, 101.0, 102.0])
        valid_short = np.full(3, 100.0)

        results = [_make_step_result(0, [
            {"long_pnl": None, "short_pnl": None, "t_len": 0},  # skipped
            {"long_pnl": valid_long, "short_pnl": valid_short, "t_len": 3},  # used
        ])]

        with patch("filter.backtest.engine.logger"), \
             patch("filter.backtest.metrics.compute_backtest_metrics") as mock_compute:
            mock_compute.return_value = {
                "total_return_pct": 2.0, "sharpe_ratio": 0.5,
                "max_drawdown_pct": 0.0, "total_trades": 0, "win_rate_pct": 0.0,
            }
            _compute_and_log_metrics(results, "TEST")

        call_args = mock_compute.call_args[0]
        assert np.array_equal(call_args[0], valid_long)
        assert len(call_args[2]) == 0

    def test_metrics_log_output_format(self):
        """验证日志输出包含预期的指标字段。"""
        from filter.backtest.engine import _compute_and_log_metrics

        long_pnl = np.array([100.0, 105.0, 110.0])
        short_pnl = np.full(3, 100.0)
        results = [_make_step_result(0, [
            {"long_pnl": long_pnl, "short_pnl": short_pnl, "t_len": 3},
        ])]

        with patch("filter.backtest.engine.logger") as mock_logger, \
             patch("filter.backtest.metrics.compute_backtest_metrics") as mock_compute:
            mock_compute.return_value = {
                "total_return_pct": 10.0,
                "sharpe_ratio": 2.5,
                "max_drawdown_pct": 0.0,
                "total_trades": 3,
                "win_rate_pct": 66.7,
            }
            _compute_and_log_metrics(results, "TEST")

            # 验证日志行包含关键指标占位符
            log_call = mock_logger.info.call_args
            assert log_call is not None
            log_message = log_call[0][0]
            # logger.info uses format-string with placeholder args, so check format string
            assert "ticker=" in log_message
            assert "total_return" in log_message
            assert "sharpe" in log_message
            assert "max_dd" in log_message
            # 验证第二个参数是 ticker
            assert log_call[0][1] == "TEST"

    def test_compute_exception_is_caught(self):
        """compute_backtest_metrics 抛异常时 logger.debug 记录不崩溃。"""
        from filter.backtest.engine import _compute_and_log_metrics

        long_pnl = np.array([100.0, 101.0])
        short_pnl = np.full(2, 100.0)
        results = [_make_step_result(0, [
            {"long_pnl": long_pnl, "short_pnl": short_pnl, "t_len": 2},
        ])]

        with patch("filter.backtest.engine.logger") as mock_logger, \
             patch("filter.backtest.metrics.compute_backtest_metrics",
                   side_effect=RuntimeError("calc error")):
            # 不应抛异常
            _compute_and_log_metrics(results, "TEST")
            mock_logger.debug.assert_called()


# ═══════════════════════════════════════════════════════════════════════════
# Part A-2: save_checkpoint bar_index 参数测试
# ═══════════════════════════════════════════════════════════════════════════

class TestSaveCheckpointBarIndex:
    """验证 save_checkpoint 的 bar_index 参数。"""

    def test_bar_index_100_in_checkpoint(self, tmp_path):
        """传入 bar_index=100，检查点文件中 bar_index 字段为 100。"""
        from filter.backtest.engine import BacktestRunner

        configs = _make_minimal_configs()
        mock_conn = _make_mock_db_conn(bar_count=500)

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("TEST", configs)

        cp_path = str(tmp_path / "checkpoint_bar100.json")
        runner.save_checkpoint(cp_path, bar_index=100)

        with open(cp_path) as f:
            saved = json.load(f)

        assert saved["bar_index"] == 100, (
            f"Expected bar_index=100, got {saved['bar_index']}"
        )

    def test_bar_index_default_zero_backward_compat(self, tmp_path):
        """默认参数 bar_index=0：检查点文件 bar_index 字段为 0（向后兼容）。"""
        from filter.backtest.engine import BacktestRunner

        configs = _make_minimal_configs()
        mock_conn = _make_mock_db_conn(bar_count=500)

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("TEST", configs)

        cp_path = str(tmp_path / "checkpoint_default.json")
        # 不传 bar_index，应使用默认值 0
        runner.save_checkpoint(cp_path)

        with open(cp_path) as f:
            saved = json.load(f)

        assert saved["bar_index"] == 0, (
            f"Default bar_index should be 0, got {saved['bar_index']}"
        )

    def test_bar_index_arbitrary_value(self, tmp_path):
        """任意 bar_index 值都应正确序列化到检查点。"""
        from filter.backtest.engine import BacktestRunner

        configs = _make_minimal_configs()
        mock_conn = _make_mock_db_conn(bar_count=1000)

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("TEST", configs)

        for val in [0, 1, 42, 999]:
            cp_path = str(tmp_path / f"checkpoint_bar{val}.json")
            runner.save_checkpoint(cp_path, bar_index=val)

            with open(cp_path) as f:
                saved = json.load(f)
            assert saved["bar_index"] == val

    def test_checkpoint_contains_expected_keys(self, tmp_path):
        """检查点文件包含所有预期字段: bar_index, ewma_state, bar_count, config_hash。"""
        from filter.backtest.engine import BacktestRunner

        configs = _make_minimal_configs()
        mock_conn = _make_mock_db_conn(bar_count=500)

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("TEST", configs)

        cp_path = str(tmp_path / "checkpoint_keys.json")
        runner.save_checkpoint(cp_path, bar_index=99)

        with open(cp_path) as f:
            saved = json.load(f)

        assert "bar_index" in saved
        assert "ewma_state" in saved
        assert "bar_count" in saved
        assert "config_hash" in saved
        assert isinstance(saved["config_hash"], str)
        assert len(saved["config_hash"]) == 64  # SHA256

    def test_existing_checkpoint_tests_still_pass(self, tmp_path):
        """回归：现有 save/restore checkpoint 测试应兼容 bar_index 参数。"""
        from filter.backtest.engine import BacktestRunner

        configs = _make_minimal_configs()
        mock_conn = _make_mock_db_conn(bar_count=500)

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("TEST", configs)

        runner._ewma_state = {
            "v0_日线": {"init_mu": 100.5, "init_sigma": 2.3, "state": 1, "dur": 5},
        }

        cp_path = str(tmp_path / "checkpoint_compat.json")
        state = runner.save_checkpoint(cp_path)
        assert Path(cp_path).exists()

        # 默认 bar_index=0
        assert state["bar_index"] == 0

        # 新 runner 可以恢复
        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner2 = BacktestRunner("TEST", configs)
        resume_bar = runner2._restore_checkpoint(cp_path, configs)
        assert resume_bar == 0
        assert runner2._ewma_state["v0_日线"]["init_mu"] == 100.5

    def test_save_checkpoint_bar_index_preserves_other_state(self, tmp_path):
        """bar_index 参数不影响 EWMA 状态、bar_count、config_hash 等其他字段。"""
        from filter.backtest.engine import BacktestRunner

        configs = _make_minimal_configs()
        mock_conn = _make_mock_db_conn(bar_count=777)

        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            runner = BacktestRunner("TEST", configs)

        runner._ewma_state = {
            "v0_日线": {"init_mu": 50.0, "init_sigma": 1.5, "state": -1, "dur": 10},
        }

        cp_path = str(tmp_path / "checkpoint_state.json")
        runner.save_checkpoint(cp_path, bar_index=42)

        with open(cp_path) as f:
            saved = json.load(f)

        assert saved["bar_index"] == 42
        assert saved["bar_count"] == 777
        assert saved["ewma_state"]["v0_日线"]["state"] == -1
        assert saved["ewma_state"]["v0_日线"]["dur"] == 10


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
