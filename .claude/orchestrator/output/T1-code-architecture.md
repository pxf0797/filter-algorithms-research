# 多周期股票滤波分析工具 — 代码架构深度分析报告

> **分析日期:** 2026-06-28
> **分析文件:**
> - `filter_app/streamlit_app.py` (2582行)
> - `filter_app/config_db.py` (411行)
> - `filter_app/db.py` (438行)

---

## A. 应用整体架构

### A.1 页面组织方式

该应用是一个**纯单页面 Streamlit 应用**，未使用 Streamlit 的 `pages/` 多页面机制。所有功能集中在同一页面内，通过以下方式组织：

| 组织方式 | 说明 | 代码位置 |
|----------|------|----------|
| **`st.sidebar`** | 全局控制面板：市场选择、股票代码、滤波器选择、配置方案、数据管理 | `streamlit_app.py:1943-2578` |
| **`st.columns(2)` 网格** | 4 个视图 (2x2 网格)，每个视图独立配置 | `streamlit_app.py:2331-2341` (参数面板)、`2441-2457` (图表) |
| **`st.expander`** | 可折叠面板：施密特参数、预测参数、策略参数、滤波参数 | `streamlit_app.py:1359-1455` |
| **`st.columns(N)`** | 紧凑布局，每行多个控件 | `streamlit_app.py:1328-1355` |
| **`st.components.v1.html`** | 自定义 Plotly 渲染引擎 | `streamlit_app.py:325-444` |

### A.2 主要功能区划分

| 分区 | 功能 | 代码位置 | 说明 |
|------|------|----------|------|
| **侧边栏-全局** | 市场/股票选择、数据刷新、预设管理 | `streamlit_app.py:1977-2329` | 控制全局 |
| **侧边栏-工具** | 数据健康检查、数据校验、备份恢复、导入导出 | `streamlit_app.py:2199-2560` | 运维功能 |
| **4视图-参数** | 每个视图独立的周期/N值/施密特/预测/滤波参数 | `streamlit_app.py:1323-1500` | 2x2 网格排列 |
| **4视图-图表** | K线图+滤波+子图(残差/速度/加速度/Sig/PnL) | `streamlit_app.py:1503-1933` | 按时间框架降序渲染 |

### A.3 应用入口和主流程

```
main() (streamlit_app.py:1937)
  ├─ init_db()                     — 初始化 market.db (streamlit_app.py:1938)
  ├─ init_config_tables()          — 初始化 config.db (streamlit_app.py:1939)
  ├─ import_json_files_as_presets()— 首次运行导入 config/*.json (streamlit_app.py:1941)
  ├─ 侧边栏: 市场/股票/预设/数据管理 (streamlit_app.py:1943-2329)
  ├─ Pass 1: 渲染 4 个参数面板 (streamlit_app.py:2331-2341)
  ├─ 时间窗口导航 (streamlit_app.py:2343-2391)
  ├─ 数据备份恢复 (streamlit_app.py:2394-2439)
  ├─ Pass 2: 渲染 4 个图表 (streamlit_app.py:2441-2457)
  ├─ 导出配置 (streamlit_app.py:2459-2492)
  ├─ 配置历史 (streamlit_app.py:2495-2505)
  ├─ 数据库导入/导出 (streamlit_app.py:2508-2560)
  └─ 自动刷新循环 (streamlit_app.py:2563-2578)
```

**关键设计要点:** 参数面板和图表分两轮渲染(rerender)，因为参数面板中 `st.selectbox`/`st.slider` 等 widget 创建了 `session_state` key，图表渲染时需要读取这些 key。

---

## B. 状态管理

### B.1 `st.session_state` 使用情况

应用广泛使用 `session_state` 作为"唯一真相源"，总计使用了约 **80+ 个不同的 key**，分类如下：

#### 全局 key

| Key | 类型 | 用途 | 首次设置位置 |
|-----|------|------|-------------|
| `market` | str | 市场选择 "美股 US"/"A股(沪深)"/"港股 HK" | `streamlit_app.py:1977` |
| `ticker` | str | 股票代码 | `streamlit_app.py:1981` |
| `global_f` | str | 滤波器 ID | `streamlit_app.py:2323` |
| `global_dual` | bool | 双滤波对比 | `streamlit_app.py:2325` |
| `global_f2` | str | 第二滤波器 ID | `streamlit_app.py:2328` |
| `auto_refresh` | bool | 自动刷新开关 | `streamlit_app.py:2028` |
| `refresh_interval` | int | 刷新间隔(秒) | `streamlit_app.py:2030` |
| `_config_initialized` | bool | 配置初始化标志 | `streamlit_app.py:1942` |
| `_import_data` | str/None | 导入配置的 MD5 hash | `streamlit_app.py:1949-1950` |
| `_fetched_ticker` | str | 已获取数据的股票代码 | `streamlit_app.py:2002` |
| `_last_auto_refresh` | float | 上次自动刷新时间戳 | `streamlit_app.py:2566` |
| `_day_offset` | int | 时间窗口偏移(天) | `streamlit_app.py:2346-2347` |
| `_db_import_hash` | str | 导入数据库文件的 MD5 | `streamlit_app.py:2536` |

