# 测试完整性审计报告

审计日期: 2026-07-11
项目: filter_research (量化滤波研究, Streamlit + Plotly + SQLite)

---

## 1. 概览

| 指标 | 值 |
|---|---|
| 总用例数 | **815** |
| 通过数 | **815** |
| 失败数 | **0** |
| 通过率 | **100%** |
| 测试文件数 | **23** (不含 conftest.py) |
| pytest 耗时 | 130.6s (2m10s) |

测试文件清单: `test_filters.py`, `test_signals.py`, `test_strategy.py`, `test_charts.py`, `test_sidebar.py`, `test_state.py`, `test_db.py`, `test_config_db.py`, `test_data_loader.py`, `test_cascading_synthesis.py`, `test_backtest.py`, `test_streamlit_app.py`, `test_alignment.py`, `test_alignment_subplot.py`, `test_preset_ui.py`, `test_preset_ui_actions.py`, `test_feedback_subplot.py`, `test_boundary.py`, `test_param_export_import.py`, `test_app_ui.py`, `test_app_smoke.py`, `test_integration.py`, `test_integration_flows.py`, `test_charts.py` (重), `test_preset_ui.py` (重).

---

## 2. 按模块覆盖表

### 2.1 filter_engine.py (services/filter_engine.py) — 20 个函数

| 关键函数 | 类型 | 有无测试 | 测试文件/类 |
|---|---|---|---|
| `apply_sma` / `apply_ema` / `apply_wma` / `apply_alma` / `apply_savgol` / `apply_kalman` / `apply_butterworth` / `apply_gaussian` / `apply_median` / `apply_lowess` | 滤波器 | 有 | `test_filters.py`: `TestConstantSignal`, `TestNoiseReduction`, `TestEdgeCases`, `TestSavgolSpecial`, `TestKalmanSpecial` |
| `compute_metrics` | 指标 | 有 | `test_filters.py`: `TestComputeMetrics` |
| `_schmitt_trigger` | 核心信号 | 有 | `test_signals.py`: `TestSchmittTrigger`; `test_boundary.py`: `TestSchmittTriggerBoundary` |
| `_find_all_pairs` | 核心配对 | 有 | `test_signals.py`: `TestFindAllPairs`; `test_boundary.py`: `TestFindAllPairsBoundary` |
| `_fit_parabolic` | 抛物线拟合 | 有 | `test_strategy.py`: `TestFitParabolic`; `test_boundary.py`: `TestFitBoundary` |
| `_fit_physics_parabola` | 物理抛物线 | 有 | `test_strategy.py`: `TestFitPhysicsParabola`; `test_boundary.py`: `TestFitBoundary` |
| `_compute_strategy_pnl` | 核心策略 | 有 | `test_strategy.py`: `TestComputeStrategyPnL`; `test_boundary.py`: `TestComputeStrategyPnlBoundary` |
| `_align_pnl_to_current_tf` | 跨周期对齐 | 有 | `test_alignment.py`: `TestAlignPnlToCurrentTf`, `TestEodHigherPositionExtend`, `TestPreWindowEntryExtend`; `test_boundary.py`: `TestAlignPnlBoundary` |
| `_compute_holding_masks` | 持仓掩码 | 有 | `test_alignment_subplot.py`: `TestComputeHoldingMasks` |

### 2.2 streamlit_app.py — 60+ 个函数

