# 测试文档完整性报告

**审计日期**: 2026-07-11 | **基线文档**: docs/test_cases.md (2026-07-05 更新) | **pytest 收集**: 815 passed

---

## 1. 现有测试文档清单

| 文档 | 路径 | 覆盖范围说明 |
|------|------|-------------|
| 主测试用例文档 | `docs/test_cases.md` | 21 个测试文件的 TC-ID 表格（宣称 662 用例） |
| 数据计算测试规格 | `docs/tests/data_computation_test_cases.md` | 滤波/Schmitt/拟合/PnL/对齐手动计算验证（TC-DATA-01~06） |
| UI 交互测试手动用例 | `docs/tests/ui_test_cases.md` | 6 个手工交互域（TC-UI-01~06），含 Bug 回归矩阵 |

---

## 2. 文档-实际差异总表

### 2.1 完整缺失文件（MISSING）

| 测试文件 | 实际测试数 | 文档状态 | 依据 |
|----------|-----------|---------|------|
| `test_feedback_subplot.py` | 6 (6 functions) | **完全未提及** | `grep -rn 'feedback_subplot' docs/test_cases.md` → 0 匹配 |
| `test_cascading_synthesis.py` | 53 (15 classes) | **完全未提及** | docs/test_cases.md 附录统计表列 21 文件，但实际 tests/ 有 23 个 test_*.py |

### 2.2 已覆盖但实际多出（UNDER-COUNTED / DRIFT）

| 文件 | 文档声称 | 实际函数数 | 差距 | 遗露的测试类/方法（依据：grep 比对） |
|------|---------|-----------|------|--------------------------------------|
| `test_alignment.py` | 6 (section 4.2) | 10 (+4) | +4 | `TestEodHigherPositionExtend` (2 方法: `test_eod_exit_maps_to_last_current_bar`, `test_non_eod_exit_not_extended`), `TestPreWindowEntryExtend` (2 方法: `test_pre_window_entry_includes_start`, `test_position_entirely_before_window_not_shown`) |
| `test_strategy.py` | 18 (section 2.3) | 20 (+2) | +2 | `TestComputeStrategyPnL` 中 `test_entry_without_prediction_still_trades`, `test_downward_parabola_no_longer_vetoes_long` |
| `test_param_export_import.py` | 13 (section 6.2) | 18 (+5) | +5 | `TestParamRegistryGuard` (5 方法: `test_registry_includes_pnlfb`, `test_sidebar_params_covered_by_registry`, `test_export_helper_covers_pnlfb`, `test_collect_current_params_includes_pnlfb`, `test_json_roundtrip_pnlfb`) |
| `test_backtest.py` | 56 (附录表格) | 75 (+19) | +19 | 多个新测试类: `TestPlayback` (8), `TestNavigationButtons` (6) — doc 中部分存在但代码中同名/类似类有不同方法数；代码中 `TestBacktestNavButtons` (10) 远多于 doc 的 TestBacktestNavButtons (7) |

### 2.3 文档多于实际（STALE）

| 文件 | 文档声称 | 实际函数数 | 差距 | 过期条目（依据：TC-ID 在 docs 中存在但代码中无对应） |
|------|---------|-----------|------|------------------------------------------------------|
| `test_charts.py` | 90 (section 4.1) | 86 (-4) | -4 | `TestCrossPnlSubplot` 在代码中仅 4 方法，但文档列 8 条 TC-CHART-060~067。**TC-CHART-061** (空头参考线添加)、**TC-CHART-064** (入场标记渲染)、**TC-CHART-065** (出场标记渲染)、**TC-CHART-067** (marker 颜色为金色) 已不存在或已被合并入 `TestCrossPnlSubplotTrades` |

### 2.4 内部文档不一致

| 位置 | 问题 | 依据 |
|------|------|------|
| `test_state.py` | section 1.3 标题写 "59 用例"，但附录表写 62 | 69 行 vs 1663 行，实际是 62（多出的 3 个是 TestBacktestSliderKeys） |
| `test_backtest.py` section 8.2 | 标题写 "31 用例"，但列出的 TC-BT-026~062 共 **37** 个 ID | 1548 行 vs 实际表格行数，差额 6 |
| `test_backtest.py` 附录表 | 写 "56" 但实际 TC-ID 范围 TC-BT-001~062 = 62 个 ID，实际代码 75 个方法 | 1679 行 vs 实际 greps |

### 2.5 一致（MATCHES，差异 ≤ 函数签名别名）

| 文件 | 文档 | 实际 | 状态 |
|------|------|------|------|
| test_config_db.py | 58 | 58 | ok |
| test_db.py | 57 | 57 | ok |
| test_filters.py | 22 | 22 | ok |
| test_signals.py | 16 | 16 | ok |
| test_sidebar.py | 36 | 36 | ok |
| test_preset_ui.py | 60 | 60 | ok |
| test_preset_ui_actions.py | 45 | 45 | ok |
| test_app_ui.py | 33 | 33 | ok |
| test_streamlit_app.py | 25 | 25 | ok |
| test_alignment_subplot.py | 14 | 14 | ok |
| test_integration.py | 6 | 6 | ok |
| test_integration_flows.py | 9 | 9 | ok |
| test_app_smoke.py | 1 | 1 | ok |
| test_boundary.py | 30 | 30 | ok |
| test_data_loader.py | 30 | 30 | ok |

