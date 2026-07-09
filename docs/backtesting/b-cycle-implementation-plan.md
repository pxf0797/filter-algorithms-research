# B周期半边多空对策略 -- 完整集成方案

> 状态: MVP v1 设计稿 | 目标: 将 v3 研究设计落实到 Streamlit 多周期滤波应用

---

## Part 0: 现状审计 -- 存在什么，缺什么

### 已存在（可直接复用）

| 组件 | 位置 | 说明 |
|------|------|------|
| `TF_HIERARCHY` | `components/sidebar.py:100-104` | 紧邻高周期映射表, D→C→B→A 相邻关系 |
| `_compute_strategy_display` | `streamlit_app.py:L279-313` | 当前策略PnL入口, 写入 `st.session_state[f"_pnl_{tf}"]` |
| `_compute_strategy_pnl` | `filter_engine.py:L649-860` | 基础策略PnL(无strategy_mode参数) |
| `_schmitt_trigger` | `filter_engine.py` | 施密特触发器 |
| `_find_all_pairs` | `filter_engine.py:L520-576` | 多空对识别(含同号合并) |
| `_fit_physics_parabola` | `filter_engine.py:L608-646` | 抛物线拟合 |
| `_align_pnl_to_current_tf` | `filter_engine.py:L867-972` | 高周期PnL时间戳对齐 |
| `_add_pnl_traces` | `components/charts.py` | PnL曲线渲染 |
| `backtest_logger.py` | 75行, 4个函数 | JSONL事件日志基础 |

### 不存在（本次需创建）

以下函数在任务描述中被提及但代码中不存在, 需全部从零实现:

- `_find_current_half_pair(sig_t, merged, edge_width)` -- 识别窗口最右侧的半边多空对
- `_run_half_pair_strategy(t, filtered, sig_t, half_pair, higher_dir, pred_pairs, params, n_extend)` -- 半边策略核心
- `_merge_segments(sig_t)` -- 公共合并函数(部分逻辑已在 `_find_all_pairs` 中)
- `_get_higher_tf_direction(higher_sig)` -- C周期方向提取
- `_c_pair_state(...)` -- C周期5态判断
- `_signed_deviation(...)` -- 偏离量计算(离场②/破位预警复用)

以及:
- `_compute_strategy_pnl` 缺少 `strategy_mode` 和 `trade_signals` 参数
- `_compute_strategy_display` 中没有半边策略条件分支
- `backtest_logger.py` 缺少半边策略日志函数

---

## Part 1: 数据流设计

### 1.1 核心约束: 渲染顺序

```python
# streamlit_app.py L1842-1843
sorted_views = sorted(enumerate(configs),
    key=lambda x: ALL_TFS.index(x[1]["tf"]), reverse=True)
```

`reverse=True` 意味着按 ALL_TFS 索引降序排列, 即粗周期先渲染: **D -> C -> B -> A**。

`ALL_TFS = ["1分钟","5分钟","15分钟","60分钟","日线","周线","月线","季线"]` (索引 0-7), 排序后索引大的先执行。

### 1.2 数据传递机制

当前系统中跨周期数据传递的唯一通道是 `st.session_state`:

```
C周期渲染(_render_chart)
  │
  ├── _compute_strategy_display(...)
  │      └── st.session_state[f"_pnl_{tf}"] = {       # L308-312
  │            "dates": dates, "t": t,
  │            "long_pnl": long_pnl,
  │            "short_pnl": short_pnl,
  │            "trade_records": trade_records,
  │          }
  │
  ▼  (session_state已写入)
B周期渲染(_render_chart)
  │
  ├── _higher_tf = TF_HIERARCHY.get(tf)                # L623
  ├── _raw_higher = st.session_state.get(f"_pnl_{_higher_tf}")  # L626
  │
  └── 使用 _raw_higher 数据对齐到B时间轴
```

### 1.3 C周期需要新增暴露的数据

当前 C 只暴露 `dates / t / long_pnl / short_pnl / trade_records`, 对半边策略不够。需补充:

| 新增字段 | 类型 | 用途 |
|---------|------|------|
| `sig` | np.ndarray | B需要C的原始schmitt信号判断方向 |
| `filtered` | np.ndarray | C的滤波价格(用于偏离计算) |
| `half_pair` | dict | C当前半边多空对信息 {start_idx, direction, state} |
| `c_state` | str | C的5态枚举值(STRONG_ALIGN/WEAK_ALIGN/C_ENDED/MISALIGN/NO_DIR) |

