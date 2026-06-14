# 多周期股票滤波回测工具 — 设计方案

> 版本: v1.0 | 日期: 2026-06-14 | 状态: 待审查

---

## 1. 总体方案概述

### 一句话目标

**在现有 Streamlit 多周期滤波分析工具上，新增基于施密特触发器信号的量化回测功能，支持多周期时间对齐的历史回放和绩效分析。**

### 与现有工具的集成关系

```
┌──────────────────────────────────────────────────────────────────┐
│                    Streamlit App (streamlit_app.py)               │
│                                                                   │
│  ┌─────────────┐    ┌──────────────────────────────────────────┐ │
│  │   Sidebar    │    │          主区域 (2x2 视图网格)            │ │
│  │             │    │  ┌──────────────┐ ┌──────────────┐       │ │
│  │ 市场/代码   │    │  │  视图1       │ │  视图2       │       │ │
│  │ 滤波器选择  │    │  │  K线+滤波+   │ │  K线+滤波+   │       │ │
│  │ 参数滑块    │    │  │  Schmitt     │ │  Schmitt     │       │ │
│  │────新增────│    │  └──────────────┘ └──────────────┘       │ │
│  │ 回放控制 ▤ │    │  ┌──────────────┐ ┌──────────────┐       │ │
│  │ ⏮⏪▶⏩⏭■  │    │  │  视图3       │ │  视图4       │       │ │
│  │ 速度: 1x   │    │  │  K线+滤波+   │ │  K线+滤波+   │       │ │
│  │ [────●───] │    │  │  Schmitt     │ │  Schmitt     │       │ │
│  │ 日期滑块   │    │  └──────────────┘ └──────────────┘       │ │
│  │────新增────│    │                                           │ │
│  │ 回测绩效   │    │  ┌──────────────────────────────────────┐ │ │
│  │ Sharpe     │    │  │  回测绩效面板 (新增底部折叠区)       │ │ │
│  │ MaxDD      │    │  │  Sharpe │ MaxDD │ WinRate │ ...      │ │ │
│  │ ...        │    │  └──────────────────────────────────────┘ │ │
│  └─────────────┘    └──────────────────────────────────────────┘ │
│                                                                   │
│  ┌────────────────────── 新增模块 ──────────────────────────────┐ │
│  │  backtest_engine.py    │  backtest_timeline.py   │  cache.py  │ │
│  │  (回测引擎封装)         │  (多周期时间对齐)        │  (缓存层) │ │
│  └──────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

### 核心设计原则

1. **零侵入现有代码** — 现有 `streamlit_app.py` 的 filter/Schmitt/渲染逻辑完全不动；新增功能通过模块导入实现
2. **Schmitt 信号即接口** — 回测引擎的输入就是 `Sig_t` 数组（+1/0/-1），Schmitt 逻辑与回测彻底解耦
3. **Streamlit rerun 即回放时钟** — 复用现有 `auto_refresh` + `st.rerun()` 机制作为回放引擎，零 JS 开销
4. **渐进式实施** — 三阶段路线：MVP 单周期回测 → 多周期对齐 → 绩效分析面板，每阶段独立可交付

---

## 2. 技术选型结论

### 最终选型表

| 领域 | 选型 | 角色 | 核心理由 |
|------|------|------|----------|
| 美股数据源 | **yfinance** (保留) | 美股 OHLCV | 现有代码已集成，无需改动 |
| A股/港股数据源 | **efinance** (新增) | A股/港股 OHLCV | 速度比 yfinance 快 10-25 倍，专为中国市场设计 |
| 回测引擎 | **vectorbt** (新增) | 信号→权益曲线 | `Portfolio.from_signals()` 直接接收 Schmitt 信号数组，纯函数无副作用，天然兼容 Streamlit rerun |
| 降级回测引擎 | **自研 pandas** (~80行) | 备选方案 | 无外部依赖，信号→持仓→收益→权益曲线，逻辑透明可控 |
| 缓存引擎 | **Parquet 分文件** + `@st.cache_data` | 数据持久化+内存缓存 | Parquet 按 ticker/tf 分文件，支持增量更新；`@st.cache_data` 作 L1 内存层 |
| 序列化 | **pyarrow** (新增) | Parquet 读写 | Parquet 必需依赖，已是 Pandas 生态标配 |

### 新增依赖清单 (requirements.txt 追加)

```
# 回测与数据源
vectorbt>=0.5.0          # 回测引擎
efinance>=0.5.0          # A股/港股数据源
pyarrow>=14.0.0          # Parquet 序列化

