"""
tests/test_config_db.py — 完整单元测试覆盖 config_db 模块
每个测试使用临时 DB 文件，互不污染。
"""

import json
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
        """重名应报错（UNIQUE 约束）。"""
        import config_db
        config_db.save_preset("first", sample_params_json)
        pid2 = config_db.save_preset("second", sample_params_json)
        with pytest.raises(sqlite3.IntegrityError):
            config_db.rename_preset(pid2, "first")

    def test_apply_preset(self, temp_config_db):
        """apply_preset 返回解析后的 dict。"""
        import config_db
        params = {"ma": 10, "std": 2.5}
        pid = config_db.save_preset("apply_test", json.dumps(params))
        result = config_db.apply_preset(pid)
        assert result == params

    def test_apply_preset_invalid_json(self, temp_config_db):
        """非法 JSON 返回 None。"""
        import config_db
        pid = config_db.save_preset("bad_json", "{not valid}")
        assert config_db.apply_preset(pid) is None

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
                n = config_db.import_json_files_as_presets()

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
                n = config_db.import_json_files_as_presets(force=False)

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
                n = config_db.import_json_files_as_presets(force=True)

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
            n = config_db.import_json_files_as_presets()
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
                n = config_db.import_json_files_as_presets()

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
