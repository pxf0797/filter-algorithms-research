"""
多周期股票滤波分析工具 — 4视图独立配置, 施密特触发器 + 滤波对比
"""

import hashlib
import json
import os
import tempfile
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
from config_db import (init_config_tables, list_presets, apply_preset,
                        save_preset, delete_preset, rename_preset,
                        save_ticker_config, record_history, get_history,
                        import_json_files_as_presets)
from db import (init_db, upsert_kline, query_kline, get_date_range, has_data,
                check_data_health, get_db_size_mb, snapshot_db, list_snapshots,
                restore_snapshot, prune_snapshots, clear_display_cache,
                checkpoint_wal, validate_db, compare_with_db, force_update_kline,
                DB_PATH)

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
def _compact_slider(label, pmin, pmax, pdefault, pstep=1, key=None, fmt=None):
    """标签与滑块同行（仅用于无 help 的简单滑块）。"""
    c = st.columns([0.35, 0.65])
    c[0].markdown(f"<small>{label}</small>", unsafe_allow_html=True)
    kwargs = dict(min_value=pmin, max_value=pmax, value=pdefault, step=pstep,
                  key=key, label_visibility="collapsed")
    if fmt: kwargs["format"] = fmt
    return c[1].slider(label, **kwargs)


def _render_param_slider(label, pmin, pmax, pstep, pdefault, key_suffix="", container=None):
    """Render an st.slider with appropriate numeric format.
    If container is None, renders in sidebar (backward compat).
    Pass container=st to render inline in the current column context.
    """
    ctx = container if container is not None else st.sidebar
    key = f"{label}_{key_suffix}" if key_suffix else None
    if key:
        pdefault = st.session_state.get(key, st.session_state.get(f"_imp_{key}", pdefault))
    if isinstance(pstep, int):
        return ctx.slider(label, pmin, pmax, pdefault, pstep, key=key)
    fmt = "%.3f" if pstep < 0.01 else "%.2f"
    return ctx.slider(label, pmin, pmax, pdefault, pstep, format=fmt, key=key)


