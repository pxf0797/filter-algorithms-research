# filter_research 使用指南

## 1. 项目概述

filter_research 是一个多时间框架股票滤波分析工具，基于 Streamlit + Plotly 构建交互式 Web 应用，支持：

- **Web 浏览模式**：交互式图表分析，2x2 四视图独立配置，施密特触发器 + 滤波对比 + 预测曲线 + 策略 PnL
- **Web 回测模式**：逐 bar 步进的历史回测，支持自动播放、跨周期级联合成、Pipeline 数据捕获
- **CLI 批量回测**：命令行批量回测，生成 JSONL 事件流 + Parquet/CSV 完整数据，支持断点续跑
- **回测数据分析**：自动指标计算（Sharpe/Sortino/Calmar/MaxDD）、Catalog 历史索引、Optuna 贝叶斯参数优化

核心策略体系：
- 10 种滤波器（SMA / EMA / WMA / ALMA / Savitzky-Golay / Kalman / Butterworth / Gaussian / Median / LOWESS）
- 施密特触发器三态信号（多 +1 / 观望 0 / 空 -1），自适应死区
- 抛物线拟合预测曲线，分段混合策略（保护期止盈止损 + 趋势跟踪期仅止盈）
- 跨周期 PnL 对齐与同向性判断

## 2. 快速开始

### 2.1 环境要求

| 要求 | 说明 |
|------|------|
| Python | 3.11 或 3.12 |
| 包管理 | pip，推荐使用 pyenv / asdf 管理 Python 版本 |
| 系统 | macOS / Linux（Windows 未测试） |
| 网络 | 需要访问 yfinance API 拉取行情数据 |

### 2.2 安装

```bash
git clone git@github.com:pxf0797/filter-algorithms-research.git
cd filter_research

# 安装依赖
make install
# 或: pip install -r requirements.lock
```

### 2.3 配置

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env，可选配置:
#   LOGURU_LEVEL=INFO         日志级别(DEBUG/INFO/WARNING/ERROR)
#   STREAMLIT_SERVER_PORT=8501 Web 端口
#   DB_PATH=data/market.db    股票数据库路径
#   CONFIG_DB_PATH=data/config.db  配置数据库路径
#   PIPELINE_CAPTURE=1         启用回测管道数据采集(可选)
```

### 2.4 启动 Web 应用

```bash
# 方式一: make 命令
make run

# 方式二: 直接使用 streamlit
streamlit run filter/browse/app.py

# 方式三: Docker
docker compose up --build -d
```

浏览器自动打开 `http://localhost:8501`，关闭终端即停止服务。

## 3. 界面布局

```
┌─────────────────────┬──────────────────────────────────────────────┐
│      侧边栏          │                 主区域                       │
│                     ├──────────────────────┬───────────────────────┤
│ 市场选择             │      视图 0          │       视图 1          │
│ 股票代码 + 操作周期   │  (收盘价+滤波+对比)   │   (收盘价+滤波+对比)   │
│ 预设方案             │                      │                       │
│ 滤波器选择           ├──────────────────────┼───────────────────────┤
│ 参数面板(2x2)        │      视图 2          │       视图 3          │
│ 回测模式切换         │  (收盘价+滤波+对比)   │   (收盘价+滤波+对比)   │
│ 导航按钮/进度条       │                      │                       │
│ 数据健康/校验        └──────────────────────┴───────────────────────┘
│ 导入/导出/备份       │  施密特子图 · 预测曲线 · PnL · 跨周期对齐    │
└─────────────────────┴──────────────────────────────────────────────┘
```

## 4. 浏览模式

浏览模式是默认模式，用于交互式图表分析和参数调优。

### 4.1 选择市场和股票

在侧边栏顶部：

1. 选择市场：`美股 US` / `A股(沪深)` / `港股 HK`
2. 输入股票代码：
   - 美股：直接输入代码，如 `AAPL`、`MSFT`
   - A股：输入 6 位代码，如 `600519`（贵州茅台）
   - 港股：输入代码，如 `3690.HK`（美团）
