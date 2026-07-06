# 回测模式变更前后逐行逻辑对比分析

> 分析日期: 2026-07-05 | 场景: AAPL, 4视图(15m/60m/日/周), bar_index 500→501→502

---

## 1. 变更概览

| 文件 | 变更行数 | 变更性质 |
|------|---------|---------|
| `streamlit_app.py` | -1/+11 | `_load_chart_data` 简化 + `main()` 新增级联合成调用 |
| `data_loader.py` | +244 | 新增级联合成子系统 (`_sync_all_cascading` + 12个辅助函数) |

核心变更: **数据写入职责从各视图的 `_load_chart_data` 内部 → 抽取到 `main()` 中的前置 `_sync_all_cascading` 调用**.

---

## 2. 源代码差异 (精确 diff)

### 2.1 `_load_chart_data()` — streamlit_app.py L116-163

**变更前 (master)**:
```python
# L122-134 (master)
if window_start is not None:
    # 回测模式：按 cutoff_date 日期对齐
    ok, count = _sync_to_display(ticker_code, tf, n_pts=n_pts, cutoff_date=cutoff_date)
    if not ok:
        # parquet 写入失败，直接走 API 回退
        t, noisy, ohlc, ticker_full, dates, err = _cached_fetch_stock(market, ticker_code, tf, n_pts)
        # API 回退路径：截断到 n_pts
        if err is None and dates is not None and len(dates) > n_pts:
            t = t[-n_pts:]
            noisy = noisy[-n_pts:]
            if hasattr(ohlc, 'iloc'):
                ohlc = ohlc.iloc[-n_pts:]
            dates = dates[-n_pts:]
        return t, noisy, ohlc, ticker_full, dates, err
```

**变更后 (当前分支)**:
```python
# L122-125 (current)
if window_start is not None:
    # 回测模式: parquet 已由 _sync_all_cascading() 前置写入
    # 直接走下方 parquet 读取路径; parquet 不存在时走 API 回退
    pass
```

### 2.2 `main()` — streamlit_app.py L1611-1617 (新增)

```python
# ── 回测模式: 前置级联合成（一次性写入所有TF的parquet）──
if cb_mode and ticker_code and cutoff_date:
    tfs_in_use = sorted(set(cfg["tf"] for cfg in configs),
                        key=lambda x: ALL_TFS.index(x))
    min_tf_val = AppState.get("_min_tf", "")
    if tfs_in_use and min_tf_val:
        _sync_all_cascading(ticker_code, tfs_in_use, cutoff_date, min_tf_val)
```

### 2.3 `data_loader.py` — 新增 +244 行

新增函数: `_query_tf_from_db`, `_query_tf_for_period`, `_find_immediate_finer_tf`, `_needs_synthesis`, `_get_period_start_ts`, `_get_query_start_for_synthesis`, `_aggregate_bars`, `_format_synth_date`, `_synthesize_incomplete_bar`, `_build_output_df`, `_write_parquet`, `_sync_all_cascading`

---

## 3. 场景追踪: bar_index 500→501→502

### 3.1 前提条件

```
play 启动时 bar_index=500:
  window_start = 500
  cutoff_date = _get_bar_date_from_db("AAPL", "15分钟", 499)
              = "2026-07-03T10:00:00+08:00"  (bar #499 的 ts)

播放器前进 (500→501):
  _run_backtest_play():
    bar_index = 500 (read from session_state)
    st.session_state._bar_index = 501        # window_start 变为 501
    cutoff_date = _get_bar_date_from_db("AAPL", "15分钟", 500)
                = "2026-07-03T10:15:00+08:00"  (bar #500 的 ts)
```

### 3.2 变更前执行路径 (bar_index 500→501)

