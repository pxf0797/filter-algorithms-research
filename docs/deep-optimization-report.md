# 深度优化分析报告

**报告日期**: 2026-07-26
**项目**: filter_research (股票滤波器研究与回测系统)
**分析来源**: 6 份专项深度分析报告 (浏览模式 / 回测模式 / CLI数据管线 / 回测展示 / 跨领域架构 / 工程精简)
**分析范围**: 约 15,000 行 Python 代码, 60+ 模块, 600+ 测试用例

---

## 执行摘要

通过对 6 个维度的专项分析，共识别出 **~95 个优化点**。经过去重合并后，按投入产出比（ROI）排序产生 Top 15 优先项。核心发现：

- **最大性能瓶颈**: 浏览模式的 Plotly JSON 内嵌 (HTML payload >1MB) 和 Dashboard 的 `go.Scatter`(SVG) + `iterrows()` 三大问题
- **最大正确性风险**: 回测指标仅取最后一步第一个视图 (可能导致 Sharpe/回撤完全错误) 和断点恢复 `bar_index` 硬编码为 0
- **最大架构债务**: browse/backtest 循环依赖、filters.py God Module (1196 行)、两个 pipeline.py 同名异义
- **最大精简机会**: ~986 行可立即删除的死代码 + 28 个未使用的依赖包

### Top 15 优化项 (按 ROI 排序)

| 排名 | 优化项 | 来源 | 影响范围 | 预估工期 | 风险 | ROI 等级 | 类型 |
|------|--------|------|---------|---------|------|---------|------|
| 1 | Dashboard `go.Scatter` -> `go.Scattergl` | 展示 #2 | 回测图表渲染 | 0.5天 | 低 | 极高 | 性能 |
| 2 | `iterrows()` -> 向量化 | 展示 #1 / 回测 #22 | 交易记录提取 | 0.5天 | 低 | 极高 | 性能 |
| 3 | `_compute_version` 用 `pq.ParquetFile.metadata.num_rows` | CLI #4 | 缓存校验 | 0.2天 | 低 | 极高 | 性能 |
| 4 | 删除死代码 (~986 行 + 28 个未使用依赖) | 精简 S1 | 全局代码库 | 2天 | 低 | 极高 | 工程 |
| 5 | 添加图表导出CSV/PNG功能 (Plotly modebar + download_button) | 展示 #3 | 分析师工作流 | 0.5天 | 低 | 极高 | 功能 |
| 6 | yfinance 添加超时/重试机制 | CLI #1 | 数据下载可靠性 | 0.3天 | 低 | 极高 | 可靠性 |
| 7 | metrics 取最后一步导致指标错误 (正确性修复) | 回测 #6 | 回测结果准确性 | 1天 | 低 | 高 | 正确性 |
| 8 | Plotly JSON payload 减少 50-70% (共享CDN + 差异化traces) | 浏览 H-1 | 页面渲染速度 | 3天 | 中 | 高 | 性能 |
| 9 | `_render_chart` 上帝函数拆分 (248行 -> 3个函数) | 浏览 H-2 | 可测试性/维护性 | 3天 | 中 | 高 | 架构 |
| 10 | 消除 browse/backtest 循环依赖 (ALL_TFS 迁移到 constants) | 跨领域 P0-1 | 模块架构 | 1天 | 低 | 高 | 架构 |
| 11 | 拆分 filters.py God Module (1196行 -> 4个模块) | 跨领域 P0-3 | 核心引擎 | 5天 | 中 | 高 | 架构 |
| 12 | 回测 numba 加速热点路径 (20-40% 总时间减少) | 回测 #13 | 回测引擎性能 | 5天 | 高 | 高 | 性能 |
| 13 | 并行化数据校验 yfinance 调用 (8次 -> 并行) | 浏览 H-4 | 浏览模式启动 | 0.5天 | 低 | 高 | 性能 |
| 14 | 创建 `filter/constants/colors.py` 集中颜色管理 | 展示 #4 / 浏览 | 视觉一致性 | 1天 | 低 | 中 | 维护性 |
| 15 | CSV 导出 float32->float64 内存翻倍修复 | CLI #11 | 导出内存 | 0.2天 | 低 | 高 | 性能 |

---

## 1. 浏览模式优化

### 模块评分总览

| 维度 | 评分 | 关键问题 |
|------|------|---------|
| 性能 | 5.5/10 | Plotly JSON 内嵌大、冗余序列化、静态资源未利用 CDN |
| 架构 | 6.0/10 | `_render_chart`(248行) / `_render_params`(216行) 是上帝函数 |
| 可维护性 | 6.5/10 | 魔法数字密集、session_state 查询冗余 |
| 错误处理 | 7.0/10 | 防御性检查覆盖关键路径 |
| Streamlit 实践 | 7.5/10 | fragment/cache_data/on_click 使用得当 |

**综合评级: C+ (良好，存在显著优化空间)**

### 关键优化项

#### [H-1] Plotly JSON 内嵌导致页面膨胀 (HTML payload >1MB)
- **文件**: `filter/browse/charts.py:34-104`
- **问题**: 4 视图各嵌入完整 Plotly figure JSON + Plotly.js CDN，总计 1-2MB HTML
- **建议**: 使用 `include_plotlyjs=False` + 单次 CDN 加载，共享 layout 模板
- **收益**: 页面传输减少 50-70%, 首屏渲染加快 0.5-1.5s
- **工期**: 3天 | **风险**: 中

#### [H-2] `_render_chart` 函数过长 (248行)
- **文件**: `filter/browse/app.py:299-547`
- **问题**: 混合了数据加载/日期标记/PnL对齐/滤波器计算/施密特/预测/策略PnL/子图布局
- **建议**: 提取 `_prepare_chart_data()` + `_build_chart_figure()` 两个独立函数
- **收益**: 可测试性大幅提升, 代码审查速度提升 3X
- **工期**: 3天 | **风险**: 中

#### [H-3] `_add_schmitt_traces` 循环创建大量小 trace 对象
- **文件**: `filter/browse/chart_builder.py:208-218`
- **问题**: 每对 B/S 信号创建一个 band trace (10-50对), 增加 trace 数量和 JSON 序列化开销
- **建议**: 合并为单个 trace 使用 mask 数组, 或限制最多显示最近 20 对
- **收益**: JSON 序列化减少 10-25%, Plotly 渲染加快 5-15%
- **工期**: 0.5天 | **风险**: 低