**实施方案:**

扩展 `_compute_strategy_display` 中 `st.session_state[f"_pnl_{tf}"]` 的写入:

```python
st.session_state[f"_pnl_{tf}"] = {
    "dates": dates, "t": t,
    "long_pnl": long_pnl, "short_pnl": short_pnl,
    "trade_records": trade_records,
    # --- 新增: 半边策略需要 ---
    "sig": schmitt["sig"],
    "filtered": filtered,
    "half_pair": c_half_pair,       # = _find_current_half_pair(...)
    "c_state": c_state,             # = _c_pair_state(...)  [C/D周期用]
}
```

**决策理由:** 不新增 session_state key, 扩展现有 `_pnl_{tf}` dict。好处: (1) B 现有的 `_raw_higher` 获取逻辑不变; (2) 一个 key 包含该周期的全部信息, 干净; (3) 新增字段向后兼容, 不破坏现有代码。

### 1.4 B如何找到C

B通过两步找到C周期数据:

```
Step 1: TF_HIERARCHY 找紧邻上级周期名
  _higher_tf = TF_HIERARCHY.get(B_tf)
  例: B="60分钟" → _higher_tf="日线"

Step 2: session_state 取C数据
  c_data = st.session_state.get(f"_pnl_{_higher_tf}")
```

**潜在问题: 4视图配置不按顺序**

用户可能配置 `[日线, 15分钟, 周线, 5分钟]`。排序后: `5分钟(A), 15分钟(B), 日线(C), 周线(D)`。

B=15分钟, TF_HIERARCHY 给出紧邻上级=60分钟。但用户没有配置60分钟视图! C=日线是实际的最近上级。

**解决方案: 动态查找C**

```python
def _find_higher_tf_in_views(tf: str, configs: list[dict]) -> str | None:
    """在已配置视图中, 找到比 tf 粗糙的最近周期。"""
    tfs_in_views = sorted(
        [c["tf"] for c in configs],
        key=lambda x: ALL_TFS.index(x)
    )
    current_idx = ALL_TFS.index(tf)
    for t in tfs_in_views:
        if ALL_TFS.index(t) > current_idx:
            return t
    return None
```

在 `_render_chart_fragment` 调用时(或 `_render_chart` 内), 用此函数替代 `TF_HIERARCHY.get(tf)` 的静态查找。

**MVP简化:** 先不改 `_render_chart` 主流程, 在 `_compute_strategy_display` 内部用上述函数查找C。这是最小侵入方式。

### 1.5 数据流总图

```
渲染顺序: D → C → B → A
                  │
configs = [v0, v1, v2, v3]  ──sorted by ALL_TFS idx reverse──▶
                  │
     D(周线)渲染       C(日线)渲染       B(60min)渲染      A(15min)渲染
     _render_chart    _render_chart     _render_chart     _render_chart
         │                 │                 │
         │         _compute_strategy_display │
         │               │                   │
         │   st.session_state["_pnl_日线"] = {  │
         │        sig, filtered, ←─── 新增 ── │
         │        half_pair, c_state,         │
         │        long_pnl, short_pnl,        │
         │        trade_records               │
         │   }                                │
         │               │                    │
         │               └────────────────────┼── B 读取
         │                                    │
         │   B._compute_strategy_display()    │
         │      c_data = st.session_state     │
         │              .get("_pnl_日线")      │
         │      c_sig = c_data["sig"]          │
         │      c_half_pair = c_data["half_pair"]
         │      c_state = c_data["c_state"]    │
         │                                     │
         │      调用 _run_half_pair_strategy()  │
         │      调用 _compute_strategy_pnl(    │
         │              strategy_mode='half_pair',│
         │              trade_signals=...)     │
```

---

## Part 2: B周期执行流程

### 2.1 每帧完整链路

