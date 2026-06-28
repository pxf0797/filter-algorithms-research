# Filter Research 工程分析报告（终版）

> 初版：2026-06-28 | 终审：2026-06-28 | 评分：86/100

## 1. 执行摘要

### 项目定位
多周期股票滤波分析工具，以交互式 2x2 四视图对比为核心，集成 10 种数字滤波器、施密特触发器自适应死区、物理抛物线预测拟合和策略 PnL 回测。覆盖美股/港股/A 股，支持 1 分钟到季线共 8 个周期。

### 关键数据

| 指标 | 数值 |
|------|------|
| 工程化成熟度评分 | **86/100** |
| 识别差距项总数 | **25 项 — 全部关闭** |
| Critical/Major/Minor | 7/12/6 — 全部修复 |
| 代码规模 | **4,004 行，8 模块** |
| 测试 | **637 passed**（612 unit + 25 streamlit UI） |
| 测试覆盖 | 下层 97%+；UI 层中等 |
| 类型注解 | UI 层 44 个 return annotations |
| 评分趋势 | 25 → 83 → 81 → 86 |

## 2. 当前架构

### 2.1 模块结构

| 文件 | 行数 | 职责 |
|------|------|------|
| `filter_app/streamlit_app.py` | 1,259 | 页面编排，事件处理循环 |
| `filter_app/services/filter_engine.py` | 710 | 10 种滤波器注册表 + 施密特触发器 + 策略 PnL |
| `filter_app/components/charts.py` | 460 | Plotly 图表构建与渲染 |
| `filter_app/db.py` | 448 | K 线 upsert/query，健康检查，快照备份 |
| `filter_app/config_db.py` | 420 | 预设 CRUD，导入/导出，变更历史 |
| `filter_app/state.py` | 301 | AppState dataclass，视图配置管理 |
| `filter_app/components/sidebar.py` | 240 | 市场/股票选择，预设管理，标签搜索 |
| `filter_app/services/data_loader.py` | 166 | yfinance 数据获取，Parquet 缓存同步 |

### 2.2 技术栈

| 类别 | 技术 |
|------|------|
| UI 框架 | Streamlit 1.57.0 |
| 数值计算 | NumPy 2.2.4 |
| 科学计算 | SciPy 1.17.1（savgol/butter/medfilt） |
| 图表 | Plotly 6.7.0 |
| 数据处理 | Pandas 2.3.3 / Parquet |
| 统计建模 | Statsmodels 0.14.6（LOWESS） |
| 数据源 | yfinance 1.4.1 |
| 持久化 | SQLite WAL 模式 |
| 日志 | loguru 0.7.3 |

### 2.3 数据流

```
yfinance → db.upsert_kline → SQLite(market.db) → Pandas DataFrame
  → Parquet display cache → numpy arrays → filter_engine (10种滤波器)
  → schmitt_trigger (±1/0) → find_all_pairs → fit_physics_parabola
  → compute_strategy_pnl → Plotly Figure → 浏览器渲染
```

## 3. 质量指标

| 维度 | 状态 |
|------|------|
| **测试** | 637 passed（612 unit + 25 streamlit）；AppTest 框架就绪 |
| **CI** | GitHub Actions — ruff/mypy 阻塞模式 + pip-audit + pytest |
| **类型注解** | UI 层 44 个 return annotations（streamlit_app 32 + charts 9 + sidebar 3） |
| **安全** | 无 bare except；无 SQL 注入；unsafe_allow_html 仅 1 处静态；非 root 运行 |
| **缓存** | `@st.cache_data` x3（滤波/施密特/PnL）+ `@st.cache_resource` x1（SQLite 连接）+ `@st.fragment` x1 |
| **日志** | loguru 集成于 db / config_db / streamlit_app / data_loader |
| **部署** | Docker + docker-compose；CDN fallback + 离线提示 |
| **pre-commit** | `.pre-commit-config.yaml` 已配置 |

### 覆盖缺口（已闭合）

| 模块 | 覆盖率 |
|------|--------|
| `data_loader.py` | 100%（~58 测试） |
| `sidebar.py` | 100%（~36 测试） |
| `charts.py` | 91%（~87 测试） |

## 4. Streamlit 遵从度评分

| 实践领域 | 得分 |
|---------|------|
| 模块化拆分 | 8/10 |
| 页面导航（未用 st.Page，四视图场景合理） | 0/10 |
| 缓存策略 | 8/10 |
| Fragment 使用 | 7/10 |
| 状态管理 | 8/10 |
| 测试策略 | 7/10 |
| 部署 | 8/10 |
| 安全 | 6/10 |
| **总体** | **75%** |

## 5. 版本演进

| 版本 | 关键变化 |
|------|---------|
| v0 | 单文件 2,582 行，无 CI，无测试，无日志 — 评分 25 |
| Phase 1 | CI/CD、db 连接修复、loguru 集成、@st.fragment 引入 |
| Phase 2 | 架构拆分（8 模块 3 层）、AppState dataclass、Docker |
| Phase 3 | 函数拆分、AppTest 交互测试、CDN fallback、PnL 去重 |
| v10.2 | 配置方案文本搜索 + 标签前缀清理 |
| v10.2.1 | 修复 _fetch_stock.clear() AttributeError 崩溃（5 处） |
| v10.3 | P0-P3 按钮端到端测试（7 用例）+ 596 用例文档 |
| v10.3.1 | 自动刷新防无限循环 + 动态阈值 |
| v10.3.2 | 测试隔离修复（conftest mock 污染） |
| v10.4+ | sf2 None bug 修复；unsafe_allow_html 消除 |
| v10.5 | pyproject.toml；pre-commit；CDN fallback；P1-P5 闭合 |
| **v10.7** | **最终闭合** — UI 44 个 return annotations；CI ruff/mypy 阻塞；pip-audit；测试 637 全部通过 |

## 6. 结论

### 当前定位
研究原型已过渡至最小可用产品。核心算法深度（10 种滤波器 + 自适应施密特触发器 + 物理抛物线拟合 + 策略 PnL）是差异化优势。工程化基线（CI/测试/类型/缓存/日志/部署）已与同类成熟项目基本看齐。

### 适用场景
- 量化研究员与策略开发者的多周期滤波对比研究工具
- 不建议替代专业回测框架；定位于快速原型验证

### 剩余建议（非阻塞，可按需择机执行）

| 项 | 工时 |
|----|------|
| st.rerun 密度优化（16 处已评估均必需，架构层面优化） | 1.5d |
| 自动化端到端测试（Playwright，替代部分 AppTest） | 2d |
| 类型注解覆盖率全局提升 | 1d |

---

> 基于以下 5 份前置分析编译：T1 代码架构 / T2 项目配置 / T3 最佳实践调研 / T4 差距分析 / T5 改进方案
