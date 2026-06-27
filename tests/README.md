# 测试文档 — filter_research

**实际测试计数**：333 个测试 / 12 个测试文件 / 66 个测试类 (+ 6 个独立函数)

pytest 输出：`pytest tests/ --collect-only -q` 确认 333 个测试被收集。

---

## 1. 测试目录结构

```
tests/
├── __init__.py                      # 空文件，标识 Python 包
├── conftest.py                      # 共享 fixtures + WidgetAwareSessionState (widget 生命周期约束检测)
├── README.md                        # 本文档
├── test_filters.py                  # 10 种滤波函数 + compute_metrics（31 测试，6 类）
├── test_strategy.py                 # 抛物线拟合 + PnL 计算 + 预测 trace（18 测试，5 类）
├── test_signals.py                  # Schmitt 触发器 + 信号配对（16 测试，2 类）
├── test_boundary.py                 # 边界条件（21 测试，9 类）
├── test_alignment.py                # 跨周期 PnL 对齐（6 测试，1 类）
├── test_alignment_subplot.py        # 对齐子图 + 持仓掩码（14 测试，2 类）
├── test_param_export_import.py      # 参数导入导出（13 测试，5 类）
├── test_config_db.py                # config_db 模块单元测试（46 测试，10 类）
├── test_preset_ui.py                # 预设管理集成测试 — 逻辑层（60 测试，16 类）
├── test_preset_ui_actions.py        # 预设管理 UI 行为测试（45 测试，8 类）
├── test_db.py                       # db.py 完整单元测试（57 测试，14 类）
└── test_integration.py              # 端到端集成测试（6 个独立函数）
```

### 标记（pytest markers）

| 标记 | 用途 | 文件 |
|:--|:--|:--|
| `filter` | 滤波算法测试 | `test_filters.py` |
| `signal` | 信号生成测试 | `test_signals.py` |
| `strategy` | 策略计算测试 | `test_strategy.py` |
| `alignment` | 跨周期对齐测试 | `test_alignment.py` |
| `slow` | 已弃用 — test_boundary.py 全部 `@pytest.mark.slow` 已移除 | — |

---

## 2. 测试概览

| 编号 | 测试文件 | 测试类数 | 测试函数数 | 占总量 | 主要标记 |
|:--|:--|--:|--:|:--|:--|
| 1 | `test_filters.py` | 6 | 31 | 9.5% | `filter` |
| 2 | `test_strategy.py` | 5 | 18 | 5.5% | `strategy` |
| 3 | `test_signals.py` | 2 | 16 | 4.9% | `signal` |
| 4 | `test_boundary.py` | 9 | 21 | 6.3% | — |
| 5 | `test_alignment.py` | 1 | 6 | 1.8% | `alignment` |
| 6 | `test_alignment_subplot.py` | 2 | 14 | 4.3% | — |
| 7 | `test_param_export_import.py` | 5 | 13 | 4.0% | — |
| 8 | `test_config_db.py` | 10 | 46 | 14.0% | — |
| 9 | `test_preset_ui.py` | 16 | 60 | 18.3% | — |
| 10 | `test_preset_ui_actions.py` | 8 | 45 | 13.5% | — |
| 11 | `test_db.py` | 14 | 57 | 17.4% | — |
| 12 | `test_integration.py` | 0 (6 函数) | 6 | 1.8% | — |
| | **合计** | **66 类 + 6 函数** | **333** | **100%** | |

---

## 3. 按源码模块组织的测试索引

### 3.1 滤波函数 — `streamlit_app.py`