| 关键函数 | 类型 | 有无测试 | 测试文件/类 |
|---|---|---|---|
| `_date_markers` | 日期标记 | 有 | `test_streamlit_app.py`: `TestDateMarkers` |
| `_load_chart_data` | 加载数据 | 间接 | 通过 `_fetch_stock` 在 `test_data_loader.py` 间接测试 |
| `_compute_filters` | 计算滤波 | 有 | `test_streamlit_app.py`: `TestComputeFilters` |
| `_compute_schmitt_trigger` | 舒密特触发 | 有 | `test_streamlit_app.py`: `TestComputeSchmittTrigger` |
| `_compute_prediction_pairs` | 预测配对 | 有 | `test_streamlit_app.py`: `TestComputePredictionPairs` |
| `_compute_strategy_display` | 策略显示 | 无 | — |
| `_determine_subplot_layout` | 子图布局 | 有 | `test_streamlit_app.py`: `TestDetermineSubplotLayout` |
| `_insert_feedback_row` | 反馈行插入 | 有 | `test_feedback_subplot.py`: `test_insert_feedback_row_shifts_rows` |
| `_add_main_price_traces` | K线/价格轨迹 | **无** | — |
| `_add_residual_traces` | 残差轨迹 | **无** | — |
| `_add_schmitt_traces` | 舒密特轨迹 | 有 | `test_charts.py`: `TestSchmittTraces` |
| `_add_pnl_traces` | PnL 可视化 | **无** | — |
| `_add_feedback_subplot` | 反馈子图 | 有 | `test_feedback_subplot.py`: `test_add_feedback_subplot_*` |
| `_get_min_tf_and_count` | 最小周期解析 | **无** | — |
| `_save_backtest_config` | 回测配置写盘 | **无** | — |
| `_load_backtest_config` | 回测配置读盘 | **无** | — |
| `_render_chart_fragment` | 图表渲染片段 | **无** | — |
| `_render_chart` | 图表渲染 | **无** | — |
| `_get_db_connection` | 数据库连接 | **无** | — |
| `_handle_pending_apply` | 待定应用 | 间接 | `test_integration_flows.py`: `TestPresetApplyFlow` |
| `_render_config_import` | 配置导入 UI | **无** | — |
| `_render_market_ticker` | 市场/股票选择器 | **无** | — |
| `_handle_initial_fetch` | 初次抓取 | **无** | — |
| `_render_refresh_row` | 刷新行 | **无** | — |
| `_render_preset_selector` | 预设选择器 | **无** | — |
| `_render_health_check` | 健康检查 UI | **无** | — |
| `_render_data_validation` | 数据验证 UI | **无** | — |
| `_render_filter_selectors` | 滤波器选择 UI | **无** | — |
| `_render_param_panels` | 参数面板 | **无** | — |
| `_render_time_nav` | 时间导航 | 有 | `test_backtest.py`: `TestRenderTimeNav` |
| `_get_bar_date_from_db` | 时间线查询 | 有 | `test_backtest.py`: `TestGetBarDateFromDb` |
| `_update_cutoff_and_rerun` | 截止时间更新 | 有 | `test_backtest.py`: `TestUpdateCutoffAndRerun` |
| `_on_slider_change` | 滑块回调 | 有 | `test_backtest.py`: `TestOnSliderChange` |
| `_render_backtest_nav` | 回测导航 | 间接 | `test_backtest.py`: `TestBacktestNavButtons` |
| `_run_backtest_play` | 回测播放 | 有 | `test_backtest.py`: `TestRunBacktestPlay`, `TestPlayback` |
| `_render_backtest_mode` | 回测模式 | 部分 | `test_backtest.py`: `TestRenderBacktestModeSlider` (patch 了内部) |
| `_render_db_backup` | 数据库备份 UI | **无** | — |
| `_view_export_params` | 导出参数 | 有 | `test_param_export_import.py`: `TestExportCompleteness`, `TestParamRegistryGuard` |
| `_render_export_config` | 导出配置 UI | **无** | — |
| `_render_config_history` | 配置历史 UI | **无** | — |
| `_render_db_import_export` | 数据库导入导出 UI | **无** | — |
| `_run_auto_refresh` | 自动刷新 | **无** | — |
| `main` | 入口函数 | **无** | 已知缺口 (见 tests/README.md) |

### 2.3 charts.py (components/charts.py)

