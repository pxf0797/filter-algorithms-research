# B 周期同向门控交易判定方案

> 版本: v1.0 | 日期: 2026-07-08
> 依赖文件: `filter_engine.py` (L434, L649, L867, L975), `sidebar.py` (L13, L100-L104), `charts.py` (L399), `streamlit_app.py` (L249, L279, L308, L622-L664)

---

## Part 1: A/B/C/D 周期模型

### 1.1 与现有 `ALL_TFS` 的映射

`ALL_TFS` 按精细度从高到低定义了一个全序：

```python
ALL_TFS = ["1分钟", "5分钟", "15分钟", "60分钟", "日线", "周线", "月线", "季线"]
# 索引:      0        1        2        3        4       5       6       7
#  ↑ 精细(小周期)                                       ↑ 粗糙(大周期)
```

**核心抽象**：用户在 4 个视图中各自独立选择一个周期。不论视图顺序如何，系统按 `ALL_TFS` 中的索引排序得到 **A < B < C < D**（索引越小 = 周期越精细 = 级别越低）：

```
A := min(idx(view0.tf), idx(view1.tf), idx(view2.tf), idx(view3.tf))  # 最精细
D := max(idx(view0.tf), idx(view1.tf), idx(view2.tf), idx(view3.tf))  # 最粗糙
B, C := 中间两个（升序排列）
```

**示例**：若视图配置为 `[日线, 15分钟, 周线, 5分钟]`
- 索引：`[4, 2, 5, 1]`
- 排序后：`[1, 2, 4, 5]` → A=5分钟, B=15分钟, C=日线, D=周线

### 1.2 "紧邻上级"关系

利用现有 `TF_HIERARCHY`（sidebar.py L100-L104）：

```python
TF_HIERARCHY = {
    "1分钟": "5分钟", "5分钟": "15分钟", "15分钟": "60分钟",
    "60分钟": "日线", "日线": "周线", "周线": "月线",
    "月线": "季线", "季线": None,
}
```

在 A/B/C/D 视角下：

```python
紧邻上级(tf) := TF_HIERARCHY[tf]

# 对于4个周期:
紧邻上级(A) = TF_HIERARCHY[A]    # 可能等于 B，也可能不是（取决于A是否在4个视图中存在中间周期）
紧邻上级(B) = C   如果 C == TF_HIERARCHY[B]，否则 C 是更高级但非紧邻
紧邻上级(C) = D   如果 D == TF_HIERARCHY[C]，否则 D 是更高级但非紧邻
紧邻上级(D) = TF_HIERARCHY[D]    # 可能超出4个视图范围（D的上级在4视图之外）
```

**重要说明**：A/B/C/D 的纯排序关系不保证紧邻。B 的紧邻上级在 TF_HIERARCHY 中可能不是 C。方案的核心设计不依赖"B 的紧邻上级一定是 C"——C 只是 B **在 4 个视图中可用的最近高级周期**。当 C 信号不可用时，回退链往下走。

### 1.3 为什么只针对 B 周期做门控

| 周期 | 角色 | 原因 |
|------|------|------|
| A | 最精细周期 | 噪声大，不适合做主信号 |
| **B** | **主交易周期** | 有一定趋势识别能力，且仍有更高级季确认 |
| C | 确认周期 | 比 B 更粗糙，用作 B 的上级同向确认 |
| D | 兜底周期 | 最粗糙，当 C 不可用时提供最后一道确认；D 没有更高级周期 |

---

## Part 2: "同向"的三种定义方式

### 定义 1: Sig 信号同向 (B-C 推荐)

**含义**：两个周期的 Schmitt 触发器信号 (Sig) 方向一致。

```python
B与C同向做多 ⇔ B.Sig == +1 AND C.Sig == +1
B与C同向做空 ⇔ B.Sig == -1 AND C.Sig == -1
```

| 属性 | 评估 |
|------|------|
| **来源** | `_schmitt_trigger()` 返回的 `{"sig": sig_t}` — `filter_engine.py` L434-L517 |
| **优点** | Sig 自身已包含滞回确认（Schmitt hysteresis），避免边缘区反复跳变；计算轻量 |
| **缺点** | C 的 Sig 在震荡/边缘区可能为 0（观望），导致"不确定"而非明确的否定 |
| **数据依赖** | 仅需 C 的滤波价格及 `v`/`a` 导数，C 不需要开启 strategy 模式 |
| **推荐度** | **B-C 同向判断首选** |

