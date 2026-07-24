# Parquet-to-RAM 可行性分析

> **调查范围**: `_sync_to_display` / `_sync_all_cascading` → parquet 落盘 → `_load_chart_data` 读回 这一链条能否改为纯内存数据流。
> **日期**: 2026-07-11

---

## 1. 现状全景

### 1.1 完整数据流

```
浏览器模式：
  _render_chart_fragment
    → _load_chart_data (window_start=None)
      → _sync_to_display(ticker, tf, n_pts)      # 写 parquet
        → query_kline(ticker, tf, n_pts)          # 读 SQLite
        → df.to_parquet("data/display/{tf}.parquet")
      → pd.read_parquet("data/display/{tf}.parquet")  # 立刻读回
      → 返回 t, noisy, ohlc, dates...

回测模式：
  main()
    → _sync_all_cascading(ticker, tfs, cutoff_date, min_tf, n_pts)
      # 遍历 tfs 从细到粗
      → _query_tf_from_db()                        # 读 SQLite
      → _synthesize_incomplete_bar() (若有合成)
      → _write_parquet(tf, combined)               # 每个 TF 写一个 parquet
  per-view:
    → _load_chart_data (window_start=bar_index)
      → pass (_sync_to_display 跳过)
      → pd.read_parquet("data/display/{tf}.parquet")  # 只读
```

### 1.2 参与函数清单

| 函数 | 位置 | 职责 | 读写 |
|------|------|------|------|
| `_sync_to_display` | `data_loader.py:174` | 浏览模式：查 DB → 写 parquet | 写 |
| `_sync_all_cascading` | `data_loader.py:735` | 回测模式：查 DB + 合成分解 → 写 parquet | 写（每个 TF 一个） |
| `_write_parquet` | `data_loader.py:711` | 单 TF 写入 `data/display/{tf}.parquet` | 写 |
| `_load_chart_data` | `streamlit_app.py:164` | 读 parquet → 返回 numpy/DataFrame | 读 |
| `clear_display_cache` | `db.py:497` | 删除 `data/display/*.parquet` | 删除 |
| `query_kline` | `db.py:118` | 从 SQLite 查 K 线数据 | DB 读（上游） |

### 1.3 调用关系

浏览模式（每次拖拽/切换 ticker）：
```
_load_chart_data (每 view 调用)
  → _sync_to_display (写 1 个 parquet)
  → pd.read_parquet (读相同文件)
  ↓
_n_pts={n_pts}, per chart_data call 触发一次 file write+read
```

回测模式（每次 bar 前进触发一次 `main()` 全重跑）：
```
main()
  → _sync_all_cascading (写 N 个 parquet, N=在用的 TF 数量, 最多 4)
  → per-view: _load_chart_data → pd.read_parquet (读 1 个 parquet)
  ↓
每次 rerun 写 N+0 次、读 N 次 parquet
```

### 1.4 数据体积

- 行数: 默认 n_pts=120, 每行 6 列 (Date, Open, High, Low, Close, Volume)
- Parquet 文件大小: 每个 TF ≈ 4-8 KB (120 行)
- 总 volume: 浏览模式 ~8KB/操作, 回测模式 ~24-32KB/操作
- 内存中 DataFrame: 极轻量, ~2-5 KB (pd.DataFrame of 120 × 6 float64 + 1 string)

### 1.5 清理时机

`clear_display_cache()` 在 3 处调用：
1. **数据验证后批量更新** (`streamlit_app.py:1187`): 强制更新了数据后
2. **快照恢复后** (`streamlit_app.py:1618`): 数据库被还原后
3. **DB 导入后** (`streamlit_app.py:1723`): 用户上传了新 .db 文件

此外: 浏览模式每次 `_sync_to_display` 覆盖写入，回测模式每次 `_sync_all_cascading` 覆盖写入。

---

## 2. 设计意图分析：为什么用文件？

### 2.1 跨 rerun 持久化（核心原因）

Streamlit 每次交互都会**从头重跑整个脚本**。局部变量在 rerun 之间全部销毁。文件系统是天然的跨 rerun 持久化层：

```
rerun #1: _sync_to_display writes → parquet exists on disk
rerun #2: _load_chart_data reads → file still there
上  ↑
如果没文件, 每次 rerun 都要重新 query_kline
```

