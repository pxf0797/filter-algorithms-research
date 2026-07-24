# PnL 实现设计说明书（基于 commit 1a4ecd7）

> 本文档是一份**可独立阅读、可交付**的 PnL 模块设计说明书。它忠实描述 `1a4ecd7` 版本代码现状，并系统对照 v4 策略文档的差异，给出设计建议。
>
> **阅读约定（务必先读）：** 全文严格区分两类内容——
> - **现状事实**：来自代码逐行规格，凡引用必附 `filter_engine.py` 行号，可回溯。
> - **【建议】**：本文档给出的落地取向，**非既成事实**，一律以【建议】前缀显式标注，绝不与现状混淆。

---

## 0. 文档元信息

| 项 | 值 |
|---|---|
| 对应代码版本 | commit `1a4ecd7` |
| 分支 | `docs/pnl-design-spec` |
| 核心文件 | `filter/services/filter_engine.py`（1020 行） |
| 关键函数 | `_compute_strategy_pnl`（L649-860）、`_find_all_pairs`（L520-576） |
| 直接调用方 | `_compute_strategy_display`（`streamlit_app.py` L287-289） |
| 上游调用链 | `streamlit_app.py` L678 → `_compute_strategy_display`（L279）→ `_compute_strategy_pnl`（L287） |

**版本声明（铁律）：** 本文档描述 `1a4ecd7` 版本**现状**。v4 策略文档（`docs/backtesting/half-pair-trading-strategy-v4.md`）中的 `half_pair`、C 五态、`N_confirm`、`gap_count`、`effective_start` 等构件均为**未落地蓝图**——这一点由 v4 文档自身 §9.1 逐行实跑确认并主动承认：**"v1、v2、v3、v4 全是设计蓝图，尚未落地。"** 本版本源码中不存在 `half_pair` / `trade_signals` / `strategy_mode`，只有 Schmitt 触发器 + `_find_all_pairs` + `_compute_strategy_pnl`。

---

## 1. 概述与设计目标

### 1.1 PnL 模块在信号流水线中的位置

PnL 计算是整条信号流水线的**收尾环节**，消费上游产出的信号与拟合结果，产出可视化的双收益曲线与逐笔交易记录。完整链路（现状事实，见 code-spec §9）：

```
noisy (原始价格)
    │
    ▼  _compute_filters(t, noisy, cfg)                     streamlit_app.py:655
filtered (滤波后价格)
    │
    ▼  _compute_schmitt_trigger(filtered, t, cfg)          streamlit_app.py:665
sig_t  (Schmitt 输出 +1/0/-1)                              filter_engine.py:434 _schmitt_trigger
    │      v = gradient(filtered); a = gradient(v)         对加速度阈值做迟滞判定
    ▼  _find_all_pairs(sig_t)                              streamlit_app.py:672
all_pairs (信号段配对边界 List[Tuple[int,int]])
    │
    ▼  _compute_prediction_pairs(...)                      streamlit_app.py:675
pred_pairs (每个 pair 区间的抛物线拟合结果)
    │
    ▼  _compute_strategy_pnl(...)                          streamlit_app.py:287-289
(long_pnl, short_pnl, trade_records)
```

**边界输入**（进入 `_compute_strategy_pnl` 时）：
- `filtered`：用户所选滤波器（SMA/EMA/Kalman 等）后的价格序列，**不是原始 `noisy`**。
- `sig_t`：Schmitt 触发器输出（±1/0），源于 `gradient(gradient(filtered))` 的加速度阈值迟滞判定。
- `all_pairs`：`_find_all_pairs` 产出的信号段边界。
- `pred_pairs`：每个 pair 区间的抛物线拟合，为止损提供**预测价参考轨道**。

### 1.2 设计目标（对现状的动机解读）

> 说明：以下为对**现状代码为何如此设计**的解读，动机部分属推断性归纳，机制描述均为现状事实。

- **为什么用双独立曲线**：`long_pnl` 与 `short_pnl` 各自从 100.0 起独立复利，互不共享权益（L690-694）。这样做把做多、做空两个方向的表现解耦，便于分别评估某一方向策略的贡献，而非混在单一权益曲线里看不清来源。本版本**不存在**"总组合权益"概念（code-spec §2 明确"代码未体现"）。
- **为什么止损基于预测价**：止损阈值相对抛物线**预测价 `pred_val`** 而非入场价（L763-766）。抛物线外推给出一条"价格本应沿此走"的参考轨道，价格偏离该轨道超过 `stop_loss_pct` 即判为"方向走错"提前止损。这是本版本区别于常规"相对入场价固定百分比止损"的**显著特征**（code-spec §5 标注）。

