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


# ===================================================================
# pyproject.toml 包安装验证
# ===================================================================


class TestPackageImports:
    """验证 pip install 后 filter 子包可正常导入."""

    def test_filter_constants_importable(self):
        """filter.constants 模块可导入."""
        import filter.constants as _fc
        assert _fc is not None

    def test_filter_common_importable(self):
        """filter.common 模块可导入."""
        import filter.common as _fcm
        assert _fcm is not None

    def test_filter_common_pnl_renderer_importable(self):
        """filter.common.pnl_renderer 子模块可导入."""
        from filter.common.pnl_renderer import (
            compute_combined_pnl, compute_drawdown,
        )
        assert callable(compute_combined_pnl)
        assert callable(compute_drawdown)

    def test_filter_constants_colors_importable(self):
        """filter.constants.colors 子模块可导入."""
        from filter.constants.colors import COLORS
        assert isinstance(COLORS, dict)
        assert len(COLORS) > 0


class TestVersionConsistency:
    """验证 filter.__version__ 与 pyproject.toml 一致."""

    def test_version_matches_pyproject(self):
        """filter.__version__ 与 pyproject.toml 中的版本一致."""
        import tomllib
        from pathlib import Path
        from filter import __version__ as pkg_version

        project_root = Path(__file__).resolve().parent.parent
        pyproject_path = project_root / "pyproject.toml"
        with open(pyproject_path, "rb") as fp:
            pyproject_data = tomllib.load(fp)

        toml_version = pyproject_data["project"]["version"]
        assert pkg_version == toml_version, (
            f"filter.__version__ ({pkg_version}) 与 pyproject.toml 版本 "
            f"({toml_version}) 不一致"
        )

    def test_version_is_semver(self):
        """版本号符合 semver 格式."""
        import re
        from filter import __version__
        assert re.match(r"^\d+\.\d+\.\d+$", __version__), (
            f"版本号 '{__version__}' 不符合 MAJOR.MINOR.PATCH 格式"
        )


# ===================================================================
# pyproject.toml [build-system] 配置验证
# ===================================================================


class TestBuildSystemConfig:
    """验证 pyproject.toml 的 [build-system] 配置."""

    def test_build_system_config(self):
        """pyproject.toml 的 [build-system] 包含正确的 requires 和 build-backend."""
        import tomllib
        from pathlib import Path

        project_root = Path(__file__).resolve().parent.parent
        pyproject_path = project_root / "pyproject.toml"
        with open(pyproject_path, "rb") as fp:
            cfg = tomllib.load(fp)

        bs = cfg["build-system"]
        assert any("setuptools>=61.0" in req for req in bs["requires"]), (
            f"build-system.requires 应包含 'setuptools>=61.0'，实际为: {bs['requires']}"
        )
        assert bs["build-backend"] == "setuptools.build_meta", (
            f"build-backend 应为 'setuptools.build_meta'，实际为: {bs['build-backend']}"
        )


# ===================================================================
# docker-compose.yml 验证
# ===================================================================


class TestDockerCompose:
    """验证 docker-compose.yml 格式与服务定义."""

    @pytest.fixture(scope="class")
    def compose_data(self):
        import yaml
        from pathlib import Path
        compose_path = Path(__file__).resolve().parent.parent / "docker-compose.yml"
        with open(compose_path) as fp:
            return yaml.safe_load(fp)

    def test_yaml_parses(self, compose_data):
        """docker-compose.yml 可被 YAML 解析器正确解析."""
        assert compose_data is not None

    def test_services_key_exists(self, compose_data):
        """顶层 services 键存在."""
        assert "services" in compose_data

    def test_streamlit_service_defined(self, compose_data):
        """streamlit 服务已定义."""
        assert "streamlit" in compose_data["services"]

    def test_streamlit_has_restart_policy(self, compose_data):
        """streamlit 服务的 restart 策略为 unless-stopped."""
        svc = compose_data["services"]["streamlit"]
        assert svc.get("restart") == "unless-stopped"

    def test_streamlit_has_healthcheck(self, compose_data):
        """streamlit 服务定义了 healthcheck."""
        svc = compose_data["services"]["streamlit"]
        assert "healthcheck" in svc

    def test_streamlit_healthcheck_has_test(self, compose_data):
        """streamlit 健康检查包含 test 命令."""
        hc = compose_data["services"]["streamlit"]["healthcheck"]
        assert "test" in hc
        assert len(hc["test"]) > 0

    def test_streamlit_healthcheck_has_retries(self, compose_data):
        """streamlit 健康检查配置了重试次数."""
        hc = compose_data["services"]["streamlit"]["healthcheck"]
        assert "retries" in hc
        assert hc["retries"] >= 1

    def test_no_version_field(self, compose_data):
        """Docker Compose V2 不应包含已废弃的 version 字段."""
        assert "version" not in compose_data
