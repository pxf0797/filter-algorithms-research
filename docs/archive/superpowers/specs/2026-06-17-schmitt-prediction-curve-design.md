# Schmitt Trigger Prediction Curve Design

**日期:** 2026-06-17
**分支:** 待创建 (管理分支)
**状态:** 待实现

---

## 目标

在价格曲线上叠加基于 Schmitt 触发状态的二次多项式预测曲线。

---

## 算法设计

### 1. 寻找最近完整多空切换对

从 `sig_t` 数组（-1空/0观望/+1多）**从后往前扫描**：

```
输入: sig_t (int array, 长度 n)
输出: fit_start_index, fit_end_index (拟合区间 [start, end])

算法:
1. 从 i=n-1 往前扫描，找到第一段非零区间（当前活跃段），记录其起始位置 idx1
2. 继续从 idx1 往前扫描，找到第二段非零区间，记录其起始位置 idx2
3. fit_start = idx2（第二段起点）
4. fit_end   = n-1（当前最新点）
5. 如果找不到两段非零区间 → 返回 None（不画预测曲线）
```

### 2. 二次多项式拟合

```python
def _fit_parabolic(x, y, start, end):
    """对滤波价格 y[start:end] 段做二次拟合，返回 (a, b, c, y_fit)"""
    x_seg = x[start:end]
    y_seg = y[start:end]
    coeffs = np.polyfit(x_seg, y_seg, 2)  # a, b, c for ax² + bx + c
    y_fit = np.polyval(coeffs, x_seg)
    return coeffs[0], coeffs[1], coeffs[2], y_fit
```

### 3. 前向延伸

```python
n_extend = 10  # 向前延伸点数
x_extend = np.arange(fit_end, fit_end + n_extend)
y_extend = np.polyval((a, b, c), x_extend)
```

---

## 显示方式

| 部分 | 线型 | 颜色 |
|------|------|------|
| 拟合区间（实线） | solid, width=2 | 绿（后面段是多）+ / 红（后面段是空）- |
| 前向预测（虚线） | dash, width=2 | 同上 |
| 背景填充 | `fill='tozeroy'`, opacity=0.06 | 同上 |

---

## 代码实现

### 新增函数（3个纯函数 + 1个渲染函数，~110行）

位置: `filter_app/streamlit_app.py`，`_schmitt_trigger()` 之后（模块化后移入 `filter_engine.py`）

```
_find_last_complete_pair(sig_t)         → (start, end) 或 None
_fit_parabolic(x, y, start, end)        → (a, b, c, y_fit)
_build_prediction_traces(x, y_f, sig_t, pred_end_color, n_extend=10)
                                        → list of Plotly trace dicts
```

### 修改位置

| 行号 | 改动 |
|------|------|
| ~613（`_schmitt_trigger` 之后） | 新增 4 个函数 |
| ~632（`_render_params` Row 1） | 新增 checkbox "预测曲线" |
| ~777（`_render_chart` Schmitt 段） | 调用拟合函数 |
| ~789（价格子图 `row=mr`） | 添加预测曲线 traces |

### 不修改

- `db.py`
- 配置文件
- 数据获取/缓存逻辑
- 其他视图

---

## 行为边界

- 找不到两段非零区间 → 不画预测曲线
- 拟合区间数据不足（<3点）→ 不画
- checkbox 未勾选 → 不画
- 向前延伸超数组边界 → 裁剪到数组长度

---

## 分支策略

1. 从 `master` 创建新分支 `schmitt-prediction`
2. 所有改动在该分支上进行
3. 当前 `部署方案研究` 分支不受影响
