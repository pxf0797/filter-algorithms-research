"""
回测引擎模块 — 对 Schmitt 信号执行量化回测

主入口: run_backtest(close, sig, dates)
优先使用 vectorbt，失败时自动降级到纯 numpy/pandas 简化引擎。

Phase 1: 纯信号验证，无手续费，初始资金 100,000。
"""

import numpy as np
import pandas as pd


def run_backtest(close: np.ndarray, sig: np.ndarray, dates=None) -> dict:
    """
    对 Schmitt 信号数组执行回测。

    Parameters
    ----------
    close : np.ndarray, shape (N,)
        收盘价序列。
    sig : np.ndarray, shape (N,)
        Schmitt 信号，取值 {+1: 做多, 0: 观望, -1: 做空}。
    dates : pd.DatetimeIndex, optional
        时间索引，传入后用作 price Series 的 index。

    Returns
    -------
    dict
        "equity"    : np.ndarray — 权益曲线
        "metrics"   : dict      — 绩效指标
        "trades"    : pd.DataFrame or None — 交易记录 (仅 vectorbt 路径)
        "portfolio" : object or None       — vectorbt Portfolio (仅 vectorbt 路径)
    """
    price = pd.Series(close, index=dates if dates is not None else range(len(close)))

    # --- 信号拆分 ---
    entries = (sig == 1) & (np.roll(sig, 1) != 1)
    entries[0] = sig[0] == 1
    exits = (sig != 1) & (np.roll(sig, 1) == 1)
    exits[0] = False

    shorts = (sig == -1) & (np.roll(sig, 1) != -1)
    shorts[0] = sig[0] == -1
    short_exits = (sig != -1) & (np.roll(sig, 1) == -1)
    short_exits[0] = False

    # --- vectorbt 路径 ---
    try:
        import vectorbt as vbt

        pf = vbt.Portfolio.from_signals(
            price,
            entries=entries,
            exits=exits,
            short_entries=shorts,
            short_exits=short_exits,
            freq="1d",
            init_cash=100_000.0,
        )
        equity = pf.value().values
        metrics = _extract_metrics(pf)
        return {
            "equity": equity,
            "metrics": metrics,
            "trades": pf.trades.records_readable,
            "portfolio": pf,
        }
    except Exception:
        return _simple_backtest(close, sig)


def _extract_metrics(pf) -> dict:
    """
    从 vectorbt Portfolio 提取绩效指标。

    优先从 pf.stats() 读取已知存在的字段，
    年化收益 / 年化波动率 / 平均持仓 由权益曲线和交易记录手动计算。

    Parameters
    ----------
    pf : vbt.Portfolio

    Returns
    -------
    dict
        total_return, annual_return, sharpe_ratio, max_drawdown,
        win_rate, profit_factor, annual_volatility, calmar_ratio,
        total_trades, avg_hold_days.
    """
    stats = pf.stats()

    # vectorbt 1.0.0 stats 是 pd.Series，用 loc 访问已知键
    def _s(key, default=0.0):
        try:
            return stats.loc[key]
        except (KeyError, TypeError):
            return default

    # 年化收益 & 年化波动率 — stats 中未必存在，手动计算
    equity = pf.value().values
    N = len(equity)
    init_cash = 100_000.0
    total_return = (equity[-1] - init_cash) / init_cash
    annual_return = (1 + total_return) ** (252 / N) - 1 if N > 0 else 0.0

    daily_ret = np.diff(equity) / equity[:-1] if N > 1 else np.array([0.0])
    annual_vol = float(daily_ret.std() * np.sqrt(252)) if len(daily_ret) > 0 else 0.0

    # 平均持仓天数 — 从 trades.records 计算
    trades = pf.trades.records
    n_trades = len(trades)
    if n_trades > 0:
        durations = trades["exit_idx"] - trades["entry_idx"]
        avg_hold = float(durations.mean())
    else:
        avg_hold = 0.0

    metrics = {
        "total_return": _s("Total Return [%]"),
        "annual_return": annual_return * 100,
        "sharpe_ratio": _s("Sharpe Ratio"),
        "max_drawdown": _s("Max Drawdown [%]"),
        "win_rate": _s("Win Rate [%]"),
        "profit_factor": _s("Profit Factor"),
        "annual_volatility": annual_vol * 100,
        "calmar_ratio": _s("Calmar Ratio"),
        "total_trades": int(_s("Total Trades", 0)),
        "avg_hold_days": avg_hold,
    }
    return metrics


