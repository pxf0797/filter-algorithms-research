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
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from loguru import logger

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
    ("close", pa.float32()),
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
_STR_NA = None

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
    """Build the full Parquet schema for the given view prefixes.

    The schema has 3 fixed columns (bar_index, bar_timestamp, close) plus
    12 columns per view (信号层 3 + PnL层 2 + 持仓层 2 + 交易层 3 + BS层 2),
    for a total of 3 + (4 * 12) = 51 columns."""
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

    _MAX_BUFFER_MB: int = 200  # hard memory limit for in-memory buffer

    def __init__(
        self,
        output_dir: str,
        ticker: str,
        view_configs: list[dict],
        save_debug_data: bool = True,
        track_pnl_from_price: bool = False,
    ) -> None:
        self._output_dir = Path(output_dir)
        self._ticker = ticker
        self._view_configs = view_configs
        self._save_debug_data = save_debug_data
        self._track_pnl_from_price = track_pnl_from_price

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
        self._buffer_memory_bytes: int = 0

    # ── Public API ──────────────────────────────────────────────────

    @property
    def output_dir(self) -> Path:
        """Public read-only access to the output directory (session dir if started, else root)."""
        return self._session_dir or self._output_dir

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
        self._buffer_memory_bytes = 0
        self._last_pnl: dict[str, float] = {}

        # ── Price-based PnL tracking state (当 track_pnl_from_price=True 时生效) ──
        self._prev_pos: dict[str, bool] = {}          # 上一条 bar 的持仓状态
        self._entry_capital: dict[str, float] = {}     # 当前持仓的入场资本
        self._entry_filtered: dict[str, float] = {}    # 当前持仓的入场滤波价格

        # ── Pending events for post-hoc trade/BS matching ──
        # Key: "v0:2026-04-01", Value: {"v0_trade": "exit_long", ...}
        self._pending_events: dict[str, dict[str, Any]] = {}

        # Disable auto-flush so all rows stay in buffer until end_session
        self._saved_buffer_size = self._buffer_size
        self._buffer_size = 10_000_000  # effectively infinite
        self._max_buffer_mb = self._MAX_BUFFER_MB  # P0-7: hard cap

        self._write_metadata(status="running")
        return self._session_id

    def append_row(
        self,
        bar_index: int,
        bar_timestamp: Any,
        cutoff_date: str,
        stage_outputs: dict,
    ) -> None:
        """Extract one row from pipeline output and buffer it.

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
            self._buffer_memory_bytes += sys.getsizeof(row)
            self._accumulate_events(stage_outputs)

            # Memory pressure tracking with tiered logging
            max_bytes = self._max_buffer_mb * 1024 * 1024
            mem_mb = self._buffer_memory_bytes / (1024 * 1024)
            row_count = len(self._buffer)
            if self._buffer_memory_bytes > max_bytes * 0.9:
                logger.warning(
                    "ParquetStore buffer 内存 {:.1f}MB / {}MB ({} 行)，强制 flush",
                    mem_mb, self._max_buffer_mb, row_count,
                )
                self.flush()
            elif self._buffer_memory_bytes > max_bytes * 0.75:
                logger.warning(
                    "ParquetStore buffer 内存 {:.1f}MB / {}MB ({} 行)",
                    mem_mb, self._max_buffer_mb, row_count,
                )
            elif self._buffer_memory_bytes > max_bytes * 0.5:
                logger.info(
                    "ParquetStore buffer 内存 {:.1f}MB / {}MB ({} 行)",
                    mem_mb, self._max_buffer_mb, row_count,
                )

            self._maybe_flush()
        except Exception:
            logger.warning(
                "ParquetStore.append_row failed for "
                "bar_index={}, cutoff_date={}",
                bar_index, cutoff_date,
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

            # Runtime schema validation before writing
            issues = validate_schema(table, self._full_schema)
            if issues:
                logger.warning(
                    "ParquetStore schema mismatch in "
                    "part_{:04d}:",
                    self._part_index,
                )
                for issue in issues:
                    logger.warning("  - {}", issue)

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
        self._buffer_memory_bytes = 0
        self._last_flush_time = time.time()

    def end_session(self) -> None:
        """Finalise the session: apply pending trade/BS events, flush
        remaining rows, merge segments, export CSV, and update metadata
        with final statistics.
        """
        if self._session_dir is None:
            return

        # Apply pending trade/BS events to buffered rows before flushing
        self._apply_pending_events()

        # Restore original buffer size before final flush
        self._buffer_size = getattr(self, "_saved_buffer_size", 100)

        # Final flush of anything still in the buffer
        self.flush()

        try:
            self._merge_parts_and_export_csv()
        except Exception:
            logger.warning("ParquetStore.end_session merge/export failed")

        self._write_metadata(status="completed")

    # ── Row extraction ──────────────────────────────────────────────

    def _extract_row(
        self,
        bar_index: int,
        bar_timestamp: Any,
        stage_outputs: dict,
    ) -> dict[str, Any]:
        """Build a flat dict for one pipeline step.

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
            Mapping of column name → scalar value for all columns.
        """
        row: dict[str, Any] = {
            "bar_index": bar_index,
            "bar_timestamp": _to_timestamp_ns(bar_timestamp),
            "close": float(stage_outputs.get("ohlcv", {}).get("close", float("nan"))),
        }

        # Compute the current bar's date for date-based BS/trade matching
        bar_date: Any = None
        try:
            bar_date = pd.Timestamp(bar_timestamp)
        except Exception:
            pass

        views: dict[str, dict] = stage_outputs.get("views", {})

        # Iterate views in deterministic order (v0, v1, v2, v3)
        sorted_keys = sorted(views.keys(), key=lambda k: k.split("_", 1)[0])

        for view_key in sorted_keys:
            prefix = view_key.split("_", 1)[0]
            view_data = views.get(view_key)

            if view_data is None:
                for col in _VIEW_COLUMNS:
                    row[f"{prefix}_{col}"] = _COL_DEFAULTS[col]
            else:
                try:
                    extracted = self._extract_view_columns(prefix, view_data, bar_date=bar_date)
                    row.update(extracted)
                except Exception:
                    logger.warning(
                        "ParquetStore: failed to extract columns "
                        "for view {} at bar_index={}",
                        view_key, bar_index,
                    )
                    for col in _VIEW_COLUMNS:
                        row[f"{prefix}_{col}"] = _COL_DEFAULTS[col]

            # ── PnL freeze / price-based tracking ──
            long_pos = row.get(f"{prefix}_long_pos", False)
            short_pos = row.get(f"{prefix}_short_pos", False)
            for pnl_key, pos_flag, is_short in [
                ("pnl_long", long_pos, False), ("pnl_short", short_pos, True)
            ]:
                col_name = f"{prefix}_{pnl_key}"
                state_key = f"{prefix}_{pnl_key}"
                prev_pos = self._prev_pos.get(state_key, False)

                if self._track_pnl_from_price:
                    # ── 基于滤波价格的 PnL 追踪 ──
                    filtered_val = row.get(f"{prefix}_filtered", float("nan"))
                    frozen = self._last_pnl.get(state_key, 100.0)

                    if pos_flag and not prev_pos:
                        # 入场：PnL = 冻结资本（消除入场跳跃）
                        self._entry_capital[state_key] = frozen
                        self._entry_filtered[state_key] = filtered_val
                        row[col_name] = frozen
                        self._last_pnl[state_key] = frozen
                    elif pos_flag:
                        # 持仓中：PnL = 入场资本 * 滤波价格变化
                        entry_cap = self._entry_capital.get(state_key, frozen)
                        entry_filt = self._entry_filtered.get(state_key, filtered_val)
                        if (
                            not np.isnan(filtered_val) and filtered_val > 0
                            and not np.isnan(entry_filt) and entry_filt > 0
                        ):
                            if is_short:
                                # 空头：价格下跌则 PnL 上涨
                                row[col_name] = entry_cap * (
                                    2.0 - filtered_val / entry_filt
                                )
                            else:
                                # 多头：价格上涨则 PnL 上涨
                                row[col_name] = entry_cap * (
                                    filtered_val / entry_filt
                                )
                            self._last_pnl[state_key] = row[col_name]
                        # 若价格无效，保持 frozen 值不变
                    else:
                        # 非持仓：冻结 PnL
                        row[col_name] = frozen
                else:
                    # ── 原有行为：仅冻结 ──
                    if pos_flag:
                        self._last_pnl[state_key] = row.get(col_name, 100.0)
                    else:
                        row[col_name] = self._last_pnl.get(state_key, 100.0)

                self._prev_pos[state_key] = pos_flag

        return row

    @staticmethod
    def _extract_view_columns(
        prefix: str, view_data: dict,
        bar_date: Any = None,
    ) -> dict[str, Any]:
        """Extract the 12 per-view columns from a single view's pipeline output.

        Parameters
        ----------
        prefix : str
            View prefix, e.g. ``"v0"``.
        view_data : dict
            View pipeline output.  Expected keys: ``schmitt``, ``filtered``,
            ``long_pnl``, ``short_pnl``, ``long_mask``, ``short_mask``,
            ``trade_records``, ``bs_markers``, ``t``, ``dates``.
        bar_date : pd.Timestamp or None
            The current bar's date (from the backtest loop's bar_timestamp).
            Used for date-based BS marker and trade exit matching.

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

        # ── Position state: computed from view's OWN trade_records ──
        # P4 fix: previously derived from cross-TF aligned masks which could
        # contradict the view's own PnL direction. Now position reflects the
        # view's actual trading state, consistent with _pnl_long / _pnl_short.

        t_arr = view_data.get("t")
        view_last_idx: int = len(t_arr) - 1 if t_arr is not None else -1

        long_pos: bool = _BOOL_NA
        short_pos: bool = _BOOL_NA
        pos_trades: list[dict] = view_data.get("trade_records") or []
        for tr in pos_trades:
            entry_idx = tr.get("entry_idx")
            exit_idx = tr.get("exit_idx")
            tt = tr.get("type", "")
            reason = str(tr.get("exit_reason", ""))
            # Active trade: entered at or before current bar and still open
            if entry_idx is not None and int(entry_idx) <= view_last_idx:
                if (
                    exit_idx is None
                    or int(exit_idx) > view_last_idx
                    or reason == "eod"
                ):
                    if tt == "long":
                        long_pos = True
                    elif tt == "short":
                        short_pos = True
        result[f"{prefix}_long_pos"] = long_pos
        result[f"{prefix}_short_pos"] = short_pos

        # ── Trade & BS columns: filled by post-hoc join in end_session ──
        # Per-bar matching (exit_idx==view_last_idx, date matching) fails
        # because trade/BS events at earlier bars in the sliding window
        # never match the current bar's index or date.  Instead, events are
        # accumulated in append_row and applied to the buffer in end_session.

        result[f"{prefix}_trade"] = _STR_NA
        result[f"{prefix}_trade_return"] = _FLOAT_NA
        result[f"{prefix}_trade_reason"] = _STR_NA
        result[f"{prefix}_bs_entry"] = _STR_NA
        result[f"{prefix}_bs_exit"] = _STR_NA

        return result

    # ── Event accumulation & post-hoc join ─────────────────────────────

    def _accumulate_events(self, stage_outputs: dict) -> None:
        """Accumulate trade exit/entry and BS marker events keyed by date.

        Called from ``append_row`` after extracting the row.  Events are
        stored in ``self._pending_events`` keyed by ``"v0:2026-04-01"``
        and applied to the buffer in ``end_session``.

        Trade exits take precedence over entries (last exit on a date
        wins).  BS markers overwrite earlier values for the same date.
        """
        views: dict[str, dict] = stage_outputs.get("views", {})
        for view_key, view_data in views.items():
            prefix = view_key.split("_", 1)[0]
            dates = view_data.get("dates")
            if dates is None or len(dates) == 0:
                continue

            # ── Trade events ──────────────────────────────────────
            for trade in view_data.get("trade_records", []):
                exit_idx = trade.get("exit_idx")
                entry_idx = trade.get("entry_idx")
                tt = trade.get("type", "")
                reason = str(trade.get("exit_reason", ""))

                # Exit event (non-eod only)
                if exit_idx is not None and reason != "eod":
                    try:
                        exit_date = _normalize_date(dates[int(exit_idx)])
                        if exit_date:
                            key = f"{prefix}:{exit_date}"
                            existing = self._pending_events.get(key, {})
                            existing[f"{prefix}_trade"] = f"exit_{tt}"
                            existing[f"{prefix}_trade_return"] = float(
                                trade.get("return_pct", _FLOAT_NA)
                            )
                            existing[f"{prefix}_trade_reason"] = reason
                            self._pending_events[key] = existing
                    except Exception:
                        pass

                # Entry event (only if entry_idx is set and
                # no exit has been recorded for this date yet)
                if entry_idx is not None:
                    try:
                        entry_date = _normalize_date(dates[int(entry_idx)])
                        if entry_date:
                            key = f"{prefix}:{entry_date}"
                            existing = self._pending_events.get(key, {})
                            trade_col = existing.get(f"{prefix}_trade", "")
                            if not trade_col:
                                existing[f"{prefix}_trade"] = f"entry_{tt}"
                                existing.setdefault(
                                    f"{prefix}_trade_return", _FLOAT_NA
                                )
                                existing.setdefault(
                                    f"{prefix}_trade_reason", _STR_NA
                                )
                                self._pending_events[key] = existing
                    except Exception:
                        pass

            # ── BS marker events ───────────────────────────────────
            bs_markers = view_data.get("bs_markers")
            if not bs_markers:
                continue

            for m in bs_markers.get("entry_markers", []):
                marker_date = m[3] if len(m) > 3 else None
                if marker_date is None:
                    continue
                try:
                    md = _normalize_date(marker_date)
                    if md:
                        key = f"{prefix}:{md}"
                        existing = self._pending_events.get(key, {})
                        existing[f"{prefix}_bs_entry"] = str(m[1])
                        self._pending_events[key] = existing
                except Exception:
                    pass

            for m in bs_markers.get("exit_markers", []):
                marker_date = m[4] if len(m) > 4 else None
                if marker_date is None:
                    continue
                try:
                    md = _normalize_date(marker_date)
                    if md:
                        key = f"{prefix}:{md}"
                        existing = self._pending_events.get(key, {})
                        existing[f"{prefix}_bs_exit"] = str(m[1])
                        self._pending_events[key] = existing
                except Exception:
                    pass

    def _apply_pending_events(self) -> None:
        """Apply accumulated trade/BS events to the in-memory buffer.

        For each row in ``self._buffer``, the ``bar_timestamp`` is
        converted to a date string and looked up in
        ``self._pending_events`` per view prefix.  Matching columns
        are overwritten in-place.
        """
        if not self._pending_events:
            return

        for row in self._buffer:
            bar_ts = row.get("bar_timestamp")
            if bar_ts is None:
                continue
            bar_date = _normalize_date(bar_ts)
            if not bar_date:
                continue

            for prefix in self._view_prefixes:
                key = f"{prefix}:{bar_date}"
                events = self._pending_events.get(key)
                if events is None:
                    continue

                trade_col = f"{prefix}_trade"
                if trade_col in events:
                    row[trade_col] = events[trade_col]
                trade_ret_col = f"{prefix}_trade_return"
                if trade_ret_col in events:
                    row[trade_ret_col] = events[trade_ret_col]
                trade_reason_col = f"{prefix}_trade_reason"
                if trade_reason_col in events:
                    row[trade_reason_col] = events[trade_reason_col]
                bs_entry_col = f"{prefix}_bs_entry"
                if bs_entry_col in events:
                    row[bs_entry_col] = events[bs_entry_col]
                bs_exit_col = f"{prefix}_bs_exit"
                if bs_exit_col in events:
                    row[bs_exit_col] = events[bs_exit_col]

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

        Pre-allocates numpy arrays matching the schema types for zero-copy
        conversion, avoiding the memory overhead of dict-of-lists pivot.
        """
        n = len(self._buffer)

        # Separate columns by storage strategy:
        #   - numpy arrays for fixed-width types (zero-copy → pyarrow)
        #   - Python lists for variable-width strings (unavoidable)
        np_cols: dict[str, np.ndarray] = {}
        str_cols: dict[str, list] = {}

        for field in self._full_schema:
            name = field.name
            if pa.types.is_string(field.type):
                str_cols[name] = [None] * n
            elif pa.types.is_timestamp(field.type):
                np_cols[name] = np.empty(n, dtype="datetime64[ns]")
            elif pa.types.is_boolean(field.type):
                np_cols[name] = np.empty(n, dtype=np.bool_)
            elif pa.types.is_floating(field.type):
                np_cols[name] = np.empty(n, dtype=np.float32)
            else:
                np_cols[name] = np.empty(n, dtype=np.int32)

        # Fill arrays by row index
        for i, row in enumerate(self._buffer):
            for name in self._column_names:
                val = row.get(name)
                if val is None:
                    if name in np_cols:
                        dtype = np_cols[name].dtype
                        if np.issubdtype(dtype, np.integer):
                            val = 0
                        elif np.issubdtype(dtype, np.floating):
                            val = np.nan
                        elif np.issubdtype(dtype, np.bool_):
                            val = False
                        elif np.issubdtype(dtype, np.datetime64):
                            val = np.datetime64("NaT")
                        else:
                            val = 0
                        logger.debug(
                            "_buffer_to_table: column={!r} row={} "
                            "is None, using default={!r}",
                            name, i, val,
                        )
                    else:
                        # str_cols: None is fine (pyarrow handles null)
                        str_cols[name][i] = val
                        continue
                if name in np_cols:
                    np_cols[name][i] = val
                else:
                    str_cols[name][i] = val

        # Build pyarrow arrays (zero-copy from numpy where possible)
        arrays: list[pa.Array] = []
        for field in self._full_schema:
            name = field.name
            col_type = field.type
            if name in np_cols:
                try:
                    arr = pa.array(np_cols[name], type=col_type)
                except (pa.ArrowInvalid, pa.ArrowTypeError, TypeError):
                    arr = pa.array(np_cols[name])
                    arr = arr.cast(col_type, safe=False)
            else:
                try:
                    arr = pa.array(str_cols[name], type=col_type)
                except (pa.ArrowInvalid, pa.ArrowTypeError, TypeError):
                    arr = pa.array(str_cols[name])
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
            table = pq.read_table(str(pf))
            issues = validate_schema(table, self._full_schema)
            if issues:
                logger.warning(
                    "ParquetStore schema mismatch in "
                    "part file {}:",
                    pf.name,
                )
                for issue in issues:
                    logger.warning("  - {}", issue)
            tables.append(table)

        merged = pa.concat_tables(tables)

        # Validate merged table
        merged_issues = validate_schema(merged, self._full_schema)
        if merged_issues:
            logger.warning("ParquetStore schema mismatch in merged table:")
            for issue in merged_issues:
                logger.warning("  - {}", issue)

        # Atomic write of the merged Parquet
        merged_path = self._session_dir / "backtest_result.parquet"
        tmp_parquet = self._session_dir / "backtest_result.parquet.tmp"
        try:
            pq.write_table(
                merged, str(tmp_parquet),
                compression="zstd", compression_level=3,
            )
            os.replace(str(tmp_parquet), str(merged_path))
        except Exception:
            if tmp_parquet.exists():
                try:
                    tmp_parquet.unlink()
                except OSError:
                    pass
            raise

        # CSV export (debug data only)
        if self._save_debug_data:
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
            "schema_version": "3.4",
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
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2, default=str, ensure_ascii=False)
            os.replace(str(tmp_path), str(meta_path))
        except Exception:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise


# ── Schema validation ──────────────────────────────────────────────────


def validate_schema(table: pa.Table, expected_schema: pa.Schema) -> list[str]:
    """Validate that a PyArrow table matches the expected schema.

    Parameters
    ----------
    table : pa.Table
        The table to validate.
    expected_schema : pa.Schema
        The expected schema.

    Returns
    -------
    list[str]
        List of issue descriptions.  Empty list means the table is valid.
    """
    issues: list[str] = []

    expected_names = set(expected_schema.names)
    actual_names = set(table.column_names)

    # Column count
    if len(table.column_names) != len(expected_schema.names):
        issues.append(
            f"Column count mismatch: got {len(table.column_names)}, "
            f"expected {len(expected_schema.names)}"
        )

    # Missing columns
    missing = expected_names - actual_names
    if missing:
        issues.append(f"Missing columns: {sorted(missing)}")

    # Extra columns
    extra = actual_names - expected_names
    if extra:
        issues.append(f"Extra columns: {sorted(extra)}")

    # Type mismatches (only for columns that exist in both)
    for col_name in sorted(expected_names & actual_names):
        expected_type = expected_schema.field(col_name).type
        actual_type = table.schema.field(col_name).type
        if actual_type != expected_type:
            issues.append(
                f"Type mismatch for '{col_name}': "
                f"got {actual_type}, expected {expected_type}"
            )

    return issues


def load_parquet(path: str, expected_schema: pa.Schema) -> pa.Table:
    """Load a Parquet file and validate it against an expected schema.

    Parameters
    ----------
    path : str
        Path to the Parquet file.
    expected_schema : pa.Schema
        Expected schema to validate the loaded table against.

    Returns
    -------
    pa.Table
        The loaded table.

    Raises
    ------
    ValueError
        If the loaded table fails schema validation.
    """
    table = pq.read_table(str(path))
    issues = validate_schema(table, expected_schema)
    if issues:
        raise ValueError(
            f"Schema validation failed for {path}:\n"
            + "\n".join(f"  - {i}" for i in issues)
        )
    return table


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


def _current_bar_date(view_data: dict) -> Any:
    """Extract the current bar's date from the view's ``dates`` array.

    Returns the last element of ``dates`` as a ``pd.Timestamp``, or
    ``None`` if ``dates`` is missing / empty / unparseable.

    Used by BS marker date-based matching.
    """
    dates = view_data.get("dates")
    if dates is None:
        return None
    try:
        if hasattr(dates, "iloc"):
            val = dates.iloc[-1]
        elif hasattr(dates, "__getitem__"):
            val = dates[-1]
        else:
            return None
        return pd.Timestamp(val)
    except Exception:
        return None


def _date_matches(marker_date: Any, current_date: Any) -> bool:
    """Compare a BS marker's date against the current bar's date.

    Both are normalised to ``pd.Timestamp`` for comparison.
    Normalised to **day** granularity so that intra-day min_tf bars
    match daily-view BS markers correctly (e.g. a 15-min bar at
    ``2026-04-01T17:40`` matches a daily marker at ``2026-04-01``).

    Returns ``False`` on any parse failure — a missing date never
    produces a false-positive match.
    """
    if marker_date is None or current_date is None:
        return False
    try:
        m = pd.Timestamp(marker_date)
        c = pd.Timestamp(current_date)
        return m.date() == c.date()
    except Exception:
        return False


def _normalize_date(val: Any) -> Optional[str]:
    """Convert any date-like value to a ``"YYYY-MM-DD"`` string.

    Returns ``None`` on parse failure.  Used by the post-hoc event
    join to build stable dict keys across data sources.
    """
    if val is None:
        return None
    try:
        ts = pd.Timestamp(val)
        return ts.strftime("%Y-%m-%d")
    except Exception:
        return None