| 源码函数 | 源码行号 | 测试文件 | 测试类 | 测试函数数 | 覆盖状态 |
|:--|:--|:--|:--|--:|:--|
| `apply_sma` | 47 | test_filters.py | TestConstantSignal | 1 | ✅ |
| `apply_ema` | 55 | test_filters.py | TestConstantSignal | 1 | ✅ |
| `apply_wma` | 60 | test_filters.py | TestConstantSignal, TestNoiseReduction | 2 | ✅ |
| `apply_alma` | 69 | test_filters.py | TestConstantSignal | 1 | ✅ |
| `apply_savgol` | 81 | test_filters.py | TestConstantSignal, TestNoiseReduction, TestSavgolSpecial | 5 | ✅ |
| `apply_kalman` | 90 | test_filters.py | TestConstantSignal, TestNoiseReduction, TestKalmanSpecial | 5 | ✅ |
| `apply_butterworth` | 115 | test_filters.py | TestConstantSignal, TestNoiseReduction | 3 | ✅ |
| `apply_gaussian` | 124 | test_filters.py | TestConstantSignal, TestNoiseReduction | 2 | ✅ |
| `apply_median` | 129 | test_filters.py | TestConstantSignal, TestNoiseReduction, TestEdgeCases | 3 | ✅ |
| `apply_lowess` | 136 | test_filters.py | TestConstantSignal | 1 | ✅ |
| `compute_metrics` | 217 | test_filters.py, test_integration.py | TestComputeMetrics | 5 | ✅ |
| 所有 10 个滤波器 | — | test_filters.py | TestEdgeCases | 4 | ✅ |

**跨文件集成**：
- `test_integration.py::test_filter_metrics_pipeline` 对全部 10 个滤波器运行端到端管线

### 3.2 信号生成 — `streamlit_app.py`

| 源码函数 | 源码行号 | 测试文件 | 测试类 | 测试函数数 | 覆盖状态 |
|:--|:--|:--|:--|--:|:--|
| `_schmitt_trigger` | 586 | test_signals.py | TestSchmittTrigger | 8 | ✅ |
| `_schmitt_trigger` | 586 | test_boundary.py | TestSchmittTriggerBoundary | 3 | ✅ |
| `_find_all_pairs` | 647 | test_signals.py | TestFindAllPairs | 8 | ✅ |
| `_find_all_pairs` | 647 | test_boundary.py | TestFindAllPairsBoundary | 5 | ✅ |

**跨文件集成**：
- `test_integration.py::test_schmitt_trigger_pipeline` 端到端施密特触发器管线

### 3.3 策略计算 — `streamlit_app.py`

| 源码函数 | 源码行号 | 测试文件 | 测试类 | 测试函数数 | 覆盖状态 |
|:--|:--|:--|:--|--:|:--|
| `_fit_parabolic` | 693 | test_strategy.py | TestFitParabolic | 3 | ✅ |
| `_fit_physics_parabola` | 705 | test_strategy.py | TestFitPhysicsParabola | 4 | ✅ |
| `_fit_parabolic` / `_fit_physics_parabola` | — | test_strategy.py | TestFitComparison | 1 | ✅ |
| `_fit_parabolic` / `_fit_physics_parabola` | — | test_boundary.py | TestFitBoundary | 4 | ✅ |
| `_compute_strategy_pnl` | 780 | test_strategy.py | TestComputeStrategyPnL | 6 | ✅ |
| `_compute_strategy_pnl` | 780 | test_boundary.py | TestComputeStrategyPnlBoundary | 2 | ✅ |
| `_compute_strategy_pnl` | 780 | test_boundary.py | TestEmptyDataDegradation | 1 | ✅ |
| `_compute_strategy_pnl` | 780 | test_boundary.py | TestNumericalStability | 2 | ✅ |
| `_add_prediction_traces` | 726 | test_strategy.py | TestAddPredictionTraces | 3 | ✅ |

**跨文件集成**：
- `test_integration.py::test_prediction_fit_consistency` 两种拟合模式一致性

### 3.4 跨周期对齐 — `streamlit_app.py`

