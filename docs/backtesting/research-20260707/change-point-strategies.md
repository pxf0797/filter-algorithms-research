# 变化点驱动的交易策略模式研究

> **研究日期**: 2026-07-07
> **前置阅读**: [T4 根因分析](./t4-root-cause-analysis.md)
> **核心约束**: 窗口最右侧 N 个 bar 的变化点不稳定（边缘区），已离开边缘区的历史变化点是稳定的
> **研究目标**: 在"信号边缘不稳定"约束下，设计可执行的交易策略模式

---

## 0. 约束回顾与设计原则

### 0.1 核心约束

从 T4 根因分析可知，回测框架的信号不稳定源于一条放大链：

```
滤波端点效应 → gradient 边界退化 → EWMA sigma_v 全局递归 → Schmitt 状态机 Cascade → PnL 全局重建
```

关键量化事实：
- **SMA/EMA 滤波下边缘区约 2-5 bar**：仅窗口最右侧的少量 bar 的信号可能变化
- **Butterworth 零相位滤波下边缘区可达全窗口**：历史信号完全不可信
- **变化点从"首次出现"到"被锁定"需要等待其离开边缘区**
- **EWMA sigma_v 的全局依赖 + Schmitt 状态机 Cascade 构成"乘法放大效应"**：1 个 bar 的 v 变化可影响 60+ 个 bar 的 eps，再通过状态机传播到序列末尾

### 0.2 设计原则

基于上述约束，所有可执行策略必须遵循两条铁律：

1. **不交易边缘区信号**：窗口最右侧 `edge_width` 个 bar 内的 Sig 翻转不作为入场依据
2. **入场理由消失则离场**：如果某笔交易基于的变化点在后续窗口中消失了，则该笔交易的逻辑基础不复存在

其中 `edge_width` 是滤波器类型的函数：

| 滤波器 | edge_width (bar) | 备注 |
|--------|-----------------|------|
| SMA/EMA/WMA | 2-5 | 最稳定，边缘区窄 |
| Kalman | 5-15 | 状态估计需要收敛 |
| LOWESS | 8-15 | 局部加权回归边界非对称 |
| Savitzky-Golay | 8-12 | 多项式拟合边界不稳定 |
| Butterworth | 全窗口 | 不建议用于回测 |

---

## 1. 模式 1：确认后入场（Confirmation-Based Entry）

### 1.1 逻辑描述

**核心思想**：只交易已离开边缘区的稳定变化点。信号一旦被"锁定"（即该 bar 已不在边缘区内），则信号不再因新数据加入而改变，可以作为可靠的入场依据。

**入场条件**：
- 历史区（`bar_index < current_bar - edge_width`）出现 Sig 翻转（0→+1 做多，0→-1 做空）
- 该 bar 已离开边缘区（即该信号是"已锁定"的）

**离场条件**（三选一）：
- Sig 反向翻转（做多时 Sig→-1，做空时 Sig→+1）
- 固定持仓 N bar 后离场
- 止盈/止损触发

**止损**：入场 bar 的极点（做多 = 最低点，做空 = 最高点），即"如果价格跌破入场 bar 的最低点，说明入场判断错误"。

### 1.2 伪代码

```python
def confirmation_entry(sig_sequence, price_data, edge_width, hold_bars=None):
    """
    sig_sequence: 当前窗口的完整 Sig 序列 (长度 N)
    price_data: 对应的 OHLC 数据
    edge_width: 边缘区宽度 (bar)
    hold_bars: 固定持仓 bar 数，None 表示用 Sig 反转离场
    """
    locked_boundary = len(sig_sequence) - edge_width  # 锁定区边界 index
    trades = []
    in_position = False
    entry_bar = None

    for i in range(1, locked_boundary):  # 只遍历锁定区
        if not in_position:
            # 检测锁定区内的 Sig 翻转
            if sig_sequence[i-1] == 0 and sig_sequence[i] == +1:
                in_position = True
                entry_bar = i
                entry_price = price_data['close'][i]
                stop_loss = price_data['low'][i]  # 做多止损 = 入场 bar 最低点
                trades.append({
                    'type': 'long',
                    'entry_bar': i,
                    'entry_price': entry_price,
                    'stop_loss': stop_loss,
                    'signal_locked': True  # 该信号已锁定
                })
        else:
            # 离场判定
            exit_signal = False
            if sig_sequence[i] == -1:
                exit_signal = True  # Sig 反转
            if hold_bars and (i - entry_bar) >= hold_bars:
                exit_signal = True  # 持仓到期
            if price_data['low'][i] < stop_loss:
                exit_signal = True  # 止损触发

            if exit_signal:
                in_position = False
                trades[-1]['exit_bar'] = i
                trades[-1]['exit_price'] = price_data['close'][i]

    return trades
```

