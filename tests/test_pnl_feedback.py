"""
Tests for Layer 1 PnL-feedback binary position gate
(_pnl_feedback_gate_1d, _compute_pnl_feedback_positions).

设计见 docs/backtesting/pnl-feedback-position-process-v1.md
"""

import numpy as np
from services.filter_engine import (
    _pnl_feedback_gate_1d,
    _compute_pnl_feedback_positions,
)


# ============================================================================
# 零回归：dd_clear=inf ⇒ actual ≡ shadow，永不清仓
# ============================================================================
class TestZeroRegression:
    def test_actual_equals_shadow_when_never_clears(self):
        shadow = np.array([100, 110, 90, 130, 85, 120], dtype=float)
        active, actual, clears, recovers = _pnl_feedback_gate_1d(
            shadow, dd_clear=float("inf"), dd_recover=0.03)
        assert np.array_equal(actual, shadow)     # 逐字节复现
        assert active.all()
        assert clears == [] and recovers == []

    def test_positions_dict_zero_regression(self):
        long_pnl = np.array([100, 108, 95, 112], dtype=float)
        short_pnl = np.array([100, 97, 105, 92], dtype=float)
        out = _compute_pnl_feedback_positions(
            long_pnl, short_pnl, dd_clear=float("inf"))
        assert np.array_equal(out["actual_long"], long_pnl)
        assert np.array_equal(out["actual_short"], short_pnl)
        assert out["long_active"].all() and out["short_active"].all()


# ============================================================================
# 清仓触发：回撤越过 dd_clear ⇒ 冻结实际权益
# ============================================================================
class TestClearTrigger:
    def test_clear_freezes_actual(self):
        # peak=110@idx2；idx5=101 → dd=8.18% > 8% → 清仓
        shadow = np.array([100, 105, 110, 108, 105, 101, 100, 100], dtype=float)
        active, actual, clears, recovers = _pnl_feedback_gate_1d(
            shadow, dd_clear=0.08, dd_recover=0.03)
        assert clears == [5]
        assert not active[5:].any()               # 清仓后保持 FLAT
        # 冻结在清仓前一 bar 的实际值(=105)，之后恒定
        assert actual[5] == 105.0
        assert np.all(actual[5:] == 105.0)

    def test_no_clear_below_threshold(self):
        # 最大回撤 5.45% < 8%，不清仓
        shadow = np.array([100, 110, 104, 108, 110], dtype=float)
        active, actual, clears, _ = _pnl_feedback_gate_1d(
            shadow, dd_clear=0.08, dd_recover=0.03)
        assert clears == []
        assert active.all()
        assert np.array_equal(actual, shadow)


# ============================================================================
# 迟滞恢复：清仓后需回撤 < dd_recover 才恢复；band 内保持 FLAT
# ============================================================================
class TestRecoverHysteresis:
    def test_recover_only_below_recover_band(self):
        # idx2 dd=9.09%→清仓; idx3 dd=4.5%(band内)→仍FLAT; idx4 dd=0.9%<3%→恢复
        shadow = np.array([100, 110, 100, 105, 109, 110], dtype=float)
        active, actual, clears, recovers = _pnl_feedback_gate_1d(
            shadow, dd_clear=0.08, dd_recover=0.03)
        assert clears == [2]
        assert recovers == [4]
        assert actual[3] == 110.0                 # band 内仍冻结(不恢复)
        assert active[4] and active[5]            # 恢复后持仓
        # 恢复后实际线从冻结值(110)续接并跟涨
        assert actual[4] == 110.0
        assert actual[5] == 111.0


# ============================================================================
# 多空独立：一方向清仓不影响另一方向
# ============================================================================
class TestDirectionIndependence:
    def test_long_clears_short_untouched(self):
        long_pnl = np.array([100, 105, 110, 108, 105, 101, 100], dtype=float)
        short_pnl = np.array([100, 101, 103, 106, 108, 110, 112], dtype=float)
        out = _compute_pnl_feedback_positions(
            long_pnl, short_pnl, dd_clear=0.08, dd_recover=0.03)
        assert len(out["long_clears"]) > 0        # long 触发清仓
        assert out["short_clears"] == []          # short 单调上行，不清仓
        assert out["short_active"].all()
        assert np.array_equal(out["actual_short"], short_pnl)


# ============================================================================
# 边界：空数组 / 单元素
# ============================================================================
class TestBoundary:
    def test_empty(self):
        active, actual, clears, recovers = _pnl_feedback_gate_1d(
            np.array([], dtype=float), dd_clear=0.08, dd_recover=0.03)
        assert len(active) == 0 and len(actual) == 0
        assert clears == [] and recovers == []

    def test_single_bar(self):
        active, actual, clears, recovers = _pnl_feedback_gate_1d(
            np.array([100.0]), dd_clear=0.08, dd_recover=0.03)
        assert active[0] and actual[0] == 100.0
        assert clears == [] and recovers == []
