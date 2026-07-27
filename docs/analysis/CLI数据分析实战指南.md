# filter_research CLI 数据分析实战指南

> **版本**: v1.0 | **日期**: 2026-07-27
> **前置条件**: Python 3.12+, pandas, numpy, scipy, pyarrow 已安装
> **工作目录**: `/Users/xfpan/claude/filter_research`
> **核心理念**: 每段代码都可直接在终端运行,不依赖 Streamlit UI

---

## 摘要

本指南教你如何**从拿到回测 Parquet 文件开始**,通过 Python 脚本对 filter_research 的 CLI 输出数据进行系统化分析。核心要点: (1) 51 列 Parquet 中每 12 列对应一个视图,不同回测 session 的 view_labels 映射可能不同——先读 metadata.json 再分析;(2) 6 大分析陷阱(前视偏差、幸存者偏差、过拟合、多重检验、数据窥探、伪相关性)在工具层有保护但用户仍需警惕;(3) 端到端分析流程从数据加载到稳健性检验共 6 步,每步都有 Go/No-Go 标准;(4) 只看 long/short PnL 各自指标,**不使用 combined_pnl = max(long, short)**——该值不可实盘实现。

---

## 第一章: 拿到数据后第一步(数据理解)

### 1.1 Parquet 51 列 Schema 速览

回测输出文件 `backtest_result.parquet` 固定 51 列,结构如下:

```
全局列(3):
  bar_index          int32         滑动窗口 bar 序号
  bar_timestamp      datetime64    当前 bar 时间戳
  close              float32       收盘价

视图列(每视图 12 列 x 4 视图 = 48):
  v{i}_sig           int8          Schmitt 信号: -1(空)/0(观望)/+1(多)
  v{i}_filtered      float32       滤波后的价格序列
  v{i}_eps           float32       自适应死区阈值(当前 bar)
  v{i}_pnl_long      float32       多头 PnL(以 100 为基数)
  v{i}_pnl_short     float32       空头 PnL(以 100 为基数)
  v{i}_long_pos      bool          是否有多头持仓
  v{i}_short_pos     bool          是否有空头持仓
  v{i}_trade         object        交易事件(None/'entry_long'/'exit_long'/'entry_short'/'exit_short')
  v{i}_trade_return  float32       单笔交易收益率(退出 bar 才有值,多 bar 退出时重复)
  v{i}_trade_reason  object        退出原因('take_profit'/'stop_loss'/'signal_reverse')
  v{i}_bs_entry      object        入场标记('B'/'S',用于图表标注)
  v{i}_bs_exit       object        出场标记('B'/'S',用于图表标注)
```

**关键理解**:
- `pnl_long/short` 以 100 为基数: 值 105 表示 5% 盈利,值 92 表示 8% 亏损
- `trade_return` 在退出 bar 上**多行重复**(整个退出窗口),去重方法见 5.4 节
- `combined_pnl` **不在 Parquet 中**——它由 `pnl_renderer.py` 在仪表盘渲染时计算为 `max(long_pnl, short_pnl)`,实盘不可实现

### 1.2 metadata.json 的关键字段

每次回测 session 目录中都有一份 `metadata.json`,必须在分析前先读取:

```python
import json

meta = json.load(open("backtest_output/3690_20260726-193857-3690/metadata.json"))

# === 必备字段 ===
meta["ticker"]          # "3690"
meta["session_id"]      # "20260726-193857-3690"
meta["view_labels"]     # {"v0": "周线", "v1": "日线", "v2": "60分钟", "v3": "15分钟"}
meta["status"]          # "completed"
meta["parquet_row_count"] # 30

# === 可选但有价值的字段 ===
meta["config"]["start_bar"]    # 回测起始 bar
meta["config"]["end_bar"]      # 回测结束 bar
meta["config"]["configs"]      # 4 视图完整参数(滤波器类型/参数/k_eps/止损等)
meta["end_time"]               # 回测完成时间

# === 快速提取每个视图的关键参数 ===
for vi, cfg in meta.get("view_configs", {}).items():
    tf = meta["view_labels"][vi]
    fid = cfg["_fid"]
    ke = cfg["ke"]
    sl = cfg["stop_loss_pct"]
    print(f"{vi}({tf}): filter={fid}, k_eps={ke}, stop_loss={sl}%")
```

### 1.3 不同 ticker/session 的 view_labels 映射差异(重要!)

**这个坑踩过的人最多。** v0 在一份数据中可能是周线,在另一份中可能是 15 分钟线。

实测数据中发现的 4 种映射:

| 映射模式 | v0 | v1 | v2 | v3 | 示例 session |
|---------|----|----|----|----|-------------|
| 粗到细(周) | 周线 | 日线 | 60分钟 | 15分钟 | 3690_HK_DP preset |
| 粗到细(日) | 日线 | 60分钟 | 15分钟 | 5分钟 | AAPL_US preset |
| 细到粗 | 15分钟 | 60分钟 | 日线 | 周线 | AAPL_US 另一 preset |
| 混合 | 日线 | 60分钟 | 15分钟 | 周线 | 3690_HK 另一 preset |

**正确做法**: 每次分析前从 `metadata.json["view_labels"]` 读取映射,**不要假设 v0=日线**。

```python
# 正确:从 metadata 读取后建立反向索引
label_to_view = {v: k for k, v in meta["view_labels"].items()}
day_view = label_to_view["日线"]     # 可能是 v0 或 v1 或 v2
min15_view = label_to_view["15分钟"] # 可能是 v1 或 v2 或 v3

# 错误:硬编码假设
day_pnl = df["v1_pnl_long"]  # ❌ v1 不一定是日线!
```

---

## 第二章: 逻辑正确的分析框架

### 2.1 6 大陷阱与工具保护速查表