### 1.3 延迟代价量化

核心权衡：**立即入场（可能止损）vs 延迟入场（信号确定）**。

定义：
- `E_immediate`：在信号首次出现（边缘区内）时立即入场的期望收益
- `E_delayed`：等待信号锁定后再入场的期望收益
- `P_flip`：边缘区内信号翻转（消失）的概率
- `cost_flip`：信号消失后止损的预期损失（以入场 bar 极点为参考）
- `gain_trend`：信号确认后趋势延续的预期收益

```
E_immediate = (1 - P_flip) * gain_trend + P_flip * (-cost_flip)
E_delayed  = (1 - P_flip) * (gain_trend - delay_cost) + P_flip * 0
```

其中 `delay_cost` = 锁定前的 bar 数 × 每 bar 错过的平均收益。

**延迟值得当且仅当** `E_delayed > E_immediate`，简化后：

```
P_flip * (gain_trend + delay_cost) > delay_cost
```

在趋势市中 `P_flip` 低、`gain_trend` 大，延迟的代价（错过部分趋势）可能超过避免假信号的收益。在盘整市中 `P_flip` 高，延迟的价值凸显。

### 1.4 优缺点

| 优点 | 缺点 |
|------|------|
| 信号确定性强，入场后不会出现"信号消失"的困境 | 延迟入场，在强趋势市中错过早期利润 |
| 实现简单，仅需 `edge_width` 一个参数 | `edge_width` 是滤波器相关的，需要为每个滤波器校准 |
| 止损逻辑清晰：入场 bar 极点是自然的"判断错误"标志 | 在 SMA/EMA 下延迟可接受（2-5 bar），但在 Savitzky-Golay 下延迟较大（8-12 bar） |
| 天然避免了 T4 中描述的"信号闪烁导致错误入场"场景 | 不适用于 Butterworth（边缘区 = 全窗口，无锁定区） |

---

## 2. 模式 2：预判+确认两步法（Anticipate-then-Confirm）

### 2.1 逻辑描述

**核心思想**：将 Schmitt 触发器的内部状态暴露出来用于交易决策。当前 Schmitt 触发器只输出最终的 Sig（+1/0/-1），但其内部有一个"触发态"标记——当加速度 a 穿越阈值 eps 时，内部状态进入"预触发"，但需等待速度 v 同向确认后才输出 Sig 翻转。

两步法将这个过程拆分为可交易的步骤：

**第一步（预警）**：加速度 a 穿越自适应阈值 eps，且速度 v 同向 → 标记预警区
**第二步（确认）**：预警区内出现 Sig 翻转（内部触发态转为正式输出）→ 入场
**取消预警**：预警区内 Sig 未翻转，反而 a 回到 eps 以内 → 取消预警

### 2.2 伪代码

