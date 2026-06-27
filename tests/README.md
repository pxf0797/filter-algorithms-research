# 测试文档 — filter_research

## 1. 测试目录结构

```
tests/
├── __init__.py                  # 空文件，标识 Python 包
├── conftest.py                  # 共享 fixtures：streamlit mock、信号数据、OHLC、日期
├── README.md                    # 本文档
├── test_config_db.py            # config_db 模块单元测试（45 tests，10 个测试类）
├── test_preset_ui.py            # 预设管理集成测试 — 逻辑层（60 tests，16 个测试类）
├── test_preset_ui_actions.py    # 预设管理 UI 行为测试 — session_state 标志、同步、反馈（35 tests，5 个测试类）
├── test_filters.py              # 10 种滤波函数 + compute_metrics 测试（32 tests，6 个测试类）
├── test_signals.py              # Schmitt 触发器信号 + _find_all_pairs 测试（17 tests，2 个测试类）
├── test_strategy.py             # 策略 PnL 计算 + 抛物线拟合测试（17 tests，5 个测试类）
├── test_boundary.py             # 边界条件测试（22 tests，9 个测试类）
├── test_alignment.py            # 跨周期 PnL 对齐测试（6 tests，1 个测试类）
├── test_alignment_subplot.py    # 对齐子图 + 持仓掩码测试（15 tests，2 个测试类）
└── test_param_export_import.py  # 参数导入导出测试（13 tests，5 个测试类）
```

### 各文件用途

| 文件 | 用途 | 依赖 |
|:--|:--|:--|
| `conftest.py` | 共享 pytest fixtures：mock `streamlit` 模块（`MagicMock`）、信号数据（`constant_signal`、`linear_signal`、`noisy_sine`、`clean_sine`、`random_walk`、`time_index`）、OHLC DataFrame、日期序列（日线/分钟/周线） | 无 |
| `test_config_db.py` | `config_db` 模块完整单元测试：表初始化、预设 CRUD、ticker 配置、历史记录、JSON 导入、参数收集、入口校验、返回值验证、外键约束 | `conftest.py` |
| `test_preset_ui.py` | 预设管理集成测试（逻辑层）：完整生命周期、删除、重命名、应用填充 session_state、覆盖保存、JSON 导入、空列表、同名处理、边界条件、hash-based selectbox 刷新、连续操作、session_state 边界、大规模数据、category 保留、参数收集完整性、preset_map 查表 | `conftest.py` |
| `test_preset_ui_actions.py` | 预设管理 UI 新行为测试（无 Streamlit 运行时依赖）：`_preset_action` / `_preset_action_id` 标志状态机、`new_preset_name` 自动同步、`preset_map` 字典查表、category 参数补全、toast 反馈机制模拟 | `conftest.py` |
| `test_filters.py` | 10 种滤波函数测试：SMA、EMA、WMA、ALMA、Savitzky-Golay、Kalman、Butterworth、Gaussian、Median、LOWESS + `compute_metrics`。覆盖常量信号、降噪效果、边界条件 | `conftest.py` |
| `test_signals.py` | `_schmitt_trigger` 和 `_find_all_pairs` 单元测试：滞回验证、做多/做空触发、sigma_min 地板、NaN 传播、信号段配对、同号合并 | 无（直接 import） |
| `test_strategy.py` | `_fit_parabolic`、`_fit_physics_parabola`、`_compute_strategy_pnl`、`_add_prediction_traces` 测试：抛物线拟合精度、多空交易 PnL、止损触发、独立资金池、连续交易、极端止损 | 无（直接 import） |
| `test_boundary.py` | 边界条件与数值稳定性：全 NaN filtered、短序列、零间隔合并、频繁交替信号、极端价格、负价格、拟合退化、空数据降级、TF_HIERARCHY 链完整性 | `conftest.py` |
| `test_alignment.py` | `_align_pnl_to_current_tf` 测试：时区混合对齐（tz-naive + tz-aware HKT）、无时间重叠、前向填充验证、higher_dates=None 降级、marker 位置验证 | `conftest.py` |
| `test_alignment_subplot.py` | `_compute_holding_masks` 和 `_add_alignment_subplot` 测试：多空掩码生成、多段持仓拼接、trade_records 高亮、越界防护 | 无（直接 import） |
| `test_param_export_import.py` | 参数导入导出完整性测试：导出 JSON key 覆盖验证、`_imp_` 备份覆盖、参数变更检测、折叠展开恢复、导入幂等性 | 无（读真实 config JSON） |

---

## 2. 测试用例清单

### 2.1 test_config_db.py — config_db 模块单元测试（45 tests）

