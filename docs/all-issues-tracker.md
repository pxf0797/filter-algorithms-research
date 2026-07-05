# 回测级联合成 — 完整问题追踪清单

> 汇总日期: 2026-07-05
> 来源文档: design-review.md (v1审查), design-review-v2.md (v2再审), backtest-logic-change-analysis.md (实现分析), db-to-parquet-deep-dive.md (深度分析), cascading-synthesis-v2.md (v2方案)

---

## 0. 问题总览

| 优先级 | 总数 | 设计已解决 | 待修复 | 已修复 |
|--------|------|-----------|--------|--------|
| P0 | 3 | 3 | 0 | 0 |
| P1 | 7 | 2 | 0 | 5 |
| P2 | 10 | 4 | 6 | 0 |
| **合计** | **20** | **9** | **6** | **5** |

> 注: "设计已解决"指 v1 审查中发现的问题在 v2 设计方案中已彻底解决，无需额外代码修改。"待修复"指当前实现中仍存在的问题。"已修复"指已在代码中修复并验证。

---

## 1. P0 — 阻断性问题 (设计阶段发现，已在 v2 方案中修复)

| 编号 | 简述 | v1 中的表现 | v2 修复方案 | 状态 |
|------|------|------------|------------|------|
| P0-1 | tz-aware vs tz-naive TypeError | `_needs_synthesis()` 和 `_synthesize_incomplete_bar()` 中 `cutoff_dt` (tz-aware, 来自 min_tf DB) 与 `period_start` (tz-naive, 来自 `_get_period_start_ts()`) 直接比较，pandas 抛出 `TypeError: Cannot compare tz-naive and tz-aware timestamps`，阻断整个级联合成流程 | 新增 `_ensure_tz_naive()` 函数，所有 `pd.Timestamp` 比较前统一剥离时区标记（`tz_localize(None)`）。详见 cascading-synthesis-v2.md §2.2, design-review-v2.md §1.1 | ✅ 设计已解决 |
| P0-2 | 周线 period_start 计算错误 | `_get_period_start_ts()` 对周线返回"下一个周五"（period END 而非 START）。导致 `cutoff_dt >= 下周五` 判定：在新一周的任何时间都无法触发合成。例如 last_ts=`2026-06-26`(周五)，cutoff=`2026-07-01`(周三)，period_start 返回 `2026-07-03`(下周五) → 不合成（应为合成） | 周线/月线/季线统一改为 `last_ts + 1天`。`_get_period_start_ts()` 重写。详见 cascading-synthesis-v2.md §3.5, design-review-v2.md §1.2 | ✅ 设计已解决 |
| P0-3 | Parquet Date 列时区混合 | 正常日线 bar 的 Date 为 tz-naive (`"2026-07-02T00:00:00"`)，合成日线 bar 的 Date 为 tz-aware (`"2026-07-03T14:47:00+08:00"`)。`pd.to_datetime()` 处理混合时区列行为不确定（pandas >= 2.0 抛出 TypeError） | 新增 `_format_synth_date()` 函数，按目标 TF 的 DB 原生格式决定合成 bar 的 Date 是否带时区。分钟级保留时区，日线级剥离时区。详见 cascading-synthesis-v2.md §2.1/§2.3, design-review-v2.md §1.3 | ✅ 设计已解决 |

---

## 2. P0 — 实施 Bug (已修复)

当前实现中无已修复的 P0 实施 Bug。v1 的 3 个 P0 问题均在 v2 设计阶段解决，未进入代码实施。

---

## 3. P1 — 修复问题 (已全部修复)

### P1-1: n_pts 强制统一为 120，各视图配置被忽略

- **状态**: ✅ 已修复 | **提交**: e92a147
- **发现来源**: backtest-logic-change-analysis.md §差异#2
- **简述**: `_sync_all_cascading()` 对所有 TF 使用固定 `n_pts=120`（data_loader.py L374），不读取各视图的 `cfg["n_pts"]`。日线视图配置 `n_pts=60` 但实际显示 120 条 bar。
- **根因分析**: v2 设计将数据写入从各视图内部（`_load_chart_data`）提升到全局入口（`_sync_all_cascading`），但未传递 per-view 的 n_pts 参数。`_load_chart_data` 中 L141 注释 "不再需要截断！parquet 已经是 n_pts 条" 已不准确——parquet 有 120 条，但视图可能只需要 60 条。
- **影响范围**:
  1. 图表显示的数据点数量与用户配置不符
  2. 滤波参数（如 N_EWMA）基于 `n_pts` 调优，实际数据量不同可能影响滤波效果
  3. 注释误导后续维护者
