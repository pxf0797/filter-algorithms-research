# 半边多空对策略 · 升级版设计（无锁定入场 + C 周期多空对状态模型）

> 核心改动：**B 周期起点一出现即判入场（不等锁定）**，用 **C 周期"多空对状态"**（而非 C.Sig 瞬时值）过滤假信号；新增"入场依据消失"快速离场兜底不锁定风险。

---

## 〇、代码前提（读码确认）

- `_schmitt_trigger` 输出 `sig`（+1/0/-1）与 `dur`（当前状态已持续根数）。**关键状态机事实：Sig 永不直接 +1↔-1，必先经过 0（观望）。** 因为 +1→0 的条件是 `a<-ε`（加速度掉头），-1 的建立还要再等 `a<-ε AND v<0`。这个天然的"先降速到 0、再反向"两段式，正是本设计"中间态"的物理基础。
- `_find_all_pairs` 只返回**完整多空对**（配对循环 `range(len(merged)-1)` 止于倒数第二段），最右半边永远进不了列表 → 需新增"当前半边"识别。
- `_compute_strategy_pnl` 现离场：预测保护期内止损 + 全程 Sig 完全反转（`sig==-1`/`+1`）止盈。**离场只认"完全反转"，不认降速到 0** —— 升级版要把"降速到 0 / 反向未确认"显式变成一个可观测的中间态。
- `_align_pnl_to_current_tf` 提供跨周期时间戳前向对齐基建，取 dir_C / C 状态复用它。

---

## 一、新入场逻辑（无锁定）

### 1.1 时机：起点那一帧即判

废弃"等起点离开边缘区（锁定）"这一前提。**B 半边起点一诞生（`sig_B` 0→±1，即当前半边 `pair_start == i`）当帧就判入场。** 理由：等锁定要 3-5 根 K 线，趋势往往已走一大段，进场太晚、性价比差。

### 1.2 条件：C 多空对状态背书替代"等锁定"

入场唯一门槛从"起点锁定"换成"C 多空对状态 = 强同向"：

```
入场 IF:
    B 当前半边起点当帧诞生 (pair_start_B == i)         # 时机
    AND C_pair_state(dir_B, i) == STRONG_ALIGN         # 背书（默认）
    [可配置 allow_weak: WEAK_ALIGN 时降级入场/减仓]
```

- **STRONG_ALIGN** 是打开入场的钥匙（详见第二部分）。
- 可配置开关 `allow_weak_entry`：允许 WEAK_ALIGN 时降级入场（半仓 / 更紧离场），默认关闭（保守不入）。

### 1.3 不锁定的假起点风险 → 双重兜底

不等锁定 = 起点可能是边缘区假信号（下一帧可能蒸发）。风险不再由"等锁定"消化，改由两层兜底：

1. **入场时：C 同向确认**——B 单边边缘假信号与"C 已有同向活跃半边"同时出现的概率远低于 B 单独假信号，C 背书把假信号概率大幅压低。
2. **入场后：入场依据消失快速离场**（详见第四部分 4.3）——若起点在边缘窗口内蒸发，立刻小损离场，把假起点的伤害限制在几根 bar。

### 1.4 新旧入场逻辑对比

| 维度 | 旧版（锁定） | 新版（无锁定） |
|------|------------|--------------|
| 入场时机 | 起点离开边缘区后（延迟 3-5 bar） | 起点诞生当帧（0 延迟） |
| 假信号防线 | 等锁定（时间成本换确定性） | C 强同向确认 + 事后快速离场 |
| 进场点位 | 趋势已走一段（可能已涨/跌很多） | 趋势起点（性价比最高） |
| 上级过滤单元 | C.Sig 或 dir_C（瞬时/粗糙） | **C 多空对状态**（含中间态，4 态） |
| 假起点残留风险 | 天然免疫（消失的起点等不到锁定） | 由快速离场兜底（限损几根 bar） |

---

## 二、C 周期多空对状态模型（核心）

C 周期不是被动提供一个方向，而是**自己也在操作半边多空对**——它同样有起点、有活跃半边、也会给自己的离场信号。B 要判断的是 C 这个多空对**整体处于什么阶段**，而不是 C 某一帧的 Sig。

### 2.1 为什么要建状态模型：C.Sig 太粗糙