| # | 陷阱 | 风险等级 | filter_research 的工具保护 | 用户仍需注意 |
|---|------|---------|--------------------------|-------------|
| 1 | **前视偏差** | 高 | BacktestRunner 逐 bar 滑动窗口,每个 bar 只用 `[i-n_pts, i]` 数据 | 数据预处理(如全局归一化)若在全序列上做则引入未来信息 |
| 2 | **幸存者偏差** | 中 | 当前 DB 存 13 个标的的历史数据(含退市/长期横盘) | 自行添加股票池时要包含已退市标的 |
| 3 | **过拟合** | 高 | 10 滤波器对比工具的 Pareto 前沿可视化 | 测试集只能跑一次;网格搜索在训练集上做;参数量 < 3 |
| 4 | **多重检验** | 中 | 无内置校正(已知局限) | 10 滤波器 x 4 时间帧 = 40 种组合,做 Bonferroni 校正: α=0.05/40=0.00125 |
| 5 | **数据窥探** | 高 | 数据集按时间划分(60/20/20)而非随机划分 | 看到测试集结果后绝不回去调参——这是最致命的错误 |
| 6 | **伪相关性** | 低 | 跨周期对齐用前向填充(粗→细),避免反向 | 粗周期信号(如日线级)投影到 5m 细周期上产生的"高相关"是幻觉 |

### 2.2 统计显著性判别

#### 2.2.1 t-statistic 速算

```python
import numpy as np
from scipy import stats

def signal_significance(returns: np.ndarray, risk_free_rate: float = 0.02) -> dict:
    """对交易收益率做 t 检验"""
    n = len(returns)
    if n < 30:
        return {"verdict": "样本不足", "n": n, "t_stat": None, "p_value": None}
    
    excess = returns - risk_free_rate / 252  # 日化无风险利率
    t_stat = np.mean(excess) / (np.std(excess, ddof=1) / np.sqrt(n))
    p_value = 2 * stats.t.sf(abs(t_stat), df=n-1)
    
    verdict = "显著" if p_value < 0.05 else "不显著"
    return {"verdict": verdict, "n": n, "t_stat": t_stat, "p_value": p_value}
```

#### 2.2.2 窗口长度与统计意义

| 日线 bar 数 | 约合年份 | 统计可靠性 | 判断 |
|------------|---------|-----------|------|
| < 30 | < 1.5 个月 | 完全不可靠 | 放弃 |
| 30-100 | 1.5-5 个月 | 波动极大 | 仅作方向参考 |
| 100-500 | 5 个月-2 年 | 勉强可靠 | Sharpe 可信度 ~60% |
| 500+ | 2 年以上 | 基本可靠 | Sharpe 可信度 ~85% |
| 1000+ | 4 年以上 | 可靠 | 可做统计推断 |

#### 2.2.3 多重检验校正(10 滤波器 x 4 时间帧场景)

```python
from statsmodels.stats.multitest import multipletests

# 假设你跑了 40 种滤波器/时间帧组合,得到了 40 个策略的 Sharpe p-value
p_values = [0.002, 0.015, 0.03, 0.08, ...]  # 40 个

# Bonferroni 校正
reject_bonf, p_bonf, _, _ = multipletests(p_values, alpha=0.05, method='bonferroni')

# Benjamini-Hochberg (更宽松,控制 FDR)
reject_bh, p_bh, _, _ = multipletests(p_values, alpha=0.05, method='fdr_bh')

# 如果 Bonferroni 校正后仍有显著的组合 → 信号可信度极高
# 如果只有 BH 显著而 Bonferroni 不显著 → 信号可信度中等,需更多验证
```

---

## 第三章: 实操分析流程(含代码)

### Step 1: 数据加载与验证

```python
import pandas as pd
import numpy as np
import json
from pathlib import Path
from glob import glob

# ============================================================
# Step 1: 数据加载与验证
# ============================================================

def load_backtest_session(session_dir: str) -> tuple:
    """
    加载一个回测 session 的全部数据。
    
    参数:
        session_dir: 回测 output 目录,如 "backtest_output/3690_20260726-193857-3690"
    
    返回:
        (df, meta, view_label_to_idx)
        - df: Parquet DataFrame(51 列)
        - meta: metadata dict
        - view_label_to_idx: {"日线": "v1", "60分钟": "v2", ...}
    """
    parquet_path = Path(session_dir) / "backtest_result.parquet"
    meta_path = Path(session_dir) / "metadata.json"
    
    if not parquet_path.exists():
        raise FileNotFoundError(f"找不到 {parquet_path}")
    
    df = pd.read_parquet(parquet_path)
    meta = json.load(open(meta_path)) if meta_path.exists() else {}
    
    # 建立中文标签 → 视图索引的反向映射
    view_label_to_idx = {v: k for k, v in meta.get("view_labels", {}).items()}
    
    return df, meta, view_label_to_idx

# ---- 验证清单 ----
def validate_session(df: pd.DataFrame, meta: dict) -> dict:
    """验证 session 数据质量,返回检查报告"""
    report = {}
    
    # 1. 列数检查
    report["n_cols"] = df.shape[1]
    report["cols_ok"] = df.shape[1] == 51
    
    # 2. 行数检查
    report["n_rows"] = df.shape[0]
    report["rows_ok"] = df.shape[0] >= 100  # 至少 100 bar
    
    # 3. 空值检查
    null_cols = df.isnull().sum()
    null_cols = null_cols[null_cols > 0]
    report["null_cols"] = dict(null_cols) if len(null_cols) > 0 else {}
    report["has_nulls"] = len(null_cols) > 0
    
    # 4. bar_index 连续性检查
    diffs = df["bar_index"].diff().dropna()
    gaps = diffs[diffs != 1]
    report["bar_gaps"] = len(gaps)
    report["bar_continuous"] = len(gaps) == 0
    
    # 5. view_labels 一致性检查
    if "view_labels" in meta:
        labels = meta["view_labels"]
        report["n_views"] = len(labels)
        report["view_labels"] = labels
        report["has_duplicate_labels"] = len(set(labels.values())) != len(labels)
    
    # 6. 状态检查
    report["status"] = meta.get("status", "unknown")
    report["completed"] = meta.get("status") == "completed"
    
    return report

# ============================================================
# 使用示例
# ============================================================
session_dir = "backtest_output/3690_20260726-193857-3690"
df, meta, view_map = load_backtest_session(session_dir)

print("=== 视图映射 ===")
for label, vidx in view_map.items():
    print(f"  {vidx} = {label}")

print("\n=== 验证报告 ===")
report = validate_session(df, meta)
for k, v in report.items():
    print(f"  {k}: {v}")
```

