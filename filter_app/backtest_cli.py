"""
回测 CLI 入口 — 连接 BacktestRunner + EventRecorder

用法::

    python -m filter_app.backtest_cli --ticker 03690.HK --preset 3690_HK_DP
    python -m filter_app.backtest_cli --ticker AAPL --config-file my_config.json
    python -m filter_app.backtest_cli --ticker 03690.HK --preset 3690_HK_DP --start-bar 500 --end-bar 1000 --quiet
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List
from loguru import logger

# ── 确保 filter_app 在 sys.path 上（支持 python -m filter_app.backtest_cli） ──
_pkg_dir = os.path.dirname(os.path.abspath(__file__))
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

# ── 项目内导入（与现有模块导入风格一致） ──
from services.backtest_core import BacktestRunner
from shared.constants import ALL_TFS, DEFAULT_TFS
from services.event_recorder import EventRecorder
from engine.filters import FILTERS
from data.store import ParquetStore
from data.config_db import apply_preset, list_presets
from data.db import has_data, get_conn

# 视图参数映射（与 config_db.VIEW_PARAM_SPECS 对齐）
# (preset_key_suffix, cfg_key, default)
_VIEW_SPECS: List[tuple] = [
    ("tf", "tf", None),
    ("n", "n_pts", None),
    ("sch", "show_sch", False),
    ("pred", "show_pred", False),
    ("ke", "ke", None),
    ("sm", "sm", None),
    ("ew", "ew", None),
    ("fm", "fit_mode", None),
    ("next", "n_ext", 8),
    ("fc", "fc", None),
    ("fc2", "fc2", None),
    ("strat", "show_strategy", False),
    ("sl", "stop_loss_pct", 2.0),
    ("cross_pnl", "show_cross_pnl", False),
    ("align", "show_alignment", False),
    ("pnlfb", "show_pnl_feedback", False),
]


# ═══════════════════════════════════════════════════════════════
# 参数解析
# ═══════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="多周期滤波回测 CLI — 逐 bar 运行管道计算并记录事件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m filter_app.backtest_cli --ticker 03690.HK --preset 3690_HK_DP
  python -m filter_app.backtest_cli --ticker AAPL --config-file my_config.json
  python -m filter_app.backtest_cli --ticker 03690.HK --preset 3690_HK_DP --start-bar 500 --end-bar 1000 --quiet
  python -m filter_app.backtest_cli --ticker 03690.HK --preset 3690_HK_DP --view-filter v0_日线
        """,
    )

    parser.add_argument(
        "--ticker", required=True,
        help="股票代码（如 03690.HK, AAPL）",
    )
    config_group = parser.add_mutually_exclusive_group()
    config_group.add_argument(
        "--preset",
        help="预设配置名称（从 config_db 加载）",
    )
    config_group.add_argument(
        "--config-file",
        help="JSON 配置文件路径",
    )
    parser.add_argument(
        "--start-bar", type=int, default=None,
        help="起始 bar 索引（默认：所有视图中最小的 n_pts）",
    )
    parser.add_argument(
        "--end-bar", type=int, default=None,
        help="结束 bar 索引（默认：总 bar 数）",
    )
    parser.add_argument(
        "--output-dir", default="./backtest_output/",
        help="输出根目录（默认: ./backtest_output/）",
    )
    parser.add_argument(
        "--no-save-data", action="store_true",
        help="不保存 Parquet/CSV，仅生成 JSONL 事件流",
    )
    parser.add_argument(
        "--step-interval", type=int, default=1,
        help="步进间隔（默认: 1）",
    )
    parser.add_argument(
        "--resume", default=None, metavar="PATH",
        help="从断点文件恢复回测",
    )
    parser.add_argument(
        "--checkpoint-interval", type=int, default=100,
        help="断点自动保存间隔（bar 数，默认: 100，设为 0 禁用）",
    )
    parser.add_argument(
        "--view-filter", default=None,
        help="只运行指定视图（如 v0_日线），默认运行全部",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="静默模式，只打印开始和结束信息",
    )

    return parser.parse_args()


# ═══════════════════════════════════════════════════════════════
# 配置加载
# ═══════════════════════════════════════════════════════════════

