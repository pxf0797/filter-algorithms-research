"""
tests/test_snapshots.py — Syrupy 快照测试

覆盖:
1. Plotly Figure 序列化快照（to_dict）
2. Plotly Figure JSON 字符串快照
3. 滤波输出数组快照
4. 施密特触发器输出快照
"""

import sys
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
import pytest

# Ensure filter_app is importable
_src = Path(__file__).resolve().parent.parent / "filter_app"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from engine.filters import (
    apply_savgol,
    apply_ema,
    apply_sma,
    apply_median,
    _schmitt_trigger,
)


# ═════════════════════════════════════════════════════════════════════════
# Plotly Figure 快照
# ═════════════════════════════════════════════════════════════════════════


def test_plotly_candlestick_figure_snapshot(snapshot):
    """Plotly K线图 + 滤波曲线的 to_dict 快照"""
    np.random.seed(42)
    n = 60
    dates = np.arange(n)
    close = np.cumsum(np.random.randn(n) * 0.5) + 100

    ohlc = {
        "Open": close - 0.1,
        "High": close + 0.3,
        "Low": close - 0.3,
        "Close": close,
    }

    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=dates,
        open=ohlc["Open"],
        high=ohlc["High"],
        low=ohlc["Low"],
        close=ohlc["Close"],
        name="K线",
    ))
    fig.add_trace(go.Scatter(
        x=dates, y=close, mode="lines",
        name="收盘", line=dict(color="#5f6c80", width=1.0),
    ))
    fig.update_layout(
        title="K线图快照测试",
        xaxis_title="bar index",
        yaxis_title="价格",
        template="plotly_dark",
    )

    assert snapshot == fig.to_dict()


def test_plotly_schmitt_trigger_figure_snapshot(snapshot):
    """Plotly 施密特触发器子图的 to_dict 快照"""
    np.random.seed(42)
    n = 100
    x = np.arange(n, dtype=float)
    noisy = np.sin(x / 5.0) + np.random.randn(n) * 0.1
    filtered = apply_savgol(noisy, x, window=21, order=2)
    v = np.gradient(filtered)
    a = np.gradient(v)
    schmitt = _schmitt_trigger(v, a, ewma_span=30, k_eps=0.15, sigma_min=0.05)

    fig = go.Figure()
    # 加速度 & ±ε 子图
    fig.add_trace(go.Scatter(
        x=x, y=schmitt["eps"], mode="lines",
        name="+ε", line=dict(color="#f85149", width=0.8, dash="dash"),
    ))
    fig.add_trace(go.Scatter(
        x=x, y=-schmitt["eps"], mode="lines",
        name="-ε", line=dict(color="#f85149", width=0.8, dash="dash"),
    ))
    fig.add_trace(go.Scatter(
        x=x, y=a, mode="lines",
        name="加速度", line=dict(color="#d2991d", width=1.5),
    ))
    fig.add_trace(go.Scatter(
        x=x, y=schmitt["sig"].astype(float), mode="lines",
        name="信号", line=dict(color="#58a6ff", width=2, shape="hv"),
    ))
    fig.update_layout(
        title="施密特触发器快照测试",
        xaxis_title="bar index",
        template="plotly_dark",
    )

    assert snapshot == fig.to_dict()


# ═════════════════════════════════════════════════════════════════════════
# 滤波输出数组快照
# ═════════════════════════════════════════════════════════════════════════


def test_savgol_filter_output_snapshot(snapshot):
    """Savgol 滤波输出数组快照"""
    np.random.seed(42)
    n = 80
    x = np.arange(n, dtype=float)
    noisy = np.sin(x / 5.0) + np.random.randn(n) * 0.1
    filtered = apply_savgol(noisy, x, window=21, order=2)

    # 截取部分便于比对：前20和后20点（边缘效应区域）
    result = {
        "n": n,
        "noisy_head_10": noisy[:10].tolist(),
        "filtered_head_10": filtered[:10].tolist(),
        "noisy_tail_10": noisy[-10:].tolist(),
        "filtered_tail_10": filtered[-10:].tolist(),
        "filtered_mean": float(np.mean(filtered)),
        "filtered_std": float(np.std(filtered)),
    }

    assert snapshot == result


def test_multi_filter_comparison_snapshot(snapshot):
    """多种滤波器对同一信号的处理结果对比快照"""
    np.random.seed(42)
    n = 60
    x = np.arange(n, dtype=float)
    noisy = np.sin(x / 5.0) + np.random.randn(n) * 0.1

    filters_output = {}
    # 4 种滤波器的输出（截取区间 [20:40] 避免边缘效应）
    filters_output["sma_11"] = apply_sma(noisy, x, window=11)[20:40].tolist()
    filters_output["ema_10"] = apply_ema(noisy, x, span=10)[20:40].tolist()
    filters_output["savgol_21_2"] = apply_savgol(noisy, x, window=21, order=2)[20:40].tolist()
    filters_output["median_7"] = apply_median(noisy, x, window=7)[20:40].tolist()

    # 同时记录原始信号段便于比对
    filters_output["_noisy_segment"] = noisy[20:40].tolist()
    filters_output["_meta_n"] = n

    assert snapshot == filters_output


def test_schmitt_trigger_output_snapshot(snapshot):
    """施密特触发器完整输出结构快照"""
    np.random.seed(42)
    n = 80
    x = np.arange(n, dtype=float)
    noisy = np.sin(x / 5.0) + np.random.randn(n) * 0.1
    filtered = apply_savgol(noisy, x, window=21, order=2)
    v = np.gradient(filtered)
    a = np.gradient(v)
    result = _schmitt_trigger(v, a, ewma_span=30, k_eps=0.15, sigma_min=0.05)

    # 快照关键统计而非完整数组（太大会降低可读性）
    snapshot_data = {
        "n": n,
        "sig_head_20": result["sig"][:20].tolist(),
        "sig_tail_20": result["sig"][-20:].tolist(),
        "eps_mean": float(np.mean(result["eps"])),
        "eps_std": float(np.std(result["eps"])),
        "sigma_v_mean": float(np.mean(result["sigma_v"])),
        "dur_mean": float(np.mean(result["dur"])),
        "dur_max": int(np.max(result["dur"])),
        "sig_unique": [int(v) for v in sorted(np.unique(result["sig"]).tolist())],
    }

    assert snapshot == snapshot_data
