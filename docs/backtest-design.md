# Filter Research 回测方案设计文档

> 版本: v1.0 | 日期: 2026-06-29 | 基于代码版本 v10.10

## 1. 概述与目标

### 1.1 回测目标

在现有 2x2 四视图滤波分析工具上，增加 bar 级回测功能。当前系统一次性计算全部 N 个 bar 的所有信号；回测需要的是对于每个 bar_index，仅使用 <= bar_index 的数据计算信号，两者的计算语义完全不同。

### 1.2 核心能力

- **逐 bar 导航**: 以最小显示周期为粒度，前进/后退/跳转到开头/跳转到最新
- **自动播放**: 逐 bar 向前推进，速度可调
- **高周期自动合成**: 从低周期 bars 合成高周期最后一根 bar 的 OHLC
- **严格前视偏差防护**: 所有信号计算仅使用截止到 bar_index 的数据
- **视觉标注**: 当前 bar 位置竖线标记，未来区域遮罩

### 1.3 设计原则

- **最小侵入**: 仅修改 2 个文件（streamlit_app.py ~303 行新增 + 6 处修改，state.py +7 行）
- **零新依赖**: 不引入任何第三方库
- **不影响现有功能**: 浏览模式完全不受影响，通过回测模式开关隔离
- **数据流保留**: 纯函数（滤波、施密特、配对、拟合、PnL）输入输出语义不变

## 2. 核心概念

### 2.1 Playhead（播放头）

bar_index 以 4 个视图中最小周期的 bar 为刻度，从 0 到 `total_bars - 1`。移动播放头即改变数据可见窗口 `data[:bar_index + 1]`。所有视图共享同一个 bar_index，每个视图根据自身周期密度映射到本地索引。

### 2.2 最小显示周期

遍历 configs 中各视图的 tf 字段，取 ALL_TFS 中索引最大的（最精细的）作为 `_min_tf`。例如如果有 1 分钟、5 分钟、日线、周线四个视图，最小周期为 1 分钟。

### 2.3 多周期 Bar 对齐

全局 bar_index 以最小周期为刻度。各视图根据自身周期密度，通过以下映射找到本地索引：

```python
local_bar_index = np.searchsorted(dates, min_tf_dates[global_idx], side="right") - 1
```

此操作为 O(log n)，无须循环。

### 2.4 高层级周期合成

沿 TF_HIERARCHY 逐级向上。高周期最后一个 bar 的 OHLC 由低一层级周期的 bars 合成：
- **Open**: 首 bar Open
- **High**: max(Highs)
- **Low**: min(Lows)
- **Close**: 末 bar Close

历史 bars 使用实际数据，仅合成最后一个 bar。例如 5 分钟视图的 bar_index 对应日线视图时，日线最后一个 bar 可能尚未完整收出，需要从 5 分钟 bars 合成。

### 2.5 前视偏差防护

数据截断到 `[:bar_index + 1]` 后，所有纯函数（滤波、施密特、配对、拟合、PnL）自动安全。唯一需要显式过滤的是 `_compute_prediction_pairs` 中 `pair_end <= bar_index`。核心策略：

1. `_load_chart_data` 加载全部 N 条数据（保留上下文）
2. `_render_chart` Step 1 之后截断：`t = t[:bar_index + 1]`，`noisy = noisy[:bar_index + 1]`，`ohlc = ohlc.iloc[:bar_index + 1]`，`dates = dates[:bar_index + 1]`
3. 所有后续计算使用截断数组

**注意事项**:
- Savgol 滤波需要对称窗口，截断后首尾部分点可能不可用 -- 用 np.nan 标记
- EWMA 滤波在截断后累计值正确（从历史起点的 EWMA 是因果的）
- Kalman 同样因果安全
- 截断后的施密特信号与全量计算的结果在前 N 个 bar 保持一致（这是正确行为 -- 回测模拟的是当时已知的信息）

## 3. 数据模型

### 3.1 当前核心数据结构

