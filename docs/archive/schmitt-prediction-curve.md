# 施密特触发预测曲线 — 完整说明文档

**分支:** `master`（已合并）
**文件:** `filter_app/streamlit_app.py`
**日期:** 2026-06-19

---

## 一、功能概述

基于施密特触发器（Schmitt Trigger）的多空信号，在价格曲线上自动叠加**拟合曲线 + 前向预测**，并在残差子图上显示预测偏离。

### 核心流程

```
Schmitt 信号 → 识别所有多空切换对 → 每对全段拟合 → 价格: 橙实线+紫虚线 → 残差: 红/绿虚线
```

### 视觉示意

```
价格子图:
  ┌── 多空对 #1 ──┐          ┌── 多空对 #2 ──┐
  [══ 橙色拟合 ════]··紫预测··[══ 橙色拟合 ════]··紫预测··

残差子图:
  ··红/绿残差线··              ← 预测段偏离最后已知价的幅度

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
  2. 合并相邻同号段（+1,0,+1 → 一个连续多头段）
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
- 前向预测：全局坐标 x 代入多项式

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

### 2.3 曲线绘制 — `_add_prediction_traces(fig, t, filtered, fit_result, ...)`

每个多空对在**价格子图(row=1)**绘制两段，**残差子图(row=2)**绘制一段：

| 子图 | 段 | x 范围 | 线型 | 颜色 | 图例 |
|------|----|--------|------|------|------|
| 价格 | 拟合段 | `[pair_start, pair_end]` | 实线 width=2 | 橙 `#f0a040` | 预测曲线(拟合) |
| 价格 | 前向预测 | `[pair_end, pair_end+N]` | 虚线 dash | 紫 `#a371f7` | 预测曲线(预测) |
| 残差 | 预测偏离 | `[pair_end, pair_end+N]` | 点线 dot | 红/绿 | 预测曲线(残差) |

- N 由「预测点」滑块控制（1~50，默认 10）
- 所有历史对均绘制
- 仅第一对显示图例，其余共享 `legendgroup`
- 残差 = 预测值 − 最后已知滤波价 `filtered[pair_end]`
- 残差颜色：红 `#f85149` = 预测趋势向上，绿 `#3fb950` = 预测趋势向下

### 2.4 Sig_t 背景色带

在 Sig 子图上用半透明矩形标记每个多空对：

| 对结尾 | 填充区 | 含义 |
|--------|--------|------|
| +1 | y: [0, 1] | 多头区（观→多） |
| -1 | y: [-1, 0] | 空头区（空→观） |

交替色：浅蓝 `rgba(88,166,255,0.10)` / 浅紫 `rgba(163,113,247,0.10)`

### 2.5 工具提示（Crosshair Tooltip）

光标悬停时，自定义 tooltip 显示：

- 日期（dateStr）
- 光标 x 值
- 每个**有数据在光标附近**的 trace 的 y 值
  - 每条 trace 独立查找最近 x 点（不再共用全局索引）
  - 过滤填充/色带类 trace（`hoverinfo='skip'`）
  - 距离检查：`|nearest_x - cursor_x| ≤ 1.0` 才显示
  - 预测曲线仅在光标接近其数据范围时出现

---

## 三、UI 参数

### 布局概览（Scheme C+A）

Schmitt OFF 时极简，ON 时展开：

```
Row 1: [周期▼] [N▬] [施密特☑] [预测☑] [▲▼]    ← 始终可见

▸ 施密特参数(默认展开)                             ← Schmitt ON 时
  k_ε ?  label+slider 同行
  σ_min ?  label+slider 同行
  N_EWMA ?  label+slider 同行

▸ 预测参数(默认展开)                               ← Schmitt+预测 ON 时
  [拟合方式: 二次多项式 | 抛物线拟合]  [预测点数▬]

▸ 滤波参数 · Savitzky-Golay 滤波(默认收起)         ← 滤波参数折叠
  [窗口大小▬] [多项式阶数▬] [🎨]
```

### 参数表

