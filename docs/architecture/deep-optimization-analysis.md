# filter_research 深度优化分析报告（第二轮）

> **分析日期**: 2026-07-24
> **分析范围**: 算法与信号处理(D1)、数据管道与存储(D2)、Streamlit UI(D3)、回测架构(D4)、工程化成熟度(D5)、测试策略(D6)
> **原始发现数**: 97 项(去重后 53 项独立问题)

---

## 执行摘要

六维度深度分析共发现 **97 项原始问题**，经跨维度去重合并后得到 **53 项独立优化点**。

**TOP 5 最高 ROI 优化**:
1. `np.searchsorted` 替代线性扫描(日期查找 50-200x, 跨周期对齐 10-50x) -- 合计 3 行改动
2. 回测批量预取消除逐 bar DB 查询 -- 回测速度提升 5-10x
3. SQLite PRAGMA 优化(mmap+temp_store+cache_size) -- 读延迟降低 20-40%
4. Numba 编译 Schmitt 触发器 -- 10-50x 加速, 回测管道核心热点
5. PnL 曲线批量切片填充 -- 消除 O(trades x n) 重复写入

与第一轮 P0-P3 的关系: 第一轮已解决增量计算缓存(P0-2)和多视图并行(P3-2)。本轮发现的 **53 项中约 15 项是第一轮未覆盖的全新发现**，集中在算法层面(A2-A14)、Streamlit 工程化(D3)、回测指标与参数优化(D4)、CI/可观测性(D5)和测试基础设施(D6)等维度。

---

## 交叉分析矩阵

### 跨维度关联图

```
                  D1 算法          D2 数据管道        D3 Streamlit       D4 回测           D5 工程化         D6 测试
D1 算法           —                
D2 数据管道       A7↔分区间查找     —                  
D3 Streamlit      —                D2-缓存↔D3-缓存    —                  
D4 回测           A3↔Numba化★     D2-P0↔批量预取★    D3-级联合成↔D4-预同步★  —
D5 工程化         —                —                  D3-session↔D5-config  —              —
D6 测试           A12↔精度验证     —                  D3-fragment↔D6-UI   D4-WF↔D6-e2e   D5-CI↔D6-CI★    —
```

**★ 标记的跨维度关联表示完全重叠的发现，已合并为同一优化项。**

### 去重合并的关键重叠

| 重叠发现 | 涉及维度 | 合并后名称 | 说明 |
|----------|----------|-----------|------|
| Numba Schmitt 优化 | D1 A3 ↔ D4 2.1.C | 施密特触发器 Numba 化 | D1 从算法角度,D4 从回测加速角度,本质相同 |
| 回测批量预取 | D2 P0 ↔ D4 2.1.A | 回测批量数据预取 | D2 从 DB 查询角度,D4 从管道架构角度 |
| 级联合成缓存 | D3 P0 ↔ D4 2.1.B | 回测预同步 + 级联合成缓存优化 | D3 从 session_state 角度,D4 从数据同步角度 |
| ParquetStore 内存 | D2 P0 ↔ D4 4.3 | ParquetStore buffer 内存上限 | D2 从存储角度,D4 从风险角度 |
| CI 分阶段 | D5 CI ↔ D6 3.1 | CI 三阶段流水线 | D5 从工程化角度,D6 从测试执行角度 |

### 各维度发现贡献统计

| 维度 | 原始发现 | 去重后独立 | P0 | P1 | P2 | P3 |
|------|---------|-----------|----|----|----|-----|
| D1 算法 | 14 | 12 | 2 | 2 | 3 | 5 |
| D2 数据管道 | 23 | 11 | 3 | 1 | 3 | 4 |
| D3 Streamlit | 12 | 12 | 1 | 4 | 3 | 4 |
| D4 回测 | 10 | 8 | 2 | 3 | 2 | 1 |
| D5 工程化 | 25 | 21 | 0 | 4 | 6 | 11 |
| D6 测试 | 13 | 13 | 2 | 3 | 4 | 4 |
| **合计** | **97** | **53(含合并)** | **10** | **17** | **21** | **29** |

*注: 部分去重后的发现归入主要维度,在报告中标注所有来源维度。合并项计入主要维度计数。*

---

## 发现总览（优先级排序）

