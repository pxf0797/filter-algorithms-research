"""
Shared pytest fixtures for filter_research tests.

Mocks the `streamlit` module before any project imports so that pytest can
import pure functions from streamlit/streamlit_app.py without triggering a
Streamlit runtime environment.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Ensure the streamlit/ package directory is importable
# ---------------------------------------------------------------------------
_src = Path(__file__).resolve().parent.parent / "streamlit"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

# ---------------------------------------------------------------------------
# Mock streamlit before any project module imports it
# ---------------------------------------------------------------------------
if "streamlit" not in sys.modules:
    mock_st = MagicMock()
    mock_st.cache_resource = lambda **kw: (lambda f: f)
    mock_st.cache_data = lambda **kw: (lambda f: f)
    sys.modules["streamlit"] = mock_st

import numpy as np
import pandas as pd
import pytest


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
