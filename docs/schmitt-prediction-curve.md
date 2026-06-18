# 施密特触发预测曲线 — 完整说明文档

**分支:** `parabola-fit`
**文件:** `streamlit/streamlit_app.py`
**日期:** 2026-06-18

---

## 一、功能概述

基于施密特触发器（Schmitt Trigger）的多空信号，在价格曲线上自动叠加**二次多项式拟合曲线 + 前向预测**。

### 核心流程

```
Schmitt 信号 → 识别所有多空切换对 → 每对全段二次拟合 → 橙色拟合线 + 紫色前向预测
```

### 视觉示意

```
价格子图:
  ┌── 多空对 #1 ──┐          ┌── 多空对 #2 ──┐
  [══ 橙色拟合 ════]··紫预测··[══ 橙色拟合 ════]··紫预测··

Sig_t 子图:
  ┌──对1──┐  ┌──对2──┐        ← 半透明色带标记每个对
  ┃ ░░░░░░ ┃  ┃ ░░░░░░ ┃       +1结尾→填[0,1]; -1结尾→填[-1,0]
```

---

## 二、算法详解

### 2.1 多空切换对识别 — `_find_all_pairs(sig_t)`

```
规则:
  1. 收集 sig_t 中所有非零段: [(start, end, ±1), ...]
  2. 合并相邻同号段（+1,0,+1 → 一个连续多头段，将多次入场+观望合并）
  3. 相邻异号段配对，结束于相反信号的「入口边缘」

示例:
  sig:  [0, +1,+1, 0,0, +1,+1, 0, -1,-1, 0]
  segments: [(1,2,+1), (5,6,+1), (8,9,-1)]
  合并:  [+1:1-2] + [+1:5-6] → [+1:1-6]
  merged: [(1,6,+1), (8,9,-1)]
  pairs: [(1,8)]  ← 从首次+1入场，经过0和二次+1，止于-1入口边缘

对类型:
  +1→-1边: 首次0→+1起始 → 多次+1+观望合并 → 0→-1边缘结束
  -1→+1边: 首次0→-1起始 → 多次-1+观望合并 → 0→+1边缘结束
```

### 2.2 拟合方式

#### 二次多项式 — `_fit_parabolic(x, y, start, end)`

```python
x_seg = x[start:end+1]      # 多空对全段
y_seg = y[start:end+1]      # 滤波价格
coeffs = np.polyfit(x_seg, y_seg, 2)  # 最小二乘: ax² + bx + c
y_fit = np.polyval(coeffs, x_seg)     # 拟合值
return {"a": a, "b": b, "c": c, "y_fit": y_fit}
```

- 拟合对象：滤波后的价格曲线
- 拟合范围：整个多空对 `[pair_start, pair_end]`
- 要求 ≥ 3 个数据点

#### 抛物线拟合 — `_fit_physics_parabola(x, y, start, end)`

```python
x0 = x[end]              # 顶点 x = 对终点（转折点）
y0 = y[end]              # 顶点 y = 实际滤波价（固定）
dt = x - x0              # ≤0（左半段）
a = Σ(dt²·(y-y₀)) / Σ(dt⁴)  # 唯一自由参数：曲率
y_fit = y0 + a·dt²       # y = a·(x-x₀)² + y₀
return {"a": a, "b": 0.0, "c": y0, "y_fit": y_fit, "x0": x0}
```

- 顶点 `(x₀, y₀)` 固定在对终点（转折点），不可调整
- 仅曲率 `a` 由最小二乘确定（1 参数 vs 二次多项式的 3 参数）
- 预测段 = 右半抛物线，与左半对称
- 锚点 `x0` 传给 `_add_prediction_traces` 用于局部坐标前向预测

### 2.3 曲线绘制 — `_add_prediction_traces(...)`

每个多空对在价格子图上绘制两段：

| 段 | x 范围 | 线型 | 颜色 | 图例 |
|----|--------|------|------|------|
| 拟合段 | `[pair_start, pair_end]` | 实线 width=2 | 橙 `#f0a040` | 预测曲线(拟合) |
| 前向预测 | `[pair_end, pair_end+N]` | 虚线 dash | 紫 `#a371f7` | 预测曲线(预测) |

- N 由「预测点」滑块控制（1~50，默认 10）
- 所有历史对均绘制前向预测
- 仅第一对显示图例，其余共享 `legendgroup`

### 2.4 Sig_t 背景色带

在 Sig 子图上用半透明矩形标记每个多空对：

