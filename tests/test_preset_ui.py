"""
test_preset_ui.py — 预设管理集成测试 (逻辑层)

直接测试 config_db 的 CRUD + 模拟 session_state 交互，验证预设管理的
完整业务流程。无 Streamlit 运行时依赖。
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ============================================================
# conftest 已将 streamlit/ 添加到 sys.path，但 streamlit 被 mock
# 后不再是 package，所以需要直接 import config_db
# 这里手工补路径以确保模块级导入成功
# ============================================================

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
    # 清理: 删除临时目录
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


# ============================================================
# 1. 完整生命周期
# ============================================================

class TestPresetLifecycle:
    """创建 -> 应用 -> 更新 -> 重命名 -> 删除"""

    def test_full_preset_lifecycle(self):
        """验证完整生命周期: 创建->应用->更新->重命名->删除"""
        params = {"market": "美股 US", "ticker": "AAPL", "global_f": "ema"}
        params_json = json.dumps(params, ensure_ascii=False)

        # 1) 创建预设
        pid = cfg.save_preset("MyPreset", params_json, description="测试", category="单滤波")
        assert pid > 0

        # 2) 读取确认
        p = cfg.get_preset(pid)
        assert p is not None
        assert p["name"] == "MyPreset"
        assert p["description"] == "测试"
        assert p["category"] == "单滤波"

        # 3) 应用预设 (解析 params_json)
        applied = cfg.apply_preset(pid)
        assert applied is not None
        assert applied["ticker"] == "AAPL"
        assert applied["market"] == "美股 US"
        assert applied["global_f"] == "ema"

        # 4) 更新参数 (同名保存)
        new_params = {"market": "港股 HK", "ticker": "2382", "global_f": "hma"}
        new_params_json = json.dumps(new_params, ensure_ascii=False)
        pid2 = cfg.save_preset("MyPreset", new_params_json, description="更新后", category="通用")
        assert pid2 == pid  # 同一条记录

        # 5) 重命名
        cfg.rename_preset(pid, "RenamedPreset")
        renamed = cfg.get_preset(pid)
        assert renamed["name"] == "RenamedPreset"

        # 6) 删除
        cfg.delete_preset(pid)
        assert cfg.get_preset(pid) is None


# ============================================================
# 2. 删除
# ============================================================

class TestDeletePreset:
    """删除后不应再出现在列表中"""

    def test_delete_preset_removes_from_list(self):
        """delete_preset 后 list_presets() 不再包含该项"""
        pid1 = cfg.save_preset("Keep", "{}")
        pid2 = cfg.save_preset("Remove", "{}")
        assert len(cfg.list_presets()) == 2

        cfg.delete_preset(pid2)
        names = {p["name"] for p in cfg.list_presets()}
        assert "Keep" in names
        assert "Remove" not in names

    def test_get_preset_after_delete(self):
        """删除后 get_preset 返回 None"""
        pid = cfg.save_preset("Temp", "{}")
        cfg.delete_preset(pid)
        assert cfg.get_preset(pid) is None

    def test_delete_nonexistent_does_not_raise(self):
        """删除不存在的 ID 不应抛出异常"""
        cfg.delete_preset(9999)  # should not raise


# ============================================================
# 3. 重命名
# ============================================================

class TestRenamePreset:
    """重命名后 list 反映新名称"""

    def test_rename_preset_updates_list(self):
        """重命名后 list_presets() 反映新名称，旧名称不存在"""
        pid = cfg.save_preset("OldName", "{}")
        cfg.rename_preset(pid, "NewName")

        names = {p["name"] for p in cfg.list_presets()}
        assert "NewName" in names
        assert "OldName" not in names

    def test_get_preset_reflects_new_name(self):
        """rename_preset 后 get_preset 返回新名称"""
        pid = cfg.save_preset("Before", "{}")
        cfg.rename_preset(pid, "After")
        p = cfg.get_preset(pid)
        assert p["name"] == "After"

    def test_get_preset_by_name_old_name_fails(self):
        """旧名称的 get_preset_by_name 返回 None"""
        pid = cfg.save_preset("OldName", "{}")
        cfg.rename_preset(pid, "NewName")
        assert cfg.get_preset_by_name("OldName") is None
        assert cfg.get_preset_by_name("NewName") is not None


# ============================================================
# 4. apply_preset 填充 session_state
# ============================================================

class TestApplyPresetSessionState:
    """apply_preset 返回正确的参数字典"""

    def test_apply_preset_populates_session_state(self):
        """apply_preset 返回可写入 session_state 的 dict"""
        params = {
            "market": "美股 US", "ticker": "AAPL",
            "v0_tf": "4h", "v0_n": 50, "v0_sch": "high",
            "global_f": "ema",
        }
        pid = cfg.save_preset("SessTest", json.dumps(params, ensure_ascii=False))

        result = cfg.apply_preset(pid)
        assert result is not None
        assert result["ticker"] == "AAPL"
        assert result["v0_tf"] == "4h"
        assert result["v0_n"] == 50

        # 验证可以写入 session_state
        for k, v in result.items():
            assert v == params[k]

    def test_apply_preset_empty_params(self):
        """params_json 为 {} 时返回空 dict"""
        pid = cfg.save_preset("Empty", "{}")
        result = cfg.apply_preset(pid)
        assert result == {}

    def test_apply_preset_invalid_json_returns_none(self):
        """非法 JSON 在 save_preset 阶段即被拒绝（P1-5 修复）。"""
        with pytest.raises(ValueError, match="有效的 JSON"):
            cfg.save_preset("BadJSON", "{invalid json}")

    def test_apply_nonexistent_preset_returns_none(self):
        """不存在的 preset_id 返回 None"""
        assert cfg.apply_preset(9999) is None


# ============================================================
# 5. 覆盖保存
# ============================================================

class TestSaveOverwritePreset:
    """同名保存触发 UPDATE 而非 INSERT"""

    def test_save_overwrite_preset(self):
        """覆盖保存后参数更新，preset_id 不变"""
        params_v1 = {"value": 1}
        pid = cfg.save_preset("OverwriteTest", json.dumps(params_v1))

        params_v2 = {"value": 2}
        pid2 = cfg.save_preset("OverwriteTest", json.dumps(params_v2))

        assert pid == pid2

        p = cfg.get_preset(pid)
        loaded = json.loads(p["params_json"])
        assert loaded["value"] == 2

    def test_overwrite_reduces_list_count(self):
        """同名保存不会增加列表条目数"""
        cfg.save_preset("A", "{}")
        cfg.save_preset("B", "{}")
        cfg.save_preset("A", '{"x": 1}')  # overwrite

        assert len(cfg.list_presets()) == 2

    def test_multiple_overwrites(self):
        """多次覆盖保存都更新同一条记录"""
        pid = cfg.save_preset("Multi", '{"v": 1}')
        for v in range(2, 6):
            pid2 = cfg.save_preset("Multi", json.dumps({"v": v}))
            assert pid2 == pid

        p = cfg.get_preset(pid)
        assert json.loads(p["params_json"])["v"] == 5


# ============================================================
# 6. JSON 文件导入
# ============================================================

class TestImportJsonAsPresets:
    """模拟 config/ 目录有 JSON 文件"""

    def test_import_json_as_presets(self, isolate_db):
        """import_json_files_as_presets 成功导入"""
        config_dir = isolate_db / "config"
        # 写入一个模拟 JSON
        (config_dir / "TEST_US.json").write_text(
            json.dumps({"ticker": "TEST", "market": "US"}, ensure_ascii=False)
        )

        count, _ = cfg.import_json_files_as_presets()
        assert count == 1

        presets = cfg.list_presets()
        assert any(p["name"] == "TEST_US" for p in presets)

    def test_import_respects_existing_names(self, isolate_db):
        """同名预设存在时不覆盖（force=False）"""
        config_dir = isolate_db / "config"
        (config_dir / "EXIST.json").write_text(
            json.dumps({"ticker": "OLD", "market": "HK"}, ensure_ascii=False)
        )

        # 先手动创建同名预设
        cfg.save_preset("EXIST", json.dumps({"manual": True}), category="单滤波")

        count, _ = cfg.import_json_files_as_presets(force=False)
        assert count == 0  # 跳过

        p = cfg.get_preset_by_name("EXIST")
        loaded = json.loads(p["params_json"])
        assert loaded.get("manual") is True  # 原值保留

    def test_import_force_overwrites(self, isolate_db):
        """force=True 时同名预设被覆盖"""
        config_dir = isolate_db / "config"
        (config_dir / "EXIST.json").write_text(
            json.dumps({"ticker": "NEW", "market": "US"}, ensure_ascii=False)
        )

        cfg.save_preset("EXIST", json.dumps({"manual": True}))
        count, _ = cfg.import_json_files_as_presets(force=True)

        assert count == 1
        p = cfg.get_preset_by_name("EXIST")
        loaded = json.loads(p["params_json"])
        assert loaded["ticker"] == "NEW"

    def test_import_empty_config_dir(self, isolate_db):
        """空 config 目录导入 0 条"""
        count, _ = cfg.import_json_files_as_presets()
        assert count == 0

    def test_import_categorizes_by_suffix(self, isolate_db):
        """_DP 后缀分类为双滤波, _QS 为快速, 其余为单滤波"""
        config_dir = isolate_db / "config"
        (config_dir / "A_DP.json").write_text("{}")
        (config_dir / "B_QS.json").write_text("{}")
        (config_dir / "C.json").write_text("{}")

        cfg.import_json_files_as_presets()
        presets = {p["name"]: p["category"] for p in cfg.list_presets()}
        assert presets["A_DP"] == "双滤波"
        assert presets["B_QS"] == "快速"
        assert presets["C"] == "单滤波"

    def test_import_bad_json_skipped(self, isolate_db):
        """非法的 JSON 文件被跳过"""
        config_dir = isolate_db / "config"
        (config_dir / "BAD.json").write_text("this is not json")

        count, _ = cfg.import_json_files_as_presets()
        assert count == 0


# ============================================================
# 7. list_presets 初始为空
# ============================================================

class TestPresetListEmpty:
    """空 DB 时 list_presets() 返回 []"""

    def test_preset_list_empty_initially(self):
        """未添加任何预设时 list 为空"""
        assert cfg.list_presets() == []

    def test_list_with_category(self):
        """按分类过滤（空分类返回 []）"""
        assert cfg.list_presets(category="不存在") == []


# ============================================================
# 8. 同名触发 UPDATE 而非 INSERT
# ============================================================

class TestDuplicatePresetName:
    """同名保存触发 UPDATE"""

    def test_duplicate_preset_name_updates(self):
        """同名预设第二次保存触发 UPDATE，preset_id 不变"""
        pid1 = cfg.save_preset("Dup", '{"v": 1}')
        pid2 = cfg.save_preset("Dup", '{"v": 2}')
        assert pid1 == pid2

    def test_duplicate_name_count(self):
        """同名多次保存不增加总数"""
        for i in range(5):
            cfg.save_preset("Dup", json.dumps({"i": i}))
        assert len(cfg.list_presets()) == 1

    def test_rename_into_conflicting_name_succeeds(self):
        """rename 可以重命名为已删除的旧名称（同名列不存在）"""
        pid = cfg.save_preset("Original", "{}")
        cfg.delete_preset(pid)
        # 再创建同名
        pid2 = cfg.save_preset("Original", "{}")
        assert pid2 != pid


# ============================================================
# 9. 边界条件
# ============================================================

class TestPresetEdgeCases:
    """边界条件和异常场景"""

    def test_preset_name_empty_string(self):
        """名称空字符串被拒绝（P1-5 修复后入口校验空名称）。"""
        with pytest.raises(ValueError, match="名称不能为空"):
            cfg.save_preset("", "{}")

    def test_large_params_json(self):
        """大量参数的 JSON 可以正常保存和读取"""
        big_params = {f"param_{i}": f"value_{i}" for i in range(1000)}
        pid = cfg.save_preset("BigParams", json.dumps(big_params, ensure_ascii=False))
        p = cfg.get_preset(pid)
        loaded = json.loads(p["params_json"])
        assert len(loaded) == 1000
        assert loaded["param_0"] == "value_0"
        assert loaded["param_999"] == "value_999"

    def test_list_presets_sorted_by_category_then_name(self):
        """list_presets() 按 category 排序再按 name 排序"""
        cfg.save_preset("B", "{}", category="z")
        cfg.save_preset("A", "{}", category="z")
        cfg.save_preset("Z", "{}", category="a")

        presets = cfg.list_presets()
        assert presets[0]["name"] == "Z"  # category "a" first
        assert presets[1]["name"] == "A"  # category "z", then "A"
        assert presets[2]["name"] == "B"

    def test_uniqueness_constraint_violation_triggers_update(self):
        """违反 UNIQUE 约束时触发 UPDATE，不引发异常"""
        pid = cfg.save_preset("UniqueTest", json.dumps({"key": "original"}))


# ============================================================
# 10. Selectbox 刷新模式 (Streamlit session_state 模拟)
# ============================================================

class TestSelectboxRefresh:
    """验证 hash-based 动态 key 模式：选项数据变了，key 自动变，widget 自然重置"""

    def _render_selectbox(self, presets):
        """模拟 selectbox 渲染 (hash key 模式)。"""
        import hashlib
        preset_names = ["(不选择)"] + [
            f"[{p['category']}] {p['name']}" for p in presets]
        # 基于选项内容生成 key — 数据变了 key 就变
        key_hash = hashlib.md5("|".join(preset_names).encode()).hexdigest()[:8]
        # 新 key → Streamlit 视为新 widget → 默认选中第0项
        return preset_names[0], preset_names, key_hash

    def test_delete_changes_hash_resets_widget(self):
        """删除后选项列表变了，hash 变化，widget 自动重置到 (不选择)"""
        cfg.save_preset("ToDelete", json.dumps({"x": 1}))
        before = self._render_selectbox(cfg.list_presets())

        p = cfg.get_preset_by_name("ToDelete")
        cfg.delete_preset(p["preset_id"])
        after = self._render_selectbox(cfg.list_presets())

        # hash 变了 → 新 widget
        assert before[2] != after[2]
        # 新 widget 默认选中第0项
        assert after[0] == "(不选择)"
        assert "[通用] ToDelete" not in after[1]

    def test_rename_changes_hash(self):
        """重命名后选项列表变了，hash 变化"""
        cfg.save_preset("OldName", json.dumps({"x": 1}))
        before = self._render_selectbox(cfg.list_presets())

        p = cfg.get_preset_by_name("OldName")
        cfg.rename_preset(p["preset_id"], "NewName")
        after = self._render_selectbox(cfg.list_presets())

        assert before[2] != after[2]
        assert "[通用] NewName" in after[1]
        assert "[通用] OldName" not in after[1]

    def test_save_new_changes_hash(self):
        """保存新预设后选项列表变了，hash 变化"""
        before = self._render_selectbox(cfg.list_presets())
        cfg.save_preset("Fresh", json.dumps({"a": 1}))
        after = self._render_selectbox(cfg.list_presets())

        assert before[2] != after[2]
        assert "[通用] Fresh" in after[1]

    def test_no_change_same_hash(self):
        """没有增删改操作时 hash 保持不变"""
        cfg.save_preset("A", json.dumps({"x": 1}))
        cfg.save_preset("B", json.dumps({"x": 2}))
        r1 = self._render_selectbox(cfg.list_presets())
        r2 = self._render_selectbox(cfg.list_presets())
        assert r1[2] == r2[2]  # hash 不变 — widget 状态保持

    def test_apply_preset_does_not_change_hash(self):
        """应用预设不改变数据，hash 不变"""
        pid = cfg.save_preset("MyPreset", json.dumps({"v0_ke": 0.15}))
        before = self._render_selectbox(cfg.list_presets())
        cfg.apply_preset(pid)  # 不改变 DB
        after = self._render_selectbox(cfg.list_presets())
        assert before[2] == after[2]  # 纯读操作，hash 不变


# ============================================================
# 11. 增删改查连续操作
# ============================================================

class TestPresetCrudCycle:
    """模拟用户连续操作: 创建→修改→删除→重建 的完整流程"""

    def test_create_modify_delete_recreate(self):
        """创建预设 → 更新参数 → 删除 → 用同名重建"""
        # 创建
        pid = cfg.save_preset("Cycle", json.dumps({"step": 1}))
        assert cfg.get_preset(pid) is not None
        assert json.loads(cfg.get_preset(pid)["params_json"])["step"] == 1

        # 修改(更新)
        cfg.save_preset("Cycle", json.dumps({"step": 2}))
        assert json.loads(cfg.get_preset(pid)["params_json"])["step"] == 2

        # 删除
        cfg.delete_preset(pid)
        assert cfg.get_preset(pid) is None

        # 同名重建
        new_pid = cfg.save_preset("Cycle", json.dumps({"step": 3}))
        assert new_pid != pid  # 新 ID
        assert json.loads(cfg.get_preset(new_pid)["params_json"])["step"] == 3
        assert len(cfg.list_presets()) == 1

    def test_rename_then_recreate_original_name(self):
        """重命名后立即用旧名创建新预设"""
        pid = cfg.save_preset("Alpha", json.dumps({"v": 1}))
        cfg.rename_preset(pid, "Beta")

        # 旧名可用
        new_pid = cfg.save_preset("Alpha", json.dumps({"v": 2}))
        assert new_pid != pid
        assert cfg.get_preset_by_name("Alpha") is not None
        assert cfg.get_preset_by_name("Beta") is not None
        assert len(cfg.list_presets()) == 2

    def test_multiple_rapid_renames(self):
        """连续多次重命名"""
        pid = cfg.save_preset("R1", json.dumps({"x": 0}))
        for name in ["R2", "R3", "R4"]:
            cfg.rename_preset(pid, name)
            assert cfg.get_preset_by_name(name) is not None

        assert cfg.get_preset_by_name("R1") is None
        assert cfg.get_preset_by_name("R4") is not None

    def test_delete_all_then_reimport(self):
        """删除全部预设后重新从 JSON 导入"""
        cfg.save_preset("A", json.dumps({"x": 1}))
        cfg.save_preset("B", json.dumps({"x": 2}))
        assert len(cfg.list_presets()) == 2

        for p in cfg.list_presets():
            cfg.delete_preset(p["preset_id"])
        assert len(cfg.list_presets()) == 0

        # 创建模拟 JSON 文件来"重新导入"
        (cfg._CONFIG_DIR / "C_US.json").write_text(
            json.dumps({"market": "美股 US", "ticker": "C"}))
        n, _ = cfg.import_json_files_as_presets()
        assert n == 1
        presets = cfg.list_presets()
        assert len(presets) == 1
        assert presets[0]["name"] == "C_US"

    def test_overwrite_preserves_id(self):
        """覆盖更新保持 preset_id 不变"""
        pid = cfg.save_preset("Stable", json.dumps({"gen": 1}))
        cfg.save_preset("Stable", json.dumps({"gen": 2}))
        cfg.save_preset("Stable", json.dumps({"gen": 3}))
        p = cfg.get_preset(pid)
        assert p is not None
        assert json.loads(p["params_json"])["gen"] == 3
        assert len(cfg.list_presets()) == 1  # 没创建重复


# ============================================================
# 12. Session State 边界情况
# ============================================================

class TestSessionStateEdgeCases:
    """Streamlit session_state 的各种边缘情况"""

    def setup_method(self):
        import streamlit as st
        st.session_state = {}

    def _ss(self):
        import streamlit as st
        return st.session_state

    def test_empty_session_state_collect(self):
        params = cfg.collect_current_params()
        assert isinstance(params, dict)
        assert len(params) == 0

    def test_partial_global_keys(self):
        ss = self._ss()
        ss["market"] = "港股 HK"
        ss["ticker"] = "0700"
        ss["some_random"] = "junk"
        params = cfg.collect_current_params()
        assert params["market"] == "港股 HK"
        assert params["ticker"] == "0700"
        assert "some_random" not in params

    def test_collect_all_four_views(self):
        ss = self._ss()
        for vi in range(4):
            for pk in ["tf", "n", "sch", "pred", "ke", "sm", "ew",
                        "fm", "next", "fc", "fc2", "strat", "sl",
                        "cross_pnl", "align"]:
                ss[f"v{vi}_{pk}"] = f"val_{vi}_{pk}"
        params = cfg.collect_current_params()
        for vi in range(4):
            assert params[f"v{vi}_tf"] == f"val_{vi}_tf"

    def test_collect_chinese_filter_keys(self):
        ss = self._ss()
        ss["窗口大小_v0_f1_savgol"] = 21
        ss["多项式阶数_v0_f1_savgol"] = 3
        ss["跨度_v0_f2_ema"] = 15
        ss["过程噪声_v1_f1_kalman"] = 0.01
        ss["测量噪声_v1_f1_kalman"] = 0.1
        ss["滤波器阶数_v2_f1_butterworth"] = 4
        ss["截止频率_v2_f1_butterworth"] = 0.05
        ss["平滑比例_v3_f1_lowess"] = 0.3
        ss["标准差_v3_f1_gaussian"] = 1.5
        ss["偏移量_v3_f1_alma"] = 0.85
        params = cfg.collect_current_params()
        assert params["窗口大小_v0_f1_savgol"] == 21
        assert params["过程噪声_v1_f1_kalman"] == 0.01
        assert params["截止频率_v2_f1_butterworth"] == 0.05
        assert params["平滑比例_v3_f1_lowess"] == 0.3
        assert params["标准差_v3_f1_gaussian"] == 1.5
        assert params["偏移量_v3_f1_alma"] == 0.85

    def test_import_data_flag_preserved(self):
        ss = self._ss()
        pid = cfg.save_preset("FlagTest", json.dumps({"v0_ke": 0.2}))
        params = cfg.apply_preset(pid)
        for k, v in params.items():
            ss[k] = v
            ss[f"_imp_{k}"] = v
        ss["_import_data"] = "preset"
        assert ss["v0_ke"] == 0.2
        assert ss["_imp_v0_ke"] == 0.2
        assert ss["_import_data"] == "preset"

    def test_imp_backup_not_overwritten(self):
        ss = self._ss()
        params = {"v0_ke": 0.08, "v0_sm": 0.03}
        for k, v in params.items():
            ss[k] = v
            ss[f"_imp_{k}"] = v
        ss["v0_ke"] = 0.15  # widget 默认值覆盖
        assert ss["_imp_v0_ke"] == 0.08  # _imp_ 备份不受影响

    def test_ticker_switch_different_presets(self):
        ss = self._ss()
        cfg.save_preset("AAPL_base", json.dumps({"ticker": "AAPL", "v0_ke": 0.1}))
        cfg.save_preset("TSLA_base", json.dumps({"ticker": "TSLA", "v0_ke": 0.3}))
        aapl = cfg.apply_preset(cfg.get_preset_by_name("AAPL_base")["preset_id"])
        for k, v in aapl.items():
            ss[k] = v
        assert ss["v0_ke"] == 0.1
        ss.clear()
        tsla = cfg.apply_preset(cfg.get_preset_by_name("TSLA_base")["preset_id"])
        for k, v in tsla.items():
            ss[k] = v
        assert ss["v0_ke"] == 0.3


# ============================================================
# 13. 大规模数据
# ============================================================

class TestLargeScale:
    """大量预设和参数时的行为"""

    def test_many_presets_list_and_query(self):
        """100 个预设的列表和查询性能"""
        for i in range(100):
            cfg.save_preset(f"Preset{i:03d}",
                            json.dumps({"index": i}),
                            category=["单滤波", "双滤波", "快速"][i % 3])

        presets = cfg.list_presets()
        assert len(presets) == 100
        # 按分类过滤
        single = cfg.list_presets(category="单滤波")
        assert len(single) == 34  # 0,3,6,...,99 = 34 个

        # 按名查询
        p = cfg.get_preset_by_name("Preset042")
        assert p is not None
        assert json.loads(p["params_json"])["index"] == 42

    def test_large_params_json(self):
        """1000 个 key 的大 JSON 配置"""
        big = {f"key_{i}": f"value_{i}" for i in range(1000)}
        pid = cfg.save_preset("BigConfig", json.dumps(big))
        loaded = cfg.apply_preset(pid)
        assert len(loaded) == 1000
        assert loaded["key_999"] == "value_999"

    def test_bulk_delete_and_recreate(self):
        """批量删除后批量重建"""
        ids = [cfg.save_preset(f"Bulk{i}", json.dumps({"n": i})) for i in range(50)]
        assert len(cfg.list_presets()) == 50

        for pid in ids:
            cfg.delete_preset(pid)
        assert len(cfg.list_presets()) == 0

        # 重建
        for i in range(50):
            cfg.save_preset(f"Bulk{i}", json.dumps({"n": i * 10}))
        assert len(cfg.list_presets()) == 50
        # 同名覆盖更新
        new_pid = cfg.save_preset("Bulk0", json.dumps({"n": 999}))
        p = cfg.get_preset(new_pid)
        assert p is not None
        assert json.loads(p["params_json"])["n"] == 999


# ============================================================
# 14. save_preset 分类保留
# ============================================================

class TestSavePresetWithCategory:
    """save_preset 覆盖时保留原分类，新建时使用默认分类 '通用'"""

    def test_overwrite_explicit_category_preserved(self):
        """覆盖保存时显式传相同分类，分类不变"""
        pid = cfg.save_preset("CatPreset", json.dumps({"v": 1}), category="双滤波")
        # 覆盖保存，传相同分类
        cfg.save_preset("CatPreset", json.dumps({"v": 2}), category="双滤波")
        p = cfg.get_preset(pid)
        assert p["category"] == "双滤波"
        assert json.loads(p["params_json"])["v"] == 2

    def test_new_preset_uses_default_category(self):
        """新建预设不传分类时使用默认分类 '通用'"""
        pid = cfg.save_preset("DefaultCat", json.dumps({"x": 1}))
        p = cfg.get_preset(pid)
        assert p["category"] == "通用"

    def test_overwrite_with_different_category_updates(self):
        """覆盖保存时传不同分类，分类应更新为新值"""
        pid = cfg.save_preset("ChangeCat", json.dumps({"v": 1}), category="单滤波")
        cfg.save_preset("ChangeCat", json.dumps({"v": 2}), category="快速")
        p = cfg.get_preset(pid)
        assert p["category"] == "快速"
        assert json.loads(p["params_json"])["v"] == 2


# ============================================================
# 15. collect_current_params 完整性
# ============================================================

class TestCollectParamsCompleteness:
    """验证 collect_current_params 收集所有必要 key，不遗漏不混杂"""

    def setup_method(self):
        import streamlit as st
        st.session_state = {}

    def _ss(self):
        import streamlit as st
        return st.session_state

    def test_collects_all_global_keys(self):
        """收集所有全局参数 key"""
        ss = self._ss()
        global_keys = ["market", "ticker", "global_f", "global_dual", "global_f2"]
        for k in global_keys:
            ss[k] = f"val_{k}"
        params = cfg.collect_current_params()
        for k in global_keys:
            assert k in params, f"缺少全局 key: {k}"
            assert params[k] == f"val_{k}"

    def test_collects_all_view_keys_for_four_views(self):
        """收集 v0~v3 全部视图参数的完整 pk 集合"""
        ss = self._ss()
        view_pks = ["tf", "n", "sch", "pred", "ke", "sm", "ew",
                    "fm", "next", "fc", "fc2", "strat", "sl",
                    "cross_pnl", "align"]
        for vi in range(4):
            for pk in view_pks:
                ss[f"v{vi}_{pk}"] = f"val_{vi}_{pk}"
        params = cfg.collect_current_params()
        for vi in range(4):
            for pk in view_pks:
                k = f"v{vi}_{pk}"
                assert k in params, f"缺少视图 key: {k}"
                assert params[k] == f"val_{vi}_{pk}"

    def test_collects_all_chinese_filter_prefixes(self):
        """收集所有中文滤波器参数前缀"""
        ss = self._ss()
        filter_keys = {
            "窗口大小_test": 10,
            "跨度_test": 5,
            "偏移量_test": 0.5,
            "标准差_test": 2.0,
            "多项式阶数_test": 3,
            "过程噪声": 0.01,
            "测量噪声": 0.1,
            "滤波器阶数_test": 4,
            "截止频率_test": 0.3,
            "平滑比例_test": 0.8,
        }
        for k, v in filter_keys.items():
            ss[k] = v
        params = cfg.collect_current_params()
        for k, v in filter_keys.items():
            assert k in params, f"缺少滤波器 key: {k}"
            assert params[k] == v

    def test_unrelated_keys_not_collected(self):
        """session_state 中无关 key 不被收集"""
        ss = self._ss()
        ss["market"] = "US"
        ss["ticker"] = "AAPL"
        ss["_internal"] = "secret"
        ss["sidebar_state"] = "open"
        params = cfg.collect_current_params()
        assert "market" in params
        assert "ticker" in params
        assert "_internal" not in params
        assert "sidebar_state" not in params


# ============================================================
# 16. 通过 preset_id 区分不同预设
# ============================================================

class TestPresetMapLookup:
    """验证通过 preset_id 精确区分不同分类/名称的预设"""

    def test_different_presets_distinct_by_id(self):
        """不同预设拥有不同 preset_id，可通过 ID 精确查找"""
        pid1 = cfg.save_preset("AAPL_US", json.dumps({"ticker": "AAPL"}),
                               category="单滤波")
        pid2 = cfg.save_preset("TSLA_US", json.dumps({"ticker": "TSLA"}),
                               category="双滤波")

        p1 = cfg.get_preset(pid1)
        p2 = cfg.get_preset(pid2)
        assert p1["name"] == "AAPL_US"
        assert p1["category"] == "单滤波"
        assert p2["name"] == "TSLA_US"
        assert p2["category"] == "双滤波"
        assert pid1 != pid2

    def test_list_presets_ordered_by_category_then_name(self):
        """list_presets 按分类排序，同分类按名称排序"""
        cfg.save_preset("Z", json.dumps({}), category="快速")
        cfg.save_preset("A", json.dumps({}), category="双滤波")
        cfg.save_preset("M", json.dumps({}), category="快速")

        presets = cfg.list_presets()
        # 按 category 排序: 双滤波 < 快速, 同 category 内按 name 排序
        assert [p["category"] for p in presets] == ["双滤波", "快速", "快速"]
        assert [p["name"] for p in presets] == ["A", "M", "Z"]

    def test_same_name_overwrites_not_creates_duplicate(self):
        """同名预设触发 UPSERT，不创建重复记录（UNIQUE 约束）"""
        pid1 = cfg.save_preset("SameName", json.dumps({"v": 1}), category="单滤波")
        # 同名覆盖
        pid2 = cfg.save_preset("SameName", json.dumps({"v": 2}), category="双滤波")
        assert pid1 == pid2
        p = cfg.get_preset(pid2)
        assert p["category"] == "双滤波"  # category 被覆盖为新值
        assert len(cfg.list_presets()) == 1

    def test_get_preset_by_name_retrieves_unique(self):
        """get_preset_by_name 精确返回唯一匹配（名称唯一）"""
        cfg.save_preset("Unique", json.dumps({"a": 1}))
        p = cfg.get_preset_by_name("Unique")
        assert p is not None
        assert p["name"] == "Unique"
        assert cfg.get_preset_by_name("Nonexistent") is None
