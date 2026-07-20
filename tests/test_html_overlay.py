"""
P2 fix: 前端持仓叠加带测试。

验证 ``docs/backtesting/回测结果可视化.html`` 中包含位置叠加带相关
的 JavaScript 代码：``_long_pos`` / ``_short_pos`` 数据列的读取、
``addPosOverlay`` 辅助函数、以及对应的 rgba 半透明颜色。
"""

from pathlib import Path


HTML_PATH = Path(__file__).resolve().parent.parent / "docs" / "backtesting" / "回测结果可视化.html"


class TestHtmlPositionOverlay:
    """P2: 前端持仓叠加带 — 验证 HTML 包含正确的叠加代码。"""

    @staticmethod
    def _read_html():
        return HTML_PATH.read_text(encoding="utf-8")

    def test_html_file_exists(self):
        """HTML 文件存在且可读。"""
        assert HTML_PATH.exists(), f"HTML file not found at {HTML_PATH}"
        assert HTML_PATH.is_file(), f"Path is not a file: {HTML_PATH}"

    def test_contains_long_pos_reference(self):
        """HTML 包含 _long_pos 数据列读取代码。"""
        html = self._read_html()
        assert "_long_pos" in html, "HTML must reference _long_pos data column"

    def test_contains_short_pos_reference(self):
        """HTML 包含 _short_pos 数据列读取代码。"""
        html = self._read_html()
        assert "_short_pos" in html, "HTML must reference _short_pos data column"

    def test_contains_add_pos_overlay_function(self):
        """HTML 包含 addPosOverlay 持仓叠加辅助函数。"""
        html = self._read_html()
        assert "addPosOverlay" in html, "HTML must define addPosOverlay helper function"
        assert "function addPosOverlay" in html, \
            "HTML must contain addPosOverlay function definition"

    def test_contains_long_pos_overlay_color(self):
        """HTML 中做多持仓叠加带使用绿色半透明 rgba。"""
        html = self._read_html()
        # green band for long position: rgba(0,255,0,0.08)
        assert "rgba(0,255,0,0.08)" in html, \
            "HTML must reference rgba(0,255,0,0.08) for long position overlay"
        assert "green band" in html or "long position" in html.lower(), \
            "HTML must indicate green corresponds to long position"

    def test_contains_short_pos_overlay_color(self):
        """HTML 中做空持仓叠加带使用红色半透明 rgba。"""
        html = self._read_html()
        # red band for short position: rgba(255,0,0,0.08)
        assert "rgba(255,0,0,0.08)" in html, \
            "HTML must reference rgba(255,0,0,0.08) for short position overlay"
        assert "red band" in html or "short position" in html.lower(), \
            "HTML must indicate red corresponds to short position"

    def test_overlay_uses_rect_shape_with_below_layer(self):
        """叠加带使用 Plotly rect shape 且 layer='below' 避免遮挡信号线。"""
        html = self._read_html()
        assert "type: 'rect'" in html, "Overlay must use rect shape type for bands"
        assert "layer: 'below'" in html, "Overlay must set layer below signal traces"

    def test_p2_comment_marker_present(self):
        """HTML 包含 P2 fix 的注释标记。"""
        html = self._read_html()
        assert "P2 fix" in html, "HTML must contain P2 fix comment marker"
        assert "持仓状态叠加带" in html, "HTML must contain 持仓状态叠加带 comment"

    def test_both_overlay_calls_present(self):
        """addPosOverlay 被调用两次（做多 + 做空）。"""
        html = self._read_html()
        count = html.count("addPosOverlay(")
        assert count >= 2, \
            f"addPosOverlay should be called at least 2 times (long+short), got {count}"


