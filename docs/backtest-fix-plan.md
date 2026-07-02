# Filter Research 回测功能修复设计方案
> 基于 T1 深度分析 | 2026-07-02 | 经代码逐行验证

## 1. Q1: 时区问题

### 1.1 根因

yfinance `.download()` 返回值时区不一致：

| 周期 | Index 类型 | 时区 |
|------|-----------|------|
| `1m` / `5m` / `15m` / `1h` | `DatetimeIndex` | **tz-aware**（交易所本地时区，如 Asia/Shanghai） |
| `1d` / `1wk` / `1mo` / `3mo` | `DatetimeIndex` | **tz-naive**（裸日期） |

时区差异沿数据流一路传递：

```
yfinance → SQLite (isoformat 字符串，含/不含时区偏移)
         → pd.to_datetime() → tz-aware / tz-naive 混合 DatetimeIndex
         → Parquet → _load_backtest_window → 跨 tf 比较
```

### 1.2 风险点

| 位置 | 行号 | 表达式 | 风险 |
|------|------|--------|------|
| `_load_backtest_window` | L334 | `_binary_search_le(df.index, cutoff)` | ⚠️ 高危：两个不同 tf 的 DatetimeIndex 直接比较 |
| `_load_backtest_window` | L331 | `cutoff = min_tf_dates[bar_index_int]` | ⚠️ 高危：min_tf 可能是日内 tz-aware，当前 tf 可能是日线 tz-naive |
| `_load_backtest_window` | L375-376 | `lower_df.index > last_complete_date` / `lower_df.index <= cutoff` | ⚠️ 高危：合成路径中的跨周期比较 |

当 tz-aware 与 tz-naive 的 `pd.Timestamp` 比较时，pandas 会触发 `TypeError` 或隐式转为 UTC 后比较（版本相关），导致结果不可预期。

### 1.3 解决方案：源头统一（方案 A）

**修改文件**: `/Users/xfpan/claude/filter_research/filter_app/services/data_loader.py`

**修改位置**: `_fetch_stock` 函数，第 96 行 `yf.download(...)` 之后，第 120 行 `data = data[data["Close"].notna()]` 之前

**插入代码**（在第 98 行 `if data.empty:` 判断与第 100 行 MultiIndex 处理之间，或 98-99 行之后）：

```python
    # 第 96 行（现有）
    data = yf.download(full, period=period, interval=interval, progress=False)
    if data.empty:
        return None, None, None, full, f"无数据: {full}", None

    # ── 新增：统一时区，消除日内/日线时区差异 ──
    # yfinance 日内周期返回 tz-aware DatetimeIndex（交易所本地时区），
    # 日线以上返回 tz-naive。统一去掉时区，避免跨 tf 比较时 TypeError。
    if isinstance(data.index, pd.DatetimeIndex) and data.index.tz is not None:
        data.index = data.index.tz_localize(None)

    # 第 100 行（现有）
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.droplevel(1)
```

**改动量**: 4 行（含注释）

### 1.4 附带影响与处理

| 影响 | 处理方式 |
|------|---------|
| 已有 parquet 缓存中含 tz-aware 数据 | 修复后首次启动，因 DB 新写入数据为 tz-naive，parquet 自动被覆盖。或手动调用 `clear_display_cache()` |
| SQLite 中已有 tz-aware 字符串 | 新 `_fetch_stock` 写入的数据为 tz-naive，upsert 时自然覆盖 |
| 跨周期比较安全 | 所有 DatetimeIndex 统一为 tz-naive，`_binary_search_le` 和合成路径不再有类型冲突 |

### 1.5 验证清单

1. [ ] 日内周期（如 "5分钟"）parquet 的 `Date` 列不再包含 `+08:00` 后缀
2. [ ] 日线及以上周期 parquet 数据不变
3. [ ] `_load_backtest_window` L334 中两种 DatetimeIndex 可以安全比较
4. [ ] 合成路径 L375-376 不再报 `TypeError`
5. [ ] 浏览模式与回测模式切换正常
6. [ ] `_render_data_validation` 中的 `compare_with_db` 不会因时区不一致而误报差异

