"""六视图（2×3）布局回归测试 — 4 视图 → 6 视图的 T3 改动验收。

覆盖 ``verify-T3.md`` 指出的三处回归缺口，以及 4→6 视图布局的端到端契约：

1. 常量：``DEFAULT_TFS`` 长度 6、v0~v3 绑定不变、v4=周线/v5=月线、
   ``NUM_VIEWS``/``VIEW_GRID_ROWS``/``VIEW_GRID_COLS``。
2. ``_render_param_panels`` 网格（2 行 × 3 列）、6 个 cfg 的 ``_vid``、
   首次渲染的默认周期去重（``_default_view_tf``）与「已存在值不覆盖」。
3. 图表网格：AppTest 真实渲染 6 个参数面板控件 + 6 个图表 iframe。
4. 配置导出/收集覆盖 v0~v5。
5. CLI 2B：视图数随预设收缩 + ``_vid`` 与 tf 对应。
6. BS 监测周期选项覆盖 v4/v5 的周期。
7. ``state.py`` 的 ``_imp_`` 清理保留 v4/v5、删除 v6+。
8. ``_vid`` 贯通：ParquetStore 列前缀/列数、``BacktestRunner._config_hash()``
   对 ``_vid`` 不敏感。

AppTest 相关用例按 ``test_app_ui.py`` 的方式恢复真实 streamlit：
``conftest.py`` 在收集阶段把 streamlit mock 成 MagicMock，因此必须在
fixture 内部延迟导入并清除已持有旧 mock 的项目模块。真实 AppTest 只跑
一次（module scope），两条用例读取同一份渲染结果。

所有用例不依赖真实行情/网络：AppTest 仅读取本机 SQLite 缓存（与既有
``test_app_ui.py`` 相同前提）；其余用例使用 mock / tmp_path。
"""

import gc
import json
import os
import sys
import threading
from unittest.mock import MagicMock

import pytest

# ===========================================================================
# 1. 常量 — 单一真源与网格尺寸
# ===========================================================================

class TestSixViewConstants:
    """``DEFAULT_TFS`` / ``NUM_VIEWS`` / 网格常量的契约。"""

    def test_default_tfs_length_and_legacy_bindings(self):
        """长度 6；前四项仍是历史绑定，v4/v5 为追加的周线/月线。"""
        from filter.shared.constants import DEFAULT_TFS

        assert len(DEFAULT_TFS) == 6
        # v0~v3 被已存预设的 v{i}_tf 键绑定，不可重排
        assert DEFAULT_TFS[:4] == ["日线", "60分钟", "15分钟", "5分钟"]
        assert DEFAULT_TFS[4] == "周线"
        assert DEFAULT_TFS[5] == "月线"

    def test_num_views_and_grid_are_2x3(self):
        """NUM_VIEWS==6，网格 2 行 × 3 列且恰好容纳全部视图。"""
        from filter.shared.constants import (
            DEFAULT_TFS,
            NUM_VIEWS,
            VIEW_GRID_COLS,
            VIEW_GRID_ROWS,
        )

        assert NUM_VIEWS == 6 == len(DEFAULT_TFS)
        assert VIEW_GRID_ROWS == 2
        assert VIEW_GRID_COLS == 3
        assert VIEW_GRID_ROWS * VIEW_GRID_COLS == NUM_VIEWS

    def test_filter_package_reexports_view_constants(self):
        """``filter`` 包 re-export 了视图数/网格常量（browse/backtest 共用入口）。"""
        import filter as fpkg
        import filter.shared.constants as constants

        for name in ("NUM_VIEWS", "VIEW_GRID_COLS", "VIEW_GRID_ROWS"):
            assert name in fpkg.__all__, f"filter.__all__ 缺少 {name}"
            assert getattr(fpkg, name) == getattr(constants, name)


# ===========================================================================
# 2. 参数面板：2×3 网格 + 6 cfg + 首次默认周期去重
# ===========================================================================

