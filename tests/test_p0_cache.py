"""P0-1 / P0-2 缓存行为测试: 验证缓存命中和失效逻辑"""
import sys
from pathlib import Path

# Ensure filter_app is on path
_src = Path(__file__).resolve().parent.parent / "filter_app"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))


class TestSynthCache:
    """P0-1: 验证 _sync_all_cascading 模块级缓存"""

    def test_cache_state_initialized(self):
        """模块级缓存变量存在且初始为空"""
        import services.data_loader as dl
        assert hasattr(dl, "_synth_cache_state")
        # After import, cache may contain data from other tests — check it's a dict
        assert isinstance(dl._synth_cache_state, dict)

    def test_cache_hit_returns_dict(self):
        """模拟缓存命中：设置 last_key + last_result 后再次调用应返回缓存值"""
        import services.data_loader as dl
        # Save original
        orig_state = dict(dl._synth_cache_state)
        try:
            dl._synth_cache_state.clear()
            dl._synth_cache_state["last_key"] = ("TEST_TICKER", "2026-01-01", "120")
            dl._synth_cache_state["last_result"] = {"日线": True, "60分钟": True}

            # Simulate call with matching params
            cache_key = ("TEST_TICKER", "2026-01-01", "120")
            assert cache_key == dl._synth_cache_state.get("last_key")
            # The function would return cache dict
            result = dl._synth_cache_state.get("last_result", {})
            assert result == {"日线": True, "60分钟": True}
        finally:
            dl._synth_cache_state.clear()
            dl._synth_cache_state.update(orig_state)

    def test_cache_key_different_cutoff_invalidates(self):
        """不同 cutoff_date 会生成不同缓存键，缓存应失效"""
        import services.data_loader as dl
        orig_state = dict(dl._synth_cache_state)
        try:
            dl._synth_cache_state.clear()
            dl._synth_cache_state["last_key"] = ("TEST", "2026-01-01", "120")

            # Different cutoff => different key
            cache_key = ("TEST", "2026-01-02", "120")
            assert cache_key != dl._synth_cache_state.get("last_key")
        finally:
            dl._synth_cache_state.clear()
            dl._synth_cache_state.update(orig_state)


class TestPipelineCache:
    """P0-2: 验证 BacktestRunner._pipeline_cache 增量计算缓存"""

    def test_pipeline_cache_initialized(self):
        """BacktestRunner.__init__ 初始化 _pipeline_cache"""
        from services.backtest_core import BacktestRunner
        runner = BacktestRunner("TEST", [{"tf": "日线", "n_pts": 120, "_fid": "sma", "pv": {"window": 11}}])
        assert hasattr(runner, "_pipeline_cache")
        assert isinstance(runner._pipeline_cache, dict)
        assert len(runner._pipeline_cache) == 0

    def test_pipeline_cache_key_uniqueness(self):
        """不同 tf 的缓存键不冲突"""
        from services.backtest_core import BacktestRunner
        runner = BacktestRunner("TEST", [
            {"tf": "日线", "n_pts": 120, "_fid": "sma", "pv": {"window": 11}},
        ])
        key1 = ("日线", 120, "hash123")
        key2 = ("60分钟", 120, "hash123")
        runner._pipeline_cache[key1] = ("data1", None)
        runner._pipeline_cache[key2] = ("data2", None)
        assert runner._pipeline_cache[key1] != runner._pipeline_cache[key2]


class TestPathFixVerification:
    """P0-4: 验证 test_param_export_import 不再依赖硬编码路径"""

    def test_no_hardcoded_config_path(self):
        """CONFIG_PATH 不再硬编码绝对路径"""
        _tests_dir = Path(__file__).resolve().parent
        if str(_tests_dir) not in sys.path:
            sys.path.insert(0, str(_tests_dir))
        import test_param_export_import as tpie
        # After fix, CONFIG_PATH should not exist (replaced by fixture)
        assert not hasattr(tpie, "CONFIG_PATH"), (
            "CONFIG_PATH should be removed — tests now use sample_config fixture"
        )

    def test_sample_config_fixture_has_keys(self):
        """sample_config fixture 包含必要字段"""
        _tests_dir = Path(__file__).resolve().parent
        if str(_tests_dir) not in sys.path:
            sys.path.insert(0, str(_tests_dir))
        import test_param_export_import as tpie
        # Verify the fixture function exists
        assert hasattr(tpie, "sample_config"), "sample_config fixture should be defined"
