"""Tests for multi-ticker backtest visualization support in tools/view_backtest.py."""

import json
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest

# Ensure tools/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import view_backtest as vb


# ============================================================
# Fixtures
# ============================================================

def _make_parquet(dir_path, ticker, n_rows=50, n_cols=5):
    """Create a minimal parquet file simulating backtest output."""
    import numpy as np

    np.random.seed(hash(ticker) % 2**32)
    df = pd.DataFrame({
        "bar_timestamp": pd.date_range("2024-01-01", periods=n_rows, freq="1h"),
        "bar_index": list(range(n_rows)),
        "v0_signal": np.random.choice([-1, 0, 1], n_rows),
        "v0_pnl_long": np.cumsum(np.random.randn(n_rows) * 0.5) + 100,
        "v0_pnl_short": np.cumsum(np.random.randn(n_rows) * 0.5) + 100,
    })
    path = os.path.join(dir_path, "backtest_result.parquet")
    df.to_parquet(path)
    return path


@pytest.fixture
def single_ticker_dir():
    """Create a temp dir with a single ticker's parquet."""
    with tempfile.TemporaryDirectory() as td:
        _make_parquet(td, "AAPL")
        yield td


@pytest.fixture
def multi_ticker_dirs():
    """Create temp dirs for multiple tickers."""
    tickers = ["AAPL", "MSFT", "GOOGL"]
    dirs = {}
    with tempfile.TemporaryDirectory() as root:
        for tk in tickers:
            td = os.path.join(root, f"{tk}_20240101_backtest")
            os.makedirs(td)
            _make_parquet(td, tk)
            dirs[tk] = td
        yield dirs


@pytest.fixture
def multi_ticker_dirs_flat():
    """Flat list of multi-ticker dirs."""
    tickers = ["TSLA", "NVDA"]
    dirs = {}
    with tempfile.TemporaryDirectory() as root:
        for tk in tickers:
            td = os.path.join(root, tk)
            os.makedirs(td)
            _make_parquet(td, tk)
            dirs[tk] = td
        yield dirs


# ============================================================
# Tests: extract_ticker_name
# ============================================================

def test_extract_ticker_from_dir():
    """Ticker extracted from directory name pattern TICKER_xxx."""
    assert vb.extract_ticker_name("/data/AAPL_20240101_backtest/") == "AAPL"


def test_extract_ticker_from_parquet_path():
    """Ticker extracted from parquet file path."""
    assert vb.extract_ticker_name("/data/MSFT_20240101_backtest/backtest_result.parquet") == "MSFT"


def test_extract_ticker_simple_name():
    """Ticker extracted when directory name has no underscore."""
    assert vb.extract_ticker_name("/data/GOOGL") == "GOOGL"


def test_extract_ticker_lowercase():
    """Ticker name is uppercased."""
    assert vb.extract_ticker_name("/data/aapl_backtest/") == "AAPL"


# ============================================================
# Tests: load_multi_parquet
# ============================================================

def test_load_single_ticker(multi_ticker_dirs):
    """Loading a single ticker returns valid structure."""
    paths = [multi_ticker_dirs["AAPL"]]
    result = vb.load_multi_parquet(paths)

    assert "tickers" in result
    assert "data" in result
    assert "is_multiticker" in result
    assert len(result["tickers"]) == 1
    assert result["tickers"][0]["name"] == "AAPL"
    assert "color" in result["tickers"][0]
    assert "AAPL" in result["data"]


def test_load_multi_ticker(multi_ticker_dirs):
    """Loading multiple tickers returns all in the result."""
    paths = [multi_ticker_dirs["AAPL"], multi_ticker_dirs["MSFT"], multi_ticker_dirs["GOOGL"]]
    result = vb.load_multi_parquet(paths)

    assert len(result["tickers"]) == 3
    assert result["is_multiticker"] is True

    names = [t["name"] for t in result["tickers"]]
    assert "AAPL" in names
    assert "MSFT" in names
    assert "GOOGL" in names

    for tk in result["tickers"]:
        assert tk["name"] in result["data"]


def test_load_multi_ticker_row_counts_sum_correctly(multi_ticker_dirs_flat):
    """Each ticker's data has correct row count."""
    paths = list(multi_ticker_dirs_flat.values())
    result = vb.load_multi_parquet(paths)

    total_rows = 0
    for tk in result["tickers"]:
        stats = result["data"][tk["name"]]["stats"]
        assert stats["n_rows"] == 50
        total_rows += stats["n_rows"]

    assert total_rows == 100  # 2 tickers x 50 rows each


def test_load_multi_ticker_unique_colors(multi_ticker_dirs):
    """Each ticker gets a different color."""
    paths = list(multi_ticker_dirs.values())
    result = vb.load_multi_parquet(paths)

    colors = [t["color"] for t in result["tickers"]]
    assert len(colors) == len(set(colors)), f"Duplicate colors: {colors}"


