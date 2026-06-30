# Filter Research 回测功能深度分析报告

> 基于分支 `feature/backtest-analysis` (94de92c) | 2026-06-30
>
> 本报告汇总 T1（数据源差异）、T2（回测分支深度分析）、T3（管道分支扫描审计）三项分析结果。

---

## 1. TypeError 根因分析

### 1.1 前 7 次修复为什么无效

T2 的核心发现：**`np.searchsorted(DatetimeIndex, Timestamp)` 在类型上完全正确，不会崩溃。**

逐路径证明：

| 路径 | `min_tf_dates` 类型 | `cutoff` 类型 | `np.searchsorted` 兼容性 |
|------|---------------------|---------------|--------------------------|
| 2a (不同周期, L255-264) | `pd.DatetimeIndex` (从 parquet 读取后构造) | `pd.Timestamp` (从 DatetimeIndex 下标取值 + `pd.Timestamp()` 包装) | 兼容 |
| 2b (相同周期, L265-267) | `pd.DatetimeIndex` (来自 `df.index`，由 `set_index("Date").sort_index()` 保证) | `pd.Timestamp` (同上) | 兼容 |
| 2c (空字符串, L254 跳过) | `pd.DatetimeIndex([])` (空，length=0) | `pd.Timestamp` (走 `df.index[-1]` 分支) | 兼容 |

**所有 6 种子路径组合中，`df.index` 和 `cutoff` 的类型始终正确。** 前 7 次修复都在强化类型保护（添加 `pd.Timestamp()` 包装、增加类型检查），但这些保护治疗的是"不存在的病"——类型从来就不是问题。

### 1.2 真正的崩溃机制

T2 指出，真正可能崩溃的位置是 **_load_chart_data L312**：

```python
last_bar_end = n_pts_slice.index[-1]
```

当 `n_pts_slice` 为**空 DataFrame** 时，`index[-1]` 抛出 `IndexError: index -1 is out of bounds for axis 0 with size 0`。

**空 DataFrame 的形成条件：**

1. `df` 在 `_load_chart_data` L245-249 处读取 parquet 时，parquet 文件为空或仅含表头
2. `cutoff_idx = max(0, np.searchsorted(empty_index, cutoff) - 1) = max(0, 0 - 1) = 0`
3. `n_pts_slice = df.iloc[0:1]` → 在空 df 上切片，得到空 DataFrame（不报错）
4. L312 `n_pts_slice.index[-1]` → **IndexError**

**parquet 为空的可能原因（结合 T1 发现）：**

- T1 揭示了 Path A（浏览）和 Path B（回测）对 `_sync_to_display` 的参数不同：Path A 传 `day_offset`（用户偏移值），Path B 传 `0`。在同一个 Streamlit render 周期内，两个路径会**先后覆盖同一个 parquet 文件**。
- 如果执行顺序异常（例如浏览模式先写入偏移后的空窗口数据，然后回测路径在 parquet 被重新写入之前就读取），parquet 内容可能为空或意外截断。

### 1.3 `_bt_data_cache` 结构不稳定（T3 发现的关键风险）

T3 在 `_render_backtest_status` L1298-1304 发现：

```python
cache = AppState.get("_bt_data_cache", {})
if min_tf and min_tf in cache and total_bars > 0:
    df = cache[min_tf]          # 假设 df 是带 DatetimeIndex 的 DataFrame
    if bar_index < len(df):
        idx = df.index[bar_index]  # 假设 index 是 DatetimeIndex
```

`_bt_data_cache` 在 `_render_backtest_mode_switch` L1273 被写入：

```python
cache[tf] = df    # df 来自 pd.read_parquet，未显式 set_index
AppState.set("_bt_data_cache", cache)
```

**问题：** 缓存 DataFrame 的 `index` 类型取决于 `pd.read_parquet` 的默认行为——如果 parquet 文件中 "Date" 列不是索引（因为 `_sync_to_display` 写入时未将其设为索引），则 `df.index` 是默认的 `RangeIndex`（int 0, 1, 2...），而非 `DatetimeIndex`。

此时 `df.index[bar_index]` 返回 `int`（而非 `Timestamp`），`hasattr(idx, "date")` 为 `False`，回退到 `str(idx)` 显示数字而非日期——这是**静默错误**，不抛异常但显示错误信息。