3. 首次输入自动拉取 **全部 8 个周期**（1分钟/5分钟/15分钟/60分钟/日线/周线/月线/季线）数据至本地 SQLite 数据库
4. 选择**操作周期**：从当前 4 个视图中使用的周期中选择，用于 BS 仓位操作标识的基准

> **提示**：数据拉取后缓存在 `data/market.db`，后续访问从本地读取。点击 **刷新数据** 按钮重新拉取。

### 4.2 配置预设方案

预设方案允许保存和快速切换完整参数组合。

**应用预设**：
1. 在侧边栏搜索框中输入股票代码或名称过滤预设
2. 从下拉菜单选择预设
3. 点击 **应用** 按钮

**管理预设**：
- **更新**：用当前参数覆盖选中预设
- **重命名**：修改预设名称
- **删除**：删除选中预设（不可恢复）

**保存预设**：
1. 展开 `保存 / 另存为预设`
2. 输入预设名称和可选描述
3. 勾选 `覆盖` 可覆盖已有预设
4. 点击 **保存**

**配置导入/导出**：
- 导出：侧边栏底部 **导出配置** 按钮，下载 `filter_config.json`
- 导入：拖入 JSON 文件到侧边栏顶部的 **导入配置** 上传区域

### 4.3 选择滤波器

在侧边栏中：

1. **滤波器**下拉菜单：选择主滤波器类型
2. 可选勾选 **双滤波对比**：在同一视图叠加第二个滤波器

支持的 10 种滤波器：

| ID | 名称 | 参数 |
|----|------|------|
| `sma` | 简单移动平均 (SMA) | `window` — 窗口大小 (3-101, 默认 11) |
| `ema` | 指数移动平均 (EMA) | `span` — 跨度 (2-100, 默认 10) |
| `wma` | 加权移动平均 (WMA) | `window` — 窗口大小 (3-101, 默认 11) |
| `alma` | Arnaud Legoux 移动平均 (ALMA) | `window`—窗口, `offset`—偏移, `sigma`—标准差 |
| `savgol` | Savitzky-Golay 滤波 | `window`—窗口(5-101, 默认21), `order`—多项式阶数(1-5, 默认2) |
| `kalman` | 卡尔曼滤波 | `Q`—过程噪声(0.001-1.0), `R`—测量噪声(0.01-10.0) |
| `butterworth` | 巴特沃斯低通滤波 | `order`—阶数(1-8, 默认4), `cutoff`—截止频率(1-45Hz) |
| `gaussian` | 高斯滤波 | `sigma`—标准差(0.5-20, 默认3.0) |
| `median` | 中值滤波 | `window`—窗口大小(3-101, 默认5) |
| `lowess` | LOWESS 平滑 | `frac`—平滑比例(0.01-0.5, 默认0.1) |

### 4.4 视图参数配置

每个视图有独立的参数面板，包含以下配置项：

**基础设置（第一行）**：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| 周期 | 时间框架（1分钟 ~ 季线） | 日线/60分钟/15分钟/5分钟 |
| N | 数据点数 (20-300) | 120 |
| 施密特 | 启用施密特触发器 | 开启 |
| 预测 | 启用预测曲线 | 开启 |

**施密特参数** (`展开` 折叠面板)：

| 参数 | 说明 | 范围 | 默认值 |
|------|------|------|--------|
| k_ε | 灵敏度系数，越小越敏感，死区 ε_t = k_ε * max(σ_t(v), σ_min) | 0.01-0.50 | 0.15 |
| σ_min | 地板保护，防止低波动下死区趋零 | 0.001-0.20 | 0.05 |
| N_EWMA | EWMA 平滑周期，α=2/(N+1)，越大越平滑 | 10-120 | 60 |

> **注意**：实际 bar 数 (N) 必须 >= N_EWMA，否则无法计算施密特信号。

**预测参数**：