| 用例ID | 测试类 | 测试方法 | 描述 | 前置条件 | 预期结果 |
|:--|:--|:--|:--|:--|:--|
| CDB-01 | TestInitConfigTables | `test_creates_tables` | 验证 3 张表（config_presets、config_ticker、config_history）被创建 | temp_config_db fixture | 3 张 config_ 前缀表存在 |
| CDB-02 | TestInitConfigTables | `test_idempotent` | 重复调用 init_config_tables 不抛异常 | temp_config_db fixture | 连续 3 次调用无异常 |
| CDB-03 | TestPresetCRUD | `test_save_new_preset` | 保存新预设返回 preset_id | temp_config_db + sample_params_json | 返回 int > 0 |
| CDB-04 | TestPresetCRUD | `test_save_update_existing` | 同名保存触发 UPDATE，preset_id 不变，内容+分类更新 | temp_config_db + sample_params_json | pid1 == pid2，params 和 category 更新 |
| CDB-05 | TestPresetCRUD | `test_list_presets_all` | 列出全部预设 | temp_config_db | 2 条记录 |
| CDB-06 | TestPresetCRUD | `test_list_presets_by_category` | 按分类过滤预设 | temp_config_db | 筛选后仅含指定分类 |
| CDB-07 | TestPresetCRUD | `test_get_preset` | 按 ID 获取预设完整信息 | temp_config_db + sample_params_json | name/description/category/params_json 全匹配 |
| CDB-08 | TestPresetCRUD | `test_get_preset_by_name` | 按名称获取预设 | temp_config_db + sample_params_json | 非 None，name 匹配 |
| CDB-09 | TestPresetCRUD | `test_get_preset_not_found` | 不存在预设返回 None | temp_config_db | get_preset(99999) is None，get_preset_by_name 也 None |
| CDB-10 | TestPresetCRUD | `test_delete_preset` | 删除后查不到 | temp_config_db + sample_params_json | get_preset(pid) is None |
| CDB-11 | TestPresetCRUD | `test_rename_preset` | 重命名后 list/get 反映新名称 | temp_config_db + sample_params_json | name 变更，旧名称从列表中消失 |
| CDB-12 | TestPresetCRUD | `test_rename_to_existing_name` | 重名为已存在名称返回 None（名称唯一性检查） | temp_config_db + sample_params_json | 返回 None，原名未被篡改 |
| CDB-13 | TestPresetCRUD | `test_apply_preset` | apply_preset 返回解析后的 dict | temp_config_db | 返回值 == 保存时的 params dict |
| CDB-14 | TestPresetCRUD | `test_apply_preset_invalid_json` | 非法 JSON 在 save_preset 阶段即被拒绝 | temp_config_db | raise ValueError("有效的 JSON") |
| CDB-15 | TestPresetCRUD | `test_apply_preset_not_found` | 不存在 preset 返回 None | temp_config_db | apply_preset(99999) is None |
| CDB-16 | TestTickerConfig | `test_save_and_load_ticker_config` | 保存后能完整读出 ticker 配置 | temp_config_db | ticker/variant/market/params_json 全匹配 |
| CDB-17 | TestTickerConfig | `test_save_with_preset_id` | 包含 preset_id 的 ticker 保存 | temp_config_db | load_ticker_config 返回 preset_id 匹配 |
| CDB-18 | TestTickerConfig | `test_load_nonexistent_ticker` | 不存在的 ticker 返回 None | temp_config_db | load_ticker_config("NOEXIST") is None |
| CDB-19 | TestHistory | `test_record_and_get_history` | 写入历史后能按序读出 | temp_config_db | 2 条记录，new_json/old_json 内容正确 |
| CDB-20 | TestHistory | `test_history_with_preset_id` | 带 preset_id 的历史记录正确 JOIN | temp_config_db | preset_id 和 preset_name 匹配 |
| CDB-21 | TestHistory | `test_history_empty_for_new_ticker` | 从未记录过的 ticker 返回空列表 | temp_config_db | get_history("UNKNOWN") == [] |
| CDB-22 | TestHistory | `test_history_default_limit` | 默认 limit=20，过多记录只返回最近 20 条 | temp_config_db | 25 条写入，返回 20 条 |
| CDB-23 | TestImportJSONFiles | `test_import_creates_presets` | mock config 目录，导入 3 个 JSON 文件为预设 | temp_config_db + temp dir with 3 JSONs | n==3，分类推断正确（_DP→双滤波, _QS→快速） |
| CDB-24 | TestImportJSONFiles | `test_import_skips_existing` | force=False 时已存在预设不覆盖 | temp_config_db + 预先保存同名预设 | n==0，原值保留 |
| CDB-25 | TestImportJSONFiles | `test_import_force_overwrite` | force=True 时覆盖同名预设 | temp_config_db + 预先保存同名预设 | n==1，参数被新文件内容覆盖 |
| CDB-26 | TestImportJSONFiles | `test_import_empty_dir` | config 目录不存在时返回 0 | temp_config_db + /nonexistent_dir | n==0 |
| CDB-27 | TestImportJSONFiles | `test_import_skips_bad_json` | 非法 JSON 文件被静默跳过 | temp_config_db + 1 good + 2 bad JSONs | n==1，get_preset_by_name("good") 非 None |
| CDB-28 | TestCollectCurrentParams | `test_collects_global_keys` | 收集 market/ticker/global_f/global_dual/global_f2 | temp_config_db + 设置 session_state | 5 个全局 key 全部收集 |
| CDB-29 | TestCollectCurrentParams | `test_collects_view_params` | 收集 v0~v3 视图参数（tf、n、strat、align 等） | temp_config_db + 设置 session_state | 视图参数正确映射 |
| CDB-30 | TestCollectCurrentParams | `test_collects_filter_params` | 收集中文 key 的滤波器参数 | temp_config_db + 10 种中文 key | 窗口大小、跨度、偏移量等全部收集 |
| CDB-31 | TestCollectCurrentParams | `test_ignores_unrelated_keys` | session_state 中无关 key 不被收集 | temp_config_db + 无关 key | unrelated/foobar 不出现在结果中 |
| CDB-32 | TestCollectCurrentParams | `test_empty_session_state` | 空 session_state 返回空 dict | temp_config_db | params == {} |
| CDB-33 | TestSavePresetValidation | `test_empty_name_raises_value_error` | 空字符串/纯空白名称抛出 ValueError | temp_config_db | raise ValueError("名称不能为空") |
| CDB-34 | TestSavePresetValidation | `test_invalid_json_raises_value_error` | 无效 JSON 字符串抛出 ValueError | temp_config_db | raise ValueError("有效的 JSON") |
| CDB-35 | TestSavePresetValidation | `test_valid_save_returns_int` | 正常保存返回 int | temp_config_db + sample_params_json | isinstance(pid, int) and pid > 0 |
| CDB-36 | TestSavePresetValidation | `test_overwrite_returns_same_preset_id` | 覆盖已有名称返回相同 preset_id | temp_config_db + sample_params_json | pid1 == pid2，内容更新 |
| CDB-37 | TestDeletePresetReturnValue | `test_delete_existing_returns_true` | 删除存在预设返回 True | temp_config_db + sample_params_json | result is True，记录消失 |
| CDB-38 | TestDeletePresetReturnValue | `test_delete_nonexistent_returns_false` | 删除不存在预设返回 False | temp_config_db | result is False |
| CDB-39 | TestDeletePresetReturnValue | `test_delete_referenced_preset_sets_null` | 删除被 ticker 引用的预设，FK ON DELETE SET NULL 生效 | temp_config_db + ticker 引用预设 | preset_id 变为 None，ticker 记录保留 |
| CDB-40 | TestRenamePresetValidation | `test_rename_to_existing_name_returns_none` | 重命名为已存在名称返回 None | temp_config_db + sample_params_json | result is None，原名未变 |
| CDB-41 | TestRenamePresetValidation | `test_rename_to_empty_string_returns_none` | 重命名为空字符串返回 None | temp_config_db + sample_params_json | result is None，原名保留 |
| CDB-42 | TestRenamePresetValidation | `test_rename_nonexistent_preset_returns_none` | 重命名不存在 ID 返回 None | temp_config_db | result is None |
| CDB-43 | TestRenamePresetValidation | `test_rename_success_returns_new_name` | 重命名成功返回新名称字符串 | temp_config_db + sample_params_json | result == "new_name" |
| CDB-44 | TestImportJsonFilesReturnValue | `test_returns_tuple_of_count_and_errors` | 验证返回 (int, list) 元组结构 | temp_config_db + 1 valid JSON | result == (1, []) |
| CDB-45 | TestImportJsonFilesReturnValue | `test_invalid_json_in_errors_not_raised` | 无效 JSON 不抛异常，出现在 errors 列表中 | temp_config_db + bad JSON | count==0, errors 包含文件名和"失败" |
| CDB-46 | TestImportJsonFilesReturnValue | `test_mixed_valid_and_invalid` | 混合有效无效文件，count 只计成功 | temp_config_db + 2 good + 1 bad JSONs | count==2, len(errors)==1 |

### 2.2 test_preset_ui.py — 预设管理集成测试（60 tests）

