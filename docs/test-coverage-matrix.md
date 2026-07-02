# 回测功能测试覆盖矩阵

> 对照文档：`docs/backtest-final-design.md` 第 6 章（L950-1075）
> 对照测试：`tests/test_backtest.py`（97 个测试函数）
> 代码侦察：`orch-20260702-224911-74024/codebase-scout.md`
> 生成日期：2026-07-02

---

## 6.1 单元测试覆盖

### 6.1.1 `_binary_search_le`（10 个设计用例）

| # | 设计文档用例 | 输入/条件 | 期望输出 | test_backtest.py 方法 | 状态 |
|---|------------|----------|---------|----------------------|------|
| 1 | 空数组 | `arr=[], target=任意` | `-1` | `TestBinarySearchLe::test_empty_dates` | ✅ |
| 2 | target < 所有值 | `arr=[10,20,30], target=5` | `-1` | `TestBinarySearchLe::test_before_first` | ✅ |
| 3 | target = 第一个值 | `arr=[10,20,30], target=10` | `0` | `TestBinarySearchLe::test_cutoff_equals_first` | ✅ |
| 4 | target = 最后一个值 | `arr=[10,20,30], target=30` | `2` | `TestBinarySearchLe::test_cutoff_equals_last` | ✅ |
| 5 | target 在中间 | `arr=[10,20,30], target=15` | `0` | `TestBinarySearchLe::test_cutoff_between_two_dates` | ✅ |
| 6 | target > 所有值 | `arr=[10,20,30], target=50` | `2` | `TestBinarySearchLe::test_after_last` | ✅ |
| 7 | 单元素等于 | `arr=[42], target=42` | `0` | `TestBinarySearchLe::test_single_element` (子场景1) | ✅ |
| 8 | 单元素小于 | `arr=[42], target=10` | `-1` | `TestBinarySearchLe::test_single_element` (子场景2) | ✅ |
| 9 | 单元素大于 | `arr=[42], target=100` | `0` | `TestBinarySearchLe::test_single_element` (子场景3) | ✅ |
| 10 | 重复值（取最大索引） | `arr=[10,20,20,30], target=20` | `2` | — | ❌ 未覆盖 |

**额外已覆盖（设计文档未列出）**：
- `test_exact_match` — cutoff 精确等于中间元素
- `test_large_dataset_smoke` — 大数据集（10000 条）冒烟测试
- `test_monotonic_assertion` — 结果单调性验证

**§6.1.1 统计：9/10 已覆盖，1 未覆盖**（重复值场景）

---

### 6.1.2 `_load_backtest_window`（11 个设计用例）

| # | 设计文档用例 | 条件 | 期望输出 | test_backtest.py 方法 | 状态 |
|---|------------|------|---------|----------------------|------|
| 1 | bar_index=0, n_pts=100 | 日线有 500 根 bar | slice_length=1, is_partial=True | `TestLoadBacktestWindow::test_bar_index_zero` | ✅ |
| 2 | bar_index=n_pts-1, n_pts=100 | 日线有 500 根 bar | slice_length=100, is_partial=False | `TestLoadBacktestWindow::test_cutoff_date_exact_boundary`（bar_index=10, n_pts=5） | ⚠️ 部分：场景覆盖但未精确验证 n_pts-1 边界 |
| 3 | bar_index=250, n_pts=100 | 日线有 500 根 bar | slice_length=100, start_idx=151, end_idx=250 | `TestLoadBacktestWindow::test_bar_index_mid`（bar_index=150, n_pts=100） | ✅ |
| 4 | bar_index=total-1, n_pts=100 | 日线有 500 根 bar | slice_length=100 | `TestLoadBacktestWindow::test_bar_index_last` | ✅ |
| 5 | tf 数据为空 | `len(df)=0` | slice_length=0, is_partial=True | `TestLoadBacktestWindow::test_empty_parquet_handling` | ✅ |
| 6 | tf 数据全部在 cutoff_date 之后 | `cutoff_idx=-1` | slice_length=0, is_partial=True | — | ❌ 未覆盖 |
| 7 | 周线需要合成（周三） | tf=周线, cutoff_date=周三 | is_synthesized_last=True | `TestHigherTfSynthesis::test_mid_week_synthesis` | ✅ |
| 8 | 周线不需要合成（周五/周日） | tf=周线, cutoff_date=周期边界 | is_synthesized_last=False | `TestHigherTfSynthesis::test_cross_week_boundary_no_synthesis` | ✅ |
| 9 | 周线同周期替换 | 推进 1 天仍在同一周 | 长度不变，最后一根被替换 | — | ❌ 未覆盖 |
| 10 | 周线跨周期追加 | 从周五推进到下一周周一 | 长度+1（若满则 trim） | — | ❌ 未覆盖 |
| 11 | 季线总 bar 不足 | 季线仅 24 根, n_pts=100 | slice_length=24, is_partial=True | `TestFullBarGuard::test_window_partial_when_data_insufficient`（日线场景，非季线） | ⚠️ 部分：逻辑覆盖但未用季线数据验证 |

