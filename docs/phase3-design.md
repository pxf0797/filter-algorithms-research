# Phase3 详细设计方案

> 分支: feat/backtest-phase3 | 日期: 2026-07-04 | 基于 v1.0.0-backtest

## 一、总体目标

Phase3 聚焦于回测体验的完善：自动播放、策略回测、性能优化。

## 二、改进项详述

### P3-1: 自动播放功能 [Major]

**当前状态**: _is_playing 状态键已预留，但无播放逻辑。

**方案**:
- 在回测控制栏添加 播放/暂停 按钮
- 6档速度: 0.25x/0.5x/1x/2x/5x/10x
- 实现: `_run_backtest_play()` 函数，用 `time.sleep(interval)` + `st.rerun()`
- 到达末尾自动停止
- 播放时 slider 跟随移动

**函数签名**:
```python
def _run_backtest_play():
    """回测自动播放循环。"""
    if not AppState.get("_is_playing", False):
        return
    bar_index = AppState.get("_bar_index", 0)
    total = AppState.get("_min_tf_bar_count", 0)
    if bar_index >= total - 1:
        AppState.set("_is_playing", False)
        return
    speed = AppState.get("_play_speed", 1.0)
    time.sleep(1.0 / speed)
    AppState.set("_bar_index", bar_index + 1)
    st.rerun()
```

**涉及文件**: streamlit_app.py (+30行), state.py (+1 key)

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
| streamlit_app.py | +30 | +80 | +15 | +10 | +20 | +155 |
| services/filter_engine.py | — | +40 | — | — | — | +40 |
| state.py | +1 | — | — | — | — | +1 |
| **合计** | **+31** | **+120** | **+15** | **+10** | **+20** | **~+200** |

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

1. P3-3 (缓存优化) — 基础设施
2. P3-4 (ticker切换) + P3-5 (配置预热) — 状态完善
3. P3-1 (自动播放) — 用户交互
4. P3-2 (策略回测) — 功能增强
