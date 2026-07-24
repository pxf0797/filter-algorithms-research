# 以「多空对（Pair）」为单元的完整交易策略设计

> 依据：T1 分析（`t1-pair-lifecycle-analysis.md`）+ 源码 `filter/services/filter_engine.py`
> 关键前提（T1 结论）：现有 `_find_all_pairs` **不返回半边 Pair**（`range(len(merged)-1)` 止于倒数第二段），当前实现事实上即「保守策略 A」——只在完成 Pair 的 `pair_end` 入场。本设计要在**不破坏这一保守语义的前提下，增量地把半边 Pair 纳入可交易范围，并补齐退出机制与多周期协同**。

---

## 0. 设计总纲：三层可插拔

本方案不推翻现有代码，而是分三层增量叠加，每层可独立开关（回测时逐层验证增益）：

1. **识别层**：新增 `_find_current_half_pair`，把「窗口最右侧未完成段」显式暴露出来。
2. **入场层**：`_compute_strategy_pnl` 增加一条「半边支路」，用可靠性评分做门控（模式 A/B/C）。
3. **退出层**：在持仓扫描循环中加入两类新退出（趋势偏离、进入下一 Pair），并定义与现有止损/止盈的优先级。
4. **协同层**：跨周期 Pair 门控（C 约束 B），复用 `_align_pnl_to_current_tf` 基建。

---

## 1. 两种入场语义

### 1.1 语义对比表

| 维度 | 语义A：完成 Pair 入场（现状） | 语义B：半边 Pair 入场（用户诉求） |
|------|------------------------------|-----------------------------------|
| 入场索引 | `entry_idx = pair_end`（=下一反向段起点 s2） | `entry_idx = current_bar`（半边段进行中的当前帧） |
| 方向来源 | `v2 = sig_t[pair_end]`，即**新反向趋势**方向 | `v = sig_t[half_start]`，即**当前正在走的趋势**方向 |
| 本质 | 在旧趋势死、新趋势生的反转点入场，赌新趋势 | 参与当前正在延伸的趋势，及时但未证实 |
| 时序 | 永远滞后一整段趋势 | 与趋势同步 |
| 假信号暴露 | 零（半边消失时未持仓，见 T1 场景C） | 直接暴露于场景C（~5-10% 整段被否定） |
| 适用 | 回测验证、低频、保守 | 实盘参与趋势、需承担不确定性 |

### 1.2 关键洞察（T1 的 `pair_end` 双重身份）

语义A 的入场点 `pair_end=s2` 同时是「上一段 Pair 的终点」和「下一段半边 Pair 的诞生点」。因此语义A 本质上就是「**在半边 Pair 刚诞生的那一刻入场，但方向锁定为该半边段方向**」——它其实是语义B 的一个特例（入场点=半边起点，且用完成对确认了前一段结束）。

**统一视角**：两种语义只是「入场时机」的差异——
- 语义A：等前一段完成（反向段出现）→ 在新半边段的**第 0 bar**（诞生点）入场；
- 语义B：不等前段完成 → 在当前半边段的**第 k bar**（进行中）入场。

这让二者能共用同一套方向/预测/退出逻辑，只在「入场触发条件」上分叉。二者**不互斥**：完成 Pair 走原路径，半边 Pair 走新支路，同一帧最多持一仓（见第 5 节门控）。

---

## 2. 半边 Pair 的识别与可靠性评分

### 2.1 识别函数

新增独立函数（不改 `_find_all_pairs`，保持其保守语义纯净）：

```python
def _find_current_half_pair(sig_t: np.ndarray) -> Optional[Tuple[int, int, int]]:
    """返回窗口最右侧未完成的半边 Pair。
    Returns (half_start, half_end, direction) 或 None。
    - half_start: 最后一个非零段（跨零合并后）的起点
    - half_end:   n-1（当前帧，永远贴右边缘）
    - direction:  +1 / -1
    仅当最右段之后【无反向段】时才算半边；若已有反向段，它已被
    _find_all_pairs 收尾为完成对，不再是半边。
    """
    n = len(sig_t)
    if n < 3:
        return None
    # 复用 Step1+Step2（收集非零段 + 跨零合并），取 merged 最后一段
    merged = _merge_segments(sig_t)          # 抽出 _find_all_pairs 的 Step1/2 为公共函数
    if not merged:
        return None
    s, e, v = merged[-1]
    # 若最后一段直抵右边缘且其后无反向段 → 半边
    if e >= n - 1 or all(sig_t[k] == 0 for k in range(e + 1, n)):
        return (s, n - 1, v)
    return None
```