```
main()
│
├─ _run_backtest_play()
│   ├─ _bar_index: 500 → 501
│   ├─ cutoff_date → bar#500 的 ts: "2026-07-03T10:15:00+08:00"
│   └─ return True
│
├─ cb_mode=True, window_start=501, cutoff_date="2026-07-03T10:15:00"
│
├─ [渲染 4 个视图] ──────────────────────────────────────────────
│
│   ┌─ 视图1 (15分钟, n_pts=120) ────────────────────────────┐
│   │ _render_chart_fragment()                               │
│   │   └─ _render_chart()                                   │
│   │       └─ _load_chart_data(market, "AAPL", "15分钟",    │
│   │                            day_offset, n_pts=120,      │
│   │                            window_start=501,            │
│   │                            cutoff_date="...10:15:00")   │
│   │           │                                            │
│   │           ├─ if window_start is not None:  ← 命中      │
│   │           │   _sync_to_display("AAPL", "15分钟",       │
│   │           │       n_pts=120, cutoff_date="...10:15")   │
│   │           │   → SQL: SELECT ... ts <= cutoff_date      │
│   │           │           ORDER BY ts DESC LIMIT 120       │
│   │           │   → rows.reverse(), 构建 DataFrame         │
│   │           │   → 写 parquet: data/display/15分钟.parquet│
│   │           │   → return (True, 120)                     │
│   │           │                                            │
│   │           ├─ pd.read_parquet("15分钟.parquet")         │
│   │           ├─ 转换为 numpy, 构建 ohlc                   │
│   │           └─ return (t, noisy, ohlc, ...)              │
│   │                                                        │
│   │       继续渲染: 滤波 → 施密特 → PnL → Plotly           │
│   └────────────────────────────────────────────────────────┘
│
│   ┌─ 视图2 (60分钟, n_pts=100) ────────────────────────────┐
│   │ _load_chart_data(market, "AAPL", "60分钟",              │
│   │                   day_offset, n_pts=100,                │
│   │                   window_start=501,                     │
│   │                   cutoff_date="...10:15:00")            │
│   │   → _sync_to_display("AAPL", "60分钟",                  │
│   │       n_pts=100, cutoff_date="...10:15")               │
│   │   → SQL: SELECT ... ts <= cutoff_date                  │
│   │           ORDER BY ts DESC LIMIT 100                   │
│   │   → 写 parquet: data/display/60分钟.parquet             │
│   │   → 读 parquet → 渲染                                  │
│   └────────────────────────────────────────────────────────┘
│
│   ┌─ 视图3 (日线, n_pts=60)  ── 同上, LIMIT 60 ──────────┐
│   ┌─ 视图4 (周线, n_pts=40)  ── 同上, LIMIT 40 ──────────┐
│
├─ sleep(1s) → st.rerun()
```

**每步 rerun: 4 次 DB 查询, 4 次 parquet 写入, 无合成逻辑.**

### 3.3 变更后执行路径 (bar_index 500→501)