#### 视图配置 key (v0~v3)

| Key 模式 | 用途 | 示例值 |
|----------|------|--------|
| `v{i}_tf` | 周期 | `"日线"` |
| `v{i}_n` | 数据点数 | 120 |
| `v{i}_sch` | 施密特开关 | True |
| `v{i}_pred` | 预测开关 | True |
| `v{i}_ke` | 灵敏度系数 k_ε | 0.15 |
| `v{i}_sm` | 地板保护 σ_min | 0.05 |
| `v{i}_ew` | EWMA 周期 N_EWMA | 60 |
| `v{i}_next` | 预测延伸点数 N_ext | 8 |
| `v{i}_fm` | 拟合方式 | `"parabola"` / `"poly2"` |
| `v{i}_fc` | 滤波颜色 | `"#00d4aa"` |
| `v{i}_fc2` | 滤波2颜色 | `"#ff6b6b"` |
| `v{i}_strat` | 策略叠加开关 | False |
| `v{i}_sl` | 止损阈值(%) | 2.0 |
| `v{i}_cross_pnl` | 高周期 PnL 参考 | False |
| `v{i}_align` | 同向性判断 | False |

#### 备份机制: `_imp_` prefix key

这是一个值得注意的设计模式。应用为每个 widget 绑定的 key 创建了 `_imp_{key}` 备份：

```python
# streamlit_app.py:1470-1485
cfg["n_ext"] = st.session_state.get(f"{key}_next",
    st.session_state.get(f"_imp_{key}_next", cfg["n_ext"]))
```

**目的:** 解决 Streamlit `st.rerun()` 时 widget 未渲染但 session_state 已丢失的问题，确保从配置导入/预设应用后的参数能持久保留。

#### 预设操作 key

| Key | 用途 |
|-----|------|
| `_pending_apply_params` | 延迟应用的预设参数字典 |
| `_preset_action` | 预设操作类型: "update"/"rename"/"delete" |
| `_preset_action_id` | 预设操作目标的 preset_id |
| `_pending_reset_overwrite` | 延迟重置 overwrite checkbox |
| `new_preset_name` | 新预设名称输入 |
| `new_preset_desc` | 新预设描述输入 |
| `overwrite_preset` | 覆盖现有预设 checkbox |
| `_last_sel_name` | 上次选择的预设名称 |

#### PnL 缓存 key

```python
# streamlit_app.py:1682-1687
st.session_state[f"_pnl_{tf}"] = {
    "dates": dates, "t": t,
    "long_pnl": long_pnl, "short_pnl": short_pnl,
    "trade_records": trade_records,
}
```

低周期视图读取高周期的 PnL 数据用于跨周期参考子图。

### B.2 `st.cache_data` 使用情况

| 缓存函数 | TTL | Spinner | 用途 | 位置 |
|----------|-----|---------|------|------|
| `_fetch_stock()` | 300s (5分钟) | 关闭 | 从 yfinance 获取股票数据 | `streamlit_app.py:478-479` |
| `_stock_name()` | 3600s (1小时) | 关闭 | 获取股票名称 | `streamlit_app.py:1984` |

**未使用** `st.cache_resource`。

### B.3 状态初始化位置

状态初始化分散在多个位置，采用"惰性初始化"模式：

```python
# streamlit_app.py:1949-1950
if "_import_data" not in st.session_state:
    st.session_state._import_data = None

# streamlit_app.py:2001-2002
if "_fetched_ticker" not in st.session_state:
    st.session_state._fetched_ticker = ""

# streamlit_app.py:2346-2347
if "_day_offset" not in st.session_state:
    st.session_state._day_offset = 0
```

**评价:** 没有统一的初始化入口，而是分散在首次使用处。这符合 Streamlit 的 rerun 模式，但不利于追踪所有 session_state key。

---

## C. 核心功能模块

### C.1 4 视图参数配置 (_render_params)

**位置:** `streamlit_app.py:1323-1500`

每个视图是一个独立的参数面板，包含：

1. **周期选择**: `st.selectbox` — 8 个周期选项 (1分钟~季线)
2. **数据点数**: `st.slider` — 20~300 点 (默认 120)
3. **施密特触发**: `st.checkbox` — 开启时展开施密特参数面板
4. **预测开关**: `st.checkbox` — 开启时展开预测参数和策略参数面板
5. **滤波器选择**: 两个 `st.expander` 面板 (单滤波/双滤波)
6. **滑块渲染**: `_render_param_slider()` 封装了 slider 的数值格式和 session_state 恢复

