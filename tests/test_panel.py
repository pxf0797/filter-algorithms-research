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
# The code uses ``from filter.data.db import get_conn``, so we must patch
# ``filter.data.db.get_conn``, because the module is stored as
# ``filter.data.db`` in sys.modules.
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
        with patch("filter.data.db.get_conn", return_value=_mock_get_conn([500])):
            min_tf, bar_count = _get_min_tf_and_count(configs, "AAPL")
            assert min_tf == "日线"
            assert bar_count == 500

    def test_multiple_tf_picks_finest(self):
        """Multiple TFs pick the one with the smallest index in ALL_TFS."""
        from filter.backtest.panel import _get_min_tf_and_count
        configs = [{"tf": "周线"}, {"tf": "日线"}]
        with patch("filter.data.db.get_conn", return_value=_mock_get_conn([300])):
            min_tf, bar_count = _get_min_tf_and_count(configs, "AAPL")
            assert min_tf == "日线"  # finer than 周线

    def test_db_failure_returns_zero_count(self):
        """DB exception is caught, bar_count falls back to 0."""
        from filter.backtest.panel import _get_min_tf_and_count
        configs = [{"tf": "日线"}]
        with patch("filter.data.db.get_conn", side_effect=Exception("DB down")):
            min_tf, bar_count = _get_min_tf_and_count(configs, "AAPL")
            assert min_tf == "日线"
            assert bar_count == 0

    def test_db_returns_none_row(self):
        """DB returns None (no rows) -> bar_count = 0."""
        from filter.backtest.panel import _get_min_tf_and_count
        configs = [{"tf": "60分钟线"}]
        with patch("filter.data.db.get_conn", return_value=_mock_get_conn(None)):
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
        with patch("filter.data.db.get_conn", return_value=_mock_get_conn(None)):
            result = _get_bar_date_from_db("UNKNOWN", "日线", 99999)
            assert result == ""

    def test_db_exception_propagates(self):
        """DB connection exception propagates (no try/except in this function)."""
        from filter.backtest.panel import _get_bar_date_from_db
        with patch("filter.data.db.get_conn", side_effect=RuntimeError("DB unavailable")):
            with pytest.raises(RuntimeError, match="DB unavailable"):
                _get_bar_date_from_db("AAPL", "日线", 0)


# ═══════════════════════════════════════════════════════════════════════════════
# T5: 循环依赖修复验证
# ═══════════════════════════════════════════════════════════════════════════════

class TestCircularDependencyPrevention:
    """验证 panel.py 与 browse 模块之间不存在循环依赖。

    问题背景：
    - browse/app.py 导入 filter.backtest.panel 和 filter.backtest.dashboard
    - panel.py 必须独立于 browse 模块，不能触发 browse 导入
    - ALL_TFS 等常量定义在 shared/constants.py 中，不依赖 browse
    """

    def test_panel_import_does_not_trigger_browse_module(self):
        """from filter.backtest.panel import ... 不应将 browse 加载到 sys.modules。

        在隔离的子进程中验证，确保 import 不会间接触发 browse 模块导入。
        """
        import subprocess
        import sys

        code = """
import sys

# 确保 browse 尚未加载
assert "filter.browse" not in sys.modules, "browse should not be pre-loaded"
assert "browse" not in sys.modules, "browse (short) should not be pre-loaded"

# 导入 panel 的公开符号
from filter.backtest.panel import render_backtest_panel, run_backtest_play

# 验证 browse 模块未被间接触发
browse_loaded = "filter.browse" in sys.modules or "browse" in sys.modules
if browse_loaded:
    browse_keys = [k for k in sys.modules if "browse" in k.lower()]
    print(f"FAIL: browse modules loaded unexpectedly: {browse_keys}")
    sys.exit(1)
else:
    print("OK: browse module not loaded by panel import")
    sys.exit(0)
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert result.returncode == 0, (
            f"panel import triggered browse module:\n{result.stdout}\n{result.stderr}"
        )

    def test_dashboard_import_does_not_trigger_browse_module(self):
        """from filter.backtest.dashboard import ... 不应将 browse 加载到 sys.modules。"""
        import subprocess
        import sys

        code = """
import sys

# 确保 browse 尚未加载
assert "filter.browse" not in sys.modules, "browse should not be pre-loaded"

# 导入 dashboard
from filter.backtest.dashboard import render_backtest_dashboard

# 验证 browse 模块未被间接触发
browse_loaded = "filter.browse" in sys.modules or "browse" in sys.modules
if browse_loaded:
    browse_keys = [k for k in sys.modules if "browse" in k.lower()]
    print(f"FAIL: browse modules loaded unexpectedly: {browse_keys}")
    sys.exit(1)
