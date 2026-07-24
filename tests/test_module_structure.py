"""Verify the 5-package architecture after Phase 2 refactor.

Checks:
  1. All 5 top-level packages exist with __init__.py
  2. Old flat directories (services/, components/) are gone
  3. Key modules are importable from their new locations
"""
import importlib
import sys
from pathlib import Path


# ── Project root ──────────────────────────────────────────────────────────
PROJECT = Path(__file__).resolve().parent.parent
FILTER_APP = PROJECT / "filter_app"


# ── Helpers ───────────────────────────────────────────────────────────────

def _package_path(name: str) -> Path:
    return FILTER_APP / name


def _has_init(pkg: str) -> bool:
    return (_package_path(pkg) / "__init__.py").exists()


# ── 1. Top-level packages exist ──────────────────────────────────────────
PACKAGES = ["shared", "engine", "data", "browse", "backtest"]


class TestPackageStructure:
    def test_all_five_packages_exist(self):
        for pkg in PACKAGES:
            assert _package_path(pkg).is_dir(), f"Missing package dir: {pkg}"
            assert _has_init(pkg), f"Missing __init__.py in {pkg}"

    def test_root_has_init(self):
        assert (FILTER_APP / "__init__.py").exists(), "Missing filter_app/__init__.py"

    def test_old_dirs_removed(self):
        """services/ and components/ directories no longer exist."""
        for old in ["services", "components"]:
            assert not _package_path(old).exists(), (
                f"Old directory {old}/ should be removed"
            )


# ── 2. Key modules are importable ────────────────────────────────────────
# These tests verify that the new import paths resolve correctly.
# They use importlib so a single failure doesn't crash the whole suite.


class TestImports:
    # shared
    def test_shared_config(self):
        from shared.config import ViewConfig
        assert ViewConfig is not None

    def test_shared_constants(self):
        from shared.constants import ALL_TFS, DEFAULT_TFS, TF_HIERARCHY, TF_INTERVAL
        assert isinstance(ALL_TFS, (list, tuple))
        assert len(ALL_TFS) > 0

    def test_shared_state(self):
        from shared.state import AppState, ViewState
        assert AppState is not None
        assert ViewState is not None

    def test_shared_logger(self):
        from shared.logger import setup_logging
        assert callable(setup_logging)

    def test_shared_repository(self):
        from shared.repository import BaseRepository
        assert BaseRepository is not None

    # engine
    def test_engine_filters(self):
        from engine.filters import FILTERS
        assert isinstance(FILTERS, dict)
        assert len(FILTERS) > 0

    def test_engine_signals(self):
        from engine.signals import compute_bs_markers
        assert callable(compute_bs_markers)

    # backtest
    def test_backtest_engine(self):
        from backtest.engine import BacktestRunner
        assert BacktestRunner is not None

    def test_backtest_cli(self):
        from backtest.cli import parse_args
        assert callable(parse_args)

    def test_backtest_recorder(self):
        from backtest.recorder import EventRecorder
        assert EventRecorder is not None

    def test_backtest_catalog(self):
        from backtest.catalog import BacktestCatalog
        assert BacktestCatalog is not None


# ── 3. filter_app top-level re-exports work ──────────────────────────────

class TestTopLevelReexports:
    def test_view_config(self):
        from filter_app import ViewConfig
        assert ViewConfig is not None

    def test_all_tfs(self):
        from filter_app import ALL_TFS
        assert isinstance(ALL_TFS, (list, tuple))

    def test_filters(self):
        from filter_app import FILTERS
        assert isinstance(FILTERS, dict)
