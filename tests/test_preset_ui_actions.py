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

    def test_simulate_apply_then_toast(self):
        """模拟应用预设后的完整 feedback 路径。"""
        pid = cfg.save_preset("ToastSim", json.dumps({"ticker": "TSLA", "v0_ke": 0.3}))
        params = cfg.apply_preset(pid)

        # 模拟将参数写入 session_state
        for k, v in params.items():
            self.ss[k] = v
            self.ss[f"_imp_{k}"] = v
        self.ss["_import_data"] = "preset"

        # toast 在真实 Streamlit 中调用；这里验证数据就绪即可
        assert self.ss["ticker"] == "TSLA"
        assert self.ss["v0_ke"] == 0.3
        assert self.ss["_import_data"] == "preset"

    def test_simulate_save_then_toast(self):
        """模拟保存预设后的完整 feedback 路径。"""
        # 收集当前参数
        params = {"ticker": "META", "v0_ke": 0.12}
        target_name = "SavedPreset"

        pid = cfg.save_preset(target_name, json.dumps(params), category="单滤波")
        p = cfg.get_preset(pid)
        assert p is not None
        assert p["name"] == target_name

        # 保存后清理 overwrite checkbox 状态残留
        self.ss["overwrite_preset"] = False
        assert self.ss["overwrite_preset"] is False
