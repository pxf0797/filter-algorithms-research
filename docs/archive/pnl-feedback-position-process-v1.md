# PnL 反馈驱动持仓设计（纯 PnL 反馈版 · v1）

> ⚠️ **已废弃（SUPERSEDED）— 2026-07-11。** 本文档描述的「PnL 回撤门控」方案
> （`dd_clear`/`dd_recover`、清仓/恢复、迟滞带、actual 权益曲线）**已弃用，不再实现**。
> 最终落地为**纯持仓状态显示**：在 PnL 下方以两条状态轨展示 Layer 0 实际成交区间——
> 绿=做多持仓 / 红=做空持仓 / 空白=不持，**无任何 PnL 反馈门控、无百分比轴**。
> 实现见 `filter/streamlit_app.py::_add_feedback_subplot`。以下正文保留作历史设计记录。

> **状态：完整设计文档（草案），供落地与迭代。** 承接 v0 骨架（`pnl-feedback-position-process-v0.md`），
> 依据反馈定稿三条约束：①**二值持仓**（只有持仓/清仓，无加减仓）；②**多空分离**（绿做多、红做空，各一套独立 gate）；
> ③**只做纯 PnL 反馈**，C 周期整套搁置为未来可选插件。
>
> **不改动 Layer 0**（现有 `_compute_strategy_pnl` / `long_pnl` / `short_pnl` / `trade_records`）。Layer 1 是纯 overlay。

---

## 0. 范围与非目标

| | 本版本 |
|---|---|
| **做** | 在现有 PnL 下方，新增「按 PnL 反馈决定该方向持仓/清仓」的一层，回测+实时共用 |
| **不做** | 加仓/减仓/仓位大小（只有 0 或 1）、C 周期五态、N_confirm、gap_count、双阈值 C 门、entry_void |
| **依赖** | 仅现有 `long_pnl` / `short_pnl`（逐 bar，含未实现）+ Layer 0 的逐笔入/离场事件 |

## 1. 定位与分层

```
Layer 0（现有，不改）：信号 → pairs → _compute_strategy_pnl
        产出 long_pnl / short_pnl（逐bar，含未实现）+ trade_records
                              │  ← 作为"理论满仓、永不停"的影子
                              ▼   （在 PnL 显示下方新增）
Layer 1（新增）：PnL 反馈持仓管理器（多空各一份 gate）
        产出 actual_long_curve / actual_short_curve + actual_trades + gate_timeline
```

## 2. 核心模型

### 2.1 多空分离，二值持仓
- **两条独立轨**：long gate 作用于 `long_pnl` 与 Layer0 的做多成交；short gate 作用于 `short_pnl` 与做空成交。互不影响，对齐现有双独立曲线。
- **每条轨只有两个状态**：`ACTIVE`（可持仓 = 仓）/ `FLAT`（清仓）。无中间仓位、无加减仓。
- **显示**：绿色 = 实际做多持仓段，红色 = 实际做空持仓段，空白 = 清仓。

### 2.2 理论 PnL 当「影子」，反馈信号 = 理论曲线回撤
- **Layer 0 的 `long_pnl`/`short_pnl` = 理论满仓、永不停**（每笔都交易）。这是你说"没问题"、要保留的那层。
- **关键洞察**：该曲线逐 bar 且**含持仓期未实现 PnL**，所以它的**回撤** `dd = 1 - shadow/shadow_peak` **同时表达了"浮亏"（持仓中未实现回撤）与"连亏"（累计回撤）**——一个信号源覆盖两个时间尺度，不必再单造"浮亏止损"。
- **Layer 1 = 实际持仓**：按该方向理论回撤，二值地开/关这条轨。
- **为何清仓后要靠影子恢复**：清仓后实际权益冻结、无法自我恢复；而理论曲线仍在跑，代表"若继续满仓会怎样"，用它回暖判断何时重新进场。

## 3. 数据结构

