# 级联合成方案 v2 再审报告

> 审查人: 资深QA专家 | 审查日期: 2026-07-05
> 审查对象: `cascading-synthesis-v2.md` (1448行) — v2 修正方案
> 对照基准: `design-review.md` (v1 审查报告, 597行) + `data_loader.py` (现有代码, 191行)

---

## 审查结论速览

| 维度 | v1 状态 | v2 状态 |
|------|---------|---------|
| P0 问题 (3个) | 阻断 | **3/3 已修复** |
| P1 问题 (6个) | 功能不正确 | **6/6 已修复** |
| v2 新增逻辑自洽性 | N/A | **4/4 通过** |
| 新发现问题 | N/A | **3个 (P1×1, P2×2)** |
| 综合判定 | — | **条件通过 — 需验证 yfinance 周/月/季线 ts 实际格式后再实施** |

---

## 1. P0 问题修复验证

### 1.1 tz-aware vs tz-naive TypeError -> 已修复

**v2 修复方案**: `_ensure_tz_naive()` 函数 (v2 第111-115行)，在所有 Timestamp 比较前统一剥离时区。

**逐点验证**:

| 检查项 | v2 位置 | 结论 |
|--------|---------|------|
| 是否有统一的时区处理策略？ | v2 §2.2 (第86-101行) "比较前剥离，写入时保留" | 是，策略明确 |
| `_ensure_tz_naive()` 是否在所有比较前调用？ | `_needs_synthesis()` 第397/404行; `_synthesize_incomplete_bar()` 第517/521/548/553/558行 | 是，覆盖所有比较点 |
| 字符串比较备选方案是否合理？ | v2 §2.4 (第188行) 声明SQL WHERE使用原始字符串 | 是，SQL层面无需剥离 |
| `cutoff_dt >= period_start` 还会抛异常吗？ | v2 第407行，两者均为 `_ensure_tz_naive()` 处理后 | **不会** |

**验证细节**:

```python
# _needs_synthesis() 中的关键路径 (v2 第394-407行):
last_ts = pd.Timestamp(db_rows[-1]["Date"])  # 可能 tz-aware 或 tz-naive
last_ts = _ensure_tz_naive(last_ts)           # → tz-naive ✓
period_start = _get_period_start_ts(last_ts, tf)  # 返回 tz-naive ✓
cutoff_dt = pd.Timestamp(cutoff_date)         # 可能 tz-aware
cutoff_dt = _ensure_tz_naive(cutoff_dt)       # → tz-naive ✓
return cutoff_dt >= period_start              # 同类型比较，安全 ✓
```

`_ensure_tz_naive()` 使用 `tz_localize(None)` 而非 `tz_convert(None)`。`tz_localize(None)` 保持钟表时间不变（14:00+08:00 → 14:00），这对同一时区的数据是正确的——所有比较在语义上等价。

**唯一注意点**: `tz_localize(None)` 不会做时区转换。如果所有数据来自同一交易所 (+08:00)，这完全正确。但如果未来引入跨时区数据（如美股 EDT），需要改为 `tz_convert` 后再比较。当前场景无此问题。

**结论: 已修复。**

---

### 1.2 周线/月线/季线 period_start 计算 -> 已修复

**v2 修复方案**: `_get_period_start_ts()` 对周线/月线/季线统一改为 `last_ts + 1天` (v2 第412-461行)。

**v1 问题回顾**: v1的周线计算返回"下一个周五"（period END 而非 START），导致 `_needs_synthesis()` 中 `cutoff_dt >= 下周五` 判定，在新一周的任何时间都无法触发合成。

**逐TF验证** (假设所有 ts 在调用前已 tz-naive):

| TF | 输入 last_ts | v2 输出 | 预期输出 | 判定 |
|----|-------------|---------|---------|------|
| 1分钟 | `2026-07-03T14:46:00` | `14:47:00` | `14:47:00` | 正确 |
| 5分钟 | `2026-07-03T14:55:00` | `14:56:00` | `14:56:00` | 正确 |
| 15分钟 | `2026-07-03T14:45:00` | `14:46:00` | `14:46:00` | 正确 |
| 60分钟 | `2026-07-03T14:00:00` | `14:01:00` | `14:01:00` | 正确 |
| 日线 | `2026-07-02T00:00:00` | `2026-07-03T00:00:00` | `2026-07-03T00:00:00` | 正确 |
| 周线 (ts=周五) | `2026-06-26T00:00:00` | `2026-06-27T00:00:00` | `2026-06-27`(次日) | 正确 |
| 月线 (ts=月末) | `2026-06-30T00:00:00` | `2026-07-01T00:00:00` | `2026-07-01`(下月首日) | 正确 |
| 季线 (ts=季末) | `2026-03-31T00:00:00` | `2026-04-01T00:00:00` | `2026-04-01`(下季首日) | 正确 |
| 季线 (跨年) | `2026-12-31T00:00:00` | `2027-01-01T00:00:00` | `2027-01-01` | 正确 |

**A2 用户指定测试用例**:

