# 实现规格 -- 回测数据采集系统 MVP

> 基于 `/Users/xfpan/claude/filter_research/docs/development/backtest-capture-framework.md` v1.0
> 产出日期: 2026-07-13

---

## 1. BacktestRunner (`filter_app/backtest_core.py`)

### 1.1 模块职责

纯计算编排层。从 `_render_chart()` (streamlit_app.py line 679-881) 中抽取计算管线，剔除所有 Plotly/Streamlit 渲染代码，被 CLI 和 Streamlit 共同调用。

**依赖原则**: 不导入 `streamlit`，不使用 `@st.cache_data`，不访问 `st.session_state`。

### 1.2 类签名

```python
class BacktestRunner:
    """回测计算编排器 -- 零 Streamlit 依赖。

    Parameters
    ----------
    ticker : str
        股票代码, e.g. "03690.HK", "AAPL"。
    market : str
        市场标识, e.g. "港股 HK", "美股 US", "A股(沪深)"。
    configs : list[dict]
        4 个视图的配置 dict 列表 (v0~v3)，每个 dict 至少包含
        {"tf", "n_pts", "_fid", "pv", "show_sch", "ke", "sm", "ew",
         "show_pred", "fit_mode", "n_ext", "show_strategy",
         "stop_loss_pct", "_dual", "_fid2", "pv2"}。
    operating_tf : str
        操作周期, e.g. "日线"。
    min_tf : str
        最细粒度周期 (回测窗口锚定周期), e.g. "60分钟"。
    lower_tfs : list[str]
        需计算 BS 标记的低级周期列表 (来自 get_lower_tfs(operating_tf))。

    Raises
    ------
    ValueError
        当 configs 为空或缺少必需键时抛出。
    """

    def __init__(
        self,
        ticker: str,
        market: str,
        configs: list[dict],
        operating_tf: str,
        min_tf: str,
        lower_tfs: list[str],
    ) -> None:
        ...
```

### 1.3 关键方法

#### `run_step(bar_index: int) -> dict[str, PipelineStageData]`

单步回测计算。是所有调用入口的核心方法。

```python
def run_step(self, bar_index: int) -> dict[str, PipelineStageData]:
    """执行单个 bar 的完整管道计算。

    流程:
      1. 通过 _get_bar_date_from_db(min_tf, bar_index - 1) 获取 cutoff_date
      2. 调用 _sync_all_cascading(ticker, sorted_tfs, cutoff_date, min_tf, n_pts=...)
         将各周期数据写入 data/display/{tf}.parquet
      3. for each view (v0→v3):
           a. 调用 _load_chart_data() 读取 parquet
           b. 调用 compute_pipeline_for_view() 执行完整管道
           c. 收集结果到 PipelineStageData
      4. 返回 {view_name: PipelineStageData} 字典

    Parameters
    ----------
    bar_index : int
        当前窗口结束位置 (1-based，对应 streamlit 中的 _bar_index)。

    Returns
    -------
    dict[str, PipelineStageData]
        键为 view_name (e.g. "v0_日线")，值为该视图的管道阶段数据。

    Raises
    ------
    RuntimeError
        当 _sync_all_cascading() 所有 TF 均失败时抛出。
    """
```

**内部调用链**:

| 步骤 | 调用函数 | 所在模块 | 备注 |
|------|---------|---------|------|
| 数据准备 | `_sync_all_cascading(ticker, tfs, cutoff_date, min_tf, n_pts)` | `services/data_loader.py` line 734 | 写入各 TF 的 parquet 文件 |
| 日期查询 | `_get_bar_date_from_db(ticker, min_tf, bar_index - 1)` | 需从 `streamlit_app.py` line 1373 提取 | 查询 bar 对应的日期字符串 |
| 数据加载 | `_load_chart_data(market, ticker, tf, n_pts, window_start, cutoff_date)` | 当前在 `streamlit_app.py` line 172 | **需提取到共享位置**，或直接内联 parquet 读取逻辑 |
| 管线计算 | `BacktestRunner._compute_pipeline_for_view(...)` | 本文件 (私有方法) | 见下文 |

