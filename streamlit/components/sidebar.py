"""
侧边栏控件构建模块 — Streamlit UI 组件

包含：
- 参数滑块渲染函数
- 视图参数面板构建
"""

import streamlit as st
from services.filter_engine import FILTERS

ALL_TFS = ["1分钟","5分钟","15分钟","60分钟","日线","周线","月线","季线"]
DEFAULT_TFS = ["日线", "60分钟", "15分钟", "5分钟"]


def _compact_slider(label, pmin, pmax, pdefault, pstep=1, key=None, fmt=None):
    """标签与滑块同行（仅用于无 help 的简单滑块）。"""
    c = st.columns([0.35, 0.65])
    c[0].markdown(f"<small>{label}</small>", unsafe_allow_html=True)
    kwargs = dict(min_value=pmin, max_value=pmax, value=pdefault, step=pstep,
                  key=key, label_visibility="collapsed")
    if fmt: kwargs["format"] = fmt
    return c[1].slider(label, **kwargs)


def _render_param_slider(label, pmin, pmax, pstep, pdefault, key_suffix="", container=None):
    """Render an st.slider with appropriate numeric format.
    If container is None, renders in sidebar (backward compat).
    Pass container=st to render inline in the current column context.
    """
    ctx = container if container is not None else st.sidebar
    key = f"{label}_{key_suffix}" if key_suffix else None
    if key:
        pdefault = st.session_state.get(key, st.session_state.get(f"_imp_{key}", pdefault))
    if isinstance(pstep, int):
        return ctx.slider(label, pmin, pmax, pdefault, pstep, key=key)
    fmt = "%.3f" if pstep < 0.01 else "%.2f"
    return ctx.slider(label, pmin, pmax, pdefault, pstep, format=fmt, key=key)


# 紧邻高周期映射：本周期 → 高周期（用于跨周期PnL参考子图）
TF_HIERARCHY = {
    "1分钟": "5分钟", "5分钟": "15分钟", "15分钟": "60分钟",
    "60分钟": "日线", "日线": "周线", "周线": "月线",
    "月线": "季线", "季线": None,
}


