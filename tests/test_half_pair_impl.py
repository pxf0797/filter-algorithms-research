"""
B-cycle 半边多空对策略单元测试 -- contract tests

Tests the building-block functions that Phase 1 of the implementation plan
will introduce into services/filter_engine.py:

    _merge_segments              [已存在, lines 520-567]
    _find_current_half_pair      [待实现]
    _get_higher_tf_direction     [待实现]
    _signed_deviation            [待实现]
    _c_pair_state                [待实现]
    _run_half_pair_strategy      [待实现]
    _compute_strategy_pnl        [已存在, 测试 strategy_mode 兼容性]

这些测试定义了函数的预期行为契约（contract）。在 filter_engine.py
中实现对应函数后即可全部通过。
"""

import numpy as np
import pytest
from services.filter_engine import (
    _merge_segments,
    _find_current_half_pair,
    _get_higher_tf_direction,
    _signed_deviation,
    _c_pair_state,
    _run_half_pair_strategy,
    _compute_strategy_pnl,
    _find_all_pairs,
)


# ============================================================================
# _merge_segments  (已存在于 filter_engine.py L520)
# 返回 List[Tuple[int, int, int]] → [(start_idx, end_idx, direction), ...]
# ============================================================================

class TestMergeSegments:
    """_merge_segments 单元测试 -- 同号段合并。"""

    @pytest.mark.strategy
    def test_basic_merge(self):
        """(+1,0,+1) → 合并为一个 +1 段。"""
        sig_t = np.array([1, 1, 0, 0, 1, 1, 0, 0], dtype=int)
        merged = _merge_segments(sig_t)
        assert len(merged) == 1, f"Expected 1 segment, got {len(merged)}"
        start, end, direction = merged[0]
        assert direction == 1
        assert start == 0
        assert end == 5

    @pytest.mark.strategy
    def test_opposite_no_merge(self):
        """(+1,0,-1) → 不合并，保留两个独立段。"""
        sig_t = np.array([1, 1, 0, 0, -1, -1], dtype=int)
        merged = _merge_segments(sig_t)
        assert len(merged) == 2
        assert merged[0][2] == 1   # first segment: +1
        assert merged[1][2] == -1  # second segment: -1

    @pytest.mark.strategy
    def test_all_zero(self):
        """All zeros → []."""
        sig_t = np.array([0, 0, 0, 0], dtype=int)
        merged = _merge_segments(sig_t)
        assert merged == []

    @pytest.mark.strategy
    def test_n_less_than_2(self):
        """Single element → []."""
        sig_t = np.array([1], dtype=int)
        merged = _merge_segments(sig_t)
        assert merged == []


# ============================================================================
# _find_current_half_pair  [待实现]
# 签名: (sig_t, merged, edge_width=5) -> dict | None
# ============================================================================

class TestFindCurrentHalfPair:
    """_find_current_half_pair 单元测试。"""

    @pytest.mark.strategy
    def test_active(self):
        """正常半边：末尾有活跃非零信号且已锁定。"""
        sig_t = np.array([0, 0, 1, 1, 1, 1, 1, 1, 1], dtype=int)
        merged = _merge_segments(sig_t)
        result = _find_current_half_pair(sig_t, merged, edge_width=3)
        # start=2, current_idx=8, diff=6 > 3 → locked
        assert result is not None
        assert result["direction"] == 1
        assert result["is_locked"] is True

    @pytest.mark.strategy
    def test_none_trailing_zero(self):
        """末尾 0 → None。"""
        sig_t = np.array([0, 0, 0, 0, 0], dtype=int)
        merged = _merge_segments(sig_t)
        result = _find_current_half_pair(sig_t, merged, edge_width=5)
        assert result is None

    @pytest.mark.strategy
    def test_locked(self):
        """起点距右边缘 > edge_width → is_locked=True。"""
        sig_t = np.array([0, 0, 1, 0, 1, 1, 1], dtype=int)  # merged=[(2,2,1),(4,6,1)]
        merged = _merge_segments(sig_t)
        # start=4, current_idx=6, diff=2, edge_width=1 → 2>1 → locked
        result = _find_current_half_pair(sig_t, merged, edge_width=1)
        assert result is not None
        assert result["direction"] == 1
        assert result["is_locked"] is True

    @pytest.mark.strategy
    def test_not_locked(self):
        """起点在边缘区内 → is_locked=False。"""
        sig_t = np.array([0, 0, 0, 0, 1, 1], dtype=int)
        merged = _merge_segments(sig_t)
        # start=4, current_idx=5, diff=1 <= edge_width=3 → not locked
        result = _find_current_half_pair(sig_t, merged, edge_width=3)
        assert result is not None
        assert result["direction"] == 1
        assert result["is_locked"] is False


# ============================================================================
# _get_higher_tf_direction  [待实现]
# 签名: (higher_sig: np.ndarray) -> int
# ============================================================================