| 用例ID | 测试类 | 测试方法 | 描述 | 前置条件 | 预期结果 |
|:--|:--|:--|:--|:--|:--|
| UI-01 | TestPresetLifecycle | `test_full_preset_lifecycle` | 创建→应用→更新→重命名→删除 完整生命周期 | isolate_db | 每阶段状态正确，最终 get_preset 返回 None |
| UI-02 | TestDeletePreset | `test_delete_preset_removes_from_list` | 删除后 list_presets() 不含该项 | isolate_db | "Keep" 在列表中，"Remove" 不在 |
| UI-03 | TestDeletePreset | `test_get_preset_after_delete` | 删除后 get_preset 返回 None | isolate_db | get_preset(pid) is None |
| UI-04 | TestDeletePreset | `test_delete_nonexistent_does_not_raise` | 删除不存在 ID 不抛异常 | isolate_db | 不抛异常 |
| UI-05 | TestRenamePreset | `test_rename_preset_updates_list` | 重命名后 list 反映新名称 | isolate_db | names 含 "NewName"，不含 "OldName" |
| UI-06 | TestRenamePreset | `test_get_preset_reflects_new_name` | get_preset 返回新名称 | isolate_db | p["name"] == "After" |
| UI-07 | TestRenamePreset | `test_get_preset_by_name_old_name_fails` | 旧名称查不到 | isolate_db | get_preset_by_name("OldName") is None |
| UI-08 | TestApplyPresetSessionState | `test_apply_preset_populates_session_state` | apply_preset 返回可写入 session_state 的 dict | isolate_db | 返回值各 key 与保存值一致 |
| UI-09 | TestApplyPresetSessionState | `test_apply_preset_empty_params` | params_json 为 {} 时返回空 dict | isolate_db | result == {} |
| UI-10 | TestApplyPresetSessionState | `test_apply_preset_invalid_json_returns_none` | 非法 JSON 在 save_preset 阶段即被拒绝 | isolate_db | raise ValueError("有效的 JSON") |
| UI-11 | TestApplyPresetSessionState | `test_apply_nonexistent_preset_returns_none` | 不存在 ID 返回 None | isolate_db | apply_preset(9999) is None |
| UI-12 | TestSaveOverwritePreset | `test_save_overwrite_preset` | 覆盖后参数更新，ID 不变 | isolate_db | pid == pid2，loaded["value"] == 2 |
| UI-13 | TestSaveOverwritePreset | `test_overwrite_reduces_list_count` | 覆盖不增加列表条目数 | isolate_db | len(list_presets()) == 2 |
| UI-14 | TestSaveOverwritePreset | `test_multiple_overwrites` | 多次覆盖参数正确更新到最终值 | isolate_db | loaded["v"] == 5 |
| UI-15 | TestImportJsonAsPresets | `test_import_json_as_presets` | JSON 文件成功导入 | isolate_db + config dir with JSON | count==1，预设名匹配 |
| UI-16 | TestImportJsonAsPresets | `test_import_respects_existing_names` | force=False 不覆盖已有预设 | isolate_db + 预先创建同名 | count==0，原值保留 |
| UI-17 | TestImportJsonAsPresets | `test_import_force_overwrites` | force=True 覆盖已有预设 | isolate_db + 预先创建同名 | count==1，参数被覆盖 |
| UI-18 | TestImportJsonAsPresets | `test_import_empty_config_dir` | 空目录导入 0 条 | isolate_db | count==0 |
| UI-19 | TestImportJsonAsPresets | `test_import_categorizes_by_suffix` | _DP → 双滤波，_QS → 快速，其他 → 单滤波 | isolate_db + 3 JSONs | 分类推断全部正确 |
| UI-20 | TestImportJsonAsPresets | `test_import_bad_json_skipped` | 非法 JSON 跳过 | isolate_db + bad JSON | count==0 |
| UI-21 | TestPresetListEmpty | `test_preset_list_empty_initially` | 空 DB 返回 [] | isolate_db | list_presets() == [] |
| UI-22 | TestPresetListEmpty | `test_list_with_category` | 空分类返回 [] | isolate_db | list_presets(category="不存在") == [] |
| UI-23 | TestDuplicatePresetName | `test_duplicate_preset_name_updates` | 同名保存触发 UPDATE | isolate_db | pid1 == pid2 |
| UI-24 | TestDuplicatePresetName | `test_duplicate_name_count` | 同名多次不增加总数 | isolate_db | len(list_presets()) == 1 |
| UI-25 | TestDuplicatePresetName | `test_rename_into_conflicting_name_succeeds` | 重命名可使用已删除的旧名称 | isolate_db | pid2 != pid1，新名称创建成功 |
| UI-26 | TestPresetEdgeCases | `test_preset_name_empty_string` | 空字符串名称被拒绝 | isolate_db | raise ValueError("名称不能为空") |
| UI-27 | TestPresetEdgeCases | `test_large_params_json` | 1000 参数大 JSON 正常读写 | isolate_db | 1000 个键值对完整恢复 |
| UI-28 | TestPresetEdgeCases | `test_list_presets_sorted_by_category_then_name` | list 按 category 再按 name 排序 | isolate_db | category "a" 在前，同 category 内按 name 排序 |
| UI-29 | TestPresetEdgeCases | `test_uniqueness_constraint_violation_triggers_update` | UNIQUE 冲突触发 UPDATE，不抛异常 | isolate_db | 不抛异常 |
| UI-30 | TestSelectboxRefresh | `test_delete_changes_hash_resets_widget` | 删除后选项列表变→hash 变→widget 自动重置到"(不选择)" | isolate_db | before_hash != after_hash，选中项为 "(不选择)" |
| UI-31 | TestSelectboxRefresh | `test_rename_changes_hash` | 重命名后 hash 变化 | isolate_db | hash 变化，选项含新名称不含旧名称 |
| UI-32 | TestSelectboxRefresh | `test_save_new_changes_hash` | 保存新预设后 hash 变化 | isolate_db | hash 变化，新预设出现在选项中 |
| UI-33 | TestSelectboxRefresh | `test_no_change_same_hash` | 无操作时 hash 不变，widget 状态保持 | isolate_db | 两次 hash 相同 |
| UI-34 | TestSelectboxRefresh | `test_apply_preset_does_not_change_hash` | 纯读操作不改变 hash | isolate_db | 前后 hash 相同 |
| UI-35 | TestPresetCrudCycle | `test_create_modify_delete_recreate` | 创建→更新→删除→同名重建 完整周期 | isolate_db | 新建的 pid != 原 pid，data 正确 |
| UI-36 | TestPresetCrudCycle | `test_rename_then_recreate_original_name` | 重命名后旧名可复用 | isolate_db | "Alpha" 和 "Beta" 各一条 |
| UI-37 | TestPresetCrudCycle | `test_multiple_rapid_renames` | 连续多次重命名 | isolate_db | 最终名称为 "R4"，中间名称都不存在 |
| UI-38 | TestPresetCrudCycle | `test_delete_all_then_reimport` | 全删后从 JSON 重新导入 | isolate_db + 创建 JSON 文件 | n==1，presets[0]["name"]=="C_US" |
| UI-39 | TestPresetCrudCycle | `test_overwrite_preserves_id` | 覆盖更新保持 preset_id 不变 | isolate_db | pid 不变，gen==3，总数==1 |
| UI-40 | TestSessionStateEdgeCases | `test_empty_session_state_collect` | 空 session_state 返回空 dict | setup_method 清空 session_state | params == {} |
| UI-41 | TestSessionStateEdgeCases | `test_partial_global_keys` | 部分全局参数正确收集，无关 key 被过滤 | 设置 market/ticker/some_random | market/ticker 被收集，some_random 不在结果中 |
| UI-42 | TestSessionStateEdgeCases | `test_collect_all_four_views` | 4 视图全量参数收集（15 pk × 4） | 设置 v0~v3 所有 pk | 60 个 key 全部收集 |
| UI-43 | TestSessionStateEdgeCases | `test_collect_chinese_filter_keys` | 10 种带 view 前缀的中文滤波器 key | 设置完整中文 key | 所有中文 key 被正确收集 |
| UI-44 | TestSessionStateEdgeCases | `test_import_data_flag_preserved` | 应用预设后 _import_data 标志正确 | 创建预设+apply+写入 session_state | _import_data == "preset" |
| UI-45 | TestSessionStateEdgeCases | `test_imp_backup_not_overwritten` | _imp_ 备份不被 widget 默认值覆盖 | 设置 _imp_ 备份后修改 widget 值 | _imp_ 备份值不变 |
| UI-46 | TestSessionStateEdgeCases | `test_ticker_switch_different_presets` | 切换 ticker 加载不同预设参数 | 创建 AAPL_base + TSLA_base | 各自 v0_ke 不同，互不干扰 |
| UI-47 | TestLargeScale | `test_many_presets_list_and_query` | 100 个预设的列表和查询 | isolate_db | 100 条，单滤波 34 条，Preset042 的 index=42 |
| UI-48 | TestLargeScale | `test_large_params_json` | 1000 key 大 JSON 配置读写 | isolate_db | 1000 键值对完整恢复 |
| UI-49 | TestLargeScale | `test_bulk_delete_and_recreate` | 50 条批量删除后重建 | isolate_db | 删后 0 条，重建后 50 条，覆盖后数据正确 |
| UI-50 | TestSavePresetWithCategory | `test_overwrite_explicit_category_preserved` | 覆盖保存时显式传相同分类，分类不变 | isolate_db | category=="双滤波"，params 更新 |
| UI-51 | TestSavePresetWithCategory | `test_new_preset_uses_default_category` | 新建不传分类使用默认 "通用" | isolate_db | category=="通用" |
| UI-52 | TestSavePresetWithCategory | `test_overwrite_with_different_category_updates` | 覆盖传不同分类，分类更新 | isolate_db | category=="快速"，params 更新 |
| UI-53 | TestCollectParamsCompleteness | `test_collects_all_global_keys` | 收集全部 5 个全局参数 key | setup_method | market/ticker/global_f/global_dual/global_f2 完整 |
| UI-54 | TestCollectParamsCompleteness | `test_collects_all_view_keys_for_four_views` | 收集 v0~v3 全部 15 pk × 4 = 60 个视图参数 | setup_method | 60 个 key 全部存在且值正确 |
| UI-55 | TestCollectParamsCompleteness | `test_collects_all_chinese_filter_prefixes` | 收集全部 10 种中文滤波器参数前缀 | setup_method | 10 个中文 key 全部收集 |
| UI-56 | TestCollectParamsCompleteness | `test_unrelated_keys_not_collected` | 无关 key（_internal、sidebar_state）不被收集 | setup_method | market/ticker 在结果中，无关 key 不在 |
| UI-57 | TestPresetMapLookup | `test_different_presets_distinct_by_id` | 不同预设拥有不同 preset_id，可通过 ID 精确查找 | isolate_db | pid1 != pid2，name/category 各自正确 |
| UI-58 | TestPresetMapLookup | `test_list_presets_ordered_by_category_then_name` | list 排序：双滤波 < 快速，同 category 内按 name | isolate_db | category 顺序 ["双滤波","快速","快速"]，name 顺序 ["A","M","Z"] |
| UI-59 | TestPresetMapLookup | `test_same_name_overwrites_not_creates_duplicate` | 同名 UPSERT 不创建重复 | isolate_db | pid1==pid2，category 更新，总数==1 |
| UI-60 | TestPresetMapLookup | `test_get_preset_by_name_retrieves_unique` | get_preset_by_name 精确返回唯一匹配 | isolate_db | 存在返回非 None，不存在返回 None |

