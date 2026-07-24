# 功能缺口审计

> 审计日期: 2026-07-24
> 测试环境: macOS Python 3.12.6 | 项目不依赖 Git 托管远程

## 按优先级排序

| # | 问题 | 严重度 | 影响 | 修复建议 |
|---|------|--------|------|---------|
| 1 | streamlit_app.py 入口文件缺失 | P0-CRITICAL | Docker 构建无法启动, make run 失败, CI 测试失败 | 统一入口路径为 `filter_app/browse/app.py`, 同步更新 Dockerfile, Makefile, test_app_smoke.py |
| 2 | config_presets 表缺少 description 列 | P0-CRITICAL | FK 迁移后 get_preset_by_name 崩溃 | init_config_tables 增加 ALTER TABLE 补齐缺失列 |
| 3 | pyproject.toml 版本落后于 git tag | P1-HIGH | CI 版本一致性检查失败, 打包错误 | 更新 version 为 3.5.0 或重新 tag |
| 4 | CHANGELOG 缺少 v3.5.0 条目 | P1-HIGH | CI changelog-check 失败 | 运行 make changelog 或手动添加 |
| 5 | charts.py 路径过期 (4 个测试) | P2-MEDIUM | 4 个 RenderPlotlyHtml 测试全部 FileNotFoundError | 测试中路径从 `components/charts.py` 改为 `browse/charts.py` |
| 6 | test_render_params_expand_toggle_button 断言失效 | P2-MEDIUM | UI 默认行为与测试预期不一致 | 检查 _render_params 中 expand 默认值是否应为 True |
| 7 | Dockerfile HEALTHCHECK 使用进程内自检 | P3-LOW | 无 curl/wget 的 slim 镜像中可能失败 | 改用 Python urllib 自检或移除 HEALTHCHECK |

## 详细分析

### 1. streamlit_app.py 入口文件缺失 (P0-CRITICAL)

- **现状**: 代码入口已重构为 `filter_app/browse/app.py`, 但 Dockerfile CMD 和 Makefile 仍引用 `filter_app/streamlit_app.py`（文件不存在）。
- **根因**: 目录重构（components → browse）后, 入口文件路径变更但配置文件未同步更新。
- **修复**:
  1. Dockerfile 第 33 行: `streamlit run filter_app/streamlit_app.py` → `streamlit run filter_app/browse/app.py`
  2. Makefile 第 38 行: 同上
  3. tests/test_app_smoke.py 第 14 行: 同上
- **预估**: 3 处改动, 5 分钟.

### 2. config_presets 表缺少 description 列 (P0-CRITICAL)

- **现状**: `init_config_tables()` 只处理 `config_ticker` 表的 FK 迁移, 但 `get_preset_by_name()` 查询 `description` 列。旧 schema 的 `config_presets` 表无此列, 导致 `sqlite3.OperationalError: no such column: description`。
- **根因**: `config_presets` 的 schema 曾增加 `description` 列, 但 `init_config_tables` 缺少 ALTER TABLE 补齐逻辑。
- **修复**: 在 `init_config_tables()` 的迁移逻辑中, 检测 `config_presets` 表是否含 `description` 列, 缺失时执行 `ALTER TABLE config_presets ADD COLUMN description TEXT DEFAULT ''`。
- **预估**: ~20 行代码, 30 分钟.

### 3. pyproject.toml 版本落后于 git tag (P1-HIGH)

- **现状**: `pyproject.toml` 声明 version=`3.4.0`, 但最新 git tag (按 creatordate) 为 `v3.5.0`。
- **根因**: tag v3.5.0 已创建但 pyproject.toml 未同步更新。
- **修复**: 将 `pyproject.toml` 第 11 行 `version = "3.4.0"` 改为 `version = "3.5.0"`。或如果 v3.5.0 是误打的 tag, 则删除该 tag。
- **预估**: 1 行改动, 1 分钟.

### 4. CHANGELOG 缺少 v3.5.0 条目 (P1-HIGH)

- **现状**: CHANGELOG.md 记录了 v10.9.0 → v1.0 的历史, 但缺少 v3.5.0。`make changelog` 可能无法正常执行（依赖 git-cliff 等工具）。
- **根因**: tag v3.5.0 被创建时未运行 `make changelog` 同步。
- **修复**: 手动在 CHANGELOG.md 的 `[Unreleased]` 之后添加 `## [v3.5.0]` 条目, 或安装 git-cliff 后运行 `make changelog`。
- **预估**: 5 分钟.

### 5. charts.py 路径过期 (4 个测试) (P2-MEDIUM)

- **现状**: `tests/test_charts.py` 第 275-300 行的 `TestRenderPlotlyHtml` 类中的 4 个测试引用 `filter_app/components/charts.py`, 但该文件已迁移至 `filter_app/browse/charts.py`。
- **根因**: 目录重构后测试未更新路径。
- **修复**:
  - `_src / "components" / "charts.py"` → `_src / "browse" / "charts.py"` (3 处)
  - `_src / "static" / "charts.js"` → `_src / ".." / "web_tool" / "verify_filters.js"` 或确认 `charts.js` 的实际位置