```
main()
│
├─ _run_backtest_play()
│   ├─ _bar_index: 500 → 501
│   ├─ cutoff_date → bar#500 的 ts: "2026-07-03T10:15:00+08:00"
│   └─ return True
│
├─ cb_mode=True, window_start=501, cutoff_date="2026-07-03T10:15:00"
│
├─ ★ 新增: 前置级联合成 ─────────────────────────────────────
│   │
│   │ tfs_in_use = ["15分钟","60分钟","日线","周线"] (按ALL_TFS排序)
│   │ min_tf_val = "15分钟"
│   │
│   │ _sync_all_cascading("AAPL", tfs_in_use, cutoff_date, "15分钟", n_pts=120)
│   │
│   │   ┌─ TF=15分钟 ─────────────────────────────────────┐
│   │   │ _query_tf_from_db("AAPL", "15分钟", cutoff_date, 120)
│   │   │   → SQL: SELECT ... ts <= cutoff_date          │
│   │   │           ORDER BY ts DESC LIMIT 120           │
│   │   │   → 返回 120 条 dict (ASC序)                   │
│   │   │                                                │
│   │   │ _needs_synthesis("15分钟", ...)                │
│   │   │   → tf == min_tf → **不合成**                  │
│   │   │                                                │
│   │   │ _build_output_df(db_rows, None, 120)           │
│   │   │   → DataFrame(120 rows)                        │
│   │   │ _write_parquet("15分钟", df) → True             │
│   │   │                                                │
│   │   │ synth_cache["15分钟"] = {"synth_bar": None}     │
│   │   └────────────────────────────────────────────────┘
│   │
│   │   ┌─ TF=60分钟 ─────────────────────────────────────┐
│   │   │ _query_tf_from_db("AAPL", "60分钟", cutoff_date, 120)
│   │   │   → 返回 N 条 (最后一条 bar 的 ts 可能是 10:00)│
│   │   │                                                │
│   │   │ _needs_synthesis("60分钟", rows, cutoff_date)  │
│   │   │   last_ts = pd.Timestamp("2026-07-03 10:00")    │
│   │   │   period_start = 10:00 + 1min = 10:01          │
│   │   │   cutoff_dt = 10:15                            │
│   │   │   → 10:15 >= 10:01 → **需要合成**              │
│   │   │                                                │
│   │   │ _find_immediate_finer_tf("60分钟", tfs)        │
│   │   │   → "15分钟"                                   │
│   │   │                                                │
│   │   │ _synthesize_incomplete_bar(                    │
│   │   │     target_tf="60分钟", db_rows=N条,            │
│   │   │     cutoff_date="10:15",                       │
│   │   │     finer_tf="15分钟", finer_synth_bar=None)   │
│   │   │   ├─ query_start = 10:00 + 1min = 10:01       │
│   │   │   ├─ _query_tf_for_period("AAPL","15分钟",     │
│   │   │   │      "10:01", "10:15")                     │
│   │   │   │   → 1条 bar: [{10:15, O:195, C:196...}]   │
│   │   │   ├─ _aggregate_bars → synth bar               │
│   │   │   │   O=195, H=196, L=195, C=196, V=5000      │
│   │   │   └─ return synth bar                          │
│   │   │                                                │
│   │   │ _build_output_df(N条 + 1条 synth, 120)         │
│   │   │   → DataFrame(N+1 rows)                        │
│   │   │ _write_parquet("60分钟", df) → True             │
│   │   │                                                │
│   │   │ synth_cache["60分钟"] = {"synth_bar": {...}}    │
│   │   └────────────────────────────────────────────────┘
│   │
│   │   ┌─ TF=日线 ───────────────────────────────────────┐
│   │   │ _query_tf_from_db("AAPL", "日线", cutoff_date, 120)
│   │   │   → 返回 M 条 (最后一条: 7月2日)               │
│   │   │                                                │
│   │   │ _needs_synthesis("日线", rows, cutoff_date)    │
│   │   │   last_ts = pd.Timestamp("2026-07-02")          │
│   │   │   period_start = 7月2日 + 1day = 7月3日         │
│   │   │   cutoff_dt = "2026-07-03 10:15"               │
│   │   │   → cutoff >= period_start → **需要合成**       │
│   │   │                                                │
│   │   │ _find_immediate_finer_tf("日线", tfs)          │
│   │   │   → "60分钟"                                   │
│   │   │                                                │
│   │   │ _synthesize_incomplete_bar(                    │
│   │   │     target_tf="日线", finer_tf="60分钟",        │
│   │   │     finer_synth_bar=60分钟的 synth_bar)        │
│   │   │   ├─ query_start = 7月2日 + 1day = 7月3日      │
│   │   │   ├─ market_open 调整: 7月3日 09:30            │
│   │   │   ├─ actual_start = 09:30                      │
│   │   │   ├─ _query_tf_for_period("AAPL","60分钟",      │
│   │   │   │      "09:30", "10:15")                     │
│   │   │   │   → 1条 60min bar (09:30-10:00)            │
│   │   │   ├─ merge 60分钟的 synth_bar                  │
│   │   │   ├─ _aggregate_bars → synth daily bar         │
│   │   │   └─ return synth bar                          │
│   │   │                                                │
│   │   │ _build_output_df(M条 + 1条 synth, 120)         │
│   │   │   → DataFrame(M+1 rows)                        │
│   │   │ _write_parquet("日线", df) → True               │
│   │   └────────────────────────────────────────────────┘
│   │
│   │   ┌─ TF=周线 ── 同上，从日线合成 ───────────────────┐
│   │
│   │ return {"15分钟": True, "60分钟": True, "日线": True, "周线": True}
│   │
│   └─── 返回值被丢弃 (main 未检查) ───────────────────────
│
├─ [渲染 4 个视图] ──────────────────────────────────────────────
│
│   ┌─ 视图1 (15分钟) ──────────────────────────────────────┐
│   │ _load_chart_data(window_start=501, ...)               │
│   │   ├─ if window_start is not None: pass  ← **跳过!**   │
│   │   ├─ pd.read_parquet("data/display/15分钟.parquet")    │
│   │   │   → 120 条 bar (由级联合成写入)                    │
│   │   ├─ 转换为 numpy                                     │
│   │   └─ return (t, noisy, ohlc, ...)                     │
│   └────────────────────────────────────────────────────────┘
│
│   ┌─ 视图2 (60分钟) ── 同上, 读 parquet, 含合成 bar ──────┐
│   ┌─ 视图3 (日线)   ── 同上, 读 parquet, 含合成 bar ──────┐
│   ┌─ 视图4 (周线)   ── 同上, 读 parquet, 含合成 bar ──────┐
│
├─ sleep(1s) → st.rerun()
```