### 定义 2: 持仓状态同向

**含义**：利用 C 周期的 `long_mask`/`short_mask`（来自 `_compute_holding_masks`）判断 C 是否处于持仓状态。

```python
B与C同向做多 ⇔ C的long_mask在B的当前bar对应位置为True
B与C同向做空 ⇔ C的short_mask在B的当前bar对应位置为True
```

| 属性 | 评估 |
|------|------|
| **来源** | `_compute_holding_masks()` — `filter_engine.py` L975-L1020 |
| **优点** | 精确到 bar 级别的持仓状态；不是瞬时信号而是持续状态 |
| **缺点** | 需要 C 开启了 strategy 模式并已计算 PnL；mask 依赖于已发生的交易记录（有历史依赖）；计算链路长 |
| **推荐度** | **备选方案**：当需要更精确判断时使用 |

### 定义 3: PnL 方向同向 (D 周期推荐)

**含义**：D 没有更高级周期做 Sig 对齐，使用 D 自身的 PnL 轨迹方向做自包含判断。

```python
D.PnL方向做多 ⇔ D.long_pnl 近期斜率 > 阈值（如最近 N_bar 的线性拟合斜率 > 0）
            OR D.long_pnl[-1] > D.short_pnl[-1]  # 做多曲线优于做空曲线
            OR D.long_pnl[-1] > D.long_pnl 的 N 日前值  # 做多曲线在上升
```

| 属性 | 评估 |
|------|------|
| **来源** | `_compute_strategy_pnl()` — `filter_engine.py` L649，结果存储在 `st.session_state._pnl_{tf}` |
| **优点** | 自包含，不依赖更高级周期；PnL 曲线反映实际策略表现 |
| **缺点** | PnL 随回测窗口变化；需要 D 开启 strategy 模式；滞后性（PnL 是历史累积结果） |
| **推荐度** | **D 周期方向判断专用** |

### 推荐组合

```
B ↔ C: 定义1 (Sig同向) — 简单、可靠、不需要 strategy 模式
D:     定义3 (PnL方向) — D 没有上级，用自包含判断
```

备用方案：当 C 开启了 strategy 且有 PnL 数据时，可以用定义2（持仓Mask）替代或补充定义1，提供更精确的 bar 级确认。

---

## Part 3: 门控决策逻辑 (核心)

### 3.1 总体架构

```
                 ┌──────────┐
                 │ B.Sig    │
                 │ B.边缘区? │
                 └────┬─────┘
                      │
              Step 0: B自身检查
                      │
              ┌───────▼────────┐
              │ B在边缘区?      │──Yes──▶ ⚠️ PENDING (待确认)
              └───────┬────────┘
                      │ No
              ┌───────▼────────┐
              │ B.Sig == +1?   │──No───▶ ❌ INVALID (B不看好)
              └───────┬────────┘
                      │ Yes
              ┌───────▼────────┐
              │ C 可用?         │
              └───┬───────┬────┘
              Yes │       │ No
         ┌────────▼──┐    │
         │ C.Sig==+1? │    │
         └──┬─────┬───┘    │
        Yes │     │ No     │
    ┌───────▼┐ ┌──▼────────▼──────┐
    │✅ VALID│ │ Step 3: D周期兜底 │
    │C确认   │ └────────┬─────────┘
    └────────┘          │
                ┌───────▼────────┐
                │ D 可用?         │
                └───┬───────┬────┘
                Yes │       │ No
           ┌────────▼──┐    │
           │D.PnL做多?  │    │
           └──┬─────┬───┘    │
          Yes │     │ No     │
      ┌───────▼┐ ┌──▼──────┐ ▼──────────┐
      │✅ VALID│ │❌ INVALID│ ⚠️ UNCERTAIN│
      │D确认   │ │C/D不支持 │ 无可参考    │
      └────────┘ └─────────┘ ────────────┘
```

