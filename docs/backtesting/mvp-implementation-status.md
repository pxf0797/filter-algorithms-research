# MVP 实现状态

> 半边多空对策略最小可行版本实现跟踪

## 已实现

| 功能 | 文件 | 状态 |
|------|------|------|
| `_merge_segments` | `filter_engine.py` | ✅ |
| `_find_current_half_pair` | `filter_engine.py` | ✅ |
| `_get_higher_tf_direction` | `filter_engine.py` | ✅ |
| `_run_half_pair_strategy` | `filter_engine.py` | ✅ |
| `_compute_strategy_pnl` 加 `strategy_mode` 参数 | `filter_engine.py` | ✅ |
| `_find_all_pairs` 内部重构调用 `_merge_segments` | `filter_engine.py` | ✅ |
| 门控开关（sidebar half_pair_strategy） | `streamlit_app.py` | ✅ |
| C 周期同向确认（简化版） | `streamlit_app.py` | ✅ |
| 进入下一多空对离场 | `filter_engine.py` | ✅ |
| 趋势偏离离场 | `filter_engine.py` | ✅ |
| C 周期反向离场（最高优先级） | `filter_engine.py` | ✅ |

## 验证状态

| 验证项 | 方法 | 状态 |
|--------|------|------|
| `_find_all_pairs` 返回值不变 | A/B 对比 `_merge_segments` 重构前后 | ⚪ |
| `_compute_strategy_pnl` mode=None 不变 | 现有回测基线 PnL 逐元素断言 | ⚪ |
| UI 开关关闭后图表不变 | `half_pair_strategy=False` vs 改造前截图 | ⚪ |
| 单元测试覆盖 | tests/test_half_pair.py | ✅ |

## 后续迭代

| 功能 | 设计版本 | 说明 |
|------|---------|------|
| C 多空对状态门控（4/5 态） | v2/v3 | 目前仅用 `dir_C=±1/0` 三态，v2/v3 扩展为 4 态/5 态门控矩阵 |
| `C_ENDED` 提前离场 | v3 | C 周期当前半边结束时提前离场，不等 C 翻向 |
| `entry_void` 快速离场 | v2 | 入场后 N 根内无进展即离场，控制时间成本 |
| 断续度量化 / 有效起点重定义 | v4 | 量化 Sig=0 占比，排除噪声段后的起点更可靠 |
| gap 上限 | v4 | 半边内部允许的观望 gap 长度上限 |

## 使用方式

1. 在回测模式中勾选 "半边多空对策略"
2. 确保 B 周期和 C 周期均已加载数据
3. 开启 strategy 模式查看 PnL

## 参数参考

| 参数 | 默认值 | 含义 |
|------|--------|------|
| `N_confirm` | 2 | 反向段连续 bar 数才确认反转 |
| `MAX_DEV_PCT` | 4.0 | 趋势偏离阈值 (%) |
| `edge_width` | 3 | 边缘区宽度（bar 数） |
| `entry_require_c` | True | 入场是否要求 C 同向 |
| `exit_on_reverse` | True | 启用 进入下一多空对 离场 |
| `exit_on_deviation` | True | 启用 趋势偏离 离场 |
| `exit_on_c_reverse` | True | 启用 C 周期反向 离场 |
