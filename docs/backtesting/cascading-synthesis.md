# 回测数据级联合成方案

> **文档版本**: v2.0 (合并版) | **最后更新**: 2026-07-05
> **来源文件**:
>   - `cascading-synthesis-design.md` (v1.0 详细设计, 2323行)
>   - `cascading-synthesis-v2.md` (v2.0 修正方案, 1488行)
>   - `design-review.md` (v1 审查报告, 597行)
>   - `design-review-v2.md` (v2 再审报告, 654行)
>
> 本文档以 **v2 修正方案** 为主线，v1 设计作为背景参考，审查结论作为附录。合并原则：保留所有关键设计决策、代码示例和边界条件，去重后的内容按逻辑重排。

---

## 目录

1. [概述与动机](#1-概述与动机)
2. [核心挑战与方案选型](#2-核心挑战与方案选型)
3. [时间格式统一策略](#3-时间格式统一策略)
4. [核心函数设计](#4-核心函数设计)
5. [各周期合成规则](#5-各周期合成规则)
6. [边界条件处理](#6-边界条件处理)
7. [数据流图](#7-数据流图)
8. [实施计划](#8-实施计划)
9. [兼容性影响](#9-兼容性影响)
10. [附录: v1-v2 变更清单](#10-附录-v1-v2-变更清单)
11. [附录: v1 审查报告结论](#11-附录-v1-审查报告结论)
12. [附录: v2 再审报告结论](#12-附录-v2-再审报告结论)

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

### 1.3 v2 与 v1 的核心差异

| 方面 | v1 | v2 |
|------|----|----|
| 合成数据来源 | `prev_data` (120条窗口DataFrame) | 重新查询DB + 拼接更细TF合成bar |
| 时区处理 | 忽略，导致 TypeError | 统一剥离后比较，写入时恢复 |
| 周线 period_start | 计算下一周五（错误） | `last_ts + 1天`（正确） |
| 月线/季线 period_start | 复杂月份计算 | `last_ts + 1天`（简化，等价正确） |
| parquet Date 格式 | 混合时区（崩溃风险） | 按TF统一格式 |
| 合成窗口过滤 | 分钟: 09:30限制无 | 日线合成60分钟从09:30开始 |

---

## 2. 核心挑战与方案选型

### 挑战A — 合成bar不在DB中

60分钟的合成bar通过 `_sync_to_display()` 写入 parquet，不写入 DB。当下一级查询 DB 时找不到合成bar。

**方案对比**:

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| **A2 — 内存传递** (选型) | 按TF顺序处理，每级合成bar保存在内存dict中传给下一级 | 零DB污染；天然适配级联；简单可靠 | 需要重构为集中式预处理函数 |
| A1 — 临时写入DB | 合成后 INSERT，完成级联后 DELETE | 所有代码走统一DB查询路径 | 污染DB；需要清理逻辑；并发风险 |
| A3 — 扩大查询范围 | 查询时把合成bar覆盖的时间段也查进去 | 改动最小 | 查询条件复杂；可能重复数据；边界难处理 |

**选型理由**: 级联合成本质上是一个有序的预处理流水线，天然适合集中处理。零DB污染、无生命周期管理、内存开销极小（最多8个DataFrame，每个约120行）。

### 挑战B — 合成bar的时间戳

**决策**: 合成bar的 `Date` 使用 **`cutoff_date`** 而非周期结束时间。原因:
1. 合成bar代表的是不完整周期（从 last_completed_ts 到 cutoff_date）
2. 使用 cutoff_date 作为 ts 意味着它排在最后一个已完成bar之后
3. 下一级合成过滤时自然能找到它（因为 ts <= cutoff_date）

后续 v2 通过 `_format_synth_date()` 进一步按TF统一了 Date 格式（分钟级保留时区，日线级剥离时区）。

### 挑战C — 处理顺序

**解决方案**: 前置集中处理。在 `main()` 中所有 fragment 渲染之前调用一次 `_sync_all_cascading()`。

### 挑战D — 数据窗口 vs 合成窗口

显示窗口（`n_pts` 条bar）和合成窗口（`[last_completed_ts + 1, cutoff_date]`）互不影响。v2 改进为分离显示数据和合成数据路径。

---

## 3. 时间格式统一策略

### 3.1 问题根源

DB 中不同 TF 的时间戳格式不一致（由 `yfinance` 行为决定）:

| TF 类别 | DB ts 格式 | 示例 | yfinance 原因 |
|---------|-----------|------|--------------|
| 分钟级 (1m/5m/15m/60m) | ISO 8601 **带时区** | `2026-07-03T14:00:00+08:00` | intraday 数据带交易所时区 |
| 日线及以上 (日/周/月/季) | ISO 8601 **无时区** | `2026-07-03T00:00:00` | daily 数据是纯日期 |

核心矛盾链:

```
cutoff_date (来自 min_tf DB 查询 → 带时区 "+08:00")
    ↓
与 _get_period_start_ts() 的返回值 (内置 .normalize()/.replace() 丢弃时区 → tz-naive)
    ↓
pd.Timestamp 比较 → TypeError ❌
```

### 3.2 统一策略: "比较前剥离，写入时保留"

```
原则1: 所有 pd.Timestamp 比较前统一做 tz_localize(None)
        确保不会抛异常

原则2: Parquet Date 列保留各 TF 原生格式
        分钟级: 带时区后缀 (匹配DB)
        日线级: 无时区后缀 (匹配DB)

原则3: 合成bar的Date使用与DB同TF记录一致的格式
        从DB查询结果提取tz_suffix → 合成bar拼接同后缀
```

### 3.3 具体实现

```python
def _ensure_tz_naive(ts: pd.Timestamp) -> pd.Timestamp:
    """将Timestamp转为tz-naive。如果是tz-aware，剥离时区信息(保持钟表时间)。"""
    if ts.tz is not None:
        return ts.tz_localize(None)  # 剥离标记，保持本地钟表时间
    return ts

def _get_tz_suffix(ts_str: str) -> str:
    """从时间戳字符串提取时区后缀。"""
    if '+' in ts_str:
        return ts_str[ts_str.index('+'):]
    if ts_str.endswith('Z'):
        return 'Z'
    import re
    m = re.search(r'-\d{2}:\d{2}$', ts_str)
    return m.group(0) if m else ''

def _format_synth_date(cutoff_date: str, tf: str, db_rows: list[dict]) -> str:
    """格式化合成bar的Date字符串，匹配该TF的DB原生格式。"""
    dt = pd.Timestamp(cutoff_date)
    if dt.tz is not None:
        dt = dt.tz_localize(None)
    base = dt.isoformat()
    if db_rows:
        tz = _get_tz_suffix(db_rows[0]["Date"])
        if tz:
            return base + tz
    return base
```

### 3.4 时间格式对照表（v2统一后）

| 上下文 | 格式 | 时区 | 示例 |
|--------|------|------|------|
| `_ensure_tz_naive()` 之后 | `pd.Timestamp` | **无** | `Timestamp('2026-07-03T14:47:00')` |
| `_get_period_start_ts()` 返回值 | `pd.Timestamp` | **无** | `Timestamp('2026-07-03T15:00:00')` |
| `_get_query_start_for_synthesis()` 返回值 | `pd.Timestamp` | **无** | `Timestamp('2026-07-03T14:01:00')` |
| Parquet Date (分钟级TF) | `str` | **有** | `"2026-07-03T14:47:00+08:00"` |
| Parquet Date (日线级TF) | `str` | **无** | `"2026-07-03T14:47:00"` |
| SQL WHERE ts | `str` | 原始 | 字符串比较，ISO 8601字典序=时间序 |

---

## 4. 核心函数设计

### 4.1 函数清单

| 函数 | 类型 | 职责 |
|------|------|------|
| `_sync_all_cascading()` | **新增 — 主入口** | 级联合成调度，按TF顺序处理 |
| `_query_tf_from_db()` | 新增 | 查询DB中某TF的已完成bar（显示用） |
| `_query_tf_for_period()` | **v2新增** | 查询指定时间范围内的DB数据（用于合成） |
| `_needs_synthesis()` | 新增 | 判断是否需要合成不完整bar |
| `_get_period_start_ts()` | **v2重写** | 计算下一周期的开始时间戳 |
| `_get_query_start_for_synthesis()` | **v2修改** | 计算合成数据查询起点 |
| `_find_immediate_finer_tf()` | **v2新增** | 找紧邻更细一级TF |
| `_synthesize_incomplete_bar()` | **v2重写** | 从更细TF数据合成单条bar |
| `_aggregate_bars()` | 新增 | OHLCV聚合 |
| `_ensure_tz_naive()` | **v2新增** | 剥离Timestamp时区 |
| `_format_synth_date()` | **v2新增** | 格式化合成bar的Date字符串 |
| `_build_output_df()` | 新增 | 合并DB数据+合成bar→DataFrame |
| `_write_parquet()` | 新增 | 写入parquet文件 |

### 4.2 主入口: `_sync_all_cascading()`

**位置**: `filter_app/services/data_loader.py`

```python
def _sync_all_cascading(
    ticker_code: str,        # 股票代码
    tfs: list[str],          # 需处理的TF列表(已去重, 从细到粗排序)
    cutoff_date: str,        # ISO格式截止日期
    min_tf: str,             # 4个视图中最细周期
    n_pts: int = 120,        # 每个TF保留的显示bar数
) -> dict[str, bool]:
    """级联合成所有TF的数据并写入parquet。

    按TF从细到粗的顺序逐级处理。每级合成时重新查询更细TF的DB数据
    (而非依赖120条显示窗口)，确保合成数据的完整性。

    返回: {tf: bool} — 每个TF是否成功写入parquet
    """
    results: dict[str, bool] = {}
    synth_cache: dict[str, dict] = {}  # tf → {synth_bar, db_sample_ts}

    for tf in tfs:
        # ── ① 从DB查询该TF的已完成bar(用于显示) ──
        db_rows = _query_tf_from_db(ticker_code, tf, cutoff_date, n_pts)
        if not db_rows:
            results[tf] = False
            continue

        # ── ② 判断是否需要合成 ──
        needs_synth = (
            tf != min_tf
            and _needs_synthesis(tf, db_rows, cutoff_date)
        )

        synthesized_bar = None
        if needs_synth:
            finer_tf = _find_immediate_finer_tf(tf, tfs)
            if finer_tf and finer_tf in synth_cache:
                synthesized_bar = _synthesize_incomplete_bar(
                    target_tf=tf,
                    db_rows=db_rows,
                    cutoff_date=cutoff_date,
                    ticker_code=ticker_code,
                    finer_tf=finer_tf,
                    finer_synth_bar=synth_cache[finer_tf]["synth_bar"],
                )

        # ── ⑤ 构建输出DataFrame并写入parquet ──
        combined = _build_output_df(db_rows, synthesized_bar, n_pts)
        ok = _write_parquet(tf, combined)
        results[tf] = ok

        # ── ⑥ 缓存合成bar供下一级使用 ──
        synth_cache[tf] = {
            "synth_bar": synthesized_bar,
            "db_sample_ts": db_rows[0]["Date"] if db_rows else "",
        }

    return results
```

### 4.3 辅助函数: 数据查询

```python
def _query_tf_from_db(ticker_code, tf, cutoff_date, n_pts):
    """查询指定TF在cutoff_date之前的最后n_pts条已完成bar(用于显示)。"""
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
    return [
        {"Date": r[0], "Open": r[1], "High": r[2],
         "Low": r[3], "Close": r[4], "Volume": r[5]}
        for r in reversed(rows)
    ]

def _query_tf_for_period(ticker_code, tf, period_start, period_end):
    """查询指定时间范围内的所有bar(用于合成,不受n_pts限制)。v2新增。"""
    from db import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT ts, open, high, low, close, volume
               FROM kline WHERE ticker=? AND timeframe=?
               AND ts >= ? AND ts <= ?
               ORDER BY ts ASC""",
            (ticker_code, tf, period_start, period_end),
        ).fetchall()
    return [
        {"Date": r[0], "Open": r[1], "High": r[2],
         "Low": r[3], "Close": r[4], "Volume": r[5]}
        for r in rows
    ]

def _find_immediate_finer_tf(tf, tfs):
    """在tfs列表中找紧邻当前TF的更细一级TF。"""
    current_idx = ALL_TFS.index(tf)
    for candidate_tf in reversed(tfs):
        if ALL_TFS.index(candidate_tf) < current_idx:
            return candidate_tf
    return None
```

### 4.4 辅助函数: 合成判断

```python
def _needs_synthesis(tf, db_rows, cutoff_date):
    """判断是否需要为给定TF合成不完整bar。

    检查 cutoff_date 是否已进入该TF的下一个周期。
    所有 Timestamp 比较前统一剥离时区。
    """
    last_ts_str = db_rows[-1]["Date"]
    last_ts = pd.Timestamp(last_ts_str)
    last_ts = _ensure_tz_naive(last_ts)

    period_start = _get_period_start_ts(last_ts, tf)
    cutoff_dt = pd.Timestamp(cutoff_date)
    cutoff_dt = _ensure_tz_naive(cutoff_dt)

    return cutoff_dt >= period_start
```

### 4.5 周期开始计算 (v2重写)

```python
def _get_period_start_ts(ts, tf):
    """计算最后一条已完成bar所在周期结束后的下一个周期开始时间戳。
    
    ★ v2核心修复: 周线改为 last_ts + 1天(非下一周五), 月线/季线简化。
    """
    if tf in ("5分钟", "15分钟", "60分钟"):
        return ts + pd.Timedelta(minutes=1)
    elif tf == "日线":
        return (ts + pd.Timedelta(days=1)).normalize()
    elif tf in ("周线", "月线", "季线"):
        # 周线: ts是周五, +1天=周六(period_start是下一天,不是下周五)
        # 月线: ts是月末最后一天, +1天=下月1日
        # 季线: ts是季末最后一天, +1天=下季第一天
        return (ts + pd.Timedelta(days=1)).normalize()
    else:
        return ts + pd.Timedelta(minutes=1)

def _get_query_start_for_synthesis(last_completed_ts, tf):
    """计算合成时从更细TF数据中查询的起点时间戳。与 _get_period_start_ts 逻辑一致。"""
    if tf in ("5分钟", "15分钟", "60分钟"):
        return last_completed_ts + pd.Timedelta(minutes=1)
    elif tf in ("日线", "周线", "月线", "季线"):
        return (last_completed_ts + pd.Timedelta(days=1)).normalize()
    else:
        return last_completed_ts + pd.Timedelta(minutes=1)
```

### 4.6 核心: `_synthesize_incomplete_bar()` (v2重写)

```python
def _synthesize_incomplete_bar(
    target_tf, db_rows, cutoff_date,
    ticker_code, finer_tf, finer_synth_bar
):
    """从更细TF的数据合成目标TF的一条不完整bar。

    ★ v2关键改进:
    1. 不依赖prev_data(120条窗口), 重新查询DB获取合成期内所有finer_tf bar
    2. 将finer_tf的DB查询结果与finer_tf的合成bar拼接
    3. 时间比较前统一剥离时区
    """
    # ── ① 计算查询范围 ──
    last_ts_str = db_rows[-1]["Date"]
    last_ts = pd.Timestamp(last_ts_str)
    last_ts = _ensure_tz_naive(last_ts)

    query_start = _get_query_start_for_synthesis(last_ts, target_tf)
    cutoff_dt = pd.Timestamp(cutoff_date)
    cutoff_dt = _ensure_tz_naive(cutoff_dt)

    # ── ② 日线合成优化: 60分钟查询从09:30开始 ──
    actual_start = query_start
    if target_tf == "日线" and finer_tf == "60分钟":
        market_open = query_start.replace(hour=9, minute=30, second=0, microsecond=0)
        if query_start < market_open < cutoff_dt:
            actual_start = market_open

    # ── ③ 查询更细TF的DB数据 ──
    period_start_str = actual_start.isoformat()
    period_end_str = cutoff_dt.isoformat()
    finer_db_bars = _query_tf_for_period(
        ticker_code, finer_tf, period_start_str, period_end_str
    )

    # ── ④ 拼接更细TF的合成bar ──
    all_finer_bars = list(finer_db_bars)
    if finer_synth_bar is not None:
        synth_ts = pd.Timestamp(finer_synth_bar["Date"])
        synth_ts = _ensure_tz_naive(synth_ts)
        if actual_start <= synth_ts <= cutoff_dt:
            existing_ts = {_ensure_tz_naive(pd.Timestamp(b["Date"])) for b in all_finer_bars}
            if synth_ts not in existing_ts:
                all_finer_bars.append(finer_synth_bar)
                all_finer_bars.sort(
                    key=lambda b: _ensure_tz_naive(pd.Timestamp(b["Date"]))
                )

    if len(all_finer_bars) == 0:
        return None

    # ── ⑤ 构建合成bar ──
    synth_date = _format_synth_date(cutoff_date, target_tf, db_rows)
    df = pd.DataFrame(all_finer_bars)
    return {
        "Date": synth_date,
        "Open": float(df["Open"].iloc[0]),
        "High": float(df["High"].max()),
        "Low": float(df["Low"].min()),
        "Close": float(df["Close"].iloc[-1]),
        "Volume": float(df["Volume"].sum()),
    }
```

### 4.7 辅助函数: 聚合与输出

```python
def _aggregate_bars(finer_bars, target_tf, synth_date):
    """从更细TF的bar列表聚合成一条目标TF的bar。

    OHLCV聚合规则(所有TF通用):
      Open  = 第一条bar的Open
      High  = 所有bar的High的最大值
      Low   = 所有bar的Low的最小值
      Close = 最后一条bar的Close
      Volume = 所有bar的Volume之和
      Date  = synth_date(由调用方决定)
    """
    if len(finer_bars) == 0:
        return None
    df = pd.DataFrame(finer_bars)
    return {
        "Date": synth_date,
        "Open": float(df["Open"].iloc[0]),
        "High": float(df["High"].max()),
        "Low": float(df["Low"].min()),
        "Close": float(df["Close"].iloc[-1]),
        "Volume": float(df["Volume"].sum()),
    }

def _build_output_df(db_rows, synthesized_bar, n_pts):
    """将DB已完成bar + 合成bar合并为DataFrame, 截断到n_pts。"""
    data = list(db_rows)
    if synthesized_bar is not None:
        data.append(synthesized_bar)
    df = pd.DataFrame(data)
    if len(df) > n_pts:
        df = df.iloc[-n_pts:]
    return df.reset_index(drop=True)

def _write_parquet(tf, df):
    """将DataFrame写入该TF的parquet文件。"""
    display_dir = Path(__file__).parent.parent.parent / "data" / "display"
    display_dir.mkdir(parents=True, exist_ok=True)
    try:
        filepath = display_dir / f"{tf}.parquet"
        df.to_parquet(filepath, index=False)
        return True
    except Exception:
        return False
```

### 4.8 `_load_chart_data()` 修改

```python
def _load_chart_data(market, ticker_code, tf, day_offset, n_pts,
                     window_start=None, cutoff_date=None):
    if window_start is not None:
        # 回测模式: parquet已由main()中的_sync_all_cascading()准备好
        pass
    else:
        # 浏览模式: 保持原有逻辑不变
        ok, count = _sync_to_display(ticker_code, tf, day_offset=day_offset,
                                      n_pts=n_pts)
        if not ok:
            return _cached_fetch_stock(market, ticker_code, tf, n_pts)

    # 共用: 读取parquet
    display_path = Path(__file__).parent.parent / "data" / "display" / f"{tf}.parquet"
    err = None
    if display_path.exists():
        try:
            df = pd.read_parquet(display_path)
            if "Date" in df.columns and "Close" in df.columns and len(df) >= 2:
                df["Date"] = pd.to_datetime(df["Date"])
                df = df.set_index("Date").sort_index()
                # ... 后续处理不变 ...
                return t, noisy, ohlc, ticker_code, df.index, None
        except Exception as e:
            err = str(e)

    # 降级: parquet不存在或读取失败 → API回退
    return _cached_fetch_stock(market, ticker_code, tf, n_pts)
```

### 4.9 `main()` 修改

```python
# 回测模式: 级联合成预处理
cb_mode = AppState.get("_cb_mode", False)
if cb_mode and ticker_code:
    cutoff_date = AppState.get("_bt_cutoff_date", "")
    min_tf = AppState.get("_min_tf", "")
    if cutoff_date and min_tf:
        if not has_data(ticker_code):
            st.warning("回测数据未就绪，请先在浏览模式加载数据")
        else:
            unique_tfs = sorted(
                set(cfg["tf"] for cfg in configs),
                key=lambda tf: ALL_TFS.index(tf)
            )
            min_n_pts = min(cfg["n_pts"] for cfg in configs) if configs else 120
            _sync_all_cascading(ticker_code, unique_tfs, cutoff_date, min_tf, min_n_pts)
```

---

## 5. 各周期合成规则

### 5.1 统一聚合公式

所有 TF 对使用相同的 OHLCV 聚合规则:

```
合成bar.Open   = 第一条更细bar.Open    (周期内最早的价格)
合成bar.High   = MAX(所有更细bar.High) (周期内最高价)
合成bar.Low    = MIN(所有更细bar.Low)  (周期内最低价)
合成bar.Close  = 最后一条更细bar.Close  (周期内最新价格)
合成bar.Volume = SUM(所有更细bar.Volume) (周期内总成交量)
合成bar.Date   = cutoff_date (格式匹配该TF的DB原生格式)
```

### 5.2 各TF处理规则总表

| 序号 | TF | 是否需要合成 | 数据源 | period_start | 特殊处理 |
|------|-----|------------|--------|-------------|---------|
| 0 | 1分钟 | **否** (如=min_tf) | N/A | N/A | 从不合成 |
| 1 | 5分钟 | 是 (如有更细TF) | 最细可用分钟TF | `last_ts + 1分钟` | — |
| 2 | 15分钟 | 是 (如有更细TF) | 最细可用分钟TF | `last_ts + 1分钟` | — |
| 3 | 60分钟 | 是 (如有更细TF) | 最细可用分钟TF | `last_ts + 1分钟` | — |
| 4 | 日线 | 是 (如有分钟TF) | 最细可用分钟TF | `last_ts + 1天` | 60分钟源从09:30开始 |
| 5 | 周线 | 是 (如有日线) | 日线 | `last_ts + 1天` | 跨周末自然无数据 |
| 6 | 月线 | 是 (如有日线/周线) | 最细可用日线级TF | `last_ts + 1天` | 跨月自然切换 |
| 7 | 季线 | 是 (如有日/周/月) | 最细可用日线级TF | `last_ts + 1天` | 跨季自然切换 |

### 5.3 逐TF详解

各 TF 的详细合成示例参见 v2 原文 §4.3.1-4.3.8。

### 5.4 级联链路示例

假设 `min_tf="15分钟"`, 所有8个TF都在视图中:

```
处理顺序: 1分钟 → 5分钟 → 15分钟 → 60分钟 → 日线 → 周线 → 月线 → 季线

15分钟: DB查询 → parquet (tf=min_tf, 不合成)
         synth_cache["15分钟"] = {"synth_bar": None}

60分钟: DB查询 → 从15分钟合成
         finer_tf = "15分钟"
         查询15分钟DB [14:01, 14:47] + 拼接15分钟合成bar(None)

日线:   DB查询 → 从60分钟合成
         finer_tf = "60分钟"
         查询60分钟DB [07-03T00:00:00, 14:47] + 拼接60分钟合成bar

周线:   DB查询 → 从日线合成
         finer_tf = "日线"
         查询日线DB [06-27, 07-03T14:47] + 拼接日线合成bar

月线:   DB查询 → 从日线合成 (跳过不存在的周线)
         如果周线在tfs中, 则从周线合成

季线:   DB查询 → 从月线合成
```

---

## 6. 边界条件处理

### 6.1 场景汇总

| 场景 | 处理方式 |
|------|---------|
| min_tf=日线(无分钟数据) | `tf == min_tf` 不合成, 周/月/季线从日线合成 |
| 中间TF缺失(如无周线) | `_find_immediate_finer_tf` 跳过缺失TF |
| 数据不足 | `_synthesize_incomplete_bar` 返回 None, 不合成 |
| cutoff_date=周期边界 | 字符串比较自动处理: DB有完整bar则不合成 |
| 跨周末/节假日 | 查询自然返回空, 聚合使用实际数据 |
| 刚切换ticker | `has_data()` 前置检查 + API回退 |
| 同一TF多个视图 | `unique_tfs = set(...)` 自动去重 |
| 并发读取parquet | 只读, `pd.read_parquet()` 线程安全 |

---

## 7. 数据流图

### 7.1 级联合成全景 (v2)

```
main() — 回测模式入口
  │
  ├─ _sync_all_cascading()  ← 前置集中处理（fragment渲染之前）
  │     │
  │     ├─ TF=15分钟: DB查询 → parquet写入 (不合成，=min_tf)
  │     ├─ TF=60分钟: DB查询 + 从15分钟DB+合成bar合成 → parquet
  │     ├─ TF=日线:   DB查询 + 从60分钟DB+合成bar合成 → parquet
  │     └─ TF=周线:   DB查询 + 从日线DB+合成bar合成 → parquet
  │
  └─ 4个 _render_chart_fragment() — 并行读取parquet（只读，无冲突）
        └─ _load_chart_data() → pd.read_parquet({tf}.parquet)
```

### 7.2 数据查询路径 (v2 vs v1)

```
v1 (错误路径):
  prev_data (120条窗口) ──→ 过滤 ──→ 合成
  问题: 120条窗口可能不够、依赖显示数据

v2 (正确路径):
  合成目标TF时:
    ├─ _query_tf_from_db(target_tf, ...)  ──→ 120条显示数据 ──→ parquet
    └─ _query_tf_for_period(finer_tf, period_start, period_end)
         ──→ 合成期完整数据 + finer_tf的synth_bar ──→ 聚合 ──→ 合成bar ──→ parquet

  显示数据和合成数据完全分离, 互不依赖.
```

### 7.3 合成bar Date格式决策树

```
合成bar需要Date字段
  ├─ 目标TF是分钟级(1m/5m/15m/60m)?
  │    └─ YES → 保留 tz_suffix → "2026-07-03T14:47:00+08:00"
  └─ 目标TF是日线级(日/周/月/季)?
       └─ YES → 剥离时区 → "2026-07-03T14:47:00"
```

---

## 8. 实施计划

### 8.1 分步实施

```
Phase 1: 基础工具函数 (低风险)
  ├─ _ensure_tz_naive() / _get_tz_suffix() / _format_synth_date()
  ├─ ALL_TFS 常量
  └─ 验证: 单元测试

Phase 2: 周期计算函数 (中等风险)
  ├─ _get_period_start_ts() / _get_query_start_for_synthesis()
  ├─ _needs_synthesis()
  └─ 验证: 参数化测试覆盖全部8个TF+边界

Phase 3: 合成核心 (中等风险)
  ├─ _query_tf_from_db() / _query_tf_for_period()
  ├─ _find_immediate_finer_tf() / _aggregate_bars()
  ├─ _synthesize_incomplete_bar() / _build_output_df()
  ├─ _write_parquet() / _sync_all_cascading()
  └─ 验证: mock数据模拟级联

Phase 4: Streamlit集成 (需谨慎)
  ├─ _load_chart_data() 回测模式跳过 _sync_to_display
  ├─ main() 添加 _sync_all_cascading 调用
  ├─ has_data() 检查
  └─ 验证: 端到端回测场景

Phase 5: 降级与边界
  ├─ _load_chart_data 降级: parquet不存在→API回退
  ├─ 日线合成60分钟09:30调整
  └─ 验证: 各种边界场景
```

### 8.2 关键测试用例

参见 v2 §7.2 的完整测试用例覆盖（时区/周期/级联三个维度）。

### 8.3 实施状态

| Phase | 内容 | 状态 |
|-------|------|------|
| 1 | 时间格式工具 | ✅ 已实现 |
| 1 | 周期边界计算 | ✅ 已实现 |
| 2 | 数据查询函数 | ✅ 已实现 |
| 2 | 合成核心 | ✅ 已实现 |
| 3 | 级联主入口 | ✅ 已实现 |
| 4 | Streamlit 集成 | ✅ 已实现 |
| 5 | 单元测试 | ✅ 已实现 |
| - | 冒烟验证 | ✅ 通过 (33项核心逻辑测试) |

### 8.4 回滚策略

```python
# 回滚步骤:
# 1. main()中移除 _sync_all_cascading() 调用
# 2. _load_chart_data()中恢复原有的 _sync_to_display() 调用
# 3. 删除 data_loader.py 中新增的函数
```

---

## 9. 兼容性影响

### 9.1 函数签名变更

| 函数 | v1状态 | v2状态 | 影响范围 |
|------|--------|--------|---------|
| `_sync_all_cascading()` | 新增 | 新增 (签名不变) | `streamlit_app.py:main()` |
| `_needs_synthesis()` | 新增 | 修改 (内部添加_ensure_tz_naive) | 仅内部 |
| `_get_period_start_ts()` | 新增 | **重写** (周/月/季逻辑) | 仅内部 |
| `_synthesize_incomplete_bar()` | 新增 | **重写** (参数+逻辑) | 仅内部 |
| `_query_tf_for_period()` | 无 | **v2新增** | 仅内部 |
| `_find_immediate_finer_tf()` | 无 | **v2新增** | 仅内部 |
| `_ensure_tz_naive()` | 无 | **v2新增** | 仅内部 |
| `_format_synth_date()` | 无 | **v2新增** | 仅内部 |
| `_sync_to_display()` | 现有 | 不变 | 浏览模式 |
| `_load_chart_data()` | 现有 | 修改 (回测分支) | `streamlit_app.py` |

### 9.2 完全不受影响的部分

- 浏览模式: 代码路径完全不变
- `db.py`, `state.py`, `components/sidebar.py`, `components/charts.py`, `backtest_logger.py`
- 现有测试: 全部兼容

### 9.3 调用方变更

| 文件 | 位置 | 变更 |
|------|------|------|
| `streamlit_app.py` | `_load_chart_data()` 回测分支 | 移除 `_sync_to_display()` 调用, 改为直接读parquet |
| `streamlit_app.py` | `main()` sorted_views 之前 | 新增 `has_data()` 检查 + `_sync_all_cascading()` 调用 |
| `data_loader.py` | 顶部 | 新增 `ALL_TFS` 常量 |
| `data_loader.py` | 新增(~200行) | 级联合成全部核心函数 |

---

## 10. 附录: v1->v2 变更清单

### A.1 P0 修复 (阻断性)

| # | 问题 | v1行为 | v2修复 |
|---|------|--------|--------|
| P0-1 | tz-aware vs tz-naive TypeError | 直接比较 Timestamp, 可能抛异常 | 所有比较前 `_ensure_tz_naive()` |
| P0-2 | 周线 period_start 错误 | 返回下一周五 | 改为 `last_ts + 1天` |
| P0-3 | Parquet Date 混合时区 | 合成bar Date 与 DB 日线格式混合 | `_format_synth_date()` 按TF统一格式 |

### A.2 P1 修复 (功能正确性)

| # | 问题 | v1行为 | v2修复 |
|---|------|--------|--------|
| P1-1 | 合成数据来源 | 从 prev_data(120条) 过滤 | 重新查询DB + 拼接合成bar |
| P1-2 | 耦合 | prev_data 同时用于显示和合成 | synth_cache 只存合成bar, 数据分离 |
| P1-3 | 日线合成查询起点 | 从00:00开始 | 调整为09:30 |
| P1-4 | 降级路径 | parquet不存在抛错 | 回退到 API |
| P1-5 | 并发安全 | 4个fragment可能同时读写 | 写入在fragment之前完成 |
| P1-6 | 函数命名 | 两个相似函数不同返回值 | 明确命名和职责 |

### A.3 P2 改进 (健壮性)

| # | 改进 | 处理方式 |
|---|------|---------|
| P2-1 | min_tf=日线场景 | `_find_immediate_finer_tf` 自然处理 |
| P2-2 | 跨周末 | 查询自然返回空 |
| P2-3 | 刚切换ticker | `has_data()` 前置检查 + 回退 |
| P2-4 | 数据不足 | 返回 None, 不合成 |

### A.4 简化与优化

| 项目 | v1 | v2 |
|------|----|----|
| `_get_period_start_ts` 月线 | 复杂月份计算(8行) | `ts + 1天` (1行) |
| `_get_period_start_ts` 季线 | 复杂季度计算(8行) | `ts + 1天` (1行) |
| `prev_data` 变量 | 存储完整DataFrame | `synth_cache[tf]` 只存合成bar |

---

## 11. 附录: v1 审查报告结论

**来源**: `design-review.md` (597行)

### 11.1 问题统计

| 等级 | 数量 | 描述 |
|------|------|------|
| **严重** (阻断性) | 3 | 混合时区, tz-aware vs tz-naive TypeError, 周线周期计算错误 |
| **中等** (可能出错) | 6 | 函数命名混淆, 窗口大小, 解析容错, 语义问题, 空数据降级, 无降级路径 |
| **轻微** (边界条件) | 7 | 格式/边界/数据不足等 |

### 11.2 必须修复才能工作的问题

1. **时区比较 TypeError (1.3)**: `_needs_synthesis()` 和 `_synthesize_incomplete_bar()` 中 tz-aware 与 tz-naive Timestamp 的比较会直接抛异常。
2. **周线周期计算 (2.1)**: `_get_period_start_ts()` 对周线返回下一个周五（period END）而非 period START，导致周线合成永远不触发。
3. **混合时区 parquet (1.2)**: 日线 parquet 中正常 bar (tz-naive) 与合成 bar (tz-aware) 混合，`pd.to_datetime()` 行为不确定。

### 11.3 可以工作的部分

- 级联合成的总体架构（内存传递 A2 方案）设计合理
- 分钟级合成逻辑正确
- 浏览模式代码路径完全不受影响
- 同一 TF 多视图去重逻辑正确

### 11.4 建议的修复优先级

```
P0 (立即修复，否则无法运行):
  1. 修复 _get_period_start_ts 周线逻辑 (2.1)
  2. 修复 tz-aware vs tz-naive TypeError (1.3)
  3. 统一 parquet Date 格式 (1.2)

P1 (Phase 3 前修复，否则可能出错):
  4. 添加 _load_chart_data 降级路径 (4.1)
  5. 合并/重命名两个 period_start 函数 (1.1)
  6. 添加数据就绪检查 (3.2)

P2 (Phase 4 前修复，改善健壮性):
  7. 提取 ALL_TFS 到共享模块 (4.3)
  8. 添加合成 bar 标记字段 (2.5)
  9. 区分浏览/回测 parquet 文件 (4.4)
  10. 补充跨年/跨月边界测试 (3.5)
```

### 11.5 关键时间格式对照表

| 位置 | 格式 | 时区 | 示例 |
|------|------|------|------|
| DB kline.ts (分钟级) | ISO 8601 str | **有** (+08:00 或 Z) | `2026-07-03T14:00:00+08:00` |
| DB kline.ts (日线+) | ISO 8601 str | **无** | `2026-07-03T00:00:00` |
| cutoff_date | ISO 8601 str | **有** | `2026-07-03T14:47:00+08:00` |
| `_get_period_start_ts` 返回 | pd.Timestamp | **无** | `Timestamp('2026-07-03T00:00:00')` |
| 合成 bar Date (设计) | str = cutoff_date | **有** | `"2026-07-03T14:47:00+08:00"` |

**核心矛盾**: 来自 min_tf 的 `cutoff_date` 和 `_get_period_start_ts` 的返回值时区不一致，且 parquet 中不同 TF 的 Date 格式混合。

---

## 12. 附录: v2 再审报告结论

**来源**: `design-review-v2.md` (654行)

### 12.1 审查结论

| 维度 | v1 状态 | v2 状态 |
|------|---------|---------|
| P0 问题 (3个) | 阻断 | **3/3 已修复** |
| P1 问题 (6个) | 功能不正确 | **6/6 已修复** |
| v2 新增逻辑自洽性 | N/A | **4/4 通过** |
| 新发现问题 | N/A | **3个 (P1x1, P2x2)** |
| 综合判定 | — | **条件通过** — 需验证 yfinance 周/月/季线 ts 实际格式后再实施 |

### 12.2 P0 修复验证

| 检查项 | v2 位置 | 结论 |
|--------|---------|------|
| 统一的时区处理策略 | §2.2 "比较前剥离，写入时保留" | 通过 |
| `_ensure_tz_naive()` 在所有比较前调用 | `_needs_synthesis()` 和 `_synthesize_incomplete_bar()` | 通过 |
| 周线 period_start 修复 | `_get_period_start_ts()` 统一改为 `last_ts + 1天` | 通过 |
| Parquet Date 统一 | `_format_synth_date()` 按TF自动匹配 | 通过 |

### 12.3 v2 新发现问题

| 编号 | 严重度 | 描述 | 建议 |
|------|--------|------|------|
| N1 | P1 | yfinance timestamp 格式未验证 | Phase 0 增加 yfinance 验证, 确认周/月/季线实际 ts 格式 |
| N2 | P2 | `immediate finer` 语义可能不是最优 | 接受, 在函数文档中说明 |
| N3 | P2 | 09:30 硬编码仅适用 A 股 | 未来扩展到其他市场时参数化 |

### 12.4 最终判定

**v2 可以进入实施阶段: YES（条件通过）**

**前置条件**: Phase 2 (周期计算) 实施前，必须先验证 yfinance 对周线/月线/季线的实际 timestamp 格式。

**建议实施顺序微调**:
```
Phase 0 (新增): yfinance timestamp 格式验证 (15分钟)
  └─ 验证: 打印周/月/季线 yfinance.download() 返回的 index 值
  └─ 根据结果决定 _get_period_start_ts 周/月/季线分支是否需要调整
```

**v2 设计亮点**:
1. 时区处理策略 "比较前剥离，写入时保留": 清晰、一致、可测试
2. `_format_synth_date()` 设计: 自动匹配 TF 原生格式
3. `synth_cache` 单条传递: 极简设计，避免 DataFrame 复制开销
4. 显示/合成数据完全解耦: 各自独立查询，互不干扰
5. 月线/季线计算简化: 从 8 行复杂月份计算简化为 `ts + 1天`

### 12.5 实施后修复的 Bug

| Bug | 提交 | 根因 | 修复 |
|-----|------|------|------|
| ALL_TFS 导入错误 | 086effc | import 路径循环依赖 | 改回本地常量 |
| _write_parquet 路径 | e040310 | parent少一层 | 统一 parent.parent.parent |
| P1 x5 | e92a147 | n_pts/返回值/链断裂/回退/参数 | 见 all-issues-tracker.md |
| 时区边界 bar 遗漏 | d6f5839 | SQL字符串比较 tz后缀 | period_end 加 tz_suffix |
| 日线级反向边界 | 2a2599b | finer_tf格式不匹配 | 从 finer_tf 提取时区 |
| 跨时区假转换 | a87cea5 | 剥离-拼接 替代真转换 | tz_convert() |
| _needs_synthesis 日线失效 | 3bf8a47 | cutoff >= 下一周期 | cutoff > last_ts |
| _get_query_start_for_synthesis 窗口颠倒 | fec408a | last_ts+1天 超出cutoff | 返回 last_ts |

---

> **文档版本**: v2.0 (合并版) | **来源文件**: cascading-synthesis-design.md, cascading-synthesis-v2.md, design-review.md, design-review-v2.md
