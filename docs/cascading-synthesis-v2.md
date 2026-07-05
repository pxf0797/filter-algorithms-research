# 回测数据级联合成方案 v2

> **作者**: 量化系统架构师 | **日期**: 2026-07-05 | **版本**: v2.0
> **基于**: v1.0 审查报告 (design-review.md) — 16 个问题全部修复
> **代码库**: `filter_app/services/data_loader.py` + `filter_app/db.py` + `streamlit_app.py`

---

## 目录

1. [架构概览](#1-架构概览)
2. [时间格式统一策略](#2-时间格式统一策略)
3. [核心函数设计](#3-核心函数设计)
4. [各周期合成规则](#4-各周期合成规则)
5. [边界条件处理](#5-边界条件处理)
6. [数据流图](#6-数据流图)
7. [实施计划](#7-实施计划)
8. [兼容性影响](#8-兼容性影响)
9. [附录: v1-v2 变更清单](#附录-v1v2-变更清单)

---

## 1. 架构概览

### 1.1 核心设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 合成bar传递方式 | **A2 — 内存传递** | 零DB污染、无并发问题、无生命周期管理 |
| 处理时机 | **前置集中处理** — `main()` 中 fragment 渲染前一次性执行 | 保证TF处理顺序、避免4个视图重复查询 |
| 合成bar时间戳 | **cutoff_date** | 确保 `ts <= cutoff_date` 过滤自然匹配、图表位置正确 |
| 合成数据来源 | **DB查询 + 更细TF合成bar拼接** (v2改进) | 不依赖120条显示窗口、合成期数据完整 |
| 周期开始计算 | **统一 `last_ts + 1单位`** (v2修复) | 消除周线/月线/季线计算错误 |
| 时区处理 | **比较前剥离、写入时保留** (v2新增) | 消除 `TypeError: Cannot compare tz-naive and tz-aware timestamps` |

### 1.2 整体架构图

```
main() — 回测模式入口
  │
  ├─ ① _sync_all_cascading()  ← 前置集中处理（fragment渲染之前）
  │     │
  │     ├─ TF=15分钟: DB查询 → parquet写入 (不合成，=min_tf)
  │     ├─ TF=60分钟: DB查询 + 从15分钟DB+合成bar合成 → parquet
  │     ├─ TF=日线:   DB查询 + 从60分钟DB+合成bar合成 → parquet
  │     └─ TF=周线:   DB查询 + 从日线DB+合成bar合成 → parquet
  │
  └─ ② 4个 _render_chart_fragment() — 并行读取parquet（只读，无冲突）
        └─ _load_chart_data() → pd.read_parquet({tf}.parquet)
```

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

## 2. 时间格式统一策略

### 2.1 问题根源

DB 中不同 TF 的时间戳格式不一致（由 `yfinance` 行为决定）：

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

### 2.2 统一策略: "比较前剥离，写入时保留"

```
┌──────────────────────────────────────────────────────────────────┐
│                      时间格式统一策略                              │
│                                                                  │
│  原则1: 所有 pd.Timestamp 比较前统一做 tz_localize(None)          │
│         确保所有 Timestamp 都是 tz-naive，比较不会抛异常            │
│                                                                  │
│  原则2: Parquet Date 列保留各 TF 原生格式                         │
│         分钟级: 带时区后缀 (匹配DB)                                │
│         日线级: 无时区后缀 (匹配DB)                                │
│                                                                  │
│  原则3: 合成bar的Date使用与DB同TF记录一致的格式                    │
│         从DB查询结果提取tz_suffix → 合成bar拼接同后缀              │
└──────────────────────────────────────────────────────────────────┘
```

### 2.3 具体实现规则

```python
# ═══════════════════════════════════════════════════════════════
# 规则1: 所有比较前剥离时区
# ═══════════════════════════════════════════════════════════════

def _ensure_tz_naive(ts: pd.Timestamp) -> pd.Timestamp:
    """将Timestamp转为tz-naive。如果是tz-aware，剥离时区信息(保持钟表时间)。"""
    if ts.tz is not None:
        return ts.tz_localize(None)  # tz_localize: 剥离标记，保持本地钟表时间
    return ts

# 在 _needs_synthesis() 中:
last_ts = _ensure_tz_naive(pd.Timestamp(db_rows[-1]["Date"]))
period_start = _get_period_start_ts(last_ts, tf)  # 返回 tz-naive
cutoff_dt = _ensure_tz_naive(pd.Timestamp(cutoff_date))
return cutoff_dt >= period_start  # ✓ 同类型比较

# 在 _synthesize_incomplete_bar() 中:
query_start = _get_query_start_for_synthesis(last_ts, tf)  # tz-naive
cutoff_dt = _ensure_tz_naive(pd.Timestamp(cutoff_date))
prev_dt = pd.to_datetime(prev_data["Date"])
prev_dt = prev_dt.apply(lambda x: _ensure_tz_naive(x) if hasattr(x, 'tz') else x)
mask = (prev_dt >= query_start) & (prev_dt <= cutoff_dt)  # ✓


# ═══════════════════════════════════════════════════════════════
# 规则2+3: 合成bar Date 匹配该TF的DB原生格式
# ═══════════════════════════════════════════════════════════════

def _get_tz_suffix(ts_str: str) -> str:
    """从时间戳字符串提取时区后缀。
    
    返回: '+08:00', 'Z', '+00:00', 或 '' (无时区)
    """
    if '+' in ts_str:
        return ts_str[ts_str.index('+'):]
    if ts_str.endswith('Z'):
        return 'Z'
    # 负偏移 e.g. '-05:00'
    import re
    m = re.search(r'-\d{2}:\d{2}$', ts_str)
    return m.group(0) if m else ''


def _format_synth_date(cutoff_date: str, tf: str, db_rows: list[dict]) -> str:
    """格式化合成bar的Date字符串，匹配该TF的DB原生格式。
    
    参数:
        cutoff_date: 截止日期 (可能带时区的ISO字符串)
        tf: 目标TF (决定是否保留时区)
        db_rows: 该TF的DB查询结果 (用于提取tz_suffix)
    
    返回:
        格式化后的Date字符串
        - 分钟级TF: "2026-07-03T14:47:00+08:00" (带时区)
        - 日线级TF: "2026-07-03T14:47:00" (无时区)
    """
    # 剥离时区得到纯净的本地时间
    dt = pd.Timestamp(cutoff_date)
    if dt.tz is not None:
        dt = dt.tz_localize(None)  # 剥离时区标记，保持钟表时间
    
    base = dt.isoformat()
    
    # 检查该TF的DB数据是否有时区后缀
    if db_rows:
        tz = _get_tz_suffix(db_rows[0]["Date"])
        if tz:
            return base + tz
    
    return base
```

### 2.4 时间格式对照表（v2统一后）

| 上下文 | 格式 | 时区 | 示例 |
|--------|------|------|------|
| `_ensure_tz_naive()` 之后 | `pd.Timestamp` | **无** | `Timestamp('2026-07-03T14:47:00')` |
| `_get_period_start_ts()` 返回值 | `pd.Timestamp` | **无** | `Timestamp('2026-07-03T15:00:00')` |
| `_get_query_start_for_synthesis()` 返回值 | `pd.Timestamp` | **无** | `Timestamp('2026-07-03T14:01:00')` |
| Parquet Date (分钟级TF) | `str` | **有** | `"2026-07-03T14:47:00+08:00"` |
| Parquet Date (日线级TF) | `str` | **无** | `"2026-07-03T14:47:00"` |
| SQL WHERE ts | `str` | 原始 | 字符串比较，ISO 8601字典序=时间序 |

---

## 3. 核心函数设计

### 3.1 函数清单

| 函数 | 类型 | 职责 |
|------|------|------|
| `_sync_all_cascading()` | **新增 — 主入口** | 级联合成调度，按TF顺序处理 |
| `_query_tf_from_db()` | 新增 | 查询DB中某TF的已完成bar |
| `_query_tf_for_period()` | **v2新增** | 查询指定时间范围内的DB数据(用于合成) |
| `_needs_synthesis()` | 新增 | 判断是否需要合成不完整bar |
| `_get_period_start_ts()` | **v2重写** | 计算下一周期的开始时间戳 |
| `_get_query_start_for_synthesis()` | **v2修改** | 计算合成数据查询起点 |
| `_synthesize_incomplete_bar()` | **v2重写** | 从更细TF数据合成单条bar |
| `_aggregate_bars()` | 新增 | OHLCV聚合 |
| `_ensure_tz_naive()` | **v2新增** | 剥离Timestamp时区 |
| `_format_synth_date()` | **v2新增** | 格式化合成bar的Date字符串 |
| `_build_output_df()` | 新增 | 合并DB数据+合成bar→DataFrame |
| `_write_parquet()` | 新增 | 写入parquet文件 |

### 3.2 主入口: `_sync_all_cascading()`

**位置**: `filter_app/services/data_loader.py` (新增)

```python
def _sync_all_cascading(
    ticker_code: str,        # 股票代码
    tfs: list[str],          # 需处理的TF列表(已去重, 从细到粗排序)
    cutoff_date: str,        # ISO格式截止日期, 如 "2026-07-03T14:47:00+08:00"
    min_tf: str,             # 4个视图中最细周期
    n_pts: int = 120,        # 每个TF保留的显示bar数
) -> dict[str, bool]:
    """级联合成所有TF的数据并写入parquet。
    
    按TF从细到粗的顺序逐级处理。每级合成时重新查询更细TF的DB数据
    (而非依赖120条显示窗口)，确保合成数据的完整性。
    
    返回: {tf: bool} — 每个TF是否成功写入parquet
    """
    results: dict[str, bool] = {}
    
    # 缓存每级合成bar，供下一级拼接使用
    # tf → {"synth_bar": dict|None, "db_sample_ts": str}
    synth_cache: dict[str, dict] = {}
    
    for tf in tfs:
        # ── ① 从DB查询该TF的已完成bar(用于显示) ──
        db_rows = _query_tf_from_db(ticker_code, tf, cutoff_date, n_pts)
        if not db_rows:
            logger.debug(f"[_sync_all_cascading] {tf}: no DB data, skip")
            results[tf] = False
            continue
        
        # ── ② 判断是否需要合成 ──
        needs_synth = (
            tf != min_tf                              # min_tf不合成
            and _needs_synthesis(tf, db_rows, cutoff_date)
        )
        
        synthesized_bar: Optional[dict] = None
        if needs_synth:
            # ── ③ 找到紧邻更细TF ──
            finer_tf = _find_immediate_finer_tf(tf, tfs)
            
            if finer_tf and finer_tf in synth_cache:
                # ── ④ 从更细TF的DB+合成bar中合成 ──
                synthesized_bar = _synthesize_incomplete_bar(
                    target_tf=tf,
                    db_rows=db_rows,
                    cutoff_date=cutoff_date,
                    ticker_code=ticker_code,
                    finer_tf=finer_tf,
                    finer_synth_bar=synth_cache[finer_tf]["synth_bar"],
                )
                if synthesized_bar:
                    logger.debug(
                        f"[_sync_all_cascading] {tf}: synthesized bar "
                        f"Date={synthesized_bar['Date']}, "
                        f"O={synthesized_bar['Open']:.2f} "
                        f"C={synthesized_bar['Close']:.2f}"
                    )
            else:
                logger.debug(
                    f"[_sync_all_cascading] {tf}: no finer_tf ({finer_tf}) "
                    f"in cache, skip synthesis"
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
    
    logger.debug(f"[_sync_all_cascading] done: {results}")
    return results
```

### 3.3 辅助函数: 数据查询

```python
def _query_tf_from_db(
    ticker_code: str, tf: str, cutoff_date: str, n_pts: int
) -> list[dict]:
    """查询指定TF在cutoff_date之前的最后n_pts条已完成bar(用于显示窗口)。
    
    返回 list[dict] (时间升序), 每条含 Date/Open/High/Low/Close/Volume。
    空列表表示无数据。
    
    注意: SQL WHERE用字符串比较, ISO 8601格式下字典序=时间序,
    跨时区比较也正确(因为同时区字符串前缀相同)。
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
    
    # DESC → ASC (时间升序)
    return [
        {"Date": r[0], "Open": r[1], "High": r[2],
         "Low": r[3], "Close": r[4], "Volume": r[5]}
        for r in reversed(rows)
    ]


def _query_tf_for_period(
    ticker_code: str,
    tf: str,
    period_start: str,      # ISO格式开始时间
    period_end: str,         # ISO格式结束时间
) -> list[dict]:
    """查询指定时间范围内的所有bar(用于合成,不受n_pts限制)。
    
    返回 list[dict] (时间升序)。
    
    ★ v2新增: 替代v1中从prev_data(120条窗口)取数据的做法,
    确保合成期数据完整。
    """
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


def _find_immediate_finer_tf(tf: str, tfs: list[str]) -> Optional[str]:
    """在tfs列表中找紧邻当前TF的更细一级TF。
    
    例: tfs=["15分钟","60分钟","日线"], tf="日线" → 返回 "60分钟"
        tfs=["60分钟","日线","月线"], tf="月线" → 返回 "日线" (跳过不存在的周线)
        tfs=["日线","周线","月线"], tf="日线" → 返回 None (日线是最细的)
    """
    current_idx = ALL_TFS.index(tf)
    for candidate_tf in reversed(tfs):
        if ALL_TFS.index(candidate_tf) < current_idx:
            return candidate_tf
    return None
```

### 3.4 辅助函数: 合成判断

```python
def _needs_synthesis(
    tf: str,
    db_rows: list[dict],
    cutoff_date: str,
) -> bool:
    """判断是否需要为给定TF合成不完整bar。
    
    逻辑: 检查 cutoff_date 是否已进入该TF的下一个周期。
    如果进入了且DB中没有该周期的完成bar → 需要合成。
    
    ★ 时间处理: 所有 Timestamp 比较前统一剥离时区(tz_localize(None)),
    避免 tz-aware vs tz-naive TypeError。
    
    参数:
        tf: 当前TF名称
        db_rows: DB查询结果(时间升序), 非空
        cutoff_date: ISO格式截止日期(可能带时区)
    
    返回: bool
    """
    # 提取最后一条已完成bar的ts并剥离时区
    last_ts_str: str = db_rows[-1]["Date"]
    last_ts: pd.Timestamp = pd.Timestamp(last_ts_str)
    last_ts = _ensure_tz_naive(last_ts)          # ★ 剥离时区
    
    # 计算下一周期开始(返回tz-naive)
    period_start: pd.Timestamp = _get_period_start_ts(last_ts, tf)
    
    # cutoff也剥离时区
    cutoff_dt: pd.Timestamp = pd.Timestamp(cutoff_date)
    cutoff_dt = _ensure_tz_naive(cutoff_dt)       # ★ 剥离时区
    
    # 同类型比较: tz-naive >= tz-naive → 安全
    return cutoff_dt >= period_start
```

### 3.5 辅助函数: 周期开始计算 (v2重写)

```python
def _get_period_start_ts(
    ts: pd.Timestamp,  # 必须是 tz-naive (调用方负责剥离)
    tf: str,
) -> pd.Timestamp:     # 返回 tz-naive
    """计算最后一条已完成bar所在周期结束后的下一个周期开始时间戳。
    
    ★ v2核心修复: 
    - 周线: 改为 last_ts + 1天 (v1错误地返回下一周五)
    - 月线/季线: 简化为 last_ts + 1天 (v1有冗余的月份计算)
    
    设计原则: last_ts 是某个周期的结束时间戳(已完成bar的ts),
    所以 last_ts + 1个更细TF的时间单位 = 下一周期的开始时刻。
    
    参数:
        ts: tz-naive pd.Timestamp — 最后一条已完成bar的时间
        tf: 周期名称
    
    返回: tz-naive pd.Timestamp
    """
    if tf in ("5分钟", "15分钟", "60分钟"):
        # 分钟级: 下一分钟即为下一周期的数据起点
        return ts + pd.Timedelta(minutes=1)
    
    elif tf == "日线":
        # 日线: ts是某天00:00, +1天=下一天00:00
        return (ts + pd.Timedelta(days=1)).normalize()
    
    elif tf == "周线":
        # ★ v2修复: ts是周五, +1天=周六(period_start是下一天,不是下周五)
        # 例: last_ts="2026-07-03"(周五), 返回"2026-07-04"
        # 虽然周六无交易数据, 但合成查询自然会返回空,
        # 实际数据从周一才开始, 不影响合成结果
        return (ts + pd.Timedelta(days=1)).normalize()
    
    elif tf == "月线":
        # ★ v2简化: ts是月末最后一天, +1天=下月1日
        # 例: last_ts="2026-06-30", 返回"2026-07-01" ✓
        # 例: last_ts="2026-12-31", 返回"2027-01-01" ✓ (跨年)
        return (ts + pd.Timedelta(days=1)).normalize()
    
    elif tf == "季线":
        # ★ v2简化: ts是季末最后一天, +1天=下季第一天
        # 例: last_ts="2026-06-30"(Q2末), 返回"2026-07-01"(Q3始) ✓
        return (ts + pd.Timedelta(days=1)).normalize()
    
    else:
        # 1分钟 或其他
        return ts + pd.Timedelta(minutes=1)
```

### 3.6 辅助函数: 合成查询起点

```python
def _get_query_start_for_synthesis(
    last_completed_ts: pd.Timestamp,  # tz-naive
    tf: str,
) -> pd.Timestamp:                    # tz-naive
    """计算合成时从更细TF数据中查询的起点时间戳。
    
    与 _get_period_start_ts() 的区别:
    - _get_period_start_ts: 用于判断是否需要合成(>= 比较)
    - _get_query_start_for_synthesis: 用于数据查询的起点(>= 比较)
    
    两者逻辑相同, 但职责不同, 保持分离以便将来可能需要不同逻辑。
    
    参数:
        last_completed_ts: tz-naive — 最后一条已完成bar的ts
        tf: 合成目标TF
    
    返回: tz-naive pd.Timestamp
    """
    # 与 _get_period_start_ts 逻辑完全一致
    if tf in ("5分钟", "15分钟", "60分钟"):
        return last_completed_ts + pd.Timedelta(minutes=1)
    elif tf in ("日线", "周线", "月线", "季线"):
        return (last_completed_ts + pd.Timedelta(days=1)).normalize()
    else:
        return last_completed_ts + pd.Timedelta(minutes=1)
```

### 3.7 核心: `_synthesize_incomplete_bar()` (v2重写)

```python
def _synthesize_incomplete_bar(
    target_tf: str,              # 要合成哪个TF的bar
    db_rows: list[dict],         # 目标TF的DB已完成bar
    cutoff_date: str,            # 截止日期(可能带时区)
    ticker_code: str,            # 股票代码
    finer_tf: str,               # 更细一级TF名称
    finer_synth_bar: Optional[dict],  # 更细TF的合成bar(可能None)
) -> Optional[dict]:
    """从更细TF的数据合成目标TF的一条不完整bar。
    
    ★ v2关键改进: 
    1. 不依赖prev_data(120条窗口), 重新查询DB获取合成期内所有finer_tf bar
    2. 将finer_tf的DB查询结果与finer_tf的合成bar拼接, 确保数据完整
    3. 时间比较前统一剥离时区
    
    返回:
        dict with Date/Open/High/Low/Close/Volume, 或 None(无法合成)
    """
    # ── ① 计算查询范围 ──
    last_ts_str: str = db_rows[-1]["Date"]
    last_ts: pd.Timestamp = pd.Timestamp(last_ts_str)
    last_ts = _ensure_tz_naive(last_ts)                  # ★ 剥离时区
    
    query_start: pd.Timestamp = _get_query_start_for_synthesis(last_ts, target_tf)
    cutoff_dt: pd.Timestamp = pd.Timestamp(cutoff_date)
    cutoff_dt = _ensure_tz_naive(cutoff_dt)               # ★ 剥离时区
    
    # ── ② 日线合成优化: 60分钟查询从09:30开始 ──
    actual_start: pd.Timestamp = query_start
    if target_tf == "日线" and finer_tf == "60分钟":
        # 避免查询盘前空数据, 从09:30开始
        market_open = query_start.replace(hour=9, minute=30, second=0, microsecond=0)
        if query_start < market_open < cutoff_dt:
            actual_start = market_open
            logger.debug(
                f"[_synthesize] daily synth: adjusted query start "
                f"from {query_start} to {actual_start} (market open)"
            )
    
    # ── ③ 查询更细TF的DB数据 ──
    period_start_str = actual_start.isoformat()
    period_end_str = cutoff_dt.isoformat()
    
    finer_db_bars: list[dict] = _query_tf_for_period(
        ticker_code, finer_tf, period_start_str, period_end_str
    )
    
    # ── ④ 拼接更细TF的合成bar(如果落在查询范围内) ──
    all_finer_bars: list[dict] = list(finer_db_bars)
    
    if finer_synth_bar is not None:
        synth_ts: pd.Timestamp = pd.Timestamp(finer_synth_bar["Date"])
        synth_ts = _ensure_tz_naive(synth_ts)            # ★ 剥离时区
        
        # 检查合成bar的ts是否在查询范围内
        if actual_start <= synth_ts <= cutoff_dt:
            # 去重: 如果DB中已有相同时戳的bar, 用合成bar覆盖
            existing_ts = {_ensure_tz_naive(pd.Timestamp(b["Date"])) for b in all_finer_bars}
            if synth_ts not in existing_ts:
                all_finer_bars.append(finer_synth_bar)
                # 按Date重新排序
                all_finer_bars.sort(
                    key=lambda b: _ensure_tz_naive(pd.Timestamp(b["Date"]))
                )
    
    # ── ⑤ 聚合 ──
    if len(all_finer_bars) == 0:
        logger.debug(
            f"[_synthesize] {target_tf}: no finer bars in "
            f"[{period_start_str}, {period_end_str}]"
        )
        return None
    
    # ── ⑥ 构建合成bar(注意Date格式) ──
    synth_date: str = _format_synth_date(cutoff_date, target_tf, db_rows)
    
    df = pd.DataFrame(all_finer_bars)
    return {
        "Date": synth_date,                             # ★ 匹配TF原生格式
        "Open": float(df["Open"].iloc[0]),
        "High": float(df["High"].max()),
        "Low": float(df["Low"].min()),
        "Close": float(df["Close"].iloc[-1]),
        "Volume": float(df["Volume"].sum()),
    }
```

### 3.8 辅助函数: 聚合与输出

```python
def _aggregate_bars(
    finer_bars: list[dict],
    target_tf: str,
    synth_date: str,
) -> dict:
    """从更细TF的bar列表聚合成一条目标TF的bar。
    
    OHLCV聚合规则(所有TF通用):
      Open  = 第一条bar的Open
      High  = 所有bar的High的最大值
      Low   = 所有bar的Low的最小值
      Close = 最后一条bar的Close
      Volume = 所有bar的Volume之和
      Date  = synth_date(由调用方决定)
    
    参数:
        finer_bars: 更细TF的bar列表(时间升序)
        target_tf: 目标TF(用于日志)
        synth_date: 合成bar的Date字符串
    
    返回: dict
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


def _build_output_df(
    db_rows: list[dict],
    synthesized_bar: Optional[dict],
    n_pts: int,
) -> pd.DataFrame:
    """将DB已完成bar + 合成bar合并为DataFrame, 截断到n_pts。
    
    参数:
        db_rows: DB已完成bar列表(时间升序)
        synthesized_bar: 合成bar(dict)或None
        n_pts: 最大保留行数
    
    返回: pd.DataFrame (列: Date/Open/High/Low/Close/Volume)
    """
    data = list(db_rows)
    
    if synthesized_bar is not None:
        data.append(synthesized_bar)
    
    df = pd.DataFrame(data)
    if len(df) > n_pts:
        df = df.iloc[-n_pts:]  # 保留最后n_pts条
    
    return df.reset_index(drop=True)


def _write_parquet(tf: str, df: pd.DataFrame) -> bool:
    """将DataFrame写入该TF的parquet文件。
    
    ★ v2注意: Date列格式已在_build_output_df之前统一,
    不会出现混合时区问题。
    
    返回: bool — 是否写入成功
    """
    display_dir = Path(__file__).parent.parent.parent / "data" / "display"
    display_dir.mkdir(parents=True, exist_ok=True)
    try:
        filepath = display_dir / f"{tf}.parquet"
        df.to_parquet(filepath, index=False)
        logger.debug(f"[_write_parquet] {tf}.parquet: {len(df)} rows")
        return True
    except Exception as e:
        logger.error(f"[_write_parquet] {tf}.parquet failed: {e}")
        return False
```

### 3.9 `_load_chart_data()` 修改

**位置**: `streamlit_app.py` (修改现有函数)

```python
def _load_chart_data(market, ticker_code, tf, day_offset, n_pts,
                     window_start=None, cutoff_date=None):
    """加载图表数据。
    
    浏览模式 (window_start=None): 
        _sync_to_display() → 读parquet → 失败则API回退
    
    回测模式 (window_start给定):
        ★ v2: 直接读parquet(已由_sync_all_cascading()预处理)
        读失败则回退到 _cached_fetch_stock()
    """
    if window_start is not None:
        # ★ 回测模式: parquet已由main()中的_sync_all_cascading()准备好
        # 不调用_sync_to_display(), 直接读取parquet
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
                # ★ v2: pd.to_datetime安全 — Date列已统一格式,无混合时区
                df["Date"] = pd.to_datetime(df["Date"])
                df = df.set_index("Date").sort_index()
                
                if len(df) < 2:
                    err = f"{tf} 数据点不足 ({len(df)})"
                    return None, None, None, None, None, err
                
                # ... 后续处理不变 ...
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
                    except Exception:
                        pass
                
                return t, noisy, ohlc, ticker_code, df.index, None
            else:
                err = "数据不足"
        except Exception as e:
            err = str(e)
    
    # ★ v2: parquet不存在或读取失败时的降级路径
    if err is not None:
        logger.warning(f"[_load_chart_data] {tf} parquet load failed: {err}")
    # 回退到API获取
    return _cached_fetch_stock(market, ticker_code, tf, n_pts)
```

### 3.10 `main()` 修改

**位置**: `streamlit_app.py` (在 `sorted_views` 循环之前)

```python
# ── 回测模式: 级联合成预处理 ──
cb_mode = AppState.get("_cb_mode", False)
if cb_mode and ticker_code:
    cutoff_date = AppState.get("_bt_cutoff_date", "")
    min_tf = AppState.get("_min_tf", "")
    if cutoff_date and min_tf:
        # ★ v2: 数据就绪检查 — 没有数据时跳过合成
        if not has_data(ticker_code):
            st.warning("回测数据未就绪，请先在浏览模式加载数据")
        else:
            # 提取所有视图中涉及的不同TF, 从细到粗排序
            unique_tfs = sorted(
                set(cfg["tf"] for cfg in configs),
                key=lambda tf: ALL_TFS.index(tf)
            )
            # 取所有视图中最小的 n_pts (保守值)
            min_n_pts = min(cfg["n_pts"] for cfg in configs) if configs else 120
            
            _sync_all_cascading(ticker_code, unique_tfs, cutoff_date, min_tf, min_n_pts)
```

---

## 4. 各周期合成规则

### 4.1 统一聚合公式

所有 TF 对使用相同的 OHLCV 聚合规则:

```
合成bar.Open   = 第一条更细bar.Open    (周期内最早的价格)
合成bar.High   = MAX(所有更细bar.High) (周期内最高价)
合成bar.Low    = MIN(所有更细bar.Low)  (周期内最低价)
合成bar.Close  = 最后一条更细bar.Close  (周期内最新价格)
合成bar.Volume = SUM(所有更细bar.Volume) (周期内总成交量)
合成bar.Date   = cutoff_date (格式匹配该TF的DB原生格式)
```

**为什么通用**: 这是 OHLC 的标准定义，对任何周期都适用。不需要按 TF 区分。

### 4.2 各TF处理规则

| 序号 | TF | 是否需要合成 | 数据源(更细TF) | period_start 计算 | period_end | 特殊处理 |
|------|-----|------------|---------------|-------------------|-----------|---------|
| 0 | 1分钟 | **否** (如=min_tf) | N/A | N/A | N/A | 从不合成, 仅DB查询 |
| 1 | 5分钟 | 是 (如1分钟在链路中) | 1分钟 | `last_ts + 1分钟` | `cutoff_date` | — |
| 2 | 15分钟 | 是 (如5/1分钟在链路中) | 5/1分钟(取最细可用) | `last_ts + 1分钟` | `cutoff_date` | — |
| 3 | 60分钟 | 是 (如15/5/1分钟在链路中) | 15/5/1分钟(取最细可用) | `last_ts + 1分钟` | `cutoff_date` | — |
| 4 | 日线 | 是 (如有分钟TF在链路中) | 60/15/5/1分钟(取最细可用) | `last_ts + 1天` | `cutoff_date` | 60分钟查询从09:30开始 |
| 5 | 周线 | 是 (如有日线在链路中) | 日线 | `last_ts + 1天` | `cutoff_date` | 跨周末自然无数据 |
| 6 | 月线 | 是 (如有日线/周线在链路中) | 日线/周线(取最细可用) | `last_ts + 1天` | `cutoff_date` | 跨月自然切换 |
| 7 | 季线 | 是 (如有日/周/月线在链路中) | 月/日/周线(取最细可用) | `last_ts + 1天` | `cutoff_date` | 跨季自然切换 |

### 4.3 逐TF详解

#### 4.3.1 1分钟

```
角色: 最细周期之一, 永不合成
DB查询: SELECT ... WHERE ts <= cutoff_date ORDER BY ts DESC LIMIT n_pts
输出: DB查询结果 → parquet
```

#### 4.3.2 5分钟

```
前置条件: tfs列表中有5分钟, 且存在更细TF(如1分钟)在链路中
合成触发: cutoff_date >= (last_completed_5m_ts + 1分钟)
数据源: 重新查询1分钟DB(period_start到cutoff_date) + 拼接1分钟合成bar
合成窗口: [last_5m_ts + 1分钟, cutoff_date]
示例:
  last_5m_ts = "2026-07-03T14:55:00+08:00"
  period_start = 14:56
  cutoff = 14:58
  查询1分钟: 14:56, 14:57, 14:58 → 3条
  聚合 → 5分钟合成bar(Date="2026-07-03T14:58:00+08:00")
```

#### 4.3.3 15分钟

```
前置条件: tfs列表中有15分钟, 且存在更细TF在链路中
合成触发: cutoff_date >= (last_completed_15m_ts + 1分钟)
数据源: 重新查询更细TF(如5分钟或1分钟)DB + 拼接其合成bar
合成窗口: [last_15m_ts + 1分钟, cutoff_date]
```

#### 4.3.4 60分钟

```
前置条件: tfs列表中有60分钟, 且存在更细分钟TF在链路中
合成触发: cutoff_date >= (last_completed_60m_ts + 1分钟)
数据源: 重新查询更细分钟TF DB + 拼接其合成bar
合成窗口: [last_60m_ts + 1分钟, cutoff_date]
示例(级联):
  min_tf=15分钟, 链路: 15分钟→60分钟→日线
  60分钟合成时:
    ① 查询15分钟DB [14:01, 14:47] → 获取14:00,14:15,14:30,14:45的15分钟bar
    ② 检查15分钟合成bar(如有): 15分钟=min_tf, 无合成bar → 跳过
    ③ 拼接结果 → 4条15分钟bar
    ④ 聚合 → 60分钟合成bar(Date="2026-07-03T14:47:00+08:00")
```

#### 4.3.5 日线

```
前置条件: tfs列表中有日线, 且存在分钟TF在链路中
合成触发: cutoff_date >= (last_completed_daily_ts + 1天)
数据源: 重新查询最细可用分钟TF DB + 拼接其合成bar
合成窗口: [last_daily_ts + 1天, cutoff_date]
优化: 数据源为60分钟时, 查询起点调整为09:30(跳过盘前空数据)

示例(级联, min_tf=15分钟, 链路含60分钟):
  日线合成时:
    ① 查询60分钟DB [07-03 00:00, 14:47]
       → 如60分钟数据从09:30开始, 实际DB只有09:30-14:00的完成bar
    ② 拼接60分钟合成bar(ts=14:47)
    ③ 聚合所有落在[07-03, 14:47]的60分钟bar
    ④ → 日线合成bar(Date="2026-07-03T14:47:00", 无时区)

注意: 
  - DB中日线ts格式为"2026-07-03T00:00:00"(无时区)
  - 合成bar Date也应为无时区格式: "2026-07-03T14:47:00"
  - 由 _format_synth_date() 自动处理
```

#### 4.3.6 周线

```
前置条件: tfs列表中有周线, 且存在日线在链路中
合成触发: cutoff_date >= (last_completed_weekly_ts + 1天)
数据源: 重新查询日线DB + 拼接日线合成bar
合成窗口: [last_weekly_ts + 1天, cutoff_date]

★ v2修复: period_start = last_ts + 1天 (v1错误地+7天到下一周五)

示例:
  last_weekly_ts = "2026-06-26" (周五, 某周线bar的ts)
  period_start = "2026-06-27" (周六)
  cutoff = "2026-07-03T14:47:00" (下周五盘中)
  
  cutoff_dt >= period_start? 07-03 >= 06-27 → YES → 触发合成 ✓
  
  查询日线DB [06-27, 07-03T14:47]:
    06-27(周六): 无数据
    06-28(周日): 无数据
    06-29(周一): 日线bar
    06-30(周二): 日线bar
    07-01(周三): 日线bar
    07-02(周四): 日线bar
    07-03(周五): 日线合成bar(如有)
    → 聚合5条日常bar → 周线合成bar(Date="2026-07-03T14:47:00", 无时区)
```

#### 4.3.7 月线

```
前置条件: tfs列表中有月线, 且存在日线(或周线)在链路中
合成触发: cutoff_date >= (last_completed_monthly_ts + 1天)
数据源: 重新查询日线DB + 拼接日线合成bar
合成窗口: [last_monthly_ts + 1天, cutoff_date]

★ v2简化: period_start = last_ts + 1天
  last_ts 是月末最后一天(如"2026-06-30"), +1天="2026-07-01"(下月1日) ✓
  
示例:
  last_monthly_ts = "2026-06-30" (6月月线bar)
  period_start = "2026-07-01"
  cutoff = "2026-07-15T14:47:00" (7月15日盘中)
  
  查询日线DB [07-01, 07-15T14:47]:
    7月1日到7月15日所有日线bar + 7月15日的日线合成bar
    → 聚合 → 月线合成bar(Date="2026-07-15T14:47:00", 无时区)
```

#### 4.3.8 季线

```
前置条件: tfs列表中有季线, 且存在月线(或日线/周线)在链路中
合成触发: cutoff_date >= (last_completed_quarterly_ts + 1天)
数据源: 重新查询最细可用TF(月线/日线/周线)DB + 拼接其合成bar
合成窗口: [last_quarterly_ts + 1天, cutoff_date]

★ v2简化: period_start = last_ts + 1天
  last_ts="2026-06-30"(Q2末), +1天="2026-07-01"(Q3始) ✓
  跨年: last_ts="2026-12-31"(Q4末), +1天="2027-01-01"(Q1始) ✓
```

### 4.4 级联链路示例

假设 `min_tf="15分钟"`, 所有8个TF都在视图中:

```
处理顺序: 1分钟 → 5分钟 → 15分钟 → 60分钟 → 日线 → 周线 → 月线 → 季线

1分钟: DB查询 → parquet (不合成, 可能=min_tf如果不含更细TF)
       但实际上min_tf=15分钟, 1分钟和5分钟是否在tfs列表中取决于视图配置.
       如果1分钟和5分钟不在tfs列表中 → 跳过.

15分钟: DB查询 → parquet (tf=min_tf, 不合成)
        synth_cache["15分钟"] = {"synth_bar": None}

60分钟: DB查询 → _needs_synthesis? → 从15分钟合成
        finer_tf = _find_immediate_finer_tf("60分钟", tfs) → "15分钟"
        查询15分钟DB [14:01, 14:47] + 拼接15分钟合成bar(None)
        synth_cache["60分钟"] = {"synth_bar": {...}}

日线: DB查询 → _needs_synthesis? → 从60分钟合成
        finer_tf = _find_immediate_finer_tf("日线", tfs) → "60分钟"
        查询60分钟DB [07-03T00:00:00, 14:47] + 拼接60分钟合成bar
        synth_cache["日线"] = {"synth_bar": {...}}

周线: DB查询 → _needs_synthesis? → 从日线合成
        finer_tf = _find_immediate_finer_tf("周线", tfs) → "日线"
        查询日线DB [06-27, 07-03T14:47] + 拼接日线合成bar
        synth_cache["周线"] = {"synth_bar": {...}}

月线: DB查询 → _needs_synthesis? → 从日线合成 (跳过周线)
        finer_tf = _find_immediate_finer_tf("月线", tfs) → "日线" (因为周线≤月线)
        ★ 等等, _find_immediate_finer_tf应返回"周线"如果周线在tfs中
        查询日线DB [07-01, 07-15T14:47] + 拼接日线合成bar
        synth_cache["月线"] = {"synth_bar": {...}}

季线: DB查询 → _needs_synthesis? → 从月线合成
        finer_tf = _find_immediate_finer_tf("季线", tfs) → "月线"
        查询月线DB [07-01, Q3] + 拼接月线合成bar
        synth_cache["季线"] = {"synth_bar": {...}}
```

---

## 5. 边界条件处理

### 5.1 min_tf = 日线 (无分钟数据)

**场景**: 4个视图配置为日线/周线/月线/季线

**处理**:
```
tfs = ["日线", "周线", "月线", "季线"]
min_tf = "日线"

日线: tf == min_tf → 不合成, DB查询 → parquet
周线: finer_tf = None (日线≥周线? 不, 日线在ALL_TFS中索引=4, 周线=5)
      → finer_tf = "日线" (日线索引4 < 周线索引5)
      查询日线DB [last_weekly_ts+1天, cutoff_date] + 拼接日线合成bar
月线: finer_tf = "周线" (周线在tfs中)
      查询周线DB? 不 — 月线合成数据源是更细TF, 即周线的DB+合成bar
      但周线的合成bar在synth_cache中.
      查询周线DB [last_monthly_ts+1天, cutoff_date] + 拼接周线合成bar
季线: finer_tf = "月线"
```

### 5.2 中间TF缺失

**场景**: tfs = ["60分钟", "日线", "月线"] (无周线)

**处理**:
```
月线合成时: finer_tf = _find_immediate_finer_tf("月线", tfs)
  → 遍历 tfs 中索引 < 月线索引(6) 的TF: 日线(4), 60分钟(3)
  → 取最大索引者 → "日线"
  → 月线从日线数据合成 ✓
```

### 5.3 数据不足

**场景1: 更细TF在合成期内无数据**
```
cutoff刚过周期边界(如60分钟周期刚开始1分钟)
查询15分钟DB [14:01, 14:01] → 空
→ synthesized_bar = None → parquet仅有DB完成bar
```

**场景2: 某TF的DB完全无数据**
```
ticker没有60分钟数据
_query_tf_from_db() → []
→ results[tf] = False → 后续TF的prev_data = None → 跳过合成
→ _load_chart_data()读到不存在parquet → 回退到_cached_fetch_stock()
```

### 5.4 `cutoff_date` 等于周期边界

**场景**: `cutoff_date = "2026-07-03T15:00:00+08:00"`, 60分钟周期正好在15:00完整结束

**处理**:
```
last_ts = "14:00" 还是 "15:00"? 
取决于DB查询:
  "15:00" <= "15:00" → 字符串比较为True
  如果DB中有15:00的bar → db_rows[-1] = "15:00"
  period_start = _get_period_start_ts("15:00", "60分钟") = "15:01"
  cutoff_dt(15:00) >= period_start(15:01)? → False → 不合成 ✓

如果没有15:00的bar → db_rows[-1] = "14:00"
  period_start = "14:01"
  cutoff_dt(15:00) >= period_start(14:01)? → True → 合成
  合成结果: 从14:01到15:00的数据聚合 → 合成bar覆盖14:00-15:00
  但DB随后可能补充15:00的完成bar → 下次rerun会看到15:00,
  不再合成 → 正确
```

### 5.5 跨周末/节假日

**场景**: 周五last_weekly_ts, period_start是周六

**处理**: 查询日线DB [周六, cutoff_date] → 周六/日无日线数据 → DB返回空 → 周一才有数据 → 聚合结果仅含周一之后的日线bar。正确反映实际情况。

### 5.6 刚切换ticker

**场景**: 用户切换到新ticker, DB可能无数据

**处理**:
```
main()中: has_data(ticker_code) → False
→ st.warning("回测数据未就绪") → 跳过_sync_all_cascading()
→ _load_chart_data() 中parquet不存在 → 回退到_cached_fetch_stock()
→ API获取当前最新数据(非回测历史数据)
```

### 5.7 同一TF多个视图

**场景**: 视图0和视图2都使用"日线"

**处理**: `unique_tfs = set(...)` 自动去重 → 日线只处理一次。

### 5.8 并发读取parquet

**场景**: 4个`st.fragment`并行调用`_load_chart_data()`, 都读parquet

**分析**: 4个fragment只读parquet, `pd.read_parquet()` 是线程安全的读取操作。写入已在`_sync_all_cascading()`中完成(在fragment渲染之前)。无竞争条件。

---

## 6. 数据流图

### 6.1 级联合成全景 (v2)

```
═══════════════════════════════════════════════════════════════════════════════
                         main() — 回测模式入口 (v2)
═══════════════════════════════════════════════════════════════════════════════

  configs = [
    {"tf": "15分钟", "n_pts": 120},  ← 视图0
    {"tf": "60分钟", "n_pts": 120},  ← 视图1  
    {"tf": "日线",   "n_pts": 120},  ← 视图2
    {"tf": "周线",   "n_pts": 120},  ← 视图3
  ]

  unique_tfs = {"15分钟", "60分钟", "日线", "周线"}  ← 去重
  min_tf = "15分钟"
  cutoff_date = "2026-07-03T14:47:00+08:00"

  ┌── _sync_all_cascading(ticker, tfs, cutoff, min_tf) ─────────────────┐
  │                                                                       │
  │  synth_cache = {}  # {tf: {synth_bar, db_sample_ts}}                 │
  │                                                                       │
  │ ┌─ [第1级] TF=15分钟 (min_tf) ──────────────────────────────────┐    │
  │ │                                                                │    │
  │ │  ① DB查询: SELECT ... WHERE ts <= cutoff, LIMIT 120            │    │
  │ │     → 120条15分钟已完成bar                                     │    │
  │ │                                                                │    │
  │ │  ② tf == min_tf → 跳过合成                                     │    │
  │ │                                                                │    │
  │ │  ③ 写入 15分钟.parquet (120条)                                 │    │
  │ │                                                                │    │
  │ │  ④ synth_cache["15分钟"] = {synth_bar: None}                   │    │
  │ └────────────────────────────────────────────────────────────────┘    │
  │                                                                       │
  │ ┌─ [第2级] TF=60分钟 ───────────────────────────────────────────┐    │
  │ │                                                                │    │
  │ │  ① DB查询: WHERE timeframe='60分钟'                             │    │
  │ │     → 最后一条 ts="14:00+08:00"                                │    │
  │ │                                                                │    │
  │ │  ② _needs_synthesis("60分钟", db_rows, cutoff):                │    │
  │ │     last_ts = _ensure_tz_naive("14:00+08:00") = 14:00         │    │
  │ │     period_start = _get_period_start_ts(14:00, "60分钟")       │    │
  │ │                   = 14:01                                      │    │
  │ │     cutoff_dt = _ensure_tz_naive("14:47+08:00") = 14:47       │    │
  │ │     14:47 >= 14:01? → YES → 需要合成                           │    │
  │ │                                                                │    │
  │ │  ③ finer_tf = "15分钟"                                         │    │
  │ │                                                                │    │
  │ │  ④ _synthesize_incomplete_bar("60分钟", ...):                  │    │
  │ │     ★ 查询15分钟DB [14:01, 14:47] → {14:00,14:15,14:30,14:45} │    │
  │ │     ★ 拼接15分钟合成bar: None (15分钟=min_tf)                   │    │
  │ │     ★ 聚合OHLCV                                               │    │
  │ │     ★ Date = _format_synth_date("14:47+08:00", "60分钟", ...) │    │
  │ │             = "2026-07-03T14:47:00+08:00"                     │    │
  │ │                                                                │    │
  │ │  ⑤ 写入 60分钟.parquet (120条DB + 1条合成)                     │    │
  │ │                                                                │    │
  │ │  ⑥ synth_cache["60分钟"] = {synth_bar: {...}}                  │    │
  │ └────────────────────────────────────────────────────────────────┘    │
  │                                                                       │
  │ ┌─ [第3级] TF=日线 ─────────────────────────────────────────────┐    │
  │ │                                                                │    │
  │ │  ① DB查询: WHERE timeframe='日线'                               │    │
  │ │     → 最后一条 ts="2026-07-02T00:00:00" (昨天日线, 无时区)      │    │
  │ │                                                                │    │
  │ │  ② _needs_synthesis("日线", db_rows, cutoff):                  │    │
  │ │     last_ts = _ensure_tz_naive("2026-07-02") = 07-02 00:00    │    │
  │ │     period_start = 07-02 + 1天 = "2026-07-03T00:00:00"         │    │
  │ │     cutoff_dt = _ensure_tz_naive("14:47+08:00") = 07-03 14:47 │    │
  │ │     07-03T14:47 >= 07-03T00:00? → YES → 需要合成               │    │
  │ │                                                                │    │
  │ │  ③ finer_tf = "60分钟"                                         │    │
  │ │                                                                │    │
  │ │  ④ _synthesize_incomplete_bar("日线", ...):                    │    │
  │ │     ★ 查询60分钟DB [07-03T00:00:00, 07-03T14:47:00]            │    │
  │ │       → 调整起点: 09:30 (跳过盘前)                             │    │
  │ │       → 实际查询: [07-03T09:30:00, 07-03T14:47:00]             │    │
  │ │       → DB返回: 09:30,10:30,11:30,12:30,13:30,14:00的完成bar  │    │
  │ │     ★ 拼接60分钟合成bar: {Date:"...14:47:00+08:00", ...}       │    │
  │ │       检查: synth_ts(14:47) 在 [09:30, 14:47] 内? YES         │    │
  │ │       去重: DB无14:47的bar → 加入                              │    │
  │ │     ★ 聚合: 共7条60分钟bar                                     │    │
  │ │     ★ Date = _format_synth_date("14:47+08:00", "日线", ...)   │    │
  │ │             → DB日线无时区 → 剥离 → "2026-07-03T14:47:00"     │    │
  │ │                                                                │    │
  │ │  ⑤ 写入 日线.parquet (120条DB + 1条合成)                       │    │
  │ │     ★ Date列统一无时区: DB日线和合成日线格式一致 ✓              │    │
  │ │                                                                │    │
  │ │  ⑥ synth_cache["日线"] = {synth_bar: {...}}                    │    │
  │ └────────────────────────────────────────────────────────────────┘    │
  │                                                                       │
  │ ┌─ [第4级] TF=周线 ─────────────────────────────────────────────┐    │
  │ │                                                                │    │
  │ │  ① DB查询: WHERE timeframe='周线'                               │    │
  │ │     → 最后一条 ts="2026-06-26T00:00:00" (上周周线, 无时区)      │    │
  │ │                                                                │    │
  │ │  ② _needs_synthesis("周线", db_rows, cutoff):                  │    │
  │ │     last_ts = "2026-06-26"                                     │    │
  │ │     ★ v2修复: period_start = 06-26 + 1天 = "2026-06-27"        │    │
  │ │     (v1错误: 返回"2026-07-03"下一周五, 导致永远不合成)          │    │
  │ │     cutoff_dt = 07-03T14:47                                    │    │
  │ │     07-03 >= 06-27? → YES → 需要合成 ✓                         │    │
  │ │                                                                │    │
  │ │  ③ finer_tf = "日线"                                           │    │
  │ │                                                                │    │
  │ │  ④ _synthesize_incomplete_bar("周线", ...):                    │    │
  │ │     ★ 查询日线DB [06-27, 07-03T14:47]                          │    │
  │ │       → 06-27(周六),06-28(周日): 无数据                        │    │
  │ │       → 06-29~07-02: 各1条日线bar                              │    │
  │ │       → DB返回: 4条日线完成bar                                  │    │
  │ │     ★ 拼接日线合成bar: {Date:"07-03T14:47:00", ...}            │    │
  │ │       检查: synth_ts(07-03 14:47) 在查询范围内 → 加入           │    │
  │ │     ★ 聚合: 4条历史日线 + 1条日线合成bar → 周线合成bar          │    │
  │ │     ★ Date: 无时区格式 "2026-07-03T14:47:00"                   │    │
  │ │                                                                │    │
  │ │  ⑤ 写入 周线.parquet                                           │    │
  │ └────────────────────────────────────────────────────────────────┘    │
  │                                                                       │
  │  return {"15分钟": True, "60分钟": True, "日线": True, "周线": True}   │
  └───────────────────────────────────────────────────────────────────────┘
```

### 6.2 数据查询路径 (v2 vs v1 对比)

```
v1 (错误路径):
  prev_data (120条窗口) ──→ 过滤 ──→ 合成
  问题: 120条窗口可能不够、依赖显示数据

v2 (正确路径):
  合成目标TF时:
    ├─ _query_tf_from_db(target_tf, ...)  ──→ 120条显示数据 ──→ parquet
    └─ _query_tf_for_period(finer_tf, period_start, period_end) 
         ──→ 合成期完整数据
              + finer_tf的synth_bar (如在期内)
              ──→ 聚合 ──→ 合成bar ──→ parquet
  
  显示数据和合成数据完全分离, 互不依赖.
```

### 6.3 合成bar Date格式决策树

```
合成bar需要Date字段
  │
  ├─ 目标TF是分钟级(1m/5m/15m/60m)?
  │    └─ YES → DB数据带时区
  │         → _format_synth_date() 保留 tz_suffix
  │         → "2026-07-03T14:47:00+08:00"
  │
  └─ 目标TF是日线级(日/周/月/季)?
       └─ YES → DB数据无时区
            → _format_synth_date() 剥离时区
            → "2026-07-03T14:47:00"
```

---

## 7. 实施计划

### 7.1 分步实施

```
Phase 1: 基础工具函数 (低风险, 可独立测试)
  ├─ 1.1 _ensure_tz_naive()         — 时区剥离
  ├─ 1.2 _get_tz_suffix()           — 时区后缀提取
  ├─ 1.3 _format_synth_date()       — 合成bar Date格式化
  ├─ 1.4 ALL_TFS 常量               — data_loader.py中添加
  └─ 验证: 单元测试 tz_naive, tz_suffix提取

Phase 2: 周期计算函数 (中等风险)
  ├─ 2.1 _get_period_start_ts()     — ★ v2重写, 统一+1单位
  ├─ 2.2 _get_query_start_for_synthesis() — ★ v2简化
  ├─ 2.3 _needs_synthesis()         — ★ 添加_ensure_tz_naive
  └─ 验证: 参数化测试覆盖全部8个TF + 边界(跨天/跨月/跨年/整点)

Phase 3: 合成核心 (中等风险)
  ├─ 3.1 _query_tf_from_db()        — DB查询(显示用)
  ├─ 3.2 _query_tf_for_period()     — ★ v2新增, 时间范围查询
  ├─ 3.3 _find_immediate_finer_tf() — 链路上溯
  ├─ 3.4 _aggregate_bars()          — OHLCV聚合
  ├─ 3.5 _synthesize_incomplete_bar() — ★ v2重写
  ├─ 3.6 _build_output_df()         — 合并输出
  ├─ 3.7 _write_parquet()           — 写入parquet
  ├─ 3.8 _sync_all_cascading()      — 主调度
  └─ 验证: mock数据模拟完整60分钟→日线→周线级联

Phase 4: Streamlit集成 (需谨慎)
  ├─ 4.1 _load_chart_data()         — 回测模式跳过_sync_to_display
  ├─ 4.2 main()                     — 添加_sync_all_cascading调用
  ├─ 4.3 has_data()检查             — 数据就绪前置检查
  └─ 验证: 端到端回测场景测试

Phase 5: 降级与边界
  ├─ 5.1 _load_chart_data降级       — parquet不存在→API回退
  ├─ 5.2 日线合成60分钟09:30调整    — P1-3
  ├─ 5.3 min_tf=日线的纯日线场景    — P2
  └─ 验证: 各种边界场景
```

### 7.2 关键测试用例

```python
# ═══════════════════════════════════════════════════════════════
# Phase 1: 时区处理
# ═══════════════════════════════════════════════════════════════

def test_ensure_tz_naive_strips_timezone():
    ts = pd.Timestamp("2026-07-03T14:47:00+08:00")
    result = _ensure_tz_naive(ts)
    assert result.tz is None
    assert result.hour == 14  # 钟表时间不变

def test_format_synth_date_minute_tf():
    db_rows = [{"Date": "2026-07-03T10:00:00+08:00"}]
    result = _format_synth_date("2026-07-03T14:47:00+08:00", "60分钟", db_rows)
    assert "+08:00" in result
    assert result == "2026-07-03T14:47:00+08:00"

def test_format_synth_date_daily_tf():
    db_rows = [{"Date": "2026-07-02T00:00:00"}]  # 无时区
    result = _format_synth_date("2026-07-03T14:47:00+08:00", "日线", db_rows)
    assert "+" not in result
    assert result == "2026-07-03T14:47:00"


# ═══════════════════════════════════════════════════════════════
# Phase 2: 周期计算
# ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("ts_str,tf,expected", [
    # 分钟级
    ("2026-07-03T14:00:00", "60分钟", "2026-07-03T14:01:00"),
    ("2026-07-03T14:15:00", "15分钟", "2026-07-03T14:16:00"),
    ("2026-07-03T14:55:00", "5分钟",  "2026-07-03T14:56:00"),
    # 日线
    ("2026-07-02T00:00:00", "日线", "2026-07-03T00:00:00"),
    # 周线 — ★ v2修复: 应为下一日, 不是下周五
    ("2026-07-03T00:00:00", "周线", "2026-07-04T00:00:00"),  # 周五→周六
    # 月线
    ("2026-06-30T00:00:00", "月线", "2026-07-01T00:00:00"),
    ("2026-12-31T00:00:00", "月线", "2027-01-01T00:00:00"),  # 跨年
    # 季线
    ("2026-06-30T00:00:00", "季线", "2026-07-01T00:00:00"),
    ("2026-12-31T00:00:00", "季线", "2027-01-01T00:00:00"),  # 跨年
])
def test_get_period_start_ts(ts_str, tf, expected):
    ts = _ensure_tz_naive(pd.Timestamp(ts_str))
    result = _get_period_start_ts(ts, tf)
    assert result == pd.Timestamp(expected)

@pytest.mark.parametrize("last_ts,tf,cutoff,expected", [
    # 需要合成
    ("2026-07-03T14:00:00+08:00", "60分钟", "2026-07-03T14:47:00+08:00", True),
    ("2026-07-02T00:00:00", "日线", "2026-07-03T14:47:00+08:00", True),
    ("2026-07-03T00:00:00", "周线", "2026-07-07T10:00:00+08:00", True),  # ★ v2: 应该合成
    # 不需要合成
    ("2026-07-03T14:00:00+08:00", "60分钟", "2026-07-03T13:45:00+08:00", False),
    ("2026-07-03T00:00:00", "日线", "2026-07-03T10:00:00+08:00", False),
])
def test_needs_synthesis(last_ts, tf, cutoff, expected):
    db_rows = [{"Date": last_ts}]
    assert _needs_synthesis(tf, db_rows, cutoff) == expected


# ═══════════════════════════════════════════════════════════════
# Phase 3: 合成
# ═══════════════════════════════════════════════════════════════

def test_cascade_60min_to_daily_uses_synth_bar():
    """验证日线合成使用60分钟的合成bar"""
    # ... mock DB查询返回指定数据 ...
    # 验证: 合成日线bar的Open=60分钟合成bar的Open
```

### 7.3 回滚策略

如果集成后发现严重问题:

```
1. main()中移除_sync_all_cascading()调用
2. _load_chart_data()中恢复原有的_sync_to_display()调用
3. 删除data_loader.py中新增的函数(或保留但注释掉调用)
```

---

## 8. 兼容性影响

### 8.1 函数签名变更

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
| `_sync_to_display()` | 现有 | 不变 | `_load_chart_data()` (浏览模式) |
| `_load_chart_data()` | 现有 | 修改 (回测分支) | `streamlit_app.py` |

### 8.2 完全不受影响的部分

- **浏览模式**: 代码路径完全不变
- **`db.py`**: 无需变更
- **`state.py`**: 无需变更  
- **`components/sidebar.py`**: 无需变更
- **`components/charts.py`**: 无需变更
- **`backtest_logger.py`**: 无需变更
- **现有测试**: 全部兼容

### 8.3 调用方变更

| 文件 | 位置 | 变更 |
|------|------|------|
| `streamlit_app.py` | `_load_chart_data()` 回测分支 | 移除 `_sync_to_display()` 调用, 改为直接读parquet |
| `streamlit_app.py` | `main()` sorted_views 之前 | 新增 `has_data()` 检查 + `_sync_all_cascading()` 调用 |
| `data_loader.py` | 顶部 | 新增 `ALL_TFS` 常量 |
| `data_loader.py` | 新增(约200行) | 级联合成全部核心函数 |

---

## 附录: v1->v2 变更清单

### A.1 P0 修复 (阻断性)

| # | 问题 | v1行为 | v2修复 | 涉及函数 |
|---|------|--------|--------|---------|
| P0-1 | tz-aware vs tz-naive TypeError | 直接比较 Timestamp, 可能抛异常 | 所有比较前 `_ensure_tz_naive()` | `_needs_synthesis`, `_synthesize_incomplete_bar` |
| P0-2 | 周线 period_start 错误 | `_get_period_start_ts` 返回下一周五 | 改为 `last_ts + 1天` | `_get_period_start_ts` |
| P0-3 | Parquet Date 混合时区 | 合成bar Date=cutoff_date(有时区)与DB日线(无时区)混合 | `_format_synth_date()` 按TF统一格式 | `_synthesize_incomplete_bar`, `_format_synth_date` |

### A.2 P1 修复 (功能正确性)

| # | 问题 | v1行为 | v2修复 | 涉及函数 |
|---|------|--------|--------|---------|
| P1-1 | 合成数据来源依赖显示窗口 | 从 `prev_data`(120条)过滤 | 重新查询DB + 拼接更细TF合成bar | `_sync_all_cascading`, `_synthesize_incomplete_bar`, `_query_tf_for_period` |
| P1-2 | 合成窗口/显示窗口耦合 | prev_data 同时用于显示和合成 | `synth_cache` 只存合成bar, 显示/合成数据分离 | `_sync_all_cascading` |
| P1-3 | 日线合成60分钟从00:00开始 | 可能查询盘前空数据 | 调整为09:30 (美股开盘) | `_synthesize_incomplete_bar` |
| P1-4 | _load_chart_data降级路径 | parquet不存在时直接抛错 | 确认回退到 `_cached_fetch_stock` | `_load_chart_data` |
| P1-5 | 并发安全 | 4个fragment可能同时读写 | 写入在fragment之前, fragment只读 | 架构设计 |
| P1-6 | _get_period_start 命名混淆 | 两个函数相似名不同返回值 | 保留 `_get_period_start_ts`(Timestamp) 和 `_get_query_start_for_synthesis`(Timestamp) | 函数命名 |

### A.3 P2 改进 (健壮性)

| # | 改进 | 处理方式 |
|---|------|---------|
| P2-1 | min_tf=日线场景 | `_find_immediate_finer_tf` 自然处理: 日线不合成, 周线从日线合成 |
| P2-2 | 跨周末 | 查询自然返回空, 聚合使用实际数据 |
| P2-3 | 刚切换ticker | `has_data()` 前置检查 + `_cached_fetch_stock` 回退 |
| P2-4 | 数据不足 | `_synthesize_incomplete_bar` 返回 None, 不合成 |

### A.4 简化与优化

| 项目 | v1 | v2 | 理由 |
|------|----|----|------|
| `_get_period_start_ts` 月线 | 复杂月份计算(8行) | `ts + 1天` (1行) | last_ts是月末 → +1天=下月1日, 逻辑等价 |
| `_get_period_start_ts` 季线 | 复杂季度计算(8行) | `ts + 1天` (1行) | last_ts是季末 → +1天=下季1日, 逻辑等价 |
| `_get_query_start_for_synthesis` | 独立的分支逻辑(20行) | 统一为 `_get_period_start_ts` 相同逻辑 | 两者语义相同 |
| `prev_data` 变量 | 存储完整DataFrame(120行) | 改为 `synth_cache[tf]` 只存合成bar | 分离显示和合成数据 |

---

> **文档版本**: v2.0 | **下次评审**: Phase 3 实施前
> **变更行数**: ~600行新增/修改 (data_loader.py), ~5行修改 (streamlit_app.py)

---

## 10. 实施状态

| Phase | 内容 | 状态 | 文件 |
|-------|------|------|------|
| 1 | 时间格式工具 | ✅ 已实现 | data_loader.py:_ensure_tz_naive/_get_tz_suffix/_format_synth_date |
| 1 | 周期边界计算 | ✅ 已实现 | data_loader.py:_get_period_start_ts/_get_query_start_for_synthesis |
| 2 | 数据查询函数 | ✅ 已实现 | data_loader.py:_query_tf_from_db/_query_tf_for_period/_find_immediate_finer_tf |
| 2 | 合成核心 | ✅ 已实现 | data_loader.py:_needs_synthesis/_aggregate_bars/_synthesize_incomplete_bar |
| 3 | 级联主入口 | ✅ 已实现 | data_loader.py:_sync_all_cascading/_build_output_df/_write_parquet |
| 4 | Streamlit 集成 | ✅ 已实现 | streamlit_app.py: import + _load_chart_data + main() |
| 5 | 单元测试 | ✅ 已实现 | tests/test_cascading_synthesis.py |
| - | 冒烟验证 | ✅ 通过 | 33项核心逻辑测试全部通过 |

### 10.2 已修复 Bug (实施后)

| Bug | 提交 | 根因 | 修复 |
|-----|------|------|------|
| ALL_TFS 导入错误 | 086effc | import 路径循环依赖 | 改回本地常量 |
| _write_parquet 路径 | e040310 | parent少一层 | 统一 parent.parent.parent |
| P1 ×5 | e92a147 | n_pts/返回值/链断裂/回退/参数 | 见 all-issues-tracker.md |
| 时区边界 bar 遗漏 | d6f5839 | SQL字符串比较 tz后缀 | period_end 加 tz_suffix |
| 日线级反向边界 | 2a2599b | finer_tf格式不匹配 | 从 finer_tf 提取时区 |
| 跨时区假转换 | a87cea5 | 剥离-拼接 替代真转换 | tz_convert() |
| _needs_synthesis 日线失效 | 3bf8a47 | cutoff >= 下一周期 | cutoff > last_ts |
| _get_query_start_for_synthesis 窗口颠倒 | fec408a | last_ts+1天 超出cutoff | 返回 last_ts |