**每步 rerun: 4 次基础 DB 查询 + 最多 3 次合成期查询 = 最多 7 次 DB 查询, 4 次 parquet 写入.**

---

## 4. 差异点逐项分析

### 差异 #1: `_sync_to_display` 调用位置与时机

| | 变更前 | 变更后 |
|---|--------|--------|
| 调用位置 | `_load_chart_data` 内部 (每个视图独立) | `main()` 中 `_sync_all_cascading` (所有视图前) |
| 调用次数/rerun | 4 次 (每个视图 ×1) | 1 次 |
| 调用时机 | 按视图渲染顺序串行, 第2个视图依赖第1个视图的 parquet? 否, 各写各的 | 所有视图渲染前一次性写入 |

**风险: P2 (低).** 调用次数和位置变化不影响正确性, 但需注意时序: 变更前每个视图写入后立即读取, 变更后先全部写入再全部读取. 如果 `_sync_all_cascading` 中途异常, 部分 parquet 可能未写入, 导致部分视图回退到 yfinance.

---

### 差异 #2: n_pts 参数不一致 — **P1**

| | 变更前 | 变更后 |
|---|--------|--------|
| 15分钟 | `_sync_to_display(n_pts=120)` | `_query_tf_from_db(n_pts=120)` |
| 60分钟 | `_sync_to_display(n_pts=cfg["n_pts"])` 例: 100 | `_query_tf_from_db(n_pts=120)` |
| 日线 | `_sync_to_display(n_pts=cfg["n_pts"])` 例: 60 | `_query_tf_from_db(n_pts=120)` |
| 周线 | `_sync_to_display(n_pts=cfg["n_pts"])` 例: 40 | `_query_tf_from_db(n_pts=120)` |

**变更后 `_sync_all_cascading` 使用固定 `n_pts=120` (L374), 不读取各个视图的 `cfg["n_pts"]`.**

**具体影响:**
- `_query_tf_from_db` 对所有 TF 使用 `LIMIT 120`, 而不是各视图配置的 n_pts
- `_build_output_df` 对所有 TF 使用 `n_pts=120` 截断, 而不是各视图配置的 n_pts
- parquet 文件中包含 120 条 bar (或 min(available, 120)), 而视图 `_render_chart` 的 `n_pts = cfg["n_pts"]` 变量仍然是从 config 读取的 (L507)
- `_load_chart_data` 不再截断数据 (L141 注释: "不再需要截断")
- 图表直接渲染 parquet 中的所有数据点 — **若 DB 中实际 bar 数 >= 120, 日线视图会显示 120 条 bar 而非配置的 60 条**

**代码证据:**
- `data_loader.py` L374: `def _sync_all_cascading(..., n_pts: int = 120)`
- `data_loader.py` L277: `_query_tf_from_db(..., n_pts)` → `LIMIT ?` (n_pts=120)
- `data_loader.py` L391: `_build_output_df(db_rows, synthesized_bar, n_pts)` → 截断到 n_pts=120
- `streamlit_app.py` L507: `n_pts = cfg["n_pts"]` (各视图自己的值, 例如 60)
- `streamlit_app.py` L517: `_load_chart_data(..., n_pts=n_pts, ...)` — n_pts 传入但回测路径忽略
- `streamlit_app.py` L141: 注释 "不再需要截断！parquet 已经是 n_pts 条" — **该注释在变更后已不准确, parquet 条数 ≠ 视图 n_pts**

**严重程度:** 不会导致崩溃, 但会导致:
1. 图表显示的数据点数量与用户配置不符
2. 滤波参数(如 N_EWMA)基于 `n_pts` 调优, 但实际数据量不同, 可能影响滤波效果
3. 不同 TF 视图的数据量不一致问题不明显(都被拉平到 120), 但用户无法区分是配置还是回退导致

---

### 差异 #3: 回退路径 _cached_fetch_stock 的行为

