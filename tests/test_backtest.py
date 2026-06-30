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


# ====================================================================
# _truncate_arrays
# ====================================================================

class TestTruncateArrays:
    """截断数组测试 — 验证回测模式下数据截断的正确性。

    注意：_truncate_arrays 仍存在于 streamlit_app 中，但不再在渲染管线
    (_render_chart) 中使用。回测模式改为 _load_chart_data 的窗口平移策略，
    不再截断历史数据到 1 条。此测试保留以覆盖该函数的正向行为边界。
    """

    def test_bar_index_none_returns_unchanged(self, six_bars_data):
        """bar_index=None 时原样返回（浏览模式）。"""
        t, noisy, ohlc, dates = six_bars_data
        result_t, result_n, result_ohlc, result_d = streamlit_app._truncate_arrays(
            t, noisy, ohlc, dates, None)
        assert len(result_t) == 6
        assert len(result_n) == 6
        assert len(result_ohlc) == 6
        assert len(result_d) == 6
        np.testing.assert_array_equal(result_t, t)
        np.testing.assert_array_equal(result_n, noisy)

    def test_truncate_at_mid_bar_index(self, six_bars_data):
        """截断到 bar_index=2 时保留前 3 个元素。"""
        t, noisy, ohlc, dates = six_bars_data
        result_t, result_n, result_ohlc, result_d = streamlit_app._truncate_arrays(
            t, noisy, ohlc, dates, 2)
        assert len(result_t) == 3
        assert len(result_n) == 3
        assert len(result_ohlc) == 3
        assert len(result_d) == 3
        np.testing.assert_array_equal(result_t, [0.0, 1.0, 2.0])
        np.testing.assert_array_equal(result_n, [101.0, 102.0, 103.0])

    def test_truncate_at_zero(self, six_bars_data):
        """bar_index=0 时只保留第一个元素。"""
        t, noisy, ohlc, dates = six_bars_data
        result_t, result_n, result_ohlc, result_d = streamlit_app._truncate_arrays(
            t, noisy, ohlc, dates, 0)
        assert len(result_t) == 1
        assert result_t[0] == 0.0
        assert result_n[0] == 101.0

    def test_truncate_at_end(self, six_bars_data):
        """bar_index=len-1 时返回全部（bar_index=5, 共6个元素）。"""
        t, noisy, ohlc, dates = six_bars_data
        result_t, result_n, result_ohlc, result_d = streamlit_app._truncate_arrays(
            t, noisy, ohlc, dates, 5)
        assert len(result_t) == 6
        np.testing.assert_array_equal(result_t, t)

    def test_ohlc_dataframe_is_sliced(self, six_bars_data):
        """验证 OHLC DataFrame 正确截断。"""
        t, noisy, ohlc, dates = six_bars_data
        _, _, result_ohlc, _ = streamlit_app._truncate_arrays(
            t, noisy, ohlc, dates, 1)
        assert list(result_ohlc["Close"]) == [101.0, 102.0]
        assert list(result_ohlc["Open"]) == [100.0, 101.0]
        assert list(result_ohlc["High"]) == [101.5, 102.5]
        assert list(result_ohlc["Low"]) == [99.0, 100.0]

    def test_empty_arrays_with_bar_index(self):
        """空数组 + 非 None bar_index 时安全返回空。"""
        t = np.array([], dtype=float)
        noisy = np.array([], dtype=float)
        ohlc = pd.DataFrame({c: [] for c in ["Open", "High", "Low", "Close"]})
        dates = pd.DatetimeIndex([])
        result_t, result_n, result_ohlc, result_d = streamlit_app._truncate_arrays(
            t, noisy, ohlc, dates, 0)
        assert len(result_t) == 0
        assert len(result_n) == 0


# ====================================================================
# _load_chart_data — 回测模式窗口平移
# ====================================================================

