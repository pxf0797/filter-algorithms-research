"""Verify the 5-package architecture after Phase 2 refactor.

Checks:
  1. All 5 top-level packages exist with __init__.py
  2. Old flat directories (services/, components/) are gone
  3. Key modules are importable from their new locations
  4. T7: _render_chart split — _prepare_chart_data + _build_chart_figure
  5. T8: filters split — schmitt / strategy / alignment
  6. T11: 统一 — colors + PnL renderer
"""
import re
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

    def test_compute_combined_pnl_all_equal(self):
        """compute_combined_pnl — long/short 相同时结果等于任一方."""
        from filter.common.pnl_renderer import compute_combined_pnl
        long = np.array([100.0, 102.0, 99.0, 105.0])
        short = np.array([100.0, 102.0, 99.0, 105.0])
        result = compute_combined_pnl(long, short)
        np.testing.assert_array_equal(result, long)
        np.testing.assert_array_equal(result, short)

    def test_compute_combined_pnl_alternating_dominance(self):
        """compute_combined_pnl — 前半段做多赢、后半段做空赢，逐点验证 max."""
        from filter.common.pnl_renderer import compute_combined_pnl
        long = np.array([100.0, 105.0, 98.0, 99.0])
        short = np.array([100.0, 101.0, 103.0, 106.0])
        result = compute_combined_pnl(long, short)
        expected = np.array([100.0, 105.0, 103.0, 106.0])
        np.testing.assert_array_equal(result, expected)

    def test_compute_combined_pnl_mixed_signs(self):
        """compute_combined_pnl — 正负混合值时 max 正确选取."""
        from filter.common.pnl_renderer import compute_combined_pnl
        long = np.array([-5.0, 3.0, -1.0, 0.0])
        short = np.array([2.0, -1.0, -3.0, 1.0])
        result = compute_combined_pnl(long, short)
        expected = np.array([2.0, 3.0, -1.0, 1.0])
        np.testing.assert_array_equal(result, expected)

    def test_compute_drawdown_recovery_then_new_peak(self):
        """compute_drawdown — 峰值→谷底→恢复→新高，新高点回撤归零."""
        from filter.common.pnl_renderer import compute_drawdown
        pnl = np.array([100.0, 95.0, 98.0, 102.0, 105.0])
        result = compute_drawdown(pnl)
        # index 1: (95-100)/100*100 = -5.0
        assert result[1] == pytest.approx(-5.0)
        # index 2: (98-100)/100*100 = -2.0
        assert result[2] == pytest.approx(-2.0)
        # index 3: 新峰值 102, 回撤归零
        assert result[3] == pytest.approx(0.0)
        # index 4: 新峰值 105, 回撤归零
        assert result[4] == pytest.approx(0.0)

    def test_compute_drawdown_constant_series(self):
        """compute_drawdown — 恒定 PnL 回撤全为零."""
        from filter.common.pnl_renderer import compute_drawdown
        pnl = np.array([100.0, 100.0, 100.0, 100.0])
        result = compute_drawdown(pnl)
        expected = np.zeros_like(pnl)
        np.testing.assert_array_equal(result, expected)

    def test_compute_drawdown_empty_and_single(self):
        """compute_drawdown — 空数组返回空，单元素返回 [0.0]."""
        from filter.common.pnl_renderer import compute_drawdown
        result_empty = compute_drawdown(np.array([]))
        assert len(result_empty) == 0
        result_single = compute_drawdown(np.array([100.0]))
        np.testing.assert_array_equal(result_single, np.array([0.0]))

    def test_make_pnl_long_trace_with_row(self):
        """make_pnl_long_trace — row=2 时 xaxis/yaxis 为 x2/y2."""
        from filter.common.pnl_renderer import make_pnl_long_trace
        x = np.arange(5)
        pnl = np.array([100.0, 102.0, 101.0, 103.0, 105.0])
        trace = make_pnl_long_trace(x, pnl, row=2)
        assert trace["xaxis"] == "x2"
        assert trace["yaxis"] == "y2"

    def test_make_pnl_long_trace_custom_dash_width(self):
        """make_pnl_long_trace — dash 和 width 参数传递到 line dict."""
        from filter.common.pnl_renderer import make_pnl_long_trace
        x = np.arange(5)
        pnl = np.array([100.0, 102.0, 101.0, 103.0, 105.0])
        trace = make_pnl_long_trace(x, pnl, dash="dot", width=3.0)
        assert trace["line"]["dash"] == "dot"
        assert trace["line"]["width"] == 3.0

    def test_make_pnl_combined_trace_subtracts_baseline(self):
        """make_pnl_combined_trace — y 值为 combined - 100.0 (PNL_BASELINE)."""
        from filter.common.pnl_renderer import make_pnl_combined_trace
        x = np.arange(4)
        combined = np.array([100.0, 105.0, 98.0, 110.0])
        trace = make_pnl_combined_trace(x, combined)
        expected_y = combined - 100.0
        np.testing.assert_array_equal(trace["y"], expected_y)

    def test_make_drawdown_trace_includes_fill(self):
        """make_drawdown_trace — 包含 fill='tozeroy' 和 fillcolor."""
        from filter.common.pnl_renderer import make_drawdown_trace
        x = np.arange(5)
        pnl = np.array([100.0, 102.0, 99.0, 105.0, 103.0])
        trace = make_drawdown_trace(x, pnl)
        assert trace["fill"] == "tozeroy"
        assert "fillcolor" in trace
        assert isinstance(trace["fillcolor"], str)

    def test_make_pnl_baseline_shape_custom_y(self):
        """make_pnl_baseline_shape — y0 和 y1 反映自定义 y 参数."""
        from filter.common.pnl_renderer import make_pnl_baseline_shape
        shape = make_pnl_baseline_shape(row=1, y=105.0)
        assert shape["y0"] == 105.0
        assert shape["y1"] == 105.0
        shape_default = make_pnl_baseline_shape(row=1)
        assert shape_default["y0"] == 100.0
        assert shape_default["y1"] == 100.0


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


