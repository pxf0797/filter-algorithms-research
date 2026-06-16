# 施密特触发预测曲线 — 详细说明文档

**分支:** `schmitt-prediction`
**更新日期:** 2026-06-17
**文件:** `streamlit/streamlit_app.py`

---

## 一、功能概述

基于施密特触发器（Schmitt Trigger）的多空信号，在价格曲线上自动叠加**二次多项式预测曲线**。核心特性：

- 对窗口中**每一对多空切换**进行独立拟合与预测
- 每个切换对左半段用二次多项式拟合（实线），右半段外推预测（虚线）
- 最新一对额外做前向延伸预测
- 一键开关，紫色统一配色

### 视觉示意

```
┌── 切换对 #1 (最早的) ──────────────────────────┐
│ [═════ 左半拟合(实线) ═════][- - 右半预测(虚线) - -]│
└────────────────────────────────────────────────┘
┌── 切换对 #2 ───────────────────────────────────┐
│ [═══ 左半拟合 ═══][- - - 右半预测 - - -]         │
└────────────────────────────────────────────────┘
┌── 切换对 #N (最新的) ───────────────────────────┐
│ [══ 左半拟合 ═══][- - 右半预测 - -]· · ·前向延伸· ·│
└────────────────────────────────────────────────┘
```

---

## 二、算法详解

### 2.1 多空切换对发现

**函数:** `_find_all_pairs(sig_t)`

```
输入: sig_t — Schmitt 触发信号数组
      +1 = 看多, -1 = 看空, 0 = 观望

Step 1: 扫描所有非零段
  sig: [0,0, +1,+1,+1, 0,0, -1,-1, 0, +1,+1, 0]
        └ segments ──────────┘└ seg2 ─┘└ seg3 ─┘
        seg1=(2,4,+1), seg2=(7,8,-1), seg3=(10,11,+1)

Step 2: 相邻异号段配对
  seg1(+1) vs seg2(-1) → 异号 → pair: (2, 8)
  seg2(-1) vs seg3(+1) → 异号 → pair: (7, 11)

输出: [(2, 8), (7, 11)]
```

**关键规则：**
- 相邻段必须符号不同才成对（+1 接 -1 或 -1 接 +1）
- 同号相邻段（不应出现，但防御性跳过）
- 窗口中最少 2 段非零且符号不同才产生 pair

### 2.2 二次多项式拟合

**函数:** `_fit_parabolic(x, y, start, end)`

```python
x_seg = x[start:end+1]     # 左半段索引范围
y_seg = y[start:end+1]     # 对应滤波价格
coeffs = np.polyfit(x_seg, y_seg, 2)  # 最小二乘拟合 ax² + bx + c
y_fit = np.polyval(coeffs, x_seg)     # 拟合值
return {"a": coeffs[0], "b": coeffs[1], "c": coeffs[2], "y_fit": y_fit}
```

- 拟合对象：**滤波后的价格曲线**（非原始收盘价）
- 要求 ≥3 个数据点
- 使用 NumPy `polyfit` 最小二乘法

### 2.3 三段式曲线绘制

**函数:** `_add_prediction_traces(fig, t, filtered, fit_result, fit_start, mid, pred_end, row, n_extend, show_legend)`

每个切换对在价格子图上画出三段：

| 区间 | x 范围 | 线型 | 说明 |
|------|--------|------|------|
| 拟合段 | `[fit_start, mid]` | 实线 `width=2` | 左半段，用拟合多项式值 |
| 验证段 | `[mid, pred_end]` | 虚线 `dash='dash'` | 右半段，多项式外推，可与实际价格对比 |
| 前向段 | `[pred_end, pred_end+n_extend]` | 点线 `dash='dot'` | 超出数据范围的前向预测 |

**中点计算：**
```python
mid = (pair_start + pair_end) // 2
```

**前向延伸：**
- 仅最新一对（最后一个 pred_pairs）绘制前向段
- 延伸点数由「预测点」滑块控制（1~50）

### 2.4 图例控制

- 仅第一对（`i == 0`）显示图例条目
- 后续对共享 `legendgroup="预测曲线"`，不重复占位
- 三条线分别标注：拟合、验证、前向

---

## 三、UI 参数

参数面板 Row 1，当勾选「施密特」后可见：

| 参数 | 范围 | 默认 | 说明 |
|------|------|------|------|
| 施密特 ☑ | on/off | on | 启用 Schmitt 触发器 |
| 预测曲线 ☑ | on/off | on | 启用预测曲线叠加 |
| 预测点 ▬ | 1~50 | 10 | 前向延伸点数（仅最新一对） |

**配色：** 统一紫色 `#a371f7`，填充 `rgba(163,113,247,0.08)`

---

## 四、修改的文件

| 文件 | 改动 | 行数变化 |
|------|------|----------|
| `streamlit/streamlit_app.py` | 新增/替换 3 个函数，修改 2 处集成点 | +79 / -47 |

### 新增函数

| 函数 | 行号 | 用途 |
|------|------|------|
| `_find_all_pairs(sig_t)` | ~616 | 扫描所有非零段，找出相邻异号对 |
| `_fit_parabolic(x, y, start, end)` | ~645 | 二次多项式拟合（逻辑不变） |
| `_add_prediction_traces(...)` | ~658 | 三段式曲线绘制（签名从 `fit_start,fit_end` 改为 `fit_start,mid,pred_end`） |

### 修改位置

| 位置 | 改动 |
|------|------|
| `_render_params()` Row 1 | 列布局 7→8，新增「预测点」滑块 |
| `_render_chart()` 预测区块 | `pred_result` → `pred_pairs` 列表，循环处理 |
| `_render_chart()` 图表区 | `for pp in pred_pairs:` 循环添加 traces |

### 未修改

- `db.py` — SQLite 数据层
- `_schmitt_trigger()` — 触发器算法
- `_render_params()` 其他部分
- 数据获取/缓存/配置导入导出

---

## 五、与其他功能的关系

| 功能 | 关系 |
|------|------|
| Schmitt 触发器 | 预测功能依赖 Schmitt，关闭 Schmitt 则预测自动隐藏 |
| 滤波算法 | 预测对象是滤波后的价格曲线 |
| 双滤波对比 | 预测仅应用于滤波器 1 的输出 |
| 时间窗口导航 | 预测曲线随数据窗口变化动态刷新 |
| 周期切换 | 各周期独立计算，预测曲线随周期数据更新 |

---

## 六、提交记录

```
5d29075 feat: all-pair prediction — every long/short switch pair in window gets left-fit + right-predict
e9947d6 docs: add design spec and implementation plan for schmitt prediction curve
8173908 fix: prediction dashed line never rendered — min() guard clamped n_ext to 0
eaf1980 feat: configurable prediction points, purple color scheme
99a773b fix: correct fill polygon y-coordinate length mismatch in prediction traces
760c07f feat: add Schmitt trigger prediction curve with parabolic fitting
```

---

## 七、未来扩展方向

- [ ] 拟合度 R² 标注在每个 pair 上
- [ ] 预测段与实际价格的误差指标（MAE/RMSE）
- [ ] 多阶拟合选项（3 次/4 次多项式）
- [ ] 颜色按预测准确度分级
- [ ] 导出预测数据到 CSV