**设计特点:** 
- 使用 `_compact_slider()` 将标签和滑块放到同一行
- 通过 `st.columns([1.0, 0.8, 0.8, 0.8, 0.4])` 紧凑排列控件
- 使用 `st.session_state` 记住折叠展开状态 `exp_key = f"{key}_exp_all"`

### C.2 图表渲染 (_render_chart)

**位置:** `streamlit_app.py:1503-1933`

这是应用中最复杂的函数 (~430 行)，核心流程：

#### 数据获取 (streamlit_app.py:1516-1537)
```
_sync_to_display() → Parquet 文件
  ↓ (如果 Parquet 不存在或数据不足)
_fetch_stock() → yfinance → upsert_kline() → query_kline()
```

#### 信号处理 (streamlit_app.py:1596-1609)
```python
filtered = sf["func"](noisy, t, **cfg["pv"])  # 主滤波
filtered2 = sf2["func"](noisy, t, **cfg["pv2"])  # 第二滤波(可选)
```

#### 施密特触发器 (streamlit_app.py:1618-1621)
```python
_v = np.gradient(filtered, t); _a = np.gradient(_v, t)
schmitt = _schmitt_trigger(_v, _a, ewma_span=cfg["ew"], ...)
```

#### 多空对检测 (streamlit_app.py:1629-1631)
```python
all_pairs = _find_all_pairs(schmitt["sig"])
```

#### 预测曲线拟合 (streamlit_app.py:1634-1645)
```python
fit_func = _fit_physics_parabola if cfg.get("fit_mode") == "parabola" else _fit_parabolic
for pair_start, pair_end in all_pairs:
    fit_result = fit_func(t, filtered, pair_start, pair_end)
```

#### 策略 PnL 计算 (streamlit_app.py:1647-1659)
```python
long_pnl, short_pnl, trade_records = _compute_strategy_pnl(...)
```

#### 子图布局 (streamlit_app.py:1689-1730)

动态子图数量 (4~8 行)，根据功能开启状态决定：

| 场景 | 行数 | 子图行含义 |
|------|------|-----------|
| 无施密特 | 4 | 价格&滤波 / 残差 / 速度v / 加速度a |
| 有施密特 | 5 | + Sig_t |
| + 策略PnL | 6 | + PnL收益(%) |
| + 跨周期PnL | 7 | + 高周期PnL参考 |
| + 同向性判断 | 8 | + 同向性判断 |

#### 图表输出 (streamlit_app.py:1733-1933)

- **主图 row 1**: `go.Candlestick` K线图 + `go.Scatter` 收盘价 + 滤波曲线
- **残差 row 2**: `go.Scatter` 滤波-收盘
- **速度 row 3**: `go.Scatter` v = d(filtered)/dt
- **加速度±ε row 4**: `go.Scatter` 加速度 + ε死区带 + σ(v)波动率
- **Sig_t row 5**: `go.Scatter` 施密特信号 (±1/0) + 切换对色带
- **PnL row 6**: 做多(绿) + 做空(红) 曲线 + 入场/离场标记
- **高周期PnL row 7**: 虚线参考 + △/×/○ 事件标记 + 盈亏标注
- **同向性 row 8**: 高周期持仓同向时采样

### C.3 滤波器实现 (streamlit_app.py:47-211)

**位置:** `streamlit_app.py:47-211`

9 种滤波器注册在 `FILTERS` 字典中：

| 滤波器 | 函数 | 参数 | 算法复杂度 |
|--------|------|------|-----------|
| SMA | `apply_sma` | window | O(n·k) |
| EMA | `apply_ema` | span | O(n) |
| WMA | `apply_wma` | window | O(n·k) |
| ALMA | `apply_alma` | window, offset, sigma | O(n·k) |
| Savitzky-Golay | `apply_savgol` | window, order | O(n·k) |
| 卡尔曼 | `apply_kalman` | Q, R | O(n) 迭代 |
| 巴特沃斯 | `apply_butterworth` | order, cutoff | O(n·log n) |
| 高斯 | `apply_gaussian` | sigma | O(n·k) |
| 中值 | `apply_median` | window | O(n·k) |
| LOWESS | `apply_lowess` | frac | O(n²) |

**设计模式:** 函数注册表模式 — 所有滤波器统一签名为 `func(signal, t, **param_values)`，通过 `FILTERS` 字典索引查找，调用时解包参数。

### C.4 施密特触发器 (_schmitt_trigger)

**位置:** `streamlit_app.py:586-644`

算法实现来自文档"多周期趋势策略V2_优化4"的 §二, chart ④⑥:

1. **EWMA 波动率估计**: `σ_t(v)` 通过指数加权移动平均估计
2. **自适应死区**: `ε_t = k_ε · max(σ_t(v), σ_min)` 
3. **状态机**: 三态 (±1/0) 带滞后切换

