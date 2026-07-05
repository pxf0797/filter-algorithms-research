# 回测模式高周期边界 Bar 合成方案

> 修订记录：v1.0 — 初始方案
> v1.1 — 全面审计，统一为动态 ALL_TFS.index 逻辑
> v1.2 — 实现完成，修复季线/分钟线边界计算 3 个 bug
> v1.3 — 修复 REPLACE vs APPEND 双场景逻辑 + Date 格式统一 ISO8601

---

## 1. 概述

### 1.1 问题描述

当前回测模式下，系统通过 `cutoff_date` 控制各周期的数据窗口。`cutoff_date` 由用户拖拽 slider 决定，其值为最小周期（如日线）DB 中第 `bar_index` 条记录的 `ts` 字段。

对于低周期（日线及以下），`ts` 与 `cutoff_date` 在同一粒度层级，SQL 的 `WHERE ts <= cutoff_date` 语义准确。但对于更高周期（`ALL_TFS.index(tf) > ALL_TFS.index(min_tf)` 的所有 TF），`ts` 是周期结束日（如周五/月末），当 `cutoff_date` 落在周期中间时（如周三），该条尚在进行中的高周期 bar 会被 SQL 过滤掉，造成信号缺失和数据错位。

### 1.2 目标

在回测模式下，对所有 `ALL_TFS.index(tf) > ALL_TFS.index(min_tf)` 的 TF（即周期大于最小周期的所有周期）自动检测并合成"进行中的不完整 bar"，确保：

- 截止到 `cutoff_date` 的每个高周期都有对应的 OHLC 数据
- 合成 bar 与 DB 中已完成的 bar 在时间轴上无缝衔接
- 所有周期（min_tf 到季线）的数据点数量一致，满足 `n_pts` 窗口要求
- 合成数据**不写入 DB**，仅写入 display parquet

### 1.3 适用范围

| 适用 | 不适用 |
|------|--------|
| 回测模式（`_cb_mode = True`） | 浏览模式所有逻辑 |
| 所有 `ALL_TFS.index(tf) > ALL_TFS.index(min_tf)` 的 TF | DB schema / upsert_kline / query_kline |
| `_sync_to_display()` 内部 | 滤波计算 / 施密特触发 / PnL 计算 |
| 各视图独立渲染路径 | 前端图表渲染 / 日期标记 |

---

## 2. 当前架构分析

### 2.1 回测数据流

```
用户拖拽 slider
       │
       ▼
_bar_index (回测模式入口, streamlit_app.py:1294)
       │
       ▼
_get_bar_date_from_db(ticker, min_tf, bar_index-1)
  → SELECT ts FROM kline WHERE ticker=? AND timeframe=?
    ORDER BY ts ASC LIMIT 1 OFFSET ?
  (streamlit_app.py:1120-1128)
       │
       ▼
cutoff_date (AppState._bt_cutoff_date, streamlit_app.py:1158)
       │
       ▼
_load_chart_data(market, ticker_code, tf, ...)
  → _sync_to_display(ticker_code, tf, n_pts=n_pts, cutoff_date=cutoff_date)
  (streamlit_app.py:123)
       │
       ▼
_sync_to_display (data_loader.py:139-173):
  SELECT ts, open, high, low, close, volume
  FROM kline WHERE ticker=? AND timeframe=? AND ts <= ?
  ORDER BY ts DESC LIMIT ?
       │
       ▼
data/display/{tf}.parquet → 各视图读取渲染
```

### 2.2 关键函数调用链

| 函数 | 文件 | 行号 | 职责 |
|------|------|------|------|
| `_sync_to_display()` | `services/data_loader.py` | 139-173 | 从 DB 查询 kline 写入 parquet |
| `_load_chart_data()` | `streamlit_app.py` | 115-172 | 调用 `_sync_to_display()`，从 parquet 读取 |
| `_render_backtest_mode()` | `streamlit_app.py` | 1267-1363 | 侧边栏回测模式 UI + 状态管理 |
| `_on_slider_change()` | `streamlit_app.py` | 1145-1158 | slider → cutoff_date 同步 |
| `_get_bar_date_from_db()` | `streamlit_app.py` | 1120-1128 | 从 DB 查询 bar 日期 |
| `_run_backtest_play()` | `streamlit_app.py` | 1225-1264 | 自动播放循环 |

### 2.3 问题根因

高周期 DB 时间戳语义：

| 周期 | `ts` 含义 | 示例 |
|------|-----------|------|
| 日线 | 交易日当天 | `2024-03-13` (周三) |
| 周线 | 周五（周期结束日） | `2024-03-08` (上周五), `2024-03-15` (本周五) |
| 月线 | 月末日历日 | `2024-02-29`, `2024-03-31` |
| 季线 | 季末日历日 | `2024-03-31`, `2024-06-30` |

当 `cutoff_date = '2024-03-13'`（周三）时：

```sql
-- 周线查询：
SELECT ts FROM kline WHERE ticker='AAPL' AND timeframe='周线' AND ts <= '2024-03-13'
-- 结果：...2024-03-01(上周五), 2024-03-08(上周五)
-- 缺失：2024-03-15(本周五) 因为 ts > '2024-03-13'
```

结果是回测窗口中的周线数据缺少正在进行的本周 bar，导致：

