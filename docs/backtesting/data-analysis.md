# 回测数据加载流程 — 完整分析报告

分析日期: 2026-07-05
覆盖模块: `data_loader.py` / `db.py` / `streamlit_app.py` / `state.py` / `backtest_logger.py`

---

## 1. 架构总览

### 1.1 系统分层图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Presentation Layer                                 │
│  streamlit_app.py                                                          │
│  _render_chart_fragment() → _render_chart() → Plotly figures               │
│  _run_backtest_play() → bar_index/cutoff_date 状态机                        │
│  _render_backtest_mode() → 模式切换 / Slider / 播放控制                      │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────────────┐
│                          Orchestration Layer                                │
│  _load_chart_data() — 数据加载入口                                          │
│  _cached_fetch_stock() — API 回退路径                                       │
│  _sync_to_display() — DB→Parquet 同步桥梁                                   │
└──────────┬───────────────────────────────────────────────┬──────────────────┘
           │                                               │
┌──────────▼──────────────┐               ┌───────────────▼──────────────────┐
│   Data Access Layer     │               │      Cache Layer                 │
│   data_loader.py        │               │   data/display/{tf}.parquet      │
│   db.py                 │               │   data/backtest_config.json      │
│                         │               │                                  │
│   _fetch_all_timeframes │               │   每个 tf 一份 parquet            │
│   _fetch_stock          │               │   覆盖写入, 无版本管理             │
│   upsert_kline          │               │                                  │
│   query_kline           │               │                                  │
│   compare_with_db       │               │                                  │
│   force_update_kline    │               │                                  │
└──────────┬──────────────┘               └──────────────────────────────────┘
           │
