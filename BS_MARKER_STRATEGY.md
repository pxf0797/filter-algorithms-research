# BS 仓位操作标识 — 完整设计方案 v4

## 1. 核心定义

**BS 标记 = 策略交易记录（PnL）经过同向性判断（holding masks）过滤后的结果。**

- BS 的数据源是 `trade_records`（Row 6/7 的策略交易，含入场点=信号确认点 `pair_end`）
- BS 的过滤器是 `_compute_holding_masks` 的输出（Row 8 同向性判断子图的数据）
- 只有落在 holding mask 区间内的交易才标 BS；不在区间内的不标

## 2. 标记规则

| 方向 | 入场 | 出场 | 颜色 |
|------|------|------|------|
| 做多 | 🟢 B | 🟢 S | 绿色 |
| 做空 | 🔴 S | 🔴 B | 红色 |

做多：先 B 后 S（全绿）。做空：先 S 后 B（全红）。

入场标在 K 线 low 下方，出场标在 high 上方。

## 3. 各子图数据源（以日线操作周期为例）

| Row | 子图 | 数据来源 | BS 使用？ |
|-----|------|----------|----------|
| 1 | 价格&滤波 | K线 OHLC + 滤波输出 | 显示位置 |
| 2-4 | 残差/v/a±ε | 滤波梯度/加速度/Schmitt阈值 | 否 |
| 5 | Sig_t | Schmitt 信号(-1/0/+1) | 否 |
| 6 | PnL收益(%) | trade_records（策略交易） | **是 — BS 的标的** |
| 7 | 实际持仓 | trade_records → 多/空/不持 | 否 |
| 8 | 同向性判断 | Row6 被周线 holding_masks 过滤 | **是 — BS 的过滤器** |

## 4. 操作周期（日线）

### 4.1 数据流

```
日线 trade_records (Row 6 数据，入场=entry_idx)
       ↓
周线 trade_records → _align_pnl_to_current_tf → _compute_holding_masks
       ↓                                                    ↓
  周线 holding_masks (long_mask / short_mask)  ← 同向性判断过滤器
       ↓
日线 trade_records ──→ 过滤：entry_idx 必须落在对应方向 mask 内
       ↓
  ├── 在 mask 内 → 标 BS（入场=entry_idx, 出场=exit_idx）
  └── 不在 mask 内 → 不标 BS
```

### 4.2 入场

trade_records 中每笔交易的 `entry_idx`：

- 检查 `entry_idx` 是否在对应方向的 mask 内（long trade → `long_mask`, short trade → `short_mask`）
- 在 → 标入场 BS
- 不在 → 跳过

### 4.3 出场

trade_records 中每笔交易的 `exit_idx` + `exit_reason`：

- 正常出场（`take_profit` / `pair_end`）：标在 `exit_idx`
- 偏离退出（`stop_loss`）：标在 `exit_idx`，同时级联到低一级立即标

## 5. 低一级周期（60分钟）

### 5.1 入场级联

```
日线 BS 入场标记（如 bar=81 🟢B，做多）
  ↓ 日线 bar=81 的日期 → 在60分钟定位 start_bar
  ↓ 遍历60分钟 all_pairs
  ↓ 找第一个: pair_start ≥ start_bar 且 sig[pair_end] == 1（同向）
  ↓ 找到 → 在60分钟 pair_start 标 🟢B
  ↓ 找不到 → 不标
```

### 5.2 正常出场级联

```
日线 BS 出场标记 → 找60分钟对应同向 pair 的 pair_end → 标出场 BS
```

### 5.3 异常出场级联（stop_loss）

```
日线 stop_loss 出场 → 不等60分钟确认 → 立即在对应时间标出场 BS
```

## 6. 再低一级（15分钟、5分钟…）

同理：上一级 BS → 本级 all_pairs 同向确认 → 标 BS。

## 7. 系统已有数据（无需重复计算）

操作周期渲染时，以下数据**已经在 `_render_chart` 中计算好了**：

- `trade_records`：来自 `_compute_strategy_display`（Step 8）
- `higher_pnl`：来自 `_align_pnl_to_current_tf`（Step 3），含 `entry_markers` / `exit_markers`
- `_align_masks`：来自 `_compute_holding_masks`（用 `higher_pnl` 算得），含 `long_mask` / `short_mask`

**BS 标记只需要把 `trade_records` 和 `_align_masks` 结合，不做任何额外计算。**

## 8. compute_bs_markers 接口