```
B周期视图渲染(_render_chart)
  │
  ├── Step 1-5: 数据加载/滤波/Schmitt/配对/拟合
  │     (现有逻辑, 不改)
  │     产出: t, filtered, schmitt, all_pairs, pred_pairs
  │
  ├── Step 6: 识别B当前半边多空对 [新增]
  │     half_pair_B = _find_current_half_pair(
  │         sig_t=schmitt["sig"],
  │         merged=_merge_segments(schmitt["sig"]),
  │         edge_width=cfg.get("edge_width", 5)
  │     )
  │     # 返回: {start_idx, direction, is_half}
  │     # direction: +1(多) / -1(空) / 0(无活跃半边)
  │
  ├── Step 7: 获取C数据 [新增]
  │     c_tf = _find_higher_tf_in_views(tf, configs)
  │     c_data = st.session_state.get(f"_pnl_{c_tf}")
  │
  ├── Step 8 (_compute_strategy_display内): [大幅改造]
  │     │
  │     ├── show_strategy AND 半边策略开关=ON AND tf==B AND c_data存在?
  │     │     │  [YES]
  │     │     ├── 8a. 提取C状态
  │     │     │     c_sig = c_data["sig"]
  │     │     │     c_half_pair = c_data["half_pair"]
  │     │     │     c_state = c_data["c_state"]  # C已算好5态
  │     │     │
  │     │     ├── 8b. 入场门控 (Gate)
  │     │     │     IF half_pair_B["direction"] != 0        ← B有活跃半边
  │     │     │        AND half_pair_B 是起点当帧            ← 不在中间
  │     │     │        AND c_state == "STRONG_ALIGN":        ← C强同向
  │     │     │           gate = "ENTRY"
  │     │     │     ELIF c_state in ("C_ENDED","MISALIGN","NO_DIRECTION"):
  │     │     │           gate = "SKIP"   ← MVP不细化
  │     │     │     ELSE:
  │     │     │           gate = "SKIP"
  │     │     │
  │     │     ├── 8c. 离场判定 (已有持仓前提下)
  │     │     │     优先级链:
  │     │     │       优1: c_state=="MISALIGN" → 强制离场
  │     │     │       优2: c_state=="C_ENDED"  → [MVP暂不实现,留v2]
  │     │     │       优3: B entry_void        → 小损离场
  │     │     │       优4: B 反向段确认        → 正常离场
  │     │     │       优5: 偏离过大           → 止损离场
  │     │     │
  │     │     ├── 8d. 调用 _run_half_pair_strategy()
  │     │     │     → 返回 trade_signals: [(entry_bar, exit_bar, direction, reason), ...]
  │     │     │
  │     │     ├── 8e. 调用 _compute_strategy_pnl(
  │     │     │         strategy_mode='half_pair',
  │     │     │         trade_signals=trade_signals
  │     │     │     )
  │     │     │     当 strategy_mode='half_pair' 时:
  │     │     │       - 跳过 _find_all_pairs 入场逻辑
  │     │     │       - 直接使用传入的 trade_signals 构建PnL曲线
  │     │     │       - 离场触发沿用现有止损/止盈机制
  │     │     │
  │     │     └── 8f. 记录本帧状态
  │     │           _push_half_pair_log(...)
  │     │
  │     └── [NO] 走原有 _compute_strategy_pnl 路径
  │
  ├── Step 9-11: 图表构建
  │     _add_pnl_traces() 已支持 strategy_label
  │     复用 _add_cross_pnl_subplot 显示C的PnL参考
  │
  └── Step 11: 状态记录
        st.session_state[f"_pnl_{tf}"] 写入(含sig/filtered/half_pair)
```

### 2.2 关键函数的伪代码

**`_find_current_half_pair(sig_t, edge_width)`** -- 新增于 `filter_engine.py`

```python
def _find_current_half_pair(
    sig_t: np.ndarray, edge_width: int = 5
) -> dict | None:
    """
    识别窗口最右侧的半边多空对(有起点无终点)。

    利用 _merge_segments 合并同号段后,
    取最后一个非零段作为当前半边。
    """
    segments = _merge_segments(sig_t)
    if not segments:
        return None

    last = segments[-1]
    if last["direction"] == 0:  # 最右段是观望
        return None

    n = len(sig_t)
    is_half = (n - 1 - last["end_idx"]) < edge_width
    # 若终点的bar距离窗口右边缘≤edge_width, 则是半边

    return {
        "start_idx": last["start_idx"],
        "end_idx": last["end_idx"],
        "direction": last["direction"],  # +1 or -1
        "is_half": is_half,
    }
```

**`_merge_segments(sig_t)`** -- 新增于 `filter_engine.py`

