"""
回测核心指标计算（P0-6）

提供 Sharpe / Sortino / Calmar / Max Drawdown / 交易统计等核心指标，
输入 PnL 数组和交易记录，返回标准化 dict。
"""

import numpy as np
from itertools import groupby

try:
    from numba import njit
    HAS_NUMBA = True
except ImportError:
    def njit(*args, **kwargs):
        """Identity decorator: numba not installed, return function unchanged."""
        return lambda f: f
    HAS_NUMBA = False


# ═══════════════════════════════════════════════════════════════════════════
# Numba-accelerated core: max drawdown & underwater duration
# ═══════════════════════════════════════════════════════════════════════════

@njit(cache=True)
def _compute_drawdown_metrics(pnl: np.ndarray):
    """Compute max drawdown and max drawdown duration in a single O(n) pass.

    Replaces the three-pass approach (``np.maximum.accumulate`` +
    vectorised drawdown + ``itertools.groupby``) with one numba loop.

    Parameters
    ----------
    pnl : np.ndarray
        PnL curve values (e.g. combined long/short).

    Returns
    -------
    (max_dd, max_dd_dur) : (float, int)
        ``max_dd`` is the most negative drawdown ratio (e.g. -0.15 for 15%),
        ``max_dd_dur`` is the longest consecutive underwater bar count.
    """
    n = len(pnl)
    if n == 0:
        return 0.0, 0

    peak = pnl[0]
    max_dd = 0.0
    max_dd_dur = 0
    current_dur = 0

    for i in range(n):
        val = pnl[i]
        if val > peak:
            peak = val

        if peak != 0.0:
            dd = (val - peak) / peak
        else:
            dd = 0.0

        if dd < max_dd:
            max_dd = dd

        if dd < 0.0:
            current_dur += 1
            if current_dur > max_dd_dur:
                max_dd_dur = current_dur
        else:
            current_dur = 0

    return max_dd, max_dd_dur


def _compute_drawdown_metrics_py(pnl: np.ndarray):
    """Pure Python fallback for :func:`_compute_drawdown_metrics`.

    Uses ``np.maximum.accumulate`` + ``itertools.groupby``, matching the
    original implementation exactly.
    """
    if len(pnl) == 0:
        return 0.0, 0

    peak = np.maximum.accumulate(pnl)
    drawdown = np.where(peak != 0, (pnl - peak) / peak, 0.0)
    max_dd = float(np.min(drawdown))

    underwater = drawdown < 0
    max_dd_dur = max(
        (len(list(g)) for k, g in groupby(underwater) if k),
        default=0,
    )
    return max_dd, max_dd_dur


def compute_backtest_metrics(
    long_pnl: np.ndarray,
    short_pnl: np.ndarray,
    trade_records: list[dict],
    n_bars: int,
    risk_free_rate: float = 0.03,
) -> dict[str, float]:
    """计算核心回测指标。

    Parameters
    ----------
    long_pnl : np.ndarray
        做多 PnL 曲线。
    short_pnl : np.ndarray
        做空 PnL 曲线。
    trade_records : list[dict]
        交易记录列表，每条至少包含 ``"return_pct"``。
    n_bars : int
        总 bar 数（用于年化，假设日线 252 交易日/年）。
    risk_free_rate : float
        无风险利率（默认 3%）。

    Returns
    -------
    dict[str, float]
        Keys:
            total_return_pct, win_rate_pct, profit_factor,
            sharpe_ratio, sortino_ratio, calmar_ratio,
            max_drawdown_pct, max_drawdown_duration,
            annualized_return_pct, annualized_volatility_pct,
            avg_trade_return_pct, avg_win_pct, avg_loss_pct,
            total_trades, winning_trades, losing_trades
    """
    combined_pnl = np.maximum(long_pnl, short_pnl)

    # ── Returns ──
    if len(combined_pnl) < 2:
        return _empty_metrics()

    returns = np.diff(combined_pnl) / np.where(combined_pnl[:-1] != 0, combined_pnl[:-1], 100.0)
    returns = returns[np.isfinite(returns)]

    # 年化收益率 & 波动率（假设日线 252 交易日）
    annual_return = np.mean(returns) * 252 if len(returns) > 0 else 0.0
    annual_vol = np.std(returns, ddof=1) * np.sqrt(252) if len(returns) > 1 else 0.0

    # ── Sharpe ──
    sharpe = ((annual_return - risk_free_rate) / annual_vol
              if annual_vol > 0 else 0.0)

    # ── Sortino ──
    downside = returns[returns < 0]
    downside_vol = (np.std(downside, ddof=1) * np.sqrt(252)
                    if len(downside) > 1 else 0.0)
    sortino = ((annual_return - risk_free_rate) / downside_vol
               if downside_vol > 0 else 0.0)

    # ── Max Drawdown & Duration ──
    if HAS_NUMBA:
        max_dd, max_dd_dur = _compute_drawdown_metrics(combined_pnl)
    else:
        max_dd, max_dd_dur = _compute_drawdown_metrics_py(combined_pnl)

    # ── Calmar ──
    calmar = annual_return / abs(max_dd) if max_dd != 0 else 0.0

    # ── 总收益率 & 年化 ──
    total_return = (combined_pnl[-1] / combined_pnl[0]) - 1.0
    years = n_bars / 252.0
    annualized_return = ((1 + total_return) ** (1 / years) - 1
                         if years > 0 and total_return > -1 else 0.0)

    # ── Trade stats ──
    returns_pct = [t["return_pct"] for t in trade_records] if trade_records else []
    wins = [r for r in returns_pct if r > 0]
    losses = [r for r in returns_pct if r <= 0]
    profit_factor = (sum(wins) / abs(sum(losses))
                     if losses and sum(losses) != 0 else float("inf"))

    return {
        "total_return_pct": round(total_return * 100, 2),
        "win_rate_pct": (round(len(wins) / len(returns_pct) * 100, 2)
                         if returns_pct else 0.0),
        "profit_factor": round(profit_factor, 2),
        "sharpe_ratio": round(sharpe, 3),
        "sortino_ratio": round(sortino, 3),
        "calmar_ratio": round(calmar, 3),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "max_drawdown_duration": int(max_dd_dur),
        "annualized_return_pct": round(annualized_return * 100, 2),
        "annualized_volatility_pct": round(annual_vol * 100, 2),
        "avg_trade_return_pct": (round(float(np.mean(returns_pct)), 2)
                                 if returns_pct else 0.0),
        "avg_win_pct": round(float(np.mean(wins)), 2) if wins else 0.0,
        "avg_loss_pct": round(float(np.mean(losses)), 2) if losses else 0.0,
        "total_trades": len(trade_records),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
    }


def _empty_metrics() -> dict[str, float]:
    """返回空指标的默认值。"""
    return {
        "total_return_pct": 0.0,
        "win_rate_pct": 0.0,
        "profit_factor": 0.0,
        "sharpe_ratio": 0.0,
        "sortino_ratio": 0.0,
        "calmar_ratio": 0.0,
        "max_drawdown_pct": 0.0,
        "max_drawdown_duration": 0,
        "annualized_return_pct": 0.0,
        "annualized_volatility_pct": 0.0,
        "avg_trade_return_pct": 0.0,
        "avg_win_pct": 0.0,
        "avg_loss_pct": 0.0,
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
    }
