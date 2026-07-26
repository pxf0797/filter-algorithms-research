"""
Shared pytest fixtures for filter_research tests.

Mocks the `streamlit` module before any project imports so that pytest can
import pure functions from filter/streamlit_app.py without triggering a
Streamlit runtime environment.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, settings

# ---------------------------------------------------------------------------
# Hypothesis profiles — CI 模式减少 max_examples 以加速 CI 运行
# ---------------------------------------------------------------------------
settings.register_profile("ci", max_examples=30, deadline=2000,
                          suppress_health_check=[HealthCheck.too_slow])
settings.register_profile("dev", max_examples=100)
if os.environ.get("CI"):
    settings.load_profile("ci")

# ---------------------------------------------------------------------------
# Ensure the filter/ package directory is importable
# ---------------------------------------------------------------------------
_src = Path(__file__).resolve().parent.parent / "filter"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

# ---------------------------------------------------------------------------
# Mock streamlit before any project module imports it
# ---------------------------------------------------------------------------
def _make_streamlit_mock():
    """创建 streamlit MagicMock，模拟核心装饰器/函数。"""
    mock_st = MagicMock()
    mock_st.cache_resource = lambda f=None, **kw: f if callable(f) else (lambda g: g)
    mock_st.cache_data = lambda f=None, **kw: f if callable(f) else (lambda g: g)
    mock_st.fragment = lambda f=None, **kw: f if callable(f) else (lambda g: g)
    return mock_st


# 收集阶段：pytest 导入各测试文件时，被测试模块通过 import streamlit 拿到 mock
if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = _make_streamlit_mock()


@pytest.fixture(autouse=True)
def _mock_streamlit_module():
    """全局 mock streamlit — 每次测试前确保 mock 就位。

    背景: test_app_ui.py 的模块级 fixture 通过 _fix_streamlit()
    删除 MagicMock 并导入真实的 streamlit 包。该 fixture teardown
    后不会还原 mock，导致后续测试文件（如 test_backtest_cli.py、
    test_charts.py）导入链受真实 streamlit 污染。

    本 autouse fixture 在每个测试函数运行前检测：若 streamlit 已
    被替换为真实模块，则重新注入 MagicMock。
    """
    st_mod = sys.modules.get("streamlit")
    if st_mod is None or "MagicMock" not in type(st_mod).__name__:
        sys.modules["streamlit"] = _make_streamlit_mock()
    yield
    # 不主动拆 mock — 下一个测试的 setup 会处理状态检查


# ---------------------------------------------------------------------------
# Widget-aware session_state mock — 模拟 Streamlit widget 生命周期约束
# ---------------------------------------------------------------------------

class WidgetKeyModifiedAfterInstantiationError(Exception):
    """模拟 StreamlitAPIException: widget 实例化后不可修改其绑定 key."""


class WidgetAwareSessionState(dict):
    """dict-like session_state，增加 widget 生命周期约束检测。

    模拟 Streamlit 规则：所有 widget 创建完成后 (lock)，禁止直接修改
    widget 绑定的 session_state key。非 widget key 始终安全。

    用法:
        ss = WidgetAwareSessionState()
        ss.register_widget("my_checkbox")     # widget 创建
        ss.lock()                             # 所有 widget 就绪，进入回调阶段
        ss["my_checkbox"] = True              # ❌ 抛出 WidgetKeyModifiedAfterInstantiationError
        ss["_pending_flag"] = True            # ✅ 非 widget key 始终安全

        # rerun 周期
        ss.begin_rerun()                      # 解锁 + 清除 widget 注册
        ss["my_checkbox"] = False             # ✅ widget 重新创建前安全
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._widget_keys: set = set()
        self._locked: bool = False

    def register_widget(self, key: str):
        """标记 key 为 widget 绑定。模拟 st.checkbox(... key=...) 等。"""
        self._widget_keys.add(key)

    def lock(self):
        """标记所有 widget 已创建完成，进入回调阶段。

        此后任何对 widget key 的直接赋值将触发异常。
        """
        self._locked = True

    def begin_rerun(self):
        """模拟 st.rerun() 后的新执行周期：解锁并清除 widget 注册。"""
        self._locked = False
        self._widget_keys.clear()

    def __setitem__(self, key, value):
        if self._locked and key in self._widget_keys:
            raise WidgetKeyModifiedAfterInstantiationError(
                f"st.session_state.{key} cannot be modified after the widget "
                f"with key '{key}' is instantiated."
            )
        super().__setitem__(key, value)


# ---- Signal / data fixtures -----------------------------------------------

@pytest.fixture
def constant_signal():
    """常量信号: 100个点全为1.0"""
    return np.ones(100)


