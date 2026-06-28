# 多周期股票滤波分析工具 — 测试用例文档

> 自动生成于 2026-06-28 | 测试总数: 600（pytest 收集，不含 test_streamlit_app.py 的 25 个 mock 测试） | Python 3.12 + pytest

## 文档说明

**用例格式**: 每个用例一行表格行，包含 **用例ID** | **测试目的** | **前置条件** | **操作步骤** | **通过标准** 五列。

**用例ID** 编码规则: `TC-<模块缩写>-<序号>`，模块缩写为文件名去掉 `test_` 前缀和 `.py` 后缀。

---

## 1. 数据持久层测试

### 1.1 配置数据库 (`test_config_db.py`) — 58 用例

#### 数据库初始化 (`TestInitConfigTables`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CONFIG-001 | 验证 init_config_tables 创建预设表和配置表 | temp_config_db fixture | 调用 init_config_tables | 表已创建，再次查询不报错 |
| TC-CONFIG-002 | 验证 init_config_tables 幂等性 | temp_config_db fixture | 连续调用两次 init_config_tables | 第二次调用不抛出异常 |

#### 预设 CRUD (`TestPresetCRUD`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CONFIG-003 | 新建预设并保存 | temp_config_db + sample_params_json | 调用 save_preset 保存新预设 | 返回有效的 preset_id（整数） |
| TC-CONFIG-004 | 更新已存在的预设（按 name 去重） | temp_config_db，已存在同名预设 | 再次调用 save_preset 同名预设 | 更新成功，不创建新记录 |
| TC-CONFIG-005 | 列表所有预设 | temp_config_db，已保存多个预设 | 调用 list_presets() | 返回包含所有预设的列表 |
| TC-CONFIG-006 | 按分类列表预设 | temp_config_db，已保存不同分类的预设 | 调用 list_presets(category="xxx") | 只返回指定分类的预设 |
| TC-CONFIG-007 | 按 ID 获取预设详情 | temp_config_db + sample_params_json | 调用 get_preset(preset_id) | 返回完整预设记录 |
| TC-CONFIG-008 | 按名称获取预设 | temp_config_db + sample_params_json | 调用 get_preset_by_name(name) | 返回正确的预设记录 |
| TC-CONFIG-009 | 获取不存在的预设返回 None | temp_config_db | 调用 get_preset(不存在的id) | 返回 None |
| TC-CONFIG-010 | 删除预设 | temp_config_db + sample_params_json | 调用 delete_preset(preset_id) | 预设被移除，再次查询返回 None |
| TC-CONFIG-011 | 重命名预设 | temp_config_db + sample_params_json | 调用 rename_preset(id, new_name) | 名称更新为新名称 |
| TC-CONFIG-012 | 重命名为已存在的名称返回 None | temp_config_db，存在两个预设 | 将预设A重命名为预设B的名称 | 返回 None，名称不变 |
| TC-CONFIG-013 | 应用预设并填充 session_state | temp_config_db + sample_params_json | 调用 apply_preset(preset_id) | 返回参数，session_state 被填充 |
| TC-CONFIG-014 | 应用预设时 JSON 无效返回 None | temp_config_db，保存含损坏 JSON 的预设 | 调用 apply_preset(preset_id) | 返回 None |
| TC-CONFIG-015 | 应用不存在的预设返回 None | temp_config_db | 调用 apply_preset(不存在的id) | 返回 None |

#### Ticker 配置 (`TestTickerConfig`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CONFIG-015 | 保存并加载 ticker 配置 | temp_config_db | save_ticker_config → load_ticker_config | 加载的数据与保存的一致 |
| TC-CONFIG-016 | 保存 ticker 配置并关联预设 | temp_config_db，已有预设 | save_ticker_config 指定 preset_id | 配置正确关联到预设 |
| TC-CONFIG-017 | 加载不存在的 ticker 配置 | temp_config_db | load_ticker_config(不存在的ticker) | 返回空/默认配置 |

#### 历史记录 (`TestHistory`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CONFIG-018 | 记录并获取 ticker 操作历史 | temp_config_db | record_history → get_history | 返回的历史包含刚记录的条目 |
| TC-CONFIG-019 | 历史记录关联 preset_id | temp_config_db | 记录时传入 preset_id，查询历史 | 历史条目包含正确的 preset_id |
| TC-CONFIG-020 | 新 ticker 历史为空 | temp_config_db，无历史数据 | get_history(新ticker) | 返回空列表 |
| TC-CONFIG-021 | 历史记录默认限制条数 | temp_config_db，写入多条历史 | get_history 不传 limit | 返回默认条数限制内的记录 |

#### 导入 JSON 文件 (`TestImportJSONFiles`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CONFIG-022 | 导入 JSON 文件创建预设 | temp_config_db，准备 JSON 文件目录 | 调用 import_json_files | 预设被成功创建 |
| TC-CONFIG-023 | 导入时跳过已存在的预设 | temp_config_db，已有同名预设 | 再次导入同名 JSON | 不会创建重复预设 |
| TC-CONFIG-024 | 强制覆盖导入 | temp_config_db，已有同名预设 | 使用 force=True 导入 | 已有预设被覆盖更新 |
| TC-CONFIG-025 | 导入空目录 | temp_config_db，空目录 | 调用 import_json_files | 不抛出异常，返回空结果 |
| TC-CONFIG-026 | 跳过格式错误的 JSON 文件 | temp_config_db，含错误 JSON 文件 | 调用 import_json_files | 跳过错误文件，继续处理其他文件 |

#### 参数采集 (`TestCollectCurrentParams`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CONFIG-027 | 采集全局键 | temp_config_db，session_state 含全局配置 | 调用 collect_current_params | 全局键被正确采集 |
| TC-CONFIG-028 | 采集视图参数 | temp_config_db，含视图配置 | 调用 collect_current_params | 视图参数被包含 |
| TC-CONFIG-029 | 采集滤波参数 | temp_config_db，含滤波配置 | 调用 collect_current_params | 滤波参数被包含 |
| TC-CONFIG-030 | 忽略不相关的键 | temp_config_db，含无关键 | 调用 collect_current_params | 无关键不在结果中 |
| TC-CONFIG-031 | 空 session_state | temp_config_db，session_state 为空 | 调用 collect_current_params | 返回空字典或默认值 |

#### 保存预设验证 (`TestSavePresetValidation`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CONFIG-032 | 空名称抛出 ValueError | temp_config_db | save_preset(name="") | 抛出 ValueError |
| TC-CONFIG-033 | 无效 JSON 参数抛出 ValueError | temp_config_db | save_preset(params=无效JSON) | 抛出 ValueError |
| TC-CONFIG-034 | 合法保存返回整数 | temp_config_db + sample_params_json | save_preset(name, params) | 返回值是 int 类型 |
| TC-CONFIG-035 | 覆盖保存返回相同 preset_id | temp_config_db，已有预设 | 再次保存同名预设 | 返回相同的 preset_id |

#### 删除预设返回值 (`TestDeletePresetReturnValue`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CONFIG-036 | 删除存在的预设返回 True | temp_config_db + sample_params_json | delete_preset(存在的id) | 返回 True |
| TC-CONFIG-037 | 删除不存在的预设返回 False | temp_config_db | delete_preset(不存在的id) | 返回 False |
| TC-CONFIG-038 | 删除被引用的预设时外键置 NULL | temp_config_db，预设已被 ticker 引用 | delete_preset(被引用的id) | ticker 配置中 preset_id 变 NULL |
| TC-CONFIG-039 | 删除操作 rowcount 为 0 返回 False | temp_config_db | 模拟并发删除场景 | 返回 False |

#### 重命名预设验证 (`TestRenamePresetValidation`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CONFIG-040 | 重命名为已存在的名称返回 None | temp_config_db，存在两个预设 | rename_preset(预设A, 预设B的名称) | 返回 None |
| TC-CONFIG-041 | 重命名为空字符串返回 None | temp_config_db + sample_params_json | rename_preset(id, "") | 返回 None |
| TC-CONFIG-042 | 重命名不存在的预设返回 None | temp_config_db | rename_preset(不存在的id, "new") | 返回 None |
| TC-CONFIG-043 | 重命名成功返回新名称 | temp_config_db + sample_params_json | rename_preset(id, "新名称") | 返回 "新名称" |

#### 导入返回值 (`TestImportJsonFilesReturnValue`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CONFIG-044 | 返回 (count, errors) 元组 | temp_config_db，准备 JSON 文件 | import_json_files | 返回二元组 (int, list) |
| TC-CONFIG-045 | 无效 JSON 记录在 errors 中而非抛出 | temp_config_db，含无效 JSON | import_json_files | errors 列表包含该文件信息 |
| TC-CONFIG-046 | 混合有效和无效文件正确处理 | temp_config_db，混合目录 | import_json_files | count > 0 且 errors 含无效文件 |

#### 异常处理 (`TestGetConnException`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CONFIG-047 | 数据库异常时触发回滚 | temp_config_db，模拟连接异常 | 执行会失败的操作 | 数据库状态未被部分修改 |

#### Ticker 配置边界 (`TestTickerConfigEdgeCases`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CONFIG-048 | 保存时关联不存在的预设则置 NULL | temp_config_db | save_ticker_config(preset_id=不存在的) | preset_id 被设置为 NULL |

#### 应用预设边界 (`TestApplyPresetEdgeCases`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CONFIG-049 | 应用含损坏 JSON 的预设返回 None | temp_config_db，预设含损坏 JSON | apply_preset(id) | 返回 None |

#### 导入边界 (`TestImportJsonFilesEdgeCases`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CONFIG-050 | 导入时数据库保存失败被报告 | temp_config_db，模拟保存失败 | import_json_files | errors 列表包含失败信息 |
| TC-CONFIG-051 | 导入空文件 JSON 被正确处理 | temp_config_db，空 JSON 文件 | import_json_files | 跳过或记录错误 |

#### 删除预设与 JSON 文件 (`TestDeletePresetWithJsonFile`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CONFIG-052 | 删除预设时移除关联 JSON 文件 | temp_config_db，预设有关联 JSON 文件 | delete_preset(id) | JSON 文件被删除 |
| TC-CONFIG-053 | 删除无 JSON 文件的预设 | temp_config_db，预设无 JSON 文件 | delete_preset(id) | 不报错 |

#### 数据库迁移 (`TestInitConfigTablesMigration`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CONFIG-054 | 旧 schema 外键迁移 | tmp_path，创建旧版数据库 | init_config_tables | 外键约束被正确添加 |
| TC-CONFIG-055 | 强制路径迁移 | tmp_path | 使用 force_path 迁移 | 迁移成功执行 |
| TC-CONFIG-056 | 无需迁移时不执行 | temp_config_db，已是最新 schema | init_config_tables | 不执行迁移，不报错 |

#### 主块执行 (`TestMainBlock`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CONFIG-057 | 主块执行测试 | tmp_path | 以脚本方式运行 config_db | 正常执行不报错 |

---

### 1.2 行情数据库 (`test_db.py`) — 57 用例

#### 数据库初始化 (`TestInitDb`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-DB-001 | init_db 创建所需表 | tmp_path | 调用 init_db | kline 表存在 |
| TC-DB-002 | init_db 幂等性 | tmp_path | 连续两次调用 init_db | 第二次不报错 |

#### K线 Upsert (`TestUpsertKline`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-DB-003 | Upsert 空 DataFrame | db_target | upsert_kline(空df) | 不报错 |
| TC-DB-004 | 首次插入数据 | db_target | upsert_kline(有效df) | 数据入库，查询可获取 |
| TC-DB-005 | 追加新 K 线 | db_target，已有数据 | upsert_kline(新日期df) | 新数据追加，旧数据不变 |
| TC-DB-006 | 修正历史数据 | db_target，已有数据 | upsert_kline(重叠日期df) | 重叠部分更新，其他不变 |
| TC-DB-007 | 多时间帧数据隔离 | db_target | upsert 不同 timeframe 的数据 | 各时间帧数据互不影响 |
| TC-DB-008 | 处理 NaN 值 | db_target | upsert_kline(含NaN的df) | NaN 正确处理入库 |

