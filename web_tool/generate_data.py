#!/usr/bin/env python3
"""Generate sample signal data for the interactive filter visualization web tool."""

import json
import numpy as np

FS = 100  # sample rate (Hz)
RNG = np.random.RandomState(42)  # reproducible


def _round(arr, decimals=6):
    """Round array values and convert to Python list for JSON serialization."""
    return np.round(arr, decimals).tolist()


def dataset_sinusoid(n, fs=FS):
    """5Hz sine + Gaussian noise, SNR ~3dB (σ=0.5 for unit-amplitude sine)."""
    t = np.arange(n) / fs
    clean = np.sin(2 * np.pi * 5 * t)
    noise = RNG.randn(n) * 0.5
    noisy = clean + noise
    return t, clean, noisy


def dataset_step(n, fs=FS):
    """Step 0→1 at midpoint + Gaussian noise σ=0.15."""
    t = np.arange(n) / fs
    mid = n // 2
    clean = np.zeros(n)
    clean[mid:] = 1.0
    noise = RNG.randn(n) * 0.15
    noisy = clean + noise
    return t, clean, noisy


def dataset_trend_seasonal(n, fs=FS):
    """Linear trend + 3Hz seasonal + AR(1) noise (φ=0.7)."""
    t = np.arange(n) / fs
    trend = 0.002 * t
    seasonal = 0.3 * np.sin(2 * np.pi * 3 * t)

    # AR(1) noise: x[i] = φ * x[i-1] + w[i], w ~ N(0, σ²)
    phi = 0.7
    sigma_w = 0.05
    w = RNG.randn(n) * sigma_w
    ar_noise = np.zeros(n)
    ar_noise[0] = w[0]
    for i in range(1, n):
        ar_noise[i] = phi * ar_noise[i - 1] + w[i]

    clean = trend + seasonal
    noisy = clean + ar_noise
    return t, clean, noisy


def dataset_impulse(n, fs=FS):
    """Gaussian pulse at t=1s on sinusoidal baseline + noise σ=0.1."""
    t = np.arange(n) / fs
    clean_baseline = 0.5 * np.sin(2 * np.pi * 3 * t)

    sigma_pulse = 0.02
    amp = 2.0
    pulse = amp * np.exp(-0.5 * ((t - 1.0) / sigma_pulse) ** 2)

    clean = clean_baseline + pulse
    noise = RNG.randn(n) * 0.1
    noisy = clean + noise
    return t, clean, noisy


def dataset_chirp(n, fs=FS):
    """Linear frequency sweep 1Hz → 20Hz with negligible noise."""
    t = np.arange(n) / fs
    f0, f1 = 1.0, 20.0
    T = t[-1]
    # integral of linear frequency gives quadratic phase
    phase = 2 * np.pi * (f0 * t + (f1 - f0) * t ** 2 / (2 * T))
    clean = np.sin(phase)
    # tiny noise for UI consistency (both clean and noisy fields present)
    noise = RNG.randn(n) * 0.02
    noisy = clean + noise
    return t, clean, noisy


# ---------------------------------------------------------------------------
# Build the full JSON structure
# ---------------------------------------------------------------------------

DATASET_GENERATORS = {
    "sinusoid": {
        "name": "正弦波 + 高斯噪声",
        "description": "5Hz 正弦信号叠加高斯白噪声，SNR ≈ 3dB",
        "defaultFilter": "savgol",
        "defaultParams": {"window": 21, "order": 3},
        "gen": dataset_sinusoid,
    },
    "step": {
        "name": "阶跃信号 + 噪声",
        "description": "中点阶跃 0→1 叠加高斯白噪声 σ=0.15",
        "defaultFilter": "median",
        "defaultParams": {"window": 11},
        "gen": dataset_step,
    },
    "trend_seasonal": {
        "name": "趋势 + 季节成分",
        "description": "线性趋势 (0.002/s) + 3Hz 季节成分 (幅值 0.3) + AR(1) 噪声 φ=0.7",
        "defaultFilter": "butterworth",
        "defaultParams": {"order": 4, "cutoff": 8.0},
        "gen": dataset_trend_seasonal,
    },
    "impulse": {
        "name": "脉冲 + 正弦基线",
        "description": "t=1s 处高斯脉冲 (σ=0.02, 幅值 2.0) 叠加在 3Hz 正弦基线上，含 σ=0.1 噪声",
        "defaultFilter": "gaussian",
        "defaultParams": {"sigma": 3.0},
        "gen": dataset_impulse,
    },
    "chirp": {
        "name": "线性扫频 (Chirp)",
        "description": "频率从 1Hz 线性扫描至 20Hz，含微弱噪声 σ=0.02",
        "defaultFilter": "butterworth",
        "defaultParams": {"order": 6, "cutoff": 15.0},
        "gen": dataset_chirp,
    },
}

