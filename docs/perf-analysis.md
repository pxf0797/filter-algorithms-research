# filter_research 性能分析报告

**分析日期**: 2026-07-23
**代码库**: `/Users/xfpan/claude/filter_research`
**分析方法**: 静态代码审查 (只读)

---

## 1. 热点路径 (Hot Paths)

### 1.1 回测主循环 (最热路径)

**文件**: `filter_app/services/backtest_core.py`, 行 158-244

`BacktestRunner.run()` 是整个系统的最热路径，在回测模式下对每个 bar 索引执行完整管道。每个 bar 执行流程：

```
bar_index → _sync_data() → _load_window_data() → _compute_pipeline_for_view()
→ _compute_masks_for_view() → _compute_bs_for_view()
```

对于 N 个 bar、V 个视图，每个 bar 都执行：
- **数据同步**: `_sync_all_cascading()` 写入 parquet 文件 (I/O)
- **数据加载**: 从 parquet 文件读取 (I/O)
- **管道计算**: 滤波器 + 施密特触发器 + 预测 + PnL (CPU)
- **跨周期对齐**: 高周期 PnL 按时间戳前向填充到低周期 (O(n*m) CPU)

**预估**: 对于 4 视图、1000 bar 的回测，预计总调用约 4000 次管道计算（不含 I/O）。

### 1.2 策略 PnL 计算

**文件**: `filter_app/services/filter_engine.py`, 行 673-876

`_compute_strategy_pnl()` 对每个交易对执行纯 Python `for` 循环遍历 bar，每个 bar 都做价格比对。在 4 个视图中，这个函数每次回测步骤被调用 4 次。

热点循环（行 822-845）：
```python
for i in range(entry_idx, exit_idx + 1):   # Python 级别逐 bar 遍历
    cur_p = filtered[i]
    unrealized = (cur_p - entry_price) / entry_price
    long_pnl[i] = long_capital * (1 + unrealized)
```

### 1.3 施密特触发器

**文件**: `filter_app/services/filter_engine.py`, 行 434-534

`_schmitt_trigger()` 包含 3 个顺序 Python `for` 循环：
1. 行 487-490: EWMA 波动率计算 (逐点循环)
2. 行 500-527: 施密特状态机 (逐点循环，含 if-else 分支)

对于 120-bar 窗口，每步约 240 次 Python 级迭代。每次回测步骤 4 个视图 = 960 次迭代。

### 1.4 卡尔曼滤波

**文件**: `filter_app/services/filter_engine.py`, 行 157-198

`apply_kalman()` 使用纯 Python `for i in range(n)` 循环实现逐点卡尔曼滤波更新（行 184-197），无法利用向量化加速。numpy 的矩阵运算 (`F @ x`, `P @ F @ P.T`) 在循环内部进行，每次迭代都创建新的 numpy 数组对象。

---

## 2. 数据处理效率 (Pandas/NumPy)

### 2.1 Pandas iterrows() 反模式 (高影响)

**文件**: `filter_app/db.py`

三处使用了 `df.iterrows()`，每次调用都创建 Python 逐行迭代器：

| 位置 | 行号 | 调用场景 | 影响 |
|------|------|----------|------|
| `upsert_kline` | 86 | 每次 yfinance 数据写入 DB | 每次 8 个 TF 并行获取后调用 |
| `compare_with_db` | 559 | DB vs 数据源对比 | 按需调用 |
| `force_update_kline` | 626 | 强制更新 | 按需调用 |

**问题**: `upsert_kline` 是热路径，每次获取数据都被调用（启动时 8 次）。对于 500+ 行数据，`iterrows()` 比 `df.to_dict('records')` 慢 3-10 倍。

**建议**: 使用 `zip(df['Open'], df['High'], df['Low'], df['Close'], df['Volume'])` 或 `df.itertuples()`。

### 2.2 apply_ema 不必要的 DataFrame 转换

**文件**: `filter_app/services/filter_engine.py`, 行 49-66