```python
def _merge_segments(sig_t: np.ndarray) -> list[dict]:
    """合并相邻同号段(含中间0), 返回合并后的段列表。"""
    # 复用 _find_all_pairs 中的 Step 1-2 逻辑
    # 返回: [{start_idx, end_idx, direction}, ...]
```

**`_c_pair_state(higher_sig, higher_half_pair, dir_B)`** -- MVP简化版, 新增于 `filter_engine.py`

```python
def _c_pair_state(
    higher_sig: np.ndarray,
    higher_half_pair: dict | None,
    dir_B: int,
) -> str:
    """
    C周期5态判定 -- MVP简化版。

    MVP实现:
      STRONG_ALIGN  = C方向与B同向
      MISALIGN      = C方向与B反向
      NO_DIRECTION  = C无活跃半边
      WEAK_ALIGN    = MVP暂不区分(留后续)
      C_ENDED       = MVP暂不实现(留后续)

    后续迭代:
      - 引入 signed_deviation 计算 → 区分 WEAK/C_ENDED
      - 引入 WARN_DEV_PCT_C / MAX_DEV_PCT_C 阈值
    """
    if higher_half_pair is None:
        return "NO_DIRECTION"

    c_dir = higher_half_pair["direction"]
    if c_dir == dir_B:
        return "STRONG_ALIGN"
    elif c_dir == -dir_B:
        return "MISALIGN"
    else:
        return "NO_DIRECTION"  # c_dir==0 或未预期
```

### 2.3 `_compute_strategy_pnl` 的改造

**现状:** 函数签名 `(t, filtered, sig_t, all_pairs, pred_pairs, stop_loss_pct, n_extend)` -- 无strategy_mode。

**改造后:**

```python
def _compute_strategy_pnl(
    t, filtered, sig_t, all_pairs, pred_pairs,
    stop_loss_pct, n_extend=10,
    strategy_mode: str | None = None,       # 新增
    trade_signals: list[dict] | None = None, # 新增
) -> tuple[np.ndarray, np.ndarray, list[dict]]:
```

当 `strategy_mode == "half_pair"` 且 `trade_signals` 不为空时:
- 跳过原有的 `for pair_start, pair_end in all_pairs` 遍历
- 直接用 `trade_signals` 中的 entry_idx/exit_idx 构建PnL曲线和trade_records
- 离场逻辑(止损/止盈/偏离)在 `_run_half_pair_strategy` 中已处理

当 `strategy_mode is None` 时: 保持现有逻辑不变(零回退)。

**决策理由:** 传入 trade_signals 而非在 PnL 函数内重算, 因为半边策略的入场/离场规则与基础策略完全不同(起点当帧、C门控、梯度离场), 不适合在同一个 for 循环中混用。分离后才清晰。

---

## Part 3: 状态记录方案

### 3.1 方案对比

| 维度 | 方案A: session_state 列表 | 方案B: backtest_logger JSONL |
|------|--------------------------|------------------------------|
| 持久化 | 否(页面刷新即丢失) | 是(磁盘文件) |
| 查询 | Python内存操作 | 需解析JSONL |
| 实时回测 | 方便: 内存追加+渲染 | 需flush+reload |
| 与现有架构一致 | 否(backtest_logger已存在) | 是 |
| 实现复杂度 | 低 | 中 |

### 3.2 推荐: 方案B(扩展backtest_logger)

**理由:**

1. **持久化是硬需求。** 半边的意义在于回测验证--比较"无门控"vs"有门控"的绩效差异。session_state 在页面重跑时丢失, JSONL 文件可保留整个回测会话。

2. **与回测模式一致。** 系统已有回测bar导航, 每次推进日志追加一行。半边的帧记录应与回测bar记录在同一个JSONL中, 便于按时间线追溯。

3. **轻量。** 每帧一行JSON, 250帧回测 = 250行, 不影响性能。

### 3.3 新增日志函数

在 `backtest_logger.py` 中新增:

```python
def log_half_pair_frame(
    ticker: str, tf: str, bar_index: int,
    b_half_state: dict | None,
    c_state: str,
    gate_decision: str,       # "ENTRY" | "SKIP" | "EXIT" | "HOLD"
    gate_reason: str,
    trade_action: str | None, # "LONG_ENTRY" | "SHORT_ENTRY" | "EXIT" | None
    elapsed_ms: float = 0,
):
    """记录每帧半边策略状态。"""
    _log_event("half_pair_frame", {
        "ticker": ticker, "tf": tf,
        "bar_index": bar_index,
        "b_direction": b_half_state.get("direction") if b_half_state else 0,
        "b_is_half": b_half_state.get("is_half") if b_half_state else False,
        "c_state": c_state,
        "gate_decision": gate_decision,
        "gate_reason": gate_reason,
        "trade_action": trade_action,
        "elapsed_ms": round(elapsed_ms, 1),
    })
```

