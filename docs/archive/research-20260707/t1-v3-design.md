# 半边多空对交易策略（v3：补上 C_ENDED 等价结束状态）

> 修正 v2 的漏洞——C 的多空对也会因偏离过大而**提前离场**，这和 Sig 反向结束效果等价。v2 把它错判成"仍在场内的 WEAK_ALIGN"，v3 把它升级为独立的第 5 态 **C_ENDED**。

---

## 0. 一句话修正摘要

**用户纠正（原话）：** "入场决策表少考虑了 C 的离场状态，C 的实际操作有一些情况走不完到完整的多空对，会因为偏离过大而提前结束。但提前结束和多空对结束效果是一样的。"

**问题定位：** v2 的破位预警 `deviation_warning`（`signed_dev > MAX_DEV_PCT_C`）本意是"C 离场②已触发"，却被归进 **WEAK_ALIGN（仍持仓、谨慎）**。这是自相矛盾的——离场②触发意味着 **C 已按自己的趋势偏离规则平仓离场了**，它不再是"仍在场内的弱同向"，而是"多空对已提前结束"。v2 只承认"Sig 反向"这一种结束（→ MISALIGN），漏了"偏离提前离场"这一种等价结束。

**v3 修正：** 把 `signed_dev > MAX_DEV_PCT_C` 从 WEAK_ALIGN 中剥离，独立成新态 **C_ENDED**。WEAK_ALIGN 的偏离维度降级为"接近但未超阈"的预警带。C 状态从 4 态升到 5 态。

---

## 1. "C 多空对结束"的完整定义

C 周期和 B 一样在操作半边多空对，也跑同一套三离场（止损 / 趋势偏离② / Sig 反转①）。因此 **C 的当前半边有两种等价的"结束"方式**，对 B 的意义完全一样：**C 这段趋势结束了，背书没了。**

| 结束方式 | 触发离场 | dir_C 是否翻转 | C 仓位 | v2 是否覆盖 |
|---------|---------|--------------|--------|-----------|
| **正常结束** | 离场①：Sig 反向 `reverse_run ≥ N_confirm` | ✅ 翻转到 -d_B | 已平仓 | ✅ → MISALIGN |
| **提前结束** | 离场②：偏离过大 `signed_dev > MAX_DEV_PCT_C` | ❌ 未翻（仍名义同向） | **已平仓** | ❌ **被 v2 漏掉** |

C 半边完整生命周期状态机：

```
   健康持仓                衰竭预警（仍持仓）             结束（两种等价，对 B 都是背书消失）
   STRONG_ALIGN  ───────▶  WEAK_ALIGN        ───────▶  ┌─ 反向结束 MISALIGN（dir_C 翻转，C 反手做空）
   dir_C=d_B 满速          · 反转苗头未达 N_confirm      │  = C 已反向，战略否定 B
   无预警、未离场          · 偏离接近但未超 MAX_DEV_C     └─ 提前离场 C_ENDED（dir_C 未翻，C 已平仓）⭐新
                          · [软] Sig 降回 0               = C 背书撤销，但未反手
```

**核心洞察：** "反向结束（MISALIGN）"与"偏离提前离场（C_ENDED）"都是"C 多空对结束"，对 B 都意味着**背书消失**。区别只在于结束后 C 站在哪里——MISALIGN 是 C **反手站到了 B 对面**，C_ENDED 是 C **退场观望、既不背书也不反对**。这个区别决定了它们对 B 入场/离场的处理强度不同（见第 4、5 部分）。

---

## 2. C 状态模型升级：4 → 5 态

| 状态 | 精确定义 | 检测条件 | 对 B 入场意义 |
|------|---------|---------|-------------|
| **STRONG_ALIGN** | dir_C==d_B，健康奔跑，无预警、未离场 | 无任何预警且 `signed_dev ≤ WARN_DEV_PCT_C` | ✅ 入场（满仓） |
| **WEAK_ALIGN** | dir_C==d_B，**仍持仓**，露出衰竭前兆但离场②未触发 | `0<reverse_run<N_confirm` **或** `WARN_DEV_PCT_C ≤ signed_dev ≤ MAX_DEV_PCT_C`（**未超阈**）**或** [软] Sig→0 | ⚠️ 降级/减仓/不入 |
| **C_ENDED** ⭐新 | dir_C==d_B（名义同向）但 **C 离场②已触发、C 已平仓**（多空对提前结束，dir_C 未翻） | `signed_dev > MAX_DEV_PCT_C`（**已超阈**） | ❌ 不入场（背书已撤销） |
| **MISALIGN** | dir_C == -d_B，C 反向结束、已反手 | `reverse_run ≥ N_confirm`（dir_C 已翻） | ❌ 不入场（逆 C 大势） |
| **NO_DIRECTION** | dir_C == 0，C 无活跃非零半边 | 无当前非零合并段 | ❌ 不入场（无背书） |

