"""
filter_app/config_db.py — 配置管理数据层 (独立 SQLite)
管理预设模板、标的配置快照、变更历史。
存储于 data/config.db，与股票数据 data/market.db 分离。
"""

import json
import sqlite3
from loguru import logger
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, List, Dict, Any

_CONFIG_DB_PATH = Path(__file__).parent.parent / "data" / "config.db"
_CONFIG_DIR = Path(__file__).parent.parent / "config"


@contextmanager
def _get_conn() -> sqlite3.Connection:
    """获取配置数据库连接上下文（独立于 market.db）。
    退出时自动提交/回滚并关闭连接。"""
    conn = sqlite3.connect(str(_CONFIG_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()  # 显式提交，确保关闭前写入
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()  # P1-7: sqlite3 Connection 的 __exit__ 不关闭连接，必须显式 close()

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
    preset_id    INTEGER REFERENCES config_presets(preset_id) ON DELETE SET NULL,
    params_json  TEXT    DEFAULT '',
    updated_at   TEXT    DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (ticker, variant)
);
"""  # P0-2: 外键添加 ON DELETE SET NULL，删除 preset 时自动置空引用而非抛异常

# config_history 单独创建，因为其 FOREIGN KEY 引用 config_ticker，需要表已存在
_HISTORY_SCHEMA = """
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
    """确保配置表存在并迁移旧 schema。在 db.init_db() 之后调用。"""
    logger.info("Config DB initialized at {}", _CONFIG_DB_PATH)
    with _get_conn() as conn:
        conn.executescript(_SCHEMA)
        conn.executescript(_HISTORY_SCHEMA)

        # P0-2: 迁移已有 config_ticker 表 — SQLite 不支持 ALTER COLUMN 改 FK，
        # 需重建表来添加 ON DELETE SET NULL。检查现有 FK 是否缺少 ON DELETE 子句。
        fk_list = conn.execute("PRAGMA foreign_key_list('config_ticker')").fetchall()
        needs_migration = any(
            fk["from"] == "preset_id" and (fk["on_delete"] or "") == ""
            for fk in fk_list
        )
        if needs_migration:
            logger.info("init_config_tables: 检测到旧版 FK（无 ON DELETE），开始迁移 config_ticker 表")
            # 禁用 FK 后重建 config_ticker 表
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS config_ticker_new (
                    ticker       TEXT    NOT NULL,
                    variant      TEXT    NOT NULL DEFAULT 'single',
                    market       TEXT    DEFAULT '',
                    preset_id    INTEGER REFERENCES config_presets(preset_id) ON DELETE SET NULL,
                    params_json  TEXT    DEFAULT '',
                    updated_at   TEXT    DEFAULT (datetime('now','localtime')),
                    PRIMARY KEY (ticker, variant)
                )
            """)
            conn.execute(
                "INSERT INTO config_ticker_new SELECT * FROM config_ticker"
            )
            conn.execute("DROP TABLE config_ticker")
            conn.execute("ALTER TABLE config_ticker_new RENAME TO config_ticker")
            conn.execute("PRAGMA foreign_keys=ON")
            logger.info("init_config_tables: config_ticker 表迁移完成")


# ═══════════════════════════════════════════════════════════
# Presets CRUD
# ═══════════════════════════════════════════════════════════

def list_presets(category: Optional[str] = None) -> List[Dict[str, Any]]:
    logger.debug("Listing presets (category={})", category)
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
    logger.debug("Getting preset by id={}", preset_id)
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM config_presets WHERE preset_id=?", (preset_id,)
        ).fetchone()
    return dict(row) if row else None


def get_preset_by_name(name: str) -> Optional[Dict[str, Any]]:
    logger.debug("Getting preset by name={}", name)
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM config_presets WHERE name=?", (name,)
        ).fetchone()
    return dict(row) if row else None


def save_preset(name: str, params_json: str,
                description: str = "", category: str = "通用") -> int:
    """INSERT or UPSERT preset. Returns preset_id.
    Raises ValueError on invalid input."""
    # P1-5: 入口校验 — 空名称或无效 JSON 直接拒绝
    if not name or not name.strip():
        raise ValueError("预设名称不能为空")
    try:
        json.loads(params_json)
    except (json.JSONDecodeError, TypeError) as e:
        raise ValueError(f"params_json 不是有效的 JSON: {e}")

    with _get_conn() as conn:
        # P0-3: 使用原子 UPSERT 避免 SELECT-then-UPDATE 竞态条件
        row = conn.execute(
            """INSERT INTO config_presets(name, description, category, params_json)
               VALUES(?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                   params_json=excluded.params_json,
                   description=excluded.description,
                   category=excluded.category,
                   updated_at=datetime('now','localtime')
               RETURNING preset_id""",
            (name.strip(), description, category, params_json)
        ).fetchone()
        preset_id = row[0]
        logger.debug("Saved preset id={}, name={}, category={}", preset_id, name, category)
        return preset_id


def delete_preset(preset_id: int) -> bool:
    """删除预设。返回 True 表示成功删除了记录，False 表示记录不存在。
    同时删除对应的 JSON 配置文件（如果存在），防止下次启动时被重新导入。"""
    logger.debug("Deleting preset id={}", preset_id)
    with _get_conn() as conn:
        # 先获取名称，以便同步删除 JSON 文件
        row = conn.execute(
            "SELECT name FROM config_presets WHERE preset_id=?", (preset_id,)
        ).fetchone()
        if row is None:
            return False  # P1-1: 返回 bool 让调用者能区分成功/不存在/失败

        cur = conn.execute("DELETE FROM config_presets WHERE preset_id=?", (preset_id,))
        if cur.rowcount > 0:
            # 同步删除对应的 JSON 配置文件，防止下次启动时被 import_json_files_as_presets 重新导入
            json_path = _CONFIG_DIR / f"{row['name']}.json"
            if json_path.exists():
                json_path.unlink()
                logger.info("delete_preset: 已同步删除 JSON 文件 {}", json_path)
            return True
        return False


def rename_preset(preset_id: int, new_name: str) -> Optional[str]:
    """重命名预设。成功返回新名称，名称冲突返回 None，预设不存在返回 None。
    调用者应区分：冲突返回 None（名称已被占用），不存在也返回 None（追加日志判断）。"""
    # P1-6: 空名称校验
    if not new_name or not new_name.strip():
        logger.warning("rename_preset: 拒绝空名称 (preset_id={})", preset_id)
        return None

    new_name = new_name.strip()
    with _get_conn() as conn:
        # P1-2: 重名前检查名称唯一性，避免 UNIQUE 约束触发异常
        existing = conn.execute(
            "SELECT preset_id FROM config_presets WHERE name=? AND preset_id!=?",
            (new_name, preset_id)
        ).fetchone()
        if existing:
            logger.warning("rename_preset: 名称 '{}' 已被 preset_id={} 占用", new_name, existing[0])
            return None

        cur = conn.execute(
            "UPDATE config_presets SET name=?, updated_at=datetime('now','localtime') WHERE preset_id=?",
            (new_name, preset_id))
        if cur.rowcount == 0:
            logger.warning("rename_preset: preset_id={} 不存在", preset_id)
            return None
        return new_name


def apply_preset(preset_id: int) -> Optional[Dict[str, Any]]:
    """解析 preset 的 params_json 为 dict。
    Returns: 解析后的 dict，或 None（预设不存在 / JSON 损坏）。
    """
    p = get_preset(preset_id)
    if not p:
        logger.warning("apply_preset: preset_id={} 不存在", preset_id)  # P1-3: 日志区分
        return None
    try:
        return json.loads(p["params_json"])
    except (json.JSONDecodeError, TypeError) as e:
        logger.error("apply_preset: preset_id={} ({}) JSON 解析失败: {}",
                     preset_id, p.get("name", "?"), e)  # P1-3: 日志区分
        return None


# ═══════════════════════════════════════════════════════════
# Ticker config
# ═══════════════════════════════════════════════════════════

def load_ticker_config(ticker: str, variant: str = "single") -> Optional[Dict[str, Any]]:
    logger.debug("Loading ticker config: ticker={}, variant={}", ticker, variant)
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM config_ticker WHERE ticker=? AND variant=?",
            (ticker, variant)).fetchone()
    if not row:
        return None
    return dict(row)


def save_ticker_config(ticker: str, market: str, variant: str,
                       params_json: str, preset_id: Optional[int] = None):
    logger.debug("Saving ticker config: ticker={}, variant={}, market={}, preset_id={}",
                 ticker, variant, market, preset_id)
    # P1-4: 校验 preset_id 存在性，避免 FK 引用不存在的预设
    if preset_id is not None:
        if get_preset(preset_id) is None:
            logger.warning("save_ticker_config: preset_id={} 不存在，设为 NULL", preset_id)
            preset_id = None

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
    logger.debug("Recording history: ticker={}, variant={}, source={}", ticker, variant, source)
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO config_history(ticker,variant,preset_id,old_json,new_json,source)
               VALUES(?,?,?,?,?,?)""",
            (ticker, variant, preset_id, old_json, new_json, source))


def get_history(ticker: str, variant: str = "single",
                limit: int = 20) -> List[Dict[str, Any]]:
    logger.debug("Getting history: ticker={}, variant={}, limit={}", ticker, variant, limit)
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
    Returns:
        (imported_count, errors_list) — errors_list 每项为 (filename, error_message)
    """
    if not _CONFIG_DIR.exists():
        return 0, []

    existing_names = {p["name"] for p in list_presets()}
    imported = 0
    errors: List[str] = []  # P0-1: 收集错误而非静默吞掉

    for fpath in sorted(_CONFIG_DIR.glob("*.json")):
        name = fpath.stem  # e.g. "AAPL_US", "2382_HK_DP"
        if name in existing_names and not force:
            continue
        try:
            params = json.loads(fpath.read_text())
        except Exception as e:
            errors.append(f"{fpath.stem}: 文件读取/解析失败 — {e}")
            continue
        try:
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
        except Exception as e:
            errors.append(f"{fpath.stem}: 存入数据库失败 — {e}")
            logger.warning("import_json_files_as_presets: {} 导入失败: {}", fpath, e)

    if errors:
        logger.warning("import_json_files_as_presets: 完成 {} 个，{} 个错误 — {}",
                       imported, len(errors), errors)
    return imported, errors


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

if __name__ == "__main__":  # pragma: no cover
    from db import init_db
    init_db()
    init_config_tables()

    n, errs = import_json_files_as_presets(force=True)
    logger.info("Imported {} JSON files as presets", n)
    if errs:
        logger.warning("Errors during import: {}", errs)

    presets = list_presets()
    logger.info("Presets ({} total):", len(presets))
    for p in presets:
        params = json.loads(p["params_json"])
        logger.info("  [{}] {} — {} ({} keys)", p["category"], p["name"], p["description"], len(params))
