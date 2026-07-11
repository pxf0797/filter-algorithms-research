# 工程设计总览（据实）— filter_research

> 本文据当前代码实测撰写（非旧蓝图），关键处标注函数名与 `文件:行`。
> 仓库根：`/Users/xfpan/claude/filter_research`，主包 `filter_app/`。
> 撰写基准：`filter_app/` 9 个核心 Python 文件（合计约 6237 行）+ `tests/`（`pytest --collect-only` 实测 **815** 个用例）。

---

## 1. 系统概述与目标

一个 **Streamlit 单页应用**，用于股票多周期 K 线的滤波—信号—策略研究与可视化。核心流水线：

```
拉取多周期 K 线(SQLite) → 滤波 → 施密特触发器出信号 → 信号段配对(pairs)
    → 抛物线预测 → 策略 PnL 回测 → Plotly 多子图可视化
```

产品形态与目标：
- **4 视图并列**（v0~v3），各视图独立选周期/滤波/参数，默认周期 `["日线","60分钟","15分钟","5分钟"]`（`sidebar.py:14 DEFAULT_TFS`），2×2 网格布局。
- **两种运行模式**：浏览模式（取最新 N 条窗口，支持按天前后移）与回测模式（按 bar 逐步/自动播放，级联合成未完成 K 线，避免未来信息泄漏）。
- **配置持久化**：JSON 导入/导出 + SQLite 预设库两条路径；参数以 `VIEW_PARAM_SPECS` 为单一真源。
- 版本号 `pyproject.toml` 记为 `10.5.0`。技术栈：streamlit 1.57 / plotly 6.7 / numpy 2.2 / scipy 1.17 / pandas 2.3 / statsmodels 0.14 / yfinance 1.4（`filter_app/requirements.txt`）。

---

## 2. 总体架构与模块职责（文件级依赖关系）

分层：UI 编排层（Streamlit）→ 组件层 → 服务层（纯计算/数据）→ 数据层（SQLite/Parquet）。

| 文件 | 行数 | 职责 | 关键对外符号 |
|---|---:|---|---|
| `streamlit_app.py` | 1912 | 应用入口、`main()` 编排、chart 构建、侧边栏各分区、回测导航/播放 | `main`、`_render_chart`、`_determine_subplot_layout`、`_compute_*` 系列 |
| `services/filter_engine.py` | 1029 | **纯函数、无 Streamlit 依赖**：滤波器注册表、施密特触发、拟合、策略 PnL、跨周期对齐 | `FILTERS`、`_schmitt_trigger`、`_find_all_pairs`、`_fit_*`、`_compute_strategy_pnl`、`_align_pnl_to_current_tf`、`_compute_holding_masks` |
| `services/data_loader.py` | 816 | yfinance 拉取、写 DB、查询、display parquet 同步、**级联合成**(回测) | `_fetch_all_timeframes`、`_fetch_stock`、`_sync_to_display`、`_sync_all_cascading` |
| `components/charts.py` | 455 | Plotly 图构建/HTML 渲染、持仓色块、跨周期/同向性子图 | `_render_plotly`、`_add_prediction_traces`、`_add_cross_pnl_subplot`、`_add_alignment_subplot`、`_draw_holding_bands`、`_contiguous_runs` |
| `components/sidebar.py` | 328 | 单视图参数面板、滑块渲染、周期层级表 | `_render_params`、`ALL_TFS`、`DEFAULT_TFS`、`TF_HIERARCHY` |
| `config_db.py` | 665 | 配置 SQLite（预设/标的/历史）、`VIEW_PARAM_SPECS` 单一真源、`collect_current_params` | `list/get/save/delete/rename/apply_preset`、`VIEW_PARAM_SPECS`、`import_json_files_as_presets` |
| `db.py` | 644 | 市场数据 SQLite（`kline` 表）、健康检查、快照、DB 对比/强更 | `get_conn`、`init_db`、`upsert_kline`、`query_kline`、`check_data_health`、`snapshot_db`、`compare_with_db` |
| `state.py` | 313 | 集中式 `session_state` 封装（含 `_imp_` 备份） | `AppState`、`ViewState` |
| `backtest_logger.py` | 75 | JSONL 回测事件日志 | `log_mode_switch`、`log_bar_navigation`、`log_data_load`、`log_error` |