| 关键函数 | 有无测试 | 测试文件/类 |
|---|---|---|
| `_render_plotly` | 有 | `test_charts.py`: `TestRenderPlotlyHtml`, `TestRenderPlotlySerialization` |
| `_sanitize_for_json` | 有 | `test_charts.py`: `TestSanitizeForJson` |
| `_add_prediction_traces` | 有 | `test_charts.py`: `TestPredictionTraces`; `test_strategy.py`: `TestAddPredictionTraces` |
| `_render_entry_marker` | 有 | `test_charts.py`: `TestRenderEntryMarker` |
| `_render_exit_marker_with_label` | 有 | `test_charts.py`: `TestRenderExitMarker` |
| `_render_pnl_curves` | 有 | `test_charts.py`: `TestRenderPnLCurves` |
| `_render_baseline` | 有 | `test_charts.py`: `TestRenderBaseline` |
| `_render_fill_background` | 有 | `test_charts.py`: `TestRenderFillBackground` |
| `_contiguous_runs` | 有 | `test_feedback_subplot.py`: `test_contiguous_runs` |
| `_draw_holding_bands` | 有 | `test_feedback_subplot.py`: `test_draw_holding_bands_shapes`, `_empty` |
| `_add_cross_pnl_subplot` | 有 | `test_charts.py`: `TestCrossPnlSubplot`, `TestCrossPnlSubplotTrades` |
| `_add_alignment_subplot` | 有 | `test_charts.py`: `TestAlignmentSubplot`; `test_alignment_subplot.py`: `TestAlignmentSubplot` |
| `NpEncoder` | 有 | `test_charts.py`: `TestNpEncoder` |

### 2.4 sidebar.py (components/sidebar.py)

| 关键函数 | 有无测试 | 测试文件/类 |
|---|---|---|
| `_compact_slider` | 有 | `test_sidebar.py`: `TestCompactSlider`, `TestCompactSliderFormatLogic` |
| `_render_param_slider` | 有 | `test_sidebar.py`: `TestRenderParamSlider`, `TestRenderParamSliderLogic` |
| `_render_params` | 有 | `test_sidebar.py`: `TestRenderParams` |

### 2.5 data_loader.py (services/data_loader.py)

| 关键函数 | 有无测试 | 测试文件/类 |
|---|---|---|
| `_fetch_all_timeframes` / `_fetch_one` | 有 | `test_data_loader.py`: `TestFetchAllTimeframes` |
| `_fetch_stock` | 有 | `test_data_loader.py`: `TestFetchStock` |
| `_sync_to_display` | 有 | `test_data_loader.py`: `TestSyncToDisplay` |
| `_stock_name_lookup` | 有 | `test_data_loader.py`: `TestStockNameLookup` |
| `_offset_to_tz` | 有 | `test_cascading_synthesis.py`: `TestOffsetToTz` |
| `_ensure_tz_naive` | 有 | `test_cascading_synthesis.py`: `TestEnsureTzNaive` |
| `_get_tz_suffix` | 有 | `test_cascading_synthesis.py`: `TestGetTzSuffix` |
| `_format_synth_date` | 有 | `test_cascading_synthesis.py`: `TestFormatSynthDate` |
| `_get_period_start_ts` | 有 | `test_cascading_synthesis.py`: `TestGetPeriodStartTs` |
| `_get_query_start_for_synthesis` | 有 | `test_cascading_synthesis.py`: `TestGetQueryStartForSynthesis` |
| `_query_tf_from_db` / `_query_tf_for_period` | 有 | 间接通过 cascade 端到端测试 |
| `_find_immediate_finer_tf` | 有 | `test_cascading_synthesis.py`: `TestFindImmediateFinerTf` |
| `_needs_synthesis` | 有 | `test_cascading_synthesis.py`: `TestNeedsSynthesis`, `TestNeedsSynthesisRegression`, `TestBoundarySynthesis` |
| `_aggregate_bars` | 有 | `test_cascading_synthesis.py`: `TestAggregateBars` |
| `_synthesize_incomplete_bar` | 有 | 间接通过 `TestCascadeEndToEnd`, `TestCrossPeriodFilter` |
| `_build_output_df` | 有 | `test_cascading_synthesis.py`: `TestBuildOutputDf` |
| `_write_parquet` | 无 | — |
| `_sync_all_cascading` | 有 | 间接通过 `TestCascadeEndToEnd` |

### 2.6 config_db.py