**变更前:**
```python
# _load_chart_data (master L126-134)
if window_start is not None:
    ok, count = _sync_to_display(ticker_code, tf, n_pts=n_pts, cutoff_date=cutoff_date)
    if not ok:
        # 回退到 yfinance
        t, noisy, ohlc, ticker_full, dates, err = _cached_fetch_stock(...)
        # 截断到 n_pts
        if err is None and dates is not None and len(dates) > n_pts:
            t = t[-n_pts:]
            noisy = noisy[-n_pts:]
            ...
        return t, noisy, ohlc, ticker_full, dates, err  # ← 提前返回
```

**变更后:**
```python
# _load_chart_data (current L122-125)
if window_start is not None:
    pass  # 继续到下方 parquet 读取

# L132-163 (current)
display_path = ...
if display_path.exists():
    # 读 parquet
    ...
if err is not None:
    return None, None, None, None, None, err
return _cached_fetch_stock(market, ticker_code, tf, n_pts)  # ← 回退
```

**风险: P1.** 两个子问题:

**(a) 变更前回退路径有 n_pts 截断, 变更后没有:**
- 变更前: `_cached_fetch_stock` 返回后, 显式截断到 `n_pts`
- 变更后: `_cached_fetch_stock` 直接返回, 无截断 — `_fetch_stock` 内部调用 `query_kline(code, tf, n_pts, day_offset=0)` 返回最新 n_pts 条, 所以数据量是 `n_pts`. 但如果 yfinance 返回了更多数据(force_period 参数), 实际写入 DB 的数据可能超过 n_pts. 不过 `query_kline` 做了 LIMIT, 所以返回数据量 = min(n_pts, 可用数据). 变更前后在这个子问题上**实际等价**.

**(b) 变更前 `_cached_fetch_stock` 提前 return, 变更后走 parquet → 回退路径:**
- 变更前: `_sync_to_display` 失败 → 立即 `_cached_fetch_stock` → 返回. 不尝试读 parquet.
- 变更后: `_sync_all_cascading` 失败(parquet 未写入) → 尝试读 parquet(不存在) → err=None → `_cached_fetch_stock`. **结果相同: 都回退到 yfinance.**

**(c) 核心问题: 两种回退都返回最新数据, 非 cutoff_date 的历史数据.**
- `_cached_fetch_stock` → `_fetch_stock` → `query_kline(code, tf, n_pts, day_offset=0)` → **最新的 n_pts 条, 不考虑 cutoff_date**
- 这意味着回测中若 parquet 缺失, 图表会突然跳到最新数据, 破坏回测的时间一致性
- **此问题存在于变更前后, 但变更后 parquet 缺失的概率发生了改变**: 变更前 `_sync_to_display` 失败意味着 DB 中该 TF 无数据; 变更后 `_sync_all_cascading` 失败可能是级联合成链路中的任何一环断裂

---

### 差异 #4: `_sync_all_cascading` 返回值被丢弃 — **P1**

```python
# streamlit_app.py L1617
_sync_all_cascading(ticker_code, tfs_in_use, cutoff_date, min_tf_val)
# ↑ 返回值 dict 被丢弃!
```

**问题:** 如果全部 TF 都失败(如 DB 连接异常、权限问题), 函数返回 `{"15m": False, "60m": False, "日线": False, "周线": False}`, 但 `main()` 完全不知道. 所有 4 个视图都回退到 `_cached_fetch_stock`, 显示最新数据而非回测截断数据 — 且没有任何错误提示.

**变更前:** `_sync_to_display` 失败是个别 TF 的问题, 其他 TF 仍可能成功. `ok==False` 时独立回退.

**风险:** 虽然 DB 连接失败是小概率事件, 但一旦发生, 回测模式悄然崩溃为浏览模式, 用户看到的是:
- 最新数据(非历史截断)
- 没有错误提示
- 播放仍正常推进(bar_index 仍在递增)
→ 用户可能完全没注意到回测已失效

---

### 差异 #5: cutoff_date 为空时行为 — **P2**

```
刚进入回测模式时:
  _render_backtest_mode():
    AppState.set("_bt_cutoff_date", cutoff_date)  # 设置 cutoff_date
  → 用户切换 radio → st.rerun()  # Streamlit 自然触发 rerun
  → main():
      cb_mode = True
      cutoff_date = AppState.get("_bt_cutoff_date", "")
      → 已设置, 非空 ✓
```

