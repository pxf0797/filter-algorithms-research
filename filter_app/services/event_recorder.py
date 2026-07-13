"""
Event Sourcing data recorder for backtest pipeline observability.

JSONL append-only, grep-friendly. Records per-step BS marker changes,
filter tails, Schmitt snapshots, and trade summaries to timestamped
session directories. Write failures are logged but never interrupt the
backtest loop — designed for zero-intrusion integration.

Usage::

    recorder = EventRecorder(output_dir="data/events", ticker="AAPL")
    session_id = recorder.start_session(config={...})
    # ... for each step in backtest loop:
    recorder.record_step(step_index=42, cutoff_date="2025-06-15",
                         pipeline_output={...})
    recorder.end_session()
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np
from loguru import logger


class EventRecorder:
    """Event Sourcing data recorder. JSONL append-only, grep-friendly.

    Parameters
    ----------
    output_dir : str or Path
        Root output directory for event sessions.
    ticker : str
        Ticker symbol (e.g. ``"03690.HK"``).
    """

    def __init__(self, output_dir: Union[str, Path], ticker: str):
        self.output_dir = Path(output_dir)
        self.ticker = ticker
        self._session_dir: Optional[Path] = None
        self._session_id: str = ""
        self._prev_bs_by_view: Dict[str, Optional[dict]] = {}
        self._step_count: int = 0
        # JSONL append file handles — opened in start_session, closed in end_session
        self._events_fp = None
        self._filter_tail_fp = None
        self._schmitt_snapshot_fp = None
        self._trade_summary_fp = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_session(self, config: dict) -> str:
        """Create session directory + metadata.json and return session_id.

        Session id format: ``YYYYMMDD-HHMMSS-{ticker}``.
        metadata.json contains: ticker, session_id, start_time, config.

        Parameters
        ----------
        config : dict
            Backtest configuration snapshot (operating_tf, view_configs, etc.).
            Non-serializable values fall back to ``str()`` representation.
        """
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._session_id = f"{ts}-{self.ticker}"
        self._session_dir = self.output_dir / f"{self.ticker}_{self._session_id}"
        self._session_dir.mkdir(parents=True, exist_ok=True)

        metadata: Dict[str, Any] = {
            "ticker": self.ticker,
            "session_id": self._session_id,
            "start_time": datetime.now().isoformat(),
            "config": config,
        }
        _write_json(self._session_dir / "metadata.json", metadata)

        # Open JSONL files for append
        self._events_fp = _open_jsonl(self._session_dir / "events.jsonl")
        self._filter_tail_fp = _open_jsonl(self._session_dir / "filter_tail.jsonl")
        self._schmitt_snapshot_fp = _open_jsonl(self._session_dir / "schmitt_snapshot.jsonl")
        self._trade_summary_fp = _open_jsonl(self._session_dir / "trade_summary.jsonl")

        # Write session_started event
        self._append_jsonl(self._events_fp, {
            "event": "session_started",
            "timestamp": datetime.now().isoformat(),
            "ticker": self.ticker,
            "session_id": self._session_id,
        })

        return self._session_id

    def record_step(
        self,
        step_index: int,
        cutoff_date: str,
        pipeline_output: dict,
    ) -> None:
        """Record one step's pipeline output across all views.

        Appends to:
        - ``events.jsonl`` — BS change events (via ``_compare_bs_markers``)
        - ``filter_tail.jsonl`` — last 5 filter values per view
        - ``schmitt_snapshot.jsonl`` — Schmitt key metrics per view
        - ``trade_summary.jsonl`` — trade record summary per view

        Parameters
        ----------
        step_index : int
            Monotonically increasing step counter (0-based).
        cutoff_date : str
            ISO-format cutoff date string (e.g. ``"2025-06-15"``).
        pipeline_output : dict
            Output from BacktestRunner. Expected structure::

                {
                    "step_index": int,
                    "cutoff_date": str,
                    "views": {
                        "v0_日线": {
                            "filtered": ndarray,
                            "sig": ndarray,
                            "eps": ndarray,
                            "all_pairs": list,
                            "trade_records": list,
                            "bs_markers": {
                                "entry_markers": [...],
                                "exit_markers": [...]
                            },
                        },
                        ...
                    }
                }
        """
        if self._session_dir is None:
            return

        views: Dict[str, dict] = pipeline_output.get("views", {})
        if not views:
            return

        for view_name, view_data in views.items():
            try:
                self._record_view_step(step_index, cutoff_date, view_name, view_data)
            except Exception:
                logger.warning(
                    "EventRecorder: failed to record step {} for view {}",
                    step_index, view_name, exc_info=True,
                )

        self._step_count = step_index + 1

    def end_session(self) -> None:
        """Close session, write end_time and step_count to metadata.json."""
        if self._session_dir is None:
            return

        # Write session_ended event before closing files
        if self._events_fp:
            try:
                self._append_jsonl(self._events_fp, {
                    "event": "session_ended",
                    "timestamp": datetime.now().isoformat(),
                    "step_count": self._step_count,
                })
            except Exception:
                logger.warning("EventRecorder: failed to write session_ended event", exc_info=True)

        # Close all JSONL file handles
        for fp in (self._events_fp, self._filter_tail_fp,
                    self._schmitt_snapshot_fp, self._trade_summary_fp):
            if fp is not None:
                try:
                    fp.close()
                except Exception:
                    pass
        self._events_fp = None
        self._filter_tail_fp = None
        self._schmitt_snapshot_fp = None
        self._trade_summary_fp = None

        # Update metadata.json with final stats
        meta_path = self._session_dir / "metadata.json"
        meta: Dict[str, Any] = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        meta["end_time"] = datetime.now().isoformat()
        meta["step_count"] = self._step_count
        _write_json(meta_path, meta)

    # ------------------------------------------------------------------
    # BS Marker Comparison
    # ------------------------------------------------------------------

    def _compare_bs_markers(
        self,
        step_index: int,
        prev_bs: Optional[dict],
        curr_bs: dict,
    ) -> List[dict]:
        """Compare BS markers between two consecutive steps.

        Identity is tracked by ``(bar_idx, label, date)`` triples. For the
        first step (``prev_bs is None``), every marker is reported as
        ``bs_added``. For subsequent steps, markers present in ``curr_bs``
        but not in ``prev_bs`` are ``bs_added``, those present only in
        ``prev_bs`` are ``bs_removed``, those with changed attributes are
        ``bs_modified``, and unchanged markers are collapsed into a single
        ``bs_stable`` count event.

        Parameters
        ----------
        step_index : int
            Current step index.
        prev_bs : dict or None
            Previous step's ``bs_markers`` (``None`` for the first step).
        curr_bs : dict
            Current step's ``bs_markers`` with ``entry_markers`` and
            ``exit_markers`` lists.

        Returns
        -------
        list[dict]
            BS change event dicts, each containing at minimum ``event``,
            ``step``, ``type``, ``label``, ``bar_idx``, and ``date``.
        """
        curr_map = _build_identity_map(curr_bs)

        # First step: every marker is a new addition
        if prev_bs is None:
            events: List[dict] = []
            for key, info in curr_map.items():
                evt = {"event": "bs_added", "step": step_index}
                evt.update(info)
                events.append(evt)
            return events

        prev_map = _build_identity_map(prev_bs)
        prev_keys = set(prev_map.keys())
        curr_keys = set(curr_map.keys())
        events: List[dict] = []

        # Added: keys present in current but absent in previous
        for key in curr_keys - prev_keys:
            info = curr_map[key]
            evt = {"event": "bs_added", "step": step_index}
            evt.update(info)
            events.append(evt)

        # Removed: keys present in previous but absent in current
        for key in prev_keys - curr_keys:
            info = prev_map[key]
            evt = {"event": "bs_removed", "step": step_index}
            evt.update(info)
            events.append(evt)

        # Modified: same key but different attributes
        modified_keys = {
            key for key in curr_keys & prev_keys
            if curr_map[key] != prev_map[key]
        }
        for key in modified_keys:
            info = curr_map[key]
            evt = {"event": "bs_modified", "step": step_index}
            evt.update(info)
            events.append(evt)

        # Stable: unchanged markers collapsed into a single count
        stable_count = len(curr_keys & prev_keys) - len(modified_keys)
        if stable_count > 0:
            events.append({
                "event": "bs_stable",
                "step": step_index,
                "marker_count": stable_count,
            })

        return events

    # ------------------------------------------------------------------
    # Internal: per-view step recording
    # ------------------------------------------------------------------

    def _record_view_step(
        self,
        step_index: int,
        cutoff_date: str,
        view_name: str,
        view_data: dict,
    ) -> None:
        """Record one step's data for a single view into all JSONL streams."""
        # --- BS marker change events ---
        curr_bs = view_data.get("bs_markers")
        prev_bs = self._prev_bs_by_view.get(view_name)
        if curr_bs is not None:
            bs_events = self._compare_bs_markers(step_index, prev_bs, curr_bs)
            for evt in bs_events:
                evt["view"] = view_name
                self._append_jsonl(self._events_fp, evt)
            self._prev_bs_by_view[view_name] = curr_bs

        # --- Filter tail ---
        filtered = view_data.get("filtered")
        if filtered is not None:
            tail = _ndarray_tail(filtered, 5)
            self._append_jsonl(self._filter_tail_fp, {
                "event": "filter_tail",
                "step": step_index,
                "view": view_name,
                "cutoff_date": cutoff_date,
                "tail": tail,
            })

        # --- Schmitt snapshot ---
        sig = view_data.get("sig")
        eps = view_data.get("eps")
        all_pairs = view_data.get("all_pairs")
        if sig is not None or eps is not None or all_pairs is not None:
            snapshot: Dict[str, Any] = {
                "event": "schmitt_snapshot",
                "step": step_index,
                "view": view_name,
            }
            if sig is not None:
                sig_arr = np.asarray(sig)
                snapshot["sig_counts"] = {
                    "-1": int((sig_arr == -1).sum()),
                    "0": int((sig_arr == 0).sum()),
                    "1": int((sig_arr == 1).sum()),
                }
            if all_pairs is not None:
                snapshot["pair_count"] = len(all_pairs)
            if eps is not None:
                snapshot["eps_tail"] = _ndarray_tail(eps, 3)
            self._append_jsonl(self._schmitt_snapshot_fp, snapshot)

        # --- Trade summary ---
        trade_records = view_data.get("trade_records")
        if trade_records is not None:
            summary = _build_trade_summary(step_index, trade_records)
            summary["view"] = view_name
            self._append_jsonl(self._trade_summary_fp, summary)

    # ------------------------------------------------------------------
    # Internal: JSONL I/O
    # ------------------------------------------------------------------

    def _append_jsonl(self, fp, obj: dict) -> None:
        """Append a JSON-encoded line to *fp* and flush immediately."""
        if fp is None:
            return
        line = json.dumps(obj, default=_json_default, ensure_ascii=False)
        fp.write(line + "\n")
        fp.flush()