#### K线查询 (`TestQueryKline`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-DB-009 | 查询空表 | db_target | query_kline | 返回空 DataFrame |
| TC-DB-010 | 查最近 N 条 K 线 | populate_kline | query_kline(n=N) | 返回 N 条记录 |
| TC-DB-011 | 按日偏移查询 | populate_kline | query_kline(day_offset=X) | 返回偏移后的数据 |
| TC-DB-012 | 请求条数超实际数据 | populate_kline | query_kline(n=超大值) | 返回所有可用数据 |
| TC-DB-013 | 查询不存在的 ticker | populate_kline | query_kline(ticker="不存在") | 返回空 DataFrame |

#### 辅助函数 (`TestHelpers`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-DB-014 | 获取日期范围 | populate_kline | get_date_range | 返回正确的最小/最大日期 |
| TC-DB-015 | 空表获取日期范围 | db_target | get_date_range | 返回 (None, None) |
| TC-DB-016 | has_data 返回 True | populate_kline | has_data | 返回 True |
| TC-DB-017 | has_data 返回 False | db_target | has_data | 返回 False |

#### 数据健康检查 (`TestCheckDataHealth`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-DB-018 | 空数据库健康检查 | db_target | check_data_health | 返回提示无数据的报告 |
| TC-DB-019 | 正常数据健康检查 | populate_kline | check_data_health | 报告数据正常 |
| TC-DB-020 | 含 NULL 值数据健康检查 | db_target，写入含 NULL 的数据 | check_data_health | 正确标识 NULL 问题 |
| TC-DB-021 | 数据过期检测 | db_target，写入旧日期的数据 | check_data_health | 报告数据过期 |
| TC-DB-022 | 按时间帧检测问题的 bug 修复 | db_target，部分时间帧有问题 | check_data_health | 每个时间帧独立报告 |
| TC-DB-023 | 数据间隔检测 | db_target，写入有间隔的数据 | check_data_health | 检测到数据间隔 |
| TC-DB-024 | 零行时间帧检测 | db_target，某时间帧无数据 | check_data_health | 报告该时间帧无数据 |

#### 数据库验证 (`TestValidateDb`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-DB-025 | 验证有效的数据库 | db_target | validate_db | 返回 True |
| TC-DB-026 | 验证缺失表的数据库 | db_target，删除表 | validate_db | 返回 False |
| TC-DB-027 | 验证损坏的数据库文件 | db_target，损坏的文件 | validate_db | 返回 False 或抛出异常 |
| TC-DB-028 | 验证不存在的路径 | tmp_path | validate_db(不存在的路径) | 返回 False |

#### 数据对比 (`TestCompareWithDb`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-DB-029 | 对比完全相同的数据 | populate_kline | compare_with_db(相同数据) | 报告无差异 |
| TC-DB-030 | 对比仅新数据不同 | populate_kline | compare_with_db(含新数据) | 正确标识新增部分 |
| TC-DB-031 | 对比冲突数据 | db_target，人工制造冲突 | compare_with_db(冲突数据) | 正确标识冲突 |
| TC-DB-032 | 对比空数据库 | db_target | compare_with_db(任意数据) | 所有数据被标记为新 |

#### 强制更新 (`TestForceUpdateKline`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-DB-033 | 强制更新重叠数据 | db_target，已有数据 | force_update_kline(重叠df) | 重叠部分被完全替换 |
| TC-DB-034 | 强制更新纯新数据 | db_target | force_update_kline(全新df) | 新数据全部入库 |

#### 快照备份 (`TestSnapshotBackup`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-DB-035 | 创建快照 | populate_kline | create_snapshot | 快照文件被创建 |
| TC-DB-036 | 列出快照 | populate_kline，已有快照 | list_snapshots | 返回快照列表 |
| TC-DB-037 | 空快照列表 | db_target | list_snapshots | 返回空列表 |
| TC-DB-038 | 恢复快照 | db_target，有快照文件 | restore_snapshot | 数据恢复到快照状态 |
| TC-DB-039 | 清理快照时 OS 错误被忽略 | db_target | prune_snapshots | 不因 OS 错误而崩溃 |
| TC-DB-040 | 清理过期快照 | populate_kline，多个快照 | prune_snapshots | 超出保留数的快照被删除 |

#### WAL Checkpoint (`TestCheckpointWal`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-DB-041 | WAL checkpoint 执行 | populate_kline | checkpoint_wal | 正常执行不报错 |
| TC-DB-042 | 空数据库 WAL checkpoint | db_target | checkpoint_wal | 正常执行不报错 |

#### 数据库大小 (`TestGetDbSize`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-DB-043 | 非空数据库大小 | populate_kline | get_db_size | 返回值 > 0 |
| TC-DB-044 | 不存在的数据库文件 | tmp_path | get_db_size(不存在的路径) | 返回 0 或适当值 |

#### 缓存清理 (`TestClearDisplayCache`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-DB-045 | 清理显示缓存 | tmp_path，有缓存文件 | clear_display_cache | 缓存文件被删除 |
| TC-DB-046 | 无缓存目录时清理 | tmp_path | clear_display_cache | 不报错 |
| TC-DB-047 | 清理时的错误处理 | tmp_path，模拟权限错误 | clear_display_cache | 异常被妥善处理 |

#### 边界情况 (`TestEdgeCases`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-DB-048 | Upsert 缺少 volume 列 | db_target | upsert_kline(缺volume的df) | 不崩溃，正确处理 |
| TC-DB-049 | Upsert 使用真实索引 | db_target | upsert_kline(含真实日期索引) | 正确入库 |
| TC-DB-050 | 查询日偏移超出数据范围 | populate_kline | query_kline(极大偏移) | 返回空或适当值 |
| TC-DB-051 | 与空 yfinance 数据对比 | db_target | compare_with_db(空yf数据) | 不报错 |
| TC-DB-052 | 空数据强制更新 | db_target | force_update(空df) | 不报错 |
| TC-DB-053 | ticker 无数据时的健康检查 | db_target | check_data_health(ticker=无数据) | 正确报告无数据 |
| TC-DB-054 | 恢复快照时处理 WAL/SHM 文件 | tmp_path，有 WAL/SHM | restore_snapshot | 正确恢复并处理 WAL |
| TC-DB-055 | 快照目录按需创建 | tmp_path | 触发快照创建 | 目录自动创建 |

#### 并发访问 (`TestConcurrentAccess`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-DB-056 | 多连接读写 | db_target | 多连接同时读写 | 不产生数据库锁错误 |
| TC-DB-057 | WAL 模式已启用 | db_target | 检查 PRAGMA journal_mode | 返回 "wal" |

---

### 1.3 状态管理 (`test_state.py`) — 59 用例

#### 默认值初始化 (`TestAppStateInitDefaults`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-STATE-001 | 跳过 None 默认值 | real_session_state | init_defaults | None 默认值的键不被写入 session_state |
| TC-STATE-002 | 写入非 None 默认值 | real_session_state | init_defaults | 非 None 默认值的键被写入 |
| TC-STATE-003 | 不覆盖已有键 | real_session_state，已有键 | init_defaults | 已有键值不变 |

#### has 判断 (`TestAppStateHas`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-STATE-004 | 缺失键返回 False | real_session_state | state.has("missing_key") | 返回 False |
| TC-STATE-005 | 存在键返回 True | real_session_state，已设置键 | state.has("existing_key") | 返回 True |
| TC-STATE-006 | 回退到 imp 备份键 | real_session_state，仅 imp 键存在 | state.has("key") | 返回 True |
| TC-STATE-007 | None 默认键不在 session 中 | real_session_state | state.has("none_default_key") | 返回 False |
| TC-STATE-008 | 设置后 has 返回 True | real_session_state | set → has | 返回 True |

#### get 获取 (`TestAppStateGet`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-STATE-009 | 获取存在键的值 | real_session_state，已设置键 | state.get | 返回正确值 |
| TC-STATE-010 | 获取缺失键返回 None | real_session_state | state.get("missing") | 返回 None |
| TC-STATE-011 | 获取缺失键返回指定默认值 | real_session_state | state.get("missing", default=X) | 返回 X |
| TC-STATE-012 | 回退到 imp 备份 | real_session_state，仅 imp 键存在 | state.get | 返回 imp 备份的值 |
| TC-STATE-013 | 主键优先于 imp 备份 | real_session_state，两键都存在 | state.get | 返回主键值 |

#### set 设置 (`TestAppStateSet`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-STATE-014 | set 写入主键 | real_session_state | state.set(key, value) | 主键被写入 |
| TC-STATE-015 | set 同时写入 imp 备份 | real_session_state | state.set(key, value) | imp 备份键也被写入 |
| TC-STATE-016 | imp 禁用时跳过备份 | real_session_state，imp 禁用 | state.set(key, value) | 仅主键被写入 |

#### set_many 批量设置 (`TestAppStateSetMany`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-STATE-017 | set_many 写入所有键 | real_session_state | state.set_many({k1:v1, k2:v2}) | 所有键值都被写入 |
| TC-STATE-018 | set_many 写入所有 imp 备份 | real_session_state | state.set_many(dict) | imp 键同步更新 |

#### pop 移除 (`TestAppStatePop`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-STATE-019 | pop 移除键 | real_session_state，已设置键 | state.pop | 键被移除 |
| TC-STATE-020 | pop 返回值 | real_session_state，已设置键 | state.pop | 返回被移除的值 |
| TC-STATE-021 | pop 缺失键返回 None | real_session_state | state.pop("missing") | 返回 None |
| TC-STATE-022 | pop 缺失键返回指定默认值 | real_session_state | state.pop("missing", default=X) | 返回 X |
| TC-STATE-023 | pop 同时移除 imp 备份 | real_session_state，imp 键存在 | state.pop | imp 键也被移除 |

#### get_global 全局值 (`TestAppStateGetGlobal`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-STATE-024 | 返回全局默认值 | real_session_state，无对应键 | state.get_global | 返回全局默认值 |
| TC-STATE-025 | 全局回退到显式默认 | real_session_state | state.get_global(..., default=X) | 返回 X |

#### get_view_key (`TestAppStateGetViewKey`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-STATE-026 | 视图键格式正确 | real_session_state | state.get_view_key | 返回正确格式的视图键 |

#### 延迟应用参数生命周期 (`TestPendingApplyParamsLifecycle`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-STATE-027 | set 后 pop 返回正确值 | real_session_state | set → pop | pop 返回 set 的值 |
| TC-STATE-028 | pop 后键被清除 | real_session_state | set → pop | 键在 session_state 中不存在 |
| TC-STATE-029 | 双重 pop 返回 None | real_session_state | set → pop → pop | 第二次 pop 返回 None |
| TC-STATE-030 | session 中 None 不可迭代 | real_session_state，键值为 None | 遍历 session_state | 不因 None 值而崩溃 |
| TC-STATE-031 | 完整 apply 生命周期 | real_session_state | set_many → pop 应用 | 所有参数正确应用 |

#### 视图状态 (`TestViewState`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-STATE-032 | init 前缀正确 | 无 | ViewState("prefix") | view_key 格式正确 |
| TC-STATE-033 | init v3 前缀 | 无 | ViewState("prefix_v3") | 生成正确前缀 |
| TC-STATE-034 | 无 session_state 时 get | real_session_state | view.get("suffix") | 返回适当默认值 |
| TC-STATE-035 | 从 session_state get | real_session_state，已设置键 | view.get("suffix") | 返回正确值 |
| TC-STATE-036 | get 回退到 imp | real_session_state，仅 imp 存在 | view.get("suffix") | 返回 imp 值 |
| TC-STATE-037 | 主键覆盖 imp | real_session_state，两键都存在 | view.get("suffix") | 返回主键值 |
| TC-STATE-038 | set 写入 session_state | real_session_state | view.set("suffix", value) | 键值被写入 |
| TC-STATE-039 | set 写入 imp 备份 | real_session_state | view.set("suffix", value) | imp 键也被写入 |
| TC-STATE-040 | set_many 批量设置 | real_session_state | view.set_many(dict) | 所有键值被写入 |
| TC-STATE-041 | get_expanded 默认 False | real_session_state | view.get_expanded | 返回 False |
| TC-STATE-042 | get_expanded 返回 True | real_session_state，已设置为 True | view.get_expanded | 返回 True |
| TC-STATE-043 | toggle_expanded 切换 | real_session_state | view.toggle_expanded | expanded 状态取反 |
| TC-STATE-044 | 未知后缀 get 返回 None | real_session_state | view.get("unknown") | 返回 None |
| TC-STATE-045 | 未知后缀 get 返回显式默认 | real_session_state | view.get("unknown", default=X) | 返回 X |
| TC-STATE-046 | 不同视图独立 | real_session_state | 两个 ViewState 对象 | 互不影响 |
| TC-STATE-047 | key 方法返回值 | 无 | view.key("suffix") | 返回完整键名 |
| TC-STATE-048 | load 返回 ViewState 实例 | real_session_state | state.view("prefix") | 返回 ViewState 对象 |