```
DirGate（long 一份、short 一份，结构相同）:
    active        : bool     # True=持仓可开(仓) / False=清仓(FLAT)
    holding       : bool     # 当前该方向是否有持仓
    shadow_peak   : float    # 该方向理论权益峰值（long_pnl/short_pnl 的 running max）
    actual_equity : float    # 该方向实际权益（只累计 ACTIVE 期已实现成交），初值 100.0
    loss_streak   : int      # 该方向连亏笔数

ActualTrade = Layer0 单笔字段 + {
    direction        : "long" | "short",
    taken            : bool,      # 是否真实成交（FLAT 期的 Layer0 信号 taken=False）
    exit_reason      : ... | "pnl_clear",   # 新增第4类：被 PnL 反馈强制清仓
    feedback_state   : "active" | "flat",
}

GateTimeline[i] = { long: active/flat, short: active/flat }   # 逐 bar，供显示与实时
```

## 4. 反馈规则（单方向，两方向各跑一份）

设 `shadow[i]` = 该方向理论曲线值（`long_pnl[i]` 或 `short_pnl[i]`），`dd = 1 - shadow[i]/shadow_peak`。

### 4.1 清仓触发（ACTIVE → FLAT，满足任一）
- **T1 理论回撤过大**：`dd > DD_CLEAR`（含浮亏，持仓中也可触发 → 立即平掉当前仓）
- **T2 连亏**：`loss_streak >= LOSS_K`（可选，默认开）

### 4.2 恢复触发（FLAT → ACTIVE）
- **R1 回撤回落**：`dd < DD_RECOVER`，其中 `DD_RECOVER < DD_CLEAR` 构成**迟滞带**，防止在阈值线附近反复抖动。恢复时 `loss_streak` 清零。

### 4.3 清仓动作
- 若触发清仓时**正持仓** → 在当前 bar 以 `filtered[i]` 平仓，记 `exit_reason="pnl_clear"`，结算该笔（计入 `actual_equity` 与 `loss_streak`）。
- 之后进入 FLAT：**不接该方向任何新仓**，直到 R1 恢复。

## 5. 完整算法（逐 bar / 单方向；两方向独立各一份）

```
init: active=True, holding=False, shadow_peak=100.0, actual_equity=100.0, loss_streak=0

for i in range(n):
    shadow_peak = max(shadow_peak, shadow[i])
    dd = 1 - shadow[i] / shadow_peak                      # 理论回撤（含未实现）

    # ── (a) 先更新 gate 状态 ──
    if active:
        if dd > DD_CLEAR or loss_streak >= LOSS_K:        # 触发清仓
            if holding:
                平仓@i, exit_reason="pnl_clear"
                r = 该笔实际收益; actual_equity *= (1+r)
                loss_streak = loss_streak+1 if r < 0 else 0
                holding = False
            active = False
    else:                                                 # FLAT
        if dd < DD_RECOVER:                               # 恢复
            active = True; loss_streak = 0

    # ── (b) 再按 Layer0 事件执行持仓动作（受 gate 门控）──
    if active:
        if Layer0 该方向在 i 有入场 and not holding:
            开仓@i (entry=filtered[i]); holding = True
        elif holding and Layer0 该方向在 i 有离场:         # take_profit/stop_loss/eod
            平仓@i, exit_reason=Layer0原因
            r = 该笔实际收益; actual_equity *= (1+r)
            loss_streak = loss_streak+1 if r < 0 else 0
            holding = False
    # FLAT 期：Layer0 的入场信号被忽略（taken=False），无新仓

    # ── (c) 记录逐 bar 实际权益（持仓期叠加未实现，与 long_pnl 同构）──
    actual_curve[i] = actual_equity * (1 + 未实现@i) if holding else actual_equity
    gate_timeline[i] = active
```

> 两方向差异仅在 `shadow`、入/离场事件、收益公式（多 `(exit-entry)/entry`、空 `(entry-exit)/entry`）——完全复用 Layer0 已有算术。

## 6. 与 Layer 0 的接口契约

| 方向 | 内容 |
|---|---|
| **输入** | `long_pnl[]` / `short_pnl[]`（逐 bar，含未实现）；Layer0 每方向的逐笔入场/离场事件（可从 `trade_records` 的 `entry_idx/exit_idx/type/exit_reason` 还原） |
| **输出** | `actual_long_curve[]` / `actual_short_curve[]`；`actual_trades[]`（含 `taken`、`feedback_state`、`pnl_clear`）；`gate_timeline[]` |
| **约束** | 不修改 Layer 0 任一函数；Layer 1 是纯后处理 overlay |