**额外已覆盖**：
- `test_n_pts_larger_than_data` — n_pts 超过数据总量时全部返回
- `test_returns_datetime_index` — 返回 index 类型验证
- `test_preserves_ohlc_columns` — 列完整性验证
- `test_min_tf_equals_current_tf` — min_tf == tf 分支
- `test_high_tf_window_smaller_than_min_tf` — 高周期窗口更短场景
- `test_backtest_data_window_size`（集成测试）— 多 bar_index 窗口大小验证

**§6.1.2 统计：7/11 已覆盖，1 部分覆盖，3 未覆盖**

---

### 6.1.3 `_get_min_tf_and_count`（4 个设计用例）

| # | 设计文档用例 | 输入 | 期望输出 | test_backtest.py 方法 | 状态 |
|---|------------|------|---------|----------------------|------|
| 1 | 标准 4 视图 | `[日线, 60分钟, 15分钟, 5分钟]` | `min_tf="5分钟"` | `TestGetMinTfAndCount::test_finds_min_tf` | ✅ |
| 2 | 只有日线和周线 | `[日线, 周线]` | `min_tf="日线"` | `TestBacktestIntegration::test_min_tf_determination`（配置1） | ✅ |
| 3 | 单视图 | `[15分钟]` | `min_tf="15分钟"` | `TestGetMinTfAndCount::test_single_view` | ✅ |
| 4 | 包含 1分钟 | `[日线, 1分钟]` | `min_tf="1分钟"` | — | ❌ 未覆盖 |

**额外已覆盖**：
- `test_all_same_tf` — 所有视图同周期
- `test_empty_configs_returns_empty` — 空配置返回 ("", 0)
- `test_unknown_tf_in_config` — 未知周期不崩溃
- `TestBacktestIntegration::test_min_tf_determination` — 4 种配置组合验证

**§6.1.3 统计：3/4 已覆盖，1 未覆盖**（"1分钟"周期场景）

---

### 6.1.4 `_synthesize_higher_tf_bar`（4 个设计用例）

| # | 设计文档用例 | 条件 | 期望输出 | test_backtest.py 方法 | 状态 |
|---|------------|------|---------|----------------------|------|
| 1 | 正常合成（3 天数据） | synth_data 有 3 行 | O=第1行O, H=max(H), L=min(L), C=第3行C | `TestSynthesizeHigherTfBar::test_synthesize_from_two_bars` / `test_synthesize_from_many_bars` | ✅ |
| 2 | 单日合成 | synth_data 有 1 行 | O=H=L=C=该行Close, is_synthesized=True | `TestSynthesizeHigherTfBar::test_single_bar_returns_none` | ⚠️ 设计-实现差异：设计期望返回合成 bar（O=H=L=C），实现返回 None（`<2 条返回 None`，见 scout L31） |
| 3 | 无数据 | synth_data 为空 | 返回 None | `TestSynthesizeHigherTfBar::test_empty_df_returns_none` | ✅ |
| 4 | 值校验 | 手工构造数据 | High>=Low, High>=Open, High>=Close... | `test_synthesize_from_many_bars`（隐式通过 OHLC 断言验证） | ⚠️ 隐式：未显式断言不等式 |

**额外已覆盖**：
- `test_none_returns_none` — None 输入防护

**§6.1.4 统计：2/4 已覆盖，2 部分覆盖**（含 1 个设计-实现差异）

---

### 6.1.5 `_get_period_boundary`（6 个设计用例）

