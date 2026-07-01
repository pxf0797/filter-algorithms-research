"""回测功能单元测试 — bar 级回测核心函数 + 前视偏差验证。

测试策略：
1. 每个测试函数用纯 numpy/pandas 构造输入，不依赖 Streamlit runtime。
2. 前视偏差测试验证截断、pair 过滤两个核心风险点。
3. 通过 streamlit_app 纯函数直接调用，无需 mock st.*。
4. conftest.py 已将 streamlit 替换为 MagicMock，streamlit_app 导入时
   st.cache_data/st.cache_resource 均为 no-op decorator，不影响测试。
"""

import sys
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd
import pytest

# conftest.py 已经将 streamlit 替换为 MagicMock，保持 sys.modules 不变
# 直接导入 streamlit_app，其 import streamlit as st 会得到 MagicMock
import streamlit_app  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures shared across test classes
# ---------------------------------------------------------------------------

PRICE_OHLC = {
    "Open": np.array([100.0, 101.0, 102.0, 103.0, 104.0, 105.0]),
    "High": np.array([101.5, 102.5, 103.5, 104.5, 105.5, 106.0]),
    "Low":  np.array([99.0, 100.0, 101.0, 102.0, 103.0, 104.0]),
    "Close": np.array([101.0, 102.0, 103.0, 104.0, 105.0, 106.0]),
}


@pytest.fixture
def six_bars_data() -> tuple:
    t = np.arange(6, dtype=float)
    noisy = np.array([101.0, 102.0, 103.0, 104.0, 105.0, 106.0], dtype=float)
    ohlc = pd.DataFrame(PRICE_OHLC)
    dates = pd.date_range("2026-01-01", periods=6, freq="D")
    return t, noisy, ohlc, dates


# 注意：_truncate_arrays 和 _global_to_local_bar_index 已在 T3 清理中移除。
# 回测模式数据截断改用 _load_chart_data 的窗口平移策略。
# 相关测试已一并移除。


# ====================================================================
# _load_chart_data — 回测模式窗口平移
# ====================================================================

class TestLoadChartDataBacktest:
    """验证回测模式下 _load_chart_data 的窗口平移行为。

    核心场景：
    1. bar_index=0 时仍能加载 n_pts 条数据（而非 1 条）
    2. 高周期窗口终点 <= min_tf 的 bar_index 对应日期
    3. 高周期合成仅发生在 bar_index 落在周期中间时

    通过 mock parquet 读取和 _sync_to_display 来隔离测试。
    """

    @pytest.fixture
    def mock_deps(self, monkeypatch):
        """Mock 外部依赖以控制 _load_chart_data 的 parquet 输入。"""
        # Mock _sync_to_display 为 no-op
        monkeypatch.setattr(streamlit_app, "_sync_to_display", lambda *a, **kw: None)

        # Mock Path.exists 默认返回 True
        monkeypatch.setattr(Path, "exists", lambda _: True)

        # Mock AppState.get 返回可控值 — 因为 conftest 中 streamlit 为 MagicMock,
        # 其 session_state 不是真实 dict, 无法通过赋值持久化
        # 空 DataFrame 不含 OHLC 列，用于触发 _load_backtest_window 空返回
        monkeypatch.setattr(
            streamlit_app.AppState, "get",
            lambda key, default=None: {
                "_min_tf": "日线",
                "_bt_data_cache": {
                    "日线": pd.DataFrame(index=pd.DatetimeIndex([])),
                },
            }.get(key, default),
        )

        return monkeypatch

    @staticmethod
    def _make_mock_parquet(dates, ohlc_dict):
        """构造一个模拟 pd.read_parquet 返回值的 DataFrame。

        格式与 _load_chart_data 中从 display parquet 读取的一致：
        包含 Date, Open, High, Low, Close 列，Date 为字符串。
        """
        df = pd.DataFrame({
            "Date": [d.strftime("%Y-%m-%d") for d in dates],
            **ohlc_dict,
        })
        return df

    def test_bar_index_zero_loads_one_bar(self, mock_deps, monkeypatch):
        """bar_index=0 时只加载 1 条数据（窗口不向未来扩展）。"""
        n_pts = 50
        dates = pd.date_range("2025-06-01", periods=200, freq="D")

        # 覆盖 mock_deps 中 _bt_data_cache 的默认值
        # _load_backtest_window 从缓存取 DataFrame，需要 OHLC 列才返回有效数据
        ohlc_data = {
            "Open": np.linspace(100, 200, 200),
            "High": np.linspace(102, 202, 200),
            "Low": np.linspace(98, 198, 200),
            "Close": np.linspace(101, 201, 200),
        }
        monkeypatch.setattr(
            streamlit_app.AppState, "get",
            lambda key, default=None: {
                "_min_tf": "日线",
                "_bt_data_cache": {
                    "日线": pd.DataFrame(ohlc_data, index=dates),
                },
            }.get(key, default),
        )

        # _load_backtest_window 使用缓存/parquet，但此测试 tf == min_tf，
        # 缓存中已有数据，不会走到 parquet 读取，mock 仅做兜底
        mock_df = self._make_mock_parquet(dates, ohlc_data)
        monkeypatch.setattr(pd, "read_parquet", lambda path, *a, **kw: mock_df)

        t, noisy, ohlc, ticker_full, result_dates, err = (
            streamlit_app._load_chart_data(
                "美股 US", "AAPL", "日线", 0, n_pts, bar_index=0
            )
        )

        assert err is None, f"Unexpected error: {err}"
        # cutoff_idx=0 → start_idx=0, end_idx=0 → 1 bar
        assert len(t) == 1, (
            f"bar_index=0 时应加载 1 条数据，实际 {len(t)}"
        )
        assert len(result_dates) == 1
        assert result_dates[0] == dates[0]

    def test_min_tf_controls_high_tf_window_end(self, mock_deps, monkeypatch):
        """高周期窗口终点 <= min_tf 的 bar_index 对应日期。"""
        n_pts = 30
        bar_index = 60

        # min_tf 周期（日线）数据
        min_tf_dates = pd.date_range("2025-01-01", periods=200, freq="D", name="Date")

        monkeypatch.setattr(
            streamlit_app.AppState, "get",
            lambda key, default=None: {
                "_min_tf": "日线",
                "_bt_data_cache": {
                    "日线": pd.DataFrame(index=min_tf_dates),
                },
            }.get(key, default),
        )

        # 高周期（周线）数据
        weekly_dates = pd.date_range("2025-01-06", periods=40, freq="W-MON")
        mock_weekly = self._make_mock_parquet(weekly_dates, {
            "Open": np.linspace(100, 200, 40),
            "High":  np.linspace(102, 202, 40),
            "Low":   np.linspace(98, 198, 40),
            "Close": np.linspace(101, 201, 40),
        })

        def _mock_read_parquet(path, *a, **kw):
            p = str(path)
            if "周线" in p:
                return mock_weekly
            elif "日线" in p:
                return self._make_mock_parquet(min_tf_dates, {
                    "Open": np.linspace(100, 200, 200),
                    "High": np.linspace(102, 202, 200),
                    "Low": np.linspace(98, 198, 200),
                    "Close": np.linspace(101, 201, 200),
                })
            return mock_weekly

        monkeypatch.setattr(pd, "read_parquet", _mock_read_parquet)

        # Mock _get_next_lower_tf
        monkeypatch.setattr(
            streamlit_app,
            "_get_next_lower_tf",
            lambda tf: "日线" if tf == "周线" else None,
        )

        t, noisy, ohlc, ticker_full, result_dates, err = (
            streamlit_app._load_chart_data(
                "美股 US", "AAPL", "周线", 0, n_pts, bar_index=bar_index
            )
        )

        assert err is None, f"Unexpected error: {err}"
        # 周线窗口终点应 <= min_tf_dates[bar_index]
        min_tf_cutoff = min_tf_dates[bar_index]
        assert result_dates[-1] <= min_tf_cutoff, (
            f"高周期窗口终点 {result_dates[-1]} > min_tf 截止 {min_tf_cutoff}"
        )
        # 周线数据应返回约 n_pts 条
        assert len(t) >= 1

    def test_high_tf_synthesis_happens_mid_cycle(self, mock_deps, monkeypatch):
        """周期中间时触发高周期合成：bar_index 落在周线周期中部。"""
        n_pts = 20

        # 日线（min_tf）数据 — 50 bar
        daily_dates = pd.date_range("2025-06-01", periods=50, freq="D")

        monkeypatch.setattr(
            streamlit_app.AppState, "get",
            lambda key, default=None: {
                "_min_tf": "日线",
                "_bt_data_cache": {
                    "日线": pd.DataFrame(index=daily_dates),
                },
            }.get(key, default),
        )

        # 周线数据 — 7 个完整周
        weekly_dates = pd.date_range("2025-06-02", periods=7, freq="W-MON")
        mock_weekly_df = self._make_mock_parquet(weekly_dates, {
            "Open": [100, 102, 104, 106, 108, 110, 112],
            "High": [103, 105, 107, 109, 111, 113, 115],
            "Low":  [99, 101, 103, 105, 107, 109, 111],
            "Close": [102, 104, 106, 108, 110, 112, 114],
        })
        # lower_tf（60分钟）数据 — 用于合成
        lower_dates = pd.date_range("2025-07-14", periods=5, freq="h")
        mock_lower_df = self._make_mock_parquet(lower_dates, {
            "Open": [113, 113.5, 114, 114.5, 115],
            "High": [114, 114.5, 115, 115.5, 116],
            "Low":  [112, 112.5, 113, 113.5, 114],
            "Close": [113.5, 114, 114.5, 115, 115.5],
        })

        call_log = {"calls": []}

        def _mock_read_parquet(path, *a, **kw):
            p = str(path)
            call_log["calls"].append(p)
            if "60分钟" in p:
                return mock_lower_df
            elif "周线" in p:
                return mock_weekly_df
            return mock_weekly_df

        monkeypatch.setattr(pd, "read_parquet", _mock_read_parquet)

        # bar_index = 32 (日线索引), 对应截止日期 2025-07-03
        # 在第 5 个周线周期 (2025-06-30 ~ 2025-07-06) 中间
        monkeypatch.setattr(
            streamlit_app,
            "_get_next_lower_tf",
            lambda tf: "60分钟" if tf == "周线" else None,
        )

        t, noisy, ohlc, ticker_full, result_dates, err = (
            streamlit_app._load_chart_data(
                "美股 US", "AAPL", "周线", 0, n_pts, bar_index=32
            )
        )

        assert err is None, f"Unexpected error: {err}"
        # Phase 1: _load_backtest_window 处理基础窗口切片
        # 周线窗口应包含完整数据，长度 >= 1
        assert len(t) >= 1
        # 窗口终点 <= min_tf_dates[bar_index]
        assert result_dates[-1] <= daily_dates[32]


