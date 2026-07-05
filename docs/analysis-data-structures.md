# 数据结构与状态管理全景分析

分析日期: 2026-07-05
源代码:
- `/Users/xfpan/claude/filter_research/filter_app/db.py`
- `/Users/xfpan/claude/filter_research/filter_app/state.py`
- `/Users/xfpan/claude/filter_research/filter_app/backtest_logger.py`

---

## 1. 数据存储层次架构

```
┌──────────────────────────────────────────────────────────────────┐
│                        Application Layer                         │
│  streamlit_app.py  ← 通过 AppState / ViewState 读写 session      │
└────────────┬────────────┬────────────┬───────────────────────────┘
             │            │            │
    ┌────────▼───┐  ┌─────▼──────┐  ┌─▼──────────────────────────┐
    │ AppState   │  │ Parquet    │  │ JSONL Logs                 │
    │ (内存)     │  │ Cache      │  │ data/backtest_logs/        │
    │ Streamlit  │  │ data/      │  │   backtest_YYYYMMDD.jsonl  │
    │ session    │  │ display/   │  │                             │
    │ _state     │  │  {tf}.     │  │ Events: mode_switch,       │
    │            │  │  parquet   │  │   bar_navigation,          │
    │            │  │            │  │   data_load, error          │
    └────────────┘  └─────┬──────┘  └─────────────────────────────┘
                          │
              ┌───────────▼───────────┐
              │     SQLite (WAL)      │
              │   data/market.db      │
              │                       │
              │  ┌─────────────────┐  │
              │  │ kline table     │  │
              │  │ PRIMARY KEY:    │  │
              │  │ (ticker, tf, ts)│  │
              │  │ INDEX:          │  │
              │  │ (ticker,tf,ts)  │  │
              │  └─────────────────┘  │
              │                       │
              │  PRAGMA journal_mode= │
              │    WAL                │
              │  PRAGMA synchronous=  │
              │    NORMAL             │
              │  PRAGMA busy_timeout= │
              │    5000               │
              └───────────────────────┘
                          │
              ┌───────────▼───────────┐
              │  Snapshots            │
              │  data/snapshots/      │
              │    market_YYYYMMDD_   │
              │    HHMMSS.db          │
              │  (checkpoint + copy)  │
              └───────────────────────┘
```

---

## 2. SQLite kline 表设计

### 2.1 Schema

```sql
CREATE TABLE IF NOT EXISTS kline (
    ticker    TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ts        TEXT NOT NULL,       -- ISO 格式时间戳 (如 "2024-06-15T16:00:00+08:00")
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

### 2.2 设计要点

| 特性 | 说明 |
|------|------|
| 复合主键 | `(ticker, timeframe, ts)` 三元组唯一标识一条 bar |
| ts 类型 | TEXT 而非 DATETIME — 字符串比较，带时区后缀 |
| volume 类型 | REAL — 支持大数值和 NaN |
| 索引 | `(ticker, timeframe, ts)` 覆盖最常见的查询模式 |
| WAL 模式 | 允许并发读写，避免锁冲突 |
| synchronous=NORMAL | 平衡性能与安全性 |

### 2.3 查询模式总结

| 查询 | SQL 模式 | 使用场景 |
|------|---------|---------|
| 最新 N 条 | `WHERE tk=? AND tf=? AND ts<=? ORDER BY ts DESC LIMIT ?` | 浏览模式 |
| 偏移窗口 | `WHERE tk=? AND tf=? ORDER BY ts ASC LIMIT ? OFFSET ?` | 回测模式 |
| 日期范围 | `WHERE tk=? AND tf=? AND ts<=? ORDER BY ts DESC LIMIT ?` | 回测 cutoff |
| 计数 | `SELECT COUNT(*) WHERE tk=? AND tf=?` | 获取 bar 总数 |
| 日期查询 | `SELECT ts WHERE tk=? AND tf=? ORDER BY ts ASC LIMIT 1 OFFSET ?` | 获取 bar_index 对应日期 |

---

## 3. Parquet 缓存结构

### 3.1 文件布局

```
data/display/
├── 1分钟.parquet      ← 1分钟周期缓存
├── 5分钟.parquet
├── 15分钟.parquet
├── 60分钟.parquet
├── 日线.parquet
├── 周线.parquet
├── 月线.parquet        (如已拉取)
└── 季线.parquet        (如已拉取)
```

### 3.2 Schema

| 列 | 类型 | 说明 |
|----|------|------|
| Date | string → datetime | 写入后由 `pd.to_datetime()` 转换 |
| Open | float64 | 开盘价 |
| High | float64 | 最高价 |
| Low | float64 | 最低价 |
| Close | float64 | 收盘价 |
| Volume | float64 | 成交量 |

### 3.3 生命周期

```
_sync_to_display() 写入
    │
    ▼
