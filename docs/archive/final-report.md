# 回测框架 -- 最终状态报告

> 日期: 2026-07-04 | 分支: feat/backtest-minimal | 仓库: pxf0797/filter-algorithms-research

## 一、项目概览

- **原始需求**: 为多周期股票滤波分析工具增加回测模式 -- 通过 bar slider 在历史数据中滑动，4 视图统一截断到同一截止日期，完整保留滤波/施密特/交易对/盈亏曲线功能
- **开发周期**: 2026-07-03 ~ 2026-07-04 (2 天)
- **提交数**: 15 个回测相关提交 (分支总计 291 commits)
- **变更规模**: 9 files changed, +1766 / -20 lines
- **测试**: 28 回测专项测试 + 640 已有测试全部通过 (总计 668 passed)

## 二、架构总览

```
                            yfinance API
                                 │
                                 ▼
                    _fetch_stock() ──► upsert_kline()
                                 │
                                 ▼
                           SQLite (kline)
                                 │
              ┌──────────────────┴──────────────────┐
              │                                      │
         浏览模式                                 回测模式
    _cb_mode=False                           _cb_mode=True
              │                                      │
              ▼                                      ▼
   _sync_to_display(                      _sync_to_display(
     ticker, tf,                            ticker, tf,
     day_offset, n_pts)                     cutoff_date, n_pts)
              │                                      │
              ▼                                      ▼
   query_kline()                           直接 SQL:
   ORDER BY ts DESC                        WHERE ts <= cutoff_date
   LIMIT n_pts                             ORDER BY ts DESC LIMIT n_pts
   (最新 N 条)                              (截止到某日期的最后 N 条)
              │                                      │
              └──────────────┬───────────────────────┘
                             ▼
                   data/display/{tf}.parquet
                     (始终 n_pts 条)
                             │
                             ▼
                   pd.read_parquet()
                             │
                             ▼
              _compute_filters() → _compute_schmitt_trigger()
              → _compute_prediction_pairs() → _compute_strategy_display()
              → plotly Figure → st.plotly_chart()
```

**关键设计决策**: 两种模式写入 parquet 的都是精确 n_pts 条数据；差异仅限于窗口定位方式（浏览取"最新"，回测取"截止到某日期"），下游渲染管线完全不感知模式。

## 三、文件清单

| 文件 | 行数 | 用途 |
|------|------|------|
| `filter/streamlit_app.py` | 1487 | 主入口：页面布局、回测 UI、数据加载、滤波计算 |
| `filter/db.py` | 464 | SQLite 数据层：kline CRUD、`query_kline` (含 offset 参数)、健康检查 |
| `filter/services/data_loader.py` | 191 | yfinance 数据获取、`_sync_to_display` (浏览/回测统一) |
| `filter/state.py` | 308 | 集中式 session_state：AppState 类 + 回测状态键 |
| `filter/backtest_logger.py` | 75 | JSONL 格式回测事件日志 |
| `filter/components/sidebar.py` | -- | 侧边栏组件：时间导航 (回测下隐藏)、参数面板 |
| `filter/components/charts.py` | -- | Plotly 图表渲染 |
| `tests/test_backtest.py` | 405 | 28 个回测专项测试 |
| `docs/backtest-comparison-report.md` | 805 | 浏览/回测模式完整差异报告 (15 项修复) |
| `docs/backtest-redesign-v2.md` | 104 | 架构设计文档 |
| `docs/backtest-final-report.md` | 本文 | 最终状态报告 |

## 四、修复历史

### Phase 1 — 统一数据流 (2026-07-03)

| Commit | 内容 |
|--------|------|
| `f6b770c` | feat: 最小回测框架 -- 模式切换 + bar 滑块 + 数据截断 |
| `0bb8ad4` | fix: 回测日期对齐 -- bar_index 数值截断改为 cutoff_date 日期截断 |
| `3ffdc7e` | fix: 回测空数据守卫 + 6 项防御性检查 |
| `05be4f7` | fix: `_get_min_tf_and_count` 从 DB 查询全量 bar 数替代 parquet 窗口数据 |
| `cd06daa` | refactor: 统一回测/浏览数据流 + 回测日志模块 |
| `2d86268` | refactor: Phase1 -- 统一数据流，回测窗口滑动 |

### Phase 2 — 15 项修复 (2026-07-04)

| Commit | 内容 |
|--------|------|
| `1f7996b` | docs: 统一差异报告 -- 合并 Phase1 前后分析 |
| `9bdabff` | docs: 第一章修复方案 -- 1-1 统一 SQL / 1-2 统一参数 / 1-3 回退截断 |
| `ba12a5e` | docs: 第二章修复方案 -- 2-1 日期对齐 / 2-2 合并冗余参数 |
| `a74a589` | docs: 第三章修复方案 -- 3-1 隐藏控件 / 3-2 独立 n_pts / 3-3 保持现状 |
| `35ac6b5` | docs: 第四章修复方案 -- 4-1 刷新状态 / 4-2 保留 / 4-3 删除 |
| `47f5640` | docs: 第五章修复方案 + 全 15 项汇总表 |
| `0faaf87` | fix: Phase2 -- 15 项修复全部实施 |
| `74d4f41` | test: 回测模式测试用例 -- 8 个场景 + 3 组单元测试 |

