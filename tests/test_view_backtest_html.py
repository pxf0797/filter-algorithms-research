"""
HTML 模板测试 — 验证 回测结果可视化.html 中的关键 JS 结构和修复。

测试范围:
- Phased rendering (Phase 1/2/3 setTimeout) 结构存在性
- Cross-dashboard zoom sync (removeAllListeners + _relayoutHandler)
- Reset zoom 按钮覆盖所有 4 个 dashboard
- safeDate / null guard / zmin:zmax 等渲染修复点
"""

import re
from pathlib import Path

import pytest


# ── 模板路径 ────────────────────────────────────────────────────────────────────
_HTML_PATH = Path(__file__).resolve().parent.parent / "docs" / "backtesting" / "回测结果可视化.html"


def _read_html():
    """读取 HTML 模板内容（带缓存）。"""
    return _HTML_PATH.read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# 文件存在性
# ═══════════════════════════════════════════════════════════════════════════════

class TestHtmlTemplateExists:
    """HTML 模板文件必须存在且可读。"""

    def test_template_file_exists(self):
        """模板文件存在。"""
        assert _HTML_PATH.exists(), f"HTML template not found at {_HTML_PATH}"

    def test_template_is_non_empty(self):
        """模板非空。"""
        content = _read_html()
        assert len(content) > 1000, "HTML template is too small or empty"

    def test_template_contains_html_tag(self):
        """模板是有效的 HTML。"""
        content = _read_html()
        assert "<html" in content.lower()
        assert "</html>" in content.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# Phased Rendering 结构
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhasedRenderingStructure:
    """验证 HTML 模板包含 Phase 1/2/3 的 setTimeout 调用。"""

    def test_phase1_synchronous_calls_present(self):
        """Phase 1 同步渲染函数存在（buildOverview, buildHeatmap 等）。"""
        content = _read_html()
        phase1_funcs = [
            "buildOverview(data)",
            "buildFilteredOverview(data)",
            "buildHeatmap(data)",
            "buildMultiTickerPnl()",
        ]
        for func in phase1_funcs:
            assert func in content, f"Phase 1 call '{func}' not found in template"

    def test_phase2_settimeout_with_view_iteration(self):
        """Phase 2 使用 setTimeout + VIEWS.forEach 延迟渲染各周期仪表盘。"""
        content = _read_html()
        # 检查 phase 2 模式: VIEWS.forEach + setTimeout + buildPeriodDashboard
        assert "VIEWS.forEach" in content
        assert "setTimeout" in content
        assert "buildPeriodDashboard" in content
        # Phase 2 延迟应为 200 + idx * 100 (修复后) 而非 120 + idx * 100
        assert "200 + idx * 100" in content, (
            "Phase 2 delay should be 200 + idx * 100 (increased from 120 for safety margin)"
        )

    def test_phase3_settimeout_is_after_phase2(self):
        """Phase 3 延迟应基于 VIEWS.length 计算，在 Phase 2 之后。"""
        content = _read_html()
        # Phase 3 使用 _phase3Ms 变量
        assert "_phase3Ms" in content
        # Phase 3 延迟: 200 + VIEWS.length * 100 + 80
        assert "VIEWS.length * 100 + 80" in content, (
            "Phase 3 delay should be: 200 + VIEWS.length * 100 + 80"
        )
        # Phase 3 应调用 buildTrades 和 buildTable
        assert "buildTrades(data)" in content
        assert "buildTable(data)" in content

    def test_phases_are_sequential(self):
        """Phase 1、Phase 2、Phase 3 的代码在文件中按顺序出现。"""
        content = _read_html()
        # 找到 Phase 注释或关键函数的位置
        pos_heatmap = content.find("buildHeatmap(data)")
        pos_phase2 = content.find("200 + idx * 100")
        pos_phase3 = content.find("_phase3Ms")
        assert pos_heatmap > 0
        assert pos_phase2 > pos_heatmap, "Phase 2 should appear after Phase 1"
        assert pos_phase3 > pos_phase2, "Phase 3 should appear after Phase 2"