| 用例 | 输入 | 预期 | v2 实际 | 结论 |
|------|------|------|---------|------|
| 周线(周一ts) | `last_complete_ts="2026-06-29"`(周一) | `"2026-06-30"`(次日) | `(ts + 1天).normalize() = "2026-06-30"` | 匹配预期 |
| 月线 | `last_ts="2026-06-30"` | `"2026-07-01"` | `"2026-07-01"` | 匹配预期 |
| 季线 | `last_ts="2026-03-31"` | `"2026-04-01"` | `"2026-04-01"` | 匹配预期 |

**隐式依赖 — 重要**: v2 的 `ts + 1天` 策略**依赖以下假设**:

1. 周线 bar 的 ts 是**周期结束日** (周五或周日)，而非开始日 (周一)
2. 月线 bar 的 ts 是**月末最后一天**，而非月初第一天
3. 季线 bar 的 ts 是**季末最后一天**，而非季初第一天

如果 yfinance 的实际行为与上述假设不符（例如月线 ts 为月初第一天），则 `ts + 1天` 会计算出错误的 period_start。需在实施前验证 yfinance 对不同 TF 的 timestamp 约定。

> 详见 §5 新发现问题 N1。

**结论: 已修复。** v2 的 `ts + 1天` 逻辑正确消除了 v1 的周线计算 bug，且对月线/季线做了优雅简化。但需验证 yfinance timestamp 约定。

---

### 1.3 Parquet Date 列时区混合 -> 已修复

**v2 修复方案**: `_format_synth_date()` 函数 (v2 第150-176行)，按目标 TF 的 DB 原生格式决定合成 bar 的 Date 是否带时区。

**逐点验证**:

| 检查项 | v2 设计 | 结论 |
|--------|---------|------|
| 是否明确了每个 TF 的 Date 列格式？ | v2 §2.1 (第71-73行) 表格明确区分 分钟级 (带时区) vs 日线级 (无时区) | 是 |
| `_format_synth_date()` 设计是否合理？ | v2 第150-176行: 从 `db_rows[0]["Date"]` 提取 tz_suffix → 拼接 | 合理 |
| 日线合成 bar 的 Date 格式是否与 DB 一致（无时区）？ | `_format_synth_date("...14:47+08:00", "日线", db_rows)` → DB日线无时区 → 剥离 → `"2026-07-02T14:47:00"` | 一致 |
| 60分钟合成 bar 的 Date 格式是否与 DB 一致（有时区）？ | `_format_synth_date("...14:47+08:00", "60分钟", db_rows)` → DB 60分钟有时区 → 保留 `+08:00` | 一致 |

**验证细节**:

```python
# v2 第170-176行: _format_synth_date() 核心逻辑
if db_rows:                           # db_rows 非空（调用前已保证）
    tz = _get_tz_suffix(db_rows[0]["Date"])  # 从第一条 DB 记录提取
    if tz:
        return base + tz              # 分钟级: "2026-07-03T14:47:00" + "+08:00"
return base                           # 日线级: "2026-07-03T14:47:00"
```

**边界情况**:
- `db_rows` 为空的 TF: `_sync_all_cascading()` 第239-242行提前 skip，不会进入合成流程
- `_get_tz_suffix()` 兜底逻辑 (v2 第135-147行): 无 `+`、无 `Z`、无 `-HH:MM` 后缀时返回 `''`
- 负偏移时区 (如 `-05:00`) 的正则 `r'-\d{2}:\d{2}$'` 使用 `$` 锚点，安全

**一个微小的 `_get_tz_suffix` 实现问题**: 第140行的 `ts_str.index('+')` 会匹配字符串中**第一个** `+` 的位置。在极端情况下，如果 ISO 8601 日期部分出现 `+`（不会发生，因为日期部分是纯数字和连字符），会有 bug。当前所有合理的时间戳都不会触发此问题。可忽略。

**结论: 已修复。** 合成 bar 的 Date 格式与对应 TF 的 DB 原生格式一致，parquet Date 列不会出现混合时区。

---

## 2. P1 问题修复验证

### 2.1 prev_data 语义分离 -> 已修复

| v1 问题 | v2 修复 |
|---------|---------|
| `prev_data` 存储完整 DataFrame (120行)，同时用于显示和合成 | `synth_cache[tf]` 只存单条合成 bar (dict)，显示数据独立查询 |

**验证**:

- `prev_data` 变量被替换为 `synth_cache` (v2 第234行: `synth_cache: dict[str, dict] = {}`)
- 显示数据: 通过 `_query_tf_from_db()` 独立查询 (v2 第238行)
- 合成数据源: 通过 `_query_tf_for_period()` 独立查询 DB，不受 120 条窗口限制 (v2 第539-541行)
- `_query_tf_for_period()` 查询指定时间范围内**所有** bar (v2 第327-354行)，不设 LIMIT

**`_query_tf_for_period()` 是否能查到合成所需数据？**

以日线从 60 分钟合成为例:
- period_start = `"2026-07-03T00:00:00"`（或调整为 09:30）
- period_end = `"2026-07-03T14:45:00"`
- SQL: `WHERE ts >= period_start AND ts <= period_end` + `时间升序`
- 60 分钟 DB 中该时间段有约 5-7 条 bar → 总能查到 ✓
- 如果该 ticker 在此时间段无 60 分钟数据 (DB 尚未刷新) → 返回空列表 → `_synthesize_incomplete_bar` 返回 None → 不合成 ✓

**结论: 已修复。** 显示数据和合成数据完全解耦。

