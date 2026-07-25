# filter_research 深度优化分析报告

> **合成日期**: 2026-07-23 | **版本**: 10.5.0
> **数据来源**: 四个维度深度分析（架构 + 性能 + 代码质量 + 配置部署）
> **分析方法**: 静态代码审查 + pytest 运行结果 + Docker/CI 配置审查

---

## 执行摘要

filter_research 是一个多周期股票滤波分析工具，代码规模约 10,500 行 Python，623 个测试用例，整体代码质量在同类个人项目中处于中上水平。四个维度综合评估：**架构 5.0/10 + 性能中等(关键瓶颈为回测I/O) + 代码质量 B+(83/100) + 配置部署 5.8/10**。

**TOP 3 最关键的发现：**

1. **回测模式逐 bar 写 Parquet 文件是最大性能瓶颈**（预计 10x-50x 吞吐提升空间）。每次 bar 推进都完整序列化整个数据窗口到磁盘，产生大量冗余 I/O。
2. **`streamlit_app.py`（1702 行）是架构瓶颈**，集页面编排、图表构建、侧边栏逻辑于一体，维护成本和测试难度随功能增长非线性增加。
3. **CI 流水线红灯** — 7 个测试失败 + 4 个错误集中在 `test_param_export_import.py`，阻塞了持续集成的反馈价值。

**推荐优先处理方向：** 先止血（修测试 + 修 CI），再攻坚（回测 I/O 优化 + God Module 拆分），后提升（测试覆盖 + 工程化）。

---

## 交叉分析矩阵

以下标注跨维度相关的问题，合并重复发现后的汇总：

| # | 问题 | 维度来源 | 交叉影响 |
|---|------|---------|---------|
| 1 | `streamlit_app.py` God Module (1702行) | 🔧架构 P0 + 🧪质量 P2 | 架构--可测试性--性能（所有缓存装饰器集中于此，拆分后可独立优化） |
| 2 | 回测逐 bar Parquet I/O | ⚡性能 Critical + 🔧架构（数据层） | 性能--数据架构--可扩展性（新数据源接入也受此设计影响） |
| 3 | 回测零缓存/无增量计算 | ⚡性能 Critical | 性能--算法设计--内存使用（增量计算可同时降低 CPU 和 I/O） |
| 4 | cfg dict 无类型约束 (20+字段) | 🔧架构 P0 + 🧪质量（类型注解） | 架构--代码质量--可维护性（运行时拼写错误难以排查） |
| 5 | CI 测试失败 (7 FAIL + 4 ERROR) | 🧪质量 P0 + ⚙️配置（CI） | 质量--CI/CD--部署信心 |
| 6 | ALL_TFS 常量重复 4次 + 魔法字符串 | 🔧架构 P1 + 🧪质量（命名） | 架构--代码规范--重构风险 |
| 7 | iterrows() 反模式 + DataFrame 转换 | ⚡性能 P1 | 性能--代码规范 |
| 8 | 异常处理 broad except (20处) | 🧪质量 P1 | 质量--运维可观测性 |
| 9 | Docker 构建低效（无多阶段、COPY全量） | ⚙️配置 P0 | 部署--CI/CD--安全 |
| 10 | 覆盖率阈值 55% 偏低 | 🧪质量 P1 + ⚙️配置（CI） | 质量--CI 有效性 |
| 11 | 日志双轨制 (loguru + print + JSONL) | 🧪质量 P2 | 质量--运维可观测性--架构（backtest_logger 与 loguru 功能重复） |
| 12 | 回测三通道输出 (JSONL + Parquet + JSON) | 🔧架构 P1 + ⚡性能（JSONL flush） | 架构--性能（3套序列化）--数据一致性 |
| 13 | DB 连接逻辑重复 + 连接管理模式不统一 | 🧪质量 P1 + ⚙️配置（环境） | 代码质量--配置--性能 |
| 14 | 单线程回测（多视图可并行但未实现） | ⚡性能 P1 + 🔧架构（可扩展性） | 性能--架构 |
| 15 | 无 lock 文件 + 依赖版本无上界 | ⚙️配置 P1 | 部署--CI/CD--可复现性 |
| 16 | pyproject.toml 缺少 [build-system] | ⚙️配置 + 🔧架构（包结构） | 配置--工程化 |
| 17 | config/ 目录不存在但 docker-compose 挂载 | ⚙️配置 | 部署--环境一致性 |
| 18 | pre-commit 缺少安全 hooks | ⚙️配置 + 🔧架构（安全） | 配置--质量--安全 |

