# 测试与测试文档完整性审计（综合结论）

> 审计日期：2026-07-11 ｜ 项目：filter_research ｜ 基准：`pytest --collect-only` 实测 **815** 用例 / **23** 个 `test_*.py`
> 本文为综合结论；两份详细报告见同目录：
> - [`test-coverage-audit.md`](test-coverage-audit.md) — 逐模块测试覆盖
> - [`test-doc-completeness-audit.md`](test-doc-completeness-audit.md) — 测试文档 vs 实际差异

---

## 0. 一句话结论

- **测试用例**：**完整性良好**。815 用例 100% 通过，**核心算法层（滤波/施密特/配对/策略PnL/跨周期对齐/持仓掩码/配置持久化）覆盖扎实、边界充分**。主要盲区在**显示层 3 个 trace 渲染函数**（0 单测）。
- **测试文档**：**明显滞后**。`docs/test_cases.md` 称 662 用例 / 21 文件，**实际 815 / 23**（误差 23%），2 个测试文件整体漏录、3 个部分漏录、`test_charts` 有 4 条过期 TC-ID。

---

## A. 测试用例完整性

**规模**：815 用例 / 815 通过 / 0 失败 / 23 个测试文件。核心算法层每个关键函数都有单元测试 + 边界测试（见 coverage 报告 §5：空 pairs、无预测入场、入场解耦、eod/窗口前延续、持仓掩码各边界、NaN 传播、极端价格等均覆盖）。

**覆盖盲区（按风险）：**

| 级别 | 函数 | 文件 | 说明 |
|---|---|---|---|
| **Critical** | `_add_pnl_traces` | streamlit_app.py | PnL 可视化核心（多空曲线/止损止盈标记/收益标注），0 单测 |
| **Critical** | `_add_main_price_traces` | streamlit_app.py | K线+收盘+双滤波线主图，0 单测 |
| **Critical** | `_add_residual_traces` | streamlit_app.py | 残差/速度/加速度（含 `np.gradient`+NaN 分支），0 单测 |
| Normal | `_save/_load_backtest_config`、`_get_min_tf_and_count`、`_compute_strategy_display`、`_handle_initial_fetch`、`_write_parquet` | streamlit_app / data_loader | 文件 I/O、编排、解析逻辑无单测 |
| Normal | 约 10 个 `_render_*` UI 函数 | streamlit_app | Streamlit 渲染，纯单测困难（smoke 已验证不崩溃） |
| Low | `main()` | streamlit_app | 入口，强 Streamlit 耦合，建议集成级覆盖 |

**建议（优先级）**：优先补 3 个 Critical trace 函数的纯函数单测（构造 `plotly.graph_objects.Figure` 断言 trace 属性即可，约 15–20 用例），可消除最关键盲区。详见 coverage 报告 §4 已给出具体用例骨架。

## B. 测试文档完整性

现有测试文档 3 份：`docs/test_cases.md`（主，TC-ID 表）、`docs/tests/data_computation_test_cases.md`（手算验证）、`docs/tests/ui_test_cases.md`（手工交互）。

**`docs/test_cases.md` 与实际的差异：**

| 类别 | 内容 |
|---|---|
| **计数过时** | 称 662 用例 / 21 文件 → 实际 **815 / 23**（误差 +23%）；头部总数、附录统计表均需更新 |
| **整文件漏录** | `test_cascading_synthesis.py`(53)、`test_feedback_subplot.py`(6) 完全未提 |
| **部分漏录** | `test_alignment.py`(+4：eod/窗口前延续)、`test_strategy.py`(+2：入场解耦回归)、`test_param_export_import.py`(+5：TestParamRegistryGuard)、`test_backtest.py`(多个类漂移) |
| **过期条目** | `test_charts.py` 的 `TestCrossPnlSubplot` 已从 8→4 方法，TC-CHART-061/064/065/067 已不存在 |
| **内部计数矛盾** | `test_state.py`(59 vs 62)、`test_backtest.py`(31 vs 37、56 vs 62) |

**建议**：见 doc 报告 §4——P0 补录 2 个漏录文件、P1 补录 3 个部分漏录 + 删 4 条过期 TC-ID、P2 全局修正计数。同时 `tests/README.md` 记为 333，也严重过时（→815）。

## C. 附带修复

本次审计据实核对时发现并已修复：
- **死代码**：`_compute_strategy_pnl` 末尾一处**不可达的重复 `return`**（原 `filter_engine.py:854`），已移除。
- 澄清一处事实分歧：测试文件数**实际 23**（某中间报告曾误记 26，因重复计数），本文以 `ls tests/test_*.py` 与 `pytest --collect-only` 为准。

## D. 下一步（可选，待定）

1. 补 3 个 Critical trace 函数单测（约 15–20 用例）。
2. 更新 `docs/test_cases.md`：补录 2 个漏录文件 + 5 个漏录类、删 4 条过期 TC-ID、全局改 662→815 / 21→23、修内部计数。
3. 更新 `tests/README.md` 计数 333→815。

以上是"检查确认"的结论；是否要我接着**执行 D.1（补测）/ D.2（更新测试文档）** 由你定。
