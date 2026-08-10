"""
Tests for BSMonitor — BS 仓位操作监测模块单元测试和集成测试
"""

import pandas as pd
import pytest
from browse.bs_monitor import BSMonitor, BSRecord


# ── Test data helpers ──────────────────────────────────────────────────
#
# compute_bs_markers 返回格式（实际格式，来自 filter/engine/signals.py）：
#   entry marker: (idx, bs_type, color, date)              — 4 元组
#   exit marker:  (idx, bs_type, color, exit_reason, date) — 5 元组
#
# _parse_marker 解析规则：
#   label  = str(marker[1])   → "B" / "S"
#   color  = str(marker[2])   → "green" → direction="long", 其他 → "short"
#   ts     = marker[4] if len >= 5 else marker[3]
#   price  = 0.0（从 marker 无法提取价格）


def make_markers(entries=None, exits=None):
    """构造 compute_bs_markers 格式的返回值。"""
    return {
        "entry_markers": entries or [],
        "exit_markers": exits or [],
    }


def make_entry(bs_type="B", color="green", date_str="2026-08-10", idx=0):
    """构造一条 entry marker。"""
    return (idx, bs_type, color, pd.Timestamp(date_str))


def make_exit(bs_type="S", color="green", reason="tp", date_str="2026-08-10", idx=0):
    """构造一条 exit marker。"""
    return (idx, bs_type, color, reason, pd.Timestamp(date_str))


# ═══════════════════════════════════════════════════════════════════════
# 单元测试
# ═══════════════════════════════════════════════════════════════════════


