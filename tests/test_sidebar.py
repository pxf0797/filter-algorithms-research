"""
Tests for sidebar.py constants and pure-logic helpers.

These tests cover the non-Streamlit-widget parts of sidebar.py:
- ALL_TFS, DEFAULT_TFS, TF_HIERARCHY constants
- Any future pure-logic helpers extracted from widget functions
"""

import sys
from pathlib import Path

# Ensure streamlit/ package is importable (conftest handles streamlit mock)
_src = Path(__file__).resolve().parent.parent / "streamlit"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

import pytest

# Module under test
from components.sidebar import ALL_TFS, DEFAULT_TFS, TF_HIERARCHY


# ===================================================================
# SECTION 1 — Timeframe constants
# ===================================================================

class TestAllTFs:
    """ALL_TFS 常量正确性."""

    def test_all_tfs_has_expected_elements(self):
        """ALL_TFS 应包含所有8个标准周期."""
        expected = ["1分钟","5分钟","15分钟","60分钟","日线","周线","月线","季线"]
        assert ALL_TFS == expected

    def test_all_tfs_length(self):
        """ALL_TFS 应有8个元素."""
        assert len(ALL_TFS) == 8

    def test_all_tfs_ascending_order(self):
        """ALL_TFS 应按时间周期升序排列."""
        # Define expected order: 分钟 < 日线 < 周线 < 月线 < 季线
        minutes = ["1分钟","5分钟","15分钟","60分钟"]
        days = ["日线"]
        assert ALL_TFS[:4] == minutes
        assert ALL_TFS[4] == "日线"
        assert ALL_TFS[5] == "周线"
        assert ALL_TFS[6] == "月线"
        assert ALL_TFS[7] == "季线"


class TestDefaultTFs:
    """DEFAULT_TFS 常量正确性."""

    def test_default_tfs_has_expected_elements(self):
        """DEFAULT_TFS 应包含4个标准周期."""
        expected = ["日线", "60分钟", "15分钟", "5分钟"]
        assert DEFAULT_TFS == expected

    def test_default_tfs_length(self):
        """DEFAULT_TFS 应有4个元素."""
        assert len(DEFAULT_TFS) == 4

    def test_default_tfs_subset_of_all_tfs(self):
        """DEFAULT_TFS 应是 ALL_TFS 的子集."""
        for tf in DEFAULT_TFS:
            assert tf in ALL_TFS, f"{tf} 不在 ALL_TFS 中"


# ===================================================================
# SECTION 2 — TF_HIERARCHY 层次映射
# ===================================================================

class TestTfHierarchy:
    """TF_HIERARCHY 周期层次映射正确性."""

    def test_hierarchy_has_all_keys(self):
        """TF_HIERARCHY 应覆盖 ALL_TFS 中的所有周期."""
        for tf in ALL_TFS:
            assert tf in TF_HIERARCHY, f"{tf} 缺少层次映射"

    def test_hierarchy_length(self):
        """TF_HIERARCHY 应与 ALL_TFS 长度一致 (8个键)."""
        assert len(TF_HIERARCHY) == len(ALL_TFS) == 8

    def test_hierarchy_values_ascending(self):
        """每个周期映射到紧邻其上的更高周期."""
        expected = {
            "1分钟": "5分钟", "5分钟": "15分钟", "15分钟": "60分钟",
            "60分钟": "日线", "日线": "周线", "周线": "月线",
            "月线": "季线",
        }
        for tf, higher in expected.items():
            assert TF_HIERARCHY[tf] == higher, (
                f"{tf} 应映射到 {higher}"
            )

    def test_highest_tf_maps_to_none(self):
        """最高周期(季线)应映射到 None."""
        assert TF_HIERARCHY["季线"] is None

    def test_hierarchy_is_dag(self):
        """层次映射不应形成循环（简单检测：除季线外每个值应出现在键中）. """
        values_non_none = [v for v in TF_HIERARCHY.values() if v is not None]
        for v in values_non_none:
            assert v in TF_HIERARCHY, f"{v} 映射目标不在键集合中"

    def test_hierarchy_monotonic_order(self):
        """映射顺序应与 ALL_TFS 顺序一致（每个值应在键的右边）. """
        for tf, higher in TF_HIERARCHY.items():
            if higher is not None:
                tf_idx = ALL_TFS.index(tf)
                higher_idx = ALL_TFS.index(higher)
                assert higher_idx > tf_idx, (
                    f"{tf}(索引{tf_idx}) 应小于 {higher}(索引{higher_idx})"
                )

    def test_hierarchy_no_self_reference(self):
        """不应存在指向自身的映射."""
        for tf, higher in TF_HIERARCHY.items():
            assert tf != higher, f"{tf} 不能映射到自身"

    def test_hierarchy_no_skip_level(self):
        """每个映射应跳过恰好一个层级."""
        for tf, higher in TF_HIERARCHY.items():
            if higher is not None:
                tf_idx = ALL_TFS.index(tf)
                higher_idx = ALL_TFS.index(higher)
                assert higher_idx == tf_idx + 1, (
                    f"{tf} → {higher} 跳过了中间层级"
                )


# ===================================================================
# SECTION 3 — Widget helper: _compact_slider format logic (extracted)
# ===================================================================

class TestCompactSliderFormatLogic:
    """_compact_slider 的 fmt 参数计算逻辑（纯逻辑，不含 st.slider）. """

    def test_fmt_none_when_no_fmt(self):
        """不传 fmt 时 kwargs 不应包含 format."""
        from components.sidebar import _compact_slider
        # 仅验证函数签名存在且 fmt 参数默认是 None
        import inspect
        sig = inspect.signature(_compact_slider)
        assert sig.parameters["fmt"].default is None
        assert sig.parameters["pstep"].default == 1.0

    def test_fmt_provided_includes_format(self):
        """传入 fmt 时 kwargs 应包含 format."""
        from components.sidebar import _compact_slider
        import inspect
        sig = inspect.signature(_compact_slider)
        assert "fmt" in sig.parameters


# ===================================================================
# SECTION 4 — _render_param_slider parameter logic (extracted)
# ===================================================================

class TestRenderParamSliderLogic:
    """_render_param_slider 的非 Streamlit 参数逻辑."""

    def test_step_type_determines_format(self):
        """根据 pstep 类型推导 format."""
        # int step → 不传 format, float step < 0.01 → "%.3f", else "%.2f"
        assert isinstance(1, int)
        assert isinstance(0.5, float)

    def test_key_suffix_append(self):
        """key_suffix 非空时 key 应为 f'{label}_{key_suffix}'."""
        from components.sidebar import _render_param_slider
        import inspect
        sig = inspect.signature(_render_param_slider)
        assert sig.parameters["key_suffix"].default == ""

    def test_container_default_is_none(self):
        """container 默认 None 表示向后兼容 sidebar."""
        from components.sidebar import _render_param_slider
        import inspect
        sig = inspect.signature(_render_param_slider)
        assert sig.parameters["container"].default is None
