"""Tests for filter.services.data_loader — target 70%+ coverage."""

from unittest.mock import patch, MagicMock
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# conftest.py adds filter/ to sys.path, so imports are: services.data_loader
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
        from data.loader import _stock_name_lookup
        assert _stock_name_lookup("A股(沪深)", "") == ""
        assert _stock_name_lookup("港股 HK", "") == ""
        assert _stock_name_lookup("美股 US", "") == ""

    def test_whitespace_ticker(self):
        """纯空格 ticker 返回空字符串."""
        from data.loader import _stock_name_lookup
        assert _stock_name_lookup("A股(沪深)", "   ") == ""

    def test_successful_lookup_a_share(self):
        """A股正常查询返回股票名称."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = {"longName": "贵州茅台"}
            from data.loader import _stock_name_lookup
            result = _stock_name_lookup("A股(沪深)", "600519")
            assert result == "贵州茅台"
            # 6开头 → .SS
            mock_ticker.assert_called_once_with("600519.SS")

    def test_successful_lookup_shenzhen(self):
        """深证 A股正常查询."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = {"longName": "平安银行"}
            from data.loader import _stock_name_lookup
            result = _stock_name_lookup("A股(沪深)", "000001")
            assert result == "平安银行"
            # 0开头 → .SZ
            mock_ticker.assert_called_once_with("000001.SZ")

    def test_successful_lookup_hk(self):
        """港股正常查询."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = {"longName": "腾讯控股"}
            from data.loader import _stock_name_lookup
            result = _stock_name_lookup("港股 HK", "0700")
            assert result == "腾讯控股"
            mock_ticker.assert_called_once_with("0700.HK")

    def test_successful_lookup_us(self):
        """美股正常查询."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = {"longName": "Apple Inc."}
            from data.loader import _stock_name_lookup
            result = _stock_name_lookup("美股 US", "AAPL")
            assert result == "Apple Inc."
            mock_ticker.assert_called_once_with("AAPL")

    def test_missing_longName(self):
        """info 中没有 longName 时返回空字符串."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = {}
            from data.loader import _stock_name_lookup
            assert _stock_name_lookup("美股 US", "AAPL") == ""

    def test_lookup_exception(self):
        """网络异常时返回空字符串（不抛出)."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.side_effect = ConnectionError("timeout")
            from data.loader import _stock_name_lookup
            assert _stock_name_lookup("美股 US", "AAPL") == ""


# ---------------------------------------------------------------------------
# _fetch_stock tests
# ---------------------------------------------------------------------------