更危险的是：如果 `_bt_data_cache` 中某个周期的 DataFrame 被外部修改（如在 `_sync_to_display` 的写入竞赛中被覆盖后重新读取了不同结构的数据），则 `_load_chart_data` L266 的 `min_tf_dates = df.index` 可能拿到非 DatetimeIndex 的索引。不过 T2 已证明 `df` 在 `_load_chart_data` 内部总是经过 `set_index("Date").sort_index()` 处理，因此这条路径受保护。

### 1.4 综合根因假设（按可能性排序）

| 优先级 | 根因假设 | 证据 | 崩溃表现 |
|--------|----------|------|----------|
| **P0** | `n_pts_slice` 为空 DataFrame，L312 `index[-1]` 触发 IndexError | T2 第 5 节：空 df + `iloc` 切片 = 空 DataFrame + `index[-1]` 崩溃 | IndexError / 下游传播为 TypeError |
| **P1** | `_bt_data_cache` 存储的 DataFrame 缺少 DatetimeIndex，导致 `_render_backtest_status` 读取错误日期 → 用户基于错误日期调整 bar_index → 越界 | T3 第 12 节：缓存结构假设无强制校验 | 静默错误 → bar_index 越界 → IndexError |
| **P2** | `_sync_to_display` 写入竞赛：浏览模式用 day_offset 覆盖 parquet → 回测模式读到截断数据 → df 行数不足 → 切片为空 | T1 第 1-3 节：同文件两次不同参数的写入 | 空 DataFrame → 同 P0 |
| **P3** | `_cb_mode=True` 但 `_bar_index=None` 的静默退化：回测流程走浏览路径，数据不一致 | T3 第 15a 节：L1700 初始化顺序风险 | 数据量/格式不匹配 → 下游异常 |

**最可能的根因是 P0**：空 DataFrame 场景下 L312 的 `index[-1]` 崩溃。前 7 次修复之所以无效，是因为它们一直试图在 `np.searchsorted` 层面加固类型安全，而真正的漏洞在**数据完整性**层面——没有人检查 `n_pts_slice` 是否为空就直接访问 `index[-1]`。

---

## 2. 数据源差异对比

T1 对 Path A（浏览模式）和 Path B（回测模式）的全链路分析结论如下：

### 2.1 是否使用相同的 parquet 文件？

**物理路径相同**（`data/display/{tf}.parquet`），但**内容不同**。

原因：`_sync_to_display` 被两个路径以不同参数调用：

| 调用点 | day_offset | 写入内容 |
|--------|-----------|----------|
| Path A (浏览, L219) | 用户侧边栏设定值（0~365+） | 从数据库 MAX(ts) 往前推 N 天的窗口数据 |
| Path B (回测, L241) | 硬编码 `0` | 以 MAX(ts) 为截止的最新全量数据 |

此外，`_render_backtest_mode_switch` L1257 在进入回测模式时会对所有周期重新调用 `_sync_to_display(code, tf, 0, n_pts)`，确保回测路径使用的是全量数据。

### 2.2 是否经过相同的 `_sync_to_display`？

**是**，但参数不同（见上表）。

`_sync_to_display` 内部逻辑（data_loader.py L139-148）：
1. 调用 `query_kline(code, tf, n_pts, day_offset)` 查 SQLite
2. `query_kline` (db.py L97-103)：`day_offset > 0` 时从 MAX(ts) - N days 截断；`day_offset == 0` 时取到 MAX(ts)
3. 写入 `data/display/{tf}.parquet`

### 2.3 返回数据差异汇总

| 对比维度 | Path A (浏览) | Path B (回测) | 一致性 |
|----------|---------------|---------------|--------|
| day_offset | 用户设定值 | 硬编码 0 | **不一致** |
| _sync_to_display 参数 | (code, tf, day_offset, n_pts) | (code, tf, 0, n_pts) | **不一致** |
| Parquet 文件路径 | `data/display/{tf}.parquet` | 相同路径 | 一致 |
| Parquet 内容 | 偏移窗口数据 | 全量最新数据 | **不一致** |
| 读取方式 | `pd.read_parquet` + `set_index("Date").sort_index()` | 相同 | 一致 |
| 返回数据长度 | `len(df)`（全部行） | min(n_pts, len(df))（窗口切片） | **不一致** |
| 高周期合成 | 无 | L301-327 合成最后一根未完成 bar | **不一致**（回测有，浏览无） |
| dates 类型 | `pd.DatetimeIndex` | `pd.DatetimeIndex` | 一致 |
| 回退分支 | 走 `_cached_fetch_stock` (yfinance) | 直接返回错误 | **不一致** |
| t/noisy/ohlc 结构 | 相同结构，长度不同 | 相同结构，长度不同 | 结构一致 |

