# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [v3.5.0] - 2026-07-24

### Architecture
- Domain-driven 5-package reorganization (browse/backtest/data/engine/shared)
- Web/CLI pipeline unification — 0%→100% code reuse
- data_loader split into fetcher/synth/loader
- God Module split: streamlit_app 1702→845 lines

### Performance
- searchsorted optimization (10-50x)
- numba JIT: Schmitt trigger + Kalman filter
- SQLite PRAGMA optimization, batch DELETE

### Features
- Optuna Bayesian parameter optimization
- Parquet time-based partitioning
- Incremental yfinance data fetching
- BacktestCatalog cross-session indexing
- Financial metrics: Sharpe/Sortino/Calmar/MaxDD

### Developer Experience
- Property-based testing (hypothesis)
- Snapshot testing (syrupy)
- Concurrency safety tests
- CI: matrix build, Docker verify, CHANGELOG automation
- LICENSE (MIT), Makefile, .python-version

## [v3.4.0]

## [v1.1-backtest-fix]

## [v1.0-bs-markers]

## [v10.9.0]

## [v10.8.0]

## [design-v4-complete]

## [v1.1-stable]

## [v1.1-cascading-synthesis]

## [v1.0-cascading-synthesis]

## [v1.2.0-backtest]

## [v1.1.0-backtest]

## [v1.0.1-backtest]

## [v1.0.0-backtest]

## [v10.7.1]

## [v10.7-complete]

## [v10.6-final]

## [v10.5-phase34]

## [v10.4-gap-closure]

## [v10.3.2-dynamic-threshold]