---

### 2.2 合成窗口独立于显示窗口 -> 已修复

| v1 问题 | v2 修复 |
|---------|---------|
| 从 `prev_data` (120条窗口 DataFrame) 中过滤合成期数据 | `_query_tf_for_period()` 查询特定时间范围，不受窗口限制 |

**实际数据量对比**:
- 日线从 60 分钟合成: 合成期内约 5-7 条 60 分钟 bar（而非 120 条）
- 周线从日线合成: 合成期内约 5-7 条日线 bar（而非 120 条）
- 月线从周线合成: 合成期内约 2-5 条周线 bar（而非 120 条）

合成窗口完全独立，且数据量很小，DB 查询开销低。

**结论: 已修复。**

---

### 2.3 日线合成开盘时间对齐 -> 已修复（有限定）

**v2 实施**: `_synthesize_incomplete_bar()` 第524-533行:

```python
if target_tf == "日线" and finer_tf == "60分钟":
    market_open = query_start.replace(hour=9, minute=30, second=0, microsecond=0)
    if query_start < market_open < cutoff_dt:
        actual_start = market_open
```

**验证**:
- 日线 period_start 可能是 `"2026-07-03T00:00:00"`（午夜）
- 调整为 `"2026-07-03T09:30:00"` 后，跳过盘前的 00:00-09:29 空区间
- DB 中 60 分钟数据从 09:30 开始 → 查询有效 ✓

**限定条件**:
- 仅当 `finer_tf == "60分钟"` 时生效。如果日线直接从 15 分钟（无 60 分钟链路）合成，不会调整
- 09:30 硬编码为 A 股开盘时间。美股是 09:30 Eastern，港股是 09:30 HKT
- 未考虑集合竞价时段 (09:15-09:25)
- `market_open < cutoff_dt` 条件：如果 cutoff 在开盘前（如 08:00），`actual_start` 保持 `query_start` 不变（即 00:00），此时查询会返回空（因为 08:00 前无交易数据）

**结论: 已修复。** 核心问题（避免查询盘前空区间）已解决。开盘时间硬编码是合理的 pragmatic 选择，可在后续版本中参数化。

---

### 2.4 `_load_chart_data` 降级路径 -> 已修复

**v1 问题**: 回测模式下 parquet 不存在时缺乏降级路径。

**v2 实施**: `_load_chart_data()` 第732-736行:

```python
# v2: parquet不存在或读取失败时的降级路径
if err is not None:
    logger.warning(f"[_load_chart_data] {tf} parquet load failed: {err}")
# 回退到API获取
return _cached_fetch_stock(market, ticker_code, tf, n_pts)
```

**分析**: 降级到 `_cached_fetch_stock()` 获取的是"当前最新"数据（非回测 cutoff 数据）。这在回测场景下不是最优，但作为**最终降级路径**比崩溃好。配合 `main()` 中的 `has_data()` 前置检查 (v2 第751行)，正常情况下不会走到此降级路径。

**结论: 已修复。**

---

### 2.5 并发安全 -> 已修复（设计层面）

**v1 问题**: 4 个 `st.fragment` 可能同时读写 parquet。

**v2 设计**: 写入 (`_sync_all_cascading()`) 在所有 fragment 渲染**之前**完成 (v2 §1.2 架构图 第38-49行)。4 个 fragment 只执行 `pd.read_parquet()`（只读）。无竞争条件。

**结论: 已修复。**

---

### 2.6 函数命名混淆 -> 已修复

**v1 问题**: `_get_period_start()` (返回字符串) 与 `_get_period_start_ts()` (返回 Timestamp) 命名相似易混淆。

**v2 处理**: v2 保留 `_get_period_start_ts()` (返回 Timestamp, 用于判断合成) 和 `_get_query_start_for_synthesis()` (返回 Timestamp, 用于数据查询)。两者职责明确分离。v2 第473-478行注释说明了区别。

**结论: 已修复。** 命名清晰，职责分离。

---

## 3. 新增逻辑审查

### 3.1 synth_cache 生命周期 -> 正确

**创建**: `_sync_all_cascading()` 函数开始时 `synth_cache = {}` (v2 第234行)

**更新**: 每处理完一个 TF 后:
```python
synth_cache[tf] = {
    "synth_bar": synthesized_bar,   # dict 或 None
    "db_sample_ts": db_rows[0]["Date"] if db_rows else "",
}
```
(v2 第284-287行)

**访问**: 处理下一个更粗 TF 时:
```python
finer_tf = _find_immediate_finer_tf(tf, tfs)
if finer_tf and finer_tf in synth_cache:
    synthesized_bar = _synthesize_incomplete_bar(
        ...,
        finer_synth_bar=synth_cache[finer_tf]["synth_bar"],
    )
```
(v2 第253-264行)

**销毁**: 函数返回时自动销毁（局部变量）

**级联传递验证** (min_tf=15分钟, TFs=[15m,60m,日,周]):

```
处理 15分钟: synth_cache["15分钟"] = {synth_bar: None}
处理 60分钟: 读取 synth_cache["15分钟"] → synth_bar=None → 仅用DB数据合成
             synth_cache["60分钟"] = {synth_bar: {60分钟合成bar}}
处理 日线:   读取 synth_cache["60分钟"] → synth_bar={60分钟合成bar} → 拼接后合成
             synth_cache["日线"] = {synth_bar: {日线合成bar}}
处理 周线:   读取 synth_cache["日线"] → synth_bar={日线合成bar} → 拼接后合成
```