def _render_panels_with_state(monkeypatch, initial_state: dict):
    """用假 ``st`` 渲染 ``_render_param_panels``，返回 (configs, 列宽, captions)。

    假 ``_render_params`` 在返回 cfg 前把 ``tf_default`` 写回
    ``session_state[f"{prefix}_tf"]`` —— 真实 ``st.selectbox(key=...)``
    实例化后就会把当前值写进 session_state，后续面板据此做去重；
    不模拟这一步会得到「每个视图都取 DEFAULT_TFS[0]」的假象。
    """
    import filter.browse.sidebar as sidebar

    st_mock = MagicMock()
    st_mock.session_state = dict(initial_state)
    column_widths: list[int] = []
    captions: list[str] = []

    def fake_columns(n):
        column_widths.append(n)
        return [MagicMock() for _ in range(n)]

    def fake_render_params(prefix, filter_id, dual, filter_id2, tf_default):
        st_mock.session_state[f"{prefix}_tf"] = tf_default
        return {"tf": tf_default}

    st_mock.columns = fake_columns
    st_mock.caption = captions.append

    monkeypatch.setattr(sidebar, "st", st_mock)
    monkeypatch.setattr(sidebar, "_render_params", fake_render_params)

    configs = sidebar._render_param_panels("sma", False, None)
    return configs, column_widths, captions


class TestParamPanelsGrid:
    """``_render_param_panels`` 的网格尺寸、``_vid`` 与默认周期规则。"""

    def test_empty_session_uses_default_tfs(self, monkeypatch):
        """空会话：6 个面板按 2×3 排列，默认周期即 DEFAULT_TFS 顺序。"""
        from filter.shared.constants import DEFAULT_TFS

        configs, widths, captions = _render_panels_with_state(monkeypatch, {})

        assert widths == [3, 3], f"应为 2 行、每行 3 列，实际列宽序列={widths}"
        assert len(configs) == 6
        assert [c["_vid"] for c in configs] == list(range(6))
        assert [c["tf"] for c in configs] == DEFAULT_TFS
        assert captions == [f"视图{i + 1} · {DEFAULT_TFS[i]}" for i in range(6)]

    def test_daily_weekly_preset_fills_v4_v5_with_first_unused(self, monkeypatch):
        """旧预设占用 15/60/日/周 → v4=5分钟、v5=月线，6 个周期不重复。"""
        state = {
            "v0_tf": "15分钟", "v1_tf": "60分钟",
            "v2_tf": "日线", "v3_tf": "周线",
        }
        configs, widths, _ = _render_panels_with_state(monkeypatch, state)

        tfs = [c["tf"] for c in configs]
        assert widths == [3, 3]
        assert tfs[:4] == ["15分钟", "60分钟", "日线", "周线"], "已有预设值不得被覆盖"
        assert tfs[4] == "5分钟", "v4 应取 DEFAULT_TFS 中首个未被占用的周期"
        assert tfs[5] == "月线"
        assert len(set(tfs)) == 6, f"6 个视图周期不得重复: {tfs}"

    def test_minute_preset_fills_v4_v5_from_remaining(self, monkeypatch):
        """旧预设占用 1/5/15/60 分钟 → v4=日线、v5=周线（首个未占用）。"""
        state = {
            "v0_tf": "1分钟", "v1_tf": "5分钟",
            "v2_tf": "15分钟", "v3_tf": "60分钟",
        }
        configs, _, _ = _render_panels_with_state(monkeypatch, state)

        tfs = [c["tf"] for c in configs]
        assert tfs[:4] == ["1分钟", "5分钟", "15分钟", "60分钟"]
        assert tfs[4] == "日线"
        assert tfs[5] == "周线"
        assert len(set(tfs)) == 6

    def test_existing_v4_value_is_not_overwritten(self, monkeypatch):
        """已存在的 v4_tf（用户设置/预设）必须原样保留，v5 继续补首个未占用周期。"""
        state = {
            "v0_tf": "15分钟", "v1_tf": "60分钟",
            "v2_tf": "日线", "v3_tf": "周线",
            "v4_tf": "月线",
        }
        configs, _, captions = _render_panels_with_state(monkeypatch, state)

        assert configs[4]["tf"] == "月线", "已存在的 v4_tf 不应被默认值覆盖"
        assert captions[4] == "视图5 · 月线"
        # 前 5 个视图已占用 15/60/日/周/月，v5 只能补 5分钟
        assert configs[5]["tf"] == "5分钟"
        assert len({c["tf"] for c in configs}) == 6


def _seed_view_tfs(monkeypatch, initial_state: dict) -> dict:
    """用假 ``st`` 调 ``ensure_view_tf_defaults``，返回种子化后的 session_state。"""
    import filter.browse.sidebar as sidebar

    st_mock = MagicMock()
    st_mock.session_state = dict(initial_state)
    monkeypatch.setattr(sidebar, "st", st_mock)
    sidebar.ensure_view_tf_defaults()
    return st_mock.session_state