输出:
- `sig_t`: 信号序列 (±1=多空, 0=观望)
- `eps`: 自适应死区序列
- `dur_t`: 状态持续时间

### C.5 多空对检测 (_find_all_pairs)

**位置:** `streamlit_app.py:647-690`

1. 收集所有非零段
2. 合并相邻同号段 (含中间观望区)
3. 相邻异号段配对

### C.6 抛物线拟合 (_fit_physics_parabola / _fit_parabolic)

**位置:** `streamlit_app.py:693-723`

两种拟合模式:
- **poly2**: 标准二次多项式拟合 (3 个自由度)
- **parabola**: 锚定终点为顶点，仅拟合曲率 a (1 个自由度)

### C.7 策略PnL计算 (_compute_strategy_pnl)

**位置:** `streamlit_app.py:781-975`

分段混合方案 (方案D):
1. **预测保护期** [entry+1, entry+N_ext]: 止损 + 止盈双保护
2. **趋势跟踪期** [entry+N_ext+1, ...]: 仅 Sig 反转止盈
3. 做多/做空两条独立资本曲线

### C.8 跨周期PnL对齐 (_align_pnl_to_current_tf)

**位置:** `streamlit_app.py:978-1062`

1. 时区归一化: 去掉 tz-aware 信息
2. 前向填充: 对当前周期每个 bar，找 ≤ 该时间戳的最近高周期 bar
3. 交易事件映射: 将高周期的入场/离场索引映射到当前周期 bar 索引

### C.9 数据获取 (_fetch_stock / _fetch_all_timeframes)

**位置:** `streamlit_app.py:450-579`

- **_fetch_stock**: 单周期数据获取，使用 yfinance + 缓存的组合
- **_fetch_all_timeframes**: 8 个周期并行获取 (ThreadPoolExecutor max_workers=8)
- 数据缓存策略: 首次获取 → 写入 SQLite → 查询最近 N 条 → 写入 Parquet

### C.10 配置预设管理

**位置:** `streamlit_app.py:2032-2195` (UI) + `config_db.py` (数据层)

- 预设列表 → 选择 → 应用/更新/重命名/删除
- 应用: 延迟机制 `_pending_apply_params` → 下次 rerun 时在 widget 创建前写入 session_state
- 更新/删除/重命名: `_preset_action` 标志 + 确认 UI (streamlit_app.py:2078-2146)

---

## D. 数据层

### D.1 数据库概览

**两个独立的 SQLite 数据库:**

| 数据库 | 路径 | 用途 |
|--------|------|------|
| `market.db` | `data/market.db` | K线数据存储 |
| `config.db` | `data/config.db` | 配置预设、标的配置快照、变更历史 |

### D.2 db.py — K线数据层

#### 表结构

```sql
-- data/market.db
CREATE TABLE IF NOT EXISTS kline (
    ticker    TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ts        TEXT NOT NULL,
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL,
    volume    REAL,
    PRIMARY KEY (ticker, timeframe, ts)
);
CREATE INDEX IF NOT EXISTS idx_kline_lookup
    ON kline(ticker, timeframe, ts);
```

**主要字段:** ticker, timeframe, ts, open/high/low/close/volume

#### 核心操作

| 函数 | 操作类型 | 说明 | 位置 |
|------|---------|------|------|
| `upsert_kline()` | INSERT OR IGNORE + INSERT OR REPLACE | 批量 upsert，历史 bar IGNORE，最新 bar REPLACE | `db.py:47-76` |
| `query_kline()` | SELECT ... ORDER BY ts DESC LIMIT | 按时间倒序查最近 N 条 | `db.py:79-109` |
| `get_date_range()` | SELECT MIN/MAX | 数据起止日期 | `db.py:112-121` |
| `has_data()` | SELECT 1 ... LIMIT 1 | 检查是否有数据 | `db.py:124-128` |
| `compare_with_db()` | SELECT ... + MD5指纹 | 对比 DB 和 yfinance 数据差异 | `db.py:336-408` |
| `force_update_kline()` | DELETE + upsert | 强制更新重复 timestamps 的数据 | `db.py:411-432` |

#### 数据库管理模式

| 函数 | 说明 | 位置 |
|------|------|------|
| `init_db()` | 创建 kline 表和索引 | `db.py:27-44` |
| `checkpoint_wal()` | 强制 WAL checkpoint | `db.py:135-138` |
| `check_data_health()` | 健康检查: 行数、空值、缺口、过期 | `db.py:141-248` |
| `validate_db()` | 验证 SQLite 文件有效性 | `db.py:259-272` |
| `snapshot_db()` / `list_snapshots()` / `restore_snapshot()` / `prune_snapshots()` | 备份管理 | `db.py:275-318` |
| `clear_display_cache()` | 删除 Parquet 缓存 | `db.py:321-329` |
| `get_db_size_mb()` | 文件大小 | `db.py:251-256` |

