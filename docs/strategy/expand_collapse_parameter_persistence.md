# 折叠/展开参数持久化问题说明

> 版本: 1.0 | 日期: 2026-06-20 | 关联: v1.1.0~v1.2.0 多个 Bug 修复

---

## 1. 问题根因

### 1.1 Streamlit 的 Widget Key 生命周期

Streamlit 中每个交互组件（`st.slider`、`st.checkbox` 等）通过 `key=` 参数绑定到 `st.session_state`。当组件渲染时，其值自动写入 session_state。

**核心问题**：当组件不渲染时（如在折叠的 `st.expander` 内，或在 `if` 条件分支的未执行路径中），Streamlit 可能清理该 widget 的 session_state key。后续读取时 key 不存在，回退到硬编码默认值，而非用户设置或导入的值。

### 1.2 触发场景

```
折叠面板 → 内部 widget 不渲染 → st.rerun() 触发
  → widget key 被 Streamlit 标记为 idle → 后续被清理
  → st.session_state.get(key, default) 返回 default（硬编码值）
  → 用户看到参数"被重置"
```

### 1.3 影响链路

```
st.rerun() (展开/折叠/切换周期等)
  → widget key 丢失
  → 初始读取: cfg[key] = st.session_state.get(key, hardcode_default) → 返回默认值
  → widget 渲染: 使用错误的 cfg[key] 作为 value → 显示默认值
  → 最终回读: cfg[key] = st.session_state.get(key, cfg[key]) → 仍是默认值
  → _render_chart 收到错误参数 → 滤波/信号/预测/PnL 全部异常
```

---

## 2. 解决方案：`_imp_` 三级备份机制

### 2.1 机制说明

```
JSON 导入时:  st.session_state[key] = json_value       (widget key)
              st.session_state["_imp_" + key] = json_value  (备份 key，非 widget)

参数读取时:   st.session_state.get(key,
                st.session_state.get("_imp_" + key,
                  hardcode_default))                    ← 三级 fallback
```

**三级优先级**：
1. widget key（组件渲染时写入，最新）
2. `_imp_` key（导入时写入，不受 widget 清理影响）
3. 硬编码默认值（最后兜底）

### 2.2 实现位置

| 位置 | 代码 | 作用 |
|------|------|------|
| `main()` 导入段 (line ~1907) | `st.session_state[f"_imp_{k}"] = v` | 导入时创建备份 |
| `_render_params` 初始读取 | `st.session_state.get(key, st.session_state.get(f"_imp_{key}", default))` | 读取时 fallback |
| `_render_params` widget value | 同上 | 组件初始值正确 |
| `_render_params` 最终回读 | 同上 | 折叠时恢复 |
| `_render_param_slider` | `pdefault = st.session_state.get(key, st.session_state.get(f"_imp_{key}", pdefault))` | 滑块默认值 |

---

## 3. 参数备份状态清单

### 3.1 已有 `_imp_` 备份的参数 ✅

| 参数 | session_state key | 面板位置 | 风险 |
|------|-------------------|---------|------|
| ke (k_eps) | `v{i}_ke` | 施密特 expander | 折叠时丢失 |
| sm (sigma_min) | `v{i}_sm` | 施密特 expander | 折叠时丢失 |
| ew (N_EWMA) | `v{i}_ew` | 施密特 expander | 折叠时丢失 |
| show_pred | `v{i}_pred` | Row1 (条件可见) | show_sch=False时不渲染 |
| fit_mode | `v{i}_fm` | 预测 expander | 折叠时丢失 |
| n_ext | `v{i}_next` | 预测 expander | 折叠时丢失 |
| show_strategy | `v{i}_strat` | 策略 expander | 折叠时丢失 |
| show_cross_pnl | `v{i}_cross_pnl` | 策略 expander | 折叠+strategy关闭时丢失 |
| show_alignment | `v{i}_align` | 策略 expander | 折叠+cross关闭时丢失 |
| filter params (pv) | `{label}_v{i}_f1_{fid}` | 滤波 expander | 折叠时丢失 |
| filter params (pv2) | `{label}_v{i}_f2_{fid2}` | 滤波2 expander | 折叠时丢失 |

### 3.2 已修复但默认值与 JSON 一致的参数 ⚠️

