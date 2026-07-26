"""
侧边栏 UI 模块 — 从 streamlit_app.py 提取的所有侧边栏渲染函数

依赖: streamlit, config_db, db, state, services/data_loader, constants, components/sidebar
无 Streamlit 页面级状态依赖（除 st.session_state），可独立测试。
"""

import hashlib
import json
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf
from loguru import logger

from filter.data.config_db import (
    apply_preset, delete_preset, get_history, import_json_files_as_presets,
    list_presets, rename_preset, save_preset, VIEW_PARAM_SPECS,
)
from filter.data.db import (
    check_data_health, checkpoint_wal, clear_display_cache, compare_with_db,
    force_update_kline, get_db_size_mb, has_data, init_db, list_snapshots,
    prune_snapshots, restore_snapshot, snapshot_db, validate_db, DB_PATH,
)
from filter.data.loader import _fetch_all_timeframes
from filter.engine.filters import FILTERS
from filter.browse.components_sidebar import _render_params, ALL_TFS, DEFAULT_TFS
from filter.shared.constants import TF_INTERVAL
from filter.shared.state import AppState


# =====================================================================
# Sidebar rendering functions
# =====================================================================


def _handle_pending_apply() -> None:
    """Apply pending preset params from session_state."""
    if AppState.has("_pending_apply_params"):
        params = AppState.pop("_pending_apply_params")
        if params is None:
            return
        for k, v in params.items():
            AppState.set(k, v)
        AppState.set("_import_data", "preset")


def _render_config_import() -> None:
    """Render config file uploader and handle import."""
    uploaded = st.sidebar.file_uploader("导入配置", type=["json"], key="config_import",
                                         label_visibility="collapsed")
    if uploaded is not None:
        raw = uploaded.read()
        file_hash = hashlib.md5(raw).hexdigest()
        if AppState.get("_import_data") != file_hash:
            try:
                config = json.loads(raw)
                for k, v in config.items():
                    AppState.set(k, v)
                AppState.set("_import_data", file_hash)
                logger.info(f"Config imported: {len(config)} keys")
                st.sidebar.success("配置已加载")
            except Exception as e:
                logger.error(f"Config import failed: {e}", exc_info=True)
                st.sidebar.error(f"导入失败: {e}")


def _handle_initial_fetch(market, ticker_code) -> None:
    """Auto-fetch all timeframes on first load."""
    if not AppState.has("_fetched_ticker"):
        AppState.set("_fetched_ticker", "")
    if ticker_code and ticker_code != AppState.get("_fetched_ticker"):
        if not has_data(ticker_code):
            with st.spinner(f"首次获取 {ticker_code} 全部周期数据..."):
                results = _fetch_all_timeframes(market, ticker_code)
                ok = sum(1 for ok, _ in results.values() if ok)
                logger.info(f"Initial fetch: {ticker_code} — {ok}/8 timeframes loaded")
                if ok > 0:
                    st.sidebar.success(f"已获取 {ok}/8 个周期")
        AppState.set("_fetched_ticker", ticker_code)


def _render_refresh_row(market, ticker_code) -> tuple:
    """Render refresh button + auto-refresh checkbox. Returns (auto_refresh, interval)."""
    c_refresh, c_auto = st.sidebar.columns([1, 1.2])
    auto_refresh = False
    interval = 60
    with c_refresh:
        if st.button("刷新数据", use_container_width=True):
            st.cache_data.clear()
            with st.spinner("正在获取全部周期..."):
                results = _fetch_all_timeframes(market, ticker_code)
            ok = sum(1 for r_ok, _ in results.values() if r_ok)
            fail = sum(1 for r_ok, _ in results.values() if not r_ok)
            if ok > 0:
                st.sidebar.success(f"✅ 获取成功 {ok}/8 个周期")
            if fail > 0:
                for tf, (r_ok, detail) in results.items():
                    if not r_ok:
                        st.sidebar.warning(f"❌ {tf}: {detail}")
    with c_auto:
        auto_refresh = st.checkbox("自动刷新", value=False, key="auto_refresh")
    if auto_refresh:
        interval = st.sidebar.slider("刷新间隔(秒)", 10, 600, 60, 10, key="refresh_interval")
    return auto_refresh, interval


