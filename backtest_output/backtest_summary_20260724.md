# Batch Backtest Summary -- All Stocks

**Date:** 2026-07-24  
**Branch:** feat/domain-refactor  
**Bar Window:** 100 bars per stock (end_bar=150, start_bar=50 for preset stocks)

---

## Overview

| # | Ticker | Market | Preset | Bars | Return% | Sharpe | MaxDD% | Trades | WinRate% | Status |
|---|--------|--------|--------|------|---------|--------|--------|--------|----------|--------|
| 1 | 2382 | HK | 2382_HK | 100 | 25.36 | 5.37 | -0.24 | 6 | 100.0 | PASS |
| 2 | 3690 | HK | 3690_HK | 100 | 16.87 | 11.54 | -0.22 | 11 | 100.0 | PASS |
| 3 | 600115 | A-Shares | 600115_SS | 100 | 21.69 | 7.85 | -0.41 | 11 | 90.91 | PASS |
| 4 | AAPL | US | AAPL_US | 100 | 21.28 | 10.85 | -0.29 | 10 | 90.0 | PASS |
| 5 | MSFT | US | N/A (default) | 30 | 0.0 | 0.0 | 0.0 | 0 | 0.0 | **DEGRADED** |

---

## Detailed Analysis

### 1. 2382 (HK) -- 2382_HK Preset
- **Views:** 15分钟 / 60分钟 / 日线 / 周线
- **Total Return:** 25.36%
- **Sharpe Ratio:** 5.37
- **Max Drawdown:** -0.24%
- **Trades:** 6 (all winning)
- **Assessment:** Strong performance with zero losing trades. Low drawdown indicates tight risk control.

### 2. 3690 (HK) -- 3690_HK Preset
- **Views:** 15分钟 / 60分钟 / 日线 / 周线
- **Total Return:** 16.87%
- **Sharpe Ratio:** 11.54 (highest among all stocks)
- **Max Drawdown:** -0.22%
- **Trades:** 11 (all winning)
- **Assessment:** Highest risk-adjusted return (Sharpe 11.54). Very consistent strategy. 11 trades with 100% win rate in a short 100-bar window.

### 3. 600115 (A-Shares) -- 600115_SS Preset
- **Views:** 15分钟 / 60分钟 / 日线 / 周线
- **Total Return:** 21.69%
- **Sharpe Ratio:** 7.85
- **Max Drawdown:** -0.41%
- **Trades:** 11 (90.91% win rate, 1 losing trade)
- **Assessment:** Slightly higher drawdown than HK stocks but still excellent. One losing trade brought win rate below 100%.

### 4. AAPL (US) -- AAPL_US Preset
- **Views:** 15分钟 / 60分钟 / 日线 / 周线
- **Total Return:** 21.28%
- **Sharpe Ratio:** 10.85
- **Max Drawdown:** -0.29%
- **Trades:** 10 (90.0% win rate, 1 losing trade)
- **Assessment:** Solid performance for a US stock with the Savgol+EMA dual filter. Near-perfect win rate.

### 5. MSFT (US) -- No Preset (Fallback)
- **Views:** 日线 / 60分钟 / 15分钟 / 5分钟
- **Total Return:** 0.0%
- **Sharpe Ratio:** 0.0
- **Trades:** 0
- **Bar Range:** Only 30 bars processed (120-150)
- **Root Cause:** No MSFT-specific preset in config_db. The default fallback uses n_pts=120 per view, leaving only 30 tradeable bars in a 150-bar window. No trades were generated because the window was too small for the filter to produce signals.
- **Fix Required:** Create an `MSFT_US` preset in config_db mirroring the AAPL_US structure.

---

## Summary Statistics

| Metric | Value |
|--------|-------|
| Total Stocks Tested | 5 (out of 6 in DB, TEST_BATCH skipped) |
| Passed | 4 |
| Degraded | 1 (MSFT, no preset) |
| Average Return (passing) | 21.30% |
| Average Sharpe (passing) | 8.90 |
| Average Win Rate (passing) | 95.23% |
| Best Performer (Return) | 2382 (25.36%) |
| Best Performer (Sharpe) | 3690 (11.54) |
| Lowest Drawdown | 3690 (-0.22%) |

---

## Kline Data Inventory

| Ticker | Bars Available | In Backtest |
|--------|---------------|-------------|
| AAPL | 37,302 | Yes |
| MSFT | 27,539 | Yes |
| 2382 | 25,883 | Yes |
| 3690 | 23,328 | Yes |
| 600115 | 23,231 | Yes |
| TEST_BATCH | 6 | Skipped |

---

## Recommendations

1. **Create MSFT_US Preset:** Mirror the AAPL_US preset for MSFT to enable full backtesting.
2. **Full Bar Backtest:** Rerun all stocks with full bar ranges (not just 100 bars) for more statistically significant results.
3. **Cross-View Aggregation:** The current metrics only use the first view (v0). Aggregating across views could provide more robust performance metrics.
4. **Sortino/Calmar Ratios:** The engine computes these but doesn't log them. Add to the log output for better risk assessment.

---

## Output Files

| Session | Path |
|---------|------|
| 2382_20260724-231031-2382 | backtest_output/2382_20260724-231031-2382/ |
| 3690_20260724-231038-3690 | backtest_output/3690_20260724-231038-3690/ |
| 600115_20260724-231043-600115 | backtest_output/600115_20260724-231043-600115/ |
| AAPL_20260724-231045-AAPL | backtest_output/AAPL_20260724-231045-AAPL/ |
| MSFT_20260724-231048-MSFT | backtest_output/MSFT_20260724-231048-MSFT/ |
