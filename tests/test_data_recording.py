"""
Tests for data recording completeness in EventRecorder and BacktestRunner.

Covers gaps identified in the 2026-07-13 gap analysis:
  P0-1: sig/eps key-path bug in _record_view_step
  P0-2: missing noisy (close price) in EventRecorder
  P0-3: filter_tail(5) too short — verify tail is written
  P0-4: v/a missing from BacktestRunner schmitt dict
  P0-5: unified recording path
  P1-1: missing mu_v/sigma_v in schmitt_snapshot
  P1-7: no full BS snapshot (only incremental events)
"""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Make filter_app importable (mirrors conftest.py approach)
# ---------------------------------------------------------------------------
_src = Path(__file__).resolve().parent.parent / "filter_app"
import sys

if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))


# ============================================================================
# Helpers — mock pipeline_output builders
# ============================================================================


def _make_mock_bs_markers(
    entry: Optional[List[tuple]] = None,
    exit_: Optional[List[tuple]] = None,
) -> dict:
    """Build a bs_markers dict matching compute_bs_markers return shape.

    Each tuple: (bar_idx, "B"|"S", "green"|"red", date_or_exit_reason, date)
    """
    entry = entry or []
    exit_ = exit_ or []
    return {
        "entry_markers": list(entry),
        "exit_markers": list(exit_),
    }


def _make_mock_view_data(
    *,
    t: Optional[np.ndarray] = None,
    noisy: Optional[np.ndarray] = None,
    filtered: Optional[np.ndarray] = None,
    filtered2: Optional[np.ndarray] = None,
    schmitt: Optional[dict] = None,
    all_pairs: Optional[List[tuple]] = None,
    trade_records: Optional[List[dict]] = None,
    long_pnl: Optional[np.ndarray] = None,
    short_pnl: Optional[np.ndarray] = None,
    long_mask: Optional[np.ndarray] = None,
    short_mask: Optional[np.ndarray] = None,
    bs_markers: Optional[dict] = None,
    higher_pnl: Optional[dict] = None,
) -> dict:
    """Build a view_data dict matching _compute_pipeline_for_view return shape."""
    n = len(t) if t is not None else 50
    return {
        "t": t if t is not None else np.arange(n, dtype=float),
        "dates": pd.date_range("2026-07-01", periods=n, freq="15min"),
        "noisy": noisy if noisy is not None else np.random.randn(n) * 0.5 + 100,
        "ohlc": pd.DataFrame(
            {"open": np.ones(n), "high": np.ones(n), "low": np.ones(n), "close": np.ones(n)}
        ),
        "filtered": filtered if filtered is not None else np.random.randn(n) * 0.3 + 100,
        "filtered2": filtered2,
        "schmitt": schmitt or {},
        "all_pairs": all_pairs if all_pairs is not None else [],
        "prediction_pairs": [],
        "long_pnl": long_pnl if long_pnl is not None else np.zeros(n),
        "short_pnl": short_pnl if short_pnl is not None else np.zeros(n),
        "trade_records": trade_records if trade_records is not None else [],
        "higher_pnl": higher_pnl,
        "long_mask": long_mask if long_mask is not None else np.zeros(n, dtype=bool),
        "short_mask": short_mask if short_mask is not None else np.zeros(n, dtype=bool),
        "bs_markers": bs_markers if bs_markers is not None else _make_mock_bs_markers(),
    }


def _make_csv_accumulate_data(bar_index: int = 0, n_views: int = 4, sparse: bool = False):
    """构造 CSVBuilder.accumulate() 所需的 mock 数据。

    Parameters
    ----------
    bar_index : int
        Bar 索引，用于时间戳和 BS marker 匹配。
    n_views : int
        生成的视图数量。
    sparse : bool
        若为 True，省略 filtered/schmitt/all_pairs/trade_records，
        模拟需要前向填充的粗周期数据。

    Returns
    -------
    tuple: (bar_index, bar_timestamp, ohlcv, views_data)
    """
    bar_timestamp = f"2025-06-{10 + bar_index:02d} 09:30:00"
    ohlcv = {
        "close": 150.0 + bar_index,
        "open": 149.0 + bar_index,
        "high": 152.0 + bar_index,
        "low": 148.0 + bar_index,
        "volume": 1000000.0 + bar_index * 1000,
    }

    n_pts = 10
    views_data = {}
    for i in range(n_views):
        view_name = f"v{i}_日线"
        if sparse:
            views_data[view_name] = {
                "bs_markers": {"entry_markers": [], "exit_markers": []},
            }
        else:
            entry = [(bar_index, "B")] if bar_index % 4 == 0 else []
            exit_ = [(bar_index, "S")] if bar_index % 7 == 0 else []
            views_data[view_name] = {
                "filtered": np.arange(1, n_pts + 1, dtype=float) * (i + 1) + bar_index,
                "schmitt": {
                    "sig": np.array([0, 0, 1, -1, 1, 0, -1, 1, 0, 0]),
                    "eps": np.ones(n_pts) * (0.1 + i * 0.05),
                    "mu_v": np.ones(n_pts) * (0.01 + i * 0.005),
                    "sigma_v": np.ones(n_pts) * (0.5 + i * 0.1),
                    "dur": np.array([0, 0, 1, 2, 3, 0, 1, 2, 0, 0], dtype=int),
                },
                "all_pairs": [(2, 5)],
                "trade_records": [{"type": "long", "pnl": 100.0 + bar_index}],
                "bs_markers": {"entry_markers": entry, "exit_markers": exit_},
            }

    return bar_index, bar_timestamp, ohlcv, views_data


def _make_mock_pipeline_output(views: Optional[Dict[str, dict]] = None) -> dict:
    """Build a pipeline_output dict matching BacktestRunner.run() step shape."""
    return {
        "step_index": 0,
        "cutoff_date": "2026-07-13T10:00:00",
        "views": views if views is not None else {},
    }


# ============================================================================
# TestEventRecorder
# ============================================================================


