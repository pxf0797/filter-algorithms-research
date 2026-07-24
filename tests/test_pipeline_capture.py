"""
Tests for PipelineCapture — feature-flag-gated pipeline data capture.

Covers:
  - PipelineStageData creation and defaults
  - PIPELINE_CAPTURE=0 (disabled): all methods are no-ops
  - PIPELINE_CAPTURE=1 (enabled): creates files, correct structure
  - _write_view, _write_parquet, _write_json helpers
  - session cleanup
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Ensure filter is importable
# ---------------------------------------------------------------------------
_src = Path(__file__).resolve().parent.parent / "filter"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))


# ============================================================================
# PipelineStageData tests
# ============================================================================


class TestPipelineStageData:
    """PipelineStageData container creation and defaults."""

    def test_default_construction(self):
        """All fields default to None/empty strings on construction."""
        from backtest.pipeline import PipelineStageData

        psd = PipelineStageData()
        assert psd.view_name == ""
        assert psd.tf == ""
        assert psd.t is None
        assert psd.noisy is None
        assert psd.filtered is None
        assert psd.filtered2 is None
        assert psd.sig is None
        assert psd.v is None
        assert psd.a is None
        assert psd.eps is None
        assert psd.mu_v is None
        assert psd.sigma_v is None
        assert psd.all_pairs is None
        assert psd.prediction_pairs is None
        assert psd.trade_records is None
        assert psd.pnl_long is None
        assert psd.pnl_short is None
        assert psd.higher_pnl is None
        assert psd.long_mask is None
        assert psd.short_mask is None
        assert psd.bs_markers is None

    def test_partial_construction(self):
        """Can set a few fields, rest default to None."""
        from backtest.pipeline import PipelineStageData

        t = np.arange(50, dtype=float)
        psd = PipelineStageData(view_name="v0_日线", t=t, filtered=np.ones(50))
        assert psd.view_name == "v0_日线"
        assert psd.t is not None
        assert len(psd.t) == 50
        assert psd.filtered is not None
        assert psd.filtered2 is None  # unset

    def test_full_construction(self):
        """All kwargs set correctly."""
        from backtest.pipeline import PipelineStageData

        n = 20
        t = np.arange(n, dtype=float)
        dates = pd.date_range("2026-01-01", periods=n, freq="D")
        noisy = np.random.randn(n)
        ohlc = pd.DataFrame({"open": np.ones(n), "high": np.ones(n),
                             "low": np.ones(n), "close": np.ones(n)})
        filtered = np.random.randn(n)
        sig = np.zeros(n, dtype=int)
        v = np.ones(n) * 0.5
        a = np.zeros(n)
        eps = np.full(n, 0.1)
        mu_v = np.zeros(n)
        sigma_v = np.ones(n) * 0.05
        pairs = [(10, 50)]
        preds = [(10, 50, 0.8)]
        trades = [{"type": "long", "entry_idx": 10, "exit_idx": 50}]
        pnl_l = np.linspace(100, 110, n)
        pnl_s = np.linspace(100, 95, n)
        higher = {"dates": dates, "long_pnl": pnl_l}
        lm = np.zeros(n, dtype=bool)
        sm = np.zeros(n, dtype=bool)
        bs = {"entry_markers": [], "exit_markers": []}

        psd = PipelineStageData(
            view_name="v1_60分钟", tf="60分钟",
            t=t, dates=dates, noisy=noisy, ohlc=ohlc,
            filtered=filtered, filtered2=None,
            sig=sig, v=v, a=a, eps=eps, mu_v=mu_v, sigma_v=sigma_v,
            all_pairs=pairs, prediction_pairs=preds,
            trade_records=trades, pnl_long=pnl_l, pnl_short=pnl_s,
            higher_pnl=higher, long_mask=lm, short_mask=sm, bs_markers=bs,
        )
        assert psd.view_name == "v1_60分钟"
        assert psd.tf == "60分钟"
        assert len(psd.t) == n
        assert psd.sig is not None
        assert psd.all_pairs == pairs
        assert psd.trade_records == trades


# ============================================================================
# PipelineCapture — disabled mode (PIPELINE_CAPTURE=0)
# ============================================================================


class TestPipelineCaptureDisabled:
    """When PIPELINE_CAPTURE=0 (or not set), all methods are no-ops."""

    @pytest.fixture(autouse=True)
    def _ensure_disabled(self, monkeypatch):
        """Ensure PIPELINE_CAPTURE is not set."""
        monkeypatch.delenv("PIPELINE_CAPTURE", raising=False)

    def test_is_enabled_returns_false(self):
        """is_enabled() returns False when env var not set."""
        from backtest.pipeline import PipelineCapture
        assert PipelineCapture.is_enabled() is False

    def test_is_enabled_false_with_zero(self, monkeypatch):
        """is_enabled() returns False when PIPELINE_CAPTURE=0."""
        monkeypatch.setenv("PIPELINE_CAPTURE", "0")
        from backtest.pipeline import PipelineCapture
        assert PipelineCapture.is_enabled() is False

    def test_start_session_returns_empty(self):
        """start_session() returns '' when disabled."""
        from backtest.pipeline import PipelineCapture
        capture = PipelineCapture(ticker="AAPL")
        sid = capture.start_session()
        assert sid == ""

    def test_capture_step_noop(self, tmp_path):
        """capture_step() is a no-op when disabled (no files created)."""
        from backtest.pipeline import PipelineCapture, PipelineStageData

        capture = PipelineCapture(output_dir=str(tmp_path), ticker="AAPL")
        capture.start_session()

        psd = PipelineStageData(view_name="v0", t=np.arange(10, dtype=float),
                                filtered=np.ones(10), sig=np.zeros(10, dtype=int))
        capture.capture_step(0, "2026-01-15", {"v0": psd})

        # No files should be created
        all_files = list(tmp_path.rglob("*"))
        assert len(all_files) == 0

    def test_end_session_returns_empty(self):
        """end_session() returns {} when disabled."""
        from backtest.pipeline import PipelineCapture
        capture = PipelineCapture(ticker="AAPL")
        capture.start_session()
        summary = capture.end_session()
        assert summary == {}


# ============================================================================
# PipelineCapture — enabled mode (PIPELINE_CAPTURE=1)
# ============================================================================


class TestPipelineCaptureEnabled:
    """When PIPELINE_CAPTURE=1, methods create files and return data."""

    @pytest.fixture(autouse=True)
    def _ensure_enabled(self, monkeypatch):
        """Ensure PIPELINE_CAPTURE=1 for this test class."""
        monkeypatch.setenv("PIPELINE_CAPTURE", "1")

    def test_is_enabled_returns_true(self):
        """is_enabled() returns True when env var is 1."""
        from backtest.pipeline import PipelineCapture
        assert PipelineCapture.is_enabled() is True

    def test_start_session_creates_dir_and_metadata(self, tmp_path):
        """start_session creates a timestamped dir with metadata.json."""
        from backtest.pipeline import PipelineCapture

        capture = PipelineCapture(output_dir=str(tmp_path), ticker="AAPL",
                                  config={"min_tf": "日线"})
        sid = capture.start_session()
        assert sid != ""
        assert "AAPL" in sid

        # Check session directories exist
        session_dirs = list(tmp_path.glob("AAPL_*"))
        assert len(session_dirs) == 1

        session_dir = session_dirs[0]
        assert (session_dir / "metadata.json").exists()
        assert (session_dir / "steps").is_dir()

        # Validate metadata content
        meta = json.loads((session_dir / "metadata.json").read_text(encoding="utf-8"))
        assert meta["ticker"] == "AAPL"
        assert meta["config"] == {"min_tf": "日线"}
        assert "start_time" in meta
        assert "session_id" in meta

    def test_capture_step_creates_stage_files(self, tmp_path):
        """capture_step writes parquet/json files for a view's pipeline stages."""
        from backtest.pipeline import PipelineCapture, PipelineStageData

        capture = PipelineCapture(output_dir=str(tmp_path), ticker="AAPL")
        capture.start_session()

        n = 50
        t = np.arange(n, dtype=float)
        psd = PipelineStageData(
            view_name="v0_日线",
            t=t,
            filtered=np.random.randn(n),
            sig=np.zeros(n, dtype=int),
            v=np.ones(n) * 0.5,
            a=np.zeros(n),
            eps=np.full(n, 0.1),
            mu_v=np.zeros(n),
            sigma_v=np.ones(n) * 0.05,
            all_pairs=[(10, 40)],
            trade_records=[{"type": "long", "entry_idx": 10, "exit_idx": 40}],
            pnl_long=np.linspace(100, 110, n),
            pnl_short=np.linspace(100, 95, n),
            long_mask=np.zeros(n, dtype=bool),
            short_mask=np.zeros(n, dtype=bool),
            bs_markers={"entry_markers": [], "exit_markers": []},
        )
        capture.capture_step(0, "2026-01-15", {"v0_日线": psd})

        # Locate step dir and verify files
        session_dirs = list(tmp_path.glob("AAPL_*"))
        assert len(session_dirs) == 1
        step_dir = session_dirs[0] / "steps" / "000000"
        assert step_dir.exists()

        # index.json
        assert (step_dir / "index.json").exists()
        idx = json.loads((step_dir / "index.json").read_text(encoding="utf-8"))
        assert idx["step_index"] == 0
        assert idx["cutoff_date"] == "2026-01-15"

        # View directory and stage files
        view_dir = step_dir / "v0_日线"
        assert view_dir.is_dir()

        # Stage 03: filter
        assert (view_dir / "stage_03_filter.parquet").exists()
        # Stage 04: schmitt
        assert (view_dir / "stage_04_schmitt.parquet").exists()
        # Stage 05: pairs
        assert (view_dir / "stage_05_pairs.json").exists()
        # Stage 07: trades
        assert (view_dir / "stage_07_trades.json").exists()
        # Stage 07: pnl
        assert (view_dir / "stage_07_pnl.parquet").exists()
        # Stage 09: masks
        assert (view_dir / "stage_09_masks.parquet").exists()
        # Stage 10: BS markers
        assert (view_dir / "stage_10_bs_markers.json").exists()

    def test_capture_step_skips_null_fields(self, tmp_path):
        """Fields that are None should be skipped (no parquet written)."""
        from backtest.pipeline import PipelineCapture, PipelineStageData

        capture = PipelineCapture(output_dir=str(tmp_path), ticker="AAPL")
        capture.start_session()

        # Only provide filtered — no schmitt, pairs, trades, pnl, etc.
        n = 10
        psd = PipelineStageData(view_name="v0", t=np.arange(n, dtype=float),
                                filtered=np.ones(n))
        capture.capture_step(0, "2026-01-15", {"v0": psd})

        session_dirs = list(tmp_path.glob("AAPL_*"))
        view_dir = session_dirs[0] / "steps" / "000000" / "v0"
        assert view_dir.is_dir()

        # Only stage_03_filter should exist
        files = list(view_dir.iterdir())
        file_names = [f.name for f in files]
        assert "stage_03_filter.parquet" in file_names
        assert "stage_04_schmitt.parquet" not in file_names
        assert "stage_05_pairs.json" not in file_names

    def test_capture_step_skips_none_cutoff(self, tmp_path):
        """capture_step with cutoff_date=None should skip entirely."""
        from backtest.pipeline import PipelineCapture, PipelineStageData

        capture = PipelineCapture(output_dir=str(tmp_path), ticker="AAPL")
        capture.start_session()

        psd = PipelineStageData(view_name="v0", t=np.arange(10, dtype=float),
                                filtered=np.ones(10))
        capture.capture_step(0, None, {"v0": psd})

        session_dirs = list(tmp_path.glob("AAPL_*"))
        steps_dir = session_dirs[0] / "steps"
        # steps dir exists but no step subdirectories
        step_subdirs = list(steps_dir.iterdir())
        assert len(step_subdirs) == 0

    def test_capture_step_empty_views(self, tmp_path):
        """capture_step with empty views_data skips."""
        from backtest.pipeline import PipelineCapture

        capture = PipelineCapture(output_dir=str(tmp_path), ticker="AAPL")
        capture.start_session()

        capture.capture_step(0, "2026-01-15", {})

        session_dirs = list(tmp_path.glob("AAPL_*"))
        steps_dir = session_dirs[0] / "steps"
        step_subdirs = list(steps_dir.iterdir())
        assert len(step_subdirs) == 0

    def test_end_session_returns_summary(self, tmp_path):
        """end_session returns summary with step_count and sizes."""
        from backtest.pipeline import PipelineCapture, PipelineStageData

        capture = PipelineCapture(output_dir=str(tmp_path), ticker="MSFT")
        capture.start_session()

        n = 20
        psd = PipelineStageData(view_name="v0", t=np.arange(n, dtype=float),
                                filtered=np.ones(n), sig=np.zeros(n, dtype=int),
                                v=np.ones(n) * 0.5, a=np.zeros(n),
                                eps=np.full(n, 0.1), mu_v=np.zeros(n),
                                sigma_v=np.ones(n) * 0.05)
        capture.capture_step(0, "2026-01-15", {"v0": psd})
        capture.capture_step(1, "2026-01-16", {"v0": psd})

        summary = capture.end_session()
        assert summary["step_count"] == 2
        assert summary["total_size_bytes"] > 0
        assert summary["total_size_mb"] >= 0  # may round to 0 for tiny files
        # Metadata updated
        session_dirs = list(tmp_path.glob("MSFT_*"))
        meta = json.loads((session_dirs[0] / "metadata.json").read_text(encoding="utf-8"))
        assert "end_time" in meta
        assert meta["step_count"] == 2

    def test_multiple_views_same_step(self, tmp_path):
        """Multiple views in a single capture_step create separate dirs."""
        from backtest.pipeline import PipelineCapture, PipelineStageData

        capture = PipelineCapture(output_dir=str(tmp_path), ticker="AAPL")
        capture.start_session()

        n = 30
        psd1 = PipelineStageData(view_name="v0_日线", t=np.arange(n, dtype=float),
                                 filtered=np.ones(n), sig=np.zeros(n, dtype=int),
                                 v=np.ones(n) * 0.5, a=np.zeros(n),
                                 eps=np.full(n, 0.1), mu_v=np.zeros(n),
                                 sigma_v=np.ones(n) * 0.05)
        psd2 = PipelineStageData(view_name="v1_60分钟", t=np.arange(n, dtype=float),
                                 filtered=np.ones(n) * 2, sig=np.zeros(n, dtype=int),
                                 v=np.ones(n) * 0.3, a=np.zeros(n),
                                 eps=np.full(n, 0.08), mu_v=np.zeros(n),
                                 sigma_v=np.ones(n) * 0.04)
        capture.capture_step(0, "2026-01-15", {"v0_日线": psd1, "v1_60分钟": psd2})

        session_dirs = list(tmp_path.glob("AAPL_*"))
        step_dir = session_dirs[0] / "steps" / "000000"
        assert (step_dir / "v0_日线").is_dir()
        assert (step_dir / "v1_60分钟").is_dir()

    def test_start_session_no_ticker(self, tmp_path):
        """start_session without ticker still works."""
        from backtest.pipeline import PipelineCapture

        capture = PipelineCapture(output_dir=str(tmp_path))
        sid = capture.start_session()
        assert sid != ""
        # No ticker prefix in directory name
        session_dirs = list(tmp_path.glob("*"))
        assert len(session_dirs) > 0

    def test_index_json_only_written_once_per_step(self, tmp_path):
        """index.json is written only once even with multiple views."""
        from backtest.pipeline import PipelineCapture, PipelineStageData

        capture = PipelineCapture(output_dir=str(tmp_path), ticker="AAPL")
        capture.start_session()

        n = 10
        psd1 = PipelineStageData(view_name="v0", t=np.arange(n, dtype=float),
                                 filtered=np.ones(n), sig=np.zeros(n, dtype=int),
                                 v=np.ones(n), a=np.zeros(n), eps=np.full(n, 0.1),
                                 mu_v=np.zeros(n), sigma_v=np.ones(n) * 0.05)
        psd2 = PipelineStageData(view_name="v1", t=np.arange(n, dtype=float),
                                 filtered=np.ones(n) * 2, sig=np.zeros(n, dtype=int),
                                 v=np.ones(n), a=np.zeros(n), eps=np.full(n, 0.1),
                                 mu_v=np.zeros(n), sigma_v=np.ones(n) * 0.05)

        capture.capture_step(5, "2026-07-01", {"v0": psd1, "v1": psd2})

        session_dirs = list(tmp_path.glob("AAPL_*"))
        step_dir = session_dirs[0] / "steps" / "000005"
        idx = json.loads((step_dir / "index.json").read_text(encoding="utf-8"))
        assert idx["step_index"] == 5