- 周线数据点数量少于日线，无法对齐显示
- 滤波信号缺失——本周趋势不在计算范围内
- 跨周期 PnL 对齐（`show_cross_pnl`）因周线数据空洞而失效

---

## 3. 方案设计

### 3.1 总体思路

采用**方案 A：直接用最小周期（min_tf）数据聚合到任意高周期，不经过中间层级**。

核心原理：对所有 `ALL_TFS.index(tf) > ALL_TFS.index(min_tf)` 的 TF（即周期大于最小周期的所有周期），检查 `cutoff_date` 是否落在该周期的最后一个已完成 bar 的下一个周期边界之前。如果是，则从 min_tf DB 数据中实时聚合出该不完整 bar 的 OHLCV，追加到结果末尾。

> `ALL_TFS = ["1分钟","5分钟","15分钟","60分钟","日线","周线","月线","季线"]`，索引越小周期越精细。
>
> 例如：`min_tf="日线"`（索引 4）→ 合成周线(5)、月线(6)、季线(7)；`min_tf="60分钟"`（索引 3）→ 合成日线(4)、周线(5)、月线(6)、季线(7)。

### 3.2 周期边界计算规则

#### `_get_period_end(ts, tf)` 函数

| 参数 `tf` | 计算规则 | 示例 |
|-----------|----------|------|
| `"5分钟"` | 找到下一个 5 分钟整点边界（精确实现需考虑交易所交易时间） | `2024-03-13 09:32` → `2024-03-13 09:35` |
| `"15分钟"` | 找到下一个 15 分钟整点边界（精确实现需考虑交易所交易时间） | `2024-03-13 09:32` → `2024-03-13 09:45` |
| `"60分钟"` | 找到下一个整点小时（精确实现需考虑交易所交易时间） | `2024-03-13 09:30` → `2024-03-13 10:00` |
| `"日线"` | 找到下一天的 00:00（精确实现需考虑交易所交易时间） | `2024-03-12` → `2024-03-13` |
| `"周线"` | 找到 `ts` 后的第一个周五 | `2024-03-08（周五）` → `2024-03-15（周五）` |
| `"月线"` | 找到 `ts` 后的第一个月末日 | `2024-02-29` → `2024-03-31` |
| `"季线"` | 找到下一个季度末（3/6/9/12月） | `2024-02-29` → `2024-03-31`, `2024-06-15` → `2024-09-30` |

实现伪代码：

```python
def _get_period_end(ts: pd.Timestamp, tf: str) -> pd.Timestamp:
    """计算给定 ts 之后的下一个周期结束时间戳。"""
    import pandas as pd
    ts = pd.Timestamp(ts)

    if tf == "5分钟":
        # 下一个 5 分钟整点边界（精确实现需考虑交易所交易时间）
        minute_ceil = ((ts.minute // 5) + 1) * 5
        if minute_ceil >= 60:
            ts = ts.ceil("1h") + pd.Timedelta(hours=1)  # 跨小时取整
            minute_ceil = 0
        return ts.replace(minute=minute_ceil, second=0, microsecond=0)

    elif tf == "15分钟":
        # 下一个 15 分钟整点边界（精确实现需考虑交易所交易时间）
        minute_ceil = ((ts.minute // 15) + 1) * 15
        if minute_ceil >= 60:
            ts = ts.ceil("1h") + pd.Timedelta(hours=1)
            minute_ceil = 0
        return ts.replace(minute=minute_ceil, second=0, microsecond=0)

    elif tf == "60分钟":
        # 下一个整点小时（精确实现需考虑交易所交易时间）
        return (ts + pd.Timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

    elif tf == "日线":
        # 下一天 00:00（精确实现需考虑交易所交易时间）
        return (ts + pd.Timedelta(days=1)).normalize()

    elif tf == "周线":
        # 计算下一个周五：weekday()=4 是周五
        days_ahead = 4 - ts.weekday()  # 周五=4
        if days_ahead <= 0:
            days_ahead += 7  # 已经是周五或已过，跳到下周五
        return (ts + pd.Timedelta(days=days_ahead)).normalize()

    elif tf == "月线":
        # 下个月的第一天 - 1天 = 本月末日
        if ts.month == 12:
            next_month = pd.Timestamp(year=ts.year + 1, month=1, day=1)
        else:
            next_month = pd.Timestamp(year=ts.year, month=ts.month + 1, day=1)
        return next_month - pd.Timedelta(days=1)

    elif tf == "季线":
        # 下一个季末：3/6/9/12月
        q_end_month = ((ts.month - 1) // 3 + 1) * 3
        if q_end_month <= ts.month:
            # 已过本季末，推至下季
            if q_end_month == 12:
                q_end_month = 3
                year = ts.year + 1
            else:
                q_end_month = q_end_month + 3
                year = ts.year
        else:
            year = ts.year
        next_q_end = pd.Timestamp(year=year, month=q_end_month, day=1)
        return next_q_end - pd.Timedelta(days=1)

    else:
        return ts
```

#### `_synthesize_higher_tf_bar(conn, ticker, period_start, cutoff_date, period_end)` 函数