def _render_preset_selector(market, ticker_code) -> None:
    """Render preset selector, action buttons, and confirmation flows."""
    st.sidebar.markdown("---")
    search_query = st.sidebar.text_input("🔍 搜索配置", key="preset_search",
                                          placeholder="输入股票代码或名称…")
    all_presets = list_presets()
    if search_query.strip():
        q = search_query.strip().lower()
        presets = [p for p in all_presets if
                   q in p["name"].lower() or
                   q in p.get("description", "").lower()]
    else:
        presets = all_presets
    preset_labels = ["(不选择)"] + [p["name"] for p in presets]
    preset_map = {p["name"]: p for p in presets}

    _hash = hashlib.md5("|".join(preset_labels).encode()).hexdigest()[:8]
    selected_label = st.sidebar.selectbox("📋 配置方案", preset_labels,
                                          key=f"preset_sel_{_hash}")
    selected_preset = preset_map.get(selected_label)

    if selected_preset is None:
        return  # 用户尚未选择预设，不渲染任何操作

    if selected_preset:
        p = selected_preset
        st.sidebar.caption(f"💡 {p['description']}")

        def _cancel_preset_action() -> None:
            AppState.pop("_preset_action")
            AppState.pop("_preset_action_id")

        c1, c2, c3, c4 = st.sidebar.columns([1.2, 1, 1, 0.8])
        with c1:
            # P2-opt: on_click callback — Streamlit auto-reruns, no explicit st.rerun() needed
            def _apply_preset_cb():
                params = apply_preset(p["preset_id"])
                if params:
                    logger.info(f"Preset applied: {p['name']} ({len(params)} params)")
                    AppState.set("_pending_apply_params", params)
                    st.toast(f"已应用: {p['name']}")
            st.button("✅ 应用", key="apply_preset", use_container_width=True,
                      on_click=_apply_preset_cb)
        with c2:
            if st.button("📝 更新", key="update_preset_btn", use_container_width=True):
                AppState.set("_preset_action", "update")
                AppState.set("_preset_action_id", p["preset_id"])
        with c3:
            if st.button("✏️ 重命名", key="rename_preset_btn", use_container_width=True):
                AppState.set("_preset_action", "rename")
                AppState.set("_preset_action_id", p["preset_id"])
        with c4:
            if st.button("🗑️ 删除", key="delete_preset_btn", use_container_width=True):
                AppState.set("_preset_action", "delete")
                AppState.set("_preset_action_id", p["preset_id"])

    _action = AppState.get("_preset_action")
    _action_id = AppState.get("_preset_action_id")
    if _action and _action_id is not None:
        target = next((p for p in presets if p["preset_id"] == _action_id), None)
        if target is None:
            AppState.pop("_preset_action")
            AppState.pop("_preset_action_id")
            st.rerun()
        elif _action == "update":
            st.sidebar.warning(f"将当前参数覆盖到 **{target['name']}**？")
            st.sidebar.caption("这会将当前所有参数写入该预设。")
            cc1, cc2 = st.sidebar.columns(2)
            with cc1:
                # P2-opt: Streamlit auto-reruns after button click — no st.rerun() needed
                if st.button("确认覆盖", key="update_confirm_btn", use_container_width=True):
                    from data.config_db import collect_current_params
                    import json as _json
                    params = collect_current_params()
                    save_preset(target["name"],
                                _json.dumps(params, ensure_ascii=False),
                                description=target.get("description", ""),
                                category=target.get("category", "通用"))
                    st.toast(f"已更新: {target['name']}")
                    AppState.pop("_preset_action")
                    AppState.pop("_preset_action_id")
            with cc2:
                # P1-4: on_click callback — Streamlit auto-reruns after callback
                st.button("取消", key="update_cancel_btn", use_container_width=True,
                          on_click=_cancel_preset_action)
        elif _action == "rename":
            st.sidebar.caption(f"重命名 **{target['name']}**")
            new_name = st.sidebar.text_input("新名称", value=target["name"],
                                             key="rename_input_val")
            cc1, cc2 = st.sidebar.columns(2)
            with cc1:
                # P2-opt: Streamlit auto-reruns after button click — no st.rerun() needed
                if st.button("确认重命名", key="rename_confirm_btn", use_container_width=True):
                    if new_name.strip() and new_name.strip() != target["name"]:
                        rename_preset(target["preset_id"], new_name.strip())
                        st.toast(f"已重命名: {target['name']} → {new_name.strip()}")
                        AppState.pop("_preset_action")
                        AppState.pop("_preset_action_id")
                    elif new_name.strip() == target["name"]:
                        st.warning("名称未变化")
                    else:
                        st.error("名称不能为空")
            with cc2:
                # P1-4: on_click callback — Streamlit auto-reruns after callback
                st.button("取消", key="rename_cancel_btn", use_container_width=True,
                          on_click=_cancel_preset_action)
        elif _action == "delete":
            st.sidebar.error(f"确认删除 **{target['name']}**？此操作不可恢复。")
            cc1, cc2 = st.sidebar.columns(2)
            with cc1:
                # P2-opt: on_click callback — Streamlit auto-reruns, no explicit st.rerun()
                def _delete_confirm_cb():
                    delete_preset(target["preset_id"])
                    st.toast(f"已删除: {target['name']}")
                    AppState.pop("_preset_action")
                    AppState.pop("_preset_action_id")
                st.button("确认删除", key="delete_confirm_btn", use_container_width=True,
                          on_click=_delete_confirm_cb)
            with cc2:
                # P1-4: on_click callback — Streamlit auto-reruns after callback
                st.button("取消", key="delete_cancel_btn", use_container_width=True,
                          on_click=_cancel_preset_action)

    # Save as preset
    with st.sidebar.expander("💾 保存 / 另存为预设", expanded=False):
        if not AppState.has("_last_sel_name"):
            AppState.set("_last_sel_name", "")
        curr_sel_name = selected_preset["name"] if selected_preset else ""
        if AppState.get("_last_sel_name") != curr_sel_name:
            AppState.set("new_preset_name", (curr_sel_name + "_副本" if curr_sel_name else ""))
            AppState.set("_last_sel_name", curr_sel_name)
        new_name = st.text_input("预设名称", key="new_preset_name", placeholder="如: 我的港股配置")
        new_desc = st.text_input("描述(可选)", key="new_preset_desc", placeholder="港股·短线·savgol")
        if AppState.pop("_pending_reset_overwrite", False):
            AppState.set("overwrite_preset", False)
        overwrite = False
        if selected_preset:
            overwrite = st.checkbox(f"覆盖「{selected_preset['name']}」", key="overwrite_preset")
        # P2-opt: Streamlit auto-reruns after button click — no st.rerun() needed
        if st.button("💾 保存", key="save_preset_btn", use_container_width=True):
            if new_name.strip():
                from data.config_db import collect_current_params
                import json as _json
                params = collect_current_params()
                target_name = (selected_preset["name"] if overwrite and selected_preset else new_name.strip())
                cat = (selected_preset.get("category", "通用") if overwrite and selected_preset else "通用")
                logger.info(f"Preset saved: {target_name} (overwrite={overwrite})")
                save_preset(target_name,
                            _json.dumps(params, ensure_ascii=False),
                            description=(new_desc.strip() if not overwrite else selected_preset.get("description", "")),
                            category=cat)
                st.toast(f"已保存: {target_name}")
                AppState.set("_pending_reset_overwrite", True)
            else:
                st.error("请输入预设名称")


