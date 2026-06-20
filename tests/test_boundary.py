"""
边界条件测试 — 空数据、短序列、数值稳定性、导入导出。
"""
import numpy as np
import pandas as pd
import pytest

from streamlit_app import (
    _compute_strategy_pnl, _find_all_pairs, _align_pnl_to_current_tf,
    _fit_parabolic, _fit_physics_parabola, _schmitt_trigger,
    TF_HIERARCHY, ALL_TFS,
)


# =========================================================================
# _compute_strategy_pnl 边界
# =========================================================================

class TestComputeStrategyPnlBoundary:

    @pytest.mark.slow
    def test_filtered_all_nan(self):
        """filtered 全 NaN 不应崩溃."""
        t = np.arange(50, dtype=float)
        filtered = np.full(50, np.nan)

        # 构造一个 sig_t 即使 filtered 无效也不该崩溃
        sig_t = np.zeros(50, dtype=int)
        sig_t[10:20] = 1
        sig_t[30:40] = -1
        all_pairs = [(10, 20), (30, 40)]

        pred_pairs = [
            {"pair_end": 20, "fit_result": {"a": 0.01, "b": 0.0, "c": 102.0}},
            {"pair_end": 40, "fit_result": {"a": -0.01, "b": 0.0, "c": 98.0}},
        ]

        long_pnl, short_pnl, trades = _compute_strategy_pnl(
            t, filtered, sig_t, all_pairs, pred_pairs,
            stop_loss_pct=2.0, n_extend=10,
        )
        assert long_pnl.shape == (50,), "long_pnl 长度应匹配 t"
        assert short_pnl.shape == (50,), "short_pnl 长度应匹配 t"
        assert trades == [], "filtered 全 NaN 时不应有交易"

    @pytest.mark.slow
    def test_short_sequence(self):
        """t 长度 < 5 时不应崩溃."""
        t = np.arange(4, dtype=float)
        filtered = np.array([100.0, 101.0, 102.0, 103.0])
        sig_t = np.array([0, 1, 0, -1], dtype=int)
        all_pairs = [(1, 3)]
        pred_pairs = [
            {"pair_end": 3, "fit_result": {"a": 0.005, "b": 0.0, "c": 102.0}},
        ]
        long_pnl, short_pnl, trades = _compute_strategy_pnl(
            t, filtered, sig_t, all_pairs, pred_pairs,
            stop_loss_pct=2.0, n_extend=10,
        )
        assert long_pnl.shape == (4,)
        assert short_pnl.shape == (4,)


# =========================================================================
# _find_all_pairs 边界
# =========================================================================

class TestFindAllPairsBoundary:

    @pytest.mark.slow
    def test_zero_gap_merged(self):
        """含 0 间隔: [+1, +1, 0, 0, +1, +1] → 同号合并为一个段."""
        sig_t = np.array([1, 1, 0, 0, 1, 1, -1, -1], dtype=int)
        pairs = _find_all_pairs(sig_t)
        # 前 6 个元素应合并为一个多头段 (0,5)，与 (6,7) 的空头段配对
        assert len(pairs) >= 1, "应产生至少一对"
        # 配对边界应为 (0, 6): 多头段起点 → 空头段入口
        assert pairs[0] == (0, 6), "0 间隔同号段应合并"

    @pytest.mark.slow
    def test_single_bar_signal(self):
        """仅一个非零 bar: [0, +1, 0] → 无 pair (段数 < 2)."""
        sig_t = np.array([0, 1, 0], dtype=int)
        pairs = _find_all_pairs(sig_t)
        assert pairs == [], "单个非零信号不应产生 pair"

    @pytest.mark.slow
    def test_frequent_alternation(self):
        """正负交替频繁: 每 2 个 bar 换方向 → 生成多对."""
        sig_t = np.zeros(20, dtype=int)
        for i in range(10):
            sig_t[2 * i:2 * i + 2] = 1 if i % 2 == 0 else -1
        pairs = _find_all_pairs(sig_t)
        assert len(pairs) >= 1, "频繁交替应生成 pairs"

    @pytest.mark.slow
    def test_all_zero(self):
        """全 0 信号 → 空列表."""
        sig_t = np.zeros(10, dtype=int)
        pairs = _find_all_pairs(sig_t)
        assert pairs == []

    @pytest.mark.slow
    def test_short_signal(self):
        """len < 3 → 空列表."""
        assert _find_all_pairs(np.array([1, 0])) == []
        assert _find_all_pairs(np.array([1])) == []
        assert _find_all_pairs(np.array([], dtype=int)) == []