```python
def _synthesize_higher_tf_bar(conn, ticker, min_tf, period_start, cutoff_date, period_end):
    """从 min_tf DB 数据聚合为高周期 OHLC bar。

    参数：
        period_start: 不完整 bar 的起始时间（含，ISO 字符串）
        cutoff_date: 截止时间（含，ISO 字符串）
        period_end:   该 bar 的规范时间戳（周期结束日，ISO 字符串）

    返回 dict 或 None:
        {ts, open, high, low, close, volume}
    """
    rows = conn.execute(
        """SELECT ts, open, high, low, close, volume
           FROM kline
           WHERE ticker=? AND timeframe=? AND ts >= ? AND ts <= ?
           ORDER BY ts ASC""",
        (ticker, min_tf, period_start, cutoff_date),
    ).fetchall()

    if not rows:
        return None

    opens = [r[1] for r in rows]
    highs = [r[2] for r in rows]
    lows  = [r[3] for r in rows]
    closes = [r[4] for r in rows]
    volumes = [r[5] for r in rows]

    return {
        "Date": period_end,
        "Open": opens[0],
        "High": max(highs),
        "Low":  min(lows),
        "Close": closes[-1],
        "Volume": sum(volumes),
    }
```

#### `_get_period_start(last_completed_ts, tf)` 函数

```python
def _get_period_start(last_completed_ts: pd.Timestamp, tf: str) -> str:
    """计算下一个周期的起始时间点（含）。

    这个函数用于确定不完整 bar 的数据查询起始点（min_tf 维度）。
    """
    ts = pd.Timestamp(last_completed_ts)

    if tf == "5分钟":
        # 下一个 5 分钟周期的起点
        start = ts + pd.Timedelta(minutes=1)
    elif tf == "15分钟":
        # 下一个 15 分钟周期的起点
        start = ts + pd.Timedelta(minutes=1)
    elif tf == "60分钟":
        # 下一个周期的起点 = last_completed_ts 的下一分钟
        start = ts + pd.Timedelta(minutes=1)
    elif tf == "日线":
        # 下一个交易日起点（简化：下一天 00:00；精确实现需考虑交易所交易时间）
        start = ts + pd.Timedelta(days=1)
    elif tf == "周线":
        # last_completed_ts 是上周五，下一个周一起点是 last_completed_ts + 3 天
        # 但如果遇到节假日，DB 中可能没有 min_tf 数据，查询会返回空
        # 直接用 last_completed_ts + 1 天，然后由 SQL 过滤出实际交易日
        start = ts + pd.Timedelta(days=1)
    elif tf == "月线":
        # 下个月第一天
        if ts.month == 12:
            start = pd.Timestamp(year=ts.year + 1, month=1, day=1)
        else:
            start = pd.Timestamp(year=ts.year, month=ts.month + 1, day=1)
    elif tf == "季线":
        # 下个季度第一天
        q_start_month = ((ts.month - 1) // 3) * 3 + 1
        if ts.month >= q_start_month + 3:
            # 已过本季
            if q_start_month + 3 <= 12:
                start = pd.Timestamp(year=ts.year, month=q_start_month + 3, day=1)
            else:
                start = pd.Timestamp(year=ts.year + 1, month=1, day=1)
        else:
            start = pd.Timestamp(year=ts.year, month=q_start_month, day=1)
    else:
        start = ts + pd.Timedelta(days=1)

    return start.strftime("%Y-%m-%d")
```

### 3.3 核心算法

#### `_sync_to_display()` 修改

```
输入: ticker_code, tf, n_pts, cutoff_date（回测模式）
       + 新增参数: min_tf（从回测状态传入）

输出: (success, count)

算法:

1. 从 DB 查询已完成 bar:
   SELECT ... FROM kline WHERE ticker=? AND timeframe=? AND ts <= cutoff_date
   ORDER BY ts DESC LIMIT n_pts

   if rows 为空: 返回 (False, 0) 或尝试聚合

2. 取结果中最后一条 bar 的 ts → last_completed_ts

3. 计算 next_period_end = _get_period_end(last_completed_ts, tf)

4. 判断是否触发生成:
   cutoff_date < next_period_end → 需要合成本周期的不完整 bar
   否则 → DB 数据已经包含了完整的最后一个 bar，直接返回

5. 触发合成:
   a. period_start = _get_period_start(last_completed_ts, tf)
   b. 从 DB 查询 min_tf 数据:
      SELECT ... FROM kline WHERE ticker=? AND timeframe=?
      AND ts >= ? AND ts <= ?
      ORDER BY ts ASC
      (参数: ticker, min_tf, period_start, cutoff_date)
   c. 如果无数据 → 跳过合成（可能节假日）
   d. 聚合为高周期 OHLC bar
   e. 追加到 rows 末尾
   f. 如果 len(rows) > n_pts，截断掉最早的 bar（保持 n_pts 窗口）

6. 写入 parquet
```

### 3.4 数据流变更

```
当前:
  cutoff_date → _sync_to_display(tf) → DB WHERE ts <= cutoff_date → parquet

变更后:
  cutoff_date + min_tf → _sync_to_display(tf, min_tf)
    → 1. DB WHERE ts <= cutoff_date → completed_bars
    → 2. 检查最后一个已完成 bar → 是否需要合成
    → 3. 如果需要：DB WHERE ts∈[period_start, cutoff_date] AND tf=min_tf → 聚合
    → 4. 追加到 completed_bars → 截断到 n_pts → parquet
```

### 3.5 完整伪代码

