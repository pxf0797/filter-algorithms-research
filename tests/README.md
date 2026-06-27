# 测试文档 — filter_research

## 目录结构

```
tests/
├── __init__.py                 # 空文件，标识包
├── conftest.py                 # 共享 fixtures: mock streamlit、信号、日期
├── test_alignment.py           # 时间对齐（跨周期 PnL 对齐）
├── test_alignment_subplot.py   # 对齐子图（持仓掩码、holding 计算）
├── test_boundary.py            # 边界检测、Schmitt trigger、导出配置
├── test_config_db.py           # config_db 单元测试（预设 CRUD、ticker、history）
├── test_filters.py             # 滤波算法（中值、Kalman、SavGol、HP、EMA）
├── test_param_export_import.py # 参数导入导出（完整性、_imp_ 备份、可恢复性）
├── test_preset_ui.py           # 预设管理集成测试（完整生命周期、边界条件）
├── test_signals.py             # 信号生成（Schmitt trigger、find_all_pairs）
├── test_strategy.py            # 策略 PnL、抛物线拟合、预测 trace
├── README.md                   # 本文档
```

## 如何运行

```bash
# 运行全部测试
pytest

# 运行单个文件
pytest tests/test_preset_ui.py

# 运行指定测试类
pytest tests/test_preset_ui.py::TestPresetLifecycle

# 运行指定测试用例
pytest tests/test_preset_ui.py::TestDeletePreset::test_delete_preset_removes_from_list

# 跳过慢速测试
pytest -m "not slow"

# 显示详细输出 + 短回溯
pytest -v --tb=short

# 覆盖率报告
pytest --cov=streamlit --cov-report=term-missing
```

## 各文件说明

### test_config_db.py — config_db 单元测试

测试 `config_db.py` 中数据库层的每个函数，使用独立的临时 SQLite 数据库隔离。

**预设 CRUD 测试：**

| 测试用例 | 类别 | 说明 |
|---|---|---|
| `test_save_new_preset` | CRUD | 新建预设并验证返回 ID |
| `test_save_update_existing` | CRUD | 同名保存触发 UPDATE |
| `test_list_presets_all` | CRUD | 列出全部预设 |
| `test_list_presets_by_category` | CRUD | 按分类过滤列表 |
| `test_get_preset` | CRUD | 按 ID 获取预设 |
| `test_get_preset_by_name` | CRUD | 按名称获取预设 |
| `test_get_preset_not_found` | CRUD | 不存在的 ID 返回 None |
| `test_delete_preset` | CRUD | 删除后查不到 |
| `test_rename_preset` | CRUD | 重命名后名称变更 |
| `test_apply_preset_parse` | CRUD | 应用预设解析 JSON |

**Ticker 配置测试：**

| 测试用例 | 类别 | 说明 |
|---|---|---|
| `test_save_and_load_ticker` | Ticker | 保存并加载标的配置 |
| `test_save_ticker_with_preset_ref` | Ticker | 带预设引用的标的保存 |
| `test_ticker_not_found` | Ticker | 不存在的标的返回 None |
| `test_update_ticker_config` | Ticker | 更新标的配置 |

**History 测试：**

| 测试用例 | 类别 | 说明 |
|---|---|---|
| `test_record_and_get_history` | History | 记录并读取变更历史 |
| `test_history_limit` | History | 历史记录数量限制 |
| `test_history_with_preset_name` | History | 历史中关联预设名称 |

**JSON 导入测试：**

| 测试用例 | 类别 | 说明 |
|---|---|---|
| `test_import_json_files` | Import | JSON 文件导入为预设 |
| `test_import_empty_config_dir` | Import | 空目录导入 0 条 |
| `test_import_skip_existing` | Import | 跳过已存在的同名预设 |

**边界条件测试：**

| 测试用例 | 类别 | 说明 |
|---|---|---|
| `test_init_tables_idempotent` | Schema | 重复初始化不报错 |
| `test_schema_creates_tables` | Schema | 验证三张表都存在 |

### test_preset_ui.py — 预设管理集成测试

逻辑层测试，直接测试 `config_db` 函数 + 模拟 `session_state` 交互，无需 Streamlit 运行时。

**测试覆盖矩阵：**

