"""
多周期股票滤波分析工具 — 4视图独立配置, 施密特触发器 + 滤波对比
"""

import json
import uuid
import streamlit as st
import numpy as np
import pandas as pd
import yfinance as yf
from scipy.signal import savgol_filter, butter, sosfiltfilt, medfilt
from scipy.ndimage import gaussian_filter1d
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pandas import DataFrame
from statsmodels.nonparametric.smoothers_lowess import lowess

# ---------------------------------------------------------------------------
# Page config (must be the first Streamlit command)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="滤波算法对比",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Filter implementations (scipy / numpy only)
# All accept (signal, t, ...) signature so they can be called uniformly as
#   filter_func(noisy, t, **param_values)
# ---------------------------------------------------------------------------

def apply_sma(signal, t, window):
    """简单移动平均 (Simple Moving Average)."""
    if window % 2 == 0:
        window += 1
    kernel = np.ones(window) / window
    return np.convolve(signal, kernel, mode="same")


def apply_ema(signal, t, span):
    """指数移动平均 (Exponential Moving Average) via pandas ewm."""
    return DataFrame({"v": signal}).ewm(span=span, adjust=False).mean().values.flatten()


def apply_wma(signal, t, window):
    """加权移动平均 (Weighted Moving Average)."""
    if window % 2 == 0:
        window += 1
    weights = np.arange(1, window + 1)
    weights = weights / weights.sum()
    return np.convolve(signal, weights, mode="same")


def apply_alma(signal, t, window, offset, sigma):
    """Arnaud Legoux 移动平均 (ALMA)."""
    if window % 2 == 0:
        window += 1
    m = (window - 1) * offset               # Gaussian center (offset=0.85 → near right = past)
    s = window / sigma if sigma > 0 else 1.0
    i = np.arange(window)
    weights = np.exp(-0.5 * ((i - m) / s) ** 2)
    weights /= weights.sum()
    return np.convolve(signal, weights, mode="same")


def apply_savgol(signal, t, window, order):
    """Savitzky-Golay 滤波 (多项式平滑)."""
    if window % 2 == 0:
        window += 1
    if order >= window:
        order = window - 1
    return savgol_filter(signal, window, order)


def apply_kalman(signal, t, Q, R):
    """1D 恒定速度卡尔曼滤波."""
    dt = t[1] - t[0]
    n = len(signal)
    x = np.array([signal[0], 0.0])     # [position, velocity]
    P = np.eye(2) * 0.1
    F = np.array([[1, dt], [0, 1]])     # state transition
    result = np.zeros(n)
    for i in range(n):
        # Predict
        x = F @ x
        P = F @ P @ F.T + np.array([
            [Q * dt ** 4 / 4, Q * dt ** 3 / 2],
            [Q * dt ** 3 / 2, Q * dt ** 2],
        ])
        # Update (scalar observation)
        y = signal[i] - x[0]             # innovation
        S = P[0, 0] + R                  # innovation covariance
        K = np.array([P[0, 0] / S, P[1, 0] / S])  # Kalman gain
        x = x + K * y
        P = P - np.outer(K, K) * S
        result[i] = x[0]
    return result


def apply_butterworth(signal, t, order, cutoff):
    """巴特沃斯低通滤波 (零相位)."""
    nyquist = 0.5  # stock: bar index dt≈1, fs=1, nyquist=0.5
    if cutoff >= nyquist:
        cutoff = nyquist * 0.99
    sos = butter(order, cutoff / nyquist, btype="low", output="sos")
    return sosfiltfilt(sos, signal)


def apply_gaussian(signal, t, sigma):
    """高斯滤波 (scipy.ndimage)."""
    return gaussian_filter1d(signal, sigma)


def apply_median(signal, t, window):
    """中值滤波."""
    if window % 2 == 0:
        window += 1
    return medfilt(signal, kernel_size=window)


def apply_lowess(signal, t, frac):
    """LOWESS 局部加权回归平滑."""
    result = lowess(signal, t, frac=frac, return_sorted=False)
    # return_sorted=False returns a 1-D array of smoothed y-values
    return result