| # | 设计文档用例 | 条件 | 期望输出 | test_backtest.py 方法 | 状态 |
|---|------------|------|---------|----------------------|------|
| 1 | 周线周三 | tf=周线, date=周三 | 本周五（若周五是交易日） | `TestPeriodBoundary::test_weekly_boundary_mid_week` | ⚠️ 设计-实现差异：设计期望周五，测试断言周日（`2026-06-21`） |
| 2 | 周线周五 | tf=周线, date=周五 | 本周五（自身） | `TestPeriodBoundary::test_weekly_boundary_friday` | ⚠️ 同上：测试断言周日 |
| 3 | 月线月中 | tf=月线, date=6/15 | 6/30 | `TestPeriodBoundary::test_monthly_boundary_mid_month` | ✅ |
| 4 | 月线月末 | tf=月线, date=6/30 | 6/30（自身） | — | ❌ 未覆盖 |
| 5 | 周五是假日 | tf=周线, date=周三, 周五假日 | 周四（前一交易日） | — | ❌ 未覆盖（需交易日历 mock） |
| 6 | 月线 2 月 | tf=月线, date=2/15 | 2/28 或 2/29 | `TestPeriodBoundary::test_monthly_boundary_february` | ✅ |

**额外已覆盖**：
- `test_daily_boundary` — 日线边界返回自身
- `test_quarterly_boundary_mid_quarter` — 季线 Q2 中 → Q2 末
- `test_quarterly_boundary_q4` — 季线 Q4 → 12/31
- `test_yearly_boundary` — 年线 → 12/31
- `_belongs_to_same_period` 系列测试（10 个）：same_week, same_month, same_quarter, same_year, same_period_daily

**§6.1.5 统计：3/6 已覆盖，1 部分覆盖，2 未覆盖**（含 1 个设计-实现差异）

---

## 6.2 集成测试覆盖

### 6.2.1 端到端回测流程（11 个设计用例）

| # | 设计文档用例 | 验证点 | test_backtest.py 方法 | 状态 |
|---|------------|--------|----------------------|------|
| 1 | 浏览 → 回测切换 | `_cb_mode=True`; 缓存含 4 个 tf; toast 显示 | `TestBacktestIntegration::test_full_backtest_flow`（Phase A→B） | ✅ |
| 2 | 点击 ▶ 播放 | bar_index 自动递增; 图表窗口跟随 | `TestBacktestIntegration::test_bar_index_advance` | ✅ |
| 3 | 播放中点击 ⏸ | 播放停止; `_is_playing=False` | `TestBacktestIntegration::test_playback_stops_at_end`（只测末尾停止，未测中间暂停） | ⚠️ 部分：未测播放中途暂停 |
| 4 | 播放中拖拽 slider | 播放自动暂停; bar_index 跳转 | — | ❌ 未覆盖 |
| 5 | 到达末尾 | 自动暂停; ▶ 按钮可用 | `TestBacktestIntegration::test_playback_stops_at_end` | ✅ |
| 6 | 点击 ◀ 后退 | bar_index 减 1 | `TestBacktestStateTransitions::test_step_back_from_zero`（边界测试） | ⚠️ 部分：仅测边界 |
| 7 | 点击 ⏮ 跳到开头 | bar_index=0; is_partial=True | `TestBacktestStateTransitions::test_goto_start` | ✅ |
| 8 | 点击 ⏭ 跳到最新 | bar_index=total-1; 满窗口 | `TestBacktestStateTransitions::test_goto_end` | ✅ |
| 9 | 更改速度 → 继续播放 | 新速度在下一个 cycle 生效 | — | ❌ 未覆盖 |
| 10 | 回测 → 切换回浏览模式 | `_cb_mode=False`; 缓存清除; 时间导航恢复 | `TestBacktestIntegration::test_full_backtest_flow`（Phase E）+ `test_browse_mode_unchanged` | ✅ |
| 11 | 浏览模式 → 修改 day_offset → 切换回测 | day_offset 不影响回测 | `TestBacktestIntegration::test_browse_mode_unchanged`（隐式） | ⚠️ 部分：未显式验证 day_offset=0 |

**§6.2.1 统计：7/11 已覆盖，3 部分覆盖，1 未覆盖**

---

### 6.2.2 多视图一致性（3 个设计用例）

