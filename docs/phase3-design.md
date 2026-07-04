# Phase3 详细设计方案

> 分支: feat/backtest-phase3 | 日期: 2026-07-04 | 基于 v1.0.0-backtest

## 一、总体目标

Phase3 聚焦于回测体验的完善：自动播放、策略回测、性能优化。

## 二、改进项详述

### P3-1: 回测导航控件 [Major]

**当前状态**: 只有 slider + 模式 radio，缺少导航按钮。`_is_playing` 状态键已预留。

**方案**: 在回测控制栏添加完整的导航按钮组：

```
┌─────────────────────────────────────────────────────┐
│ ⏮  ◀  ▶ / ⏸  ▶▶  ⏭    速度: [1x ▼]              │
│ [━━━━━━━━━━━●━━━━━━━━━━]  bar 500/2500             │
└─────────────────────────────────────────────────────┘
```

**按钮功能**:
| 按钮 | 功能 | 键盘 | 边界行为 |
|------|------|------|---------|
| ⏮ | 跳到开头 | Home | bar_index = N (窗口最小值) |
| ◀ | 后退一步 | Left | min(N, bar_index - 1) |
| ▶/⏸ | 播放/暂停 | Space | 切换 _is_playing |
| ▶▶ | 前进一步 | Right | max(total, bar_index + 1) |
| ⏭ | 跳到末尾 | End | bar_index = total |

**速度选择**: 6档 — 0.25x / 0.5x / 1x / 2x / 5x / 10x

**播放实现**:
```python
def _run_backtest_play():
    """回测自动播放循环。到达末尾自动停止。"""
    if not AppState.get("_is_playing", False):
        return
    bar_index = AppState.get("_bar_index", 0)
    total = AppState.get("_min_tf_bar_count", 0)
    if bar_index >= total:
        AppState.set("_is_playing", False)
        return
    speed = AppState.get("_play_speed", 1.0)
    time.sleep(1.0 / speed)
    AppState.set("_bar_index", bar_index + 1)
    st.rerun()
```

**涉及文件**: streamlit_app.py (~45行新控件), state.py (~2 keys: _is_playing, _play_speed)

---

### P3-1b: Slider 范围修正 — 窗口结束位置 [Major]

**当前状态 (Phase2)**: slider 表示窗口起始位置 `window_start`，范围 `0 ~ total - N`。用户拖动到位置 X 时，显示 [X, X+N) 的数据。

**问题**: 
- slider 最小值 0，但 bar 0 时显示 N 条数据，其中有 N 条是 bar 0 及之后
- 用户不理解"窗口起始位置"的语义
- 与直觉相反：用户想看"到第 500 条 bar 为止的 N 条数据"，而非"从第 380 条开始的 N 条"

**方案**: slider 改为窗口**结束位置**，范围 `N ~ total`：
```
修改前: window_start ∈ [0, total-N]
       显示 bars[window_start : window_start+N]

修改后: bar_index ∈ [N, total]
       显示 bars[bar_index-N : bar_index]
```

**具体改动**:
1. `_render_backtest_mode` 中 slider: `min_value=N, max_value=total`
2. `_bar_index` 语义变更: 从"窗口起始"改为"窗口结束"
3. `cutoff_date` 计算简化: 直接从 `bar_index` 位置取日期（不需要 `+ N - 1`）
4. `_sync_to_display`: `cutoff_date` 仍然是窗口最后一条的日期，查询 `WHERE ts <= cutoff_date ORDER BY ts DESC LIMIT N`

**效果**: 用户拖动 slider 到 500，图表显示"截止到第 500 条 bar 的最后 N 条数据"。

---

### P3-2: 跨周期策略回测 [Major]

**当前状态**: 策略 PnL 计算存在，但未充分利用回测窗口。

**方案**:
- 回测模式下，基于窗口数据计算策略 PnL
- 在窗口内逐 bar 模拟交易（用 Schmitt 信号 + 预测对）
- 统计窗口内的胜率、盈亏比、最大回撤
- 在侧边栏显示回测统计面板

**涉及文件**: streamlit_app.py (+80行), services/filter_engine.py (+40行)

---

### P3-3: Parquet 缓存优化 [Minor]

**当前状态**: 每次 `_sync_to_display` 都重写 parquet，回测 slider 移动时产生冗余 I/O。

**方案**:
- 增加缓存标记 `_bt_data_synced` 
- 仅在进入回测模式时做一次全量同步
- slider 移动时只更新 cutoff_date，不重写 parquet
- 浏览模式下恢复窗口写入

**涉及文件**: streamlit_app.py (+15行)

---

### P3-4: Ticker 切换完善 [Minor]

**当前状态**: Phase2 已实现 ticker 切换时刷新回测状态。待完善：应在 `_render_backtest_mode` 中检测 `_bt_last_ticker` 变化并自动刷新 UI。

**方案**:
- `_render_backtest_mode` 入口检测 ticker 变化
- 变化时自动调用 `_get_min_tf_and_count` + `_save_backtest_config`
- 无需用户手动操作

**涉及文件**: streamlit_app.py (+10行)

---

### P3-5: 配置缓存预热 [Minor]

**当前状态**: `_load_backtest_config` 已删除。重新设计配置缓存预热。

**方案**:
- 进入回测模式时，首先尝试从 `backtest_config.json` 加载已有配置
- 如果 ticker 匹配，直接使用缓存的 min_tf/bar_count，跳过 DB 查询
- 如果 ticker 不匹配或文件不存在，重新计算并保存

**涉及文件**: streamlit_app.py (+20行)

---

## 三、文件变更预估

| 文件 | P3-1 | P3-2 | P3-3 | P3-4 | P3-5 | 合计 |
|------|------|------|------|------|------|------|
| streamlit_app.py | +50 | +80 | +15 | +10 | +20 | +175 |
| services/filter_engine.py | — | +40 | — | — | — | +40 |
| state.py | +2 | — | — | — | — | +2 |
| **合计** | **+52** | **+120** | **+15** | **+10** | **+20** | **~+217** |

## 四、测试计划

| 改进项 | 测试用例 |
|--------|---------|
| P3-1 | 播放/暂停切换、6档速度、到达末尾自动停止 |
| P3-2 | 窗口内策略PnL计算、胜率统计、空窗口 |
| P3-3 | 缓存命中/未命中、slider移动不重写 |
| P3-4 | ticker变化自动刷新、同ticker不变 |
| P3-5 | 缓存加载、ticker不匹配重新计算 |

预估新增测试: ~20个用例

## 五、风险评估

| 风险 | 级别 | 缓解 |
|------|------|------|
| 自动播放 timer 与 st.rerun 交互不稳定 | Medium | 使用简单 time.sleep + st.rerun 方案，已验证可行 |
| 策略回测计算量 | Low | 窗口大小限定为 n_pts(≤300)，计算量可控 |
| 缓存一致性 | Low | 基于 ticker+min_tf 双重校验 |

## 六、实施顺序

1. P3-1b (slider 范围修正) — 语义修正，影响后续所有功能
2. P3-1 (导航控件) — 用户交互
3. P3-3 (缓存优化) — 基础设施
4. P3-4 (ticker切换) + P3-5 (配置预热) — 状态完善
5. P3-2 (策略回测) — 功能增强
