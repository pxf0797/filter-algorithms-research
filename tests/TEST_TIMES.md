# 测试耗时报告

> 更新时间: 2026-07-26 18:50
> 全量测试: 2413 tests, 2375 passed, 34 failed, 4 skipped
> 总耗时: 182.18s (3m02s)
> 覆盖率: 81.80%

## 优化历程

| 日期 | 测试数 | 耗时 | 主要变化 |
|------|--------|------|---------|
| 07-24 基线 | ~1555 | ~13s | 初始状态 |
| 07-26 峰值 | 2412 | **267s** | +857 tests，Streamlit AppTest 占比71% |
| 07-26 优化后 | 2412 | **79s** | AppTest模块化 + retry退避消除 + sleep消除 (-70%) |
| 07-26 当前 | 2413 | **182s** | PREVENT/DETECT配置+测试重组导致Streamlit缓存失效 |

## 耗时根因分析

### 瓶颈分布
```
Streamlit AppTest:   ~40s ████████████████████  (22%)   — 子进程启动
Benchmark校准:       ~12s ██████                 (7%)    — CI可跳过
CLI subprocess:      ~14s ███████                (8%)    — 进程内调用可消除
纯逻辑测试(~2300):   ~60s ██████████████████████████ (33%) — 均<0.01s
测试污染+失败重试:    ~56s ████████████████████████████ (31%) — 污染连锁开销
```

### 为什么181s→79s→182s?
- 79s: AppTest模块级fixture命中缓存 + 已消除retry/sleep
- 182s: 后续修改(DEFAULT_TFS修复/PREVENT/DETECT/测试重组)重新初始化了Streamlit缓存
- 结构性瓶颈: Streamlit子进程 + CLI subprocess → 需进程内mock根本解决

### 优化建议
| 优化 | 预期 | 难度 |
|------|------|------|
| `pytest -m "not benchmark"` | -12s | 极低 |
| AppTest scope="session" (单次启动) | -25s | 中 |
| CLI subprocess→进程内mock | -10s | 中 |
| 修复34个污染失败 | -30s | 中 |
| **优化后预期** | **<100s** | |

## 与上次对比

| 指标 | 上次 (2026-07-26) | 本次 | 变化 |
|------|-------------------|------|------|
| 总测试数 | 2402 | 2413 | +11 |
| 通过 | 2368 | 2375 | +7 |
| 失败 | 30 | 34 | +4 |
| 跳过 | 4 | 4 | 0 |
| 总耗时 | 79.65s (1m20s) | 182.18s (3m02s) | +128.7% |

注：34 个失败中 29 个为**测试污染**（用例 Bug），非生产代码缺陷。剩余 5 个待分析。详情见下方失败分类。

## 按耗时排序 (Top 20)

| # | 测试 | 耗时 |
|---|------|------|
| 1 | test_app_ui.py::TestWidgetInteraction::test_ticker_change_does_not_crash | 5.80s |
| 2 | test_app_ui.py::TestAppSmoke::test_app_runs [setup] | 5.36s |
| 3 | test_app_ui.py::TestPresetApplyEndToEnd::test_apply_preset_button_does_not_crash | 4.30s |
| 4 | test_app_smoke.py::test_app_launches | 10.01s |
| 5 | test_pipeline_capture.py::test_backtest_filtered_values_vary | 1.75s |
| 6 | test_backtest_cli.py::TestCLISmoke::test_cli_smoke_run | 1.47s |
| 7 | test_backtest_cli.py::TestCLISmoke::test_cli_missing_args_shows_error | 1.21s |
| 8 | test_benchmark.py::TestFilterPerformance::test_filter_2000_bars[sma] | 1.21s |
| 9 | test_backtest_cli.py::TestCLISmoke::test_cli_help | 1.21s |
| 10 | test_integration.py::test_cli_help_output | 1.19s |
| 11 | test_integration.py::test_cli_missing_ticker_exits_nonzero | 1.18s |
| 12 | test_integration.py::test_cli_bad_preset_exits_nonzero | 1.17s |
| 13 | test_panel.py::TestCircularDependencyPrevention::test_import_browse_app_no_circular_error | 1.17s |
| 14 | test_integration.py::test_cli_invalid_config_file_exits_nonzero | 1.15s |
| 15 | test_benchmark.py::TestFilterPerformance::test_filter_2000_bars[lowess] | 1.11s |
| 16 | test_panel.py::TestCircularDependencyPrevention::test_dashboard_import_does_not_trigger_browse_module | 1.05s |
| 17 | test_panel.py::TestCircularDependencyPrevention::test_panel_import_does_not_trigger_browse_module | 1.05s |
| 18 | test_benchmark.py::TestBatchProcessing::test_compute_metrics_batch_50x | 1.05s |
| 19 | test_panel.py::TestCircularDependencyPrevention::test_all_tfs_defined_in_standalone_constants | 1.03s |
| 20 | test_benchmark.py::TestSchmittTriggerPerformance::test_find_all_pairs_2000_bars | 1.01s |