### P0 -- 一行改动, 巨大收益

| # | 优化项 | 来源 | 改动量 | 预估提升 | 人天 |
|---|--------|------|--------|---------|------|
| P0-1 | `np.searchsorted` 替换日期线性扫描 | D1 A8 | 3 行 | 50-200x | 0.1 |
| P0-2 | `np.searchsorted` 替换跨周期对齐 O(n*m) 扫描 | D1 A7 | 3 行 | 10-50x | 0.1 |
| P0-3 | 回测批量预取：一次性 DB 查询替代逐 bar 查询 | D2 P0 D4 | ~30 行 | 5-10x 回测速度 | 0.5 |
| P0-4 | SQLite PRAGMA 优化(mmap + temp_store=MEMORY + cache_size) | D2 S1/S2/S3 | 3 行 | 读延迟 -20~40% | 0.1 |
| P0-5 | 回测开始前预同步级联合成数据(消除逐 bar `_sync_data`) | D3 P0 D4 2.1.B | ~15 行 | I/O 减少 3-10x | 0.2 |
| P0-6 | 补充核心回测指标(Sharpe/Sortino/Calmar/MaxDD 持续) | D4 2.3 | ~60 行 | 专业回测基础 | 0.3 |
| P0-7 | ParquetStore buffer 内存上限(防超长回测 OOM) | D2 P0 D4 4.3 | ~10 行 | 防御性 | 0.2 |
| P0-8 | `force_update_kline` 循环 DELETE 改 `executemany` | D2 P8 | ~5 行 | 批量更新提速 | 0.1 |
| P0-9 | 清理空测试目录(`tests/e2e/` `tests/integration/` `tests/unit/`) | D6 P0-1 | 删除残留 | 结构清晰 | 0.1 |
| P0-10 | CLI 端到端烟雾测试(至少 1 个 `subprocess.run` 用例) | D6 P0-2 | ~30 行 | 关键路径保护 | 0.3 |

### P1 -- 中等改动, 显著改善

| # | 优化项 | 来源 | 改动量 | 预估提升 | 人天 |
|---|--------|------|--------|---------|------|
| P1-1 | 施密特触发器 Numba JIT 编译 | D1 A3 D4 2.1.C | ~50 行 | 10-50x(Schmitt 占比~30%管道) | 1.0 |
| P1-2 | PnL 曲线切片批量填充(消除 O(trades*n) 重复尾部写入) | D1 A5 | ~40 行 | 3-10x(PnL 扫描段) | 0.5 |
| P1-3 | 合并 4 个图表 iframe 为单个 HTML(减少 Plotly.js 加载 4→1) | D3 P1 | ~100 行 | 首屏加载 -50%, 内存 -60% | 1.5 |
| P1-4 | 侧边栏 12 处 `st.rerun()` 改为 `on_click` callback | D3 P1 | ~80 行 | UI 交互延迟 -30~50% | 1.0 |
| P1-5 | 修复 `_run_auto_refresh` 的 `time.sleep` 阻塞(改时间戳比较) | D3 P1 | ~10 行 | 消除无响应期 | 0.1 |
| P1-6 | 滑点+佣金建模(ExecutionSimulator 层) | D4 2.4 | ~80 行 | 回测真实度质的提升 | 1.0 |
| P1-7 | LICENSE + CHANGELOG + Makefile 补全 | D5 P1 | 3 新文件 | 合规+开发体验 | 0.5 |
| P1-8 | `.env.example` 扩展 + `pydantic-settings` 启动校验 | D5 P1 | ~30 行 | 配置安全 | 0.3 |
| P1-9 | CI 三阶段拆分(fast/medium/slow) -- 实施 TEST_TIMES.md 建议 | D5 CI D6 3.1 | ~50 行 | CI 耗时 134s→60s | 0.5 |
| P1-10 | `_hash_array` 自定义 hash 移除(Streamlit≥1.28 已内置) | D3 P2 | 删除 ~8 行 | 每 rerun -0.5ms | 0.1 |
| P1-11 | EWMA 波动率 `mu_v` 向量化(pandas ewm) | D1 A2 | ~10 行 | EWMA 段 20-40% | 0.3 |
| P1-12 | PnL 前向填充改用 `pd.Series.ffill()` | D1 A4 | ~10 行 | 5-15x | 0.2 |
| P1-13 | 展开/折叠按钮改用 `@st.fragment` 局部刷新 | D3 P2 | ~15 行 | 避免全页 rerun | 0.3 |
| P1-14 | Loguru 生产模式配置 JSON 格式输出 | D5 P2 | ~5 行 | 可对接日志聚合 | 0.1 |
| P1-15 | `config_db.py` 中 `SELECT *` 改显式列名 | D2 P9 | ~15 行 | 规范性 | 0.2 |
| P1-16 | CI 增加 Docker 镜像构建+推送 GHCR | D5 P2 | ~40 行 | 发布自动化 | 0.3 |
| P1-17 | pre-commit 增加 mypy 和 bandit | D5 P2 | ~5 行 | 提交前质量门禁 | 0.2 |