# ----------------------------------------------------------------------
# Module-level helpers
# ----------------------------------------------------------------------

def _open_jsonl(path: Path):
    """Open a file for UTF-8 append. Returns None on failure (logged)."""
    try:
        return open(path, "a", encoding="utf-8")
    except OSError:
        logger.warning("EventRecorder: cannot open {} for append", path, exc_info=True)
        return None


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    """Write *data* as indented JSON. ``default=str`` handles non-serializable types."""
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=2, default=str, ensure_ascii=False)


def _ndarray_tail(arr, n: int) -> list:
    """Extract the last *n* values from a numpy array as a Python list.

    For 1-D arrays the trailing *n* scalars are returned. For 2-D arrays
    the trailing *n* rows are returned as nested lists.
    """
    a = np.asarray(arr)
    if a.size == 0:
        return []
    if a.ndim == 1:
        return a[-n:].tolist()
    return a[-n:].tolist()


def _to_str(val) -> str:
    """Convert *val* to a string, using ``isoformat()`` for datetime-like values."""
    if val is None:
        return ""
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val)


def _json_default(obj):
    """Fallback serializer for ``json.dumps`` — handles numpy and datetime types."""
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def _build_identity_map(bs: dict) -> Dict[tuple, dict]:
    """Build an identity-keyed map of BS markers.

    Each marker is keyed by ``(bar_idx, label, date)``.
    Entry markers: ``(bar_idx, label, color, date)`` → date at index 3.
    Exit markers: ``(bar_idx, label, color, exit_reason, date)`` → date at index 4.

    Parameters
    ----------
    bs : dict
        Dictionary with ``entry_markers`` and ``exit_markers`` lists.

    Returns
    -------
    dict
        Mapping from ``(bar_idx, label, date_str)`` to marker info dict.
    """
    result: Dict[tuple, dict] = {}

    for m in bs.get("entry_markers", []):
        key = (int(m[0]), str(m[1]), _to_str(m[3]))
        result[key] = {
            "type": "entry",
            "label": str(m[1]),
            "bar_idx": int(m[0]),
            "date": _to_str(m[3]),
        }

    for m in bs.get("exit_markers", []):
        key = (int(m[0]), str(m[1]), _to_str(m[4]))
        info: Dict[str, Any] = {
            "type": "exit",
            "label": str(m[1]),
            "bar_idx": int(m[0]),
            "date": _to_str(m[4]),
        }
        # exit_reason is at index 3 for 5-tuple exit markers
        if len(m) > 3:
            info["exit_reason"] = str(m[3])
        result[key] = info

    return result


def _build_trade_summary(step_index: int, trade_records: list) -> dict:
    """Build a trade summary dict from a list of trade records.

    Parameters
    ----------
    step_index : int
        Current step index.
    trade_records : list[dict]
        Trade record dicts with ``type`` ("long"/"short") and optional
        ``pnl`` fields.

    Returns
    -------
    dict
        Summary with event type, step, trade_count, long_count,
        short_count, and win_rate.
    """
    long_count = sum(1 for t in trade_records if t.get("type") == "long")
    short_count = len(trade_records) - long_count

    wins = sum(1 for t in trade_records if t.get("pnl", 0) > 0)
    total = len(trade_records)
    win_rate = round(wins / total, 4) if total > 0 else 0.0

    return {
        "event": "trade_summary",
        "step": step_index,
        "trade_count": total,
        "long_count": long_count,
        "short_count": short_count,
        "win_rate": win_rate,
    }