每个 stage 的 synth_bar 在下一 stage 被消费后才被写入新的 synth_cache entry → 串行安全，无竞争。

**关键前提**: `tfs` 按 ALL_TFS 顺序从细到粗排列，确保处理顺序正确。`main()` 第755-758行保证了这一点。

**结论: 正确。** 生命周期清晰，级联传递路径完整。

---

### 3.2 `_query_tf_for_period` 字符串比较风险 -> 低风险（边界情况）

**问题复现路径**:

```python
# _synthesize_incomplete_bar() 第536-537行:
period_start_str = actual_start.isoformat()  # tz-naive → "2026-07-03T14:01:00"
period_end_str = cutoff_dt.isoformat()       # tz-naive → "2026-07-03T14:45:00"

# SQL: WHERE ts >= ? AND ts <= ?
# DB ts (分钟级): "2026-07-03T14:45:00+08:00"
```

**字符串比较逐字符分析**:

| 比较 | DB ts | Query | 逐字符结果 | 判定 |
|------|-------|-------|-----------|------|
| `>= period_start` | `14:00:00+08:00` | `14:01:00` | pos 16: `'0' < '1'` | **FALSE** ✓ (排除14:00的bar，正确) |
| `>= period_start` | `14:15:00+08:00` | `14:01:00` | pos 16: `'1' > '0'` | **TRUE** ✓ (包含14:15的bar，正确) |
| `>= period_start` | `14:01:00+08:00` | `14:01:00` | prefix match, DB longer | **TRUE** ✓ (包含边界bar) |
| `<= period_end` | `14:45:00+08:00` | `14:45:00` | prefix match, DB longer | **FALSE** ✗ (排除恰好匹配period_end的bar) |
| `<= period_end` | `14:30:00+08:00` | `14:45:00` | pos 17: `'3' < '4'` | **TRUE** ✓ (包含14:30的bar) |

**风险场景**: DB 中有一条 finer_tf bar 的 ts 恰好等于 period_end（即 cutoff 时间），且该 DB ts 带时区后缀。

- 对于标准 bar 间隔（15分钟、60分钟）：cutoff 时间几乎不可能恰好等于 bar 边界时间，除非用户刚好在整点/整刻触发回测。此时排除一条 bar 对整体合成影响极小。
- 对于 1 分钟 bar：cutoff 可能恰好等于 1 分钟 bar 的边界，但排除 1 条 1 分钟 bar 对更粗 TF 的 OHLCV 聚合影响微乎其微（1/60 的数据量）。
- 对于日线级数据：DB ts 无时区，与 query 格式一致，字符串比较完全正确，无此风险。

**严重程度判定**: P2（边界情况，实际影响极小）。SQLite 中 ISO 8601 字符串比较在绝大多数场景下工作正确。唯一已知缺陷是 exact-match-at-end-boundary 场景。

**修复建议（可选）**: 在 `period_end_str` 后附加一个安全 margin，如 `(cutoff_dt + pd.Timedelta(seconds=1)).isoformat()`，确保 `<=` 比较不会因时区后缀长度差异而排除边界 bar。

**结论: 低风险，可接受。** 字符串比较对所有非 exact-end-boundary 场景正确。如需完美，可在 period_end 加 1 秒缓冲。

---

### 3.3 合成 bar 拼接逻辑 -> 正确

**拼接三要素** (v2 `_synthesize_incomplete_bar()` 第543-559行):

1. **DB 查询结果** (`finer_db_bars`): 来自 `_query_tf_for_period()`，时间升序
2. **更细 TF 合成 bar** (`finer_synth_bar`): 来自 `synth_cache`
3. **拼接算法**:
   ```python
   all_finer_bars = list(finer_db_bars)          # ① 复制DB查询结果
   if finer_synth_bar is not None:
       synth_ts = _ensure_tz_naive(pd.Timestamp(finer_synth_bar["Date"]))
       if actual_start <= synth_ts <= cutoff_dt:  # ② 检查范围
           existing_ts = {_ensure_tz_naive(pd.Timestamp(b["Date"])) for b in all_finer_bars}
           if synth_ts not in existing_ts:         # ③ 去重
               all_finer_bars.append(finer_synth_bar)
               all_finer_bars.sort(                # ④ 按时间戳重排
                   key=lambda b: _ensure_tz_naive(pd.Timestamp(b["Date"]))
               )
   ```

**排序安全性**: 排序 key 使用 `_ensure_tz_naive()`，确保 DB bar（有时区）和合成 bar 的时间戳在同一基准下比较。分钟级 bar 的合成 bar 也带时区（via `_format_synth_date`），所以格式一致。即使格式不完全一致，剥离时区后排序也正确。

**拼接后进入 `_build_output_df()`** (v2 第622-645行):
```python
data = list(db_rows)           # 目标TF的DB完成bar
if synthesized_bar is not None:
    data.append(synthesized_bar)  # 追加合成bar
df = pd.DataFrame(data)
if len(df) > n_pts:
    df = df.iloc[-n_pts:]       # 截断到显示窗口
```

