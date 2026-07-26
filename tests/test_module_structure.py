"""Verify the 5-package architecture after Phase 2 refactor.

Checks:
  1. All 5 top-level packages exist with __init__.py
  2. Old flat directories (services/, components/) are gone
  3. Key modules are importable from their new locations
  4. T7: _render_chart split — _prepare_chart_data + _build_chart_figure
  5. T8: filters split — schmitt / strategy / alignment
  6. T11: 统一 — colors + PnL renderer
"""
import sys
from pathlib import Path

import numpy as np
import pytest


# ── Project root ──────────────────────────────────────────────────────────
PROJECT = Path(__file__).resolve().parent.parent
FILTER_APP = PROJECT / "filter"


# ── Helpers ───────────────────────────────────────────────────────────────

def _package_path(name: str) -> Path:
    return FILTER_APP / name


def _has_init(pkg: str) -> bool:
    return (_package_path(pkg) / "__init__.py").exists()


# ── 1. Top-level packages exist ──────────────────────────────────────────
PACKAGES = ["shared", "engine", "data", "browse", "backtest"]


class TestPackageStructure:
    def test_all_five_packages_exist(self):
        for pkg in PACKAGES:
            assert _package_path(pkg).is_dir(), f"Missing package dir: {pkg}"
            assert _has_init(pkg), f"Missing __init__.py in {pkg}"

    def test_root_has_init(self):
        assert (FILTER_APP / "__init__.py").exists(), "Missing filter/__init__.py"

    def test_old_dirs_removed(self):
        """services/ and components/ directories no longer exist."""
        for old in ["services", "components"]:
            assert not _package_path(old).exists(), (
                f"Old directory {old}/ should be removed"
            )


# ── 2. Key modules are importable ────────────────────────────────────────
# These tests verify that the new import paths resolve correctly.
# They use importlib so a single failure doesn't crash the whole suite.


class TestImports:
    # shared
    def test_shared_config(self):
        from shared.config import ViewConfig
        assert ViewConfig is not None

    def test_shared_constants(self):
        from shared.constants import ALL_TFS
        assert isinstance(ALL_TFS, (list, tuple))
        assert len(ALL_TFS) > 0

    def test_shared_state(self):
        from shared.state import AppState, ViewState
        assert AppState is not None
        assert ViewState is not None

    # engine
    def test_engine_filters(self):
        from engine.filters import FILTERS
        assert isinstance(FILTERS, dict)
        assert len(FILTERS) > 0

    def test_engine_signals(self):
        from engine.signals import compute_bs_markers
        assert callable(compute_bs_markers)

    # backtest
    def test_backtest_engine(self):
        from backtest.engine import BacktestRunner
        assert BacktestRunner is not None

    def test_backtest_cli(self):
        from backtest.cli import parse_args
        assert callable(parse_args)

    def test_backtest_recorder(self):
        from backtest.recorder import EventRecorder
        assert EventRecorder is not None

    def test_backtest_catalog(self):
        from backtest.catalog import BacktestCatalog
        assert BacktestCatalog is not None


# ── 3. filter top-level re-exports work ──────────────────────────────

class TestTopLevelReexports:
    def test_view_config(self):
        from filter import ViewConfig
        assert ViewConfig is not None

    def test_all_tfs(self):
        from filter import ALL_TFS
        assert isinstance(ALL_TFS, (list, tuple))

    def test_filters(self):
        from filter import FILTERS
        assert isinstance(FILTERS, dict)


# ── 4. T8: filters split — engine sub-modules ────────────────────────