```python
def apply_ema(signal, t, span):
    return DataFrame({"v": signal}).ewm(span=span, adjust=False).mean().values.flatten()
```

每次调用创建临时 DataFrame（分配内存、创建索引），实际等价于一行 numpy。对于 120-bar 窗口，额外开销可忽略；但在回测热循环中每次步骤调用 4 次（4 视图），累计影响明显。

**建议**: 直接使用 `pd.Series(signal).ewm(span=span, adjust=False).mean().to_numpy()` 避免 DataFrame 开销。

### 2.3 db.py 中无深拷贝风险 (低影响)

经检查 `filter_app/services/` 目录下的代码没有使用 `deepcopy` 或 `copy.deepcopy`。这是一项正面发现，说明开发团队已经意识到避免不必要拷贝。

### 2.4 NumPy 使用质量 (中等)

大部分滤波函数使用向量化 numpy 操作（`np.convolve`, `np.gradient`, `np.polyfit`），这部分是良好的。但在以下位置存在逐个元素操作：

- `filter_engine.py:862-866`: 前向填充 long_pnl (Python for-loop)
- `filter_engine.py:870-874`: 前向填充 short_pnl (Python for-loop)
- `filter_engine.py:947-953`: `_align_pnl_to_current_tf` 中逐 bar 的 mask 和 argmax 查找（详见表 7）

---

## 3. I/O 瓶颈

### 3.1 回测逐 bar 重复 Parquet 写入 (关键瓶颈)

**文件**: `filter_app/services/backtest_core.py`, 行 431-455 和 `filter_app/services/data_loader.py`, 行 737-818

回测模式的主循环结构：
```
for bar_index in range(start_bar, end_bar):
    cutoff_date = ...
    self._sync_data(cutoff_date)      # ← 每次 bar 都同步所有 TF 到 parquet
    self._load_window_data(tf, n_pts)  # ← 每次 bar 都从 parquet 读取
    self._compute_pipeline_for_view()  # ← 计算
```

`_sync_all_cascading()` 对每个 TF 执行：
1. DB 查询 (`_query_tf_from_db`) — SQLite SELECT
2. 级联合成 (`_synthesize_incomplete_bar`) — 额外 DB 查询更细粒度数据
3. 写入 parquet (`_write_parquet`) — 文件 I/O

对于 N 个 bar、T 个 TF，产生 N * T 次 parquet 写入。这是**典型的 I/O 写放大的反模式**：窗口数据变化通常只是追加 1 个 bar，但整个窗口被完整序列化到 parquet。

**预估影响**: 对于 500 bar 回测、4 TF，约 2000 次 parquet 写入 + 2000 次 parquet 读取。每次写入约 10-50ms，总计约 20-100 秒的纯 I/O 开销。

### 3.2 EventRecorder 同步 JSONL 写入

**文件**: `filter_app/services/event_recorder.py`, 行 392-469

`record_step()` 每次调用：
- 打开 5 个 JSONL 文件句柄
- 每个事件写入后立即 `fp.flush()` (行 712) — 强制 fsync
- 4 视图 × 多个事件类型 = 每步约 8-20 个 `flush()` 调用

对于 1000 步回测，约 8000-20000 次 `flush()` 调用。每次 `flush()` 可能触发实际的磁盘写入（取决于操作系统缓冲区）。

**建议**: 使用内存缓冲，仅在 `end_session` 或定期（每 100 步）批量写入并 flush。

### 3.3 DB 连接模式

**文件**: `filter_app/db.py`, 行 19 和 `filter_app/streamlit_app.py`, 行 862-878

两个独立的 DB 连接管理策略共存：
- `db.py:get_conn()` — 每次调用创建新连接（用于 services/ 模块）
- `streamlit_app._cached_conn` — @st.cache_resource 单例连接（仅 Streamlit 使用）

这导致回测模式（BacktestRunner）每步创建/销毁多个 SQLite 连接，每次都有 `PRAGMA journal_mode=WAL` 等初始化开销。