### 2.2 回测无未来信息约束（P1-4）

```python
# ★ P1-4: parquet不存在时返回错误,不回退到yfinance
# (yfinance会返回最新数据破坏时间一致性)
if _is_backtest:
    return None, ..., f"{tf} 数据未就绪，请先加载数据"
```

Parquet 的存在与否是**门控**: 回测模式不通过 parquet 不存在时的 yfinance fallback, 避免"将来数据泄露"(未来的 Close 注入给过去)。如果数据在 RAM 中, 同样需要门控——但 RAM 的生命周期 (session 绑定) 使得门控性质不同。

### 2.3 多 TF 间解耦

回测模式下 `_sync_all_cascading` 为每个 TF 独立写 parquet。`_load_chart_data` 各 TF 独立读。文件系统允许这个写-N-次、读-1-次模式无需共享内存状态。

### 2.4 调试/巡检友好

Parquet 文件可被任何工具 (pandas, parquet-tools, Tdms) 直接读取检查。

---

## 3. RAM 替代方案评估

### 方案 (a): `st.session_state` 缓存 DataFrame

**原理**: 以 `f"display_df_{ticker}_{tf}"` 为键存储在 `st.session_state` 中。

**优点**:
- 无文件 IO, 纯内存读写
- session_state 天然跨 rerun 持久化 (属于同一 session)
- 可以直接存储 DataFrame, 零拷贝
- 清理简单: `del st.session_state[key]`

**风险**:
- ⚠️ **多 tab/multi-session 串数据**: 不同浏览器 tab 属于**不同 session**, 但如果打开多个 tab 操作同一 ticker, 每个 tab 各自有独立 session_state → 不会串。但 Streamlit 的 `st.session_state` 是 per-session 的, 所以这是安全的。
- ⚠️ **数据突变**: `st.session_state.df` 是引用, 下游代码可能原地修改 (mutate)。现有代码读取 parquet 后 `set_index("Date")`、切片等操作不写回, 但若未来路由有突变则会污染缓存。Streamlit 文档建议 `st.cache_data` 因为"it creates a new copy at each function call, making it safe against mutations"。
- **回测门控**: `_sync_all_cascading` 写入 session_state 后, `_load_chart_data` 检查 session_state 中有无 `"display_{tf}"` → 有则读, 无则回测模式报错。等效于文件存在性检测。
- **session 退出后自动释放**: 不占磁盘, 但 tab 关闭后丢失, 不影响下次启动 (下次启动会重新 `_sync_to_display` → 写入 session_state, 等效于重新生成 parquet)。

**改动面**:
- `_sync_to_display` + `_sync_all_cascading` → 改为写 session_state
- `_load_chart_data` → 改为读 session_state
- `clear_display_cache` → 改为 `del st.session_state[keys]`
- `data_loader.py` 目前无 streamlit 依赖, 需要注入或改为在 streamlit_app.py 中组装

### 方案 (b): `@st.cache_data` 缓存查询结果

**原理**: 把 `query_kline(ticker, tf, n_pts, day_offset)` 的结果用 `@st.cache_data` 缓存:

```python
@st.cache_data(ttl=300)
def _cached_query_kline(ticker, tf, n_pts, day_offset=0):
    return query_kline(ticker, tf, n_pts, day_offset)
```

**优点**:
- 自动序列化/反序列化 (安全拷贝, 无突变风险)
- 内置 TTL 策略
- 可用 `_cached_query_kline.clear()` 全局清
- 零额外代码维护

**风险**:
- ⚠️ **缓存键爆炸**: ticker × tf × n_pts × day_offset → 用户每次滑动参数都会产生新缓存项。用 `@st.cache_data` 缓存原始 `query_kline` 结果会在 n_pts 变化时继续缓存, 但如果用户不断调参, cache 持续膨胀。可用 `max_entries` 限制。
- ⚠️ **没有明确的"回测 vs 浏览"区分**: `@st.cache_data` 不知道何时是回测模式 (需要把 cutoff 作为缓存键)。如果同时缓存了带 cutoff 和不带 cutoff 的版本, 需要确保键唯一。
- ⚠️ **序列化开销**: 每次读缓存都反序列化。对 120 行的 DataFrame 可以忽略 (微秒级), 但如果数据量增大到数万行会显著。
- **无法完全替代 cascading 合成**: `_sync_all_cascading` 做了 DB 查询 + 跨 TF 合成两个复杂操作, 合成结果不能仅靠缓存 query_kline 得到。仍需在每次 cutoff_date 变化时重新合成。

