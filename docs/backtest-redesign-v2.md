# 回测框架重构方案 v2

> 日期: 2026-07-04 | 分支: feat/backtest-minimal

## 1. 当前实现问题

### 1.1 数据流现状

```
                    ┌───────────── 浏览模式 ─────────────┐
                    │   DB ──查all──→ parquet (全部历史)     │
                    │   Streamlit 加载后 df.tail(N) 截断    │
                    │                                      │
DB ──数据获取──→ 两条路径                                  │
                    │                                      │
                    └───────────── 回测模式 ─────────────┘
                        DB ──查all──→ parquet (全部历史)
                        Streamlit 加载后用 cutoff_date 截断
                        截断后数据量随 slider 变化
```

核心矛盾：两套模式从 DB 查相同数据，但在 Streamlit 层做不同的后处理，
回测时显示的数据条数随 slider 位置变化，用户无法直观对比。

### 1.2 8个已知问题

| # | 问题 | 严重程度 |
|---|------|----------|
| 1 | 回测和浏览显示条数不一致，回测数据量随 slider 变化 | 高 |
| 2 | 没有统一的 min_tf 元信息缓存，每次加载需查 DB | 中 |
| 3 | 拖块（slider）语义不清晰，基于 bar_index 截断而非真正控制数据窗口 | 高 |
| 4 | 切换 ticker 时回测状态未重置 | 中 |
| 5 | 回测模式下 day_offset 控件仍可见可操作 | 低 |
| 6 | 高周期（60分钟/日线）无数据时缺少优雅降级 | 低 |
| 7 | _load_chart_data 中 force_full 参数引入了不必要的分支 | 中 |
| 8 | parquet 数据量不稳定，多次写入时全量覆盖无缓存策略 | 中 |

## 2. 目标架构

### 2.1 核心原则

- 数据获取完全统一：浏览和回测走相同的 DB → parquet → load 管道
- 回测只改变窗口位置，不改变数据量
- 配置文件缓存 min_tf 元信息

### 2.2 统一数据流

```
DB ──查N条(窗口位置=X)──→ parquet (始终N条)
                              │
                   ┌──────────┴──────────┐
                   │                     │
              浏览模式                回测模式
              X = 最新               X = slider位置
              df.tail(N)             df (DB已截好的N条)
```

## 3. Phase 1 宏观修复（本次）

### 3.1 回测显示相同个数

修改 `_load_chart_data`:
- 删除 `force_full` 参数，统一从 DB 查 N 条写入 parquet
- 回测模式: 根据 `cutoff_date` 确定窗口结束位置，从 DB 查 N 条
- 浏览模式: 取最新 N 条

### 3.2 配置文件缓存 min_tf 范围

新增 `data/backtest_config.json`:

```json
{
  "ticker": "3690.HK",
  "min_tf": "15分钟",
  "bar_count": 1414,
  "first_date": "2025-01-02 09:30:00",
  "last_date": "2026-07-04 16:00:00",
  "cached_at": "2026-07-04T12:00:00"
}
```

### 3.3 拖块控制 N 条窗口

回测 slider 不再基于 `bar_index` 做日期截断，而是控制窗口的起始位置:
- slider 范围: `0 ~ (total_bars - n_pts)`
- slider 位置 `X` → 从 DB 查第 `X` 到 `X + n_pts - 1` 条，写入 parquet
- 图形显示的就是这 N 条数据

## 4. Phase 2 计划（后续）

- ticker 切换时重置回测状态
- 回测模式下隐藏 `day_offset` 控件
- 高周期无数据时的优雅降级

## 5. 文件变更清单

| 文件 | 修改 | 行数 |
|------|------|------|
| `streamlit_app.py` | `_load_chart_data` 统一 | ~30 |
| `streamlit_app.py` | `_render_backtest_mode` slider逻辑 | ~15 |
| `services/data_loader.py` | `_sync_to_display` 删除force_full，统一查N条 | ~10 |
| `data/backtest_config.json` | 新建配置文件 | 新建 |
| `state.py` | 可选：增加配置相关key | ~5 |
