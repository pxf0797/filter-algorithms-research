# filter_research 领域驱动重构方案

> 基于四维交叉分析生成 | 2026-07-24

---

## 1. 现状诊断

四维分析（浏览模式 / 回测模式 / 数据管线 / 分析模式）揭示的核心发现：

**TOP 5 最严重问题：**

| # | 问题 | 严重度 | 影响范围 |
|---|------|--------|---------|
| 1 | **Web/CLI 管线计算代码复用度为 0%** — `streamlit_app.py` 和 `backtest_core.py` 各自独立实现了完全相同的 `_compute_filters`、`_compute_schmitt_trigger`、`_compute_prediction_pairs` 等函数，约 200 行重复逻辑。修一个 bug 需要改两处。 | 致命 | 浏览 + 回测 |
| 2 | **`data_loader.py` 上帝类** — 1149 行承担 9 种职责（API拉取、数据清洗、SQLite读写、Parquet缓存、版本管理、级联K线合成、股票查询、模块缓存、增量策略），从 API 到底层存储全部耦合在一个文件。 | 致命 | 全部域 |
| 3 | **`BacktestRunner.run()` 违反单一职责** — 250 行方法包含边界校验、数据同步、逐bar循环、并行/顺序分支（约 50 行重复）、EWMA状态管理、PnL对齐、BS计算、断点保存等 9 项职责。 | 严重 | 回测 |
| 4 | **回测代码侵入浏览模式** — `main()` 中约 25 行回测专用逻辑（占 main 函数体的 11%），包括 `run_backtest_play()`、`PipelineCapture`、`_sync_all_cascading` 等，浏览模式下全部为空操作/None 分支。 | 严重 | 浏览 |
| 5 | **`backtest_metrics.py` 和 `backtest_catalog.py` 是孤儿模块** — 两个模块已完成开发并有测试覆盖，但零应用代码调用。CLI 回测结束后不输出任何指标，`backtest_output/` 目录下无任何 `.catalog.json` 文件。 | 中 | 分析 |

**量化总结：**
- 6 次格式转换（yfinance → DataFrame → SQLite → DataFrame → Parquet → DataFrame → NumPy）
- 3 处数据双写（SQLite + Parquet；Parquet + CSV；两份 metadata.json）
- 3 个死函数 + 6 个未使用导入 + 1 个代码 bug
- 2 个独立工具目录（`web_tool/`、`tools/`）功能与主应用重叠但零耦合

---

## 2. 四大功能域现状

### 2.1 浏览模式

**涉及文件：**

| 文件 | 行数 | 角色 | 必需性 |
|------|------|------|--------|
| `streamlit_app.py` | 840 | 入口、编排、cached计算、图表组装 | **核心** |
| `sidebar_sections.py` | 624 | 侧边栏所有 UI 控件（18个函数） | **核心** |
| `chart_builder.py` | 316 | trace/shape/annotation 构建 | **核心** |
| `components/charts.py` | 371 | Plotly HTML 渲染、预测曲线、跨周期子图 | **核心** |
| `components/sidebar.py` | 317 | 单视图参数面板 `_render_params()` | **核心** |
| `state.py` | 348 | AppState + ViewState | **核心** |
| `config_db.py` | 676 | 预设方案 CRUD | **核心** |
| `db.py` | 670 | SQLite 操作、数据健康检查 | **核心** |
| `constants.py` | 57 | 全局常量（叶子节点） | **核心** |
| `services/data_loader.py` | 1149 | yfinance 拉取、DB 写入、Parquet 缓存 | **核心** |
| `services/filter_engine.py` | 1190+ | 滤波算法、Schmitt 触发、PnL 计算 | **核心** |
| `components/backtest_panel.py` | 504 | 回测模式控件、自动播放 | 非必需但始终渲染 |
| `services/backtest_core.py` | 1144 | 回测管道 | 非必需但被导入 |
| `backtest_logger.py` | 76 | 回测日志 | 非必需但被导入 |

**核心链路：**