依赖方向（自上而下，无循环）：

```
streamlit_app.py
  ├─ config_db.py ─────────────► db.py(get_conn)   [__main__ 自测时]
  ├─ db.py
  ├─ services/filter_engine.py  (纯计算, 仅 numpy/scipy/pandas/statsmodels)
  ├─ services/data_loader.py ──► db.py
  ├─ components/charts.py ─────► services/filter_engine.py(_compute_holding_masks) + streamlit
  ├─ components/sidebar.py ────► services/filter_engine.py(FILTERS)
  ├─ state.py                   (仅 streamlit, 可无 st 运行)
  └─ backtest_logger.py
```

要点：`filter_engine.py` 是**纯计算核心**（无 st 依赖，便于单测）；`charts.py`/`sidebar.py` 反向复用 `filter_engine` 的计算符号；`data_loader.py` 只依赖 `db.py`。

---

## 3. 数据流水线（逐环节）

主编排在 `streamlit_app.py::main()`（:1751），逐视图渲染在 `_render_chart()`（:616，Step 1~11 有明确注释）。

**① 数据获取**
- 首次进入某 ticker：`_handle_initial_fetch`（:887）→ `data_loader._fetch_all_timeframes`（8 周期并行 `ThreadPoolExecutor`，:19）→ `_fetch_stock`（:60）拉 yfinance、`db.upsert_kline` 写库。
- 单视图取数：`_load_chart_data`（:164）。浏览模式 → `_sync_to_display`（`data_loader.py:174`）把 DB 最新 `n_pts` 条写 `data/display/{tf}.parquet` 再读；回测模式 → 由 `_sync_all_cascading` 预置 parquet，直接读，**parquet 不存在不回退 yfinance**（P1-4，避免时间一致性被最新数据破坏，:214）。
- 日线 Close 回退：yfinance 日线末 bar 未结算(nan)时用周线 Close 回填（`data_loader.py:141-153`）。

**② 滤波** — `_compute_filters`（:220）：从 `FILTERS[cfg["_fid"]]` 取函数，`func(noisy, t, **cfg["pv"])`；双滤波再算 `filtered2`。异常回退 `np.full_like(noisy, nan)`。未缓存（参数含 np.ndarray 不可哈希）。

**③ 施密特触发** — `_compute_schmitt_trigger`（:250）：`v=np.gradient(filtered,t)`、`a=np.gradient(v,t)` → `filter_engine._schmitt_trigger(v,a,ewma_span=ew,k_eps=ke,sigma_min=sm)`。`bar 数 < ewma_span` 返回 None（页面提示降 N_EWMA，:702）。

**④ 配对** — `_find_all_pairs(schmitt["sig"])`（`_render_chart:708`）→ 段收集/同号合并/异号配对，得 `all_pairs=[(pair_start,pair_end),...]`。

**⑤ 预测** — `_compute_prediction_pairs`（:261）：仅当 `show_pred` 且 schmitt 存在；对每个 pair **`pair_end-pair_start>=3` 才拟合**（3 点定二次）。`fit_mode=="parabola"` → `_fit_physics_parabola`，否则 `_fit_parabolic`。产出 `pred_pairs=[{fit_result, fit_start, pair_end}]`。

**⑥ 策略 PnL** — `_compute_strategy_display`（:280）：需 `show_strategy and schmitt and len(pred_pairs)>0`，调 `filter_engine._compute_strategy_pnl(t, filtered, sig, all_pairs, pred_pairs, stop_loss_pct, n_extend)`，得 `long_pnl/short_pnl/trade_records`，并写 `st.session_state[f"_pnl_{tf}"]`（供低周期取高周期 PnL 做跨周期对齐）。摘要 caption：交易数/胜率/多空收益/回撤。

**⑦ 显示** — `_render_chart` Step 9~11（:724+）：`_determine_subplot_layout` 定行数/行高/标题 → `make_subplots` → 逐面板 `_add_*` → `_render_plotly`（自定义 HTML+Plotly.js，跨子图十字光标 + 日期 tooltip）。