| 参数 | 说明 | 范围 | 默认值 |
|------|------|------|--------|
| 预测点数 | 每段预测延伸的 bar 数 | 1-50 | 8 |

**策略参数** (`展开` 折叠面板)：

| 参数 | 说明 |
|------|------|
| 启用策略叠加 | 显示基于预测曲线 + 施密特信号的策略 PnL 曲线 |
| 止损阈值 (%) | 预测偏差超过此阈值即止损离场，范围 0.5-10.0%，默认 2.0% |
| 显示高周期 PnL 参考 | 在本周期 PnL 下方显示紧邻高周期的交易事件标记 |
| 显示同向性判断 | 高周期持仓时，本周期同向 PnL 才计入子图，反之维持不变 |
| 显示实际持仓状态 | 在 PnL 下方以绿/红色标记多空持仓时段 |

**滤波参数颜色**：

每个滤波器对应一个颜色选择器，主滤波器默认 `#00d4aa`（青色），副滤波器默认 `#ff6b6b`（红色）。

### 4.5 图表解读

每个视图的图表包含多个子图（根据开启的功能动态增加）：

| 子图 | 内容 | 说明 |
|------|------|------|
| 价格 | K 线(OHLC) + 收盘价 + 滤波曲线 | 主滤波(青色) + 副滤波(红色) |
| 残差 | 滤波输出与收盘价的差值 | 衡量滤波跟踪精度 |
| 速度 v | 滤波曲线一阶数值梯度 | 反映趋势强弱 |
| 加速度/±ε | 二阶梯度 + 自适应死区 | 死区上下界随波动率动态变化 |
| Sig 信号 | 三态信号 +1(多)/0(观望)/-1(空) | 速度和加速度跃迁判据 |
| PnL 收益 | 策略损益曲线(100=初始本金) | 三角形入场/圆形止盈/叉号止损 |
| 跨周期 PnL | 高周期交易事件映射到当前子图 | 粉色 = 多仓 / 橙色 = 空仓 |
| 同向性判断 | 持仓时段高亮 / 反向变灰 | 验证跨周期信号一致性 |
| 实际持仓 | 绿线 = 做多持仓 / 红线 = 做空持仓 | 实际仓位状态 |

顶部信息栏显示：
- `股票代码·周期 | 现价 ¥XXX.XX`
- `σ=波动率 平滑=二阶差分平方和`
- `N 点`

策略 PnL 信息栏显示（启用策略叠加后）：
- 交易笔数 | 胜率
- 多: +XX% / 空: +XX% / 总和: +XX%
- 最大回撤: 多DD / 空DD

### 4.6 数据管理

**刷新数据**：侧边栏 **刷新数据** 按钮，重新拉取全部 8 个周期。

**自动刷新**：勾选 **自动刷新** 复选框，设置刷新间隔（10-600 秒），自动轮询 yfinance 更新数据。

**数据健康检查**：
1. 展开 `数据健康检查`
2. 点击 **运行检查**
3. 查看完整性报告（缺失值、异常跳变、数据量）

**数据校验**：
对比本地数据库与 yfinance 源数据，发现差异：

1. 展开 `数据校验`
2. 点击 **校验全部周期**
3. 查看对比结果：
   - 黄底 = 历史数据被修正（冲突）
   - 绿底 = 有新增数据
   - 白底 = 一致
4. 点击 **更新全部有差异的周期** 一键同步

**数据库备份与恢复**：
1. 展开 `数据备份与恢复`
2. 创建备份：点击 **创建备份**，自动保留最近 5 个
3. 恢复备份：选择备份，点击 **恢复到此备份**
4. 删除备份：选择备份，点击 **删除此备份**

**数据库导入/导出**：
1. 展开 `数据库导入/导出`
2. 导出：点击 **导出数据库** 下载 `market.db`
3. 导入：拖入 `.db` 文件，自动校验格式后替换

### 4.7 配置历史

展开 `配置历史` 可查看最近的参数变更记录，包括时间、来源（UI/导入/预设应用/回退）。