### P2 -- 值得做, 不紧急

| # | 优化项 | 来源 | 人天 | 说明 |
|---|--------|------|------|------|
| P2-1 | 贝叶斯参数优化(Optuna 替代手调) | D4 2.2 | 4.0 | 300 次评估找到接近最优参数,vs 手工试错 |
| P2-2 | 基准对比(Buy & Hold 自动计算) | D4 2.3 | 0.3 | 验证策略超额收益 |
| P2-3 | 信号-执行解耦(ExecutionSimulator) | D4 2.4 | 2.0 | 架构清晰度+可扩展性 |
| P2-4 | 属性测试试点(为 3-5 个滤波函数引入 hypothesis) | D6 P1 | 2.0 | 预计发现 2-5 个边界 bug |
| P2-5 | 快照测试试点(Plotly Figure 序列化 syrupy) | D6 P1 | 1.0 | 防重构意外改变输出 |
| P2-6 | `backtest_core.py`(43KB) 独立单元测试 | D6 P2 | 3.0 | 核心模块目前仅间接测试 |
| P2-7 | pytest-benchmark 基线(替换硬编码阈值) | D6 P2 | 1.0 | CI 环境校准,跨平台可比 |
| P2-8 | 回测输出 catalog 元数据汇总(`_catalog/`) | D2 P2 | 0.5 | 跨 session 聚合查询能力 |
| P2-9 | `compare_with_db` 加防御性 LIMIT | D2 P2 | 0.1 | 防御性编程 |
| P2-10 | display cache 元数据/校验和(自动过期) | D2 P2 | 0.5 | UI 数据新鲜度保证 |
| P2-11 | 评估并清理 `_imp_` 双倍 session_state 备份机制 | D3 P1 | 1.0 | 内存减半,清理~50 孤立 key |
| P2-12 | PnL 数据移出 `st.session_state`(存模块级 dict) | D3 P2 | 0.5 | 避免每步序列化开销 |
| P2-13 | Git tag 规范化为严格 semver | D5 P2 | 0.2 | 发布规范化 |
| P2-14 | 统一使用 `requirements.lock`(CI + Docker) | D5 P2 | 0.3 | 可重现构建 |
| P2-15 | Sentry 错误追踪接入 | D5 P3 | 0.3 | 生产异常发现 |
| P2-16 | 多环境配置方案(dev/staging/prod) | D5 P3 | 1.0 | 环境隔离 |
| P2-17 | `apply_ema` 纯 NumPy 实现(避免 DataFrame 包装) | D1 A6 | 0.2 | 5-10x 加速 |
| P2-18 | 卡尔曼滤波整体 Numba 化 | D1 A11 | 0.5 | 10-100x(需先引入 numba) |
| P2-19 | 数据 schema 验证(yfinance 返回结构) | D6 P1 | 0.5 | 防上游 API 变更 |
| P2-20 | 并发安全测试(ThreadPoolExecutor 路径) | D6 P1 | 1.0 | 线程安全验证 |
| P2-21 | 扩展 auto-refresh 为 polling 模式 | D3 P1 | 0.3 | UI 响应性 |

### P3 -- 锦上添花

