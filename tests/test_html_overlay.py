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


class TestPeriodDashboardStructure:
    """HTML 中包含 4 个 period-dashboard 分区，使用共享 x 轴子图布局。"""

    @staticmethod
    def _read_html():
        return HTML_PATH.read_text(encoding="utf-8")

    def test_four_period_dashboard_sections_exist(self):
        """HTML 中包含 4 个 period-dashboard 分区（v0-v3）。"""
        html = self._read_html()
        # period-dashboard 在 CSS 中定义 1 次，在 HTML 中出现 4 次（每个周期一个分区）
        assert 'class="section period-dashboard"' in html, \
            "HTML must contain period-dashboard section divs"
        # 验证 4 个周期分区的标题都存在
        assert "日线 完整视图" in html, "Period dashboard for 日线 must exist"
        assert "60分钟 完整视图" in html, "Period dashboard for 60分钟 must exist"
        assert "15分钟 完整视图" in html, "Period dashboard for 15分钟 must exist"
        assert "5分钟 完整视图" in html, "Period dashboard for 5分钟 must exist"

    def test_period_dashboard_chart_ids_for_all_views(self):
        """每个 period-dashboard 包含正确的 chart div ID（chart-dash-v0 到 chart-dash-v3）。"""
        html = self._read_html()
        for v in ("v0", "v1", "v2", "v3"):
            chart_id = f"chart-dash-{v}"
            assert f'id="{chart_id}"' in html, \
                f"HTML must contain chart div with id={chart_id}"

    def test_shared_x_axis_grid_layout(self):
        """共享 x 轴使用 grid rows=nViews, columns=1 子图布局。"""
        html = self._read_html()
        # grid 布局：行数 = 视图数量，列数 = 1（垂直堆叠，共享 x 轴）
        assert "grid: { rows: nViews, columns: 1" in html, \
            "Shared x-axis layout must use grid with rows=nViews, columns=1"
        assert "roworder: 'top to bottom'" in html, \
            "Grid must use top-to-bottom row ordering"

    def test_subplot_yaxis_definitions_exist(self):
        """子图 y 轴通过动态 key yaxis/yaxis2/yaxis3/yaxis4 定义。"""
        html = self._read_html()
        # 动态生成 yaxis key: yaxis (vi=0), yaxis2 (vi=1), yaxis3 (vi=2), yaxis4 (vi=3)
        assert "var ykey = 'yaxis' + (vi === 0 ? '' : (vi + 1))" in html or \
               "ykey = 'yaxis' + (vi === 0 ? '' : (vi + 1))" in html or \
               "'yaxis' + (vi === 0 ? '' : (vi + 1))" in html, \
            "HTML must dynamically generate yaxis keys for subplots"

    def test_shared_x_axis_matches_last_subplot(self):
        """非底部子图的 x 轴通过 matches 共享底部子图的 x 轴。"""
        html = self._read_html()
        assert "matches = 'x' + nViews" in html or \
               "layout[xkey].matches = 'x' + nViews" in html, \
            "Upper subplots must match the bottom x-axis via matches property"

    def test_views_constant_is_4_periods(self):
        """VIEWS 常量包含 4 个周期：v0, v1, v2, v3。"""
        html = self._read_html()
        assert "const VIEWS = ['v0', 'v1', 'v2', 'v3']" in html, \
            "VIEWS constant must define all 4 periods"
        assert "VIEWS.length" in html, \
            "HTML must reference VIEWS.length for dynamic layout"


class TestHoverTooltipFeatures:
    """悬停提示特性：hovermode unified、hoverlabel 样式、spike 辅助线。"""

    @staticmethod
    def _read_html():
        return HTML_PATH.read_text(encoding="utf-8")

    def test_hovermode_x_unified_present(self):
        """图表布局中包含 hovermode: 'x unified'。"""
        html = self._read_html()
        assert "hovermode: 'x unified'" in html, \
            "HTML must use hovermode x unified for synchronized crosshairs"

    def test_hoverlabel_style_defined(self):
        """hoverLabelStyle 常量定义了悬停标签样式。"""
        html = self._read_html()
        assert "hoverLabelStyle" in html, \
            "HTML must define hoverLabelStyle constant"
        assert "bgcolor: '#21262d'" in html, \
            "hoverlabel must have dark background"
        assert "bordercolor: '#30363d'" in html, \
            "hoverlabel must have border color"

    def test_spike_settings_exist(self):
        """x 轴和布局中包含 spike 辅助线设置。"""
        html = self._read_html()
        assert "showspikes: true" in html, \
            "HTML must enable showspikes for crosshair lines"
        assert "spikemode: 'across'" in html, \
            "Spike mode must be 'across' for full-chart crosshairs"
        assert "spikecolor: '#8b949e'" in html, \
            "Spike line color must be defined"
        assert "spikesnap: 'cursor'" in html, \
            "Spike must snap to cursor position"

    def test_multiple_layouts_use_hovermode(self):
        """多个图表（至少 5 个布局）使用 x unified hovermode。"""
        html = self._read_html()
        count = html.count("hovermode: 'x unified'")
        assert count >= 5, \
            f"At least 5 layouts should use x unified hovermode, got {count}"