```python
def anticipate_then_confirm(a_sequence, v_sequence, eps_sequence, price_data, edge_width):
    """
    需要修改 _schmitt_trigger 使其输出内部触发状态 internal_trigger
    a_sequence: 加速度序列
    v_sequence: 速度序列
    eps_sequence: 自适应阈值序列
    """
    locked_boundary = len(a_sequence) - edge_width
    warning_zone = None  # (start_bar, direction)
    trades = []

    for i in range(1, locked_boundary):
        # === 预警逻辑 ===
        if warning_zone is None:
            # 检测 a 穿越 eps（上穿 = 做多预警，下穿 = 做空预警）
            if a_sequence[i-1] < eps_sequence[i-1] and a_sequence[i] > eps_sequence[i]:
                if v_sequence[i] > 0:  # v 同向确认
                    warning_zone = (i, +1)  # 做多预警
            elif a_sequence[i-1] > -eps_sequence[i-1] and a_sequence[i] < -eps_sequence[i]:
                if v_sequence[i] < 0:
                    warning_zone = (i, -1)  # 做空预警

        else:
            warn_start, warn_dir = warning_zone

            # 检测 Sig 翻转（确认）
            sig_flip = get_sig_at(i)  # 从 Schmitt 触发器获取当前 Sig
            if warn_dir == +1 and sig_flip == +1:
                # 确认入场
                trades.append(enter_long(i, price_data))
                warning_zone = None
            elif warn_dir == -1 and sig_flip == -1:
                trades.append(enter_short(i, price_data))
                warning_zone = None

            # 取消预警：a 回到 eps 以内
            elif warn_dir == +1 and a_sequence[i] < eps_sequence[i]:
                warning_zone = None  # 做多预警取消
            elif warn_dir == -1 and a_sequence[i] > -eps_sequence[i]:
                warning_zone = None  # 做空预警取消

            # 预警超时：预警后 N 个 bar 仍未确认
            elif i - warn_start > MAX_WARNING_BARS:
                warning_zone = None

    return trades
```

### 2.3 优缺点

| 优点 | 缺点 |
|------|------|
| 相比纯 Sig 确认（模式 1），能提前 1-3 bar 入场 | 需要修改 `_schmitt_trigger` 输出内部状态，有一定实现复杂度 |
| 预警取消机制天然过滤假突破 | 预警区内的价格波动可能已经造成机会成本 |
| 利用了"a 先动、Sig 后动"的物理直觉（加速度是速度变化的原因） | 在 a 频繁穿越 eps 的盘整市中，预警→取消反复触发 |
| 在趋势启动初期（a 放大、v 即将跟上）时特别有效 | `MAX_WARNING_BARS` 参数需要校准 |

---

## 3. 模式 3：变化点消失止损（Invalidation Stop）

### 3.1 逻辑描述

**核心思想**：入场理由消失了就离场。这是最直接针对"信号闪烁"的应对方案。

每笔交易都绑定到具体的变化点 CP₀（如 "bar=100 处 Sig 从 0→+1"）。每次窗口滑动后，检查 CP₀ 是否仍在当前窗口的锁定区中存在。如果 CP₀ 在后续窗口中消失了，说明市场否定了当初的入场理由，应立即离场。

**关键区分**：变化点消失 vs 变化点被覆盖。

- **消失**：原始 Sig 翻转位置处的 Sig 值变回 0（该 bar 不再被视为趋势变化点）
- **被覆盖**：原始翻转仍然存在，但在其附近出现了新的、更强的翻转（pair 合并导致原 pair 的边界移动）

这两种情况的含义不同：消失 = 入场理由被否定（应离场）；被覆盖 = 趋势在加速（可继续持有，但需更新止损到新的变化点）。

### 3.2 伪代码

