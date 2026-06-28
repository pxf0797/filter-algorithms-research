"""Tests for filter_app.services.data_loader — target 70%+ coverage."""

from unittest.mock import patch, MagicMock
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# conftest.py adds filter_app/ to sys.path, so imports are: services.data_loader
# Patch paths also use services.data_loader.xxx


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

def _mock_ohlc_df(days=50, seed=42, start="2024-01-01"):
    """Build a standard OHLC DataFrame with a DatetimeIndex."""
    dates = pd.date_range(start, periods=days, freq="D")
    np.random.seed(seed)
    close = np.cumsum(np.random.randn(days) * 0.5) + 100
    return pd.DataFrame({
        "Open": close - 0.1,
        "High": close + 0.3,
        "Low": close - 0.3,
        "Close": close,
        "Volume": np.random.randint(1000, 10000, days),
    }, index=dates)


def _mock_multiindex_ohlc(days=50):
    """Simulate yfinance MultiIndex columns (Ticker level)."""
    df = _mock_ohlc_df(days=days)
    cols = pd.MultiIndex.from_product([df.columns, ["AAPL"]])
    df.columns = cols
    return df


def _mock_weekly_close_df(value=105.0):
    """Single-row weekly DataFrame used as weekly fallback."""
    dates = pd.date_range("2024-01-01", periods=1, freq="W")
    return pd.DataFrame({
        "Open": [value - 0.1],
        "High": [value + 0.3],
        "Low": [value - 0.3],
        "Close": [value],
        "Volume": [5000],
    }, index=dates)


def _query_result(df):
    """Convert OHLC DataFrame (DatetimeIndex) to query_kline output (Date column)."""
    return df.reset_index().rename(columns={"index": "Date"})


# ---------------------------------------------------------------------------
# _stock_name_lookup tests
# ---------------------------------------------------------------------------

class TestStockNameLookup:

    def test_empty_ticker(self):
        """空 ticker 返回空字符串."""
        from services.data_loader import _stock_name_lookup
        assert _stock_name_lookup("A股(沪深)", "") == ""
        assert _stock_name_lookup("港股 HK", "") == ""
        assert _stock_name_lookup("美股 US", "") == ""

    def test_whitespace_ticker(self):
        """纯空格 ticker 返回空字符串."""
        from services.data_loader import _stock_name_lookup
        assert _stock_name_lookup("A股(沪深)", "   ") == ""

    def test_successful_lookup_a_share(self):
        """A股正常查询返回股票名称."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = {"longName": "贵州茅台"}
            from services.data_loader import _stock_name_lookup
            result = _stock_name_lookup("A股(沪深)", "600519")
            assert result == "贵州茅台"
            # 6开头 → .SS
            mock_ticker.assert_called_once_with("600519.SS")

    def test_successful_lookup_shenzhen(self):
        """深证 A股正常查询."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = {"longName": "平安银行"}
            from services.data_loader import _stock_name_lookup
            result = _stock_name_lookup("A股(沪深)", "000001")
            assert result == "平安银行"
            # 0开头 → .SZ
            mock_ticker.assert_called_once_with("000001.SZ")

    def test_successful_lookup_hk(self):
        """港股正常查询."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = {"longName": "腾讯控股"}
            from services.data_loader import _stock_name_lookup
            result = _stock_name_lookup("港股 HK", "0700")
            assert result == "腾讯控股"
            mock_ticker.assert_called_once_with("0700.HK")

    def test_successful_lookup_us(self):
        """美股正常查询."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = {"longName": "Apple Inc."}
            from services.data_loader import _stock_name_lookup
            result = _stock_name_lookup("美股 US", "AAPL")
            assert result == "Apple Inc."
            mock_ticker.assert_called_once_with("AAPL")

    def test_missing_longName(self):
        """info 中没有 longName 时返回空字符串."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = {}
            from services.data_loader import _stock_name_lookup
            assert _stock_name_lookup("美股 US", "AAPL") == ""

    def test_lookup_exception(self):
        """网络异常时返回空字符串（不抛出)."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.side_effect = ConnectionError("timeout")
            from services.data_loader import _stock_name_lookup
            assert _stock_name_lookup("美股 US", "AAPL") == ""