```python
def _sync_to_display(ticker_code: str, tf: str, day_offset: int = 0,
                     n_pts: int = 120, cutoff_date: Optional[str] = None,
                     min_tf: Optional[str] = None) -> Tuple[bool, int]:
    """同步数据到 display parquet。

    cutoff_date=None: 浏览模式（不变）
    cutoff_date=YYYY-MM-DD: 回测模式
    min_tf: 回测模式下的最小周期 TF，用于聚合高周期 bar
    """
    if cutoff_date is None:
        # 浏览模式：原有逻辑（不变）
        df = query_kline(ticker_code, tf, n_pts, day_offset=day_offset)
        if len(df) < 5:
            return False, len(df)
        df["Date"] = pd.to_datetime(df["Date"])
        display_dir = Path(__file__).parent.parent.parent / "data" / "display"
        display_dir.mkdir(parents=True, exist_ok=True)
        df.to_parquet(display_dir / f"{tf}.parquet", index=False)
        return True, len(df)

    # 回测模式
    from db import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT ts, open, high, low, close, volume
               FROM kline WHERE ticker=? AND timeframe=? AND ts <= ?
               ORDER BY ts DESC LIMIT ?""",
            (ticker_code, tf, cutoff_date, n_pts),
        ).fetchall()

    if not rows:
        return False, 0

    # 判断是否需要合成：当前 tf 在 ALL_TFS 中排在 min_tf 之后（周期更大）
    synthesized_bar = None

    if min_tf and cutoff_date and ALL_TFS.index(tf) > ALL_TFS.index(min_tf):
        last_completed_ts = rows[-1][0]  # 最后一条（最早时间）的 ts
        next_period_end = _get_period_end(last_completed_ts, tf)
        cutoff_dt = pd.Timestamp(cutoff_date)

        if cutoff_dt < next_period_end:
            period_start = _get_period_start(last_completed_ts, tf)

            with get_conn() as conn:
                min_rows = conn.execute(
                    """SELECT open, high, low, close, volume
                       FROM kline
                       WHERE ticker=? AND timeframe=? AND ts >= ? AND ts <= ?
                       ORDER BY ts ASC""",
                    (ticker_code, min_tf, period_start, cutoff_date),
                ).fetchall()

            if min_rows:
                opens = [r[0] for r in min_rows]
                highs = [r[1] for r in min_rows]
                lows  = [r[2] for r in min_rows]
                closes = [r[3] for r in min_rows]
                volumes = [r[4] for r in min_rows]

                synthesized_bar = {
                    "Date": next_period_end.strftime("%Y-%m-%d"),
                    "Open": opens[0],
                    "High": max(highs),
                    "Low":  min(lows),
                    "Close": closes[-1],
                    "Volume": sum(volumes),
                }

    needs_synthesis = synthesized_bar is not None

    # 构建 DataFrame
    if needs_synthesis and synthesized_bar:
        # rows 是 DESC 结果，反转
        # 但 synthesized_bar 时间更晚，需要放在 rows 反转后的末尾
        # 所以先建一个列表，反转 rows，追加合成 bar，再截断
        data = []
        for r in reversed(rows):
            data.append({
                "Date": r[0], "Open": r[1], "High": r[2],
                "Low": r[3], "Close": r[4], "Volume": r[5],
            })
        data.append(synthesized_bar)
        # 裁剪到 n_pts 条（去掉最早的）
        if len(data) > n_pts:
            data = data[-n_pts:]
        df = pd.DataFrame(data)
    else:
        rows = list(reversed(rows))
        if len(rows) > n_pts:
            rows = rows[-n_pts:]
        df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])

    display_dir = Path(__file__).parent.parent.parent / "data" / "display"
    display_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(display_dir / f"{tf}.parquet", index=False)
    return True, len(df)
```

### 3.6 示意图

```
cutoff_date = 2024-03-13 (周三)

DB 周线 WHERE ts <= '2024-03-13':
  2024-03-01(周五) ✓
  2024-03-08(周五) ✓  ← last_completed_ts
  2024-03-15(周五) ✗  (被 WHERE 过滤掉，ts > cutoff_date)

合成判断：
  _get_period_end('2024-03-08', '周线') → 2024-03-15(周五)
  cutoff_date(2024-03-13) < next_period_end(2024-03-15) → 需要合成

合成过程：
  _get_period_start('2024-03-08', '周线') → '2024-03-09' (周六)
  min_tf 日线查询: ts ∈ ['2024-03-09', '2024-03-13']
  → SQL 只返回有数据的交易日: 03-11(周一), 03-12(周二), 03-13(周三)

  聚合：
  Open  = 03-11 的 open
  High  = max(03-11 的 high, 03-12 的 high, 03-13 的 high)
  Low   = min(03-11 的 low, 03-12 的 low, 03-13 的 low)
  Close = 03-13 的 close
  Volume = sum(03-11 的 volume, 03-12 的 volume, 03-13 的 volume)
  ts    = 2024-03-15(规范时间戳，本周五)

最终结果：[..., 03-08周线(DB), 03-15周线(合成)]
```

#### 示例 2：min_tf="60分钟" → 合成日线 bar