```python
class ChangePointTracker:
    """追踪每个变化点的生命周期"""

    def __init__(self):
        self.cps = {}  # cp_id -> {bar_index, type, first_seen_frame, last_seen_frame, status}

    def register(self, bar_index, cp_type, frame_id):
        """注册新变化点"""
        cp_id = f"CP_{frame_id}_{bar_index}_{cp_type}"
        self.cps[cp_id] = {
            'bar_index': bar_index,
            'type': cp_type,       # '+1' or '-1' (Sig 翻转方向)
            'first_seen': frame_id,
            'last_seen': frame_id,
            'status': 'active'     # active / vanished / covered
        }

    def check_survival(self, current_sig, current_frame, edge_width):
        """检查所有活跃变化点是否仍存在"""
        locked_boundary = len(current_sig) - edge_width
        vanished = []

        for cp_id, cp in self.cps.items():
            if cp['status'] != 'active':
                continue
            if cp['bar_index'] >= locked_boundary:
                continue  # 仍在边缘区，暂不判定

            # 检查原始 bar 上的 Sig 是否仍保持
            bar_idx = cp['bar_index']
            if current_sig[bar_idx] == 0:
                cp['status'] = 'vanished'
                cp['vanished_frame'] = current_frame
                vanished.append(cp_id)

        return vanished


def invalidation_stop_strategy(sig_history, price_data, tracker, edge_width):
    """
    sig_history: 每帧的 Sig 序列列表 (按时间顺序)
    """
    trades = []
    active_trades = {}  # trade_id -> {cp_id, entry_bar, entry_price, ...}

    for frame_id, sig in enumerate(sig_history):
        # 检查已入场交易的 CP 是否仍存在
        vanished_cps = tracker.check_survival(sig, frame_id, edge_width)

        for trade_id, trade in list(active_trades.items()):
            if trade['cp_id'] in vanished_cps:
                # 入场理由消失 → 立即离场
                trade['exit_bar'] = len(sig) - 1  # 当前最新 bar
                trade['exit_price'] = price_data[frame_id]['close'][-1]
                trade['exit_reason'] = 'invalidation'
                trades.append(trade)
                del active_trades[trade_id]

        # 检测新的入场信号（使用模式 1 的确认逻辑）
        locked_boundary = len(sig) - edge_width
        for i in range(1, locked_boundary):
            if sig[i-1] == 0 and sig[i] != 0:
                # 注册变化点
                cp_id = f"CP_{frame_id}_{i}_{sig[i]}"
                tracker.register(i, sig[i], frame_id)

                # 入场
                trade_id = f"TRADE_{frame_id}_{i}"
                active_trades[trade_id] = {
                    'cp_id': cp_id,
                    'entry_bar': i,
                    'entry_price': price_data[frame_id]['close'][i],
                    'direction': 'long' if sig[i] == +1 else 'short'
                }

    return trades, tracker
```

### 3.3 优缺点

| 优点 | 缺点 |
|------|------|
| 逻辑自洽：入场理由消失 = 离场，不存在"持有无理由仓位" | 实现复杂度高：需要跨帧追踪变化点 |
| 这是唯一能系统性地防御"信号闪烁导致错误持仓"的模式 | 区分"消失"和"被覆盖"需要额外的合并逻辑分析 |
| 止损触发是"逻辑驱动"而非"价格驱动"，避免了噪音止损 | 如果变化点频繁消失→出现→消失，可能导致频繁开平仓 |
| 可与模式 1 叠加使用 | 变化点追踪器需要持久化状态（跨请求） |

---

## 4. 模式 4：多周期变化点共振（Multi-TF Resonance）

### 4.1 逻辑描述

**核心思想**：当多个时间周期的变化点在时间上接近时，形成"共振"，信号可靠性显著提高。

直观理解：如果日线在 bar=50 处 Sig 翻转为 +1，同时 60 分钟线在对应时间附近（±几个 bar）也出现 Sig 翻转为 +1，说明不同时间尺度上的趋势判断者达成了共识——这个变化点的可信度远高于单一周期信号。

**共振强度定量**：

```
共振强度 = Σ( 1 / (1 + |Δtᵢ|) ) × w(tf)
```

- `Δtᵢ`：周期 i 的变化点与参考周期（最长周期）变化点的时间差（以参考周期的 bar 计）
- `w(tf)`：周期权重，建议日线 = 1.0，60min = 0.7，15min = 0.4，5min = 0.2
- 分母 `1 + |Δtᵢ|` 确保完美对齐（Δt=0）时权重最大，随时间差增大而衰减

**时间窗口定义**（"接近"的标准）：

| 参考周期 | 60min ± | 15min ± | 5min ± |
|---------|---------|---------|--------|
| 日线 (±1 bar) | ±4 bar | ±16 bar | ±48 bar |
| 60min (±1 bar) | — | ±4 bar | ±12 bar |

### 4.2 伪代码

