"""
预设配置管理模块 — 预设选择器、导入/导出、配置历史

从 streamlit_app.py 提取的侧边栏 UI 组件。
处理预设方案的选择、应用、更新、重命名、删除，以及配置的导入/导出和配置历史展示。
"""

import json
import hashlib
import streamlit as st
from loguru import logger

from config_db import (
    list_presets, apply_preset, save_preset, delete_preset,
    rename_preset, get_history, VIEW_PARAM_SPECS,
)
from services.filter_engine import FILTERS
from state import AppState


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
        c1, c2, c3, c4 = st.sidebar.columns([1.2, 1, 1, 0.8])
        with c1:
            if st.button("✅ 应用", key="apply_preset", use_container_width=True):
                params = apply_preset(p["preset_id"])
                if params:
                    logger.info(f"Preset applied: {p['name']} ({len(params)} params)")
                    AppState.set("_pending_apply_params", params)
                    st.toast(f"已应用: {p['name']}")
                    st.rerun()
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
                if st.button("确认覆盖", key="update_confirm_btn", use_container_width=True):
                    from config_db import collect_current_params
                    import json as _json
                    params = collect_current_params()
                    save_preset(target["name"],
                                _json.dumps(params, ensure_ascii=False),
                                description=target.get("description", ""),
                                category=target.get("category", "通用"))
                    st.toast(f"已更新: {target['name']}")
                    AppState.pop("_preset_action")
                    AppState.pop("_preset_action_id")
                    st.rerun()
            with cc2:
                if st.button("取消", key="update_cancel_btn", use_container_width=True):
                    AppState.pop("_preset_action")
                    AppState.pop("_preset_action_id")
                    st.rerun()
        elif _action == "rename":
            st.sidebar.caption(f"重命名 **{target['name']}**")
            new_name = st.sidebar.text_input("新名称", value=target["name"],
                                             key="rename_input_val")
            cc1, cc2 = st.sidebar.columns(2)
            with cc1:
                if st.button("确认重命名", key="rename_confirm_btn", use_container_width=True):
                    if new_name.strip() and new_name.strip() != target["name"]:
                        rename_preset(target["preset_id"], new_name.strip())
                        st.toast(f"已重命名: {target['name']} → {new_name.strip()}")
                        AppState.pop("_preset_action")
                        AppState.pop("_preset_action_id")
                        st.rerun()
                    elif new_name.strip() == target["name"]:
                        st.warning("名称未变化")
                    else:
                        st.error("名称不能为空")
            with cc2:
                if st.button("取消", key="rename_cancel_btn", use_container_width=True):
                    AppState.pop("_preset_action")
                    AppState.pop("_preset_action_id")
                    st.rerun()
        elif _action == "delete":
            st.sidebar.error(f"确认删除 **{target['name']}**？此操作不可恢复。")
            cc1, cc2 = st.sidebar.columns(2)
            with cc1:
                if st.button("确认删除", key="delete_confirm_btn", use_container_width=True):
                    delete_preset(target["preset_id"])
                    st.toast(f"已删除: {target['name']}")
                    AppState.pop("_preset_action")
                    AppState.pop("_preset_action_id")
                    st.rerun()
            with cc2:
                if st.button("取消", key="delete_cancel_btn", use_container_width=True):
                    AppState.pop("_preset_action")
                    AppState.pop("_preset_action_id")
                    st.rerun()

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
        if st.button("💾 保存", key="save_preset_btn", use_container_width=True):
            if new_name.strip():
                from config_db import collect_current_params
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
                st.rerun()
            else:
                st.error("请输入预设名称")


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
        f1 = FILTERS.get(filter_id, {})
        for pname, pval in cfg.get("pv", {}).items():
            label = f1["params"].get(pname, (pname,))[0]
            export_data[f"{label}_v{i}_f1_{filter_id}"] = pval
        f2 = FILTERS.get(filter_id2, {}) if filter_id2 else {}
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
