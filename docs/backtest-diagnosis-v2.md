# Filter Research 回测功能综合诊断报告 v2

> 分支 feature/backtest-analysis | 基于 T1-T4 分析 | 2026-07-01

---

## 1. 播放控件失效诊断

### 1.1 问题现象

切换到回测模式后，播放/前进/后退按钮不显示。

### 1.2 调用链路

```
切换到回测模式 (sidebar radio)
  → _render_backtest_mode_switch (L1202-1258)
    → for cfg in configs: _sync_to_display(...)  # 同步数据到 parquet
    → _get_min_tf_and_count(configs, ticker_code)  # L1245, 计算最小周期和 bar 数
    → AppState.set("_min_tf_bar_count", bar_count)  # L1247
  → st.rerun()  # L1258
  → main() 重新执行
    → _render_backtest_controls()  # L1684
      → L1302: if total_bars == 0 → st.caption("回测数据未就绪") → return  # ⚠️ 阻断点
```

### 1.3 根因

**`_min_tf_bar_count == 0` 导致控件渲染提前 return。** 按钮、进度条、速度选择器全部被跳过，用户只看到一行灰色 caption "回测数据未就绪"。

`_get_min_tf_and_count` (L1162-1199) 在以下三种情况下返回 `bar_count=0`：

| 场景 | 触发条件 | 返回值 |
|------|----------|--------|
| 1 | `configs` 为空列表 | `("", 0)` |
| 2 | 所有 config 的 `tf` 值都不在 `ALL_TFS` 列表中（`ALL_TFS.index(tf)` 全部抛出 `ValueError`） | `("", 0)` |
| 3 | parquet 文件不存在、无法读取或读取异常 | `(min_tf, 0)` |

**最可能的实际触发路径：** `data/display/{min_tf}.parquet` 不存在。原因可能是：
- 首次切换到回测模式时，`_sync_to_display` 返回 `(False, <5)`（数据不足 5 行），不写 parquet
- `_sync_to_display` 内部 `query_kline` 失败（网络异常、SQLite 无数据），异常被外层 `except Exception` 吞掉
- 用户从未在浏览模式下加载过数据

### 1.4 次要发现

`_get_min_tf_and_count` 接受 `ticker_code` 参数但**从不使用**（L1162）。它仅从 `configs` 推断最小周期，然后读 parquet。如果 parquet 文件是另一个 ticker 的旧缓存，`bar_count` 会虚高但数据不对，形成**静默错误**（控件正常渲染但图表数据可能不匹配当前 ticker）。

### 1.5 结论

**按钮不显示不是隐藏 CSS 或渲染顺序问题，而是数据守卫逻辑阻止了渲染。** 代码逻辑本身无 bug，但缺乏足够的诊断信息让用户理解原因。T2 分析的 `st.stop()` / 提前 return / 时序竞争 均被排除。

---

## 2. 回测/浏览显示差异诊断

T3 识别出 8 个差异点，逐一评估必要性与否：

| # | 差异 | 必要/不必要 | 评估 |
|---|------|------------|------|
| 1 | **数据加载路径** — 浏览直接返回 parquet 全量，回测进入 `_apply_backtest_window` 切片 | **必要** | 回测需要按 bar_index 截断数据模拟"到此为止"的可见性 |
| 2 | **数据量** — 浏览返回 parquet 全量，回测返回 n_pts 窗口切片（可能加 1 个合成 bar） | **必要** | 性能（避免超大数据量）和防前视偏差的双重需求 |
| 3 | **预测对过滤** — 回测过滤 `pair_end > bar_index` 的未来信号 | **必要** | 这是回测核心语义：不能看到未来 |
| 4 | **回测 overlay 标注** — 金线 + 灰色遮罩 + "回测模式"标签 | **必要** | 回测模式需要视觉区分 |
| 5 | **高周期合成 bar** — `tf != min_tf` 时从低周期合成最后一个 bar | **必要** | 高周期数据可能存在未完成 bar，合成填补缺失 |
| 6 | **数据不足提示** — 消息文案不同 | **必要（边界情况）** | "回测窗口数据为空"比通用"数据不足"更精确 |
| 7 | **day_offset 控件隐藏** — 回测模式下 `_render_time_nav` 强制 `day_offset=0` 并隐藏控件 | **必要** | 回测用 bar_index 导航，day_offset 无意义 |
| 8 | **灰色遮罩不可见** — `_add_backtest_overlay` 中 `x0 == x1` 导致遮罩宽度为 0 | **不必要的 BUG** | 见第 3 节详细分析 |

### 2.1 关于 day_offset 的补充说明