#### [H-4] 数据校验顺序调用 yfinance API 8 次
- **文件**: `filter/browse/sidebar.py:307-318`
- **问题**: 8 个周期依次调用 `yf.download()`, 总阻塞 8-24s
- **建议**: `concurrent.futures.ThreadPoolExecutor` 并行请求
- **收益**: 数据校验速度 3-6X (15-30s -> 3-5s)
- **工期**: 0.5天 | **风险**: 低

### 其余优化项 (简要列表)
- [M-1] `_cached_strategy_pnl` JSON 往返序列化 -> 使用 `pickle.dumps` 或 `frozenset`
- [M-2] `charts.js` 在 4 视图中重复嵌入 -> 合并为单 figure 或 minify
- [M-3] `_render_params` 末尾冗余 `session_state.get` -> 提取辅助函数, 减少 40 行代码
- [M-4] `_date_markers` 无缓存 + 未使用变量 -> 添加 `@st.cache_data`
- [M-5] DB 连接双层缓存冗余 -> 合并 `_cached_conn` + `_get_db_connection`
- [L-1] preset_selector hash-based key 不稳定 -> 固定 key + on_change 回调
- [L-2] OHLC `.ravel()` 语义不明确 -> 替换为 `.to_numpy()`
- [L-3] PnL traces 与 charts.py 标记代码重复 -> 复用共享函数
- [L-4] `_cached_fetch_stock` TTL 固定 3600s -> 按周期调整 (分钟级 60s, 日线 600s)

---

## 2. 回测模式优化

### 模块评分总览

| 维度 | 评分 | 关键问题 |
|------|------|---------|
| 性能 | 6.5/10 | 逐 bar 冗余排序/parquet I/O、numba 零覆盖 |
| 正确性 | 7.5/10 | 指标仅取最后一步第一个视图、断点恢复 bar_index 硬编码 |
| 可维护性 | 7.0/10 | recorder.py 与 store.py 存在 ~90% 重复的数据提取逻辑 |

**综合评级: B- (良好，存在数个关键正确性风险)**

### 关键优化项

#### [H-5] 自动指标计算仅取最后一步的第一个视图 (正确性风险)
- **文件**: `filter/backtest/engine.py:1062-1113`
- **问题**: `_compute_and_log_metrics` 只使用 `results[-1]` 和 `next(iter(views.values()))`, 跨视图聚合和工作步骤 PnL 时序被忽略。输出的 Sharpe/回撤/胜率可能完全错误。
- **建议**: 从所有 steps 重建完整时序 PnL 曲线
- **收益**: 正确性修复 (当前指标可能不可靠)
- **工期**: 1天 | **风险**: 低 | **优先级: 最紧急**

#### [H-6] `save_checkpoint` bar_index 硬编码为 0 (断点恢复 bug)
- **文件**: `filter/backtest/engine.py:334`
- **问题**: 写入 `"bar_index": 0` 并标注"caller tracks this", 若 client 未正确覆盖则从 bar 0 重跑
- **建议**: `save_checkpoint` 接受 `bar_index: int` 参数并正确序列化
- **收益**: 断点恢复正确性修复
- **工期**: 0.2天 | **风险**: 低

#### [H-7] numba 零覆盖 -- 回测热点路径全在纯 Python/NumPy 运行
- **文件**: 全 `filter/backtest/` 目录
- **问题**: `_extract_view_columns`、交易匹配循环、连续水下计算等热点路径均未使用 numba。Numba 仅在 `engine/filters.py` 中的 `_kalman_core_loop` 和 `_schmitt_trigger_core` 使用
- **建议**: 为 `_extract_view_columns` 和 `compute_backtest_metrics` 中 `groupby` 连续水下计算添加 `@njit` 版本
- **收益**: 10 万 bar 回测总时间减少 20-40%
- **工期**: 5天 | **风险**: 高 (需重构数据结构为 numba 兼容)