#### 连接管理

```python
# db.py:18-24
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn
```

**注意:** `db.py` 使用 `get_conn()` 返回原始 connection 对象，而 `config_db.py` 使用上下文管理器 `_get_conn()`。两种模式差异:

- `db.py`: 函数内 `with get_conn() as conn:` → 依赖 `sqlite3.Connection.__exit__` 自动 close (但文档说明需要显式 close)
- `config_db.py`: 自定义 `@contextmanager` → 显式 `conn.close()` + commit/rollback

### D.3 config_db.py — 配置管理

#### 表结构

```sql
-- data/config.db
-- 表1: 配置预设
CREATE TABLE IF NOT EXISTS config_presets (
    preset_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL UNIQUE,
    description  TEXT    DEFAULT '',
    category     TEXT    DEFAULT '通用',
    params_json  TEXT    NOT NULL,
    created_at   TEXT    DEFAULT (datetime('now','localtime')),
    updated_at   TEXT    DEFAULT (datetime('now','localtime'))
);

-- 表2: 标的配置
CREATE TABLE IF NOT EXISTS config_ticker (
    ticker       TEXT    NOT NULL,
    variant      TEXT    NOT NULL DEFAULT 'single',
    market       TEXT    DEFAULT '',
    preset_id    INTEGER REFERENCES config_presets(preset_id) ON DELETE SET NULL,
    params_json  TEXT    DEFAULT '',
    updated_at   TEXT    DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (ticker, variant)
);

-- 表3: 配置历史
CREATE TABLE IF NOT EXISTS config_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT    NOT NULL,
    variant      TEXT    NOT NULL DEFAULT 'single',
    preset_id    INTEGER,
    old_json     TEXT    DEFAULT '',
    new_json     TEXT    DEFAULT '',
    changed_at   TEXT    DEFAULT (datetime('now','localtime')),
    source       TEXT    DEFAULT 'ui',
    FOREIGN KEY (ticker, variant) REFERENCES config_ticker(ticker, variant)
);
```

#### CRUD 操作

