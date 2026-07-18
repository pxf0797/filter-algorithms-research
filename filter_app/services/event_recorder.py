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
import pandas as pd
from loguru import logger


class CSVBuilder:
    """累积逐 bar 数据，end_session 时写入 CSV。

    在内存 dict 中逐行累积每个 bar 的完整状态快照（基础 OHLCV 列 +
    各视图的计算列），``end_session`` 时用 pandas 一次性写入 CSV。
    写入时通过 ``df.ffill()`` 自动前向填充粗周期数据，确保每个
    min_tf bar 都有完整的列值。
    """

    def __init__(self) -> None:
        self._rows: dict[int, dict] = {}  # bar_index -> {col_name: value}
        self._min_tf: str = ""

    def accumulate(
        self,
        bar_index: int,
        bar_timestamp: str,
        ohlcv: dict,
        views_data: dict,
    ) -> None:
        """累积一个 bar 的数据。

        Parameters
        ----------
        bar_index : int
            最小周期 bar 索引。
        bar_timestamp : str
            该 bar 的真实时间戳（ISO 8601 格式）。
        ohlcv : dict
            OHLCV 数据，包含 ``close``, ``open``, ``high``, ``low``, ``volume`` 键。
        views_data : dict
            各视图数据，键为视图名（如 ``"v0_日线"``），值为该视图的管道输出。
        """
        row: dict[str, Any] = {
            "bar_index": bar_index,
            "bar_timestamp": bar_timestamp,
            "close": ohlcv.get("close", float("nan")),
            "open": ohlcv.get("open", float("nan")),
            "high": ohlcv.get("high", float("nan")),
            "low": ohlcv.get("low", float("nan")),
            "volume": ohlcv.get("volume", float("nan")),
        }
        for view_name, view_data in views_data.items():
            cols = self._extract_view_columns(view_name, view_data)
            row.update(cols)
        self._rows[bar_index] = row

    def _extract_view_columns(
        self, view_name: str, view_data: dict
    ) -> dict:
        """从一个视图数据中提取 CSV 列。

        提取的列：
        - ``{prefix}_filtered``: filter 数组最后一个值
        - ``{prefix}_sig``: sig 数组最后一个值（-1/0/1）
        - ``{prefix}_eps``: eps 数组最后一个值
        - ``{prefix}_mu_v``: mu_v 数组最后一个值
        - ``{prefix}_sigma_v``: sigma_v 数组最后一个值
        - ``{prefix}_sig_dur``: 当前 sig 持续期
        - ``{prefix}_pair_count``: all_pairs 数量
        - ``{prefix}_trade_count``: trade_records 数量
        - ``{prefix}_bs_entry``: entry marker label (B/S/-)
        - ``{prefix}_bs_exit``: exit marker label (B/S/-)
        - ``{prefix}_pnl_long``: 做多 PnL 终值
        - ``{prefix}_pnl_short``: 做空 PnL 终值
        - ``{prefix}_long_pos``: 做多持仓 (1/0)
        - ``{prefix}_short_pos``: 做空持仓 (1/0)
        - ``{prefix}_trade``: 交易事件 (entry_long/exit_long/...)
        - ``{prefix}_trade_return``: 盈亏%
        - ``{prefix}_trade_reason``: 离场原因

        Parameters
        ----------
        view_name : str
            视图名，如 ``"v0_日线"``。前缀从名称中提取。
        view_data : dict
            该视图的管道输出数据。

        Returns
        -------
        dict
            以 ``{prefix}_{col}`` 为键的列值字典。
        """
        prefix = view_name.split("_", 1)[0]  # "v0_日线" -> "v0"
        result: dict[str, Any] = {}

        # --- filtered ---
        filtered = view_data.get("filtered")
        if filtered is not None:
            result[f"{prefix}_filtered"] = _last_value(filtered)

        # --- schmitt ---
        schmitt: dict = view_data.get("schmitt", {})
        if schmitt:
            sig = schmitt.get("sig")
            if sig is not None:
                result[f"{prefix}_sig"] = int(_last_value(sig))

            eps = schmitt.get("eps")
            if eps is not None:
                result[f"{prefix}_eps"] = _last_value(eps)

            mu_v = schmitt.get("mu_v")
            if mu_v is not None:
                result[f"{prefix}_mu_v"] = _last_value(mu_v)

            sigma_v = schmitt.get("sigma_v")
            if sigma_v is not None:
                result[f"{prefix}_sigma_v"] = _last_value(sigma_v)

            dur = schmitt.get("dur")
            if dur is not None:
                result[f"{prefix}_sig_dur"] = _last_value(dur)

        # --- all_pairs ---
        all_pairs = view_data.get("all_pairs")
        if all_pairs is not None:
            result[f"{prefix}_pair_count"] = len(all_pairs)

        # --- trade_records ---
        trade_records = view_data.get("trade_records") or []
        if trade_records is not None:
            result[f"{prefix}_trade_count"] = len(trade_records)

        # --- 局部窗口索引（用于 BS marker 和 trade 匹配） ---
        t_arr = view_data.get("t")
        view_last_idx = len(t_arr) - 1 if t_arr is not None else -1

        # --- BS markers ---
        bs_markers = view_data.get("bs_markers") or {}
        entry_label = "-"
        exit_label = "-"
        # Use <= to capture the most recent entry/exit up to view_last_idx.
        # Exact match (==) systematically misses entry markers because
        # pair_end (entry bar) almost never lands on the last bar, while
        # stop-loss exits can reach the last bar.
        for m in bs_markers.get("entry_markers", []):
            if int(m[0]) <= view_last_idx:
                entry_label = str(m[1])
        for m in bs_markers.get("exit_markers", []):
            if int(m[0]) <= view_last_idx:
                exit_label = str(m[1])
        result[f"{prefix}_bs_entry"] = entry_label
        result[f"{prefix}_bs_exit"] = exit_label

        # --- PnL & 持仓终值 ---
        long_pnl = view_data.get("long_pnl")
        if long_pnl is not None:
            result[f"{prefix}_pnl_long"] = _last_value(long_pnl)
        short_pnl = view_data.get("short_pnl")
        if short_pnl is not None:
            result[f"{prefix}_pnl_short"] = _last_value(short_pnl)
        long_mask = view_data.get("long_mask")
        if long_mask is not None:
            result[f"{prefix}_long_pos"] = int(_last_value(long_mask))
        short_mask = view_data.get("short_mask")
        if short_mask is not None:
            result[f"{prefix}_short_pos"] = int(_last_value(short_mask))

        # --- 交易事件匹配 ---
        # Use <= to find the most recent trade event at or before
        # view_last_idx.  Exact match (==) systematically misses
        # entry events because entry_idx (pair_end) almost never
        # lands on the last bar of the dataset.
        trade_val = ""
        trade_return = float("nan")
        trade_reason = ""
        for t in trade_records:
            if t.get("exit_idx") is not None and int(t["exit_idx"]) <= view_last_idx:
                trade_type = t.get("type", "long")
                trade_val = f"exit_{trade_type}"
                trade_return = t.get("return_pct", float("nan"))
                trade_reason = t.get("exit_reason", "")
            elif t.get("entry_idx") is not None and int(t["entry_idx"]) <= view_last_idx:
                trade_type = t.get("type", "long")
                trade_val = f"entry_{trade_type}"
        result[f"{prefix}_trade"] = trade_val
        result[f"{prefix}_trade_return"] = trade_return
        result[f"{prefix}_trade_reason"] = trade_reason

        return result

    def write(self, filepath: Union[str, Path]) -> None:
        """用 pandas 将累积数据写入 CSV。

        自动前向填充粗周期数据：BS 标记列中的 ``"-"`` 先替换为 NaN，
        ``ffill()`` 传播最近的非 ``"-"`` 标签后，再将剩余 NaN 还原为 ``"-"``。
        其他列（数值）中因粗周期产生的缺失值直接由 ``ffill()`` 处理。

        Parameters
        ----------
        filepath : str or Path
            输出 CSV 文件路径。
        """
        if not self._rows:
            return
        df = pd.DataFrame.from_dict(self._rows, orient="index")
        df.sort_index(inplace=True)

        # 将 BS 列中的 "-" 替换为 NaN 以便 ffill 传播实际标签
        bs_cols = [
            c for c in df.columns
            if c.endswith("_bs_entry") or c.endswith("_bs_exit")
        ]
        for col in bs_cols:
            df[col] = df[col].replace("-", np.nan)

        df.ffill(inplace=True)

        # 还原 BS 列中未被 ffill 覆盖的 NaN（首段无 marker 的 bar）
        for col in bs_cols:
            df[col] = df[col].fillna("-")

        df.to_csv(filepath, index=False)


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
        self._bs_snapshot_fp = None
        self._csv_builder = CSVBuilder()

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

        # Extract view_labels from config for consistency with ParquetStore
        view_labels: dict[str, str] = {}
        view_configs = config.get("configs", [])
        for i, cfg in enumerate(view_configs):
            view_labels[f"v{i}"] = cfg.get("tf", f"view_{i}")

        metadata: Dict[str, Any] = {
            "ticker": self.ticker,
            "session_id": self._session_id,
            "start_time": datetime.now().isoformat(),
            "config": config,
            "view_labels": view_labels,
        }
        _write_json(self._session_dir / "metadata.json", metadata)

        # Open JSONL files for append
        self._events_fp = _open_jsonl(self._session_dir / "events.jsonl")
        self._filter_tail_fp = _open_jsonl(self._session_dir / "filter_tail.jsonl")
        self._schmitt_snapshot_fp = _open_jsonl(self._session_dir / "schmitt_snapshot.jsonl")
        self._trade_summary_fp = _open_jsonl(self._session_dir / "trade_summary.jsonl")
        self._bs_snapshot_fp = _open_jsonl(self._session_dir / "bs_snapshot.jsonl")

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

        # --- CSV 累积（在 JSONL 写入之后，非阻断） ---
        try:
            bar_index: int = pipeline_output.get("bar_index", step_index)
            bar_timestamp: str = pipeline_output.get("bar_timestamp", cutoff_date)
            ohlcv_data = pipeline_output.get("ohlcv", {})
            ohlcv: dict[str, float] = {
                "close": float(ohlcv_data.get("close", float("nan"))),
                "open": float(ohlcv_data.get("open", float("nan"))),
                "high": float(ohlcv_data.get("high", float("nan"))),
                "low": float(ohlcv_data.get("low", float("nan"))),
                "volume": float(ohlcv_data.get("volume", float("nan"))),
            }
            self._csv_builder.accumulate(bar_index, bar_timestamp, ohlcv, views)
        except Exception:
            logger.warning(
                "EventRecorder: failed to accumulate CSV data for step %d",
                step_index, exc_info=True,
            )

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
                    self._schmitt_snapshot_fp, self._trade_summary_fp,
                    self._bs_snapshot_fp):
            if fp is not None:
                try:
                    fp.close()
                except Exception:
                    pass
        self._events_fp = None
        self._filter_tail_fp = None
        self._schmitt_snapshot_fp = None
        self._trade_summary_fp = None
        self._bs_snapshot_fp = None

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

        # --- CSV 写入 ---
        try:
            csv_path: Path = self._session_dir / "backtest_data.csv"
            self._csv_builder.write(csv_path)
        except Exception:
            logger.warning("EventRecorder: failed to write CSV", exc_info=True)

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

            # --- BS full snapshot ---
            self._append_jsonl(self._bs_snapshot_fp, {
                "event": "bs_snapshot",
                "step": step_index,
                "view": view_name,
                "entry": [
                    {"bar_idx": int(m[0]), "label": str(m[1]), "date": _to_str(m[3])}
                    for m in curr_bs.get("entry_markers", [])
                ],
                "exit": [
                    {"bar_idx": int(m[0]), "label": str(m[1]), "date": _to_str(m[4])}
                    for m in curr_bs.get("exit_markers", [])
                ],
            })

        # --- Filter tail ---
        filtered = view_data.get("filtered")
        if filtered is not None:
            tail = _ndarray_tail(filtered, 5)
            record = {
                "event": "filter_tail",
                "step": step_index,
                "view": view_name,
                "cutoff_date": cutoff_date,
                "tail": tail,
            }
            noisy = view_data.get("noisy")
            if noisy is not None:
                record["close_tail"] = _ndarray_tail(noisy, 5)
            self._append_jsonl(self._filter_tail_fp, record)

        # --- Schmitt snapshot ---
        schmitt = view_data.get("schmitt", {})
        sig = schmitt.get("sig") if schmitt else None
        eps = schmitt.get("eps") if schmitt else None
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
            if schmitt:
                if schmitt.get("mu_v") is not None:
                    snapshot["mu_v_tail"] = _ndarray_tail(schmitt["mu_v"], 5)
                if schmitt.get("sigma_v") is not None:
                    snapshot["sigma_v_tail"] = _ndarray_tail(schmitt["sigma_v"], 5)
                if schmitt.get("v") is not None:
                    snapshot["v_tail"] = _ndarray_tail(schmitt["v"], 5)
                if schmitt.get("a") is not None:
                    snapshot["a_tail"] = _ndarray_tail(schmitt["a"], 5)
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


def _last_value(arr) -> Any:
    """Extract the last element from an array-like as a Python scalar.

    Returns ``float("nan")`` for empty arrays.
    """
    a = np.asarray(arr)
    if a.size == 0:
        return float("nan")
    val = a.flat[-1]
    if hasattr(val, "item"):
        return val.item()
    return val


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
