"""
PnL 曲线渲染公共模块 — 回测仪表盘与图表构建器共享的 PnL 计算与 trace 生成函数。

提供:
- 组合 PnL / 回撤计算
- 标准化的 PnL trace dict（做多/做空/组合曲线）
- 基线 shapes 和 yaxis 配置
"""

import numpy as np

from filter.constants.colors import COLORS, get_colors

# ── 常量 ──────────────────────────────────────────────────────────
PNL_BASELINE = 100.0  # PnL 以 100 为起点（百分比）


# ── 计算函数 ──────────────────────────────────────────────────────
def compute_combined_pnl(long_pnl: np.ndarray, short_pnl: np.ndarray) -> np.ndarray:
    """逐点取做多/做空 PnL 最大值（理想方向选择收益）。"""
    return np.maximum(long_pnl, short_pnl)


def compute_drawdown(pnl_array: np.ndarray) -> np.ndarray:
    """从运行峰值计算回撤百分比。"""
    peak = np.maximum.accumulate(pnl_array)
    return np.where(peak != 0, (pnl_array - peak) / peak * 100, 0.0)


# ── Trace 生成 ────────────────────────────────────────────────────
def make_pnl_long_trace(
    x, pnl, row=None,
    dash: str = "solid",
    width: float = 1.5,
    name: str = "做多PnL",
    opacity: float = 1.0,
) -> dict:
    """生成做多 PnL 曲线 trace dict。"""
    colors = get_colors()
    ax = {"xaxis": f"x{row}", "yaxis": f"y{row}"} if row else {}
    return dict(
        type="scattergl", x=x, y=pnl, mode="lines", name=name,
        line=dict(color=colors["pnl_long"], width=width, dash=dash),
        opacity=opacity, **ax,
    )


def make_pnl_short_trace(
    x, pnl, row=None,
    dash: str = "solid",
    width: float = 1.5,
    name: str = "做空PnL",
    opacity: float = 1.0,
) -> dict:
    """生成做空 PnL 曲线 trace dict。"""
    colors = get_colors()
    ax = {"xaxis": f"x{row}", "yaxis": f"y{row}"} if row else {}
    return dict(
        type="scattergl", x=x, y=pnl, mode="lines", name=name,
        line=dict(color=colors["pnl_short"], width=width, dash=dash),
        opacity=opacity, **ax,
    )


def make_pnl_combined_trace(
    x, combined, row=None,
    name: str = "max(做多, 做空) PnL",
) -> dict:
    """生成 max(做多,做空) 组合 PnL 曲线 trace dict（含填色）。"""
    colors = get_colors()
    ax = {"xaxis": f"x{row}", "yaxis": f"y{row}"} if row else {}
    return dict(
        type="scattergl", x=x, y=combined - PNL_BASELINE, mode="lines", name=name,
        line=dict(color=colors["pnl_combined"], width=2),
        fill="tozeroy", fillcolor=colors["pnl_combined_fill"],
        **ax,
    )


def make_drawdown_trace(x, pnl_array, row=None) -> dict:
    """生成回撤曲线 trace dict。"""
    colors = get_colors()
    dd = compute_drawdown(pnl_array)
    ax = {"xaxis": f"x{row}", "yaxis": f"y{row}"} if row else {}
    return dict(
        type="scattergl", x=x, y=dd, mode="lines", name="回撤 %",
        line=dict(color=colors["pnl_short"], width=1.5, dash="solid"),
        fill="tozeroy", fillcolor=colors["drawdown_fill"],
        **ax,
    )


def make_pnl_baseline_shape(row: int, y: float = PNL_BASELINE) -> dict:
    """生成 PnL 子图基线 shape dict。"""
    return dict(
        type="line", x0=0, x1=1, xref="paper", y0=y, y1=y,
        yref=f"y{row}", line=dict(color=COLORS["baseline"], dash="dash"),
        opacity=0.5,
    )


def make_pnl_yaxis_config(row: int) -> dict:
    """生成 PnL 子图 yaxis 配置。"""
    return {f"yaxis{row}": {"title_text": "PnL(%)", "ticksuffix": "%"}}