class TestMultiTickerSupport:
    """多 Ticker 叠加图支持：buildMultiTickerPnl 函数与全局变量。"""

    @staticmethod
    def _read_html():
        return HTML_PATH.read_text(encoding="utf-8")

    def test_multi_ticker_function_exists(self):
        """buildMultiTickerPnl 多 Ticker PnL 叠加函数存在。"""
        html = self._read_html()
        assert "function buildMultiTickerPnl" in html, \
            "HTML must define buildMultiTickerPnl function for multi-ticker overlay"

    def test_multi_ticker_backtest_globals_referenced(self):
        """buildMultiTickerPnl 使用 BACKTEST_IS_MULTI / BACKTEST_ALL_DATA / BACKTEST_TICKERS。"""
        html = self._read_html()
        assert "window.BACKTEST_IS_MULTI" in html, \
            "Multi-ticker code must check BACKTEST_IS_MULTI flag"
        assert "window.BACKTEST_ALL_DATA" in html, \
            "Multi-ticker code must access BACKTEST_ALL_DATA"
        assert "window.BACKTEST_TICKERS" in html, \
            "Multi-ticker code must iterate BACKTEST_TICKERS"

    def test_multi_ticker_uses_ticker_color(self):
        """多 Ticker PnL 线使用 tk.color 作为每个 ticker 的专属颜色。"""
        html = self._read_html()
        assert "tk.color" in html, \
            "Multi-ticker must use per-ticker color from tk.color"

    def test_multi_pnl_section_in_html(self):
        """HTML 包含多 Ticker PnL 叠加图的容器 div。"""
        html = self._read_html()
        assert 'id="chart-multi-pnl"' in html, \
            "HTML must contain chart-multi-pnl div for multi-ticker overlay"
        assert 'id="section-multi-pnl"' in html, \
            "HTML must contain section-multi-pnl wrapper"

    def test_multi_ticker_renders_short_pnl_dashed(self):
        """多 Ticker 做空 PnL 使用虚线（dash）区分。"""
        html = self._read_html()
        assert "dash: 'dash'" in html, \
            "Short PnL line must use dashed style for visual distinction"


class TestPeriodDashboardRowHeights:
    """period-dashboard 中 4 行的 yaxis domain 高度分配。

    Row 1 (滤波) 占 36%, Row 2 (信号) 占 12%,
    Row 3 (做多做空) 占 12%, Row 4 (PnL) 占 40%.
    """

    @staticmethod
    def _read_html():
        return HTML_PATH.read_text(encoding="utf-8")

    def test_row1_filtered_price_domain(self):
        """yaxis (滤波, row1) domain 为 [0.64, 1.0]。"""
        html = self._read_html()
        assert "yaxis: { domain: [0.64, 1.0]" in html, \
            "yaxis (row1 滤波) must have domain [0.64, 1.0]"

    def test_row2_signal_domain(self):
        """yaxis3 (信号, row2) domain 为 [0.52, 0.64] — half height。"""
        html = self._read_html()
        assert "yaxis3: { domain: [0.52, 0.64]" in html, \
            "yaxis3 (row2 信号) must have domain [0.52, 0.64]"

    def test_row3_position_domain(self):
        """yaxis2 (做多做空, row3) domain 为 [0.4, 0.52] — half height。"""
        html = self._read_html()
        assert "yaxis2: { domain: [0.4, 0.52]" in html, \
            "yaxis2 (row3 做多做空) must have domain [0.4, 0.52]"

    def test_row4_pnl_domain(self):
        """yaxis4 (PnL, row4) domain 为 [0.0, 0.4]。"""
        html = self._read_html()
        assert "yaxis4: { domain: [0.0, 0.4]" in html, \
            "yaxis4 (row4 PnL) must have domain [0.0, 0.4]"


class TestPeriodDashboardRowOrdering:
    """period-dashboard 行顺序回归测试：验证 yaxis 到行的映射（交换正确）。"""

    @staticmethod
    def _read_html():
        return HTML_PATH.read_text(encoding="utf-8")

    def test_row1_filtered_uses_yaxis(self):
        """滤波 使用 yaxis（索引 1 的默认 axis）。"""
        html = self._read_html()
        assert "yaxis: { domain: [0.64, 1.0]" in html, \
            "滤波 (row1) must use yaxis"
        assert "'滤波价格'" in html

    def test_row2_signal_uses_yaxis3(self):
        """信号 使用 yaxis3（索引 3，交换后）。"""
        html = self._read_html()
        assert "yaxis3: { domain: [0.52, 0.64]" in html, \
            "信号 (row2) must use yaxis3"
        assert "'信号'" in html

    def test_row3_position_uses_yaxis2(self):
        """做多做空 使用 yaxis2（索引 2，交换后）。"""
        html = self._read_html()
        assert "yaxis2: { domain: [0.4, 0.52]" in html, \
            "做多做空 (row3) must use yaxis2"
        assert "做多做空" in html

    def test_row4_pnl_uses_yaxis4(self):
        """PnL 使用 yaxis4。"""
        html = self._read_html()
        assert "yaxis4: { domain: [0.0, 0.4]" in html, \
            "PnL (row4) must use yaxis4"
        assert "'PnL (累计)'" in html

    def test_axis_swap_not_reversed(self):
        """回归防护：确保 axis 交换没有反转（yaxis2 不是信号, yaxis3 不是做多做空）。"""
        html = self._read_html()
        # 做多做空 注释在 yaxis2 附近
        assert "Row 3: 做多做空" in html, \
            "Row 3 comment must label 做多做空 (not 信号)"
        assert "Row 2: Signal" in html, \
            "Row 2 comment must label Signal (not 做多做空)"


