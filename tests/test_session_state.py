"""
AppState _imp_ 清理 + Streamlit 缓存 TTL — 单元测试
"""
import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — 构造带 _imp_ 备份的 session_state
# ---------------------------------------------------------------------------

def _make_ss_with_imp(extras=None):
    """构造 dict-like session_state，内含预设的 _imp_ 备份。"""
    ss = {
        # 有效的 view key main + _imp_
        "v0_ke": 0.15,
        "_imp_v0_ke": 0.15,
        "v0_tf": "日线",
        "_imp_v0_tf": "日线",
        "v3_sm": 0.05,
        "_imp_v3_sm": 0.05,
        "v1_exp_all": True,
        "_imp_v1_exp_all": True,
        # 孤立的 view _imp_ — N 在有效范围但 suffix 无效
        "_imp_v0_oldparam": 123,
        # 孤立的 view _imp_ — N 超出有效范围
        "_imp_v5_ke": 0.2,
        "_imp_v99_tf": "周线",
        # 非 view-pattern _imp_（widget key）— 应保留
        "_imp_f_schmitt_ke": 0.15,
        "_imp_market": "美股 US",
        "_imp__config_initialized": True,
    }
    if extras:
        ss.update(extras)
    return ss


# ---------------------------------------------------------------------------
# Test: cleanup_orphaned_imp_keys
# ---------------------------------------------------------------------------

class TestCleanupOrphanedImpKeys:
    """验证 AppState.cleanup_orphaned_imp_keys() 的清理逻辑。"""

    def test_removes_orphaned_view_imp_keys(self, monkeypatch):
        """孤立的 view-pattern _imp_ key 应被删除。"""
        from shared.state import AppState

        ss = _make_ss_with_imp()
        # 模拟 st.session_state 为 dict
        mock_st = MagicMock()
        mock_st.session_state = ss
        monkeypatch.setattr("shared.state.st", mock_st)

        AppState.cleanup_orphaned_imp_keys()

        # 这些孤立 key 应被删除
        assert "_imp_v0_oldparam" not in ss, "无效 suffix 的 _imp_ key 应被删除"
        assert "_imp_v5_ke" not in ss, "vi=5 超出范围的 _imp_ key 应被删除"
        assert "_imp_v99_tf" not in ss, "vi=99 超出范围的 _imp_ key 应被删除"

    def test_preserves_valid_view_imp_keys(self, monkeypatch):
        """有效的 view-pattern _imp_ key 应保留。"""
        from shared.state import AppState

        ss = _make_ss_with_imp()
        mock_st = MagicMock()
        mock_st.session_state = ss
        monkeypatch.setattr("shared.state.st", mock_st)

        AppState.cleanup_orphaned_imp_keys()

        assert "_imp_v0_ke" in ss, "vi=0 suffix=ke 是有效的 view key"
        assert "_imp_v0_tf" in ss, "vi=0 suffix=tf 是有效的 view key"
        assert "_imp_v3_sm" in ss, "vi=3 suffix=sm 是有效的 view key"
        assert "_imp_v1_exp_all" in ss, "vi=1 suffix=exp_all 是有效的 view key"

    def test_preserves_non_view_imp_keys(self, monkeypatch):
        """非 view-pattern _imp_ key（widget key、系统 key）应保留。"""
        from shared.state import AppState

        ss = _make_ss_with_imp()
        mock_st = MagicMock()
        mock_st.session_state = ss
        monkeypatch.setattr("shared.state.st", mock_st)

        AppState.cleanup_orphaned_imp_keys()

        assert "_imp_f_schmitt_ke" in ss, "widget key _imp_ 备份应保留"
        assert "_imp_market" in ss, "全局 key _imp_ 备份应保留"
        assert "_imp__config_initialized" in ss, "系统 key _imp_ 备份应保留"

    def test_noop_on_empty_session_state(self, monkeypatch):
        """空 session_state 上调用不应报错。"""
        from shared.state import AppState

        ss = {}
        mock_st = MagicMock()
        mock_st.session_state = ss
        monkeypatch.setattr("shared.state.st", mock_st)

        AppState.cleanup_orphaned_imp_keys()

        assert len(ss) == 0

    def test_init_defaults_calls_cleanup(self, monkeypatch):
        """init_defaults() 应自动调用 cleanup_orphaned_imp_keys()。"""
        from shared.state import AppState

        ss = _make_ss_with_imp()
        mock_st = MagicMock()
        mock_st.session_state = ss
        monkeypatch.setattr("shared.state.st", mock_st)

        AppState.init_defaults()

        # 验证清理已执行
        assert "_imp_v0_oldparam" not in ss
        assert "_imp_v5_ke" not in ss

    def test_main_keys_untouched_by_cleanup(self, monkeypatch):
        """cleanup 只删 _imp_ 前缀的 key，主 key 原封不动。"""
        from shared.state import AppState

        ss = _make_ss_with_imp()
        # 加一个"疑似孤立但主key还在"的场景
        ss["v5_ke"] = 0.3
        ss["_imp_v5_ke"] = 0.3
        mock_st = MagicMock()
        mock_st.session_state = ss
        monkeypatch.setattr("shared.state.st", mock_st)

        AppState.cleanup_orphaned_imp_keys()

        # v5_ke 的 _imp_ 应被删除（vi=5 超出范围），但主 key v5_ke 保留
        assert "_imp_v5_ke" not in ss
        assert "v5_ke" in ss, "非 _imp_ 前缀的 key 不受清理影响"
        assert "v0_ke" in ss, "有效的 view 主 key 应保留"
        assert "v0_tf" in ss


