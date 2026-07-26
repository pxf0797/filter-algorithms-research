"""
Pipeline data capture for backtest debugging.

Feature-flag gated via PIPELINE_CAPTURE=1 env var. Captures per-step,
per-view pipeline stage outputs (filters, schmitt, pairs, trades, pnl,
holding masks, BS markers) to a timestamped session directory.

Usage::

    capture = PipelineCapture(ticker="AAPL", config={...})
    session_id = capture.start_session()
    # ... for each step in backtest loop:
    capture.capture_step(step_index=42, cutoff_date="2025-06-15",
                         views_data={view_name: PipelineStageData(...)})
    summary = capture.end_session()
"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd


class PipelineStageData:
    """Container for one view's pipeline stage outputs.

    All fields default to None. Only populate what is available for the
    current view; ``PipelineCapture._write_view`` skips files when
    required fields are None.
    """
    __slots__ = (
        "view_name", "tf", "t", "dates", "noisy", "ohlc",
        "filtered", "filtered2",
        "sig", "v", "a", "eps", "mu_v", "sigma_v",
        "all_pairs", "prediction_pairs",
        "trade_records", "pnl_long", "pnl_short",
        "higher_pnl", "long_mask", "short_mask", "bs_markers",
    )

    def __init__(
        self,
        view_name: str = "",
        tf: str = "",
        t=None,
        dates=None,
        noisy=None,
        ohlc=None,
        filtered=None,
        filtered2=None,
        sig=None,
        v=None,
        a=None,
        eps=None,
        mu_v=None,
        sigma_v=None,
        all_pairs=None,
        prediction_pairs=None,
        trade_records=None,
        pnl_long=None,
        pnl_short=None,
        higher_pnl=None,
        long_mask=None,
        short_mask=None,
        bs_markers=None,
    ):
        self.view_name = view_name
        self.tf = tf
        self.t = t
        self.dates = dates
        self.noisy = noisy
        self.ohlc = ohlc
        self.filtered = filtered
        self.filtered2 = filtered2
        self.sig = sig
        self.v = v
        self.a = a
        self.eps = eps
        self.mu_v = mu_v
        self.sigma_v = sigma_v
        self.all_pairs = all_pairs
        self.prediction_pairs = prediction_pairs
        self.trade_records = trade_records
        self.pnl_long = pnl_long
        self.pnl_short = pnl_short
        self.higher_pnl = higher_pnl
        self.long_mask = long_mask
        self.short_mask = short_mask
        self.bs_markers = bs_markers


class PipelineCapture:
    """Feature-flag-gated pipeline data capture for backtest debugging.

    Parameters
    ----------
    output_dir : str, optional
        Root directory for capture sessions. Default ``data/pipeline_captures/``.
    ticker : str
        Ticker symbol (e.g. ``"AAPL"``, ``"03690.HK"``).
    config : dict, optional
        Backtest configuration snapshot (operating_tf, min_tf, lower_tfs,
        view_configs, etc.).
    """

    def __init__(self, output_dir: str = None, ticker: str = "", config: dict = None):
        self.output_dir = output_dir or "data/pipeline_captures"
        self.ticker = ticker
        self.config = config or {}
        self._session_dir: Optional[Path] = None
        self._enabled = os.environ.get("PIPELINE_CAPTURE", "") == "1"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_session(self) -> str:
        """Create a timestamped session directory and return the session_id.

        Returns ``""`` immediately when ``PIPELINE_CAPTURE`` is not set.
        """
        if not self._enabled:
            return ""
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        session_id = f"{ts}-{self.ticker}" if self.ticker else ts
        self._session_dir = Path(self.output_dir) / f"{self.ticker}_{session_id}"
        self._session_dir.mkdir(parents=True, exist_ok=True)

        metadata: Dict[str, Any] = {
            "ticker": self.ticker,
            "session_id": session_id,
            "start_time": datetime.now().isoformat(),
            "config": self.config,
        }
        _write_json(self._session_dir / "metadata.json", metadata)
        (self._session_dir / "steps").mkdir(exist_ok=True)
        return session_id

    def capture_step(
        self,
        step_index: int,
        cutoff_date,
        views_data: Dict[str, PipelineStageData],
    ) -> None:
        """Capture one bar step across all visible views.

        Parameters
        ----------
        step_index : int
            Monotonically increasing step counter (0-based).
        cutoff_date : str or None
            ISO-format cutoff date string. Capture is skipped when None.
        views_data : dict[str, PipelineStageData]
            Keyed by view_name (e.g. ``"v0_日线"``).
        """
        if not self._enabled or self._session_dir is None:
            return
        if cutoff_date is None:
            return
        if not views_data:
            return

        step_dir = self._session_dir / "steps" / f"{step_index:06d}"

        # Write index.json once per step (first view wins)
        index_path = step_dir / "index.json"
        if not index_path.exists():
            step_dir.mkdir(parents=True, exist_ok=True)
            _write_json(index_path, {
                "step_index": step_index,
                "cutoff_date": str(cutoff_date),
                "capture_ts": datetime.now().isoformat(),
            })

        for view_name, sd in views_data.items():
            view_dir = step_dir / view_name
            view_dir.mkdir(parents=True, exist_ok=True)
            _write_view(view_dir, sd)

    def end_session(self) -> dict:
        """Finalize the session and return a summary dict.

        Updates metadata.json with end_time, step_count, total_size_bytes.
        Returns empty dict when capture is not enabled or no session exists.
        """
        if not self._enabled or self._session_dir is None:
            return {}

        steps_dir = self._session_dir / "steps"
        step_count = (
            len([p for p in steps_dir.iterdir() if p.is_dir()])
            if steps_dir.exists()
            else 0
        )
        total_size = sum(
            f.stat().st_size
            for f in self._session_dir.rglob("*")
            if f.is_file()
        )

        meta_path = self._session_dir / "metadata.json"
        meta: Dict[str, Any] = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        meta["end_time"] = datetime.now().isoformat()
        meta["step_count"] = step_count
        meta["total_size_bytes"] = total_size
        _write_json(meta_path, meta)

        return {
            "session_id": meta.get("session_id", ""),
            "step_count": step_count,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 1),
        }

    @staticmethod
    def is_enabled() -> bool:
        """Check the PIPELINE_CAPTURE environment variable."""
        return os.environ.get("PIPELINE_CAPTURE", "") == "1"


# ----------------------------------------------------------------------
# Internal helpers (module-level for testability)
# ----------------------------------------------------------------------

def _write_view(view_dir: Path, sd: PipelineStageData) -> None:
    """Write all pipeline stages for a single view to disk.

    Stages skipped (already in parquet cache / derived / duplicative):
    - stage_01_raw  — available in ``data/display/{tf}.parquet``
    - stage_02_markers — not captured per-step; generated by downstream analysis
    - stage_06_prediction — derivable from pairs + filtered
    - stage_08_higher_pnl — duplicative; align from higher-TF stage_07
    """
    # Stage 03: Filters
    if sd.filtered is not None:
        cols: Dict[str, Any] = {"t": sd.t, "filtered": sd.filtered}
        if sd.filtered2 is not None:
            cols["filtered2"] = sd.filtered2
        _write_parquet(view_dir / "stage_03_filter.parquet", cols)

    # Stage 04: Schmitt trigger
    if sd.sig is not None:
        _write_parquet(view_dir / "stage_04_schmitt.parquet", {
            "t": sd.t, "sig": sd.sig, "v": sd.v, "a": sd.a, "eps": sd.eps,
            "mu_v": sd.mu_v, "sigma_v": sd.sigma_v,
        })

    # Stage 05: Signal pairs
    if sd.all_pairs is not None:
        _write_json(view_dir / "stage_05_pairs.json", {
            "all_pairs": sd.all_pairs,
            "pair_count": len(sd.all_pairs),
        })

    # Stage 07: Trade records
    if sd.trade_records is not None:
        _write_json(view_dir / "stage_07_trades.json", {
            "trade_records": sd.trade_records,
        })

    # Stage 07: PnL
    if sd.pnl_long is not None and sd.pnl_short is not None:
        _write_parquet(view_dir / "stage_07_pnl.parquet", {
            "t": sd.t, "pnl_long": sd.pnl_long, "pnl_short": sd.pnl_short,
        })

    # Stage 09: Holding masks
    if sd.long_mask is not None and sd.short_mask is not None:
        _write_parquet(view_dir / "stage_09_masks.parquet", {
            "long_mask": sd.long_mask, "short_mask": sd.short_mask,
        })

    # Stage 10: BS markers
    if sd.bs_markers is not None:
        bs = sd.bs_markers if isinstance(sd.bs_markers, dict) else {}
        _write_json(view_dir / "stage_10_bs_markers.json", {
            "entry_markers": bs.get("entry_markers", []),
            "exit_markers": bs.get("exit_markers", []),
        })


def _write_parquet(path: Path, columns: Dict[str, Any]) -> None:
    """Write columns as a DataFrame to parquet with snappy compression."""
    df = pd.DataFrame(columns)
    # Ensure directories exist (path's parent is the view dir, already created)
    df.to_parquet(path, index=False, compression="snappy")


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    """Write data as indented JSON. ``default=str`` handles datetime types."""
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=2, default=str, ensure_ascii=False)