# 已有依赖 (不动)
streamlit>=1.28.0
numpy>=1.24.0
scipy>=1.10.0
plotly>=5.15.0
pandas>=2.0.0
statsmodels>=0.14.0
```

### 降级说明

- **efinance 不可用时**: 回退到 yfinance（但 A股/港股速度慢 10-25 倍），通过 try/except 自动切换
- **vectorbt 不可用时**: 自动切换到 `backtest_engine.py` 内置的自研 pandas 引擎（~80 行），功能子集但保证可运行
- **pyarrow 不可用时**: 回退到 CSV 缓存，速度略慢但可用

---

## 3. 系统架构设计

### 整体架构图 (组件 + 数据流)

```
                        ┌─────────────────────┐
                        │   streamlit_app.py   │
                        │   (现有，基本不动)    │
                        └──────┬──────┬───────┘
                               │      │
              ┌────────────────┘      └────────────────┐
              ▼                                         ▼
┌─────────────────────────┐               ┌─────────────────────────┐
│   backtest_timeline.py  │               │    backtest_engine.py   │
│   (多周期时间对齐)        │               │    (回测引擎封装)        │
│                         │               │                         │
│  · build_timeline()     │               │  · run_backtest()       │
│  · slice_at_decision()  │               │  · compute_metrics()    │
│  · validate_no_leak()   │               │  · _simple_backtest()   │
└───────────┬─────────────┘               └───────────┬─────────────┘
            │                                         │
            │  ┌──────────────────────┐               │
            └──►     cache.py         ◄───────────────┘
               │  (数据缓存层)         │
               │                      │
               │  · get_ohlcv()       │
               │  · save_schmitt()    │
               │  · load_schmitt()    │
               └──────────┬───────────┘
                          │
            ┌─────────────┼─────────────┐
            ▼             ▼             ▼
     ┌──────────┐  ┌──────────┐  ┌──────────┐
     │ yfinance │  │ efinance │  │  Parquet │
     │  (美股)   │  │ (A/港股) │  │   Files  │
     └──────────┘  └──────────┘  └──────────┘
