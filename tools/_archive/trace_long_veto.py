# 根因追踪 + 修复验证：3690 60min "该做多没做多"
#
# 根因(已修复)：入场曾被 _compute_prediction_pairs 的 `pair_end-pair_start>=3` 门槛
#       + _compute_strategy_pnl 的 `if pair_end not in pred_map: continue` 静默丢弃。
#       修复(入场与预测解耦)后：入场仅凭 sig；无预测的 pair 也入场，止损回退固定%。
#       预期：应做多的 [16,37,51] 现在全部入场，被丢弃=[]。
import sqlite3
import sys
import numpy as np
sys.path.insert(0, "filter")
from services.filter_engine import (
    FILTERS, _schmitt_trigger, _find_all_pairs, _fit_physics_parabola,
    _compute_strategy_pnl,
)

N = 60
conn = sqlite3.connect("data/market.db")
rows = conn.execute(
    "SELECT close FROM kline WHERE ticker='3690' AND timeframe='60分钟' "
    "ORDER BY ts DESC LIMIT ?", (N,)).fetchall()
noisy = np.array([r[0] for r in rows][::-1], dtype=float)
t = np.arange(len(noisy), dtype=float)
filtered = np.asarray(FILTERS["savgol"]["func"](noisy, t, window=13, order=4), float).ravel()
_v = np.gradient(filtered, t); _a = np.gradient(_v, t)
sig = _schmitt_trigger(_v, _a, ewma_span=60, k_eps=0.1, sigma_min=0.05)["sig"]
all_pairs = _find_all_pairs(sig)

# ---- 逐 pair: 入场方向 + 是否因 <3 门槛被丢 ----
print("逐 pair 入场判定:")
print(f"{'pair':>10} {'gap':>4} {'sig[pe]':>7} {'方向':>6} {'有预测?':>7}  结果")
for ps, pe in all_pairs:
    gap = pe - ps
    v2 = int(sig[pe])
    d = "做多" if v2 == 1 else ("做空" if v2 == -1 else "-")
    has_pred = gap >= 3
    if not has_pred:
        res = f"无预测(gap={gap}<3)→止损回退固定%，仍入场{d}"
    else:
        res = f"有预测轨道→入场{d}"
    print(f"({ps:>3},{pe:>3}) {gap:>4} {v2:>7} {d:>6} {str(has_pred):>7}  {res}")

# ---- 完整策略成交 ----
pred_pairs = []
for ps, pe in all_pairs:
    if pe - ps >= 3:
        fr = _fit_physics_parabola(t, filtered, ps, pe)
        if fr is not None:
            pred_pairs.append({"fit_result": fr, "fit_start": ps, "pair_end": pe})
long_pnl, short_pnl, trades = _compute_strategy_pnl(t, filtered, sig, all_pairs, pred_pairs, 2.0, n_extend=8)
print(f"\n完整策略: long_pnl末值={long_pnl[-1]:.2f} short_pnl末值={short_pnl[-1]:.2f} 成交{len(trades)}笔")
for tr in trades:
    print(f"  #{tr['id']} {tr['type']:>5} entry={tr['entry_idx']} exit={tr['exit_idx']} "
          f"收益{tr['return_pct']:+.2f}% ({tr['exit_reason']})")
long_bars = sorted(pe for ps, pe in all_pairs if sig[pe] == 1)
long_taken = sorted(tr['entry_idx'] for tr in trades if tr['type'] == 'long')
print(f"\nsig=+1(应做多)的 pair_end: {long_bars}")
print(f"实际做多入场:             {long_taken}")
print(f"★被丢弃的做多入场:        {sorted(set(long_bars) - set(long_taken))}  ← 因 gap<3 无预测")
