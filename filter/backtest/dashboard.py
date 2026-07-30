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

from filter.common.pnl_renderer import (
    PNL_BASELINE,
    compute_combined_pnl,
    make_pnl_long_trace,
    make_pnl_short_trace,
    make_pnl_combined_trace,
    make_drawdown_trace,
)
from filter.constants.colors import COLORS, get_colors, view_color


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
    _render_pnl_chart(long_pnl, short_pnl, result_df, primary_view)

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
    """渲染 KPI 指标卡片 — 三层分层展示。

    Tier 1 (核心): Sharpe, 最大回撤, 胜率, 总PnL — 大号 KPI 卡片
    Tier 2 (次要): 年化收益, 波动率, Calmar — 小号指标行
    Tier 3 (详情): 其余指标 — 折叠面板
    """
    import math

    # Sanitize NaN/Inf values that would crash f-string formatting.
    _safe = {}
    for k, v in metrics.items():
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            _safe[k] = 0
        else:
            _safe[k] = v

    st.subheader("核心指标")

    # ── Tier 1: 核心 KPI（大号卡片）──
    t1_cols = st.columns(4)
    t1_cols[0].metric(
        "Sharpe",
        f"{_safe.get('sharpe_ratio', 0):.3f}",
    )
    t1_cols[1].metric(
        "最大回撤",
        f"{_safe.get('max_drawdown_pct', 0):.2f}%",
    )
    t1_cols[2].metric(
        "胜率",
        f"{_safe.get('win_rate_pct', 0):.1f}%",
    )
    t1_cols[3].metric(
        "总PnL",
        f"{_safe.get('total_return_pct', 0):.2f}%",
    )

    # ── Tier 2: 次要指标（小号指标行）──
    t2_cols = st.columns(3)
    t2_cols[0].metric(
        "年化收益",
        f"{_safe.get('annualized_return_pct', 0):.2f}%",
    )
    t2_cols[1].metric(
        "年化波动",
        f"{_safe.get('annualized_volatility_pct', 0):.2f}%",
    )
    t2_cols[2].metric(
        "Calmar",
        f"{_safe.get('calmar_ratio', 0):.3f}",
    )

    # ── Tier 3: 详情指标（折叠面板）──
    with st.expander("查看详细指标", expanded=False):
        d_cols = st.columns(6)
        d_cols[0].metric(
            "交易次数",
            str(_safe.get('total_trades', 0)),
        )
        d_cols[1].metric(
            "盈利因子",
            f"{_safe.get('profit_factor', 0):.2f}",
        )
        d_cols[2].metric(
            "Sortino",
            f"{_safe.get('sortino_ratio', 0):.3f}",
        )
        d_cols[3].metric(
            "最大水下天数",
            str(_safe.get('max_drawdown_duration', 0)),
        )
        d_cols[4].metric(
            "平均交易收益",
            f"{_safe.get('avg_trade_return_pct', 0):.2f}%",
        )
        d_cols[5].metric(
            "盈利交易",
            str(_safe.get('winning_trades', 0)),
        )

        d_cols2 = st.columns(6)
        d_cols2[0].metric(
            "亏损交易",
            str(_safe.get('losing_trades', 0)),
        )
        d_cols2[1].metric(
            "平均盈利%",
            f"{_safe.get('avg_win_pct', 0):.2f}%",
        )
        d_cols2[2].metric(
            "平均亏损%",
            f"{_safe.get('avg_loss_pct', 0):.2f}%",
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
    view_prefix: str = "",
) -> None:
    """渲染 PnL 累积曲线和回撤图。

    ``combined = np.maximum(long_pnl, short_pnl)`` 在每根 bar 上取做多和做空
    PnL 的逐点最大值。这反映"选择对的方向"的理想收益 —— 信号切换时曲线可能不连续
    (从做多 PnL 跳变到做空 PnL)，不代表实盘可实现的收益。

    为提供完整的上下文，此图同时用虚线绘制做多 ``long_pnl`` 和细实线绘制做空
    ``short_pnl`` 两条独立曲线。
    """
    st.subheader("PnL 曲线")

    combined = compute_combined_pnl(long_pnl, short_pnl)
    colors = get_colors()

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.65, 0.35],
        subplot_titles=("累计 PnL", "回撤"),
        vertical_spacing=0.08,
    )

    # PnL 曲线
    x_vals = list(range(len(combined)))

    # 做多独立曲线 (虚线)
    fig.add_trace(
        go.Scattergl(**make_pnl_long_trace(
            x_vals, long_pnl - PNL_BASELINE,
            dash="dot", width=1, opacity=0.5, name="做多 PnL",
        )),
        row=1, col=1,
    )
    # 做空独立曲线 (细实线)
    fig.add_trace(
        go.Scattergl(**make_pnl_short_trace(
            x_vals, short_pnl - PNL_BASELINE,
            dash="solid", width=0.8, opacity=0.5, name="做空 PnL",
        )),
        row=1, col=1,
    )

    # max 组合曲线 (实线)
    fig.add_trace(
        go.Scattergl(**make_pnl_combined_trace(x_vals, combined)),
        row=1, col=1,
    )
    # 零线
    fig.add_hline(y=0, line_dash="dash", line_color=COLORS["zero_line"], row=1, col=1)

    # ── 入场/离场标记 ──
    if view_prefix:
        trade_col = f"{view_prefix}_trade"
        return_col = f"{view_prefix}_trade_return"
        reason_col = f"{view_prefix}_trade_reason"
        if trade_col in df.columns:
            # 入场标记
            l_entry_x, l_entry_y = [], []
            s_entry_x, s_entry_y = [], []
            # 离场标记
            l_exit_sl_x, l_exit_sl_y = [], []
            l_exit_tp_x, l_exit_tp_y = [], []
            s_exit_sl_x, s_exit_sl_y = [], []
            s_exit_tp_x, s_exit_tp_y = [], []
            # 盈亏标注
            annotations = []

            for i, trade_val in enumerate(df[trade_col]):
                if pd.isna(trade_val) or str(trade_val).strip() == "":
                    continue
                tv = str(trade_val)
                if tv.startswith("entry_"):
                    if "long" in tv:
                        l_entry_x.append(x_vals[i])
                        l_entry_y.append(float(long_pnl[i]) - PNL_BASELINE)
                    else:
                        s_entry_x.append(x_vals[i])
                        s_entry_y.append(float(short_pnl[i]) - PNL_BASELINE)
                elif tv.startswith("exit_"):
                    pnl_arr = long_pnl if "long" in tv else short_pnl
                    raw_reason = str(df[reason_col].iloc[i]) if reason_col in df.columns else ""
                    ret_pct = float(df[return_col].iloc[i]) if return_col in df.columns and not pd.isna(df[return_col].iloc[i]) else 0.0
                    is_sl = raw_reason == "stop_loss"
                    if "long" in tv:
                        if is_sl:
                            l_exit_sl_x.append(x_vals[i])
                            l_exit_sl_y.append(float(pnl_arr[i]) - PNL_BASELINE)
                        else:
                            l_exit_tp_x.append(x_vals[i])
                            l_exit_tp_y.append(float(pnl_arr[i]) - PNL_BASELINE)
                    else:
                        if is_sl:
                            s_exit_sl_x.append(x_vals[i])
                            s_exit_sl_y.append(float(pnl_arr[i]) - PNL_BASELINE)
                        else:
                            s_exit_tp_x.append(x_vals[i])
                            s_exit_tp_y.append(float(pnl_arr[i]) - PNL_BASELINE)
                    # 盈亏% 标注
                    label_color = colors["exit_sl"] if is_sl else colors["exit_tp"]
                    arrow = "↑" if "long" in tv else "↓"
                    annotations.append(dict(
                        x=x_vals[i], y=float(pnl_arr[i]) - PNL_BASELINE,
                        text=f"{arrow}{ret_pct:+.1f}%", showarrow=False,
                        font=dict(size=8, color=label_color), yshift=12,
                    ))

            def _add_markers(xs, ys, sym, clr):
                if xs:
                    fig.add_trace(
                        go.Scattergl(x=xs, y=ys, mode="markers",
                                     marker=dict(color=clr, symbol=sym, size=8),
                                     showlegend=False),
                        row=1, col=1,
                    )

            _add_markers(l_entry_x, l_entry_y, "triangle-up", colors["pnl_long"])
            _add_markers(s_entry_x, s_entry_y, "triangle-down", colors["pnl_short"])
            _add_markers(l_exit_sl_x, l_exit_sl_y, "x", colors["exit_sl"])
            _add_markers(s_exit_sl_x, s_exit_sl_y, "x", colors["exit_sl"])
            _add_markers(l_exit_tp_x, l_exit_tp_y, "circle", colors["exit_tp"])
            _add_markers(s_exit_tp_x, s_exit_tp_y, "circle", colors["exit_tp"])

            for ann in annotations:
                fig.add_annotation(ann, row=1, col=1)

    # 回撤
    fig.add_trace(
        go.Scattergl(**make_drawdown_trace(x_vals, combined)),
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

    for i, v in enumerate(views):
        long_col = f"{v}_pnl_long"
        short_col = f"{v}_pnl_short"
        combined = compute_combined_pnl(
            df[long_col].values.astype(float),
            df[short_col].values.astype(float),
        )
        cum = combined - PNL_BASELINE
        fig.add_trace(go.Scattergl(
            y=cum,
            mode="lines",
            name=v,
            line=dict(color=view_color(i), width=2),
        ))

    fig.add_hline(y=0, line_dash="dash", line_color=COLORS["zero_line"])

    fig.update_layout(
        template="plotly_dark",
        height=350,
        margin=dict(l=10, r=10, t=40, b=10),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )

    st.plotly_chart(fig, use_container_width=True, config={"modeBarButtonsToAdd": ["downloadImage"]})


def _render_trade_table(df: pd.DataFrame, view_prefix: str) -> None:
    """渲染交易明细表（分页 + 搜索/排序）。"""
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

    # ── 搜索 ──
    search_term = st.text_input("搜索交易类型", key=f"trade_search_{view_prefix}",
                                placeholder="输入关键词筛选交易类型...", label_visibility="collapsed")
    if search_term:
        search_mask = trades_df["交易类型"].astype(str).str.contains(search_term, case=False, na=False)
        if "离场原因" in trades_df.columns:
            search_mask |= trades_df["离场原因"].astype(str).str.contains(search_term, case=False, na=False)
        trades_df = trades_df[search_mask]

    total_rows = len(trades_df)
    if total_rows == 0:
        st.caption("无匹配的交易记录")
        return

    # ── 排序 ──
    sort_options = _build_sort_options(trades_df)
    if sort_options:
        sort_by = st.selectbox("排序", list(sort_options.keys()), key=f"trade_sort_{view_prefix}")
        sort_col_name = sort_options[sort_by]
        if sort_col_name in trades_df.columns:
            trades_df = trades_df.sort_values(sort_col_name, ascending=False)

    # ── 分页 ──
    page_size = 50
    total_pages = max(1, (total_rows + page_size - 1) // page_size)
    page_options = [f"第 {p} 页 (共 {total_rows} 条)" for p in range(1, total_pages + 1)]
    selected_page_label = st.selectbox(
        "选择页码",
        page_options,
        key=f"trade_page_{view_prefix}",
        label_visibility="collapsed",
    )
    page_idx = page_options.index(selected_page_label)
    start = page_idx * page_size
    end = min(start + page_size, total_rows)

    st.caption(f"显示第 {start + 1}–{end} 条，共 {total_rows} 条交易")
    st.dataframe(trades_df.iloc[start:end], use_container_width=True, height=400)

    # CSV 导出（导出全部交易，而非仅当前页）
    csv = trades_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label=f"导出全部交易明细 CSV ({total_rows} 条)",
        data=csv,
        file_name=f"{view_prefix}_trades.csv",
        mime="text/csv",
    )


def _build_sort_options(trades_df: pd.DataFrame) -> dict:
    """根据交易表列名构建排序选项。

    返回 label -> 实际列名 的映射。"""
    options = {}
    if "收益%" in trades_df.columns:
        options["按收益%降序"] = "收益%"
    if "交易类型" in trades_df.columns:
        options["按交易类型"] = "交易类型"
    if "离场原因" in trades_df.columns:
        options["按离场原因"] = "离场原因"
    return options


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