- C.Sig 是瞬时值，会在 0 和 ±1 间抖动，用它判同向会被单帧噪声反复误导。
- 更致命的是它**看不见"C 趋势即将衰竭"这个阶段**：C 的 +1 半边已经降速（Sig 掉到 0）或已冒出反向苗头（Sig 短暂 -1 但未确认），此时 C 这段趋势正在尾部，B 若此刻入场 = 在 C 趋势末端上车，是最危险的进场。C.Sig 无法表达"活跃但衰竭中"这个中间层。

### 2.2 C 的三种离场信号（复用本级三离场）

C 判定自身半边多空对是否走向结束，复用与 B 相同的三种离场机制：

| C 的离场机制 | 触发 | 对"C 多空对是否结束"的含义 |
|-------------|------|--------------------------|
| ① 进入下一多空对 | C 反向段持续 ≥ N_confirm | **已结束**：dir_C 翻转，产生新半边 |
| ② 趋势偏离过大 | C 价格不利偏离预测轨道 > MAX_DEV | **未结束但破位**：Sig 未反转，轨道已脱 |
| ③ 上级（D）反向 | D 周期掉头 | 本设计止于 C，可选接入，不作 B 判据 |

### 2.3 中间态检测："离场信号已给但多空对未结束"（重点）

这是整个模型的核心。C 的 +1 半边尚未正式结束（dir_C 仍 = +1），但已经露出结束前兆。两个严格可量化的检测器：

**检测器 A — 反转预警（reversal_warning）：C 反向 Sig 已现但未达 N_confirm**

```
reverse_run = 末尾连续 sign == -dir_C 的 bar 数（截至 i_C）
IF 0 < reverse_run < N_confirm:
    reversal_warning = True     # 反向苗头已冒，但 dir_C 尚未翻（未结束）
```

> 注：一旦 `reverse_run >= N_confirm`，C 的离场①触发、dir_C 翻转 → 已经是 MISALIGN，不再属于中间态。所以中间态里 reverse_run 恒 < N_confirm。

**检测器 B — 破位预警（deviation_warning）：C 趋势偏离已触发**

```
pred_val_C(i_C) = C 半边预测轨道值（_fit_physics_parabola）
signed_dev = 不利方向偏离 = (dir_C=+1 时) (pred_val - price)/pred_val
                          (dir_C=-1 时) (price - pred_val)/pred_val
IF signed_dev > MAX_DEV_PCT_C:
    deviation_warning = True    # C 离场②已触发，但 Sig 未反转（未结束）
```

**软信号（可选）— 降速预警（decel_warning）：C.Sig 从 dir_C 掉回 0**

```
IF sig_C[i_C] == 0 AND last_nonzero_sign(C, i_C) == dir_C:
    decel_warning = True        # 加速度掉头(a<-ε)，趋势降速但未反向
```

三者取或即"C 已给离场信号但多空对未结束"：

```
c_exit_signal_given = reversal_warning OR deviation_warning [OR decel_warning]
```

### 2.4 四状态枚举定义

先取 C 当前半边方向 `dir_C`（当前活跃半边 = 最右侧已确认的合并非零段的符号；无活跃非零段则 dir_C=0）。相对 B 的方向 `d_B`：

| 状态 | 定义 | 语义 | 对 B 入场 |
|------|------|------|----------|
| **STRONG_ALIGN** | dir_C == d_B 且 `c_exit_signal_given == False` | C 有活跃同向半边、健康奔跑、未露衰竭 | ✅ 入场 |
| **WEAK_ALIGN** | dir_C == d_B 且 `c_exit_signal_given == True` | C 同向但已给离场信号、趋势尾部衰竭中（未正式结束） | ⚠️ 降级/减仓/不入（可配） |
| **MISALIGN** | dir_C == -d_B（C 已确认反向） | C 活跃半边方向相反，或 C 已翻向 | ❌ 不入场 |
| **NO_DIRECTION** | dir_C == 0（C 无活跃非零半边，观望中） | C 无背书，方向未表态 | ⚠️ 无背书，保守不入 |

### 2.5 C 状态判定伪代码