#### 构建配置 (`TestViewStateBuildCfg`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-STATE-049 | 全默认构建配置 | real_session_state | view.build_cfg | 返回默认配置字典 |
| TC-STATE-050 | 带额外参数构建配置 | real_session_state | view.build_cfg(extra=...) | extra 键被加入配置 |
| TC-STATE-051 | extra 覆盖默认值 | real_session_state | view.build_cfg(extra会覆盖默认) | extra 值生效 |
| TC-STATE-052 | 从 session_state 读取 | real_session_state，已设置视图键 | view.build_cfg | 返回 session 中的值 |
| TC-STATE-053 | 使用 get 的默认值处理未设键 | real_session_state | view.build_cfg | 未设置的键使用默认值 |
| TC-STATE-054 | suffix 到 cfg key 的映射 | 无 | 验证映射关系 | 映射正确 |

#### 应用预设参数 (`TestApplyPresetParams`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-STATE-055 | 视图键直接写入 | real_session_state | apply_preset_params(view_keys) | 视图键写入 session_state |
| TC-STATE-056 | 非视图键直接写入 | real_session_state | apply_preset_params(non_view_keys) | 非视图键写入 session_state |
| TC-STATE-057 | 混合键处理 | real_session_state | apply_preset_params(mixed) | 所有键正确写入 |

#### 快捷函数 (`TestShortcutFunctions`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-STATE-058 | view 返回 ViewState | real_session_state | state.view("prefix") | 返回 ViewState 实例 |
| TC-STATE-059 | get_view_cfg 正常工作 | real_session_state | get_view_cfg | 返回配置字典 |

---

## 2. 算法核心测试

### 2.1 滤波引擎 (`test_filters.py`) — 22 用例

#### 常量信号 (`TestConstantSignal`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-FILT-001 | 常量信号去噪不改变值 | constant_signal + time_index fixture | 应用各滤波算法 | 输出与输入的常数值接近 |

#### 降噪算法 (`TestNoiseReduction`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-FILT-002 | Savitzky-Golay 滤波降噪 | noisy_sine + clean_sine + time_index | 应用 sg_filter | 降噪后信号更接近干净信号 |
| TC-FILT-003 | Gaussian 滤波降噪 | noisy_sine + clean_sine + time_index | 应用 gaussian_filter | 降噪后信号更接近干净信号 |
| TC-FILT-004 | Kalman 滤波降噪 | noisy_sine + clean_sine + time_index | 应用 kalman_filter | 降噪后信号更接近干净信号 |
| TC-FILT-005 | Butterworth 滤波降噪 | noisy_sine + clean_sine + time_index | 应用 butterworth_filter | 降噪后信号更接近干净信号 |
| TC-FILT-006 | WMA 滤波降噪 | noisy_sine + clean_sine + time_index | 应用 wma_filter | 降噪后信号更接近干净信号 |
| TC-FILT-007 | Butterworth Nyquist 频率钳制 | noisy_sine + time_index | 应用 butterworth_filter | 截止频率被钳制防止混叠 |
| TC-FILT-008 | 中值滤波去除脉冲噪声 | 含脉冲噪声的信号 | 应用 median_filter | 脉冲被去除 |

#### 边界情况 (`TestEdgeCases`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-FILT-009 | 空数组抛出异常 | 无 | denoise([]) | 抛出适当异常 |
| TC-FILT-010 | 全 NaN 数组 | time_index，全 NaN 数据 | denoise(all_nan) | 不崩溃，返回全 NaN |
| TC-FILT-011 | 窗口为 1 | constant_signal + time_index | denoise(window=1) | 输出接近输入（几乎不过滤） |
| TC-FILT-012 | 窗口大于信号长度 | constant_signal + time_index | denoise(window >> len) | 输出合理，不崩溃 |

#### Savgol 特殊情况 (`TestSavgolSpecial`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-FILT-013 | 偶数窗口自动转为奇数 | constant_signal + time_index | sg_filter(window=偶数) | 窗口被调整为奇数，正常执行 |
| TC-FILT-014 | 阶数 >= 窗口自动降低阶数 | constant_signal + time_index | sg_filter(order >= window) | 阶数被调整，正常执行 |
| TC-FILT-015 | 偶数窗口去噪 | noisy_sine + clean_sine + time_index | sg_filter(window=偶数) | 降噪效果正常 |

#### Kalman 特殊情况 (`TestKalmanSpecial`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-FILT-016 | 极端 Q 值不崩溃 | noisy_sine + time_index | kalman_filter(Q=极端值) | 不崩溃 |
| TC-FILT-017 | 极端 R 值不崩溃 | noisy_sine + time_index | kalman_filter(R=极端值) | 不崩溃 |
| TC-FILT-018 | 常量信号收敛 | constant_signal + time_index | kalman_filter | 卡尔曼滤波收敛到常量 |

#### 指标计算 (`TestComputeMetrics`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-FILT-019 | 完美拟合指标 | 相同信号 | compute_metrics(clean, clean) | RMSE=0, R²=1 |
| TC-FILT-020 | 少于 3 个有效点 | 仅 2 个有效数据点 | compute_metrics | 返回适当值或 None |
| TC-FILT-021 | 已知偏移量 | 信号加固定偏移 | compute_metrics | 正确计算偏移 |
| TC-FILT-022 | SNR 改善单调性 | noisy_sine + 不同滤波结果 | 对比 SNR 改善 | 滤波后 SNR 不降低 |

---

### 2.2 信号处理 (`test_signals.py`) — 16 用例

#### 施密特触发器 (`TestSchmittTrigger`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-SIG-001 | 死区无加速度不触发 | 含缓慢变化的信号 | schmitt_trigger | 输出为 0（不触发） |
| TC-SIG-002 | 多头触发 | 含上升加速的信号 | schmitt_trigger | 输出包含正值 (1) |
| TC-SIG-003 | 空头触发 | 含下降加速的信号 | schmitt_trigger | 输出包含负值 (-1) |
| TC-SIG-004 | 迟滞效应 | 阈值附近的信号 | schmitt_trigger | 切换需要超过上下阈值 |
| TC-SIG-005 | 短序列返回 None | 长度 < span 的信号 | schmitt_trigger | 返回 None |
| TC-SIG-006 | NaN 传播 | 含 NaN 的信号 | schmitt_trigger | NaN 点对应输出为 0 |
| TC-SIG-007 | 常量速度输入 | 匀速变化的信号 | schmitt_trigger | 输出全为 0 |
| TC-SIG-008 | sigma_min 下限 | 参数 sigma_min 设置 | schmitt_trigger | 标准差不低于 sigma_min |

#### 查找信号对 (`TestFindAllPairs`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-SIG-009 | 空数组返回空 | 空信号数组 | find_all_pairs([]) | 返回 [] |
| TC-SIG-010 | 全零返回空 | 全零信号数组 | find_all_pairs | 返回 [] |
| TC-SIG-011 | 单向多头信号段 | 只有一段连续 +1 的数组 | find_all_pairs | 返回正确的 (start, end) |
| TC-SIG-012 | 交替信号 | +1 和 -1 交替的数组 | find_all_pairs | 正确识别各段 |
| TC-SIG-013 | 相邻同向信号合并 | 相邻两段同向信号 | find_all_pairs | 合并为一段 |
| TC-SIG-014 | 相邻同向信号合并（含反向段） | 同向-异向-同向 | find_all_pairs | 两同向段合并 |
| TC-SIG-015 | 短信号序列 | 长度不足的信号 | find_all_pairs | 正确处理或不形成对 |
| TC-SIG-016 | 无零分隔符 | 连续信号无缝切换 | find_all_pairs | 正确处理边界 |

---

### 2.3 交易策略 (`test_strategy.py`) — 18 用例

#### 抛物线拟合 (`TestFitParabolic`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-STRAT-001 | 精确二次曲线拟合 | 纯二次函数数据 | fit_parabolic | 返回精确的 a, b, c 系数 |
| TC-STRAT-002 | 点数不足 | 少于 3 个数据点 | fit_parabolic | 返回 None 或空结果 |
| TC-STRAT-003 | 子段拟合 | 全数据取子段 | fit_parabolic(subsegment) | 拟合子段数据正确 |

#### 物理抛物线拟合 (`TestFitPhysicsParabola`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-STRAT-004 | 已知曲率拟合 | 预定义抛物线数据 | fit_physics_parabola | 拟合参数匹配预期 |
| TC-STRAT-005 | 点数不足返回 None | 少于 3 个点 | fit_physics_parabola | 返回 None |
| TC-STRAT-006 | 共线数据 | 三点一线 | fit_physics_parabola | 返回 None 或处理退化情况 |
| TC-STRAT-007 | 子段拟合 | 部分数据 | fit_physics_parabola(subsegment) | 正确拟合子段 |

#### 拟合对比 (`TestFitComparison`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-STRAT-008 | 外推差异比较 | 二次函数数据 | 对比 parabolic vs physics 外推 | 两种方法外推结果有差异说明合理性 |

#### 策略盈亏计算 (`TestComputeStrategyPnL`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-STRAT-009 | 空交易对列表 | 空 pairs | compute_strategy_pnl | 返回空/零结果 |
| TC-STRAT-010 | 多头交易盈亏 | 多头交易对 + 价格数据 | compute_strategy_pnl | P&L 为正（价格上涨盈利） |
| TC-STRAT-011 | 空头交易盈亏 | 空头交易对 + 价格数据 | compute_strategy_pnl | P&L 为正（价格下跌盈利） |
| TC-STRAT-012 | 止损触发 | 价格触发止损条件 | compute_strategy_pnl | 交易在止损位退出 |
| TC-STRAT-013 | 独立资金池 | 多个交易对 | compute_strategy_pnl | 各交易对资金独立计算 |
| TC-STRAT-014 | 连续交易 | 多笔连续交易 | compute_strategy_pnl | P&L 正确累计 |
| TC-STRAT-015 | 极端止损值 | 止损设置极端值 | compute_strategy_pnl | 不崩溃，P&L 合理 |

#### 预测轨迹 (`TestAddPredictionTraces`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-STRAT-016 | poly2 添加拟合和预测线 | subplot_fig + 交易对 | add_prediction_traces(method="poly2") | 图上添加了拟合线和预测延伸线 |
| TC-STRAT-017 | physics fit 使用顶点锚定 | subplot_fig + 交易对 | add_prediction_traces(method="physics") | 预测线从顶点延伸 |
| TC-STRAT-018 | no_extend 仅添加拟合线 | subplot_fig | add_prediction_traces(extend=False) | 图中仅有拟合线，无预测延伸 |

---

## 3. UI 组件测试

### 3.1 侧边栏组件 (`test_sidebar.py`) — 36 用例

#### 全时间帧 (`TestAllTFs`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-SIDEBAR-001 | ALL_TFs 包含预期元素 | 无 | 检查 ALL_TFs 内容 | 包含所有时间帧选项 |
| TC-SIDEBAR-002 | ALL_TFs 长度正确 | 无 | 检查 len(ALL_TFs) | 返回预期长度 |
| TC-SIDEBAR-003 | ALL_TFs 升序排列 | 无 | 检查顺序 | 时间帧按升序排列 |

