# PnL 曲线与信号对比图表不一致 — 根因分析报告

**报告类型**: 根因分析 (Root Cause Analysis)
**来源**: T1 (PnL 逻辑追踪) + T2 (信号逻辑追踪) 综合
**分析日期**: 2026-07-19
**状态**: 最终版

---

## 1. 现象描述

用户在回测结果可视化的 HTML 页面中观察到：**PnL 曲线显示的多空方向与信号对比图表中同一时间段的 Schmitt 状态变化不同步**。具体表现包括：

- 信号对比图表 (Section 3) 显示 `sig=+1`（绿色做多区域），但 PnL 曲线 (Section 4) 做多线 `pnl_long` 保持水平 100，无任何波动。
- 信号已翻转为 `sig=-1`（红色做空区域），但做多 PnL 曲线仍停留在前一笔做多交易的结算值，视觉上像是"还在做多"。
- 空仓期间（信号=0 或信号已反转但未开仓），PnL 线被冻结，信号线却持续更新，两者完全脱节。
- 少数情况下，持仓热力图 (Section 5) 显示的持仓方向与 PnL 图、信号图三者各不相同。

**核心问题**: 用户期望信号图表中的多空状态变化与 PnL 曲线的多空切换在时间上同步，但实际系统存在多个机制导致三张图表各说各话。

---

## 2. 数据流全景

以下 ASCII 图展示从 filter engine 计算到前端渲染的完整数据流，标注每个分叉点的数据含义差异：

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     filter_engine.py (每个 bar 执行一次)                      │
│                                                                             │
│  Step 1: filtered = FILTERS[fid](noisy, t)                                   │
│  Step 2: schmitt = _schmitt_trigger(v, a, ...)                               │
│          │  sig_t = schmitt["sig"]    [+1=多, -1=空, 0=观望]                 │
│          │                                                                    │
│          │  ┌── CSV/Parquet {v}_sig = _last_value(sig_t)                     │
│          │  │                                                               │
│          │  │     ───────────→  信号对比图表 (Section 3)                     │
│          │  │                   瞬时 Schmitt 状态, 每 bar 更新               │
│          │  │                   值域: {-1, 0, +1}                            │
│          │  │                   ⚠ 不受任何 freeze 影响                       │
│          │  │                                                               │
│          ├── Step 3: all_pairs = _find_all_pairs(sig_t)                      │
│          │   │        配对 (s1, s2), 第一个信号段不被配对 ← P0 根因           │
│          │   │                                                               │
│          ├── Step 4: pred_pairs = _fit_physics_parabola(...)                  │
│          │   │        ⚠ len(pred_pairs)==0 → PnL 返回全 100 ← P1 根因        │
│          │   │                                                               │
│          └── Step 5: _compute_strategy_pnl(t, filtered, sig_t,                │
│                      all_pairs, pred_pairs, ...)                              │
│              │                                                               │
│              │  ┌── long_pnl / short_pnl (累计, 起始 100)                    │
│              │  │  ┌── trade_records (入场/离场事件)                          │
│              │  │  │                                                        │
│              │  │  │  ───→ ParquetStore / CSVBuilder                         │
│              │  │  │       │                                                │
│              │  │  │       ├── {v}_pnl_long  = _last_value(long_pnl)         │
│              │  │  │       │   ⚠ 受 freeze 影响                             │
│              │  │  │       │   ───→ PnL 曲线图 (Section 4)                   │
│              │  │  │       │       累计盈亏 + freeze 机制                     │
│              │  │  │       │       值域: (0, +∞), 起始 100                   │
│              │  │  │       │                                                │
│              │  │  │       ├── {v}_pnl_short = _last_value(short_pnl)        │
│              │  │  │       │                                                │
│              │  │  │       └── {v}_trade / {v}_trade_return /               │
│              │  │  │           {v}_trade_reason                              │
│              │  │  │                                                        │
│              │  │  └──→ BS markers 生成 (bs_marker.py)                       │
│              │  │       │                                                    │
│              │  │       ├── {v}_bs_entry ← ≤ 匹配 (永不回退) ← T2 发现 #2    │
│              │  │       └── {v}_bs_exit  ← ≤ 匹配                           │
│              │  │                                                            │
│              └── Step 6: 跨周期对齐 (仅低周期)                                │
│                  │                                                           │
│                  ├── long_mask / short_mask  ← 来自高周期                     │
│                  │   ⚠ 低周期的 {v}_long_pos 数据源 ≠ {v}_pnl_long 数据源     │
│                  │   ───→ 持仓热力图 (Section 5)                              │
│                  │       高周期对齐的持仓状态                                  │
│                  │       与 Section 4 PnL 可能矛盾 ← P3 根因                  │
│                  │                                                           │
│                  └── bs_markers 叠加 holding_masks                            │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  PnL Freeze 机制 (parquet_store.py:362-371 / event_recorder.py:75-85) │   │
│  │                                                                       │   │
│  │  if long_pos:  _last_pnl[last_key] = pnl_long   # 更新               │   │
│  │  else:         pnl_long = _last_pnl[last_key]    # 冻结! ← P1/P2 根因 │   │
│  │                                                                       │   │
│  │  结果: sig 持续更新, PnL 被冻结 → 视觉不一致                           │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘

                                ↓ Parquet 文件 (50 列, N 行)

