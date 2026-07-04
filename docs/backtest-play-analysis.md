# 回测模式：前进下一个bar vs 播放 — 逻辑差异分析

## 1. 关键代码段定位

文件 `/Users/xfpan/claude/filter_research/filter_app/streamlit_app.py` 的 1641 行。

### 1.1 前进下一个bar（⏵按钮）

`_render_backtest_nav()` 函数，第 1197-1201 行：

```python
with col_nav[3]:
    if st.button("⏵", key="_bt_step_fwd", use_container_width=True,
                 disabled=bar_index >= total_bars, help="前进一个 bar"):
        st.session_state._bar_index = min(total_bars, bar_index + 1)
        _update_cutoff_and_rerun()
```

依赖的 `_update_cutoff_and_rerun()` 函数，第 1131-1140 行：

```python
def _update_cutoff_and_rerun():
    ticker = AppState.get("_fetched_ticker", "")
    min_tf = AppState.get("_min_tf", "")
    bar_index = st.session_state.get("_bar_index", 0)
    if min_tf and ticker:
        cutoff_date = _get_bar_date_from_db(ticker, min_tf, bar_index - 1)
        if cutoff_date:
            AppState.set("_bt_cutoff_date", cutoff_date)
    st.rerun()
```

### 1.2 播放/暂停按钮（▶/⏸）

`_render_backtest_nav()` 函数，第 1175-1195 行：

```python
with col_nav[2]:
    label = "⏸" if is_playing else "▶"
    if st.button(label, key="_bt_toggle_play", use_container_width=True, help=help_text):
        if is_playing:
            AppState.set("_is_playing", False)
            st.rerun()
        else:
            if bar_index >= total_bars:
                st.session_state._bar_index = min_n_pts
            ticker_v = AppState.get("_fetched_ticker", "")
            min_tf_v = AppState.get("_min_tf", "")
            if min_tf_v and ticker_v:
                cur = st.session_state.get("_bar_index", total_bars)
                d = _get_bar_date_from_db(ticker_v, min_tf_v, cur - 1)
                if d:
                    AppState.set("_bt_cutoff_date", d)
            AppState.set("_is_playing", True)
            st.rerun()
```

### 1.3 播放核心逻辑

`_run_backtest_play()` 函数，第 1220-1259 行：

```python
def _run_backtest_play():
    if not AppState.get("_is_playing", False):
        return False
    if not AppState.get("_cb_mode", False):
        AppState.set("_is_playing", False); return False
    bar_index = st.session_state.get("_bar_index", 0)
    total = AppState.get("_min_tf_bar_count", 0)
    if total <= 0:
        AppState.set("_is_playing", False); return False
    if bar_index >= total:
        AppState.set("_is_playing", False); return False
    st.session_state._bar_index = bar_index + 1
    ticker = AppState.get("_fetched_ticker", "")
    min_tf = AppState.get("_min_tf", "")
    if min_tf and ticker:
        cutoff_date = _get_bar_date_from_db(ticker, min_tf, bar_index)
        if cutoff_date:
            AppState.set("_bt_cutoff_date", cutoff_date)
    return True
```

### 1.4 播放循环驱动

`main()` 函数：
- 第 1525 行：`need_rerun = _run_backtest_play()` — 在 widget 渲染之前执行
- 第 1634-1637 行：`if need_rerun: time.sleep(1.0 / speed); st.rerun()` — 在所有内容渲染完成后驱动下一次循环

---

## 2. 对比分析表

| 维度 | 前进下一个bar (⏵) | 播放 (▶) |
|------|------------------|----------|
| 触发方式 | 用户点击 `_bt_step_fwd` 按钮 | 用户点击 `_bt_toggle_play` → `_is_playing=True` → `st.rerun()` |
| 状态变更 | `_bar_index += 1` → `_update_cutoff_and_rerun()`（含 `st.rerun()`） | `_run_backtest_play()`: `_bar_index += 1` → 更新 `_bt_cutoff_date` → 返回 `True` |
| 数据加载 | 同播放 — 底层渲染路径完全一致 | 同左 |
| 循环/重复机制 | 无自循环，一次点击只前进一个 bar | `main()` 生命周期循环：前进 bar → 渲染 → sleep → rerun → 再前进 |
| 速度控制 | 无 | `_play_speed` (0.25x~10x)，`time.sleep(1.0/speed)` 控制间隔 |
| 终止条件 | slider 达到 total 时按钮 disabled | `bar_index >= total` 或用户点击暂停或退出回测模式 |
| 占位执行时机 | 不占位执行 | `_run_backtest_play()` 在 `main()` 第一行执行（在 widget 渲染之前） |
| 依赖的 session_state | `_bar_index`, `_fetched_ticker`, `_min_tf`, `_bt_cutoff_date` | 同左 + `_is_playing`, `_play_speed`, `_play_speed_label`, `_min_tf_bar_count`, `_cb_mode` |