class TestEnsureViewTfDefaults:
    """``ensure_view_tf_defaults``（T3 major 修复）：侧栏候选与面板同源、首屏即完整。"""

    def test_empty_session_seeds_default_tfs(self, monkeypatch):
        """空 session_state → 种子化出 DEFAULT_TFS 顺序的 v0~v5。"""
        from filter.shared.constants import DEFAULT_TFS

        state = _seed_view_tfs(monkeypatch, {})

        assert [state[f"v{i}_tf"] for i in range(6)] == DEFAULT_TFS
        assert len(set(state.values())) == 6

    def test_preset_fills_v4_5min_and_v5_monthly(self, monkeypatch):
        """已有 v0~v3=15/60/日/周（旧预设）→ v4=5分钟、v5=月线，不重复。"""
        state = _seed_view_tfs(monkeypatch, {
            "v0_tf": "15分钟", "v1_tf": "60分钟",
            "v2_tf": "日线", "v3_tf": "周线",
        })

        tfs = [state[f"v{i}_tf"] for i in range(6)]
        assert tfs == ["15分钟", "60分钟", "日线", "周线", "5分钟", "月线"]
        assert len(set(tfs)) == 6

    def test_existing_v4_value_is_not_overwritten(self, monkeypatch):
        """已设 v4=季线 → 保持季线；v5 仍按「首个未占用周期」补位。"""
        state = _seed_view_tfs(monkeypatch, {
            "v0_tf": "15分钟", "v1_tf": "60分钟",
            "v2_tf": "日线", "v3_tf": "周线",
            "v4_tf": "季线",
        })

        assert state["v4_tf"] == "季线", "已存在的用户/预设值不得被覆盖"
        # 已占用 15/60/日/周/季线，DEFAULT_TFS 顺序中的首个未占用项 = 5分钟
        assert state["v5_tf"] == "5分钟"
        assert len({state[f"v{i}_tf"] for i in range(6)}) == 6

    def test_empty_string_tf_is_seeded(self, monkeypatch):
        """空字符串视作未初始化（``not st.session_state.get(key)``），应被种子化。"""
        from filter.shared.constants import DEFAULT_TFS

        state = _seed_view_tfs(monkeypatch, {"v4_tf": ""})

        assert [state[f"v{i}_tf"] for i in range(6)] == DEFAULT_TFS



def _seed_view_strategies(monkeypatch, initial_state: dict) -> dict:
    """用假 ``st`` 调 ``ensure_view_strategy_defaults``。"""
    import filter.browse.sidebar as sidebar

    st_mock = MagicMock()
    st_mock.session_state = dict(initial_state)
    monkeypatch.setattr(sidebar, "st", st_mock)
    sidebar.ensure_view_strategy_defaults()
    return st_mock.session_state


class TestEnsureViewStrategyDefaults:
    """新视图策略开关继承（修复「视图5/6 没有策略 PnL 与多空持仓带」）。"""

    def test_new_views_inherit_previous_view_flags(self, monkeypatch):
        """v3 的策略开关为开 → v4/v5 首次渲染时继承（含止损值）。"""
        state = _seed_view_strategies(monkeypatch, {
            "v3_strat": True, "v3_cross_pnl": True,
            "v3_align": True, "v3_pnlfb": True, "v3_sl": 2.5,
        })

        for i in (4, 5):
            assert state[f"v{i}_strat"] is True
            assert state[f"v{i}_cross_pnl"] is True
            assert state[f"v{i}_align"] is True
            assert state[f"v{i}_pnlfb"] is True
            assert state[f"v{i}_sl"] == 2.5

    def test_existing_value_is_not_overwritten(self, monkeypatch):
        """用户/预设显式设置的值不得被继承覆盖；v5 从 v4 继承。"""
        state = _seed_view_strategies(monkeypatch, {
            "v3_strat": True, "v3_sl": 2.0,
            "v4_strat": False, "v4_sl": 5.0,
        })

        assert state["v4_strat"] is False
        assert state["v4_sl"] == 5.0
        assert state["v5_strat"] is False
        assert state["v5_sl"] == 5.0

    def test_empty_session_stays_off(self, monkeypatch):
        """空会话没有任何视图设置 → 不凭空打开策略开关。"""
        state = _seed_view_strategies(monkeypatch, {})

        assert state.get("v4_strat", False) is False
        assert state.get("v5_strat", False) is False


# ===========================================================================
# 3. 图表网格 — AppTest 真实渲染
# ===========================================================================

_REAL_STREAMLIT = None

_PROJECT_STEMS = {
    "filter", "browse", "state", "streamlit_app", "services", "db",
    "config_db", "data", "charts", "types", "config", "signals",
    "components", "engine", "shared", "scripts",
}