- **建议修复方案**:
  - **方案 A（推荐）**: `_sync_all_cascading` 接受 per-TF n_pts 参数，从 configs 中读取。在 `main()` 中构造 `Dict[str, int]` 传入。
  - **方案 B**: 接受 `n_pts=120` 为统一值，但在 `_load_chart_data` 回测路径恢复截断逻辑。
  - **方案 C**: 取所有视图 n_pts 的最小值（当前 `main()` 中 `min_n_pts` 已计算但未向下传递）。
- **涉及文件/行号**:
  - `streamlit_app.py` L141（注释不准确）
  - `data_loader.py` L374（`_sync_all_cascading` 签名）
  - `streamlit_app.py` L122-125（回测分支 pass）
  - `streamlit_app.py` L507（cfg["n_pts"] 读取但未使用于级联合成）

### P1-2: `_sync_all_cascading` 返回值被丢弃，失败无感知

- **状态**: ✅ 已修复 | **提交**: e92a147
- **发现来源**: backtest-logic-change-analysis.md §差异#4
- **简述**: `main()` L1617 调用 `_sync_all_cascading(...)` 但丢弃其返回的 `dict[str, bool]`。如果所有 TF 都写入失败（如 DB 连接异常），所有 4 个视图静默回退到 `_cached_fetch_stock()`，显示最新数据而非回测历史数据，且无任何错误提示。
- **根因分析**: v1 设计中每个视图独立处理 `_sync_to_display` 的失败（`ok==False` 时独立回退），v2 集中处理后缺少全局失败检测。
- **影响范围**: 回测模式下级联合成全部失败时，用户看到的是最新数据而非历史截断数据，播放仍正常推进（bar_index 递增），用户完全无感知。
- **建议修复方案**:
  ```python
  # streamlit_app.py L1617 附近
  results = _sync_all_cascading(ticker_code, tfs_in_use, cutoff_date, min_tf_val)
  failed_tfs = [tf for tf, ok in results.items() if not ok]
  if failed_tfs:
      logger.warning(f"回测级联合成失败: {failed_tfs}")
  if len(failed_tfs) == len(tfs_in_use):
      st.warning("回测数据未就绪，部分视图可能显示非回测数据")
  ```
- **涉及文件/行号**: `streamlit_app.py` L1612-1617

### P1-3: 合成链断裂 — 中间 TF 无数据时粗粒度 TF 无法合成

- **状态**: ✅ 已修复 | **提交**: e92a147
- **发现来源**: backtest-logic-change-analysis.md §差异#6
- **简述**: 当某个中间 TF 的 DB 无数据时，`_sync_all_cascading` 执行 `continue`（跳过该 TF），`synth_cache` 中无该 TF 条目。后续更粗 TF 调用 `_find_immediate_finer_tf()` 找到该 TF 但 `finer_tf in synth_cache` 为 False → 不合成，所有更粗 TF 的合成 bar 缺失。
- **根因分析**: v2 设计引入了 TF 间数据依赖（级联合成），但 `_sync_all_cascading` 中 `if not db_rows: continue` 直接跳过，未尝试降级到下一个可用的更细 TF。
- **影响范围**: DB 中某些 ticker 缺少 60 分钟数据（常见于 A 股/港股部分 ticker）时，日线和周线的回测视图将无法显示截止到 cutoff_date 的合成 bar。
- **建议修复方案**:
  ```python
  # data_loader.py _sync_all_cascading 中
  if needs_synth:
      finer_tf = _find_immediate_finer_tf(tf, tfs)
      # 若 finer_tf 不在 cache 中，尝试往更细方向继续查找
      while finer_tf and finer_tf not in synth_cache:
          next_finer = _find_immediate_finer_tf(finer_tf, tfs)
          if next_finer == finer_tf or next_finer is None:
              finer_tf = None
              break
          finer_tf = next_finer
  ```
- **涉及文件/行号**: `data_loader.py` L239-264 (`_sync_all_cascading` 循环体)

### P1-4: 回退路径返回最新数据，破坏回测时间一致性