### 3.4 日志数据流

```
B._compute_strategy_display() 每帧:
  │
  ├── half_pair_B = _find_current_half_pair(...)
  ├── c_state = _c_pair_state(...)
  ├── gate = 入场门控判定
  │
  └── log_half_pair_frame(
          ticker, tf, bar_index=当前帧索引,
          b_half_state=half_pair_B,
          c_state=c_state,
          gate_decision=gate,
          gate_reason=判定理由,
          trade_action=交易动作,
      )
```

---

## Part 4: 可视化方案

### 4.1 区分"原始信号"vs"策略信号"

**方案: 以 Caption + Annotation 为主, 颜色为辅助**

| 元素 | 原始策略 | 半边策略 |
|------|---------|---------|
| Caption文本 | "多: xx%" | "半多#1: xx%" |
| PnL曲线颜色 | long=蓝, short=红 | long=深蓝(#1565c0), short=深红(#c62828) |
| 入场标记 | "多#1" | "半多#1" |
| 离场标记 | "止盈/止损" | "止盈/止损/C_RVRS" |

### 4.2 实施方式

复用 `_add_pnl_traces` (components/charts.py), 不新增渲染函数。策略标签通过 `strategy_label` 参数传入:

```python
# 在 _compute_strategy_display 中
strategy_label = "半边多空对" if strategy_mode == "half_pair" else None

# 传递给 _add_pnl_traces
_add_pnl_traces(fig, t, long_pnl, short_pnl, trade_records, pnl_row,
                strategy_label="半边多空对")
```

图表 caption 区（L294-307的c4/c5/c6列）在策略 mode 下自动显示"半多% / 半空% / 半DD", 与原始策略视觉区分。

### 4.3 后续迭代: 双线对比

当用户同时开启普通策略和半边策略的AB对比时, 可以在PnL子图中画两条曲线。但这涉及子图布局重置和颜色区分, 复杂度高, 放到v2。

---

## Part 5: C周期5态的简化实现

### 5.1 MVP: 3态模型

v3设计有5态(STRONG/WEAK/C_ENDED/MISALIGN/NO_DIR), MVP先实现3态:

| 状态 | 判定 | B入场 | B持仓响应 |
|------|------|-------|----------|
| STRONG_ALIGN | C方向==B方向 | 可入场 | 持有 |
| MISALIGN | C方向==-B方向 | 不入场 | 强制离场 |
| NO_DIRECTION | C无活跃半边 | 不入场 | 观察 |

**实现:** `_c_pair_state()` 只返回三种值。WEAK_ALIGN 和 C_ENDED 留到后续迭代(需要引入 `_signed_deviation` 和双阈值)。

### 5.2 后续迭代: 完整5态

```
STRONG_ALIGN → [引入 signed_deviation] → WEAK_ALIGN (预警带, 未超阈)
                                        → C_ENDED    (已超阈, C已离场)
MISALIGN      → 保持不变
NO_DIRECTION  → 保持不变
```

5态需要:
1. `_signed_deviation(price, fit_values, direction)` -- 计算不利方向偏离百分比
2. `WARN_DEV_PCT_C` 和 `MAX_DEV_PCT_C` 两个新阈值
3. `_c_pair_state` 中新增预警带分支

---

## Part 6: 改动文件清单

| 文件 | 改动 | 优先级 | 预估行数 |
|------|------|--------|---------|
| `filter_engine.py` | 新增6个函数 + 改造 _compute_strategy_pnl | P0 | ~200行 |
| `streamlit_app.py` | 改造 _compute_strategy_display + _render_chart 中C数据暴露 | P0 | ~80行 |
| `backtest_logger.py` | 新增 log_half_pair_frame | P1 | ~20行 |
| `components/charts.py` | 可能无需改动(复用 _add_pnl_traces) | P2 | 0 |

### 6.1 filter_engine.py 新增函数清单

```
1. _merge_segments(sig_t)                  -- ~30行, 从 _find_all_pairs 抽取合并逻辑
2. _find_current_half_pair(sig_t, edge_width) -- ~25行
3. _c_pair_state(higher_sig, higher_half_pair, dir_B) -- ~25行 (MVP 3态)
4. _get_higher_tf_direction(higher_sig)     -- ~15行
5. _run_half_pair_strategy(...)            -- ~80行 (入场门控 + 离场优先级链)
6. _signed_deviation(price, pred, dir)     -- ~20行 (MVP计算但暂不用, 为v2预留)

改造:
7. _compute_strategy_pnl(...)              -- ~30行改动 (添加 strategy_mode 分支)
```

### 6.2 streamlit_app.py 改动

```
1. _compute_strategy_display()              -- 添加半边策略条件路径 (~60行)
   - 新增参数: higher_data, configs, is_b_cycle
   - 内部: 门控判定 → _run_half_pair_strategy → _compute_strategy_pnl(half_pair mode)

2. _render_chart()                          -- 扩展 _pnl_{tf} 写入 (~15行)
   - L308-312: 添加 sig/filtered/half_pair 到 session_state

3. _find_higher_tf_in_views()              -- 新增辅助函数 (~10行)
```

---

## Part 7: 实施路线图

### Phase 1: 基础函数 (P0, 预计1天)

```
1. 实现 _merge_segments          [filter_engine.py]
2. 实现 _find_current_half_pair  [filter_engine.py]
3. 实现 _get_higher_tf_direction [filter_engine.py]
4. 实现 _c_pair_state (MVP 3态)  [filter_engine.py]
5. 实现 _run_half_pair_strategy  [filter_engine.py]
   验证: 单元测试 - 给定sig_t, 验证half_pair识别正确
```

### Phase 2: 引擎集成 (P0, 预计1天)

```
6. 改造 _compute_strategy_pnl [filter_engine.py]
   - 添加 strategy_mode / trade_signals 参数
   - 半边模式跳过all_pairs遍历
   验证: 调用 _compute_strategy_pnl(strategy_mode='half_pair', trade_signals=...)
         对比输出与预期PnL曲线

7. 改造 _compute_strategy_display [streamlit_app.py]
   - 添加半边策略条件路径
   - 门控判定集成
   验证: 手动测试 - B视图开启半边策略, 检查图表渲染和Caption文本
```

### Phase 3: 数据流 (P0, 预计0.5天)

```
8. 扩展 _pnl_{tf} session_state 写入 [streamlit_app.py _render_chart]
   - 添加 sig/filtered/half_pair 字段
9. 实现 _find_higher_tf_in_views [streamlit_app.py]
   验证: 在4视图任意配置下, B能获取正确的C周期数据
```

### Phase 4: 日志和可视化 (P1, 预计0.5天)

```
10. 新增 log_half_pair_frame [backtest_logger.py]
11. 在 _compute_strategy_display 中集成日志调用
12. 验证 PnL 曲线颜色和 Caption 文本区分
```

### Phase 5: 后续迭代 (P2)

```
- _c_pair_state 升级到完整5态
- _signed_deviation 阈值调优
- 双线AB对比可视化
- WEAK_ALIGN 降级入场选项
- C_ENDED 梯度离场
```

---

## Part 8: 关键设计决策汇总

| # | 决策 | 方案 | 理由 |
|---|------|------|------|
| 1 | C数据传递 | 扩展 `_pnl_{tf}` dict | 不改现有channel, 向后兼容 |
| 2 | 查找C周期 | `_find_higher_tf_in_views` 动态查找 | TF_HIERARCHY 只给紧邻上级, 用户可能没配该周期 |
| 3 | 策略PnL分离 | strategy_mode 分支, 传入 trade_signals | 半边入场规则与基础策略完全不同, 不适合混用 |
| 4 | 状态记录 | 扩展 backtest_logger JSONL | 持久化是回测对比的硬需求 |
| 5 | C态MVP | 仅3态(STRONG/MISALIGN/NO_DIR) | 5态需要 signed_deviation 基础设施, 先跑通主流程 |
| 6 | 可视化 | Caption文本 + PnL颜色区分 | 不新增图表组件, 复用现有渲染管道 |
| 7 | 门控位置 | 插在 _compute_strategy_display 内 | 不改变上游滤波链路, 改动最小 |
| 8 | B与C关系 | 通过 session_state 在渲染顺序中自然处理 | C总是在B之前渲染, 数据保证就绪 |