def _render_health_check(ticker_code) -> None:
    """Render data health check expander section."""
    with st.sidebar.expander("🩺 数据健康检查", expanded=False):
        if st.button("运行检查", key="health_btn", use_container_width=True) and ticker_code:
            with st.spinner("检查数据完整性..."):
                logger.debug(f"Running data health check for {ticker_code}")
                report = check_data_health(ticker_code)
            status = report.get("status", "ok")
            color_map = {"ok": "green", "warn": "orange", "error": "red"}
            color = color_map.get(status, "gray")
            st.markdown(f"**:{color}[状态: {report.get('summary', status)}]**")
            if report.get("issues"):
                for issue in report["issues"]:
                    st.warning(issue)
            if report.get("details"):
                detail_df = pd.DataFrame(report["details"])
                st.dataframe(detail_df, use_container_width=True, hide_index=True,
                             height=min(35 * len(detail_df) + 38, 300))


def _render_data_validation(market, ticker_code) -> None:
    """Render DB vs source data validation expander section."""
    with st.sidebar.expander("📋 数据校验", expanded=False):
        st.caption("对比数据库与 yfinance 全部周期，发现历史数据修正")
        if st.button("校验全部周期", key="val_btn", use_container_width=True) and ticker_code:
            logger.debug(f"Validating all timeframes for {ticker_code}")
            if market == "A股(沪深)":
                if not ticker_code.strip():
                    st.warning("股票代码不能为空")
                    return
                full_code = ticker_code + (".SS" if ticker_code[0] == "6" else ".SZ")
            elif market == "港股 HK":
                full_code = ticker_code.zfill(4) + ".HK"
            else:
                full_code = ticker_code.upper()

            rows = []
            has_conflict = False
            has_update = False
            # Phase 1: parallel download of all timeframes (I/O-bound)
            with st.spinner("校验全部周期中..."):
                raw_data = {}
                with ThreadPoolExecutor(max_workers=4) as executor:
                    futures = {}
                    for tf in ALL_TFS:
                        interval, period = TF_INTERVAL[tf]
                        future = executor.submit(yf.download, full_code, period=period, interval=interval, progress=False)
                        futures[future] = tf
                    for future in as_completed(futures):
                        tf = futures[future]
                        try:
                            raw_data[tf] = future.result(timeout=30)
                        except Exception as e:
                            raw_data[tf] = e

            # Phase 2: process results in ALL_TFS order (logic unchanged)
            for tf in ALL_TFS:
                try:
                    data = raw_data[tf]
                    if isinstance(data, Exception):
                        raise data
                    if data.empty or len(data[data["Close"].notna()]) < 5:
                        rows.append({"周期": tf, "DB": "-", "yf": "-", "重叠": "-",
                                     "指纹": "⚠️ 数据不足", "仅DB": "-", "仅yf": "-", "操作": ""})
                        continue
                    data = data[data["Close"].notna()]
                    report = compare_with_db(ticker_code, tf, data)
                except Exception as e:
                    rows.append({"周期": tf, "DB": "-", "yf": "-", "重叠": "-",
                                 "指纹": f"❌ {str(e)[:30]}", "仅DB": "-", "仅yf": "-", "操作": ""})
                    continue
                db_c = report["db_count"]
                yf_c = report["yf_count"]
                fp = "✅" if report["fingerprint_match"] else "❌"
                status = report["status"]
                if status == "conflict":
                    has_conflict = True
                elif status == "update_available":
                    has_update = True
                rows.append({
                    "周期": tf, "DB": db_c, "yf": yf_c,
                    "重叠": report["overlap_count"], "指纹": fp,
                    "仅DB": report["only_db"], "仅yf": report["only_yf"], "操作": status,
                })

            if rows:
                import pandas as _pd
                df = _pd.DataFrame(rows)

                def _row_style(r):
                    s = r["差异"]
                    if s == "conflict":
                        return ["background-color: #fff3cd"] * len(r)
                    elif s == "update_available":
                        return ["background-color: #d4edda"] * len(r)
                    return [""] * len(r)

                df_display = df.rename(columns={"操作": "差异"})
                df_display["差异"] = df_display["差异"].replace({
                    "conflict": "⚠️ 数据冲突", "update_available": "有新数据", "ok": "✅ 一致",
                })
                styled = df_display.style.apply(_row_style, axis=1)
                st.dataframe(styled, use_container_width=True, hide_index=True,
                             height=min(35 * len(df) + 38, 350))
                st.caption("🟡 黄底 = 历史数据被修正 | 🟢 绿底 = 有新增数据")
                if has_conflict or has_update:
                    if st.button("⚠️ 更新全部有差异的周期", key="force_update_all", use_container_width=True):
                        updated = 0
                        for row in df.itertuples(index=False):
                            s = getattr(row, "操作")
                            if s in ("conflict", "update_available"):
                                tf = getattr(row, "周期")
                                try:
                                    interval, period = TF_INTERVAL[tf]
                                    data = yf.download(full_code, period=period, interval=interval, progress=False)
                                    if not data.empty:
                                        data = data[data["Close"].notna()]
                                        force_update_kline(ticker_code, tf, data)
                                        updated += 1
                                except Exception as e:
                                    logger.warning(f"Force update {tf} failed: {e}")
                        # P2-opt: Streamlit auto-reruns after button click — no st.rerun() needed
                        if updated > 0:
                            st.cache_data.clear()
                            clear_display_cache()
                            st.success(f"已更新 {updated} 个周期，页面将刷新")
                        else:
                            st.warning("没有周期被更新")


