# PnL 计算模块设计文档（现状 / As-Built，commit 1a4ecd7）

> 文档定位：**纯现状（As-Built）设计文档**。全文只描述 `1a4ecd7` 版本代码"现在是什么样"，
> 所有机制均为可回溯的代码事实，凡引用附 `filter_engine.py` 行号；少量对"为何如此"的
> 归纳性解读一律以 **【解读】** 前缀显式标注，绝不与现状事实混淆。
>
> 目标读者：半年后接手本模块的工程师、代码审查者、需要在现状基线上提改进的设计者。

---

## 1. TL;DR

本模块是整条信号流水线的收尾环节：消费上游产出的滤波价格、Schmitt 触发信号与抛物线拟合结果，
按"入场需信号方向与抛物线外推方向双确认、离场为保护期止损 + 全程信号反转止盈"的规则，
产出**做多、做空两条相互独立、各自从 100.0 起复利的 PnL 曲线**与**逐笔交易记录**，供上层 Streamlit 图表渲染。
核心计算集中在 `_compute_strategy_pnl`（`filter_engine.py` L649-860）单一纯函数中，每次界面交互全量重算。

---

## 2. 元信息

| 项 | 值 |
|---|---|
| 对应代码版本 | commit `1a4ecd7` |
| 分支 | `docs/pnl-design-spec` |
| 核心文件 | `filter/services/filter_engine.py`（1020 行） |
| 核心函数 | `_compute_strategy_pnl`（L649-860）、`_find_all_pairs`（L520-576） |
| 跨周期辅助函数 | `_align_pnl_to_current_tf`（L867-974） |
| 直接调用方 | `_compute_strategy_display`（`streamlit_app.py` L279-313，调用点 L287-289） |
| 上游调用链 | `streamlit_app.py` L678 → `_compute_strategy_display`（L279）→ `_compute_strategy_pnl`（L287） |
| 消费/渲染层 | `_add_pnl_traces`（`streamlit_app.py` L428-468）、`charts.py` 的 `_add_cross_pnl_subplot`（L351-396）、`_add_alignment_subplot`（L399-461） |
| 配置来源 | `filter/state.py` VIEW_DEFAULTS（L70-87）、`filter/components/sidebar.py` |

---

## 3. Context & Scope

### 3.1 模块在信号流水线中的位置

PnL 计算是信号流水线的**最后一环**。它不生产信号，只把上游"信号 + 拟合"翻译成"如果按这套信号交易，
两个方向各自的收益曲线长什么样、每笔交易怎么进出"。完整上游链路（现状事实，见附录调用链）：

