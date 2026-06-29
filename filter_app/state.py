"""
集中式 session_state 管理 — 类型安全的结构化访问

提供:
1. AppState 类 — 统一的 session_state 读写，含 _imp_ 备份兼容
2. 视图参数辅助函数 — 一键读写 4 视图的全部参数
3. 向后兼容 — 旧 _imp_ key 自动 fallback，预设/导入/导出无缝衔接

用法:
    from state import AppState, ViewState
    AppState.init_defaults()          # main() 开头
    vs = ViewState.load(0)            # 加载视图 0 的参数
    vs.slider("ke", 0.15)            # 类型安全的 slider 读写
    vs.set("ke", 0.20)               # 写优先 key + 写 _imp_ 备份
"""
from typing import Any, Dict, Optional

try:
    import streamlit as st
except ImportError:
    st = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Defaults catalog — 对照 session_state key 清单
# ---------------------------------------------------------------------------

# 系统内部 key（_ 前缀），无需用户交互
SYSTEM_KEYS: Dict[str, Any] = {
    "_config_initialized": True,
    "_import_data": None,
    "_fetched_ticker": "",
    "_day_offset": 0,
    "_db_import_hash": None,
    "_last_auto_refresh": None,
    "_last_sel_name": "",
    "_pending_apply_params": None,
    "_pending_reset_overwrite": False,
    "_preset_action": None,
    "_preset_action_id": None,
    "new_preset_name": "",
    "overwrite_preset": False,
    # 回测模式状态键
    "_cb_mode": False,           # 回测模式开关
    "_bar_index": 0,             # 当前 bar 位置 (0-indexed)
    "_is_playing": False,        # 是否自动播放中
    "_play_speed": 0.5,          # 播放速度（秒/步）
    "_min_tf": "",               # 4 视图中最小周期名称
    "_min_tf_bar_count": 0,      # 最小周期总 bar 数
    "_bt_data_cache": {},        # 回测全量数据缓存 (key: tf)
}

# 全局参数 — 已在 main() 中由 widget 初始化，这里只做参考
GLOBAL_KEYS = {
    "market": "美股 US",
    "ticker": "AAPL",
    "global_f": "schmitt",
    "global_dual": False,
    "global_f2": None,
    "auto_refresh": False,
    "refresh_interval": 60,
    "config_import": None,
    "day_step": 5,
}

# 视图参数默认值（每个视图独立, v0..v3）
VIEW_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "tf": {"default": "日线", "type": str},
    "n": {"default": 120, "type": int},
    "sch": {"default": True, "type": bool},
    "pred": {"default": True, "type": bool},
    "ke": {"default": 0.15, "type": float},
    "sm": {"default": 0.05, "type": float},
    "ew": {"default": 60, "type": int},
    "fm": {"default": "parabola", "type": str},
    "next": {"default": 8, "type": int},
    "fc": {"default": "#00d4aa", "type": str},
    "fc2": {"default": "#ff6b6b", "type": str},
    "strat": {"default": False, "type": bool},
    "sl": {"default": 2.0, "type": float},
    "cross_pnl": {"default": False, "type": bool},
    "align": {"default": False, "type": bool},
    "exp_all": {"default": False, "type": bool},
}

# 滤波器参数中文前缀 → 默认值（占位，实际由 FILTERS 注册表驱动）
# 编译时检查 st.session_state 即可，不需要枚举所有


# ---------------------------------------------------------------------------
# AppState — 核心 API
# ---------------------------------------------------------------------------
class AppState:
    """集中式 session_state 访问层。

    使用约定:
        AppState.get("key")          # 只读，不存在返回 None
        AppState.get("key", 42)      # 带默认值
        AppState.set("key", value)   # 写 key + _imp_ 备份
        AppState.has("key")          # 是否存在

    所有 _imp_ 备份的读写自动处理，上层代码无需关心。
    """

    # _imp_ 备份写入开关 — 设为 False 则只写主 key（测试/调试用）
    _imp_enabled = True

    @staticmethod
    def init_defaults() -> None:
        """初始化所有系统 key 的默认值（main() 开头调用一次）。"""
        if st is None:
            return
        for k, v in SYSTEM_KEYS.items():
            if k not in st.session_state and v is not None:
                st.session_state[k] = v

    @staticmethod
    def get(key: str, default: Any = None) -> Any:
        """安全的 session_state 读取，自动 fallback _imp_ 备份。

        优先读主 key；不存在时读 _imp_{key}；再不存在返回 default。
        """
        if st is None:
            return default
        if key in st.session_state:
            return st.session_state[key]
        imp_key = f"_imp_{key}"
        if imp_key in st.session_state:
            return st.session_state[imp_key]
        return default

    @staticmethod
    def set(key: str, value: Any) -> None:
        """写入 session_state 主 key + _imp_ 备份。

        _imp_ 备份确保在 Streamlit rerun 后参数不丢失。
        """
        if st is None:
            return
        st.session_state[key] = value
        if AppState._imp_enabled:
            st.session_state[f"_imp_{key}"] = value

    @staticmethod
    def set_many(items: Dict[str, Any]) -> None:
        """批量设置多个 key。"""
        for k, v in items.items():
            AppState.set(k, v)

    @staticmethod
    def has(key: str) -> bool:
        """检查 key 是否存在（含 _imp_ fallback）。"""
        if st is None:
            return False
        if key in st.session_state:
            return True
        return f"_imp_{key}" in st.session_state

    @staticmethod
    def pop(key: str, default: Any = None) -> Any:
        """删除并返回 key 的值（含 _imp_ 备份）。"""
        if st is None:
            return default
        val = st.session_state.pop(key, default)
        imp_key = f"_imp_{key}"
        st.session_state.pop(imp_key, None)
        return val

    @staticmethod
    def get_view_key(vi: int, suffix: str) -> str:
        """生成视图 key 名，如 v0_ke, v1_tf。"""
        return f"v{vi}_{suffix}"

    @staticmethod
    def get_global(key: str, default: Any = None) -> Any:
        """读取全局 widget 参数，默认值来自 GLOBAL_KEYS。"""
        return AppState.get(key, GLOBAL_KEYS.get(key, default))


