# 方案A 重构测试覆盖缺口审计

审计日期: 2026-07-12
审计范围: `tests/test_plan_a_api.py`, `tests/test_subplot_layout.py`, `tests/test_charts.py`, `tests/test_render_traces.py`, `tests/test_feedback_subplot.py`, `tests/test_strategy.py`
源文件: `filter_app/streamlit_app.py`, `filter_app/components/charts.py`

## 当前状态

- 总用例数: **794** (1 skipped)
- 通过: **793**
- 新 API 回归测试文件: `test_plan_a_api.py` (218 行), `test_subplot_layout.py` (205 行)

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

---

## 缺口

### ⚠️ 缺口 1 (P0): 无真实 `_add_*` 输出的端到端 Figure 构造测试

`TestGoFigureConstruction` 使用的是**手写 dict**，不是实际调用 `_add_*` 函数产生的输出。这意味：
- 不会捕获 `_add_*` 函数因含 np.ndarray/np.floating 等非 JSON 原生类型导致 `to_json()` 报错。
- 不会验证 8 行最复杂布局下 traces/shapes/annotations 合并后能否正常序列化。
- 不会捕获 `xaxis` 引用 layout 中不存在的轴（被 Plotly 静默忽略）。

**具体缺的用例**：模拟 `_render_chart` 第 813-889 行的完整 trace/shape/annotation 收集逻辑，使用真实 `_add_*` 函数输出构造 `go.Figure(data=all_traces, layout=layout_dict)`，调用 `fig.to_json()`，覆盖全部 5 个 `_determine_subplot_layout` 分支。至少包括 8 行最复杂布局。

### ⚠️ 缺口 2 (P1): `_add_prediction_traces` 的 Plan A 返回格式未验证

`test_strategy.py::TestAddPredictionTraces` 测试 trace 长度（`len(traces)==3`）但**未验证**：
- 返回值类型是 `list[dict]`（而非 `go.Scatter` 对象）
- 每个 trace dict 包含正确的 `"type": "scattergl"` 和 `"xaxis"/"yaxis"` 键
- axis 引用在对应 layout 中存在

`test_subplot_layout.py` 中也没有 `_add_prediction_traces` 的 axis 正确性测试。

**具体缺的用例**：
1. `_add_prediction_traces(...)` 返回 `list[dict]`，每个元素 `isinstance(d, dict)` 且 `d["type"] == "scattergl"`
2. row=1 时 axis 引用为 `"x"/"y"`；row>1 时 axis 引用为 `"xN"/"yN"`
3. n_extend=0 边界：只有拟合 trace，无预测/残差 trace

### ⚠️ 缺口 3 (P1): `_determine_subplot_layout` 的返回行映射值未显式验证

`TestAxisNameConvention` 通过测试不同 `rows` 值（4-8）间接验证了分支，但**没有直接验证**每个分支返回的 `mr`, `rr`, `vr`, `sar`, `ssr`, `ar`, `pnl_row`, `cross_row`, `align_row` 值。如果某次重构误将 `pnl_row` 从 6 改为 1（导致 `f"x1"` 而非 `f"x"`），现有测试不会捕获。

**具体缺的用例**：对所有 5 个分支直接断言返回值：
- `has_s+strategy+cross+alignment` → `(rows=8, mr=1, rr=2, vr=3, sar=4, ssr=5, ar=None, pnl_row=6, cross_row=7, align_row=8)`
- `has_s+strategy+cross` → `(rows=7, ..., pnl_row=6, cross_row=7, align_row=None)`
- `has_s+strategy` → `(rows=6, ..., pnl_row=6, cross_row=None, align_row=None)`
- `has_s only` → `(rows=5, ..., pnl_row=None, cross_row=None, align_row=None)`
- `no_s` → `(rows=4, ..., sar=None, ssr=None, ar=4, pnl_row=None, cross_row=None, align_row=None)`

### ⚠️ 缺口 4 (P1): `_add_alignment_subplot` 的链式调用未验证

函数内部依次调用 `_render_pnl_curves` → 循环（`_render_entry_marker` / `_render_exit_marker_with_label`）→ `_render_fill_background` ×2 → `_render_baseline`。现有测试 `test_alignment_returns_4_tuple` 只验证返回数量 `len(traces) >= 3`，**未验证**内部各组件是否正确组合：
- traces 中应包含来自 `_render_pnl_curves` 的 2 条基线曲线
- traces 中应包含 `_render_fill_background` 产生的填充 trace
- shapes 中应包含 `_render_baseline` 产生的 hline
- exit_reason 为 `stop_loss`/`take_profit` 时 annotations 应有值