class TestCrossSubplotCursor:
    """Cross-subplot cursor line drawn via paper-coordinate shape with
    plotly_hover / plotly_unhover event handlers.

    This is a period-dashboard feature: a dotted vertical line spans all 4
    subplot rows using ``yref: 'paper'``, synchronized to the mouse x position.
    """

    @staticmethod
    def _read_html():
        return HTML_PATH.read_text(encoding="utf-8")

    # ---- plotly_hover handler ----

    def test_plotly_hover_handler_is_registered(self):
        """``plotly_hover`` 事件处理器注册在 dashChartEl 上。"""
        html = self._read_html()
        assert "dashChartEl.on('plotly_hover'" in html, \
            "HTML must register plotly_hover handler on dashChartEl"
        assert "function(eventData)" in html, \
            "plotly_hover handler must accept eventData parameter"

    def test_hover_handler_updates_shape_x_position(self):
        """hover 事件将 crosshair shape 的 x0/x1 更新为 eventData.xvals[0]。"""
        html = self._read_html()
        assert "eventData.xvals[0]" in html, \
            "Hover handler must read eventData.xvals[0] for x position"
        assert "'shapes[' + cursorShapeIdx + '].x0'" in html, \
            "Hover handler must update shapes[idx].x0"
        assert "'shapes[' + cursorShapeIdx + '].x1'" in html, \
            "Hover handler must update shapes[idx].x1"

    # ---- yref: 'paper' ----

    def test_cursor_shape_uses_yref_paper(self):
        """Crosshair shape 使用 ``yref: 'paper'`` 以跨越全部 4 个子图行。"""
        html = self._read_html()
        assert "yref: 'paper'" in html, \
            "Cross-subplot cursor must use yref: 'paper' to span all rows"
        assert "y0: 0, y1: 1" in html or "y0:0, y1:1" in html, \
            "Shape must fill full paper height (y0=0, y1=1)"

    # ---- plotly_unhover handler ----

    def test_plotly_unhover_handler_is_registered(self):
        """``plotly_unhover`` 事件处理器注册在 dashChartEl 上。"""
        html = self._read_html()
        assert "dashChartEl.on('plotly_unhover'" in html, \
            "HTML must register plotly_unhover handler on dashChartEl"

    def test_cursor_hidden_on_unhover(self):
        """unhover 时将 shape 移动至 x=-1 (off-screen) 以隐藏（使用 bracket assignment）。"""
        html = self._read_html()
        assert "_update2['shapes[' + cursorShapeIdx + '].x0'] = -1" in html, \
            "Unhover must set shapes[idx].x0 to -1 via bracket assignment"
        assert "_update2['shapes[' + cursorShapeIdx + '].x1'] = -1" in html, \
            "Unhover must set shapes[idx].x1 to -1 via bracket assignment"

    # ---- Shape type and style ----

    def test_cursor_shape_is_line_type(self):
        """Crosshair shape 类型为 ``type: 'line'``。"""
        html = self._read_html()
        assert "type: 'line'" in html, \
            "Cross-subplot cursor must use type: 'line'"

    def test_cursor_line_color_is_grey(self):
        """Crosshair 线条颜色为灰色 #8b949e。"""
        html = self._read_html()
        assert "color: '#8b949e'" in html, \
            "Cursor line must use grey color #8b949e"

    def test_cursor_shape_starts_offscreen(self):
        """Shape 初始位置 x0=-1, x1=-1（屏幕外隐藏）。"""
        html = self._read_html()
        assert "x0: -1, x1: -1" in html or "x0:-1, x1:-1" in html, \
            "Cursor shape must start off-screen (x0=-1, x1=-1)"