#### `_compute_pipeline_for_view(cfg: dict, key: str, cutoff_date: str, higher_pnl: dict | None) -> PipelineStageData`

单个视图的完整管道计算 (私有方法)。对应 `_render_chart()` 中 line 729-881 的计算部分。

```python
def _compute_pipeline_for_view(
    self,
    cfg: dict,
    key: str,
    cutoff_date: str,
    higher_pnl: dict | None = None,
) -> PipelineStageData:
    """执行单个视图的完整计算管线。

    管线步骤:
      S1: _load_chart_data(market, ticker, tf, n_pts,
                           window_start=bar_index, cutoff_date=cutoff_date)
          → t, noisy, ohlc, full, dates, err
          若 err 或数据不足 → 返回空 PipelineStageData (所有字段为 None)

      S3: filtered, filtered2 = _compute_filters(noisy, t, cfg)
          注意: 需移除 @st.cache_data 装饰器，或直接调用 FILTERS[id]["func"]
          → filtered: np.ndarray, filtered2: np.ndarray | None

      S4: 若 cfg["show_sch"] 为 True 且 filtered 有效:
            v = np.gradient(filtered, t)
            a = np.gradient(v, t)
            schmitt = _schmitt_trigger(v, a,
                ewma_span=cfg["ew"], k_eps=cfg["ke"], sigma_min=cfg["sm"])
          否则 schmitt = None

      S5: all_pairs = _find_all_pairs(schmitt["sig"]) if schmitt else []

      S6: 若 cfg["show_pred"] 且 schmitt:
            pred_pairs = []
            for (start, end) in all_pairs:
                if end - start >= 3:
                    r = _fit_physics_parabola(t, filtered, start, end)
                    if r: pred_pairs.append({"fit_result": r,
                                             "fit_start": start,
                                             "pair_end": end})
          否则 pred_pairs = []

      S7: 若 cfg["show_strategy"] 且 schmitt 且 pred_pairs:
            long_pnl, short_pnl, trade_records = _compute_strategy_pnl(
                t, filtered, schmitt["sig"], all_pairs, pred_pairs,
                cfg["stop_loss_pct"], n_extend=cfg["n_ext"])
          否则 long_pnl=short_pnl=None, trade_records=[]

      S7.5: 跨周期对齐:
            若 higher_pnl 非 None:
              aligned = _align_pnl_to_current_tf(
                  higher_pnl["dates"], higher_pnl["long_pnl"],
                  higher_pnl["short_pnl"], higher_pnl["trade_records"],
                  dates)
              holding_masks = _compute_holding_masks(
                  len(t), aligned["entry_markers"], aligned["exit_markers"])

      S10: 判断是否需计算 BS 标记:
             show_bs = (tf == operating_tf) or (tf in lower_tfs)
             若 show_bs:
               bs_markers = compute_bs_markers(
                   t, dates, schmitt, all_pairs, trade_records,
                   tf, operating_tf, higher_bs=None,
                   holding_masks=holding_masks)

      返回: PipelineStageData(...) 填充所有计算值

    Parameters
    ----------
    cfg : dict
        视图配置字典。
    key : str
        视图标识, e.g. "v0"。
    cutoff_date : str
        回测截止日期 (ISO 格式)。
    higher_pnl : dict or None
        高周期 PnL 数据 (用于跨周期对齐)。

    Returns
    -------
    PipelineStageData
        该视图的管线阶段数据容器。
    """
```

#### `run(start_bar: int, end_bar: int) -> None`

范围回测驱动循环。依次调用 `run_step()` 并触发记录。