```

**数据流简述:**

1. 用户在 sidebar 点击"进入回测模式" → `st.session_state._backtest_mode = True`
2. `cache.py` 按各视图配置拉取数据（yfinance/efinance），缓存到 Parquet 文件 + `@st.cache_data`
3. `streamlit_app.py` 现有逻辑计算各视图的 Schmitt 信号 `Sig_t`
4. `backtest_timeline.py` 构建统一时间轴，按可见性规则切片各视图信号
5. `backtest_engine.py` 用 vectorbt 或自研引擎计算权益曲线和绩效指标
6. Sidebar 显示回放控制面板，4 个图表视图同步显示历史切片 + 垂直时间线

### 模块划分

| 文件 | 类别 | 职责 | 预估行数 |
|------|------|------|----------|
| `streamlit_app.py` | **现有复用** | UI 框架、filter、Schmitt、渲染。新增 ~140 行回放控制逻辑 | +140 |
| `backtest_engine.py` | **新增** | vectorbt 封装、降级自研引擎、绩效指标计算 | ~120 |
| `backtest_timeline.py` | **新增** | 统一时间轴构建、可见性切片、防未来函数验证 | ~100 |
| `cache.py` | **新增** | Parquet 读写、`@st.cache_data` 包装、efinance 接入 | ~80 |
| `requirements.txt` | **修改** | 追加 3 个依赖 | +3 |

### 现有代码改动范围 (streamlit_app.py)

改动点仅限于:
1. **Sidebar 新增 section**: 回放控制面板（~40 行）
2. **`main()` 函数新增回测模式分支**: 在 `_render_chart` 前插入数据切片逻辑（~60 行）
3. **`_render_chart` 新增 `slice_to` 参数**: 支持图表数据截止到指定索引 + 垂直时间线（~40 行）
4. **Session State 初始化**: 新增 `_backtest_*` 键值（~10 行）

**绝对不动**的部分: `FILTERS` 注册表、所有 `apply_*` 滤波函数、`_schmitt_trigger`、`_fetch_stock`、`_render_params`、`_render_plotly`

---

## 4. 多周期时间对齐方案

### 时间戳规则速查表

| K线周期 | yfinance 时间戳含义 | 示例 |
|---------|-------------------|------|
| 1分钟 | K线起始时刻 | 09:30 对应 09:30-09:31 这根K线 |
| 5分钟 | K线起始时刻 | 09:30 对应 09:30-09:35 |
| 15分钟 | K线起始时刻 | 09:30 对应 09:30-09:45 |
| 60分钟 | K线起始时刻 | 09:30 对应 09:30-10:30 |
| 日线 | 周期首日 00:00 (UTC) | 2024-01-02 00:00 代表该交易日 |
| 周线 | 周期首日 00:00 (UTC) | 2024-01-01 00:00 (周一) |
| 月线 | 首日 00:00 | 2024-01-01 00:00 |
| 季线 | 首日 00:00 | 2024-01-01 00:00 |

### 数据可见性规则

核心规则: **`bar_end <= t`** — 一根K线的数据只有在它完全形成后才能被"看见"。

```
时间轴:  ...  |────K_t────|────K_{t+1}────| ...
                               ↑
                          bar_end
          此时间点可以"看见" K_t 及之前所有已完成的K线
          K_{t+1} 尚未完成，不可见
```

对分钟线: `bar_end = timestamp + 1/5/15/60分钟`
对日线及以上: `bar_end = timestamp + 1天/周/月/季` — 但实践中日线数据在收盘后才有，所以日线 timestamp 当天收盘前不可用

### 方案C: 统一时间轴设计（推荐方案）

**核心理念**: 以回测决策周期为时间轴，各周期数据独立存储，决策点按可见性规则切片。

```
                       统一时间轴 (决策周期: 日线)
         t=0    t=1    t=2    t=3    t=4    t=5    ...
         ├──────┼──────┼──────┼──────┼──────┼──────┤
日线:    D0     D1     D2     D3     D4     D5      (每个决策点有对应日线K)
60分钟:  H0-H7 H8-H15 H16-H23 ...                   (每天8根60分钟K只在当天收盘后可见)
5分钟:   80根/天                                      (按可见性规则切片)
```

**实现方式** (`backtest_timeline.py`):

```python
def build_timeline(decision_tf: str, data_dict: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]):
    """
    data_dict: {"日线": (t_arr, sig_arr, timestamps), "60分钟": (...), ...}
    返回: unified_decisions: list of {idx, timestamp, signals: {tf: sig_value}}
    """
    # 1. 以决策周期的时间戳为主轴
    # 2. 对每个决策点，遍历各周期数据，按 bar_end <= decision_ts 切片
    # 3. 未更新的周期使用前一条已完成K的值（天然前向填充）
    ...
```

**优势**:
- 天然前向填充: 大周期信号未更新时，自动沿用上一已完成K线的信号，无需额外插值
- 决策逻辑清晰: 每个决策点明确知道"此时刻我能看到的各周期信号是什么"
- 回放直观: 时间轴就是决策周期，用户看到的就是"那一瞬间的可用信息"

### 防未来函数机制 (三层防护)

| 层 | 机制 | 说明 |
|----|------|------|
| **L1 数据截止** | `_fetch_stock` 按 `n_pts` 截断，且 `bar_end <= t` 规则保证未形成K线不可见 | 数据层 |
| **L2 可见性切片** | `slice_at_decision()` 严格按 `timestamp <= decision_ts` 过滤 | 信号层 |
| **L3 交叉验证** | `validate_no_leak()` 对比"完整回测结果"与"逐日滚动回测结果"，差异必须为 0 | 验证层 |

L3 实现: 对回测区间每一天，模拟从起始日到该天的子回测，确保权益曲线与完整回测在该点一致。不一致 = 存在未来函数泄漏。

---

## 5. 时间回放交互设计

### 推荐方案: 方案D (混合方案) — slider 粗调 + 按钮微调 + st.rerun 回放时钟

**核心理由**: 现有 `auto_refresh` 的 `st.rerun()` 机制天然就是回放引擎。回放本质上就是"每隔 N 秒，把数据切片索引+1，然后 rerun 刷新所有图表"。

### 回放状态机

```
                    ┌──────────┐
          进入回测 → │   IDLE   │ ← 停止(■)
                    └────┬─────┘
                         │ 播放(▶)
                         ▼
                    ┌──────────┐
              ┌────→│ PLAYING  │
              │     └────┬─────┘
              │          │ 暂停(⏸)
              │          ▼
              │     ┌──────────┐
              └─────│ PAUSED   │──────→ IDLE (停止■)
              恢复(▶)└──────────┘