# ---------------------------------------------------------------------------
# _fetch_stock tests
# ---------------------------------------------------------------------------

class TestFetchStock:

    def test_empty_code(self):
        """空 code 直接返回 Empty ticker."""
        from services.data_loader import _fetch_stock
        t, close, ohlc, full, err, dates = _fetch_stock("美股 US", "", "日线", 100)
        assert err == "Empty ticker code"
        assert t is None

        t, close, ohlc, full, err, dates = _fetch_stock("美股 US", "   ", "日线", 100)
        assert err == "Empty ticker code"

    @pytest.mark.parametrize("market,code,expected_full", [
        ("A股(沪深)", "600000", "600000.SS"),
        ("A股(沪深)", "000001", "000001.SZ"),
        ("港股 HK", "700", "0700.HK"),
        ("港股 HK", "0700", "0700.HK"),
        ("美股 US", "AAPL", "AAPL"),
    ])
    def test_ticker_construction(self, market, code, expected_full):
        """验证不同市场下 ticker 拼接逻辑."""
        mock_df = _mock_ohlc_df(days=10)
        with patch("services.data_loader.yf.download", return_value=mock_df), \
             patch("services.data_loader.upsert_kline"), \
             patch("services.data_loader.query_kline",
                   return_value=_query_result(mock_df)):
            from services.data_loader import _fetch_stock
            t, close, ohlc, full, err, dates = _fetch_stock(market, code, "日线", 10)
            assert err is None
            assert full == expected_full

    def test_yfinance_empty_data(self):
        """yfinance 返回空 DataFrame 时返回 无数据."""
        with patch("services.data_loader.yf.download",
                   return_value=pd.DataFrame()):
            from services.data_loader import _fetch_stock
            _, _, _, full, err, _ = _fetch_stock("美股 US", "AAPL", "日线", 10)
            assert err and "无数据" in err

    def test_multiindex_columns_flattened(self):
        """MultiIndex columns 被正确 flatten."""
        mock_df = _mock_multiindex_ohlc(days=10)
        with patch("services.data_loader.yf.download",
                   return_value=mock_df), \
             patch("services.data_loader.upsert_kline"), \
             patch("services.data_loader.query_kline",
                   return_value=_query_result(mock_df)):
            from services.data_loader import _fetch_stock
            _, _, ohlc, _, err, _ = _fetch_stock("美股 US", "AAPL", "日线", 10)
            assert err is None
            assert ohlc is not None
            assert "Open" in ohlc.columns

    def test_weekly_close_fallback_applied(self):
        """日线最后 Close 为 nan 时用周线回填."""
        df = _mock_ohlc_df(days=10)
        df.iloc[-1, df.columns.get_loc("Close")] = np.nan
        weekly_df = _mock_weekly_close_df(value=105.0)

        with patch("services.data_loader.yf.download",
                   side_effect=[df, weekly_df]), \
             patch("services.data_loader.upsert_kline"), \
             patch("services.data_loader.query_kline",
                   return_value=_query_result(df.dropna(subset=["Close"]))):
            from services.data_loader import _fetch_stock
            _, close, _, _, err, _ = _fetch_stock("美股 US", "AAPL", "日线", 10)
            assert err is None

    def test_weekly_close_fallback_multiindex(self):
        """周线回填且周线也是 MultiIndex 时正确 flatten."""
        df = _mock_ohlc_df(days=10)
        df.iloc[-1, df.columns.get_loc("Close")] = np.nan
        weekly_df = _mock_multiindex_ohlc(days=3)

        with patch("services.data_loader.yf.download",
                   side_effect=[df, weekly_df]), \
             patch("services.data_loader.upsert_kline"), \
             patch("services.data_loader.query_kline",
                   return_value=_query_result(df.dropna(subset=["Close"]))):
            from services.data_loader import _fetch_stock
            _, close, _, _, err, _ = _fetch_stock("美股 US", "AAPL", "日线", 10)
            assert err is None

    def test_weekly_close_fallback_empty_weekly(self):
        """周线回填时如果周线也为空，不报错."""
        df = _mock_ohlc_df(days=10)
        df.iloc[-1, df.columns.get_loc("Close")] = np.nan
        empty_weekly = pd.DataFrame()

        with patch("services.data_loader.yf.download",
                   side_effect=[df, empty_weekly]), \
             patch("services.data_loader.upsert_kline"), \
             patch("services.data_loader.query_kline",
                   return_value=_query_result(df.dropna(subset=["Close"]))):
            from services.data_loader import _fetch_stock
            _, close, _, _, err, _ = _fetch_stock("美股 US", "AAPL", "日线", 10)
            assert err is None

    def test_weekly_close_fallback_exception(self):
        """周线回填抛出异常时不阻断流程."""
        df = _mock_ohlc_df(days=10)
        df.iloc[-1, df.columns.get_loc("Close")] = np.nan

        def yf_side_effect(ticker, **kw):
            if kw.get("interval") == "1wk":
                raise ValueError("API error")
            return df

        with patch("services.data_loader.yf.download",
                   side_effect=yf_side_effect), \
             patch("services.data_loader.upsert_kline"), \
             patch("services.data_loader.query_kline",
                   return_value=_query_result(df.dropna(subset=["Close"]))):
            from services.data_loader import _fetch_stock
            _, close, _, _, err, _ = _fetch_stock("美股 US", "AAPL", "日线", 10)
            assert err is None

    @pytest.mark.parametrize("tf,expected_interval", [
        ("1分钟", "1m"), ("5分钟", "5m"), ("15分钟", "15m"),
        ("60分钟", "1h"), ("日线", "1d"), ("周线", "1wk"),
        ("月线", "1mo"), ("季线", "3mo"),
    ])
    def test_all_timeframes(self, tf, expected_interval):
        """验证所有周期对应的 interval 参数传递正确."""
        mock_df = _mock_ohlc_df(days=10)
        with patch("services.data_loader.yf.download",
                   return_value=mock_df) as mock_dl, \
             patch("services.data_loader.upsert_kline"), \
             patch("services.data_loader.query_kline",
                   return_value=_query_result(mock_df)):
            from services.data_loader import _fetch_stock
            _, _, _, _, err, _ = _fetch_stock("美股 US", "AAPL", tf, 10)
            assert err is None
            _, call_kw = mock_dl.call_args
            assert call_kw["interval"] == expected_interval

    @pytest.mark.parametrize("tf,n_pts,expected_period", [
        # 日线的各个 period 分支 (n_pts*2)
        ("日线", 10, "1mo"),    # wanted=20  <=30
        ("日线", 16, "3mo"),    # wanted=32  <=90
        ("日线", 46, "6mo"),    # wanted=92  <=180
        ("日线", 100, "1y"),    # wanted=200 <=365
        ("日线", 190, "2y"),    # wanted=380 <=730
        ("日线", 400, "5y"),    # wanted=800 <=1825
        ("日线", 1000, "10y"),  # wanted=2000 <=3650
        ("日线", 2000, "max"),  # wanted=4000 >3650
        # 周线的各个 period 分支 (n_pts*5)
        ("周线", 10, "1y"),     # wanted=50  <=52
        ("周线", 11, "2y"),     # wanted=55  <=104
        ("周线", 30, "5y"),     # wanted=150 <=260
        ("周线", 60, "10y"),    # wanted=300 <=520
        ("周线", 110, "max"),   # wanted=550 >520
        # 月线的各个 period 分支 (n_pts*1.5)
        ("月线", 8, "1y"),      # wanted=12  <=12
        ("月线", 9, "2y"),      # wanted=13.5 <=24
        ("月线", 20, "5y"),     # wanted=30  <=60
        ("月线", 50, "10y"),    # wanted=75  <=120
        ("月线", 100, "max"),   # wanted=150 >120
    ])
    def test_period_calculation(self, tf, n_pts, expected_period):
        """验证周期的 period 自动计算逻辑覆盖所有分支."""
        mock_df = _mock_ohlc_df(days=max(n_pts, 10))
        with patch("services.data_loader.yf.download",
                   return_value=mock_df) as mock_dl, \
             patch("services.data_loader.upsert_kline"), \
             patch("services.data_loader.query_kline",
                   return_value=_query_result(mock_df)):
            from services.data_loader import _fetch_stock
            _, _, _, _, err, _ = _fetch_stock("美股 US", "AAPL", tf, n_pts)
            assert err is None
            _, call_kw = mock_dl.call_args
            assert call_kw["period"] == expected_period, \
                f"{tf} n_pts={n_pts}: expected {expected_period}, got {call_kw['period']}"

    def test_db_upsert_failure(self):
        """upsert_kline 抛出异常不阻碍流程，继续查询."""
        mock_df = _mock_ohlc_df(days=10)
        with patch("services.data_loader.yf.download",
                   return_value=mock_df), \
             patch("services.data_loader.upsert_kline",
                   side_effect=RuntimeError("DB locked")), \
             patch("services.data_loader.query_kline",
                   return_value=_query_result(mock_df)):
            from services.data_loader import _fetch_stock
            _, _, _, _, err, _ = _fetch_stock("美股 US", "AAPL", "日线", 10)
            # upsert 失败但 query 成功则不应报错
            assert err is None

    def test_query_returns_empty_after_upsert(self):
        """upsert 成功但 query_kline 返回空 = 写入成功但查询失败."""
        mock_df = _mock_ohlc_df(days=10)
        with patch("services.data_loader.yf.download",
                   return_value=mock_df), \
             patch("services.data_loader.upsert_kline"), \
             patch("services.data_loader.query_kline",
                   return_value=pd.DataFrame()):
            from services.data_loader import _fetch_stock
            _, _, _, _, err, _ = _fetch_stock("美股 US", "AAPL", "日线", 10)
            assert err == "写入成功但查询失败"

    def test_force_period_argument(self):
        """force_period 参数覆盖自动 period 计算."""
        mock_df = _mock_ohlc_df(days=10)
        with patch("services.data_loader.yf.download",
                   return_value=mock_df) as mock_dl, \
             patch("services.data_loader.upsert_kline"), \
             patch("services.data_loader.query_kline",
                   return_value=_query_result(mock_df)):
            from services.data_loader import _fetch_stock
            _, _, _, _, err, _ = _fetch_stock("美股 US", "AAPL", "日线", 10,
                                               force_period="1y")
            assert err is None
            _, call_kw = mock_dl.call_args
            assert call_kw["period"] == "1y"

    def test_yfinance_exception(self):
        """yfinance 本身抛出异常时冒泡."""
        with patch("services.data_loader.yf.download",
                   side_effect=ConnectionError("network error")):
            from services.data_loader import _fetch_stock
            with pytest.raises(ConnectionError):
                _fetch_stock("美股 US", "AAPL", "日线", 10)

    def test_hk_ticker(self):
        """港股 ticker 拼接."""
        mock_df = _mock_ohlc_df(days=5)
        with patch("services.data_loader.yf.download",
                   return_value=mock_df), \
             patch("services.data_loader.upsert_kline"), \
             patch("services.data_loader.query_kline",
                   return_value=_query_result(mock_df)):
            from services.data_loader import _fetch_stock
            _, _, _, full, err, _ = _fetch_stock("港股 HK", "700", "60分钟", 100)
            assert err is None
            assert full == "0700.HK"

    def test_non_daily_interval_no_weekly_fallback(self):
        """非日线周期不走周线回填逻辑."""
        mock_df = _mock_ohlc_df(days=100)
        with patch("services.data_loader.yf.download",
                   return_value=mock_df) as mock_dl, \
             patch("services.data_loader.upsert_kline"), \
             patch("services.data_loader.query_kline",
                   return_value=_query_result(mock_df)):
            from services.data_loader import _fetch_stock
            _, _, _, _, err, _ = _fetch_stock("美股 US", "AAPL", "周线", 10)
            assert err is None
            # 确保 yf.download 只被调用一次（没有第二次周线查询）
            assert mock_dl.call_count == 1


