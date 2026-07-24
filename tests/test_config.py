"""Test filter.config — ViewConfig dataclass creation, serialization, defaults."""

import pytest
from shared.config import ViewConfig


class TestViewConfigDefaults:
    def test_default_tf(self):
        cfg = ViewConfig()
        assert cfg.tf == "日线"

    def test_default_n_pts(self):
        cfg = ViewConfig()
        assert cfg.n_pts == 120

    def test_default_ke(self):
        cfg = ViewConfig()
        assert cfg.ke == 0.15

    def test_default_sm(self):
        cfg = ViewConfig()
        assert cfg.sm == 0.05

    def test_default_ew(self):
        cfg = ViewConfig()
        assert cfg.ew == 60

    def test_default_fit_mode(self):
        cfg = ViewConfig()
        assert cfg.fit_mode == "parabola"

    def test_default_n_ext(self):
        cfg = ViewConfig()
        assert cfg.n_ext == 8

    def test_default_stop_loss_pct(self):
        cfg = ViewConfig()
        assert cfg.stop_loss_pct == 2.0

    def test_default_fc(self):
        cfg = ViewConfig()
        assert cfg.fc == "#00d4aa"

    def test_default_fc2(self):
        cfg = ViewConfig()
        assert cfg.fc2 == "#ff6b6b"

    def test_default_show_sch(self):
        cfg = ViewConfig()
        assert cfg.show_sch is True

    def test_default_show_pred(self):
        cfg = ViewConfig()
        assert cfg.show_pred is True

    def test_default_show_strategy(self):
        cfg = ViewConfig()
        assert cfg.show_strategy is False

    def test_default_show_cross_pnl(self):
        cfg = ViewConfig()
        assert cfg.show_cross_pnl is False

    def test_default_show_alignment(self):
        cfg = ViewConfig()
        assert cfg.show_alignment is False

    def test_default_show_pnl_feedback(self):
        cfg = ViewConfig()
        assert cfg.show_pnl_feedback is False

    def test_default_pv_is_empty_dict(self):
        cfg = ViewConfig()
        assert cfg.pv == {}

    def test_default_pv2_is_empty_dict(self):
        cfg = ViewConfig()
        assert cfg.pv2 == {}

    def test_default_fid_is_empty_string(self):
        cfg = ViewConfig()
        assert cfg._fid == ""

    def test_default_dual_is_false(self):
        cfg = ViewConfig()
        assert cfg._dual is False

    def test_default_fid2_is_none(self):
        cfg = ViewConfig()
        assert cfg._fid2 is None


class TestViewConfigSerialization:
    def test_to_dict_roundtrip(self):
        """to_dict() → from_dict() should produce an equal ViewConfig."""
        original = ViewConfig(
            tf="60分钟", n_pts=200, _fid="ema", pv={"span": 26},
            _dual=True, _fid2="sma", pv2={"window": 11},
            show_sch=False, ke=0.3, sm=0.1, ew=30,
            show_pred=False, fit_mode="linear", n_ext=12,
            show_strategy=True, stop_loss_pct=5.0,
            fc="#ff0000", fc2="#00ff00",
            show_cross_pnl=True, show_alignment=True, show_pnl_feedback=True,
        )
        restored = ViewConfig.from_dict(original.to_dict())
        assert restored == original

    def test_to_dict_has_all_fields(self):
        cfg = ViewConfig()
        d = cfg.to_dict()
        expected_fields = [
            "tf", "n_pts", "_fid", "pv", "_dual", "_fid2", "pv2",
            "show_sch", "ke", "sm", "ew", "show_pred", "fit_mode",
            "n_ext", "show_strategy", "stop_loss_pct", "fc", "fc2",
            "show_cross_pnl", "show_alignment", "show_pnl_feedback",
        ]
        for field in expected_fields:
            assert field in d, f"Field '{field}' missing from to_dict()"

    def test_from_dict_with_extra_keys(self):
        """from_dict() should ignore extra keys not in ViewConfig fields."""
        d = {"tf": "周线", "n_pts": 50, "extra_unknown_key": "should_be_ignored"}
        cfg = ViewConfig.from_dict(d)
        assert cfg.tf == "周线"
        assert cfg.n_pts == 50

    def test_from_dict_partial(self):
        """from_dict() with partial data should use defaults for missing fields."""
        cfg = ViewConfig.from_dict({"tf": "15分钟"})
        assert cfg.tf == "15分钟"
        assert cfg.n_pts == 120  # default
        assert cfg.ke == 0.15    # default


class TestViewConfigDictAccess:
    def test_getitem_access(self):
        cfg = ViewConfig(tf="日线", ke=0.25)
        assert cfg["tf"] == "日线"
        assert cfg["ke"] == 0.25

    def test_getitem_raises_keyerror(self):
        cfg = ViewConfig()
        with pytest.raises(KeyError):
            _ = cfg["nonexistent"]

    def test_get_method(self):
        cfg = ViewConfig(tf="60分钟")
        assert cfg.get("tf") == "60分钟"
        assert cfg.get("nonexistent") is None
        assert cfg.get("nonexistent", "fallback") == "fallback"
