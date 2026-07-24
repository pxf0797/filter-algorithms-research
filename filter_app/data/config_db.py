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
    """Get a context manager for a config database connection.

    Opens a connection to the standalone SQLite database at ``_CONFIG_DB_PATH``.
    On exit the connection is committed on success or rolled back on exception,
    then closed.

    Returns
    -------
    sqlite3.Connection
        Database connection with WAL journal mode, NORMAL synchronous,
        5-second busy timeout, and foreign keys enabled.
    """
    conn = sqlite3.connect(str(_CONFIG_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()  # 显式提交，确保关闭前写入
    except Exception as e:
        conn.rollback()
        logger.error(f"Config DB connection error: {e}")
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
    """Ensure config tables exist and migrate legacy schema if needed.

    Creates ``config_presets`` and ``config_ticker`` tables, as well as
    the ``config_history`` table.  Performs a one-time migration of the
    ``config_ticker`` table when its foreign key on ``preset_id`` lacks the
    ``ON DELETE SET NULL`` clause.

    Notes
    -----
    Should be called after ``db.init_db()``.
    """
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
                "INSERT INTO config_ticker_new (ticker, variant, market, preset_id, params_json, updated_at) "
                "SELECT ticker, variant, market, preset_id, params_json, updated_at FROM config_ticker"
            )
            conn.execute("DROP TABLE config_ticker")
            conn.execute("ALTER TABLE config_ticker_new RENAME TO config_ticker")
            conn.execute("PRAGMA foreign_keys=ON")
            logger.info("init_config_tables: config_ticker 表迁移完成")


# ═══════════════════════════════════════════════════════════
# Presets CRUD
# ═══════════════════════════════════════════════════════════

def list_presets(category: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all presets, optionally filtered by category.

    Parameters
    ----------
    category : str or None
        If provided, only presets in this category are returned.

    Returns
    -------
    list of dict
        Each dict contains a full ``config_presets`` row.
    """
    logger.debug("Listing presets (category={})", category)
    with _get_conn() as conn:
        if category:
            rows = conn.execute(
                "SELECT preset_id, name, description, category, params_json, "
                "created_at, updated_at FROM config_presets "
                "WHERE category=? ORDER BY name", (category,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT preset_id, name, description, category, params_json, "
                "created_at, updated_at FROM config_presets "
                "ORDER BY category, name"
            ).fetchall()
    return [dict(r) for r in rows]


def get_preset(preset_id: int) -> Optional[Dict[str, Any]]:
    """Get a single preset by its primary key.

    Parameters
    ----------
    preset_id : int
        The preset's primary key.

    Returns
    -------
    dict or None
        The preset row as a dict, or ``None`` if not found.
    """
    logger.debug("Getting preset by id={}", preset_id)
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT preset_id, name, description, category, params_json, "
            "created_at, updated_at FROM config_presets WHERE preset_id=?", (preset_id,)
        ).fetchone()
    return dict(row) if row else None


def get_preset_by_name(name: str) -> Optional[Dict[str, Any]]:
    """Get a single preset by its unique name.

    Parameters
    ----------
    name : str
        The preset name.

    Returns
    -------
    dict or None
        The preset row as a dict, or ``None`` if not found.
    """
    logger.debug("Getting preset by name={}", name)
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT preset_id, name, description, category, params_json, "
            "created_at, updated_at FROM config_presets WHERE name=?", (name,)
        ).fetchone()
    return dict(row) if row else None


def save_preset(name: str, params_json: str,
                description: str = "", category: str = "通用") -> int:
    """Insert or upsert a preset.

    Uses ``INSERT ... ON CONFLICT ... DO UPDATE`` so the operation is atomic.
    Raises ``ValueError`` when the name is empty or ``params_json`` is not
    valid JSON.

    Parameters
    ----------
    name : str
        Preset name (must not be blank).
    params_json : str
        JSON-encoded parameter dictionary.
    description : str, optional
        Human-readable description (default ``""``).
    category : str, optional
        Category label (default ``"通用"``).

    Returns
    -------
    int
        The ``preset_id`` of the inserted or updated row.

    Raises
    ------
    ValueError
        If ``name`` is blank or ``params_json`` is not valid JSON.
    """
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
    """Delete a preset by its primary key.

    Also removes the corresponding JSON config file from ``_CONFIG_DIR`` if it
    exists, preventing re-import on the next startup.

    Parameters
    ----------
    preset_id : int
        The preset's primary key.

    Returns
    -------
    bool
        ``True`` if a row was deleted, ``False`` if no preset with this ID
        existed.
    """
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
    """Rename a preset.

    Checks uniqueness of the new name before updating to avoid a
    ``UNIQUE`` constraint violation.

    Parameters
    ----------
    preset_id : int
        The preset's primary key.
    new_name : str
        Desired new name (must not be blank).

    Returns
    -------
    str or None
        The new name on success, or ``None`` if the preset was not found or
        the new name conflicts with an existing preset.

    Notes
    -----
    Both failure cases (not found / name conflict) return ``None``.
    Callers should inspect the log to distinguish between them.
    """
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
    """Parse a preset's ``params_json`` into a Python dict.

    Parameters
    ----------
    preset_id : int
        The preset's primary key.

    Returns
    -------
    dict or None
        The parsed parameters dict, or ``None`` if the preset does not exist
        or its ``params_json`` is invalid.
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
    """Load a ticker's config from ``config_ticker``.

    Parameters
    ----------
    ticker : str
        Ticker symbol (e.g. ``"AAPL"``, ``"2382.HK"``).
    variant : str, optional
        Variant identifier (default ``"single"``).

    Returns
    -------
    dict or None
        The row as a dict, or ``None`` if no config exists for this ticker
        and variant.
    """
    logger.debug("Loading ticker config: ticker={}, variant={}", ticker, variant)
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT ticker, variant, market, preset_id, params_json, updated_at "
            "FROM config_ticker WHERE ticker=? AND variant=?",
            (ticker, variant)).fetchone()
    if not row:
        return None
    return dict(row)


def save_ticker_config(ticker: str, market: str, variant: str,
                       params_json: str, preset_id: Optional[int] = None):
    """Insert or replace a ticker's config row.

    Validates that ``preset_id`` references an existing preset; if it does
    not, the reference is silently set to ``NULL``.

    Parameters
    ----------
    ticker : str
        Ticker symbol.
    market : str
        Market label (e.g. ``"US"``, ``"HK"``).
    variant : str
        Variant identifier (e.g. ``"single"``, ``"dual"``).
    params_json : str
        JSON-encoded parameter dictionary.
    preset_id : int or None, optional
        Foreign key to ``config_presets`` (default ``None``).
    """
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
    """Insert a history entry recording a config change.

    Parameters
    ----------
    ticker : str
        Ticker symbol.
    variant : str
        Variant identifier.
    old_json : str
        Previous value of ``params_json`` (may be empty).
    new_json : str
        New value of ``params_json`` (may be empty).
    preset_id : int or None, optional
        Associated preset ID (default ``None``).
    source : str, optional
        Origin of the change, e.g. ``"ui"`` or ``"import"`` (default ``"ui"``).
    """
    logger.debug("Recording history: ticker={}, variant={}, source={}", ticker, variant, source)
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO config_history(ticker,variant,preset_id,old_json,new_json,source)
               VALUES(?,?,?,?,?,?)""",
            (ticker, variant, preset_id, old_json, new_json, source))


def get_history(ticker: str, variant: str = "single",
                limit: int = 20) -> List[Dict[str, Any]]:
    """Retrieve the change history for a ticker and variant.

    Joins with ``config_presets`` to include the preset name for each
    history entry.

    Parameters
    ----------
    ticker : str
        Ticker symbol.
    variant : str, optional
        Variant identifier (default ``"single"``).
    limit : int, optional
        Maximum number of entries to return (default 20).

    Returns
    -------
    list of dict
        Each dict contains a ``config_history`` row, enriched with
        ``preset_name`` from the joined preset.
    """
    logger.debug("Getting history: ticker={}, variant={}, limit={}", ticker, variant, limit)
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT h.id, h.ticker, h.variant, h.preset_id,
                      h.old_json, h.new_json, h.changed_at, h.source,
                      p.name as preset_name
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
    """Import ``config/*.json`` files into the presets table.

    Reads every ``*.json`` file in ``_CONFIG_DIR``, infers a category from
    the file-name suffix (``_DP`` → 双滤波, ``_QS`` → 快速, else 单滤波),
    and inserts each into ``config_presets``.

    Parameters
    ----------
    force : bool, optional
        If ``True``, overwrite existing presets with the same name
        (default ``False``).

    Returns
    -------
    imported : int
        Number of files successfully imported.
    errors : list of str
        List of error messages; each item has the form
        ``"filename: error description"``.
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

# ═══════════════════════════════════════════════════════════
# 单一参数清单 — 每视图 v0~v3 可持久化参数的唯一真源
# 新增/删除可持久化参数时只改这一处；JSON 导出与 DB 收集都从此驱动，
# 避免两处手动键表漂移(历史 bug: show_pnl_feedback 曾两处都漏保存)。
# (session_state 后缀, cfg 键, 导出默认值)
# ═══════════════════════════════════════════════════════════
VIEW_PARAM_SPECS = [
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
VIEW_PARAM_SUFFIXES = [suffix for suffix, _, _ in VIEW_PARAM_SPECS]


def collect_current_params() -> Dict[str, Any]:
    """Collect all current configuration parameters from ``st.session_state``.

    Reads global parameters (market, ticker, global_f, global_dual,
    global_f2), view parameters (v0–v3 with their sub-keys), and
    filter parameters (identified by Chinese-named keys).

    Returns
    -------
    dict
        Flat dictionary of all collected parameter values.
    """
    import streamlit as st

    params = {}
    # 全局参数
    for k in ["market", "ticker", "global_f", "global_dual", "global_f2"]:
        if k in st.session_state:
            params[k] = st.session_state[k]

    # 视图参数 v0~v3（键表来自单一真源 VIEW_PARAM_SUFFIXES）
    for vi in range(4):
        for pk in VIEW_PARAM_SUFFIXES:
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
    from data.db import init_db
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
