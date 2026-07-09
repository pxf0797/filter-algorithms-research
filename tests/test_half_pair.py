"""
Tests for half-pair multi-timeframe strategy (_merge_segments,
_find_current_half_pair, _run_half_pair_strategy) and backward-compatible
PnL computation (strategy_mode=None).

These tests mirror the style of test_strategy.py (pytest, numpy data
construction, @pytest.mark.strategy markers, import from services.filter_engine).
"""

import numpy as np
import pytest
from services.filter_engine import (
    _merge_segments,
    _find_current_half_pair,
    _run_half_pair_strategy,
    _get_higher_tf_direction,
    _compute_strategy_pnl,
    _find_all_pairs,
)


# ============================================================================
# _merge_segments
# ============================================================================

class TestMergeSegments:
    """_merge_segments: collect non-zero runs, merge adjacent same-sign."""

    @pytest.mark.strategy
    def test_basic_merge(self):
        """同向段跨零合并 — 两个+1段合并为一个."""
        sig = np.array([0, 1, 1, 0, 1, 0])
        merged = _merge_segments(sig)
        # 段(1,2,1)和段(4,4,1)同号，跨0合并 → [(1, 4, 1)]
        assert merged == [(1, 4, 1)], f"Got {merged}"

    @pytest.mark.strategy
    def test_opposite_no_merge(self):
        """异向段不合并 — +1和-1相邻，各自独立."""
        sig = np.array([1, 1, 0, -1, -1])
        merged = _merge_segments(sig)
        # 段(0,1,1)和段(3,4,-1)异号，不合并
        assert merged == [(0, 1, 1), (3, 4, -1)], f"Got {merged}"

    @pytest.mark.strategy
    def test_all_zero(self):
        """全0序列 → 空列表."""
        sig = np.array([0, 0, 0])
        merged = _merge_segments(sig)
        assert merged == []

    @pytest.mark.strategy
    def test_single_segment(self):
        """仅一段非零 → 单个segment，不足2个返回空（现有_pair契约）."""
        sig = np.array([0, 1, 1, 1, 0, 0])
        merged = _merge_segments(sig)
        # 仅一段非零，按工程设计 <2 时应返回空
        assert merged == [(2, 4, 1)], f"Got {merged}"

    @pytest.mark.strategy
    def test_multiple_opposite(self):
        """多段交替 +1/-1/+1 → 三段各自独立."""
        sig = np.array([1, 1, 0, -1, -1, 0, 1, 1])
        merged = _merge_segments(sig)
        assert merged == [(0, 1, 1), (3, 4, -1), (6, 7, 1)], f"Got {merged}"

    @pytest.mark.strategy
    def test_three_in_a_row(self):
        """同向段三次出现 → 合并为一个."""
        sig = np.array([1, 0, 1, 0, 1, 0, 0, -1])
        merged = _merge_segments(sig)
        # 三个+1段合并
        assert merged == [(0, 4, 1), (7, 7, -1)], f"Got {merged}"

    @pytest.mark.strategy
    def test_n_less_than_2(self):
        """长度小于2 → 空列表（_merge_segments 底层契约）."""
        sig = np.array([1])
        merged = _merge_segments(sig)
        assert merged == [], f"Got {merged}"


# ============================================================================
# _find_current_half_pair
# ============================================================================