**关键结论：** 两路径在 parquet 物理路径和读取方式上一致，但**数据内容、数据长度、高周期处理、回退策略**均不同。它们不是简单的"参数差异"而是**两套不同的数据管道**。

---

## 3. 统一方案设计

### 3.1 简化的可行性

T3 扫描了 17 个分支点，发现：
- 大部分分支是**UI 层分支**（回测标注、状态显示、控件渲染），与数据管道无关
- 2 个函数是**死代码**（`_truncate_arrays` L74、`_global_to_local_bar_index` L83），无任何调用者
- 核心数据流差异集中在 `_load_chart_data` 的 `bar_index is None` 分支（L218）

这支持**大幅简化**：核心数据加载可以统一。

### 3.2 统一方案

**目标：** 让浏览模式和回测模式使用同一套数据加载逻辑，仅通过参数控制行为差异。

```
方案：统一 _load_chart_data，消除 Path A / Path B 分裂
```

#### Step 1: 消除 `day_offset` 硬编码差异

当前：
```python
# Path A (L638)
_load_chart_data(market, ticker_code, tf, day_offset, n_pts, bar_index=None)
# Path B (L636)
_load_chart_data(market, ticker_code, tf, 0,            n_pts, bar_index=0)
```

修改为：**浏览模式也使用 `day_offset=0` 加载全量数据**，在 Python 内存中做窗口偏移，而非在 SQL 查询层截断。

好处：
- 两个路径的 parquet 内容永远一致，消除写入竞赛
- `_sync_to_display` 只需调用一次，不再被两个路径先后覆盖
- 减少 SQLite 查询的复杂性（`query_kline` 不再需要 day_offset 逻辑）

#### Step 2: 统一数据加载入口

将 `_load_chart_data` 的内部逻辑重构为：

```python
def _load_chart_data(market, ticker_code, tf, day_offset, n_pts, bar_index=None):
    # 1. 统一数据源：始终从 display parquet 加载全量数据
    df = _load_display_parquet(ticker_code, tf, n_pts)  # day_offset 不再传入
    
    # 2. bar_index is None → 浏览模式：返回全量或内存截断
    # 3. bar_index is not None → 回测模式：窗口切片 + 高周期合成
```

`day_offset` 改为**纯内存操作**：在返回前对 DataFrame 做 `iloc[-n_pts:]` 截断。

#### Step 3: 统一回退策略

当前回测模式在 parquet 不存在时直接返回错误，而浏览模式会回退到 `_cached_fetch_stock`（yfinance API）。统一为：**parquet 不存在时，两个路径都回退到 `_cached_fetch_stock`，然后在内存中应用窗口逻辑。**

#### Step 4: 清理死代码

移除：
- `_truncate_arrays` (L74-L80) — 无调用者
- `_global_to_local_bar_index` (L83-L91) — 无调用者
- `query_kline` 中的 `day_offset` SQL 逻辑（db.py L97-103），如果 Step 1 确认后不再需要

### 3.3 重构影响范围

| 模块 | 变更 | 风险 |
|------|------|------|
| `_load_chart_data` | 合并双路径为单一路径 + 参数控制 | 中 — 核心函数，需完整回归测试 |
| `_sync_to_display` | 移除 day_offset 参数，始终写入全量数据 | 低 — 简化逻辑 |
| `query_kline` (db.py) | 移除 day_offset SQL 过滤 | 低 — 减少 SQL 复杂度 |
| `_render_chart` | 调用方式不变，语义不变 | 低 |
| `_truncate_arrays` | 删除 | 无 — 死代码 |
| `_global_to_local_bar_index` | 删除 | 无 — 死代码 |