# ---------------------------------------------------------------------------
# ViewState — 视图参数便捷访问
# ---------------------------------------------------------------------------
class ViewState:
    """单个视图的 session_state 参数封装。

    用法:
        vs = ViewState.load(0)
        vs.get("tf")          # → "日线"
        vs.get("ke", 0.15)    # → 0.15 (带默认值)
        vs.set("ke", 0.20)    # 写主 key + _imp_
        vs.get_expanded()     # ex: v0_exp_all
        vs.toggle_expanded()  # 切换展开/折叠
    """

    _PREFIX = "v"
    _EXP_SUFFIX = "exp_all"

    def __init__(self, vi: int) -> None:
        self.vi = vi
        self.prefix = f"{self._PREFIX}{vi}_"

    # ---- 读写 ----

    def get(self, suffix: str, default: Any = None) -> Any:
        """读取视图参数。suffix 不带前缀，如 "ke"。

        查找顺序：v{vi}_{suffix} → _imp_v{vi}_{suffix} → default。
        如果 default 未指定且 suffix 在 VIEW_DEFAULTS 中，使用其默认值。
        """
        key = self._key(suffix)
        if default is None and suffix in VIEW_DEFAULTS:
            default = VIEW_DEFAULTS[suffix]["default"]
        return AppState.get(key, default)

    def set(self, suffix: str, value: Any) -> None:
        """写入视图参数。"""
        key = self._key(suffix)
        AppState.set(key, value)

    def set_many(self, items: Dict[str, Any]) -> None:
        """批量写入视图参数。"""
        for suffix, value in items.items():
            self.set(suffix, value)

    # ---- 展开/折叠 ----

    def get_expanded(self) -> bool:
        """当前展开/折叠状态。"""
        return AppState.get(self._key(self._EXP_SUFFIX), False)

    def toggle_expanded(self) -> bool:
        """切换展开/折叠并返回新状态。"""
        new = not self.get_expanded()
        self.set(self._EXP_SUFFIX, new)
        return new

    # ---- 构建参数 cfg dict — 兼容 _render_params / collect_current_params ----

    @staticmethod
    def _suffix_to_cfg_key(suffix: str) -> str:
        """将视图参数后缀（如 "n", "sch"）映射为 cfg dict 的 key。

        部分后缀在内部使用不同命名，显式映射确保一致性。
        """
        mapping: Dict[str, str] = {
            "n": "n_pts",
            "sch": "show_sch",
            "pred": "show_pred",
            "next": "n_ext",
            "fm": "fit_mode",
            "strat": "show_strategy",
            "sl": "stop_loss_pct",
            "cross_pnl": "show_cross_pnl",
            "align": "show_alignment",
        }
        return mapping.get(suffix, suffix)

    def build_cfg(self, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """构建与 _render_params 返回格式一致的 cfg dict。

        包含视图预设 key + 用户自定义 extra。
        """
        cfg: Dict[str, Any] = {}
        for suffix, info in VIEW_DEFAULTS.items():
            val = self.get(suffix)
            cfg_key = self._suffix_to_cfg_key(suffix)
            cfg[cfg_key] = val

        if extra:
            cfg.update(extra)
        return cfg

    # ---- ViewState 工厂 ----

    @classmethod
    def load(cls, vi: int) -> "ViewState":
        """创建 ViewState 实例。无需初始化 — 直接读 session_state。"""
        return cls(vi)

    @classmethod
    def apply_preset_params(cls, params: Dict[str, Any]) -> None:
        """将预设参数批量写入 session_state（适配 _pending_apply_params 场景）。

        只处理形如 'v0_ke' / 'v1_tf' 的视图键；其他 key 直接写入。
        """
        for k, v in params.items():
            if k.startswith(cls._PREFIX) and "_" in k:
                # 形如 'v0_ke' 的视图键，直接写入
                AppState.set(k, v)
            else:
                AppState.set(k, v)

    # ---- 内部 ----

    def _key(self, suffix: str) -> str:
        """生成完整 key，如 vi_ke → v3_ke。"""
        return f"{self.prefix}{suffix}"


# ---------------------------------------------------------------------------
# Shortcut helpers — 保持 import 简洁
# ---------------------------------------------------------------------------
def view(vi: int) -> ViewState:
    """alias: view(0) → ViewState.load(0)"""
    return ViewState.load(vi)


def get_view_cfg(vi: int, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """alias: 一次构建视图 cfg dict"""
    return ViewState.load(vi).build_cfg(extra)