## 5. 回测模式 (Web)

### 5.1 进入回测模式

在侧边栏 **回测模式** 区域，选择 `回测模式`：

1. 系统自动确定最小周期（从 4 个视图中取最精细的）
2. 查询该周期在数据库中的总 bar 数
3. 初始化窗口位置到末尾（最新 bar）
4. 图表切换为回测窗口渲染模式

### 5.2 导航控制

回测模式下侧边栏显示导航控件：

```
⏮  ◀  ▶/⏸  ⏵  ⏭  速度▼
```

| 按钮 | 功能 | 快捷键说明 |
|------|------|------------|
| ⏮ | 跳到开头 | 窗口起始位置 = min(n_pts) |
| ◀ | 后退一个 bar | 窗口左移 |
| ▶ | 开始播放 | 自动逐 bar 前进 |
| ⏸ | 暂停播放 | 停止自动前进 |
| ⏵ | 前进一个 bar | 窗口右移 |
| ⏭ | 跳到末尾 | 窗口结束位置 = 最新 bar |

播放速度选项：`0.25x` / `0.5x` / `1x` / `2x` / `5x` / `10x`（1x = 每秒 1 bar）。

**窗口进度滑块**：直接拖动滑块跳转到任意 bar 位置。

### 5.3 窗口信息

侧边栏显示当前窗口范围：
```
📊 显示 bar {win_start} ~ {bar_index} / {total_bars}
```

图表自动根据 `bar_index` 对应的 `cutoff_date` 截断数据，仅显示截止日期之前的数据。

### 5.4 Pipeline 数据捕获

设置环境变量 `PIPELINE_CAPTURE=1` 可启用回测过程的逐步数据采集，捕获各视图的管道阶段输出（滤波、施密特、信号对、交易记录、PnL、持仓掩码、BS 标记）到 `data/pipeline_captures/` 目录。

## 6. CLI 批量回测

CLI 回测适用于批量运行、参数扫描和脚本化分析。

### 6.1 基本用法

```bash
# 模块方式运行
python -m filter.backtest.cli --ticker 03690.HK --preset 3690_HK_DP

# 从配置文件加载
python -m filter.backtest.cli --ticker AAPL --config-file my_config.json

# 指定 bar 范围
python -m filter.backtest.cli --ticker 03690.HK --preset 3690_HK_DP \
    --start-bar 500 --end-bar 1000

# 只运行单个视图
python -m filter.backtest.cli --ticker 03690.HK --preset 3690_HK_DP \
    --view-filter v0_日线

# 静默模式
python -m filter.backtest.cli --ticker 03690.HK --preset 3690_HK_DP --quiet
```

### 6.2 命令行参数

| 参数 | 必填 | 说明 | 默认值 |
|------|------|------|--------|
| `--ticker` | 是 | 股票代码，如 `03690.HK`, `AAPL` | -- |
| `--preset` | 二选一 | 预设配置名称（从 config_db 加载） | -- |
| `--config-file` | 二选一 | JSON 配置文件路径 | -- |
| `--start-bar` | 否 | 起始 bar 索引 | min(n_pts) |
| `--end-bar` | 否 | 结束 bar 索引 | 总 bar 数 |
| `--output-dir` | 否 | 输出根目录 | `./backtest_output/` |
| `--no-save-data` | 否 | 不保存 Parquet/CSV，仅 JSONL | -- |
| `--step-interval` | 否 | 步进间隔（每隔 N 个 bar 采样） | 1 |
| `--resume` | 否 | 从断点文件恢复回测 | -- |
| `--checkpoint-interval` | 否 | 断点自动保存间隔（bar 数），0=禁用 | 100 |
| `--view-filter` | 否 | 只运行指定视图，如 `v0_日线` | -- |
| `--quiet` | 否 | 静默模式，只打印开始和结束信息 | -- |

### 6.3 配置来源

**预设配置** (`--preset`)：
从 `data/config.db` 中读取已保存的预设，自动解析为 4 视图配置。

