"""
回测控制面板组件 — 回测模式切换、窗口导航、自动播放

提供:
- render_backtest_panel(market, ticker_code, configs) — 渲染侧边栏回测面板
- run_backtest_play() — 回测自动播放帧前进（main 循环顶部调用）
"""

import json
import time
from datetime import datetime
from pathlib import Path

import streamlit as st
from loguru import logger

from state import AppState
from services.backtest_logger import log_mode_switch
from components.sidebar import ALL_TFS


# ============================================================================
# 数据库查询辅助
# ============================================================================

def _get_bar_date_from_db(ticker_code, tf, bar_index):
    """Query the date string for a given bar index from the database.

    Parameters
    ----------
    ticker_code : str
        Ticker symbol to query.
    tf : str
        Timeframe label (e.g. ``"日线"``).
    bar_index : int
        Zero-based row offset (``OFFSET`` in the SQL query).

    Returns
    -------
    str
        Date string from ``kline.ts`` for the bar at the requested
        offset, or an empty string if no row is found.
    """
    from db import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT ts FROM kline WHERE ticker=? AND timeframe=? ORDER BY ts ASC LIMIT 1 OFFSET ?",
            (ticker_code, tf, bar_index),
        ).fetchone()
    return row[0] if row else ""


# ============================================================================
# 回测配置缓存
# ============================================================================

def _save_backtest_config(ticker_code, min_tf, bar_count, window_size):
    """保存回测配置到 JSON 文件。"""
    config_path = Path(__file__).parent.parent.parent / "data" / f"backtest_config_{ticker_code}.json"
    config = {
        "ticker": ticker_code,
        "min_tf": min_tf,
        "bar_count": bar_count,
        "window_size": window_size,
        "cached_at": datetime.now().isoformat(),
    }
    with open(config_path, "w") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def _load_backtest_config(ticker_code):
    """加载回测配置缓存。ticker匹配时返回配置，否则返回None。"""
    config_path = Path(__file__).parent.parent.parent / "data" / f"backtest_config_{ticker_code}.json"
    if config_path.exists():
        try:
            with open(config_path) as f:
                config = json.load(f)
            if config.get("ticker") == ticker_code:
                return config
        except Exception:
            pass
    return None


# ============================================================================
# 视图配置辅助
# ============================================================================

def _get_min_tf_and_count(configs, ticker_code) -> tuple:
    """从4个视图的tf配置中确定最小周期（最精细）和总bar数。
    遍历configs中各视图的tf字段，取ALL_TFS中索引最小的（最精细的）为_min_tf。
    从 DB 查询该 tf 的全量 bar 数（不使用 parquet，parquet 只有窗口数据）。
    返回 (_min_tf, bar_count)。"""
    if not configs:
        return "", 0

    # 取索引最小的 tf（最精细周期）
    min_idx = len(ALL_TFS)
    min_tf = ""
    for cfg in configs:
        tf = cfg.get("tf", "")
        try:
            idx = ALL_TFS.index(tf)
            if idx < min_idx:
                min_idx = idx
                min_tf = tf
        except ValueError:
            continue

    if not min_tf:
        return "", 0

    # 从 DB 查询全量 bar 数（parquet 只含当前窗口数据，不能用作 slider 上限）
    try:
        from db import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM kline WHERE ticker=? AND timeframe=?",
                (ticker_code, min_tf),
            ).fetchone()
            bar_count = row[0] if row else 0
    except Exception:
        bar_count = 0

    return min_tf, bar_count


# ============================================================================
# 导航回调
# ============================================================================

def _update_cutoff_and_rerun():
    """Update ``cutoff_date`` from the current bar index and trigger a rerun.

    Called when the user clicks a backtest navigation button (⏮ ◀ ▶ ⏭).
    Reads ``_bar_index`` from ``st.session_state``, looks up the
    corresponding date in the DB for the minimum timeframe, writes it
    to ``AppState._bt_cutoff_date``, and calls ``st.rerun()``.

    Parameters
    ----------
    None — reads from ``st.session_state`` and ``AppState``.

    Returns
    -------
    None
    """
    ticker = AppState.get("_fetched_ticker", "")
    min_tf = AppState.get("_min_tf", "")
    bar_index = st.session_state.get("_bar_index", 0)
    # 同步 widget key，确保 slider 手柄跟随导航按钮移动
    st.session_state._bt_slider_pos = bar_index
    if min_tf and ticker:
        cutoff_date = _get_bar_date_from_db(ticker, min_tf, bar_index - 1)
        if cutoff_date:
            AppState.set("_bt_cutoff_date", cutoff_date)
    st.rerun()