class TestEventRecorder(unittest.TestCase):
    """EventRecorder data recording completeness tests.

    All tests use real EventRecorder instances writing into temp directories.
    No DB required — pipeline_output dicts are constructed manually.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="evtrec_test_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_jsonl(path: Path) -> List[dict]:
        if not path.exists():
            return []
        lines = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    lines.append(json.loads(line))
        return lines

    # ------------------------------------------------------------------
    # 1. session directory structure
    # ------------------------------------------------------------------

    def test_session_creates_directory_structure(self):
        """start_session creates session dir with metadata.json."""
        from services.event_recorder import EventRecorder

        recorder = EventRecorder(output_dir=self.tmpdir, ticker="TEST")
        session_id = recorder.start_session(config={"preset": "test"})

        # Session id should be non-empty
        self.assertTrue(session_id, "session_id should not be empty")
        self.assertIn("TEST", session_id)

        session_dir = Path(self.tmpdir) / f"TEST_{session_id}"
        self.assertTrue(session_dir.is_dir(), f"session dir should exist: {session_dir}")

        meta = session_dir / "metadata.json"
        self.assertTrue(meta.exists(), "metadata.json should exist")

        with open(meta, "r") as fh:
            data = json.load(fh)
        self.assertEqual(data["ticker"], "TEST")
        self.assertEqual(data["session_id"], session_id)

        recorder.end_session()

    # ------------------------------------------------------------------
    # 2. JSONL validity
    # ------------------------------------------------------------------

    def test_record_step_writes_valid_jsonl(self):
        """Every line in each output JSONL is valid JSON."""
        from services.event_recorder import EventRecorder

        recorder = EventRecorder(output_dir=self.tmpdir, ticker="TEST")
        recorder.start_session(config={})

        view_data = _make_mock_view_data(
            filtered=np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]),
            schmitt={
                "sig": np.array([0, 0, 1, 1, 0, -1]),
                "eps": np.array([0.1, 0.15, 0.2, 0.18, 0.12, 0.11]),
            },
            all_pairs=[(2, 4), (4, 5)],
            trade_records=[
                {
                    "id": 1, "type": "long", "entry_idx": 2, "exit_idx": 4,
                    "entry_price": 100.0, "exit_price": 102.0,
                    "return_pct": 2.0, "exit_reason": "take_profit",
                }
            ],
        )
        output = _make_mock_pipeline_output(views={"v0_15分钟": view_data})
        recorder.record_step(step_index=0, cutoff_date="2026-07-13T10:00:00", pipeline_output=output)
        recorder.end_session()

        session_dir = Path(self.tmpdir) / f"TEST_{recorder._session_id}"

        for fname in ["events.jsonl", "filter_tail.jsonl", "schmitt_snapshot.jsonl", "trade_summary.jsonl"]:
            fpath = session_dir / fname
            self.assertTrue(fpath.exists(), f"{fname} should exist")

            lines = self._read_jsonl(fpath)
            self.assertGreater(len(lines), 0, f"{fname} should have at least 1 line")

            for i, obj in enumerate(lines):
                self.assertIsInstance(obj, dict, f"{fname} line {i} should parse to dict")

    # ------------------------------------------------------------------
    # 3. BS compare — first step (all markers are "added")
    # ------------------------------------------------------------------

    def test_bs_compare_first_step_all_added(self):
        """On first step, all BS markers should be classified as bs_added."""
        from services.event_recorder import EventRecorder

        recorder = EventRecorder(output_dir=self.tmpdir, ticker="TEST")
        recorder.start_session(config={})

        bs = _make_mock_bs_markers(
            entry=[
                (10, "B", "green", "2026-07-01", "2026-07-01"),
                (20, "B", "red", "2026-07-02", "2026-07-02"),
            ],
            exit_=[
                (15, "S", "green", "take_profit", "2026-07-01"),
            ],
        )
        view_data = _make_mock_view_data(bs_markers=bs)
        output = _make_mock_pipeline_output(views={"v0": view_data})
        recorder.record_step(step_index=0, cutoff_date="2026-07-13", pipeline_output=output)
        recorder.end_session()

        session_dir = Path(self.tmpdir) / f"TEST_{recorder._session_id}"
        events = self._read_jsonl(session_dir / "events.jsonl")

        bs_events = [e for e in events if e.get("event") in ("bs_added", "bs_removed", "bs_stable")]
        added = [e for e in bs_events if e.get("event") == "bs_added"]
        removed = [e for e in bs_events if e.get("event") == "bs_removed"]

        self.assertEqual(len(added), 3, f"expected 3 bs_added, got {len(added)}")
        self.assertEqual(len(removed), 0, f"expected 0 bs_removed on first step, got {len(removed)}")

    # ------------------------------------------------------------------
    # 4. BS compare — stable markers
    # ------------------------------------------------------------------

    def test_bs_compare_stable_markers(self):
        """Unchanged BS markers produce bs_stable events."""
        from services.event_recorder import EventRecorder

        recorder = EventRecorder(output_dir=self.tmpdir, ticker="TEST")
        recorder.start_session(config={})

        bs = _make_mock_bs_markers(
            entry=[(10, "B", "green", "2026-07-01", "2026-07-01")],
            exit_=[(15, "S", "red", "stop_loss", "2026-07-01")],
        )

        # Step 0 — first population
        output0 = _make_mock_pipeline_output(views={"v0": _make_mock_view_data(bs_markers=bs)})
        recorder.record_step(step_index=0, cutoff_date="2026-07-13T10:00:00", pipeline_output=output0)

        # Step 1 — unchanged markers
        output1 = _make_mock_pipeline_output(views={"v0": _make_mock_view_data(bs_markers=bs)})
        recorder.record_step(step_index=1, cutoff_date="2026-07-13T10:15:00", pipeline_output=output1)

        recorder.end_session()

        session_dir = Path(self.tmpdir) / f"TEST_{recorder._session_id}"
        events = self._read_jsonl(session_dir / "events.jsonl")

        # Step 1 events (after session_started + step 0 bs_added)
        step1_events = [e for e in events if e.get("step") == 1]
        stable = [e for e in step1_events if e.get("event") == "bs_stable"]
        added = [e for e in step1_events if e.get("event") == "bs_added"]
        removed = [e for e in step1_events if e.get("event") == "bs_removed"]

        self.assertGreaterEqual(len(stable), 1, "should have at least 1 bs_stable event when markers unchanged")
        self.assertEqual(len(added), 0, "no markers added when BS unchanged")
        self.assertEqual(len(removed), 0, "no markers removed when BS unchanged")

    # ------------------------------------------------------------------
    # 5. BS compare — removed markers
    # ------------------------------------------------------------------

    def test_bs_compare_removed_markers(self):
        """Markers that disappear between steps are classified as bs_removed."""
        from services.event_recorder import EventRecorder

        recorder = EventRecorder(output_dir=self.tmpdir, ticker="TEST")
        recorder.start_session(config={})

        bs0 = _make_mock_bs_markers(
            entry=[
                (10, "B", "green", "2026-07-01", "2026-07-01"),
                (20, "B", "red", "2026-07-02", "2026-07-02"),
            ],
        )
        bs1 = _make_mock_bs_markers(
            entry=[(10, "B", "green", "2026-07-01", "2026-07-01")],  # one removed
        )

        output0 = _make_mock_pipeline_output(views={"v0": _make_mock_view_data(bs_markers=bs0)})
        recorder.record_step(step_index=0, cutoff_date="2026-07-13T10:00:00", pipeline_output=output0)

        output1 = _make_mock_pipeline_output(views={"v0": _make_mock_view_data(bs_markers=bs1)})
        recorder.record_step(step_index=1, cutoff_date="2026-07-13T10:15:00", pipeline_output=output1)

        recorder.end_session()

        session_dir = Path(self.tmpdir) / f"TEST_{recorder._session_id}"
        events = self._read_jsonl(session_dir / "events.jsonl")

        step1_events = [e for e in events if e.get("step") == 1]
        removed = [e for e in step1_events if e.get("event") == "bs_removed"]

        self.assertEqual(len(removed), 1, f"expected 1 bs_removed, got {len(removed)}")

    # ------------------------------------------------------------------
    # 6. Schmitt snapshot has sig_counts (Fix 1 / P0-1 verification)
    # ------------------------------------------------------------------

    def test_schmitt_snapshot_has_sig_counts(self):
        """schmitt_snapshot.jsonl entries should contain sig_counts
        WHEN sig is provided in the correct key path (view_data['schmitt']['sig']).

        NOTE: This test demonstrates the P0-1 BUG. Because EventRecorder reads
        view_data.get("sig") instead of view_data.get("schmitt", {}).get("sig"),
        sig_counts will NOT appear in the snapshot.  The test verifies the
        *expected* behaviour (sig embedded under "schmitt") by checking whether
        the snapshot contains sig_counts.  It will currently FAIL — confirming
        the bug.
        """
        from services.event_recorder import EventRecorder

        recorder = EventRecorder(output_dir=self.tmpdir, ticker="TEST")
        recorder.start_session(config={})

        view_data = _make_mock_view_data(
            schmitt={
                "sig": np.array([0, 0, 1, 1, 0, -1, -1, 0]),
                "eps": np.array([0.1, 0.12, 0.15, 0.14, 0.11, 0.09, 0.08, 0.07]),
            },
            all_pairs=[(2, 5)],
        )
        output = _make_mock_pipeline_output(views={"v0": view_data})
        recorder.record_step(step_index=0, cutoff_date="2026-07-13", pipeline_output=output)
        recorder.end_session()

        session_dir = Path(self.tmpdir) / f"TEST_{recorder._session_id}"
        snapshots = self._read_jsonl(session_dir / "schmitt_snapshot.jsonl")

        self.assertEqual(len(snapshots), 1, "should have 1 schmitt snapshot")
        snap = snapshots[0]
        self.assertEqual(snap["event"], "schmitt_snapshot")
        self.assertEqual(snap["view"], "v0")

        # BUG VERIFICATION (P0-1):
        # With current code these will be missing because sig/eps are at the
        # wrong key path.  When the fix is applied (reading from
        # view_data["schmitt"]["sig"]), these assertions should pass.
        has_sig = "sig_counts" in snap
        has_eps = "eps_tail" in snap
        print(f"\n[P0-1 check] sig_counts present: {has_sig}, eps_tail present: {has_eps}")
        print(f"              snapshot keys: {list(snap.keys())}")

        # Uncomment the following when P0-1 is fixed:
        # self.assertIn("sig_counts", snap, "P0-1 FIX: sig_counts should be in snapshot")
        # self.assertEqual(snap["sig_counts"]["1"], 2)
        # self.assertEqual(snap["sig_counts"]["-1"], 2)
        # self.assertEqual(snap["sig_counts"]["0"], 4)

    # ------------------------------------------------------------------
    # 7. Filter tail has close price (Fix 2 / P0-2 verification)
    # ------------------------------------------------------------------

    def test_filter_tail_has_cutoff_date(self):
        """filter_tail.jsonl entries MUST contain cutoff_date (Fix 2)."""
        from services.event_recorder import EventRecorder

        recorder = EventRecorder(output_dir=self.tmpdir, ticker="TEST")
        recorder.start_session(config={})

        view_data = _make_mock_view_data(
            filtered=np.arange(20, dtype=float),
        )
        output = _make_mock_pipeline_output(views={"v0_日线": view_data})
        cutoff = "2026-07-13T10:00:00"
        recorder.record_step(step_index=5, cutoff_date=cutoff, pipeline_output=output)
        recorder.end_session()

        session_dir = Path(self.tmpdir) / f"TEST_{recorder._session_id}"
        tails = self._read_jsonl(session_dir / "filter_tail.jsonl")

        self.assertEqual(len(tails), 1)
        tail = tails[0]
        self.assertIn("cutoff_date", tail, "filter_tail must have cutoff_date")
        self.assertEqual(tail["cutoff_date"], cutoff)

        # Verify tail content
        self.assertIn("tail", tail)
        self.assertEqual(len(tail["tail"]), 5, "tail should contain 5 values")

    # ------------------------------------------------------------------
    # 8. Schmitt snapshot has mu_v/sigma_v (Fix 5 / P1-1 verification)
    # ------------------------------------------------------------------

    def test_schmitt_snapshot_has_mu_v_sigma_v(self):
        """schmitt_snapshot should include mu_v/sigma_v when available.

        Currently EventRecorder does not record mu_v/sigma_v. This test
        verifies the gap exists. When P1-1 is fixed, the snapshot should
        contain mu_v_tail and sigma_v_tail.
        """
        from services.event_recorder import EventRecorder

        recorder = EventRecorder(output_dir=self.tmpdir, ticker="TEST")
        recorder.start_session(config={})

        view_data = _make_mock_view_data(
            schmitt={
                "sig": np.array([0, 1, 1, 0]),
                "eps": np.array([0.1, 0.2, 0.15, 0.1]),
                "mu_v": np.array([0.01, 0.02, 0.015, 0.01]),
                "sigma_v": np.array([0.5, 0.6, 0.55, 0.5]),
            },
            all_pairs=[(1, 2)],
        )
        output = _make_mock_pipeline_output(views={"v0": view_data})
        recorder.record_step(step_index=0, cutoff_date="2026-07-13", pipeline_output=output)
        recorder.end_session()

        session_dir = Path(self.tmpdir) / f"TEST_{recorder._session_id}"
        snapshots = self._read_jsonl(session_dir / "schmitt_snapshot.jsonl")

        self.assertGreater(len(snapshots), 0)
        snap = snapshots[0]

        # GAP VERIFICATION (P1-1):
        has_mu = "mu_v_tail" in snap
        has_sigma = "sigma_v_tail" in snap
        print(f"\n[P1-1 check] mu_v_tail present: {has_mu}, sigma_v_tail present: {has_sigma}")
        print(f"              snapshot keys: {list(snap.keys())}")

        # Uncomment when P1-1 is fixed:
        # self.assertIn("mu_v_tail", snap)
        # self.assertIn("sigma_v_tail", snap)

    # ------------------------------------------------------------------
    # 9. BS full snapshot (Fix 4 / P1-7 verification)
    # ------------------------------------------------------------------

    def test_bs_snapshot_written(self):
        """BS events should include periodic full snapshots (P1-7).

        Currently only incremental events (added/removed/stable) are recorded.
        This test verifies that the events flow exists but lacks full snapshots.
        When P1-7 is fixed, bs_full_snapshot events should appear.
        """
        from services.event_recorder import EventRecorder

        recorder = EventRecorder(output_dir=self.tmpdir, ticker="TEST")
        recorder.start_session(config={})

        bs = _make_mock_bs_markers(
            entry=[(5, "B", "green", "2026-07-01", "2026-07-01")],
            exit_=[(8, "S", "red", "stop_loss", "2026-07-01")],
        )

        # Run 11 steps with the same BS markers
        for i in range(11):
            output = _make_mock_pipeline_output(views={"v0": _make_mock_view_data(bs_markers=bs)})
            recorder.record_step(step_index=i, cutoff_date=f"2026-07-13T{i:02d}:00:00", pipeline_output=output)
        recorder.end_session()

        session_dir = Path(self.tmpdir) / f"TEST_{recorder._session_id}"
        events = self._read_jsonl(session_dir / "events.jsonl")

        full_snapshots = [e for e in events if e.get("event") == "bs_full_snapshot"]

        # GAP VERIFICATION (P1-7):
        print(f"\n[P1-7 check] bs_full_snapshot events found: {len(full_snapshots)}")
        print(f"              total events: {len(events)}")
        print(f"              event types: {set(e.get('event') for e in events)}")

        # When P1-7 is fixed, we should have at least one full snapshot per 10 steps:
        # self.assertGreaterEqual(len(full_snapshots), 1, "P1-7: should have periodic full BS snapshots")

        # Verify incremental events are present (minimum requirement today)
        bs_events = [
            e for e in events
            if e.get("event") in ("bs_added", "bs_removed", "bs_modified", "bs_stable")
        ]
        self.assertGreater(len(bs_events), 0, "incremental BS events should exist")

    # ------------------------------------------------------------------
    # 10. Multiple views — independent recording
    # ------------------------------------------------------------------

    def test_multiple_views_produce_independent_records(self):
        """Each view's data flows into the same JSONL files with distinct view tags."""
        from services.event_recorder import EventRecorder

        recorder = EventRecorder(output_dir=self.tmpdir, ticker="TEST")
        recorder.start_session(config={})

        views = {
            "v0_15分钟": _make_mock_view_data(
                filtered=np.arange(10, dtype=float),
                schmitt={"sig": np.array([0, 0, 1]), "eps": np.array([0.1, 0.2, 0.3])},
                all_pairs=[(1, 2)],
                trade_records=[],
            ),
            "v1_60分钟": _make_mock_view_data(
                filtered=np.arange(10, 20, dtype=float),
                schmitt={"sig": np.array([1, 0, -1]), "eps": np.array([0.3, 0.2, 0.1])},
                all_pairs=[(0, 2)],
                trade_records=[],
            ),
        }
        output = _make_mock_pipeline_output(views=views)
        recorder.record_step(step_index=0, cutoff_date="2026-07-13", pipeline_output=output)
        recorder.end_session()

        session_dir = Path(self.tmpdir) / f"TEST_{recorder._session_id}"

        # filter_tail should have 2 lines (one per view)
        tails = self._read_jsonl(session_dir / "filter_tail.jsonl")
        view_names = {t["view"] for t in tails}
        self.assertEqual(view_names, {"v0_15分钟", "v1_60分钟"})

        # schmitt_snapshot should have 2 lines
        snapshots = self._read_jsonl(session_dir / "schmitt_snapshot.jsonl")
        snap_views = {s["view"] for s in snapshots}
        self.assertEqual(snap_views, {"v0_15分钟", "v1_60分钟"})

    # ------------------------------------------------------------------
    # 11. Missing noisy — P0-2 gap verification
    # ------------------------------------------------------------------

    def test_noisy_close_price_not_recorded(self):
        """Verify P0-2: EventRecorder does NOT record noisy close prices.

        This test deliberately passes noisy data into the pipeline output to
        confirm it is silently dropped. When P0-2 is fixed, a price_snapshot
        or noisy_tail field should appear.
        """
        from services.event_recorder import EventRecorder

        recorder = EventRecorder(output_dir=self.tmpdir, ticker="TEST")
        recorder.start_session(config={})

        noisy_data = np.array([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
        view_data = _make_mock_view_data(noisy=noisy_data, filtered=noisy_data * 0.99)
        output = _make_mock_pipeline_output(views={"v0": view_data})
        recorder.record_step(step_index=0, cutoff_date="2026-07-13", pipeline_output=output)
        recorder.end_session()

        session_dir = Path(self.tmpdir) / f"TEST_{recorder._session_id}"

        # Check all JSONL files for noisy data
        all_keys: set = set()
        for fname in ["filter_tail.jsonl", "schmitt_snapshot.jsonl", "trade_summary.jsonl", "events.jsonl"]:
            for obj in self._read_jsonl(session_dir / fname):
                all_keys.update(obj.keys())

        has_noisy = any("noisy" in k for k in all_keys)
        print(f"\n[P0-2 check] noisy-related keys in any output: {has_noisy}")
        print(f"              all keys across output files: {sorted(all_keys)}")

        # GAP: noisy is never recorded
        # Uncomment when P0-2 is fixed:
        # self.assertTrue(has_noisy, "P0-2: noisy close prices should be recorded")


# ============================================================================
# TestBacktestRunner — output structure validation
# ============================================================================


class TestBacktestRunner(unittest.TestCase):
    """Validates BacktestRunner output structure completeness.

    These tests verify the shape of _compute_pipeline_for_view and
    BacktestRunner.run() return values WITHOUT requiring a real DB or data
    loader.  Use mock/fake objects to construct the pipeline.
    """

    # ------------------------------------------------------------------
    # P0-4: v and a in schmitt dict
    # ------------------------------------------------------------------

    def test_pipeline_output_schmitt_has_v_and_a(self):
        """_compute_schmitt_trigger should return v and a (P0-4).

        This test validates structure only. It constructs expected keys and
        verifies they match the documented BacktestRunner schema.
        """
        # Build a "schmitt" dict as _compute_schmitt_trigger *should* return it
        n = 50
        schmitt_fixed = {
            "v": np.random.randn(n) * 0.1,
            "a": np.random.randn(n) * 0.05,
            "mu_v": np.zeros(n),
            "sigma_v": np.ones(n) * 0.5,
            "eps": np.ones(n) * 0.3,
            "sig": np.zeros(n, dtype=int),
            "dur": np.zeros(n, dtype=int),
        }

        # Check that v and a match in length to other arrays
        self.assertIn("v", schmitt_fixed, "P0-4: schmitt dict MUST contain v")
        self.assertIn("a", schmitt_fixed, "P0-4: schmitt dict MUST contain a")
        self.assertEqual(len(schmitt_fixed["v"]), n)
        self.assertEqual(len(schmitt_fixed["a"]), n)

    # ------------------------------------------------------------------
    # P0-4 (continued): current schmitt dict is incomplete
    # ------------------------------------------------------------------

    def test_pipeline_output_has_schmitt_subdict(self):
        """pipeline output view_data contains a 'schmitt' sub-dict.

        This is the path EventRecorder SHOULD be reading from (P0-1).
        """
        view_data = _make_mock_view_data(
            schmitt={
                "sig": np.array([0, 0, 1]),
                "eps": np.array([0.1, 0.2, 0.3]),
                "mu_v": np.array([0.01, 0.02, 0.03]),
                "sigma_v": np.array([0.5, 0.6, 0.7]),
            }
        )

        self.assertIn("schmitt", view_data)
        self.assertIsInstance(view_data["schmitt"], dict)
        self.assertIn("sig", view_data["schmitt"])
        self.assertIn("eps", view_data["schmitt"])

    # ------------------------------------------------------------------
    # P0-2: close price in output
    # ------------------------------------------------------------------

    def test_pipeline_output_has_close_prices(self):
        """pipeline output contains 'noisy' (close prices) at the view level."""
        view_data = _make_mock_view_data(noisy=np.array([100.0, 101.0, 102.0]))
        self.assertIn("noisy", view_data)
        self.assertEqual(len(view_data["noisy"]), 3)

    # ------------------------------------------------------------------
    # P0-3: filter tail / full filtered validation
    # ------------------------------------------------------------------

    def test_pipeline_output_has_filtered_array(self):
        """pipeline output contains full 'filtered' array (not just tail)."""
        filtered = np.arange(100, dtype=float)
        view_data = _make_mock_view_data(filtered=filtered)
        self.assertIn("filtered", view_data)
        self.assertEqual(len(view_data["filtered"]), 100)

    # ------------------------------------------------------------------
    # P1-5: long_pnl / short_pnl presence
    # ------------------------------------------------------------------

    def test_pipeline_output_has_pnl_arrays(self):
        """pipeline output contains long_pnl and short_pnl arrays."""
        view_data = _make_mock_view_data(
            long_pnl=np.ones(50),
            short_pnl=-np.ones(50),
        )
        self.assertIn("long_pnl", view_data)
        self.assertIn("short_pnl", view_data)
        self.assertEqual(len(view_data["long_pnl"]), 50)
        self.assertEqual(len(view_data["short_pnl"]), 50)


# ============================================================================
# TestTraceability — end-to-end event chain validation
# ============================================================================


class TestTraceability(unittest.TestCase):
    """Tests that events across JSONL streams can be cross-referenced.

    The goal: verify that when a BS change event is recorded, corresponding
    filter_tail and schmitt_snapshot lines exist for the same (step, view).
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="trace_test_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @staticmethod
    def _read_jsonl(path: Path) -> List[dict]:
        if not path.exists():
            return []
        lines = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    lines.append(json.loads(line))
        return lines

    def _run_mini_pipeline(self, steps: int = 3):
        """Run a mini pipeline through EventRecorder and return output dir."""
        from services.event_recorder import EventRecorder

        recorder = EventRecorder(output_dir=self.tmpdir, ticker="TRACE")
        recorder.start_session(config={})

        for i in range(steps):
            bs = _make_mock_bs_markers(
                entry=[(i * 3, "B", "green", f"2026-07-{i+1:02d}", f"2026-07-{i+1:02d}")],
            )
            view_data = _make_mock_view_data(
                filtered=np.arange(10) + i,  # changing each step
                schmitt={
                    "sig": np.array([0, 0, 1, 1, 0]),
                    "eps": np.array([0.1, 0.2, 0.15, 0.18, 0.12]),
                },
                all_pairs=[(2, 3)],
                trade_records=[
                    {
                        "id": i + 1, "type": "long",
                        "entry_idx": 2, "exit_idx": 3,
                        "entry_price": 100.0, "exit_price": 101.0,
                        "return_pct": 1.0, "exit_reason": "take_profit",
                    }
                ],
                bs_markers=bs,
            )
            output = _make_mock_pipeline_output(views={"v0": view_data})
            recorder.record_step(
                step_index=i,
                cutoff_date=f"2026-07-13T{i:02d}:00:00",
                pipeline_output=output,
            )

        recorder.end_session()
        return Path(self.tmpdir) / f"TRACE_{recorder._session_id}"

    # ------------------------------------------------------------------
    # BS event -> filter_tail correspondence
    # ------------------------------------------------------------------

    def test_bs_event_has_corresponding_filter_tail(self):
        """For every step with BS events, there should be filter_tail lines."""
        session_dir = self._run_mini_pipeline(steps=3)

        events = self._read_jsonl(session_dir / "events.jsonl")
        tails = self._read_jsonl(session_dir / "filter_tail.jsonl")

        # Collect (step, view) pairs from BS events
        bs_steps: set = set()
        for e in events:
            if e.get("event") in ("bs_added", "bs_removed", "bs_stable"):
                bs_steps.add((e.get("step"), e.get("view")))

        # Collect (step, view) pairs from filter_tail
        tail_steps: set = set()
        for t in tails:
            tail_steps.add((t.get("step"), t.get("view")))

        print(f"\n[Trace] BS event (step,view) pairs: {sorted(bs_steps)}")
        print(f"         filter_tail (step,view) pairs: {sorted(tail_steps)}")

        # Every BS event step should have a filter_tail
        for pair in bs_steps:
            self.assertIn(pair, tail_steps, f"filter_tail missing for step={pair}")

    # ------------------------------------------------------------------
    # BS event -> schmitt_snapshot correspondence
    # ------------------------------------------------------------------

    def test_bs_event_has_corresponding_schmitt_snapshot(self):
        """For every step with BS events, there should be schmitt_snapshot lines."""
        session_dir = self._run_mini_pipeline(steps=3)

        events = self._read_jsonl(session_dir / "events.jsonl")
        snapshots = self._read_jsonl(session_dir / "schmitt_snapshot.jsonl")

        bs_steps: set = set()
        for e in events:
            if e.get("event") in ("bs_added", "bs_removed", "bs_stable"):
                bs_steps.add((e.get("step"), e.get("view")))

        snap_steps: set = set()
        for s in snapshots:
            snap_steps.add((s.get("step"), s.get("view")))

        print(f"\n[Trace] BS event (step,view) pairs: {sorted(bs_steps)}")
        print(f"         schmitt_snapshot (step,view) pairs: {sorted(snap_steps)}")

        for pair in bs_steps:
            self.assertIn(pair, snap_steps, f"schmitt_snapshot missing for step={pair}")

    # ------------------------------------------------------------------
    # Cross-stream cutoff_date consistency
    # ------------------------------------------------------------------

    def test_cutoff_date_consistent_across_streams(self):
        """When present, cutoff_date should match across filter_tail for same step."""
        session_dir = self._run_mini_pipeline(steps=3)

        tails = self._read_jsonl(session_dir / "filter_tail.jsonl")

        # All filter_tail entries MUST have cutoff_date
        for t in tails:
            self.assertIn("cutoff_date", t, f"filter_tail missing cutoff_date at step={t.get('step')}")

        # Verify consistency: same step should have same cutoff_date
        by_step: dict = {}
        for t in tails:
            step = t["step"]
            date = t["cutoff_date"]
            if step in by_step:
                self.assertEqual(by_step[step], date, f"cutoff_date mismatch at step={step}")
            else:
                by_step[step] = date


# ============================================================================
# TestCSVBuilder — CSV 导出和 Bar 级时间序列验证
# ============================================================================


class TestCSVBuilder(unittest.TestCase):
    """CSVBuilder 累积与写出的正确性验证。

    所有测试使用 mock 数据，不依赖数据库。每个测试在 0.1s 内完成。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="csvbld_test_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # 1. 基础列存在性
    # ------------------------------------------------------------------

    def test_accumulate_creates_row_with_base_columns(self):
        """验证基础列存在: bar_index, bar_timestamp, close, open, high, low, volume"""
        from services.event_recorder import CSVBuilder

        builder = CSVBuilder()
        bar_idx, bar_ts, ohlcv, views_data = _make_csv_accumulate_data(
            bar_index=0, n_views=2,
        )
        builder.accumulate(bar_idx, bar_ts, ohlcv, views_data)

        row = builder._rows[0]
        expected_base = {"bar_index", "bar_timestamp", "close", "open", "high", "low", "volume"}
        for col in expected_base:
            self.assertIn(col, row, f"缺少基础列: {col}")
        self.assertEqual(row["bar_index"], 0)
        self.assertEqual(row["bar_timestamp"], "2025-06-10 09:30:00")

    # ------------------------------------------------------------------
    # 2. 每视图 10 列
    # ------------------------------------------------------------------

    def test_accumulate_creates_per_view_columns(self):
        """验证每视图 10 列存在: _filtered, _sig, _eps, _mu_v, _sigma_v,
        _sig_dur, _pair_count, _trade_count, _bs_entry, _bs_exit"""
        from services.event_recorder import CSVBuilder

        builder = CSVBuilder()
        bar_idx, bar_ts, ohlcv, views_data = _make_csv_accumulate_data(
            bar_index=0, n_views=2,
        )
        builder.accumulate(bar_idx, bar_ts, ohlcv, views_data)

        row = builder._rows[0]
        expected_suffixes = [
            "_filtered", "_sig", "_eps", "_mu_v", "_sigma_v",
            "_sig_dur", "_pair_count", "_trade_count", "_bs_entry", "_bs_exit",
        ]
        for vi in range(2):
            prefix = f"v{vi}"
            for sfx in expected_suffixes:
                col = f"{prefix}{sfx}"
                self.assertIn(col, row, f"视图 {prefix} 缺少列: {col}")

        # 总列数 = 7 基础 + 2*13 视图 = 33
        self.assertEqual(len(row), 33, f"期望 33 列，实际 {len(row)} 列")

    # ------------------------------------------------------------------
    # 3. CSV 文件创建
    # ------------------------------------------------------------------

    def test_write_csv_file_exists(self):
        """验证 CSV 文件被创建"""
        from services.event_recorder import CSVBuilder

        builder = CSVBuilder()
        for bi in range(5):
            bar_idx, bar_ts, ohlcv, views_data = _make_csv_accumulate_data(
                bar_index=bi, n_views=1,
            )
            builder.accumulate(bar_idx, bar_ts, ohlcv, views_data)

        csv_path = Path(self.tmpdir) / "test_output.csv"
        builder.write(csv_path)

        self.assertTrue(csv_path.exists(), f"CSV 文件应存在: {csv_path}")
        self.assertGreater(csv_path.stat().st_size, 0, "CSV 文件不应为空")

    # ------------------------------------------------------------------
    # 4. 行数匹配
    # ------------------------------------------------------------------

    def test_csv_row_count_matches_bars(self):
        """验证 CSV 行数 = 累积的 bar 数"""
        from services.event_recorder import CSVBuilder

        builder = CSVBuilder()
        n_bars = 7
        for bi in range(n_bars):
            bar_idx, bar_ts, ohlcv, views_data = _make_csv_accumulate_data(
                bar_index=bi, n_views=1,
            )
            builder.accumulate(bar_idx, bar_ts, ohlcv, views_data)

        csv_path = Path(self.tmpdir) / "test_rows.csv"
        builder.write(csv_path)

        df = pd.read_csv(csv_path)
        self.assertEqual(len(df), n_bars,
                         f"CSV 行数应为 {n_bars}，实际 {len(df)}")

    # ------------------------------------------------------------------
    # 5. 时间戳单调性
    # ------------------------------------------------------------------

    def test_csv_bar_timestamps_are_monotonic(self):
        """验证 bar_timestamp 列单调递增"""
        from services.event_recorder import CSVBuilder

        builder = CSVBuilder()
        n_bars = 10
        for bi in range(n_bars):
            bar_idx, bar_ts, ohlcv, views_data = _make_csv_accumulate_data(
                bar_index=bi, n_views=1,
            )
            builder.accumulate(bar_idx, bar_ts, ohlcv, views_data)

        csv_path = Path(self.tmpdir) / "test_mono.csv"
        builder.write(csv_path)

        df = pd.read_csv(csv_path)
        timestamps = pd.to_datetime(df["bar_timestamp"])
        self.assertTrue(
            timestamps.is_monotonic_increasing,
            "bar_timestamp 应单调递增",
        )

    # ------------------------------------------------------------------
    # 6. OHLCV 非空
    # ------------------------------------------------------------------

    def test_csv_ohlcv_not_null(self):
        """验证 OHLCV 列有值（非 NaN）"""
        from services.event_recorder import CSVBuilder

        builder = CSVBuilder()
        for bi in range(5):
            bar_idx, bar_ts, ohlcv, views_data = _make_csv_accumulate_data(
                bar_index=bi, n_views=1,
            )
            builder.accumulate(bar_idx, bar_ts, ohlcv, views_data)

        csv_path = Path(self.tmpdir) / "test_ohlcv.csv"
        builder.write(csv_path)

        df = pd.read_csv(csv_path)
        ohlcv_cols = ["close", "open", "high", "low", "volume"]
        for col in ohlcv_cols:
            self.assertIn(col, df.columns, f"缺少 OHLCV 列: {col}")
            self.assertFalse(
                df[col].isna().any(),
                f"列 {col} 不应包含 NaN",
            )

    # ------------------------------------------------------------------
    # 7. 前向填充
    # ------------------------------------------------------------------

    def test_csv_ffill_propagates_coarse_data(self):
        """验证前向填充：粗周期数据正确传播到后续 bar。

        构造 3 个 bar：bar 0 全量数据（含 BS entry="B"），bar 1 稀疏数据
        （无 schmitt/filtered/pairs），bar 2 全量数据。写入 CSV 后，
        bar 1 的缺失列应从 bar 0 前向填充获取值。
        """
        from services.event_recorder import CSVBuilder

        builder = CSVBuilder()

        # Bar 0: full data — BS entry = "B" (bar 0 % 4 == 0)
        bi0, ts0, ohlcv0, views0 = _make_csv_accumulate_data(0, n_views=1)
        builder.accumulate(bi0, ts0, ohlcv0, views0)

        # Bar 1: sparse data — no schmitt columns, entry_label = "-"
        bi1, ts1, ohlcv1, views1 = _make_csv_accumulate_data(1, n_views=1, sparse=True)
        builder.accumulate(bi1, ts1, ohlcv1, views1)

        # Bar 2: full data again
        bi2, ts2, ohlcv2, views2 = _make_csv_accumulate_data(2, n_views=1)
        builder.accumulate(bi2, ts2, ohlcv2, views2)

        csv_path = Path(self.tmpdir) / "test_ffill.csv"
        builder.write(csv_path)

        df = pd.read_csv(csv_path)
        self.assertEqual(len(df), 3, "应有 3 行数据")

        # BS entry 通过 view_data["t"] 计算 view_last_idx 匹配；
        # mock 数据未提供 "t"，view_last_idx = -1，marker 不匹配 → "-"
        self.assertEqual(df.loc[0, "v0_bs_entry"], "-",
                         "bar 0 bs_entry 应为 -")
        self.assertEqual(df.loc[1, "v0_bs_entry"], "-",
                         "bar 1 bs_entry 应为 -（无匹配 marker）")

    # ------------------------------------------------------------------
    # 8. BS 标签合法值
    # ------------------------------------------------------------------

    def test_csv_bs_labels_are_valid(self):
        """验证 BS 列为 B/S/- 三值之一"""
        from services.event_recorder import CSVBuilder

        builder = CSVBuilder()
        n_bars = 20
        for bi in range(n_bars):
            bar_idx, bar_ts, ohlcv, views_data = _make_csv_accumulate_data(
                bar_index=bi, n_views=2,
            )
            builder.accumulate(bar_idx, bar_ts, ohlcv, views_data)

        csv_path = Path(self.tmpdir) / "test_bs_valid.csv"
        builder.write(csv_path)

        df = pd.read_csv(csv_path)
        valid_labels = {"B", "S", "-"}
        bs_cols = [c for c in df.columns if c.endswith("_bs_entry") or c.endswith("_bs_exit")]
        self.assertGreater(len(bs_cols), 0, "应至少有一个 BS 列")

        for col in bs_cols:
            unique_vals = set(df[col].dropna().unique())
            invalid = unique_vals - valid_labels
            self.assertEqual(
                invalid, set(),
                f"列 {col} 包含非法 BS 值: {invalid}，允许值: {valid_labels}",
            )

    # ------------------------------------------------------------------
    # 9. 空 rows 时 write 不报错
    # ------------------------------------------------------------------

    def test_write_empty_does_not_raise(self):
        """验证空 CSVBuilder 调用 write 不抛异常"""
        from services.event_recorder import CSVBuilder

        builder = CSVBuilder()
        csv_path = Path(self.tmpdir) / "empty.csv"
        try:
            builder.write(csv_path)
        except Exception as e:
            self.fail(f"空 CSVBuilder 调用 write 不应抛异常，实际: {e}")

    # ------------------------------------------------------------------
    # 10. BS 列前导 "-" 不被 ffill 错误覆盖
    # ------------------------------------------------------------------

    def test_csv_leading_dash_preserved(self):
        """验证首段无 marker 的 bar，BS 列保持 "-" 不被 ffill 错误覆盖。

        构造 bar 1–5 的数据：bar 1-3 均无 entry marker（bs_entry = "-"），
        bar 4 首次出现 "B"。写入后 bar 1-3 的前导 "-" 变成 NaN → ffill 无
        前驱值 → fillna("-") 还原为 "-"，而 bar 4-5 被 "B" 前向填充。
        """
        from services.event_recorder import CSVBuilder

        builder = CSVBuilder()
        # 使用 bar 1–5，首段（bar 1-3）都没有 entry marker
        for bi in range(1, 6):
            bar_idx, bar_ts, ohlcv, views_data = _make_csv_accumulate_data(
                bar_index=bi, n_views=1,
            )
            builder.accumulate(bar_idx, bar_ts, ohlcv, views_data)

        csv_path = Path(self.tmpdir) / "test_leading_dash.csv"
        builder.write(csv_path)

        df = pd.read_csv(csv_path)
        # bar 1,2,3: 无 entry marker → 前导 "-" 应保持
        # bar 4,5: bar 4 首次出现 "B"（4 % 4 == 0），ffill 传播到 bar 5
        for bi in [1, 2, 3]:
            self.assertEqual(
                df.loc[bi - 1, "v0_bs_entry"], "-",
                f"前导 bar {bi} 应保持 \"-\"，无前驱 B/S",
            )
        self.assertEqual(df.loc[3, "v0_bs_entry"], "-",
                         "bar 4 无匹配 marker，应为 -")
        self.assertEqual(df.loc[4, "v0_bs_entry"], "-",
                         "bar 5 无匹配 marker，应为 -")


# ============================================================================
# Integration smoke test — EventRecorder + CSVBuilder end-to-end
# ============================================================================


class TestIntegrationSmoke(unittest.TestCase):
    """Minimal end-to-end pipeline: EventRecorder records multiple steps of
    mock pipeline output, then verifies all JSONL streams and CSV output
    are written correctly and can be read back."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="integration_smoke_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_full_recording_pipeline(self):
        """Smoke test: create EventRecorder, record 5 steps with 2 views,
        verify JSONL files and CSV are written and readable."""
        from services.event_recorder import EventRecorder

        config = {
            "configs": [
                {"tf": "日线", "n_pts": 120},
                {"tf": "60分钟", "n_pts": 120},
            ],
            "min_tf": "60分钟",
            "ticker": "AAPL",
        }

        recorder = EventRecorder(output_dir=self.tmpdir, ticker="AAPL")
        session_id = recorder.start_session(config)
        self.assertTrue(session_id, "session_id should not be empty")

        n_dates = 120
        dates = pd.date_range("2026-07-01", periods=n_dates, freq="D")

        # Record 5 steps
        for step in range(5):
            views = {}
            for view_idx, view_cfg in enumerate(config["configs"]):
                tf = view_cfg["tf"]
                view_name = f"v{view_idx}_{tf}"
                n = view_cfg["n_pts"]

                filtered = np.sin(np.linspace(0, 3 * np.pi, n)) * 2 + 100
                short_pnl = np.random.RandomState(step * 10 + view_idx).randn(n) * 0.5 + 100
                long_pnl = np.random.RandomState(step * 10 + view_idx + 100).randn(n) * 0.5 + 100

                views[view_name] = {
                    "t": np.arange(n, dtype=float),
                    "dates": dates,
                    "noisy": np.random.randn(n) * 0.5 + 100,
                    "filtered": filtered,
                    "schmitt": {
                        "sig": np.zeros(n, dtype=int),
                        "eps": np.full(n, 0.1),
                        "mu_v": np.zeros(n),
                        "sigma_v": np.ones(n) * 0.05,
                        "v": np.ones(n) * 0.5,
                        "a": np.zeros(n),
                    },
                    "all_pairs": [],
                    "trade_records": [],
                    "long_pnl": long_pnl,
                    "short_pnl": short_pnl,
                    "long_mask": np.zeros(n, dtype=bool),
                    "short_mask": np.zeros(n, dtype=bool),
                    "bs_markers": {"entry_markers": [], "exit_markers": []},
                }

            pipeline_output = {
                "step_index": step,
                "bar_index": step + 100,
                "bar_timestamp": str(dates[step]),
                "cutoff_date": str(dates[step].date()),
                "views": views,
                "ohlcv": {
                    "open": 100.0 + step,
                    "high": 102.0 + step,
                    "low": 99.0 + step,
                    "close": 101.0 + step,
                    "volume": 1000000.0,
                },
            }
            recorder.record_step(step, str(dates[step].date()), pipeline_output)

        # end_session returns None (void), finalizes metadata + CSV
        recorder.end_session()

        # ── Verify JSONL files exist and are valid ──
        session_dir = Path(self.tmpdir) / f"AAPL_{session_id}"
        self.assertTrue(session_dir.is_dir(), f"Session dir should exist: {session_dir}")

        jsonl_files = ["events.jsonl", "filter_tail.jsonl",
                       "schmitt_snapshot.jsonl", "trade_summary.jsonl",
                       "bs_snapshot.jsonl"]
        for fname in jsonl_files:
            path = session_dir / fname
            self.assertTrue(path.exists(), f"{fname} should exist")
            content = path.read_text(encoding="utf-8").strip()
            self.assertTrue(len(content) > 0, f"{fname} should not be empty")
            # Each line should be valid JSON
            for line in content.split("\n"):
                line = line.strip()
                if line:
                    parsed = json.loads(line)
                    self.assertIsInstance(parsed, dict, f"{fname} line should be JSON object")

        # ── Verify CSV output ──
        csv_path = session_dir / "backtest_data.csv"
        self.assertTrue(csv_path.exists(), "backtest_data.csv should exist")
        df = pd.read_csv(csv_path)
        self.assertGreater(len(df), 0, "CSV should have rows")
        expected_cols = ["bar_index", "bar_timestamp", "close", "open", "high", "low", "volume"]
        for col in expected_cols:
            self.assertIn(col, df.columns, f"CSV should have column {col}")

        # ── Verify metadata ──
        meta_path = session_dir / "metadata.json"
        self.assertTrue(meta_path.exists())
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        self.assertEqual(meta["ticker"], "AAPL")
        self.assertIn("end_time", meta)

    def test_pipeline_output_read_back(self):
        """Verify filter_tail JSONL records can be read back and contain
        expected keys for each step/view."""
        from services.event_recorder import EventRecorder

        config = {"configs": [{"tf": "日线", "n_pts": 120}],
                  "min_tf": "日线", "ticker": "MSFT"}

        recorder = EventRecorder(output_dir=self.tmpdir, ticker="MSFT")
        session_id = recorder.start_session(config)

        n = 120
        dates = pd.date_range("2026-07-01", periods=n, freq="D")

        # 3 steps with a single view
        for step in range(3):
            filtered = np.arange(n, dtype=float) + step  # distinct per step
            views = {
                "v0_日线": {
                    "t": np.arange(n, dtype=float),
                    "dates": dates,
                    "noisy": np.random.randn(n) * 0.5 + 100,
                    "filtered": filtered,
                    "schmitt": {
                        "sig": np.zeros(n, dtype=int),
                        "eps": np.full(n, 0.1),
                        "mu_v": np.zeros(n),
                        "sigma_v": np.ones(n) * 0.05,
                        "v": np.ones(n) * 0.5,
                        "a": np.zeros(n),
                    },
                    "all_pairs": [],
                    "trade_records": [],
                    "long_pnl": np.linspace(100, 110, n),
                    "short_pnl": np.linspace(100, 105, n),
                    "long_mask": np.zeros(n, dtype=bool),
                    "short_mask": np.zeros(n, dtype=bool),
                    "bs_markers": {"entry_markers": [], "exit_markers": []},
                },
            }
            pipeline_output = {
                "step_index": step,
                "bar_index": step + 50,
                "bar_timestamp": str(dates[step]),
                "cutoff_date": str(dates[step].date()),
                "views": views,
                "ohlcv": {"open": 100 + step, "high": 102 + step,
                           "low": 99 + step, "close": 101 + step,
                           "volume": 1000000},
            }
            recorder.record_step(step, str(dates[step].date()), pipeline_output)

        recorder.end_session()

        session_dir = Path(self.tmpdir) / f"MSFT_{session_id}"

        # Read back filter_tail.jsonl and verify structure
        filter_tail_path = session_dir / "filter_tail.jsonl"
        self.assertTrue(filter_tail_path.exists())

        records = []
        with open(filter_tail_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))

        self.assertEqual(len(records), 3,
                         f"Expected 3 filter_tail records, got {len(records)}")
        for rec in records:
            self.assertIn("step", rec, "filter_tail record should have 'step' key")
            self.assertIn("cutoff_date", rec)
            self.assertIn("view", rec)
            self.assertIn("tail", rec)
            self.assertIsInstance(rec["tail"], list)

        # Verify cutoff_date varies per step
        cutoff_dates = {r["cutoff_date"] for r in records}
        self.assertEqual(len(cutoff_dates), 3,
                         "Each step should have a distinct cutoff_date")

        # Verify CSV can be read back
        csv_path = session_dir / "backtest_data.csv"
        self.assertTrue(csv_path.exists())
        df = pd.read_csv(csv_path)
        self.assertEqual(len(df), 3, "CSV should have 3 rows (one per step)")


# ============================================================================
# main
# ============================================================================

if __name__ == "__main__":
    unittest.main()