```
main() [streamlit_app.py:609]
│
├─► 初始化层
│   ├─ run_backtest_play()           ← ★ 回测侵入：无条件调用（浏览模式返回 False）
│   ├─ _get_db_connection()          → init_db() → SQLite
│   ├─ init_config_tables()          → config_db
│   ├─ AppState.init_defaults()      → state.py
│   └─ import_json_files_as_presets()
│
├─► 侧边栏渲染 (sidebar_sections.py)
│   ├─ market radio + ticker 输入
│   ├─ _handle_initial_fetch()       → _fetch_all_timeframes() → yfinance → DB
│   ├─ _render_refresh_row()         → st.cache_data.clear() + 全量重取
│   ├─ _render_preset_selector()     → 预设列表/搜索/CRUD
│   ├─ _render_health_check()        → db.check_data_health()
│   ├─ _render_filter_selectors()    → 滤波器选择
│   └─ _render_param_panels()        → _render_params() → 返回 configs: list[dict]
│
├─► 数据加载 (每视图独立)
│   └─ _load_chart_data()
│       ├─ 浏览模式: _sync_to_display() → query_kline(DB) → Parquet → load_display_cache()
│       └─ 失败回退: _cached_fetch_stock() → _fetch_stock() → yfinance → DB
│
├─► 滤波计算 (@st.cache_data)
│   ├─ _compute_filters()            → FILTERS[fid].func() [filter_engine.py]
│   ├─ _compute_schmitt_trigger()     → _schmitt_trigger() [filter_engine.py]
│   ├─ _compute_prediction_pairs()    → _fit_physics_parabola() [filter_engine.py]
│   └─ _compute_strategy_display()    → _compute_strategy_pnl() [filter_engine.py]
│
├─► 图表渲染 (_render_chart → 每视图 @st.fragment)
│   ├─ _determine_subplot_layout()    [chart_builder.py]
│   ├─ _add_main_price_traces()       [chart_builder.py]
│   ├─ _add_prediction_traces()       [components/charts.py]
│   ├─ _add_residual_traces()         [chart_builder.py]
│   ├─ _add_schmitt_traces()          [chart_builder.py]
│   ├─ _add_pnl_traces()              [chart_builder.py]
│   ├─ _add_cross_pnl_subplot()       [components/charts.py]
│   ├─ _add_alignment_subplot()       [components/charts.py]
│   └─ _render_plotly()               [components/charts.py]
│
└─► 尾部侧边栏
    ├─ _render_export_config()
    ├─ _render_config_history()
    ├─ _render_db_import_export()
    └─ _run_auto_refresh()
```

**主要问题：**

1. **回测代码侵入** — `main()` 中约 25 行回测专用逻辑（L636-637、L744-785、L801-808、L822-836），占 main 函数体的 11%
2. **`_load_chart_data()` 参数污染** — 函数签名包含 `window_start` 和 `cutoff_date`，浏览模式下永远为 None
3. **状态管理不一致** — `components/sidebar.py` 使用原生 `st.session_state` 51 次，其他文件使用 `AppState` 封装
4. **间接依赖** — `streamlit_app.py` 通过 `components.sidebar` 间接获取 `ALL_TFS`，而非直接从 `constants` 导入

**冗余/杂物：**

| 位置 | 内容 | 类型 |
|------|------|------|
| `sidebar_sections.py:73` | `_render_market_ticker()` | 死函数（无调用者） |
| `sidebar_sections.py:423` | `_render_operating_tf_selector()` | 死函数（已被内联替代） |
| `streamlit_app.py:691` | `_stock_name()` | 三处重复定义 |
| `sidebar_sections.py:83` | `_stock_name()` (在死函数内) | 重复 |
| `services/data_loader.py:536` | `_stock_name_lookup()` | 重复 + bug（L555-556 重复赋值） |
| `streamlit_app.py:67` | `from backtest_logger import log_bar_navigation, log_error` | 死导入（从未使用） |
| `streamlit_app.py:7-12` | `import json, os, tempfile; from pathlib import Path` | 死导入（`json` 在函数内用 `import json as _json` 重新导入） |
| `components/__init__.py` | 空文件 | 无导出管理 |

---

### 2.2 回测模式

**涉及文件：**

| 文件 | 行数 | 职责 |
|------|------|------|
| `services/backtest_core.py` | 1144 | 核心回测引擎 + `BacktestRunner` |
| `backtest_cli.py` | 572 | CLI 入口：参数解析 → 配置加载 → 运行 → 保存 |
| `components/backtest_panel.py` | 504 | Web 回测 UI：模式切换、slider、播放控件 |
| `services/event_recorder.py` | 892 | 事件溯源记录：5 个 JSONL + CSV |
| `services/parquet_store.py` | 1041 | 列式持久化：Parquet + CSV |
| `services/backtest_metrics.py` | 138 | Sharpe/Sortino/Calmar 等指标（**未集成**） |
| `services/backtest_catalog.py` | 61 | 回测结果索引（**未集成**） |
| `backtest_logger.py` | 76 | 模式切换日志 |

**核心链路：**