> 集成注：把 `_find_all_pairs` 的 Step1（收集非零段）+ Step2（跨零合并）抽成公共 `_merge_segments(sig_t)`，`_find_all_pairs` 和 `_find_current_half_pair` 共用，避免逻辑漂移。`_find_all_pairs` 本体不变。

**为何不改 `_find_all_pairs(include_half=True)`**：完成对和半边对的下游消费完全不同（完成对入场点=pair_end，半边入场点=当前帧；完成对无假信号风险，半边有）。混在同一列表里会污染现有回测的确定性，也难以独立开关。独立函数更符合「Surgical Changes」。

### 2.2 可靠性评分公式

半边 Pair 是否值得入场，由 6 指标加权评分决定。评分越高，越像「真趋势」而非「即将消失的假段」：

```
reliability = w1·duration + w2·purity + w3·higher_tf
            + w4·velocity + w5·prediction + w6·edge_zone
```

各指标量化（全部归一到 [0,1]）：

| 指标 | 符号 | 量化公式 | 代码来源 | 权重建议 |
|------|------|----------|----------|----------|
| 持续时间 | duration | `clip(dur_t[half_end] / D_ref, 0, 1)`，D_ref≈8 | `schmitt["dur"]` 现成 | w1=0.20 |
| Sig 纯度 | purity | `1 - zeros/len`，zeros=`sum(sig_t[s:e+1]==0)` | 扫 sig 现成 | w2=0.15 |
| 高周期同向 | higher_tf | C 周期活跃 Pair 同向=1；无=0.5；反向=0 | 跨周期（新建，见§4） | w3=0.25 |
| v 方向稳定 | velocity | `clip(|mu_v[e]| / (k·sigma_v[e]), 0, 1)` 且 `sign(mu_v)==dir` 否则 0 | `mu_v/sigma_v` 现成 | w4=0.15 |
| 预测方向 | prediction | 抛物线外推 `pred_up` 与 dir 同向=1，否则 0 | `_fit_*`+外推现成 | w5=0.15 |
| 起点离边缘 | edge_zone | `1 if (n-1-half_start) >= edge_w else 0`，edge_w≈3-5 | dur_t 现成 | w6=0.10 |

设计要点：
- **w3（高周期同向）权重最高（0.25）**：T1 指出跨周期确认是唯一需新建的基建，但也是最强的可靠性来源——B 的半边 + C 完整同向锁定，几乎排除假信号。
- duration+edge_zone 共同保证「段已长够、起点已越过死区」——直接压制 T1 场景C（半边消失）的概率。
- prediction+velocity 保证「方向自洽」——预测曲线和动量都认同该方向。
- 阈值 `REL_TH` 建议 0.6（保守）～0.5（激进），作回测超参。

---

## 3. 三种处理模式

| 模式 | 行为 | 对应代码路径 | 风险/收益 |
|------|------|--------------|-----------|
| **A 保守** | 不碰半边，仅完成 Pair 入场 | 现有 `_compute_strategy_pnl`，`_find_current_half_pair` 不接入 | 零假信号暴露，滞后一段 |
| **B 条件入场** | `reliability ≥ REL_TH` 时，当前帧入场 | 新增半边支路，方向=half dir，入场=half_end | 暴露场景C，靠止损+退出兜底 |
| **C 观察补仓** | 半边期只标记不下单；待其完成且方向被验证，在后续同向完成 Pair 放大仓位 | 记录 `pending_half`，完成后在 `pair_end` 用 size 因子加仓 | 高胜率低频，最兼容现状 |

模式选择由配置 `cfg["half_pair_mode"] ∈ {A,B,C}` 控制，默认 A（向后兼容）。