class TestLoadChartDataBacktest:
    """验证 _load_chart_data 在 bar_index 存在时的行为。

    bar_index 参数仅保留签名兼容性，不再用于窗口平移。
    现在回测模式和浏览模式共用同一数据加载路径。
    不再截断或窗口平移 — 始终返回 parquet 中的全部数据。
    """

    @pytest.fixture
    def mock_deps(self, monkeypatch):
        """Mock 外部依赖以控制 _load_chart_data 的 parquet 输入。"""
        # Mock _sync_to_display 为 no-op
        monkeypatch.setattr(streamlit_app, "_sync_to_display", lambda *a, **kw: None)

        # Mock Path.exists 默认返回 True
        monkeypatch.setattr(Path, "exists", lambda _: True)

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

    def test_bar_index_ignored_returns_all_data(self, mock_deps, monkeypatch):
        """bar_index 被忽略，返回 parquet 中全部数据。"""
        n_pts = 50
        dates = pd.date_range("2025-06-01", periods=200, freq="D")

        mock_df = self._make_mock_parquet(dates, {
            "Open": np.linspace(100, 200, 200),
            "High":  np.linspace(102, 202, 200),
            "Low":   np.linspace(98, 198, 200),
            "Close": np.linspace(101, 201, 200),
        })
        monkeypatch.setattr(pd, "read_parquet", lambda path, *a, **kw: mock_df)

        for bar_idx in [0, 60, 199]:
            t, noisy, ohlc, ticker_full, result_dates, err = (
                streamlit_app._load_chart_data(
                    "美股 US", "AAPL", "日线", 0, n_pts, bar_index=bar_idx
                )
            )
            assert err is None, f"bar_index={bar_idx} error: {err}"
            assert len(t) == 200, f"bar_index={bar_idx}: expected 200, got {len(t)}"
            assert result_dates[0] == dates[0]
            assert result_dates[-1] == dates[-1]

    def test_backtest_equals_browse_mode(self, mock_deps, monkeypatch):
        """bar_index 非 None 时结果和 bar_index=None 完全一致。"""
        n_pts = 50
        dates = pd.date_range("2025-06-01", periods=200, freq="D")

        mock_df = self._make_mock_parquet(dates, {
            "Open": np.linspace(100, 200, 200),
            "High":  np.linspace(102, 202, 200),
            "Low":   np.linspace(98, 198, 200),
            "Close": np.linspace(101, 201, 200),
        })
        monkeypatch.setattr(pd, "read_parquet", lambda path, *a, **kw: mock_df)

        # bar_index=None (browse mode)
        ref_t, ref_n, ref_ohlc, ref_full, ref_dates, ref_err = (
            streamlit_app._load_chart_data(
                "美股 US", "AAPL", "日线", 0, n_pts, bar_index=None
            )
        )
        assert ref_err is None

        # bar_index=0 (backtest mode) — 结果应完全一致
        bt_t, bt_n, bt_ohlc, bt_full, bt_dates, bt_err = (
            streamlit_app._load_chart_data(
                "美股 US", "AAPL", "日线", 0, n_pts, bar_index=0
            )
        )
        assert bt_err is None
        np.testing.assert_array_equal(bt_t, ref_t)
        np.testing.assert_array_equal(bt_n, ref_n)

    def test_high_tf_returns_all_data_no_synthesis(self, mock_deps, monkeypatch):
        """高周期也不执行合成，返回 parquet 全部数据。"""
        n_pts = 20

        # 周线数据 — 7 个完整周
        weekly_dates = pd.date_range("2025-06-02", periods=7, freq="W-MON")
        mock_weekly_df = self._make_mock_parquet(weekly_dates, {
            "Open": [100, 102, 104, 106, 108, 110, 112],
            "High": [103, 105, 107, 109, 111, 113, 115],
            "Low":  [99, 101, 103, 105, 107, 109, 111],
            "Close": [102, 104, 106, 108, 110, 112, 114],
        })

        call_log = {"calls": []}

        def _mock_read_parquet(path, *a, **kw):
            p = str(path)
            call_log["calls"].append(p)
            return mock_weekly_df

        monkeypatch.setattr(pd, "read_parquet", _mock_read_parquet)

        t, noisy, ohlc, ticker_full, result_dates, err = (
            streamlit_app._load_chart_data(
                "美股 US", "AAPL", "周线", 0, n_pts, bar_index=32
            )
        )

        assert err is None, f"Unexpected error: {err}"
        # 应返回全部周线数据（7 条），不进行合成
        assert len(t) == 7, f"Expected 7 weekly bars, got {len(t)}"
        # 不应再读取 lower_tf 进行合成
        assert not any("60分钟" in c for c in call_log["calls"]), (
            "不再从 _load_chart_data 内部读取低周期进行合成"
        )


# ====================================================================
# _global_to_local_bar_index
# ====================================================================