```

**状态转换规则**:
- `IDLE → PLAYING`: 用户点击 ▶ (play) 按钮
- `PLAYING → PAUSED`: 用户点击 ⏸ (pause) 按钮
- `PAUSED → PLAYING`: 用户点击 ▶ (play) 按钮
- `PLAYING/PAUSED → IDLE`: 用户点击 ■ (stop) 按钮，重置到第 0 个决策点

### Session State 完整键值设计

```python
# ── 回测模式 (所有键以 _backtest 开头，避免与现有键冲突) ──

st.session_state._backtest_mode = False          # bool: 是否进入回测模式
st.session_state._backtest_is_playing = False     # bool: 是否正在播放 (PLAYING state)
st.session_state._backtest_is_paused = False      # bool: 是否暂停 (PAUSED state)
st.session_state._backtest_current_idx = 0        # int: 当前决策点索引 (0..N-1)
st.session_state._backtest_total_steps = 0        # int: 决策点总数
st.session_state._backtest_speed = 1              # int: 回放速度倍数 (1/2/5/10)
st.session_state._backtest_last_step_time = 0.0   # float: 上一步的 time.time()
st.session_state._backtest_signals = {}           # dict: {tf: np.ndarray} 各周期信号
st.session_state._backtest_equity = None          # np.ndarray: 权益曲线 (用于绩效显示)
st.session_state._backtest_metrics = None         # dict: 绩效指标

# ── 导出给 _render_chart 的切片参数 ──
st.session_state._backtest_slice_to = None        # int or None: 图表数据显示截止索引
```

**命名约定**: `_backtest_*` 前缀的下划线表示 internal/transient 状态（Streamlit 惯例），不会被导出配置 JSON 包含。

### UI 布局图

```
Sidebar 回测控制区 (新增 section)
┌──────────────────────────────────────┐
│  ═══ 回测控制 ═══          [进入回测] │  ← 按钮切换 _backtest_mode
│                                      │
│  ┌──── 回放控制 ────────────────┐    │  ← 进入回测模式后才显示
│  │                              │    │
│  │  ⏮  ⏪  ▶  ⏩  ⏭  ■         │    │  ← 6个按钮: 首帧/退1帧/播放/进1帧/末帧/停止
│  │                              │    │
│  │  速度: [1x] [2x] [5x] [10x] │    │  ← 4个速度按钮，选中的高亮
│  │                              │    │
│  │  ●═══════════════●────────── │    │  ← st.slider 当前帧滑块
│  │  2024-03-15                  │    │  ← 当前决策点日期
│  │  第 42 / 250 步              │    │  ← 进度文字
│  └──────────────────────────────┘    │
│                                      │
│  ┌──── 绩效摘要 ────────────────┐    │  ← 仅在回测区间完整后显示 (到末尾或手动计算)
│  │  Sharpe:  1.85               │    │
│  │  MaxDD:  -12.3%              │    │
│  │  WinRate: 58.2%              │    │
│  │  年化收益: 22.7%             │    │
│  └──────────────────────────────┘    │
│                                      │
│  [导出回测报告 (CSV)]                │  ← 下载按钮
└──────────────────────────────────────┘