┌─────────────────────────────────────────────────────────────────────────────┐
│  view_backtest.py → df_to_columns() → json.dumps() → window.BACKTEST_DATA   │
│                                                                             │
│  前端同源读取:                                                               │
│  ├── buildSignals()  ← col(data, v + '_sig')            信号对比图          │
│  └── buildPnl()      ← col(data, v + '_pnl_long/short') PnL 曲线图          │
└─────────────────────────────────────────────────────────────────────────────┘
```

**关键事实**: PnL 曲线和信号对比图读取的是同一个 Parquet 文件、同一个 `window.BACKTEST_DATA` 对象、同一行数据。它们读取不同的**列**，差异源自数据生成阶段的业务逻辑——信号是原始意图，PnL 是实际持仓结果（含 freeze-on-exit 机制）。差异是设计如此，而非数据源不同。

---

## 3. 根因分析（按严重程度排序）

### P0 — 首个信号段永不交易

**严重程度**: 高 — 导致回测起始阶段信号与 PnL 完全脱节

**位置**: `filter_app/services/filter_engine.py`, `_find_all_pairs()` 函数，行 537-593

**机制**:

`_find_all_pairs()` 的三步配对逻辑：

```
Step 1: 收集所有非零段 segments = [(start, end, val), ...]
Step 2: 合并相邻同号段（中间 0 值段被吸收）
Step 3: 相邻异号段配对 → pairs = [(seg_j.start, seg_{j+1}.start), ...]
        即: pair_end = 下一个信号段的起始索引
```

关键问题：pairs 是从**第二个**信号段开始生成。第一个信号段永远不会作为 `pair_end` 出现在任何 pair 中，因此永远不会产生交易。

**实例**:
```
sig_t = [0, 0, 0, 1, 1, 1, 1, 0, -1, -1, -1, -1]
                          [--------]         [------------]
                          seg1: (3,6,+1)     seg2: (8,11,-1)

pairs = [(3, 8)]  ← 只有一对, pair_end=8, sig_t[8]=-1 → 做空
                     bars 3-6 的做多信号段没有被交易!
