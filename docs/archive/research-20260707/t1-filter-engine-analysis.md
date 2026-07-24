# filter_engine.py 逐函数深度分析

> **分析日期**: 2026-07-07
> **源文件**: `/Users/xfpan/claude/filter_research/filter/services/filter_engine.py`
> **分析方法**: 逐行源码阅读，追踪数据依赖链与状态传递

---

## 数据流全景

```
价格序列 (noisy)
  │
  ├─[滤波]──→ filtered (e.g., SMA/EMA/Kalman/Butterworth...)
  │
  ├─[np.gradient]──→ v = d(filtered)/dt  (速度/动量)
  ├─[np.gradient]──→ a = d(v)/dt         (加速度)
  │
  ├─[_schmitt_trigger(v, a)]──→ sig, eps, sigma_v, mu_v
  │
  ├─[_find_all_pairs(sig)]──→ all_pairs [(start, end), ...]
  │
  ├─[_fit_parabolic / _fit_physics_parabola]──→ pred_pairs [{fit_result, ...}, ...]
  │
  ├─[_compute_strategy_pnl]──→ long_pnl, short_pnl, trade_records
  │
  └─[_align_pnl_to_current_tf + _compute_holding_masks]──→ 跨周期对齐
```

**关键依赖**: 整条链路是**严格串行**的。上游计算的任何变化都会级联到下游。

---

## 1. `_schmitt_trigger(v, a, ewma_span, k_eps, sigma_min)`

**位置**: filter_engine.py 行 434-517

### 1.1 输入参数 v 和 a 的来源

v 和 a 并非直接来自原始价格，而是来自**滤波后**的价格序列：

```python
# streamlit_app.py 行 254-255
_v = np.gradient(filtered, t)     # 一阶导数 → 速度/动量
_a = np.gradient(_v, t)           # 二阶导数 → 加速度
```

- `filtered` 是原始价格经过某种滤波（SMA/EMA/Kalman/Butterworth/LOWESS 等）后的序列
- `np.gradient` 使用**二阶中心差分**，但边界点使用一阶前向/后向差分
- **这意味着**: v 和 a 的边界值（序列首尾各 1 个点）使用了不同的差分公式，但其附近值也受影响（中心差分需要邻域点）

### 1.2 eps（动态阈值）的计算

```python
# 行 469-477: EWMA 波动率估计 (σ_v)
alpha = 2.0 / (ewma_span + 1)
mu_v[0] = v[0]
sigma_v[0] = 0.0
for i in range(1, n):
    mu_v[i] = alpha * v[i] + (1 - alpha) * mu_v[i - 1]
    sigma_v[i] = sqrt(alpha * (v[i] - mu_v[i])² + (1 - alpha) * sigma_v[i - 1]²)

# 行 480: 自适应死区
eps_t = k_eps * max(sigma_v, sigma_min)
```

**eps 的数据依赖分析**:

|| 依赖项 | 依赖范围 | 说明 |
||---|---|---|
| `sigma_v[i]` | v[0..i] 全部 | EWMA 递归，每个 sigma_v[i] 依赖历史上所有 v[j] (j <= i) |
| `mu_v[i]` | v[0..i] 全部 | 同上，EWMA 递归 |
| `eps_t[i]` | v[0..i] 全部 | 通过 sigma_v[i] 间接依赖 |

- **不是滚动窗口**，而是**指数衰减的全局依赖**。EWMA 的衰减因子 `alpha = 2/(span+1)`，对 `span=60`，约 3.3% 的权重给当前点，96.7% 权重给历史
- 序列**开头 60 个点之后**，初始条件 sigma_v[0]=0 的影响基本消失
- **序列中间任意位置的 v 变化会改变该位置之后的所有 sigma_v 和 eps**

### 1.3 sigma_v 的计算

- **不是滚动窗口** — 是 EWMA 递归（指数加权移动方差）
- 每个 `sigma_v[i]` 由 `v[i]` 和 `sigma_v[i-1]` 递归计算
- 等价于对所有历史 v 做指数衰减加权，越近的 v 权重越大
- 初始值 `sigma_v[0] = 0.0`，前几个点的 sigma_v 受初始值影响

### 1.4 sig（信号）的状态机逻辑

