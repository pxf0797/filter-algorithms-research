# BS 仓位操作标识 — 策略文档

> **版本**: 1.0
> **最后更新**: 2026-07-12
> **适用范围**: 基于 Schmitt 触发器的多周期 K 线交易信号系统

---

## 目录

1. [核心概念](#1-核心概念)
2. [标记规则](#2-标记规则)
3. [操作周期（本级）](#3-操作周期本级)
4. [低一级周期（级联）](#4-低一级周期级联)
5. [再低一级及更深周期](#5-再低一级及更深周期)
6. [数据流](#6-数据流)
7. [边界情况](#7-边界情况)
8. [完整示例](#8-完整示例)
9. [术语表](#9-术语表)

---

## 1. 核心概念

### 1.1 Schmitt 触发器

Schmitt 触发器是一个滞回比较器，用于将价格序列转换为离散的方向信号。它避免了传统交叉信号在阈值附近反复跳变的问题——只有价格偏离超过滞回带时，方向才会翻转。

```
价格序列:  [100, 102, 105, 103, 101,  98,  95,  97,  99, 104, 107, 110]
             ↓     ↓     ↓     ↓     ↓    ↓    ↓    ↓    ↓    ↓    ↓    ↓
信号 sig:  [ 0,   0,   1,   1,   1,   1,  -1,  -1,  -1,   1,   1,   1 ]
                        ↑                        ↑              ↑
                    上穿阈值                  下穿阈值       再次上穿
```

### 1.2 同向段（Same-Direction Segment）

**定义**: Schmitt 触发器产生的连续同向信号区间。

一个 `all_pairs` 元素 `(pair_start, pair_end)` 就是一个同向段：

- `sig[pair_end] == 1`  → **做多同向段**（该段内价格总体看涨）
- `sig[pair_end] == -1` → **做空同向段**（该段内价格总体看跌）

```
all_pairs = [(2, 9), (12, 19), (25, 30), ...]
              ↑            ↑
         做多同向段     做空同向段
         (sig[9]=1)    (sig[19]=-1)
```

**关键属性**:
- `pair_start`: 同向段的起始 bar 索引（信号首次出现的时刻，Schmitt 刚翻转）
- `pair_end`: 同向段的结束 bar 索引（信号确认的时刻，此时方向已由预测曲线验证）
- 同向段之间是反向或中性区域

> **时序差异（关键）**: `all_pairs` 的 `pair_start` 是信号**首次出现**的时刻（Schmitt 触发器刚翻转），而 `trade_records` 的入场点是 `pair_end` 时刻——即信号**确认**的时刻（预测曲线已验证方向）。同向性判断子图使用 `trade_records` 的时机，BS 标记也必须对齐到同一时机。详见 [1.5 同向性判断](#15-同向性判断)。

### 1.3 BS 标记

**BS 标记** 是在同向段的关键位置标注的买卖点，直接显示在 K 线主图上。BS 标记的入场/出场时机必须与系统的**同向性判断（alignment）子图**保持一致——即使用 `trade_records`（`pair_end` 确认点）而非原始 `all_pairs`（`pair_start` 首次出现点）。

```
同向段生命周期:

  pair_start                              pair_end
      ↓                                       ↓
  ────[■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■]──── 时间 →
      ↑                                       ↑
    入场点                                 出场点
   (标 B 或 S)                           (标 S 或 B)
```

- **入场**: 同向段第一个点（`pair_start`），信号刚确立，进入仓位
- **出场**: 同向段结束点（`pair_end`），信号翻转，平仓离场
- **异常出场**: 止损触发，不等同向段自然结束，强制平仓

### 1.4 关键数据结构

| 结构 | 含义 | 示例 |
|------|------|------|
| `sig` | Schmitt 信号数组，长度 = 价格 bar 数 | `[0, 0, 1, 1, -1, -1]` |
| `all_pairs` | 同向段列表，每个元素 `(start, end)` | `[(2, 3), (4, 5)]` |
| `trade_records` | 策略交易记录（含止损、盈亏等） | 策略内部使用 |
| `higher_bs` | 上级周期传递下来的 BS 标记 | 级联用 |

### 1.5 同向性判断（Alignment Subplot）

**同向性判断子图** 是系统 K 线界面中的一个独立子图，用于展示策略在各个周期的方向判断是否一致。BS 标记必须与该子图对齐。

**数据来源**: 同向性判断子图由 `_compute_strategy_pnl` 生成的 `trade_records` 驱动，**不是** 由原始 `all_pairs`（Schmitt 同向段）驱动。

**关键差异**:

| 数据源 | 入场时机 | 含义 | 是否用于同向性判断 |
|--------|----------|------|-------------------|
| `all_pairs` | `pair_start`（信号首次出现） | Schmitt 刚翻转，方向"猜测"阶段 | 否 |
| `trade_records` | `pair_end`（信号确认点） | 预测曲线已验证方向，"确认"阶段 | **是** |

**为什么必须对齐**:

1. 同向性判断子图展示的是 `trade_records` 中的方向判断结果
2. 如果 BS 标记使用 `all_pairs` 的 `pair_start` 时机，BS 标记会**早于**同向性判断的确认时点
3. 用户在 K 线主图上看到 🟢B 标在 bar=2，但同向性判断子图显示方向确认在 bar=9——产生视觉矛盾
4. BS 标记使用 `trade_records` 的时机后，主图 BS 标注与同向性判断子图完全对齐，用户看到的买卖点和方向判断是同一套逻辑

**数据流关系**:

```
Schmitt 触发器
   │
   ├──→ all_pairs (pair_start 时机)  ──→ 原始同向段，仅供参考
   │
   └──→ _compute_strategy_pnl
           │
           └──→ trade_records (pair_end 时机)  ──→ 同向性判断子图
                                                      │
                                                      ▼
                                                  BS 标记（优先使用）
```

> **结论**: BS 标记优先使用 `trade_records`，确保主图的买卖点标注与同向性判断子图使用相同的入场/出场时机。`all_pairs` 仅在 `trade_records` 不可用时作为回退方案。

---

## 2. 标记规则

### 2.1 颜色与标签规则

| 方向 | 入场标记 | 出场标记 | 颜色 | 逻辑 |
|------|----------|----------|------|------|
| 做多 | 🟢 **B** | 🟢 **S** | 绿色 | 先买后卖 |
| 做空 | 🔴 **S** | 🔴 **B** | 红色 | 先卖后买 |

**记忆口诀**:
- 做多: **B**uy then **S**ell — 全绿色
- 做空: **S**ell then **B**uy — 全红色

### 2.2 标记在 K 线图上的位置

```
         ┌─────┐
         │     │  high ─── 🟢S (做多出场) 或 🔴B (做空出场)
         │ K线 │
         │     │
         │     │  low  ─── 🟢B (做多入场) 或 🔴S (做空入场)
         └─────┘
```

- **入场标记** 显示在 K 线 **low 下方**（靠近底部）
- **出场标记** 显示在 K 线 **high 上方**（靠近顶部）
- 这样入场和出场在视觉上不会重叠，便于快速识别

### 2.3 标记规则汇总

```
                    入场(low下)    出场(high上)
                    ───────────    ────────────
  做多同向段:        🟢B             🟢S
  (sig[end]=1)

  做空同向段:        🔴S             🔴B
  (sig[end]=-1)
```

---

## 3. 操作周期（本级）

操作周期（如日线）的 BS 标记 **优先** 从 `trade_records`（来自 `_compute_strategy_pnl`）生成，`all_pairs` 作为回退。这确保 BS 标记的入场/出场时机与系统的**同向性判断（alignment）子图**使用相同的数据源和确认时点（`pair_end`）。

> **数据源优先级**: `trade_records`（优先）→ `all_pairs`（回退）。仅在 `_compute_strategy_pnl` 未执行或 `trade_records` 为空时，才回退到 `all_pairs`。

### 3.1 生成算法

```
优先路径 — 使用 trade_records（推荐）:
─────────────────────────────────────
输入: trade_records = [
        {entry_bar: e1, exit_bar: x1, direction: 1, ...},
        {entry_bar: e2, exit_bar: x2, direction: -1, ...},
        ...
      ]

对于 trade_records 中的每笔交易:

  判断方向 (来自 trade_records 而非 sig):
    direction = record.direction  # 1=做多, -1=做空

  标记入场 (在 bar=entry_bar):      ← pair_end 确认点
    if LONG:  标记 🟢B
    if SHORT: 标记 🔴S

  标记出场 (在 bar=exit_bar):
    if LONG:  标记 🟢S
    if SHORT: 标记 🔴B


回退路径 — 使用 all_pairs（仅在 trade_records 不可用时）:
─────────────────────────────────────────────────────────
输入: all_pairs = [(start_1, end_1), (start_2, end_2), ...]
      sig = [0, 0, 1, 1, ..., -1, -1, ...]

对于 all_pairs 中的每个 (pair_start, pair_end):

  判断方向:
    if sig[pair_end] == 1:
      direction = LONG   # 做多
    elif sig[pair_end] == -1:
      direction = SHORT  # 做空

  标记入场 (在 bar=pair_start):      ← pair_start 首次出现点（回退行为）
    if LONG:  标记 🟢B
    if SHORT: 标记 🔴S

  标记出场 (在 bar=pair_end):
    if LONG:  标记 🟢S
    if SHORT: 标记 🔴B
```

### 3.2 具体示例

**优先路径（使用 trade_records）**:

```
假设日线 trade_records = [
  {entry_bar: 9,  exit_bar: 30, direction: 1, ...},   # 做多交易
  {entry_bar: 30, exit_bar: 45, direction: -1, ...},  # 做空交易
]

处理交易记录 #1, direction=1 → 做多:
  bar=9:  标 🟢B (入场，pair_end 确认点，与同向性判断对齐)
  bar=30: 标 🟢S (出场)

处理交易记录 #2, direction=-1 → 做空:
  bar=30: 标 🔴S (入场，pair_end 确认点)
  bar=45: 标 🔴B (出场)
```

**注意**: trade_records 的 `entry_bar` 是 `pair_end` 时刻（信号确认点），而非 `all_pairs` 的 `pair_start`（信号首次出现）。这保证了 BS 标记与同向性判断子图使用相同的入场时机。

**回退路径（使用 all_pairs，仅在 trade_records 不可用时）**:

```
假设日线 all_pairs = [(2, 9), (12, 19), (25, 30)]
      sig[9] = 1, sig[19] = -1, sig[30] = 1

处理 (2, 9), sig[9]=1 → 做多同向段:
  bar=2:  标 🟢B (入场，pair_start 首次出现点)
  bar=9:  标 🟢S (出场，同向段结束)

处理 (12, 19), sig[19]=-1 → 做空同向段:
  bar=12: 标 🔴S (入场，pair_start 首次出现点)
  bar=19: 标 🔴B (出场，同向段结束)
```

### 3.3 K 线图上的视觉效果

```
做多段                              做空段
  🟢S                                 🔴B
   │                                   │
 ┌─┴─┐      ┌───┐      ┌───┐        ┌─┴─┐      ┌───┐
 │   │      │   │      │   │        │   │      │   │
 │   │ ...  │   │ ...  │   │  ...   │   │ ...  │   │
 │   │      │   │      │   │        │   │      │   │
 └─┬─┘      └───┘      └─┬─┘        └─┬─┘      └───┘
   │                     │            │
  🟢B                   🟢S          🔴S
  bar=2                bar=9        bar=12              bar=19
```

### 3.4 代码对应

在代码中，操作周期的 BS 标记由 `_compute_own_markers()` 函数生成：

```python
def _compute_own_markers(trade_records=None, all_pairs=None, sig=None):
    """生成操作周期的 BS 标记。

    优先使用 trade_records（与同向性判断子图对齐），
    all_pairs 作为回退。
    """
    markers = []

    # 优先路径: trade_records（entry_bar 是 pair_end 确认点）
    if trade_records:
        for record in trade_records:
            direction = record["direction"]  # 1=做多, -1=做空
            markers.append({
                "bar": record["entry_bar"],
                "type": "B" if direction == 1 else "S",
                "color": "green" if direction == 1 else "red",
                "label": "entry",
                "source": "trade_records",  # 标记数据来源
            })
            markers.append({
                "bar": record["exit_bar"],
                "type": "S" if direction == 1 else "B",
                "color": "green" if direction == 1 else "red",
                "label": "exit",
                "source": "trade_records",
            })
        return markers

    # 回退路径: all_pairs（仅 trade_records 不可用时）
    if all_pairs and sig is not None:
        for pair_start, pair_end in all_pairs:
            direction = sig[pair_end]  # 1=做多, -1=做空
            markers.append({
                "bar": pair_start,  # pair_start 而非 pair_end（回退行为）
                "type": "B" if direction == 1 else "S",
                "color": "green" if direction == 1 else "red",
                "label": "entry",
                "source": "all_pairs",  # 标记为回退来源
            })
            markers.append({
                "bar": pair_end,
                "type": "S" if direction == 1 else "B",
                "color": "green" if direction == 1 else "red",
                "label": "exit",
                "source": "all_pairs",
            })

    return markers
```

---

## 4. 低一级周期（级联）

操作周期标完 BS 后，将标记向 **低一级周期** 级联（cascade）。级联的意义在于：大周期的方向判断在更精细的时间粒度上找到精确的入场和出场位置。

### 4.1 入场级联

**场景**: 操作周期标了入场 BS（如日线 bar=2 🟢B），需要在 60 分钟线上找到对应的精确入场点。

```
算法流程:

  操作周期标了入场 BS（日线 bar=N 🟢B 或 🔴S）
       │
       ▼
  在低一级周期(如60分钟)定位日线 bar=N 对应的日期 → start_bar
       │
       ▼
  遍历低一级周期的 all_pairs，找第一个满足以下条件的 pair:
      条件1: pair_start ≥ start_bar
      条件2: sig[pair_end] == 期望方向 (1=做多, -1=做空)
       │
       ├── 找到 → 在低一级 (pair_start) 标 BS 入场
       │         （只标同向段第一个点，不在后续 bar 重复标）
       │
       └── 找不到 → 不标（低一级周期不同向，放弃级联）
```

**关键约束**:
- **只在同向段的第一个点标 BS**。如果同向段有 10 根 bar，只在第 1 根标入场，不在 bar 2~10 重复标。
- 方向必须一致：日线做多 → 60 分钟找做多同向段；日线做空 → 60 分钟找做空同向段。

**示例**:

```
日线 bar=2 标 🟢B (做多入场)
  日线 bar=2 对应日期 2026-03-15
       │
       ▼
  60分钟数据中 2026-03-15 的第一根 bar → start_bar=50
       │
       ▼
  60分钟 all_pairs:
    [(30, 35), (40, 48), (52, 60), (65, 72)]
     sig[35]=-1     sig[48]=-1   sig[60]=1   sig[72]=1
        ✗ (end<start_bar)  ✗ (方向不符)  ✓ (符合!)
                                               │
                                               ▼
                                    在60分钟 bar=52 标 🟢B
```

### 4.2 正常出场级联（多空对结束）

**场景**: 操作周期同向段自然结束（如日线 bar=9 🟢S），将出场信号级联到低一级周期。

```
算法流程:

  操作周期同向段结束（日线 bar=N 🟢S 或 🔴B）
       │
       ▼
  这是同向段的正常结束，等待低一级同向确认
       │
       ▼
  在低一级周期找对应同向段的结束点 (pair_end)
       │
       ▼
  在低一级 pair_end 标 🟢S（做多出场）或 🔴B（做空出场）
```

**与入场级联的区别**:
- 入场级联找 `pair_start`（同向段开始）
- 出场级联找 `pair_end`（同向段结束）
- 正常出场 **要等** 低一级周期的同向段自然结束

**示例**:

```
日线 bar=9 标 🟢S (做多出场，同向段 (2,9) 正常结束)
       │
       ▼
  60分钟同向段 (52, 60) 在 bar=60 结束
       │
       ▼
  在60分钟 bar=60 标 🟢S
```

### 4.3 异常出场级联（偏离退出 / stop_loss）

**场景**: 策略因止损（stop_loss）在中途退出，没有等到同向段自然结束。

```
算法流程:

  操作周期 stop_loss 触发，中途退出
       │
       ▼
  不等低一级同向确认（因为这不是"自然结束"）
       │
       ▼
  在低一级同一时间位置立即标 BS 出场
  （时间对齐而非同向段对齐）
```

**与正常出场的关键差异**:

| 特性 | 正常出场 | 异常出场（止损） |
|------|----------|------------------|
| 触发条件 | 同向段自然结束 | 止损触发 |
| 等待低一级同向确认 | 是 | 否 |
| 标记位置 | 低一级 `pair_end` | 同一时间位置 |
| 级联时机 | 等低一级同向段走完 | 立即级联 |

### 4.4 代码对应

级联逻辑由 `_compute_cascade_markers()` 函数处理：

```python
def _compute_cascade_markers(higher_bs, lower_all_pairs, lower_sig, bar_time_map):
    """
    将上级 BS 标记级联到本级。

    参数:
        higher_bs: 上级周期的 BS 标记列表
        lower_all_pairs: 本级 all_pairs
        lower_sig: 本级 Schmitt 信号
        bar_time_map: bar 索引到时间的映射
    """
    cascade_markers = []
    for bs in higher_bs:
        if bs["label"] == "entry":
            # 入场级联：找本级同向段第一个点
            start_bar = find_date_index(bs["bar"], bar_time_map)
            for pair_start, pair_end in lower_all_pairs:
                if pair_start >= start_bar and lower_sig[pair_end] == bs["direction"]:
                    cascade_markers.append({...})  # 标在 pair_start
                    break
        elif bs["label"] == "exit":
            # 正常出场级联：找本级同向段结束点
            ...
        elif bs["label"] == "stop_loss":
            # 异常出场级联：时间对齐，立即标
            ...
    return cascade_markers
```

---

## 5. 再低一级及更深周期

级联是一个 **递归/迭代过程**，逐级向下传递：

```
日线(操作周期)
   │  _compute_own_markers  → 本级 BS
   │  _compute_cascade_markers → 存入 st.session_state["_bs_60min"]
   ▼
60分钟(级联)
   │  读取 st.session_state["_bs_60min"] 作为 higher_bs
   │  _compute_cascade_markers → 存入 st.session_state["_bs_15min"]
   ▼
15分钟(级联)
   │  读取 st.session_state["_bs_15min"] 作为 higher_bs
   │  _compute_cascade_markers → 存入 st.session_state["_bs_5min"]
   ▼
5分钟(级联)
   │  ...
   ▼
  (继续向下，直到没有更低周期)
```

### 5.1 核心规则（适用所有级联层级）

上一级标了 BS → 本级执行相同的级联逻辑：

| BS 类型 | 本级处理 |
|----------|----------|
| 入场 (entry) | 找同向段第一个点，标入场 |
| 正常出场 (exit) | 等同向段结束，标在 `pair_end` |
| 异常出场 (stop_loss) | 时间对齐，立即标出场 |

### 5.2 级联终止条件

级联在以下情况下停止：

1. **没有更低周期**: 当前已是最细粒度（如 5 分钟），无法继续向下
2. **Schmitt 未启用**: 低一级周期的 Schmitt 触发器未配置或未计算
3. **找不到同向 pair**: 入场级联时，低一级周期没有任何同向段满足方向和位置条件
4. **数据不可用**: 低一级周期的数据时间窗口不覆盖上级 BS 的时间范围

---

## 6. 数据流

### 6.1 整体架构

```
                        ┌─────────────────────┐
                        │   价格数据 (price)    │
                        └──────────┬──────────┘
                                   │
                        ┌──────────▼──────────┐
                        │  Schmitt 触发器      │
                        │  → sig[], all_pairs │
                        └──────────┬──────────┘
                                   │
                        ┌──────────▼──────────┐
                        │ _compute_strategy_   │
                        │   pnl()              │
                        │ → trade_records      │
                        │ (pair_end 确认点)     │
                        └──────────┬──────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
                    ▼              │              │
          ┌─────────────────┐     │              │
          │ 同向性判断子图    │     │              │
          │ (alignment)      │     │              │
          │ 使用 trade_records│     │              │
          └─────────────────┘     │              │
                                  │              │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
     ┌────────▼────────┐  ┌───────▼────────┐  ┌───────▼────────┐
     │   操作周期(日线)  │  │  低一级(60分钟)  │  │  更低级(15分钟)  │
     │                  │  │                │  │                │
     │ _compute_own_    │  │ _compute_      │  │ _compute_      │
     │   markers()      │  │   cascade_     │  │   cascade_     │
     │ 优先:trade_      │  │   markers()    │  │   markers()    │
     │ records          │  │      │         │  │      │         │
     │ 回退:all_pairs   │  │      ▼         │  │      ▼         │
     │      │           │  │  BS标记(entry   │  │  BS标记(entry   │
     │      ▼           │  │  + exit) ───────┼──│→ + exit) ───────┼──→ ...
     │  BS标记(entry    │  │                │  │                │
     │  + exit) ────────┼──│→ higher_bs ────┼──│→ higher_bs     │
     │      │           │  │  → BS标记      │  │  → BS标记      │
     └──────┼───────────┘  └───────────────┘  └────────────────┘
            │
            ▼
     ┌──────────────────────────────────────────┐
     │         st.session_state                  │
     │  ["_bs_daily"]  = 日线 BS 标记            │
     │  ["_bs_60min"]  = 60分钟 BS 标记          │
     │  ["_bs_15min"]  = 15分钟 BS 标记          │
     │  ["_bs_5min"]   = 5分钟 BS 标记           │
     └──────────────────────────────────────────┘
```

### 6.2 状态传递

BS 标记通过 Streamlit 的 `st.session_state` 在视图之间传递：

```python
# 键名约定
st.session_state[f"_bs_{timeframe}"]  # 如 _bs_daily, _bs_60min, _bs_15min

# 读取上级标记（用于级联）
higher_bs = st.session_state.get(f"_bs_{higher_tf}", [])

# 存储本级标记（供下级读取和自己显示）
st.session_state[f"_bs_{current_tf}"] = current_markers
```

### 6.3 数据不变量

在级联过程中，以下关系始终成立：

```
本级标记数量 ≤ 上级标记数量 × 2
```
每个上级 BS 最多在低一级产生 1~2 个标记（入场 + 出场/止损），如果找不到同向段则可能为 0。

```
级联链路方向: 操作周期 → 低一级 → 再低一级 → ... → 最细粒度
```
级联是单向的，不会从细粒度向粗粒度反向传递。

---

## 7. 边界情况

### 7.1 周期缺失

| 场景 | 行为 |
|------|------|
| 操作周期无低一级周期 | 仅在本级标 BS，不级联 |
| 用户只启用日线和 15 分钟，跳过 60 分钟 | 级联链从日线直接到 15 分钟（如果有级联链路跳过逻辑），或日线的标记仅停留在日线 |

### 7.2 Schmitt 状态

| 场景 | 行为 |
|------|------|
| 低一级 Schmitt 未启用 | 不级联，低一级周期无任何标记 |
| 低一级 Schmitt 参数导致无 all_pairs | `_compute_cascade_markers` 收到空列表，返回空 |
| 低一级 sig 全为 0 | 无同向段，级联不产生标记 |

### 7.3 时间对齐

| 场景 | 行为 |
|------|------|
| 低一级数据时间窗口不覆盖上级 BS 时间 | `_find_date_index` 进行日期对齐（时区归一化），若仍无法匹配则跳过该 BS |
| 上级 BS 落在低一级的周末/假期（无数据） | 日期对齐时找最接近的下一个有效 bar |
| 不同数据源的时区不一致 | 统一归一到 UTC 或指定时区后再比较 |

### 7.4 同向段匹配

| 场景 | 行为 |
|------|------|
| 级联找不到同向 pair（方向一致但位置不对） | 该 entry 不级联，低一级无对应标记 |
| 级联找不到同向 pair（方向不一致） | 该 entry 不级联——低一级周期与上级"看法相反"，放弃 |
| 低一级有多个同向段匹配 | 取第一个满足条件的（`pair_start ≥ start_bar` 且方向一致） |

### 7.5 异常退出

| 场景 | 行为 |
|------|------|
| 止损（stop_loss）触发 | 不等同向确认，立即级联到同一时间位置 |
| 偏离退出（deviation exit） | 同上，视为异常退出 |
| 策略信号翻转但同向段未结束 | 这属于异常退出范畴，按 stop_loss 逻辑处理 |

### 7.6 空数据

| 场景 | 行为 |
|------|------|
| 操作周期 all_pairs 为空 | 无 BS 标记产生，不级联 |
| 低一级 all_pairs 为空但上级有 BS | 级联不产生标记 |
| 价格数据加载失败 | 所有 BS 计算跳过，st.session_state 中对应键为空列表 |

---

## 8. 完整示例

### 8.1 示例数据

```
日线 (操作周期):
  all_pairs = [(2, 9), (12, 19)]
  sig[9] = 1   → 做多同向段
  sig[19] = -1 → 做空同向段

60分钟 (低一级):
  all_pairs = [(30, 35), (40, 48), (52, 60), (65, 72)]
  sig[35] = -1, sig[48] = -1, sig[60] = 1, sig[72] = 1

时间映射:
  日线 bar=2  →  60分钟 bar=45  (2026-03-15 09:30)
  日线 bar=9  →  60分钟 bar=58  (2026-03-17 15:00)
  日线 bar=12 →  60分钟 bar=63  (2026-03-18 10:00)
  日线 bar=19 →  60分钟 bar=70  (2026-03-20 14:00)
```

### 8.2 逐步骤计算

**步骤 1: 日线计算自身 BS**

```
日线 all_pairs = [(2, 9), (12, 19)]

处理 (2, 9), sig[9]=1 (做多):
  bar=2:  🟢B (入场)
  bar=9:  🟢S (出场)

处理 (12, 19), sig[19]=-1 (做空):
  bar=12: 🔴S (入场)
  bar=19: 🔴B (出场)

日线 BS 结果:
  [{bar: 2, type: "B", color: "green", label: "entry"},
   {bar: 9, type: "S", color: "green", label: "exit"},
   {bar: 12, type: "S", color: "red", label: "entry"},
   {bar: 19, type: "B", color: "red", label: "exit"}]
```

**步骤 2: 级联到 60 分钟**

```
级联日线 bar=2 🟢B (做多入场):
  定位 start_bar = 45
  遍历60分钟 all_pairs:
    (30, 35): pair_start(30) < 45  → 跳过（在此之前）
    (40, 48): pair_start(40) < 45  → 跳过（在此之前）
             但 sig[48]=-1         → 方向也不符
    (52, 60): pair_start(52) ≥ 45 ✓, sig[60]=1 ✓ → 匹配!
  → 在60分钟 bar=52 标 🟢B

级联日线 bar=9 🟢S (做多出场):
  这是正常出场，找对应同向段的 pair_end
  (52, 60) 的 pair_end=60
  → 在60分钟 bar=60 标 🟢S

级联日线 bar=12 🔴S (做空入场):
  定位 start_bar = 63
  遍历60分钟 all_pairs:
    (65, 72): pair_start(65) ≥ 63 ✓, sig[72]=1 ✗ → 方向不符!
  60分钟目前在看多做多，与日线做空方向矛盾
  → 找不到同向 pair，不标!

级联日线 bar=19 🔴B (做空出场):
  正常出场，但入场都没级联成功
  → 不标（没有对应的60分钟持仓段）

60分钟 BS 结果:
  [{bar: 52, type: "B", color: "green", label: "entry"},
   {bar: 60, type: "S", color: "green", label: "exit"}]
```

### 8.3 最终 K 线图展示

```
日线:
  bar:  1   2   3   4   5   6   7   8   9  10  11  12  13  14  15  16  17  18  19  20
       ────────────────────────────────────────────────────────────────────────────────
        │ 🟢B                                   🟢S│   │ 🔴S                      🔴B│
        │                                         │   │                            │
        └── 做多同向段 (2,9) ─────────────────────┘   └── 做空同向段 (12,19) ──────┘

60分钟:
  bar:  ... 50  51  52  53  54  55  56  57  58  59  60  61  62  63  64  65 ...
       ─────────────────────────────────────────────────────────────────────
                  │ 🟢B                           🟢S│
                  │                                  │
                  └── 做多同向段 (52,60) ───────────┘
                  (日线做空入场未级联: 60分钟在65之后方向已变为做多)
```

---

## 9. 术语表

| 术语 | 英文 | 说明 |
|------|------|------|
| BS 标记 | BS Marker | K 线图上的买卖点标注（Buy/Sell） |
| 同向段 | Same-Direction Segment | Schmitt 触发器产生的连续同向信号区间 |
| Schmitt 触发器 | Schmitt Trigger | 滞回比较器，将价格转换为离散方向信号 |
| 操作周期 | Operating Timeframe | 用户选择的主分析周期（如日线） |
| 级联 | Cascade | 将上级周期的 BS 标记传递到低一级周期 |
| 入场 | Entry | 同向段开始，建立仓位 |
| 出场 | Exit | 同向段结束，平仓离场 |
| 止损 | Stop Loss | 价格触及止损线，强制平仓 |
| 偏离退出 | Deviation Exit | 价格偏离预期，策略提前退出 |
| all_pairs | — | `[(start, end), ...]` 同向段列表 |
| sig | — | Schmitt 信号数组，`1`=做多, `-1`=做空, `0`=中性 |
| higher_bs | — | 从上级周期传递下来的 BS 标记列表 |
| pair_start | — | 同向段的起始 bar 索引 |
| pair_end | — | 同向段的结束 bar 索引 |

---

## 附录 A: 代码层级对应

```
filter_app/
├── bs_marker.py            # BS 标记核心逻辑 (_compute_own_markers, _compute_cascade_markers)
├── schmitt.py              # Schmitt 触发器 (生成 sig, all_pairs)
├── views/
│   ├── daily_view.py       # 日线视图（操作周期）
│   ├── hourly_view.py      # 60 分钟视图
│   ├── min15_view.py       # 15 分钟视图
│   └── min5_view.py        # 5 分钟视图
└── utils/
    └── time_utils.py        # 时间对齐工具 (_find_date_index 等)
```

## 附录 B: 常见问题

**Q: BS 标记和实际交易记录是什么关系？**

A: BS 标记**优先**使用 `trade_records`（来自 `_compute_strategy_pnl`）来生成，确保 BS 标记的入场/出场时机与系统的**同向性判断（alignment）子图**保持一致。`all_pairs` 仅作为回退方案。关键区别在于：`trade_records` 的入场点是 `pair_end` 时刻（信号确认点），而 `all_pairs` 的入场点是 `pair_start` 时刻（信号首次出现）。使用 `trade_records` 后，BS 标记与同向性判断子图使用同一套数据和时机。

> **旧方案（已弃用）**: ~~BS 标记来自 `all_pairs`（Schmitt 同向段），而交易记录来自 `trade_records`（策略执行结果）。策略可能在同一同向段内多次进出（如部分止盈后再入场），而 BS 标记只在同向段边界标一次。~~

**Q: 级联时为什么要等低一级同向确认？**

A: 为了确保精细周期的方向与大周期一致。如果低一级周期与大周期方向相反，说明短期内可能有反向波动，此时入场风险较高。

**Q: 止损出场为什么不等待同向确认？**

A: 止损是强制性风险控制动作，不等同向确认直接退出是为了及时止损。在低一级周期时间对齐后立即标记，反映的是风控决策而非信号决策。
