"""
多周期股票滤波分析工具 — 4视图独立配置, 施密特触发器 + 滤波对比
"""

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
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
from db import init_db, upsert_kline, query_kline, get_date_range, has_data

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
def _render_plotly(fig, height=750, dates=None):
    """Render Plotly chart with cross-subplot crosshair + custom tooltip.

    If dates is provided (list of Timestamps), the tooltip will display the
    date at the hovered position.
    """
    fig_dict = {"data": [], "layout": fig.layout.to_plotly_json()}
    if dates is not None:
        # Store date strings in layout for JS tooltip
        if hasattr(dates[0], 'strftime'):
            fig_dict["layout"]["_dates"] = [d.strftime("%Y-%m-%d %H:%M") for d in dates]
        else:
            fig_dict["layout"]["_dates"] = [str(d) for d in dates]
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
            var dateStr = (gd.layout._dates && idx < gd.layout._dates.length)
                ? gd.layout._dates[idx] : '';
            if (dateStr) lines.push('<b>' + dateStr + '</b>');
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
                // 防遮挡：右侧溢出时自动切到光标左边
                var tx = e.clientX + 18;
                var tw = tip.offsetWidth || 200;  // 首次渲染时估算宽度
                if (tx + tw > window.innerWidth - 10) {{
                    tx = e.clientX - tw - 18;
                }}
                if (tx < 5) tx = 5;
                tip.style.left = tx + 'px';
                // 底部溢出时上移
                var ty = e.clientY - 10;
                var th = tip.offsetHeight || 60;
                if (ty + th > window.innerHeight - 10) {{
                    ty = e.clientY - th - 10;
                }}
                tip.style.top = ty + 'px';
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
def _fetch_all_timeframes(market, code):
    """获取某股票全部8个周期的数据，并行写入DB。返回成功/失败统计。"""
    tf_config = {
        "1分钟": ("7d",), "5分钟": ("60d",), "15分钟": ("60d",),
        "60分钟": ("730d",), "日线": ("max",), "周线": ("max",),
        "月线": ("max",), "季线": ("max",),
    }
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _fetch_one(tf):
        force_period = tf_config[tf][0]
        try:
            t, close, ohlc, full, err, dates = _fetch_stock(market, code, tf, 99999, force_period=force_period)
            if err or ohlc is None:
                return tf, False, err or "无数据"
            return tf, True, len(ohlc)
        except Exception as e:
            return tf, False, str(e)[:80]

    results = {}
    with ThreadPoolExecutor(max_workers=8) as exec:
        futures = {exec.submit(_fetch_one, tf): tf for tf in tf_config}
        for fut in as_completed(futures):
            tf, ok, detail = fut.result()
            results[tf] = (ok, detail)
    return results


@st.cache_data(show_spinner=False, ttl=300)
def _fetch_stock(market, code, tf, n_pts, force_period=None):
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
    if force_period:
        period = force_period
    elif tf == "1分钟":
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
        return None, None, None, full, f"无数据: {full}", None

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.droplevel(1)

    data = data[data["Close"].notna()]

    # ── 全量写入 SQLite ──
    try:
        upsert_kline(code, tf, data)
    except Exception:
        pass

    # 从DB返回最后 n_pts 条
    result_df = query_kline(code, tf, n_pts, day_offset=0)
    n = len(result_df)
    if n == 0:
        return None, None, None, full, "写入成功但查询失败", None
    close = result_df["Close"].values.ravel()
    dates = pd.to_datetime(result_df["Date"])
    result_ohlc = result_df if "Open" in result_df.columns else pd.DataFrame({"Open":close,"High":close,"Low":close,"Close":close}, index=dates)
    return np.arange(n, dtype=float), close, result_ohlc, full, None, dates


def _sync_to_display(code, tf, day_offset, n_pts):
    """从 SQLite 按天偏移查询，写入 display parquet。"""
    df = query_kline(code, tf, n_pts, day_offset=day_offset)
    if len(df) < 5:
        return False, len(df)
    df["Date"] = pd.to_datetime(df["Date"])
    display_dir = Path(__file__).parent.parent / "data" / "display"
    display_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(display_dir / f"{tf}.parquet", index=False)
    return True, len(df)


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