```
┌─── CLI 路径 (backtest_cli.py) ─────────────────────────────────┐
│                                                                  │
│  parse_args() → _build_configs() → BacktestRunner(ticker, configs)
│       ↓                                                          │
│  runner.run(start, end, step)                                    │
│       │                                                          │
│       ├─ _sync_data(cutoff_date)  ← SQLite → 级联合成 → Parquet │
│       │                                                          │
│       └─ 逐 bar 循环 [start_bar → end_bar]:                      │
│             ├─ _load_window_data(tf, n_pts) → Parquet 读取       │
│             ├─ _compute_pipeline_for_view(tf, cfg)               │
│             │   ├─ filters → schmitt → pairs → prediction        │
│             │   └─ → PnL → alignment → BS markers               │
│             ├─ EventRecorder.record_step() → 5 JSONL + CSV       │
│             └─ ParquetStore.append_row() → Parquet + CSV         │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘

┌─── Web 路径 (streamlit_app.py) ─────────────────────────────────┐
│                                                                  │
│  完全不使用 BacktestRunner!                                       │
│                                                                  │
│  _render_param_panels() → configs (list[dict])                   │
│  _sync_all_cascading(ticker, tfs, cutoff_date) → Parquet         │
│                                                                  │
│  for each view:                                                  │
│    _load_chart_data() → load_display_cache(tf)                   │
│    _compute_filters(noisy, t, cfg)        ← @st.cache_data      │
│    _compute_schmitt_trigger(filtered, t, cfg)                    │
│    ... 直接渲染图表 ...                                           │
│                                                                  │
│  ★ 管线计算在 streamlit_app.py 中重复实现                          │
└──────────────────────────────────────────────────────────────────┘
```

**主要问题：**

1. **Web/CLI 管线代码 0% 复用** — `streamlit_app.py` 和 `backtest_core.py` 各自独立实现：
   - `_compute_filters` — Web 版有 `@st.cache_data`，Core 版多 `pipeline_cache` 参数
   - `_compute_schmitt_trigger` — Web 版走 pickle 缓存，Core 版支持跨窗口状态传递
   - `_compute_prediction_pairs` — 逻辑相同但独立实现
   - `_compute_strategy_pnl` — Core 版多了 fallback 空值处理
2. **`BacktestRunner.run()` 职责过多** — 250 行，包含边界校验、数据同步、逐bar循环、并行/顺序分支（约 50 行重复代码）、EWMA 状态管理、PnL 对齐、BS 计算、断点管理
3. **配置管理无类型安全** — 全链路使用裸 `dict`，键名拼写错误静默失败
4. **输出严重冗余** — 单次 CLI 回测产生 10 个文件（5 JSONL + 2 CSV + 1 Parquet + 2 metadata.json）
5. **两份 metadata.json** — EventRecorder 和 ParquetStore 各自写入，后写者覆盖前者

**冗余/杂物：**

| 位置 | 内容 | 类型 |
|------|------|------|
| `services/event_recorder.py` | CSVBuilder（CSV 输出） | 与 ParquetStore CSV 双重写入相同数据 |
| `services/parquet_store.py` | `metadata.json` 写入 | 与 EventRecorder 重复 |
| `services/parquet_store.py` | `end_session()` CSV 导出 | 与 Parquet 内容完全相同（5x 更大） |
| `backtest_core.py:run()` | 并行/顺序分支中 ~50 行重复逻辑 | 代码重复 |

---

### 2.3 CLI 回测数据获取

**涉及文件：**

| 文件 | 行数 | 职责 |
|------|------|------|
| `services/data_loader.py` | 1149 | 上帝类：API 拉取、清洗、SQLite 读写、Parquet 缓存、级联合成、版本管理 |
| `db.py` | 670 | SQLite upsert/query、数据健康检查 |
| `services/parquet_store.py` | 1041 | 回测结果 Parquet 持久化 |
| `config_db.py` | 676 | 预设方案 DB 管理 |

**核心链路：**

```
                    yfinance API
                         │
                         ▼
              _fetch_stock() [data_loader.py]
                ├─ 代码转换: "600519" → "600519.SS"
                ├─ 周期映射: "日线" → "1d"
                ├─ yf.download(full, period, interval)
                ├─ MultiIndex 降维
                ├─ NaN Close 过滤
                └─ 日线 Close 周线回退 (workaround)
                         │
                         ▼
              upsert_kline() [db.py]
                ├─ 历史 bar: INSERT OR IGNORE
                └─ 最新 bar: INSERT OR REPLACE
                         │
                         ▼
              SQLite (data/market.db)              ← 持久层 #1
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
    浏览模式                      回测模式
    _sync_to_display()           _sync_all_cascading()
    ├─ query_kline()             ├─ _query_tf_from_db()
    ├─ df.to_parquet()           ├─ _synthesize_incomplete_bar()
    └─ _save_version()           ├─ _build_output_df()
              │                  ├─ _write_parquet()
              ▼                  └─ _save_version()
    data/display/ Parquet                   │
    (最近 n_pts 条)                          ▼
                               data/display/ Parquet
                               (cutoff_date 对齐)
```

**主要问题：**