### 2.3 test_preset_ui_actions.py — 预设管理 UI 行为测试（35 tests）

| 用例ID | 测试类 | 测试方法 | 描述 | 前置条件 | 预期结果 |
|:--|:--|:--|:--|:--|:--|
| ACT-01 | TestPresetActionFlags | `test_update_button_sets_action_flags` | 点击"更新"按钮后设置 _preset_action='update' | isolate_db + MockSessionState | flags 值正确 |
| ACT-02 | TestPresetActionFlags | `test_rename_button_sets_action_flags` | 点击"重命名"按钮后设置 _preset_action='rename' | isolate_db + MockSessionState | flags 值正确 |
| ACT-03 | TestPresetActionFlags | `test_delete_button_sets_action_flags` | 点击"删除"按钮后设置 _preset_action='delete' | isolate_db + MockSessionState | flags 值正确 |
| ACT-04 | TestPresetActionFlags | `test_action_flags_cleared_after_update_complete` | 更新完成后标志被清除 | isolate_db + 预先设置 flags | _preset_action 和 _preset_action_id 都不存在 |
| ACT-05 | TestPresetActionFlags | `test_action_flags_cleared_after_rename_complete` | 重命名完成后标志被清除 | isolate_db + 预先设置 flags | flags 被 pop 清除 |
| ACT-06 | TestPresetActionFlags | `test_action_flags_cleared_after_delete_complete` | 删除完成后标志被清除 | isolate_db + 预先设置 flags | flags 被 pop 清除 |
| ACT-07 | TestPresetActionFlags | `test_action_flags_cleared_on_cancel` | 取消操作后标志被清除 | isolate_db + 预先设置 flags | flags 被 pop 清除 |
| ACT-08 | TestPresetActionFlags | `test_action_target_not_found_clears_flags` | 目标预设不存在时清除标志 | 设置 actions 指向不存在 ID | flags 被清除 |
| ACT-09 | TestPresetActionFlags | `test_no_action_when_flags_unset` | 没有 action 标志时不渲染确认 UI | MockSessionState 无设置 | get("_preset_action") is None |
| ACT-10 | TestPresetNameSync | `test_select_preset_syncs_name_with_copy_suffix` | 选择预设时 new_preset_name 自动更新为 {name}_副本 | isolate_db | 期望值为 "MyConfig_副本" |
| ACT-11 | TestPresetNameSync | `test_select_none_clears_new_preset_name` | 选择"(不选择)"时清空名称 | MockSessionState | expected == "" |
| ACT-12 | TestPresetNameSync | `test_last_sel_name_tracks_change` | _last_sel_name 追踪变化，避免重复同步 | MockSessionState | new_preset_name=="PresetA_副本"，_last_sel_name 更新 |
| ACT-13 | TestPresetNameSync | `test_same_selection_no_re_sync` | 同一预设重复选择不覆盖手动编辑的名称 | _last_sel_name 已设置 | 手动编辑的名称被保留 |
| ACT-14 | TestPresetNameSync | `test_selection_changes_triggers_sync` | 预设选择变化时名称被重新同步 | 预设 _last_sel_name="OldPreset" | 切换到 "NewPreset_副本" |
| ACT-15 | TestPresetNameSync | `test_init_no_selection` | 初始无选择时 _last_sel_name=""，new_preset_name="" | MockSessionState 无预设 | _last_sel_name==""，new_preset_name=="" |
| ACT-16 | TestPresetMapLookup | `test_preset_map_builds_correctly` | preset_map 正确构建，label 含分类前缀 | isolate_db + 3 个预设 | 3 个 label 都在 map 中 |
| ACT-17 | TestPresetMapLookup | `test_same_name_different_category_unique_keys` | 同名不同分类有独立 label key | isolate_db | "[双滤波] Shared" 在 map 中 |
| ACT-18 | TestPresetMapLookup | `test_preset_map_lookup_returns_full_record` | preset_map 查询返回完整记录 | isolate_db | preset_id/name/description/category 全匹配 |
| ACT-19 | TestPresetMapLookup | `test_empty_presets_produces_empty_map` | 无预设时 preset_map 为空 | isolate_db | preset_map == {} |
| ACT-20 | TestPresetMapLookup | `test_preset_map_label_parsing_roundtrip` | selectbox label 可还原对应预设 | isolate_db | selected_preset["preset_id"] == pid |
| ACT-21 | TestPresetMapLookup | `test_deselect_label_not_in_map` | "(不选择)" label 不在 preset_map 中 | isolate_db | preset_map.get("(不选择)") is None |
| ACT-22 | TestCategoryPreservation | `test_overwrite_preserves_original_category` | 覆盖保存时 category 保持原值 | isolate_db | category 仍为 "双滤波" |
| ACT-23 | TestCategoryPreservation | `test_new_preset_uses_default_category` | 新建预设时默认 category 为 "通用" | isolate_db | category=="通用" |
| ACT-24 | TestCategoryPreservation | `test_new_preset_can_specify_category` | 新建可指定 category | isolate_db | category=="快速" |
| ACT-25 | TestCategoryPreservation | `test_category_persists_through_multiple_overwrites` | 多次覆盖后 category 不变 | isolate_db | category 始终 "双滤波"，gen==5 |
| ACT-26 | TestCategoryPreservation | `test_default_category_is_general` | save_preset 的 category 默认值为 "通用" | 无 DB | inspect 验证参数默认值 |
| ACT-27 | TestToastFeedback | `test_apply_preset_returns_params_for_feedback` | apply_preset 返回 dict 用于 toast 判断 | isolate_db | params 非 None，含 "v0_ke" |
| ACT-28 | TestToastFeedback | `test_save_preset_returns_valid_preset_id` | save_preset 返回有效 preset_id | isolate_db | pid > 0，get_preset 非 None |
| ACT-29 | TestToastFeedback | `test_rename_preset_returns_new_name` | rename_preset 成功返回新名称 | isolate_db | result=="AfterRename" |
| ACT-30 | TestToastFeedback | `test_delete_preset_returns_true_on_success` | delete_preset 成功返回 True | isolate_db | result is True |
| ACT-31 | TestToastFeedback | `test_delete_preset_returns_false_on_missing` | delete_preset 不存在返回 False | isolate_db | result is False |
| ACT-32 | TestToastFeedback | `test_simulate_apply_then_toast` | 模拟应用预设后的完整 feedback 路径 | isolate_db + MockSessionState | session_state 数据就绪 |
| ACT-33 | TestToastFeedback | `test_simulate_save_then_toast` | 模拟保存预设后的完整 feedback 路径 | isolate_db + MockSessionState | 保存后 overwrite_preset 标志被重置 |

### 2.4 test_filters.py — 滤波函数测试（32 tests）