def _build_configs_from_params(params: dict) -> list:
    """从预设参数构造 4 视图配置列表。

    Parameters
    ----------
    params : dict
        ``apply_preset()`` 返回的平铺参数字典，含 ``global_f``,
        ``global_dual``, ``global_f2`` 及各视图的 ``v{i}_*`` 键。

    Returns
    -------
    list[dict]
        视图配置列表，每项包含 ``_fid``, ``tf``, ``n_pts``, ``pv`` 等字段。
    """
    fid = params.get("global_f", "sma")
    dual = params.get("global_dual", False)
    fid2 = params.get("global_f2", None)

    configs = []
    for i in range(4):
        cfg: Dict[str, Any] = {
            "_fid": fid,
            "_dual": dual,
            "_fid2": fid2,
        }

        # ── 基础视图参数 ──
        for suffix, cfg_key, default_val in _VIEW_SPECS:
            preset_key = f"v{i}_{suffix}"
            val = params.get(preset_key, default_val)
            if val is not None or default_val is not None:
                cfg[cfg_key] = val if val is not None else default_val

        # ── 主滤波器参数 (pv) ──
        cfg["pv"] = _extract_filter_params(params, fid, i, is_secondary=False)

        # ── 副滤波器参数 (pv2) ──
        if dual and fid2:
            cfg["pv2"] = _extract_filter_params(params, fid2, i, is_secondary=True)
        else:
            cfg["pv2"] = {}

        configs.append(cfg)

    return configs


def _extract_filter_params(
    params: dict, fid: str, view_index: int, is_secondary: bool = False,
) -> dict:
    """从平铺预设参数中提取某视图的滤波器参数。

    Parameters
    ----------
    params : dict
        平铺预设参数字典。
    fid : str
        滤波器 ID（如 ``"savgol"``）。
    view_index : int
        视图序号 0-3。
    is_secondary : bool
        是否为主滤波器（``True`` 时为副滤波器）。

    Returns
    -------
    dict
        ``{param_name: value}`` 字典。
    """
    sf = FILTERS.get(fid)
    if sf is None:
        return {}

    tag = "f2" if is_secondary else "f1"
    pv = {}
    for pname, (label, _lo, _hi, _step, default) in sf["params"].items():
        preset_key = f"{label}_v{view_index}_{tag}_{fid}"
        pv[pname] = params.get(preset_key, default)

    return pv


def _load_configs_from_file(path: str) -> list:
    """从 JSON 文件加载视图配置。

    支持两种格式：
    - 包含 ``"configs"`` 键的列表格式
    - 平铺预设参数字典（自动转换为 configs）

    Parameters
    ----------
    path : str
        JSON 文件路径。

    Returns
    -------
    list[dict]
        视图配置列表。

    Raises
    ------
    SystemExit
        文件不存在、JSON 解析失败或缺少必要字段时退出。
    """
    file_path = Path(path)
    if not file_path.exists():
        logger.error("配置文件不存在: {}", path)
        sys.exit(1)

    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.error("JSON 解析失败: {}", e)
        sys.exit(1)

    # 格式 1: 包含 configs/view_configs 列表
    if isinstance(data, dict):
        configs = data.get("configs") or data.get("view_configs")
        if configs is not None:
            if not isinstance(configs, list) or len(configs) == 0:
                logger.error("configs/view_configs 字段为空或格式不正确")
                sys.exit(1)
            return configs

    # 格式 2: 平铺预设参数
    if isinstance(data, dict):
        return _build_configs_from_params(data)

    logger.error("JSON 顶层必须是对象（dict）")
    sys.exit(1)


def _build_default_configs(ticker: str) -> list:
    """构建默认 4 视图配置（savgol + ema 双滤波）。

    当用户既未提供 ``--preset`` 也未提供 ``--config-file`` 时使用。

    Parameters
    ----------
    ticker : str
        股票代码（保留参数，未来可用于 ticker 级默认值）。

    Returns
    -------
    list[dict]
        4 视图默认配置列表。
    """
    configs = []
    for i, tf in enumerate(DEFAULT_TFS):
        cfg = {
            "_fid": "savgol",
            "_dual": True,
            "_fid2": "ema",
            "tf": tf,
            "n_pts": 120,
            "show_sch": True,
            "show_pred": True,
            "ke": 0.15,
            "sm": 0.05,
            "ew": 60,
            "fit_mode": "parabola",
            "n_ext": 8,
            "show_strategy": True,
            "stop_loss_pct": 2.0,
            "show_cross_pnl": False,
            "show_alignment": False,
            "show_pnl_feedback": False,
            "fc": "#00d4aa",
            "fc2": "#ff6b6b",
            "pv": {"window": 21, "order": 2},
            "pv2": {"span": 10},
        }
        configs.append(cfg)

    return configs