**只有 STRONG_ALIGN 打开入场。** C_ENDED、MISALIGN、NO_DIRECTION 明确不入；WEAK_ALIGN 是可配灰区。新增的 C_ENDED 插在 WEAK_ALIGN（未离场）和 MISALIGN（反向离场）之间，正好补上"同向离场"这个缺口。

---

## 3. ⭐重点：WEAK_ALIGN vs C_ENDED 的边界（本文核心）

这是 v3 最关键的一刀。两态都满足 `dir_C == d_B`（名义同向、Sig 未翻），**唯一的分界线是 C 的趋势偏离 `signed_dev` 有没有超过 C 的离场②阈值 `MAX_DEV_PCT_C`**：

```
signed_dev（C 当前半边不利方向偏离）
       │
       │   pred_val_C = C 半边预测轨道值（_fit_physics_parabola）
       │   signed_dev = (dir_C=+1) (pred_val_C - price_C)/pred_val_C
       │             = (dir_C=-1) (price_C - pred_val_C)/pred_val_C
       │
  0 ───┼───────────────┬────────────────────────┬──────────────▶ signed_dev
       │               │                        │
       │  STRONG 区     │  WEAK 预警带            │  C_ENDED 区
       │  健康          │  接近但未离场            │  离场②已触发、C 已平仓
       │               │                        │
       └───────────────┴────────────────────────┴──────────────
     signed_dev ≤              WARN_DEV_PCT_C ≤       signed_dev >
     WARN_DEV_PCT_C            signed_dev ≤            MAX_DEV_PCT_C
                              MAX_DEV_PCT_C
```

| 维度 | WEAK_ALIGN | C_ENDED ⭐ |
|------|-----------|-----------|
| dir_C | == d_B（同向） | == d_B（名义同向） |
| C 仓位 | **仍持仓** | **已平仓**（离场②触发） |
| 偏离量 | `WARN_DEV_PCT_C ≤ dev ≤ MAX_DEV_PCT_C`（未超阈） | `dev > MAX_DEV_PCT_C`（已超阈） |
| C 多空对 | 未结束（尾部衰竭中） | **已提前结束** |
| 语义 | "C 可能要走弱了" | "C 这段趋势已经断了" |
| 对 B | 尾部风险高，谨慎 | 背书已消失，等同结束 |

**为什么边界严丝合缝、不重叠？**

1. **偏离维度：** 阈值 `MAX_DEV_PCT_C` 是 C 离场②的触发线。`dev ≤ MAX` 时 C 离场②**未触发、C 仍持仓** → 顶多是 WEAK 预警；`dev > MAX` 时 C 离场②**已触发、C 已平仓** → 必然是 C_ENDED。一条线两侧，物理上互斥。v2 的错误正是把 `dev > MAX`（离场已触发）也塞进 WEAK（仍持仓），逻辑自相矛盾；v3 用 `MAX_DEV_PCT_C` 把它切开，`WARN_DEV_PCT_C` 只是在 MAX 之下再划出一条预警下界（如 `WARN=0.7×MAX`），让 STRONG 和 WEAK 之间也有过渡带。

2. **反转维度：** `0<reverse_run<N_confirm`（苗头未确认）→ WEAK；`reverse_run ≥ N_confirm`（确认反向、dir_C 翻转）→ MISALIGN。这条边界 v2 已处理好，v3 不动。

3. **三态终局对齐：** WEAK 是"未结束"的唯一中间态；它有两条出口——偏离方向越过 `MAX_DEV_PCT_C` 走向 **C_ENDED**，反转方向越过 `N_confirm` 走向 **MISALIGN**。两个终局各占一维阈值，互不干涉。

判定伪代码（C_ENDED 必须先于 WEAK 检查，因为它是终局态）：