else:
    print("OK: browse module not loaded by dashboard import")
    sys.exit(0)
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert result.returncode == 0, (
            f"dashboard import triggered browse module:\n{result.stdout}\n{result.stderr}"
        )

    def test_all_tfs_accessible_from_panel_direct_import(self):
        """ALL_TFS 在 panel.py 中可访问且类型为 list。"""
        # panel.py 内部使用 ALL_TFS，如果导入成功说明依赖正确
        # 直接导入验证
        from filter.shared.constants import ALL_TFS
        assert isinstance(ALL_TFS, list), f"ALL_TFS 应为 list，实际为 {type(ALL_TFS)}"
        assert len(ALL_TFS) == 8
        assert ALL_TFS[0] == "1分钟"
        assert ALL_TFS[-1] == "季线"

    def test_all_tfs_defined_in_standalone_constants(self):
        """ALL_TFS 定义在 filter.shared.constants 中，该模块不依赖任何 browse/backtest。

        验证 shared/constants.py 的导入不涉及 browse 或 backtest 包。
        """
        import subprocess
        import sys

        code = """
import sys

# 导入 constants 模块
from filter.shared.constants import ALL_TFS, DEFAULT_TFS, TF_HIERARCHY, TF_INTERVAL

# 验证这些常量是正确的
assert len(ALL_TFS) == 8
assert isinstance(ALL_TFS, list)
assert len(DEFAULT_TFS) == 4
assert len(TF_HIERARCHY) == 8  # 8 entries (季线→None 也是 entry)

# 验证没有触发 browse 或 backtest 导入
forbidden = {"filter.browse", "browse", "filter.backtest.engine", "filter.backtest.panel"}
loaded = forbidden & set(sys.modules.keys())
if loaded:
    print(f"FAIL: unexpected modules loaded: {loaded}")
    sys.exit(1)
print("OK: shared.constants is standalone")
sys.exit(0)
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert result.returncode == 0, (
            f"shared.constants import triggered unexpected modules:\n{result.stdout}\n{result.stderr}"
        )

    def test_import_browse_app_no_circular_error(self):
        """import filter.browse.app 不应触发循环导入 (ImportError)。

        在子进程中执行，因为主进程可能已经加载了这些模块。
        """
        import subprocess
        import sys

        code = """
import sys

try:
    import filter.browse.app
    print("OK: filter.browse.app imported successfully")
    sys.exit(0)
except ImportError as e:
    msg = str(e)
    if "circular" in msg.lower() or "cannot import" in msg.lower():
        print(f"FAIL: circular import detected: {e}")
    else:
        print(f"FAIL: import error: {e}")
    sys.exit(1)
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert result.returncode == 0, (
            f"browse.app import failed:\n{result.stdout}\n{result.stderr}"
        )

    def test_panel_uses_all_tfs_from_constants_not_from_browse(self):
        """panel.py 的 ALL_TFS 来自 filter.shared.constants，而非 filter.browse。

        验证 panel.py 源码中不包含 ``from filter.browse`` 或 ``from browse``。
        """
        panel_path = Path(__file__).resolve().parent.parent / "filter" / "backtest" / "panel.py"
        source = panel_path.read_text()

        assert "from filter.browse" not in source, (
            "panel.py 不应从 filter.browse 导入任何内容"
        )
        assert "from browse" not in source, (
            "panel.py 不应从 browse 导入任何内容"
        )
        assert "import filter.browse" not in source, (
            "panel.py 不应导入 filter.browse"
        )
        # 验证确实从 constants 导入了 ALL_TFS
        assert "from filter.shared.constants import" in source or \
               "from filter.shared.constants import ALL_TFS" in source, (
            "panel.py 应从 filter.shared.constants 导入 ALL_TFS"
        )

    def test_dashboard_does_not_import_browse(self):
        """dashboard.py 也不应从 browse 导入任何内容。"""
        dashboard_path = Path(__file__).resolve().parent.parent / "filter" / "backtest" / "dashboard.py"
        source = dashboard_path.read_text()

        assert "from filter.browse" not in source, (
            "dashboard.py 不应从 filter.browse 导入任何内容"
        )
        assert "from browse" not in source, (
            "dashboard.py 不应从 browse 导入任何内容"
        )
        assert "import filter.browse" not in source, (
            "dashboard.py 不应导入 filter.browse"
        )

    def test_shared_constants_has_no_internal_browse_dependency(self):
        """filter/shared/constants.py 源码中不涉及 browse 或 backtest 导入。"""
        constants_path = Path(__file__).resolve().parent.parent / "filter" / "shared" / "constants.py"
        source = constants_path.read_text()

        assert "browse" not in source.lower(), (
            "shared/constants.py 不应引用 browse"
        )
        assert "backtest" not in source.lower(), (
            "shared/constants.py 不应引用 backtest"
        )