# ---------------------------------------------------------------------------
# _sync_to_display tests
# ---------------------------------------------------------------------------

class TestSyncToDisplay:

    def test_normal_sync(self, tmp_path):
        """正常写入 display parquet."""
        df = _mock_ohlc_df(days=20)
        mock_df = _query_result(df)
        with patch("services.data_loader.query_kline",
                   return_value=mock_df), \
             patch("services.data_loader.Path") as mock_path_cls:
            fake_path = tmp_path / "filter_app" / "services" / "data_loader.py"
            mock_path_cls.return_value = fake_path

            from services.data_loader import _sync_to_display
            ok, count = _sync_to_display("AAPL", "日线", 0, 20)
            assert ok is True
            assert count == 20

            # Verify parquet file was created
            parquet_path = tmp_path / "data" / "display" / "日线.parquet"
            assert parquet_path.exists()
            loaded = pd.read_parquet(parquet_path)
            assert len(loaded) == 20

    def test_fewer_than_5_rows(self):
        """不足5条数据返回 (False, n)."""
        df = _mock_ohlc_df(days=3)
        mock_df = _query_result(df)
        with patch("services.data_loader.query_kline",
                   return_value=mock_df):
            from services.data_loader import _sync_to_display
            ok, count = _sync_to_display("AAPL", "日线", 0, 3)
            assert ok is False
            assert count == 3