跨周期链路：`_render_chart` 开头（:659）用 `TF_HIERARCHY[tf]` 找紧邻高周期，读 `st.session_state[f"_pnl_{higher_tf}"]`，经 `_align_pnl_to_current_tf` 对齐后画高周期持仓/同向性子图。

---

## 4. 关键算法

### 4.1 滤波器（`FILTERS` 注册表，`filter_engine.py:304`）
统一签名 `func(signal, t, **params)`，返回同长数组。注册项结构 `{name, func, params:{pname:(label,min,max,step,default)}}`。共 **10 种**：

| id | 实现 | 关键参数 |
|---|---|---|
| `sma` | `np.convolve` 均匀核（偶窗+1） | window |
| `ema` | pandas `ewm(span,adjust=False)` | span |
| `wma` | 线性递增权重卷积 | window |
| `alma` | 高斯加权窗（offset 控延迟，sigma 控宽） | window/offset/sigma |
| `savgol` | `scipy.savgol_filter`（order≥window 自动降阶） | window/order |
| `kalman` | 1D 恒速卡尔曼([pos,vel]，F/Q/R 迭代) | Q/R |
| `butterworth` | `butter`+`sosfiltfilt` 零相位低通（nyquist=0.5，cutoff≥nyquist 收窄） | order/cutoff |
| `gaussian` | `gaussian_filter1d` | sigma |
| `median` | `medfilt` | window |
| `lowess` | statsmodels `lowess(return_sorted=False)` | frac |

`compute_metrics`（:374）：MSE/RMSE/MAE、SNR 提升(dB)、互相关滞后 lag、二阶差分粗糙度。

### 4.2 施密特迟滞 `_schmitt_trigger`（:434）
- 物理映射：`v`=速度/动量，`a`=加速度。
- EWMA 波动率：`alpha=2/(ewma_span+1)`，递推 `mu_v`、`sigma_v`（:474-477）。
- 自适应死区：`eps_t = k_eps·max(sigma_v, sigma_min)`（:480）。
- 迟滞状态机（:487-514）：
  - 态 0：`a>eps 且 v>0` → +1；`a<-eps 且 v<0` → -1；否则维持。
  - 态 +1：`a<-eps` → 0（否则维持）。
  - 态 -1：`a>eps` → 0（否则维持）。
  - a/v 为 nan 时沿用旧态。
- 返回 `{mu_v, sigma_v, eps, sig, dur}`；`n<ewma_span` → None。

### 4.3 配对语义 `_find_all_pairs`（:520）
1. **段收集**：扫 `sig`，把连续同值(≠0)聚成 `(start,end,val)`。
2. **同号合并**：相邻段若同号则合并（吞掉中间的 0 观望区，如 `+1,0,+1→ 一段多头`，:560-566）。
3. **异号配对**：相邻异号段 `merged[j],merged[j+1]` 生成 `pair=(s1,s2)=(first_seg_start, second_seg_start)`（:569-575）。
- 语义：pair 从**首次入场边缘**起、止于**相反信号的入口边缘**（第二段起点）。`< 2 段` 返回空。
- **推论（已知边界）**：最后一段反转后若无相反段，则不成 pair → **half-pair**（窗口内未平仓头寸），由对齐层做 eod 右延续处理（见 §9）。

### 4.4 拟合
- `_fit_parabolic`（:579，poly2）：`np.polyfit(x_seg,y_seg,2)`。
- `_fit_physics_parabola`（:608，默认）：**锚定终点为顶点** `(x0,y0)=(x[end],y[end])`，仅最小二乘拟合曲率 `a`，`y=a·(x-x0)²+y0`，`b=0,c=y0`；预测段=右半对称。分母 `<1e-12` 返回 None。