1. **`data_loader.py` 上帝类** — 1149 行，9 种职责横跨数据获取、存储、缓存、合成、版本管理五个领域
2. **6 次格式转换** — yfinance→DataFrame→SQLite→DataFrame→Parquet→DataFrame→NumPy，每次涉及类型/时区/索引处理
3. **SQLite + Parquet 双重存储重叠数据** — Parquet 是 SQLite 的"窗口视图"，但独立持久化
4. **Parquet 全量覆盖** — 不支持增量追加，每次 `_sync_to_display` / `_sync_all_cascading` 都重写整个文件
5. **`.version.json` 过度读取** — `_compute_version` 完整读取 Parquet 文件计算元数据，应用 `pyarrow.parquet.read_metadata()` 替代
6. **Web/CLI 数据准备路径分裂** — Web 回测隐式依赖 Web 浏览模式留下的 Parquet，CLI 显式调用 `_sync_all_cascading`

**冗余/杂物：**

| 位置 | 内容 | 类型 |
|------|------|------|
| `data_loader.py:555-556` | `full = code + (".SS"...` 写了两次 | **Bug**（幂等但异常） |
| `data/display/` Parquet | 与 SQLite kline 表数据重叠 | 存储冗余 |
| `.version.json` | 可通过 `os.stat` + `pq.read_metadata` 替代 `pd.read_parquet` | 性能冗余 |

---

### 2.4 回测数据分析

**涉及文件：**

| 文件 | 行数 | 职责 |
|------|------|------|
| `services/backtest_metrics.py` | 138 | 指标计算（**未集成**） |
| `services/backtest_catalog.py` | 61 | 会话索引（**未集成**） |
| `chart_builder.py` | 316 | 图表 trace 构建（浏览+回测共享） |
| `components/charts.py` | 371 | Plotly 渲染（浏览+回测共享） |
| `tools/view_backtest.py` | ~18k | 独立 HTML 可视化（与 charts.py 功能重叠） |
| `tools/analyze_captured_backtest.py` | ~28k | Pipeline Capture 差异分析 |
| `tools/benchmark_pipeline.py` | ~22k | 性能基准测试 |
| `tools/replay_bar.py` | ~9k | 单 bar 重放调试 |
| `tools/trace_long_veto.py` | ~3k | 一次性调试脚本 |

**核心链路：**

```
回测运行 (CLI/Streamlit)
  → BacktestRunner.run()
    → 逐 bar 管道计算
    → ParquetStore: backtest_result.parquet + backtest_result.csv
    → EventRecorder: events.jsonl + filter_tail.jsonl + schmitt_snapshot.jsonl
                     + bs_snapshot.jsonl + trade_summary.jsonl + backtest_data.csv
                     + metadata.json
  → ★ backtest_metrics.compute_backtest_metrics() — 从未被调用
  → ★ BacktestCatalog.save_index() — 从未被调用
  → 分析完全依赖 Streamlit 的 _render_chart()（与浏览模式共用）
```

**主要问题：**

1. **`backtest_metrics.py` 和 `backtest_catalog.py` 是完全的孤儿模块** — 已实现、已测试，但零应用代码调用
2. **无独立的"分析模式"** — 浏览和回测共用 `_render_chart()`，回测完成后无法对比多 run 指标、查看 PnL 分布
3. **Streamlit 中的指标是行内计算** — `_compute_strategy_display()` 手工计算做多/做空收益、胜率，不复用 `backtest_metrics.py`
4. **缺失关键分析图表** — 收益率分布直方图、回撤曲线、滚动 Sharpe/Sortino、多周期 PnL 叠加对比
5. **单次回测产生 10 个输出文件** — 其中 2 个 CSV 内容完全相同，5 个 JSONL 可从 Parquet 重建

**冗余/杂物：**

| 位置 | 内容 | 类型 |
|------|------|------|
| `backtest_output/{session}/backtest_data.csv` | 窗口输入数据 CSV 副本 | 冗余（DB 已有） |
| `backtest_output/{session}/backtest_result.csv` | 与 `.parquet` 完全相同，体积 5x | 冗余 |
| `backtest_output/{session}/metadata.json` (EventRecorder) | 与 ParquetStore 的 metadata.json 冲突 | 冗余 + Bug |
| `tools/view_backtest.py` | 与 `components/charts.py` 功能重叠 | 冗余 |
| `tools/trace_long_veto.py` | 一次性调试脚本 | 可归档 |

---

## 3. 跨域交叉问题

### 3.1 Web/CLI 代码重复度

**核心结论：管线计算代码复用率为 0%。**

| 函数 | streamlit_app.py | backtest_core.py | 差异 |
|------|-----------------|------------------|------|
| `_compute_filters` | L175-202 (`@st.cache_data`) | L712-785 (static method) | Web 版有缓存装饰器；Core 版多 `pipeline_cache` |
| `_compute_schmitt_trigger` | L205-209 (`@st.cache_data`) | L787-839 (static method) | Web 版走 pickle 缓存；Core 版支持跨窗口状态 |
| `_compute_prediction_pairs` | inline in `_render_chart` | L841-884 (static method) | 逻辑相同，独立实现 |
| `_compute_strategy_pnl` | 直接调用 filter_engine | L887-936 (static wrapper) | Core 版多 fallback |

