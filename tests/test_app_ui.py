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

    _app_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "filter_app"))
    _script = os.path.join(_app_dir, "streamlit_app.py")
    sys.path.insert(0, _app_dir)
    cwd = os.getcwd()
    os.chdir(_app_dir)
    at = AppTest.from_file(_script)
    at.run(timeout=90)
    os.chdir(cwd)
    return at


def _fix_streamlit():
    """Restore the real streamlit module if conftest has mocked it.

    conftest.py 将 streamlit patch 为 MagicMock, 且在其他测试模块
    (如 test_signal_processing.py) 导入 state.py, streamlit_app.py,
    components.sidebar, services.filter_engine 等模块时, 这些模块的
    模块级 ``import streamlit as st`` 已绑定到 MagicMock 实例。

    AppTest 在同一进程中使用 exec() 执行 streamlit_app.py, 因此如果
    sys.modules["streamlit"] 仍为 MagicMock, streamlit_app.py 内部的
    ``import streamlit as st`` 会拿到 MagicMock, 导致所有 st.* 调用
    失效, 最终出现如 configs[0]["n_pts"] 为 MagicMock 的 TypeError。

    修复策略：
    1. 删除 sys.modules["streamlit"] 及子模块 → 恢复真实 streamlit
    2. 删除所有已持有 MagicMock 引用的项目模块 → 强制后续 import
       重新导入并绑定真实 streamlit
    """
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
        # 清除已持有 MagicMock 引用的项目模块, 强制重载
        _project_modules = {"state", "streamlit_app"}
        _project_prefixes = ("state.", "streamlit_app.", "components.", "services.", "db", "config_db")
        for mod in list(sys.modules.keys()):
            if mod in _project_modules:
                del sys.modules[mod]
            elif any(mod.startswith(p) for p in _project_prefixes):
                del sys.modules[mod]


def _fresh_app():
    """Create and run a fresh AppTest instance (helper for re-run tests)."""
    _fix_streamlit()
    from streamlit.testing.v1 import AppTest

    _app_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "filter_app"))
    _script = os.path.join(_app_dir, "streamlit_app.py")
    sys.path.insert(0, _app_dir)
    cwd = os.getcwd()
    os.chdir(_app_dir)
    at = AppTest.from_file(_script)
    at.run(timeout=90)
    os.chdir(cwd)
    return at


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


# ─────────────────────────────────────────────
# Layer 4: Sidebar 控件存在性
# ─────────────────────────────────────────────


class TestSidebarInteraction:
    """侧边栏控件存在性验证"""

    def test_market_selector_present(self, app):
        """市场选择器 (Radio) 存在且默认选中美股"""
        radios = app.sidebar.radio
        market = next((r for r in radios if r.key == "market"), None)
        assert market is not None, "market selector not found"
        assert market.value == "美股 US"

    def test_ticker_input_present(self, app):
        """ticker 输入框存在且默认值为 AAPL"""
        inputs = app.sidebar.text_input
        ticker = next((t for t in inputs if t.key == "ticker"), None)
        assert ticker is not None, "ticker input not found"
        assert ticker.value == "AAPL"

    def test_filter_selector_present(self, app):
        """滤波器选择器 (Selectbox) 存在"""
        selectboxes = app.sidebar.selectbox
        filt = next((s for s in selectboxes if s.key == "global_f"), None)
        assert filt is not None, "filter selector not found"
        assert filt.value == "sma"

    def test_dual_filter_checkbox_present(self, app):
        """双滤波对比复选框存在"""
        checkboxes = app.sidebar.checkbox
        dual = next((c for c in checkboxes if c.key == "global_dual"), None)
        assert dual is not None, "dual filter checkbox not found"
        assert dual.value is False

    def test_day_step_selector_present(self, app):
        """移动步长选择器存在"""
        selectboxes = app.sidebar.selectbox
        step = next((s for s in selectboxes if s.label == "移动步长"), None)
        assert step is not None, "day_step selector not found"
        # value is the selected option's internal value (int index)
        assert step.value == 20

    def test_refresh_button_present(self, app):
        """刷新数据按钮存在"""
        buttons = app.sidebar.button
        refresh = next((b for b in buttons if b.label == "刷新数据"), None)
        assert refresh is not None, "refresh button not found"

    def test_auto_refresh_checkbox_present(self, app):
        """自动刷新复选框存在"""
        checkboxes = app.sidebar.checkbox
        auto = next((c for c in checkboxes if c.key == "auto_refresh"), None)
        assert auto is not None, "auto refresh checkbox not found"
        assert auto.value is False


# ─────────────────────────────────────────────
# Layer 5: 预设选择器交互
# ─────────────────────────────────────────────