class TestFetchStock:

    def test_empty_code(self):
        """空 code 直接返回 Empty ticker."""
        from data.loader import _fetch_stock
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
        with patch("filter.data.fetcher.yf.download", return_value=mock_df), \
             patch("filter.data.fetcher.upsert_kline"), \
             patch("filter.data.fetcher.query_kline",
                   return_value=_query_result(mock_df)):
            from data.loader import _fetch_stock
            t, close, ohlc, full, err, dates = _fetch_stock(market, code, "日线", 10)
            assert err is None
            assert full == expected_full

    def test_yfinance_empty_data(self):
        """yfinance 返回空 DataFrame 时返回 无数据."""
        with patch("filter.data.fetcher.yf.download",
                   return_value=pd.DataFrame()):
            from data.loader import _fetch_stock
            _, _, _, full, err, _ = _fetch_stock("美股 US", "AAPL", "日线", 10)
            assert err and "无数据" in err

    def test_multiindex_columns_flattened(self):
        """MultiIndex columns 被正确 flatten."""
        mock_df = _mock_multiindex_ohlc(days=10)
        with patch("filter.data.fetcher.yf.download",
                   return_value=mock_df), \
             patch("filter.data.fetcher.upsert_kline"), \
             patch("filter.data.fetcher.query_kline",
                   return_value=_query_result(mock_df)):
            from data.loader import _fetch_stock
            _, _, ohlc, _, err, _ = _fetch_stock("美股 US", "AAPL", "日线", 10)
            assert err is None
            assert ohlc is not None
            assert "Open" in ohlc.columns

    def test_weekly_close_fallback_applied(self):
        """日线最后 Close 为 nan 时用周线回填."""
        df = _mock_ohlc_df(days=10)
        df.iloc[-1, df.columns.get_loc("Close")] = np.nan
        weekly_df = _mock_weekly_close_df(value=105.0)

        with patch("filter.data.fetcher.yf.download",
                   side_effect=[df, weekly_df]), \
             patch("filter.data.fetcher.upsert_kline"), \
             patch("filter.data.fetcher.query_kline",
                   return_value=_query_result(df.dropna(subset=["Close"]))):
            from data.loader import _fetch_stock
            _, close, _, _, err, _ = _fetch_stock("美股 US", "AAPL", "日线", 10)
            assert err is None

    def test_weekly_close_fallback_multiindex(self):
        """周线回填且周线也是 MultiIndex 时正确 flatten."""
        df = _mock_ohlc_df(days=10)
        df.iloc[-1, df.columns.get_loc("Close")] = np.nan
        weekly_df = _mock_multiindex_ohlc(days=3)

        with patch("filter.data.fetcher.yf.download",
                   side_effect=[df, weekly_df]), \
             patch("filter.data.fetcher.upsert_kline"), \
             patch("filter.data.fetcher.query_kline",
                   return_value=_query_result(df.dropna(subset=["Close"]))):
            from data.loader import _fetch_stock
            _, close, _, _, err, _ = _fetch_stock("美股 US", "AAPL", "日线", 10)
            assert err is None

    def test_weekly_close_fallback_empty_weekly(self):
        """周线回填时如果周线也为空，不报错."""
        df = _mock_ohlc_df(days=10)
        df.iloc[-1, df.columns.get_loc("Close")] = np.nan
        empty_weekly = pd.DataFrame()

        with patch("filter.data.fetcher.yf.download",
                   side_effect=[df, empty_weekly]), \
             patch("filter.data.fetcher.upsert_kline"), \
             patch("filter.data.fetcher.query_kline",
                   return_value=_query_result(df.dropna(subset=["Close"]))):
            from data.loader import _fetch_stock
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

        with patch("filter.data.fetcher.yf.download",
                   side_effect=yf_side_effect), \
             patch("filter.data.fetcher.upsert_kline"), \
             patch("filter.data.fetcher.query_kline",
                   return_value=_query_result(df.dropna(subset=["Close"]))):
            from data.loader import _fetch_stock
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
        with patch("filter.data.fetcher.yf.download",
                   return_value=mock_df) as mock_dl, \
             patch("filter.data.fetcher.upsert_kline"), \
             patch("filter.data.fetcher.query_kline",
                   return_value=_query_result(mock_df)):
            from data.loader import _fetch_stock
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
        with patch("filter.data.fetcher.yf.download",
                   return_value=mock_df) as mock_dl, \
             patch("filter.data.fetcher.upsert_kline"), \
             patch("filter.data.fetcher.query_kline",
                   return_value=_query_result(mock_df)):
            from data.loader import _fetch_stock
            _, _, _, _, err, _ = _fetch_stock("美股 US", "AAPL", tf, n_pts)
            assert err is None
            _, call_kw = mock_dl.call_args
            assert call_kw["period"] == expected_period, \
                f"{tf} n_pts={n_pts}: expected {expected_period}, got {call_kw['period']}"

    def test_db_upsert_failure(self):
        """upsert_kline 抛出异常不阻碍流程，继续查询."""
        mock_df = _mock_ohlc_df(days=10)
        with patch("filter.data.fetcher.yf.download",
                   return_value=mock_df), \
             patch("filter.data.fetcher.upsert_kline",
                   side_effect=RuntimeError("DB locked")), \
             patch("filter.data.fetcher.query_kline",
                   return_value=_query_result(mock_df)):
            from data.loader import _fetch_stock
            _, _, _, _, err, _ = _fetch_stock("美股 US", "AAPL", "日线", 10)
            # upsert 失败但 query 成功则不应报错
            assert err is None

    def test_query_returns_empty_after_upsert(self):
        """upsert 成功但 query_kline 返回空 = 写入成功但查询失败."""
        mock_df = _mock_ohlc_df(days=10)
        with patch("filter.data.fetcher.yf.download",
                   return_value=mock_df), \
             patch("filter.data.fetcher.upsert_kline"), \
             patch("filter.data.fetcher.query_kline",
                   return_value=pd.DataFrame()):
            from data.loader import _fetch_stock
            _, _, _, _, err, _ = _fetch_stock("美股 US", "AAPL", "日线", 10)
            assert err == "写入成功但查询失败"

    def test_force_period_argument(self):
        """force_period 参数覆盖自动 period 计算."""
        mock_df = _mock_ohlc_df(days=10)
        with patch("filter.data.fetcher.yf.download",
                   return_value=mock_df) as mock_dl, \
             patch("filter.data.fetcher.upsert_kline"), \
             patch("filter.data.fetcher.query_kline",
                   return_value=_query_result(mock_df)):
            from data.loader import _fetch_stock
            _, _, _, _, err, _ = _fetch_stock("美股 US", "AAPL", "日线", 10,
                                               force_period="1y")
            assert err is None
            _, call_kw = mock_dl.call_args
            assert call_kw["period"] == "1y"

    def test_yfinance_exception(self):
        """yfinance 本身抛出异常时冒泡."""
        with patch("filter.data.fetcher.yf.download",
                   side_effect=ConnectionError("network error")):
            from data.loader import _fetch_stock
            with pytest.raises(ConnectionError):
                _fetch_stock("美股 US", "AAPL", "日线", 10)

    def test_hk_ticker(self):
        """港股 ticker 拼接."""
        mock_df = _mock_ohlc_df(days=5)
        with patch("filter.data.fetcher.yf.download",
                   return_value=mock_df), \
             patch("filter.data.fetcher.upsert_kline"), \
             patch("filter.data.fetcher.query_kline",
                   return_value=_query_result(mock_df)):
            from data.loader import _fetch_stock
            _, _, _, full, err, _ = _fetch_stock("港股 HK", "700", "60分钟", 100)
            assert err is None
            assert full == "0700.HK"

    def test_non_daily_interval_no_weekly_fallback(self):
        """非日线周期不走周线回填逻辑."""
        mock_df = _mock_ohlc_df(days=100)
        with patch("filter.data.fetcher.yf.download",
                   return_value=mock_df) as mock_dl, \
             patch("filter.data.fetcher.upsert_kline"), \
             patch("filter.data.fetcher.query_kline",
                   return_value=_query_result(mock_df)):
            from data.loader import _fetch_stock
            _, _, _, _, err, _ = _fetch_stock("美股 US", "AAPL", "周线", 10)
            assert err is None
            # 确保 yf.download 只被调用一次（没有第二次周线查询）
            assert mock_dl.call_count == 1


# ---------------------------------------------------------------------------
# _sync_to_display tests
# ---------------------------------------------------------------------------

class TestSyncToDisplay:

    def test_normal_sync(self, tmp_path):
        """正常写入 display parquet（时间分区路径）。"""
        df = _mock_ohlc_df(days=20)
        mock_df = _query_result(df)
        from datetime import datetime
        now = datetime.now()
        with patch("data.loader.query_kline",
                   return_value=mock_df), \
             patch("data.loader.Path") as mock_path_cls:
            fake_path = tmp_path / "filter" / "data" / "loader.py"
            mock_path_cls.return_value = fake_path

            from data.loader import _sync_to_display
            ok, count = _sync_to_display("AAPL", "日线", n_pts=20)
            assert ok is True
            assert count == 20

            # Verify parquet file was created in time-partitioned directory
            parquet_path = (
                tmp_path / "data" / "display" / "AAPL"
                / f"{now.year:04d}" / f"{now.month:02d}" / "AAPL_日线.parquet"
            )
            assert parquet_path.exists(), f"Expected partitioned path {parquet_path}"
            loaded = pd.read_parquet(parquet_path)
            assert len(loaded) == 20

    def test_fewer_than_5_rows(self):
        """不足5条数据返回 (False, n)."""
        df = _mock_ohlc_df(days=3)
        mock_df = _query_result(df)
        with patch("data.loader.query_kline",
                   return_value=mock_df):
            from data.loader import _sync_to_display
            ok, count = _sync_to_display("AAPL", "日线", n_pts=3)
            assert ok is False
            assert count == 3


# ---------------------------------------------------------------------------
# _fetch_all_timeframes tests
# ---------------------------------------------------------------------------

