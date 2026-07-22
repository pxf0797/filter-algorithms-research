# PnL feedback position process -- mockup generator
# Synthetic data only. Illustrates the mechanism (clear / recover / hold-band)
# of the new "actual long/short position process" panel. NOT a return promise.
# Output: pnl-feedback-mockup.png
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

matplotlib.rcParams["axes.unicode_minus"] = False

n = 201
x = np.arange(n)

# ---- synthetic THEORETICAL long_pnl (Layer0, with per-bar unrealized wiggle) ----
cx = [0, 12, 60, 92, 108, 132, 165, 200]
cy = [100, 100, 118, 110, 104, 114, 125, 123]
long_pnl = np.interp(x, cx, cy) + 0.6 * np.sin(x / 6.0) * np.clip(0.3 + x / n, 0, 1)

# ---- synthetic THEORETICAL short_pnl (Layer0) ----
sx = [0, 20, 70, 110, 150, 200]
sy = [100, 101, 99, 106, 108, 107]
short_pnl = np.interp(x, sx, sy) + 0.4 * np.sin(x / 5.0 + 1)

# ---- gate simulation (long direction) ----
DD_CLEAR, DD_RECOVER = 0.08, 0.03
peak, active = long_pnl[0], True
state = np.zeros(n, bool)
clears, recovers = [], []
for i in range(n):
    peak = max(peak, long_pnl[i])
    dd = 1 - long_pnl[i] / peak
    if active and dd > DD_CLEAR:
        active = False
        clears.append(i)
    elif (not active) and dd < DD_RECOVER:
        active = True
        recovers.append(i)
    state[i] = active

# ---- actual long equity: follow theoretical while ACTIVE, freeze while FLAT ----
actual = np.empty(n)
offset = 0.0
frozen = long_pnl[0]
prev = True
for i in range(n):
    if state[i]:
        if not prev:
            offset = frozen - long_pnl[i]
        actual[i] = long_pnl[i] + offset
    else:
        if prev:
            frozen = actual[i - 1] if i > 0 else long_pnl[0]
        actual[i] = frozen
    prev = state[i]

# ---- short hold band (illustrative): short taken while long is cleared ----
short_hold = (x >= 100) & (x <= 128)

# ================= PLOT =================
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True,
                               gridspec_kw={"height_ratios": [1, 1.2]})

# Row 1: existing theoretical PnL (unchanged)
ax1.plot(x, long_pnl, color="#2e7d32", lw=1.8, label="long_pnl (theoretical, unchanged)")
ax1.plot(x, short_pnl, color="#c62828", lw=1.4, alpha=0.85, label="short_pnl (theoretical)")
ax1.axhline(100, color="#999", lw=0.8, ls=":")
ax1.set_ylabel("PnL")
ax1.set_title("Existing PnL panel  (Layer 0, unchanged)")
ax1.legend(loc="upper left", fontsize=8)
ax1.grid(alpha=0.25)

# Row 2: NEW actual position process
ax2.plot(x, long_pnl, color="#2e7d32", lw=1.2, ls="--", alpha=0.55,
         label="theoretical long (shadow)")
ax2.plot(x, actual, color="#2e7d32", lw=2.4, label="ACTUAL long equity (gated)")
ax2.axhline(100, color="#999", lw=0.8, ls=":")

# status strip at bottom (x=data, y=axes-fraction)
tr = ax2.get_xaxis_transform()

def draw_band(mask, y0, color):
    s = None
    for i in range(n + 1):
        h = mask[i] if i < n else False
        if h and s is None:
            s = i
        if (not h) and s is not None:
            ax2.add_patch(Rectangle((s, y0), i - s, 0.05, transform=tr,
                                    color=color, alpha=0.55, lw=0))
            s = None

draw_band(state, 0.0, "#2e7d32")        # green = long held
draw_band(short_hold, 0.055, "#c62828")  # red = short held

# markers
for c in clears:
    ax2.annotate("CLEAR", (c, actual[c]), xytext=(0, 20), textcoords="offset points",
                 ha="center", fontsize=8, color="#b71c1c",
                 arrowprops=dict(arrowstyle="->", color="#b71c1c"))
for r in recovers:
    ax2.annotate("RECOVER", (r, actual[r]), xytext=(6, -24), textcoords="offset points",
                 ha="center", fontsize=8, color="#1565c0",
                 arrowprops=dict(arrowstyle="->", color="#1565c0"))

ax2.set_ylabel("Actual equity")
ax2.set_xlabel("bar index")
ax2.set_title("NEW: actual long/short position process  (driven by PnL feedback)")
ax2.legend(loc="upper left", fontsize=8)
ax2.grid(alpha=0.25)

fig.text(0.5, 0.005,
         "status strip:  green = LONG held   |   red = SHORT held   |   blank = CLEARED (flat)   "
         "--   CLEAR when theoretical drawdown > DD_CLEAR;  RECOVER when drawdown < DD_RECOVER",
         ha="center", fontsize=8, color="#444")

fig.suptitle("PnL-feedback position process -- mockup (mechanism illustration, not a return promise)",
             fontsize=11)
fig.tight_layout(rect=[0, 0.03, 1, 0.97])
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pnl-feedback-mockup.png")
fig.savefig(out, dpi=130)
print("saved:", out)