这是一个带**磁滞（hysteresis）**的三态状态机：

```
状态转换规则 (行 483-514):

                   a > +eps AND v > 0
     ┌────── 0 ──────────────────────→ +1 (做多)
     │         a < -eps AND v < 0
     │         ──────────────────────→ -1 (做空)
     │
     │  a < -eps
     ├── +1 ─────────────────────────→ 0  (多翻观望)
     │
     │  a > +eps
     └── -1 ─────────────────────────→ 0  (空翻观望)
```

关键特性：
- **0 → +1** 需要 v>0 的**额外方向确认**（防止假突破）
- **0 → -1** 同样需要 v<0 的方向确认
- **±1 → 0** 只需要 a 反向穿过 eps，**不需要 v 确认**（快速离场）
- NaN 值不改变状态，仅累积 duration

### 1.5 关键问题：边界效应分析

**问题**: 当输入序列 v/a 在窗口右边界附近发生变化时（因为滤波边界效应），eps 和 sig 在序列中间位置会变化吗？

**答案**: **会变化，而且影响范围很大。**

分析如下：

| 变化场景 | 受影响的 eps 范围 | 受影响的 sig 范围 | 原因 |
|---|---|---|---|
| v[K] 变化 (K 在边界附近) | eps[K:] 全部 | sig[K:] 全部 | EWMA sigma_v 从 K 向后的所有值都变了 |
| v[K] 变化 (K 在序列中间) | eps[K:] 全部 | sig[K:] 全部 + 可能向前传递（状态机） | sig 的状态机会将状态变化向后传递 |
| filtered 右边界变化 → v[-N:] 变化 | eps[-N:] → 最终 eps[-1] 变化 | sig[-N:] → 最终 sig[-1] 变化 | 新边界值改变了 v[-N:]，eps 传播到末尾 |

**根本原因**: `sigma_v` 使用 EWMA 是**因果的**（只向前看），所以位置 `i` 之前的 v 变化不影响 `eps[i]`，但位置 `i` 之后的 v 变化会改变 `eps[i:]` 的全部值。**sig 的状态机也有后向传递性** — 如果 `sig[i]` 因 `eps[i]` 变化而改变，则从 `i` 开始的所有后续 sig 都可能改变（因为状态机是从当前位置继续的）。

**依赖标注**: ⚠️ 全局依赖 — 任何位置的 v 变化都会级联影响到该位置之后的所有 eps 和 sig。

---

## 2. `_find_all_pairs(sig)`

**位置**: filter_engine.py 行 520-576

### 2.1 配对检测逻辑

分三步进行：

**Step 1: 收集非零段** (行 544-554)
```python
segments = [(start, end, val), ...]
# 例如 sig = [0,0,1,1,1,0,0,-1,-1,0] → [(2,4,1), (7,8,-1)]
```
只记录连续的相同非零值段。0 值被跳过。

**Step 2: 合并相邻同号段** (行 559-566)
```python
# 合并规则: 如果下一段与上一段符号相同 → 合并为一个段
# +1,0,+1 → 一个连续多头段 (包含中间的 0 观望)
# 实际上，由于 Step 1 只收集非零段，同号段中间的 0 已经被跳过了，
# 所以合并的是相邻的同号非零段。
# 例如: sig = [1,1,0,0,1,1] → segments = [(0,1,1), (4,5,1)]
# → merged = [(0,5,1)]  合并为一个大段
```
这一步的关键语义是：**中间的 0 值被视为同方向延续，而非离场**。

**Step 3: 相邻异号段配对** (行 569-575)
```python
for j in range(len(merged) - 1):
    s1, e1, v1 = merged[j]
    s2, e2, v2 = merged[j + 1]
    if v1 != v2:
        pairs.append((s1, s2))  # 结束于相反信号的入口
```
配对的结束点是 `s2`（相反信号的**第一个** index），**不是** e1（原信号的最后一个 index）。

### 2.2 配对的起止条件

- **pair_start**: 同号非零段的第一个 bar 的 index (`s1`)
- **pair_end**: **相反信号**出现时的 index (`s2`)，即方向改变边界