### 其余优化项 (简要列表)
- [H] 逐 bar 文件存在性检查 -> 预检查并缓存 `Path(...).exists()` 结果 (engine.py:545)
- [H] 逐 bar 重复 `set_index().sort_index()` -> 缓存 parquet 读取结果 (engine.py:553)
- [H] 逐 bar 重复排序视图配置 -> `__init__` 预计算 `_sorted_view_indices` (engine.py:191)
- [H] 管道缓存 MD5 哈希开销 -> 使用 Python `hash()` 或尾部 3 点作为缓存键 (engine.py:628)
- [H] 并行模式禁用了 pipeline_cache 但无收益 -> 方案 A: 移除并行恢复缓存 (engine.py:198)
- [M] `np.where` 魔数 100.0 -> 提取模块常量 `_PNL_BASE` (metrics.py:51)
- [M] Calmar ratio 回撤为 0 返回 0.0 -> 改为 `float("inf")` (metrics.py:82)
- [M] CSVBuilder 全量内存累积 (100-300MB) -> 用 DataFrame 追加或分段写入 (recorder.py:39)
- [M] `_buffer_to_table()` 列式 pivot 开销 -> 预分配 numpy 数组 (store.py:682)
- [M] 同步 parquet/json I/O 阻塞回测主循环 -> ThreadPoolExecutor 后台写入 (pipeline.py:246)
- [M] `query()` 忽略 `date_from`/`min_sharpe` 参数 -> 实现或添加 warning (catalog.py:50)
- [M] optuna 优化无进度回调 -> 添加 `optuna.logging.set_verbosity()` (optimizer.py:75)
- [M] recorder 和 store 双重写入 (90% 重复逻辑) -> 预提取标量值统一传递 (cli.py:548)
- [L] `_load_all_bar_info` 超过 LIMIT 静默截断 -> 添加 warning (engine.py:433)
- [L] 播放模式逐 bar OFFSET SQL 查询 -> 预加载日期列表 (panel.py:26)
- [L] snappy -> zstd 压缩统一 (pipeline.py:293)
- [L] `BACKTEST_PARALLEL_VIEWS` 环境变量无文档 -> 添加注释 (engine.py:198)
- [L] `dashboard.py` 使用 `df.iterrows()` -> 向量化 (已合并至展示 #1)

---

## 3. CLI 数据获取优化

### 模块评分总览

| 维度 | 评分 | 关键问题 |
|------|------|---------|
| I/O 效率 | 6/10 | 版本校验反序列化全文件、连接无复用、缓存策略保守 |
| 数据质量 | 8/10 | 健康检查完善、checksum 版本校验、schema 验证严谨 |
| CLI 体验 | 6/10 | filter_comparison_tool 无参数、大数据嵌入体验差 |

**综合评分: 6.7/10 (基础扎实，存在显著效率瓶颈)**

### 关键优化项

#### [H-8] yfinance 下载缺少重试和超时机制
- **文件**: `filter/data/fetcher.py:163-167`
- **问题**: `yf.download()` 无 `timeout` 和 retry 逻辑，单次网络波动直接导致失败
- **建议**: 引入 tenacity 或手动指数退避重试 (3次, 1s/2s/4s)
- **收益**: 网络抖动场景成功率 85% -> 99%
- **工期**: 0.3天 | **风险**: 低

#### [H-9] `_compute_version` 全量读取 Parquet 文件获取行数
- **文件**: `filter/data/loader.py:163-178`
- **问题**: `pd.read_parquet()` 反序列化完整文件 (50MB+ 耗时数秒), 仅为了获取行数
- **建议**: `pq.ParquetFile(path).metadata.num_rows` (仅读元数据, O(1))
- **收益**: 版本校验时间从秒级降到毫秒级, I/O 减少 99%+
- **工期**: 0.2天 | **风险**: 低 | **最简单且收益最高的单项修改**

#### [H-10] 内存缓冲区无上限导致 OOM 风险
- **文件**: `filter/data/store.py:197`
- **问题**: `_buffer_size=10_000_000` (近乎无限), Python dict overhead 远超估算
- **建议**: 降低 `_MAX_BUFFER_MB` 到 200MB, 使用 `sys.getsizeof(row)` 累加, 考虑 `pyarrow.RecordBatch`
- **收益**: 避免大回测 OOM 崩溃
- **工期**: 1天 | **风险**: 中

### 其余优化项 (简要列表)
- [H] 每次操作创建新 SQLite 连接 -> 线程本地连接池 (db.py:19)
- [H] 周线回退增加额外网络调用 -> 缓存周线数据 (fetcher.py:177)
- [H] 级联合成中重复 DB 查询 -> 合并时区+数据查询 (synth.py:375)
- [M] ThreadPoolExecutor 线程数硬编码 8 -> 基于 `os.cpu_count()` 或可配置 (fetcher.py:76)
- [M] 时间戳 TEXT 存储查询效率低 -> 考虑 Unix 时间戳 INTEGER (db.py:54)
- [M] view_backtest 大数据嵌入 HTML 膨胀 -> HTTP serve 模式 + 自动切换 (view_backtest.py:375)
- [M] filter_comparison_tool 无 CLI 参数 -> 添加 argparse (filter_comparison_tool.py:807)
- [M] ConfigDB 与 Streamlit 强耦合 -> `collect_current_params` 接受 dict 参数 (config_db.py:626)
- [L] `_resolve_read_path` 无缓存 -> TTL 缓存扫描结果 (loader.py:88)
- [L] 健康检查 O(n*m) 查询 -> GROUP BY 聚合查询 (db.py:300)
- [L] benchmark_pipeline 缺少结果持久化 -> 自动保存 + `--compare` 模式 (benchmark_pipeline.py:465)

---

## 4. 回测展示优化

### 模块评分总览

| 维度 | 评分 | 关键问题 |
|------|------|---------|
| 可视化性能 | 4/10 | `iterrows` + `go.Scatter`(SVG) 是两大性能瓶颈 |
| 交互体验 | 5/10 | 图表缺少缩放/平移、无导出、每次导航全页 rerun |
| 信息完整性 | 7/10 | 12/16 指标展示, 盈亏比缺失 |

**综合评级: C- (存在亟待解决的性能问题和重大 UX 缺口)**

### 关键优化项

#### [H-11] Dashboard 使用 `go.Scatter`(SVG) 而非 `scattergl`(WebGL)
- **文件**: `filter/backtest/dashboard.py:241,251,263,280`
- **问题**: PnL 曲线和回撤图全部使用 SVG 渲染。>1000 条 bar 时严重卡顿。chart_builder.py 已全部使用 `scattergl`
- **建议**: 全局替换 `go.Scatter` -> `go.Scattergl` (需验证 `fill="tozeroy"` 兼容性)
- **收益**: 1000 bar: 10-30x 加速; 5000 bar: 从数秒卡顿 -> 即时渲染
- **工期**: 0.5天 | **风险**: 低

#### [H-12] `iterrows()` 逐行遍历交易记录
- **文件**: `filter/backtest/dashboard.py:129`
- **问题**: `_extract_trade_records` 使用 pandas 最慢行遍历方式, 10000 bar 约 2-3 秒
- **建议**: 向量化 mask + `df.loc[mask].to_dict("records")`
- **收益**: 10-50x 加速
- **工期**: 0.5天 | **风险**: 低

#### [H-13] 缺少导出功能 (CSV/PNG/PDF)
- **文件**: `filter/backtest/dashboard.py`, `tools/view_backtest.py`
- **问题**: 图表无法导出 PNG, 交易明细无法下载 CSV, 无指标汇总报告导出
- **建议**: Plotly `config={'displayModeBar': True}` + `st.download_button` 下载 CSV
- **收益**: 分析师工作流效率直接提升
- **工期**: 0.5天 | **风险**: 低

### 其余优化项 (简要列表)
- [M] 颜色方案硬编码散落三处 -> 创建 `filter/constants/colors.py` (dashboard/chart_builder/view_backtest)
- [M] KPI 指标缺少信息分层 -> 分组 (收益/风险/效率) + delta 参数 + tooltip
- [M] 交易明细表无分页/排序/筛选 -> `st.dataframe` 的 `column_config` 参数
- [M] PnL 图缺少交互控制和 tooltip -> 添加 `dragmode`, `scrollZoom`, `uirevision`
- [M] 4 个未展示指标 (`avg_win_pct`, `avg_loss_pct`, `winning_trades`, `losing_trades`) -> 补充展示
- [M] `df_to_columns` 全量逐值序列化 -> `df.to_dict(orient='list')` + API 分页 (view_backtest.py:58)
- [L] 导航按钮每次 `st.rerun()` 全页刷新 -> `st.fragment` 隔离 (panel.py:210)
- [L] 无响应式适配 -> 调整 column 数量和 gap
- [L] 红绿色盲可访问性 -> 双编码 (颜色+线型+符号) + 色盲配色模式
- [L] Dashboard 与 chart_builder PnL 代码重复 -> 提取公共模块 `filter/common/pnl_renderer.py`

---

## 5. 跨领域与架构优化

### 工程健康度评分

| 维度 | 评分 | 关键问题 |
|------|------|---------|
| 架构 | 6/10 | 循环依赖 + God Module + 两个 pipeline.py 命名混淆 |
| 测试 | 5/10 | 覆盖率阈值仅 55%, test_app_ui.py 耗时 100s 拖慢 CI |
| 配置 | 5/10 | ViewConfig 双接口过渡期, .env 含未使用变量 |
| 安全 | 4/10 | 无 SAST、无依赖漏洞扫描、requirements.lock 膨胀增加攻击面 |

**综合评分: 5.0/10 (架构基础合理，但存在多个结构性债务)**

### 关键优化项

#### [H-14] browse/backtest 循环依赖 (架构倒置)
- **文件**: `filter/backtest/panel.py:19`, `filter/backtest/dashboard.py`, `filter/browse/app.py:64-67`
- **问题**: backtest 层导入 browse 层 (`from filter.browse.components_sidebar import ALL_TFS`), 而 browse 同时导入 backtest。注释已承认问题但代码未迁移
- **建议**: 将 `ALL_TFS` 导入源切换到 `filter.shared.constants` (已有定义)
- **收益**: 消除循环依赖, 架构清晰化
- **工期**: 1天 | **风险**: 低

#### [H-15] 拆分 filters.py God Module (1196行)
- **文件**: `filter/engine/filters.py`
- **问题**: 单一文件包含 10 个滤波算法、FILTERS 注册表、Kalman numba core、Schmitt trigger、信号配对、抛物线拟合、策略 PnL、跨周期对齐、持仓掩码、质量指标
- **建议**: 拆分为 `filter/engine/filters.py` (滤波算法) + `schmitt.py` + `strategy.py` + `alignment.py`
- **收益**: 可维护性大幅提升, 代码审查和测试聚焦化
- **工期**: 5天 | **风险**: 中

#### [H-16] 两个 pipeline.py 同名异义
- **文件**: `filter/engine/pipeline.py` (158行, 计算管线) vs `filter/backtest/pipeline.py` (299行, 调试捕获)
- **问题**: 同名模块做完全不同的事, 容易混淆
- **建议**: 重命名 `backtest/pipeline.py` -> `backtest/capture.py`
- **收益**: 消除命名歧义
- **工期**: 0.5天 | **风险**: 低

### 其余优化项 (简要列表)
- [P0] 合并 TF_LOWER 到 constants.py, 从 TF_HIERARCHY 推导
- [P0] 移除 ViewConfig.__getitem__/get() 或添加 DeprecationWarning
- [P1] 统一日志系统 — 合并 backtest/logger.py 至 shared/logger.py
- [P1] 抽象 state.py 存储层 — 解耦 Streamlit
- [P1] CI 覆盖率阈值从 55% 逐步提升至 65% -> 80%
- [P2] 移除 repository.py 过度抽象 (生产代码已完全绕过)
- [P2] 配置管理分散 — 统一 .env 加载
- [P2] 移除 `filter/__init__.py` 的 sys.path hack
- [P3] pre-commit mypy 版本对齐 (v1.11.0 vs lock file 2.1.0)
- [P3] CI 增加 mypy/类型检查 job
- [P3] docker-compose.yml 升级格式 + 修复健康检查

---

## 6. 工程精简方案

### 6.1 立即可删 (零风险，确认无引用)

| 项目 | 行数 | 说明 |
|------|------|------|
| `filter/shared/logger.py` | 56 | `setup_logging()` 零生产调用, 所有代码直接 `from loguru import logger` |
| `filter/backtest/optimizer.py` | 93 | `suggest_params()`/`create_study()`/`optimize_backtest_params()` 仅被测试引用 |
| `filter/shared/repository.py` | 109 | `BaseRepository` + `PresetRepository` 零生产模块调用 (全部绕过直接调用 config_db) |
| `filter/backtest/logger.py` (部分) | 38 | `log_bar_navigation()` 和 `log_error()` 零调用者 |
| `filter/data/config_db.py` (部分) | 80 | `load_ticker_config()`/`save_ticker_config()`/`record_history()` 零外部调用 |
| **生产代码小计** | **~376** | |
| `tests/test_optuna.py` | 106 | 测试死模块 |
| `tests/test_repository.py` | 283 | 测试死模块 |
| `tests/test_cleanup.py` | 221 | 元测试 (应转 lint 规则) |
| **测试代码小计** | **~610** | |
| **总计可删除** | **~986** | |

### 6.2 可合并/重组 (低风险)

| 项目 | 影响 |
|------|------|
| `filter/tests/` (3文件) -> `tests/` | 统一测试目录。`pyproject.toml` 配置 `testpaths = ["tests"]` 导致 `filter/tests/` 中的测试不会被 CI 自动发现 |
| `components_sidebar.py` 常量去重 | `ALL_TFS`/`DEFAULT_TFS`/`TF_HIERARCHY` 已在 `filter.shared.constants` 中定义, 移除冗余 |
| `backtest/pipeline.py` -> `backtest/capture.py` | 消除与 `engine/pipeline.py` 的命名歧义 |
| `TEST_TIMES.md` -> `tests/README.md` | 合并测试文档碎片 |

### 6.3 可简化 (中风险，过度设计)

| 项目 | 建议 |
|------|------|
| `filter/data/config_db.py` (683行) | Streamlit 辅助代码移至 `filter/browse/`, 死函数删除 |
| `tests/test_backtest.py` (2215行, 116测试) | 按功能域拆分为 panel/logger/cutoff 等独立测试文件 |
| `tests/test_cleanup.py` (221行) | 将检查逻辑移至 `.pre-commit-config.yaml` 或 `Makefile` lint 目标 |
| `filter/shared/state.py` (348行) | 暂不处理, 未来按"系统默认值 / ViewState / AppState"拆分 |

### 6.4 依赖清理清单 (28 个未使用包)

以下依赖在代码中零引用 (部分可能为传递依赖, 建议用 `pipdeptree` 精确分析后清理):

| 类别 | 依赖 |
|------|------|
| ORM | `peewee` |
| 缓存/队列 | `redis`, `rq` |
| 云服务 | `boto3`, `azure-storage-blob`, `azure-identity`, `azure-core` |
| 数据源 | `tushare`, `akshare`, `efinance` |
| 量化框架 | `backtrader`, `vectorbt`, `bt`, `ffn` |
| AI/ML | `openai`, `litellm`, `torch`, `mlx`, `mlx-whisper`, `faster-whisper`, `openai-whisper`, `scikit-learn`, `joblib` |
| 可视化 | `seaborn`, `panel` |
| 调度 | `APScheduler` |
| 加密交易 | `ccxt` |
| 数据处理 | `polars` |
| 超参优化 | `optuna` |

**预估节省**: requirements.lock 从 349 行 -> ~100 行, 安装时间减少 ~60%, 镜像体积减少 ~40%。

### 6.5 精简路线图

**第一阶段 (零风险，立即执行，2天)**:
1. 删除 `filter/shared/logger.py`
2. 删除 `filter/backtest/optimizer.py` + `tests/test_optuna.py`
3. 删除 `filter/shared/repository.py` + `tests/test_repository.py`
4. 删除 `filter/backtest/logger.py` 中死函数
5. 删除 `filter/data/config_db.py` 中死函数
6. 清理 `filter/__init__.py` 相应 re-export
7. 从 `requirements.lock` 移除 `optuna`

**第二阶段 (低风险，需回归测试，1天)**:
8. 合并 `filter/tests/` -> `tests/`
9. 去重 `components_sidebar.py` 常量定义
10. 重命名 `backtest/pipeline.py` -> `backtest/capture.py`
11. 合并 `TEST_TIMES.md` -> `tests/README.md`

**第三阶段 (中风险，需设计评审，3天)**:
12. 拆分 `tests/test_backtest.py`
13. 拆分 `filter/data/config_db.py`
14. 移除 `tests/test_cleanup.py` (转为 lint 规则)

---

## 7. 优化路线图

### 短期 (1-2周): Quick Wins

**目标**: 在不改变架构的前提下，用最小改动获取最大收益。

| 里程碑 | 优化项 | 预估收益 | 工期 |
|--------|--------|---------|------|
| M1: 展示性能 | Dashboard `go.Scatter` -> `scattergl` | 10-30x 图表渲染加速 | 0.5天 |
| M1: 展示性能 | `iterrows()` -> 向量化 | 10-50x 数据提取加速 | 0.5天 |
| M2: 数据性能 | `_compute_version` 用 metadata.num_rows | 99%+ I/O 减少 | 0.2天 |
| M2: 数据可靠性 | yfinance 超时/重试 | 成功率 85%->99% | 0.3天 |
| M3: 用户体验 | 添加图表导出 (Plotly modebar + download_button) | 分析师工作流效率提升 | 0.5天 |
| M3: 正确性 | metrics 全时序 PnL 修复 (最紧急 bug 修复) | 正确性修复 | 1天 |
| M4: 正确性 | `save_checkpoint` bar_index 修复 | 断点恢复正确性 | 0.2天 |
| M4: 浏览性能 | 数据校验并行化 (8 次 yfinance -> ThreadPoolExecutor) | 3-6X 加速 | 0.5天 |
| M5: 架构 | 消除 browse/backtest 循环依赖 (ALL_TFS->constants) | 架构清晰化 | 1天 |
| M5: 精简 | 删除全部死代码 (~986 行 + optimizer) | 代码库瘦身 | 2天 |

**短期总工期: ~7 个工作日**

### 中期 (1-2月): 结构性改进

**目标**: 重构核心模块，消除架构债务。

| 里程碑 | 优化项 | 预估收益 | 工期 |
|--------|--------|---------|------|
| M6: 浏览核心 | Plotly JSON payload 减少 (共享 CDN + 差异化 traces) | HTML 传输减少 50-70% | 3天 |
| M7: 浏览核心 | `_render_chart` 上帝函数拆分 (248行 -> 3函数) | 可测试性大幅提升 | 3天 |
| M8: 引擎核心 | 拆分 filters.py God Module (1196行 -> 4模块) | 维护性提升 | 5天 |
| M9: 回测核心 | numba 加速回测热点路径 | 总时间减少 20-40% | 5天 |
| M10: 存储 | ParquetStore OOM 防护 + 列式 buffer 重构 | 内存安全 | 3天 |
| M11: 展示 | 提取公共 PnL 渲染模块 (消除 dashboard/chart_builder 代码重复) | 50行重复消除 | 2天 |
| M12: 颜色 | 创建 `filter/constants/colors.py` 集中管理 | 3处颜色统一 | 1天 |
| M13: 日志 | 统一 backtest/logger.py -> shared/logger.py | 日志系统单一入口 | 1天 |

**中期总工期: ~23 个工作日 (约 5 周)**

### 长期 (3月+): 架构演进

**目标**: 系统性架构升级，提升工程质量。

| 里程碑 | 优化项 | 预估收益 | 工期 |
|--------|--------|---------|------|
| M14: 测试 | CI 覆盖率阈值 55% -> 80% (分步推进) | 回归保护增强 | 持续 |
| M15: 测试 | CI 增加 mypy 类型检查 + mypy 版本对齐 | 类型安全 | 2天 |
| M16: 架构 | state.py 解耦 Streamlit (StateStore + StreamlitStateStore) | CLI 可复用状态管理 | 3天 |
| M17: 架构 | ViewConfig 双接口清理 (移除 __getitem__/get) | 类型契约清晰化 | 2天 |
| M18: 依赖 | 28 个未使用包精确清理 (pipdeptree 分析) | 安装速度 +60%, 镜像 -40% | 2天 |
| M19: 工程 | docker-compose.yml 升级 + 健康检查修复 | 部署健壮性 | 1天 |
| M20: 工程 | requirements.lock 拆分为 requirements.txt + requirements-dev.txt | 生产依赖最小化 | 1天 |
| M21: 工程 | 迁移 filter/tests/ -> tests/ + 拆分 test_backtest.py | 测试结构规范化 | 2天 |
| M22: 可访问性 | Dashboard 色盲友好配色模式 | WCAG 2.1 AA 合规 | 2天 |

**长期总工期: ~15 个工作日 (分散在 3 个月中)**

---

## 8. 风险评估与缓解

### 8.1 高风险项

| 风险 | 来源 | 影响 | 概率 | 缓解措施 |
|------|------|------|------|---------|
| `go.Scattergl` 的 `fill="tozeroy"` 不兼容 | 展示 H-1 | PnL 填充区域丢失 | 中 | 降级策略: 保留 1 个 `go.Scatter` 用于填充层, 其余用 `scattergl` |
| numba `@njit` 数据重构引入 bug | 回测 H-7 | 回测结果错误 | 中 | 保留原实现作为 reference, 对比测试确保数值一致 |
| filters.py 拆分破坏 import 链 | 跨领域 P0-3 | 模块无法导入 | 中 | 保留原文件作为 re-export 代理, 渐进式迁移 |
| 死代码误删 (依赖未被检测) | 精简 S1 | 运行时 ImportError | 低 | 逐文件删除 + 全量测试套件验证 |
| metrics 修复改变历史回测指标 | 回测 H-5 | 已有回测结果不再可比 | 中 | 保留旧指标作为参考, 新计算结果标注版本 |

### 8.2 累积效应风险

- **短期密集修改**: 7 个工作日执行 10+ 项修改, 建议分批提交, 每批后运行全量测试
- **numba 加速 + 代码拆分同时进行**: 两个都是中型重构, 建议串行执行, 避免交互 bug 难以定位
- **依赖清理**: 28 个包中部分可能是传递依赖, 需要 `pipdeptree` 精确分析后逐包移除并测试

### 8.3 回退方案

- 所有重构保留原代码作为 `_deprecated` 或注释备用
- 每次重大改动在独立分支上进行, 通过 CI 后再合并
- 性能优化类改动保留 benchmark 对比数据

---

## 附录 A: 各模块评分汇总

| 模块 | 综合评分 | 性能 | 可维护性 | 正确性 | 备注 |
|------|---------|------|---------|--------|------|
| 浏览模式 (browse/) | C+ (6.0) | 5.5 | 6.5 | 7.0 | 5 文件, 2377 行 |
| 回测模式 (backtest/) | B- (6.5) | 6.5 | 7.0 | 7.5 | 8 文件, ~5400 行 |
| CLI 数据管线 (data/ + tools/) | B- (6.7) | 6.0 | N/A | 8.0 | 6 数据文件 + 3 工具 |
| 回测展示 (dashboard/panel/view_backtest) | C- (5.5) | 4.0 | 5.0 | N/A | 4 核心文件 |
| 跨领域架构 (shared/engine/CI) | C (5.0) | N/A | 5.0 | N/A | 整体工程健康度 |

---

## 附录 B: 完整优化清单 (~95 项汇总表)

下表汇总了 6 份分析报告中所有优化点, 经过去重合并后的完整清单。

**优先级标记**: 🔴 P0 (紧急) | 🟠 P1 (高) | 🟡 P2 (中) | 🟢 P3 (低)

### B.1 浏览模式 (Browse)

| # | 优先级 | 优化项 | 文件 | 行号 | 复杂度 | 去重标记 |
|---|--------|--------|------|------|--------|---------|
| B1 | 🔴 | Plotly JSON 内嵌导致 HTML payload >1MB | `browse/charts.py` | 34-104 | 中 | |
| B2 | 🟠 | `_render_chart` 上帝函数 (248行) | `browse/app.py` | 299-547 | 高 | |
| B3 | 🟠 | `_add_schmitt_traces` 循环创建大量小 trace | `browse/chart_builder.py` | 208-218 | 低-中 | |
| B4 | 🟠 | 数据校验顺序调用 yfinance 8 次 | `browse/sidebar.py` | 307-318 | 低 | |
| B5 | 🟡 | `_cached_strategy_pnl` JSON 往返序列化 | `browse/app.py` | 200-216 | 低 | |
| B6 | 🟡 | `charts.js` 在 4 视图中重复嵌入 | `browse/charts.py` | 25-28 | 中 | |
| B7 | 🟡 | `_render_params` 冗余 session_state.get | `browse/components_sidebar.py` | 277-316 | 低 | |
| B8 | 🟡 | `_date_markers` 无缓存 + 未使用变量 | `browse/chart_builder.py` | 16-76 | 低 | |
| B9 | 🟡 | DB 连接双层缓存冗余 | `browse/app.py` | 551-567 | 低 | |
| B10 | 🟢 | preset_selector hash-based key 不稳定 | `browse/sidebar.py` | 129-131 | 低 | |
| B11 | 🟢 | OHLC `.ravel()` -> `.to_numpy()` | `browse/chart_builder.py` | 142-144 | 低 | |
| B12 | 🟢 | PnL traces 与 charts.py 标记代码重复 | `browse/chart_builder.py` vs `charts.py` | 236-287 | 低 | |
| B13 | 🟢 | `_cached_fetch_stock` TTL 固定值 | `browse/app.py` | 83-110 | 低 | |
| B14 | 🟡 | 颜色方案硬编码散落 | `browse/chart_builder.py` | 多处 | 中 | 与 D4 合并 |

### B.2 回测模式 (Backtest)

| # | 优先级 | 优化项 | 文件 | 行号 | 复杂度 | 去重标记 |
|---|--------|--------|------|------|--------|---------|
| B15 | 🔴 | 逐 bar 文件存在性检查 | `backtest/engine.py` | 545-546 | 极低 | |
| B16 | 🔴 | 逐 bar 重复 set_index().sort_index() | `backtest/engine.py` | 553-554 | 中 | |
| B17 | 🔴 | 逐 bar 重复排序视图配置 | `backtest/engine.py` | 191-195 | 极低 | |
| B18 | 🟠 | 管道缓存 MD5 哈希开销 | `backtest/engine.py` | 628-631 | 低 | |
| B19 | 🟠 | 并行模式禁用 pipeline_cache 无收益 | `backtest/engine.py` | 198,218 | 低 | |
| B20 | 🔴 | metrics 仅取最后一步第一个视图 (**最紧急 bug**) | `backtest/engine.py` | 1062-1113 | 中 | |
| B21 | 🔴 | save_checkpoint bar_index 硬编码 0 | `backtest/engine.py` | 334 | 极低 | |
| B22 | 🟠 | from_checkpoint 抛出 NotImplementedError | `backtest/engine.py` | 380 | 低 | |
| B23 | 🟡 | np.where 魔数 100.0 | `backtest/metrics.py` | 51 | 极低 | |
| B24 | 🟡 | Calmar ratio 回撤 0 返回 0.0 | `backtest/metrics.py` | 82 | 极低 | |
| B25 | 🟡 | CSVBuilder 全量内存累积 | `backtest/recorder.py` | 39,72-87 | 中 | |
| B26 | 🟡 | `_buffer_to_table` 列式 pivot 开销 | `data/store.py` | 682-708 | 高 | |
| B27 | 🔴 | numba 零覆盖 (回测热点路径) | 全 `backtest/` | — | 高 | |
| B28 | 🟡 | Parquet/JSON 同步 I/O 阻塞主循环 | `backtest/pipeline.py` | 246-286 | 中 | |
| B29 | 🟡 | query() 忽略 date_from/min_sharpe | `backtest/catalog.py` | 50-61 | 低 | |
| B30 | 🟡 | optuna 优化无进度回调 | `backtest/optimizer.py` | 75-93 | 极低 | |
| B31 | 🟡 | recorder 和 store 双重写入 (90% 重复) | `backtest/cli.py` | 548-561 | 中 | |
| B32 | 🟢 | `_load_all_bar_info` LIMIT 静默截断 | `backtest/engine.py` | 433 | 极低 | |
| B33 | 🟢 | 播放模式 OFFSET SQL 查询 | `backtest/panel.py` | 26-50 | 低 | |
| B34 | 🟢 | snappy -> zstd 压缩统一 | `backtest/pipeline.py` | 293 | 极低 | |
| B35 | 🟢 | BACKTEST_PARALLEL_VIEWS 无文档 | `backtest/engine.py` | 198 | 极低 | |

### B.3 CLI 数据管线 (Data & Tools)

| # | 优先级 | 优化项 | 文件 | 行号 | 复杂度 | 去重标记 |
|---|--------|--------|------|------|--------|---------|
| B36 | 🔴 | yfinance 下载缺少超时/重试 | `data/fetcher.py` | 163-167 | 低 | |
| B37 | 🔴 | 内存缓冲区无上限 OOM 风险 | `data/store.py` | 197 | 中 | |
| B38 | 🟠 | 每次操作创建新 SQLite 连接 | `data/db.py` | 19-39 | 中 | |
| B39 | 🔴 | `_compute_version` 全量读 Parquet | `data/loader.py` | 163-178 | 极低 | |
| B40 | 🟠 | 周线回退增加额外网络调用 | `data/fetcher.py` | 177-189 | 低 | |
| B41 | 🟠 | 级联合成中重复 DB 查询 | `data/synth.py` | 375-389 | 低 | |
| B42 | 🟡 | ThreadPoolExecutor 线程数硬编码 | `data/fetcher.py` | 76 | 低 | |
| B43 | 🟡 | 时间戳 TEXT 查询效率低 | `data/db.py` | 54-67 | 中 | |
| B44 | 🟡 | view_backtest 大数据嵌入 HTML 膨胀 | `tools/view_backtest.py` | 375-391 | 中 | |
| B45 | 🟡 | filter_comparison_tool 缺少 CLI 参数 | `tools/filter_comparison_tool.py` | 807-865 | 中 | |
| B46 | 🟡 | CSV 导出 float32->float64 内存翻倍 | `data/store.py` | 770-773 | 极低 | |
| B47 | 🟢 | `_resolve_read_path` os.walk 无缓存 | `data/loader.py` | 88-92 | 低 | |
| B48 | 🟢 | 健康检查 O(n*m) 查询 | `data/db.py` | 300-399 | 低 | |
| B49 | 🟢 | ConfigDB 与 Streamlit 强耦合 | `data/config_db.py` | 626-662 | 中 | |
| B50 | 🟢 | benchmark_pipeline 缺少版本对比 | `tools/benchmark_pipeline.py` | 465-540 | 低 | |

### B.4 回测展示 (Display)

| # | 优先级 | 优化项 | 文件 | 行号 | 复杂度 | 去重标记 |
|---|--------|--------|------|------|--------|---------|
| B51 | 🔴 | `iterrows()` 逐行遍历 | `backtest/dashboard.py` | 129 | 低 | 与 B36(回测#22) 合并 |
| B52 | 🔴 | go.Scatter(SVG) -> scattergl(WebGL) | `backtest/dashboard.py` | 241,251,263,280 | 低 | |
| B53 | 🟠 | 缺少导出功能 (CSV/PNG/PDF) | `backtest/dashboard.py` | — | 低 | |
| B54 | 🟡 | 颜色方案散落三处无集中管理 | dashboard/chart_builder/view_backtest | 多处 | 中 | 与 B14 合并 |
| B55 | 🟡 | KPI 指标缺少信息分层和高亮 | `backtest/dashboard.py` | 142-206 | 低 | |
| B56 | 🟡 | 交易明细表无分页/排序/筛选 | `backtest/dashboard.py` | 339-368 | 低 | |
| B57 | 🟡 | PnL 图缺少交互控制和 tooltip | `backtest/dashboard.py` | 209-301 | 低 | |
| B58 | 🟡 | 4 个指标未展示 (avg_win/loss_pct, win/lose_trades) | `backtest/dashboard.py` vs `metrics.py` | 142-206, 97 | 低 | |
| B59 | 🟡 | `df_to_columns` 全量逐值序列化 | `tools/view_backtest.py` | 58-82 | 中 | |
| B60 | 🟢 | 导航按钮每次 st.rerun() | `backtest/panel.py` | 210-253 | 中 | |
| B61 | 🟢 | 无响应式设计适配 | `backtest/dashboard.py` | 156,182 | 低 | |
| B62 | 🟢 | 红绿色盲可访问性 | dashboard/chart_builder | 多处 | 中 | |
| B63 | 🟢 | Dashboard 与 chart_builder 代码重复 | dashboard vs chart_builder | — | 中 | |

### B.5 跨领域架构 (Cross-Cutting)

| # | 优先级 | 优化项 | 文件 | 行号 | 复杂度 | 去重标记 |
|---|--------|--------|------|------|--------|---------|
| B64 | 🔴 | browse/backtest 循环依赖 | panel.py/dashboard.py/app.py | 19,64-67 | 低 | 与 S2.2 合并 |
| B65 | 🔴 | TF_LOWER/TF_HIERARCHY 重复常量 | shared/constants, engine/signals | 17-21, 18-22 | 低 | |
| B66 | 🔴 | ViewConfig 双接口模式 (__getitem__/get) | `shared/config.py` | 157-165 | 中 | |
| B67 | 🔴 | filters.py God Module (1196行) | `engine/filters.py` | 全文件 | 高 | |
| B68 | 🟠 | 两个 pipeline.py 同名 | engine/pipeline, backtest/pipeline | — | 低 | 与 S3.5 合并 |
| B69 | 🟠 | 两种日志系统并存 | shared/logger, backtest/logger | — | 中 | |
| B70 | 🟠 | state.py 紧耦合 Streamlit | `shared/state.py` | 19-22 | 高 | |
| B71 | 🟠 | requirements.lock 依赖膨胀 (349行) | `requirements.lock` | 全文件 | 中 | |
| B72 | 🟠 | CI 覆盖率阈值过低 (55%) | `pyproject.toml` | 36 | 低 | |
| B73 | 🟡 | repository.py 过度抽象 | `shared/repository.py` | 全文 | 低 | 与 S1.3 合并 |
| B74 | 🟡 | 配置管理分散 (3处) | pyproject.toml/.env/config.py | — | 低 | |
| B75 | 🟡 | filter/__init__.py sys.path hack | `filter/__init__.py` | 17-19 | 低 | |
| B76 | 🟡 | filter/tests/ 残留测试 | `filter/tests/` | — | 低 | 与 S2.1 合并 |
| B77 | 🟡 | Dockerfile 单阶段 vs requirements.lock | `Dockerfile` | 4 | 低 | |
| B78 | 🟢 | pre-commit mypy 版本不匹配 | `.pre-commit-config.yaml` | 22-27 | 低 | |
| B79 | 🟢 | CI 缺少 mypy/类型检查 job | `.github/workflows/ci.yml` | — | 低 | |
| B80 | 🟢 | docker-compose.yml 旧格式 + 脆弱健康检查 | `docker-compose.yml` | 1,18-21 | 低 | |
| B81 | 🟢 | Makefile changelog 命令脆弱 | `Makefile` | 40-49 | 低 | |

### B.6 工程精简 (Simplification)

| # | 优先级 | 优化项 | 文件 | 复杂度 | 去重标记 |
|---|--------|--------|------|--------|---------|
| B82 | 🔴 | 删除 shared/logger.py 死代码 (56行) | `filter/shared/logger.py` | 极低 | 与 B69 关联 |
| B83 | 🔴 | 删除 backtest/optimizer.py (93行) | `filter/backtest/optimizer.py` | 极低 | |
| B84 | 🔴 | 删除 shared/repository.py (109行) | `filter/shared/repository.py` | 极低 | 与 B73 合并 |
| B85 | 🔴 | 删除 backtest/logger.py 死函数 (38行) | `filter/backtest/logger.py` | 极低 | |
| B86 | 🔴 | 删除 config_db.py 死函数 (80行) | `filter/data/config_db.py` | 极低 | |
| B87 | 🔴 | 删除 tests/test_optuna.py (106行) | `tests/test_optuna.py` | 极低 | |
| B88 | 🔴 | 删除 tests/test_repository.py (283行) | `tests/test_repository.py` | 极低 | |
| B89 | 🟡 | tests/test_cleanup.py 转 lint 规则 (221行) | `tests/test_cleanup.py` | 低 | |
| B90 | 🟠 | 合并 filter/tests/ -> tests/ | `filter/tests/` | 低 | 与 B76 合并 |
| B91 | 🟡 | components_sidebar.py 常量去重 | `browse/components_sidebar.py` | 低 | 与 B64 关联 |
| B92 | 🟡 | 重命名 backtest/pipeline.py -> capture.py | `backtest/pipeline.py` | 低 | 与 B68 合并 |
| B93 | 🟡 | 合并 TEST_TIMES.md -> tests/README.md | `tests/TEST_TIMES.md` | 低 | |
| B94 | 🟡 | 拆分 tests/test_backtest.py (2215行) | `tests/test_backtest.py` | 中 | |
| B95 | 🟡 | 拆分 config_db.py (683行) | `filter/data/config_db.py` | 中 | |

### B.7 测试覆盖缺口 (新增测试)

| # | 测试目标 | 缺口描述 |
|---|---------|---------|
| T1 | `fetcher.py` | 无独立测试文件, 缺少 8 周期并发正确性测试、yfinance 异常场景测试 (429/超时/空响应) |
| T2 | `filter_comparison_tool.py` | 零测试覆盖, 10 个滤波器和 5 种信号正确性未验证 |
| T3 | `benchmark_pipeline.py` | 工具本身无测试, 仅测试 filter 性能不测试 pipeline 工具 |
| T4 | `store.py` 集成 | 缺少 append_row -> flush -> end_session 完整生命周期测试 |
| T5 | `db.py` 并发 | 缺少多线程 upsert 竞态测试 |
| T6 | `synth.py` 并发 | `_synth_cache_state` 模块级全局变量的多线程安全测试 |

---

## 附录 C: 模块文件大小与复杂度分布

| 排名 | 文件 | 行数 | 复杂度 | 建议 |
|------|------|------|--------|------|
| 1 | `filter/engine/filters.py` | 1196 | **极高** | 拆分为 4 个模块 |
| 2 | `filter/backtest/engine.py` | 1113 | 高 | 可接受 (核心引擎) |
| 3 | `filter/data/store.py` | 1052 | 高 | 可接受 (存储引擎) |
| 4 | `filter/backtest/recorder.py` | 897 | 高 | 可接受 |
| 5 | `filter/browse/app.py` | 794 | 高 | 拆分 `_render_chart` |
| 6 | `filter/browse/sidebar.py` | 574 | 中 | 可优化 |
| 7 | `filter/data/config_db.py` | 683 | 中 | 移除 Streamlit 耦合 + 死代码 |
| 8 | `filter/backtest/cli.py` | 598 | 中 | 可接受 |
| 9 | `tests/test_backtest.py` | 2215 | 高 | 拆分为多个文件 |
| 10 | `filter/browse/charts.py` | 372 | 中 | 重构 Plotly 渲染 |
| 11 | `filter/shared/state.py` | 349 | 中 | 解耦 Streamlit |
| 12 | `filter/browse/components_sidebar.py` | 318 | 中 | 常量化 + 去重 |
| 13 | `filter/browse/chart_builder.py` | 317 | 中 | 可接受 |
| 14 | `filter/backtest/pipeline.py` | 299 | 中 | 重命名为 capture.py |
| 15 | `filter/engine/pipeline.py` | 158 | 低 | 可接受 |
| 16 | `filter/shared/config.py` | 166 | 低 | 移除双接口 |
| 17 | `filter/shared/repository.py` | 109 | 低 | **待删除** |
| 18 | `filter/backtest/optimizer.py` | 93 | 低 | **待删除** |
| 19 | `filter/backtest/catalog.py` | 61 | 低 | 可接受 |
| 20 | `filter/shared/constants.py` | 56 | 低 | 合并 TF_LOWER |
| 21 | `filter/shared/logger.py` | 56 | 低 | **待删除** |

---

*报告由 6 份专项深度分析自动汇总生成。所有优化建议均附有文件路径和具体方向。建议从短期 Quick Wins 开始，在 2 周内获取可见收益，随后按路线图分阶段推进。*
