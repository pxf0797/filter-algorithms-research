# 测试文档 — filter_research

## 目录结构

```
tests/
├── __init__.py                 # 空文件，标识包
├── conftest.py                 # 共享 fixtures: mock streamlit、信号、日期
├── README.md                   # 本文档
├── test_config_db.py           # config_db 单元测试 (32 tests)
├── test_preset_ui.py           # 预设管理集成测试 (49 tests)
├── test_param_export_import.py # 参数导入导出测试 (13 tests)
├── test_filters.py             # 滤波算法测试
├── test_boundary.py            # 边界检测 & Schmitt trigger
├── test_signals.py             # 信号生成 & find_all_pairs
├── test_strategy.py            # 策略 PnL & 抛物线拟合
├── test_alignment.py           # 跨周期 PnL 对齐
└── test_alignment_subplot.py   # 对齐子图 & 持仓掩码
```

## 如何运行

```bash
# 全部测试
pytest

# 预设管理相关（94 tests）
pytest tests/test_config_db.py tests/test_preset_ui.py tests/test_param_export_import.py

# 单个文件
pytest tests/test_preset_ui.py

# 指定测试类
pytest tests/test_preset_ui.py::TestSelectboxRefresh

# 指定用例
pytest tests/test_preset_ui.py::TestSelectboxRefresh::test_delete_clears_selection

# 详细输出
pytest -v --tb=short
```

## 各文件说明

### test_config_db.py — DB 层单元测试 (32 tests, 92% 覆盖率)

使用独立临时 SQLite 数据库，每个测试完全隔离。

**Schema & Init (2):**
| 测试 | 说明 |
|:--|:--|
| `test_creates_tables` | 验证 3 张表被创建 |
| `test_idempotent` | 重复初始化不报错 |

**Preset CRUD (11):**
| 测试 | 说明 |
|:--|:--|
| `test_save_new_preset` | 新建预设，返回 preset_id |
| `test_save_update_existing` | 同名保存触发 UPDATE |
| `test_list_presets_all` | 列出全部预设 |
| `test_list_presets_by_category` | 按分类过滤 |
| `test_get_preset` | 按 ID 获取 |
| `test_get_preset_by_name` | 按名称获取 |
| `test_get_preset_not_found` | 不存在返回 None |
| `test_delete_preset` | 删除后查不到 |
| `test_rename_preset` | 重命名后名称变更 |
| `test_rename_to_existing_name` | 重名应报错 (UNIQUE) |
| `test_apply_invalid_json` / `test_apply_not_found` | 边界情况 |

**Ticker Config (3):**
| 测试 | 说明 |
|:--|:--|
| `test_save_and_load_ticker_config` | 保存并加载标的配置 |
| `test_load_nonexistent_ticker` | 不存在的标的返回 None |
| `test_save_ticker_with_preset_ref` | 带 preset_id 引用的标的保存 |

**History (4):**
| 测试 | 说明 |
|:--|:--|
| `test_record_and_get_history` | 记录并读取变更历史 |
| `test_history_with_preset_id` | 关联 preset_id 的历史 |
| `test_history_empty_for_new_ticker` | 新标的历史为空 |
| `test_history_default_limit` | 默认 limit=20 |

**Import JSON (5):**
| 测试 | 说明 |
|:--|:--|
| `test_import_creates_presets` | JSON 文件导入为预设 |
| `test_import_skips_existing` | 已存在不覆盖 (force=False) |
| `test_import_force_overwrite` | force=True 覆盖 |
| `test_import_empty_dir` | 空目录导入 0 条 |
| `test_import_skips_bad_json` | 非法 JSON 跳过不崩溃 |

**Collect Params (5):**
| 测试 | 说明 |
|:--|:--|
| `test_collects_global_keys` | 收集 market/ticker 等全局参数 |
| `test_collects_view_params` | 收集 v0~v3 视图参数 |
| `test_collects_filter_params` | 收集中文滤波器 key |
| `test_ignores_unrelated_keys` | 过滤无关 session_state 值 |
| `test_empty_session_state` | 空状态不报错 |