```python
def run(self, start_bar: int, end_bar: int) -> None:
    """执行完整回测范围，逐 bar 计算并记录。

    循环体内:
      1. step_data = self.run_step(bar_index)
      2. self.recorder.record_step(bar_index, cutoff_date, step_data)

    Parameters
    ----------
    start_bar : int
        起始 bar 索引 (1-based)。
    end_bar : int
        结束 bar 索引 (1-based, 含)。
    """
```

### 1.4 需提取的现有函数

以下函数当前定义在 `streamlit_app.py` 中，CLI 需要访问。**策略: 不复制代码**，而是:

1. `_load_chart_data()` (line 172): 在 `BacktestRunner._compute_pipeline_for_view()` 中直接内联 parquet 读取逻辑 (约 15 行)，因为:
   - 浏览模式的 yfinance fallback 分支在 CLI 中不需要
   - `_is_backtest` 变量的作用域问题自然消除
   - `@st.cache_data` 的 `_cached_fetch_stock()` 回退在 CLI 中不需要

2. `_compute_filters()` (line 229): 提取 `_hash_array` 辅助函数并移除 `@st.cache_data`，或直接调用 `FILTERS[id]["func"](noisy, t, **cfg["pv"])`

3. `_compute_schmitt_trigger()` (line 260): 直接使用 `_schmitt_trigger()` from `filter_engine.py` + 自己计算 `v = np.gradient(filtered, t)`, `a = np.gradient(v, t)`

4. `_compute_prediction_pairs()` (line 272): 直接在 `_compute_pipeline_for_view()` 中内联，逻辑简单 (约 15 行)

5. `_compute_strategy_display()` (line 292): 直接调用 `_compute_strategy_pnl()` from `filter_engine.py`

6. `_get_bar_date_from_db()` (line 1373): **提取到 `db.py`** 或作为 `BacktestRunner` 的私有静态方法

### 1.5 `@st.cache_data` 处理策略

| 函数 | 位置 | 处理方式 |
|------|------|---------|
| `_compute_filters` | streamlit_app.py:229 | 直接调用 FILTERS 注册表，不加缓存 |
| `_compute_schmitt_trigger` | streamlit_app.py:260 | 直接调用 `_schmitt_trigger()` + gradient |
| `_compute_prediction_pairs` | streamlit_app.py:272 | 内联 ~15 行逻辑 |
| `_cached_fetch_stock` | streamlit_app.py | CLI 不需要，数据由 `_sync_all_cascading` 准备 |

CLI 为顺序执行，不依赖缓存加速。

### 1.6 异常定义

```python
class BacktestError(Exception):
    """回测执行过程中的可恢复错误。"""
    pass

class DataNotReadyError(BacktestError):
    """_sync_all_cascading() 所有周期均写入失败时抛出。"""
    pass

class ConfigError(BacktestError):
    """配置无效 (缺少必需字段、filter_id 不存在等)。"""
    pass
```

---

## 2. EventRecorder (`filter_app/services/event_recorder.py`)

### 2.1 模块职责

接收每步的 `PipelineStageData`，按分层记录策略写入文件。BS 变动采用 Event Sourcing (增量事件)，Filter Tail 和 Schmitt 变化量采用追加 JSONL。

### 2.2 类签名

```python
class EventRecorder:
    """回测事件记录器 -- Event Sourcing + JSONL 追加。

    目录结构:
      backtest_output/{ticker}_{session_id}/
        ├── metadata.json
        ├── data_manifest.json
        ├── events.jsonl          # BS 变动事件流 (核心)
        ├── filter_tail.jsonl     # 每步 filter 尾部 10 点
        └── steps/                # (MVP 不实现) 可选全量快照

    Parameters
    ----------
    output_dir : str | Path
        输出根目录, 默认 "./backtest_output/"。
    ticker : str
        股票代码。
    config : dict
        回测配置快照, 含 operating_tf, min_tf, lower_tfs, view_configs。

    Raises
    ------
    OSError
        无法创建输出目录时抛出。
    """

    def __init__(
        self,
        output_dir: str = "./backtest_output",
        ticker: str = "",
        config: dict | None = None,
    ) -> None:
        ...
```

