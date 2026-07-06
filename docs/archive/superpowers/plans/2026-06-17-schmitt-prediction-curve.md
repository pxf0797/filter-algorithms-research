# Schmitt Prediction Curve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在价格子图上叠加基于施密特触发状态的二次多项式预测曲线（实线拟合段 + 虚线延伸段）

**Architecture:** 新增 3 个纯函数（区间扫描、多项式拟合、trace 构建）+ 1 个 UI checkbox + `_render_chart` 内一小段集成代码。全部改动集中在 `filter_app/streamlit_app.py` 单文件（当时为单文件架构），约 100 行新增。

**Tech Stack:** Python 3, NumPy, Plotly

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `filter_app/streamlit_app.py` | Modify (+~100 lines) | 新增预测曲线函数 + UI + 图表集成 |

---

### Task 1: Create management branch

**Files:** None (git operation)

- [ ] **Step 1: Switch to master and create branch**

```bash
cd /Users/xfpan/claude/filter_research && git checkout master && git checkout -b schmitt-prediction
```

Expected: new branch `schmitt-prediction` created from `master`

---

### Task 2: Add prediction curve utility functions

**Files:**
- Modify: `filter_app/streamlit_app.py` — insert 3 new functions after `_schmitt_trigger()` (~line 613)

- [ ] **Step 1: Add `_find_last_complete_pair(sig_t)` function**

Insert after `_schmitt_trigger()` return statement (after line 613):

```python
def _find_last_complete_pair(sig_t):
    """从后往前扫描 sig_t，找最近两段非零区间。
    返回 (fit_start, fit_end) — 从较早段起点到数组末尾。
    找不到则返回 None。"""
    n = len(sig_t)
    if n < 3:
        return None

    i = n - 1
    segments = []  # [(start, end, val), ...] 按从近到远排列

    while i >= 0 and len(segments) < 2:
        if sig_t[i] != 0:
            end = i
            val = sig_t[i]
            while i >= 0 and sig_t[i] == val:
                i -= 1
            start = i + 1
            segments.append((start, end, val))
        else:
            i -= 1

    if len(segments) < 2:
        return None

    # segments[1] 是较早的非零段
    return (segments[1][0], n - 1)
```

- [ ] **Step 2: Add `_fit_parabolic(x, y, start, end)` function**

Insert after `_find_last_complete_pair`:

```python
def _fit_parabolic(x, y, start, end):
    """对 y[start:end+1] 做二次多项式拟合。
    返回 dict {a, b, c, y_fit} 或 None（数据不足）。"""
    x_seg = x[start:end + 1]
    y_seg = y[start:end + 1]
    if len(x_seg) < 3:
        return None
    coeffs = np.polyfit(x_seg, y_seg, 2)
    y_fit = np.polyval(coeffs, x_seg)
    return {"a": coeffs[0], "b": coeffs[1], "c": coeffs[2], "y_fit": y_fit}
```

- [ ] **Step 3: Add `_add_prediction_traces(fig, t, filtered, sig_t, fit_result, fit_start, fit_end, row, n_extend=10)` function**

Insert after `_fit_parabolic`:

```python
def _add_prediction_traces(fig, t, filtered, sig_t, fit_result, fit_start, fit_end, row, n_extend=10):
    """在 price 子图上添加预测曲线：实线拟合段 + 虚线延伸段 + 半透明填充。
    row: Plotly subplot row index (价格子图, 通常为 1)。"""
    current_val = sig_t[fit_end]
    if current_val not in (1, -1):
        return

    color = "#3fb950" if current_val == 1 else "#f85149"
    fill_color = "rgba(63,185,80,0.08)" if current_val == 1 else "rgba(248,81,73,0.08)"
    name = "预测(多)" if current_val == 1 else "预测(空)"

    a, b, c = fit_result["a"], fit_result["b"], fit_result["c"]

    # 拟合段实线
    x_fit = t[fit_start:fit_end + 1]
    y_fit = fit_result["y_fit"]
    fig.add_trace(go.Scatter(
        x=x_fit, y=y_fit,
        mode="lines", name=f"{name}(拟合)",
        line=dict(color=color, width=2),
        legendgroup=name,
        showlegend=True,
    ), row=row, col=1)

    # 前向延伸虚线
    n_ext = min(n_extend, len(t) - fit_end - 1)
    if n_ext > 0:
        x_ext = np.arange(fit_end, fit_end + n_ext)
        y_ext = np.polyval((a, b, c), x_ext)
        fig.add_trace(go.Scatter(
            x=x_ext, y=y_ext,
            mode="lines", name=f"{name}(预测)",
            line=dict(color=color, width=2, dash="dash"),
            legendgroup=name,
            showlegend=True,
        ), row=row, col=1)

        # 延伸段半透明填充
        fig.add_trace(go.Scatter(
            x=list(x_ext) + list(x_ext[::-1]),
            y=list(y_ext) + [filtered[fit_end]] * n_ext + [filtered[fit_end]],
            fill="toself", fillcolor=fill_color,
            line=dict(width=0),
            legendgroup=name,
            showlegend=False, hoverinfo="skip",
        ), row=row, col=1)
```

---

### Task 3: Add "预测曲线" checkbox in parameter panel

**Files:**
- Modify: `filter_app/streamlit_app.py` — `_render_params()` Row 1 columns (~line 631)