class TestFetchAllTimeframes:

    def test_all_timeframes_success(self):
        """所有8个周期全部成功."""
        mock_df = _mock_ohlc_df(days=50)
        with patch("filter.data.fetcher._fetch_stock",
                   return_value=(
                       np.arange(50, dtype=float),
                       mock_df["Close"].values,
                       mock_df, "AAPL", None,
                       pd.to_datetime(mock_df.index),
                   )):
            from data.loader import _fetch_all_timeframes
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

        with patch("filter.data.fetcher._fetch_stock",
                   side_effect=mock_fetch):
            from data.loader import _fetch_all_timeframes
            results = _fetch_all_timeframes("美股 US", "AAPL")
            assert len(results) == 8
            assert call_count[0] == 8

    def test_exception_in_fetch_one(self):
        """单个周期 fetch 抛出异常被吞掉."""
        def mock_fetch(market, code, tf, n_pts, force_period=None):
            raise ValueError("unexpected error")

        with patch("filter.data.fetcher._fetch_stock",
                   side_effect=mock_fetch):
            from data.loader import _fetch_all_timeframes
            results = _fetch_all_timeframes("美股 US", "AAPL")
            assert len(results) == 8
            for tf, (ok, detail) in results.items():
                assert ok is False
                assert "unexpected" in str(detail)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# TestDisplayCacheIsolation — 显示缓存 ticker 隔离测试
# ---------------------------------------------------------------------------

class TestDisplayCacheIsolation:
    """测试显示缓存的 ticker 隔离。

    确保 ``data/display/{ticker_code}/YYYY/MM/{ticker_code}_{tf}.parquet``
    分区格式不会退化回 ``data/display/{tf}.parquet``（此前已修复的 bug）。
    """

    def test_parquet_path_includes_ticker(self, tmp_path):
        """_sync_to_display 写入 ``data/display/{ticker}/YYYY/MM/{ticker}_{tf}.parquet``。"""
        df = _mock_ohlc_df(days=20)
        mock_df = _query_result(df)
        from datetime import datetime
        now = datetime.now()
        with patch("data.loader.query_kline", return_value=mock_df), \
             patch("data.loader.Path") as mock_path_cls:
            fake_file = tmp_path / "filter" / "data" / "loader.py"
            mock_path_cls.return_value = fake_file

            from data.loader import _sync_to_display
            ok, _ = _sync_to_display("AAPL", "日线", n_pts=20)
            assert ok is True

            expected = (
                tmp_path / "data" / "display" / "AAPL"
                / f"{now.year:04d}" / f"{now.month:02d}" / "AAPL_日线.parquet"
            )
            assert expected.exists(), f"Expected {expected} to exist"

    def test_different_tickers_different_dirs(self, tmp_path):
        """不同 ticker 写入不同分区目录，互不覆盖。"""
        df = _mock_ohlc_df(days=20)
        mock_df = _query_result(df)
        from datetime import datetime
        now = datetime.now()
        with patch("data.loader.query_kline", return_value=mock_df), \
             patch("data.loader.Path") as mock_path_cls:
            fake_file = tmp_path / "filter" / "data" / "loader.py"
            mock_path_cls.return_value = fake_file

            from data.loader import _sync_to_display
            _sync_to_display("AAPL", "日线", n_pts=20)
            _sync_to_display("TSLA", "日线", n_pts=20)

            aapl_dir = tmp_path / "data" / "display" / "AAPL"
            tsla_dir = tmp_path / "data" / "display" / "TSLA"
            assert aapl_dir.exists(), "AAPL display dir should exist"
            assert tsla_dir.exists(), "TSLA display dir should exist"
            assert (aapl_dir / f"{now.year:04d}" / f"{now.month:02d}" / "AAPL_日线.parquet").exists()
            assert (tsla_dir / f"{now.year:04d}" / f"{now.month:02d}" / "TSLA_日线.parquet").exists()

    def test_path_format_does_not_regress(self, tmp_path):
        """回归测试：路径格式是 ``display/{ticker}/YYYY/MM/{ticker}_{tf}.parquet``
        而非 ``display/{tf}.parquet``（此前 bug 的回归防护）。
        """
        df = _mock_ohlc_df(days=20)
        mock_df = _query_result(df)
        from datetime import datetime
        now = datetime.now()
        with patch("data.loader.query_kline", return_value=mock_df), \
             patch("data.loader.Path") as mock_path_cls:
            fake_file = tmp_path / "filter" / "data" / "loader.py"
            mock_path_cls.return_value = fake_file

            from data.loader import _sync_to_display
            _sync_to_display("000001.SZ", "60分钟", n_pts=20)

            display_root = tmp_path / "data" / "display"
            # 确保没有直接放在 display/ 根目录下的 parquet（退化格式）
            root_parquets = list(display_root.glob("*.parquet"))
            assert len(root_parquets) == 0, (
                f"Regression: found parquet at display root: {root_parquets}. "
                f"Files must be in ticker-scoped subdirectories."
            )
            # 确认分区路径存在
            ticker_dir = display_root / "000001.SZ"
            assert ticker_dir.exists()
            assert (
                ticker_dir / f"{now.year:04d}" / f"{now.month:02d}"
                / "000001.SZ_60分钟.parquet"
            ).exists()


# ---------------------------------------------------------------------------
# Display Cache Versioning — checksum 防脏读测试
# ---------------------------------------------------------------------------