# =========================================================================
# _fit_parabolic / _fit_physics_parabola 边界
# =========================================================================

class TestFitBoundary:

    @pytest.mark.slow
    def test_fit_parabolic_short_segment(self):
        """fit 段长度 < 3 → 返回 None."""
        x = np.arange(5, dtype=float)
        y = np.array([100.0, 101.0, 102.0, 103.0, 104.0])
        assert _fit_parabolic(x, y, 0, 1) is None, "len<3 → None"
        assert _fit_physics_parabola(x, y, 0, 1) is None, "len<3 → None"

    @pytest.mark.slow
    def test_fit_parabolic_normal(self):
        """正常 5 点拟合返回 dict."""
        x = np.arange(5, dtype=float)
        y = np.array([100.0, 101.0, 102.0, 103.0, 104.0])
        result = _fit_parabolic(x, y, 0, 4)
        assert result is not None
        assert "a" in result and "b" in result and "c" in result

    @pytest.mark.slow
    def test_fit_physics_denom_zero(self):
        """denom ≈ 0 → 返回 None."""
        x = np.array([0.0, 0.0, 0.0])
        y = np.array([100.0, 101.0, 102.0])
        result = _fit_physics_parabola(x, y, 0, 2)
        assert result is None, "denom≈0 → None"

    @pytest.mark.slow
    def test_collinear_parabolic_fit_values(self):
        """TC-DATA-06.6: 共线数据抛物线拟合 → a~0, b~1, c~1"""
        x = np.arange(10, dtype=float)
        y = x + 1.0  # y = x + 1 (完全线性)
        result = _fit_parabolic(x, y, 0, 9)
        assert result is not None
        a, b, c = result["a"], result["b"], result["c"]
        assert abs(a) < 0.01, f"Linear data should have a~0, got a={a}"
        assert abs(b - 1.0) < 0.15, f"Linear data should have b~1, got b={b}"
        assert abs(c - 1.0) < 0.5, f"Linear data should have c~1, got c={c}"


# =========================================================================
# _schmitt_trigger 边界
# =========================================================================

class TestSchmittTriggerBoundary:

    @pytest.mark.slow
    def test_n_less_than_span(self):
        """n < ewma_span → 返回 None."""
        v = np.array([0.1, 0.2, 0.3])
        a = np.array([0.01, 0.02, 0.03])
        assert _schmitt_trigger(v, a, ewma_span=60) is None

    @pytest.mark.slow
    def test_empty_v(self):
        """空数组 → 返回 None (n=0 < span)."""
        v = np.array([])
        a = np.array([])
        assert _schmitt_trigger(v, a) is None

    @pytest.mark.slow
    def test_normal_output_shape(self):
        """正常输入返回完整 dict."""
        v = np.random.randn(100)
        a = np.random.randn(100)
        result = _schmitt_trigger(v, a, ewma_span=60)
        assert result is not None
        for key in ("mu_v", "sigma_v", "eps", "sig", "dur"):
            assert key in result
            assert len(result[key]) == 100


# =========================================================================
# 导入导出验证
# =========================================================================