---

### test_preset_ui.py — 预设管理集成测试 (49 tests)

逻辑层测试，直接调用 `config_db` 函数 + 模拟 session_state 交互，不依赖 Streamlit 运行时。

**生命周期 (1):**
| 测试 | 说明 |
|:--|:--|
| `test_full_preset_lifecycle` | 创建 → 应用 → 更新 → 重命名 → 删除 全流程 |

**删除 (3):**
| 测试 | 说明 |
|:--|:--|
| `test_delete_preset_removes_from_list` | 删除后 `list_presets()` 不含该项 |
| `test_get_preset_after_delete` | 删除后 `get_preset()` 返回 None |
| `test_delete_nonexistent_does_not_raise` | 删除不存在的 ID 不抛异常 |

**重命名 (3):**
| 测试 | 说明 |
|:--|:--|
| `test_rename_preset_updates_list` | 列表反映新名称，无旧名称 |
| `test_get_preset_reflects_new_name` | `get_preset()` 返回新名称 |
| `test_get_preset_by_name_old_name_fails` | 旧名称查不到 |

**应用预设 (4):**
| 测试 | 说明 |
|:--|:--|
| `test_apply_preset_populates_session_state` | 返回值可直接写入 session_state |
| `test_apply_preset_empty_params` | 空 JSON 返回空 dict |
| `test_apply_preset_invalid_json_returns_none` | 非法 JSON 返回 None |
| `test_apply_nonexistent_preset_returns_none` | 不存在的 ID 返回 None |

**覆盖保存 (3):**
| 测试 | 说明 |
|:--|:--|
| `test_save_overwrite_preset` | 覆盖后参数更新、ID 不变 |
| `test_overwrite_reduces_list_count` | 覆盖不增加总数 |
| `test_multiple_overwrites` | 多次覆盖参数正确更新 |

**JSON 导入 (6):**
| 测试 | 说明 |
|:--|:--|
| `test_import_json_as_presets` | JSON 文件成功导入 |
| `test_import_respects_existing_names` | force=False 不覆盖已有 |
| `test_import_force_overwrites` | force=True 覆盖已有 |
| `test_import_empty_config_dir` | 空目录导入 0 条 |
| `test_import_categorizes_by_suffix` | _DP→双滤波, _QS→快速 |
| `test_import_bad_json_skipped` | 非法 JSON 跳过 |

**空列表 (2):**
| 测试 | 说明 |
|:--|:--|
| `test_preset_list_empty_initially` | 空 DB 返回 [] |
| `test_list_with_category` | 空分类返回 [] |

**同名处理 (3):**
| 测试 | 说明 |
|:--|:--|
| `test_duplicate_preset_name_updates` | 同名保存触发 UPDATE |
| `test_duplicate_name_count` | 同名多次不增加总数 |
| `test_rename_into_conflicting_name_succeeds` | 重命名为已删除的名称 |

**边界情况 (4):**
| 测试 | 说明 |
|:--|:--|
| `test_preset_name_empty_string` | 空字符串名称 |
| `test_large_params_json` | 1000 参数的大 JSON |
| `test_list_presets_sorted_by_category_then_name` | 分类+名称排序 |
| `test_uniqueness_constraint_violation_triggers_update` | UNIQUE 冲突触发 UPDATE |

**Selectbox 刷新 — `_selected_preset` + `index` 模式 (5):**
| 测试 | 说明 |
|:--|:--|
| `test_delete_clears_selection` | 删除后 `_selected_preset='(不选择)'`，selectbox 归零 |
| `test_rename_updates_selection` | 重命名后 `_selected_preset` 指向新名 |
| `test_stale_value_falls_back_to_first` | 幽灵值不在选项中 → 自动回退第0项 |
| `test_apply_preset_keeps_selection` | 应用预设保持选中状态 |
| `test_save_new_targets_new_preset` | 保存新预设自动跳到新项 |