def _render_filter_selectors() -> tuple:
    """Render filter selector widgets. Returns (filter_id, dual, filter_id2)."""
    filter_id = st.sidebar.selectbox("滤波器", list(FILTERS.keys()),  # type: ignore[arg-type]
        format_func=lambda x: FILTERS.get(x, {}).get("name", x), key="global_f")  # type: ignore[arg-type,return-value]
    dual = st.sidebar.checkbox("双滤波对比", value=False, key="global_dual")
    filter_id2 = None
    if dual:
        filter_id2 = st.sidebar.selectbox("滤波器 2", list(FILTERS.keys()),  # type: ignore[arg-type]
            format_func=lambda x: FILTERS.get(x, {}).get("name", x), key="global_f2")  # type: ignore[arg-type,return-value]
    return filter_id, dual, filter_id2


def _render_param_panels(filter_id, dual, filter_id2) -> list:
    """Render 2x2 parameter panels. Returns list of config dicts."""
    configs = []
    for row_idx in range(2):
        c1, c2 = st.columns(2)
        for col_idx, col in enumerate([c1, c2]):
            i = row_idx * 2 + col_idx
            with col:
                tf_label = st.session_state.get(f"v{i}_tf", DEFAULT_TFS[i])
                st.caption(f"视图{i + 1} · {tf_label}")
                cfg = _render_params(f"v{i}", filter_id, dual, filter_id2, DEFAULT_TFS[i])
                configs.append(cfg)
    return configs