- [ ] **Step 1: Adjust column layout and add checkbox**

Replace the Row 1 column definitions in `_render_params()` (lines 630-645):

**Before:**
```python
    c = st.columns([1.0, 0.8, 0.8, 1.1, 1.1, 1.1])
    with c[0]:
        cfg["tf"] = st.selectbox("周期", ALL_TFS, index=ALL_TFS.index(tf_default),
            key=f"{key}_tf", label_visibility="collapsed")
    with c[1]:
        cfg["n_pts"] = st.slider("N", 20, 300, 120, 10, key=f"{key}_n", label_visibility="collapsed")
    with c[2]:
        cfg["show_sch"] = st.checkbox("施密特", value=True, key=f"{key}_sch")
    cfg["ke"]=0.15; cfg["sm"]=0.05; cfg["ew"]=60
    if cfg["show_sch"]:
        with c[3]: cfg["ke"] = st.slider("k_ε",0.01,0.50,0.15,0.05,key=f"{key}_ke",
            help="灵敏度系数,越小越敏感. ε_t=k_ε·max(σ_t(v),σ_min)")
        with c[4]: cfg["sm"] = st.slider("σ_min",0.01,0.20,0.05,0.02,key=f"{key}_sm",
            help="地板保护,防止低波动下ε_t→0")
        with c[5]: cfg["ew"] = st.slider("N_EWMA",10,120,60,10,key=f"{key}_ew",
            help="EWMA周期,α=2/(N+1),越大越平滑")
```

**After:**
```python
    c = st.columns([1.0, 0.8, 0.8, 0.8, 1.1, 1.1, 1.1])
    with c[0]:
        cfg["tf"] = st.selectbox("周期", ALL_TFS, index=ALL_TFS.index(tf_default),
            key=f"{key}_tf", label_visibility="collapsed")
    with c[1]:
        cfg["n_pts"] = st.slider("N", 20, 300, 120, 10, key=f"{key}_n", label_visibility="collapsed")
    with c[2]:
        cfg["show_sch"] = st.checkbox("施密特", value=True, key=f"{key}_sch")
    cfg["ke"]=0.15; cfg["sm"]=0.05; cfg["ew"]=60; cfg["show_pred"]=False
    if cfg["show_sch"]:
        with c[3]: cfg["show_pred"] = st.checkbox("预测曲线", value=True, key=f"{key}_pred")
        with c[4]: cfg["ke"] = st.slider("k_ε",0.01,0.50,0.15,0.05,key=f"{key}_ke",
            help="灵敏度系数,越小越敏感. ε_t=k_ε·max(σ_t(v),σ_min)")
        with c[5]: cfg["sm"] = st.slider("σ_min",0.01,0.20,0.05,0.02,key=f"{key}_sm",
            help="地板保护,防止低波动下ε_t→0")
        with c[6]: cfg["ew"] = st.slider("N_EWMA",10,120,60,10,key=f"{key}_ew",
            help="EWMA周期,α=2/(N+1),越大越平滑")
```

---

### Task 4: Wire prediction logic into chart rendering

**Files:**
- Modify: `filter_app/streamlit_app.py` — `_render_chart()`, Schmitt + figure section (~line 777)

- [ ] **Step 1: Add prediction computation after Schmitt trigger calculation**

After `schmitt = _schmitt_trigger(...)` (line 775), insert:

```python
    # 预测曲线拟合
    pred_result = None
    if cfg.get("show_pred") and schmitt is not None:
        pair = _find_last_complete_pair(schmitt["sig"])
        if pair is not None:
            pred_result = _fit_parabolic(t, filtered, pair[0], pair[1])
            if pred_result is not None:
                pred_result["fit_start"] = pair[0]
                pred_result["fit_end"] = pair[1]
```

- [ ] **Step 2: Add prediction traces to figure after filter traces**

After the `filtered2` trace block (line 801), insert:

```python
    if pred_result is not None:
        _add_prediction_traces(fig, t, filtered, schmitt["sig"],
                               pred_result, pred_result["fit_start"],
                               pred_result["fit_end"], row=mr)
```

---

### Task 5: Verify with a smoke test

**Files:** None (manual verification)

- [ ] **Step 1: Run the Streamlit app**

```bash
cd /Users/xfpan/claude/filter_research && streamlit run filter_app/streamlit_app.py --server.port 8502
```

- [ ] **Step 2: Verify visually**

Check:
- "预测曲线" checkbox appears next to "施密特" checkbox, only when Schmitt is enabled ✅
- 价格子图上显示预测曲线：实线（拟合段） + 虚线（前向延伸段） ✅
- 看多时线条为绿色，看空时为红色 ✅
- 取消勾选 "预测曲线" → 预测线消失 ✅
- 切换时间周期（日线/60分钟/15分钟/5分钟）→ 预测曲线随数据更新 ✅

- [ ] **Step 3: Commit**

```bash
cd /Users/xfpan/claude/filter_research
git add filter_app/streamlit_app.py
git commit -m "feat: add Schmitt trigger prediction curve with parabolic fitting

- _find_last_complete_pair(): scan sig_t for last 2 non-zero segments
- _fit_parabolic(): quadratic polynomial fit on filtered price
- _add_prediction_traces(): solid fit line + dashed forward projection
- '预测曲线' checkbox in param panel, shown when Schmitt enabled"
```