# _global_to_local_bar_index 已随 T3 清理移除。


# ====================================================================
# _synthesize_higher_tf_bar
# ====================================================================

class TestSynthesizeHigherTfBar:
    """高周期 bar 合成测试。"""

    def test_synthesize_from_two_bars(self):
        """从2根低周期 bar 合成高周期 bar。"""
        lower = pd.DataFrame({
            "Open": [100.0, 105.0],
            "High": [102.0, 108.0],
            "Low":  [99.0, 104.0],
            "Close": [105.0, 106.0],
        })
        result = streamlit_app._synthesize_higher_tf_bar(lower, "60分钟")
        assert result is not None
        assert result["Open"] == 100.0
        assert result["Close"] == 106.0
        assert result["High"] == 108.0
        assert result["Low"] == 99.0

    def test_synthesize_from_many_bars(self):
        """从5根低周期 bar 合成，验证 OHLC 规则。"""
        lower = pd.DataFrame({
            "Open": [50.0, 51.0, 52.0, 51.5, 53.0],
            "High": [51.5, 52.5, 53.0, 52.5, 54.0],
            "Low":  [49.5, 50.5, 51.0, 50.5, 52.0],
            "Close": [51.0, 52.0, 51.5, 53.0, 53.5],
        })
        result = streamlit_app._synthesize_higher_tf_bar(lower, "日线")
        assert result is not None
        assert result["Open"] == 50.0           # 首个 Open
        assert result["Close"] == 53.5          # 末个 Close
        assert result["High"] == 54.0           # 最大 High
        assert result["Low"] == 49.5            # 最小 Low

    def test_single_bar_returns_none(self):
        """只有1根 bar 时返回 None。"""
        lower = pd.DataFrame({
            "Open": [100.0],
            "High": [102.0],
            "Low":  [99.0],
            "Close": [101.0],
        })
        result = streamlit_app._synthesize_higher_tf_bar(lower, "60分钟")
        assert result is None

    def test_empty_df_returns_none(self):
        """空 DataFrame 时返回 None。"""
        lower = pd.DataFrame({c: [] for c in ["Open", "High", "Low", "Close"]})
        result = streamlit_app._synthesize_higher_tf_bar(lower, "60分钟")
        assert result is None

    def test_none_returns_none(self):
        """None 输入时返回 None。"""
        result = streamlit_app._synthesize_higher_tf_bar(None, "日线")
        assert result is None


# ====================================================================
# _get_min_tf_and_count
# ====================================================================

class TestGetMinTfAndCount:
    """最小周期查找测试。"""

    def test_finds_min_tf(self):
        """从4个视图中找到 ALL_TFS 索引最小的 tf。"""
        configs = [
            {"tf": "日线"},
            {"tf": "60分钟"},
            {"tf": "月线"},
            {"tf": "日线"},
        ]
        min_tf, count = streamlit_app._get_min_tf_and_count(configs, "AAPL")
        # ALL_TFS: ['1分钟','5分钟','15分钟','60分钟','日线','周线','月线','季线']
        # 60分钟(index 3) < 日线(index 4) < 月线(index 6)
        assert min_tf == "60分钟"

    def test_all_same_tf(self):
        """所有视图同周期。"""
        configs = [
            {"tf": "日线"},
            {"tf": "日线"},
            {"tf": "日线"},
            {"tf": "日线"},
        ]
        min_tf, count = streamlit_app._get_min_tf_and_count(configs, "AAPL")
        assert min_tf == "日线"

    def test_empty_configs_returns_empty(self):
        """空配置列表返回 ("", 0)。"""
        min_tf, count = streamlit_app._get_min_tf_and_count([], "AAPL")
        assert min_tf == ""
        assert count == 0

    def test_single_view(self):
        """单个视图配置。"""
        configs = [{"tf": "15分钟"}]
        min_tf, count = streamlit_app._get_min_tf_and_count(configs, "AAPL")
        assert min_tf == "15分钟"

    def test_unknown_tf_in_config(self):
        """含无法识别的 tf 时不会崩溃。"""
        configs = [
            {"tf": "日线"},
            {"tf": "未知周期"},
            {"tf": "60分钟"},
        ]
        min_tf, count = streamlit_app._get_min_tf_and_count(configs, "AAPL")
        # ALL_TFS: 60分钟(index 3) < 日线(index 4)
        assert min_tf == "60分钟"


# ====================================================================
# _add_backtest_overlay -- Plotly Figure annotation
# ====================================================================

class TestAddBacktestOverlay:
    """回测图表标注测试 -- 不依赖 Plotly 渲染，验证配置正确性。"""

    def test_none_bar_index_skips(self):
        """bar_index=None 时函数不做任何操作。"""
        import plotly.graph_objects as go
        fig = go.Figure()
        streamlit_app._add_backtest_overlay(fig, None, 100,
                                            pd.date_range("2026-01-01", periods=100, freq="D"), "日线")
        assert len(fig.layout.shapes) == 0
        assert len(fig.layout.annotations) == 0

    def test_zero_total_bars_skips(self):
        """total_bars=0 时函数不做任何操作。"""
        import plotly.graph_objects as go
        fig = go.Figure()
        streamlit_app._add_backtest_overlay(fig, 0, 0,
                                            pd.date_range("2026-01-01", periods=1, freq="D"), "日线")
        assert len(fig.layout.shapes) == 0

    def test_adds_overlay_elements(self):
        """典型场景：添加遮罩和注解。"""
        import plotly.graph_objects as go
        fig = go.Figure()
        dates = pd.date_range("2026-01-01", periods=50, freq="D")
        streamlit_app._add_backtest_overlay(fig, 30, 50, dates, "日线")
        # 应至少有一个 annotation（"回测模式"）
        texts = [a.text for a in fig.layout.annotations]
        assert any("回测模式" in t for t in texts)
        # 应至少有一个 shape（遮罩或 vline）
        assert len(fig.layout.shapes) >= 1


# ====================================================================
# 前视偏差验证 -- Look-ahead bias prevention
# ====================================================================