---

## 2. 当前实现精确规格（现状事实，引用行号）

> 本章所有内容均为 `1a4ecd7` 代码事实，行号对应 `filter_engine.py`。

### 2.1 输入 / 输出契约

**函数签名（L649-860，签名在 L649-655）：**

```python
def _compute_strategy_pnl(
    t: np.ndarray, filtered: np.ndarray, sig_t: np.ndarray,
    all_pairs: List[Tuple[int, int]], pred_pairs: List[Dict[str, Any]],
    stop_loss_pct: float, n_extend: int = 10,
) -> Tuple[np.ndarray, np.ndarray, List[Dict[str, Any]]]:
```

**输入参数来源：**

| 参数 | 来源 | 类型 | 含义 |
|---|---|---|---|
| `t` | 上游 `noisy` 时间索引 | `np.ndarray` | 均匀时间索引（bar 索引） |
| `filtered` | `_compute_filters()` | `np.ndarray` | 滤波后价格序列 |
| `sig_t` | `_schmitt_trigger` → `schmitt["sig"]` | `np.ndarray[int]` | Schmitt 输出：+1(多)/0(中)/-1(空) |
| `all_pairs` | `_find_all_pairs(sig_t)` | `List[Tuple[int,int]]` | 合并后异号段边界 `(first_seg_start, second_seg_start)` |
| `pred_pairs` | `_compute_prediction_pairs()` | `List[Dict]` | 每项 `{"fit_result":{...},"fit_start":int,"pair_end":int}` |
| `stop_loss_pct` | `cfg["stop_loss_pct"]`（默认 2.0） | `float` | 止损阈值百分比 |
| `n_extend` | `cfg["n_ext"]`（默认 10） | `int` | 预测前推 bar 数 |

**返回值：**

| 返回 | 类型 | 说明 |
|---|---|---|
| `long_pnl` | `np.ndarray` | 长度 = `len(t)`，初值 100.0。持仓期逐 bar 未实现 PnL，非持仓期为已实现本金水平线 |
| `short_pnl` | `np.ndarray` | 同上，做空方向 |
| `trade_records` | `List[Dict]` | 每笔完成交易一条记录 |

**`trade_records` 字段（L832-841）：**

```python
{
    "id": trade_id,                          # int, 从 1 自增
    "type": "long" | "short",                # str
    "entry_idx": int(entry_idx),             # int, bar 索引
    "exit_idx":  int(exit_idx),              # int, bar 索引
    "entry_price": float(entry_price),       # float
    "exit_price":  float(exit_price),        # float
    "return_pct":  float(trade_return*100),  # float, 百分比
    "exit_reason": exit_reason,              # str: "take_profit"|"stop_loss"|"eod"
}
```

**空数据契约（L697-698）：** 当 `len(all_pairs)==0 or len(pred_pairs)==0` 时，返回全 100.0 的两条曲线与**空列表 `[]`**（非 `None`）。

### 2.2 pair 检测与 dir 判定（`_find_all_pairs`，L520-576）

**三步算法：**

1. **收集连续非零段（L543-554）**：线性扫描 `sig_t`，对每个 `sig_t[i]!=0` 记录 `(start, end, val)`，`val` 在连续块内恒为 +1 或 -1。遇 `sig_t[i]==0` 终止当前段。**所有 0 被直接丢弃，不保留任何 gap 长度信息。**
2. **跨零合并同号段（L559-566）**：判据**只有一个——符号相同即合并**，完全不看隔了几个 0。`+1,0,+1` → 合并成一段 `+1`；`+1,0,-1` → 不合并（异号）。
3. **异号配对（L568-575）**：相邻异号段配成 `pair = (merged[j].start, merged[j+1].start)`。

**两条确定规则（现状事实）：**
- **无 gap 上限**：`+1,0,0,0,+1` 隔 3 个还是 300 个 0，结果一样合并成一段（L93 "代码未体现 gap 上限"）。
- **无 N_confirm 缓冲**：同号合并无条件，与零 gap 长度无关（L92 "代码未体现 N_confirm"）。合并只被"反向非零段"打断，哪怕单根 -1。

**pair 语义：**
- `pair = (pair_start, pair_end)`，其中 `pair_start` = 第一个信号段起点，`pair_end` = 异号段起点。
- **方向不编码在 pair 元组里**——留待后续由 `sig_t[pair_end]` 联合预测方向确定（见 2.3）。