### 3.2 完整决策树 (文字版)

做多判定（做空对称，方向取反）：

```
B周期做多操作有效性判定:

输入: B.Sig, B.在边缘区?, C.Sig, D.PnL方向, C可用?, D可用?

Step 0: B 自身检查
  ├── B 在边缘区 → ⚠️ 标记"待确认"，信号不触发
  └── B 不在边缘区 → 继续

Step 1: B 方向检查
  ├── B.Sig ≠ +1 → ❌ B非做多，无需上级确认
  └── B.Sig = +1 → 继续

Step 2: C 周期同向检查
  ├── C 不可用 → 跳到 Step 3 (降级)
  ├── C.Sig = +1 → ✅ 有效 (B与C同向)
  └── C.Sig ≠ +1 (C=0或C=-1) → 跳到 Step 3 (C不确认，试D)

Step 3: D 周期兜底检查
  ├── D 不可用 → ⚠️ 不确定 (无上级可参考，保守不交易)
  ├── D.PnL方向 = 做多 → ✅ 有效 (C不支持但D确认)
  └── D.PnL方向 ≠ 做多 → ❌ 无效 (C和D均不确认)

结论:
  ✅ VALID    → B 做多信号可信，生成交易
  ❌ INVALID  → B 做多信号被否决，不交易
  ⚠️ PENDING  → B 在边缘区，等待下一 bar 再判断
  ⚠️ UNCERTAIN → 缺少上级数据，保守不交易
```

### 3.3 做空判定 (对称)

```python
# 对称逻辑 — 所有方向取反:
B.Sig = -1 AND B不在边缘区 AND (C.Sig = -1 OR D.PnL方向 = 做空)
```

### 3.4 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| C=0 时是否直接否决 | **否，给 D 机会** | C 在观望不意味着趋势不存在；D 可能有独立判断 |
| C=-1 时是否直接否决 | **否，给 D 机会** | C 可能错判；D 作为更高级周期有更全面的视角 |
| C 和 D 都不可用 | **保守不交易** | 没有上级确认的单向信号，噪声概率高 |
| C 和 D 矛盾 | **C 优先** | C 比 D 更接近 B 的周期级别，信息更相关；D 仅在 C 不可用或 C 不确认时才介入 |

---

## Part 4: 完整决策真值表

### 4.1 做多判定

| # | B.Sig | B.边缘区 | C.Sig | C可用 | D.PnL方向 | D可用 | 结论 | 原因 |
|---|-------|----------|-------|-------|----------|-------|------|------|
| 1 | +1 | No | +1 | Yes | -- | -- | ✅ VALID | C同向确认 |
| 2 | +1 | No | 0 | Yes | 做多 | Yes | ✅ VALID | C观望，D确认 |
| 3 | +1 | No | 0 | Yes | 不做多 | Yes | ❌ INVALID | C观望，D不确认 |
| 4 | +1 | No | -1 | Yes | 做多 | Yes | ✅ VALID | C反向但D确认(覆盖) |
| 5 | +1 | No | -1 | Yes | 不做多 | Yes | ❌ INVALID | C反向，D不确认 |
| 6 | +1 | No | -- | No | 做多 | Yes | ✅ VALID | 仅D可用且确认 |
| 7 | +1 | No | -- | No | 不做多 | Yes | ❌ INVALID | 仅D可用但不确认 |
| 8 | +1 | No | -- | No | -- | No | ⚠️ UNCERTAIN | 无上级周期可参考 |
| 9 | 0 | -- | -- | -- | -- | -- | ❌ INVALID | B本身不看多 |
| 10 | -1 | -- | -- | -- | -- | -- | ❌ INVALID | B看空，不做多 |
| 11 | +1 | **Yes** | -- | -- | -- | -- | ⚠️ PENDING | B在边缘区 |

### 4.2 做空判定 (对称)

