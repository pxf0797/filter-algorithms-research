# BS 仓位操作标识 — 设计方案 v5

## 1. 核心定义

**BS 标记 = 策略交易记录（trade_records）经过同向性判断（holding_masks）过滤后的结果。**

- BS 的**标的**: `trade_records`（每笔交易含 `entry_idx`, `exit_idx`, `type`, `exit_reason`）
- BS 的**过滤器**: `holding_masks = (long_mask, short_mask)`，由 `_compute_holding_masks` 从上级 PnL 的入场/出场 markers 生成
- 只有 `entry_idx` 落在对应方向 mask 内的交易才标 BS

**关键设计决策 (v5)**: 废弃级联（cascade）机制。所有周期使用统一的过滤逻辑，每个视图独立从自己的 `trade_records + holding_masks` 计算 BS，不再将上级 BS 标记向下级联传播。

## 2. 标记规则

| 方向 | 入场 | 出场 | 颜色 |
|------|------|------|------|
| 做多 | B | S | 绿色 |
| 做空 | S | B | 红色 |

- 做多: 绿 B 入场，绿 S出场
- 做空: 红 S 入场，红 B 出场
- 入场标记显示在 K 线 Low 下方，出场标记显示在 K 线 High 上方

实现位置:
- 标记生成: `bs_marker.py` — 返回 `{"entry_markers": [...], "exit_markers": [...]}`
- 标记渲染: `components/charts.py:_add_bs_markers` — 将 markers 转为 Plotly annotations

## 3. 各子图数据源

| Row | 子图 | BS 使用？ |
|-----|------|----------|
| 1 | 价格 & 滤波 | 显示位置 |
| 2-4 | 残差 / v / a+epsilon | 否 |
| 5 | Sig_t (Schmitt 信号) | 否 |
| 6 | PnL 收益(%) | **是 — BS 的标的** (trade_records 来源) |
| 7 | 实际持仓 | 否 |
| 8 | 同向性判断 | **是 — BS 的过滤器** (holding_masks 来源) |

Row 6 提供 `trade_records`（入场=信号确认点 `pair_end`）。
Row 8 提供 `holding_masks`（上级 PnL 持仓区间）。

## 4. 统一过滤逻辑（无级联）

所有视图（操作周期及低周期）使用完全相同的算法。`compute_bs_markers` 是唯一入口:

```
holding_masks is not None and trade_records 非空 → _compute_from_trades_filtered
仅 trade_records 非空（无上级时回退）          → _compute_own_from_trades
都没有                                         → 空
```

### 4.1 `_compute_from_trades_filtered` — 主路径

遍历 `trade_records`，对每笔交易:

1. 取 `entry_idx`，检查 `mask[entry_idx]`（long 交易查 `long_mask`，short 交易查 `short_mask`）
2. 若 `False` 或 `entry_idx >= len(mask)` → 跳过整笔交易
3. 若 `True` → 标入场 BS（entry_idx），并标出场 BS（exit_idx）

```python
def _compute_from_trades_filtered(t, dates, trade_records, holding_masks):
    long_mask, short_mask = holding_masks
    # 遍历 trade_records，只保留 entry_idx 落在同向 mask 内的交易
```

### 4.2 `_compute_own_from_trades` — 回退路径

当无上级 `holding_masks` 时（如操作周期之上无更高周期），不过滤，直接从 `trade_records` 生成全部 BS 标记。

## 5. 数据流

```
上级视图 _render_chart
  ↓ 计算出 trade_records → 存入 st.session_state["_pnl_{tf}"]
  ↓
本级视图 _render_chart
  ↓ Step 3: 从 session_state 读取上级 PnL → _align_pnl_to_current_tf → higher_pnl
  ↓ Step 8: _compute_strategy_display → 本层 trade_records
  ↓ BS 段: _compute_holding_masks(higher_pnl) → _align_masks (holding_masks)
  ↓        compute_bs_markers(..., holding_masks=_align_masks) → bs_markers
  ↓ Step 10: _add_bs_markers(bs_markers) → Plotly annotations on K-line chart
```

每层独立计算: **本层 trade_records + 上层 holding_masks → 本层 BS**。低周期不复用操作周期的 BS 结果，而是用自己的 trade_records 结合自己的上级 holding_masks 独立计算。

## 6. 系统已有数据复用

BS 标记**不新增任何计算**，全部复用 `_render_chart` 中已有的数据:

| 数据 | 来源 | 计算位置 |
|------|------|---------|
| `trade_records` | `_compute_strategy_display` | Step 8 |
| `higher_pnl` | `_align_pnl_to_current_tf` | Step 3 |
| `_align_masks` | `_compute_holding_masks(higher_pnl)` | BS 段 (line ~784) |
| `higher_pnl` 原始数据 | `st.session_state["_pnl_{higher_tf}"]` | 上级视图渲染时写入 |

## 7. compute_bs_markers 接口

```python
def compute_bs_markers(t, dates, schmitt, all_pairs, trade_records,
                        tf, operating_tf, higher_bs=None,
                        holding_masks=None):
    """
    t : np.ndarray          — bar 索引
    dates : pd.DatetimeIndex — bar 日期
    schmitt : dict or None   — Schmitt 触发器输出（保留但 v5 未使用）
    all_pairs : list         — Schmitt 信号对（保留但 v5 未使用）
    trade_records : list[dict] — 策略交易记录
    tf : str                 — 当前视图周期
    operating_tf : str       — 用户操作周期（保留但 v5 未用于分支）
    higher_bs : dict or None — 保留参数，不再使用
    holding_masks : tuple or None — (long_mask, short_mask) 各为 np.ndarray[bool]
    """
```