### 4.5 策略 PnL — **当前入场/离场逻辑** `_compute_strategy_pnl`（:649）
- **空守卫**：`len(all_pairs)==0 or len(pred_pairs)==0` → 返回两条平线+空交易（:697）。
- **入场**：仅凭 `sig[pair_end]` 方向（`is_long=(sig==1)`、`is_short=(sig==-1)`），**不再依赖抛物线预测**（入场与预测解耦，见 §9）；`entry_idx=pair_end`，`entry_price=filtered[entry_idx]`。
- **预测轨道（可选）**：pair_end 命中 `pred_map` 则取 `a,b,c,x0` 作止损参考；无预测则回退。
- **离场扫描**（entry+1 → 末尾，:738）分段混合（方案 D）：
  - **预测保护期** `i∈[entry+1, entry+n_extend]`：查止损。有预测 → 相对预测价轨道 `pred_val=polyval((a,b,c), i-x0)`；无预测 → 回退 `pred_val=entry_price`（相对入场价固定%）。多头 `cur<pred_val·(1-sl%)`、空头 `cur>pred_val·(1+sl%)` 触发 `stop_loss`。
  - **全程止盈**：`sig` 反转即离场（多头遇 `sig==-1`、空头遇 `sig==1`）→ `take_profit`。
  - 保护期之后止损停用，让利润奔跑；始终未触发 → `exit_idx=n-1, reason="eod"`。
- **收益**：多 `(exit-entry)/entry`、空 `(entry-exit)/entry`。
- **两条独立复利曲线**：`long_pnl/short_pnl` 初值 100，持仓期随价格填未实现，`long_capital/short_capital` 各自 `*=(1+trade_return)`；离场后前向填已实现值；最后前向填充使空仓期为水平直线（:836-849）。
- `trade_records` 每笔含 id/type/entry_idx/exit_idx/entry_price/exit_price/return_pct/exit_reason。

### 4.6 跨周期对齐 `_align_pnl_to_current_tf`（:861）
- **时区归一化** `_normalize_dates`（:902）：tz-aware 保留字面值去时区，统一 datetime64 比较（日内 HKT vs 日线无时区）。
- **PnL 前向填充**：当前周期每个 bar 取 `≤ 该时间戳的最近高周期 bar`（`np.max(np.where(hd<=cd[i]))`，:925-933）。
- **事件映射**：高周期 trade 的 entry/exit_idx 映射到当前周期 bar；
  - **窗口前开仓**：entry 早于窗口起点但 `exit≥cd[0]` → `entry_bar=0`（与 eod 右延续镜像，:947-953）。
  - **eod 离场**：`exit_reason=="eod"` → `exit_bar=n-1`（延续到最右边缘，避免半边多空对停在较早位置，:963-964）。
- 返回 `aligned_long/short + entry_markers + exit_markers`。
- `_compute_holding_masks`（:984）：每个 entry 找 `>` 它的最近同类型 exit，无则到 `n-1`，得 long/short 持仓 bool 掩码。

### 4.7 回测级联合成（`data_loader.py` §Cascading）
`_sync_all_cascading`（:735）从细到粗遍历周期，对非 min_tf 且需合成者（`_needs_synthesis`：`cutoff≥last_ts` 即最后完整 K 线后仍有数据，:490），用紧邻更细周期数据 `_synthesize_incomplete_bar`（:548）聚合出一根未完成 K 线（O=首/H=max/L=min/C=末/V=sum），`_build_output_df` **替换**（而非追加）最后一条 DB 行以避免未来信息，写 parquet。含跨时区真实转换（v3 BUGFIX，`tz_convert`，:593+）与跨周期边界过滤。

---

## 5. 配置持久化

### 5.1 单一真源 `VIEW_PARAM_SPECS`（`config_db.py:587`）
16 条 `(session_state 后缀, cfg 键, 导出默认值)`，覆盖 tf/n/sch/pred/ke/sm/ew/fm/next/fc/fc2/strat/sl/cross_pnl/align/pnlfb。新增/删除可持久化参数**只改这一处**，JSON 导出与 DB 收集都从它驱动，避免两处键表漂移（历史 bug：`show_pnl_feedback` 曾两处漏存）。派生 `VIEW_PARAM_SUFFIXES`。