| # | 优化项 | 来源 | 人天 | 说明 |
|---|--------|------|------|------|
| P3-1 | 多页面架构(st.navigation 分离图表/配置/回测/数据管理) | D3 P3 | 4.0 | 可维护性 |
| P3-2 | Walk-Forward 分析框架(策略验证金标准) | D4 2.6 | 10.0 | 需先完成 P2-1 参数优化 |
| P3-3 | 图表 trace 合并(减少 draw call,static lines 用 SVG) | D3 P3 | 1.0 | 渲染优化 |
| P3-4 | 数据下采样框架(LTTB,阈值 1000 点) | D3 P3 | 1.0 | 未来数据量增长准备 |
| P3-5 | 复现性增强(种子管理+pip freeze 快照+Git commit hash) | D4 2.5 | 1.0 | 科学严谨性 |
| P3-6 | `_cached_strategy_pnl` JSON 序列化 cache key 优化 | D3 P3 | 0.5 | 大 pair 列表时改善 |
| P3-7 | `collect_current_params()` 全量扫描优化 | D3 P3 | 0.3 | 边际改善 |
| P3-8 | yfinance 增量拉取(利用 DB 最近日期缩小请求范围) | D2 P3 | 0.5 | 网络 I/O 节省 |
| P3-9 | display cache 时间维度分区(`cutoff={date}/`) | D2 P3 | 1.0 | 扩展性准备 |
| P3-10 | `np.gradient` 替换为 `np.diff`(均匀 bar 索引) | D1 A10 | 0.1 | ~2x 梯度计算 |
| P3-11 | 卡尔曼 `Q_mat` 循环外预计算 | D1 A1 | 0.1 | 5-10% Kalman 段 |
| P3-12 | `np.polyval` 预生成保护期预测值数组 | D1 A14 | 0.2 | 3-5x 止损扫描段 |
| P3-13 | 粗糙度计算显式 float64 精度 | D1 A12 | 0.1 | 大窗口数值安全 |
| P3-14 | `_format_synth_date` 时区后缀缓存 | D1 A13 | 0.1 | 1-3% 合成段 |
| P3-15 | 自动化 release workflow(tag→build→push→GitHub Release) | D5 P3 | 0.5 | 发布自动化 |
| P3-16 | dev/prod 依赖分离(uv 或 pip-tools) | D5 P4 | 1.0 | 依赖管理 |
| P3-17 | Prometheus metrics 暴露 + Grafana 仪表板 | D5 P3 | 3.0 | 可观测性 |
| P3-18 | 应用级 `/health` 端点(含 DB 连接检测) | D5 P2 | 0.3 | 运维基础 |
| P3-19 | 架构决策记录(ADR) | D5 P4 | on-going | 技术决策追溯 |
| P3-20 | 测试覆盖率提升至 80%+ | D5 P4 | 5.0 | 长期目标 |
| P3-21 | conftest.py Streamlit mock 重构为 opt-in fixture | D6 P3 | 2.0 | 测试隔离 |
| P3-22 | 测试数据工厂统一(集中 synthetic data 生成) | D6 P3 | 2.0 | 减少重复代码 |
| P3-23 | `cached_strategy_pnl` 的 pred_pairs 改为不可变数据结构 | D3 P3 | 0.5 | 避免 JSON dump/load |
| P3-24 | `@st.cache_data` 增加 `max_entries=50` 防内存膨胀 | D3 P3 | 0.1 | 长期运行保护 |
| P3-25 | Streamlit `check_same_thread=False` 风险评估 | D2 P10 | 0.1 | 防御性 |
| P3-26 | SQLite 连接 `atexit` 清理 | D2 P3 | 0.1 | 资源管理 |
| P3-27 | `make_subplots` 临时 Figure 消除 | D3 P3 | 0.2 | 微小 |
| P3-28 | 损坏配置降级测试 | D6 P2 | 1.0 | 鲁棒性 |
| P3-29 | 测试 flaky 检测(pytest-rerunfailures) | D6 P3 | 0.5 | CI 稳定性 |

---

## 特色发现（本轮独有的深层洞察）

以下发现是第一轮 P0-P3 优化未覆盖的**全新洞察**：

### 算法维度(D1)

1. **A7/A8 系统性 `np.searchsorted` 替换**: 代码中存在多处已排序数组上的线性扫描模式。`np.searchsorted` 二分查找替代后,日期查找 50-200x,跨周期对齐 10-50x。这是"一行改动、千倍回报"的典型案例。