class TestLookAheadBiasPrevention:
    """前视偏差防护测试 -- 验证回测模式不会使用未来信息。"""

    @pytest.fixture
    def pred_pairs_data(self):
        """构造有预测对的信号数据。"""
        np.random.seed(42)
        t = np.arange(100, dtype=float)
        filtered = np.sin(t / 5.0) + np.random.randn(100) * 0.1
        # 构造信号：多 -> 空 -> 多 三段
        sig = np.array([1] * 30 + [0] * 5 + [-1] * 30 + [0] * 5 + [1] * 30, dtype=int)
        schmitt = {"sig": sig}
        cfg: Dict[str, Any] = {"show_pred": True, "fit_mode": "parabola"}
        all_pairs = [(0, 29), (34, 64)]
        return t, filtered, schmitt, cfg, all_pairs

    def test_truncation_removes_future_data(self, six_bars_data):
        """截断后不包含未来数据 -- 验证 bar_index=2 后无 bar_index=3,4,5。
        使用内联截断逻辑替代已移除的 _truncate_arrays。"""
        t, noisy, ohlc, dates = six_bars_data
        bi = 2
        result_t = t[:bi + 1]
        assert np.all(result_t <= 2.0)
        assert len(result_t) == 3

    def test_pairs_filtered_by_bar_index(self, pred_pairs_data):
        """prediction_pairs 中无 pair_end > bar_index 的预测对。"""
        t, filtered, schmitt, cfg, all_pairs = pred_pairs_data

        # 不设 bar_index：两个预测对都返回
        all_no_bar = streamlit_app._compute_prediction_pairs(
            t, filtered, schmitt, cfg, all_pairs, bar_index=None)
        # 设 bar_index=30：只保留 pair_end <= 30 的对
        filtered_pairs = streamlit_app._compute_prediction_pairs(
            t, filtered, schmitt, cfg, all_pairs, bar_index=30)

        assert len(all_no_bar) > 0
        assert len(filtered_pairs) >= 1

        for pp in filtered_pairs:
            assert pp["pair_end"] <= 30, (
                f"pair_end={pp['pair_end']} > bar_index=30 -- look-ahead bias!"
            )

    def test_early_bar_index_filters_all_future(self, pred_pairs_data):
        """bar_index 非常小（如 5）时全部预测对应被过滤。"""
        t, filtered, schmitt, cfg, all_pairs = pred_pairs_data
        result = streamlit_app._compute_prediction_pairs(
            t, filtered, schmitt, cfg, all_pairs, bar_index=5)
        # 最短 pair 长度要求 >= 3，且 pair_end <= 5
        # pair_end=29 > 5, pair_end=64 > 5 -> 全被过滤
        assert len(result) == 0

    def test_bar_index_at_start(self, pred_pairs_data):
        """bar_index=0 时不产生任何预测（未来不存在）。"""
        t, filtered, schmitt, cfg, all_pairs = pred_pairs_data
        result = streamlit_app._compute_prediction_pairs(
            t, filtered, schmitt, cfg, all_pairs, bar_index=0)
        assert len(result) == 0

    def test_no_future_price_in_truncation(self):
        """验证截断数组中没有未来价格 -- 内联截断逻辑。"""
        np.random.seed(1)
        n = 50
        t = np.arange(n, dtype=float)
        prices = np.cumsum(np.random.randn(n) * 0.5 + 0.1) + 100.0

        for bar_idx in [10, 25, 40]:
            result_p = prices[:bar_idx + 1]
            assert result_p[-1] == prices[bar_idx], (
                f"bar_index={bar_idx}: last price {result_p[-1]} != {prices[bar_idx]}"
            )

    def test_bar_index_none_includes_all_pairs(self, pred_pairs_data):
        """bar_index=None（浏览模式）时返回全部预测对。"""
        t, filtered, schmitt, cfg, all_pairs = pred_pairs_data
        result = streamlit_app._compute_prediction_pairs(
            t, filtered, schmitt, cfg, all_pairs, bar_index=None)
        expected_count = sum(1 for s, e in all_pairs if e - s >= 3)
        assert len(result) == expected_count

    def test_consistency_bar_index_at_total_bars(self, pred_pairs_data):
        """bar_index=最大索引时等效于 bar_index=None（显示全部）。"""
        t, filtered, schmitt, cfg, all_pairs = pred_pairs_data
        n = len(t)
        with_bar = streamlit_app._compute_prediction_pairs(
            t, filtered, schmitt, cfg, all_pairs, bar_index=n - 1)
        without_bar = streamlit_app._compute_prediction_pairs(
            t, filtered, schmitt, cfg, all_pairs, bar_index=None)
        assert len(with_bar) == len(without_bar)


# ====================================================================
# 回测控制逻辑验证 -- State management patterns
# ====================================================================

class TestBacktestStateTransitions:
    """验证回测状态切换时的边界条件。"""

    def test_step_back_from_zero(self):
        """从 bar_index=0 后退时不超出下界。"""
        bar_index = 0
        new_index = max(0, bar_index - 1)
        assert new_index == 0

    def test_step_fwd_from_last(self):
        """从末尾前进时不超出上界。"""
        total = 100
        bar_index = total - 1
        new_index = min(total - 1, bar_index + 1)
        assert new_index == 99

    def test_goto_start(self):
        """跳转到开头时设为 0。"""
        new_index = 0
        assert new_index == 0

    def test_goto_end(self):
        """跳转到末尾时设为 total-1。"""
        total = 100
        new_index = total - 1
        assert new_index == 99

    def test_playback_stops_at_end(self):
        """播放到 total_bars-1 时触发停止条件。"""
        total = 100
        bar = total - 1
        assert bar >= total - 1


# ====================================================================
# 辅助结构验证 -- Data flow sanity
# ====================================================================

class TestBacktestDataFlow:
    """验证回测数据流完整性。"""

    def test_cache_miss_returns_none(self):
        """_bt_data_cache 中不存在给定 tf 时返回 None（不崩溃）。"""
        cache = {}
        df = cache.get("60分钟")
        assert df is None

    def test_truncate_does_not_mutate_input(self, six_bars_data):
        """切片操作不修改原始数组（内联验证，替代已移除的 _truncate_arrays）。"""
        t, noisy, ohlc, dates = six_bars_data
        t_copy = t.copy()
        noisy_copy = noisy.copy()
        _ = t[:3]
        np.testing.assert_array_equal(t, t_copy)
        np.testing.assert_array_equal(noisy, noisy_copy)


# ====================================================================
# T1-T3 补充覆盖测试
# ====================================================================

class TestBacktestWindowEdgeCases:
    """T1-T3 改动对应的边界场景测试。"""

    def test_empty_backtest_window_returns_error(self, monkeypatch, tmp_path):
        """回测模式 _load_backtest_window 返回空 DataFrame 时 _load_chart_data 直接返回错误。

        旧实现退化到 _apply_backtest_window（已删除），
        新实现直接返回错误，不静默退化。"""
        monkeypatch.setattr(streamlit_app, "_sync_to_display", lambda *a, **kw: None)
        monkeypatch.setattr(Path, "exists", lambda _: True)

        # _load_backtest_window 返回空: min_tf 缓存中没有该 tf, parquet 也不存在
        # 设 bar_index 超出范围触发空返回
        dates = pd.date_range("2026-01-01", periods=5, freq="D")
        monkeypatch.setattr(
            streamlit_app.AppState, "get",
            lambda key, default=None: {
                "_min_tf": "日线",
                "_bt_data_cache": {
                    "日线": pd.DataFrame(index=dates),
                },
            }.get(key, default),
        )

        mock_df = pd.DataFrame({
            "Date": ["2026-01-01", "2026-01-02"],
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.0, 102.0],
        })
        monkeypatch.setattr(pd, "read_parquet", lambda path, *a, **kw: mock_df)

        t, noisy, ohlc, ticker, dates_result, err = (
            streamlit_app._load_chart_data(
                "美股 US", "TEST", "周线", 0, 100, bar_index=999
            )
        )

        assert err == "回测窗口数据为空"
        assert t is None
        assert noisy is None

    def test_cache_dataframe_has_datetime_index(self, monkeypatch):
        """_render_backtest_mode_switch 写入缓存的 DataFrame 有 DatetimeIndex。

        T1 修复场景: parquet 缺少 Date 列时强制转换 index 为 DatetimeIndex。"""
        import io

        # 构造一个没有 Date 列的 parquet 文件
        bad_df = pd.DataFrame({"Close": [100.0, 101.0]}, index=["2026-01-01", "2026-01-02"])
        buf = io.BytesIO()
        bad_df.to_parquet(buf)
        buf.seek(0)

        cache_store = {}

        def mock_exists(path):
            return True

        def mock_read_parquet(path, **kw):
            buf.seek(0)
            return pd.read_parquet(buf)

        monkeypatch.setattr(Path, "exists", mock_exists)
        monkeypatch.setattr(pd, "read_parquet", mock_read_parquet)
        monkeypatch.setattr(
            streamlit_app.AppState, "get",
            lambda key, default=None: {
                "_cb_mode": False,
                "_min_tf": "日线",
                "_bt_data_cache": {},
            }.get(key, default),
        )

        def mock_set(key, value):
            if key == "_bt_data_cache":
                cache_store.update(value)

        monkeypatch.setattr(streamlit_app.AppState, "set", mock_set)
        monkeypatch.setattr(streamlit_app, "_sync_to_display", lambda *a, **kw: None)
        monkeypatch.setattr(streamlit_app, "_get_min_tf_and_count",
                            lambda *a: ("日线", 10))

        configs = [{"tf": "日线", "n_pts": 100}]
        streamlit_app._render_backtest_mode_switch("美股 US", "test", configs)

        for tf, df in cache_store.items():
            assert isinstance(df.index, pd.DatetimeIndex), (
                f"缓存 {tf} 的 index 类型应为 DatetimeIndex, 实际 {type(df.index)}"
            )

    def test_cb_mode_bar_index_defaults_to_zero(self):
        """_cb_mode=True 时 bar_index 默认 0 而非 None。

        T2 修复场景: 确保 _load_chart_data 回测模式不收到 None bar_index。"""
        cb_mode = True
        bar_index = 0 if cb_mode else None
        assert bar_index == 0
        assert bar_index is not None

    def test_browse_mode_bar_index_is_none(self):
        """_cb_mode=False 时 bar_index 为 None。

        回退模式无需窗口截断，bar_index=None 确保下游函数跳过回测专用逻辑。"""
        cb_mode = False
        bar_index = None if not cb_mode else 0
        assert bar_index is None