| 用例ID | 测试类 | 测试方法 | 描述 | 前置条件 | 预期结果 |
|:--|:--|:--|:--|:--|:--|
| FLT-01 | TestConstantSignal | `test_sma_constant` | SMA 对常量信号返回 ≈1.0（内部区域） | constant_signal + time_index | np.allclose(interior, 1.0) |
| FLT-02 | TestConstantSignal | `test_ema_constant` | EMA 常量信号返回全 1.0 | constant_signal + time_index | np.allclose(result, 1.0) |
| FLT-03 | TestConstantSignal | `test_wma_constant` | WMA 常量信号返回 ≈1.0 | constant_signal + time_index | np.allclose(interior, 1.0) |
| FLT-04 | TestConstantSignal | `test_alma_constant` | ALMA 常量信号返回 ≈1.0 | constant_signal + time_index | np.allclose(interior, 1.0) |
| FLT-05 | TestConstantSignal | `test_savgol_constant` | Savitzky-Golay 常量信号返回全 1.0 | constant_signal + time_index | np.allclose(result, 1.0) |
| FLT-06 | TestConstantSignal | `test_kalman_constant` | Kalman 对常量信号收敛至 1.0 | constant_signal + time_index | np.allclose(result[-20:], 1.0, atol=1e-3) |
| FLT-07 | TestConstantSignal | `test_butterworth_constant` | Butterworth 常量信号返回 ≈1.0 | constant_signal + time_index | np.allclose(result[10:-10], 1.0) |
| FLT-08 | TestConstantSignal | `test_gaussian_constant` | Gaussian 常量信号返回全 1.0 | constant_signal + time_index | np.allclose(result, 1.0) |
| FLT-09 | TestConstantSignal | `test_median_constant` | Median 常量信号返回全 1.0 | constant_signal + time_index | np.allclose(result, 1.0) |
| FLT-10 | TestConstantSignal | `test_lowess_constant` | LOWESS 常量信号返回 ≈1.0 | constant_signal + time_index | np.allclose(result, 1.0, atol=1e-5) |
| FLT-11 | TestNoiseReduction | `test_savgol_denoise` | Savgol 降噪：MSE < 原始 MSE * 0.5 | noisy_sine + clean_sine | filt_mse < orig_mse * 0.5 |
| FLT-12 | TestNoiseReduction | `test_gaussian_denoise` | Gaussian 降噪：MSE < 原始 MSE | noisy_sine + clean_sine | filt_mse < orig_mse |
| FLT-13 | TestNoiseReduction | `test_kalman_denoise` | Kalman 降噪：MSE < 原始 MSE | noisy_sine + clean_sine | filt_mse < orig_mse |
| FLT-14 | TestNoiseReduction | `test_butterworth_denoise` | Butterworth 低通降噪：MSE < 原始 MSE | noisy_sine + clean_sine | filt_mse < orig_mse |
| FLT-15 | TestNoiseReduction | `test_wma_denoise` | WMA 降噪：MSE < 原始 MSE | noisy_sine + clean_sine | result_mse < orig_mse |
| FLT-16 | TestNoiseReduction | `test_butterworth_nyquist_clamp` | Butterworth cutoff>=Nyquist 自动钳制不崩溃 | noisy_sine + time_index | len(result) 匹配，无全 NaN |
| FLT-17 | TestNoiseReduction | `test_median_impulse_removal` | Median window=3 脉冲去除 | 含脉冲信号 | result[1] < 50 |
| FLT-18 | TestEdgeCases | `test_empty_array_raises` | 空数组引发 ValueError/IndexError（SMA/WMA/ALMA/Savgol/Kalman/Butterworth） | np.array([]) | raise (ValueError or IndexError) |
| FLT-19 | TestEdgeCases | `test_all_nan` | 全 NaN 输入产生全 NaN 输出（或至少不崩溃） | np.full(100, np.nan) | 各滤波器输出符合预期（NaN 或零填充） |
| FLT-20 | TestEdgeCases | `test_window_1` | window=1 输出等于输入 | constant_signal + time_index | 所有 4 个滤波器 np.allclose(result, 1.0) |
| FLT-21 | TestEdgeCases | `test_large_window_vs_signal_length` | window > len(signal) 不崩溃 | constant_signal + time_index | len(result) >= len(signal)，Savgol 抛 ValueError |
| FLT-22 | TestSavgolSpecial | `test_even_window_auto_odd` | 偶数 window 自动 +1 变奇数 | constant_signal + time_index | 不报错，结果全 1.0 |
| FLT-23 | TestSavgolSpecial | `test_order_gte_window_auto_reduce` | order >= window 自动降阶至 window-1 | constant_signal + time_index | 不报错，结果全 1.0 |
| FLT-24 | TestSavgolSpecial | `test_denoise_with_even_window` | 偶数 window 仍能正常降噪 | noisy_sine + clean_sine | filt_mse < orig_mse * 0.5 |
| FLT-25 | TestKalmanSpecial | `test_extreme_q_does_not_crash` | 极端 Q 值（1e-6 / 1e6）不崩溃 | noisy_sine + time_index | len(result) 匹配，无全 NaN |
| FLT-26 | TestKalmanSpecial | `test_extreme_r_does_not_crash` | 极端 R 值（1e-6 / 1e6）不崩溃 | noisy_sine + time_index | len(result) 匹配，无全 NaN |
| FLT-27 | TestKalmanSpecial | `test_constant_signal_convergence` | 常量信号 Kalman 收敛至真实值 | constant_signal + time_index | np.allclose(result[-30:], 1.0, atol=1e-2) |
| FLT-28 | TestComputeMetrics | `test_perfect_fit` | 完美拟合：SNR=99, MSE=0, RMSE=0, MAE=0 | 人工构造数据 | 全指标 == 预期值 |
| FLT-29 | TestComputeMetrics | `test_less_than_3_valid_points` | 少于 3 个有效点返回 NaN | 含 NaN 的短数组 | mse/rmse/mae/snr_imp 全 NaN，lag==0 |
| FLT-30 | TestComputeMetrics | `test_known_offset` | filtered = clean + 1.0 → MSE=1.0 | 人工构造数据 | mse==1.0, rmse≈1.0, mae==1.0 |
| FLT-31 | TestComputeMetrics | `test_snr_improvement_monotonic` | 好滤波 SNR > 差滤波 SNR，MSE 反向 | 真实滤波结果比较 | m_good["snr_imp"] > m_bad["snr_imp"] |
| FLT-32 | TestComputeMetrics | (继承) | 各种边界指标计算正确 | — | — |

### 2.5 test_signals.py — Schmitt 触发器 + 信号配对测试（17 tests）

| 用例ID | 测试类 | 测试方法 | 描述 | 前置条件 | 预期结果 |
|:--|:--|:--|:--|:--|:--|
| SIG-01 | TestSchmittTrigger | `test_deadzone_no_accel` | v>0, a=0 → 所有 sig=0（死区） | v=0.5, a=0 | np.all(sig==0) |
| SIG-02 | TestSchmittTrigger | `test_long_trigger` | a > eps and v > 0 → sig=+1 | v=0.5, a=0.3 | 至少一个 sig==1 |
| SIG-03 | TestSchmittTrigger | `test_short_trigger` | a < -eps and v < 0 → sig=-1 | v=-0.5, a=-0.3 | 至少一个 sig==-1 |
| SIG-04 | TestSchmittTrigger | `test_hysteresis` | 滞回：state 不在 a<0 时退出，仅在 a<-eps 时退出 | 构造 ramp-down 序列 | 温和负加速度区域 state=1 保持，大幅负加速度后退出 |
| SIG-05 | TestSchmittTrigger | `test_short_sequence_returns_none` | n < ewma_span 返回 None | v=ones(30), a=ones(30) | result is None |
| SIG-06 | TestSchmittTrigger | `test_nan_propagation` | NaN 输入不崩溃，状态前向传递 | a 含 NaN 段 | sig 非全 NaN |
| SIG-07 | TestSchmittTrigger | `test_constant_velocity` | v=0, a=0 → 所有 sig=0 | v=np.zeros(n), a=np.zeros(n) | np.all(sig==0) |
| SIG-08 | TestSchmittTrigger | `test_sigma_min_floor` | 极低波动被 sigma_min 地板保护 | v=0.001, a=0.02 | eps >= k_eps * sigma_min |
| SIG-09 | TestFindAllPairs | `test_empty_array` | 空数组 → [] | np.array([], dtype=int) | result == [] |
| SIG-10 | TestFindAllPairs | `test_all_zero` | 全零 → [] | np.zeros(50, dtype=int) | result == [] |
| SIG-11 | TestFindAllPairs | `test_single_segment_long` | 单 +1 段 → []（无配对） | [+1 段 20:60] | result == [] |
| SIG-12 | TestFindAllPairs | `test_alternating` | [+1, 0, -1, 0, +1] → 2 对 | [+1 10:30], [-1 50:70], [+1 80:90] | 2 对，(10,50) 和 (50,80) |
| SIG-13 | TestFindAllPairs | `test_adjacent_same_sign_merge` | [+1, 0, +1] → 合并为一段 → 无 pair | 间隔零的同号段 | result == [] |
| SIG-14 | TestFindAllPairs | `test_adjacent_same_sign_merge_with_opposite` | [+1, 0, +1, 0, -1] → 1 对 | 同号合并后与异号配对 | 1 对，(5,30) |
| SIG-15 | TestFindAllPairs | `test_short_sequence` | len < 3 → [] | [1,0] 和 [1] | result == [] |
| SIG-16 | TestFindAllPairs | `test_no_zero_separator` | [+1, +1, -1, -1] 相邻异号配对 | 无零间隔 | 1 对，(10,20) |