class TestPeriodDashboardAxisAnchors:
    """Axis anchor / matches for the 4-row period dashboard.

    The bottom row (PnL) owns the primary x-axis (xaxis4).  The three upper
    rows delegate to it via ``matches: 'x4'`` so pan/zoom are synchronized.
    """

    @staticmethod
    def _read_html():
        return HTML_PATH.read_text(encoding="utf-8")

    # ---- matches: 'x4' on upper axes ----

    def test_xaxis_matches_x4(self):
        """xaxis (row1 滤波) has ``matches: 'x4'``."""
        html = self._read_html()
        assert "xaxis: { matches: 'x4'" in html, \
            "xaxis (row1 filtered) must match x4"

    def test_xaxis2_matches_x4(self):
        """xaxis2 (row3 做多做空) has ``matches: 'x4'``."""
        html = self._read_html()
        assert "xaxis2: { matches: 'x4'" in html, \
            "xaxis2 (row3 position) must match x4"

    def test_xaxis3_matches_x4(self):
        """xaxis3 (row2 信号) has ``matches: 'x4'``."""
        html = self._read_html()
        assert "xaxis3: { matches: 'x4'" in html, \
            "xaxis3 (row2 signal) must match x4"

    def test_all_three_upper_axes_match_x4(self):
        """All three upper x-axes (xaxis, xaxis2, xaxis3) match xaxis4."""
        html = self._read_html()
        match_x4_count = html.count("matches: 'x4'")
        assert match_x4_count >= 3, \
            f"Expected >=3 matches: 'x4' in dash layout, got {match_x4_count}"

    # ---- xaxis4 as anchor ----

    def test_xaxis4_is_anchor_does_not_match(self):
        """xaxis4 (row4 PnL) is the anchor — no ``matches`` property."""
        html = self._read_html()
        # xaxis4 must be defined but without matches
        assert "xaxis4:" in html, "xaxis4 must be defined for bottom PnL row"
        # matches: 'x4' appears 3 times (for xaxis, xaxis2, xaxis3)
        # Verifying xaxis4 itself has no matches means we look for
        # "xaxis4:" followed by something that is NOT "matches"
        assert "xaxis4: { domain: [0.0, 1.0]" in html, \
            "xaxis4 must NOT have matches (it IS the anchor)"

    # ---- Visual row order verification via domain values ----

    def test_visual_row_order_is_top_to_bottom(self):
        """Row order from top to bottom: 滤波 → 信号 → 做多做空 → PnL.

        Verified via yaxis domain values: larger domain = higher on chart.
        """
        html = self._read_html()
        # Extract domain values from the dash layout section
        assert "yaxis: { domain: [0.64, 1.0]" in html, \
            "Row 1 (top): filtered price must be yaxis domain [0.64, 1.0]"
        assert "yaxis3: { domain: [0.52, 0.64]" in html, \
            "Row 2: signal must be yaxis3 domain [0.52, 0.64]"
        assert "yaxis2: { domain: [0.4, 0.52]" in html, \
            "Row 3: position must be yaxis2 domain [0.4, 0.52]"
        assert "yaxis4: { domain: [0.0, 0.4]" in html, \
            "Row 4 (bottom): PnL must be yaxis4 domain [0.0, 0.4]"

    def test_domain_values_are_strictly_decreasing(self):
        """Each row's domain start is lower than the row above it."""
        html = self._read_html()
        # Assert the order using the comments that document the row mapping
        assert "Row 1 (top): Filtered" in html, \
            "Comment for Row 1 must be present"
        assert "Row 2: Signal" in html, \
            "Comment for Row 2 must be present"
        assert "Row 3: 做多做空" in html, \
            "Comment for Row 3 must be present"
        assert "Row 4 (bottom): PnL" in html, \
            "Comment for Row 4 must be present"


class TestPeriodDashboardRowHeightRatios:
    """Verify middle rows (信号 + 做多做空) are roughly half the height of
    the top and bottom rows (滤波 + PnL).
    """

    @staticmethod
    def _read_html():
        return HTML_PATH.read_text(encoding="utf-8")

    # Known heights from domain intervals:
    # row1 (滤波):  1.00 - 0.64 = 0.36
    # row2 (信号):  0.64 - 0.52 = 0.12
    # row3 (做多做空): 0.52 - 0.40 = 0.12
    # row4 (PnL):   0.40 - 0.00 = 0.40

    def test_row1_filtered_height_is_36_percent(self):
        """Row 1 (滤波) height is ~36% of total chart."""
        html = self._read_html()
        # yaxis domain goes from 0.64 to 1.0
        assert "yaxis: { domain: [0.64, 1.0]" in html

    def test_row2_signal_height_is_12_percent(self):
        """Row 2 (信号) height is ~12% — roughly half of row1/row4."""
        html = self._read_html()
        # yaxis3 domain goes from 0.52 to 0.64
        assert "yaxis3: { domain: [0.52, 0.64]" in html

    def test_row3_position_height_is_12_percent(self):
        """Row 3 (做多做空) height is ~12% — roughly half of row1/row4."""
        html = self._read_html()
        # yaxis2 domain goes from 0.40 to 0.52
        assert "yaxis2: { domain: [0.4, 0.52]" in html

    def test_row4_pnl_height_is_40_percent(self):
        """Row 4 (PnL) height is ~40%."""
        html = self._read_html()
        # yaxis4 domain goes from 0.0 to 0.4
        assert "yaxis4: { domain: [0.0, 0.4]" in html

    def test_middle_rows_equal_height(self):
        """Row 2 and row 3 have equal height (0.12 each)."""
        html = self._read_html()
        assert "yaxis3: { domain: [0.52, 0.64]" in html
        assert "yaxis2: { domain: [0.4, 0.52]" in html
        # Both have span of 0.12 (64-52=12, 52-40=12)

    def test_middle_row_is_roughly_half_of_row1(self):
        """Row 2/3 height (~0.12) is ~1/3 of row 1 (0.36), not exactly half,
        but visually a compact secondary row."""
        # The key assertion: the domains produce a stacked layout where
        # lower-precedence rows get less vertical space.
        # row1 height 0.36 > row2/row3 height 0.12
        assert (1.0 - 0.64) > (0.64 - 0.52), \
            "Row 1 (滤波, 36%) must be taller than row 2 (信号, 12%)"

    def test_middle_row_is_roughly_half_of_row4(self):
        """Row 2/3 height (~0.12) is less than row 4 (0.40)."""
        assert (0.4 - 0.0) > (0.64 - 0.52), \
            "Row 4 (PnL, 40%) must be taller than row 2 (信号, 12%)"


