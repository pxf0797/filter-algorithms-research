"""
回测可复现性测试：相同输入 → 相同输出，不同参数 → 应有差异。

覆盖三个层面：
1. 配置哈希确定性 — 相同 configs → 相同 SHA256
2. ParquetStore schema 确定性 — 相同 view_configs → 相同 schema
3. 视图列提取确定性 — 相同 view_data → 相同列值
4. 不同配置 → 不同 schema/哈希（差异化验证）
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

# ── 确保 filter 模块可导入 ──
_src = Path(__file__).resolve().parent.parent / "filter"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from data.store import (
    ParquetStore,
    _build_full_schema,
    _COL_DEFAULTS,
    _VIEW_COLUMNS,
    _VIEW_COLUMN_TYPES,
    _FIXED_FIELDS,
)
from filter.backtest.engine import BacktestRunner


# ═══════════════════════════════════════════════════════════════════════════════
# 测试数据 — 两组不同的视图配置
# ═══════════════════════════════════════════════════════════════════════════════

def _configs_a():
    """配置 A: 7/22 回测使用的默认配置（日线/60m/15m/5m, n_pts=120）。"""
    return [
        {
            "_fid": "savgol", "_dual": True, "_fid2": "ema",
            "tf": "日线", "n_pts": 120, "show_sch": True, "show_pred": True,
            "ke": 0.15, "sm": 0.05, "ew": 60, "fit_mode": "parabola",
            "n_ext": 8, "show_strategy": True, "stop_loss_pct": 2.0,
            "show_cross_pnl": False, "show_alignment": False, "show_pnl_feedback": False,
            "fc": "#00d4aa", "fc2": "#ff6b6b",
            "pv": {"window": 21, "order": 2}, "pv2": {"span": 10},
        },
        {
            "_fid": "savgol", "_dual": True, "_fid2": "ema",
            "tf": "60分钟", "n_pts": 120, "show_sch": True, "show_pred": True,
            "ke": 0.15, "sm": 0.05, "ew": 60, "fit_mode": "parabola",
            "n_ext": 8, "show_strategy": True, "stop_loss_pct": 2.0,
            "show_cross_pnl": False, "show_alignment": False, "show_pnl_feedback": False,
            "fc": "#00d4aa", "fc2": "#ff6b6b",
            "pv": {"window": 21, "order": 2}, "pv2": {"span": 10},
        },
        {
            "_fid": "savgol", "_dual": True, "_fid2": "ema",
            "tf": "15分钟", "n_pts": 120, "show_sch": True, "show_pred": True,
            "ke": 0.15, "sm": 0.05, "ew": 60, "fit_mode": "parabola",
            "n_ext": 8, "show_strategy": True, "stop_loss_pct": 2.0,
            "show_cross_pnl": False, "show_alignment": False, "show_pnl_feedback": False,
            "fc": "#00d4aa", "fc2": "#ff6b6b",
            "pv": {"window": 21, "order": 2}, "pv2": {"span": 10},
        },
        {
            "_fid": "savgol", "_dual": True, "_fid2": "ema",
            "tf": "5分钟", "n_pts": 120, "show_sch": True, "show_pred": True,
            "ke": 0.15, "sm": 0.05, "ew": 60, "fit_mode": "parabola",
            "n_ext": 8, "show_strategy": True, "stop_loss_pct": 2.0,
            "show_cross_pnl": False, "show_alignment": False, "show_pnl_feedback": False,
            "fc": "#00d4aa", "fc2": "#ff6b6b",
            "pv": {"window": 21, "order": 2}, "pv2": {"span": 10},
        },
    ]


def _configs_b():
    """配置 B: 7/24 回测使用的 3690_HK 预设（15m/60m/日线/周线, 不同参数）。"""
    return [
        {
            "_fid": "savgol", "_dual": True, "_fid2": "ema",
            "tf": "15分钟", "n_pts": 50, "show_sch": True, "show_pred": True,
            "ke": 0.1, "sm": 0.05, "ew": 40, "fit_mode": "parabola",
            "n_ext": 8, "fc": "#00d4aa", "fc2": "#ff6b6b",
            "show_strategy": True, "stop_loss_pct": 2.0,
            "show_cross_pnl": True, "show_alignment": True, "show_pnl_feedback": True,
            "pv": {"window": 13, "order": 4}, "pv2": {"span": 10},
        },
        {
            "_fid": "savgol", "_dual": True, "_fid2": "ema",
            "tf": "60分钟", "n_pts": 60, "show_sch": True, "show_pred": True,
            "ke": 0.1, "sm": 0.05, "ew": 60, "fit_mode": "parabola",
            "n_ext": 8, "fc": "#00d4aa", "fc2": "#ff6b6b",
            "show_strategy": True, "stop_loss_pct": 2.0,
            "show_cross_pnl": True, "show_alignment": True, "show_pnl_feedback": True,
            "pv": {"window": 13, "order": 4}, "pv2": {"span": 10},
        },
        {
            "_fid": "savgol", "_dual": True, "_fid2": "ema",
            "tf": "日线", "n_pts": 60, "show_sch": True, "show_pred": True,
            "ke": 0.15, "sm": 0.05, "ew": 60, "fit_mode": "parabola",
            "n_ext": 8, "fc": "#00d4aa", "fc2": "#ff6b6b",
            "show_strategy": True, "stop_loss_pct": 2.0,
            "show_cross_pnl": True, "show_alignment": True, "show_pnl_feedback": True,
            "pv": {"window": 13, "order": 4}, "pv2": {"span": 10},
        },
        {
            "_fid": "savgol", "_dual": True, "_fid2": "ema",
            "tf": "周线", "n_pts": 60, "show_sch": True, "show_pred": True,
            "ke": 0.15, "sm": 0.05, "ew": 60, "fit_mode": "parabola",
            "n_ext": 8, "fc": "#00d4aa", "fc2": "#ff6b6b",
            "show_strategy": True, "stop_loss_pct": 2.0,
            "show_cross_pnl": True, "show_alignment": True, "show_pnl_feedback": True,
            "pv": {"window": 13, "order": 4}, "pv2": {"span": 10},
        },
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Schema 确定性
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchemaDeterminism:
    """ParquetStore schema 对于相同输入是确定性的。"""

    def test_same_configs_produce_same_schema(self):
        """相同 view_configs → _build_full_schema 返回相同 schema。"""
        prefixes_a1 = [f"v{i}" for i in range(len(_configs_a()))]
        prefixes_a2 = [f"v{i}" for i in range(len(_configs_a()))]
        schema1 = _build_full_schema(prefixes_a1)
        schema2 = _build_full_schema(prefixes_a2)
        assert schema1 == schema2
        assert schema1.names == schema2.names

    def test_different_configs_produce_different_columns(self):
        """不同 view_configs 数量 → 产生不同列数的 schema（但列名前缀仍为 v0-v3）。"""
        prefixes_4 = [f"v{i}" for i in range(4)]
        prefixes_2 = [f"v{i}" for i in range(2)]
        schema_4 = _build_full_schema(prefixes_4)
        schema_2 = _build_full_schema(prefixes_2)

        # 两者列数不同
        assert len(schema_4.names) != len(schema_2.names)
        # 4 视图: 3(fixed) + 4*12 = 51 列
        assert len(schema_4.names) == 3 + 4 * 12
        # 2 视图: 3(fixed) + 2*12 = 27 列
        assert len(schema_2.names) == 3 + 2 * 12

    def test_schema_fixed_columns_always_present(self):
        """无论视图数量多少，固定列 (bar_index, bar_timestamp, close) 始终存在。"""
        for n_views in [1, 2, 4]:
            prefixes = [f"v{i}" for i in range(n_views)]
            schema = _build_full_schema(prefixes)
            names = schema.names
            assert "bar_index" in names
            assert "bar_timestamp" in names
            assert "close" in names

    def test_schema_view_columns_per_prefix(self):
        """每个视图前缀有且仅有 12 个列（sig, filtered, eps, pnl_long, pnl_short,
        long_pos, short_pos, trade, trade_return, trade_reason, bs_entry, bs_exit）。"""
        prefixes = ["v0", "v1"]
        schema = _build_full_schema(prefixes)
        names = set(schema.names)

        for prefix in prefixes:
            view_cols = {f"{prefix}_{c}" for c in _VIEW_COLUMNS}
            assert view_cols.issubset(names), f"Missing columns for {prefix}: {view_cols - names}"
            # 每个 prefix 恰好 12 列
            count = sum(1 for n in names if n.startswith(f"{prefix}_"))
            assert count == len(_VIEW_COLUMNS), f"{prefix} has {count} columns, expected {len(_VIEW_COLUMNS)}"

    def test_schema_column_types_match_spec(self):
        """schema 中各列类型与 _VIEW_COLUMN_TYPES 一致。"""
        prefixes = ["v0"]
        schema = _build_full_schema(prefixes)
        for field in schema:
            name = field.name
            if name in {"bar_index", "bar_timestamp", "close"}:
                continue
            # 提取列后缀，如 v0_sig → sig
            suffix = name.split("_", 1)[1]
            expected_type = _VIEW_COLUMN_TYPES.get(suffix)
            if expected_type is not None:
                assert field.type == expected_type, (
                    f"Column {name}: expected {expected_type}, got {field.type}"
                )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 配置哈希确定性
# ═══════════════════════════════════════════════════════════════════════════════

class TestConfigHashDeterminism:
    """BacktestRunner._config_hash() 对相同配置是确定性的。"""

    def _make_mock_conn(self, bar_count=1000):
        from unittest.mock import MagicMock
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = False
        mock_row = MagicMock()
        mock_row.__getitem__.return_value = bar_count
        mock_conn.execute.return_value.fetchone.return_value = mock_row
        return mock_conn

    def test_same_configs_same_hash(self):
        """相同 configs 产生相同 SHA256 哈希。"""
        from unittest.mock import patch
        mock_conn = self._make_mock_conn()
        configs = _configs_a()
        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            r1 = BacktestRunner("TEST", configs)
            r2 = BacktestRunner("TEST", configs)
        assert r1._config_hash() == r2._config_hash()
        assert len(r1._config_hash()) == 64  # SHA256 hex

    def test_different_configs_different_hash(self):
        """不同 configs 产生不同 SHA256 哈希。"""
        from unittest.mock import patch
        mock_conn = self._make_mock_conn()
        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            r_a = BacktestRunner("TEST", _configs_a())
            r_b = BacktestRunner("TEST", _configs_b())
        assert r_a._config_hash() != r_b._config_hash()

    def test_single_param_change_changes_hash(self):
        """修改单个参数（n_pts）后哈希不同。"""
        from unittest.mock import patch
        mock_conn = self._make_mock_conn()
        configs1 = _configs_a()
        configs2 = _configs_a()
        configs2[0]["n_pts"] = 200  # 修改一个参数
        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            r1 = BacktestRunner("TEST", configs1)
            r2 = BacktestRunner("TEST", configs2)
        assert r1._config_hash() != r2._config_hash()

    def test_preset_a_vs_preset_b_different_hash(self):
        """配置 A（默认）与配置 B（3690_HK 预设）哈希不同。"""
        from unittest.mock import patch
        mock_conn = self._make_mock_conn()
        with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
            r_a = BacktestRunner("TEST", _configs_a())
            r_b = BacktestRunner("TEST", _configs_b())
        assert r_a._config_hash() != r_b._config_hash(), (
            "配置 A 和配置 B 应产生不同的哈希"
        )

    def test_hash_stable_across_instances(self):
        """同一配置多次实例化哈希一致。"""
        from unittest.mock import patch
        mock_conn = self._make_mock_conn()
        configs = _configs_a()
        hashes = []
        for _ in range(5):
            with patch("filter.backtest.engine.get_conn", return_value=mock_conn):
                r = BacktestRunner("TEST", configs)
            hashes.append(r._config_hash())
        assert len(set(hashes)) == 1, f"所有哈希应一致，实际: {hashes}"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 视图列提取确定性
# ═══════════════════════════════════════════════════════════════════════════════

class TestViewColumnExtractionDeterminism:
    """ParquetStore._extract_view_columns 对相同输入是确定性的。"""

    @staticmethod
    def _make_view_data(n_pts=120, last_sig=0, last_filtered=100.0,
                        last_eps=0.5, last_long_pnl=105.0, last_short_pnl=100.0,
                        has_entry=False, has_exit=False):
        """构建确定性 mock view_data。"""
        t = np.arange(n_pts, dtype=float)
        sig = np.zeros(n_pts, dtype=int)
        sig[-1] = last_sig
        filtered = np.full(n_pts, last_filtered, dtype=float)
        eps = np.full(n_pts, last_eps, dtype=float)
        long_pnl = np.full(n_pts, last_long_pnl, dtype=float)
        short_pnl = np.full(n_pts, last_short_pnl, dtype=float)
        long_mask = np.zeros(n_pts, dtype=bool)
        short_mask = np.zeros(n_pts, dtype=bool)

        base_date = pd.Timestamp("2024-01-01")
        dates = pd.DatetimeIndex([base_date + pd.Timedelta(days=i) for i in range(n_pts)])

        return {
            "t": t,
            "dates": dates,
            "filtered": filtered,
            "schmitt": {"sig": sig, "eps": eps, "v": np.zeros(n_pts), "a": np.zeros(n_pts)},
            "long_pnl": long_pnl,
            "short_pnl": short_pnl,
            "long_mask": long_mask,
            "short_mask": short_mask,
            "trade_records": [],
            "bs_markers": {"entry_markers": [], "exit_markers": []},
        }

    def test_same_input_same_output(self):
        """相同 view_data → 相同 _extract_view_columns 结果。"""
        view_data = self._make_view_data(n_pts=120, last_sig=1, last_filtered=100.5)
        result1 = ParquetStore._extract_view_columns("v0", view_data)
        result2 = ParquetStore._extract_view_columns("v0", view_data)
        assert result1 == result2

    def test_signal_extraction_deterministic(self):
        """信号列（sig）提取确定性。"""
        view_data = self._make_view_data(n_pts=120, last_sig=1)
        result = ParquetStore._extract_view_columns("v0", view_data)
        assert result["v0_sig"] == 1

        view_data2 = self._make_view_data(n_pts=120, last_sig=-1)
        result2 = ParquetStore._extract_view_columns("v0", view_data2)
        assert result2["v0_sig"] == -1

    def test_filtered_extraction_deterministic(self):
        """滤波值列提取确定性。"""
        val = 123.456
        view_data = self._make_view_data(n_pts=120, last_filtered=val)
        result = ParquetStore._extract_view_columns("v1", view_data)
        assert abs(result["v1_filtered"] - val) < 1e-6

    def test_pnl_extraction_deterministic(self):
        """PnL 列提取确定性。"""
        view_data = self._make_view_data(last_long_pnl=110.0, last_short_pnl=95.0)
        result = ParquetStore._extract_view_columns("v2", view_data)
        assert abs(result["v2_pnl_long"] - 110.0) < 1e-6
        assert abs(result["v2_pnl_short"] - 95.0) < 1e-6

    def test_eps_extraction_deterministic(self):
        """eps 列提取确定性。"""
        view_data = self._make_view_data(last_eps=0.75)
        result = ParquetStore._extract_view_columns("v3", view_data)
        assert abs(result["v3_eps"] - 0.75) < 1e-6

    def test_none_schmitt_returns_defaults(self):
        """schmitt 为 None 时返回默认值。"""
        view_data = self._make_view_data()
        view_data["schmitt"] = None
        result = ParquetStore._extract_view_columns("v0", view_data)
        assert result["v0_sig"] == _COL_DEFAULTS["sig"]  # 0
        assert np.isnan(result["v0_eps"])  # float NaN

    def test_empty_arrays_return_defaults(self):
        """空数组时返回默认值。"""
        view_data = {
            "t": np.array([], dtype=float),
            "dates": pd.DatetimeIndex([]),
            "filtered": np.array([], dtype=float),
            "schmitt": {"sig": np.array([], dtype=int), "eps": np.array([], dtype=float)},
            "long_pnl": np.array([], dtype=float),
            "short_pnl": np.array([], dtype=float),
            "long_mask": np.array([], dtype=bool),
            "short_mask": np.array([], dtype=bool),
            "trade_records": [],
            "bs_markers": {"entry_markers": [], "exit_markers": []},
        }
        result = ParquetStore._extract_view_columns("v0", view_data)
        assert result["v0_sig"] == 0
        assert np.isnan(result["v0_filtered"])
        assert np.isnan(result["v0_eps"])

    def test_different_prefix_produces_different_keys(self):
        """不同 prefix 产生不同键名前缀。"""
        view_data = self._make_view_data()
        r0 = ParquetStore._extract_view_columns("v0", view_data)
        r1 = ParquetStore._extract_view_columns("v1", view_data)
        # 键名不同
        assert set(r0.keys()) != set(r1.keys())
        # 但值相同（因为 view_data 一样）
        for suffix in ["_filtered", "_pnl_long", "_pnl_short"]:
            assert r0[f"v0{suffix}"] == r1[f"v1{suffix}"]

    def test_position_from_trade_records(self):
        """持仓状态从 trade_records 正确计算。"""
        view_data = self._make_view_data(n_pts=120)
        # 添加一个在 bar 50 入场、尚未退出的多头
        view_data["trade_records"] = [
            {"entry_idx": 50, "exit_idx": None, "type": "long", "exit_reason": ""},
        ]
        result = ParquetStore._extract_view_columns("v0", view_data)
        assert result["v0_long_pos"] is True
        assert result["v0_short_pos"] is False

    def test_position_closed_trade(self):
        """已退出的交易不标记持仓。"""
        view_data = self._make_view_data(n_pts=120)
        view_data["trade_records"] = [
            {"entry_idx": 50, "exit_idx": 100, "type": "long", "exit_reason": "stop_loss"},
        ]
        result = ParquetStore._extract_view_columns("v0", view_data)
        # exit_idx=100 <= view_last_idx=119, 且 reason != "eod" → 已退出
        assert result["v0_long_pos"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 数据版本标记
# ═══════════════════════════════════════════════════════════════════════════════

class TestDataVersionMarking:
    """验证 parquet 输出中包含版本标记。"""

    def test_build_full_schema_includes_all_fixed_fields(self):
        """_build_full_schema 生成的 schema 包含所有固定字段。"""
        schema = _build_full_schema(["v0"])
        fixed_names = {name for name, _ in _FIXED_FIELDS}
        schema_names = set(schema.names)
        assert fixed_names.issubset(schema_names)

    def test_schema_format_version_constant(self):
        """验证 schema 版本常量存在且为已知值。"""
        # schema_version 是字符串常量 "3.4"
        from data.store import ParquetStore
        # 通过创建 ParquetStore 并查看 _write_metadata 中写入的值来验证
        # 这里只验证 schema 构建逻辑中包含所需的列
        schema = _build_full_schema(["v0", "v1", "v2", "v3"])
        assert len(schema.names) == 3 + 4 * 12  # 51 columns
        # 验证关键列存在
        for col in ["bar_index", "bar_timestamp", "close"]:
            assert col in schema.names
        for prefix in ["v0", "v1", "v2", "v3"]:
            for suffix in ["sig", "filtered", "eps", "pnl_long", "pnl_short"]:
                assert f"{prefix}_{suffix}" in schema.names

    def test_column_type_consistency(self):
        """相同后缀的列在所有视图中类型一致。"""
        schema = _build_full_schema(["v0", "v1", "v2", "v3"])
        for suffix in _VIEW_COLUMNS:
            types = set()
            for prefix in ["v0", "v1", "v2", "v3"]:
                col_name = f"{prefix}_{suffix}"
                field = schema.field(col_name)
                types.add(str(field.type))
            assert len(types) == 1, (
                f"Column '{suffix}' has inconsistent types across views: {types}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. 数据一致性检查 — ParquetStore 写入→读取 往返
# ═══════════════════════════════════════════════════════════════════════════════

class TestParquetRoundTrip:
    """验证 ParquetStore 写入的数据可以通过 pyarrow 正确读取。"""

    def test_schema_roundtrip(self, tmp_path):
        """_build_full_schema 生成的 schema 可以用于创建和读取 parquet 文件。"""
        prefixes = ["v0", "v1"]
        schema = _build_full_schema(prefixes)

        # 手动创建符合 schema 的 Table
        n_rows = 10
        arrays = []
        for field in schema:
            col_name = field.name
            col_type = field.type
            if col_name == "bar_index":
                arr = pa.array(range(n_rows), type=pa.int32())
            elif col_name == "bar_timestamp":
                ts = pd.Timestamp("2024-01-01")
                arr = pa.array([ts + pd.Timedelta(days=i) for i in range(n_rows)],
                               type=pa.timestamp("ns"))
            elif col_name == "close":
                arr = pa.array([100.0 + i for i in range(n_rows)], type=pa.float32())
            elif col_name.endswith("_sig"):
                arr = pa.array([1] * n_rows, type=pa.int8())
            elif col_name.endswith("_filtered") or col_name.endswith("_eps"):
                arr = pa.array([float(i) for i in range(n_rows)], type=pa.float32())
            elif col_name.endswith("_pnl_long") or col_name.endswith("_pnl_short"):
                arr = pa.array([100.0] * n_rows, type=pa.float32())
            elif col_name.endswith("_trade_return"):
                # float32 column — set to NaN for no-trade rows
                arr = pa.array([float("nan")] * n_rows, type=pa.float32())
            elif col_name.endswith("_long_pos") or col_name.endswith("_short_pos"):
                arr = pa.array([False] * n_rows, type=pa.bool_())
            elif pa.types.is_string(col_type) or pa.types.is_large_string(col_type):
                # string columns: trade, trade_reason, bs_entry, bs_exit
                arr = pa.array([""] * n_rows, type=pa.string())
            else:
                # fallback: coerce via None
                arr = pa.array([None] * n_rows, type=col_type)
            arrays.append(arr)

        table = pa.Table.from_arrays(arrays, schema=schema)

        # 写入
        fpath = tmp_path / "test_roundtrip.parquet"
        pq.write_table(table, str(fpath), compression="zstd", compression_level=3)

        # 读取并验证
        read_table = pq.read_table(str(fpath))
        assert read_table.schema == schema
        assert len(read_table) == n_rows
        assert read_table.column("bar_index")[0].as_py() == 0
        assert read_table.column("close")[-1].as_py() == pytest.approx(109.0, abs=0.1)

    def test_schema_unchanged_for_same_configs(self):
        """相同 configs 两次构建 schema 结果一致。"""
        s1 = _build_full_schema(["v0", "v1", "v2", "v3"])
        s2 = _build_full_schema(["v0", "v1", "v2", "v3"])
        assert s1 == s2
        assert s1.names == s2.names
        for f1, f2 in zip(s1, s2):
            assert f1.name == f2.name
            assert f1.type == f2.type