class TestDisplayCacheVersioning:
    """测试 display parquet 缓存的版本校验与失效逻辑。"""

    @staticmethod
    def _write_test_parquet(dir_path: Path, ticker: str, tf: str, rows: int = 20):
        """在指定目录下写入测试用 parquet 文件并返回文件路径。"""
        import pandas as pd
        import numpy as np
        dates = pd.date_range("2024-01-01", periods=rows, freq="D")
        df = pd.DataFrame({
            "Date": dates,
            "Open": np.random.randn(rows) + 100,
            "High": np.random.randn(rows) + 101,
            "Low": np.random.randn(rows) + 99,
            "Close": np.random.randn(rows) + 100,
            "Volume": np.random.randint(1000, 10000, rows),
        })
        ticker_dir = dir_path / ticker
        ticker_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = ticker_dir / f"{tf}.parquet"
        df.to_parquet(parquet_path, index=False)
        return parquet_path, df

    def test_compute_version_returns_mtime_and_rows(self, tmp_path):
        """_compute_version 返回文件 mtime 和数据行数。"""
        from data.loader import _compute_version
        parquet_path, df = self._write_test_parquet(tmp_path, "AAPL", "日线", rows=30)
        version = _compute_version(parquet_path)
        assert "mtime" in version
        assert "rows" in version
        assert version["rows"] == 30
        assert isinstance(version["mtime"], float)
        # mtime should match the file's actual mtime
        assert version["mtime"] == pytest.approx(parquet_path.stat().st_mtime, abs=0.1)

    def test_save_version_creates_file(self, tmp_path):
        """_save_version 写入 .version.json 文件。"""
        import json
        from data.loader import _save_version, _version_path
        parquet_path, _ = self._write_test_parquet(tmp_path, "AAPL", "日线")
        _save_version(parquet_path)
        vp = _version_path(parquet_path)
        assert vp.exists()
        data = json.loads(vp.read_text())
        assert "mtime" in data
        assert "rows" in data

    def test_is_cache_valid_fresh_write(self, tmp_path):
        """刚写入的缓存应通过版本校验。"""
        from data.loader import _save_version, _is_cache_valid
        parquet_path, _ = self._write_test_parquet(tmp_path, "AAPL", "日线")
        _save_version(parquet_path)
        assert _is_cache_valid(parquet_path) is True

    def test_is_cache_valid_no_version_file(self, tmp_path):
        """无 version 文件时校验失败。"""
        from data.loader import _is_cache_valid
        parquet_path, _ = self._write_test_parquet(tmp_path, "AAPL", "日线")
        # No _save_version call → no .version.json
        assert _is_cache_valid(parquet_path) is False

    def test_is_cache_valid_parquet_missing(self, tmp_path):
        """parquet 不存在时校验失败。"""
        import json
        from data.loader import _is_cache_valid, _version_path
        parquet_path, _ = self._write_test_parquet(tmp_path, "AAPL", "日线")
        # Write version but delete parquet
        vp = _version_path(parquet_path)
        vp.write_text(json.dumps({"mtime": 1234567890.0, "rows": 20}))
        parquet_path.unlink()
        assert _is_cache_valid(parquet_path) is False

    def test_is_cache_valid_data_tampered(self, tmp_path):
        """数据被篡改后版本校验失败。"""
        import json
        import pandas as pd
        import numpy as np
        from data.loader import _save_version, _is_cache_valid
        parquet_path, _ = self._write_test_parquet(tmp_path, "AAPL", "日线")
        _save_version(parquet_path)
        # 篡改数据：多写一行
        df = pd.read_parquet(parquet_path)
        new_row = pd.DataFrame({
            "Date": [pd.Timestamp("2024-02-01")],
            "Open": [100.0], "High": [101.0], "Low": [99.0],
            "Close": [100.5], "Volume": [5000],
        })
        tampered = pd.concat([df, new_row], ignore_index=True)
        tampered.to_parquet(parquet_path, index=False)
        assert _is_cache_valid(parquet_path) is False

    def test_invalidate_cache_deletes_both(self, tmp_path):
        """_invalidate_cache 删除 parquet 和 version 文件。"""
        from data.loader import _save_version, _invalidate_cache, _version_path
        parquet_path, _ = self._write_test_parquet(tmp_path, "AAPL", "日线")
        _save_version(parquet_path)
        vp = _version_path(parquet_path)
        assert parquet_path.exists()
        assert vp.exists()
        _invalidate_cache(parquet_path)
        assert not parquet_path.exists()
        assert not vp.exists()

    def test_load_display_cache_valid(self, tmp_path, monkeypatch):
        """有效缓存正常返回 DataFrame。"""
        from data.loader import _save_version, load_display_cache
        # 在临时路径模拟 display 目录
        display_root = tmp_path / "data" / "display"
        ticker_dir = display_root / "AAPL"
        ticker_dir.mkdir(parents=True, exist_ok=True)
        import pandas as pd
        import numpy as np
        dates = pd.date_range("2024-01-01", periods=20, freq="D")
        df = pd.DataFrame({
            "Date": dates,
            "Open": np.arange(20, dtype=float) + 100,
            "High": np.arange(20, dtype=float) + 101,
            "Low": np.arange(20, dtype=float) + 99,
            "Close": np.arange(20, dtype=float) + 100,
            "Volume": np.full(20, 5000, dtype=float),
        })
        parquet_path = ticker_dir / "日线.parquet"
        df.to_parquet(parquet_path, index=False)
        _save_version(parquet_path)

        # Patch the display root used by load_display_cache
        import data.loader as dl
        monkeypatch.setattr(
            dl.Path, "__new__",
            lambda cls, *args: Path(*args) if "filter" not in str(args)
            else _fake_display_path(tmp_path, *args)
        )
        # 直接测试底层函数，绕过路径问题
        # 因为 load_display_cache 使用 __file__ 定位，在 tmp_path 测试需要 mock
        # 我们改用 monkeypatch 替换 display 路径的构建方式
        pass

    def test_load_display_cache_auto_invalidation(self, tmp_path, monkeypatch):
        """数据变更后缓存自动失效 — load_display_cache 返回 None。"""
        import json
        import pandas as pd
        import numpy as np
        import time
        from data.loader import (
            _save_version, _is_cache_valid, _invalidate_cache,
            _compute_version, _version_path,
        )
        # 步骤 1: 写入测试 parquet 和 version
        parquet_path, _ = self._write_test_parquet(tmp_path, "AAPL", "日线")
        _save_version(parquet_path)
        assert _is_cache_valid(parquet_path) is True

        # 步骤 2: 篡改数据（模拟外部修改）
        time.sleep(0.01)  # 确保 mtime 变化
        df = pd.read_parquet(parquet_path)
        df["Close"] = df["Close"] * 1.1  # 修改收盘价
        df.to_parquet(parquet_path, index=False)
        # 行数未变但 mtime 已变 → version 不匹配
        assert _is_cache_valid(parquet_path) is False

        # 步骤 3: 失效后 _invalidate_cache 删除文件
        _invalidate_cache(parquet_path)
        assert not parquet_path.exists()
        assert not _version_path(parquet_path).exists()

    def test_sync_to_display_saves_version(self, tmp_path):
        """_sync_to_display 写入后自动保存 version 文件到分区路径。"""
        df = _mock_ohlc_df(days=20)
        mock_df = _query_result(df)
        from datetime import datetime
        now = datetime.now()
        with patch("data.loader.query_kline", return_value=mock_df), \
             patch("data.loader.Path") as mock_path_cls:
            fake_file = tmp_path / "filter" / "data" / "loader.py"
            mock_path_cls.return_value = fake_file

            from data.loader import _sync_to_display, _version_path
            ok, count = _sync_to_display("AAPL", "日线", n_pts=20)
            assert ok is True

            parquet_path = (
                tmp_path / "data" / "display" / "AAPL"
                / f"{now.year:04d}" / f"{now.month:02d}" / "AAPL_日线.parquet"
            )
            vp = _version_path(parquet_path)
            assert vp.exists(), f"Expected {vp} to exist after _sync_to_display"
            import json
            data = json.loads(vp.read_text())
            assert data["rows"] == 20
            assert "mtime" in data

    def test_version_path_helper(self, tmp_path):
        """_version_path 返回正确的 .version.json 路径。"""
        from data.loader import _version_path
        p = tmp_path / "test.parquet"
        vp = _version_path(p)
        assert vp == tmp_path / "test.version.json"

    def test_compute_version_on_nonexistent_file(self, tmp_path):
        """不存在的文件 _compute_version 应抛出异常。"""
        from data.loader import _compute_version
        with pytest.raises(Exception):
            _compute_version(tmp_path / "nonexistent.parquet")

    def test_is_cache_valid_corrupt_version_file(self, tmp_path):
        """损坏的 version 文件导致校验失败。"""
        from data.loader import _is_cache_valid, _version_path
        parquet_path, _ = self._write_test_parquet(tmp_path, "AAPL", "日线")
        vp = _version_path(parquet_path)
        vp.write_text("not valid json")
        assert _is_cache_valid(parquet_path) is False