# ---------------------------------------------------------------------------
# Filter registry
# Each entry: (name, function, {param_name: (label, min, max, step, default)})
# ---------------------------------------------------------------------------
FILTERS = {
    "sma": {
        "name": "简单移动平均 (SMA)",
        "func": apply_sma,
        "params": {"window": ("窗口大小", 3, 101, 2, 11)},
    },
    "ema": {
        "name": "指数移动平均 (EMA)",
        "func": apply_ema,
        "params": {"span": ("跨度", 2, 100, 1, 10)},
    },
    "wma": {
        "name": "加权移动平均 (WMA)",
        "func": apply_wma,
        "params": {"window": ("窗口大小", 3, 101, 2, 11)},
    },
    "alma": {
        "name": "Arnaud Legoux 移动平均 (ALMA)",
        "func": apply_alma,
        "params": {
            "window": ("窗口大小", 3, 101, 2, 21),
            "offset": ("偏移量", 0.0, 1.0, 0.01, 0.85),
            "sigma": ("标准差", 1.0, 20.0, 0.1, 6.0),
        },
    },
    "savgol": {
        "name": "Savitzky-Golay 滤波",
        "func": apply_savgol,
        "params": {
            "window": ("窗口大小", 5, 101, 2, 21),
            "order": ("多项式阶数", 1, 5, 1, 2),
        },
    },
    "kalman": {
        "name": "卡尔曼滤波",
        "func": apply_kalman,
        "params": {
            "Q": ("过程噪声 Q", 0.001, 1.0, 0.001, 0.01),
            "R": ("测量噪声 R", 0.01, 10.0, 0.01, 1.0),
        },
    },
    "butterworth": {
        "name": "巴特沃斯低通滤波",
        "func": apply_butterworth,
        "params": {
            "order": ("滤波器阶数", 1, 8, 1, 4),
            "cutoff": ("截止频率 (Hz)", 1.0, 45.0, 0.5, 10.0),
        },
    },
    "gaussian": {
        "name": "高斯滤波",
        "func": apply_gaussian,
        "params": {"sigma": ("标准差 Sigma", 0.5, 20.0, 0.1, 3.0)},
    },
    "median": {
        "name": "中值滤波",
        "func": apply_median,
        "params": {"window": ("窗口大小", 3, 101, 2, 5)},
    },
    "lowess": {
        "name": "LOWESS 平滑",
        "func": apply_lowess,
        "params": {"frac": ("平滑比例", 0.01, 0.5, 0.01, 0.1)},
    },
}


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------
def compute_metrics(clean, noisy, filtered):
    """计算 6 项滤波质量指标."""
    valid = ~np.isnan(filtered) & ~np.isnan(clean) & ~np.isnan(noisy)
    c, n, f = clean[valid], noisy[valid], filtered[valid]
    if len(c) < 3:
        return {
            "mse": np.nan, "rmse": np.nan, "mae": np.nan,
            "snr_imp": np.nan, "lag": 0, "roughness": np.nan,
        }

    residuals = f - c
    mse = float(np.mean(residuals ** 2))
    rmse = float(np.sqrt(mse))
    mae = float(np.mean(np.abs(residuals)))

    noise_var = float(np.var(n - c))
    err_var = float(np.var(residuals))
    snr_imp = 10 * np.log10(noise_var / err_var) if err_var > 1e-12 else 99.0

    # Lag via cross-correlation (peak of corr(filtered, noisy))
    crosscorr = np.correlate(f - np.mean(f), n - np.mean(n), mode="full")
    lag = int(np.argmax(crosscorr) - (len(f) - 1))

    # Roughness: sum of squared second differences
    roughness = float(np.sum(np.diff(f, 2) ** 2)) if len(f) > 2 else 0.0

    return {
        "mse": mse, "rmse": rmse, "mae": mae,
        "snr_imp": snr_imp, "lag": lag, "roughness": roughness,
    }