def _find_all_pairs(sig_t):
    """扫描 sig_t，找出窗口中所有多空切换对。
    规则：相邻异号原始段直接配对，不合并同号段。
    +1→0→+1 是两次独立多头信号，不应合并。
    返回 [(start, end), ...] 或空列表。"""
    n = len(sig_t)
    if n < 3:
        return []

    # Step 1: 收集所有非零段
    segments = []  # [(start, end, val), ...]
    i = 0
    while i < n:
        if sig_t[i] != 0:
            start = i
            val = sig_t[i]
            while i < n and sig_t[i] == val:
                i += 1
            end = i - 1
            segments.append((start, end, val))
        else:
            i += 1

    if len(segments) < 2:
        return []

    # Step 2: 相邻异号段配对 — 结束于相反信号的入口边缘
    # 向上对: 0→+1起始 → +1段 → 0区 → 0→-1边缘结束
    # 向下对: 0→-1起始 → -1段 → 0区 → 0→+1边缘结束
    pairs = []
    for j in range(len(segments) - 1):
        s1, e1, v1 = segments[j]
        s2, e2, v2 = segments[j + 1]
        if v1 != v2:  # 多→空 或 空→多
            pairs.append((s1, s2))  # 结束于相反信号的入口（边缘）

    return pairs


def _fit_parabolic(x, y, start, end):
    """对 y[start:end+1] 做二次多项式拟合。
    返回 dict {a, b, c, y_fit} 或 None（数据不足）。"""
    x_seg = x[start:end + 1]
    y_seg = y[start:end + 1]
    if len(x_seg) < 3:
        return None
    coeffs = np.polyfit(x_seg, y_seg, 2)
    y_fit = np.polyval(coeffs, x_seg)
    return {"a": coeffs[0], "b": coeffs[1], "c": coeffs[2], "y_fit": y_fit}