```python
def compute_resonance(cp_dict, tf_weights, time_windows):
    """
    cp_dict: {tf_name: [cp_bar_indices]}  各周期的变化点 bar index 列表
    tf_weights: {tf_name: weight}  周期权重
    time_windows: {tf_name: max_bar_diff}  各周期允许的最大 bar 差
    """
    reference_tf = max(tf_weights, key=tf_weights.get)  # 取权重最高的作为参考
    ref_cps = cp_dict[reference_tf]
    resonance_scores = []

    for ref_bar in ref_cps:
        score = 0.0
        contributors = []

        for tf_name, cps in cp_dict.items():
            if tf_name == reference_tf:
                continue
            # 找最近的 CP
            for cp_bar in cps:
                dt = abs(cp_bar - ref_bar)  # 以参考周期 bar 计
                if dt <= time_windows[tf_name]:
                    contribution = 1.0 / (1.0 + dt) * tf_weights[tf_name]
                    if contribution > 0:
                        contributors.append({
                            'tf': tf_name,
                            'cp_bar': cp_bar,
                            'dt': dt,
                            'contribution': contribution
                        })
                        score += contribution
                    break  # 只取最近的

        resonance_scores.append({
            'ref_bar': ref_bar,
            'score': score,
            'contributors': contributors,
            'threshold': 0.5  # 可调：多少分算"共振"
        })

    return resonance_scores


def multi_tf_resonance_entry(cp_dict, tf_weights, time_windows, price_data, edge_width):
    """仅当共振强度超过阈值时入场"""
    resonances = compute_resonance(cp_dict, tf_weights, time_windows)
    trades = []

    for res in resonances:
        if res['score'] >= res['threshold']:
            # 共振确认，入场
            # 使用参考周期的 CP bar 作为入场点
            # 但需确保该 CP 已离开边缘区
            pass  # 入场逻辑

    return trades
```

### 4.3 优缺点

| 优点 | 缺点 |
|------|------|
| 信号质量极高，假信号率大幅降低 | 交易机会极少（共振不常发生） |
| 可与现有 alignment 逻辑复用（T3 中已有跨周期对齐基础设施） | 需要精确的时间对齐（不同周期的 bar index 映射） |
| 天然跨周期验证，减少单一周期滤波偏差的影响 | 多周期同时计算增加系统负载 |
| 共振强度是连续值，可用于仓位管理（共振越强，仓位越大） | `time_windows` 和 `tf_weights` 参数需要大量回测校准 |

---

## 5. 模式 5：变化点密度作为市场状态过滤器（CP Density Filter）

### 5.1 逻辑描述

**核心思想**：变化点的出现频率反映了市场状态——盘整市变化点频繁出现又消失，趋势市变化点稳定保持。通过监控变化点密度，可以判断当前是否适合交易。

**密度度量**：

```
CP_density(t) = count(Sig_flips in [t-M, t]) / M
```

其中 `Sig_flips` 包括所有 Sig 值变化（0→+1, +1→0, 0→-1, -1→0, +1→-1, -1→+1），无论该翻转后来是否被"锁定"或"消失"。计算时只看锁定区（排除边缘区），避免边缘区的不稳定翻转污染密度估计。

**状态分类**：
- **高密度期**（`CP_density > pct_70`）：变化点频繁出现→消失→再出现 → 盘整市 → 不做或减仓
- **正常密度期**（`pct_30 < CP_density < pct_70`）：正常交易
- **低密度期**（`CP_density < pct_30`）：变化点出现后稳定保持 → 趋势市 → 正常交易，可适当加重仓位

其中 `pct_70` 和 `pct_30` 是过去 N 天（如 60 天）密度分布的 70 和 30 分位数，应滚动计算。

### 5.2 伪代码

```python
def compute_cp_density(sig_history, window=60, edge_width=5):
    """
    sig_history: 按时间排序的 Sig 序列列表（仅锁定区部分或带边缘区标记）
    window: 密度计算窗口 M
    """
    densities = []

    for t in range(window, len(sig_history)):
        flip_count = 0
        for i in range(t - window, t):
            sig = sig_history[i]
            locked_end = len(sig) - edge_width
            # 只统计锁定区内的翻转
            for j in range(1, locked_end):
                if sig[j] != sig[j-1]:
                    flip_count += 1
        densities.append(flip_count / window)

    return densities


def cp_density_filter(sig_history, density_thresholds, price_data):
    """
    density_thresholds: {'high': pct_70, 'low': pct_30}
    返回: 每帧的市场状态标签
    """
    densities = compute_cp_density(sig_history)
    states = []

    for d in densities:
        if d > density_thresholds['high']:
            states.append('ranging')      # 盘整 → 暂停交易
        elif d < density_thresholds['low']:
            states.append('trending')     # 趋势 → 正常/加重
        else:
            states.append('normal')       # 正常

    return states


# 使用示例：密度过滤器叠加在模式 1 上
def confirmation_with_density_filter(sig_sequence, price_data, edge_width, density_state):
    if density_state == 'ranging':
        return []  # 盘整市不交易
    return confirmation_entry(sig_sequence, price_data, edge_width)
```