| 参数 | 范围 | 默认 | 说明 |
|------|------|------|------|
| 周期 ▼ | 1min~季线 | 日线 | 数据周期 |
| N ▬ | 20~300 | 120 | 显示点数 |
| 施密特 ☑ | on/off | on | 启用 Schmitt 触发器 |
| 预测 ☑ | on/off | on | 启用预测曲线（Schmitt ON 后出现） |
| ▲/▼ | 切换 | — | 本视图全部参数展开/折叠 |
| k_ε ? ▬ | 0.01~0.50 | 0.15 | 灵敏度系数。ε_t=k_ε·max(σ_t(v),σ_min) |
| σ_min ? ▬ | 0.01~0.20 | 0.05 | 地板保护，防止低波动下 ε_t→0 |
| N_EWMA ? ▬ | 10~120 | 60 | EWMA 周期。α=2/(N+1) |
| 拟合方式 ○ | 二次/抛物线 | 二次 | 二次多项式(3参数) / 抛物线(顶点固定,1参数) |
| 预测点数 ▬ | 1~50 | 10 | 每对前向预测延伸点数 |
| 滤波参数 ▬ | 视滤波器 | 视滤波器 | 滤波器1的参数，exapnder 折叠 |
| 滤波参数2 ▬ | 视滤波器 | 视滤波器 | 滤波器2的参数（双滤波时），exapnder 折叠 |

> ? = help tooltip 悬停显示

### 导出/导入

所有参数（含 fit_mode、n_ext、show_pred、滤波参数）导出为 JSON。导入时 `session_state` 逐 key 恢复，`_render_params` 末尾从 session_state 强制读回确保一致性。

---

## 四、代码结构

### 函数（4 个新增）

| 函数 | 行号 | 用途 |
|------|------|------|
| `_find_all_pairs(sig_t)` | ~616 | 识别窗口中所有多空切换对（含同号合并） |
| `_fit_parabolic(x, y, start, end)` | ~655 | 二次多项式拟合 |
| `_fit_physics_parabola(x, y, start, end)` | ~684 | 抛物线拟合（顶点固定在对终点） |
| `_add_prediction_traces(fig, t, filtered, fit_result, ...)` | ~705 | 绘制拟合线 + 前向预测线 + 残差线 |
| `_compact_slider(label, ...)` | ~243 | 无 help 的紧凑滑块（标签|滑块同行） |

### 修改位置

| 位置 | 改动 |
|------|------|
| `_render_params()` | 双行布局 + expanders + 展开/折叠按钮；9 参数 session_state 读回 |
| `_render_chart()` 预测区块 | 多空对计算 → 选择拟合函数 → 存入 `pred_pairs[]` |
| `_render_chart()` 图表区 | 遍历 `pred_pairs`，调用 `_add_prediction_traces`（含 filtered） |
| `_render_chart()` Sig_t 区 | 多空对背景色带 |
| `_render_plotly()` JS | 自定义 tooltip：逐 trace 查找最近 x 点，过滤填充/色带，距离检查 |
| `main()` 导出区 | 新增 `pred`、`fm`、`next` 三个导出字段 |

### 未修改

- `db.py` — SQLite 数据层
- `_schmitt_trigger()` — 触发器算法
- 数据获取 / 缓存 / 配置导入逻辑

---

## 五、与其他功能的关系

| 功能 | 关系 |
|------|------|
| Schmitt 触发器 | 预测功能依赖 Schmitt，关闭则预测自动隐藏 |
| 滤波算法 | 拟合对象是滤波后的价格曲线，非原始收盘价 |
| 双滤波对比 | 预测仅应用于滤波器 1 的输出 |
| 时间窗口导航 | 预测曲线随窗口移动动态刷新 |
| 周期切换 | 各周期独立计算，独立显示 |
| 配置导出/导入 | 全量参数 JSON 序列化，session_state 读回确保一致性 |

---

## 六、交互逻辑

```
施密特 OFF → Row 1 仅 [周期▼] [N▬] [施密特☑]，无 expanders
施密特 ON  → Row 1 [+预测☑] [+▲▼]，显示所有 expanders

预测 ☑ off → 预测 traces 不显示，Sig_t 色带依然可见
预测 ☑ on  → 每对橙色拟合线 + 紫色前向虚线 + 残差子图红/绿虚线
预测点 ▬   → 控制前向延伸点数和残差范围
拟合方式 ○ → 切换二次多项式/抛物线拟合算法
▲/▼       → 独立展开/折叠本视图全部 expander

导入配置  → session_state 全 key 写入 → 末尾强制读回 → UI 状态一致
```

---

## 七、修改清单

### parabola-fit 分支 (21 commits)
```
ui: Scheme C+A layout, compact sliders, expanders, per-view toggle
feat: parabola fit mode (vertex-fixed, 1-param), fit_mode radio
fix: merge same-sign segments, pair edge boundaries
fix: fill polygon, marker rendering, n_ext guard
fix: export/import round-trip, session_state readback
docs: design spec, implementation plan, comprehensive guide
```

### residual-prediction 分支 (4 commits)
```
feat: prediction residual on residual subplot (red/green by direction)
fix: tooltip per-trace nearest-x lookup, distance check, fill/band filter
```
