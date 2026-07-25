"""测试 view_backtest.py 可视化功能"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.view_backtest import (
    df_to_columns,
    load_parquet,
    serialize_value,
    extract_ticker_name,
)


class TestDateParsing:
    """日期解析兼容多种格式"""

    def test_serialize_timestamp_to_iso(self):
        """Timestamp 应转为 ISO 格式字符串"""
        ts = pd.Timestamp("2026-04-01 10:30:00")
        result = serialize_value(ts)
        assert isinstance(result, str)
        assert "2026" in result
        assert "04" in result

    def test_serialize_none_to_null(self):
        """None 应转为 None (JSON null)"""
        assert serialize_value(None) is None

    def test_serialize_nan_to_null(self):
        """NaN 应转为 None"""
        import math
        assert serialize_value(float('nan')) is None

    def test_serialize_inf_to_null(self):
        """Infinity 应转为 None"""
        assert serialize_value(float('inf')) is None
        assert serialize_value(float('-inf')) is None

    def test_serialize_bool(self):
        """Bool 应保留类型"""
        assert serialize_value(True) is True
        assert serialize_value(False) is False


class TestHeatmapDataNoNanGaps:
    """热力图数据应跳过 NaN，保持连续性"""

    def test_df_with_null_positions(self):
        """含有 None 的 position 列应转为 null (不是空白字符串)"""
        df = pd.DataFrame({
            "bar_index": [0, 1, 2],
            "v0_long_pos": [True, None, False],
            "v0_short_pos": [False, None, True],
        })
        columns, stats = df_to_columns(df)
        assert columns["v0_long_pos"] == [True, None, False]
        assert columns["v0_short_pos"] == [False, None, True]

    def test_df_with_nan_positions(self):
        """NaN float 应转为 null"""
        import numpy as np
        df = pd.DataFrame({
            "bar_index": [0, 1, 2],
            "v0_long_pos": [1.0, np.nan, 0.0],
        })
        columns, stats = df_to_columns(df)
        assert columns["v0_long_pos"][0] == 1.0
        assert columns["v0_long_pos"][1] is None
        assert columns["v0_long_pos"][2] == 0.0


class TestEmptyPositionHandling:
    """空持仓数据处理"""

    def test_all_null_positions(self):
        """全为 None 的持仓列不应导致异常"""
        df = pd.DataFrame({
            "bar_index": [0, 1, 2],
            "v0_long_pos": [None, None, None],
        })
        columns, stats = df_to_columns(df)
        assert all(v is None for v in columns["v0_long_pos"])

    def test_empty_dataframe(self):
        """空 DataFrame 应返回空 columns 和 stats"""
        df = pd.DataFrame()
        columns, stats = df_to_columns(df)
        assert stats["n_rows"] == 0
        assert stats["n_cols"] == 0


class TestTickerExtraction:
    """Ticker 名称提取"""

    def test_parquet_file_path(self):
        name = extract_ticker_name("AAPL_20240101/backtest_result.parquet")
        assert name == "AAPL"

    def test_directory_path(self):
        name = extract_ticker_name("MSFT_20240101/")
        assert name == "MSFT"

    def test_chinese_ticker(self):
        name = extract_ticker_name("3690_20260725/backtest_result.parquet")
        assert name == "3690"


class TestDfToColumns:
    """DataFrame 转 column-major JSON"""

    def test_basic_conversion(self):
        df = pd.DataFrame({
            "bar_index": [0, 1, 2],
            "close": [100.5, 101.2, 99.8],
        })
        columns, stats = df_to_columns(df)
        assert stats["n_rows"] == 3
        assert stats["n_cols"] == 2
        assert stats["column_names"] == ["bar_index", "close"]
        assert columns["close"] == [100.5, 101.2, 99.8]

    def test_timestamp_column_converted_to_string(self):
        df = pd.DataFrame({
            "bar_timestamp": pd.to_datetime(["2026-01-01", "2026-01-02"]),
        })
        columns, stats = df_to_columns(df)
        assert isinstance(columns["bar_timestamp"][0], str)
        assert "2026" in columns["bar_timestamp"][0]


class TestParquetRoundtrip:
    """Parquet 读写 roundtrip"""

    def test_load_and_serialize(self):
        df = pd.DataFrame({
            "bar_index": range(10),
            "bar_timestamp": pd.date_range("2026-01-01", periods=10, freq="D"),
            "close": [100.0 + i for i in range(10)],
            "v0_sig": [0, 1, 1, -1, -1, 0, 1, -1, 0, 0],
            "v0_long_pos": [False, True, True, False, False, False, True, False, False, False],
            "v0_short_pos": [False, False, False, True, True, False, False, True, False, False],
        })
        fd, path = tempfile.mkstemp(suffix=".parquet")
        os.close(fd)
        try:
            df.to_parquet(path)
            columns, stats = load_parquet(path)
            assert stats["n_rows"] == 10
            assert len(columns["v0_sig"]) == 10
        finally:
            os.unlink(path)


class TestPanel5PositionHeatmap:
    """Panel 5 (持仓状态热力图) 依赖 bar_index 和 position 列"""

    def test_bar_index_in_serialized_columns(self):
        """bar_index 必须在序列化数据中存在（buildHeatmap 依赖 barIdx）"""
        df = pd.DataFrame({
            "bar_index": range(5),
            "bar_timestamp": pd.date_range("2026-01-01", periods=5, freq="D"),
            "v0_long_pos": [False, True, True, False, False],
            "v0_short_pos": [False, False, False, True, True],
        })
        columns, stats = df_to_columns(df)
        assert "bar_index" in columns
        assert len(columns["bar_index"]) == 5
        assert all(isinstance(v, (int, float)) for v in columns["bar_index"])

    def test_position_columns_non_null_for_heatmap(self):
        """Position 列应存在且无全空，确保热力图有数据可渲染"""
        df = pd.DataFrame({
            "bar_index": range(3),
            "v0_long_pos": [True, False, True],
            "v0_short_pos": [False, False, False],
            "v1_long_pos": [False, True, False],
            "v1_short_pos": [True, False, False],
        })
        columns, stats = df_to_columns(df)
        for v in range(2):
            for pos in ["long_pos", "short_pos"]:
                col = f"v{v}_{pos}"
                assert col in columns, f"Missing {col}"
                assert len(columns[col]) == 3
                # At least one non-null value for heatmap rendering
                non_null = [x for x in columns[col] if x is not None]
                assert len(non_null) > 0, f"{col} has all None values"


class TestPanel6PeriodDashboard:
    """Panel 6 (周期视图 D1-D4) 依赖 bar_index + 各视图信号/过滤列"""

    def test_period_dashboard_columns_present(self):
        """buildPeriodDashboard 所需的列应为所有 4 个视图提供"""
        df = pd.DataFrame({"bar_index": range(5)})
        for v in range(4):
            df[f"v{v}_filtered"] = [100.0 + i + v * 0.1 for i in range(5)]
            df[f"v{v}_sig"] = [0, 1, -1, 0, 1]
            df[f"v{v}_eps"] = [0.5] * 5
        df["close"] = [100.0 + i for i in range(5)]
        columns, stats = df_to_columns(df)
        for v in range(4):
            assert f"v{v}_filtered" in columns
            assert f"v{v}_sig" in columns
            assert f"v{v}_eps" in columns
            assert len(columns[f"v{v}_filtered"]) == 5

    def test_bar_index_present_for_x_axis(self):
        """buildPeriodDashboard 使用 barIdx 作为 x 轴，不能缺失"""
        df = pd.DataFrame({
            "bar_index": range(10),
            "close": [100.0] * 10,
            "v0_filtered": [100.0] * 10,
            "v0_sig": [0] * 10,
            "v0_eps": [0.5] * 10,
        })
        columns, stats = df_to_columns(df)
        assert "bar_index" in columns
        assert len(columns["bar_index"]) == 10


class TestPanel8DataTable:
    """Panel 8 (完整数据表) 依赖列数据存在"""

    def test_columns_have_data_for_table_rendering(self):
        """buildTable 需要列数据 — 所有 Parquet 列都应存在"""
        df = pd.DataFrame({
            "bar_index": range(3),
            "bar_timestamp": pd.date_range("2026-01-01", periods=3, freq="D"),
            "close": [100.0, 101.0, 102.0],
            "v0_sig": [0, 1, -1],
        })
        columns, stats = df_to_columns(df)
        # buildTable uses parquetCols (stats.column_names) and columns dict
        assert stats["n_rows"] == 3
        assert len(stats["column_names"]) == 4
        for col_name in stats["column_names"]:
            assert col_name in columns
            assert len(columns[col_name]) == 3