**共享部分仅限于** `filter_engine.py` 中的纯函数（`FILTERS` 注册表、`_schmitt_trigger`、`_find_all_pairs` 等）。

### 3.2 data_loader 上帝类

`data_loader.py` 同时服务于浏览和回测两个域，是系统中最严重的耦合点：

- **浏览模式调用**：`_fetch_stock` → `_sync_to_display` → `load_display_cache`
- **回测模式调用**：`_sync_all_cascading` → `_query_tf_from_db` → `_synthesize_incomplete_bar` → `_write_parquet`

级联合成逻辑（~580行）是该文件最复杂的部分，涉及对 SQLite 的直接查询、时区转换、周期边界计算、B 格线聚合——与"数据加载"概念相距甚远。

### 3.3 输出格式冗余

单次 CLI 回测产生 **10 个文件**：

| 文件 | 可否消除 | 原因 |
|------|---------|------|
| `events.jsonl` | 保留 | BS 变更事件流（唯一用途） |
| `bs_snapshot.jsonl` | 可合并 | 可从 Parquet 重建 |
| `filter_tail.jsonl` | 可合并 | 可从 Parquet 重建 |
| `schmitt_snapshot.jsonl` | 可合并 | 可从 Parquet 重建 |
| `trade_summary.jsonl` | 可合并 | 可从 Parquet 重建 |
| `backtest_data.csv` | **消除** | 数据在 DB 中已有 |
| `backtest_result.parquet` | 保留 | 主要持久化格式 |
| `backtest_result.csv` | **消除** | 与 Parquet 完全相同，体积 5x |
| `metadata.json` (EventRecorder) | **消除一份** | 两份存在覆盖冲突 |
| `metadata.json` (ParquetStore) | 保留 | 统一由此方管理 |

### 3.4 孤立模块

| 模块 | 状态 | 影响 |
|------|------|------|
| `services/backtest_metrics.py` | 已实现 + 有测试 → 零调用 | CLI 回测无指标输出 |
| `services/backtest_catalog.py` | 已实现 + 有测试 → 零调用 | 回测历史不可查询 |
| `web_tool/` | 独立 HTML/JS 应用 | 与主应用零耦合，互补不重叠 |
| `tools/filter_comparison_tool.py` | 独立基准工具 | 与 `web_tool` 部分重叠 |

---

## 4. 目标架构

### 4.1 模块重组方案

```
filter_app/
├── browse/                    ← 浏览模式专属
│   ├── __init__.py
│   ├── app.py                 (streamlit_app.py 的精简入口，移除回测代码)
│   ├── pages.py               (各视图页面，从 app.py 抽取)
│   ├── sidebar.py             (sidebar_sections.py 重命名)
│   ├── charts.py              (chart_builder.py + components/charts.py 合并)
│   └── params.py              (components/sidebar.py，_render_params)
│
├── backtest/                  ← 回测模式专属
│   ├── __init__.py
│   ├── engine.py              (backtest_core.py 精简，仅保留编排)
│   ├── cli.py                 (backtest_cli.py)
│   ├── pipeline.py            (NEW: 从 engine.py 提取单视图管线)
│   ├── aligner.py             (NEW: 跨周期 PnL 对齐 + 持仓掩码)
│   ├── checkpoint.py          (NEW: 断点管理，从 engine.py 提取)
│   ├── metrics.py             (backtest_metrics.py)
│   ├── catalog.py             (backtest_catalog.py)
│   ├── recorder.py            (event_recorder.py，移除 CSVBuilder)
│   ├── store.py               (parquet_store.py，唯一持久化层)
│   ├── logger.py              (backtest_logger.py)
│   └── panel.py               (components/backtest_panel.py)
│
├── data/                      ← 数据层（浏览 + 回测共享）
│   ├── __init__.py
│   ├── fetcher.py             (data_loader._fetch_stock + 清洗)
│   ├── loader.py              (data_loader.load_display_cache + _sync_to_display)
│   ├── synthesis.py           (data_loader 级联合成逻辑，~580行)
│   ├── db.py                  (db.py 原文件)
│   └── config_db.py           (config_db.py 原文件)
│
├── engine/                    ← 计算引擎（共享纯函数）
│   ├── __init__.py
│   ├── filters.py             (filter_engine.py)
│   ├── schmitt.py             (从 filter_engine.py 提取 Schmitt 相关)
│   ├── kalman.py              (从 filter_engine.py 提取 Kalman 相关)
│   └── signals.py             (策略信号生成 + PnL 计算)
│
├── shared/                    ← 共享基础设施
│   ├── __init__.py
│   ├── config.py              (ViewConfig dataclass)
│   ├── constants.py           (ALL_TFS, TF_HIERARCHY, TF_INTERVAL)
│   ├── state.py               (AppState + ViewState)
│   ├── logger.py              (通用日志)
│   └── repository.py          (DB 连接管理等)
│
├── tools/                     ← 保持独立，清理冗余
│   ├── view_backtest.py       (评估是否可废弃，有 Streamlit 可替代)
│   ├── analyze_captured_backtest.py
│   ├── benchmark_pipeline.py
│   ├── replay_bar.py
│   └── filter_comparison_tool.py
│
└── web_tool/                  ← 保持独立，不加耦合
    └── (不变)
```

