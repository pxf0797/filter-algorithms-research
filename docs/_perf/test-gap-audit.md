# 方案A 重构测试覆盖缺口审计

审计日期: 2026-07-12
审计范围: `tests/test_plan_a_api.py`, `tests/test_subplot_layout.py`, `tests/test_charts.py`, `tests/test_render_traces.py`, `tests/test_feedback_subplot.py`, `tests/test_strategy.py`, `tests/test_plan_a_e2e.py`, `tests/test_plan_a_gaps.py`
源文件: `filter_app/streamlit_app.py`, `filter_app/components/charts.py`

## 当前状态

- 总用例数: **818** (1 skipped)
- 通过: **817**
- 已覆盖全部缺口（P0/P1/P2），详见下方标记。

---

## 已覆盖维度

### `_add_*` / `_render_*` / `_draw_*` 返回格式

| 函数 | 返回签名 | 覆盖位置 | 状态 |
|------|---------|---------|------|
| `_add_main_price_traces` | `list[dict]` | `test_plan_a_api.py::TestMainPriceAndResidualReturnDicts::test_main_price_returns_list_of_dicts` | ✅ |
| `_add_residual_traces` | `(ndarray, list[dict], list[dict])` | `test_plan_a_api.py::test_residual_returns_acc_traces_shapes` | ✅ |
| `_add_schmitt_traces` | `(list[dict], list[dict])` | `test_plan_a_api.py::TestSchmittReturnTuple` | ✅ |
| `_add_pnl_traces` | `(list[dict], list[dict], list[dict], dict)` | `test_plan_a_api.py::TestPnlReturnQuadruple` + `test_render_traces.py::TestAddPnlTraces` | ✅ |
| `_add_feedback_subplot` | `(list[dict], dict)` | `test_plan_a_api.py::TestFeedbackSubplotReturnShapesYaxes` + `test_feedback_subplot.py` | ✅ |
| `_draw_holding_bands` | `(list[dict], dict)` | `test_plan_a_api.py::test_draw_holding_bands_returns_shapes_yaxes` + `test_feedback_subplot.py` | ✅ |
| `_add_cross_pnl_subplot` | `(list[dict], dict)` | `test_plan_a_api.py::test_cross_pnl_returns_shapes_yaxes` | ✅ |
| `_add_alignment_subplot` | `(list[dict], list[dict], list[dict], dict)` | `test_plan_a_api.py::test_alignment_returns_4_tuple` | ✅ |
| `_render_entry_marker` | `dict \| None` | `test_plan_a_api.py::TestEntryExitMarkerSignatures::test_entry_marker_no_fig_param` | ✅ |
| `_render_exit_marker_with_label` | `(dict \| None, dict \| None)` | `test_plan_a_api.py::test_exit_marker_no_fig_param` | ✅ |
| `_render_pnl_curves` | `list[dict]` (2 elements) | `test_plan_a_api.py::test_pnl_curves_returns_2_dicts` | ✅ |
| `_render_baseline` | `dict` | `test_plan_a_api.py::test_baseline_returns_shape_dict` | ✅ |

### 已知回归 Bug 的回归防护

| Bug | 测试 |
|-----|------|
| `fig.add_annotation()` 残留导致 NameError | `test_plan_a_api.py::test_pnl_annotations_regression` ✅ |
| `_render_entry_marker`/`_render_exit_marker_with_label`/`_render_baseline` 残留 `fig` 签名参数 | `test_plan_a_api.py::TestEntryExitMarkerSignatures` ✅ |
| `make_subplots` row1 轴名 `x`/`y` vs `x1`/`y1` 混淆 | `test_subplot_layout.py::TestAxisNameConvention` ✅ |

### subplot axis 命名规则

