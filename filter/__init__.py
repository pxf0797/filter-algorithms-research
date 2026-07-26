"""
filter — Stock filter & backtest application.

5-package architecture:
  shared   — config, constants, state, logging, repository
  engine   — filters, signals computation
  data     — loader, store, db, config_db
  browse   — Streamlit app, charts, sidebar
  backtest — backtest engine, CLI, metrics, panel, recorder, optimizer, capture
"""

import tomllib
from pathlib import Path

# Read version from pyproject.toml (canonical source)
_pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
with open(_pyproject_path, "rb") as _fp:
    __version__ = tomllib.load(_fp)["project"]["version"]

# Backward compatibility re-exports from shared
from filter.shared.config import ViewConfig
from filter.shared.constants import ALL_TFS, DEFAULT_TFS, TF_HIERARCHY, TF_INTERVAL
from filter.shared.state import AppState, ViewState, StateStore, StreamlitStateStore, DictStateStore

# Backward compatibility re-exports from engine
from filter.engine.filters import FILTERS
from filter.engine.signals import compute_bs_markers, get_lower_tfs

__all__ = [
    "__version__",
    "ViewConfig",
    "ALL_TFS", "DEFAULT_TFS", "TF_HIERARCHY", "TF_INTERVAL",
    "AppState", "ViewState",
    "FILTERS",
    "compute_bs_markers", "get_lower_tfs",
]
