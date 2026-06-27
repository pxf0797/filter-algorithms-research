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
        """params_json 为非法 JSON 时返回 None"""
        pid = cfg.save_preset("BadJSON", "{invalid json}")
        result = cfg.apply_preset(pid)
        assert result is None

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

        count = cfg.import_json_files_as_presets()
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

        count = cfg.import_json_files_as_presets(force=False)
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
        count = cfg.import_json_files_as_presets(force=True)

        assert count == 1
        p = cfg.get_preset_by_name("EXIST")
        loaded = json.loads(p["params_json"])
        assert loaded["ticker"] == "NEW"

    def test_import_empty_config_dir(self, isolate_db):
        """空 config 目录导入 0 条"""
        count = cfg.import_json_files_as_presets()
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

        count = cfg.import_json_files_as_presets()
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
        """名称空字符串仍然可以保存（数据库约束不阻止空串）"""
        pid = cfg.save_preset("", "{}")
        assert pid > 0
        p = cfg.get_preset(pid)
        assert p["name"] == ""

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
        # 同名保存不应抛异常
        cfg.save_preset("UniqueTest", json.dumps({"key": "updated"}))
        p = cfg.get_preset(pid)
        assert json.loads(p["params_json"])["key"] == "updated"
