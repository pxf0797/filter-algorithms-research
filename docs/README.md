# 多周期滤波策略 -- 文档索引

## 核心参考

| 文档 | 说明 |
|------|------|
| [architecture/engineering-design-overview.md](architecture/engineering-design-overview.md) | **工程设计总览（据实）**：据当前代码实测撰写，标注函数名与文件行号 |
| [strategy/strategy_documentation.md](strategy/strategy_documentation.md) | **策略体系总览 v1.4**：滤波算法、施密特触发器、预测曲线、交叉PnL分析 |
| [test_cases.md](test_cases.md) | **测试用例参考**：按测试文件组织的用例索引（830+ 用例） |

---

## 架构 (architecture/)

系统设计、数据流、配置管理。

| 文档 | 说明 |
|------|------|
| [engineering-design-overview.md](architecture/engineering-design-overview.md) | 工程设计总览，标注函数名与文件行号 |
| [data-processing.md](architecture/data-processing.md) | 数据处理说明：拉取策略、写入机制、健康检查、校验、备份恢复 |
| [data-fetch-analysis.md](architecture/data-fetch-analysis.md) | 数据获取与入库链路分析（2026-07-05） |
| [data-structures-analysis.md](architecture/data-structures-analysis.md) | 数据结构与状态管理全景分析（2026-07-05） |
| [db-to-parquet-deep-dive.md](architecture/db-to-parquet-deep-dive.md) | `_sync_to_display()` DB到Parquet完整数据流深度分析 |
| [per-tf-data-write-analysis.md](architecture/per-tf-data-write-analysis.md) | 回测模式各周期数据写入变更前后详细对比 |
| [backtest-window-analysis.md](architecture/backtest-window-analysis.md) | 回测窗口数据加载链路分析（2026-07-05） |
| [data-freshness.md](architecture/data-freshness.md) | 数据新鲜度说明：日线最后一条bar NaN问题与修复 |
| [config-param-persistence.md](architecture/config-param-persistence.md) | 配置参数持久化：单一真源机制，解决参数漏保存 |
| [config-db-proposal.md](architecture/config-db-proposal.md) | 配置管理方案：DB + UI 预设选择器迁移设计 |

## 回测 (backtesting/)

回测模式设计与分析。

| 文档 | 说明 |
|------|------|
| [final-report.md](backtesting/final-report.md) | **回测最终状态报告**（2026-07-04） |
| [half-pair-trading-strategy-v4.md](backtesting/half-pair-trading-strategy-v4.md) | **半边多空对交易策略 v4（最新）**：断续多空对质量甄别 |
| [cascading-synthesis.md](backtesting/cascading-synthesis.md) | 回测数据级联合成方案 v2（合并版） |
| [comparison-report.md](backtesting/comparison-report.md) | 浏览模式 vs 回测模式完整差异报告 |
| [data-analysis.md](backtesting/data-analysis.md) | 回测数据加载流程完整分析（2026-07-05） |
| [logic-change-analysis.md](backtesting/logic-change-analysis.md) | 回测模式变更前后逐行逻辑对比 |
| [play-analysis.md](backtesting/play-analysis.md) | 前进下一个bar vs 播放逻辑差异分析 |
| [redesign-v2.md](backtesting/redesign-v2.md) | 回测框架重构方案 v2 |
| [before-after-comparison.md](backtesting/before-after-comparison.md) | 回测数据流变更前后完整对比 |
| [pair-based-strategy-plan.md](backtesting/pair-based-strategy-plan.md) | 基于多空对的B周期交易策略（完善版） |
| [b-cycle-gating-plan.md](backtesting/b-cycle-gating-plan.md) | B周期同向门控交易方案 |
| [change-point-trading-plan.md](backtesting/change-point-trading-plan.md) | 周期变化点监测与交易方案（五层体系+六种模式） |
| [strategy-improvement-plan.md](backtesting/strategy-improvement-plan.md) | 策略改进方案（2026-07-07） |
| [pnl-design-doc.md](backtesting/pnl-design-doc.md) | PnL计算模块设计文档（As-Built，commit 1a4ecd7） |
| [pnl-implementation-design-spec.md](backtesting/pnl-implementation-design-spec.md) | PnL实现设计说明书（基于1a4ecd7） |
| [cross-period-position-state-design.md](backtesting/cross-period-position-state-design.md) | 高周期持仓状态色块设计（替代高周期PnL参考） |
| [long-entry-drop-rootcause.md](backtesting/long-entry-drop-rootcause.md) | 根因分析：做多入场被静默丢弃（3690 60min复现） |

## 策略 (strategy/)

交易策略设计文档。

| 文档 | 说明 |
|------|------|
| [strategy_documentation.md](strategy/strategy_documentation.md) | **策略系统完整说明 v1.4**：滤波算法、施密特触发器、预测曲线、交叉PnL |
| [BS_MARKER_STRATEGY.md](strategy/BS_MARKER_STRATEGY.md) | **BS仓位操作标识设计方案 v5**：仓位状态定义与可视化 |
| [expand_collapse_parameter_persistence.md](strategy/expand_collapse_parameter_persistence.md) | 折叠/展开参数持久化问题说明 |

## 滤波研究 (filtering/)