2. **A5 PnL 尾部重复填充**: 每笔交易循环都覆写尾部 `long_pnl[exit_idx+1:n] = capital`,后一笔覆盖前一笔,只有最后一笔的填充有意义。消除这个 O(trades*n) 浪费,PnL 扫描段加速 3-10x。

3. **A3/A11 Numba 机会识别**: 施密特状态机和卡尔曼滤波是纯数值 Python 循环,无法向量化,但天然适合 Numba JIT 编译。10-100x 加速潜力,且不影响算法正确性。

4. **A9 缓存键哈希过热**: `np.ascontiguousarray(noisy).data.tobytes()` + MD5 作为缓存键涉及完整数组拷贝+密码学哈希。简单的采样指纹即可满足冲突概率要求。

### 数据管道维度(D2)

5. **级联合成缓存命中率为零**: `_synth_cache_state` 使用 `cutoff_date` 作为 key,回测中每个 bar 的 `cutoff_date` 都不同,缓存完全无效。这是一个"存在但实际上不工作"的缓存。

6. **ParquetStore 无界内存缓冲**: `_buffer_size=10_000_000` 旨在禁用分段 flush,将全部数据保留到 `end_session`。对于 10 万 bars x 51 列,峰值内存可达数 GB,无任何上限保护。

7. **`LIMIT 1 OFFSET N` 在 SQLite 是 O(N)**: `_get_bar_info` 用此模式逐 bar 取数据,大偏移量下性能线性退化。

### Streamlit 维度(D3)

8. **4 个独立 iframe 各载入一次 Plotly.js(~3.5MB)**: 虽然浏览器缓存可复用,但每个 iframe 各自创建独立的 Plotly 上下文,内存和解析开销 x4。

9. **`_imp_` 备份机制**: `session_state` 中每个 key 存储双份(`key` + `_imp_key`),约 50 个孤立备份 key 从未被清理。Streamlit≥1.30 已稳定保持 session_state,`_imp_` 可能是过度的防御性代码。

10. **`_run_auto_refresh` 使用 `time.sleep(interval)` 阻塞主线程**: 如果设置 `interval=600`,页面 10 分钟无响应。应改为时间戳比较模式。

### 回测维度(D4)

11. **逐 bar `_sync_data` 重复级联合成**: 批量回测中所有 bar 共享同一数据窗口,级联合成只需在开始前执行一次。当前每 bar 都重新合成 + 写 parquet。

12. **回测指标严重不足**: 仅有胜率和总收益率,缺失 Sharpe/Sortino/Calmar/年化指标。这些是专业量化回测的基础设施,实现成本极低(约 60 行纯计算)。

13. **无滑点/佣金建模**: 成交价等于信号价,等同于假设零交易成本。对频繁交易策略,实测收益会被严重高估。

### 工程化维度(D5)

14. **合规性空白**: 无 LICENSE(他人无法合法使用),无 CHANGELOG,无 Makefile。这是开源项目的基础门面,1 天内可补齐。

15. **Git tag 命名混乱**: 混用 `v10.9.0`、`v10.5-phase34`、`v10.4-gap-closure` 等格式,`.pyproject.toml`(v10.5.0)与最新 tag(v10.9.0)不同步。

### 测试维度(D6)

16. **空测试目录**: `tests/e2e/`、`tests/integration/`、`tests/unit/` 仅有 `__pycache__` 残留,源代码已删除但目录结构未清理。README 引用的 `test_alignment_subplot.py` 等文件已不存在。

17. **CI 未分阶段**: `TEST_TIMES.md` 已规划三阶段(fast/medium/slow)可降到 60s,但 CI YAML 仍是全量串行 134s。

---

## 优化路线图

### Sprint 1 (本周): 5 个 < 1 小时的 Quick Wins

| # | 优化项 | 时间 | 来源 | 收益 |
|---|--------|------|------|------|
| 1 | P0-1+P0-2: `np.searchsorted` 两处替换 | 15min | D1 | 日期查找 50-200x,跨周期对齐 10-50x |
| 2 | P0-4: SQLite PRAGMA 三行优化 | 15min | D2 | 读延迟 -20~40% |
| 3 | P0-6: 补充核心回测指标函数 | 30min | D4 | 专业回测基础 |
| 4 | P0-7: ParquetStore buffer 内存上限 | 15min | D2 D4 | 防 OOM |
| 5 | P1-5: 修复 auto-refresh time.sleep | 15min | D3 | 消除 UI 无响应期 |