### 5.3 优缺点

| 优点 | 缺点 |
|------|------|
| 不直接产生信号，作为过滤器叠加到任何模式上，非侵入式 | 依赖历史密度分布估计，冷启动期（前 N 天）不可用 |
| 直接解决"盘整市中假信号频繁导致连续止损"的核心痛点 | 分位数阈值是滚动计算的，市场结构变化时可能滞后 |
| 实现简单，仅需统计翻转次数 | 密度指标本身有滞后性（需要 M 个 bar 的观察窗口） |
| 密度变化本身可能领先于价格趋势变化（变化点收敛 = 趋势即将启动） | 需要区分"因边缘区不稳定导致的假翻转"和"锁定区内的真翻转" |

---

## 6. 模式 6：变化点确认速度加权（Confirmation Speed Weighting）

### 6.1 逻辑描述

**核心思想**：变化点从"首次出现"到"被锁定"的速度反映了趋势强度。这不是把不稳定性当作问题来规避，而是正面利用——不稳定性本身包含了关于趋势质量的信息。

**确认速度定义**：

```
确认速度 = edge_width - (lock_frame - first_appearance_frame)
```

- `first_appearance_frame`：变化点首次被检测到的帧号（此时 CP 在边缘区内）
- `lock_frame`：变化点离开边缘区的帧号（此时 CP 在锁定区内）
- `edge_width`：边缘区宽度（bar）

确认速度的取值范围是 `[0, edge_width]`，越大表示确认越快：
- **快确认（≥ edge_width - 2）**：1-2 bar 内确认 → 强趋势，正常仓位（100%）
- **中确认（edge_width - 5 到 edge_width - 3）**：3-5 bar 确认 → 中等趋势，减仓（60-70%）
- **慢确认（< edge_width - 5）**：>5 bar 才确认 → 弱趋势/可能是假信号，跳过
- **零确认**：首次出现后始终未锁定（在边缘区内反复出现消失）→ 噪声，忽略

### 6.2 伪代码

```python
class CPConfirmationTracker:
    """追踪每个变化点的确认速度"""

    def __init__(self, edge_width):
        self.edge_width = edge_width
        self.pending_cps = {}  # cp_id -> {first_seen_frame, first_seen_bar, direction}
        self.confirmed_cps = {}  # cp_id -> {confirmed_frame, speed}

    def on_new_frame(self, frame_id, sig_sequence):
        """每帧调用，检测变化点并追踪确认"""
        locked_boundary = len(sig_sequence) - self.edge_width

        # 1. 检测新出现的变化点（可在边缘区内）
        for i in range(len(sig_sequence) - self.edge_width, len(sig_sequence)):
            if i > 0 and sig_sequence[i-1] == 0 and sig_sequence[i] != 0:
                cp_id = f"CP_{i}_{sig_sequence[i]}"
                if cp_id not in self.pending_cps:
                    self.pending_cps[cp_id] = {
                        'first_seen_frame': frame_id,
                        'first_seen_bar': i,
                        'direction': sig_sequence[i]
                    }

        # 2. 检查已 pending 的 CP 是否已锁定
        for cp_id, cp in list(self.pending_cps.items()):
            bar = cp['first_seen_bar']
            if bar < locked_boundary:
                # 已离开边缘区，检查是否仍然存在
                if sig_sequence[bar] == cp['direction']:
                    # 已锁定
                    speed = self.edge_width - (frame_id - cp['first_seen_frame'])
                    speed = max(0, min(speed, self.edge_width))
                    self.confirmed_cps[cp_id] = {
                        **cp,
                        'confirmed_frame': frame_id,
                        'speed': speed
                    }
                # 无论是否存在，从 pending 中移除
                del self.pending_cps[cp_id]

    def get_position_size_multiplier(self, cp_id):
        """根据确认速度返回仓位系数"""
        if cp_id not in self.confirmed_cps:
            return 0.0  # 未确认，不交易

        speed = self.confirmed_cps[cp_id]['speed']
        if speed >= self.edge_width - 2:
            return 1.0   # 快确认 → 满仓
        elif speed >= self.edge_width - 5:
            return 0.65  # 中确认 → 减仓
        else:
            return 0.0   # 慢确认 → 跳过


def speed_weighted_entry(sig_sequence, price_data, tracker, edge_width):
    """确认速度加权的入场逻辑"""
    locked_boundary = len(sig_sequence) - edge_width
    trades = []

    for i in range(1, locked_boundary):
        if sig_sequence[i-1] == 0 and sig_sequence[i] == +1:
            cp_id = f"CP_{i}_{+1}"
            multiplier = tracker.get_position_size_multiplier(cp_id)
            if multiplier > 0:
                trades.append({
                    'type': 'long',
                    'entry_bar': i,
                    'size_multiplier': multiplier,
                    'cp_id': cp_id
                })

    return trades
```