def _add_prediction_traces(fig, t, fit_result, fit_start, pair_end, row,
                          n_extend=10, show_legend=True):
    """在 price 子图上添加预测曲线。
    fit_start .. pair_end  — 多空对全段拟合（橙色实线）
    pair_end .. +n_extend  — 前向预测（紫色虚线）"""
    name = "预测曲线"
    fit_color = "#f0a040"   # 橙色
    pred_color = "#a371f7"  # 紫色
    a, b, c = fit_result["a"], fit_result["b"], fit_result["c"]

    # 拟合段 — 橙色实线（覆盖整个多空对）
    x_fit = t[fit_start:pair_end + 1]
    y_fit = fit_result["y_fit"]
    fig.add_trace(go.Scatter(
        x=x_fit, y=y_fit,
        mode="lines", name=f"{name}(拟合)",
        line=dict(color=fit_color, width=2),
        legendgroup=name,
        showlegend=show_legend,
    ), row=row, col=1)

    # 前向延伸 — 紫色虚线
    if n_extend > 0:
        x_ext = np.arange(pair_end, pair_end + n_extend)
        y_ext = np.polyval((a, b, c), x_ext)
        fig.add_trace(go.Scatter(
            x=x_ext, y=y_ext,
            mode="lines", name=f"{name}(预测)",
            line=dict(color=pred_color, width=2, dash="dash"),
            legendgroup=name,
            showlegend=show_legend,
        ), row=row, col=1)


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

    # Row 1: [周期▼] [N▬] [施密特☑] [预测曲线☑] [预测点▬] [k_ε▬] [σ_min▬] [N_EWMA▬]
    c = st.columns([1.0, 0.8, 0.8, 0.8, 0.8, 1.1, 1.1, 1.1])
    with c[0]:
        cfg["tf"] = st.selectbox("周期", ALL_TFS, index=ALL_TFS.index(tf_default),
            key=f"{key}_tf", label_visibility="collapsed")
    with c[1]:
        cfg["n_pts"] = st.slider("N", 20, 300, 120, 10, key=f"{key}_n", label_visibility="collapsed")
    with c[2]:
        cfg["show_sch"] = st.checkbox("施密特", value=True, key=f"{key}_sch")
    cfg["ke"]=0.15; cfg["sm"]=0.05; cfg["ew"]=60; cfg["show_pred"]=False; cfg["n_ext"]=10
    if cfg["show_sch"]:
        with c[3]: cfg["show_pred"] = st.checkbox("预测曲线", value=True, key=f"{key}_pred")
        if cfg["show_pred"]:
            with c[4]: cfg["n_ext"] = st.slider("预测点", 1, 50, 10, 1, key=f"{key}_next")
        with c[5]: cfg["ke"] = st.slider("k_ε",0.01,0.50,0.15,0.05,key=f"{key}_ke",
            help="灵敏度系数,越小越敏感. ε_t=k_ε·max(σ_t(v),σ_min)")
        with c[6]: cfg["sm"] = st.slider("σ_min",0.01,0.20,0.05,0.02,key=f"{key}_sm",
            help="地板保护,防止低波动下ε_t→0")
        with c[7]: cfg["ew"] = st.slider("N_EWMA",10,120,60,10,key=f"{key}_ew",
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


def _render_chart(market, ticker_code, cfg, key, compact=True, day_offset=0):
    """Fetch data + render multi-subplot figure from config.
    优先从本地 Parquet 读取；day_offset=向历史前移N天（各周期独立对齐）。"""
    tf = cfg["tf"]
    n_pts = cfg["n_pts"]

    # ── 数据获取：DB → display → 渲染 ──
    _sync_to_display(ticker_code, tf, day_offset, n_pts)

    display_path = Path(__file__).parent.parent / "data" / "display" / f"{tf}.parquet"
    err = None
    if display_path.exists():
        try:
            df = pd.read_parquet(display_path)
            if "Date" in df.columns and "Close" in df.columns and len(df) >= 5:
                df["Date"] = pd.to_datetime(df["Date"])
                df = df.set_index("Date").sort_index()
                t = np.arange(len(df), dtype=float)
                noisy = df["Close"].values.ravel()
                ohlc = df[["Open","High","Low","Close"]] if all(c in df.columns for c in ["Open","High","Low"]) else pd.DataFrame({"Open":noisy,"High":noisy,"Low":noisy,"Close":noisy}, index=df.index)
                ticker_full = ticker_code
                dates = df.index
            else:
                err = "数据不足"
        except Exception as e:
            err = str(e)
    else:
        t, noisy, ohlc, ticker_full, err, dates = _fetch_stock(market, ticker_code, tf, n_pts)

    if err: st.error(err); return

    # ── Date markers: vertical dashed lines + tick labels ──
    def _date_markers(dates, tf):
        """Return (positions, labels) for vertical date markers."""
        if dates is None or len(dates) == 0:
            return [], []
        positions, labels = [], []
        n = len(dates)
        if tf in ("1分钟", "5分钟", "15分钟", "60分钟"):
            # Intraday: mark each calendar day boundary
            prev_d = None
            for i, d in enumerate(dates):
                day = d.date() if hasattr(d, 'date') else pd.Timestamp(d).date()
                if prev_d is not None and day != prev_d:
                    positions.append(i)
                    labels.append(day.strftime("%m/%d"))
                prev_d = day
        elif tf == "日线":
            # Daily: mark Monday (week start)
            for i, d in enumerate(dates):
                if d.weekday() == 0:
                    positions.append(i)
                    labels.append(d.strftime("%m/%d"))
        elif tf == "周线":
            # Weekly: mark first week of each month
            prev_m = None
            for i, d in enumerate(dates):
                m = d.month
                if prev_m is not None and m != prev_m:
                    positions.append(i)
                    labels.append(d.strftime("%m/%d"))
                prev_m = m
        elif tf == "月线":
            # Monthly: mark January
            for i, d in enumerate(dates):
                if d.month == 1:
                    positions.append(i)
                    labels.append(d.strftime("%Y"))
        else:  # 季线
            for i, d in enumerate(dates):
                if d.month == 1:
                    positions.append(i)
                    labels.append(d.strftime("%Y"))
        return positions, labels

    marker_positions, marker_labels = _date_markers(dates, cfg["tf"])

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

    rough = float(np.sum(np.diff(filtered,2)**2)) if len(filtered)>2 else 0.0
    c1,c2,c3 = st.columns(3)
    c1.caption(f"{ticker_full}·{cfg['tf']}  |  ¥{noisy[-1]:.2f}")
    c2.caption(f"σ={noisy.std():.2f}  平滑={rough:.1f}")
    c3.caption(f"{len(t)} 点")

    # Schmitt
    schmitt = None
    if cfg["show_sch"] and not np.all(np.isnan(filtered)):
        _v = np.gradient(filtered, t); _a = np.gradient(_v, t)
        schmitt = _schmitt_trigger(_v, _a, ewma_span=cfg["ew"], k_eps=cfg["ke"], sigma_min=cfg["sm"])

    # 多空切换对 — 始终计算（用于预测曲线 + Sig_t 背景标记）
    all_pairs = []
    if schmitt is not None:
        all_pairs = _find_all_pairs(schmitt["sig"])

    # 预测曲线 — 窗口中所有多空对，全段拟合 + 末对前向预测
    pred_pairs = []
    if cfg.get("show_pred") and schmitt is not None:
        for pair_start, pair_end in all_pairs:
            if pair_end - pair_start >= 3:  # 需 ≥3 点
                fit_result = _fit_parabolic(t, filtered, pair_start, pair_end)
                if fit_result is not None:
                    pred_pairs.append({
                        "fit_result": fit_result,
                        "fit_start": pair_start,
                        "pair_end": pair_end,
                    })

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

    for i, pp in enumerate(pred_pairs):
        _add_prediction_traces(fig, t,
                               pp["fit_result"], pp["fit_start"],
                               pp["pair_end"], row=mr,
                               n_extend=cfg.get("n_ext", 10),
                               show_legend=(i == 0))

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
        # 切换对背景色带 — 向上对填充 0~1（多-观），向下对填充 -1~0（观-空）
        for i, (p_start, p_end) in enumerate(all_pairs):
            direction = sig[p_end]  # 对尾段符号：+1 向上(多)，-1 向下(空)
            y_lo, y_hi = (0, 1) if direction == 1 else (-1, 0)
            band_color = "rgba(88,166,255,0.10)" if i % 2 == 0 else "rgba(163,113,247,0.10)"
            fig.add_trace(go.Scatter(
                x=[p_start, p_end, p_end, p_start],
                y=[y_hi, y_hi, y_lo, y_lo],
                fill="toself", fillcolor=band_color,
                mode="lines", line=dict(width=0),
                showlegend=False, hoverinfo="skip",
            ), row=ssr, col=1)
    if ar is not None and not np.all(np.isnan(filtered)):
        fig.add_trace(go.Scatter(x=t, y=acc, mode="lines", name="a",
            line=dict(color="#ffa502", width=1.5)), row=ar, col=1)
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5, row=ar, col=1)
    fig.add_shape(type="line", x0=0, x1=0, y0=0, y1=1, xref="x", yref="paper",
                   line=dict(color="rgba(200,200,200,0.4)", width=1, dash="dot"), visible=False)
    # Date markers: vertical dashed lines at period boundaries
    for pos in marker_positions:
        fig.add_vline(x=pos, line=dict(color="rgba(255,255,255,0.10)", width=0.8, dash="dot"),
                       layer="below")
    fh = (540 if has_s else 420) if compact else (880 if has_s else 700)
    fig.update_layout(template="plotly_dark", height=fh,
        margin=dict(l=10,r=10,t=25,b=10), hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=9)))
    # X-axis: show date labels at marker positions
    fig.update_xaxes(title_text="", row=rows, col=1,
                      tickvals=marker_positions, ticktext=marker_labels,
                      tickfont=dict(size=9, color="#8b949e"))
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
    _render_plotly(fig, height=fh+30, dates=dates)