#### 默认时间帧 (`TestDefaultTFs`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-SIDEBAR-004 | DEFAULT_TFs 包含预期元素 | 无 | 检查 DEFAULT_TFs 内容 | 包含默认时间帧 |
| TC-SIDEBAR-005 | DEFAULT_TFs 长度正确 | 无 | len(DEFAULT_TFs) | 返回预期长度 |
| TC-SIDEBAR-006 | DEFAULT_TFs 是 ALL_TFs 的子集 | 无 | set 运算 | DEFAULT 包含于 ALL |

#### 时间帧层级 (`TestTfHierarchy`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-SIDEBAR-007 | hierarchy 包含所有 ALL_TFs 键 | 无 | 检查 hierarchy.keys() | 包含所有 TF 键 |
| TC-SIDEBAR-008 | hierarchy 长度正确 | 无 | len(hierarchy) | 与 ALL_TFs 匹配 |
| TC-SIDEBAR-009 | hierarchy 值升序排列 | 无 | 检查值顺序 | 逐级递增（更高 TF 映射到更低 TF） |
| TC-SIDEBAR-010 | 最高 TF 映射到 None | 无 | hierarchy[最高TF] | 返回 None |
| TC-SIDEBAR-011 | hierarchy 是有向无环图 | 无 | 图结构验证 | 无循环引用 |
| TC-SIDEBAR-012 | hierarchy 单调顺序 | 无 | 验证键的值不指向自己更高的 | 映射方向正确 |
| TC-SIDEBAR-013 | hierarchy 无自引用 | 无 | 遍历检查 | 没有 TF 指向自己 |
| TC-SIDEBAR-014 | hierarchy 无跳级 | 无 | 验证相邻级别映射 | 每级只映射到上一级 |

#### 紧凑 slider 格式逻辑 (`TestCompactSliderFormatLogic`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-SIDEBAR-015 | 无 format 时 fmt 为 None | 无 format 字符串 | compact_slider_format_logic | 返回 None |
| TC-SIDEBAR-016 | 提供 format 时包含格式 | 有 format 字符串 | compact_slider_format_logic | 返回内容包含 format 信息 |

#### 渲染参数 slider 逻辑 (`TestRenderParamSliderLogic`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-SIDEBAR-017 | step 类型决定格式 | 不同类型 step | render_param_slider_logic | 整数步长用 %d，浮点用 %f |
| TC-SIDEBAR-018 | key 后缀追加 | 给定 key 和 suffix | render_param_slider_logic | 生成的 key = key + suffix |
| TC-SIDEBAR-019 | container 默认为 None | 不传 container | render_param_slider_logic | container 参数为 None |

#### 紧凑 slider 渲染 (`TestCompactSlider`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-SIDEBAR-020 | 不传 key 和 fmt 时正常渲染 | mock st.columns | _compact_slider(N, 20, 300, 120, 10) | 返回 slider 值，caption 被调用 |
| TC-SIDEBAR-021 | 传入 key 和 fmt 时传递给 slider | mock st.columns | _compact_slider(sigma, ..., key="my_ke", fmt="%.3f") | key 和 format 透传给 slider |
| TC-SIDEBAR-022 | caption 显示 label 文本 | mock st.columns | _compact_slider("窗口", ...) | caption 内容为 "窗口" |

#### 渲染参数 slider (`TestRenderParamSlider`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-SIDEBAR-023 | container=None 使用 st.sidebar.slider | mock st.sidebar | _render_param_slider(...) | sidebar.slider 被调用 |
| TC-SIDEBAR-024 | container=st 使用 st.slider | mock st | _render_param_slider(..., container=mock_st) | st.slider 被调用 |
| TC-SIDEBAR-025 | key_suffix 非空时 key 为 f"{label}_{key_suffix}" | mock st.sidebar | _render_param_slider(key_suffix="f1_sma") | key 为 "跨度_f1_sma" |
| TC-SIDEBAR-026 | int step 不传 format 参数 | mock st.sidebar | _render_param_slider(step=1) | format 不在 kwargs 中 |
| TC-SIDEBAR-027 | float step < 0.01 使用 %.3f 格式 | mock st.sidebar | _render_param_slider(step=0.001) | format 为 "%.3f" |
| TC-SIDEBAR-028 | float step >= 0.01 使用 %.2f 格式 | mock st.sidebar | _render_param_slider(step=0.1) | format 为 "%.2f" |
| TC-SIDEBAR-029 | key_suffix='' 不查 session_state | mock st.sidebar | _render_param_slider(key_suffix="") | key 为 None |

#### 渲染参数面板 (`TestRenderParams`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-SIDEBAR-030 | 基本 SMA filter 渲染 | mock FILTERS + st 组件 | _render_params(sma) | 组件被正确渲染 |
| TC-SIDEBAR-031 | 未知 filter 显示警告 | mock FILTERS 不含目标 filter | _render_params(unknown) | 警告信息被显示 |
| TC-SIDEBAR-032 | 双滤波模式渲染 | mock 双 filter 配置 | _render_params(dual=True) | 两组参数面板均渲染 |
| TC-SIDEBAR-033 | 未知 filter_id2 不崩溃 | mock 无效 filter_id2 | _render_params(filter_id2=unknown) | 应用不崩溃 |
| TC-SIDEBAR-034 | show_sch=False 跳过施密特展开 | mock st 组件 | _render_params(show_sch=False) | 施密特 expander 不被渲染 |
| TC-SIDEBAR-035 | 展开/折叠按钮状态切换 | mock st.button True | _render_params(...) | expander 状态正确切换 |
| TC-SIDEBAR-036 | 策略禁用时从 session_state 读取止损 | mock session_state | _render_params(strategy=False) | 止损值从 state 读取 |

---

### 3.2 预设选择器 (`test_preset_ui.py`) — 60 用例

#### 预设生命周期 (`TestPresetLifecycle`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PUI-001 | 完整预设生命周期 | 空数据库 | 创建→应用→修改→删除 | 每步操作成功且状态正确 |

#### 删除预设 (`TestDeletePreset`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PUI-002 | 删除后从列表中移除 | 存在预设 | delete_preset → list_presets | 列表中不再包含该预设 |
| TC-PUI-003 | 删除后 get_preset 返回 None | 存在预设 | delete → get_preset | 返回 None |
| TC-PUI-004 | 删除不存在的预设不抛异常 | 空数据库 | delete_preset(不存在的) | 不报错 |

#### 重命名预设 (`TestRenamePreset`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PUI-005 | 重命名后列表更新 | 存在预设 | rename_preset → list_presets | 列表显示新名称 |
| TC-PUI-006 | get_preset 反映新名称 | 存在预设 | rename → get_preset | 返回记录 name 已更新 |
| TC-PUI-007 | 旧名称 get_preset_by_name 失败 | 存在预设 | rename → get_preset_by_name(旧名) | 返回 None |

#### 应用预设到 session_state (`TestApplyPresetSessionState`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PUI-008 | apply_preset 填充 session_state | 预设含完整参数 | apply_preset | session_state 包含预设参数 |
| TC-PUI-009 | 空参数预设应用 | 预设 params={} | apply_preset | 不报错 |
| TC-PUI-010 | 无效 JSON 参数返回 None | 预设含无效 JSON | apply_preset | 返回 None |
| TC-PUI-011 | 应用不存在的预设返回 None | 无此预设 | apply_preset(不存在) | 返回 None |

#### 保存覆盖预设 (`TestSaveOverwritePreset`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PUI-012 | 覆盖保存预设 | 同名预设已存在 | save_preset(同名) | 预设参数被更新 |
| TC-PUI-013 | 覆盖不增加列表计数 | 已有 1 个预设 | save_preset(覆盖) | list_presets 数量不变 |
| TC-PUI-014 | 多次覆盖 | 已有预设 | 连续 3 次覆盖保存 | 列表数量始终不变 |

#### 导入 JSON 为预设 (`TestImportJsonAsPresets`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PUI-015 | 导入 JSON 创建预设 | isolate_db，JSON 文件目录 | import_json_as_presets | 预设被创建 |
| TC-PUI-016 | 导入尊重已存在名称 | isolate_db，部分名称冲突 | import_json_as_presets | 冲突的跳过，新的创建 |
| TC-PUI-017 | 强制导入覆盖 | isolate_db，同名预设 | import_json_as_presets(force=True) | 预设被覆盖 |
| TC-PUI-018 | 空配置目录导入 | isolate_db，空目录 | import_json_as_presets | 返回空结果 |
| TC-PUI-019 | 按后缀分类导入 | isolate_db，含不同分类后缀 | import_json_as_presets | 分类信息正确 |
| TC-PUI-020 | 跳过损坏 JSON | isolate_db，含无效 JSON | import_json_as_presets | 跳过并记录，不影响其他 |

#### 空预设列表 (`TestPresetListEmpty`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PUI-021 | 初始列表为空 | 空数据库 | list_presets | 返回空列表 |
| TC-PUI-022 | 按分类列表（空） | 空数据库 | list_presets(category="xxx") | 返回空列表 |

#### 重复预设名称 (`TestDuplicatePresetName`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PUI-023 | 重复名称触发更新 | 已有同名预设 | save_preset(同名) | 更新而非报错 |
| TC-PUI-024 | 重复名称不增加计数 | 已有 1 个预设 | save_preset(同名) | 列表中仍为 1 个 |
| TC-PUI-025 | 重命名为冲突名称成功 | 预设 A 名称为 old，预设 B 为 new | rename_preset(A, "new") | 重命名成功 |

#### 预设边界情况 (`TestPresetEdgeCases`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PUI-026 | 空字符串名称 | 无 | save_preset(name="") | 抛出异常 |
| TC-PUI-027 | 大 JSON 参数 | 巨大参数 JSON | save_preset | 正常保存 |
| TC-PUI-028 | 列表按分类+名称排序 | 多个分类的预设 | list_presets | 返回排序正确的列表 |
| TC-PUI-029 | 唯一性约束违规触发更新 | 数据库唯一约束 | save_preset(违规) | 触发更新逻辑 |

#### 选择框刷新 (`TestSelectboxRefresh`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PUI-030 | 删除改变 hash 重置 widget | 有预设列表 | delete → 检查 hash | hash 值改变 |
| TC-PUI-031 | 重命名改变 hash | 有预设 | rename → 检查 hash | hash 值改变 |
| TC-PUI-032 | 保存新预设改变 hash | 空列表 | save → 检查 hash | hash 值改变 |
| TC-PUI-033 | 无变化时 hash 不变 | 有预设列表 | 连续检查 hash | hash 值不变 |
| TC-PUI-034 | apply_preset 不改变 hash | 有预设 | apply → 检查 hash | hash 值不变 |

#### 预设 CRUD 循环 (`TestPresetCrudCycle`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PUI-035 | 创建→修改→删除→重建 | 空数据库 | 完整 CRUD 循环 | 每步操作后状态正确 |
| TC-PUI-036 | 重命名后再用原名创建 | 已重命名的预设 | recreate(原名称) | 创建成功 |
| TC-PUI-037 | 多次快速重命名 | 有预设 | 连续 3 次 rename | 最终名称正确 |
| TC-PUI-038 | 全部删除后重新导入 | 有多个预设 | delete_all → reimport | 恢复原始状态 |
| TC-PUI-039 | 覆盖保存保持 preset_id | 有预设 | save(覆盖) | preset_id 不变 |

#### session_state 边界 (`TestSessionStateEdgeCases`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PUI-040 | 空 session_state collect | session_state 为空 | collect_current_params | 返回空字典 |
| TC-PUI-041 | 部分全局键 | session_state 部分键存在 | collect_current_params | 仅采集存在的键 |
| TC-PUI-042 | 采集全部 4 个视图 | session_state 含 4 视图 | collect_current_params | 4 视图参数都采集到 |
| TC-PUI-043 | 采集中文滤波键 | session_state 含中文滤波键 | collect_current_params | 中文滤波键被采集 |
| TC-PUI-044 | import_data 标志保留 | session_state.import_data=True | collect_current_params | import_data 键不被采集 |
| TC-PUI-045 | imp 备份不被覆盖 | session_state 含 imp 键 | collect_current_params | imp 键值不受影响 |
| TC-PUI-046 | 不同 ticker 不同预设 | 切换 ticker | 切换连接 | 每个 ticker 独立预设 |