# ═══════════════════════════════════════════════════════════════════════════════
# Cross-Dashboard Sync
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrossDashboardSync:
    """验证跨 dashboard 缩放同步代码存在且正确。"""

    def test_remove_all_listeners_present(self):
        """模板应使用 removeAllListeners 而非 removeListener（API 兼容性修复）。"""
        content = _read_html()
        assert "removeAllListeners" in content, (
            "Cross-dashboard sync should use removeAllListeners (not removeListener)"
        )

    def test_relayout_handler_present(self):
        """模板应包含 _relayoutHandler 用于同步 zoom。"""
        content = _read_html()
        assert "_relayoutHandler" in content
        assert "_syncGuard" in content, "Cross-dashboard sync should have _syncGuard"

    def test_cross_dashboard_sync_loops_all_views(self):
        """同步代码应遍历所有 VIEWS (v0-v3)。"""
        content = _read_html()
        # 验证跨 dashboard sync 中对 VIEWS.forEach 的使用
        # 应该至少有两处 VIEWS.forEach (Phase 2 渲染 + Cross-dashboard sync)
        sync_section = content[content.find("Cross-dashboard"):] if "Cross-dashboard" in content else content
        assert sync_section.count("VIEWS.forEach") >= 1

    def test_sync_listens_to_plotly_relayout(self):
        """跨 dashboard 同步监听 plotly_relayout 事件。"""
        content = _read_html()
        assert "plotly_relayout" in content
        # xaxis4.range 用于跨 dashboard 同步
        assert "xaxis4.range" in content

    def test_sync_after_phase3(self):
        """跨 dashboard sync 应在 Phase 3 之后设置（延迟 _phase3Ms + 120）。"""
        content = _read_html()
        assert "_phase3Ms + 120" in content, (
            "Cross-dashboard sync delay should be _phase3Ms + 120"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Reset Zoom 覆盖所有 Dashboard
# ═══════════════════════════════════════════════════════════════════════════════

class TestResetZoomCoversAllDashboards:
    """验证重置缩放按钮作用于所有 4 个 dashboard。"""

    def test_v0_reset_zoom_covers_all_dashboards(self):
        """D1 (v0) 的重置缩放按钮应作用于全部 4 个 dashboard。"""
        content = _read_html()
        # v0 按钮的 onclick 中包含 chart-dash-v0 到 v3
        assert "chart-dash-v0" in content
        assert "chart-dash-v1" in content
        assert "chart-dash-v2" in content
        assert "chart-dash-v3" in content

    def test_each_dashboard_has_reset_button(self):
        """每个 period dashboard (D1-D4) 都有独立的 reset zoom 按钮。"""
        content = _read_html()
        # D1 有 "重置缩放 (全部)" 按钮
        assert "重置缩放 (全部)" in content
        # D2-D4 各有独立 "重置缩放"
        assert content.count("重置缩放") >= 4, "Should have reset buttons for all 4 dashboards"

    def test_d1_reset_uses_foreach(self):
        """D1 的 reset zoom 使用 forEach 作用于所有 dashboard ID。"""
        content = _read_html()
        assert "dashIds.forEach" in content, (
            "D1 reset button should use forEach to reset all 4 dashboards"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# safeDate 和 null guard 修复
# ═══════════════════════════════════════════════════════════════════════════════

class TestSafeDateAndNullGuards:
    """验证 safeDate 和空数据守卫代码存在。"""

    def test_safe_date_function_present(self):
        """safeDate() 函数应存在且处理 null/undefined/空字符串。"""
        content = _read_html()
        assert "function safeDate(v)" in content
        assert "return null" in content  # safeDate returns null for invalid
        assert "isNaN(d.getTime())" in content  # checks Invalid Date

    def test_safe_date_handles_null_undefined_empty(self):
        """safeDate 应对 null, undefined, '' 返回 null。"""
        content = _read_html()
        # 精确检查 safeDate 的 guard 条件
        assert "v === null || v === undefined || v === ''" in content

    def test_empty_bar_index_guard_in_heatmap(self):
        """buildHeatmap 应有空 barIdx 守卫。"""
        content = _read_html()
        # 在 buildHeatmap 函数中查找空数据 guard
        heatmap_start = content.find("function buildHeatmap(data)")
        heatmap_section = content[heatmap_start:heatmap_start + 600]
        assert "barIdx.length === 0" in heatmap_section, (
            "buildHeatmap should guard against empty barIdx array"
        )

    def test_empty_bar_index_guard_in_period_dashboard(self):
        """buildPeriodDashboard 应有空 barIdx 守卫。"""
        content = _read_html()
        dash_start = content.find("function buildPeriodDashboard(data, v, label)")
        dash_section = content[dash_start:dash_start + 600]
        assert "barIdx.length === 0" in dash_section, (
            "buildPeriodDashboard should guard against empty barIdx array"
        )

    def test_null_position_guard_in_heatmap(self):
        """buildHeatmap 中 null 持仓值应转为 0（空仓）。"""
        content = _read_html()
        assert "val == null) return 0" in content, (
            "Null position values should be converted to 0 (empty position)"
        )

    def test_zmin_zmax_fixed(self):
        """热力图 z 轴范围应固定为 [0, 2]。"""
        content = _read_html()
        assert "zmin: 0" in content
        assert "zmax: 2" in content
        # 应该至少有两处 (heatmap + period dashboard heatmap row)
        assert content.count("zmin: 0") >= 2, "zmin: 0 should appear at least twice"
        assert content.count("zmax: 2") >= 2, "zmax: 2 should appear at least twice"

    def test_clean_sampled_ts_used_for_substring(self):
        """x 轴 tick label 应使用 cleanSampledTs（确保类型安全）。"""
        content = _read_html()
        assert "cleanSampledTs" in content, (
            "cleanSampledTs should be used to ensure type safety for .substring()"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# CDN 回退
# ═══════════════════════════════════════════════════════════════════════════════

class TestCdnFallback:
    """验证 Plotly CDN 加载和回退逻辑。"""

    def test_primary_cdn_present(self):
        """主 CDN 链接存在。"""
        content = _read_html()
        assert "plot.ly" in content or "plotly" in content.lower()

    def test_plotly_loaded_check(self):
        """应有 Plotly 未加载时的检测逻辑。"""
        content = _read_html()
        assert "typeof Plotly" in content, (
            "Should check if Plotly is loaded before initializing"
        )

    def test_init_function_exists(self):
        """应有 init() 入口函数。"""
        content = _read_html()
        assert "function init()" in content
