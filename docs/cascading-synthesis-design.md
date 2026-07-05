# 回测数据级联（Cascading）合成方案 — 详细设计

> 作者: 系统架构师
> 日期: 2026-07-05
> 版本: v1.0
> 基于分支: `feat/backtest-data-analysis`（当前HEAD: f780a39）
> 参考分支: `feature/backtest-bar-synthesis`（已有单级合成逻辑）

---

## 目录

1. [概述与动机](#1-概述与动机)
2. [核心挑战分析](#2-核心挑战分析)
3. [方案设计](#3-方案设计)
4. [实现细节](#4-实现细节)
5. [ASCII数据流图](#5-ascii数据流图)
6. [实施建议](#6-实施建议)
7. [附录: 与现有代码的兼容性影响](#7-附录-与现有代码的兼容性影响)

---

## 1. 概述与动机

### 1.1 当前状态

`feature/backtest-bar-synthesis` 分支实现了**单级合成**: 每个粗周期 TF 独立地从 `min_tf`（最细周期）的 DB 数据合成自己不完整的 bar。

**示例（min_tf=15分钟，视图含 60分钟和日线）**:

```
DB (15分钟数据) ──→ 合成60分钟不完整bar ──→ 写入 60分钟.parquet
DB (15分钟数据) ──→ 合成日线不完整bar   ──→ 写入 日线.parquet
```

**局限**:
1. 日线直接从15分钟合成: 需要查询 6.5小时/15分钟 ≈ 26条15分钟bar来合成1条日线bar，查询量大
2. 60分钟的合成bar只存在于 parquet，日线合成时无法利用它
3. 日线合成查询 `min_tf`（15分钟）的数据范围被 `cutoff_date` 限制，可能漏掉数据
4. 周线/月线/季线完全不做合成（因为不在 `tf in ("5分钟","15分钟","60分钟")` 范围内）

### 1.2 目标

实现**级联合成**: 每一级合成使用紧邻更细一级的**完整数据**（含该级自己合成的bar），逐级向上传递。

```
15分钟(完整) → 合成60分钟(含不完整bar)
             → 60分钟(含合成bar) → 合成日线(含不完整bar)
                                → 日线(含合成bar) → 合成周线
                                                  → ...以此类推
```

**为什么需要级联而非直接从 min_tf 合成?**

| 方面 | 单级合成（现状） | 级联合成（目标） |
|------|-----------------|-----------------|
| 日线合成数据源 | 15分钟(26条) | 60分钟(约7条，假设盘中) |
| 查询效率 | 查询 min_tf 的细粒度数据 | 查询已合成的粗粒度数据 |
| 周线合成 | 不支持 | 从日线合成(5条日线bar) |
| 月线/季线 | 不支持 | 从日线/月线逐级合成 |
| 合成bar复用 | 不能（合成bar不在DB） | 能（内存传递，下一级直接使用） |

---

## 2. 核心挑战分析

### 挑战A — 合成bar不在DB中

**问题描述**:

60分钟的合成bar通过 `_sync_to_display()` 写入 parquet，不写入 DB。当 `_sync_to_display()` 为日线执行时，它查询 DB 的60分钟数据（`WHERE ts <= cutoff_date`），找不到合成bar。

**解决方案对比**:

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| A1 — 临时写入DB | 合成后 INSERT，完成级联后 DELETE | 所有代码走统一DB查询路径 | 污染DB；需要清理逻辑；并发风险 |
| A2 — 内存传递 | 按TF顺序处理，每级合成bar保存在内存dict中，传给下一级 | 零DB污染；天然适配级联；简单可靠 | 需要重构为集中式预处理函数 |
| A3 — 扩大查询范围 | 查询时把合成bar覆盖的时间段也查进去 | 改动最小 | 查询条件复杂；可能重复数据；边界难处理 |

**推荐: A2 — 内存传递**

理由:
- 级联合成本质上是一个**有序的预处理流水线**，天然适合集中处理
- 不污染 DB，无清理逻辑，无并发问题
- 当前架构中 `_sync_to_display()` 已经是每个视图独立调用的模式，改为集中预处理可以减少重复查询
- 内存开销极小: 最多 8 个 DataFrame，每个约 120 行

**实现方式**: 新增 `_sync_all_cascading()` 函数，在 `main()` 中所有 fragment 渲染之前调用一次。函数内部按 TF_HIERARCHY 顺序处理，维护一个 `synthesized_data: Dict[str, pd.DataFrame]` 字典:

```python
synthesized_data = {}  # tf → 该TF的完整DataFrame(含合成bar)

for tf in processing_order:  # 从min_tf到max_tf
    db_rows = query_db(ticker, tf, cutoff_date, n_pts)
    finer_data = synthesized_data.get(finer_tf)  # 上一级的数据
    
    if needs_synthesis(tf, db_rows, cutoff_date):
        synth_bar = synthesize(tf, finer_data, db_rows, cutoff_date)
        combined = combine(db_rows, synth_bar)
    else:
        combined = db_rows
    
    write_parquet(tf, combined, n_pts)
    synthesized_data[tf] = combined  # 传给下一级
```

### 挑战B — 合成bar的时间戳

**问题描述**:

```
60分钟的合成bar ts="2026-07-03T15:00:00"（标注周期结束时间）
cutoff_date="2026-07-03T14:45:00"（当前时刻）
日线合成查询60分钟时用 ts <= cutoff_date，查不到ts=15:00的合成bar
```

**分析**:

这是 A2（内存传递）方案下自动解决的问题。因为日线合成不查询 DB 的60分钟数据，而是直接使用 `synthesized_data["60分钟"]` 这个完整 DataFrame。该 DataFrame 已经包含了合成bar（时间戳为周期结束时间），日线合成只需要过滤出时间范围 `[period_start, cutoff_date]` 内的60分钟bar即可。

**合成bar的时间戳策略**: 统一使用**周期结束时间** `next_end`（如 "14:45之后的60分钟周期的结束时间 → 15:00"）。这确保了:
- 合成bar在时间轴上位于正确位置（在最后一个完成bar之后）
- 下一级合成可以正确过滤时间范围（用 `ts <= cutoff_date` 虽然找不到它，但我们走内存传递）
- 显示时时间轴连续

**过滤条件**: 当从 `synthesized_data[finer_tf]` 中选取用于合成的数据时，使用:
```python
finer_df = synthesized_data[finer_tf]
finer_df = finer_df[(finer_df["Date"] >= period_start) & (finer_df["Date"] <= cutoff_date)]
```

注意: 虽然合成bar的 ts 可能 > cutoff_date，但在 A2 方案下，这没关系——我们按 period_start/cutoff_date 做时间范围过滤，合成bar的 ts（= period_end）可能落在范围内，也可能不。实际过滤逻辑是:
```python
# 选取 period_start 到 cutoff_date 之间的所有 bar（含合成bar）
mask = (finer_df["Date"] >= period_start) & (finer_df["Date"] <= cutoff_date)
```

如果合成bar的 ts = next_end（比如15:00），而 cutoff_date=14:45，那么 `ts <= 14:45` 为 False，合成bar不会被纳入。

**但实际上这就是正确的行为**: 合成60分钟bar代表整个60分钟周期的聚合（如14:00-15:00），如果 cutoff 在14:45，我们用于日线合成的应该是14:00-14:45之间的实际60分钟数据（如果有的话），而不是整个周期的聚合。

不过更正确的理解是: 60分钟的合成bar是14:00-14:45之间的数据聚合（因为它是从15分钟数据合成的，而15分钟数据也截止于14:45）。所以60分钟合成bar代表的是14:00-14:45这一段。

**修正策略**: 合成bar的 ts 使用 **`cutoff_date`** 而非 `next_end`。原因:
1. 合成bar代表的是一个不完整周期（从 last_completed_ts 到 cutoff_date）
2. 使用 cutoff_date 作为 ts 意味着它排在最后一个已完成bar之后、cutoff_date 时刻之前
3. 下一级合成过滤时自然能找到它（因为 ts <= cutoff_date）

```python
synthesized_bar = {
    "Date": cutoff_date,  # 不是 next_end
    "Open": ...,
    "High": ...,
    "Low": ...,
    "Close": ...,
    "Volume": ...,
}
```

但这样会有一个显示问题: 如果原始60分钟bar的ts是整点（10:00, 11:00...），合成bar的ts却是一个非整点时间，在图表上会看起来奇怪。

**最终决策**: 使用双重时间戳策略:
- `ts` = `cutoff_date`（用于排序和过滤，确保在截止时间之前）
- 可选: 在Date列添加一个标记字段或使用特殊格式表示这是合成bar

简化实现: 直接使用 `cutoff_date` 作为合成bar的 `Date`。在显示端（`_load_chart_data()` -> `_render_chart()`），时间标签正常显示，无需特殊处理。

### 挑战C — 处理顺序

**问题描述**:

`_sync_to_display()` 当前在每个视图的 `_load_chart_data()` 中被调用。4个视图各自调用一次，每次独立查询和写入。Streamlit 的 `st.fragment` 使4个视图并行渲染，无法保证处理顺序。

**解决方案**: 前置集中处理

在 `main()` 中，**在所有 fragment 渲染之前**调用一次 `_sync_all_cascading()`:

```python
# main() 中（在 sorted_views 循环之前）
if cb_mode:
    _sync_all_cascading(ticker_code, configs, cutoff_date, min_tf)
```

然后在 `_load_chart_data()` 中，回测模式下**直接读取 parquet**，不再调用 `_sync_to_display()`:

```python
# _load_chart_data() 中
if window_start is not None:
    # 回测模式: 假设 parquet 已由 _sync_all_cascading() 准备好
    # 直接读取，不再调用 _sync_to_display()
    pass  # 直接走读取 parquet 路径
```

**为什么这样设计**: 
- 保证顺序: `_sync_all_cascading()` 在单线程中按 TF 顺序执行
- 避免重复: 每个 TF 只处理一次，而非每个视图一次
- 兼容 fragment: 预处理在所有 fragment 之前完成

**需要处理的特殊情况**: 如果4个视图使用相同的 TF（比如两个视图都用日线），只需要对该 TF 合成一次。

```python
# 提取所有视图中涉及的不同 TF
unique_tfs = sorted(
    set(cfg["tf"] for cfg in configs),
    key=lambda tf: ALL_TFS.index(tf),  # 从细到粗排序
)
```

### 挑战D — 数据窗口 vs 合成窗口

**问题描述**:

日线显示需要120条日线bar，但合成1条日线bar需要的60分钟数据可能远少于120条（只需要当天的不完整60分钟数据）。这两个窗口是不同的概念。

**分析**:

- **显示窗口**: `n_pts` 条bar，用于渲染图表
- **合成窗口**: 从 `last_completed_ts + 1` 到 `cutoff_date` 之间的数据，用于合成1条不完整bar

这两个窗口互不影响:
- DB 查询返回 `n_pts + buffer` 条（或直接 `n_pts` 条），用于显示
- 合成只需要最近的部分数据

具体地说，对于每个 TF:
1. 从 DB 查询最后 `n_pts` 条已完成bar → 用于显示
2. 从更细 TF 的数据中选取 `[period_start, cutoff_date]` 范围 → 用于合成1条不完整bar
3. 合成bar追加到第1步的数据末尾 → 写入 parquet

`n_pts` 不需要因为合成而增大，因为:
- 合成只需要最新的一小段数据
- 历史数据（已完成的bar）已经在 DB 中，不需要合成

**唯一例外**: 当 `last_completed_ts` 到 `cutoff_date` 之间的时间跨度很大（如跨周末），可能需要多天的细粒度数据来合成。但这不是窗口大小问题，而是数据完整性检查问题。

---

## 3. 方案设计

### 3.1 新增函数 `_sync_all_cascading()`

**位置**: `filter_app/services/data_loader.py`

**签名**:

```python
def _sync_all_cascading(
    ticker_code: str,
    tfs: list[str],          # 需要处理的 TF 列表（去重，已排序从细到粗）
    cutoff_date: str,        # ISO格式截止日期
    min_tf: str,             # 最细周期（用于判断是否需要合成）
    n_pts: int = 120,        # 每个 TF 的显示窗口大小
) -> dict[str, bool]:
    """级联合成所有 TF 的数据并写入 parquet。

    按 TF_HIERARCHY 顺序从 min_tf 到最粗 TF 逐级处理。
    每级的合成使用紧邻更细一级的完整数据（含该级自己的合成bar）。

    返回: {tf: success} 字典，标识每个 TF 是否成功写入 parquet。
    """
```

**处理流程伪代码**:

```python
def _sync_all_cascading(ticker_code, tfs, cutoff_date, min_tf, n_pts=120):
    """
    Step 1: 确定处理顺序
    ─────────────────────
    从 ALL_TFS 中筛选出需要处理的 TF，按索引从小到大排序（细→粗）。
    只处理在 tfs 列表中出现的 TF。
    """

    # 从细到粗排序
    processing_order = sorted(tfs, key=lambda tf: ALL_TFS.index(tf))
    results = {}

    # 上一级数据缓存（用于下一级合成）
    prev_data = None   # DataFrame | None
    prev_tf = None     # str | None

    """
    Step 2: 逐级处理
    ─────────────────
    """
    for tf in processing_order:
        # ── 2a. 查询DB: 取该TF的已完成bar ──
        db_rows = _query_tf_from_db(ticker_code, tf, cutoff_date, n_pts)

        if db_rows is None or len(db_rows) == 0:
            results[tf] = False
            prev_data = None
            prev_tf = tf
            continue

        # ── 2b. 判断是否需要合成 ──
        needs_synth = (
            ALL_TFS.index(tf) > ALL_TFS.index(min_tf)
            and prev_data is not None
            and len(prev_data) > 0
        )

        synthesized_bar = None
        if needs_synth:
            # ── 2c. 从上一级数据中合成不完整bar ──
            last_completed_ts = db_rows[0]["Date"]  # 最近一条已完成bar的ts
            next_end = _get_period_end(last_completed_ts, tf)

            if pd.Timestamp(cutoff_date) < next_end:
                # 当前周期不完整，需要合成
                period_start = _get_period_start(last_completed_ts, tf)

                # 从 prev_data（上一级的完整DataFrame）中选取
                # [period_start, cutoff_date] 范围内的bar
                prev_df = prev_data.copy()
                prev_df["Date"] = pd.to_datetime(prev_df["Date"])
                mask = (prev_df["Date"] >= period_start) & \
                       (prev_df["Date"] <= cutoff_date)
                finer_bars = prev_df[mask]

                if len(finer_bars) > 0:
                    synthesized_bar = _aggregate_bars(finer_bars, tf)
        else:
            # ── 2d. 无需合成: tf == min_tf 或 prev_data 不可用 ──
            # min_tf 不做合成（没有更细的数据源）
            pass

        # ── 2e. 构建完整DataFrame并写入parquet ──
        combined = _build_combined_df(db_rows, synthesized_bar, n_pts)
        ok = _write_parquet(tf, combined)

        results[tf] = ok

        # ── 2f. 保存当前TF的数据供下一级使用 ──
        if ok:
            prev_data = combined
            prev_tf = tf
        else:
            prev_data = None  # 写入失败则下一级无法合成

    return results
```

**辅助函数**:

```python
def _query_tf_from_db(ticker_code, tf, cutoff_date, n_pts):
    """查询指定TF在cutoff_date之前的最后n_pts条已完成bar。
    返回 list[dict] 或 None。每条dict有 Date/Open/High/Low/Close/Volume 键。
    """
    from db import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT ts, open, high, low, close, volume
               FROM kline WHERE ticker=? AND timeframe=? AND ts <= ?
               ORDER BY ts DESC LIMIT ?""",
            (ticker_code, tf, cutoff_date, n_pts),
        ).fetchall()
    if not rows:
        return None
    # DESC → ASC（时间升序）
    return [
        {"Date": r[0], "Open": r[1], "High": r[2],
         "Low": r[3], "Close": r[4], "Volume": r[5]}
        for r in reversed(rows)
    ]


def _aggregate_bars(finer_bars, target_tf):
    """从更细TF的bar列表聚合成一条目标TF的bar。
    
    finer_bars: pd.DataFrame, 含 Date/Open/High/Low/Close/Volume 列
    target_tf: 目标周期（仅用于日志）
    
    返回 dict 或 None。
    
    聚合规则（对所有TF通用）:
      Open  = 第一条bar的Open
      High  = 所有bar的High的最大值
      Low   = 所有bar的Low的最小值
      Close = 最后一条bar的Close
      Volume = 所有bar的Volume之和
    """
    if len(finer_bars) == 0:
        return None
    
    return {
        "Date": finer_bars["Date"].iloc[-1],  # 使用最后一条bar的ts
        "Open": float(finer_bars["Open"].iloc[0]),
        "High": float(finer_bars["High"].max()),
        "Low": float(finer_bars["Low"].min()),
        "Close": float(finer_bars["Close"].iloc[-1]),
        "Volume": float(finer_bars["Volume"].sum()),
    }


def _build_combined_df(db_rows, synthesized_bar, n_pts):
    """将DB已完成bar + 合成bar合并为DataFrame，截断到n_pts。
    返回 pd.DataFrame。
    """
    import pandas as pd
    
    data = list(db_rows)  # 已经是 list[dict]
    
    if synthesized_bar is not None:
        data.append(synthesized_bar)
    
    df = pd.DataFrame(data)
    if len(df) > n_pts:
        df = df.iloc[-n_pts:]  # 保留最后 n_pts 条
    
    df = df.reset_index(drop=True)
    return df


def _write_parquet(tf, df):
    """将DataFrame写入对应TF的parquet文件。返回 bool。"""
    from pathlib import Path
    display_dir = Path(__file__).parent.parent.parent / "data" / "display"
    display_dir.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(display_dir / f"{tf}.parquet", index=False)
        return True
    except Exception:
        return False
```

### 3.2 修改 `_load_chart_data()`

**当前逻辑** (`streamlit_app.py:115-172`):

```python
def _load_chart_data(market, ticker_code, tf, day_offset, n_pts,
                     window_start=None, cutoff_date=None):
    if window_start is not None:
        # 回测模式: 调用 _sync_to_display()，然后读parquet
        ok, count = _sync_to_display(ticker_code, tf, n_pts=n_pts,
                                      cutoff_date=cutoff_date)
        if not ok:
            return _cached_fetch_stock(...)  # API回退
    else:
        # 浏览模式: 调用 _sync_to_display()，然后读parquet
        ok, count = _sync_to_display(ticker_code, tf, day_offset=day_offset,
                                      n_pts=n_pts)
        if not ok:
            return _cached_fetch_stock(...)

    # 读parquet
    df = pd.read_parquet(display_path)
    ...
```

**修改后逻辑**:

```python
def _load_chart_data(market, ticker_code, tf, day_offset, n_pts,
                     window_start=None, cutoff_date=None):
    if window_start is not None:
        # ★ 回测模式: 直接读parquet（已由 _sync_all_cascading() 预处理）
        # 不再调用 _sync_to_display()
        pass  # 直接跳转到下面的 parquet 读取
    else:
        # 浏览模式: 保持原有逻辑不变
        ok, count = _sync_to_display(ticker_code, tf, day_offset=day_offset,
                                      n_pts=n_pts)
        if not ok:
            return _cached_fetch_stock(market, ticker_code, tf, n_pts)

    # 读parquet（共用的读取逻辑）
    display_path = Path(__file__).parent.parent / "data" / "display" / f"{tf}.parquet"
    err = None
    if display_path.exists():
        try:
            df = pd.read_parquet(display_path)
            # ... 现有处理逻辑保持不变 ...
        except Exception as e:
            err = str(e)

    if err is not None:
        return None, None, None, None, None, err
    return _cached_fetch_stock(market, ticker_code, tf, n_pts)
```

**关键变化**:
- 回测模式下移除 `_sync_to_display()` 调用
- 浏览模式下 `_sync_to_display()` 保持不变
- parquet 读取逻辑从 if/else 分支中提取出来成为共用路径

### 3.3 合成bar的临时存储策略

**选择: A2 — 内存传递**

决策理由汇总:
1. **零DB污染**: 合成bar是临时的、特定于当前回测位置的，不应写入持久化存储
2. **天然适配级联**: 每级的 DataFrame 自然传给下一级
3. **无生命周期管理**: 不需要 INSERT/DELETE 配对，函数返回即释放
4. **简单可靠**: 无并发问题，无事务边界问题
5. **内存开销可忽略**: 每个 DataFrame 约 120 行 × 6 列 × 8 bytes ≈ 6KB，8个TF总共 < 50KB

**数据传递结构**:

```
_sync_all_cascading() 内部:
  prev_data: pd.DataFrame = None  # 上一级的完整数据

  for tf in processing_order:
      db_rows = query_db(tf)
      if prev_data is not None and needs_synthesis(tf):
          # 从 prev_data 中选取数据来合成
          synth_bar = aggregate(prev_data, ...)
      combined = db_rows + synth_bar
      write_parquet(tf, combined)
      prev_data = combined  # ← 传给下一级
```

**为什么 prev_data 是完整 DataFrame 而非仅合成bar?**

因为下一级合成时，需要的不只是上一级的合成bar，而是所有在 `[period_start, cutoff_date]` 范围内的上一级bar。例如日线合成时需要当天所有的60分钟bar（不仅是合成的那个），所以需要完整的60分钟DataFrame来做时间范围过滤。

### 3.4 日线/周线/月线/季线的合成规则

#### 3.4.1 日线从60分钟合成

**数据源**: 60分钟的完整数据（含其合成bar）

**需要哪些60分钟bar**: 
- 从 `last_completed_daily_ts + 1天` 到 `cutoff_date` 之间的所有60分钟bar
- last_completed_daily_ts 是 DB 中最后一条已完成日线bar的 ts

**交易时段考虑**:
- 美股: 60分钟bar可能覆盖 extended hours。如果 DB 中的60分钟数据不含盘前/盘后，则合成日线时会自然缺失这些时段
- A股: 60分钟bar时间固定（10:00, 11:00, 11:30, 14:00, 15:00），间隙较大
- 当前不做特殊处理: 有什么bar用什么bar合成，缺失的时段自然反映为日线bar数据不完整

**聚合规则**（与所有其他TF对一致）:
```
Open  = 第一个60分钟bar的Open
High  = 所有60分钟bar的High的最大值
Low   = 所有60分钟bar的Low的最小值
Close = 最后一个60分钟bar的Close
Volume = 所有60分钟bar的Volume之和
```

#### 3.4.2 周线从日线合成

**数据源**: 日线的完整数据（含其合成bar）

**需要哪些日线bar**: 
- 从 `last_completed_weekly_ts + 1天` 到 `cutoff_date` 之间的所有日线bar
- 通常覆盖周一到周五（或到 cutoff_date 所在的交易日）

**聚合规则**: 同上（O=第一个的O, H=max, L=min, C=最后一个的C, V=sum）

**边界情况**: 
- 周一没有前一个周五的数据（正常，不用管）
- 这周只有周一到周三的数据（正常，合成的周线bar就是不完整的）

#### 3.4.3 月线从日线合成

**数据源**: 日线的完整数据（含其合成bar）

**需要哪些日线bar**:
- 从 `last_completed_monthly_ts + 1天`（即下月1日）到 `cutoff_date` 之间的所有日线bar

**聚合规则**: 同上

#### 3.4.4 季线从月线合成

**数据源**: 月线的完整数据（含其合成bar）

**需要哪些月线bar**:
- 从 `last_completed_quarterly_ts + 1月` 到 `cutoff_date` 之间的所有月线bar
- 每季度3个月

**聚合规则**: 同上

#### 3.4.5 聚合规则统一性

所有 TF 对的聚合规则完全一致:

```python
O = bars[0].Open      # 第一条bar的开盘价
H = max(bars.High)    # 周期内最高价
L = min(bars.Low)     # 周期内最低价
C = bars[-1].Close    # 最后一条bar的收盘价
V = sum(bars.Volume)  # 周期内总成交量
```

这是 OHLC 标准聚合逻辑，对所有周期（分钟/日/周/月/季）普遍适用，无需区分。

---

## 4. 实现细节

### 4.1 合成查询窗口计算

当前 `_get_period_end()` 和 `_get_period_start()` 已在 `feature/backtest-bar-synthesis` 分支实现，需要**扩展**以支持日线及以上周期。

**完整实现**:

```python
def _get_period_end(ts, tf):
    """计算给定 ts 所在周期的结束时间戳。
    
    参数:
        ts: pd.Timestamp 或时间字符串 — 通常是最后一条已完成bar的ts
        tf: 周期名称
    
    返回:
        pd.Timestamp — 当前周期的结束时间（ts之后的第一个周期边界）
    """
    ts = pd.Timestamp(ts)
    
    if tf == "5分钟":
        current_minutes = ts.hour * 60 + ts.minute
        next_boundary = ((current_minutes // 5) + 1) * 5
        next_hour = next_boundary // 60
        next_minute = next_boundary % 60
        if next_hour >= 24:
            return (ts + pd.Timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0)
        return ts.replace(hour=next_hour, minute=next_minute,
                          second=0, microsecond=0)
    
    elif tf == "15分钟":
        current_minutes = ts.hour * 60 + ts.minute
        next_boundary = ((current_minutes // 15) + 1) * 15
        next_hour = next_boundary // 60
        next_minute = next_boundary % 60
        if next_hour >= 24:
            return (ts + pd.Timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0)
        return ts.replace(hour=next_hour, minute=next_minute,
                          second=0, microsecond=0)
    
    elif tf == "60分钟":
        return (ts + pd.Timedelta(hours=1)).replace(
            minute=0, second=0, microsecond=0)
    
    elif tf == "日线":
        return (ts + pd.Timedelta(days=1)).normalize()
    
    elif tf == "周线":
        # 周五之后的下一个周五（或标注为周结束的日期）
        days_ahead = 4 - ts.weekday()  # weekday()=4是周五
        if days_ahead <= 0:
            days_ahead += 7
        return (ts + pd.Timedelta(days=days_ahead)).normalize()
    
    elif tf == "月线":
        if ts.month == 12:
            return pd.Timestamp(year=ts.year + 1, month=1, day=1) \
                   - pd.Timedelta(days=1)
        else:
            return pd.Timestamp(year=ts.year, month=ts.month + 1, day=1) \
                   - pd.Timedelta(days=1)
    
    elif tf == "季线":
        q_end_month = ((ts.month - 1) // 3 + 1) * 3
        if q_end_month == 12:
            return pd.Timestamp(year=ts.year + 1, month=1, day=1) \
                   - pd.Timedelta(days=1)
        else:
            return pd.Timestamp(year=ts.year, month=q_end_month + 1, day=1) \
                   - pd.Timedelta(days=1)
    
    else:
        return ts  # 1分钟或其他无法计算周期的


def _get_period_start(last_completed_ts, tf):
    """计算不完整bar的数据查询起点。
    
    返回从什么时候开始查询更细粒度数据来合成目标TF的bar。
    """
    ts = pd.Timestamp(last_completed_ts)
    
    if tf in ("5分钟", "15分钟", "60分钟"):
        return (ts + pd.Timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")
    elif tf == "日线":
        return (ts + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    elif tf == "周线":
        return (ts + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    elif tf == "月线":
        if ts.month == 12:
            start = pd.Timestamp(year=ts.year + 1, month=1, day=1)
        else:
            start = pd.Timestamp(year=ts.year, month=ts.month + 1, day=1)
        return start.strftime("%Y-%m-%d")
    elif tf == "季线":
        next_q_start_month = ((ts.month - 1) // 3 + 1) * 3 + 1
        if next_q_start_month > 12:
            start = pd.Timestamp(year=ts.year + 1, month=1, day=1)
        else:
            start = pd.Timestamp(year=ts.year, month=next_q_start_month, day=1)
        return start.strftime("%Y-%m-%d")
    else:
        return (ts + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
```

**合成判断的简化逻辑**:

```python
def _needs_synthesis(tf, db_rows, cutoff_date, min_tf):
    """判断是否需要为给定TF合成不完整bar。"""
    # min_tf本身不需要合成（没有更细的数据源）
    if tf == min_tf:
        return False
    
    if not db_rows or len(db_rows) == 0:
        return False
    
    last_completed_ts = db_rows[-1]["Date"]  # 升序的最后一条
    next_end = _get_period_end(last_completed_ts, tf)
    cutoff_dt = pd.Timestamp(cutoff_date)
    
    # 如果截止时间在当前周期结束之前 → 需要合成
    return cutoff_dt < next_end
```

### 4.2 边界条件

#### 4.2.1 min_tf不是分钟级

**场景**: 4个视图配置为日线、周线、月线、季线（无分钟数据）

**处理**: `min_tf` = "日线"。处理顺序: 日线 → 周线 → 月线 → 季线。
- 日线: `tf == min_tf`，不合成（DB查询即可）
- 周线: 从日线数据合成
- 月线: 从日线数据合成（跳过周线中的合成bar）
- 季线: 从月线数据合成

这是**天然支持的**，因为级联合成不依赖分钟数据的存在。

#### 4.2.2 刚开盘数据很少

**场景**: 刚开盘10分钟，15分钟周期只有1条完成bar

**处理**:
- 15分钟的 DB 查询返回 1 条 bar
- 60分钟合成: `last_completed_ts` 是9:45（或类似值），`next_end` 是10:00，`cutoff_date` 是9:40
- `cutoff_dt (9:40) < next_end (10:00)` → 需要合成
- 从15分钟数据中选取 `period_start` 到 `cutoff_date` 的数据: 可能为空（没有15分钟bar落在这个范围）
- 如果为空 → `synthesized_bar = None`，不合成

**合成失败的降级策略**: 不合成，使用 DB 中已有的 bar（可能少一条）。图表显示会缺少最新的不完整bar，但不会报错。

#### 4.2.3 跨周末/节假日

**场景**: 周五收盘后到周一开盘前没有交易数据

**处理**:
- 对于分钟级TF: DB 中没有周末数据，`_get_period_end` 计算不受影响
- 对于日线: 周五之后的下一个日线周期结束是周一（或下个交易日），查询日线数据时 DB 自然没有周六/周日的 bar
- 对于周线: 周五是一个周结束，`_get_period_end` 返回下一个周五

**关键**: 不需要特殊处理。DB 中没有周末/节假日的数据，合成时自然查询不到这些时段的 bar。合成结果反映实际可用的交易数据。

#### 4.2.4 某个TF的DB数据完全为空

**场景**: ticker 没有60分钟数据（yfinance 不提供此周期）

**处理**: 
- `_query_tf_from_db()` 返回 None
- `prev_data` 设为 None
- 更粗 TF 跳过合成（因为 `prev_data is None`）
- parquet 写入空 DataFrame → `_load_chart_data()` 回退到 API

#### 4.2.5 cutoff_date 恰好等于周期边界

**场景**: cutoff_date = "2026-07-03T15:00:00"，60分钟周期正好结束

**处理**: `cutoff_dt < next_end (15:00)` → False（相等不算 <）。不合成。DB 中应该已有这条完成的60分钟bar。

#### 4.2.6 cutoff_date 在最后一个完成bar之前

**场景**: cutoff_date 对应的时间点，在该TF的DB中已有大量已完成bar

**处理**: 正常。`_query_tf_from_db()` 用 `ts <= cutoff_date` 查询，能正确返回。`_needs_synthesis()` 的 `last_completed_ts` 是查询结果中最后一条bar的ts。如果 `last_completed_ts` 的 `_get_period_end` 仍然 > cutoff_date → 合成。如果 ≤ cutoff_date → 不合成。

### 4.3 Parquet写入顺序

**顺序**: 从细到粗（15分钟 → 60分钟 → 日线 → 周线 → ...）

这由 `processing_order` 的排序保证（`sorted(tfs, key=lambda tf: ALL_TFS.index(tf))`）。

**写入时机**: 每个 TF 处理完立即写入，不等全部完成。

**为什么不等全部完成再写**:
- 如果中间某级失败（如合成失败），前面的结果仍然可用
- Streamlit fragment 在 rerun 后会重新读取 parquet，提前写入能更快响应

**写入完成后通知**:
- 在 `main()` 中 `_sync_all_cascading()` 返回后，`st.rerun()` 自然触发 fragment 重新渲染
- Fragment 内部的 `_load_chart_data()` 读取最新 parquet
- 无需显式通知机制

---

## 5. ASCII数据流图

### 5.1 级联合成全景

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        main() — 回测模式入口                                  │
│                                                                             │
│  window_start = st.session_state._bar_index      # slider位置               │
│  cutoff_date  = AppState.get("_bt_cutoff_date")  # 截止日期                  │
│  min_tf       = AppState.get("_min_tf")          # 最细周期                  │
│                                                                             │
│  configs = [                                                               │
│    {"tf": "15分钟", "n_pts": 120},  # 视图1                                 │
│    {"tf": "60分钟", "n_pts": 120},  # 视图2                                 │
│    {"tf": "日线",   "n_pts": 120},  # 视图3                                 │
│    {"tf": "周线",   "n_pts": 120},  # 视图4                                 │
│  ]                                                                          │
│                                                                             │
│  unique_tfs = {"15分钟", "60分钟", "日线", "周线"}                            │
│  processing_order = ["15分钟", "60分钟", "日线", "周线"]  # 细→粗             │
│                                                                             │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    _sync_all_cascading(ticker, tfs, cutoff, min_tf)          │
│                                                                             │
│  prev_data = None                                                           │
│  results   = {}                                                             │
│                                                                             │
│  ┌─ for tf in processing_order ───────────────────────────────────────┐     │
│  │                                                                     │     │
│  │  ┌──────────────┐    ┌─────────────────────┐                       │     │
│  │  │ ① DB 查询     │    │ ② 上一级数据         │                       │     │
│  │  │              │    │  (prev_data)         │                       │     │
│  │  │ SELECT ...   │    │                      │                       │     │
│  │  │ FROM kline   │    │ prev_tf=15分钟时:    │                       │     │
│  │  │ WHERE ts <=  │    │   prev_data=None     │                       │     │
│  │  │   cutoff     │    │   (15分钟=min_tf,    │                       │     │
│  │  │ ORDER BY ts  │    │    无更细数据源)      │                       │     │
│  │  │ DESC LIMIT N │    │                      │                       │     │
│  │  └──────┬───────┘    └──────────┬──────────┘                       │     │
│  │         │                       │                                   │     │
│  │         │  db_rows              │  finer_df                         │     │
│  │         │  (list[dict])         │  (pd.DataFrame | None)            │     │
│  │         │                       │                                   │     │
│  │         └───────────┬───────────┘                                   │     │
│  │                     │                                               │     │
│  │                     ▼                                               │     │
│  │         ┌───────────────────────┐                                   │     │
│  │         │ ③ 判断是否需要合成     │                                   │     │
│  │         │                       │                                   │     │
│  │         │ needs = (tf != min_tf │                                   │     │
│  │         │   and prev_data ok    │                                   │     │
│  │         │   and cutoff <        │                                   │     │
│  │         │     next_period_end)  │                                   │     │
│  │         └───────────┬───────────┘                                   │     │
│  │                     │                                               │     │
│  │              ┌──────┴──────┐                                        │     │
│  │              │             │                                        │     │
│  │           YES            NO                                          │     │
│  │              │             │                                        │     │
│  │              ▼             │                                        │     │
│  │  ┌─────────────────────┐  │                                        │     │
│  │  │ ④ 合成不完整bar       │  │                                        │     │
│  │  │                     │  │                                        │     │
│  │  │ period_start =      │  │                                        │     │
│  │  │  _get_period_start  │  │                                        │     │
│  │  │  (last_completed,   │  │                                        │     │
│  │  │   tf)               │  │                                        │     │
│  │  │                     │  │                                        │     │
│  │  │ finer_bars =        │  │                                        │     │
│  │  │  prev_data[         │  │                                        │     │
│  │  │   (Date >= start) & │  │                                        │     │
│  │  │   (Date <= cutoff)  │  │                                        │     │
│  │  │  ]                  │  │                                        │     │
│  │  │                     │  │                                        │     │
│  │  │ synth_bar =         │  │                                        │     │
│  │  │  _aggregate_bars(   │  │                                        │     │
│  │  │   finer_bars)       │  │                                        │     │
│  │  └─────────┬───────────┘  │                                        │     │
│  │            │              │                                        │     │
│  │            └──────┬───────┘                                        │     │
│  │                   │                                                 │     │
│  │                   ▼                                                 │     │
│  │  ┌────────────────────────────┐                                    │     │
│  │  │ ⑤ 构建DataFrame & 写parquet │                                    │     │
│  │  │                            │                                    │     │
│  │  │ combined = db_rows         │                                    │     │
│  │  │   + [synth_bar] (if any)   │                                    │     │
│  │  │ truncated = combined[-N:]  │                                    │     │
│  │  │                            │                                    │     │
│  │  │ → data/display/{tf}.parquet│                                    │     │
│  │  └────────────┬───────────────┘                                    │     │
│  │               │                                                     │     │
│  │               ▼                                                     │     │
│  │  ┌────────────────────────────┐                                    │     │
│  │  │ ⑥ 保存数据供下一级使用       │                                    │     │
│  │  │                            │                                    │     │
│  │  │ prev_data = combined_df    │──── 传递给下一个 tf ────►           │     │
│  │  │ prev_tf   = tf             │                                    │     │
│  │  └────────────────────────────┘                                    │     │
│  └─────────────────────────────────────────────────────────────────────┘     │
│                                                                             │
│  return results  # {"15分钟": True, "60分钟": True, ...}                     │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 5.2 级联数据传递示意（具体数据）

```
min_tf = "15分钟"
cutoff_date = "2026-07-03T14:47:00"

═══════════════════════════════════════════════════════════════════
 第1级: 15分钟 (min_tf, 不合成)
═══════════════════════════════════════════════════════════════════

DB查询 (WHERE ts <= "14:47", DESC LIMIT 120):
  ...  [历史120条已完成15分钟bar] ...

prev_data = None
→ 不合成 (tf == min_tf)

写入 15分钟.parquet: 120条已完成bar
prev_data = DataFrame(120行)  ──────────────────────┐
                                                     │
══════════════════════════════════════════════════════│═════════
 第2级: 60分钟                                        │
══════════════════════════════════════════════════════│═════════
                                                     │
DB查询 (WHERE ts <= "14:47"):                         │
  ...  [历史 60分钟 bar] ...                          │
  {ts:"2026-07-03T14:00:00", O:..., H:..., ...}      │
  ← last_completed_ts = "14:00"                       │
                                                     │
prev_data = 15分钟的DataFrame ◄───────────────────────┘
  → next_end = _get_period_end("14:00", "60分钟") = "15:00"
  → cutoff_dt("14:47") < next_end("15:00")? YES → 需要合成
  
  从 prev_data 中选取:
    period_start = _get_period_start("14:00", "60分钟") = "14:01"
    过滤: prev_data[Date >= "14:01" AND Date <= "14:47"]
    → {14:00, 14:15, 14:30, 14:45} 的15分钟bar (4条)
  
  合成:
    O = 14:00的Open
    H = max(14:00H, 14:15H, 14:30H, 14:45H)
    L = min(...)
    C = 14:45的Close
    V = sum(...)
    Date = "14:47" (cutoff_date)

combined = DB_rows + synth_bar
写入 60分钟.parquet: 120条(含合成bar)
prev_data = DataFrame(121行, 含合成bar) ────────────┐
                                                     │
══════════════════════════════════════════════════════│═════════
 第3级: 日线                                          │
══════════════════════════════════════════════════════│═════════
                                                     │
DB查询 (WHERE ts <= "14:47"):                         │
  ...  [历史 日线 bar] ...                            │
  {ts:"2026-07-02T00:00:00", ...}  ← 昨天的日线       │
  last_completed_ts = "2026-07-02"                    │
                                                     │
prev_data = 60分钟的DataFrame ◄──────────────────────┘
  → next_end = _get_period_end("07-02", "日线") = "07-03"
  → cutoff_dt("07-03T14:47") < next_end("07-03")? NO
     (07-03T14:47 不是 < 07-03T00:00)
  
  等等，不对。_get_period_end 返回的是 "2026-07-03T00:00:00"
  cutoff_dt = "2026-07-03T14:47:00"
  14:47 < 00:00? NO → 不需要合成？不对！

修正:
  _get_period_end("2026-07-02", "日线") = 
    2026-07-02 + 1天 = 2026-07-03T00:00:00
  
  cutoff_dt = 2026-07-03T14:47:00
  2026-07-03T14:47 < 2026-07-03T00:00? NO
  
  但 DB 中没有 7月3日的日线！last_completed_ts 是 7月2日。
  因为 7月3日还没结束，DB 中没有这一天的日线bar。

修正逻辑:
  cutoff_dt(14:47) 的日期部分(07-03) > last_completed_ts的日期部分(07-02)
  → 需要合成！cutoff_date 已经进入了新的日线周期。

重新设计 _needs_synthesis 的判断条件:

对于日线及以上周期:
  last_completed_date = pd.Timestamp(last_completed_ts).date()
  cutoff_date_part = pd.Timestamp(cutoff_date).date()
  needs = cutoff_date_part > last_completed_date

但这个逻辑不适用于分钟级TF（因为分钟级在同一天内可能有多个周期）。

统一逻辑:
  使用 _get_period_end 的返回值:
    next_end = _get_period_end(last_completed_ts, tf)
    # 对于日线: next_end = 下一天的00:00
    # cutoff_date 是包含时间的完整时间戳
    # 如果 cutoff_date.date() >= next_end.date()? 需要更精确的判断
    
  更好的方式: 比较时间戳
    next_end_start = _get_period_start_ts(last_completed_ts, tf)
    # 返回的是下一个周期的开始时间戳
    # 对于60分钟: 14:01
    # 对于日线: 07-03T00:00:00
    # 对于周线: 下周一00:00
    
    判断: cutoff_dt >= next_period_start
    → DB中可能没有这个周期的数据 → 需要合成

但这又回到原来的问题了...

实际上，关键insight是:
_last_completed_ts 是 DB 中该 TF 最后一条 bar 的时间。
_period_end(last_completed_ts, tf) 返回这个 last bar 所属周期的结束时间。_

对于已完成的 bar, period_end 应该等于 bar 的 ts（因为 bar 的 ts 标注的就是周期结束时间）。但这里有个微妙之处：日线 bar 的 ts 格式是 "2026-07-02" (日期)，而 60分钟 bar 的 ts 是 "2026-07-03T14:00:00"。

所以对于日线:
  last_completed_ts = "2026-07-02" (这是7月2日)
  _get_period_end("2026-07-02", "日线") = pd.Timestamp("2026-07-02") + 1天 = "2026-07-03"
  cutoff_dt = "2026-07-03T14:47"
  
  判断: cutoff_dt >= next_end? 即 07-03T14:47 >= 07-03T00:00? YES
  → DB中可能没有07-03的日线 → 需要合成！

这个逻辑其实是: 如果 cutoff_date 已经进入了下一个周期（>= 下一个周期的结束时间点），且 DB 中只有上一个周期的数据，就可能需要合成。

但 `cutoff_dt >= next_end` 可能为 True 但 DB 中已有该周期的完成bar（如果该周期已经完整结束）。例如:
  - last_completed_ts = "07-01", next_end = "07-02"
  - DB 中有 07-02 的完成日线 bar, cutoff = "07-03T10:00"
  - 但 DB 查询的 last db row 是 07-02（因为 cutoff_dt 还没到 07-03 的结束）
  
  等等，DB 查询是 `ORDER BY ts DESC`，取的是 `ts <= cutoff_date` 的最新数据。如果 DB 中有 07-02 的完成日线 bar（ts="2026-07-02"），而 cutoff_date="2026-07-03T10:00"，那么:
  - ts="07-02" <= "07-03T10:00"? YES
  - 但 ts="07-03" (如果存在的完成bar) 也 <= "07-03T10:00"? 取决于 ts 格式
  
  如果日线 bar 的 ts 是 "2026-07-03" (只有日期)，而 cutoff_date 是 "2026-07-03T10:00:00" (有时间)，在字符串比较中:
  "2026-07-03" < "2026-07-03T10:00:00"? YES (字符串比较)
  
  所以 DB 查询能拿到 07-03 的完成日线 bar。
  
  那么就回到了判断:
  last_completed_ts = "07-03" (最新DB bar)
  next_end = "07-04"
  cutoff_dt = "07-03T10:00"
  07-03T10:00 < 07-04? YES → 不需要合成（07-03的bar已在DB中）

所以正确的判断逻辑其实是:
1. 查询 DB，获取 last_completed_ts
2. 计算 next_end = _get_period_end(last_completed_ts, tf)  
3. 如果 cutoff_dt >= next_end → 需要合成（DB中可能缺少当前周期的bar）
   如果 cutoff_dt < next_end → cutoff 还在 last bar 的周期内 → 不合成

等等，我搞混了。

重新理清:

对于 TF="日线":
- DB 中的 bars 的 ts 格式可能是 "2026-07-01", "2026-07-02", "2026-07-03"
- 查询 `ts <= cutoff` 的结果: last_completed_ts 是查询结果中 ts 最大的一条
- 如果 cutoff="2026-07-03T14:47"，DB 中可能只有到 "2026-07-02" 的 bar（因为 07-03 的日线还没完成，没有写入 DB）

等等，日线 bar 什么时候写入 DB？在 yfinance 数据获取时。日线 bar 在交易日结束后才会被 yfinance 返回。所以如果现在是 07-03 14:47（盘中），DB 中没有 07-03 的日线 bar。

`_get_period_end("2026-07-02", "日线")`:
  = pd.Timestamp("2026-07-02") + 1天
  = "2026-07-03T00:00:00"

  cutoff_dt = "2026-07-03T14:47:00"
  cutoff_dt (= 07-03 14:47) >= next_end (= 07-03 00:00)? YES
  
  所以需要合成 07-03 的不完整日线！

对于 TF="60分钟":
- DB 中的 bars 的 ts: "2026-07-03T10:00", "2026-07-03T11:00", ..., "2026-07-03T14:00"
- 查询 `ts <= cutoff("14:47")` 的结果: 最后一条是 "14:00"
- _get_period_end("14:00", "60分钟") = "15:00"
- cutoff_dt(14:47) >= next_end(15:00)? NO → 不合成... 

但等等，我们需要合成 14:00-14:47 之间的不完整60分钟bar！所以判断条件应该反过来:

正确的逻辑:
- last_completed_ts 是 DB 中最后一条 bar 的 ts
- 下一个周期开始 = _get_period_start_ts(last_completed_ts, tf)
- 下一个周期结束 = _get_period_end(last_completed_ts, tf)
- 如果 cutoff_date 落在 [下一个周期开始, 下一个周期结束) 之间 → 需要合成不完整bar

对于60分钟:
  - last_completed_ts = "14:00"
  - period_start = "14:01" (下一个60分钟周期的开始)
  - period_end = "15:00"
  - cutoff_dt = "14:47"
  - 14:47 在 [14:01, 15:00) 之间? YES → 需要合成！

对于日线:
  - last_completed_ts = "2026-07-02"
  - period_start = "2026-07-03T00:00:00" (下一天)
  - period_end = "2026-07-04T00:00:00"
  - cutoff_dt = "2026-07-03T14:47"
  - 在 [07-03, 07-04) 之间? YES → 需要合成！

对于周线:
  - last_completed_ts = "2026-07-03" (周五)
  - period_start = "07-04" (周六)
  - period_end = "07-11" (下周五)
  - cutoff_dt = "2026-07-07" (周二)
  - 在 [07-04, 07-11) 之间? YES → 需要合成！

所以统一判断条件:
```python
def _needs_synthesis(tf, db_rows, cutoff_date, min_tf):
    if tf == min_tf:
        return False
    if not db_rows:
        return False
    
    last_ts = pd.Timestamp(db_rows[-1]["Date"])
    period_end = _get_period_end(last_ts, tf)
    period_start = _get_period_start_as_ts(last_ts, tf)
    cutoff_dt = pd.Timestamp(cutoff_date)
    
    # cutoff 在 [period_start, period_end) 之间 → 需要合成
    return period_start <= cutoff_dt < period_end
```

但对于已完成的周期，`last_ts` 就是 `period_end - 1个周期`，所以 `period_start` 应该 > `last_ts`。如果 `cutoff_dt >= period_start`，说明 cutoff 进入了新周期。

简化: `return cutoff_dt >= period_start`

对于60分钟: period_start = "14:01", cutoff="14:47" → 14:47 >= 14:01? YES
对于日线: period_start = "07-03", cutoff="07-03T14:47" → YES
对于周线: period_start = "07-04", cutoff="07-07" → YES

当 cutoff 在已完成bar的周期内时: 
  last_ts = "14:00", period_start = "14:01", cutoff = "13:45" → 13:45 >= 14:01? NO → 不合成 ✓

当 DB 中已有当前周期的完成bar时:
  last_ts = "15:00" (DB已有完成的60分钟bar), period_start = "15:01", cutoff = "14:47"
  嗯，这里 last_ts 不应该是 "15:00"，因为 DB 查询 `ts <= "14:47"` 拿不到 "15:00" 的 bar。
  所以 last_ts 总是 < cutoff_date (因为查询条件)。正确。

所以判断条件简化为: `cutoff_dt >= _get_period_start_as_ts(last_ts, tf)`

OK this is getting very detailed. Let me document the final simplified logic in the design document and move on to writing it.

Actually, I realize I should just write the document now with all of this reasoning embedded. Let me structure this better. The core logic is:

```python
needs_synthesis = (tf != min_tf and prev_data_available and 
                   cutoff_dt >= period_start_of_next_cycle)
```

where `period_start_of_next_cycle` = `_get_period_start_as_ts(last_completed_ts, tf)`

Let me write the final comprehensive document now. I've been thinking enough - I need to produce the output.<｜end▁of▁thinking｜>Now I have all the information needed. Let me write the comprehensive design document.

<｜｜DSML｜｜tool_calls>
<｜｜DSML｜｜invoke name="Write">
<｜｜DSML｜｜parameter name="content" string="true"># 回测数据级联（Cascading）合成方案 — 详细设计

> **作者**: 系统架构师 | **日期**: 2026-07-05 | **版本**: v1.0
> **基于分支**: `feat/backtest-data-analysis` (HEAD: f780a39)
> **参考分支**: `feature/backtest-bar-synthesis` (已有单级合成逻辑)

---

## 目录

1. [概述与动机](#1-概述与动机)
2. [核心挑战分析](#2-核心挑战分析)
3. [方案设计](#3-方案设计)
4. [实现细节](#4-实现细节)
5. [ASCII数据流图](#5-ascii数据流图)
6. [实施建议](#6-实施建议)
7. [附录: 与现有代码的兼容性影响](#7-附录-与现有代码的兼容性影响)

---

## 1. 概述与动机

### 1.1 当前状态

`feature/backtest-bar-synthesis` 分支实现了**单级合成**: 每个粗周期 TF 独立地从 `min_tf`（最细周期）的 DB 数据合成自己不完整的 bar。

```
DB (15分钟数据) ──→ _sync_to_display("60分钟") 中合成60分钟不完整bar ──→ 60分钟.parquet
DB (15分钟数据) ──→ _sync_to_display("日线")   中合成日线不完整bar   ──→ 日线.parquet
```

**局限**:
1. 日线直接从15分钟合成: 需要查询约26条15分钟bar来合成1条日线bar，查询量大
2. 60分钟的合成bar只存在于 parquet，日线合成时无法利用（因为日线合成查询的是 DB 的15分钟数据，不是60分钟数据）
3. 周线/月线/季线完全不做合成：`feature/backtest-bar-synthesis` 分支限定 `tf in ("5分钟","15分钟","60分钟")` 才触发合成
4. 4个视图各自调用 `_sync_to_display()`，对同一 TF 可能重复查询和写入

### 1.2 目标

实现**级联（cascading）合成**: 每一级合成使用紧邻更细一级的**完整数据**（含该级自己合成的bar），逐级向上传递。

```
15分钟(DB查询) → 合成60分钟不完整bar
               → 60分钟内存DataFrame(含合成bar) → 合成日线不完整bar
                                                → 日线内存DataFrame → 合成周线
                                                                    → ...以此类推
```

**为什么是级联而非直接从 min_tf 合成?**

| 方面 | 单级合成 | 级联合成 |
|------|---------|---------|
| 日线合成数据源 | 15分钟(约26条bar) | 60分钟(约5-7条bar，含合成bar) |
| 查询效率 | 每次查询 min_tf 的细粒度数据 | 查询紧邻一级的粗粒度数据 |
| 周线合成 | 不支持（没有 min_tf 分钟数据对应一周） | 从日线合成(5条日线bar) |
| 月线/季线 | 不支持 | 从日线/月线逐级合成 |
| 合成bar复用 | 不能（合成bar不在DB中，其他TF的合成看不到它） | 能（内存传递） |
| 重复查询 | 4个视图可能对同一TF重复调用 `_sync_to_display()` | 每个TF只处理一次 |

---

## 2. 核心挑战分析

### 挑战A — 合成bar不在DB中，下一级无法查到它

**场景**: 60分钟的合成bar写入 parquet（不写入DB）。当日线需要合成不完整bar时，日线的
`_sync_to_display()` 查询 DB 的 60 分钟数据——找不到那条合成bar。

**三个解决方案对比**:

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| **A1** — 临时写入DB | 合成后 INSERT，级联完成后 DELETE | 所有代码走统一 DB 查询路径 | 污染DB；需要事务管理确保清理；并发不安全 |
| **A2** — 内存传递 | 按TF顺序处理，每级合成bar保存在内存 DataFrame 中，传给下一级 | 零DB污染；天然适配级联；简单可靠；无生命周期管理 | 需要重构为集中式预处理函数 |
| **A3** — 扩大查询范围 | 查询时计算合成bar覆盖的时间段，一并纳入查询条件 | 改动最小（只改SQL WHERE条件） | 查询条件复杂；需要识别"哪些时间段需要扩大"；边界难处理 |

**选择: A2 — 内存传递**

理由:
- 级联合成本质上是一个**有序的预处理流水线**，天然适合集中处理
- 不污染 DB，无清理逻辑，无并发问题
- 内存开销极小: 每个 TF 的 DataFrame 约 120 行 × 6 列 × 8 bytes ≈ 6KB，8个 TF 总共 < 50KB
- 顺便解决了重复查询问题（每个 TF 只查一次 DB）

### 挑战B — 合成bar的时间戳导致过滤条件不匹配

**场景**: 

```
60分钟合成bar ts = "2026-07-03T15:00:00" (周期结束时间)
cutoff_date = "2026-07-03T14:45:00" (当前时刻)
日线合成需要60分钟数据，如果用 ts <= cutoff_date 过滤，找不到 ts=15:00 的合成bar
```

**分析**:

在 A2（内存传递）方案下，日线合成不查询 DB，而是直接使用60分钟的完整 DataFrame。该 DataFrame 已包含合成bar。过滤条件改为基于时间范围而非字符串比较:

```python
# 从prev_data中选取用于合成的数据
mask = (prev_dt >= period_start_ts) & (prev_dt <= cutoff_ts)
```

**合成bar时间戳策略（决定使用 cutoff_date 而非 period_end）**:

| 选项 | ts值 | 优点 | 缺点 |
|------|------|------|------|
| 使用 period_end (15:00) | "2026-07-03T15:00:00" | 符合"这是15:00的60分钟bar"直觉 | 字符串比较 `<= "14:45"` 为 False；图表上时间超前 |
| 使用 cutoff_date (14:45) | "2026-07-03T14:45:00" | 过滤条件自然匹配；图表时间正确 | 非整点时间，与正常bar的整点时间不一致 |

**选择: 使用 cutoff_date 作为合成bar的 ts**

理由:
- 合成bar代表的是一个不完整周期的快照（截止于 cutoff_date），使用 cutoff_date 准确地表达了这一点
- 在过滤条件中自然匹配（`ts <= cutoff_date` 为 True），无需特殊处理
- 图表显示时，合成bar出现在正确的时间位置
- 在图表上，合成bar 的 x 轴位置可能在非标准时间点，但这正确地反映了它是不完整bar的事实

### 挑战C — 处理顺序在并行渲染框架下的保证

**当前架构**: `_sync_to_display()` 在每个视图的 `_load_chart_data()` 中被调用。4个视图由 `st.fragment` 包装，Streamlit 可能并行渲染它们。无法在内保证处理顺序。

**解决方案**: **前置集中处理 — 将级联合成提升到 fragment 渲染之前**

在 `main()` 中，所有 `_render_chart_fragment()` 调用之前，执行一次 `_sync_all_cascading()`:

```python
# main() — 在 sorted_views 循环之前（streamlit_app.py 约1621行）
if cb_mode and ticker_code:
    unique_tfs = sorted(
        set(cfg["tf"] for cfg in configs),
        key=lambda tf: ALL_TFS.index(tf)
    )
    _sync_all_cascading(ticker_code, unique_tfs, cutoff_date, min_tf, n_pts)
```

然后在 `_load_chart_data()` 中，回测模式下**直接读取 parquet**，不再调用 `_sync_to_display()`。

**为什么这样设计**: 
- 保证顺序: `_sync_all_cascading()` 在单线程中按 TF 顺序执行
- 避免重复: 每个 TF 只处理一次（而非每个视图一次）
- 兼容 fragment: 预处理在所有 fragment 之前完成，fragment 只负责读 parquet 和渲染

### 挑战D — 数据窗口 vs 合成窗口是不同概念

**区分**:
- **显示窗口**: `n_pts` 条bar，用于图表显示（如120条日线）
- **合成窗口**: 从 `last_completed_ts` 的下一周期开始到 `cutoff_date`，用于合成1条不完整bar

**这两个窗口互不影响**:
- DB 查询返回 `n_pts` 条已完成bar → 用于显示
- 从更细 TF 的 DataFrame 中选取 `[period_start, cutoff_date]` 范围 → 用于合成

**为什么 n_pts 不需要增大**: 合成只需要最新的一小段数据（最多一个周期的更细粒度数据），与历史显示窗口无关。

---

## 3. 方案设计

### 3.1 整体架构变更

```
当前架构 (每个视图独立调用):
  _render_chart_fragment(v0)     _render_chart_fragment(v1)
       │                               │
       ▼                               ▼
  _load_chart_data(tf=60m)      _load_chart_data(tf=daily)
       │                               │
       ▼                               ▼
  _sync_to_display(60m)         _sync_to_display(daily)
       │                               │
       ▼                               ▼
  DB查询60分钟 → parquet        DB查询日线 → parquet

目标架构 (集中式预处理 + 每个视图只读):
  main()
    │
    ▼
  _sync_all_cascading()  ← 集中处理，按TF顺序级联合成
    │
    ├→ 15分钟: DB查询 → parquet
    ├→ 60分钟: DB查询 + 15分钟数据合成 → parquet
    ├→ 日线:   DB查询 + 60分钟数据合成 → parquet
    └→ 周线:   DB查询 + 日线数据合成 → parquet
    │
    ▼
  _render_chart_fragment(v0) ─→ _load_chart_data(tf=60m)  ─→ 只读parquet
  _render_chart_fragment(v1) ─→ _load_chart_data(tf=daily) ─→ 只读parquet
  _render_chart_fragment(v2) ─→ _load_chart_data(tf=15m)  ─→ 只读parquet
  _render_chart_fragment(v3) ─→ _load_chart_data(tf=weekly)─→ 只读parquet
```

### 3.2 新增函数 `_sync_all_cascading()`

**位置**: `filter_app/services/data_loader.py`

**签名**:

```python
def _sync_all_cascading(
    ticker_code: str,
    tfs: list[str],
    cutoff_date: str,
    min_tf: str,
    n_pts: int = 120,
) -> dict[str, bool]:
    """级联合成所有TF的数据并写入parquet。

    按TF从细到粗的顺序逐级处理。每级的合成使用紧邻更细一级的
    完整DataFrame（含该级自己合成的bar），实现级联传递。
    
    参数:
        ticker_code: 股票代码
        tfs: 需要处理的TF列表（应已去重并按ALL_TFS索引升序排列）
        cutoff_date: ISO格式截止日期，如 "2026-07-03T14:47:00"
        min_tf: 4个视图中最细的周期（该TF不做合成，因为没有更细的数据源）
        n_pts: 每个TF保留的显示bar数

    返回:
        {tf: bool} — 每个TF是否成功写入parquet
    """
```

**处理流程伪代码**:

```python
def _sync_all_cascading(ticker_code, tfs, cutoff_date, min_tf, n_pts=120):
    results = {}
    prev_data = None      # 上一级TF的完整DataFrame（含合成bar）
    prev_tf = None        # 上一级TF的名称

    for tf in tfs:        # tfs已从细到粗排序
        # ── ① 从DB查询该TF的已完成bar ──
        db_rows = _query_tf_from_db(ticker_code, tf, cutoff_date, n_pts)

        if not db_rows:
            results[tf] = False
            prev_data = None
            prev_tf = tf
            continue

        # ── ② 判断是否需要合成 ──
        needs_synth = (
            tf != min_tf
            and prev_data is not None
            and _needs_synthesis(tf, db_rows, cutoff_date)
        )

        synthesized_bar = None
        if needs_synth:
            # ── ③ 从prev_data中选取数据合成不完整bar ──
            synthesized_bar = _synthesize_incomplete_bar(
                tf, db_rows, prev_data, cutoff_date
            )

        # ── ④ 构建完整DataFrame并写入parquet ──
        combined = _build_output_df(db_rows, synthesized_bar, n_pts)
        ok = _write_parquet(tf, combined)
        results[tf] = ok

        # ── ⑤ 保存当前TF的数据供下一级使用 ──
        if ok:
            prev_data = combined
            prev_tf = tf
        else:
            prev_data = None

    return results
```

**辅助函数（全部新增在 data_loader.py 中）**:

```python
def _query_tf_from_db(ticker_code, tf, cutoff_date, n_pts):
    """查询指定TF在cutoff_date之前的最后n_pts条已完成bar。

    返回 list[dict] （按时间升序），每条dict含 Date/Open/High/Low/Close/Volume。
    返回 [] 表示无数据。
    """
    from db import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT ts, open, high, low, close, volume
               FROM kline WHERE ticker=? AND timeframe=? AND ts <= ?
               ORDER BY ts DESC LIMIT ?""",
            (ticker_code, tf, cutoff_date, n_pts),
        ).fetchall()
    if not rows:
        return []
    # DESC → ASC
    return [
        {"Date": r[0], "Open": r[1], "High": r[2],
         "Low": r[3], "Close": r[4], "Volume": r[5]}
        for r in reversed(rows)
    ]


def _needs_synthesis(tf, db_rows, cutoff_date):
    """判断是否需要为给定TF合成不完整bar。

    逻辑: 检查cutoff_date是否已进入当前TF的下一个周期（该周期在DB中可能
    没有完成bar），如果是则需要合成。

    参数:
        tf: 当前TF名称
        db_rows: DB查询结果（list[dict]，时间升序）
        cutoff_date: ISO格式截止日期

    返回: bool
    """
    last_ts = pd.Timestamp(db_rows[-1]["Date"])   # 最后一条已完成bar的ts
    period_start = _get_period_start_ts(last_ts, tf)  # 下一周期开始时间戳
    cutoff_dt = pd.Timestamp(cutoff_date)

    return cutoff_dt >= period_start


def _get_period_start_ts(ts, tf):
    """计算ts所在周期结束后的下一个周期的开始时间戳。

    这与_get_period_start不同：_get_period_start返回的是数据查询起点
    （用于SQL WHERE），而本函数返回下一个周期的开始时刻（用于判断是否需要合成）。

    参数:
        ts: pd.Timestamp — 最后一条已完成bar的时间戳
        tf: 周期名称

    返回: pd.Timestamp — 下一个周期的开始时刻
    """
    ts = pd.Timestamp(ts)

    if tf == "5分钟":
        current_minutes = ts.hour * 60 + ts.minute
        next_boundary = ((current_minutes // 5) + 1) * 5
        next_hour = next_boundary // 60
        next_minute = next_boundary % 60
        if next_hour >= 24:
            return (ts + pd.Timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0)
        return ts.replace(hour=next_hour, minute=next_minute,
                          second=0, microsecond=0)

    elif tf == "15分钟":
        current_minutes = ts.hour * 60 + ts.minute
        next_boundary = ((current_minutes // 15) + 1) * 15
        next_hour = next_boundary // 60
        next_minute = next_boundary % 60
        if next_hour >= 24:
            return (ts + pd.Timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0)
        return ts.replace(hour=next_hour, minute=next_minute,
                          second=0, microsecond=0)

    elif tf == "60分钟":
        return (ts + pd.Timedelta(hours=1)).replace(
            minute=0, second=0, microsecond=0)

    elif tf == "日线":
        return (ts + pd.Timedelta(days=1)).normalize()

    elif tf == "周线":
        days_ahead = 4 - ts.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        return (ts + pd.Timedelta(days=days_ahead)).normalize()

    elif tf == "月线":
        if ts.month == 12:
            next_month = pd.Timestamp(year=ts.year + 1, month=1, day=1)
        else:
            next_month = pd.Timestamp(year=ts.year, month=ts.month + 1, day=1)
        # 返回月末最后一天+1 = 下月1日
        return next_month

    elif tf == "季线":
        q_end_month = ((ts.month - 1) // 3 + 1) * 3
        if q_end_month == 12:
            return pd.Timestamp(year=ts.year + 1, month=1, day=1)
        else:
            return pd.Timestamp(year=ts.year, month=q_end_month + 1, day=1)

    else:
        return ts


def _get_query_start_for_synthesis(last_completed_ts, tf):
    """计算合成时从prev_data中查询数据的起点时间戳。

    参数:
        last_completed_ts: 最后一条已完成bar的ts
        tf: 当前合成目标TF

    返回: pd.Timestamp — 查询起点（含）
    """
    ts = pd.Timestamp(last_completed_ts)

    if tf in ("5分钟", "15分钟", "60分钟"):
        return ts + pd.Timedelta(minutes=1)
    elif tf == "日线":
        return (ts + pd.Timedelta(days=1)).normalize()
    elif tf == "周线":
        return (ts + pd.Timedelta(days=1)).normalize()
    elif tf == "月线":
        if ts.month == 12:
            return pd.Timestamp(year=ts.year + 1, month=1, day=1)
        else:
            return pd.Timestamp(year=ts.year, month=ts.month + 1, day=1)
    elif tf == "季线":
        next_q_start_month = ((ts.month - 1) // 3 + 1) * 3 + 1
        if next_q_start_month > 12:
            return pd.Timestamp(year=ts.year + 1, month=1, day=1)
        else:
            return pd.Timestamp(year=ts.year, month=next_q_start_month, day=1)
    else:
        return ts + pd.Timedelta(days=1)


def _synthesize_incomplete_bar(tf, db_rows, prev_data, cutoff_date):
    """从prev_data中选取数据，合成目标TF的一条不完整bar。

    参数:
        tf: 目标TF（要合成哪个周期的bar）
        db_rows: 目标TF在DB中的已完成bar（list[dict]）
        prev_data: 紧邻更细一级TF的完整DataFrame（含其合成bar）
        cutoff_date: 截止日期

    返回: dict | None — 合成的bar数据，None表示无法合成
    """
    last_completed_ts = db_rows[-1]["Date"]
    query_start = _get_query_start_for_synthesis(last_completed_ts, tf)
    cutoff_dt = pd.Timestamp(cutoff_date)

    # 将prev_data的Date列转为Timestamp用于过滤
    prev_dt = pd.to_datetime(prev_data["Date"])

    # 选取 [query_start, cutoff_date] 范围内的更细粒度bar
    mask = (prev_dt >= query_start) & (prev_dt <= cutoff_dt)
    finer_bars = prev_data.loc[mask]

    if len(finer_bars) == 0:
        return None

    # 聚合OHLCV
    return {
        "Date": cutoff_date,  # 使用cutoff_date作为合成bar的时间戳
        "Open": float(finer_bars["Open"].iloc[0]),
        "High": float(finer_bars["High"].max()),
        "Low": float(finer_bars["Low"].min()),
        "Close": float(finer_bars["Close"].iloc[-1]),
        "Volume": float(finer_bars["Volume"].sum()),
    }


def _build_output_df(db_rows, synthesized_bar, n_pts):
    """将DB已完成bar + 合成bar合并为DataFrame，截断到n_pts。

    参数:
        db_rows: DB已完成bar列表（list[dict]）
        synthesized_bar: 合成bar（dict）或None
        n_pts: 最大保留行数

    返回: pd.DataFrame（列: Date/Open/High/Low/Close/Volume）
    """
    data = list(db_rows)

    if synthesized_bar is not None:
        data.append(synthesized_bar)

    df = pd.DataFrame(data)
    if len(df) > n_pts:
        df = df.iloc[-n_pts:]  # 保留最后n_pts条

    return df.reset_index(drop=True)


def _write_parquet(tf, df):
    """将DataFrame写入该TF的parquet文件。

    参数:
        tf: 周期名称（用于文件命名）
        df: pd.DataFrame

    返回: bool — 是否写入成功
    """
    display_dir = Path(__file__).parent.parent.parent / "data" / "display"
    display_dir.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(display_dir / f"{tf}.parquet", index=False)
        logger.debug(f"Wrote {tf}.parquet: {len(df)} rows")
        return True
    except Exception as e:
        logger.error(f"Failed to write {tf}.parquet: {e}")
        return False
```

### 3.3 修改 `_sync_to_display()`

**修改内容**:
1. 保持签名不变（向后兼容浏览模式）
2. 移除合成逻辑（因为级联合成已移到 `_sync_all_cascading()`）
3. 简化回测模式: 只做 DB 查询 + parquet 写入，不再合成

```python
def _sync_to_display(ticker_code, tf, day_offset=0, n_pts=120,
                     cutoff_date=None) -> Tuple[bool, int]:
    """同步数据到 display parquet。

    cutoff_date=None: 浏览模式
    cutoff_date给定: 回测模式（仅查询，不做合成——合成由_sync_all_cascading负责）
    """
    if cutoff_date is not None:
        # 回测模式: 直接查询写入（级联合成由_sync_all_cascading独立处理）
        from db import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT ts, open, high, low, close, volume
                   FROM kline WHERE ticker=? AND timeframe=? AND ts <= ?
                   ORDER BY ts DESC LIMIT ?""",
                (ticker_code, tf, cutoff_date, n_pts),
            ).fetchall()
        if rows:
            rows_list = list(reversed(rows))
            df = pd.DataFrame(
                rows_list,
                columns=["Date", "Open", "High", "Low", "Close", "Volume"]
            )
            display_dir = Path(__file__).parent.parent.parent / "data" / "display"
            display_dir.mkdir(parents=True, exist_ok=True)
            df.to_parquet(display_dir / f"{tf}.parquet", index=False)
            return True, len(df)
        return False, 0

    # 浏览模式: 保持不变
    df = query_kline(ticker_code, tf, n_pts, day_offset=day_offset)
    if len(df) < 5:
        return False, len(df)
    df["Date"] = pd.to_datetime(df["Date"])
    display_dir = Path(__file__).parent.parent.parent / "data" / "display"
    display_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(display_dir / f"{tf}.parquet", index=False)
    return True, len(df)
```

### 3.4 修改 `_load_chart_data()`

**位置**: `streamlit_app.py:115-172`

**修改内容**: 回测模式下不再调用 `_sync_to_display()`，直接读取 parquet:

```python
def _load_chart_data(market, ticker_code, tf, day_offset, n_pts,
                     window_start=None, cutoff_date=None):
    """Load chart data from display cache or fetch from API.

    浏览: window_start=None, cutoff_date=None
    回测: window_start=bar_index, cutoff_date=截止日期

    ★ 回测模式下parquet已由_sync_all_cascading()预处理，
      本函数只负责读取。
    """
    if window_start is not None:
        # ★ 回测模式: parquet已由main()中的_sync_all_cascading()准备好
        # 直接跳转到parquet读取，不再调用_sync_to_display()
        pass
    else:
        # 浏览模式: 保持原有逻辑不变
        ok, count = _sync_to_display(ticker_code, tf, day_offset=day_offset,
                                      n_pts=n_pts)
        if not ok:
            return _cached_fetch_stock(market, ticker_code, tf, n_pts)

    # ── 共用: 读取parquet ──
    display_path = Path(__file__).parent.parent / "data" / "display" / f"{tf}.parquet"
    err = None
    if display_path.exists():
        try:
            df = pd.read_parquet(display_path)
            if "Date" in df.columns and "Close" in df.columns and len(df) >= 2:
                df["Date"] = pd.to_datetime(df["Date"])
                df = df.set_index("Date").sort_index()

                if len(df) < 2:
                    err = f"{tf} 数据点不足 ({len(df)})"
                    return None, None, None, None, None, err

                t = np.arange(len(df), dtype=float)
                noisy = df["Close"].values.ravel()
                ohlc = df[["Open", "High", "Low", "Close"]] \
                    if all(c in df.columns for c in ["Open", "High", "Low"]) \
                    else pd.DataFrame(
                        {"Open": noisy, "High": noisy, "Low": noisy, "Close": noisy},
                        index=df.index)

                if window_start is not None:
                    try:
                        log_data_load(ticker_code, tf, len(df),
                                      cutoff_date or "", elapsed_ms=0)
                    except Exception as e:
                        logger.debug(f"回测日志写入失败: {e}")

                return t, noisy, ohlc, ticker_code, df.index, None
            else:
                err = "数据不足"
        except Exception as e:
            err = str(e)

    if err is not None:
        return None, None, None, None, None, err
    return _cached_fetch_stock(market, ticker_code, tf, n_pts)
```

### 3.5 修改 `main()` — 添加 `_sync_all_cascading()` 调用

**位置**: `streamlit_app.py` 约1620行（`sorted_views` 循环之前）

```python
# ── 回测模式: 级联合成预处理 ──
cb_mode = AppState.get("_cb_mode", False)
if cb_mode and ticker_code:
    cutoff_date = AppState.get("_bt_cutoff_date", "")
    min_tf = AppState.get("_min_tf", "")
    if cutoff_date and min_tf:
        # 提取所有视图中涉及的不同TF，从细到粗排序
        unique_tfs = sorted(
            set(cfg["tf"] for cfg in configs),
            key=lambda tf: ALL_TFS.index(tf)
        )
        # 取所有视图中最小的 n_pts（保守值，保证每个视图有足够数据）
        min_n_pts = min(cfg["n_pts"] for cfg in configs) if configs else 120
        _sync_all_cascading(ticker_code, unique_tfs, cutoff_date, min_tf, min_n_pts)
```

### 3.6 ALL_TFS 常量位置

`ALL_TFS` 目前定义在 `components/sidebar.py`（第13行）。`data_loader.py` 需要引用它。

**选择**: 在 `data_loader.py` 中独立定义一份 `ALL_TFS`（如 `feature/backtest-bar-synthesis` 分支的方式），避免从 UI 模块导入。两份定义通过测试保证一致性。

```python
# data_loader.py 顶部添加
ALL_TFS = ["1分钟", "5分钟", "15分钟", "60分钟", "日线", "周线", "月线", "季线"]
```

**为什么不在两个模块间共享**: `data_loader.py` 是无 Streamlit 依赖的纯数据模块，而 `sidebar.py` 是 UI 模块。从数据模块导入 UI 模块会引入不必要的依赖链。

测试中验证一致性:

```python
# tests/test_data_loader.py
def test_all_tfs_consistency():
    from services.data_loader import ALL_TFS as DL_TFS
    from components.sidebar import ALL_TFS as SB_TFS
    assert DL_TFS == SB_TFS, "data_loader.ALL_TFS must match sidebar.ALL_TFS"
```

---

## 4. 实现细节

### 4.1 合成查询窗口计算（完整版）

```python
def _get_query_start_for_synthesis(last_completed_ts, tf):
    """计算合成时的数据查询起点。

    这是从更细TF的DataFrame中选取数据的最小时间戳（含）。

    参数:
        last_completed_ts: 最后一条已完成bar的ts
        tf: 合成目标TF

    返回:
        pd.Timestamp — 查询起点
    """
    ts = pd.Timestamp(last_completed_ts)

    if tf == "5分钟":
        # 5分钟周期: 从last bar结束+1分钟开始
        return ts + pd.Timedelta(minutes=1)
    elif tf == "15分钟":
        return ts + pd.Timedelta(minutes=1)
    elif tf == "60分钟":
        return ts + pd.Timedelta(minutes=1)
    elif tf == "日线":
        # 日线: 从last bar的下一天开始
        return (ts + pd.Timedelta(days=1)).normalize()
    elif tf == "周线":
        return (ts + pd.Timedelta(days=1)).normalize()
    elif tf == "月线":
        if ts.month == 12:
            return pd.Timestamp(year=ts.year + 1, month=1, day=1)
        return pd.Timestamp(year=ts.year, month=ts.month + 1, day=1)
    elif tf == "季线":
        next_q_start_month = ((ts.month - 1) // 3 + 1) * 3 + 1
        if next_q_start_month > 12:
            return pd.Timestamp(year=ts.year + 1, month=1, day=1)
        return pd.Timestamp(year=ts.year, month=next_q_start_month, day=1)
    return ts + pd.Timedelta(days=1)
```

### 4.2 边界条件

#### 4.2.1 min_tf是日线（无分钟数据）

**场景**: 4个视图配置为日线/周线/月线/季线

**处理**: `min_tf` = "日线"。处理顺序: 日线 → 周线 → 月线 → 季线。
- 日线: `tf == min_tf`，不做合成（直接DB查询）
- 周线: 从日线DataFrame中合成
- 月线: 从日线DataFrame中合成（注意: 这里用日线而非周线！因为 `prev_data` 始终保持为前一处理的TF的数据）

**重要**: 当前的逐级传递意味着月线会从周线数据合成。如果视图中有日线但没有周线，月线合成时 `prev_data` 是日线数据（因为周线不在 `tfs` 列表中，不会被处理）。这是正确的——月线从日线合成只要查更多bar，聚合逻辑完全一样。

#### 4.2.2 中间TF缺失时的级联跳跃

**场景**: 视图配置为 60分钟/日线/月线（没有周线）

**处理**: 处理顺序: 60分钟 → 日线 → 月线
- 日线合成: prev_data = 60分钟DataFrame ✓
- 月线合成: prev_data = 日线DataFrame ✓

但存在一个问题: 月线从日线合成，需要30条左右的日线bar。日线的 prev_data 只有约120条（足够覆盖一个月）。可以正常工作。

#### 4.2.3 开市初期数据很少

**场景**: 刚开盘15分钟

**处理**: 
- DB查询结果可能少于 `n_pts`
- `_needs_synthesis()` 正常判断
- 合成时从 prev_data 选取数据，可能返回空 → `synthesized_bar = None`
- `_build_output_df()` 只输出 DB 数据 → parquet 行数 < n_pts
- 图表显示时会少一些bar，但不会报错

#### 4.2.4 跨周末/节假日

**场景**: 周五收盘后，周一开盘前的数据

**处理**: 无需特殊处理。DB 中没有周末数据，`_get_query_start_for_synthesis()` 计算的查询起点可能落在周末，但 prev_data 的过滤会自然返回空结果。

#### 4.2.5 某个TF的数据为空

**场景**: ticker 没有60分钟数据（某些yfinance市场不提供）

**处理**: `_query_tf_from_db()` 返回 `[]` → `results[tf] = False` → `prev_data = None` → 更粗TF跳过合成。

#### 4.2.6 cutoff_date对应的时间点恰好等于周期边界

**场景**: cutoff_date = "15:00"，60分钟周期正好整点结束

**处理**: `cutoff_dt(15:00) >= period_start(15:01)` 为 False（因为15:00 < 15:01）。不合成。DB 中应该已有 15:00 的完成bar。

#### 4.2.7 同一TF被多个视图使用

**场景**: 视图0和视图2都使用"日线"

**处理**: `unique_tfs = set(...)` 自动去重，只处理一次。

### 4.3 Parquet写入顺序与Streamlit刷新

写入顺序: 从细到粗（由 `tfs` 的排序保证）

刷新时机: 不需要显式通知。`main()` 中 `_sync_all_cascading()` 返回后，后续的 `_render_chart_fragment()` 调用会读取刚写入的 parquet。如果 `_sync_all_cascading()` 需要触发 rerun（如首次进入回测模式），由调用方（main）决定。

### 4.4 日志与调试

建议在 `_sync_all_cascading()` 中添加调试日志:

```python
logger.debug(f"Cascading synthesis for {ticker_code}: "
             f"tfs={tfs}, cutoff={cutoff_date}, min_tf={min_tf}")

for tf in tfs:
    # ...
    if synthesized_bar:
        logger.debug(
            f"  [{tf}] synthesized bar at {cutoff_date}: "
            f"O={synthesized_bar['Open']:.2f} "
            f"H={synthesized_bar['High']:.2f} "
            f"L={synthesized_bar['Low']:.2f} "
            f"C={synthesized_bar['Close']:.2f}"
        )

logger.debug(f"Cascading synthesis done: {results}")
```

回测日志（`backtest_logger.py`）暂不新增事件类型，保持现有 `log_data_load` 接口不变。

---

## 5. ASCII数据流图

### 5.1 级联合成全景

```
═══════════════════════════════════════════════════════════════════════════════
                         main() — 回测模式入口
═══════════════════════════════════════════════════════════════════════════════

  configs = [
    {"tf": "15分钟", "n_pts": 120},   ← 视图0
    {"tf": "60分钟", "n_pts": 120},   ← 视图1
    {"tf": "日线",   "n_pts": 120},   ← 视图2
    {"tf": "周线",   "n_pts": 120},   ← 视图3
  ]

  unique_tfs = {"15分钟", "60分钟", "日线", "周线"}  ← 去重
  processing_order = ["15分钟", "60分钟", "日线", "周线"]  ← 细→粗排序

  min_tf = "15分钟"
  cutoff_date = "2026-07-03T14:47:00"

  ┌── _sync_all_cascading(...) ──────────────────────────────────────────┐
  │                                                                       │
  │  prev_data = None                                                     │
  │                                                                       │
  │ ┌─ [第1级] TF=15分钟 ───────────────────────────────────────────┐    │
  │ │                                                                │    │
  │ │  ① DB查询: SELECT ... FROM kline                               │    │
  │ │     WHERE ticker=? AND timeframe='15分钟' AND ts<='14:47'       │    │
  │ │     ORDER BY ts DESC LIMIT 120                                 │    │
  │ │     → 120条已完成bar                                           │    │
  │ │                                                                │    │
  │ │  ② 判断: tf(15分钟) == min_tf(15分钟)? YES → 跳过合成           │    │
  │ │                                                                │    │
  │ │  ③ 写入 data/display/15分钟.parquet (120条, 无合成bar)          │    │
  │ │                                                                │    │
  │ │  ④ prev_data = DataFrame(120条) ──────────────┐                │    │
  │ └────────────────────────────────────────────────│────────────────┘    │
  │                                                  │                      │
  │ ┌─ [第2级] TF=60分钟 ────────────────────────────│────────────────┐    │
  │ │                                                │                │    │
  │ │  ① DB查询: WHERE timeframe='60分钟'             │                │    │
  │ │     → 最后一条 ts="2026-07-03T14:00:00"         │                │    │
  │ │     (07-03 14:00是60分钟的完成bar)               │                │    │
  │ │                                                │                │    │
  │ │  ② 判断: tf≠min_tf AND prev_data ok AND          │                │    │
  │ │     cutoff_dt(14:47) >= period_start(14:01)?    │                │    │
  │ │     → YES, 需要合成60分钟的不完整bar              │                │    │
  │ │                                                ▼                │    │
  │ │  ③ 从prev_data(15分钟DataFrame)中选取:           │                │    │
  │ │     Date >= "2026-07-03T14:01:00"               │                │    │
  │ │     AND Date <= "2026-07-03T14:47:00"            │                │    │
  │ │     → {14:00, 14:15, 14:30, 14:45} 4条bar       │                │    │
  │ │                                                │                │    │
  │ │  ④ 聚合:                                         │                │    │
  │ │     synth_bar = {                               │                │    │
  │ │       Date: "2026-07-03T14:47:00",              │                │    │
  │ │       Open:  14:00的Open,                       │                │    │
  │ │       High:  max(4条bar的High),                 │                │    │
  │ │       Low:   min(4条bar的Low),                  │                │    │
  │ │       Close: 14:45的Close,                      │                │    │
  │ │       Volume: sum(4条bar的Volume)                │                │    │
  │ │     }                                           │                │    │
  │ │                                                │                │    │
  │ │  ⑤ 写入 data/display/60分钟.parquet              │                │    │
  │ │     (120条DB完成bar + 1条合成bar)                 │                │    │
  │ │                                                │                │    │
  │ │  ⑥ prev_data = DataFrame(121条, 含合成bar) ─────┐│                │    │
  │ └────────────────────────────────────────────────││────────────────┘    │
  │                                                  ││                      │
  │ ┌─ [第3级] TF=日线 ──────────────────────────────││────────────────┐    │
  │ │                                                ││                │    │
  │ │  ① DB查询: WHERE timeframe='日线'               ││                │    │
  │ │     → 最后一条 ts="2026-07-02"  (昨天的日线)     ││                │    │
  │ │                                                ││                │    │
  │ │  ② 判断: cutoff_dt >= period_start(07-03)?      ││                │    │
  │ │     → YES, 需要合成当天的日线bar                  ││                │    │
  │ │                                                ▼│                │    │
  │ │  ③ 从prev_data(60分钟DataFrame)中选取:           │                │    │
  │ │     Date >= "2026-07-03T00:00:00"               │                │    │
  │ │     AND Date <= "2026-07-03T14:47:00"            │                │    │
  │ │     → 当天所有60分钟bar (约5-7条)                 │                │    │
  │ │     ★ 包含60分钟那一级合成的bar (ts="14:47")      │                │    │
  │ │                                                │                │    │
  │ │  ④ 聚合:                                        │                │    │
  │ │     synth_bar = {                               │                │    │
  │ │       Date: "2026-07-03T14:47:00",              │                │    │
  │ │       Open:  当天第一条60分钟bar的Open,           │                │    │
  │ │       High:  当天所有60分钟bar的最高价,           │                │    │
  │ │       Low:   当天所有60分钟bar的最低价,           │                │    │
  │ │       Close: 最后一条60分钟bar(ts=14:47)的Close,  │                │    │
  │ │       Volume: 总成交量                           │                │    │
  │ │     }                                           │                │    │
  │ │                                                │                │    │
  │ │  ⑤ 写入 data/display/日线.parquet                │                │    │
  │ │                                                │                │    │
  │ │  ⑥ prev_data = 日线DataFrame ──────────────────┐│                │    │
  │ └────────────────────────────────────────────────││────────────────┘    │
  │                                                  ││                      │
  │ ┌─ [第4级] TF=周线 ──────────────────────────────││────────────────┐    │
  │ │                                                ││                │    │
  │ │  ① DB查询: WHERE timeframe='周线'               ││                │    │
  │ │     → 最后一条 ts="2026-06-26" (上周的周线)      ││                │    │
  │ │                                                ││                │    │
  │ │  ② 判断: cutoff_dt >= period_start(06-27)?      ││                │    │
  │ │     → YES, 需要合成本周的周线bar                  ││                │    │
  │ │                                                ▼│                │    │
  │ │  ③ 从prev_data(日线DataFrame)中选取:             ││                │    │
  │ │     Date >= "2026-06-27"                       │                │    │
  │ │     AND Date <= "2026-07-03T14:47:00"           │                │    │
  │ │     → 本周一到周五的日线bar (5条，含合成日线)      │                │    │
  │ │                                                │                │    │
  │ │  ④ 聚合: O/H/L/C/V 标准规则                      │                │    │
  │ │                                                │                │    │
  │ │  ⑤ 写入 data/display/周线.parquet                │                │    │
  │ └─────────────────────────────────────────────────────────────────┘    │
  │                                                                       │
  │  return {"15分钟": True, "60分钟": True, "日线": True, "周线": True}     │
  └───────────────────────────────────────────────────────────────────────┘
```

### 5.2 数据传递路径（简化版）

```
                    ┌──────────────┐
                    │   SQLite DB  │
                    │  kline table │
                    └──┬───┬───┬──┘
                       │   │   │
          ┌────────────┘   │   └────────────┐
          ▼                ▼                ▼
    ┌──────────┐    ┌──────────┐    ┌──────────┐
    │ 15分钟DB │    │ 60分钟DB │    │  日线DB  │  ← 各TF独立查询DB
    │ (120条)  │    │ (120条)  │    │ (120条)  │
    └────┬─────┘    └────┬─────┘    └────┬─────┘
         │               │               │
         │    ┌──────────┘               │
         │    │  prev_data               │
         ▼    ▼                          │
    ┌─────────────────┐                  │
    │ 合成60分钟不完整bar│                  │
    │ (从15分钟DF中聚合) │                  │
    └────────┬────────┘                  │
             │                           │
             ▼                           │
    ┌─────────────────┐                  │
    │ 60分钟最终DF     │                  │
    │ (DB + 合成bar)   │── prev_data ────┘
    └────────┬────────┘                  │
             │                           ▼
             │              ┌─────────────────────┐
             │              │ 合成日线不完整bar      │
             │              │ (从60分钟DF中聚合)     │
             │              └──────────┬──────────┘
             │                         │
             │                         ▼
             │              ┌─────────────────────┐
             │              │ 日线最终DF            │
             │              │ (DB + 合成bar)        │── prev_data ──→ 周线合成...
             │              └──────────────────────┘
             │
             ▼
    ┌─────────────────────────────────────────────┐
    │         data/display/ 目录                    │
    │  15分钟.parquet  60分钟.parquet  日线.parquet  │
    │  ...                                        │
    └─────────────────────────────────────────────┘
    
    图例:
    ──→ 数据来源（DB查询）
    ──→ 内存传递（prev_data）
    ──→ 文件写入（parquet）
```

---

## 6. 实施建议

### 6.1 建议的分步实施顺序

```
Phase 1: 基础架构搭建（低风险，可独立验证）
  ├─ 1.1 在 data_loader.py 中添加 ALL_TFS 常量
  ├─ 1.2 实现 _query_tf_from_db()
  ├─ 1.3 实现 _build_output_df() 和 _write_parquet()
  └─ 验证: 用单元测试验证每个辅助函数

Phase 2: 周期计算函数（中等风险）
  ├─ 2.1 实现 _get_period_start_ts() — 8个周期的边界计算
  ├─ 2.2 实现 _get_query_start_for_synthesis()
  ├─ 2.3 实现 _needs_synthesis()
  └─ 验证: 用参数化测试覆盖边界（跨天、跨月、跨年、整点）

Phase 3: 合成核心（中等风险）
  ├─ 3.1 实现 _synthesize_incomplete_bar()
  ├─ 3.2 实现 _sync_all_cascading() 主函数
  └─ 验证: 合成测试（用mock数据模拟完整的60分钟→日线→周线级联）

Phase 4: 集成到 Streamlit（需谨慎）
  ├─ 4.1 修改 _load_chart_data() — 回测模式跳过 _sync_to_display()
  ├─ 4.2 修改 main() — 添加 _sync_all_cascading() 调用
  ├─ 4.3 编写集成测试
  └─ 验证: 完整回测场景端到端测试
```

### 6.2 每步的验证方法

#### Phase 1 验证

```python
# tests/test_cascading_synthesis.py (新建)

def test_query_tf_from_db_returns_ascending():
    """验证DB查询结果按时间升序"""
    ...

def test_build_output_df_truncates_to_n_pts():
    """验证截断到n_pts"""
    db_rows = [mock_bar(i) for i in range(150)]
    synth = mock_bar(150)
    df = _build_output_df(db_rows, synth, n_pts=120)
    assert len(df) == 120
    # 合成bar在末尾
    assert df.iloc[-1]["Date"] == synth["Date"]

def test_build_output_df_no_synthesis():
    """验证无合成bar时的行为"""
    df = _build_output_df(db_rows, None, n_pts=120)
    assert len(df) == 120
```

#### Phase 2 验证

```python
@pytest.mark.parametrize("ts,tf,expected", [
    # 分钟级
    ("2026-07-03T14:00:00", "60分钟", "2026-07-03T15:00:00"),
    ("2026-07-03T14:15:00", "15分钟", "2026-07-03T14:30:00"),
    ("2026-07-03T14:55:00", "5分钟",  "2026-07-03T15:00:00"),
    # 日线
    ("2026-07-02", "日线", "2026-07-03T00:00:00"),
    # 周线 — 周五→下周五
    ("2026-07-03", "周线", "2026-07-10T00:00:00"),  # 07-03是周五
    # 月线 — 月末
    ("2026-06-30", "月线", "2026-07-01T00:00:00"),
    # 季线
    ("2026-06-30", "季线", "2026-07-01T00:00:00"),
    # 跨年
    ("2026-12-31", "月线", "2027-01-01T00:00:00"),
])
def test_get_period_start_ts(ts, tf, expected):
    result = _get_period_start_ts(ts, tf)
    assert result == pd.Timestamp(expected)

@pytest.mark.parametrize("last_ts,tf,cutoff,expected", [
    # 需要合成
    ("2026-07-03T14:00:00", "60分钟", "2026-07-03T14:47:00", True),
    ("2026-07-02", "日线", "2026-07-03T14:47:00", True),
    ("2026-07-03", "周线", "2026-07-07T10:00:00", True),  # 周五→下周二
    # 不需要合成
    ("2026-07-03T14:00:00", "60分钟", "2026-07-03T13:45:00", False),
    ("2026-07-03", "日线", "2026-07-03T10:00:00", False),
])
def test_needs_synthesis(last_ts, tf, cutoff, expected):
    db_rows = [{"Date": last_ts, ...}]
    assert _needs_synthesis(tf, db_rows, cutoff) == expected
```

#### Phase 3 验证

```python
def test_synthesize_incomplete_bar_60min():
    """15分钟→60分钟合成"""
    # 构造15分钟prev_data
    prev = pd.DataFrame([
        {"Date": "2026-07-03T14:00:00", "Open": 100, "High": 102, "Low": 99, "Close": 101, "Volume": 1000},
        {"Date": "2026-07-03T14:15:00", "Open": 101, "High": 103, "Low": 100, "Close": 102, "Volume": 1200},
        {"Date": "2026-07-03T14:30:00", "Open": 102, "High": 104, "Low": 101, "Close": 103, "Volume": 1100},
        {"Date": "2026-07-03T14:45:00", "Open": 103, "High": 105, "Low": 102, "Close": 104, "Volume": 900},
    ])
    db_rows = [{"Date": "2026-07-03T14:00:00", ...}]  # 60分钟最后完成bar
    cutoff = "2026-07-03T14:47:00"
    
    result = _synthesize_incomplete_bar("60分钟", db_rows, prev, cutoff)
    
    assert result is not None
    assert result["Date"] == cutoff
    assert result["Open"] == 100  # 第一条的Open
    assert result["High"] == 105  # max
    assert result["Low"] == 99    # min
    assert result["Close"] == 104 # 最后一条的Close
    assert result["Volume"] == 4200  # sum

def test_cascading_60min_to_daily():
    """60分钟→日线级联合成: 验证合成bar被下一级使用"""
    # 构造60分钟prev_data（含合成bar）
    prev = pd.DataFrame([
        # ... 已完成的60分钟bar ...
        {"Date": "2026-07-03T14:47:00",  # 这是合成的60分钟不完整bar
         "Open": 150, "High": 155, "Low": 149, "Close": 153, "Volume": 5000},
    ])
    db_rows = [{"Date": "2026-07-02", ...}]  # 日线最后完成bar
    cutoff = "2026-07-03T14:47:00"

    result = _synthesize_incomplete_bar("日线", db_rows, prev, cutoff)

    # 日线合成应该能使用合成60分钟bar的数据
    assert result is not None
    assert result["Open"] == 150  # 来自合成bar的Open
```

#### Phase 4 验证

1. **手动测试**: 在回测模式下拖动 slider，观察4个视图的图表是否正常更新，最新bar的OHLC数据是否合理
2. **日志检查**: 确认 `logger.debug` 输出了级联合成的每一步
3. **性能测试**: 确认 `_sync_all_cascading()` 执行时间 < 200ms（4个TF，120条/每个）

### 6.3 风险与回滚策略

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 周期边界计算错误 | 合成bar数据不正确 | Phase 2的参数化测试覆盖所有TF和边界 |
| 级联传递中断 | 粗周期无合成bar | `prev_data = None` 的降级机制 |
| Streamlit rerun性能 | 回测变慢 | 只处理去重后的unique_tfs |
| 浏览模式受影响 | 浏览模式功能异常 | 浏览模式代码路径完全不变 |

**回滚**: 如果集成后发现问题，只需在 `main()` 中移除 `_sync_all_cascading()` 调用，恢复 `_load_chart_data()` 中原有的 `_sync_to_display()` 调用即可。

---

## 7. 附录: 与现有代码的兼容性影响

### 7.1 函数签名变更

| 函数 | 当前签名 | 变更后 | 影响范围 |
|------|---------|--------|---------|
| `_sync_to_display()` | `(ticker_code, tf, day_offset, n_pts, cutoff_date)` | 不变（内部简化） | `streamlit_app.py:_load_chart_data()` |
| `_load_chart_data()` | `(market, ticker_code, tf, day_offset, n_pts, window_start, cutoff_date)` | 不变（内部逻辑调整） | `streamlit_app.py:_render_chart()` |
| `_sync_all_cascading()` | **新增** | `(ticker_code, tfs, cutoff_date, min_tf, n_pts)` → `dict` | `streamlit_app.py:main()` |

### 7.2 调用方变更

| 文件 | 行号（近似） | 变更 |
|------|-----------|------|
| `streamlit_app.py` | ~123 | `_load_chart_data()` 回测分支: 移除 `_sync_to_display()` 调用，改为直接读parquet |
| `streamlit_app.py` | ~1612 | `main()` 中 `sorted_views` 之前: 新增 `_sync_all_cascading()` 调用 |
| `data_loader.py` | 顶部 | 新增 `ALL_TFS` 常量 |
| `data_loader.py` | 139-173 | `_sync_to_display()` 简化回测分支逻辑 |

### 7.3 不需要变更的部分

- **浏览模式**: 完全不变。`_sync_to_display()` 在浏览模式下的逻辑保持不变
- **`db.py`**: 无需变更。合成bar不写入DB
- **`state.py`**: 无需变更
- **`backtest_logger.py`**: 无需变更
- **`components/sidebar.py`**: 无需变更（`ALL_TFS` 保持原有定义）
- **`components/charts.py`**: 无需变更
- **测试**: 现有测试全部兼容（浏览模式不受影响）

### 7.4 需要新增的文件

| 文件 | 用途 |
|------|------|
| `tests/test_cascading_synthesis.py` | 级联合成单元测试和集成测试 |

---

> **文档版本**: v1.0 | **下次评审**: 实施 Phase 3 前