**去重统计：** 四个维度原始发现问题约 50+ 条，交叉去重后汇总为 **18 个独立问题**。

---

## 发现汇总（按优先级排序）

### P0 -- 立即修复（阻塞性问题）

| # | 维度 | 问题 | 影响 | 修复成本 | 预估收益 |
|---|------|------|------|---------|---------|
| P0-1 | ⚡性能 | **回测逐 bar 重复 Parquet I/O** — `backtest_core.py:431-455` 每次 bar 推进都完整写/读所有 TF 的 parquet 文件 | 500 bar 回测产生 ~2000 次文件 I/O，约 20-100 秒纯 I/O | 2-4h | **5x-10x** 回测吞吐提升 |
| P0-2 | ⚡性能 | **回测零缓存/无增量计算** — `backtest_core.py:513-599` 相邻 bar 窗口 99% 数据重叠但全部重算 | 每次 step 的滤波/触发器/PnL 全部从头计算 | 4-8h | **3x-5x** 管道吞吐提升 |
| P0-3 | 🔧架构 | **`streamlit_app.py` God Module (1702行)** — 集页面编排、图表构建、侧边栏逻辑、回测协调于一体 | 新增功能需理解 1702 行上下文，测试隔离困难 | 8-16h | 可维护性翻倍，新功能开发提速 50% |
| P0-4 | 🧪质量 | **CI 测试失败 (7 FAIL + 4 ERROR)** — 全部集中在 `test_param_export_import.py`，含硬编码绝对路径 `/Users/xfpan/claude/filter_research/config/3690_HK_DP.json` | CI 长期红灯，PR 时无法判断是否引入新问题 | 2-3h | CI 回归检测能力恢复 |
| P0-5 | ⚙️配置 | **Docker 镜像构建低效** — 无多阶段构建、`COPY . .` 复制 tests/docs/tools 到生产镜像 | 镜像体积膨胀、构建缓存失效、安全攻击面增大 | 2-3h | 镜像体积缩小 60%+，构建加速 50% |
| P0-6 | ⚙️配置 | **无 lock 文件** — `requirements.txt` 无版本锁定，传递依赖不受控 | 新环境 `pip install` 可能拉到不兼容版本，生产事故风险 | 1h | 环境可复现性 100% |

### P1 -- 短期优化（1-2 周）