**改动面**:
- 可以部分替代 `_sync_to_display` 的 DB 查询, 但不能完全去掉 parquet (还需考虑 cascading 合成结果的缓存)
- `_sync_all_cascading` 的写 parquet → 改为注入 session_state 或另建缓存

### 方案 (c): DuckDB 内存表 / SQLite `:memory:`

**原理**: 用 DuckDB 内存表 (`CREATE TABLE display AS SELECT ...`) 或 SQLite `:memory:` 替代文件 parquet。

**优点**:
- 可以 SQL 查询, 与现有 `query_kline` 风格一致
- DuckDB 可以原生读 DataFrame, 零拷贝
- 内存表在同一连接内跨 SQL 操作持久

**风险**:
- ⚠️ **过度工程**: 数据总量 ~36 KB, 用 DB 内存表属于"用火箭筒打蚊子"
- **内存表不跨 rerun 持久**: `:memory:` 在连接关闭后消失。每次 rerun 需要重建。
- **DuckDB 引入新依赖**: 需要在 filter 中加入 duckdb。现有项目只有 sqlite3 + pandas。
- 相对纯 DataFrame 方案没有额外收益

**结论**: 不推荐。没有解决 file IO 的核心问题, 反而增加了复杂度。

### 方案 (d): 直接返回 DataFrame（纯函数化 `_load_chart_data`）

**原理**: 让 `_load_chart_data` 直接调用 `query_kline` (浏览模式) 或 cascading 合成 (回测模式), 不走 parquet 中间层。

**本质** = 方案 (a) 但没有 session_state 缓存, 每次 rerun 都查 DB。

**风险**:
- ⓪ **每次 rerun 都查 SQLite**: 当前代码已经这么做了 (`_sync_to_display` 每次都会 query_kline 然后写 parquet), 所以只是去掉了 file IO。对浏览模式而言, query_kline 是轻量 SQLite 查询 (120 行), 预计 <1ms, 无性能问题。
- ⚠️ **回测模式问题**: `_sync_all_cascading` 做了 cascading 合成, 这个合成是**cutoff_date 维度的计算**。如果走纯函数, 每次 rerun 都重新合成, 对性能影响更大。而且合成涉及 cache 跨 TF 传递 (`synth_cache`), 纯函数化需要把这个缓存也放入 session_state。

---

## 4. 关键约束核对

### 4.1 回测时间一致性 (P1-4)

**现状**:
- `_sync_all_cascading` 从 DB 查询截止到 `cutoff_date` 的数据 → 合成 → 写入 parquet
- `_load_chart_data` 在回测模式读取 parquet, **不存在则报错, 不 fallback 到 yfinance**
- 这确保同一个 cutoff_date 下, 所有 TF 的数据都在这个时间点冻住, 不会因为 yfinance 返回最新数据而泄露未来信息

**RAM 方案保障**:
- 浏览模式 (`_sync_to_display`): 仍然调用 `query_kline` (SQLite 查询), 写入 `st.session_state[f"display_{ticker}_{tf}"]`。不涉及 yfinance → 无泄露。
- 回测模式 (`_sync_all_cascading`): 写入 `st.session_state[f"cascading_{ticker}_{tf}"]`。当 session_state 中没有 `cascading_*` 键时, `_load_chart_data` 报错 "数据未就绪"。
- 关键升级: `_sync_all_cascading` 需要把合成的 `combined` DataFrame 直接存入 session_state, 同时把 `cutoff_date` 也存入 session_state 作为佐证, 确保后续读到的合成数据与当前 cutoff_date 匹配。
- 如果用户拖拽回测 slider 到新 cutoff_date, `main()` 中的 `_sync_all_cascading` 会**先执行** → 更新 session_state → 然后 chart 读取。与当前 parquet 覆盖写入流程完全一致。

**结论**: 可行, 只需在 session_state 中也记录 `cutoff_date` 用于断言。

### 4.2 多 tab / multi-session 隔离

