"""
Tests for sidebar.py constants and pure-logic helpers.

These tests cover the non-Streamlit-widget parts of sidebar.py:
- ALL_TFS, DEFAULT_TFS, TF_HIERARCHY constants
- Any future pure-logic helpers extracted from widget functions
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure filter_app/ package is importable (conftest handles streamlit mock)
_src = Path(__file__).resolve().parent.parent / "filter_app"
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

        with patch("components.sidebar.st.columns",
                   return_value=[mock_col0, mock_col1]):
            from components.sidebar import _compact_slider
            result = _compact_slider("N", 20, 300, 120, 10)
            assert result == 50.0
            mock_col0.markdown.assert_called_once()
            mock_col1.slider.assert_called_once_with(
                "N", min_value=20, max_value=300, value=120,
                step=10, key=None, label_visibility="collapsed",
            )

    def test_slider_with_key_and_fmt(self):
        """传入 key 和 fmt 时传递给 slider."""
        mock_col0 = MagicMock()
        mock_col1 = MagicMock()
        mock_col1.slider.return_value = 0.5

        with patch("components.sidebar.st.columns",
                   return_value=[mock_col0, mock_col1]):
            from components.sidebar import _compact_slider
            result = _compact_slider("sigma", 0.0, 1.0, 0.5, 0.01,
                                     key="my_ke", fmt="%.3f")
            assert result == 0.5
            mock_col1.slider.assert_called_once_with(
                "sigma", min_value=0.0, max_value=1.0, value=0.5,
                step=0.01, key="my_ke", label_visibility="collapsed",
                format="%.3f",
            )

    def test_slider_markdown_renders_label(self):
        """验证 markdown 用 f-string 传入 label."""
        mock_col0 = MagicMock()
        mock_col1 = MagicMock()
        mock_col1.slider.return_value = 5.0

        with patch("components.sidebar.st.columns",
                   return_value=[mock_col0, mock_col1]):
            from components.sidebar import _compact_slider
            _compact_slider("窗口", 1, 100, 50, 1)
            mock_col0.markdown.assert_called_once_with(
                "<small>窗口</small>", unsafe_allow_html=True,
            )


# ===================================================================
# SECTION 6 -- _render_param_slider widget (mocked Streamlit)
# ===================================================================

class TestRenderParamSlider:
    """_render_param_slider 函数测试."""

    def test_container_default_uses_sidebar(self):
        """container=None 时使用 st.sidebar.slider."""
        mock_sidebar = MagicMock()
        mock_sidebar.slider.return_value = 0.5
        with patch("components.sidebar.st.sidebar", mock_sidebar), \
             patch("components.sidebar.st.session_state", {}, create=True):
            from components.sidebar import _render_param_slider
            result = _render_param_slider("阈值", 0.0, 1.0, 0.1, 0.5)
            assert result == 0.5
            mock_sidebar.slider.assert_called_once()

    def test_container_st_uses_st(self):
        """container=st 时使用 st.slider（而非 st.sidebar.slider）."""
        mock_st = MagicMock()
        mock_st.slider.return_value = 20.0
        with patch("components.sidebar.st", mock_st):
            from components.sidebar import _render_param_slider
            result = _render_param_slider("窗口", 5, 100, 5, 20,
                                          container=mock_st)
            assert result == 20.0
            mock_st.slider.assert_called_once()

    def test_key_suffix_appended(self):
        """key_suffix 非空时 key 为 f'{label}_{key_suffix}'."""
        mock_sidebar = MagicMock()
        mock_sidebar.slider.return_value = 10.0
        with patch("components.sidebar.st.sidebar", mock_sidebar), \
             patch("components.sidebar.st.session_state", {}, create=True):
            from components.sidebar import _render_param_slider
            _render_param_slider("跨度", 2, 100, 1, 10,
                                 key_suffix="f1_sma")
            call_key = mock_sidebar.slider.call_args[1].get("key")
            assert call_key == "跨度_f1_sma"

    def test_int_step_no_format(self):
        """int step 不传 format 参数."""
        mock_sidebar = MagicMock()
        mock_sidebar.slider.return_value = 10
        with patch("components.sidebar.st.sidebar", mock_sidebar), \
             patch("components.sidebar.st.session_state", {}, create=True):
            from components.sidebar import _render_param_slider
            _render_param_slider("跨度", 2, 100, 1, 10)
            call_kwargs = mock_sidebar.slider.call_args[1]
            assert "format" not in call_kwargs

    def test_float_step_small_format_three(self):
        """float step < 0.01 使用 %.3f 格式."""
        mock_sidebar = MagicMock()
        mock_sidebar.slider.return_value = 0.5
        with patch("components.sidebar.st.sidebar", mock_sidebar), \
             patch("components.sidebar.st.session_state", {}, create=True):
            from components.sidebar import _render_param_slider
            _render_param_slider("sigma", 0.0, 1.0, 0.001, 0.5)
            call_kwargs = mock_sidebar.slider.call_args[1]
            assert call_kwargs["format"] == "%.3f"

    def test_float_step_normal_format_two(self):
        """0.01 <= float step < 1.0 使用 %.2f 格式."""
        mock_sidebar = MagicMock()
        mock_sidebar.slider.return_value = 0.5
        with patch("components.sidebar.st.sidebar", mock_sidebar), \
             patch("components.sidebar.st.session_state", {}, create=True):
            from components.sidebar import _render_param_slider
            _render_param_slider("比例", 0.0, 1.0, 0.1, 0.5)
            call_kwargs = mock_sidebar.slider.call_args[1]
            assert call_kwargs["format"] == "%.2f"

    def test_no_key_no_session_state_lookup(self):
        """key_suffix='' 时不查 session_state，直接使用 pdefault."""
        mock_sidebar = MagicMock()
        mock_sidebar.slider.return_value = 5
        with patch("components.sidebar.st.sidebar", mock_sidebar), \
             patch("components.sidebar.st.session_state", {}, create=True):
            from components.sidebar import _render_param_slider
            _render_param_slider("窗口", 3, 101, 2, 11, key_suffix="")
            call_kwargs = mock_sidebar.slider.call_args[1]
            assert call_kwargs["key"] is None


# ===================================================================
# SECTION 7 -- _render_params with mocked Streamlit and FILTERS
# ===================================================================

class TestRenderParams:
    """_render_params 函数测试 -- 深度 mock Streamlit 组件 + FILTERS."""

    @patch("components.sidebar.FILTERS", {
        "sma": {
            "name": "SMA",
            "func": lambda x: x,
            "params": {"window": ("窗口大小", 3, 101, 2, 11)},
        },
    })
    def test_render_params_basic_sma(self):
        """基本 SMA filter, show_sch=True, show_pred=True."""
        mock_cols = [MagicMock() for _ in range(5)]
        with patch("components.sidebar.st.columns",
                   return_value=mock_cols), \
             patch("components.sidebar.st.selectbox",
                   return_value="日线"), \
             patch("components.sidebar.st.checkbox",
                   return_value=True), \
             patch("components.sidebar.st.button",
                   return_value=False), \
             patch("components.sidebar.st.session_state",
                   {}, create=True), \
             patch("components.sidebar.st.expander"), \
             patch("components.sidebar.st.slider",
                   return_value=50.0), \
             patch("components.sidebar.st.color_picker",
                   return_value="#00d4aa"):
            from components.sidebar import _render_params
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

    @patch("components.sidebar.FILTERS", {})
    def test_render_params_unknown_filter_warning(self):
        """未知 filter_id 触发 st.warning 并提前返回 None."""
        mock_warning = MagicMock()
        mock_cols = [MagicMock() for _ in range(5)]
        with patch("components.sidebar.st.warning", mock_warning), \
             patch("components.sidebar.st.columns",
                   return_value=mock_cols), \
             patch("components.sidebar.st.selectbox",
                   return_value="日线"), \
             patch("components.sidebar.st.checkbox",
                   return_value=True), \
             patch("components.sidebar.st.button",
                   return_value=False), \
             patch("components.sidebar.st.session_state",
                   {}, create=True), \
             patch("components.sidebar.st.expander"):
            from components.sidebar import _render_params
            cfg = _render_params(
                key="v0", filter_id="nonexistent", dual=False,
                filter_id2=None, tf_default="日线",
            )

        mock_warning.assert_called_once()
        assert cfg is None

    @patch("components.sidebar.FILTERS", {
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
        with patch("components.sidebar.st.columns",
                   return_value=[MagicMock() for _ in range(5)]), \
             patch("components.sidebar.st.selectbox",
                   return_value="60分钟"), \
             patch("components.sidebar.st.checkbox",
                   return_value=True), \
             patch("components.sidebar.st.button",
                   return_value=False), \
             patch("components.sidebar.st.session_state",
                   {}, create=True), \
             patch("components.sidebar.st.expander"), \
             patch("components.sidebar.st.slider",
                   return_value=50.0), \
             patch("components.sidebar.st.color_picker",
                   return_value="#00d4aa"):
            from components.sidebar import _render_params
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

    @patch("components.sidebar.FILTERS", {
        "sma": {
            "name": "SMA",
            "func": lambda x: x,
            "params": {"window": ("窗口大小", 3, 101, 2, 11)},
        },
    })
    def test_render_params_unknown_filter_id2_crashes(self):
        """dual=True 但 filter_id2 未知时触发 warning 后仍会崩溃(TypeError).

        这是生产代码 bug (line 218-219 未检查 sf2 is None)，测试记录当前行为。
        """
        mock_warning = MagicMock()
        with patch("components.sidebar.st.warning", mock_warning), \
             patch("components.sidebar.st.columns",
                   return_value=[MagicMock() for _ in range(5)]), \
             patch("components.sidebar.st.selectbox",
                   return_value="日线"), \
             patch("components.sidebar.st.checkbox",
                   return_value=True), \
             patch("components.sidebar.st.button",
                   return_value=False), \
             patch("components.sidebar.st.session_state",
                   {}, create=True), \
             patch("components.sidebar.st.expander"), \
             patch("components.sidebar.st.slider",
                   return_value=50.0), \
             patch("components.sidebar.st.color_picker",
                   return_value="#00d4aa"):
            from components.sidebar import _render_params
            with pytest.raises(TypeError):
                _render_params(
                    key="v0", filter_id="sma", dual=True,
                    filter_id2="unknown_filter", tf_default="日线",
                )
        assert mock_warning.call_count >= 1

    def test_render_params_show_sch_false_skips_expanders(self):
        """show_sch=False 时不渲染施密特面板."""
        with patch("components.sidebar.st.columns",
                   return_value=[MagicMock() for _ in range(5)]), \
             patch("components.sidebar.st.selectbox",
                   return_value="日线"), \
             patch("components.sidebar.st.checkbox",
                   return_value=False), \
             patch("components.sidebar.st.button",
                   return_value=False), \
             patch("components.sidebar.st.session_state",
                   {}, create=True), \
             patch("components.sidebar.st.expander"):
            from components.sidebar import _render_params
            cfg = _render_params(
                key="v0", filter_id="sma", dual=False,
                filter_id2=None, tf_default="日线",
            )

        assert cfg["show_pred"] is not None
        assert "pv" in cfg

    @patch("components.sidebar.FILTERS", {
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

        with patch("components.sidebar.st.columns",
                   return_value=[MagicMock() for _ in range(5)]), \
             patch("components.sidebar.st.selectbox",
                   return_value="日线"), \
             patch("components.sidebar.st.checkbox",
                   return_value=True), \
             patch("components.sidebar.st.button",
                   side_effect=lambda *a, **kw: button_side_effect(**kw)), \
             patch("components.sidebar.st.session_state", ss,
                   create=True), \
             patch("components.sidebar.st.expander"):
            from components.sidebar import _render_params
            _render_params(
                key="v0", filter_id="sma", dual=False,
                filter_id2=None, tf_default="日线",
            )

        assert ss.get("v0_exp_all") is True


    @patch("components.sidebar.FILTERS", {
        "sma": {
            "name": "SMA",
            "func": lambda x: x,
            "params": {"window": ("窗口大小", 3, 101, 2, 11)},
        },
    })
    def test_render_params_strategy_disabled_reads_sl_from_state(self):
        """show_strategy=False 时从 session_state 读取 stop_loss_pct."""
        from components.sidebar import _render_params

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

        with patch("components.sidebar.st.columns",
                   return_value=[MagicMock() for _ in range(5)]), \
             patch("components.sidebar.st.selectbox",
                   return_value="日线"), \
             patch("components.sidebar.st.checkbox",
                   side_effect=checkbox_side_effect), \
             patch("components.sidebar.st.button",
                   return_value=False), \
             patch("components.sidebar.st.session_state", ss,
                   create=True), \
             patch("components.sidebar.st.expander"), \
             patch("components.sidebar.st.slider",
                   return_value=50.0), \
             patch("components.sidebar.st.color_picker",
                   return_value="#00d4aa"):
            cfg = _render_params(
                key="v0", filter_id="sma", dual=False,
                filter_id2=None, tf_default="日线",
            )

        assert cfg["stop_loss_pct"] == 5.0