**守卫（L539-540, L556-557）：** `n < 3` 返回 `[]`；`<2` 段返回 `[]`。

**示例（code-spec §3）：**
```
sig_t   = [0, +1, +1, 0, 0, +1, 0, -1, -1, 0, +1]
Segments = [(1,2,+1), (5,5,+1), (7,8,-1), (10,10,+1)]
Merged   = [(1,5,+1), (7,8,-1), (10,10,+1)]      # 前两段同号合并
Pairs    = [(1,7), (7,10)]                        # pair0: +1→-1;  pair1: -1→+1
```

### 2.3 入场逻辑（L729-740）

入场发生在 **`entry_idx = pair_end`**（异号段起点），需**信号方向与抛物线外推方向双确认**：

```
v2 = sig_t[pair_end]                              # L729 入场处信号
pred_up = (y_pred[-1] > y_pred[0])                # 抛物线外推趋势
is_long  = (v2 ==  1 and pred_up)                 # L730
is_short = (v2 == -1 and not pred_up)             # L731
若两者皆非 → 跳过该 pair                          # L733-734

entry_idx   = pair_end                            # L737
entry_price = filtered[entry_idx]                 # L738
若 entry_price 为 NaN 或 ≤ 0 → 跳过该 pair        # L739
```

- 预测方向 `pred_up` 由抛物线在 `[pair_end, pair_end+n_extend)` 区间外推得到（若 `len(y_pred)<2` 则跳过，L724）。
- **入场确认来源 = 本级抛物线外推方向**（非任何上级周期背书）。

### 2.4 离场逻辑（L742-789）

离场扫描采用**两阶段模型**，从 `entry_idx+1` 扫描到 `n-1`：

**Phase 1 — 保护期 `[entry+1, entry+n_extend]`（L756-770）：**
- 仅在 `i <= protect_end`（`protect_end = entry_idx + n_extend`）内检查止损。
- 止损相对**预测价 `pred_val`**（非入场价）：
  - 做多：`cur_price < pred_val * (1 - stop_loss_pct/100)`（L764）
  - 做空：`cur_price > pred_val * (1 + stop_loss_pct/100)`（L766）
  - 命中 → `exit_idx = i`，`exit_reason = "stop_loss"`，break（L769-770）。

**Phase 2 — 全程止盈（信号反转，L774-780）：**
- 做多：`sig_t[i] == -1` → 离场（L774）
- 做空：`sig_t[i] == 1`  → 离场（L778）
- `exit_reason` 保持默认 `"take_profit"`（L746 设默认），break。

**关键现状事实——无 N_confirm 缓冲：**
- **单根 `sig_t[i]==-1`（做多）即立即触发离场，无任何确认缓冲**（L774）。
- 之所以不产生噪声：`_schmitt_trigger` 迟滞（L501-511）保证原始信号 +1→-1 之间必经过 0，因此 `sig_t` 中异号连续段之间总有至少一个 0；但**0 本身不触发离场**，离场发生在**首个 `sig_t[i]==-1` 的 bar**（新异号段的第一根）。

**边界——无离场触发（L784-789）：**
- 若 `n-1 > entry_idx`：在末 bar 离场，`exit_reason = "eod"`（数据末端，L787）。
- 若 `entry_idx == n-1`（在最后一根入场）：整笔跳过。

### 2.5 收益与曲线构建（L796-829, L843-858）

**已实现收益（L796-799）：**
```
is_long:  trade_return = (exit_price - entry_price) / entry_price   # L797
is_short: trade_return = (entry_price - exit_price) / entry_price   # L799
return_pct = trade_return * 100                                     # L839
```

**持仓期逐 bar 未实现 PnL 填充（做多 L804-816）：**
```python
for i in range(entry_idx, exit_idx + 1):            # L806
    cur_p = filtered[i]
    if np.isnan(cur_p) or cur_p <= 0: continue      # L808-809
    unrealized = (cur_p - entry_price) / entry_price # L810
    long_pnl[i] = long_capital * (1 + unrealized)    # L811
long_capital *= (1 + trade_return)                   # L813 已实现本金复利
for i in range(exit_idx + 1, n):                     # L815 离场后填充已实现本金
    long_pnl[i] = long_capital                       # L816
```

**做空对称（L817-829）：** `unrealized = (entry_price - cur_p)/entry_price`（L823），`short_capital *= (1 + trade_return)`（L826），离场后填充 `short_capital`（L828-829）。

