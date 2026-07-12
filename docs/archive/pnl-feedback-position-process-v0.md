# PnL 反馈驱动持仓过程 —— v0 骨架

> **状态：初步骨架（skeleton），供讨论迭代，非最终设计。**
> 目标：在现有 PnL 显示（`_compute_strategy_pnl` 产出的 long_pnl/short_pnl/trade_records）**下方**，
> 新增一层「根据 PnL 处理结果决定实际持仓」的过程。现有 PnL 层不改动。
>
> 本文档配合两点约束：①实时交易与回测共用一套方案；②刻意**避开** v3/v4 未落地的重型机制
> （C 五态 / N_confirm / gap_count / 双阈值 / entry_void）——那部分作为**可选粒度2**单列，默认不启用。

---

## 1. 定位：在 PnL「下方」加一层

```
Layer 0（现有，不改）：信号 → pairs → _compute_strategy_pnl
        产出 long_pnl / short_pnl / trade_records        ← 现有显示层，"没问题"、保留
                              │
                              ▼   ← 新增这一层（"下方"）
Layer 1（新增）：PnL 反馈持仓管理器
        输入：Layer0 的逐笔/逐帧 PnL 结果
        输出：实际持仓决策（开/平/加/减）+ 实际权益曲线
```

## 2. 关键设计：理论 PnL 当「影子」

- **Layer 0 的 PnL = 理论满仓、永不停**（每个 pair 都按单位仓位交易）。这是现有、要保留的那层。
- **Layer 1 = 实际持仓**，受反馈调制（可减仓、可暂停开新仓）。
- 妙处：**暂停期间，理论 PnL 继续当"纸上交易"跑**，用它的近况判断何时恢复实盘。
  现有 `long_pnl/short_pnl` 直接复用成恢复信号，不用另造。

## 3. 数据结构骨架

```
FeedbackState:                      # 逐笔/逐帧演进
    actual_equity   : float         # 实际权益（施加仓位后）
    peak_equity     : float
    drawdown        : float         # = 1 - actual_equity/peak_equity
    loss_streak     : int           # 连亏笔数
    size_mult       : float ∈ [0,1] # 当前仓位系数
    mode            : OPEN | THROTTLE | HALT   # 满仓 / 减仓 / 只平不开

ActualTrade  = trade_records 每笔 + { size_mult, actual_return_pct, feedback_reason }
```

## 4. 反馈状态机（核心，伪代码）

```
def step_feedback(state, raw_return, shadow_pnl, cfg):
    size = state.size_mult                       # 本笔仓位由"历史表现"决定
    if state.mode == HALT:                        # 暂停期：本笔不实盘
        if shadow_recovered(shadow_pnl, cfg):     # 影子PnL回暖 → 恢复
            state.mode, state.size_mult = OPEN, 1.0
        return state, 0.0, "halted"

    state.actual_equity *= (1 + raw_return * size)          # 结算实际贡献
    state.peak_equity    = max(state.peak_equity, state.actual_equity)
    state.drawdown       = 1 - state.actual_equity/state.peak_equity
    state.loss_streak    = state.loss_streak+1 if raw_return < 0 else 0

    if   state.drawdown > cfg.DD_HALT:                       # 回撤过大 → 暂停
        state.mode, state.size_mult = HALT, 0.0
    elif state.drawdown > cfg.DD_WARN or state.loss_streak >= cfg.LOSS_K:
        state.mode, state.size_mult = THROTTLE, cfg.THROTTLE_MULT   # 如 0.5
    else:
        state.mode, state.size_mult = OPEN, 1.0
    return state, raw_return*size, ("full" if size == 1 else "throttled")
```

## 5. 主流程（回测 / 实时同一套）

```
state = FeedbackState(actual_equity=100, peak=100, size_mult=1.0, mode=OPEN)
for pair in Layer0 逐个成交:                    # 回测=遍历历史; 实时=逐帧喂当前半边
    raw_trade = Layer0 单笔结果(pair)            # 复用现有入场/离场逻辑，不改
    state, actual_ret, reason = step_feedback(state, raw_trade.return, shadow, cfg)
    记录 ActualTrade + 更新"实际权益曲线"（画在 PnL 下方）
```

> **回测/实时统一**：状态机完全相同，只有驱动不同（回放 vs 实时喂帧）。对应"实时交易和回测一套方案"。

## 6. 两种粒度对比

| 维度 | **粒度1 · 极简版（纯 PnL 反馈）** | **粒度2 · 含 C 周期背书版** |
|---|---|---|
| 依赖 | 只用现有 `trade_records`/PnL | + 需实现 C 五态、双阈值、上级周期拟合 |
| 新增超参 | 4 个：`DD_WARN`/`DD_HALT`/`LOSS_K`/`THROTTLE_MULT` | 上面 + `WARN/MAX_DEV_PCT_C`、`N_confirm`、`c_ended_reduce_frac`… |
| 开仓门控 | 仅反馈状态（mode） | 反馈状态 **×** C 五态门（`size *= C门系数`） |
| 落地成本 | 低（一个状态机） | 高（v3 全套未落地机制） |
| 过度设计风险 | 低 | 主要风险都在这 |
| 适用时机 | 先验证"PnL 反馈本身有没有用" | 确认反馈有效后，再叠加上级过滤 |

粒度2 只是在第 4 步开仓前多一道门：
`if C_state in (MISALIGN, C_ENDED, NO_DIR): 不开; WEAK: size*=weak_frac`，离场再叠加 C 梯度。
**它与极简版正交**——极简版是骨架，C 门是可后加的插件。

## 7. 建议

**先落极简版（粒度1）。** 用最少机器闭合 PnL 反馈环，回测/实时天然统一，
且能在付出 C 周期成本**之前**先验证反馈是否有用。C 门以后作为正交插件叠加即可。

## 8. 待定项（下一轮细化）

1. 反馈调制的是**仓位大小（连续减仓）**，还是**开关（满仓/半仓/停三档）**？当前骨架给的是三档折中。
2. `shadow_recovered()` 的恢复判据：理论权益新高 / 连续 N 笔理论盈利 / 固定冷却期？
3. 显示层落点：现有 PnL 显示层（文档引用的 `streamlit_app.py`）真实位置待定位后，再定"下方面板"如何挂载。
4. 多空是否分开反馈（long/short 各一个 FeedbackState），还是合并成一条实际权益？

---

> 变更历史：v0 = 初始骨架（本文件）。后续迭代在同分支追加提交。
