# 价格子图定位 bug 诊断报告

## 诊断方法

1. 在 `filter/streamlit_app.py` 的 `_render_chart` 中完整复现 Step 9-10 的 figure 构造
2. 合成 60 根 bar 的股票数据，计算 savgol 滤波、施密特触发器、预测曲线、策略 PnL
3. 配齐最复杂的 **8+1 行**布局（has_s + has_strategy + has_cross + has_alignment + has_feedback）
4. 逐一打印每个 trace 的 `xaxis`/`yaxis` 引用，对照 layout 中存在的 axis 键名
5. 额外测试了无 feedback、仅 Schmitt、无 Schmitt 三种变体

诊断脚本：`/Users/xfpan/claude/filter_research/diagnose_axis.py`

---

## 问题一：`_add_prediction_traces` 的 axis 引用未处理 row=1 特例

### 现象

`_add_prediction_traces` 在 `components/charts.py:178-216` 中构造拟合段和前向延伸 trace 时使用：

```python
xaxis=f"x{row}", yaxis=f"y{row}"
```

当 `row=1`（即主图价格 row），生成 `xaxis="x1"`、`yaxis="y1"`。

### 为什么错

Plotly 的 axis id 命名约定：
| row | 正确的 trace `xaxis` 值 | 对应的 layout 键 |
|-----|------------------------|-----------------|
| 1   | `"x"`                  | `xaxis`         |
| 2   | `"x2"`                 | `xaxis2`        |
| 3   | `"x3"`                 | `xaxis3`        |

`"x1"` 不是有效引用 — `make_subplots` 从不为第一个 subplot 创建 `xaxis1`（只有 `xaxis`）。

`_add_main_price_traces` 已正确实现此逻辑（`streamlit_app.py:388-389`）：

```python
_ax = "x" if mr == 1 else f"x{mr}"
_ay = "y" if mr == 1 else f"y{mr}"
```

但 `_add_prediction_traces` **缺少**同样的处理。

### 当前 plotly 版本的侥幸

在 plotly.py 5.x 中，`go.Figure(data=..., layout=...)` 构造时会将 `xaxis="x1"` 自动归一化为 `xaxis="x"`。但 plotly.js 客户端在 JSON 反序列化时**是否做同样的归一化存在不确定性**，且这是未定义行为，不保证跨版本一致。

### 影响范围

每次调用 `_add_prediction_traces(row=1)` 都会产生 2 条错引 trace（拟合段 + 前向延伸段）。在 has_s + has_strategy 的最复杂配置下，4 组预测曲线共产生 **8 条错引 trace**。运行诊断脚本证实了这一点：

```
*** BAD *** [3] 预测曲线(拟合) (scattergl) xaxis=x1→xaxis1 yaxis=y1→yaxis1
*** BAD *** [4] 预测曲线(预测) (scattergl) xaxis=x1→xaxis1 yaxis=y1→yaxis1
*** BAD *** [6] 预测曲线(拟合) (scattergl) xaxis=x1→xaxis1 yaxis=y1→yaxis1
...共 8 条...
```

测试的 4 个变体中有 3 个受到影响（"无 Schmitt" 变体无预测曲线，未触发）。

---

## 问题二：轴标题写到 `yaxis1` 而非 `yaxis`

### 现象

`streamlit_app.py:875-876`：

```python
for _r, _t in [(mr,"价格"),(rr,"残差"),(vr,"速度")]:
    layout_dict.setdefault(f"yaxis{_r}", {}).update(title_text=_t)
```

`mr=1` → `layout_dict.setdefault("yaxis1", {}).update(title_text="价格")` — **此 `yaxis1` 在 layout 中不存在**，是一条孤儿轴键。真实的第 1 subplot 的 yaxis 键是 `yaxis`（无后缀）。

### 后果

诊断日志确认：

```
yaxis: domain=[0.6495, 1.0]  title=            ← 有 domain，无 title
yaxis1: domain=N/A            title=价格        ← 有 title，无 domain，不被任何 trace 引用
```

价格子图的 y-axis 标签 **丢失**。残差（`yaxis2`）和速度（`yaxis3`）标题正确显示，仅 "价格" 不显示。

---

## Axis domain 检查结论

所有 9 个 subplot 的 yaxis domain **正确分布**（从 top=1.0 到底部=0.0，`make_subplots` 按 `row_heights` 正确计算），无重叠或越界。

---

## 根因一句话

两个独立的 axis 命名 bug：

1. **预测曲线 trace** `xaxis=f"x{row}"` 在 `row=1` 时产生了不存在的 `"x1"`引用（应改为 `"x"`）
2. **y-axis 标题代码** `f"yaxis{_r}"` 在 `_r=1` 时写入了不存在的 `"yaxis1"` 键（应改为 `"yaxis"`）

---

## 修复建议

### 修复 1：`components/charts.py` `_add_prediction_traces`

将第 188-204 行的 axis 引用改为：

```python
ax = "x" if row == 1 else f"x{row}"
ay = "y" if row == 1 else f"y{row}"
```

并替换：

```python
xaxis=ax, yaxis=ay   # 替代 xaxis=f"x{row}", yaxis=f"y{row}"
```

以及第 195 行的残差 subplot 引用：

```python
# 注意：残差 subplot 的 row+1 不会等于 1，此处的 f"x{row+1}" 是安全的
# 但为一致性和 future-proof，也可改为：
_nxt = row + 1
_ax_r1 = "x" if _nxt == 1 else f"x{_nxt}"
_ay_r1 = "y" if _nxt == 1 else f"y{_nxt}"
```

### 修复 2：`streamlit_app.py` 轴标题代码，第 875 行

将：

```python
layout_dict.setdefault(f"yaxis{_r}", {}).update(title_text=_t)
```

改为：

```python
_yk = f"yaxis{_r}" if _r != 1 else "yaxis"
layout_dict.setdefault(_yk, {}).update(title_text=_t)
```

---

## 修复后验证方法

重新运行诊断脚本 `diagnose_axis.py`，确认所有 trace 的 axis 引用为 **OK**，且 `yaxis`（而非 `yaxis1`）的 title 为 "价格"。

---

## 修复验证结果（2026-07-12）

两处修复已应用于源码并验证通过：

### 修复 1 验证

`_add_prediction_traces` 对 `row=1` 的正确输出：
```
Trace 0: xaxis=x yaxis=y name=预测曲线(拟合)
Trace 1: xaxis=x yaxis=y name=预测曲线(预测)
Trace 2: xaxis=x2 yaxis=y2 name=预测曲线(残差)
```

### 修复 2 验证

Layout 中 `yaxis` 的 title 正确显示 "价格"，孤儿键 `yaxis1` 不再出现：
```
yaxis: domain=[0.6495, 1.0]  title=价格    ← 正确
（yaxis1 不再存在于 layout 中）
```

### 全量测试结果

4 个测试变体共 **123 条 trace**，全部 `ALL OK`，0 条 BAD：

| 测试变体                     | traces | OK   | BAD |
|------------------------------|--------|------|-----|
| full                         | 44     | 44   | 0   |
| no_feedback                  | 44     | 44   | 0   |
| schmitt_only                 | 29     | 29   | 0   |
| no_schmitt                   | 6      | 6    | 0   |

所有 yaxis domain 正确分布，无重叠或越界。