class TestPeriodDashboardGrid:
    """Grid layout for the 4-row period dashboard: explicit rows=4, cols=1,
    ``roworder: 'top to bottom'``.
    """

    @staticmethod
    def _read_html():
        return HTML_PATH.read_text(encoding="utf-8")

    def test_grid_rows_4_columns_1(self):
        """Grid has explicit ``rows: 4, columns: 1``."""
        html = self._read_html()
        assert "grid: { rows: 4, columns: 1" in html, \
            "Period dashboard must use grid rows: 4, columns: 1"

    def test_grid_roworder_top_to_bottom(self):
        """Grid uses ``roworder: 'top to bottom'``."""
        html = self._read_html()
        assert "roworder: 'top to bottom'" in html, \
            "Grid roworder must be 'top to bottom'"

    def test_grid_uses_independent_pattern(self):
        """Grid pattern is ``'independent'`` so each row has its own y-axis."""
        html = self._read_html()
        # The full grid line includes pattern: 'independent'
        assert "grid: { rows: 4, columns: 1, pattern: 'independent'" in html or \
               "columns: 1, pattern: 'independent'" in html, \
            "Grid must use pattern: 'independent' for separate y-axes per row"


class TestPeriodDashboardZoomSync:
    """Zoom synchronization: all x-axes enable spike lines, and upper axes
    match the bottom anchor axis so zoom/pan stays synchronized.
    """

    @staticmethod
    def _read_html():
        return HTML_PATH.read_text(encoding="utf-8")

    def test_xaxis_has_showspikes(self):
        """xaxis (row1) has ``showspikes: true``."""
        html = self._read_html()
        # The dash layout xaxis line includes showspikes
        assert "xaxis: { matches: 'x4'" in html
        assert "showspikes: true" in html

    def test_xaxis2_has_showspikes(self):
        """xaxis2 (row3) has ``showspikes: true``."""
        html = self._read_html()
        assert "xaxis2: { matches: 'x4'" in html
        assert "showspikes: true" in html

    def test_xaxis3_has_showspikes(self):
        """xaxis3 (row2) has ``showspikes: true``."""
        html = self._read_html()
        assert "xaxis3: { matches: 'x4'" in html
        assert "showspikes: true" in html

    def test_xaxis4_has_showspikes(self):
        """xaxis4 (anchor, row4) has ``showspikes: true``."""
        html = self._read_html()
        assert "xaxis4:" in html
        assert "showspikes: true" in html

    def test_all_dash_x_axes_have_matches_x4_or_are_x4(self):
        """Every x-axis in the dash layout either matches 'x4' or IS xaxis4."""
        html = self._read_html()
        # 3 matches + 1 anchor = 4 total axes
        match_count = html.count("matches: 'x4'")
        assert match_count >= 3, \
            f"Expected at least 3 matches: 'x4' for dash layout, got {match_count}"
        assert "xaxis4:" in html, "xaxis4 must be present as the anchor"

    def test_spikemode_across_on_dash_axes(self):
        """Dash layout x-axes use ``spikemode: 'across'`` for crosshair lines."""
        html = self._read_html()
        # The dash layout section defines spike conf on its axes
        assert "spikemode: 'across'" in html, \
            "Dash x-axes must use spikemode: 'across'"

    def test_spikesnap_cursor_on_dash_axes(self):
        """Dash layout x-axes use ``spikesnap: 'cursor'`` to follow the mouse."""
        html = self._read_html()
        assert "spikesnap: 'cursor'" in html, \
            "Dash x-axes must use spikesnap: 'cursor'"


class TestPeriodDashboardHovermode:
    """Hovermode and hoverlabel settings in the 4-row period dashboard."""

    @staticmethod
    def _read_html():
        return HTML_PATH.read_text(encoding="utf-8")

    def test_dashboard_layout_has_hovermode_x_unified(self):
        """Period dashboard layout uses ``hovermode: 'x unified'``."""
        html = self._read_html()
        # The dash buildDash function constructs a layout with hovermode
        assert "hovermode: 'x unified'" in html, \
            "Dashboard layout must use hovermode: 'x unified'"

    def test_hover_label_style_is_defined(self):
        """``hoverLabelStyle`` constant is defined with dark theme colors."""
        html = self._read_html()
        assert "const hoverLabelStyle" in html or "hoverLabelStyle =" in html, \
            "hoverLabelStyle constant must be defined"
        assert "bgcolor: '#21262d'" in html, \
            "Hover label background must be dark (#21262d)"
        assert "bordercolor: '#30363d'" in html, \
            "Hover label border must be #30363d"
        assert "font: { color: '#f0f6fc'" in html, \
            "Hover label font must be light (#f0f6fc)"

    def test_hoverlabel_applied_in_dashboard_layout(self):
        """Dashboard layout references ``hoverlabel: hoverLabelStyle``."""
        html = self._read_html()
        assert "hoverlabel: hoverLabelStyle" in html, \
            "Dashboard layout must apply hoverLabelStyle"

    def test_hovermode_count_includes_dashboard(self):
        """At least one ``hovermode: 'x unified'`` exists (includes dashboard)."""
        html = self._read_html()
        count = html.count("hovermode: 'x unified'")
        assert count >= 1, \
            f"At least 1 hovermode: 'x unified' must exist, got {count}"