def _restore_real_streamlit() -> None:
    """把 conftest 注入的 streamlit MagicMock 换回真实包，并清掉旧项目模块。"""
    import importlib

    import streamlit as _st
    if "MagicMock" in type(_st).__name__:
        del sys.modules["streamlit"]
        for mod in list(sys.modules):
            if mod.startswith("streamlit."):
                del sys.modules[mod]
        importlib.import_module("streamlit")
    for mod in list(sys.modules):
        if mod.split(".")[0] in _PROJECT_STEMS:
            del sys.modules[mod]


def _local_cache_skip_reason(ticker: str = "AAPL") -> str | None:
    """返回跳过真实 AppTest 的原因；``None`` 表示本机有可用行情缓存。

    判据是本地 SQLite（``data/market.db``，gitignored）已存在且含 ``ticker``
    数据。**不调用任何抓取函数**：DB 文件不存在时连 ``sqlite3.connect`` 都不做，
    避免在干净检出/CI 上创建空库或触发 ``_fetch_all_timeframes``。
    """
    from pathlib import Path

    from filter.data import db as db_mod

    db_path = Path(db_mod.DB_PATH)
    if not db_path.exists():
        return (
            f"本机无行情缓存 {db_path}（干净检出/CI）；真实 AppTest 需要 "
            f"data/market.db 中的 {ticker} 缓存且绝不触发网络抓取，已跳过"
        )
    try:
        if not db_mod.has_data(ticker):
            return (
                f"本机行情缓存 {db_path} 中无 {ticker} 数据；"
                "跳过真实 AppTest（绝不触发网络抓取）"
            )
    except Exception as exc:  # 空库/坏库/缺 kline 表等
        return (
            f"本机行情缓存 {db_path} 不可读（{type(exc).__name__}: {exc}）；"
            "跳过真实 AppTest（绝不触发网络抓取）"
        )
    return None


def _require_local_market_cache(ticker: str = "AAPL") -> None:
    """AppTest fixture 的统一入口：无本机缓存时 pytest.skip（不下网、不建库）。"""
    reason = _local_cache_skip_reason(ticker)
    if reason is not None:
        pytest.skip(reason)


@pytest.fixture(scope="module")
def six_view_app():
    """真实 AppTest：渲染一次 browse/app.py，供 6 面板/6 图表/操作周期用例读取。

    入口先确认本机有 ``data/market.db`` 的 AAPL 缓存，否则 ``pytest.skip``：
    CI（``pytest tests/ --ignore=tests/test_app_ui.py``）是干净检出、无该文件，
    AppTest 会触发真实 yfinance 抓取；skip 保证 CI 不联网、不因断网/限流失败。
    """
    global _REAL_STREAMLIT

    _require_local_market_cache()
    _restore_real_streamlit()
    import streamlit as real_st
    _REAL_STREAMLIT = real_st
    from streamlit.testing.v1 import AppTest

    app_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "filter"))
    script = os.path.join(app_dir, "browse", "app.py")
    sys.path.insert(0, app_dir)
    cwd = os.getcwd()
    os.chdir(app_dir)
    try:
        at = AppTest.from_file(script)
    finally:
        os.chdir(cwd)
    at.run(timeout=120)

    yield at

    try:
        delattr(threading.current_thread(), "streamlit_script_run_ctx")
    except AttributeError:
        pass
    del at
    gc.collect()