| 关键函数 | 有无测试 | 测试文件/类 |
|---|---|---|
| `init_config_tables` | 有 | `test_config_db.py`: `TestInitConfigTables`, `TestInitConfigTablesMigration`, `TestMainBlock` |
| `list_presets` / `get_preset` / `get_preset_by_name` / `save_preset` / `delete_preset` / `rename_preset` | 有 | `test_config_db.py`: `TestPresetCRUD`, `TestRenamePresetValidation`, `TestDeletePresetReturnValue`; `test_preset_ui.py`: 大量生命周期测试 |
| `apply_preset` | 有 | `test_config_db.py`; `test_preset_ui.py`: `TestApplyPresetSessionState` |
| `load_ticker_config` / `save_ticker_config` | 有 | `test_config_db.py`: `TestTickerConfig`, `TestTickerConfigEdgeCases` |
| `record_history` / `get_history` | 有 | `test_config_db.py`: `TestHistory` |
| `import_json_files_as_presets` | 有 | `test_config_db.py`: `TestImportJSONFiles`, `TestImportJSONFilesReturnValue`, `TestImportJSONFilesEdgeCases` |
| `collect_current_params` | 有 | `test_config_db.py`: `TestCollectCurrentParams`; `test_preset_ui.py`: `TestSessionStateEdgeCases`, `TestCollectParamsCompleteness` |
| `VIEW_PARAM_SPECS` (常量) | 有 | `test_param_export_import.py`: `TestParamRegistryGuard` |

### 2.7 db.py

| 关键函数 | 有无测试 | 测试文件/类 |
|---|---|---|
| `get_conn` / `init_db` | 有 | `test_db.py`: `TestInitDb` |
| `upsert_kline` | 有 | `test_db.py`: `TestUpsertKline, TestEdgeCases, TestConcurrentAccess` |
| `query_kline` | 有 | `test_db.py`: `TestQueryKline` |
| `get_date_range` / `has_data` | 有 | `test_db.py`: `TestHelpers` |
| `check_data_health` | 有 | `test_db.py`: `TestCheckDataHealth` |
| `validate_db` | 有 | `test_db.py`: `TestValidateDb` |
| `snapshot_db` / `list_snapshots` / `restore_snapshot` / `prune_snapshots` | 有 | `test_db.py`: `TestSnapshotBackup` |
| `checkpoint_wal` | 有 | `test_db.py`: `TestCheckpointWal` |
| `get_db_size_mb` | 有 | `test_db.py`: `TestGetDbSize` |
| `clear_display_cache` | 有 | `test_db.py`: `TestClearDisplayCache` |
| `compare_with_db` | 有 | `test_db.py`: `TestCompareWithDb` |
| `force_update_kline` | 有 | `test_db.py`: `TestForceUpdateKline` |

### 2.8 state.py

| 关键函数 | 有无测试 | 测试文件/类 |
|---|---|---|
| `AppState.init_defaults` / `get` / `set` / `set_many` / `has` / `pop` / `get_global` / `get_view_key` | 有 | `test_state.py`: `TestAppStateInitDefaults`, `TestAppStateHas`, `TestAppStateGet`, `TestAppStateSet`, `TestAppStateSetMany`, `TestAppStatePop`, `TestAppStateGetGlobal`, `TestAppStateGetViewKey` |
| `ViewState.__init__` / `get` / `set` / `set_many` / `get_expanded` / `toggle_expanded` / `build_cfg` / `load` / `apply_preset_params` | 有 | `test_state.py`: `TestViewState`, `TestViewStateBuildCfg`, `TestApplyPresetParams` |
| `view()` / `get_view_cfg()` | 有 | `test_state.py`: `TestShortcutFunctions` |
| `PendingApplyParams` 生命周期 | 有 | `test_state.py`: `TestPendingApplyParamsLifecycle` |

### 2.9 backtest_logger.py

| 关键函数 | 有无测试 | 测试文件/类 |
|---|---|---|
| `log_mode_switch` / `log_bar_navigation` / `log_data_load` / `log_error` | 有 | `test_backtest.py`: `TestBacktestLogger` |

---

## 3. 未覆盖 / 薄弱清单