---

## 4. 其他类似问题

基于 T3 对 17 个分支点的扫描，以下问题需要关注：

### 4.1 `_bt_data_cache` 结构不稳定（中风险）

**位置：** `_render_backtest_mode_switch` L1273（写入）、`_render_backtest_status` L1309（读取）

**问题：** 缓存 DataFrame 的 index 类型无保证。`pd.read_parquet` 读出的 DataFrame 默认 index 是 `RangeIndex`（0, 1, 2...），不是 `DatetimeIndex`。而 `_render_backtest_status` L1312 假设 `df.index[bar_index]` 返回 `Timestamp`（调用 `.date()` 方法）。

**影响：** 如果缓存中的 DataFrame 未被 `set_index("Date")` 处理，`df.index[bar_index]` 返回 `int`，`hasattr(idx, "date")` 为 False，回退到 `str(idx)` → 回测状态栏显示数字而非日期。

**验证方法：** 在 `_render_backtest_mode_switch` L1273 处，对 `df` 做 `df = df.set_index("Date").sort_index()` 再存入缓存。

### 4.2 `_cb_mode=True` 但 `_bar_index` 未初始化的静默降级（中风险）

**位置：** `main()` L1700

```python
bar_index = AppState.get("_bar_index", None) if cb_mode else None
```

**问题：** 当 `_cb_mode=True` 但 `_bar_index` 尚未被设置时（如新 tab 首次进入回测模式，或 SessionState 在 `st.rerun()` 后丢失），`bar_index` 为 `None`。此时整个回测管道退化为浏览模式——数据不截断、无回测标注、无前视偏差防护——但用户界面上仍显示回测控件，形成"假回测"状态。

**影响：** 用户在不知情的情况下看到的是包含未来数据的浏览视图，所有回测 PnL 计算错误。

**修复：** 在 `main()` L1700 处添加守卫：

```python
if cb_mode:
    bar_index = AppState.get("_bar_index", 0)  # 默认 0 而非 None
else:
    bar_index = None
```

或在 `_render_chart` 的回测分支入口增加断言：

```python
if bar_index is not None:
    assert isinstance(bar_index, int), f"bar_index 类型异常: {type(bar_index)}"
```

### 4.3 死代码残留（低风险，但混淆调试）

| 函数 | 位置 | 状态 |
|------|------|------|
| `_truncate_arrays` | L74-L80 | 无任何调用者，可能被 `_load_chart_data` 的窗口逻辑替代后遗留 |
| `_global_to_local_bar_index` | L83-L91 | 无任何调用者，功能与 `_load_chart_data` 内 L280 的 searchsorted 重复 |

**建议：** 删除这两个函数。它们的存在让开发者误以为存在另一套 bar_index 映射机制，增加调试时的认知负担。

### 4.4 高周期合成导致的视觉标记偏移（低风险）

**位置：** `_render_chart` L770-L772

```python
if bar_index is not None:
    local_pos = len(t) - 1    # 假设回测窗口末尾 = bar_index 位置
    _add_backtest_overlay(fig, local_pos, len(t), dates, ...)
```

**问题：** 当高周期视图触发合成逻辑（`_load_chart_data` L302-327），`n_pts_slice` 被 `pd.concat` 追加 1 条合成 bar 后，`len(t)` 比预期多 1。此时 `local_pos = len(t) - 1` 指向合成 bar 而非实际 bar_index 对应位置，金色竖线偏移 1 个索引。

**影响：** 仅视觉显示偏移，不影响数据计算。在绝大多数 bar_index 位置不可见，仅在边界情况（如刚好在周期切换点）可察觉。

**修复：** 在 `_load_chart_data` 返回时增加 `local_pos` 字段，让调用侧准确知道 bar_index 在切片中的本地位置。

### 4.5 `_compute_prediction_pairs` 的类型守卫依赖（中风险）

**位置：** L404

```python
if bar_index is not None and pair_end > bar_index:
    continue
```

**问题：** 依赖 `bar_index is None` 守卫来防止 `None > int` 的 TypeError。当前调用链保证 `bar_index` 为 `int | None`，但若未来引入 `float` 或其他类型，`pair_end > bar_index` 的行为不可预测。