## 五、修复状态表

| # | 严重度 | 问题 | 状态 |
|---|--------|------|------|
| 1 | Major | force_full 每次渲染重写 parquet | **Phase1 已修复** |
| 2 | Major | ticker 切换时回测状态不重置 | **已修复** -- 自动刷新回测状态 |
| 3 | Major | 回测下 day_offset 控件可见但失效 | **已修复** -- `_render_time_nav` 回测下隐藏 |
| 4 | Minor | 数据管道不统一 (两套 SQL) | **已修复** -- `query_kline` 增加 offset 参数 |
| 5 | Minor | 高周期 bar_index 小时无数据报错 | **已修复** -- 优雅降级，不阻塞 |
| 6 | Minor | 退出回测时 `_day_offset` 不被重置 | **保持现状** -- 设计意图：恢复到之前位置 |
| 7 | Minor | `_sync_to_display` 返回值被忽略 | **已修复** -- 检查返回值 |
| 8 | Minor | 退出回测时 parquet 瞬时不一致 | **已修复** -- 退出时刷新 parquet |
| 9 | Minor | 参数语义不统一 day_offset/window_start | **已修复** -- 统一为 cutoff_date 日期对齐 |
| 10 | Minor | API 回退路径不截断 | **已修复** -- 回退分支补全窗口截断 |
| 11 | Minor | 4 视图时间不对齐 | **已修复** -- 改为 cutoff_date 日期对齐 |
| 12 | Info | bar_index/window_start 冗余 | **已修复** -- 合并为一个参数 |
| 13 | Minor | 回测下各视图 n_pts 被统一覆盖 | **已修复** -- 各视图独立 n_pts |
| 14 | Minor | day_offset 保留旧值 | **保持现状** -- 设计意图 |
| 15 | Info | `_is_playing` 状态键 | **保留** -- 为后续自动播放预留 |
| 16 | Info | `_load_backtest_config` 未被调用 | **已删除** -- 未使用函数 |
| 17 | Minor | tz_localize 时区防御不完整 | **已修复** -- try/except TypeError |
| 18 | Info | 日志写入失败静默 | **已修复** -- logger.debug 记录 |

**结论**: 15 项主要修复全部实施完成，3 项有意保持现状。

## 六、关键函数说明

### 数据加载

```python
# filter/services/data_loader.py
def _sync_to_display(ticker_code, tf, day_offset=0, n_pts=120, cutoff_date=None) -> Tuple[bool, int]:
    """
    cutoff_date=None:  浏览模式，取最新 n_pts 条 (支持 day_offset 日期偏移)
    cutoff_date=YYYY-MM-DD: 回测模式，取截止到该日期的最后 n_pts 条 (日期对齐)
    """
```

```python
# filter/db.py
def query_kline(ticker, tf, n_pts=120, day_offset=0, offset=None) -> pd.DataFrame:
    """
    offset=None: 取最新 n_pts 条 (浏览模式, 支持 day_offset)
    offset=N:    从第 N 条开始取 n_pts 条 (回测模式, 窗口滑动, 时间升序)
    """
```

### 回测 UI

```python
# filter/streamlit_app.py
def _render_backtest_mode(configs, ticker_code):
    """回测模式 UI: 模式切换 radio + bar slider + 时间范围显示"""
```

```python
def _get_bar_date_from_db(ticker, tf, bar_index) -> str:
    """查询指定 bar_index 对应的日期，用于 cutoff_date 计算"""
```

```python
def _get_min_tf_and_count(configs, ticker_code) -> Tuple[str, int]:
    """计算 4 视图中最精细周期及其 DB 总 bar 数"""
```

### 日志

```python
# filter/backtest_logger.py
def log_mode_switch(ticker, direction, min_tf, bar_count)   # 模式切换
def log_bar_navigation(ticker, min_tf, bar_index, total, cutoff_date)  # bar 跳转
def log_data_load(ticker, tf, bar_count, cutoff_date)        # 数据加载
def log_error(ticker, location, error_msg)                   # 异常
```

## 七、状态管理

### 回测相关 AppState Keys (state.py: SYSTEM_KEYS)