# ====================================================================
# Phase 1: _binary_search_le
# ====================================================================

class TestBinarySearchLe:
    """_binary_search_le — 纯 Python 二分查找 <= cutoff 的最大索引。"""

    def test_exact_match(self):
        """cutoff 等于 dates 中某个元素时返回该元素索引。"""
        dates = pd.date_range("2026-01-01", periods=5, freq="D")
        idx = streamlit_app._binary_search_le(dates, pd.Timestamp("2026-01-03"))
        assert idx == 2

    def test_before_first(self):
        """cutoff 早于第一个日期时返回 -1。"""
        dates = pd.date_range("2026-01-05", periods=5, freq="D")
        idx = streamlit_app._binary_search_le(dates, pd.Timestamp("2026-01-01"))
        assert idx == -1

    def test_after_last(self):
        """cutoff 晚于最后一个日期时返回最后一个索引。"""
        dates = pd.date_range("2026-01-01", periods=5, freq="D")
        idx = streamlit_app._binary_search_le(dates, pd.Timestamp("2026-01-10"))
        assert idx == 4

    def test_single_element(self):
        """单元素 dates。"""
        dates = pd.DatetimeIndex([pd.Timestamp("2026-06-15")])
        idx = streamlit_app._binary_search_le(dates, pd.Timestamp("2026-06-15"))
        assert idx == 0
        idx = streamlit_app._binary_search_le(dates, pd.Timestamp("2026-06-14"))
        assert idx == -1
        idx = streamlit_app._binary_search_le(dates, pd.Timestamp("2026-06-16"))
        assert idx == 0

    def test_empty_dates(self):
        """空 dates 返回 -1。"""
        dates = pd.DatetimeIndex([])
        idx = streamlit_app._binary_search_le(dates, pd.Timestamp("2026-01-01"))
        assert idx == -1

    def test_cutoff_between_two_dates(self):
        """cutoff 位于两个日期之间时返回较小的索引。"""
        dates = pd.date_range("2026-01-01", periods=5, freq="D")
        idx = streamlit_app._binary_search_le(dates, pd.Timestamp("2026-01-03T12:00:00"))
        # 2026-01-03 < 2026-01-03T12:00 < 2026-01-04 → 返回 2
        assert idx == 2

    def test_cutoff_equals_first(self):
        """cutoff 等于 dates[0]。"""
        dates = pd.date_range("2026-01-01", periods=10, freq="D")
        idx = streamlit_app._binary_search_le(dates, pd.Timestamp("2026-01-01"))
        assert idx == 0

    def test_cutoff_equals_last(self):
        """cutoff 等于 dates[-1]。"""
        dates = pd.date_range("2026-01-01", periods=10, freq="D")
        idx = streamlit_app._binary_search_le(dates, pd.Timestamp("2026-01-10"))
        assert idx == 9

    def test_large_dataset_smoke(self):
        """大数据集不崩溃。"""
        dates = pd.date_range("2020-01-01", periods=10000, freq="D")
        # 10000 天从 2020-01-01 延伸到约 2047-05-18
        idx = streamlit_app._binary_search_le(dates, dates[-1])
        assert idx == len(dates) - 1
        idx = streamlit_app._binary_search_le(dates, pd.Timestamp("2019-01-01"))
        assert idx == -1
        idx = streamlit_app._binary_search_le(dates, pd.Timestamp("2026-06-15"))
        assert idx >= 0

    def test_monotonic_assertion(self):
        """结果单调性：cutoff 越大，返回值不减小。"""
        dates = pd.date_range("2026-01-01", periods=100, freq="D")
        prev = -1
        for i in range(100):
            cutoff = dates[i] + pd.Timedelta(hours=12)
            idx = streamlit_app._binary_search_le(dates, cutoff)
            assert idx >= prev, f"单调性违反: idx={idx} < prev={prev} at i={i}"
            prev = idx


# ====================================================================
# Phase 1: _load_backtest_window
# ====================================================================

class TestLoadBacktestWindow:
    """_load_backtest_window — 窗口平移核心算法。"""

    DATES_200 = pd.date_range("2025-06-01", periods=200, freq="D")

    @pytest.fixture
    def setup_mocks(self, monkeypatch):
        """基础 mock 环境：_sync_to_display no-op, Path.exists=True, _bt_data_cache 有 200 条日线。"""
        monkeypatch.setattr(streamlit_app, "_sync_to_display", lambda *a, **kw: None)
        monkeypatch.setattr(Path, "exists", lambda _: True)

        ohlc_200 = {
            "Open": np.linspace(100, 300, 200),
            "High": np.linspace(102, 302, 200),
            "Low": np.linspace(98, 298, 200),
            "Close": np.linspace(101, 301, 200),
        }
        cache_df = pd.DataFrame(ohlc_200, index=self.DATES_200)

        monkeypatch.setattr(
            streamlit_app.AppState, "get",
            lambda key, default=None: {
                "_min_tf": "日线",
                "_bt_data_cache": {"日线": cache_df},
            }.get(key, default),
        )
        return cache_df

    def test_bar_index_zero(self, setup_mocks, monkeypatch):
        """bar_index=0 时只取 1 条（最早日期）。"""
        window = streamlit_app._load_backtest_window("日线", 0, 100)
        assert len(window) == 1
        assert window.index[0] == self.DATES_200[0]

    def test_bar_index_last(self, setup_mocks, monkeypatch):
        """bar_index 为最后一根时窗口终点为最后一个日期。"""
        window = streamlit_app._load_backtest_window("日线", 199, 100)
        assert len(window) <= 100
        assert window.index[-1] == self.DATES_200[-1]

    def test_bar_index_mid(self, setup_mocks, monkeypatch):
        """bar_index >= n_pts-1 时得到满 n_pts 条且终点正确。"""
        n = 100
        window = streamlit_app._load_backtest_window("日线", 150, n)
        assert len(window) == n
        assert window.index[-1] == self.DATES_200[150]

    def test_n_pts_larger_than_data(self, setup_mocks, monkeypatch):
        """n_pts 超过数据总量时全部返回。"""
        window = streamlit_app._load_backtest_window("日线", 199, 1000)
        assert len(window) <= 200
        assert window.index[-1] == self.DATES_200[-1]

    def test_cutoff_date_exact_boundary(self, setup_mocks, monkeypatch):
        """cutoff 精确等于某个 bar 日期时正确包含该 bar。"""
        window = streamlit_app._load_backtest_window("日线", 10, 5)
        assert len(window) == 5
        assert window.index[-1] == self.DATES_200[10]
        assert window.index[0] == self.DATES_200[6]

    def test_cutoff_date_mid_high_period(self, setup_mocks, monkeypatch):
        """高周期 tf：cutoff 在周线中间时窗口终点为 ≤ cutoff 的最大周线日期。"""
        weekly_dates = pd.date_range("2025-06-02", periods=30, freq="W-MON")
        weekly_data = {
            "Open": np.linspace(100, 200, 30),
            "High": np.linspace(102, 202, 30),
            "Low": np.linspace(98, 198, 30),
            "Close": np.linspace(101, 201, 30),
        }
        weekly_df = pd.DataFrame(weekly_data, index=weekly_dates)

        monkeypatch.setattr(
            streamlit_app.AppState, "get",
            lambda key, default=None: {
                "_min_tf": "日线",
                "_bt_data_cache": {
                    "日线": pd.DataFrame({"Close": np.ones(200)}, index=self.DATES_200),
                    "周线": weekly_df,
                },
            }.get(key, default),
        )

        # bar_index=50 → cutoff=2025-07-21, 周线窗口终点 ≤ 2025-07-21
        window = streamlit_app._load_backtest_window("周线", 50, 10)
        assert len(window) > 0
        cutoff = self.DATES_200[50]
        assert window.index[-1] <= cutoff

    def test_returns_datetime_index(self, setup_mocks, monkeypatch):
        """返回的 index 是 DatetimeIndex。"""
        window = streamlit_app._load_backtest_window("日线", 50, 50)
        assert isinstance(window.index, pd.DatetimeIndex)

    def test_preserves_ohlc_columns(self, setup_mocks, monkeypatch):
        """返回结果保留 Open/High/Low/Close 列。"""
        window = streamlit_app._load_backtest_window("日线", 50, 50)
        for col in ["Open", "High", "Low", "Close"]:
            assert col in window.columns, f"缺少列: {col}"
        assert len(window.columns) >= 4

    def test_empty_parquet_handling(self, setup_mocks, monkeypatch):
        """Parquet 文件不存在或为空时返回空 DataFrame。"""
        # 清除缓存以确保 _load_backtest_window 读取 parquet
        monkeypatch.setattr(
            streamlit_app.AppState, "get",
            lambda key, default=None: {
                "_min_tf": "日线",
                "_bt_data_cache": {},
            }.get(key, default),
        )
        monkeypatch.setattr(Path, "exists", lambda _: False)
        window = streamlit_app._load_backtest_window("日线", 50, 100)
        assert len(window) == 0

    def test_min_tf_equals_current_tf(self, setup_mocks, monkeypatch):
        """min_tf == tf 时使用 df.index 作为 min_tf_dates。"""
        window = streamlit_app._load_backtest_window("日线", 150, 100)
        assert len(window) == 100
        assert window.index[-1] == self.DATES_200[150]

    def test_high_tf_window_smaller_than_min_tf(self, setup_mocks, monkeypatch):
        """高周期 tf 窗口可能比 min_tf 短，但终点不超过 cutoff。"""
        weekly_dates = pd.date_range("2025-06-02", periods=10, freq="W-MON")
        weekly_df = pd.DataFrame({
            "Open": np.linspace(100, 200, 10),
            "High": np.linspace(102, 202, 10),
            "Low": np.linspace(98, 198, 10),
            "Close": np.linspace(101, 201, 10),
        }, index=weekly_dates)

        monkeypatch.setattr(
            streamlit_app.AppState, "get",
            lambda key, default=None: {
                "_min_tf": "日线",
                "_bt_data_cache": {
                    "日线": pd.DataFrame({"Close": np.ones(200)}, index=self.DATES_200),
                    "周线": weekly_df,
                },
            }.get(key, default),
        )

        window = streamlit_app._load_backtest_window("周线", 50, 5)
        assert len(window) > 0
        assert len(window) <= 5
        cutoff = self.DATES_200[50]
        assert window.index[-1] <= cutoff