### 2.3 关键方法

#### `start_session() -> str`

```python
def start_session(self) -> str:
    """创建会话目录, 写入 metadata.json 和 data_manifest.json。

    目录命名: {ticker}_{YYYYmmdd-HHMMSS}/

    metadata.json 内容:
      {
        "ticker": str,
        "session_id": str,        # "20260713-143022"
        "created_at": str,        # ISO 时间戳
        "config": {...}           # 传入的 config 快照
      }

    data_manifest.json 内容:
      {
        "data_sources": {
          tf: f"data/display/{tf}.parquet" for tf in sorted_tfs
        },
        "n_pts": {tf: n for tf, n in per_tf_n_pts.items()}
      }

    Returns
    -------
    str
        session_id 字符串。

    副作用
    ------
    创建 {output_dir}/{ticker}_{session_id}/ 目录及 metadata.json, data_manifest.json。
    """
```

#### `record_step(step_index: int, cutoff_date: str, views_data: dict[str, PipelineStageData]) -> None`

```python
def record_step(
    self,
    step_index: int,
    cutoff_date: str,
    views_data: dict[str, PipelineStageData],
) -> None:
    """记录一步的管道输出。

    对 operating TF 视图:
      1. 提取 BS markers → 与上一步比较 → 写入 events.jsonl
      2. 提取 filtered tail (最后 10 点) → 写入 filter_tail.jsonl

    Parameters
    ----------
    step_index : int
        单调节递增步号 (0-based 或 1-based 均可)。
    cutoff_date : str
        该步对应的截止日期字符串。
    views_data : dict[str, PipelineStageData]
        键为 view_name (e.g. "v0_日线"), 值为该视图的管道数据。

    副作用
    ------
    追加行到 events.jsonl 和 filter_tail.jsonl。
    """
```

#### `_compare_bs_markers(current: dict, previous: dict) -> list[dict]`

BS 变动检测核心 (私有方法)。

```python
def _compare_bs_markers(
    self,
    current: dict | None,
    previous: dict | None,
    step_index: int,
) -> list[dict]:
    """比较两步的 BS 标记, 生成事件列表。

    BS 标记格式 (来自 compute_bs_markers() in bs_marker.py):
      {
        "entry_markers": [(bar_idx: int, label: str, color: str, date: Timestamp), ...],
        "exit_markers":  [(bar_idx: int, label: str, color: str,
                           exit_reason: str, date: Timestamp), ...]
      }

    比较键: (bar_idx, label, color) 三元组 (忽略 date 和 exit_reason)。

    返回事件列表:
      [
        {"event": "bs_added",  "step": step_index, "kind": "entry",
         "bar": int, "label": "B"|"S", "color": "green"|"red"},
        {"event": "bs_removed", "step": step_index, "kind": "entry",
         "bar": int, "label": "B"|"S", "color": "green"|"red"},
        ...
      ]
      若 step_index == 0，返回单个 {"event": "init", "step": 0,
      "entry_count": int, "exit_count": int}。

    Parameters
    ----------
    current : dict or None
        当前步的 BS 标记字典。
    previous : dict or None
        上一步的 BS 标记字典。为 None 时视为空。
    step_index : int
        当前步号。

    Returns
    -------
    list[dict]
        事件字典列表。
    """
```

#### `end_session() -> dict`

```python
def end_session(self) -> dict:
    """关闭会话, 更新 metadata.json 的 end_time, step_count, total_size_bytes。

    Returns
    -------
    dict
        {"session_id": str, "step_count": int, "total_size_bytes": int,
         "total_size_mb": float}
    """
```

### 2.4 JSONL 行格式定义

#### `events.jsonl` -- 每行一个事件