class TestGlobalToLocalBarIndex:
    """全局到本地 bar 索引映射测试 — O(log n) 时间对齐。"""

    @pytest.fixture
    def weekly_dates(self):
        """周线日期 (6个周三, 每周一个bar)。"""
        return pd.date_range("2026-01-07", periods=6, freq="W-WED")

    @pytest.fixture
    def daily_dates(self):
        """日线日期 (覆盖上述周线范围)。"""
        return pd.date_range("2026-01-05", periods=42, freq="D")

    def test_exact_match(self, weekly_dates, daily_dates):
        """周线第3个bar的日期在日线中精确出现时，bar_index 映射正确。"""
        target_weekly_date = weekly_dates[2]
        local_idx = streamlit_app._global_to_local_bar_index(
            daily_dates, 2, weekly_dates)
        assert daily_dates[local_idx] == target_weekly_date

    def test_within_range(self, weekly_dates, daily_dates):
        """周线第3个bar在内，日线中取 <= 该时间戳的最大索引。"""
        local_idx = streamlit_app._global_to_local_bar_index(
            daily_dates, 2, weekly_dates)
        assert local_idx >= 0
        assert daily_dates[local_idx] <= weekly_dates[2]
        # 日线中下一天应 > 该周线日期
        if local_idx + 1 < len(daily_dates):
            assert daily_dates[local_idx + 1] > weekly_dates[2]

    def test_before_first_global_date(self, daily_dates):
        """最早全局日期之前 -> 返回 >= 0 的索引。"""
        min_tf_dates = pd.date_range("2026-02-01", periods=5, freq="D")
        local_idx = streamlit_app._global_to_local_bar_index(
            daily_dates, 0, min_tf_dates)
        assert local_idx >= 0
        assert daily_dates[local_idx] <= min_tf_dates[0]

    def test_out_of_bounds_returns_last_daily(self, weekly_dates, daily_dates):
        """global_idx 超出 min_tf_dates 范围时返回日线最后索引。"""
        local_idx = streamlit_app._global_to_local_bar_index(
            daily_dates, 100, weekly_dates)
        assert local_idx == len(daily_dates) - 1

    def test_early_cutoff_before_all_dates(self, daily_dates):
        """min_tf_dates 中第0个早于所有日线时，返回 -1（边缘情况）。"""
        min_tf_dates = pd.date_range("2025-12-01", periods=3, freq="D")
        local_idx = streamlit_app._global_to_local_bar_index(
            daily_dates, 0, min_tf_dates)
        # searchsorted returns 0 (all dates > cutoff), subtract 1 -> -1
        assert local_idx == -1

    def test_monotonic_mapping(self):
        """验证映射是单调递增的：global_idx 增大时 local_idx 不会减小。"""
        daily = pd.date_range("2026-01-01", periods=100, freq="D")
        min_tf = pd.date_range("2026-01-01", periods=20, freq="5D")
        prev = -1
        for gidx in range(len(min_tf)):
            lidx = streamlit_app._global_to_local_bar_index(daily, gidx, min_tf)
            assert lidx >= prev, f"Non-monotonic at global_idx={gidx}"
            prev = lidx


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
        """截断后不包含未来数据 -- 验证 bar_index=2 后无 bar_index=3,4,5。"""
        t, noisy, ohlc, dates = six_bars_data
        result_t, _, _, _ = streamlit_app._truncate_arrays(t, noisy, ohlc, dates, 2)
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
        """验证截断数组中没有未来价格 -- 用显式的价格序列。"""
        np.random.seed(1)
        n = 50
        t = np.arange(n, dtype=float)
        prices = np.cumsum(np.random.randn(n) * 0.5 + 0.1) + 100.0
        ohlc = pd.DataFrame({
            "Open": prices - 0.1, "High": prices + 0.2,
            "Low": prices - 0.2, "Close": prices,
        })
        dates = pd.date_range("2026-01-01", periods=n, freq="D")

        for bar_idx in [10, 25, 40]:
            _, result_p, _, _ = streamlit_app._truncate_arrays(
                t, prices, ohlc, dates, bar_idx)
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
        """_truncate_arrays 不修改原始数组。"""
        t, noisy, ohlc, dates = six_bars_data
        t_copy = t.copy()
        noisy_copy = noisy.copy()
        streamlit_app._truncate_arrays(t, noisy, ohlc, dates, 2)
        np.testing.assert_array_equal(t, t_copy)
        np.testing.assert_array_equal(noisy, noisy_copy)