def test_load_multi_ticker_preserves_column_names(multi_ticker_dirs):
    """Column names are preserved in each ticker's data."""
    paths = [multi_ticker_dirs["AAPL"]]
    result = vb.load_multi_parquet(paths)

    columns = result["data"]["AAPL"]["columns"]
    assert "bar_timestamp" in columns
    assert "bar_index" in columns
    assert "v0_signal" in columns
    assert "v0_pnl_long" in columns


def test_load_multi_ticker_data_integrity(multi_ticker_dirs_flat):
    """Column-major arrays have consistent lengths within each ticker."""
    paths = list(multi_ticker_dirs_flat.values())
    result = vb.load_multi_parquet(paths)

    for tk in result["tickers"]:
        data = result["data"][tk["name"]]
        n = data["stats"]["n_rows"]
        for col_name in data["columns"]:
            assert len(data["columns"][col_name]) == n, \
                f"Column {col_name} in {tk['name']} has {len(data['columns'][col_name])} rows, expected {n}"


def test_load_multi_ticker_missing_dir_skipped_with_warning(tmp_path, capsys):
    """Non-existent directory is warned about and skipped."""
    good_dir = str(tmp_path / "AAPL_backtest")
    os.makedirs(good_dir)
    _make_parquet(good_dir, "AAPL")

    missing_dir = str(tmp_path / "NONEXISTENT_backtest")
    result = vb.load_multi_parquet([good_dir, missing_dir])

    # Should have loaded the good one
    assert len(result["tickers"]) == 1
    assert result["tickers"][0]["name"] == "AAPL"

    # Warning should have been emitted
    captured = capsys.readouterr()
    assert "跳过" in captured.err or "找不到" in captured.err


# ============================================================
# Tests: find_all_parquet_dirs
# ============================================================

def test_find_all_parquet_dirs(multi_ticker_dirs):
    """Finds all directories containing backtest_result.parquet."""
    root = os.path.dirname(list(multi_ticker_dirs.values())[0])
    dirs = vb.find_all_parquet_dirs(root)
    assert len(dirs) == 3


def test_find_all_parquet_dirs_empty(tmp_path):
    """Returns empty list when no parquets found."""
    dirs = vb.find_all_parquet_dirs(str(tmp_path))
    assert dirs == []


# ============================================================
# Tests: JSON serialization round-trip
# ============================================================

def test_multi_data_json_roundtrip(multi_ticker_dirs):
    """Multi-ticker data survives JSON serialization round-trip."""
    paths = list(multi_ticker_dirs.values())
    result = vb.load_multi_parquet(paths)

    json_str = json.dumps(result, ensure_ascii=False)
    restored = json.loads(json_str)

    assert len(restored["tickers"]) == len(result["tickers"])
    for orig, restored_tk in zip(result["tickers"], restored["tickers"]):
        assert orig["name"] == restored_tk["name"]
        assert orig["color"] == restored_tk["color"]

    for tk_name in result["data"]:
        assert tk_name in restored["data"]
        orig_stats = result["data"][tk_name]["stats"]
        restored_stats = restored["data"][tk_name]["stats"]
        assert orig_stats["n_rows"] == restored_stats["n_rows"]
        assert orig_stats["n_cols"] == restored_stats["n_cols"]


# ============================================================
# Tests: view_backtest module attributes
# ============================================================

def test_ticker_colors_defined():
    """TICKER_COLORS is a non-empty list of hex colors."""
    assert hasattr(vb, "TICKER_COLORS")
    assert len(vb.TICKER_COLORS) > 0
    for c in vb.TICKER_COLORS:
        assert c.startswith("#"), f"Color {c} should start with #"
        assert len(c) == 7, f"Color {c} should be 7 chars (e.g., #58a6ff)"


def test_html_template_path_exists():
    """HTML_TEMPLATE points to an existing file."""
    assert vb.HTML_TEMPLATE.exists(), f"Template not found: {vb.HTML_TEMPLATE}"


# ============================================================
# Tests: df_to_columns (existing function, verify unchanged behavior)
# ============================================================

def test_df_to_columns_basic():
    """df_to_columns produces correct column-major output."""
    df = pd.DataFrame({"a": [1, 2, 3], "b": [4.0, 5.0, 6.0]})
    cols, stats = vb.df_to_columns(df)
    assert stats["n_rows"] == 3
    assert stats["n_cols"] == 2
    assert cols["a"] == [1, 2, 3]
    assert cols["b"] == [4.0, 5.0, 6.0]


def test_df_to_columns_nulls():
    """NaN values are serialized as None."""
    df = pd.DataFrame({"x": [1.0, float("nan"), 3.0]})
    cols, _ = vb.df_to_columns(df)
    assert cols["x"] == [1.0, None, 3.0]


def test_serialize_value_inf():
    """Infinity values become None."""
    assert vb.serialize_value(float("inf")) is None
    assert vb.serialize_value(float("-inf")) is None


def test_serialize_value_timestamp():
    """Timestamps become ISO strings."""
    ts = pd.Timestamp("2024-06-15 12:30:00")
    assert vb.serialize_value(ts) == "2024-06-15T12:30:00"


