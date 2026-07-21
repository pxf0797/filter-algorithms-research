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
        """每个 period-dashboard 包含正确的 chart div ID（filtered/signal/pnl/heatmap × 4 views）。"""
        html = self._read_html()
        for v in ("v0", "v1", "v2", "v3"):
            for chart_type in ("chart-filtered", "chart-signal", "chart-pnl", "chart-heatmap"):
                chart_id = f'{chart_type}-{v}'
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