---

## 4. 退出机制（三类）

现有退出只有两条：预测保护期止损（行 756-771，仅 `[entry+1, entry+n_extend]` 内生效）+ Sig 反转止盈（行 774-781，全程）。用户要求新增两条，并明确优先级。

### 4.1 退出①：趋势偏离过大（全程生效）

利用 `_fit_physics_parabola`/`_fit_parabolic` 的预测轨道，衡量实际价格偏离预测的程度：

```
pred_val(i) = polyval((a,b,c), i - x0)          # 预测曲线在 i 的值
deviation(i) = |filtered[i] - pred_val(i)| / pred_val(i)
IF deviation(i) > MAX_DEV_PCT (默认 3%~5%):
    → 趋势已破位，退出，exit_reason="trend_deviation"
```

**与现有「预测保护期止损」的本质区别**：

| | 现有保护期止损（行756-771） | 新增趋势偏离退出 |
|---|---|---|
| 生效区间 | 仅 `[entry+1, protect_end]`（前 n_extend bar） | **整个持仓期** `[entry+1, exit]` |
| 判定 | 单边击穿（多头跌破轨道下沿 / 空头涨破上沿） | **双向绝对偏离** |val - pred| 超阈 |
| 语义 | 入场初期防「进错方向」 | 全程防「趋势破位/加速失效」 |

**方向定义**：
- 做多：`deviation` 只在 `filtered[i] < pred_val` 方向计（价格跌离上升轨道）时触发退出；上方偏离（涨超预测）是有利偏离，不退出。
- 做空：只在 `filtered[i] > pred_val`（价格涨离下降轨道）触发。
- 实现上：`signed_dev = (pred_val - cur_price)/pred_val`（多）或 `(cur_price - pred_val)/pred_val`（空），`if signed_dev > MAX_DEV_PCT: exit`。这样只惩罚「不利偏离」，让有利偏离继续奔跑。

保护期止损保留（前期严防），趋势偏离退出接管后期——二者互补，不冲突。

### 4.2 退出②：进入下一个多空对（Pair 反转，全程生效）

当前 Pair 结束、新反向 Pair 诞生时退出：

```
做多持仓 + sig_t[i] == -1（新做空段诞生）→ 退出做多
做空持仓 + sig_t[i] == +1（新做多段诞生）→ 退出做多
```

这与现有 Sig 反转止盈（行 774-781）**判定条件相同**，但语义升级为「Pair 级」：现有逻辑遇到单个反向 bar 就退。**问题**：T1 场景B/C 指出边缘区单 bar 有 5-10% 概率是噪声翻转。

**缓冲设计（推荐）**：新反向段需**持续 N_confirm bar 才确认**（默认 N_confirm=2），避免假反转误退：

```
reverse_run = 连续反向 sig 的 bar 数
IF reverse_run >= N_confirm:
    → 确认进入下一 Pair，退出，exit_reason="next_pair"
```

代价：确认期内回吐 1-2 bar 利润。收益：避开边缘噪声的假止盈。N_confirm 作回测超参（0=退化为现有行为）。

### 4.3 退出③：上级周期否定（协同退出，见§5）

C 周期活跃 Pair 翻向、与当前持仓反向 → 强制退出。这是最高优先级的「大势否定」。

### 4.4 退出优先级

同一 bar 多条件同时满足时，按「风险紧迫度」降序：

```
1. 上级周期否定 (trend_deviation 之上)  —— 大势已反，立即离场，最高优先
2. 趋势偏离过大 (trend_deviation)        —— 本级趋势破位，价格已脱轨
3. 进入下一 Pair (next_pair, 需 N_confirm) —— 本级趋势正常反转，止盈离场
4. 预测保护期止损 (stop_loss, 仅前期)     —— 入场初期防错方向
```

理由：①③是「趋势判断已被推翻」（结构性），必须先于②「价格脱轨」和④「初期止损」。实现上在扫描循环内**按此顺序 if-elif 判定，命中即 break**。

---

## 5. 多周期 Pair 协同