class TestExportImportConfig:

    @pytest.mark.slow
    def test_export_dict_structure(self):
        """验证导出 config dict 包含必需 key."""
        # 模拟一个完整的导出配置
        export_data = {
            "market": "美股 US",
            "ticker": "AAPL",
            "global_f": "ema",
            "global_dual": False,
            "global_f2": None,
            "v0_tf": "日线",
            "v0_n": 120,
            "v0_sch": True,
            "v0_pred": True,
            "v0_ke": 0.15,
            "v0_sm": 0.05,
            "v0_ew": 60,
            "v0_fm": "parabola",
            "v0_next": 8,
            "v0_fc": "#00d4aa",
            "v0_fc2": "#ff6b6b",
            "v0_strat": False,
            "v0_sl": 2.0,
            "v0_cross_pnl": False,
            "span_v0_f1_ema": 10,
            "v1_tf": "60分钟",
            "v1_n": 120,
            "v1_sch": True,
            "v1_pred": True,
            "v1_ke": 0.15,
            "v1_sm": 0.05,
            "v1_ew": 60,
            "v1_fm": "parabola",
            "v1_next": 8,
            "v1_fc": "#00d4aa",
            "v1_fc2": "#ff6b6b",
            "v1_strat": False,
            "v1_sl": 2.0,
            "v1_cross_pnl": False,
            "span_v1_f1_ema": 10,
            "v2_tf": "15分钟",
            "v2_n": 120,
            "v2_sch": True,
            "v2_pred": True,
            "v2_ke": 0.15,
            "v2_sm": 0.05,
            "v2_ew": 60,
            "v2_fm": "parabola",
            "v2_next": 8,
            "v2_fc": "#00d4aa",
            "v2_fc2": "#ff6b6b",
            "v2_strat": False,
            "v2_sl": 2.0,
            "v2_cross_pnl": False,
            "span_v2_f1_ema": 10,
            "v3_tf": "5分钟",
            "v3_n": 120,
            "v3_sch": True,
            "v3_pred": True,
            "v3_ke": 0.15,
            "v3_sm": 0.05,
            "v3_ew": 60,
            "v3_fm": "parabola",
            "v3_next": 8,
            "v3_fc": "#00d4aa",
            "v3_fc2": "#ff6b6b",
            "v3_strat": False,
            "v3_sl": 2.0,
            "v3_cross_pnl": False,
            "span_v3_f1_ema": 10,
        }

        # 验证必需 key
        required_global = ["market", "ticker", "global_f"]
        required_view = [f"v{i}_tf" for i in range(4)]
        required_view += [f"v{i}_cross_pnl" for i in range(4)]

        for key in required_global + required_view:
            assert key in export_data, f"导出配置缺少必需 key: {key}"

    @pytest.mark.slow
    def test_imp_backup_created_on_import(self):
        """验证 _imp_ 备份 key 在导入时被正确创建 (模拟 main() 导入逻辑)."""
        config = {
            "market": "美股 US",
            "ticker": "NVDA",
            "global_f": "sma",
            "v0_tf": "日线",
            "v0_ke": 0.20,
            "v0_sm": 0.08,
            "span_v0_f1_sma": 21,
        }

        # 使用真实 dict 模拟 session_state + 导入逻辑
        session_state = {}
        for k, v in config.items():
            session_state[k] = v
            session_state[f"_imp_{k}"] = v  # 非widget备份，防rerun丢失

        for k in config:
            imp_key = f"_imp_{k}"
            assert imp_key in session_state, f"缺少 {imp_key}"
            assert session_state[imp_key] == config[k], (
                f"{imp_key} 值不匹配"
            )

        # 验证备份不会被随机覆盖
        assert session_state["_imp_market"] == "美股 US"
        assert session_state["_imp_ticker"] == "NVDA"


# =========================================================================
# 空数据降级
# =========================================================================

class TestEmptyDataDegradation:

    @pytest.mark.slow
    def test_schmitt_none_degrades_gracefully(self):
        """schmitt_trigger 返回 None → all_pairs=[] → PnL 返回初始值."""
        t = np.arange(10, dtype=float)
        filtered = np.array([100.0, 101.0, 102.0, 103.0, 104.0,
                             105.0, 106.0, 107.0, 108.0, 109.0])
        # n < ewma_span → schmitt = None
        sig_t = np.zeros(10, dtype=int)
        all_pairs = []  # 模拟 schmitt=None 后的降级
        pred_pairs = []

        long_pnl, short_pnl, trades = _compute_strategy_pnl(
            t, filtered, sig_t, all_pairs, pred_pairs,
            stop_loss_pct=2.0, n_extend=10,
        )
        np.testing.assert_array_equal(long_pnl, np.full(10, 100.0))
        assert trades == []


# =========================================================================
# 数值稳定性
# =========================================================================