### 5.2 读取侧（通用）
`sidebar._render_params`（:107）与 `state.AppState.get` 统一采用 `主 key → _imp_{key} 备份 → 默认` 三级回退（`_imp_` 备份防 Streamlit rerun 丢参，`state.py:120`/`AppState.set` 同时写主键与备份）。视图参数、滤波参数（中文标签 key）都走此通用读取，不依赖具体来源。

### 5.3 写入侧（两条路径，均由真源驱动）

**路径 A：JSON 导入/导出**
- 导出 `_render_export_config`（:1647）：全局键 + 对每个 cfg 调 `_view_export_params`（:1638，按 `VIEW_PARAM_SPECS` 生成 `v{i}_{suffix}`）+ 各视图滤波参数（`{label}_v{i}_f1_{fid}`）。
- 导入 `_render_config_import`（:837）：读上传 JSON，逐键 `AppState.set` 写入 session_state，用 md5 去重（`_import_data`）。

**路径 B：DB 预设库**
- `collect_current_params`（`config_db.py:608`）：读 session_state 全局键 + `v0~v3 × VIEW_PARAM_SUFFIXES` + 中文前缀滤波键，产出扁平 dict。
- 存 `config_presets`（`save_preset` 原子 UPSERT，:211）；应用 `apply_preset` 解析 JSON → `_pending_apply_params` → `_handle_pending_apply`（:826）逐键写回。
- 启动一次性 `import_json_files_as_presets`（:515）：把 `config/*.json`（现 10 个，如 `AAPL_US.json`、`2382_HK_DP.json`）按后缀推断分类（`_DP`→双滤波、`_QS`→快速、否则单滤波）导入预设表。删除预设时 `delete_preset` 同步删对应 JSON 文件防重导入（:294）。

UI 侧 `_render_preset_selector`（:927）提供搜索/应用/更新/重命名/删除/另存，均走确认流（`_preset_action` 状态机）。

---

## 6. 显示层

### 6.1 动态子图布局 `_determine_subplot_layout`（:317）
按 `has_s/has_strategy/has_cross/has_alignment` 组合返回 `(rows, row_heights, titles, mr, rr, vr, sar, ssr, ar, pnl_row, cross_row, align_row)`。分支：
- 无施密特：4 行（价格&滤波 / 残差 / 速度 / 加速度）。
- 有施密特无策略：5 行（+ a&±ε / Sig_t）。
- 有策略：6 行（+ PnL）；有跨周期：7 行（+ 高周期持仓状态）；再有同向性：8 行。
`_insert_feedback_row`（:357）在 PnL 下插「实际持仓状态」行，从 PnL 行高匀 14% 给它，并顺延 cross/align 行号。图高 `fh` 按 compact/施密特/cross/alignment 递增（:788）。

### 6.2 各面板（`_add_*`）
- **价格&滤波**（`_add_main_price_traces`:375）：K 线 + 收盘线 + 滤波1/2；叠加 `_add_prediction_traces`（`charts.py:210`）画拟合(橙实线)/预测(紫虚线)及残差子图。
- **残差 / 速度 / a&±ε**（`_add_residual_traces`:392、`_add_schmitt_traces`:410）：残差(点线)、速度 v、加速度 a、`±ε` 死区带、`σ(v)`、Sig 阶梯线，并按 `all_pairs` 画配对色带（方向取 `sig[p_end]`）。
- **PnL**（`_add_pnl_traces`:447）：多/空 PnL 曲线 + 逐笔加粗段 + ▲入场/离场标记(止损 x/止盈 ○) + 收益标注 + 100 基线 + 收益区背景。
- **实际持仓状态**（`_add_feedback_subplot`:490）：由 `trade_records` 的 entry→exit 直接建 long/short 掩码 → `_draw_holding_bands`。