**Go 标准**: `rows_ok=True`, `bar_continuous=True`, `has_nulls=False` 或仅有 `trade/trade_return` 列的空值(正常——无交易时这些列为空)。
**No-Go**: `rows_ok=False`(行数 < 100),`bar_continuous=False`(有缺口),或任意 `pnl_*`/`sig` 列有空值。

### Step 2: 单视图信号质量评估

```python
# ============================================================
# Step 2: 单视图信号质量评估
# ============================================================

def analyze_single_view(df: pd.DataFrame, view: str) -> dict:
    """
    对单个视图做完整的信号质量分析。
    
    参数:
        df: Parquet DataFrame
        view: 视图索引,如 "v0", "v1", "v2", "v3"
    """
    sig = df[f"{view}_sig"]
    pnl_l = df[f"{view}_pnl_long"]
    pnl_s = df[f"{view}_pnl_short"]
    
    results = {}
    
    # ---- 信号基础统计 ----
    results["total_bars"] = len(sig)
    results["sig_long_pct"] = (sig == 1).mean()   # 多头信号占比
    results["sig_short_pct"] = (sig == -1).mean()  # 空头信号占比
    results["sig_neutral_pct"] = (sig == 0).mean() # 观望占比
    
    # ---- 信号翻转频率 ----
    flips = (sig.diff() != 0).sum()
    results["signal_flips"] = flips
    results["bars_per_flip"] = len(sig) / max(flips, 1)
    
    # ---- 信号质量判断 ----
    if results["bars_per_flip"] < 3:
        results["quality"] = "震荡——信号翻转过于频繁,噪声主导"
    elif results["bars_per_flip"] < 10:
        results["quality"] = "正常——信号密度适中"
    else:
        results["quality"] = "稀疏——信号太保守,可能错过大部分行情"
    
    # ---- PnL 基础统计 ----
    results["pnl_long_final"] = pnl_l.iloc[-1] - 100
    results["pnl_short_final"] = pnl_s.iloc[-1] - 100
    results["pnl_long_max"] = pnl_l.max() - 100
    results["pnl_short_max"] = pnl_s.max() - 100
    
    # ---- 多空一致性(理想情况:多空互为镜像) ----
    corr = pnl_l.corr(pnl_s)
    results["long_short_corr"] = corr
    results["long_short_mirror"] = "多空互为镜像 ✓" if corr < -0.5 else "多空不镜像,存在单向偏差"
    
    return results

# ---- 批量分析 4 个视图 ----
def analyze_all_views(df: pd.DataFrame, meta: dict) -> pd.DataFrame:
    """对所有视图做批量分析,返回汇总 DataFrame"""
    rows = []
    labels = meta.get("view_labels", {f"v{i}": f"v{i}" for i in range(4)})
    for vi in ["v0", "v1", "v2", "v3"]:
        r = analyze_single_view(df, vi)
        r["view"] = vi
        r["label"] = labels.get(vi, vi)
        r["n_trades_long"] = df[f"{vi}_trade"].str.contains("long", na=False).sum()
        r["n_trades_short"] = df[f"{vi}_trade"].str.contains("short", na=False).sum()
        rows.append(r)
    return pd.DataFrame(rows)

# ---- 使用示例 ----
view_report = analyze_all_views(df, meta)
print(view_report[["view", "label", "bars_per_flip", "quality",
                    "pnl_long_final", "pnl_short_final", "long_short_corr"]].to_string(index=False))
```

**Go 标准**: 粗周期 `bars_per_flip >= 8`,细周期 `bars_per_flip >= 3`;`long_short_corr < -0.3`(多空基本对称)。
**No-Go**: 任一视图 `bars_per_flip < 2`(信号密度过高——纯噪声);粗周期多空 PnL 同亏。

### Step 3: 跨周期一致性检查