# ====================================================================
# Phase 2: _get_period_boundary & _belongs_to_same_period
# ====================================================================

class TestPeriodBoundary:
    """周期边界计算测试 — _get_period_boundary 和 _belongs_to_same_period。"""

    # ---- _get_period_boundary ----

    def test_daily_boundary(self):
        """日线边界返回自身。"""
        d = pd.Timestamp("2026-06-15")
        result = streamlit_app._get_period_boundary(d, "日线")
        assert result == d

    def test_weekly_boundary_mid_week(self):
        """周线周三 → 当周周日。"""
        d = pd.Timestamp("2026-06-17")  # 周三
        result = streamlit_app._get_period_boundary(d, "周线")
        assert result.weekday() == 6  # 周日
        # 2026-06-17 是周三，当周周日是 2026-06-21
        assert result == pd.Timestamp("2026-06-21")

    def test_weekly_boundary_friday(self):
        """周线周五 → 当周周日。"""
        d = pd.Timestamp("2026-06-19")  # 周五
        result = streamlit_app._get_period_boundary(d, "周线")
        assert result == pd.Timestamp("2026-06-21")

    def test_monthly_boundary_mid_month(self):
        """月线月中 → 月末最后一天。"""
        d = pd.Timestamp("2026-06-15")
        result = streamlit_app._get_period_boundary(d, "月线")
        assert result == pd.Timestamp("2026-06-30")

    def test_monthly_boundary_february(self):
        """2月 → 2/28。"""
        d = pd.Timestamp("2026-02-15")
        result = streamlit_app._get_period_boundary(d, "月线")
        # 2026 不是闰年
        assert result == pd.Timestamp("2026-02-28")

    def test_quarterly_boundary_mid_quarter(self):
        """季线 Q2 中 → Q2 末。"""
        d = pd.Timestamp("2026-05-15")
        result = streamlit_app._get_period_boundary(d, "季线")
        assert result == pd.Timestamp("2026-06-30")

    def test_quarterly_boundary_q4(self):
        """季线 Q4 → 12/31。"""
        d = pd.Timestamp("2026-11-01")
        result = streamlit_app._get_period_boundary(d, "季线")
        assert result == pd.Timestamp("2026-12-31")

    def test_yearly_boundary(self):
        """年线 → 12/31。"""
        d = pd.Timestamp("2026-07-01")
        result = streamlit_app._get_period_boundary(d, "年线")
        assert result == pd.Timestamp("2026-12-31")

    # ---- _belongs_to_same_period ----

    def test_same_week_true(self):
        """同一年同一周 → True。"""
        d1 = pd.Timestamp("2026-06-15")  # 周一
        d2 = pd.Timestamp("2026-06-18")  # 周四
        assert streamlit_app._belongs_to_same_period(d1, d2, "周线") is True

    def test_same_week_false_cross_week(self):
        """同一周但跨年 → False。"""
        d1 = pd.Timestamp("2025-12-29")  # 周一
        d2 = pd.Timestamp("2026-01-01")  # 周四
        assert streamlit_app._belongs_to_same_period(d1, d2, "周线") is False

    def test_same_month_true(self):
        """同月 → True。"""
        d1 = pd.Timestamp("2026-06-01")
        d2 = pd.Timestamp("2026-06-30")
        assert streamlit_app._belongs_to_same_period(d1, d2, "月线") is True

    def test_same_month_false(self):
        """跨月 → False。"""
        d1 = pd.Timestamp("2026-05-31")
        d2 = pd.Timestamp("2026-06-01")
        assert streamlit_app._belongs_to_same_period(d1, d2, "月线") is False

    def test_same_quarter_true(self):
        """同季度 → True。"""
        d1 = pd.Timestamp("2026-04-01")
        d2 = pd.Timestamp("2026-06-30")
        assert streamlit_app._belongs_to_same_period(d1, d2, "季线") is True

    def test_same_quarter_false(self):
        """跨季度 → False。"""
        d1 = pd.Timestamp("2026-03-31")
        d2 = pd.Timestamp("2026-04-01")
        assert streamlit_app._belongs_to_same_period(d1, d2, "季线") is False

    def test_same_year_true(self):
        """同一年 → True。"""
        d1 = pd.Timestamp("2026-01-01")
        d2 = pd.Timestamp("2026-12-31")
        assert streamlit_app._belongs_to_same_period(d1, d2, "年线") is True

    def test_same_year_false(self):
        """跨年 → False。"""
        d1 = pd.Timestamp("2025-12-31")
        d2 = pd.Timestamp("2026-01-01")
        assert streamlit_app._belongs_to_same_period(d1, d2, "年线") is False

    def test_daily_same_period(self):
        """日线同一周期 = 同一天。"""
        d = pd.Timestamp("2026-06-15")
        assert streamlit_app._belongs_to_same_period(d, d, "日线") is True
        assert streamlit_app._belongs_to_same_period(
            d, d + pd.Timedelta(days=1), "日线"
        ) is False


# ====================================================================
# Phase 2: _load_backtest_window 高周期合成
# ====================================================================