```
noisy (原始价格)
    │
    ▼  _compute_filters(t, noisy, cfg)                     streamlit_app.py:655
filtered (滤波后价格：SMA / EMA / Kalman 等，非原始 noisy)
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

### 3.2 谁消费本模块的输出

`_compute_strategy_pnl` 返回 `(long_pnl, short_pnl, trade_records)` 三元组，被四处消费（现状事实，见 as-is 补充 §2）：

| 消费方 | 文件位置 | 职责 |
|---|---|---|
| `_compute_strategy_display` | `streamlit_app.py` L279-313 | 计算胜率、多/空/合计收益、最大回撤，渲染三段 `st.columns` 摘要（L293-307）；并把 `{dates, t, long_pnl, short_pnl, trade_records}` 持久化到 `st.session_state[f"_pnl_{tf}"]`（L308-312），供跨周期对齐读取 |
| `_add_pnl_traces` | `streamlit_app.py` L428-468（L719 调用） | 主 PnL 子图：完整双曲线 + 逐笔持仓段加粗 + 入/出场标记 + 收益标注 + 100 上下填充带 |
| `_add_cross_pnl_subplot` | `charts.py` L351-396（`streamlit_app.py` L722 调用） | 高周期 PnL 参考线（虚线/点线），入/出场标记与收益标注 |
| `_add_alignment_subplot` | `charts.py` L399-461（`streamlit_app.py` L727 调用） | 同向性判断子图：只显示高周期持仓掩码激活期间的交易段 |

### 3.3 触发时机

每一次用户交互（滑块拖动、复选框切换）触发一次完整 Streamlit rerun，整条链路从头重算，
**无逐 rerun 的记忆化/缓存**（as-is 补充 §3）。跨周期数据通过 `st.session_state[f"_pnl_{tf}"]` 在 rerun 之间传递（详见 §8.3）。

---

## 4. Goals & Non-Goals

### 4.1 Goals（本模块要做到的事）

- **忠实计算双向 PnL 曲线**：分别为做多、做空产出逐 bar 的 PnL 曲线（`long_pnl` / `short_pnl`），持仓期反映未实现盈亏，非持仓期为已实现本金水平线。
- **逐笔交易可回溯**：为每笔完成交易产出一条结构化记录（进出场索引、价格、收益率、离场原因），供表格展示与图表标注。
- **确定性计算**：给定相同输入（`t`、`filtered`、`sig_t`、`all_pairs`、`pred_pairs`、配置），输出完全可复现——纯函数、无随机性、无外部状态依赖。
- **为可视化服务**：曲线与交易记录的结构直接面向下游四处渲染层，字段与初值约定（如初值 100.0）为渲染填充带、基线、标记服务。

### 4.2 Non-Goals（可能被合理认为是目标、但代码现状确实未做的事）

- **不做组合净值合并**：做多与做空是两条独立曲线、两个独立本金变量，代码中**不存在**"总组合权益"这一概念（code-spec §2 明确"代码未体现"）。
- **不做手续费 / 滑点 / 融资成本建模**：收益公式为纯价差比率 `(exit-entry)/entry`，无任何交易成本项（L796-799）。
- **不做跨币种 / 多资产汇总**：函数只接收单一 `filtered` 价格序列，无币种或资产维度。
- **不做信号生成或行情预测**：信号（`sig_t`）与预测（`pred_pairs`）均由上游产出，本模块只消费；抛物线仅作为止损参考轨道使用，不在本模块内拟合。
- **不做可视化本身**：本模块只产出数值曲线与记录；绘图、标记、填充带全部在渲染层（§3.2）完成。

---

## 5. The Design（详细设计）

> 本章所有机制均为 `1a4ecd7` 代码事实，行号对应 `filter_engine.py`（除非另注）。

### 5.1 System Context / 数据流图

```
                         ┌─────────────────────────────────────────────┐
   上游产物              │        _compute_strategy_pnl (L649-860)       │
 ┌──────────┐           │                                               │
 │ t        │──────────▶│  1. 初始化双曲线 long_pnl/short_pnl = 100.0   │
 │ filtered │──────────▶│     独立本金 long/short_capital = 100.0       │
 │ sig_t    │──────────▶│  2. 空数据守卫 (L697)                         │
 │ all_pairs│──────────▶│  3. 建 pred_map: pair_end → 拟合数据          │
 │ pred_pairs│─────────▶│  4. 主循环 遍历 all_pairs:                    │
 └──────────┘           │       预测方向 → 方向判定 → 入场 →            │
   配置                 │       两阶段离场扫描 → 结算 → 填曲线 → 记录    │
 ┌──────────────┐       │  5. forward-fill 非持仓期                     │
 │ stop_loss_pct│──────▶│                                               │
 │ n_extend     │──────▶│                                               │
 └──────────────┘       └──────────────────────┬────────────────────────┘
                                                │
                                                ▼
                         (long_pnl, short_pnl, trade_records)
                                                │
              ┌─────────────────────────────────┼─────────────────────────────────┐
              ▼                    ▼             ▼                    ▼
     _compute_strategy_display  _add_pnl_traces  _add_cross_pnl_subplot  _add_alignment_subplot
     (统计 + session_state)     (主子图)          (高周期参考)             (同向性子图)
