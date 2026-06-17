# 施密特触发预测曲线 — 完整说明文档

**分支:** `schmitt-prediction`（已合并 master）
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
  2. 相邻异号段配对，结束于相反信号的「入口边缘」
  3. 同号相邻段不合并（+1→0→+1 是两次独立多头信号）

示例:
  sig:  [0,0,0, +1,+1, 0,0, -1,-1, 0, +1,+1, 0, -1,-1, 0]
  segments: [(3,4,+1), (7,8,-1), (10,11,+1), (13,14,-1)]
  pairs: [(3,7), (7,10), (10,13)]  ← 每对止于相反信号的入口

对类型:
  +1→-1边: 0→+1 起始 → +1段 → 0区 → 0→-1 边缘结束
  -1→+1边: 0→-1 起始 → -1段 → 0区 → 0→+1 边缘结束
```

### 2.2 二次多项式拟合 — `_fit_parabolic(x, y, start, end)`

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
| 预测曲线 ☑ | on/off | on | 启用预测曲线（勾选后出现预测点） |
| 预测点 ▬ | 1~50 | 10 | 每对前向预测延伸点数 |
| k_ε ▬ | 0.01~0.50 | 0.15 | Schmitt 灵敏度系数 |
| σ_min ▬ | 0.01~0.20 | 0.05 | 地板保护 |
| N_EWMA ▬ | 10~120 | 60 | EWMA 周期 |

布局：`[周期▼] [N▬] [施密特☑] [预测曲线☑] [预测点▬] [k_ε▬] [σ_min▬] [N_EWMA▬]`（8 列）

---

## 四、代码结构

### 新增函数（3 个）

| 函数 | 行号 | 用途 |
|------|------|------|
| `_find_all_pairs(sig_t)` | ~616 | 识别窗口中所有多空切换对 |
| `_fit_parabolic(x, y, start, end)` | ~655 | 二次多项式拟合 |
| `_add_prediction_traces(fig, t, fit_result, ...)` | ~667 | 绘制拟合线 + 前向预测线 |

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