| # | B.Sig | B.边缘区 | C.Sig | C可用 | D.PnL方向 | D可用 | 结论 |
|---|-------|----------|-------|-------|----------|-------|------|
| 1 | -1 | No | -1 | Yes | -- | -- | ✅ VALID |
| 2 | -1 | No | 0 | Yes | 做空 | Yes | ✅ VALID |
| 3 | -1 | No | 0 | Yes | 不做空 | Yes | ❌ INVALID |
| 4 | -1 | No | +1 | Yes | 做空 | Yes | ✅ VALID |
| 5 | -1 | No | +1 | Yes | 不做空 | Yes | ❌ INVALID |
| 6 | -1 | No | -- | No | 做空 | Yes | ✅ VALID |
| 7 | -1 | No | -- | No | 不做空 | Yes | ❌ INVALID |
| 8 | -1 | No | -- | No | -- | No | ⚠️ UNCERTAIN |
| 9 | 0 | -- | -- | -- | -- | -- | ❌ INVALID |
| 10 | +1 | -- | -- | -- | -- | -- | ❌ INVALID |
| 11 | -1 | **Yes** | -- | -- | -- | -- | ⚠️ PENDING |

---

## Part 5: 边界情况处理

### 5.1 B 在边缘区

**问题**：趋势滤波（Savitzky-Golay、Butterworth 等）在窗口首尾存在边界效应，Sig 值在最后几个 bar 不可靠。

**判定**：窗口尾部 `edge_width` 个 bar 内的 Sig 值标记为 "PENDING"（待确认）。

**实现参考**：现有 `_find_all_pairs()` 中 `"结束于相反信号的入口（边缘）"` 的概念（`filter_engine.py` L568-L574），当前 pair 的终点是下一个相反信号的起点（即切换的边缘），说明边缘信号需要特殊处理。

```python
B_EDGE_WIDTH = 5  # 窗口尾部被认为边缘区的bar数（可配置）
b_in_edge_zone = (当前bar的窗口内位置 >= len(t) - B_EDGE_WIDTH)
```

**处理**：边缘区的 B.Sig 不触发任何交易，保留标记等待下一轮 bar 推进后重新评估。

### 5.2 C 在边缘区

**问题**：C 作为上级周期也可能在边缘区，其 Sig 同样不可靠。

**处理**：**仍使用 C.Sig 的当前可见值**。理由：
1. C 的精度需求低于 B——C 只需要方向信号，不需要精确的入场时机
2. 即使 C 在边缘区，其 Sig 方向信息仍优于完全不可用
3. 如果 C.Sig 确实错误（与 D 矛盾），门控逻辑会尝试 D 兜底

### 5.3 C 和 D 矛盾 (C看多、D看空)

**处理**：**以 C 为准**。

理由：
- C 比 D 更接近 B 的周期级别（粒度更细）
- C 的信号与 B 在更相近的时间尺度上形成共振
- D 的角色是"兜底"，不是"覆盖"
- 只有当 C 不确认（Sig=0或-1）时，D 才介入

体现在真值表第 4 行：`C=-1, D=做多 → ✅ VALID`（D 的做多覆盖了 C 的负向不确认）。但如果 C=+1 且 D=做空，直接在第 1 行通过（C确认即有效，不过D）。

### 5.4 C 视图未开启 strategy 模式

**处理**：不影响。主判断用 Sig（定义1），Sig 仅需要滤波价格+导数计算（`_compute_schmitt_trigger` → `_schmitt_trigger`），不需要 PnL 数据。

只有当 C 始终未计算（视图未渲染）时，C 才标记为"不可用"，门控降级到 D。

### 5.5 任一周期数据缺失

**回退链**：
```
B 数据缺失 → 无法交易（根本无信号）
C 数据缺失 → 降级到 D
D 数据缺失 → 只有 B+C 判断（没有 D 兜底）
C和D 均缺失 → ⚠️ UNCERTAIN
```

### 5.6 B/C/D 使用不同滤波器

**处理**：各周期独立计算，边缘区宽度各自用自身的 `edge_width`（或统一用 5）。B 的边缘区判断只看 B 自身配置。C/D 的信号取"当前可见值"（当前 bar 的 Sig 或 PnL 方向计算值），不追溯 C/D 自身是否是边缘区。

---

## Part 6: 伪代码实现

### 6.1 B 周期门控判定函数

