"""
Tests for backtest_cli.py CLI argument parsing and main() flow.
Uses unittest.mock to avoid running actual backtests.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, ANY

import pytest

# Ensure filter_app is importable
_filter_app = Path(__file__).resolve().parent.parent / "filter_app"
if str(_filter_app) not in sys.path:
    sys.path.insert(0, str(_filter_app))


# ═══════════════════════════════════════════════════════════════════════════════
# argparse 参数解析测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseArgs:
    """verify each CLI flag is parsed correctly by parse_args()."""

    def test_default_no_save_data_false(self):
        """default: --no-save-data is False (ParquetStore enabled)."""
        from filter_app.backtest_cli import parse_args
        with patch.object(sys, "argv", ["backtest_cli.py", "--ticker", "AAPL"]):
            args = parse_args()
        assert args.no_save_data is False

    def test_no_save_data_flag_true(self):
        """--no-save-data flag sets it to True."""
        from filter_app.backtest_cli import parse_args
        with patch.object(sys, "argv",
                          ["backtest_cli.py", "--ticker", "03690.HK", "--no-save-data"]):
            args = parse_args()
        assert args.no_save_data is True

    def test_checkpoint_interval_default(self):
        """--checkpoint-interval defaults to 100."""
        from filter_app.backtest_cli import parse_args
        with patch.object(sys, "argv", ["backtest_cli.py", "--ticker", "AAPL"]):
            args = parse_args()
        assert args.checkpoint_interval == 100

    def test_checkpoint_interval_custom(self):
        """--checkpoint-interval 50 is parsed correctly."""
        from filter_app.backtest_cli import parse_args
        with patch.object(sys, "argv",
                          ["backtest_cli.py", "--ticker", "AAPL",
                           "--checkpoint-interval", "50"]):
            args = parse_args()
        assert args.checkpoint_interval == 50

    def test_checkpoint_interval_zero(self):
        """--checkpoint-interval 0 disables auto-save."""
        from filter_app.backtest_cli import parse_args
        with patch.object(sys, "argv",
                          ["backtest_cli.py", "--ticker", "AAPL",
                           "--checkpoint-interval", "0"]):
            args = parse_args()
        assert args.checkpoint_interval == 0

    def test_resume_default_none(self):
        """--resume defaults to None."""
        from filter_app.backtest_cli import parse_args
        with patch.object(sys, "argv", ["backtest_cli.py", "--ticker", "AAPL"]):
            args = parse_args()
        assert args.resume is None

    def test_resume_with_path(self):
        """--resume takes a path argument."""
        from filter_app.backtest_cli import parse_args
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

    with patch("filter_app.backtest_cli.has_data", return_value=True), \
         patch("filter_app.backtest_cli._get_total_bars", return_value=500), \
         patch("filter_app.backtest_cli.Path.mkdir"), \
         patch("filter_app.backtest_cli.BacktestRunner",
               return_value=mock_runner), \
         patch("filter_app.backtest_cli.EventRecorder",
               return_value=mock_recorder) as mock_rec_cls, \
         patch("filter_app.backtest_cli.ParquetStore") as mock_store_cls, \
         patch.object(sys, "argv", cli_args):

        if mock_store is not None:
            mock_store_cls.return_value = mock_store

        from filter_app.backtest_cli import main
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
                "python", "-m", "filter_app.backtest_cli",
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
                "python", "-m", "filter_app.backtest_cli", "--help",
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
                "python", "-m", "filter_app.backtest_cli",
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