# ---------------------------------------------------------------------------
# _fetch_all_timeframes tests
# ---------------------------------------------------------------------------

class TestFetchAllTimeframes:

    def test_all_timeframes_success(self):
        """所有8个周期全部成功."""
        mock_df = _mock_ohlc_df(days=50)
        with patch("services.data_loader._fetch_stock",
                   return_value=(
                       np.arange(50, dtype=float),
                       mock_df["Close"].values,
                       mock_df, "AAPL", None,
                       pd.to_datetime(mock_df.index),
                   )):
            from services.data_loader import _fetch_all_timeframes
            results = _fetch_all_timeframes("美股 US", "AAPL")
            assert len(results) == 8
            for tf, (ok, detail) in results.items():
                assert ok is True, f"{tf} failed: {detail}"
                assert isinstance(detail, int)

    def test_partial_failures(self):
        """部分周期失败."""
        call_count = [0]

        def mock_fetch(market, code, tf, n_pts, force_period=None):
            call_count[0] += 1
            if call_count[0] % 2 == 0:  # 偶数次调用失败
                return None, None, None, "AAPL", "API error", None
            mock_df = _mock_ohlc_df(days=50)
            return (np.arange(50, dtype=float), mock_df["Close"].values,
                    mock_df, "AAPL", None, pd.to_datetime(mock_df.index))

        with patch("services.data_loader._fetch_stock",
                   side_effect=mock_fetch):
            from services.data_loader import _fetch_all_timeframes
            results = _fetch_all_timeframes("美股 US", "AAPL")
            assert len(results) == 8
            assert call_count[0] == 8

    def test_exception_in_fetch_one(self):
        """单个周期 fetch 抛出异常被吞掉."""
        def mock_fetch(market, code, tf, n_pts, force_period=None):
            raise ValueError("unexpected error")

        with patch("services.data_loader._fetch_stock",
                   side_effect=mock_fetch):
            from services.data_loader import _fetch_all_timeframes
            results = _fetch_all_timeframes("美股 US", "AAPL")
            assert len(results) == 8
            for tf, (ok, detail) in results.items():
                assert ok is False
                assert "unexpected" in str(detail)


# ---------------------------------------------------------------------------
# Smoke tests: module-level import does not crash
# ---------------------------------------------------------------------------

class TestModule:
    """Smoke tests ensuring the module can be imported and key symbols exist."""

    def test_module_imports(self):
        """模块导入不报错."""
        from services import data_loader
        assert hasattr(data_loader, "_fetch_stock")
        assert hasattr(data_loader, "_fetch_all_timeframes")
        assert hasattr(data_loader, "_sync_to_display")
        assert hasattr(data_loader, "_stock_name_lookup")
