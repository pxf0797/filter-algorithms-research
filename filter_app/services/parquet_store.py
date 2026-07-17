"""
ParquetStore — backtest data persistence layer.

Writes per-bar backtest pipeline outputs to segmented Parquet files
(columnar, ZSTD-compressed) and exports a CSV copy for human review.
Uses atomic writes and segmented files for crash safety.

Usage::

    store = ParquetStore(output_dir="./results", ticker="AAPL", view_configs=[...])
    session_id = store.start_session()
    for output in runner.run(start, end):
        store.append_row(
            output["bar_index"], output["bar_timestamp"],
            output["cutoff_date"], output,
        )
    store.end_session()
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# ── Schema constants ────────────────────────────────────────────────────

_VIEW_COLUMNS: list[str] = [
    "sig", "filtered", "eps",
    "pnl_long", "pnl_short",
    "long_pos", "short_pos",
    "trade", "trade_return", "trade_reason",
    "bs_entry", "bs_exit",
]

_FIXED_FIELDS: list[tuple[str, pa.DataType]] = [
    ("bar_index", pa.int32()),
    ("bar_timestamp", pa.timestamp("ns")),
]

_VIEW_COLUMN_TYPES: dict[str, pa.DataType] = {
    "sig": pa.int8(),
    "filtered": pa.float32(),
    "eps": pa.float32(),
    "pnl_long": pa.float32(),
    "pnl_short": pa.float32(),
    "long_pos": pa.bool_(),
    "short_pos": pa.bool_(),
    "trade": pa.string(),
    "trade_return": pa.float32(),
    "trade_reason": pa.string(),
    "bs_entry": pa.string(),
    "bs_exit": pa.string(),
}

_FLOAT_NA: float = float("nan")
_INT_NA: int = 0
_BOOL_NA: bool = False
_STR_NA: str = ""

_COL_DEFAULTS: dict[str, Any] = {
    "sig": _INT_NA,
    "filtered": _FLOAT_NA,
    "eps": _FLOAT_NA,
    "pnl_long": _FLOAT_NA,
    "pnl_short": _FLOAT_NA,
    "long_pos": _BOOL_NA,
    "short_pos": _BOOL_NA,
    "trade": _STR_NA,
    "trade_return": _FLOAT_NA,
    "trade_reason": _STR_NA,
    "bs_entry": _STR_NA,
    "bs_exit": _STR_NA,
}


def _build_full_schema(view_prefixes: list[str]) -> pa.Schema:
    """Build the full 50-column Parquet schema for the given view prefixes."""
    fields: list[pa.Field] = [pa.field(name, dtype) for name, dtype in _FIXED_FIELDS]
    for prefix in view_prefixes:
        for col in _VIEW_COLUMNS:
            fields.append(pa.field(f"{prefix}_{col}", _VIEW_COLUMN_TYPES[col]))
    return pa.schema(fields)


# ── ParquetStore ─────────────────────────────────────────────────────────

class ParquetStore:
    """Append-only columnar store for backtest pipeline outputs.

    Buffers rows in memory, flushes to segmented Parquet files when the
    buffer reaches *buffer_size* rows or *flush_interval_secs* elapses,
    and produces a merged Parquet + CSV pair on ``end_session``.

    Parameters
    ----------
    output_dir : str
        Root directory for backtest result storage.
    ticker : str
        Ticker symbol (e.g. ``"AAPL"``).
    view_configs : list[dict]
        View configuration dicts, one per view. Stored in metadata.json
        so downstream consumers can reproduce the backtest setup.
    """

    def __init__(
        self,
        output_dir: str,
        ticker: str,
        view_configs: list[dict],
    ) -> None:
        self._output_dir = Path(output_dir)
        self._ticker = ticker
        self._view_configs = view_configs

        # ── set in start_session ──
        self._session_dir: Optional[Path] = None
        self._session_id: str = ""
        self._view_prefixes: list[str] = []
        self._full_schema: Optional[pa.Schema] = None
        self._column_names: list[str] = []

        # ── buffer / flush ──
        self._buffer: list[dict[str, Any]] = []
        self._buffer_size: int = 100
        self._flush_interval_secs: float = 30.0

        # ── segment tracking ──
        self._part_index: int = 0
        self._last_flush_time: float = 0.0
        self._total_row_count: int = 0

    # ── Public API ──────────────────────────────────────────────────

    def start_session(self) -> str:
        """Create the session directory and write an initial metadata file.

        Session id format: ``YYYYMMDD-HHMMSS-{ticker}``.

        Returns
        -------
        str
            The new session identifier.
        """
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._session_id = f"{ts}-{self._ticker}"
        self._session_dir = (
            self._output_dir / f"{self._ticker}_{self._session_id}"
        )
        self._session_dir.mkdir(parents=True, exist_ok=True)

        # Remove stale .tmp files from a previous crashed session
        for tmp in self._session_dir.glob("*.tmp"):
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

        self._view_prefixes = [f"v{i}" for i in range(len(self._view_configs))]
        self._full_schema = _build_full_schema(self._view_prefixes)
        self._column_names = [f.name for f in self._full_schema]

        self._buffer.clear()
        self._part_index = 0
        self._last_flush_time = time.time()
        self._total_row_count = 0

        self._write_metadata(status="running")
        return self._session_id

    def append_row(
        self,
        bar_index: int,
        bar_timestamp: Any,
        cutoff_date: str,
        stage_outputs: dict,
    ) -> None:
        """Extract one 50-column row from pipeline output and buffer it.

        All exceptions inside this method are caught and logged — a single
        malformed row **never** crashes the backtest loop.  Columns for a
        view whose data is missing or ``None`` are filled with sensible
        defaults (``NaN`` for floats, ``0`` for ints, ``""`` for strings,
        ``False`` for bools).

        Parameters
        ----------
        bar_index : int
            Global min_tf bar index.
        bar_timestamp : str or datetime-like
            Bar timestamp (ISO-8601 string, ``pd.Timestamp``, or
            ``np.datetime64``).
        cutoff_date : str
            Cutoff date string (stored in the output row for reference).
        stage_outputs : dict
            One element from the list returned by
            ``BacktestRunner.run()``::

                {
                    "bar_index": int,
                    "bar_timestamp": str,
                    "cutoff_date": str,
                    "views": {
                        "v0_日线": {
                            "t": ndarray,
                            "schmitt": {"sig": ndarray, "eps": ndarray, ...},
                            "filtered": ndarray,
                            "long_pnl": ndarray,
                            "short_pnl": ndarray,
                            "long_mask": ndarray,
                            "short_mask": ndarray,
                            "trade_records": list[dict],
                            "bs_markers": {
                                "entry_markers": list[tuple],
                                "exit_markers": list[tuple],
                            },
                        },
                        ...
                    },
                }
        """
        if self._session_dir is None:
            return

        try:
            row = self._extract_row(bar_index, bar_timestamp, stage_outputs)
            self._buffer.append(row)
            self._maybe_flush()
        except Exception:
            print(
                f"WARNING: ParquetStore.append_row failed for "
                f"bar_index={bar_index}, cutoff_date={cutoff_date}"
            )

    def flush(self) -> None:
        """Write all buffered rows to a new segmented Parquet file.

        Uses an atomic write (``.tmp`` → ``os.replace``) so a crash
        during write never leaves a half-written file.
        Safe to call when the buffer is empty (no-op).
        """
        if not self._buffer or self._session_dir is None:
            return

        self._part_index += 1
        part_name = f"part_{self._part_index:04d}.parquet"
        tmp_path = self._session_dir / f"{part_name}.tmp"
        final_path = self._session_dir / part_name

        try:
            table = self._buffer_to_table()
            pq.write_table(
                table, str(tmp_path),
                compression="zstd", compression_level=3,
            )
            os.replace(str(tmp_path), str(final_path))
        except Exception:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise

        self._total_row_count += len(self._buffer)
        self._buffer.clear()
        self._last_flush_time = time.time()

    def end_session(self) -> None:
        """Finalise the session: flush remaining rows, merge segments,
        export CSV, and update metadata with final statistics.
        """
        if self._session_dir is None:
            return

        # Final flush of anything still in the buffer
        self.flush()

        try:
            self._merge_parts_and_export_csv()
        except Exception:
            print("WARNING: ParquetStore.end_session merge/export failed")

        self._write_metadata(status="completed")

    # ── Row extraction ──────────────────────────────────────────────

    def _extract_row(
        self,
        bar_index: int,
        bar_timestamp: Any,
        stage_outputs: dict,
    ) -> dict[str, Any]:
        """Build a flat 50-column dict for one pipeline step.

        Parameters
        ----------
        bar_index : int
            Global min_tf bar index.
        bar_timestamp : Any
            Raw timestamp value, converted to ``np.datetime64[ns]``.
        stage_outputs : dict
            Pipeline output for one step.

        Returns
        -------
        dict
            Mapping of column name → scalar value for all 50 columns.
        """
        row: dict[str, Any] = {
            "bar_index": bar_index,
            "bar_timestamp": _to_timestamp_ns(bar_timestamp),
        }

        views: dict[str, dict] = stage_outputs.get("views", {})

        # Iterate views in deterministic order (v0, v1, v2, v3)
        sorted_keys = sorted(views.keys(), key=lambda k: k.split("_", 1)[0])

        for view_key in sorted_keys:
            prefix = view_key.split("_", 1)[0]
            view_data = views.get(view_key)

            if view_data is None:
                for col in _VIEW_COLUMNS:
                    row[f"{prefix}_{col}"] = _COL_DEFAULTS[col]
                continue

            try:
                extracted = self._extract_view_columns(prefix, view_data)
                row.update(extracted)
            except Exception:
                print(
                    f"WARNING: ParquetStore: failed to extract columns "
                    f"for view {view_key} at bar_index={bar_index}"
                )
                for col in _VIEW_COLUMNS:
                    row[f"{prefix}_{col}"] = _COL_DEFAULTS[col]

        return row

    @staticmethod
    def _extract_view_columns(
        prefix: str, view_data: dict,
    ) -> dict[str, Any]:
        """Extract the 12 per-view columns from a single view's pipeline output.

        Parameters
        ----------
        prefix : str
            View prefix, e.g. ``"v0"``.
        view_data : dict
            View pipeline output.  Expected keys: ``schmitt``, ``filtered``,
            ``long_pnl``, ``short_pnl``, ``long_mask``, ``short_mask``,
            ``trade_records``, ``bs_markers``, ``t``.

        Returns
        -------
        dict
            ``{prefix}_{col}`` → scalar for each of the 12 view columns.
        """
        result: dict[str, Any] = {}
        schmitt: Optional[dict] = view_data.get("schmitt")

        # ── Array-backed columns: take [-1] ──────────────────────

        # sig
        if schmitt is not None:
            sig_arr = schmitt.get("sig")
            result[f"{prefix}_sig"] = (
                _last_int(sig_arr) if sig_arr is not None else _INT_NA
            )
        else:
            result[f"{prefix}_sig"] = _INT_NA

        # filtered
        filtered = view_data.get("filtered")
        result[f"{prefix}_filtered"] = (
            _last_float(filtered) if filtered is not None else _FLOAT_NA
        )

        # eps
        if schmitt is not None:
            eps_arr = schmitt.get("eps")
            result[f"{prefix}_eps"] = (
                _last_float(eps_arr) if eps_arr is not None else _FLOAT_NA
            )
        else:
            result[f"{prefix}_eps"] = _FLOAT_NA

        # pnl_long
        long_pnl = view_data.get("long_pnl")
        result[f"{prefix}_pnl_long"] = (
            _last_float(long_pnl) if long_pnl is not None else _FLOAT_NA
        )

        # pnl_short
        short_pnl = view_data.get("short_pnl")
        result[f"{prefix}_pnl_short"] = (
            _last_float(short_pnl) if short_pnl is not None else _FLOAT_NA
        )

        # long_pos
        long_mask = view_data.get("long_mask")
        result[f"{prefix}_long_pos"] = (
            bool(_last_scalar(long_mask))
            if long_mask is not None
            else _BOOL_NA
        )

        # short_pos
        short_mask = view_data.get("short_mask")
        result[f"{prefix}_short_pos"] = (
            bool(_last_scalar(short_mask))
            if short_mask is not None
            else _BOOL_NA
        )

        # ── Trade columns: traverse, match exit_idx ───────────────

        t_arr = view_data.get("t")
        view_last_idx: int = len(t_arr) - 1 if t_arr is not None else -1

        trade_records: list[dict] = view_data.get("trade_records") or []
        trade_val = _STR_NA
        trade_return = _FLOAT_NA
        trade_reason = _STR_NA

        for trade in trade_records:
            exit_idx = trade.get("exit_idx")
            entry_idx = trade.get("entry_idx")
            tt = trade.get("type", "")

            if exit_idx is not None and int(exit_idx) == view_last_idx:
                trade_val = f"exit_{tt}"
                trade_return = float(trade.get("return_pct", _FLOAT_NA))
                trade_reason = str(trade.get("exit_reason", ""))
                break
            elif entry_idx is not None and int(entry_idx) == view_last_idx:
                trade_val = f"entry_{tt}"
                # entry has no realised return yet
                trade_return = _FLOAT_NA
                trade_reason = _STR_NA
                break

        result[f"{prefix}_trade"] = trade_val
        result[f"{prefix}_trade_return"] = trade_return
        result[f"{prefix}_trade_reason"] = trade_reason

        # ── BS marker columns: traverse, match bar_idx ────────────

        bs_markers: Optional[dict] = view_data.get("bs_markers")
        bs_entry = _STR_NA
        bs_exit = _STR_NA

        if bs_markers is not None:
            # entry_markers: (bar_idx, label, color, date)
            for m in bs_markers.get("entry_markers", []):
                if int(m[0]) == view_last_idx:
                    bs_entry = str(m[1])
                    break
            # exit_markers: (bar_idx, label, color, exit_reason, date)
            for m in bs_markers.get("exit_markers", []):
                if int(m[0]) == view_last_idx:
                    bs_exit = str(m[1])
                    break

        result[f"{prefix}_bs_entry"] = bs_entry
        result[f"{prefix}_bs_exit"] = bs_exit

        return result

    # ── Flush helpers ───────────────────────────────────────────────

    def _maybe_flush(self) -> None:
        """Flush if the row buffer is full or the time threshold has passed."""
        if len(self._buffer) >= self._buffer_size:
            self.flush()
        elif (
            self._buffer
            and (time.time() - self._last_flush_time) > self._flush_interval_secs
        ):
            self.flush()

    def _buffer_to_table(self) -> pa.Table:
        """Convert the current in-memory buffer to a pyarrow Table.

        Each dict in the buffer must have keys matching ``_column_names``.
        Returns a table typed according to ``_full_schema``.
        """
        # Pivot: dict[col_name] → list of values
        columns: dict[str, list] = {name: [] for name in self._column_names}
        for row in self._buffer:
            for name in self._column_names:
                columns[name].append(row.get(name))

        # Build typed arrays matching the schema
        arrays: list[pa.Array] = []
        for field in self._full_schema:
            col_name = field.name
            col_type = field.type
            values = columns[col_name]
            try:
                arr = pa.array(values, type=col_type)
            except (pa.ArrowInvalid, pa.ArrowTypeError, TypeError):
                # Coerce by building untyped then casting
                arr = pa.array(values)
                arr = arr.cast(col_type, safe=False)
            arrays.append(arr)

        return pa.Table.from_arrays(arrays, schema=self._full_schema)

    # ── Merge & export ─────────────────────────────────────────────

    def _merge_parts_and_export_csv(self) -> None:
        """Concatenate all part files into a single Parquet and export CSV.

        Part files are deleted after a successful merge so the session
        directory only contains the final ``backtest_result.parquet``.
        """
        part_files = sorted(self._session_dir.glob("part_*.parquet"))
        if not part_files:
            return

        tables: list[pa.Table] = []
        for pf in part_files:
            tables.append(pq.read_table(str(pf)))

        merged = pa.concat_tables(tables)

        # Atomic write of the merged Parquet
        merged_path = self._session_dir / "backtest_result.parquet"
        tmp_parquet = self._session_dir / "backtest_result.parquet.tmp"
        pq.write_table(
            merged, str(tmp_parquet),
            compression="zstd", compression_level=3,
        )
        os.replace(str(tmp_parquet), str(merged_path))

        # CSV export
        csv_path = self._session_dir / "backtest_result.csv"
        df = merged.to_pandas()
        df.to_csv(str(csv_path), index=False)

        # Remove part files now that the merged file exists
        for pf in part_files:
            try:
                pf.unlink()
            except OSError:
                pass

    # ── Metadata persistence ───────────────────────────────────────

    def _write_metadata(self, status: str) -> None:
        """Write (or update) ``metadata.json``.

        The file is written atomically (``.tmp`` → ``os.replace``).
        When *status* is ``"completed"`` any existing fields (such as
        ``start_time`` from the initial write) are preserved.

        Parameters
        ----------
        status : str
            ``"running"`` (called from ``start_session``) or
            ``"completed"`` (called from ``end_session``).
        """
        if self._session_dir is None:
            return

        meta_path = self._session_dir / "metadata.json"

        # Preserve fields written by a previous call (e.g. start_time)
        existing: dict[str, Any] = {}
        if meta_path.exists():
            try:
                existing = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        view_labels: dict[str, str] = {}
        for i, cfg in enumerate(self._view_configs):
            view_labels[f"v{i}"] = cfg.get("tf", f"view_{i}")

        meta: dict[str, Any] = {
            **existing,
            "format_version": "1.0",
            "schema_version": "3.2",
            "status": status,
            "session_id": self._session_id,
            "ticker": self._ticker,
            "views": self._view_prefixes,
            "view_labels": view_labels,
            "parquet_row_count": self._total_row_count,
            "parquet_compression": "zstd",
            "parquet_compression_level": 3,
            "column_names": self._column_names,
            "view_configs": {
                f"v{i}": cfg for i, cfg in enumerate(self._view_configs)
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        if status == "running":
            meta["start_time"] = datetime.now(timezone.utc).isoformat()
        else:
            meta["end_time"] = datetime.now(timezone.utc).isoformat()
            meta["step_count"] = self._total_row_count

        # Atomic write
        tmp_path = self._session_dir / "metadata.json.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, default=str, ensure_ascii=False)
        os.replace(str(tmp_path), str(meta_path))


# ── Module-level helpers ─────────────────────────────────────────────────

def _to_timestamp_ns(val: Any) -> Optional[np.datetime64]:
    """Convert a timestamp value to nanosecond-resolution ``np.datetime64``.

    Handles ISO-8601 strings, ``pd.Timestamp``, ``datetime.datetime``,
    and ``np.datetime64`` inputs.  Returns ``None`` for unparseable values
    (pyarrow will store them as null).
    """
    if val is None:
        return None
    try:
        ts = pd.Timestamp(val)
        # .as_unit("ns") returns a new Timestamp; .to_datetime64() gives np.datetime64
        return ts.as_unit("ns").to_datetime64()
    except (ValueError, TypeError, OverflowError):
        return None


def _last_scalar(arr: Any) -> Any:
    """Return the last element of an array-like as a Python scalar.

    Returns ``None`` for empty or unparseable arrays.
    """
    try:
        a = np.asarray(arr)
    except (ValueError, TypeError):
        return None
    if a.size == 0:
        return None
    val = a.flat[-1]
    if hasattr(val, "item"):
        return val.item()
    return val


def _last_float(arr: Any) -> float:
    """Return the last element as a ``float``, or ``NaN`` on failure."""
    val = _last_scalar(arr)
    if val is None:
        return float("nan")
    try:
        return float(val)
    except (ValueError, TypeError):
        return float("nan")


def _last_int(arr: Any) -> int:
    """Return the last element as an ``int``, or ``0`` on failure."""
    val = _last_scalar(arr)
    if val is None:
        return 0
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0