```python
# ============================================================
# Step 3: 跨周期一致性检查
# ============================================================

def cross_timeframe_consistency(df: pd.DataFrame, meta: dict) -> dict:
    """
    检查 4 个时间帧之间的一致性。
    
    核心逻辑:粗周期定方向,细周期只在同向时交易。检查:
    1. 粗周期持仓时细周期同向 PnL 是否为正
    2. 同向性过滤是否提升细周期表现
    """
    labels = meta.get("view_labels", {})
    view_map = {v: k for k, v in labels.items()}
    
    # 确定粗细关系(按时间帧长度排序)
    tf_order = ["周线", "日线", "60分钟", "15分钟", "5分钟"]
    ordered_views = []
    for tf in tf_order:
        if tf in view_map:
            ordered_views.append((tf, view_map[tf]))
    
    if len(ordered_views) < 2:
        return {"error": "需要至少 2 个时间帧"}
    
    results = {}
    
    # 对每一对相邻的粗→细周期做检查
    for i in range(len(ordered_views) - 1):
        coarse_tf, coarse_v = ordered_views[i]
        fine_tf, fine_v = ordered_views[i + 1]
        
        pair_key = f"{coarse_tf}→{fine_tf}"
        
        # 粗周期多头持仓时,细周期多头的表现
        coarse_long = df[f"{coarse_v}_sig"] == 1
        fine_pnl_when_coarse_long = df.loc[coarse_long, f"{fine_v}_pnl_long"]
        
        # 粗周期空头持仓时,细周期空头的表现
        coarse_short = df[f"{coarse_v}_sig"] == -1
        fine_pnl_when_coarse_short = df.loc[coarse_short, f"{fine_v}_pnl_short"]
        
        # 一致性比率
        fine_sig = df[f"{fine_v}_sig"]
        coarse_sig = df[f"{coarse_v}_sig"]
        same_direction = (fine_sig == coarse_sig) & (fine_sig != 0)
        consistency_rate = same_direction.sum() / max((fine_sig != 0).sum(), 1)
        
        results[pair_key] = {
            "coarse_long_bars": coarse_long.sum(),
            "fine_long_pnl_when_coarse_long": fine_pnl_when_coarse_long.iloc[-1] - fine_pnl_when_coarse_long.iloc[0] if len(fine_pnl_when_coarse_long) > 1 else 0,
            "coarse_short_bars": coarse_short.sum(),
            "fine_short_pnl_when_coarse_short": fine_pnl_when_coarse_short.iloc[-1] - fine_pnl_when_coarse_short.iloc[0] if len(fine_pnl_when_coarse_short) > 1 else 0,
            "consistency_rate": consistency_rate,
        }
    
    return results

# ---- 使用示例 ----
consistency = cross_timeframe_consistency(df, meta)
for pair, stats in consistency.items():
    rate = stats["consistency_rate"]
    flag = "✓ 一致" if rate > 0.6 else ("△ 勉强" if rate > 0.4 else "✗ 矛盾")
    print(f"{pair}: 一致性={rate:.1%} {flag}")
```

**Go 标准**: 所有粗→细对的一致性 >= 60%;粗周期同向时细周期 PnL 为正。
**No-Go**: 任一粗→细对一致性 < 40%——跨周期信号互相矛盾,放弃。

### Step 4: 策略绩效评估(6 指标检查表)

**关键原则**: 分别看 long 和 short 的指标,**不使用 combined**(`max(long, short)` 实盘不可实现)。

