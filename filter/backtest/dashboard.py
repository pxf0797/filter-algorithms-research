"""
回测结果仪表盘 — Plotly KPI 卡片 + PnL/回撤曲线 + 交易明细表

输入 Parquet 文件路径或 DataFrame，渲染可交互的 Streamlit 仪表盘。
"""

from pathlib import Path
from typing import Optional, Union

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st


def render_backtest_dashboard(
    source: Union[str, Path, pd.DataFrame],
    metrics: Optional[dict] = None,
) -> None:
    """渲染回测结果仪表盘。

    Parameters
    ----------
    source : str, Path, or pd.DataFrame
        Parquet 文件路径或包含回测结果的 DataFrame。
    metrics : dict, optional
        预计算的指标 dict。若为 None 则自动从数据计算。
    """
    # ── 加载数据 ──
    if isinstance(source, pd.DataFrame):
        result_df = source
    else:
        path = Path(source)
        if not path.exists():
            st.error(f"文件不存在: {path}")
            return
        result_df = pd.read_parquet(path)

    if result_df.empty:
        st.info("回测数据为空")
        return

    # ── 检测可用的 PnL 视图 ──
    pnl_views = _detect_pnl_views(result_df)
    if not pnl_views:
        st.info("未找到 PnL 数据列")
        return

    # 默认使用第一个视图的 PnL
    primary_view = pnl_views[0]
    long_col = f"{primary_view}_pnl_long"
    short_col = f"{primary_view}_pnl_short"
    sig_col = f"{primary_view}_sig"

    long_pnl = result_df[long_col].values.astype(float)
    short_pnl = result_df[short_col].values.astype(float)
    n_bars = len(result_df)

    # ── 自动计算指标（若未预计算）──
    if metrics is None:
        metrics = _compute_metrics_from_pnl(long_pnl, short_pnl, result_df, primary_view, n_bars)

    # ── KPI 指标卡片 ──
    _render_kpi_cards(metrics)

    # ── PnL 曲线 + 回撤 ──
    _render_pnl_chart(long_pnl, short_pnl, result_df)

    # ── 视图对比（多视图时）──
    if len(pnl_views) > 1:
        _render_view_comparison(result_df, pnl_views)

    # ── 交易明细表 ──
    _render_trade_table(result_df, primary_view)

    # ── 信号统计 ──
    if sig_col in result_df.columns:
        _render_signal_stats(result_df, primary_view)


# ====================================================================
# Internal helpers
# ====================================================================


def _detect_pnl_views(df: pd.DataFrame) -> list[str]:
    """检测 DataFrame 中有 PnL 列的视图前缀列表。"""
    views = set()
    for col in df.columns:
        if col.endswith("_pnl_long"):
            prefix = col.rsplit("_pnl_long", 1)[0]
            if f"{prefix}_pnl_short" in df.columns:
                views.add(prefix)
    return sorted(views)


def _compute_metrics_from_pnl(
    long_pnl: np.ndarray,
    short_pnl: np.ndarray,
    df: pd.DataFrame,
    view_prefix: str,
    n_bars: int,
) -> dict:
    """从 PnL 数组计算核心指标，补充交易统计。"""
    from filter.backtest.metrics import compute_backtest_metrics

    # 重建简单的交易记录（从 trade 列提取）
    trade_records = _extract_trade_records(df, view_prefix)

    return compute_backtest_metrics(
        long_pnl=long_pnl,
        short_pnl=short_pnl,
        trade_records=trade_records,
        n_bars=n_bars,
    )


def _extract_trade_records(df: pd.DataFrame, view_prefix: str) -> list[dict]:
    """从 DataFrame 的 trade 列提取交易记录列表（向量化版本）。"""
    trade_col = f"{view_prefix}_trade"
    return_col = f"{view_prefix}_trade_return"
    reason_col = f"{view_prefix}_trade_reason"

    if trade_col not in df.columns:
        return []

    # 向量化筛选：trade 列为非空字符串
    mask = (
        df[trade_col].notna()
        & (df[trade_col].astype(str).str.strip() != "")
    )

    if not mask.any():
        return []

    # 确定可用的列并映射到期望的键名
    col_map = {}
    if return_col in df.columns:
        col_map[return_col] = "return_pct"
    if reason_col in df.columns:
        col_map[reason_col] = "reason"

    if not col_map:
        return []

    selected = df.loc[mask, list(col_map.keys())].copy()
    selected = selected.rename(columns=col_map)

    # 填充默认值，匹配原始逐行逻辑
    if "return_pct" in selected.columns:
        selected["return_pct"] = selected["return_pct"].fillna(0).astype(float)
    if "reason" in selected.columns:
        selected["reason"] = selected["reason"].fillna("").astype(str)

    return selected.to_dict("records")