class TestGetHigherTfDirection:
    """_get_higher_tf_direction 单元测试。"""

    @pytest.mark.strategy
    def test_positive(self):
        """末尾 +1 → 1。"""
        sig = np.array([-1, -1, 1, 1, 1], dtype=int)
        result = _get_higher_tf_direction(sig)
        assert result == 1

    @pytest.mark.strategy
    def test_negative(self):
        """末尾 -1 → -1。"""
        sig = np.array([1, 1, -1, -1], dtype=int)
        result = _get_higher_tf_direction(sig)
        assert result == -1

    @pytest.mark.strategy
    def test_none(self):
        """全 0 或空 → 0。"""
        sig = np.array([0, 0, 0, 0], dtype=int)
        result = _get_higher_tf_direction(sig)
        assert result == 0

    @pytest.mark.strategy
    def test_all_zeros_none_input(self):
        """None 输入 → 0。"""
        result = _get_higher_tf_direction(None)
        assert result == 0


# ============================================================================
# _signed_deviation  [待实现]
# 签名: (price, fit_values, direction) -> float
# ============================================================================

class TestSignedDeviation:
    """_signed_deviation 单元测试。"""

    @pytest.mark.strategy
    def test_long_deviation(self):
        """做多偏离：price > fit → 正偏离。"""
        price, fit, direction = 90.0, 100.0, 1
        result = _signed_deviation(price, fit, direction)
        assert result > 0  # 做多跌到预测下方: (100-90)/100=0.1 不利偏离

    @pytest.mark.strategy
    def test_short_deviation(self):
        """做空偏离：price > fit → 不利于空头。"""
        price, fit, direction = 110.0, 100.0, -1
        result = _signed_deviation(price, fit, direction)
        assert result > 0  # 做空: (price-pred)/pred = (110-100)/100 = 0.1 > 0 不利于空头

    @pytest.mark.strategy
    def test_zero_direction(self):
        """direction=0 → 0."""
        price, fit, direction = 100.0, 100.0, 0
        result = _signed_deviation(price, fit, direction)
        assert result == 0.0


# ============================================================================
# _c_pair_state  [待实现 -- MVP 3态: STRONG_ALIGN / MISALIGN / NO_DIRECTION]
# 签名: (higher_sig, higher_half_pair, dir_B) -> str
# ============================================================================

class TestCPairStateMVP:
    """_c_pair_state MVP 3态单元测试。"""

    @pytest.mark.strategy
    def test_strong_align(self):
        """C 方向与 B 同向 → STRONG_ALIGN。"""
        sig_C = np.array([0, 0, 1, 1, 1])
        result = _c_pair_state(sig_C, None, None, d_B=1)
        assert result == "STRONG_ALIGN"

    @pytest.mark.strategy
    def test_misalign(self):
        """C 方向与 B 反向 → MISALIGN。"""
        sig_C = np.array([0, 0, 1, 1, 1])
        result = _c_pair_state(sig_C, None, None, d_B=-1)
        assert result == "MISALIGN"

    @pytest.mark.strategy
    def test_no_direction(self):
        """C 全0 → NO_DIRECTION。"""
        sig_C = np.array([0, 0, 0, 0])
        result = _c_pair_state(sig_C, None, None, d_B=1)
        assert result == "NO_DIRECTION"


# ============================================================================
# _run_half_pair_strategy  [待实现]
# 签名: (t, filtered, sig_t, half_pair, higher_dir, pred_pairs, params, n_extend)
# 返回: list[dict]  trade signals
# ============================================================================

