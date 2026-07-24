"""
filter_app — Stock filter & backtest application.

5-package architecture:
  shared   — config, constants, state, logging, repository
  engine   — filters, signals computation
  data     — loader, store, db, config_db
  browse   — Streamlit app, charts, sidebar
  backtest — backtest engine, CLI, metrics, panel, recorder, optimizer, pipeline
"""

import sys
import os

# Ensure filter_app/ is on sys.path so internal bare imports
# (e.g. ``from shared.constants import ...``) resolve correctly.
_pkg_dir = os.path.dirname(os.path.abspath(__file__))
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

# Backward compatibility re-exports from shared
from filter_app.shared.config import ViewConfig
from filter_app.shared.constants import ALL_TFS, DEFAULT_TFS, TF_HIERARCHY, TF_INTERVAL
from filter_app.shared.state import AppState, ViewState
from filter_app.shared.repository import BaseRepository, PresetRepository

# Backward compatibility re-exports from engine
from filter_app.engine.filters import FILTERS
from filter_app.engine.signals import compute_bs_markers, get_lower_tfs

__all__ = [
    "ViewConfig",
    "ALL_TFS", "DEFAULT_TFS", "TF_HIERARCHY", "TF_INTERVAL",
    "AppState", "ViewState",
    "BaseRepository", "PresetRepository",
    "FILTERS",
    "compute_bs_markers", "get_lower_tfs",
]