| # | 维度 | 问题 | 影响 | 修复成本 | 预估收益 |
|---|------|------|------|---------|---------|
| P1-1 | ⚡性能 | **单线程回测（多视图可并行）** — `backtest_core.py:174-235` 4 个视图顺序计算 | 白白浪费 2-3x 加速机会（GIL 在 numpy 计算时释放） | 3-4h | **2x-3x** 管道吞吐 |
| P1-2 | ⚡性能 | **JSONL 逐事件 flush()** — `event_recorder.py:712` 每次写入后立即 fsync | 1000 步回测产生 8000-20000 次 fsync 调用 | 1h | **10x-50x** 录制吞吐 |
| P1-3 | 🔧架构 | **cfg dict 无类型 → ViewConfig dataclass** — 20+ 字段扁平 dict，字段约束分散在 VIEW_PARAM_SPECS/VIEW_DEFAULTS | 重构时拼写错误到运行时才发现，IDE 无智能提示 | 4-6h | 类型安全 + IDE 支持 |
| P1-4 | 🔧架构 | **ALL_TFS 常量重复 4 次 + 魔法字符串泛滥** — `"日线"`, `"v0_ke"` 等字符串硬编码在多个文件 | 修改周期名需同步 4+ 文件，重构风险高 | 2-3h | 零运行时影响，大幅降低重构风险 |
| P1-5 | 🧪质量 | **异常处理 broad except (20处)** — `streamlit_app.py` 15处 + `backtest_cli.py` 裸异常吞没 | 线上问题排查缺乏上下文，错误被静默丢弃 | 2-3h | 问题定位速度提升 3x+ |
| P1-6 | 🧪质量 | **集成测试仅 6 个用例** — 未覆盖多周期级联、预设导入导出、回测管线等关键路径 | 管线回归风险高，重构信心不足 | 4-6h | 重构安全感大幅提升 |
| P1-7 | 🧪质量 | **DB 连接逻辑重复** — `db.py` 和 `config_db.py` 的 WAL/synchronous/busy_timeout 配置几乎相同但独立实现 | 连接参数漂移风险 | 2h | 维护成本降低 |
| P1-8 | ⚙️配置 | **CI/CD 8 项改进** — pip-audit 重复安装、mypy 过于宽松、ruff 忽略 F841、测试分两次运行、无 Docker 构建验证、仅 2 个 Python 版本 | CI 反馈循环质量低 | 3-4h | CI 有效性和速度提升 |
| P1-9 | ⚙️配置 | **pyproject.toml 缺少 [build-system] + pytest 配置在 pyproject.toml 和 pytest.ini 重复** | 工程化不完整 | 1h | 包管理标准化 |

### P2 -- 中期改进（1-2 月）

| # | 维度 | 问题 | 影响 | 修复成本 | 预估收益 |
|---|------|------|------|---------|---------|
| P2-1 | ⚡性能 | **iterrows() 反模式** — `db.py:86,559,626` 三处使用 `df.iterrows()` | `upsert_kline` 是热路径（启动时 8 次调用），性能损失 3-10x | 1h | **3x-10x** 行迭代速度 |
| P2-2 | ⚡性能 | **_align_pnl_to_current_tf O(n*m) 线性搜索** — `filter_engine.py:947` | 回测热路径中每次 step 调用 1-3 次 | 1h | **5x-10x** 对齐速度 |
| P2-3 | ⚡性能 | **apply_ema 不必要的 DataFrame 转换** — `filter_engine.py:66` | 热循环中每次 step 调用 4 次，累积开销 | 0.5h | **~2x** EMA 计算速度 |
| P2-4 | 🔧架构 | **Python/HTML 混合渲染 (200+ 行 JS)** — `charts.py` 中 JS 嵌套在 Python f-string | 无法 lint、无法单独测试、不易调试 | 3-4h | 可测试性 + 可维护性 |
| P2-5 | 🔧架构 | **回测三通道输出统一** — EventRecorder(JSONL) + ParquetStore(Parquet) + PipelineCapture(JSON) 三套并行写入 | 数据重复、维护 3 套序列化逻辑 | 4-6h | 维护成本降低 60% |
| P2-6 | 🧪质量 | **日志双轨制** — CLI 全部使用 `print()`、services 层使用 `loguru`、backtest_logger 手动写 JSONL | 无法统一日志级别控制、生产运维困难 | 3-4h | 运维可观测性大幅提升 |
| P2-7 | 🧪质量 | **components 层类型注解缺失** — charts.py(8%)、backtest_panel.py(27%)、bs_marker.py(0%)、sidebar.py(0%) | IDE 智能提示受限 | 3h | 开发体验提升 |
| P2-8 | ⚙️配置 | **pre-commit 缺少安全 hooks** — check-added-large-files、detect-private-key、check-merge-conflict 等 | 安全风险 | 0.5h | 安全防护 |
| P2-9 | ⚙️配置 | **部署缺少 HTTPS/认证/资源限制** — Docker Compose 无 Nginx 反代、无用户认证、无内存/CPU 限制 | 生产就绪度不足 | 4-6h | 部署安全性 |