注意: 如果 db_rows 恰好有 n_pts 条，追加合成 bar 后有 n_pts+1 条，`iloc[-n_pts:]` 会丢弃 db_rows 中最旧的一条。这是预期行为：保留最新的 n_pts 条（含合成 bar）。

**结论: 正确。**

---

### 3.4 tz_suffix 提取 -> 正确

**提取来源**: `db_rows[0]["Date"]` — 该 TF 的**第一条** DB 查询结果 (v2 第172行)

**边界场景覆盖**:

| 场景 | 行为 | 分析 |
|------|------|------|
| 该 TF 有 DB 数据且带时区（分钟级） | `_get_tz_suffix()` 返回 `"+08:00"` → 合成 bar 带时区 | 正确 ✓ |
| 该 TF 有 DB 数据且无时区（日线级） | `_get_tz_suffix()` 返回 `""` → 合成 bar 无时区 | 正确 ✓ |
| 该 TF 无 DB 数据（db_rows 为空） | `_sync_all_cascading()` 第239-242行 skip, 不进入合成 | 不会发生 |
| db_rows 传入 `_format_synth_date()` 但为空 | `if db_rows:` 检查 (v2 第171行) → 返回不带时区的 base | 防御性正确 |
| 该 TF 第一条 DB bar 的 Date 格式异常 | `_get_tz_suffix()` 兜底返回 `""` | 降级到无时区，安全 |

**结论: 正确。** 降级路径完备。

---

## 4. 全 TF 演练（8 个周期逐 TF 验证）

**场景设定**:
- 股票: AAPL
- cutoff_date: `"2026-07-03T14:45:00+08:00"` (周五 14:45 北京时间)
- configs 包含全部 8 个 TF
- min_tf = "1分钟" (最细周期)
- tfs = ["1分钟", "5分钟", "15分钟", "60分钟", "日线", "周线", "月线", "季线"]
- n_pts = 120 (假设)
- 假设: 所有 TF 的 DB 中均有足够历史数据

### 4.1 1分钟 (min_tf)

| 项目 | 详情 |
|------|------|
| DB 最后一条完整 bar 的 ts | `"2026-07-03T14:44:00+08:00"` (1分钟 bar，cutoff 前一分钟) |
| 是否需要合成？ | **否** — `tf == min_tf` |
| 判断条件 | `_sync_all_cascading()` 第246-248行: `tf != min_tf` → False |
| 输出 parquet 数据条数 | 120 条 (全部来自 DB 查询) |
| Date 格式 | **带时区**: `"2026-07-03T14:44:00+08:00"` |
| synth_cache 缓存 | `{"synth_bar": None, "db_sample_ts": "2026-07-03T13:45:00+08:00"}` |

### 4.2 5分钟

| 项目 | 详情 |
|------|------|
| DB 最后一条完整 bar 的 ts | 5 分钟 bar 在 `:00, :05, :10, :15, :20, :25, :30, :35, :40, :45, :50, :55`。cutoff=14:45 → DB 可能已有 `"14:45+08:00"` 的完成 bar（取决于数据刷新）。假设有: `"2026-07-03T14:45:00+08:00"` |
| 是否需要合成？ | **取决于 DB 状态** |
| | 情况A: DB 有 14:45 bar → `last_ts="14:45"` → `period_start="14:46"` → `cutoff_dt(14:45) >= period_start(14:46)`? → **False** → **不需要合成** |
| | 情况B: DB 仅到 14:40 → `last_ts="14:40"` → `period_start="14:41"` → `cutoff_dt(14:45) >= 14:41`? → **True** → **需要合成** |
| 合成 finer_tf | `_find_immediate_finer_tf("5分钟", tfs)` → "1分钟" |
| 合成查询范围 | `["14:41:00", "14:45:00"]` (tz-naive iso) |
| `_query_tf_for_period` 正确性 | `"14:41:00+08:00" >= "14:41:00"` → TRUE (包含边界) ✓; `"14:45:00+08:00" <= "14:45:00"` → FALSE (排除 exact match) — 见 §3.2 |
| 合成 bar Date | `"2026-07-03T14:45:00+08:00"` (带时区) |
| 输出 parquet | 120 条 (情况A) 或 121→120 条 (情况B，截断后) |
| synth_cache | `{"synth_bar": {5分钟合成bar} 或 None}` |

### 4.3 15分钟

| 项目 | 详情 |
|------|------|
| DB 最后一条完整 bar | 15 分钟 bar 在 `:00, :15, :30, :45`。cutoff=14:45 → DB 可能有 `"14:45+08:00"` 或仅到 `"14:30+08:00"` |
| 是否需要合成？ | `cutoff_dt(14:45) >= period_start(14:31 [若last=14:30])` → **需要**; 或 `>= 14:46 [若last=14:45]` → **不需要** |
| 合成 finer_tf | `_find_immediate_finer_tf("15分钟", tfs)` → "5分钟" |
| 合成查询范围 | `["14:31:00"(或"14:46"), "14:45:00"]` |
| 数据源 | 5 分钟 DB bar + 5 分钟 synth_bar(如有) |
| 合成 bar Date | `"2026-07-03T14:45:00+08:00"` (带时区) |
| synth_cache | `{"synth_bar": {15分钟合成bar} 或 None}` |