示例：
```
sig  = [0, 1, 1, 1, 0, -1, -1, 1, 1]
         ↑s1    e1   ↑s2  e2  ↑s3 e3
pairs = [(1, 5), (5, 7)]
```
第一个 pair (1,5)：多头从 index=1 开始，在 index=5 出现空头信号时结束。
第二个 pair (5,7)：空头从 index=5 开始，在 index=7 出现多头信号时结束。

### 2.3 关键问题：sig 序列中间变化的影响

**问题**: 如果 sig 序列中间有一个 bar 从 0 变成了 1（因为窗口滑动导致信号变化），会对整个配对列表产生什么影响？

**答案**: 影响是**局部但可能有级联效应**。

分析各种情况：

| 变化 | 影响范围 | 具体影响 |
|---|---|---|
| sig[i]: 0→1，且前后都是 0 | 可能创建新的非零段 | 如果新段与相邻同号段合并，可能改变配对 |
| sig[i]: 0→1，且前一个非零段也是 +1 | 前一个段被延长（合并） | 前一配对的 pair_end 可能不变（因为 pair_end=s2 是下一个异号段的 index），但如果延长的段覆盖了原来的 s2，则该配对消失 |
| sig[i]: 1→0，且是整个非零段中的点 | 非零段可能分裂为两个 | 产生新的配对或破坏原有配对 |
| sig[K] 从 0 变 1，K 是原来某个 pair 的 pair_end | 该 pair 的终点变为 K 之前的某个点 | PnL 计算中的入场点会变化 |

**最坏情况**: 如果中间一个 bar 的变化导致一个 merged 段被拆分或合并，则**整个 merged 列表可能重建**，所有后续配对都可能重新计算。但因为 sig 的状态机是递推的，中间一个 sig 变化必然伴随状态机重置，后续 sig 也全部变化，所以 `_find_all_pairs` 看到的 sig 数组已经完全不同了。

**依赖标注**: ✅ 局部依赖（函数本身是无状态的纯函数，只依赖 sig 数组的结构特征），但 ⚠️ 因为 sig 来自 `_schmitt_trigger`（全局依赖），所以本质上整个配对列表在窗口滑动后可能完全不同。

---

## 3. `_fit_parabolic` 和 `_fit_physics_parabola`

**位置**: filter_engine.py 行 579-605 和 608-646

### 3.1 两个拟合函数的对比

| 特性 | `_fit_parabolic` | `_fit_physics_parabola` |
|---|---|---|
| 数学模型 | y = a·x² + b·x + c | y = a·(x-x₀)² + y₀ |
| 自由参数 | 3 个 (a, b, c) | 1 个 (a) |
| 顶点 | 自由（由数据决定） | **锚定**在 segment 终点 (x₀=x[end], y₀=y[end]) |
| 最小二乘 | 标准 polyfit(x, y, 2) | 仅拟合曲率 a，顶点固定 |
| 使用条件 | fit_mode != "parabola" (默认) | fit_mode == "parabola" |

**`_fit_parabolic`**: 标准的二阶多项式拟合。给 `np.polyfit` 传入 `(x_seg, y_seg, 2)`，返回 [a, b, c] 三个系数。这是最通用的二次曲线拟合，顶点位置完全由数据决定。

**`_fit_physics_parabola`**: 物理抛物线拟合。将 segment 的**终点**强制设为抛物线顶点 `(x0, y0)`。然后通过最小二乘拟合唯一的自由参数 a（曲率）：
```python
dt = x_seg - x0  # 所有点相对顶点的 x 偏移（全部 ≤0，因为 x0 是终点）
dy = y_seg - y0  # 所有点相对顶点的 y 偏移
# 最小二乘: minimize Σ(a·dt² - dy)²
a = Σ(dt² · dy) / Σ(dt⁴)
```
- `b` 恒为 0（因为在以顶点为原点的坐标系中，一次项系数为 0）
- `c` = y₀（顶点 y 坐标）
- 这种拟合强制了"趋势正在顶点处转折"的物理直觉

### 3.2 拟合使用的数据点

两个函数都**仅使用 pair 范围内的数据点**：

```python
x_seg = x[start:end + 1]  # 只取 [pair_start, pair_end]
y_seg = y[start:end + 1]
```

