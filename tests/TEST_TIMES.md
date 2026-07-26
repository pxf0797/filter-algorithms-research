# 测试耗时报告

> 更新时间: 2026-07-26
> 全量测试: 2412 tests, 254.90s (4m15s)
> 通过: 2333 / 失败: 75 / 跳过: 4
> 覆盖率: 待获取

## 按耗时排序 (Top 30)

| # | 测试 | 耗时 |
|---|------|------|
| 1 | test_app_ui.py::TestRefreshButtonEndToEnd::test_refresh_button_click_does_not_crash [setup] | 16.71s |
| 2 | test_app_ui.py::TestRefreshButtonEndToEnd::test_refresh_button_clears_cache [call] | 10.91s |
| 3 | test_app_ui.py::TestAppSmoke::test_title_present [setup] | 10.87s |
| 4 | test_app_smoke.py::test_app_launches [call] | 10.01s |
| 5 | test_app_ui.py::TestWidgetInteraction::test_ticker_change_does_not_crash [call] | 9.79s |
| 6 | test_app_ui.py::TestPresetApplyEndToEnd::test_apply_preset_button_does_not_crash [setup] | 8.40s |
| 7 | test_app_ui.py::TestRefreshButtonEndToEnd::test_refresh_button_click_does_not_crash [call] | 7.64s |
| 8 | test_app_ui.py::TestSidebarInteraction::test_dual_filter_checkbox_present [setup] | 6.91s |
| 9 | test_app_ui.py::TestPresetInteraction::test_preset_options_not_empty [setup] | 6.24s |
| 10 | test_app_ui.py::TestAppSmoke::test_sidebar_present [setup] | 5.99s |
| 11 | test_app_ui.py::TestSessionState::test_config_initialized [setup] | 5.87s |
| 12 | test_app_ui.py::TestAutoRefreshSafety::test_auto_refresh_checkbox_toggle [setup] | 5.76s |
| 13 | test_app_ui.py::TestWidgetInteraction::test_filter_change_does_not_crash [setup] | 5.50s |
| 14 | test_app_ui.py::TestSidebarInteraction::test_auto_refresh_checkbox_present [setup] | 5.39s |
| 15 | test_app_ui.py::TestPresetApplyEndToEnd::test_apply_preset_button_does_not_crash [call] | 5.19s |
| 16 | test_app_ui.py::TestPresetInteraction::test_preset_selector_present [setup] | 4.82s |
| 17 | test_app_ui.py::TestExceptionPathCoverage::test_invalid_ticker_does_not_crash [setup] | 4.67s |
| 18 | test_app_ui.py::TestSidebarInteraction::test_ticker_input_present [setup] | 4.66s |
| 19 | test_app_ui.py::TestPresetInteraction::test_no_preset_selection_safe [setup] | 4.65s |
| 20 | test_app_ui.py::TestSidebarInteraction::test_filter_selector_present [setup] | 4.36s |
| 21 | test_app_ui.py::TestRefreshButtonEndToEnd::test_refresh_button_clears_cache [setup] | 4.29s |
| 22 | test_app_ui.py::TestP0RegressionExtended::test_empty_ticker_safe [setup] | 4.11s |
| 23 | test_app_ui.py::TestAppSmoke::test_app_runs [setup] | 3.99s |
| 24 | test_app_ui.py::TestBackupRestoreButtons::test_create_backup_button_exists_and_clickable [setup] | 3.98s |
| 25 | test_app_ui.py::TestPresetInteraction::test_preset_options_include_none [setup] | 3.95s |
| 26 | test_app_ui.py::TestIsolationRegression::test_multiple_reruns_consistent [setup] | 3.94s |
| 27 | test_app_ui.py::TestDeleteBackupEdgeCase::test_delete_backup_with_missing_file_handled [setup] | 3.86s |
| 28 | test_app_ui.py::TestP0Regression::test_app_does_not_crash_before_render [setup] | 3.55s |
| 29 | test_app_ui.py::TestAppSmoke::test_main_present [setup] | 3.47s |
| 30 | test_app_ui.py::TestP0RegressionExtended::test_unknown_filter_setting [setup] | 3.45s |

> Top 30 全部来自 **Streamlit UI 测试** (test_app_ui.py / test_app_smoke.py)。每个 setup 阶段启动完整 Streamlit 子进程，是主要耗时瓶颈。

## 耗时分布

| 区间 | 数量 | 占比 |
|------|------|------|
| <0.01s | 6962 | 96.2% |
| 0.01-0.1s | 175 | 2.4% |
| 0.1-1s | 41 | 0.6% |
| 1-10s | 53 | 0.7% |
| >10s | 4 | 0.1% |

> 96.2% 的测试阶段在 0.01s 内完成。耗时 >1s 的 57 个条目几乎全部是 Streamlit 子进程的 setup/call 开销。

## 优化建议

1. **Streamlit 测试复用进程** (最大收益): test_app_ui.py 的 33 个测试各启动独立子进程，改用 `session-scoped fixture` 共享一个 Streamlit 进程可节省 ~100s。
2. **并行化非 UI 测试**: 排除 Streamlit 测试文件后，使用 `pytest-xdist -n auto` 可将剩余 ~2300 测试加速 3-5 倍。
3. **修复失败测试**: 75 个失败测试（主要集中在 test_charts.py / test_engine.py / test_pipeline_capture.py / test_parquet_store.py）增加了调试和重跑成本。
4. **pytest-benchmark 开销**: 18 个 benchmark 测试使用校准预热，可考虑在 CI 中跳过 (`-m "not benchmark"`)。

## 历史对比

| 日期 | 总测试数 | 总耗时 | 变化 |
|------|---------|--------|------|
| 2026-07-24 | ~1555 | ~13s | baseline |
| 2026-07-26 | 2412 | 254.90s | +857 tests, +241.90s |
