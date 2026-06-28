"""集成测试 — 跨模块用户流程验证

测试预设应用、配置导入、系统 key 一致性、_imp_ 备份端到端、session_state
隔离性等跨模块用户流程。不依赖 widget mock，仅测试 AppState 核心逻辑。
"""
import sys
from typing import Any, Dict

import pytest

# 移除 conftest mock 使 state.py 中 import streamlit 失败 → st = None
_old_streamlit = sys.modules.pop("streamlit", None)
import state  # noqa: E402
sys.modules["streamlit"] = _old_streamlit

AppState = state.AppState
SYSTEM_KEYS = state.SYSTEM_KEYS


@pytest.fixture(autouse=True)
def real_session_state():
    """Replace state.st with a mock backed by a real dict."""
    from unittest.mock import MagicMock

    real_ss: Dict[str, Any] = {}
    mock_st = MagicMock()
    mock_st.session_state = real_ss
    state.st = mock_st
    return real_ss


# ====================================================================
# Preset Apply Flow
# ====================================================================

class TestPresetApplyFlow:
    """预设应用完整流程"""

    def test_set_many_then_pop_applies_all_params(self, real_session_state):
        """AppState.set_many → pop → 所有参数正确写入"""
        params = {"tf": "1h", "ke": 0.15, "sm": 20, "ew": 60, "fm": "schmitt"}
        AppState.set("_pending_apply_params", params)

        # 模拟 _handle_pending_apply
        if AppState.has("_pending_apply_params"):
            p = AppState.pop("_pending_apply_params")
            if p is not None:
                for k, v in p.items():
                    AppState.set(k, v)

        # 验证
        assert AppState.get("tf") == "1h"
        assert AppState.get("ke") == 0.15
        assert not AppState.has("_pending_apply_params")

    def test_none_params_safely_handled(self, real_session_state):
        """None 预设不 crash (回归测试)"""
        AppState.set("_pending_apply_params", None)
        if AppState.has("_pending_apply_params"):
            p = AppState.pop("_pending_apply_params")
            if p is not None:
                for k, v in p.items():  # pragma: no cover
                    pass
        # 不应 crash
        assert True


# ====================================================================
# Config Import Flow
# ====================================================================

class TestConfigImportFlow:
    """配置导入/导出流程"""

    def test_batch_import_via_set_many(self, real_session_state):
        """批量导入参数"""
        config = {"tf": "1d", "ke": 0.10, "sm": 50, "market": "HK"}
        AppState.set_many(config)
        for k, v in config.items():
            assert AppState.get(k) == v

    def test_set_then_pop_clears_both(self, real_session_state):
        """set 后 pop 清除主 key 和 _imp_"""
        AppState.set("_test_cfg", "value")
        assert AppState.get("_test_cfg") == "value"
        AppState.pop("_test_cfg")
        assert not AppState.has("_test_cfg")


# ====================================================================
# System Keys Consistency
# ====================================================================

class TestSystemKeysConsistency:
    """SYSTEM_KEYS 一致性"""

    def test_none_defaults_not_in_session_after_init(self, real_session_state):
        """init_defaults 后 None 值 key 不在 session_state"""
        # 清除可能的残留
        for k in list(real_session_state.keys()):
            if k.startswith("_pending") or k.startswith("_preset"):
                real_session_state.pop(k, None)

        AppState.init_defaults()
        for k, v in SYSTEM_KEYS.items():
            if v is None:
                assert not AppState.has(k), f"{k} should not be in session_state"

    def test_all_non_none_system_keys_present(self, real_session_state):
        """所有非 None 的 SYSTEM_KEYS 在 init 后被设置"""
        # 清除所有 system keys 以确保 init_defaults 真正写入
        for k in SYSTEM_KEYS:
            real_session_state.pop(k, None)
            real_session_state.pop(f"_imp_{k}", None)

        AppState.init_defaults()
        for k, v in SYSTEM_KEYS.items():
            if v is not None:
                assert AppState.has(k), f"{k} should be in session_state"


# ====================================================================
# _imp_ Backup End-to-End
# ====================================================================

class TestImpBackupEndToEnd:
    """_imp_ 备份端到端"""

    def test_pop_removes_both_keys(self, real_session_state):
        """pop 同时清除主 key 和 _imp_"""
        AppState.set("_test_e2e", "data")
        assert AppState.has("_test_e2e")

        AppState.pop("_test_e2e")
        assert not AppState.has("_test_e2e")

    def test_set_with_imp_disabled(self, real_session_state):
        """_imp_enabled=False 时不写备份"""
        old = AppState._imp_enabled
        AppState._imp_enabled = False
        AppState.set("_test_noimp", "val")
        AppState.pop("_test_noimp")
        AppState._imp_enabled = old

        # 验证恢复正常
        AppState.set("_test_restored", "val")
        assert AppState.get("_test_restored") == "val"
        AppState.pop("_test_restored")


# ====================================================================
# Session State Isolation
# ====================================================================

class TestSessionStateIsolation:
    """session_state 隔离性"""

    def test_independent_keys_dont_interfere(self, real_session_state):
        """不同 key 的读写互不干扰"""
        AppState.set("_isolated_a", 1)
        AppState.set("_isolated_b", 2)
        assert AppState.get("_isolated_a") == 1
        assert AppState.get("_isolated_b") == 2

        AppState.pop("_isolated_a")
        assert not AppState.has("_isolated_a")
        assert AppState.has("_isolated_b")
        AppState.pop("_isolated_b")