### 2.6 test_strategy.py — 策略 PnL + 抛物线拟合测试（17 tests）

| 用例ID | 测试类 | 测试方法 | 描述 | 前置条件 | 预期结果 |
|:--|:--|:--|:--|:--|:--|
| STG-01 | TestFitParabolic | `test_exact_quadratic` | 无噪二次函数 y=2x²+3x+1 精确拟合 | x=arange(20), y 无噪声 | a/b/c 误差 < 1e-10, y_fit == y |
| STG-02 | TestFitParabolic | `test_insufficient_points` | 少于 3 点返回 None | x,y 长度 2 | result is None |
| STG-03 | TestFitParabolic | `test_subsegment` | 子段拟合精度正确 | 50 点中取 [10:30] | a/b 误差 < 1e-10 |
| STG-04 | TestFitPhysicsParabola | `test_known_curvature` | 物理抛物线 y=y0+a(x-x0)² 精确拟合 | 顶点在 endpoint 的抛物线 | a 误差 < 1e-10, x0==x[-1] |
| STG-05 | TestFitPhysicsParabola | `test_insufficient_points` | 少于 3 点返回 None | x,y 长度 2 | result is None |
| STG-06 | TestFitPhysicsParabola | `test_collinear_data` | 共线数据 → a≈0 | 线性上升 + 端点 peak | a 有界 |
| STG-07 | TestFitPhysicsParabola | `test_subsegment_fit` | 子段物理抛物线拟合 | 50 点中取 [20:45] | a 误差 < 1e-10 |
| STG-08 | TestFitComparison | `test_extrapolation_difference` | 多项式 vs 物理抛物线外推差异 | 含噪抛物线 | max(abs(y_poly - y_phys)) > 1e-6 |
| STG-09 | TestComputeStrategyPnL | `test_empty_pairs` | 空 pairs → long_pnl=100, short_pnl=100, trades=[] | 无交易信号 | 初始 PnL 全 100， trades 空 |
| STG-10 | TestComputeStrategyPnL | `test_long_trade` | 已知做多交易 → long_pnl[-1] > 100 | _make_long_scenario | PnL 超过 100，至少 1 笔 long trade |
| STG-11 | TestComputeStrategyPnL | `test_short_trade` | 已知做空交易 → short_pnl[-1] > 100 | _make_short_scenario | PnL 超过 100，至少 1 笔 short trade |
| STG-12 | TestComputeStrategyPnL | `test_stop_loss_trigger` | 价格急跌触发止损 | 构造价格急跌序列 | 至少 1 笔 stop_loss trade，return_pct < 0 |
| STG-13 | TestComputeStrategyPnL | `test_independent_capital_pools` | 多空资金池独立 | 分别构造 long/short 场景 | long 场景 short_pnl==100，short 场景 long_pnl==100 |
| STG-14 | TestComputeStrategyPnL | `test_sequential_trades` | 连续多笔交易：long 后接 short | 构造先涨后跌序列 | trades >= 1，long/short PnL 不一致 |
| STG-15 | TestComputeStrategyPnL | `test_extreme_stop_loss` | 紧止损触发次数 >= 松止损触发次数 | 同一场景 0.5% vs 1000% sl | tight_sl >= loose_sl |
| STG-16 | TestAddPredictionTraces | `test_no_crash_poly2` | poly2 拟合结果 add_trace 不崩溃 | subplot_fig | 不抛异常 |
| STG-17 | TestAddPredictionTraces | `test_no_crash_physics_fit` | physics fit 结果 add_trace 不崩溃 | subplot_fig | 不抛异常 |
| STG-18 | TestAddPredictionTraces | `test_no_crash_no_extend` | n_extend=0 不崩溃 | subplot_fig | 不抛异常 |

### 2.7 test_boundary.py — 边界条件测试（22 tests）

| 用例ID | 测试类 | 测试方法 | 描述 | 前置条件 | 预期结果 |
|:--|:--|:--|:--|:--|:--|
| BDY-01 | TestComputeStrategyPnlBoundary | `test_filtered_all_nan` | filtered 全 NaN 不应崩溃 | np.full(50, np.nan) | long_pnl/short_pnl 长度匹配，trades==[] |
| BDY-02 | TestComputeStrategyPnlBoundary | `test_short_sequence` | t 长度 < 5 不崩溃 | 4 点数据 | long_pnl/short_pnl 长度 == 4 |
| BDY-03 | TestFindAllPairsBoundary | `test_zero_gap_merged` | 含 0 间隔同号段合并 | [+1,+1,0,0,+1,+1,-1,-1] | 配对 (0,6) |
| BDY-04 | TestFindAllPairsBoundary | `test_single_bar_signal` | 仅一个非零 bar → 无 pair | [0,1,0] | pairs == [] |
| BDY-05 | TestFindAllPairsBoundary | `test_frequent_alternation` | 每 2 bar 换方向 → 多对 | 20 点交替信号 | len(pairs) >= 1 |
| BDY-06 | TestFindAllPairsBoundary | `test_all_zero` | 全 0 → [] | np.zeros(10) | pairs == [] |
| BDY-07 | TestFindAllPairsBoundary | `test_short_signal` | len<3 → [] | [1,0], [1], [] | 全 [] |
| BDY-08 | TestFitBoundary | `test_fit_parabolic_short_segment` | fit 段 < 3 点 → None | 5 点取 [0:1] | result is None |
| BDY-09 | TestFitBoundary | `test_fit_parabolic_normal` | 正常 5 点返回 dict | x,y 5 点 | result 含 a/b/c |
| BDY-10 | TestFitBoundary | `test_fit_physics_denom_zero` | denom≈0 → None | x=[0,0,0] | result is None |
| BDY-11 | TestFitBoundary | `test_collinear_parabolic_fit_values` | 共线数据拟合 a≈0, b≈1, c≈1 | y = x + 1 | abs(a)<0.01, abs(b-1)<0.15, abs(c-1)<0.5 |
| BDY-12 | TestSchmittTriggerBoundary | `test_n_less_than_span` | n < ewma_span → None | 3 点 | result is None |
| BDY-13 | TestSchmittTriggerBoundary | `test_empty_v` | 空数组 → None | np.array([]) | result is None |
| BDY-14 | TestSchmittTriggerBoundary | `test_normal_output_shape` | 正常输入返回完整 dict | randn(100) | mu_v/sigma_v/eps/sig/dur 各 100 点 |
| BDY-15 | TestExportImportConfig | `test_export_dict_structure` | 导出 config dict 包含必需 key | 构造完整 config | 全局 + 4 视图必需 key 存在 |
| BDY-16 | TestExportImportConfig | `test_imp_backup_created_on_import` | _imp_ 备份在导入时被正确创建 | 模拟 session_state | 所有 config key 有 _imp_ 备份 |
| BDY-17 | TestEmptyDataDegradation | `test_schmitt_none_degrades_gracefully` | schmitt=None→all_pairs=[]→PnL 返回初始值 | 短序列模拟 | long_pnl==100，trades==[] |
| BDY-18 | TestNumericalStability | `test_extreme_prices` | 极端价格值不导致 NaN/inf | filtered 从 0.0001 开始 | long_pnl 无 NaN/inf, >100 |
| BDY-19 | TestNumericalStability | `test_negative_price` | 负价格被跳过不崩溃 | 含负值的 filtered | 不崩溃，trades 为 list |
| BDY-20 | TestAlignPnlBoundary | `test_higher_trades_empty` | 空交易列表无 markers | sample_dates 5 daily + 15 intraday | entry_markers/exit_markers == [] |
| BDY-21 | TestCrossTfHierarchy | `test_tf_hierarchy_chain` | 验证 8 周期映射链完整性 | — | 1m→5m→15m→60m→日→周→月→季→None |