```

### 5.2 输入 / 输出契约

**函数签名（L649-655）：**

```python
def _compute_strategy_pnl(
    t: np.ndarray, filtered: np.ndarray, sig_t: np.ndarray,
    all_pairs: List[Tuple[int, int]], pred_pairs: List[Dict[str, Any]],
    stop_loss_pct: float, n_extend: int = 10,
) -> Tuple[np.ndarray, np.ndarray, List[Dict[str, Any]]]:
```

**输入参数来源表：**

| 参数 | 来源 | 类型 | 含义 |
|---|---|---|---|
| `t` | 上游 `noisy` 时间索引，经 `_compute_filters` 透传 | `np.ndarray` | 均匀时间索引（bar 索引） |
| `filtered` | `_compute_filters()` 产出 | `np.ndarray` | 滤波后价格序列（非原始 `noisy`） |
| `sig_t` | `_schmitt_trigger` → `schmitt["sig"]` | `np.ndarray[int]` | Schmitt 输出：+1(多)/0(中)/-1(空) |
| `all_pairs` | `_find_all_pairs(sig_t)` | `List[Tuple[int,int]]` | 合并后异号段边界 `(first_seg_start, second_seg_start)` |
| `pred_pairs` | `_compute_prediction_pairs()` | `List[Dict]` | 每项 `{"fit_result":{...}, "fit_start":int, "pair_end":int}` |
| `stop_loss_pct` | `cfg["stop_loss_pct"]`（`streamlit_app.py` L282） | `float` | 止损阈值百分比 |
| `n_extend` | `cfg.get("n_ext", 10)`（`streamlit_app.py` L289） | `int` | 预测前推 bar 数 |

**返回值：**

| 返回 | 类型 | 说明 |
|---|---|---|
| `long_pnl` | `np.ndarray` | 长度 = `len(t)`，初值 100.0（L690）。持仓期逐 bar 未实现 PnL；非持仓期为已实现本金水平线 |
| `short_pnl` | `np.ndarray` | 同上结构，做空方向（L691） |
| `trade_records` | `List[Dict]` | 每笔完成交易一条记录；空数据时返回 `[]`（非 `None`，L698） |

**`trade_records` 字段表（L832-841）：**

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | `int` | 交易序号，从 1 自增 |
| `type` | `str` | `"long"` 或 `"short"` |
| `entry_idx` | `int` | 入场 bar 索引 |
| `exit_idx` | `int` | 离场 bar 索引 |
| `entry_price` | `float` | 入场价（= `filtered[entry_idx]`） |
| `exit_price` | `float` | 离场价（= `filtered[exit_idx]`） |
| `return_pct` | `float` | 收益率百分比（= `trade_return * 100`，L839） |
| `exit_reason` | `str` | `"take_profit"` / `"stop_loss"` / `"eod"`（见 §5.7） |

**空数据契约（L697-698）：** 当 `len(all_pairs)==0 or len(pred_pairs)==0` 时，返回全 100.0 的两条曲线与空列表 `[]`。

### 5.3 配置参数

两个配置项直接影响 PnL 计算（as-is 补充 §1）：

| cfg key | 含义 | VIEW_DEFAULTS（state.py） | 函数签名默认 | 调用点取值 | Sidebar 滑块 |
|---|---|---|---|---|---|
| `stop_loss_pct` | 止损阈值 % | `2.0`（state.py:83） | 无（必传参） | `cfg.get("stop_loss_pct", 2.0)`（streamlit_app.py:282） | 0.5-10.0，step 0.1，默认 2.0（sidebar.py:235-238） |
| `n_ext` | 预测前推 bar 数 | `8`（state.py:79） | `n_extend: int = 10`（filter_engine.py:652） | `cfg.get("n_ext", 10)`（streamlit_app.py:289） | 1-50，step 1，默认 `cfg["n_ext"]`（sidebar.py:192） |

其他与 PnL 相关的 cfg 项（控制渲染开关，不改计算本身）：

| cfg key | 类型 | VIEW_DEFAULTS | 含义 |
|---|---|---|---|
| `show_strategy` | bool | `False`（state.py:82） | 是否启用策略 PnL 叠加（`_compute_strategy_display` 的入口守卫，L281） |
| `show_cross_pnl` | bool | `False`（state.py:84） | 是否显示高周期 PnL 参考线 |
| `show_alignment` | bool | `False`（state.py:85） | 是否显示同向性判断子图 |
| `fit_mode` | str | `"parabola"`（state.py:78） | 预测曲线拟合方式（影响上游 `pred_pairs`，非本模块） |

> 关于 `n_ext` 的 VIEW_DEFAULTS（8）与函数签名默认（10）的取值现状，见 §7 LIM-002。

### 5.4 算法描述

算法分为两部分：**pair 检测**（`_find_all_pairs`）与 **PnL 计算主流程**（`_compute_strategy_pnl`）。

#### 5.4.1 pair 检测：`_find_all_pairs`（L520-576）

三步算法：

1. **收集连续非零段（L543-554）**：线性扫描 `sig_t`，对每个 `sig_t[i]!=0` 记录 `(start, end, val)`，`val` 在连续块内恒为 +1 或 -1；遇 `sig_t[i]==0` 终止当前段。扫描时**所有 0 被直接丢弃，不保留 gap 长度信息**。
2. **跨零合并同号段（L559-566）**：判据只有一个——**符号相同即合并**，不看隔了几个 0。`+1,0,+1` → 合并成一段 `+1`；`+1,0,-1` → 不合并（异号）。合并只被反向非零段打断。
3. **异号配对（L568-575）**：相邻异号段配成 `pair = (merged[j].start, merged[j+1].start)`，即 `pair_start` = 第一个信号段起点、`pair_end` = 异号段起点。**方向不编码在 pair 元组内**，留待主流程由 `sig_t[pair_end]` 联合预测方向确定。

**守卫（L539-540, L556-557）：** `n < 3` 返回 `[]`；`< 2` 段返回 `[]`。

**示例（code-spec §3）：**
```
sig_t    = [0, +1, +1, 0, 0, +1, 0, -1, -1, 0, +1]
Segments = [(1,2,+1), (5,5,+1), (7,8,-1), (10,10,+1)]
Merged   = [(1,5,+1), (7,8,-1), (10,10,+1)]      # 前两段同号跨零合并
Pairs    = [(1,7), (7,10)]                        # pair0: +1→-1;  pair1: -1→+1
```

#### 5.4.2 入场逻辑（L729-740）

入场发生在 **`entry_idx = pair_end`**（异号段起点），需**信号方向与抛物线外推方向双确认**：

```
v2      = sig_t[pair_end]                          # L729 入场处信号符号
pred_up = (y_pred[-1] > y_pred[0])                 # 抛物线外推趋势（区间 [pair_end, pair_end+n_extend)）
is_long  = (v2 ==  1 and pred_up)                  # L730
is_short = (v2 == -1 and not pred_up)              # L731
若两者皆非 → 跳过该 pair                           # L733-734