def _render_kpi_cards(metrics: dict) -> None:
    """渲染 KPI 指标卡片行。"""
    import math

    # Sanitize NaN/Inf values that would crash f-string formatting.
    _safe = {}
    for k, v in metrics.items():
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            _safe[k] = 0
        else:
            _safe[k] = v

    st.subheader("核心指标")

    cols = st.columns(6)
    cols[0].metric(
        "累计收益",
        f"{_safe.get('total_return_pct', 0):.2f}%",
    )
    cols[1].metric(
        "Sharpe",
        f"{_safe.get('sharpe_ratio', 0):.3f}",
    )
    cols[2].metric(
        "最大回撤",
        f"{_safe.get('max_drawdown_pct', 0):.2f}%",
    )
    cols[3].metric(
        "胜率",
        f"{_safe.get('win_rate_pct', 0):.1f}%",
    )
    cols[4].metric(
        "交易次数",
        str(_safe.get('total_trades', 0)),
    )
    cols[5].metric(
        "Calmar",
        f"{_safe.get('calmar_ratio', 0):.3f}",
    )

    cols2 = st.columns(6)
    cols2[0].metric(
        "盈利因子",
        f"{_safe.get('profit_factor', 0):.2f}",
    )
    cols2[1].metric(
        "Sortino",
        f"{_safe.get('sortino_ratio', 0):.3f}",
    )
    cols2[2].metric(
        "年化收益",
        f"{_safe.get('annualized_return_pct', 0):.2f}%",
    )
    cols2[3].metric(
        "年化波动",
        f"{_safe.get('annualized_volatility_pct', 0):.2f}%",
    )
    cols2[4].metric(
        "最大水下天数",
        str(_safe.get('max_drawdown_duration', 0)),
    )
    cols2[5].metric(
        "平均交易收益",
        f"{_safe.get('avg_trade_return_pct', 0):.2f}%",
    )

    # 指标 CSV 导出
    metrics_df = pd.DataFrame(list(_safe.items()), columns=["指标", "数值"])
    csv = metrics_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="导出指标汇总 CSV",
        data=csv,
        file_name="backtest_metrics.csv",
        mime="text/csv",
    )