**注意**: `schmitt`, `all_pairs`, `tf`, `operating_tf`, `higher_bs` 五个参数在 v5 中**均未使用**，仅保留以兼容旧调用方。实际决策仅依赖 `trade_records` 和 `holding_masks`。

## 8. streamlit_app 集成

在 `_render_chart` 的 BS markers 段 (line ~782-800):

```python
# Step 1: 从上级 PnL 计算 holding_masks
_align_masks = None
if higher_pnl is not None:
    _align_masks = _compute_holding_masks(
        len(t), higher_pnl["entry_markers"], higher_pnl["exit_markers"])

# Step 2: 判断是否需要显示 BS（操作周期或可见低周期）
_op_tf = st.session_state.get("operating_tf", "日线")
_lower_tfs = st.session_state.get("_bs_lower_tfs", [])
_show_bs = (tf == _op_tf) or (tf in _lower_tfs)

# Step 3: 统一调用
bs_markers = None
if _show_bs:
    _holding = _align_masks if _align_masks is not None else None
    bs_markers = compute_bs_markers(
        t, dates, schmitt, all_pairs, trade_records,
        tf, _op_tf, higher_bs=None,
        holding_masks=_holding,
    )
    st.session_state[f"_bs_{tf}"] = bs_markers

# Step 4: 渲染
if bs_markers is not None:
    all_annotations += _add_bs_markers(t, ohlc, bs_markers)
```

`_bs_lower_tfs` 在 `main()` 中预计算 (line ~1927-1931):

```python
_view_tfs = set(cfg["tf"] for cfg in configs)
_all_lower = get_lower_tfs(operating_tf)
_visible_lower_tfs = [tf for tf in _all_lower if tf in _view_tfs]
st.session_state["_bs_lower_tfs"] = _visible_lower_tfs
```

仅操作周期及其以下且**实际可见**的周期才显示 BS 标记。

## 9. `holding_masks` 构造

`_compute_holding_masks(n_bars, entry_markers, exit_markers)` 位于 `services/filter_engine.py`:

1. 从上级 PnL 的 `entry_markers` 中分离 long/short 的入场 bar 索引
2. 从上级 PnL 的 `exit_markers` 中分离 long/short 的出场 bar 索引
3. 对每个入场，找到下一个同类型出场，标记区间 `[entry, exit]` 为 `True`
4. 返回 `(long_mask, short_mask)` — 各为 `np.ndarray[bool]`，长度 = `n_bars`

```python
# 示例: 上级在第 10 根 bar 做多入场，第 20 根出场
# → long_mask[10:21] = True
# 本级做多交易 entry_idx=12 → 落在 mask 内 → 标 BS
# 本级做多交易 entry_idx=25 → 不在 mask 内 → 跳过
```

## 10. 辅助工具

### `_find_date_index(dates, target_date)`

在 `dates` 中查找第一个 `>= target_date` 的 bar 索引。做了时区标准化处理（兼容 tz-aware 分钟线与 tz-naive 日线/周线/月线的混合比较）。当前 v5 中未被 `compute_bs_markers` 调用，但保留作为工具函数供未来使用。

### `get_lower_tfs(operating_tf)`

遍历 `TF_LOWER` 映射链，返回操作周期以下所有周期，用于 `_bs_lower_tfs` 计算:

```
季线 → 月线 → 周线 → 日线 → 60分钟 → 15分钟 → 5分钟 → 1分钟 → None
```

## 11. 边界情况

| 场景 | 行为 |
|------|------|
| 操作周期无更高周期上级 | `higher_pnl=None` → `_align_masks=None` → 回退 `_compute_own_from_trades`（不过滤） |
| 上级无策略交易 | `entry_markers/exit_markers` 为空 → holding_masks 全 False → 无 BS 标记 |
| 本级策略未启用 | `trade_records` 为空 → 返回空 |
| 单笔交易部分在 mask 外 | entry_idx 在 mask 内则整笔标（含出场），不在则整笔跳过 |
| entry_idx 超出 mask 长度 | 安全跳过（`entry_idx >= len(mask)` 检查） |
| 不同 TF 数据时间窗不重叠 | `_align_pnl_to_current_tf` 处理对齐 |

## 12. v4 → v5 变更摘要

| 项目 | v4 | v5 |
|------|----|----|
| 级联机制 | `_compute_cascade` — 上级 BS → 下级 all_pairs 同向确认 | **已删除**，无级联 |
| 分支逻辑 | `tf == operating_tf` 分叉，不同周期不同路径 | **统一逻辑**，所有周期同一路径 |
| all_pairs 回退 | 无 trade_records 时从 all_pairs 推断 | **已删除**，无此路径 |
| 数据源 | BS 从上级 BS 级联 + 本级确认 | BS 从本级 trade_records + 上级 holding_masks 独立计算 |
| `schmitt/all_pairs` 参数 | 被 `_compute_cascade` 和 `_compute_from_pairs` 使用 | 保留但未使用 |
| `higher_bs` 参数 | 级联的上级 BS 来源 | 保留但未使用 |
| `_find_date_index` | 级联时定位日期索引 | 保留但未使用 |