entry_idx   = pair_end                             # L737
entry_price = filtered[entry_idx]                  # L738
若 entry_price 为 NaN 或 ≤ 0 → 跳过该 pair         # L739
```

- 预测方向 `pred_up` 由抛物线在 `[pair_end, pair_end+n_extend)` 区间外推得到；若 `len(y_pred) < 2` 则跳过该 pair（L724）。
- **入场确认来源 = 本级抛物线外推方向**。

#### 5.4.3 离场逻辑（L742-789，两阶段模型）

从 `entry_idx+1` 扫描到 `n-1`：

**Phase 1 — 保护期止损 `[entry+1, entry+n_extend]`（L756-770）：**
- 仅在 `i <= protect_end`（`protect_end = entry_idx + n_extend`）内检查止损。
- 止损相对**预测价 `pred_val`**（非入场价）：
  - 做多：`cur_price < pred_val * (1 - stop_loss_pct/100)`（L764）
  - 做空：`cur_price > pred_val * (1 + stop_loss_pct/100)`（L766）
  - 命中 → `exit_idx = i`，`exit_reason = "stop_loss"`，break（L769-770）。
- `pred_val` 为抛物线在当前 bar 的预测价 `polyval((a,b,c), i - x0)`（`x0` 为 None 时用 `polyval((a,b,c), i)`）；若 `pred_val` 为 NaN 或 ≤ 0，跳过该 bar 的止损检查（L762）。

**Phase 2 — 全程止盈（信号反转，L774-780）：**
- 做多：`sig_t[i] == -1` → 离场（L774）
- 做空：`sig_t[i] == 1` → 离场（L778）
- `exit_reason` 保持默认 `"take_profit"`（L746 设默认），break。

**无确认缓冲的现状说明：** 单根 `sig_t[i]==-1`（做多）即立即触发离场。之所以不产生噪声，是因为 `_schmitt_trigger` 迟滞（L501-511）保证原始信号 +1→-1 之间必经过 0，因此 `sig_t` 中异号连续段之间总有至少一个 0；但 0 本身不触发离场，离场发生在**首个 `sig_t[i]==-1` 的 bar**（新异号段的第一根）。

**边界——无离场触发（L784-789）：**
- 若 `n-1 > entry_idx`：在末 bar 离场，`exit_reason = "eod"`（数据末端，L787）。
- 若 `entry_idx == n-1`（在最后一根入场）：整笔跳过。

#### 5.4.4 收益与曲线构建（L796-829, L843-858）

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

**非持仓期 forward-fill（L843-858）：** 所有交易处理完后，把仍为 100.0 的 bar 用上一已知值兜底填充：
```
last_val = 100.0
for i in range(n):
    if long_pnl[i]==100.0 and i>0 and last_val!=100.0:   # 该 bar 视为"未写入"
        long_pnl[i] = last_val                           # 用上一已知值填充
    if long_pnl[i]!=100.0 or (i==0):
        last_val = long_pnl[i]                           # 更新跟踪值