**变更后 main() L1612 的条件 `if cb_mode and ticker_code and cutoff_date:` 能正确处理空 cutoff_date — 不执行级联合成.**

但是, 如果 cutoff_date 为空但 cb_mode=True, 变更前:
- `_load_chart_data` 走到 `window_start is not None` 分支
- `_sync_to_display(n_pts=n_pts, cutoff_date="")` 被调用
- cutoff_date 为空字符串 → SQL: `... ts <= ''` → 0 条结果 → `return False, 0`
- 回退到 `_cached_fetch_stock`

变更后:
- `_sync_all_cascading` 不被调用(条件保护)
- `_load_chart_data` 走到 `pass` → 读 parquet
- parquet 不存在 → err=None → `_cached_fetch_stock`
- 结果相同: 都回退到最新数据

**风险: P2.** 条件保护 (L1612) 阻止了错误调用, 行为等价.

---

### 差异 #6: 合成链断裂 — **P1**

当某个中间 TF 的 DB 无数据时:

```
假设 60分钟 DB 无数据:

_sync_all_cascading:
  TF=15分钟: db_rows=120 → 写入成功 ✓ → synth_cache["15分钟"] = {"synth_bar": None}
  TF=60分钟: db_rows=0 → continue (跳过) → synth_cache 中无 "60分钟" 条目 ✗
  TF=日线:   db_rows=M条 → needs_synthesis=True
             finer_tf = _find_immediate_finer_tf("日线", tfs) → "60分钟"
             if finer_tf in synth_cache: → False! (因为 60分钟 被跳过了)
             → 不合成, 只用完成的 M 条日线 bar
             结果: 日线 parquet 缺少今天的合成 bar
  TF=周线:   同上, 也无法合成 (即使日线有合成bar, 但日线的 synth_cache 中 synth_bar=None)
```

**实际上更微妙:** 因为 `_find_immediate_finer_tf` 只是找 ALL_TFS 索引中更小的那个, 不检查该 TF 是否有数据. 真正的保护在 L407: `if finer_tf and finer_tf in synth_cache`.

**变更前:** 不存在此问题 — 每个视图独立查询, 不会跨 TF 依赖. 日线视图查询自己的 DB 数据, 没有合成概念.

**变更后:** 引入了 TF 间数据依赖. 中间 TF 缺失时, 所有更粗粒度的 TF 都无法合成.

**风险:** 如果 DB 中 60分钟 数据缺失(常见于 A 股/港股 部分 ticker), 日线 和 周线 的回测视图将无法显示截止到 cutoff_date 的合成 bar, 只能看到上一完整周期的数据.

---

### 差异 #7: DB 查询次数变化

| | 变更前 | 变更后 |
|---|--------|--------|
| 基础查询/rerun | 4 次 (每个视图各自 `_sync_to_display`) | 4 次 (`_query_tf_from_db`) |
| 合成期查询/rerun | 0 | 最多 3 次 (`_query_tf_for_period`) |
| 合计 | **4 次** | **4~7 次** |

**风险: P2.** 查询次数增加在 1x 播放速度下无影响, 在 5x+ 速度下可能成为瓶颈, 但远未达到需要优化的程度.

---

### 差异 #8: parquet 写入内容的语义差异

**变更前:**
- 每个 TF 的 parquet 包含: DB 中该 TF 的 completed bars (按 cutoff_date 截断)
- 没有合成逻辑 — 当天尚未完成的 bar 不出现在 parquet 中
- 例: 10:15 时查看日线, parquet 只有到昨天(7月2日)的 bars

**变更后:**
- 非 min_tf 的 parquet 包含: DB 中 completed bars + **合成的部分 bar** (由级联合成追加)
- 例: 10:15 时查看日线, parquet 有到昨天(7月2日)的 bars + 7月3日(今天)的合成 bar

**这是一个增强功能**, 让用户在当前 bar 尚未完成时就能在粗粒度视图上看到"部分完成"的 bar 数据.

**风险: P2.** 合成 bar 使用 `_aggregate_bars` (O=first, C=last, H=max, L=min, V=sum) — 这是 OHLC 的标准合成规则, 正确. 但需要注意:
- 合成的 bar 有 `Date = cutoff_date` (经过 `_format_synth_date` 处理时区), 这可能与常规 bar 的 `Date` 命名规则不同(常规 bar 通常标记周期的起始或结束时间)
- 用户看到的日线图最后一条 bar 是 **不完整的白天线** — 这需要在 UI 上有所标识, 目前没有