```
cutoff_date = 2024-03-13 15:30（60分钟线某 bar 的 ts）

DB 日线 WHERE ts <= '2024-03-13':
  2024-03-12(周二) ✓  ← last_completed_ts
  2024-03-13(周三) ✗  (日线 ts 是当天收盘 2024-03-13 23:59, cutoff_date 是 15:30, ts > cutoff_date)

合成判断：
  _get_period_end('2024-03-12', '日线') → 2024-03-13(周三)
  cutoff_date(2024-03-13 15:30) < next_period_end(2024-03-13 23:59) → 需要合成

合成过程：
  _get_period_start('2024-03-12', '日线') → '2024-03-13'
  60分钟查询: ts ∈ ['2024-03-13 09:30', '2024-03-13 15:30']
  → SQL 返回 03-13 当天到 15:30 为止的 60分钟 bar

  聚合：
  Open  = 第一根 60分钟 bar 的 open
  High  = max(各 60分钟 bar 的 high)
  Low   = min(各 60分钟 bar 的 low)
  Close = 最后一根 60分钟 bar 的 close
  Volume = sum(各 60分钟 bar 的 volume)
  ts    = 2024-03-13(规范时间戳，当日收盘)

最终结果：[..., 03-12日线(DB), 03-13日线(合成)]
```

当 `cutoff_date` 推进到 `2024-03-13`（周五收盘）：

```
DB 周线 WHERE ts <= '2024-03-15':
  ...2024-03-08(周五) ✓
  2024-03-15(周五) ✓ ← last_completed_ts (此刻 DB 中已有完整周线)

  _get_period_end('2024-03-15', '周线') → 2024-03-22(下周五)
  cutoff_date(2024-03-15) < next_period_end(2024-03-22) → 又开始合成新一周
```

---

## 4. 变更影响分析

### 4.1 修改文件清单

#### 文件 1：`services/data_loader.py`

| 位置 | 改动 |
|------|------|
| 第 8 行 import | 新增 `import pandas as pd`（当前 `pd` 已在第 8 行导入，无需变更） |
| 第 139-173 行 `_sync_to_display()` 签名 | `min_tf: Optional[str] = None` 参数 |
| 第 146-163 行（回测分支） | 新增合成逻辑：周期边界判断、min_tf 聚合查询、bar 追加与截断 |
| 文件末尾 | 新增 3 个私有函数 `_get_period_end()`, `_get_period_start()`, `_synthesize_higher_tf_bar()`（约 60 行） |

#### 文件 2：`streamlit_app.py`

| 位置 | 改动 |
|------|------|
| 第 123 行 `_load_chart_data()` | `_sync_to_display()` 调用处传入 `min_tf`——读取 `AppState.get("_min_tf", "")` |
| 第 137 行（浏览模式） | 不需要传入 `min_tf`，传递默认 None |

#### 文件 3：`tests/test_boundary.py`

| 位置 | 改动 |
|------|------|
| 文件末尾 | 新增 `TestBacktestBarSynthesis` 测试类，覆盖合成逻辑 |

### 4.2 修改代码

#### `data_loader.py` — `_sync_to_display()` 签名变更

```python
# 原签名（第 139-140 行）
def _sync_to_display(ticker_code: str, tf: str, day_offset: int = 0, n_pts: int = 120,
                     cutoff_date: Optional[str] = None) -> Tuple[bool, int]:

# 新签名
def _sync_to_display(ticker_code: str, tf: str, day_offset: int = 0, n_pts: int = 120,
                     cutoff_date: Optional[str] = None,
                     min_tf: Optional[str] = None) -> Tuple[bool, int]:
```

#### `streamlit_app.py` — `_load_chart_data()` 调用处修改

```python
# 第 121-134 行，回测分支：
if window_start is not None:
    # 原代码：
    # ok, count = _sync_to_display(ticker_code, tf, n_pts=n_pts, cutoff_date=cutoff_date)

    # 新代码：
    _bt_min_tf = AppState.get("_min_tf", "")
    ok, count = _sync_to_display(ticker_code, tf, n_pts=n_pts,
                                  cutoff_date=cutoff_date, min_tf=_bt_min_tf)
```

### 4.3 不影响的部分

- DB schema（`kline` 表结构不变）
- `upsert_kline()` / `query_kline()` — 不修改
- 浏览模式所有逻辑 — `cutoff_date=None` 分支不变
- 回测模式下 min_tf 自身及更精细周期的数据加载 — `ALL_TFS.index(tf) <= ALL_TFS.index(min_tf)` 时跳过合成
- 滤波计算 / 施密特触发 / PnL 计算 — 只消费 parquet 数据，无感知
- 前端图表渲染 — 只消费 parquet 数据，无感知
- 回测播放循环 / slider / 导航按钮 — 仅改变 `_bar_index` 和 `cutoff_date`
- Parquet 写入路径 — `data/display/{tf}.parquet` 不变

### 4.4 变更前后行为对比