# ============================================================================
# _write_view helper tests
# ============================================================================


class TestWriteView:
    """Tests for the module-level _write_view helper."""

    def test_write_view_all_none_skips_all(self, tmp_path):
        """When all stage fields are None, no files are written."""
        from backtest.pipeline import _write_view, PipelineStageData

        psd = PipelineStageData()
        view_dir = tmp_path / "v0"
        view_dir.mkdir()
        _write_view(view_dir, psd)

        files = list(view_dir.iterdir())
        assert len(files) == 0

    def test_write_view_filter_only(self, tmp_path):
        """Only filtered is set → only stage_03_filter written."""
        from backtest.pipeline import _write_view, PipelineStageData

        n = 10
        psd = PipelineStageData(t=np.arange(n, dtype=float),
                                filtered=np.ones(n))
        view_dir = tmp_path / "v0"
        view_dir.mkdir()
        _write_view(view_dir, psd)

        files = list(view_dir.iterdir())
        assert len(files) == 1
        assert files[0].name == "stage_03_filter.parquet"

    def test_write_view_with_filtered2(self, tmp_path):
        """filtered2 is included when not None."""
        from backtest.pipeline import _write_view, PipelineStageData

        n = 10
        psd = PipelineStageData(t=np.arange(n, dtype=float),
                                filtered=np.ones(n),
                                filtered2=np.ones(n) * 2)
        view_dir = tmp_path / "v0"
        view_dir.mkdir()
        _write_view(view_dir, psd)

        df = pd.read_parquet(view_dir / "stage_03_filter.parquet")
        assert "filtered2" in df.columns
        assert (df["filtered2"] == 2).all()

    def test_write_view_schmitt_all_fields(self, tmp_path):
        """Stage 04 schmitt includes all fields: sig, v, a, eps, mu_v, sigma_v."""
        from backtest.pipeline import _write_view, PipelineStageData

        n = 10
        psd = PipelineStageData(
            t=np.arange(n, dtype=float),
            sig=np.array([1, 0, -1] * 3 + [0], dtype=int)[:n],
            v=np.ones(n) * 0.5,
            a=np.zeros(n),
            eps=np.full(n, 0.1),
            mu_v=np.zeros(n),
            sigma_v=np.ones(n) * 0.05,
        )
        view_dir = tmp_path / "v0"
        view_dir.mkdir()
        _write_view(view_dir, psd)

        df = pd.read_parquet(view_dir / "stage_04_schmitt.parquet")
        assert "sig" in df.columns
        assert "v" in df.columns
        assert "a" in df.columns
        assert "eps" in df.columns
        assert "mu_v" in df.columns
        assert "sigma_v" in df.columns

    def test_write_view_schmitt_skipped_when_sig_none(self, tmp_path):
        """schmitt is skipped when sig is None, even if v/a are set."""
        from backtest.pipeline import _write_view, PipelineStageData

        psd = PipelineStageData(t=np.arange(10, dtype=float),
                                v=np.ones(10), a=np.zeros(10),
                                sig=None)  # sig is the gate key
        view_dir = tmp_path / "v0"
        view_dir.mkdir()
        _write_view(view_dir, psd)
        assert not (view_dir / "stage_04_schmitt.parquet").exists()

    def test_bs_markers_non_dict(self, tmp_path):
        """bs_markers that is not a dict uses empty defaults."""
        from backtest.pipeline import _write_view, PipelineStageData

        psd = PipelineStageData(bs_markers=[("entry", 0)])  # non-dict
        view_dir = tmp_path / "v0"
        view_dir.mkdir()
        _write_view(view_dir, psd)

        data = json.loads((view_dir / "stage_10_bs_markers.json").read_text())
        assert data["entry_markers"] == []
        assert data["exit_markers"] == []