沿用 A<B<C<D 周期门控，但门控单元从 Sig 升级为 **Pair**。交易在 B 周期发生，C 周期做方向约束，D 周期做背景过滤。

### 5.1 C 周期活跃 Pair 的提取

C 周期永远站在右边缘，其「当前方向」= C 的半边 Pair 方向（`_find_current_half_pair(sig_C)` 的 direction），或 C 最近完成 Pair 的方向（若无半边）。记为 `dir_C ∈ {+1, -1, 0}`。

### 5.2 门控规则

| B 信号 | C 状态 | 决策 |
|--------|--------|------|
| B 半边同向 | C 完整/半边同向锁定 | **模式B 入场**（higher_tf=1，可靠性大幅提升） |
| B 半边同向 | C 无同向（dir_C=0 或反向） | 降级为**模式A（等待完成）** 或不入场 |
| B 完成 Pair | C 同向 | 正常入场（现有路径 + C 加持） |
| B 完成 Pair | C 反向 | 否决入场（逆大势） |
| 持仓中 | C 翻向为反向 | 触发**退出③（上级否定）** |

核心：**C 同向是打开模式B（半边条件入场）的钥匙**。B 单独的半边不确定（场景C 风险），但「B 半边 + C 完整同向锁定」把假信号概率从 5-10% 压到接近 0——此时值得提前上车。反之 C 不背书，就退回保守的等待完成。

### 5.3 复用现有基建

`_align_pnl_to_current_tf`（行 867+）已具备「高周期时间戳→当前周期前向填充对齐」能力。协同层新增一个轻量 `_get_higher_tf_direction(higher_sig, higher_dates, current_dates, i)`：对齐后返回当前帧对应的 `dir_C`，供 §2.2 的 higher_tf 指标和 §5.2 门控共用。这是 T1 指出「唯一需新建的基建」，但可站在 `_align_pnl_to_current_tf` 肩上。

---

## 6. 完整决策流程（每帧执行）

```
┌─────────────────────────────────────────────────────────────┐
│ 每帧 i（回测推进 / 实盘 tick）                                │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
        ┌────────────────────────────────────────┐
        │ 计算各周期 schmitt: sig_B, sig_C, sig_D │
        │ all_pairs_B = _find_all_pairs(sig_B)    │
        │ half_B = _find_current_half_pair(sig_B) │
        │ dir_C  = _get_higher_tf_direction(...)  │
        │ dir_D  = 背景过滤方向                    │
        └───────────────┬────────────────────────┘
                        ▼
              ┌──────────────────┐
              │  当前是否持仓?     │
              └───┬──────────┬───┘
             无仓 │          │ 有仓
                  ▼          ▼
     ┌─────────────────┐   ┌──────────────────────────────────┐
     │  入场判定         │   │  退出判定（按优先级 if-elif）      │
     ├─────────────────┤   ├──────────────────────────────────┤
     │ 1. 完成Pair到达?  │   │ ① 上级否定: dir_C 反向? → EXIT     │
     │   (pair_end==i)  │   │ ② 趋势偏离: signed_dev>MAX_DEV?    │
     │   且 dir_C 不反   │   │      → EXIT(trend_deviation)      │
     │   → 语义A 入场    │   │ ③ 下一Pair: 反向run>=N_confirm?   │
     │                  │   │      → EXIT(next_pair)            │
     │ 2. half_B 存在?   │   │ ④ 保护期止损: i<=protect_end 且   │
     │   mode==B 且      │   │      击穿轨道? → EXIT(stop_loss)  │
     │   reliability>=TH │   │ 否则 → 继续持有                    │
     │   且 dir_C 同向    │   └──────────────────────────────────┘
     │   → 语义B 入场    │
     │                  │
     │ 3. mode==C:       │
     │   标记 pending_half│
     │   待完成后加仓     │
     │ 否则 → 空仓等待    │
     └─────────────────┘
                  │
                  ▼
         记录 trade / 更新 PnL 曲线
```

**决策优先级摘要**：无仓时「完成 Pair 入场」优先于「半边入场」（前者确定性高）；有仓时退出按 §4.4 优先级。同一帧不同时开多个仓（单持仓模型，与现有 `_compute_strategy_pnl` 一致）。

