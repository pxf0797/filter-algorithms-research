"""
集中颜色管理 — 图表、K线、PnL 等所有硬编码颜色的单一真源。

用法:
    from filter.constants.colors import COLORS
    line_color = COLORS["pnl_long"]
"""

COLORS: dict[str, str] = {
    # ── PnL 曲线 ──
    "pnl_long":               "#3fb950",   # 做多 PnL（绿）
    "pnl_short":              "#f85149",   # 做空 PnL（红）
    "pnl_combined":           "#58a6ff",   # max(做多,做空) 组合 PnL（蓝）
    "pnl_combined_fill":      "rgba(88,166,255,0.08)",
    "drawdown_fill":          "rgba(248,81,73,0.15)",
    "pnl_long_bg":            "rgba(63,185,80,0.04)",
    "pnl_short_bg":           "rgba(248,81,73,0.04)",

    # ── 信号 / 滤波 ──
    "signal":                 "#58a6ff",   # Sig 信号线（蓝）
    "epsilon":                "#f85149",   # ±ε 边界（红）
    "sigma_v":                "#a371f7",   # σ(v) 波动率（紫）
    "acceleration":           "#d2991d",   # 加速度 a（黄）
    "residual":               "#5f6c80",   # 残差（灰）
    "close_line":             "#5f6c80",   # 收盘价线（灰）
    "fit_curve":              "#f0a040",   # 拟合曲线（橙）
    "prediction":             "#a371f7",   # 预测曲线（紫）

    # ── K 线 ──
    "kline_increasing":       "#26a69a",   # 阳线（青绿）
    "kline_decreasing":       "#ef5350",   # 阴线（红）

    # ── 信号标记 fill ──
    "sig_long_fill":          "rgba(63,185,80,0.06)",
    "sig_short_fill":         "rgba(248,81,73,0.06)",
    "sig_band_even":          "rgba(88,166,255,0.10)",
    "sig_band_odd":           "rgba(163,113,247,0.10)",

    # ── 离场标记 ──
    "exit_sl":                "#f85149",   # 止损（红 ×）
    "exit_tp":                "#3fb950",   # 止盈（绿 ○）

    # ── BS 仓位标记 ──
    "bs_buy_bg":              "#2ea043",   # B 标记背景（深绿）
    "bs_sell_bg":             "#f85149",   # S 标记背景（红）
    "bs_text":                "#ffffff",   # B/S 文字（白）

    # ── 通用 ──
    "baseline":               "gray",      # 基线零线
    "zero_line":              "rgba(255,255,255,0.3)",
    "epsilon_band_fill":      "rgba(128,128,128,0.06)",
    "marker_border":          "rgba(0,0,0,0.3)",

    # ── 视图对比调色板 ──
    "view_0":                 "#58a6ff",
    "view_1":                 "#3fb950",
    "view_2":                 "#f85149",
    "view_3":                 "#d2991d",

    # ── 多 Ticker 调色板 ──
    "ticker_0":               "#58a6ff",
    "ticker_1":               "#3fb950",
    "ticker_2":               "#f85149",
    "ticker_3":               "#d2991d",
    "ticker_4":               "#a371f7",
    "ticker_5":               "#39d2c0",
    "ticker_6":               "#f78166",
    "ticker_7":               "#db61a2",
    "ticker_8":               "#8b949e",
    "ticker_9":               "#79c0ff",
    "ticker_10":              "#56d364",
    "ticker_11":              "#e5534b",
}


def ticker_color(index: int) -> str:
    """返回多 ticker 对比图中第 index 个 ticker 的颜色。"""
    return COLORS.get(f"ticker_{index % 12}", COLORS["ticker_0"])


def view_color(index: int) -> str:
    """返回多视图对比图中第 index 个视图的颜色。"""
    return COLORS.get(f"view_{index % 4}", COLORS["view_0"])
