# BS 标记级联失效诊断报告 — 3690_HK_2

## 1. 问题现象
- 操作周期=日线时，60分钟视图无任何 BS 标记（entry_markers 和 exit_markers 均为空）

## 2. 根因（★ 主要根因）

### 时区不兼容导致 `_find_date_index` 全线返回 None

**位置**: `services/bs_marker.py`, `_find_date_index` (lines 23-53)

**机制**:
1. 日线的 `dates` 是 tz-naive (e.g., `2026-06-01 00:00:00`)
2. 60分钟的 `dates` 是 tz-aware (e.g., `2026-05-29 12:30:00+08:00`)，来自数据库 datetime 字段自动附加 `+08:00`
3. `pd.Timestamp(target_date) < pd.Timestamp(dates[0])` 比较时 pandas 抛出 `TypeError: Cannot compare tz-naive and tz-aware timestamps`
4. `except Exception: return None` (line 52) 静默吞掉异常
5. `_find_date_index` 返回 `None` → `_compute_cascade_markers` 中 `start_bar is None` → `continue` 跳过所有 marker
6. 最终: `compute_bs_markers` 返回 `{"entry_markers": [], "exit_markers": []}` — 零标记

**验证**:
```
pd.Timestamp('2026-06-01 00:00:00') < pd.Timestamp('2026-05-29 12:30:00+08:00')
→ TypeError: Cannot compare tz-naive and tz-aware timestamps
```
修复后（`tz_localize(None)` 归一化）:
```
_find_date_index 对 2026-06-01 → 返回 4 (正确)
_find_date_index 对 2026-06-05 → 返回 32 (正确)
```

**修复状态**: [x] commit 806140e — 比较前用 `tz_localize(None)` 归一化双方时区；新增 2 个 tz 测试用例，41/41 pass

## 3. 次级发现：数据窗口不对齐

| 维度 | 日线 | 60分钟 |
|------|------|--------|
| bar 数 | 200 根 | 200 根 |
| 时间覆盖 | ~10 个月 (2025-09-12 ~ 2026-07-10) | ~36 个交易日 (2026-05-29 ~ 2026-07-10) |
| Schmitt pairs | 16 对 (8 long, 8 short) | 7 对 (4 long, 3 short) |

**窗口重叠分析**:
- 日线覆盖 10 个月，60分钟仅覆盖最近 36 个交易日
- 日线 16 个 entry 中仅 2 个落在 60 分钟窗口内 (2026-06-01, 2026-06-05)
- 其余 14 个 entry 日期早于 60 分钟窗口起始日，`_find_date_index` 返回 0
- 导致所有早于窗口的 entry 全部锚定到 bar 0，产生重复标记：多个日线信号映射到同一 60 分钟 pair

**影响**: 60 分钟图上出现大量重复 BS 标记，视觉效果混乱。tick 不同但都落在同一 pair 上是因为 `_find_date_index` 的 `target_ts < first_date → return 0` 逻辑。

## 4. 其他潜在风险（静态分析发现，非当前根因）

以下风险来自对 `bs_marker.py`、`streamlit_app.py`、`sidebar.py` 的静态代码审查，当前未被触发但可能在特定条件下导致级联失效：

### 4.1 Schmitt 未启用的级联静默失败 (Dimension 5B/5E)
- **位置**: `bs_marker.py` line 118-119 (`schmitt is None → return empty`)
- **场景**: 用户在操作 TF 启用 Schmitt，但级联 TF 上未启用
- **后果**: 级联 TF 的 `_compute_cascade_markers` 被调用时 `schmitt=None` → 直接返回空，无任何警告
- **评级**: 概率 MEDIUM x 影响 HIGH
- **建议**: 在 `_render_chart_fragment` 中添加 guard：当 `_show_bs=True` 且 `schmitt is None` 时提示用户

### 4.2 Fragment 增量渲染导致 session_state 过期 (Dimension 3A/3B)
- **位置**: `streamlit_app.py` lines 1966-1972 (全量渲染顺序) vs `@st.fragment` 增量重渲染
- **场景**: 自动刷新获取新数据后，用户仅操作 60 分钟视图参数，触发该 fragment 单独重渲染
- **机制**: 60 分钟 fragment 读取 `_bs_日线` 时，日线 fragment 未重渲染 → 使用旧数据的 stale BS markers
- **评级**: 概率 LOW (自动刷新通常触发 `st.rerun()` 全量渲染)
- **缓解**: 全量页面加载时 `sorted_views` 保证 TF 降序渲染顺序 (季线 → 月线 → …)

### 4.3 n_pts 不匹配导致 marker 丢弃 (Dimension 4B)
- **位置**: `bs_marker.py` lines 47-48 (`target_ts > last_date → return None`)
- **场景**: 不同 TF 视图设置不同的 bar 数量（如日线 n_pts=120, 60分钟 n_pts=60）
- **后果**: 日线中落在 60 分钟窗口之前的 entry marker 被 `_find_date_index` 返回 None → 静默丢弃
- **评级**: 概率 MEDIUM x 影响 MEDIUM

### 4.4 级联窗口内无匹配方向 pair (Dimension 4C)
- **位置**: `bs_marker.py` lines 137-144
- **场景**: 高级别 TF 的 entry 方向在低级别 TF 的 Schmitt 信号中找不到对应方向
- **后果**: 单个 marker 静默丢弃，语义上正确（低 TF 不同意高 TF 的方向），但用户无提示
- **评级**: 概率 LOW-MEDIUM x 影响 LOW-MEDIUM

## 5. 修复状态

| 项目 | 状态 | 说明 |
|------|------|------|
| 时区兼容修复 | [x] 完成 | commit 806140e, `tz_localize(None)` 归一化 |
| 回归测试 | [x] 完成 | 2 个新 tz 测试用例, 41/41 pass |
| 数据窗口对齐优化 | [ ] 待后续 | 考虑不同 TF 使用不同 n_pts 以均衡时间覆盖 |
| Schmitt 未启用时的用户提示 | [ ] 待后续 | 级联 TF 无 Schmitt 时警告用户 |
| `_find_date_index` 二分查找优化 | [ ] 可选 | 当前 O(n) 线性扫描对 300 bar 可接受 |
