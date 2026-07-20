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
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import pandas as pd

# HTML 模板路径（相对于脚本所在目录）
SCRIPT_DIR = Path(__file__).resolve().parent
HTML_TEMPLATE = SCRIPT_DIR.parent / "docs" / "backtesting" / "回测结果可视化.html"

# 多 Ticker 颜色调色板（深色背景可读）
TICKER_COLORS = [
    "#58a6ff", "#3fb950", "#f85149", "#d2991d", "#a371f7",
    "#39d2c0", "#f78166", "#db61a2", "#8b949e", "#79c0ff",
    "#56d364", "#e5534b",
]


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
    return df_to_columns(df)


def df_to_columns(df: "pd.DataFrame") -> tuple[dict, dict]:
    """将 DataFrame 转为 column-major JSON 格式的 (columns, stats)。"""
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


def find_all_parquet_dirs(base_dir: str) -> list[str]:
    """在 base_dir 下找所有包含 backtest_result.parquet 的目录。"""
    base = Path(base_dir)
    dirs = set()
    for p in base.glob("*/backtest_result.parquet"):
        dirs.add(str(p.parent))
    return sorted(dirs)


def extract_ticker_name(path: str) -> str:
    """从目录/文件路径提取 ticker 名称。如 'AAPL_20240101/backtest_result.parquet' -> 'AAPL'."""
    p = Path(path)
    # 如果是 .parquet 文件，使用父目录名；否则使用路径最后一个组件名
    if p.suffix == ".parquet":
        name = p.parent.name
    else:
        # 去掉末尾斜杠后取最后一个组件
        name = p.name or p.parent.name
    return name.split("_")[0].upper()


def load_multi_parquet(paths: list[str]) -> dict:
    """加载多个 parquet 路径，返回多 ticker 数据结构。

    Returns:
        {
            "tickers": [{"name": "AAPL", "color": "#58a6ff"}, ...],
            "data": {"AAPL": {"columns": ..., "stats": ...}, ...},
            "is_multiticker": True,
        }
    """
    result: dict = {"tickers": [], "data": {}, "is_multiticker": len(paths) > 1}

    for i, path in enumerate(paths):
        p = Path(path)
        if p.is_dir():
            candidates = list(p.glob("backtest_result.parquet")) or list(p.glob("*.parquet"))
            if not candidates:
                print(f"警告：在 {path} 中找不到 parquet 文件，跳过", file=sys.stderr)
                continue
            parquet_path = str(candidates[0])
        elif p.exists() and p.suffix == ".parquet":
            parquet_path = path
        else:
            print(f"警告：路径不存在或不是 parquet 文件: {path}，跳过", file=sys.stderr)
            continue

        ticker_name = extract_ticker_name(path)
        color = TICKER_COLORS[i % len(TICKER_COLORS)]

        print(f"[{i + 1}/{len(paths)}] 加载 {ticker_name}: {parquet_path}")
        columns, stats = load_parquet(parquet_path)
        print(f"       {stats['n_rows']} 行 x {stats['n_cols']} 列")

        result["tickers"].append({"name": ticker_name, "color": color})
        result["data"][ticker_name] = {"columns": columns, "stats": stats}

    return result


# ========== HTTP 服务器模式 ==========

class BacktestHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器：GET 返回 HTML 页面，POST /api/parse 解析上传的 parquet 文件。"""
    html_template = None  # 由 start_server() 设置
    embedded_html = None  # 预处理的 HTML（含嵌入数据）

    def do_GET(self):
        if self.path == "/api/health":
            self._send_json({"status": "ok"})
        elif self.path == "/" or self.path == "/index.html":
            self._serve_html()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/parse":
            self._handle_parse()
        else:
            self.send_error(404)

    def _serve_html(self):
        try:
            if self.embedded_html is not None:
                html = self.embedded_html
            else:
                with open(self.html_template, encoding="utf-8") as f:
                    html = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
        except Exception as e:
            self.send_error(500, str(e))

    def _handle_parse(self):
        file_data = self._parse_multipart()
        if file_data is None:
            self._send_json({"error": "未找到上传的文件，请拖入 .parquet 文件"}, 400)
            return

        # 写入临时文件后读取
        fd, tmp_path = tempfile.mkstemp(suffix=".parquet")
        os.close(fd)
        try:
            with open(tmp_path, "wb") as f:
                f.write(file_data)
            columns, stats = load_parquet(tmp_path)
            self._send_json({"columns": columns, "stats": stats, "metadata": None})
        except Exception as e:
            self._send_json({"error": f"解析 parquet 失败: {str(e)}"}, 400)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _parse_multipart(self) -> bytes | None:
        """从 multipart/form-data 请求中提取上传的文件内容。"""
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            return None

        # 提取 boundary
        boundary = None
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part.split("=", 1)[1].strip('"').strip("'")
        if not boundary:
            return None

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        boundary_bytes = ("--" + boundary).encode()
        parts = body.split(boundary_bytes)

        for part in parts:
            if not part or part in (b"--\r\n", b"--", b"\r\n"):
                continue
            idx = part.find(b"\r\n\r\n")
            if idx == -1:
                continue
            headers_raw = part[:idx].decode("utf-8", errors="replace")
            data = part[idx + 4:]
            if data.endswith(b"\r\n"):
                data = data[:-2]
            if "filename=" in headers_raw:
                return data
        return None

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def log_message(self, format, *args):
        if args:
            print(f"[server] {args[0]}")


def start_server(port: int, html_template: str, metadata: dict | None = None):
    """启动本地 HTTP 服务器，支持拖入 Parquet 文件可视化。

    Parameters
    ----------
    port : int
        服务器端口。
    html_template : str
        HTML 模板路径。
    metadata : dict or None
        可选的 metadata，会嵌入到 HTML 中供前端读取 view_labels 等。
    """
    BacktestHandler.html_template = html_template

    # Pre-embed metadata into HTML if available
    if metadata is not None:
        try:
            with open(html_template, encoding="utf-8") as f:
                html = f.read()
            embed_script = "<script>\n"
            embed_script += "window.BACKTEST_METADATA = " + json.dumps(metadata, ensure_ascii=False) + ";\n"
            embed_script += "</script>\n"
            html = html.replace("</head>", embed_script + "</head>", 1)
            BacktestHandler.embedded_html = html
        except Exception as e:
            print(f"警告：嵌入 metadata 失败: {e}", file=sys.stderr)
            BacktestHandler.embedded_html = None
    else:
        BacktestHandler.embedded_html = None

    try:
        server = HTTPServer(("127.0.0.1", port), BacktestHandler)
    except OSError as e:
        print(f"错误：无法启动服务器 — {e}", file=sys.stderr)
        sys.exit(1)

    url = f"http://localhost:{port}"

    print(f"\n{'='*60}")
    print(f"  回测可视化服务器已启动")
    print(f"  地址: {url}")
    print(f"  拖入 .parquet 文件即可查看可视化")
    print(f"  按 Ctrl+C 停止服务器")
    print(f"{'='*60}\n")

    webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")
        server.server_close()


def embed_and_open(parquet_paths: list[str], metadata_path: str | None, html_template: str):
    """核心流程：读数据 → 序列化 → 嵌入 HTML → 打开浏览器。支持多 Ticker。"""
    is_multi = len(parquet_paths) > 1
    multi_data = None

    if is_multi:
        print(f"[1/4] 读取多 Ticker 数据: {len(parquet_paths)} 个路径")
        multi_data = load_multi_parquet(parquet_paths)
        # 取第一个 ticker 的数据用于单 ticker 图表（向后兼容）
        first_ticker = multi_data["tickers"][0]["name"]
        columns = multi_data["data"][first_ticker]["columns"]
        stats = multi_data["data"][first_ticker]["stats"]
    else:
        print(f"[1/4] 读取 Parquet: {parquet_paths[0]}")
        columns, stats = load_parquet(parquet_paths[0])
        print(f"       {stats['n_rows']} 行 x {stats['n_cols']} 列")

    metadata = None
    first_path = parquet_paths[0]
    if metadata_path:
        print(f"[2/4] 读取 Metadata: {metadata_path}")
        metadata = load_metadata(metadata_path)
        if metadata:
            print(f"       view_labels: {metadata.get('view_labels', {})}")
    else:
        # 自动探测
        auto_meta_dir = Path(first_path).parent if not Path(first_path).is_dir() else Path(first_path)
        auto_meta = auto_meta_dir / "metadata.json"
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

    if multi_data:
        embed_script += "window.BACKTEST_IS_MULTI = true;\n"
        embed_script += "window.BACKTEST_TICKERS = " + json.dumps(multi_data["tickers"], ensure_ascii=False) + ";\n"
        embed_script += "window.BACKTEST_ALL_DATA = " + json.dumps(multi_data["data"], ensure_ascii=False) + ";\n"
    else:
        embed_script += "window.BACKTEST_IS_MULTI = false;\n"
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
        description="回测结果可视化 — Python 脚本嵌入数据，浏览器打开。（支持多 Ticker 对比）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 单 ticker
  python tools/view_backtest.py result.parquet
  python tools/view_backtest.py --latest ./test_backtest_output

  # 多 ticker 对比（传入多个目录或 parquet 文件）
  python tools/view_backtest.py AAPL_dir/ MSFT_dir/ GOOGL_dir/
  python tools/view_backtest.py --all ./test_backtest_output

  # 带 metadata
  python tools/view_backtest.py -m metadata.json result.parquet
        """,
    )
    parser.add_argument("parquet", nargs="*", help="Parquet 文件或目录路径（支持多个用于多 ticker 对比）")
    parser.add_argument("--latest", "-l", metavar="DIR", help="自动找 DIR 下最新的 backtest_result.parquet")
    parser.add_argument("--all", "-a", metavar="DIR", help="自动找 DIR 下所有 ticker 目录（多 ticker 对比模式）")
    parser.add_argument("--metadata", "-m", metavar="FILE", help="metadata.json 文件路径")
    parser.add_argument("--template", "-t", metavar="FILE", default=str(HTML_TEMPLATE),
                        help="HTML 模板路径（默认: docs/backtesting/回测结果可视化.html）")
    parser.add_argument("--serve", "-s", action="store_true", help="启动本地 HTTP 服务器模式")
    parser.add_argument("--port", "-p", type=int, default=8899, help="服务器端口（默认 8899，仅 --serve 模式）")
    args = parser.parse_args()

    # --serve 模式
    if args.serve:
        # Try to auto-detect metadata from parquet paths if provided
        serve_metadata = None
        parquet_for_meta = args.parquet or []
        if args.all:
            parquet_for_meta = find_all_parquet_dirs(args.all)
        elif args.latest:
            p = find_latest_parquet(args.latest)
            if p:
                parquet_for_meta = [p]

        if parquet_for_meta:
            first_path = parquet_for_meta[0]
            auto_meta_dir = Path(first_path).parent if not Path(first_path).is_dir() else Path(first_path)
            auto_meta = auto_meta_dir / "metadata.json"
            if auto_meta.exists():
                meta = load_metadata(str(auto_meta))
                if meta:
                    serve_metadata = meta
                    print(f"加载 Metadata: {auto_meta}")
                    print(f"    view_labels: {meta.get('view_labels', {})}")

        if metadata_path:
            serve_metadata = load_metadata(metadata_path)
            if serve_metadata:
                print(f"加载 Metadata: {metadata_path}")

        start_server(args.port, args.template, serve_metadata)
        return

    # 确定 parquet 路径列表
    parquet_paths: list[str] = []

    if args.all:
        parquet_paths = find_all_parquet_dirs(args.all)
        if not parquet_paths:
            print(f"错误：在 {args.all} 下找不到包含 backtest_result.parquet 的目录", file=sys.stderr)
            sys.exit(1)
        print(f"找到 {len(parquet_paths)} 个 ticker 目录")
    elif args.latest:
        path = find_latest_parquet(args.latest)
        if not path:
            print(f"错误：在 {args.latest} 下找不到 backtest_result.parquet", file=sys.stderr)
            sys.exit(1)
        parquet_paths = [path]
    elif args.parquet:
        parquet_paths = list(args.parquet)
    else:
        # 默认在当前目录下找
        path = find_latest_parquet("test_backtest_output")
        if not path:
            print("错误：请指定 parquet 文件路径、使用 --latest 或 --all", file=sys.stderr)
            sys.exit(1)
        parquet_paths = [path]

    for p in parquet_paths:
        if not os.path.exists(p):
            print(f"错误：路径不存在: {p}", file=sys.stderr)
            sys.exit(1)

    if not os.path.exists(args.template):
        print(f"错误：HTML 模板不存在: {args.template}", file=sys.stderr)
        sys.exit(1)

    embed_and_open(parquet_paths, args.metadata, args.template)


if __name__ == "__main__":
    main()
