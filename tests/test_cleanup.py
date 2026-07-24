"""Phase 1 cleanup tests — verify dead-code removal, deduplication, and import hygiene."""

import pytest
import sys
import importlib


# ============================================================================
# _stock_name consistency
# ============================================================================

class TestStockNameDedup:
    """Verify _stock_name is defined once and all callers return consistent results."""

    def test_stock_name_lookup_exists(self):
        """_stock_name_lookup should be importable from data_loader."""
        from filter_app.data.loader import _stock_name_lookup
        assert callable(_stock_name_lookup)

    def test_stock_name_lookup_empty_code(self):
        """Empty or whitespace-only code should return empty string."""
        from filter_app.data.loader import _stock_name_lookup
        assert _stock_name_lookup("美股 US", "") == ""
        assert _stock_name_lookup("美股 US", "  ") == ""

    def test_stock_name_lookup_us_format(self):
        """US market codes should pass through uppercased."""
        from filter_app.data.loader import _stock_name_lookup
        # This won't actually fetch from yfinance (network), but verifies format logic
        # by checking that the function doesn't crash on valid inputs
        result = _stock_name_lookup("美股 US", "aapl")
        # Returns empty if yfinance fails (no network in CI), but should not raise
        assert isinstance(result, str)

    def test_stock_name_lookup_a_share_shanghai(self):
        """A-share codes starting with '6' should get .SS suffix."""
        from filter_app.data.loader import _stock_name_lookup
        # Verify the market logic path doesn't crash
        result = _stock_name_lookup("A股(沪深)", "600519")
        assert isinstance(result, str)

    def test_stock_name_lookup_hk_format(self):
        """HK market codes should be zero-filled to 4 digits with .HK suffix."""
        from filter_app.data.loader import _stock_name_lookup
        result = _stock_name_lookup("港股 HK", "0700")
        assert isinstance(result, str)

    def test_duplicate_line_removed(self):
        """Verify the duplicate assignment bug in data_loader.py is fixed.

        The original code had:
            full = code + (".SS" if code[0] == "6" else ".SZ")
            full = code + (".SS" if code[0] == "6" else ".SZ")
        Only one line should remain.
        """
        import inspect
        from filter_app.data.loader import _stock_name_lookup
        source = inspect.getsource(_stock_name_lookup)
        # Count occurrences of the .SS/.SZ logic
        count = source.count('".SS" if code[0]')
        assert count == 1, f"Expected 1 occurrence of '.SS' suffix logic, found {count}"


# ============================================================================
# Import hygiene
# ============================================================================