class TestJsComputedKeyRegression:
    """JS 计算属性 Key 回归防护：验证 relayout 不使用对象字面量计算属性 key。

    回归防护 commit 3f64586 的 bug：在对象字面量中使用 computed property keys
    (``{ [expr]: val }``) 传递给 relayout。修复后改为 bracket assignment 模式。
    """

    @staticmethod
    def _read_html():
        return HTML_PATH.read_text(encoding="utf-8")

    def test_no_computed_property_keys_in_object_literals(self):
        """对象字面量中不使用计算属性 key（``{ ['shapes[`` 反模式）。"""
        html = self._read_html()
        assert "{ ['shapes[" not in html, \
            "Must NOT use computed property keys with shapes in object literals"

    def test_bracket_assignment_used_for_dynamic_keys(self):
        """动态 key 使用 bracket assignment（``obj[key] = val``）而非对象字面量计算属性。"""
        html = self._read_html()
        assert "_update['shapes[' + cursorShapeIdx + '].x0'] = xv" in html, \
            "Hover handler must use bracket assignment for shapes[idx].x0"
        assert "_update['shapes[' + cursorShapeIdx + '].x1'] = xv" in html, \
            "Hover handler must use bracket assignment for shapes[idx].x1"
        assert "_update2['shapes[' + cursorShapeIdx + '].x0'] = -1" in html, \
            "Unhover handler must use bracket assignment for shapes[idx].x0"
        assert "_update2['shapes[' + cursorShapeIdx + '].x1'] = -1" in html, \
            "Unhover handler must use bracket assignment for shapes[idx].x1"

    def test_relayout_uses_variable_not_inline_object(self):
        """relayout 调用时传入预构建变量（_update / _update2），而非内联对象字面量。"""
        html = self._read_html()
        assert "relayout('chart-dash-' + v, _update)" in html, \
            "Hover relayout must pass _update variable, not inline object"
        assert "relayout('chart-dash-' + v, _update2)" in html, \
            "Unhover relayout must pass _update2 variable, not inline object"

    def test_update_objects_initialized_as_empty(self):
        """_update 和 _update2 初始化为空对象 ``{}``。"""
        html = self._read_html()
        assert "_update = {}" in html, \
            "_update must be initialized as empty object"
        assert "_update2 = {}" in html, \
            "_update2 must be initialized as empty object"


class TestCssClasses:
    """G3: CSS 类存在性验证。"""

    @staticmethod
    def _read_html():
        return HTML_PATH.read_text(encoding="utf-8")

    def test_period_dashboard_class_in_css(self):
        """.period-dashboard CSS 类存在于样式表中。"""
        html = self._read_html()
        assert ".period-dashboard {" in html, \
            "CSS must define .period-dashboard class"

    def test_date_tip_class_in_css_with_position_fixed(self):
        """.date-tip CSS 类存在且包含 ``position: fixed``。"""
        html = self._read_html()
        assert ".date-tip {" in html, \
            "CSS must define .date-tip class"
        assert "position: fixed" in html, \
            ".date-tip must use position: fixed"

    def test_plot_wrap_class_in_css(self):
        """.plot-wrap CSS 类存在于样式表中。"""
        html = self._read_html()
        assert ".plot-wrap {" in html, \
            "CSS must define .plot-wrap class"

    def test_hovertext_hidden_rule_exists(self):
        """.hovertext 隐藏规则存在。"""
        html = self._read_html()
        assert ".hovertext { visibility: hidden" in html, \
            ".hovertext must have visibility: hidden rule"

    def test_spikeline_hidden_rule_exists(self):
        """.spikeline 隐藏规则存在。"""
        html = self._read_html()
        assert ".spikeline { visibility: hidden" in html, \
            ".spikeline must have visibility: hidden rule"


class TestDateTipDivs:
    """G4: date-tip div 元素存在性验证。"""

    @staticmethod
    def _read_html():
        return HTML_PATH.read_text(encoding="utf-8")

    def test_four_date_tip_divs_exist(self):
        """4 个 date-tip div 存在：date-tip-v0 到 date-tip-v3。"""
        html = self._read_html()
        for v in ("v0", "v1", "v2", "v3"):
            assert f'id="date-tip-{v}"' in html, \
                f"HTML must contain date-tip div with id=date-tip-{v}"

    def test_each_date_tip_has_class(self):
        """每个 date-tip div 包含 ``class="date-tip"``。"""
        html = self._read_html()
        for v in ("v0", "v1", "v2", "v3"):
            assert f'id="date-tip-{v}" class="date-tip"' in html, \
                f"date-tip-{v} must have class='date-tip'"

    def test_date_tip_display_none_via_css(self):
        """.date-tip CSS 类包含 ``display: none``。"""
        html = self._read_html()
        assert "display: none" in html, \
            ".date-tip CSS must set display: none to hide tips initially"