**本周合计**: ~1.5 小时

### Sprint 2 (本月): 4 个 1-3 天的改进

| # | 优化项 | 时间 | 来源 | 收益 |
|---|--------|------|------|------|
| 1 | P0-3+P0-5: 回测批量预取 + 预同步数据 | 1天 | D2 D3 D4 | 回测速度 10-50x(与算法优化组合) |
| 2 | P1-1: 施密特触发器 Numba JIT | 1天 | D1 D4 | 管道热点 10-50x |
| 3 | P1-3+P1-4+P1-13: Streamlit 交互优化三件套 | 2天 | D3 | UI 渲染和响应显著改善 |
| 4 | P1-7+P1-8+P1-14: 工程合规补全 | 1天 | D5 | LICENSE+CHANGELOG+Makefile+配置校验 |

**本月合计**: ~5 天

### Sprint 3 (本季度): 3 个 1 周+的架构优化

| # | 优化项 | 时间 | 来源 | 收益 |
|---|--------|------|------|------|
| 1 | P1-6+P2-3: 滑点/佣金建模 + 信号-执行解耦 | 3天 | D4 | 回测真实度质的飞跃 |
| 2 | P2-1: 贝叶斯参数优化(Optuna) | 4天 | D4 | 自动化参数发现,替代手工试错 |
| 3 | P3-1: 多页面架构 + 测试基础设施完善 | 5天 | D3 D6 | 可维护性+测试覆盖 |

**本季度合计**: ~12 天

---

## 投入产出 TOP 10

| 排名 | 优化项 | 来源 | 人天 | 预估提升 | ROI 评分 |
|------|--------|------|------|---------|---------|
| 1 | `np.searchsorted` 日期+跨周期对齐 (P0-1, P0-2) | D1 | 0.1 | 10-200x(两处合计) | ★★★★★ |
| 2 | SQLite PRAGMA 三行优化 (P0-4) | D2 | 0.1 | 读延迟 -20~40% | ★★★★★ |
| 3 | 回测批量预取 + 预同步 (P0-3, P0-5) | D2 D3 D4 | 0.7 | 回测 10-50x | ★★★★★ |
| 4 | 补充回测指标 (P0-6) | D4 | 0.3 | 专业度质变 | ★★★★★ |
| 5 | 施密特触发器 Numba JIT (P1-1) | D1 D4 | 1.0 | 管道热点 10-50x | ★★★★ |
| 6 | 合并 4 iframe (P1-3) | D3 | 1.5 | 首屏 -50%, 内存 -60% | ★★★★ |
| 7 | PnL 切片填充消除 O(trades*n) (P1-2) | D1 | 0.5 | 扫描段 3-10x | ★★★★ |
| 8 | 工程合规补全 (P1-7, P1-8) | D5 | 0.8 | 开源就绪 | ★★★★ |
| 9 | 滑点+佣金建模 (P1-6) | D4 | 1.0 | 真实度质变 | ★★★ |
| 10 | 贝叶斯参数优化 (P2-1) | D4 | 4.0 | 参数发现自动化 | ★★★ |

*ROI 评分: 综合考虑投入(人天)、收益(性能/质量提升)、风险的综合评分。★★★★★ 为最高性价比。*

---

## 风险矩阵

### 高风险改动及缓解措施