| 对结尾 | 填充区 | 含义 |
|--------|--------|------|
| +1 | y: [0, 1] | 多头区（观→多） |
| -1 | y: [-1, 0] | 空头区（空→观） |

交替色：浅蓝 `rgba(88,166,255,0.10)` / 浅紫 `rgba(163,113,247,0.10)`

---

## 三、UI 参数

参数面板 Row 1，当勾选「施密特」后可见：

| 参数 | 范围 | 默认 | 说明 |
|------|------|------|------|
| 施密特 ☑ | on/off | on | 启用 Schmitt 触发器 |
| 预测曲线 ☑ | on/off | on | 启用预测曲线（勾选后出现拟合方式+预测点） |
| 拟合方式 ○ | 二次/抛物线 | 二次 | 二次多项式(3参数) 或 抛物线(顶点固定,1参数) |
| 预测点 ▬ | 1~50 | 10 | 每对前向预测延伸点数 |
| k_ε ▬ | 0.01~0.50 | 0.15 | Schmitt 灵敏度系数 |
| σ_min ▬ | 0.01~0.20 | 0.05 | 地板保护 |
| N_EWMA ▬ | 10~120 | 60 | EWMA 周期 |

布局：`[周期▼] [N▬] [施密特☑] [预测曲线☑] [拟合方式○] [预测点▬] [k_ε▬] [σ_min▬] [N_EWMA▬]`（9 列）

---

## 四、代码结构

### 新增函数（3 个）

| 函数 | 行号 | 用途 |
|------|------|------|
| `_find_all_pairs(sig_t)` | ~616 | 识别窗口中所有多空切换对（含同号合并） |
| `_fit_parabolic(x, y, start, end)` | ~655 | 二次多项式拟合 |
| `_fit_physics_parabola(x, y, start, end)` | ~667 | 抛物线拟合（顶点固定在对终点） |
| `_add_prediction_traces(fig, t, fit_result, ...)` | ~680 | 绘制拟合线 + 前向预测线 |

### 修改位置

| 位置 | 改动 |
|------|------|
| `_render_params()` Row 1 | 8 列布局，新增「预测曲线」「预测点」两个控件 |
| `_render_chart()` 预测区块 (~870) | 多空对计算 + 拟合 + 存入 `pred_pairs[]` |
| `_render_chart()` 图表区 (~900) | 遍历 `pred_pairs`，调用 `_add_prediction_traces` |
| `_render_chart()` Sig_t 区 (~946) | 多空对背景色带 |

### 未修改

- `db.py` — SQLite 数据层
- `_schmitt_trigger()` — 触发器算法
- 数据获取 / 缓存 / 配置导入导出

---

## 五、与其他功能的关系

| 功能 | 关系 |
|------|------|
| Schmitt 触发器 | 预测功能依赖 Schmitt，关闭则预测自动隐藏 |
| 滤波算法 | 拟合对象是滤波后的价格曲线，非原始收盘价 |
| 双滤波对比 | 预测仅应用于滤波器 1 的输出 |
| 时间窗口导航 | 预测曲线随窗口移动动态刷新 |
| 周期切换 | 各周期独立计算，独立显示 |

---

## 六、交互逻辑

```
预测曲线 ☑  off  → 所有预测相关 trace 不显示，Sig_t 色带依然可见
预测曲线 ☑  on   → 每个多空对显示橙色拟合线 + 紫色前向虚线
预测点 ▬   1~50 → 控制每个对前向延伸的点数
施密特 ☑   off  → 预测曲线和色带全部隐藏（依赖 Schmitt 信号）
```

---

## 七、修改清单（21 commits）

```
96e6ae3 Revert residual subplot feature
21616db Forward prediction for all pairs (not just last)
bb3f058 Pair as full fit range (orange fit + purple forward)
591a96f Direction-based colors (reverted later)
ee62ca4 Pair ends at opposite signal edge (s2, not e2)
d87004d Remove same-sign merge (pair raw adjacent segments)
a3ea340 Sig_t pair bands direction-aware fill
5eb98bd Pair background bands on Sig_t subplot
ab4663c Restore prediction points slider
7eeb6ee Merge consecutive same-sign (later removed)
08d34a9 Non-overlapping pairs (later reverted)
2f851ce Simplify to orange fit + purple predict
2eb484b Fix fill polygon marker rendering
5ed13d2 Documentation update
5d29075 All-pair left-fit + right-predict
e9947d6 Design spec + implementation plan
8173908 Fix n_ext=0 bug (min guard)
eaf1980 Configurable prediction points + purple color
99a773b Fix fill polygon y-coordinate mismatch
760c07f Initial prediction curve implementation
```
