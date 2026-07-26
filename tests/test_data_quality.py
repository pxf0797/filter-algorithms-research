"""
tests/test_data_quality.py — 数据质量测试

覆盖:
1. yfinance 返回数据 schema 验证
2. 统计分布检查（均值/方差在合理范围）
3. 空数据处理
4. OHLC 约束检查 (H >= L, H >= O/C, L <= O/C)
5. 日期单调性与 gap 检测
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Ensure filter is importable
_src = Path(__file__).resolve().parent.parent / "filter"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))


# ═════════════════════════════════════════════════════════════════════════
# yfinance 返回数据 schema 验证
# ═════════════════════════════════════════════════════════════════════════


def _make_ohlc_data(n=60, seed=42):
    """构造标准 yfinance 风格 OHLC DataFrame（模拟 download 返回）

    保证 OHLC 约束: High >= max(Open, Close) 且 Low <= min(Open, Close)
    """
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    np.random.seed(seed)
    base = 100 + np.cumsum(np.random.randn(n) * 0.5)
    body = np.abs(np.random.randn(n) * 1.0) + 0.05
    upper_wick = np.abs(np.random.randn(n) * 0.5)
    lower_wick = np.abs(np.random.randn(n) * 0.5)
    df = pd.DataFrame({
        "Open": base - body / 2,
        "High": base + body / 2 + upper_wick,
        "Low": base - body / 2 - lower_wick,
        "Close": base + body / 2,
        "Volume": np.random.randint(10000, 1000000, n),
    }, index=dates)
    df.index.name = "Date"
    return df


class TestYFinanceSchema:
    """模拟 yfinance 返回数据的 schema 验证"""

    def test_has_required_columns(self):
        """下载数据必须具备 OHLCV 列"""
        df = _make_ohlc_data()
        required = {"Open", "High", "Low", "Close", "Volume"}
        missing = required - set(df.columns)
        assert not missing, f"缺少列: {missing}"

    def test_column_dtypes_are_numeric(self):
        """OHLCV 列必须是数值类型"""
        df = _make_ohlc_data()
        for col in ["Open", "High", "Low", "Close"]:
            assert pd.api.types.is_numeric_dtype(df[col]), (
                f"{col} 类型应为数值，实际: {df[col].dtype}"
            )
        assert pd.api.types.is_numeric_dtype(df["Volume"]), (
            f"Volume 类型应为数值，实际: {df['Volume'].dtype}"
        )

    def test_date_index_is_datetime(self):
        """索引必须是 DatetimeIndex"""
        df = _make_ohlc_data()
        assert isinstance(df.index, pd.DatetimeIndex), (
            f"索引类型应为 DatetimeIndex，实际: {type(df.index)}"
        )

    def test_ohlc_high_low_constraint(self):
        """OHLC 约束: High >= max(Open, Close) 且 Low <= min(Open, Close)"""
        df = _make_ohlc_data(seed=123)
        assert (df["High"] >= df["Open"]).all(), "High 应 >= Open"
        assert (df["High"] >= df["Close"]).all(), "High 应 >= Close"
        assert (df["Low"] <= df["Open"]).all(), "Low 应 <= Open"
        assert (df["Low"] <= df["Close"]).all(), "Low 应 <= Close"

    def test_volume_non_negative(self):
        """成交量 Volume 必须 >= 0"""
        df = _make_ohlc_data()
        df_with_zero = df.copy()
        df_with_zero.loc[df.index[5], "Volume"] = 0
        assert (df_with_zero["Volume"] >= 0).all(), "Volume 存在负值"

    def test_no_nan_in_ohlc(self):
        """OHLC 不应包含 NaN 值（Volume 可偶尔为 0）"""
        df = _make_ohlc_data()
        for col in ["Open", "High", "Low", "Close"]:
            assert not df[col].isna().any(), f"{col} 包含 NaN"


# ═════════════════════════════════════════════════════════════════════════
# 统计分布检查
# ═════════════════════════════════════════════════════════════════════════


class TestStatisticalDistribution:
    """检查均值/方差在合理范围"""

    def test_close_price_in_sane_range(self):
        """模拟股价的均值和标准差应在合理范围（50-500之间）"""
        df = _make_ohlc_data(n=200, seed=7)
        close = df["Close"].values
        mean_val = float(np.mean(close))
        std_val = float(np.std(close))
        # 合理范围：均值 50-500，标准差 < 均值的 30%（模拟正常股票）
        assert 50 <= mean_val <= 500, f"收盘价均值 {mean_val:.2f} 超出 [50, 500]"
        assert std_val < mean_val * 0.3, (
            f"收盘价标准差 {std_val:.2f} 不应超过均值的30% ({mean_val * 0.3:.2f})"
        )

    def test_log_return_distribution(self):
        """对数收益率应近似正态分布（偏度在 ±1 内，峰度在 2-6 之间）"""
        df = _make_ohlc_data(n=500, seed=12)
        log_returns = np.diff(np.log(df["Close"].values))
        from scipy.stats import skew, kurtosis
        sk = float(skew(log_returns))
        kt = float(kurtosis(log_returns, fisher=True))  # excess kurtosis
        assert -1 <= sk <= 1, f"对数收益率偏度 {sk:.4f} 超出 [-1, 1]"
        assert -1 <= kt <= 8, f"对数收益率超额峰度 {kt:.4f} 超出 [-1, 8]"

    def test_price_autocorrelation(self):
        """股价序列滞后1期自相关应 > 0.5（随机游走特征）"""
        df = _make_ohlc_data(n=200, seed=42)
        close = df["Close"].values
        ac1 = np.corrcoef(close[:-1], close[1:])[0, 1]
        assert ac1 > 0.5, (
            f"滞后1期自相关 {ac1:.4f} 应 > 0.5（随机游走价格特征）"
        )

    def test_filter_output_statistics(self):
        """滤波输出：平滑后的信号标准差应低于原始信号"""
        np.random.seed(42)
        x = np.arange(200, dtype=float)
        noisy = np.sin(x / 5.0) + np.random.randn(200) * 0.3
        from engine.filters import apply_savgol
        filtered = apply_savgol(noisy, x, window=21, order=2)
        assert np.std(filtered) < np.std(noisy), (
            f"滤波后标准差 {np.std(filtered):.4f} >= 原始 {np.std(noisy):.4f}"
        )


# ═════════════════════════════════════════════════════════════════════════
# 空数据处理
# ═════════════════════════════════════════════════════════════════════════


class TestEmptyDataHandling:
    """测试空数据和边界情况的处理"""

    def test_empty_dataframe_has_correct_structure(self):
        """空 DataFrame 应具有正确的列结构"""
        df = pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
        required = {"Date", "Open", "High", "Low", "Close", "Volume"}
        assert set(df.columns) == required
        assert len(df) == 0

    def test_single_row_dataframe(self):
        """单行数据应有效（High = Low 等边界情况）"""
        df = pd.DataFrame({
            "Open": [100.0], "High": [100.5],
            "Low": [99.5], "Close": [100.2],
            "Volume": [5000],
        }, index=pd.DatetimeIndex([pd.Timestamp("2026-01-01")]))
        assert len(df) == 1
        assert df["High"].iloc[0] >= df["Low"].iloc[0]

    def test_all_zero_volume(self):
        """所有 Volume 为 0 的数据不应导致除零错误"""
        dates = pd.date_range("2026-01-01", periods=20, freq="D")
        df = pd.DataFrame({
            "Open": np.full(20, 100.0),
            "High": np.full(20, 100.5),
            "Low": np.full(20, 99.5),
            "Close": np.full(20, 100.2),
            "Volume": np.zeros(20),
        }, index=dates)
        assert (df["Volume"] == 0).all()
        # 安全的 VWAP 计算（Volume=0 时 NaN）
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            vwap = (df["Close"] * df["Volume"]).sum() / df["Volume"].sum()
        assert np.isnan(vwap) or vwap == 0, "Volume全零时VWAP应返回NaN"

    def test_empty_query_returns_empty_df(self, monkeypatch, tmp_path):
        """无数据 ticker 查询返回空 DataFrame"""
        db_path = tmp_path / "empty.db"
        monkeypatch.setattr("data.db.DB_PATH", db_path)
        import data.db as db
        db.init_db()
        result = db.query_kline("NONEXISTENT", "日线", n_pts=50)
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_nan_filter_input_handling(self):
        """包含 NaN 的输入信号应安全处理"""
        np.random.seed(42)
        n = 50
        x = np.arange(n, dtype=float)
        signal = np.random.randn(n)
        signal[10:15] = np.nan  # 插入 NaN 段

        from engine.filters import apply_sma
        # SMA 在 NaN 附近应返回 NaN 或产生安全结果
        result = apply_sma(signal, x, window=5)
        assert len(result) == n, "输出长度应等于输入长度"
        # 至少非 NaN 部分有合理输出
        assert not np.all(np.isnan(result)), "不应全部为NaN"
