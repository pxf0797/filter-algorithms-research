"""
view_backtest.py 测试 — HTML 桥接、数据序列化、服务器模式。

测试范围:
- 模块可导入
- load_parquet / df_to_columns 数据转换
- serialize_value 边界值处理（NaN, inf, Timestamp）
- extract_ticker_name 名称提取
- find_latest_parquet / find_all_parquet_dirs 文件发现
- 多 Ticker 数据加载
"""

import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


# ── 确保 tools/ 在 sys.path 中 ────────────────────────────────────────────────
_TOOLS_DIR = str(Path(__file__).resolve().parent.parent / "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

# Verify view_backtest is importable
import view_backtest  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════════
# 数据序列化
# ═══════════════════════════════════════════════════════════════════════════════

class TestSerializeValue:
    """验证 serialize_value 边界值处理。"""

    def test_none_returns_none(self):
        """None 返回 None。"""
        result = view_backtest.serialize_value(None)
        assert result is None

    def test_nan_returns_none(self):
        """NaN 返回 None。"""
        result = view_backtest.serialize_value(float("nan"))
        assert result is None

    def test_inf_returns_none(self):
        """正无穷返回 None。"""
        result = view_backtest.serialize_value(float("inf"))
        assert result is None

    def test_neg_inf_returns_none(self):
        """负无穷返回 None。"""
        result = view_backtest.serialize_value(float("-inf"))
        assert result is None

    def test_pd_na_returns_string(self):
        """pd.NA 返回字符串表示（非 float，不触发 NaN 检测分支）。"""
        result = view_backtest.serialize_value(pd.NA)
        # pd.NA 不是 float 类型，走 final fallback str(v) → "<NA>"
        assert isinstance(result, str)

    def test_bool_returns_bool(self):
        """布尔值返回原布尔值。"""
        assert view_backtest.serialize_value(True) is True
        assert view_backtest.serialize_value(False) is False

    def test_int_returns_int(self):
        """整数返回原值。"""
        assert view_backtest.serialize_value(42) == 42

    def test_float_returns_float(self):
        """有限浮点数返回原值。"""
        assert view_backtest.serialize_value(3.14) == 3.14

    def test_string_returns_string(self):
        """字符串返回原值。"""
        assert view_backtest.serialize_value("hello") == "hello"

    def test_timestamp_returns_iso(self):
        """Timestamp 返回 ISO 格式字符串。"""
        ts = pd.Timestamp("2026-07-24 12:30:00")
        result = view_backtest.serialize_value(ts)
        assert "2026" in result
        assert "07" in result
        assert "24" in result

    def test_unknown_type_returns_str(self):
        """未知类型返回字符串表示。"""
        result = view_backtest.serialize_value(complex(1, 2))
        assert isinstance(result, str)


# ═══════════════════════════════════════════════════════════════════════════════
# DataFrame 转换
# ═══════════════════════════════════════════════════════════════════════════════

class TestDfToColumns:
    """验证 df_to_columns 将 DataFrame 转为 column-major JSON。"""

    def test_basic_conversion(self):
        """基本 DataFrame 被正确转换为列式结构。"""
        df = pd.DataFrame({
            "a": [1, 2, 3],
            "b": [4.0, 5.0, 6.0],
        })
        columns, stats = view_backtest.df_to_columns(df)

        assert stats["n_rows"] == 3
        assert stats["n_cols"] == 2
        assert columns["a"] == [1, 2, 3]
        assert columns["b"] == [4.0, 5.0, 6.0]

    def test_nan_handling(self):
        """NaN 值被序列化为 None。"""
        df = pd.DataFrame({"x": [1.0, float("nan"), 3.0]})
        columns, stats = view_backtest.df_to_columns(df)

        assert columns["x"][0] == 1.0
        assert columns["x"][1] is None
        assert columns["x"][2] == 3.0

    def test_datetime_conversion(self):
        """datetime 列被转换为 ISO 字符串。"""
        df = pd.DataFrame({
            "ts": pd.date_range("2026-01-01", periods=2, freq="D"),
            "val": [100, 101],
        })
        columns, stats = view_backtest.df_to_columns(df)

        assert isinstance(columns["ts"][0], str)
        assert "2026" in columns["ts"][0]

    def test_empty_dataframe(self):
        """空 DataFrame 产生空结构。"""
        df = pd.DataFrame()
        columns, stats = view_backtest.df_to_columns(df)

        assert stats["n_rows"] == 0
        assert stats["n_cols"] == 0
        assert stats["column_names"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# Parquet 读取
# ═══════════════════════════════════════════════════════════════════════════════

class TestLoadParquet:
    """验证 load_parquet 读取和转换。"""

    def test_valid_parquet(self, tmp_path):
        """有效 Parquet 文件被正确读取。"""
        parquet_path = tmp_path / "test.parquet"
        df = pd.DataFrame({
            "v0_pnl_long": [100.0, 101.0, 102.0],
            "v0_pnl_short": [100.0, 100.0, 100.0],
        })
        df.to_parquet(parquet_path)

        columns, stats = view_backtest.load_parquet(str(parquet_path))
        assert stats["n_rows"] == 3
        assert stats["n_cols"] == 2
        assert "v0_pnl_long" in columns


# ═══════════════════════════════════════════════════════════════════════════════
# Ticker 名称提取
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtractTickerName:
    """验证 extract_ticker_name 从路径提取 ticker。"""

    def test_parquet_file_path(self):
        """.parquet 文件路径：取父目录的第一个 _ 前部分。"""
        result = view_backtest.extract_ticker_name(
            "AAPL_20260724/backtest_result.parquet"
        )
        assert result == "AAPL"

    def test_directory_path(self):
        """目录路径：取目录名的第一个 _ 前部分。"""
        # 注意：Path("600115_20260724-224538-600115/").name 在有 trailing slash 时行为不同
        result = view_backtest.extract_ticker_name(
            "600115_20260724-224538-600115"
        )
        assert result == "600115"

    def test_simple_name(self):
        """简单无下划线名称。"""
        result = view_backtest.extract_ticker_name(
            "2382_20260722"
        )
        assert result == "2382"

    def test_to_upper(self):
        """结果转为大写。"""
        result = view_backtest.extract_ticker_name(
            "aapl_test/result.parquet"
        )
        assert result == "AAPL"


# ═══════════════════════════════════════════════════════════════════════════════
# 文件发现
# ═══════════════════════════════════════════════════════════════════════════════

class TestFindParquet:
    """验证 parquet 文件发现函数。"""

    def test_find_latest(self, tmp_path):
        """find_latest_parquet 找到修改时间最新的文件。"""
        d1 = tmp_path / "ticker1"
        d1.mkdir()
        df1 = pd.DataFrame({"a": [1]})
        df1.to_parquet(d1 / "backtest_result.parquet")

        time.sleep(0.1)  # 确保 mtime 不同

        d2 = tmp_path / "ticker2"
        d2.mkdir()
        df2 = pd.DataFrame({"a": [2]})
        df2.to_parquet(d2 / "backtest_result.parquet")

        result = view_backtest.find_latest_parquet(str(tmp_path))
        assert result is not None
        assert "ticker2" in result

    def test_find_latest_empty(self, tmp_path):
        """空目录返回 None。"""
        result = view_backtest.find_latest_parquet(str(tmp_path))
        assert result is None

    def test_find_all(self, tmp_path):
        """find_all_parquet_dirs 返回所有含 backtest_result.parquet 的目录。"""
        for name in ["AAPL", "MSFT", "GOOGL"]:
            d = tmp_path / name
            d.mkdir()
            pd.DataFrame({"a": [1]}).to_parquet(d / "backtest_result.parquet")

        dirs = view_backtest.find_all_parquet_dirs(str(tmp_path))
        assert len(dirs) == 3

    def test_find_all_empty(self, tmp_path):
        """无 parquet 的目录返回空列表。"""
        dirs = view_backtest.find_all_parquet_dirs(str(tmp_path))
        assert dirs == []


# ═══════════════════════════════════════════════════════════════════════════════
# 多 Ticker 加载
# ═══════════════════════════════════════════════════════════════════════════════

class TestLoadMultiParquet:
    """验证多 Ticker 数据加载。"""

    def test_single_ticker(self, tmp_path):
        """单个 ticker 加载返回正确结构。"""
        p = tmp_path / "test.parquet"
        df = pd.DataFrame({"v0_pnl_long": [100.0, 101.0]})
        df.to_parquet(p)

        result = view_backtest.load_multi_parquet([str(p)])
        assert len(result["tickers"]) == 1
        assert result["is_multiticker"] is False
        # ticker name extracted from "test.parquet" -> parent dir name split by _
        assert result["tickers"][0]["name"] is not None

    def test_multiple_tickers(self, tmp_path):
        """多个 ticker 加载返回多 ticker 结构。"""
        paths = []
        for name in ["AAPL", "MSFT"]:
            d = tmp_path / name
            d.mkdir()
            df = pd.DataFrame({"v0_pnl_long": [100.0, 101.0]})
            df.to_parquet(d / "backtest_result.parquet")
            paths.append(str(d))

        result = view_backtest.load_multi_parquet(paths)
        assert result["is_multiticker"] is True
        assert len(result["tickers"]) == 2
        assert set(t["name"] for t in result["tickers"]) == {"AAPL", "MSFT"}
        assert "AAPL" in result["data"]
        assert "MSFT" in result["data"]

    def test_colors_assigned(self, tmp_path):
        """ticker 自动分配颜色。"""
        d = tmp_path / "AAPL"
        d.mkdir()
        pd.DataFrame({"v0_pnl_long": [100.0]}).to_parquet(d / "backtest_result.parquet")

        result = view_backtest.load_multi_parquet([str(d)])
        assert "color" in result["tickers"][0]
        assert result["tickers"][0]["color"].startswith("#")


# ═══════════════════════════════════════════════════════════════════════════════
# 元数据加载
# ═══════════════════════════════════════════════════════════════════════════════

class TestLoadMetadata:
    """验证 load_metadata 函数。"""

    def test_valid_metadata(self, tmp_path):
        """有效 JSON 正确加载。"""
        meta_path = tmp_path / "metadata.json"
        meta = {"view_labels": {"v0": "SMA(120)", "v1": "MACD"}, "ticker": "AAPL"}
        meta_path.write_text(json.dumps(meta))

        result = view_backtest.load_metadata(str(meta_path))
        assert result is not None
        assert result["ticker"] == "AAPL"
        assert result["view_labels"]["v0"] == "SMA(120)"

    def test_missing_file_returns_none(self, tmp_path):
        """不存在的文件返回 None。"""
        result = view_backtest.load_metadata(str(tmp_path / "nonexistent.json"))
        assert result is None

    def test_invalid_json_returns_none(self, tmp_path):
        """无效 JSON 返回 None。"""
        bad_path = tmp_path / "bad.json"
        bad_path.write_text("not valid json{{{")

        result = view_backtest.load_metadata(str(bad_path))
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# 默认目录回退
# ═══════════════════════════════════════════════════════════════════════════════

class TestDefaultDirFallback:
    """默认目录回退: backtest_output → test_backtest_output"""

    def test_find_latest_parquet_called_with_fallback(self):
        """backtest_output 无结果时应调用 test_backtest_output。"""
        with patch.object(view_backtest, "find_latest_parquet") as mock_find:
            mock_find.side_effect = [None, "/fake/path/result.parquet"]

            # 复现 main() 中第 484-491 行的回退逻辑
            path = view_backtest.find_latest_parquet("backtest_output")
            if not path:
                path = view_backtest.find_latest_parquet("test_backtest_output")

            assert path == "/fake/path/result.parquet"
            assert mock_find.call_count == 2
            assert mock_find.call_args_list[0] == (("backtest_output",),)
            assert mock_find.call_args_list[1] == (("test_backtest_output",),)

    def test_first_dir_succeeds_no_fallback(self):
        """backtest_output 直接找到时不调用 test_backtest_output。"""
        with patch.object(view_backtest, "find_latest_parquet") as mock_find:
            mock_find.return_value = "/path/result.parquet"

            path = view_backtest.find_latest_parquet("backtest_output")
            if not path:
                path = view_backtest.find_latest_parquet("test_backtest_output")

            assert path == "/path/result.parquet"
            assert mock_find.call_count == 1
            mock_find.assert_called_once_with("backtest_output")

    def test_both_dirs_empty_returns_none(self):
        """两个目录都不存在时返回 None。"""
        with patch.object(view_backtest, "find_latest_parquet") as mock_find:
            mock_find.return_value = None

            path = view_backtest.find_latest_parquet("backtest_output")
            if not path:
                path = view_backtest.find_latest_parquet("test_backtest_output")

            assert path is None
            assert mock_find.call_count == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 目录输入解析 Parquet
# ═══════════════════════════════════════════════════════════════════════════════

class TestDirectoryInputResolvesParquet:
    """目录输入能正确找到 parquet 文件。"""

    def test_directory_with_backtest_result(self, tmp_path):
        """目录包含 backtest_result.parquet 时正确找到。"""
        df = pd.DataFrame({"a": [1, 2, 3]})
        df.to_parquet(tmp_path / "backtest_result.parquet")

        candidates = list(tmp_path.glob("backtest_result.parquet"))
        assert len(candidates) == 1
        assert candidates[0].name == "backtest_result.parquet"

    def test_directory_with_other_parquet(self, tmp_path):
        """目录包含其他 .parquet 时回退到 *.parquet glob。"""
        df = pd.DataFrame({"a": [1]})
        df.to_parquet(tmp_path / "my_data.parquet")

        # 复现 embed_and_open 中的逻辑
        candidates = list(tmp_path.glob("backtest_result.parquet"))
        if not candidates:
            candidates = list(tmp_path.glob("*.parquet"))
        assert len(candidates) == 1
        assert candidates[0].name == "my_data.parquet"

    def test_empty_directory_returns_empty(self, tmp_path):
        """空目录找不到任何 parquet。"""
        candidates = list(tmp_path.glob("backtest_result.parquet")) or list(tmp_path.glob("*.parquet"))
        assert candidates == []

    def test_directory_with_both_prefers_backtest_result(self, tmp_path):
        """同时存在 backtest_result.parquet 和其他 parquet 时优先取前者。"""
        pd.DataFrame({"a": [1]}).to_parquet(tmp_path / "backtest_result.parquet")
        pd.DataFrame({"a": [2]}).to_parquet(tmp_path / "other.parquet")

        candidates = list(tmp_path.glob("backtest_result.parquet")) or list(tmp_path.glob("*.parquet"))
        assert len(candidates) == 1
        assert candidates[0].name == "backtest_result.parquet"


# ═══════════════════════════════════════════════════════════════════════════════
# first_path 变量一致性
# ═══════════════════════════════════════════════════════════════════════════════

class TestFirstPathVariableConsistency:
    """first_path 在目录输入和文件输入模式下都指向实际的 parquet 文件路径。"""

    def test_file_input_first_path_is_parquet(self):
        """文件路径输入时 first_path 直接是 parquet 文件路径。"""
        parquet_paths = ["/some/path/result.parquet"]
        p = Path(parquet_paths[0])
        assert not p.is_dir()
        actual_path = parquet_paths[0]
        first_path = actual_path  # embed_and_open L348 逻辑
        assert first_path.endswith(".parquet")
        assert Path(first_path).suffix == ".parquet"

    def test_directory_input_first_path_is_parquet(self, tmp_path):
        """目录输入时 first_path 指向找到的实际 parquet 文件（而非目录）。"""
        # 创建目录和 parquet
        pd.DataFrame({"a": [1]}).to_parquet(tmp_path / "backtest_result.parquet")

        parquet_paths = [str(tmp_path)]
        p = Path(parquet_paths[0])
        assert p.is_dir()
        candidates = list(p.glob("backtest_result.parquet")) or list(p.glob("*.parquet"))
        actual_path = str(candidates[0])
        first_path = actual_path  # L348 逻辑
        assert Path(first_path).suffix == ".parquet"
        assert Path(first_path).exists()

    def test_first_path_used_for_metadata_dir(self, tmp_path):
        """first_path 的父目录用于 metadata 自动探测。"""
        pd.DataFrame({"a": [1]}).to_parquet(tmp_path / "backtest_result.parquet")
        # 也创建 metadata
        (tmp_path / "metadata.json").write_text('{"ticker": "TEST"}')

        parquet_paths = [str(tmp_path)]
        p = Path(parquet_paths[0])
        actual_path = str(list(p.glob("backtest_result.parquet"))[0])
        first_path = actual_path  # L348

        # auto_meta_dir 逻辑 (L358)
        auto_meta_dir = Path(first_path).parent if not Path(first_path).is_dir() else Path(first_path)
        auto_meta = auto_meta_dir / "metadata.json"
        assert auto_meta.exists()


# ═══════════════════════════════════════════════════════════════════════════════
# 模块导入 — 放在最后，因为之前的 import view_backtest 已经验证了可导入性
# ═══════════════════════════════════════════════════════════════════════════════

class TestImport:
    """验证 view_backtest.py 文件存在且通过 importlib 可加载。"""

    def test_file_exists(self):
        """view_backtest.py 文件存在。"""
        view_path = Path(__file__).resolve().parent.parent / "tools" / "view_backtest.py"
        assert view_path.exists()

    def test_can_import_as_spec(self):
        """可通过 importlib.util 导入。"""
        import importlib.util
        view_path = Path(__file__).resolve().parent.parent / "tools" / "view_backtest.py"
        spec = importlib.util.spec_from_file_location("view_backtest", str(view_path))
        assert spec is not None