---

## 3. 结构评估

`docs/test_cases.md` 采用 **模块(1-8) → 文件 → 测试类 → 表格** 的 4 级结构，整体清晰。但存在以下结构问题：

1. **文件级遗漏**: 2 个完整测试文件 (`test_feedback_subplot.py`, `test_cascading_synthesis.py`) 毫无文档映射。这两个文件超过 55+ 个测试方法，占实际总数 815 的约 7%。
2. **类/方法级遗漏**: 3 个已覆盖文件 (`test_alignment.py`, `test_strategy.py`, `test_param_export_import.py`) 共有 11 个测试方法未在文档中列出。
3. **附录统计表不准确**: 声称 662 用例、21 文件，实际是 815 用例、23 文件。误差率达 **23%**。
4. **内部计数矛盾**: test_state.py 和 test_backtest.py 的 section 标题计数与附录表或实际 TC-ID 数量不符。

---

## 4. 具体更新建议

### 4.1 必须补录

| 优先级 | 建议 | 理由 |
|--------|------|------|
| P0 | 在文档中新增 `## 9. 回测级联合成` 或划入 `## 2. 算法核心测试`，覆盖 `test_cascading_synthesis.py` 全部 15 个类（~53 测试），分配 TC-CASCADE-001~053 | 文件已 661 行，是第二大测试文件 |
| P0 | 在文档 `## 4. 可视化测试` 新增 `test_feedback_subplot.py` 小节（6 测试），分配 TC-FB-001~006 | 持仓状态可视化，与 alignment_subplot 紧密相关 |
| P1 | `test_alignment.py` 补录 `TestEodHigherPositionExtend`、`TestPreWindowEntryExtend` 两个类（4 方法），TC-ALIGN-007~010 | 日末持仓延续和窗口前入场是时间对齐的关键边界场景 |
| P1 | `test_strategy.py` 补录 `test_entry_without_prediction_still_trades` 和 `test_downward_parabola_no_longer_vetoes_long`（TC-STRAT-019~020） | 入场解耦回归和二次项符号守卫逻辑是策略重大变更 |
| P1 | `test_param_export_import.py` 补录 `TestParamRegistryGuard` 类（5 方法），TC-PARAM-014~018 | PnL feedback 参数在导入导出注册表中的守卫测试 |
| P1 | `test_backtest.py` 补录 `TestPlayback` (8)、`TestNavigationButtons` (6)、`TestBacktestIntegration` (1)、`TestUpdateCutoffAndRerun` (4) 等未覆盖类的缺失方法 | 播放/导航/滑块同步是回测模式的核心交互 |

### 4.2 必须删除/修正

| 优先级 | 建议 | 理由 |
|--------|------|------|
| P1 | `test_charts.py` 中删除 TC-CHART-061/064/065/067 共 4 个已不存在的用例 | 代码中 `TestCrossPnlSubplot` 已从 8 个测试重构为 4 个 |
| P2 | 全局修正计数：section 1.3 标题改为 "62 用例"；section 8.2 标题改为 "37 用例" 或按实际调整；附录表 test_backtest.py 改为 ≥62 | 消除内部不一致 |
| P2 | 更新文档头部 "测试总数: 662" 为 "815"（或按函数数写 772） | 与实际 pytest 结果对齐 |

### 4.3 命名对齐（非功能性）

| 位置 | 建议 |
|------|------|
| test_backtest.py section 8.1 | 文档写 "TestBacktestDataLoading" 但实际类名是 `TestSyncToDisplay` — 建议统一为实际类名 |
| docs/test_cases.md 内附录 | 增加 `test_feedback_subplot.py` 和 `test_cascading_synthesis.py` 两行，更新 `21 文件` → `23 文件` |
| test_state.py section 1.3 | 文档列了 TestAppStateHas 的 TC-STATE-004~008 为 5 个用例，实际代码中 TestAppStateHas 也是 5 个方法，一致。但 TestViewStateBuildCfg 在文档中是 6 个用例（049-054），实际也是 6 个方法，一致。 |

---

## 5. 汇总统计

| 指标 | 数值 |
|------|------|
| 文档宣称测试数 | 662 |
| pytest 收集总数 | 815 |
| 差异绝对值 | +153 (+23%) |
| 测试文件数（文档） | 21 |
| 测试文件数（实际） | 23 |
| 完全遗漏的测试文件 | 2 (test_feedback_subplot.py, test_cascading_synthesis.py) |
| 部分遗漏的测试文件 | 3 (test_alignment, test_strategy, test_param_export_import) |
| 过期条目 | 4 (test_charts.py TestCrossPnlSubplot) |
| 内部计数矛盾 | 3 处 (test_state, test_backtest ×2) |
