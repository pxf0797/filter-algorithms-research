"""
streamlit/config_db.py — 配置管理数据层 (独立 SQLite)
管理预设模板、标的配置快照、变更历史。
存储于 data/config.db，与股票数据 data/market.db 分离。
"""

import json
import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any

_CONFIG_DB_PATH = Path(__file__).parent.parent / "data" / "config.db"
_CONFIG_DIR = Path(__file__).parent.parent / "config"


def _get_conn() -> sqlite3.Connection:
    """获取配置数据库连接（独立于 market.db）。"""
    conn = sqlite3.connect(str(_CONFIG_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn

# ═══════════════════════════════════════════════════════════
# Schema init
# ═══════════════════════════════════════════════════════════

_SCHEMA = """
CREATE TABLE IF NOT EXISTS config_presets (
    preset_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL UNIQUE,
    description  TEXT    DEFAULT '',
    category     TEXT    DEFAULT '通用',
    params_json  TEXT    NOT NULL,
    created_at   TEXT    DEFAULT (datetime('now','localtime')),
    updated_at   TEXT    DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS config_ticker (
    ticker       TEXT    NOT NULL,
    variant      TEXT    NOT NULL DEFAULT 'single',
    market       TEXT    DEFAULT '',
    preset_id    INTEGER REFERENCES config_presets(preset_id),
    params_json  TEXT    DEFAULT '',
    updated_at   TEXT    DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (ticker, variant)
);

CREATE TABLE IF NOT EXISTS config_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT    NOT NULL,
    variant      TEXT    NOT NULL DEFAULT 'single',
    preset_id    INTEGER,
    old_json     TEXT    DEFAULT '',
    new_json     TEXT    DEFAULT '',
    changed_at   TEXT    DEFAULT (datetime('now','localtime')),
    source       TEXT    DEFAULT 'ui',
    FOREIGN KEY (ticker, variant) REFERENCES config_ticker(ticker, variant)
);
CREATE INDEX IF NOT EXISTS idx_history_lookup
    ON config_history(ticker, variant, changed_at DESC);
"""


def init_config_tables():
    """确保配置表存在。在 db.init_db() 之后调用。"""
    with _get_conn() as conn:
        conn.executescript(_SCHEMA)
        conn.execute("PRAGMA foreign_keys=ON")


# ═══════════════════════════════════════════════════════════
# Presets CRUD
# ═══════════════════════════════════════════════════════════

def list_presets(category: Optional[str] = None) -> List[Dict[str, Any]]:
    with _get_conn() as conn:
        if category:
            rows = conn.execute(
                "SELECT * FROM config_presets WHERE category=? ORDER BY name", (category,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM config_presets ORDER BY category, name"
            ).fetchall()
    return [dict(r) for r in rows]


def get_preset(preset_id: int) -> Optional[Dict[str, Any]]:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM config_presets WHERE preset_id=?", (preset_id,)
        ).fetchone()
    return dict(row) if row else None


def get_preset_by_name(name: str) -> Optional[Dict[str, Any]]:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM config_presets WHERE name=?", (name,)
        ).fetchone()
    return dict(row) if row else None


def save_preset(name: str, params_json: str,
                description: str = "", category: str = "通用") -> int:
    """INSERT or UPDATE preset. Returns preset_id."""
    with _get_conn() as conn:
        cur = conn.execute("SELECT preset_id FROM config_presets WHERE name=?", (name,))
        existing = cur.fetchone()
        if existing:
            conn.execute(
                """UPDATE config_presets
                   SET params_json=?, description=?, category=?,
                       updated_at=datetime('now','localtime')
                   WHERE preset_id=?""",
                (params_json, description, category, existing[0]))
            return existing[0]
        else:
            conn.execute(
                "INSERT INTO config_presets(name,description,category,params_json) VALUES(?,?,?,?)",
                (name, description, category, params_json))
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def delete_preset(preset_id: int):
    with _get_conn() as conn:
        conn.execute("DELETE FROM config_presets WHERE preset_id=?", (preset_id,))


def rename_preset(preset_id: int, new_name: str):
    with _get_conn() as conn:
        conn.execute(
            "UPDATE config_presets SET name=?, updated_at=datetime('now','localtime') WHERE preset_id=?",
            (new_name, preset_id))


def apply_preset(preset_id: int) -> Optional[Dict[str, Any]]:
    """解析 preset 的 params_json 为 dict。"""
    p = get_preset(preset_id)
    if not p:
        return None
    try:
        return json.loads(p["params_json"])
    except (json.JSONDecodeError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════
# Ticker config
# ═══════════════════════════════════════════════════════════

def load_ticker_config(ticker: str, variant: str = "single") -> Optional[Dict[str, Any]]:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM config_ticker WHERE ticker=? AND variant=?",
            (ticker, variant)).fetchone()
    if not row:
        return None
    return dict(row)


def save_ticker_config(ticker: str, market: str, variant: str,
                       params_json: str, preset_id: Optional[int] = None):
    with _get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO config_ticker
               (ticker, variant, market, preset_id, params_json, updated_at)
               VALUES(?,?,?,?,?,datetime('now','localtime'))""",
            (ticker, variant, market, preset_id, params_json))


# ═══════════════════════════════════════════════════════════
# History
# ═══════════════════════════════════════════════════════════

def record_history(ticker: str, variant: str,
                   old_json: str, new_json: str,
                   preset_id: Optional[int] = None,
                   source: str = "ui"):
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO config_history(ticker,variant,preset_id,old_json,new_json,source)
               VALUES(?,?,?,?,?,?)""",
            (ticker, variant, preset_id, old_json, new_json, source))


