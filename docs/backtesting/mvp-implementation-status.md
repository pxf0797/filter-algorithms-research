# B-Cycle Half-Pair Strategy — MVP Implementation Status

> 跟踪 B 周期半边多空对策略各阶段的实现状态。
> 对应 `docs/backtesting/b-cycle-implementation-plan.md` 的 Phase 1-5。

---

## Phase 1: 基础函数 (filter_engine.py)

| # | 函数 | 状态 | 备注 |
|---|------|------|------|
| 1 | `_merge_segments` | ✅ 已存在 (L520) | 从 `_find_all_pairs` 抽取的合并逻辑 |
| 2 | `_find_current_half_pair` | ⬜ 待实现 | 单元测试已写 |
| 3 | `_get_higher_tf_direction` | ⬜ 待实现 | 单元测试已写 |
| 4 | `_c_pair_state` (MVP 3态) | ⬜ 待实现 | 单元测试已写 |
| 5 | `_run_half_pair_strategy` | ⬜ 待实现 | 单元测试已写 |
| 6 | `_signed_deviation` | ⬜ 待实现 | 单元测试已写 |

## Phase 2: 引擎集成

| # | 改动 | 位置 | 状态 |
|---|------|------|------|
| 7 | `_compute_strategy_pnl` 添加 strategy_mode/trade_signals 参数 | `filter_engine.py` | ⬜ 待实现 |
| 8 | `_compute_strategy_display` 半边策略条件路径 | `streamlit_app.py` | ⬜ 待实现 |

## Phase 3: 数据流

| # | 改动 | 位置 | 状态 |
|---|------|------|------|
| 9 | 扩展 `_pnl_{tf}` session_state 写入 (sig/filtered/half_pair) | `streamlit_app.py` | ⬜ 待实现 |
| 10 | `_find_higher_tf_in_views` | `streamlit_app.py` | ⬜ 待实现 |

## Phase 4: 日志和可视化

| # | 改动 | 位置 | 状态 |
|---|------|------|------|
| 11 | `log_half_pair_frame` | `backtest_logger.py` | ✅ 已完成 |
| 12 | `read_half_pair_log` | `backtest_logger.py` | ✅ 已完成 |
| 13 | 在 `_compute_strategy_display` 中集成日志调用 | `streamlit_app.py` | ⬜ 待实现 |
| 14 | PnL 曲线颜色和 Caption 文本区分 | `components/charts.py` | ⬜ 待实现 |

## Phase 5: 后续迭代

| 功能 | 状态 |
|------|------|
| `_c_pair_state` 升级到完整 5 态 | ⬜ v2 |
| `_signed_deviation` 阈值调优 | ⬜ v2 |
| 双线 AB 对比可视化 | ⬜ v2 |
| WEAK_ALIGN 降级入场选项 | ⬜ v2 |
| C_ENDED 梯度离场 | ⬜ v2 |

## 测试

| 文件 | 状态 | 说明 |
|------|------|------|
| `tests/test_half_pair_impl.py` | ✅ 已创建 | 7 个测试类, 20+ 个用例, @pytest.mark.strategy |