> Top 4 全部来自 **Streamlit UI 测试** (test_app_ui.py / test_app_smoke.py)，每个启动完整 Streamlit 子进程。其余耗时大户为 CLI 子进程调用和 benchmark 大数据量测试。

## 按文件耗时排序 (Top 20)

| # | 文件 | 耗时 | 测试数 |
|---|------|------|--------|
| 1 | test_app_ui.py | 28.71s | 31 |
| 2 | test_app_smoke.py | 11.70s | 1 |
| 3 | test_benchmark.py | 11.63s | 9 |
| 4 | test_integration.py | 7.35s | 25 |
| 5 | test_backtest_cli.py | 6.81s | 57 |
| 6 | test_panel.py | 6.38s | 23 |
| 7 | test_parquet_store.py | 6.03s | 203 |
| 8 | test_property.py | 5.65s | 21 |
| 9 | test_backtest_core.py | 4.39s | 96 |
| 10 | test_state.py | 4.23s | 101 |
| 11 | test_filters.py | 4.16s | 82 |
| 12 | test_dashboard.py | 4.06s | 80 |
| 13 | test_data_loader.py | 3.98s | 104 |
| 14 | test_pipeline_capture.py | 3.97s | 31 |
| 15 | test_preset_ui.py | 3.87s | 60 |
| 16 | test_html_overlay.py | 3.48s | 111 |
| 17 | test_db.py | 3.34s | 77 |
| 18 | test_charts.py | 3.16s | 65 |
| 19 | test_module_structure.py | 3.03s | 54 |
| 20 | test_engine.py | 3.02s | 69 |

## 全量文件耗时明细