```python
from typing import Tuple, Optional
from enum import Enum

class GateResult(Enum):
    VALID = "valid"           # 信号有效，可交易
    INVALID = "invalid"       # 信号被否决
    PENDING = "pending"       # B在边缘区，待确认
    UNCERTAIN = "uncertain"   # 缺少上级信息，保守不交易


def validate_b_trade_long(
    b_sig: int,              # B.Sig: +1/0/-1
    b_in_edge_zone: bool,    # B 是否在窗口边缘区
    c_sig: Optional[int],    # C.Sig (None = 不可用)
    d_pnl_direction: Optional[int],  # D.PnL方向: +1做多/0中性/-1做空 (None=不可用)
) -> Tuple[GateResult, str]:
    """
    B周期做多信号门控判定。

    逻辑:
      B做多有效 ⇔ B.Sig=+1 AND B不在边缘区 AND (C.Sig=+1 OR D.PnL方向=做多)

    Returns:
        (GateResult, reason_str)
    """
    # Step 0: B 自身边缘区检查
    if b_in_edge_zone:
        return GateResult.PENDING, "B 在窗口边缘区，信号待确认（等待更多 bar）"

    # Step 1: B 方向检查
    if b_sig != 1:
        return GateResult.INVALID, f"B.Sig={b_sig:+d}，无做多信号，无需上级周期确认"

    # Step 2: C 周期同向检查
    if c_sig is not None:
        if c_sig == 1:
            return GateResult.VALID, "B 与 C 同向做多（C 确认）"
        # C 不确认 (0 或 -1)，不直接否决，给 D 兜底机会
        c_note = "观望" if c_sig == 0 else "反向"
    else:
        c_note = "不可用"

    # Step 3: D 周期兜底
    if d_pnl_direction is not None:
        if d_pnl_direction == 1:
            return GateResult.VALID, f"D 周期 PnL 支持做多（C {c_note}，但 D 确认）"
        else:
            return GateResult.INVALID, f"C {c_note} 且 D 不支持做多，信号被否决"

    # Step 4: 无上级可参考
    if c_sig is None and d_pnl_direction is None:
        return GateResult.UNCERTAIN, "无上级周期数据可确认，保守不交易"

    # 剩余情况：C 不确认且 D 不可用
    return GateResult.INVALID, f"C {c_note} 且 D 不可用，信号被否决"


def validate_b_trade_short(
    b_sig: int,
    b_in_edge_zone: bool,
    c_sig: Optional[int],
    d_pnl_direction: Optional[int],
) -> Tuple[GateResult, str]:
    """B周期做空信号门控判定（对称逻辑）。"""
    if b_in_edge_zone:
        return GateResult.PENDING, "B 在窗口边缘区，信号待确认"
    if b_sig != -1:
        return GateResult.INVALID, f"B.Sig={b_sig:+d}，无做空信号"
    if c_sig is not None:
        if c_sig == -1:
            return GateResult.VALID, "B 与 C 同向做空（C 确认）"
        c_note = "观望" if c_sig == 0 else "反向"
    else:
        c_note = "不可用"
    if d_pnl_direction is not None:
        if d_pnl_direction == -1:
            return GateResult.VALID, f"D 周期 PnL 支持做空（C {c_note}，但 D 确认）"
        else:
            return GateResult.INVALID, f"C {c_note} 且 D 不支持做空，信号被否决"
    if c_sig is None and d_pnl_direction is None:
        return GateResult.UNCERTAIN, "无上级周期数据可确认"
    return GateResult.INVALID, f"C {c_note} 且 D 不可用，信号被否决"
```

### 6.2 D 周期 PnL 方向计算