```jsonl
{"event":"init","step":0,"entry_count":3,"exit_count":2}
{"event":"bs_added","step":23,"kind":"entry","bar":118,"label":"B","color":"green"}
{"event":"bs_removed","step":23,"kind":"entry","bar":116,"label":"B","color":"green"}
{"event":"bs_added","step":47,"kind":"exit","bar":125,"label":"S","color":"green"}
```

#### `filter_tail.jsonl` -- 每行 filter 尾部 10 点

```jsonl
{"step":0,"cutoff":"2025-06-01","tf":"日线","view":"v0_日线","tail":[12.34,12.35,12.33,12.36,12.38,12.37,12.39,12.40,12.38,12.41]}
{"step":1,"cutoff":"2025-06-02","tf":"日线","view":"v0_日线","tail":[12.35,12.33,12.36,12.38,12.37,12.39,12.40,12.38,12.41,12.43]}
```

### 2.5 内部辅助函数 (模块级)

```python
def _write_jsonl(path: Path, data: dict) -> None:
    """追加一行 JSON 到文件。自动处理目录创建。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)
        f.write("\n")

def _write_json(path: Path, data: dict) -> None:
    """写入格式化的 JSON 文件 (覆盖模式)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
```

---

## 3. CLI (`filter_app/backtest_cli.py`)

### 3.1 模块职责

命令行入口。解析参数 → 加载配置 → 实例化 `BacktestRunner` + `EventRecorder` → 驱动回测循环。

### 3.2 argparse 参数完整定义

```python
import argparse

def build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="python -m filter_app.backtest_cli",
        description="回测数据采集 CLI — 批量运行管道计算并输出 BS 变动事件流",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ── 必需参数 ──
    parser.add_argument(
        "--ticker", required=True, type=str,
        help="股票代码, e.g. 03690.HK, AAPL"
    )
    parser.add_argument(
        "--market", required=True, type=str,
        choices=["美股 US", "A股(沪深)", "港股 HK"],
        help="市场标识"
    )

    # ── 配置来源 (二选一) ──
    config_group = parser.add_mutually_exclusive_group()
    config_group.add_argument(
        "--preset", type=str, default=None,
        help="预设方案名 (从 data/config.db 的 config_presets 表读取)"
    )
    config_group.add_argument(
        "--config-file", type=str, default=None,
        help="JSON 配置文件路径 (格式与 preset params_json 相同)"
    )

    # ── 范围控制 (二选一) ──
    range_group = parser.add_argument_group("范围控制 (bar 索引与日期二选一)")
    range_group.add_argument(
        "--start-bar", type=int, default=None,
        help="起始 bar 索引 (1-based)。与 --start-date 二选一"
    )
    range_group.add_argument(
        "--end-bar", type=int, default=None,
        help="结束 bar 索引 (1-based, 含)。默认到最后一个 bar"
    )
    range_group.add_argument(
        "--start-date", type=str, default=None,
        help="起始日期, YYYY-MM-DD 格式"
    )
    range_group.add_argument(
        "--end-date", type=str, default=None,
        help="结束日期, YYYY-MM-DD 格式"
    )

    # ── 可选参数 ──
    parser.add_argument(
        "--operating-tf", type=str, default=None,
        help="操作周期 (默认取自预设配置中的 operating_tf 或 v0_tf)"
    )
    parser.add_argument(
        "--output-dir", type=str, default="./backtest_output/",
        help="输出根目录 (默认 ./backtest_output/)"
    )
    parser.add_argument(
        "--log-level", type=str, default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别 (默认 INFO)"
    )
    parser.add_argument(
        "--step-range", type=str, default=None,
        help="仅运行指定步数范围, 格式: 'START,END' (1-based, 含)。用于分片并行"
    )

    return parser
```

### 3.3 main() 函数