def _simple_backtest(close: np.ndarray, sig: np.ndarray) -> dict:
    """
    纯 numpy/pandas 回测，无外部依赖。

    逐 bar 维护 position 状态，记录每笔交易，计算权益曲线与绩效指标。
    当 vectorbt 不可用或报错时自动降级。

    Parameters
    ----------
    close : np.ndarray, shape (N,)
    sig   : np.ndarray, shape (N,)

    Returns
    -------
    dict
        "equity"  : np.ndarray — 权益曲线
        "metrics" : dict       — 绩效指标
        "trades"  : None
        "portfolio" : None
    """
    n = len(close)
    position = np.zeros(n, dtype=int)  # 0=空仓, 1=多头, -1=空头
    daily_ret = np.zeros(n)

    for i in range(n):
        if sig[i] == 1:
            position[i] = 1
        elif sig[i] == -1:
            position[i] = -1
        else:
            position[i] = position[i - 1] if i > 0 else 0

        if i > 0 and position[i - 1] != 0:
            daily_ret[i] = position[i - 1] * (close[i] / close[i - 1] - 1)

    equity = 100_000 * np.cumprod(1 + daily_ret)

    # --- 绩效指标 (纯 numpy) ---
    metrics = _compute_metrics(equity, daily_ret)

    return {
        "equity": equity,
        "metrics": metrics,
        "trades": None,
        "portfolio": None,
    }


def _compute_metrics(equity: np.ndarray, daily_ret: np.ndarray) -> dict:
    """
    从权益曲线和日收益率计算绩效指标。

    Parameters
    ----------
    equity   : np.ndarray — 权益曲线
    daily_ret : np.ndarray — 日收益率

    Returns
    -------
    dict
    """
    init_cash = 100_000.0
    n = len(equity)

    total_return = (equity[-1] - init_cash) / init_cash
    annual_return = (1 + total_return) ** (252 / n) - 1 if n > 0 else 0.0

    risk_free = 0.02
    annual_vol = daily_ret.std() * np.sqrt(252) if n > 1 else 0.0
    sharpe = (annual_return - risk_free) / annual_vol if annual_vol > 0 else 0.0

    # 最大回撤
    peak = np.maximum.accumulate(equity)
    drawdown = (equity - peak) / peak
    max_dd = np.min(drawdown) * 100  # 转为百分比负值

    # 胜率 & 盈亏比 (通过日收益率符号近似)
    pos_mask = daily_ret > 0
    neg_mask = daily_ret < 0
    total_pos = pos_mask.sum() + neg_mask.sum()
    win_rate = pos_mask.sum() / total_pos * 100 if total_pos > 0 else 0.0
    gross_profit = daily_ret[pos_mask].sum()
    gross_loss = abs(daily_ret[neg_mask].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    calmar = annual_return / abs(max_dd / 100) if max_dd != 0 else 0.0

    return {
        "total_return": total_return * 100,
        "annual_return": annual_return * 100,
        "sharpe_ratio": sharpe,
        "max_drawdown": max_dd,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "annual_volatility": annual_vol * 100,
        "calmar_ratio": calmar,
        "total_trades": 0,
        "avg_hold_days": 0.0,
    }


# ===========================================================================
# 自检: python streamlit/backtest_engine.py
# ===========================================================================
if __name__ == "__main__":
    import time

    print("=== backtest_engine.py 自检 ===\n")

    # ---- 构造测试数据 ----
    np.random.seed(42)
    N = 252
    close = 100 * np.exp(np.random.randn(N).cumsum() * 0.01 + 0.0005)

    # 简单趋势信号: 上涨做多, 下跌做空
    ma5 = pd.Series(close).rolling(5).mean().values
    sig = np.where(close > ma5, 1, np.where(close < ma5 * 0.98, -1, 0))

    # ---- 回测 ----
    t0 = time.time()
    result = run_backtest(close, sig)
    elapsed = time.time() - t0

    print(f"回测耗时: {elapsed*1000:.1f} ms")
    print(f"数据长度: {N} bars")
    print()

    metrics = result["metrics"]
    print("--- 绩效指标 ---")
    for k, v in metrics.items():
        if isinstance(v, float):
            print(f"  {k:20s}: {v:>10.4f}")
        else:
            print(f"  {k:20s}: {v}")

    print(f"\n权益曲线长度: {len(result['equity'])}")
    print(f"权益曲线[-5:]: {result['equity'][-5:].round(2)}")
    print(f"有 trades 记录: {result['trades'] is not None}")
    print(f"有 portfolio 对象: {result['portfolio'] is not None}")

    print("\n=== 自检完成 ===")