```

**表现**:
- 信号对比图: `sig=+1` (bars 3-6)，绿色做多区域
- PnL 曲线图: `pnl_long=100` (bars 3-6)，水平线，无任何交易
- 每个窗口的 `_find_all_pairs` 重新扫描，第一个信号段在每个窗口中都不被交易，问题在整条回测曲线上持续存在

**影响范围**: 所有回测视图的起始阶段，信号第一次出现到第一次翻转之间的所有 bar。

**修复方向**:
- 方案 A: 修改 `_find_all_pairs()` 从第一个非零信号段开始生成 pair，而非从第二个。例如：在 segs[0] 之前插入一个虚拟前置段（值为 segs[0] 的相反信号），使第一个真实信号段成为某个 pair 的 `pair_end`。
- 方案 B: 在 `_compute_strategy_pnl()` 中单独处理首个信号段：如果窗口的第一个非零信号段没有对应的前置 pair，仍创建交易（方向由 `sig_t[seg_start]` 决定，入场在 `seg_start`）。

---

### P1 — pred_pairs 为空导致 PnL 全平 100

**严重程度**: 高 — 即使信号正常，PnL 也可以完全不响应

**位置**: `filter_app/services/backtest_core.py`, `_compute_strategy_for_view()` 函数，行 789-796

**机制**:

```python
show_strategy = cfg.get("show_strategy", False)
if not show_strategy or schmitt is None or len(pred_pairs) == 0:
    n = len(t)
    return (
        np.full(n, 100.0),  # long_pnl 全平
        np.full(n, 100.0),  # short_pnl 全平
        [],
    )
```

当 `len(pred_pairs) == 0` 时，即使 `show_sch=True` 且 Schmitt 信号正常输出 ±1，PnL 也会**直接返回全 100 的平坦曲线**，绕过了整个 `_compute_strategy_pnl()` 交易逻辑。

**`pred_pairs` 为空的触发条件**:
1. `cfg.get("show_pred")` 为 False（`backtest_core.py:740`）—— 用户关闭了预测显示
2. 所有 pair 的 `pair_end - pair_start < 3` —— 不足 3 个数据点无法拟合抛物线
3. 抛物线拟合失败 —— `_fit_physics_parabola()` 返回 None

**表现**:
- 信号对比图: `sig=±1` 正常显示
- PnL 曲线图: `pnl_long=100`, `pnl_short=100` 完全平坦
- 两者看起来"方向不一致"——信号在动，PnL 不动

**与 P0 的叠加效应**: 如果首个信号段未配对（P0），且剩下的 pair 数量不足或拟合失败（P1），整个窗口可能完全无交易，PnL 全程为 100。

**修复方向**:
- 解耦 `pred_pairs` 与 PnL 计算的硬依赖：当 `len(pred_pairs) == 0` 但 `len(all_pairs) > 0` 时，回退到仅基于 Schmitt 信号配对（无预测轨道止损）的交易逻辑。
- 或者：当 `pred_pairs` 为空但 `show_strategy=True` 时，使用 `all_pairs` 替代 `pred_pairs` 送入 `_compute_strategy_pnl()`，仅禁用抛物线轨道离场条件。

---

### P2 — PnL Freeze 机制导致空仓期信号与 PnL 脱节

**严重程度**: 高 — 最直接影响用户看到的"不一致"

**这是 T1 根因四和 T2 根因一的合并**

**位置**:
- `filter_app/services/parquet_store.py`, 行 362-371
- `filter_app/services/event_recorder.py`, 行 75-85

**机制**:

```python
# parquet_store.py:362-371 (ParquetStore 版本)
long_pos = row.get(f"{prefix}_long_pos", False)
for pnl_key, pos_flag in [("pnl_long", long_pos), ("pnl_short", short_pos)]:
    col_name = f"{prefix}_{pnl_key}"
    last_key = f"{prefix}_{pnl_key}"
    if pos_flag:
        self._last_pnl[last_key] = row.get(col_name, 100.0)   # 持仓: 更新最新值
    else:
        row[col_name] = self._last_pnl.get(last_key, 100.0)   # 空仓: 冻结为上次终值

# event_recorder.py:75-85 (CSVBuilder 版本, 逻辑完全一致)
if pos_flag:
    self._last_pnl[last_key] = cols[col_name]
else:
    cols[col_name] = self._last_pnl.get(last_key, 100.0)