---

## 2. Q2: 窗口不满 bar 问题

### 2.1 现状

`_load_backtest_window` L267-397 中，bar_index 从 0 到 total-1 遍历。当前 `min_tf_dates` 在缓存阶段经过 `sort_index()` 升序排列，因此：

- `bar_index = 0` → `min_tf_dates[0]` = 最旧日期
- `bar_index = total-1` → `min_tf_dates[-1]` = 最新日期

窗口计算 `start_idx = max(0, end_idx - n_pts + 1)` 从 cutoff_date 向前取 n_pts 条。

### 2.2 根因

当 bar_index 接近 0（历史边界）时，cutoff_date 很旧，往前历史数据不足 n_pts 条。但 bar_index 接近 total-1（最新）时数据充足。

### 2.3 解决方案

无需修改算法。不满 bar 时已有 `logger.warning` 诊断（L344-348）：

```python
# L344-348（现有，无需修改）
if slice_length < n_pts:
    logger.warning(
        f"_load_backtest_window: {tf} 窗口不满 bar ({slice_length}/{n_pts}), "
        f"bar_index={bar_index}, df_total={len(df)}"
    )
```

函数尽力返回已有数据（有多少返回多少），行为符合设计预期。

---

## 3. Q3: 满 bar 播放语义

### 3.1 实际 bar_index 语义（代码验证结论）

> ⚠️ T1 分析中 bar_index 映射关系的结论与实际代码不符，以下为逐行验证后的正确结论。

**验证路径**:

1. `query_kline` (db.py L108): `ORDER BY ts DESC LIMIT n_pts` → 最新在前
2. `query_kline` (db.py L116): `df.iloc[::-1]` → 反转为最旧在前
3. `_sync_to_display` (data_loader.py L147): 写入 parquet，不改变顺序
4. 缓存加载 (streamlit_app.py L1370): `set_index("Date").sort_index()` → 升序，最旧在前
5. `_load_backtest_window` (streamlit_app.py L331): `cutoff = min_tf_dates[bar_index_int]`

**因此**:

| bar_index | min_tf_dates 索引 | cutoff 日期 | 当前标签 | 含义 |
|-----------|-------------------|-------------|---------|------|
| 0 | 0 | **最旧** | ⏮ 跳到开头 | 数据起点 |
| total-1 | last | **最新** | ⏭ 跳到最新 | 数据终点 |

**当前按钮标签与行为一致**。边界提示 "位于数据起始边界"（bar_index=0）和 "位于数据结束边界"（bar_index>=total-1）也正确。

### 3.2 决策：是否修改语义

存在两种设计方向：

| 方向 | bar_index=0 含义 | 播放方向 | 改动 |
|------|-----------------|---------|------|
| **A: 保持现状** | 最旧（数据起点） | 旧→新 | 无 |
| **B: 反转语义** | 最新（与浏览模式一致） | 新→旧 | 修改 `_load_backtest_window` L331 |

**推荐方向 A（保持现状）**：
- 当前实现与标签一致，无 bug
- 浏览模式 day_offset=0 显示最新数据，回测模式 bar_index=total-1 同样显示最新数据 —— 两种模式在逻辑上是对齐的（都需要主动跳到末尾才能看到最新数据）
- 0 改动，零风险

**如果选择方向 B**，需要修改以下位置：

| 文件 | 行号 | 修改内容 |
|------|------|---------|
| `streamlit_app.py` | L331 | `cutoff = min_tf_dates[bar_index_int]` → `cutoff = min_tf_dates[len(min_tf_dates) - 1 - bar_index_int]` |
| `streamlit_app.py` | L1413 | `idx = df.index[bar_index]` → `idx = df.index[len(df) - 1 - bar_index]` |
| `streamlit_app.py` | L1459-1522 | 按钮标签、help、边界提示全部反置 |