基本配置流程：
1. 先在 Web 应用中调好参数并保存为预设
2. CLI 中通过 `--preset <名称>` 引用

**JSON 配置文件** (`--config-file`)：
支持两种 JSON 格式：

格式 1 — 包含 `configs` 或 `view_configs` 列表：
```json
{
  "configs": [
    {
      "_fid": "savgol", "_dual": true, "_fid2": "ema",
      "tf": "日线", "n_pts": 120,
      "show_sch": true, "ke": 0.15, "sm": 0.05, "ew": 60,
      "show_pred": true, "fit_mode": "parabola", "n_ext": 8,
      "show_strategy": true, "stop_loss_pct": 2.0,
      "pv": {"window": 21, "order": 2},
      "pv2": {"span": 10}
    }
  ]
}
```

格式 2 — 平铺预设参数字典（与导出格式兼容）：
```json
{
  "market": "港股 HK", "ticker": "3690.HK",
  "global_f": "savgol", "global_dual": true, "global_f2": "ema",
  "v0_tf": "日线", "v0_n": 120, ...
}
```

**默认配置** (无 `--preset` 和 `--config-file`)：
使用 savgol + ema 双滤波、日线/60分钟/15分钟/5分钟四个周期的默认配置。

### 6.4 Bar 范围规则

- `--start-bar` 不指定时，自动取所有视图中最小的 `n_pts`
- `--end-bar` 不指定时，自动取数据库中的总 bar 数
- 必须满足 `start < end`
- 实际的 bar_index 范围是 `[start, end)`，步进为 `step_interval`

### 6.5 输出文件

每次回测在 `backtest_output/{ticker}_{session_id}/` 下生成：

| 文件 | 格式 | 内容 |
|------|------|------|
| `events.jsonl` | JSONL | 每步骤的完整事件，包含所有视图管道输出 |
| `bs_snapshot.jsonl` | JSONL | 每步骤的 BS 买卖标记变化 |
| `schmitt_snapshot.jsonl` | JSONL | 每步骤的施密特触发器快照 |
| `filter_tail.jsonl` | JSONL | 每步骤的滤波尾部数据 |
| `trade_summary.jsonl` | JSONL | 每步骤的交易记录汇总 |
| `metadata.json` | JSON | 会话元数据（配置、时间、步骤数） |
| `{ticker}_full.csv` | CSV | 逐 bar 完整快照（含 FFill 前向填充） |

若未使用 `--no-save-data`，还会生成 Parquet 格式的完整数据。

运行完成后自动：
- 计算回测核心指标（Sharpe/Sortino/Calmar/MaxDD 等）并记录日志
- 更新 `backtest_output/.catalog.json` 索引

### 6.6 断点续跑

大型回测中断后可从中断点恢复：

```bash
# 运行回测时自动保存断点（每 100 bar）
python -m filter.backtest.cli --ticker 03690.HK --preset my_preset \
    --checkpoint-interval 100

# 从断点恢复
python -m filter.backtest.cli --ticker 03690.HK --preset my_preset \
    --resume backtest_output/03690.HK_checkpoint.json
```

断点文件包含：
- 已完成的 bar_index
- 跨窗口 EWMA 状态（确保信号连续性）
- 配置哈希（校验配置一致性，不匹配则拒绝恢复）

## 7. 回测数据分析

### 7.1 自动指标计算

CLI 回测完成后自动计算以下指标并记录日志：

| 指标 | 说明 |
|------|------|
| `total_return_pct` | 总收益率 (%) |
| `sharpe_ratio` | 夏普比率（假设无风险利率 3%） |
| `sortino_ratio` | 索提诺比率（仅下行波动率） |
| `calmar_ratio` | 卡玛比率（年化收益 / 最大回撤） |
| `max_drawdown_pct` | 最大回撤 (%) |
| `max_drawdown_duration` | 最长连续水下 bar 数 |
| `annualized_return_pct` | 年化收益率（假设 252 交易日） |
| `annualized_volatility_pct` | 年化波动率 |
| `win_rate_pct` | 胜率 (%) |
| `profit_factor` | 盈亏比 |
| `total_trades` | 总交易次数 |
| `avg_trade_return_pct` | 平均每笔交易收益率 |
| `avg_win_pct` / `avg_loss_pct` | 平均盈利/亏损百分比 |

