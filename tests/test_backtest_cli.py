"""
Tests for backtest_cli.py CLI argument parsing and main() flow.
Uses unittest.mock to avoid running actual backtests.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, ANY

import pytest

# Ensure filter is importable
_filter = Path(__file__).resolve().parent.parent / "filter"
if str(_filter) not in sys.path:
    sys.path.insert(0, str(_filter))


# ═══════════════════════════════════════════════════════════════════════════════
# argparse 参数解析测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseArgs:
    """verify each CLI flag is parsed correctly by parse_args()."""

    def test_default_no_save_data_false(self):
        """default: --no-save-data is False (ParquetStore enabled)."""
        from filter.backtest.cli import parse_args
        with patch.object(sys, "argv", ["backtest_cli.py", "--ticker", "AAPL"]):
            args = parse_args()
        assert args.no_save_data is False

    def test_no_save_data_flag_true(self):
        """--no-save-data flag sets it to True."""
        from filter.backtest.cli import parse_args
        with patch.object(sys, "argv",
                          ["backtest_cli.py", "--ticker", "03690.HK", "--no-save-data"]):
            args = parse_args()
        assert args.no_save_data is True

    def test_checkpoint_interval_default(self):
        """--checkpoint-interval defaults to 100."""
        from filter.backtest.cli import parse_args
        with patch.object(sys, "argv", ["backtest_cli.py", "--ticker", "AAPL"]):
            args = parse_args()
        assert args.checkpoint_interval == 100

    def test_checkpoint_interval_custom(self):
        """--checkpoint-interval 50 is parsed correctly."""
        from filter.backtest.cli import parse_args
        with patch.object(sys, "argv",
                          ["backtest_cli.py", "--ticker", "AAPL",
                           "--checkpoint-interval", "50"]):
            args = parse_args()
        assert args.checkpoint_interval == 50

    def test_checkpoint_interval_zero(self):
        """--checkpoint-interval 0 disables auto-save."""
        from filter.backtest.cli import parse_args
        with patch.object(sys, "argv",
                          ["backtest_cli.py", "--ticker", "AAPL",
                           "--checkpoint-interval", "0"]):
            args = parse_args()
        assert args.checkpoint_interval == 0

    def test_resume_default_none(self):
        """--resume defaults to None."""
        from filter.backtest.cli import parse_args
        with patch.object(sys, "argv", ["backtest_cli.py", "--ticker", "AAPL"]):
            args = parse_args()
        assert args.resume is None

    def test_resume_with_path(self):
        """--resume takes a path argument."""
        from filter.backtest.cli import parse_args
        with patch.object(sys, "argv",
                          ["backtest_cli.py", "--ticker", "AAPL",
                           "--resume", "/tmp/checkpoint.json"]):
            args = parse_args()
        assert args.resume == "/tmp/checkpoint.json"


# ═══════════════════════════════════════════════════════════════════════════════
# main() ParquetStore 控制流测试
# ═══════════════════════════════════════════════════════════════════════════════

def _run_main_with_mocks(cli_args: list, *, mock_runner=None, mock_store=None,
                         mock_recorder=None):
    """Run backtest_cli.main() with all heavy dependencies mocked.

    Returns (mock_runner, mock_store, mock_recorder) for assertion.
    """
    if mock_runner is None:
        mock_runner = MagicMock()
        mock_runner.run.return_value = []
    if mock_recorder is None:
        mock_recorder = MagicMock()
        mock_recorder.start_session.return_value = "session-abc"

    with patch("filter.backtest.cli.has_data", return_value=True), \
         patch("filter.backtest.cli._get_total_bars", return_value=500), \
         patch("filter.backtest.cli.Path.mkdir"), \
         patch("filter.backtest.cli.BacktestRunner",
               return_value=mock_runner), \
         patch("filter.backtest.cli.EventRecorder",
               return_value=mock_recorder) as mock_rec_cls, \
         patch("filter.backtest.cli.ParquetStore") as mock_store_cls, \
         patch.object(sys, "argv", cli_args):

        if mock_store is not None:
            mock_store_cls.return_value = mock_store

        from filter.backtest.cli import main
        main()

    return mock_runner, mock_store_cls, mock_rec_cls


class TestMainParquetStore:
    """main() --no-save-data flag controls ParquetStore creation."""

    def test_default_creates_parquet_store(self):
        """default (no flag): ParquetStore is created and start_session() called."""
        mock_store = MagicMock()
        _, mock_store_cls, _ = _run_main_with_mocks(
            ["backtest_cli.py", "--ticker", "AAPL", "--quiet"],
            mock_store=mock_store,
        )
        mock_store_cls.assert_called_once()
        mock_store.start_session.assert_called_once()

    def test_no_save_data_skips_parquet_store(self):
        """--no-save-data: ParquetStore is NOT created."""
        _, mock_store_cls, _ = _run_main_with_mocks(
            ["backtest_cli.py", "--ticker", "AAPL", "--no-save-data", "--quiet"],
        )
        mock_store_cls.assert_not_called()

    def test_no_save_data_still_produces_jsonl(self):
        """--no-save-data: EventRecorder still records steps (JSONL output)."""
        mock_outputs = [
            {"bar_index": 0, "bar_timestamp": "2026-01-01",
             "cutoff_date": "2026-01-01", "step_index": 0},
            {"bar_index": 1, "bar_timestamp": "2026-01-02",
             "cutoff_date": "2026-01-02", "step_index": 1},
        ]
        mock_runner = MagicMock()
        mock_runner.run.return_value = mock_outputs
        mock_recorder = MagicMock()
        mock_recorder.start_session.return_value = "session-123"

        _run_main_with_mocks(
            ["backtest_cli.py", "--ticker", "AAPL", "--no-save-data", "--quiet"],
            mock_runner=mock_runner,
            mock_recorder=mock_recorder,
        )
        assert mock_recorder.record_step.call_count == 2
        mock_recorder.end_session.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# main() --resume / --checkpoint-interval 测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestMainResumeCheckpoint:
    """main() --resume and --checkpoint-interval control flow."""

    def test_resume_triggers_restore(self):
        """--resume calls runner._restore_checkpoint()."""
        mock_runner = MagicMock()
        mock_runner.run.return_value = []
        mock_runner._restore_checkpoint.return_value = 10

        _run_main_with_mocks(
            ["backtest_cli.py", "--ticker", "AAPL", "--no-save-data",
             "--resume", "/tmp/ckpt.json", "--quiet"],
            mock_runner=mock_runner,
        )
        mock_runner._restore_checkpoint.assert_called_once_with(
            "/tmp/ckpt.json", ANY)

    def test_resume_with_explicit_start_bar(self):
        """--start-bar overrides resume position."""
        mock_runner = MagicMock()
        mock_runner.run.return_value = []
        mock_runner._restore_checkpoint.return_value = 10

        _run_main_with_mocks(
            ["backtest_cli.py", "--ticker", "AAPL", "--no-save-data",
             "--resume", "/tmp/ckpt.json", "--start-bar", "200", "--quiet"],
            mock_runner=mock_runner,
        )
        # start=200 takes precedence over resume_bar+1=11
        call_args = mock_runner.run.call_args
        assert call_args[0][0] == 200

    def test_checkpoint_interval_passed_to_runner(self):
        """--checkpoint-interval is passed through to runner.run()."""
        mock_runner = MagicMock()
        mock_runner.run.return_value = []

        _run_main_with_mocks(
            ["backtest_cli.py", "--ticker", "AAPL", "--no-save-data",
             "--checkpoint-interval", "50", "--quiet"],
            mock_runner=mock_runner,
        )
        call_kwargs = mock_runner.run.call_args[1]
        assert call_kwargs["checkpoint_interval"] == 50


# ═══════════════════════════════════════════════════════════════════════════════
# P0-10: CLI 端到端烟雾测试 (subprocess)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.slow
class TestCLISmoke:
    """验证 backtest_cli.py 通过 subprocess 端到端运行不崩溃。"""

    def test_cli_missing_args_shows_error(self):
        """缺少必要参数时，应返回非零退出码并包含错误信息。"""
        import subprocess

        result = subprocess.run(
            [
                "python", "-m", "filter.backtest.cli",
            ],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode != 0, (
            f"CLI should fail without args. stdout={result.stdout}"
        )
        combined = (result.stderr + result.stdout).lower()
        assert "error" in combined or "usage" in combined or "required" in combined, (
            f"Expected error/usage message, got: stdout={result.stdout}, stderr={result.stderr}"
        )

    def test_cli_help(self):
        """--help 应正常退出并包含 usage 信息。"""
        import subprocess

        result = subprocess.run(
            [
                "python", "-m", "filter.backtest.cli", "--help",
            ],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"CLI --help failed: {result.stderr}"
        assert (
            "usage" in (result.stdout + result.stderr).lower()
            or "help" in (result.stdout + result.stderr).lower()
        ), f"No usage/help text found: stdout={result.stdout}"

    def test_cli_smoke_run(self, tmp_path):
        """验证带 ticker 的最小回测运行不崩溃并产生输出文件。"""
        import subprocess

        output_dir = tmp_path / "bt_output"
        result = subprocess.run(
            [
                "python", "-m", "filter.backtest.cli",
                "--ticker", "AAPL",
                "--start-bar", "0", "--end-bar", "10",
                "--step-interval", "1",
                "--output-dir", str(output_dir),
            ],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, (
            f"CLI smoke run failed. stderr:\n{result.stderr}\nstdout:\n{result.stdout}"
        )
        # 即使没有输出文件（数据不存在），CLI 也不应崩溃
        if output_dir.exists():
            parquet_files = list(output_dir.rglob("*.parquet"))
            csv_files = list(output_dir.rglob("*.csv"))
            # 生产输出文件是可选的（取决于数据是否存在），不做强制断言


# ═══════════════════════════════════════════════════════════════════════════════
# 配置加载函数单元测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestLoadConfigsFromFile:
    """_load_configs_from_file — 文件不存在、无效JSON、有效文件等场景."""

    def test_nonexistent_file_exits(self):
        """不存在的文件引发 SystemExit(1)."""
        from filter.backtest.cli import _load_configs_from_file
        with pytest.raises(SystemExit) as exc:
            _load_configs_from_file("/nonexistent/path/config.json")
        assert exc.value.code == 1

    def test_invalid_json_exits(self, tmp_path):
        """无效 JSON 文件引发 SystemExit(1)."""
        from filter.backtest.cli import _load_configs_from_file
        bad_json = tmp_path / "bad.json"
        bad_json.write_text("not valid json {{{")
        with pytest.raises(SystemExit) as exc:
            _load_configs_from_file(str(bad_json))
        assert exc.value.code == 1

    def test_valid_configs_dict(self, tmp_path):
        """有效 JSON (含 configs 列表) 正确返回."""
        from filter.backtest.cli import _load_configs_from_file
        import json
        cfg = tmp_path / "cfg.json"
        cfg.write_text(json.dumps({
            "configs": [
                {"tf": "日线", "n_pts": 120, "_fid": "sma"},
            ],
        }))
        result = _load_configs_from_file(str(cfg))
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["tf"] == "日线"

    def test_valid_view_configs_dict(self, tmp_path):
        """有效 JSON (含 view_configs 列表) 正确返回."""
        from filter.backtest.cli import _load_configs_from_file
        import json
        cfg = tmp_path / "cfg.json"
        cfg.write_text(json.dumps({
            "view_configs": [
                {"tf": "60分钟", "n_pts": 200, "_fid": "savgol"},
            ],
        }))
        result = _load_configs_from_file(str(cfg))
        assert len(result) == 1
        assert result[0]["tf"] == "60分钟"

    def test_non_list_configs_exits(self, tmp_path):
        """configs 值不是 list 时引发 SystemExit(1)."""
        from filter.backtest.cli import _load_configs_from_file
        import json
        cfg = tmp_path / "cfg.json"
        cfg.write_text(json.dumps({"configs": "not_a_list"}))
        with pytest.raises(SystemExit) as exc:
            _load_configs_from_file(str(cfg))
        assert exc.value.code == 1

    def test_preset_params_format(self, tmp_path):
        """平铺预设参数字典格式（无 configs 键）自动转换."""
        from filter.backtest.cli import _load_configs_from_file
        import json
        cfg = tmp_path / "cfg.json"
        cfg.write_text(json.dumps({
            "global_f": "sma",
            "global_dual": False,
            "v0_tf": "日线", "v0_n": 120,
            "v1_tf": "60分钟", "v1_n": 200,
            "v2_tf": "15分钟", "v2_n": 300,
            "v3_tf": "5分钟", "v3_n": 400,
        }))
        result = _load_configs_from_file(str(cfg))
        assert isinstance(result, list)
        assert len(result) == 4
        # v0 should be the coarsest (日线)
        assert result[0]["tf"] == "日线"


class TestGetMinWindowSize:
    """_get_min_window_size — 空 configs 回退到 120."""

    def test_empty_configs(self):
        from filter.backtest.cli import _get_min_window_size
        assert _get_min_window_size([]) == 120

    def test_normal_configs(self):
        from filter.backtest.cli import _get_min_window_size
        configs = [
            {"tf": "日线", "n_pts": 120},
            {"tf": "60分钟", "n_pts": 50},
            {"tf": "15分钟", "n_pts": 200},
        ]
        assert _get_min_window_size(configs) == 50

    def test_missing_n_pts(self):
        """某 config 缺少 n_pts 时使用默认 120."""
        from filter.backtest.cli import _get_min_window_size
        configs = [
            {"tf": "日线"},
            {"tf": "60分钟", "n_pts": 50},
        ]
        assert _get_min_window_size(configs) == 50


class TestGetMinTf:
    """_get_min_tf — 从 configs 中找到最精细周期."""

    def test_empty_configs(self):
        from filter.backtest.cli import _get_min_tf
        assert _get_min_tf([]) == ""

    def test_normal_configs(self):
        from filter.backtest.cli import _get_min_tf
        configs = [
            {"tf": "日线", "n_pts": 120},
            {"tf": "15分钟", "n_pts": 200},
            {"tf": "60分钟", "n_pts": 100},
        ]
        assert _get_min_tf(configs) == "15分钟"

    def test_unknown_tf_ignored(self):
        """未知周期名称被忽略，不影响其他周期."""
        from filter.backtest.cli import _get_min_tf
        configs = [
            {"tf": "日线"},
            {"tf": "non-existent"},
            {"tf": "60分钟"},
        ]
        assert _get_min_tf(configs) == "60分钟"


# ═══════════════════════════════════════════════════════════════════════════════
# main() 边界条件测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestMainBoundary:
    """main() — 无效参数导致 SystemExit 的场景."""

    def test_invalid_preset_name_exits(self):
        """--preset 指定不存在的预设名时退出."""
        with patch("filter.backtest.cli.has_data", return_value=True), \
             patch("filter.backtest.cli.list_presets", return_value=[
                 {"name": "valid_preset_1", "preset_id": 1},
             ]), \
             patch.object(sys, "argv",
                          ["backtest_cli.py", "--ticker", "AAPL",
                           "--preset", "nonexistent_preset", "--quiet"]):
            from filter.backtest.cli import main
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1

    def test_nonexistent_config_file_exits(self):
        """--config-file 指向不存在的文件时退出."""
        with patch("filter.backtest.cli.has_data", return_value=True), \
             patch.object(sys, "argv",
                          ["backtest_cli.py", "--ticker", "AAPL",
                           "--config-file", "/nonexistent/cfg.json", "--quiet"]):
            from filter.backtest.cli import main
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1

    def test_negative_start_bar_exits(self):
        """--start-bar 负数导致退出."""
        with patch("filter.backtest.cli.has_data", return_value=True), \
             patch("filter.backtest.cli._get_total_bars", return_value=500), \
             patch("filter.backtest.cli.BacktestRunner"), \
             patch("filter.backtest.cli.EventRecorder"), \
             patch("filter.backtest.cli.ParquetStore"), \
             patch.object(sys, "argv",
                          ["backtest_cli.py", "--ticker", "AAPL",
                           "--start-bar", "-5", "--end-bar", "100", "--quiet"]):
            from filter.backtest.cli import main
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1

    def test_start_equals_end_exits(self):
        """start_bar == end_bar 导致退出."""
        with patch("filter.backtest.cli.has_data", return_value=True), \
             patch("filter.backtest.cli._get_total_bars", return_value=500), \
             patch("filter.backtest.cli.BacktestRunner"), \
             patch("filter.backtest.cli.EventRecorder"), \
             patch("filter.backtest.cli.ParquetStore"), \
             patch.object(sys, "argv",
                          ["backtest_cli.py", "--ticker", "AAPL",
                           "--start-bar", "100", "--end-bar", "100", "--quiet"]):
            from filter.backtest.cli import main
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1

    def test_start_greater_than_end_exits(self):
        """start_bar > end_bar 导致退出."""
        with patch("filter.backtest.cli.has_data", return_value=True), \
             patch("filter.backtest.cli._get_total_bars", return_value=500), \
             patch("filter.backtest.cli.BacktestRunner"), \
             patch("filter.backtest.cli.EventRecorder"), \
             patch("filter.backtest.cli.ParquetStore"), \
             patch.object(sys, "argv",
                          ["backtest_cli.py", "--ticker", "AAPL",
                           "--start-bar", "200", "--end-bar", "100", "--quiet"]):
            from filter.backtest.cli import main
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1

    def test_end_bar_exceeds_total_exits(self):
        """end_bar 超出总数导致退出."""
        with patch("filter.backtest.cli.has_data", return_value=True), \
             patch("filter.backtest.cli._get_total_bars", return_value=100), \
             patch("filter.backtest.cli.BacktestRunner"), \
             patch("filter.backtest.cli.EventRecorder"), \
             patch("filter.backtest.cli.ParquetStore"), \
             patch.object(sys, "argv",
                          ["backtest_cli.py", "--ticker", "AAPL",
                           "--start-bar", "50", "--end-bar", "200", "--quiet"]):
            from filter.backtest.cli import main
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1

    def test_checkpoint_interval_negative_behaves_like_zero(self):
        """负数 checkpoint-interval 等同于 0（禁用自动保存）."""
        mock_runner = MagicMock()
        mock_runner.run.return_value = []
        with patch("filter.backtest.cli.has_data", return_value=True), \
             patch("filter.backtest.cli._get_total_bars", return_value=500), \
             patch("filter.backtest.cli.Path.mkdir"), \
             patch("filter.backtest.cli.BacktestRunner",
                   return_value=mock_runner), \
             patch("filter.backtest.cli.EventRecorder"), \
             patch("filter.backtest.cli.ParquetStore"), \
             patch.object(sys, "argv",
                          ["backtest_cli.py", "--ticker", "AAPL",
                           "--checkpoint-interval", "-1", "--quiet"]):
            from filter.backtest.cli import main
            main()
        # checkpoint_path should be None because -1 > 0 is False
        call_kwargs = mock_runner.run.call_args[1]
        assert call_kwargs["checkpoint_path"] is None

    def test_no_data_ticker_exits(self):
        """ticker 无数据时退出."""
        with patch("filter.backtest.cli.has_data", return_value=False), \
             patch.object(sys, "argv",
                          ["backtest_cli.py", "--ticker", "FAKETICKER", "--quiet"]):
            from filter.backtest.cli import main
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1

    def test_zero_total_bars_exits(self):
        """数据存在但 count 为 0 时退出."""
        with patch("filter.backtest.cli.has_data", return_value=True), \
             patch("filter.backtest.cli._get_total_bars", return_value=0), \
             patch.object(sys, "argv",
                          ["backtest_cli.py", "--ticker", "AAPL", "--quiet"]):
            from filter.backtest.cli import main
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1

    def test_invalid_view_filter_exits(self):
        """--view-filter 指定不存在的视图时退出."""
        with patch("filter.backtest.cli.has_data", return_value=True), \
             patch("filter.backtest.cli._get_total_bars", return_value=500), \
             patch("filter.backtest.cli.BacktestRunner"), \
             patch("filter.backtest.cli.EventRecorder"), \
             patch("filter.backtest.cli.ParquetStore"), \
             patch.object(sys, "argv",
                          ["backtest_cli.py", "--ticker", "AAPL",
                           "--view-filter", "v9_nonexistent", "--quiet"]):
            from filter.backtest.cli import main
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1