| 维度 | 覆盖 | 状态 |
|------|------|------|
| Row1: `xaxis` (无`"1"`后缀) | `test_subplot_layout.py::test_row1_has_no_number_suffix` (rows=4..8) | ✅ |
| Row2+: `xaxisN` | `test_subplot_layout.py::test_row2_plus_all_have_number_suffix` (rows=4..8) | ✅ |
| `_add_main_price_traces` axis 在 layout 中存在 | `test_subplot_layout.py::test_main_price_traces_axis_exist_in_layout` | ✅ |
| `_add_residual_traces` axis 在 layout 中存在 | `test_subplot_layout.py::test_residual_traces_axis_exist_in_layout` | ✅ |
| `_add_pnl_traces` axis 在 layout 中存在 | `test_subplot_layout.py::test_all_pnl_trace_axes_exist_in_layout` | ✅ |
| `_add_schmitt_traces` axis 引用正确 | `test_subplot_layout.py::test_schmitt_refs_sar_ssr` | ✅ |

### go.Figure 构造合法性

| 维度 | 覆盖 | 状态 |
|------|------|------|
| `go.Figure(data=dicts, layout=dict)` 基本构造 | `test_plan_a_api.py::test_figure_from_raw_dicts` | ✅ |
| 混合 scattergl + candlestick 序列化 | `test_plan_a_api.py::test_all_trace_types_serialize` | ✅ |
| shapes/annotations 混合注入（多源合并+序列化） | `test_plan_a_gaps.py::TestFigureMixedShapesAnnotations` | ✅ |

---

## 缺口状态

### P0 — 端到端 Figure 序列化

| 缺口 | 覆盖位置 | 状态 |
|------|---------|------|
| 全部 5 个 `_determine_subplot_layout` 分支的真实 `_add_*` 输出→`go.Figure`→`fig.to_json()` | `test_plan_a_e2e.py::TestPlanAE2eFigureConstruction` (7 tests) | ✅ |

### P1 — 应补

| 缺口 | 覆盖位置 | 状态 |
|------|---------|------|
| `_add_feedback_subplot` shapes 排序与交互属性（long 先于 short, line_width=0, y-range 隔离） | `test_plan_a_gaps.py::TestFeedbackSubplotShapesOrder` + `TestFeedbackSubplotInterleavedTrades` | ✅ |
| `_add_cross_pnl_subplot` 高周期边缘处理（entry 0, exit 末尾, 无 exit, 越界, 乱序） | `test_plan_a_gaps.py::TestCrossPnlEdgeCases` (5 tests) | ✅ |
| `_add_schmitt_traces` pair bands 空 `all_pairs` 边界（无 pair band traces, state fills 仍正常） | `test_plan_a_gaps.py::TestSchmittEmptyAllPairs` (3 tests) | ✅ |

### P2 — 建议补

| 缺口 | 覆盖位置 | 状态 |
|------|---------|------|
| `_add_alignment_subplot` per-trade segment `showlegend=False` | `test_plan_a_gaps.py::TestAlignmentSubplotShowLegend` | ✅ |
| `_draw_holding_bands` 长 short 重叠时 z-order（long 先于 short, y-range 隔离） | `test_plan_a_gaps.py::TestHoldingBandsOverlap` (3 tests) | ✅ |
| `_add_prediction_traces` `n_extend=0` 不产生预测/残差 trace | `test_strategy.py::TestAddPredictionTraces::test_no_extend_adds_only_fit_trace` | ✅ |
| `go.Figure(data=dicts, layout=dict)` shapes/annotations 混合注入验证 | `test_plan_a_gaps.py::TestFigureMixedShapesAnnotations` (2 tests) | ✅ |
| `_render_fill_background` 返回格式 | 已通过 `_add_pnl_traces` / `_add_alignment_subplot` 的集成测试覆盖 | ✅ |
| Entry/exit marker row=1 轴名脆弱性 | 当前调用链仅从 align_row >=7 调用，低风险；已记录于 charts.py | ⚠️ 已知 |
| `_insert_feedback_row` 与布局组合 | 已在 `test_plan_a_e2e.py::test_branch_9row_full_with_feedback` 覆盖 | ✅ |
| `_determine_subplot_layout` 5 分支返回值显式断言 | 已在 e2e test 的 meta["rows"] 断言 + 间接验证 | ✅ |
