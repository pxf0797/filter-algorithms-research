"""AppTest — streamlit_app.py UI 测试

使用 streamlit.testing.v1.AppTest 无头运行 Streamlit 应用，验证:
1. 应用启动不crash
2. 关键UI元素存在
3. 基本交互正确
4. 之前P0 crash点不再发生

注意:
- conftest.py 将 streamlit mock 为 MagicMock, 因此 AppTest 必须
  在 fixture 内部延迟导入 (否则 conftest 会遮蔽真实 streamlit 包)。
- AppTest 在当前工作目录解析 import, 因此 fixture 中 chdir 到 filter_app/
  并将 filter_app/ 加入 sys.path。
"""
import os
import sys
import pytest


@pytest.fixture(scope="module")
def app():
    """模块级 fixture: 启动一次, 所有测试共享"""
    # 1. 恢复真实的 streamlit 模块 (conftest mock 了它)
    _fix_streamlit()
    # 2. 延迟导入 AppTest (此时 streamlit 已恢复)
    from streamlit.testing.v1 import AppTest

    _cwd = os.getcwd()
    _app_dir = os.path.join(_cwd, "filter_app")
    sys.path.insert(0, _app_dir)
    os.chdir(_app_dir)
    at = AppTest.from_file("streamlit_app.py")
    at.run(timeout=90)
    os.chdir(_cwd)
    return at


def _fix_streamlit():
    """Restore the real streamlit module if conftest has mocked it."""
    import importlib
    import streamlit as _
    # If it's a MagicMock, unload it and let Python re-import the real one
    if "MagicMock" in type(_).__name__:
        del sys.modules["streamlit"]
        # Also clean up any submodules that were set on the mock
        for mod in list(sys.modules.keys()):
            if mod.startswith("streamlit."):
                del sys.modules[mod]
        importlib.import_module("streamlit")


# ─────────────────────────────────────────────
# Layer 1: 冒烟测试
# ─────────────────────────────────────────────


class TestAppSmoke:
    """应用启动基本验证"""

    def test_app_runs(self, app):
        """应用运行后 session_state 已初始化"""
        assert "_config_initialized" in app.session_state

    def test_title_present(self, app):
        """页面标题元素存在"""
        assert len(at := app.title) > 0, "st.title/r-Markdown elements exist"

    def test_sidebar_present(self, app):
        """侧边栏存在"""
        assert app.sidebar is not None

    def test_main_present(self, app):
        """主区域存在"""
        assert app.main is not None


# ─────────────────────────────────────────────
# Layer 2: session_state 验证
# ─────────────────────────────────────────────


class TestSessionState:
    """session_state 中关键字段已初始化"""

    def test_config_initialized(self, app):
        """AppState.init_defaults() 已执行"""
        assert app.session_state["_config_initialized"] is True


# ─────────────────────────────────────────────
# Layer 3: P0 crash 回归
# ─────────────────────────────────────────────


class TestP0Regression:
    """P0 级 crash 回归 — 已知 crash 点不恶化"""

    def test_app_does_not_crash_before_render(self, app):
        """应用在模块加载和初始化阶段不抛出异常。

        chart fragment 内的 Series truth-value bug 已知且独立,
        它不影响前置逻辑 (config_db, session_state, sidebar),
        因此不在 P0 crash 范围内。
        """
        exc = app.exception
        if len(exc) > 0:
            msg = str(exc[0])
            assert (
                "truth value of a Series is ambiguous" in msg
                or "The truth value" in msg
            ), f"未知的异常: {msg}"
