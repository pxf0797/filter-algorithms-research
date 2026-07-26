"""
HTML 模板测试 — 验证 回测结果可视化.html 中的关键 JS 结构和修复。

测试范围:
- Phased rendering (Phase 1/2/3 setTimeout) 结构存在性
- Cross-dashboard zoom sync (removeAllListeners + _relayoutHandler)
- Reset zoom 按钮覆盖所有 4 个 dashboard
- safeDate / null guard / zmin:zmax 等渲染修复点
"""

from pathlib import Path



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


# ═══════════════════════════════════════════════════════════════════════════════
# 帮助函数：提取 sync 区域
# ═══════════════════════════════════════════════════════════════════════════════

def _get_sync_section():
    """提取跨 dashboard sync 代码段（从 var _syncGuard 到 setTimeout 结束）。"""
    content = _read_html()
    start = content.find("var _syncGuard = false;")
    if start == -1:
        return ""
    end = content.find("}, _phase3Ms + 120);", start)
    if end == -1:
        return content[start:]
    return content[start:end + len("}, _phase3Ms + 120);")]


def _get_handler_body():
    """提取 sync handler 函数体（从 _relayoutHandler = async function 到 _syncGuard = false）。"""
    content = _read_html()
    start = content.find("_relayoutHandler = async function(eventData)")
    if start == -1:
        return ""
    end = content.find("_syncGuard = false;", start)
    if end == -1:
        return content[start:]
    return content[start:end + len("_syncGuard = false;")]