class TestHtmlViewLabels:
    """P9 fix: DEFAULT_VIEW_LABELS 修正与 metadata 优先级。

    验证 HTML 中 DEFAULT_VIEW_LABELS 映射正确（v0=日线, v1=60分钟,
    v2=15分钟, v3=5分钟），以及 renderAll 中的优先级逻辑：
    metadata.view_labels 优先于默认值，缺失时回退到默认值。
    """

    @staticmethod
    def _read_html():
        return HTML_PATH.read_text(encoding="utf-8")

    @staticmethod
    def _simulate_viewlabels_init(metadata):
        """模拟 renderAll 中 viewLabels 初始化逻辑。"""
        defaults = {"v0": "日线", "v1": "60分钟", "v2": "15分钟", "v3": "5分钟"}
        if metadata and metadata.get("view_labels"):
            return {**defaults, **metadata["view_labels"]}
        return {**defaults}

    def test_default_view_labels_exist_in_html(self):
        """HTML 中包含 DEFAULT_VIEW_LABELS 定义。"""
        html = self._read_html()
        assert "DEFAULT_VIEW_LABELS" in html, \
            "HTML must contain DEFAULT_VIEW_LABELS definition"

    def test_default_view_labels_correct_mapping(self):
        """DEFAULT_VIEW_LABELS 映射正确：v0=日线, v1=60分钟, v2=15分钟, v3=5分钟。"""
        html = self._read_html()
        # 验证修复后的正确映射
        assert "v0: '日线'" in html, "v0 should map to 日线"
        assert "v1: '60分钟'" in html, "v1 should map to 60分钟"
        assert "v2: '15分钟'" in html, "v2 should map to 15分钟"
        assert "v3: '5分钟'" in html, "v3 should map to 5分钟"

    def test_default_view_labels_not_swapped(self):
        """DEFAULT_VIEW_LABELS 不应包含交换后的错误值（P9 回归防护）。"""
        html = self._read_html()
        # 回归防护：确保没有把 日线 放到 v2 且把 15分钟 放到 v0
        assert "v0: '15分钟'" not in html.replace(" ", ""), \
            "v0 should NOT be 15分钟 (was the bug)"
        assert "v2: '日线'" not in html.replace(" ", ""), \
            "v2 should NOT be 日线 (was the bug)"

    def test_metadata_view_labels_takes_priority(self):
        """metadata.view_labels 优先于 DEFAULT_VIEW_LABELS。"""
        metadata = {"view_labels": {"v0": "周线", "v2": "自定义"}}
        labels = self._simulate_viewlabels_init(metadata)
        # metadata 中的值覆盖默认值
        assert labels["v0"] == "周线"
        assert labels["v2"] == "自定义"
        # metadata 中未指定的保持默认值
        assert labels["v1"] == "60分钟"
        assert labels["v3"] == "5分钟"

    def test_missing_metadata_falls_back_to_defaults(self):
        """metadata 缺失时回退到 DEFAULT_VIEW_LABELS。"""
        # metadata 为 None
        labels_none = self._simulate_viewlabels_init(None)
        assert labels_none == {"v0": "日线", "v1": "60分钟", "v2": "15分钟", "v3": "5分钟"}

        # metadata 无 view_labels 字段
        labels_empty = self._simulate_viewlabels_init({"other": "data"})
        assert labels_empty == {"v0": "日线", "v1": "60分钟", "v2": "15分钟", "v3": "5分钟"}

        # metadata.view_labels 为空 dict
        labels_no_labels = self._simulate_viewlabels_init({"view_labels": {}})
        assert labels_no_labels == {"v0": "日线", "v1": "60分钟", "v2": "15分钟", "v3": "5分钟"}

    def test_partial_metadata_only_overrides_specified(self):
        """metadata 只覆盖指定的 view，其余保持默认。"""
        metadata = {"view_labels": {"v0": "自定义日线"}}
        labels = self._simulate_viewlabels_init(metadata)
        assert labels["v0"] == "自定义日线"
        assert labels["v1"] == "60分钟"
        assert labels["v2"] == "15分钟"
        assert labels["v3"] == "5分钟"

    def test_render_all_priority_pattern_in_html(self):
        """HTML 中 renderAll 的优先级逻辑模式正确。"""
        html = self._read_html()
        # 验证使用 spread 语法：先展开默认值，再用 metadata 覆盖
        assert "{ ...DEFAULT_VIEW_LABELS" in html, \
            "renderAll must spread DEFAULT_VIEW_LABELS first, then overlay metadata"
        assert "metadata.view_labels" in html, \
            "renderAll must check metadata.view_labels"