不包含全局数据。pair 范围由 `_find_all_pairs` 确定，即从同方向非零信号段开始，到反方向信号出现为止。

### 3.3 预测延伸（n_extend）逻辑

n_extend 的预测不在拟合函数中，而在 `_compute_strategy_pnl` 中计算（行 718-726）：

```python
x_pred = np.arange(pair_end, pair_end + n_extend)

if x0 is not None:  # _fit_physics_parabola 的结果
    y_pred = np.polyval((a, b, c), x_pred - x0)  # 以顶点为坐标原点
else:  # _fit_parabolic 的结果
    y_pred = np.polyval((a, b, c), x_pred)       # 直接外推

pred_up = y_pred[-1] > y_pred[0]  # 判断预测方向：曲线上翘还是下弯
```

n_extend 默认为 10。预测方向 `pred_up` 决定开仓方向：
- 做多条件：sig[pair_end] == 1 **且** pred_up == True
- 做空条件：sig[pair_end] == -1 **且** pred_up == False

### 3.4 关键问题：窗口滑动影响

`_fit_parabolic` 和 `_fit_physics_parabola` 本身是纯函数，仅在 `[start, end]` 范围内做最小二乘。它们本身不依赖全局数据。

**但是**：它们的输入 `start` 和 `end` 来自 `all_pairs`（由 `_find_all_pairs(sig)` 产生）。sig 的窗口依赖性（见第 1 节）意味着 pair 范围在窗口滑动后会改变，从而导致拟合使用不同的数据点，得出不同的拟合参数。

**依赖标注**: ✅ 局部依赖（拟合本身仅依赖 [start, end] 范围内的数据），但调用链上游的 sig 是 ⚠️ 全局依赖，因此拟合结果在窗口滑动后也会变化。

---

## 4. `_compute_strategy_pnl(t, filtered, sig, all_pairs, pred_pairs, stop_loss_pct, n_extend)`

**位置**: filter_engine.py 行 649-860

### 4.1 做多和做空的入场/离场条件

**入场条件** (行 728-731):
```python
v2 = sig_t[pair_end]          # pair_end 处的信号值
pred_up = y_pred[-1] > y_pred[0]  # 预测曲线上翘

is_long  = (v2 == 1 and pred_up)     # sig 做多 + 预测上翘
is_short = (v2 == -1 and not pred_up) # sig 做空 + 预测下弯
```

需要**两个条件同时满足**：信号方向要与预测曲线方向一致。如果 sig=+1 但预测曲线下弯，则不交易（信号和预测矛盾）。

**离场条件** (行 742-789):

分两个阶段：

| 阶段 | 范围 | 止损检查 | 止盈检查 |
|---|---|---|---|
| **预测保护期** | [entry+1, entry+n_extend] | 有（价格跌破/涨破预测值） | 无 |
| **趋势跟踪期** | [entry+n_extend+1, 末尾] | 无 | 有（sig 反转） |

**离场优先级**: 预测保护期内先检查止损，触发则立即离场。如果没触发止损，继续检查 sig 反转。如果都没触发，持有到数据末尾（eod）。

```python
# 止损逻辑 (行 756-771)
if is_long:
    stop_hit = cur_price < pred_val * (1 - stop_loss_pct / 100.0)
    # 做多: 当前价 低于 预测价*(1-stop%) → 止损
else:
    stop_hit = cur_price > pred_val * (1 + stop_loss_pct / 100.0)
    # 做空: 当前价 高于 预测价*(1+stop%) → 止损

# sig 反转止盈 (行 774-781)
if is_long and sig_t[i] == -1:   # 做多时出现做空信号 → 止盈离场
    exit_idx = i
if is_short and sig_t[i] == 1:   # 做空时出现做多信号 → 止盈离场
    exit_idx = i
```

### 4.2 止损逻辑细节

- **止损价计算**: 基于多项式拟合的预测值 `pred_val`，而非基于入场价
  - 做多止损价 = `pred_val * (1 - stop_loss_pct/100)` — 曲线下方 stop_loss_pct%
  - 做空止损价 = `pred_val * (1 + stop_loss_pct/100)` — 曲线上方 stop_loss_pct%