```python
def compute_d_pnl_direction(
    d_long_pnl: np.ndarray,
    d_short_pnl: np.ndarray,
    lookback: int = 10,
) -> int:
    """
    计算 D 周期的 PnL 方向 (做多 / 中性 / 做空)。

    判断逻辑:
      1. 做多曲线近期斜率（线性拟合）> 0 → +1
      2. 做空曲线近期斜率 > 0 → -1
      3. long_pnl[-1] vs short_pnl[-1] 比较 → 谁的近期回撤小/曲线高谁占优
      4. 都不满足 → 0 (中性)

    Args:
        d_long_pnl: D 周期做多 PnL 曲线
        d_short_pnl: D 周期做空 PnL 曲线
        lookback: 计算斜率的回溯 bar 数

    Returns:
        +1: 做多, -1: 做空, 0: 中性
    """
    n = len(d_long_pnl)
    if n < lookback:
        return 0

    recent_l = d_long_pnl[-lookback:]
    recent_s = d_short_pnl[-lookback:]

    # 线性拟合斜率
    x = np.arange(lookback)
    slope_l = np.polyfit(x, recent_l, 1)[0]
    slope_s = np.polyfit(x, recent_s, 1)[0]

    # 做多势头
    long_momentum = (slope_l > 0.01) and (d_long_pnl[-1] > 100.0)
    # 做空势头
    short_momentum = (slope_s > 0.01) and (d_short_pnl[-1] > 100.0)

    if long_momentum and not short_momentum:
        return 1
    elif short_momentum and not long_momentum:
        return -1
    elif long_momentum and short_momentum:
        # 两者都在涨 → 比较谁更强
        return 1 if d_long_pnl[-1] >= d_short_pnl[-1] else -1
    else:
        return 0
```

### 6.3 B 边缘区判断

```python
def is_b_in_edge_zone(
    bar_idx: int,
    n_total_bars: int,
    edge_width: int = 5,
) -> bool:
    """
    判断 B 周期当前 bar 是否在窗口尾部边缘区。

    边缘区定义：窗口尾部 edge_width 个 bar，该区域的滤波值受边界效应影响。

    Args:
        bar_idx: 当前 bar 的索引 (0-based)
        n_total_bars: 窗口内总 bar 数
        edge_width: 边缘区宽度，默认 5

    Returns:
        True 如果在边缘区
    """
    return bar_idx >= n_total_bars - edge_width
```

### 6.4 主集成入口

```python
def should_execute_b_trade(
    b_cfg: dict,       # B 周期视图配置
    b_schmitt: dict,   # B 周期的 schmitt 结果 {"sig": ..., "dur": ...}
    b_t: np.ndarray,   # B 周期时间轴
    configs: list,     # 全部4个视图的配置列表
    session_state,     # st.session_state
) -> Tuple[bool, str]:
    """
    B 周期交易执行前的主门控入口。

    此函数在 _compute_strategy_pnl 调用之前执行，
    用于过滤不应交易的信号。

    Returns:
        (should_trade, reason)
    """
    # 1. 识别 A/B/C/D
    a_tf, b_tf, c_tf, d_tf = identify_abcd_periods(configs)  # Part 1

    # 2. B 自身状态
    current_bar_idx = len(b_t) - 1
    b_in_edge = is_b_in_edge_zone(current_bar_idx, len(b_t), edge_width=5)
    b_sig = b_schmitt["sig"][current_bar_idx]

    # 3. C 周期 Sig
    c_sig = get_available_sig(c_tf, configs, session_state)

    # 4. D 周期 PnL 方向
    d_pnl_dir = get_d_pnl_direction(d_tf, session_state)

    # 5. 执行门控
    result, reason = validate_b_trade_long(
        b_sig=b_sig,
        b_in_edge_zone=b_in_edge,
        c_sig=c_sig,
        d_pnl_direction=d_pnl_dir,
    )

    # 做空: 同理调用 validate_b_trade_short

    return result == GateResult.VALID, reason
```

---

## Part 7: 与现有代码的集成

### 7.1 集成点映射