---

### 差异 #9: `_get_period_start_ts` 和 `_get_query_start_for_synthesis` 缺少 "1分钟" 显式分支

```python
# data_loader.py L246-254
def _get_period_start_ts(ts, tf: str):
    ts = _ensure_tz_naive(pd.Timestamp(ts))
    if tf in ("5分钟", "15分钟", "60分钟"):
        return ts + pd.Timedelta(minutes=1)
    elif tf in ("日线", "周线", "月线", "季线"):
        return (ts + pd.Timedelta(days=1)).normalize()
    else:
        return ts + pd.Timedelta(minutes=1)  # "1分钟" 走这里
```

`"1分钟"` 不在显式列表中, 靠 `else` 分支覆盖, 行为正确. 但如果将来 `min_tf` 是 "1分钟", 且 `1分钟` 不在 `tfs_in_use` 中(因为 1-min 数据量太大, 用户可能不在视图中配置), 级联合成链路的行为是:
- `_needs_synthesis("5分钟", ..., cutoff_date)` → 可能为 True
- `_find_immediate_finer_tf("5分钟", tfs)` → 在 tfs 中找比 5分钟 更细的 → 如果 tfs 中没有"1分钟" → 返回 "15分钟"? 不, reversed(tfs) 按 ALL_TFS 索引搜索:
  - tfs = ["15分钟", "60分钟", "日线", "周线"]
  - reversed = ["周线", "日线", "60分钟", "15分钟"]
  - ALL_TFS.index("周线")=5, ALL_TFS.index("日线")=4, ALL_TFS.index("60分钟")=3, ALL_TFS.index("15分钟")=2
  - ALL_TFS.index("5分钟")=1
  - 找到 ALL_TFS.index("15分钟")=2 > 1? No, 2 is NOT < 1
  - 继续: ALL_TFS.index("60分钟")=3 < 1? No
  - 继续: ALL_TFS.index("日线")=4 < 1? No
  - 继续: ALL_TFS.index("周线")=5 < 1? No
  - → 返回 None!  无法合成 5分钟 bar!

  等等, 但 5分钟 不在 tfs 中... `_sync_all_cascading` 只处理 tfs 参数中的 TF. 所以如果 tfs 中没有 5分钟, 就不会尝试合成 5分钟. 如果 min_tf 是 "1分钟" 但 tfs 中没有它... 这不太可能, 因为 `tfs_in_use` 来自 configs 的 tf 字段, min_tf 也来自这些 configs.

**风险: P2 (代码质量).** 当前场景 (min_tf="15分钟", tfs=["15分钟","60分钟","日线","周线"]) 不受影响.

---

## 5. 潜在问题汇总

| # | 问题 | 风险等级 | 影响 | 触发条件 |
|---|------|---------|------|---------|
| 1 | **n_pts 强制统一为 120** | **P1** | 日线/周线显示的数据量 ≠ 配置值; 注释"不再需要截断"不准确 | 任何视图的 n_pts != 120 |
| 2 | **`_sync_all_cascading` 返回值被丢弃** | **P1** | 级联合成全部失败时用户无感知, 悄然回退到最新数据 | DB 连接异常 / 所有 TF 查询失败 |
| 3 | **合成链断裂** | **P1** | 中间 TF 无数据时, 粗粒度 TF 的合成 bar 缺失 | 60分钟 DB 无数据(常见于部分 ticker) |
| 4 | **回退路径返回最新数据** | **P1** | 回测中 parquet 缺失 → 显示最新数据, 破坏时间一致性 | parquet 写入失败 / 不存在 |
| 5 | **n_pts 截断注释错误** | **P2** | L141 注释误导: parquet 条数(120) ≠ 视图 n_pts | 任何视图的 n_pts != 120 |
| 6 | **合成 bar 无 UI 标识** | **P2** | 用户无法区分完整 bar 和合成部分 bar | 播放到当天尚未完成的 bar 时 |
| 7 | **DB 查询量增加 75%** | **P2** | 4→7 次查询/rerun, 高速播放时可能卡顿 | 3+ TF 且需要级联合成 |
| 8 | **`1分钟` 分支靠 else 覆盖** | **P2** | `_get_period_start_ts` 无显式"1分钟"分支 | min_tf="1分钟"时 |