| # | 分类 | 测试用例 | 覆盖点 |
|---|---|---|---|
| 1 | 生命周期 | `test_full_preset_lifecycle` | 创建 → 读取 → 应用(JSON解析) → 更新 → 重命名 → 删除 |
| 2 | 删除 | `test_delete_preset_removes_from_list` | 删除后列表不含该项 |
| 3 | 删除 | `test_get_preset_after_delete` | 删除后 get_preset 返回 None |
| 4 | 删除 | `test_delete_nonexistent_does_not_raise` | 删除不存在的 ID 不抛异常 |
| 5 | 重命名 | `test_rename_preset_updates_list` | 列表反映新名称，无旧名称 |
| 6 | 重命名 | `test_get_preset_reflects_new_name` | get_preset 返回新名称 |
| 7 | 重命名 | `test_get_preset_by_name_old_name_fails` | 旧名称查不到 |
| 8 | 应用 | `test_apply_preset_populates_session_state` | 返回值可直接写入 session_state |
| 9 | 应用 | `test_apply_preset_empty_params` | 空 JSON 返回空 dict |
| 10 | 应用 | `test_apply_preset_invalid_json_returns_none` | 非法 JSON 返回 None |
| 11 | 应用 | `test_apply_nonexistent_preset_returns_none` | 不存在的 ID 返回 None |
| 12 | 覆盖 | `test_save_overwrite_preset` | 覆盖后参数更新、ID 不变 |
| 13 | 覆盖 | `test_overwrite_reduces_list_count` | 覆盖不增加总数 |
| 14 | 覆盖 | `test_multiple_overwrites` | 多次覆盖更新同一条 |
| 15 | 导入 | `test_import_json_as_presets` | JSON 文件成功导入 |
| 16 | 导入 | `test_import_respects_existing_names` | 不覆盖已有 (force=False) |
| 17 | 导入 | `test_import_force_overwrites` | force=True 覆盖已有 |
| 18 | 导入 | `test_import_empty_config_dir` | 空目录导入 0 条 |
| 19 | 导入 | `test_import_categorizes_by_suffix` | _DP/QS/其他分类正确 |
| 20 | 导入 | `test_import_bad_json_skipped` | 非法 JSON 被跳过 |
| 21 | 空列表 | `test_preset_list_empty_initially` | 空 DB 返回 [] |
| 22 | 空列表 | `test_list_with_category` | 空分类返回 [] |
| 23 | 同名 | `test_duplicate_preset_name_updates` | 同名保存触发 UPDATE |
| 24 | 同名 | `test_duplicate_name_count` | 同名多次不增加总数 |
| 25 | 同名 | `test_rename_into_conflicting_name_succeeds` | 重命名为已删除的名称 |
| 26 | 边界 | `test_preset_name_empty_string` | 空字符串名称 |
| 27 | 边界 | `test_large_params_json` | 1000 参数的大 JSON |
| 28 | 边界 | `test_list_presets_sorted_by_category_then_name` | 分类+名称排序 |
| 29 | 边界 | `test_uniqueness_constraint_violation_triggers_update` | UNIQUE 冲突触发 UPDATE |

### test_param_export_import.py — 参数导入导出测试

| 测试类 | 说明 |
|---|---|
| `TestExportCompleteness` | 验证导出的 JSON 包含所有必需参数 |
| `TestImpBackupCoverage` | 验证每个导入参数有 `_imp_` 备份 |
| `TestParameterChangeDetection` | 自动检测新增/删除的参数（信息性） |
| `TestExpandCollapseParameterRecovery` | 模拟折叠展开后参数恢复 |
| `TestImportIdempotency` | 重复导入不产生脏数据 |

### test_filters.py — 滤波算法测试

覆盖中值滤波器、Kalman 滤波器、Savitzky-Golay 滤波器、高通滤波器、EMA 滤波器，以及空数组、NaN 输入、大窗口等边界条件。

### test_boundary.py — 边界检测测试

Schmitt trigger 边界检测、抛物线拟合、导出配置、数值稳定性、跨周期对齐。

### test_alignment.py / test_alignment_subplot.py

时间轴对齐、持仓掩码计算、多空标识、交易记录对齐。

### test_strategy.py

抛物线拟合（parabolic/physics 两种模式）、策略 PnL 计算、止损逻辑、预测 trace。

### test_signals.py

Schmitt trigger 信号生成、find_all_pairs 信号段合并。

## 测试前置条件

- `conftest.py` 在 `sys.path` 中添加 `streamlit/` 目录
- `conftest.py` 在导入前 mock 整个 `streamlit` 模块，确保 pytest 不依赖 Streamlit 运行时
- `test_config_db.py` 和 `test_preset_ui.py` 使用 `tempfile` + `monkeypatch` 隔离数据库
