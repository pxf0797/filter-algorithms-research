"""
Tests for backtest_cli.py CLI argument parsing and main() flow.
Uses unittest.mock to avoid running actual backtests.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, ANY

import pytest

# Ensure filter_app is importable
_filter_app = Path(__file__).resolve().parents[2] / "filter_app"
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