```

**核心机制**: 当 `long_pos=False`（空仓）时，`pnl_long` 被**强制覆盖**为上一次持仓时的退出值。但 `sig` 列不受此影响——它始终是当前 bar 的原始 Schmitt 信号值。

**各阶段状态对照表**:

| 阶段 | sig | long_pos | pnl_long | 视觉表现 |
|------|-----|----------|----------|---------|
| 信号出现 | +1 | False | 100 (冻结) | 信号绿, PnL 平 |
| 确认开仓 | +1 | True | 100.5 | 信号绿, PnL 升 |
| 持仓中 | +1 | True | 105.2 | **一致** |
| 信号翻转 | -1 | False | 105.2 (冻结) | 信号红, PnL 平在盈利位 |
| 信号持续空 | -1 | False | 105.2 (冻结) | 信号红, PnL 仍是做多的盈利 |
| 确认开空 | -1 | True | 104.8 | 信号红, PnL 降 |

**关键矛盾**: 信号从 +1 翻转为 -1 后，用户看到信号图显示红色（做空），但 `pnl_long` 仍停留在做多交易的盈利值 105.2，视觉上暗示"还在做多"。直到做空交易真正入场，`pnl_short` 才开始下降。

**设计意图 vs 用户期望**:
- 设计意图：PnL 是"实际账户权益曲线"，空仓期间权益不变，所以 freeze 是正确的。
- 用户期望：PnL 曲线和信号方向应该"对齐"——看多信号对应上升曲线，看空信号对应下降曲线。
- **冲突根源**: 用户在信号图中直观地看到多空方向，期望 PnL 图能即时反映这个方向的变化，但系统设计的 PnL 是滞后指标。

**修复方向**:
- 方案 A (推荐): 空仓期间 PnL 不应 freeze 在退出值，而应显示为特殊值（如 NaN，前端渲染为虚线/灰色线段）或显示为 0（平仓基线），让用户一眼看出"当前无持仓"。
- 方案 B: 在信号对比图表中叠加 `_long_pos` / `_short_pos` 作为半透明背景带，让用户同时看到信号方向（sig）和实际持仓状态（long_pos/short_pos），区分"信号意图"与"执行状态"。

---

### P3 — 时间维度差异：信号是瞬时状态，PnL 是累计结果

**严重程度**: 中 — 在信号转换边界造成视觉不一致

**位置**: 不限于单一代码位置，是系统设计的固有特性

**机制**:

- **信号图**绘制 `_sig` 列：**瞬时** Schmitt 状态（当前 bar 的信号值），每个 bar 独立更新。
- **PnL 图**绘制 `_pnl_long` / `_pnl_short` 列：**累计** PnL 值（从窗口起点到当前 bar 的所有交易结果），叠加了 freeze 机制。

在信号转换的边界 bar，这种时间维度的差异尤为明显：

```
bar 49: sig=+1, pnl_long=108.5, pnl_short=100
bar 50: sig=-1 (信号翻转!) → 做多离场, 做空入场
        pnl_long=108.3 (做多结算, 然后被 freeze)
        pnl_short=100   (做空刚入场, 无浮盈)