`config_db.py` 使用独立的 `config.db`，有单独的连接管理和 WAL 初始化。

---

## 4. 缓存策略

### 4.1 Streamlit 缓存 (UI 模式良好，回测模式缺失)

**文件**: `filter_app/streamlit_app.py`

Streamlit UI 模式有良好的缓存层：

| 函数 | 行号 | 缓存类型 | 效果 |
|------|------|----------|------|
| `_cached_fetch_stock` | 75 | `@st.cache_data(ttl=3600)` | 1小时内缓存 yfinance 调用 |
| `_compute_filters` | 229 | `@st.cache_data(hash_funcs={np.ndarray})` | 缓存滤波计算 |
| `_compute_schmitt_trigger` | 260 | `@st.cache_data` | 缓存施密特触发器 |
| `_compute_prediction_pairs` | 272 | `@st.cache_data` | 缓存预测曲线 |
| `_cached_conn` | 862 | `@st.cache_resource` | 缓存 DB 连接 |

### 4.2 回测模式零缓存 (严重问题)

**文件**: `filter_app/services/backtest_core.py`

BacktestRunner 的回测管道(`_compute_pipeline_for_view`)完全不使用缓存：
- 没有 `functools.lru_cache`
- 没有内存缓存（滑动窗口间数据变化很小但全部重算）
- 没有利用 Streamlit 的 `st.cache_data`

在滑动窗口中，相邻 bar 的数据窗口有 **N-1 个 bar 重叠**。例如 120-bar 窗口从 bar_index=120 移到 bar_index=121 时，119 个 bar 完全相同，但滤波、施密特触发器、预测、PnL 全部重新计算。

**方案**: 实现增量计算：
- 大多数移动平均滤波 (SMA/EMA/WMA) 支持 O(1) 增量更新（保留 running sum）
- 施密特触发器的 EWMA 状态已通过 `_ewma_state` 跨窗口传递（好），但滤波器自身仍重新计算
- 对不支持增量计算的滤波（如 Savitzky-Golay），可使用 result cache（键 = hash(window N-1 个元素)）

### 4.3 Parquet 显示缓存 (未命中已存在数据)

**文件**: `filter_app/services/data_loader.py`, 行 737-818

`_sync_all_cascading()` 在回测模式下每步都重新写入所有 TF 的 parquet 文件，即使 DB 中数据未变。没有检查 parquet 文件是否已是最新版本（如通过 MD5 或时间戳比较）。

---

## 5. 并发模型

### 5.1 现状：以单线程为主

整个项目**仅有一处并发**：

**文件**: `filter_app/services/data_loader.py`, 行 52-56

```python
with ThreadPoolExecutor(max_workers=8) as exec:
    futures = {exec.submit(_fetch_one, tf): tf for tf in tf_config}
    for fut in as_completed(futures):
        ...
```

`_fetch_all_timeframes()` 使用 8 线程并行获取 8 个周期的数据。这是 I/O 绑定操作的良好使用。

### 5.2 回测循环：纯粹串行

**文件**: `filter_app/services/backtest_core.py`, 行 158

```python
for bar_index in range(start_bar, end_bar, step_interval):
    self._sync_data(cutoff_date)   # 必须串行 — 数据依赖
    view_outputs = {}
    for view_index, view_cfg in sorted_views:
        view_outputs[key] = self._compute_pipeline_for_view(...)
```

回测的主循环无法并行化（每个 bar 的 cutoff 依赖前序 bar），但同一 bar 内部的**多视图计算可以并行化**。目前 4 个视图在行 180-235 处顺序计算，尽管它们之间无数据依赖（跨周期对齐使用上一步的缓存）。

### 5.3 缺失的并行机会

| 任务 | 并行潜力 | 当前状态 |
|------|----------|----------|
| 多视图管道计算 | 高 (独立窗口) | 串行 |
| 回测 + 录制 | 高 (管道/线程) | 串行 |
| 数据同步 (`_sync_all_cascading` 内各 TF) | 中 | 串行 |
| 级联合成 (cascading synthesis) 各 TF | 中 | 串行 |