#### 大规模测试 (`TestLargeScale`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PUI-047 | 大量预设列表和查询 | 100 个预设 | list + query | 性能可接受，结果正确 |
| TC-PUI-048 | 大 JSON 参数预设 | 超大参数 | save + get | 正常处理 |
| TC-PUI-049 | 批量删除和重建 | 50 个预设 | bulk delete → recreate | 操作正确执行 |

#### 预设分类保存 (`TestSavePresetWithCategory`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PUI-050 | 覆盖时保持原分类 | 预设分类为 "A" | save_preset(覆盖，分类="B") | 分类可能更新或保持 |
| TC-PUI-051 | 新预设使用默认分类 | 无分类指定 | save_preset(新) | 使用默认分类 "general" |
| TC-PUI-052 | 覆盖时不同分类更新 | 预设分类 "A" | save_preset(覆盖，分类="B") | 分类被更新 |

#### 参数采集完整性 (`TestCollectParamsCompleteness`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PUI-053 | 采集所有全局键 | session_state 含完整全局键 | collect_current_params | 所有全局键都被采集 |
| TC-PUI-054 | 采集 4 视图全部键 | session_state 含 4 视图 | collect_current_params | 所有视图键都被采集 |
| TC-PUI-055 | 采集所有中文滤波前缀 | session_state 含中文滤波键 | collect_current_params | 所有中文滤波键被采集 |
| TC-PUI-056 | 无关键不被采集 | session_state 含无关键 | collect_current_params | 无关键不在结果中 |

#### 预设映射查找 (`TestPresetMapLookup`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PUI-057 | 不同预设按 ID 区分 | 多个不同预设 | preset_map_lookup | 每个预设独立可查 |
| TC-PUI-058 | 列表按分类+名称排序 | 混合分类预设 | list_presets | 先按分类再按名称排序 |
| TC-PUI-059 | 同名称覆盖不创建重复 | 同名预设 | save(覆盖) | 数据库中仅一条记录 |
| TC-PUI-060 | get_preset_by_name 检索唯一预设 | 多个不同名称预设 | get_preset_by_name | 返回唯一匹配预设 |

---

### 3.3 预设操作交互 (`test_preset_ui_actions.py`) — 45 用例

#### 预设操作标志 (`TestPresetActionFlags`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PUIA-001 | 更新按钮设置 action flags | session_state 干净 | 点击更新按钮 | action_flags 被正确设置 |
| TC-PUIA-002 | 重命名按钮设置 action flags | session_state 干净 | 点击重命名按钮 | action_flags 被正确设置 |
| TC-PUIA-003 | 删除按钮设置 action flags | session_state 干净 | 点击删除按钮 | action_flags 被正确设置 |
| TC-PUIA-004 | 更新完成后 flags 被清除 | session_state 有 update flags | 完成更新操作 | action_flags 被清除 |
| TC-PUIA-005 | 重命名完成后 flags 被清除 | session_state 有 rename flags | 完成重命名 | action_flags 被清除 |
| TC-PUIA-006 | 删除完成后 flags 被清除 | session_state 有 delete flags | 完成删除 | action_flags 被清除 |
| TC-PUIA-007 | 取消操作清除 flags | session_state 有 action flags | 触发取消 | action_flags 被清除 |
| TC-PUIA-008 | action_target 未找到清除 flags | session_state 有 flags | 操作无效目标 | flags 被清除 |
| TC-PUIA-009 | 无 action flags 时不执行操作 | session_state 无 flags | 调用处理函数 | 不执行任何操作 |

#### 预设名称同步 (`TestPresetNameSync`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PUIA-010 | 选择预设同步名称含 "copy" 后缀 | 选择已有预设 | 检查 new_preset_name | 名称为 "原名称 (copy)" |
| TC-PUIA-011 | 选择 None 清除 new_preset_name | 已选预设名称 | 选择 None | new_preset_name 被清除 |
| TC-PUIA-012 | last_sel_name 跟踪变化 | 切换预设选择 | 检查 last_sel_name | 反映最新选择 |
| TC-PUIA-013 | 相同选择不重新同步 | 已选预设 A | 再次选 A | 不触发重新同步 |
| TC-PUIA-014 | 选择变化触发同步 | 选 A 后选 B | 检查状态 | 同步逻辑被触发 |
| TC-PUIA-015 | 初始无选择状态 | session_state 初始化 | 检查 new_preset_name | 为空字符串 |

#### 预设映射 (`TestPresetMapLookup`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PUIA-016 | preset_map 正确构建 | 多个预设 | 构建 preset_map | 映射包含所有预设 |
| TC-PUIA-017 | 同名称不同分类唯一键 | 同名称不同分类预设 | 构建 preset_map | 两个键不同 |
| TC-PUIA-018 | preset_map lookup 返回完整记录 | 已知预设 | lookup(label) | 返回完整预设记录 |
| TC-PUIA-019 | 空预设列表产生空 map | 空数据库 | 构建 preset_map | 返回空字典 |
| TC-PUIA-020 | preset_map label 解析往返 | 已知 label | 解析 → 重构 label | 往返一致 |
| TC-PUIA-021 | 取消选择 label 不在 map 中 | label="-- 无预设 --" | lookup | 返回 None |

#### 分类保持 (`TestCategoryPreservation`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PUIA-022 | 覆盖保持原始分类 | 预设分类 "A" | save_preset(覆盖) | 分类仍然是 "A" |
| TC-PUIA-023 | 新预设使用默认分类 | 无分类指定 | save_preset(新) | 分类为默认 |
| TC-PUIA-024 | 新预设可指定分类 | 指定分类 "B" | save_preset(新, category="B") | 分类为 "B" |
| TC-PUIA-025 | 分类在多次覆盖中保持 | 预设经过多次覆盖 | 多次 save_preset(覆盖) | 分类始终不变 |
| TC-PUIA-026 | 默认分类为 "general" | 不指定分类 | 检查默认分类 | 返回 "general" |

#### Toast 反馈 (`TestToastFeedback`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PUIA-027 | apply_preset 返回参数供反馈 | 有预设 | apply_preset | 返回 params 用于 toast |
| TC-PUIA-028 | save_preset 返回有效 preset_id | 有效参数 | save_preset | 返回整数 preset_id |
| TC-PUIA-029 | rename_preset 返回新名称 | 有效重命名 | rename_preset | 返回新名称字符串 |
| TC-PUIA-030 | delete_preset 成功返回 True | 存在预设 | delete_preset | 返回 True |
| TC-PUIA-031 | delete_preset 缺失返回 False | 不存在 | delete_preset | 返回 False |
| TC-PUIA-032 | 模拟延迟 apply 机制 | session_state | 设置 → 延迟 → pop | 参数最终生效 |
| TC-PUIA-033 | 延迟 apply 保留非 widget 内部键 | session_state | 延迟 apply | 内部键不被影响 |
| TC-PUIA-034 | 模拟保存后 toast | 保存操作 | save → 检查返回值 | 返回值可用于 toast 提示 |

#### 控件键冲突防止 (`TestWidgetKeyConflictPrevention`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PUIA-035 | collect_params 只返回 widget 绑定键 | monkeypatch | collect_current_params | 不返回非 widget 键 |
| TC-PUIA-036 | 所有采集的键需要延迟 apply | monkeypatch | collect → deferred apply | 所有采集键通过延迟机制 |
| TC-PUIA-037 | 延迟机制隔离 widget 键 | 含 widget 和非 widget 键 | deferred_apply | widget 键处理正确 |
| TC-PUIA-038 | 延迟 apply 处理额外键 | 含额外键 | deferred_apply | 优雅处理额外键 |
| TC-PUIA-039 | 延迟 apply 处理缺失键 | 缺少部分键 | deferred_apply | 不因缺失键崩溃 |
| TC-PUIA-040 | 文件导入在 widget 前使用直接 set | 导入场景 | import → set | 直接设置绕过 widget 冲突 |
| TC-PUIA-041 | overwrite_preset 键从 collect 中排除 | monkeypatch | collect_current_params | overwrite 键不在结果中 |

#### 覆盖复选框重置流程 (`TestOverwriteCheckboxResetFlow`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PUIA-042 | widget 实例化后直接重置抛异常 | 已实例化 widget | 直接 set | 抛出异常 |
| TC-PUIA-043 | 延迟重置避免 widget 冲突 | 已实例化 widget | deferred reset | 不报错 |
| TC-PUIA-044 | 仅当 flag 存在时延迟重置 | session_state | 检查 deferred_reset 逻辑 | flag 不存在时不执行 |
| TC-PUIA-045 | rerun 重置锁和 widget 注册表 | session_state | 模拟 rerun | 锁和注册表被重置 |

---

### 3.4 应用 UI (`test_app_ui.py`) — 33 用例

#### 冒烟测试 (`TestAppSmoke`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-APPUI-001 | 应用可运行 | app fixture | 启动 app | 不抛出异常 |
| TC-APPUI-002 | 标题存在 | app fixture | 检查页面标题 | 标题包含预期文本 |
| TC-APPUI-003 | 侧边栏存在 | app fixture | 检查 st.sidebar | 侧边栏被渲染 |
| TC-APPUI-004 | 主区域存在 | app fixture | 检查主区域 | 主区域被渲染 |

#### Session State (`TestSessionState`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-APPUI-005 | config 已初始化 | app fixture | 检查 session_state 配置键 | 配置键存在且不为空 |

#### P0 回归 (`TestP0Regression`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-APPUI-006 | 渲染前应用不崩溃 | app fixture | 启动应用到渲染完成 | 无未捕获异常 |

#### 侧边栏交互 (`TestSidebarInteraction`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-APPUI-007 | 市场选择器存在 | app fixture | 检查 sidebar 中的 market selector | 选择器被渲染 |
| TC-APPUI-008 | ticker 输入框存在 | app fixture | 检查 sidebar 中的 ticker input | 输入框被渲染 |
| TC-APPUI-009 | 滤波选择器存在 | app fixture | 检查 filter selector | 选择器被渲染 |
| TC-APPUI-010 | 双滤波复选框存在 | app fixture | 检查 dual filter checkbox | 复选框被渲染 |
| TC-APPUI-011 | day_step 选择器存在 | app fixture | 检查 day_step selector | 选择器被渲染 |
| TC-APPUI-012 | 刷新按钮存在 | app fixture | 检查 refresh button | 按钮被渲染 |
| TC-APPUI-013 | 自动刷新复选框存在 | app fixture | 检查 auto_refresh checkbox | 复选框被渲染 |

#### 预设交互 (`TestPresetInteraction`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-APPUI-014 | 预设选择器存在 | app fixture | 检查 preset selector | 选择器被渲染 |
| TC-APPUI-015 | 预设选项包含 "无预设" | app fixture | 检查 preset 选项 | 包含 None 选项 |
| TC-APPUI-016 | 预设选项非空 | app fixture，有预设 | 检查 preset 选项列表 | 列表包含预设项 |
| TC-APPUI-017 | 无预设选择时安全 | app fixture | 选择 None 预设 | 不崩溃 |

#### 控件交互 (`TestWidgetInteraction`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-APPUI-018 | ticker 变化不崩溃 | app fixture | 修改 ticker 输入 | 不抛出异常 |
| TC-APPUI-019 | 滤波变化不崩溃 | app fixture | 修改 filter 选择 | 不抛出异常 |
| TC-APPUI-020 | 双滤波切换 | app fixture | 勾选/取消 dual filter | widget 状态正确切换 |
| TC-APPUI-021 | 日期导航按钮存在 | app fixture | 检查 day nav 按钮 | 前后导航按钮被渲染 |

#### P0 回归扩展 (`TestP0RegressionExtended`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-APPUI-022 | 空 ticker 安全 | app fixture | 清空 ticker 输入 | 应用不崩溃 |
| TC-APPUI-023 | 未知滤波设置 | app fixture | 设置未知 filter_id | 应用不崩溃 |