### P3 -- 长期规划（季度）

| # | 维度 | 问题 | 影响 | 修复成本 | 预估收益 |
|---|------|------|------|---------|---------|
| P3-1 | 🔧架构 | **硬编码文件路径** — `DB_PATH`、`_CONFIG_DB_PATH`、`LOG_DIR` 硬编码相对路径 | 部署灵活性差 | 2h | 环境可配置性 |
| P3-2 | 🔧架构 | **双轨状态管理 (`AppState` + `st.session_state`)** — `_imp_` 备份机制增加调试复杂性 | 新人理解成本高 | 4-6h | 认知负担降低 |
| P3-3 | 🔧架构 | **缺少接口/抽象层** — 无 Protocol/ABC 定义，各层通过具体导入交互 | 可替换性受限 | 8-16h | 架构灵活性 |
| P3-4 | 🧪质量 | **缺少自定义异常类体系** | 错误分类困难，只能通过字符串匹配区分 | 2h | 错误处理精确度 |
| P3-5 | ⚙️配置 | **config/ 目录不存在但 docker-compose 挂载** — README 引用的 config/ 目录实际不存在 | 文档与实现不一致 | 1h | 文档准确性 |
| P3-6 | ⚙️配置 | **环境变量管理不完整** — `.env.example` 仅 2 个变量 | 新环境配置依赖代码阅读 | 1h | 上手体验 |

---

## 优化路线图

### Phase 1: 止血（Week 1）

目标：CI 变绿 + 消除已知错误 + 建立可复现环境

| # | 任务 | 文件/范围 | 预估耗时 | 验证方式 |
|---|------|---------|---------|---------|
| 1.1 | **修复 test_param_export_import.py 硬编码路径** | `tests/test_param_export_import.py:20` | 0.5h | pytest 绿 |
| 1.2 | **修复 7 个测试失败 + 4 个错误** | `tests/test_param_export_import.py`, `tests/` | 2h | `pytest tests/test_param_export_import.py -v` 全绿 |
| 1.3 | **生成 lock 文件** | 项目根目录 | 0.5h | `pip freeze > requirements.lock` |
| 1.4 | **修复 backtest_cli.py 裸异常吞没** | `filter/backtest_cli.py:338` | 0.5h | 手动触发无数据 ticker，确认 stderr 有日志 |
| 1.5 | **修复 config_db.py 裸 except** | `filter/config_db.py:41` | 0.5h | 代码审查 |

**Phase 1 产出**: CI 全绿，lock 文件就绪，关键错误处理修复。

---

### Phase 2: 重构（Week 2-4）

目标：消除性能瓶颈 + 拆分 God Module + 统一基础设置

| # | 任务 | 文件/范围 | 预估耗时 | 预期效果 |
|---|------|---------|---------|---------|
| 2.1 | **回测 I/O 优化：内存内数据窗口** | `services/backtest_core.py:431-455` | 4h | 5x-10x 回测吞吐 |
| 2.2 | **回测增量计算** | `services/backtest_core.py:513-599`, `services/filter_engine.py` | 6h | 3x-5x 管道吞吐 |
| 2.3 | **拆分 streamlit_app.py** → `chart_builder.py` + `sidebar_sections.py` | `filter/` | 12h | 可维护性翻倍 |
| 2.4 | **提取 constants.py + TimeFrame 枚举** | 新建 `filter/constants.py` | 2h | 消除 4 处常量重复 |
| 2.5 | **ViewConfig dataclass** | `state.py` 或新建 `filter/models.py` | 4h | 类型安全 |
| 2.6 | **Docker 多阶段构建 + .dockerignore** | `Dockerfile`, 新建 `.dockerignore` | 2h | 镜像缩小 60%+ |
| 2.7 | **CI/CD 修复**（合并测试步骤、锁定 mypy、修复 ruff 规则） | `pyproject.toml`, `.github/workflows/` | 3h | CI 有效性和速度 |