## 7. 回测 / 实时统一

同一个 `DirGate` 状态机，**只有驱动方式不同**：
- **回测**：`for i in range(n)` 遍历历史，一次跑完。
- **实时**：每来一根新 bar 调用一次状态转移；gate 状态跨帧持久化。**当前"半边多空对"所处方向的 gate = 此刻该不该持仓的答案。**

这对应第 1 点"实时交易和回测一套方案"——无需两套逻辑。

## 8. 显示层（PnL 下方面板）

```
┌─ 现有 PnL 图（不改）：long_pnl(绿) / short_pnl(红) 双曲线 ──────────┐
├─ 新增「实际多空对过程」面板 ───────────────────────────────────────┤
│  ▸ 持仓带：  ▓▓绿▓▓        ▓红▓      ▓▓绿▓▓        （空白=清仓）    │
│  ▸ 标记：    ▲恢复   ▼清仓(pnl_clear/连亏)                          │
│  ▸ 实际权益：actual_long_curve / actual_short_curve（可与理论叠加对比）│
└────────────────────────────────────────────────────────────────────┘
```
> **落点待定**：文档引用的 PnL 显示层 `streamlit_app.py` 不在仓库根目录，需先定位其真实位置，再决定这块面板如何挂到 PnL 下方。

## 9. 超参与零回归基线

| 超参 | 含义 | 默认（示意，待回测标定） |
|---|---|---|
| `DD_CLEAR` | 理论回撤触发清仓 | 8% |
| `DD_RECOVER` | 理论回撤回落恢复（须 < DD_CLEAR，构成迟滞） | 3% |
| `LOSS_K` | 连亏清仓笔数（可选） | 3 |

**零回归基线**：令 `DD_CLEAR=∞`、`LOSS_K=∞`（永不触发清仓），则 gate 恒为 ACTIVE，`actual_curve ≡ long_pnl/short_pnl`（满仓），**逐字节复现 Layer 0**。这是第一道验证——先确认 overlay 无侵入，再逐步收紧阈值，对比"实际 vs 理论"的胜率/回撤/Sharpe，量化 PnL 反馈的边际贡献。

## 10. 边界与不变量

- 多空两 gate **完全独立**，同帧可一多一空、一仓一清。
- gate **严格二值**：无中间仓位；`actual_equity` 只在 ACTIVE 期成交时复利。
- 迟滞带 `DD_RECOVER < DD_CLEAR` 保证不在阈值线抖动。
- `pnl_clear` 与 Layer0 的 `take_profit/stop_loss/eod` **并列为第 4 类 `exit_reason`**，可追溯。
- FLAT 期该方向零成交（`taken=False`），实际权益水平冻结。
- NaN/非法价沿用 Layer0 的跳过规则，不新增数值风险。

## 11. 待定项与下一步

1. `LOSS_K`（连亏触发）默认开还是关？与回撤 T1 是"或"关系，可能过于敏感——建议先只开 T1，回测后再决定是否加 T2。
2. 恢复判据除 R1（回撤回落带）外，是否需要"固定冷却期"兜底（避免长期困在 FLAT）？
3. 显示层 `streamlit_app.py` 真实位置定位后，敲定面板挂载方式。
4. 超参默认值需回测标定，当前 8%/3%/3 仅为示意。

**未来可叠加（本版本不做）**：确认纯 PnL 反馈有效后，可把 v3 的 C 周期背书作为**正交插件**，在第 5 步 (b) 开仓前多一道方向门（`C=MISALIGN/C_ENDED → 不开`），与本 gate 相乘，不改本设计骨架。

---

> 变更历史：v0 = 骨架（含减仓/size_mult，已被本版二值模型取代）；v1 = 完整设计（二值 + 多空分离 + 纯 PnL 反馈，本文件）。
> **实现落地（2026-07-11）：门控方案整体废弃，改为纯持仓状态显示——见顶部 SUPERSEDED 说明。**