- **仅保护期生效**: 止损只在 `i <= protect_end`（即 entry + n_extend）内检查
- **保护期后**: 止损关闭，依赖 sig 反转止盈或 eod 离场

### 4.3 PnL 曲线计算方式

两条独立的累积收益曲线，从 **100** 开始：

```python
long_pnl  = np.full(n, 100.0)   # 做多曲线
short_pnl = np.full(n, 100.0)   # 做空曲线

long_capital  = 100.0  # 做多已实现本金（随交易累积增长）
short_capital = 100.0  # 做空已实现本金
```

对于每笔交易：

1. **持仓期间**: 画浮动盈亏
   ```python
   # 做多
   unrealized = (cur_price - entry_price) / entry_price
   long_pnl[i] = long_capital * (1 + unrealized)

   # 做空
   unrealized = (entry_price - cur_price) / entry_price
   short_pnl[i] = short_capital * (1 + unrealized)
   ```

2. **离场后**: 更新已实现本金，将后续 bar 填充为已实现值
   ```python
   long_capital *= (1 + trade_return)
   for i in range(exit_idx + 1, n):
       long_pnl[i] = long_capital  # 水平直线
   ```

3. **前向填充** (行 843-858): 非持仓期维持上一个值不变，画成水平直线
   ```python
   last_val = 100.0
   for i in range(n):
       if long_pnl[i] == 100.0 and i > 0 and last_val != 100.0:
           long_pnl[i] = last_val
       if long_pnl[i] != 100.0 or (i == 0):
           last_val = long_pnl[i]
   ```
   注意：初始值 100.0 被用作"哨兵值"。如果某 bar 的 PnL 正好等于 100.0（价格回到入场价），前向填充逻辑可能出错。这是一个潜在的 bug。

### 4.4 关键问题：sig 值变化对 PnL 曲线的影响

**问题**: 如果回测前进一个 bar 后，之前某个 bar 的 sig 值变了，PnL 曲线会如何变化？

**答案**: PnL 曲线会**完全重构**。

分析各环节：

| 变化的环节 | 导致的结果 |
|---|---|
| `sig[i]` 变化 | `_find_all_pairs` 产生不同的配对列表 |
| `all_pairs` 变化 | 拟合的 pair 范围变了，`fit_result` 不同 |
| `fit_result` 变化 | `pred_up` 方向可能翻转 |
| `pred_up` 翻转 | `is_long`/`is_short` 判断可能改变 |
| 配对列表完全不同 | 交易入场/离场 bar 完全改变 |
| 入场/离场改变 | `trade_return`、`long_capital`、`short_capital` 全部不同 |
| 本金历史改变 | `long_pnl`、`short_pnl` 从 100 重新累积计算 |

**结论**: 一个 bar 的 sig 值变化不是局部修补，而是**整条 PnL 曲线全部重新计算**。long_pnl 和 short_pnl 的每一个值都可能改变。

此外，**前向填充逻辑（行 843-858）加剧了这种不稳定性**：即使某段曲线在逻辑上可以保持，前向填充的哨兵值机制也会使"未持仓区"的值依赖之前所有交易的结果。

**依赖标注**: ⚠️ 全局依赖 — PnL 曲线的每个点依赖所有上游输入（sig, all_pairs, pred_pairs, filtered），任何一个变化都可能导致整条曲线重建。

---

## 5. `_align_pnl_to_current_tf(higher_dates, higher_long_pnl, higher_short_pnl, higher_trades, current_dates)`

**位置**: filter_engine.py 行 867-972

### 5.1 跨周期对齐逻辑

将**高周期**（日线/周线）的 PnL 对时到**低周期**（60分钟/日线）的时间轴上：

```python
# 对当前周期的每个 bar i
for i in range(n):
    mask = hd <= cd[i]           # 找高周期中 ≤ 当前bar时间的所有日期
    if not mask.any():
        continue
    j = np.max(np.where(mask)[0])  # 取最近的一个高周期bar
    aligned_long[i]  = higher_pnl_long[j]
    aligned_short[i] = higher_pnl_short[j]
```

**对齐方式**: **前向填充（forward fill）** — 每个当前周期的 bar 取时间上最近的、不晚于它的高周期 bar 的值。