# ---------------------------------------------------------------------------
# Helper: render a slider for each filter parameter
# ---------------------------------------------------------------------------
def _render_param_slider(label, pmin, pmax, pstep, pdefault, key_suffix="", container=None):
    """Render an st.slider with appropriate numeric format.
    If container is None, renders in sidebar (backward compat).
    Pass container=st to render inline in the current column context.
    """
    ctx = container if container is not None else st.sidebar
    key = f"{label}_{key_suffix}" if key_suffix else None
    if isinstance(pstep, int):
        return ctx.slider(label, pmin, pmax, pdefault, pstep, key=key)
    fmt = "%.3f" if pstep < 0.01 else "%.2f"
    return ctx.slider(label, pmin, pmax, pdefault, pstep, format=fmt, key=key)


# ---------------------------------------------------------------------------
# Plotly cross-subplot crosshair helper
# ---------------------------------------------------------------------------
def _render_plotly(fig, height=750):
    """将 Plotly 图表渲染为带跨子图十字光标+统一 tooltip 的 HTML 组件。

    解决 Plotly 原生 hovermode="x unified" 只能聚合当前子图 trace 的限制：
    JS 在 hover 时遍历 gd.data 中全部 trace，查找 hover x 处的 y 值，
    渲染为自定义 tooltip，实现"光标位置看到所有 y 轴数据"。

    关键：pio.to_json 将 numpy 数组编码为 base64 bdata，JS 端无法通过
    trace.x[idx] 索引访问。这里手动构建 figure JSON，将 x/y 数据替换为
    普通 JSON 数组，确保 gd.data[t].x[idx] / y[idx] 在浏览器中可用。
    """
    # 构建 figure dict，手动替换 trace 中的 x/y bdata 为 Python list
    fig_dict = {"data": [], "layout": fig.layout.to_plotly_json()}
    for trace in fig.data:
        tr = trace.to_plotly_json()
        if isinstance(tr.get("x"), dict) and "bdata" in tr["x"]:
            tr["x"] = trace.x.tolist()
        if isinstance(tr.get("y"), dict) and "bdata" in tr["y"]:
            tr["y"] = trace.y.tolist()
        fig_dict["data"].append(tr)

    class _NpEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            return super().default(obj)

    figure_json = json.dumps(fig_dict, cls=_NpEncoder)
    div_id = f"plot-{uuid.uuid4().hex[:8]}"

    html = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ width: 100%; height: 100%; overflow: hidden; }}