```python
# ============================================================
# Step 4: 策略绩效评估
# ============================================================

def compute_sharpe(pnl_series: np.ndarray, rf_annual: float = 0.02) -> float:
    """年化 Sharpe Ratio(简化版)"""
    returns = np.diff(pnl_series) / pnl_series[:-1]
    excess = returns - rf_annual / 252
    if np.std(excess) == 0:
        return 0.0
    return np.mean(excess) / np.std(excess) * np.sqrt(252)

def compute_max_drawdown(pnl_series: np.ndarray) -> tuple:
    """计算最大回撤和回撤持续时间"""
    peak = pnl_series[0]
    max_dd = 0.0
    max_dd_dur = 0
    current_dur = 0
    for v in pnl_series:
        if v > peak:
            peak = v
        dd = (v - peak) / peak
        if dd < max_dd:
            max_dd = dd
        if dd < 0:
            current_dur += 1
            if current_dur > max_dd_dur:
                max_dd_dur = current_dur
        else:
            current_dur = 0
    return max_dd, max_dd_dur

def compute_calmar(pnl_series: np.ndarray, rf: float = 0.02) -> float:
    """Calmar Ratio = 年化收益 / |最大回撤|"""
    total_return = (pnl_series[-1] - pnl_series[0]) / pnl_series[0]
    n_years = len(pnl_series) / 252
    ann_return = (1 + total_return) ** (1 / max(n_years, 0.1)) - 1
    max_dd, _ = compute_max_drawdown(pnl_series)
    return ann_return / abs(max_dd) if max_dd != 0 else float('inf')

def compute_win_rate(trades: list[dict]) -> float:
    """从交易列表计算胜率"""
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t["return"] > 0)
    return wins / len(trades)

def compute_profit_factor(trades: list[dict]) -> float:
    """盈亏比 = 总盈利/总亏损"""
    gross_win = sum(t["return"] for t in trades if t["return"] > 0)
    gross_loss = abs(sum(t["return"] for t in trades if t["return"] < 0))
    return gross_win / gross_loss if gross_loss > 0 else float('inf')

# ---- 提取唯一交易(去重多 bar 退出) ----
def extract_trades(df: pd.DataFrame, view: str) -> list[dict]:
    """从 Parquet 中提取唯一交易列表,去重多 bar 退出标注"""
    trade_col = df[f"{view}_trade"]
    return_col = df[f"{view}_trade_return"]
    reason_col = df[f"{view}_trade_reason"]
    
    trades = []
    prev_trade = None
    for i in range(len(df)):
        t = trade_col.iloc[i]
        if pd.isna(t):
            prev_trade = None
            continue
        # 同一交易事件的连续 bar → 只记第一笔
        if t == prev_trade:
            continue
        prev_trade = t
        trades.append({
            "bar": df["bar_index"].iloc[i],
            "timestamp": df["bar_timestamp"].iloc[i],
            "type": t,
            "return": return_col.iloc[i] if not pd.isna(return_col.iloc[i]) else 0,
            "reason": reason_col.iloc[i] if not pd.isna(reason_col.iloc[i]) else "unknown",
        })
    return trades

# ---- 完整的单视图绩效评估 ----
def evaluate_view_performance(df: pd.DataFrame, view: str) -> dict:
    """
    对单个视图分别评估 long 和 short 策略绩效。
    
    返回 6 核心指标 x 2 方向 = 12 个指标值
    """
    pnl_long = df[f"{view}_pnl_long"].values
    pnl_short = df[f"{view}_pnl_short"].values
    all_trades = extract_trades(df, view)
    
    long_trades = [t for t in all_trades if "long" in str(t["type"])]
    short_trades = [t for t in all_trades if "short" in str(t["type"])]
    
    results = {}
    for side, pnl, trades in [("long", pnl_long, long_trades),
                               ("short", pnl_short, short_trades)]:
        max_dd, max_dd_dur = compute_max_drawdown(pnl)
        results[side] = {
            "sharpe": compute_sharpe(pnl),
            "max_dd": max_dd,
            "max_dd_duration": max_dd_dur,
            "calmar": compute_calmar(pnl),
            "win_rate": compute_win_rate(trades),
            "profit_factor": compute_profit_factor(trades),
            "n_trades": len(trades),
            "total_return": (pnl[-1] - 100),
        }
    
    return results

# ---- 6 指标检查表 ----
def six_metric_checklist(perf: dict) -> dict:
    """
    对评估结果做 Go/No-Go 判定。
    
    合格标准:
      Sharpe     > 0.8 (优异 > 1.5)
      MaxDD      > -20% (优异 > -10%)
      MaxDD Dur  < 100 (优异 < 50)
      Calmar     > 0.5 (优异 > 1.0)
      Win Rate   > 40% (趋势策略胜率本就不高)
      Profit Factor > 1.3 (优异 > 1.8)
    """
    checks = {}
    for side in ["long", "short"]:
        p = perf[side]
        score = 0
        items = []
        
        # 1. Sharpe
        ok = p["sharpe"] > 0.8
        if ok: score += 1
        items.append({"metric": "Sharpe", "value": p["sharpe"], "pass": ok, "threshold": "> 0.8"})
        
        # 2. MaxDD
        ok = p["max_dd"] > -0.20
        if ok: score += 1
        items.append({"metric": "MaxDD", "value": f"{p['max_dd']:.2%}", "pass": ok, "threshold": "> -20%"})
        
        # 3. MaxDD Duration
        ok = p["max_dd_duration"] < 100
        if ok: score += 1
        items.append({"metric": "MaxDD Dur", "value": p["max_dd_duration"], "pass": ok, "threshold": "< 100 bars"})
        
        # 4. Calmar
        ok = p["calmar"] > 0.5
        if ok: score += 1
        items.append({"metric": "Calmar", "value": f"{p['calmar']:.2f}", "pass": ok, "threshold": "> 0.5"})
        
        # 5. Win Rate
        ok = p["win_rate"] > 0.40
        if ok: score += 1
        items.append({"metric": "Win Rate", "value": f"{p['win_rate']:.1%}", "pass": ok, "threshold": "> 40%"})
        
        # 6. Profit Factor
        ok = p["profit_factor"] > 1.3
        if ok: score += 1
        items.append({"metric": "Profit Factor", "value": f"{p['profit_factor']:.2f}", "pass": ok, "threshold": "> 1.3"})
        
        checks[side] = {"score": score, "items": items,
                        "verdict": "Go" if score >= 4 else ("观望" if score >= 3 else "No-Go")}
    
    return checks

# ---- 使用示例 ----
perf = evaluate_view_performance(df, "v1")  # 假设 v1=日线
checks = six_metric_checklist(perf)
for side in ["long", "short"]:
    print(f"\n=== {side.upper()} ===")
    c = checks[side]
    print(f"Score: {c['score']}/6 → {c['verdict']}")
    for item in c["items"]:
        flag = "✓" if item["pass"] else "✗"
        print(f"  {flag} {item['metric']}: {item['value']} ({item['threshold']})")
```

**Go 标准**: long/short 至少一个方向的 score >= 4(满足 4/6 指标)。
**No-Go**: long 和 short 的 score 都 < 3——两个方向都不可靠,放弃。

### Step 5: 交易归因与事件分析

```python
# ============================================================
# Step 5: 交易归因与事件分析
# ============================================================

def trade_attribution(df: pd.DataFrame, view: str) -> dict:
    """交易归因分析:离场原因分布、盈亏分布、连续亏损"""
    trades = extract_trades(df, view)
    if not trades:
        return {"error": "无交易记录"}
    
    # 1. 离场原因分布
    reasons = {}
    for t in trades:
        r = t.get("reason", "unknown")
        reasons[r] = reasons.get(r, 0) + 1
    
    # 2. 收益分布
    returns = [t["return"] for t in trades]
    
    # 3. 最大连续亏损(连续亏损次数)
    max_consecutive_loss = 0
    current_consecutive = 0
    for r in returns:
        if r < 0:
            current_consecutive += 1
            max_consecutive_loss = max(max_consecutive_loss, current_consecutive)
        else:
            current_consecutive = 0
    
    # 4. 单笔最大贡献(检查是否单笔撑起总收益)
    total_return = sum(returns)
    max_single_pct = max(returns) / total_return if total_return > 0 else 999
    
    # 5. 去掉最佳交易后
    without_best = total_return - max(returns)
    
    return {
        "total_trades": len(trades),
        "exit_reasons": reasons,
        "mean_return": np.mean(returns),
        "median_return": np.median(returns),
        "std_return": np.std(returns),
        "max_single_return": max(returns),
        "min_single_return": min(returns),
        "max_consecutive_loss": max_consecutive_loss,
        "best_trade_pct_of_total": max_single_pct,
        "total_without_best": without_best,
        "ruined_by_single_trade": without_best < 0,  # 红旗标志!
    }

# ---- 使用示例 ----
for vi in ["v0", "v1", "v2", "v3"]:
    attr = trade_attribution(df, vi)
    if "error" in attr:
        continue
    label = meta["view_labels"][vi]
    print(f"\n=== {vi}({label}) 交易归因 ===")
    print(f"  总交易: {attr['total_trades']} 笔")
    print(f"  平均收益: {attr['mean_return']:.2%}")
    print(f"  最佳单笔: {attr['max_single_return']:.2%} (占总收益 {attr['best_trade_pct_of_total']:.0%})")
    print(f"  最大连续亏损: {attr['max_consecutive_loss']} 笔")
    print(f"  离场原因: {attr['exit_reasons']}")
    if attr["ruined_by_single_trade"]:
        print(f"  ⚠️ 红旗: 去掉最佳交易后总收益变负!")
```

