"""Test filter_app.constants — verify constant consistency and completeness."""

import pytest
from shared.constants import ALL_TFS, DEFAULT_TFS, TF_HIERARCHY, TF_INTERVAL


class TestAllTfs:
    def test_length_is_8(self):
        assert len(ALL_TFS) == 8, "ALL_TFS should contain exactly 8 timeframes"

    def test_order_fine_to_coarse(self):
        """Fine-grained TFs come first, coarse-grained later."""
        assert ALL_TFS[0] == "1分钟"
        assert ALL_TFS[-1] == "季线"

    def test_contains_expected_tfs(self):
        expected = {"1分钟", "5分钟", "15分钟", "60分钟", "日线", "周线", "月线", "季线"}
        assert set(ALL_TFS) == expected


class TestDefaultTfs:
    def test_length_is_4(self):
        assert len(DEFAULT_TFS) == 4

    def test_all_in_all_tfs(self):
        for tf in DEFAULT_TFS:
            assert tf in ALL_TFS, f"DEFAULT_TFS entry '{tf}' not in ALL_TFS"


class TestTfHierarchy:
    def test_all_keys_map_to_valid_tfs(self):
        for src, dst in TF_HIERARCHY.items():
            if dst is not None:
                assert dst in ALL_TFS, f"TF_HIERARCHY maps '{src}' → '{dst}', but '{dst}' not in ALL_TFS"

    def test_seasonal_maps_to_none(self):
        assert TF_HIERARCHY["季线"] is None, "季线 should map to None (no higher TF)"

    def test_chain_forms_total_order(self):
        """Following TF_HIERARCHY from finest to coarsest reaches 季线."""
        current = "1分钟"
        chain = [current]
        while True:
            nxt = TF_HIERARCHY[current]
            if nxt is None:
                break
            chain.append(nxt)
            current = nxt
        assert chain[-1] == "季线"
        assert len(chain) == 8


class TestTfInterval:
    def test_all_tfs_have_intervals(self):
        for tf in ALL_TFS:
            assert tf in TF_INTERVAL, f"TF '{tf}' missing from TF_INTERVAL"

    def test_each_interval_is_tuple_of_two_strings(self):
        for tf, (interval, period) in TF_INTERVAL.items():
            assert isinstance(interval, str), f"TF '{tf}': interval is not a string"
            assert isinstance(period, str), f"TF '{tf}': period is not a string"

    def test_daily_and_above_use_max_period(self):
        for tf in ("日线", "周线", "月线", "季线"):
            _, period = TF_INTERVAL[tf]
            assert period == "max", f"TF '{tf}' should use period='max', got '{period}'"


class TestImportConsistency:
    def test_constants_importable(self):
        """Verify all constants are importable from their primary module."""
        from shared.constants import ALL_TFS as c1, DEFAULT_TFS as c2, TF_HIERARCHY as c3, TF_INTERVAL as c4
        assert len(c1) == 8

    def test_sidebar_re_exports_all_tfs(self):
        """sidebar.py should re-export ALL_TFS from constants."""
        from components.sidebar import ALL_TFS as S_ALL_TFS
        assert S_ALL_TFS == ALL_TFS

    def test_backtest_core_re_exports_all_tfs(self):
        """backtest_core.py should import ALL_TFS from constants."""
        from services.backtest_core import ALL_TFS as BC_ALL_TFS
        assert BC_ALL_TFS == ALL_TFS