### 6.3 持仓状态色块与同向性（`charts.py`）
- `_draw_holding_bands`（:366）：**双轨色块**，上轨绿=做多持仓、下轨红=做空持仓、空白=不持；y 为类别轴（做多/做空，无百分比）。当前周期「实际持仓状态」与高周期状态子图**共用此绘制**。
- `_add_cross_pnl_subplot`（:383）：**高周期持仓状态**——用 `_compute_holding_masks` 从对齐后 entry/exit_markers 得高周期持仓区间，再 `_draw_holding_bands`（设计上替代了旧的“高周期 PnL 参考曲线”）。
- `_add_alignment_subplot`（:393）：**同向性判断**——高周期持仓时才 sample 本周期 PnL 增量，非持仓时 hold（`long_filtered/short_filtered` 递推，:398-410），叠加逐笔段与标记；用于判断本周期与高周期方向是否一致。
- `_render_plotly`（:19）：把 fig 转 JSON（`_NpEncoder`+`_sanitize_for_json` 处理 NaN/Inf→null），内联自建 HTML + Plotly.js CDN（含 fallback），实现跨子图竖直十字光标 + 悬停日期 tooltip；日期存于 `layout._dates`。

---

## 7. 数据存储（SQLite / Parquet / JSON）

**市场数据 `data/market.db`（约 21 MB，`db.py`）**
```sql
CREATE TABLE kline (
  ticker TEXT, timeframe TEXT, ts TEXT,
  open REAL, high REAL, low REAL, close REAL, volume REAL,
  PRIMARY KEY (ticker, timeframe, ts));
CREATE INDEX idx_kline_lookup ON kline(ticker, timeframe, ts);
```
- WAL 模式、`synchronous=NORMAL`、`busy_timeout=5000`（`get_conn`:19）。
- `upsert_kline`（:67）：历史 bar `INSERT OR IGNORE`、最新 bar `INSERT OR REPLACE`（允许覆盖未完成 bar）。
- `query_kline`（:118）双模式：浏览（`offset=None`，取最新 n_pts，支持 day_offset，降序取后翻转升序）/ 回测（`offset=N`，`LIMIT n_pts OFFSET N` 升序）。
- 可靠性：`check_data_health`（缺口/空值/新鲜度，:252）、`snapshot_db`/`list/restore/prune_snapshots`、`compare_with_db`（重叠 MD5 指纹判 ok/update_available/conflict，:515）、`force_update_kline`（:609）。

**配置数据 `data/config.db`（`config_db.py`，与 market.db 分离）**
- `config_presets(preset_id PK, name UNIQUE, description, category, params_json, created_at, updated_at)`
- `config_ticker(ticker, variant, market, preset_id→presets ON DELETE SET NULL, params_json, PK(ticker,variant))`
- `config_history(id PK, ticker, variant, preset_id, old_json, new_json, changed_at, source, FK→config_ticker)` + `idx_history_lookup`
- `init_config_tables`（:91）含一次性迁移（旧 FK 无 `ON DELETE SET NULL` 则重建 config_ticker 表）。

**其它文件产物**
- `data/display/{tf}.parquet`：显示缓存（仅当前窗口/回测窗口数据，8 个周期文件）。
- `data/backtest_config.json`：回测配置缓存（ticker/min_tf/bar_count/window_size）。
- `data/backtest_logs/backtest_YYYYMMDD.jsonl`：回测事件日志（`backtest_logger.py`）。
- `config/*.json`：10 个预设源文件（AAPL_US、2382_HK/_DP/_QS、3690_HK*、600115_SS*）。

---

## 8. 测试体系概览

- 配置：`pytest.ini` + `pyproject.toml [tool.pytest.ini_options]`，`addopts=-v --tb=short --strict-markers`；markers：`slow/filter/signal/strategy/alignment`。覆盖率门槛 `fail_under=55`（`pyproject.toml`）。CI 有 pre-commit（ruff line-length 120）+ pip-audit。
- 规模：`tests/` 共 **23 个 `test_*.py`**，`grep def test_` 计 772，`pytest --collect-only` 实测 **815** 个用例（含参数化）。
  > 注：`tests/README.md` 记为“333 个”，已**明显过时**，与实际 815 不符（据实以 collect-only 为准）。