```python
def _c_pair_state(sig_C, price_C, fit_C, i_C, d_B,
                  N_confirm, MAX_DEV_PCT_C, WARN_DEV_PCT_C, use_decel=False):
    dir_C = _find_current_half_pair(sig_C, N_confirm).dir   # 反向尾达 N_confirm 已归入新半边
    if dir_C == 0:
        return NO_DIRECTION
    if dir_C == -d_B:
        return MISALIGN                     # 反向结束（离场①），dir_C 已翻

    # dir_C == d_B：名义同向，进一步分 STRONG / WEAK / C_ENDED
    signed_dev = _signed_deviation(price_C[i_C], fit_C, i_C, dir_C)  # 复用 B 离场②逻辑

    # ① 先判终局：偏离提前离场（离场②已触发、C 已平仓）
    if signed_dev > MAX_DEV_PCT_C:
        return C_ENDED                      # ⭐ v3 新增：等价结束

    # ② 再判中间态：仍持仓、衰竭预警
    reverse_run   = trailing_run(sig_C, i_C, sign=-dir_C)
    reversal_warn = 0 < reverse_run < N_confirm
    dev_warn      = WARN_DEV_PCT_C <= signed_dev <= MAX_DEV_PCT_C   # 接近但未超
    decel_warn    = use_decel and (sig_C[i_C] == 0
                                   and last_nonzero_sign(sig_C, i_C) == dir_C)
    if reversal_warn or dev_warn or decel_warn:
        return WEAK_ALIGN

    return STRONG_ALIGN                     # 健康奔跑
```

一句话记住边界：**未超阈 = WEAK 预警（C 还在场内挣扎）；已超阈 = C_ENDED 离场（C 已经出场了）。**

---

## 4. 入场决策表（v3，5 态）

**B 半边方向（当帧起点）× C 多空对状态（5 态）→ 入场/降级/不入场。**

| # | B 起点当帧 | C 多空对状态 | 决策 | 原因 |
|---|-----------|-------------|------|------|
| 1 | 是 | STRONG_ALIGN | ✅ 入场（满仓） | C 同向健康奔跑，最可靠 |
| 2 | 是 | WEAK_ALIGN | ⚠️ 降级：`allow_weak` 开→半仓+紧离场；关→不入 | C 同向但衰竭中，尾部进场高风险 |
| 3 | 是 | **C_ENDED** ⭐ | ❌ 不入场 | **C 多空对已提前离场，背书已撤销**（等同结束，无背书可依） |
| 4 | 是 | MISALIGN | ❌ 不入场 | C 已反向结束、反手做空，逆大势 |
| 5 | 是 | NO_DIRECTION | ❌ 不入场（默认保守） | C 无背书，B 单边假信号裸奔 |
| 6 | 否（非起点帧） | 任意 | — 不判入场 | 仅起点当帧判入场 |

**与 v2 决策表差异：** 新增第 3 行 C_ENDED → 不入场。它和 MISALIGN 一样都拒绝入场，但语义不同——**C_ENDED 是"背书消失"（C 退场观望），MISALIGN 是"背书反转"（C 反手对立）**。都不入场，因为入场的前提是"C 有活跃同向半边在背书"，而 C_ENDED 下 C 的同向半边已经提前结束了。

---

## 5. 离场逻辑调整：C_ENDED vs MISALIGN 差异化

**问题：** v2 离场③只有 C=MISALIGN 触发 B 强退。C_ENDED（C 提前离场、背书消失）是否也该触发 B 离场或收紧？

**分析：** C_ENDED 意味着 C 的背书撤销了，B 正是靠 C 背书才入场的，背书没了 B 的持仓依据被削弱。但 C_ENDED **C 并未反手对立**（dir_C 仍名义同向），B 自己的本级趋势可能仍然有效。因此不宜像 MISALIGN 那样"战略否定、立即强退"，而应"收紧防线、择机减仓"——强度介于 WEAK（轻收紧）和 MISALIGN（强退）之间。

**建议：按 C 生命周期给梯度化响应（越接近结束，B 越保守）：**