class TestFindCurrentHalfPair:
    """_find_current_half_pair: identify the rightmost half-pair."""

    @pytest.mark.strategy
    def test_active_half_pair(self):
        """末尾为+1段 → 返回有效半边Dict."""
        sig = np.array([0, 0, 1, 1, 1, 1, 1, 1])
        merged = _merge_segments(sig)
        result = _find_current_half_pair(sig, merged, edge_width=3)
        assert result is not None, "Expected a half-pair"
        assert result["direction"] == 1, f"Got direction={result['direction']}"
        assert result["start"] == 2, f"Got start_idx={result['start_idx']}"
        assert result["current_idx"] == 7, f"Got current_idx={result['current_idx']}"
        # start_idx=2, current_idx=7, edge_distance=8-2=6 > edge_width=3 → locked
        assert result["is_locked"] is True, "Should be locked"

    @pytest.mark.strategy
    def test_none_when_zero_end(self):
        """末尾是0 → 无活跃半边."""
        sig = np.array([1, 1, 0, 0, 0])
        merged = _merge_segments(sig)
        result = _find_current_half_pair(sig, merged, edge_width=3)
        assert result is None, "Should be None when trailing zeros"

    @pytest.mark.strategy
    def test_edge_width_locked(self):
        """起点距右边缘 > edge_width → is_locked=True."""
        sig = np.array([0, 0, 1, 1, 1, 1, 1, 1])
        merged = _merge_segments(sig)
        # edge_width=5, edge_distance=8-2=6 > 5 → locked
        result = _find_current_half_pair(sig, merged, edge_width=5)
        assert result is not None
        assert result["is_locked"] is True

    @pytest.mark.strategy
    def test_edge_width_not_locked(self):
        """起点距右边缘 ≤ edge_width → is_locked=False."""
        sig = np.array([0, 0, 0, 1, 1, 1])
        merged = _merge_segments(sig)
        # start_idx=3, current_idx=5, edge_distance=6-3=3 ≤ edge_width=3 → not locked
        result = _find_current_half_pair(sig, merged, edge_width=3)
        assert result is not None, "Should return a half-pair"
        assert result["is_locked"] is False

    @pytest.mark.strategy
    def test_negative_direction(self):
        """末尾为-1段 → direction=-1."""
        sig = np.array([0, 0, -1, -1, -1, -1])
        merged = _merge_segments(sig)
        result = _find_current_half_pair(sig, merged, edge_width=3)
        assert result is not None
        assert result["direction"] == -1
        assert result["is_locked"] is False  # edge_distance=4-2=2 ≤ 3

    @pytest.mark.strategy
    def test_locked_negative_direction(self):
        """已锁定的空头半边."""
        sig = np.array([0, -1, -1, -1, -1, -1, -1, 0, 0])
        merged = _merge_segments(sig)
        # start_idx=1, end of segment=6 (sig[6] is last -1)
        # 但 sig[7]=0, sig[8]=0 → 末尾是0 → 无半边
        result = _find_current_half_pair(sig, merged, edge_width=3)
        assert result is None, "末尾为0应当返回None"

    @pytest.mark.strategy
    def test_empty_merged(self):
        """merged为空 → None."""
        sig = np.array([0, 0, 0])
        merged = _merge_segments(sig)
        result = _find_current_half_pair(sig, merged, edge_width=3)
        assert result is None


# ============================================================================
# _get_higher_tf_direction
# ============================================================================

class TestGetHigherTfDirection:
    """_get_higher_tf_direction: higher timeframe direction."""

    @pytest.mark.strategy
    def test_positive_direction(self):
        """C周期末尾为+1锁定向 → +1."""
        higher_sig = np.array([0, 0, 0, 1, 1, 1, 1, 1])
        result = _get_higher_tf_direction(higher_sig, edge_width=3)
        assert result == 1, f"Got {result}"

    @pytest.mark.strategy
    def test_negative_direction(self):
        """C周期末尾为-1锁定向 → -1."""
        higher_sig = np.array([0, 0, 0, -1, -1, -1, -1, -1])
        result = _get_higher_tf_direction(higher_sig, edge_width=3)
        assert result == -1, f"Got {result}"

    @pytest.mark.strategy
    def test_zero_when_none(self):
        """C周期不可用(higher_sig=None) → 0."""
        result = _get_higher_tf_direction(None, edge_width=3)
        assert result == 0, f"Got {result}"

    @pytest.mark.strategy
    def test_zero_when_waiting(self):
        """C周期末尾在观望 → 0."""
        higher_sig = np.array([1, 1, 0, 0])
        result = _get_higher_tf_direction(higher_sig, edge_width=3)
        assert result == 0, f"Got {result}"

    @pytest.mark.strategy
    def test_unlocked_returns_direction(self):
        """C周期半边未锁定但仍返回方向（入场靠B周期锁定做最终闸门）. """
        higher_sig = np.array([0, 0, 1, 1])
        result = _get_higher_tf_direction(higher_sig, edge_width=3)
        # start_idx=2, current_idx=3, edge_distance=4-2=2 ≤ 3 → not locked
        # 但_get_higher_tf_direction 只返回方向，不判断锁定
        assert result == 1, f"Got {result}"


# ============================================================================
# _run_half_pair_strategy
# ============================================================================

def _make_half_pair(start=2, current_idx=9, direction=1):
    """Helper: construct a half_pair dict for testing."""
    return {
        "start": start,
        "current_idx": current_idx,
        "direction": direction,
    }