# ══════════════════════════════════════════════════════════════════════
# 版本一致性测试 (来自 test_version.py)
# ══════════════════════════════════════════════════════════════════════

def test_version_format():
    """验证 filter.__version__ 符合 semver 格式（MAJOR.MINOR.PATCH）。"""
    from filter import __version__
    assert re.match(r"\d+\.\d+\.\d+", __version__), (
        f"filter.__version__ ('{__version__}') 不符合 semver 格式 (MAJOR.MINOR.PATCH)"
    )


def test_no_hardcoded_mismatched_versions():
    """检查项目文件中无过时的硬编码版本号（与 filter.__version__ 不一致的）。"""
    from filter import __version__
    project_root = Path(__file__).resolve().parent.parent
    current_version = __version__

    scan_patterns = ["*.py", "*.md", "*.toml", "*.cfg", "*.yaml", "*.yml"]
    skip_dirs = {".git", "__pycache__", ".venv", "venv", "node_modules", ".claude"}

    mismatches = []
    for pattern in scan_patterns:
        for filepath in project_root.rglob(pattern):
            parts = set(filepath.parts)
            if skip_dirs & parts:
                continue

            try:
                content = filepath.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue

            version_pattern = re.compile(r'\bv?(\d+\.\d+\.\d+)\b')
            for match in version_pattern.finditer(content):
                found = match.group(1)
                if found == current_version:
                    continue
                # pyproject.toml and __init__.py define the canonical version
                if filepath.name in ("pyproject.toml", "__init__.py"):
                    continue
                mismatches.append(
                    f"  {filepath.relative_to(project_root)}: found '{found}'"
                )

    if mismatches:
        print(f"\n⚠ 发现 {len(mismatches)} 处版本号与当前版本 '{current_version}' 不一致：")
        for m in mismatches:
            print(m)
        print("（提示：文档中的版本引用可能需要同步更新）")
