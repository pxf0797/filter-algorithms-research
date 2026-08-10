"""
BS 仓位操作监测模块 — 实时追踪 B/S 信号、持久化记录、表格渲染

依赖: Streamlit（仅 render_bs_table）、engine.signals（compute_bs_markers 返回值格式）
"""

import datetime
from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd
import streamlit as st


@dataclass
class BSRecord:
    """单条 BS 监测记录。"""

    stock_code: str       # 股票代码，如 "0700.HK"
    timeframe: str        # 周期，如 "日线", "60分钟"
    timestamp: Any        # BS 信号时刻（使用 marker 中的原始时间类型）
    bs_type: str          # "B" 或 "S"
    direction: str        # "long" 或 "short"
    price: float          # 信号发生时的价格（从 marker 中提取，若无则填 0.0）
    detected_at: str      # 监测时间，ISO 格式字符串


class BSMonitor:
    """BS 信号监测器，在 st.session_state 中持久化。

    使用示例::

        monitor = BSMonitor(max_records=500)
        st.session_state["bs_monitor"] = monitor

        # 每轮渲染时喂入新 markers
        new_records = monitor.feed("0700.HK", "日线", bs_markers)
        if new_records:
            st.toast(f"检测到 {len(new_records)} 个新 BS 信号")

        # 渲染监测表格
        render_bs_table(monitor, "日线")
        monitor.mark_round_complete()
    """

    def __init__(self, max_records: int = 500):
        self.max_records = max_records
        self._records: list[BSRecord] = []
        self._seen: set[tuple] = set()        # (time_str, bs_type, direction)
        self._new_indices: set[int] = set()    # 本轮新增记录在 _records 中的索引

    # ── feed ─────────────────────────────────────────────────

    def feed(self, stock_code: str, tf: str, markers) -> list[BSRecord]:
        """喂入 compute_bs_markers 返回值，去重后追加。

        Parameters
        ----------
        stock_code : str
            股票代码。
        tf : str
            信号来源周期。
        markers : dict or list
            ``compute_bs_markers`` 的返回值
            ``{"entry_markers": [...], "exit_markers": [...]}``。

        Returns
        -------
        list[BSRecord]
            本轮新增的记录。
        """
        incoming = self._extract_markers(markers)
        new_records: list[BSRecord] = []

        for marker in incoming:
            ts, bs_type, direction, price = self._parse_marker(marker)
            if ts is None:
                continue

            # 去重键：(时间字符串, B/S, 方向)
            try:
                ts_str = str(ts)
            except Exception:
                ts_str = repr(ts)
            dedup_key = (ts_str, bs_type, direction)
            if dedup_key in self._seen:
                continue

            record = BSRecord(
                stock_code=stock_code,
                timeframe=tf,
                timestamp=ts,
                bs_type=bs_type,
                direction=direction,
                price=price,
                detected_at=datetime.datetime.now().isoformat(),
            )
            self._records.append(record)
            self._new_indices.add(len(self._records) - 1)
            self._seen.add(dedup_key)
            new_records.append(record)

        self._evict_if_needed()
        return new_records

    # ── get_all ───────────────────────────────────────────────

    def get_all(self, tf: Optional[str] = None) -> list[BSRecord]:
        """返回全部记录（detected_at 倒序）。tf 非 None 时仅返回匹配周期的记录。"""
        records = self._records
        if tf is not None:
            records = [r for r in records if r.timeframe == tf]
        return sorted(records, key=lambda r: r.detected_at, reverse=True)

    # ── get_new_count ────────────────────────────────────────

    def get_new_count(self) -> int:
        """本轮新增数量。"""
        return len(self._new_indices)

    # ── mark_round_complete ──────────────────────────────────

    def mark_round_complete(self):
        """清除本轮新增标记（下次 feed 开始新的"本轮"）。"""
        self._new_indices.clear()

    # ── clear ────────────────────────────────────────────────

    def clear(self, tf: Optional[str] = None):
        """清空数据。tf=None 全部清空，否则仅清空指定周期。"""
        if tf is None:
            self._records.clear()
            self._seen.clear()
            self._new_indices.clear()
            return

        # 保留非指定周期的记录；需重建 _new_indices 和 _seen
        new_ids = {
            id(self._records[i])
            for i in self._new_indices
            if i < len(self._records) and self._records[i].timeframe != tf
        }
        self._records = [r for r in self._records if r.timeframe != tf]
        self._new_indices = {
            i for i, r in enumerate(self._records) if id(r) in new_ids
        }
        self._seen.clear()
        for r in self._records:
            try:
                ts_str = str(r.timestamp)
            except Exception:
                ts_str = repr(r.timestamp)
            self._seen.add((ts_str, r.bs_type, r.direction))

    # ── reset_stock ──────────────────────────────────────────

    def reset_stock(self, new_code: str):
        """切换股票时清空全部数据。"""
        self._records.clear()
        self._seen.clear()
        self._new_indices.clear()

    # ── 内部方法 ─────────────────────────────────────────────

    @staticmethod
    def _extract_markers(markers):
        """从 compute_bs_markers 返回值中提取扁平 marker 列表。

        compute_bs_markers 返回 ``{"entry_markers": [(idx, B/S, color, date), ...],
        "exit_markers": [(idx, B/S, color, reason, date), ...]}``。
        """
        if isinstance(markers, dict):
            items: list = []
            items.extend(markers.get("entry_markers", []))
            items.extend(markers.get("exit_markers", []))
            return items
        if isinstance(markers, (list, tuple)):
            return list(markers)
        return []

    @staticmethod
    def _parse_marker(marker):
        """解析单个 marker tuple → (timestamp, bs_type, direction, price)。

        entry marker: ``(idx, label, color, date)``               — 4 元素
        exit marker:  ``(idx, label, color, exit_reason, date)``   — 5 元素

        color "green" → direction "long",  其它 → "short"。
        marker 不含价格信息，price 固定返回 0.0。
        """
        if not isinstance(marker, (list, tuple)) or len(marker) < 4:
            return None, "?", "?", 0.0

        label = str(marker[1])
        color = str(marker[2])
        direction = "long" if color == "green" else "short"

        # 时间：entry 在索引 3，exit 在索引 4（多了 exit_reason）
        ts = marker[4] if len(marker) >= 5 else marker[3]

        return ts, label, direction, 0.0

    def _evict_if_needed(self):
        """FIFO 淘汰：_records 超出 max_records 时从头删除最旧记录。"""
        while len(self._records) > self.max_records:
            removed = self._records.pop(0)
            # 所有 _new_indices 减 1，丢弃索引 0 的记录
            self._new_indices = {i - 1 for i in self._new_indices if i > 0}
            # 从 _seen 移除被淘汰的记录
            try:
                ts_str = str(removed.timestamp)
            except Exception:
                ts_str = repr(removed.timestamp)
            self._seen.discard((ts_str, removed.bs_type, removed.direction))