- `st.session_state` 是 **per-session** 隔离的。不同浏览器 tab 之间互不干扰。
- 但同一 tab 内 rerun 之间共享 session_state → 这正是我们需要的跨 rerun 持久化。
- **潜在问题**: 如果用户在同一 browser 打开两个 tab, 操作不同 ticker, 各自 session_state 独立, 不会串。
- **潜在问题**: 如果在 session_state 中缓存了 `ticker="AAPL"` 的数据, 然后用户在侧边栏切换到 `ticker="MSFT"` → `_sync_to_display` 以新 ticker 写入 → `_load_chart_data` 正确读到新数据 → 旧数据被覆盖。不会串。
- ⚠️ **但需要注意**: 如果 `_sync_to_display` 中的 ticker 参数不参与 session_state 键名, 会导致键冲突。方案必须确保键中包含 `ticker + tf + cutoff_date`(回测时)。

### 4.3 缓存失效 / 数据更新一致性

**现状**: 每次调用 `_sync_to_display` 或 `_sync_all_cascading` 都**覆盖写入** parquet。

**RAM 方案**:
- 浏览模式: 每次 `_load_chart_data` 调用 → `_sync_to_display` → 覆盖 session_state[f"display_{ticker}_{tf}"]。与现状语义等价。
- 回测模式: 每次 `main()` → 覆盖 session_state[f"cascading_{ticker}_{tf}"]。与现状语义等价。
- `clear_display_cache` 等效: 删 session_state 中 `display_*` 和 `cascading_*` 前缀的所有键。不需要文件删除。

### 4.4 `data_loader.py` 的无 streamlit 依赖约束

当前 `data_loader.py` 的文档字符串写着"无 Streamlit 依赖, 仅基础库 + db 模块"。如果走 `st.session_state` 方案:
- 选项1: 在 `data_loader.py` 中 `import streamlit as st` → 引入新依赖, 违反模块化约定
- 选项2: `_sync_to_display` 和 `_sync_all_cascading` 改为返回 DataFrame, 由调用方 (`streamlit_app.py`) 决定写入 session_state 还是 parquet → 保持 data_loader 纯洁

**推荐选项2**: 把数据加载 (查询 DB、合成) 和 数据存储 (写 parquet / 写 session_state) 分离。

---

## 5. 推荐路径

### 5.1 推荐方案

**方案 (a) `st.session_state` 为主, 辅以函数化重构**。理由:

| 维度 | (a) session_state | (b) cache_data | (c) DuckDB | (d) 纯函数 |
|------|-------------------|----------------|------------|------------|
| 改动面 | 中 | 小 | 大 | 小 |
| 回测一致性保障 | 强(需键设计) | 中(需键设计) | 强 | 弱(重复计算) |
| 多session隔离 | 完全隔离 | 完全隔离 | N/A | N/A |
| 跨rerun持久 | 是 | 是 | 否 | 否 |
| 性能 | 直接引用快 | 反序列化稍慢 | 中等 | 最快 |
| 代码复杂度 | 中 | 低 | 高 | 低 |
| 数据安全(防突变) | 弱(引用) | 强(深拷贝) | 强 | 强 |

**综合**: session_state 在需要跨 rerun 持久化方面符合原始需求, 且可精确控制键空间。配合深拷贝 (`df.copy()`) 或不可变返回模式可缓解突变风险。

### 5.2 迁移步骤

```
Step 1: 重构 data_loader._sync_to_display 和 _sync_all_cascading
  - 改为返回 DataFrame (而非写文件)
  - 签名: _sync_to_display(...) -> Tuple[bool, int, Optional[pd.DataFrame]]
  - _sync_all_cascading -> Dict[str, Tuple[bool, Optional[pd.DataFrame]]]
  - 保证 data_loader.py 无 streamlit 依赖
  验证: 现有 test_data_loader.py 的 parquet 断言改为 DataFrame 断言

Step 2: 在 streamlit_app.py 中添加 session_state 读写
  - 新函数: _store_display_df(key, df), _load_display_df(key)
  - 键模式:
    浏览: "display_df_{ticker_code}_{tf}"
    回测: "cascading_df_{ticker_code}_{tf}_{cutoff_date}"
  - 修改 _load_chart_data: 先查 session_state → 无则调 _sync_to_display/报错
  - 修改 main(): _sync_all_cascading 后把返回的 df 写入 session_state

Step 3: 修改 clear_display_cache
  - 删除所有 "display_df_" 和 "cascading_df_" 前缀的 session_state 键
  - 可保留 parquet 物理删除作为 fallback (兼容旧状态)

Step 4: 更新测试
  - test_data_loader.py: 改为 mock session_state
  - test_cascading_synthesis.py: 改为 assert DataFrame match
```