| # | 设计文档用例 | 验证点 | test_backtest.py 方法 | 状态 |
|---|------------|--------|----------------------|------|
| 1 | 4 视图同一时间点 | 4 个图表窗口终点对应同一绝对时间 | `TestBacktestIntegration::test_multi_view_consistency` | ✅ |
| 2 | 日线视图满窗口后 | 4 个视图 K 线走势同步 | — | ❌ 未覆盖（需多视图渲染环境） |
| 3 | 跨周期 PnL 对齐 | 高周期 PnL 子图与主图时间对齐 | — | ❌ 未覆盖（需 PnL 计算 + 渲染环境） |

**§6.2.2 统计：1/3 已覆盖，2 未覆盖**

---

## 6.3 回归测试覆盖（浏览模式不受影响）

| # | 设计文档用例 | 验证方法 | test_backtest.py 方法 | 状态 |
|---|------------|---------|----------------------|------|
| 1 | `_load_chart_data` (bar_index=None) 正常 | 浏览模式图表正常加载 | `TestBacktestIntegration::test_browse_mode_unchanged` | ✅ |
| 2 | `day_offset` 功能正常 | 前后按钮调整 day_offset | — | ❌ 未覆盖 |
| 3 | 自动刷新正常 | 等待几个周期确认 | — | ❌ 未覆盖（需 Streamlit runtime） |
| 4 | 参数调整正常 | 修改参数，图表实时更新 | — | ❌ 未覆盖（需 Streamlit runtime） |
| 5 | 预设导入/导出正常 | 导入配置预设，参数面板更新 | — | ❌ 未覆盖（需 Streamlit runtime） |
| 6 | 双滤波模式正常 | 两条滤波线都显示 | — | ❌ 未覆盖（需渲染环境） |
| 7 | 跨周期 PnL 参考正常 | PnL 子图显示正确 | — | ❌ 未覆盖（需渲染环境） |
| 8 | 施密特触发正常 | 买卖点标记显示正确 | — | ❌ 未覆盖（需渲染环境） |
| 9 | 预测曲线正常 | 浏览模式下显示全部预测 | `TestLookAheadBiasPrevention::test_bar_index_none_includes_all_pairs` | ⚠️ 部分：验证了预测对不过滤，但未验证曲线渲染 |
| 10 | 数据健康检查正常 | health check expander 数据正确 | — | ❌ 未覆盖（需 Streamlit runtime） |

**§6.3 统计：1/10 已覆盖，1 部分覆盖，8 未覆盖**（大部分需要 Streamlit runtime/渲染环境）

---

## 6.4 手动验证清单映射

| # | bar_index | 检查项 | 预期结果 | test_backtest.py 方法 | 状态 |
|---|-----------|--------|---------|----------------------|------|
| 1 | 0 | 日线视图 bar 数 | 1 根，左侧留空 | `TestLoadBacktestWindow::test_bar_index_zero` + `TestBacktestIntegration::test_backtest_data_window_size` | ✅ |
| 2 | 0 | 周线/月线/季线视图 | 可能为空或少量 bar | — | ❌ 未覆盖 |
| 3 | 0 | 状态显示 | `bar 1/{total} \| {最早日期}` | `TestBacktestUIControls::test_status_shows_current_date`（bar_index=5，非 0） | ⚠️ 部分：未测 bar_index=0 时的状态文字 |
| 4 | n_pts - 1 | 日线视图 bar 数 | 恰好 n_pts 根 | `TestBacktestIntegration::test_backtest_data_window_size` | ✅ |
| 5 | n_pts - 1 | is_partial | False（日线） | — | ❌ 未显式断言 |
| 6 | 500（中间） | 日线窗口范围 | bar_index-99 到 bar_index，100 根 | `TestLoadBacktestWindow::test_bar_index_mid` + `test_backtest_data_window_size` | ✅ |
| 7 | 500（中间） | 高周期合成标记 | 周期中间显示合成标记 | `TestHigherTfSynthesis::test_mid_week_synthesis` | ✅ |
| 8 | total - 1 | 日线视图 | 最后 100 根，最新数据 | `TestLoadBacktestWindow::test_bar_index_last` | ✅ |
| 9 | total - 1 | 浏览模式对比 | 切换到浏览模式 day_offset=0，K 线相同 | — | ❌ 未覆盖 |
| 10 | 播放中 | 前视偏差 | 预测曲线不超过 bar_index | `TestLookAheadBiasPrevention::test_pairs_filtered_by_bar_index` + `test_no_future_price_in_truncation` | ✅ |

