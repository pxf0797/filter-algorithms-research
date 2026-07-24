# 统一回测+可视化方案设计

> 日期: 2026-07-24 | 状态: 提案

---

## 1. 现有架构分析

### 1.1 资产清单

| 组件 | 路径 | 规模 | 功能 |
|------|------|------|------|
| Streamlit 主应用 | `filter/browse/app.py` | 777 行 | 4 视图图表、参数配置、预设管理 |
| 侧边栏 | `filter/browse/sidebar.py` | ~750 行 | 市场/股票选择、数据校验、过滤参数、导出 |
| 回测面板 | `filter/backtest/panel.py` | 549 行 | 已嵌入侧边栏：浏览/回测模式切换、窗口导航、播放 |
| 回测引擎 | `filter/backtest/engine.py` | ~1100 行 | `BacktestRunner` — 零 Streamlit 依赖，逐 bar 管道计算 |
| 回测 CLI | `filter/backtest/cli.py` | ~500 行 | argparse CLI，调用 Runner + Recorder |
| 事件录制器 | `filter/backtest/recorder.py` | ~900 行 | `EventRecorder` + `CSVBuilder`，JSONL/CSV/Parquet 输出 |
| 回测指标 | `filter/backtest/metrics.py` | 139 行 | Sharpe/Sortino/Calmar/MaxDD/交易统计 |
| 回测目录 | `filter/backtest/catalog.py` | 62 行 | `BacktestCatalog` 扫描 `backtest_output/`，维护 `.catalog.json` |
| 旧 HTML 可视化 | `docs/backtesting/回测结果可视化.html` | 1951 行 | 独立 HTML + Plotly.js，文件拖拽上传，KPI + 多图表 |
| 回测输出 | `backtest_output/` | 9 个 session | 每个含 `metadata.json`、`backtest_result.parquet`、`events.jsonl` 等 |

### 1.2 现有数据流

```
[浏览模式]
  yfinance API → _sync_to_display() → Parquet → _load_chart_data() → 管道计算 → Plotly 图表

[回测模式(Streamlit)]
  SQLite DB → _sync_all_cascading() → 窗口化 Parquet → _load_chart_data(window_start, cutoff_date)
  → 管道计算 → Plotly 图表（逐 bar 播放）

[回测模式(CLI)]
  SQLite DB → BacktestRunner.run(start_bar, end_bar) → EventRecorder → backtest_output/{session}/
  → 输出: metadata.json, backtest_result.csv, events.jsonl, trade_summary.jsonl, ...
```

### 1.3 关键发现

1. **Streamlit 已有回测面板**：`render_backtest_panel()` 已在 `main()` 第 687 行调用，提供了模式切换、窗口导航、自动播放等功能。但它是**逐 bar 播放**模式（可视化管道中间状态），而非**完整运行 + 结果报告**模式。

2. **CLI 已产出结构化结果**：`backtest_output/` 下每个 session 有完整的 CSV/Parquet/JSONL 数据，包含信号、PnL、交易记录等。但 Streamlit 应用中**没有加载和展示这些结果的界面**。

3. **旧 HTML 已有完整的可视化设计**：1951 行的独立 HTML 包含 KPI 卡片、多维度图表（信号、PnL、热力图、交易明细表），但其数据加载依赖文件拖拽，且与 Streamlit 脱节。

4. **指标计算已独立**：`filter/backtest/metrics.py` 的 `compute_backtest_metrics()` 可直接复用。

---

## 2. 三种方案对比

### 方案 A: Streamlit 增强（推荐）

在现有 Streamlit 应用侧边栏添加"回测运行"面板和"结果仪表盘"主视图。

**架构**：
```
Streamlit 主页面
├── 侧边栏（现有 + 新增）
│   ├── [现有] 市场/股票/预设/参数/数据校验
│   ├── [现有] 回测模式切换 + 窗口导航 ← 保留（逐 bar 播放）
│   └── [新增] 回测运行面板
│       ├── bar 范围选择（start/end）
│       ├── "运行回测"按钮 → 调用 BacktestRunner + EventRecorder
│       ├── 运行进度条
│       └── 历史回测列表（从 BacktestCatalog 加载）
└── 主区域
    ├── [现有] 4 视图图表网格（浏览/回测模式）
    └── [新增] 结果仪表盘（运行完成后自动展示）
        ├── KPI 指标卡片行（总收益/夏普/最大回撤/胜率/…）
        ├── PnL 曲线图（Plotly, 含多空分开）
        ├── 回撤曲线图
        ├── 交易分布图（按视图的盈亏分布）
        ├── 交易明细表（可排序/筛选的 dataframe）
        └── 导出按钮（HTML 报告下载）
```