| 现有组件 | 文件:行号 | 集成方式 |
|----------|-----------|----------|
| `ALL_TFS` | `sidebar.py:13` | A/B/C/D 周期排序的基准全序 |
| `TF_HIERARCHY` | `sidebar.py:100-104` | 确定紧邻上级关系，辅助回退链 |
| `_schmitt_trigger()` | `filter_engine.py:434-517` | C 周期的 Sig 来源（定义1） |
| `_compute_strategy_pnl()` | `filter_engine.py:649-728` | C/D 周期的 PnL 数据来源 |
| `_align_pnl_to_current_tf()` | `filter_engine.py:867-972` | B 参考 C/D PnL 时的时序对齐（已有） |
| `_compute_holding_masks()` | `filter_engine.py:975-1020` | 可选的定义2 同向判断 |
| `_add_alignment_subplot()` | `charts.py:399-462` | 可视化 B 与 C/D 的同向性（已有基础设施） |
| `st.session_state._pnl_{tf}` | `streamlit_app.py:308` | C/D 周期的 PnL 数据存储 |
| `_compute_strategy_display()` | `streamlit_app.py:279-313` | **主集成点**: 在调用 `_compute_strategy_pnl` 之前插入门控判定 |
| `_compute_schmitt_trigger()` | `streamlit_app.py:249-257` | B/C 周期的 Sig 获取入口 |

### 7.2 最小侵入集成方案

```python
# streamlit_app.py, _compute_strategy_display() 内 (L279-L313)

# === 现有逻辑 ===
if show_strategy and schmitt is not None and len(pred_pairs) > 0:
    # ★ 新增: B周期门控判定 ★
    if tf == b_tf:  # 只有当前渲染的是 B 周期才做门控
        should_trade, reason = should_execute_b_trade(
            b_cfg=cfg, b_schmitt=schmitt, b_t=t,
            configs=configs, session_state=st.session_state,
        )
        if not should_trade:
            logger.info(f"B周期门控否决: {reason}")
            # 可选: 在UI显示一个st.caption提示
            return None, None, []  # 不计算 PnL

    long_pnl, short_pnl, trade_records = _compute_strategy_pnl(...)
```

### 7.3 不修改的组件

- `_schmitt_trigger` — 纯函数，Sig 计算不受门控影响
- `_align_pnl_to_current_tf` — 时序对齐复用，不改动
- `_add_alignment_subplot` — 可视化复用，不改动
- 侧边栏参数面板 — 不新增门控 UI 控件（第一阶段），后续可加开关

### 7.4 数据流

```
用户配置4个视图周期
       │
       ▼
  identify_abcd_periods(configs)
       │
  ┌────▼────┐
  │ A  B  C  D │  (排序后的周期)
  └────┬────┘
       │
  B周期渲染时:
       │
  ┌────▼──────────────────────┐
  │ 1. _compute_schmitt_trigger(filtered, t, cfg) → schmitt
  │ 2. b_sig = schmitt["sig"][-1]
  │ 3. b_in_edge = is_b_in_edge_zone(...)
  │ 4. c_sig = get_c_sig(...)           ← 从 session_state 或重新计算
  │ 5. d_pnl_dir = get_d_pnl_direction(...)  ← 从 session_state._pnl_D
  │ 6. result = validate_b_trade_long(...)
  └────┬──────────────────────┘
       │
  result == VALID? ──Yes──▶ _compute_strategy_pnl() → 生成交易
       │ No
       ▼
  跳过/标记，不交易
```

---

## 附录 A: 函数签名参考

```python
# filter_engine.py
def _schmitt_trigger(v, a, ewma_span=60, k_eps=0.15, sigma_min=0.05) -> dict | None
def _find_all_pairs(sig_t) -> list[tuple[int, int]]
def _compute_strategy_pnl(t, filtered, sig_t, all_pairs, pred_pairs, stop_loss_pct, n_extend=10) -> tuple
def _align_pnl_to_current_tf(higher_dates, higher_pnl_long, higher_pnl_short, higher_trades, current_dates) -> dict
def _compute_holding_masks(n_bars, entry_markers, exit_markers) -> tuple[np.ndarray, np.ndarray]

# sidebar.py
ALL_TFS = ["1分钟","5分钟","15分钟","60分钟","日线","周线","月线","季线"]
TF_HIERARCHY = {"1分钟":"5分钟", "5分钟":"15分钟", ..., "季线":None}
```

## 附录 B: GateResult 状态机

```
     ┌─────────┐
     │ PENDING │ ← 边缘区等待
     └────┬────┘
          │ bar 推进，退出边缘区
          ▼
     ┌─────────┐
     │VALID    │ → 通过门控 → 生成交易
     │INVALID  │ → 否决 → 跳过
     │UNCERTAIN│ → 保守跳过
     └─────────┘
```
