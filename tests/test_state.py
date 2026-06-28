"""Tests for streamlit.state -- AppState session_state management.

The conftest.py mocks `streamlit` as a MagicMock, which shadows the real
package name in sys.modules.  Since state.py guards with
    try: import streamlit as st; except ImportError: st = None
we remove the mock before importing state so that `st = None`, then
patch in a real dict-backed mock via the autouse fixture.
"""
import sys
from typing import Any, Dict

import pytest


# Remove the conftest mock so that state.py's import guard fires and `st = None`
_old_streamlit = sys.modules.pop("streamlit", None)

# Now import state.py as a standalone module (conftest.py already added
# its parent dir to sys.path).
import state  # noqa: E402

# Restore the mock into sys.modules so conftest's other behavior still works
sys.modules["streamlit"] = _old_streamlit


AppState = state.AppState
SYSTEM_KEYS = state.SYSTEM_KEYS


# ---------------------------------------------------------------------------
# Autouse fixture: replace state.st with a streamlit mock backed by a real dict
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def real_session_state():
    """Replace state.st with a mock whose session_state is a real dict.

    This gives AppState methods (__contains__, pop, __getitem__) real dict
    behavior so the method bodies are actually exercised.
    """
    from unittest.mock import MagicMock

    real_ss: Dict[str, Any] = {}
    mock_st = MagicMock()
    mock_st.session_state = real_ss
    state.st = mock_st
    return real_ss


# ====================================================================
# init_defaults
# ====================================================================

class TestAppStateInitDefaults:
    @staticmethod
    def _clear_system_keys(ss: Dict[str, Any]):
        for k in SYSTEM_KEYS:
            ss.pop(k, None)

    def test_skips_none_defaults(self, real_session_state):
        self._clear_system_keys(real_session_state)
        AppState.init_defaults()
        for k, v in SYSTEM_KEYS.items():
            if v is None:
                assert k not in real_session_state, (
                    f"Key {k!r} has None default and should not be written"
                )

    def test_writes_non_none_defaults(self, real_session_state):
        self._clear_system_keys(real_session_state)
        AppState.init_defaults()
        for k, v in SYSTEM_KEYS.items():
            if v is not None:
                assert k in real_session_state, (
                    f"Key {k!r} with default {v!r} should be written"
                )
                assert real_session_state[k] == v

    def test_does_not_overwrite_existing_keys(self, real_session_state):
        self._clear_system_keys(real_session_state)
        real_session_state["_config_initialized"] = False
        AppState.init_defaults()
        assert real_session_state["_config_initialized"] is False


# ====================================================================
# has
# ====================================================================

class TestAppStateHas:
    def test_missing_key_returns_false(self, real_session_state):
        assert AppState.has("_nonexistent") is False

    def test_present_key_returns_true(self, real_session_state):
        real_session_state["_my_key"] = 42
        assert AppState.has("_my_key") is True

    def test_fallback_imp_key(self, real_session_state):
        real_session_state["_imp__my_key"] = "backup"
        assert AppState.has("_my_key") is True

    def test_none_default_key_not_in_session(self, real_session_state):
        real_session_state.pop("_pending_apply_params", None)
        real_session_state.pop("_imp__pending_apply_params", None)
        assert AppState.has("_pending_apply_params") is False

    def test_key_set_then_has(self, real_session_state):
        AppState.set("_tmp_has_test", "hello")
        assert AppState.has("_tmp_has_test") is True
        AppState.pop("_tmp_has_test")


# ====================================================================
# get
# ====================================================================

class TestAppStateGet:
    def test_get_existing_key(self, real_session_state):
        real_session_state["_my_val"] = "world"
        assert AppState.get("_my_val") == "world"

    def test_get_missing_key_returns_none(self, real_session_state):
        assert AppState.get("_ghost") is None

    def test_get_missing_key_returns_default(self, real_session_state):
        assert AppState.get("_ghost", 42) == 42

    def test_get_fallback_to_imp(self, real_session_state):
        real_session_state["_imp__fallback_test"] = "from_imp"
        assert AppState.get("_fallback_test") == "from_imp"

    def test_primary_takes_precedence_over_imp(self, real_session_state):
        real_session_state["_precedence"] = "primary"
        real_session_state["_imp__precedence"] = "backup"
        assert AppState.get("_precedence") == "primary"


# ====================================================================
# set
# ====================================================================