# =====================================================================
def main():
    init_db()
    st.sidebar.title("多周期股票滤波分析")

    # ── Import config (before any widget) ──
    # _import_data stores the MD5 hash of the last successfully applied config.
    # When a file is uploaded, we compare its hash to detect "stale hold"
    # (same file re-triggering on rerun) vs genuine new uploads.
    if "_import_data" not in st.session_state:
        st.session_state._import_data = None  # None = no config applied yet

    uploaded = st.sidebar.file_uploader("导入配置", type=["json"], key="config_import",
                                         label_visibility="collapsed")
    if uploaded is not None:
        raw = uploaded.read()
        file_hash = hashlib.md5(raw).hexdigest()
        if st.session_state._import_data != file_hash:  # new or different file
            try:
                config = json.loads(raw)
                for k, v in config.items():
                    st.session_state[k] = v
                st.session_state._import_data = file_hash
                st.sidebar.success("配置已加载")
            except Exception as e:
                st.sidebar.error(f"导入失败: {e}")

    market = st.sidebar.radio("市场", ["美股 US","A股(沪深)","港股 HK"],
                               horizontal=True, key="market")
    c1, c2 = st.sidebar.columns([1, 1])
    with c1:
        ticker_code = st.text_input("股票代码", value="AAPL", key="ticker").strip()
    with c2:
        if ticker_code:
            @st.cache_data(show_spinner=False, ttl=3600)
            def _stock_name(mkt, code):
                try:
                    if mkt == "A股(沪深)":
                        full = code + (".SS" if code[0] == "6" else ".SZ")
                    elif mkt == "港股 HK":
                        full = code.zfill(4) + ".HK"
                    else:
                        full = code.upper()
                    return yf.Ticker(full).info.get("longName") or ""
                except Exception:
                    return ""
            name = _stock_name(market, ticker_code)
            if name:
                st.caption(f"📌 {name}")

    # 首次加载：DB无数据时自动获取全部周期
    if "_fetched_ticker" not in st.session_state:
        st.session_state._fetched_ticker = ""
    if ticker_code and ticker_code != st.session_state._fetched_ticker:
        if not has_data(ticker_code):
            with st.spinner(f"首次获取 {ticker_code} 全部周期数据..."):
                results = _fetch_all_timeframes(market, ticker_code)
                ok = sum(1 for ok, _ in results.values() if ok)
                if ok > 0:
                    st.sidebar.success(f"已获取 {ok}/8 个周期")
        st.session_state._fetched_ticker = ticker_code

    # Refresh row: button + auto toggle
    c_refresh, c_auto = st.sidebar.columns([1, 1.2])
    with c_refresh:
        if st.button("刷新数据", use_container_width=True):
            _fetch_stock.clear()
            with st.spinner("正在获取全部周期..."):
                results = _fetch_all_timeframes(market, ticker_code)
            ok = sum(1 for r_ok, _ in results.values() if r_ok)
            fail = sum(1 for r_ok, _ in results.values() if not r_ok)
            if ok > 0:
                st.sidebar.success(f"✅ 获取成功 {ok}/8 个周期")
            if fail > 0:
                for tf, (r_ok, detail) in results.items():
                    if not r_ok:
                        st.sidebar.warning(f"❌ {tf}: {detail}")
    with c_auto:
        auto_refresh = st.checkbox("自动刷新", value=False, key="auto_refresh")
    if auto_refresh:
        interval = st.sidebar.slider("刷新间隔(秒)", 10, 600, 60, 10, key="refresh_interval")

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
                    tf_label = st.session_state.get(f"v{i}_tf", DEFAULT_TFS[i])
                    st.caption(f"视图{i+1} · {tf_label}")
                    cfg = _render_params(f"v{i}", filter_id, dual, filter_id2, DEFAULT_TFS[i])
                    configs.append(cfg)

    # ── 时间窗口导航（按天前移/后移，各周期独立对齐）──
    st.sidebar.markdown("---")
    st.sidebar.caption("⏪ 时间窗口（按天移动）")
    if "_day_offset" not in st.session_state:
        st.session_state._day_offset = 0

    step_days = st.sidebar.selectbox("移动步长", [1, 3, 5, 10, 20, 30, 60, 90, 180, 365],
                                      index=4, key="day_step",
                                      format_func=lambda x: f"{x}天")

    # ── 读取归档数据范围，判断前移/后移是否可用 ──
    data_start = data_end = None
    date_range = get_date_range(ticker_code)
    if date_range:
        data_start = pd.Timestamp(date_range[0][:10]).date()
        data_end = pd.Timestamp(date_range[1][:10]).date()

    # 计算当前显示窗口
    cur_offset = st.session_state._day_offset
    n_pts = configs[0]["n_pts"] if configs else 120
    if data_end:
        win_end = data_end - pd.Timedelta(days=cur_offset)
        win_start = win_end - pd.Timedelta(days=n_pts * 2)  # 粗略估算
        has_older = data_start and win_start > data_start  # 还有更早数据
        has_newer = cur_offset > 0  # 可以往后移（回到最新）
    else:
        has_older = True
        has_newer = cur_offset > 0

    c_prev, c_next, c_home = st.sidebar.columns([1, 1, 0.8])
    with c_prev:
        disabled = not has_older
        if st.button("◀ 前移", key="day_prev", use_container_width=True, disabled=disabled,
                     help="无更早数据" if disabled else f"前移{step_days}天"):
            st.session_state._day_offset += step_days
    with c_next:
        disabled = not has_newer
        if st.button("后移 ▶", key="day_next", use_container_width=True, disabled=disabled,
                     help="已是最新" if disabled else f"后移{step_days}天"):
            st.session_state._day_offset = max(0, st.session_state._day_offset - step_days)
    with c_home:
        if st.button("最新", key="day_home", use_container_width=True, disabled=cur_offset == 0,
                     help="已是最新"):
            st.session_state._day_offset = 0

    st.sidebar.caption(f"已偏移: {cur_offset} 天")
    if data_start and data_end:
        st.sidebar.caption(f"数据范围: {data_start} ~ {data_end}")
    day_offset = st.session_state._day_offset

    # ---- Pass 2: Bottom 2x2 chart views ----

    for row_idx in range(2):
        c1, c2 = st.columns(2)
        for col_idx, col in enumerate([c1, c2]):
            i = row_idx * 2 + col_idx
            with col:
                _render_chart(market, ticker_code, configs[i], f"v{i}", compact=True,
                              day_offset=day_offset)

    # ── Export (after ALL widgets) ──
    st.sidebar.markdown("---")
    export_data = {
        "market": market, "ticker": ticker_code,
        "global_f": filter_id, "global_dual": dual, "global_f2": filter_id2,
    }
    for i, cfg in enumerate(configs):
        export_data[f"v{i}_tf"] = cfg["tf"]
        export_data[f"v{i}_n"] = cfg["n_pts"]
        export_data[f"v{i}_sch"] = cfg["show_sch"]
        export_data[f"v{i}_ke"] = cfg["ke"]
        export_data[f"v{i}_sm"] = cfg["sm"]
        export_data[f"v{i}_ew"] = cfg["ew"]
        export_data[f"v{i}_fc"] = cfg["fc"]
        export_data[f"v{i}_fc2"] = cfg["fc2"]
        # Use slider label (Chinese) as key prefix, matching _render_param_slider
        f1 = FILTERS.get(filter_id, {})
        for pname, pval in cfg.get("pv", {}).items():
            label = f1["params"].get(pname, (pname,))[0]  # spec tuple's first element = label
            export_data[f"{label}_v{i}_f1_{filter_id}"] = pval
        f2 = FILTERS.get(filter_id2, {}) if filter_id2 else {}
        for pname, pval in cfg.get("pv2", {}).items():
            label = f2["params"].get(pname, (pname,))[0]
            export_data[f"{label}_v{i}_f2_{filter_id2}"] = pval
    st.sidebar.download_button("导出配置", json.dumps(export_data, ensure_ascii=False, indent=2),
        file_name="filter_config.json", mime="application/json",
        use_container_width=True)

    # ── Auto-refresh execution ──
    if auto_refresh:
        now = time.time()
        last = st.session_state.get("_last_auto_refresh")
        if last is None:
            st.session_state._last_auto_refresh = now
        elif now - last >= interval:
            _fetch_stock.clear()
            _fetch_all_timeframes(market, ticker_code)
            st.session_state._last_auto_refresh = now
        remaining = interval - (now - st.session_state.get("_last_auto_refresh", now))
        time.sleep(remaining)
        st.rerun()

if __name__ == "__main__":
    main()