class TestChartGridRendered:
    """真实 AppTest：6 个参数面板控件 + 6 个图表窗口，且无异常。"""

    def test_six_tf_selectboxes_six_captions_six_charts(self, six_view_app):
        """断言点：v0~v5 六个 tf selectbox、6 条视图 caption、6 个图表 iframe。

        网格几何由 ``VIEW_GRID_ROWS=2``/``VIEW_GRID_COLS=3``（见常量用例）与
        app.py 的 ``[st.columns(VIEW_GRID_COLS) for _ in range(VIEW_GRID_ROWS)]``
        保证；AppTest 能观测到的是「6 个视图各渲染一次图表、各有一个周期下拉」。
        """
        at = six_view_app
        assert [str(e) for e in at.exception] == []

        tf_keys = [s.key for s in at.selectbox if s.key and s.key.startswith("v") and s.key.endswith("_tf")]
        assert sorted(tf_keys) == [f"v{i}_tf" for i in range(6)], (
            f"参数面板应渲染 6 个视图周期下拉，实际: {tf_keys}"
        )

        captions = [str(c.value) for c in at.caption if str(c.value).startswith("视图")]
        assert len(captions) == 6, f"应有 6 条视图 caption，实际: {captions}"
        for i in range(6):
            assert any(c.startswith(f"视图{i + 1} · ") for c in captions), (
                f"缺少视图{i + 1} 的 caption: {captions}"
            )

        charts = at.get("iframe")
        assert len(charts) == 6, f"应渲染 6 个图表窗口，实际 {len(charts)} 个"

    def test_operating_tf_defaults_to_daily(self, six_view_app):
        """操作周期默认仍为日线（4→6 后 view_tfs 排序会把 index=0 变成月线）。"""
        from filter.shared.constants import ALL_TFS

        at = six_view_app
        op = at.selectbox(key="operating_tf")
        assert op.value == "日线", f"操作周期默认应为日线，实际 {op.value!r}"
        assert len(op.options) == len(set(op.options)), f"操作周期候选重复: {op.options}"
        assert all(tf in ALL_TFS for tf in op.options)

    def test_first_screen_sidebar_period_options_include_new_views(self, six_view_app):
        """首屏单次 run 侧栏两个下拉即含全部 6 个视图周期（守种子化）。

        回归点（T3 major）：修复前 operating_tf / BS 候选只覆盖 v0~v3，
        新视图周期必须第二次 rerun 才补齐。这里不绑定某个具体预设的内容
        （本机预设可能被用户修改），而是断言「侧栏候选集合 == 首屏 6 个
        视图面板实际周期集合」，且首屏只 run 一次即可用。
        """
        at = six_view_app
        assert [str(e) for e in at.exception] == []

        captions = [str(c.value) for c in at.caption if str(c.value).startswith("视图")]
        view_tfs = [c.split(" · ", 1)[1].strip() for c in captions]
        assert len(view_tfs) == 6, f"应有 6 条视图 caption: {captions}"

        op = at.selectbox(key="operating_tf")
        assert op.value == "日线"
        assert set(op.options) == set(view_tfs), (
            f"首屏操作周期候选应与 6 个视图周期一致: options={op.options}, views={view_tfs}"
        )
        assert len(op.options) == 6, f"首屏操作周期应为 6 项: {op.options}"

        bs = at.selectbox(key="bs_monitor_tf_selector")
        assert bs.options[0] == "不选择"
        assert set(bs.options[1:]) == set(view_tfs), (
            f"首屏 BS 监测候选应与 6 个视图周期一致: options={bs.options}, views={view_tfs}"
        )

        assert len(at.get("iframe")) == 6, "6 个图表窗口应全部渲染"


class TestAppTestSkipGuard:
    """无本机行情缓存时 AppTest 用例必须在 fixture 入口 skip（CI 不联网）。"""

    def test_missing_market_db_skips_without_creating_file(self, monkeypatch, tmp_path):
        """market.db 不存在 → 返回 skip 原因、抛 Skipped，且探针不创建 DB 文件。"""
        from filter.data import db as db_mod

        missing = tmp_path / "market.db"
        monkeypatch.setattr(db_mod, "DB_PATH", missing)

        reason = _local_cache_skip_reason()
        assert reason is not None, "无 market.db 时不得判定缓存可用"
        assert "跳过" in reason and "缓存" in reason
        assert not missing.exists(), "缓存探针不得创建数据库（否则会污染干净检出）"

        with pytest.raises(pytest.skip.Exception) as excinfo:
            _require_local_market_cache()
        assert "跳过" in str(excinfo.value)

    def test_empty_db_without_kline_table_skips(self, monkeypatch, tmp_path):
        """DB 存在但无 kline 表/无 AAPL → 返回 skip 原因而非抛异常/下网。"""
        import sqlite3

        from filter.data import db as db_mod

        empty_db = tmp_path / "market.db"
        conn = sqlite3.connect(empty_db)
        conn.close()
        monkeypatch.setattr(db_mod, "DB_PATH", empty_db)

        reason = _local_cache_skip_reason()
        assert reason is not None
        assert "跳过" in reason
        with pytest.raises(pytest.skip.Exception):
            _require_local_market_cache()


# ===========================================================================
# 4. 配置导出 / 收集覆盖 v0~v5
# ===========================================================================