---

## 6. 修复建议 (按优先级)

### P1-1: n_pts 一致性

**方案 A (推荐):** 接受 `n_pts=120` 为统一值, 但需修改 `_load_chart_data` 在回测路径也做截断:

```python
# streamlit_app.py L122-125, 替换 pass
if window_start is not None:
    # 回测模式: parquet 已由 _sync_all_cascading() 前置写入
    # 直接走下方 parquet 读取路径; parquet 不存在时走 API 回退
    pass  # 保持不变

# L132-163 的 parquet 读取后, 恢复截断:
# 修改 L141 注释并添加截断:
# 将 "★ 不再需要截断！parquet 已经是 n_pts 条" 
# 改为实际需要的截断逻辑
```

或者

**方案 B:** `_sync_all_cascading` 接受 per-TF n_pts, 从 configs 读取:

```python
# 在 main() L1617 前构造 per-TF n_pts dict
tf_n_pts = {cfg["tf"]: cfg["n_pts"] for cfg in configs}
# 修改 _sync_all_cascading 签名, 接受 Dict[str, int] 而非固定 int
```

### P1-2: 级联合成失败检测

```python
# streamlit_app.py L1617
results = _sync_all_cascading(ticker_code, tfs_in_use, cutoff_date, min_tf_val)
failed_tfs = [tf for tf, ok in results.items() if not ok]
if failed_tfs:
    logger.warning(f"回测级联合成失败: {failed_tfs}")
    # 可选: 在 UI 显示 warning
```

### P1-3: 合成链断裂处理

当 `_find_immediate_finer_tf` 返回的 finer_tf 不在 `synth_cache` 中时, 应尝试跳过该 TF, 用下一个可用的 finer TF 继续. 当前实现在 L407 处悄然跳过.

```python
# data_loader.py L405-420, 增强:
if needs_synth:
    finer_tf = _find_immediate_finer_tf(tf, tfs)
    # 若 finer_tf 不在 cache 中, 尝试往更细方向继续查找
    while finer_tf and finer_tf not in synth_cache:
        next_finer = _find_immediate_finer_tf(finer_tf, tfs)
        if next_finer == finer_tf:
            finer_tf = None
            break
        finer_tf = next_finer
    ...
```

### P1-4: 回退路径保留历史语义

在 `_load_chart_data` 的回退路径中, 当 `window_start is not None` 时, 不应调用无过滤的 `_cached_fetch_stock`, 而应:
- 尝试用 `_sync_to_display(cutoff_date=cutoff_date, n_pts=n_pts)` 重新写入 parquet
- 或直接查询 DB 而非 yfinance

```python
# streamlit_app.py L163, 修改:
if window_start is not None:
    # 回测回退: 重试 _sync_to_display (保留 cutoff_date)
    ok, _ = _sync_to_display(ticker_code, tf, n_pts=n_pts, cutoff_date=cutoff_date)
    if ok:
        # 重读 parquet
        df = pd.read_parquet(display_path)
        ...
    else:
        err = f"回测数据不可用: {tf}"
        return None, None, None, None, None, err
else:
    return _cached_fetch_stock(market, ticker_code, tf, n_pts)
```

### P2 项

- **合成 bar UI 标识:** 在图表上对合成 bar 加前缀标记或使用虚线
- **注释更新:** L141 注释改为准确描述
- **"1分钟" 显式分支:** 在 `_get_period_start_ts` 和 `_get_query_start_for_synthesis` 中添加 "1分钟" 分支

---

## 7. 总结

| 维度 | 评估 |
|------|------|
| **核心逻辑正确性** | 级联合成逻辑本身正确, OHLC 聚合规则符合金融数据标准 |
| **架构合理性** | 前置写入减少重复查询, 合成增强粗粒度视图的实时性 — 方向正确 |
| **主要缺陷** | 4 个 P1 问题: n_pts 不一致, 失败不可见, 合成链脆弱, 回退路径语义错误 |
| **可观测性** | 级联合成失败完全静默, 用户无法区分"合成成功"和"静默回退到最新数据" |
| **推荐行动** | 修复 4 个 P1 问题后即可合并; P2 项可后续迭代 |

---

*分析完成. 所有代码引用基于 `git diff master...HEAD` 精确对比.*