- 组织（按主题）：
  - 计算核心：`test_filters.py`(22)、`test_signals.py`(16)、`test_strategy.py`(20)、`test_boundary.py`(30)、`test_cascading_synthesis.py`(53)、`test_backtest.py`(75)、`test_alignment.py`(10)、`test_alignment_subplot.py`(14)。
  - 显示：`test_charts.py`(86)、`test_feedback_subplot.py`(6)。
  - 配置/状态：`test_config_db.py`(58)、`test_preset_ui.py`(60)、`test_preset_ui_actions.py`(45)、`test_param_export_import.py`(18)、`test_state.py`(62)、`test_sidebar.py`(36)。
  - 数据：`test_db.py`(57)、`test_data_loader.py`(30)。
  - App/集成：`test_app_ui.py`(33)、`test_streamlit_app.py`(25)、`test_app_smoke.py`(1)、`test_integration.py`(6)、`test_integration_flows.py`(9)。
- `conftest.py` 提供共享 fixtures + `WidgetAwareSessionState`（检测 widget 生命周期约束，模拟 Streamlit session_state）。
- 辅助工具：`tools/trace_long_veto.py`（跑真实 DB 复现做多丢单）、`tools/filter_comparison_tool.py`。

---

## 9. 近期关键设计决策与已知边界

**关键决策**
1. **入场与预测解耦（方案 A）** — `docs/backtesting/long-entry-drop-rootcause.md`。原先 `_compute_strategy_pnl` 对无预测的 pair `continue` 跳过，导致“上涨快速回踩”行情里做多被系统性丢弃（跨度<3 无抛物线→无预测→漏单）。现改为入场仅凭 `sig` 方向、无预测也入场；预测降级为“有则用作止损轨道，无则回退相对入场价固定%”（`_compute_strategy_pnl:709-757`）。
2. **持仓状态可视化替代 PnL 参考** — `docs/backtesting/cross-period-position-state-design.md`。高周期子图从“PnL 曲线+标记”改为**双轨持仓色块**，复用 `_compute_holding_masks`+`_draw_holding_bands`；当前周期「实际持仓状态」与高周期状态**同一绘制逻辑**，仅掩码来源不同。
3. **eod / 窗口前对齐镜像** — `_align_pnl_to_current_tf`：eod 离场延续到最右 bar（`exit_bar=n-1`）、窗口前开仓延续到 bar0（`entry_bar=0`），保证跨周期半边多空对在低周期正确铺展（`filter_engine.py:947-964`）。
4. **参数单一真源 `VIEW_PARAM_SPECS`** — 消除“导出键表 vs DB 收集键表”双写漂移（历史 `show_pnl_feedback` 漏存 bug）。读取侧通用三级回退（含 `_imp_` 备份），写入侧由真源驱动。
5. **回测无未来信息** — parquet 不回退 yfinance（P1-4）；级联合成用**替换**末条 bar 而非追加；`_needs_synthesis` 用 `cutoff≥last_ts`（修正日线及以上周期永不合成的旧 bug）。

**已知边界 / 风险点**
- **half-pair（半边多空对）**：`_find_all_pairs` 只对相邻异号段配对，最后一段反转后无相反段则不成 pair，窗口内该头寸不闭合；靠对齐层 eod 右延续兜底（`docs/backtesting/half-pair-trading-strategy*.md` 系列）。
- **`pred_pairs` 空守卫仍在**：入场虽已与预测解耦，但 `_compute_strategy_pnl:697` 仍有 `len(pred_pairs)==0 → 直接返回空`，且 `_compute_strategy_display:286` 要求 `len(pred_pairs)>0` 才计算。因此若**全部 pair 跨度<3**（无任何预测），策略层整体不出结果——解耦是**部分**的（per-pair 跳过已删，函数级/显示级空守卫未删）。
- **`pair_end-pair_start>=3` 门槛**：仍是预测是否生成的唯一闸门（`_compute_prediction_pairs:269），快速反转段无预测轨道，止损退化为“相对入场价固定%”。
- **死代码（已清理）**：本次审计发现 `_compute_strategy_pnl` 末尾一处不可达的重复 `return`，已移除。
- **文档漂移**：`tests/README.md` 计数（333）与实际（815）严重不符；`docs/` 下并存多版旧蓝图（half-pair v2/v3/v4、redesign-v2 等），阅读时以代码为准。
- **N_EWMA 约束**：`bar 数 < ewma_span` 施密特直接失效返回 None（页面告警），回测窗口过小时需下调 N_EWMA。