class TestExportCollectSixViews:
    """``_render_export_config`` 的导出 JSON 与 ``collect_current_params`` 含 v4/v5。"""

    def test_export_config_json_includes_v4_v5(self, monkeypatch):
        """导出 JSON 含 v4/v5 的全部持久化键与滤波参数键，值取自各 cfg。"""
        import filter.browse.sidebar as sidebar
        from filter.backtest.cli import _build_default_configs
        from filter.data.config_db import VIEW_PARAM_SUFFIXES

        # app.py 传给 _render_export_config 的 configs 来自 _render_param_panels，
        # 即「视图序」v0..v5（不是 CLI 的粗→细序）；按 _vid 重排以还原调用点语义。
        configs = sorted(_build_default_configs("TEST_TICKER"), key=lambda c: c["_vid"])
        captured: dict = {}

        st_mock = MagicMock()
        st_mock.sidebar.download_button.side_effect = (
            lambda label, data, **kw: captured.update(label=label, data=data)
        )
        monkeypatch.setattr(sidebar, "st", st_mock)

        sidebar._render_export_config(configs, "savgol", "ema", True, "美股 US", "TEST_TICKER")

        exported = json.loads(captured["data"])
        for i in range(6):
            for suffix in VIEW_PARAM_SUFFIXES:
                assert f"v{i}_{suffix}" in exported, f"导出缺少 v{i}_{suffix}"
        assert exported["v4_tf"] == "周线"
        assert exported["v5_tf"] == "月线"
        assert exported["v0_tf"] == "日线"
        # 每视图滤波参数：savgol(pv) + ema(pv2) 中文标签键
        assert exported["窗口大小_v4_f1_savgol"] == 21
        assert exported["跨度_v5_f2_ema"] == 10

    def test_collect_current_params_includes_v4_v5(self, monkeypatch):
        """session_state 注入 v4/v5 键后，DB 收集能原样取到（含 pnlfb/数值键）。"""
        import streamlit as st

        from filter.shared.constants import DEFAULT_TFS

        fake_state = {f"v{i}_tf": tf for i, tf in enumerate(DEFAULT_TFS)}
        fake_state.update({
            "global_f": "savgol",
            "v4_ke": 0.21, "v5_sm": 0.07,
            "v4_pnlfb": True, "v5_n": 88,
        })
        monkeypatch.setattr(st, "session_state", fake_state)

        from filter.data.config_db import collect_current_params
        params = collect_current_params()

        assert params["v4_tf"] == "周线"
        assert params["v5_tf"] == "月线"
        assert params["v4_ke"] == 0.21
        assert params["v5_sm"] == 0.07
        assert params["v4_pnlfb"] is True
        assert params["v5_n"] == 88
        assert params["global_f"] == "savgol"


# ===========================================================================
# 5. CLI 2B — 视图数随预设收缩 + _vid 与 tf 对应
# ===========================================================================

_OLD_4_TFS = ["日线", "60分钟", "15分钟", "5分钟"]


def _old_four_key_preset() -> dict:
    params = {"global_f": "sma", "global_dual": False, "global_f2": None}
    for i, tf in enumerate(_OLD_4_TFS):
        params[f"v{i}_tf"] = tf
        params[f"v{i}_n"] = 120
    return params