```python
def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。

    流程:
      1. parse args
      2. 从 preset 或 config-file 加载配置 → 构建 configs list[dict]
      3. 确定 operating_tf, min_tf, lower_tfs
      4. 确定 bar 范围 (从日期或 bar 索引)
      5. 初始化 EventRecorder → start_session()
      6. 初始化 BacktestRunner(ticker, market, configs, operating_tf, min_tf, lower_tfs)
      7. runner.recorder = recorder  # 注入记录器
      8. for bar_index in range(start, end+1):
             runner.run_step(bar_index)  # 内部调用 recorder.record_step()
      9. recorder.end_session()
      10. 输出摘要: session_id, step_count, total_size_mb, output_path

    Returns
    -------
    int
        0 表示成功, 1 表示参数错误, 2 表示运行时错误。
    """
```

### 3.4 配置加载逻辑

```python
def load_configs(args: argparse.Namespace) -> tuple[list[dict], str, str]:
    """从 preset 或 config-file 加载视图配置。

    加载优先级:
      1. --preset: 从 config_db.apply_preset(preset_id) 获取 params dict,
         然后从 VIEW_PARAM_SPECS 构建每个视图的 cfg dict。
         预设查找: config_db.get_preset_by_name(args.preset)
      2. --config-file: 从 JSON 文件读取, 格式与 preset params_json 相同
      3. 两者都未提供: 从 config_db.list_presets() 加载第一个匹配 ticker 的预设,
         若无匹配则报错退出

    Returns
    -------
    tuple[list[dict], str, str]
        (configs, operating_tf, min_tf)
        configs: 4 个视图的 cfg dict 列表 [v0_cfg, v1_cfg, v2_cfg, v3_cfg]。
    """
```

**视图 cfg dict 构建** (与 `ViewState.build_cfg()` 和 `_render_params()` 逻辑一致):

```python
def _build_view_cfg(params: dict, vi: int) -> dict:
    """从 preset params 字典构建单个视图的 cfg dict。

    params 中视图键形如 "v0_tf", "v0_n", "v0_ke" 等。
    后缀到 cfg key 的映射参考 state.py ViewState._suffix_to_cfg_key()。
    补充 key:
      - "_fid": 从全局 filter_id 获取
      - "pv": 从滤波器参数中文 key 构建
      - "_dual": 从 global_dual 获取
      - "_fid2", "pv2": 双滤波参数
    """
```

### 3.5 Bar 范围解析

```python
def resolve_bar_range(
    args: argparse.Namespace,
    ticker: str,
    min_tf: str,
) -> tuple[int, int]:
    """将日期或 bar 索引参数解析为 (start_bar, end_bar) 元组。

    - --start-bar/--end-bar: 直接使用 (1-based → 保持不变)
    - --start-date/--end-date: 通过 DB 查询转换为 bar 索引
    - 默认: start_bar = min_n_pts, end_bar = total_bars

    Raises
    ------
    SystemExit
        当指定的日期在数据库中不存在时退出 (exit code 1)。
    """
```

### 3.6 退出码约定

| 码 | 含义 |
|----|------|
| 0 | 成功 |
| 1 | 参数错误 (ticker 不存在、preset 未找到、日期范围无效) |
| 2 | 运行时错误 (数据加载失败、DB 连接失败) |

---

## 4. `pipeline_capture.py` 修改清单

> 现有文件: `/Users/xfpan/claude/filter_research/filter_app/services/pipeline_capture.py` (293 行)

### 4.1 不变部分

- `PipelineStageData` 类 — 保持现有 `__slots__` 和字段不变，CLI 与 Streamlit 共用
- `_write_json()` — 保持
- `_write_parquet()` — 保持

### 4.2 修改点

#### M1: 新增 `mu_v`, `sigma_v` 字段到 `PipelineStageData`

**文件**: `pipeline_capture.py`
**行号**: `__slots__` 定义附近 (line 34-41)
**修改**: 在 `__slots__` 中新增 `"mu_v"` 和 `"sigma_v"` 字段，在 `__init__` 中添加对应参数 (默认 `None`)。

```python
# 修改前 __slots__:
    "sig", "v", "a", "eps",

# 修改后 __slots__:
    "sig", "v", "a", "eps", "mu_v", "sigma_v",
```