**Phase 2 产出**: 回测速度提升 10x+，架构分层清晰，Docker 镜像优化。

---

### Phase 3: 工程化提升（Month 2-3）

目标：提升测试覆盖 + 日志统一 + 类型注解补全

| # | 任务 | 文件/范围 | 预估耗时 | 预期效果 |
|---|------|---------|---------|---------|
| 3.1 | **增加集成测试**（多周期级联、预设导入导出、回测管线） | `tests/test_integration.py` | 6h | 集成回归保护 |
| 3.2 | **覆盖率阈值 55% → 70%** | `pyproject.toml` | 1h + 逐步补测试 | 质量信心 |
| 3.3 | **统一日志体系**（CLI 使用 loguru、backtest_logger 合并到 loguru） | `backtest_cli.py`, `backtest_logger.py` | 4h | 运维可观测性 |
| 3.4 | **components 层类型注解补全** | `charts.py`, `sidebar.py`, `backtest_panel.py`, `bs_marker.py` | 3h | IDE 支持 |
| 3.5 | **iterrows() → itertuples()** | `db.py:86,559,626` | 1h | 3x-10x 行迭代速度 |
| 3.6 | **回测三通道输出统一**（Parquet 为主，JSON 按需导出） | `services/event_recorder.py`, `services/parquet_store.py` | 6h | 维护成本降低 60% |
| 3.7 | **pre-commit 安全 hooks** | `.pre-commit-config.yaml` | 0.5h | 安全防护 |
| 3.8 | **pyproject.toml 补全 [build-system]** | `pyproject.toml` | 0.5h | 包管理标准化 |

**Phase 3 产出**: 测试覆盖 70%+，日志统一，类型安全，工程化达标。

---

### Phase 4: 架构演进（Quarter+）

目标：长期架构健康度和生产就绪度

| # | 任务 | 文件/范围 | 预估耗时 | 预期效果 |
|---|------|---------|---------|---------|
| 4.1 | **引入 Repository 模式统一数据访问** | `db.py` + `data_loader.py` | 8h | 数据源可替换 |
| 4.2 | **定义 Protocol/ABC 抽象层** | `services/`, `components/` | 12h | 架构灵活性 |
| 4.3 | **双轨状态管理统一为 AppState** | `state.py`, `streamlit_app.py` | 6h | 认知负担降低 |
| 4.4 | **Python/HTML 混合渲染分离** | `components/charts.py` | 4h | 可测试性 + 可维护性 |
| 4.5 | **自定义异常类体系** | 新建 `filter/exceptions.py` | 2h | 错误处理精确度 |
| 4.6 | **环境变量统一管理** | `.env` 体系 | 2h | 部署灵活性 |

**Phase 4 产出**: 架构评分 5.0 → 7.5+，生产就绪。

---

## Quick Wins（今天就做）

以下 5 个改进每个可在 30 分钟内完成，无需理解大量上下文，立即见效：

| # | 改进 | 文件 | 操作 | 耗时 |
|---|------|------|------|------|
| QW-1 | **替换 iterrows() 为 itertuples()** | `db.py:86,559,626` | `for row in df.itertuples():` 替代 `for _, row in df.iterrows():` | 20min |
| QW-2 | **修复硬编码路径** | `test_param_export_import.py:20` | `CONFIG_PATH = Path(__file__).parent.parent / "config" / "3690_HK_DP.json"` | 15min |
| QW-3 | **提取 ALL_TFS 到 constants.py** | 新建 `filter/constants.py` | 定义 `ALL_TFS`，4 个文件改为 `from constants import ALL_TFS` | 15min |
| QW-4 | **apply_ema 去 DataFrame** | `filter_engine.py:66` | `pd.Series(signal).ewm(span=span, adjust=False).mean().to_numpy()` | 10min |
| QW-5 | **移除 JSONL flush()** | `event_recorder.py:712` | 删除 `fp.flush()` 调用，依赖 OS buffer 管理 | 5min |

