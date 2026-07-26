#!/usr/bin/env python3
"""
Pipeline benchmark — times each stage of the backtest pipeline.

Usage::

    python tools/benchmark_pipeline.py [--bars N] [--output json|text]

Measures:
1. BacktestRunner throughput (bars/sec)          — if DB + ticker available
2. Filter computation latency (us per call)       — pure math
3. Schmitt trigger latency (us per call)          — EWMA + state machine
4. Parquet write throughput (MB/s)               — ZSTD compressed
5. Parquet read latency (ms)                     — full table read
6. Event recording latency (us per event)         — JSONL append
7. End-to-end pipeline wall time                  — runner.run() if available

Uses synthetic/minimal config (AAPL daily, 1 view, SMA filter, small window).
If the DB is absent, stages 1 and 7 are skipped gracefully.
"""

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from loguru import logger

# ── Suppress noisy DEBUG logs during benchmarks ──────────────────────────────
logger.remove()
logger.add(sys.stderr, level="WARNING")

# ── Ensure filter is importable ──────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FILTER_APP = PROJECT_ROOT / "filter"
# Both are needed: PROJECT_ROOT for "import filter.backtest.*", and
# FILTER_APP for intra-package imports like "from db import ..." within services.
for _p in (str(PROJECT_ROOT), str(FILTER_APP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── Synthetic data generation ────────────────────────────────────────────────


def generate_random_walk(n_bars: int, start_price: float = 150.0, seed: int = 42) -> np.ndarray:
    """Generate a synthetic random-walk price series."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(0, 1.0, size=n_bars)
    prices = start_price + np.cumsum(returns)
    return np.maximum(prices, 1.0)  # floor at $1


# ── Minimal config ───────────────────────────────────────────────────────────


def make_minimal_config() -> list[dict]:
    """Return a single-view config (sma, 60-day window, daily timeframe)."""
    return [
        {
            "tf": "日线",
            "n_pts": 60,
            "_fid": "sma",
            "pv": {"window": 11},
            "_dual": False,
            "show_sch": True,
            "show_strategy": True,
            "show_pred": False,
            "ew": 60,
            "ke": 0.15,
            "sm": 0.05,
            "stop_loss_pct": 2.0,
            "n_ext": 10,
        }
    ]


# ── Benchmark helpers ────────────────────────────────────────────────────────


def timeit(n_iter: int, func, *args, **kwargs):
    """Time *func* over *n_iter* calls; return (total_sec, per_call_sec)."""
    t0 = time.perf_counter()
    for _ in range(n_iter):
        func(*args, **kwargs)
    elapsed = time.perf_counter() - t0
    return elapsed, elapsed / n_iter


def format_us(sec: float) -> str:
    return f"{sec * 1_000_000:.1f}"


def format_ms(sec: float) -> str:
    return f"{sec * 1_000:.2f}"


# ══════════════════════════════════════════════════════════════════════════════
# Stage 1: BacktestRunner throughput
# ══════════════════════════════════════════════════════════════════════════════

def benchmark_runner(n_bars: int) -> dict | None:
    """Benchmark BacktestRunner.run().  Returns None if DB/ticker unavailable."""
    try:
        from filter.backtest.engine import BacktestRunner
    except Exception:
        return {"error": "import failed", "skipped": True}

    configs = make_minimal_config()
    try:
        runner = BacktestRunner("AAPL", configs)
    except Exception as e:
        return {"error": str(e)[:120], "skipped": True}

    total_bars = runner.get_bar_count()
    # Start past the sparse early-data tail; n_pts=60 needs 60+ bars before cutoff.
    # Starting from bar 120 guarantees at least 60 bars of history for any ticker.
    start_bar = max(120, total_bars // 20)  # skip first 5% of bars
    end_bar = min(start_bar + n_bars, total_bars)
    if end_bar <= start_bar + 1:
        return {"error": f"not enough bars: total={total_bars}, start={start_bar}", "skipped": True}

    t0 = time.perf_counter()
    try:
        results = runner.run(start_bar, end_bar, step_interval=1)
    except Exception as e:
        return {"error": str(e)[:120], "skipped": True}
    elapsed = time.perf_counter() - t0

    return {
        "bars_processed": len(results),
        "total_sec": round(elapsed, 3),
        "bars_per_sec": round(len(results) / elapsed, 1) if elapsed > 0 else 0,
        "skipped": False,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Stage 2: Filter computation
# ══════════════════════════════════════════════════════════════════════════════

def benchmark_filter(n_bars: int) -> dict:
    """Benchmark raw filter (SMA) computation speed."""
    from filter.engine.filters import FILTERS

    noisy = generate_random_walk(n_bars)
    t_arr = np.arange(n_bars, dtype=float)
    sma_func = FILTERS["sma"]["func"]

    # Warm-up
    sma_func(noisy, t_arr, window=11)

    # Benchmark
    n_iter = max(100, 1000 // max(1, n_bars // 100))
    total_sec, per_call = timeit(n_iter, sma_func, noisy, t_arr, window=11)

    return {
        "filter": "sma",
        "signal_size": n_bars,
        "iterations": n_iter,
        "total_sec": round(total_sec, 4),
        "us_per_call": round(per_call * 1_000_000, 1),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Stage 3: Schmitt trigger
# ══════════════════════════════════════════════════════════════════════════════

def benchmark_schmitt(n_bars: int) -> dict:
    """Benchmark schmitt trigger computation speed."""
    from filter.engine.filters import FILTERS
    from filter.engine.schmitt import _schmitt_trigger

    noisy = generate_random_walk(n_bars)
    t_arr = np.arange(n_bars, dtype=float)
    sma_func = FILTERS["sma"]["func"]
    filtered = sma_func(noisy, t_arr, window=11)

    v = np.gradient(filtered, t_arr)
    a = np.gradient(v, t_arr)

    # Warm-up
    _schmitt_trigger(v, a, ewma_span=60, k_eps=0.15, sigma_min=0.05)

    # Benchmark
    n_iter = max(100, 500 // max(1, n_bars // 100))
    total_sec, per_call = timeit(
        n_iter, _schmitt_trigger, v, a,
        ewma_span=60, k_eps=0.15, sigma_min=0.05,
    )

    return {
        "signal_size": n_bars,
        "iterations": n_iter,
        "total_sec": round(total_sec, 4),
        "us_per_call": round(per_call * 1_000_000, 1),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Stage 4: Parquet write throughput
# ══════════════════════════════════════════════════════════════════════════════

def benchmark_parquet_write(n_rows: int) -> dict:
    """Benchmark ParquetStore write speed with synthetic data."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    # Build synthetic rows resembling pipeline output
    rng = np.random.default_rng(42)
    data = {
        "bar_index": np.arange(n_rows, dtype=np.int32),
        "bar_timestamp": np.array(
            [np.datetime64("2026-01-01", "ns") + np.timedelta64(i, "D") for i in range(n_rows)],
            dtype="datetime64[ns]",
        ),
        "v0_sig": rng.integers(-1, 2, size=n_rows, dtype=np.int8),
        "v0_filtered": rng.normal(150, 5, size=n_rows).astype(np.float32),
        "v0_eps": rng.normal(0, 0.1, size=n_rows).astype(np.float32),
        "v0_pnl_long": (100 + rng.normal(0, 2, size=n_rows)).astype(np.float32),
        "v0_pnl_short": (100 + rng.normal(0, 2, size=n_rows)).astype(np.float32),
        "v0_long_pos": rng.choice([True, False], size=n_rows),
        "v0_short_pos": rng.choice([True, False], size=n_rows),
    }
    table = pa.table(data)

    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
        tmp_path = f.name

    try:
        t0 = time.perf_counter()
        pq.write_table(table, tmp_path, compression="zstd", compression_level=3)
        elapsed = time.perf_counter() - t0

        file_size = os.path.getsize(tmp_path)
    finally:
        os.unlink(tmp_path)

    return {
        "rows": n_rows,
        "columns": len(data),
        "write_sec": round(elapsed, 4),
        "file_size_bytes": file_size,
        "write_mb_per_sec": round(file_size / elapsed / 1_048_576, 2) if elapsed > 0 else 0,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Stage 5: Parquet read latency
# ══════════════════════════════════════════════════════════════════════════════

def benchmark_parquet_read(n_rows: int) -> dict:
    """Benchmark Parquet read speed with synthetic data."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    rng = np.random.default_rng(42)
    data = {
        "bar_index": np.arange(n_rows, dtype=np.int32),
        "v0_sig": rng.integers(-1, 2, size=n_rows, dtype=np.int8),
        "v0_filtered": rng.normal(150, 5, size=n_rows).astype(np.float32),
        "v0_pnl_long": (100 + rng.normal(0, 2, size=n_rows)).astype(np.float32),
        "v0_pnl_short": (100 + rng.normal(0, 2, size=n_rows)).astype(np.float32),
    }
    table = pa.table(data)

    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
        tmp_path = f.name

    try:
        pq.write_table(table, tmp_path, compression="zstd", compression_level=3)
        file_size = os.path.getsize(tmp_path)

        # Warm-up
        pq.read_table(tmp_path)

        # Benchmark multiple reads
        n_iter = max(10, 100 // max(1, n_rows // 1000))
        t0 = time.perf_counter()
        for _ in range(n_iter):
            _ = pq.read_table(tmp_path)
        elapsed = time.perf_counter() - t0
    finally:
        os.unlink(tmp_path)

    return {
        "rows": n_rows,
        "file_size_bytes": file_size,
        "read_iterations": n_iter,
        "total_sec": round(elapsed, 4),
        "read_ms": round(elapsed / n_iter * 1_000, 2),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Stage 6: Event recording latency
# ══════════════════════════════════════════════════════════════════════════════

def benchmark_event_recording(n_events: int) -> dict:
    """Benchmark EventRecorder.record_step() latency."""
    from filter.backtest.recorder import EventRecorder
    from filter.engine.filters import FILTERS

    noisy = generate_random_walk(60)
    t_arr = np.arange(60, dtype=float)
    filtered = FILTERS["sma"]["func"](noisy, t_arr, window=11)

    # Build a realistic-looking pipeline output
    pipeline_output = {
        "bar_index": 0,
        "bar_timestamp": "2026-01-01",
        "cutoff_date": "2026-01-01",
        "views": {
            "v0_日线": {
                "t": t_arr,
                "dates": None,
                "noisy": noisy,
                "ohlc": None,
                "filtered": filtered,
                "filtered2": None,
                "schmitt": {
                    "sig": np.zeros(60, dtype=int),
                    "eps": np.zeros(60),
                    "mu_v": np.zeros(60),
                    "sigma_v": np.ones(60) * 0.1,
                    "v": np.gradient(filtered, t_arr),
                    "a": np.gradient(np.gradient(filtered, t_arr), t_arr),
                    "final_mu": 0.0,
                    "final_sigma": 0.1,
                    "final_state": 0,
                    "final_dur": 0,
                },
                "all_pairs": [],
                "prediction_pairs": [],
                "long_pnl": np.full(60, 100.0),
                "short_pnl": np.full(60, 100.0),
                "trade_records": [],
                "long_mask": np.zeros(60, dtype=bool),
                "short_mask": np.zeros(60, dtype=bool),
                "bs_markers": {"entry_markers": [], "exit_markers": []},
            },
        },
        "ohlcv": {"close": 150.0, "open": 149.0, "high": 151.0, "low": 148.0, "volume": 1000000.0},
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        recorder = EventRecorder(output_dir=tmpdir, ticker="AAPL")
        recorder.start_session(config={"configs": make_minimal_config()})

        # Warm-up
        recorder.record_step(0, "2026-01-01", pipeline_output)

        # Benchmark
        t0 = time.perf_counter()
        for i in range(n_events):
            recorder.record_step(i, "2026-01-01", pipeline_output)
        elapsed = time.perf_counter() - t0

        recorder.end_session()

    return {
        "events_recorded": n_events,
        "total_sec": round(elapsed, 4),
        "us_per_event": round(elapsed / n_events * 1_000_000, 1) if n_events > 0 else 0,
    }


# ══════════════════════════════════════════════════════════════════════════════
# E2E: Full pipeline
# ══════════════════════════════════════════════════════════════════════════════

def benchmark_e2e(n_bars: int) -> dict | None:
    """Time the full BacktestRunner.run() wall-clock.  Returns None if unavailable."""
    return benchmark_runner(n_bars)


# ══════════════════════════════════════════════════════════════════════════════
# Report formatting
# ══════════════════════════════════════════════════════════════════════════════

def print_text_report(results: dict) -> None:
    """Print human-readable benchmark report."""
    meta = results.get("meta", {})
    print("=" * 60)
    print("  Pipeline Benchmark Report")
    print("=" * 60)
    print(f"  Bars (synthetic):   {meta.get('n_bars', 'N/A')}")
    print(f"  Config:             {meta.get('config', 'N/A')}")
    print(f"  Timestamp:          {meta.get('timestamp', 'N/A')}")
    print(f"  Python:             {meta.get('python_version', 'N/A')}")
    print("-" * 60)

    stages = results.get("stages", {})

    # 1. BacktestRunner
    r = stages.get("backtest_runner")
    if r and not r.get("skipped"):
        print(f"\n  [1] BacktestRunner throughput")
        print(f"      Bars processed:     {r['bars_processed']}")
        print(f"      Wall time:          {r['total_sec']:.3f} s")
        print(f"      Throughput:         {r['bars_per_sec']:.1f} bars/sec")
    else:
        err = (r or {}).get("error", "DB unavailable")
        print(f"\n  [1] BacktestRunner — SKIPPED ({err})")

    # 2. Filter
    f = stages.get("filter")
    if f:
        print(f"\n  [2] Filter computation (SMA, window=11)")
        print(f"      Signal size:        {f['signal_size']}")
        print(f"      Iterations:         {f['iterations']}")
        print(f"      Latency:            {f['us_per_call']:.1f} us/call")

    # 3. Schmitt
    s = stages.get("schmitt")
    if s:
        print(f"\n  [3] Schmitt trigger")
        print(f"      Signal size:        {s['signal_size']}")
        print(f"      Iterations:         {s['iterations']}")
        print(f"      Latency:            {s['us_per_call']:.1f} us/call")

    # 4. Parquet write
    pw = stages.get("parquet_write")
    if pw:
        print(f"\n  [4] Parquet write")
        print(f"      Rows:               {pw['rows']}")
        print(f"      Columns:            {pw['columns']}")
        print(f"      File size:          {pw['file_size_bytes']:,} bytes")
        print(f"      Write time:         {pw['write_sec']:.4f} s")
        print(f"      Throughput:         {pw['write_mb_per_sec']:.2f} MB/s")

    # 5. Parquet read
    pr = stages.get("parquet_read")
    if pr:
        print(f"\n  [5] Parquet read")
        print(f"      Rows:               {pr['rows']}")
        print(f"      File size:          {pr['file_size_bytes']:,} bytes")
        print(f"      Read latency:       {pr['read_ms']:.2f} ms")

    # 6. Event recording
    er = stages.get("event_recording")
    if er:
        print(f"\n  [6] Event recording (JSONL)")
        print(f"      Events recorded:    {er['events_recorded']}")
        print(f"      Total time:         {er['total_sec']:.4f} s")
        print(f"      Latency:            {er['us_per_event']:.1f} us/event")

    # 7. E2E
    e2e = stages.get("e2e")
    if e2e and not e2e.get("skipped"):
        print(f"\n  [7] End-to-end pipeline")
        print(f"      Wall time:          {e2e['total_sec']:.3f} s")
        print(f"      Throughput:         {e2e['bars_per_sec']:.1f} bars/sec")

    print("\n" + "=" * 60)


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def run_benchmarks(n_bars: int) -> dict:
    """Run all benchmark stages and return results dict."""
    results: dict = {
        "meta": {
            "n_bars": n_bars,
            "config": "AAPL, 1 view, SMA(11), daily, 60-pt window",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "python_version": sys.version.split()[0],
        },
        "stages": {},
    }

    stages = results["stages"]

    print("Running benchmarks...", flush=True)

    # Stage 1
    print("  [1/7] BacktestRunner ...", end=" ", flush=True)
    stages["backtest_runner"] = benchmark_runner(n_bars)
    print("OK" if not stages["backtest_runner"].get("skipped") else "SKIPPED", flush=True)

    # Stage 2
    print("  [2/7] Filter computation ...", end=" ", flush=True)
    stages["filter"] = benchmark_filter(n_bars)
    print("OK", flush=True)

    # Stage 3
    print("  [3/7] Schmitt trigger ...", end=" ", flush=True)
    stages["schmitt"] = benchmark_schmitt(n_bars)
    print("OK", flush=True)

    # Stage 4
    print("  [4/7] Parquet write ...", end=" ", flush=True)
    stages["parquet_write"] = benchmark_parquet_write(n_bars)
    print("OK", flush=True)

    # Stage 5
    print("  [5/7] Parquet read ...", end=" ", flush=True)
    stages["parquet_read"] = benchmark_parquet_read(n_bars)
    print("OK", flush=True)

    # Stage 6
    print("  [6/7] Event recording ...", end=" ", flush=True)
    stages["event_recording"] = benchmark_event_recording(n_bars)
    print("OK", flush=True)

    # Stage 7
    print("  [7/7] End-to-end ...", end=" ", flush=True)
    stages["e2e"] = benchmark_e2e(n_bars)
    print("OK" if not stages["e2e"].get("skipped") else "SKIPPED", flush=True)

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark the backtest pipeline stages",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--bars", type=int, default=500,
        help="Number of synthetic bars / bar steps (default: 500)",
    )
    parser.add_argument(
        "--output", choices=["json", "text"], default="text",
        help="Output format (default: text)",
    )
    args = parser.parse_args()

    results = run_benchmarks(args.bars)

    if args.output == "json":
        print(json.dumps(results, indent=2, default=str, ensure_ascii=False))
    else:
        print_text_report(results)


if __name__ == "__main__":
    main()
