import json
from pathlib import Path

class BacktestCatalog:
    """扫描回测输出目录, 维护session索引"""

    def __init__(self, base_dir="backtest_output"):
        self.base_dir = Path(base_dir)
        self.index_file = self.base_dir / ".catalog.json"

    def scan(self):
        """扫描目录, 提取每个session的元信息"""
        sessions = []
        for session_dir in sorted(self.base_dir.iterdir()):
            if session_dir.is_dir() and not session_dir.name.startswith("."):
                info = self._extract_session_info(session_dir)
                if info:
                    sessions.append(info)
        return sessions

    def _extract_session_info(self, session_dir):
        """提取单个session的元信息: ticker, 时间, 参数, 关键指标"""
        info = {"path": str(session_dir), "name": session_dir.name}
        for f in session_dir.iterdir():
            if f.suffix == ".json":
                try:
                    with open(f) as fh:
                        data = json.load(fh)
                    info["ticker"] = data.get("ticker")
                    info["session_id"] = data.get("session_id")
                    info["start_time"] = data.get("start_time")
                    info["end_time"] = data.get("end_time")
                    info["status"] = data.get("status")
                    info["parquet_row_count"] = data.get("parquet_row_count")
                    info["step_count"] = data.get("step_count")
                    info["views"] = data.get("views", [])
                except Exception:
                    pass
        return info

    def save_index(self, sessions=None):
        """保存索引到 .catalog.json"""
        if sessions is None:
            sessions = self.scan()
        with open(self.index_file, "w") as f:
            json.dump(sessions, f, indent=2, default=str)

    def query(self, ticker=None, date_from=None, min_sharpe=None):
        """查询历史回测结果"""
        if self.index_file.exists():
            with open(self.index_file) as f:
                sessions = json.load(f)
        else:
            sessions = self.scan()

        results = sessions
        if ticker:
            results = [s for s in results if ticker in s.get("ticker", "")]
        return results