class TestEngineSubModules:
    """T8: filters.py split into schmitt / strategy / alignment sub-modules."""

    def test_schmitt_module_importable(self):
        """engine.schmitt 模块可独立导入 — _schmitt_trigger 核心函数."""
        from engine.schmitt import _schmitt_trigger
        assert callable(_schmitt_trigger)

    def test_strategy_module_importable(self):
        """engine.strategy 模块可独立导入."""
        from engine.strategy import _compute_holding_masks
        assert callable(_compute_holding_masks)

    def test_alignment_module_importable(self):
        """engine.alignment 模块可独立导入."""
        from engine.alignment import compute_metrics
        assert callable(compute_metrics)

    def test_filters_module_still_importable(self):
        """engine.filters 仍然导入 FILTERS 注册表."""
        from engine.filters import FILTERS
        assert isinstance(FILTERS, dict)
        assert len(FILTERS) > 0

    def test_filters_functional_parity_sma_basic(self):
        """拆分后 filters.py 核心功能一致：SMA 输出正确."""
        from engine.filters import apply_sma
        signal = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        t = np.arange(5, dtype=float)
        result = apply_sma(signal, t, window=3)
        assert len(result) == 5
        # SMA window=3 使用 centered rolling: edges 为 NaN，中间有效
        assert not np.isnan(result[2]), "SMA window=3 中心应在 index 2 有效"

    def test_schmitt_functional_parity_basic(self):
        """拆分后 schmitt.py 核心功能一致：基本触发."""
        from engine.schmitt import _schmitt_trigger
        np.random.seed(42)
        n = 100  # need >= ewma_span (default 60)
        t = np.arange(n, dtype=float)
        v = np.sin(t / 8.0) + np.random.randn(n) * 0.1  # velocity signal
        a = np.gradient(v)  # acceleration (derivative of v)
        result = _schmitt_trigger(v, a)
        assert result is not None, "schmitt trigger 应对足够长的信号返回有效结果"
        assert "sig" in result
        assert len(result["sig"]) == n
        # sig values are -1, 0, or 1
        assert set(np.unique(result["sig"])).issubset({-1, 0, 1})


# ── 5. T7: _render_chart split — independently testable functions ────

class TestRenderChartSplit:
    """T7: _prepare_chart_data and _build_chart_figure are independently importable."""

    def test_prepare_chart_data_importable(self):
        """_prepare_chart_data 可作为独立函数导入."""
        from browse.app import _prepare_chart_data
        assert callable(_prepare_chart_data)

    def test_build_chart_figure_importable(self):
        """_build_chart_figure 可作为独立函数导入."""
        from browse.app import _build_chart_figure
        assert callable(_build_chart_figure)

    def test_prepare_chart_data_has_expected_signature(self):
        """_prepare_chart_data 接受 params dict 返回 dict."""
        from browse.app import _prepare_chart_data
        import inspect
        sig = inspect.signature(_prepare_chart_data)
        params = list(sig.parameters.keys())
        assert "params" in params, f"expected 'params' in signature, got {params}"

    def test_build_chart_figure_has_expected_signature(self):
        """_build_chart_figure 接受 data dict + cfg 返回 tuple."""
        from browse.app import _build_chart_figure
        import inspect
        sig = inspect.signature(_build_chart_figure)
        params = list(sig.parameters.keys())
        assert "data" in params, f"expected 'data' in signature, got {params}"
        assert "cfg" in params, f"expected 'cfg' in signature, got {params}"


# ── 6. T11: 统一 — colors + PnL renderer ──────────────────────────

class TestColorsModule:
    """T11: 集中颜色管理 — filter/constants/colors.py."""

    def test_colors_module_importable(self):
        """filter.constants.colors 模块可导入."""
        from filter.constants.colors import COLORS
        assert isinstance(COLORS, dict)
        assert len(COLORS) > 0

    def test_colors_has_pnl_keys(self):
        """COLORS 包含 PnL 相关颜色键."""
        from filter.constants.colors import COLORS
        expected_keys = ["pnl_long", "pnl_short", "pnl_combined", "pnl_combined_fill"]
        for key in expected_keys:
            assert key in COLORS, f"COLORS 缺少键: {key}"

    def test_colors_has_signal_keys(self):
        """COLORS 包含信号/滤波相关颜色键."""
        from filter.constants.colors import COLORS
        expected_keys = ["signal", "epsilon", "sigma_v", "residual"]
        for key in expected_keys:
            assert key in COLORS, f"COLORS 缺少键: {key}"

    def test_ticker_color_helper(self):
        """ticker_color 辅助函数返回有效颜色."""
        from filter.constants.colors import ticker_color
        for i in range(15):
            color = ticker_color(i)
            assert isinstance(color, str), f"ticker_color({i}) 应为 str"
            assert color.startswith("#") or color.startswith("rgba"), (
                f"ticker_color({i}) 应为有效颜色值，得到: {color}"
            )

    def test_view_color_helper(self):
        """view_color 辅助函数返回有效颜色."""
        from filter.constants.colors import view_color
        for i in range(8):
            color = view_color(i)
            assert isinstance(color, str), f"view_color({i}) 应为 str"
            assert color.startswith("#") or color.startswith("rgba"), (
                f"view_color({i}) 应为有效颜色值，得到: {color}"
            )