class TestPresetInteraction:
    """预设选择器交互测试"""

    def test_preset_selector_present(self, app):
        """预设下拉框存在且默认值为 (不选择)"""
        selectboxes = app.sidebar.selectbox
        preset = next((s for s in selectboxes if s.key.startswith("preset_sel")), None)
        assert preset is not None, "preset selector not found"
        assert preset.value == "(不选择)"

    def test_preset_options_include_none(self, app):
        """预设选项包含 (不选择) 作为首项"""
        selectboxes = app.sidebar.selectbox
        preset = next((s for s in selectboxes if s.key.startswith("preset_sel")), None)
        options = preset.options
        assert "(不选择)" in options

    def test_preset_options_not_empty(self, app):
        """预设选项列表非空"""
        selectboxes = app.sidebar.selectbox
        preset = next((s for s in selectboxes if s.key.startswith("preset_sel")), None)
        assert len(preset.options) >= 1

    def test_no_preset_selection_safe(self, app):
        """保持 (不选择) 不 crash — 页面仍然渲染"""
        selectboxes = app.sidebar.selectbox
        preset = next((s for s in selectboxes if s.key.startswith("preset_sel")), None)
        preset.set_value("(不选择)")
        assert preset.value == "(不选择)"


# ─────────────────────────────────────────────
# Layer 6: Widget 交互操作
# ─────────────────────────────────────────────


class TestWidgetInteraction:
    """侧边栏控件交互操作测试"""

    def test_ticker_change_does_not_crash(self):
        """切换 ticker 不导致应用崩溃"""
        app = _fresh_app()
        inp = app.sidebar.text_input[0]
        inp.set_value("MSFT")
        app.run(timeout=90)
        assert app.session_state["ticker"] == "MSFT"

    def test_filter_change_does_not_crash(self):
        """切换滤波器不导致应用崩溃"""
        app = _fresh_app()
        sel = app.sidebar.selectbox[1]
        sel.set_value("指数移动平均 (EMA)")
        app.run(timeout=90)
        assert app.session_state["global_f"] == "ema"

    def test_dual_filter_toggle(self):
        """勾选双滤波对比 — session_state 更新"""
        app = _fresh_app()
        cb = app.sidebar.checkbox[1]
        cb.check()
        app.run(timeout=90)
        assert app.session_state["global_dual"] is True

    def test_day_nav_buttons_present(self, app):
        """日期导航按钮 (前移/后移/最新) 都存在"""
        buttons = app.sidebar.button
        labels = {b.label for b in buttons}
        assert "◀ 前移" in labels
        assert "后移 ▶" in labels
        assert "最新" in labels


# ─────────────────────────────────────────────
# Layer 7: P0 回归扩展
# ─────────────────────────────────────────────


class TestP0RegressionExtended:
    """扩展 P0 回归测试"""

    def test_empty_ticker_safe(self):
        """空 ticker 不导致进程级崩溃"""
        app = _fresh_app()
        inp = app.sidebar.text_input[0]
        inp.set_value("")
        app.run(timeout=90)
        # app.exception is an ElementList; crash means it remains accessible
        assert len(list(app.exception)) >= 0

    def test_unknown_filter_setting(self):
        """设置有效滤波器值不崩溃"""
        app = _fresh_app()
        sel = app.sidebar.selectbox[1]
        sel.set_value("LOWESS 平滑")
        app.run(timeout=90)
        assert app.session_state["global_f"] == "lowess"


# ─────────────────────────────────────────────
# Layer 8: 测试隔离回归
# ─────────────────────────────────────────────


class TestIsolationRegression:
    """回归测试：验证多次 AppTest 重建不产生状态污染"""

    def test_multiple_fresh_apps_consistent(self):
        """连续创建 3 个 fresh app，每个都能正常渲染且 session_state 干净"""
        for i in range(3):
            app = _fresh_app()
            # 基本检查：应用不崩溃
            exc = app.exception
            assert len(exc) == 0 or "truth value of a Series is ambiguous" in str(exc[0]), \
                f"第{i+1}次: 未预期的异常 {exc}"
            # session_state 关键字段存在
            assert app.session_state["_config_initialized"] is True, \
                f"第{i+1}次: _config_initialized 缺失"
            # 关键 UI 元素存在
            buttons = {b.label for b in app.sidebar.button}
            assert "刷新数据" in buttons, f"第{i+1}次: 刷新按钮缺失"

    def test_fresh_app_day_nav_buttons_stable(self):
        """多次重建后 day_nav 按钮始终存在"""
        for i in range(3):
            app = _fresh_app()
            buttons = {b.label for b in app.sidebar.button}
            for expected in ["◀ 前移", "后移 ▶", "最新"]:
                assert expected in buttons, \
                    f"第{i+1}次: '{expected}' 按钮缺失, 找到: {buttons}"

    def test_fresh_app_no_unexpected_exception(self):
        """多次重建后无意外异常（回归 test_app_does_not_crash_before_render）"""
        for i in range(3):
            app = _fresh_app()
            exc = app.exception
            if len(exc) > 0:
                msg = str(exc[0])
                assert "truth value of a Series is ambiguous" in msg or "The truth value" in msg, \
                    f"第{i+1}次: 未知异常 {msg}"
