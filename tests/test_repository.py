"""
tests/test_repository.py — 测试 Repository 模式抽象层

验证 BaseRepository 接口契约与 PresetRepository 具体实现。
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def repo_with_db():
    """创建临时 config.db，初始化表，返回 PresetRepository 实例。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "config.db"
        with patch("config_db._CONFIG_DB_PATH", db_path):
            import data.config_db
            config_db.init_config_tables()
            from shared.repository import PresetRepository
            yield PresetRepository()


@pytest.fixture
def sample_params():
    return json.dumps({"market": "US", "ticker": "AAPL", "sma": 20})


# ═══════════════════════════════════════════════════════════════════════════
# 1. TestBaseRepository — 抽象接口契约
# ═══════════════════════════════════════════════════════════════════════════

class TestBaseRepository:
    """验证 BaseRepository 是合法的 ABC 抽象类。"""

    def test_cannot_instantiate_abstract(self):
        """直接实例化抽象类应抛出 TypeError。"""
        from shared.repository import BaseRepository
        with pytest.raises(TypeError, match="abstract"):
            BaseRepository()  # type: ignore[abstract]

    def test_concrete_subclass_instantiates(self):
        """实现所有抽象方法的子类可实例化。"""
        from shared.repository import BaseRepository

        class Impl(BaseRepository[int]):
            def get_by_id(self, id):
                return id

            def get_all(self):
                return [1, 2, 3]

            def save(self, entity):
                return 42

        repo = Impl()
        assert repo.get_by_id(5) == 5
        assert repo.get_all() == [1, 2, 3]
        assert repo.save(0) == 42

    def test_missing_abstract_raises(self):
        """缺少任一抽象方法的子类无法实例化。"""
        from shared.repository import BaseRepository

        class Incomplete(BaseRepository[int]):
            def get_by_id(self, id):
                return id

            # 缺少 get_all 和 save

        with pytest.raises(TypeError, match="abstract"):
            Incomplete()  # type: ignore[abstract]


# ═══════════════════════════════════════════════════════════════════════════
# 2. TestPresetRepository — 预设 CRUD 操作
# ═══════════════════════════════════════════════════════════════════════════

class TestPresetRepository:
    """验证 PresetRepository 通过 BaseRepository 接口与扩展方法正确工作。"""

    def test_save_and_get_by_id(self, repo_with_db, sample_params):
        """save 返回 preset_id，get_by_id 可获取。"""
        pid = repo_with_db.save({
            "name": "algo1",
            "params_json": sample_params,
            "description": "test desc",
            "category": "单滤波",
        })
        assert isinstance(pid, int)
        assert pid > 0

        preset = repo_with_db.get_by_id(pid)
        assert preset is not None
        assert preset["name"] == "algo1"
        assert preset["description"] == "test desc"
        assert preset["category"] == "单滤波"
        assert preset["params_json"] == sample_params

    def test_get_by_id_not_found(self, repo_with_db):
        """不存在时返回 None。"""
        assert repo_with_db.get_by_id(99999) is None

    def test_get_all(self, repo_with_db, sample_params):
        """返回全部预设列表。"""
        repo_with_db.save({"name": "a", "params_json": sample_params})
        repo_with_db.save({"name": "b", "params_json": sample_params})
        all_presets = repo_with_db.get_all()
        assert len(all_presets) == 2
        names = {p["name"] for p in all_presets}
        assert names == {"a", "b"}

    def test_get_all_empty(self, repo_with_db):
        """无数据时返回空列表。"""
        assert repo_with_db.get_all() == []

    def test_save_upsert(self, repo_with_db, sample_params):
        """同名 save 更新已有预设，返回相同 ID。"""
        pid1 = repo_with_db.save({
            "name": "upsert_test",
            "params_json": sample_params,
            "category": "单滤波",
        })
        pid2 = repo_with_db.save({
            "name": "upsert_test",
            "params_json": json.dumps({"updated": True}),
            "category": "双滤波",
        })
        assert pid1 == pid2

        p = repo_with_db.get_by_id(pid2)
        assert json.loads(p["params_json"]) == {"updated": True}
        assert p["category"] == "双滤波"

    def test_save_default_category(self, repo_with_db, sample_params):
        """未指定 category 时默认为 '通用'。"""
        pid = repo_with_db.save({"name": "default_cat", "params_json": sample_params})
        p = repo_with_db.get_by_id(pid)
        assert p["category"] == "通用"

    def test_save_empty_description(self, repo_with_db, sample_params):
        """未指定 description 时默认为空字符串。"""
        pid = repo_with_db.save({"name": "no_desc", "params_json": sample_params})
        p = repo_with_db.get_by_id(pid)
        assert p["description"] == ""

    def test_save_empty_name_raises(self, repo_with_db):
        """空名称抛出 ValueError（透传 config_db 的校验）。"""
        with pytest.raises(ValueError, match="名称不能为空"):
            repo_with_db.save({"name": "", "params_json": "{}"})

    def test_save_invalid_json_raises(self, repo_with_db):
        """无效 JSON 抛出 ValueError。"""
        with pytest.raises(ValueError, match="有效的 JSON"):
            repo_with_db.save({"name": "bad", "params_json": "not json"})


