"""
回测核心编排器 — 零 Streamlit 依赖

逐 bar 遍历历史数据，运行完整管道计算并收集每步输出。
复用现有 services/ 模块的纯函数，对齐 _render_chart 的管道调用顺序。
"""

import numpy as np
import pandas as pd
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from loguru import logger

from .filter_engine import (
    FILTERS,
    _schmitt_trigger,
    _find_all_pairs,
    _fit_physics_parabola,
    _compute_strategy_pnl,
    _align_pnl_to_current_tf,
    _compute_holding_masks,
)
from .data_loader import _sync_all_cascading
from .bs_marker import compute_bs_markers
from db import get_conn

# 周期层级定义（与 components/sidebar.py 保持一致）
ALL_TFS = ["1分钟", "5分钟", "15分钟", "60分钟", "日线", "周线", "月线", "季线"]
TF_HIERARCHY = {
    "1分钟": "5分钟", "5分钟": "15分钟", "15分钟": "60分钟",
    "60分钟": "日线", "日线": "周线", "周线": "月线",
    "月线": "季线", "季线": None,
}


class BacktestRunner:
    """编排回测管道计算，零 Streamlit 依赖。

    对每个 bar 索引依次执行：数据同步 → 窗口加载 → 管道计算，
    收集所有阶段的中间输出供下游分析使用。

    Examples
    --------
    >>> configs = [{"tf": "日线", "n_pts": 120, "_fid": "sma", "pv": {"window": 11}, ...}]
    >>> runner = BacktestRunner("03690", configs)
    >>> results = runner.run(0, 500, step_interval=1)
    """

    def __init__(self, ticker: str, configs: list) -> None:
        """
        Parameters
        ----------
        ticker : str
            股票代码（DB 中存储的原始代码，如 ``"03690"``）。
        configs : list[dict]
            视图配置列表，每个 dict 至少包含 ``"tf"`` 和 ``"n_pts"`` 字段。
            完整字段参考：``tf``, ``n_pts``, ``_fid``, ``pv``, ``_dual``,
            ``_fid2``, ``pv2``, ``ew``, ``ke``, ``sm``, ``show_sch``,
            ``show_strategy``, ``show_pred``, ``stop_loss_pct``, ``n_ext``,
            ``fc``, ``fc2``, ``fit_mode``。

        Raises
        ------
        ValueError
            若 configs 为空或缺少必要字段。
        """
        if not ticker or not ticker.strip():
            raise ValueError("ticker 不能为空")
        if not configs:
            raise ValueError("configs 不能为空")

        self.ticker = ticker.strip()
        self.configs = configs

        # ── 提取周期信息 ──
        self._tfs_in_use: list[str] = sorted(
            set(cfg["tf"] for cfg in configs),
            key=lambda x: ALL_TFS.index(x),
        )
        self._min_tf: str = self._tfs_in_use[0]  # ALL_TFS 索引最小 = 最精细

        # 每 TF 的最大 n_pts（多个视图可能共享同一 TF）
        self._tf_n_pts: dict[str, int] = {}
        for cfg in configs:
            tf = cfg["tf"]
            n = cfg.get("n_pts", 120)
            self._tf_n_pts[tf] = max(self._tf_n_pts.get(tf, 0), n)

        # bar 总数
        self._bar_count: int = self._query_bar_count()

        # 跨窗口 EWMA + 施密特状态（回测连续模式用，避免信号跳变）
        # {view_key: {"init_mu": float, "init_sigma": float, "state": int, "dur": int}}
        self._ewma_state: dict[str, dict] = {}

        logger.info(
            "BacktestRunner 初始化: ticker={}, min_tf={}, tfs={}, bar_count={}, views={}",
            self.ticker, self._min_tf, self._tfs_in_use, self._bar_count, len(configs),
        )

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def run(
        self,
        start_bar: int,
        end_bar: int,
        step_interval: int = 1,
    ) -> list[dict]:
        """逐 bar 运行回测管道，返回每步的管道输出列表。

        对 [start_bar, end_bar) 范围内的每个 bar 索引，同步数据、
        加载窗口、计算所有视图的管道输出。

        Parameters
        ----------
        start_bar : int
            起始 bar 索引（含）。
        end_bar : int
            结束 bar 索引（不含）。
        step_interval : int, default 1
            步进间隔；设为 N 表示每隔 N 个 bar 采样一次。

        Returns
        -------
        list[dict]
            每步的结果字典：
            ``[{"step_index": N, "bar_index": N, "bar_timestamp": "...",
               "cutoff_date": "...", "views": {"v0_日线": {...}, ...},
               "ohlcv": {"open": ..., "high": ..., "low": ...,
                          "close": ..., "volume": ...}}, ...]``

            每个 view 的输出包含管道各阶段数据：``t``, ``dates``, ``noisy``,
            ``ohlc``, ``ohlcv``, ``filtered``, ``filtered2``, ``schmitt``,
            ``all_pairs``, ``prediction_pairs``, ``long_pnl``, ``short_pnl``,
            ``trade_records``, ``long_mask``, ``short_mask``, ``bs_markers``。

        Raises
        ------
        ValueError
            若 ticker 无数据。
        IndexError
            若 bar_index 超出有效范围。
        """
        if self._bar_count == 0:
            raise ValueError(f"ticker '{self.ticker}' 在数据库中无数据")

        max_bar = self._bar_count - 1
        if start_bar < 0:
            raise IndexError(f"start_bar={start_bar} 不能为负数")
        if end_bar > self._bar_count:
            raise IndexError(
                f"end_bar={end_bar} 超出有效范围 (max={max_bar})"
            )

        results: list[dict] = []

        for bar_index in range(start_bar, end_bar, step_interval):
            bar_info = self._get_bar_info(bar_index)
            cutoff_date = bar_info["cutoff_date"]
            logger.debug(
                "回测步骤: bar_index={}, cutoff_date={}",
                bar_index, cutoff_date,
            )

            # 1) 同步数据（写入 parquet）
            self._sync_data(cutoff_date)

            # 2) 逐视图加载窗口数据并运行管道（按 TF 从粗到细排序）
            view_outputs: dict[str, dict] = {}
            # 存储每 TF 的 PnL 结果，供低周期视图做跨周期对齐
            tf_pnl_cache: dict[str, dict] = {}

            sorted_views = sorted(
                enumerate(self.configs),
                key=lambda x: ALL_TFS.index(x[1]["tf"]),
                reverse=True,  # 粗→细
            )

            for view_index, view_cfg in sorted_views:
                tf = view_cfg["tf"]
                n_pts = view_cfg.get("n_pts", 120)
                view_key = f"v{view_index}_{tf}"

                # 加载窗口数据
                window_data = self._load_window_data(tf, n_pts)
                if window_data is None:
                    logger.warning("视图 {} 窗口数据为空，跳过", view_key)
                    continue

                # 跨窗口 EWMA 初始状态（首次为 None → 正常初始化）
                ewma_init = self._ewma_state.get(view_key)

                # 运行管道
                stage_output = self._compute_pipeline_for_view(
                    view_cfg, window_data, ewma_init=ewma_init,
                )

                # 保存本窗口 EWMA + 施密特末态，供下一窗口使用
                schmitt = stage_output.get("schmitt")
                if schmitt is not None:
                    self._ewma_state[view_key] = {
                        "init_mu": schmitt.get("final_mu", 0.0),
                        "init_sigma": schmitt.get("final_sigma", 0.0),
                        "state": schmitt.get("final_state", 0),
                        "dur": schmitt.get("final_dur", 0),
                    }

                # 跨周期 PnL 对齐：检查是否有高周期 PnL 可用
                higher_tf = TF_HIERARCHY.get(tf)
                higher_pnl = tf_pnl_cache.get(higher_tf) if higher_tf else None
                stage_output["higher_pnl"] = higher_pnl

                # 计算持仓掩码和 BS 标记
                long_mask, short_mask = self._compute_masks_for_view(
                    stage_output, higher_pnl,
                )
                stage_output["long_mask"] = long_mask
                stage_output["short_mask"] = short_mask

                bs_markers = self._compute_bs_for_view(
                    stage_output, view_cfg, long_mask, short_mask,
                )
                stage_output["bs_markers"] = bs_markers

                view_outputs[view_key] = stage_output

                # 缓存本 TF 的 PnL 数据供更低周期使用
                if stage_output.get("long_pnl") is not None:
                    tf_pnl_cache[tf] = {
                        "dates": stage_output["dates"],
                        "long_pnl": stage_output["long_pnl"],
                        "short_pnl": stage_output["short_pnl"],
                        "trade_records": stage_output["trade_records"],
                    }

            results.append({
                "step_index": bar_index,
                "bar_index": bar_index,
                "bar_timestamp": bar_info["bar_timestamp"],
                "cutoff_date": cutoff_date,
                "views": view_outputs,
                "ohlcv": bar_info["ohlcv"],
            })

        logger.info(
            "回测完成: ticker={}, bar 范围=[{},{}), 间隔={}, 步数={}",
            self.ticker, start_bar, end_bar, step_interval, len(results),
        )
        return results

    def get_bar_count(self) -> int:
        """返回 min_tf 上的总 bar 数。"""
        return self._bar_count

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _get_bar_info(self, bar_index: int) -> dict:
        """从 DB 查询 bar_index 对应的 bar 信息。

        以 min_tf 为基准周期，查询第 bar_index 条记录的 ts、OHLCV。

        Parameters
        ----------
        bar_index : int
            零基 bar 索引。

        Returns
        -------
        dict
            ``{"bar_timestamp": str, "cutoff_date": str, "ohlcv": {...}}``。

        Raises
        ------
        IndexError
            若 bar_index 超出有效范围。
        """
        if bar_index < 0 or bar_index >= self._bar_count:
            raise IndexError(
                f"bar_index={bar_index} 超出范围 [0, {self._bar_count})"
            )

        with get_conn() as conn:
            row = conn.execute(
                """SELECT ts, open, high, low, close, volume FROM kline
                   WHERE ticker=? AND timeframe=?
                   ORDER BY ts ASC LIMIT 1 OFFSET ?""",
                (self.ticker, self._min_tf, bar_index),
            ).fetchone()

        if row is None:
            raise IndexError(
                f"bar_index={bar_index} 在 DB 中无对应数据"
            )
        return {
            "bar_timestamp": row["ts"],
            "cutoff_date": row["ts"],
            "ohlcv": {
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
            },
        }

    def _sync_data(self, cutoff_date: str) -> None:
        """同步数据到 display parquet（级联合成）。

        调用 ``_sync_all_cascading`` 将所有在用 TF 的窗口数据写入
        ``data/display/{tf}.parquet``。

        Parameters
        ----------
        cutoff_date : str
            回测截止日期字符串。
        """
        results = _sync_all_cascading(
            self.ticker,
            self._tfs_in_use,
            cutoff_date,
            self._min_tf,
            n_pts=self._tf_n_pts,
        )
        failed = [tf for tf, ok in results.items() if not ok]
        if failed:
            logger.warning("数据同步部分失败: ticker={}, cutoff={}, failed={}",
                           self.ticker, cutoff_date, failed)
        if len(failed) == len(self._tfs_in_use):
            logger.error("数据同步全部失败: ticker={}, cutoff={}",
                         self.ticker, cutoff_date)

    def _load_window_data(
        self, tf: str, n_pts: int,
    ) -> Optional[Tuple[np.ndarray, np.ndarray, pd.DataFrame, pd.DatetimeIndex]]:
        """从 ``data/display/{tf}.parquet`` 加载窗口数据。

        对齐 ``_load_chart_data`` 中回测模式的 parquet 读取逻辑。

        Parameters
        ----------
        tf : str
            周期名称。
        n_pts : int
            期望的数据点数（用于校验）。

        Returns
        -------
        Optional[Tuple[np.ndarray, np.ndarray, pd.DataFrame, pd.DatetimeIndex]]
            ``(t, noisy, ohlc, dates)`` — bar 索引、收盘价、OHLC DataFrame、
            日期索引。parquet 不存在或数据不足时返回 ``None``。
        """
        display_path = (
            Path(__file__).parent.parent.parent / "data" / "display" / self.ticker / f"{tf}.parquet"
        )
        if not display_path.exists():
            logger.warning("parquet 不存在: {}", display_path)
            return None

        try:
            df = pd.read_parquet(display_path)
        except Exception as e:
            logger.warning("parquet 读取失败 {}: {}", display_path, e)
            return None

        if "Date" not in df.columns or "Close" not in df.columns:
            logger.warning("parquet {} 缺少 Date/Close 列", display_path)
            return None
        if len(df) < 2:
            logger.warning("parquet {} 数据点不足 (len={})", display_path, len(df))
            return None

        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index()

        t = np.arange(len(df), dtype=float)
        noisy = df["Close"].values.ravel()

        if all(c in df.columns for c in ["Open", "High", "Low"]):
            ohlc = df[["Open", "High", "Low", "Close"]]
        else:
            ohlc = pd.DataFrame(
                {"Open": noisy, "High": noisy, "Low": noisy, "Close": noisy},
                index=df.index,
            )

        return t, noisy, ohlc, df.index

    def _compute_pipeline_for_view(
        self, view_cfg: dict, window_data: tuple,
        ewma_init: Optional[dict] = None,
    ) -> dict:
        """对单个视图运行完整管道计算（步骤 1–6）。

        管道顺序精确对齐 ``_render_chart`` 中的调用顺序：
        1. ``_compute_filters(noisy, t, cfg)`` → filtered, filtered2
        2. ``_compute_schmitt_trigger(filtered, t, cfg)`` → schmitt dict
        3. ``_find_all_pairs(schmitt["sig"])`` → all_pairs
        4. ``_compute_prediction_pairs(t, filtered, schmitt, cfg, all_pairs)`` → pred_pairs
        5. ``_compute_strategy_pnl(t, filtered, sig, all_pairs, pred_pairs, ...)`` → PnL
        6. (跨周期对齐与 BS 标记由 ``run`` 负责)

        Parameters
        ----------
        view_cfg : dict
            视图配置，包含 ``tf``, ``_fid``, ``pv``, ``_dual``, ``_fid2``,
            ``pv2``, ``ew``, ``ke``, ``sm``, ``show_sch``, ``show_strategy``,
            ``show_pred``, ``stop_loss_pct``, ``n_ext``, ``fit_mode`` 等字段。
        window_data : tuple
            ``(t, noisy, ohlc, dates)`` 来自 ``_load_window_data``。
        ewma_init : Optional[dict], optional
            跨窗口 EWMA 初始状态 ``{"init_mu": float, "init_sigma": float}``；
            用于回测连续模式下跨窗口传递 EWMA 状态，避免信号跳变。

        Returns
        -------
        dict
            管道各阶段输出，键名对应阶段产物：
            ``t``, ``dates``, ``noisy``, ``ohlc``, ``ohlcv``, ``filtered``,
            ``filtered2``, ``schmitt``, ``all_pairs``, ``prediction_pairs``,
            ``long_pnl``, ``short_pnl``, ``trade_records``。
        """
        t, noisy, ohlc, dates = window_data
        tf = view_cfg["tf"]

        # ── Step 1: 计算滤波器 ──
        filtered, filtered2 = self._compute_filters(noisy, t, view_cfg)

        # ── Step 2: 施密特触发器 ──
        init_mu = ewma_init.get("init_mu") if ewma_init else None
        init_sigma = ewma_init.get("init_sigma") if ewma_init else None
        init_state = ewma_init.get("state", 0) if ewma_init else 0
        init_dur = ewma_init.get("dur", 0) if ewma_init else 0
        schmitt = self._compute_schmitt_trigger(
            filtered, t, view_cfg,
            init_mu=init_mu, init_sigma=init_sigma,
            init_state=init_state, init_dur=init_dur,
        )

        # ── Step 3: 查找多空切换对 ──
        all_pairs: list = []
        if schmitt is not None:
            all_pairs = _find_all_pairs(schmitt["sig"])

        # ── Step 4: 预测曲线 ──
        pred_pairs = self._compute_prediction_pairs(
            t, filtered, schmitt, view_cfg, all_pairs,
        )

        # ── Step 5: 策略 PnL ──
        long_pnl, short_pnl, trade_records = self._compute_strategy_for_view(
            t, filtered, schmitt, all_pairs, pred_pairs, view_cfg,
        )

        return {
            "t": t,
            "dates": dates,
            "noisy": noisy,
            "ohlc": ohlc,
            "ohlcv": {
                "close": float(noisy[-1]) if len(noisy) > 0 else None,
                "open": float(ohlc["Open"].iloc[-1]),
                "high": float(ohlc["High"].iloc[-1]),
                "low": float(ohlc["Low"].iloc[-1]),
                "volume": float(ohlc["Volume"].iloc[-1]) if "Volume" in ohlc.columns else 0.0,
            },
            "filtered": filtered,
            "filtered2": filtered2,
            "schmitt": schmitt,
            "all_pairs": all_pairs,
            "prediction_pairs": pred_pairs,
            "long_pnl": long_pnl,
            "short_pnl": short_pnl,
            "trade_records": trade_records,
        }

    # ------------------------------------------------------------------
    # 管道阶段方法（纯函数，对齐 streamlit_app 中的对应逻辑）
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_filters(
        noisy: np.ndarray, t: np.ndarray, cfg: dict,
    ) -> Tuple[np.ndarray, Optional[np.ndarray]]:
        """计算主线滤波与可选的副线滤波。

        对齐 ``streamlit_app._compute_filters`` 的逻辑，去掉 ``@st.cache_data``。

        Parameters
        ----------
        noisy : np.ndarray
            原始价格序列。
        t : np.ndarray
            时间索引。
        cfg : dict
            视图配置。

        Returns
        -------
        Tuple[np.ndarray, Optional[np.ndarray]]
            ``(filtered, filtered2)`` — 主线滤波结果与副线结果（可能为 None）。
        """
        sf = FILTERS.get(cfg["_fid"])
        if sf is None:
            logger.warning("未知 filter_id '{}', 使用 NaN", cfg["_fid"])
            filtered = np.full_like(noisy, np.nan)
            return filtered, None

        try:
            filtered = sf["func"](noisy, t, **cfg["pv"])
            filtered = np.asarray(filtered, dtype=float).ravel()
        except Exception as e:
            logger.error("滤波器 {} 失败: {}", cfg["_fid"], e)
            filtered = np.full_like(noisy, np.nan)

        filtered2: Optional[np.ndarray] = None
        if cfg.get("_dual") and cfg.get("_fid2") and cfg.get("pv2"):
            try:
                sf2 = FILTERS.get(cfg["_fid2"])
                if sf2 is None:
                    logger.warning("未知 filter_id2 '{}'", cfg["_fid2"])
                    filtered2 = np.full_like(noisy, np.nan)
                else:
                    filtered2 = sf2["func"](noisy, t, **cfg["pv2"])
                filtered2 = np.asarray(filtered2, dtype=float).ravel()
            except Exception as e:
                logger.warning("副线滤波器 {} 失败: {}", cfg["_fid2"], e)
                filtered2 = np.full_like(noisy, np.nan)

        return filtered, filtered2

    @staticmethod
    def _compute_schmitt_trigger(
        filtered: np.ndarray, t: np.ndarray, cfg: dict,
        init_mu: Optional[float] = None,
        init_sigma: Optional[float] = None,
        init_state: int = 0,
        init_dur: int = 0,
    ) -> Optional[dict]:
        """计算施密特触发器信号。

        对齐 ``streamlit_app._compute_schmitt_trigger`` 的逻辑。

        Parameters
        ----------
        filtered : np.ndarray
            滤波价格序列。
        t : np.ndarray
            时间索引。
        cfg : dict
            视图配置，需含 ``show_sch``, ``ew``, ``ke``, ``sm``。
        init_mu : Optional[float], optional
            跨窗口 EWMA 均值初始值（回测连续模式用）。
        init_sigma : Optional[float], optional
            跨窗口 EWMA 标准差初始值（回测连续模式用）。
        init_state : int, optional
            跨窗口施密特状态初始值（回测连续模式用，默认 0）。
        init_dur : int, optional
            跨窗口施密特持续期数初始值（回测连续模式用，默认 0）。

        Returns
        -------
        Optional[dict]
            施密特触发器输出字典；数据不足时返回 ``None``。
        """
        if not cfg.get("show_sch") or np.all(np.isnan(filtered)) or len(t) < 2:
            return None

        v = np.gradient(filtered, t)
        a = np.gradient(v, t)
        result = _schmitt_trigger(
            v, a,
            ewma_span=cfg.get("ew", 60),
            k_eps=cfg.get("ke", 0.15),
            sigma_min=cfg.get("sm", 0.05),
            init_mu=init_mu,
            init_sigma=init_sigma,
            init_state=init_state,
            init_dur=init_dur,
        )
        if result is not None:
            result["v"] = v
            result["a"] = a
        return result

    @staticmethod
    def _compute_prediction_pairs(
        t: np.ndarray,
        filtered: np.ndarray,
        schmitt: Optional[dict],
        cfg: dict,
        all_pairs: list,
    ) -> list:
        """计算每对多空信号的预测曲线。

        对齐 ``streamlit_app._compute_prediction_pairs`` 的逻辑。

        Parameters
        ----------
        t : np.ndarray
            时间索引。
        filtered : np.ndarray
            滤波价格序列。
        schmitt : Optional[dict]
            施密特触发器输出。
        cfg : dict
            视图配置，需含 ``show_pred``。
        all_pairs : list
            多空切换对列表。

        Returns
        -------
        list[dict]
            预测曲线数据列表。
        """
        if not cfg.get("show_pred") or schmitt is None:
            return []

        pred_pairs = []
        for pair_start, pair_end in all_pairs:
            if pair_end - pair_start >= 3:
                fit_result = _fit_physics_parabola(t, filtered, pair_start, pair_end)
                if fit_result is not None:
                    pred_pairs.append({
                        "fit_result": fit_result,
                        "fit_start": pair_start,
                        "pair_end": pair_end,
                    })
        return pred_pairs

    @staticmethod
    def _compute_strategy_for_view(
        t: np.ndarray,
        filtered: np.ndarray,
        schmitt: Optional[dict],
        all_pairs: list,
        pred_pairs: list,
        cfg: dict,
    ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], list]:
        """计算策略 PnL。

        对齐 ``streamlit_app._compute_strategy_display`` 中
        ``_compute_strategy_pnl`` 的调用逻辑，去掉 UI 部分。

        Parameters
        ----------
        t : np.ndarray
            时间索引。
        filtered : np.ndarray
            滤波价格序列。
        schmitt : Optional[dict]
            施密特触发器输出。
        all_pairs : list
            多空切换对列表。
        pred_pairs : list
            预测曲线数据列表。
        cfg : dict
            视图配置，需含 ``show_strategy``, ``stop_loss_pct``, ``n_ext``。

        Returns
        -------
        Tuple[Optional[np.ndarray], Optional[np.ndarray], list]
            ``(long_pnl, short_pnl, trade_records)``。
        """
        show_strategy = cfg.get("show_strategy", False)
        if not show_strategy or schmitt is None or len(pred_pairs) == 0:
            n = len(t)
            return (
                np.full(n, 100.0),
                np.full(n, 100.0),
                [],
            )

        stop_loss_pct = cfg.get("stop_loss_pct", 2.0)
        n_extend = cfg.get("n_ext", 10)

        long_pnl, short_pnl, trade_records = _compute_strategy_pnl(
            t, filtered, schmitt["sig"], all_pairs, pred_pairs,
            stop_loss_pct, n_extend=n_extend,
        )
        return long_pnl, short_pnl, trade_records

    @staticmethod
    def _compute_masks_for_view(
        stage_output: dict,
        higher_pnl: Optional[dict],
    ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """计算持仓掩码（从高周期 PnL 对齐到当前周期）。

        Parameters
        ----------
        stage_output : dict
            当前视图的管道阶段输出（含 ``t``, ``dates``）。
        higher_pnl : Optional[dict]
            紧邻高周期的 PnL 数据，含 ``dates``, ``long_pnl``,
            ``short_pnl``, ``trade_records``。

        Returns
        -------
        Tuple[Optional[np.ndarray], Optional[np.ndarray]]
            ``(long_mask, short_mask)`` — bool 数组，无高周期数据时均为 ``None``。
        """
        if higher_pnl is None:
            return None, None

        try:
            aligned = _align_pnl_to_current_tf(
                higher_pnl["dates"],
                higher_pnl["long_pnl"],
                higher_pnl["short_pnl"],
                higher_pnl["trade_records"],
                stage_output["dates"],
            )
            long_mask, short_mask = _compute_holding_masks(
                len(stage_output["t"]),
                aligned["entry_markers"],
                aligned["exit_markers"],
            )
            return long_mask, short_mask
        except Exception as e:
            logger.warning("跨周期对齐失败: {}", e)
            return None, None

    @staticmethod
    def _compute_bs_for_view(
        stage_output: dict,
        view_cfg: dict,
        long_mask: Optional[np.ndarray],
        short_mask: Optional[np.ndarray],
    ) -> dict:
        """计算 BS 仓位操作标记。

        Parameters
        ----------
        stage_output : dict
            当前视图的管道阶段输出。
        view_cfg : dict
            视图配置（含 ``tf``）。
        long_mask : Optional[np.ndarray]
            做多持仓掩码。
        short_mask : Optional[np.ndarray]
            做空持仓掩码。

        Returns
        -------
        dict
            ``{"entry_markers": [...], "exit_markers": [...]}``。
        """
        holding_masks = None
        if long_mask is not None and short_mask is not None:
            holding_masks = (long_mask, short_mask)

        return compute_bs_markers(
            t=stage_output["t"],
            dates=stage_output["dates"],
            schmitt=stage_output.get("schmitt"),
            all_pairs=stage_output.get("all_pairs", []),
            trade_records=stage_output.get("trade_records", []),
            tf=view_cfg["tf"],
            operating_tf=view_cfg["tf"],  # 回测中每视图独立操作
            holding_masks=holding_masks,
        )

    # ------------------------------------------------------------------
    # DB 查询
    # ------------------------------------------------------------------

    def _query_bar_count(self) -> int:
        """查询 min_tf 上的总 bar 数。

        Returns
        -------
        int
            bar 总数；ticker 无数据时返回 0。
        """
        try:
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM kline WHERE ticker=? AND timeframe=?",
                    (self.ticker, self._min_tf),
                ).fetchone()
                return row[0] if row else 0
        except Exception as e:
            logger.warning("查询 bar_count 失败: {}", e)
            return 0


