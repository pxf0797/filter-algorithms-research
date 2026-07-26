#!/usr/bin/env python3
"""
重放指定 bar 的 Streamlit 回测状态 — 按需重算完整管道输出。

用法:
    # 从 parquet 文件所在目录加载配置，重放指定 bar
    python tools/replay_bar.py --parquet test_backtest_output/AAPL_xxx/backtest_result.parquet --bar 450

    # 直接指定 ticker 和 preset
    python tools/replay_bar.py --ticker AAPL --preset AAPL_US --bar 450

    # 输出 JSON 格式的完整结果
    python tools/replay_bar.py --parquet test_backtest_output/AAPL_xxx/backtest_result.parquet --bar 450 --json
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# 将 filter 加入搜索路径
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from filter.backtest.engine import replay_bar
from filter.config_db import get_preset_by_name


# ── 序列化辅助 ──

def _make_json_safe(obj):
    """递归将 numpy 类型转换为 JSON 兼容的 Python 原生类型。"""
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        val = float(obj)
        return None if np.isnan(val) or val == float('inf') or val == float('-inf') else val
    if isinstance(obj, np.ndarray):
        return [_make_json_safe(x) for x in obj.tolist()]
    if isinstance(obj, dict):
        return {str(k): _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_json_safe(x) for x in obj]
    if isinstance(obj, (pd.Timestamp,)):
        return obj.isoformat()
    if isinstance(obj, pd.DatetimeIndex):
        return [t.isoformat() for t in obj]
    if isinstance(obj, pd.DataFrame):
        return {str(k): _make_json_safe(obj[k].values) for k in obj.columns}
    if isinstance(obj, pd.Index):
        return [_make_json_safe(x) for x in obj.tolist()]
    return obj


# ── 配置加载 ──

def _load_configs_from_parquet_dir(parquet_path: str) -> tuple[str, list[dict]]:
    """从 parquet 同级目录的 metadata.json 中提取 ticker 和 view_configs。

    Returns
    -------
    tuple[str, list[dict]]
        ``(ticker, configs_list)`` — configs_list 可直接传给 ``replay_bar()``。
    """
    meta_path = Path(parquet_path).parent / "metadata.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"metadata.json 不存在: {meta_path}")

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    ticker = meta.get("ticker")
    if not ticker:
        raise ValueError("metadata.json 缺少 ticker 字段")

    # 优先使用 config.configs（列表格式），兼容旧格式
    config = meta.get("config", {})
    configs = config.get("configs")
    if configs:
        return ticker, configs

    # 回退到 metadata 顶层的 view_configs（dict 格式），转为列表
    view_configs_dict = meta.get("view_configs")
    if view_configs_dict:
        configs_list = list(view_configs_dict.values())
        return ticker, configs_list

    raise ValueError("metadata.json 中未找到 configs 或 view_configs")


def _load_configs_from_preset(preset_name: str) -> list[dict]:
    """从预设名称加载视图配置。

    复用 ``backtest_cli._build_configs_from_params`` 的配置构造逻辑。
    """
    from filter.config_db import apply_preset as _apply_preset_db
    from filter.backtest_cli import _build_configs_from_params

    preset = get_preset_by_name(preset_name)
    if preset is None:
        raise ValueError(f"预设不存在: {preset_name}")

    params = _apply_preset_db(preset["preset_id"])
    if params is None:
        raise ValueError(f"无法解析预设参数: {preset_name}")

    return _build_configs_from_params(params)


# ── 摘要输出 ──

def _print_summary(result: dict) -> None:
    """打印 replay 结果的易读摘要。"""
    bar_index = result.get("bar_index")
    bar_ts = result.get("bar_timestamp")
    cutoff = result.get("cutoff_date")
    ohlcv = result.get("ohlcv", {})
    views = result.get("views", {})

    print(f"\n{'='*60}")
    print("  Bar 重放摘要")
    print(f"{'='*60}")
    print(f"  bar_index:   {bar_index}")
    print(f"  timestamp:   {bar_ts}")
    print(f"  cutoff_date: {cutoff}")
    print(f"  OHLCV:       O={ohlcv.get('open')} H={ohlcv.get('high')} "
          f"L={ohlcv.get('low')} C={ohlcv.get('close')} V={ohlcv.get('volume')}")
    print(f"  视图数:      {len(views)}")
    print()

    for view_key, vdata in sorted(views.items()):
        sig_val = "N/A"
        schmitt = vdata.get("schmitt")
        if schmitt is not None:
            sig_arr = schmitt.get("sig")
            if sig_arr is not None and len(sig_arr) > 0:
                sig_val = f"{sig_arr[-1]:+.2f}" if isinstance(sig_arr[-1], float) else str(sig_arr[-1])

        long_pnl = vdata.get("long_pnl")
        short_pnl = vdata.get("short_pnl")
        pnl_long = f"{long_pnl[-1]:.2f}" if (long_pnl is not None and len(long_pnl) > 0) else "N/A"
        pnl_short = f"{short_pnl[-1]:.2f}" if (short_pnl is not None and len(short_pnl) > 0) else "N/A"

        pairs_count = len(vdata.get("all_pairs", []))
        pred_count = len(vdata.get("prediction_pairs", []))
        trades_count = len(vdata.get("trade_records", []))

        bs = vdata.get("bs_markers", {})
        bs_entry_count = len(bs.get("entry_markers", [])) if bs else 0
        bs_exit_count = len(bs.get("exit_markers", [])) if bs else 0

        window_size = len(vdata.get("noisy", [])) if vdata.get("noisy") is not None else 0

        print(f"  [{view_key}]")
        print(f"    窗口大小:       {window_size} 点")
        print(f"    信号末值 (sig):  {sig_val}")
        print(f"    PnL (做多/做空):  {pnl_long} / {pnl_short}")
        print(f"    信号对 / 预测对:  {pairs_count} / {pred_count}")
        print(f"    交易记录:        {trades_count}")
        print(f"    BS 标记 (进出):   {bs_entry_count} / {bs_exit_count}")
        print()

    print(f"{'='*60}")


def _print_json(result: dict) -> None:
    """打印 JSON 格式的完整结果。"""
    safe = _make_json_safe(result)
    print(json.dumps(safe, ensure_ascii=False, indent=2))


# ── CLI ──

def main():
    parser = argparse.ArgumentParser(
        description="重放指定 bar 的 Streamlit 回测状态",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python tools/replay_bar.py --parquet test_backtest_output/AAPL_xxx/backtest_result.parquet --bar 450
  python tools/replay_bar.py --ticker AAPL --preset AAPL_US --bar 450
  python tools/replay_bar.py --parquet result.parquet --bar 450 --json > replay.json
        """,
    )
    # 数据源互斥组
    src_group = parser.add_mutually_exclusive_group(required=True)
    src_group.add_argument("--parquet", metavar="PATH", help="Parquet 文件路径（自动加载同级目录的 metadata.json）")
    src_group.add_argument("--ticker", metavar="SYM", help="股票代码（需配合 --preset）")

    parser.add_argument("--preset", metavar="NAME", help="预设名称（与 --ticker 一起使用）")
    parser.add_argument("--bar", type=int, required=True, help="目标 bar 索引")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出完整结果")

    args = parser.parse_args()

    # ── 确定 ticker 和 view_configs ──
    ticker: str
    view_configs: list[dict]

    if args.parquet:
        parquet_path = args.parquet
        if not Path(parquet_path).exists():
            print(f"错误：parquet 文件不存在: {parquet_path}", file=sys.stderr)
            sys.exit(1)
        try:
            ticker, view_configs = _load_configs_from_parquet_dir(parquet_path)
        except (FileNotFoundError, ValueError) as e:
            print(f"错误：{e}", file=sys.stderr)
            sys.exit(1)

    elif args.ticker:
        if not args.preset:
            print("错误：--ticker 需要配合 --preset 使用", file=sys.stderr)
            sys.exit(1)
        ticker = args.ticker
        try:
            view_configs = _load_configs_from_preset(args.preset)
        except (ValueError, ImportError) as e:
            print(f"错误：{e}", file=sys.stderr)
            sys.exit(1)
    else:
        # 不应到达这里（argparse 互斥组保证）
        parser.print_help()
        sys.exit(1)

    print(f"[1/2] 重算 ticker={ticker}, bar_index={args.bar}, views={len(view_configs)}", file=sys.stderr)
    result = replay_bar(ticker, args.bar, view_configs)

    if result is None:
        print("错误：重算失败（数据不足或 bar_index 越界）", file=sys.stderr)
        sys.exit(1)

    print("[2/2] 重算完成", file=sys.stderr)

    if args.json:
        _print_json(result)
    else:
        _print_summary(result)


if __name__ == "__main__":
    main()