**建议**: 回测循环中对同一步的多个视图使用 `concurrent.futures.ThreadPoolExecutor` 并行执行管道计算，预计加速比约 2x-3x（受 GIL 限制，因 numpy 计算会释放 GIL）。

---

## 6. 内存使用

### 6.1 CSVBuilder 全量内存累积

**文件**: `filter_app/services/event_recorder.py`, 行 29-310

`CSVBuilder._rows` 是一个 dict[int, dict]，存储**所有 bar** 的数据。对于 5000 bar 回测 × 4 视图 × 每视图约 15 列 = 约 300,000 个标量值。每个 Python dict 条目有 key + value + overhead（约 72 字节），总计约 **20-50 MB**。

`write()` 方法（行 279-310）通过 `pd.DataFrame.from_dict()` + `ffill()` 生成最终 CSV。如果回测包含数万 bar，内存使用可能达到数百 MB。

**建议**: 分批写入 CSV（每 500-1000 bar），使用 `mode='a'` 追加。

### 6.2 ParquetStore 缓冲区无限增长

**文件**: `filter_app/services/parquet_store.py`, 行 189-191

```python
self._saved_buffer_size = self._buffer_size
self._buffer_size = 10_000_000   # 1千万! 实际上禁用了分块写入
```

`start_session()` 将 buffer_size 设为 10,000,000，意味着所有行在 `end_session()` 前不会写入磁盘。对于 5000 行 × 51 列，每条记录约 200 字节，总内存约 **1 MB** — 这实际上不是问题，但设计意图被覆盖（原本应为 100 行触发 flush）。

### 6.3 管道输出持有完整数组

**文件**: `filter_app/services/backtest_core.py`, 行 579-599

`_compute_pipeline_for_view()` 返回的 dict 包含长数组的引用（`t`, `noisy`, `filtered`, `filtered2`, `long_pnl`, `short_pnl` 等），每个约 120 * 8 字节 = ~1KB。`run()` 的结果列表 `results` 持有所有步的完整数据，对于 1000 步 = 约 4 视图 × 1KB × 6 数组 × 1000 = **~24 MB**。这是合理的，但如果有 10000 步则达 240 MB。

### 6.4 无内存泄漏风险

代码审查未发现明显的循环引用或全局状态累积。`BacktestRunner._ewma_state` 按视图键存储，数量固定（≤ 4），不存在泄漏。

---

## 7. 算法复杂度

### 7.1 _align_pnl_to_current_tf — O(n*m) 线性搜索 (严重)

**文件**: `filter_app/services/filter_engine.py`, 行 947-953

```python
for i in range(n):  # 当前周期的每个 bar
    mask = hd <= cd[i]
    j = np.max(np.where(mask)[0])  # 在较高周期中线性搜索最近 bar
```

n = 当前周期 bar 数 (~120)，m = 较高周期 bar 数 (~120)，总复杂度 O(n*m)。虽然常数因子小（120*120=14400），但在回测热路径中每次步骤调用 1-3 次（跨 3 个 TF 级联）。

**方案**: 使用 `np.searchsorted(hd, cd[i], side='right') - 1` 实现 O(n log m) 的对数时间搜索。对于 120*120 的规模，实际改进约 5-10x。

### 7.2 bs_marker._find_date_index — 线性扫描 O(n)

**文件**: `filter_app/services/bs_marker.py`, 行 56-58

```python
for i, d in enumerate(dates_idx):  # 线性扫描
    if d >= target_ts:
        return i
```

**方案**: 使用 `np.searchsorted` 替代线性扫描。

### 7.3 ParquetStore._apply_pending_events — O(R * V * E) 嵌套循环

**文件**: `filter_app/services/parquet_store.py`, 行 609-648

```python
for row in self._buffer:                    # O(R) — 所有缓冲行
    for prefix in self._view_prefixes:      # O(V) — 4 个视图
        key = f"{prefix}:{bar_date}"
        events = self._pending_events.get(key)  # O(1) dict 查找 ✓
        if events:
            row[trade_col] = events[trade_col]  # 逐个覆盖
```