---

## 7. 与现有代码的集成方案

### 7.1 需新增的函数

| 函数 | 位置 | 职责 |
|------|------|------|
| `_merge_segments(sig_t)` | filter_engine.py | 抽出 `_find_all_pairs` Step1+2，公共复用 |
| `_find_current_half_pair(sig_t)` | filter_engine.py | 识别右边缘半边 Pair（§2.1） |
| `_half_pair_reliability(sig_t, schmitt, half, fit, dir_C)` | filter_engine.py | 6 指标加权评分（§2.2） |
| `_get_higher_tf_direction(...)` | filter_engine.py | 跨周期方向，复用 `_align_pnl_to_current_tf`（§5.3） |

### 7.2 需修改的函数

**`_find_all_pairs`**：仅内部改为调用 `_merge_segments`，**对外行为与返回值完全不变**（保护现有回测）。

**`_compute_strategy_pnl`**：这是主要改动，但采取「增量支路 + 参数默认关闭」策略，保证 `half_pair_mode="A"` 时逐字节复现现有行为：

1. 新增参数：`half_pair=None, half_pair_mode="A", reliability_th=0.6, max_dev_pct=4.0, n_confirm=2, dir_higher=None`（全部有默认值，旧调用不受影响）。
2. **入场支路**：现有 `for pair_start, pair_end in all_pairs` 循环保留（语义A）。当 `mode in {B,C}` 且存在 `half_pair` 时，追加一段半边入场逻辑：算 reliability，过门 + `dir_C` 同向 → 以 `entry_idx=half_end`、方向=half dir 入场，复用下方同一套持仓扫描。
3. **退出扫描循环**（行 750-781）内：在现有 stop_loss/take_profit 之上，按 §4.4 优先级插入 ①上级否定 ②趋势偏离 ③下一Pair缓冲。现有两条退出降为优先级 ③(升级为带缓冲)、④，逻辑保留。
4. `exit_reason` 枚举扩展：`trend_deviation` / `next_pair` / `higher_tf_veto`。

**`_compute_prediction_pairs`（streamlit_app.py 260）**：为半边 Pair 也生成一条预测曲线——追加对 `half_pair` 的 `fit_func(t, filtered, half_start, half_end)`，存入 `pred_pairs`（key 用 `half_end`），供半边入场的方向判定与趋势偏离退出复用。

**`_compute_strategy_display`（streamlit_app.py 279）**：透传新配置项 `half_pair_mode` 等；调用 `_find_current_half_pair` 得到 `half_pair` 一并传入 `_compute_strategy_pnl`。

### 7.3 向后兼容与验证

- **默认 `half_pair_mode="A"`** → 所有新支路短路，输出与当前完全一致。回测「策略A 基线不回归」是第一道验证。
- 逐层开关验证增益：A（基线）→ +退出①② → +半边模式B → +多周期协同，每层独立回测胜率/回撤/夏普，量化每一层的边际贡献。
- 成功标准：模式B 在「C 同向门控」下，应比模式A **提前入场且不显著抬高假信号率**（对比 trade_records 的 `next_pair`/`trend_deviation` 退出占比与净收益）。

---

## 8. 小结

本设计把用户的核心矛盾——「完整 Pair 确定但滞后 vs 半边 Pair 及时但不确定」——用**三把锁**化解：

1. **可靠性评分门**（§2.2）：只让像真趋势的半边 Pair 上车；
2. **多周期同向门**（§5）：用 C 周期的完整 Pair 为 B 的半边背书，把假信号概率压到最低；
3. **三级退出网**（§4）：趋势偏离（全程脱轨）+ 下一 Pair（带缓冲的反转）+ 上级否定（大势反转），为提前入场的不确定性兜底。

全部改动以「增量支路 + 默认关闭」落地，`_find_all_pairs` 保守语义不受污染，现有回测零回归，符合 Surgical Changes 原则。半边 Pair 从「永不被交易的盲区」变为「可评分、可门控、可退出的一等公民」，让策略能够参与当前正在进行的趋势，而非永远滞后一段。
