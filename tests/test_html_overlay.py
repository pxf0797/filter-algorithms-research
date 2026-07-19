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