**执行 QW-1 到 QW-5 后**: CI 测试可运行、3x 行迭代速度提升、消除 4 处常量重复、2x EMA 计算速度提升、10x-50x 录制吞吐提升。—— 总共不到 65 分钟。

---

## 投入产出分析

### ROI 评估表

排序依据：收益/成本比（ROI Ratio），优先处理高 ROI 项。

| 优先级 | 项目 | 预估工作量 | 预估收益 | ROI 比率 | 类型 |
|:---:|------|:---:|------|:---:|------|
| 1 | JSONL 去 flush() (QW-5) | 5 min | 10x-50x 录制吞吐 | **极高** | 性能 |
| 2 | apply_ema 去 DataFrame (QW-4) | 10 min | ~2x EMA 速度 | **极高** | 性能 |
| 3 | iterrows → itertuples (QW-1) | 20 min | 3x-10x 行迭代 | **极高** | 性能 |
| 4 | 提取 ALL_TFS (QW-3) | 15 min | 消除 4 处重复 | **极高** | 架构 |
| 5 | 修复硬编码路径 (QW-2) | 15 min | CI 可运行 | **极高** | 质量 |
| 6 | 生成 lock 文件 | 30 min | 环境可复现 | **极高** | 配置 |
| 7 | 修复裸 except | 1h | 问题定位 3x+ | **极高** | 质量 |
| 8 | searchsorted 替代线性搜索 | 1h | 5x-10x 搜索速度 | **极高** | 性能 |
| 9 | pre-commit 安全 hooks | 30 min | 安全防护 | **高** | 配置 |
| 10 | DB 连接逻辑合并 | 2h | 维护成本降低 | **高** | 质量 |
| 11 | Docker 多阶段构建 | 2h | 镜像缩小 60% | **高** | 配置 |
| 12 | 回测多视图并行化 | 3h | 2x-3x 管道吞吐 | **高** | 性能 |
| 13 | 回测 I/O 内存化 | 4h | 5x-10x 回测吞吐 | **高** | 性能 |
| 14 | ViewConfig dataclass | 4h | 类型安全 | **中** | 架构 |
| 15 | CI/CD 修复 | 3h | CI 有效性 | **中** | 配置 |
| 16 | 集成测试增强 | 6h | 重构安全感 | **中** | 质量 |
| 17 | 回测增量计算 | 6h | 3x-5x 管道吞吐 | **中** | 性能 |
| 18 | God Module 拆分 | 12h | 可维护性翻倍 | **中** | 架构 |
| 19 | 日志体系统一 | 4h | 运维可观测性 | **中** | 质量 |
| 20 | 回测三通道统一 | 6h | 维护成本 60% | **中** | 架构 |
| 21 | 分离 Python/HTML 渲染 | 4h | 可测试性 | **中** | 架构 |
| 22 | Repository 模式 | 8h | 数据源可替换 | **低** | 架构 |
| 23 | Protocol/ABC 抽象层 | 12h | 架构灵活性 | **低** | 架构 |
| 24 | 状态管理统一 | 6h | 认知负担降低 | **低** | 架构 |

### 预估总工作量

| Phase | 内容 | 人天 |
|-------|------|:---:|
| Phase 1: 止血 | 测试修复 + lock 文件 + 错误处理 | **0.5 天** |
| Phase 2: 重构 | 性能优化 + God Module + Docker + CI | **4.5 天** |
| Phase 3: 工程化 | 测试 + 日志 + 类型 + 统一 | **3 天** |
| Phase 4: 架构演进 | Repository + ABC + 状态 + 异常 | **4.5 天** |
| **合计** | | **12.5 人天** |