**具体缺的用例**：模拟有 trade 记录且 exit_reason 可识别的场景，验证返回的 traces 数量精确匹配预期（基线 2 + 段线 N + 填充 2 + 标记 M），shapes 有 baseline，annotations 包含盈亏标签。

### ⚠️ 缺口 5 (P2): `_render_fill_background` 返回格式未测试

该函数返回一个 trace dict，被 `_add_pnl_traces` 和 `_add_alignment_subplot` 间接使用。但**没有独立测试**验证：
- 返回值是 `dict` 且 `dict["type"] == "scattergl"`
- 包含正确的 `"fill": "toself"` 和 `"fillcolor"` 键
- `xaxis`/`yaxis` 引用与传入的 `row` 参数匹配

### ⚠️ 缺口 6 (P2): Entry/exit marker 的 row=1 axis 命名脆弱性

`_render_entry_marker` (charts.py:235) 和 `_render_exit_marker_with_label` (charts.py:251) 使用硬编码 `f"x{row}"` / `f"y{row}"`。对于 row=1 会生成 `"x1"/"y1"`，但 make_subplots 的 row 1 使用 `"x"/"y"` 无后缀。这不同于 `_add_prediction_traces` 和 `_add_main_price_traces` 中正确的 `"x" if row == 1 else f"x{row}"` 逻辑。

当前调用链中这两函数只从 `_add_alignment_subplot` 调用，且传入的 `align_row` 始终 ≥ 7，因此实际无 bug。但**属于脆弱代码**——若未来在任何 row=1 的上下文中调用，将产生静默 bug（axis 引用不存在，Plotly 将 trace 丢弃到默认轴，子图布局错乱）。

**建议**：修复为与 `_add_prediction_traces` 一致的轴名逻辑，或用 docstring 显式声明 `row != 1` 前提条件。

### ⚠️ 缺口 7 (P2): `_insert_feedback_row` 与 `_determine_subplot_layout` 组合未测试

`test_insert_feedback_row_shifts_rows` 单独测试了 `_insert_feedback_row`，但**未测试**与 `_determine_subplot_layout` 各分支的组合：
- 5 个 `_determine_subplot_layout` 分支中，`has_s+strategy+cross+alignment` (rows=8) 和 `has_s+strategy+cross` (rows=7) 的 `cross_row` 和 `align_row` 在插入 feedback row 后需要顺延。无测试验证这一组合的正确性。
- 仅 `has_s+strategy` (rows=6) 分支 `pnl_row=6` 可插入 feedback row，其他分支 `pnl_row=None`。无测试验证 `None` 分支跳过 feedback 插入时的兼容性。

### ⚠️ 缺口 8 (P2): cross_pnl / alignment 中 `_draw_holding_bands` 的边界情况

`_add_cross_pnl_subplot`（charts.py:322）委派给 `_draw_holding_bands`。现有测试只覆盖了 `_draw_holding_bands` 的基本流程和空掩码，但**未覆盖**：
- `_compute_holding_masks` 返回的掩码为空时的正确性（`_draw_holding_bands` 内的 `_contiguous_runs` 虽然单独测试了，但完整链条未测）
- `entry_markers`/`exit_markers` 中存在无效索引（越界）时，`_compute_holding_masks` 不崩溃

---

## 建议补测（按优先级）

### P0 — 必须补（端到端安全网）

```
test_e2e_real_add_functions_produce_valid_figure():
  - 覆盖全部 5 个 _determine_subplot_layout 分支
  - 使用真实 _add_* 函数输出 → go.Figure(data=, layout=) → fig.to_json()
  - 8 行最复杂布局（has_s+strategy+cross+alignment）
  - 断言不抛异常，序列化 JSON 有效
```

### P1 — 应补

```
test_add_prediction_traces_returns_dicts(): 验证返回 list[dict]，axis 命名正确
test_determine_subplot_layout_all_branches(): 对 5 个分支逐一断言返回的行映射值
test_alignment_subplot_chain_composition(): 验证链式调用产生的 traces/shapes/annotations 精确结构
```

### P2 — 建议补

```
test_render_fill_background_return_format(): 验证 trace dict 的 type/fill/fillcolor/xaxis
test_render_entry_marker_axis_naming_row1(): 记录/修复 row=1 的 axis 命名脆弱性
test_insert_feedback_with_layout_all_applicable_branches(): 测试 _insert_feedback_row 与各大布局分支的组合
```