总体 O(R * V) = O(4R)。对于 5000 行，约 20000 次操作。不算严重，但可以合并 `view_prefixes` 的循环。

### 7.4 _compute_strategy_pnl 的 forward fill — 两次 O(n) 遍历

**文件**: `filter_app/services/filter_engine.py`, 行 860-874

```python
# 做多曲线前向填充
for i in range(n):          # O(n)
    if long_pnl[i] == 100.0 and i > 0 and last_val != 100.0:
        long_pnl[i] = last_val
# 做空曲线前向填充
for i in range(n):          # O(n)
    if short_pnl[i] == 100.0 and i > 0 and last_val != 100.0:
        short_pnl[i] = last_val
```

**方案**: 使用 numpy 索引 + `np.maximum.accumulate` 或 `pd.Series.replace().ffill()` 实现向量化前向填充。

### 7.5 复杂度汇总

| 函数 | 文件:行 | 复杂度 | 数据规模 | 影响 |
|------|---------|--------|---------|------|
| `_align_pnl_to_current_tf` | filter_engine.py:947 | O(n*m) | n=m≈120 | 中 |
| `_find_date_index` | bs_marker.py:56 | O(n) 线性 | n≈120 | 低 |
| `apply_kalman` | filter_engine.py:184 | O(n) Python | n≈120 | 低 |
| `_schmitt_trigger` | filter_engine.py:487 | O(n) Python | n≈120 | 中 |
| `_compute_strategy_pnl` | filter_engine.py:673 | O(n + trades*bar) | n≈120 | 中 |
| `_apply_pending_events` | parquet_store.py:609 | O(R*V) | R≈5000 | 低 |

**整体评级**: 没有 O(n²) 或更差复杂度的缺陷。主要问题是 Python 级循环而非 numpy 向量化，以及线性搜索可优化为对数搜索。

---

## 8. 启动时间

### 8.1 模块导入开销

**文件**: `filter_app/streamlit_app.py`, 行 1-59

Streamlit 启动加载以下重量级模块：
- `yfinance` (~1.5s 首次导入，含网络栈初始化)
- `plotly.graph_objects` + `plotly.subplots` (~0.8s)
- `scipy.signal` + `scipy.ndimage` (~0.5s)
- `statsmodels.nonparametric.smoothers_lowess` (~0.3s)
- `pyarrow` + `pyarrow.parquet` (~0.4s)
- `numpy` + `pandas` (~0.3s)

**预估总导入时间**: 约 3-5 秒（冷启动，取决于磁盘缓存和 Python 版本）。

### 8.2 初始化顺序

`streamlit_app.main()` 执行顺序（估计）：
1. `AppState.init_defaults()` — ~1ms
2. `init_db()` — 创建 kline 表 + 索引，~10-50ms
3. `init_config_tables()` — 创建配置表，~10-50ms
4. `has_data(ticker)` — SQLite 查询，~1ms
5. `_fetch_all_timeframes()` — 8 线程并行 yfinance 调用，**网络依赖**，~5-30s

第 4 步对无数据 ticker 触发"首次获取"，这是**启动延迟的主要来源**（不是代码问题，而是网络 I/O）。

### 8.3 回测 CLI 启动

**文件**: `filter_app/backtest_cli.py`

CLI 模式较简洁：导入模块 → 解析参数 → 校验 ticker → 创建 runner → 运行。导入耗时与 Streamlit 相同（3-5s），但无需 Streamlit 框架启动。

---

## 优化方案汇总

### 关键修复 (高影响, 低成本)