**当前状态：** 调用链（`main()` L1700 → `_render_chart_fragment` L613 → `_render_chart` L680 → `_compute_prediction_pairs` L404）保证类型一致性。**暂无实际风险，但缺少防御性断言。**

### 4.6 播放循环的 delay=0 极端情况（低风险）

**位置：** `_run_backtest_play` L1413

**问题：** 如果 `_play_speed_label` 不在 `speed_map` 中，`delay = speed_map.get(label, 1)` 的默认值 1 秒有效。但如果未来有人修改 speed_map 时不小心让某个 key 映射到 0，`time.sleep(0)` 会导致无限紧密循环，CPU 100%。

**当前状态：** speed_map 覆盖全部 6 个选项，无实际风险。但缺少最小值守卫（`delay = max(0.05, delay)`）。

### 4.7 `_add_backtest_overlay` 的 total_bars 类型防御缺失（低风险）

**位置：** L111

```python
if bar_index is None or total_bars == 0:
    return
```

**问题：** `total_bars` 未做类型检查。如果传入非 int（如 `float(0)`），`total_bars == 0` 仍然正确（`0.0 == 0` 为 True），但语义隐晦。当前调用侧 `_render_chart` L770 传入 `len(t)`（int），无实际风险。

---

## 5. 综合建议

### 5.1 立即修复（P0 — 阻断性）

1. **在 `_load_chart_data` L312 前添加空 DataFrame 守卫：**

   ```python
   n_pts_slice = df.iloc[start_idx:end_idx + 1]
   if len(n_pts_slice) == 0:
       return np.array([]), np.array([]), pd.DataFrame(), ticker_code, pd.DatetimeIndex([]), "数据为空"
   ```

   这是对 T2 发现的空 DataFrame → `index[-1]` 崩溃的最直接防御。

2. **在 `_render_backtest_mode_switch` L1273 确保缓存 DataFrame 具有正确的 DatetimeIndex：**

   ```python
   df = pd.read_parquet(display_path)
   df = df.set_index("Date").sort_index()  # 显式确保 DatetimeIndex
   cache[tf] = df
   ```

   消除 `_bt_data_cache` 结构不稳定的风险。

### 5.2 短期改进（P1 — 防御性）

3. **修复静默退化（§4.2）：** `main()` L1700 中 `_bar_index` 默认值从 `None` 改为 `0`（当 `_cb_mode=True` 时）。

4. **统一 parquet 写入（§3.2 Step 1）：** `_sync_to_display` 移除 `day_offset` 参数，始终写入全量数据。浏览模式的偏移改为内存操作。消除两个路径先后覆盖同一文件的竞争条件。

5. **删除死代码（§4.3）：** 移除 `_truncate_arrays` 和 `_global_to_local_bar_index`。

### 5.3 中期重构（P2 — 架构简化）

6. **统一数据管道（§3.2 Step 2-3）：** 合并 `_load_chart_data` 的双路径为单一路径，用 `bar_index` 参数控制窗口切片行为。消除 Path A / Path B 的代码分裂。

7. **添加防御性断言：**
   - `_compute_prediction_pairs` L404：`assert bar_index is None or isinstance(bar_index, int)`
   - `_add_backtest_overlay` L111：`assert isinstance(total_bars, int)`
   - `_run_backtest_play` L1413：`delay = max(0.05, delay)`

### 5.4 长期建议（P3 — 可测试性）

8. **为 `_load_chart_data` 添加单元测试**，覆盖以下边界场景：
   - parquet 文件为空 → 应返回空数组 + 错误信息，不抛异常
   - parquet 文件仅 1 行 → 窗口切片不应崩溃
   - `bar_index=0` 且 `n_pts` > 数据总量 → 应返回所有可用数据
   - 高周期合成在 low_tf parquet 不存在时 → 应优雅降级

9. **为 `_bt_data_cache` 添加 schema 校验：**
   ```python
   def _validate_cache_entry(df, tf):
       assert isinstance(df.index, pd.DatetimeIndex), f"缓存 {tf} 的 index 不是 DatetimeIndex"
       assert {"Open", "High", "Low", "Close"}.issubset(df.columns), f"缓存 {tf} 缺少 OHLC 列"
   ```
   在 `_render_backtest_mode_switch` L1273 写入后和 `_render_backtest_status` L1309 读取前调用。