def _on_slider_change():
    """Synchronise slider position to bar index and update cutoff date.

    Streamlit ``on_change`` callback for the backtest slider widget.
    Reads ``_bt_slider_pos`` from ``st.session_state``, writes it to
    ``_bar_index``, and queries the DB for the corresponding cutoff date.

    Skipped during auto-play (``_is_playing`` is ``True``) because
    ``_run_backtest_play`` handles the progression independently.

    Parameters
    ----------
    None — reads from ``st.session_state`` and ``AppState``.

    Returns
    -------
    None
    """
    if AppState.get("_is_playing", False):
        return  # 播放中，避免 on_change 自动 rerun 干扰播放循环
    # 从 Slider Widget Key 读取当前值，同步到程序状态
    slider_val = st.session_state.get("_bt_slider_pos", 0)
    # ★ 若 _bar_index 已匹配 slider 值，说明是导航按钮或播放停止触发的
    # 程序化变更，跳过冗余同步和 DB 查询（_update_cutoff_and_rerun
    # 或 _run_backtest_play 已处理 cutoff_date 更新）。
    if slider_val == st.session_state.get("_bar_index", None):
        return
    st.session_state._bar_index = slider_val
    ticker = AppState.get("_fetched_ticker", "")
    min_tf = AppState.get("_min_tf", "")
    if min_tf and ticker:
        cutoff_date = _get_bar_date_from_db(ticker, min_tf, slider_val - 1)
        if cutoff_date:
            AppState.set("_bt_cutoff_date", cutoff_date)


# ============================================================================
# 导航按钮
# ============================================================================

def _render_backtest_nav(total_bars, min_n_pts):
    """渲染回测导航按钮：⏮ ◀ ▶/⏸ ⏵ ⏭ + 速度"""
    bar_index = st.session_state.get("_bar_index", total_bars)
    is_playing = AppState.get("_is_playing", False)

    col_nav = st.sidebar.columns([1, 1, 1, 1, 1, 2])

    with col_nav[0]:
        if st.button("⏮", key="_bt_goto_start", use_container_width=True,
                     help=f"跳到开头 (bar {min_n_pts})"):
            st.session_state._bar_index = min_n_pts
            _update_cutoff_and_rerun()

    with col_nav[1]:
        if st.button("◀", key="_bt_step_back", use_container_width=True,
                     disabled=bar_index <= min_n_pts, help="后退一个 bar"):
            st.session_state._bar_index = max(min_n_pts, bar_index - 1)
            _update_cutoff_and_rerun()

    with col_nav[2]:
        label = "⏸" if is_playing else "▶"
        help_text = "暂停" if is_playing else "播放"
        if st.button(label, key="_bt_toggle_play", use_container_width=True, help=help_text):
            if is_playing:
                AppState.set("_is_playing", False)
                st.rerun()
            else:
                # 如果已到末尾，从头开始播放
                if bar_index >= total_bars:
                    st.session_state._bar_index = min_n_pts
                # ★ 在任何位置开始播放时，都预更新 cutoff_date
                ticker_v = AppState.get("_fetched_ticker", "")
                min_tf_v = AppState.get("_min_tf", "")
                if min_tf_v and ticker_v:
                    cur = st.session_state.get("_bar_index", total_bars)
                    d = _get_bar_date_from_db(ticker_v, min_tf_v, cur - 1)
                    if d:
                        AppState.set("_bt_cutoff_date", d)
                AppState.set("_is_playing", True)
                st.rerun()

    with col_nav[3]:
        if st.button("⏵", key="_bt_step_fwd", use_container_width=True,
                     disabled=bar_index >= total_bars, help="前进一个 bar"):
            st.session_state._bar_index = min(total_bars, bar_index + 1)
            _update_cutoff_and_rerun()

    with col_nav[4]:
        if st.button("⏭", key="_bt_goto_end", use_container_width=True,
                     help=f"跳到末尾 (bar {total_bars})"):
            st.session_state._bar_index = total_bars
            _update_cutoff_and_rerun()

    with col_nav[5]:
        speeds = ["0.25x", "0.5x", "1x", "2x", "5x", "10x"]
        speed_map = {"0.25x": 0.25, "0.5x": 0.5, "1x": 1.0, "2x": 2.0, "5x": 5.0, "10x": 10.0}
        current = AppState.get("_play_speed_label", "1x")
        idx = speeds.index(current) if current in speeds else 2
        new_speed = st.selectbox("速度", speeds, index=idx, key="_bt_speed", label_visibility="collapsed")
        if new_speed != current:
            AppState.set("_play_speed_label", new_speed)
            AppState.set("_play_speed", speed_map[new_speed])


# ============================================================================
# 自动播放循环
# ============================================================================