### 2.8 test_alignment.py — 跨周期对齐测试（6 tests）

| 用例ID | 测试类 | 测试方法 | 描述 | 前置条件 | 预期结果 |
|:--|:--|:--|:--|:--|:--|
| ALN-01 | TestAlignPnlToCurrentTf | `test_tz_mixed_hkt_naive` | 时区混合对齐（tz-naive daily→tz-aware HKT intraday） | sample_dates | aligned 非全 NaN，entry/exit markers 正确 |
| ALN-02 | TestAlignPnlToCurrentTf | `test_no_time_overlap` | 无时间重叠（higher 在 current 之后） | 2024 daily vs 2019 intraday | aligned 全 NaN，markers 结构正确 |
| ALN-03 | TestAlignPnlToCurrentTf | `test_forward_fill` | 前向填充：high=[D1,D2,D3]，current=[每 bar 1h 间隔] | 5 点 current | expected = [100, 100, 105, 105, 110] |
| ALN-04 | TestAlignPnlToCurrentTf | `test_higher_dates_none` | higher_dates=None 返回全 NaN + 空 markers | sample_dates_intraday | aligned 全 NaN，markers 空 |
| ALN-05 | TestAlignPnlToCurrentTf | `test_marker_positions` | 交易 marker 在 current_dates 中正确位置 | 5 daily + 15 intraday (每 8h) | entry/exit markers 包含 return_pct 和 exit_reason |
| ALN-06 | TestAlignPnlToCurrentTf | `test_higher_shorter_than_current` | 高周期 PnL 短于当前周期不崩溃 | 5 daily + 15 intraday | 前向填充，非全 NaN |

### 2.9 test_alignment_subplot.py — 对齐子图测试（15 tests）

| 用例ID | 测试类 | 测试方法 | 描述 | 前置条件 | 预期结果 |
|:--|:--|:--|:--|:--|:--|
| ASP-01 | TestComputeHoldingMasks | `test_basic_long_mask` | 做多 entry→exit 产生正确 mask | entry(10)/exit(30) | bar 10→30 True，其他 False |
| ASP-02 | TestComputeHoldingMasks | `test_basic_short_mask` | 做空 entry→exit 产生正确 mask | entry(5)/exit(25) | short_mask 正确，long_mask 全 False |
| ASP-03 | TestComputeHoldingMasks | `test_both_long_and_short` | 多空都有互不干扰 | 各一段 long/short | 各自区域独立 |
| ASP-04 | TestComputeHoldingMasks | `test_entry_without_exit` | entry 无对应 exit 用到末尾 | entry(30) 无 exit | long_m[30:] 全 True |
| ASP-05 | TestComputeHoldingMasks | `test_empty_markers` | 空 markers → 全 False | — | long_m/short_m 全 False |
| ASP-06 | TestComputeHoldingMasks | `test_multiple_entries_same_type` | 同类型多个 entry 各自配对最近 exit | 2 段 long | 各段独立，中间为 False |
| ASP-07 | TestComputeHoldingMasks | `test_entry_before_first_exit_no_match` | entry 前于第一个 exit 但类型不匹配 | long entry + short exit | long 用至末尾 |
| ASP-08 | TestAlignmentSubplot | `test_no_hold_flat_at_100` | 无持仓时曲线维持 100 | 全 False mask | 不崩溃 |
| ASP-09 | TestAlignmentSubplot | `test_long_follows_pnl` | 做多期间跟随 long_pnl，无持仓持平 | 两段 long mask | long_filtered 各段正确累积 |
| ASP-10 | TestAlignmentSubplot | `test_short_follows_pnl` | 做空期间跟随 short_pnl | 全程 short mask | short_filtered 跟踪 short_pnl |
| ASP-11 | TestAlignmentSubplot | `test_no_crash_with_empty_data` | 空数据不崩溃 | — | fig.data >= 3 |
| ASP-12 | TestAlignmentSubplot | `test_trade_records_with_masks` | trade_records 传入同向段高亮 | long trade | fig.data > 2 |
| ASP-13 | TestAlignmentSubplot | `test_trade_records_skip_on_mask_mismatch` | mask 不匹配时跳过高亮 | mask 全 False | 不崩溃，fig.data >= 3 |
| ASP-14 | TestAlignmentSubplot | `test_entry_out_of_bounds` | entry_idx 超出 n → 跳过 | entry_idx=20, n=10 | 不 raise |

### 2.10 test_param_export_import.py — 参数导入导出测试（13 tests）

| 用例ID | 测试类 | 测试方法 | 描述 | 前置条件 | 预期结果 |
|:--|:--|:--|:--|:--|:--|
| PEI-01 | TestExportCompleteness | `test_all_per_view_keys_exported` | 每视图 15 个参数在 JSON 中 | 读 3690_HK.json | 60 个 v{N}_{key} 存在 |
| PEI-02 | TestExportCompleteness | `test_global_keys_exported` | 5 个全局参数在 JSON 中 | 读 3690_HK.json | market/ticker/global_f/global_dual/global_f2 存在 |
| PEI-03 | TestExportCompleteness | `test_filter_params_exported` | 滤波参数在导出中存在 | 读 3690_HK.json | 各视图至少 1 个滤波 key |
| PEI-04 | TestImpBackupCoverage | `test_all_config_keys_have_imp_backup` | 每个 JSON key 都有 _imp_ 备份 | 读 3690_HK.json | 无缺失 |
| PEI-05 | TestImpBackupCoverage | `test_imp_values_match_original` | _imp_ 备份值与原始一致 | 读 3690_HK.json | 无 mismatch |
| PEI-06 | TestParameterChangeDetection | `test_no_stale_keys_in_json` | JSON 中无多余未知参数 | 读 3690_HK.json | print 信息性输出 |
| PEI-07 | TestParameterChangeDetection | `test_all_json_per_view_keys_match_pattern` | per-view key 符合 v{N}_{name} 模式 | 读 3690_HK.json | print 信息性输出 |
| PEI-08 | TestExpandCollapseParameterRecovery | `test_all_params_recoverable_after_widget_loss` | widget key 丢失后可从 _imp_ 恢复 | 读 3690_HK.json | 无不可恢复参数 |
| PEI-09 | TestExpandCollapseParameterRecovery | `test_parameter_values_preserved_after_recovery` | 恢复后值与原始一致 | 读 3690_HK.json | 无 mismatch |
| PEI-10 | TestExpandCollapseParameterRecovery | `test_specific_critical_params_recoverable` | 关键参数（fm/next/cross_pnl/align/strat/ke/sm/ew）可恢复 | 读 3690_HK.json | 8 个关键参数全部恢复 |
| PEI-11 | TestExpandCollapseParameterRecovery | `test_filter_params_recoverable` | 中文滤波 key 可从 _imp_ 备份恢复 | 读 3690_HK.json | 滤波 key 全部恢复 |
| PEI-12 | TestImportIdempotency | `test_repeated_import_idempotent` | 重复导入相同文件 session_state 不变 | 读 3690_HK.json | 两次导入后 snapshot 一致 |
| PEI-13 | TestImportIdempotency | `test_partial_import_no_leftover` | 导入子集不留旧 key | 构造 subset config | 只有 subset key 存在 |

---

## 3. 运行测试的命令

```bash
# 运行全部测试（详细输出）
python -m pytest tests/ -v

# 运行全部测试（含覆盖率报告）
python -m pytest tests/ --cov=streamlit --cov-report=term-missing

# 运行特定模块
python -m pytest tests/test_config_db.py -v
python -m pytest tests/test_preset_ui.py -v
python -m pytest tests/test_preset_ui_actions.py -v

# 运行单个测试类
python -m pytest tests/test_config_db.py::TestPresetCRUD -v

# 运行单个测试方法
python -m pytest tests/test_config_db.py::TestPresetCRUD::test_save_new_preset -v

# 运行预设管理全部测试（3 个文件）
python -m pytest tests/test_config_db.py tests/test_preset_ui.py tests/test_preset_ui_actions.py -v

# 运行信号和策略相关测试
python -m pytest tests/test_filters.py tests/test_signals.py tests/test_strategy.py -v

# 运行边界和对齐测试
python -m pytest tests/test_boundary.py tests/test_alignment.py tests/test_alignment_subplot.py -v

# 仅运行非慢速测试（跳过 @pytest.mark.slow）
python -m pytest tests/ -v -m "not slow"

# 仅运行慢速测试
python -m pytest tests/ -v -m "slow"

# 带短回溯的快速运行
python -m pytest tests/ -v --tb=short

# 生成 HTML 覆盖率报告
python -m pytest tests/ --cov=streamlit --cov-report=html
open htmlcov/index.html
```