主区域 (4视图同步回放)
┌──────────────────────┬──────────────────────┐
│  视图1 · 日线          │  视图2 · 60分钟        │
│  ┌──────────────────┐ │  ┌──────────────────┐ │
│  │  K线 + 滤波       │ │  │  K线 + 滤波       │ │
│  │  ▎ ← 垂直时间线    │ │  │  ▎                │ │
│  │  Sig_t: +1 (多)   │ │  │  Sig_t: 0 (观望)  │ │
│  └──────────────────┘ │  └──────────────────┘ │
│  权益截止此点: ¥108.5K │  信号: 20/250 (8%)    │
├──────────────────────┼──────────────────────┤
│  视图3 · 15分钟        │  视图4 · 5分钟          │
│  ┌──────────────────┐ │  ┌──────────────────┐ │
│  │  K线 + 滤波       │ │  │  K线 + 滤波       │ │
│  │  ▎                │ │  │  ▎                │ │
│  │  Sig_t: +1 (多)   │ │  │  Sig_t: -1 (空)   │ │
│  └──────────────────┘ │  └──────────────────┘ │
│  信号: 45/1000 (4.5%) │  信号: 180/4000 (4.5%) │
└──────────────────────┴──────────────────────┘
  ▎ = 垂直虚线标记当前时间位置
```

**垂直时间线**: 在每个视图的 K线图上叠加一条垂直虚线 (`fig.add_vline`)，位置 = 当前切片索引。回放时这条线从左到右移动。

---

## 6. 回测引擎集成方案

### vectorbt 与 Schmitt 信号的对接

**接口**: `Sig_t` 数组直接作为 vectorbt 的 entries/exits 信号。

```python
# backtest_engine.py

import vectorbt as vbt
import numpy as np
import pandas as pd

def run_backtest(close: np.ndarray, sig: np.ndarray, dates=None) -> dict:
    """
    用 vectorbt 对 Schmitt 信号数组执行回测。

    参数:
        close: 收盘价数组 (len=N)
        sig:   Schmitt 信号数组 (len=N), 取值 {+1(做多), 0(观望), -1(做空)}
        dates: 可选，时间戳数组

    返回:
        dict: {
            "equity":      权益曲线数组,
            "metrics":     绩效指标字典,
            "trades":      交易记录 DataFrame,
            "portfolio":   vectorbt Portfolio 对象 (可选，用于高级分析)
        }
    """
    price = pd.Series(close, index=dates if dates is not None else range(len(close)))

    # 拆分信号: entries = sig从0→+1 的瞬间, exits = sig从+1→0 或 +1→-1
    entries = (sig == 1) & (np.roll(sig, 1) != 1)
    entries[0] = sig[0] == 1
    exits = (sig != 1) & (np.roll(sig, 1) == 1)
    exits[0] = False

    # 做空方向 (可选)
    shorts = (sig == -1) & (np.roll(sig, 1) != -1)
    shorts[0] = sig[0] == -1
    short_exits = (sig != -1) & (np.roll(sig, 1) == -1)
    short_exits[0] = False

    try:
        pf = vbt.Portfolio.from_signals(
            price,
            entries=entries,
            exits=exits,
            short_entries=shorts,
            short_exits=short_exits,
            freq="1d",
            init_cash=100_000.0,
        )
        equity = pf.value().values
        metrics = _extract_metrics(pf)
        return {"equity": equity, "metrics": metrics, "trades": pf.trades.records_readable, "portfolio": pf}
    except Exception:
        # fallback to simple backtest
        return _simple_backtest(close, sig)
