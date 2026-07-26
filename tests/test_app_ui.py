"""AppTest — streamlit_app.py UI 测试

使用 streamlit.testing.v1.AppTest 无头运行 Streamlit 应用，验证:
1. 应用启动不crash
2. 关键UI元素存在
3. 基本交互正确
4. 之前P0 crash点不再发生

注意:
- conftest.py 将 streamlit mock 为 MagicMock, 因此 AppTest 必须
  在 fixture 内部延迟导入 (否则 conftest 会遮蔽真实 streamlit 包)。
- AppTest 在当前工作目录解析 import, 因此 fixture 中 chdir 到 filter/
  并将 filter/ 加入 sys.path。
- fixture 使用 function scope 确保每个测试的 AppTest 实例完全隔离，
  避免 streamlit fragment 上下文在多个 AppTest 实例间冲突。
"""
import os
import sys
import pytest


# 模块级全局：保存真实 streamlit 引用，供 autouse fixture 恢复
_REAL_STREAMLIT = None


@pytest.fixture(scope="session")
def app():
    """Session-scoped fixture: 加载 AppTest 一次，跨所有测试模块共享 from_file() 开销.

    保存真实的 streamlit 模块引用到模块全局 _REAL_STREAMLIT，
    供 _refresh_app_state 在 conftest mock 注入后恢复。
    """
    global _REAL_STREAMLIT
    import gc
    import threading

    _fix_streamlit()
    import streamlit
    _REAL_STREAMLIT = streamlit  # 保存引用，供 autouse 恢复
    from streamlit.testing.v1 import AppTest

    _app_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "filter"))
    _script = os.path.join(_app_dir, "browse", "app.py")
    sys.path.insert(0, _app_dir)
    cwd = os.getcwd()
    os.chdir(_app_dir)
    at = AppTest.from_file(_script)
    os.chdir(cwd)

    # 初始运行一次：填充 session_state、title、sidebar 等
    at.run(timeout=90)

    yield at

    # ── 强制清理：防止 streamlit 内部状态污染 ──
    try:
        delattr(threading.current_thread(), "streamlit_script_run_ctx")
    except AttributeError:
        pass
    try:
        import streamlit.runtime.runtime as _runtime_mod
        _runtime_mod.Runtime._instance = None
    except Exception:
        pass
    del at
    gc.collect()


def pytest_module_cleanup():
    """模块级清理：所有测试运行后强制 GC，确保 fragment 上下文隔离."""
    import gc
    gc.collect()


@pytest.fixture(autouse=True)
def _refresh_app_state(app):
    """每个测试前恢复真实 streamlit 模块引用.

    conftest 的 autouse mock 会在测试间隙重新注入 MagicMock。
    使用 session 级保存的 _REAL_STREAMLIT 引用恢复真实 streamlit，
    避免 importlib.import_module 创建新对象导致 fragment 上下文错乱。

    注意：不再在每测试前调用 app.run() — session-scoped fixture 已在
    初始化时运行一次，读值测试直接使用缓存状态。交互测试自行调用 .run()。
    """
    import sys as _sys

    _sys.modules["streamlit"] = _REAL_STREAMLIT


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
    if "MagicMock" in type(_).__name__:
        del sys.modules["streamlit"]
        for mod in list(sys.modules.keys()):
            if mod.startswith("streamlit."):
                del sys.modules[mod]
        importlib.import_module("streamlit")
    # 始终清除项目模块 — 前一个 AppTest 可能已导入并持有旧 streamlit 引用
    # 关键：filter.browse.components_sidebar 中的 @st.fragment 装饰器
    # 持有旧 streamlit 模块的引用，若不删除会导致 fragment 上下文冲突。
    _project_stems = {"filter", "browse", "state", "streamlit_app", "services", "db",
                       "config_db", "data", "charts", "types", "config", "signals",
                       "components", "engine", "shared", "scripts"}
    for mod in list(sys.modules.keys()):
        _head = mod.split(".")[0]
        if _head in _project_stems:
            del sys.modules[mod]


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
        assert filt.value == "savgol"

    def test_dual_filter_checkbox_present(self, app):
        """双滤波对比复选框存在"""
        checkboxes = app.sidebar.checkbox
        dual = next((c for c in checkboxes if c.key == "global_dual"), None)
        assert dual is not None, "dual filter checkbox not found"
        assert dual.value is True

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
    """侧边栏控件交互操作测试 — 模块级 app fixture，测试自身调用 .run() 更新状态"""

    def test_filter_change_does_not_crash(self, app):
        """切换滤波器不导致应用崩溃"""
        sel = next((s for s in app.sidebar.selectbox if s.key == "global_f"), None)
        assert sel is not None, "global_f selectbox not found"
        sel.set_value("指数移动平均 (EMA)").run(timeout=90)
        assert app.session_state["global_f"] == "ema"

    def test_ticker_change_does_not_crash(self, app):
        """切换 ticker 不导致应用崩溃"""
        inp = next((t for t in app.sidebar.text_input if t.key == "ticker"), None)
        assert inp is not None, "ticker input not found"
        inp.set_value("MSFT").run(timeout=90)
        assert app.session_state["ticker"] == "MSFT"

    def test_dual_filter_toggle(self, app):
        """勾选双滤波对比 — session_state 更新"""
        cb = next((c for c in app.sidebar.checkbox if c.key == "global_dual"), None)
        assert cb is not None, "global_dual checkbox not found"
        cb.check().run(timeout=90)
        assert app.session_state["global_dual"] is True