@pytest.fixture
def noisy_sine():
    """含噪正弦波: sin(x/5) + N(0, 0.1)"""
    np.random.seed(42)
    x = np.arange(200, dtype=float)
    return np.sin(x / 5.0) + np.random.randn(200) * 0.1


@pytest.fixture
def clean_sine():
    """纯净正弦波: sin(x/5)"""
    x = np.arange(200, dtype=float)
    return np.sin(x / 5.0)


@pytest.fixture
def time_index():
    """时间索引数组"""
    return np.arange(200, dtype=float)


# ---- Datetime fixtures -----------------------------------------------------

@pytest.fixture
def sample_dates_daily():
    """日线日期 (tz-naive)"""
    return pd.date_range("2026-01-01", periods=120, freq="D")


@pytest.fixture
def sample_dates_intraday():
    """60分钟日期 (tz-aware HKT)"""
    return pd.date_range("2026-06-01 09:30", periods=120, freq="h", tz="Asia/Hong_Kong")


# =============================================================================
# P0-2: 全局清理 fixtures — 消除跨测试状态污染 (30个污染失败)
# =============================================================================

@pytest.fixture(autouse=True)
def _reset_db_connection():
    """每个测试后关闭线程本地数据库连接，防止跨测试连接泄漏。

    问题根因:
    filter.data.db 使用 ``threading.local()`` 存储每个线程的 sqlite3
    连接。若测试中使用 ``patch()`` 替换 ``get_conn`` 后上下文管理器
    在异常路径下未能恢复原始函数，残留的 mock 连接会导致后续
    ``BacktestRunner._bar_count`` 查询返回 0。

    本 fixture 在 teardown 阶段调用 ``close_conn()`` 清除当前线程的
    连接缓存（``_local.conn = None``），确保下个测试创建新连接。
    """
    yield  # ── 测试执行 ──
    try:
        from filter.data.db import close_conn
        close_conn()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset_streamlit_components():
    """每个测试后清理 streamlit components.v1.html mock 残留。

    问题根因:
    test_charts.py 通过 ``monkeypatch.setattr(st.components.v1, "html", ...)``
    替换 mock 内部的 ``html`` 子属性。若 monkeypatch 在异常路径下未能
    执行 undo（如测试内部 assert 失败后 monkeypatch 链断裂），后续测试
    调用 ``_render_plotly`` 时 ``st.components.v1.html()`` 返回空值或
    非预期对象，导致图表 HTML 为空。

    本 fixture 在 teardown 阶段检测 ``st.components.v1.html`` 是否为
    纯净的 MagicMock；若非（即被替换为函数/其他对象），则显式重置。
    """
    yield  # ── 测试执行 ──
    st_mod = sys.modules.get("streamlit")
    if st_mod is not None and "MagicMock" in type(st_mod).__name__:
        try:
            html_attr = st_mod.components.v1.html
            if not isinstance(html_attr, MagicMock):
                # monkeypatch 残留：html 被替换为非 Mock 对象
                st_mod.components.v1.html = MagicMock()
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _restore_engine_get_conn():
    """每个测试后确保 ``filter.backtest.engine.get_conn`` 是真实函数。

    问题根因:
    多个测试文件（test_engine.py, test_metrics_fix.py, test_backtest_core.py）
    使用 ``patch("filter.backtest.engine.get_conn", return_value=...)``
    替换模块级函数引用。若 patch 的 ``__exit__`` 在异常时无法恢复，
    ``BacktestRunner.__init__`` → ``_query_bar_count()`` → ``get_conn()``
    拿到 mock 对象，执行 ``SELECT COUNT(*)`` 返回错误值导致
    ``_bar_count == 0``。

    本 fixture 在 teardown 阶段检测 engine 模块中的 ``get_conn`` 是否
    被替换为 MagicMock；若是则恢复为 ``filter.data.db.get_conn`` 原引用。
    """
    yield  # ── 测试执行 ──
    try:
        import filter.backtest.engine as eng
        import filter.data.db as _dbmod
        if isinstance(eng.get_conn, MagicMock):
            eng.get_conn = _dbmod.get_conn
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _cleanup_gc_and_modules():
    """每个测试后主动回收内存，清理模块级缓存残留。

    问题根因:
    长时间运行的测试套件可能累积循环引用、失效的模块缓存和线程本地
    变量，导致后续测试行为异常（如 ParquetStore session dir UUID 碰撞
    引起的时间戳竞争、threading.local() 变量跨线程污染）。

    本 fixture 在 teardown 阶段调用 ``gc.collect()`` 强制回收不可达
    对象，帮助释放文件句柄和数据库连接。
    """
    yield  # ── 测试执行 ──
    import gc
    gc.collect()