| 场景 | 变更前 | 变更后 |
|------|--------|--------|
| 日线（min_tf）回测 | `WHERE ts <= '周三'` 返回正确 | 不变 |
| 周线，cutoff=周三 | 漏掉本周 bar | 从 min_tf 聚合 + 追加 |
| 周线，cutoff=周五 | 查不到下周 bar（正常） | DB 返回完整本周 bar，但同时开始合成下一周期不完整 bar |
| 月线，cutoff=月中 | 漏掉本月 bar | 从 min_tf 聚合 + 追加 |
| 季线，cutoff=季中 | 漏掉本季 bar | 从 min_tf 聚合 + 追加 |
| 日线，min_tf="60分钟"，cutoff=03-13 15:30 | 漏掉本周三日线 bar | 从 60分钟 聚合 + 追加日线 bar |
| 5分钟，min_tf="1分钟"，cutoff=09:32 | 漏掉 09:35 这个 5分钟 bar | 从 1分钟 聚合 + 追加 5分钟 bar |
| 周线，min_tf="1分钟"，cutoff=周三 | 漏掉本周周线 bar | 从 1分钟 聚合 + 追加周线 bar |
| 数据刷新后首次回测 | 依赖 DB 写入顺序 | 不变（DB 数据仍被完整使用） |
| 最小周期本身 | N/A | `ALL_TFS.index(tf) == ALL_TFS.index(min_tf)` 时跳过合成 |

### 4.5 副作用分析

| 潜在副作用 | 影响 | 措施 |
|-----------|------|------|
| 合成 bar 的 Volume 和 min_tf 累积一致 | 正确（累加） | 无 |
| 合成 bar 的 High/Low 来自 min_tf 中的极值 | 可能高于实际周线 High（min_tf 的 OHLC 是子区间精确值） | 这是回测可接受近似，min_tf 极值本身就是原始数据的精确值 |
| 合成 bar 与 DB 中实际 bar 的 OHLC 不完全一致 | 仅在 `cutoff_date` 推进到周期结束后才对比 | 当 DB 中有真实数据时不使用合成 |
| 多视图不同 `min_tf` | 各视图使用各自 `AppState._min_tf` | 当前架构中 `_min_tf` 是全局共享的（取所有视图中的最小 TF），见 `_get_min_tf_and_count()` |
| `n_pts` 截断 + 合成 bar 导致总条数超过 `n_pts` | 已做截断处理 | 追加后取 `data[-n_pts:]` |

---

## 5. 边界与异常处理

### 5.1 节假日 / 周末

**场景**：`cutoff_date = 2024-03-11（周一）`，`period_start = 2024-03-09（周六）`，min_tf 查询 `ts >= '2024-03-09' AND ts <= '2024-03-11'` 只返回周一的数据。

**处理**：`_synthesize_higher_tf_bar()` 使用 `ts >= period_start AND ts <= cutoff_date`，周末无数据 → SQL 返回空 → 跳过合成。此时该周线表现为只有已完成的上周 bar，接收后前端显示更少的数据点。

**改进建议**：当聚合结果为空时，可以回退到仅使用 DB 中已有的数据，不做特殊处理。

### 5.2 数据不足

**场景**：股票上市不足一周，DB 中周线只有 1 条。

**处理**：合成逻辑检查 `rows` 非空后取 `last_completed_ts`，正常计算 `next_period_end`。如果 `cutoff_date < next_period_end` 且 min_tf 也数据不足 → 聚合返回空 → 跳过合成。最终 parquet 可能不足 `n_pts` 条，由下游 `_load_chart_data()` 的长度检查处理。

### 5.3 周期边界恰好对齐

**场景**：`cutoff_date == next_period_end`（例如 cutoff_date 恰好等于当前 TF 的周期结束日）。

**判断**：`cutoff_date < next_period_end` 为 False → 不触发合成，DB 查询正常返回。

**特殊情况**：对于月线，如果 `cutoff_date = 2024-03-31（月末日）` 且 DB 中已有一条 `ts = 2024-03-31` 的月线 bar → 不触发合成。如果由于 DB 写入时序，当月线 bar 尚未写入 DB → 触发合成，聚合结果与最终 DB bar 一致。

### 5.4 多视图 min_tf 不一致

**场景**：视图 1 使用 "日线"，视图 2 使用 "60分钟"。

**当前架构**：`_get_min_tf_and_count()` 遍历所有视图的 `tf`，取 `ALL_TFS` 中索引最小的（最精细周期）为全局 `_min_tf`（见 `streamlit_app.py` 第 427-463 行）。所以所有视图共享同一个 `_min_tf`。

**对合成的影响**：合成时使用全局 `_min_tf` 作为聚合源。如果视图 2 的 tf = "60分钟" 且被定为 `_min_tf`，周线的合成就基于 60 分钟线聚合，精度比日线更高。

**潜在问题**：60 分钟的数据量可能很大，查询和聚合性能下降。但当前所有 TF 的查询都使用 `n_pts` 限制，实际生效范围有限。

### 5.5 合成 bar 重复

**场景**：`cutoff_date` 推进到 `next_period_end` 当天，DB 中可能也刚完成该周线的写入（`upsert_kline` 在切换回测模式时可能写入）。

**处理**：合成判断 `cutoff_date < next_period_end` 为 False → 不走合成 → DB 返回完整数据。不会重复。

### 5.6 最小周期本身也需要合成

**场景**：当 `_min_tf` 是 60 分钟，且 `cutoff_date` 发生在某个小时内。此时日线（60分钟的下一个更大周期）**需要**合成。但 `_min_tf` 自身（60 分钟）**不需要**合成。

**规则**：`ALL_TFS.index(tf) > ALL_TFS.index(min_tf)` — 只有严格大于 min_tf 的周期才进行合成。因此：

