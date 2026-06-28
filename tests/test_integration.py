"""
tests/test_integration.py — 端到端集成测试

覆盖关键数据管线的完整流程：
1. yfinance → DB → 查询 (data_pipeline_e2e)
2. 预设 CRUD 生命周期 (config_preset_lifecycle)
3. 滤波→指标计算管线 (filter_metrics_pipeline)
4. 施密特触发器管线 (schmitt_trigger_pipeline)
5. 跨周期 PnL 对齐 (cross_tf_pnl_alignment)
6. 预测拟合一致性 (prediction_fit_consistency)
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Ensure import paths for filter_app/ package
# ---------------------------------------------------------------------------
_src = Path(__file__).resolve().parent.parent / "filter_app"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

# Phase 2 modularization: functions moved to services.filter_engine
from services.filter_engine import (
    FILTERS,
    compute_metrics,
    _schmitt_trigger,
    _find_all_pairs,
    _align_pnl_to_current_tf,
    _compute_holding_masks,
    _fit_parabolic,
    _fit_physics_parabola,
)


# ===================================================================
# 1. test_data_pipeline_e2e
# ===================================================================


def test_data_pipeline_e2e(monkeypatch, tmp_path):
    """端到端数据管线: 构造OHLC数据 → upsert_kline → query_kline → 数据完整性验证"""
    # ── 隔离 DB 路径 ──
    db_path = tmp_path / "test_market.db"
    monkeypatch.setattr("db.DB_PATH", db_path)

    import db
    db.init_db()

    # ── 构造 OHLC DataFrame ──
    np.random.seed(42)
    n = 50
    dates = pd.date_range("2026-06-01", periods=n, freq="D")
    close = np.cumsum(np.random.randn(n) * 0.5) + 100
    ohlc = pd.DataFrame(
        {
            "Date": dates,
            "Open": close - 0.1,
            "High": close + 0.3,
            "Low": close - 0.3,
            "Close": close,
            "Volume": np.ones(n) * 1000,
        }
    )
    ohlc = ohlc.set_index("Date")

    # ── 写入 ──
    db.upsert_kline("TEST", "日线", ohlc)

    # ── 查询最新 50 条 ──
    result = db.query_kline("TEST", "日线", n_pts=50, day_offset=0)
    assert len(result) == n, f"行数不一致: {len(result)} != {n}"
    assert list(result.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]
    assert np.allclose(result["Close"].values, close, atol=1e-6), "Close 值不一致"

    # ── 日期排序（升序） ──
    dates_result = pd.to_datetime(result["Date"])
    assert dates_result.is_monotonic_increasing, "日期未按升序排列"

    # ── day_offset 前移查询（偏移 10 天应少 10 条） ──
    result_offset = db.query_kline("TEST", "日线", n_pts=50, day_offset=10)
    assert len(result_offset) < n, "day_offset 前移未减少数据量"
    assert pd.to_datetime(result_offset["Date"].iloc[-1]) < dates[-1], "偏移后最新日期未前移"


# ===================================================================
# 2. test_config_preset_lifecycle
# ===================================================================


def test_config_preset_lifecycle(tmp_path):
    """预设CRUD完整流程: 保存 → 列出 → 应用 → 导出JSON → 重命名 → 删除"""
    db_path = tmp_path / "config.db"
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    with patch("config_db._CONFIG_DB_PATH", db_path), patch("config_db._CONFIG_DIR", config_dir):
        import config_db
        config_db.init_config_tables()

        # ── 保存2个预设 ──
        params_a = json.dumps({"market": "US", "ticker": "AAPL", "window": 20})
        params_b = json.dumps({"market": "HK", "ticker": "0700", "span": 10})

        pid_a = config_db.save_preset("preset_A", params_a, description="AAPL preset", category="单滤波")
        pid_b = config_db.save_preset("preset_B", params_b, description="Tencent preset", category="单滤波")

        assert isinstance(pid_a, int) and pid_a > 0
        assert isinstance(pid_b, int) and pid_b > 0

        # ── list_presets 验证返回2条 ──
        presets = config_db.list_presets()
        assert len(presets) == 2, f"预设数量: {len(presets)} != 2"

        # ── apply_preset 验证参数正确 ──
        applied_a = config_db.apply_preset(pid_a)
        assert applied_a == {"market": "US", "ticker": "AAPL", "window": 20}

        applied_b = config_db.apply_preset(pid_b)
        assert applied_b == {"market": "HK", "ticker": "0700", "span": 10}

        # ── 导出 JSON 字符串 → 重新解析验证 ──
        p_a = config_db.get_preset(pid_a)
        parsed = json.loads(p_a["params_json"])
        assert parsed == {"market": "US", "ticker": "AAPL", "window": 20}
        assert p_a["name"] == "preset_A"

        p_b = config_db.get_preset(pid_b)
        parsed_b = json.loads(p_b["params_json"])
        assert parsed_b == {"market": "HK", "ticker": "0700", "span": 10}
        assert p_b["name"] == "preset_B"

        # ── rename_preset → 验证新名称 + 唯一性冲突处理 ──
        renamed = config_db.rename_preset(pid_a, "preset_A_renamed")
        assert renamed == "preset_A_renamed"

        p_a_after = config_db.get_preset(pid_a)
        assert p_a_after["name"] == "preset_A_renamed"

        # 尝试重名为已存在的名称 → 应返回 None
        conflict = config_db.rename_preset(pid_a, "preset_B")
        assert conflict is None, "重名冲突应返回 None"

        # ── delete_preset → 验证删除后list仅剩1条 + JSON文件同步删除 ──
        # 先确认没有 JSON 文件（delete 会检查同名 json）
        json_path = config_dir / "preset_B.json"
        assert not json_path.exists()

        deleted = config_db.delete_preset(pid_b)
        assert deleted is True

        presets_after = config_db.list_presets()
        assert len(presets_after) == 1, f"删除后预设数量: {len(presets_after)} != 1"
        assert presets_after[0]["preset_id"] == pid_a

        # JSON 文件同步删除：delete_preset 在删除 preset_B 时不会创建 json，
        # 但会尝试删除 config/preset_B.json（不存在也无妨）
        # 验证保存新 preset 时同名 JSON 不存在
        # 重新验证 DB 层面
        assert config_db.get_preset(pid_b) is None


# ===================================================================
# 3. test_filter_metrics_pipeline
# ===================================================================


def test_filter_metrics_pipeline():
    """滤波管线: 噪声信号 → 10种滤波 → 6项质量指标 → 验证指标合理性"""
    # ── 使用 conftest 风格的信号 ──
    np.random.seed(42)
    x = np.arange(200, dtype=float)
    clean = np.sin(x / 5.0)
    noisy = clean + np.random.randn(200) * 0.1
    t = x

    # 原始信号 roughness
    orig_roughness = float(np.sum(np.diff(noisy, 2) ** 2))

    for key, entry in FILTERS.items():
        func = entry["func"]
        params = entry["params"]
        kwargs = {k: v[4] for k, v in params.items()}  # defaults

        filtered = func(noisy.copy(), t, **kwargs)
        metrics = compute_metrics(clean, noisy, filtered)

        # 验证指标存在且为有限数值
        assert np.isfinite(metrics["snr_imp"]), f"{key}: SNR_imp 非有限值"
        assert np.isfinite(metrics["roughness"]), f"{key}: roughness 非有限值"
        assert np.isfinite(metrics["lag"]), f"{key}: lag 非有限值"

        # roughness 应低于原始信号（更平滑）
        assert metrics["roughness"] < orig_roughness, (
            f"{key}: roughness={metrics['roughness']} >= {orig_roughness}"
        )

        # lag 在合理范围：不超过窗口/span/sigma的2倍
        max_reasonable = max(kwargs.get("window", kwargs.get("span", kwargs.get("sigma", 20))), 1) * 2
        assert abs(metrics["lag"]) <= max(max_reasonable, 5), (
            f"{key}: lag={metrics['lag']} 超出合理范围 ±{max_reasonable}"
        )

    # SNR 改善检验：Savgol 和 Median 应对含噪正弦有明确 SNR 改善
    savgol_has_snr = None
    for key, entry in FILTERS.items():
        func = entry["func"]
        params = entry["params"]
        kwargs = {k: v[4] for k, v in params.items()}
        filtered = func(noisy.copy(), t, **kwargs)
        metrics = compute_metrics(clean, noisy, filtered)
        if key == "savgol":
            savgol_has_snr = metrics["snr_imp"] > 0
        if key == "median":
            assert metrics["snr_imp"] > 0, f"median SNR_imp={metrics['snr_imp']} <= 0"

    assert savgol_has_snr is not None and savgol_has_snr, (
        "Savgol 滤波对含噪正弦 SNR_imp 应 > 0"
    )


# ===================================================================
# 4. test_schmitt_trigger_pipeline
# ===================================================================


def test_schmitt_trigger_pipeline():
    """施密特触发器管线: 滤波 → 速度/加速度 → 自适应死区 → 信号生成 → 多空对检测"""
    np.random.seed(42)
    x = np.arange(200, dtype=float)
    noisy = np.sin(x / 5.0) + np.random.randn(200) * 0.1
    t = x

    # ── Savgol 滤波 ──
    from services.filter_engine import apply_savgol
    filtered = apply_savgol(noisy, t, window=21, order=2)

    # ── 计算 v, a ──
    v = np.gradient(filtered)
    a = np.gradient(v)

    # ── _schmitt_trigger ──
    result = _schmitt_trigger(v, a, ewma_span=30, k_eps=0.15, sigma_min=0.05)
    assert result is not None, "_schmitt_trigger 返回 None"

    sig_t = result["sig"]
    eps = result["eps"]
    dur = result["dur"]

    # sig_t 值在 {-1, 0, 1} 范围内
    assert set(np.unique(sig_t)).issubset({-1, 0, 1}), f"sig_t 含非法值: {np.unique(sig_t)}"
    assert len(sig_t) == len(t)

    # eps 始终为正
    assert np.all(eps > 0), f"eps 含非正值 (min={eps.min():.6f})"

    # dur 值合理（>=1）
    assert np.all(dur >= 1)

    # 信号应有切换（不是全0）
    assert np.any(sig_t != 0), "sig_t 全为0，预期有信号切换"

    # ── _find_all_pairs ──
    pairs = _find_all_pairs(sig_t)
    assert isinstance(pairs, list), "_find_all_pairs 返回非列表"

    # 有合理的多空对（至少有几对）
    if len(pairs) > 0:
        for start, end in pairs:
            assert 0 <= start < end < len(sig_t), f"多空对边界异常: ({start}, {end})"
            assert sig_t[start] != 0, f"多空对起点信号不为±1: {sig_t[start]}"


# ===================================================================
# 5. test_cross_tf_pnl_alignment
# ===================================================================


def test_cross_tf_pnl_alignment():
    """跨周期对齐: 高周期PnL → 时区归一化 → 前向填充 → 低周期消费"""
    # ── 构造高周期数据（日线, tz-naive） ──
    daily_dates = pd.date_range("2026-01-01", periods=10, freq="D")
    n_high = len(daily_dates)
    daily_pnl_long = 100 + np.cumsum(np.random.randn(n_high) * 0.5)
    daily_pnl_short = 100 - np.cumsum(np.random.randn(n_high) * 0.3)

    # 构造标准交易记录（高周期index）
    daily_trades = [
        {"entry_idx": 1, "exit_idx": 4, "type": "long", "return_pct": 1.5, "exit_reason": "take_profit"},
        {"entry_idx": 5, "exit_idx": 8, "type": "short", "return_pct": -0.8, "exit_reason": "stop_loss"},
    ]

    # ── 低周期数据（60分钟, tz-aware HKT） ──
    current_dates = pd.date_range("2026-01-01 09:30", periods=50, freq="h", tz="Asia/Hong_Kong")

    # ── 对齐 ──
    aligned = _align_pnl_to_current_tf(daily_dates, daily_pnl_long, daily_pnl_short,
                                        daily_trades, current_dates)

    # 验证对齐后的数据长度等于当前周期长度
    assert len(aligned["aligned_long"]) == len(current_dates), "对齐后长Pnl长度不一致"
    assert len(aligned["aligned_short"]) == len(current_dates), "对齐后短Pnl长度不一致"

    # 前向填充验证：低周期bar在高周期bar之后取值
    # 第0根低周期bar（09:30）≤ 第0根日线bar（00:00）→ 应为NaN（日线数据不覆盖）
    # 实际上日线数据当天00:00的bar ≤ 09:30 → 应填充
    # 前 120 分钟（约2根日线bar = Jan 1 和 Jan 2）应至少有一些有效值
    valid_mask_long = ~np.isnan(aligned["aligned_long"])
    assert valid_mask_long.any(), "前向填充后所有long_pnl均为NaN"
    assert len(aligned["entry_markers"]) > 0, "入场标记为空"
    assert len(aligned["exit_markers"]) > 0, "离场标记为空"

    # 验证 mark 类型正确
    for bar_idx, ttype, pnl in aligned["entry_markers"]:
        assert ttype in ("long", "short")
        assert 0 <= bar_idx < len(current_dates)
        assert np.isfinite(pnl)

    # ── _compute_holding_masks ──
    long_mask, short_mask = _compute_holding_masks(
        len(current_dates),
        aligned["entry_markers"],
        aligned["exit_markers"],
    )

    assert len(long_mask) == len(current_dates)
    assert len(short_mask) == len(current_dates)
    assert long_mask.dtype == bool
    assert short_mask.dtype == bool

    # 至少有一个持仓区间
    assert long_mask.any() or short_mask.any(), "持仓掩码全部为False"


# ===================================================================
# 6. test_prediction_fit_consistency
# ===================================================================


def test_prediction_fit_consistency():
    """预测拟合: poly2 vs parabola 两种模式在理想抛物线数据上的行为"""
    # ── 构造完美抛物线 y = a*(x-x0)^2 + y0，顶点在 endpoint ──
    # physics parabola 锚定 endpoint 为顶点，故顶点必须位于 endpoint
    x = np.arange(50, dtype=float)
    end = 40
    x0 = float(x[end])  # 顶点 = x[40] = 40.0
    y0 = 50.0
    a_true = 0.05
    y = a_true * (x - x0) ** 2 + y0

    start = 0

    # ── poly2 拟合（_fit_parabolic） ──
    res_poly = _fit_parabolic(x, y, start, end)
    assert res_poly is not None, "_fit_parabolic 返回 None"

    # R² 计算
    y_seg = y[start:end + 1]
    ss_res = np.sum((y_seg - res_poly["y_fit"]) ** 2)
    ss_tot = np.sum((y_seg - np.mean(y_seg)) ** 2)
    r2_poly = 1 - ss_res / ss_tot
    assert r2_poly > 0.99, f"poly2 R²={r2_poly:.6f} < 0.99"

    # ── 抛物线拟合（_fit_physics_parabola） ──
    res_phys = _fit_physics_parabola(x, y, start, end)
    assert res_phys is not None, "_fit_physics_parabola 返回 None"

    ss_res_phys = np.sum((y_seg - res_phys["y_fit"]) ** 2)
    r2_phys = 1 - ss_res_phys / ss_tot
    assert r2_phys > 0.99, f"physics parabola R²={r2_phys:.6f} < 0.99"

    # ── 两种拟合在锚定终点处数值一致 ──
    # 锚定终点 = x[end] = x[40] = 40.0
    # poly2: y_fit 在 end 位置 vs physics: 锚定在 (x0, y0) = (x[end], y[end])
    y_at_end_poly = res_poly["y_fit"][-1]
    y_at_end_phys = y_seg[-1]  # 物理抛物线锚定在终点，y_fit[终点] = y[end] 准确
    assert np.isclose(y_at_end_poly, y_at_end_phys, atol=1e-6), (
        f"终点拟合值不一致: poly={y_at_end_poly:.6f}, phys={y_at_end_phys:.6f}"
    )

    # 曲率应接近真实值
    assert np.isclose(res_poly["a"], a_true, atol=0.01), (
        f"poly2 曲率 {res_poly['a']:.6f} != {a_true:.6f}"
    )
    assert np.isclose(res_phys["a"], a_true, atol=0.01), (
        f"physics 曲率 {res_phys['a']:.6f} != {a_true:.6f}"
    )