# ============================================================================
# _write_parquet and _write_json helpers
# ============================================================================


class TestWriteHelpers:
    """Tests for _write_parquet and _write_json module-level helpers."""

    def test_write_parquet_roundtrip(self, tmp_path):
        """Data written with _write_parquet can be read back correctly."""
        from backtest.pipeline import _write_parquet

        cols = {"t": np.arange(5, dtype=float), "filtered": np.array([1.0, 2.0, 3.0, 4.0, 5.0])}
        path = tmp_path / "test.parquet"
        _write_parquet(path, cols)

        df = pd.read_parquet(path)
        assert list(df["t"]) == [0, 1, 2, 3, 4]
        assert list(df["filtered"]) == [1.0, 2.0, 3.0, 4.0, 5.0]

    def test_write_json_roundtrip(self, tmp_path):
        """Data written with _write_json can be read back correctly."""
        from backtest.pipeline import _write_json

        data = {"key": "value", "nested": {"a": 1, "b": [2, 3]}}
        path = tmp_path / "test.json"
        _write_json(path, data)

        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded == data


# ============================================================================
# Cleanup / isolation
# ============================================================================


class TestPipelineCaptureCleanup:
    """Verifies that multiple PipelineCapture instances don't interfere."""

    @pytest.fixture(autouse=True)
    def _ensure_enabled(self, monkeypatch):
        monkeypatch.setenv("PIPELINE_CAPTURE", "1")

    def test_second_session_creates_separate_dir(self, tmp_path):
        """Each start_session call creates a separate session directory."""
        from backtest.pipeline import PipelineCapture
        import time

        capture = PipelineCapture(output_dir=str(tmp_path), ticker="AAPL")
        sid1 = capture.start_session()
        assert sid1 != ""
        # Sleep to ensure the next timestamp is different
        time.sleep(1.1)
        sid2 = capture.start_session()
        assert sid2 != ""
        assert sid1 != sid2, (
            f"Expected different session IDs, got {sid1!r} and {sid2!r}"
        )

        session_dirs = list(tmp_path.glob("AAPL_*"))
        assert len(session_dirs) == 2, (
            f"Expected 2 session dirs, got {len(session_dirs)}: {session_dirs}"
        )

    def test_end_session_without_start_returns_empty(self, monkeypatch):
        """end_session without start_session returns empty dict."""
        monkeypatch.setenv("PIPELINE_CAPTURE", "1")
        from backtest.pipeline import PipelineCapture

        capture = PipelineCapture(ticker="AAPL")
        summary = capture.end_session()
        assert summary == {}