bar 51: sig=-1, pnl_long=108.3 (freeze), pnl_short=99.8
```

在 bar 50：
- `_sig` = -1（空头信号）—— 信号图显示红色
- `_pnl_long` = 108.3（上一笔做多的盈利结算值）—— PnL 图显示做多线在盈利高位
- `_pnl_short` = 100（做空刚入场）—— PnL 图显示做空线在起始位

用户看到信号是空头，但做多 PnL 线在盈利高位、做空 PnL 线在起始位，产生"信号与 PnL 方向不一致"的视觉感受。

**这实际上是正确行为**，但可以通过可视化手段缓解：
- 在信号图中用竖线标注交易入场/离场点
- 用颜色渐变或透明度标识"当前处于哪笔交易中"
- 增加"滞后 N bars"的标注说明

---

### P4 — 低周期视图的 PnL 与持仓状态数据源不同

**严重程度**: 中 — 导致三张图可能显示三种不同的方向

**位置**:
- PnL 计算: `backtest_core.py:_compute_strategy_for_view()` → `filter_engine.py:_compute_strategy_pnl()` (本周期独立计算)
- 持仓掩码: `backtest_core.py:_compute_masks_for_view()` → `filter_engine.py:_compute_holding_masks()` (高周期对齐)

**机制**:

对于低周期视图（如 5 分钟），回测管道按**粗到细**顺序处理：

```
1. 日线:   计算 PnL → 缓存到 tf_pnl_cache["日线"]
2. 60分钟: _compute_masks_for_view(日线 PnL) → long_mask/short_mask
3. 15分钟: _compute_masks_for_view(60分钟 PnL) → long_mask/short_mask
4. 5分钟:  _compute_masks_for_view(15分钟 PnL) → long_mask/short_mask
```

低周期的两套数据来源不同：

| 列名 | 数据来源 | 计算方式 |
|------|----------|----------|
| `_pnl_long` / `_pnl_short` | **本周期** PnL 计算 | 本周期 sig 驱动的独立交易 |
| `_long_pos` / `_short_pos` | **高周期**跨周期对齐 | 高周期 PnL 经过 `_align_pnl_to_current_tf` + `_compute_holding_masks` |

**表现**: 如果本周期 Schmitt 信号与高周期方向不一致：
- 信号对比图 (Section 3): 显示 5 分钟的 `sig=+1`（做多信号）
- 持仓热力图 (Section 5): 显示高周期的 `_short_pos=1`（做空持仓）
- PnL 曲线图 (Section 4): 显示 5 分钟的 `_pnl_long` / `_pnl_short`
- 三张图可能分别显示不同的方向

**修复方向**:
- 方案 A: 低周期视图统一使用本周期计算的数据（PnL 和持仓掩码都用本周期），不使用跨周期对齐。
- 方案 B: 在前端图表上明确标注每张图的数据来源（"本周期信号"/"高周期持仓对齐"/"本周期 PnL"），让用户了解差异来源。
- 方案 C: 对低周期视图，`_long_pos` 也应基于本周期计算，保持与 `_pnl_long` 的一致性。

---

### P5 — BS Marker 的 `<=` 匹配导致标记永不回退

**严重程度**: 低 — 影响 BS 标记列的语义清晰度

**位置**: `filter_app/services/parquet_store.py`, 行 514-521

**机制**:

```python
# entry_markers 使用 <= 匹配
for m in bs_markers.get("entry_markers", []):
    if int(m[0]) <= view_last_idx:   # ← 捕获"曾经"出现但可能已过期的标记
        bs_entry = str(m[1])