class TestCliPresetViewCount:
    """``_preset_view_count`` / ``_build_configs_from_params`` / ``_build_default_configs``。"""

    def test_preset_view_count(self):
        """无 tf 键 → NUM_VIEWS；v0~v2 → 3；旧 4 键预设 → 4。"""
        from filter.backtest.cli import _preset_view_count
        from filter.shared.constants import NUM_VIEWS

        assert _preset_view_count({}) == NUM_VIEWS == 6
        assert _preset_view_count({"v0_tf": "日线"}) == 1
        assert _preset_view_count({
            "v0_tf": "日线", "v1_tf": "60分钟", "v2_tf": "15分钟",
        }) == 3
        assert _preset_view_count(_old_four_key_preset()) == 4

    def test_old_four_key_preset_builds_four_unique_configs(self):
        """仅含 v0~v3_tf 的旧预设：4 个配置、无 KeyError、tf 不重复。"""
        from filter.backtest.cli import _build_configs_from_params
        from filter.shared.constants import ALL_TFS, DEFAULT_TFS

        configs = _build_configs_from_params(_old_four_key_preset())

        assert len(configs) == 4
        assert [c["tf"] for c in configs] == _OLD_4_TFS
        assert [c["_vid"] for c in configs] == [0, 1, 2, 3]
        assert len({c["tf"] for c in configs}) == 4, "旧 4 视图预设不得产生重复周期"
        assert [ALL_TFS.index(c["tf"]) for c in configs] == sorted(
            [ALL_TFS.index(c["tf"]) for c in configs], reverse=True)
        for cfg in configs:
            assert cfg["_vid"] == DEFAULT_TFS.index(cfg["tf"])

    def test_empty_params_builds_six_configs_coarse_to_fine(self):
        """``{}`` → 6 个配置，tf 顺序 月/周/日/60/15/5，``_vid`` 保留视图编号。"""
        from filter.backtest.cli import _build_configs_from_params
        from filter.shared.constants import DEFAULT_TFS

        configs = _build_configs_from_params({})

        assert len(configs) == 6
        assert [c["tf"] for c in configs] == [
            "月线", "周线", "日线", "60分钟", "15分钟", "5分钟",
        ]
        assert [c["_vid"] for c in configs] == [5, 4, 0, 1, 2, 3]
        assert len({c["tf"] for c in configs}) == 6
        for cfg in configs:
            assert cfg["_vid"] == DEFAULT_TFS.index(cfg["tf"]), (
                f"cfg 的 _vid 应等于其 tf 在 DEFAULT_TFS 中的索引: {cfg['tf']}"
            )

    def test_missing_tf_keys_do_not_raise_keyerror(self):
        """预设只有部分 tf 键时，缺键视图回退首个未占用周期而非 KeyError。

        真实场景：用户导出/旧预设只写了部分视图的 tf；修复前排序会抛
        ``KeyError: 'tf'``。此处 3 视图，v1 缺 tf → 回退 60分钟。
        """
        from filter.backtest.cli import _build_configs_from_params

        configs = _build_configs_from_params({
            "global_f": "sma",
            "v0_tf": "15分钟", "v1_ke": 0.2, "v2_tf": "日线",
        })

        assert len(configs) == 3
        tfs = [c["tf"] for c in configs]
        assert len(set(tfs)) == 3, f"回退后周期不得重复: {tfs}"
        by_vid = {c["_vid"]: c["tf"] for c in configs}
        assert by_vid[0] == "15分钟"
        assert by_vid[1] == "60分钟", f"v1 缺 tf 应回退首个未占用周期，实际: {tfs}"
        assert by_vid[2] == "日线"

    def test_build_default_configs_six_with_vid_mapping(self):
        """``_build_default_configs('X')`` → 6 个且每个 ``_vid`` 与 tf 对应正确。"""
        from filter.backtest.cli import _build_default_configs
        from filter.shared.constants import DEFAULT_TFS, NUM_VIEWS

        configs = _build_default_configs("X")

        assert len(configs) == NUM_VIEWS == 6
        assert [c["tf"] for c in configs] == [
            "月线", "周线", "日线", "60分钟", "15分钟", "5分钟",
        ]
        for cfg in configs:
            assert cfg["_vid"] == DEFAULT_TFS.index(cfg["tf"])
        assert {c["_vid"] for c in configs} == set(range(NUM_VIEWS))


# ===========================================================================
# 6. BS 监测周期选项覆盖 v4/v5
# ===========================================================================

class TestBSMonitorPeriodOptions:
    """``_render_refresh_row`` 的 BS 监测下拉应收集全部 6 个视图周期。"""

    def test_bs_options_include_weekly_and_monthly(self, monkeypatch):
        """v4=周线、v5=月线时，下拉选项包含周线/月线且无重复。"""
        import filter.browse.sidebar as sidebar
        from filter.shared.constants import NUM_VIEWS

        class _FakeAppState:
            values = {
                f"v{i}_tf": tf for i, tf in enumerate(
                    ["1分钟", "5分钟", "15分钟", "60分钟", "周线", "月线"])
            }

            @classmethod
            def get(cls, key, default=None):
                return cls.values.get(key, default)

            @classmethod
            def set(cls, key, value):
                cls.values[key] = value

        st_mock = MagicMock()
        st_mock.sidebar.columns.return_value = [MagicMock(), MagicMock()]
        st_mock.button.return_value = False
        st_mock.checkbox.return_value = False
        st_mock.sidebar.selectbox.return_value = "不选择"
        monkeypatch.setattr(sidebar, "st", st_mock)
        monkeypatch.setattr(sidebar, "AppState", _FakeAppState)

        sidebar._render_refresh_row("美股 US", "AAPL")

        bs_calls = [
            c for c in st_mock.sidebar.selectbox.call_args_list
            if c.args and c.args[0] == "BS监测周期"
        ]
        assert len(bs_calls) == 1, "BS 监测周期下拉应只渲染一次"
        options = bs_calls[0].args[1]

        assert options[0] == "不选择"
        assert "周线" in options, f"BS 选项缺少 v4 的周线: {options}"
        assert "月线" in options, f"BS 选项缺少 v5 的月线: {options}"
        assert len(options) == 1 + NUM_VIEWS, f"应收集 6 个视图周期: {options}"
        assert len(options) == len(set(options)), f"BS 选项重复: {options}"