**增删改连续操作 (5):**
| 测试 | 说明 |
|:--|:--|
| `test_create_modify_delete_recreate` | 创建→更新→删除→同名重建 完整周期 |
| `test_rename_then_recreate_original_name` | 重命名后旧名可复用 |
| `test_multiple_rapid_renames` | 连续多次重命名 |
| `test_delete_all_then_reimport` | 全删后从 JSON 重新导入 |
| `test_overwrite_preserves_id` | 覆盖更新保持 preset_id 不变 |

**Session State 边界 (7):**
| 测试 | 说明 |
|:--|:--|
| `test_empty_session_state_collect` | 空 session_state 无报错 |
| `test_partial_global_keys` | 部分全局参数正确收集 |
| `test_collect_all_four_views` | 4 视图全量参数收集 |
| `test_collect_chinese_filter_keys` | 9 种中文滤波器 key 正确处理 |
| `test_import_data_flag_preserved` | 应用预设后 `_import_data` 标志正确 |
| `test_imp_backup_not_overwritten` | `_imp_` 备份不被 widget 默认值覆盖 |
| `test_ticker_switch_different_presets` | 切换 ticker 加载不同预设 |

**大规模数据 (3):**
| 测试 | 说明 |
|:--|:--|
| `test_many_presets_list_and_query` | 100 个预设的列表和查询 |
| `test_large_params_json` | 1000 key 大 JSON 配置 |
| `test_bulk_delete_and_recreate` | 50 条批量删除后重建 |

---

### test_param_export_import.py — 参数导入导出测试 (13 tests)

| 测试类 | 说明 |
|:--|:--|
| `TestExportCompleteness` (3) | 导出 JSON 包含所有必需参数 |
| `TestImpBackupCoverage` (2) | 每个导入参数有 `_imp_` 备份 |
| `TestParameterChangeDetection` (2) | 自动检测新增/删除的参数 |
| `TestExpandCollapseParameterRecovery` (4) | 折叠展开后参数恢复 |
| `TestImportIdempotency` (2) | 重复导入不产生脏数据 |

### 其他测试文件

| 文件 | 覆盖 |
|:--|:--|
| `test_filters.py` | 中值/Kalman/SavGol/HP/EMA 滤波算法 + 边界条件 |
| `test_boundary.py` | Schmitt trigger 边界检测 & 抛物线拟合 |
| `test_signals.py` | 信号生成 & find_all_pairs 信号段合并 |
| `test_strategy.py` | 策略 PnL、止损逻辑、预测 trace |
| `test_alignment.py` | 跨周期 PnL 时间对齐 |
| `test_alignment_subplot.py` | 持仓掩码、holding 计算、多空对齐 |

## 测试前置条件

- `conftest.py` 在 `sys.path` 中添加 `streamlit/` 目录
- `conftest.py` 在导入前 mock 整个 `streamlit` 模块，无需 Streamlit 运行时
- `test_config_db.py` 和 `test_preset_ui.py` 使用 `tempfile` + `monkeypatch` 隔离数据库
- 预设管理测试 (94) 完全独立，不依赖网络或真实股票数据

## 预设管理功能覆盖矩阵

```
功能                         单元测试    集成测试    总计
─────────────────────────────────────────────────────
预设 CRUD (增删改查)          11          4         15
Selectbox 刷新 (index模式)     —          5          5
增删改连续操作                  —          5          5
Session State 交互             5          7         12
JSON 导入导出                  5          6         11
Ticker 配置                    3          —          3
History 变更记录                4          —          4
大规模/边界                    2          9         11
参数导入导出 (兼容)             —         13         13
Schema 初始化                  2          —          2
─────────────────────────────────────────────────────
总计                          32         49         81 + 13 = 94
```
