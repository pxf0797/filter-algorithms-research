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


# ---- Signal / data fixtures -----------------------------------------------

@pytest.fixture
def constant_signal():
    """常量信号: 100个点全为1.0"""
    return np.ones(100)


@pytest.fixture
def linear_signal():
    """线性信号: y = 0.1*x"""
    x = np.arange(100, dtype=float)
    return x * 0.1


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
def random_walk():
    """随机游走: 200步"""
    np.random.seed(42)
    return np.cumsum(np.random.randn(200) * 0.1) + 100.0


@pytest.fixture
def time_index():
    """时间索引数组"""
    return np.arange(200, dtype=float)


# ---- DataFrame fixtures ----------------------------------------------------

@pytest.fixture
def ohlc_df():
    """模拟OHLC DataFrame"""
    np.random.seed(42)
    n = 100
    close = np.cumsum(np.random.randn(n) * 0.5) + 100
    return pd.DataFrame({
        "Open": close - 0.1,
        "High": close + 0.3,
        "Low": close - 0.3,
        "Close": close,
    })


# ---- Datetime fixtures -----------------------------------------------------

@pytest.fixture
def sample_dates_daily():
    """日线日期 (tz-naive)"""
    return pd.date_range("2026-01-01", periods=120, freq="D")


@pytest.fixture
def sample_dates_intraday():
    """60分钟日期 (tz-aware HKT)"""
    return pd.date_range("2026-06-01 09:30", periods=120, freq="h", tz="Asia/Hong_Kong")


@pytest.fixture
def sample_dates_weekly():
    """周线日期 (tz-naive)"""
    return pd.date_range("2024-01-01", periods=90, freq="W")