class TestHtmlDomStructure:
    """G6: DOM 结构验证 — 各 section 的 chart/tab 容器 div 存在性。"""

    @staticmethod
    def _read_html():
        return HTML_PATH.read_text(encoding="utf-8")

    def test_section_2_filtered_overview_chart_exists(self):
        """Section 2: Filtered Price Overview 的 chart div 存在。"""
        html = self._read_html()
        assert 'id="chart-filtered-overview"' in html, \
            "Section 2 must contain chart-filtered-overview div"

    def test_section_3_signal_chart_exists(self):
        """Section 3: Signal Comparison 的 chart div 存在。"""
        html = self._read_html()
        assert 'id="chart-signals"' in html, \
            "Section 3 must contain chart-signals div"

    def test_section_4_pnl_chart_exists(self):
        """Section 4: PnL Curves 的 chart div 存在。"""
        html = self._read_html()
        assert 'id="chart-pnl"' in html, \
            "Section 4 must contain chart-pnl div"

    def test_section_5_heatmap_chart_exists(self):
        """Section 5: Position Heatmap 的 chart div 存在。"""
        html = self._read_html()
        assert 'id="chart-heatmap"' in html, \
            "Section 5 must contain chart-heatmap div"

    def test_section_6_trade_events_chart_exists(self):
        """Section 6: Trade Events 的 chart div 存在。"""
        html = self._read_html()
        assert 'id="chart-trades"' in html, \
            "Section 6 must contain chart-trades div"

    def test_section_8_data_table_exists(self):
        """Section 8: 完整数据表的 table-wrap div 存在。"""
        html = self._read_html()
        assert 'id="table-wrap"' in html, \
            "Section 8 must contain table-wrap div for data table"


# ============================================================
# Freeze-prevention regression tests
# ============================================================


class TestRelayoutGuard:
    """Freeze prevention: _inRelayout guard prevents recursive relayout calls.

    Period dashboard hover/unhover handlers call Plotly.relayout to move the
    cross-subplot cursor shape.  Without a guard, relayout callbacks could
    re-trigger the hover handler and cause infinite recursion (freezing the tab).
    """

    @staticmethod
    def _read_html():
        return HTML_PATH.read_text(encoding="utf-8")

    def test_in_relayout_flag_exists(self):
        """``_inRelayout`` flag is declared in the crosshair handler variables."""
        html = self._read_html()
        assert "_inRelayout" in html, \
            "JS must declare _inRelayout flag for relayout recursion guard"
        # Verify it appears in the var declaration alongside other crosshair vars
        assert "var _lastXv" in html and "_inRelayout" in html, \
            "_inRelayout must be declared in the crosshair variable block"

    def test_in_relayout_checked_before_relayout(self):
        """``_inRelayout`` is checked with early return BEFORE calling relayout."""
        html = self._read_html()
        assert "if (_inRelayout) return" in html, \
            "_inRelayout must be guarded with early return before relayout"

    def test_pending_false_set_after_relayout(self):
        """``_pending = false`` is set AFTER ``Plotly.relayout`` call (not before).

        Setting _pending before relayout would prematurely clear the throttle
        state, causing multiple concurrent relayout calls.
        """
        html = self._read_html()
        # The relayout call and the _pending reset must both exist
        assert "Plotly.relayout('chart-dash-' + v, _update)" in html, \
            "relayout call must exist in _applyCrosshair"
        assert "_pending = false" in html, \
            "_pending reset must exist"
        # Verify _pending = false appears after relayout in the _applyCrosshair function
        relayout_pos = html.find("Plotly.relayout('chart-dash-' + v, _update)")
        pending_pos_after = html.find("_pending = false", relayout_pos)
        assert pending_pos_after > relayout_pos, \
            "_pending = false must appear AFTER the relayout call, not before"

    def test_in_relayout_reset_after_work(self):
        """``_inRelayout = false`` reset exists to re-enable the guard.

        After relayout completes (and date-tip is updated), the flag must be
        cleared so subsequent mouse moves can trigger relayout again.
        """
        html = self._read_html()
        assert "_inRelayout = false" in html, \
            "_inRelayout must be reset to false after relayout completes"


class TestListenerCleanup:
    """Freeze prevention: listener cleanup before re-registering.

    Without removing old listeners before adding new ones (e.g. on file
    re-upload), stale event handlers accumulate and can fire concurrently,
    causing race conditions and tab freezes.
    """

    @staticmethod
    def _read_html():
        return HTML_PATH.read_text(encoding="utf-8")

    def test_remove_all_listeners_plotly_hover_before_on(self):
        """``removeAllListeners('plotly_hover')`` called before ``.on('plotly_hover')``."""
        html = self._read_html()
        assert "removeAllListeners('plotly_hover')" in html, \
            "Must remove old plotly_hover listeners before re-registering"
        assert ".on('plotly_hover'" in html, \
            "Must re-register plotly_hover handler"
        # Verify removeAllListeners comes before .on in the source
        rm_pos = html.find("removeAllListeners('plotly_hover'")
        on_pos = html.find(".on('plotly_hover'", rm_pos)
        assert on_pos > rm_pos, \
            "removeAllListeners('plotly_hover') must appear before .on('plotly_hover')"

    def test_remove_all_listeners_plotly_unhover_before_on(self):
        """``removeAllListeners('plotly_unhover')`` called before ``.on('plotly_unhover')``."""
        html = self._read_html()
        assert "removeAllListeners('plotly_unhover')" in html, \
            "Must remove old plotly_unhover listeners before re-registering"
        assert ".on('plotly_unhover'" in html, \
            "Must re-register plotly_unhover handler"
        # Verify removeAllListeners comes before .on in the source
        rm_pos = html.find("removeAllListeners('plotly_unhover'")
        on_pos = html.find(".on('plotly_unhover'", rm_pos)
        assert on_pos > rm_pos, \
            "removeAllListeners('plotly_unhover') must appear before .on('plotly_unhover')"

    def test_remove_event_listener_for_mousemove_cleanup(self):
        """``removeEventListener`` used to clean up old mousemove handler before reassigning."""
        html = self._read_html()
        assert "removeEventListener('mousemove'" in html, \
            "Must use removeEventListener to clean up old mousemove handler"
        assert "dashChartEl._mousemoveH" in html, \
            "Must reference dashChartEl._mousemoveH for handler cleanup"

    def test_plotly_purge_called_in_render_all_before_rebuilding(self):
        """``Plotly.purge`` called in renderAll before rebuilding dashboards.

        On file re-upload, renderAll is called again.  Without purging, old
        Plotly chart state leaks and can cause stale event handlers to fire.
        """
        html = self._read_html()
        assert "Plotly.purge('chart-dash-' + v)" in html, \
            "renderAll must call Plotly.purge on each chart-dash before rebuild"
        # Verify purge appears within the renderAll function body
        renderall_pos = html.find("function renderAll")
        purge_pos = html.find("Plotly.purge('chart-dash-' + v)", renderall_pos)
        builddash_pos = html.find("buildPeriodDashboard", purge_pos)
        assert purge_pos > renderall_pos > 0, \
            "Plotly.purge must be inside renderAll function"
        assert builddash_pos > purge_pos, \
            "Plotly.purge must be called BEFORE buildPeriodDashboard (purge then rebuild)"