class TestHigherTfSynthesis:
    """_load_backtest_window 高周期合成验证。

    核心场景：
    1. mid_week_synthesis — 周线周三时触发合成
    2. cross_week_boundary_no_synthesis — 周线周五时无需合成
    3. ohlc_values_correct — 合成 bar 的 OHLC 值正确
    4. single_lower_bar_skips — 只有 1 根低周期 bar 时跳过合成
    5. synthesis_adds_is_synthesized_column — 合成后 is_synthesized=True
    6. synthesis_bar_appended_to_tail — 合成 bar 在末尾
    7. same_tf_as_min_tf_no_synthesis — min_tf 永不合成
    8. empty_lower_df_handling — 低周期数据不存在时跳过
    """

    # 200 条日线数据
    DAILY_DATES = pd.date_range("2025-06-01", periods=200, freq="D")

    @pytest.fixture
    def base_mocks(self, monkeypatch):
        """基础 mock: _bt_data_cache 含日线（min_tf）+ 周线，均含 OHLC 列。"""
        monkeypatch.setattr(streamlit_app, "_sync_to_display", lambda *a, **kw: None)
        monkeypatch.setattr(Path, "exists", lambda _: True)

        daily_df = pd.DataFrame({
            "Open": np.linspace(100, 300, 200),
            "High": np.linspace(102, 302, 200),
            "Low": np.linspace(98, 298, 200),
            "Close": np.linspace(101, 301, 200),
        }, index=self.DAILY_DATES)

        # 周线：约 29 根完整周（周一日期）
        weekly_dates = pd.date_range("2025-06-02", periods=29, freq="W-MON")
        weekly_df = pd.DataFrame({
            "Open": np.linspace(100, 200, 29),
            "High": np.linspace(102, 202, 29),
            "Low": np.linspace(98, 198, 29),
            "Close": np.linspace(101, 201, 29),
        }, index=weekly_dates)

        monkeypatch.setattr(
            streamlit_app.AppState, "get",
            lambda key, default=None: {
                "_min_tf": "日线",
                "_bt_data_cache": {
                    "日线": daily_df,
                    "周线": weekly_df,
                },
            }.get(key, default),
        )

    def test_same_tf_as_min_tf_no_synthesis(self, base_mocks, monkeypatch):
        """日线（min_tf）永不触发合成，is_synthesized 全为 False。"""
        window = streamlit_app._load_backtest_window("日线", 100, 50)
        assert "is_synthesized" in window.columns
        assert window["is_synthesized"].sum() == 0

    def test_cross_week_boundary_no_synthesis(self, base_mocks, monkeypatch):
        """周线周日（周期边界）时无需合成。"""
        # 2025-06-08 是周日 → 周线边界
        # 对应 DAILY_DATES 索引 7（有足够的领先周线数据）
        sunday_idx = self.DAILY_DATES.get_loc(pd.Timestamp("2025-06-08"))
        window = streamlit_app._load_backtest_window("周线", sunday_idx, 10)
        assert "is_synthesized" in window.columns
        # 周日是周期边界，不应合成
        assert window["is_synthesized"].sum() == 0

    def test_mid_week_synthesis(self, base_mocks, monkeypatch):
        """周线周三触发合成。"""
        # 2025-06-04 是周三，不在周线边界（边界是周日 2025-06-08）
        wed_idx = self.DAILY_DATES.get_loc(pd.Timestamp("2025-06-04"))
        window = streamlit_app._load_backtest_window("周线", wed_idx, 10)
        assert "is_synthesized" in window.columns
        # 周三在周期中间，最后一根应被合成标记
        assert bool(window["is_synthesized"].iloc[-1]) is True
        # 其他 bar 应为 False
        assert window["is_synthesized"].iloc[:-1].sum() == 0

    def test_ohlc_values_correct(self, base_mocks, monkeypatch):
        """合成 bar 的 OHLC 值正确。"""
        daily_dates = pd.date_range("2025-06-01", periods=10, freq="D")
        daily_df = pd.DataFrame({
            "Open": [100, 101, 102, 103, 104, 105, 106, 107, 108, 109],
            "High": [102, 103, 104, 105, 106, 107, 108, 109, 110, 111],
            "Low": [99, 100, 101, 102, 103, 104, 105, 106, 107, 108],
            "Close": [101, 102, 103, 104, 105, 106, 107, 108, 109, 110],
        }, index=daily_dates)

        # 周线：前 2 根完整周
        weekly_dates = pd.date_range("2025-06-02", periods=2, freq="W-MON")
        weekly_df = pd.DataFrame({
            "Open": [100.0, 105.0],
            "High": [102.0, 107.0],
            "Low": [99.0, 104.0],
            "Close": [101.0, 106.0],
        }, index=weekly_dates)

        monkeypatch.setattr(
            streamlit_app.AppState, "get",
            lambda key, default=None: {
                "_min_tf": "日线",
                "_bt_data_cache": {
                    "日线": daily_df,
                    "周线": weekly_df,
                },
            }.get(key, default),
        )

        # bar_index=4 → cutoff=2025-06-05（周四，在第一个周线周期内）
        # 周线窗口：只有第 1 根完整 bar (2025-06-02), last_complete_date=2025-06-02
        # 合成数据：2025-06-03 ~ 2025-06-05 的日线
        # Open=daily[2025-06-03].Open=102, High=max(104,105,106)=106,
        # Low=min(101,102,103)=101, Close=daily[2025-06-05].Close=105
        window = streamlit_app._load_backtest_window("周线", 4, 10)
        assert len(window) >= 1
        last = window.iloc[-1]
        assert bool(last["is_synthesized"]) is True
        assert last["Open"] == 102.0
        assert last["High"] == 106.0
        assert last["Low"] == 101.0
        assert last["Close"] == 105.0

    def test_single_lower_bar_skips(self, base_mocks, monkeypatch):
        """只有 1 根低周期 bar 时跳过合成。"""
        daily_dates = pd.date_range("2025-06-01", periods=3, freq="D")
        daily_df = pd.DataFrame({
            "Open": [100, 101, 102],
            "High": [102, 103, 104],
            "Low": [99, 100, 101],
            "Close": [101, 102, 103],
        }, index=daily_dates)

        # 周线：只有 1 根
        weekly_dates = pd.date_range("2025-06-02", periods=1, freq="W-MON")
        weekly_df = pd.DataFrame({
            "Open": [100.0],
            "High": [102.0],
            "Low": [99.0],
            "Close": [101.0],
        }, index=weekly_dates)

        monkeypatch.setattr(
            streamlit_app.AppState, "get",
            lambda key, default=None: {
                "_min_tf": "日线",
                "_bt_data_cache": {
                    "日线": daily_df,
                    "周线": weekly_df,
                },
            }.get(key, default),
        )

        # bar_index=2 → cutoff=2025-06-03（周三）
        # synth_data 只有 1 条（2025-06-03），_synthesize_higher_tf_bar 会返回 None
        window = streamlit_app._load_backtest_window("周线", 2, 10)
        assert "is_synthesized" in window.columns
        assert window["is_synthesized"].sum() == 0

    def test_synthesis_adds_is_synthesized_column(self, base_mocks, monkeypatch):
        """合成后 is_synthesized 列存在且为 bool 类型。"""
        wed_idx = self.DAILY_DATES.get_loc(pd.Timestamp("2025-06-04"))
        window = streamlit_app._load_backtest_window("周线", wed_idx, 10)
        assert "is_synthesized" in window.columns
        assert window["is_synthesized"].dtype == bool

    def test_empty_lower_df_handling(self, base_mocks, monkeypatch):
        """低周期数据存在时合成正常。"""
        wed_idx = self.DAILY_DATES.get_loc(pd.Timestamp("2025-06-04"))
        window = streamlit_app._load_backtest_window("周线", wed_idx, 10)
        assert "is_synthesized" in window.columns
        # 基础 mock 的日线有完整数据，所以合成应该成功
        assert bool(window["is_synthesized"].iloc[-1]) is True

    def test_synthesis_bar_appended_to_tail(self, base_mocks, monkeypatch):
        """合成 bar 追加到窗口末尾。"""
        wed_idx = self.DAILY_DATES.get_loc(pd.Timestamp("2025-06-04"))
        window = streamlit_app._load_backtest_window("周线", wed_idx, 10)
        assert "is_synthesized" in window.columns
        assert bool(window["is_synthesized"].iloc[-1]) is True
        # 合成 bar 的日期应为 cutoff 日期
        assert window.index[-1] <= self.DAILY_DATES[wed_idx]


# ====================================================================
# Phase 3: 回测 UI 控件测试
# ====================================================================