pd.to_parquet("data/display/{tf}.parquet", index=False)
    │  覆盖写入（同一 tf 只有一份）
    │
    ▼
_load_chart_data() 读取
    │
    ▼
pd.read_parquet(display_path)
    → df.set_index("Date").sort_index()
    → t = arange(n), noisy = Close, ohlc = OHLC
```

**关键特性**:
- **覆盖写入**: 同一 tf 的 parquet 每次都被完全覆盖，不分片
- **无版本管理**: 旧的 parquet 数据不可恢复
- **无过期机制**: 不会自动清理，需手动 `clear_display_cache()`
- **大小**: 典型 120 条的日线 parquet 约 5-10KB，分钟级稍大

---

## 4. AppState 回测键依赖图

### 4.1 回测相关键清单

```
SYSTEM_KEYS (state.py:28-54)
│
├── _cb_mode: bool          ← 回测模式开关
├── _bar_index: int         ← 当前 bar 位置（1-based，从 slider 同步）
├── _bt_cutoff_date: str    ← 回测截止日期（ISO 字符串）
├── _bt_last_ticker: str    ← 上次回测 ticker
├── _bt_slider_pos: int     ← Slider Widget Key（独立于 _bar_index）
├── _min_tf: str            ← 最精细周期
├── _min_tf_bar_count: int  ← min_tf 的总 bar 数
├── _is_playing: bool       ← 自动播放状态
├── _play_speed: float      ← 播放速度倍率
└── _play_speed_label: str  ← 播放速度显示标签
```

### 4.2 键依赖关系

```
_cb_mode ─────────────────────────────────────────────┐
    │                                                  │
    ├── True → _min_tf, _min_tf_bar_count 被设置       │
    │           _bar_index = bar_count                  │
    │           _bt_slider_pos = bar_count               │
    │           _bt_cutoff_date = date(bar_index-1)     │
    │                                                  │
    │           _is_playing ── True → 每帧递增          │
    │               │             _bar_index += 1       │
    │               │             _bt_cutoff_date 更新  │
    │               │                                  │
    │               └── False → Slider 可手动拖动       │
    │                            _bt_slider_pos → _bar_index
    │                                                  │
    └── False → 所有回测键重置为默认值                  │
```

### 4.3 Slider ↔ _bar_index 双键设计

```
用户拖动 Slider               程序更新 bar_index
      │                              │
      ▼                              ▼
_bt_slider_pos (Widget Key)    _bar_index (程序状态)
      │                              │
      └──── _on_slider_change ──────►│  (非播放时)
                                     │
      ◄──── _update_cutoff_and_rerun ┘  (导航按钮)