- **状态**: ✅ 已修复 | **提交**: e92a147
- **发现来源**: backtest-logic-change-analysis.md §差异#3(c)
- **简述**: 回测模式下 parquet 缺失时，`_load_chart_data()` 回退到 `_cached_fetch_stock()`，该函数调用 `query_kline(code, tf, n_pts, day_offset=0)` 返回最新 n_pts 条数据，不考虑 cutoff_date。图表显示最新数据而非回测历史数据。
- **根因分析**: 这是 v1 就存在的问题（`_cached_fetch_stock` 不具备 cutoff_date 过滤能力），但 v2 中 parquet 缺失的概率发生了变化——v1 是个别 TF 问题，v2 可能是级联合成链路中任何一环断裂。
- **影响范围**: 回测中 parquet 缺失时，图表跳到最新数据而非 cutoff_date 对应的历史数据，破坏回测体验。
- **建议修复方案**:
  ```python
  # streamlit_app.py L163 附近，修改回退路径
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
- **涉及文件/行号**: `streamlit_app.py` L132-163

### P1-5: yfinance 周/月/季线 timestamp 约定未验证

- **状态**: ✅ 已修复 | **提交**: e92a147
- **发现来源**: design-review-v2.md §5 N1
- **简述**: v2 的 `_get_period_start_ts()` 对周线/月线/季线使用 `last_ts + 1天`，假设 DB 中这些 TF 的 ts 是周期结束日（周五/月末/季末）。若 yfinance 实际返回周期开始日（周一/月初/季初），则 `ts + 1天` 计算出错误的 period_start，导致周/月/季线合成判断全部错误。
- **根因分析**: v2 设计方案确立后未执行 Phase 0（yfinance timestamp 验证），直接进入实施。该假设从未被显式验证。
- **影响范围**: 如果假设错误，周线/月线/季线的合成逻辑全部错误，级联合成链路在日线以上全部失效。
- **建议修复方案**:
  1. 运行验证脚本确认 yfinance 对不同 interval 返回的 index 格式:
     ```python
     import yfinance as yf
     data = yf.download("AAPL", period="1mo", interval="1wk")
     print(data.index)  # 检查是周一还是周五
     data = yf.download("AAPL", period="6mo", interval="1mo")
     print(data.index)  # 检查是月初还是月末
     ```
  2. 如果验证通过（ts=周期结束日），无需修改。
  3. 如果 ts=周期开始日，需要重新计算 `_get_period_start_ts()` 的周/月/季线分支。
- **涉及文件/行号**: `data_loader.py` `_get_period_start_ts()` 周线/月线/季线分支

### 实施后追加修复 (未在原始P1清单中)

以下 bug 在实施后发现并修复，记录于此以保证追踪完整性：

| Bug | 提交 | 根因 | 修复 |
|-----|------|------|------|
| ALL_TFS 导入错误 | 086effc | import 路径循环依赖 | 改回本地常量 |
| _write_parquet 路径 | e040310 | parent少一层 | 统一 parent.parent.parent |
| 时区边界 bar 遗漏 | d6f5839 | SQL字符串比较缺少tz后缀 | period_end 加 tz_suffix |
| 日线级反向边界 | 2a2599b | finer_tf 时区格式不匹配 | 从 finer_tf 提取时区 |
| 跨时区假转换 | a87cea5 | 剥离-拼接 替代真转换 | 真 tz_convert() |
| _needs_synthesis 日线失效 | 3bf8a47 | cutoff >= 下一周期判定错误 | cutoff > last_ts |
| _get_query_start_for_synthesis 窗口颠倒 | fec408a | last_ts+1天 超出 cutoff | 返回 last_ts |

---

## 4. P2 — 可延后修复

### P2-1: 合成 bar 无 UI 标识

- **发现来源**: backtest-logic-change-analysis.md §差异#8
- **简述**: 合成 bar 在图表上与完成 bar 外观相同，用户无法区分。合成的部分 bar（如当天尚未完成的日线 bar）与历史完成 bar 并列显示，缺少视觉提示。
- **影响**: UX 问题，用户可能误以为合成 bar 代表完整周期数据。
- **建议修复**: 在图表上对合成 bar 使用不同颜色/虚线标记，或在 `_build_output_df` 中添加 `is_synthetic` 标记列。

### P2-2: L141 注释不准确

- **发现来源**: backtest-logic-change-analysis.md §差异#5
- **简述**: `streamlit_app.py` L141 注释 "★ 不再需要截断！parquet 已经是 n_pts 条" 不准确——parquet 条数（120）可能与视图 n_pts（如 60）不一致。
- **影响**: 代码可读性，误导维护者。
- **建议修复**: 更新注释为 "parquet 由 _sync_all_cascading() 前置写入，实际条数可能超过视图 n_pts"。

### P2-3: DB 查询量增加 75%

- **发现来源**: backtest-logic-change-analysis.md §差异#7
- **简述**: V2 每次 rerun 的 DB 查询从 4 次增加到最多 7 次（4 次基础查询 + 最多 3 次合成期查询）。
- **影响**: 1x 播放速度下无影响，5x+ 速度下可能成为瓶颈。当前无需优化，但需关注。
- **建议**: 观察实际性能，必要时对 `_query_tf_for_period` 的结果做短期缓存。

### P2-4: `_get_period_start_ts` 缺少 "1分钟" 显式分支

- **发现来源**: backtest-logic-change-analysis.md §差异#9
- **简述**: `_get_period_start_ts()` 中 "1分钟" 靠 `else` 分支覆盖（`ts + pd.Timedelta(minutes=1)`）。行为正确但缺少显式处理。
- **影响**: 代码可维护性。如果将来重构 `else` 分支逻辑，"1分钟" 行为可能被意外改变。
- **建议修复**: 添加显式 `if tf == "1分钟":` 分支。

### P2-5: 浏览模式与回测模式共享 parquet 文件

- **发现来源**: design-review.md §4.4, db-to-parquet-deep-dive.md §5.3
- **简述**: 两种模式都写入 `data/display/{tf}.parquet`。用户快速切换模式时可能读到对方写入的数据。切换回浏览模式时 parquet 可能包含回测截断数据（非最新）。
- **影响**: UX 问题，模式切换时可能短暂显示错误数据。`clear_display_cache()` 提供了清除机制但未被自动调用。
- **建议修复**: 使用不同文件名前缀（`browse_{tf}.parquet` vs `backtest_{tf}.parquet`），或在 `clear_display_cache()` 基础上增加自动检测。

### P2-6: `_sync_to_display` 回测分支成为死代码

- **发现来源**: design-review.md §4.2
- **简述**: v2 中回测模式不再调用 `_sync_to_display()`，但该函数中保留了回测分支（`cutoff_date is not None`）。成为死代码。
- **影响**: 代码维护困惑。如果将来有人重构恢复回测模式调用，会走旧的单级合成路径。
- **建议修复**: 评估是否保留作为 `_sync_all_cascading` 的降级路径；若不保留，添加注释标记为 deprecated。

---

## 5. 设计阶段已解决的问题 (无需代码修改)

以下问题在 v1 审查中发现，在 v2 设计方案中已彻底解决。不需要在代码层面额外处理。

| 编号 | 问题 | v1 表现 | v2 解决方式 | 验证文档 |
|------|------|---------|------------|---------|
| D1 | tz-aware vs tz-naive TypeError | `_needs_synthesis()` 直接比较不同时区的 Timestamp 抛异常 | `_ensure_tz_naive()` 统一剥离时区 | design-review-v2.md §1.1 |
| D2 | 周线 period_start 计算错误 | 返回下一周五（period END），周线永远不合成 | 统一 `last_ts + 1天` | design-review-v2.md §1.2 |
| D3 | Parquet Date 混合时区 | 正常 bar 和合成 bar Date 格式不一致 | `_format_synth_date()` 按 TF 匹配原生格式 | design-review-v2.md §1.3 |
| D4 | 合成数据来源依赖显示窗口 | `prev_data` (120条窗口) 过滤合成数据 | `_query_tf_for_period()` 重新查询 DB，不依赖显示窗口 | design-review-v2.md §2.1 |
| D5 | 显示/合成数据耦合 | `prev_data` 同时用于显示和合成 | `synth_cache` 只存合成 bar，显示数据独立查询 | design-review-v2.md §2.1 |
| D6 | 日线合成未考虑开盘时间 | 从 00:00 开始查询盘前空区间 | `query_start` 调整为 09:30（当 finer_tf=60分钟时） | design-review-v2.md §2.3 |
| D7 | `_load_chart_data` 无降级路径 | parquet 不存在时无处理 | 回退到 `_cached_fetch_stock()` + `has_data()` 前置检查 | design-review-v2.md §2.4 |
| D8 | 并发安全 | 4 个 fragment 可能同时读写 parquet | 写入在 fragment 渲染之前完成，fragment 只读 | design-review-v2.md §2.5 |
| D9 | 函数命名混淆 | `_get_period_start()` (返回 str) 与 `_get_period_start_ts()` (返回 Timestamp) 易混淆 | 保留 `_get_period_start_ts` + `_get_query_start_for_synthesis`，注释说明区别 | design-review-v2.md §2.6 |
| D10 | `_get_period_start_ts` 月线/季线注释误导 | 变量名 `next_month` 暗示月末 | 简化为 `ts + 1天`，代码和注释统一 | design-review.md §2.2 |
| D11 | 数据就绪检查 | 新 ticker 无数据时 `prev_data` 为空 | `has_data()` 前置检查 + `st.warning` 提示 | design-review.md §3.2 |
| D12 | 跨周末/节假日处理 | 未显式处理 | 查询自然返回空（DB 无周末数据），聚合使用实际数据 | design-review.md §3.3/§3.5 |
| D13 | 同一 TF 多视图去重 | 未处理 | `unique_tfs = set(...)` 自动去重 | design-review.md §4.3 |

---

## 6. 修复优先级建议

按依赖关系和影响范围排序:

```
第一优先级 (本次迭代修复，影响功能正确性):
  P1-5  yfinance timestamp 验证          ← 前置依赖，影响着周/月/季线全部逻辑
  P1-1  n_pts 强制统一为 120             ← 影响所有视图的数据显示
  P1-2  _sync_all_cascading 返回值被丢弃  ← 失败无感知，用户可能看到错误数据