### 7.2 BacktestCatalog 索引

`BacktestCatalog` 类扫描 `backtest_output/` 目录，维护 `.catalog.json` 索引文件。

```python
from filter.backtest.catalog import BacktestCatalog

catalog = BacktestCatalog()

# 查询所有回测会话
sessions = catalog.scan()

# 按 ticker 过滤
results = catalog.query(ticker="AAPL")

# 手动保存索引
catalog.save_index()
```

每个会话条目包含：路径、ticker、session_id、开始/结束时间、状态、步骤数、视图列表。

### 7.3 Optuna 参数优化

使用贝叶斯优化自动搜索最优参数：

```python
from filter.backtest.optimizer import suggest_params, create_study, optimize_backtest_params

# 定义参数搜索空间
param_space = {
    "ke": {"type": "float", "low": 0.01, "high": 0.50, "log": False, "step": 0.01},
    "ew": {"type": "int", "low": 10, "high": 120, "step": 1},
    "window": {"type": "int", "low": 5, "high": 101, "step": 2},
    "stop_loss_pct": {"type": "float", "low": 0.5, "high": 10.0, "step": 0.1},
}

def objective(trial):
    params = suggest_params(trial, param_space)
    # ... 使用 params 运行回测 ...
    return sharpe_ratio  # 最大化夏普比率

# 运行优化
best_params, best_value = optimize_backtest_params(
    objective, n_trials=100, n_jobs=1, direction="maximize",
)
```

可用采样器：
- `TPESampler` — Tree-structured Parzen Estimator（默认，贝叶斯优化）
- `RandomSampler` — 随机搜索
- `CmaEsSampler` — CMA-ES 进化算法

可用剪枝器：
- `MedianPruner` — 中位数剪枝（默认）
- `HyperbandPruner` — Hyperband 剪枝
- `PercentilePruner` — 百分位剪枝

## 8. 配置说明

### 8.1 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LOGURU_LEVEL` | 日志级别 | `INFO` |
| `STREAMLIT_SERVER_PORT` | Web 服务端口 | `8501` |
| `STREAMLIT_SERVER_ADDRESS` | 绑定地址 | `0.0.0.0` |
| `DB_PATH` | 股票数据库路径 | `data/market.db` |
| `CONFIG_DB_PATH` | 配置数据库路径 | `data/config.db` |
| `BACKTEST_MAX_BARS` | 回测最大 bar 数 | `100000` |
| `BACKTEST_PARALLEL_VIEWS` | 是否启用多视图并行计算 | `1` (启用) |
| `YFINANCE_TIMEOUT` | yfinance API 超时(秒) | `30` |
| `PIPELINE_CAPTURE` | 启用回测 Pipeline 数据捕获 | 空 (禁用) |
| `SENTRY_DSN` | Sentry 错误追踪 DSN | 空 |
| `SLACK_WEBHOOK_URL` | Slack 告警 Webhook | 空 |
| `ENV` | 运行环境 (`development` / `production`) | `development` |

### 8.2 ViewConfig 字段说明

每个视图的配置字典包含以下字段：