| 源码函数 | 源码行号 | 测试文件 | 测试类 | 测试函数数 | 覆盖状态 |
|:--|:--|:--|:--|--:|:--|
| `_align_pnl_to_current_tf` | 978 | test_alignment.py | TestAlignPnlToCurrentTf | 6 | ✅ |
| `_align_pnl_to_current_tf` | 978 | test_boundary.py | TestAlignPnlBoundary | 1 | ✅ |
| `_compute_holding_masks` | 1148 | test_alignment_subplot.py | TestComputeHoldingMasks | 7 | ✅ |
| `_add_alignment_subplot` | 1186 | test_alignment_subplot.py | TestAlignmentSubplot | 7 | ✅ |

**跨文件集成**：
- `test_integration.py::test_cross_tf_pnl_alignment` 跨周期对齐端到端
- `test_boundary.py::TestCrossTfHierarchy::test_tf_hierarchy_chain` 验证 TF 层次链

### 3.5 参数导入导出 — `streamlit_app.py` / `config_db.py`

| 测试文件 | 测试类 | 测试函数数 | 覆盖范围 |
|:--|:--|--:|:--|
| test_param_export_import.py | TestExportCompleteness | 3 | 导出 JSON key 完整性 |
| test_param_export_import.py | TestImpBackupCoverage | 2 | _imp_ 备份全覆盖 |
| test_param_export_import.py | TestParameterChangeDetection | 2 | 新增/删除参数检测 |
| test_param_export_import.py | TestExpandCollapseParameterRecovery | 4 | 折叠展开后参数恢复 |
| test_param_export_import.py | TestImportIdempotency | 2 | 重复导入幂等性 |

### 3.6 config_db 模块

| 源码函数 | 测试文件 | 测试类 | 测试函数数 | 覆盖状态 |
|:--|:--|:--|--:|:--|
| `init_config_tables` | test_config_db.py | TestInitConfigTables | 2 | ✅ |
| `save_preset` | test_config_db.py | TestPresetCRUD, TestSavePresetValidation | 7 | ✅ |
| `list_presets` | test_config_db.py | TestPresetCRUD | 2 | ✅ |
| `get_preset` | test_config_db.py | TestPresetCRUD | 2 | ✅ |
| `get_preset_by_name` | test_config_db.py | TestPresetCRUD | 1 | ✅ |
| `delete_preset` | test_config_db.py | TestPresetCRUD, TestDeletePresetReturnValue | 3 | ✅ |
| `rename_preset` | test_config_db.py | TestPresetCRUD, TestRenamePresetValidation | 4 | ✅ |
| `apply_preset` | test_config_db.py | TestPresetCRUD | 2 | ✅ |
| `save_ticker_config` | test_config_db.py | TestTickerConfig | 2 | ✅ |
| `load_ticker_config` | test_config_db.py | TestTickerConfig | 1 | ✅ |
| `record_history` | test_config_db.py | TestHistory | 2 | ✅ |
| `get_history` | test_config_db.py | TestHistory | 2 | ✅ |
| `import_json_files_as_presets` | test_config_db.py | TestImportJSONFiles, TestImportJsonFilesReturnValue | 6 | ✅ |
| `collect_current_params` | test_config_db.py | TestCollectCurrentParams | 5 | ✅ |

上述所有函数也在 `test_preset_ui.py` 中通过集成测试覆盖（60 个测试）。

### 3.7 db 模块