### 3.3 当前控件状态（方向 A，无需修改）

| 按钮 | 标签 | bar_index 变化 | disabled 条件 | 语义 |
|------|------|---------------|--------------|------|
| ⏮ | 跳到开头 | bar_index = 0 | 永不 disabled | 跳到最旧数据 |
| ◀ | 后退一个 bar | bar_index -= 1 | bar_index <= 0 | 向更旧方向 |
| ▶▶ | 前进一个 bar | bar_index += 1 | bar_index >= total-1 | 向更新方向 |
| ⏭ | 跳到最新 | bar_index = total-1 | 永不 disabled | 跳到最新数据 |
| ▶/⏸ | 播放/暂停 | bar_index 递增 | - | 从旧到新遍历 |

### 3.4 播放行为（方向 A）

- ▶ 播放：bar_index 从当前位置递增（向更新方向），到达 total-1 自动停止（L1538-1539）
- ⏸ 暂停：停止递增
- 拖拽进度条：自动暂停播放（L1514-1515）
- 速度控制：已有 6 档（0.25x ~ 10x），无需修改

---

## 4. 实施顺序

| 步骤 | 内容 | 文件 | 预估改动 | 验证 |
|------|------|------|---------|------|
| 1 | `_fetch_stock` 中插入 `tz_localize(None)` | `data_loader.py` L96-99 | 新增 4 行 | 手动测试日内+日线周期 |
| 2 | 清除旧 parquet 缓存 | 启动时调用 `clear_display_cache()` 或手动 `rm data/display/*.parquet` | 1 行或手动 | 确认 parquet 中 Date 无时区偏移 |
| 3 | 全量回归 | 无代码改动 | 0 行 | `pytest` 全量测试 |
| 4 | (可选) Q3 语义调整 | `streamlit_app.py` | 取决于方向选择 | 手动测试回测播放 |

### 4.1 步骤 1 详细修改

**文件**: `/Users/xfpan/claude/filter_research/filter_app/services/data_loader.py`

**当前代码** (L96-100):
```python
    data = yf.download(full, period=period, interval=interval, progress=False)
    if data.empty:
        return None, None, None, full, f"无数据: {full}", None

    if isinstance(data.columns, pd.MultiIndex):
```

**修改后**:
```python
    data = yf.download(full, period=period, interval=interval, progress=False)
    if data.empty:
        return None, None, None, full, f"无数据: {full}", None

    # 统一时区：yfinance 日内周期返回 tz-aware，日线返回 tz-naive
    # 去掉时区避免跨 tf 比较时 TypeError
    if isinstance(data.index, pd.DatetimeIndex) and data.index.tz is not None:
        data.index = data.index.tz_localize(None)

    if isinstance(data.columns, pd.MultiIndex):
```

### 4.2 步骤 2 缓存清理

```bash
# 手动清理 display 缓存
rm -f /Users/xfpan/claude/filter_research/data/display/*.parquet

# 重启应用后，回测模式会自动通过 _sync_to_display 重新生成
```

### 4.3 回归测试

```bash
cd /Users/xfpan/claude/filter_research
pytest
```

---

## 5. 修改函数完整清单

| # | 函数 | 文件:行号 | 修改类型 | 内容 |
|---|------|----------|---------|------|
| 1 | `_fetch_stock` | `data_loader.py` L96-99 | **新增** | 插入 `tz_localize(None)` 统一时区 |
| 2 | `_load_backtest_window` | `streamlit_app.py` L331 | **不改**（方向A） | 当前逻辑正确 |
| 3 | `_render_backtest_controls` | `streamlit_app.py` L1459-1522 | **不改**（方向A） | 标签与行为一致 |
| 4 | `_render_backtest_status` | `streamlit_app.py` L1398-1427 | **不改**（方向A） | 显示正确 |
| 5 | `_run_backtest_play` | `streamlit_app.py` L1525-1552 | **不改**（方向A） | 播放逻辑正确 |