| 字段 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `_fid` | str | 主滤波器 ID（如 `savgol`, `ema`） | `sma` |
| `_dual` | bool | 是否启用双滤波对比 | `False` |
| `_fid2` | str | 副滤波器 ID | `None` |
| `tf` | str | 周期名称（见下） | `日线` |
| `n_pts` | int | 数据点数 (20-300) | `120` |
| `show_sch` | bool | 启用施密特触发器 | `True` |
| `ke` | float | k_ε 灵敏度系数 (0.01-0.50) | `0.15` |
| `sm` | float | σ_min 地板保护 (0.001-0.20) | `0.05` |
| `ew` | int | N_EWMA 平滑周期 (10-120) | `60` |
| `show_pred` | bool | 启用预测曲线 | `True` |
| `n_ext` | int | 预测延伸点数 (1-50) | `8` |
| `fit_mode` | str | 拟合模式（固定 `parabola`） | `parabola` |
| `show_strategy` | bool | 启用策略 PnL 叠加 | `False` |
| `stop_loss_pct` | float | 止损阈值 (%) | `2.0` |
| `show_cross_pnl` | bool | 显示高周期 PnL 参考 | `False` |
| `show_alignment` | bool | 显示同向性判断 | `False` |
| `show_pnl_feedback` | bool | 显示实际持仓状态 | `False` |
| `fc` | str | 主滤波曲线颜色 (hex) | `#00d4aa` |
| `fc2` | str | 副滤波曲线颜色 (hex) | `#ff6b6b` |
| `pv` | dict | 主滤波器参数字典 | `{}` |
| `pv2` | dict | 副滤波器参数字典 | `{}` |

### 8.3 周期层级

| 周期 | yfinance interval | 获取范围 | TF_HIERARCHY（紧邻高周期） |
|------|-------------------|----------|---------------------------|
| 1分钟 | 1m | 7d | 5分钟 |
| 5分钟 | 5m | 60d | 15分钟 |
| 15分钟 | 15m | 60d | 60分钟 |
| 60分钟 | 1h | 730d | 日线 |
| 日线 | 1d | max | 周线 |
| 周线 | 1wk | max | 月线 |
| 月线 | 1mo | max | 季线 |
| 季线 | 3mo | max | None |

### 8.4 数据库结构

| 数据库文件 | 用途 | 表 |
|------------|------|-----|
| `data/market.db` | 股票 K 线数据 | `kline` (ticker, timeframe, ts, open, high, low, close, volume) |
| `data/config.db` | 配置预设与历史 | `config_presets`, `config_history` |

数据缓存路径：
- `data/display/{ticker}/{tf}.parquet` — 显示用缓存（窗口数据）
- `data/snapshots/` — 数据库备份快照

## 9. 数据获取 (Python API)

### 9.1 增量拉取

```python
from filter.data.fetcher import fetch_incremental

# 增量拉取：只获取 DB 中最新日期之后的数据
t, close, ohlc, ticker_full, dates, err = fetch_incremental(
    "美股 US", "AAPL", "日线", n_pts=120,
)
```

### 9.2 批量拉取全部周期

```python
from filter.data.loader import _fetch_all_timeframes

# 并行拉取全部 8 个周期数据，写入 DB
results = _fetch_all_timeframes("港股 HK", "3690.HK")
for tf, (ok, detail) in results.items():
    print(f"{tf}: {'OK' if ok else detail}")
```

### 9.3 数据同步到显示缓存

```python
from filter.data.loader import _sync_to_display, load_display_cache

# 同步到 Parquet 显示缓存
ok, count = _sync_to_display("AAPL", "日线", n_pts=120)

# 读取缓存
df = load_display_cache("AAPL", "日线")
```

## 10. 测试

```bash
# 运行全部测试
make test

# 运行快照测试
make test-snapshots

# 更新快照
make snapshot-update

# 代码检查
make lint          # ruff 代码检查
make format        # ruff 格式化
make mypy          # 类型检查
make bandit        # 安全检查

# 一键全检
make check

# 覆盖率报告
pytest tests/ --ignore=tests/test_app_ui.py --cov=filter --cov-report=term
```

## 11. 常见问题

**Q: 启动 Web 应用报错 "No module named 'streamlit_app'"？**
A: 入口文件已从 `streamlit_app.py` 重构为 `browse/app.py`。请使用 `streamlit run filter/browse/app.py`。

**Q: 切换股票后数据没有更新？**
A: 首次输入新代码会自动拉取。如需重新拉取，点击侧边栏 **刷新数据** 按钮，或开启 **自动刷新**。

