"""
回测事件日志模块 — JSONL 格式，轻量、追加写入、人类可读
"""

import json
from pathlib import Path
from datetime import datetime

LOG_DIR = Path(__file__).parent.parent / "data" / "backtest_logs"


def _ensure_dir():
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _log_event(event_type: str, data: dict):
    """写入一条 JSONL 日志记录。"""
    _ensure_dir()
    today = datetime.now().strftime("%Y%m%d")
    log_path = LOG_DIR / f"backtest_{today}.jsonl"
    record = {
        "ts": datetime.now().isoformat(),
        "event": event_type,
        **data,
    }
    with open(log_path, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---- Public API ----

def log_mode_switch(ticker: str, direction: str, min_tf: str, bar_count: int):
    """记录模式切换事件。
    direction: "enter" | "exit"
    """
    _log_event("mode_switch", {
        "ticker": ticker,
        "direction": direction,
        "min_tf": min_tf,
        "bar_count": bar_count,
    })


def log_bar_navigation(ticker: str, min_tf: str, bar_index: int, total: int,
                       cutoff_date: str, elapsed_ms: float = 0):
    """记录 bar 位置跳转事件。"""
    _log_event("bar_navigation", {
        "ticker": ticker,
        "min_tf": min_tf,
        "bar_index": bar_index,
        "total": total,
        "cutoff_date": cutoff_date,
        "elapsed_ms": round(elapsed_ms, 1),
    })


def log_data_load(ticker: str, tf: str, bar_count: int, cutoff_date: str,
                  elapsed_ms: float = 0):
    """记录回测数据加载事件。"""
    _log_event("data_load", {
        "ticker": ticker,
        "tf": tf,
        "bar_count": bar_count,
        "cutoff_date": cutoff_date,
        "elapsed_ms": round(elapsed_ms, 1),
    })


def log_error(ticker: str, location: str, error_msg: str):
    """记录回测异常事件。"""
    _log_event("error", {
        "ticker": ticker,
        "location": location,
        "error": error_msg,
    })


# ---- Half-pair strategy log ----

HALF_PAIR_LOG_PATH = Path(__file__).parent.parent / "data" / "backtest" / "half_pair_log.jsonl"


def log_half_pair_frame(ticker: str, tf: str, frame_data: dict) -> None:
    """记录一帧半边多空对策略状态(JSONL追加写入)。

    frame_data 建议字段:
        bar_index, B_half_start, B_half_direction, B_half_locked,
        C_state, decision(entry/exit/hold), exit_reason, timestamp
    """
    HALF_PAIR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now().isoformat(),
        "ticker": ticker,
        "tf": tf,
        **frame_data,
    }
    with open(str(HALF_PAIR_LOG_PATH), "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_half_pair_log(ticker: str, tf: str) -> list:
    """解析 JSONL 返回 list of dict（回测后分析用）。

    仅返回 ticker 和 tf 匹配的记录。
    """
    if not HALF_PAIR_LOG_PATH.exists():
        return []
    records = []
    with open(str(HALF_PAIR_LOG_PATH), "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("ticker") == ticker and record.get("tf") == tf:
                records.append(record)
    return records