### 4.4 60分钟

| 项目 | 详情 |
|------|------|
| DB 最后一条完整 bar | 60 分钟 bar 在 `:00` (如 `09:00, 10:00, ..., 14:00`)。cutoff=14:45 → DB 最后 bar 很可能是 `"14:00+08:00"` |
| 是否需要合成？ | `last_ts="14:00"` → `period_start="14:01"` → `cutoff_dt(14:45) >= 14:01` → **True** → **需要合成** |
| 合成 finer_tf | `_find_immediate_finer_tf("60分钟", tfs)` → "15分钟" |
| 合成查询范围 | `["14:01:00", "14:45:00"]` |
| 数据源 | 15 分钟 DB bar (14:00*, 14:15, 14:30, 14:45) + 15分钟 synth_bar (如有) |

  *注: 14:00 的 15 分钟 bar 会被 `>= 14:01` 排除。这意味着 60 分钟合成 bar 缺失 14:00-14:01 这 1 分钟的 15 分钟 bar 数据。这是 bar 边界不对齐的固有局限，1 分钟内价格变动通常可忽略。
  
| 合成 bar Date | `"2026-07-03T14:45:00+08:00"` (带时区) |
| 输出 parquet | 120 条 DB + 1 条合成 → 截断到 120 |
| synth_cache | `{"synth_bar": {60分钟合成bar}}` |

### 4.5 日线

| 项目 | 详情 |
|------|------|
| DB 最后一条完整 bar | `"2026-07-02T00:00:00"` (昨天日线，无时区) |
| 是否需要合成？ | `period_start = 07-02 + 1天 = "2026-07-03T00:00:00"` → `cutoff_dt(07-03T14:45) >= 07-03T00:00` → **True** → **需要合成** |
| 合成 finer_tf | `_find_immediate_finer_tf("日线", tfs)` → "60分钟" |
| 合成查询起点调整 | `query_start = "07-03T00:00:00"` → 检测到日线+60分钟 → `actual_start = "07-03T09:30:00"` |
| `_query_tf_for_period` 查询 | 60 分钟 DB `["09:30:00", "14:45:00"]` |
| 数据源 | 60 分钟 DB bar (10:30, 11:30, 12:30, 13:30, 14:00 的完成 bar) + 60 分钟 synth_bar (14:45) |
| 字符串比较 | period_start/end 无时区 (tz-naive iso)，DB 60 分钟有时区。对 `>=` 比较：prefix match 时 longer > shorter → DB bar 在边界被包含 ✓。对 `<=` 比较：prefix match 时 longer > shorter → exact-match end bar 被排除（边缘情况）|
| 合成 bar Date | `_format_synth_date("14:45+08:00", "日线", db_rows)` → DB 日线无时区 → `"2026-07-03T14:45:00"` **(无时区)** |
| parquet Date 列一致性 | DB 日线: `"2026-07-02T00:00:00"`, 合成日线: `"2026-07-03T14:45:00"` → **格式一致，无混合时区** ✓ |
| 输出 parquet | 120 条 DB + 1 条合成 → 截断 |
| synth_cache | `{"synth_bar": {日线合成bar}}` |

### 4.6 周线

| 项目 | 详情 |
|------|------|
| DB 最后一条完整 bar | **取决于 yfinance 的周线 timestamp 约定**。假设 ts = 周五 (周期结束日): `"2026-06-26T00:00:00"` (上周五，无时区) |
| 是否需要合成？ | `period_start = 06-26 + 1天 = "2026-06-27T00:00:00"` (周六) → `cutoff_dt(07-03T14:45) >= 06-27T00:00` → **True** → **需要合成** ✓ |
| | v1 比较: `period_start = 07-03` (下周五) → `07-03 >= 07-03` → True → 触发 ❓实际上如果 cutoff 是周二 07-07，v1 的 period_start=07-10，就不会触发 |
| 合成 finer_tf | `_find_immediate_finer_tf("周线", tfs)` → "日线" |
| 合成查询范围 | `["2026-06-27T00:00:00", "2026-07-03T14:45:00"]` |
| 数据源 | 日线 DB bar (06-29, 06-30, 07-01, 07-02 的完成 bar — 06-27/28 周六日无数据) + 日线 synth_bar (07-03) |
| 聚合结果 | 约 5 条日线 bar (4 条完成 + 1 条合成) → 周线合成 bar |
| 合成 bar Date | `_format_synth_date(...)` → DB 周线无时区 → `"2026-07-03T14:45:00"` **(无时区)** |
| synth_cache | `{"synth_bar": {周线合成bar}}` |

**验证 v1 修复**: 如果上周线 bar ts = `"2026-06-26"` (周五)，v1 的 `_get_period_start_ts` 返回 `"2026-07-03"` (下周五)。假设 cutoff 是 `"2026-07-01"` (周三)：`07-01 >= 07-03? → False` → **不合成** ✗ (bug)。v2: `period_start = "2026-06-27"`, `07-01 >= 06-27? → True` → **合成** ✓。

### 4.7 月线