注：以上为估算工作量，实际取决于对代码库的熟悉程度。若仅实施 Phase 1-2（5 人天），即可解决 80% 的关键问题。

---

## 风险与依赖

### 风险

| 风险 | 影响等级 | 可能性 | 缓解措施 |
|------|:---:|:---:|------|
| God Module 拆分引入回归 bug | 高 | 中 | Phase 2 前先完成集成测试增强（P1-6） |
| 回测 I/O 优化改变行为 | 高 | 低 | 利用现有 623 个测试，特别是 `test_backtest.py`(1455行) 作为回归基准 |
| 增量计算引入数值精度差异 | 中 | 中 | 使用 `test_filters.py` 中的 `np.allclose` 断言作为精度基准 |
| ViewConfig dataclass 迁移工作量超预期 | 中 | 中 | 渐进式迁移：先定义 dataclass，再逐文件替换，保持向后兼容 |
| lock 文件与 CI 环境的 Python 版本不一致 | 低 | 低 | 使用 `pip-compile` 生成多版本 lock 文件 |

### 依赖

| 前置任务 | 被阻塞任务 | 原因 |
|---------|-----------|------|
| P0-4 (修复测试) | P1-6 (集成测试) | 需要 CI 绿后才能有效增加测试 |
| P1-6 (集成测试) | P0-3 (God Module 拆分) | 重构前需要集成回归保护 |
| P1-4 (ALL_TFS 统一) | P0-3 (God Module 拆分) | 拆分会涉及常量引用的模块，先统一常量减少冲突 |
| P1-3 (ViewConfig dataclass) | P0-3 (God Module 拆分) | 拆分涉及 cfg dict 传递，先建立类型约束 |
| Phase 1 (止血) | Phase 2 (重构) | 需要稳定基线 |
| 无 lock 文件 (P0-6) | CI/CD 修复 (P1-8) | CI 环境需要可复现的依赖 |

### 建议执行顺序

```
Phase 1 (Week 1)
  Day 1: QW-1 ~ QW-5 (Quick Wins, < 65 min 总计)
  Day 1-2: P0-4 (修复测试) → P0-6 (lock 文件) → P1-4 (ALL_TFS 统一)
  Day 2-3: P0-5 (Docker 多阶段) → P1-8 (CI/CD 修复)

Phase 2 (Week 2-4)
  Week 2: P1-6 (集成测试) → P1-3 (ViewConfig dataclass)
  Week 3: P0-1 (回测 I/O) → P0-2 (增量计算) → P1-1 (并行化)
  Week 4: P0-3 (God Module 拆分) → P1-7 (DB 连接合并)

Phase 3 (Month 2-3)
  按 P2 优先级顺序渐进推进

Phase 4 (Quarter+)
  按需选择，根据项目实际演进方向调整
```

---

## 附：各维度原始评分

| 维度 | 子维度 | 评分 |
|------|--------|:---:|
| **架构** | 代码组织 / 模块耦合度 / 内聚性 / 分层清晰度 / 类型安全 / 可测试性 / 可扩展性 / 新人友好度 | **5.0/10** |
| **性能** | 热点路径 / 数据处理 / I/O / 缓存 / 并发 / 内存 / 算法复杂度 / 启动时间 | **中等** (I/O Critical) |
| **代码质量** | 测试覆盖 B+ / 测试质量 B+ / 代码规范 B+ / 错误处理 B- / 日志 B- / 重复度 B / 技术债务 B+ / 安全 A | **B+ (83/100)** |
| **配置部署** | 依赖健康度 / Docker / CI/CD / 环境管理 / pre-commit / 配置文件 / 部署策略 / 依赖冲突 | **5.8/10** |

> 注：配置部署维度评分来源于事件日志中的分析摘要（config-analysis.md 文件未生成，但其发现已并入本报告）。

---

*报告由四个维度的分析 Agent 产出，经 Writer Agent 交叉去重、优先级排序、ROI 评估后合成。*