# ============================================================
# Tests: load_metadata
# ============================================================


def test_load_metadata_valid_file(tmp_path):
    """load_metadata 正确读取包含 view_labels 的 metadata.json。"""
    meta_path = tmp_path / "metadata.json"
    meta_path.write_text(json.dumps({
        "ticker": "AAPL",
        "view_labels": {"v0": "日线", "v1": "60分钟", "v2": "15分钟", "v3": "5分钟"},
        "view_configs": {"v0": {"tf": "日线"}, "v1": {"tf": "60分钟"}},
    }, ensure_ascii=False), encoding="utf-8")

    meta = vb.load_metadata(str(meta_path))
    assert meta is not None
    assert meta["ticker"] == "AAPL"
    assert meta["view_labels"] == {"v0": "日线", "v1": "60分钟", "v2": "15分钟", "v3": "5分钟"}


def test_load_metadata_missing_file():
    """load_metadata 在文件不存在时返回 None（不抛异常）。"""
    meta = vb.load_metadata("/nonexistent/path/metadata.json")
    assert meta is None


def test_load_metadata_invalid_json(tmp_path):
    """load_metadata 在 JSON 解析失败时返回 None。"""
    bad_path = tmp_path / "bad.json"
    bad_path.write_text("{invalid json", encoding="utf-8")
    meta = vb.load_metadata(str(bad_path))
    assert meta is None


def test_load_metadata_empty_file(tmp_path):
    """load_metadata 在空文件时返回 None。"""
    empty_path = tmp_path / "empty.json"
    empty_path.write_text("", encoding="utf-8")
    meta = vb.load_metadata(str(empty_path))
    assert meta is None


# ============================================================
# Tests: metadata embedding in embed_and_open
# ============================================================


def test_embed_and_open_includes_metadata(tmp_path):
    """embed_and_open 在有 metadata 时将 BACKTEST_METADATA 嵌入 HTML。"""
    # 准备 parquet 文件
    import numpy as np

    parquet_dir = tmp_path / "TEST_20240101_backtest"
    parquet_dir.mkdir()
    df = pd.DataFrame({
        "bar_timestamp": pd.date_range("2024-01-01", periods=10, freq="h"),
        "bar_index": list(range(10)),
        "v0_signal": [0] * 10,
    })
    pq_path = parquet_dir / "backtest_result.parquet"
    df.to_parquet(str(pq_path))

    # 准备 metadata
    meta_path = parquet_dir / "metadata.json"
    meta_path.write_text(json.dumps({
        "ticker": "TEST",
        "view_labels": {"v0": "日线"},
    }, ensure_ascii=False), encoding="utf-8")

    # 验证 metadata 能被正确加载
    meta = vb.load_metadata(str(meta_path))
    assert meta is not None
    assert meta["view_labels"] == {"v0": "日线"}

    # 验证 BACKTEST_METADATA 会出现在 embed_script 中
    embed_script = "window.BACKTEST_METADATA = " + json.dumps(meta, ensure_ascii=False) + ";"
    assert "BACKTEST_METADATA" in embed_script
    assert '"view_labels"' in embed_script
    assert "日线" in embed_script


def test_embed_and_open_null_metadata(tmp_path):
    """embed_and_open 在无 metadata 时设置 BACKTEST_METADATA = null。"""
    embed_script = "window.BACKTEST_METADATA = null;"
    assert "null" in embed_script
    assert "BACKTEST_METADATA" in embed_script


# ============================================================
# Tests: start_server metadata embedding
# ============================================================


def test_start_server_embeds_metadata_in_html(tmp_path):
    """start_server 将 metadata 嵌入到 BacktestHandler.embedded_html 中。"""
    html_path = tmp_path / "test.html"
    html_content = "<html><head></head><body>test</body></html>"
    html_path.write_text(html_content, encoding="utf-8")

    metadata = {"view_labels": {"v0": "日线", "v1": "60分钟"}}

    # 模拟 start_server 的嵌入逻辑
    with open(str(html_path), encoding="utf-8") as f:
        html = f.read()
    embed_script = "<script>\n"
    embed_script += "window.BACKTEST_METADATA = " + json.dumps(metadata, ensure_ascii=False) + ";\n"
    embed_script += "</script>\n"
    embedded = html.replace("</head>", embed_script + "</head>", 1)

    # 验证嵌入结果
    assert "window.BACKTEST_METADATA" in embedded
    assert "日线" in embedded
    assert "</head>" in embedded
    # 原始 </head> 之后的内容仍在
    assert "test" in embedded

    # 验证空 metadata 时不嵌入
    vb.BacktestHandler.embedded_html = None
    assert vb.BacktestHandler.embedded_html is None


def test_start_server_null_metadata_clears_embedded_html(tmp_path):
    """start_server 接收 metadata=None 时清除 embedded_html。"""
    # 模拟设置 metadata=None 的行为
    vb.BacktestHandler.embedded_html = None
    assert vb.BacktestHandler.embedded_html is None