| 维度 | 评价 |
|------|------|
| **优点** | 统一入口，零切换成本；复用现有的 Plotly 渲染基础设施、预设系统、数据加载管线；指标计算直接复用 `metrics.py`；`BacktestRunner` 和 `EventRecorder` 可直接调用无需修改 |
| **缺点** | 长时间回测需处理 Streamlit 的超时和同步问题（可用 `st.spinner` + 后台线程）；dashboard 在大量 bar 时可能有渲染性能压力 |
| **工作量** | 中等（3-5 天）。新增 ~500 行 Python（回测运行面板 + dashboard 组件），不修改现有核心逻辑 |
| **风险** | 低。改动完全增量，不触及现有图表渲染和数据流 |

### 方案 B: 修复旧 HTML + iframe 嵌入

修复 `docs/backtesting/回测结果可视化.html` 的数据加载逻辑，使其能从 `backtest_output/` 目录或 API 端点读取数据，然后在 Streamlit 中通过 `st.components.v1.html` 嵌入。

| 维度 | 评价 |
|------|------|
| **优点** | 保留已有的 1951 行可视化代码和设计；Plotly.js 图表效果不变 |
| **缺点** | 需要维护两套代码（Streamlit 中 Python + 独立 HTML/JS）；iframe 通信复杂（需通过 `postMessage` 或静态文件服务）；HTML 的数据加载逻辑需重写（目前是文件拖拽，需改为 fetch JSON）；Streamlit 与 iframe 内 JS 的交互有延迟和限制 |
| **工作量** | 较大（5-7 天）。HTML 需要大幅重构数据加载部分，增加静态文件服务端点，处理跨域和消息通信 |
| **风险** | 中。iframe 嵌入方案在 Streamlit 中常有渲染尺寸、滚动、事件传递等问题。长期维护两套代码增加技术债务 |

### 方案 C: CLI + 自包含 HTML 报告

CLI 执行回测完成后，额外生成一个自包含的 HTML 报告文件（内嵌 Plotly.js + 所有数据），浏览器直接打开。

| 维度 | 评价 |
|------|------|
| **优点** | 零服务依赖，报告可分享/归档；CLI 已有成熟能力；适合批量回测场景 |
| **缺点** | 与 Streamlit 完全脱节，无法利用现有参数配置 UI；每次运行需离开 Streamlit；无法交互式筛选和对比多次回测结果 |
| **工作量** | 小（1-2 天）。基于旧 HTML 模板，用 Jinja2 注入数据生成自包含 HTML |
| **风险** | 低。但这是**补充方案**而非统一方案——用户仍需在两个工具间切换 |

### 对比总结

| 维度 | 方案 A (Streamlit 增强) | 方案 B (修复 HTML + iframe) | 方案 C (CLI + HTML 报告) |
|------|------------------------|---------------------------|------------------------|
| 统一入口 | 是 | 是（但割裂） | 否 |
| 代码复用 | 高（复用 metrics/charts/engine） | 中（HTML JS 独立维护） | 中（复用 recorder 输出格式） |
| 交互性 | 高（Streamlit 原生交互） | 中（iframe 通信限制） | 低（静态页面） |
| 开发工作量 | 3-5 天 | 5-7 天 | 1-2 天 |
| 维护成本 | 低 | 高（双代码库） | 低 |
| 可分享性 | 需 Streamlit 运行 | 需 Streamlit 运行 | 高（独立 HTML 文件） |
| 适合场景 | **日常研究 + 交互探索** | 需要复杂 JS 图表时 | **批量回测 + 归档分享** |

---

## 3. 推荐方案: A + C 组合

**主方案 A**：在 Streamlit 中增加"运行回测 + 结果仪表盘"，覆盖日常交互式研究场景。

**辅方案 C**：CLI 增加 `--report` 选项生成自包含 HTML 报告，覆盖批量回测和分享场景。

两者共享数据格式（`backtest_output/` 下的 Parquet/JSON），指标计算逻辑统一使用 `filter/backtest/metrics.py`。

---

## 4. 方案 A 详细架构

### 4.1 新增文件

```
filter/backtest/
├── engine.py          # [现有] BacktestRunner
├── recorder.py        # [现有] EventRecorder
├── metrics.py         # [现有] compute_backtest_metrics()
├── catalog.py         # [现有] BacktestCatalog
├── cli.py             # [现有] CLI 入口
├── panel.py           # [现有] 回测面板（保留不变）
├── runner_panel.py    # [新增] 回测运行面板（侧边栏组件）
└── dashboard.py       # [新增] 回测结果仪表盘（主区域组件）

filter/browse/
└── app.py             # [修改] main() 中增加仪表盘渲染调用
```