```python
def c_pair_state(sig_C, price_C, fit_C, i_C, d_B,
                 N_confirm=2, MAX_DEV_PCT_C=4.0, use_decel=False):
    # 1) 当前活跃半边方向 = 最右已确认合并非零段符号
    dir_C = current_half_pair_direction(sig_C, i_C)   # +1 / -1 / 0

    # 2) 无方向
    if dir_C == 0:
        return "NO_DIRECTION"

    # 3) 已确认反向（C 离场① 已把 dir_C 翻成 -d_B）
    if dir_C == -d_B:
        return "MISALIGN"

    # 4) dir_C == d_B → 再看 C 是否已给离场信号（中间态检测）
    # 4a 反转预警：反向 run 已冒但未确认（<N_confirm；≥N_confirm 已在步骤3变 MISALIGN）
    reverse_run = trailing_run(sig_C, i_C, target= -dir_C)
    reversal_warning = (0 < reverse_run < N_confirm)

    # 4b 破位预警：C 半边不利偏离超阈
    signed_dev = unfavorable_deviation(price_C, fit_C, i_C, dir_C)
    deviation_warning = (signed_dev > MAX_DEV_PCT_C)

    # 4c 降速预警（软，可关）：Sig 掉回 0
    decel_warning = use_decel and (sig_C[i_C] == 0)

    if reversal_warning or deviation_warning or decel_warning:
        return "WEAK_ALIGN"
    return "STRONG_ALIGN"
```

其中 `current_half_pair_direction` 复用 `_find_all_pairs` 的 Step1/Step2（收集非零段 + 跨零合并），取截至 `i_C` 最后一个合并段的符号；若合并段的反向尾巴已达 N_confirm 则该反向段成为当前半边（dir_C 已翻）。这保证"翻转确认"与"中间态"边界一致、不重叠。

---

## 三、升级版入场决策真值表

B 半边方向（当帧起点）× C 多空对状态 → 决策：

| # | B 起点当帧 | C 多空对状态 | 决策 | 原因 |
|---|-----------|-------------|------|------|
| 1 | 是 | STRONG_ALIGN | ✅ 入场（满仓） | C 同向健康奔跑，最可靠 |
| 2 | 是 | WEAK_ALIGN | ⚠️ 降级：`allow_weak` 开→半仓+紧离场；关→不入 | C 同向但已衰竭，在 C 尾部进场高风险 |
| 3 | 是 | MISALIGN | ❌ 不入场 | 逆 C 大势的反弹，不参与 |
| 4 | 是 | NO_DIRECTION | ❌/⚠️ 不入场（默认保守） | C 无背书，B 单边假信号风险裸奔 |
| 5 | 否（非起点帧） | 任意 | — 不判入场 | 仅在起点当帧判入场 |

只有第 1 行是无条件入场；第 2 行是可配灰区；3/4/5 均不入场。**注意与旧表差异：旧版第 4 行是"起点未锁定→等待"，新版取消该行——起点当帧就判，不存在"等锁定"。**

---

## 四、离场逻辑调整

### 4.1 三种离场是否仍适用

仍适用，且**因不锁定入场，离场更重要**（假起点风险后移到离场兜底）。优先级：

| 优先级 | 离场机制 | 触发（升级后） |
|--------|---------|---------------|
| 1（最高） | ③ 上级反向 | **C 多空对状态 → MISALIGN**（非 C.Sig 瞬时翻） |
| 2 | 入场依据消失（新增） | 边缘窗口内 B 起点蒸发（见 4.3） |
| 3 | ② 趋势偏离 | B 不利偏离 > MAX_DEV，全程 |
| 4 | ① 进入下一多空对 | B 反向段 ≥ N_confirm |
| （保留） | 预测保护期止损 | 入场初期击穿轨道 |

> 新增的"入场依据消失"放在优先级 2：它专治不锁定引入的假起点，越早识别越省损失，仅次于战略级的 C 反向。

### 4.2 离场③改用 C 多空对状态（非 C.Sig）

旧版：C.Sig 翻为反向即离场（瞬时、易被单帧噪声误触）。升级：

```
持仓期每帧重算 c_pair_state(dir_持仓, i):
    == MISALIGN     → 🚪 立即离场（离场③，最高优先级，战略否定）
    == WEAK_ALIGN   → ⚠️ 不强制离场，但收紧其余阈值（可选：MAX_DEV_PCT 减半 / 提前止盈）
    == STRONG_ALIGN → 继续持有
    == NO_DIRECTION → ⚠️ 背书消失，可选收紧（默认继续持有，靠 ①②兜底）
```

要点：**只有 MISALIGN（C 已确认反向）才触发离场③**。WEAK_ALIGN（C 衰竭中但未反）不强制离场，只做风险收紧——避免被 C 的中间态噪声频繁甩出正常持仓。这正是"用多空对状态而非 Sig"的价值：Sig 一抖就走会过度交易，状态模型能区分"真反向"与"衰竭波动"。