```

### 绩效指标清单

| 指标 | 英文 | 计算方式 | 说明 |
|------|------|----------|------|
| 总收益率 | Total Return | `(最终权益 - 初始资金) / 初始资金` | |
| 年化收益率 | Annual Return | `(1 + Total Return)^(252/N) - 1` | N=交易日数 |
| 夏普比率 | Sharpe Ratio | `(年化收益 - 无风险利率) / 年化波动率` | 无风险利率默认 2% |
| 最大回撤 | Max Drawdown | `max(1 - 权益/历史峰值)` | 百分比 |
| 胜率 | Win Rate | `盈利交易数 / 总交易数` | |
| 盈亏比 | Profit Factor | `总盈利 / 总亏损` | |
| 年化波动率 | Annual Volatility | `日收益标准差 * sqrt(252)` | |
| 卡玛比率 | Calmar Ratio | `年化收益 / Max Drawdown` | |
| 交易次数 | Total Trades | vectorbt 统计 | |
| 平均持仓天数 | Avg Hold Days | `总持仓天数 / 交易次数` | |

### 降级方案: 自研 pandas 引擎 (~80行)

当 vectorbt 不可用或报错时，自动切换到内置的简化引擎:

```python
def _simple_backtest(close: np.ndarray, sig: np.ndarray) -> dict:
    """纯 numpy/pandas 回测，无外部依赖。"""
    n = len(close)
    position = np.zeros(n, dtype=int)  # 0=空仓, 1=多头, -1=空头
    daily_ret = np.zeros(n)

    for i in range(n):
        if sig[i] == 1:
            position[i] = 1
        elif sig[i] == -1:
            position[i] = -1
        else:
            position[i] = position[i-1] if i > 0 else 0

        if i > 0 and position[i-1] != 0:
            daily_ret[i] = position[i-1] * (close[i] / close[i-1] - 1)

    equity = 100_000 * np.cumprod(1 + daily_ret)
    # ... 计算 metrics 用纯 numpy
```

功能子集: 支持总收益、最大回撤、夏普比率、胜率。不支持交易明细。

---

## 7. 数据缓存架构

### Parquet 文件命名和组织规则

```
data/cache/
├── US_AAPL/
│   ├── 1m.parquet
│   ├── 5m.parquet
│   ├── 15m.parquet
│   ├── 1h.parquet
│   ├── 1d.parquet
│   ├── 1wk.parquet
│   └── 1mo.parquet
├── CN_600519/
│   ├── 1d.parquet
│   └── ...
└── HK_00700/
    ├── 1d.parquet
    └── ...
```

**命名规则**: `{市场前缀}_{代码}/{周期}.parquet`
- 市场前缀: `US` / `CN` / `HK`
- 周期: yfinance interval 格式 (`1m`, `5m`, `15m`, `1h`, `1d`, `1wk`, `1mo`, `3mo`)

### 缓存读写流程

```
                       ┌──────────────┐
    数据请求 ─────────►│ @st.cache_data│ (L1: 内存, TTL=300s)
                       └──────┬───────┘
                              │ miss
                              ▼
                       ┌──────────────┐
                       │  Parquet 文件 │ (L2: 磁盘持久化)
                       └──────┬───────┘
                              │ miss
                              ▼
                       ┌──────────────┐
                       │ yf/ef 拉取   │ (L3: 网络)
                       └──────┬───────┘
                              │
                              ▼
                       ┌─ 写 Parquet ─┐
                       └──────────────┘
```

```python
# cache.py 核心接口

@st.cache_data(show_spinner=False, ttl=300)
def get_ohlcv(market: str, code: str, tf: str, n_pts: int) -> tuple:
    """
    获取 OHLCV 数据 (内存缓存层)。
    内部调用 _fetch_with_parquet() 实现 L2+L3 逻辑。
    """
    ...

def _fetch_with_parquet(market: str, code: str, tf: str) -> pd.DataFrame:
    """
    1. 检查 Parquet 文件是否存在且新鲜 (日线: 1天内; 分钟线: 5分钟内)
    2. 新鲜 → 直接读取返回
    3. 过期/不存在 → 拉取数据 → 写 Parquet → 返回
    """
    ...
