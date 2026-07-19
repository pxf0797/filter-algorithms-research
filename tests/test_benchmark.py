"""
Performance regression canary — runs the benchmark with a small N and asserts
throughput is above a minimum threshold.

This test catches accidentally-introduced O(N^2) regressions, memory blow-ups,
or refactors that kill filter/schmitt/parquet performance.

All tests use synthetic data — no DB required.
"""

import sys
from pathlib import Path

import pytest

# ── Ensure tools/ is importable ──────────────────────────────────────────────
_TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

# Trigger lazy imports inside test functions so import errors surface clearly.


# ══════════════════════════════════════════════════════════════════════════════
# Thresholds (conservative — 10x higher than observed on a MacBook Pro M1)
# ══════════════════════════════════════════════════════════════════════════════

# SMA on 100 bars should complete in well under 500 us
FILTER_MAX_US = 500  # microseconds per call

# Schmitt trigger on 100 bars should complete in well under 2 ms
SCHMITT_MAX_US = 2000  # microseconds per call

# Parquet write of 100 rows should take well under 50 ms
PARQUET_WRITE_MAX_MS = 50  # milliseconds

# Parquet read of 100 rows should take well under 20 ms
PARQUET_READ_MAX_MS = 20  # milliseconds

# Event recording of 100 events should take well under 500 us per event
EVENT_RECORDING_MAX_US = 500  # microseconds per event


# ══════════════════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestFilterThroughput:
    """SMA filter throughput must stay above threshold."""

    def test_sma_filter_latency(self):
        from benchmark_pipeline import benchmark_filter

        result = benchmark_filter(n_bars=100)
        us = result["us_per_call"]
        assert us < FILTER_MAX_US, (
            f"SMA filter latency {us:.1f} us exceeds threshold {FILTER_MAX_US} us. "
            f"Possible performance regression."
        )


class TestSchmittThroughput:
    """Schmitt trigger throughput must stay above threshold."""

    def test_schmitt_latency(self):
        from benchmark_pipeline import benchmark_schmitt

        result = benchmark_schmitt(n_bars=100)
        us = result["us_per_call"]
        assert us < SCHMITT_MAX_US, (
            f"Schmitt trigger latency {us:.1f} us exceeds threshold {SCHMITT_MAX_US} us. "
            f"Possible performance regression."
        )


class TestParquetWrite:
    """Parquet write performance must stay above threshold."""

    def test_parquet_write_speed(self):
        from benchmark_pipeline import benchmark_parquet_write

        result = benchmark_parquet_write(n_rows=100)
        ms = result["write_sec"] * 1000
        mbps = result["write_mb_per_sec"]
        assert ms < PARQUET_WRITE_MAX_MS, (
            f"Parquet write time {ms:.2f} ms exceeds threshold {PARQUET_WRITE_MAX_MS} ms. "
            f"Throughput: {mbps:.2f} MB/s. Possible performance regression."
        )


class TestParquetRead:
    """Parquet read performance must stay above threshold."""

    def test_parquet_read_speed(self):
        from benchmark_pipeline import benchmark_parquet_read

        result = benchmark_parquet_read(n_rows=100)
        ms = result["read_ms"]
        assert ms < PARQUET_READ_MAX_MS, (
            f"Parquet read latency {ms:.2f} ms exceeds threshold {PARQUET_READ_MAX_MS} ms. "
            f"Possible performance regression."
        )


class TestEventRecording:
    """Event recording latency must stay above threshold."""

    def test_event_recording_latency(self):
        from benchmark_pipeline import benchmark_event_recording

        result = benchmark_event_recording(n_events=100)
        us = result["us_per_event"]
        assert us < EVENT_RECORDING_MAX_US, (
            f"Event recording latency {us:.1f} us exceeds threshold {EVENT_RECORDING_MAX_US} us. "
            f"Possible performance regression."
        )


class TestEndToEnd:
    """Basic sanity: benchmark runs end-to-end without error and returns valid data."""

    def test_full_run_returns_valid_results(self):
        from benchmark_pipeline import run_benchmarks

        results = run_benchmarks(n_bars=100)
        assert "meta" in results
        assert "stages" in results
        stages = results["stages"]
        # All synthetic stages must succeed
        assert "filter" in stages
        assert "schmitt" in stages
        assert "parquet_write" in stages
        assert "parquet_read" in stages
        assert "event_recording" in stages
        # Verfiy filter stage has expected structure
        assert stages["filter"]["us_per_call"] > 0
        assert stages["schmitt"]["us_per_call"] > 0
        assert stages["parquet_write"]["write_mb_per_sec"] > 0
        assert stages["parquet_read"]["read_ms"] > 0
        assert stages["event_recording"]["us_per_event"] > 0