第二优先级 (本次迭代修复，影响健壮性):
  P1-4  回退路径返回最新数据              ← 回测一致性受破坏
  P1-3  合成链断裂                       ← 部分 ticker 的粗粒度视图无法合成

第三优先级 (下一迭代修复):
  P2-1  合成 bar 无 UI 标识              ← UX 改进
  P2-6  _sync_to_display 死代码          ← 代码清洁
  P2-2  L141 注释不准确                   ← 代码清洁

第四优先级 (按需修复):
  P2-4  1分钟显式分支                    ← 代码可维护性
  P2-5  浏览/回测共享 parquet 文件       ← UX 边缘情况
  P2-3  DB 查询量增加 75%                ← 性能观察，暂不修复
```

### 关键依赖链

```
P1-5 (yfinance验证)
  └─→ 如果验证不通过 → 需要修改 _get_period_start_ts() 周/月/季线分支
       └─→ 影响 P1-3 (合成链断裂) 的修复策略

P1-1 (n_pts) + P1-2 (返回值丢弃)
  └─→ 都需要修改 main() 和 _sync_all_cascading() 的接口

P1-3 (合成链断裂) + P1-4 (回退路径)
  └─→ 都涉及数据可用性降级，可一起处理
```

---

## 附录 A: 问题来源交叉索引

| 本文编号 | design-review.md | design-review-v2.md | backtest-logic-change-analysis.md | db-to-parquet-deep-dive.md | cascading-synthesis-v2.md |
|----------|-----------------|--------------------|-----------------------------------|---------------------------|-------------------------|
| P0-1 | §1.3 | §1.1 | — | — | §2.2 / §A.1 |
| P0-2 | §2.1 | §1.2 | — | — | §3.5 / §A.1 |
| P0-3 | §1.2 | §1.3 | — | — | §2.1 / §A.1 |
| P1-1 | — | — | §差异#2 | — | — |
| P1-2 | — | — | §差异#4 | — | — |
| P1-3 | — | — | §差异#6 | — | — |
| P1-4 | §4.1 | — | §差异#3(c) | — | — |
| P1-5 | — | §5 N1 | — | — | — |
| P2-1 | — | — | §差异#8 | — | — |
| P2-2 | — | — | §差异#5 | — | — |
| P2-3 | — | — | §差异#7 | — | — |
| P2-4 | — | — | §差异#9 | — | — |
| P2-5 | §4.4 | — | — | §5.3 | — |
| P2-6 | §4.2 | — | — | — | — |

## 附录 B: 涉及文件汇总

| 文件 | 需修改的问题 |
|------|------------|
| `filter_app/services/data_loader.py` | P1-1, P1-3, P1-5, P2-4, P2-6 |
| `filter_app/streamlit_app.py` | P1-1, P1-2, P1-4, P2-2 |

---

> 清单维护: 每完成一个问题的修复，请更新对应状态。新增问题请在对应优先级表格中追加。