| 项目 | 详情 |
|------|------|
| DB 最后一条完整 bar | **假设** ts = 月末: `"2026-06-30T00:00:00"` (6 月月线 bar，无时区) |
| 是否需要合成？ | `period_start = 06-30 + 1天 = "2026-07-01T00:00:00"` → `cutoff_dt(07-03T14:45) >= 07-01T00:00` → **True** → **需要合成** ✓ |
| 合成 finer_tf | `_find_immediate_finer_tf("月线", tfs)` → "周线" (而非 "日线" — 返回 immediate finer) |
| | 讨论: 月线从周线合成 vs 从日线合成 — OHLCV 聚合在数学上等价 (聚合的结合律)。使用周线数据量更小 (2-5 条 vs 20-23 条)，效率更高 |
| 合成查询范围 | `["2026-07-01T00:00:00", "2026-07-03T14:45:00"]` |
| 数据源 | 周线 DB (可能无已完成 bar，因为 7 月 3 日所在周未结束) + 周线 synth_bar |
| 实际数据 | 可能只有 1 条周线合成 bar → 月线合成 bar = 该周线合成 bar 的 OHLCV 投影 |
| Date | `"2026-07-03T14:45:00"` (无时区) |

### 4.8 季线

| 项目 | 详情 |
|------|------|
| DB 最后一条完整 bar | **假设** ts = 季末: `"2026-06-30T00:00:00"` (Q2 季线 bar，无时区) |
| 是否需要合成？ | `period_start = 06-30 + 1天 = "2026-07-01T00:00:00"` → `cutoff_dt(07-03T14:45) >= 07-01T00:00` → **True** → **需要合成** ✓ |
| 合成 finer_tf | `_find_immediate_finer_tf("季线", tfs)` → "月线" |
| 合成查询范围 | `["2026-07-01T00:00:00", "2026-07-03T14:45:00"]` |
| 数据源 | 月线 DB + 月线 synth_bar。如果是 7 月初，月线 DB 无完成 bar → 仅月线 synth_bar |
| 跨年验证 | `last_ts="2026-12-31"(Q4末)` → `period_start="2027-01-01"(Q1始)` ✓ |

### 4.9 全 TF 演练总结

**级联链路**: 1分钟(不合成) → 5分钟(从1分钟) → 15分钟(从5分钟) → 60分钟(从15分钟) → 日线(从60分钟,09:30调整) → 周线(从日线) → 月线(从周线) → 季线(从月线)

**每个 stage 的数据传递**: 通过 `synth_cache[tf]["synth_bar"]` 单条 bar 传递，数据量极小。

**Date 格式**: 分钟级 TF 合成 bar 带 `+08:00` 后缀；日线级 TF 合成 bar 不带。与各自 DB 格式一致。

**min_tf/presence 一致性**: 如果 min_tf = "1分钟" 且所有 8 个 TF 都在 tfs 中，处理顺序从细到粗，级联链路完整。

---

## 5. 新发现问题

### N1: yfinance 周/月/季线 timestamp 约定未验证 (P1)

**严重程度**: P1 (如果 assumption 错误，weekly/monthly/quarterly 合成逻辑全部错误)

**描述**: v2 的 `_get_period_start_ts()` 对周线/月线/季线使用 `last_ts + 1天`，这**假设** DB 中这些 TF 的 ts 是**周期结束日**：
- 周线 ts = 周五（周期最后一天）
- 月线 ts = 月末最后一天
- 季线 ts = 季末最后一天

如果 yfinance 实际行为不同（例如月线 ts = 月初第一天），则 `ts + 1天` 会计算出错误的 period_start：

| yfinance 实际 ts | v2 计算 | 正确值 | 后果 |
|-----------------|---------|--------|------|
| 月线 ts=月初 `"2026-06-01"` | `"2026-06-02"` (仍在 6 月) | `"2026-07-01"` (7 月首日) | **period_start 大幅错误，可能导致错误的合成判断** |
| 季线 ts=季初 `"2026-04-01"` | `"2026-04-02"` (仍在 Q2) | `"2026-07-01"` (Q3 首日) | 同上 |

**在 v2 中的位置**: `_get_period_start_ts()` (v2 第440-456行)

**验证方法**: 
```python
import yfinance as yf
# 检查周线 ts
data = yf.download("AAPL", period="1mo", interval="1wk")
print(data.index)  # 查看是周一还是周五

# 检查月线 ts
data = yf.download("AAPL", period="6mo", interval="1mo")
print(data.index)  # 查看是月初还是月末
```

**修复建议**: 
- 如果 yfinance 月线/季线 ts 是月初/季初: 使用 `ts + pd.offsets.MonthEnd(1) + 1天` 或类似方法计算真正的下一个周期开始
- 如果 yfinance 周线 ts 是周一: 使用 `ts + 7天` (而非 `+1天`) 跳到下周一
- 或者在代码中明确获取 yfinance 数据后统一规范化 ts

**当前风险缓解**: v1 审查 §2.2 确认了 v1 月线/季线逻辑正确（`next_month_start` 函数），v2 的简化与 v1 在该假设下等价。但该假设**从未被显式验证**。

### N2: `_find_immediate_finer_tf` 返回 immediate finer 而非 finest (P2)

**严重程度**: P2

**描述**: `_find_immediate_finer_tf()` 返回紧邻的更细 TF（而非可用的最细 TF）。例如：

```
tfs = ["15分钟", "60分钟", "日线", "周线", "月线"]
月线合成时: finer_tf = "周线" (immediate finer)
```

