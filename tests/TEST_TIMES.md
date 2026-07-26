# 测试耗时报告

> 更新时间: 2026-07-26
> 全量测试: 2402 tests, 79.65s (1m20s)
> 通过: 2368 / 失败: 30 / 跳过: 4
> 覆盖率: 81.81% (5476 stmts, 996 miss)

## 与上次对比

| 指标 | 上次 (2026-07-26) | 本次 | 变化 |
|------|-------------------|------|------|
| 总测试数 | 2412 | 2402 | -10 |
| 通过 | 2333 | 2368 | +35 |
| 失败 | 75 | 30 | -45 |
| 跳过 | 4 | 4 | 0 |
| 总耗时 | 254.90s (4m15s) | 79.65s (1m20s) | -68.8% |

注：30 个失败全部为**测试污染**（用例 Bug），非生产代码缺陷。详情见下方失败分类。

## 按耗时排序 (Top 20)

| # | 测试 | 耗时 |
|---|------|------|
| 1 | test_app_ui.py::TestAppSmoke::test_app_runs [setup] | 16.57s |
| 2 | test_app_smoke.py::test_app_launches [call] | 10.01s |
| 3 | test_app_ui.py::TestPresetApplyEndToEnd::test_apply_preset_button_does_not_crash [call] | 7.53s |
| 4 | test_app_ui.py::TestWidgetInteraction::test_ticker_change_does_not_crash [call] | 5.40s |
| 5 | test_pipeline_baseline_parity.py::test_backtest_filtered_values_vary | 1.71s |
| 6 | test_backtest_cli.py::TestCLISmoke::test_cli_smoke_run | 1.43s |
| 7 | test_panel.py::TestCircularDependencyPrevention::test_import_browse_app_no_circular_error | 1.28s |
| 8 | test_benchmark.py::TestFilterPerformance::test_filter_2000_bars[sma] | 1.20s |
| 9 | test_backtest_cli.py::TestCLISmoke::test_cli_missing_args_shows_error | 1.17s |
| 10 | test_integration.py::test_cli_help_output | 1.16s |
| 11 | test_integration.py::test_cli_missing_ticker_exits_nonzero | 1.15s |
| 12 | test_integration.py::test_cli_invalid_config_file_exits_nonzero | 1.15s |
| 13 | test_backtest_cli.py::TestCLISmoke::test_cli_help | 1.15s |
| 14 | test_integration.py::test_cli_bad_preset_exits_nonzero | 1.14s |
| 15 | test_benchmark.py::TestFilterPerformance::test_filter_2000_bars[lowess] | 1.10s |
| 16 | test_benchmark.py::TestBatchProcessing::test_compute_metrics_batch_50x | 1.05s |
| 17 | test_panel.py::TestCircularDependencyPrevention::test_panel_import_does_not_trigger_browse_module | 1.03s |
| 18 | test_panel.py::TestCircularDependencyPrevention::test_all_tfs_defined_in_standalone_constants | 1.02s |
| 19 | test_panel.py::TestCircularDependencyPrevention::test_dashboard_import_does_not_trigger_browse_module | 1.02s |
| 20 | test_benchmark.py::TestSchmittTriggerPerformance::test_schmitt_trigger_2000_bars | 0.94s |

> Top 4 全部来自 **Streamlit UI 测试** (test_app_ui.py / test_app_smoke.py)，每个启动完整 Streamlit 子进程。

## 耗时分布

| 区间 | 数量 | 占比 |
|------|------|------|
| >= 1s | 19 | ~0.8% |
| 0.1s - 1s | ~60 | ~2.5% |
| 0.01s - 0.1s | ~180 | ~7.5% |
| < 0.01s | ~2143 | ~89.2% |

> 约 89% 的测试在 10ms 内完成。耗时大户集中在 Streamlit 子进程启动和 CLI 子进程调用。

## 失败分类

### 🔴 功能 Bug (0 个)

无。所有失败均为测试污染，非生产代码缺陷。

### 🟡 用例 Bug — 测试污染 (30 个)

**关键证据**：隔离运行 `tests/test_charts.py tests/test_engine.py tests/test_backtest_cutoff.py tests/test_parquet_store.py` 全部通过（352 passed, 1 skipped, 1.63s），但全集运行时 30 个失败。根因是**全集中的前置测试污染了共享状态**。

#### test_engine.py — DB Mock 被污染 (14 个)