def _run_backtest_play():
    """回测自动播放 — 只更新窗口位置，不调用 rerun。

    返回 True 表示需要 main() 末尾 sleep + rerun 触发下一步。
    播放按钮只需设置 _is_playing=True 再 st.rerun()，本函数自动处理前进。
    """
    if not AppState.get("_is_playing", False):
        return False

    # 安全守卫：不在回测模式或 ticker 未就绪时停止播放
    if not AppState.get("_cb_mode", False):
        AppState.set("_is_playing", False)
        return False

    bar_index = st.session_state.get("_bar_index", 0)
    total = AppState.get("_min_tf_bar_count", 0)

    # 安全守卫：bar 总数异常时停止播放
    if total <= 0:
        logger.debug(f"回测播放停止: total={total}, 数据未就绪")
        AppState.set("_is_playing", False)
        return False

    if bar_index >= total:
        logger.debug(f"回测播放停止: bar_index={bar_index} >= total={total}")
        AppState.set("_is_playing", False)
        # ★ 同步 slider 位置，防止播放停止后 slider 显示过期值
        st.session_state._bt_slider_pos = bar_index
        return False

    # 前进一个 bar（在 slider widget 渲染之前，Streamlit 允许修改 widget key）
    st.session_state._bar_index = bar_index + 1

    # 同步更新 cutoff_date
    ticker = AppState.get("_fetched_ticker", "")
    min_tf = AppState.get("_min_tf", "")
    if min_tf and ticker:
        cutoff_date = _get_bar_date_from_db(ticker, min_tf, bar_index)
        if cutoff_date:
            AppState.set("_bt_cutoff_date", cutoff_date)

    return True


# ============================================================================
# 回测面板渲染
# ============================================================================

def _render_backtest_mode(market, ticker_code, configs) -> None:
    """Render the backtest mode toggle and window-position slider in the sidebar.

    Provides a radio button to switch between browse and backtest modes.
    When switching to backtest mode it:
    - Determines the minimum timeframe from the active configs.
    - Queries the total bar count from the DB (or loads a cached config).
    - Initialises ``_bar_index`` and ``_bt_cutoff_date``.
    When switching back to browse mode it clears all backtest-related
    state.

    While in backtest mode it renders navigation buttons (via
    ``_render_backtest_nav``) and a slider (or progress bar during
    auto-play) for the window end position.

    Parameters
    ----------
    market : str
        Market identifier (``"美股 US"``, ``"A股(沪深)"``, ``"港股 HK"``).
    ticker_code : str
        Ticker symbol, e.g. ``"AAPL"``.
    configs : list of dict
        List of per-view configuration dicts, each containing at least
        ``"tf"`` and ``"n_pts"`` keys.

    Returns
    -------
    None
    """
    st.sidebar.markdown("---")
    st.sidebar.caption("🔬 回测模式")

    mode_options = ["浏览模式", "回测模式"]
    mode_index = 1 if AppState.get("_cb_mode", False) else 0
    selected = st.sidebar.radio("模式", mode_options, horizontal=True,
                                 index=mode_index, key="_bt_mode_radio")

    new_cb_mode = (selected == "回测模式")
    old_cb_mode = AppState.get("_cb_mode", False)

    if new_cb_mode != old_cb_mode:
        if new_cb_mode:
            # 切换到回测模式：先尝试从缓存加载配置
            cached = _load_backtest_config(ticker_code)
            if cached:
                min_tf = cached["min_tf"]
                bar_count = cached["bar_count"]
            else:
                # 缓存未命中，从 DB 查询
                min_tf, bar_count = _get_min_tf_and_count(configs, ticker_code)
            AppState.set("_min_tf", min_tf)
            AppState.set("_min_tf_bar_count", bar_count)
            # bar_index = 窗口结束位置，默认在末尾
            AppState.set("_bar_index", bar_count)
            st.session_state._bar_index = bar_count
            # 取 min_tf 视图中最小的 n_pts 作为 slider 范围下限
            min_n_pts = min((cfg["n_pts"] for cfg in configs if cfg["tf"] == min_tf), default=120)
            if not cached:
                _save_backtest_config(ticker_code, min_tf, bar_count, min_n_pts)
            # 初始化 cutoff_date：从 DB 查询 bar_index 位置（最后一条 bar）的日期
            if bar_count > 0 and min_tf:
                cutoff_date = _get_bar_date_from_db(ticker_code, min_tf, bar_count - 1)
                if cutoff_date:
                    AppState.set("_bt_cutoff_date", cutoff_date)
            if bar_count > 0:
                st.toast(f"回测模式已启用  最小周期: {min_tf} ({bar_count} bars)")
            else:
                st.toast("回测模式已启用  数据未就绪")
            try:
                log_mode_switch(ticker_code, "enter", min_tf, bar_count)
            except Exception as e:
                logger.debug(f"回测日志写入失败: {e}")
        else:
            # 切换回浏览模式：清除回测状态
            # 捕获退出前的回测参数（必须在清除前读取）
            _exit_min_tf = AppState.get("_min_tf", "")
            _exit_bar_count = AppState.get("_min_tf_bar_count", 0)
            AppState.set("_bar_index", 0)
            st.session_state._bar_index = 0
            AppState.set("_bt_cutoff_date", "")
            AppState.set("_min_tf", "")
            AppState.set("_min_tf_bar_count", 0)
            try:
                log_mode_switch(ticker_code, "exit", _exit_min_tf, _exit_bar_count)
            except Exception as e:
                logger.debug(f"回测日志写入失败: {e}")

        AppState.set("_cb_mode", new_cb_mode)
        st.rerun()

    # 回测模式下显示窗口位置信息
    if AppState.get("_cb_mode", False):
        bar_index = st.session_state.get("_bar_index", 0)
        total_bars = AppState.get("_min_tf_bar_count", 0)
        min_tf = AppState.get("_min_tf", "")
        # 取 min_tf 视图中最小的 n_pts 作为 slider 范围下限
        min_n_pts = min((cfg["n_pts"] for cfg in configs if cfg["tf"] == min_tf), default=120)

        if total_bars > 0:
            # ── 导航按钮 ──
            _render_backtest_nav(total_bars, min_n_pts)

            # 显示当前窗口信息
            win_start_display = max(1, bar_index - min_n_pts + 1)
            st.sidebar.caption(f"📊 显示 bar {win_start_display} ~ {bar_index} / {total_bars}")

            # 窗口位置进度 indicator
            # 播放期间用进度条替代 Slider（避免 Widget Key 与 _run_backtest_play 的递增冲突）
            if AppState.get("_is_playing", False):
                progress = (bar_index - min_n_pts) / max(1, total_bars - min_n_pts)
                st.sidebar.progress(progress, text=f"播放中... {bar_index}/{total_bars}")
            else:
                # 非播放时：Slider 用独立 key，on_change 同步到 _bar_index
                # 确保 _bar_index 在 slider 有效范围内（防止退出回测后残留 0 值）
                if st.session_state.get("_bar_index", 0) < min_n_pts:
                    st.session_state._bar_index = total_bars
                    bar_index = total_bars  # 同步本地变量，确保 value= 参数正确
                # ★ 同步 widget key 到当前 bar_index。
                # Streamlit 在 widget 有 key 时优先使用 session_state 中的值，
                # 因此必须在渲染前将 _bt_slider_pos 同步到 _bar_index，
                # 否则播放停止、ticker 切换等场景下 slider 会显示过期位置。
                st.session_state._bt_slider_pos = bar_index
                st.sidebar.slider(
                    "窗口结束位置", min_n_pts, total_bars,
                    value=bar_index,
                    key="_bt_slider_pos",
                    on_change=_on_slider_change,
                )
        else:
            st.sidebar.warning("回测数据未就绪，请先在浏览模式加载数据")