def _render_db_backup() -> None:
    """Render DB backup/restore expander section."""
    with st.sidebar.expander("💾 数据备份与恢复", expanded=False):
        db_size = get_db_size_mb()
        logger.debug(f"DB status: {DB_PATH.name} ({db_size:.1f} MB)")
        st.caption(f"数据库: {DB_PATH.name} ({db_size:.1f} MB)")
        c_s1, c_s2 = st.columns([1, 1])
        with c_s1:
            if st.button("创建备份", key="snap_btn", use_container_width=True):
                try:
                    path = snapshot_db()
                    prune_snapshots(max_keep=5)
                    logger.info(f"Snapshot created: {Path(path).name}")
                    st.success(f"已创建: {Path(path).name}")
                except Exception as e:
                    logger.error(f"Snapshot failed: {e}", exc_info=True)
                    st.error(f"备份失败: {e}")
        snapshots = list_snapshots()
        with c_s2:
            snap_count = len(snapshots)
            st.caption(f"共 {snap_count} 个备份" if snap_count else "暂无备份")
        if snapshots:
            snap_labels = [s[3] for s in snapshots]
            selected_idx = st.selectbox("选择备份", range(len(snap_labels)),
                                        format_func=lambda i: snap_labels[i], key="restore_select")
            c_r1, c_r2 = st.columns([1, 1])
            with c_r1:
                # P2-opt: Streamlit auto-reruns after button click — no st.rerun() needed
                if st.button("恢复到此备份", key="restore_btn", use_container_width=True):
                    try:
                        restore_snapshot(snapshots[selected_idx][0])
                        st.cache_data.clear()
                        clear_display_cache()
                        AppState.set("_fetched_ticker", "")
                        logger.info(f"Snapshot restored: {snap_labels[selected_idx]}")
                        st.success("已恢复，页面将刷新")
                    except Exception as e:
                        logger.error(f"Snapshot restore failed: {e}", exc_info=True)
                        st.error(f"恢复失败: {e}")
            with c_r2:
                # P2-opt: on_click callback — Streamlit auto-reruns, no explicit st.rerun()
                def _delete_snapshot_cb() -> None:
                    os.remove(snapshots[selected_idx][0])
                    logger.info(f"Snapshot deleted: {snap_labels[selected_idx]}")
                st.button("删除此备份", key="del_snap_btn", use_container_width=True,
                          on_click=_delete_snapshot_cb)


def _view_export_params(cfg, i) -> dict:
    """按单一真源 VIEW_PARAM_SPECS 从单个视图 cfg 构建导出键值。

    export(JSON) 与测试共用此函数，参数增删只跟随 VIEW_PARAM_SPECS，不会漏。
    """
    return {f"v{i}_{suffix}": cfg.get(cfg_key, default)
            for suffix, cfg_key, default in VIEW_PARAM_SPECS}