┌──────────▼──────────────────────────────────────────────────────────────────┐
│                        Persistence Layer                                    │
│                                                                             │
│  ┌───────────────────────┐   ┌──────────────────┐   ┌───────────────────┐  │
│  │ SQLite (WAL mode)     │   │ Snapshots         │   │ JSONL Logs        │  │
│  │ data/market.db        │   │ data/snapshots/   │   │ data/backtest_    │  │
│  │                       │   │ market_*.db       │   │ logs/*.jsonl      │  │
│  │ kline table           │   │ (checkpoint+copy) │   │ (按天分片)        │  │
│  │ PK:(ticker,tf,ts)     │   │                   │   │                   │  │
│  └───────────────────────┘   └──────────────────┘   └───────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────────────┐
│                        External Data Source                                 │
│  yfinance (Yahoo Finance API)                                               │
│  ThreadPoolExecutor(max_workers=8) — 8 周期并行拉取                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 核心模块关系图

```
                  ┌──────────────┐
                  │  yfinance    │
                  └──────┬───────┘
                         │
              ┌──────────▼──────────┐
              │  _fetch_all_        │  ThreadPoolExecutor(8)
              │  timeframes()       │  并行拉取 8 个周期
              └──────────┬──────────┘
                         │ per (ticker, tf)
              ┌──────────▼──────────┐
              │  _fetch_stock()     │  download → clean → upsert → query
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │  upsert_kline()     │  history→IGNORE / recent→REPLACE
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │  SQLite kline       │  (ticker, timeframe, ts) PK
              └──┬───────┬───────┬─┘
                 │       │       │
    ┌────────────▼──┐ ┌──▼────────────┐ ┌──▼──────────────┐
    │ query_kline() │ │ _sync_to_     │ │ check_data_     │
    │ 浏览/回测双模  │ │ display()     │ │ health()        │
    └───────────────┘ └──┬────────────┘ └─────────────────┘
                         │
              ┌──────────▼──────────┐
              │  Parquet Cache      │  data/display/{tf}.parquet
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │  _load_chart_data() │  parquet→DataFrame→numpy
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │  _render_chart()    │  滤波→施密特→PnL→Plotly
              └─────────────────────┘
```

### 1.3 数据流向总结

```
yfinance API
  → _fetch_stock (拉取 + 清洗 + 日线Close回退)
    → upsert_kline (分段写入 SQLite)
      → query_kline / _sync_to_display (DB → DataFrame)
        → Parquet 缓存 (data/display/{tf}.parquet)
          → _load_chart_data (parquet → numpy arrays)
            → _render_chart (滤波计算 → Plotly 渲染)
```

---

## 2. 数据获取与入库（Layer 1: yfinance → SQLite）

### 2.1 并行拉取策略

`_fetch_all_timeframes()` 使用 `ThreadPoolExecutor(max_workers=8)` 并行拉取 8 个周期的数据:

| 周期 | yfinance interval | force_period | 说明 |
|------|-------------------|-------------|------|
| 1分钟 | `1m` | `7d` | 数据量大，仅取 7 天 |
| 5分钟 | `5m` | `60d` | |
| 15分钟 | `15m` | `60d` | |
| 60分钟 | `1h` | `730d` | |
| 日线 | `1d` | `max` | |
| 周线 | `1wk` | `max` | |
| 月线 | `1mo` | `max` | |
| 季线 | `3mo` | `max` | |

- **异常隔离**: 单个周期拉取失败不影响其他周期
- **as_completed 收集**: 先完成先处理，不阻塞等待

### 2.2 `_fetch_stock` 完整流程

```
1. 代码拼接: A股(.SS/.SZ), 港股(.HK), 美股(大写)
2. Period 计算: 根据 n_pts 动态选择 period，减少数据传输
3. yf.download(full, period, interval)
4. 数据清洗:
   - MultiIndex 列名展平
   - 去除 Close=NaN 的行
5. 日线 Close 回退:
   - 当最后一条日线 Close 为 NaN 时（未结算）
   - 用周线 Close 回填（周线 API 已通过实时行情计算）
6. upsert_kline(code, tf, data) → 全量写入 SQLite
7. query_kline(code, tf, n_pts) → 从 DB 读回返回
```

### 2.3 upsert 分段策略

```
新数据中的每条 bar:
  ts < DB.MAX(ts) → INSERT OR IGNORE (历史 bar，不覆盖)
  ts >= DB.MAX(ts) → INSERT OR REPLACE (最新 bar，可更新)
```

| 场景 | 策略 | 原因 |
|------|------|------|
| 历史 bar（已完成） | `INSERT OR IGNORE` | 历史数据不变，避免重复写入 |
| 最新 bar（可能未完成） | `INSERT OR REPLACE` | 盘中 OHLC 持续更新 |
| 空 DB（首次写入） | 全部走 history 分支 | last_ts=None |

### 2.4 关键边界条件

- **时区**: yfinance 返回带时区时间戳，`isoformat()` 转换后存为 TEXT，字符串比较可能受时区影响
- **历史修正**: `INSERT OR IGNORE` 不更新历史数据，拆股调整需 `force_update_kline()`
- **限流**: 8 线程并行未触发 Yahoo 限流
- **数据量**: 分钟级数据量大（1分钟约 240条/天），长期累积关注 DB 大小

---

## 3. 回测窗口加载（Layer 2: SQLite → Parquet → Chart）

### 3.1 `_sync_to_display` 双模式详解

这是 SQLite 到 Parquet 缓存的桥梁，有两个完全不同的执行路径:

| 特性 | 浏览模式 (`cutoff_date=None`) | 回测模式 (`cutoff_date=YYYY-MM-DD`) |
|------|------------------------------|-------------------------------------|
| DB 查询方式 | `query_kline()` 函数 | 原始 SQL（绕过 query_kline） |
| 日期过滤 | `ts <= MAX(ts) - day_offset` | `ts <= cutoff_date` |
| day_offset | 支持 | 否（固定 0） |
| 排序处理 | `DESC LIMIT` + `iloc[::-1]` | `DESC LIMIT` + `rows.reverse()` |
| parquet 内容 | 从最新（或偏移后）的 N 条 | 截止日期前的 N 条 |
| 数据不足阈值 | `< 5` 条 | 无检查（if rows） |

**回测模式 SQL**:
```sql
SELECT ts, open, high, low, close, volume
FROM kline WHERE ticker=? AND timeframe=? AND ts <= ?
ORDER BY ts DESC LIMIT ?
```

### 3.2 `_load_chart_data` 三层通路

```
_load_chart_data(tf, n_pts, window_start, cutoff_date)
    │
    ├── 回测模式 (window_start != None)
    │   ├── _sync_to_display(cutoff_date=date) → parquet
    │   │   └── 失败 → _cached_fetch_stock() API 回退 + n_pts 截断
    │   └── pd.read_parquet() → DataFrame
    │
    ├── 浏览模式 (window_start == None)
    │   ├── _sync_to_display(day_offset, n_pts) → parquet
    │   │   └── 失败 → _cached_fetch_stock() API 回退（无截断）
    │   └── pd.read_parquet() → DataFrame
    │
    └── Parquet → NumPy 转换
        t = np.arange(len(df), dtype=float)        # x轴: 整数索引[0..n-1]
        noisy = df["Close"].values.ravel()          # 收盘价序列
        ohlc = df[["Open","High","Low","Close"]]    # K线OHLC
        return (t, noisy, ohlc, ticker_code, df.index, None)
```

**关键注意事项**:
- `t` 是 `arange(n)` 整数索引，不是时间戳 — x 轴均匀间距而非真实时间轴
- 日期标签通过 `_date_markers()` 从 `df.index` 提取
- 回退只在 `_sync_to_display` 失败时触发

### 3.3 回测状态机

#### 模式切换流程

```
浏览 → 回测:
  1. _load_backtest_config() 尝试缓存
  2. 缓存未命中 → _get_min_tf_and_count() 从 DB 查询
  3. 设置 AppState: _min_tf, _min_tf_bar_count, _bar_index
  4. 计算 cutoff_date = _get_bar_date_from_db(ticker, min_tf, bar_count-1)
  5. 无缓存时 _save_backtest_config() 写入 JSON

回测 → 浏览:
  1. 捕获退出前参数 (_exit_min_tf, _exit_bar_count)
  2. 清除所有回测状态键
  3. AppState._cb_mode = False
  4. log_mode_switch("exit") → st.rerun()
```

#### bar_index / cutoff_date 关系

```
bar_index   = 窗口在 min_tf 时间轴上的结束位置 (1-based)
cutoff_date = bar_index - 1 位置 bar 的 ts 值
win_start   = max(1, bar_index - min_n_pts + 1)
slider范围   = [min_n_pts, total_bars]
```

#### 导航控制

| 按钮 | 操作 | 边界 |
|------|------|------|
| `⏮` | `_bar_index = min_n_pts` | 最早可显示位置 |
| `◀` | `_bar_index = max(min_n_pts, bar_index-1)` | 不能 < min_n_pts |
| `▶` | `_bar_index = min(total_bars, bar_index+1)` | 不能 > total_bars |
| `⏭` | `_bar_index = total_bars` | 末尾 |

每次导航后: 同步 slider key → 查询 cutoff_date → `st.rerun()`

#### 自动播放

- `_run_backtest_play()` 在 `main()` 第一行调用
- 播放期间: `_bar_index += 1` → 更新 cutoff_date → `time.sleep(1/speed)` → `st.rerun()`
- 播放时 Slider 隐藏，改用 `st.progress` 进度条
- 安全守卫: `_cb_mode=False` 或越界时自动停止

#### Slider 双键设计

```
_bt_slider_pos (Widget Key)     _bar_index (程序状态)
       │                               │
       └── _on_slider_change ─────────►│  (用户拖动)
       ◄── _update_cutoff_and_rerun ───┘  (程序导航)
```

播放期间: Slider 隐藏，只有 `_bar_index` 在递增，避免 Widget 冲突。

---

## 4. 数据结构全景（Layer 3: Schema & State）

### 4.1 SQLite kline 表

```sql
CREATE TABLE kline (
    ticker    TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ts        TEXT NOT NULL,       -- ISO 格式 (如 "2024-06-15T16:00:00+08:00")
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL,
    volume    REAL,
    PRIMARY KEY (ticker, timeframe, ts)
);
CREATE INDEX idx_kline_lookup ON kline(ticker, timeframe, ts);
```

配置: `PRAGMA journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`

### 4.2 Parquet 缓存

```
data/display/{tf}.parquet
列: Date, Open, High, Low, Close, Volume
覆盖写入, 无版本管理
```

### 4.3 AppState 回测键

| 键 | 类型 | 说明 |
|----|------|------|
| `_cb_mode` | bool | 回测模式开关 |
| `_bar_index` | int | 当前 bar 位置 (1-based) |
| `_bt_cutoff_date` | str | 回测截止日期 |
| `_bt_slider_pos` | int | Slider Widget Key |
| `_min_tf` | str | 最精细周期 |
| `_min_tf_bar_count` | int | min_tf 总 bar 数 |
| `_is_playing` | bool | 自动播放状态 |
| `_play_speed` | float | 播放速度 |

写入机制: `AppState.set(k, v)` 同时写入 `k` 和 `_imp_k`（备份），防止 Streamlit rerun 丢失。

### 4.4 JSONL 日志

```
data/backtest_logs/backtest_YYYYMMDD.jsonl
```

事件类型: `mode_switch`, `bar_navigation`, `data_load`, `error`

每条记录: `{"ts": "ISO时间", "event": "类型", ...具体字段}`

### 4.5 数据快照

- `snapshot_db()`: WAL checkpoint + `shutil.copy2` → `data/snapshots/market_YYYYMMDD_HHMMSS.db`
- `restore_snapshot(path)`: 覆盖 DB + 清理 WAL/SHM
- `prune_snapshots(max_keep=5)`: 保留最近 N 个
- `backtest_config.json`: 缓存最后一次回测配置（ticker, min_tf, bar_count, window_size）

---

## 5. 数据校验流程

### 5.1 `compare_with_db()`

对比 yfinance 新拉取数据与 DB 中已有数据:

```
输入: ticker, tf, df_fetched (yfinance DataFrame)
处理:
  1. 从 DB 读取所有 (ts, close) → db_dict
  2. 从 df_fetched 提取 (ts, close) → yf_dict
  3. 交集 ts 计算 MD5 fingerprint
  4. 逐条比较交集 close 值差异 (> 1e-6)
输出:
  status: "ok" | "update_available" | "conflict"
  diffs: [(ts, db_close, yf_close), ...]
  only_db / only_yf: 仅在 DB/仅在 yfinance 中的 bar 数
```

### 5.2 `force_update_kline()`

当检测到数据冲突时，强制更新 DB:

```
1. DELETE FROM kline WHERE (ticker, tf, ts) IN (df 中的 ts)
2. upsert_kline(ticker, tf, df) → 重新写入
```

此操作: 删除重叠时间戳的旧数据 → 插入修正后的新数据。非重叠的历史数据保留。

### 5.3 `check_data_health()`

健康检查覆盖:
- **行数统计**: 每个 (ticker, tf) 的 bar 数量
- **日期范围**: MIN/MAX ts
- **空值检测**: close IS NULL 计数
- **缺口检测**: 使用 `LAG()` 窗口函数检测相邻 bar 间距 > 3倍预期间隔
- **数据过期**: 日线最新数据 > 7天未更新

返回值: `{status, summary, details[], issues[]}`

---

## 6. 痛点与改进方向

### 6.1 已发现的架构问题

1. **`_sync_to_display` 回测模式绕过 `query_kline()`**:
   - 回测模式直接写原始 SQL，与浏览模式走不同代码路径
   - 两套逻辑维护成本高，边界条件行为不一致（如数据不足检查）

2. **Parquet 覆盖写入无版本管理**:
   - 每次 `_sync_to_display` 完全覆盖同一 tf 的 parquet
   - 多个视图使用不同 tf 不冲突，但快进退可能引发竞争
   - 无法恢复之前窗口的数据

3. **`backtest_config.json` 不验证新鲜度**:
   - 同一 ticker 切换时不检查 DB 是否有新数据
   - bar_count 可能过期，需手动刷新

4. **x 轴非时间性**:
   - `t = arange(n)` 是整数索引而非真实时间戳
   - 非均匀间隔的 bar（如跳过周末/节假日）在图上显示为均匀间距
   - 时间标注靠 `_date_markers()` 后处理

### 6.2 性能瓶颈

| 瓶颈 | 位置 | 影响 |
|------|------|------|
| yfinance HTTP 延迟 | `_fetch_stock` | 首次拉取等待时间长 |
| 8 线程并发 | `_fetch_all_timeframes` | Yahoo API 潜在限流 |
| 每次 slider 全链路重跑 | `_render_chart_fragment` | 4 视图 x 滤波计算 |
| Plotly 渲染 | `_render_plotly` | 大数据量时前端卡顿 |
| Parquet 写 I/O | `_sync_to_display` | 每次窗口切换都写入 |

### 6.3 数据一致性风险

1. **时区字符串比较**: `ts <= cutoff_date` 是字符串比较，不同时区的 ISO 字符串可能排序异常
2. **跨周期 cutoff 不一致**: `cutoff_date` 从 `min_tf` 表查询，粗周期用同一 cutoff 可能只包含很少 bar
3. **历史数据不可修正**: `INSERT OR IGNORE` 使历史数据固化，拆股/分红调整需手动 `force_update_kline`
4. **缓存一致性**: `backtest_config.json` 不随 DB 更新而刷新

---

## 附录: 源代码索引

| 模块 | 文件 | 核心函数 |
|------|------|---------|
| 数据拉取 | `services/data_loader.py` | `_fetch_all_timeframes`, `_fetch_stock` |
| 数据同步 | `services/data_loader.py` | `_sync_to_display` |
| 数据库 | `db.py` | `init_db`, `upsert_kline`, `query_kline`, `get_conn` |
| 数据校验 | `db.py` | `compare_with_db`, `force_update_kline`, `check_data_health` |
| 快照 | `db.py` | `snapshot_db`, `restore_snapshot`, `prune_snapshots` |
| 图表加载 | `streamlit_app.py` | `_load_chart_data` |
| 回测控制 | `streamlit_app.py` | `_render_backtest_mode`, `_run_backtest_play`, `_on_slider_change` |
| 状态管理 | `state.py` | `AppState`, `ViewState` |
| 日志 | `backtest_logger.py` | `log_mode_switch`, `log_bar_navigation`, `log_data_load`, `log_error` |