例如：日线 PnL = [100, 102, 105] 对应日期 [D1, D2, D3]，低周期是每 60 分钟一根 bar，D1 内的所有 60 分钟 bar 都取日线 PnL=D1 的值。

### 5.2 entry_markers 和 exit_markers

**entry_markers**: `[(bar_idx, trade_type, pnl_val), ...]`
- `bar_idx`: 当前周期中，与高周期 entry 时间对齐的 bar index
- `trade_type`: "long" 或 "short"
- `pnl_val`: 入场时该方向 PnL 曲线的值

**exit_markers**: `[(bar_idx, trade_type, pnl_val, return_pct, exit_reason), ...]`
- 多了 `return_pct`（该笔交易收益率%）和 `exit_reason`（stop_loss/take_profit/eod）

```python
# 入场标记：找到 ≤ entry_time 的最近低周期 bar
entry_mask = cd <= entry_time
entry_bar = int(np.max(np.where(entry_mask)[0]))

# 离场标记：找到 ≤ exit_time 的最近低周期 bar
exit_mask = cd <= exit_time
exit_bar = int(np.max(np.where(exit_mask)[0]))
```

### 5.3 关键问题：窗口滑动影响

- `higher_pnl_long` 和 `higher_pnl_short` 来自上游的高周期 PnL，如果高周期也使用滑动窗口计算，则其 PnL 曲线也会变化
- `_align_pnl_to_current_tf` 本身是**纯映射函数**，只有时间对齐逻辑
- 但如果高周期的 PnL 数据本身就变了，对齐结果自然完全改变

**依赖标注**: ✅ 局部依赖（纯时间对齐函数，对每个 bar 做独立的前向填充查询），但输入 `higher_pnl_long/short` 来自上游（⚠️ 全局依赖的 `_compute_strategy_pnl`）。

---

## 6. `_compute_holding_masks(n, entry_markers, exit_markers)`

**位置**: filter_engine.py 行 975-1021

### 6.1 持仓遮罩计算逻辑

从高周期的 entry/exit markers 构建低周期上的持仓区间遮罩：

```python
# 按类型分组
long_entries  = sorted([b for b, t, _ in entry_markers if t == "long"])
long_exits    = sorted([b for b, t, _, _, _ in exit_markers if t == "long"])

# 配对：每个 entry 找下一个 exit
for e_bar in long_entries:
    later_exits = [x for x in long_exits if x > e_bar]
    x_bar = later_exits[0] if later_exits else n_bars - 1  # 没找到exit → 持仓到末尾
    long_mask[e_bar:min(x_bar + 1, n_bars)] = True
```

**配对逻辑**: 对于每个入场 bar，在低周期上找它之后的第一个同类型离场 bar。如果没找到（持仓到数据末尾），则遮罩延伸到 n_bars-1。

### 6.2 long_mask 和 short_mask 的含义

- **long_mask[i] = True**: 在低周期的 bar i 处，高周期处于做多持仓状态
- **short_mask[i] = True**: 在低周期的 bar i 处，高周期处于做空持仓状态
- 两个 mask 可以同时为 True（做多和做空都有持仓）
- 两个 mask 可以同时为 False（高周期空仓）

这些 mask 在可视化中用于**高亮标注**低周期图表上高周期的持仓区间，实现"同向性"（alignment）分析：比较低周期和高周期的持仓方向是否一致。

### 6.3 关键问题：窗口滑动影响

`_compute_holding_masks` 本身是纯函数，仅依赖 entry_markers 和 exit_markers。但 entry/exit markers 来自 `_align_pnl_to_current_tf`，后者又依赖高周期的 PnL 和 trade_records。

如果高周期的 PnL 在窗口滑动后改变：
- trade_records 变化 → entry_markers/exit_markers 的 bar 位置和数量变化
- holding masks 的 True/False 区间完全重建

**依赖标注**: ✅ 局部依赖（函数本身是对 markers 的纯机械映射），但 markers 来源有 ⚠️ 全局依赖。

---

## 汇总：所有"会导致动态变化"的计算环节