**红旗标志**(任一触发则放弃此视图):
- `ruined_by_single_trade == True`(去掉最佳一笔总收益变负)
- `max_single_pct > 30%`(最佳单笔贡献超过总收益 30%)
- `max_consecutive_loss > 8`(连续亏损超过 8 笔)

### Step 6: 稳健性检验与样本外验证

```python
# ============================================================
# Step 6: 稳健性检验与样本外验证
# ============================================================

def robustness_check(sessions: list[str]) -> dict:
    """
    跨多个 session 的稳健性检验。
    
    参数:
        sessions: 多个 session 目录路径列表
    
    返回:
        每个视图在多个 session 间的指标稳定性
    """
    results = {}
    for v in ["v0", "v1", "v2", "v3"]:
        sharpes = []
        maxdds = []
        for sdir in sessions:
            df, meta, _ = load_backtest_session(sdir)
            pnl_l = df[f"{v}_pnl_long"].values
            pnl_s = df[f"{v}_pnl_short"].values
            
            # 使用较好的那个方向
            ret_l = (pnl_l[-1] - 100)
            ret_s = (pnl_s[-1] - 100)
            pnl = pnl_l if abs(ret_l) > abs(ret_s) else pnl_s
            
            sharpes.append(compute_sharpe(pnl))
            maxdds.append(compute_max_drawdown(pnl)[0])
        
        if sharpes:
            results[v] = {
                "sharpe_mean": np.mean(sharpes),
                "sharpe_std": np.std(sharpes),
                "sharpe_cv": np.std(sharpes) / abs(np.mean(sharpes)) if np.mean(sharpes) != 0 else 999,
                "maxdd_mean": np.mean(maxdds),
                "maxdd_std": np.std(maxdds),
                "n_sessions": len(sessions),
            }
    
    return results

def sample_forward_validation(df: pd.DataFrame, view: str,
                               train_split: float = 0.6,
                               val_split: float = 0.8) -> dict:
    """
    时序划分的样本内/样本外验证。
    
    注意:时序划分不能随机——必须用前 60% 的数据做训练,
    中间 20% 做验证,最后 20% 做测试。绝不能在测试集上看到结果后回头调参。
    """
    n = len(df)
    train_end = int(n * train_split)
    val_end = int(n * val_split)
    
    def eval_segment(pnl: np.ndarray) -> dict:
        if len(pnl) < 30:
            return {"error": "段太短"}
        md, dur = compute_max_drawdown(pnl)
        return {
            "sharpe": compute_sharpe(pnl),
            "max_dd": md,
            "max_dd_dur": dur,
            "total_return": (pnl[-1] - pnl[0]),
            "n_bars": len(pnl),
        }
    
    # 选择 PnL 序列(以绝对值较大的方向为准)
    pnl_l = df[f"{view}_pnl_long"].values
    pnl_s = df[f"{view}_pnl_short"].values
    pnl = pnl_l if abs(pnl_l[-1] - 100) > abs(pnl_s[-1] - 100) else pnl_s
    
    train_result = eval_segment(pnl[:train_end])
    val_result = eval_segment(pnl[train_end:val_end])
    test_result = eval_segment(pnl[val_end:])
    
    # 过拟合检测
    overfit_flags = []
    if isinstance(train_result.get("sharpe"), (int, float)) and isinstance(test_result.get("sharpe"), (int, float)):
        if train_result["sharpe"] > 2.0 and test_result["sharpe"] < 0.5:
            overfit_flags.append("严重过拟合:训练 Sharpe>2.0 但测试<0.5")
        if test_result["sharpe"] < 0:
            overfit_flags.append("测试集亏损")
        decay = (train_result["sharpe"] - test_result["sharpe"]) / train_result["sharpe"] if train_result["sharpe"] > 0 else 0
        if decay > 0.5:
            overfit_flags.append(f"衰减过大:{(decay*100):.0f}%")
    
    return {
        "train": train_result,
        "val": val_result,
        "test": test_result,
        "overfit_flags": overfit_flags,
        "overfit_verdict": "无过拟合迹象" if not overfit_flags else "过拟合警告",
    }

# ---- 多 session 对比使用示例 ----
import glob
session_dirs = sorted(glob.glob("backtest_output/3690_*/"))[:5]  # 同标的最近 5 次
if len(session_dirs) >= 3:
    robust = robustness_check(session_dirs)
    for view, stats in robust.items():
        cv = stats["sharpe_cv"]
        flag = "✓ 稳定" if cv < 0.3 else ("△ 波动" if cv < 0.5 else "✗ 不稳定")
        print(f"{view}: Sharpe={stats['sharpe_mean']:.2f}±{stats['sharpe_std']:.2f} (CV={cv:.2f}) {flag}")

# ---- 样本外验证使用示例 ----
sfv = sample_forward_validation(df, "v1")  # 假设 v1=日线
print(f"\n样本外验证:")
for seg in ["train", "val", "test"]:
    if "error" in sfv[seg]:
        continue
    s = sfv[seg]
    print(f"  {seg}: Sharpe={s['sharpe']:.2f}, MaxDD={s['max_dd']:.2%}, bars={s['n_bars']}")
print(f"过拟合判断: {sfv['overfit_verdict']}")
for flag in sfv["overfit_flags"]:
    print(f"  ⚠️ {flag}")
```