# ═══════════════════════════════════════════════════════════════
# Bar 范围辅助
# ═══════════════════════════════════════════════════════════════

def _get_total_bars(ticker: str, configs: list) -> int:
    """查询 ticker 在最小时间框上的总 bar 数。

    Parameters
    ----------
    ticker : str
        股票代码。
    configs : list[dict]
        视图配置列表（用于确定最小时间框）。

    Returns
    -------
    int
        总 bar 数；无数据时返回 0。
    """
    min_tf = _get_min_tf(configs)
    if not min_tf:
        return 0

    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM kline WHERE ticker=? AND timeframe=?",
                (ticker, min_tf),
            ).fetchone()
            return row[0] if row else 0
    except Exception as e:
        logger.error(f"get_total_bars failed for {ticker}/{min_tf}: {e}")
        return 0


def _get_min_window_size(configs: list) -> int:
    """所有视图中最小的 n_pts。

    Parameters
    ----------
    configs : list[dict]
        视图配置列表。

    Returns
    -------
    int
        最小窗口大小；configs 为空时返回 120。
    """
    if not configs:
        return 120
    return min(cfg.get("n_pts", 120) for cfg in configs)


def _get_min_tf(configs: list) -> str:
    """从 configs 中找到周期层级最小的（最精细的）时间框。

    Parameters
    ----------
    configs : list[dict]
        视图配置列表。

    Returns
    -------
    str
        最小周期名称；configs 为空时返回空字符串。
    """
    if not configs:
        return ""

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

    return min_tf


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def main() -> None:
    """回测 CLI 主入口。

    流程：解析参数 → 校验 ticker → 加载配置 → 确定 bar 范围 →
    创建 Runner + Recorder → 运行回测 → 保存结果 → 打印摘要。
    """
    args = parse_args()

    # ── 1. 校验 ticker 存在 ──
    if not has_data(args.ticker):
        logger.error("ticker '{}' 无数据", args.ticker)
        logger.error("提示: 请先在 Streamlit 中获取数据，或检查代码是否正确")
        sys.exit(1)

    # ── 2. 加载配置 ──
    if args.preset:
        presets = list_presets()
        match = next((p for p in presets if p["name"] == args.preset), None)
        if not match:
            available = [p["name"] for p in presets]
            logger.error("预设 '{}' 不存在", args.preset)
            logger.error("可用预设: {}", available)
            sys.exit(1)
        params = apply_preset(match["preset_id"])
        if params is None:
            logger.error("预设 '{}' 参数解析失败", args.preset)
            sys.exit(1)
        configs = _build_configs_from_params(params)
        if not args.quiet:
            logger.info("已加载预设: {} ({} 视图)", args.preset, len(configs))
    elif args.config_file:
        configs = _load_configs_from_file(args.config_file)
        if not args.quiet:
            logger.info("已加载配置文件: {} ({} 视图)", args.config_file, len(configs))
    else:
        configs = _build_default_configs(args.ticker)
        if not args.quiet:
            logger.info("使用默认配置 ({} 视图)", len(configs))

    # ── 视图过滤 ──
    if args.view_filter:
        target = args.view_filter
        filtered = []
        for idx, cfg in enumerate(configs):
            view_key = f"v{idx}_{cfg.get('tf', '')}"
            if view_key == target:
                filtered.append(cfg)
        if not filtered:
            available_views = [f"v{i}_{c.get('tf', '?')}"
                               for i, c in enumerate(configs)]
            logger.error("视图 '{}' 不在配置中", target)
            logger.error("可用视图: {}", available_views)
            sys.exit(1)
        configs = filtered
        if not args.quiet:
            logger.info("已过滤到视图: {}", target)

    # ── 3. 确定 bar 范围 ──
    total_bars = _get_total_bars(args.ticker, configs)
    if total_bars == 0:
        logger.error("ticker '{}' 在最小时间框上无 bar 数据", args.ticker)
        sys.exit(1)

    start = args.start_bar if args.start_bar is not None else _get_min_window_size(configs)
    end = args.end_bar if args.end_bar is not None else total_bars

    if start < 0:
        logger.error("start_bar={} 不能为负数", start)
        sys.exit(1)
    if start >= end:
        logger.error("bar 范围非法 (start={}, end={}, 必须 start < end)", start, end)
        sys.exit(1)
    if end > total_bars:
        logger.error("end_bar={} 超出总数 ({})", end, total_bars)
        sys.exit(1)

    if not args.quiet:
        logger.info("Bar 范围: [{}, {}) 步进={} 总量={} 预计步数≈{}",
                     start, end, args.step_interval, total_bars,
                     (end - start) // args.step_interval)

    # ── 4. 创建 Runner + Recorder ──
    try:
        runner = BacktestRunner(args.ticker, configs)
    except ValueError as e:
        logger.error("初始化 BacktestRunner 失败: {}", e)
        sys.exit(1)

    # ── 断点恢复 ──
    resume_bar = 0
    if args.resume:
        try:
            resume_bar = runner._restore_checkpoint(args.resume, configs)
            if not args.quiet:
                logger.info("已从断点恢复: bar_index={}, 文件={}", resume_bar, args.resume)
        except ValueError as e:
            logger.error("断点恢复失败: {}", e)
            sys.exit(1)
        # 如果 CLI 未显式指定 start_bar，使用断点中的进度
        if args.start_bar is None:
            start = resume_bar + 1

    # 确定断点文件路径
    checkpoint_path = None
    if args.checkpoint_interval > 0:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = str(output_dir / f"{args.ticker}_checkpoint.json")

    recorder = EventRecorder(args.output_dir, args.ticker)
    session_id = recorder.start_session({
        "ticker": args.ticker,
        "preset": args.preset,
        "config_file": args.config_file,
        "start_bar": start,
        "end_bar": end,
        "step_interval": args.step_interval,
        "configs": configs,
        "resumed_from": args.resume,
    })

    # ParquetStore for full data persistence (default on; use --no-save-data to skip)
    parquet_store = None
    if not args.no_save_data:
        parquet_store = ParquetStore(args.output_dir, args.ticker, configs)
        parquet_store.start_session()
        if not args.quiet:
            logger.info("数据存储已启用: {}", parquet_store.output_dir)

    # ── 5. 运行回测循环 ──
    results = []
    step_count = 0
    try:
        results = runner.run(
            start, end, args.step_interval,
            checkpoint_interval=args.checkpoint_interval,
            checkpoint_path=checkpoint_path,
        )
        for step_idx, output in enumerate(results):
            recorder.record_step(step_idx, output["cutoff_date"], output)
            if parquet_store:
                parquet_store.append_row(
                    bar_index=output["bar_index"],
                    bar_timestamp=output["bar_timestamp"],
                    cutoff_date=output["cutoff_date"],
                    stage_outputs=output,
                )
            step_count = step_idx + 1
            if not args.quiet:
                bar_index = output["step_index"]
                logger.info("[{}] bar={}/{}  {}", step_count, bar_index, end - 1,
                             output['cutoff_date'])
    except KeyboardInterrupt:
        logger.warning("中断，正在保存...")
    except (ValueError, IndexError) as e:
        logger.error("回测运行失败: {}", e)
        recorder.end_session()
        sys.exit(1)
    finally:
        recorder.end_session()
        if parquet_store:
            parquet_store.end_session()
            if not args.quiet:
                logger.info("回测数据已保存到: {}", parquet_store.output_dir)

    # ── 6. 打印摘要 ──
    output_path = Path(args.output_dir) / f"{args.ticker}_{session_id}"
    logger.info("完成! {} 步已保存到 {}/", step_count, output_path)
    if not args.quiet:
        logger.info("  - events.jsonl")
        logger.info("  - bs_snapshot.jsonl")
        logger.info("  - filter_tail.jsonl")
        logger.info("  - schmitt_snapshot.jsonl")
        logger.info("  - trade_summary.jsonl")
        logger.info("  - metadata.json")


if __name__ == "__main__":
    main()