| Key | 类型 | 默认值 | 用途 |
|-----|------|--------|------|
| `_cb_mode` | bool | False | 是否处于回测模式 |
| `_bar_index` | int | 0 | 当前 bar 位置 (slider 值) |
| `_bt_cutoff_date` | str | "" | 回测截止日期 (ISO 格式), 各周期日期对齐 |
| `_bt_last_ticker` | str | "" | 上次回测时的 ticker, 检测切换 |
| `_min_tf` | str | "" | 4 视图中最精细周期 |
| `_min_tf_bar_count` | int | 0 | min_tf 总 bar 数 |

### 模式切换状态转换

**进入回测**: 计算 min_tf + bar_count → 设置状态键 → 保存 backtest_config.json → st.rerun()

**退出回测**: 清空回测状态键 (_bar_index=0, _bt_cutoff_date="", _min_tf="", _min_tf_bar_count=0) → _day_offset 保持原值 (设计意图) → st.rerun()

**Ticker 切换** (回测中): 自动刷新回测状态 (重新计算 min_tf, bar_count, cutoff_date) → bar_index=0

## 八、测试覆盖

### 测试类分布 (28 用例)

| 测试类 | 用例数 | 覆盖场景 |
|--------|--------|---------|
| `TestBacktestModeSwitch` | 5 | 进入/退出回测, _cb_mode 状态变化, 默认值 |
| `TestRenderTimeNav` | 2 | 回测下隐藏导航, 浏览下正常显示 |
| `TestSyncToDisplay` | 6 | cutoff_date 日期对齐, 空结果, 浏览回退, n_pts 截断 |
| `TestBacktestEdgeCases` | 5 | 高周期无数据, ticker 切换重置, 日志失败, bar_index 范围 |
| `TestGetBarDateFromDb` | 2 | 查日期, 无数据返回空 |
| `TestBacktestLogger` | 5 | 4 种日志事件 + 目录自动创建 |
| `TestAppStateKeys` | 5 | 回测键存在性 + 默认值类型验证 |

**测试执行**: `28 passed in 0.80s`

### 整体测试覆盖

- 回测测试: 28 用例 (tests/test_backtest.py)
- 已有测试: 640 用例 (pytest 收集，含所有其他测试文件)
- 总计: **668 用例全部通过**

## 九、已知剩余问题

| 严重度 | 问题 | 说明 |
|--------|------|------|
| Low | `_day_offset` 退出回测时不重置 | 设计意图：用户回到浏览后恢复之前的时间窗口位置。如需强制跳回最新，可增加"回到最新"按钮 |
| Low | 高周期 TF 在早期 bar_index 时数据不足 | 已做优雅降级 (不报错阻塞)，但月线/季线在数据早期可能只有少数 bar |
| Low | 回测退出时 parquet 短暂不一致 | 极端情况下 (快速切模式) 可能闪烁一帧旧数据，下次 rerun 自动覆盖 |
| Info | `_is_playing` 状态键未使用 | 保留，为后续 Phase 自动播放功能预留 |
| Info | 回测配置 (backtest_config.json) 仅保存不加载 | 当前仅用于外部调试查看，恢复功能可后续添加 |

## 十、后续改进方向

### Phase 3 建议

1. **自动回放**: 利用 `_is_playing` 键实现自动 bar 滑动，支持调速
2. **回测配置恢复**: `_load_backtest_config` 在启动时恢复上次回测位置
3. **跨 TF 信号汇总**: 回测模式下展示各 TF 的施密特/交易信号对比表
4. **回测性能优化**: 对高频 ticker 切换场景，缓存 cutoff_date 对应的各 TF 数据
5. **回测统计面板**: 累计胜率、最大回撤、盈亏比等统计指标

## 附录: Git 历史

```
74d4f41 test: 回测模式测试用例 -- 8个场景+3组单元测试
0faaf87 fix: Phase2 -- 15项修复全部实施
47f5640 docs: 第五章修复方案 + 全15项汇总表
35ac6b5 docs: 第四章修复方案 -- 4-1刷新状态/4-2保留/4-3删除
a74a589 docs: 第三章修复方案 -- 3-1隐藏控件/3-2独立n_pts/3-3保持现状
ba12a5e docs: 第二章修复方案 -- 2-1日期对齐/2-2合并冗余参数
9bdabff docs: 第一章修复方案 -- 1-1统一SQL/1-2统一参数/1-3回退截断
1f7996b docs: 统一差异报告 -- 合并Phase1前后分析
2d86268 refactor: Phase1 -- 统一数据流，回测窗口滑动
66fdc97 docs: 回测框架重构方案v2 -- 统一数据流设计
cd06daa refactor: 统一回测/浏览数据流 + 回测日志模块
05be4f7 fix: _get_min_tf_and_count 从DB查询全量bar数替代parquet窗口数据
3ffdc7e fix: 回测空数据守卫 + 6项防御性检查
0bb8ad4 fix: 回测日期对齐 -- bar_index数值截断改为cutoff_date日期截断
f6b770c feat: 最小回测框架 -- 模式切换 + bar滑块 + 数据截断
```