| 数据结构 | 类型 | 用途 |
|----------|------|------|
| `configs` | `list[dict]` | 4 个视图的参数配置，每个包含 tf, n_pts, show_sch, pv 等 |
| `cfg` | `dict` | 单个视图的完整配置 |
| `t` | `np.ndarray(float)` | 时间索引 [0, 1, 2, ..., N-1] |
| `noisy` | `np.ndarray(float)` | 收盘价序列 |
| `ohlc` | `pd.DataFrame` | OHLC 价格数据 |
| `dates` | `pd.DatetimeIndex` | 时间戳索引 |
| `filtered` | `np.ndarray(float)` | 滤波后价格 |
| `schmitt` | `dict` | 施密特信号：sig, eps, sigma_v, mu_v, dur |
| `all_pairs` | `list[tuple[int,int]]` | 多空切换对 (start, end) |
| `pred_pairs` | `list[dict]` | 预测曲线数据 |
| `long_pnl` | `np.ndarray(float)` | 做多 PnL 曲线（基准 100） |
| `short_pnl` | `np.ndarray(float)` | 做空 PnL 曲线（基准 100） |
| `trade_records` | `list[dict]` | 每笔交易的详情 |

### 3.2 新增状态键（state.py +7 行）

| 状态键 | 类型 | 默认值 | 用途 |
|--------|------|--------|------|
| `_cb_mode` | `bool` | `False` | 回测模式开关 |
| `_bar_index` | `int` | `0` | 当前 bar 位置 (0-indexed) |
| `_is_playing` | `bool` | `False` | 播放中 |
| `_play_speed` | `float` | `0.5` | 播放速度（秒/步） |
| `_min_tf` | `str` | `""` | 最小周期名 |
| `_min_tf_bar_count` | `int` | `0` | 总 bar 数 |
| `_bt_data_cache` | `dict` | `{}` | 全量数据缓存（key: tf） |

添加到 `state.py` 的 `SYSTEM_KEYS` 列表中，`AppState.init_defaults()` 自动初始化。

### 3.3 状态转换

```
浏览模式 ----------> 回测模式(暂停) ----------> 回测模式(播放中)
      <----------                      <----------
```

- **浏览 -> 回测**: 切换 radio 时触发全量数据加载到 `_bt_data_cache`，bar_index 设为 0，显示 spinner + toast 提示
- **回测 -> 浏览**: 清除 `_bt_data_cache` 和回测状态，恢复原有 day_offset 控制
- **暂停 <-> 播放**: 仅切换 `_is_playing`，数据不变

## 4. UI/UX 设计

### 4.1 布局方案

- **侧边栏**: 模式切换 radio（浏览/回测）+ 紧凑状态显示（当前 bar 时间 + bar_index/total）
- **主区域 2x2 上方**: 完整控制条（导航按钮 + 速度选择 + 进度 slider）
- **图表区域**: 金色竖线标记 + 未来遮罩 + 角标

### 4.2 导航控件

| 按钮 | 功能 | 对应操作 |
|------|------|---------|
| ⏮ 开头 | 跳到第一个 bar | `bar_index = 0` |
| ◀ 后退 | 后退一个 bar | `bar_index -= 1` |
| ▶/⏸ 播放/暂停 | 切换自动播放 | `is_playing = not is_playing` |
| ▶▶ 前进 | 前进一个 bar | `bar_index += 1` |
| ⏭ 最新 | 跳到最后一个 bar | `bar_index = max` |

速度选择：0.25x / 0.5x / 1x / 2x / 5x / 10x，对应 4000ms ~ 100ms/步。

### 4.3 图表标注

在每个视图的 Plotly Figure 上叠加三层视觉标记：

1. **金色竖线**: 使用 `fig.add_vline(x=local_bar_index, line_width=2, line_color="gold")` 标记当前回测位置
2. **未来遮罩**: 使用 `fig.add_shape(type="rect", x0=bar_index, x1=total_bars-1, y0=0, y1=1, fillcolor="gray", opacity=0.3, layer="above")` 覆盖未来数据区域
3. **模式角标**: 在图表标题或注解中追加 "回测模式" 标识

被遮罩区域的数据仍然加载并传入计算函数（部分函数如滤波需要上下文），但视觉上标记为"未来"。

## 5. 数据流与集成点

### 5.1 回测介入的数据流