# ═══════════════════════════════════════════════════════════════════
# Streamlit 渲染函数
# ═══════════════════════════════════════════════════════════════════

def render_bs_table(monitor: BSMonitor, bs_monitor_tf: str):
    """渲染 BS 监测表格（Streamlit 组件）。

    布局:
    - 标题行: "📊 BS 监测 — {bs_monitor_tf}" + 新增计数 + [清空] 按钮
    - 无数据时: st.info 占位提示
    - 有数据时: st.dataframe（新增行 🟢 标记），底部 st.caption 总记录数

    Parameters
    ----------
    monitor : BSMonitor
        BS 监测器实例。
    bs_monitor_tf : str
        要监测的周期，如 ``"日线"``。
    """
    records = monitor.get_all(bs_monitor_tf)

    # 本轮新增记录的 id 集合（用于高亮）
    new_ids: set[int] = {
        id(monitor._records[i])
        for i in monitor._new_indices
        if i < len(monitor._records)
    }

    # ── 标题行 ──
    c1, c2 = st.columns([4, 1])
    with c1:
        new_count = monitor.get_new_count()
        badge = f" 🔴{new_count}" if new_count > 0 else ""
        st.markdown(f"##### 📊 BS 监测 — {bs_monitor_tf}{badge}")
    with c2:
        if st.button("🗑️ 清空", key=f"_bs_clear_{bs_monitor_tf}"):
            monitor.clear(bs_monitor_tf)
            st.rerun()

    # ── 无数据 ──
    if not records:
        st.info("📭 暂无监测数据，等待 BS 信号出现...")
        return

    # ── 构建 DataFrame ──
    rows: list[dict] = []
    for i, r in enumerate(records):
        is_new = id(r) in new_ids
        # 格式化时间戳显示
        ts_display = ""
        if r.timestamp is not None:
            try:
                ts_display = r.timestamp.strftime("%Y-%m-%d %H:%M")
            except Exception:
                ts_display = str(r.timestamp)[:19]

        rows.append({
            "#": f"🟢{i + 1}" if is_new else str(i + 1),
            "代码": r.stock_code,
            "周期": r.timeframe,
            "时刻": ts_display,
            "BS": r.bs_type,
            "方向": "多" if r.direction == "long" else "空",
            "价格": f"{r.price:.2f}" if r.price else "-",
            "检测时间": r.detected_at[:19] if r.detected_at else "-",
        })

    df = pd.DataFrame(rows)

    st.dataframe(
        df,
        column_config={
            "#": st.column_config.Column(width="small"),
            "代码": st.column_config.Column(width="small"),
            "周期": st.column_config.Column(width="small"),
            "时刻": st.column_config.Column(width="medium"),
            "BS": st.column_config.Column(width="small"),
            "方向": st.column_config.Column(width="small"),
            "价格": st.column_config.Column(width="small"),
            "检测时间": st.column_config.Column(width="medium"),
        },
        use_container_width=True,
        hide_index=True,
    )

    st.caption(f"共 {len(records)} 条记录（缓存上限 {monitor.max_records} 条）")