def _render_pnl_chart(
    long_pnl: np.ndarray,
    short_pnl: np.ndarray,
    df: pd.DataFrame,
) -> None:
    """渲染 PnL 累积曲线和回撤图。

    ``combined = np.maximum(long_pnl, short_pnl)`` 在每根 bar 上取做多和做空
    PnL 的逐点最大值。这反映"选择对的方向"的理想收益 —— 信号切换时曲线可能不连续
    (从做多 PnL 跳变到做空 PnL)，不代表实盘可实现的收益。

    为提供完整的上下文，此图同时用虚线绘制 ``long_pnl`` 和 ``short_pnl``
    两条独立曲线。
    """
    st.subheader("PnL 曲线")

    combined = np.maximum(long_pnl, short_pnl)
    cum_pnl = combined - 100.0  # 相对于 100 基准

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.65, 0.35],
        subplot_titles=("累计 PnL", "回撤"),
        vertical_spacing=0.08,
    )

    # PnL 曲线
    x_vals = list(range(len(cum_pnl)))

    # 做多 / 做空独立曲线 (虚线)
    fig.add_trace(
        go.Scattergl(
            x=x_vals, y=long_pnl - 100.0,
            mode="lines",
            name="做多 PnL",
            line=dict(color="#3fb950", width=1, dash="dot"),
            opacity=0.5,
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scattergl(
            x=x_vals, y=short_pnl - 100.0,
            mode="lines",
            name="做空 PnL",
            line=dict(color="#f85149", width=1, dash="dot"),
            opacity=0.5,
        ),
        row=1, col=1,
    )

    # max 组合曲线 (实线)
    fig.add_trace(
        go.Scattergl(
            x=x_vals, y=cum_pnl,
            mode="lines",
            name="max(做多, 做空) PnL",
            line=dict(color="#58a6ff", width=2),
            fill="tozeroy",
            fillcolor="rgba(88,166,255,0.08)",
        ),
        row=1, col=1,
    )
    # 零线
    fig.add_hline(y=0, line_dash="dash", line_color="rgba(255,255,255,0.3)", row=1, col=1)

    # 回撤
    peak = np.maximum.accumulate(combined)
    drawdown = np.where(peak != 0, (combined - peak) / peak * 100, 0.0)
    fig.add_trace(
        go.Scattergl(
            x=x_vals, y=drawdown,
            mode="lines",
            name="回撤 %",
            line=dict(color="#f85149", width=1.5),
            fill="tozeroy",
            fillcolor="rgba(248,81,73,0.15)",
        ),
        row=2, col=1,
    )

    fig.update_layout(
        template="plotly_dark",
        height=500,
        margin=dict(l=10, r=10, t=40, b=10),
        hovermode="x unified",
        showlegend=False,
    )
    fig.update_yaxes(title_text="PnL", row=1, col=1)
    fig.update_yaxes(title_text="回撤 %", row=2, col=1)

    st.plotly_chart(fig, use_container_width=True, config={"modeBarButtonsToAdd": ["downloadImage"]})


def _render_view_comparison(df: pd.DataFrame, views: list[str]) -> None:
    """多视图 PnL 对比图。"""
    st.subheader("视图 PnL 对比")

    fig = go.Figure()

    colors = ["#58a6ff", "#3fb950", "#f85149", "#d2991d"]
    for i, v in enumerate(views):
        long_col = f"{v}_pnl_long"
        short_col = f"{v}_pnl_short"
        combined = np.maximum(
            df[long_col].values.astype(float),
            df[short_col].values.astype(float),
        )
        cum = combined - 100.0
        fig.add_trace(go.Scattergl(
            y=cum,
            mode="lines",
            name=v,
            line=dict(color=colors[i % len(colors)], width=2),
        ))

    fig.add_hline(y=0, line_dash="dash", line_color="rgba(255,255,255,0.3)")

    fig.update_layout(
        template="plotly_dark",
        height=350,
        margin=dict(l=10, r=10, t=40, b=10),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )

    st.plotly_chart(fig, use_container_width=True, config={"modeBarButtonsToAdd": ["downloadImage"]})


def _render_trade_table(df: pd.DataFrame, view_prefix: str) -> None:
    """渲染交易明细表。"""
    st.subheader("交易明细")

    trade_col = f"{view_prefix}_trade"
    return_col = f"{view_prefix}_trade_return"
    reason_col = f"{view_prefix}_trade_reason"

    trade_cols = [c for c in [trade_col, return_col, reason_col] if c in df.columns]
    if not trade_cols:
        st.caption("无交易明细数据")
        return

    # 筛选有交易的行
    mask = df[trade_col].notna() & (df[trade_col] != "")
    trades_df = df.loc[mask, trade_cols].copy()

    if trades_df.empty:
        st.caption("无交易记录")
        return

    # 美化列名
    rename_map = {
        trade_col: "交易类型",
        return_col: "收益%",
        reason_col: "离场原因",
    }
    trades_df = trades_df.rename(columns={k: v for k, v in rename_map.items() if k in trades_df.columns})

    st.dataframe(trades_df, use_container_width=True, height=300)

    # CSV 导出
    csv = trades_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="导出交易明细 CSV",
        data=csv,
        file_name=f"{view_prefix}_trades.csv",
        mime="text/csv",
    )


def _render_signal_stats(df: pd.DataFrame, view_prefix: str) -> None:
    """渲染信号统计信息。"""
    sig_col = f"{view_prefix}_sig"
    if sig_col not in df.columns:
        return

    st.subheader("信号分布")
    sig_vals = df[sig_col].dropna()

    long_count = int((sig_vals == 1).sum())
    short_count = int((sig_vals == -1).sum())
    neutral_count = int((sig_vals == 0).sum())
    total = long_count + short_count + neutral_count

    if total == 0:
        st.caption("无信号数据")
        return

    cols = st.columns(4)
    cols[0].metric("做多信号", long_count, f"{long_count / total:.1%}")
    cols[1].metric("做空信号", short_count, f"{short_count / total:.1%}")
    cols[2].metric("观望", neutral_count, f"{neutral_count / total:.1%}")
    cols[3].metric("总 bar 数", total)
