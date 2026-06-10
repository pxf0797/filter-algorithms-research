"""
Streamlit 滤波算法交互式对比分析工具
=====================================
基于 scipy/numpy 的信号滤波器对比可视化平台。
支持 5 种测试信号 × 10 种滤波器算法，提供时域波形、残差分析、多项质量指标。
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

SAMPLE_RATE = 100  # Hz, fixed across all signals


# ---------------------------------------------------------------------------
# Signal generation (cached)
# ---------------------------------------------------------------------------
@st.cache_data
def generate_signals(n_points: int = 1000, sample_rate: int = 100, seed: int = 42):
    """生成 5 种测试信号，返回 {signal_id: {...}} 字典。"""
    rng = np.random.default_rng(seed)
    t = np.arange(n_points) / sample_rate
    datasets = {}

    # 1) 正弦波 + AWGN
    clean = np.sin(2 * np.pi * 5 * t)
    noisy = clean + rng.normal(0, 0.25, n_points)
    datasets["sinusoid"] = {
        "t": t, "clean": clean, "noisy": noisy,
        "name": "正弦波 + 高斯噪声",
        "desc": "5Hz 正弦信号，SNR ≈ 3dB",
    }

    # 2) 阶跃信号
    clean = np.zeros(n_points)
    clean[n_points // 2:] = 1.0
    noisy = clean + rng.normal(0, 0.15, n_points)
    datasets["step"] = {
        "t": t, "clean": clean, "noisy": noisy,
        "name": "阶跃信号",
        "desc": "中点 0→1 跳变，测试瞬态响应",
    }

    # 3) 趋势 + 季节性 + AR(1) 噪声
    clean = 0.002 * np.arange(n_points) + 0.3 * np.sin(2 * np.pi * 3 * t)
    ar_noise = np.zeros(n_points)
    for i in range(1, n_points):
        ar_noise[i] = 0.7 * ar_noise[i - 1] + rng.normal(0, 0.1)
    noisy = clean + ar_noise
    datasets["trend_seasonal"] = {
        "t": t, "clean": clean, "noisy": noisy,
        "name": "趋势 + 季节性",
        "desc": "线性趋势 + 3Hz 周期 + AR(1) 噪声",
    }

    # 4) 脉冲信号（高斯脉冲在 t=1s）
    clean = 0.2 * np.sin(2 * np.pi * 2 * t)
    pulse = 2.0 * np.exp(-0.5 * ((t - 1.0) / 0.02) ** 2)
    clean = clean + pulse
    noisy = clean + rng.normal(0, 0.1, n_points)
    datasets["impulse"] = {
        "t": t, "clean": clean, "noisy": noisy,
        "name": "脉冲信号",
        "desc": "t=1s 高斯脉冲 + 正弦基线，测试峰值保持",
    }

    # 5) Chirp 扫频 1→20Hz
    freq = np.linspace(1, 20, n_points)
    phase = 2 * np.pi * np.cumsum(freq) / sample_rate
    clean = np.sin(phase)
    noisy = clean + rng.normal(0, 0.02, n_points)
    datasets["chirp"] = {
        "t": t, "clean": clean, "noisy": noisy,
        "name": "Chirp 扫频",
        "desc": "1→20Hz 线性扫频，测试频率响应",
    }

    # 6) Random walk (Brownian motion)
    steps = rng.normal(0, 0.05, n_points)
    clean = np.cumsum(steps)
    clean -= clean[0]
    noisy = clean + rng.normal(0, 0.03, n_points)
    datasets["random_walk"] = {
        "t": t, "clean": clean, "noisy": noisy,
        "name": "随机游走",
        "desc": "布朗运动轨迹，测试非平稳信号滤波",
    }

    # 7) Multi-tone composite
    clean = (
        np.sin(2 * np.pi * 2 * t)
        + 0.5 * np.sin(2 * np.pi * 7 * t)
        + 0.25 * np.sin(2 * np.pi * 15 * t)
    )
    noisy = clean + rng.normal(0, 0.12, n_points)
    datasets["multitone"] = {
        "t": t, "clean": clean, "noisy": noisy,
        "name": "多频叠加",
        "desc": "2+7+15Hz 三频复合信号，测试频率选择性",
    }

    # 8) Sparse spikes
    clean = np.zeros(n_points)
    spike_positions = rng.choice(n_points, size=8, replace=False)
    for pos in spike_positions:
        clean[pos] = rng.choice([-1, 1]) * rng.uniform(0.8, 2.0)
    noisy = clean + rng.normal(0, 0.08, n_points)
    datasets["spikes"] = {
        "t": t, "clean": clean, "noisy": noisy,
        "name": "稀疏脉冲",
        "desc": "8 个随机位置脉冲 + 低噪底，测试离群值保持/抑制",
    }

    # 9) Triangle wave
    period = int(sample_rate / 3)  # 3Hz triangle
    phase = np.arange(n_points) % period
    clean = 2 * np.abs(2 * phase / period - 1) - 1
    noisy = clean + rng.normal(0, 0.15, n_points)
    datasets["triangle"] = {
        "t": t, "clean": clean, "noisy": noisy,
        "name": "三角波",
        "desc": "3Hz 三角波 + 高斯噪声，测试谐波响应",
    }

    # 10) Exponential decay + oscillation
    decay_env = np.exp(-t / 2.0)
    clean = decay_env * np.sin(2 * np.pi * 4 * t)
    noisy = clean + rng.normal(0, 0.06, n_points)
    datasets["damped_osc"] = {
        "t": t, "clean": clean, "noisy": noisy,
        "name": "阻尼振荡",
        "desc": "指数衰减包络 × 4Hz 正弦，测试瞬态 + 时变幅度",
    }

    return datasets


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
    nyquist = SAMPLE_RATE / 2.0
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
def _render_param_slider(label, pmin, pmax, pstep, pdefault, key_suffix=""):
    """Render an st.slider with appropriate numeric format."""
    key = f"{label}_{key_suffix}" if key_suffix else None
    if isinstance(pstep, int):
        return st.sidebar.slider(label, pmin, pmax, pdefault, pstep, key=key)
    fmt = "%.3f" if pstep < 0.01 else "%.2f"
    return st.sidebar.slider(label, pmin, pmax, pdefault, pstep, format=fmt, key=key)


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
    # Adjust period per timeframe: shorter bars need less calendar time
    intraday = tf in ("1分钟", "5分钟", "15分钟", "60分钟")
    if intraday:
        period = "7d"   # yfinance intraday max ~7 days for 1m, ~60 days for others
    elif tf == "日线":
        period = f"{max(n_pts * 2, 10)}d"
    elif tf == "周线":
        period = f"{max(n_pts * 5, 52)}wk"
    elif tf == "月线":
        period = f"{max(n_pts * 21, 730)}d"
    else:  # 季线
        period = f"{max(n_pts * 63, 2190)}d"

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
def main():
    # ======================== SIDEBAR ========================
    st.sidebar.title("滤波算法交互式对比分析工具")

    # ---- Data source ----
    st.sidebar.markdown("#### 数据源")
    data_source = st.sidebar.radio("", ["合成信号", "股票数据"], horizontal=True,
                                    key="data_source")

    # ==================== STOCK MODE ====================
    if data_source == "股票数据":
        st.sidebar.markdown("---")
        market = st.sidebar.radio("市场", ["美股 US", "A股(沪深)", "港股 HK"],
                                   horizontal=True, key="stock_market")
        ticker_code = st.sidebar.text_input("股票代码", value="AAPL",
                                             key="stock_ticker").strip()
        timeframe = st.sidebar.radio("周期",
            ["1分钟", "5分钟", "15分钟", "60分钟", "日线", "周线", "月线", "季线"],
            horizontal=False, index=4, key="stock_tf")
        n_points = st.sidebar.number_input("数据点数", 20, 500, 120, 10, key="stock_n")

        fetch_clicked = st.sidebar.button("获取股票数据", type="primary",
                                           use_container_width=True)

        # Fetch or use cached
        if fetch_clicked:
            t, noisy, ohlc_data, ticker_full, err = _fetch_stock(
                market, ticker_code, timeframe, n_points)
            if err:
                st.sidebar.error(err)
                st.session_state.stock_data = None
            else:
                st.session_state.stock_data = (t, noisy, ohlc_data, ticker_full, timeframe)
        else:
            # Use persisted data if available
            pass

        stock = st.session_state.get("stock_data")
        if stock is None:
            st.info("输入股票代码后点击「获取股票数据」")
            st.stop()

        t, noisy, ohlc_data, ticker_full, tf_label = stock
        clean = None  # no clean signal for stocks
        st.header(f"{ticker_full}  ·  {tf_label}")
        st.caption(f"数据点数: {len(t)}  |  收盘价范围: ¥{noisy.min():.2f} – ¥{noisy.max():.2f}")
        n_points_syn = len(t)  # not used
        datasets = None
        dataset_id = None
        show_clean = False
        show_noisy = True
        noisy_label = "收盘价"
        is_stock = True

    # ==================== SYNTHETIC MODE ====================
    else:
        n_points_syn = st.sidebar.radio("数据长度", [200, 1000], horizontal=True)
        datasets = generate_signals(n_points=n_points_syn)
        dataset_id = st.sidebar.selectbox(
            "数据集", options=list(datasets.keys()),
            format_func=lambda x: datasets[x]["name"])
        is_stock = False

    # ==================== COMMON: FILTER SELECTION ====================
    filter_id = st.sidebar.selectbox(
        "滤波器", options=list(FILTERS.keys()),
        format_func=lambda x: FILTERS[x]["name"])

    selected_filter = FILTERS[filter_id]
    param_values = {}
    for pname, spec in selected_filter["params"].items():
        param_values[pname] = _render_param_slider(*spec, key_suffix=f"f1_{filter_id}")

    # ---- Second filter (optional comparison) ----
    st.sidebar.markdown("---")
    dual_mode = st.sidebar.checkbox("对比双滤波器", value=False)
    if dual_mode:
        filter_id2 = st.sidebar.selectbox(
            "滤波器 2", options=list(FILTERS.keys()),
            format_func=lambda x: FILTERS[x]["name"], key="filter2")
        selected_filter2 = FILTERS[filter_id2]
        param_values2 = {}
        for pname, spec in selected_filter2["params"].items():
            param_values2[pname] = _render_param_slider(*spec, key_suffix=f"f2_{filter_id2}")
    else:
        selected_filter2 = None
        param_values2 = {}

    # Display toggles
    st.sidebar.markdown("---")
    st.sidebar.markdown("#### 显示选项")
    if is_stock:
        show_noisy = True   # always show close price
        show_clean = False
        noisy_label = "收盘价"
    else:
        show_noisy = st.sidebar.checkbox("含噪信号", value=True)
        show_clean = st.sidebar.checkbox("干净信号", value=True)
        noisy_label = "含噪信号"
    show_filtered = st.sidebar.checkbox("滤波输出", value=True)
    filter_color = st.sidebar.color_picker("滤波输出颜色", "#00d4aa")
    if dual_mode:
        filter_color2 = st.sidebar.color_picker("滤波输出 2 颜色", "#ff6b6b")
    else:
        filter_color2 = "#ff6b6b"

    # Schmitt trigger toggle + params (stock mode only)
    show_schmitt = False
    schmitt_k_eps = 0.15; schmitt_smin = 0.05; schmitt_ewma = 60
    if is_stock:
        show_schmitt = st.sidebar.checkbox("施密特触发器", value=True,
            help="v=动量(一阶导) a=加速度(二阶导) → 自适应死区 → 迟滞信号")
        if show_schmitt:
            schmitt_k_eps = st.sidebar.slider(
                "k_ε 灵敏度系数", 0.01, 0.50, 0.15, 0.01,
                help="无量纲,越小越敏感。ε_t=k_ε·max(σ_t(v),σ_min)")
            schmitt_smin = st.sidebar.slider(
                "σ_min 地板保护", 0.01, 0.20, 0.05, 0.01,
                help="防止低波动下ε_t→0导致频繁触发")
            schmitt_ewma = st.sidebar.slider(
                "EWMA 周期 N", 10, 120, 60, 10,
                help="α=2/(N+1),越大越平滑,滞后越大")

    # ==================== MAIN AREA: LOAD DATA ====================
    if is_stock:
        stock = st.session_state.stock_data
        t, noisy, ohlc_data, ticker_full, tf_label = stock
        clean = None
    else:
        data = datasets[dataset_id]
        t = data["t"]
        clean = data["clean"]
        noisy = data["noisy"]
        st.header(f"数据集: {data['name']}")
        st.caption(data["desc"])

    # ---- Apply filter ----
    try:
        filtered = selected_filter["func"](noisy, t, **param_values)
        filtered = np.asarray(filtered, dtype=float).ravel()
    except Exception as exc:
        st.error(f"滤波计算出错: {exc}")
        filtered = np.full_like(noisy, np.nan)

    if np.all(np.isnan(filtered)):
        st.error("滤波输出全部为 NaN，请调整参数。")

    # ---- Apply second filter (if dual mode) ----
    if dual_mode and selected_filter2:
        try:
            filtered2 = selected_filter2["func"](noisy, t, **param_values2)
            filtered2 = np.asarray(filtered2, dtype=float).ravel()
        except Exception as exc:
            st.error(f"滤波 2 计算出错: {exc}")
            filtered2 = np.full_like(noisy, np.nan)
    else:
        filtered2 = None

    # ---- Schmitt Trigger computation (stock mode, uses v & a) ----
    schmitt = None
    if is_stock and show_schmitt and not np.all(np.isnan(filtered)):
        _v = np.gradient(filtered, t)
        _a = np.gradient(_v, t)
        schmitt = _schmitt_trigger(_v, _a,
            ewma_span=schmitt_ewma, k_eps=schmitt_k_eps, sigma_min=schmitt_smin)

    # ==================== BUILD FIGURE ====================
    if is_stock:
        if show_schmitt and schmitt:
            rows = 5
            row_heights = [0.28, 0.15, 0.18, 0.18, 0.21]
            titles = ("价格 & 滤波输出", "残差分析",
                       "滤波输出 — 速度 (v)",
                       "施密特: 加速度 a & 死区 ±ε",
                       "施密特: 离散信号 Sig_t")
            main_row = 1; r_row = 2; v_row = 3; st_a_row = 4; st_sig_row = 5
            a_row = None
        else:
            rows = 4; row_heights = [0.40, 0.18, 0.20, 0.22]
            titles = ("价格 & 滤波输出", "残差分析",
                       "滤波输出 — 速度 (v)", "滤波输出 — 加速度 (a)")
            main_row = 1; r_row = 2; v_row = 3; a_row = 4
            st_a_row = st_sig_row = None
        t_row = None
    else:
        rows = 4
        row_heights = [0.35, 0.18, 0.22, 0.25]
        titles = ("时域波形对比", "残差分析", "滤波输出 — 速度 (v)", "滤波输出 — 加速度 (a)")
        t_row = 1
        r_row = 2
        v_row = 3
        a_row = 4
        main_row = None  # not used in synthetic mode

    fig = make_subplots(
        rows=rows, cols=1, shared_xaxes=True,
        vertical_spacing=0.03, row_heights=row_heights,
        subplot_titles=titles)

    # Row 1 (stock): candlestick + close + filter all in same subplot
    if is_stock and ohlc_data is not None:
        fig.add_trace(go.Candlestick(
            x=t,
            open=ohlc_data["Open"].values.ravel(),
            high=ohlc_data["High"].values.ravel(),
            low=ohlc_data["Low"].values.ravel(),
            close=ohlc_data["Close"].values.ravel(),
            name="K线", increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
            showlegend=False,
        ), row=main_row, col=1)
        fig.add_trace(go.Scatter(
            x=t, y=noisy, mode="lines", name="收盘价",
            line=dict(color="#5f6c80", width=1.0),
        ), row=main_row, col=1)
    if not is_stock:
        # Synthetic: time-domain traces in row 1
        if show_clean and clean is not None:
            fig.add_trace(go.Scatter(
                x=t, y=clean, mode="lines", name="干净信号",
                line=dict(color="#ff6b6b", width=1.5),
            ), row=t_row, col=1)
        if show_noisy:
            fig.add_trace(go.Scatter(
                x=t, y=noisy, mode="lines", name=noisy_label,
                line=dict(color="#5f6c80", width=1.0),
            ), row=t_row, col=1)

    # Filter output (both modes)
    filter_row = main_row if is_stock else t_row
    if show_filtered and not np.all(np.isnan(filtered)):
        fig.add_trace(go.Scatter(
            x=t, y=filtered, mode="lines", name="滤波输出",
            line=dict(color=filter_color, width=2.0),
        ), row=filter_row, col=1)
    if dual_mode and filtered2 is not None and not np.all(np.isnan(filtered2)):
        fig.add_trace(go.Scatter(
            x=t, y=filtered2, mode="lines", name="滤波输出 2",
            line=dict(color=filter_color2, width=2.0),
        ), row=filter_row, col=1)

    # ---- Schmitt: acceleration a + deadband ±ε (ref: chart ④) ----
    if is_stock and schmitt is not None:
        eps_arr = schmitt["eps"]; sig_arr = schmitt["sig"]
        # Recompute v, a for display (already computed above but kept local)
        _v_disp = np.gradient(filtered, t)
        _a_disp = np.gradient(_v_disp, t)
        # deadband fill
        fig.add_trace(go.Scatter(
            x=list(t) + list(t[::-1]),
            y=list(eps_arr) + list(-eps_arr[::-1]),
            fill="toself", fillcolor="rgba(128,128,128,0.06)",
            line=dict(width=0), name="死区 ±ε", hoverinfo="skip",
        ), row=st_a_row, col=1)
        # ±ε boundary lines
        fig.add_trace(go.Scatter(
            x=t, y=eps_arr, mode="lines",
            name="+ε_t", line=dict(color="#f85149", width=0.8, dash="dash"),
            showlegend=False,
        ), row=st_a_row, col=1)
        fig.add_trace(go.Scatter(
            x=t, y=-eps_arr, mode="lines",
            name="-ε_t", line=dict(color="#f85149", width=0.8, dash="dash"),
        ), row=st_a_row, col=1)
        # acceleration a
        fig.add_trace(go.Scatter(
            x=t, y=_a_disp, mode="lines", name="加速度 a",
            line=dict(color="#d2991d", width=1.5),
        ), row=st_a_row, col=1)
        fig.add_hline(y=0, line_dash="solid", line_color="gray",
                       opacity=0.3, row=st_a_row, col=1)

    # ---- Schmitt: discrete signal Sig_t (ref: chart ⑥) ----
    if is_stock and schmitt is not None:
        fig.add_trace(go.Scatter(
            x=t, y=sig_arr.astype(float), mode="lines",
            name="Schmitt Sig", line=dict(color="#58a6ff", width=2, shape="hv"),
        ), row=st_sig_row, col=1)
        fig.add_hline(y=0, line_dash="solid", line_color="gray",
                       opacity=0.15, row=st_sig_row, col=1)
        # Schmitt state background
        for state, color in [(1, "rgba(63,185,80,0.06)"), (-1, "rgba(248,81,73,0.06)")]:
            mask = sig_arr == state
            if mask.any():
                y_arr = np.where(sig_arr[mask] == 1, 1.0, -1.0)
                fig.add_trace(go.Scatter(
                    x=t[mask], y=y_arr,
                    mode="lines", line=dict(width=0),
                    fill="tozeroy", fillcolor=color,
                    showlegend=False, hoverinfo="skip",
                ), row=st_sig_row, col=1)

    # Residual row
    if not np.all(np.isnan(filtered)):
        if clean is not None:
            fig.add_trace(go.Scatter(
                x=t, y=filtered - clean, mode="lines",
                name="残差 (滤波 - 干净)",
                line=dict(color="#ffa502", width=1.5),
            ), row=r_row, col=1)
        fig.add_trace(go.Scatter(
            x=t, y=filtered - noisy, mode="lines",
            name="残差 (滤波 - 收盘)" if is_stock else "残差 (滤波 - 含噪)",
            line=dict(color="#5f6c80", width=1.0, dash="dot"),
        ), row=r_row, col=1)
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5, row=r_row, col=1)

    # Velocity row + acceleration row (synthetic mode or stock without schmitt)
    if not np.all(np.isnan(filtered)):
        velocity = np.gradient(filtered, t)
        acceleration = np.gradient(velocity, t)

        fig.add_trace(go.Scatter(
            x=t, y=velocity, mode="lines", name="速度 v",
            line=dict(color=filter_color, width=1.5),
        ), row=v_row, col=1)
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5, row=v_row, col=1)

        if a_row is not None:  # synthetic mode or stock without schmitt
            fig.add_trace(go.Scatter(
                x=t, y=acceleration, mode="lines", name="加速度 a",
                line=dict(color="#ffa502", width=1.5),
            ), row=a_row, col=1)
            fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5, row=a_row, col=1)

    # Crosshair
    fig.add_shape(type="line", x0=0, x1=0, y0=0, y1=1,
                   xref="x", yref="paper",
                   line=dict(color="rgba(200,200,200,0.4)", width=1, dash="dot"),
                   visible=False)

    fig_height = 880 if (is_stock and schmitt) else 700
    fig.update_layout(
        template="plotly_dark", height=fig_height,
        margin=dict(l=20, r=20, t=40, b=20),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig.update_xaxes(title_text="时间索引" if is_stock else "时间 (s)", row=rows, col=1)
    if is_stock:
        fig.update_xaxes(rangeslider_visible=False, row=1, col=1)
    first_row = main_row if is_stock else t_row
    fig.update_yaxes(title_text="价格" if is_stock else "幅值", row=first_row, col=1)
    fig.update_yaxes(title_text="残差", row=r_row, col=1)
    fig.update_yaxes(title_text="速度", row=v_row, col=1)
    if is_stock and schmitt is not None:
        fig.update_yaxes(title_text="a & ±ε", row=st_a_row, col=1)
        fig.update_yaxes(title_text="Sig", row=st_sig_row, col=1,
                          tickvals=[-1, 0, 1], ticktext=["空头", "观望", "多头"],
                          range=[-1.5, 1.5])
    if a_row is not None:
        fig.update_yaxes(title_text="加速度", row=a_row, col=1)

    _render_plotly(fig, height=fig_height + 40)

    # ---- Metrics row ----
    if clean is not None:
        metrics = compute_metrics(clean, noisy, filtered)
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("MSE", f"{metrics['mse']:.4f}" if not np.isnan(metrics["mse"]) else "N/A")
        c2.metric("RMSE", f"{metrics['rmse']:.4f}" if not np.isnan(metrics["rmse"]) else "N/A")
        c3.metric("MAE", f"{metrics['mae']:.4f}" if not np.isnan(metrics["mae"]) else "N/A")
        snr = metrics["snr_imp"]
        c4.metric("SNR ↑", f"{snr:.1f} dB" if not np.isnan(snr) else "N/A")
        c5.metric("延迟", f"{metrics['lag']:.0f} 点")
        c6.metric("平滑度", f"{metrics['roughness']:.1f}")
    else:
        roughness = float(np.sum(np.diff(filtered, 2) ** 2)) if len(filtered) > 2 else 0.0
        c1, c2, c3 = st.columns(3)
        c1.metric("收盘价均值", f"{noisy.mean():.4f}")
        c2.metric("收盘价标准差", f"{noisy.std():.4f}")
        c3.metric("平滑度", f"{roughness:.1f}")


if __name__ == "__main__":
    main()