**§6.4 统计：6/10 已覆盖，1 部分覆盖，3 未覆盖**

---

## 总体覆盖率统计

| 章节 | 设计用例数 | ✅ 已覆盖 | ⚠️ 部分覆盖 | ❌ 未覆盖 | 覆盖率 |
|------|----------|----------|------------|----------|--------|
| §6.1.1 `_binary_search_le` | 10 | 9 | 0 | 1 | 90% |
| §6.1.2 `_load_backtest_window` | 11 | 7 | 1 | 3 | 64% |
| §6.1.3 `_get_min_tf_and_count` | 4 | 3 | 0 | 1 | 75% |
| §6.1.4 `_synthesize_higher_tf_bar` | 4 | 2 | 2 | 0 | 50% |
| §6.1.5 `_get_period_boundary` | 6 | 3 | 1 | 2 | 50% |
| **6.1 单元测试小计** | **35** | **24** | **4** | **7** | **69%** |
| §6.2.1 端到端流程 | 11 | 7 | 3 | 1 | 64% |
| §6.2.2 多视图一致性 | 3 | 1 | 0 | 2 | 33% |
| **6.2 集成测试小计** | **14** | **8** | **3** | **3** | **57%** |
| §6.3 回归测试 | 10 | 1 | 1 | 8 | 10% |
| §6.4 手动验证清单 | 10 | 6 | 1 | 3 | 60% |
| **总计** | **69** | **39** | **9** | **21** | **57%** |

### 覆盖率说明

- **§6.3 回归测试覆盖率低（10%）是预期的**：10 个回归用例中有 8 个需要 Streamlit runtime 或完整渲染环境（day_offset 交互、自动刷新、参数面板、预设导入导出、图表渲染等），不适合在单元/集成测试中实现。这些应在手动 QA 或 E2E 测试中覆盖。
- **排除 §6.3 后**的有效覆盖率为 **39/59 = 66%**。
- **高价值未覆盖项**集中在：§6.1.2 case 6（数据全在 cutoff 之后）、§6.1.1 case 10（重复值）、§6.1.2 case 11（季线不足）。

---

## 未覆盖用例优先级评估

| 优先级 | 用例 | 原因 | 可自动化 |
|--------|------|------|---------|
| **P0** | §6.1.1 case 10：`_binary_search_le` 重复值 | 核心算法正确性，简单可测 | ✅ 是 |
| **P0** | §6.1.2 case 6：tf 数据全在 cutoff_date 之后 | 边界条件，可能触发空窗口崩溃 | ✅ 是 |
| **P1** | §6.1.2 case 11：季线总 bar 不足 | 高周期边界场景，已在 TestFullBarGuard 中日线验证 | ⚠️ 可（需构造季线数据） |
| P1 | §6.1.3 case 4：包含 1分钟周期 | 低频场景，逻辑已被 test_finds_min_tf 覆盖 | ✅ 是 |
| P1 | §6.1.5 case 5：周五假日 → 周四 | 需交易日历，低频 | ❌ 需交易日历 mock |
| P2 | §6.2.1 case 4：slider 拖拽暂停 | UI 交互，需 Streamlit runtime | ❌ 需 runtime |
| P2 | §6.2.2 case 2-3：多视图一致性 | 需渲染环境 | ❌ 需 runtime |
| P3 | §6.3 cases 2-10：回归测试 | 大部分需 Streamlit runtime | ❌ 需 runtime |

---

## 补充测试（本次新增）

以下 3 个关键缺失用例已添加到 `tests/test_backtest.py`：

| # | 测试方法 | 覆盖的设计用例 | 说明 |
|---|---------|--------------|------|
| 1 | `TestBinarySearchLe::test_duplicate_values_returns_max_index` | §6.1.1 case 10 | 验证重复值时返回最大索引 |
| 2 | `TestLoadBacktestWindow::test_cutoff_before_all_tf_data` | §6.1.2 case 6 | 验证 tf 数据全在 cutoff 之后返回空 DataFrame |
| 3 | `TestPeriodBoundary::test_monthly_boundary_month_end` | §6.1.5 case 4 | 验证月线月末日期返回自身 |