| 参数 | 默认值 | JSON值 | 修复状态 |
|------|--------|--------|---------|
| stop_loss_pct | 2.0 | 2.0 | ✅ 已加（本次） |
| fc | #00d4aa | #00d4aa | ✅ 已加（本次） |
| fc2 | #ff6b6b | #ff6b6b | ✅ 已加（本次） |

### 3.3 不需要 `_imp_` 备份的参数（始终渲染）

| 参数 | 原因 |
|------|------|
| tf | Row1 selectbox，始终可见 |
| n_pts | Row1 slider，始终可见 |
| show_sch | Row1 checkbox，始终可见。`value=True` 硬编码兜底 |

---

## 4. 新增参数接入清单

当需要新增一个 widget 参数时，按以下 Checklist 接入 `_imp_` 机制：

### 4.1 必须修改的 4 个位置

| # | 位置 | 示例 |
|---|------|------|
| 1 | **JSON 导入段** (`main()`) | `st.session_state[f"_imp_{k}"] = v` (已有循环，无需修改) |
| 2 | **初始读取** (`_render_params` 顶部) | `cfg["x"] = st.session_state.get(key, st.session_state.get(f"_imp_{key}", default))` |
| 3 | **widget value 参数** | `st.checkbox(..., value=st.session_state.get(key, st.session_state.get(f"_imp_{key}", default)), key=key)` |
| 4 | **最终回读段** (`_render_params` 尾部) | `cfg["x"] = st.session_state.get(key, st.session_state.get(f"_imp_{key}", cfg.get("x", default)))` |

### 4.2 可选步骤

| # | 位置 | 说明 |
|---|------|------|
| 5 | **导出段** (`main()` 尾部) | `export_data[f"v{i}_x"] = cfg.get("x", default)` |
| 6 | **else 分支** | 如果 widget 在 `if` 块内，`else` 分支也要用 `st.session_state.get(key, st.session_state.get(f"_imp_{key}", default))` |
| 7 | **JSON 配置文件** | 在 `config/*.json` 中添加对应键值 |

### 4.3 关键原则

1. **三级 fallback**: `widget_key → _imp_key → hardcode_default`
2. **JSON 导入时自动创建所有 `_imp_` 备份**（循环遍历 config items）
3. **始终渲染的 widget（Row1）不需要**，但加了也不会有副作用
4. **`st.session_state.get()` 是读操作，不会阻止 key 被清理**。只有 widget 渲染才能 keep key alive。`_imp_` 备份才是真正的兜底

---

## 5. 历史 Bug 回顾

| Bug ID | 问题 | 根因 | 修复版本 |
|--------|------|------|---------|
| BUG-002 | 导入配置后 rerun，所有参数回退默认值 | 无 `_imp_` 备份，widget key 被清理 | v1.1.0 |
| BUG-004 | show_cross_pnl 在 show_strategy=False 后丢失 | checkbox 不渲染，key 被清理 | v1.1.0 |
| BUG-006 | 滤波颜色值折叠后丢失 | fc/fc2 在 expander 内，无备份 | v1.1.0 |
| BUG-007 | 滤波参数 slider rerun 后回退默认值 | `_render_param_slider` 无 `_imp_` fallback | v1.1.0 |
| — | fit_mode 折叠后变为 poly2 | 初始读取无 `_imp_` 备份 | v1.1.0 |
| — | n_ext 折叠后变为 10 | 同上 | v1.1.0 |
| — | stop_loss_pct 可能在 rerun 后丢失 | 缺少 `_imp_` 备份 | v1.3.0 (本次) |
| — | fc/fc2 可能在 rerun 后丢失 | 缺少 `_imp_` 备份 | v1.3.0 (本次) |

---

## 6. 测试验证方法

### 6.1 手动验证

```
1. 导入 config/3690_HK.json
2. 在某个视图修改任意参数（如 stop_loss_pct 改为 5.0）
3. 折叠策略参数 expander
4. 展开策略参数 expander
5. 确认 stop_loss_pct 仍为 5.0（非 2.0）
```

### 6.2 程序化验证

```python
# tests/test_boundary.py 中已有
def test_imp_backup_created_on_import():
    # 验证 _imp_ 备份被正确创建
    ...
```

---

> **总结**: 任何在可折叠面板内或条件渲染的 widget，都必须接入三级 `_imp_` fallback 机制。始终渲染的 Row1 widget 可以例外。