| min_tf | ALL_TFS 索引 | 跳过合成（`ALL_TFS.index(tf) <= ALL_TFS.index(min_tf)`） | 合成（`ALL_TFS.index(tf) > ALL_TFS.index(min_tf)`） |
|--------|-------------|--------------------------------------------------------|---------------------------------------------------|
| 1分钟  | 0 | 1分钟 | 5分钟, 15分钟, 60分钟, 日线, 周线, 月线, 季线 |
| 5分钟  | 1 | 1分钟, 5分钟 | 15分钟, 60分钟, 日线, 周线, 月线, 季线 |
| 15分钟 | 2 | 1分钟, 5分钟, 15分钟 | 60分钟, 日线, 周线, 月线, 季线 |
| 60分钟 | 3 | 1分钟, 5分钟, 15分钟, 60分钟 | 日线, 周线, 月线, 季线 |
| 日线   | 4 | 1分钟, 5分钟, 15分钟, 60分钟, 日线 | 周线, 月线, 季线 |
| 周线   | 5 | 1分钟, 5分钟, 15分钟, 60分钟, 日线, 周线 | 月线, 季线 |
| 月线   | 6 | 所有 1分钟~月线 | 季线 |
| 季线   | 7 | 所有周期 | _（无，无周期大于季线）_ |

**合理性**：`cutoff_date` 总是来自 `_min_tf` 的 bar 时间戳，所以 `_min_tf` 的每个 bar 在 DB 中都是完整的。不需要对 `_min_tf` 自身做合成。同时，对于更精细于 min_tf 的周期，回测模式下也不会显示这些周期（回测只显示 min_tf 及以上周期），因此也不需要合成。

---

## 6. 测试策略

### 6.1 单元测试

新建测试类 `TestBacktestBarSynthesis`，覆盖：

| 测试用例 | 输入 | 预期 |
|----------|------|------|
| `test_get_period_end_weekly_midweek` | ts=周三, tf=周线 | 返回下周五 |
| `test_get_period_end_weekly_friday` | ts=周五, tf=周线 | 返回下周五（跳过本周） |
| `test_get_period_end_weekly_saturday` | ts=周六, tf=周线 | 返回下周五 |
| `test_get_period_end_monthly_midmonth` | ts=月中, tf=月线 | 返回本月末日 |
| `test_get_period_end_monthly_eoy` | ts=Dec, tf=月线 | 返回 Dec-31 |
| `test_get_period_end_quarterly_mid` | ts=季中, tf=季线 | 返回本季末 |
| `test_get_period_end_quarterly_end` | ts=季末日, tf=季线 | 返回下季末 |
| `test_synthesize_normal` | 3 条日线数据 | 正确聚合 OHLCV |
| `test_synthesize_empty` | 无 min_tf 数据 | 返回 None |
| `test_synthesize_single_row` | 1 条分钟数据 | 每条字段映射正确 |
| `test_sync_to_display_already_complete` | cutoff=周期结束日，DB 有数据 | 不走合成 |
| `test_sync_to_display_min_tf_skip` | ALL_TFS.index(tf) <= ALL_TFS.index(min_tf) | 不走合成 |

### 6.2 手动测试场景

| 场景 | 操作步骤 | 验证 |
|------|----------|------|
| 周线回测正常穿梭 | 切换到回测模式，slider 从右向左快速拉动 | 周线不发生跳变/空白 |
| 跨周边界 | slider 推进到周五（收盘），再退到周四 | 周线 bar 的 OHLC 在周五时恢复正常（不再显示合成），后退到周四时重新显示合成 |
| 月线/季线合成 | 类似周线，验证月末/季末边界 | 月线/季线连续无断档 |
| 多视图一致 | 视图 1 = 日线(最小值)，视图 2 = 周线 | 两个视图数据点在相同 `bar_index` 下都有值 |
| 节假日跳过 | 选择包含假期（如春节/圣诞）的股票 | 合成不崩溃，无数据时跳过 |
| 新上市股票 | 选择一个上市不足 4 周的股票 | 长周期（月线/季线）无数据时不崩溃 |

### 6.3 验证方法

1. **数据验证**：在关键断点处打印合成 bar 的 OHLCV 与后续 DB 真实 bar 对比
2. **可视化验证**：回测模式下观察 K 线图，合成的 bar 与真实 bar 在视觉上应连续
3. **n_pts 验证**：打印每个周期 parquet 的行数，应都等于 min(n_pts, DB 最大可用)
4. **性能验证**：合成查询添加日志，确保一次回测 slider 滑动只触发一次合成

---

## 7. 实施步骤

### 7.1 分支策略

```
master
  └── feature/backtest-bar-synthesis    (新分支)
        └── 按以下顺序提交
```

### 7.2 开发完成状态

| 步骤 | 内容 | 状态 |
|------|------|------|
| 1 | `data_loader.py`：添加 `_get_period_end()`, `_get_period_start()` | 已完成 |
| 2 | `data_loader.py`：修改 `_sync_to_display()` 签名 + 合成分支 | 已完成 |
| 3 | `streamlit_app.py`：`_load_chart_data()` 传入 `min_tf` | 已完成 |
| 4 | 单元测试（46 个，覆盖全部周期边界计算） | 已完成 |
| 5 | Bug 修复：季线 `_get_period_end` 月末计算、分钟线跨小时、季线 `_get_period_start` | 已完成 |
| 6 | 手动回测验证 | 待完成 |