class TestPnlRendererModule:
    """T11: 公共 PnL 渲染模块 — filter/common/pnl_renderer.py."""

    def test_pnl_renderer_importable(self):
        """filter.common.pnl_renderer 模块可导入."""
        from filter.common.pnl_renderer import (
            compute_combined_pnl,
            compute_drawdown,
            make_pnl_long_trace,
        )
        assert callable(compute_combined_pnl)
        assert callable(compute_drawdown)
        assert callable(make_pnl_long_trace)

    def test_compute_combined_pnl(self):
        """compute_combined_pnl 取 long/short PnL 最大值."""
        from filter.common.pnl_renderer import compute_combined_pnl
        long = np.array([100.0, 102.0, 99.0, 105.0])
        short = np.array([100.0, 101.0, 103.0, 104.0])
        result = compute_combined_pnl(long, short)
        expected = np.maximum(long, short)
        np.testing.assert_array_equal(result, expected)

    def test_compute_drawdown(self):
        """compute_drawdown 从运行峰值计算回撤百分比."""
        from filter.common.pnl_renderer import compute_drawdown
        pnl = np.array([100.0, 102.0, 98.0, 104.0, 101.0])
        result = compute_drawdown(pnl)
        # peak at index 1 = 102, drawdown at index 2 = (98-102)/102 * 100 = -3.92
        assert result[2] == pytest.approx(-3.921, abs=0.01)
        # peak at index 3 = 104, drawdown at index 4 = (101-104)/104 * 100 = -2.88
        assert result[4] == pytest.approx(-2.884, abs=0.01)

    def test_make_pnl_long_trace_returns_dict(self):
        """make_pnl_long_trace 返回 dict 格式 trace."""
        from filter.common.pnl_renderer import make_pnl_long_trace
        x = np.arange(10)
        pnl = np.linspace(100, 110, 10)
        trace = make_pnl_long_trace(x, pnl)
        assert isinstance(trace, dict)
        assert trace["type"] == "scattergl"
        assert trace["name"] == "做多PnL"

    def test_make_pnl_short_trace_returns_dict(self):
        """make_pnl_short_trace 返回 dict 格式 trace."""
        from filter.common.pnl_renderer import make_pnl_short_trace
        x = np.arange(10)
        pnl = np.linspace(100, 95, 10)
        trace = make_pnl_short_trace(x, pnl)
        assert isinstance(trace, dict)
        assert trace["type"] == "scattergl"
        assert trace["name"] == "做空PnL"

    def test_make_pnl_combined_trace_has_fill(self):
        """make_pnl_combined_trace 包含 fill 配置."""
        from filter.common.pnl_renderer import make_pnl_combined_trace
        x = np.arange(10)
        combined = np.linspace(100, 110, 10)
        trace = make_pnl_combined_trace(x, combined)
        assert trace["fill"] == "tozeroy"
        assert "fillcolor" in trace

    def test_make_pnl_baseline_shape(self):
        """make_pnl_baseline_shape 返回基线 shape dict."""
        from filter.common.pnl_renderer import make_pnl_baseline_shape
        shape = make_pnl_baseline_shape(row=1)
        assert shape["type"] == "line"
        assert shape["y0"] == 100.0

    def test_make_pnl_yaxis_config(self):
        """make_pnl_yaxis_config 返回 yaxis 配置 dict."""
        from filter.common.pnl_renderer import make_pnl_yaxis_config
        config = make_pnl_yaxis_config(row=1)
        assert "yaxis1" in config
        assert config["yaxis1"]["ticksuffix"] == "%"


# ══════════════════════════════════════════════════════════════════════
# P2-4: 工程债务清理测试
# ══════════════════════════════════════════════════════════════════════

class TestB68PipelineRename:
    """B68: pipeline.py → capture.py — import 路径更新后正常工作."""

    def test_capture_module_importable(self):
        """from filter.backtest.capture import PipelineCapture, PipelineStageData."""
        from filter.backtest.capture import PipelineCapture, PipelineStageData
        assert PipelineCapture is not None
        assert PipelineStageData is not None

    def test_capture_module_is_enabled_method(self):
        """PipelineCapture 包含 is_enabled 静态方法."""
        from filter.backtest.capture import PipelineCapture
        result = PipelineCapture.is_enabled()
        assert isinstance(result, bool)


class TestB75SysPathRemoval:
    """B75: import filter 不修改 sys.path."""

    def test_import_filter_does_not_modify_sys_path(self):
        """import filter 前后 sys.path 保持一致."""
        import copy
        path_before = copy.copy(sys.path)
        import filter  # noqa: F401
        path_after = list(sys.path)
        # filter/ 目录不应被新添加到 sys.path（pip install -e . 已解决路径问题）
        assert path_before == path_after, (
            f"sys.path 被 import filter 修改了!\n"
            f"添加了: {set(path_after) - set(path_before)}"
        )