**Go 标准**: `sharpe_cv < 0.3`(多次运行变异系数低于 30%); 测试集 `sharpe > 0.5` 且 `max_dd` 不超过训练集 1.5 倍。
**No-Go**: `sharpe_cv > 0.5`(结果高度不稳定); 测试集亏损或 `max_dd` 翻倍。

---

## 第四章: 分析结论置信度分级

综合 Step 1-6 的结果,按以下标准对分析结论分级:

### 高置信度

满足**全部**条件:
- 日线数据 >= 500 bar
- Step 4 中至少一个方向(long 或 short)score >= 5(6 项指标满足 5 项)
- Step 3 中所有粗→细对一致性 >= 60%
- Step 5 中无红旗标志触发
- Step 6 中 `sharpe_cv < 0.3` 且测试集 Sharpe > 0.5

**结论**: 策略有统计依据,可考虑小仓位实盘测试。仍需注意实盘交易成本(回测未建模)打 6-7 折。

### 中置信度

满足**大部分**条件,但有 1-2 个临界值:
- 粗周期 score >= 4 但 < 5
- 或跨周期一致性在 40-60% 之间
- 或 Sharpe CV 在 0.3-0.5 之间

**结论**: 信号有方向价值,但不够稳健。建议:(1) 扩大样本量;(2) 换滤波器组合验证;(3) 暂不实盘,跟踪观察。

### 低置信度

满足**少数**条件:
- 多视图 score 都在 3 左右
- 或存在 1 个红旗标志
- 或测试集表现明显差于训练集

**结论**: 信号可能来自噪声。不建议实盘。如果对该标的有强烈信念,需完全不同的参数组合重新验证。

### 放弃

任一触发:
- 数据量 < 100 bar
- 多视图中全部 score < 3
- Step 5 中 `ruined_by_single_trade == True`
- Step 6 中测试集亏损
- 长期/短期 PnL 同亏

**结论**: 换标的或换策略。不要投入更多时间。

---

## 第五章: 代码工具箱

以下函数均可直接复制到 `.py` 文件或 Jupyter notebook 中使用:

### 5.1 数据加载函数

```python
import pandas as pd
import json
from pathlib import Path

def load_backtest_session(session_dir: str) -> tuple:
    """加载回测 session → 返回 (df, meta, view_map)"""
    parquet_path = Path(session_dir) / "backtest_result.parquet"
    meta_path = Path(session_dir) / "metadata.json"
    
    df = pd.read_parquet(parquet_path)
    meta = json.load(open(meta_path)) if meta_path.exists() else {}
    view_map = {v: k for k, v in meta.get("view_labels", {}).items()}
    
    return df, meta, view_map
```

### 5.2 视图提取函数

```python
def extract_view_data(df: pd.DataFrame, view: str) -> dict:
    """提取单个视图的完整数据为 dict"""
    return {
        "sig": df[f"{view}_sig"].values,
        "filtered": df[f"{view}_filtered"].values,
        "eps": df[f"{view}_eps"].values,
        "pnl_long": df[f"{view}_pnl_long"].values,
        "pnl_short": df[f"{view}_pnl_short"].values,
        "long_pos": df[f"{view}_long_pos"].values,
        "short_pos": df[f"{view}_short_pos"].values,
    }
```

### 5.3 交易提取函数

```python
import numpy as np

def extract_trades(df: pd.DataFrame, view: str) -> list[dict]:
    """提取唯一交易列表,自动去重多 bar 退出标注"""
    trades = []
    prev_event = None
    for i in range(len(df)):
        event = df[f"{view}_trade"].iloc[i]
        if pd.isna(event):
            prev_event = None
            continue
        if event == prev_event:  # 同一事件连续 bar → 跳过
            continue
        prev_event = event
        trades.append({
            "bar": int(df["bar_index"].iloc[i]),
            "timestamp": str(df["bar_timestamp"].iloc[i]),
            "type": event,
            "return": float(df[f"{view}_trade_return"].iloc[i]) if not pd.isna(df[f"{view}_trade_return"].iloc[i]) else 0.0,
            "reason": str(df[f"{view}_trade_reason"].iloc[i]) if not pd.isna(df[f"{view}_trade_reason"].iloc[i]) else "unknown",
        })
    return trades
```

### 5.4 跨 Session 对比函数

```python
from glob import glob

def compare_sessions(session_dirs: list[str], view: str, side: str = "long") -> pd.DataFrame:
    """跨多个 session 对比同一视图+方向的绩效"""
    rows = []
    for sdir in session_dirs:
        df, meta, _ = load_backtest_session(sdir)
        pnl = df[f"{view}_pnl_{side}"].values
        max_dd, dur = compute_max_drawdown(pnl)
        rows.append({
            "session": Path(sdir).name[:30],
            "sharpe": compute_sharpe(pnl),
            "max_dd": max_dd,
            "calmar": compute_calmar(pnl),
            "total_return": (pnl[-1] - 100),
            "n_bars": len(pnl),
            "ticker": meta.get("ticker", "?"),
        })
    return pd.DataFrame(rows)

# 使用示例:
# sessions = sorted(glob("backtest_output/3690_*/"))
# comparison = compare_sessions(sessions, "v1", "long")
# print(comparison.to_string())
```

### 5.5 信号一致性分析函数