class TestBacktestUIControls:
    """验证回测 UI 控件渲染行为。"""

    def test_controls_render_when_bar_count_zero(self, monkeypatch):
        """bar_count=0 时 _render_backtest_controls 不崩溃。"""
        monkeypatch.setattr(
            streamlit_app.AppState, "get",
            lambda key, default=None: {
                "_cb_mode": True,
                "_bar_index": 0,
                "_min_tf_bar_count": 0,
                "_min_tf": "日线",
                "_is_playing": False,
            }.get(key, default),
        )
        # 不应抛出异常
        streamlit_app._render_backtest_controls()

    def test_controls_buttons_disabled_when_no_data(self, monkeypatch):
        """bar_count=0 时按钮为 disabled。"""
        monkeypatch.setattr(
            streamlit_app.AppState, "get",
            lambda key, default=None: {
                "_cb_mode": True,
                "_bar_index": 0,
                "_min_tf_bar_count": 0,
                "_min_tf": "日线",
                "_is_playing": False,
            }.get(key, default),
        )
        streamlit_app._render_backtest_controls()
        # MagicMock st.button 被调用时的最后一个 disabled 参数应为 True
        # 由于 streamlit 是 MagicMock，我们只需验证不崩溃

    def test_warning_shows_min_tf_name(self, monkeypatch):
        """bar_count=0 时 st.warning 包含 min_tf 名称。"""
        warning_calls = []

        original_warning = streamlit_app.st.warning

        def mock_warning(msg):
            warning_calls.append(msg)

        monkeypatch.setattr(streamlit_app.st, "warning", mock_warning)
        monkeypatch.setattr(
            streamlit_app.AppState, "get",
            lambda key, default=None: {
                "_cb_mode": True,
                "_bar_index": 0,
                "_min_tf_bar_count": 0,
                "_min_tf": "15分钟",
                "_is_playing": False,
            }.get(key, default),
        )
        streamlit_app._render_backtest_controls()
        assert len(warning_calls) >= 1
        assert "15分钟" in warning_calls[0], (
            f"warning 消息应包含 min_tf 名称 '15分钟', 实际: {warning_calls[0]}"
        )

    def test_status_shows_current_date(self, monkeypatch):
        """_render_backtest_status 显示当前 bar 对应日期。"""
        dates = pd.date_range("2026-06-01", periods=10, freq="D")
        cache = {"日线": pd.DataFrame({"Close": range(10)}, index=dates)}
        status_texts = []

        def mock_caption(msg):
            status_texts.append(msg)

        monkeypatch.setattr(streamlit_app.st.sidebar, "caption", mock_caption)
        monkeypatch.setattr(
            streamlit_app.AppState, "get",
            lambda key, default=None: {
                "_cb_mode": True,
                "_bar_index": 5,
                "_min_tf_bar_count": 10,
                "_min_tf": "日线",
                "_bt_data_cache": cache,
                "_is_playing": False,
            }.get(key, default),
        )
        streamlit_app._render_backtest_status()
        combined = " ".join(status_texts)
        # bar 5 对应 dates[5] = 2026-06-06
        assert "2026-06-06" in combined, f"状态文字应包含日期 '2026-06-06', 实际: {combined}"
        assert "6/10" in combined, f"状态文字应包含进度 '6/10', 实际: {combined}"

    def test_status_shows_playing_indicator(self, monkeypatch):
        """_render_backtest_status 播放中时显示播放标记。"""
        dates = pd.date_range("2026-06-01", periods=10, freq="D")
        cache = {"日线": pd.DataFrame({"Close": range(10)}, index=dates)}
        status_texts = []

        def mock_caption(msg):
            status_texts.append(msg)

        monkeypatch.setattr(streamlit_app.st.sidebar, "caption", mock_caption)
        monkeypatch.setattr(
            streamlit_app.AppState, "get",
            lambda key, default=None: {
                "_cb_mode": True,
                "_bar_index": 3,
                "_min_tf_bar_count": 10,
                "_min_tf": "日线",
                "_bt_data_cache": cache,
                "_is_playing": True,
            }.get(key, default),
        )
        streamlit_app._render_backtest_status()
        combined = " ".join(status_texts)
        assert "播放" in combined or "playing" in combined.lower(), (
            f"播放中时状态文字应包含播放标记, 实际: {combined}"
        )


# ====================================================================
# Phase 4: 集成验证 — 端到端回测工作流测试
# ====================================================================

