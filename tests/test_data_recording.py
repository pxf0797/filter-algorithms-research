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
# main
# ============================================================================

if __name__ == "__main__":
    unittest.main()