| C 状态 | B 持仓响应 | 强度 |
|--------|-----------|------|
| STRONG_ALIGN | 继续持有 | — |
| WEAK_ALIGN | 只收紧其余阈值（可选 `MAX_DEV_PCT_B` 减半 / 提前止盈），不减仓 | 轻 |
| **C_ENDED** ⭐ | **B 收紧 + 减仓**：`MAX_DEV_PCT_B` 减半、拉紧止盈；可选减半仓落袋，把剩余仓交给本级①②兜底 | 中 |
| MISALIGN | **立即全额强退**（离场③，最高优先级，战略否定） | 强 |

升级后离场优先级（if-elif）：

```
优1   C 状态 == MISALIGN            → 🚪 立即全额离场（离场③，战略否定）
优1.5 C 状态 == C_ENDED  ⭐新       → ⚠️ 收紧 B 阈值 + 减仓（背书撤销，非强退）
优2   边缘窗口内 B 起点蒸发          → 🚪 entry_void 快速小损离场
优3   B 不利偏离 > MAX_DEV_PCT_B     → 🚪 离场②（趋势偏离）
优4   B 反向段 ≥ N_confirm          → 🚪 离场①（进入下一多空对）
(保留) 预测保护期击穿轨道            → 🚪 止损
否则                                → 继续持有
```

要点：**MISALIGN 强退、C_ENDED 收紧减仓**——两者都源于"C 多空对结束"，但 MISALIGN 是 C 反手站到对面（危险，全退），C_ENDED 是 C 退场观望（背书没了但不对立，减仓+收紧让本级趋势自行验证）。这样既补上了 C_ENDED 的响应，又不因为"C 只是退场没反手"就把 B 正常持仓一刀切掉。

---

## 6. 集成与改动范围

**"C 提前离场"如何计算：** 对 C 复用 B 的离场②（趋势偏离）逻辑即可，无需新算法——

```
1. C 周期已跑 _schmitt_trigger 得 sig_C、price_C（v2 已有）
2. 对 C 当前半边跑 _fit_physics_parabola 得预测轨道 fit_C（v2 已有，供破位预警）
3. 逐帧算 signed_dev（C 当前价 vs fit_C，仅取不利方向）—— 与 B 离场② 同一函数
4. signed_dev > MAX_DEV_PCT_C  → 判定 C_ENDED（C 离场②已触发、已平仓）
   WARN_DEV_PCT_C ≤ dev ≤ MAX  → 判定 WEAK 破位预警（未离场）
```

**关键事实：** v2 其实**已经算了这个 `signed_dev`**（在 `deviation_warning` 检测器里），只是把 `dev > MAX` 错误地归进了 WEAK。**v3 的代码改动因此极小**——不是新增计算，而是把同一个 `signed_dev` 按两条阈值线重新分流。

**改动范围（增量 + 默认关闭，回测零回归）：**

| 改动 | 位置 | 说明 |
|------|------|------|
| **改** | `_c_pair_state(...)` | 加 `C_ENDED` 枚举；把 `signed_dev > MAX_DEV_PCT_C` 从 WEAK 剥离为 C_ENDED；WEAK 的偏离维度改为 `WARN_DEV_PCT_C ≤ dev ≤ MAX_DEV_PCT_C` 预警带（**主改动**） |
| **改** | 入场支路 | 决策表加 C_ENDED → 不入场（与 MISALIGN 同为拒绝，语义分开） |
| **改** | 离场支路 | 离场③拆两支：MISALIGN → 强退；C_ENDED → 收紧 `MAX_DEV_PCT_B` + 减仓（新优1.5） |
| **新增超参** | `WARN_DEV_PCT_C` | WEAK 破位预警下界（默认 `0.7×MAX_DEV_PCT_C`）；`c_ended_reduce_frac`（C_ENDED 减仓比例，默认 0.5）；`allow_weak_entry` 沿用 |
| **不改** | `_fit_physics_parabola` / `_schmitt_trigger` / `_signed_deviation` | 完全复用，C_ENDED 不引入任何新数学 |

**向后兼容：** 令 `WARN_DEV_PCT_C = MAX_DEV_PCT_C` 且关闭 C_ENDED 分支时，WEAK 预警带塌缩、C_ENDED 永不触发，系统逐字节复现 v2 行为——**回测基线零回归是第一道验证**。随后逐步下调 `WARN_DEV_PCT_C`、打开 C_ENDED，对比"v3 vs v2 vs 基线"的胜率/交易次数/最大回撤/Sharpe，量化补上 C_ENDED 的边际贡献。