所有引擎测试的根因一致：`BacktestRunner._bar_count == 0`，导致 `run()` 在第一步就抛出 `ValueError: ticker 'TEST' 在数据库中无数据`。日志明确显示 `bar_count=0`。

| 测试 | 错误 |
|------|------|
| test_bar_count_queried | `assert 0 == 500` |
| test_get_bar_count_public_method | `assert 0 == 777` |
| test_run_negative_start_bar_raises | `ValueError: ticker 'TEST' 在数据库中无数据` |
| test_run_end_bar_beyond_range_raises | 同上 |
| test_run_empty_range_returns_empty_list | 同上 |
| test_run_with_step_interval | 同上 |
| test_run_result_keys | 同上 |
| test_replay_bar_bar_index_below_max_n_pts_adjusted | 同上 -> `assert None is not None` |
| test_bs_markers_passed_holding_masks_when_provided | `AttributeError: 'NoneType' object has no attribute 'kwargs'` |
| test_file_exists_cache_not_rechecked | `assert 3 == 2`（缓存失效） |
| test_file_exists_cache_updated_on_success | `assert None is not None` |
| test_sorted_window_cache_hit | `KeyError: '日线'` |
| test_sorted_window_cache_miss_on_changed_data | `KeyError: '日线'` |
| test_sorted_views_matches_runtime_sort | `bar_count=0` |

疑似污染源：`test_config_db.py` 或 `test_integration.py` 中的 `get_conn` mock 未正确恢复。

#### test_charts.py — Plotly/Streamlit Mock 被污染 (13 个)

渲染函数 `_render_plotly` 返回空 HTML，`_capture_html` 回调未被触发。

| 测试 | 错误 |
|------|------|
| test_fallback_html_structure | `AssertionError: _render_plotly 应产生 HTML 输出` |
| test_timeout_safety_check | `assert ''` |
| test_iife_wrapping_is_valid | `assert ''` |
| test_render_plotly_with_nan_values | `assert 'html' in {}` |
| test_render_plotly_empty_data | `assert 'html' in {}` |
| test_render_plotly_with_dates | `assert 'html' in {}` |
| test_cdn_js_in_html_output | `assert 'cdn.plot.ly' in ''` |
| test_fallback_div_in_html_output | `assert 'plotly-fallback-' in ''` |
| test_render_plotly_json_parsable_by_plotly_io | `HTML 输出不应为空` |
| test_exact_keys_stripped | `assert None is not None` |
| test_template_stripped_when_string_match | `assert None is not None` |
| test_non_matching_keys_preserved | `assert None is not None` |
| test_visual_data_equivalence_after_strip | `assert None is not None` |

疑似污染源：`test_app_ui.py` 的 conftest 中对 `st.components.v1.html` 的 monkeypatch 在模块级别未恢复。

#### test_backtest_cutoff.py — 与 Engine 同源 (2 个)

| 测试 | 错误 |
|------|------|
| test_checkpoint_auto_save_interval | `ValueError: ticker 'TEST' 在数据库中无数据` |
| test_checkpoint_cleanup_on_completion | 同上 |

#### test_parquet_store.py — Session Dir 冲突 (1 个)

| 测试 | 错误 |
|------|------|
| test_same_ticker_serial_runs_isolated | 两次运行使用相同的 session 目录（时间戳相同到秒级，UUID 一致） |

### 🟢 环境问题 (0 个)

无。

## 修复建议

1. **引擎测试污染**：在各 test 文件级别使用 `pytest.mark.usefixtures` 或在 `conftest.py` 中添加 `autouse` fixture 来确保 `get_conn` mock 在每个模块后被重置。
2. **图表测试污染**：`test_app_ui.py` 的 `conftest.py` 中对 `st.components.v1.html` 的 monkeypatch 应在 session/module scope 的 teardown 中恢复。
3. **Parquet session 冲突**：在 mock 中添加毫秒级时间精度或随机后缀，避免同秒运行冲突。
4. **通用建议**：在 CI 中增加 `pytest-randomly` 插件运行以暴露顺序依赖。

## 历史对比

| 日期 | 总测试数 | 通过 | 失败 | 总耗时 | 覆盖率 |
|------|---------|------|------|--------|--------|
| 2026-07-24 | ~1555 | - | - | ~13s | - |
| 2026-07-26 (前) | 2412 | 2333 | 75 | 254.90s | 待获取 |
| 2026-07-26 (今) | 2402 | 2368 | 30 | 79.65s | 81.81% |