`_render_time_nav` (L1410-1412) 在回测模式下强制 `day_offset=0`。这意味着回测始终从数据源的最新可用日期开始加载。如果用户想回测 2023 年的历史区间，当前代码不支持——这更像是功能缺失而非 bug，但值得标注为未来增强项。

### 2.2 结论

8 个差异中 7 个是设计意图，1 个（灰色遮罩不可见）是 bug。浏览和回测显示的核心差异来自 `_apply_backtest_window` 的窗口切片逻辑，这在回测场景中是正确的。

---

## 3. 金线问题诊断

### 3.1 问题现象

金线位置正确，但灰色遮罩不可见（宽度为 0）。用户期望看到金线 + 灰色遮罩覆盖未来数据。

### 3.2 根因分析

**双根因**：

#### 根因 A：遮罩宽度为零

调用链：
```python
# _render_chart (L741-742)
local_pos = len(t) - 1               # 窗口最后位置 = 全局 len(t)-1
_add_backtest_overlay(fig, local_pos, len(t), dates, cfg.get("tf", ""))

# _add_backtest_overlay (L112-123)
fig.add_shape(type="rect",
    x0=bar_index,      # = len(t) - 1
    x1=total_bars - 1, # = len(t) - 1
    ...)                # → x0 == x1 → 宽度为 0 → 不可见！
```

因为 `bar_index = local_pos = len(t) - 1` 且 `total_bars = len(t)`，两者相等，矩形宽度恒为 0。

#### 根因 B：当 `bar_index < n_pts` 时金线位置错误

`_apply_backtest_window` (L256-262) 中有一个窗口扩展逻辑：

```python
if tf == min_tf:
    end_idx = cutoff_idx               # cutoff_idx 对应 bar_index 的日期
    if bar_index < n_pts:               # ⚠️ 数据不足 n_pts 时：
        start_idx = 0
        end_idx = max(end_idx, min(n_pts - 1, len(df) - 1))  # 窗口向未来扩展！
    else:
        start_idx = max(0, end_idx - n_pts + 1)
```

**Bug 场景**：当 `bar_index < n_pts`（回测起始阶段）且 `tf == min_tf` 时：
- `cutoff_idx` = 10（bar_index=10 对应的实际数据位置）
- `end_idx` = `max(10, min(119, len(df)-1))` = **119**（窗口扩展到 n_pts-1）
- `start_idx` = 0
- `len(t)` = 120
- `local_pos = len(t) - 1` = **119**（金线画在窗口末尾位置 119）
- 但真正的回测当前位置是 **`cutoff_idx - start_idx = 10`**（窗口内偏移为 10）

**金线被画在了错误位置**（窗口末尾 119 而非截止位置 10），偏后了 109 个 bar。

当 `bar_index >= n_pts` 或 `tf != min_tf` 时，`end_idx = cutoff_idx`，窗口末尾确实对应 bar_index，此时金线位置正确。

### 3.3 修复后预期效果

修复后：
- 金线标记在 `cutoff_idx - start_idx`（真正的回测截止位置）
- 灰色遮罩覆盖 `x0=cutoff_local_pos` 到 `x1=len(t)-1`（截止位置右侧的扩展区间）
- 当 `bar_index >= n_pts` 时：遮罩宽度仍为 0（因为窗口末端即截止位置，无未来数据），行为不变
- 当 `bar_index < n_pts` 时：遮罩覆盖从截止位置到窗口末尾的扩展区间，**可见**

### 3.4 金线与图表坐标一致性

T4 已确认：所有 trace（K 线、滤波器、施密特、PnL）的 x 坐标都使用 `t = np.arange(len(n_pts_slice))` 局部索引。金线 `x=local_pos` 与图表 x 轴类型一致，无 datetime/int 混用问题。`_date_markers` 仅覆盖 tick 标签文本，不影响坐标系统。

### 3.5 结论

**金线位置在大部分情况下正确，但在 `bar_index < n_pts` 时存在偏移 bug**。遮罩宽度为零是金线位置使用 `len(t)-1` 的直接后果，修复根因 B 后遮罩自然可见。

---

## 4. TypeError 状态

### 4.1 已确认安全的部分

| 位置 | 代码 | 安全性 |
|------|------|--------|
| L253 | `np.searchsorted(df.index, cutoff, side="right")` | **安全** — `df.index` 是 `pd.DatetimeIndex`，`cutoff` 经 L250 显式 `pd.Timestamp(cutoff)` 转换，返回值经 `int()` 包裹。当前分支仅此一处 searchsorted |
| L254,260,262,264 | `max(0, min(...))` 系列 | **安全** — 全部操作 Python `int` |
| L570,574 | `min/max(float(...), 100.0)` | **安全** — 显式 `float()` 转换 |