**双曲线核心结构（现状事实）：**
- 两曲线初值均 100.0（L690-691）；`long_capital`/`short_capital` 各自追踪**已实现（复利）本金**（L693-694）。
- 两曲线**完全独立**，不共享单一权益曲线。
- 每次离场后水平线反映该方向累计已实现收益。

**非持仓期 forward-fill（L843-858）：**
```
last_val = 100.0
for i in range(n):
    if long_pnl[i]==100.0 and i>0 and last_val!=100.0:  # 该 bar 视为"未写入"
        long_pnl[i] = last_val                          # 用上一已知值填充
    if long_pnl[i]!=100.0 or (i==0):
        last_val = long_pnl[i]                          # 更新跟踪值
# short 曲线逻辑完全相同
```
- **首笔交易前**的 bar：保持 100.0（从未写入，`last_val` 停在 100.0）。
- **交易之间**的 bar：已被上一笔离场后填充（L815/L828），forward-fill 仅兜底可能的边缘缺口。

### 2.6 止损公式（保护期内，相对预测价）

| 方向 | 止损条件 | 行号 |
|---|---|---|
| 做多 | `cur_price < pred_val * (1 - stop_loss_pct/100)` | L764 |
| 做空 | `cur_price > pred_val * (1 + stop_loss_pct/100)` | L766 |

- `pred_val`：抛物线在当前 bar `i` 处的预测价，`polyval((a,b,c), i - x0)`（`x0` 为 None 时用 `polyval((a,b,c), i)`）。
- 若 `pred_val` 为 NaN 或 ≤ 0，跳过该 bar 的止损检查（L762）。
- **仅保护期 `[entry+1, entry+n_extend]` 内生效**；保护期外不再检查止损（L756: `if i <= protect_end`）。

### 2.7 `exit_reason` 分类（三值）

| 值 | 触发条件 | 行号 |
|---|---|---|
| `"take_profit"` | 默认值。信号反转离场（保护期内或外均可），或末 bar 截断 | L746(默认), L776/L780(break), L786(n-1) |
| `"stop_loss"` | 价格偏离预测轨道超 `stop_loss_pct`，仅保护期检查 | L769-770 |
| `"eod"` | 无任何离场触发 → 在末 bar `n-1` 离场（数据末端） | L787 |

### 2.8 完整流程编号伪代码（`_compute_strategy_pnl`）

> 复述 code-spec §10，行号对应 `filter_engine.py`。

```
1.0 初始化
   1.1 n = len(t)
   1.2 long_pnl  = full(n, 100.0)                              # L690
   1.3 short_pnl = full(n, 100.0)                              # L691
   1.4 long_capital = short_capital = 100.0                    # L693-694

2.0 守卫
   2.1 若 all_pairs 空 或 pred_pairs 空:
       return (long_pnl, short_pnl, [])                        # L697-698

3.0 建 pred_map: pair_end → pred_data
   3.1 for pp in pred_pairs: pred_map[pp["pair_end"]] = pp

4.0 主循环 — 遍历 all_pairs 的 (pair_start, pair_end):

   4.1 预测方向
       若 pair_end ∉ pred_map → 跳过                            # L709
       取 fit_result 的 a,b,c,x0
       x_pred = arange(pair_end, pair_end + n_extend)
       y_pred = polyval((a,b,c), x_pred - x0)                  # 或 polyval(..., x_pred)
       若 len(y_pred) < 2 → 跳过                                # L724
       pred_up = (y_pred[-1] > y_pred[0])

   4.2 方向判定                                                 # L729-734
       v2 = sig_t[pair_end]
       is_long  = (v2 == 1  and pred_up)
       is_short = (v2 == -1 and not pred_up)
       两者皆非 → 跳过

   4.3 入场                                                     # L737-739
       entry_idx = pair_end;  entry_price = filtered[entry_idx]
       entry_price 为 NaN 或 ≤ 0 → 跳过

   4.4 离场扫描                                                 # L742-789
       protect_end = entry_idx + n_extend;  exit_idx = None
       exit_reason = "take_profit"  (默认)                     # L746
       for i in [entry_idx+1 .. n-1]:
           cur = filtered[i]; NaN 或 ≤ 0 → continue            # L752-753
           if i <= protect_end:                                # 保护期止损
               pred_val = polyval(...)                         # 有效则
               long : cur < pred_val*(1-slp/100) → stop        # L764
               short: cur > pred_val*(1+slp/100) → stop        # L766
               命中 → exit_idx=i, reason="stop_loss", break
           # 全程止盈
           long  and sig_t[i]==-1 → exit_idx=i, break          # L774
           short and sig_t[i]== 1 → exit_idx=i, break          # L778
       若 exit_idx is None:
           n-1 > entry_idx → exit_idx=n-1, reason="eod"        # L787
           否则(末 bar 入场) → 跳过

   4.5 结算                                                     # L792-799
       exit_price = filtered[exit_idx]; NaN 或 ≤ 0 → 跳过(不记录)
       trade_return = (exit-entry)/entry  或 (entry-exit)/entry

   4.6 曲线填充(以做多为例)                                     # L804-816
       for i in [entry_idx .. exit_idx]:
           long_pnl[i] = long_capital*(1 + (cur-entry)/entry)
       long_capital *= (1 + trade_return)
       for i in [exit_idx+1 .. n-1]: long_pnl[i] = long_capital
       (做空对称 L817-829)

   4.7 记录交易                                                 # L832-841
       append {id, type, entry_idx, exit_idx, entry_price,
               exit_price, return_pct, exit_reason}

5.0 forward-fill 非持仓期(两曲线各一遍)                          # L843-858

6.0 return (long_pnl, short_pnl, trade_records)
```