def get_history(ticker: str, variant: str = "single",
                limit: int = 20) -> List[Dict[str, Any]]:
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT h.*, p.name as preset_name
               FROM config_history h
               LEFT JOIN config_presets p ON h.preset_id = p.preset_id
               WHERE h.ticker=? AND h.variant=?
               ORDER BY h.changed_at DESC LIMIT ?""",
            (ticker, variant, limit)).fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════
# Migration: JSON files → presets
# ═══════════════════════════════════════════════════════════

def import_json_files_as_presets(force: bool = False):
    """首次运行时将 config/*.json 导入为预设。
    Args:
        force: True 时覆盖同名预设
    """
    if not _CONFIG_DIR.exists():
        return 0

    existing_names = {p["name"] for p in list_presets()}
    imported = 0

    for fpath in sorted(_CONFIG_DIR.glob("*.json")):
        name = fpath.stem  # e.g. "AAPL_US", "2382_HK_DP"
        if name in existing_names and not force:
            continue
        try:
            params = json.loads(fpath.read_text())
            # 推断分类
            if name.endswith("_DP"):
                category = "双滤波"
            elif name.endswith("_QS"):
                category = "快速"
            else:
                category = "单滤波"
            # 提取描述
            ticker = params.get("ticker", "?")
            market = params.get("market", "?")
            desc = f"{market}·{ticker}" if ticker != "?" else fpath.stem
            save_preset(name, json.dumps(params, ensure_ascii=False),
                        description=desc, category=category)
            imported += 1
        except Exception:
            pass

    return imported


# ═══════════════════════════════════════════════════════════
# Streamlit helpers
# ═══════════════════════════════════════════════════════════

def collect_current_params() -> Dict[str, Any]:
    """从 st.session_state 收集当前所有配置参数。"""
    import streamlit as st

    params = {}
    # 全局参数
    for k in ["market", "ticker", "global_f", "global_dual", "global_f2"]:
        if k in st.session_state:
            params[k] = st.session_state[k]

    # 视图参数 v0~v3
    for vi in range(4):
        for pk in ["tf", "n", "sch", "pred", "ke", "sm", "ew",
                    "fm", "next", "fc", "fc2", "strat", "sl",
                    "cross_pnl", "align"]:
            k = f"v{vi}_{pk}"
            if k in st.session_state:
                params[k] = st.session_state[k]

    # 滤波器参数（中文 key）
    for sk in st.session_state:
        if any(sk.startswith(p) for p in ["窗口大小_", "跨度_", "偏移量_",
                                            "标准差_", "多项式阶数_",
                                            "过程噪声", "测量噪声",
                                            "滤波器阶数_", "截止频率_",
                                            "平滑比例_"]):
            params[sk] = st.session_state[sk]

    return params


# ═══════════════════════════════════════════════════════════
# Self-test
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    from db import init_db
    init_db()
    init_config_tables()

    n = import_json_files_as_presets(force=True)
    print(f"Imported {n} JSON files as presets")

    presets = list_presets()
    print(f"\nPresets ({len(presets)} total):")
    for p in presets:
        params = json.loads(p["params_json"])
        print(f"  [{p['category']}] {p['name']} — {p['description']} ({len(params)} keys)")