FILTER_DEFINITIONS = [
    {
        "id": "sma",
        "name": "简单移动平均 (SMA)",
        "params": [
            {"name": "window", "type": "int", "min": 3, "max": 101, "step": 2, "default": 11,
             "label": "窗口大小"},
        ],
    },
    {
        "id": "ema",
        "name": "指数移动平均 (EMA)",
        "params": [
            {"name": "span", "type": "int", "min": 2, "max": 100, "step": 1, "default": 10,
             "label": "衰减跨度"},
        ],
    },
    {
        "id": "wma",
        "name": "加权移动平均 (WMA)",
        "params": [
            {"name": "window", "type": "int", "min": 3, "max": 101, "step": 2, "default": 11,
             "label": "窗口大小"},
        ],
    },
    {
        "id": "alma",
        "name": "阿恩霍德-马蒂斯移动平均 (ALMA)",
        "params": [
            {"name": "window", "type": "int", "min": 3, "max": 101, "step": 2, "default": 21,
             "label": "窗口大小"},
            {"name": "offset", "type": "float", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.85,
             "label": "偏移量"},
            {"name": "sigma", "type": "float", "min": 1.0, "max": 20.0, "step": 0.5, "default": 6.0,
             "label": "标准差"},
        ],
    },
    {
        "id": "savgol",
        "name": "Savitzky-Golay 滤波",
        "params": [
            {"name": "window", "type": "int", "min": 5, "max": 101, "step": 2, "default": 21,
             "label": "窗口大小"},
            {"name": "order", "type": "int", "min": 1, "max": 5, "step": 1, "default": 3,
             "label": "多项式阶数"},
        ],
    },
    {
        "id": "kalman",
        "name": "卡尔曼滤波 (1D)",
        "params": [
            {"name": "Q", "type": "float", "min": 0.001, "max": 1.0, "step": 0.01, "default": 0.01,
             "label": "过程噪声 (Q)"},
            {"name": "R", "type": "float", "min": 0.01, "max": 10.0, "step": 0.1, "default": 1.0,
             "label": "测量噪声 (R)"},
        ],
    },
    {
        "id": "butterworth",
        "name": "巴特沃斯低通滤波",
        "params": [
            {"name": "order", "type": "int", "min": 1, "max": 8, "step": 1, "default": 4,
             "label": "滤波器阶数"},
            {"name": "cutoff", "type": "float", "min": 1.0, "max": 45.0, "step": 0.5, "default": 10.0,
             "label": "截止频率 (Hz)"},
        ],
    },
    {
        "id": "gaussian",
        "name": "高斯滤波",
        "params": [
            {"name": "sigma", "type": "float", "min": 0.5, "max": 20.0, "step": 0.5, "default": 3.0,
             "label": "高斯标准差"},
        ],
    },
    {
        "id": "median",
        "name": "中值滤波",
        "params": [
            {"name": "window", "type": "int", "min": 3, "max": 101, "step": 2, "default": 11,
             "label": "窗口大小"},
        ],
    },
    {
        "id": "lowess",
        "name": "局部加权回归 (LOWESS)",
        "params": [
            {"name": "frac", "type": "float", "min": 0.01, "max": 0.5, "step": 0.01, "default": 0.1,
             "label": "平滑比例"},
        ],
    },
]


def build_version(ds_id, n):
    """Return a {"n", "sampleRate", "t", "clean", "noisy"} dict."""
    gen_fn = DATASET_GENERATORS[ds_id]["gen"]
    t, clean, noisy = gen_fn(n)
    return {
        "n": n,
        "sampleRate": FS,
        "t": _round(t),
        "clean": _round(clean),
        "noisy": _round(noisy),
    }


def main():
    datasets = []
    for ds_id, meta in DATASET_GENERATORS.items():
        datasets.append({
            "id": ds_id,
            "name": meta["name"],
            "description": meta["description"],
            "defaultFilter": meta["defaultFilter"],
            "defaultParams": meta["defaultParams"],
            "versions": {
                "short": build_version(ds_id, 200),
                "long": build_version(ds_id, 1000),
            },
        })

    payload = {
        "datasets": datasets,
        "filters": FILTER_DEFINITIONS,
    }

    out_path = "/Users/xfpan/claude/filter_research/web_tool/sample_data.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"Written {out_path}")
    print(f"  datasets: {[ds['id'] for ds in payload['datasets']]}")
    print(f"  filters:  {len(payload['filters'])}")
    total_pts = sum(
        ds["versions"]["short"]["n"] + ds["versions"]["long"]["n"]
        for ds in payload["datasets"]
    )
    print(f"  total sample points: {total_pts}")


if __name__ == "__main__":
    main()
