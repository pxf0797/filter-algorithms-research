# 多周期滤波策略 — 文档索引

## 📖 核心报告

| 文档 | 说明 |
|------|------|
| [**research_report.md**](research_report.md) | **主报告**：多周期滤波策略实盘交易应用研究报告 —— 以同向判断为核心（12章 + 2附录，~70KB） |

## 📐 策略设计

| 文档 | 说明 |
|------|------|
| [strategy/strategy_documentation.md](strategy/strategy_documentation.md) | 策略体系总览：滤波算法、施密特触发器、预测曲线、交叉P&L分析 |
| [strategy/expand_collapse_parameter_persistence.md](strategy/expand_collapse_parameter_persistence.md) | 展开/折叠状态的参数持久化方案 |

## 🔌 券商接口

| 文档 | 说明 |
|------|------|
| [broker/domestic_broker_api_report.md](broker/domestic_broker_api_report.md) | **国内券商+港股通接口综合报告**：A股量化接口全景、港股通量化方案、股股通调查、综合推荐 |
| [broker/futu_openapi_technical_guide.md](broker/futu_openapi_technical_guide.md) | 富途OpenAPI技术指南：OpenD网关、行情订阅、交易接口、Python集成模板、成本分析 |

## ⚙️ 实盘执行

| 文档 | 说明 |
|------|------|
| [execution/实盘交易执行方案_技术挑战与风险控制.md](execution/实盘交易执行方案_技术挑战与风险控制.md) | 回测→实盘差距分析、信号重绘修复、四层级风控、监控体系、港股特殊考虑 |
| [execution/实盘执行方案_参数优化与风险控制_CH7-11.md](execution/实盘执行方案_参数优化与风险控制_CH7-11.md) | 参数优化体系 + 实盘执行架构 + 分阶段上线路径（第7-11章初稿） |

## 🧪 测试

| 文档 | 说明 |
|------|------|
| [tests/test_cases.md](tests/test_cases.md) | 综合测试用例 |
| [tests/data_computation_test_cases.md](tests/data_computation_test_cases.md) | 数据计算测试用例 |
| [tests/ui_test_cases.md](tests/ui_test_cases.md) | UI 测试用例 |

### 测试文件索引

| 文件 | 说明 |
|------|------|
| `test_alignment.py` | 对齐模块单元测试 |
| `test_alignment_subplot.py` | 子图对齐测试 |
| `test_app_smoke.py` | AppTest 冒烟测试 (6 tests) |
| `test_app_ui.py` | Streamlit UI 交互测试 |
| `test_boundary.py` | 边界条件测试 |
| `test_charts.py` | 图表模块测试 |
| `test_config_db.py` | 配置数据库测试 |
| `test_data_loader.py` | 数据加载器测试 |
| `test_db.py` | 数据库基础操作测试 |
| `test_filters.py` | 滤波算法测试 |
| `test_integration.py` | 集成测试 |
| `test_integration_flows.py` | 集成流程测试 |
| `test_param_export_import.py` | 参数导出导入测试 |
| `test_preset_ui.py` | 预设 UI 单元测试 |
| `test_preset_ui_actions.py` | 预设 UI 操作测试 |
| `test_sidebar.py` | 侧边栏模块测试 (19 tests) |
| `test_signals.py` | 信号生成测试 |
| `test_state.py` | 状态管理测试 |
| `test_strategy.py` | 策略逻辑测试 |
| `test_streamlit_app.py` | streamlit_app 函数测试 |

## 🗄️ 数据处理

| 文档 | 说明 |
|------|------|
| [**data_processing.md**](data_processing.md) | **数据处理说明**：数据架构、拉取策略、写入机制、健康检查、数据校验、备份恢复、导入导出 |

## 🗄️ 归档

| 文档 | 说明 |
|------|------|
| [archive/filter_algorithms_research.md](archive/filter_algorithms_research.md) | 滤波算法深度研究 |
| [archive/schmitt-prediction-curve.md](archive/schmitt-prediction-curve.md) | 施密特触发器与预测曲线设计 |
| [archive/STPP_策略文档.md](archive/STPP_策略文档.md) | STPP 策略文档 |
| [archive/database_design.md](archive/database_design.md) | 数据库设计 |
| [archive/database_guide.md](archive/database_guide.md) | 数据库使用指南 |

---

*最后更新：2026-06-28 | 测试数：623 | 覆盖率：50%*
