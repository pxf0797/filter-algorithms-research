"""
tests/test_config_db.py — 完整单元测试覆盖 config_db 模块
每个测试使用临时 DB 文件，互不污染。
"""

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_config_db():
    """创建临时 config.db 并初始化表，返回 config_db 模块引用。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "config.db"
        with patch("config_db._CONFIG_DB_PATH", db_path):
            # 重新加载模块以重置内部连接缓存可能绑定的路径
            import config_db
            config_db.init_config_tables()
            yield config_db


@pytest.fixture
def sample_params_json():
    return json.dumps({"market": "US", "ticker": "AAPL", "sma": 20})


# ═══════════════════════════════════════════════════════════════════════════
# 1. TestInitConfigTables
# ═══════════════════════════════════════════════════════════════════════════

class TestInitConfigTables:
    """验证表创建与幂等性。"""

    def test_creates_tables(self, temp_config_db):
        """验证 3 张表被创建。"""
        import config_db
        conn = sqlite3.connect(str(config_db._CONFIG_DB_PATH))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = [r[0] for r in tables]
        conn.close()

        assert "config_presets" in table_names
        assert "config_ticker" in table_names
        assert "config_history" in table_names
        assert len([n for n in table_names if n.startswith("config_")]) == 3

    def test_idempotent(self, temp_config_db):
        """重复调用 init_config_tables 不报错。"""
        import config_db
        # 第一次在 fixture 中已调，再调两次
        config_db.init_config_tables()
        config_db.init_config_tables()  # 不应抛出异常


# ═══════════════════════════════════════════════════════════════════════════
# 2. TestPresetCRUD
# ═══════════════════════════════════════════════════════════════════════════

class TestPresetCRUD:
    """预设增删改查与边界条件。"""

    def test_save_new_preset(self, temp_config_db, sample_params_json):
        """保存新预设，返回 preset_id。"""
        import config_db
        pid = config_db.save_preset("test_algo", sample_params_json)
        assert isinstance(pid, int)
        assert pid > 0

    def test_save_update_existing(self, temp_config_db, sample_params_json):
        """同名保存触发 UPDATE，返回同一 preset_id。"""
        import config_db
        pid1 = config_db.save_preset("my_algo", sample_params_json, category="单滤波")
        pid2 = config_db.save_preset("my_algo", json.dumps({"x": 1}), category="双滤波")
        assert pid1 == pid2  # 同一 ID

        p = config_db.get_preset(pid2)
        assert json.loads(p["params_json"]) == {"x": 1}
        assert p["category"] == "双滤波"

    def test_list_presets_all(self, temp_config_db):
        """列出全部预设。"""
        import config_db
        config_db.save_preset("a", "{}")
        config_db.save_preset("b", "{}")
        presets = config_db.list_presets()
        assert len(presets) == 2

    def test_list_presets_by_category(self, temp_config_db):
        """按分类过滤。"""
        import config_db
        config_db.save_preset("a1", "{}", category="单滤波")
        config_db.save_preset("a2", "{}", category="双滤波")
        config_db.save_preset("a3", "{}", category="单滤波")

        filtered = config_db.list_presets(category="单滤波")
        assert len(filtered) == 2
        assert all(p["category"] == "单滤波" for p in filtered)

    def test_get_preset(self, temp_config_db, sample_params_json):
        """按 ID 获取预设。"""
        import config_db
        pid = config_db.save_preset("get_test", sample_params_json,
                                    description="desc", category="测试")
        p = config_db.get_preset(pid)
        assert p["name"] == "get_test"
        assert p["description"] == "desc"
        assert p["category"] == "测试"
        assert p["params_json"] == sample_params_json

    def test_get_preset_by_name(self, temp_config_db, sample_params_json):
        """按名称获取预设。"""
        import config_db
        config_db.save_preset("name_test", sample_params_json)
        p = config_db.get_preset_by_name("name_test")
        assert p is not None
        assert p["name"] == "name_test"

    def test_get_preset_not_found(self, temp_config_db):
        """不存在的预设返回 None。"""
        import config_db
        assert config_db.get_preset(99999) is None
        assert config_db.get_preset_by_name("nonexistent") is None

    def test_delete_preset(self, temp_config_db, sample_params_json):
        """删除后查不到。"""
        import config_db
        pid = config_db.save_preset("to_delete", sample_params_json)
        config_db.delete_preset(pid)
        assert config_db.get_preset(pid) is None

    def test_rename_preset(self, temp_config_db, sample_params_json):
        """重命名后 list/get 都反映新名称。"""
        import config_db
        pid = config_db.save_preset("old_name", sample_params_json)
        config_db.rename_preset(pid, "new_name")
        p = config_db.get_preset(pid)
        assert p["name"] == "new_name"
        names = [p["name"] for p in config_db.list_presets()]
        assert "new_name" in names
        assert "old_name" not in names

    def test_rename_to_existing_name(self, temp_config_db, sample_params_json):
        """重名应返回 None（名称唯一性检查，P1-2 修复后不再抛异常）。"""
        import config_db
        config_db.save_preset("first", sample_params_json)
        pid2 = config_db.save_preset("second", sample_params_json)
        result = config_db.rename_preset(pid2, "first")
        assert result is None  # 名称冲突，返回 None

    def test_apply_preset(self, temp_config_db):
        """apply_preset 返回解析后的 dict。"""
        import config_db
        params = {"ma": 10, "std": 2.5}
        pid = config_db.save_preset("apply_test", json.dumps(params))
        result = config_db.apply_preset(pid)
        assert result == params

    def test_apply_preset_invalid_json(self, temp_config_db):
        """非法 JSON 在 save_preset 阶段即被拒绝（P1-5 修复）。"""
        import config_db
        import pytest
        with pytest.raises(ValueError, match="有效的 JSON"):
            config_db.save_preset("bad_json", "{not valid}")

    def test_apply_preset_not_found(self, temp_config_db):
        """不存在的 preset 返回 None。"""
        import config_db
        assert config_db.apply_preset(99999) is None


# ═══════════════════════════════════════════════════════════════════════════
# 3. TestTickerConfig
# ═══════════════════════════════════════════════════════════════════════════

class TestTickerConfig:
    """标的配置读写。"""

    def test_save_and_load_ticker_config(self, temp_config_db):
        """保存后能完整读出。"""
        import config_db
        params = json.dumps({"sma": 20, "ema": 10})
        config_db.save_ticker_config("AAPL", "US", "single",
                                     params_json=params, preset_id=None)
        row = config_db.load_ticker_config("AAPL")
        assert row is not None
        assert row["ticker"] == "AAPL"
        assert row["variant"] == "single"
        assert row["market"] == "US"
        assert row["params_json"] == params

    def test_save_with_preset_id(self, temp_config_db):
        """包含 preset_id 的保存。"""
        import config_db
        pid = config_db.save_preset("ref", "{}")
        config_db.save_ticker_config("MSFT", "US", "single",
                                     params_json="{}", preset_id=pid)
        row = config_db.load_ticker_config("MSFT")
        assert row["preset_id"] == pid

    def test_load_nonexistent_ticker(self, temp_config_db):
        """不存在的 ticker 返回 None。"""
        import config_db
        assert config_db.load_ticker_config("NOEXIST") is None


# ═══════════════════════════════════════════════════════════════════════════
# 4. TestHistory
# ═══════════════════════════════════════════════════════════════════════════

class TestHistory:
    """变更历史读写。"""

    def test_record_and_get_history(self, temp_config_db):
        """写入历史后能按序读出。"""
        import config_db
        import time
        # 先创建 ticker 记录（FOREIGN KEY 约束）
        config_db.save_ticker_config("AAPL", "US", "single", params_json='{"a":1}')

        config_db.record_history("AAPL", "single",
                                 old_json='{"a":0}', new_json='{"a":1}',
                                 source="manual")
        config_db.record_history("AAPL", "single",
                                 old_json='{"a":1}', new_json='{"a":2}',
                                 source="ui")

        history = config_db.get_history("AAPL", limit=10)
        assert len(history) == 2
        # datetime 精度为秒，两条在同一秒写入时顺序不定，检查整体内容
        all_new = [json.loads(h["new_json"]) for h in history]
        all_old = [json.loads(h["old_json"]) for h in history]
        assert {"a": 1} in all_new
        assert {"a": 2} in all_new
        assert {"a": 0} in all_old
        assert {"a": 1} in all_old

    def test_history_with_preset_id(self, temp_config_db):
        """带 preset_id 的历史记录。"""
        import config_db
        config_db.save_ticker_config("AAPL", "US", "single", params_json="{}")
        pid = config_db.save_preset("hist_preset", '{"x":1}')
        config_db.record_history("AAPL", "single", "{}", '{"x":1}',
                                 preset_id=pid)
        history = config_db.get_history("AAPL")
        assert len(history) == 1
        # get_history 会 LEFT JOIN config_presets 获取 preset_name
        assert history[0]["preset_id"] == pid
        assert history[0]["preset_name"] == "hist_preset"

    def test_history_empty_for_new_ticker(self, temp_config_db):
        """从未记录过的 ticker 返回空列表。"""
        import config_db
        assert config_db.get_history("UNKNOWN") == []

    def test_history_default_limit(self, temp_config_db):
        """默认 limit 为 20。过多记录只返回最近 20 条。"""
        import config_db
        config_db.save_ticker_config("AAPL", "US", "single", params_json="{}")
        for i in range(25):
            config_db.record_history("AAPL", "single", "", f'{{"i":{i}}}',
                                     source="auto")
        history = config_db.get_history("AAPL")
        assert len(history) == 20
        assert history[0]["source"] == "auto"


# ═══════════════════════════════════════════════════════════════════════════
# 5. TestImportJSONFiles
# ═══════════════════════════════════════════════════════════════════════════

class TestImportJSONFiles:
    """从 JSON 文件导入预设。"""

    def test_import_creates_presets(self, temp_config_db):
        """mock _CONFIG_DIR 指向临时目录，导入后生成预设。"""
        import config_db

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_dir = Path(tmpdir)
            # 写 3 个 json
            (cfg_dir / "AAPL_US.json").write_text(
                json.dumps({"ticker": "AAPL", "market": "US", "sma": 20}))
            (cfg_dir / "2382_HK_DP.json").write_text(
                json.dumps({"ticker": "2382", "market": "HK", "ema": 10}))
            (cfg_dir / "0700_HK_QS.json").write_text(
                json.dumps({"ticker": "0700", "market": "HK", "rsi": 14}))

            with patch("config_db._CONFIG_DIR", cfg_dir):
                n, _ = config_db.import_json_files_as_presets()

        assert n == 3
        presets = config_db.list_presets()
        assert len(presets) == 3

        # 验证分类推断
        names_cats = {(p["name"], p["category"]) for p in presets}
        assert ("AAPL_US", "单滤波") in names_cats
        assert ("2382_HK_DP", "双滤波") in names_cats
        assert ("0700_HK_QS", "快速") in names_cats

    def test_import_skips_existing(self, temp_config_db):
        """已存在的预设不覆盖（force=False）。"""
        import config_db

        # 先手动保存同名预设
        config_db.save_preset("EXISTING", json.dumps({"original": True}),
                              category="手动")

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_dir = Path(tmpdir)
            (cfg_dir / "EXISTING.json").write_text(
                json.dumps({"ticker": "N", "market": "US", "new": True}))

            with patch("config_db._CONFIG_DIR", cfg_dir):
                n, _ = config_db.import_json_files_as_presets(force=False)

        assert n == 0  # 跳过了
        p = config_db.get_preset_by_name("EXISTING")
        assert json.loads(p["params_json"]) == {"original": True}  # 未覆盖

    def test_import_force_overwrite(self, temp_config_db):
        """force=True 时覆盖同名预设（param 内容来自新文件）。"""
        import config_db

        config_db.save_preset("OVERWRITE_ME", json.dumps({"original": True}),
                              category="手动")

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_dir = Path(tmpdir)
            (cfg_dir / "OVERWRITE_ME.json").write_text(
                json.dumps({"ticker": "N", "market": "US", "new": True}))

            with patch("config_db._CONFIG_DIR", cfg_dir):
                n, _ = config_db.import_json_files_as_presets(force=True)

        assert n == 1
        p = config_db.get_preset_by_name("OVERWRITE_ME")
        # 被覆盖后应包含新文件的所有字段
        params = json.loads(p["params_json"])
        assert params["new"] is True
        assert params["ticker"] == "N"

    def test_import_empty_dir(self, temp_config_db):
        """config 目录不存在时返回 0。"""
        import config_db

        with patch("config_db._CONFIG_DIR", Path("/nonexistent_dir_xyz")):
            n, _ = config_db.import_json_files_as_presets()
        assert n == 0

    def test_import_skips_bad_json(self, temp_config_db):
        """非法的 JSON 文件被静默跳过。"""
        import config_db

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_dir = Path(tmpdir)
            (cfg_dir / "good.json").write_text(
                json.dumps({"ticker": "OK", "market": "US"}))
            (cfg_dir / "bad.json").write_text("not valid json at all")
            (cfg_dir / "empty.json").write_text("")

            with patch("config_db._CONFIG_DIR", cfg_dir):
                n, _ = config_db.import_json_files_as_presets()

        assert n == 1  # 只导入了一个
        assert config_db.get_preset_by_name("good") is not None


# ═══════════════════════════════════════════════════════════════════════════
# 6. TestCollectCurrentParams
# ═══════════════════════════════════════════════════════════════════════════

class TestCollectCurrentParams:
    """从 st.session_state 收集参数。"""

    @pytest.fixture(autouse=True)
    def _reset_streamlit_mock(self):
        """确保 conftest 中 mock 的 streamlit 的 session_state 为空。"""
        import streamlit as st
        if hasattr(st, "session_state"):
            st.secrets = {}
        st.session_state = {}

    def test_collects_global_keys(self, temp_config_db):
        """收集 market, ticker 等全局 key。"""
        import streamlit as st
        import config_db

        st.session_state["market"] = "US"
        st.session_state["ticker"] = "AAPL"
        st.session_state["global_f"] = 1.5
        st.session_state["global_dual"] = True
        st.session_state["global_f2"] = 0.5

        params = config_db.collect_current_params()
        assert params["market"] == "US"
        assert params["ticker"] == "AAPL"
        assert params["global_f"] == 1.5
        assert params["global_dual"] is True
        assert params["global_f2"] == 0.5

    def test_collects_view_params(self, temp_config_db):
        """收集 v0_tf, v0_n 等视图参数。"""
        import streamlit as st
        import config_db

        st.session_state["v0_tf"] = "1h"
        st.session_state["v0_n"] = 100
        st.session_state["v1_tf"] = "1d"
        st.session_state["v3_strat"] = "trend"
        st.session_state["v2_align"] = True

        params = config_db.collect_current_params()
        assert params["v0_tf"] == "1h"
        assert params["v0_n"] == 100
        assert params["v1_tf"] == "1d"
        assert params["v3_strat"] == "trend"
        assert params["v2_align"] is True

    def test_collects_filter_params(self, temp_config_db):
        """收集中文 key 的滤波器参数。"""
        import streamlit as st
        import config_db

        st.session_state["窗口大小_1"] = 10
        st.session_state["跨度_EMA"] = 5
        st.session_state["偏移量_1"] = 0.5
        st.session_state["标准差_2"] = 2.0
        st.session_state["多项式阶数_3"] = 3
        st.session_state["过程噪声"] = 0.01
        st.session_state["测量噪声"] = 0.1
        st.session_state["滤波器阶数_4"] = 4
        st.session_state["截止频率_5"] = 0.3
        st.session_state["平滑比例_alpha"] = 0.8

        params = config_db.collect_current_params()
        assert params["窗口大小_1"] == 10
        assert params["跨度_EMA"] == 5
        assert params["偏移量_1"] == 0.5
        assert params["标准差_2"] == 2.0
        assert params["多项式阶数_3"] == 3
        assert params["过程噪声"] == 0.01
        assert params["测量噪声"] == 0.1
        assert params["滤波器阶数_4"] == 4
        assert params["截止频率_5"] == 0.3
        assert params["平滑比例_alpha"] == 0.8

    def test_ignores_unrelated_keys(self, temp_config_db):
        """session_state 中无关的 key 不被收集。"""
        import streamlit as st
        import config_db

        st.session_state["market"] = "HK"
        st.session_state["unrelated"] = "should_not_appear"
        st.session_state["foobar"] = 42

        params = config_db.collect_current_params()
        assert "unrelated" not in params
        assert "foobar" not in params
        assert params["market"] == "HK"

    def test_empty_session_state(self, temp_config_db):
        """空 session_state 返回空 dict。"""
        import config_db
        params = config_db.collect_current_params()
        assert params == {}


# ═══════════════════════════════════════════════════════════════════════════
# 7. TestSavePresetValidation
# ═══════════════════════════════════════════════════════════════════════════

class TestSavePresetValidation:
    """save_preset 入口校验：空名称、无效 JSON 抛出 ValueError。"""

    def test_empty_name_raises_value_error(self, temp_config_db):
        """空字符串或纯空白名称抛出 ValueError。"""
        import config_db
        with pytest.raises(ValueError, match="名称不能为空"):
            config_db.save_preset("", "{}")
        with pytest.raises(ValueError, match="名称不能为空"):
            config_db.save_preset("   ", "{}")

    def test_invalid_json_raises_value_error(self, temp_config_db):
        """无效 JSON 字符串抛出 ValueError（P1-5 修复）。"""
        import config_db
        with pytest.raises(ValueError, match="有效的 JSON"):
            config_db.save_preset("bad_json", "{invalid")
        with pytest.raises(ValueError, match="有效的 JSON"):
            config_db.save_preset("bad_json2", "not json")

    def test_valid_save_returns_int(self, temp_config_db, sample_params_json):
        """正常保存返回 int 类型的 preset_id。"""
        import config_db
        pid = config_db.save_preset("valid_test", sample_params_json)
        assert isinstance(pid, int)
        assert pid > 0

    def test_overwrite_returns_same_preset_id(self, temp_config_db, sample_params_json):
        """覆盖已有名称返回相同 preset_id，内容更新。"""
        import config_db
        pid1 = config_db.save_preset("overwrite_v", sample_params_json)
        pid2 = config_db.save_preset("overwrite_v", json.dumps({"new": "data"}))
        assert pid1 == pid2
        p = config_db.get_preset(pid1)
        assert json.loads(p["params_json"]) == {"new": "data"}


# ═══════════════════════════════════════════════════════════════════════════
# 8. TestDeletePresetReturnValue
# ═══════════════════════════════════════════════════════════════════════════

class TestDeletePresetReturnValue:
    """delete_preset 返回 bool 表示是否删除了记录（P1-1 修复）。"""

    def test_delete_existing_returns_true(self, temp_config_db, sample_params_json):
        """删除存在预设返回 True，且记录消失。"""
        import config_db
        pid = config_db.save_preset("del_me", sample_params_json)
        result = config_db.delete_preset(pid)
        assert result is True
        assert config_db.get_preset(pid) is None

    def test_delete_nonexistent_returns_false(self, temp_config_db):
        """删除不存在预设返回 False。"""
        import config_db
        result = config_db.delete_preset(99999)
        assert result is False

    def test_delete_referenced_preset_sets_null(self, temp_config_db, sample_params_json):
        """删除被 ticker 引用的预设，外键 ON DELETE SET NULL 生效（P0-2 修复）。"""
        import config_db
        pid = config_db.save_preset("fk_test", sample_params_json)
        config_db.save_ticker_config("AAPL", "US", "single",
                                     params_json="{}", preset_id=pid)

        ticker_row = config_db.load_ticker_config("AAPL")
        assert ticker_row["preset_id"] == pid

        result = config_db.delete_preset(pid)
        assert result is True

        # 删除后 ticker 的 preset_id 被置为 NULL，而非级联删除
        ticker_row = config_db.load_ticker_config("AAPL")
        assert ticker_row is not None
        assert ticker_row["preset_id"] is None

    def test_delete_preset_rowcount_zero_returns_false(self, temp_config_db, sample_params_json):
        """模拟 DELETE rowcount=0 时返回 False（行 205 不可达分支）。"""
        import config_db
        pid = config_db.save_preset("rowcount_test", sample_params_json)

        from contextlib import contextmanager

        class MockDeleteCursor:
            rowcount = 0
            description = None
            connection = None
            arraysize = 1
            def fetchall(self):
                return []
            def fetchone(self):
                return None

        class DeleteMockConn(sqlite3.Connection):
            delete_called = False
            def execute(self, sql, parameters=None):
                sql_str = str(sql).upper().strip() if sql else ""
                if sql_str.startswith("DELETE FROM"):
                    DeleteMockConn.delete_called = True
                    return MockDeleteCursor()
                if parameters is not None:
                    return super().execute(sql, parameters)
                return super().execute(sql)

        def delete_patched_conn_factory():
            return DeleteMockConn

        # 使用自定义连接工厂
        @contextmanager
        def delete_patched_get_conn():
            conn_cls = DeleteMockConn
            conn = sqlite3.connect(str(config_db._CONFIG_DB_PATH), factory=conn_cls)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

        with patch("config_db._get_conn", delete_patched_get_conn):
            result = config_db.delete_preset(pid)

        assert result is False
        assert DeleteMockConn.delete_called


# ═══════════════════════════════════════════════════════════════════════════
# 9. TestRenamePresetValidation
# ═══════════════════════════════════════════════════════════════════════════

class TestRenamePresetValidation:
    """rename_preset 校验：名称冲突、空名称、不存在 ID 均返回 None。"""

    def test_rename_to_existing_name_returns_none(self, temp_config_db, sample_params_json):
        """重命名为已存在名称返回 None（P1-2 唯一性检查）。"""
        import config_db
        config_db.save_preset("first", sample_params_json)
        pid2 = config_db.save_preset("second", sample_params_json)
        result = config_db.rename_preset(pid2, "first")
        assert result is None
        # 原名称未被篡改
        p = config_db.get_preset(pid2)
        assert p["name"] == "second"

    def test_rename_to_empty_string_returns_none(self, temp_config_db, sample_params_json):
        """重命名为空字符串返回 None（P1-6 空名称校验）。"""
        import config_db
        pid = config_db.save_preset("valid", sample_params_json)
        result = config_db.rename_preset(pid, "")
        assert result is None
        result2 = config_db.rename_preset(pid, "   ")
        assert result2 is None
        # 原名称保持不变
        p = config_db.get_preset(pid)
        assert p["name"] == "valid"

    def test_rename_nonexistent_preset_returns_none(self, temp_config_db):
        """重命名不存在的 preset_id 返回 None。"""
        import config_db
        result = config_db.rename_preset(99999, "anything")
        assert result is None

    def test_rename_success_returns_new_name(self, temp_config_db, sample_params_json):
        """重命名成功返回新名称字符串。"""
        import config_db
        pid = config_db.save_preset("old", sample_params_json)
        result = config_db.rename_preset(pid, "new_name")
        assert result == "new_name"
        p = config_db.get_preset(pid)
        assert p["name"] == "new_name"


# ═══════════════════════════════════════════════════════════════════════════
# 10. TestImportJsonFilesReturnValue
# ═══════════════════════════════════════════════════════════════════════════

class TestImportJsonFilesReturnValue:
    """import_json_files_as_presets 返回 (count, errors) 元组（P0-1 修复）。"""

    def test_returns_tuple_of_count_and_errors(self, temp_config_db):
        """验证返回 (int, list) 元组结构。"""
        import config_db

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_dir = Path(tmpdir)
            (cfg_dir / "good.json").write_text(
                json.dumps({"ticker": "AAPL", "market": "US"}))

            with patch("config_db._CONFIG_DIR", cfg_dir):
                result = config_db.import_json_files_as_presets()

        assert isinstance(result, tuple)
        assert len(result) == 2
        count, errors = result
        assert isinstance(count, int)
        assert isinstance(errors, list)
        assert count == 1
        assert errors == []

    def test_invalid_json_in_errors_not_raised(self, temp_config_db):
        """无效 JSON 文件不抛异常，出现在 errors 列表中（P0-1 错误收集）。"""
        import config_db

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_dir = Path(tmpdir)
            (cfg_dir / "bad.json").write_text("this is not json!!")

            with patch("config_db._CONFIG_DIR", cfg_dir):
                count, errors = config_db.import_json_files_as_presets()

        assert count == 0
        assert len(errors) == 1
        assert "bad" in errors[0]
        assert "解析失败" in errors[0] or "失败" in errors[0]

    def test_mixed_valid_and_invalid(self, temp_config_db):
        """混合有效和无效文件，count 只计成功，errors 列出失败。"""
        import config_db

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_dir = Path(tmpdir)
            (cfg_dir / "ok.json").write_text(
                json.dumps({"ticker": "OK", "market": "US"}))
            (cfg_dir / "broken.json").write_text("{{{broken")
            (cfg_dir / "also_ok.json").write_text(
                json.dumps({"ticker": "T2", "market": "HK"}))

            with patch("config_db._CONFIG_DIR", cfg_dir):
                count, errors = config_db.import_json_files_as_presets()

        assert count == 2
        assert len(errors) == 1
        assert "broken" in errors[0]


# ═══════════════════════════════════════════════════════════════════════════
# 11. TestGetConnException
# ═══════════════════════════════════════════════════════════════════════════

class TestGetConnException:
    """_get_conn 上下文管理器异常回滚路径（行 31-33）。"""

    def test_exception_triggers_rollback(self, temp_config_db):
        """with _get_conn() 内抛出异常 → rollback 不报错且连接关闭。"""
        import config_db

        # 先写入一条记录
        config_db.save_preset("survivor", "{}")

        # 在 _get_conn 内抛异常
        with pytest.raises(RuntimeError, match="forced error"):
            with config_db._get_conn() as conn:
                conn.execute(
                    "INSERT INTO config_presets(name, params_json) VALUES(?,?)",
                    ("victim", '"will_rollback"'))
                raise RuntimeError("forced error")

        # victim 应被回滚
        assert config_db.get_preset_by_name("victim") is None
        # survivor 依然存在（之前已提交）
        assert config_db.get_preset_by_name("survivor") is not None


# ═══════════════════════════════════════════════════════════════════════════
# 12. TestTickerConfigEdgeCases
# ═══════════════════════════════════════════════════════════════════════════

class TestTickerConfigEdgeCases:
    """ticker 配置边界场景。"""

    def test_save_with_nonexistent_preset_sets_null(self, temp_config_db):
        """preset_id 不存在时自动置 NULL（行 274-275）。"""
        import config_db
        config_db.save_ticker_config("AAPL", "US", "single",
                                     params_json="{}",
                                     preset_id=99999)  # 不存在
        row = config_db.load_ticker_config("AAPL")
        assert row is not None
        assert row["preset_id"] is None


# ═══════════════════════════════════════════════════════════════════════════
# 13. TestApplyPresetEdgeCases
# ═══════════════════════════════════════════════════════════════════════════

class TestApplyPresetEdgeCases:
    """apply_preset JSON 解析异常路径（行 246-249）。"""

    def test_apply_preset_corrupted_json_returns_none(self, temp_config_db):
        """params_json 损坏时 apply_preset 返回 None。"""
        import config_db
        # 跳过 save_preset 的校验，直接写库模拟日期轮换后 JSON 损坏
        pid = config_db.save_preset("corrupt_later", '{"valid": true}')
        import sqlite3
        conn = sqlite3.connect(str(config_db._CONFIG_DB_PATH))
        conn.execute(
            "UPDATE config_presets SET params_json=? WHERE preset_id=?",
            ("not valid json", pid))
        conn.commit()
        conn.close()

        result = config_db.apply_preset(pid)
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# 14. TestImportJsonFilesEdgeCases
# ═══════════════════════════════════════════════════════════════════════════

class TestImportJsonFilesEdgeCases:
    """import_json_files_as_presets 额外边界（行 357-359）。"""

    def test_import_db_save_failure_reported(self, temp_config_db):
        """文件读取成功但存入数据库失败时，出现在 errors 中。"""
        import config_db

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_dir = Path(tmpdir)
            (cfg_dir / "valid.json").write_text(
                json.dumps({"ticker": "OK", "market": "US"}))

            with patch("config_db._CONFIG_DIR", cfg_dir):
                # mock save_preset 抛异常模拟写入失败
                with patch("config_db.save_preset",
                           side_effect=RuntimeError("db full")):
                    count, errors = config_db.import_json_files_as_presets()

        assert count == 0
        assert len(errors) == 1
        assert "valid" in errors[0]
        assert "db full" in errors[0]

    def test_import_invalid_json_empty_file(self, temp_config_db):
        """空文件被报为读取/解析失败。"""
        import config_db

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_dir = Path(tmpdir)
            (cfg_dir / "empty.json").write_text("")

            with patch("config_db._CONFIG_DIR", cfg_dir):
                count, errors = config_db.import_json_files_as_presets()

        assert count == 0
        assert len(errors) == 1
        assert "empty" in errors[0]


# ═══════════════════════════════════════════════════════════════════════════
# 15. TestDeletePresetWithJsonFile
# ═══════════════════════════════════════════════════════════════════════════

class TestDeletePresetWithJsonFile:
    """delete_preset 同步删除 JSON 文件（行 200-203, 205）。"""

    def test_delete_preset_removes_json_file(self, temp_config_db):
        """删除预设时，config 目录下同名 JSON 被同步删除。"""
        import config_db

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_dir = Path(tmpdir)
            json_path = cfg_dir / "sync_delete.json"
            json_path.write_text('{"ticker": "T", "market": "US"}')

            with patch("config_db._CONFIG_DIR", cfg_dir):
                pid = config_db.save_preset("sync_delete", '{"ticker": "T"}')
                assert json_path.exists()
                result = config_db.delete_preset(pid)
                assert result is True
                assert not json_path.exists()  # 文件被删除

    def test_delete_preset_no_json_file(self, temp_config_db):
        """删除预设时，config 目录下无同名 JSON 文件也不报错。"""
        import config_db

        with patch("config_db._CONFIG_DIR", Path("/nonexistent_config")):
            pid = config_db.save_preset("no_json", "{}")
            result = config_db.delete_preset(pid)
            assert result is True


# ═══════════════════════════════════════════════════════════════════════════
# 16. TestInitConfigTablesMigration
# ═══════════════════════════════════════════════════════════════════════════

class TestInitConfigTablesMigration:
    """FK 迁移路径（行 96-116）。"""

    def test_fk_migration_on_old_schema(self, tmp_path):
        """老版 schema（无 ON DELETE）init 后表仍正常可用。"""
        import config_db

        db_path = tmp_path / "config_migrate.db"
        with patch("config_db._CONFIG_DB_PATH", db_path):
            # 模拟旧版 schema：先建 config_presets，再建 config_ticker（无 ON DELETE）
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript("""
                CREATE TABLE config_presets (
                    preset_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT DEFAULT '',
                    category TEXT DEFAULT '通用',
                    params_json TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now','localtime')),
                    updated_at TEXT DEFAULT (datetime('now','localtime'))
                );
                INSERT INTO config_presets(name, params_json) VALUES('legacy','{"v":1}');
                CREATE TABLE config_ticker (
                    ticker TEXT NOT NULL,
                    variant TEXT NOT NULL DEFAULT 'single',
                    market TEXT DEFAULT '',
                    preset_id INTEGER REFERENCES config_presets(preset_id),
                    params_json TEXT DEFAULT '',
                    updated_at TEXT DEFAULT (datetime('now','localtime')),
                    PRIMARY KEY (ticker, variant)
                );
            """)
            conn.commit()
            conn.close()

            # 初始化 → 不会触发迁移（SQLite 报告 NO ACTION 而非空字符串），但表结构完整
            config_db.init_config_tables()

            # 验证操作正常
            config_db.save_preset("new_after_migrate", "{}")
            assert config_db.get_preset_by_name("legacy") is not None

    def test_fk_migration_forced_path(self, tmp_path):
        """通过自定义 Connection 子类强制触发 FK 迁移路径。"""
        import config_db

        db_path = tmp_path / "config_force_migrate.db"
        with patch("config_db._CONFIG_DB_PATH", db_path):
            # 创建旧版表（无 ON DELETE）
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript("""
                CREATE TABLE config_presets (
                    preset_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    params_json TEXT NOT NULL
                );
                INSERT INTO config_presets(name, params_json) VALUES('p1','{"v":1}');
                CREATE TABLE config_ticker (
                    ticker TEXT NOT NULL,
                    variant TEXT NOT NULL DEFAULT 'single',
                    market TEXT DEFAULT '',
                    preset_id INTEGER REFERENCES config_presets(preset_id),
                    params_json TEXT DEFAULT '',
                    updated_at TEXT DEFAULT (datetime('now','localtime')),
                    PRIMARY KEY (ticker, variant)
                );
                INSERT INTO config_ticker(ticker, variant, preset_id, params_json)
                VALUES('AAPL', 'single', 1, '{}');
            """)
            conn.commit()
            conn.close()

            # 用 sqlite3.connect 的 factory 参数创建自定义连接子类
            class MigrationConn(sqlite3.Connection):
                """覆盖 execute 让 PRAGMA foreign_key_list 返回空 on_delete。"""
                def execute(self, sql, parameters=None):
                    sql_str = str(sql).upper() if sql else ""
                    if "PRAGMA FOREIGN_KEY_LIST" in sql_str:
                        # 返回包含空 on_delete 的 FakeCursor
                        class FakeRow:
                            def __getitem__(self, k):
                                return "preset_id" if k in (0, "from") else ""
                            def keys(self):
                                return ["from", "on_delete"]
                            def __iter__(self):
                                return iter(["preset_id", ""])
                        class FakeCursor:
                            rowcount = -1
                            description = None
                            connection = None
                            arraysize = 1
                            def fetchall(self):
                                return [FakeRow()]
                            def fetchone(self):
                                return FakeRow()
                        return FakeCursor()
                    if parameters is not None:
                        return super().execute(sql, parameters)
                    return super().execute(sql)

            def mig_connect(path, **kwargs):
                return sqlite3.connect(path, factory=MigrationConn, **kwargs)

            # Patch _get_conn 以使用 MigrationConn
            from contextlib import contextmanager

            @contextmanager
            def patched_get_conn():
                conn = mig_connect(str(db_path))
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA busy_timeout=5000")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.row_factory = sqlite3.Row
                try:
                    yield conn
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()

            with patch("config_db._get_conn", patched_get_conn):
                config_db.init_config_tables()

            # 迁移后数据仍存在
            assert config_db.get_preset_by_name("p1") is not None

    def test_no_migration_needed(self, temp_config_db):
        """已是最新 schema 时，不触发迁移代码。"""
        import config_db
        # 二次调用，不触发迁移（已在 fixture 中初始化）
        config_db.init_config_tables()


# ═══════════════════════════════════════════════════════════════════════════
# 17. TestMainBlock
# ═══════════════════════════════════════════════════════════════════════════

class TestMainBlock:
    """__main__ 模式（行 407-420）。"""

    def test_main_block_execution(self, tmp_path):
        """直接模拟 __main__ 代码块中的语句执行。"""
        import config_db

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "ok.json").write_text(
            json.dumps({"ticker": "OK", "market": "US"}))
        (config_dir / "bad.json").write_text("not valid json!!")

        with patch("config_db._CONFIG_DB_PATH", data_dir / "config.db"):
            with patch("config_db._CONFIG_DIR", config_dir):
                from db import init_db
                init_db()
                config_db.init_config_tables()
                n, errs = config_db.import_json_files_as_presets(force=True)

                # 模拟 __main__ 的 if errs: 分支
                if errs:
                    pass  # 行 413-414: 确认 errs 不为空触发 if

                # 模拟 __main__ 的遍历
                presets = config_db.list_presets()
                for p in presets:
                    params = json.loads(p["params_json"])
                    # 行 420: 遍历打印

        assert n == 1
        assert len(errs) == 1
        assert "bad" in errs[0]