### 5.5 优先级矩阵

| 建议 | 优先级 | 预期工作量 | 影响范围 |
|------|--------|-----------|----------|
| #1 空 DataFrame 守卫 | **P0** | 2 行代码 | 阻止 L312 崩溃 |
| #2 缓存 index 规范化 | **P0** | 2 行代码 | 消除静默错误 |
| #3 修复静默退化 | P1 | 1 行代码 | 阻止假回测状态 |
| #4 统一 parquet 写入 | P1 | ~10 行 | 消除写入竞赛 |
| #5 删除死代码 | P1 | 删除 ~20 行 | 减少认知负担 |
| #6 统一数据管道 | P2 | ~30 行 | 架构简化 |
| #7 防御性断言 | P2 | ~10 行 | 增强健壮性 |
| #8 单元测试 | P3 | ~100 行 | 防止回归 |
| #9 Schema 校验 | P3 | ~15 行 | 缓存安全 |

---

## 附录

### A. 关键代码引用

#### A.1 `_load_chart_data` 回测路径 (streamlit_app.py L240-L333)

```python
# L245-249: Parquet 读取（两个路径一致）
df = pd.read_parquet(display_path)
df["Date"] = pd.to_datetime(df["Date"])
df = df.set_index("Date").sort_index()

# L253-267: min_tf_dates 计算（3 条子路径，类型一致）
min_tf = AppState.get("_min_tf", "")
min_tf_dates = pd.DatetimeIndex([])
if min_tf and min_tf != tf:
    # Path 2a: 读取 min_tf parquet
    min_df = pd.read_parquet(min_path, columns=["Date"])
    raw_dates = pd.to_datetime(min_df["Date"])
    min_tf_dates = pd.DatetimeIndex(raw_dates)
elif min_tf == tf:
    # Path 2b: 复用 df.index
    min_tf_dates = df.index

# L270-277: cutoff 计算（所有路径下均为 pd.Timestamp）
bar_index_int = int(bar_index)
if len(min_tf_dates) <= bar_index_int:
    cutoff = df.index[-1]
else:
    cutoff = min_tf_dates[bar_index_int]
cutoff = pd.Timestamp(cutoff)

# L280: 关键行 — 类型正确，不会崩溃
cutoff_idx = int(np.searchsorted(df.index, cutoff, side="right") - 1)

# L283-299: 窗口切片
# L301-327: 高周期合成 (仅回测路径)
# L312: **崩溃点** — n_pts_slice 为空时 index[-1] 抛 IndexError
last_bar_end = n_pts_slice.index[-1]
```

#### A.2 入口差异 (streamlit_app.py L634-L638)

```python
# Path A (浏览, L638):
_load_chart_data(market, ticker_code, tf, day_offset, n_pts, bar_index=None)
# Path B (回测, L636):
_load_chart_data(market, ticker_code, tf, 0,            n_pts, bar_index=0)
```

#### A.3 `_bt_data_cache` 结构假设 (streamlit_app.py L1298-L1304)

```python
cache = AppState.get("_bt_data_cache", {})
if min_tf and min_tf in cache and total_bars > 0:
    df = cache[min_tf]
    if bar_index < len(df):
        idx = df.index[bar_index]           # 假设 DatetimeIndex
        current_date = str(idx.date()) ...  # 假设有 .date() 方法
```

#### A.4 静默退化入口 (streamlit_app.py L1700)

```python
bar_index = AppState.get("_bar_index", None) if cb_mode else None
# 若 cb_mode=True 但 _bar_index 未初始化 → bar_index=None → 回测退化为浏览
```

### B. 分析方法说明

| 任务 | 方法 | 产出 |
|------|------|------|
| T1 数据源差异 | 逐行追踪 `_load_chart_data` 两条路径的完整数据流，对比 12 个维度 | 差异汇总表 |
| T2 回测分支 | 对 `_load_chart_data` L240-330 的每个类型转换做逐路径穷举 | 类型安全证明 + 真正崩溃点定位 |
| T3 全管道扫描 | 搜索 `bar_index`, `_cb_mode`, `_bt_data_cache`, `_min_tf` 的 17 个分支点 | 风险矩阵 + 死代码识别 |