# ===========================================================================
# 7. state.py 的 _imp_ 清理：保留 v4/v5，删除 v6+
# ===========================================================================

class TestImpCleanupSixViews:
    """``AppState.cleanup_orphaned_imp_keys`` 的视图索引范围随 6 视图扩展。"""

    def test_keeps_v4_v5_removes_out_of_range(self):
        """v4/v5 的 ``_imp_`` 键保留；v6/v99（越界）与非法 suffix 删除。"""
        from filter.shared.state import AppState, DictStateStore

        store = DictStateStore({
            "_imp_v0_tf": "日线",
            "_imp_v3_ke": 0.15,
            "_imp_v4_tf": "周线",
            "_imp_v4_ke": 0.20,
            "_imp_v5_tf": "月线",
            "_imp_v5_sm": 0.05,
            "_imp_v6_tf": "季线",
            "_imp_v6_ke": 0.30,
            "_imp_v99_tf": "周线",
            "_imp_v5_oldparam": 123,
            "_imp_f_schmitt_ke": 0.15,
        })

        AppState.cleanup_orphaned_imp_keys(store=store)

        assert "_imp_v4_tf" in store, "vi=4 在 0..5 范围内，应保留"
        assert "_imp_v4_ke" in store
        assert "_imp_v5_tf" in store
        assert "_imp_v5_sm" in store
        assert "_imp_v6_tf" not in store, "vi=6 越界，应删除"
        assert "_imp_v6_ke" not in store
        assert "_imp_v99_tf" not in store
        assert "_imp_v5_oldparam" not in store, "非法 suffix 应删除"
        assert "_imp_v0_tf" in store
        assert "_imp_v3_ke" in store
        assert "_imp_f_schmitt_ke" in store, "非 view-pattern 的 _imp_ key 应保留"


# ===========================================================================
# 8. _vid 贯通 — ParquetStore 列前缀/列数 + 配置哈希
# ===========================================================================

class TestVidPlumbing:
    """``cfg["_vid"]`` → ParquetStore 列前缀；``_config_hash`` 忽略 ``_vid``。"""

    def test_store_prefixes_and_column_count(self, tmp_path):
        """browse 视图序传入 6 配置 → 前缀 v0..v5、3+6*12=75 列、labels 对得上。"""
        from filter.data.store import ParquetStore
        from filter.shared.constants import DEFAULT_TFS, NUM_VIEWS

        configs = [
            {"_vid": i, "tf": DEFAULT_TFS[i], "n_pts": 120}
            for i in range(NUM_VIEWS)
        ]
        store = ParquetStore(str(tmp_path), "TEST", configs)
        store.start_session()

        assert store._view_prefixes == [f"v{i}" for i in range(NUM_VIEWS)]
        assert len(store._full_schema) == 3 + NUM_VIEWS * 12 == 75
        assert len(store._column_names) == 75
        assert store._column_names[:3] == ["bar_index", "bar_timestamp", "close"]

        meta = json.loads((store.output_dir / "metadata.json").read_text(encoding="utf-8"))
        assert meta["views"] == [f"v{i}" for i in range(NUM_VIEWS)]
        assert meta["view_labels"] == {
            f"v{i}": DEFAULT_TFS[i] for i in range(NUM_VIEWS)
        }

    def test_config_hash_ignores_vid_but_detects_real_change(self):
        """仅 ``_vid`` 不同的 configs 哈希相同；真实参数变化则哈希不同。

        用 ``object.__new__`` 跳过 ``BacktestRunner.__init__`` 的 DB 查询，
        只验证 ``_config_hash()`` 的真实实现（不依赖 data/market.db）。
        """
        from filter.backtest.engine import BacktestRunner

        base = {"tf": "日线", "n_pts": 120, "_fid": "savgol", "pv": {"window": 21, "order": 2}}
        runner = object.__new__(BacktestRunner)
        runner.configs = [dict(base, _vid=0)]
        hash_vid0 = runner._config_hash()
        runner.configs = [dict(base, _vid=5)]
        hash_vid5 = runner._config_hash()
        assert hash_vid0 == hash_vid5, "仅 _vid 不同不应改变配置哈希（兼容旧断点）"

        runner.configs = [dict(base, _vid=0, n_pts=121)]
        assert runner._config_hash() != hash_vid0, "真实参数变化必须改变配置哈希"
