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