### 4.3 新增离场：入场依据消失（快速离场）

因不锁定入场，B 起点可能是边缘区假信号、几根 bar 后蒸发。为此新增"入场依据消失"快速离场，把假起点损失限制在边缘窗口内：

```
edge_window = 边缘区宽度（默认 3~5 bar，= 旧锁定所需根数）
入场后，对 i in [entry+1, entry+edge_window]:
    dir_now = current_half_pair_direction(sig_B, i)   # 重算当前半边方向
    IF dir_now != entry_dir:          # 起点方向不再是入场方向 = 起点蒸发
        exit_idx = i
        exit_reason = "entry_void"    # 依据消失，快速小损离场
        break
```

语义：入场时赌"这个起点是真的"，若边缘窗口内 B 当前半边方向已不是入场方向（Sig 塌回 0 后未复位、或直接冒反向），说明当初那个起点是边缘噪声，立即离场止损。与离场①（take_profit，正常趋势走完才反转）区分：`entry_void` 是**快速刮损**（趋势根本没成立），发生在入场后极短窗口内、损失极小。edge_window 外不再检查（此时半边已站稳，回归常规三离场）。

---

## 五、集成方案

### 5.1 新增 / 修改函数

| 改动 | 函数 | 说明 |
|------|------|------|
| **新增** | `_merge_segments(sig_t)` | 抽出 `_find_all_pairs` Step1/2（收集非零段+跨零合并），公共复用 |
| **新增** | `_find_current_half_pair(sig_t, N_confirm)` | 返回最右半边 `(pair_start, cur_idx, dir)`；反向尾达 N_confirm 则归入新半边 |
| **新增** | `_c_pair_state(sig_C, price_C, fit_C, i_C, d_B, ...)` | 按第二部分伪代码返回 4 态枚举 |
| **新增** | `_get_c_state_at(current_dates, c_dates, c_state_series, i)` | 复用 `_align_pnl_to_current_tf` 时间戳前向对齐，取当前帧对应 C 状态 |
| **改** | `_compute_strategy_pnl` | 加"起点当帧+C 强同向"入场支路；离场按新优先级（含 entry_void、C 状态版离场③）；全部参数默认关闭，旧行为逐字节复现 |
| **不改对外** | `_find_all_pairs` | 仅内部改调 `_merge_segments`，保护回测确定性 |

### 5.2 C 多空对状态如何获取

对 C 周期**也运行一遍半边识别 + 离场信号计算**（而不仅取 dir_C）：

1. C 周期同样跑 `_schmitt_trigger` 得 `sig_C`、`price_C(filtered_C)`。
2. 对 C 当前半边跑 `_fit_physics_parabola` 得预测轨道 `fit_C`（供破位预警）。
3. 逐帧算 `c_state_series[j] = _c_pair_state(...)`，得到 C 时间轴上的状态序列。
4. B 每帧 i 通过 `_get_c_state_at` 前向对齐到最近的 C bar，取该帧 C 状态用于入场门控（第三部分）与离场③（4.2）。

> C 的 `MAX_DEV_PCT_C`、`N_confirm` 是独立超参，可与 B 的分开调。全部新支路走"增量 + 默认关闭"，关掉开关 → 回测基线零回归（第一道验证）。

### 5.3 关键超参

| 超参 | 作用 | 默认 |
|------|------|------|
| `N_confirm` | 反向确认根数（决定"反转预警"vs"已反向"边界） | 2 |
| `MAX_DEV_PCT` / `MAX_DEV_PCT_C` | B/C 趋势偏离阈 | 4% |
| `edge_window` | 入场依据消失检查窗口（= 旧锁定根数，现只用于事后兜底） | 3~5 |
| `allow_weak_entry` | WEAK_ALIGN 是否降级入场 | False |
| `use_decel` | 是否把 C.Sig→0 计入 WEAK 中间态 | False |

---

## 六、一句话收束

**入场**：B 起点当帧 + C 强同向即上车，不等锁定。**过滤**：把 C 从"一个 Sig"升级为"一个有 4 态（含衰竭中间态）的多空对状态机"，在 C 趋势尾部（WEAK）谨慎、C 反向（MISALIGN）拒绝。**兜底**：不锁定引入的假起点由"入场依据消失"快速刮损离场处理，离场③从认 C.Sig 改认 C 状态，避免中间态噪声误伤正常持仓。