class TestRunHalfPairStrategy:
    """_run_half_pair_strategy 单元测试。"""

    @pytest.mark.strategy
    def test_entry_c_confirm(self):
        """C 同向 → 入场。"""
        n = 50
        t = np.arange(n, dtype=float)
        filtered = np.ones(n) * 100.0
        sig_t = np.zeros(n, dtype=int)
        sig_t[10:] = 1
        half_pair = {"start": 10, "end_idx": 49, "direction": 1, "is_locked": True}
        higher_dir = 1
        params = {"N_confirm": 2, "MAX_DEV_PCT": 5.0}
        signals = _run_half_pair_strategy(
            t, filtered, sig_t, half_pair, higher_dir, [],
            params, n_extend=5,
        )
        assert len(signals) >= 1
        assert signals[0]["direction"] == 1

    @pytest.mark.strategy
    def test_no_entry_c_reverse(self):
        """C 反向 → 不入。"""
        n = 50
        t = np.arange(n, dtype=float)
        filtered = np.ones(n) * 100.0
        sig_t = np.zeros(n, dtype=int)
        sig_t[10:] = 1
        half_pair = {"start": 10, "end_idx": 49, "direction": 1, "is_locked": True}
        higher_dir = -1  # C opposes
        params = {"N_confirm": 2, "MAX_DEV_PCT": 5.0}
        signals = _run_half_pair_strategy(
            t, filtered, sig_t, half_pair, higher_dir, [],
            params, n_extend=5,
        )
        assert len(signals) == 0

    @pytest.mark.strategy
    def test_exit_take_profit(self):
        """反向确认 ≥ N_CONFIRM → 离场（take_profit）。"""
        n = 50
        t = np.arange(n, dtype=float)
        filtered = np.ones(n) * 100.0
        sig_t = np.zeros(n, dtype=int)
        sig_t[10:30] = 1       # long
        sig_t[30:] = -1         # reverses at 30
        half_pair = {"start": 10, "end_idx": 49, "direction": 1, "is_locked": True}
        higher_dir = 1
        params = {"N_confirm": 2, "MAX_DEV_PCT": 5.0}
        signals = _run_half_pair_strategy(
            t, filtered, sig_t, half_pair, higher_dir, [],
            params, n_extend=5,
        )
        assert len(signals) >= 1
        # 应该有离场事件
        s = signals[0]
        assert "exit_reason" in s, "Expected an exit_reason in the trade signal"
        assert s["exit_reason"] in ("take_profit",)

    @pytest.mark.strategy
    def test_exit_trend_deviation(self):
        """偏离 > MAX_DEV_PCT → 离场（deviation）。"""
        n = 50
        t = np.arange(n, dtype=float)
        # 价格在入场后大幅远离预测
        filtered = np.ones(n) * 100.0
        filtered[18:] = 82.0  # -18% from entry, well past 5% max dev
        sig_t = np.zeros(n, dtype=int)
        sig_t[10:] = 1
        half_pair = {"start": 10, "end_idx": 49, "direction": 1, "is_locked": True}
        higher_dir = 1
        params = {"N_confirm": 2, "MAX_DEV_PCT": 3.0}  # tight threshold
        # 提供拟合结果以便函数计算偏离
        fit_result = {"a": 0.0, "b": 0.0, "c": 100.0,
                      "y_fit": np.full(n, 100.0), "x0": 10}
        pred_pairs = [{"fit_result": fit_result, "fit_start": 5, "pair_end": 9}]
        signals = _run_half_pair_strategy(
            t, filtered, sig_t, half_pair, higher_dir,
            pred_pairs, params, n_extend=5,
        )
        assert len(signals) >= 1
        s = signals[0]
        assert "exit_reason" in s
        # deviation 或 stop_loss 均可（取决于实现细节）
        assert s["exit_reason"] in ("trend_deviation", "stop_loss")

    @pytest.mark.strategy
    def test_n_confirm_buffer(self):
        """单根反向 bar 不触发离场（N_CONFIRM 缓冲）。"""
        n = 50
        t = np.arange(n, dtype=float)
        filtered = np.ones(n) * 100.0
        sig_t = np.zeros(n, dtype=int)
        sig_t[10:25] = 1       # long
        sig_t[25] = -1          # single reverse bar
        sig_t[26:] = 1          # back to long
        half_pair = {"start": 10, "end_idx": 49, "direction": 1, "is_locked": True}
        higher_dir = 1
        params = {"N_confirm": 3, "MAX_DEV_PCT": 5.0}  # needs 3 consecutive
        signals = _run_half_pair_strategy(
            t, filtered, sig_t, half_pair, higher_dir, [],
            params, n_extend=5,
        )
        assert len(signals) >= 1
        # 可能存在 exit，但不应是因为反向确认（仅1根反向 bar）
        for s in signals:
            if "exit_reason" in s:
                assert s["exit_reason"] not in ("take_profit",)

    @pytest.mark.strategy
    def test_direction_zero(self):
        """direction=0 → 空列表。"""
        n = 30
        t = np.arange(n, dtype=float)
        filtered = np.ones(n) * 100.0
        sig_t = np.zeros(n, dtype=int)
        half_pair = {"start": 0, "end_idx": 29, "direction": 0, "is_locked": False}
        higher_dir = 0
        params = {"N_confirm": 2, "MAX_DEV_PCT": 5.0}
        signals = _run_half_pair_strategy(
            t, filtered, sig_t, half_pair, higher_dir, [],
            params, n_extend=5,
        )
        assert signals == []


# ============================================================================
# _compute_strategy_pnl -- strategy_mode 兼容性
# 已存在于 filter_engine.py L649
# ============================================================================

class TestComputeStrategyPnLDefault:
    """验证 strategy_mode 参数不影响默认路径。"""

    @pytest.mark.strategy
    def test_default_mode_unchanged(self):
        """strategy_mode=None → 行为不变，不崩溃。"""
        t = np.arange(50, dtype=float)
        filtered = np.ones(50) * 100.0
        sig_t = np.zeros(50, dtype=int)
        all_pairs = _find_all_pairs(sig_t)
        long_pnl, short_pnl, trades = _compute_strategy_pnl(
            t, filtered, sig_t, all_pairs, [],
            stop_loss_pct=2.0, n_extend=10,
        )
        assert np.allclose(long_pnl, 100.0)
        assert np.allclose(short_pnl, 100.0)
        assert trades == []