class TestAppStateSet:
    def test_set_writes_primary(self, real_session_state):
        AppState.set("_set_test", "value")
        assert real_session_state["_set_test"] == "value"

    def test_set_writes_imp_backup(self, real_session_state):
        AppState.set("_set_test2", "value2")
        assert real_session_state["_imp__set_test2"] == "value2"

    def test_set_imp_disabled_skips_backup(self, real_session_state):
        old = AppState._imp_enabled
        AppState._imp_enabled = False
        try:
            AppState.set("_set_no_imp", "val")
            assert "_imp__set_no_imp" not in real_session_state
        finally:
            AppState._imp_enabled = old


# ====================================================================
# set_many
# ====================================================================

class TestAppStateSetMany:
    def test_set_many_writes_all(self, real_session_state):
        AppState.set_many({"_a": 1, "_b": 2, "_c": 3})
        assert real_session_state["_a"] == 1
        assert real_session_state["_b"] == 2
        assert real_session_state["_c"] == 3

    def test_set_many_writes_imp_for_all(self, real_session_state):
        AppState.set_many({"_x": 10, "_y": 20})
        assert real_session_state["_imp__x"] == 10
        assert real_session_state["_imp__y"] == 20


# ====================================================================
# pop
# ====================================================================

class TestAppStatePop:
    def test_pop_removes_key(self, real_session_state):
        real_session_state["_pop_me"] = "bye"
        AppState.pop("_pop_me")
        assert "_pop_me" not in real_session_state

    def test_pop_returns_value(self, real_session_state):
        real_session_state["_pop_val"] = 99
        assert AppState.pop("_pop_val") == 99

    def test_pop_missing_returns_none(self, real_session_state):
        assert AppState.pop("_not_there") is None

    def test_pop_missing_returns_default(self, real_session_state):
        assert AppState.pop("_not_there", "fallback") == "fallback"

    def test_pop_also_removes_imp(self, real_session_state):
        real_session_state["_both"] = "main"
        real_session_state["_imp__both"] = "backup"
        AppState.pop("_both")
        assert "_both" not in real_session_state
        assert "_imp__both" not in real_session_state


# ====================================================================
# get_global
# ====================================================================

class TestAppStateGetGlobal:
    def test_returns_global_default(self, real_session_state):
        val = AppState.get_global("market")
        assert val == "美股 US"

    def test_global_fallback_to_explicit_default(self, real_session_state):
        val = AppState.get_global("undefined_key", "fallback")
        assert val == "fallback"


# ====================================================================
# get_view_key (no session_state needed)
# ====================================================================

class TestAppStateGetViewKey:
    def test_view_key_format(self):
        assert AppState.get_view_key(0, "ke") == "v0_ke"
        assert AppState.get_view_key(3, "tf") == "v3_tf"
        assert AppState.get_view_key(1, "n") == "v1_n"


# ====================================================================
# Bug regression -- NoneType crash on _pending_apply_params
# ====================================================================

class TestPendingApplyParamsLifecycle:
    """Regression tests for the NoneType bug.

    The bug: _pending_apply_params has a None default.  After preset
    apply, code did `params = AppState.pop("_pending_apply_params")`
    without checking for None, then iterated over it, causing
    `TypeError: 'NoneType' object is not iterable`.
    """

    def test_pop_after_set_returns_correct_value(self, real_session_state):
        AppState.set("_pending_apply_params", {"key1": "val1"})
        params = AppState.pop("_pending_apply_params")
        assert params == {"key1": "val1"}

    def test_key_cleared_after_pop(self, real_session_state):
        AppState.set("_pending_apply_params", {"k": "v"})
        AppState.pop("_pending_apply_params")
        assert AppState.has("_pending_apply_params") is False

    def test_double_pop_returns_none(self, real_session_state):
        AppState.set("_pending_apply_params", {"k": "v"})
        AppState.pop("_pending_apply_params")
        val = AppState.pop("_pending_apply_params")
        assert val is None

    def test_none_in_session_is_not_iterable(self, real_session_state):
        real_session_state["_pending_apply_params"] = None
        val = AppState.get("_pending_apply_params")
        assert val is None
        with pytest.raises(TypeError):
            for _ in val:  # type: ignore[arg-type]
                pass

    def test_full_apply_lifecycle(self, real_session_state):
        preset = {"tf": "1h", "ke": 0.15, "sm": 20}
        AppState.set("_pending_apply_params", preset)

        if AppState.has("_pending_apply_params"):
            params = AppState.pop("_pending_apply_params")
            if params is not None:  # guard clause
                for k, v in params.items():
                    AppState.set(k, v)

        assert AppState.get("tf") == "1h"
        assert AppState.get("ke") == 0.15
        assert AppState.get("sm") == 20