### 4.2 新发现：潜在 TypeError 来源

#### 风险 A（中等）：时区敏感的 DatetimeIndex 比较 (L289)

```python
synthetic_bars = lower_df[
    (lower_df.index > last_bar_end) & (lower_df.index <= cutoff)
]
```

`lower_df.index` 来自 `pd.read_parquet`（时区取决于写入时的格式），`cutoff` 来自 L250 的 `pd.Timestamp(cutoff)`。

**触发条件**：如果 parquet 中存储的是时区感知时间戳（如 `UTC`），而 `cutoff` 是时区无关时间戳，**比较操作会直接抛出**：

```
TypeError: Cannot compare tz-naive and tz-aware datetime-like objects
```

这是当前分支中**最可能**触发 TypeError 的未处理路径。触发条件是数据源中混合了时区信息（parquet 文件由其他程序写入时带 tz，而 `df.index` 从 SQLite 读取时不带 tz）。

#### 风险 B（低）：`bar_index < n_pts` 的 None 穿透 (L258)

```python
if bar_index < n_pts:
```

`bar_index` 在 `_apply_backtest_window` 中没有内部守卫。虽然在调用者 `_load_chart_data` (L213) 中已检查 `bar_index is not None`，但缺乏防御性编程。如果未来重构引入调用路径，可能导致 `TypeError: '<' not supported between instances of 'NoneType' and 'int'`。

### 4.3 用户报告的 TypeError 可能原因

1. **用户运行的不是最新代码** — 如果用户本地分支未更新 L253 的 `int()` 修复，旧的 `np.searchsorted` 返回到 numpy 整数导致的后续比较操作可能触发 TypeError
2. **触发风险 A** — 时区不匹配的 DatetimeIndex 比较（L289）
3. **其他未在本文档分析的比较** — 如 `st.slider` 类型断言、`pd.to_datetime` 异常传入非预期类型

### 4.4 结论

当前分支代码中 `np.searchsorted` 调用（唯一一处）是类型安全的。如果用户仍遇到 TypeError，优先排查是否运行了旧代码，其次排查 L289 时区不匹配问题。

---

## 5. 修复方案（按优先级）

### P0 — 金线位置错误 + 遮罩不可见

**问题**：回测初期（`bar_index < n_pts`）金线画在窗口末尾而非真实的截止位置，遮罩不可见。

**根因**：`_render_chart` L741 使用 `local_pos = len(t) - 1` 作为金线位置，但窗口扩展后 `len(t)-1` 可能远超实际的截止位置。

**修复方案**：

#### Step 1: `_apply_backtest_window` 返回截止位置偏移

修改 L226 函数签名和 L306 返回值，增加 `cutoff_local_pos`：

```python
# L226: 修改函数文档 + 返回值
def _apply_backtest_window(df, ohlc, ticker_code, bar_index, n_pts, tf) -> tuple:
    """返回 (t, noisy, ohlc, ticker_full, dates, err, cutoff_local_pos)。
    cutoff_local_pos 是回测截止 bar 在窗口 t 数组中的索引偏移。"""
    ...
    # L253-267 之后，L302 之前，新增：
    cutoff_local_pos = cutoff_idx - start_idx
    
    # L306: 修改 return
    return t, noisy, ohlc, ticker_code, n_pts_slice.index, None, cutoff_local_pos
```

同时修改 L275 的空切片守卫返回值：

```python
# L274
return t, noisy, ohlc, ticker_code, pd.DatetimeIndex([]), "回测窗口数据为空", 0
```

#### Step 2: 所有调用者解包新返回值

- `_load_chart_data` (L216) 解包新增的 `cutoff_local_pos` 并返回
- `_render_chart_fragment` (L589) 传递到 `_render_chart`
- `_render_chart` (L741-742) 使用 `cutoff_local_pos` 代替 `len(t) - 1`：

```python
# L741-742: 替换
if bar_index is not None:
    _add_backtest_overlay(fig, cutoff_local_pos, len(t), dates, cfg.get("tf", ""))
```

**验证方法**：
1. 设置 `n_pts=120`，切换到回测模式
2. 将 slider 拖到 bar_index < 120 的位置
3. 确认金线在正确的 bar 上（不是窗口最右端）
4. 确认灰色遮罩可见且覆盖金线右侧区域
5. 将 slider 拖到 bar_index >= 120 的位置，确认金线仍在窗口末尾（行为不变）

---

### P1 — 控件不显示的诊断信息增强

**问题**：当 `_min_tf_bar_count == 0` 时，用户只看到 "回测数据未就绪"，无法判断原因。