class TestBSMonitor:
    """BSMonitor 单元测试和集成测试。"""

    # ── 构造 fixture ─────────────────────────────────────────────────

    @staticmethod
    def _new_monitor(max_records=500):
        """快捷创建 monitor。"""
        return BSMonitor(max_records=max_records)

    # ── 1. BSRecord dataclass ────────────────────────────────────────

    def test_bs_record_creation(self):
        """BSRecord dataclass 字段可独立创建并正确存储。"""
        r = BSRecord(
            stock_code="0700.HK",
            timeframe="日线",
            timestamp=pd.Timestamp("2026-08-10"),
            bs_type="B",
            direction="long",
            price=12.5,
            detected_at="2026-08-10T10:00:00",
        )
        assert r.stock_code == "0700.HK"
        assert r.timeframe == "日线"
        assert r.timestamp == pd.Timestamp("2026-08-10")
        assert r.bs_type == "B"
        assert r.direction == "long"
        assert r.price == 12.5
        assert r.detected_at == "2026-08-10T10:00:00"

    # ── 2. feed 空数据 ──────────────────────────────────────────────

    def test_feed_empty_markers(self):
        """feed markers=[] 返回空列表，records 不增加。"""
        m = self._new_monitor()
        new = m.feed("0700.HK", "日线", [])
        assert new == []
        assert m.get_all() == []

    def test_feed_empty_dict(self):
        """feed {"entry_markers": [], "exit_markers": []} 正常处理。"""
        m = self._new_monitor()
        new = m.feed("0700.HK", "日线", make_markers())
        assert new == []
        assert m.get_all() == []

    def test_feed_none_markers(self):
        """feed markers=None 不崩溃，返回空列表。"""
        m = self._new_monitor()
        new = m.feed("0700.HK", "日线", None)
        assert new == []
        assert m.get_all() == []

    # ── 3. feed 正常数据 ────────────────────────────────────────────

    def test_feed_new_entry(self):
        """feed 一个 entry marker，返回 1 条新记录。"""
        m = self._new_monitor()
        markers = make_markers(entries=[
            make_entry(idx=10, bs_type="B", color="green", date_str="2026-08-10"),
        ])
        new = m.feed("0700.HK", "日线", markers)
        assert len(new) == 1
        assert m.get_new_count() == 1

        r = new[0]
        assert r.stock_code == "0700.HK"
        assert r.timeframe == "日线"
        assert r.bs_type == "B"
        assert r.direction == "long"
        assert r.timestamp == pd.Timestamp("2026-08-10")
        assert r.price == 0.0
        assert isinstance(r.detected_at, str)

    def test_feed_new_exit(self):
        """feed 一个 exit marker，正常记录（short direction）。"""
        m = self._new_monitor()
        markers = make_markers(exits=[
            make_exit(idx=20, bs_type="S", color="red", reason="sl",
                      date_str="2026-08-11"),
        ])
        new = m.feed("AAPL", "60分钟", markers)
        assert len(new) == 1
        r = new[0]
        assert r.stock_code == "AAPL"
        assert r.timeframe == "60分钟"
        assert r.bs_type == "S"
        assert r.direction == "short"   # color="red" → "short"
        assert r.timestamp == pd.Timestamp("2026-08-11")

    def test_feed_multiple_markers(self):
        """feed 多个 entry + exit markers，全部记录。"""
        m = self._new_monitor()
        markers = make_markers(
            entries=[
                make_entry(idx=1, bs_type="B", color="green", date_str="2026-01-01"),
                make_entry(idx=3, bs_type="S", color="red", date_str="2026-01-03"),
            ],
            exits=[
                make_exit(idx=2, bs_type="S", color="green", reason="tp",
                          date_str="2026-01-02"),
                make_exit(idx=4, bs_type="B", color="red", reason="sl",
                          date_str="2026-01-04"),
            ],
        )
        new = m.feed("0700.HK", "日线", markers)
        assert len(new) == 4
        assert m.get_new_count() == 4
        assert len(m.get_all()) == 4

        # 验证方向解析
        directions = {r.direction for r in new}
        assert "long" in directions
        assert "short" in directions

    # ── 4. 去重 ──────────────────────────────────────────────────────

    def test_feed_dedup(self):
        """两次 feed 相同 markers，第二次返回空列表。"""
        m = self._new_monitor()
        markers = make_markers(entries=[
            make_entry(idx=0, bs_type="B", color="green", date_str="2026-08-10"),
        ])
        new1 = m.feed("0700.HK", "日线", markers)
        assert len(new1) == 1

        new2 = m.feed("0700.HK", "日线", markers)
        assert new2 == []
        assert m.get_new_count() == 1   # 第一次的仍是新的

    def test_feed_dedup_partial(self):
        """第一次 feed [A, B]，第二次 feed [B, C]，仅返回 [C]。"""
        m = self._new_monitor()
        marker_a = make_entry(idx=0, bs_type="B", color="green", date_str="2026-08-10")
        marker_b = make_entry(idx=1, bs_type="S", color="red", date_str="2026-08-11")
        marker_c = make_entry(idx=2, bs_type="B", color="green", date_str="2026-08-12")

        markers1 = make_markers(entries=[marker_a, marker_b])
        new1 = m.feed("0700.HK", "日线", markers1)
        assert len(new1) == 2

        markers2 = make_markers(entries=[marker_b, marker_c])
        new2 = m.feed("0700.HK", "日线", markers2)
        assert len(new2) == 1
        assert new2[0].timestamp == pd.Timestamp("2026-08-12")

    # ── 5. get_all ───────────────────────────────────────────────────

    def test_get_all_returns_all(self):
        """get_all() 返回全部记录。"""
        m = self._new_monitor()
        m.feed("0700.HK", "日线", make_markers(entries=[
            make_entry(date_str="2026-08-10"),
        ]))
        m.feed("0700.HK", "60分钟", make_markers(entries=[
            make_entry(date_str="2026-08-11"),
        ]))
        assert len(m.get_all()) == 2

    def test_get_all_filter_by_tf(self):
        """get_all(tf="日线") 仅返回匹配周期记录。"""
        m = self._new_monitor()
        # 使用不同日期避免去重碰撞（_seen 不含 timeframe）
        m.feed("0700.HK", "日线", make_markers(entries=[
            make_entry(date_str="2026-08-10"),
        ]))
        m.feed("0700.HK", "60分钟", make_markers(entries=[
            make_entry(date_str="2026-08-11"),
        ]))

        daily = m.get_all(tf="日线")
        assert len(daily) == 1
        assert daily[0].timeframe == "日线"

        hourly = m.get_all(tf="60分钟")
        assert len(hourly) == 1
        assert hourly[0].timeframe == "60分钟"

    def test_get_all_order(self):
        """get_all() 按 detected_at 倒序（最新在前）。"""
        m = self._new_monitor()
        m.feed("0700.HK", "日线", make_markers(entries=[
            make_entry(date_str="2026-08-10"),
        ]))
        m.feed("0700.HK", "日线", make_markers(entries=[
            make_entry(date_str="2026-08-11"),
        ]))

        records = m.get_all()
        assert len(records) >= 2
        # detected_at 倒序
        for i in range(len(records) - 1):
            assert records[i].detected_at >= records[i + 1].detected_at

    # ── 6. new_count / mark_round_complete ──────────────────────────

    def test_get_new_count(self):
        """feed 后 get_new_count() 正确，mark_round_complete() 后归零。"""
        m = self._new_monitor()
        assert m.get_new_count() == 0

        m.feed("0700.HK", "日线", make_markers(entries=[
            make_entry(date_str="2026-08-10"),
        ]))
        assert m.get_new_count() == 1

        m.mark_round_complete()
        assert m.get_new_count() == 0

    def test_mark_round_complete(self):
        """标记后 get_new_count() == 0，记录本身不丢失。"""
        m = self._new_monitor()
        m.feed("0700.HK", "日线", make_markers(entries=[
            make_entry(date_str="2026-08-10"),
        ]))
        assert m.get_new_count() == 1

        m.mark_round_complete()
        assert m.get_new_count() == 0
        assert len(m.get_all()) == 1  # 记录仍在

    def test_bs_record_is_new_field(self):
        """通过 get_all + get_new_count 配合判断新增记录。"""
        m = self._new_monitor()
        # 第一轮
        m.feed("0700.HK", "日线", make_markers(entries=[
            make_entry(date_str="2026-08-10"),
        ]))
        assert m.get_new_count() == 1
        # get_new_count() 告诉有多少条新记录，get_all()[:n] 即是新增
        assert len(m.get_all()[:m.get_new_count()]) == 1

        m.mark_round_complete()
        assert m.get_new_count() == 0
        assert len(m.get_all()[:m.get_new_count()]) == 0

    # ── 7. clear ────────────────────────────────────────────────────

    def test_clear_all(self):
        """clear() 后 get_all() 为空，_seen 清空。"""
        m = self._new_monitor()
        m.feed("0700.HK", "日线", make_markers(entries=[
            make_entry(date_str="2026-08-10"),
        ]))
        assert len(m.get_all()) == 1

        m.clear()
        assert m.get_all() == []
        assert m.get_new_count() == 0

    def test_clear_by_tf(self):
        """clear(tf="日线") 仅清空日线，其他周期保留。"""
        m = self._new_monitor()
        # 使用不同日期避免去重碰撞
        m.feed("0700.HK", "日线", make_markers(entries=[
            make_entry(date_str="2026-08-10"),
        ]))
        m.feed("0700.HK", "60分钟", make_markers(entries=[
            make_entry(date_str="2026-08-11"),
        ]))

        m.clear(tf="日线")
        assert len(m.get_all(tf="日线")) == 0
        assert len(m.get_all(tf="60分钟")) == 1
        assert len(m.get_all()) == 1
        assert m.get_all()[0].timeframe == "60分钟"

    def test_clear_by_tf_updates_seen(self):
        """clear(tf) 后 _seen 重建：清除周期的 marker 可重新 feed。"""
        m = self._new_monitor()
        marker_daily = make_entry(date_str="2026-08-10", bs_type="B", color="green")
        marker_hourly = make_entry(date_str="2026-08-11", bs_type="B", color="green")

        m.feed("0700.HK", "日线", make_markers(entries=[marker_daily]))
        m.feed("0700.HK", "60分钟", make_markers(entries=[marker_hourly]))
        assert len(m.get_all()) == 2

        # 清除日线
        m.clear(tf="日线")

        # 重新 feed 相同的日线 marker → 应视为新记录
        new = m.feed("0700.HK", "日线", make_markers(entries=[marker_daily]))
        assert len(new) == 1

        # 重新 feed 相同的 60分钟 marker → 应去重
        new = m.feed("0700.HK", "60分钟", make_markers(entries=[marker_hourly]))
        assert new == []

    # ── 8. reset_stock ──────────────────────────────────────────────

    def test_reset_stock(self):
        """reset_stock("NEW") 清空所有数据。"""
        m = self._new_monitor()
        m.feed("0700.HK", "日线", make_markers(entries=[
            make_entry(date_str="2026-08-10"),
        ]))
        m.feed("0700.HK", "60分钟", make_markers(entries=[
            make_entry(date_str="2026-08-11"),
        ]))
        assert len(m.get_all()) == 2

        m.reset_stock("AAPL")
        assert m.get_all() == []
        assert m.get_new_count() == 0

        # 重置后可以 feed 新数据
        new = m.feed("AAPL", "日线", make_markers(entries=[
            make_entry(date_str="2026-09-01"),
        ]))
        assert len(new) == 1
        assert new[0].stock_code == "AAPL"
        assert len(m.get_all()) == 1

    # ── 9. FIFO 淘汰 ────────────────────────────────────────────────

    def test_fifo_eviction(self):
        """超过 max_records 上限时淘汰最旧记录（max_records=3）。"""
        m = self._new_monitor(max_records=3)
        markers_a = make_markers(entries=[
            make_entry(date_str="2026-08-10"),
        ])
        markers_b = make_markers(entries=[
            make_entry(date_str="2026-08-11"),
        ])
        markers_c = make_markers(entries=[
            make_entry(date_str="2026-08-12"),
        ])
        markers_d = make_markers(entries=[
            make_entry(date_str="2026-08-13"),
        ])

        m.feed("0700.HK", "日线", markers_a)
        m.feed("0700.HK", "日线", markers_b)
        m.feed("0700.HK", "日线", markers_c)
        assert len(m.get_all()) == 3

        # feed D → A 被淘汰，留 [B, C, D]
        m.feed("0700.HK", "日线", markers_d)
        records = m.get_all()
        assert len(records) == 3
        timestamps = {r.timestamp for r in records}
        assert pd.Timestamp("2026-08-10") not in timestamps  # A 被淘汰
        assert pd.Timestamp("2026-08-11") in timestamps       # B 保留
        assert pd.Timestamp("2026-08-12") in timestamps       # C 保留
        assert pd.Timestamp("2026-08-13") in timestamps       # D 新加入

    def test_fifo_eviction_updates_seen(self):
        """淘汰时 _seen 集合也清理：淘汰后可重新 feed 相同 marker。"""
        m = self._new_monitor(max_records=3)
        marker_a = make_entry(date_str="2026-08-10", bs_type="B", color="green")
        marker_b = make_entry(date_str="2026-08-11", bs_type="B", color="green")
        marker_c = make_entry(date_str="2026-08-12", bs_type="B", color="green")
        marker_d = make_entry(date_str="2026-08-13", bs_type="B", color="green")

        m.feed("0700.HK", "日线", make_markers(entries=[marker_a]))
        m.feed("0700.HK", "日线", make_markers(entries=[marker_b]))
        m.feed("0700.HK", "日线", make_markers(entries=[marker_c]))

        # feed D 触发淘汰 A
        m.feed("0700.HK", "日线", make_markers(entries=[marker_d]))

        # A 已从 _seen 移除，重新 feed A 应被接受
        new = m.feed("0700.HK", "日线", make_markers(entries=[marker_a]))
        assert len(new) == 1
        assert new[0].timestamp == pd.Timestamp("2026-08-10")

        # 重新 feed B/C/D：B 在步骤 3 中被 evict 且 _seen 已移除 → 可重新加入
        # C 和 D 仍在 _seen → 去重
        new = m.feed("0700.HK", "日线", make_markers(entries=[marker_b,
                                                               marker_c,
                                                               marker_d]))
        assert len(new) == 1
        assert new[0].timestamp == pd.Timestamp("2026-08-11")  # B

    # ── 10. 解析边界 ─────────────────────────────────────────────────

    def test_feed_invalid_marker_length(self):
        """长度 < 4 的 marker 被跳过，不崩溃。"""
        m = self._new_monitor()
        new = m.feed("0700.HK", "日线", [(0, "B", "green")])  # 3 元组
        assert new == []
        assert m.get_all() == []

    def test_feed_non_tuple_marker(self):
        """非 list/tuple 的 marker 被跳过。"""
        m = self._new_monitor()
        new = m.feed("0700.HK", "日线", ["not_a_tuple"])  # 字符串 len=11 但非 tuple
        assert new == []

    def test_feed_list_of_markers_directly(self):
        """feed 直接传入 marker 列表（非 dict 包装）也能工作。"""
        m = self._new_monitor()
        marker = make_entry(date_str="2026-08-10")
        new = m.feed("0700.HK", "日线", [marker])
        assert len(new) == 1
        assert new[0].bs_type == "B"

    def test_feed_same_time_diff_direction(self):
        """相同时刻、不同方向的 marker 不会被错误去重。"""
        m = self._new_monitor()
        m1 = make_entry(idx=0, bs_type="B", color="green", date_str="2026-08-10")
        m2 = make_entry(idx=0, bs_type="S", color="red", date_str="2026-08-10")
        # 相同 timestamp 但 bs_type 和 direction 不同

        new = m.feed("0700.HK", "日线", make_markers(entries=[m1, m2]))
        assert len(new) == 2

    def test_feed_same_time_same_direction(self):
        """相同时刻、相同 B/S 和方向 → 去重生效。"""
        m = self._new_monitor()
        m1 = make_entry(idx=0, bs_type="B", color="green", date_str="2026-08-10")
        m2 = make_entry(idx=0, bs_type="B", color="green", date_str="2026-08-10")

        new = m.feed("0700.HK", "日线", make_markers(entries=[m1, m2]))
        assert len(new) == 1  # 第二个被去重