# ═══════════════════════════════════════════════════════════════
# 按需重算函数
# ═══════════════════════════════════════════════════════════════

def replay_bar(
    ticker: str,
    bar_index: int,
    view_configs: list[dict],
) -> Optional[dict]:
    """重算指定 bar 的完整管道输出，与 Streamlit 回测状态完全一致。

    创建 ``BacktestRunner`` 实例，在目标 bar 上运行一步管道计算，
    返回该 bar 的完整 stage_output。

    Parameters
    ----------
    ticker : str
        股票代码（DB 中存储的原始代码，如 ``"AAPL"``）。
    bar_index : int
        目标 bar 索引（min_tf 上的全局索引）。
    view_configs : list[dict]
        视图配置列表（从 metadata.json 的 ``config.configs`` 获取）。

    Returns
    -------
    Optional[dict]
        该 bar 的完整管道输出，结构与 ``BacktestRunner.run()``
        单步结果相同：``{"bar_index": ..., "bar_timestamp": ...,
        "cutoff_date": ..., "views": {...}, "ohlcv": {...}}``。
        若数据不足或重算失败则返回 ``None``。

    Notes
    -----
    边界处理：若 ``bar_index`` 小于所有视图中最大的 ``n_pts``，
    则自动调整为 ``max(n_pts)``，以确保有足够的历史窗口数据。
    """
    if not ticker or not ticker.strip():
        logger.warning("replay_bar: ticker 为空")
        return None
    if not view_configs:
        logger.warning("replay_bar: view_configs 为空")
        return None

    ticker = ticker.strip()

    # 计算窗口安全边界：bar_index 必须 >= max(n_pts)
    max_n_pts = max(cfg.get("n_pts", 120) for cfg in view_configs)
    if bar_index < max_n_pts:
        logger.info(
            "replay_bar: bar_index={} < max_n_pts={}, 自动调整为 {}",
            bar_index, max_n_pts, max_n_pts,
        )
        bar_index = max_n_pts

    runner = BacktestRunner(ticker, view_configs)
    total_bars = runner.get_bar_count()
    if bar_index >= total_bars:
        logger.warning(
            "replay_bar: bar_index={} 超出范围 (total={})", bar_index, total_bars,
        )
        return None

    try:
        results = runner.run(bar_index, bar_index + 1, 1)
    except (ValueError, IndexError) as e:
        logger.error("replay_bar 执行失败: ticker={}, bar_index={}, error={}",
                     ticker, bar_index, e)
        return None

    if results:
        return results[0]
    return None