**services/ 目录将被删除**，内容按职责迁移到 `data/`、`engine/`、`backtest/`。

### 4.2 关键接口定义

**（1）Browse-Backtest 数据共享接口**

```python
# data/loader.py — 统一数据加载接口
def prepare_session_data(
    ticker: str,
    configs: list[ViewConfig],
    cutoff_date: datetime | None = None,
    n_pts: int = 120,
) -> SessionData:
    """
    浏览和回测共享的数据准备入口。

    - cutoff_date=None → 浏览模式：使用最新数据
    - cutoff_date=datetime → 回测模式：数据截止到指定日期
    """
    ...

@dataclass
class SessionData:
    ticker: str
    tfs: dict[str, np.ndarray]          # tf → (t, noisy, ohlc, dates)
    cutoff_date: datetime | None
    n_pts: int
```

**（2）计算引擎统一 API**

```python
# engine/pipeline.py — 被 Web 和 CLI 共享的管线计算
@dataclass
class PipelineInput:
    t: np.ndarray
    noisy: np.ndarray
    ohlc: np.ndarray
    cfg: ViewConfig

@dataclass
class PipelineOutput:
    filtered: np.ndarray
    filtered2: np.ndarray | None
    schmitt_signals: np.ndarray
    prediction_pairs: list[dict]
    pnl_long: np.ndarray
    pnl_short: np.ndarray
    long_pos: np.ndarray
    short_pos: np.ndarray
    bs_entry: np.ndarray
    bs_exit: np.ndarray

def compute_pipeline(input: PipelineInput) -> PipelineOutput:
    """统一管线计算入口。Web 端用 @st.cache_data 包装，CLI 端直接调用。"""
    ...

# 管线步骤（可独立组合）
def compute_filters(noisy, t, cfg) -> tuple[np.ndarray, np.ndarray | None]: ...
def compute_schmitt(filtered, t, cfg) -> np.ndarray: ...
def compute_prediction_pairs(filtered, t, cfg) -> list[dict]: ...
def compute_strategy_pnl(filtered, schmitt, ohlc, cfg) -> StrategyPnl: ...
```

**（3）配置数据类**

```python
# shared/config.py
@dataclass
class ViewConfig:
    """单个视图的完整配置（替代裸 dict）"""
    tf: str
    n_pts: int = 120
    filter_id: str = "savgol"
    filter_params: dict = field(default_factory=dict)
    dual_enabled: bool = False
    dual_filter_id: str | None = None
    dual_filter_params: dict = field(default_factory=dict)
    schmitt: SchmittConfig = field(default_factory=SchmittConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    # UI 字段（颜色、显示开关）
    color: str = "#00d4aa"
    color2: str = "#ff6b6b"
    show_schmitt: bool = True
    show_prediction: bool = True
    show_strategy: bool = True
    show_cross_pnl: bool = False
    show_alignment: bool = False
```

---

## 5. 清理清单

### 立即删除

| 文件/位置 | 原因 |
|-----------|------|
| `sidebar_sections.py:73-100` — `_render_market_ticker()` | 死函数，无调用者 |
| `sidebar_sections.py:423-440` — `_render_operating_tf_selector()` | 死函数，功能已被内联替代 |
| `sidebar_sections.py:82-98` — 内嵌的 `_stock_name()` | 在死函数内部，直接无用 |
| `streamlit_app.py:691-704` — 重复的 `_stock_name()` | 三处重复定义之一，迁移到 `shared/` |
| `streamlit_app.py:67` — `log_bar_navigation, log_error` 导入 | 死导入，从未使用 |
| `streamlit_app.py:7-12` — `json, os, tempfile, Path` 顶层导入 | 死导入，`json` 在函数内重新导入 |

### 建议归档

| 文件/目录 | 原因 |
|-----------|------|
| `tools/trace_long_veto.py` | 一次性调试脚本，移至 `docs/debug/` |
| `components/__init__.py` (空文件) | 无内容，重组后自然消失 |

### 建议合并

