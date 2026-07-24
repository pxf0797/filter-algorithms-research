# 多周期滤波策略 -- 文档索引

## 核心参考

| 文档 | 说明 |
|------|------|
| [architecture/engineering-design-overview.md](architecture/engineering-design-overview.md) | **工程设计总览（据实）**：据当前代码实测撰写，标注函数名与文件行号 |
| [strategy/strategy_documentation.md](strategy/strategy_documentation.md) | **策略体系总览 v1.4**：滤波算法、施密特触发器、预测曲线、交叉PnL分析 |
| [references/test_cases.md](references/test_cases.md) | **测试用例参考**：按测试文件组织的用例索引（830+ 用例） |

---

## 架构 (architecture/)

系统设计、数据流、配置管理。

| 文档 | 说明 |
|------|------|
| [engineering-design-overview.md](architecture/engineering-design-overview.md) | 工程设计总览，标注函数名与文件行号 |
| [arch-analysis.md](architecture/arch-analysis.md) | **架构深度分析**：代码架构评估与问题诊断（2026-07-23） |
| [final-report.md](architecture/final-report.md) | **深度优化综合报告**：架构+性能+代码质量+配置部署四维度综合分析（2026-07-23） |
| [perf-analysis.md](architecture/perf-analysis.md) | **性能分析报告**：静态代码审查，瓶颈定位（2026-07-23） |
| [quality-analysis.md](architecture/quality-analysis.md) | **代码质量分析**：10,554行Python + 35,329行测试的全面审计（2026-07-23） |
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

回测数据存储与CLI工具。

| 文档 | 说明 |
|------|------|
| [回测数据CLI存储方案设计.md](backtesting/回测数据CLI存储方案设计.md) | 回测数据命令行存储方案设计 |
| [回测数据分析示例.md](backtesting/回测数据分析示例.md) | 回测数据分析使用示例 |

> **注意**：历史回测模式设计与分析文档已移至 [archive/](archive/)，包括回测框架重构方案、策略设计、PnL计算等。

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

工程分析、已知问题、实现规格与问题追踪。

| 文档 | 说明 |
|------|------|
| [KNOWN_ISSUES.md](development/KNOWN_ISSUES.md) | 已知问题列表 |
| [all-issues-tracker.md](development/all-issues-tracker.md) | 回测级联合成完整问题追踪清单（2026-07-05） |
| [backtest-capture-framework.md](development/backtest-capture-framework.md) | **回测数据采集系统 -- 宏观框架设计** v1.1（2026-07-13） |
| [backtest-capture-guide.md](development/backtest-capture-guide.md) | **回测管道数据捕获与分析 -- 使用指南**（2026-07-13） |
| [implementation-spec.md](development/implementation-spec.md) | **实现规格 -- 回测数据采集系统 MVP**（2026-07-13） |
| [gap-analysis.md](development/gap-analysis.md) | **数据记录缺口分析**：EventRecorder / PipelineCapture / BacktestRunner 数据覆盖完整性（2026-07-13） |

## 研究 (research/)

深度研究报告与根因分析。

| 文档 | 说明 |
|------|------|
| [回测信号稳定性-行业研究.md](research/回测信号稳定性-行业研究.md) | **回测信号稳定性 -- 量化行业解决方案深度研究**：学术文献+五大平台架构+七类稳定技术（2026-07-12） |
| [PnL与信号不一致根因分析.md](research/PnL与信号不一致根因分析.md) | **PnL 曲线与信号对比图表不一致 -- 根因分析**（2026-07-19） |

## 参考 (references/)

测试用例、版本对比、运行示例等参考资料。

| 文档 | 说明 |
|------|------|
| [test_cases.md](references/test_cases.md) | **测试用例文档**：830+用例，按测试文件组织（2026-07-11） |
| [版本对比信号差异分析.md](references/版本对比信号差异分析.md) | **版本对比 -- 信号识别差异分析**：76ee7f0 vs HEAD P0-P5 修复后（2026-07-19） |
| [example-run-results.md](references/example-run-results.md) | **回测数据采集系统 -- 示例运行结果**（2026-07-13） |

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

## 待审查归档 (_archive/)

以下文档已从根目录移入，建议审查后决定保留或删除。

| 文档 | 说明 | 建议 |
|------|------|------|
| [optimization-analysis-report.md](_archive/optimization-analysis-report.md) | 与 architecture/final-report.md 完全重复 | **建议删除**：内容与 final-report.md 完全相同 |
| [分支整理与优化路线图.md](_archive/分支整理与优化路线图.md) | 分支整理工作规划（2026-07-19） | **建议归档**：已完成的分支整理计划，无长期参考价值 |
| [当前分支变更参考.md](_archive/当前分支变更参考.md) | feat/backtest-data-storage 分支变更参考（2026-07-19） | **建议归档**：分支特异性参考，分支合并后可删除 |

---

*最后更新：2026-07-23*