```
SQLite market.db
  └─ query_kline(ticker, tf, n_pts, day_offset=0)     ← day_offset=0 加载全量
      └─ data_loader._sync_to_display(code, tf, 0, n_pts)
          └─ _load_chart_data(..., bar_index=None)     ← 新增 bar_index 参数
              ├─ [回测] 加载全部 N 条数据到 _bt_data_cache
              ├─ [浏览] 保持现有 day_offset 截断逻辑
              │
              └─ _render_chart(..., bar_index=None)    ← 新增 bar_index 参数
                  ├─ Step 1: 截断: t = t[:bar_index+1], ...
                  ├─ Steps 2-10: _compute_filters, _compute_schmitt_trigger,
                  │              _find_all_pairs, _compute_prediction_pairs,
                  │              _compute_strategy_pnl (使用截断后的数组)
                  ├─ [回测] 过滤: _compute_prediction_pairs 中 pair_end <= bar_index
                  └─ Step 11: _add_backtest_overlay (金色竖线 + 遮罩 + 角标)
```

### 5.2 集成点清单（8项）

| ID | 集成点 | 位置 | 修改策略 |
|----|--------|------|---------|
| IP-1 | day_offset -> bar_index 转换 | `_render_time_nav`, `_render_chart`, `_load_chart_data` | 新增回测模式 state key 决定使用 day_offset 还是 bar_index |
| IP-2 | 最小周期确定 | `main()` 循环 configs | 遍历 configs 的 tf 字段，ALL_TFS.index() 找最小 |
| IP-3 | 图表遮罩/标注 | `_render_chart` Step 11 后 | fig.add_vline + fig.add_shape + 标题注解 |
| IP-4 | 导航控件 | `_render_time_nav` 区域 | 新增 bar 级导航按钮，cb_mode 控制显示 |
| IP-5 | 信号前视偏差 | `_compute_schmitt_trigger`, `_find_all_pairs`, `_compute_prediction_pairs` | 数组截断自动修复，pair_end 显式过滤 |
| IP-6 | Session State | `state.py` SYSTEM_KEYS | 新增 7 个回测状态键 |
| IP-7 | PnL 重算 | `_compute_strategy_pnl` | 全量重算（截断后数据量变小），可选增量缓存优化 |
| IP-8 | 2x2 视图协调 | `main()` 的 configs 构建和 2x2 渲染 | bar_index 传入每个 fragment，各视图按 tf 映射本地索引 |

### 5.3 核心代码逻辑

```python
# 数据截断（_render_chart Step 1 之后）
def _render_chart(market, ticker_code, cfg, key, compact=True,
                  day_offset=0, higher_pnl=None, bar_index=None):
    t, noisy, ohlc, ticker_full, dates, err = _load_chart_data(...)

    if bar_index is not None:
        t = t[:bar_index + 1]
        noisy = noisy[:bar_index + 1]
        ohlc = ohlc.iloc[:bar_index + 1]
        dates = dates[:bar_index + 1]

    # Steps 2-10 unchanged

    if bar_index is not None:
        _add_backtest_overlay(fig, local_bar_index, len(t), dates)
```

```python
# 播放循环
def _run_backtest_play(market, ticker_code, configs, play_speed):
    if not st.session_state.get("_is_playing", False):
        return
    bar_index = st.session_state.get("_bar_index", 0)
    total = st.session_state.get("_min_tf_bar_count", 0)
    if bar_index >= total - 1:
        st.session_state["_is_playing"] = False
        return
    time.sleep(play_speed)
    st.session_state["_bar_index"] = bar_index + 1
    st.rerun()
```

## 6. 实现路线图

### Phase 1: 基础状态与模式切换（~30min）

**文件**: `state.py` +7 行，`streamlit_app.py` +40 行

**新增函数**:
- `_render_backtest_mode_switch()` -- 侧边栏 radio 控件，切换浏览/回测模式
- `_get_min_tf_and_count()` -- 遍历 configs 计算最小周期和总 bar 数

**修改**:
- `_render_time_nav` -- 根据 `_cb_mode` 状态决定显示浏览控件还是回测控件
- `main()` -- 回测模式下初始化 bar_index，传入各 fragment