### 测试标记（pytest markers）

| 标记 | 用途 | 文件 |
|:--|:--|:--|
| `filter` | 滤波算法测试 | `test_filters.py` |
| `signal` | 信号生成测试 | `test_signals.py` |
| `strategy` | 策略计算测试 | `test_strategy.py` |
| `alignment` | 跨周期对齐测试 | `test_alignment.py` |
| `slow` | 边界条件（较慢） | `test_boundary.py` |

---

## 4. 本次修复的测试覆盖说明

以下 bug 修复均有对应的测试用例覆盖：

### 4.1 CRUD 操作返回值/异常处理验证

| 修复项 | 问题 | 覆盖测试 |
|:--|:--|:--|
| P0-1 | `import_json_files_as_presets` 返回 `int` 而非 `(int, list)` 元组 | CDB-44, CDB-45, CDB-46 — `TestImportJsonFilesReturnValue` |
| P1-1 | `delete_preset` 不返回操作结果 | CDB-37, CDB-38, CDB-39 — `TestDeletePresetReturnValue` |
| P1-2 | `rename_preset` 重名时抛异常而非安全返回 | CDB-12, CDB-40 — `rename_to_existing_name_returns_none` |
| P1-5 | `save_preset` 未校验 JSON 有效性，非法 JSON 写入 DB | CDB-14, CDB-33, CDB-34 — `TestSavePresetValidation` |
| P1-6 | `rename_preset` 空名称未校验 | CDB-41 — `rename_to_empty_string_returns_none` |

### 4.2 popover -> session_state 标志状态机

| 修复项 | 问题 | 覆盖测试 |
|:--|:--|:--|
| — | 弃用 popover，改用 `_preset_action` / `_preset_action_id` session_state 标志驱动操作 | ACT-01~ACT-09 — `TestPresetActionFlags`：更新/重命名/删除按钮设置标志，操作完成后清除，取消时清除，目标不存在时清除 |

### 4.3 text_input 自动同步机制

| 修复项 | 问题 | 覆盖测试 |
|:--|:--|:--|
| — | 选择预设时 `new_preset_name` 自动更新 + `_last_sel_name` 追踪避免覆盖手动编辑 | ACT-10~ACT-15 — `TestPresetNameSync`：选择时同步 `_副本` 后缀，取消选择清空，同一预设不重复同步，选择变化触发同步，初始状态正确 |

### 4.4 category 保留

| 修复项 | 问题 | 覆盖测试 |
|:--|:--|:--|
| — | 覆盖保存时原预设分类被默认值"通用"覆盖 | ACT-22, UI-50~UI-52 — `TestCategoryPreservation` + `TestSavePresetWithCategory`：覆盖时保留原 category，新建默认"通用"，覆盖传不同分类时更新 |

### 4.5 参数收集完整性

| 修复项 | 问题 | 覆盖测试 |
|:--|:--|:--|
| — | `collect_current_params` 遗漏新增参数 key | CDB-28~CDB-32, UI-53~UI-56 — `TestCollectCurrentParams` + `TestCollectParamsCompleteness`：5 全局 key + 60 视图 key + 10 中文 filter key 全部覆盖，无关 key 被过滤 |

### 4.6 preset_map 查表

| 修复项 | 问题 | 覆盖测试 |
|:--|:--|:--|
| — | 用 selectbox label 查预设时未正确区分同名不同分类的预设 | ACT-16~ACT-21, UI-57~UI-60 — `TestPresetMapLookup`（两个版本）：label 格式含分类前缀，同名不同分类独立 key，查询返回完整记录，label→preset roundtrip 正确 |

### 4.7 FK ON DELETE SET NULL

| 修复项 | 问题 | 覆盖测试 |
|:--|:--|:--|
| P0-2 | 删除被 ticker 引用的预设时级联删除或报错，而非 SET NULL | CDB-39 — `test_delete_referenced_preset_sets_null`：删除后 ticker 的 preset_id 变为 None，ticker 记录保留 |

---

## 5. 已知问题和限制

### 5.1 Streamlit mock 的限制

- **`conftest.py`** 将 `streamlit` 替换为 `MagicMock`，因此任何依赖 Streamlit 运行时行为的功能**无法**在单元测试中真实验证：
  - `st.button()` 的点击回调
  - `st.selectbox()` / `st.text_input()` 的 widget 交互
  - `st.toast()` / `st.success()` / `st.error()` 的实际 UI 反馈
  - `st.rerun()` 触发的页面重渲染
  - `st.session_state` 在真实 Streamlit 中的生命周期（如同步回写）
- **变通方案**：行为测试（`test_preset_ui_actions.py`）使用 `dict`-like `MockSessionState` 模拟标志状态机，验证逻辑分支而非实际 UI 渲染。

### 5.2 无法测试真实 UI 渲染

- Plotly 图表（`plotly.graph_objects.Figure`）的视觉效果（颜色、标注位置、缩放等）无法在测试中验证，只能验证 trace 数量和数据值的正确性（参见 `test_alignment_subplot.py` 中的 `TestAlignmentSubplot`）。
- Streamlit 的 layout（`st.columns`、`st.expander`、`st.sidebar`）完全不在测试范围内。

### 5.3 数据库测试隔离

- 所有 `config_db` 相关测试使用 `tempfile.TemporaryDirectory` + `monkeypatch.setattr` 替换 `_CONFIG_DB_PATH` 和 `_CONFIG_DIR`，确保测试间完全隔离。
- 但由于 `config_db` 模块内部使用模块级连接缓存，`temp_config_db` fixture 通过 `import config_db` 重新加载模块来规避路径绑定问题。

### 5.4 无真实网络/数据依赖

- 所有信号和策略测试使用 `numpy` 生成的合成数据（`constant_signal`、`noisy_sine`、`random_walk` 等），不依赖真实市场数据或网络 API。
- 参数导入导出测试（`test_param_export_import.py`）读取 `config/3690_HK.json` 作为参考文件，**不访问网络**。如果该文件被移动或删除，相关测试会失败。

### 5.5 平台兼容性

- `tempfile.TemporaryDirectory` 的自动清理行为依赖 Python 版本和操作系统。在 macOS/Linux 上工作正常，Windows 上可能因文件锁定导致清理失败（但测试通过 `shutil.rmtree` 显式清理）。

### 5.6 测试标记使用

- `test_boundary.py` 的测试方法标记为 `@pytest.mark.slow`，默认不运行。如果运行全部测试时需要包含它们，需显式指定 `-m "slow"` 或不使用标记过滤。

### 5.7 未覆盖的测试场景

- Streamlit app 的主流程（`main()` 函数）未在单元测试中覆盖 — 所有测试针对纯函数和 DB 层。
- 并发/多用户场景下的 SQLite 锁竞争未测试。
- 异常 DB 文件（损坏的 SQLite、权限不足）的降级行为未测试。
- `config_db.get_connection()` 的线程安全问题未测试。

---

## 6. 测试统计概览

| 文件 | 测试类数 | 测试方法数 | 标记 |
|:--|--:|--:|:--|
| `test_config_db.py` | 10 | 46 | — |
| `test_preset_ui.py` | 16 | 60 | — |
| `test_preset_ui_actions.py` | 5 | 33 | — |
| `test_filters.py` | 6 | 32 | `filter` |
| `test_signals.py` | 2 | 16 | `signal` |
| `test_strategy.py` | 5 | 18 | `strategy` |
| `test_boundary.py` | 9 | 21 | `slow` |
| `test_alignment.py` | 1 | 6 | `alignment` |
| `test_alignment_subplot.py` | 2 | 14 | — |
| `test_param_export_import.py` | 5 | 13 | — |
| **总计** | **61** | **259** | — |