| 源码函数 | 源码模块 | 测试文件 | 测试类 | 测试函数数 | 覆盖状态 |
|:--|:--|:--|:--|--:|:--|
| `init_db` | db.py | test_db.py | TestInitDb | 2 | ✅ |
| `upsert_kline` | db.py | test_db.py | TestUpsertKline | 6 | ✅ |
| `query_kline` | db.py | test_db.py | TestQueryKline | 4 | ✅ |
| `get_date_range` | db.py | test_db.py | TestHelpers | 2 | ✅ |
| `has_data` | db.py | test_db.py | TestHelpers | 2 | ✅ |
| `check_data_health` | db.py | test_db.py | TestCheckDataHealth | 7 | ✅ |
| `validate_db` | db.py | test_db.py | TestValidateDb | 4 | ✅ |
| `compare_with_db` | db.py | test_db.py | TestCompareWithDb | 4 | ✅ |
| `force_update_kline` | db.py | test_db.py | TestForceUpdateKline | 2 | ✅ |
| `snapshot_db` / `list_snapshots` / `restore_snapshot` / `prune_snapshots` | db.py | test_db.py | TestSnapshotBackup | 6 | ✅ |
| `checkpoint_wal` | db.py | test_db.py | TestCheckpointWal | 2 | ✅ |
| `get_db_size_mb` | db.py | test_db.py | TestGetDbSize | 2 | ✅ |
| `clear_display_cache` | db.py | test_db.py | TestClearDisplayCache | 3 | ✅ |
| 边界条件 | db.py | test_db.py | TestEdgeCases | 8 | ✅ |
| 并发访问 | db.py | test_db.py | TestConcurrentAccess | 2 | ✅ |

**跨文件集成**：
- `test_integration.py::test_data_pipeline_e2e` 端到端数据管线（写入→查询→偏移）

### 3.8 预设管理 UI 行为

| 测试文件 | 测试类 | 测试函数数 | 覆盖范围 |
|:--|:--|--:|:--|
| test_preset_ui.py | 16 个类 | 60 | 生命周期、CRUD、排序、大规模、分类、session_state 边界 |
| test_preset_ui_actions.py | 8 个类 | 45 | action 标志、名称同步、preset_map、category、toast、widget 冲突、overwrite 重置 |

---

## 4. Bug-修复-测试追溯

| 修复 ID | Bug 描述 | 修复 commit | 覆盖测试 | 测试文件 |
|:--|:--|:--|:--|:--|
| R01 | Plotly JSON NaN 序列化导致图表白屏 | `4cc451e` | — (渲染层) | — |
| R03 | `time.sleep` 阻塞 Streamlit 主线程 | `4cc451e` | — (UI 层) | — |
| R04 | `check_data_health` issues 汇总只记录最后周期 | `4cc451e` | test_health_issues_per_tf_bug_fix | test_db.py |
| R05 | `overwrite_preset` widget-key 冲突崩溃 | `8a50a43` | TestOverwriteCheckboxResetFlow (×4) | test_preset_ui_actions.py |
| P0-1 | `import_json_files_as_presets` 返回 `int` 而非元组 | `f9f7621` | CDB-44~CDB-46 | test_config_db.py |
| P1-1 | `delete_preset` 不返回操作结果 | `f9f7621` | CDB-37~CDB-39 | test_config_db.py |
| P1-2 | `rename_preset` 重名时抛异常 | `f9f7621` | CDB-12, CDB-40 | test_config_db.py |
| P1-5 | `save_preset` 未校验 JSON 有效性 | `f9f7621` | CDB-14, CDB-33, CDB-34 | test_config_db.py |
| P1-6 | `rename_preset` 空名称未校验 | `f9f7621` | CDB-41 | test_config_db.py |
| P0-2 | 删除预设时 FK 级联删除而非 SET NULL | `f9f7621` | CDB-39 | test_config_db.py |

---

## 5. 运行指南

### 基础用法

```bash
# 运行全部 333 个测试
python -m pytest tests/ -v

# 运行全部测试（含覆盖率报告）
python -m pytest tests/ --cov=streamlit --cov-report=term-missing

# 快速运行（短回溯）
python -m pytest tests/ -v --tb=short
```

### 按标记

```bash
# 仅非慢速测试（跳过边界）
python -m pytest tests/ -v -m "not slow"

# 仅慢速测试（边界条件）
python -m pytest tests/ -v -m "slow"

# 仅滤波测试
python -m pytest tests/ -v -m "filter"

# 仅信号测试
python -m pytest tests/ -v -m "signal"

# 仅策略测试
python -m pytest tests/ -v -m "strategy"

# 仅对齐测试
python -m pytest tests/ -v -m "alignment"
```