### 4.2 组件设计

#### `runner_panel.py` — 回测运行面板

```
render_backtest_runner(market, ticker_code, configs) -> None

侧边栏 UI:
├── "历史回测" 折叠面板
│   ├── session 列表（从 BacktestCatalog.scan() 读取）
│   ├── 点击 session → 加载到仪表盘显示
│   └── "删除" 按钮
├── "运行新回测" 折叠面板
│   ├── bar 范围输入（start_bar / end_bar, 默认 50 / 总 bar 数）
│   ├── 步进间隔（默认 1）
│   ├── "▶ 运行回测" 按钮
│   └── 运行中：进度条 + 已用时/预计剩余
└── "导出" 按钮 → 生成 HTML 报告下载
```

#### `dashboard.py` — 结果仪表盘

```
render_backtest_dashboard(session_data: dict) -> None

主区域 UI（替代或并列于 4 视图网格）:
├── 摘要行（ticker, 时间范围, bar 数, session ID）
├── KPI 卡片行（6 列）
│   ├── 总收益率 | 年化收益率
│   ├── 夏普比率 | 索提诺比率
│   ├── 最大回撤 | 卡玛比率
│   ├── 胜率 | 盈亏比
│   ├── 总交易笔数 | 平均持仓
│   └── 多头收益 | 空头收益
├── Tab 1: 概览
│   ├── PnL 曲线（Plotly, 多视图叠加, 多空分色）
│   ├── 回撤曲线
│   └── 参数摘要表
├── Tab 2: 信号分析
│   ├── BS 标记时间线（各视图）
│   ├── 信号分布（多/空/观望占比）
│   └── 持仓重叠热力图
├── Tab 3: 交易明细
│   ├── 可排序/筛选的 st.dataframe
│   └── 按视图/方向筛选
└── Tab 4: 逐视图详情
    └── 每个视图独立的 PnL + 信号 + 交易子面板
```

### 4.3 数据流

```
用户点击"运行回测"
  → runner_panel._run_backtest()
    → BacktestRunner(ticker, configs).run(start, end, step_interval)
    → EventRecorder 记录事件/快照/交易
    → 写入 backtest_output/{ticker}_{timestamp}/
  → 读取 backtest_result.parquet
    → compute_backtest_metrics()
    → 存入 st.session_state["_bt_dashboard_data"]
  → st.rerun() → dashboard.py 渲染仪表盘

用户点击历史 session
  → runner_panel._load_session(session_path)
    → 读取 backtest_result.parquet
    → compute_backtest_metrics()
    → 存入 st.session_state["_bt_dashboard_data"]
  → st.rerun() → 仪表盘渲染
```

### 4.4 关键设计决策

1. **数据载体用 Parquet**：`backtest_result.parquet` 已是现有输出格式，列包含所有视图的信号/PnL。用 `pd.read_parquet()` 加载，比 CSV 快 5-10x。

2. **指标实时计算**：不预存指标到文件，而是在加载时调用 `compute_backtest_metrics()`。这确保指标算法更新后历史数据也能用到新公式。

3. **不替代现有回测面板**：现有的逐 bar 播放模式（`panel.py`）保留不变，与新的"运行完整回测"功能并行。用户可选择：
   - **逐 bar 播放**：观察管道中间状态，调试参数
   - **完整运行**：快速得到统计结果，对比多次回测

4. **仪表盘与图表网格互斥**：在 `main()` 中通过 `st.session_state` 标志位控制显示图表网格还是仪表盘，避免页面过长。

5. **进度反馈用 Streamlit 原生组件**：`st.progress()` + `st.status()` 显示回测进度，通过 session_state 在运行线程和 UI 线程间传递进度。

---

## 5. 方案 C 补充设计

在 `filter/backtest/cli.py` 中增加 `--report` 选项：

```bash
python -m filter.backtest.cli --ticker AAPL --preset AAPL_US --report
# → 输出: backtest_output/AAPL_xxx/ + backtest_output/AAPL_xxx/report.html
```

报告使用 Jinja2 模板渲染自包含 HTML（内嵌 Plotly.js CDN + 内联 JSON 数据），包含与方案 A 仪表盘相同的 KPI 卡片和图表。

---

## 6. 实施步骤

### Phase 1: 基础设施（1 天）

| 步骤 | 内容 | 验证 |
|------|------|------|
| 1.1 | 创建 `filter/backtest/dashboard.py` 骨架，实现 `compute_backtest_metrics()` 的 Streamlit 渲染包装 | 导入不报错 |
| 1.2 | 创建 `filter/backtest/runner_panel.py` 骨架，实现 `render_backtest_runner()` 占位函数 | 导入不报错 |
| 1.3 | 在 `filter/backtest/__init__.py` 中导出新模块 | pytest test_module_structure 通过 |