| 风险 | 涉及优化 | 风险描述 | 严重度 | 缓解措施 |
|------|----------|----------|--------|---------|
| Numba 新增依赖 | P1-1, P2-18 | `numba` 增加安装复杂度, JIT 首次编译有 0.5s 延迟, LLVM 依赖可能在某些平台不可用 | 中 | 1) 先试点 Schmitt(A3),验证收益后再扩展 2) 设 `cache=True` 缓存编译 3) 保留纯 Python fallback 路径 |
| PnL 计算逻辑重构 | P1-2, P0-3, P0-5 | 回测管道核心代码改动,边缘情况(NaN/非正价格)可能导致 PnL 计算偏差 | 高 | 1) 用小规模已知输出回测做回归对比 2) 保留旧实现作为 `_legacy` 分支直到验证通过 3) 逐 bar diff 新旧 PnL 曲线 |
| session_state 重构 | P2-11, P2-12 | `_imp_` 移除和 PnL 迁移影响整个 UI 状态管理,连锁 bug 风险高 | 高 | 1) 先确认 Streamlit 版本≥1.30 2) 灰度迁移:先停 `_imp_` 写入,观察 rerun 行为 3) 保留 rollback 机制 |
| 级联合成缓存重构 | P0-5, D3 P0 | 改变缓存 key 粒度可能影响回测播放和浏览模式的数据一致性 | 中 | 1) 两个模式(浏览/回测)分别测试 2) 添加 assert 验证合成数据一致性 |
| ParquetStore buffer 限制 | P0-7 | 设置内存上限后超长回测可能提前 flush,改变输出行为 | 低 | 1) 上限设 500MB(远超当前用量) 2) flush 时打 WARNING 日志 |
| CI 分阶段 | P1-9 | 三阶段拆分可能引入 stage 间依赖问题或缓存失效 | 低 | 1) 先并行运行新旧 CI 对比 2) 保留 fallback 到全量串行 |
| 多页面架构 | P3-1 | `st.navigation` 改变状态管理,可能破坏 fragment 隔离和回测播放 | 中 | 1) 先在分支上实验 2) 评估是否真正需要(个人项目单页足够) |
| `np.searchsorted` 替换 | P0-1, P0-2 | 二分查找要求数组严格排序,乱序数据会静默返回错误结果 | 低 | DatetimeIndex 天然排序,添加 `assert np.all(np.diff(arr) >= 0)` 前置检查 |

### 低风险/无需担心的改动

以下改动是纯计算等价变换,零风险:
- P0-4: SQLite PRAGMA 增加(只影响性能,不影响正确性)
- P0-6: 新增指标函数(不修改现有逻辑)
- P0-8: `executemany` 替换循环(语义等价)
- P1-5: auto-refresh 时间戳比较(逻辑等价)
- P1-10: `_hash_array` 移除(Streamlit 内置等价功能)
- P1-12: `pd.Series.ffill()` 替换(语义等价)
- P1-14: Loguru JSON 格式(仅输出格式变化)
- P1-15: `SELECT *` 改显式列(查询语义等价)

---

## 附录: 完整发现索引

### D1 算法

| ID | 发现 | 优先级 |
|----|------|--------|
| A1 | 卡尔曼 Q_mat 循环外预计算 | P3 |
| A2 | EWMA 波动率向量化 | P1 |
| A3 | Schmitt 状态机 Numba JIT | P1 |
| A4 | PnL 前向填充 pd.ffill | P1 |
| A5 | PnL 切片批量填充(消除尾部覆写) | P1 |
| A6 | apply_ema 纯 NumPy | P2 |
| A7 | 跨周期对齐 searchsorted | P0 |
| A8 | 日期查找 searchsorted | P0 |
| A9 | 缓存键哈希过热 | P3 |
| A10 | gradient vs diff | P3 |
| A11 | 卡尔曼滤波 Numba JIT | P2 |
| A12 | 粗糙度 float64 精度 | P3 |
| A13 | 时区后缀缓存 | P3 |
| A14 | polyval 预计算 | P3 |

### D2 数据管道

| ID | 发现 | 优先级 |
|----|------|--------|
| P1 | display cache 缺时间分区 | P3 |
| P2 | 回测输出缺 catalog | P2 |
| P3 | compare_with_db 无 LIMIT | P2 |
| P4 | _query_tf_for_period 无 LIMIT | P3 |
| P5 | Display parquet 无列裁剪 | P3 |
| P6 | ParquetStore merge 全量重读 | P0(合并到 P0-7) |
| P7 | recent rows 逐条 INSERT | P3 |
| P8 | force_update_kline 逐条 DELETE | P0 |
| P9 | 无连接池(设计选择,可接受) | P3 |
| P10 | check_same_thread=False 冗余 | P3 |
| S1 | 启用 mmap | P0(合并到 P0-4) |
| S2 | temp_store=MEMORY | P0(合并到 P0-4) |
| S3 | cache_size 增至 32MB | P0(合并到 P0-4) |
| S4 | display cache 元数据 | P2 |
| S5 | ParquetStore 内存上限 | P0(合并到 P0-7) |
| S6 | 回测批量预取 | P0(合并到 P0-3) |
| S7 | yfinance 增量拉取 | P3 |
| -- | config_db SELECT * | P1 |

