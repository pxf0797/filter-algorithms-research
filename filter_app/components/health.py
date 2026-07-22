"""
数据健康检查与维护模块 — Streamlit UI 组件

从 streamlit_app.py 提取的侧边栏 expander：
- 数据健康检查
- 数据校验 (DB vs yfinance)
- 数据库备份与恢复
- 数据库导入/导出
"""

import os
import time
import hashlib
import tempfile
import pandas as pd
import streamlit as st
import yfinance as yf
from pathlib import Path
from loguru import logger

from db import (
    check_data_health, get_db_size_mb, snapshot_db, list_snapshots,
    restore_snapshot, prune_snapshots, clear_display_cache,
    checkpoint_wal, validate_db, compare_with_db, force_update_kline,
    DB_PATH,
)
from components.sidebar import ALL_TFS
from state import AppState


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
            TF_INTERVAL = {"1分钟": ("1m", "7d"), "5分钟": ("5m", "60d"), "15分钟": ("15m", "60d"),
                           "60分钟": ("1h", "730d"), "日线": ("1d", "max"), "周线": ("1wk", "max"),
                           "月线": ("1mo", "max"), "季线": ("3mo", "max")}
            for tf in ALL_TFS:
                interval, period = TF_INTERVAL[tf]
                with st.spinner(f"校验 {tf} ..."):
                    try:
                        data = yf.download(full_code, period=period, interval=interval, progress=False)
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
                        for _, r in df.iterrows():
                            s = r["操作"]
                            if s in ("conflict", "update_available"):
                                tf = r["周期"]
                                try:
                                    interval, period = TF_INTERVAL[tf]
                                    data = yf.download(full_code, period=period, interval=interval, progress=False)
                                    if not data.empty:
                                        data = data[data["Close"].notna()]
                                        force_update_kline(ticker_code, tf, data)
                                        updated += 1
                                except Exception as e:
                                    logger.warning(f"Force update {tf} failed: {e}")
                        if updated > 0:
                            from components.data_processing import _cached_fetch_stock
                            _cached_fetch_stock.clear()
                            clear_display_cache()
                            st.success(f"已更新 {updated} 个周期，页面将刷新")
                            time.sleep(0.5)
                            st.rerun()
                        else:
                            st.warning("没有周期被更新")


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
                if st.button("恢复到此备份", key="restore_btn", use_container_width=True):
                    try:
                        restore_snapshot(snapshots[selected_idx][0])
                        from components.data_processing import _cached_fetch_stock
                        _cached_fetch_stock.clear()
                        clear_display_cache()
                        AppState.set("_fetched_ticker", "")
                        logger.info(f"Snapshot restored: {snap_labels[selected_idx]}")
                        st.success("已恢复，页面将刷新")
                        time.sleep(0.5)
                        st.rerun()
                    except Exception as e:
                        logger.error(f"Snapshot restore failed: {e}", exc_info=True)
                        st.error(f"恢复失败: {e}")
            with c_r2:
                if st.button("删除此备份", key="del_snap_btn", use_container_width=True):
                    try:
                        os.remove(snapshots[selected_idx][0])
                        logger.info(f"Snapshot deleted: {snap_labels[selected_idx]}")
                        st.rerun()
                    except Exception as e:
                        logger.warning(f"Snapshot deletion failed: {e}")
                        st.error(f"删除失败: {e}")


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
                    from components.data_processing import _cached_fetch_stock
                    _cached_fetch_stock.clear()
                    clear_display_cache()
                    AppState.set("_fetched_ticker", "")
                    AppState.set("_db_import_hash", file_hash)
                    logger.info(f"DB imported: {len(raw) / 1024 / 1024:.1f} MB")
                    st.success(f"数据库已导入 ({len(raw) / 1024 / 1024:.1f} MB)，页面将刷新")
                    time.sleep(0.5)
                    st.rerun()