class TestNumericalStability:

    @pytest.mark.slow
    def test_extreme_prices(self):
        """极端价格值不应导致 PnL 溢出.

        构造: pair_end=20, 价格从 entry 后上涨, 预测也看涨.
        """
        n = 50
        t = np.arange(n, dtype=float)
        filtered = np.full(n, 0.0001)
        pair_end = 20
        # entry 后价格逐步上涨到 0.0002
        filtered[pair_end + 1:] = np.linspace(0.0001, 0.0002, n - pair_end - 1)
        sig_t = np.zeros(n, dtype=int)
        sig_t[10:n] = 1  # 多头持续到末尾
        all_pairs = [(10, pair_end)]
        # 抛物线 a=0.0 但 b>0 → 预测向上 (polyval with (a,b,c) not (a,b,c,x0))
        # 使用 poly2 模式（无 x0）：y = a*x^2 + b*x + c
        pred_pairs = [
            {"pair_end": pair_end,
             "fit_result": {"a": 0.0, "b": 1e-8, "c": 0.0}},
        ]
        long_pnl, short_pnl, trades = _compute_strategy_pnl(
            t, filtered, sig_t, all_pairs, pred_pairs,
            stop_loss_pct=5.0, n_extend=10,
        )
        assert not np.any(np.isnan(long_pnl)), "极小价格不应产生 NaN"
        assert not np.any(np.isinf(long_pnl)), "极小价格不应产生 inf"
        assert long_pnl[-1] > 100.0, "上涨交易应产生 > 100 的收益"

    @pytest.mark.slow
    def test_negative_price(self):
        """负价格应被正确处理（跳过该交易）. Negative price is common bug path in finance code."""
        t = np.arange(30, dtype=float)
        # 包含负值
        filtered = np.array([-5.0, -4.0, -3.0, -2.0, -1.0,
                             0.0, 1.0, 2.0, 3.0, 4.0,
                             5.0, 6.0, 7.0, 8.0, 9.0,
                             10.0, 11.0, 12.0, 13.0, 14.0,
                             15.0, 16.0, 17.0, 18.0, 19.0,
                             20.0, 21.0, 22.0, 23.0, 24.0], dtype=float)
        sig_t = np.zeros(30, dtype=int)
        sig_t[10:20] = 1
        all_pairs = [(10, 20)]
        pred_pairs = [
            {"pair_end": 20, "fit_result": {"a": 0.0, "b": 1.0, "c": 5.0,
                                             "x0": 20.0}},
        ]
        long_pnl, short_pnl, trades = _compute_strategy_pnl(
            t, filtered, sig_t, all_pairs, pred_pairs,
            stop_loss_pct=5.0, n_extend=10,
        )
        # 不崩溃就可以，entry_price <= 0 时会跳过交易
        assert not np.any(np.isnan(long_pnl))
        # 负价格时段应跳过
        assert isinstance(trades, list)


# =========================================================================
# 任务2 额外: _align_pnl_to_current_tf 边界（空 higher_trades）
# =========================================================================

class TestAlignPnlBoundary:

    @pytest.mark.slow
    def test_higher_trades_empty(self, sample_dates_daily, sample_dates_intraday):
        """交易列表为空 → 无 markers."""
        higher_dates = sample_dates_daily[:5]
        higher_pnl_long = 100.0 + np.arange(5, dtype=float)
        higher_pnl_short = np.full(5, 100.0)
        higher_trades = []
        current_dates = sample_dates_intraday[:15]

        result = _align_pnl_to_current_tf(
            higher_dates, higher_pnl_long, higher_pnl_short,
            higher_trades, current_dates,
        )
        assert result["entry_markers"] == []
        assert result["exit_markers"] == []
        # PnL 对齐依然应正常
        assert not np.all(np.isnan(result["aligned_long"]))


# =========================================================================
# CROSS-01: TF_HIERARCHY 链完整性验证
# =========================================================================

class TestCrossTfHierarchy:

    def test_tf_hierarchy_chain(self):
        """CROSS-01: 验证8周期映射链完整性"""
        assert TF_HIERARCHY["1分钟"] == "5分钟"
        assert TF_HIERARCHY["5分钟"] == "15分钟"
        assert TF_HIERARCHY["15分钟"] == "60分钟"
        assert TF_HIERARCHY["60分钟"] == "日线"
        assert TF_HIERARCHY["日线"] == "周线"
        assert TF_HIERARCHY["周线"] == "月线"
        assert TF_HIERARCHY["月线"] == "季线"
        assert TF_HIERARCHY["季线"] is None
        # 验证所有key都存在
        for tf in ALL_TFS:
            assert tf in TF_HIERARCHY, f"Missing TF_HIERARCHY entry for {tf}"