- **预估**: ~5 行改动, 15 分钟.

### 6. test_render_params_expand_toggle_button 断言失效 (P2-MEDIUM)

- **现状**: 测试断言 `ss.get("v0_exp_all") is True`, 但默认值为 `False`。
- **根因**: `_render_params` 中展开/折叠按钮的默认状态从 `True` 改为 `False`（可能是 UI 优化，默认折叠更简洁），测试未同步更新。
- **修复**: 两种选择:
  - A) 若默认折叠是有意为之 → 测试改为 `assert ss.get("v0_exp_all") is False`
  - B) 若默认展开是设计要求 → `_render_params` 中初始值改回 `True`
- **预估**: 1 行改动 + 确认设计意图, 10 分钟.

### 7. Dockerfile HEALTHCHECK 在 slim 镜像中的风险 (P3-LOW)

- **现状**: HEALTHCHECK 使用 `python -c "import urllib.request; ..."` 方式自检, 当前实现正确（不依赖 curl/wget）。但之前 Dockerfile 可能用 curl 方式, 已在当前版本修复。
- **根因**: N/A（当前实现正确）。
- **修复**: 不需要修复, 仅记录为观察项。
- **预估**: 0 分钟（已正确处理）。

---

## 审计清单逐项结果

### 1. 预存测试失败
9 个失败, 1510 通过, 1 跳过。根因分类:
- 路径过期: 5 个（app smoke + 4 charts HTML）
- 版本不同步: 2 个（pyproject + changelog）
- Schema 迁移缺陷: 1 个（config_db FK migration）
- 断言更新遗漏: 1 个（sidebar expand toggle）

### 2. 未集成模块: metrics / catalog
**已集成**, 无缺口。`engine.py` 通过 lazy import 在 `_compute_and_log_metrics()` 和 `run()` 的 finally 块中调用。
- `compute_backtest_metrics`: 在 `_compute_and_log_metrics` (L1066) 中调用
- `BacktestCatalog.save_index()`: 在 `run()` (L305) 中调用

### 3. CLI 完整性
**完备**。argparse 覆盖了所有必要参数（ticker, preset, config-file, start-bar, end-bar, output-dir, step-interval, resume, checkpoint-interval, view-filter, quiet, no-save-data）。错误处理充分（ticker 无数据、preset 不存在、bar 范围非法、断点恢复失败均有明确错误信息）。`--help` 输出友好且包含使用示例。

### 4. Web 完整性
**合并为单页面应用**（`filter_app/browse/app.py`），通过侧边栏提供以下功能：
- 浏览: 4 视图 2x2 图表、参数配置
- 回测: backtest panel（时间导航 + 级联数据同步 + 自动播放）
- 数据: 健康检查、数据验证、DB 备份/恢复、导入/导出
- 分析: 滤波器选择、施密特触发器、策略 PnL、跨周期对齐、BS 标记

**无独立分页路由**，所有功能在同一页面内通过 session_state 切换模式。这实际上是设计选择而非缺口。

### 5. 数据链路: fetcher → db → store → loader → cache
**链路完整**, 无断点。
```
fetcher._fetch_stock() → yfinance API
  → force_update_kline() → db kline 表
    → loader.query_kline() → pd.DataFrame
      → loader._sync_to_display() → data/display/{ticker}/YYYY/MM/{ticker}_{tf}.parquet
        → loader.load_display_cache() → browse/app.py 图表渲染
```
`store.py` 中的 `ParquetStore` 服务于回测数据输出（独立链路），不在此浏览链路中。

### 6. 配置完整性: ViewConfig vs 实际使用
**一致**。`ViewConfig` dataclass 定义了 19 个字段, 与 `cli.py` 的 `_VIEW_SPECS` (14 项映射) 和 `config_db.py` 的 `VIEW_PARAM_SPECS` 完全对齐。字段涵盖: tf, n_pts, _fid, pv, _dual, _fid2, pv2, show_sch, ke, sm, ew, show_pred, fit_mode, n_ext, show_strategy, stop_loss_pct, fc, fc2, show_cross_pnl, show_alignment, show_pnl_feedback。

### 7. Docker 可用性
`docker build` 存在运行时问题（CMD 入口路径错误），但构建本身（COPY/RUN 阶段）应能成功。修复 P0-1 后即可正常工作。CI 中的 `docker-verify` 任务使用 `docker/build-push-action` 进行构建验证。

### 8. CI 通过率
CI 配置覆盖 6 个阶段:
- **lint**: ruff 代码检查 — 应通过
- **fast-tests** (11 test files): test_app_smoke, test_config_db, test_changelog, test_version 会失败
- **medium-tests** (17 test files): test_charts (4 failures), test_sidebar (1 failure) 会失败
- **snapshot-tests**: 未本地验证
- **slow-tests**: test_app_ui 未本地验证
- **coverage**: 目标 55%, 但会因为上述失败影响报告
- **changelog-check**: PR 时检查 CHANGELOG 更新 — 针对当前状态会失败
- **docker-verify**: 构建应能成功, 但启动会因入口路径问题失败

**预估 CI 通过率**: 6/8 阶段部分或全部通过（仅 lint + snapshot 可能全绿）。