# ---------------------------------------------------------------------------
# Test: Cache TTL — verify decorators have ttl parameter
# ---------------------------------------------------------------------------

class TestCacheTTL:
    """验证 streamlit_app.py 中 @st.cache_data 装饰器包含 ttl 参数。"""

    def test_all_cache_data_have_ttl(self):
        """所有 @st.cache_data 装饰器必须有 ttl 参数。"""
        app_path = Path(__file__).resolve().parent.parent / "filter" / "browse" / "app.py"
        source = app_path.read_text()
        tree = ast.parse(source)

        cache_decorators = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                # 解析 @st.cache_data(...) 或 @st.cache_data
                if isinstance(dec, ast.Call):
                    func = dec.func
                    if isinstance(func, ast.Attribute):
                        full = self._attr_name(func)
                        if full in ("st.cache_data",):
                            cache_decorators.append((node.name, dec))
                elif isinstance(dec, ast.Attribute):
                    full = self._attr_name(dec)
                    if full == "st.cache_data":
                        # 无括号用法: @st.cache_data (无参数)
                        cache_decorators.append((node.name, dec))

        assert len(cache_decorators) > 0, "应至少有一个 @st.cache_data 函数"

        for func_name, dec in cache_decorators:
            if isinstance(dec, ast.Call):
                has_ttl = any(
                    kw.arg == "ttl" for kw in dec.keywords
                )
                assert has_ttl, (
                    f"函数 '{func_name}' 的 @st.cache_data 缺少 ttl 参数"
                )

    def test_cached_fetch_stock_ttl_is_3600(self):
        """数据加载缓存 _cached_fetch_stock ttl 应为 3600 (1小时)。"""
        app_path = Path(__file__).resolve().parent.parent / "filter" / "browse" / "app.py"
        source = app_path.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_cached_fetch_stock":
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call):
                        for kw in dec.keywords:
                            if kw.arg == "ttl":
                                assert kw.value.value == 3600, (
                                    f"_cached_fetch_stock ttl 应为 3600，实际 {kw.value.value}"
                                )
                                return
        pytest.fail("_cached_fetch_stock 未找到或缺少 ttl 参数")

    def test_compute_functions_ttl_is_600(self):
        """计算结果缓存函数 ttl 应为 600 (10分钟)。"""
        compute_funcs = [
            "_compute_filters",
            "_compute_schmitt_trigger",
            "_compute_prediction_pairs",
            "_cached_strategy_pnl",
        ]
        app_path = Path(__file__).resolve().parent.parent / "filter" / "browse" / "app.py"
        source = app_path.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in compute_funcs:
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call):
                        for kw in dec.keywords:
                            if kw.arg == "ttl":
                                assert kw.value.value == 600, (
                                    f"{node.name} ttl 应为 600，实际 {kw.value.value}"
                                )
                                compute_funcs.remove(node.name)
                                break

        assert len(compute_funcs) == 0, (
            f"以下函数未找到或缺少 ttl=600: {compute_funcs}"
        )

    @staticmethod
    def _attr_name(node):
        """递归构建 ast.Attribute 的完整名称，如 st.cache_data。"""
        parts = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))


class TestHandlePendingApply:
    """_handle_pending_apply 测试."""

    def test_no_pending_params(self):
        """无等待参数时不报错."""
        from filter.browse.sidebar import _handle_pending_apply
        with patch("filter.browse.sidebar.AppState.has", return_value=False):
            _handle_pending_apply()

    def test_pending_params_none(self):
        """等待参数为 None 时直接返回."""
        from filter.browse.sidebar import _handle_pending_apply
        with patch("filter.browse.sidebar.AppState.has", return_value=True), \
             patch("filter.browse.sidebar.AppState.pop", return_value=None):
            _handle_pending_apply()

    def test_pending_params_applied(self):
        """等待参数正常应用."""
        from filter.browse.sidebar import _handle_pending_apply
        params = {"key1": "val1", "key2": 42}

        call_args = []

        def fake_has(key):
            return key == "_pending_apply_params"

        def fake_pop(key):
            return params

        def fake_set(key, value):
            call_args.append((key, value))

        with patch("filter.browse.sidebar.AppState.has", side_effect=fake_has), \
             patch("filter.browse.sidebar.AppState.pop", side_effect=fake_pop), \
             patch("filter.browse.sidebar.AppState.set", side_effect=fake_set):
            _handle_pending_apply()

        assert len(call_args) == 3  # key1, key2, _import_data