#### 隔离回归 (`TestIsolationRegression`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-APPUI-024 | 多次新鲜 app 一致性 | 无 | 多次创建新 app | 每次行为一致 |
| TC-APPUI-025 | 新鲜 app 日期按钮稳定 | 新 app | 检查 day nav 按钮 | 按钮始终可交互 |
| TC-APPUI-026 | 新鲜 app 无意外异常 | 新 app | 完整渲染 | 无未预期异常 |

#### P0 刷新按钮点击 (`TestRefreshButtonEndToEnd`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-APPUI-027 | 点击刷新数据按钮后应用不崩溃 | _fresh_app | 点击"刷新数据"按钮后 run() | 无意外异常或仅有 Series truth value 已知异常 |
| TC-APPUI-028 | 点击刷新后缓存被清除 | _fresh_app | 点击"刷新数据"按钮后 run() | 不抛出 AttributeError |

#### P1 备份/预设操作 (`TestBackupRestoreButtons`, `TestPresetApplyEndToEnd`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-APPUI-029 | 创建备份按钮存在且可点击 | _fresh_app | 点击"创建备份"按钮后 run() | 不崩溃 |
| TC-APPUI-030 | 选择预设后点击应用按钮不崩溃 | _fresh_app，存在预设 | 选择预设，点击应用按钮后 run() | 不崩溃 |

#### P2 自动刷新 + 异常路径 (`TestAutoRefreshSafety`, `TestExceptionPathCoverage`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-APPUI-031 | 勾选自动刷新复选框不崩溃 (xfail) | _fresh_app | 勾选 auto_refresh 后 run() | auto_refresh 在 session_state 中 (xfail: rerun 循环导致 AppTest timeout) |
| TC-APPUI-032 | 无效 ticker 下刷新不崩溃 | _fresh_app | 清空 ticker，点击刷新按钮后 run() | 不崩溃 |

#### P3 删除备份边缘情况 (`TestDeleteBackupEdgeCase`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-APPUI-033 | 删除不存在的备份文件不崩溃 | _fresh_app | 点击"删除此备份"按钮后 run() | 不崩溃 |

---

### 3.5 Streamlit App (`test_streamlit_app.py`) — 25 用例

#### 日期标记 (`TestDateMarkers`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-STRM-001 | 空日期返回空 | 空列表 | date_markers([]) | 返回空 |
| TC-STRM-002 | None 日期返回空 | None | date_markers(None) | 返回空 |
| TC-STRM-003 | 日内标记天数边界 | 日内时间戳 | date_markers | 正确放置天数标记 |
| TC-STRM-004 | 日线标记周一边界 | 日线时间戳 | date_markers(week) | 正确放置周一标记 |
| TC-STRM-005 | 周线标记月边界 | 周线时间戳 | date_markers(month) | 正确放置月标记 |
| TC-STRM-006 | 月线标记一月 | 月线数据 | date_markers(month) | 一月被标记 |
| TC-STRM-007 | 季度标记同月度 | 季度数据 | date_markers(quarter) | 使用月度标记逻辑 |

#### 计算滤波 (`TestComputeFilters`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-STRM-008 | 未知滤波 ID | 无效 filter_id | compute_filters | 不崩溃或返回 None |
| TC-STRM-009 | SMA 滤波 | simple_noisy fixture | compute_filters("sma") | 返回滤波结果 |
| TC-STRM-010 | 双滤波模式 | simple_noisy fixture | compute_filters(dual=True) | 返回双滤波结果 |
| TC-STRM-011 | filtered 输出为 float ravel 化 | simple_noisy fixture | compute_filters | 输出是展平的 float 数组 |

#### 施密特触发计算 (`TestComputeSchmittTrigger`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-STRM-012 | show_sch 为 False 时禁用 | show_schmitt=False | compute_schmitt_trigger | 不执行计算 |
| TC-STRM-013 | 全 NaN 时禁用 | 全 NaN 滤波结果 | compute_schmitt_trigger | 不执行计算 |
| TC-STRM-014 | 返回 schmitt 字典 | sine_signal fixture | compute_schmitt_trigger | 返回包含 key 的字典 |
| TC-STRM-015 | 紧参数更多非零输出 | sine_signal fixture | compute_schmitt_trigger(紧参数) | 非零信号数量更多 |

#### 预测对计算 (`TestComputePredictionPairs`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-STRM-016 | show_pred 为 False 时为空 | show_prediction=False | compute_prediction_pairs | 返回空 |
| TC-STRM-017 | schmitt 为 None 时为空 | schmitt=None | compute_prediction_pairs | 返回空 |
| TC-STRM-018 | 有效对执行抛物线拟合 | 有效 signal pairs | compute_prediction_pairs | 返回拟合结果 |
| TC-STRM-019 | 对过短时跳过 | 长度不足的对 | compute_prediction_pairs | 该对被跳过 |

#### 子图布局确定 (`TestDetermineSubplotLayout`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-STRM-020 | 无功能时的最小布局 | 所有功能关闭 | determine_subplot_layout | 返回最小行数 |
| TC-STRM-021 | 有 schmitt 无策略时 | show_schmitt=True | determine_subplot_layout | 布局包含 schmitt 行 |
| TC-STRM-022 | 全部功能完整布局 | 所有功能开启 | determine_subplot_layout | 返回完整行数 |
| TC-STRM-023 | cross_pnl 无 alignment | show_cross_pnl=True | determine_subplot_layout | 布局调整正确 |
| TC-STRM-024 | 策略无 cross 无 alignment | strategy=True | determine_subplot_layout | 行数正确 |
| TC-STRM-025 | 无 schmitt 时 ar 为第四 | show_schmitt=False | determine_subplot_layout | AR 在正确行位置 |

---

## 4. 可视化测试

### 4.1 图表渲染 (`test_charts.py`) — 87 用例

#### 入场标记渲染 (`TestRenderEntryMarker`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CHART-001 | 添加一条 trace | Plotly figure | render_entry_marker | figure.data 增加 1 |
| TC-CHART-002 | trace 是三角形标记 | Plotly figure | render_entry_marker | marker.symbol 为 triangle |
| TC-CHART-003 | 无效索引不添加 trace | Plotly figure | render_entry_marker(index=越界) | 不添加新 trace |
| TC-CHART-004 | 自定义颜色和大小 | Plotly figure | render_entry_marker(color, size) | 颜色和大小生效 |
| TC-CHART-005 | showlegend 为 False | Plotly figure | render_entry_marker | trace.showlegend=False |
| TC-CHART-006 | hovertext 透传 | Plotly figure | render_entry_marker(hovertext="...") | hovertext 被设置 |
| TC-CHART-007 | 空 t 数组 | Plotly figure | render_entry_marker(t=[]) | 不崩溃 |
| TC-CHART-008 | marker 线有边框 | Plotly figure | render_entry_marker | marker.line 属性存在 |

#### 出场标记渲染 (`TestRenderExitMarker`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CHART-009 | 止损用 X 标记 | Plotly figure | render_exit_marker(reason="stop_loss") | marker.symbol 为 x |
| TC-CHART-010 | 止盈用圆圈标记 | Plotly figure | render_exit_marker(reason="take_profit") | marker.symbol 为 circle |
| TC-CHART-011 | 未知原因默认圆圈 | Plotly figure | render_exit_marker(reason="unknown") | marker.symbol 为 circle |
| TC-CHART-012 | 注释文本含收益率 | Plotly figure | render_exit_marker(return_pct=10.5) | 注释包含 "10.5%" |
| TC-CHART-013 | 多头交易箭头上 | Plotly figure | render_exit_marker(trade="long") | marker.symbol 为 triangle-up |
| TC-CHART-014 | 空头交易箭头下 | Plotly figure | render_exit_marker(trade="short") | marker.symbol 为 triangle-down |
| TC-CHART-015 | 负收益率 | Plotly figure | render_exit_marker(return_pct=-5) | 标记正确渲染 |
| TC-CHART-016 | 无效索引 catch | Plotly figure | render_exit_marker(index=越界) | 不崩溃 |
| TC-CHART-017 | marker 颜色透传 | Plotly figure | render_exit_marker(color="red") | 颜色生效 |

#### 基线渲染 (`TestRenderBaseline`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CHART-018 | 添加水平线 | Plotly figure | render_baseline | 添加了 hline trace |
| TC-CHART-019 | y 位置正确 | Plotly figure | render_baseline(y=100) | hline 在 y=100 |
| TC-CHART-020 | 虚线样式 | Plotly figure | render_baseline | line.dash 为 dash |
| TC-CHART-021 | 灰色颜色 | Plotly figure | render_baseline | line.color 为 gray |
| TC-CHART-022 | 自定义透明度透传 | Plotly figure | render_baseline(opacity=0.5) | opacity 生效 |
| TC-CHART-023 | 自定义 y | Plotly figure | render_baseline(y=自定义) | y 值生效 |

#### 背景填充渲染 (`TestRenderFillBackground`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CHART-024 | 添加填充 trace | Plotly figure | render_fill_background | 添加了 fill trace |
| TC-CHART-025 | fill 模式为 toself | Plotly figure | render_fill_background | fill 为 "toself" |
| TC-CHART-026 | 填充颜色透传 | Plotly figure | render_fill_background(fill_color="#...") | 颜色生效 |
| TC-CHART-027 | hoverinfo 为 skip | Plotly figure | render_fill_background | hoverinfo="skip" |
| TC-CHART-028 | 基线 y_max 计算 | Plotly figure | render_fill_background | y_max 超过数据最大值 |

#### P&L 曲线渲染 (`TestRenderPnLCurves`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CHART-029 | 添加两条 trace | Plotly figure | render_pnl_curves | figure.data 增加 2 |
| TC-CHART-030 | trace 默认名称 | Plotly figure | render_pnl_curves | 名称包含 "Long"/"Short" |
| TC-CHART-031 | 自定义名称 | Plotly figure | render_pnl_curves(names=...) | 自定义名称生效 |
| TC-CHART-032 | 自定义颜色 | Plotly figure | render_pnl_curves(colors=...) | 自定义颜色生效 |

#### JSON 清理 (`TestSanitizeForJson`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CHART-033 | NaN 转 None | float("nan") | sanitize_for_json | 返回 None |
| TC-CHART-034 | Inf 转 None | float("inf") | sanitize_for_json | 返回 None |
| TC-CHART-035 | -Inf 转 None | float("-inf") | sanitize_for_json | 返回 None |
| TC-CHART-036 | 正常浮点数保持 | 3.14 | sanitize_for_json | 返回 3.14 |
| TC-CHART-037 | 含 NaN 的字典 | {"a": NaN} | sanitize_for_json | {"a": None} |
| TC-CHART-038 | 含 NaN 的列表 | [NaN, 1] | sanitize_for_json | [None, 1] |
| TC-CHART-039 | 嵌套字典列表 | 复杂嵌套结构 | sanitize_for_json | 所有 NaN/Inf 被替换 |
| TC-CHART-040 | ndarray 转换 | np.array | sanitize_for_json | 转换为 list |
| TC-CHART-041 | 整数保持 | 42 | sanitize_for_json | 返回 42 |
| TC-CHART-042 | 字符串保持 | "hello" | sanitize_for_json | 返回 "hello" |
| TC-CHART-043 | tuple 转换为 list | (1, 2) | sanitize_for_json | 返回 [1, 2] |
| TC-CHART-044 | 零保持 | 0 | sanitize_for_json | 返回 0（非 None） |
| TC-CHART-045 | None 保持 | None | sanitize_for_json | 返回 None |

#### NpEncoder (`TestNpEncoder`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CHART-046 | ndarray 编码 | np.array([1,2,3]) | NpEncoder.encode | 返回 [1, 2, 3] |
| TC-CHART-047 | np 整数编码 | np.int64(5) | NpEncoder.encode | 返回 5 |
| TC-CHART-048 | np 正常浮点编码 | np.float64(3.14) | NpEncoder.encode | 返回 3.14 |
| TC-CHART-049 | np NaN 需先清理 | np.float64("nan") | NpEncoder.encode | 抛出异常（需要先 sanitize） |
| TC-CHART-050 | np Inf 需先清理 | np.float64("inf") | NpEncoder.encode | 抛出异常（需要先 sanitize） |
| TC-CHART-051 | 清理后嵌套编码 | 嵌套含 np 值 | sanitize → encode | 正常编码 |
| TC-CHART-052 | 普通整数编码 | 42 | NpEncoder.encode | 返回 42 |
| TC-CHART-053 | 普通浮点编码 | 3.14 | NpEncoder.encode | 返回 3.14 |
| TC-CHART-054 | np bool 编码 | np.bool_(True) | NpEncoder.encode | 返回 True |
| TC-CHART-055 | 含 np 值嵌套列表 | [np.int64(1), np.float64(2.0)] | NpEncoder.encode | 返回 [1, 2.0] |