### 6.3 优缺点

| 优点 | 缺点 |
|------|------|
| 正向利用不稳定性——不稳定本身是信息 | 需要跨帧追踪，实现复杂度中等 |
| 仓位管理有逻辑依据（快确认 = 强趋势 = 大仓位），而非固定仓位 | 边缘区内的 CP 可能多次出现消失，pending_cps 需要去重和清理逻辑 |
| 自动过滤弱信号（慢确认的 CP 不交易） | 在 SMA/EMA 下 edge_width 只有 2-5 bar，"速度"的分辨率有限 |
| 可与模式 3（消失止损）组合：慢确认的 CP 更可能后续消失 | `speed` 的计算依赖 frame_id 的线性递增，需确保帧号正确 |

---

## 7. 模式量化评估汇总

| 模式 | 预期胜率提升 | 交易频率影响 | 最大回撤改善 | 实现复杂度 | 依赖改造 | 推荐优先级 |
|------|------------|------------|------------|-----------|---------|-----------|
| 1. 确认入场 | +10-20% | 中（减少假信号交易） | 中 | ★★ | 无 | **P0** |
| 2. 预判确认 | +5-15% | 中高（提前入场） | 低 | ★★★ | 需改造 `_schmitt_trigger` | **P1** |
| 3. 消失止损 | +15-25% | —（不改变入场） | **高** | ★★★ | 需新增 ChangePointTracker | **P1** |
| 4. 多周期共振 | +20-30% | **低**（大幅减少交易） | 高 | ★★★★ | 需跨周期对齐基础设施 | **P2** |
| 5. 密度过滤 | 间接提升 | —（只做过滤） | 中 | ★★ | 无 | **P1** |
| 6. 速度加权 | +5-10% | —（只调仓位） | 低 | ★★ | 需新增 CPConfirmationTracker | **P2** |

### 推荐实施路线

```
第一阶段（立即）：模式 1 + 模式 5
  └── 确认入场：最小改动，最大收益，解决"交易边缘区信号"的核心问题
  └── 密度过滤：叠加在模式 1 上，过滤盘整市的假信号

第二阶段（短期）：模式 3
  └── 消失止损：系统性防御信号闪烁，需要 CP 追踪基础设施

第三阶段（中期）：模式 2 + 模式 6
  └── 预判确认：需要改造 Schmitt 触发器
  └── 速度加权：可与模式 3 共用 CP 追踪器

第四阶段（长期）：模式 4
  └── 多周期共振：需要最复杂的基础设施，但信号质量最高
```

---

## 8. 原创洞察

### 洞察 1：变化点闪烁频率与未来波动率的相关性

**假设**：变化点的"闪烁频率"（在锁定区内反复出现→消失→再出现）与未来 N bar 的已实现波动率正相关。

**直觉**：闪烁意味着市场参与者在趋势判断上存在分歧——滤波器和状态机反复在"趋势形成"和"趋势消失"之间摇摆。这种分歧往往在波动率扩张之前出现（市场在"选择方向"前的胶着状态）。

**可验证方案**：

```python
# 定义闪烁频率
flicker_score(t) = count(Sig_flips in locked_zone[t-M:t]) / M

# 定义未来波动率
future_vol(t) = std(returns[t:t+N]) * sqrt(N)

# 检验
correlation = pearsonr(flicker_score[:-N], future_vol)
```