```

`bs_entry` 永远不会被"清除"为 None 或空字符串。一旦出现过 B 标记，所有后续 bar 的 `bs_entry` 都会保持为 B，直到下一个 S 标记覆盖。这让 BS 标记看起来像**持久状态**而非**离散事件**。

**影响**: 用户查看 `_bs_entry` 列时，无法区分"当前 bar 有入场事件"和"历史上某个 bar 有过入场事件"。如果 `_sig` 已翻转为相反方向，而 `_bs_entry` 仍是旧方向标记，会加剧视觉混淆。

**修复方向**:
- 方案 A: 改为 `==` 精确匹配（仅当前 bar 有标记时才写入），空 bar 写入空字符串。
- 方案 B: 在前端渲染时，仅在 `_trade` 列有入场事件的 bar 上显示 BS 标记点（散点图），而非连线。

---

## 4. 修复方案汇总

| 优先级 | 根因 | 修复方案 | 涉及文件 | 影响范围 | 风险 |
|--------|------|---------|---------|---------|------|
| **P0** | 首个信号段不交易 | 修改 `_find_all_pairs()`：在 segs[0] 前插入虚拟反向前置段,使第一个真实信号段成为某 pair 的 `pair_end`; 或修改 `_compute_strategy_pnl()`：单独处理窗口第一个非零信号段 | `filter_engine.py:537-593` 或 `filter_engine.py:666-868` | 所有回测视图的起始阶段 | 中 — 需验证虚拟前置段不影响离场逻辑 |
| **P1** | pred_pairs 为空导致 PnL 全平 100 | `_compute_strategy_for_view()` 中，当 `len(pred_pairs)==0` 但 `len(all_pairs)>0` 且 `show_strategy=True` 时，回退到仅使用 `all_pairs`（无抛物线轨道止损）计算 PnL | `backtest_core.py:789-796` | 所有视图 | 低 — 仅增加回退路径,不改主路径 |
| **P2** | PnL Freeze 时机不一致 | 方案 A: 空仓时 `pnl_long`/`pnl_short` 写 NaN，前端渲染为虚线/灰色段; 方案 B: 在信号图中叠加 `long_pos`/`short_pos` 半透明背景带 | `parquet_store.py:362-371` + `event_recorder.py:75-85` + `回测结果可视化.html` | 所有视图的 PnL 列 + 前端渲染 | 低-A / 极低-B |
| **P3** | 信号瞬时 vs PnL 累计 (时间维度) | 在信号图中用竖线标注交易入场/离场点，或在转换 bar 增加视觉提示（颜色渐变、tooltip 说明"信号已翻转,上一笔做多结算中"） | `回测结果可视化.html:625-745` | 前端渲染 | 极低 — 纯前端改动 |
| **P4** | 低周期 PnL 与持仓数据源不一致 | 低周期视图的 `_long_pos` 也基于本周期 PnL 计算,保持与 `_pnl_long` 一致性; 或前端标注数据来源 | `backtest_core.py:789-850` + `filter_engine.py:998-1043` | 低周期视图 | 中 — 需确认跨周期对齐的设计意图 |
| **P5** | BS Marker <= 匹配永不回退 | 改为 `==` 精确匹配; 或在事件流层面区分"当前有效"和"历史出现" | `parquet_store.py:514-521` | BS 标记列 | 低 — 需确认前端 BS 渲染逻辑依赖 |

---

## 5. 建议修复优先级与实施路线

### 第一阶段：立即修复（1-2 天）

**P2 — PnL Freeze 可视化改善** (方案 B)

- **理由**: 影响面最小（仅前端改动），用户可见改善最大。
- **改动**: 在 `回测结果可视化.html` 的 `buildSignals()` 函数中，为每个子图叠加 `_long_pos` 和 `_short_pos` 列作为半透明背景色带（绿色=做多持仓中，红色=做空持仓中，透明=空仓）。
- **效果**: 用户一眼能看到：信号图下半透明色带标识"实际持仓"，上半部分信号线标识"信号意图"，两个维度的差异一目了然。
- **风险**: 极低，纯前端展示层改动。

**P1 — pred_pairs 空数据回退**

- **理由**: 修复一个功能性缺陷——当用户打开 `show_sch` 但未打开 `show_pred` 时，PnL 完全空白。这属于"配置组合导致功能异常"。
- **改动**: 在 `backtest_core.py:_compute_strategy_for_view()` 行 789 处增加回退逻辑：`if len(pred_pairs) == 0 and len(all_pairs) > 0: pred_pairs = all_pairs`（仅用于 PnL 计算，不用于抛物线轨道）。
- **风险**: 低，仅增加回退路径。

### 第二阶段：短期修复（3-5 天）

**P0 — 首个信号段交易**

- **理由**: 修复回测起始阶段的系统性偏差。每次回测的第一个信号段都不被交易，会低估策略收益，同时造成起始阶段信号与 PnL 的明显不一致。
- **改动**: 在 `_find_all_pairs()` 中插入虚拟前置段，使第一个真实信号段也成为有效 pair。
- **风险**: 中，需充分测试虚拟前置段的插入逻辑（虚拟前置段的值应设为第一个真实段的相反信号，确保入场方向正确）。

**P3 — 信号转换边界的可视化提示**

- **理由**: 在不改变 PnL 计算逻辑的前提下，通过前端改善让用户理解"信号先变、持仓后变"的时序关系。
- **改动**: 在信号图中添加 `_trade` 列的入场/离场事件竖线标注，tooltip 显示"做多入场"/"做空离场"。
- **风险**: 极低。

### 第三阶段：长期改进（1-2 周）

**P4 — 低周期数据源统一**

- **理由**: 需要仔细审查设计意图——跨周期对齐是有意为之还是实现便利？如果是有意设计，则不应改逻辑而应改进文档/标注。
- **改动**: 与团队确认跨周期对齐的业务需求后，再决定统一还是标注。
- **风险**: 中，涉及跨周期逻辑变更。

**P5 — BS Marker 语义澄清**

- **理由**: 优先级低，主要是数据语义的清晰度问题，不影响核心功能。
- **改动**: 与前端 BS 渲染逻辑协调后修改匹配规则。
- **风险**: 低。

---

## 6. 总结

PnL 曲线与信号对比图表的"不一致"并非单一 bug，而是**五个独立机制的叠加效应**：

1. **P0**: 首个信号段不被交易——信号出现了，但没有交易。
2. **P1**: pred_pairs 为空→PnL 全平——即使有信号和 pairs，PnL 也不计算。
3. **P2**: PnL Freeze——空仓时 PnL 被冻结，信号持续更新。
4. **P3**: 时间维度差异——信号是前瞻指标，PnL 是滞后结果。
5. **P4**: 低周期数据源分裂——PnL、持仓状态、信号来自不同周期。
6. **P5**: BS 标记永不回退——加剧视觉混淆。

**核心矛盾**: 系统将"瞬时信号意图"（sig）和"累计交易结果"（pnl）放在同一个时间轴上并排展示，但两者在时间维度、更新频率、状态持久化上存在本质差异。用户直观地期望它们同步变化。

**推荐修复策略**: 先做 P2 方案 B（前端叠加持仓状态带）——这是成本最低、效果最明显的改善。后续根据用户反馈决定是否需要进一步改动 P1 和 P0。

---

## 附录：关键文件索引

| 文件 (绝对路径) | 关键行号 | 涉及根因 |
|----------------|----------|---------|
| `/Users/xfpan/claude/filter_research/filter_app/services/filter_engine.py` | 537-593 | P0: `_find_all_pairs()` 配对逻辑 |
| `/Users/xfpan/claude/filter_research/filter_app/services/filter_engine.py` | 666-868 | P0/P1: `_compute_strategy_pnl()` 交易计算 |
| `/Users/xfpan/claude/filter_research/filter_app/services/filter_engine.py` | 727-731 | 方向判定: `is_long = (sig_t[pair_end] == 1)` |
| `/Users/xfpan/claude/filter_research/filter_app/services/filter_engine.py` | 875-995 | P4: `_align_pnl_to_current_tf` |
| `/Users/xfpan/claude/filter_research/filter_app/services/filter_engine.py` | 998-1043 | P4: `_compute_holding_masks` |
| `/Users/xfpan/claude/filter_research/filter_app/services/backtest_core.py` | 789-796 | P1: pred_pairs 空数据回退 |
| `/Users/xfpan/claude/filter_research/filter_app/services/parquet_store.py` | 362-371 | P2: PnL Freeze (ParquetStore) |
| `/Users/xfpan/claude/filter_research/filter_app/services/parquet_store.py` | 514-521 | P5: BS Marker <= 匹配 |
| `/Users/xfpan/claude/filter_research/filter_app/services/event_recorder.py` | 75-85 | P2: PnL Freeze (CSVBuilder) |
| `/Users/xfpan/claude/filter_research/filter_app/services/event_recorder.py` | 136-138 | {v}_sig 列生成 |
| `/Users/xfpan/claude/filter_research/filter_app/services/event_recorder.py` | 188-196 | {v}_pnl_long/short 列生成 |
| `/Users/xfpan/claude/filter_research/docs/backtesting/回测结果可视化.html` | 625-745 | 信号对比图表渲染 (Section 3) |
| `/Users/xfpan/claude/filter_research/docs/backtesting/回测结果可视化.html` | 749-823 | PnL 曲线图渲染 (Section 4) |
| `/Users/xfpan/claude/filter_research/docs/backtesting/回测结果可视化.html` | 978-985 | 持仓热力图渲染 (Section 5) |