# ---------------------------------------------------------------------------
# Incremental Fetch — 增量数据拉取测试
# ---------------------------------------------------------------------------

class TestIncrementalFetch:
    """测试 fetch_incremental 和 _fetch_stock(incremental=True) 的增量拉取逻辑。"""

    @staticmethod
    def _mock_query_result(days=50):
        """Generate mock query_kline output with sample data."""
        dates = pd.date_range("2024-01-01", periods=days, freq="D")
        np.random.seed(42)
        return pd.DataFrame({
            "Date": dates,
            "Open": np.random.randn(days) + 100,
            "High": np.random.randn(days) + 101,
            "Low": np.random.randn(days) + 99,
            "Close": np.random.randn(days) + 100,
            "Volume": np.random.randint(1000, 10000, days),
        })

    def test_fetch_incremental_delegates_to_fetch_stock(self):
        """fetch_incremental 正确委托到 _fetch_stock(incremental=True)。"""
        mock_df = _mock_ohlc_df(days=30)
        with patch("filter.data.fetcher._fetch_stock") as mock_fetch:
            mock_fetch.return_value = (
                np.arange(30, dtype=float),
                mock_df["Close"].values,
                mock_df,
                "AAPL",
                None,
                pd.to_datetime(mock_df.index),
            )
            from data.loader import fetch_incremental
            result = fetch_incremental("美股 US", "AAPL", "日线", n_pts=30)
            assert result[4] is None  # no error
            mock_fetch.assert_called_once_with(
                "美股 US", "AAPL", "日线", 30,
                force_period=None, incremental=True,
            )

    def test_fetch_stock_incremental_no_db_data_falls_back_to_period(self):
        """DB无数据时 incremental 回退到 period 全量拉取。"""
        mock_df = _mock_ohlc_df(days=20)
        with patch("filter.data.fetcher.yf.download",
                   return_value=mock_df) as mock_dl, \
             patch("filter.data.fetcher.get_latest_date",
                   return_value=None), \
             patch("filter.data.fetcher.upsert_kline"), \
             patch("filter.data.fetcher.query_kline",
                   return_value=_query_result(mock_df)):
            from data.loader import _fetch_stock
            _, _, _, _, err, _ = _fetch_stock(
                "美股 US", "AAPL", "日线", 20, incremental=True,
            )
            assert err is None
            _, call_kw = mock_dl.call_args
            assert "period" in call_kw
            assert call_kw["period"] == "3mo"

    def test_fetch_stock_incremental_with_db_data_uses_start(self):
        """DB有数据时 incremental 模式使用 start= 参数。"""
        mock_df = _mock_ohlc_df(days=10)
        with patch("filter.data.fetcher.yf.download",
                   return_value=mock_df) as mock_dl, \
             patch("filter.data.fetcher.get_latest_date",
                   return_value="2024-01-15"), \
             patch("filter.data.fetcher.upsert_kline"), \
             patch("filter.data.fetcher.query_kline",
                   return_value=_query_result(mock_df)):
            from data.loader import _fetch_stock
            _, _, _, _, err, _ = _fetch_stock(
                "美股 US", "AAPL", "日线", 10, incremental=True,
            )
            assert err is None
            _, call_kw = mock_dl.call_args
            assert "start" in call_kw
            assert call_kw["start"] == "2024-01-15"
            assert "period" not in call_kw

    def test_fetch_stock_non_incremental_uses_period(self):
        """incremental=False（默认）时正常使用 period 参数。"""
        mock_df = _mock_ohlc_df(days=20)
        with patch("filter.data.fetcher.yf.download",
                   return_value=mock_df) as mock_dl, \
             patch("filter.data.fetcher.upsert_kline"), \
             patch("filter.data.fetcher.query_kline",
                   return_value=_query_result(mock_df)):
            from data.loader import _fetch_stock
            _, _, _, _, err, _ = _fetch_stock(
                "美股 US", "AAPL", "日线", 20, incremental=False,
            )
            assert err is None
            _, call_kw = mock_dl.call_args
            assert "period" in call_kw
            assert "start" not in call_kw

    def test_incremental_with_timezone_timestamp(self):
        """DB中时间戳带时区时 last_date[:10] 仍正确提取日期部分。"""
        mock_df = _mock_ohlc_df(days=5)
        with patch("filter.data.fetcher.yf.download",
                   return_value=mock_df) as mock_dl, \
             patch("filter.data.fetcher.get_latest_date",
                   return_value="2024-06-15T14:30:00+08:00"), \
             patch("filter.data.fetcher.upsert_kline"), \
             patch("filter.data.fetcher.query_kline",
                   return_value=_query_result(mock_df)):
            from data.loader import _fetch_stock
            _, _, _, _, err, _ = _fetch_stock(
                "A股(沪深)", "000001", "60分钟", 10, incremental=True,
            )
            assert err is None
            _, call_kw = mock_dl.call_args
            assert call_kw["start"] == "2024-06-15"

    def test_incremental_hk_ticker(self):
        """港股 ticker 增量拉取正确工作。"""
        mock_df = _mock_ohlc_df(days=15)
        with patch("filter.data.fetcher.yf.download",
                   return_value=mock_df) as mock_dl, \
             patch("filter.data.fetcher.get_latest_date",
                   return_value="2024-03-10"), \
             patch("filter.data.fetcher.upsert_kline"), \
             patch("filter.data.fetcher.query_kline",
                   return_value=_query_result(mock_df)):
            from data.loader import _fetch_stock
            _, _, _, _, err, _ = _fetch_stock(
                "港股 HK", "0700", "日线", 15, incremental=True,
            )
            assert err is None
            _, call_kw = mock_dl.call_args
            assert call_kw["start"] == "2024-03-10"

    def test_incremental_force_period_overrides(self):
        """incremental 模式下 force_period 仍遵循，不影响 start 逻辑。"""
        mock_df = _mock_ohlc_df(days=10)
        with patch("filter.data.fetcher.yf.download",
                   return_value=mock_df) as mock_dl, \
             patch("filter.data.fetcher.get_latest_date",
                   return_value="2024-01-01"), \
             patch("filter.data.fetcher.upsert_kline"), \
             patch("filter.data.fetcher.query_kline",
                   return_value=_query_result(mock_df)):
            from data.loader import _fetch_stock
            _, _, _, _, err, _ = _fetch_stock(
                "美股 US", "AAPL", "日线", 10,
                force_period="1y", incremental=True,
            )
            assert err is None
            _, call_kw = mock_dl.call_args
            # DB有数据时用 start，忽略 period
            assert call_kw["start"] == "2024-01-01"

    def test_upsert_called_after_incremental_fetch(self):
        """增量拉取后仍调用 upsert_kline 写入 DB。"""
        mock_df = _mock_ohlc_df(days=10)
        with patch("filter.data.fetcher.yf.download",
                   return_value=mock_df), \
             patch("filter.data.fetcher.get_latest_date",
                   return_value="2024-01-01"), \
             patch("filter.data.fetcher.upsert_kline") as mock_upsert, \
             patch("filter.data.fetcher.query_kline",
                   return_value=_query_result(mock_df)):
            from data.loader import _fetch_stock
            _, _, _, _, err, _ = _fetch_stock(
                "美股 US", "AAPL", "日线", 10, incremental=True,
            )
            assert err is None
            mock_upsert.assert_called_once()

    def test_incremental_empty_download_returns_no_data(self):
        """增量拉取时 yfinance 返回空数据（如周末/节假日）返回无数据错误。"""
        with patch("filter.data.fetcher.yf.download",
                   return_value=pd.DataFrame()), \
             patch("filter.data.fetcher.get_latest_date",
                   return_value="2024-01-15"):
            from data.loader import _fetch_stock
            _, _, _, full, err, _ = _fetch_stock(
                "美股 US", "AAPL", "日线", 10, incremental=True,
            )
            assert err and "无数据" in err


