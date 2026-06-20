"""
同向性子图测试 — _compute_holding_masks + _add_alignment_subplot.
"""
import numpy as np
import pytest
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from streamlit_app import _compute_holding_masks, _add_alignment_subplot


# =========================================================================
# _compute_holding_masks
# =========================================================================

class TestComputeHoldingMasks:

    def test_basic_long_mask(self):
        """TC-ALIGN-01: 做多entry→exit产生正确mask"""
        n = 50
        entries = [(10, "long", 100.0)]
        exits = [(30, "long", 105.0, 5.0, "take_profit")]
        long_m, short_m = _compute_holding_masks(n, entries, exits)
        assert long_m[9] == False   # 入场前
        assert long_m[10] == True   # 入场
        assert long_m[20] == True   # 持仓中
        assert long_m[30] == True   # 离场（包含）
        assert long_m[31] == False  # 离场后
        assert not short_m.any()    # 无做空

    def test_basic_short_mask(self):
        """做空entry→exit产生正确mask"""
        n = 50
        entries = [(5, "short", 100.0)]
        exits = [(25, "short", 95.0, -5.0, "stop_loss")]
        long_m, short_m = _compute_holding_masks(n, entries, exits)
        assert short_m[5] == True
        assert short_m[25] == True
        assert short_m[26] == False
        assert not long_m.any()

    def test_both_long_and_short(self):
        """TC-ALIGN-02: 多空都有，互不干扰"""
        n = 60
        entries = [(10, "long", 100.0), (35, "short", 100.0)]
        exits = [(25, "long", 105.0, 5.0, "take_profit"),
                 (50, "short", 95.0, -5.0, "stop_loss")]
        long_m, short_m = _compute_holding_masks(n, entries, exits)
        # 做多区段
        assert long_m[10:26].all()
        assert not long_m[30:35].any()
        # 做空区段
        assert short_m[35:51].all()
        assert not short_m[5:10].any()

    def test_entry_without_exit(self):
        """entry没有对应exit→用到数据末尾"""
        n = 40
        entries = [(30, "long", 100.0)]
        exits = []
        long_m, short_m = _compute_holding_masks(n, entries, exits)
        assert long_m[29] == False
        assert long_m[30] == True
        assert long_m[39] == True  # 末尾

    def test_empty_markers(self):
        """空markers→全False"""
        long_m, short_m = _compute_holding_masks(20, [], [])
        assert not long_m.any()
        assert not short_m.any()

    def test_multiple_entries_same_type(self):
        """同类型多个entry, 各自配对最近的exit"""
        n = 80
        entries = [(10, "long", 100.0), (40, "long", 100.0)]
        exits = [(25, "long", 105.0, 5.0, "tp"),
                 (60, "long", 110.0, 10.0, "tp")]
        long_m, short_m = _compute_holding_masks(n, entries, exits)
        assert long_m[10:26].all()   # 第一段
        assert not long_m[30:35].any()  # 两段之间
        assert long_m[40:61].all()   # 第二段

    def test_entry_before_first_exit_no_match(self):
        """entry在第一个exit之前，但exit类型不匹配"""
        n = 30
        entries = [(5, "long", 100.0)]
        exits = [(10, "short", 100.0, 0.0, "take_profit")]  # 只有做空exit
        long_m, short_m = _compute_holding_masks(n, entries, exits)
        # 做多找不到exit→用到末尾
        assert long_m[5:].all()
        assert not long_m[:5].any()


# =========================================================================
# _add_alignment_subplot (纯函数行为)
# =========================================================================