# short 曲线逻辑完全相同
```
- **首笔交易前**的 bar：保持 100.0（从未写入，`last_val` 停在 100.0）。
- **交易之间**的 bar：已被上一笔离场后填充（L815/L828），forward-fill 仅兜底可能的边缘缺口。

#### 5.4.5 完整流程编号伪代码（`_compute_strategy_pnl`）

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

### 5.5 数据结构

| 结构 | 类型 / 形状 | 生命周期 | 说明 |
|---|---|---|---|
| `long_pnl` / `short_pnl` | `np.ndarray`，长度 `n = len(t)` | 函数内创建并返回 | 双曲线；初值 100.0；持仓期为 `capital*(1+unrealized)`，非持仓期为水平本金线 |
| `long_capital` / `short_capital` | `float` 标量 | 函数内，随主循环递进 | 各方向**已实现（复利）本金**，每笔离场后 `*= (1 + trade_return)`（L813/826） |
| `pred_map` | `Dict[int, Dict]` | 主循环前构建 | 键 = `pair_end`，值 = 该 pair 的拟合数据；用于 O(1) 查预测 |
| `trade_records` | `List[Dict]` | 主循环追加 | 逐笔交易记录，字段见 §5.2 |
| `all_pairs` | `List[Tuple[int,int]]`（输入） | 上游产出 | 异号段边界 `(pair_start, pair_end)` |

**【解读】** `long_pnl`/`short_pnl` 用 `np.full(n, 100.0)` 一次性预分配、事后回填，配合 `pred_map` 的哈希查找，使主循环对每个 pair 都是常数时间定位预测数据，整体复杂度约 O(pair 数 × 平均持仓跨度)。

### 5.6 边界与不变量

**NaN / 非法价格 / 索引越界处理表（现状事实）：**

| 位置 | 检查 | 动作 |
|---|---|---|
| L739 | `np.isnan(entry_price) or entry_price <= 0` | 跳过整个 pair |
| L752-753 | `np.isnan(cur_price) or cur_price <= 0` | 扫描循环内 `continue` 跳过该 bar |
| L762 | `np.isnan(pred_val) or pred_val <= 0` | 跳过该 bar 的止损检查 |
| L792-793 | `np.isnan(exit_price) or exit_price <= 0` | 不记录该笔交易 |
| L539 | `n < 3` | `_find_all_pairs` 返回 `[]` |
| L556 | `< 2` 段 | `_find_all_pairs` 返回 `[]` |
| L697 | `len(all_pairs)==0 or len(pred_pairs)==0` | 返回初值曲线 + 空 trade 列表 |
| L709 | `pair_end not in pred_map` | 跳过该 pair（无预测） |
| L724 | `len(y_pred) < 2` | 跳过该 pair（无法判 pred_up） |
| L785-789 | `exit_idx is None` | `n-1 > entry_idx` 则在 `n-1` 离场（eod）；否则跳过 |

**不变量清单（现状事实）：**

- **两曲线初值恒为 100.0**（L690-691）；非持仓期为水平线（已实现本金）。
- **做多/做空本金独立复利**（L693-694, L813, L826），互不影响。
- **pair 成对性**：`_find_all_pairs` 产出的每个 pair 必为相邻异号段边界 `(first_seg_start, second_seg_start)`（L568-575）。
- **入场恒在 pair_end**，且需信号符号与抛物线方向双对齐（L730-737）。
- **止损仅在保护期 `[entry+1, entry+n_extend]` 检查**（L756）；止盈全程有效（L774-780）。
- **空数据返回 `[]` 而非 `None`**（L698）。
- **确定性**：无随机数、无外部 I/O，相同输入产出完全相同输出。

### 5.7 `exit_reason` 分类（三值）

| 值 | 触发条件 | 行号 |
|---|---|---|
| `"take_profit"` | 默认值。信号反转离场（保护期内或外均可） | L746（默认）, L776/L780（break） |
| `"stop_loss"` | 价格偏离预测轨道超 `stop_loss_pct`，仅保护期检查 | L769-770 |
| `"eod"` | 无任何离场触发 → 在末 bar `n-1` 离场（数据末端 end-of-data） | L787 |

---

## 6. Design Rationale

> 本章是对"现状代码为何如此"的客观解读，采用 As-Built 语调，**不做备选方案对比、不为设计辩护**，
> 只帮助读者理解现有结构的内在逻辑。所有动机性表述均属 **【解读】**。

**【解读】双独立曲线的解耦价值。** `long_pnl` 与 `short_pnl` 各自从 100.0 起独立复利、互不共享权益（L690-694, L813, L826）。
这种结构把做多、做空两个方向的表现解耦：读图时可以分别看到"如果只做多"和"如果只做空"各自的累计收益曲线，
而不会把两个方向混进单一权益曲线里、看不清收益来源。相应地，本版本**不存在**"总组合权益"概念（§4.2 Non-Goal），
两条曲线是两个独立的评估视角，而非一个组合的两条腿。

**【解读】止损基于预测价的含义。** 止损阈值相对抛物线**预测价 `pred_val`** 而非入场价（L763-766）。
抛物线外推给出一条"价格本应沿此走"的参考轨道，价格偏离该轨道超过 `stop_loss_pct` 即判为"方向走错"而提前止损。
其含义是：止损衡量的是"实际价格 vs 预期路径"的偏离，而非"实际价格 vs 成本"的浮亏——这是一条随时间移动的动态参考线，
与常规"相对入场价的固定百分比止损"在语义上不同。

**【解读】止盈与迟滞的配合。** 止盈采用"单根异号信号即离场"的极简判据（L774），本身不含确认缓冲；
但它依赖上游 `_schmitt_trigger` 的迟滞特性（L501-511）——原始信号 +1→-1 之间必经过 0，
故异号信号的出现本身已被 Schmitt 层过滤过一轮。离场判据的简洁，建立在信号层已做去噪的前提之上。

**【解读】入场双确认的作用。** 入场同时要求"信号符号"（`sig_t[pair_end]`）与"抛物线外推方向"（`pred_up`）一致（L730-731），
两者皆非则跳过。这构成一道过滤：只有当离散的 Schmitt 信号与连续的抛物线趋势彼此印证时才建仓，
用两个来源不同的证据降低单一信号误判的概率。

---

## 7. Known Limitations & Future Improvements

> 本章客观陈述现状发现的局限，每条含**现状（事实）+ 影响（严重程度）+ 改进方向（留给后续）**。
> 均为中性描述，不构成缺陷指控。改进方向仅为思路，非既定方案。

### LIM-001 哨兵值 100.0 与"未写入"判据共用

- **现状（事实）**：两条 PnL 曲线初值 = 100.0（L690-691），而 forward-fill 又用 `long_pnl[i]==100.0` 作为"该 bar 未被写入"的判据（L843-858）。二者共用同一数值。
- **影响（有影响，边缘条件）**：若某真实交易恰好使 PnL 回到 100.0（净收益约为 0，即 `unrealized/trade_return ≈ 0`），该 bar 会被 forward-fill 视为"未写入"而被上一已知值覆盖。在浮点价格下精确等于 100.0 的概率极低，故实际触发少见，但边缘条件成立。
- **改进方向（留给用户）**：可将"是否已写入"与"初始资本 100.0"解耦——例如用独立布尔掩码 `written = np.zeros(n, bool)` 标记写入位，或曲线初始化为 `np.nan`、在 forward-fill 后统一回填。

### LIM-002 `n_ext` UI 默认 8 与函数签名默认 10 不一致

- **现状（事实）**：`n_extend` 在函数签名的默认值为 10（filter_engine.py:652），调用点 `cfg.get("n_ext", 10)` 的回退值也是 10（streamlit_app.py:289）；但 VIEW_DEFAULTS（state.py:79）与 sidebar 初始值均为 8。用户在 UI 中看到的有效默认是 8。
- **影响（有影响，一致性）**：正常路径下 cfg 始终携带 `n_ext=8`，用户实际使用 8，无功能异常；但当 cfg 缺失 `n_ext` 键时（如直接调用函数或 cfg 未初始化），会回退到 10，与 UI 默认不一致。这是"同一参数存在两个默认来源"的一致性问题。
- **改进方向（留给用户）**：统一默认值来源——例如让函数签名默认与 VIEW_DEFAULTS 对齐，或去掉调用点的字面回退值改为强制读取配置常量。

### LIM-003 止损仅在保护期生效

- **现状（事实）**：止损检查被 `if i <= protect_end` 限制在保护期 `[entry+1, entry+n_extend]` 内（L756）；保护期之外只剩全程信号反转止盈（L774-780），不再有任何基于价格偏离的止损。
- **影响（有影响，取决于持仓时长）**：对持仓跨度长于 `n_extend` 的交易，保护期后价格即便大幅不利偏离预测轨道，也只能等到信号反转（或数据末端 eod）才离场。持仓越长，尾段无止损保护的区间越大。
- **改进方向（留给用户）**：如需全程止损保护，可考虑将偏离型止损扩展到保护期之外，或引入独立于保护期的全程离场判据。

### LIM-004 无组合净值 / 无交易成本

- **现状（事实）**：做多、做空为两条独立曲线、两个独立本金变量，无合并权益（§4.2、L690-694）；收益公式为纯价差比率，无手续费/滑点/融资项（L796-799）。
- **影响（设计边界，非缺陷）**：当前输出适合"分方向观察策略贡献"的可视化目的；若需评估"实际可交易的组合净值"或"计入成本后的真实收益"，现状数值会系统性偏乐观（无成本）且无法直接得到组合层面的净值。
- **改进方向（留给用户）**：如有组合评估需求，可在渲染层或新增函数中合并双曲线为组合净值，并在收益公式中引入成本项。

### FUT-001 每次 rerun 全量重算

- **现状（事实）**：每次 Streamlit 交互触发完整 rerun，整条链路（含 `_compute_strategy_pnl`）从头重算，无逐 rerun 缓存（as-is 补充 §3）。
- **影响（性能，通常无感）**：对当前数据规模开销可接受；数据量或 pair 数显著增大时，重复计算成本会线性上升。
- **改进方向（留给用户）**：可对纯函数结果按输入指纹做记忆化（如 `st.cache_data`），在输入未变时跳过重算。

### FUT-002 跨周期 PnL 数据依赖依赖 session 先后顺序

- **现状（事实，标注待复核）**：跨周期参考子图依赖高周期视图已在同一 session 中至少计算过一次并写入 `st.session_state[f"_pnl_{tf}"]`（as-is 补充 §3）；`_compute_strategy_pnl` 本体是**单周期纯函数**，此依赖不在其行号覆盖范围内，故本文档无法用 `filter_engine.py` 行号确证其在 `1a4ecd7` 的具体表现。
- **影响（有影响，取决于交互顺序）**：若低周期视图先于高周期视图被渲染，跨周期参考可能读到过期或缺失的高周期 PnL。
- **改进方向（留给用户）**：如需稳健跨周期展示，可显式管理各周期 PnL 的计算/失效顺序，并对读侧做"数据新鲜度"校验。

---

## 8. Cross-Cutting Concerns

### 8.1 错误处理与降级

模块对上游数据缺陷采取**逐点跳过、不崩溃**策略：NaN/非法价格在入场、扫描、止损、结算四处分别被守卫拦截（§5.6 处理表）；
`_find_all_pairs` 对 `n<3` 或段数不足返回 `[]`（L539/556）；主函数对空 `all_pairs`/`pred_pairs` 返回初值曲线与空记录（L697-698）。
任一 pair 数据不完整（无预测、无法判方向、末 bar 入场）时跳过该 pair 而非中断整个计算。

### 8.2 可复现性

`_compute_strategy_pnl` 是**确定性纯函数**：无随机数、无外部 I/O、无隐藏状态。相同输入（`t`、`filtered`、`sig_t`、`all_pairs`、`pred_pairs`、`stop_loss_pct`、`n_extend`）
产出完全相同的输出。每次 rerun 全量重算（§8.3），因此不存在"上次残留状态影响本次结果"的问题——同一输入永远得到同一双曲线与同一批交易记录。

### 8.3 跨周期 session_state 数据流

- 每个视图（v0..v3）独立计算自己的 PnL，并把 `{dates, t, long_pnl, short_pnl, trade_records}` 存入 `st.session_state[f"_pnl_{tf}"]`（`streamlit_app.py` L308-312）。
- 下一次低周期视图 rerun 时，通过 `_align_pnl_to_current_tf`（filter_engine.py L867-974）读取高周期视图已存的 PnL，按最近日期 forward-fill 对齐到当前周期时间轴，产出 `{aligned_long, aligned_short, entry_markers, exit_markers}` 供 `_add_cross_pnl_subplot` 使用（as-is 补充 §2e）。
- 该机制要求高周期 PnL 在同一 session 中至少已计算一次（相关现状局限见 §7 FUT-002）。

### 8.4 可观测性

模块本身不打印计算日志；可观测性体现在**渲染层**：`_compute_strategy_display` 计算并展示胜率、多/空/合计收益、最大回撤三段摘要（`streamlit_app.py` L293-307），
逐笔交易通过入/出场标记、收益标注、离场原因符号（stop_loss → `x`，take_profit → `circle`）在主图与跨周期子图上可视化（as-is 补充 §2b/§2c）。
每笔交易的进出场索引、价格、收益率、离场原因均保存在 `trade_records` 中，可追溯到具体 bar。

---

## 9. Appendix

### 9.1 已有测试覆盖（引 `docs/test_cases.md`）

| 测试类 | 用例编号 | 覆盖内容 |
|---|---|---|
| `TestComputeStrategyPnL` | TC-STRAT-009 ~ 015 | `_compute_strategy_pnl` 核心逻辑 |
| `TestRenderExitMarker` | TC-CHART-009 ~ 017 | 离场标记渲染 |
| `TestRenderPnLCurves` | TC-CHART-029 ~ 032 | PnL 双曲线渲染 |
| `TestCrossPnlSubplot` | TC-CHART-060 ~ 064 | 跨周期 PnL 子图 |

> 说明：以上测试类与用例编号来自对 `docs/test_cases.md` 的盘点，用于指示已有覆盖范围。

### 9.2 术语表

| 术语 | 含义 |
|---|---|
| **pair** | `_find_all_pairs` 产出的相邻异号信号段边界 `(pair_start, pair_end)` |
| **pair_start / pair_end** | pair 中第一个信号段起点 / 异号段起点；入场恒在 `pair_end` |
| **Schmitt 触发 / 迟滞** | 上游对加速度阈值的双阈值触发，保证 +1↔-1 之间必经 0 |
| **保护期** | 入场后 `[entry+1, entry+n_extend]` 区间，唯一检查止损的窗口 |
| **pred_val** | 抛物线在某 bar 处的预测价，止损参考轨道 |
| **pred_up** | 抛物线外推趋势方向 `y_pred[-1] > y_pred[0]`，入场方向确认之一 |
| **unrealized** | 持仓期逐 bar 未实现收益率 |
| **trade_return** | 完成交易的已实现收益率；做多 `(exit-entry)/entry`，做空 `(entry-exit)/entry` |
| **capital（long/short）** | 各方向已实现复利本金，离场后 `*=(1+trade_return)` |
| **forward-fill** | 对未写入 bar 用上一已知值兜底填充 |
| **eod** | end-of-data，无离场触发时在数据末端离场 |

### 9.3 文件索引

| 文件 | 相关行 |
|---|---|
| `filter/services/filter_engine.py` | 649-860（`_compute_strategy_pnl`）、520-576（`_find_all_pairs`）、434/501-511（`_schmitt_trigger`）、867-974（`_align_pnl_to_current_tf`） |
| `filter/streamlit_app.py` | 279-313（`_compute_strategy_display`）、428-468（`_add_pnl_traces`）、646-727（main 链路 steps 3-10） |
| `filter/components/charts.py` | 262-275 / 278-307（进出场标记）、351-396（`_add_cross_pnl_subplot`）、399-461（`_add_alignment_subplot`） |
| `filter/state.py` | 70-87（VIEW_DEFAULTS） |
| `filter/components/sidebar.py` | 153 / 192 / 235-243（n_ext、stop_loss_pct 滑块） |

### 9.4 本文档诚实边界

- 跨周期 PnL 数据依赖（§7 FUT-002、§8.3）中"过期/缺失"的具体表现在 `1a4ecd7` **无 `_compute_strategy_pnl` 行号支撑**，因该函数是单周期纯函数；相关表述据 as-is 补充转述，标注为"待复核"。
- 本文档所有代码事实与 code-spec 严格一致；§6 Design Rationale 与个别 **【解读】** 标注段落含推断性归纳，机制描述本身均为现状事实。