| 源文件 | 目标文件 | 原因 |
|--------|---------|------|
| `services/data_loader.py:536-564` — `_stock_name_lookup()` | `shared/config.py` 或 `data/fetcher.py` | 消除 `_stock_name` 三处重复 |
| `chart_builder.py` + `components/charts.py` | `browse/charts.py` | 两者都是图表构建，拆分边界模糊且交叉调用 |
| `streamlit_app.py` 中的 `_compute_filters` 等 | `engine/pipeline.py` (新建) | 与 `backtest_core.py` 中的管线计算合并为统一实现 |
| `services/event_recorder.py` 中的 CSVBuilder | **删除**（仅保留 `parquet_store.py` 的 CSV） | 两份 CSV 内容完全相同 |
| `tools/view_backtest.py` | 评估废弃 | 与 Streamlit 内嵌图表功能重叠 |

### 建议拆分

| 源文件 | 拆分为 | 原因 |
|--------|--------|------|
| `services/data_loader.py` (1149行) | `data/fetcher.py` + `data/loader.py` + `data/synthesis.py` | 上帝类，9 种职责 |
| `services/backtest_core.py` (1144行) | `backtest/engine.py` + `backtest/pipeline.py` + `backtest/aligner.py` + `backtest/checkpoint.py` | `run()` 方法 250 行，职责过多 |
| `streamlit_app.py` (840行) | `browse/app.py` + `browse/pages.py` | 入口 + 编排 + 计算 + 图表混在一起 |

---

## 6. 迁移路线图

### Phase 1: 清理（不改变行为）

**目标：** 消除死代码、修复 bug、统一命名，零行为变化。

**具体任务：**

| # | 任务 | 文件 | 工作量 |
|---|------|------|--------|
| 1.1 | 删除 `_render_market_ticker()` 和 `_render_operating_tf_selector()` | `sidebar_sections.py` | 5 min |
| 1.2 | 删除 `streamlit_app.py` 中的死导入 (`log_bar_navigation`, `log_error`, `json`, `os`, `tempfile`, `Path`) | `streamlit_app.py` | 5 min |
| 1.3 | 修复 `data_loader.py:555-556` 重复赋值 bug | `services/data_loader.py` | 2 min |
| 1.4 | 统一 `_stock_name`：提取到 `shared/utils.py`，删除三处重复定义 | 3 个文件 | 15 min |
| 1.5 | 将 `backtest_metrics.py` 集成到 CLI 回测：`backtest_cli.py` 结束后自动调用 `compute_backtest_metrics()` | `backtest_cli.py` | 20 min |
| 1.6 | 将 `backtest_catalog.py` 集成到 `ParquetStore.end_session()`：自动写入 `.catalog.json` | `parquet_store.py` | 15 min |
| 1.7 | 移除 `EventRecorder` 的 CSVBuilder 和 metadata.json 写入 | `event_recorder.py` | 10 min |
| 1.8 | 运行全量测试确认无回归 | `pytest` | 10 min |

**预估总工作量：** 1.5 小时
**验证方式：** 全量测试通过 + Streamlit 浏览模式正常 + CLI 回测正常

---

### Phase 2: 模块重组（移动文件 + 更新导入）

**目标：** 按目标架构重组目录结构，文件内容基本不变，仅更新 import 路径。

**具体任务：**

| # | 任务 | 涉及文件 | 工作量 |
|---|------|---------|--------|
| 2.1 | 创建 `browse/`、`backtest/`、`data/`、`engine/`、`shared/` 目录 | 新建 | 5 min |
| 2.2 | 移动并重命名浏览模式文件 | `streamlit_app.py` → `browse/app.py`，`sidebar_sections.py` → `browse/sidebar.py` 等 | 30 min |
| 2.3 | 移动并重命名回测模式文件 | `backtest_core.py` → `backtest/engine.py`，`event_recorder.py` → `backtest/recorder.py` 等 | 30 min |
| 2.4 | 移动数据层文件 | `data_loader.py` → `data/fetcher.py` + `data/loader.py`（暂不拆分 synthesis） | 30 min |
| 2.5 | 移动计算引擎文件 | `filter_engine.py` → `engine/filters.py` | 10 min |
| 2.6 | 移动共享文件 | `constants.py`、`state.py`、`config_db.py`、`db.py` → `shared/` | 15 min |
| 2.7 | 全局更新 import 路径 | 所有 `.py` 文件 | 45 min |
| 2.8 | 删除空的 `services/` 和 `components/` 目录 | 清理 | 5 min |
| 2.9 | 运行全量测试 + 手动验证 Streamlit + CLI | 验证 | 20 min |

**预估总工作量：** 3.5 小时
**验证方式：** 全量测试通过 + `streamlit run browse/app.py` 正常 + `python backtest/cli.py` 正常

---

### Phase 3: 消除重复（统一 Web/CLI）

**目标：** 消除 Web 和 CLI 的管线计算重复，统一数据准备路径。

**具体任务：**