# ═══════════════════════════════════════════════════════════════════════════
# 3. TestPresetRepositoryExtended — 预设特有方法
# ═══════════════════════════════════════════════════════════════════════════

class TestPresetRepositoryExtended:
    """验证超出 BaseRepository 的领域方法。"""

    def test_get_by_name_found(self, repo_with_db, sample_params):
        """按名称查找成功。"""
        repo_with_db.save({"name": "by_name_test", "params_json": sample_params,
                           "category": "快速"})
        p = repo_with_db.get_by_name("by_name_test")
        assert p is not None
        assert p["name"] == "by_name_test"
        assert p["category"] == "快速"

    def test_get_by_name_not_found(self, repo_with_db):
        """名称不存在返回 None。"""
        assert repo_with_db.get_by_name("no_such") is None

    def test_list_by_category(self, repo_with_db, sample_params):
        """按分类过滤。"""
        repo_with_db.save({"name": "p1", "params_json": sample_params, "category": "单滤波"})
        repo_with_db.save({"name": "p2", "params_json": sample_params, "category": "双滤波"})
        repo_with_db.save({"name": "p3", "params_json": sample_params, "category": "单滤波"})

        filtered = repo_with_db.list_by_category("单滤波")
        assert len(filtered) == 2
        assert all(p["category"] == "单滤波" for p in filtered)

    def test_delete_existing(self, repo_with_db, sample_params):
        """删除存在预设返回 True，之后查不到。"""
        pid = repo_with_db.save({"name": "del_test", "params_json": sample_params})
        assert repo_with_db.delete(pid) is True
        assert repo_with_db.get_by_id(pid) is None

    def test_delete_nonexistent(self, repo_with_db):
        """删除不存在预设返回 False。"""
        assert repo_with_db.delete(99999) is False

    def test_rename_success(self, repo_with_db, sample_params):
        """重命名成功返回新名称。"""
        pid = repo_with_db.save({"name": "old_name", "params_json": sample_params})
        result = repo_with_db.rename(pid, "new_name")
        assert result == "new_name"

        p = repo_with_db.get_by_id(pid)
        assert p["name"] == "new_name"

    def test_rename_conflict_returns_none(self, repo_with_db, sample_params):
        """重命名冲突返回 None，原名称不变。"""
        repo_with_db.save({"name": "existing", "params_json": sample_params})
        pid2 = repo_with_db.save({"name": "to_rename", "params_json": sample_params})

        result = repo_with_db.rename(pid2, "existing")
        assert result is None
        p = repo_with_db.get_by_id(pid2)
        assert p["name"] == "to_rename"

    def test_rename_empty_string_returns_none(self, repo_with_db, sample_params):
        """空字符串重命名返回 None。"""
        pid = repo_with_db.save({"name": "valid", "params_json": sample_params})
        assert repo_with_db.rename(pid, "") is None

    def test_rename_nonexistent_returns_none(self, repo_with_db):
        """不存在的 preset 重命名返回 None。"""
        assert repo_with_db.rename(99999, "anything") is None

    def test_apply_returns_parsed_dict(self, repo_with_db):
        """apply 返回解析后的参数 dict。"""
        params = {"ma": 10, "std": 2.5}
        pid = repo_with_db.save({"name": "apply_test", "params_json": json.dumps(params)})
        result = repo_with_db.apply(pid)
        assert result == params

    def test_apply_nonexistent_returns_none(self, repo_with_db):
        """不存在的 preset 返回 None。"""
        assert repo_with_db.apply(99999) is None


# ═══════════════════════════════════════════════════════════════════════════
# 4. TestPresetRepositoryIsolation — 数据隔离与兼容性
# ═══════════════════════════════════════════════════════════════════════════

class TestPresetRepositoryIsolation:
    """验证 Repository 不与 config_db 直接函数调用产生数据隔离问题。"""

    def test_repo_and_raw_share_data(self, repo_with_db, sample_params):
        """Repository 与原始 config_db 函数共享同一数据库。"""
        import data.config_db

        # 通过 repo 写入
        pid = repo_with_db.save({"name": "shared_test", "params_json": sample_params,
                                 "category": "测试"})

        # 通过原始函数读取
        p = config_db.get_preset(pid)
        assert p is not None
        assert p["name"] == "shared_test"
        assert p["category"] == "测试"

        # 通过原始函数写入
        pid2 = config_db.save_preset("from_raw", sample_params, category="手动")
        p2 = repo_with_db.get_by_id(pid2)
        assert p2 is not None
        assert p2["name"] == "from_raw"

    def test_entity_keys_match_preset_columns(self, repo_with_db, sample_params):
        """save 产生的行包含 config_presets 全部列。"""
        pid = repo_with_db.save({
            "name": "full_entity",
            "params_json": sample_params,
            "description": "A full entity",
            "category": "快速",
        })
        p = repo_with_db.get_by_id(pid)
        expected_keys = {"preset_id", "name", "description", "category",
                         "params_json", "created_at", "updated_at"}
        assert set(p.keys()) == expected_keys
