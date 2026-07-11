# 高周期持仓状态色块（替代高周期 PnL 参考）· 设计

> 目标：把「高一周期 PnL 参考」子图（`show_cross_pnl`）从**做多/做空 PnL 曲线 + 入离场标记**
> 换成**高一周期持仓状态色块**——绿=高周期做多持仓、红=高周期做空持仓、空白=不持。
> 与当前周期「实际持仓状态」面板样式一致。

## 1. 变更前后

| | 变更前（PnL 参考） | 变更后（持仓状态色块） |
|---|---|---|
| 子图内容 | 高周期 `aligned_long`/`aligned_short` PnL 虚线 + ▲入场/▼离场金色标记 + 盈亏标注 | 双轨色块：绿=高周期做多持仓、红=高周期做空持仓、空白=不持 |
| y 轴 | 百分比 `{tf}(%)` | 类别轴：`做多`/`做空`，无百分比 |
| 子图标题 | `{tf}PnL参考` | `{tf}持仓状态` |
| 开关 | `show_cross_pnl`（不变） | `show_cross_pnl`（不变，现在显示色块） |

## 2. 数据来源（全部复用，无新算法）

```
_align_pnl_to_current_tf(...)  →  aligned（含 entry_markers / exit_markers，已对齐到当前周期 bar）
        │
        ▼  _compute_holding_masks(len(t), entry_markers, exit_markers)   ← 现有函数，直接复用
(long_mask, short_mask)  高周期做多/做空持仓区间 bool 掩码
        │
        ▼  _draw_holding_bands(fig, t, long_mask, short_mask, row)       ← 新增共享助手
双轨色块
```

- `_compute_holding_masks` 已存在（`filter_engine.py`），原本仅供「同向性判断」子图使用；本次在高周期色块路径也复用。
- 高周期色块与「同向性判断」子图（`show_alignment`）**互不影响**，后者不改。

## 3. 渲染（与当前周期面板一致）

- 双轨：上轨绿=做多持仓、下轨红=做空持仓；空白=不持。
- 复用共享助手 `_draw_holding_bands`，当前周期「实际持仓状态」面板与本高周期面板**共用同一绘制逻辑**（只是掩码来源不同）。

## 4. 改动清单

| 文件 | 改动 |
|---|---|
| `components/charts.py` | 新增 `_contiguous_runs` + `_draw_holding_bands`（共享绘制）；**重写** `_add_cross_pnl_subplot`（签名不变）为色块：`_compute_holding_masks(aligned)` → `_draw_holding_bands` |
| `streamlit_app.py` | `_add_feedback_subplot` 改为复用 `_draw_holding_bands`（删本地 `_contiguous_runs`，从 charts 导入）；`_determine_subplot_layout` 标题 `{tf}PnL参考`→`{tf}持仓状态`；调用点去掉 `ticksuffix="%"` 的 y 轴设置 |
| `tests/test_charts.py` | SECTION 9/13 由 PnL 曲线/标记断言改为色块（shapes）断言 |
| `tests/test_feedback_subplot.py` | `_contiguous_runs` 改从 charts 导入；补 `_draw_holding_bands` 测试 |

## 5. 不改动

- `_align_pnl_to_current_tf` / `_compute_holding_masks`（复用）。
- `show_cross_pnl` 开关语义（仍是"显示高周期参考"，只是形态换成色块）。
- 「同向性判断」子图（`show_alignment`）与「实际持仓状态」面板（当前周期）逻辑。

## 6. 边界与决策

- **无高周期交易** → 掩码全 False → 无色块（空子图，不报错）。
- **单/双轨决策**：本版采用**双轨**（做多/做空各一轨），与当前周期「实际持仓状态」面板一致。若需单轨「方向 regime 带」（一条轨按方向着色），改 `_draw_holding_bands` 即可。
- `_compute_holding_masks` 的 entry→exit 配对沿用现有实现（每个 entry 配下一个同向 exit，无 exit 则持有到末 bar）。

## 7. 优化记录

- **子图高度**：高周期状态子图（`cross_row`）为状态条，高度缩到约原来的 1/3（`_determine_subplot_layout` 的 `rh`：0.18→0.06、0.15→0.05），腾出的高度给主价格图。
- **eod 持仓延续到最右**（半边多空对）：高周期最后一个仓位若尚未真正结束，会被 Layer0 在高周期末 bar 以 `exit_reason="eod"` 强制平仓；对齐到低周期时该 eod 离场的时间戳落在偏左位置，导致色块延续不到最新 bar。修复：`_align_pnl_to_current_tf` 中 `exit_reason=="eod"` 时把 `exit_bar` 设为当前周期最后一根 bar，使高周期"仍在持仓"的色块延续到右边缘。
- **窗口前开仓延续到起始点**（eod 右延续的左镜像）：高周期仓位若在当前窗口**起点之前**就开仓，入场时间戳早于窗口第一根 bar → 原逻辑映射为空、不生成入场 marker → 色块左边缘缺失。修复：入场落在窗口起点之前、但仓位延续进窗口（`exit_time ≥ cd[0]`）时，把 `entry_bar` 设为 `0`（当前周期起始点）。配合 eod 右延续，"窗口前开仓 + 未结束"的仓位整窗贯穿。整段都在窗口之前的仓位仍不显示。

---

> 变更历史：v1 = 高周期 PnL 参考 → 高周期持仓状态色块，双轨，复用现有掩码。
> v1.1 = 子图高度缩至 ~1/3；eod（未结束）高周期仓位色块延续到最右边缘。
> v1.2 = 窗口前开仓的高周期仓位从当前周期起始点(bar0)开始显示（左镜像）。
