# 配置参数持久化 — 单一真源机制

> 解决「新增/删除参数时，保存/读取多处手动键表漂移，导致参数漏保存」的问题。
> 历史 bug：`show_pnl_feedback` 在 JSON 导出与 DB 收集两处手动键表里都被漏掉。

## 1. 两条持久化路径

| 路径 | 写入(save) | 读取(load) |
|---|---|---|
| **JSON** | `_render_export_config`（下载 JSON） | `_render_config_import`（上传，**通用全量** `for k,v: AppState.set(k,v)`） |
| **DB** | `collect_current_params` → `save_preset(params_json)` | `apply_preset`（**通用** `json.loads(params_json)`） |

**读取侧都是通用的**（任何 key 都会被读回），**瓶颈只在写入侧**——两处各有一份手动列举的 per-view 键表，容易漏。

## 2. 方案：单一真源 `VIEW_PARAM_SPECS`

在 `config_db.py` 定义唯一的参数清单，写入两侧都从它驱动：

```python
# config_db.py
VIEW_PARAM_SPECS = [
    # (session_state 后缀, cfg 键, 导出默认值)
    ("tf", "tf", None), ("n", "n_pts", None), ("sch", "show_sch", False),
    ...
    ("pnlfb", "show_pnl_feedback", False),   # 新增参数只加这一行
]
VIEW_PARAM_SUFFIXES = [s for s, _, _ in VIEW_PARAM_SPECS]
```

- **JSON 导出**：`_view_export_params(cfg, i)` = `{f"v{i}_{suffix}": cfg.get(cfg_key, default) ...}`，`_render_export_config` 调用它。
- **DB 收集**：`collect_current_params` 用 `VIEW_PARAM_SUFFIXES` 遍历 `st.session_state[f"v{vi}_{suffix}"]`。

**新增/删除可持久化参数 = 只改 `VIEW_PARAM_SPECS` 一处**，两条写入路径自动同步。

## 3. 守卫：防止未来漏接

`tests/test_param_export_import.py::TestParamRegistryGuard`：

- `test_sidebar_params_covered_by_registry`：解析 `sidebar.py` 里所有 `cfg["..."]=` 赋值，断言每个可持久化 cfg 键都在 `VIEW_PARAM_SPECS`（滤波器 `pv`/`pv2` 走独立中文 key 机制，排除）。**新增参数忘接清单 → 此测试直接失败。**
- `test_export_helper_covers_pnlfb` / `test_collect_current_params_includes_pnlfb`：两条写入路径都覆盖新参数。
- `test_registry_includes_pnlfb`：回归锁定历史 bug。
- `test_json_roundtrip_pnlfb`：导出→序列化→读回值不丢。

其余导出完整性测试（`REQUIRED_PER_VIEW_KEYS`）改为**从 `VIEW_PARAM_SPECS` 派生**，自动同步。

## 4. 边界

- 滤波器参数（`pv`/`pv2`，中文 key 如「窗口大小_…」）走独立机制，不在本清单，守卫测试已排除。
- 旧的导出 JSON 文件缺新 key 无妨：导入通用全量 + sidebar 读取有默认值兜底。

---

> 变更历史：v1 = 建立 VIEW_PARAM_SPECS 单一真源 + 守卫测试；补齐历史漏保存的 show_pnl_feedback。