| # | 问题 | 文件:行 | 方案 | 预估提升 |
|---|------|---------|------|---------|
| 1 | **逐 bar 重复 parquet I/O** | backtest_core.py:431-455 | 跳过未改变的窗口（内存中持有窗口数据） | **5x-10x** 回测吞吐 |
| 2 | **回测零缓存** | backtest_core.py:513-599 | 增量滤波器计算 + lru_cache 结果 | **3x-5x** 管道吞吐 |
| 3 | **单线程多视图** | backtest_core.py:174-235 | ThreadPoolExecutor 并行化视图计算 | **2x-3x** 管道吞吐 |
| 4 | **JSONL 逐事件 flush** | event_recorder.py:712 | 移除 flush()，由 OS buffer 管理 | **10x-50x** 录制吞吐 |

### 中优修复 (中影响, 中成本)

| # | 问题 | 文件:行 | 方案 | 预估提升 |
|---|------|---------|------|---------|
| 5 | **iterrows() 反模式** | db.py:86,559,626 | zip() 或 itertuples() | **3x-10x** 行迭代速度 |
| 6 | **_align_pnl_to_current_tf O(n*m)** | filter_engine.py:947 | searchsorted 替代线性搜索 | **5x-10x** 对齐速度 |
| 7 | **_find_date_index 线性扫描** | bs_marker.py:56 | searchsorted 替代 for 循环 | **5x-10x** 查找速度 |
| 8 | **apply_ema DataFrame 转换** | filter_engine.py:66 | pd.Series 替代 DataFrame | **~2x** EMA 计算速度 |
| 9 | **apply_kalman Python 循环** | filter_engine.py:184-197 | Numba JIT 或 Cython 优化 | **10x-50x** 卡尔曼滤波 |

### 低优修复 (低影响, 优化性)

| # | 问题 | 文件:行 | 方案 | 预估提升 |
|---|------|---------|------|---------|
| 10 | **前向填充 Python 循环** | filter_engine.py:860-874 | numpy 向量化 `ffill` | **3x-10x** 填充速度 |
| 11 | **CSVBuilder 全量内存** | event_recorder.py:39 | 分批写入 CSV | 减少峰值内存 |
| 12 | **重复 DB 连接创建** | db.py:19 | 连接池或单例 | 减少连接开销 |

### 架构建议

1. **回测数据管道重构**: 将 `_sync_data()` 从热循环中移出。改为在回测开始前一次性同步所有需要的数据窗口，在运行时直接从内存读取。这是**最高价值单项改动**。

2. **增量计算框架**: 为滤波器实现增量接口 `update(new_bar)`。大部分移动平均滤波器天然支持 O(1) 更新（仅 SMA/EMA/WMA/ALMA）。对于不支持增量的滤波器（Savitzky-Golay, Butterworth），可使用滑动窗口缓存。

3. **进程级并行**: 对于大规模回测（10000+ bar），考虑使用 `multiprocessing.Pool` 按 bar 范围分区，每个 worker 运行独立范围的回测。由于各 bar 范围的计算可串行可分区，此方案可达近似线性加速。

---

## 总体评估

| 维度 | 评级 | 说明 |
|------|------|------|
| 热点路径 | ⚠️ 中等 | 主要瓶颈在回测 I/O 和重复计算 |
| 数据处理 | ⚠️ 中等 | iterrows() 反模式；大部分计算已向量化 |
| I/O 瓶颈 | ❌ 临界 | 逐 bar 重复 parquet I/O 是最大瓶颈 |
| 缓存策略 | ⚠️ 中等 | Streamlit 缓存良好；回测模式零缓存 |
| 并发模型 | ⚠️ 中等 | 仅数据获取并行；回测循环无并行 |
| 内存使用 | ✅ 良好 | 合理的内存占用；无泄漏风险 |
| 算法复杂度 | ✅ 良好 | 无 O(n²) 缺陷；小优化点 |
| 启动时间 | ⚠️ 中等 | 模块导入 3-5s；首次数据获取取决于网络 |

**关键结论**: 系统的正确性设计和模块化做得很好。性能瓶颈集中在回测模式的 **I/O** 和 **重复计算**。将数据管道从逐 bar 文件 I/O 改为内存内流水线 + 增量计算是性价比最高的优化方向，可带来 **10x-50x** 的整体吞吐提升。