| 文档 | 说明 |
|------|------|
| [main-research-report.md](filtering/main-research-report.md) | **主报告**：多周期滤波策略实盘交易应用研究报告（12章+2附录） |
| [sigma-sampling.md](filtering/sigma-sampling.md) | Sigma-Sampling可行性报告：施密特触发器+EWMA波动率 |

## 性能 (perf/)

性能分析与优化方案。

| 文档 | 说明 |
|------|------|
| [bottleneck-analysis.md](perf/bottleneck-analysis.md) | 性能瓶颈分析（据实定位）：Streamlit+Plotly+SQLite |
| [chart-build-refactoring-plan.md](perf/chart-build-refactoring-plan.md) | 构图时间缩减重构方案（供讨论，未实施） |
| [efficiency-optimization-plan.md](perf/efficiency-optimization-plan.md) | 效率优化方案（供核对，未实现） |
| [parquet-to-ram-feasibility.md](perf/parquet-to-ram-feasibility.md) | Parquet-to-RAM可行性分析：落盘→内存数据流 |
| [streamlit-plotly-best-practices.md](perf/streamlit-plotly-best-practices.md) | Streamlit+Plotly性能最佳实践对照（2026-07-11） |

## 测试 (testing/)

测试文档、审计与用例。

| 文档 | 说明 |
|------|------|
| [test-completeness-audit.md](testing/test-completeness-audit.md) | 测试与测试文档完整性审计（综合结论，2026-07-11） |
| [test-coverage-audit.md](testing/test-coverage-audit.md) | 测试完整性审计报告（2026-07-11） |
| [test-doc-completeness-audit.md](testing/test-doc-completeness-audit.md) | 测试文档完整性报告（2026-07-11） |
| [test-gap-audit.md](testing/test-gap-audit.md) | 重构测试覆盖缺口审计（2026-07-12） |
| [axis-diagnosis.md](testing/axis-diagnosis.md) | 价格子图定位bug诊断报告 |
| [data_computation_test_cases.md](testing/data_computation_test_cases.md) | 数据计算测试用例 |
| [ui_test_cases.md](testing/ui_test_cases.md) | UI交互测试用例 v1.1.0（6大交互域） |

## 券商接口 (broker/)

| 文档 | 说明 |
|------|------|
| [domestic_broker_api_report.md](broker/domestic_broker_api_report.md) | 国内券商量化交易接口与港股通接入方案综合报告 |
| [futu_openapi_technical_guide.md](broker/futu_openapi_technical_guide.md) | 富途OpenAPI完整接口方案 — 多周期策略系统集成技术文档 |

## 实盘执行 (execution/)

| 文档 | 说明 |
|------|------|
| [实盘交易执行方案_技术挑战与风险控制.md](execution/实盘交易执行方案_技术挑战与风险控制.md) | 回测→实盘差距分析、信号重绘修复、四层级风控、监控体系 |
| [实盘执行方案_参数优化与风险控制_CH7-11.md](execution/实盘执行方案_参数优化与风险控制_CH7-11.md) | 参数优化体系+实盘执行架构+分阶段上线路径 |

## 开发 (development/)

工程分析、已知问题与问题追踪。

| 文档 | 说明 |
|------|------|
| [KNOWN_ISSUES.md](development/KNOWN_ISSUES.md) | 已知问题列表 |
| [all-issues-tracker.md](development/all-issues-tracker.md) | 回测级联合成完整问题追踪清单（2026-07-05） |

## 归档 (archive/)

历史文档，不再维护但保留供参考。

| 文档 | 说明 |
|------|------|
| [filter_algorithms_research.md](archive/filter_algorithms_research.md) | 滤波算法深度研究报告 |
| [schmitt-prediction-curve.md](archive/schmitt-prediction-curve.md) | 施密特触发器与预测曲线设计 |
| [STPP_策略文档.md](archive/STPP_策略文档.md) | STPP策略文档（施密特触发器+预测曲线双确认） |
| [database_design.md](archive/database_design.md) | 数据库设计方案（SQLite统一数据管理） |
| [database_guide.md](archive/database_guide.md) | 数据库管理方案说明 |
| [chart-bug-analysis.md](archive/chart-bug-analysis.md) | Chart Bug定位分析报告 |
| [streamlit-engineering-analysis.md](archive/streamlit-engineering-analysis.md) | Filter Research工程分析报告（终版，86/100） |
| [phase3-design.md](archive/phase3-design.md) | Phase3详细设计方案（2026-07-04） |
| [half-pair-trading-strategy.md](archive/half-pair-trading-strategy.md) | 半边多空对交易策略 v1（已归档） |
| [half-pair-trading-strategy-v2.md](archive/half-pair-trading-strategy-v2.md) | 半边多空对交易策略 v2（已归档） |
| [half-pair-trading-strategy-v3.md](archive/half-pair-trading-strategy-v3.md) | 半边多空对交易策略 v3（已归档） |
| [pnl-feedback-position-process-v0.md](archive/pnl-feedback-position-process-v0.md) | PnL反馈驱动持仓 v0骨架（已废弃） |
| [pnl-feedback-position-process-v1.md](archive/pnl-feedback-position-process-v1.md) | PnL反馈驱动持仓 v1（已废弃，SUPERSEDED） |
| [research-20260707/](archive/research-20260707/) | 2026-07-07策略研究系列（17篇，见子目录） |
| [superpowers/](archive/superpowers/) | Superpowers计划与规格（与项目代码无关） |

---

*最后更新：2026-07-12*