### Critical

| # | 函数 | 所在文件 | 风险等级 | 说明 |
|---|---|---|---|---|
| 1 | `_add_pnl_traces` | streamlit_app.py:447 | **Critical** | PnL 可视化核心函数: 做多/做空曲线、分段高亮、止损/止盈标记、标注、填充。无任何单元测试。含 trade_records 循环 + np.nanmax/min 边界。 |
| 2 | `_add_main_price_traces` | streamlit_app.py:375 | **Critical** | K线 + 收盘价 + 滤波线主图绘制。无任何单元测试。dual filter 分支也未经测试。 |
| 3 | `_add_residual_traces` | streamlit_app.py:392 | **Critical** | 残差/速度/加速度子图绘制。含 np.gradient 和 NaN 处理。无任何单元测试。 |

### Normal

| # | 函数 | 所在文件 | 风险等级 | 说明 |
|---|---|---|---|---|
| 4 | `_save_backtest_config` / `_load_backtest_config` | streamlit_app.py:546/562 | Normal | 回测配置 JSON 文件读写。文件 I/O + JSON 序列化 + ticker 匹配逻辑。无测试。 |
| 5 | `_get_min_tf_and_count` | streamlit_app.py:507 | Normal | 从多视图配置中解析最小周期 + DB COUNT 查询。含 ALL_TFS.index 异常和空配置边界。无测试。 |
| 6 | `_render_chart` / `_render_chart_fragment` | streamlit_app.py:582/616 | Normal | 主图表渲染管线 (~200行, 含大量 Streamlit go.FigureWidget 交互)。与 Streamlit 紧耦合，纯单元测试困难。 |
| 7 | `_handle_initial_fetch` | streamlit_app.py:887 | Normal | 首次数据加载编排: 清缓存 + 调用 _load_chart_data + _sync_to_display。无测试。 |
| 8 | `_render_market_ticker` + `_render_filter_selectors` + `_render_param_panels` + `_render_preset_selector` | streamlit_app.py | Normal | 关键 UI 组件的 Streamlit 渲染函数 (调 sidebar / st 组件)。无单元测试。 |
| 9 | `_render_health_check` + `_render_data_validation` | streamlit_app.py:1078/1098 | Normal | 数据健康检查和验证 UI。含 DB 查询 + 图表显示。无测试。 |
| 10 | `_render_db_backup` + `_render_export_config` + `_render_config_history` + `_render_db_import_export` + `_render_config_import` | streamlit_app.py | Normal | 数据库备份/恢复/导入/导出 UI 函数。无测试。 |
| 11 | `_render_refresh_row` | streamlit_app.py:902 | Normal | 刷新按钮行 (含 force_update_kline)。无测试。 |
| 12 | `_run_auto_refresh` | streamlit_app.py:1732 | Normal | 自动刷新循环 (while + time.sleep)。无测试。 |
| 13 | `_cached_fetch_stock` | streamlit_app.py:67 | Normal | 数据获取缓存层 (st.cache_data)。虽然底层 _fetch_stock 测了，但缓存层本身无测试。 |
| 14 | `_get_db_connection` + `_handle_pending_apply` | streamlit_app.py:814/826 | Normal | DB 连接检查 + 待定配置应用。无单测 (apply 通过集成测试间接覆盖)。 |
| 15 | `_compute_strategy_display` | streamlit_app.py:280 | Normal | 策略显示编排 (调用 _add_pnl_traces 等)。无测试。 |
| 16 | `_write_parquet` | data_loader.py:711 | Normal | Parquet 文件写入 (文件 I/O)。无测试。 |
| 17 | `_render_backtest_mode` | streamlit_app.py:1461 | Normal | 回测模式主渲染 (~130行), 虽然 `test_backtest.py:TestRenderBacktestModeSlider` 部分覆盖, 但 patch 了内部 _render_backtest_nav, 整体逻辑未验证。 |

### Low

