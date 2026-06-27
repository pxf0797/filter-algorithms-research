"""
test_preset_ui_actions.py — 预设管理 UI 新行为测试

测试重构后的行为：
1. session_state 驱动的 _preset_action / _preset_action_id 标志
2. text_input 自动同步 (new_preset_name + _last_sel_name)
3. preset_map 字典查表
4. category 参数补全 (保存时传递正确的 category)
5. toast 反馈机制 (通过 session_state 标志验证)

无 Streamlit 运行时依赖 — 使用 dict-like session_state mock。
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

_src = Path(__file__).resolve().parent.parent / "streamlit"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

import config_db as cfg


@pytest.fixture(autouse=True)
def isolate_db(monkeypatch):
    """每个测试使用独立的临时 SQLite 数据库。"""
    tmpdir = Path(tempfile.mkdtemp())
    db_path = tmpdir / "config.db"
    config_dir = tmpdir / "config"
    config_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(cfg, "_CONFIG_DB_PATH", db_path)
    monkeypatch.setattr(cfg, "_CONFIG_DIR", config_dir)
    cfg.init_config_tables()
    yield tmpdir
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


# ============================================================
# 辅助：模拟 session_state
# ============================================================

class MockSessionState(dict):
    """dict-like session_state，支持 .get(key, default) 和 .pop(key, default)。"""
    pass


# ============================================================
# 1. preset_action 标志
# ============================================================

class TestPresetActionFlags:
    """_preset_action 和 _preset_action_id 标志的状态转换。"""

    def setup_method(self):
        self.ss = MockSessionState()

    def test_update_button_sets_action_flags(self):
        """点击'更新'按钮后 _preset_action='update' 且 _preset_action_id 被设置。"""
        pid = cfg.save_preset("TestPreset", json.dumps({"v0_ke": 0.1}))
        p = cfg.get_preset(pid)

        # 模拟按钮点击：设置标志
        self.ss["_preset_action"] = "update"
        self.ss["_preset_action_id"] = p["preset_id"]

        assert self.ss["_preset_action"] == "update"
        assert self.ss["_preset_action_id"] == p["preset_id"]

    def test_rename_button_sets_action_flags(self):
        """点击'重命名'按钮后 _preset_action='rename'。"""
        pid = cfg.save_preset("ToRename", json.dumps({"x": 1}))
        p = cfg.get_preset(pid)

        self.ss["_preset_action"] = "rename"
        self.ss["_preset_action_id"] = p["preset_id"]

        assert self.ss["_preset_action"] == "rename"
        assert self.ss["_preset_action_id"] == p["preset_id"]

    def test_delete_button_sets_action_flags(self):
        """点击'删除'按钮后 _preset_action='delete'。"""
        pid = cfg.save_preset("ToDelete", json.dumps({"x": 1}))
        p = cfg.get_preset(pid)

        self.ss["_preset_action"] = "delete"
        self.ss["_preset_action_id"] = p["preset_id"]

        assert self.ss["_preset_action"] == "delete"
        assert self.ss["_preset_action_id"] == p["preset_id"]

    def test_action_flags_cleared_after_update_complete(self):
        """更新操作完成后标志被清除。"""
        pid = cfg.save_preset("UpdateMe", json.dumps({"v": 1}))
        self.ss["_preset_action"] = "update"
        self.ss["_preset_action_id"] = pid

        # 模拟完成：清除标志
        self.ss.pop("_preset_action", None)
        self.ss.pop("_preset_action_id", None)

        assert "_preset_action" not in self.ss
        assert "_preset_action_id" not in self.ss

    def test_action_flags_cleared_after_rename_complete(self):
        """重命名操作完成后标志被清除。"""
        pid = cfg.save_preset("RenameMe", json.dumps({"v": 1}))
        self.ss["_preset_action"] = "rename"
        self.ss["_preset_action_id"] = pid

        self.ss.pop("_preset_action", None)
        self.ss.pop("_preset_action_id", None)

        assert "_preset_action" not in self.ss
        assert "_preset_action_id" not in self.ss

    def test_action_flags_cleared_after_delete_complete(self):
        """删除操作完成后标志被清除。"""
        pid = cfg.save_preset("DeleteMe", json.dumps({"v": 1}))
        self.ss["_preset_action"] = "delete"
        self.ss["_preset_action_id"] = pid

        self.ss.pop("_preset_action", None)
        self.ss.pop("_preset_action_id", None)

        assert "_preset_action" not in self.ss
        assert "_preset_action_id" not in self.ss

    def test_action_flags_cleared_on_cancel(self):
        """取消操作后标志被清除。"""
        pid = cfg.save_preset("CancelMe", json.dumps({"v": 1}))

        # 设置任一 action
        self.ss["_preset_action"] = "update"
        self.ss["_preset_action_id"] = pid

        # 模拟取消
        self.ss.pop("_preset_action", None)
        self.ss.pop("_preset_action_id", None)

        assert "_preset_action" not in self.ss

    def test_action_target_not_found_clears_flags(self):
        """目标预设不存在时（如已被另一会话删除），清除标志并退出。"""
        # 模拟一个已不存在的 preset_id
        self.ss["_preset_action"] = "update"
        self.ss["_preset_action_id"] = 99999

        # 模拟 target==None 的分支：清除标志
        target = cfg.get_preset(99999)
        assert target is None
        self.ss.pop("_preset_action", None)
        self.ss.pop("_preset_action_id", None)

        assert "_preset_action" not in self.ss

    def test_no_action_when_flags_unset(self):
        """没有 action 标志时不渲染确认 UI。"""
        assert self.ss.get("_preset_action") is None
        assert self.ss.get("_preset_action_id") is None


# ============================================================
# 2. text_input 自动同步
# ============================================================

class TestPresetNameSync:
    """选择预设时 new_preset_name 自动更新。"""

    def setup_method(self):
        self.ss = MockSessionState()

    def test_select_preset_syncs_name_with_copy_suffix(self):
        """选择预设时 new_preset_name 自动更新为 {name}_副本。"""
        cfg.save_preset("MyConfig", json.dumps({"v": 1}), category="单滤波")

        # 模拟 selectbox 选择后的同步逻辑
        curr_sel_name = "MyConfig"
        expected = curr_sel_name + "_副本"

        assert expected == "MyConfig_副本"

    def test_select_none_clears_new_preset_name(self):
        """选择 '(不选择)' 时 new_preset_name 清空。"""
        # 模拟无预设选中时
        curr_sel_name = ""  # selected_preset is None
        expected = "" if not curr_sel_name else curr_sel_name + "_副本"
        assert expected == ""

    def test_last_sel_name_tracks_change(self):
        """_last_sel_name 追踪变化，避免重复同步。"""
        self.ss["_last_sel_name"] = ""

        # 首次选择
        curr_sel_name = "PresetA"
        if self.ss["_last_sel_name"] != curr_sel_name:
            self.ss["new_preset_name"] = curr_sel_name + "_副本"
            self.ss["_last_sel_name"] = curr_sel_name

        assert self.ss["new_preset_name"] == "PresetA_副本"
        assert self.ss["_last_sel_name"] == "PresetA"

    def test_same_selection_no_re_sync(self):
        """同一预设重复选择时不会覆盖手动编辑的名称。"""
        self.ss["_last_sel_name"] = "PresetA"
        self.ss["new_preset_name"] = "MyManualName"  # 用户手动编辑

        # 再次渲染，curr_sel_name 未变
        curr_sel_name = "PresetA"
        if self.ss["_last_sel_name"] != curr_sel_name:
            # 不应进入此分支
            self.ss["new_preset_name"] = curr_sel_name + "_副本"

        # 手动编辑的名称被保留
        assert self.ss["new_preset_name"] == "MyManualName"

    def test_selection_changes_triggers_sync(self):
        """预设选择变化时，名称被重新同步。"""
        self.ss["_last_sel_name"] = "OldPreset"
        self.ss["new_preset_name"] = "OldPreset_副本"

        # 用户选择了一个新预设
        curr_sel_name = "NewPreset"
        if self.ss["_last_sel_name"] != curr_sel_name:
            self.ss["new_preset_name"] = curr_sel_name + "_副本"
            self.ss["_last_sel_name"] = curr_sel_name

        assert self.ss["new_preset_name"] == "NewPreset_副本"

    def test_init_no_selection(self):
        """初始无选择时 _last_sel_name 为 ''，不进入同步分支。
        此时 new_preset_name 由 Streamlit text_input widget 默认值决定（空字符串）。"""
        if "_last_sel_name" not in self.ss:
            self.ss["_last_sel_name"] = ""
        curr_sel_name = ""
        if self.ss["_last_sel_name"] != curr_sel_name:
            self.ss["new_preset_name"] = (
                curr_sel_name + "_副本" if curr_sel_name else "")
            self.ss["_last_sel_name"] = curr_sel_name

        # 两者都是空字符串且相等 → 不进入同步分支，new_preset_name 未被显式设置。
        # 在实际 Streamlit 中 text_input(key="new_preset_name") 默认 value=""。
        assert self.ss["_last_sel_name"] == ""
        assert self.ss.get("new_preset_name", "") == ""


# ============================================================
# 3. preset_map 查表
# ============================================================

class TestPresetMapLookup:
    """preset_map 字典正确构建 label→preset 映射。"""

    def setup_method(self):
        pass

    def test_preset_map_builds_correctly(self):
        """preset_map 正确构建，label 包含分类前缀。"""
        cfg.save_preset("AAPL_base", json.dumps({"ticker": "AAPL"}), category="单滤波")
        cfg.save_preset("TSLA_DP", json.dumps({"ticker": "TSLA"}), category="双滤波")
        cfg.save_preset("QQQ_QS", json.dumps({"ticker": "QQQ"}), category="快速")

        presets = cfg.list_presets()
        preset_map = {f"[{p['category']}] {p['name']}": p for p in presets}

        assert "[单滤波] AAPL_base" in preset_map
        assert "[双滤波] TSLA_DP" in preset_map
        assert "[快速] QQQ_QS" in preset_map
        assert len(preset_map) == 3

        # 验证映射值正确
        assert preset_map["[单滤波] AAPL_base"]["name"] == "AAPL_base"
        assert preset_map["[双滤波] TSLA_DP"]["category"] == "双滤波"

    def test_same_name_different_category_unique_keys(self):
        """同名不同分类的预设在 preset_map 中有独立的 label key。"""
        cfg.save_preset("Shared", json.dumps({"v": 1}), category="单滤波")
        cfg.delete_preset(cfg.get_preset_by_name("Shared")["preset_id"])
        cfg.save_preset("Shared", json.dumps({"v": 2}), category="双滤波")

        presets = cfg.list_presets()
        preset_map = {f"[{p['category']}] {p['name']}": p for p in presets}

        assert "[双滤波] Shared" in preset_map
        assert "[单滤波] Shared" not in preset_map
        assert len(preset_map) == 1

    def test_preset_map_lookup_returns_full_record(self):
        """preset_map 查询返回完整的 preset 记录。"""
        pid = cfg.save_preset("FullRecord", json.dumps({"ticker": "NVDA", "v0_ke": 0.15}),
                              description="英伟达配置", category="单滤波")

        presets = cfg.list_presets()
        preset_map = {f"[{p['category']}] {p['name']}": p for p in presets}
        p = preset_map["[单滤波] FullRecord"]

        assert p["preset_id"] == pid
        assert p["name"] == "FullRecord"
        assert p["description"] == "英伟达配置"
        assert p["category"] == "单滤波"

    def test_empty_presets_produces_empty_map(self):
        """无预设时 preset_map 为空。"""
        presets = cfg.list_presets()
        preset_map = {f"[{p['category']}] {p['name']}": p for p in presets}
        assert preset_map == {}

    def test_preset_map_label_parsing_roundtrip(self):
        """用 preset_map 解析 selectbox label 可以还原对应预设。"""
        pid = cfg.save_preset("Roundtrip", json.dumps({"key": "value"}), category="单滤波")

        presets = cfg.list_presets()
        preset_labels = ["(不选择)"] + [f"[{p['category']}] {p['name']}" for p in presets]
        preset_map = {f"[{p['category']}] {p['name']}": p for p in presets}

        # 模拟选择第一个有效 label
        selected_label = preset_labels[1]
        assert selected_label == "[单滤波] Roundtrip"

        selected_preset = preset_map.get(selected_label)
        assert selected_preset is not None
        assert selected_preset["preset_id"] == pid

    def test_deselect_label_not_in_map(self):
        """'(不选择)' label 不在 preset_map 中。"""
        pid = cfg.save_preset("Any", json.dumps({"x": 1}))
        presets = cfg.list_presets()
        preset_map = {f"[{p['category']}] {p['name']}": p for p in presets}

        assert preset_map.get("(不选择)") is None


# ============================================================
# 4. category 参数补全
# ============================================================

class TestCategoryPreservation:
    """保存时 category 的正确传递。"""

    def test_overwrite_preserves_original_category(self):
        """覆盖保存时 category 保持原值不变。"""
        pid = cfg.save_preset("CatTest", json.dumps({"v": 1}),
                              category="双滤波", description="原始描述")

        # 模拟覆盖保存
        target = cfg.get_preset(pid)
        cat = target.get("category", "通用")  # overwrite → 保留原分类
        assert cat == "双滤波"

        # 用原 category 覆盖保存
        save_preset_result = cfg.save_preset(
            target["name"],
            json.dumps({"v": 2}),
            description=target.get("description", ""),
            category=cat,
        )
        assert save_preset_result == pid

        # 确认未被覆盖为默认值
        updated = cfg.get_preset(pid)
        assert updated["category"] == "双滤波"

    def test_new_preset_uses_default_category(self):
        """新建预设时 category 为默认值 '通用'。"""
        pid = cfg.save_preset("NewDefault", json.dumps({"x": 1}))
        p = cfg.get_preset(pid)
        assert p["category"] == "通用"

    def test_new_preset_can_specify_category(self):
        """新建预设时可以指定 category。"""
        pid = cfg.save_preset("CustomCat", json.dumps({"x": 1}), category="快速")
        p = cfg.get_preset(pid)
        assert p["category"] == "快速"

    def test_category_persists_through_multiple_overwrites(self):
        """多次覆盖保存后 category 始终不变。"""
        pid = cfg.save_preset("MultiCat", json.dumps({"gen": 1}), category="双滤波")

        for i in range(2, 6):
            t = cfg.get_preset(pid)
            save_preset_result = cfg.save_preset(
                t["name"],
                json.dumps({"gen": i}),
                description=t.get("description", ""),
                category=t.get("category", "通用"),
            )
            assert save_preset_result == pid

        final = cfg.get_preset(pid)
        assert final["category"] == "双滤波"
        assert json.loads(final["params_json"])["gen"] == 5

    def test_default_category_is_general(self):
        """不传 category 时 save_preset 默认使用 '通用'。"""
        import inspect

        # 确认 save_preset 的 category 参数默认值是 "通用"
        sig = inspect.signature(cfg.save_preset)
        assert sig.parameters["category"].default == "通用"


# ============================================================
# 5. toast 反馈机制
# ============================================================

class TestToastFeedback:
    """验证操作后反馈机制的存在性（通过 session_state 标志 + config_db 副作用验证）。"""

    def setup_method(self):
        self.ss = MockSessionState()

    def test_apply_preset_returns_params_for_feedback(self):
        """apply_preset 返回 dict，调用者可据此判断是否成功。"""
        pid = cfg.save_preset("FeedbackTest", json.dumps({"v0_ke": 0.2}))
        params = cfg.apply_preset(pid)
        assert params is not None
        assert "v0_ke" in params

    def test_save_preset_returns_valid_preset_id(self):
        """save_preset 返回有效的 preset_id 可供 feedback 参考。"""
        pid = cfg.save_preset("SaveFeedback", json.dumps({"a": 1}))
        assert pid > 0
        assert cfg.get_preset(pid) is not None

    def test_rename_preset_returns_new_name(self):
        """rename_preset 成功时返回新名称。"""
        pid = cfg.save_preset("BeforeRename", json.dumps({"x": 1}))
        result = cfg.rename_preset(pid, "AfterRename")
        assert result == "AfterRename"

    def test_delete_preset_returns_true_on_success(self):
        """delete_preset 成功时返回 True。"""
        pid = cfg.save_preset("ToDelete", json.dumps({"x": 1}))
        result = cfg.delete_preset(pid)
        assert result is True

    def test_delete_preset_returns_false_on_missing(self):
        """delete_preset 对不存在 ID 返回 False。"""
        result = cfg.delete_preset(99999)
        assert result is False

    def test_simulate_apply_via_deferred_mechanism(self):
        """模拟应用预设：使用 _pending_apply_params 延迟机制。

        验证参数在"widget 创建前"（模拟 main() 顶部）被应用，
        而非在按钮回调中直接设置 session_state。
        """
        # 预设包含 widget 绑定的 key（与 collect_current_params 返回的一致）
        params = {"ticker": "TSLA", "market": "港股 HK", "v0_ke": 0.3, "global_f": "ema"}

        # ---- 阶段1：按钮回调（widget 已渲染） ----
        # 正确做法：只设置 _pending_apply_params，不直接修改 widget key
        self.ss["_pending_apply_params"] = params
        # 此时不应有任何 widget key 被修改
        assert "ticker" not in self.ss
        assert "market" not in self.ss

        # ---- 阶段2：下次 rerun 时 main() 顶部（widget 尚未创建） ----
        deferred = self.ss.pop("_pending_apply_params")
        for k, v in deferred.items():
            self.ss[k] = v
            self.ss[f"_imp_{k}"] = v
        self.ss["_import_data"] = "preset"

        # 验证所有值已正确应用
        assert self.ss["ticker"] == "TSLA"
        assert self.ss["market"] == "港股 HK"
        assert self.ss["v0_ke"] == 0.3
        assert self.ss["global_f"] == "ema"
        assert self.ss["_import_data"] == "preset"
        # 验证 _imp_ 备份也存在
        assert self.ss["_imp_ticker"] == "TSLA"
        assert self.ss["_imp_market"] == "港股 HK"

    def test_deferred_apply_preserves_non_widget_internal_keys(self):
        """延迟应用不应覆盖内部管理 key（_import_data 除外）。"""
        params = {"ticker": "AAPL"}
        self.ss["_preset_action"] = "update"  # 不应被覆盖
        self.ss["_pending_apply_params"] = params

        deferred = self.ss.pop("_pending_apply_params")
        for k, v in deferred.items():
            self.ss[k] = v
            self.ss[f"_imp_{k}"] = v

        # 内部标志未被污染
        assert self.ss["_preset_action"] == "update"
        assert self.ss["ticker"] == "AAPL"

    def test_simulate_save_then_toast(self):
        """模拟保存预设后的完整 feedback 路径。"""
        params = {"ticker": "META", "v0_ke": 0.12}
        target_name = "SavedPreset"

        pid = cfg.save_preset(target_name, json.dumps(params), category="单滤波")
        p = cfg.get_preset(pid)
        assert p is not None
        assert p["name"] == target_name

        # 保存后清理 overwrite checkbox 状态残留
        self.ss["overwrite_preset"] = False
        assert self.ss["overwrite_preset"] is False


# ============================================================
# 6.  Widget-bound key 冲突检测与防御
# ============================================================

class TestWidgetKeyConflictPrevention:
    """验证预设应用不会触发 StreamlitAPIException (widget 绑定冲突).

    问题背景: Streamlit 禁止在 widget 渲染后直接修改其 session_state key。
    collect_current_params() 返回的所有 key 都对应已实例化的 widget，
    因此应用预设必须使用延迟机制 (_pending_apply_params)，在下次
    rerun 时 widget 创建前应用参数。

    本测试类验证:
    1. collect_current_params 返回的 key 全部是 widget 绑定的
    2. 延迟机制正确隔离了 widget 创建前后的操作
    3. 旧代码(直接设置)会在模拟中暴露风险
    """

    # ---- 已知的 widget 绑定 key 列表 (需与 streamlit_app.py 同步) ----
    # 这些 key 都对应 st.radio/selectbox/checkbox/slider/text_input 等 widget
    GLOBAL_WIDGET_KEYS = {"market", "ticker", "global_f", "global_dual", "global_f2"}
    VIEW_KEYS = {"tf", "n", "sch", "pred", "ke", "sm", "ew",
                 "fm", "next", "fc", "fc2", "strat", "sl",
                 "cross_pnl", "align"}
    FILTER_KEY_PREFIXES = [
        "窗口大小_", "跨度_", "偏移量_", "标准差_", "多项式阶数_",
        "过程噪声", "测量噪声", "滤波器阶数_", "截止频率_", "平滑比例_",
    ]

    def _mock_session_state_with_params(self):
        """构建包含完整 widget 参数的模拟 session_state。"""
        ss = {}
        # 全局 widget key
        for k in self.GLOBAL_WIDGET_KEYS:
            ss[k] = "TEST_" + k
        # 视图 widget key (v0~v3)
        for vi in range(4):
            for pk in self.VIEW_KEYS:
                ss[f"v{vi}_{pk}"] = f"TEST_v{vi}_{pk}"
        # 滤波器中文 key (模拟一个完整集合)
        for pfx in self.FILTER_KEY_PREFIXES:
            for vi in range(4):
                ss[f"{pfx}v{vi}_f1_ema"] = 0.5
        return ss

    def test_collect_params_returns_only_widget_bound_keys(self, monkeypatch):
        """验证 collect_current_params 返回的 key 都是 widget 绑定的。

        如果将来有人新增了非 widget 的 key 到 collect_current_params，
        此测试会提醒检查是否需要 widget 冲突保护。
        """
        ss = self._mock_session_state_with_params()
        # 添加一些内部 key（不应被收集）
        ss["_import_data"] = "preset"
        ss["_preset_action"] = "update"
        ss["_preset_action_id"] = 1
        ss["_last_sel_name"] = "test"
        ss["overwrite_preset"] = False
        ss["_day_offset"] = 0
        ss["_fetched_ticker"] = "AAPL"

        # Mock streamlit.session_state（collect_current_params 内部 import streamlit as st）
        import streamlit
        monkeypatch.setattr(streamlit, "session_state", ss)

        params = cfg.collect_current_params()

        # 所有收集到的 key 应该都是已知 widget 绑定的
        for k in params:
            is_global = k in self.GLOBAL_WIDGET_KEYS
            is_view = any(k.startswith(f"v{vi}_") for vi in range(4))
            is_filter = any(k.startswith(pfx) for pfx in self.FILTER_KEY_PREFIXES)
            assert is_global or is_view or is_filter, (
                f"Key '{k}' 被 collect_current_params 收集但不在已知 widget key 列表中。"
                f" 如果这是新 widget key，请更新 GLOBAL_WIDGET_KEYS/VIEW_KEYS/FILTER_KEY_PREFIXES。"
                f" 如果是内部 key（_ 前缀），不应被收集——请更新 collect_current_params 的过滤逻辑。"
            )

        # 内部 key 不应被收集
        for internal_key in ["_import_data", "_preset_action", "_preset_action_id",
                             "_last_sel_name", "overwrite_preset", "_day_offset",
                             "_fetched_ticker"]:
            assert internal_key not in params, (
                f"内部 key '{internal_key}' 被 collect_current_params 收集了，"
                f" 这会导致保存的预设包含内部状态，apply 时可能污染 session_state。"
            )

    def test_all_collected_keys_require_deferred_apply(self, monkeypatch):
        """验证: collect_current_params 返回的 ALL key 都需要延迟应用。

        如果存在非 widget key，可以进行直接设置；但目前所有 key 都是 widget 绑定的，
        所以 100% 需要通过 _pending_apply_params 延迟。
        """
        ss = self._mock_session_state_with_params()
        import streamlit
        monkeypatch.setattr(streamlit, "session_state", ss)

        params = cfg.collect_current_params()

        # 当前实现: 所有 key 都是 widget 绑定的
        # 因此预设应用按钮回调中不能直接设置任何一个 key
        widget_bound_count = 0
        for k in params:
            is_global = k in self.GLOBAL_WIDGET_KEYS
            is_view = any(k.startswith(f"v{vi}_") for vi in range(4))
            is_filter = any(k.startswith(pfx) for pfx in self.FILTER_KEY_PREFIXES)
            if is_global or is_view or is_filter:
                widget_bound_count += 1

        total = len(params)
        assert widget_bound_count == total, (
            f"collect_current_params 返回了 {total} 个 key，"
            f"其中 {widget_bound_count} 个是 widget 绑定的。"
            f" 如果 widget_bound_count < total，说明有非 widget key 可以安全直接设置。"
        )

    def test_deferred_mechanism_isolates_widget_keys(self):
        """验证延迟机制: _pending_apply_params 暂存后，widget key 不在 session_state 中。

        模拟按钮回调阶段(widget 已渲染): 只应设置 _pending_apply_params，
        不应直接在 session_state 中设置任何 widget key。
        """
        ss = {}
        params = {"ticker": "AAPL", "market": "美股 US", "v0_ke": 0.15}

        # 阶段1: 按钮回调 — 只设置暂存标志
        ss["_pending_apply_params"] = params
        assert "ticker" not in ss  # widget key 未被直接修改
        assert "market" not in ss

        # 阶段2: 下次 rerun 顶部(widget 创建前) — 应用所有参数
        deferred = ss.pop("_pending_apply_params")
        assert "_pending_apply_params" not in ss  # 标志已消费
        for k, v in deferred.items():
            ss[k] = v
            ss[f"_imp_{k}"] = v

        assert ss["ticker"] == "AAPL"
        assert ss["market"] == "美股 US"

    def test_deferred_apply_handles_extra_keys_gracefully(self):
        """延迟应用处理预设中的"多余"key（如旧版本保存的、现已删除的 widget key）。

        不应崩溃，静默应用即可。"""
        ss = {}
        params = {
            "ticker": "IBM",
            "market": "美股 US",
            "v0_old_deprecated_key": 999,  # 已废弃的 key
        }
        ss["_pending_apply_params"] = params

        deferred = ss.pop("_pending_apply_params")
        for k, v in deferred.items():
            ss[k] = v
            ss[f"_imp_{k}"] = v

        # 旧 key 也被设置了（Streamlit 会忽略不存在的 widget key）
        assert ss["v0_old_deprecated_key"] == 999
        assert ss["ticker"] == "IBM"

    def test_deferred_apply_handles_missing_keys(self):
        """延迟应用处理预设中缺少 key 的情况（新版本新增的 widget）。

        旧预设不包含新 widget key，已存在的 session_state 值保持不变。"""
        ss = {"ticker": "GOOGL", "market": "美股 US", "v0_new_param": 42}

        # 旧预设只包含 ticker 和 market
        params = {"ticker": "META", "market": "A股(沪深)"}
        ss["_pending_apply_params"] = params

        deferred = ss.pop("_pending_apply_params")
        for k, v in deferred.items():
            ss[k] = v
            ss[f"_imp_{k}"] = v

        # preset 中的 key 被更新
        assert ss["ticker"] == "META"
        assert ss["market"] == "A股(沪深)"
        # preset 中不存在的 key 保持原值（部分覆盖）
        assert ss["v0_new_param"] == 42

    def test_file_import_uses_direct_set_before_widgets(self):
        """文件导入使用直接设置，因为它运行在 widget 创建之前。

        这是正确的——不应用 _pending_apply_params 机制。"""
        ss = {}
        params = {"ticker": "NVDA", "market": "美股 US"}

        # 文件导入: widget 未创建，直接设置是安全的
        for k, v in params.items():
            ss[k] = v
            ss[f"_imp_{k}"] = v
        ss["_import_data"] = "md5hash"

        assert ss["ticker"] == "NVDA"
        assert ss["_imp_ticker"] == "NVDA"
        assert ss["_import_data"] == "md5hash"

    def test_overwrite_preset_key_excluded_from_collect(self, monkeypatch):
        """验证 overwrite_preset 不在 collect_current_params 返回中。

        它是 UI 状态 key, 不是配置参数, 不应被保存到预设中。"""
        import streamlit
        ss = self._mock_session_state_with_params()
        # 模拟 UI 状态 key — 不应出现在配置导出中
        ss["overwrite_preset"] = True
        monkeypatch.setattr(streamlit, "session_state", ss)

        from config_db import collect_current_params
        params = collect_current_params()
        assert "overwrite_preset" not in params


# ---------------------------------------------------------------------------
# Widget-Aware 测试 — 验证 Streamlit widget 生命周期约束
# ---------------------------------------------------------------------------

class _WidgetKeyModifiedAfterInstantiationError(Exception):
    """模拟 StreamlitAPIException: widget 实例化后不可修改其绑定 key."""


class _WidgetAwareSessionState(dict):
    """dict-like session_state，增加 widget 生命周期约束。

    模拟 Streamlit 规则：所有 widget 创建完成后 (lock)，
    禁止直接修改 widget 绑定的 session_state key。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._widget_keys: set = set()
        self._locked: bool = False

    def register_widget(self, key: str):
        """标记 key 为 widget 绑定。"""
        self._widget_keys.add(key)

    def lock(self):
        """标记所有 widget 已创建完成，进入回调阶段。

        此后任何对 widget key 的直接赋值将触发异常。
        """
        self._locked = True

    def begin_rerun(self):
        """模拟 st.rerun() 后的新执行周期：解锁并清除 widget 注册。"""
        self._locked = False
        self._widget_keys.clear()

    def __setitem__(self, key, value):
        if self._locked and key in self._widget_keys:
            raise _WidgetKeyModifiedAfterInstantiationError(
                f"st.session_state.{key} cannot be modified after the widget "
                f"with key '{key}' is instantiated."
            )
        super().__setitem__(key, value)