### 按文件/模块

```bash
# 按源码模块分组运行
# 滤波模块
python -m pytest tests/test_filters.py -v
# 信号模块
python -m pytest tests/test_signals.py -v
# 策略模块
python -m pytest tests/test_strategy.py tests/test_boundary.py -v
# 对齐模块
python -m pytest tests/test_alignment.py tests/test_alignment_subplot.py -v
# 配置模块
python -m pytest tests/test_config_db.py tests/test_param_export_import.py -v
# 预设管理
python -m pytest tests/test_preset_ui.py tests/test_preset_ui_actions.py -v
# 数据库模块
python -m pytest tests/test_db.py -v
# 集成测试
python -m pytest tests/test_integration.py -v
```

### 按测试类/函数

```bash
# 单个测试类
python -m pytest tests/test_filters.py::TestConstantSignal -v

# 单个测试函数
python -m pytest tests/test_filters.py::TestConstantSignal::test_sma_constant -v
```

### 覆盖率报告

```bash
# HTML 覆盖率报告
python -m pytest tests/ --cov=streamlit --cov-report=html
open htmlcov/index.html
```

---

## 6. 已知问题和限制

### 6.1 Streamlit mock 的限制

- **`conftest.py`** 将 `streamlit` 替换为 `MagicMock`，因此任何依赖 Streamlit 运行时行为的功能**无法**在单元测试中真实验证：
  - `st.button()` 的点击回调
  - `st.selectbox()` / `st.text_input()` 的 widget 交互
  - `st.toast()` / `st.success()` / `st.error()` 的实际 UI 反馈
  - `st.rerun()` 触发的页面重渲染
  - `st.session_state` 在真实 Streamlit 中的生命周期
- **变通方案**：行为测试使用 `MockSessionState(dict)` 模拟标志状态机。`test_preset_ui_actions.py` 新增 `_WidgetAwareSessionState` 类模拟 Streamlit widget 生命周期约束（`register_widget → lock → 禁止直接赋值`），在测试阶段即可拦截 widget-key 冲突 bug。`conftest.py` 提供等效的 `WidgetAwareSessionState` 供其他测试复用。

### 6.2 无法测试真实 UI 渲染

- Plotly 图表的视觉效果无法在测试中验证，只能验证 trace 数量和数据值（参见 `test_alignment_subplot.py`）
- Streamlit 的 layout（`st.columns`、`st.expander`、`st.sidebar`）不在测试范围内

### 6.3 数据库测试隔离

- `config_db` 相关测试使用 `tempfile.TemporaryDirectory` + `monkeypatch` 替换 `_CONFIG_DB_PATH` 和 `_CONFIG_DIR`
- `test_db.py` 使用 `tmp_path` fixture 创建临时 SQLite，通过 `db.DB_PATH` 替换隔离

### 6.4 无真实网络/数据依赖

- 所有信号和策略测试使用 `numpy` 生成的合成数据
- `test_param_export_import.py` 读取 `config/3690_HK_DP.json` 作为参考文件，不访问网络

### 6.5 未覆盖的测试场景

- Streamlit app 的 `main()` 函数未在单元测试中覆盖
- 并发/多用户场景下的 SQLite 锁竞争未测试
- 异常 DB 文件（损坏的 SQLite、权限不足）的降级行为未测试
- `config_db.get_connection()` 的线程安全问题未测试

### 6.6 文件计数说明

- **12 个测试文件**：`tests/` 目录中所有 `test_*.py` 文件
- **66 个测试类**：分布在 10 个类式测试文件中（`test_integration.py` 不含类）
- **6 个独立函数**：`test_integration.py` 的 6 个顶级函数
- **333 个测试**：来自 `pytest --collect-only -q` 的精确计数