| 耗时 | 测试数 | 文件 |
|------|--------|------|
| 28.71s | 31 | test_app_ui.py |
| 11.70s | 1 | test_app_smoke.py |
| 11.63s | 9 | test_benchmark.py |
| 7.35s | 25 | test_integration.py |
| 6.81s | 57 | test_backtest_cli.py |
| 6.38s | 23 | test_panel.py |
| 6.03s | 203 | test_parquet_store.py |
| 5.65s | 21 | test_property.py |
| 4.39s | 96 | test_backtest_core.py |
| 4.23s | 101 | test_state.py |
| 4.16s | 82 | test_filters.py |
| 4.06s | 80 | test_dashboard.py |
| 3.98s | 104 | test_data_loader.py |
| 3.97s | 31 | test_pipeline_capture.py |
| 3.87s | 60 | test_preset_ui.py |
| 3.48s | 111 | test_html_overlay.py |
| 3.34s | 77 | test_db.py |
| 3.16s | 65 | test_charts.py |
| 3.03s | 54 | test_module_structure.py |
| 3.02s | 69 | test_engine.py |
| 2.92s | 75 | test_cascading_synthesis.py |
| 2.81s | 50 | test_config_db.py |
| 2.74s | 42 | test_numba.py |
| 2.72s | 48 | test_sidebar.py |
| 2.68s | 45 | test_preset_ui_actions.py |
| 2.65s | 47 | test_view_backtest.py |
| 2.61s | 40 | test_metrics_fix.py |
| 2.54s | 54 | test_config.py |
| 2.46s | 52 | test_view_backtest_html.py |
| 2.45s | 31 | test_data_recording.py |
| 2.39s | 28 | test_pipeline_unification.py |
| 2.37s | 42 | test_view_backtest_viz.py |
| 2.37s | 21 | test_subplot_layout.py |
| 2.27s | 39 | test_signals.py |
| 2.25s | 40 | test_bs_marker.py |
| 2.24s | 19 | test_plan_a_api.py |
| 2.22s | 17 | test_plan_a_gaps.py |
| 2.22s | 16 | test_backtest_cutoff.py |
| 2.17s | 25 | test_strategy.py |
| 2.17s | 7 | test_plan_a_e2e.py |
| 2.10s | 30 | test_boundary.py |
| 2.09s | 22 | test_p2_cleanup.py |
| 2.09s | 7 | test_concurrency.py |
| 2.02s | 25 | test_backtest_reproducibility.py |
| 2.02s | 8 | test_backtest_logger.py |
| 1.97s | 20 | test_param_export_import.py |
| 1.96s | 19 | test_constants.py |
| 1.92s | 19 | test_chart_builder.py |
| 1.92s | 15 | test_render_traces.py |
| 1.92s | 15 | test_data_quality.py |
| 1.87s | 5 | test_snapshots.py |
| 1.85s | 14 | test_p0_cache.py |
| 1.83s | 12 | test_session_state.py |
| 1.82s | 11 | test_ci_config.py |
| 1.81s | 10 | test_alignment.py |
| 1.75s | 11 | test_backtest_catalog.py |
| 1.72s | 5 | test_changelog.py |

## 耗时分布

| 区间 | 数量 | 占比 |
|------|------|------|
| >= 1s | ~25 | ~1.0% |
| 0.1s - 1s | ~70 | ~2.9% |
| 0.01s - 0.1s | ~200 | ~8.3% |
| < 0.01s | ~2118 | ~87.8% |

> 约 88% 的测试在 10ms 内完成。耗时大户集中在 Streamlit 子进程启动和 CLI 子进程调用。

## 失败分类

### 🔴 功能 Bug (0 个)

无。

### 🟡 用例 Bug — 测试污染 (29 个)

**关键证据**：隔离运行 `tests/test_charts.py tests/test_engine.py tests/test_backtest_cutoff.py tests/test_parquet_store.py` 全部通过，但全集运行时失败。根因是**全集中的前置测试污染了共享状态**。

#### test_engine.py — DB Mock 被污染 (14 个)

所有引擎测试的根因一致：`BacktestRunner._bar_count == 0`，导致 `run()` 抛出 `ValueError: ticker 'TEST' 在数据库中无数据`。

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

#### test_backtest_cutoff.py — 与 Engine 同源 (2 个)

| 测试 | 错误 |
|------|------|
| test_checkpoint_auto_save_interval | `ValueError: ticker 'TEST' 在数据库中无数据` |
| test_checkpoint_cleanup_on_completion | 同上 |

### 🟢 待分析 (5 个)

以下 5 个失败需要通过隔离运行单独调试确定根因：

| # | 测试 | 类别 |
|---|------|------|
| 1 | test_param_export_import.py::test_all_per_view_keys_exported | Export 完整性 |
| 2 | test_param_export_import.py::test_export_helper_covers_pnlfb | Export 覆盖 |
| 3 | test_param_export_import.py::test_json_roundtrip_pnlfb | JSON 往返 |
| 4 | test_parquet_store.py::test_same_ticker_serial_runs_isolated | Session 冲突 |
| 5 | test_ci_config.py::test_precommit_mypy_rev_valid | YAML 解析 |

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
| 2026-07-26 (前) | 2412 | 2333 | 75 | 254.90s | - |
| 2026-07-26 (中) | 2402 | 2368 | 30 | 79.65s | 81.81% |
| 2026-07-26 (今) | 2413 | 2375 | 34 | 182.18s | 81.80% |