### 5.3 风险清单

| # | 风险 | 等级 | 说明 | 缓解 |
|---|------|------|------|------|
| 1 | **突变污染** | **高** | 下游代码修改 session_state 中的 DataFrame 引用, 导致后续 rerun 读到脏数据 | 存入时 `df.copy()`, 读取时文档明示只读; 或始终在 session_state 中存序列化 bytes (降低性能) |
| 2 | **合成 cutoff_date 不一致** | **中** | 用户回测拖 slider 产生新 cutoff_date → `_sync_all_cascading` 覆盖 session_state。但如果 `_sync_all_cascading` 执行异常 (DB 查询失败, 部分 TF 合成失败), session_state 中残留旧数据 → 图表显示与 cutoff_date 不匹配 | 在 session_state 中同时存入 `cutoff_date` 佐证; 读取时检查; `_sync_all_cascading` 失败时应清对应 TF 的 session 缓存 |
| 3 | **session 启动空缓存** | **低** | 第一次 rerun 时 session_state 中无数据 → `_load_chart_data` 走 fallback。在浏览模式会调用 `_sync_to_display` (触发 query_kline + 写入 session_state); 在回测模式 `_sync_all_cascading` 在 main() 中先执行 → 新用户第一次看到图表的时间不变 | 无影响, 与当前 parquet 不存在的路径一致 |
| 4 | **大量用户 tab 内存膨胀** | **低** | 每个 tab 都存一份 36KB DataFrames。100 个 tab ~3.6 MB。不影响服务器内存。 | 无需处理 |
| 5 | **P1-2 回测日志关联断开** | **低** | 现有 `_sync_all_cascading` 返回 dict, 写入文件后 `_load_chart_data` 读回并调用 `log_data_load`。RAM 方案需确保回测日志仍能获取到 `len(df)` 信息 | 日志在 `_load_chart_data` 中记录, 不受存储位置影响 |
| 6 | **`@st.cache_data` 与 session_state 协同** | **低** | 如果同时使用 `@st.cache_data` 缓存 `_cached_fetch_stock` 和 session_state 缓存 display_df, 两者需要一致的失效策略。`clear_display_cache` 调用处目前也调用了 `_cached_fetch_stock.clear()` | 保持 `_cached_fetch_stock.clear()` + session_state 清理的同步 |

### 5.4 维持现状的理由

如果以下至少一条成立, 建议不迁移:
- 你觉得 ~36KB parquet IO 不是性能瓶颈 (当前每个交互 <5ms 的 file IO, 人类无感知)
- 你不愿承担突变污染的维护成本
- 你喜欢 parquet 文件的"可检查性" (开发调试时直接看文件)

但若目标是**减少不必要的文件 IO、简化数据流、未来支持更多并发 session**, RAM 方案是合理且安全的改进。

---

## 6. 附录: 关键代码位置

| 标识 | 文件 | 行号 |
|------|------|------|
| `_sync_to_display` | `filter/services/data_loader.py` | 174 |
| `_sync_all_cascading` | `filter/services/data_loader.py` | 735 |
| `_write_parquet` | `filter/services/data_loader.py` | 711 |
| `_load_chart_data` | `filter/streamlit_app.py` | 164 |
| `clear_display_cache` | `filter/db.py` | 497 |
| `query_kline` | `filter/db.py` | 118 |
| `_cached_fetch_stock` | `filter/streamlit_app.py` | 66 |
| `P1-4 门控` | `filter/streamlit_app.py` | 173-174, 215-216 |
| 浏览模式 parquet 写入 | `filter/services/data_loader.py` | 225 |
| 回测模式 parquet 写入 (循环) | `filter/services/data_loader.py` | 802 |
| 回测 cascading 调用 | `filter/streamlit_app.py` | 1871 |
| 浏览模式 _sync_to_display 调用 | `filter/streamlit_app.py` | 178 |
| parquet 读取 | `filter/streamlit_app.py` | 187 |
