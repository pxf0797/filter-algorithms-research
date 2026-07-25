"""filter 类型化配置 — ViewConfig dataclass

提供单视图完整配置的类型化表示，替代裸 ``cfg`` dict。
渐进迁移：允许 ``ViewConfig`` 和旧 dict 共存。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ViewConfig:
    """单视图完整配置 — 替代裸 cfg dict

    Attributes
    ----------
    tf : str
        时间周期，如 ``"日线"``, ``"60分钟"``.
    n_pts : int
        数据点数 / 滑动窗口大小.
    _fid : str
        主滤波器 ID（内部字段）.
    pv : dict
        主滤波器参数.
    _dual : bool
        是否启用双滤波.
    _fid2 : str or None
        副滤波器 ID（内部字段）.
    pv2 : dict
        副滤波器参数.
    show_sch : bool
        是否显示施密特触发器.
    ke : float
        施密特触发器灵敏度 :math:`k_\\varepsilon`.
    sm : float
        施密特触发器地板保护 :math:`\\sigma_{\\min}`.
    ew : int
        施密特触发器 EWMA 平滑周期.
    show_pred : bool
        是否显示预测曲线.
    fit_mode : str
        拟合模式，如 ``"parabola"``.
    n_ext : int
        预测延伸点数.
    show_strategy : bool
        是否显示策略 PnL.
    stop_loss_pct : float
        止损百分比.
    fc : str
        滤波曲线颜色.
    fc2 : str
        副滤波曲线颜色.
    show_cross_pnl : bool
        是否显示跨周期 PnL.
    show_alignment : bool
        是否显示同向性判断.
    show_pnl_feedback : bool
        是否显示实际持仓反馈.

    Examples
    --------
    >>> cfg = ViewConfig(tf="日线", n_pts=120, _fid="sma", pv={"window": 11})
    >>> cfg.tf
    '日线'
    >>> d = cfg.to_dict()
    >>> ViewConfig.from_dict(d) == cfg
    True
    """

    # === 周期与数据量 ===
    tf: str = "日线"
    n_pts: int = 120

    # === 主滤波器 ===
    _fid: str = ""
    pv: Dict[str, Any] = field(default_factory=dict)

    # === 副滤波器 ===
    _dual: bool = False
    _fid2: Optional[str] = None
    pv2: Dict[str, Any] = field(default_factory=dict)

    # === 施密特触发器 ===
    show_sch: bool = True
    ke: float = 0.15
    sm: float = 0.05
    ew: int = 60

    # === 预测曲线 ===
    show_pred: bool = True
    fit_mode: str = "parabola"
    n_ext: int = 8

    # === 策略 PnL ===
    show_strategy: bool = False
    stop_loss_pct: float = 2.0

    # === 显示 ===
    fc: str = "#00d4aa"
    fc2: str = "#ff6b6b"

    # === 跨周期 ===
    show_cross_pnl: bool = False
    show_alignment: bool = False
    show_pnl_feedback: bool = False

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """导出为与旧 cfg dict 兼容的字典。

        ``_fid`` 和 ``_fid2`` 以原始键名保留（兼容 ``FILTERS`` 查找）。
        值为 ``None`` 的 Optional 字段保留以维持 JSON 结构。
        """
        return {
            "tf": self.tf,
            "n_pts": self.n_pts,
            "_fid": self._fid,
            "pv": self.pv,
            "_dual": self._dual,
            "_fid2": self._fid2,
            "pv2": self.pv2,
            "show_sch": self.show_sch,
            "ke": self.ke,
            "sm": self.sm,
            "ew": self.ew,
            "show_pred": self.show_pred,
            "fit_mode": self.fit_mode,
            "n_ext": self.n_ext,
            "show_strategy": self.show_strategy,
            "stop_loss_pct": self.stop_loss_pct,
            "fc": self.fc,
            "fc2": self.fc2,
            "show_cross_pnl": self.show_cross_pnl,
            "show_alignment": self.show_alignment,
            "show_pnl_feedback": self.show_pnl_feedback,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ViewConfig":
        """从旧 cfg dict 构造实例。

        仅提取 ``ViewConfig`` 已定义的字段，忽略多余键。
        """
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in d.items() if k in known}
        return cls(**kwargs)

    # ------------------------------------------------------------------
    # dict-like access (for gradual migration)
    # ------------------------------------------------------------------

    def __getitem__(self, key: str) -> Any:
        """允许 ``cfg["tf"]`` 风格的访问（渐进迁移辅助）。"""
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        """允许 ``cfg.get("tf", "日线")`` 风格的访问。"""
        return getattr(self, key, default)