**Q: 施密特信号没有显示（显示警告）？**
A: 数据点数 (N) 必须大于 N_EWMA 参数。请增大 N 或降低 N_EWMA。公式：N >= N_EWMA。

**Q: 策略没有开仓（PnL 始终 100）？**
A: 策略需要有效的预测曲线和 Sig 信号。请确认已开启 **预测** 复选框和 **策略叠加**，且滤波器输出无明显异常。

**Q: 高周期 PnL 参考子图为空？**
A: 需要先在高周期视图（如日线）开启策略 PnL，再到低周期视图开启 **显示高周期 PnL 参考**。

**Q: 回测模式下图表不更新？**
A: 确认数据库中有该 ticker 的数据。点击 **跳到开头/末尾** 按钮初始化窗口位置。若数据不足，增大 n_pts 或检查数据健康状态。

**Q: CLI 回测报 "ticker 无数据"？**
A: CLI 回测需要先在 Web 应用中拉取数据（或通过 Python API 获取），CLI 本身不从 yfinance 实时拉取。

**Q: 断点恢复报 "配置哈希不匹配"？**
A: 断点文件与当前使用的配置文件/预设存在差异。请使用相同的配置重新运行，或删除断点文件后重新开始。

**Q: 如何导出当前图表？**
A: Plotly 图表右上角工具栏支持缩放、平移、框选缩放，以及下载为 PNG。

**Q: 如何批量对比多种参数组合？**
A: 使用预设方案保存不同参数组合，通过预设下拉菜单快速切换。也可以导出为 JSON 文件后在外部编辑再导入。

## 12. 项目结构

```
filter_research/
├── filter/                    # 应用主目录
│   ├── browse/                    # 浏览模式
│   │   ├── app.py                 # 入口文件 (main)
│   │   ├── sidebar.py             # 侧边栏 UI
│   │   ├── charts.py              # Plotly 图表渲染
│   │   ├── chart_builder.py       # 子图构建
│   │   └── components_sidebar.py  # 参数面板组件
│   ├── backtest/                  # 回测模块
│   │   ├── cli.py                 # CLI 入口
│   │   ├── engine.py              # 回测引擎 (BacktestRunner)
│   │   ├── panel.py               # Web 回测面板
│   │   ├── recorder.py            # 事件记录 (EventRecorder)
│   │   ├── pipeline.py            # Pipeline 数据捕获
│   │   ├── metrics.py             # 核心指标计算
│   │   ├── catalog.py             # 回测索引管理
│   │   └── optimizer.py           # Optuna 参数优化
│   ├── engine/                    # 计算引擎
│   │   ├── filters.py             # 10 种滤波算法 + 施密特信号 + 策略 PnL
│   │   ├── pipeline.py            # 管道编排 (compute_filters 等)
│   │   └── signals.py             # BS 买卖标记生成
│   ├── data/                      # 数据层
│   │   ├── db.py                  # SQLite 数据层 (market.db)
│   │   ├── config_db.py           # 配置管理 (config.db)
│   │   ├── fetcher.py             # yfinance 数据拉取
│   │   ├── loader.py              # 数据加载与缓存同步
│   │   ├── store.py               # ParquetStore 持久化
│   │   └── synth.py               # 合成数据
│   └── shared/                    # 共享模块
│       ├── constants.py           # 全局常量 (ALL_TFS, TF_INTERVAL 等)
│       └── state.py               # AppState 会话状态管理
├── tests/                         # 测试目录
├── config/                        # JSON 预设文件
├── data/                          # 运行数据 (DB, Parquet, 快照)
├── docs/                          # 文档
├── backtest_output/               # CLI 回测输出
├── tools/                         # 工具脚本
├── Makefile                       # 构建与任务定义
├── Dockerfile                     # Docker 镜像
├── docker-compose.yml             # Docker Compose 编排
└── requirements.lock              # 锁定的依赖版本
```

---

*最后更新: 2026-07-24*