---

## 3. 根因分析

### 3.1 前进下一个bar 的实现机制

**本质**：一次性的 button callback + rerun。

1. 用户点击 ⏵ 按钮 → 执行 callback
2. `_bar_index += 1`，调用 `_update_cutoff_and_rerun()` 从 DB 查询新 bar 的日期，设置 `_bt_cutoff_date`，然后 `st.rerun()`
3. Streamlit 重新运行 `main()`，slider 读取更新后的 `_bar_index`，`_render_chart_fragment()` 使用新的 `window_start` 和 `cutoff_date` 渲染图表
4. 流程结束。等待用户下一次点击。

**关键特征**：无自循环。每次前进依赖用户的手动点击。

### 3.2 播放 的实现机制

**本质**：基于 Streamlit full-rerun 循环的模拟播放，通过 `_run_backtest_play()` + `time.sleep` + `st.rerun()` 构成闭环。

启动后每轮循环：
1. `main()` 第一行：`_run_backtest_play()` 检查 `_is_playing`，若 True 则将 `_bar_index += 1`，更新 `_bt_cutoff_date`，到末尾则停播
2. 渲染所有 sidebar widget（包括切换为 ⏸ 的按钮）和图表
3. `main()` 末尾：如果 `need_rerun=True`，`time.sleep(1.0/speed)` 后 `st.rerun()` 重新开始循环

### 3.3 差异根因

**两种模式的核心前进逻辑本质相同** — 都是 +1 bar → 更新 cutoff_date → rerun 触发页面重建。

**根本区别仅在于「谁触发 rerun」**：
- 前进下一个bar：button callback 触发一次 rerun，之后停在当前状态等待用户
- 播放：`main()` 末尾的 `st.rerun()` 触发下一轮，形成自循环

**播放模式的循环链条**：
```
main() 开头: _run_backtest_play() 前进 bar
    → 渲染所有 widget+chart（含 ⏸ 按钮）
    → main() 末尾: time.sleep → st.rerun()
    → 回到 main() 开头: _run_backtest_play() 再前进一次
    → ...（直到 bar_index >= total 停止）
```

---

## 4. 可行性结论与修改方案

### 4.1 能否实现连续前进？

**已经实现了，不需要改动。** 播放模式 (`_run_backtest_play`) 和前进下一个bar (⏵) 使用完全相同的 bar_index 递增逻辑：
- 播放第 1249 行：`st.session_state._bar_index = bar_index + 1`
- 前进第 1200 行：`st.session_state._bar_index = min(total_bars, bar_index + 1)`

唯一的细微差别是前进按钮有 `min(total_bars, ...)` 保护，而播放有独立的终止守卫（第 1243-1246 行检查 `bar_index >= total`），效果等价。

**结论：播放模式可以做到在任意位置连续前进下一个bar的效果，功能已经完整。**

### 4.2 如果观察到行为不一致，排查方向

1. **播放速度过慢**（如 0.25x = 4秒间隔），感觉像卡住 — 检查 `_play_speed` 设置
2. `_bar_index` **在末尾初始化**：如果一开始就在末尾会立即停止，但第 1183-1185 行已有处理，从头播放
3. **数据加载延迟覆盖 sleep**：`_load_chart_data` + parquet 写入耗时可能超过 `time.sleep(1.0/speed)`，大数据量下会感觉"跳过"了一些 bar

### 4.3 可选的小优化

如果有必要增加健壮性，可以在 `_run_backtest_play()` 中追加最小间隔保护，防止过密 rerun：

```python
# 在 _run_backtest_play() 返回 True 之前追加：
import time as _time
_now = _time.monotonic()
_last = AppState.get("_last_play_tick", 0)
if _now - _last < 0.1:
    _time.sleep(0.1 - (_now - _last))
AppState.set("_last_play_tick", _now)
```

**风险极低**：仅影响播放路径，不影响前进按钮，不影响数据正确性。

### 4.4 已知局限性（非bug）

- 播放是"伪连续"：每步都触发完整 Streamlit 脚本重建（DB 查询、parquet 读取、滤波计算、图表重建），这是 Streamlit 架构决定的
- 实际播放速度 = `max(1.0/speed, 单次完整渲染耗时)`，渲染过慢时速度会不准确
