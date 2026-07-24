"""
Concurrency safety tests for filter ThreadPoolExecutor usage patterns.

Covers patterns found in:
  - filter/services/backtest_core.py  (pipeline parallelism across views)
  - filter/services/data_loader.py    (parallel timeframe fetching)
"""

import threading
import time
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sample_df(n_rows=100):
    """Build a simple DataFrame usable as a shared read-only data source."""
    dates = pd.date_range("2024-01-01", periods=n_rows, freq="D")
    np.random.seed(42)
    return pd.DataFrame({
        "Open":  np.cumsum(np.random.randn(n_rows) * 0.5) + 100,
        "High":  np.cumsum(np.random.randn(n_rows) * 0.5) + 101,
        "Low":   np.cumsum(np.random.randn(n_rows) * 0.5) + 99,
        "Close": np.cumsum(np.random.randn(n_rows) * 0.5) + 100,
        "Volume": np.random.randint(1000, 10000, n_rows),
    }, index=dates)


# ---------------------------------------------------------------------------
# Test 1: Multi-threaded read from shared data source is safe
# ---------------------------------------------------------------------------

def test_concurrent_read_shared_dataframe():
    """Multiple threads reading the same DataFrame concurrently should not corrupt."""
    df = _make_sample_df(200)
    errors = []
    barrier = threading.Barrier(5, timeout=5)

    def read_worker(thread_id):
        try:
            barrier.wait()  # all threads start simultaneously
            for _ in range(20):
                # Perform read-only operations: slice, compute mean, access columns
                subset = df.iloc[thread_id * 10 : thread_id * 10 + 30]
                _ = subset["Close"].mean()
                _ = subset["Close"].iloc[-1]
                _ = df["Volume"].sum()
        except Exception as e:
            errors.append((thread_id, str(e)))

    threads = [threading.Thread(target=read_worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"Concurrent reads raised errors: {errors}"


# ---------------------------------------------------------------------------
# Test 2: Concurrent write detection via shared mutable container
# ---------------------------------------------------------------------------

def test_concurrent_write_race_detection():
    """Detect lost updates when threads increment a shared counter without locking.

    This test demonstrates the textbook race condition: multiple threads
    perform read-modify-write on a shared variable.  Without a lock, some
    increments will be lost, proving that unsynchronised access is unsafe.
    """
    # ---- Phase 1: unsynchronised (lost updates expected) ----
    unsafe_counter = [0]  # mutable container so threads can mutate via [0]

    def unsafe_increment(rounds):
        for _ in range(rounds):
            v = unsafe_counter[0]
            time.sleep(0.0005)  # widen the race window
            unsafe_counter[0] = v + 1

    N_THREADS = 6
    ROUNDS = 30
    threads = [threading.Thread(target=unsafe_increment, args=(ROUNDS,)) for _ in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    # Lost updates: final count will be less than total increments
    assert unsafe_counter[0] < N_THREADS * ROUNDS, (
        f"Unexpected: no lost updates detected with unsynchronised access "
        f"({unsafe_counter[0]} == {N_THREADS * ROUNDS}).  Timing may be too fast."
    )

    # ---- Phase 2: synchronised with a lock (no lost updates) ----
    safe_counter = [0]
    lock = threading.Lock()

    def safe_increment(rounds):
        for _ in range(rounds):
            with lock:
                v = safe_counter[0]
                time.sleep(0.0005)  # same delay, but held inside the lock
                safe_counter[0] = v + 1

    threads = [threading.Thread(target=safe_increment, args=(ROUNDS,)) for _ in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert safe_counter[0] == N_THREADS * ROUNDS, (
        f"Unexpected lost updates even with locking: "
        f"{safe_counter[0]} != {N_THREADS * ROUNDS}"
    )


# ---------------------------------------------------------------------------
# Test 3: ThreadPoolExecutor proper shutdown via context manager
# ---------------------------------------------------------------------------

def test_threadpool_context_manager_shutdown():
    """ThreadPoolExecutor context manager must shut down all threads cleanly."""
    active_counts = []
    lock = threading.Lock()

    def track_active():
        with lock:
            active = threading.active_count()
            active_counts.append(active)

    # Capture thread count before
    before = threading.active_count()

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(track_active) for _ in range(8)]
        for f in as_completed(futures):
            f.result(timeout=5)

    # Give threads a moment to fully terminate
    time.sleep(0.1)
    after = threading.active_count()

    # After context manager exit, thread count should return to roughly baseline
    assert after <= before + 2, (
        f"Thread count did not return to baseline: before={before}, after={after}"
    )


# ---------------------------------------------------------------------------
# Test 4: ThreadPoolExecutor + as_completed pattern (mirrors backtest_core / data_loader)
# ---------------------------------------------------------------------------

def test_threadpool_as_completed_pattern():
    """Simulate the parallel pipeline pattern: submit tasks, collect via as_completed."""
    def compute(view_id, data, fail=False):
        """Simulate _compute_pipeline_for_view / _fetch_one."""
        if fail:
            raise ValueError(f"view {view_id} compute error")
        # Simulate CPU-bound work
        time.sleep(0.01)
        return {"view_id": view_id, "mean": data["Close"].mean(), "last": data["Close"].iloc[-1]}

    df = _make_sample_df(100)
    views = [
        {"index": i, "tf": tf}
        for i, tf in enumerate(["日线", "周线", "月线", "60分钟", "15分钟"])
    ]
    # One view will fail — the pattern must handle it gracefully
    should_fail = {2}  # view index 2 fails

    results = {}
    errors = {}

    n_views = len(views)
    with ThreadPoolExecutor(max_workers=min(n_views, 4)) as executor:
        future_to_view = {}
        for v in views:
            f = executor.submit(compute, v["index"], df, fail=v["index"] in should_fail)
            future_to_view[f] = v

        for future in as_completed(future_to_view):
            v = future_to_view[future]
            try:
                results[v["index"]] = future.result()
            except Exception as e:
                errors[v["index"]] = str(e)

    # Successful views should produce results
    assert len(results) == n_views - len(should_fail), (
        f"Expected {n_views - len(should_fail)} successes, got {len(results)}"
    )
    # Failed views should be captured as errors
    assert len(errors) == len(should_fail), (
        f"Expected {len(should_fail)} errors, got {len(errors)}"
    )
    assert 2 in errors
    assert "compute error" in errors[2]


# ---------------------------------------------------------------------------
# Test 5: Thread safety of module-level cache (simulates _synth_cache_state)
# ---------------------------------------------------------------------------

def test_module_level_cache_thread_safety():
    """Concurrent access to a shared dict cache must not corrupt or lose entries."""
    cache = {}
    cache_lock = threading.Lock()
    errors = []

    def cache_worker(worker_id, rounds):
        for r in range(rounds):
            key = f"key_{worker_id}_{r}"
            # Simulate the read-check-write pattern from data_loader's _synth_cache_state
            with cache_lock:
                if key not in cache:
                    # Simulate expensive computation
                    time.sleep(0.001)
                    cache[key] = worker_id * 1000 + r
            # Read outside lock (simulating read path)
            with cache_lock:
                _ = cache.get(key)

    threads = [threading.Thread(target=cache_worker, args=(i, 10)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(cache) == 50, f"Expected 50 cache entries, got {len(cache)}"
    # Verify no corruption: each entry value should match key pattern
    for key, val in cache.items():
        parts = key.split("_")
        worker_id = int(parts[1])
        r = int(parts[2])
        assert val == worker_id * 1000 + r, (
            f"Cache corruption: {key} -> {val}, expected {worker_id * 1000 + r}"
        )


# ---------------------------------------------------------------------------
# Test 6: Error propagation through futures (as in backtest_core error handling)
# ---------------------------------------------------------------------------

def test_future_error_propagation():
    """Exceptions raised in submitted tasks must propagate to future.result()."""
    def raises_value_error(msg):
        raise ValueError(msg)

    def raises_runtime_error(msg):
        raise RuntimeError(msg)

    def succeeds():
        return "ok"

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(raises_value_error, "bad value"): "ve",
            executor.submit(raises_runtime_error, "bad runtime"): "re",
            executor.submit(succeeds): "ok",
        }

        outcomes = {}
        for future in as_completed(futures):
            tag = futures[future]
            try:
                outcomes[tag] = future.result()
            except ValueError as e:
                outcomes[tag] = f"ValueError: {e}"
            except RuntimeError as e:
                outcomes[tag] = f"RuntimeError: {e}"

    assert outcomes["ok"] == "ok"
    assert "bad value" in outcomes["ve"]
    assert "bad runtime" in outcomes["re"]


# ---------------------------------------------------------------------------
# Test 7: High-contention stress test with many short-lived tasks
# ---------------------------------------------------------------------------

def test_many_short_tasks_no_deadlock():
    """Submit many short-lived tasks to ensure no deadlock or starvation."""
    counter = 0
    lock = threading.Lock()

    def increment():
        nonlocal counter
        time.sleep(0.001)
        with lock:
            counter += 1
        return counter

    N_TASKS = 50
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(increment) for _ in range(N_TASKS)]
        results = []
        for f in as_completed(futures):
            results.append(f.result(timeout=5))

    assert len(results) == N_TASKS
    assert counter == N_TASKS, f"Counter mismatch: {counter} vs expected {N_TASKS}"