| 函数 | 用途 | 位置 |
|------|------|------|
| `list_presets()` | 列出所有预设 (可选按分类筛选) | `config_db.py:124-134` |
| `get_preset()` | 按 ID 获取预设 | `config_db.py:137-142` |
| `get_preset_by_name()` | 按名称获取预设 | `config_db.py:145-150` |
| `save_preset()` | 创建/更新预设 (UPSERT) | `config_db.py:153-178` |
| `delete_preset()` | 删除预设 + 同步删除 JSON 文件 | `config_db.py:181-200` |
| `rename_preset()` | 重命名预设 (含唯一性检查) | `config_db.py:203-228` |
| `apply_preset()` | 解析预设 params_json | `config_db.py:231-244` |
| `load_ticker_config()` | 加载标的配置 | `config_db.py:251-258` |
| `save_ticker_config()` | 保存标的配置 | `config_db.py:261-274` |
| `record_history()` | 记录配置变更历史 | `config_db.py:281-289` |
| `get_history()` | 查询历史 (JOIN config_presets) | `config_db.py:292-302` |
| `import_json_files_as_presets()` | 从 config/*.json 导入预设 | `config_db.py:309-354` |
| `collect_current_params()` | 从 session_state 收集参数 | `config_db.py:361-389` |

#### 连接管理

```python
# config_db.py:21-37
@contextmanager
def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_CONFIG_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

### D.4 查询模式分析

**N+1 问题:** 
- `upsert_kline()` (db.py:47-76) 有潜在 N+1: 先 SELECT MAX(ts)，然后对 recent 每条执行 INSERT OR REPLACE。但 recent 通常只有 1~2 条，实际影响小。
- `check_data_health()` (db.py:141-248): 遍历 ticker + timeframe 的嵌套循环，每个 (ticker, tf) 组合执行 3~4 次查询。可能存在性能问题，但属于一次性调用。
- `compare_with_db()` (db.py:336-408): 单次全量查询，无 N+1 问题。

**分页:** 使用了 `ORDER BY ts DESC LIMIT ?` 模式 (`query_kline()`)，通过 `n_pts` 参数限制返回行数，这是一个有效的分页策略。

**索引使用:** `idx_kline_lookup(ticker, timeframe, ts)` 覆盖了几乎所有查询模式。

### D.5 缓存层

**双层缓存架构:**
1. **SQLite** (持久化) — upsert_kline / query_kline
2. **Parquet** (显示缓存) — `data/display/{tf}.parquet`

写入路径:
```
yfinance → upsert_kline(SQLite) → query_kline → to_parquet(display)
```

读取路径:
```
_sync_to_display() → read_parquet(display) → 渲染
↓ (如果 display parquet 不存在或数据不足)
_fetch_stock() → yfinance → upsert_kline → query_kline → to_parquet → 渲染
```

---

## E. 可视化分析

### E.1 Plotly 图表类型

| 图表类型 | 用法 | 位置 |
|----------|------|------|
| `go.Candlestick` | K线图 (主图 row 1) | `streamlit_app.py:1733-1737` |
| `go.Scatter` | 收盘价曲线、滤波曲线、预测曲线 | `streamlit_app.py:1738-1752` |
| `go.Scatter` (fill="toself") | 加速度死区带、切换对色带、PnL盈利区域填充 | `streamlit_app.py:1764-1795` |
| `go.Scatter` (shape="hv") | 施密特信号 Sig_t 阶梯图 | `streamlit_app.py:1776-1777` |
| `fig.add_hline` | 基准线 (y=0 / y=100) | 多处 |
| `fig.add_vline` | 日期分隔线 | `streamlit_app.py:1908-1910` |
| `fig.add_shape` | 初始隐藏竖线 (cursor crosshair) | `streamlit_app.py:1905-1906` |
| `fig.add_annotation` | 盈亏百分比标注 | 多处 |

### E.2 自定义渲染引擎 (_render_plotly)

**位置:** `streamlit_app.py:280-444`

应用不使用标准的 `st.plotly_chart()`，而是**完全自定义的 HTML 渲染引擎**:

1. **优点 (为什么自定义):**
   - 实现跨子图 crosshair (plotly 原生不支持跨 subplot 的统一竖线)
   - 隐藏默认 hovertext (`g.hovertext { visibility: hidden }`)
   - 自定义日期 tooltip 定位 (跟随鼠标)
   - 移除无用的 modebar 按钮 (lasso2d, select2d)

2. **实现细节:**
   - 使用 Plotly.js CDN (v2.35.2)
   - 将 figure 序列化为 JSON → 嵌入 HTML iframe
   - **JSON 净化**: `_sanitize_for_json()` 递归替换 NaN/Inf → None，防止浏览器白屏
   - **自定义编码器**: `_NpEncoder` 处理 numpy 类型

3. **Crosshair 机制:**
   - `plotly_hover` 事件 → `_apply()` 更新所有 shape 的 x0/x1
   - 节流 (45ms) + 待处理队列 (`_pendingXv`)
   - 鼠标跟随 tooltip 显示日期

### E.3 日期标记

**位置:** `streamlit_app.py:1542-1583` (_date_markers 内部函数)

根据时间框架显示不同粒度的日期标记:
- 日内 (1/5/15/60分钟): 每天边界
- 日线: 每周一
- 周线: 每月边界
- 月线: 每年1月
- 季线: 每年1月

### E.4 大数据渲染

**没有显式的大数据优化** (downsampling、WebGL、datashader 等)。应用假设数据量在 20~300 点范围内，直接渲染所有点。

- `go.Candlestick` 在 300 点以内性能可接受
- 滤波器计算使用 numpy/scipy，向量化操作
- 预测曲线拟合使用 polyfit，O(n²) 的 LOWESS 可能是计算瓶颈

---

## F. 代码质量

### F.1 代码组织

| 维度 | 评估 |
|------|------|
| **函数平均长度** | 长函数较多: `_render_chart` (~430行)、`_render_params` (~178行)、`main()` (~645行) |
| **类使用** | 零个自定义类。仅使用 `_NpEncoder(json.JSONEncoder)` 作为内部类。**纯函数式组织** |
| **模块化** | 良好的模块分离: 滤波算法 → 核心逻辑 → DB 层 → UI |
| **重复代码** | 存在明显重复: 多处 PnL 渲染逻辑 (`_compute_strategy_pnl` 内、`_add_cross_pnl_subplot`、`_add_alignment_subplot`) 有相似的标记/标注代码 |

### F.2 错误处理

| 场景 | 处理方式 | 位置 |
|------|---------|------|
| 滤波函数异常 | `try/except` 捕获 → `np.full_like(noisy, np.nan)` | `streamlit_app.py:1597-1601` |
| yfinance 下载失败 | 返回 error string → `st.error()` 显示 | `streamlit_app.py:527-528` |
| K线 Close 为 NaN | 回退周线数据后丢弃 NaN 行 | `streamlit_app.py:537-551` |
| 数据库连接异常 | `config_db.py` 使用 contextmanager 确保关闭 | `config_db.py:30-37` |
| 配置预设不存在 | `apply_preset` 返回 None，日志警告 | `config_db.py:236-244` |
| 重命名冲突 | 返回 None，日志警告 | `config_db.py:213-220` |
| JSON 导入异常 | 收集错误列表而非静默吞掉 | `config_db.py:322-348` |

**总体评价:** 错误处理覆盖较好，但部分异常被静默捕获 (`except Exception: pass`):
- `streamlit_app.py:548-549` — 周线回退异常
- `streamlit_app.py:556-557` — upsert_kline 异常
- `db.py:327-329` — 删除 parquet 异常
- `db.py:317-318` — 删除快照异常

### F.3 日志记录

- `config_db.py`: 良好地使用了 `logging.getLogger(__name__)`，关键操作都有日志 (导入错误、重命名冲突、外键迁移)
- `db.py`: **没有 logging 模块**，仅使用 `print()` 在 `__main__` 中
- `streamlit_app.py`: **没有 logging**，所有用户反馈通过 `st.toast()`/`st.error()`/`st.warning()`/`st.success()` 实现

### F.4 类型注解

| 文件 | 覆盖率 | 说明 |
|------|--------|------|
| `config_db.py` | **高** (~90%) | 参数和返回值几乎全部有类型标注 |
| `db.py` | **中** (~60%) | 函数签名有类型，内部变量无标注 |
| `streamlit_app.py` | **低** (~20%) | 仅少数函数有类型注释，大部分无标注 |

**示例对比:**
```python
# config_db.py — 好
def list_presets(category: Optional[str] = None) -> List[Dict[str, Any]]:
def get_preset(preset_id: int) -> Optional[Dict[str, Any]]:

# streamlit_app.py — 差
def _render_chart(market, ticker_code, cfg, key, compact=True, day_offset=0, higher_pnl=None):
```

### F.5 硬编码问题

| 问题类型 | 实例 | 位置 | 风险 |
|----------|------|------|------|
| **Magic Number** | `_THROTTLE_MS = 45` (ms) | `streamlit_app.py:364` | 低 |
| **Magic Number** | `max_workers=8` | `streamlit_app.py:470` | 中 — 应使用 `len(tf_config)` |
| **Magic Number** | `4` 视图循环硬编码 | `streamlit_app.py:372-376` | 低 |
| **Magic Number** | `1e-6` (diff threshold) | `db.py:381` | 低 |
| **硬编码路径** | `"data/display"` | `streamlit_app.py:576-577` | 低 — 已用 `Path(__file__)` 相对路径 |
| **硬编码路径** | `"config"` | `config_db.py:17` | 低 — 同上 |
| **硬编码路径** | `"snapshots"` | `db.py:15` | 低 — 同上 |
| **硬编码URL** | Plotly.js CDN v2.35.2 | `streamlit_app.py:329` | 中 — 无网络时不可用；版本锁定 |
| **硬编码TF列表** | `ALL_TFS` / `DEFAULT_TFS` | `streamlit_app.py:1307-1308` | 低 — 需要更新时需修改多处 |
| **硬编码interval_days** | 缺口检测阈值字典 | `db.py:161-164` | 低 — 与 TF 列表耦合 |

---

## G. 潜在改进点

### G.1 严重度: 高

| # | 问题 | 位置 | 说明 |
|---|------|------|------|
| H1 | **`main()` 函数过长 (~645 行)** | `streamlit_app.py:1937-2581` | 违反了单一职责原则，应拆分为 `_render_sidebar()`、`_render_config_panel()`、`_render_data_tools()` 等子函数 |
| H2 | **`_render_chart()` 过长 (~430 行)** | `streamlit_app.py:1503-1933` | 应拆分为 `_build_figure()`、`_add_pnl_traces()`、`_add_schmitt_traces()` 等 |
| H3 | **db.py 连接管理不完全安全** | `db.py:18-24` | 使用 `get_conn()` 返回原始 connection，依赖 `sqlite3.Connection.__exit__` 关闭。应在函数内用 `with` 管理。文档注释 (config_db.py:37) 明确指出 `__exit__` 不关闭连接 |
| H4 | **某些异常被静默捕获** | `streamlit_app.py:548-549, 556-557` | `except Exception: pass` 会隐藏严重错误，至少应 `logger.warning()` |
| H5 | **无 WebGL 渲染优化** | `streamlit_app.py:1733-1933` | Candlestick + 多条 Scatter 在 300 点时没问题，但若后期支持更多数据点可能卡顿。可考虑 `go.Candlestick(..., opacity=0.8)` 或 WebGL 后端 |

### G.2 严重度: 中

| # | 问题 | 位置 | 说明 |
|---|------|------|------|
| M1 | **重复的 PnL 渲染逻辑** | `_compute_strategy_pnl`(i.781)、`_add_cross_pnl_subplot`(i.1065)、`_add_alignment_subplot`(i.1186) | 入场/离场标记、盈亏标注代码在三处几乎相同 |
| M2 | **`session_state` key 管理混乱** | 遍布 `streamlit_app.py` | ~80+ key 没有集中管理或枚举，容易 key 冲突或遗漏清除 |
| M3 | **`_imp_` 备份机制侵入性强** | `streamlit_app.py:1460-1498` | 每个参数需要 2-3 行重复代码处理导入回退。可以考虑 `get_param(key, default)` 封装 |
| M4 | **无 streaming/progress 反馈** | `_fetch_all_timeframes` 使用 `ThreadPoolExecutor` | 8 个线程并行获取，但用户在完成前看不到进度。可改用 `as_completed` 搭配 `st.progress` |
| M5 | **Plotly.js CDN 硬编码且无 fallback** | `streamlit_app.py:329` | 内网环境/无网络时白屏。应考虑使用本地资源或提供 fallback |
| M6 | **`check_data_health()` 嵌套循环查询** | `db.py:171-233` | 每个 ticker×tf 组合执行 4+ 次查询。N 个 ticker × 8 个 tf = 32N 次查询。当 ticker 增加时可能变慢 |
| M7 | **低周期视图依赖 PnL 缓存 key 的字符串名称** | `streamlit_app.py:1514` | `st.session_state.get(f"_pnl_{_higher_tf}")` 依赖隐式约定，缺少类型检查 |
| M8 | **无输入验证: 股票代码格式** | `streamlit_app.py:1981` | 用户输入任何字符串都接受，没有格式校验或建议 |

### G.3 严重度: 低

| # | 问题 | 位置 | 说明 |
|---|------|------|------|
| L1 | **DEFAULT_TFS 硬编码且与 TF_HIERARCHY 耦合** | `streamlit_app.py:1307, 1317-1321` | 若添加新周期需修改多处 |
| L2 | **`max_workers=8` 硬编码** | `streamlit_app.py:470` | 应使用 `len(tf_config)` |
| L3 | **`_get_conn()` 和 `get_conn()` 命名不一致** | `config_db.py:21` vs `db.py:18` | 一个带下划线前缀，一个没有 |
| L4 | **`apply_sma`/`apply_wma`/`apply_alma` window 校正为奇数** | `streamlit_app.py:49-51, 63-64, 71-72` | 多处重复逻辑，可抽取为 `ensure_odd()` |
| L5 | **无 Python 版本要求** | `requirements.txt` (未读取) | 不确定是否兼容 3.9+/3.12+ |
| L6 | **`_find_all_pairs` 的 O(N²) 合并逻辑可优化** | `streamlit_app.py:674-680` | 当前实现线性扫描已是最优，但遍历 segment 时 `merged[-1]` 的赋值可改用索引 |
| L7 | **`streamlit_app.py` 无类型注解** | 几乎整个文件 | 与其他两个文件的类型风格不一致 |
| L8 | **时间戳比较的时区处理** | `streamlit_app.py:1008-1018` | `_normalize_dates` 通过 `tz_localize(None)` 丢失时区信息，可能导致边界情况对齐误差 |

---

## H. 架构亮点与设计模式总结

### H.1 值得学习的做法

1. **滤波器注册表模式** (`streamlit_app.py:147-211`): 统一的函数签名 + 字典注册，添加新滤波器只需实现一个函数并注册
2. **双存储 + 双缓存** (SQLite + Parquet): 持久化用 SQLite，显示缓存用 Parquet，各取所长
3. **导入参数的双备份机制** (`_imp_` key): 解决 Streamlit `st.rerun()` 后 widget 未渲染时的参数丢失问题
4. **延迟应用机制** (`_pending_apply_params`): 避免在 widget 渲染后修改绑定的 session_state key
5. **配置方案预设管理**: 完整的 CRUD + JSON 导入导出 + 历史记录
6. **跨周期 PnL 对齐**: 通过 `_align_pnl_to_current_tf` + session_state 缓存，实现低周期参考高周期信号

### H.2 主要架构决策

| 决策 | 选择 | 替代方案 | 合理性 |
|------|------|----------|--------|
| 页面结构 | 单页面 + 2x2 网格 + sidebar | 多页面 pages/ | 合理 — 4 个视图需要同时对比 |
| 渲染引擎 | 自定义 HTML (Plotly.js) | st.plotly_chart() | 合理 — 需要跨子图 crosshair |
| 数据持久化 | SQLite + Parquet | Pandas 直读、DuckDB | 合理 — SQLite 可靠，Parquet 快速 |
| 状态管理 | st.session_state (纯) | session_state + callback | 合理 — 简单直接 |
| 代码组织 | 纯函数式 (无类) | 类封装 | 可接受 — 但 645 行 main 函数需拆分 |

### H.3 架构复杂度评估

- **文件数量:** 3 个核心文件
- **总代码行:** ~3431 行
- **函数数量:** ~35 个 (含内部函数)
- **热路径复杂度:** 极高 — main() → _render_chart() → 滤波 → Schmitt → 拟合 → PnL → Plotly 渲染，单路径调用深度约 8 层
- **数据流复杂度:** 中等 — yfinance → upsert_kline → query_kline → to_parquet → read_parquet → numpy → Plotly
- **状态管理复杂度:** 中高 — ~80+ session_state key，含备份/恢复/延迟应用机制