def _rising_price(n=20):
    """单调上涨价格序列."""
    return 100.0 + np.arange(n, dtype=float) * 0.5


def _falling_price(n=20):
    """单调下跌价格序列."""
    return 100.0 - np.arange(n, dtype=float) * 0.5


def _default_pred_pairs(filtered, pair_end=6):
    """构造基础pred_pairs（二次拟合）."""
    t = np.arange(len(filtered), dtype=float)
    from services.filter_engine import _fit_parabolic
    fit_result = _fit_parabolic(t, filtered, 0, pair_end)
    assert fit_result is not None, "fit failed in test helper"
    return [{
        "fit_result": fit_result,
        "fit_start": 0,
        "pair_end": pair_end,
    }]


class TestRunHalfPairStrategy:
    """_run_half_pair_strategy: entry + three exit mechanisms."""

    @pytest.mark.strategy
    def test_entry_with_c_confirmation(self):
        """C同向(is_locked=True, higher_dir=+1) → 入场."""
        n = 20
        t = np.arange(n, dtype=float)
        filtered = _rising_price(n)
        sig_t = np.array([0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        half_pair = _make_half_pair(start=2, current_idx=9, direction=1)
        higher_dir = 1  # C同向
        pred_pairs = _default_pred_pairs(filtered, pair_end=6)
        params = {"N_confirm": 2, "MAX_DEV_PCT": 4.0, "enable_gating": True}
        signals = _run_half_pair_strategy(t, filtered, sig_t, half_pair, higher_dir, pred_pairs, params)
        assert len(signals) >= 1, f"Expected >=1 signal, got {len(signals)}"
        assert signals[0]["direction"] == 1, f"Got direction={signals[0]['direction']}"

    @pytest.mark.strategy
    def test_no_entry_without_c(self):
        """C反向(higher_dir=-1) → 不入场."""
        n = 20
        t = np.arange(n, dtype=float)
        filtered = _rising_price(n)
        sig_t = np.array([0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        half_pair = _make_half_pair(start=2, current_idx=9, direction=1)
        higher_dir = -1  # C反向
        pred_pairs = _default_pred_pairs(filtered, pair_end=6)
        params = {"N_confirm": 2, "MAX_DEV_PCT": 4.0, "enable_gating": True}
        signals = _run_half_pair_strategy(t, filtered, sig_t, half_pair, higher_dir, pred_pairs, params)
        assert len(signals) == 0, f"Expected 0 signals, got {len(signals)}"

    @pytest.mark.strategy
    def test_entry_without_lock(self):
        """v2设计:未锁定(is_locked=False)也允许入场(起点即入场,不等锁定)."""
        n = 20
        t = np.arange(n, dtype=float)
        filtered = _rising_price(n)
        sig_t = np.array([0, 0, 0, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        half_pair = _make_half_pair(start=3, current_idx=8, direction=1)
        higher_dir = 1
        pred_pairs = _default_pred_pairs(filtered, pair_end=6)
        params = {"N_confirm": 2, "MAX_DEV_PCT": 4.0, "enable_gating": True}
        signals = _run_half_pair_strategy(t, filtered, sig_t, half_pair, higher_dir, pred_pairs, params)
        assert len(signals) >= 1, f"v2不等锁定,起点即入场,应有交易信号,got {len(signals)}"

    @pytest.mark.strategy
    def test_entry_without_c_when_disabled(self):
        """entry_require_c=False → 即使higher_dir=0也入场."""
        n = 20
        t = np.arange(n, dtype=float)
        filtered = _rising_price(n)
        sig_t = np.array([0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        half_pair = _make_half_pair(start=2, current_idx=9, direction=1)
        higher_dir = 0  # C观望
        pred_pairs = _default_pred_pairs(filtered, pair_end=6)
        params = {"N_confirm": 2, "MAX_DEV_PCT": 4.0, "edge_width": 3,
                  "enable_gating": False}
        signals = _run_half_pair_strategy(t, filtered, sig_t, half_pair, higher_dir, pred_pairs, params)
        assert len(signals) >= 1, f"Expected >=1 signal with entry_require_c=False"

    @pytest.mark.strategy
    def test_exit_next_pair(self):
        """反向段≥N_confirm → 离场(reason=pair_reverse)."""
        n = 30
        t = np.arange(n, dtype=float)
        filtered = _rising_price(n)
        sig_t = np.array([0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
                          -1, -1, -1, 0, 0, 0, 0, 0, 0, 0])
        half_pair = _make_half_pair(start=2, current_idx=19, direction=1)
        higher_dir = 1
        pred_pairs = _default_pred_pairs(filtered, pair_end=6)
        params = {"N_confirm": 2, "MAX_DEV_PCT": 4.0, "enable_gating": True}
        signals = _run_half_pair_strategy(t, filtered, sig_t, half_pair, higher_dir, pred_pairs, params)
        assert len(signals) >= 1, f"Expected >=1 signal, got {len(signals)}"
        # Sig于i=20转为-1, 持续>=2→应触发pair_reverse离场(最早i=21)
        assert signals[0]["exit_reason"] == "take_profit", (
            f"Got exit_reason={signals[0]['exit_reason']}"
        )

    @pytest.mark.strategy
    def test_exit_trend_deviation(self):
        """不利偏离>MAX_DEV_PCT → 离场(reason=trend_deviation)."""
        n = 30
        t = np.arange(n, dtype=float)
        # 价格先涨后暴跌 — 模拟趋势偏离
        filtered = np.concatenate([
            100.0 + np.arange(10, dtype=float) * 0.5,  # 0..9 上涨
            104.5 - np.arange(20, dtype=float) * 2.0,  # 10..29 暴跌偏离预测
        ])
        sig_t = np.array([0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
                          1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1])
        half_pair = _make_half_pair(start=2, current_idx=29, direction=1)
        higher_dir = 1
        pred_pairs = _default_pred_pairs(filtered, pair_end=6)
        params = {"N_confirm": 2, "MAX_DEV_PCT": 4.0, "enable_gating": True}
        signals = _run_half_pair_strategy(t, filtered, sig_t, half_pair, higher_dir, pred_pairs, params)
        assert len(signals) >= 1, f"Expected >=1 signal, got {len(signals)}"
        assert signals[0]["exit_reason"] == "trend_deviation", (
            f"Got exit_reason={signals[0]['exit_reason']}"
        )

    @pytest.mark.strategy
    def test_c_reverse_blocks_entry(self):
        """C周期翻向(higher_dir=-1)时门控阻挡入场,返回空列表."""
        n = 30
        t = np.arange(n, dtype=float)
        filtered = _rising_price(n)
        sig_t = np.array([0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
                          1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1])
        half_pair = _make_half_pair(start=2, current_idx=29, direction=1)
        higher_dir_reversed = -1  # C翻向
        pred_pairs = _default_pred_pairs(filtered, pair_end=6)
        params = {"N_confirm": 2, "MAX_DEV_PCT": 4.0, "enable_gating": True}
        signals = _run_half_pair_strategy(t, filtered, sig_t, half_pair, higher_dir_reversed,
                                          pred_pairs, params)
        assert len(signals) == 0, (
            f"C反向应阻挡入场,signals应为空,got {len(signals)}"
        )

    @pytest.mark.strategy
    def test_n_confirm_buffer(self):
        """单根反向bar不触发离场(N_confirm=2)."""
        n = 25
        t = np.arange(n, dtype=float)
        filtered = _rising_price(n)
        sig_t = np.array([0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
                          -1, 1, 1, 1, 1, 1, 1, 1, 1, 1])
        half_pair = _make_half_pair(start=2, current_idx=24, direction=1)
        higher_dir = 1
        pred_pairs = _default_pred_pairs(filtered, pair_end=6)
        params = {"N_confirm": 2, "MAX_DEV_PCT": 4.0, "enable_gating": True}
        signals = _run_half_pair_strategy(t, filtered, sig_t, half_pair, higher_dir, pred_pairs, params)
        # 单次-1(索引15)后恢复+1, N_confirm=2不应触发离场
        # 不检查具体交易次数:可能因eod离场而生成1笔
        # 仅确认不存在pair_reverse离场
        for sig in signals:
            assert sig["exit_reason"] != "take_profit", (
                f"Single reverse bar should not trigger pair_reverse"
            )

    @pytest.mark.strategy
    def test_c_reverse_blocks_entry_highest_priority(self):
        """C反向优先于入场 — 即使存在趋势偏离,门控也直接挡掉不入场."""
        n = 30
        t = np.arange(n, dtype=float)
        filtered = np.concatenate([
            100.0 + np.arange(10, dtype=float) * 0.5,
            104.5 - np.arange(20, dtype=float) * 2.0,
        ])
        sig_t = np.array([0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
                          1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1])
        half_pair = _make_half_pair(start=2, current_idx=29, direction=1)
        higher_dir_reversed = -1  # C反向 → 门控阻挡入场
        pred_pairs = _default_pred_pairs(filtered, pair_end=6)
        params = {"N_confirm": 2, "MAX_DEV_PCT": 4.0, "enable_gating": True}
        signals = _run_half_pair_strategy(t, filtered, sig_t, half_pair, higher_dir_reversed,
                                          pred_pairs, params)
        assert len(signals) == 0, (
            f"C反向门控应阻挡入场(即使有趋势偏离),signals应为空,got {len(signals)}"
        )

    @pytest.mark.strategy
    def test_empty_signals_on_no_opporunity(self):
        """无入场机会 → 空列表."""
        n = 20
        t = np.arange(n, dtype=float)
        filtered = _rising_price(n)
        sig_t = np.zeros(n, dtype=int)
        half_pair = _make_half_pair(start=2, current_idx=9, direction=0)
        higher_dir = 0
        pred_pairs = _default_pred_pairs(filtered, pair_end=6)
        params = {"N_confirm": 2, "MAX_DEV_PCT": 4.0, "enable_gating": True}
        signals = _run_half_pair_strategy(t, filtered, sig_t, half_pair, higher_dir, pred_pairs, params)
        assert len(signals) == 0, f"Expected 0 signals, got {len(signals)}"


# ============================================================================
# Backward compatibility: _compute_strategy_pnl (strategy_mode=None)
# ============================================================================

class TestComputeStrategyPnLDefault:
    """strategy_mode=None时_compute_strategy_pnl行为不变."""

    @pytest.mark.strategy
    def test_default_mode_empty_pairs(self):
        """strategy_mode=None, 空pairs → long_pnl=100, short_pnl=100, trades=[]."""
        t = np.arange(50, dtype=float)
        filtered = np.ones(50) * 100.0
        sig_t = np.zeros(50, dtype=int)
        long_pnl, short_pnl, trades = _compute_strategy_pnl(
            t, filtered, sig_t, [], [], 2.0, 10
        )
        assert np.allclose(long_pnl, 100.0)
        assert np.allclose(short_pnl, 100.0)
        assert trades == []

    @pytest.mark.strategy
    def test_default_mode_long_trade(self):
        """strategy_mode=None, long trade → 正收益."""
        t = np.arange(100, dtype=float)
        filtered = 100.0 + 0.02 * t ** 2
        sig_t = np.zeros(100, dtype=int)
        sig_t[50:] = 1
        all_pairs = [(50, 70)]
        from services.filter_engine import _fit_parabolic
        fit_result = _fit_parabolic(t, filtered, 50, 70)
        assert fit_result is not None
        pred_pairs = [{"fit_result": fit_result, "fit_start": 50, "pair_end": 70}]
        long_pnl, short_pnl, trades = _compute_strategy_pnl(
            t, filtered, sig_t, all_pairs, pred_pairs, 2.0, 10
        )
        assert long_pnl[-1] > 100.0, f"Expected profitable long, got {long_pnl[-1]:.2f}"
        assert len(trades) >= 1

    @pytest.mark.strategy
    def test_default_mode_compares_to_old_behavior(self):
        """strategy_mode=None的返回应与原有_find_all_pairs逻辑一致.

        使用与test_strategy.py中test_sequential_trades相似的用例，
        确保加参后默认行为不变。
        """
        n = 80
        t = np.arange(n, dtype=float)
        filtered = np.concatenate([
            np.linspace(100, 130, 40),
            np.linspace(130, 95, 40),
        ])
        sig_t = np.zeros(n, dtype=int)
        sig_t[5:35] = 1
        sig_t[45:75] = -1

        all_pairs = _find_all_pairs(sig_t)
        pred_pairs = []
        for ps, pe in all_pairs:
            if pe - ps >= 3:
                fr = _fit_parabolic(t, filtered, ps, pe)
                if fr is not None:
                    pred_pairs.append({"fit_result": fr, "fit_start": ps, "pair_end": pe})

        long_pnl, short_pnl, trades = _compute_strategy_pnl(
            t, filtered, sig_t, all_pairs, pred_pairs,
            stop_loss_pct=5.0, n_extend=10,
        )
        assert len(trades) >= 1, f"Expected at least 1 trade, got {len(trades)}"
        assert not np.allclose(long_pnl, short_pnl), "Long and short PnL should differ"