# ============================================================================
# 公共 API
# ============================================================================

def run_backtest_play():
    """回测自动播放 — 推进窗口位置（在 main() 顶部调用）。

    返回 True 表示需要在 render 完成后 sleep + rerun。
    """
    return _run_backtest_play()


def render_backtest_panel(market, ticker_code, configs) -> None:
    """Render the backtest control panel in the sidebar.

    处理 ticker 切换时的回测状态刷新 + 渲染回测模式控件。

    Parameters
    ----------
    market : str
        Market identifier.
    ticker_code : str
        Ticker symbol.
    configs : list of dict
        Per-view configuration dicts.

    Returns
    -------
    None
    """
    # ticker 切换后，若处于回测模式则刷新回测状态（需在 _render_backtest_mode 前）
    _prev_bt_ticker = AppState.get("_bt_last_ticker", "")
    if AppState.get("_cb_mode", False) and ticker_code and ticker_code != _prev_bt_ticker:
        # 先尝试从缓存加载配置
        cached = _load_backtest_config(ticker_code)
        if cached:
            min_tf = cached["min_tf"]
            bar_count = cached["bar_count"]
        else:
            min_tf, bar_count = _get_min_tf_and_count(configs, ticker_code)
        if min_tf and bar_count > 0:
            AppState.set("_min_tf", min_tf)
            AppState.set("_min_tf_bar_count", bar_count)
            AppState.set("_bar_index", bar_count)
            st.session_state._bar_index = bar_count
            min_n_pts = min((cfg["n_pts"] for cfg in configs if cfg["tf"] == min_tf), default=120)
            if not cached:
                _save_backtest_config(ticker_code, min_tf, bar_count, min_n_pts)
            cutoff_date = _get_bar_date_from_db(ticker_code, min_tf, bar_count - 1)
            if cutoff_date:
                AppState.set("_bt_cutoff_date", cutoff_date)
        AppState.set("_bt_last_ticker", ticker_code)

    # ── 回测模式切换 ──
    _render_backtest_mode(market, ticker_code, configs)