class TestAlignmentSubplot:

    def test_no_hold_flat_at_100(self):
        """无持仓时long_pnl/short_pnl线都维持100"""
        n = 20
        t = np.arange(n, dtype=float)
        long_pnl = np.full(n, 100.0)
        short_pnl = np.full(n, 100.0)
        long_mask = np.zeros(n, dtype=bool)
        short_mask = np.zeros(n, dtype=bool)

        fig = make_subplots(rows=1, cols=1)
        _add_alignment_subplot(fig, t, long_pnl, short_pnl, [],
                               long_mask, short_mask, row=1)
        # 不应有trace以外的问题，不崩溃即可

    def test_long_follows_pnl(self):
        """TC-ALIGN-03: 做多期间跟随long_pnl, 非做多持平, 后续累加"""
        n = 20
        t = np.arange(n, dtype=float)
        long_pnl = np.array([100.0, 101.0, 102.0, 103.0, 104.0,
                             105.0, 106.0, 107.0, 108.0, 109.0,
                             110.0, 111.0, 112.0, 113.0, 114.0,
                             115.0, 116.0, 117.0, 118.0, 119.0])
        short_pnl = np.full(n, 100.0)

        # 两段做多: bar 3-7, bar 12-16; 中间持平
        long_mask = np.zeros(n, dtype=bool)
        long_mask[3:8] = True
        long_mask[12:17] = True
        short_mask = np.zeros(n, dtype=bool)

        fig = make_subplots(rows=1, cols=1)
        _add_alignment_subplot(fig, t, long_pnl, short_pnl, [],
                               long_mask, short_mask, row=1)

        # 提取long_filtered trace (subplot下trace顺序与add_trace一致)
        long_trace = fig.data[0]
        lf = long_trace.y  # long_filtered

        # bar 0: 100.0
        assert lf[0] == 100.0
        # 非持仓段: 持平
        assert lf[1] == lf[0]  # bar 1 hold
        assert lf[2] == lf[0]  # bar 2 hold
        # 第一段持仓 bar 3→7: 累积涨跌幅
        assert lf[3] > 100.0   # 持仓开始
        assert lf[7] > 100.0   # 涨了
        # 持平段 bar 8-11: 不变
        assert lf[8] == lf[7]
        assert lf[11] == lf[7]
        # 第二段 bar 12→16: 从持平值继续跟long_pnl
        assert lf[12] > lf[11]  # long_pnl涨了
        assert lf[16] > lf[12]

    def test_short_follows_pnl(self):
        """做空期间跟随short_pnl变化"""
        n = 10
        t = np.arange(n, dtype=float)
        long_pnl = np.full(n, 100.0)
        short_pnl = np.array([100.0, 101.0, 102.0, 103.0, 102.0,
                              101.0, 100.0, 99.0, 98.0, 97.0])
        short_mask = np.ones(n, dtype=bool)  # 全程做空
        long_mask = np.zeros(n, dtype=bool)

        fig = make_subplots(rows=1, cols=1)
        _add_alignment_subplot(fig, t, long_pnl, short_pnl, [],
                               long_mask, short_mask, row=1)

        short_trace = fig.data[1]
        sf = short_trace.y

        # bar 0: 100.0
        assert sf[0] == 100.0
        # 做空期间跟踪short_pnl
        assert sf[2] > 100.0   # short_pnl涨到102

    def test_no_crash_with_empty_data(self):
        """空数据不崩溃"""
        fig = make_subplots(rows=1, cols=1)
        _add_alignment_subplot(fig, np.arange(5, dtype=float),
                               np.ones(5) * 100, np.ones(5) * 100,
                               [],
                               np.zeros(5, dtype=bool), np.zeros(5, dtype=bool),
                               row=1)
        assert len(fig.data) >= 3  # 至少两条曲线 + 基准线区域

    def test_trade_records_with_masks(self):
        """trade_records传入同向段高亮"""
        n = 20
        t = np.arange(n, dtype=float)
        long_pnl = np.full(n, 100.0)
        short_pnl = np.full(n, 100.0)
        long_mask = np.zeros(n, dtype=bool)
        long_mask[5:16] = True
        short_mask = np.zeros(n, dtype=bool)
        trades = [
            {"id": 1, "type": "long", "entry_idx": 5, "exit_idx": 15,
             "exit_reason": "take_profit", "pnl": 5.0},
        ]

        fig = make_subplots(rows=1, cols=1)
        _add_alignment_subplot(fig, t, long_pnl, short_pnl, trades,
                               long_mask, short_mask, row=1)
        # 有trades时应包含高亮trace（line width=3）
        # 至少2条主曲线 + 1条高亮（如果exit_index和mask匹配）
        assert len(fig.data) > 2

    def test_trade_records_skip_on_mask_mismatch(self):
        """trade中的entry/exit不在mask中→跳过高亮"""
        n = 10
        t = np.arange(n, dtype=float)
        long_pnl = np.full(n, 100.0)
        short_pnl = np.full(n, 100.0)
        long_mask = np.zeros(n, dtype=bool)
        short_mask = np.zeros(n, dtype=bool)
        trades = [
            {"id": 1, "type": "long", "entry_idx": 3, "exit_idx": 8,
             "exit_reason": "take_profit", "pnl": 5.0},
        ]

        fig = make_subplots(rows=1, cols=1)
        _add_alignment_subplot(fig, t, long_pnl, short_pnl, trades,
                               long_mask, short_mask, row=1)
        # mask全False → 不会画出高亮segment线
        # 验证信号是否处理了（至少不崩溃)
        assert len(fig.data) >= 3

    def test_entry_out_of_bounds(self):
        """entry_idx超出n→跳过"""
        n = 10
        t = np.arange(n, dtype=float)
        long_pnl = np.full(n, 100.0)
        short_pnl = np.full(n, 100.0)
        long_mask = np.ones(n, dtype=bool)
        short_mask = np.zeros(n, dtype=bool)
        trades = [
            {"id": 1, "type": "long", "entry_idx": 20, "exit_idx": 25,
             "exit_reason": "take_profit", "pnl": 5.0},
        ]

        fig = make_subplots(rows=1, cols=1)
        # 不应raise
        _add_alignment_subplot(fig, t, long_pnl, short_pnl, trades,
                               long_mask, short_mask, row=1)