# ═══════════════════════════════════════════════════════════════════════
# 集成场景测试
# ═══════════════════════════════════════════════════════════════════════


class TestBSMonitorScenarios:
    """BSMonitor 集成场景测试。"""

    @staticmethod
    def _new_monitor(max_records=500):
        return BSMonitor(max_records=max_records)

    def test_scenario_auto_refresh(self):
        """模拟自动刷新：
        第一次 feed → 查看新增 → mark_round_complete →
        第二次 feed（部分重叠 + 新数据）→ 仅返回新数据。
        """
        m = self._new_monitor()

        # ── 第 1 轮 ──
        m1 = make_entry(date_str="2026-08-10", bs_type="B", color="green")
        m2 = make_entry(date_str="2026-08-11", bs_type="S", color="red")
        new1 = m.feed("0700.HK", "日线", make_markers(entries=[m1, m2]))
        assert len(new1) == 2
        assert m.get_new_count() == 2

        # 查看：get_all() 返回全部 2 条，前 2 条为新
        all_records = m.get_all()
        assert len(all_records) == 2

        m.mark_round_complete()
        assert m.get_new_count() == 0

        # ── 第 2 轮 ──
        # m1 重复、m2 重复、m3 新增
        m3 = make_entry(date_str="2026-08-12", bs_type="B", color="green")
        new2 = m.feed("0700.HK", "日线", make_markers(entries=[m1, m2, m3]))
        assert len(new2) == 1
        assert new2[0].timestamp == pd.Timestamp("2026-08-12")
        assert m.get_new_count() == 1

        m.mark_round_complete()
        assert m.get_new_count() == 0
        assert len(m.get_all()) == 3

    def test_scenario_stock_switch(self):
        """模拟股票切换：
        feed stockA → reset_stock → feed stockB → stockA 数据已清除。
        """
        m = self._new_monitor()

        # feed stockA
        m.feed("0700.HK", "日线", make_markers(entries=[
            make_entry(date_str="2026-08-10"),
            make_entry(date_str="2026-08-11"),
        ]))
        assert len(m.get_all()) == 2
        assert m.get_all()[0].stock_code == "0700.HK"

        # 切换股票
        m.reset_stock("AAPL")
        assert len(m.get_all()) == 0
        assert m.get_new_count() == 0

        # feed stockB
        new = m.feed("AAPL", "日线", make_markers(entries=[
            make_entry(date_str="2026-09-01"),
        ]))
        assert len(new) == 1
        assert new[0].stock_code == "AAPL"
        assert len(m.get_all()) == 1

        # 验证 stockA 的数据不存在
        codes = {r.stock_code for r in m.get_all()}
        assert "0700.HK" not in codes

    def test_scenario_multi_tf(self):
        """模拟多周期监测：
        feed 日线 + feed 60分钟 → 各周期独立查询 → 切换周期时数据不丢失。
        """
        m = self._new_monitor()

        # feed 日线 markers
        m.feed("0700.HK", "日线", make_markers(entries=[
            make_entry(date_str="2026-08-10", bs_type="B", color="green"),
            make_entry(date_str="2026-08-12", bs_type="S", color="red"),
        ]))

        # feed 60分钟 markers
        m.feed("0700.HK", "60分钟", make_markers(exits=[
            make_exit(date_str="2026-08-11", bs_type="B", color="red", reason="sl"),
        ]))

        # 跨周期数据都在
        assert len(m.get_all()) == 3

        # 按周期过滤
        daily = m.get_all(tf="日线")
        assert len(daily) == 2
        assert all(r.timeframe == "日线" for r in daily)

        hourly = m.get_all(tf="60分钟")
        assert len(hourly) == 1
        assert all(r.timeframe == "60分钟" for r in hourly)

        # 再次 feed 日线（部分新增），不影响 60分钟数据
        m.feed("0700.HK", "日线", make_markers(entries=[
            make_entry(date_str="2026-08-13", bs_type="B", color="green"),
        ]))
        assert len(m.get_all(tf="日线")) == 3
        assert len(m.get_all(tf="60分钟")) == 1

        # 清除日线不影响 60分钟
        m.clear(tf="日线")
        assert len(m.get_all(tf="日线")) == 0
        assert len(m.get_all(tf="60分钟")) == 1

    def test_scenario_multi_tf_with_mark_complete(self):
        """多周期下 mark_round_complete 不会清除其他周期的新增标记。"""
        m = self._new_monitor()

        # 使用不同日期避免去重碰撞
        m.feed("0700.HK", "日线", make_markers(entries=[
            make_entry(date_str="2026-08-10"),
        ]))
        m.feed("0700.HK", "60分钟", make_markers(entries=[
            make_entry(date_str="2026-08-11"),
        ]))

        # 两轮都产生新增
        assert m.get_new_count() == 2

        m.mark_round_complete()
        # 注意：mark_round_complete 清空所有 _new_indices，不分周期
        assert m.get_new_count() == 0

        # 仅日线再 feed（使用不同日期避免与其他周期去重）
        m.feed("0700.HK", "日线", make_markers(entries=[
            make_entry(date_str="2026-08-12"),
        ]))
        assert m.get_new_count() == 1