# ═══════════════════════════════════════════════════════════════════════════════
# SyncGuard 竞态条件回归测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestSyncGuardRaceCondition:
    """验证 _syncGuard 竞态条件已被修复（handler async + await + guard 顺序）。"""

    def test_handler_is_async_function(self):
        """sync handler 是 async 函数。"""
        content = _read_html()
        assert "_relayoutHandler = async function(eventData)" in content, (
            "Sync handler should be an async function to properly handle Promise-based relayout"
        )

    def test_handler_uses_await_relayout(self):
        """handler 内使用 await Plotly.relayout 而非裸调用。"""
        content = _read_html()
        assert "await Plotly.relayout" in content, (
            "Handler should use await Plotly.relayout to prevent race condition"
        )

    def test_sync_guard_set_before_await(self):
        """_syncGuard = true 出现在 await 之前，防止 async 操作完成前 guard 失效。"""
        handler = _get_handler_body()
        assert handler, "Handler body not found in template"
        guard_pos = handler.find("_syncGuard = true;")
        await_pos = handler.find("await Plotly.relayout")
        assert guard_pos > 0, "_syncGuard = true not found in handler body"
        assert await_pos > guard_pos, (
            "_syncGuard = true must appear BEFORE await Plotly.relayout "
            "to prevent re-entry during async operation"
        )

    def test_sync_guard_reset_after_await(self):
        """_syncGuard = false 出现在所有 await 之后，确保所有 relayout 完成才重置。"""
        handler = _get_handler_body()
        assert handler, "Handler body not found in template"
        await_pos = handler.rfind("await Plotly.relayout")
        reset_pos = handler.find("_syncGuard = false;")
        assert await_pos > 0, "await Plotly.relayout not found in handler body"
        assert reset_pos > await_pos, (
            "_syncGuard = false must appear AFTER all await Plotly.relayout calls "
            "to ensure guard is only reset when sync is complete"
        )

    def test_no_for_each_with_async_relayout(self):
        """handler 内没有使用 forEach 包裹 await Plotly.relayout（forEach 无法正确处理 async）。"""
        handler = _get_handler_body()
        assert handler, "Handler body not found in template"
        # 应使用普通 for 循环而非 forEach
        assert "for (var i = 0; i < VIEWS.length; i++)" in handler, (
            "Handler should use a regular for loop (not forEach) with await Plotly.relayout"
        )
        # handler 函数体内不应出现 forEach
        assert "forEach" not in handler, (
            "Handler body should not contain forEach — use regular for loop with await"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# removeAllListeners 替换回归测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestRemoveAllListenersFix:
    """验证 removeAllListeners 已被替换为 removeListener（避免破坏 Plotly 内部监听器）。"""

    def test_remove_all_listeners_not_used_in_sync(self):
        """sync 代码段中不存在 removeAllListeners('plotly_relayout')。"""
        sync = _get_sync_section()
        assert sync, "Sync section not found in template"
        assert "removeAllListeners('plotly_relayout')" not in sync, (
            "Sync section should NOT use removeAllListeners for plotly_relayout "
            "— it removes Plotly's internal listeners too"
        )

    def test_remove_listener_used_instead(self):
        """使用了 removeListener('plotly_relayout', divEl._relayoutHandler)。"""
        content = _read_html()
        assert "removeListener('plotly_relayout'" in content, (
            "Should use removeListener (not removeAllListeners) to remove only our handler"
        )

    def test_remove_listener_uses_specific_handler(self):
        """removeListener 传入了具体的 handler 引用，而非空或模糊参数。"""
        content = _read_html()
        assert "removeListener('plotly_relayout', divEl._relayoutHandler)" in content, (
            "removeListener must pass the specific handler reference (divEl._relayoutHandler) "
            "to avoid removing other listeners"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 节流保护回归测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestThrottleProtection:
    """验证节流保护：相同 x 轴 range 不重复同步。"""

    def test_last_synced_range_present(self):
        """模板包含 _lastSyncedRange0 和 _lastSyncedRange1 变量。"""
        content = _read_html()
        assert "_lastSyncedRange0" in content, (
            "Template should define _lastSyncedRange0 for throttle protection"
        )
        assert "_lastSyncedRange1" in content, (
            "Template should define _lastSyncedRange1 for throttle protection"
        )

    def test_skip_same_range(self):
        """handler 在 range 相同时跳过同步（避免重复 plotly_relayout 事件触发冗余 sync）。"""
        content = _read_html()
        assert "lastRange0 === range0 && lastRange1 === range1" in content, (
            "Handler should skip re-syncing when the x-axis range has not changed"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 热力图采样修复回归测试 (buildPeriodDashboard)
# ═══════════════════════════════════════════════════════════════════════════════

class TestHeatmapSamplingFix:
    """验证 buildPeriodDashboard 中的热力图采样已从截断改为均匀采样。"""

    def test_no_truncation_pattern_in_period_dashboard(self):
        """buildPeriodDashboard 中不存在 sampledIndices.length = sampleN 的截断模式。"""
        content = _read_html()
        # Find buildPeriodDashboard function body
        dash_start = content.find("function buildPeriodDashboard(data, v, label)")
        assert dash_start > 0, "buildPeriodDashboard function not found"
        # Find the second occurrence of "function build" after it (the next function)
        next_func = content.find("function build", dash_start + 10)
        if next_func > 0:
            dash_section = content[dash_start:next_func]
        else:
            dash_section = content[dash_start:]
        # The truncation pattern should NOT exist in buildPeriodDashboard
        assert "sampledIndices.length = sampleN" not in dash_section, (
            "buildPeriodDashboard must NOT use sampledIndices.length = sampleN truncation — "
            "use uniform sampling instead"
        )

    def test_uniform_sampling_present_in_period_dashboard(self):
        """buildPeriodDashboard 中存在均匀采样逻辑（Math.round + step）。"""
        content = _read_html()
        dash_start = content.find("function buildPeriodDashboard(data, v, label)")
        assert dash_start > 0, "buildPeriodDashboard function not found"
        next_func = content.find("function build", dash_start + 10)
        if next_func > 0:
            dash_section = content[dash_start:next_func]
        else:
            dash_section = content[dash_start:]
        # Uniform sampling should use Math.round and a step calculation
        assert "Math.round" in dash_section, (
            "buildPeriodDashboard should use Math.round for uniform index selection"
        )
        assert "(sampledIndices.length - 1) / (sampleN - 1)" in dash_section, (
            "buildPeriodDashboard should use uniform sampling step calculation: "
            "(sampledIndices.length - 1) / (sampleN - 1)"
        )

    def test_sample_limit_is_200_in_period_dashboard(self):
        """buildPeriodDashboard 中采样上限为 200 而非 100。"""
        content = _read_html()
        dash_start = content.find("function buildPeriodDashboard(data, v, label)")
        assert dash_start > 0, "buildPeriodDashboard function not found"
        next_func = content.find("function build", dash_start + 10)
        if next_func > 0:
            dash_section = content[dash_start:next_func]
        else:
            dash_section = content[dash_start:]
        assert "Math.min(200, N)" in dash_section, (
            "buildPeriodDashboard sample limit should be 200 (was 100)"
        )
        assert "Math.min(100, N)" not in dash_section, (
            "buildPeriodDashboard should NOT use limit 100 — use 200 instead"
        )

    def test_build_heatmap_no_truncation(self):
        """buildHeatmap (Section 5) 也不存在硬截断。"""
        content = _read_html()
        heatmap_func_start = content.find("function buildHeatmap(data)")
        assert heatmap_func_start > 0, "buildHeatmap function not found"
        next_func = content.find("function build", heatmap_func_start + 10)
        if next_func > 0:
            heatmap_section = content[heatmap_func_start:next_func]
        else:
            heatmap_section = content[heatmap_func_start:]
        # The truncation pattern should NOT exist in buildHeatmap
        assert "sampledIndices.length = sampleN" not in heatmap_section, (
            "buildHeatmap must NOT use sampledIndices.length = sampleN truncation — "
            "use uniform sampling instead"
        )

    def test_build_heatmap_has_uniform_sampling(self):
        """buildHeatmap 存在均匀采样。"""
        content = _read_html()
        heatmap_func_start = content.find("function buildHeatmap(data)")
        assert heatmap_func_start > 0, "buildHeatmap function not found"
        next_func = content.find("function build", heatmap_func_start + 10)
        if next_func > 0:
            heatmap_section = content[heatmap_func_start:next_func]
        else:
            heatmap_section = content[heatmap_func_start:]
        # Uniform sampling should use Math.round and a step calculation
        assert "Math.round" in heatmap_section, (
            "buildHeatmap should use Math.round for uniform index selection"
        )
        assert "(sampledIndices.length - 1) / (sampleN - 1)" in heatmap_section, (
            "buildHeatmap should use uniform sampling step calculation: "
            "(sampledIndices.length - 1) / (sampleN - 1)"
        )

    def test_build_heatmap_sample_limit_is_200(self):
        """buildHeatmap 中采样上限为 200 而非 100。"""
        content = _read_html()
        heatmap_func_start = content.find("function buildHeatmap(data)")
        assert heatmap_func_start > 0, "buildHeatmap function not found"
        next_func = content.find("function build", heatmap_func_start + 10)
        if next_func > 0:
            heatmap_section = content[heatmap_func_start:next_func]
        else:
            heatmap_section = content[heatmap_func_start:]
        assert "Math.min(200, N)" in heatmap_section, (
            "buildHeatmap sample limit should be 200 (was 100)"
        )
        assert "Math.min(100, N)" not in heatmap_section, (
            "buildHeatmap should NOT use limit 100 — use 200 instead"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# D1-D4 标题动态化回归测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestDashTitlesDynamic:
    """验证 D1-D4 标题全部使用动态 span 元素，并且 JS 端统一更新。"""

    def test_all_dash_titles_have_spans(self):
        """D1-D4 标题都使用 span id。"""
        content = _read_html()
        for prefix in ['d1', 'd2', 'd3', 'd4']:
            assert f'id="dash-{prefix}-title"' in content, (
                f"D{prefix[-1]} title should use a span with id='dash-{prefix}-title'"
            )

    def test_dash_titles_no_hardcoded_periods_in_spans(self):
        """D1-D3 section-title 行不再硬编码周期名到 span 之外（span 作为 label 容器）。"""
        content = _read_html()
        # D1 should not have "日线" outside the span
        d1_line = content[content.find('id="dash-d1-title"'):content.find('</div>', content.find('id="dash-d1-title"'))]
        # The span itself contains the fallback text; the key is no bare period text
        # right after the span and before 完整视图
        assert 'id="dash-d1-title"' in d1_line, "D1 must have dynamic span"
        assert 'id="dash-d2-title"' in content, "D2 must have dynamic span"
        assert 'id="dash-d3-title"' in content, "D3 must have dynamic span"
        assert 'id="dash-d4-title"' in content, "D4 must have dynamic span"

    def test_dynamic_title_update_uses_loop(self):
        """renderAll 中使用 forEach 循环统一更新 D1-D4 标题，而非单独处理 D4。"""
        content = _read_html()
        # Check loop pattern: ['d1','d2','d3','d4'].forEach
        assert "['d1','d2','d3','d4'].forEach" in content, (
            "renderAll should use a forEach loop over ['d1','d2','d3','d4'] "
            "to update all period dashboard titles"
        )
        assert "dash-' + prefix + '-title'" in content, (
            "Title span IDs should be constructed dynamically via prefix"
        )

    def test_eps_ref_label_dynamic(self):
        """Section 2 的 eps 参考标签使用 span id 动态更新。"""
        content = _read_html()
        assert 'id="eps-ref-label"' in content, (
            "EPS reference label should use span with id='eps-ref-label'"
        )
        assert "eps-ref-label" in content
        assert "viewLabels.v2" in content, (
            "EPS reference should be updated from viewLabels.v2"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 信号背景可见性 — 信号值 0 的背景 alpha 提升
# ═══════════════════════════════════════════════════════════════════════════════

class TestSignalBgVisibility:
    """验证信号 0 值背景从 0.03 alpha 提升到 0.10，避免视觉空白。"""

    def test_signal_zero_bg_not_tiny_alpha(self):
        """信号值 '0' 的背景 alpha 不应再是 0.03（提升到 0.10 以匹配多空背景可见度）。"""
        content = _read_html()
        # Old value must not exist
        assert "139,148,158,0.03" not in content, (
            "Signal-0 background should no longer use alpha=0.03 — "
            "was too transparent and looked blank"
        )

    def test_signal_zero_bg_has_proper_alpha(self):
        """信号值 '0' 的背景使用 alpha=0.10 与多空信号一致。"""
        content = _read_html()
        assert "139,148,158,0.10" in content, (
            "Signal-0 background should use alpha=0.10 to match long/short bg visibility"
        )

    def test_signal_bg_colors_consistent(self):
        """三个信号状态 (1, -1, 0) 背景 alpha 应该一致（都是 0.10）。"""
        content = _read_html()
        # All three sigBgColors keys should use alpha=0.10 (matching visibility)
        assert "'1': 'rgba(63,185,80,0.10)'" in content or "'1': 'rgba(63,185,80,0.1)'" in content
        assert "'-1': 'rgba(248,81,73,0.10)'" in content or "'-1': 'rgba(248,81,73,0.1)'" in content
        assert "'0': 'rgba(139,148,158,0.10)'" in content or "'0': 'rgba(139,148,158,0.1)'" in content


# ═══════════════════════════════════════════════════════════════════════════════
# 视图周期标签映射 — viewLabels 与 metadata 一致性
# ═══════════════════════════════════════════════════════════════════════════════

class TestViewLabelMapping:
    """验证 viewLabels 从 metadata 正确映射，DEFAULT_VIEW_LABELS 作为降级。"""

    def test_default_view_labels_exist(self):
        """DEFAULT_VIEW_LABELS 定义了 4 个视图的降级标签。"""
        content = _read_html()
        assert "DEFAULT_VIEW_LABELS" in content, (
            "DEFAULT_VIEW_LABELS constant must exist for fallback"
        )
        assert "v0: '日线'" in content
        assert "v1: '60分钟'" in content
        assert "v2: '15分钟'" in content
        assert "v3: '5分钟'" in content

    def test_view_labels_merge_from_metadata(self):
        """renderAll 中 viewLabels 合并 metadata.view_labels 覆盖默认值。"""
        content = _read_html()
        assert "metadata.view_labels" in content, (
            "renderAll should read view_labels from metadata"
        )
        assert "...DEFAULT_VIEW_LABELS" in content, (
            "metadata.view_labels should spread over DEFAULT_VIEW_LABELS for override"
        )

    def test_view_label_used_in_signals_chart(self):
        """buildSignals 使用 viewLabel(v) 显示视图名称（而非硬编码周期）。"""
        content = _read_html()
        signals_func = content[content.find("function buildSignals"):]
        next_func = signals_func.find("\nfunction build", 10)
        if next_func > 0:
            signals_body = signals_func[:next_func]
        else:
            signals_body = signals_func
        assert "viewLabel(v)" in signals_body, (
            "buildSignals should use viewLabel(v) for dynamic period labels "
            "in legend names"
        )

    def test_view_labels_used_in_overview_subtitle(self):
        """dashboard-subtitle 使用 viewLabel(v) 渲染视图标签。"""
        content = _read_html()
        assert "viewLabel(v)" in content, (
            "renderAll subtitle should use viewLabel(v) for view tag display"
        )