**验证**: 模式切换后 spinner 出现、toast 提示、数据缓存加载

### Phase 2: 数据截断管线（~1h）

**修改函数**:
- `_render_chart(..., bar_index=None)` -- 新增参数，Step 1 后截断数组
- `_load_chart_data(..., bar_index=None)` -- 回测模式加载完整数据

**新增函数**:
- `_truncate_arrays(t, noisy, ohlc, dates, bar_index)` -- 统一截断入口

**验证**: bar_index=N 时图表只显示前 N+1 个 bar 的数据

### Phase 3: 多周期 Bar 对齐（~1h）

**新增函数**:
- `_global_to_local_bar_index(dates, global_idx, min_tf_dates)` -- searchsorted 映射
- `_synthesize_higher_tf_bar()` -- 高周期最后一根 bar 的 OHLC 合成

**验证**: 日线 bar_index=100 时，周线视图显示到对应周

### Phase 4: 图表标注与遮罩（~45min）

**新增函数**:
- `_add_backtest_overlay(fig, local_bar_index, total_bars, dates)` -- 金色竖线 + 遮罩 + 角标

**修改**: `_render_chart` Step 11 后调用

**验证**: 图表上可见金色竖线、未来遮罩、角标

### Phase 5: 播放动画与统计（~1h）

**新增函数**:
- `_render_backtest_controls()` -- 导航按钮 + 速度选择 + 进度 slider
- `_render_backtest_status()` -- 当前 bar 时间 + bar_index/total 显示
- `_run_backtest_play()` -- time.sleep + st.rerun 循环

**验证**: 播放时图表逐 bar 更新，到达末尾自动停止

### Phase 6: 集成测试与验证（~30min）

**测试用例**:

| 测试 | 方法 | 预期结果 |
|------|------|---------|
| 信号一致性 | 分别设 bar_index=N 和 bar_index=N-1，比较 sig_t[:N] | 前 N 个值相同 |
| 配对过滤 | 回测模式下检查 all_pairs | 所有 pair_end <= bar_index |
| 播放 vs 单步 | 播放到 bar_index=K 的结果 vs 直接跳到 K | PnL 完全一致 |
| 未来价格 | 检查 _compute_strategy_pnl 的 entry 和 exit | 均 <= bar_index |

## 7. 前视偏差风险评估

### 7.1 高风险区域（必须修正）

| 函数 | 风险描述 | 修复策略 |
|------|---------|---------|
| `_find_all_pairs` | 扫描全数组 sig_t 查找多空切换对 | 截断 sig_t 输入后自动修复 |
| `_compute_prediction_pairs` | pair_end 可能 > bar_index | 过滤 pair_end <= bar_index |
| `_compute_schmitt_trigger` | EWMA 有记忆但因果安全 | 截断后自动修复 |
| `_fit_parabolic` / `_fit_physics_parabola` | 对 y[start:end+1] 最小二乘拟合 | 截断后自动修复 |

### 7.2 中风险区域（需验证）

| 函数 | 风险描述 |
|------|---------|
| `_compute_strategy_pnl` | PnL 扫描 entry_idx 到 exit_idx，pair_end 被正确截断后安全 |
| `_compute_strategy_display` | 仅负责显示和 session_state 存储，无计算风险 |

### 7.3 前视偏差处理策略总结

```
回测模式下，数据截断策略：
1. _load_chart_data 加载全部 N 条数据（保留上下文给滤波函数）
2. _render_chart Step 1 之后，截断：
   t = t[:bar_index+1]
   noisy = noisy[:bar_index+1]
   ohlc = ohlc.iloc[:bar_index+1]
   dates = dates[:bar_index+1]
3. 所有后续计算（滤波、施密特、配对、拟合、PnL）使用截断数组
4. _compute_prediction_pairs 额外过滤 pair_end <= bar_index
5. 这样确保任何算法都无法"看到"未来数据
```

### 7.4 现有 PnL 计算正确性验证