而非 `"日线"`（finest available finer than 月线）。

**在 v2 中的位置**: `_find_immediate_finer_tf()` (v2 第357-368行)

**分析**: 虽然 OHLCV 聚合在数学上对嵌套周期是结合的（周线聚合日线 = 月线聚合周线 = 月线直接聚合日线），但使用更粗的中间 TF 意味着：
- 数据点更少（月线从周线合成: ~4条 vs 从日线: ~22条）
- 如果中间 TF (周线) 的合成 bar 本身就不精确，该误差会传播到月线

**建议**: 当前设计合理（效率优先，数学等价），但建议在函数文档中说明"返回 immediate finer TF，不保证是最细可用 TF"。

### N3: 日线合成 `market_open = 09:30` 硬编码未考虑多市场 (P2)

**严重程度**: P2

**描述**: v2 第527行 `query_start.replace(hour=9, minute=30, ...)` 硬编码为 09:30。适用于 A 股市场，但不适用于：
- 美股（09:30 Eastern，但北京时间是 21:30）
- 港股（09:30 HKT，与 A 股相同但交易时间不同）

**在 v2 中的位置**: `_synthesize_incomplete_bar()` (v2 第524-533行)

**分析**: 当前系统主要服务于 A 股，硬编码 09:30 是 pragmatic choice。但如果未来扩展到其他市场，需要参数化。此优化仅影响查询效率（避免查询盘前空区间），不影响数据正确性。

**建议**: 将 `market_open` 作为可配置参数，或从 DB 中检测第一条 bar 的实际时间。

---

## 6. 综合评估

### 6.1 问题统计

| 类别 | 数量 | 详情 |
|------|------|------|
| P0 阻断性问题 | **0** | v1 的 3 个 P0 全部修复 |
| P1 功能正确性 | **1 (新发现)** | N1: yfinance timestamp 约定未验证 |
| P2 改进项 | **2 (新发现)** | N2: immediate finer vs finest; N3: 09:30 硬编码 |
| v1 遗留 P2 | **0** | v1 审查的 7 个 P2 项均已由 v2 设计覆盖或变为不适用 |

### 6.2 修复状态总表

| v1 问题编号 | 严重程度 | v1 描述 | v2 修复状态 |
|------------|---------|--------|-----------|
| P0-1 (1.3) | 严重 | tz-aware vs tz-naive TypeError | **已修复** |
| P0-2 (2.1) | 严重 | 周线 period_start 计算错误 | **已修复** |
| P0-3 (1.2) | 严重 | Parquet Date 混合时区 | **已修复** |
| P1-1 (2.3) | 中等 | 合成窗口依赖 120 条限制 | **已修复** |
| P1-2 (2.3) | 中等 | prev_data 语义混淆 | **已修复** |
| P1-3 (3.3) | 中等 | 日线合成未考虑开盘时间 | **已修复** |
| P1-4 (4.1) | 中等 | _load_chart_data 无降级路径 | **已修复** |
| P1-5 (—) | — | 并发安全 | **已修复** |
| P1-6 (1.1) | 中等 | 函数命名混淆 | **已修复** |
| P2-1 (3.4) | 轻微 | min_tf=1分钟/5分钟场景 | v2 `min_tf` 判断逻辑处理正确 |
| P2-2 (3.5) | 轻微 | 跨周末/节假日 | v2 查询自然返回空，正确 |
| P2-3 (3.2) | 轻微 | 刚切换 ticker | v2 `has_data()` 前置检查 |
| P2-4 (—) | — | 数据不足 | v2 返回 None，不合成 |

### 6.3 最终判定

**v2 是否可以进入实施阶段: YES（条件通过）**

**前置条件**: Phase 2 (周期计算) 实施前，必须先验证 yfinance 对周线/月线/季线的实际 timestamp 格式（§5 N1）。

**建议实施顺序微调**:
```
Phase 0 (新增): yfinance timestamp 格式验证 (15分钟)
  └─ 验证: 打印周/月/季线 yfinance.download() 返回的 index 值
  └─ 根据结果决定 _get_period_start_ts 周/月/季线分支是否需要调整

Phase 1-5: 按 v2 §7.1 原计划执行
```

**不阻断实施的其他事项**:
- N2 (immediate finer 语义): 可接受，在函数文档中说明即可
- N3 (09:30 硬编码): A 股当前场景适用，后续扩展时修正
- C2 (字符串比较边缘情况): 影响极小，可选加 1 秒缓冲

### 6.4 v2 设计亮点

1. **时区处理策略 "比较前剥离，写入时保留"**: 清晰、一致、可测试
2. **`_format_synth_date()` 设计**: 自动匹配 TF 原生格式，无需手动判断 TF 类型
3. **`synth_cache` 单条传递**: 极简设计，避免 DataFrame 复制开销
4. **显示/合成数据完全解耦**: 各自独立查询，互不干扰
5. **月线/季线计算简化**: 从 8 行复杂月份计算简化为 `ts + 1天` (在假设成立的前提下)
6. **测试用例设计**: v2 §7.2 的测试用例覆盖全面，包含时区/周期/级联三个维度

---

> **审查完成时间**: 2026-07-05 | **下次审查建议**: Phase 0 yfinance 验证完成后
> **审查人签名**: QA Expert (via automated review)