#### Plotly HTML 渲染 (`TestRenderPlotlyHtml`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CHART-056 | CDN URL 在 HTML 中 | Plotly figure | render_plotly_html | HTML 包含 cdn.plot.ly |
| TC-CHART-057 | CDN 回退 URL | Plotly figure | render_plotly_html | HTML 包含备用 CDN |
| TC-CHART-058 | HTML 含日期提示 div | Plotly figure | render_plotly_html | HTML 包含 date_tip div |
| TC-CHART-059 | HTML 含 crosshair 逻辑 | Plotly figure | render_plotly_html | HTML 包含 crosshair JS |

#### 跨 P&L 子图 (`TestCrossPnlSubplot`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CHART-060 | 多头参考线添加 | Plotly figure | render_cross_pnl_subplot | 多头参考线存在 |
| TC-CHART-061 | 空头参考线添加 | Plotly figure | render_cross_pnl_subplot | 空头参考线存在 |
| TC-CHART-062 | 双向参考线 | Plotly figure | render_cross_pnl_subplot | 两根参考线都存在 |
| TC-CHART-063 | 无数据不添加 trace | Plotly figure，空数据 | render_cross_pnl_subplot | 不添加新 trace |
| TC-CHART-064 | 入场标记渲染 | 含入场标记数据 | render_cross_pnl_subplot | 入场标记被渲染 |
| TC-CHART-065 | 出场标记渲染 | 含出场标记数据 | render_cross_pnl_subplot | 出场标记被渲染 |
| TC-CHART-066 | 基线始终添加 | Plotly figure | render_cross_pnl_subplot | 基线 trace 存在 |
| TC-CHART-067 | marker 颜色为金色 | Plotly figure | render_cross_pnl_subplot | marker.color 为 gold |

#### 对齐子图 (`TestAlignmentSubplot`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CHART-068 | 基本 traces 添加 | Plotly figure + 对齐数据 | render_alignment_subplot | 多条 trace 被添加 |
| TC-CHART-069 | 交易被 masked 时高亮 | Plotly figure + 对齐数据 | render_alignment_subplot | 活跃交易区间高亮 |
| TC-CHART-070 | 空头交易红色 | Plotly figure + 空头数据 | render_alignment_subplot | 颜色为红色 |
| TC-CHART-071 | 出场越界跳过 | Plotly figure + 异常数据 | render_alignment_subplot | 越界退出被跳过 |
| TC-CHART-072 | 多头交易绿色 | Plotly figure + 多头数据 | render_alignment_subplot | 颜色为绿色 |

#### 预测轨迹 (`TestPredictionTraces`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CHART-073 | 预测 trace 属性 | Plotly figure | render_prediction_traces | trace 属性正确 |
| TC-CHART-074 | no_extend 不添加外推 | Plotly figure | render_prediction_traces(extend=False) | 仅拟合区间 |
| TC-CHART-075 | poly2 trace 名称 | Plotly figure | render_prediction_traces(method="poly2") | 名称含 "poly2" |
| TC-CHART-076 | poly2 模式通过 polyval 计算预测 | Plotly figure + fit_result(无 x0) | _add_prediction_traces | 预测值递增（正二次项系数） |
| TC-CHART-077 | physics 模式通过 polyval(x_ext-x0) 计算 | Plotly figure + fit_result(有 x0) | _add_prediction_traces | 预测以 x0 为顶点延伸 |

#### Plotly 序列化 (`TestRenderPlotlySerialization`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CHART-078 | NaN 值序列化为 JSON null | Plotly fig + NaN 数据 | _render_plotly | figure JSON 中 NaN 被替换为 null |
| TC-CHART-079 | Inf 值序列化为 JSON null | Plotly fig + Inf 数据 | _render_plotly | figure JSON 中 Inf 被替换为 null |
| TC-CHART-080 | 空数据 fig 不崩溃 | 空 go.Figure | _render_plotly | Plotly.newPlot 在 HTML 中 |
| TC-CHART-081 | dates 参数嵌入 layout | Plotly fig + 日期列表 | _render_plotly(dates=...) | HTML 包含日期字符串 |

#### 跨 P&L 子图交易标记 (`TestCrossPnlSubplotTrades`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CHART-082 | 空 entry/exit 且全 NaN 参考线不添加 traces | Plotly fig + 空标记列表 | _add_cross_pnl_subplot | 不添加新 trace |
| TC-CHART-083 | 同时有入场和离场标记时添加对应 traces | Plotly fig + 入场/出场标记 | _add_cross_pnl_subplot | 添加 4 条新 traces |

#### 施密特触发 trace (`TestSchmittTraces`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-CHART-084 | all_pairs 为空时不崩溃 | Plotly fig + 空 pairs | _add_schmitt_traces | 至少 6 条基础 traces |
| TC-CHART-085 | 单个 pair 添加 pair band trace | Plotly fig + 有 sig 数据 | _add_schmitt_traces | trace 数量增加 |
| TC-CHART-086 | sar/ssr 参数指向不同子图时正确布局 | Plotly fig(4 rows) | _add_schmitt_traces(sar=1, ssr=2) | trace 数量 >= base+7 |
| TC-CHART-087 | sig 为 1 和 -1 时添加不同颜色的 fill | Plotly fig + 双向 sig | _add_schmitt_traces | 含双向 sig fill traces |

---
### 4.2 时间对齐 (`test_alignment.py`) — 6 用例

#### 对齐 P&L 到当前时间帧 (`TestAlignPnlToCurrentTf`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-ALIGN-001 | 时区混合 HKT/naive | 含时区混用的数据 | align_pnl_to_current_tf | 时区对齐正确 |
| TC-ALIGN-002 | 无时间重叠 | 无交集的日期范围 | align_pnl_to_current_tf | 返回空或全 NaN |
| TC-ALIGN-003 | 前向填充 | 高频到低频对齐 | align_pnl_to_current_tf | 使用 forward fill 对齐 |
| TC-ALIGN-004 | 更高 TF 日期为 None | sample_dates_intraday + None higher | align_pnl_to_current_tf | 正确处理 None |
| TC-ALIGN-005 | 标记位置 | 含标记日期 | align_pnl_to_current_tf | 标记位置正确 |
| TC-ALIGN-006 | higher TF 比 current TF 短 | higher 数据更少 | align_pnl_to_current_tf | 合理处理对齐 |

---

### 4.3 对齐子图 (`test_alignment_subplot.py`) — 14 用例

#### 持仓遮罩计算 (`TestComputeHoldingMasks`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-ASUB-001 | 基本多头遮罩 | 入场和出场标记 | compute_holding_masks("long") | 多头持仓区间正确 |
| TC-ASUB-002 | 基本空头遮罩 | 入场和出场标记 | compute_holding_masks("short") | 空头持仓区间正确 |
| TC-ASUB-003 | 同时多头和空头 | 双向标记 | compute_holding_masks | 各方向遮罩独立正确 |
| TC-ASUB-004 | 有入场无出场 | 仅入场标记 | compute_holding_masks | 入场到最后为持仓区间 |
| TC-ASUB-005 | 空标记列表 | 空列表 | compute_holding_masks | 返回空或全 False |
| TC-ASUB-006 | 同类型多次入场 | 多个同向入场 | compute_holding_masks | 各入场区间正确 |
| TC-ASUB-007 | 入场在首次出场前未匹配 | 顺序错误标记 | compute_holding_masks | 未匹配的标记正确处理 |

#### 对齐子图渲染 (`TestAlignmentSubplot`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-ASUB-008 | 无持仓时基线在 100 | 无交易数据 | render_alignment_subplot | 基线 y=100 |
| TC-ASUB-009 | 多头跟随 P&L | 含多头交易 | render_alignment_subplot | 多头期间曲线跟随 P&L |
| TC-ASUB-010 | 空头跟随 P&L | 含空头交易 | render_alignment_subplot | 空头期间曲线跟随 P&L |
| TC-ASUB-011 | 空数据不崩溃 | 空 DataFrame | render_alignment_subplot | 不抛出异常 |
| TC-ASUB-012 | trade_records 与 masks 结合 | trade_records | render_alignment_subplot | 交易记录与遮罩匹配 |
| TC-ASUB-013 | mask 不匹配时跳过 trade_record | 不匹配数据 | render_alignment_subplot | 不匹配记录被跳过 |
| TC-ASUB-014 | 入场越界 | 入场索引超出范围 | render_alignment_subplot | 越界入场被跳过 |

---

## 5. 集成测试

### 5.1 端到端集成 (`test_integration.py`) — 6 用例

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-INT-001 | 数据管道端到端 | monkeypatch + tmp_path | 完整数据加载→存储→查询流程 | 数据正确流转 |
| TC-INT-002 | 配置预设生命周期 | tmp_path | 创建→导出→导入→应用→删除 | 每步成功且数据正确 |
| TC-INT-003 | 滤波指标管道 | 无 | 信号→滤波→指标计算 | 指标结果合理 |
| TC-INT-004 | 施密特触发管道 | 无 | 信号→滤波→schmitt→pairs | 完整的信号生成流程 |
| TC-INT-005 | 跨时间帧 P&L 对齐 | 无 | 多 TF 数据→对齐→P&L | 对齐后 P&L 正确 |
| TC-INT-006 | 预测拟合一致性 | 无 | 生成数据→拟合→验证 | 拟合参数一致性检查 |

---

### 5.2 集成流程 (`test_integration_flows.py`) — 9 用例

#### 预设应用流程 (`TestPresetApplyFlow`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-INTFLOW-001 | set_many 后 pop 应用所有参数 | real_session_state | set_many → pop | 所有参数正确应用 |
| TC-INTFLOW-002 | None 参数安全处理 | real_session_state | set_many(None值) | 不崩溃 |

#### 配置导入流程 (`TestConfigImportFlow`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-INTFLOW-003 | 批量导入 via set_many | real_session_state | 批次导入 | 所有参数写入 |
| TC-INTFLOW-004 | set 后 pop 清除两者 | real_session_state | set → pop | 键和 imp 键都被清除 |

#### 系统键一致性 (`TestSystemKeysConsistency`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-INTFLOW-005 | None 默认值不在 session 中 | real_session_state | init_defaults | None 默认键不出现 |
| TC-INTFLOW-006 | 所有非 None 系统键存在 | real_session_state | init_defaults | 非 None 键全部存在 |

#### Imp 备份端到端 (`TestImpBackupEndToEnd`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-INTFLOW-007 | pop 移除两键 | real_session_state | pop | 主键和 imp 键都被移除 |
| TC-INTFLOW-008 | imp 禁用时 set | real_session_state，imp 禁用 | set | 仅写主键 |

#### Session State 隔离 (`TestSessionStateIsolation`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-INTFLOW-009 | 独立键互不干扰 | real_session_state | 设置两对不相关键 | 各键互不影响 |

---

### 5.3 冒烟测试 (`test_app_smoke.py`) — 1 用例

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-SMOKE-001 | 应用可启动 | 完整环境 | 启动 streamlit 应用 | 进程启动不崩溃 |

---

## 6. 边界与参数测试

### 6.1 边界条件 (`test_boundary.py`) — 30 用例

#### 策略盈亏边界 (`TestComputeStrategyPnlBoundary`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-BOUND-001 | filtered 全 NaN | 全 NaN 滤波结果 | compute_strategy_pnl | 不崩溃，合理输出 |
| TC-BOUND-002 | 短序列 | 很短的价格序列 | compute_strategy_pnl | 不崩溃 |