**如果成立**：闪烁频率可以作为波动率预测因子，用于期权策略（高闪烁 → 预期波动率上升 → 买入期权/跨式策略）或仓位管理（高闪烁 → 降低仓位等待方向明确）。

### 洞察 2：变化点消失后的价格回撤概率

**假设**：当锁定区内的变化点消失后，价格有较大概率回撤到该变化点出现前的水平。

**直觉**：变化点消失意味着"趋势形成"被市场否定。如果多头信号（Sig 0→+1）出现后消失，说明买盘未能持续，多头力量衰竭，价格可能回吐因该信号产生的涨幅。

**可验证方案**：

```python
# 对每个消失的变化点
for vanished_cp in vanished_cps:
    cp_bar = vanished_cp.bar_index         # 变化点位置
    cp_price = price[cp_bar]                # 变化点处的价格
    pre_cp_price = price[cp_bar - K]        # 变化点出现前的价格（K = 5-10 bar）

    # 追踪消失后的价格路径
    max_adverse = min(price[cp_bar:cp_bar + N])  # 消失后 N bar 的最低价
    retracement = (cp_price - max_adverse) / (cp_price - pre_cp_price)

    # retracement > 1.0 → 完全回撤（跌破了变化点出现前的水平）
    # 0 < retracement < 1.0 → 部分回撤
    # retracement < 0 → 继续上涨（变化点虽消失但趋势延续）
```

**如果成立**：可以为模式 3（消失止损）提供量化依据——不仅要知道"该离场"，还要知道"离场后价格可能回撤多少"，从而决定是否需要反向开仓。

### 洞察 3：边缘区宽度作为策略选择的前置决策变量

**观察**：所有 6 个模式的有效性都依赖于 `edge_width`。在 SMA/EMA 下（edge_width=2-5），模式 1 的延迟代价小，模式 6 的分辨率低；在 Savitzky-Golay 下（edge_width=8-12），模式 1 的延迟代价变大但模式 6 的分辨率提高。

**推论**：不是为策略选择滤波器，而是为滤波器选择策略组合。

| 滤波器 | edge_width | 推荐策略组合 | 原因 |
|--------|-----------|------------|------|
| SMA/EMA | 2-5 | 模式 1 + 模式 3 + 模式 5 | 延迟小，确认入场优势明显；消失止损保底 |
| Kalman | 5-15 | 模式 3 + 模式 5 + 模式 6 | 边缘区中等，速度加权有区分度 |
| Savitzky-Golay | 8-12 | 模式 2 + 模式 5 + 模式 6 | 延迟大，需要预判提前入场；速度加权充分 |
| Butterworth | 全窗口 | **不建议用于回测** | 无锁定区，所有模式失效 |

---

## 9. 附录：与 T4 根因的对应关系

| T4 根因 | 被哪些模式应对 | 应对方式 |
|---------|-------------|---------|
| A3 滤波器端点效应 | 模式 1, 2, 5 | 模式 1 只交易锁定区；模式 2 预判边缘区内信号；模式 5 只统计锁定区密度 |
| A4 Schmitt 状态机 Cascade | 模式 1, 3, 5 | 模式 1 延迟入场直到信号稳定；模式 3 信号消失则离场；模式 5 高密度 = Cascade 频发 → 暂停 |
| B1 EWMA sigma_v 全局依赖 | 模式 5, 6 | 模式 5 密度升高是 sigma_v 全局变化的"症状"；模式 6 锁定慢说明 sigma_v 调整幅度大 |
| B3 Butterworth 零相位滤波 | 所有模式 | Butterworth 下所有模式失效——无锁定区。强烈建议标记为"不适合回测" |
| B4 pair 合并逻辑 | 模式 3 | 模式 3 需要区分"消失"和"被覆盖"，后者是 pair 合并导致的，不是真正的消失 |
| C1 哨兵值 100.0 | 模式 1, 3 | 修复后所有模式的 PnL 计算都受益 |
| C2 跨周期 PnL 不写入 | 模式 4 | 多周期共振依赖可靠的跨周期数据，该 bug 修复是前提 |

---

*研究基于 T1 (filter-engine)、T2 (data-loader)、T3 (chart-rendering)、T4 (root-cause-analysis) 四份报告的交叉结论。所有模式均标注了与具体根因的对应关系和可验证假设。*
