"""
filter_app/services/repository.py — 轻量 Repository 模式

不引入 ORM，保持原生 SQL。为预设管理提供面向对象的数据访问接口。
"""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar, Optional, List, Dict, Any

from data.config_db import (
    list_presets,
    get_preset,
    get_preset_by_name,
    save_preset,
    delete_preset,
    rename_preset,
    apply_preset,
)

T = TypeVar("T")


class BaseRepository(ABC, Generic[T]):
    """轻量 Repository 基类 — 不引入 ORM, 保持原生 SQL"""

    @abstractmethod
    def get_by_id(self, id: int) -> Optional[T]:
        """按主键获取单个实体"""

    @abstractmethod
    def get_all(self) -> list[T]:
        """获取全部实体"""

    @abstractmethod
    def save(self, entity: T) -> int:
        """保存实体，返回主键"""


# — Preset 是 Dict[str, Any]，与 config_db 的行 dict 一致 —


class PresetRepository(BaseRepository[Dict[str, Any]]):
    """预设数据访问 — 封装 config_db 中 presets 的 CRUD 操作。

    与现有 config_db 函数**完全兼容**：底层仍调用同一套函数，
    不改变数据库、不做数据迁移。

    Examples
    --------
    >>> repo = PresetRepository()
    >>> preset = repo.get_by_id(1)
    >>> all_presets = repo.get_all()
    >>> new_id = repo.save({"name": "my_algo", "params_json": '{"sma":20}', "category": "单滤波"})
    """

    # ══════════════════════════════════════════════════════════
    # BaseRepository 接口实现
    # ══════════════════════════════════════════════════════════

    def get_by_id(self, id: int) -> Optional[Dict[str, Any]]:
        return get_preset(id)

    def get_all(self) -> List[Dict[str, Any]]:
        return list_presets()

    def save(self, entity: Dict[str, Any]) -> int:
        """保存预设实体。

        Parameters
        ----------
        entity : dict
            必须包含 ``"name"`` 和 ``"params_json"`` 键。
            可选：``"description"`` (str), ``"category"`` (str)。

        Returns
        -------
        int
            插入或更新后的 ``preset_id``。
        """
        return save_preset(
            name=entity["name"],
            params_json=entity["params_json"],
            description=entity.get("description", ""),
            category=entity.get("category", "通用"),
        )

    # ══════════════════════════════════════════════════════════
    # 预设特有操作（超出 BaseRepository 的领域方法）
    # ══════════════════════════════════════════════════════════

    def get_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """按名称获取预设"""
        return get_preset_by_name(name)

    def list_by_category(self, category: str) -> List[Dict[str, Any]]:
        """按分类列出预设"""
        return list_presets(category=category)

    def delete(self, id: int) -> bool:
        """删除预设，返回是否成功"""
        return delete_preset(id)

    def rename(self, id: int, new_name: str) -> Optional[str]:
        """重命名预设，返回新名称（失败返回 None）"""
        return rename_preset(id, new_name)

    def apply(self, id: int) -> Optional[Dict[str, Any]]:
        """解析预设的参数 JSON，返回 Python dict"""
        return apply_preset(id)