#### 查找信号对边界 (`TestFindAllPairsBoundary`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-BOUND-003 | 零间隔合并 | 相邻信号无缝 | find_all_pairs | 正确合并 |
| TC-BOUND-004 | 单 bar 信号 | 仅 1 bar 的信号 | find_all_pairs | 正确处理短信号 |
| TC-BOUND-005 | 频繁交替 | 快速交替的信号 | find_all_pairs | 正确分段 |
| TC-BOUND-006 | 全零信号 | 全零数组 | find_all_pairs | 返回空 |
| TC-BOUND-007 | 短信号处理 | 极短信号段 | find_all_pairs | 正确处理 |

#### 拟合边界 (`TestFitBoundary`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-BOUND-008 | 抛物线拟合短段 | 点数极少 | fit_parabolic | 返回 None 或合理值 |
| TC-BOUND-009 | 抛物线拟合正常 | 有效数据 | fit_parabolic | 返回有效系数 |
| TC-BOUND-010 | 物理拟合分母为零 | 退化数据 | fit_physics_parabola | 返回 None |
| TC-BOUND-011 | 共线抛物线拟合值 | 共线三点 | fit_parabolic | a≈0，拟合合理 |

#### 施密特触发边界 (`TestSchmittTriggerBoundary`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-BOUND-012 | n < span | 数据长度小于 span | schmitt_trigger | 返回 None |
| TC-BOUND-013 | 空 v | 空输入 | schmitt_trigger | 返回 None |
| TC-BOUND-014 | 正常输出形状 | 有效输入 | schmitt_trigger | 输出长度与输入匹配 |

#### 导出导入配置 (`TestExportImportConfig`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-BOUND-015 | 导出字典结构 | session_state | export_config | 返回正确格式字典 |
| TC-BOUND-016 | 导入时创建 imp 备份 | session_state | import_config | imp 备份键被创建 |

#### 空数据降级 (`TestEmptyDataDegradation`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-BOUND-017 | schmitt 为 None 时优雅降级 | schmitt=None | 后续流程 | 不崩溃，输出合理 |

#### 数值稳定性 (`TestNumericalStability`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-BOUND-018 | 极端价格 | 极大/极小价格数 | 完整滤波+策略流程 | 数值不溢出 |
| TC-BOUND-019 | 负价格 | 含负值的价格 | 完整滤波+策略流程 | 正确处理负值 |

#### 对齐 P&L 边界 (`TestAlignPnlBoundary`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-BOUND-020 | higher 交易为空 | sample_dates_daily + sample_dates_intraday | align_pnl_to_current_tf | 返回全空 P&L |

#### 跨时间帧层级 (`TestCrossTfHierarchy`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-BOUND-021 | TF 层级链 | 多层级 TF | 验证层级链 | 链结构正确 |

#### P0 回归边界 (`TestP0RegressionBoundary`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-BOUND-022 | 空 ticker 数据加载 | 空 ticker | data_loader | 不崩溃 |
| TC-BOUND-023 | 空白 ticker 数据加载 | 空白 ticker | data_loader | 不崩溃 |
| TC-BOUND-024 | 滤波注册表未知 ID | 未知 filter_id | registry.get | 返回 None |
| TC-BOUND-025 | 滤波注册表有施密特函数 | 无 | registry.has | 施密特触发函数存在 |
| TC-BOUND-026 | 预设选择器 None 安全 | 无预设 | 处理 None 选择 | 不报错 |

#### 滤波引擎边界 (`TestFilterEngineEdgeBoundary`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-BOUND-027 | SMA 空数组 | 空数组 | sma_filter | 不崩溃 |
| TC-BOUND-028 | EMA 空数组 | 空数组 | ema_filter | 不崩溃 |
| TC-BOUND-029 | EMA 单元素 | 单元素 | ema_filter | 返回单元素 |
| TC-BOUND-030 | SMA 含 NaN | 含 NaN 数据 | sma_filter | 正确处理 NaN |

---

### 6.2 参数导出导入 (`test_param_export_import.py`) — 13 用例

#### 导出完整性 (`TestExportCompleteness`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PARAM-001 | 每个视图的所有键被导出 | session_state 含完整视图 | export | 所有视图键在导出结果中 |
| TC-PARAM-002 | 全局键被导出 | session_state | export | 全局键被包含 |
| TC-PARAM-003 | 滤波参数被导出 | session_state | export | 滤波参数被包含 |

#### Imp 备份覆盖 (`TestImpBackupCoverage`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PARAM-004 | 所有配置键都有 imp 备份 | session_state | set → 检查 imp 键 | 每个键对应 imp 键存在 |
| TC-PARAM-005 | imp 值与原始值匹配 | session_state | 对比原始值和 imp 值 | 值相等 |

#### 参数变更检测 (`TestParameterChangeDetection`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PARAM-006 | JSON 中无过期键 | 导出 JSON | 检查 JSON 键 | 所有键都在当前配置范围 |
| TC-PARAM-007 | 所有 JSON view 键匹配模式 | 导出 JSON | 正则匹配键名 | 所有键匹配预期模式 |

#### 展开/折叠参数恢复 (`TestExpandCollapseParameterRecovery`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PARAM-008 | 所有参数在 widget 丢失后可恢复 | session_state | expand → collapse → 检查参数 | 参数未丢失 |
| TC-PARAM-009 | 恢复后参数值保持不变 | session_state | expand → collapse → 验证值 | 参数值不变 |
| TC-PARAM-010 | 特定关键参数可恢复 | session_state | 验证关键参数 | 关键参数恢复正确 |
| TC-PARAM-011 | 滤波参数可恢复 | session_state | 验证滤波参数 | 滤波参数恢复正确 |

#### 导入幂等性 (`TestImportIdempotency`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-PARAM-012 | 重复导入幂等 | session_state + 导出数据 | import → import 再次 | 两次导入结果一致 |
| TC-PARAM-013 | 部分导入无残留 | session_state + 部分数据 | import(部分) → 检查 | 无意外残留值 |

---

## 7. 数据加载测试

### 7.1 数据加载器 (`test_data_loader.py`) — 30 用例

#### 股票名称查询 (`TestStockNameLookup`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-DL-001 | 空 ticker | 空字符串 | lookup_stock_name("") | 返回空字符串 |
| TC-DL-002 | 空白 ticker | 多个空格 | lookup_stock_name("   ") | 返回空字符串 |
| TC-DL-003 | A 股查询成功 | A 股代码 mock | lookup_stock_name | 返回股票名称 |
| TC-DL-004 | 深圳查询成功 | 深圳代码 mock | lookup_stock_name | 返回股票名称 |
| TC-DL-005 | 港股查询成功 | 港股代码 mock | lookup_stock_name | 返回股票名称 |
| TC-DL-006 | 美股查询成功 | 美股代码 mock | lookup_stock_name | 返回股票名称 |
| TC-DL-007 | 缺少 longName 字段 | 返回数据无 longName | lookup_stock_name | 返回空字符串 |
| TC-DL-008 | 查询异常处理 | 模拟网络异常 | lookup_stock_name | 返回空字符串 |

#### 获取股票数据 (`TestFetchStock`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-DL-009 | 空代码 | 空代码 | fetch_stock | 返回空 DataFrame |
| TC-DL-010 | ticker 构建（参数化） | 不同市场/代码组合 | fetch_stock | 完整 ticker 格式正确 |
| TC-DL-011 | yfinance 返回空数据 | mock 空返回 | fetch_stock | 返回空 DataFrame |
| TC-DL-012 | multiindex 列被展平 | mock 多级列名 | fetch_stock | 返回单级列名 |
| TC-DL-013 | 周线 close 回退生效 | mock 日线数据 | fetch_stock(weekly) | close 使用周线数据 |
| TC-DL-014 | 周线 close 回退 multiindex | mock multiindex | fetch_stock(weekly) | 正确处理多级列 |
| TC-DL-015 | 周线 close 回退空周线 | mock 空周线 | fetch_stock | 不崩溃 |
| TC-DL-016 | 周线 close 回退异常 | mock 异常 | fetch_stock | 不崩溃 |
| TC-DL-017 | 所有时间帧（参数化） | 各时间帧 | fetch_stock | interval 参数正确映射 |
| TC-DL-018 | 周期计算（参数化） | 不同 tf + n_pts | fetch_stock | period 计算正确 |
| TC-DL-019 | DB upsert 失败 | mock upsert 异常 | fetch_stock | 不崩溃，数据仍返回 |
| TC-DL-020 | upsert 后查询返回空 | mock 空查询 | fetch_stock | 不崩溃 |
| TC-DL-021 | force_period 参数 | force_period 指定 | fetch_stock | 使用指定 period |
| TC-DL-022 | yfinance 异常 | mock yfinance 异常 | fetch_stock | 返回空 DataFrame |
| TC-DL-023 | 港股 ticker | 港股代码 | fetch_stock | 正确构造 ticker |
| TC-DL-024 | 非日线间隔不触发周线回退 | 非日线 tf | fetch_stock | 周线回退不被触发 |

#### 同步到显示 (`TestSyncToDisplay`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-DL-025 | 正常同步 | tmp_path，有效数据 | sync_to_display | 数据被写入显示缓存 |
| TC-DL-026 | 少于 5 行不写入 | 仅 3 行数据 | sync_to_display | 显示缓存不更新 |

#### 获取所有时间帧 (`TestFetchAllTimeframes`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-DL-027 | 所有时间帧成功获取 | mock 正常返回 | fetch_all_timeframes | 每个 TF 都有数据 |
| TC-DL-028 | 部分失败 | mock 部分 TF 失败 | fetch_all_timeframes | 成功的 TF 有数据，失败的为空 |
| TC-DL-029 | 单个 fetch 异常 | mock 单个抛出异常 | fetch_all_timeframes | 不崩溃，其他 TF 正常 |

#### 模块级 (`TestModule`)

| 用例ID | 测试目的 | 前置条件 | 操作步骤 | 通过标准 |
|--------|---------|---------|---------|---------|
| TC-DL-030 | 模块可以正常导入 | 无 | import data_loader | 导入成功 |

---

## 附录

### A. 测试运行命令

```bash
# 运行全部测试
pytest tests/ -v

# 按模块运行
pytest tests/test_config_db.py -v
pytest tests/test_db.py -v
pytest tests/test_state.py -v

# 按标记运行
pytest tests/ -m "slow" -v
pytest tests/ -m "integration" -v

# 仅运行冒烟测试
pytest tests/test_app_smoke.py -v

# 带覆盖率
pytest tests/ --cov=. --cov-report=html
```

### B. 已知问题

- 部分 UI 测试依赖 Streamlit 的 `AppTest` fixture，需要 Streamlit >= 1.28
- 数据库测试使用临时 SQLite 文件，确保 `tmp_path` fixture 可用
- `test_streamlit_app.py` 中的 `simple_noisy` 和 `sine_signal` fixture 定义在 `conftest.py`
- 部分参数化测试（`test_fetch_stock`、`test_all_timeframes`）会根据参数组合生成多个子用例

### C. 用例统计

| 模块 | 文件 | 测试类数 | 用例数 |
|------|------|---------|--------|
| 数据持久层 | test_config_db.py | 16 | 58 |
| | test_db.py | 14 | 57 |
| | test_state.py | 11 | 59 |
| 算法核心 | test_filters.py | 6 | 22 |
| | test_signals.py | 2 | 16 |
| | test_strategy.py | 5 | 18 |
| UI 组件 | test_sidebar.py | 8 | 36 |
| | test_preset_ui.py | 14 | 60 |
| | test_preset_ui_actions.py | 7 | 45 |
| | test_app_ui.py | 8 | 33 |
| | test_streamlit_app.py | 5 | 25 |
| 可视化 | test_charts.py | 14 | 87 |
| | test_alignment.py | 1 | 6 |
| | test_alignment_subplot.py | 2 | 14 |
| 集成测试 | test_integration.py | 0 | 6 |
| | test_integration_flows.py | 5 | 9 |
| | test_app_smoke.py | 0 | 1 |
| 边界与参数 | test_boundary.py | 11 | 30 |
| | test_param_export_import.py | 5 | 13 |
| 数据加载 | test_data_loader.py | 5 | 30 |
| **合计** | **20 文件** | **139** | **600** |
