# filter_research 架构深度分析报告

> 分析日期：2026-07-23 | 版本：10.5.0 | 作者：Researcher Agent

---

## 目录

1. [项目概要](#1-项目概要)
2. [目录组织](#2-目录组织)
3. [模块架构与依赖关系](#3-模块架构与依赖关系)
4. [入口点与启动流程](#4-入口点与启动流程)
5. [核心领域模型](#5-核心领域模型)
6. [耦合度评估](#6-耦合度评估)
7. [架构反模式](#7-架构反模式)
8. [分层架构评估](#8-分层架构评估)
9. [可扩展性评估](#9-可扩展性评估)
10. [发现汇总与优先级排序](#10-发现汇总与优先级排序)

---

## 1. 项目概要

| 维度 | 详情 |
|------|------|
| **名称** | filter-research |
| **版本** | 10.5.0 |
| **描述** | 多周期股票滤波分析工具 -- 交互式对比 10 种滤波算法（SMA/EMA/WMA/ALMA/Savitzky-Golay/Kalman/Butterworth/Gaussian/Median/LOWESS），内置施密特触发器信号生成、抛物线预测、策略 PnL 回测、跨周期对齐分析 |
| **语言** | Python 3.11+ |
| **核心依赖** | Streamlit 1.57, NumPy 2.2, SciPy 1.17, Plotly 6.7, Pandas 2.3, yfinance 1.4, PyArrow 14+, statsmodels 0.14 |
| **持久化** | 双 SQLite 库（market.db + config.db），Parquet 显示缓存，JSONL 事件日志 |
| **部署** | Docker Compose (端口 8501)，纯前端备选方案 |
| **测试** | 623 个测试 (575 单元+集成 + 23 UI + 25 纯函数)，覆盖率 ~50% |
| **CI** | GitHub Actions: ruff lint + mypy type-check + pytest + coverage (阈值 45%) |

代码量统计：

| 模块 | 行数 | 职责 |
|------|------|------|
| `streamlit_app.py` | 1,702 | Web 入口：布局编排、图表构建、所有侧边栏组件 |
| `services/filter_engine.py` | 1,051 | 滤波算法实现、施密特触发器、策略 PnL |
| `services/parquet_store.py` | 1,022 | 回测输出列式持久化 |
| `services/backtest_core.py` | 1,013 | 回测管道编排 |
| `services/event_recorder.py` | 890 | 回测事件 JSONL 记录 |
| `services/data_loader.py` | 818 | yfinance 数据拉取、SQLite/Parquet 缓存 |
| `db.py` | 644 | 市场 K 线数据层 |
| `config_db.py` | 665 | 预设配置持久化 |
| `backtest_cli.py` | 575 | CLI 回测入口 |
| `components/backtest_panel.py` | 502 | 回测控制面板 UI |
| `components/charts.py` | 450 | Plotly 图表渲染 |
| `state.py` | 313 | Session state 管理 |
| `components/sidebar.py` | 321 | 侧边栏参数控件 |
| `services/pipeline_capture.py` | 299 | 回测管道调试捕获 |
| `services/bs_marker.py` | 200 | BS 买卖标记计算 |
| `backtest_logger.py` | 76 | 回测操作日志 |
| **filter_app 总计** | **~10,465** | -- |

---

## 2. 目录组织

### 2.1 顶层结构

```
filter_research/
├── filter_app/              # ★ 应用主目录 (Streamlit + CLI)
│   ├── streamlit_app.py     #   Web 入口 (1702 行)
│   ├── backtest_cli.py      #   CLI 入口 (575 行)
│   ├── backtest_logger.py   #   JSONL 日志 (76 行)
│   ├── state.py             #   Session state 抽象
│   ├── db.py                #   市场数据 SQLite 层
│   ├── config_db.py         #   预设配置 SQLite 层
│   ├── requirements.txt     #   依赖清单
│   ├── components/          #   UI 组件层
│   │   ├── charts.py        #   Plotly 图表构建 + HTML 渲染
│   │   ├── sidebar.py       #   侧边栏参数控件
│   │   └── backtest_panel.py#   回测控制面板
│   ├── services/            #   业务逻辑层
│   │   ├── filter_engine.py #   滤波算法 + 信号 + 策略 (纯函数)
│   │   ├── data_loader.py   #   数据拉取与缓存
│   │   ├── backtest_core.py #   回测编排器
│   │   ├── bs_marker.py     #   BS 买卖标记计算
│   │   ├── event_recorder.py#   回测事件记录
│   │   ├── parquet_store.py #   回测数据列式存储
│   │   └── pipeline_capture.py # 管道调试捕获
│   └── tests/               #   仅 test_bs_marker.py (420 行)
├── tests/                   # ★ 测试目录 (33 个文件)
├── tools/                   # 独立工具脚本
│   ├── filter_comparison_tool.py  # 滤波对比分析
│   ├── benchmark_pipeline.py      # 管道性能基准
│   ├── analyze_captured_backtest.py# 捕获数据回放分析
│   ├── view_backtest.py           # 回测结果查看
│   ├── replay_bar.py              # 单 bar 重放
│   └── trace_long_veto.py         # 做多否决追踪
├── web_tool/                # 纯前端版本 (HTML/JS)
├── data/                    # 持久化数据
│   ├── market.db            #   K 线数据库
│   ├── config.db            #   预设配置库
│   ├── snapshots/           #   数据库快照
│   ├── display/             #   Parquet 显示缓存
│   └── backtest_logs/       #   回测操作日志
├── backtest_output/         # 回测结果 (4 个 ticker)
├── docs/                    # 文档 (11 个 markdown)
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── diagnose_axis.py         # Plotly 轴诊断脚本
└── index.html               # 纯前端重定向
```

### 2.2 职责划分评估

| 目录 | 责任清晰度 | 评语 |
|------|-----------|------|
| `filter_app/` | **中等** | 混合了入口文件、数据层、状态管理、UI 组件和业务逻辑，缺乏 package 级别的模块边界 |
| `filter_app/services/` | **良好** | 职责清晰，多数模块零项目内依赖（纯计算或纯持久化），高度可复用 |
| `filter_app/components/` | **良好** | 仅 Streamlit UI 相关，但 `charts.py` 包含 HTML/JS 混合逻辑 |
| `tests/` | **良好** | 31 个测试文件覆盖主要模块，但出现 `tests/unit/` 和 `tests/integration/` 的空目录 |
| `tools/` | **中等** | 6 个独立脚本，功能有重叠 (如 `view_backtest.py` 与 `analyze_captured_backtest.py`)，缺乏统一入口 |
| `web_tool/` | **良好** | 独立前端版本，零依赖 |
| `data/` | **模糊** | 混合了数据库文件、Parquet 缓存、JSON 配置、快照备份、日志文件 -- 建议细分 |
| `docs/` | **良好** | 11 个文档覆盖操作指南、实现规范、差异分析，但 README 中引用的 `config/` 目录不存在 |

---

## 3. 模块架构与依赖关系

### 3.1 模块依赖图

```
                          ┌─────────────────────────────────────┐
                          │          streamlit_app.py           │
                          │          (1702 行 - God Module)      │
                          │  页面编排 · 图表构建 · 侧边栏 · 回测 │
                          └────┬────┬─────┬─────┬────┬─────┬────┘
                               │    │     │     │    │     │
              ┌────────────────┼────┼──┐  │     │    │     │
              ▼                │    │  │  │     │    │     │
     ┌──────────────┐          │    │  │  │     │    │     │
     │   state.py   │          │    │  │  │     │    │     │
     │  AppState    │◄─────────┘    │  │  │     │    │     │
     │  ViewState   │               │  │  │     │    │     │
     └──────────────┘               │  │  │     │    │     │
                                    │  │  │     │    │     │
     ┌──────────────┐               │  │  │     │    │     │
     │  config_db   │◄──────────────┘  │  │     │    │     │
     │ (config.db)  │                  │  │     │    │     │
     └──────┬───────┘                  │  │     │    │     │
            │ from db import init_db   │  │     │    │     │
            ▼                          │  │     │    │     │
     ┌──────────────┐                  │  │     │    │     │
     │    db.py     │◄─────────────────┘  │     │    │     │
     │ (market.db)  │                     │     │    │     │
     └──────────────┘                     │     │    │     │
                                          │     │    │     │
     ┌────────────────────────────────────┘     │    │     │
     │                                          │    │     │
     ▼                                          │    │     │
     ┌──────────────┐                           │    │     │
     │ backtest_    │                           │    │     │
     │ logger.py    │                           │    │     │
     └──────────────┘                           │    │     │
                                                │    │     │
     ┌──────────────────┐                       │    │     │
     │  components/     │                       │    │     │
     │  ┌────────────┐  │                       │    │     │
     │  │ charts.py  │──┼───► services/filter_engine │     │
     │  ├────────────┤  │                       │    │     │
     │  │ sidebar.py │──┼───► services/filter_engine │     │
     │  ├────────────┤  │                       │    │     │
     │  │ backtest_  │  │                       │    │     │
     │  │ panel.py   │──┼───► state, logger, sidebar │     │
     │  └────────────┘  │                       │    │     │
     └──────────────────┘                       │    │     │
                                                │    │     │
     ┌──────────────────────────────────────────┘    │     │
     │                                               │     │
     ▼                                               │     │
     ┌───────────────────────────────────────────────┼─────┼──────┐
     │  services/                                   │     │      │
     │                                              │     │      │
     │  ┌──────────────────┐                        │     │      │
     │  │ filter_engine.py │ ◄──────────────────────┼─────┼──────┤
     │  │ (零项目内依赖)    │                        │     │      │
     │  │ 10种滤波算法      │                        │     │      │
     │  │ 施密特触发器      │                        │     │      │
     │  │ 抛物线/多项式预测 │                        │     │      │
     │  │ 策略PnL计算       │                        │     │      │
     │  │ 跨周期对齐        │                        │     │      │
     │  └────────┬─────────┘                        │     │      │
     │           │                                   │     │      │
     │  ┌────────┼──────────────────────────────┐    │     │      │
     │  │ backtest_core.py                      │    │     │      │
     │  │ (编排 filter_engine + data_loader)     │    │     │      │
     │  └────────┼──────────────────────────────┘    │     │      │
     │           │                                   │     │      │
     │  ┌────────┼─────────┐  ┌──────────────┐      │     │      │
     │  │data_loader.py    │  │ bs_marker.py │      │     │      │
     │  │→ db.py           │  │(零项目内依赖) │      │     │      │
     │  └──────────────────┘  └──────────────┘      │     │      │
     │                                              │     │      │
     │  ┌──────────────┐ ┌──────────────┐           │     │      │
     │  │event_recorder│ │parquet_store │           │     │      │
     │  │(零项目内依赖) │ │(零项目内依赖) │           │     │      │
     │  └──────────────┘ └──────────────┘           │     │      │
     │                                              │     │      │
     │  ┌──────────────────┐                        │     │      │
     │  │pipeline_capture  │                        │     │      │
     │  │(零项目内依赖)     │                        │     │      │
     │  └──────────────────┘                        │     │      │
     └──────────────────────────────────────────────┘     │      │
                                                         │      │
     ┌───────────────────────────────────────────────────┘      │
     │                                                          │
     │  ┌──────────────────────────────────────────────────────┘
     │  │
     ▼  ▼
     ┌──────────────┐
     │backtest_cli  │  (CLI入口，直接调用 services + config_db + db)
     └──────────────┘
```

### 3.2 依赖方向分析

**核心依赖链**（从底层到顶层）：

```
外部库 (numpy/scipy/plotly/yfinance/streamlit)
    │
    ├──► db.py ──────────────────────── 数据持久化层
    │       ▲
    │       │
    ├──► config_db.py ───────────────── 配置持久化层
    │       ▲
    │       │
    ├──► services/filter_engine.py ──── 核心计算引擎 (零项目内依赖 ★)
    │       ▲
    │       │
    ├──► services/bs_marker.py ──────── BS 标记计算 (零项目内依赖 ★)
    │       ▲
    │       │
    ├──► services/data_loader.py ────── 数据加载 (→ db.py)
    │       ▲
    │       │
    ├──► services/backtest_core.py ──── 回测编排 (→ filter_engine, data_loader, bs_marker, db)
    │       ▲
    │       │
    ├──► services/event_recorder.py ─── 事件记录 (零项目内依赖 ★)
    ├──► services/parquet_store.py ──── 列式存储 (零项目内依赖 ★)
    ├──► services/pipeline_capture.py ─ 管道捕获 (零项目内依赖 ★)
    ├──► state.py ───────────────────── 状态抽象
    │       ▲
    │       │
    ├──► backtest_logger.py ─────────── 回测日志
    │       ▲
    │       │
    ├──► components/charts.py ───────── UI 渲染 (→ filter_engine)
    ├──► components/sidebar.py ──────── UI 控件 (→ filter_engine)
    ├──► components/backtest_panel.py ─ UI 面板 (→ state, logger, sidebar, db)
    │       ▲
    │       │
    └──► streamlit_app.py ───────────── 应用编排器 (→ ALL of the above)
            │
            └──► backtest_cli.py ────── CLI 编排器 (→ backtest_core, event_recorder, filter_engine, parquet_store, config_db, db)
```

**依赖方向结论：整体单向，无循环依赖。** `streamlit_app.py` 处于最顶层，是所有模块的汇聚点。

### 3.3 关键发现

1. **5 个模块零项目内依赖**（`filter_engine`, `bs_marker`, `event_recorder`, `parquet_store`, `pipeline_capture`）-- 这些是架构中健康的部分，可以独立提取为库。

2. **`streamlit_app.py` 是架构瓶颈** -- 它直接导入 25+ 符号，知晓所有模块的细节。

3. **`ALL_TFS` 常量重复定义 4 次**（`data_loader.py:16`, `backtest_core.py:30`, `sidebar.py:13`, `bs_marker.py`），且 `TF_HIERARCHY` 重复 2 次 -- 缺乏统一的常量定义模块。

4. **`config_db.py` → `db.py`** 存在反向引用（line 652: `from db import init_db`），打破了"数据层在上，配置层在下"的直觉层级。

---

## 4. 入口点与启动流程

### 4.1 三个入口点

| 入口 | 文件 | 启动方式 | 目标用户 |
|------|------|---------|---------|
| **Web** | `filter_app/streamlit_app.py` | `streamlit run` | 交互式分析用户 |
| **CLI** | `filter_app/backtest_cli.py` | `python -m filter_app.backtest_cli` | 批量回测用户 |
| **纯前端** | `web_tool/index.html` | `python3 -m http.server 8765` | 零依赖体验 |

根目录 `index.html` 仅做重定向：`index.html` → `web_tool/`

### 4.2 Web 启动流程（streamlit_app.main()）

```
1. run_backtest_play()            ← 回测自动播放: 提前更新窗口位置
2. _get_db_connection()           ← 初始化 DB 连接 + 建表 (market.db)
3. init_config_tables()           ← 初始化配置表 (config.db)
4. AppState.init_defaults()       ← 初始化 session_state 默认值
5. import_json_files_as_presets() ← 首次启动: 导入 config/*.json 为预设
6. 自动应用 AAPL_US 预设          ← 首次加载: 自动选中默认配置
7. 渲染侧边栏 (自上而下):
   ├── _handle_pending_apply()    ← 处理待应用的预设参数
   ├── _render_config_import()    ← JSON 配置文件上传
   ├── _render_market_ticker()    ← 市场 + 股票代码输入
   ├── _handle_initial_fetch()    ← 首次获取全部 8 周期数据
   ├── _render_refresh_row()      ← 刷新按钮 + 自动刷新
   ├── _render_preset_selector()  ← 预设选择、应用、保存、删除
   ├── _render_health_check()     ← 数据健康检查
   ├── _render_data_validation()  ← DB vs yfinance 数据校验
   ├── _render_filter_selectors() ← 滤波器类型选择
   ├── _render_param_panels()     ← 2x2 视图参数面板
   ├── render_backtest_panel()    ← 回测模式控制面板
   └── _render_db_backup()        ← 数据库快照/恢复
8. 渲染 2x2 图表网格:
   ├── 回测模式: _sync_all_cascading() 前置级联合成
   ├── 4 个 @st.fragment 视图独立渲染
   └── _render_chart() → 数据加载 → 滤波计算 → 信号计算 → PnL → Plotly
9. 渲染底部:
   ├── _render_export_config()    ← 导出 JSON 配置
   ├── _render_config_history()   ← 配置变更历史
   └── _render_db_import_export() ← 数据库导入/导出
10. _run_auto_refresh()           ← 自动刷新 (阻塞 sleep + rerun)
```

### 4.3 CLI 启动流程（backtest_cli.main()）

```
1. parse_args()                   ← 解析 CLI 参数
2. 校验 ticker 存在              ← has_data()
3. 加载配置:
   ├── --preset → apply_preset() → _build_configs_from_params()
   ├── --config-file → _load_configs_from_file()
   └── 默认 → _build_default_configs()
4. 确定 bar 范围:
   ├── _get_total_bars(ticker, configs)
   └── _get_min_window_size(configs)
5. 创建 BacktestRunner + EventRecorder
6. 运行回测循环:
   └── runner.run(start, end, step_interval)
       └── for each step:
           ├── recorder.record_step()
           └── parquet_store.append_row()
7. 保存结果 + 打印摘要
```

---

## 5. 核心领域模型

### 5.1 领域类层次

```
┌──────────────────────────────────────────────────────────┐
│                     领域类                                │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ★ AppState (state.py)                                   │
│    集中式 session_state 管理, _imp_ 备份机制              │
│    └── ViewState: 单视图参数封装                          │
│                                                          │
│  ★ FILTERS 注册表 (filter_engine.py)                     │
│    Dict[str, {name, func, params}]                       │
│    10 种滤波器: sma, ema, wma, alma, savgol,             │
│                 kalman, butterworth, gaussian,            │
│                 median, lowess                            │
│                                                          │
│  ★ BacktestRunner (backtest_core.py)                     │
│    回测管道编排器：逐 bar 计算并收集各阶段输出            │
│                                                          │
│  ★ EventRecorder (event_recorder.py)                     │
│    JSONL 事件追加记录器，含 CSVBuilder 子组件             │
│                                                          │
│  ★ ParquetStore (parquet_store.py)                       │
│    回测数据列式持久化 (PyArrow/ZSTD)                      │
│                                                          │
│  ★ PipelineCapture (pipeline_capture.py)                  │
│    管道调试数据捕获，按 step 保存各阶段中间输出           │
│    └── PipelineStageData: 单视图阶段数据容器              │
│                                                          │
│  ★ CSVBuilder (event_recorder.py)                        │
│    累积逐 bar 数据，end_session 时写 CSV                  │
│                                                          │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│                     数据模型                              │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  market.db (db.py):                                      │
│    kline(ticker, timeframe, ts, open, high, low,         │
│          close, volume)                                  │
│    PK: (ticker, timeframe, ts)                           │
│                                                          │
│  config.db (config_db.py):                               │
│    config_presets(preset_id, name, description,           │
│                   category, params_json, ...)            │
│    config_ticker(ticker, variant, market, preset_id, ...) │
│    config_history(id, ticker, variant, preset_id, ...)    │
│                                                          │
│  Parquet 显示缓存:                                       │
│    data/display/{ticker}/{tf}.parquet                    │
│    列: Date, Open, High, Low, Close                     │
│                                                          │
│  回测 JSONL 事件 (backtest_logger.py):                   │
│    backtest_{YYYYMMDD}.jsonl                             │
│    事件类型: mode_switch, bar_navigation,                 │
│             data_load, error                             │
│                                                          │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│                     视图配置模型 (cfg dict)               │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  {                                                       │
│    "tf": str,             # 周期 (日线/60分钟/...)        │
│    "n_pts": int,          # 数据点数                      │
│    "_fid": str,           # 主滤波器 ID                   │
│    "pv": dict,            # 主滤波器参数                  │
│    "_dual": bool,         # 双滤波开关                    │
│    "_fid2": str,          # 副滤波器 ID                   │
│    "pv2": dict,           # 副滤波器参数                  │
│    "show_sch": bool,      # 施密特触发器                  │
│    "ke": float,           # k_ε 灵敏度                   │
│    "sm": float,           # σ_min 地板保护               │
│    "ew": int,             # N_EWMA 平滑周期              │
│    "show_pred": bool,     # 预测曲线                      │
│    "fit_mode": str,       # 拟合模式                      │
│    "n_ext": int,          # 预测延伸点数                  │
│    "show_strategy": bool, # 策略 PnL                     │
│    "stop_loss_pct": float,# 止损百分比                    │
│    "fc"/"fc2": str,       # 滤波曲线颜色                  │
│    "show_cross_pnl": bool,# 跨周期 PnL                   │
│    "show_alignment": bool,# 同向性判断                    │
│    "show_pnl_feedback": bool, # 实际持仓反馈              │
│  }                                                       │
│                                                          │
│  注: 这是一个"动态 dict"模型 — 没有形式化的类定义，        │
│  通过 VIEW_PARAM_SPECS (config_db.py) 约束字段。          │
│  类型检查较弱，拼写错误在运行时才能发现。                  │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### 5.2 计算管道

```
原始K线数据 (yfinance → SQLite → Parquet)
    │
    ▼
[滤波计算] FILTERS[fid].func(noisy, t, **pv)
    │ → filtered: 平滑后价格
    │ → filtered2 (可选): 第二滤波器输出
    │
    ▼
[信号计算] _schmitt_trigger(v, a, ewma_span, k_eps, sigma_min)
    │ → sig: 三态信号 (+1/0/-1)
    │ → eps: 自适应死区
    │ → sigma_v: EWMA 波动率估计
    │
    ▼
[对分割] _find_all_pairs(sig)
    │ → all_pairs: [(start, end), ...]
    │
    ▼
[预测] _fit_physics_parabola(t, filtered, start, end)
    │ → pred_pairs: [{fit_result, fit_start, pair_end}, ...]
    │
    ▼
[策略PnL] _compute_strategy_pnl(t, filtered, sig, all_pairs, pred_pairs, ...)
    │ → long_pnl, short_pnl: 做多/做空净值曲线
    │ → trade_records: 逐笔交易记录
    │
    ▼
[跨周期对齐] _align_pnl_to_current_tf(high_dates, high_pnl, ..., current_dates)
    │ → higher_pnl: {entry_markers, exit_markers}
    │
    ▼
[同向性] _compute_holding_masks(len_t, entry_markers, exit_markers)
    │ → long_mask, short_mask: 高周期持仓标记
    │
    ▼
[Plotly渲染] _render_plotly(fig, height, dates)
    │ → 动态子图布局 (4-8 行自适应)
```

---

## 6. 耦合度评估

### 6.1 耦合矩阵

| 模块 | streamlit_app | components | services | db | config_db | state | backtest_cli |
|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **streamlit_app** | - | H | H | H | H | H | - |
| **components/** | - | - | M | M | - | M | - |
| **services/filter_engine** | - | - | - | - | - | - | - |
| **services/data_loader** | - | - | - | M | - | - | - |
| **services/backtest_core** | - | - | H | M | - | - | - |
| **db** | - | - | - | - | - | - | - |
| **config_db** | - | - | - | L | - | - | - |
| **state** | H | M | - | - | - | - | - |
| **backtest_cli** | - | - | H | M | M | - | - |

H=高耦合(6+导入), M=中耦合(3-5), L=低耦合(1-2), - =无耦合

### 6.2 关键发现

**高耦合节点（问题区域）：**

1. **`streamlit_app.py` (上帝模块)**
   - 导入 25+ 符号，依赖 9 个内部模块
   - 包含 UI 渲染、数据加载、图表构建、回测编排 4 种职责
   - 改变任何子模块都可能影响此文件

2. **`services/backtest_core.py`**
   - 导入 filter_engine 6 个函数 + data_loader + bs_marker + db
   - 是回测管道的事实编排层，合理但耦合度较高

**低耦合节点（健康区域）：**

3. **`services/filter_engine.py`** -- 零项目内依赖，纯函数，可独立测试
4. **`services/bs_marker.py`** -- 零项目内依赖
5. **`services/event_recorder.py`** -- 零项目内依赖
6. **`services/parquet_store.py`** -- 零项目内依赖
7. **`services/pipeline_capture.py`** -- 零项目内依赖
8. **`db.py`** -- 仅依赖 Python 标准库 + pandas + loguru

### 6.3 隐式耦合

1. **Session State 全局状态**：`streamlit_app.py` 和 `components/backtest_panel.py` 通过 `st.session_state` 隐式共享状态，缺乏形式化的接口约束。

2. **cfg dict 契约**：视图配置通过无类型的 dict 在 `streamlit_app.py` → `_render_chart` → `_compute_filters` → `_compute_schmitt_trigger` 等函数间传递，字段约束分散在 `VIEW_PARAM_SPECS`/`VIEW_DEFAULTS` 中，运行时拼写错误难以发现。

3. **常量重复**：`ALL_TFS` 在 4 个文件重复定义，修改时需同步 4 处。

4. **延迟导入**：`data_loader.py` 和 `backtest_panel.py` 中有 5+ 处函数内 `from db import get_conn` 延迟导入，暗示模块初始化顺序敏感。

---

## 7. 架构反模式

### 7.1 发现清单

| # | 反模式 | 位置 | 严重度 | 详情 |
|---|--------|------|--------|------|
| 1 | **上帝模块 (God Module)** | `streamlit_app.py` (1702行) | **严重** | 集页面编排、图表构建、侧边栏逻辑、回测协调于一体。40+ 个函数定义，掌管全部 UI 交互流 |
| 2 | **上帝类 (God Class)** | 数据模型 `cfg dict` | **中等** | 20+ 字段的扁平 dict，通过 `VIEW_PARAM_SPECS` 外部约束 -- 应该是一个 dataclass |
| 3 | **魔法字符串泛滥** | 全局 | **高** | `"日线"`, `"60分钟"` 等周期名作为字符串硬编码，`"v0_ke"` 等 session key 拼接在多个文件中 |
| 4 | **空 __init__.py** | `components/__init__.py` | **低** | 未定义组件的公共 API，外部通过隐式文件名导入 |
| 5 | **碎片化状态管理** | `state.py` + `st.session_state` | **中等** | `AppState` 和 `st.session_state` 双轨并行，`_imp_` 备份增加调试复杂性 |
| 6 | **多个数据输出通道** | 回测管道 | **中等** | 同一回测数据同时写入 EventRecorder (JSONL)、ParquetStore (Parquet)、PipelineCapture (JSON)，三套序列化逻辑 |
| 7 | **硬编码文件路径** | `backtest_logger.py`, `db.py` | **低** | `LOG_DIR`, `DB_PATH`, `_CONFIG_DB_PATH` 硬编码为相对路径，缺乏可配置性 |
| 8 | **缺少类型化领域模型** | `cfg dict`, pipeline stage data | **中等** | 核心数据流以裸 dict 传递，`PipelineStageData` 使用 `__slots__` 但各字段仍为 `Any` 类型 |
| 9 | **混合 HTML 渲染** | `components/charts.py` | **中等** | Plotly JSON 序列化后嵌入自定义 HTML/JS 模板 (200+ 行 JS)，混合了 Python 和前端渲染逻辑 |
| 10 | **UI 与业务逻辑耦合** | `streamlit_app.py`, `backtest_panel.py` | **高** | 数据处理逻辑和 Streamlit 渲染调用混在一起，难以单独测试 |

### 7.2 反模式详解

**#1: 上帝模块 `streamlit_app.py` (1702 行)**

该文件承担了 4 种本应分离的职责：
- **应用生命周期**: init_db(), init_config_tables(), AppState.init_defaults(), auto-apply preset
- **数据加载编排**: _cached_fetch_stock(), _load_chart_data(), _sync_to_display()
- **图表构建**: _render_chart() -- 包含 15 步计算管道 + Plotly figure 组装
- **侧边栏 UI**: 9 个 `_render_*` 函数 + 预设管理 CRUD 流程

建议拆分：
```
streamlit_app.py        → 仅页面布局 (st.columns, st.sidebar, main())
chart_builder.py        → _render_chart(), _add_*_traces(), _determine_subplot_layout()
sidebar_sections.py     → 9个 _render_* 函数
app_initializer.py      → 启动初始化逻辑
```

**#7: 魔法字符串**

周期名和 session key 跨文件拼接：
```python
# 4个文件各自定义
ALL_TFS = ["1分钟", "5分钟", "15分钟", "60分钟", "日线", "周线", "月线", "季线"]
# state.py 中 ViewState 拼接
key = f"{self._PREFIX}{vi}_{suffix}"  # → "v0_ke"
# streamlit_app.py 中
st.session_state[f"_pnl_{tf}"]  # → "_pnl_日线"
```

**#9: Python/HTML 混合渲染**

`charts.py` 中 `_render_plotly()` 包含 ~200 行嵌入式 JavaScript（Plotly 初始化、跨子图十字线、日期提示、CDN 回退），这些前端逻辑嵌套在 Python f-string 中，无法被 linter 检查，无法单独测试。

---

## 8. 分层架构评估

### 8.1 当前分层

```
┌──────────────────────────────────────┐
│       表现层 (Presentation)          │
│  streamlit_app.py                     │
│  components/charts.py                 │
│  components/sidebar.py                │
│  components/backtest_panel.py         │
├──────────────────────────────────────┤
│       应用层 (Application)            │
│  state.py (AppState/ViewState)        │
│  backtest_cli.py (CLI编排)            │
│  backtest_logger.py                   │
├──────────────────────────────────────┤
│       领域层 (Domain)                 │
│  services/filter_engine.py            │
│  services/bs_marker.py                │
│  services/backtest_core.py            │
│  services/pipeline_capture.py         │
├──────────────────────────────────────┤
│      基础设施层 (Infrastructure)      │
│  services/data_loader.py              │
│  services/event_recorder.py           │
│  services/parquet_store.py            │
│  db.py                                │
│  config_db.py                         │
└──────────────────────────────────────┘
```

### 8.2 分层违规

| 违规 | 描述 | 位置 |
|------|------|------|
| **表现层直连基础设施层** | `streamlit_app.py` 直接 `import sqlite3`, `import yfinance` | L10, L23 |
| **表现层包含业务逻辑** | `_compute_filters()`, `_compute_schmitt_trigger()` 等纯计算函数定义在 `streamlit_app.py` 中（虽然加了 `@st.cache_data` 装饰器） | L229-296 |
| **组件层跨层依赖** | `backtest_panel.py` 直接 `from db import get_conn` | L44 |
| **基础设施层反向引用** | `config_db.py` → `from db import init_db` | L652 |
| **缺少接口/抽象层** | 没有 Protocol/ABC 定义；各层通过具体导入交互 | 全局 |

### 8.3 分层评分

| 维度 | 评分 (1-10) | 说明 |
|------|:---:|------|
| 职责分离 | 5 | 上帝模块拖累；services 层良好 |
| 接口清晰度 | 3 | 无 Protocol/ABC；cfg dict 无类型 |
| 依赖方向 | 7 | 整体单向，无循环依赖 |
| 测试隔离性 | 6 | services 层可独立测试；UI 层难隔离 |
| 可替换性 | 6 | services 层可替换；持久层耦合 SQLite |

---

## 9. 可扩展性评估

### 9.1 添加新滤波算法

**难度：低** -- 只需在 `FILTERS` 注册表中添加条目，实现签名 `(signal, t, **params) -> np.ndarray` 的函数即可。sidebar 参数滑块、导出配置自动适配。

```python
# 仅需修改 filter_engine.py 一个文件
FILTERS["new_filter"] = {
    "name": "新滤波器",
    "func": apply_new_filter,
    "params": {"param1": ("参数1", 0, 100, 1, 10)},
}
```

### 9.2 添加新的图表子图

**难度：中** -- 涉及多个修改点：
1. `streamlit_app.py`: `_determine_subplot_layout()` 增加新行配置分支
2. `streamlit_app.py`: 新增 `_add_xxx_traces()` 函数
3. `components/charts.py`: 可能需要新渲染逻辑
4. `config_db.py`: 新配置开关需加入 `VIEW_PARAM_SPECS`
5. `state.py`: 新 bool/float 参数需加入 `VIEW_DEFAULTS`

### 9.3 添加新的数据源

**难度：高** -- 当前深度耦合 yfinance + SQLite：
- 数据格式依赖 yfinance 的特定列名
- `upsert_kline()` 假定 OHLCV 结构
- 8 周期映射硬编码在 `data_loader.py` 中
- Parquet 缓存路径硬编码

### 9.4 添加新的 UI 框架

**难度：极高** -- 1702 行的 `streamlit_app.py` 深度依赖 Streamlit 的 `@st.cache_data`、`@st.fragment`、`st.session_state` 等特性。迁移到 FastAPI/Flask 需要完全重写。

### 9.5 可扩展性评分

| 场景 | 难度 | 涉及文件数 | 风险 |
|------|:---:|:---:|------|
| 新滤波算法 | ★☆☆ | 1 | 无 |
| 新图表子图 | ★★☆ | 4-6 | 低 |
| 新参数/配置项 | ★★☆ | 3-4 | 中 (魔法字符串) |
| 新回测输出格式 | ★★☆ | 2-3 | 低 |
| 新数据源 | ★★★ | 3+ | 高 |
| 新 UI 框架 | ★★★★★ | 几乎全部 | 极高 |

---

## 10. 发现汇总与优先级排序

### 10.1 优先级排序

| 优先级 | # | 问题 | 影响 | 建议措施 |
|:---:|---|------|------|------|
| **P0** | 1 | `streamlit_app.py` 上帝模块 (1702行) | 维护困难、测试困难、新人理解成本高 | 拆分为 3-4 个模块：`chart_builder.py`(图表构建)、`sidebar_sections.py`(侧边栏)、`app.py`(编排) |
| **P0** | 2 | cfg dict 无类型约束 | 运行时拼写错误、重构风险高 | 定义 `ViewConfig` dataclass，替代裸 dict |
| **P1** | 3 | `ALL_TFS`/`TF_HIERARCHY` 重复定义 4 次 | 修改时需同步多处，易遗漏 | 提取到 `constants.py`，统一引用 |
| **P1** | 4 | Python/HTML 混合渲染 (200+ 行 JS) | 无法 lint、无法单元测试 | 将 JS 提取为独立 `.js` 文件，Python 侧只负责注入数据 |
| **P1** | 5 | 魔法字符串泛滥 | 重构时搜索替换不可靠 | 定义枚举或常量类：`Timeframe.DAILY` 替代 `"日线"` |
| **P1** | 6 | 回测三通道输出 (JSONL + Parquet + JSON) | 数据重复、维护三套序列化逻辑 | 评估是否可以统一为 Parquet 为主，JSON 按需导出 |
| **P2** | 7 | 表现层直连基础设施层 | 分层混乱，难以替换后端 | `streamlit_app.py` 应通过 services 层访问数据，而非直接 `import sqlite3` |
| **P2** | 8 | 双轨状态管理 (`AppState` + `st.session_state`) | 调试困难，`_imp_` 备份增加认知负担 | 统一为 `AppState` 单一入口，移除 `st.session_state` 直接访问 |
| **P2** | 9 | 空 `__init__.py` / 缺少公共 API | 外部不清楚模块的公开接口 | 在 `components/__init__.py` 中导出 `[render_chart, render_sidebar]` |
| **P3** | 10 | 硬编码文件路径 | 部署灵活性差 | 通过环境变量或配置文件注入路径 |
| **P3** | 11 | `config_db.py` → `db.py` 反向引用 | 依赖方向不直观 | 将 `init_db` 调用移到 `streamlit_app.main()` 中 |

### 10.2 架构健康度评分

| 维度 | 评分 (1-10) |
|------|:---:|
| 代码组织 | 7 |
| 模块耦合度 | 5 |
| 内聚性 | 6 |
| 分层清晰度 | 4 |
| 类型安全 | 3 |
| 可测试性 | 6 |
| 可扩展性 | 5 |
| 新人友好度 | 4 |
| **综合** | **5.0 / 10** |

### 10.3 亮点

1. **services/ 层设计良好** -- 5 个模块零项目内依赖，纯函数可独立测试和复用
2. **FILTERS 注册表** -- 优秀的插件式架构，添加新算法只需注册
3. **回测管道对齐** -- CLI 和 Web 使用相同的 services 层，复用度高
4. **测试覆盖完整** -- 623 个测试覆盖核心逻辑，CI 自动检查
5. **无循环依赖** -- 整体依赖方向单向，架构约束良好
6. **双入口设计** -- Web (交互式) + CLI (批量回测) 共享服务层

### 10.4 短期行动建议 (1-2 周)

```
1. 提取 constants.py，统一 ALL_TFS、TF_HIERARCHY 定义
2. 定义 ViewConfig dataclass，替代裸 cfg dict
3. 将 streamlit_app.py 中 200 行 JS 提取为独立 .js 文件
4. 在 components/__init__.py 中定义公共 API
```

### 10.5 中期重构建议 (1-3 月)

```
5. 拆分 streamlit_app.py 为 chart_builder.py + sidebar_sections.py
6. 定义 TimeFrame 枚举，替换所有魔法字符串
7. 引入 Repository 模式统一数据访问 (db.py + data_loader.py)
8. 评估统一回测输出格式 (Parquet 为主存储)
```

---

## 附录: 文件清单索引

### filter_app/ 源文件 (按职责分组)

**入口**
- `streamlit_app.py` (1702行) -- Web 入口
- `backtest_cli.py` (575行) -- CLI 入口
- `backtest_logger.py` (76行) -- 回测日志

**状态管理**
- `state.py` (313行) -- AppState, ViewState

**数据持久化**
- `db.py` (644行) -- market.db 操作
- `config_db.py` (665行) -- config.db 操作

**业务逻辑 (services/)**
- `filter_engine.py` (1051行) -- 10 种滤波 + 信号 + 策略
- `data_loader.py` (818行) -- 数据拉取与缓存
- `backtest_core.py` (1013行) -- 回测编排
- `bs_marker.py` (200行) -- BS 标记
- `event_recorder.py` (890行) -- 事件记录
- `parquet_store.py` (1022行) -- 列式存储
- `pipeline_capture.py` (299行) -- 调试捕获

**UI 组件 (components/)**
- `charts.py` (450行) -- Plotly 渲染
- `sidebar.py` (321行) -- 参数控件
- `backtest_panel.py` (502行) -- 回测面板

### 测试文件 (tests/)

- `test_parquet_store.py` (3087行) -- 最大测试文件
- `test_backtest.py` (1455行)
- `test_data_recording.py` (1390行)
- `test_html_overlay.py` (1127行)
- `test_config_db.py` (1052行)
- `test_db.py` (904行)
- 另有 26 个测试文件覆盖 UI、信号、策略、对齐、集成等

### 工具 (tools/)

- `filter_comparison_tool.py` (337行 -- 实际更多)
- `benchmark_pipeline.py` (22291 bytes)
- `analyze_captured_backtest.py` (28481 bytes)
- `view_backtest.py` (18125 bytes)
- `replay_bar.py` (8953 bytes)
- `trace_long_veto.py` (2813 bytes)