```python
def signal_consensus_score(df: pd.DataFrame) -> pd.Series:
    """
    计算 4 视图的信号共识得分。
    
    每一 bar: 每个多头信号 +1,每个空头信号 -1,观望 0
    共识得分范围 [-4, 4]: ±4 表示 4 视图全票,±1 表示仅 1 视图有信号
    """
    consensus = pd.Series(0, index=df.index)
    for v in ["v0", "v1", "v2", "v3"]:
        consensus += df[f"{v}_sig"].fillna(0).astype(int)
    return consensus

def consensus_quality(df: pd.DataFrame) -> dict:
    """分析信号共识的质量"""
    cs = signal_consensus_score(df)
    abs_cs = cs.abs()
    
    return {
        "strong_consensus_pct": (abs_cs >= 3).mean(),  # 强共识占比
        "medium_consensus_pct": ((abs_cs >= 2) & (abs_cs < 3)).mean(),
        "weak_consensus_pct": (abs_cs == 1).mean(),
        "no_signal_pct": (cs == 0).mean(),
        "mean_abs_consensus": abs_cs.mean(),
        "dominant_direction": "多头" if cs.sum() > 0 else "空头",
    }
```

---

## 附录 A: 关键文件路径速查

| 路径 | 内容 |
|------|------|
| `backtest_output/{ticker}_{timestamp}-{ticker}/` | 单次回测 session 目录 |
| `.../backtest_result.parquet` | 回测输出数据(51 列) |
| `.../metadata.json` | 回测配置与视图映射 |
| `data/market.db` | SQLite 原始行情库(141K 行,13 标的) |
| `filter/engine/filters.py` | 10 种滤波器实现 |
| `filter/engine/schmitt.py` | Schmitt 触发器(308 行) |
| `filter/backtest/metrics.py` | 16 项金融指标(Numba 加速,216 行) |
| `tools/filter_comparison_tool.py` | 滤波器对比工具 |
| `tools/view_backtest.py` | 回测结果查看器(501 行) |

## 附录 B: 已知局限清单

| # | 局限 | 影响 | 应对 |
|---|------|------|------|
| 1 | 策略 PnL 不扣交易成本/滑点 | 实盘预期打 6-7 折 | 聚焦日线/60m 级别低频策略 |
| 2 | `combined_pnl = max(long, short)` 不可实盘 | 仪表盘综合曲线会虚高 | 只看 long_pnl 和 short_pnl 各自指标 |
| 3 | 窗口 < 500 bar 时统计指标无意义 | 短窗口的 Sharpe 等全是噪声 | 确保分析窗口 >= 500 bar |
| 4 | 粗周期信号在细周期投影产生伪相关 | 跨周期对齐视图会高估相关 | 不以细周期信号密度评估粗周期效果 |
| 5 | 滤波器对比工具未设随机种子 | 每次运行 SNR/延迟值略有差异 | 运行前加 `np.random.seed(0)` |
| 6 | 无形式化统计显著性检验 | 过度依赖多滤波器共识 | 用本指南第二章的 t-statistic/Bonferroni 手动检验 |
| 7 | env 变量未被代码读取(路径硬编码) | 不同环境需改源码 | 切换环境前检查 `fetcher.py`/`db.py` 中的路径 |
| 8 | `view_labels` 映射不固定 | v0 在不同 session 可能是周线/日线/15分钟 | 每次从 metadata.json 读取,不硬编码假设 |
| 9 | `trade_return` 列在多 bar 退出时重复 | 直接求和会重复计算收益 | 使用 `extract_trades()` 去重函数 |
| 10 | 部分 session 格式为 `kv_store` 而非纯 DataFrame | 直接 `pd.read_parquet()` 可能失败 | 检查异常后尝试 `pd.read_parquet(path, filters=None)` |

## 附录 C: 常用 CLI 命令

```bash
# 0. 环境准备
cd /Users/xfpan/claude/filter_research

# 1. 查看所有回测 session
ls backtest_output/

# 2. 快速查看某 session 的元信息
python3 -c "
import json
m = json.load(open('backtest_output/3690_20260726-193857-3690/metadata.json'))
print(f'标的: {m[\"ticker\"]}')
print(f'视图: {m[\"view_labels\"]}')
print(f'行数: {m[\"parquet_row_count\"]}')
"

# 3. 查看 Parquet 的列名和前几行
python3 -c "
import pandas as pd
df = pd.read_parquet('backtest_output/3690_20260726-193857-3690/backtest_result.parquet')
print(df[['bar_index','close','v0_sig','v0_pnl_long','v0_pnl_short']].head(10))
"

# 4. 快速统计各视图交易笔数
python3 -c "
import pandas as pd
df = pd.read_parquet('backtest_output/3690_20260726-193857-3690/backtest_result.parquet')
for v in ['v0','v1','v2','v3']:
    trades = df[df[f'{v}_trade'].notna()]
    print(f'{v}: {len(trades)} 个交易事件')
"

# 5. 批量运行完整分析
python3 -c "
import glob
from pathlib import Path

# 将所有分析函数放在 analysis.py 中
# 然后:
# sessions = sorted(glob.glob('backtest_output/*/'))
# for sdir in sessions[-5:]:  # 最近 5 个
#     print(f'\\n======== {Path(sdir).name} ========')
#     df, meta, vm = load_backtest_session(sdir)
#     report = validate_session(df, meta)
#     ... 
"

# 6. 对比同一标的多个 session
python3 -c "
import glob, json
sessions = sorted(glob.glob('backtest_output/3690_*/metadata.json'))
for s in sessions[-5:]:
    m = json.load(open(s))
    labels = list(m.get('view_labels', {}).values())
    print(f'{Path(s).parent.name}: {labels}')
"

# 7. 检查 Kalman 滤波器是否为确定性(验证结论)
grep -n "signal\[0\]" filter/engine/filters.py
# 预期输出: x = [signal[0], 0.0] —— 确定性初始化,非随机
```

---

> **维护说明**: 本指南基于 filter_research 2026-07-27 版本代码和实际 Parquet 输出编写。工具版本更新后,需验证 Schema 是否变化(列数增减)、view_labels 是否有新增映射模式、metadata.json 字段是否调整。