### 2.9 本版本关键设计特征小结（现状事实）

1. **做多/做空独立本金**——无合并权益曲线，各自复利。
2. **入场 = pair_end = 异号段边界**——入场要求信号符号 + 抛物线趋势双对齐。
3. **止损基于预测价而非入场价**——抛物线投影定参考轨道。
4. **无 N_confirm 缓冲**——单根异号信号即触发止盈离场，靠 Schmitt 迟滞防噪。
5. **pair 合并无 gap 上限**——任意长度的同号间隔零均合并。
6. **无 strategy_mode / half_pair**——凡有预测的在效 pair 均交易，无跳过 half-pair 的可配模式。

---

## 3. 与 v4 文档的差异对照表

> 本章整合 diff-analysis.md。**重要背景**：v4 文档 §9.1 已"逐行实跑确认"当前代码 ≠ v3/v4，主动声明 **"v1、v2、v3、v4 全是设计蓝图，尚未落地"**。故下表大量"文档有·代码无"是 v4 作者**已知并主动承认**的 gap，而非本文档新发现的隐蔽冲突。
>
> 引用约定：代码行号来自 code-spec（对应 `filter_engine.py`）；`§` = v4 文档章节。

### 3.1 差异对照表（18 条）

| # | 维度 | v4 文档描述 (§) | 1a4ecd7 代码实际 (行号) | 差异性质 | 影响 |
|---|---|---|---|---|---|
| 1 | **入场时机 (pair_start vs pair_end)** | "半边起点一出现就入场"、"入场：起点即判定(不等锁定)"、`pair_start_B==i` 零延迟(§1.3/§2/§2.2)；§9.1 自承认代码用 pair_end | `entry_idx = pair_end`(L737)，入场在**反向段起点**，需 `sig_t[pair_end]`+抛物线方向双确认(L729-740) | **不一致(文档已承认)** | 高：入场点相差一整个半边跨度，语义相反 |
| 2 | **dir 判定** | pair dir 应由 `reverse_run ≥ N_confirm` 才翻(§4.4) | 方向由 `v2=sig_t[pair_end]` 与预测趋势联合决定：`is_long=(v2==1 and pred_up)`、`is_short=(v2==-1 and not pred_up)`(L729-734) | **不一致** | 高：文档要 N_confirm 缓冲翻向；代码是单根 sig+抛物线方向门 |
| 3 | **pair 合并·现状无 gap 上限** | "没有 gap 上限，隔 3 或 300 个 0 结果一样"(§4.2)，"已对不改"(§9.2) | Step2 同号跨零合并无条件、不看 gap 长度(L559-566) | **一致(措辞差)** | 低：完全对齐 |
| 4 | **pair 合并·MAX_GAP_BARS** | 提议可选超参 `MAX_GAP_BARS`(默认 ∞=复现)(§4.7) | 无此超参；任意长度 0 均合并(L93) | **文档有·代码无(蓝图·默认关)** | 低：默认关无行为差异 |
| 5 | **离场缓冲 N_confirm** | 离场①接入 `reverse_run≥N_confirm`，"异号单根不再即砍"；默认值文档**内部矛盾**：§4.7=2 vs §9.3=1 | 单根 `sig_t[i]==-1` 即离场，**无缓冲**(L774) | **文档有·代码无** | 高：v4 核心"补落地"项；默认值需澄清 |
| 6 | **离场①反向段(止盈)** | "进入下一多空对"，`reverse_run≥N_confirm` 止盈(§6.1) | 存在：Phase2 信号反转即止盈(L774-780, 默认746)，但单根、无累计 | **不一致** | 中：骨架在，缺 N_confirm 累计 |
| 7 | **离场②不利偏离(MAX_DEV_PCT_B)** | "趋势偏离过大"，B 不利偏离 `>MAX_DEV_PCT_B`，"只看不利方向"，全程有效(§6.1/§6.2) | **无独立 MAX_DEV_PCT_B**。唯一偏离型离场是保护期内相对预测价止损(L756-770)，双向对称、仅保护期 | **文档有·代码无** | 中：全程不利偏离离场未实现 |
| 8 | **离场③ C 状态梯度** | "上级反向"按 C 状态梯度：MISALIGN→全退、C_ENDED→收紧减仓、WEAK→仅收紧、STRONG→持有(§6.1/§6.2) | **完全无 C 周期离场**(见#9) | **文档有·代码无** | 高：v4 离场三支柱之一整体缺失 |
| 9 | **C 五态** | STRONG_ALIGN/WEAK_ALIGN/C_ENDED/MISALIGN/NO_DIRECTION 五态驱动入场与离场(§3.4/§5)；§9.1 承认"源码里完全不存在(grep 无)" | 无 C 周期。入场"背书"由**同级抛物线方向** `pred_up` 代替(L724-731) | **文档有·代码无(已承认)** | 高：v4 核心背书构件缺失 |
| 10 | **断续度量化(gap_count/gap_ratio)** | 真新增：Step1 保留 0-run 长度，Step2 累加；gap_count=子段数−1(§4.3) | Step1 扫描时 **0 被直接丢弃**、不留 gap 信息(L543-554) | **文档有·代码无(真新增·数据地基)** | 高：其余断续构件的输入前置 |
| 11 | **止损基准(预测价 vs 入场价)** | "预测保护期止损：入场初期击穿轨道"(§6.1)——以预测轨道为基准 | 止损相对 `pred_val`(抛物线预测价)非入场价(L763-766) | **一致** | 低：均以预测轨道为止损基准 |
| 12 | **PnL 曲线结构(双独立曲线)** | 极简：正文仅 §7 流程图末步"记录交易/更新 PnL"(行345)，**无双曲线/独立本金讨论** | `long_pnl`/`short_pnl` 两条独立曲线，各自复利，无合并权益(L690-694, L806-829) | **代码有·文档无** | 中：v4 未描述 PnL 结构 |
| 13 | **收益公式** | 无公式(§1 仅"更新 PnL") | long `(exit-entry)/entry`；short `(entry-exit)/entry`；持仓期未实现逐根填充，`capital*=(1+return)` 复利(L796-826) | **代码有·文档无** | 中：公式仅存在于代码 |
| 14 | **保护期机制** | "(保留)预测保护期止损：入场初期击穿轨道"(§6.1) | Phase1 保护期 `[entry+1, entry+n_extend]`，n_extend 默认 10(L756-770) | **一致(措辞差)** | 低：意图一致 |
| 15 | **exit_reason 分类** | 隐含：离场①(止盈)/②(偏离)/③(C反向)/entry_void/保护期止损；显式命名 `entry_void`(§6.1/§6.2) | `{"take_profit","stop_loss","eod"}` 三值(L746/770/787)，**无 entry_void/C 反向/MAX_DEV** | **不一致** | 中：代码粒度远粗；`eod` 是代码独有、文档无 |
| 16 | **entry_void 机制** | 保留项：边缘窗口内 B 起点蒸发→快速小损离场(§2.3/§6.2)；§9.2 称"已可正确处理(条件性)" | 代码无 entry_void 离场路径 | **文档有·代码无** | 中：文档双兜底之一未落地 |
| 17 | **有效起点(earliest/effective_start)** | 真新增：`earliest_start`(最早+1) vs `effective_start`(最近 0→+1 恢复点)，断续型入场择时改用 effective(§4.5) | 仅 earliest：merge 把后续 +1 折回最早起点，pair_start=merged 首段起点(L559-566) | **文档有·代码无(真新增·默认关)** | 中：文档承认代码"要么假起点进早，要么错过真启动" |
| 18 | **入场背书/确认机制** | C 周期 STRONG_ALIGN 背书(空间换确定性，§2.1/§2.2) | 同级抛物线方向 `pred_up=(y_pred[-1]>y_pred[0])` 作确认(L720-730) | **不一致** | 中：确认来源不同——文档=上级 C 趋势；代码=本级抛物线外推 |

### 3.2 附 B — 差异归类小结

**（B1）文档描述但代码完全未实现（v4 蓝图 / 已承认 gap）：**
- C 五态及入场决策表、离场③梯度响应（#8/#9）
- N_confirm 缓冲（dir 翻向 + 离场①，#5）——文档定性为"补落地 v3 已有"
- 断续度量化 gap_count/gap_ratio（#10）——数据结构地基
- effective_start 双起点择时（#17）、STRONG 断续型打折（属 C 态子型）
- MAX_GAP_BARS gap 上限（#4）、离场② MAX_DEV_PCT_B 全程不利偏离（#7）、entry_void（#16）

**（B2）代码实现但文档未描述：**
- PnL 双独立曲线结构、独立本金复利（#12）
- 收益公式与持仓期未实现 PnL 填充、forward-fill（#13）
- `exit_reason="eod"`（数据末端离场，#15）——文档离场分类无对应项
- 入场用抛物线 `pred_up` 方向作确认（#18 的代码侧替代品）

**（B3）文档与代码明确冲突（最需注意）：**
- **入场时机 #1**：文档"起点即入场(pair_start)" vs 代码 pair_end——方向语义相反，最根本冲突（v4 §9.1 已承认）
- **dir 判定 #2 / 离场① #6**：文档 reverse_run≥N_confirm vs 代码单根即翻/即砍
- **入场背书 #18**：C 强同向 vs 抛物线外推方向——两套不同的假信号过滤逻辑
- **exit_reason #15**：文档五类含 entry_void/C 反向 vs 代码三类含 eod
- **文档内部矛盾**：N_confirm 默认值 §4.7(=2) 与 §9.3(=1) 打架（#5），需先内部对齐再落地

> 注：#1/#2/#5/#9 等"冲突"中，v4 文档 §9.1 已主动声明代码是未落地蓝图，故属"设计蓝图 vs 现状"的**已知落差**，非隐蔽 bug。

---

## 4. 已知 gap 与设计建议（⚠️ 本章为建议，非现状）

> **格式约定：本章"现状"= 可回溯行号/章节的事实；【建议】= 本文档给出的落地取向，非既成事实。**

### 4.1 哨兵值 100.0 冲突

- **现状（事实）**：两条 PnL 曲线初值 = 100.0（L690-691），而 forward-fill 又用 `long_pnl[i]==100.0` 作为"该 bar 未被写入"的哨兵判据（L843-858）。**二者共用同一数值。**
- **现状风险（事实）**：任何真实交易若使 PnL 恰好回到 100.0（净收益为 0，即 `unrealized/trade_return ≈ 0`），会被 forward-fill 误判为"未写入"而被上一已知值覆盖。code-spec §6 也指出"首笔交易前的 bar 靠 last_val 停在 100.0"依赖该哨兵。
- **【建议】**：改用独立的"已写入"布尔掩码（如 `written = np.zeros(n, bool)`）替代数值哨兵；或初始化用 `np.nan`，在 forward-fill 后统一回填。目的是把"初始资本 100.0"与"未写入标记"**解耦**。这属独立于 v4 的工程修复，**可在零回归验证前先行合入**。

### 4.2 跨周期 PnL 过期

- **现状（事实，标注待复核）**：existing-docs-survey 记录该问题存在于跨周期 PnL 数据依赖链（渲染顺序 / 逐级写读）。但 code-spec 未覆盖此路径——`_compute_strategy_pnl` 本体是**单周期纯函数**，故本文档**无法用行号确证**其在 `1a4ecd7` 的具体表现。
- **【建议】**：撰写/维护 PnL 文档时，把跨周期依赖**单列一章**，并显式标注"待用当前代码复核"，避免把历史结论当作现状。

### 4.3 v4 蓝图落地顺序建议

- **现状（事实）**：v4 §9.4 规定所有开关取"复现现状值"时系统应逐字节复现基线，**"回测零回归是第一道验证"**，并要求断续度量化（gap_count/gap_ratio）**最先接入**（是其余构件的输入地基）。
- **【建议】落地次序**（契合"零回归先行"）：
  1. **断续度量化（#10）** —— 纯计算不改决策，风险最低，是地基。
  2. **N_confirm（#5）** —— 默认先对齐为 `1`（复现现状），再切 `2` 做边际对比；先裁决文档内部默认值矛盾（§4.7=2 vs §9.3=1，建议取 1 复现、2 作推荐起步另注）。
  3. **effective_start / MAX_GAP_BARS（#17/#4）** —— 阈值高到不触发 = 复现现状。
  4. **C 五态（#9）** —— 需新建上级周期状态机，改动面最大，**压后**。
- **【建议】理由**：#10 无决策影响且是地基；C 五态改动面最大应最后落地。

### 4.4 文档-代码对齐优先级建议

- **现状（事实）**：v4 文档 §1 对 PnL 计算近乎空白（仅"更新 PnL"一步，行345），而 PnL 双曲线 / 收益公式 / exit_reason 的完整算术只存在于代码（#12/#13/#15）与已过时的 STPP 归档文档。
- **【建议】**：对齐时应把"文档蓝图态"（pair_start 入场、N_confirm、C 五态）与"代码现状态"（pair_end 入场、单根离场、抛物线背书）**并排双列**记录，切勿把 v4 蓝图直接写成"现有实现"——这正是 v4 §9.1 反复强调的铁律。

---

## 5. 边界与不变量

### 5.1 NaN / 非法价格 / 索引越界处理（现状事实）

| 位置 | 检查 | 动作 |
|---|---|---|
| L739 | `np.isnan(entry_price) or entry_price <= 0` | 跳过整个 pair |
| L752-753 | `np.isnan(cur_price) or cur_price <= 0` | 扫描循环内 `continue` 跳过该 bar |
| L762 | `np.isnan(pred_val) or pred_val <= 0` | 跳过该 bar 的止损检查 |
| L792-793 | `np.isnan(exit_price) or exit_price <= 0` | 不记录该笔交易 |
| L539 | `n < 3` | `_find_all_pairs` 返回 `[]` |
| L556 | `<2` 段 | `_find_all_pairs` 返回 `[]` |
| L697 | `len(all_pairs)==0 or len(pred_pairs)==0` | 返回初值曲线 + 空 trade 列表 |
| L709 | `pair_end not in pred_map` | 跳过该 pair（无预测） |
| L724 | `len(y_pred) < 2` | 跳过该 pair（无法判 pred_up） |
| L785-789 | `exit_idx is None` | `n-1 > entry_idx` 则在 `n-1` 离场（eod）；否则跳过 |

### 5.2 不变量（现状事实）

- **两曲线初值恒为 100.0**（L690-691）；非持仓期为水平线（已实现本金）。
- **做多/做空本金独立复利**（L693-694, L813, L826），互不影响。
- **pair 成对性**：`_find_all_pairs` 产出的每个 pair 必为相邻异号段边界 `(first_seg_start, second_seg_start)`（L568-575）。
- **入场恒在 pair_end**，且需信号符号与抛物线方向双对齐（L730-737）。
- **止损仅在保护期 `[entry+1, entry+n_extend]` 检查**（L756）；止盈全程有效（L774-780）。
- **空数据返回 `[]` 而非 `None`**（L698）。

### 5.3 已有测试覆盖（现状事实，引 `docs/test_cases.md`）

| 测试类 | 用例编号 | 覆盖内容 |
|---|---|---|
| `TestComputeStrategyPnL` | TC-STRAT-009 ~ 015 | `_compute_strategy_pnl` 核心逻辑 |
| `TestRenderExitMarker` | TC-CHART-009 ~ 017 | 离场标记渲染 |
| `TestRenderPnLCurves` | TC-CHART-029 ~ 032 | PnL 双曲线渲染 |
| `TestCrossPnlSubplot` | TC-CHART-060 ~ 064 | 跨周期 PnL 子图 |

> 说明：以上测试类与用例编号来自 existing-docs-survey 对 `docs/test_cases.md` 的盘点，用于指示已有覆盖范围。

---

## 附：本文档诚实边界

- 跨周期 PnL 过期（§4.2）在 `1a4ecd7` 的具体行为**无 code-spec 行号支撑**，仅据 existing-docs-survey 转述，标注为"待复核"。
- code-spec 明确 `_compute_strategy_pnl` 是**单周期纯函数**；文档中"C 周期 / 上级背书"整套跨周期机制在本版本源码**无对应实现**，故 #8/#9/#18 的"代码侧"以"无 / 用 pred_up 替代"如实记录。
- 本文档所有代码事实与 code-spec.md 严格一致；§1.2 的"设计动机"部分含推断性解读，机制描述本身均为现状事实。