```python
def compute_bs_markers(t, dates, schmitt, all_pairs, trade_records,
                        tf, operating_tf, higher_bs=None,
                        holding_masks=None):  # (long_mask, short_mask) or None
    if tf == operating_tf:
        if holding_masks is not None and trade_records:
            return _compute_from_trades_filtered(t, dates, trade_records, holding_masks)
        elif trade_records:
            return _compute_from_trades(t, dates, trade_records)  # 无高一级参考，回退
        else:
            return _compute_from_pairs(t, dates, schmitt, all_pairs)  # 无策略，最后回退
    elif higher_bs:
        return _compute_cascade(t, dates, schmitt, all_pairs, trade_records, higher_bs)
    else:
        return empty
```

### 8.1 `_compute_from_trades_filtered`

```python
def _compute_from_trades_filtered(t, dates, trade_records, holding_masks):
    """
    遍历 trade_records，用 holding_masks 过滤。

    Parameters
    ----------
    t : np.ndarray
        时间戳数组
    dates : list
        日期列表
    trade_records : list[dict]
        策略交易记录，每条含 entry_idx, exit_idx, direction, exit_reason 等
    holding_masks : tuple
        (long_mask, short_mask)，各为 np.ndarray[bool]，长度等于 len(t)

    Returns
    -------
    bs_markers : list[dict]
        每个元素: {idx, label, color, is_entry, direction, from_cascade}
    """
    long_mask, short_mask = holding_masks
    markers = []

    for tr in trade_records:
        direction = tr['direction']  # 'long' or 'short'
        entry_idx = tr['entry_idx']
        exit_idx = tr['exit_idx']
        exit_reason = tr.get('exit_reason', 'take_profit')

        mask = long_mask if direction == 'long' else short_mask

        # 入场：entry_idx 必须在 mask 内
        if mask[entry_idx]:
            entry_label = 'B' if direction == 'long' else 'S'
            markers.append({
                'idx': entry_idx,
                'label': entry_label,
                'color': 'green' if direction == 'long' else 'red',
                'is_entry': True,
                'direction': direction,
                'from_cascade': False,
            })

            # 出场：exit_idx 必须在 mask 内
            if mask[exit_idx]:
                exit_label = 'S' if direction == 'long' else 'B'
                markers.append({
                    'idx': exit_idx,
                    'label': exit_label,
                    'color': 'green' if direction == 'long' else 'red',
                    'is_entry': False,
                    'direction': direction,
                    'from_cascade': False,
                })

    return markers
```

### 8.2 `_compute_cascade`

已有实现，保持不变。负责将上级 BS 标记级联到当前 TF：

- 遍历 `higher_bs` 中的每个 BS 标记
- 用 `_find_date_index` 定位当前 TF 的起始 bar
- 在 `all_pairs` 中找同向 pair（`sig[pair_end] == direction`）
- 找到后标在当前 TF 对应 bar
- `stop_loss` 出场不检查同向，直接级联

### 8.3 `_compute_from_trades`（回退）

当无高一级参考（`holding_masks=None`）但有 `trade_records` 时使用，不过滤直接标 BS。

### 8.4 `_compute_from_pairs`（最后回退）

当策略未启用（`trade_records` 为空）时使用，从 `all_pairs` 推断 BS。

## 9. streamlit_app.py 集成

在 `_render_chart` 的 BS markers 段：

```python
# _align_masks 已提前计算（从 higher_pnl via _compute_holding_masks）
_holding = _align_masks if (tf == _op_tf and _align_masks is not None) else None
bs_markers = compute_bs_markers(
    t, dates, schmitt, all_pairs, trade_records,
    tf, _op_tf, higher_bs=_higher_bs,
    holding_masks=_holding,
)
```

**仅此一处改动。** `_align_masks`、`trade_records`、`higher_pnl` 都是系统已有的。

## 10. 边界情况

| 场景 | 行为 |
|------|------|
| 操作周期无高一级 | `holding_masks=None` → 回退 `trade_records` 不过滤 |
| 高一级无策略交易 | holding_masks 全 False → 无 BS 标记 |
| 策略未启用 | `trade_records` 为空 → 回退 `all_pairs` |
| 级联无同向 pair | 该 entry 不级联 |
| `stop_loss` 退出 | 不等同向，立即级联 |
| 不同 TF 数据时间窗不重叠 | `_find_date_index` 处理（tz 已修复） |

## 11. 实现要点

1. **`_compute_from_trades_filtered`**：遍历 `trade_records`，对每笔交易检查其 `entry_idx` / `exit_idx` 是否在对应方向的 mask 内（`mask[entry_idx] == True`）
2. **`_compute_cascade`**：已有，保持不变
3. **`_find_date_index`**：已有 tz 修复，保持不变
4. **不新增系统调用**，全部复用已有数据