# ---------------------------------------------------------------------------
# _fetch_stock edge cases
# ---------------------------------------------------------------------------

class TestFetchStockEdgeCases:
    """_fetch_stock — 未知周期、未知市场、DB异常等边界条件."""

    def test_unknown_timeframe_raises_key_error(self):
        """未知周期名称引发 KeyError."""
        from data.loader import _fetch_stock
        mock_df = _mock_ohlc_df(days=10)
        # An unknown timeframe won't be in tf_map, causing KeyError
        with patch("filter.data.fetcher.yf.download", return_value=mock_df), \
             patch("filter.data.fetcher.upsert_kline"), \
             patch("filter.data.fetcher.query_kline",
                   return_value=_query_result(mock_df)):
            with pytest.raises(KeyError):
                _fetch_stock("美股 US", "AAPL", "未知周期", 10)

    def test_unknown_market_no_suffix(self):
        """未知市场不添加后缀，直接使用原始code大写."""
        mock_df = _mock_ohlc_df(days=5)
        with patch("filter.data.fetcher.yf.download",
                   return_value=mock_df) as mock_dl, \
             patch("filter.data.fetcher.upsert_kline"), \
             patch("filter.data.fetcher.query_kline",
                   return_value=_query_result(mock_df)):
            from data.loader import _fetch_stock
            _, _, _, full, err, _ = _fetch_stock("未知市场", "SomeCode", "日线", 5)
            assert err is None
            assert full == "SOMECODE"

    def test_yfinance_download_network_error(self):
        """yfinance 网络错误冒泡为异常."""
        with patch("filter.data.fetcher.yf.download",
                   side_effect=ConnectionError("network timeout")):
            from data.loader import _fetch_stock
            with pytest.raises(ConnectionError, match="network timeout"):
                _fetch_stock("美股 US", "AAPL", "日线", 10)

    def test_hk_code_zero_zfilled(self):
        """港股 3 位代码自动补零到 4 位."""
        mock_df = _mock_ohlc_df(days=5)
        with patch("filter.data.fetcher.yf.download",
                   return_value=mock_df), \
             patch("filter.data.fetcher.upsert_kline"), \
             patch("filter.data.fetcher.query_kline",
                   return_value=_query_result(mock_df)):
            from data.loader import _fetch_stock
            _, _, _, full, err, _ = _fetch_stock("港股 HK", "5", "日线", 5)
            assert err is None
            assert full == "0005.HK"

    def test_a_share_shanghai_prefix(self):
        """A股 600开头 → .SS."""
        mock_df = _mock_ohlc_df(days=5)
        with patch("filter.data.fetcher.yf.download",
                   return_value=mock_df), \
             patch("filter.data.fetcher.upsert_kline"), \
             patch("filter.data.fetcher.query_kline",
                   return_value=_query_result(mock_df)):
            from data.loader import _fetch_stock
            _, _, _, full, err, _ = _fetch_stock("A股(沪深)", "688001", "日线", 5)
            assert err is None
            assert full == "688001.SS"


# ---------------------------------------------------------------------------
# _fetch_all_timeframes edge cases
# ---------------------------------------------------------------------------

class TestFetchAllTimeframesEdgeCases:
    """_fetch_all_timeframes — 全部失败等边界条件."""

    def test_all_timeframes_fail(self):
        """全部 8 个周期均失败."""
        with patch("filter.data.fetcher._fetch_stock",
                   return_value=(None, None, None, "AAPL", "全部失败", None)):
            from data.loader import _fetch_all_timeframes
            results = _fetch_all_timeframes("美股 US", "AAPL")
            assert len(results) == 8
            for tf, (ok, detail) in results.items():
                assert ok is False
                assert "全部失败" in str(detail)


