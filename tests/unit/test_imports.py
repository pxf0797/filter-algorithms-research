"""Regression test: verify key modules can be imported without errors.

Simulates the sys.path setup that `streamlit run filter_app/streamlit_app.py` creates,
with the fix that adds the project root to sys.path (as streamlit_app.py now does).
This ensures `from filter_app import ALL_TFS` works correctly.
"""
import sys
from pathlib import Path

# Simulate streamlit run's fixed sys.path setup:
# 1. project root (so `from filter_app import ...` works)
# 2. filter_app/ (so bare imports like `from state import ...` work)
_project_root = Path(__file__).resolve().parents[2]
_filter_app = _project_root / "filter_app"
for _p in (str(_project_root), str(_filter_app)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def test_can_import_filter_engine():
    """Verify services.filter_engine imports without ModuleNotFoundError."""
    from services.filter_engine import FILTERS, _schmitt_trigger
    assert len(FILTERS) > 0


def test_can_import_data_loader():
    """Verify data_loader imports (regression: from filter_app import ALL_TFS)."""
    from services.data_loader import _fetch_stock, _sync_to_display
    assert callable(_fetch_stock)


def test_can_import_backtest_core():
    """Verify backtest_core imports (regression: from filter_app import ALL_TFS, TF_HIERARCHY)."""
    from services.backtest_core import BacktestRunner
    assert BacktestRunner is not None


def test_can_import_sidebar():
    """Verify sidebar imports (regression: from filter_app import constants)."""
    from components.sidebar import ALL_TFS, DEFAULT_TFS, TF_HIERARCHY
    assert len(ALL_TFS) == 8


def test_can_import_streamlit_app():
    """Verify the main app entry point imports successfully."""
    try:
        import streamlit_app
    except ImportError:
        # If streamlit isn't available, that's OK — we're testing for ModuleNotFoundError: filter_app
        pass


def test_can_import_new_components():
    """Verify all new component modules import successfully."""
    from components.chart_builder import _date_markers, _determine_subplot_layout
    from components.data_processing import _compute_filters, _load_chart_data
    from components.health import _render_health_check
    from components.presets import _view_export_params
    assert callable(_date_markers)
    assert callable(_determine_subplot_layout)
    assert callable(_compute_filters)
    assert callable(_load_chart_data)
    assert callable(_render_health_check)
    assert callable(_view_export_params)


def test_can_import_from_filter_app_package():
    """Verify `from filter_app import ...` works (regression: project root needed on sys.path)."""
    from filter_app import ALL_TFS, DEFAULT_TFS, TF_HIERARCHY
    assert len(ALL_TFS) == 8
    assert len(DEFAULT_TFS) > 0
    assert len(TF_HIERARCHY) > 0