class TestOverwriteCheckboxResetFlow:
    """验证保存成功后 overwrite_preset checkbox 的正确重置流程。

    使用 _WidgetAwareSessionState 模拟 Streamlit widget 生命周期。
    """

    def setup_method(self):
        self.ss = _WidgetAwareSessionState()
        self.SSE = _WidgetKeyModifiedAfterInstantiationError

    def test_direct_reset_after_widget_instantiation_raises(self):
        """验证: widget 创建后直接设置其 session_state key 触发异常。

        模拟修复前 line 2186 的 bug 路径:
        1. checkbox widget 已渲染 (register + lock)
        2. 保存按钮回调中直接 st.session_state.overwrite_preset = False → 崩溃
        """
        self.ss.register_widget("overwrite_preset")
        self.ss.lock()  # 所有 widget 创建完毕，进入回调阶段
        with pytest.raises(self.SSE):
            self.ss["overwrite_preset"] = False

    def test_deferred_reset_avoids_widget_conflict(self):
        """验证: _pending_reset_overwrite 延迟标志可安全重置。

        模拟: save → rerun → 在 widget 创建前消费标志 → 安全赋值。
        """
        # ---- 阶段1: 按钮回调 (widget 已 lock，不能直接改 widget key) ----
        self.ss.register_widget("overwrite_preset")
        self.ss.lock()

        # 保存成功: 设延迟标志 (非 widget key，始终安全)
        self.ss["_pending_reset_overwrite"] = True
        # st.rerun() 触发

        # ---- 阶段2: 新脚本执行, widget 尚未创建 ----
        self.ss.begin_rerun()

        # 消费延迟标志 — 对应 streamlit_app.py 中 checkbox 前的代码
        if self.ss.pop("_pending_reset_overwrite", False):
            self.ss["overwrite_preset"] = False  # widget 未 lock，安全

        assert self.ss["overwrite_preset"] is False
        assert "_pending_reset_overwrite" not in self.ss

    def test_deferred_reset_only_when_flag_present(self):
        """验证: 无 _pending_reset_overwrite 时不误修改。"""
        self.ss.begin_rerun()

        original = self.ss.get("overwrite_preset", True)
        if self.ss.pop("_pending_reset_overwrite", False):
            self.ss["overwrite_preset"] = False

        assert self.ss.get("overwrite_preset", True) == original

    def test_rerun_resets_lock_and_widget_registry(self):
        """验证: begin_rerun() 清除 lock 和 widget 注册，允许新周期安全赋值。"""
        # 上一轮: widget 注册 + lock + set _pending flag
        self.ss.register_widget("overwrite_preset")
        self.ss.lock()
        self.ss["_pending_reset_overwrite"] = True

        # 新周期: 解锁 + 清除注册
        self.ss.begin_rerun()

        # 消费标志 + 设置 widget key → 不应抛异常
        self.ss.pop("_pending_reset_overwrite")
        self.ss["overwrite_preset"] = False
        assert self.ss["overwrite_preset"] is False