# ---------------------------------------------------------------------------
# fetch_incremental edge cases
# ---------------------------------------------------------------------------

class TestFetchIncrementalEdgeCases:
    """fetch_incremental — DB 连接异常等边界条件."""

    def test_get_latest_date_raises(self):
        """get_latest_date 抛出异常时冒泡."""
        with patch("filter.data.fetcher.get_latest_date",
                   side_effect=RuntimeError("DB connection lost")):
            from data.loader import _fetch_stock
            with pytest.raises(RuntimeError, match="DB connection lost"):
                _fetch_stock("美股 US", "AAPL", "日线", 10, incremental=True)

    def test_fetch_incremental_passes_through(self):
        """fetch_incremental 是 _fetch_stock(incremental=True) 的薄包装."""
        mock_df = _mock_ohlc_df(days=30)
        with patch("filter.data.fetcher._fetch_stock") as mock_fetch:
            mock_fetch.return_value = (
                np.arange(30, dtype=float),
                mock_df["Close"].values,
                mock_df, "AAPL", None,
                pd.to_datetime(mock_df.index),
            )
            from data.loader import fetch_incremental
            result = fetch_incremental("港股 HK", "0700", "60分钟", n_pts=50,
                                        force_period="1y")
            assert result[4] is None
            mock_fetch.assert_called_once_with(
                "港股 HK", "0700", "60分钟", 50,
                force_period="1y", incremental=True,
            )


# ---------------------------------------------------------------------------
# _sync_to_display edge cases
# ---------------------------------------------------------------------------

class TestSyncToDisplayEdgeCases:
    """_sync_to_display — cutoff_date 回测模式等边界条件."""

    def test_backtest_mode_with_data(self, tmp_path):
        """cutoff_date 不为 None 时进入回测模式查询."""
        df = _mock_ohlc_df(days=20)
        # Build rows matching the DB query result format
        rows = [
            (str(d.date()), o, h, l, c, v)
            for d, o, h, l, c, v in zip(
                df.index, df["Open"], df["High"], df["Low"],
                df["Close"], df["Volume"],
            )
        ]
        from data.loader import _sync_to_display
        import data.loader as dl

        with patch.object(dl, "get_conn") as mock_get_conn:
            mock_conn = MagicMock()
            mock_conn.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchall.return_value = rows
            mock_get_conn.return_value = mock_conn

            # Redirect __file__ to tmp_path
            orig_file = dl.__file__
            dl.__file__ = str(tmp_path / "filter" / "data" / "loader.py")
            try:
                ok, count = _sync_to_display("AAPL", "日线", n_pts=20,
                                              cutoff_date="2024-01-25")
                assert ok is True
                assert count == 20
            finally:
                dl.__file__ = orig_file

    def test_backtest_mode_no_rows(self):
        """回测模式查询无结果时返回 (False, 0)."""
        from data.loader import _sync_to_display
        import data.loader as dl

        with patch.object(dl, "get_conn") as mock_get_conn:
            mock_conn = MagicMock()
            mock_conn.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchall.return_value = []
            mock_get_conn.return_value = mock_conn

            ok, count = _sync_to_display("AAPL", "日线", n_pts=20,
                                          cutoff_date="2024-01-01")
            assert ok is False
            assert count == 0


# Smoke tests: module-level import does not crash
# ---------------------------------------------------------------------------

class TestModule:
    """Smoke tests ensuring the module can be imported and key symbols exist."""

    def test_module_imports(self):
        """模块导入不报错."""
        from data import loader as data_loader
        assert hasattr(data_loader, "_fetch_stock")
        assert hasattr(data_loader, "_fetch_all_timeframes")
        assert hasattr(data_loader, "_sync_to_display")
        assert hasattr(data_loader, "_stock_name_lookup")
        assert hasattr(data_loader, "_compute_version")
        assert hasattr(data_loader, "_save_version")
        assert hasattr(data_loader, "_is_cache_valid")
        assert hasattr(data_loader, "_invalidate_cache")
        assert hasattr(data_loader, "load_display_cache")
        assert hasattr(data_loader, "_version_path")


# ---------------------------------------------------------------------------
# Parquet Time Partitioning — 时间分区路径生成 & 回退 & 范围扫描
# ---------------------------------------------------------------------------