| # | 任务 | 涉及文件 | 工作量 |
|---|------|---------|--------|
| 3.1 | 提取 `engine/pipeline.py`：统一 `compute_pipeline()` 函数 | 新建 + `browse/app.py` + `backtest/engine.py` | 2 h |
| 3.2 | Web 端用 `@st.cache_data` 包装 `compute_pipeline()`，而非自实现 | `browse/app.py` | 30 min |
| 3.3 | CLI 端用 `BacktestRunner` 调用统一的 `compute_pipeline()` | `backtest/engine.py` | 1 h |
| 3.4 | 统一数据准备路径：`prepare_session_data()` | `data/loader.py` | 1 h |
| 3.5 | Web 回测面板开始前显式调用 `prepare_session_data(cutoff_date=...)` | `backtest/panel.py` | 30 min |
| 3.6 | 引入 `ViewConfig` dataclass，在入口处验证 | `shared/config.py` + 所有 config 消费方 | 1.5 h |
| 3.7 | 消除 Parquet 全量覆盖：实现增量追加 | `data/loader.py` | 1 h |
| 3.8 | 将 `.version.json` 改为使用 `pyarrow.parquet.read_metadata()` | `data/loader.py` | 20 min |
| 3.9 | 运行全量测试 + 手动验证 | 验证 | 30 min |

**预估总工作量：** 8 小时
**验证方式：** Web 和 CLI 使用相同参数产生相同管线输出（逐 bar 对比验证）

---

### Phase 4: 架构验证（全量测试 + CI）

**目标：** 确认重构后的系统行为与重构前完全一致。

**具体任务：**

| # | 任务 | 工作量 |
|---|------|--------|
| 4.1 | 补齐缺失的单元测试（特别是 `engine/pipeline.py` 和 `data/loader.py`） | 2 h |
| 4.2 | 回归测试：用 3+ 组预设方案在 Web 和 CLI 分别运行，逐 bar 对比输出 | 2 h |
| 4.3 | 性能回归测试：确认重构后回测速度无退化（重点是 Parquet 增量写入效果） | 1 h |
| 4.4 | 清理回测输出目录，运行完整回测确认输出文件结构正确 | 30 min |
| 4.5 | 文档更新（README 中的目录结构说明） | 30 min |

**预估总工作量：** 6 小时

---

### 工作量汇总

| Phase | 内容 | 预估时间 | 风险等级 |
|-------|------|---------|---------|
| Phase 1 | 清理（删死代码、修 bug、集成孤儿模块） | 1.5 h | **低** |
| Phase 2 | 模块重组（移动文件 + 更新导入） | 3.5 h | **中** |
| Phase 3 | 消除重复（统一 Web/CLI 管线 + dataclass） | 8 h | **高** |
| Phase 4 | 架构验证（补齐测试 + 回归对比） | 6 h | **中** |
| **总计** | | **~19 h** (约 2.5 个工作日) | |

---

## 7. 风险与收益

### 清理工作量

- **删除代码：** ~200 行（死函数、死导入、CSVBuilder）
- **移动代码：** ~6000 行（文件重组，内容不变）
- **重写代码：** ~400 行（统一管线计算 + ViewConfig dataclass）
- **新增代码：** ~200 行（engine/pipeline.py、测试）
- **净减少：** ~500 行（消除重复管线 + CSV 冗余 + 死代码）

### 测试回归风险

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| import 路径遗漏或错误 | 中 | Phase 2 全局搜索替换 + IDE 静态检查 |
| Web/CLI 管线统一后计算结果差异 | **高** | Phase 3 逐 bar 对比验证，发现差异后逐函数排查 |
| Parquet 增量写入损坏数据一致性 | 中 | Phase 3 先保留全量覆盖作为 fallback，验证通过后再切换 |
| dataclass 引入破坏现有 config dict 消费方 | 中 | Phase 3 渐进迁移：先支持 dict 和 dataclass 双输入，再统一 |
| 模块重组后 Streamlit 路径变更导致部署失败 | 低 | Phase 2 更新 `streamlit run` 入口路径即可 |

### 预期收益

| 维度 | 当前 | 重构后 | 改善 |
|------|------|--------|------|
| **代码行数** | ~12,000 | ~11,500 | -4% |
| **管线重复** | 2 份独立实现 (~200 行) | 1 份统一实现 | bug 修复一次到位 |
| **data_loader.py** | 1149 行 1 文件 | 3 文件各 ~400 行 | 认知负担降低 60% |
| **回测输出文件数** | 10 个/次 | 3 个/次（parquet + events + metadata） | -70% 磁盘占用 |
| **回测 I/O** | 全量覆盖 Parquet | 增量追加 | 写入量减少 >90% |
| **孤儿模块** | 2 个 | 0 个 | 指标和索引自动生成 |
| **格式转换次数** | 6 次 | 4 次 | 减少 SQLite 往返 |
| **配置类型安全** | 裸 dict | ViewConfig dataclass | 拼写错误编译期发现 |