# ---------------------------------------------------------------------------
# Plotly cross-subplot crosshair helper
# ---------------------------------------------------------------------------
def _render_plotly(fig, height=750, dates=None):
    """Render Plotly chart with cross-subplot crosshair (no value tooltip)."""
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
g.hovertext {{ visibility: hidden !important; }}
.spikeline {{ visibility: hidden !important; }}
#date-tip-{div_id} {{
    display: none;
    position: fixed;
    z-index: 9999;
    background: rgba(30,30,44,0.94);
    color: #c0c0c0;
    padding: 4px 8px;
    border-radius: 4px;
    font-family: monospace;
    font-size: 11px;
    pointer-events: none;
    white-space: nowrap;
}}
</style>
</head>
<body>
<div id="{div_id}"></div>
<div id="date-tip-{div_id}"></div>
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
        var _lastXv = -Infinity, _pending = false, _pendingXv = null, _THROTTLE_MS = 45;
        function _nearestIdx(arr, xv) {{
            var lo = 0, hi = arr.length - 1;
            if (xv <= arr[lo]) return lo;
            if (xv >= arr[hi]) return hi;
            while (lo < hi - 1) {{ var mid = (lo + hi) >> 1; if (arr[mid] <= xv) lo = mid; else hi = mid; }}
            return (xv - arr[lo] <= arr[hi] - xv) ? lo : hi;
        }}
        var _shapeKeys = [];
        var shapes = gd.layout.shapes || [];
        for (var i = 0; i < shapes.length; i++) {{
            if (shapes[i].yref === 'paper' || shapes[i].yref === 'y domain') {{
                _shapeKeys.push({{ x0: 'shapes[' + i + '].x0', x1: 'shapes[' + i + '].x1', vis: 'shapes[' + i + '].visible' }});
            }}
        }}
        var _xArr0 = gd.data[0].x;
        var _dates = gd.layout._dates;
        var _hasDates = _dates && _dates.length > 0;
        var _tip = document.getElementById('date-tip-{div_id}');
        var _dateCache = '';  // cached date string for mousemove

        function _apply(xv) {{
            _pending = false;
            var u = {{}};
            for (var i = 0; i < _shapeKeys.length; i++) {{ var k = _shapeKeys[i]; u[k.x0] = xv; u[k.x1] = xv; u[k.vis] = true; }}
            Plotly.relayout(gd, u);
            _lastXv = xv;
            if (_hasDates && _xArr0 && _xArr0.length > 0) {{
                var idx = _nearestIdx(_xArr0, xv);
                _dateCache = (idx < _dates.length) ? _dates[idx] : '';
                if (_dateCache) {{
                    _tip.textContent = _dateCache;
                    _tip.style.display = 'block';
                }} else {{
                    _tip.style.display = 'none';
                }}
            }}
        }}

        gd.on('plotly_hover', function(evt) {{
            if (!evt.points || evt.points.length === 0) return;
            var xv = evt.points[0].x;
            if (xv === _lastXv) return;
            if (_pending) {{ _pendingXv = xv; }}
            else {{
                _pending = true; _pendingXv = null;
                _apply(xv);
                setTimeout(function() {{
                    if (_pendingXv !== null && _pendingXv !== _lastXv) _apply(_pendingXv);
                    else _pending = false;
                }}, _THROTTLE_MS);
            }}
        }});

        gd.on('plotly_unhover', function() {{
            _pending = false; _pendingXv = null; _lastXv = -Infinity;
            var u = {{}};
            for (var i = 0; i < _shapeKeys.length; i++) {{ u[_shapeKeys[i].vis] = false; }}
            Plotly.relayout(gd, u);
            _tip.style.display = 'none';
            _dateCache = '';
        }});

        // Follow mouse — position tooltip at cursor
        document.getElementById('{div_id}').addEventListener('mousemove', function(e) {{
            if (_tip.style.display === 'block') {{
                var tx = e.clientX + 16;
                var tw = _tip.offsetWidth || 100;
                if (tx + tw > window.innerWidth - 10) tx = e.clientX - tw - 16;
                if (tx < 5) tx = 5;
                _tip.style.left = tx + 'px';
                _tip.style.top = (e.clientY - 28) + 'px';
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

    # ── 日线 Close 回退：Yahoo 日线 API 最后一条 bar 的 Close 偶尔未结算(nan)，
    #    但 Yahoo 周线 API 已通过实时行情计算出本周 Close。
    #    用周线 Close 回填日线，解决 A 股/港股收盘价 1-2 天延迟问题。
    #    详见 docs/data-freshness.md
    if interval == "1d" and len(data) > 0:
        last_close = data["Close"].iloc[-1]
        if pd.isna(last_close):
            try:
                w = yf.download(full, period="5d", interval="1wk", progress=False)
                if len(w) > 0:
                    if isinstance(w.columns, pd.MultiIndex):
                        w.columns = w.columns.droplevel(1)
                    w_close = w["Close"].iloc[-1]
                    if not pd.isna(w_close):
                        data.loc[data.index[-1], "Close"] = float(w_close)
            except Exception:
                pass  # 回退失败不影响主流程，后续 nan 行仍会被丢弃

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
    规则：合并相邻同号段（+1,0,+1 → 一个连续段），异号配对。
    起始于首次入场边缘，经过中间的同向多次入场+观望，止于相反信号入口。
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

    # Step 2: 合并相邻同号段（+1,0,+1 → 一个连续多头段）
    merged = [segments[0]]
    for seg in segments[1:]:
        last = merged[-1]
        if seg[2] == last[2]:  # 同号 → 合并（含中间观望区）
            merged[-1] = (last[0], seg[1], seg[2])
        else:
            merged.append(seg)

    # Step 3: 相邻异号段配对 — 结束于相反信号的入口边缘
    pairs = []
    for j in range(len(merged) - 1):
        s1, e1, v1 = merged[j]
        s2, e2, v2 = merged[j + 1]
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


def _fit_physics_parabola(x, y, start, end):
    """抛物线拟合 — 锚定对终点为顶点，y = a·(x-x₀)² + y₀。
    顶点 (x₀,y₀) = (x[end], y[end]) 固定，仅拟合曲率 a。
    预测段 = 抛物线右半（与左半对称）。"""
    x_seg = x[start:end + 1]
    y_seg = y[start:end + 1]
    if len(x_seg) < 3:
        return None
    x0 = x_seg[-1]   # 顶点 x（对终点）
    y0 = y_seg[-1]   # 顶点 y（实际滤波价，固定）
    dt = x_seg - x0  # ≤0（左半段）
    dt_sq = dt ** 2
    dy = y_seg - y0
    denom = np.sum(dt_sq ** 2)
    if denom < 1e-12:
        return None
    a = np.sum(dt_sq * dy) / denom  # 最小二乘求曲率
    y_fit = y0 + a * dt_sq
    return {"a": a, "b": 0.0, "c": y0, "y_fit": y_fit, "x0": x0}


def _add_prediction_traces(fig, t, filtered, fit_result, fit_start, pair_end, row,
                          n_extend=10, show_legend=True):
    """在 price 子图上添加预测曲线 + 残差子图上的拟合残差。
    fit_start .. pair_end  — 多空对全段拟合（橙色实线）
    pair_end .. +n_extend  — 前向预测（紫色虚线）
    残差(row+1): 前向预测段与最后已知价格的残差，红=预测向上，绿=预测向下"""
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
    y_ext = None
    if n_extend > 0:
        x_ext = np.arange(pair_end, pair_end + n_extend)
        x0 = fit_result.get("x0", None)
        if x0 is not None:
            y_ext = np.polyval((a, b, c), x_ext - x0)
        else:
            y_ext = np.polyval((a, b, c), x_ext)
        fig.add_trace(go.Scatter(
            x=x_ext, y=y_ext,
            mode="lines", name=f"{name}(预测)",
            line=dict(color=pred_color, width=2, dash="dash"),
            legendgroup=name,
            showlegend=show_legend,
        ), row=row, col=1)

    # 残差子图 — 前向预测段与最后已知滤波价格的残差
    if y_ext is not None and n_extend > 0:
        baseline = filtered[pair_end]  # 最后已知滤波价
        residual = y_ext - baseline
        upward = y_ext[-1] > y_ext[0]  # 方向：向上→红，向下→绿
        res_color = "#f85149" if upward else "#3fb950"
        fig.add_trace(go.Scatter(
            x=x_ext, y=residual,
            mode="lines", name=f"{name}(残差)",
            line=dict(color=res_color, width=1.5, dash="dot"),
            legendgroup=name,
            showlegend=show_legend,
        ), row=row + 1, col=1)


def _compute_strategy_pnl(t, filtered, sig_t, all_pairs, pred_pairs,
                          stop_loss_pct, n_extend=10):
    """基于施密特触发器信号和预测曲线方向计算策略PnL。

    返回两条独立曲线：
    - long_pnl: 做多收益曲线，非持仓期水平直线
    - short_pnl: 做空收益曲线，非持仓期水平直线

    Args:
        t: np.array, 时间索引
        filtered: np.array, 滤波价格
        sig_t: np.array, 施密特信号(±1/0)
        all_pairs: list[(start,end)], 多空切换对
        pred_pairs: list[dict], 预测曲线数据
        stop_loss_pct: float, 止损阈值(如2.0表示2%)
        n_extend: int, 预测延伸点数

    Returns:
        long_pnl: np.array(len(t)), 做多PnL曲线(100=初始)
        short_pnl: np.array(len(t)), 做空PnL曲线(100=初始)
        trade_records: list[dict]
    """
    n = len(t)

    # 初始化两条独立曲线
    long_pnl = np.full(n, 100.0)
    short_pnl = np.full(n, 100.0)

    long_capital = 100.0   # 做多已实现本金
    short_capital = 100.0  # 做空已实现本金

    # 空数据保护
    if len(all_pairs) == 0 or len(pred_pairs) == 0:
        return long_pnl, short_pnl, []

    # 建立 pred_pairs 索引: pair_end → pred_data
    pred_map = {}
    for pp in pred_pairs:
        pred_map[pp["pair_end"]] = pp

    trade_records = []
    trade_id = 0

    for pair_start, pair_end in all_pairs:
        if pair_end not in pred_map:
            continue
        pp = pred_map[pair_end]
        fit_result = pp["fit_result"]

        # ---- 计算外推预测方向 ----
        a, b, c = fit_result["a"], fit_result["b"], fit_result["c"]
        x0 = fit_result.get("x0", None)

        x_pred = np.arange(pair_end, pair_end + n_extend)
        if x0 is not None:
            y_pred = np.polyval((a, b, c), x_pred - x0)
        else:
            y_pred = np.polyval((a, b, c), x_pred)

        if len(y_pred) < 2:
            continue
        pred_up = y_pred[-1] > y_pred[0]

        # ---- 判断交易方向 ----
        v2 = sig_t[pair_end]
        is_long = (v2 == 1 and pred_up)
        is_short = (v2 == -1 and not pred_up)

        if not is_long and not is_short:
            continue

        # ---- 入场 ----
        entry_idx = pair_end
        entry_price = filtered[entry_idx]
        if np.isnan(entry_price) or entry_price <= 0:
            continue

        # ---- 扫描：分段混合（方案D） ----
        # 预测保护期 [entry+1, entry+N_ext]：止损 + 止盈 双重保护
        # 趋势跟踪期 [entry+N_ext+1, ...]：仅 Sig 止盈，让利润奔跑
        exit_idx = None
        exit_reason = "take_profit"  # 默认止盈（趋势跟踪期触发或被max_hold截断）
        protect_end = entry_idx + n_extend  # 预测保护期终点
        scan_end = n - 1  # 默认扫描到数据末尾

        for i in range(entry_idx + 1, scan_end + 1):
            cur_price = filtered[i]
            if np.isnan(cur_price) or cur_price <= 0:
                continue

            # 预测保护期内：检查止损
            if i <= protect_end:
                if x0 is not None:
                    pred_val = np.polyval((a, b, c), i - x0)
                else:
                    pred_val = np.polyval((a, b, c), i)

                if not (np.isnan(pred_val) or pred_val <= 0):
                    if is_long:
                        stop_hit = cur_price < pred_val * (1 - stop_loss_pct / 100.0)
                    else:
                        stop_hit = cur_price > pred_val * (1 + stop_loss_pct / 100.0)

                    if stop_hit:
                        exit_idx = i
                        exit_reason = "stop_loss"
                        break

            # 全程：检查止盈（Sig 反转）
            if is_long and sig_t[i] == -1:
                exit_idx = i
                exit_reason = "take_profit"
                break
            if is_short and sig_t[i] == 1:
                exit_idx = i
                exit_reason = "take_profit"
                break

        # 未触发任何离场条件 → 持有到数据末尾
        if exit_idx is None:
            if n - 1 > entry_idx:
                exit_idx = n - 1
                exit_reason = "eod"  # end of data
            else:
                continue

        exit_price = filtered[exit_idx]
        if np.isnan(exit_price) or exit_price <= 0:
            continue

        # ---- 计算收益率 ----
        if is_long:
            trade_return = (exit_price - entry_price) / entry_price
        else:
            trade_return = (entry_price - exit_price) / entry_price

        trade_id += 1

        # ---- 填充持仓期间的PnL曲线 ----
        if is_long:
            # 做多：持仓期间曲线随价格变动
            for i in range(entry_idx, exit_idx + 1):
                cur_p = filtered[i]
                if np.isnan(cur_p) or cur_p <= 0:
                    continue
                unrealized = (cur_p - entry_price) / entry_price
                long_pnl[i] = long_capital * (1 + unrealized)
            # 更新做多已实现本金
            long_capital *= (1 + trade_return)
            # 离场后到数据末尾先填充已实现值（后续交易会覆盖）
            for i in range(exit_idx + 1, n):
                long_pnl[i] = long_capital
        else:
            # 做空：持仓期间曲线随价格变动
            for i in range(entry_idx, exit_idx + 1):
                cur_p = filtered[i]
                if np.isnan(cur_p) or cur_p <= 0:
                    continue
                unrealized = (entry_price - cur_p) / entry_price
                short_pnl[i] = short_capital * (1 + unrealized)
            # 更新做空已实现本金
            short_capital *= (1 + trade_return)
            # 离场后填充已实现值
            for i in range(exit_idx + 1, n):
                short_pnl[i] = short_capital

        # ---- 记录交易 ----
        trade_records.append({
            "id": trade_id,
            "type": "long" if is_long else "short",
            "entry_idx": int(entry_idx),
            "exit_idx": int(exit_idx),
            "entry_price": float(entry_price),
            "exit_price": float(exit_price),
            "return_pct": float(trade_return * 100),
            "exit_reason": exit_reason,
        })

    # ---- 前向填充：非持仓期维持上一个值不变（水平直线） ----
    # 做多曲线
    last_val = 100.0
    for i in range(n):
        if long_pnl[i] == 100.0 and i > 0 and last_val != 100.0:
            long_pnl[i] = last_val
        if long_pnl[i] != 100.0 or (i == 0):
            last_val = long_pnl[i]

    # 做空曲线
    last_val = 100.0
    for i in range(n):
        if short_pnl[i] == 100.0 and i > 0 and last_val != 100.0:
            short_pnl[i] = last_val
        if short_pnl[i] != 100.0 or (i == 0):
            last_val = short_pnl[i]

    return long_pnl, short_pnl, trade_records


def _align_pnl_to_current_tf(higher_dates, higher_pnl_long, higher_pnl_short,
                              higher_trades, current_dates):
    """将高周期PnL数据按时间戳前向填充对齐到当前周期时间轴。

    Args:
        higher_dates: pd.DatetimeIndex, 高周期的日期索引
        higher_pnl_long: np.array, 高周期做多PnL曲线
        higher_pnl_short: np.array, 高周期做空PnL曲线
        higher_trades: list[dict], 高周期交易记录（含entry_idx/exit_idx/return_pct/type/exit_reason）
        current_dates: pd.DatetimeIndex, 当前周期的日期索引

    Returns:
        dict: {
            "aligned_long": np.array(len(current_dates)),
            "aligned_short": np.array(len(current_dates)),
            "entry_markers": [(bar_idx, trade_type, pnl_val), ...],
            "exit_markers": [(bar_idx, trade_type, pnl_val, return_pct, exit_reason), ...],
        }
    """
    n = len(current_dates)
    aligned_long = np.full(n, np.nan)
    aligned_short = np.full(n, np.nan)
    entry_markers = []
    exit_markers = []

    if higher_dates is None or len(higher_dates) == 0:
        return {"aligned_long": aligned_long, "aligned_short": aligned_short,
                "entry_markers": entry_markers, "exit_markers": exit_markers}

    # 统一时区：日内数据带时区(HKT)，日线/周线无时区
    # np.datetime64 无法直接比较 tz-aware 和 tz-naive，需先归一化
    def _normalize_dates(dates):
        """去掉时区信息，按各自字面值比较（日期级对齐）"""
        result = pd.DatetimeIndex(dates)
        if result.tz is not None:
            # tz-aware → 保持本地时间字面值，去掉时区标记
            result = result.tz_localize(None)
        return np.array(result, dtype="datetime64[ns]")

    hd = _normalize_dates(higher_dates)
    cd = _normalize_dates(current_dates)

    # 对当前周期的每个bar，找 ≤ 该时间戳的最近高周期bar（前向填充）
    for i in range(n):
        mask = hd <= cd[i]
        if not mask.any():
            continue
        j = int(np.argmax(mask))  # 最后一个True的位置...
        # argmax on boolean array returns first True. We want last True.
        j = np.max(np.where(mask)[0])
        aligned_long[i] = higher_pnl_long[j]
        aligned_short[i] = higher_pnl_short[j]

    # 映射交易事件到当前周期bar index
    for trade in higher_trades:
        entry_j = trade["entry_idx"]
        exit_j = trade["exit_idx"]
        if entry_j >= len(hd) or exit_j >= len(hd):
            continue
        entry_time = hd[entry_j]
        exit_time = hd[exit_j]

        # 找到当前周期中 ≤ entry_time 的最近bar
        entry_mask = cd <= entry_time
        if entry_mask.any():
            entry_bar = int(np.max(np.where(entry_mask)[0]))
            pnl_at_entry = aligned_long[entry_bar] if trade["type"] == "long" else aligned_short[entry_bar]
            entry_markers.append((entry_bar, trade["type"], pnl_at_entry if not np.isnan(pnl_at_entry) else 100.0))

        # 离场：≤ exit_time 的最近bar
        exit_mask = cd <= exit_time
        if exit_mask.any():
            exit_bar = int(np.max(np.where(exit_mask)[0]))
            pnl_at_exit = aligned_long[exit_bar] if trade["type"] == "long" else aligned_short[exit_bar]
            exit_markers.append((exit_bar, trade["type"],
                                 pnl_at_exit if not np.isnan(pnl_at_exit) else 100.0,
                                 trade.get("return_pct", 0.0),
                                 trade.get("exit_reason", "")))

    return {
        "aligned_long": aligned_long,
        "aligned_short": aligned_short,
        "entry_markers": entry_markers,
        "exit_markers": exit_markers,
    }


def _add_cross_pnl_subplot(fig, t, aligned, row, higher_tf=""):
    """在指定row添加高周期PnL参考子图（事件标记+参考线+盈亏标注）。

    Args:
        fig: plotly figure
        t: np.array, 当前周期的bar index
        aligned: dict, _align_pnl_to_current_tf 的返回值
        row: int, 子图行号
        higher_tf: str, 高周期名称（如"日线"）
    """
    color_long = "#3fb950"
    color_short = "#f85149"
    marker_color = "#d2991d"  # 金色，高周期标记统一色
    tf_label = higher_tf or "高周期"

    # 高周期做多PnL参考线（虚线）
    mask_long = ~np.isnan(aligned["aligned_long"])
    if mask_long.any():
        fig.add_trace(go.Scatter(
            x=t[mask_long], y=aligned["aligned_long"][mask_long],
            mode="lines", name=f"{tf_label}多",
            line=dict(color=color_long, width=1.2, dash="dot"),
            showlegend=False,
        ), row=row, col=1)

    # 高周期做空PnL参考线（点线）
    mask_short = ~np.isnan(aligned["aligned_short"])
    if mask_short.any():
        fig.add_trace(go.Scatter(
            x=t[mask_short], y=aligned["aligned_short"][mask_short],
            mode="lines", name=f"{tf_label}空",
            line=dict(color=color_short, width=1.2, dash="dot"),
            showlegend=False,
        ), row=row, col=1)

    # 入场标记（▲三角形）
    for bar_idx, trade_type, pnl_val in aligned["entry_markers"]:
        if 0 <= bar_idx < len(t):
            fig.add_trace(go.Scatter(
                x=[t[bar_idx]], y=[pnl_val],
                mode="markers",
                marker=dict(color=marker_color, symbol="triangle-up", size=9,
                            line=dict(width=1, color="rgba(0,0,0,0.3)")),
                showlegend=False,
                hovertext=f"{tf_label}入场 {'多' if trade_type == 'long' else '空'}",
                hoverinfo="text",
            ), row=row, col=1)

    # 离场标记 + 盈亏标注
    for bar_idx, trade_type, pnl_val, ret_pct, exit_reason in aligned["exit_markers"]:
        if not (0 <= bar_idx < len(t)):
            continue
        # 离场符号：止损=X，止盈=○
        is_sl = exit_reason == "stop_loss"
        sym = "x" if is_sl else "circle"
        ec = color_short if is_sl else color_long
        fig.add_trace(go.Scatter(
            x=[t[bar_idx]], y=[pnl_val],
            mode="markers",
            marker=dict(color=marker_color, symbol=sym, size=9,
                        line=dict(width=1, color=ec)),
            showlegend=False,
            hovertext=f"{tf_label}离场 {'多' if trade_type == 'long' else '空'} | {ret_pct:+.2f}%",
            hoverinfo="text",
        ), row=row, col=1)

        # 盈亏数字标注
        label_color = "#f85149" if exit_reason == "stop_loss" else "#3fb950"
        arrow = "↑" if trade_type == "long" else "↓"
        fig.add_annotation(
            x=t[bar_idx], y=pnl_val,
            text=f"{arrow}{ret_pct:+.1f}%",
            showarrow=False,
            font=dict(size=8, color=label_color),
            yshift=12,
            row=row, col=1,
        )

    # 100基准线
    fig.add_hline(y=100, line_dash="dash", line_color="gray",
                  opacity=0.4, row=row, col=1)


def _compute_holding_masks(n_bars, entry_markers, exit_markers):
    """从高周期入场/离场marker计算持仓区间掩码。

    Args:
        n_bars: 当前周期bar数
        entry_markers: [(bar_idx, trade_type, pnl_val), ...]
        exit_markers: [(bar_idx, trade_type, pnl_val, return_pct, exit_reason), ...]

    Returns:
        long_mask: np.array(bool), 高周期做多持仓区间
        short_mask: np.array(bool), 高周期做空持仓区间
    """
    long_mask = np.zeros(n_bars, dtype=bool)
    short_mask = np.zeros(n_bars, dtype=bool)

    # 按类型分组，按bar_idx排序
    long_entries = sorted([b for b, t, _ in entry_markers if t == "long"])
    short_entries = sorted([b for b, t, _ in entry_markers if t == "short"])
    long_exits = sorted([b for b, t, _, _, _ in exit_markers if t == "long"])
    short_exits = sorted([b for b, t, _, _, _ in exit_markers if t == "short"])

    # 配对：每个entry找下一个同类型exit（时间上最近的）
    for e_bar in long_entries:
        # 找 > e_bar 的第一个exit
        later_exits = [x for x in long_exits if x > e_bar]
        x_bar = later_exits[0] if later_exits else n_bars - 1
        if e_bar < n_bars:
            long_mask[e_bar:min(x_bar + 1, n_bars)] = True

    for e_bar in short_entries:
        later_exits = [x for x in short_exits if x > e_bar]
        x_bar = later_exits[0] if later_exits else n_bars - 1
        if e_bar < n_bars:
            short_mask[e_bar:min(x_bar + 1, n_bars)] = True

    return long_mask, short_mask


def _add_alignment_subplot(fig, t, long_pnl, short_pnl, trade_records,
                           long_mask, short_mask, row):
    """同向性判断子图：高周期持仓时sample，非持仓时hold。

    做多线：高周期做多持仓段=long_pnl，否则维持上一个值（从100开始）
    做空线：高周期做空持仓段=short_pnl，否则维持上一个值（从100开始）
    """
    n = len(t)

    # 做多曲线：独立累积，同向期间跟随Row6涨跌幅，非同向hold
    long_filtered = np.full(n, 100.0)
    for i in range(1, n):
        if long_mask[i] and long_pnl[i - 1] != 0:
            long_filtered[i] = long_filtered[i - 1] * (long_pnl[i] / long_pnl[i - 1])
        else:
            long_filtered[i] = long_filtered[i - 1]
    fig.add_trace(go.Scatter(
        x=t, y=long_filtered,
        mode="lines", name="做多PnL",
        line=dict(color="#3fb950", width=1.5, dash="solid"),
        showlegend=False,
    ), row=row, col=1)

    # 做空曲线：独立累积，同向期间跟随Row6涨跌幅，非同向hold
    short_filtered = np.full(n, 100.0)
    for i in range(1, n):
        if short_mask[i] and short_pnl[i - 1] != 0:
            short_filtered[i] = short_filtered[i - 1] * (short_pnl[i] / short_pnl[i - 1])
        else:
            short_filtered[i] = short_filtered[i - 1]
    fig.add_trace(go.Scatter(
        x=t, y=short_filtered,
        mode="lines", name="做空PnL",
        line=dict(color="#f85149", width=1.5, dash="solid"),
        showlegend=False,
    ), row=row, col=1)

    # 交易分段高亮 + 入场/离场标记（仅在同向持仓区间内）
    for trade in trade_records:
        is_long = trade["type"] == "long"
        mask = long_mask if is_long else short_mask
        entry_i = trade["entry_idx"]
        exit_i = trade["exit_idx"]
        if entry_i >= n or exit_i >= n:
            continue
        seg_range = slice(entry_i, exit_i + 1)
        if not mask[seg_range].any():
            continue
        curve = long_filtered if is_long else short_filtered
        seg_t = t[seg_range]
        seg_pnl = curve[seg_range]
        color = "#3fb950" if is_long else "#f85149"

        fig.add_trace(go.Scatter(
            x=seg_t, y=seg_pnl,
            mode="lines",
            name=f"{'多' if is_long else '空'}#{trade['id']}",
            line=dict(color=color, width=3),
            showlegend=False,
        ), row=row, col=1)

        if mask[entry_i]:
            fig.add_trace(go.Scatter(
                x=[seg_t[0]], y=[seg_pnl[0]],
                mode="markers",
                marker=dict(color=color, symbol="triangle-up", size=8),
                showlegend=False,
            ), row=row, col=1)

        if trade["exit_reason"] in ("stop_loss", "take_profit") and mask[exit_i]:
            exit_marker = "x" if trade["exit_reason"] == "stop_loss" else "circle"
            exit_color = "#f85149" if trade["exit_reason"] == "stop_loss" else "#3fb950"
            fig.add_trace(go.Scatter(
                x=[seg_t[-1]], y=[seg_pnl[-1]],
                mode="markers",
                marker=dict(color=exit_color, symbol=exit_marker, size=8),
                showlegend=False,
            ), row=row, col=1)
            # 盈亏百分比标注
            ret_pct = trade["return_pct"]
            label_color = "#f85149" if trade["exit_reason"] == "stop_loss" else "#3fb950"
            arrow = "↑" if trade["type"] == "long" else "↓"
            fig.add_annotation(
                x=seg_t[-1], y=seg_pnl[-1],
                text=f"{arrow}{ret_pct:+.1f}%",
                showarrow=False,
                font=dict(size=8, color=label_color),
                yshift=12,
                row=row, col=1,
            )

    # 盈利区域填充
    y_max_l = max(float(np.nanmax(long_filtered)), 100.0) * 1.02
    fig.add_trace(go.Scatter(
        x=[t[0], t[-1], t[-1], t[0]],
        y=[100, 100, y_max_l, y_max_l],
        fill="toself", fillcolor="rgba(63,185,80,0.04)",
        mode="lines", line=dict(width=0),
        showlegend=False, hoverinfo="skip",
    ), row=row, col=1)
    y_min_s = min(float(np.nanmin(short_filtered)), 100.0) * 0.98
    fig.add_trace(go.Scatter(
        x=[t[0], t[-1], t[-1], t[0]],
        y=[100, 100, y_min_s, y_min_s],
        fill="toself", fillcolor="rgba(248,81,73,0.04)",
        mode="lines", line=dict(width=0),
        showlegend=False, hoverinfo="skip",
    ), row=row, col=1)

    # 100基准线
    fig.add_hline(y=100, line_dash="dash", line_color="gray",
                  opacity=0.5, row=row, col=1)


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Split render: params (top 2x2) + chart (bottom 2x2)
# ---------------------------------------------------------------------------
DEFAULT_TFS = ["日线", "60分钟", "15分钟", "5分钟"]
ALL_TFS = ["1分钟","5分钟","15分钟","60分钟","日线","周线","月线","季线"]
# (interval, period) for yfinance download
TF_PERIOD = {
    "1分钟": ("1m", "7d"), "5分钟": ("5m", "60d"), "15分钟": ("15m", "60d"),
    "60分钟": ("1h", "730d"), "日线": ("1d", "max"), "周线": ("1wk", "max"),
    "月线": ("1mo", "max"), "季线": ("3mo", "max"),
}

# 紧邻高周期映射：本周期 → 高周期（用于跨周期PnL参考子图）
TF_HIERARCHY = {
    "1分钟": "5分钟", "5分钟": "15分钟", "15分钟": "60分钟",
    "60分钟": "日线", "日线": "周线", "周线": "月线",
    "月线": "季线", "季线": None,
}

def _render_params(key, filter_id, dual, filter_id2, tf_default):
    """Ultra-compact parameter panel. Returns config dict."""
    cfg = {"_fid": filter_id, "_dual": dual, "_fid2": filter_id2}

    # Row 1: [周期▼] [N▬] [施密特☑] [预测☑] [▲▼]
    c1 = st.columns([1.0, 0.8, 0.8, 0.8, 0.4])
    with c1[0]:
        cfg["tf"] = st.selectbox("周期", ALL_TFS, index=ALL_TFS.index(tf_default),
            key=f"{key}_tf", label_visibility="collapsed")
    with c1[1]:
        cfg["n_pts"] = _compact_slider("N", 20, 300, 120, 10, key=f"{key}_n")
    with c1[2]:
        cfg["show_sch"] = st.checkbox("施密特", value=True, key=f"{key}_sch")
    cfg["ke"] = st.session_state.get(f"{key}_ke", st.session_state.get(f"_imp_{key}_ke", 0.15))
    cfg["sm"] = st.session_state.get(f"{key}_sm", st.session_state.get(f"_imp_{key}_sm", 0.05))
    cfg["ew"] = st.session_state.get(f"{key}_ew", st.session_state.get(f"_imp_{key}_ew", 60))
    cfg["show_pred"] = st.session_state.get(f"{key}_pred", st.session_state.get(f"_imp_{key}_pred", True))
    cfg["n_ext"] = st.session_state.get(f"{key}_next", st.session_state.get(f"_imp_{key}_next", 8))
    cfg["fit_mode"] = st.session_state.get(f"{key}_fm", st.session_state.get(f"_imp_{key}_fm", "parabola"))
    if cfg["show_sch"]:
        with c1[3]: cfg["show_pred"] = st.checkbox("预测", value=True, key=f"{key}_pred")

    # 本视图展开/折叠
    exp_key = f"{key}_exp_all"
    if exp_key not in st.session_state:
        st.session_state[exp_key] = False
    exp_all = st.session_state[exp_key]
    with c1[4]:
        label = "▲" if exp_all else "▼"
        if st.button(label, key=f"{key}_tgl", help="展开/折叠全部参数",
                     use_container_width=True):
            st.session_state[exp_key] = not exp_all
            st.rerun()

    # Schmitt ON → 折叠面板
    if cfg["show_sch"]:
        with st.expander("施密特参数", expanded=exp_all):
            c2 = st.columns([1.0, 1.0, 1.0])
            with c2[0]: cfg["ke"] = st.slider("k_ε", 0.01, 0.50, cfg["ke"], 0.01, key=f"{key}_ke",
                help="灵敏度系数,越小越敏感. ε_t=k_ε·max(σ_t(v),σ_min)")
            with c2[1]: cfg["sm"] = st.slider("σ_min", 0.001, 0.20, cfg["sm"], 0.001, key=f"{key}_sm", format="%.3f",
                help="地板保护,防止低波动下ε_t→0")
            with c2[2]: cfg["ew"] = st.slider("N_EWMA", 10, 120, cfg["ew"], 1, key=f"{key}_ew",
                help="EWMA周期,α=2/(N+1),越大越平滑。⚠️ 实际bar数(N)必须≥此值,否则无信号。"
                     "σ(v)估计精度≈1/√(2×N)。20 bar下建议N_EWMA≤15, 60 bar默认60")
        if cfg["show_pred"]:
            with st.expander("预测参数", expanded=exp_all):
                c3 = st.columns([1.5, 1.0])
                fit_key = f"{key}_fm"
                _fm_val = st.session_state.get(fit_key,
                    st.session_state.get(f"_imp_{fit_key}", "parabola"))
                fit_idx = 1 if _fm_val == "parabola" else 0
                with c3[0]: cfg["fit_mode"] = st.radio("拟合方式",
                    ["poly2", "parabola"], index=fit_idx, horizontal=True,
                    format_func=lambda x: "二次多项式" if x=="poly2" else "抛物线拟合",
                    key=fit_key)
                with c3[1]: cfg["n_ext"] = st.slider("预测点数", 1, 50, cfg["n_ext"], 1, key=f"{key}_next")
            # 策略参数 — 仅在开启预测时可用
            if cfg["show_pred"]:
                with st.expander("策略参数", expanded=exp_all):
                    st.markdown("""
                    <div style="font-size:12px; line-height:1.8; color:#8b949e;
                    background:rgba(88,166,255,0.06); border-radius:6px; padding:10px 14px;
                    border-left:3px solid #58a6ff;">
                    <b>📋 策略规则（分段混合 · 方案D）</b><br>
                    <b>预测保护期</b> <code>i∈[entry+1, entry+N<sub>ext</sub>]</code><br>
                    　　　 止损 <code>P<sub>t</sub>&lt;ŷ<sub>t</sub>·(1−s%)</code>（多）/<code>P<sub>t</sub>&gt;ŷ<sub>t</sub>·(1+s%)</code>（空）<br>
                    　　　 止盈 <code>Sig=-1</code>（多）/<code>Sig=+1</code>（空）<br>
                    <b>趋势跟踪期</b> <code>i∈[entry+N<sub>ext</sub>+1, …]</code><br>
                    　　　 仅止盈 <code>Sig</code> 反转离场，止损停用，让利润奔跑<br>
                    　　　 若始终未触发则持有至数据末尾<br>
                    <b>入场</b> <code>entry=pair_end</code>　做多需 <code>Sig=+1</code>且<code>ŷ<sub>end</sub>&gt;ŷ<sub>0</sub></code><br>
                    <b>曲线</b> <code>PnL<sub>t</sub>=capital·(1+未实现%)</code>　空仓期水平直线
                    </div>
                    """, unsafe_allow_html=True)
                    c_strat = st.columns([1.0, 1.0])
                    strat_key = f"{key}_strat"
                    sl_key = f"{key}_sl"
                    cross_key = f"{key}_cross_pnl"
                    with c_strat[0]:
                        cfg["show_strategy"] = st.checkbox(
                            "启用策略叠加", value=st.session_state.get(strat_key,
                                st.session_state.get(f"_imp_{strat_key}", False)),
                            key=strat_key,
                            help="在Sig子图下方显示基于预测曲线+施密特信号的策略PnL")
                        # checkbox必须在if/else外始终渲染，否则widget key会被Streamlit清理
                        cfg["show_cross_pnl"] = st.checkbox(
                            "显示高周期PnL参考", value=st.session_state.get(cross_key,
                                st.session_state.get(f"_imp_{cross_key}", False)),
                            key=cross_key, disabled=not cfg["show_strategy"],
                            help="在本周期PnL下方显示紧邻高周期的交易事件标记和PnL参考线")
                        # 同向性判断子图：高周期持仓时，本周期PnL按同向显示
                        align_key = f"{key}_align"
                        cfg["show_alignment"] = st.checkbox(
                            "显示同向性判断", value=st.session_state.get(align_key,
                                st.session_state.get(f"_imp_{align_key}", False)),
                            key=align_key,
                            disabled=not (cfg["show_strategy"] and cfg["show_cross_pnl"]),
                            help="高周期做多/空持仓时，本周期同向PnL才在子图体现，否则维持不变")
                    if cfg["show_strategy"]:
                        with c_strat[1]:
                            cfg["stop_loss_pct"] = st.slider(
                                "止损阈值(%)", 0.5, 10.0,
                                st.session_state.get(sl_key,
                                    st.session_state.get(f"_imp_{sl_key}", 2.0)), 0.1,
                                key=sl_key,
                                help="预测偏差超过此阈值即止损离场")
                    else:
                        cfg["stop_loss_pct"] = st.session_state.get(sl_key,
                            st.session_state.get(f"_imp_{sl_key}", 2.0))

    # 滤波参数 — 可折叠
    sf = FILTERS[filter_id]; cfg["pv"] = {}
    f1 = list(sf["params"].items())
    with st.expander(f"滤波参数 · {sf['name']}", expanded=exp_all):
        fc1 = st.columns([1]*len(f1) + [0.25])
        for j, (pn, sp) in enumerate(f1):
            with fc1[j]:
                cfg["pv"][pn] = _render_param_slider(*sp, key_suffix=f"{key}_f1_{filter_id}", container=st)
        with fc1[-1]:
            cfg["fc"] = st.color_picker("", "#00d4aa", key=f"{key}_fc", label_visibility="collapsed")

    # 滤波参数2（可选）
    if dual and filter_id2:
        sf2 = FILTERS[filter_id2]; cfg["pv2"] = {}
        f2 = list(sf2["params"].items())
        with st.expander(f"滤波参数2 · {sf2['name']}", expanded=exp_all):
            fc2 = st.columns([1]*len(f2) + [0.25])
            for j, (pn, sp) in enumerate(f2):
                with fc2[j]:
                    cfg["pv2"][pn] = _render_param_slider(*sp, key_suffix=f"{key}_f2_{filter_id2}", container=st)
            with fc2[-1]:
                cfg["fc2"] = st.color_picker("", "#ff6b6b", key=f"{key}_fc2", label_visibility="collapsed")
    else:
        cfg["pv2"] = {}; cfg["fc2"] = "#ff6b6b"

    # 从 session_state 读取最终值（导入参数唯一真相源，含_imp_备份防rerun丢失）
    cfg["ke"] = st.session_state.get(f"{key}_ke",
        st.session_state.get(f"_imp_{key}_ke", cfg["ke"]))
    cfg["sm"] = st.session_state.get(f"{key}_sm",
        st.session_state.get(f"_imp_{key}_sm", cfg["sm"]))
    cfg["ew"] = st.session_state.get(f"{key}_ew",
        st.session_state.get(f"_imp_{key}_ew", cfg["ew"]))
    cfg["show_pred"] = st.session_state.get(f"{key}_pred",
        st.session_state.get(f"_imp_{key}_pred", cfg["show_pred"]))
    cfg["fit_mode"] = st.session_state.get(f"{key}_fm",
        st.session_state.get(f"_imp_{key}_fm", cfg["fit_mode"]))
    cfg["n_ext"] = st.session_state.get(f"{key}_next",
        st.session_state.get(f"_imp_{key}_next", cfg["n_ext"]))
    # 滤波参数也需保护（含_imp_备份防rerun丢失）
    for pname in sf["params"]:
        label = sf["params"][pname][0]
        sk = f"{label}_{key}_f1_{filter_id}"
        cfg["pv"][pname] = st.session_state.get(sk,
            st.session_state.get(f"_imp_{sk}", cfg["pv"].get(pname, 0)))
    if dual and filter_id2:
        for pname in sf2["params"]:
            label = sf2["params"][pname][0]
            sk = f"{label}_{key}_f2_{filter_id2}"
            cfg["pv2"][pname] = st.session_state.get(sk,
                st.session_state.get(f"_imp_{sk}", cfg["pv2"].get(pname, 0)))

    cfg["show_strategy"] = st.session_state.get(f"{key}_strat",
        st.session_state.get(f"_imp_{key}_strat", cfg.get("show_strategy", False)))
    cfg["stop_loss_pct"] = st.session_state.get(f"{key}_sl",
        st.session_state.get(f"_imp_{key}_sl", cfg.get("stop_loss_pct", 2.0)))
    cfg["show_cross_pnl"] = st.session_state.get(f"{key}_cross_pnl",
        st.session_state.get(f"_imp_{key}_cross_pnl", cfg.get("show_cross_pnl", False)))
    cfg["show_alignment"] = st.session_state.get(f"{key}_align",
        st.session_state.get(f"_imp_{key}_align", cfg.get("show_alignment", False)))
    # 颜色值在可折叠面板内，折叠时需从session_state恢复
    cfg["fc"] = st.session_state.get(f"{key}_fc",
        st.session_state.get(f"_imp_{key}_fc", cfg.get("fc", "#00d4aa")))
    if dual and filter_id2:
        cfg["fc2"] = st.session_state.get(f"{key}_fc2",
            st.session_state.get(f"_imp_{key}_fc2", cfg.get("fc2", "#ff6b6b")))

    return cfg


def _render_chart(market, ticker_code, cfg, key, compact=True, day_offset=0, higher_pnl=None):
    """Fetch data + render multi-subplot figure from config.
    优先从本地 Parquet 读取；day_offset=向历史前移N天（各周期独立对齐）。
    higher_pnl: 高周期PnL数据（来自 _align_pnl_to_current_tf 的输出），非空时新增row 7子图。"""
    tf = cfg["tf"]
    n_pts = cfg["n_pts"]

    # 查找紧邻高周期tf，尝试从session_state获取其PnL数据
    _higher_tf = TF_HIERARCHY.get(tf)
    _raw_higher = None
    if higher_pnl is None and _higher_tf is not None:
        _raw_higher = st.session_state.get(f"_pnl_{_higher_tf}")

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

    # 高周期PnL数据对齐（从session_state获取的原始数据，需要时间戳对齐）
    if _raw_higher is not None and dates is not None:
        higher_pnl = _align_pnl_to_current_tf(
            _raw_higher["dates"], _raw_higher["long_pnl"], _raw_higher["short_pnl"],
            _raw_higher["trade_records"], dates,
        )
    elif higher_pnl is None:
        higher_pnl = None

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

    # 约束检查：bar数不足时提示用户
    if cfg["show_sch"] and schmitt is None and len(t) > 0:
        st.warning(f"⚠️ 施密特信号不可用：bar数({len(t)}) < N_EWMA({cfg['ew']})。"
                   f"请降低 N_EWMA 至 ≤{len(t)} 或增加数据点数(N)。")

    # 多空切换对 — 始终计算（用于预测曲线 + Sig_t 背景标记）
    all_pairs = []
    if schmitt is not None:
        all_pairs = _find_all_pairs(schmitt["sig"])

    # 预测曲线 — 窗口中所有多空对，全段拟合 + 前向预测
    pred_pairs = []
    if cfg.get("show_pred") and schmitt is not None:
        fit_func = _fit_physics_parabola if cfg.get("fit_mode") == "parabola" else _fit_parabolic
        for pair_start, pair_end in all_pairs:
            if pair_end - pair_start >= 3:  # 需 ≥3 点
                fit_result = fit_func(t, filtered, pair_start, pair_end)
                if fit_result is not None:
                    pred_pairs.append({
                        "fit_result": fit_result,
                        "fit_start": pair_start,
                        "pair_end": pair_end,
                    })

    # 策略PnL计算（两条独立曲线）
    long_pnl = None
    short_pnl = None
    trade_records = []
    show_strategy = cfg.get("show_strategy", False)
    show_cross_pnl = cfg.get("show_cross_pnl", False)
    show_alignment = cfg.get("show_alignment", False)
    stop_loss_pct = cfg.get("stop_loss_pct", 2.0)
    if show_strategy and schmitt is not None and len(pred_pairs) > 0:
        long_pnl, short_pnl, trade_records = _compute_strategy_pnl(
            t, filtered, schmitt["sig"], all_pairs, pred_pairs, stop_loss_pct,
            n_extend=cfg.get("n_ext", 10),
        )

    # 策略统计（取做多/做空最终收益更优者展示）
    has_strategy = show_strategy and long_pnl is not None and len(trade_records) > 0
    if has_strategy and trade_records:
        c4, c5, c6 = st.columns(3)
        win_trades = sum(1 for tr in trade_records if tr["return_pct"] > 0)
        long_ret = long_pnl[-1] - 100.0
        short_ret = short_pnl[-1] - 100.0
        total_ret = long_ret + short_ret
        c4.caption(f"交易: {len(trade_records)}笔 | 胜率: {win_trades}/{len(trade_records)}")
        c5.caption(f"多: {long_ret:+.2f}% | 空: {short_ret:+.2f}% | 总和: {total_ret:+.2f}%")
        # 做多回撤
        peak_l = np.maximum.accumulate(long_pnl)
        drawdown_l = (long_pnl - peak_l) / peak_l * 100
        max_dd_l = np.min(drawdown_l)
        # 做空回撤
        peak_s = np.maximum.accumulate(short_pnl)
        drawdown_s = (short_pnl - peak_s) / peak_s * 100
        max_dd_s = np.min(drawdown_s)
        c6.caption(f"多DD: {max_dd_l:.2f}% | 空DD: {max_dd_s:.2f}%")

        # 缓存本周期PnL数据到session_state，供低周期视图的跨周期子图使用
        if has_strategy and trade_records:
            st.session_state[f"_pnl_{tf}"] = {
                "dates": dates, "t": t,
                "long_pnl": long_pnl, "short_pnl": short_pnl,
                "trade_records": trade_records,
            }

    # Build figure (same as before, compact)
    has_s = schmitt is not None
    # 跨周期PnL子图：用户开启 + 有对齐数据 + 有交易事件时显示
    has_cross = (show_cross_pnl and higher_pnl is not None and
                 (len(higher_pnl.get("entry_markers", [])) > 0 or
                  len(higher_pnl.get("exit_markers", [])) > 0))
    # 同向性判断子图：高周期PnL数据可用 + 本周期有策略PnL
    _align_masks = None
    if show_alignment and has_cross and has_strategy and long_pnl is not None:
        _align_masks = _compute_holding_masks(
            len(t), higher_pnl["entry_markers"], higher_pnl["exit_markers"])
    has_alignment = (_align_masks is not None and
                     (_align_masks[0].any() or _align_masks[1].any()))
    if has_s:
        if has_strategy:
            if has_cross:
                if has_alignment:
                    rows = 8
                    rh = [0.24, 0.11, 0.12, 0.12, 0.16, 0.24, 0.15, 0.12]
                    titles = ("价格&滤波","残差","速度v","a&±ε","Sig_t","PnL收益(%)",f"{_higher_tf}PnL参考","同向性判断")
                    pnl_row = 6; cross_row = 7; align_row = 8
                else:
                    rows = 7
                    rh = [0.24, 0.11, 0.12, 0.12, 0.16, 0.27, 0.18]
                    titles = ("价格&滤波","残差","速度v","a&±ε","Sig_t","PnL收益(%)",f"{_higher_tf}PnL参考")
                    pnl_row = 6; cross_row = 7; align_row = None
            else:
                rows = 6
                rh = [0.24, 0.11, 0.12, 0.12, 0.16, 0.375]
                titles = ("价格&滤波","残差","速度v","a&±ε","Sig_t","PnL收益(%)")
                pnl_row = 6; cross_row = None; align_row = None
        else:
            rows = 5
            rh = [0.28, 0.14, 0.18, 0.18, 0.22]
            titles = ("价格&滤波","残差","速度v","a&±ε","Sig_t")
            pnl_row = None; cross_row = None; align_row = None
        mr=1;rr=2;vr=3;sar=4;ssr=5
        ar=None
    else:
        rows = 4
        rh=[0.40,0.18,0.20,0.22]; titles=("价格&滤波","残差","速度v","加速度a")
        mr=1;rr=2;vr=3;ar=4;sar=ssr=pnl_row=cross_row=align_row=None
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True,
                        vertical_spacing=0.01, row_heights=rh, subplot_titles=titles)
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
        _add_prediction_traces(fig, t, filtered,
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
    # PnL收益曲线（第6行）— 两条独立曲线
    if has_strategy:
        # 做多曲线（绿色）
        fig.add_trace(go.Scatter(
            x=t, y=long_pnl,
            mode="lines", name="做多PnL",
            line=dict(color="#3fb950", width=1.5, dash="solid"),
        ), row=pnl_row, col=1)

        # 做空曲线（红色）
        fig.add_trace(go.Scatter(
            x=t, y=short_pnl,
            mode="lines", name="做空PnL",
            line=dict(color="#f85149", width=1.5, dash="solid"),
        ), row=pnl_row, col=1)

        # 持仓期间高亮（交易分段覆盖）
        for trade in trade_records:
            seg_t = t[trade["entry_idx"]:trade["exit_idx"] + 1]
            curve = long_pnl if trade["type"] == "long" else short_pnl
            seg_pnl = curve[trade["entry_idx"]:trade["exit_idx"] + 1]
            is_long = trade["type"] == "long"
            color = "#3fb950" if is_long else "#f85149"
            label_prefix = "多" if is_long else "空"
            fig.add_trace(go.Scatter(
                x=seg_t, y=seg_pnl,
                mode="lines",
                name=f"{label_prefix}#{trade['id']}",
                line=dict(color=color, width=3),
                showlegend=False,
            ), row=pnl_row, col=1)

            # 入场标记
            marker_color = "#3fb950" if is_long else "#f85149"
            fig.add_trace(go.Scatter(
                x=[seg_t[0]], y=[seg_pnl[0]],
                mode="markers",
                marker=dict(color=marker_color, symbol="triangle-up", size=8),
                showlegend=False,
            ), row=pnl_row, col=1)

            # 离场标记: 仅真实触发时显示（止损=X红色, 止盈=○绿色）
            # eod/未触发不画标记, 避免误导
            if trade["exit_reason"] in ("stop_loss", "take_profit"):
                exit_marker = "x" if trade["exit_reason"] == "stop_loss" else "circle"
                exit_color = "#f85149" if trade["exit_reason"] == "stop_loss" else "#3fb950"
                fig.add_trace(go.Scatter(
                    x=[seg_t[-1]], y=[seg_pnl[-1]],
                    mode="markers",
                    marker=dict(color=exit_color, symbol=exit_marker, size=8),
                    showlegend=False,
                ), row=pnl_row, col=1)
                # 盈亏百分比标注
                ret_pct = trade["return_pct"]
                label_color = "#f85149" if trade["exit_reason"] == "stop_loss" else "#3fb950"
                arrow = "↑" if trade["type"] == "long" else "↓"
                fig.add_annotation(
                    x=seg_t[-1], y=seg_pnl[-1],
                    text=f"{arrow}{ret_pct:+.1f}%",
                    showarrow=False,
                    font=dict(size=8, color=label_color),
                    yshift=12,
                    row=pnl_row, col=1,
                )

        # 100%基准线
        fig.add_hline(y=100, line_dash="dash", line_color="gray",
                      opacity=0.5, row=pnl_row, col=1)

        # 做多盈利区域填充（浅绿）
        y_max_l = max(float(np.nanmax(long_pnl)), 100.0) * 1.02
        fig.add_trace(go.Scatter(
            x=[t[0], t[-1], t[-1], t[0]],
            y=[100, 100, y_max_l, y_max_l],
            fill="toself", fillcolor="rgba(63,185,80,0.04)",
            mode="lines", line=dict(width=0),
            showlegend=False, hoverinfo="skip",
        ), row=pnl_row, col=1)
        # 做空亏损区域填充（浅红）
        y_min_s = min(float(np.nanmin(short_pnl)), 100.0) * 0.98
        fig.add_trace(go.Scatter(
            x=[t[0], t[-1], t[-1], t[0]],
            y=[100, 100, y_min_s, y_min_s],
            fill="toself", fillcolor="rgba(248,81,73,0.04)",
            mode="lines", line=dict(width=0),
            showlegend=False, hoverinfo="skip",
        ), row=pnl_row, col=1)

    if has_strategy:
        fig.update_yaxes(title_text="PnL(%)", row=pnl_row, col=1,
                         ticksuffix="%")

    # 跨周期PnL参考子图（row 7）
    if has_cross and higher_pnl is not None and cross_row is not None:
        _add_cross_pnl_subplot(fig, t, higher_pnl, row=cross_row, higher_tf=_higher_tf)
        fig.update_yaxes(title_text=f"{_higher_tf}(%)", row=cross_row, col=1,
                         ticksuffix="%")

    # 同向性判断子图（row 8）：高周期持仓时显示本周期同向PnL
    if has_alignment and _align_masks is not None and align_row is not None:
        long_mask, short_mask = _align_masks
        _add_alignment_subplot(fig, t, long_pnl, short_pnl, trade_records,
                               long_mask, short_mask, row=align_row)
        fig.update_yaxes(title_text="同向(%)", row=align_row, col=1, ticksuffix="%")

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
    fh = (620 if has_s else 420) if compact else (960 if has_s else 700)
    if has_cross:
        fh += 120  # 跨周期子图额外高度
    if has_alignment:
        fh += 75  # 同向性子图额外高度
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
    init_config_tables()
    import_json_files_as_presets()  # 首次运行导入已有 JSON
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
                    st.session_state[f"_imp_{k}"] = v  # 非widget备份，防rerun丢失
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

    # ── 配置方案预设选择器 ──
    st.sidebar.markdown("---")
    presets = list_presets()
    preset_labels = ["(不选择)"] + [f"[{p['category']}] {p['name']}" for p in presets]
    # P1-3: 使用完整 label (含分类) 做 key，避免同名不同类预设匹配错误
    preset_map = {f"[{p['category']}] {p['name']}": p for p in presets}

    # 基于选项列表内容生成动态 key — 数据变了 key 自动变，widget 自然重置
    _hash = hashlib.md5("|".join(preset_labels).encode()).hexdigest()[:8]
    selected_label = st.sidebar.selectbox("📋 配置方案", preset_labels,
                                          key=f"preset_sel_{_hash}")

    selected_preset = preset_map.get(selected_label)

    if selected_preset:
        p = selected_preset
        st.sidebar.caption(f"💡 {p['description']}  [{p['category']}]")

        # 按钮行：应用 + 更新 + 重命名 + 删除
        c1, c2, c3, c4 = st.sidebar.columns([1.2, 1, 1, 0.8])
        with c1:
            if st.button("✅ 应用", key="apply_preset", use_container_width=True):
                params = apply_preset(p["preset_id"])
                if params:
                    for k, v in params.items():
                        st.session_state[k] = v
                        st.session_state[f"_imp_{k}"] = v
                    st.session_state._import_data = "preset"
                    st.toast(f"已应用: {p['name']}")
                    st.rerun()

        # P0-1: 用 session_state 标志替代 st.popover，避免 rerun 后 popover 关闭
        # 导致按钮从不触发的问题。
        with c2:
            if st.button("📝 更新", key="update_preset_btn", use_container_width=True):
                st.session_state._preset_action = "update"
                st.session_state._preset_action_id = p["preset_id"]
        with c3:
            if st.button("✏️ 重命名", key="rename_preset_btn", use_container_width=True):
                st.session_state._preset_action = "rename"
                st.session_state._preset_action_id = p["preset_id"]
        with c4:
            if st.button("🗑️ 删除", key="delete_preset_btn", use_container_width=True):
                st.session_state._preset_action = "delete"
                st.session_state._preset_action_id = p["preset_id"]

    # P0-1: 确认操作 UI（在按钮行外部渲染，不受 popover 生命周期影响）
    _action = st.session_state.get("_preset_action")
    _action_id = st.session_state.get("_preset_action_id")
    if _action and _action_id is not None:
        target = next((p for p in presets if p["preset_id"] == _action_id), None)
        if target is None:
            # 预设已被删除或不存在，清除标志
            st.session_state.pop("_preset_action", None)
            st.session_state.pop("_preset_action_id", None)
            st.rerun()
        elif _action == "update":
            st.sidebar.warning(f"将当前参数覆盖到 **{target['name']}**？")
            st.sidebar.caption("这会将当前所有参数写入该预设。")
            cc1, cc2 = st.sidebar.columns(2)
            with cc1:
                if st.button("确认覆盖", key="update_confirm_btn", use_container_width=True):
                    from config_db import collect_current_params
                    import json as _json
                    params = collect_current_params()
                    save_preset(target["name"],
                                _json.dumps(params, ensure_ascii=False),
                                description=target.get("description", ""),
                                category=target.get("category", "通用"))
                    st.toast(f"已更新: {target['name']}")
                    st.session_state.pop("_preset_action", None)
                    st.session_state.pop("_preset_action_id", None)
                    st.rerun()
            with cc2:
                if st.button("取消", key="update_cancel_btn", use_container_width=True):
                    st.session_state.pop("_preset_action", None)
                    st.session_state.pop("_preset_action_id", None)
                    st.rerun()
        elif _action == "rename":
            st.sidebar.caption(f"重命名 **{target['name']}**")
            new_name = st.sidebar.text_input("新名称", value=target["name"],
                                             key="rename_input_val")
            cc1, cc2 = st.sidebar.columns(2)
            with cc1:
                if st.button("确认重命名", key="rename_confirm_btn", use_container_width=True):
                    if new_name.strip() and new_name.strip() != target["name"]:
                        rename_preset(target["preset_id"], new_name.strip())
                        st.toast(f"已重命名: {target['name']} → {new_name.strip()}")
                        st.session_state.pop("_preset_action", None)
                        st.session_state.pop("_preset_action_id", None)
                        st.rerun()
                    elif new_name.strip() == target["name"]:
                        st.warning("名称未变化")
                    else:
                        st.error("名称不能为空")
            with cc2:
                if st.button("取消", key="rename_cancel_btn", use_container_width=True):
                    st.session_state.pop("_preset_action", None)
                    st.session_state.pop("_preset_action_id", None)
                    st.rerun()
        elif _action == "delete":
            st.sidebar.error(f"确认删除 **{target['name']}**？此操作不可恢复。")
            cc1, cc2 = st.sidebar.columns(2)
            with cc1:
                if st.button("确认删除", key="delete_confirm_btn", use_container_width=True):
                    delete_preset(target["preset_id"])
                    st.toast(f"已删除: {target['name']}")
                    st.session_state.pop("_preset_action", None)
                    st.session_state.pop("_preset_action_id", None)
                    st.rerun()
            with cc2:
                if st.button("取消", key="delete_cancel_btn", use_container_width=True):
                    st.session_state.pop("_preset_action", None)
                    st.session_state.pop("_preset_action_id", None)
                    st.rerun()

    # 保存当前为预设
    with st.sidebar.expander("💾 保存 / 另存为预设", expanded=False):
        # P0-2: 在 text_input 渲染前同步 session_state 值，
        # 避免静态 key 导致 value= 参数在预设切换后被忽略。
        if "_last_sel_name" not in st.session_state:
            st.session_state._last_sel_name = ""
        curr_sel_name = selected_preset["name"] if selected_preset else ""
        if st.session_state._last_sel_name != curr_sel_name:
            st.session_state.new_preset_name = (
                curr_sel_name + "_副本" if curr_sel_name else "")
            st.session_state._last_sel_name = curr_sel_name

        new_name = st.text_input("预设名称", key="new_preset_name",
                                 placeholder="如: 我的港股配置")
        new_desc = st.text_input("描述(可选)", key="new_preset_desc",
                                 placeholder="港股·短线·savgol")
        overwrite = False
        if selected_preset:
            overwrite = st.checkbox(f"覆盖「{selected_preset['name']}」",
                                    key="overwrite_preset")
        if st.button("💾 保存", key="save_preset_btn", use_container_width=True):
            if new_name.strip():
                from config_db import collect_current_params
                import json as _json
                params = collect_current_params()
                target_name = (selected_preset["name"]
                               if overwrite and selected_preset
                               else new_name.strip())
                # P1-2: 补充 category 参数，覆盖时保留原分类
                cat = (selected_preset.get("category", "通用")
                       if overwrite and selected_preset else "通用")
                save_preset(target_name,
                            _json.dumps(params, ensure_ascii=False),
                            description=(new_desc.strip() if not overwrite
                                         else selected_preset.get("description", "")),
                            category=cat)
                st.toast(f"已保存: {target_name}")
                # P2-1: 保存成功后清除 overwrite checkbox 状态残留
                st.session_state.overwrite_preset = False
                st.rerun()
            else:
                st.error("请输入预设名称")

    st.sidebar.markdown("---")
    # ── 数据健康检查 ──
    with st.sidebar.expander("🩺 数据健康检查", expanded=False):
        if st.button("运行检查", key="health_btn", use_container_width=True) and ticker_code:
            with st.spinner("检查数据完整性..."):
                report = check_data_health(ticker_code)
            status = report.get("status", "ok")
            color_map = {"ok": "green", "warn": "orange", "error": "red"}
            color = color_map.get(status, "gray")
            st.markdown(f"**:{color}[状态: {report.get('summary', status)}]**")
            if report.get("issues"):
                for issue in report["issues"]:
                    st.warning(issue)
            if report.get("details"):
                detail_df = pd.DataFrame(report["details"])
                st.dataframe(detail_df, use_container_width=True, hide_index=True,
                             height=min(35 * len(detail_df) + 38, 300))

    # ── 数据校验：DB vs 数据源（全部周期） ──
    with st.sidebar.expander("📋 数据校验", expanded=False):
        st.caption("对比数据库与 yfinance 全部周期，发现历史数据修正")

        if st.button("校验全部周期", key="val_btn", use_container_width=True) and ticker_code:
            # Build ticker symbol once
            if market == "A股(沪深)":
                full_code = ticker_code + (".SS" if ticker_code[0] == "6" else ".SZ")
            elif market == "港股 HK":
                full_code = ticker_code.zfill(4) + ".HK"
            else:
                full_code = ticker_code.upper()

            rows = []
            has_conflict = False
            has_update = False

            for tf in ALL_TFS:
                interval, period = TF_PERIOD[tf]
                with st.spinner(f"校验 {tf} ..."):
                    try:
                        data = yf.download(full_code, period=period, interval=interval,
                                           progress=False)
                        if data.empty or len(data[data["Close"].notna()]) < 5:
                            rows.append({"周期": tf, "DB": "-", "yf": "-", "重叠": "-",
                                         "指纹": "⚠️ 数据不足", "仅DB": "-", "仅yf": "-", "操作": ""})
                            continue
                        data = data[data["Close"].notna()]
                        report = compare_with_db(ticker_code, tf, data)
                    except Exception as e:
                        rows.append({"周期": tf, "DB": "-", "yf": "-", "重叠": "-",
                                     "指纹": f"❌ {str(e)[:30]}", "仅DB": "-", "仅yf": "-", "操作": ""})
                        continue

                db_c = report["db_count"]
                yf_c = report["yf_count"]
                fp = "✅" if report["fingerprint_match"] else "❌"
                status = report["status"]

                if status == "conflict":
                    has_conflict = True
                elif status == "update_available":
                    has_update = True

                rows.append({
                    "周期": tf,
                    "DB": db_c,
                    "yf": yf_c,
                    "重叠": report["overlap_count"],
                    "指纹": fp,
                    "仅DB": report["only_db"],
                    "仅yf": report["only_yf"],
                    "操作": status,  # temp store for button logic
                })

            # Summary table
            if rows:
                import pandas as _pd
                df = _pd.DataFrame(rows)
                # Color-code rows based on status
                def _row_style(r):
                    s = r["差异"]
                    if s == "conflict":
                        return ["background-color: #fff3cd"] * len(r)
                    elif s == "update_available":
                        return ["background-color: #d4edda"] * len(r)
                    return [""] * len(r)

                df_display = df.rename(columns={"操作": "差异"})
                df_display["差异"] = df_display["差异"].replace({
                    "conflict": "⚠️ 数据冲突", "update_available": "有新数据",
                    "ok": "✅ 一致",
                })
                styled = df_display.style.apply(_row_style, axis=1)
                st.dataframe(styled, use_container_width=True, hide_index=True,
                             height=min(35 * len(df) + 38, 350))

                # Legend
                st.caption("🟡 黄底 = 历史数据被修正 | 🟢 绿底 = 有新增数据")

                # Batch update button if any conflicts or updates
                if has_conflict or has_update:
                    if st.button("⚠️ 更新全部有差异的周期", key="force_update_all",
                                 use_container_width=True):
                        updated = 0
                        for _, r in df.iterrows():
                            s = r["操作"]
                            if s in ("conflict", "update_available"):
                                tf = r["周期"]
                                try:
                                    interval, period = TF_PERIOD[tf]
                                    data = yf.download(full_code, period=period,
                                                       interval=interval, progress=False)
                                    if not data.empty:
                                        data = data[data["Close"].notna()]
                                        force_update_kline(ticker_code, tf, data)
                                        updated += 1
                                except Exception:
                                    pass
                        if updated > 0:
                            _fetch_stock.clear()
                            clear_display_cache()
                            st.success(f"已更新 {updated} 个周期，页面将刷新")
                            time.sleep(0.5)
                            st.rerun()
                        else:
                            st.warning("没有周期被更新")

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

    # ── 数据备份与恢复 ──
    with st.sidebar.expander("💾 数据备份与恢复", expanded=False):
        db_size = get_db_size_mb()
        st.caption(f"数据库: {DB_PATH.name} ({db_size:.1f} MB)")

        c_s1, c_s2 = st.columns([1, 1])
        with c_s1:
            if st.button("创建备份", key="snap_btn", use_container_width=True):
                try:
                    path = snapshot_db()
                    prune_snapshots(max_keep=5)
                    st.success(f"已创建: {Path(path).name}")
                except Exception as e:
                    st.error(f"备份失败: {e}")

        snapshots = list_snapshots()
        with c_s2:
            snap_count = len(snapshots)
            st.caption(f"共 {snap_count} 个备份" if snap_count else "暂无备份")

        if snapshots:
            snap_labels = [s[3] for s in snapshots]
            selected_idx = st.selectbox(
                "选择备份", range(len(snap_labels)),
                format_func=lambda i: snap_labels[i],
                key="restore_select"
            )
            c_r1, c_r2 = st.columns([1, 1])
            with c_r1:
                if st.button("恢复到此备份", key="restore_btn", use_container_width=True):
                    try:
                        restore_snapshot(snapshots[selected_idx][0])
                        _fetch_stock.clear()
                        clear_display_cache()
                        st.session_state._fetched_ticker = ""
                        st.success("已恢复，页面将刷新")
                        time.sleep(0.5)
                        st.rerun()
                    except Exception as e:
                        st.error(f"恢复失败: {e}")
            with c_r2:
                if st.button("删除此备份", key="del_snap_btn", use_container_width=True):
                    try:
                        os.remove(snapshots[selected_idx][0])
                        st.rerun()
                    except Exception as e:
                        st.error(f"删除失败: {e}")

    # ---- Pass 2: Bottom 2x2 chart views ----
    # 按时间框架从高到低排序渲染，确保低周期视图能获取高周期PnL缓存
    # 先创建2×2网格容器，再按高周期优先顺序填充

    grid_cols = []
    for row_idx in range(2):
        c1, c2 = st.columns(2)
        grid_cols.append((c1, c2))

    sorted_views = sorted(enumerate(configs),
                          key=lambda x: ALL_TFS.index(x[1]["tf"]), reverse=True)
    for orig_i, cfg in sorted_views:
        row_idx = orig_i // 2
        col_idx = orig_i % 2
        with grid_cols[row_idx][col_idx]:
            _render_chart(market, ticker_code, cfg, f"v{orig_i}", compact=True,
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
        export_data[f"v{i}_pred"] = cfg["show_pred"]
        export_data[f"v{i}_ke"] = cfg["ke"]
        export_data[f"v{i}_sm"] = cfg["sm"]
        export_data[f"v{i}_ew"] = cfg["ew"]
        export_data[f"v{i}_fm"] = cfg["fit_mode"]
        export_data[f"v{i}_next"] = cfg["n_ext"]
        export_data[f"v{i}_fc"] = cfg["fc"]
        export_data[f"v{i}_fc2"] = cfg["fc2"]
        export_data[f"v{i}_strat"] = cfg.get("show_strategy", False)
        export_data[f"v{i}_sl"] = cfg.get("stop_loss_pct", 2.0)
        export_data[f"v{i}_cross_pnl"] = cfg.get("show_cross_pnl", False)
        export_data[f"v{i}_align"] = cfg.get("show_alignment", False)
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

    # ── 配置历史 ──
    if ticker_code:
        st.sidebar.markdown("---")
        with st.sidebar.expander("📜 配置历史", expanded=False):
            records = get_history(ticker_code, variant="single", limit=10)
            if records:
                for rec in records:
                    source_icon = {"ui": "✏️", "import": "📥", "preset_apply": "📋", "rollback": "↩️"}
                    icon = source_icon.get(rec.get("source", ""), "📌")
                    st.caption(f"{icon} {rec['changed_at']} — {rec.get('preset_name') or rec['source']}")
            else:
                st.caption("暂无记录")

    # ── 数据库导入/导出 ──
    st.sidebar.markdown("---")
    with st.sidebar.expander("📦 数据库导入/导出", expanded=False):
        st.caption("导出整个数据库到文件，可在其他设备导入")

        # Export
        try:
            checkpoint_wal()
        except Exception:
            pass
        try:
            db_bytes = DB_PATH.read_bytes()
            st.download_button(
                "导出数据库", db_bytes,
                file_name="market.db", mime="application/octet-stream",
                use_container_width=True,
                help=f"文件大小: {len(db_bytes) / 1024 / 1024:.1f} MB"
            )
        except Exception as e:
            st.error(f"导出失败: {e}")

        # Import
        uploaded_db = st.file_uploader(
            "导入数据库", type=["db"],
            key="db_import", label_visibility="collapsed"
        )
        if uploaded_db is not None:
            raw = uploaded_db.read()
            file_hash = hashlib.md5(raw).hexdigest()
            if st.session_state.get("_db_import_hash") != file_hash:
                with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                    tmp.write(raw)
                    tmp_path = tmp.name
                valid, err_msg = validate_db(tmp_path)
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

                if not valid:
                    st.error(f"无效的数据库文件: {err_msg}")
                else:
                    DB_PATH.write_bytes(raw)
                    for suffix in ["-wal", "-shm"]:
                        p = str(DB_PATH) + suffix
                        if os.path.exists(p):
                            os.remove(p)
                    _fetch_stock.clear()
                    clear_display_cache()
                    st.session_state._fetched_ticker = ""
                    st.session_state._db_import_hash = file_hash
                    st.success(f"数据库已导入 ({len(raw) / 1024 / 1024:.1f} MB)，页面将刷新")
                    time.sleep(0.5)
                    st.rerun()

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