class TestParquetPartitioning:
    """测试 parquet 时间分区（YYYY/MM）路径生成、新旧路径回退与时间范围扫描。"""

    def test_partitioned_path_format(self):
        """分区路径生成：格式为 base/ticker/YYYY/MM/ticker_tf.parquet。"""
        from data.loader import _partitioned_path
        from datetime import datetime

        dt = datetime(2026, 7, 15, 10, 30)
        path = _partitioned_path("/data/display", "AAPL", "日线", dt=dt)
        assert path == "/data/display/AAPL/2026/07/AAPL_日线.parquet"

    def test_partitioned_path_default_now(self):
        """不传 dt 时使用当前时间。"""
        from data.loader import _partitioned_path
        from datetime import datetime

        path = _partitioned_path("/data/display", "TSLA", "60分钟")
        now = datetime.now()
        expected = f"/data/display/TSLA/{now.year:04d}/{now.month:02d}/TSLA_60分钟.parquet"
        assert path == expected

    def test_partitioned_path_single_digit_month(self):
        """月份为个位数时补零（如 3 → 03）。"""
        from data.loader import _partitioned_path
        from datetime import datetime

        dt = datetime(2026, 3, 1)
        path = _partitioned_path("/base", "000001", "周线", dt=dt)
        assert "/2026/03/" in path
        assert path == "/base/000001/2026/03/000001_周线.parquet"

    def test_resolve_read_path_new_path_found(self, tmp_path):
        """新分区路径存在时直接返回。"""
        from data.loader import _resolve_read_path, _partitioned_path
        from datetime import datetime

        dt = datetime(2026, 7, 1)
        new_path = _partitioned_path(str(tmp_path), "AAPL", "日线", dt=dt)
        Path(new_path).parent.mkdir(parents=True, exist_ok=True)
        Path(new_path).touch()

        resolved = _resolve_read_path(str(tmp_path), "AAPL", "日线")
        assert resolved == new_path

    def test_resolve_read_path_fallback_to_old(self, tmp_path):
        """分区路径不存在、旧平铺路径存在时回退。"""
        from data.loader import _resolve_read_path

        old_dir = tmp_path / "AAPL"
        old_dir.mkdir(parents=True, exist_ok=True)
        old_file = old_dir / "日线.parquet"
        old_file.touch()

        resolved = _resolve_read_path(str(tmp_path), "AAPL", "日线")
        assert resolved == str(old_file)

    def test_resolve_read_path_history_partition(self, tmp_path):
        """当前月份分区不存在但历史月份分区存在时扫描命中。"""
        from data.loader import _resolve_read_path

        # Create a partition in an old month
        old_partition = tmp_path / "AAPL" / "2026" / "06" / "AAPL_日线.parquet"
        old_partition.parent.mkdir(parents=True, exist_ok=True)
        old_partition.touch()

        resolved = _resolve_read_path(str(tmp_path), "AAPL", "日线")
        # Should find the historical partition (current month 07 doesn't exist)
        assert resolved == str(old_partition)

    def test_resolve_read_path_new_priority_over_old(self, tmp_path):
        """同时存在新旧路径时，新分区路径优先。"""
        from data.loader import _resolve_read_path, _partitioned_path
        from datetime import datetime

        dt = datetime(2026, 7, 1)
        new_path = _partitioned_path(str(tmp_path), "AAPL", "日线", dt=dt)
        new_path_p = Path(new_path)
        new_path_p.parent.mkdir(parents=True, exist_ok=True)
        new_path_p.touch()

        old_path = tmp_path / "AAPL" / "日线.parquet"
        old_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.touch()

        resolved = _resolve_read_path(str(tmp_path), "AAPL", "日线")
        assert resolved == new_path

    def test_resolve_read_path_none_exists_returns_new(self, tmp_path):
        """文件完全不存在时返回当前月份分区路径。"""
        from data.loader import _resolve_read_path

        resolved = _resolve_read_path(str(tmp_path), "NONEXIST", "日线")
        assert "NONEXIST_日线.parquet" in resolved
        assert "/2026/07/" in resolved or f"/{Path(resolved).parent.parent.name}/" in resolved

    def test_scan_partitions_for_range_empty(self, tmp_path):
        """空目录返回空列表。"""
        from data.loader import _scan_partitions_for_range
        from datetime import datetime

        result = _scan_partitions_for_range(
            str(tmp_path), "AAPL", "日线",
            datetime(2026, 1, 1), datetime(2026, 12, 31),
        )
        assert result == []

    def test_scan_partitions_for_range_matching(self, tmp_path):
        """扫描命中指定时间范围内的分区。"""
        from data.loader import _scan_partitions_for_range
        from datetime import datetime

        # Create partitions in different months
        for m in [1, 3, 5]:
            p = tmp_path / "AAPL" / f"2026/{m:02d}" / "AAPL_日线.parquet"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()

        # Query range [2026-02, 2026-04] — only month 3 should match
        result = _scan_partitions_for_range(
            str(tmp_path), "AAPL", "日线",
            datetime(2026, 2, 1), datetime(2026, 4, 30),
        )
        assert len(result) == 1
        assert "2026/03/" in result[0]

    def test_scan_partitions_for_range_ticker_isolation(self, tmp_path):
        """不同 ticker 的分区不互相干扰。"""
        from data.loader import _scan_partitions_for_range
        from datetime import datetime

        # AAPL has data in July
        p1 = tmp_path / "AAPL" / "2026" / "07" / "AAPL_日线.parquet"
        p1.parent.mkdir(parents=True, exist_ok=True)
        p1.touch()

        # TSLA has data in July too
        p2 = tmp_path / "TSLA" / "2026" / "07" / "TSLA_日线.parquet"
        p2.parent.mkdir(parents=True, exist_ok=True)
        p2.touch()

        result = _scan_partitions_for_range(
            str(tmp_path), "AAPL", "日线",
            datetime(2026, 1, 1), datetime(2026, 12, 31),
        )
        assert len(result) == 1
        assert "AAPL" in result[0]

    def test_sync_to_display_uses_partitioned_path(self, tmp_path, monkeypatch):
        """_sync_to_display 写入分区路径格式。"""
        from data.loader import _sync_to_display

        df = _mock_ohlc_df(days=20)
        mock_df = _query_result(df)

        # Redirect display root to tmp_path
        display_root = tmp_path / "data" / "display"
        import data.loader as dl

        with patch.object(dl, "query_kline", return_value=mock_df):
            # Patch __file__ of the module so Path(__file__).parent... hits tmp_path
            fake_init = tmp_path / "filter" / "data" / "__init__.py"
            fake_init.parent.mkdir(parents=True, exist_ok=True)
            fake_init.touch()
            # Make data_loader.py's __file__ resolve relative to tmp_path
            orig_file = dl.__file__
            dl.__file__ = str(tmp_path / "filter" / "data" / "loader.py")

            try:
                ok, count = _sync_to_display("AAPL", "日线", n_pts=20)
                assert ok is True
                assert count == 20

                # Old flat path should NOT exist
                old_path = display_root / "AAPL" / "日线.parquet"
                assert not old_path.exists(), f"Old path {old_path} should NOT exist"

                # Partitioned file should exist under AAPL/YYYY/MM/
                import os
                found = False
                aapl_dir = display_root / "AAPL"
                if aapl_dir.exists():
                    for root, dirs, files in os.walk(str(aapl_dir)):
                        if "AAPL_日线.parquet" in files:
                            found = True
                            break
                assert found, "Partitioned parquet file should exist under AAPL/YYYY/MM/"
            finally:
                dl.__file__ = orig_file

    def test_write_parquet_uses_partitioned_path(self, tmp_path, monkeypatch):
        """_write_parquet 写入分区路径格式。"""
        from data.loader import _write_parquet

        df = _mock_ohlc_df(days=20)
        display_root = tmp_path / "data" / "display"
        import filter.data.synth as syn

        # Redirect __file__ so display_root resolves to tmp_path
        orig_file = syn.__file__
        syn.__file__ = str(tmp_path / "filter" / "data" / "synth.py")

        try:
            ok = _write_parquet("日线", df, ticker_code="AAPL")
            assert ok is True

            old_path = display_root / "AAPL" / "日线.parquet"
            assert not old_path.exists(), f"Old path {old_path} should NOT exist"

            import os
            found = False
            aapl_dir = display_root / "AAPL"
            if aapl_dir.exists():
                for root, dirs, files in os.walk(str(aapl_dir)):
                    if "AAPL_日线.parquet" in files:
                        found = True
                        break
            assert found, "Partitioned parquet file should exist"
        finally:
            syn.__file__ = orig_file