def _render_export_config(configs, filter_id, filter_id2, dual, market, ticker_code) -> None:
    """Render config export download button."""
    st.sidebar.markdown("---")
    export_data = {
        "market": market, "ticker": ticker_code,
        "global_f": filter_id, "global_dual": dual, "global_f2": filter_id2,
    }
    for i, cfg in enumerate(configs):
        export_data.update(_view_export_params(cfg, i))
        f1: dict = FILTERS.get(filter_id, {})  # type: ignore[assignment]
        for pname, pval in cfg.get("pv", {}).items():
            label = f1["params"].get(pname, (pname,))[0]
            export_data[f"{label}_v{i}_f1_{filter_id}"] = pval
        f2: dict = FILTERS.get(filter_id2, {}) if filter_id2 else {}  # type: ignore[assignment]
        for pname, pval in cfg.get("pv2", {}).items():
            label = f2["params"].get(pname, (pname,))[0]
            export_data[f"{label}_v{i}_f2_{filter_id2}"] = pval
    st.sidebar.download_button("导出配置", json.dumps(export_data, ensure_ascii=False, indent=2),
        file_name="filter_config.json", mime="application/json",
        use_container_width=True)


def _render_config_history(ticker_code) -> None:
    """Render config history expander section."""
    if ticker_code:
        st.sidebar.markdown("---")
        with st.sidebar.expander("📜 配置历史", expanded=False):
            records = get_history(ticker_code, variant="single", limit=10)
            if records:
                for rec in records:
                    source_icon = {"ui": "✏️", "import": "📥", "preset_apply": "📋", "rollback": "↩️"}
                    icon = source_icon.get(rec.get("source", ""), "📌")
                    st.caption(f"{icon} {rec['changed_at']} — {rec.get('preset_name') or rec['source']}")
            else:
                st.caption("暂无记录")


def _render_db_import_export() -> None:
    """Render DB import/export expander section."""
    with st.sidebar.expander("📦 数据库导入/导出", expanded=False):
        st.caption("导出整个数据库到文件，可在其他设备导入")
        try:
            checkpoint_wal()
        except Exception as e:
            logger.debug(f"Checkpoint WAL failed (non-critical): {e}")
        try:
            db_bytes = DB_PATH.read_bytes()
            logger.debug(f"DB export: {len(db_bytes) / 1024 / 1024:.1f} MB")
            st.download_button("导出数据库", db_bytes, file_name="market.db",
                mime="application/octet-stream", use_container_width=True,
                help=f"文件大小: {len(db_bytes) / 1024 / 1024:.1f} MB")
        except Exception as e:
            logger.error(f"DB export failed: {e}", exc_info=True)
            st.error(f"导出失败: {e}")
        uploaded_db = st.file_uploader("导入数据库", type=["db"], key="db_import", label_visibility="collapsed")
        if uploaded_db is not None:
            raw = uploaded_db.read()
            file_hash = hashlib.md5(raw).hexdigest()
            if AppState.get("_db_import_hash") != file_hash:
                with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                    tmp.write(raw)
                    tmp_path = tmp.name
                valid, err_msg = validate_db(tmp_path)
                try:
                    os.remove(tmp_path)
                except Exception as e:
                    logger.debug(f"Temp file cleanup failed: {e}")
                if not valid:
                    st.error(f"无效的数据库文件: {err_msg}")
                else:
                    DB_PATH.write_bytes(raw)
                    for suffix in ["-wal", "-shm"]:
                        p = str(DB_PATH) + suffix
                        if os.path.exists(p):
                            os.remove(p)
                    st.cache_data.clear()
                    clear_display_cache()
                    AppState.set("_fetched_ticker", "")
                    AppState.set("_db_import_hash", file_hash)
                    logger.info(f"DB imported: {len(raw) / 1024 / 1024:.1f} MB")
                    st.success(f"数据库已导入 ({len(raw) / 1024 / 1024:.1f} MB)，页面将刷新")
                    time.sleep(0.5)
                    st.rerun()


def _run_auto_refresh(market, ticker_code, auto_refresh, interval) -> None:
    """Execute auto-refresh if enabled — non-blocking timestamp-check mode.

    Uses session_state timestamp comparison instead of time.sleep() which
    blocked the entire Streamlit thread. Depends on natural rerun triggers
    (user interaction, browser reconnect) to check elapsed time.
    """
    if not auto_refresh:
        return

    now = time.time()
    last_refresh = st.session_state.get("_last_auto_refresh", 0)
    elapsed = now - last_refresh

    if elapsed < interval:
        remaining = int(interval - elapsed)
        st.caption(f"⏱️ {remaining}s 后自动刷新 (共 {interval}s)")
        return  # 不阻塞,正常渲染,等下次 rerun 再检查

    # 到达刷新时间
    logger.info(f"Auto-refresh triggered for {ticker_code} (interval={interval}s)")
    st.cache_data.clear()
    _fetch_all_timeframes(market, ticker_code)
    st.session_state["_last_auto_refresh"] = now
    st.rerun()