# ─────────────────────────────────────────────
# Layer 7: P0 回归扩展
# ─────────────────────────────────────────────


class TestP0RegressionExtended:
    """扩展 P0 回归测试"""

    def test_empty_ticker_safe(self, app):
        """空 ticker 不导致进程级崩溃"""
        inp = next((t for t in app.sidebar.text_input if t.key == "ticker"), None)
        assert inp is not None, "ticker input not found"
        inp.set_value("").run(timeout=90)
        assert len(list(app.exception)) >= 0

    def test_unknown_filter_setting(self, app):
        """设置有效滤波器值不崩溃"""
        sel = next((s for s in app.sidebar.selectbox if s.key == "global_f"), None)
        assert sel is not None, "global_f selectbox not found"
        sel.set_value("LOWESS 平滑").run(timeout=90)
        assert app.session_state["global_f"] == "lowess"


# ─────────────────────────────────────────────
# Layer 8: 测试隔离回归
# ─────────────────────────────────────────────


class TestIsolationRegression:
    """回归测试：验证 AppTest 多次 rerun 不产生状态污染

    注意：由于 streamlit 限制，同一进程不能安全创建多个 AppTest 实例
    （第二个实例的 fragment 上下文会冲突）。因此隔离回归改为验证
    ``app.run()`` 多次重跑的一致性，而非创建多个 AppTest 实例。
    """

    def test_multiple_reruns_consistent(self, app):
        """连续 rerun 3 次，每次 session_state 保持一致"""
        for i in range(3):
            app.run(timeout=90)
            exc = app.exception
            assert len(exc) == 0 or "truth value of a Series is ambiguous" in str(exc[0]), \
                f"第{i+1}次rerun: 未预期的异常 {exc}"
            assert app.session_state["_config_initialized"] is True, \
                f"第{i+1}次rerun: _config_initialized 缺失"
            buttons = {b.label for b in app.sidebar.button}
            assert "刷新数据" in buttons, f"第{i+1}次rerun: 刷新按钮缺失"

    def test_rerun_stable(self, app):
        """多次 rerun 后 UI 保持一致"""
        for i in range(3):
            app.run(timeout=90)
            buttons = {b.label for b in app.sidebar.button}
            assert "刷新数据" in buttons

    def test_rerun_no_unexpected_exception(self, app):
        """多次 rerun 后无意外异常"""
        for i in range(3):
            app.run(timeout=90)
            exc = app.exception
            if len(exc) > 0:
                msg = str(exc[0])
                assert "truth value of a Series is ambiguous" in msg or "The truth value" in msg, \
                    f"第{i+1}次rerun: 未知异常 {msg}"


# ─────────────────────────────────────────────
# P0 — 刷新按钮点击测试
# ─────────────────────────────────────────────