**原因**: `_schmitt_trigger()` 返回字典包含 `mu_v` 和 `sigma_v` (filter_engine.py line 516)，它们是自适应死区 eps 的基础变量，是 BS 敏感性分析的根因参数。当前 `PipelineCapture.capture_step()` 收集 `v`, `a`, `eps` 但遗漏了 `mu_v`, `sigma_v`。

#### M2: `streamlit_app.py` 中捕获代码同步收集 `mu_v`, `sigma_v`

**文件**: `streamlit_app.py`
**行号**: line 864-881 (PipelineStageData 构造处)
**修改**: 在 `PipelineStageData(...)` 构造参数中添加 `mu_v=schmitt.get("mu_v")` 和 `sigma_v=schmitt.get("sigma_v")`。

```python
# 在已有的 PipelineStageData(...) 构造中添加两行:
    mu_v=schmitt.get("mu_v") if schmitt is not None else None,
    sigma_v=schmitt.get("sigma_v") if schmitt is not None else None,
```

#### M3: `_write_view()` 在 `stage_03_schmitt.parquet` 中增加 `mu_v`, `sigma_v` 列

**文件**: `pipeline_capture.py`
**行号**: line 244-247 (`_write_view` 中 S03 写入处)
**修改**: 在 Stage 03 的 parquet 写入中添加 mu_v, sigma_v 列。

```python
# 修改前:
    _write_parquet(view_dir / "stage_03_schmitt.parquet", {
        "t": sd.t, "sig": sd.sig, "v": sd.v, "a": sd.a, "eps": sd.eps,
    })

# 修改后:
    _write_parquet(view_dir / "stage_03_schmitt.parquet", {
        "t": sd.t, "sig": sd.sig, "v": sd.v, "a": sd.a,
        "eps": sd.eps, "mu_v": sd.mu_v, "sigma_v": sd.sigma_v,
    })
```

---

## 5. 新增文件清单

| 文件 | 路径 | 预估行数 | 说明 |
|------|------|---------|------|
| `backtest_core.py` | `filter_app/backtest_core.py` | ~350 行 | BacktestRunner 类 + BacktestError 异常类 |
| `event_recorder.py` | `filter_app/services/event_recorder.py` | ~200 行 | EventRecorder 类 + JSONL 写入辅助函数 |
| `backtest_cli.py` | `filter_app/backtest_cli.py` | ~180 行 | argparse + main() + 配置加载 + bar 范围解析 |

**总计**: ~730 行新代码 + ~30 行既有文件修改。

---

## 6. 端到端验证计划 (Phase 1 验收标准)

### 6.1 冒烟测试

```bash
# 使用 3690_HK_2 预设, 运行 5 步
python -m filter_app.backtest_cli \
    --ticker 03690.HK \
    --market "港股 HK" \
    --preset 3690_HK_2 \
    --start-bar 100 --end-bar 105 \
    --output-dir ./backtest_output/ \
    --log-level DEBUG
```

**预期**:
- 退出码 0
- `backtest_output/03690.HK_*/` 目录存在
- `metadata.json`, `data_manifest.json`, `events.jsonl`, `filter_tail.jsonl` 均非空
- `events.jsonl` 第 1 行为 `{"event":"init",...}`
- `filter_tail.jsonl` 每行含 `"tail"` 数组长度 = 10

### 6.2 回归验证

```bash
# 运行完整回测, 检查是否能复现 645x 级联放大案例
python -m filter_app.backtest_cli \
    --ticker 03690.HK \
    --market "港股 HK" \
    --preset 3690_HK_2 \
    --start-bar 0 --end-bar 200 \
    --output-dir ./backtest_output/
```

**预期**:
- `events.jsonl` 中存在非 `init` 事件 (bs_added/bs_removed)
- `grep bs_added events.jsonl | wc -l` >= 1 (验证 BS 变动被捕获)
- 能通过 filter_tail.jsonl 追溯变动步骤的 filter 尾部变化