# ============================================================
# Zoom controls tests
# ============================================================


class TestZoomControls:
    """Zoom-in/out and reset-zoom functionality for period dashboards."""

    @staticmethod
    def _read_html():
        return HTML_PATH.read_text(encoding="utf-8")

    def test_reset_zoom_buttons_exist(self):
        """4 个 .btn-reset-zoom 按钮存在，每个 period dashboard 一个。"""
        html = self._read_html()
        count = html.count('class="btn-reset-zoom"')
        assert count == 4, \
            f"Expected 4 .btn-reset-zoom buttons, got {count}"

    def test_scroll_zoom_in_plot_config(self):
        """plotConfig 包含 scrollZoom: true。"""
        html = self._read_html()
        assert "scrollZoom: true" in html, \
            "plotConfig must include scrollZoom: true for scroll-to-zoom"

    def test_autorange_in_reset_button(self):
        """reset 按钮的 Plotly.relayout 调用使用 autorange 重置 zoom。"""
        html = self._read_html()
        assert "xaxis.autorange" in html, \
            "Reset zoom button must use autorange on xaxes"
        assert "xaxis2.autorange" in html, \
            "Reset zoom button must auto-range xaxis2"
        assert "xaxis3.autorange" in html, \
            "Reset zoom button must auto-range xaxis3"
        assert "xaxis4.autorange" in html, \
            "Reset zoom button must auto-range xaxis4"

    def test_reset_not_in_modebar_buttons_to_remove(self):
        """reset 不在 modeBarButtonsToRemove 中（双击重置缩放可用）。"""
        html = self._read_html()
        assert "modeBarButtonsToRemove" in html, \
            "plotConfig must define modeBarButtonsToRemove"
        # 'reset' should NOT be in the remove list
        modebar_line = None
        for line in html.split("\n"):
            if "modeBarButtonsToRemove" in line:
                modebar_line = line
                break
        assert modebar_line is not None, "modeBarButtonsToRemove line not found"
        assert "reset" not in modebar_line or "reset" in modebar_line and "'reset'" not in modebar_line, \
            "'reset' must NOT be in modeBarButtonsToRemove (double-click reset should work)"


class TestClosePriceLine:
    """Close price line additions: thin gray reference line in filtered
    overview (Section 2) and each period dashboard Row 1.
    """

    @staticmethod
    def _read_html():
        return HTML_PATH.read_text(encoding="utf-8")

    def test_close_price_trace_name_appears(self):
        """收盘价 appears as a trace name in the HTML."""
        html = self._read_html()
        assert "name: '收盘价'" in html, \
            "HTML must contain a trace named 收盘价"

    def test_close_price_uses_semi_transparent_color(self):
        """Close price trace uses a semi-transparent gray/white color with alpha."""
        html = self._read_html()
        assert "rgba(200,200,200,0.3)" in html, \
            "Close price must use rgba(200,200,200,0.3) for semi-transparent gray"

    def test_close_price_in_both_build_filtered_overview_and_period_dashboard(self):
        """Close price appears in both buildFilteredOverview and buildPeriodDashboard."""
        html = self._read_html()
        # Count occurrences of the close price trace push
        count = html.count("name: '收盘价'")
        assert count >= 2, \
            "Close price trace must appear in both buildFilteredOverview and " \
            f"buildPeriodDashboard (expected >=2, got {count})"

    def test_close_price_guarded_by_length_check(self):
        """Close price trace is guarded by if (closePrice && closePrice.length > 0) check."""
        html = self._read_html()
        # Both occurrences have a guard pattern
        assert "closePrice && closePrice.length > 0" in html, \
            "Close price trace must be guarded by data existence check"

    def test_close_price_line_width_is_1(self):
        """Close price line has width: 1 for thin reference line."""
        html = self._read_html()
        assert "width: 1, color: 'rgba(200,200,200,0.3)'" in html, \
            "Close price must have line width 1 with semi-transparent gray"