```

### 与现有 @st.cache_data 的协作

- **现有** `_fetch_stock` (line 420): 保留不动，美股数据仍走此路径
- **新增** `cache.get_ohlcv`: 对 A股/港股走 efinance + Parquet 路径；对美股走 yfinance + Parquet 路径
- **分层关系**: `@st.cache_data` 是 L1 (会话级内存缓存)，Parquet 文件是 L2 (跨会话持久化)，网络拉取是 L3
- **缓存失效**: Parquet 文件按时间戳判断新鲜度；`st.cache_data` 按 TTL (300s) 自动失效；同时提供 sidebar 的"强制刷新"按钮清除两层缓存

---

## 8. 实施路线图

### Phase 1: MVP 最小可用回测 (预估 6-8 工时)

**目标**: 单个视图 (日线) 的 Schmitt 信号能在图表下方显示权益曲线，验证 vectorbt 集成。

| 任务 | 预估 | 产出 |
|------|------|------|
| 1.1 安装依赖 & 验证 vectorbt 导入 | 0.5h | requirements.txt 更新 |
| 1.2 编写 `backtest_engine.py` (vectorbt + 降级版) | 2h | 可单元测试的引擎模块 |
| 1.3 在 `main()` 增加回测模式入口 (sidebar checkbox) | 1h | 可切换回测模式 |
| 1.4 在视图1下方渲染权益曲线 (新增 `_render_equity` 函数) | 2h | 单视图权益曲线可见 |
| 1.5 绩效指标卡片 (底部 columns 显示 Sharpe/MaxDD/WinRate) | 1h | 绩效数据可见 |
| 1.6 单元测试: 用已知信号验证回测结果正确性 | 1.5h | 测试通过 |

**验证标准**: 选择 AAPL 日线，打开施密特触发器，进入回测模式，看到权益曲线和 4 个关键指标。

### Phase 2: 多周期对齐 + 回放控制 (预估 8-10 工时)

**目标**: 4 视图同步回放，时间对齐正确，防未来函数验证通过。

| 任务 | 预估 | 产出 |
|------|------|------|
| 2.1 编写 `backtest_timeline.py` (统一时间轴 + 可见性切片) | 3h | 时间轴模块 |
| 2.2 编写 `cache.py` (Parquet + efinance 集成) | 2h | 缓存模块 |
| 2.3 Session State 设计实现 (状态机 + 所有 `_backtest_*` 键) | 1.5h | 状态管理完备 |
| 2.4 Sidebar 回放控制面板 (按钮 + slider + 速度选择) | 2h | UI 完整 |
| 2.5 `_render_chart` 支持 `slice_to` 参数 + 垂直时间线 | 2h | 图表切片显示 |
| 2.6 防未来函数验证 (`validate_no_leak`) | 1h | 验证通过 |
| 2.7 集成测试: 多视图同步回放 | 1.5h | 端到端可用 |

**验证标准**: 选择日线+60分钟+15分钟+5分钟组合，进入回测模式，点击播放，4 个图表的垂直时间线同步移动，信号值符合可见性规则。

### Phase 3: 绩效分析完善 + 优化 (预估 4-6 工时)

**目标**: 完整绩效面板、交易明细、导出功能、用户反馈优化。

| 任务 | 预估 | 产出 |
|------|------|------|
| 3.1 完整绩效指标面板 (所有 10 个指标，含图表) | 1.5h | 指标面板 |
| 3.2 交易明细表 (可展开的 st.dataframe) | 1h | 交易记录 |
| 3.3 回测报告导出 (CSV) | 0.5h | 下载功能 |
| 3.4 A股/港股验证 (efinance + Parquet 路径) | 1h | 多市场可用 |
| 3.5 性能优化 & 异常处理 | 1h | 错误处理和 loading 状态 |
| 3.6 用户文档 | 1h | 操作说明 |

### 工时汇总

| Phase | 内容 | 预估工时 |
|-------|------|----------|
| Phase 1 | MVP 单周期回测 | 6-8h |
| Phase 2 | 多周期对齐 + 回放 | 8-10h |
| Phase 3 | 绩效面板 + 完善 | 4-6h |
| **合计** | | **18-24h** |

---

## 9. 风险与备选方案

### 技术风险矩阵

| 风险 | 概率 | 影响 | 降级/替代方案 |
|------|------|------|--------------|
| **vectorbt 版本兼容性** — vectorbt 与 streamlit/pandas 版本冲突 | 中 | 高 (回测核心不可用) | 自研 pandas 引擎已内置为降级方案 (~80行)，功能子集但保证可用 |
| **efinance API 不稳定** — A股数据源接口变更或限流 | 中 | 中 (A股/港股用户受影响) | try/except 自动回退到 yfinance；美股用户完全不受影响 |
| **多周期时间对齐 Bug** — 分钟级边界情况导致信号泄漏 | 中 | 高 (回测结果不可信) | L3 交叉验证 (`validate_no_leak`) 自动检测；发现问题后可针对性地修复切片逻辑 |
| **Streamlit rerun 性能** — 回放时频繁 rerun 导致图表渲染卡顿 | 低 | 中 (用户体验差) | 降低回放速度下限 (最低 1x=1秒/帧)；使用 `@st.cache_data` 缓存中间结果；必要时限制回放区间长度 |
| **Parquet 磁盘占用增长** — 多股票多周期缓存文件累积 | 低 | 低 (存储便宜) | 设置最大缓存天数 (如 30 天)；提供"清理缓存"按钮；可配置缓存目录 |
| **yfinance 接口变更** — Yahoo Finance API 不再可靠 | 低 | 高 (美股数据不可用) | efinance 部分覆盖；长期可评估 polygon.io / alpaca 等专业数据源 |

---

## 10. 开放问题与决策点

### 需要用户确认的设计抉择

| # | 问题 | 选项 | 建议 |
|---|------|------|------|
| Q1 | **回测决策周期** — 以哪个周期的信号作为"主决策"？ | A) 日线统一决策 (简单) B) 可配置 (灵活) C) 用户指定其中一个视图的周期 | **建议 A**，Phase 1-2 固定日线决策，Phase 3 增加可配置 |
| Q2 | **做空是否启用** — Schmitt 信号的 -1 (空) 是否参与回测？ | A) 仅做多 (信号 0/+1) B) 多空双向 (信号 -1/0/+1) | **建议 B**，因为 Sig_t 本身就输出了空信号，不利用浪费 |
| Q3 | **初始资金** — 回测起始资金？ | A) 固定 100,000 B) 可配置 | **建议 A** Phase 1-2，Phase 3 增加可配置 |
| Q4 | **手续费模型** — 滑点和手续费如何设置？ | A) 零成本 (纯信号验证) B) 万分之一 C) 可配置 | **建议 A** Phase 1-2，因为目标是验证信号质量而非实际交易成本 |
| Q5 | **多周期信号融合策略** — 4 个视图的 Sig_t 如何合成一个最终交易决策？ | A) 简单投票 (多数原则) B) 加权 (用户配置权重) C) 每个视图独立回测，不融合 | **建议 A** Phase 2，Phase 3 增加 B 选项 |
| Q6 | **回放区间范围** — 回测是全部历史数据，还是可截取子区间？ | A) 全部数据 B) 用户可选起始/结束日期 | **建议 B** Phase 2 就支持，用两个 date_input 组件 |

### 建议的优先级排序

1. **Phase 1 优先落地** — MVP 单视图权益曲线，这是整个方案的存在性验证
2. **Q1/Q2/Q3/Q4 在 Phase 1 前确认** — 它们决定回测引擎的核心行为
3. **Q5/Q6 在 Phase 2 前确认** — 它们决定多周期融合和回放区间的设计
4. **性能优化为 Phase 3 关注点** — 先保证功能正确，再优化体验

---

## 附录: 关键文件变更清单

### 新增文件

| 文件 | 职责 | 预估行数 |
|------|------|----------|
| `streamlit/backtest_engine.py` | 回测引擎 (vectorbt + 降级版) | ~120 |
| `streamlit/backtest_timeline.py` | 多周期时间对齐 | ~100 |
| `streamlit/cache.py` | 数据缓存 (Parquet + @st.cache_data) | ~80 |

### 修改文件

| 文件 | 改动范围 | 预估行数 |
|------|----------|----------|
| `streamlit/streamlit_app.py` | 回放控制 + 数据切片 + 绩效渲染 | +140 |
| `streamlit/requirements.txt` | 追加 vectorbt, efinance, pyarrow | +3 |

### 不动文件

`FILTERS` 注册表、所有 `apply_*` 滤波函数、`_schmitt_trigger`、`_fetch_stock`、`_render_params`、`_render_plotly`、`_render_chart` (仅增加可选 `slice_to` 参数)