#{div_id} {{ width: 100%; height: 100%; }}
/* 隐藏 Plotly 原生 hover 标签和 spike，由自定义 tooltip + 十字光标替代 */
g.hovertext {{ visibility: hidden !important; }}
.spikeline {{ visibility: hidden !important; }}
#custom-tooltip {{
    display: none;
    position: fixed;
    z-index: 9999;
    background: rgba(30, 30, 44, 0.94);
    color: #e0e0e0;
    padding: 8px 12px;
    border-radius: 6px;
    border: 1px solid rgba(200, 200, 200, 0.25);
    font-family: monospace;
    font-size: 12px;
    line-height: 1.6;
    pointer-events: none;
    white-space: nowrap;
}}
</style>
</head>
<body>
<div id="{div_id}"></div>
<div id="custom-tooltip"></div>
<script>
(function() {{
    var figure = {figure_json};
    var config = {{
        responsive: true,
        displayModeBar: true,
        displaylogo: false,
        modeBarButtonsToRemove: ['lasso2d', 'select2d']
    }};
    Plotly.newPlot('{div_id}', figure.data, figure.layout, config).then(function(gd) {{
        gd.on('plotly_hover', function(evt) {{
            if (!evt.points || evt.points.length === 0) return;
            var xv = evt.points[0].x;

            // 1) Update crosshair line
            var shapes = gd.layout.shapes || [];
            var update = {{}};
            for (var i = 0; i < shapes.length; i++) {{
                if (shapes[i].yref === 'paper' || shapes[i].yref === 'y domain') {{
                    update['shapes[' + i + '].x0'] = xv;
                    update['shapes[' + i + '].x1'] = xv;
                    update['shapes[' + i + '].visible'] = true;
                }}
            }}
            if (Object.keys(update).length > 0) {{
                Plotly.relayout(gd, update);
            }}

            // 2) Find nearest x-index (all traces share the same t array)
            var xArr = gd.data[0].x;
            if (!xArr || xArr.length === 0) return;
            var idx = 0;
            for (var i = 0; i < xArr.length; i++) {{
                if (Math.abs(xArr[i] - xv) < Math.abs(xArr[idx] - xv)) idx = i;
            }}

            // 3) Collect y-values from ALL traces across ALL subplots
            var lines = [];
            lines.push('<b>x = ' + xv.toFixed(4) + '</b>');
            for (var t = 0; t < gd.data.length; t++) {{
                var trace = gd.data[t];
                var yArr = trace.y;
                if (!yArr || idx >= yArr.length) continue;
                var yVal = yArr[idx];
                if (yVal === null || yVal === undefined || isNaN(yVal)) continue;
                // Use trace color as indicator dot
                var color = trace.line ? trace.line.color : '#ccc';
                var name = trace.name || ('trace ' + t);
                lines.push('<span style="color:' + color + '">●</span> ' + name + ': ' + yVal.toFixed(5));
            }}

            // 4) Show custom tooltip
            var tip = document.getElementById('custom-tooltip');
            if (tip && lines.length > 1) {{
                tip.innerHTML = lines.join('<br>');
                tip.style.display = 'block';
            }}
        }});

        gd.on('plotly_unhover', function() {{
            // Hide crosshair
            var shapes = gd.layout.shapes || [];
            var update = {{}};
            for (var i = 0; i < shapes.length; i++) {{
                if (shapes[i].yref === 'paper' || shapes[i].yref === 'y domain') {{
                    update['shapes[' + i + '].visible'] = false;
                }}
            }}
            if (Object.keys(update).length > 0) {{
                Plotly.relayout(gd, update);
            }}
            // Hide custom tooltip
            var tip = document.getElementById('custom-tooltip');
            if (tip) tip.style.display = 'none';
        }});

        // Follow mouse
        document.getElementById('{div_id}').addEventListener('mousemove', function(e) {{
            var tip = document.getElementById('custom-tooltip');
            if (tip && tip.style.display === 'block') {{
                tip.style.left = (e.clientX + 18) + 'px';
                tip.style.top = (e.clientY - 10) + 'px';
            }}
        }});
    }});
}})();
</script>
</body>
</html>""".format(div_id=div_id, figure_json=figure_json)

    return st.components.v1.html(html, height=height)


# ---------------------------------------------------------------------------
# Stock data fetcher (module-level for @st.cache_data)
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=300)
def _fetch_stock(market, code, tf, n_pts):
    if market == "A股(沪深)":
        suffix = ".SS" if code[0] == "6" else ".SZ"
        full = code + suffix
    elif market == "港股 HK":
        full = code.zfill(4) + ".HK"
    else:
        full = code.upper()

    tf_map = {"1分钟": "1m", "5分钟": "5m", "15分钟": "15m", "60分钟": "1h",
               "日线": "1d", "周线": "1wk", "月线": "1mo", "季线": "3mo"}
    interval = tf_map[tf]
    # yfinance period: standard strings only
    if tf == "1分钟":
        period = "7d"
    elif tf in ("5分钟", "15分钟"):
        period = "60d"
    elif tf == "60分钟":
        period = "60d"
    elif tf == "日线":
        wanted = max(n_pts * 2, 10)
        if wanted <= 30:      period = "1mo"
        elif wanted <= 90:    period = "3mo"
        elif wanted <= 180:   period = "6mo"
        elif wanted <= 365:   period = "1y"
        elif wanted <= 730:   period = "2y"
        elif wanted <= 1825:  period = "5y"
        elif wanted <= 3650:  period = "10y"
        else:                 period = "max"
    elif tf == "周线":
        wanted = max(n_pts * 5, 52)
        if wanted <= 52:      period = "1y"
        elif wanted <= 104:   period = "2y"
        elif wanted <= 260:   period = "5y"
        elif wanted <= 520:   period = "10y"
        else:                 period = "max"
    elif tf == "月线":
        wanted = max(n_pts * 1.5, 12)
        if wanted <= 12:      period = "1y"
        elif wanted <= 24:    period = "2y"
        elif wanted <= 60:    period = "5y"
        elif wanted <= 120:   period = "10y"
        else:                 period = "max"
    else:  # 季线
        period = "max"

    data = yf.download(full, period=period, interval=interval, progress=False)
    if data.empty:
        return None, None, None, full, f"无数据: {full}"

    # yfinance >=0.2.x returns MultiIndex columns even for single ticker,
    # e.g. ('Close','AAPL'). Flatten to simple column names first.
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.droplevel(1)

    # Drop rows where Close is NaN, then trim to last n_pts
    data = data[data["Close"].notna()]
    if len(data) > n_pts:
        data = data.iloc[-n_pts:]
    n = len(data)
    close = data["Close"].values.ravel()
    t_arr = np.arange(n, dtype=float)
    return t_arr, close, data, full, None


# ---------------------------------------------------------------------------
# Schmitt Trigger computation (ref: 多周期趋势策略V2_优化4 §二, chart ④⑥)
# Inputs: v (velocity = d(filtered)/dt) as momentum, a (d²/dt²) as acceleration
# ---------------------------------------------------------------------------
def _schmitt_trigger(v, a, ewma_span=60, k_eps=0.15, sigma_min=0.05):
    """Schmitt trigger: adaptive deadband on acceleration (a),
    with velocity (v) as direction constraint.

    - v → momentum (物理意义: 趋势速度, 类比文档 x_t)
    - a → acceleration (物理意义: 趋势加速, 类比文档 a_t)
    - ε_t = k_ε · max(σ_t(v), σ_min)  — 自适应死区基于 v 的波动率
    - Sig_t: a>ε AND v>0 → +1(多); a<-ε AND v<0 → -1(空); else 0(观望)
    """
    n = len(v)
    if n < ewma_span:
        return None

    # EWMA volatility of v → σ_t(v)
    alpha = 2.0 / (ewma_span + 1)
    mu_v = np.full(n, np.nan)
    sigma_v = np.full(n, np.nan)
    mu_v[0] = v[0]
    sigma_v[0] = 0.0
    for i in range(1, n):
        mu_v[i] = alpha * v[i] + (1 - alpha) * mu_v[i - 1]
        sigma_v[i] = np.sqrt(
            alpha * (v[i] - mu_v[i]) ** 2 + (1 - alpha) * sigma_v[i - 1] ** 2)

    # Adaptive deadband
    eps_t = k_eps * np.maximum(sigma_v, sigma_min)

    # Schmitt trigger with hysteresis
    sig_t = np.zeros(n, dtype=int)
    dur_t = np.zeros(n, dtype=int)
    current_state = 0
    current_dur = 0
    for i in range(n):
        if np.isnan(a[i]) or np.isnan(v[i]):
            sig_t[i] = current_state
            current_dur += 1
        else:
            if current_state == 0:
                if a[i] > eps_t[i] and v[i] > 0:
                    current_state = 1; current_dur = 1
                elif a[i] < -eps_t[i] and v[i] < 0:
                    current_state = -1; current_dur = 1
                else:
                    current_dur += 1
            elif current_state == 1:
                if a[i] < -eps_t[i]:
                    current_state = 0; current_dur = 1
                else:
                    current_dur += 1
            else:  # -1
                if a[i] > eps_t[i]:
                    current_state = 0; current_dur = 1
                else:
                    current_dur += 1
        sig_t[i] = current_state
        dur_t[i] = current_dur

    return {"mu_v": mu_v, "sigma_v": sigma_v,
            "eps": eps_t, "sig": sig_t, "dur": dur_t}


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Split render: params (top 2x2) + chart (bottom 2x2)
# ---------------------------------------------------------------------------
DEFAULT_TFS = ["日线", "60分钟", "15分钟", "5分钟"]
ALL_TFS = ["1分钟","5分钟","15分钟","60分钟","日线","周线","月线","季线"]

def _render_params(key, filter_id, dual, filter_id2, tf_default):
    """Ultra-compact parameter panel. Returns config dict."""
    cfg = {"_fid": filter_id, "_dual": dual, "_fid2": filter_id2}

    # Row 1: [周期] [N] [Schmitt☑] [k_ε ▬] [σ_min ▬] [N_EWMA ▬]
    c = st.columns([1.4, 0.9, 0.6, 1.1, 1.1, 1.1])
    with c[0]:
        cfg["tf"] = st.selectbox("周期", ALL_TFS, index=ALL_TFS.index(tf_default),
            key=f"{key}_tf", label_visibility="collapsed")
    with c[1]:
        cfg["n_pts"] = st.slider("N", 20, 300, 120, 10, key=f"{key}_n", label_visibility="collapsed")
    with c[2]:
        cfg["show_sch"] = st.checkbox("施密特", value=True, key=f"{key}_sch")
    cfg["ke"]=0.15; cfg["sm"]=0.05; cfg["ew"]=60
    if cfg["show_sch"]:
        with c[3]: cfg["ke"] = st.slider("k_ε",0.01,0.50,0.15,0.05,key=f"{key}_ke",
            help="灵敏度系数,越小越敏感. ε_t=k_ε·max(σ_t(v),σ_min)")
        with c[4]: cfg["sm"] = st.slider("σ_min",0.01,0.20,0.05,0.02,key=f"{key}_sm",
            help="地板保护,防止低波动下ε_t→0")
        with c[5]: cfg["ew"] = st.slider("N_EWMA",10,120,60,10,key=f"{key}_ew",
            help="EWMA周期,α=2/(N+1),越大越平滑")

    # Row 2: filter 1 params
    sf = FILTERS[filter_id]; cfg["pv"] = {}
    f1 = list(sf["params"].items())
    fc1 = st.columns([1]*len(f1) + [0.25])
    for j, (pn, sp) in enumerate(f1):
        with fc1[j]:
            cfg["pv"][pn] = _render_param_slider(*sp, key_suffix=f"{key}_f1_{filter_id}", container=st)
    with fc1[-1]:
        cfg["fc"] = st.color_picker("", "#00d4aa", key=f"{key}_fc", label_visibility="collapsed")

    # Row 3 (optional): filter 2 params
    if dual and filter_id2:
        sf2 = FILTERS[filter_id2]; cfg["pv2"] = {}
        f2 = list(sf2["params"].items())
        fc2 = st.columns([1]*len(f2) + [0.25])
        for j, (pn, sp) in enumerate(f2):
            with fc2[j]:
                cfg["pv2"][pn] = _render_param_slider(*sp, key_suffix=f"{key}_f2_{filter_id2}", container=st)
        with fc2[-1]:
            cfg["fc2"] = st.color_picker("", "#ff6b6b", key=f"{key}_fc2", label_visibility="collapsed")
    else:
        cfg["pv2"] = {}; cfg["fc2"] = "#ff6b6b"
    return cfg


def _render_chart(market, ticker_code, cfg, key, compact=True):
    """Fetch data + render multi-subplot figure from config."""
    t, noisy, ohlc, ticker_full, err = _fetch_stock(market, ticker_code, cfg["tf"], cfg["n_pts"])
    if err: st.error(err); return

    sf = FILTERS[cfg["_fid"]]
    try:
        filtered = sf["func"](noisy, t, **cfg["pv"])
        filtered = np.asarray(filtered, dtype=float).ravel()
    except Exception:
        filtered = np.full_like(noisy, np.nan)
    filtered2 = None
    if cfg["_dual"] and cfg["_fid2"] and cfg["pv2"]:
        try:
            sf2 = FILTERS[cfg["_fid2"]]
            filtered2 = sf2["func"](noisy, t, **cfg["pv2"])
            filtered2 = np.asarray(filtered2, dtype=float).ravel()
        except Exception:
            filtered2 = np.full_like(noisy, np.nan)

    # Schmitt
    schmitt = None
    if cfg["show_sch"] and not np.all(np.isnan(filtered)):
        _v = np.gradient(filtered, t); _a = np.gradient(_v, t)
        schmitt = _schmitt_trigger(_v, _a, ewma_span=cfg["ew"], k_eps=cfg["ke"], sigma_min=cfg["sm"])

    # Build figure (same as before, compact)
    has_s = schmitt is not None
    rows = 5 if has_s else 4
    if has_s:
        rh = [0.28,0.14,0.18,0.18,0.22]
        titles = ("价格&滤波","残差","速度v","a&±ε","Sig_t")
        mr=1;rr=2;vr=3;sar=4;ssr=5;ar=None
    else:
        rh=[0.40,0.18,0.20,0.22]; titles=("价格&滤波","残差","速度v","加速度a")
        mr=1;rr=2;vr=3;ar=4;sar=ssr=None
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True,
                        vertical_spacing=0.02, row_heights=rh, subplot_titles=titles)
    fig.add_trace(go.Candlestick(x=t, open=ohlc["Open"].values.ravel(),
        high=ohlc["High"].values.ravel(), low=ohlc["Low"].values.ravel(),
        close=ohlc["Close"].values.ravel(), name="K",
        increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
        showlegend=False), row=mr, col=1)
    fig.add_trace(go.Scatter(x=t, y=noisy, mode="lines", name="收盘",
        line=dict(color="#5f6c80", width=1.0)), row=mr, col=1)
    if not np.all(np.isnan(filtered)):
        fig.add_trace(go.Scatter(x=t, y=filtered, mode="lines", name="滤波",
            line=dict(color=cfg["fc"], width=2.0)), row=mr, col=1)
    if cfg["_dual"] and filtered2 is not None and not np.all(np.isnan(filtered2)):
        fig.add_trace(go.Scatter(x=t, y=filtered2, mode="lines", name="滤波2",
            line=dict(color=cfg["fc2"], width=2.0)), row=mr, col=1)
    if not np.all(np.isnan(filtered)):
        fig.add_trace(go.Scatter(x=t, y=filtered-noisy, mode="lines", name="残差",
            line=dict(color="#5f6c80", width=1.0, dash="dot")), row=rr, col=1)
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5, row=rr, col=1)
        vel=np.gradient(filtered,t); acc=np.gradient(vel,t)
        fig.add_trace(go.Scatter(x=t, y=vel, mode="lines", name="v",
            line=dict(color=cfg["fc"], width=1.5)), row=vr, col=1)
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5, row=vr, col=1)
    if has_s:
        eps=schmitt["eps"]; sig=schmitt["sig"]
        fig.add_trace(go.Scatter(x=list(t)+list(t[::-1]), y=list(eps)+list(-eps[::-1]),
            fill="toself", fillcolor="rgba(128,128,128,0.06)", line=dict(width=0),
            name="±ε", hoverinfo="skip"), row=sar, col=1)
        fig.add_trace(go.Scatter(x=t, y=eps, mode="lines", name="+ε",
            line=dict(color="#f85149", width=0.8, dash="dash"), showlegend=False), row=sar, col=1)
        fig.add_trace(go.Scatter(x=t, y=-eps, mode="lines", name="-ε",
            line=dict(color="#f85149", width=0.8, dash="dash")), row=sar, col=1)
        fig.add_trace(go.Scatter(x=t, y=schmitt["sigma_v"], mode="lines", name="σ(v)",
            line=dict(color="#a371f7", width=1.0, dash="dot")), row=sar, col=1)
        fig.add_trace(go.Scatter(x=t, y=acc, mode="lines", name="a",
            line=dict(color="#d2991d", width=1.5)), row=sar, col=1)
        fig.add_hline(y=0, line_dash="solid", line_color="gray", opacity=0.3, row=sar, col=1)
        fig.add_trace(go.Scatter(x=t, y=sig.astype(float), mode="lines", name="Sig",
            line=dict(color="#58a6ff", width=2, shape="hv")), row=ssr, col=1)
        for state,cl in [(1,"rgba(63,185,80,0.06)"),(-1,"rgba(248,81,73,0.06)")]:
            msk=sig==state
            if msk.any():
                fig.add_trace(go.Scatter(x=t[msk], y=np.where(msk,state,0),
                    mode="lines", line=dict(width=0), fill="tozeroy",
                    fillcolor=cl, showlegend=False, hoverinfo="skip"), row=ssr, col=1)
    if ar is not None and not np.all(np.isnan(filtered)):
        fig.add_trace(go.Scatter(x=t, y=acc, mode="lines", name="a",
            line=dict(color="#ffa502", width=1.5)), row=ar, col=1)
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5, row=ar, col=1)
    fig.add_shape(type="line", x0=0, x1=0, y0=0, y1=1, xref="x", yref="paper",
                   line=dict(color="rgba(200,200,200,0.4)", width=1, dash="dot"), visible=False)
    fh = (540 if has_s else 420) if compact else (880 if has_s else 700)
    fig.update_layout(template="plotly_dark", height=fh,
        margin=dict(l=10,r=10,t=25,b=10), hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=9)))
    fig.update_xaxes(title_text="", row=rows, col=1)
    fig.update_xaxes(rangeslider_visible=False, row=1, col=1)
    fig.update_yaxes(title_text="价格", row=mr, col=1)
    fig.update_yaxes(title_text="残差", row=rr, col=1)
    fig.update_yaxes(title_text="速度", row=vr, col=1)
    if has_s:
        fig.update_yaxes(title_text="a±ε", row=sar, col=1)
        fig.update_yaxes(title_text="Sig", row=ssr, col=1,
                          tickvals=[-1,0,1], ticktext=["空","观","多"], range=[-1.5,1.5])
    if ar is not None:
        fig.update_yaxes(title_text="加速度", row=ar, col=1)
    _render_plotly(fig, height=fh+30)
    rough = float(np.sum(np.diff(filtered,2)**2)) if len(filtered)>2 else 0.0
    c1,c2,c3 = st.columns(3)
    c1.caption(f"{ticker_full}·{cfg['tf']}  |  ¥{noisy[-1]:.2f}")
    c2.caption(f"σ={noisy.std():.2f}  平滑={rough:.1f}")
    c3.caption(f"{len(t)} 点")


# =====================================================================
def main():
    st.sidebar.title("多周期股票滤波分析")
    market = st.sidebar.radio("市场", ["美股 US","A股(沪深)","港股 HK"],
                               horizontal=True, key="market")
    ticker_code = st.sidebar.text_input("股票代码", value="AAPL", key="ticker").strip()
    st.sidebar.markdown("---")
    filter_id = st.sidebar.selectbox("滤波器", list(FILTERS.keys()),
        format_func=lambda x: FILTERS[x]["name"], key="global_f")
    dual = st.sidebar.checkbox("双滤波对比", value=False, key="global_dual")
    filter_id2 = None
    if dual:
        filter_id2 = st.sidebar.selectbox("滤波器 2", list(FILTERS.keys()),
            format_func=lambda x: FILTERS[x]["name"], key="global_f2")

    # ---- Pass 1: Top 2x2 parameter panels ----
    configs = []
    for row_idx in range(2):
        c1, c2 = st.columns(2)
        for col_idx, col in enumerate([c1, c2]):
            i = row_idx * 2 + col_idx
            with col:
                    st.caption(f"视图{i+1} · {DEFAULT_TFS[i]}")
                    cfg = _render_params(f"v{i}", filter_id, dual, filter_id2, DEFAULT_TFS[i])
                    configs.append(cfg)

    # ---- Pass 2: Bottom 2x2 chart views ----
    
    for row_idx in range(2):
        c1, c2 = st.columns(2)
        for col_idx, col in enumerate([c1, c2]):
            i = row_idx * 2 + col_idx
            with col:
                _render_chart(market, ticker_code, configs[i], f"v{i}", compact=True)
if __name__ == "__main__":
    main()
