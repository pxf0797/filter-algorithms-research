#!/usr/bin/env python3
"""
回测结果可视化启动器 — 读取 Parquet 文件，嵌入 HTML 后在浏览器中打开。

用法:
    # 直接指定 parquet 文件
    python tools/view_backtest.py test_backtest_output/AAPL_xxx/backtest_result.parquet

    # 自动找最新的
    python tools/view_backtest.py --latest ./test_backtest_output

    # 同时加载 metadata
    python tools/view_backtest.py -m metadata.json result.parquet
"""

import argparse
import json
import os
import sys
import tempfile
import webbrowser
from pathlib import Path

import pandas as pd

# HTML 模板路径（相对于脚本所在目录）
SCRIPT_DIR = Path(__file__).resolve().parent
HTML_TEMPLATE = SCRIPT_DIR.parent / "docs" / "backtesting" / "回测结果可视化.html"


def serialize_value(v):
    """将单个值转为 JSON 兼容的类型。"""
    if v is None or (isinstance(v, float) and (pd.isna(v) or v == float('inf') or v == float('-inf'))):
        return None
    if isinstance(v, (pd.Timestamp,)):
        return v.isoformat()
    if isinstance(v, (bool,)):
        return bool(v)
    if isinstance(v, (int, float, str)):
        return v
    return str(v)


def load_parquet(path: str) -> tuple[list[dict], dict]:
    """读取 parquet 文件，返回 (columns_dict, stats_dict)."""
    df = pd.read_parquet(path)

    # 转换时间戳列为 ISO 字符串
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime('%Y-%m-%dT%H:%M:%S')

    n_rows = len(df)
    column_names = list(df.columns)

    # 构建 column-major 数据：每个 key 是一个列名的数组
    columns = {}
    for col in column_names:
        series = df[col]
        # 处理 NaN/Inf、bool、object
        arr = []
        for v in series:
            arr.append(serialize_value(v))
        columns[col] = arr

    stats = {
        "n_rows": n_rows,
        "n_cols": len(column_names),
        "column_names": column_names,
    }
    return columns, stats


def load_metadata(path: str) -> dict | None:
    """加载 metadata.json，提取 view_labels 等信息。"""
    try:
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
        return meta
    except Exception:
        return None


def find_latest_parquet(base_dir: str) -> str | None:
    """在 base_dir 下找最新的 backtest_result.parquet。"""
    base = Path(base_dir)
    candidates = sorted(base.glob("*/backtest_result.parquet"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return None
    return str(candidates[0])


def embed_and_open(parquet_path: str, metadata_path: str | None, html_template: str):
    """核心流程：读数据 → 序列化 → 嵌入 HTML → 打开浏览器。"""
    print(f"[1/4] 读取 Parquet: {parquet_path}")
    columns, stats = load_parquet(parquet_path)
    print(f"       {stats['n_rows']} 行 x {stats['n_cols']} 列")

    metadata = None
    if metadata_path:
        print(f"[2/4] 读取 Metadata: {metadata_path}")
        metadata = load_metadata(metadata_path)
        if metadata:
            print(f"       view_labels: {metadata.get('view_labels', {})}")
    else:
        # 自动探测同目录下的 metadata.json
        auto_meta = Path(parquet_path).parent / "metadata.json"
        if auto_meta.exists():
            print(f"[2/4] 自动发现 Metadata: {auto_meta}")
            metadata = load_metadata(str(auto_meta))
            if metadata:
                print(f"       view_labels: {metadata.get('view_labels', {})}")
        else:
            print("[2/4] 未找到 metadata.json（可选）")

    # 读取 HTML 模板
    print(f"[3/4] 读取 HTML 模板: {html_template}")
    with open(html_template, encoding="utf-8") as f:
        html = f.read()

    # 构造嵌入数据脚本
    embed_script = "<script>\n"
    embed_script += "window.BACKTEST_DATA = " + json.dumps(columns, ensure_ascii=False) + ";\n"
    embed_script += "window.BACKTEST_STATS = " + json.dumps(stats, ensure_ascii=False) + ";\n"
    if metadata:
        embed_script += "window.BACKTEST_METADATA = " + json.dumps(metadata, ensure_ascii=False) + ";\n"
    else:
        embed_script += "window.BACKTEST_METADATA = null;\n"
    embed_script += "</script>\n"

    # 注入到 </head> 之前
    html = html.replace("</head>", embed_script + "</head>", 1)

    # 写入临时文件
    fd, tmp_path = tempfile.mkstemp(suffix=".html", prefix="backtest_view_")
    os.close(fd)
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(html)

    file_url = "file://" + tmp_path
    print(f"[4/4] 写入临时文件: {tmp_path}")
    print(f"       在浏览器中打开...")
    webbrowser.open(file_url)
    print(f"\n完成！如需再次查看，打开: {tmp_path}")


def main():
    parser = argparse.ArgumentParser(
        description="回测结果可视化 — Python 脚本嵌入数据，浏览器打开",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python tools/view_backtest.py result.parquet
  python tools/view_backtest.py --latest ./test_backtest_output
  python tools/view_backtest.py -m metadata.json result.parquet
        """,
    )
    parser.add_argument("parquet", nargs="?", help="Parquet 文件路径")
    parser.add_argument("--latest", "-l", metavar="DIR", help="自动找 DIR 下最新的 backtest_result.parquet")
    parser.add_argument("--metadata", "-m", metavar="FILE", help="metadata.json 文件路径")
    parser.add_argument("--template", "-t", metavar="FILE", default=str(HTML_TEMPLATE),
                        help="HTML 模板路径（默认: docs/backtesting/回测结果可视化.html）")
    args = parser.parse_args()

    # 确定 parquet 路径
    parquet_path = None
    if args.latest:
        parquet_path = find_latest_parquet(args.latest)
        if not parquet_path:
            print(f"错误：在 {args.latest} 下找不到 backtest_result.parquet", file=sys.stderr)
            sys.exit(1)
    elif args.parquet:
        parquet_path = args.parquet
    else:
        # 默认在当前目录下找
        parquet_path = find_latest_parquet("test_backtest_output")
        if not parquet_path:
            print("错误：请指定 parquet 文件路径或使用 --latest", file=sys.stderr)
            sys.exit(1)

    if not os.path.exists(parquet_path):
        print(f"错误：文件不存在: {parquet_path}", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.template):
        print(f"错误：HTML 模板不存在: {args.template}", file=sys.stderr)
        sys.exit(1)

    embed_and_open(parquet_path, args.metadata, args.template)


if __name__ == "__main__":
    main()
