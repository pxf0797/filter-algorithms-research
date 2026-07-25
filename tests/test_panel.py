"""
panel.py tests for _get_min_tf_and_count and _get_bar_date_from_db.

Covers: empty configs, invalid TF, DB row/no-row, DB connection failure.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure filter/ package is importable
_src = Path(__file__).resolve().parent.parent / "filter"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))


# ---------------------------------------------------------------------------
# Import validation
# ---------------------------------------------------------------------------

class TestPanelImports:
    """Verify key panel functions are importable."""

    def test_import_get_min_tf_and_count(self):
        from filter.backtest.panel import _get_min_tf_and_count
        assert callable(_get_min_tf_and_count)

    def test_import_get_bar_date_from_db(self):
        from filter.backtest.panel import _get_bar_date_from_db
        assert callable(_get_bar_date_from_db)


# ---------------------------------------------------------------------------
# Helper: build a mock DB connection context manager for panel tests.
# The code uses ``from data.db import get_conn``, so we must patch
# ``data.db.get_conn`` (NOT ``filter.data.db.get_conn`` — they are
# distinct module objects in sys.modules).
# ---------------------------------------------------------------------------

def _mock_get_conn(row_data):
    """MagicMock that acts as a context manager returning a cursor.

    Simulates::

        with get_conn() as conn:
            row = conn.execute(sql, params).fetchone()

    MagicMock acts as a context manager, but ``__enter__`` returns
    ``__enter__.return_value`` (a new mock) rather than ``self``.
    We wire the execute / fetchone chain on the enter-mock.
    """
    m = MagicMock()
    # get_conn() returns m; ``with m as conn:`` calls m.__enter__()
    conn = m.__enter__.return_value
    conn.execute.return_value.fetchone.return_value = row_data
    return m


# ---------------------------------------------------------------------------
# _get_min_tf_and_count
# ---------------------------------------------------------------------------

class TestGetMinTfAndCount:
    """_get_min_tf_and_count config parsing and DB interactions."""

    def test_empty_configs(self):
        """Empty config list returns ("", 0)."""
        from filter.backtest.panel import _get_min_tf_and_count
        min_tf, bar_count = _get_min_tf_and_count([], "AAPL")
        assert min_tf == ""
        assert bar_count == 0

    def test_empty_configs_with_none(self):
        """None / falsy configs returns ("", 0)."""
        from filter.backtest.panel import _get_min_tf_and_count
        min_tf, bar_count = _get_min_tf_and_count(None, "AAPL")
        assert min_tf == ""
        assert bar_count == 0

    def test_all_invalid_tf(self):
        """All TFs not in ALL_TFS -> returns ("", 0)."""
        from filter.backtest.panel import _get_min_tf_and_count
        configs = [{"tf": "invalid_tf"}, {"tf": "also_invalid"}]
        min_tf, bar_count = _get_min_tf_and_count(configs, "AAPL")
        assert min_tf == ""
        assert bar_count == 0

    def test_single_valid_tf(self):
        """Single valid TF, DB returns 500 bars."""
        from filter.backtest.panel import _get_min_tf_and_count
        configs = [{"tf": "日线"}]
        with patch("data.db.get_conn", return_value=_mock_get_conn([500])):
            min_tf, bar_count = _get_min_tf_and_count(configs, "AAPL")
            assert min_tf == "日线"
            assert bar_count == 500

    def test_multiple_tf_picks_finest(self):
        """Multiple TFs pick the one with the smallest index in ALL_TFS."""
        from filter.backtest.panel import _get_min_tf_and_count
        configs = [{"tf": "周线"}, {"tf": "日线"}]
        with patch("data.db.get_conn", return_value=_mock_get_conn([300])):
            min_tf, bar_count = _get_min_tf_and_count(configs, "AAPL")
            assert min_tf == "日线"  # finer than 周线

    def test_db_failure_returns_zero_count(self):
        """DB exception is caught, bar_count falls back to 0."""
        from filter.backtest.panel import _get_min_tf_and_count
        configs = [{"tf": "日线"}]
        with patch("data.db.get_conn", side_effect=Exception("DB down")):
            min_tf, bar_count = _get_min_tf_and_count(configs, "AAPL")
            assert min_tf == "日线"
            assert bar_count == 0

    def test_db_returns_none_row(self):
        """DB returns None (no rows) -> bar_count = 0."""
        from filter.backtest.panel import _get_min_tf_and_count
        configs = [{"tf": "60分钟线"}]
        with patch("data.db.get_conn", return_value=_mock_get_conn(None)):
            min_tf, bar_count = _get_min_tf_and_count(configs, "AAPL")
            assert bar_count == 0


# ---------------------------------------------------------------------------
# _get_bar_date_from_db edge cases
# ---------------------------------------------------------------------------

class TestGetBarDateEdgeCases:
    """_get_bar_date_from_db database query edge cases."""

    def test_no_row_found_returns_empty(self):
        """No matching row -> returns empty string."""
        from filter.backtest.panel import _get_bar_date_from_db
        with patch("data.db.get_conn", return_value=_mock_get_conn(None)):
            result = _get_bar_date_from_db("UNKNOWN", "日线", 99999)
            assert result == ""

    def test_db_exception_propagates(self):
        """DB connection exception propagates (no try/except in this function)."""
        from filter.backtest.panel import _get_bar_date_from_db
        with patch("data.db.get_conn", side_effect=RuntimeError("DB unavailable")):
            with pytest.raises(RuntimeError, match="DB unavailable"):
                _get_bar_date_from_db("AAPL", "日线", 0)