| 函数/环节 | 依赖类型 | 影响范围 | 变化触发条件 | 级联影响 |
|---|---|---|---|---|
| **底层滤波** (SMA/EMA/Kalman...) | ⚠️ 全局 | 整个 filtered 数组 | 窗口滑动、添加新数据 | 所有下游函数 |
| **v = gradient(filtered)** | ⚠️ 全局 | v 的全部值 | filtered 任何点变化 | a, sigma_v, eps, sig |
| **a = gradient(v)** | ⚠️ 全局 | a 的全部值 | v 任何点变化 | sig |
| **sigma_v (EWMA 方差)** | ⚠️ 全局 | sigma_v[i:] | v[i] 变化 | eps[i:], sig[i:] |
| **eps (自适应阈值)** | ⚠️ 全局 | eps[i:] | sigma_v[i] 变化 | sig[i:] |
| **sig (施密特信号)** | ⚠️ 全局 + 🔄 状态传递 | sig[i:] 全部 | eps[i] 或 a[i] 或 v[i] 变化 | all_pairs, pred_pairs, PnL |
| **_find_all_pairs** | ✅ 局部 (但输入 sig 是全局) | 整个配对列表 | sig 数组变化 | pred_pairs, PnL |
| **_fit_parabolic** | ✅ 局部 | 单个 pair 的拟合参数 | pair 范围变化 | pred_up, PnL |
| **_fit_physics_parabola** | ✅ 局部 | 单个 pair 的拟合参数 | pair 范围变化 | pred_up, PnL |
| **pred_up 方向** | ✅ 局部 | 单个交易的入场方向判断 | 拟合参数变化 | 是否交易、做多/做空 |
| **_compute_strategy_pnl** | ⚠️ 全局 | long_pnl 和 short_pnl 全部值 | sig, all_pairs, pred_pairs, filtered 任一变化 | 交易记录、PnL 曲线 |
| **_align_pnl_to_current_tf** | ✅ 局部 | 对齐后的 PnL 数组 | 高周期 PnL 变化 | holding masks |
| **_compute_holding_masks** | ✅ 局部 | long_mask/short_mask 数组 | entry/exit markers 变化 | 对齐可视化 |

### 影响链拓扑

```
窗口右边界新数据
  │
  ├── filtered 全局变化 (卷积/EWMA 的边界效应)
  │     │
  │     ├── v 变化 (gradient 使用邻域)
  │     │     │
  │     │     ├── a 变化
  │     │     └── sigma_v 变化 (EWMA 递归 → i 后全部)
  │     │           │
  │     │           └── eps 变化 (i 后全部)
  │     │                 │
  │     │                 └── sig 变化 (i 后全部 + 状态机传递)
  │     │                       │
  │     │                       ├── all_pairs 重构 (pair 范围改变)
  │     │                       │     │
  │     │                       │     ├── _fit_parabolic/_fit_physics 对新区间拟合
  │     │                       │     │     │
  │     │                       │     │     └── pred_up 可能翻转
  │     │                       │     │
  │     │                       │     └── pair 数量可能增减
  │     │                       │
  │     │                       └── _compute_strategy_pnl 中 sig 反转止盈条件变化
  │     │
  │     └── filtered 变化 → entry_price/exit_price/cur_price 变化 → PnL 计算变化
  │
  └── 高周期 PnL 变化 → 对齐后的 PnL 变化 → holding masks 变化
```

### 关键发现

1. **最脆弱的环节**: `sigma_v` 的 EWMA 递归和 `sig` 的状态机。一个中间点的变化导致之后所有值都变。

2. **"哨兵值"隐患**: `_compute_strategy_pnl` 中使用初始值 100.0 作为"未持仓"的哨兵值，如果 PnL 恰好回到 100.0，前向填充逻辑会错误识别。建议用 `np.nan` 替代 100.0 作为哨兵，或使用单独的 bool mask 标记持仓区间。

3. **高周期 PnL 存储**: `st.session_state[f"_pnl_{tf}"]` 存储了 dates, long_pnl, short_pnl, trade_records。如果高周期的参数改变（窗口大小、滤波类型），存储的 PnL 就会不同，影响低周期的对齐。

4. **不可增量更新**: 整个计算链路不是"增量"的——窗口滑动后不能只更新最后几个 bar，而是整条链路从头算。这是设计选择，意味着回测时必须接受全量重算的成本。