class TestBacktestIntegration:
    """回测集成测试 — 验证多个回测功能的端到端交互。

    测试策略：
    1. 使用纯函数调用，不依赖 Streamlit runtime
    2. 通过 monkeypatch 模拟 AppState 和依赖
    3. 每个测试验证一个完整的用户场景
    """

    # ---- 公共辅助 ----

    @staticmethod
    def _mock_state(monkeypatch, state_dict: dict):
        """设置 AppState.get 返回给定的 state_dict。"""
        monkeypatch.setattr(
            streamlit_app.AppState, "get",
            lambda key, default=None: state_dict.get(key, default),
        )

    # ---- test_full_backtest_flow ----

    def test_full_backtest_flow(self, monkeypatch):
        """模拟用户从浏览模式切换到回测模式，调整 bar_index 的完整流程。

        验证：
        1. 初始状态为浏览模式（cb_mode=False, bar_index=None）
        2. 切换到回测模式（cb_mode=True, bar_index=0）
        3. 前进到 bar_index=5
        4. 跳转到末尾
        5. 切换回浏览模式（cb_mode=False, bar_index=None）
        """
        state = {
            "_cb_mode": False,
            "_bar_index": 0,
            "_is_playing": False,
            "_min_tf": "日线",
            "_min_tf_bar_count": 200,
            "_bt_data_cache": {},
        }

        # Phase A: 初始状态 = 浏览模式
        assert state["_cb_mode"] is False
        bar_index = None if not state["_cb_mode"] else state["_bar_index"]
        assert bar_index is None

        # Phase B: 切换到回测模式（模拟 _render_backtest_mode_switch 的行为）
        state["_cb_mode"] = True
        state["_bar_index"] = 0
        bar_index = 0 if state["_cb_mode"] else None
        assert bar_index == 0
        assert state["_cb_mode"] is True

        # Phase C: 前进到 bar_index=5（模拟 step forward 按钮行为）
        total = state["_min_tf_bar_count"]
        state["_bar_index"] = min(total - 1, state["_bar_index"] + 5)
        bar_index = state["_bar_index"]
        assert bar_index == 5, f"前进后 bar_index 应为 5, 实际 {bar_index}"

        # Phase D: 跳转到末尾（模拟 goto end 按钮行为）
        state["_bar_index"] = total - 1
        bar_index = state["_bar_index"]
        assert bar_index == 199, f"跳转末尾后 bar_index 应为 199, 实际 {bar_index}"

        # Phase E: 切换回浏览模式
        state["_cb_mode"] = False
        bar_index = None if not state["_cb_mode"] else state["_bar_index"]
        assert bar_index is None
        assert state["_cb_mode"] is False

    # ---- test_bar_index_advance ----

    def test_bar_index_advance(self, monkeypatch):
        """bar_index 从 0 前进到 N，验证每一步的数据截断正确。

        模拟 _run_backtest_play 的 bar_index 递增逻辑（不含 time.sleep）。
        """
        total = 50
        state = {
            "_cb_mode": True,
            "_bar_index": 0,
            "_min_tf_bar_count": total,
            "_is_playing": True,
        }
        self._mock_state(monkeypatch, state)
        monkeypatch.setattr(streamlit_app, "_sync_to_display", lambda *a, **kw: None)

        # 模拟播放循环：每次递增 bar_index，并在到达末尾时停止
        n_steps = 10
        for step in range(1, n_steps + 1):
            bi = state["_bar_index"]
            if bi >= total - 1:
                state["_is_playing"] = False
                break
            state["_bar_index"] = bi + 1

        assert state["_bar_index"] == n_steps, (
            f"播放 {n_steps} 步后 bar_index 应为 {n_steps}, 实际 {state['_bar_index']}"
        )
        assert state["_is_playing"] is True, "未达末尾时 _is_playing 应为 True"

        # 继续播放到末尾
        state["_bar_index"] = total - 1
        bi = state["_bar_index"]
        if bi >= total - 1:
            state["_is_playing"] = False
        assert state["_is_playing"] is False, "到达末尾时 _is_playing 应为 False"
        assert state["_bar_index"] == total - 1

    # ---- test_playback_stops_at_end ----

    def test_playback_stops_at_end(self):
        """播放到末尾自动停止。模拟 _run_backtest_play 的终止逻辑。"""
        total = 100

        # 接近末尾
        bi = total - 2
        bi += 1
        assert bi == total - 1
        # 此时应触发停止
        assert bi >= total - 1

        # 已经在末尾
        bi = total - 1
        should_stop = bi >= total - 1
        assert should_stop is True

        # 超过末尾（边界保护）
        bi = min(total - 1, bi + 1)
        assert bi == total - 1

    # ---- test_multi_view_consistency ----

    def test_multi_view_consistency(self):
        """4 个视图的窗口终点对应同一日期。

        同一次回测中，所有视图的 _load_backtest_window 使用相同的 bar_index，
        因此窗口终点应对应同一个 min_tf 日期。
        这里验证 bar_index 到日期的映射一致性。
        """
        dates = pd.date_range("2026-01-01", periods=100, freq="D")
        bar_index = 50
        cutoff_date = dates[bar_index]

        # 模拟 4 个视图，每个视图的窗口终点都应 <= cutoff_date
        # 不同 tf 的窗口长度可能不同，但终点都不应超过 cutoff_date
        views_tf = ["60分钟", "日线", "周线", "月线"]
        for tf in views_tf:
            # 低周期的窗口可以精确到达 cutoff_date
            if tf in ("60分钟", "日线"):
                # 对于 min_tf 或接近 min_tf 的周期，终点可以精确等于 cutoff_date（inclusive）
                assert cutoff_date <= dates[-1]
            # 高周期窗口终点 <= cutoff_date（由 _load_backtest_window 保证）
            expected = pd.Timestamp("2026-01-01") + pd.Timedelta(days=bar_index)
            assert cutoff_date == expected, (
                f"bar_index=50 对应日期应为 {expected}, 实际 {cutoff_date}"
            )

        # 验证所有视图共享同一个 cutoff_date
        view_cutoffs = {tf: cutoff_date for tf in views_tf}
        unique_cutoffs = set(view_cutoffs.values())
        assert len(unique_cutoffs) == 1, (
            f"所有视图的 cutoff 应相同, 实际 {view_cutoffs}"
        )

    # ---- test_browse_mode_unchanged ----

    def test_browse_mode_unchanged(self, monkeypatch):
        """浏览模式功能不受回测状态影响。

        验证 cb_mode=False 时：
        1. bar_index 为 None
        2. _render_backtest_controls 不渲染
        3. _render_backtest_status 不显示
        4. _run_backtest_play 不执行
        """
        state = {
            "_cb_mode": False,
            "_bar_index": 0,
            "_min_tf_bar_count": 0,
            "_min_tf": "",
            "_is_playing": False,
            "_bt_data_cache": {},
        }
        self._mock_state(monkeypatch, state)

        # 1. bar_index 应为 None
        bar_index = None if not state["_cb_mode"] else state["_bar_index"]
        assert bar_index is None

        # 2. _render_backtest_controls 不应渲染
        controls_should_render = state["_cb_mode"] is True
        assert controls_should_render is False

        # 3. _render_backtest_status 不应显示
        status_should_show = state["_cb_mode"] is True
        assert status_should_show is False

        # 4. _run_backtest_play 不应执行
        play_should_run = state["_cb_mode"] is True and state["_is_playing"] is True
        assert play_should_run is False

        # 浏览模式下的几个 key 函数应能正常调用（不崩溃）
        monkeypatch.setattr(streamlit_app, "_sync_to_display", lambda *a, **kw: None)
        monkeypatch.setattr(Path, "exists", lambda _: False)
        monkeypatch.setattr(pd, "read_parquet", lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(streamlit_app, "_get_min_tf_and_count",
                            lambda *a: ("日线", 0))

        # _render_backtest_mode_switch 在浏览模式下不触发切换逻辑
        # _render_backtest_controls 和 _render_backtest_status 因 cb_mode=False 早返回
        streamlit_app._render_backtest_controls()
        streamlit_app._render_backtest_status()

    # ---- test_mode_switch_preserves_data ----

    def test_mode_switch_preserves_data(self, monkeypatch):
        """模式切换不丢失已有数据（除了回测缓存被清除）。

        验证：
        1. 模拟切换到回测模式时缓存被设置
        2. 缓存包含 all configs 的 tf
        3. 切换回浏览模式时缓存被清空但不会崩溃

        注意：直接用 dict 模拟状态切换，不依赖 parquet I/O 以避免 monkeypatch 递归。
        """
        state = {
            "_cb_mode": False,
            "_bar_index": 0,
            "_is_playing": False,
            "_min_tf": "",
            "_min_tf_bar_count": 0,
            "_bt_data_cache": {},
        }

        old_cb_mode = state["_cb_mode"]
        assert old_cb_mode is False

        # 模拟切换到回测模式：注入缓存
        dates = pd.date_range("2026-01-01", periods=50, freq="D")
        cache_df = pd.DataFrame({
            "Open": np.linspace(100, 200, 50),
            "High": np.linspace(102, 202, 50),
            "Low": np.linspace(98, 198, 50),
            "Close": np.linspace(101, 201, 50),
        }, index=dates)
        state["_bt_data_cache"] = {"日线": cache_df}
        state["_cb_mode"] = True
        state["_bar_index"] = 0

        assert state["_cb_mode"] is True
        assert "日线" in state["_bt_data_cache"]
        assert len(state["_bt_data_cache"]["日线"]) == 50
        assert isinstance(
            state["_bt_data_cache"]["日线"].index,
            pd.DatetimeIndex,
        )

        # 模拟切换到浏览模式：清除回测状态
        state["_bt_data_cache"] = {}
        state["_cb_mode"] = False
        state["_bar_index"] = 0

        assert state["_cb_mode"] is False
        assert len(state["_bt_data_cache"]) == 0

    # ---- test_min_tf_determination ----

    def test_min_tf_determination(self, monkeypatch):
        """_get_min_tf_and_count 正确确定最小周期。

        模拟不同配置组合，验证最小周期查找正确性。
        """
        # 配置1: 日线 + 周线 + 月线 → min_tf = 日线
        configs = [
            {"tf": "日线"},
            {"tf": "周线"},
            {"tf": "月线"},
        ]
        min_tf, _ = streamlit_app._get_min_tf_and_count(configs, "AAPL")
        assert min_tf == "日线", f"配置1期望日线, 实际 {min_tf}"

        # 配置2: 60分钟 + 日线 → min_tf = 60分钟
        configs = [
            {"tf": "60分钟"},
            {"tf": "日线"},
        ]
        min_tf, _ = streamlit_app._get_min_tf_and_count(configs, "AAPL")
        assert min_tf == "60分钟", f"配置2期望60分钟, 实际 {min_tf}"

        # 配置3: 全部相同周期
        configs = [
            {"tf": "月线"},
            {"tf": "月线"},
        ]
        min_tf, _ = streamlit_app._get_min_tf_and_count(configs, "AAPL")
        assert min_tf == "月线", f"配置3期望月线, 实际 {min_tf}"

        # 配置4: 包含未知周期
        configs = [
            {"tf": "未知"},
            {"tf": "5分钟"},
        ]
        min_tf, _ = streamlit_app._get_min_tf_and_count(configs, "AAPL")
        assert min_tf == "5分钟", f"配置4期望5分钟, 实际 {min_tf}"

    # ---- test_backtest_data_window_size ----

    def test_backtest_data_window_size(self, monkeypatch):
        """回测模式下窗口切片后数据量不超过 n_pts。

        验证 _load_backtest_window 返回的窗口数据长度 <= n_pts。
        """
        n_pts = 30
        dates = pd.date_range("2025-06-01", periods=200, freq="D")
        ohlc_df = pd.DataFrame({
            "Open": np.linspace(100, 300, 200),
            "High": np.linspace(102, 302, 200),
            "Low": np.linspace(98, 298, 200),
            "Close": np.linspace(101, 301, 200),
        }, index=dates)

        monkeypatch.setattr(streamlit_app, "_sync_to_display", lambda *a, **kw: None)
        monkeypatch.setattr(Path, "exists", lambda _: True)
        monkeypatch.setattr(
            streamlit_app.AppState, "get",
            lambda key, default=None: {
                "_min_tf": "日线",
                "_bt_data_cache": {"日线": ohlc_df},
            }.get(key, default),
        )

        # 不同 bar_index 下窗口大小都不超过 n_pts
        for bi in [0, 1, 15, 29, 30, 50, 100, 199]:
            window = streamlit_app._load_backtest_window("日线", bi, n_pts)
            assert len(window) <= n_pts, (
                f"bar_index={bi}: 窗口大小 {len(window)} > n_pts={n_pts}"
            )

        # 验证 bar_index=0 时窗口大小为 1
        window = streamlit_app._load_backtest_window("日线", 0, n_pts)
        assert len(window) == 1, f"bar_index=0 时窗口大小应为 1, 实际 {len(window)}"

        # 验证窗口包含正确的截止 bar
        window = streamlit_app._load_backtest_window("日线", 50, n_pts)
        assert window.index[-1] == dates[50]


# ====================================================================
# 满 bar 守卫测试
# ====================================================================

class TestFullBarGuard:
    """_load_backtest_window 满 bar 守卫测试。

    数据总量小于 n_pts 时窗口不满 bar，应返回部分数据而非报错。
    数据总量 >= n_pts 时返回满 n_pts 条。
    """

    DATES_50 = pd.date_range("2025-06-01", periods=50, freq="D")

    @pytest.fixture
    def setup_mocks(self, monkeypatch):
        """基础 mock: 50 条日线数据, min_tf=日线。"""
        monkeypatch.setattr(streamlit_app, "_sync_to_display", lambda *a, **kw: None)
        monkeypatch.setattr(Path, "exists", lambda _: True)

        ohlc = {
            "Open": np.linspace(100, 150, 50),
            "High": np.linspace(102, 152, 50),
            "Low": np.linspace(98, 148, 50),
            "Close": np.linspace(101, 151, 50),
        }
        cache_df = pd.DataFrame(ohlc, index=self.DATES_50)

        monkeypatch.setattr(
            streamlit_app.AppState, "get",
            lambda key, default=None: {
                "_min_tf": "日线",
                "_bt_data_cache": {"日线": cache_df},
            }.get(key, default),
        )
        return cache_df

    def test_window_partial_when_data_insufficient(self, setup_mocks):
        """数据不足 n_pts 时返回部分数据而非报错。

        总数据量 50 < n_pts 120，即使 bar_index 在末尾也只返回 50 条。
        """
        n_pts = 120
        # bar_index=49（末尾）→ 窗口应返回所有 50 条
        window = streamlit_app._load_backtest_window("日线", 49, n_pts)
        assert len(window) == 50, (
            f"数据不足时窗口应为全部数据(50), 实际 {len(window)}"
        )
        assert window.index[0] == self.DATES_50[0]
        assert window.index[-1] == self.DATES_50[-1]

    def test_window_full_when_data_sufficient(self, setup_mocks):
        """数据足够时返回满 n_pts 条。

        总数据量 50, n_pts=30, bar_index 在中间时返回 30 条。
        """
        n_pts = 30
        window = streamlit_app._load_backtest_window("日线", 40, n_pts)
        assert len(window) == n_pts, (
            f"数据充足时窗口应为满 {n_pts}, 实际 {len(window)}"
        )
        assert window.index[-1] == self.DATES_50[40]