**根因**：`_get_min_tf_and_count` 返回 `("", 0)` 或 `(min_tf, 0)` 时缺乏诊断上下文。

**修复方案**：

在 `_render_backtest_controls` 的 L1302-1304 守卫处增强信息输出：

```python
# L1302-1304: 替换
if total_bars == 0:
    min_tf = AppState.get("_min_tf", "")
    if not min_tf:
        st.caption("回测数据未就绪（未能识别有效时间周期配置，请检查视图设置）")
    else:
        display_path = Path(__file__).parent.parent / "data" / "display" / f"{min_tf}.parquet"
        if not display_path.exists():
            st.caption(f"回测数据未就绪（数据文件缺失: {min_tf}.parquet，请先在浏览模式加载数据）")
        else:
            st.caption(f"回测数据未就绪（数据文件为空: {min_tf}.parquet）")
    return
```

需要在文件顶部确保 `from pathlib import Path` 已引入。

**验证方法**：
1. 清空 `data/display/` 目录
2. 切换到回测模式
3. 确认看到包含具体原因（如"数据文件缺失"）的提示信息，而非泛化的"回测数据未就绪"

---

### P2 — 时区安全的 DatetimeIndex 比较

**问题**：L289 的 `(lower_df.index > last_bar_end) & (lower_df.index <= cutoff)` 可能因时区不匹配抛出 TypeError。

**根因**：parquet 文件的时区语义取决于写入时的 `pd.Timestamp` 设置，与 `pd.Timestamp(cutoff)`（naive）可能不一致。

**修复方案**：

在 L287-289 的比较前增加时区归一化：

```python
# L287-290: 替换
if len(n_pts_slice) > 0:
    last_bar_end = n_pts_slice.index[-1]
    # 时区归一化：统一转为 naive Timestamp
    lower_idx = lower_df.index.tz_localize(None) if hasattr(lower_df.index, 'tz') and lower_df.index.tz is not None else lower_df.index
    last_bar = last_bar_end.tz_localize(None) if hasattr(last_bar_end, 'tz') and last_bar_end.tz is not None else last_bar_end
    cutoff_naive = cutoff.tz_localize(None) if hasattr(cutoff, 'tz') and cutoff.tz is not None else cutoff
    synthetic_bars = lower_df[
        (lower_idx > last_bar) & (lower_idx <= cutoff_naive)
    ]
```

或者使用更简洁的 `pd.Timestamp` 构造方式（pandas 2.0+）：

```python
# 更简洁的替代方案
last_bar_end = pd.Timestamp(n_pts_slice.index[-1]).tz_localize(None)
cutoff_naive = pd.Timestamp(cutoff).tz_localize(None)
lower_idx = pd.DatetimeIndex([pd.Timestamp(d).tz_localize(None) for d in lower_df.index])

synthetic_bars = lower_df[
    (lower_idx > last_bar_end) & (lower_idx <= cutoff_naive)
]
```

**验证方法**：
1. 用 `pandas.to_datetime(..., utc=True)` 构造时区感知数据写入测试 parquet
2. 确认 `_apply_backtest_window` 中高周期合成不抛出 TypeError
3. 同时在 `_apply_backtest_window` 的 L253 `np.searchsorted` 处同样做时区归一化

---

### P3 — 防御性增强

**问题 A**：L258 `bar_index < n_pts` 缺乏内部 None 守卫

**修复**：

```python
# L245 之后添加
bar_index_int = int(bar_index) if bar_index is not None else 0
```

**问题 B**：`_get_min_tf_and_count` 接受但忽略 `ticker_code` 参数

**修复**：在函数中用 `ticker_code` 限定 parquet 路径，或在函数签名上移除该参数并更新所有调用者。不建议在当前版本做架构改动，仅添加注释标注该已知缺陷。

**验证方法**：
1. 为问题 A：通过 L245 的类型保护单元测试
2. 为问题 B：代码审查确认注释已添加

---

### 修复实施优先级汇总

| 优先级 | 修复项 | 预期工作量 | 影响范围 |
|--------|--------|-----------|---------|
| **P0** | 金线位置 + 遮罩可见 | ~20 行改动，3 个调用点更新 | `_apply_backtest_window`, `_load_chart_data`, `_render_chart_fragment`, `_render_chart` |
| **P1** | 诊断信息增强 | ~10 行新增 | `_render_backtest_controls` |
| **P2** | 时区安全 | ~5 行新增 | `_apply_backtest_window` L289 |
| **P3** | 防御性增强 | ~3 行新增 | `_apply_backtest_window` L245, `_get_min_tf_and_count` 注释 |

---

*报告生成时间: 2026-07-01 | 基于 T1-T4 分析 | 只读分析，未修改代码*