| # | 函数 | 所在文件 | 风险等级 | 说明 |
|---|---|---|---|---|
| 18 | `main()` | streamlit_app.py:1751 | Low | App 入口 ~200 行, 强依赖 Streamlit 运行时, 无法纯单元测试。已知缺口。 |
| 19 | `_render_chart` | streamlit_app.py:616 | Low | 同上, 强 Streamlit 耦合。 |

---

## 4. 具体补测建议

### 4.1 Critical: 图表可视化函数 (建议优先补)

**`test_add_main_price_traces.py`** (新建):

```python
# 1. 正常 K 线 + 收盘价 + 滤波线
def test_adds_candlestick_and_close_and_filter():
    fig = make_subplots(rows=1, cols=1)
    _add_main_price_traces(fig, t, noisy, ohlc, filtered, filtered2, cfg)
    traces = [t.name for t in fig.data]
    assert "K" in traces and "收盘" in traces and "滤波" in traces

# 2. dual filter 开启时显示 "滤波2"
def test_dual_filter_shows_second_filter():
    cfg["_dual"] = True
    ...

# 3. 所有 filter 值全为 NaN 时只显示 K 线 + 收盘
def test_all_nan_filter_omitted():
    ...

# 4. 空 filtered2 (None) 不报错
def test_filtered2_none_safe():
    ...
```

**`test_add_residual_traces.py`** (新建):

```python
# 1. 正常残差 + 速度 + 加速度轨迹
def test_adds_residual_vel_acc():
    ...

# 2. len(t) < 2 返回空 array
def test_short_t_returns_empty():
    result = _add_residual_traces(fig, t[:1], ...)
    assert len(result) == 0

# 3. 全 NaN filtered, acc 用 np.zeros_like
def test_all_nan_filtered():
    ...

# 4. gradient 边界: 常数序列 vel=0
def test_constant_signal_zero_vel():
    ...
```

**`test_add_pnl_traces.py`** (新建):

```python
# 1. 正常做多/做空两条曲线 + 100 基线
def test_adds_long_short_pnl():
    ...

# 2. trade_records 为空: 只有基线 + 曲线, 无分段
def test_empty_trades():
    ...

# 3. 止损标记用 x, 止盈标记用 circle
def test_stop_loss_marker_x():
    ...

# 4. trade_records 含 entry_idx/exit_idx 边界 (idx=0, idx=len(t)-1)
def test_entry_exit_boundary():
    ...

# 5. exit_reason 为 stop_loss 时标注 return_pct
def test_exit_annotation_format():
    ...

# 6. long_pnl / short_pnl 全 NaN
def test_all_nan_pnl():
    ...

# 7. 做空 returns 显示 ↓ 箭头
def test_short_arrow_down():
    ...
```

### 4.2 Normal: 策略/配置/UI 函数

**`test_backtest_config_persistence.py`** (新建):

```python
# _save_backtest_config → JSON 文件写入验证
def test_save_creates_json():
    _save_backtest_config("AAPL", "1min", 500, 120)
    path = Path("data/backtest_config.json")
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["ticker"] == "AAPL"

# _load_backtest_config ticker 匹配返回配置
def test_load_matching_ticker():
    ...

# ticker 不匹配返回 None
def test_load_mismatch_returns_none():
    ...

# JSON 文件不存在返回 None
def test_load_missing_file():
    ...

# JSON 解析异常返回 None
def test_load_corrupt_json():
    ...
```

**`test_get_min_tf_and_count.py`** (新建):

```python
# 正常多视图取最小 tf
def test_min_tf_from_configs():
    configs = [{"tf": "日线"}, {"tf": "60分钟"}, {"tf": "周线"}]
    # ALL_TFS 顺序已知, 期望返回 "60分钟"
    ...

# 空 configs 返回 ("", 0)
def test_empty_configs():
    assert _get_min_tf_and_count([], "AAPL") == ("", 0)

# configs 中无有效 tf (全 ValueError) 
def test_no_valid_tf():
    ...

# DB 查询异常 → bar_count = 0
def test_db_exception_returns_zero():
    ...
```

**`test_handle_initial_fetch.py`** (新建):

```python
# 正常流程: 清缓存 + 加载 + 同步
def test_initial_fetch_flow():
    ...
```