当前 `_compute_strategy_pnl` 计算无前视偏差：
- `all_pairs` 中的 `pair_end` 是信号切换的位置
- 每笔交易的 `entry_idx = pair_end`，`exit_idx` 通过逐 bar 扫描 `predicted_price` 和 `sig` 找到
- 扫描从 `entry_idx + 1` 到 `n - 1`，使用实际价格 `filtered[i]` 判断止损
- 止盈（Sig 反转）是因果的：只检查已发生的信号变化

唯一风险：`_find_all_pairs` 返回的 `pair_end` 大于当前 bar_index 时，该笔交易使用了未来入场信号。这就是为什么必须截断 sig_t 后再计算。

## 8. 文件改动清单

### 8.1 修改范围总结

| 文件 | 改行数 | 风险 | 说明 |
|------|--------|------|------|
| `streamlit_app.py` | ~303 行 | 高 | 8 个新函数 + 6 处函数修改，数据流管线核心 |
| `state.py` | +7 行 | 低 | 仅新增 SYSTEM_KEYS 条目 |
| 其余 6 个文件 | 0 行 | 无 | 纯函数输入输出不变 |

### 8.2 新增函数（8个）

| 函数 | 所在 Phase | 用途 |
|------|-----------|------|
| `_render_backtest_mode_switch()` | P1 | 侧边栏模式切换 radio |
| `_get_min_tf_and_count()` | P1 | 计算最小周期和总 bar 数 |
| `_truncate_arrays()` | P2 | 统一数组截断 |
| `_global_to_local_bar_index()` | P3 | 跨周期 bar 索引映射 |
| `_synthesize_higher_tf_bar()` | P3 | 高周期 OHLC 合成 |
| `_add_backtest_overlay()` | P4 | 图表标注和遮罩 |
| `_render_backtest_controls()` | P5 | 导航控件 |
| `_render_backtest_status()` | P5 | 状态显示 |

### 8.3 修改函数（6个）

| 函数 | 修改内容 |
|------|---------|
| `_render_chart()` | 新增 bar_index 参数，Step 1 后截断，Step 11 后遮罩 |
| `_render_chart_fragment()` | 传递 bar_index 和 cb_mode |
| `_load_chart_data()` | 可选 bar_index 参数，回测模式加载完整数据 |
| `main()` | 回测模式传递 bar_index，计算 _min_tf |
| `_render_time_nav()` | 新增回测导航控件，与现有偏移导航共存 |
| `_compute_prediction_pairs()` | 过滤 pair_end <= bar_index |

## 9. 风险与注意事项

| 风险 | 缓解措施 |
|------|---------|
| st.rerun 触发数据重载 | `_load_chart_data` 检查 `_cb_mode`，用 `_bt_data_cache` 绕过 `_sync_to_display` |
| 播放卡顿 | 初次全量加载到 `_bt_data_cache`，后续 0 I/O；播放仅触发 st.rerun |
| 高周期合成精度 | 仅合成最后一个 bar；历史 bars 使用实际数据 |
| 施密特信号截断后结果不同 | 这是正确行为 -- 回测模拟的是当时已知的信息，不是"修正后的"信号 |
| 播放到达末尾 | `_run_backtest_play` 检查 `bar_index >= total - 1` 时自动停止 |
| 模式切换时数据闪烁 | spinner + toast 提示加载中 |

## 附录

### A. 术语表

| 术语 | 定义 |
|------|------|
| Playhead | 回测当前位置指针，以 bar_index 表示 |
| bar_index | 以最小周期 bar 为单位的播放头位置（0-indexed） |
| 前视偏差 | 使用了未来数据导致回测结果失真 |
| 高层合成 | 从低周期 bars 合成高周期最后一根 bar 的 OHLC |
| 最小显示周期 | 4 个视图中时间粒度最精细的周期 |
| 全局 bar_index | 以最小周期为刻度的回测位置索引 |
| 本地 bar_index | 各视图根据自身 tf 密度映射后的本地索引 |

### B. 集成顺序

```
Phase 1: 状态 + UI ----------→ Phase 2: 数据截断 ------→ Phase 3: Bar 对齐
                                     ↓                          ↓
                              Phase 4: 图表标注 ←---------------+
                                     ↓
                              Phase 5: 播放动画
                                     ↓
                              Phase 6: 集成测试
```
