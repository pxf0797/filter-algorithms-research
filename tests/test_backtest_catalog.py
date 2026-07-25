"""Tests for BacktestCatalog."""
import json
import tempfile
from pathlib import Path

import pytest

from filter.backtest.catalog import BacktestCatalog


@pytest.fixture
def sample_backtest_dir():
    """Create a temporary backtest_output directory with sample sessions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # Session 1: AAPL
        s1 = base / "AAPL_20260722-224538-AAPL"
        s1.mkdir()
        (s1 / "metadata.json").write_text(json.dumps({
            "ticker": "AAPL",
            "session_id": "20260722-224538-AAPL",
            "start_time": "2026-07-22T14:45:38+00:00",
            "end_time": "2026-07-22T14:47:19+00:00",
            "status": "completed",
            "parquet_row_count": 5745,
            "step_count": 5745,
            "views": ["v0", "v1", "v2", "v3"],
        }))

        # Session 2: 600115
        s2 = base / "600115_20260722-224538-600115"
        s2.mkdir()
        (s2 / "metadata.json").write_text(json.dumps({
            "ticker": "600115",
            "session_id": "20260722-224538-600115",
            "start_time": "2026-07-22T14:45:38+00:00",
            "end_time": "2026-07-22T14:47:19+00:00",
            "status": "completed",
            "parquet_row_count": 4200,
            "step_count": 4200,
            "views": ["v0", "v1"],
        }))

        # Session 3: TSLA (without metadata)
        s3 = base / "TSLA_20260722-test"
        s3.mkdir()

        # Hidden directory — should be skipped
        hidden = base / ".hidden_dir"
        hidden.mkdir()
        (hidden / "metadata.json").write_text(json.dumps({
            "ticker": "HIDDEN",
        }))

        yield base


class TestBacktestCatalogScan:
    def test_scan_finds_sessions(self, sample_backtest_dir):
        catalog = BacktestCatalog(str(sample_backtest_dir))
        sessions = catalog.scan()
        assert len(sessions) == 3
        names = {s["name"] for s in sessions}
        assert "AAPL_20260722-224538-AAPL" in names
        assert "600115_20260722-224538-600115" in names
        assert "TSLA_20260722-test" in names

    def test_scan_extracts_metadata(self, sample_backtest_dir):
        catalog = BacktestCatalog(str(sample_backtest_dir))
        sessions = catalog.scan()
        aapl = next(s for s in sessions if s["name"] == "AAPL_20260722-224538-AAPL")
        assert aapl["ticker"] == "AAPL"
        assert aapl["status"] == "completed"
        assert aapl["parquet_row_count"] == 5745
        assert aapl["views"] == ["v0", "v1", "v2", "v3"]

    def test_scan_handles_no_metadata(self, sample_backtest_dir):
        catalog = BacktestCatalog(str(sample_backtest_dir))
        sessions = catalog.scan()
        tsla = next(s for s in sessions if s["name"] == "TSLA_20260722-test")
        assert tsla["path"] is not None
        assert "ticker" not in tsla

    def test_scan_skips_hidden_dirs(self, sample_backtest_dir):
        catalog = BacktestCatalog(str(sample_backtest_dir))
        sessions = catalog.scan()
        names = {s["name"] for s in sessions}
        assert ".hidden_dir" not in names

    def test_scan_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            catalog = BacktestCatalog(str(tmpdir))
            sessions = catalog.scan()
            assert sessions == []


class TestBacktestCatalogIndex:
    def test_save_and_load_index(self, sample_backtest_dir):
        catalog = BacktestCatalog(str(sample_backtest_dir))
        catalog.save_index()
        assert catalog.index_file.exists()

        # Load via a new catalog instance
        catalog2 = BacktestCatalog(str(sample_backtest_dir))
        with open(catalog2.index_file) as f:
            loaded = json.load(f)
        assert len(loaded) == 3

    def test_save_with_explicit_sessions(self, sample_backtest_dir):
        catalog = BacktestCatalog(str(sample_backtest_dir))
        sessions = catalog.scan()
        # Save only AAPL
        aapl_only = [s for s in sessions if s["name"].startswith("AAPL")]
        catalog.save_index(aapl_only)
        with open(catalog.index_file) as f:
            loaded = json.load(f)
        assert len(loaded) == 1
        assert loaded[0]["name"] == "AAPL_20260722-224538-AAPL"


class TestBacktestCatalogQuery:
    def test_query_filter_by_ticker(self, sample_backtest_dir):
        catalog = BacktestCatalog(str(sample_backtest_dir))
        results = catalog.query(ticker="AAPL")
        assert len(results) == 1
        assert results[0]["ticker"] == "AAPL"

    def test_query_returns_all_without_filters(self, sample_backtest_dir):
        catalog = BacktestCatalog(str(sample_backtest_dir))
        results = catalog.query()
        assert len(results) == 3

    def test_query_no_match(self, sample_backtest_dir):
        catalog = BacktestCatalog(str(sample_backtest_dir))
        results = catalog.query(ticker="NONEXISTENT")
        assert results == []

    def test_query_uses_cached_index(self, sample_backtest_dir):
        catalog = BacktestCatalog(str(sample_backtest_dir))
        catalog.save_index()
        # Query should use .catalog.json
        results = catalog.query(ticker="600115")
        assert len(results) == 1
        assert results[0]["ticker"] == "600115"