```

**设计理由**: Streamlit 不允许同一个 widget key 同时被程序和用户修改。分离两个 key 后:
- 用户拖动 Slider → 修改 `_bt_slider_pos` → `_on_slider_change` 同步到 `_bar_index`
- 程序导航（播放/按钮） → 修改 `_bar_index` → 同步到 `_bt_slider_pos`
- 播放时 Slider 隐藏，只有 `_bar_index` 在变化

### 4.4 AppState API

```python
AppState.get(key, default)     # 读，自动 _imp_ fallback
AppState.set(key, value)       # 写，同时写 _imp_ 备份
AppState.has(key)              # 存在性检查
AppState.pop(key, default)     # 读取并删除
AppState.set_many({k:v, ...})  # 批量写入
AppState.init_defaults()       # main() 开头初始化
```

**_imp_ 备份机制**: 每个 key 写入时同时写入 `_imp_{key}` 备份。Streamlit 的 widget 重建可能导致 session_state 中部分 key 丢失，`_imp_` 备份确保在 fallback 时值不丢失。

---

## 5. JSONL 日志体系

### 5.1 文件布局

```
data/backtest_logs/
└── backtest_YYYYMMDD.jsonl    ← 按天分文件
```

### 5.2 事件类型

| 事件 | 触发时机 | 字段 |
|------|---------|------|
| `mode_switch` | 浏览↔回测切换 | ticker, direction, min_tf, bar_count |
| `bar_navigation` | bar 位置跳转（按钮/slider） | ticker, min_tf, bar_index, total, cutoff_date, elapsed_ms |
| `data_load` | _load_chart_data 完成后 | ticker, tf, bar_count, cutoff_date, elapsed_ms |
| `error` | 回测相关异常 | ticker, location, error |

### 5.3 记录格式

```json
{"ts": "2026-07-04T15:30:00", "event": "mode_switch", "ticker": "AAPL", "direction": "enter", "min_tf": "60分钟", "bar_count": 1234}
{"ts": "2026-07-04T15:30:05", "event": "data_load", "ticker": "AAPL", "tf": "60分钟", "bar_count": 120, "cutoff_date": "2024-06-15", "elapsed_ms": 12.5}
{"ts": "2026-07-04T15:30:10", "event": "bar_navigation", "ticker": "AAPL", "min_tf": "60分钟", "bar_index": 800, "total": 1234, "cutoff_date": "2024-06-15", "elapsed_ms": 8.2}
```

### 5.4 设计特点

- **追加写入**: 只 append，不修改已有记录
- **按天分片**: 每天一个文件，便于归档和清理
- **人类可读**: JSON 格式，可用 `jq` 等工具查询
- **轻量级**: 无依赖，仅标准库 `json` + `pathlib`
- **同步写入**: 每次事件直接 `open().write()`，无缓冲队列

---

## 6. 数据快照系统

### 6.1 操作流程

```
snapshot_db()
    │
    ├── checkpoint_wal()           ← 强制 WAL checkpoint，合并到主文件
    ├── shutil.copy2(market.db)    ← 复制到 snapshots/ 目录
    └── 返回 snapshot 路径

restore_snapshot(path)
    │
    ├── shutil.copy2(snapshot, market.db)   ← 覆盖当前 DB
    └── 删除 .db-wal 和 .db-shm 文件        ← 清理 WAL 残留
```

### 6.2 管理函数

| 函数 | 功能 |
|------|------|
| `snapshot_db()` | 创建当前 DB 的快照 |
| `list_snapshots()` | 列出所有快照（最新在前） |
| `restore_snapshot(path)` | 从快照恢复 DB |
| `prune_snapshots(max_keep=5)` | 只保留最近 N 个快照 |

---

## 7. backtest_config.json 缓存

**文件**: `data/backtest_config.json`

```json
{
    "ticker": "AAPL",
    "min_tf": "60分钟",
    "bar_count": 1234,
    "window_size": 120,
    "cached_at": "2026-07-05T12:00:00"
}
```

- **作用**: 避免每次切换回测重新计算 `_get_min_tf_and_count()`（需遍历配置 + DB 查询）
- **失效条件**: ticker 切换时重新计算；同一 ticker 不验证数据新鲜度
- **风险**: DB 有新数据插入后 bar_count 不会自动更新