### 6.3 Streamlit 兼容性

```bash
# 启动 Streamlit 前设置 PIPELINE_CAPTURE=1
PIPELINE_CAPTURE=1 streamlit run filter_app/streamlit_app.py
```

**预期**:
- 浏览模式正常运行 (PipelineCapture 不激活)
- 回测模式正常运行 (PipelineCapture 写入 data/pipeline_captures/)
- `PipelineStageData` 新增的 `mu_v`/`sigma_v` 字段不影响现有 parquet 写入

---

## 7. 依赖关系图

```
backtest_cli.py
  ├── backtest_core.py (BacktestRunner)
  │     ├── services/data_loader.py (_sync_all_cascading)
  │     ├── services/filter_engine.py (FILTERS, _schmitt_trigger,
  │     │       _find_all_pairs, _fit_physics_parabola, _compute_strategy_pnl,
  │     │       _align_pnl_to_current_tf, _compute_holding_masks)
  │     ├── services/bs_marker.py (compute_bs_markers, get_lower_tfs)
  │     ├── services/pipeline_capture.py (PipelineStageData)
  │     ├── config_db.py (apply_preset, get_preset_by_name, list_presets)
  │     └── db.py (get_conn)
  ├── services/event_recorder.py (EventRecorder)
  │     └── (无外部依赖, 仅 json + pathlib)
  └── (标准库: argparse, logging, sys, json, pathlib)
```

---

## 8. 从现有代码中需复制/调整的片段

以下代码段当前位于 `streamlit_app.py`，需在 `backtest_core.py` 中重新实现 (不修改原文件):

### 8.1 内联 parquet 读取 (替代 `_load_chart_data()`)

```python
def _read_parquet_window(tf: str) -> tuple[np.ndarray, ...]:
    """从 data/display/{tf}.parquet 读取窗口数据。

    返回 (t, noisy, ohlc, dates) 或 (None,None,None,None) 表示失败。
    """
    display_path = Path(__file__).parent.parent / "data" / "display" / f"{tf}.parquet"
    if not display_path.exists():
        return None, None, None, None
    df = pd.read_parquet(display_path)
    if "Date" not in df.columns or "Close" not in df.columns or len(df) < 2:
        return None, None, None, None
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date").sort_index()
    t = np.arange(len(df), dtype=float)
    noisy = df["Close"].values.ravel()
    ohlc = df[["Open", "High", "Low", "Close"]]
    return t, noisy, ohlc, df.index
```

### 8.2 内联 filter 计算 (替代 `_compute_filters()`)

```python
# 直接使用 FILTERS 注册表, 不加 @st.cache_data
sf = FILTERS.get(cfg["_fid"])
filtered = sf["func"](noisy, t, **cfg["pv"]) if sf else np.full_like(noisy, np.nan)
filtered = np.asarray(filtered, dtype=float).ravel()

filtered2 = None
if cfg.get("_dual") and cfg.get("_fid2") and cfg.get("pv2"):
    sf2 = FILTERS.get(cfg["_fid2"])
    if sf2:
        filtered2 = sf2["func"](noisy, t, **cfg["pv2"])
        filtered2 = np.asarray(filtered2, dtype=float).ravel()
```

### 8.3 `_get_bar_date_from_db()` -- 移至 `db.py`

```python
# 建议在 db.py 中新增:
def get_bar_date(ticker: str, tf: str, bar_index: int) -> str:
    """查询指定 bar 索引对应的日期字符串。

    Parameters
    ----------
    ticker : str
    tf : str
    bar_index : int  (0-based)

    Returns
    -------
    str  日期字符串, 无数据时返回 ""。
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT ts FROM kline WHERE ticker=? AND timeframe=?"
            " ORDER BY ts ASC LIMIT 1 OFFSET ?",
            (ticker, tf, bar_index),
        ).fetchone()
    return row[0] if row else ""
```