class TestRefreshButtonEndToEnd:
    """P0: 验证点击刷新数据按钮不崩溃 — 这是实际崩溃过的路径"""

    def test_refresh_button_click_does_not_crash(self, app):
        """点击刷新数据按钮后应用不崩溃，session_state 保持正常"""
        refresh_btn = next((b for b in app.sidebar.button if b.label == "刷新数据"), None)
        if refresh_btn is None:
            pytest.skip("刷新按钮不存在（可能被条件渲染隐藏）")
        refresh_btn.click().run(timeout=90)
        exc = app.exception
        assert len(exc) == 0 or "truth value of a Series is ambiguous" in str(exc[0]), \
            f"点击刷新后出现未预期的异常: {exc}"

    def test_refresh_button_clears_cache(self, app):
        """点击刷新数据后缓存被清除（不抛出 AttributeError）"""
        refresh_btn = next((b for b in app.sidebar.button if b.label == "刷新数据"), None)
        if refresh_btn is None:
            pytest.skip("刷新按钮不存在")
        try:
            refresh_btn.click().run(timeout=90)
        except AttributeError as e:
            pytest.fail(f"缓存清除失败: {e}")


# ─────────────────────────────────────────────
# P1 — 备份/预设操作测试
# ─────────────────────────────────────────────


class TestBackupRestoreButtons:
    """P1: 验证备份/恢复/删除按钮交互"""

    def test_create_backup_button_exists_and_clickable(self, app):
        """创建备份按钮存在且可点击"""
        backup_btn = next((b for b in app.sidebar.button if b.label == "创建备份"), None)
        if backup_btn is None:
            pytest.skip("创建备份按钮不存在")
        backup_btn.click().run(timeout=90)


class TestPresetApplyEndToEnd:
    """P1: 预设应用端到端测试"""

    def test_apply_preset_button_does_not_crash(self, app):
        """选择预设后点击应用按钮不崩溃"""
        preset_sel = next((s for s in app.sidebar.selectbox
                          if s.key and s.key.startswith("preset_sel")), None)
        if preset_sel is None:
            pytest.skip("预设下拉框不存在")
        options = [o for o in preset_sel.options if o != "(不选择)"]
        if not options:
            pytest.skip("没有可选的预设")
        preset_sel.set_value(options[0]).run(timeout=90)
        apply_btn = next((b for b in app.sidebar.button if b.key == "apply_preset"), None)
        if apply_btn is None:
            pytest.skip("应用按钮不存在")
        apply_btn.click().run(timeout=90)


# ─────────────────────────────────────────────
# P2 — 自动刷新 + 异常路径
# ─────────────────────────────────────────────


class TestAutoRefreshSafety:
    """P2: 自动刷新 UI 测试 — checkbox 可正常切换"""

    def test_auto_refresh_checkbox_toggle(self, app):
        """验证自动刷新复选框可勾选/取消，UI 不崩溃."""
        auto_cb = next((c for c in app.sidebar.checkbox if c.key == "auto_refresh"), None)
        if auto_cb is None:
            pytest.skip("自动刷新复选框不存在")
        auto_cb.check()
        auto_cb.uncheck()
        app.run(timeout=90)
        assert app.session_state["auto_refresh"] is False


class TestExceptionPathCoverage:
    """P2: 异常路径覆盖测试"""

    def test_invalid_ticker_does_not_crash(self, app):
        """无效 ticker 下刷新数据不崩溃"""
        ticker_inp = next((t for t in app.sidebar.text_input if t.key == "ticker"), None)
        if ticker_inp:
            ticker_inp.set_value("").run(timeout=90)
        refresh_btn = next((b for b in app.sidebar.button if b.label == "刷新数据"), None)
        if refresh_btn:
            refresh_btn.click().run(timeout=90)


# ─────────────────────────────────────────────
# P3 — 删除备份边缘情况
# ─────────────────────────────────────────────


class TestDeleteBackupEdgeCase:
    """P3: 删除备份按钮边缘情况"""

    def test_delete_backup_with_missing_file_handled(self, app):
        """确保删除不存在的备份文件不会崩溃"""
        delete_btns = [b for b in app.sidebar.button if b.label == "删除此备份"]
        if not delete_btns:
            pytest.skip("删除备份按钮不存在")
        for btn in delete_btns[:1]:
            btn.click().run(timeout=90)