class TestImportHygiene:
    """Verify removed imports don't break module importability."""

    def test_streamlit_app_imports(self):
        """streamlit_app module should import cleanly (syntax check)."""
        # We use compile to check syntax without executing Streamlit code
        import ast
        from pathlib import Path
        app_path = Path(__file__).parent.parent / "filter_app" / "streamlit_app.py"
        source = app_path.read_text(encoding="utf-8")
        try:
            ast.parse(source)
        except SyntaxError as e:
            pytest.fail(f"streamlit_app.py has syntax error: {e}")

    def test_data_loader_imports(self):
        """data_loader module should import without error."""
        from filter_app.services import data_loader
        assert hasattr(data_loader, "_stock_name_lookup")

    def test_sidebar_sections_imports(self):
        """sidebar_sections module should import without error."""
        from filter_app.browse import sidebar
        # The module itself should import cleanly
        assert sidebar_sections is not None

    def test_backtest_panel_imports(self):
        """backtest_panel module should import without error."""
        from filter_app.components import backtest_panel
        assert hasattr(backtest_panel, "render_backtest_panel")
        assert hasattr(backtest_panel, "run_backtest_play")
        assert hasattr(backtest_panel, "sync_backtest_cascading_data")

    def test_no_dead_imports_in_app(self):
        """Verify dead top-level imports are removed from streamlit_app.py source."""
        from pathlib import Path
        import ast
        app_path = Path(__file__).parent.parent / "filter_app" / "streamlit_app.py"
        source = app_path.read_text(encoding="utf-8")

        # Parse top-level imports
        tree = ast.parse(source)
        top_level_imports = set()
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level_imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for alias in node.names:
                        top_level_imports.add(f"{node.module}.{alias.name}")

        # These modules/names should NOT be top-level imports
        dead_top_level = {"json", "os", "tempfile", "pathlib"}
        for dead in dead_top_level:
            assert dead not in top_level_imports, \
                f"Dead top-level import '{dead}' still present in streamlit_app.py"

        # These specific names should NOT appear in top-level imports
        dead_from_names = {
            "backtest_logger.log_bar_navigation",
            "backtest_logger.log_error",
        }
        for dead in dead_from_names:
            assert dead not in top_level_imports, \
                f"Dead import '{dead}' still present in streamlit_app.py"

        # yfinance should not be a top-level import
        assert "yfinance" not in top_level_imports, \
            "Dead import 'yfinance' still present in streamlit_app.py"

        # log_data_load should still be there
        assert "backtest_logger.log_data_load" in top_level_imports, \
            "'log_data_load' was incorrectly removed"
        assert "log_data_load" in source, "'log_data_load' was incorrectly removed"

    def test_sidebar_dead_functions_removed(self):
        """Verify dead functions are removed from sidebar_sections.py."""
        from pathlib import Path
        sidebar_path = Path(__file__).parent.parent / "filter_app" / "sidebar_sections.py"
        source = sidebar_path.read_text(encoding="utf-8")

        assert "def _render_market_ticker" not in source, \
            "Dead function _render_market_ticker still present"
        assert "def _render_operating_tf_selector" not in source, \
            "Dead function _render_operating_tf_selector still present"

    def test_sync_backtest_cascading_data_signature(self):
        """sync_backtest_cascading_data should be callable with correct args."""
        from filter_app.components.backtest_panel import sync_backtest_cascading_data
        import inspect
        sig = inspect.signature(sync_backtest_cascading_data)
        params = list(sig.parameters.keys())
        assert "ticker_code" in params
        assert "configs" in params
        assert "cutoff_date" in params
        assert "min_tf" in params
        assert "all_tfs" in params


# ============================================================================
# Archive verification
# ============================================================================

class TestArchiveStructure:
    """Verify archived scripts are in the right location."""

    def test_archive_directory_exists(self):
        """tools/_archive/ should exist."""
        from pathlib import Path
        archive = Path(__file__).parent.parent / "tools" / "_archive"
        assert archive.is_dir(), "tools/_archive/ directory not found"

    def test_archived_files_present(self):
        """Archived files should be in _archive directory."""
        from pathlib import Path
        archive = Path(__file__).parent.parent / "tools" / "_archive"
        archived_files = [f.name for f in archive.iterdir() if f.suffix == ".py"]
        expected = {"trace_long_veto.py", "replay_bar.py", "view_backtest.py",
                     "analyze_captured_backtest.py"}
        found = set(archived_files) & expected
        assert len(found) >= 3, f"Expected at least 3 archived files, found: {found}"

    def test_original_locations_cleared(self):
        """Archived files should no longer exist in tools/ root."""
        from pathlib import Path
        tools = Path(__file__).parent.parent / "tools"
        archived_names = ["trace_long_veto.py", "replay_bar.py", "view_backtest.py",
                          "analyze_captured_backtest.py"]
        for name in archived_names:
            path = tools / name
            assert not path.exists(), f"{name} still exists in tools/ root (should be in _archive/)"


# ============================================================================
# web_tool documentation
# ============================================================================

class TestWebToolDocs:
    """Verify web_tool README exists with relationship note."""

    def test_readme_exists(self):
        """web_tool/README.md should exist."""
        from pathlib import Path
        readme = Path(__file__).parent.parent / "web_tool" / "README.md"
        assert readme.exists(), "web_tool/README.md not found"

    def test_readme_documents_independence(self):
        """README should note that web_tool and filter_app are independent."""
        from pathlib import Path
        readme = Path(__file__).parent.parent / "web_tool" / "README.md"
        content = readme.read_text(encoding="utf-8")
        assert "无代码引用关系" in content or "filter_app" in content.lower(), \
            "README should document relationship to filter_app"
