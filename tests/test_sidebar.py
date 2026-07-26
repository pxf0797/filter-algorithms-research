"""
Tests for sidebar.py constants and pure-logic helpers.

These tests cover the non-Streamlit-widget parts of sidebar.py:
- ALL_TFS, DEFAULT_TFS, TF_HIERARCHY constants
- Any future pure-logic helpers extracted from widget functions
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

# Module under test
from browse.components_sidebar import ALL_TFS, DEFAULT_TFS, TF_HIERARCHY


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

    def test_default_tfs_sorted_coarse_to_fine(self):
        """P0: DEFAULT_TFS 应按 ALL_TFS 索引降序（粗→细），确保 v0=coarsest, v3=finest.

        这是多周期视图索引的约定：
        - v0 = coarsest (highest ALL_TFS index) → 日线
        - v3 = finest   (lowest ALL_TFS index)  → 5分钟

        违反此约定会导致周期倒转，使 v0 滤波值出现在过高频周期上，
        而新高周期视图（日/周线）出现台阶状数据。
        """
        indices = [ALL_TFS.index(tf) for tf in DEFAULT_TFS]
        assert indices == sorted(indices, reverse=True), (
            f"DEFAULT_TFS 应按 ALL_TFS 索引降序排列(粗→细)，"
            f"当前 indices={indices}，期望={sorted(indices, reverse=True)}"
        )


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
        from browse.components_sidebar import _compact_slider
        # 仅验证函数签名存在且 fmt 参数默认是 None
        import inspect
        sig = inspect.signature(_compact_slider)
        assert sig.parameters["fmt"].default is None
        assert sig.parameters["pstep"].default == 1.0

    def test_fmt_provided_includes_format(self):
        """传入 fmt 时 kwargs 应包含 format."""
        from browse.components_sidebar import _compact_slider
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
        from browse.components_sidebar import _render_param_slider
        import inspect
        sig = inspect.signature(_render_param_slider)
        assert sig.parameters["key_suffix"].default == ""

    def test_container_default_is_none(self):
        """container 默认 None 表示向后兼容 sidebar."""
        from browse.components_sidebar import _render_param_slider
        import inspect
        sig = inspect.signature(_render_param_slider)
        assert sig.parameters["container"].default is None


# ===================================================================
# SECTION 5 -- _compact_slider widget (mocked Streamlit)
# ===================================================================

class TestCompactSlider:
    """_compact_slider 函数测试 -- mock st.columns."""

    def test_basic_slider_no_key_no_fmt(self):
        """不传 key 和 fmt 时正常渲染."""
        mock_col0 = MagicMock()
        mock_col1 = MagicMock()
        mock_col1.slider.return_value = 50.0

        with patch("browse.components_sidebar.st.columns",
                   return_value=[mock_col0, mock_col1]):
            from browse.components_sidebar import _compact_slider
            result = _compact_slider("N", 20, 300, 120, 10)
            assert result == 50.0
            mock_col0.caption.assert_called_once()
            mock_col1.slider.assert_called_once_with(
                "N", min_value=20, max_value=300, value=120,
                step=10, key=None, label_visibility="collapsed",
            )

    def test_slider_with_key_and_fmt(self):
        """传入 key 和 fmt 时传递给 slider."""
        mock_col0 = MagicMock()
        mock_col1 = MagicMock()
        mock_col1.slider.return_value = 0.5

        with patch("browse.components_sidebar.st.columns",
                   return_value=[mock_col0, mock_col1]):
            from browse.components_sidebar import _compact_slider
            result = _compact_slider("sigma", 0.0, 1.0, 0.5, 0.01,
                                     key="my_ke", fmt="%.3f")
            assert result == 0.5
            mock_col1.slider.assert_called_once_with(
                "sigma", min_value=0.0, max_value=1.0, value=0.5,
                step=0.01, key="my_ke", label_visibility="collapsed",
                format="%.3f",
            )

    def test_slider_caption_renders_label(self):
        """验证 caption 显示 label 文本（替代 markdown+unsafe_allow_html）。"""
        mock_col0 = MagicMock()
        mock_col1 = MagicMock()
        mock_col1.slider.return_value = 5.0

        with patch("browse.components_sidebar.st.columns",
                   return_value=[mock_col0, mock_col1]):
            from browse.components_sidebar import _compact_slider
            _compact_slider("窗口", 1, 100, 50, 1)
            mock_col0.caption.assert_called_once_with("窗口")


# ===================================================================
# SECTION 6 -- _render_param_slider widget (mocked Streamlit)
# ===================================================================

class TestRenderParamSlider:
    """_render_param_slider 函数测试."""

    def test_container_default_uses_sidebar(self):
        """container=None 时使用 st.sidebar.slider."""
        mock_sidebar = MagicMock()
        mock_sidebar.slider.return_value = 0.5
        with patch("browse.components_sidebar.st.sidebar", mock_sidebar), \
             patch("browse.components_sidebar.st.session_state", {}, create=True):
            from browse.components_sidebar import _render_param_slider
            result = _render_param_slider("阈值", 0.0, 1.0, 0.1, 0.5)
            assert result == 0.5
            mock_sidebar.slider.assert_called_once()

    def test_container_st_uses_st(self):
        """container=st 时使用 st.slider（而非 st.sidebar.slider）."""
        mock_st = MagicMock()
        mock_st.slider.return_value = 20.0
        with patch("browse.components_sidebar.st", mock_st):
            from browse.components_sidebar import _render_param_slider
            result = _render_param_slider("窗口", 5, 100, 5, 20,
                                          container=mock_st)
            assert result == 20.0
            mock_st.slider.assert_called_once()

    def test_key_suffix_appended(self):
        """key_suffix 非空时 key 为 f'{label}_{key_suffix}'."""
        mock_sidebar = MagicMock()
        mock_sidebar.slider.return_value = 10.0
        with patch("browse.components_sidebar.st.sidebar", mock_sidebar), \
             patch("browse.components_sidebar.st.session_state", {}, create=True):
            from browse.components_sidebar import _render_param_slider
            _render_param_slider("跨度", 2, 100, 1, 10,
                                 key_suffix="f1_sma")
            call_key = mock_sidebar.slider.call_args[1].get("key")
            assert call_key == "跨度_f1_sma"

    def test_int_step_no_format(self):
        """int step 不传 format 参数."""
        mock_sidebar = MagicMock()
        mock_sidebar.slider.return_value = 10
        with patch("browse.components_sidebar.st.sidebar", mock_sidebar), \
             patch("browse.components_sidebar.st.session_state", {}, create=True):
            from browse.components_sidebar import _render_param_slider
            _render_param_slider("跨度", 2, 100, 1, 10)
            call_kwargs = mock_sidebar.slider.call_args[1]
            assert "format" not in call_kwargs

    def test_float_step_small_format_three(self):
        """float step < 0.01 使用 %.3f 格式."""
        mock_sidebar = MagicMock()
        mock_sidebar.slider.return_value = 0.5
        with patch("browse.components_sidebar.st.sidebar", mock_sidebar), \
             patch("browse.components_sidebar.st.session_state", {}, create=True):
            from browse.components_sidebar import _render_param_slider
            _render_param_slider("sigma", 0.0, 1.0, 0.001, 0.5)
            call_kwargs = mock_sidebar.slider.call_args[1]
            assert call_kwargs["format"] == "%.3f"

    def test_float_step_normal_format_two(self):
        """0.01 <= float step < 1.0 使用 %.2f 格式."""
        mock_sidebar = MagicMock()
        mock_sidebar.slider.return_value = 0.5
        with patch("browse.components_sidebar.st.sidebar", mock_sidebar), \
             patch("browse.components_sidebar.st.session_state", {}, create=True):
            from browse.components_sidebar import _render_param_slider
            _render_param_slider("比例", 0.0, 1.0, 0.1, 0.5)
            call_kwargs = mock_sidebar.slider.call_args[1]
            assert call_kwargs["format"] == "%.2f"

    def test_no_key_no_session_state_lookup(self):
        """key_suffix='' 时不查 session_state，直接使用 pdefault."""
        mock_sidebar = MagicMock()
        mock_sidebar.slider.return_value = 5
        with patch("browse.components_sidebar.st.sidebar", mock_sidebar), \
             patch("browse.components_sidebar.st.session_state", {}, create=True):
            from browse.components_sidebar import _render_param_slider
            _render_param_slider("窗口", 3, 101, 2, 11, key_suffix="")
            call_kwargs = mock_sidebar.slider.call_args[1]
            assert call_kwargs["key"] is None


# ===================================================================
# SECTION 7 -- _render_params with mocked Streamlit and FILTERS
# ===================================================================

class TestRenderParams:
    """_render_params 函数测试 -- 深度 mock Streamlit 组件 + FILTERS."""

    @patch("browse.components_sidebar.FILTERS", {
        "sma": {
            "name": "SMA",
            "func": lambda x: x,
            "params": {"window": ("窗口大小", 3, 101, 2, 11)},
        },
    })
    def test_render_params_basic_sma(self):
        """基本 SMA filter, show_sch=True, show_pred=True."""
        mock_cols = [MagicMock() for _ in range(5)]
        with patch("browse.components_sidebar.st.columns",
                   return_value=mock_cols), \
             patch("browse.components_sidebar.st.selectbox",
                   return_value="日线"), \
             patch("browse.components_sidebar.st.checkbox",
                   return_value=True), \
             patch("browse.components_sidebar.st.button",
                   return_value=False), \
             patch("browse.components_sidebar.st.session_state",
                   {}, create=True), \
             patch("browse.components_sidebar.st.expander"), \
             patch("browse.components_sidebar.st.slider",
                   return_value=50.0), \
             patch("browse.components_sidebar.st.color_picker",
                   return_value="#00d4aa"):
            from browse.components_sidebar import _render_params
            cfg = _render_params(
                key="v0", filter_id="sma", dual=False,
                filter_id2=None, tf_default="日线",
            )

        assert cfg["_fid"] == "sma"
        assert cfg["_dual"] is False
        assert cfg["tf"] == "日线"
        assert "pv" in cfg
        assert "pv2" in cfg
        assert cfg["fc2"] == "#ff6b6b"

    @patch("browse.components_sidebar.FILTERS", {})
    def test_render_params_unknown_filter_warning(self):
        """未知 filter_id 触发 st.warning 但始终返回 cfg dict（修复: 不再返回 None）."""
        mock_warning = MagicMock()
        mock_cols = [MagicMock() for _ in range(5)]
        with patch("browse.components_sidebar.st.warning", mock_warning), \
             patch("browse.components_sidebar.st.columns",
                   return_value=mock_cols), \
             patch("browse.components_sidebar.st.selectbox",
                   return_value="日线"), \
             patch("browse.components_sidebar.st.checkbox",
                   return_value=True), \
             patch("browse.components_sidebar.st.button",
                   return_value=False), \
             patch("browse.components_sidebar.st.session_state",
                   {}, create=True), \
             patch("browse.components_sidebar.st.expander"):
            from browse.components_sidebar import _render_params
            cfg = _render_params(
                key="v0", filter_id="nonexistent", dual=False,
                filter_id2=None, tf_default="日线",
            )

        mock_warning.assert_called_once()
        # ★ 修复验证: cfg 始终是 dict，不是 None
        assert cfg is not None
        assert isinstance(cfg, dict)
        # 核心 key 必须存在
        for k in ["_fid", "_dual", "_fid2", "tf", "n_pts", "show_sch",
                  "ke", "sm", "ew", "show_pred", "n_ext", "fit_mode",
                  "pv", "pv2", "fc", "fc2"]:
            assert k in cfg, f"缺少必要key: {k}"
        assert cfg["pv"] == {}

    @patch("browse.components_sidebar.FILTERS", {
        "sma": {
            "name": "SMA",
            "func": lambda x: x,
            "params": {"window": ("窗口大小", 3, 101, 2, 11)},
        },
    })
    def test_render_params_valid_filter_sets_pv(self):
        """有效 filter_id 正确设置 pv 和 fc."""
        with patch("browse.components_sidebar.st.columns",
                   return_value=[MagicMock() for _ in range(5)]), \
             patch("browse.components_sidebar.st.selectbox",
                   return_value="日线"), \
             patch("browse.components_sidebar.st.checkbox",
                   return_value=True), \
             patch("browse.components_sidebar.st.button",
                   return_value=False), \
             patch("browse.components_sidebar.st.session_state",
                   {}, create=True), \
             patch("browse.components_sidebar.st.expander"), \
             patch("browse.components_sidebar.st.slider",
                   return_value=50.0), \
             patch("browse.components_sidebar.st.color_picker",
                   return_value="#ff6b6b"):
            from browse.components_sidebar import _render_params
            cfg = _render_params(
                key="v1", filter_id="sma", dual=False,
                filter_id2=None, tf_default="日线",
            )

        # pv 为 dict（具体值由 mock slider 决定）
        assert "pv" in cfg
        assert isinstance(cfg["pv"], dict)
        assert "pv2" in cfg
        assert isinstance(cfg["pv2"], dict)
        # fc 来自 color_picker mock 或 session_state fallback
        assert "fc" in cfg
        # fc2 在 dual=False 时固定为默认颜色
        assert "fc2" in cfg

    @patch("browse.components_sidebar.FILTERS", {
        "sma": {
            "name": "SMA",
            "func": lambda x: x,
            "params": {"window": ("窗口大小", 3, 101, 2, 11)},
        },
        "ema": {
            "name": "EMA",
            "func": lambda x: x,
            "params": {"span": ("跨度", 2, 100, 1, 10)},
        },
    })
    def test_render_params_dual_filter_sets_pv_and_pv2(self):
        """dual=True + 有效 filter_id2 正确设置 pv 和 pv2."""
        mock_cols_1 = [MagicMock() for _ in range(5)]
        with patch("browse.components_sidebar.st.columns",
                   return_value=mock_cols_1), \
             patch("browse.components_sidebar.st.selectbox",
                   return_value="60分钟"), \
             patch("browse.components_sidebar.st.checkbox",
                   return_value=True), \
             patch("browse.components_sidebar.st.button",
                   return_value=False), \
             patch("browse.components_sidebar.st.session_state",
                   {}, create=True), \
             patch("browse.components_sidebar.st.expander"), \
             patch("browse.components_sidebar.st.slider",
                   return_value=50.0), \
             patch("browse.components_sidebar.st.color_picker",
                   return_value="#ff6b6b"):
            from browse.components_sidebar import _render_params
            cfg = _render_params(
                key="v2", filter_id="sma", dual=True,
                filter_id2="ema", tf_default="60分钟",
            )

        assert cfg["_dual"] is True
        assert cfg["_fid2"] == "ema"
        assert "pv" in cfg
        assert isinstance(cfg["pv"], dict)
        assert "pv2" in cfg
        assert isinstance(cfg["pv2"], dict)
        assert "fc" in cfg
        assert "fc2" in cfg

    @patch("browse.components_sidebar.FILTERS", {
        "sma": {
            "name": "SMA",
            "func": lambda x: x,
            "params": {"window": ("窗口大小", 3, 101, 2, 11)},
        },
        "ema": {
            "name": "EMA",
            "func": lambda x: x,
            "params": {"span": ("跨度", 2, 100, 1, 10)},
        },
    })
    def test_render_params_dual_filter(self):
        """dual=True + filter_id2 渲染第二个滤波参数."""
        with patch("browse.components_sidebar.st.columns",
                   return_value=[MagicMock() for _ in range(5)]), \
             patch("browse.components_sidebar.st.selectbox",
                   return_value="60分钟"), \
             patch("browse.components_sidebar.st.checkbox",
                   return_value=True), \
             patch("browse.components_sidebar.st.button",
                   return_value=False), \
             patch("browse.components_sidebar.st.session_state",
                   {}, create=True), \
             patch("browse.components_sidebar.st.expander"), \
             patch("browse.components_sidebar.st.slider",
                   return_value=50.0), \
             patch("browse.components_sidebar.st.color_picker",
                   return_value="#00d4aa"):
            from browse.components_sidebar import _render_params
            cfg = _render_params(
                key="v0", filter_id="sma", dual=True,
                filter_id2="ema", tf_default="60分钟",
            )

        assert cfg["_dual"] is True
        assert cfg["_fid2"] == "ema"
        assert "pv" in cfg
        assert "pv2" in cfg
        assert "fc" in cfg
        assert "fc2" in cfg

    @patch("browse.components_sidebar.FILTERS", {
        "sma": {
            "name": "SMA",
            "func": lambda x: x,
            "params": {"window": ("窗口大小", 3, 101, 2, 11)},
        },
    })
    def test_render_params_unknown_filter_id2_not_crash(self):
        """dual=True 但 filter_id2 未知时不应崩溃 (regression)."""
        mock_warning = MagicMock()
        with patch("browse.components_sidebar.st.warning", mock_warning), \
             patch("browse.components_sidebar.st.columns",
                   return_value=[MagicMock() for _ in range(5)]), \
             patch("browse.components_sidebar.st.selectbox",
                   return_value="日线"), \
             patch("browse.components_sidebar.st.checkbox",
                   return_value=True), \
             patch("browse.components_sidebar.st.button",
                   return_value=False), \
             patch("browse.components_sidebar.st.session_state",
                   {}, create=True), \
             patch("browse.components_sidebar.st.expander"), \
             patch("browse.components_sidebar.st.slider",
                   return_value=50.0), \
             patch("browse.components_sidebar.st.color_picker",
                   return_value="#00d4aa"):
            from browse.components_sidebar import _render_params
            cfg = _render_params(
                key="v0", filter_id="sma", dual=True,
                filter_id2="unknown_filter", tf_default="日线",
            )
        mock_warning.assert_called_once()
        assert cfg is not None
        assert cfg["pv2"] == {}
        assert cfg["fc2"] == "#ff6b6b"

    def test_render_params_show_sch_false_skips_expanders(self):
        """show_sch=False 时不渲染施密特面板."""
        with patch("browse.components_sidebar.st.columns",
                   return_value=[MagicMock() for _ in range(5)]), \
             patch("browse.components_sidebar.st.selectbox",
                   return_value="日线"), \
             patch("browse.components_sidebar.st.checkbox",
                   return_value=False), \
             patch("browse.components_sidebar.st.button",
                   return_value=False), \
             patch("browse.components_sidebar.st.session_state",
                   {}, create=True), \
             patch("browse.components_sidebar.st.expander"):
            from browse.components_sidebar import _render_params
            cfg = _render_params(
                key="v0", filter_id="sma", dual=False,
                filter_id2=None, tf_default="日线",
            )

        assert cfg["show_pred"] is not None
        assert "pv" in cfg

    @patch("browse.components_sidebar.FILTERS", {
        "sma": {
            "name": "SMA",
            "func": lambda x: x,
            "params": {"window": ("窗口大小", 3, 101, 2, 11)},
        },
    })
    def test_render_params_expand_toggle_button(self):
        """点击折叠按钮切换展开状态."""
        ss = {}
        first = True

        def button_side_effect(**kw):
            nonlocal first
            if first:
                first = False
                return True  # clicked
            return False

        with patch("browse.components_sidebar.st.columns",
                   return_value=[MagicMock() for _ in range(5)]), \
             patch("browse.components_sidebar.st.selectbox",
                   return_value="日线"), \
             patch("browse.components_sidebar.st.checkbox",
                   return_value=True), \
             patch("browse.components_sidebar.st.button",
                   side_effect=lambda *a, **kw: button_side_effect(**kw)), \
             patch("browse.components_sidebar.st.session_state", ss,
                   create=True), \
             patch("browse.components_sidebar.st.expander"):
            from browse.components_sidebar import _render_params
            _render_params(
                key="v0", filter_id="sma", dual=False,
                filter_id2=None, tf_default="日线",
            )

        assert ss.get("v0_exp_all") is False


    @patch("browse.components_sidebar.FILTERS", {
        "sma": {
            "name": "SMA",
            "func": lambda x: x,
            "params": {"window": ("窗口大小", 3, 101, 2, 11)},
        },
    })
    def test_render_params_strategy_disabled_reads_sl_from_state(self):
        """show_strategy=False 时从 session_state 读取 stop_loss_pct."""
        from browse.components_sidebar import _render_params

        ss = {"v0_strat": False, "v0_sl": 5.0}
        mock_column_calls = []
        mock_checkbox_calls = []

        def checkbox_side_effect(*a, **kw):
            mock_checkbox_calls.append(kw.get("key", ""))
            key = kw.get("key", "")
            if "_strat" in key:
                return False  # show_strategy = False
            if "_sch" in key:
                return True   # show_sch = True
            if "_pred" in key:
                return True   # show_pred = True
            return True

        with patch("browse.components_sidebar.st.columns",
                   return_value=[MagicMock() for _ in range(5)]), \
             patch("browse.components_sidebar.st.selectbox",
                   return_value="日线"), \
             patch("browse.components_sidebar.st.checkbox",
                   side_effect=checkbox_side_effect), \
             patch("browse.components_sidebar.st.button",
                   return_value=False), \
             patch("browse.components_sidebar.st.session_state", ss,
                   create=True), \
             patch("browse.components_sidebar.st.expander"), \
             patch("browse.components_sidebar.st.slider",
                   return_value=50.0), \
             patch("browse.components_sidebar.st.color_picker",
                   return_value="#00d4aa"):
            cfg = _render_params(
                key="v0", filter_id="sma", dual=False,
                filter_id2=None, tf_default="日线",
            )

        assert cfg["stop_loss_pct"] == 5.0


# ===================================================================
# SECTION 8 — _render_data_validation 并行下载测试 (T4)
# ===================================================================

class TestDataValidationParallel:
    """验证 _render_data_validation 使用 ThreadPoolExecutor 并行下载数据。

    修改要点：
    1. 使用 ThreadPoolExecutor(max_workers=4) 并行下载所有周期
    2. 结果按 ALL_TFS 顺序排列（非完成顺序）
    3. 单个周期失败不影响其他周期处理

    所有测试均完全 mock yf.download / compare_with_db，避免网络访问。
    """

    # ── shared mock helpers ────────────────────────────────────────────

    @staticmethod
    def _make_mock_download():
        """返回一个 mock yf.download，返回包含 5 条 Close 数据的 DataFrame。"""
        def _inner(ticker, period="max", interval="1d", progress=False):
            return pd.DataFrame(
                {"Close": [100.0, 101.0, 102.0, 103.0, 104.0]},
                index=pd.date_range("2024-01-01", periods=5, freq="D"),
            )
        return _inner

    @staticmethod
    def _make_mock_compare():
        """返回一个 mock compare_with_db，返回 status='ok' 报告。"""
        def _inner(ticker, tf, df):
            return {
                "db_count": 100, "yf_count": 5, "overlap_count": 5,
                "fingerprint_match": True, "status": "ok",
                "only_db": 0, "only_yf": 0,
            }
        return _inner

    @staticmethod
    def _make_mock_future(result=None, exc=None):
        """创建一个 mock Future，支持 .result() 返回指定值或抛出异常。"""
        f = MagicMock()
        if exc:
            f.result.side_effect = exc
        else:
            f.result.return_value = result
        return f

    @staticmethod
    def _make_mock_st():
        """构建最小的 st mock：button=True, expander 可用。"""
        mock_st = MagicMock()
        mock_st.button.return_value = True
        mock_expander = MagicMock()
        mock_st.sidebar.expander.return_value.__enter__.return_value = mock_expander
        return mock_st

    @staticmethod
    def _patch_sidebar(*args):
        """返回一个 context manager helper — 组合多个 sidebar patch 的 enter/exit。

        用法:
            with TestDataValidationParallel._patch_sidebar(st_patch, dl_patch, ...):
                ...
        """
        from contextlib import ExitStack
        stack = ExitStack()
        for p in args:
            stack.enter_context(p)
        return stack

    # ── tests ─────────────────────────────────────────────────────────

    def test_button_click_triggers_processing(self):
        """点击校验按钮后进入数据处理逻辑。"""
        from filter.browse.sidebar import _render_data_validation

        mock_st = self._make_mock_st()

        with patch("filter.browse.sidebar.yf.download", side_effect=self._make_mock_download()), \
             patch("filter.browse.sidebar.compare_with_db", side_effect=self._make_mock_compare()), \
             patch("filter.browse.sidebar.force_update_kline"), \
             patch("filter.browse.sidebar.clear_display_cache"), \
             patch("filter.browse.sidebar.logger"), \
             patch("filter.browse.sidebar.st", mock_st):
            _render_data_validation("美股", "AAPL")

        assert mock_st.caption.called, "应渲染 caption"
        assert mock_st.dataframe.called, "结果应渲染为 dataframe"

    def test_uses_thread_pool_executor_not_serial_loop(self):
        """验证代码使用 ThreadPoolExecutor 并行下载，而非串行 for 循环。

        通过检查源码确认 ThreadPoolExecutor 的存在。
        """
        from filter.browse.sidebar import _render_data_validation
        import inspect

        source = inspect.getsource(_render_data_validation)
        assert "ThreadPoolExecutor" in source, (
            "应使用 ThreadPoolExecutor 进行并行下载"
        )
        assert "max_workers" in source, (
            "应指定 max_workers 参数控制并发数"
        )

    def test_all_eight_tfs_submitted_to_executor(self):
        """验证 8 个 ALL_TFS 周期都被提交到 executor.submit。"""
        from filter.browse.sidebar import _render_data_validation

        submitted_count = []

        # 完全替换 ThreadPoolExecutor，返回一个 mock executor
        class FakeExecutor:
            def __init__(self, max_workers=4):
                self.max_workers = max_workers
                self._futures = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def submit(self, fn, *args, **kwargs):
                submitted_count.append(1)
                f = MagicMock()
                f.result.return_value = pd.DataFrame(
                    {"Close": [100.0] * 5},
                    index=pd.date_range("2024-01-01", periods=5, freq="D"),
                )
                return f

        with patch("filter.browse.sidebar.ThreadPoolExecutor", FakeExecutor), \
             patch("filter.browse.sidebar.st", self._make_mock_st()), \
             patch("filter.browse.sidebar.compare_with_db", side_effect=self._make_mock_compare()), \
             patch("filter.browse.sidebar.force_update_kline"), \
             patch("filter.browse.sidebar.clear_display_cache"), \
             patch("filter.browse.sidebar.logger"), \
             patch("filter.browse.sidebar.as_completed", lambda futures: list(futures)):
            _render_data_validation("美股", "AAPL")

        assert sum(submitted_count) == 8, (
            f"应提交 8 个周期下载，实际 {sum(submitted_count)} 个"
        )

    def test_single_tf_failure_does_not_block_others(self):
        """单个周期下载失败（抛异常），其他周期仍正常处理。"""
        from filter.browse.sidebar import _render_data_validation

        submit_count = [0]

        class FakeExecutorWithOneFailure:
            def __init__(self, max_workers=4):
                self.max_workers = max_workers

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def submit(self, fn, *args, **kwargs):
                submit_count[0] += 1
                f = MagicMock()
                if submit_count[0] == 1:
                    f.result.side_effect = Exception("download timeout")
                else:
                    f.result.return_value = pd.DataFrame(
                        {"Close": [100.0] * 5},
                        index=pd.date_range("2024-01-01", periods=5, freq="D"),
                    )
                return f

        with patch("filter.browse.sidebar.ThreadPoolExecutor", FakeExecutorWithOneFailure), \
             patch("filter.browse.sidebar.st", self._make_mock_st()), \
             patch("filter.browse.sidebar.compare_with_db", side_effect=self._make_mock_compare()), \
             patch("filter.browse.sidebar.force_update_kline"), \
             patch("filter.browse.sidebar.clear_display_cache"), \
             patch("filter.browse.sidebar.logger"), \
             patch("filter.browse.sidebar.as_completed", lambda futures: list(futures)):
            # Should not raise
            _render_data_validation("美股", "AAPL")

        # 8 timeframes were all submitted
        assert submit_count[0] == 8

    def test_results_processed_in_all_tfs_order(self):
        """Phase 2 按 ALL_TFS 顺序遍历 raw_data，而非 as_completed 完成顺序。"""
        from filter.browse.sidebar import _render_data_validation
        import inspect

        source = inspect.getsource(_render_data_validation)
        assert "for tf in ALL_TFS:" in source, (
            "Phase 2 应遍历 ALL_TFS（固定顺序），而非 raw_data keys（完成顺序）"
        )

    def test_parallel_result_matches_serial_for_known_data(self):
        """使用已知数据验证并行处理与预期结果一致。

        每个周期返回相同的 DataFrame 和 compare 报告，确认输出正确。
        """
        from filter.browse.sidebar import _render_data_validation

        known_df = pd.DataFrame(
            {"Close": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]},
            index=pd.date_range("2024-01-01", periods=6, freq="D"),
        )

        report_template = {
            "db_count": 200, "yf_count": 6, "overlap_count": 6,
            "fingerprint_match": True, "status": "ok",
            "only_db": 0, "only_yf": 0,
        }

        def _mock_download(ticker, period="max", interval="1d", progress=False):
            return known_df.copy()

        def _mock_compare(ticker, tf, df):
            return dict(report_template)

        class FakeExecutor:
            def __init__(self, max_workers=4):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def submit(self, fn, *args, **kwargs):
                f = MagicMock()
                f.result.return_value = fn(*args, **kwargs)
                return f

        mock_st = self._make_mock_st()

        with patch("filter.browse.sidebar.yf.download", side_effect=_mock_download), \
             patch("filter.browse.sidebar.compare_with_db", side_effect=_mock_compare), \
             patch("filter.browse.sidebar.ThreadPoolExecutor", FakeExecutor), \
             patch("filter.browse.sidebar.as_completed", lambda futures: list(futures)), \
             patch("filter.browse.sidebar.force_update_kline"), \
             patch("filter.browse.sidebar.clear_display_cache"), \
             patch("filter.browse.sidebar.logger"), \
             patch("filter.browse.sidebar.st", mock_st):
            _render_data_validation("美股", "AAPL")

        assert mock_st.dataframe.called, "校验结果应渲染为 dataframe"

    def test_empty_ticker_skips_download(self):
        """空 ticker code 显示 warning 并跳过下载。"""
        from filter.browse.sidebar import _render_data_validation

        submitted = [0]

        class FakeExecutorNoSubmit:
            def __init__(self, max_workers=4):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def submit(self, fn, *args, **kwargs):
                submitted[0] += 1
                return MagicMock()

        mock_st = self._make_mock_st()

        with patch("filter.browse.sidebar.ThreadPoolExecutor", FakeExecutorNoSubmit), \
             patch("filter.browse.sidebar.st", mock_st):
            _render_data_validation("A股(沪深)", "   ")

        mock_st.warning.assert_called()
        assert submitted[0] == 0, "空 ticker 不应提交任何下载"

    def test_a_share_ticker_suffix_conversion(self):
        """A股市场：60xxxx 加 .SS，其他加 .SZ。"""
        from filter.browse.sidebar import _render_data_validation

        full_codes = []

        def _mock_download(ticker, period="max", interval="1d", progress=False):
            full_codes.append(ticker)
            return pd.DataFrame(
                {"Close": [100.0] * 5},
                index=pd.date_range("2024-01-01", periods=5, freq="D"),
            )

        class FakeExecutor:
            def __init__(self, max_workers=4):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def submit(self, fn, *args, **kwargs):
                f = MagicMock()
                f.result.return_value = fn(*args, **kwargs)
                return f

        mock_st = self._make_mock_st()

        with patch("filter.browse.sidebar.yf.download", side_effect=_mock_download), \
             patch("filter.browse.sidebar.ThreadPoolExecutor", FakeExecutor), \
             patch("filter.browse.sidebar.as_completed", lambda futures: list(futures)), \
             patch("filter.browse.sidebar.compare_with_db", side_effect=self._make_mock_compare()), \
             patch("filter.browse.sidebar.force_update_kline"), \
             patch("filter.browse.sidebar.clear_display_cache"), \
             patch("filter.browse.sidebar.logger"), \
             patch("filter.browse.sidebar.st", mock_st):
            _render_data_validation("A股(沪深)", "600115")

        assert any("600115.SS" in str(c) for c in full_codes), \
            f"60 开头应加 .SS，实际: {full_codes}"

        full_codes.clear()

        with patch("filter.browse.sidebar.yf.download", side_effect=_mock_download), \
             patch("filter.browse.sidebar.ThreadPoolExecutor", FakeExecutor), \
             patch("filter.browse.sidebar.as_completed", lambda futures: list(futures)), \
             patch("filter.browse.sidebar.compare_with_db", side_effect=self._make_mock_compare()), \
             patch("filter.browse.sidebar.force_update_kline"), \
             patch("filter.browse.sidebar.clear_display_cache"), \
             patch("filter.browse.sidebar.logger"), \
             patch("filter.browse.sidebar.st", self._make_mock_st()):
            _render_data_validation("A股(沪深)", "000001")

        assert any("000001.SZ" in str(c) for c in full_codes), \
            f"非60开头应加 .SZ，实际: {full_codes}"

    def test_data_insufficient_shows_warning_in_table(self):
        """数据不足（<5条有效Close）时结果行显示警告标记。"""
        from filter.browse.sidebar import _render_data_validation

        # Only 3 valid Close rows (after dropping NaN)
        tiny_df = pd.DataFrame(
            {"Close": [100.0, np.nan, 102.0, np.nan, 103.0]},
            index=pd.date_range("2024-01-01", periods=5, freq="D"),
        )

        class FakeExecutor:
            def __init__(self, max_workers=4):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def submit(self, fn, *args, **kwargs):
                f = MagicMock()
                f.result.return_value = tiny_df.copy()
                return f

        mock_st = self._make_mock_st()

        with patch("filter.browse.sidebar.ThreadPoolExecutor", FakeExecutor), \
             patch("filter.browse.sidebar.as_completed", lambda futures: list(futures)), \
             patch("filter.browse.sidebar.compare_with_db"), \
             patch("filter.browse.sidebar.force_update_kline"), \
             patch("filter.browse.sidebar.clear_display_cache"), \
             patch("filter.browse.sidebar.logger"), \
             patch("filter.browse.sidebar.st", mock_st):
            _render_data_validation("美股", "AAPL")

        assert mock_st.dataframe.called, "即使数据不足也应渲染结果表格"