### Phase 2: 核心功能（2 天）

| 步骤 | 内容 | 验证 |
|------|------|------|
| 2.1 | `runner_panel.py`：实现历史 session 列表（从 `BacktestCatalog` 读取），点击加载到 session_state | 手动测试：点击 session 后 `_bt_dashboard_data` 非空 |
| 2.2 | `runner_panel.py`：实现"运行回测"按钮，调用 `BacktestRunner` + `EventRecorder`，进度条 | 手动测试：对 AAPL 运行 50-150 bar 范围，进度条正常，输出目录生成 |
| 2.3 | `dashboard.py`：实现 KPI 卡片行，从 `backtest_result.parquet` 加载 + `compute_backtest_metrics()` 计算 | 手动测试：加载已有 session 后 6 个 KPI 卡片正确显示 |
| 2.4 | `dashboard.py`：实现 PnL 曲线图（Plotly），含多空分色 | 手动测试：图表渲染正确，hover 显示数值 |
| 2.5 | `dashboard.py`：实现交易明细表（`st.dataframe`，可排序/筛选） | 手动测试：表格可排序，筛选按视图/方向生效 |

### Phase 3: 集成与打磨（1-2 天）

| 步骤 | 内容 | 验证 |
|------|------|------|
| 3.1 | `app.py`：在 `main()` 中集成仪表盘渲染，通过标志位切换图表网格/仪表盘 | 手动测试：运行回测后自动切换到仪表盘，点击"返回图表"切换回 |
| 3.2 | `dashboard.py`：增加 Tab 页（概览/信号/交易/逐视图），回撤曲线图 | 手动测试：4 个 Tab 切换正常 |
| 3.3 | `runner_panel.py`：增加"导出 HTML 报告"按钮（调用方案 C 的 Jinja2 模板） | 手动测试：点击下载按钮生成 HTML，浏览器打开正常 |
| 3.4 | `cli.py`：增加 `--report` 选项，复用同一 Jinja2 模板 | 手动测试：`python -m filter.backtest.cli --ticker AAPL --preset AAPL_US --report` 生成 report.html |
| 3.5 | 端到端测试：浏览 AAPL → 切换回测模式 → 运行回测 → 查看仪表盘 → 导出报告 | `pytest tests/test_backtest_cli.py` + 手动验收 |

---

## 7. 文件变更清单

### 新增文件

| 文件 | 说明 | 预估行数 |
|------|------|----------|
| `filter/backtest/runner_panel.py` | 回测运行面板（侧边栏组件） | ~250 行 |
| `filter/backtest/dashboard.py` | 回测结果仪表盘（主区域组件） | ~400 行 |
| `filter/backtest/templates/report.html.j2` | HTML 报告 Jinja2 模板 | ~300 行 |

### 修改文件

| 文件 | 变更 | 影响范围 |
|------|------|----------|
| `filter/browse/app.py` | `main()` 中增加仪表盘渲染逻辑（~30 行）；在侧边栏调用 `render_backtest_runner()`（~3 行） | 仅 main() 函数 |
| `filter/backtest/__init__.py` | 导出 `runner_panel` 和 `dashboard` | 2 行 |
| `filter/backtest/cli.py` | 增加 `--report` 选项和相关逻辑 | ~50 行 |
| `filter/backtest/catalog.py` | 增加 `get_session_by_name()` 便捷方法（可选） | ~10 行 |

### 不变文件

| 文件 | 原因 |
|------|------|
| `filter/backtest/engine.py` | `BacktestRunner` 接口已满足需求 |
| `filter/backtest/recorder.py` | `EventRecorder` 输出格式已满足需求 |
| `filter/backtest/metrics.py` | `compute_backtest_metrics()` 直接复用 |
| `filter/backtest/panel.py` | 现有逐 bar 播放模式保留不动 |
| `filter/browse/charts.py` | 图表渲染不修改 |
| `filter/browse/sidebar.py` | 侧边栏不修改（运行面板是独立新组件） |

---

## 8. 待决议题

1. **长时间回测的体验**：如果回测超过 60 秒，Streamlit 默认会超时。建议将运行放到后台线程（`threading.Thread`），通过 `st.session_state` 轮询进度。是否需要支持运行中取消？

2. **仪表盘 vs 图表网格的切换 UX**：建议用 `st.radio` 或 Tab 切换（"图表" / "仪表盘"），还是运行完成后自动弹出 overlay/dialog？

3. **方案 C 的优先级**：是否需要在 Phase 1 就实现 `--report`，还是等方案 A 稳定后再补？