def _render_params(key, filter_id, dual, filter_id2, tf_default):
    """Ultra-compact parameter panel. Returns config dict."""
    cfg = {"_fid": filter_id, "_dual": dual, "_fid2": filter_id2}

    # Row 1: [周期▼] [N▬] [施密特☑] [预测☑] [▲▼]
    c1 = st.columns([1.0, 0.8, 0.8, 0.8, 0.4])
    with c1[0]:
        cfg["tf"] = st.selectbox("周期", ALL_TFS, index=ALL_TFS.index(tf_default),
            key=f"{key}_tf", label_visibility="collapsed")
    with c1[1]:
        cfg["n_pts"] = _compact_slider("N", 20, 300, 120, 10, key=f"{key}_n")
    with c1[2]:
        cfg["show_sch"] = st.checkbox("施密特", value=True, key=f"{key}_sch")
    cfg["ke"] = st.session_state.get(f"{key}_ke", st.session_state.get(f"_imp_{key}_ke", 0.15))
    cfg["sm"] = st.session_state.get(f"{key}_sm", st.session_state.get(f"_imp_{key}_sm", 0.05))
    cfg["ew"] = st.session_state.get(f"{key}_ew", st.session_state.get(f"_imp_{key}_ew", 60))
    cfg["show_pred"] = st.session_state.get(f"{key}_pred", st.session_state.get(f"_imp_{key}_pred", True))
    cfg["n_ext"] = st.session_state.get(f"{key}_next", st.session_state.get(f"_imp_{key}_next", 8))
    cfg["fit_mode"] = st.session_state.get(f"{key}_fm", st.session_state.get(f"_imp_{key}_fm", "parabola"))
    if cfg["show_sch"]:
        with c1[3]: cfg["show_pred"] = st.checkbox("预测", value=True, key=f"{key}_pred")

    # 本视图展开/折叠
    exp_key = f"{key}_exp_all"
    if exp_key not in st.session_state:
        st.session_state[exp_key] = False
    exp_all = st.session_state[exp_key]
    with c1[4]:
        label = "▲" if exp_all else "▼"
        if st.button(label, key=f"{key}_tgl", help="展开/折叠全部参数",
                     use_container_width=True):
            st.session_state[exp_key] = not exp_all
            st.rerun()

    # Schmitt ON → 折叠面板
    if cfg["show_sch"]:
        with st.expander("施密特参数", expanded=exp_all):
            c2 = st.columns([1.0, 1.0, 1.0])
            with c2[0]: cfg["ke"] = st.slider("k_ε", 0.01, 0.50, cfg["ke"], 0.01, key=f"{key}_ke",
                help="灵敏度系数,越小越敏感. ε_t=k_ε·max(σ_t(v),σ_min)")
            with c2[1]: cfg["sm"] = st.slider("σ_min", 0.001, 0.20, cfg["sm"], 0.001, key=f"{key}_sm", format="%.3f",
                help="地板保护,防止低波动下ε_t→0")
            with c2[2]: cfg["ew"] = st.slider("N_EWMA", 10, 120, cfg["ew"], 1, key=f"{key}_ew",
                help="EWMA周期,α=2/(N+1),越大越平滑。⚠️ 实际bar数(N)必须≥此值,否则无信号。"
                     "σ(v)估计精度≈1/√(2×N)。20 bar下建议N_EWMA≤15, 60 bar默认60")
        if cfg["show_pred"]:
            with st.expander("预测参数", expanded=exp_all):
                c3 = st.columns([1.5, 1.0])
                fit_key = f"{key}_fm"
                _fm_val = st.session_state.get(fit_key,
                    st.session_state.get(f"_imp_{fit_key}", "parabola"))
                fit_idx = 1 if _fm_val == "parabola" else 0
                with c3[0]: cfg["fit_mode"] = st.radio("拟合方式",
                    ["poly2", "parabola"], index=fit_idx, horizontal=True,
                    format_func=lambda x: "二次多项式" if x=="poly2" else "抛物线拟合",
                    key=fit_key)
                with c3[1]: cfg["n_ext"] = st.slider("预测点数", 1, 50, cfg["n_ext"], 1, key=f"{key}_next")
            # 策略参数 — 仅在开启预测时可用
            if cfg["show_pred"]:
                with st.expander("策略参数", expanded=exp_all):
                    st.markdown("""
                    <div style="font-size:12px; line-height:1.8; color:#8b949e;
                    background:rgba(88,166,255,0.06); border-radius:6px; padding:10px 14px;
                    border-left:3px solid #58a6ff;">
                    <b>📋 策略规则（分段混合 · 方案D）</b><br>
                    <b>预测保护期</b> <code>i∈[entry+1, entry+N<sub>ext</sub>]</code><br>
                    　　　 止损 <code>P<sub>t</sub>&lt;ŷ<sub>t</sub>·(1−s%)</code>（多）/<code>P<sub>t</sub>&gt;ŷ<sub>t</sub>·(1+s%)</code>（空）<br>
                    　　　 止盈 <code>Sig=-1</code>（多）/<code>Sig=+1</code>（空）<br>
                    <b>趋势跟踪期</b> <code>i∈[entry+N<sub>ext</sub>+1, …]</code><br>
                    　　　 仅止盈 <code>Sig</code> 反转离场，止损停用，让利润奔跑<br>
                    　　　 若始终未触发则持有至数据末尾<br>
                    <b>入场</b> <code>entry=pair_end</code>　做多需 <code>Sig=+1</code>且<code>ŷ<sub>end</sub>&gt;ŷ<sub>0</sub></code><br>
                    <b>曲线</b> <code>PnL<sub>t</sub>=capital·(1+未实现%)</code>　空仓期水平直线
                    </div>
                    """, unsafe_allow_html=True)
                    c_strat = st.columns([1.0, 1.0])
                    strat_key = f"{key}_strat"
                    sl_key = f"{key}_sl"
                    cross_key = f"{key}_cross_pnl"
                    with c_strat[0]:
                        cfg["show_strategy"] = st.checkbox(
                            "启用策略叠加", value=st.session_state.get(strat_key,
                                st.session_state.get(f"_imp_{strat_key}", False)),
                            key=strat_key,
                            help="在Sig子图下方显示基于预测曲线+施密特信号的策略PnL")
                        cfg["show_cross_pnl"] = st.checkbox(
                            "显示高周期PnL参考", value=st.session_state.get(cross_key,
                                st.session_state.get(f"_imp_{cross_key}", False)),
                            key=cross_key, disabled=not cfg["show_strategy"],
                            help="在本周期PnL下方显示紧邻高周期的交易事件标记和PnL参考线")
                        align_key = f"{key}_align"
                        cfg["show_alignment"] = st.checkbox(
                            "显示同向性判断", value=st.session_state.get(align_key,
                                st.session_state.get(f"_imp_{align_key}", False)),
                            key=align_key,
                            disabled=not (cfg["show_strategy"] and cfg["show_cross_pnl"]),
                            help="高周期做多/空持仓时，本周期同向PnL才在子图体现，否则维持不变")
                    if cfg["show_strategy"]:
                        with c_strat[1]:
                            cfg["stop_loss_pct"] = st.slider(
                                "止损阈值(%)", 0.5, 10.0,
                                st.session_state.get(sl_key,
                                    st.session_state.get(f"_imp_{sl_key}", 2.0)), 0.1,
                                key=sl_key,
                                help="预测偏差超过此阈值即止损离场")
                    else:
                        cfg["stop_loss_pct"] = st.session_state.get(sl_key,
                            st.session_state.get(f"_imp_{sl_key}", 2.0))

    # 滤波参数 — 可折叠
    sf = FILTERS[filter_id]
    cfg["pv"] = {}
    f1 = list(sf["params"].items())
    with st.expander(f"滤波参数 · {sf['name']}", expanded=exp_all):
        fc1 = st.columns([1]*len(f1) + [0.25])
        for j, (pn, sp) in enumerate(f1):
            with fc1[j]:
                cfg["pv"][pn] = _render_param_slider(*sp, key_suffix=f"{key}_f1_{filter_id}", container=st)
        with fc1[-1]:
            cfg["fc"] = st.color_picker("", "#00d4aa", key=f"{key}_fc", label_visibility="collapsed")

    # 滤波参数2（可选）
    if dual and filter_id2:
        sf2 = FILTERS[filter_id2]
        cfg["pv2"] = {}
        f2 = list(sf2["params"].items())
        with st.expander(f"滤波参数2 · {sf2['name']}", expanded=exp_all):
            fc2 = st.columns([1]*len(f2) + [0.25])
            for j, (pn, sp) in enumerate(f2):
                with fc2[j]:
                    cfg["pv2"][pn] = _render_param_slider(*sp, key_suffix=f"{key}_f2_{filter_id2}", container=st)
            with fc2[-1]:
                cfg["fc2"] = st.color_picker("", "#ff6b6b", key=f"{key}_fc2", label_visibility="collapsed")
    else:
        cfg["pv2"] = {}
        cfg["fc2"] = "#ff6b6b"

    # 从 session_state 读取最终值（导入参数唯一真相源，含_imp_备份防rerun丢失）
    cfg["ke"] = st.session_state.get(f"{key}_ke",
        st.session_state.get(f"_imp_{key}_ke", cfg["ke"]))
    cfg["sm"] = st.session_state.get(f"{key}_sm",
        st.session_state.get(f"_imp_{key}_sm", cfg["sm"]))
    cfg["ew"] = st.session_state.get(f"{key}_ew",
        st.session_state.get(f"_imp_{key}_ew", cfg["ew"]))
    cfg["show_pred"] = st.session_state.get(f"{key}_pred",
        st.session_state.get(f"_imp_{key}_pred", cfg["show_pred"]))
    cfg["fit_mode"] = st.session_state.get(f"{key}_fm",
        st.session_state.get(f"_imp_{key}_fm", cfg["fit_mode"]))
    cfg["n_ext"] = st.session_state.get(f"{key}_next",
        st.session_state.get(f"_imp_{key}_next", cfg["n_ext"]))
    for pname in sf["params"]:
        label = sf["params"][pname][0]
        sk = f"{label}_{key}_f1_{filter_id}"
        cfg["pv"][pname] = st.session_state.get(sk,
            st.session_state.get(f"_imp_{sk}", cfg["pv"].get(pname, 0)))
    if dual and filter_id2:
        for pname in sf2["params"]:
            label = sf2["params"][pname][0]
            sk = f"{label}_{key}_f2_{filter_id2}"
            cfg["pv2"][pname] = st.session_state.get(sk,
                st.session_state.get(f"_imp_{sk}", cfg["pv2"].get(pname, 0)))

    cfg["show_strategy"] = st.session_state.get(f"{key}_strat",
        st.session_state.get(f"_imp_{key}_strat", cfg.get("show_strategy", False)))
    cfg["stop_loss_pct"] = st.session_state.get(f"{key}_sl",
        st.session_state.get(f"_imp_{key}_sl", cfg.get("stop_loss_pct", 2.0)))
    cfg["show_cross_pnl"] = st.session_state.get(f"{key}_cross_pnl",
        st.session_state.get(f"_imp_{key}_cross_pnl", cfg.get("show_cross_pnl", False)))
    cfg["show_alignment"] = st.session_state.get(f"{key}_align",
        st.session_state.get(f"_imp_{key}_align", cfg.get("show_alignment", False)))
    cfg["fc"] = st.session_state.get(f"{key}_fc",
        st.session_state.get(f"_imp_{key}_fc", cfg.get("fc", "#00d4aa")))
    if dual and filter_id2:
        cfg["fc2"] = st.session_state.get(f"{key}_fc2",
            st.session_state.get(f"_imp_{key}_fc2", cfg.get("fc2", "#ff6b6b")))

    return cfg