**Streamlit UI 渲染函数** (在 `test_app_ui.py` 扩展或新建):

```python
# 低优先级: 目前的 smoke test 已验证 UI 渲染不报错
# 建议扩展 smoke test 验证特定子组件是否存在
# _render_market_ticker / _render_filter_selectors / _render_preset_selector
# _render_health_check / _render_data_validation
# _render_db_backup / _render_export_config 等
```

### 4.3 Low: 入口函数

```
main() 函数: 已知缺口, 建议保持在集成/E2E 级别覆盖, 不追求单元测试。
但目前也没有集成/E2E 测试验证完整的 app 启动流程 (test_app_smoke.py 仅验证不崩溃)。
可以考虑加一个 `test_main_flow_integration.py`, 模拟 st.SessionState 注入后调用 main() 部分片段。
```

---

## 5. 边界/退化场景覆盖评估

核心算法函数的边界覆盖较好:

| 场景 | 覆盖情况 |
|---|---|
| `_compute_strategy_pnl`: 空 pairs | 有 (`test_strategy.py:TestComputeStrategyPnL.test_empty_pairs`) |
| `_compute_strategy_pnl`: 无预测入场 | 有 (`test_entry_without_prediction_still_trades`) |
| 入场与抛物线预测解耦 (is_long=sig==1) | 有 (`test_downward_parabola_no_longer_vetoes_long`) |
| 止损用预测价/无预测回退固定% | 有 (`test_stop_loss_trigger`, `test_entry_without_prediction_still_trades`) |
| `_align_pnl_to_current_tf`: EOD 右延续 | 有 (`test_alignment.py:TestEodHigherPositionExtend`) |
| `_align_pnl_to_current_tf`: 窗口前左延续 | 有 (`test_alignment.py:TestPreWindowEntryExtend`) |
| `_align_pnl_to_current_tf`: 无时间重叠 | 有 (`test_no_time_overlap`) |
| `_align_pnl_to_current_tf`: forward fill | 有 (`test_forward_fill`) |
| `_compute_holding_masks`: entry 无 exit | 有 (`test_entry_without_exit`) |
| `_compute_holding_masks`: 空 markers | 有 (`test_empty_markers`) |
| `_compute_holding_masks`: 多 entry 同方向 | 有 (`test_multiple_entries_same_type`) |
| `_compute_holding_masks`: entry 在 exit 前无匹配 | 有 (`test_entry_before_first_exit_no_match`) |
| `_schmitt_trigger`: 短序列返回 None | 有 (`test_short_sequence_returns_none`) |
| `_schmitt_trigger`: NaN 传播 | 有 (`test_nan_propagation`) |
| 极端价格/负数价格 | 有 (`test_boundary.py:TestNumericalStability`) |
| `collect_current_params`: 4 视图 + 中文参数前缀 | 有 (`test_preset_ui.py:TestCollectParamsCompleteness`) |

---

## 6. 总结

```
总用例: 815 | 通过: 815 (100%) | 测试文件: 23

核心算法层 (filter_engine) 测试扎实, 覆盖全面, 边界充分。
主要缺口集中在:

  Critical (3):
    1. _add_pnl_traces        — PnL 可视化核心, 含止损/止盈标记/标注, 0 单元测试
    2. _add_main_price_traces — K线+收盘价+双滤波线渲染, 0 单元测试
    3. _add_residual_traces   — 残差/速度/加速度子图, 含 gradient 与 NaN 分支, 0 单元测试

  Normal (14): 
    _save/load_backtest_config、_get_min_tf_and_count、_render_chart、
    _handle_initial_fetch、_compute_strategy_display、_write_parquet,
    以及 10 个 _render_* UI 渲染函数

  Low (2):
    main() 入口函数 (已知缺口, 建议集成测试覆盖)

补测建议: 优先添加 _add_pnl_traces / _add_main_price_traces / _add_residual_traces
的纯函数单元测试 (模拟 plotly.graph_objects.Figure 验证 trace 属性即可),
约 15-20 个测试用例, 可消除最关键的覆盖盲区。
```