### 7.3 风险点

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|----------|
| `_get_period_end()` 节假日计算偏差 | 低 | 合成 bar 时间戳错误 | 使用 pandas 内置日历逻辑，添加节假白名单 |
| 合成 bar 的 Open/High/Low/Close 与 DB 真实值偏差大 | 中 | 回测信号误差 | 仅在确实缺少数据时使用合成，且文档说明这是回测近似 |
| 性能：每次 slider 移动都触发 min_tf 查询 | 低（min_tf 查询范围仅几天的数据） | 响应变慢 | 无需额外优化，SQL 查询范围天然受限 |
| 并发：多视图同时调用 `_sync_to_display()` 写入同一 parquet | 低（Streamlit 单线程模型） | 无并发问题 | 无需处理 |

---

## 8. 附录

### 8.1 合成范围速查表

给定 `min_tf` 后，回测模式下参与合成的 TF 范围由 `ALL_TFS.index(tf) > ALL_TFS.index(min_tf)` 决定：

| min_tf | ALL_TFS 索引 | 参与合成的 TF |
|--------|-------------|--------------|
| 1分钟  | 0 | 5分钟(1), 15分钟(2), 60分钟(3), 日线(4), 周线(5), 月线(6), 季线(7) |
| 5分钟  | 1 | 15分钟(2), 60分钟(3), 日线(4), 周线(5), 月线(6), 季线(7) |
| 15分钟 | 2 | 60分钟(3), 日线(4), 周线(5), 月线(6), 季线(7) |
| 60分钟 | 3 | 日线(4), 周线(5), 月线(6), 季线(7) |
| 日线   | 4 | 周线(5), 月线(6), 季线(7) |
| 周线   | 5 | 月线(6), 季线(7) |
| 月线   | 6 | 季线(7) |
| 季线   | 7 | _（无，无周期大于季线）_ |

> 注意：`ALL_TFS` 定义于 `components/sidebar.py` 第 13 行，当前顺序为 `["1分钟","5分钟","15分钟","60分钟","日线","周线","月线","季线"]`。

### 8.2 周期边界计算参考表

#### 60分钟

| 输入 `ts`（上一个完整小时） | `_get_period_end`（下一个整点小时） |
|----------------------------|-----------------------------------|
| `2024-03-13 09:30` | `2024-03-13 10:00` |
| `2024-03-13 15:30` | `2024-03-13 16:00` |
| `2024-03-13 23:30` | `2024-03-14 00:00` |

#### 日线

| 输入 `ts`（前一个交易日） | `_get_period_end`（下一天 00:00；精确实现需考虑交易所交易时间） |
|--------------------------|-------------------------------------------------------------|
| `2024-03-12` | `2024-03-13` |
| `2024-03-31` | `2024-04-01` |
| `2024-12-31` | `2025-01-01` |

#### 周线

| 输入 `ts`（上周五） | `_get_period_end`（下周五） |
|---------------------|---------------------------|
| `2024-03-01`（周五） | `2024-03-08`（周五） |
| `2024-03-08`（周五） | `2024-03-15`（周五） |
| `2024-12-27`（周五） | `2025-01-03`（周五） |

#### 月线

| 输入 `ts`（月末日） | `_get_period_end`（下月末日） |
|---------------------|-----------------------------|
| `2024-01-31` | `2024-02-29` |
| `2024-02-29` | `2024-03-31` |
| `2024-12-31` | `2025-01-31` |

#### 季线

| 输入 `ts`（季末日） | `_get_period_end`（下季末日） |
|---------------------|-----------------------------|
| `2024-03-31` | `2024-06-30` |
| `2024-06-30` | `2024-09-30` |
| `2024-12-31` | `2025-03-31` |

### 8.3 关键术语映射

| 术语 | 含义 |
|------|------|
| `cutoff_date` | 回测模式截止日期，来自 `_bar_index` 在 min_tf DB 中的位置 |
| `last_completed_ts` | 从 DB 查询到的最高周期最后一条 bar 的 ts（已完成 bar 中最晚的） |
| `next_period_end` | `last_completed_ts` 之后的下一个周期结束日（如周五/月末） |
| `period_start` | 合成 bar 的起始时间戳，即 `last_completed_ts` 加一天后的周期起点 |
| 合成 bar | 从 min_tf 数据实时聚合的高周期 OHLC bar，仅写入 parquet 不写 DB |
| `_min_tf` | 所有视图中的最小周期 TF（如日线），全局共享 |
| `n_pts` | 每个视图配置的数据点窗口大小（slider 范围 20-300） |

### 8.4 相关代码位置索引

| 代码片段 | 文件 | 行号 |
|----------|------|------|
| `ALL_TFS` 定义 | `components/sidebar.py` | 13 |
| `TF_HIERARCHY` 定义 | `components/sidebar.py` | 48-52 |
| `_sync_to_display()` | `services/data_loader.py` | 139-173 |
| `_load_chart_data()` | `streamlit_app.py` | 115-172 |
| `_get_min_tf_and_count()` | `streamlit_app.py` | 427-463 |
| `_render_backtest_mode()` | `streamlit_app.py` | 1267-1363 |
| `_on_slider_change()` | `streamlit_app.py` | 1145-1158 |
| `_get_bar_date_from_db()` | `streamlit_app.py` | 1120-1128 |
| DB `kline` 表 schema | `db.py` | 33-47 |

---

*文档结束*