### D3 Streamlit

| ID | 发现 | 优先级 |
|----|------|--------|
| -- | _sync_all_cascading 缓存失效 | P0(合并到 P0-5) |
| -- | 4 iframe Plotly.js 重复加载 | P1 |
| -- | _imp_ 双倍存储 | P2 |
| -- | collect_current_params 全量扫描 | P3 |
| -- | _hash_array 自定义 hash | P1 |
| -- | 侧边栏 12 处 st.rerun | P1 |
| -- | auto-refresh time.sleep 阻塞 | P1 |
| -- | cached_strategy_pnl JSON 序列化 | P3 |
| -- | 无 TTL 缓存无界增长 | P3 |
| -- | make_subplots 临时 Figure | P3 |
| -- | 展开/折叠 button 全页 rerun | P1 |
| -- | 多页面架构 | P3 |
| -- | PnL 数据在 session_state | P2 |
| -- | fragment 间 session_state 写入 | P3 |
| -- | 无数据下采样(当前 300 点无需) | P3 |

### D4 回测

| ID | 发现 | 优先级 |
|----|------|--------|
| 2.1.A | 批处理+向量化 | P0(合并到 P0-3) |
| 2.1.B | 预同步数据 | P0(合并到 P0-5) |
| 2.1.C | 施密特 Numba | P1(合并到 P1-1) |
| 2.1.D | 内存缓存 Parquet | P2 |
| 2.2 | 贝叶斯参数优化 | P2 |
| 2.3 | 回测指标补充 | P0 |
| 2.4 | 滑点/佣金建模 | P1 |
| 2.4 | 信号-执行解耦 | P2 |
| 2.5 | 复现性增强 | P3 |
| 2.6 | Walk-Forward 框架 | P3 |

### D5 工程化

| ID | 发现 | 优先级 |
|----|------|--------|
| -- | LICENSE 缺失 | P1 |
| -- | CHANGELOG 缺失 | P1 |
| -- | Makefile 缺失 | P1 |
| -- | .env.example 不全 | P1 |
| -- | 无多环境方案 | P2 |
| -- | 无配置校验(pydantic-settings) | P1 |
| -- | Git tag 命名不规范 | P2 |
| -- | pyproject.toml 版本与 tag 不同步 | P2 |
| -- | CI 无 Docker 构建推送 | P1 |
| -- | 依赖源不统一(requirements.txt vs .lock) | P2 |
| -- | Loguru 非 JSON 格式 | P1 |
| -- | 无 Sentry/错误追踪 | P2 |
| -- | 无 Prometheus metrics | P3 |
| -- | 无 OpenTelemetry | P3 |
| -- | 无应用级 /health | P3 |
| -- | pre-commit 缺 mypy+bandit | P1 |
| -- | 无 .python-version | P3 |
| -- | 无 devcontainer | P3 |
| -- | 无自动化 release workflow | P3 |
| -- | 无 ADR | P3 |
| -- | 测试覆盖率 55% 偏低 | P3 |

### D6 测试

| ID | 发现 | 优先级 |
|----|------|--------|
| -- | 空测试目录(tests/e2e,integration,unit) | P0 |
| -- | CLI 端到端测试缺失 | P0 |
| -- | CI 未分阶段 | P1 |
| -- | 属性测试缺失(hypothesis) | P2 |
| -- | 快照测试缺失(syrupy) | P2 |
| -- | 性能回归阈值硬编码 | P2 |
| -- | 数据 schema 验证缺失 | P2 |
| -- | backtest_core 无独立单元测试 | P2 |
| -- | 并发安全测试不足 | P2 |
| -- | conftest Streamlit mock 过于侵入 | P3 |
| -- | 测试数据工厂分散 | P3 |
| -- | README 引用文件已不存在 | P3 |
| -- | 配置损坏降级测试缺失 | P3 |

---

*本报告由 D1-D6 六维度深度分析汇总而成。每项发现的详细分析和代码级优化方案见各维度原始报告。*
