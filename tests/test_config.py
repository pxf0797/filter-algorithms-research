"""Test filter.config — ViewConfig dataclass creation, serialization, defaults."""

import json
import pytest
from unittest.mock import patch, MagicMock
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


class TestViewConfigDictAccessRemoved:
    """验证 __getitem__ / get() 已移除，仅保留属性式访问."""

    def test_getitem_raises_typeerror(self):
        """字典式访问 cfg["key"] 应抛出 TypeError（不是 KeyError）。"""
        cfg = ViewConfig(tf="日线", ke=0.25)
        with pytest.raises(TypeError):
            _ = cfg["tf"]

    def test_get_method_raises_typeerror(self):
        """cfg.get("key") 应不存在（AttributeError）。"""
        cfg = ViewConfig(tf="60分钟")
        with pytest.raises(AttributeError):
            _ = cfg.get("tf")

    def test_all_public_attributes_accessible(self):
        """验证所有公开属性可通过 cfg.field 正常访问."""
        cfg = ViewConfig()
        # 公开属性完整列表（与 to_dict() 键对齐）
        public_attrs = [
            "tf", "n_pts", "pv", "pv2",
            "show_sch", "ke", "sm", "ew",
            "show_pred", "fit_mode", "n_ext",
            "show_strategy", "stop_loss_pct",
            "fc", "fc2",
            "show_cross_pnl", "show_alignment", "show_pnl_feedback",
        ]
        for attr in public_attrs:
            assert hasattr(cfg, attr), f"公开属性 '{attr}' 缺失"
            # 访问不抛异常即为正常
            _ = getattr(cfg, attr)


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


# ===================================================================
# requirements 拆分验证 (M20)
# ===================================================================


class TestRequirementsSplit:
    """验证 requirements.txt 和 requirements-dev.txt 的拆分正确性."""

    @pytest.fixture(scope="class")
    def project_root(self):
        from pathlib import Path
        return Path(__file__).resolve().parent.parent

    @pytest.fixture(scope="class")
    def prod_path(self, project_root):
        return project_root / "requirements.txt"

    @pytest.fixture(scope="class")
    def dev_path(self, project_root):
        return project_root / "requirements-dev.txt"

    # ── 文件存在性 ──────────────────────────────────────────

    def test_requirements_txt_exists(self, prod_path):
        """requirements.txt 文件存在."""
        assert prod_path.exists(), f"文件不存在: {prod_path}"

    def test_requirements_dev_txt_exists(self, dev_path):
        """requirements-dev.txt 文件存在."""
        assert dev_path.exists(), f"文件不存在: {dev_path}"

    # ── requirements-dev.txt 继承关系 ───────────────────────

    def test_dev_inherits_prod(self, dev_path):
        """requirements-dev.txt 首行是 '-r requirements.txt'."""
        with open(dev_path) as fp:
            first_line = fp.readline().strip()
        assert first_line == "-r requirements.txt", (
            f"requirements-dev.txt 首行应为 '-r requirements.txt'，"
            f"实际为: '{first_line}'"
        )

    # ── 生产依赖是开发依赖的子集 ────────────────────────────

    @staticmethod
    def _parse_packages(path):
        """解析 requirements 文件，返回 {(包名, 版本说明), ...}."""
        import re
        packages = set()
        with open(path) as fp:
            for line in fp:
                line = line.strip()
                # 跳过空行、注释、-r 引用
                if not line or line.startswith("#") or line.startswith("-r"):
                    continue
                # 匹配 package>=version, package==version, package~=version
                m = re.match(r'^([a-zA-Z0-9_.-]+)\s*([><=!~]+\s*[^;]+)', line)
                if m:
                    packages.add(m.group(1).strip().lower())
        return packages

    def test_prod_is_subset_of_dev(self, prod_path, dev_path):
        """生产依赖包名是开发依赖包名的子集.

        注：requirements-dev.txt 通过 -r requirements.txt 继承生产依赖，
        因此 dev 文件不需要重复列出生产包名；但为完整性验证 dev 文件内
        不会声明与生产包冲突的版本.
        """
        import re
        prod_pkgs = self._parse_packages(prod_path)

        # 解析 dev 文件（跳过 -r 行）
        dev_pkgs = set()
        with open(dev_path) as fp:
            for line in fp:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-r"):
                    continue
                m = re.match(r'^([a-zA-Z0-9_.-]+)\s*([><=!~]+\s*[^;]+)', line)
                if m:
                    dev_pkgs.add(m.group(1).strip().lower())

        # 生产包不应与 dev 包有版本冲突（不在 dev 中显式声明）
        conflicts = prod_pkgs & dev_pkgs
        assert len(conflicts) == 0, (
            f"以下生产包不应在 requirements-dev.txt 中重复声明: "
            f"{', '.join(sorted(conflicts))}"
        )

    # ── 关键生产依赖存在性 ──────────────────────────────────

    def test_streamlit_in_prod(self, prod_path):
        """生产依赖中包含 streamlit."""
        pkgs = self._parse_packages(prod_path)
        assert "streamlit" in pkgs, "streamlit 应在 requirements.txt 中"

    def test_pip_audit_not_in_prod(self, prod_path):
        """生产依赖中不含 pip-audit（已移至 dev）."""
        pkgs = self._parse_packages(prod_path)
        assert "pip-audit" not in pkgs, (
            "pip-audit 不应在 requirements.txt 中"
        )

    def test_pip_audit_in_dev(self, dev_path):
        """开发依赖中包含 pip-audit."""
        pkgs = self._parse_packages(dev_path)
        assert "pip-audit" in pkgs, "pip-audit 应在 requirements-dev.txt 中"


class TestRenderExportConfig:
    """_render_export_config 测试."""

    def test_export_data_structure(self):
        """导出数据结构正确."""
        from filter.browse.sidebar import _render_export_config
        from filter.engine.filters import FILTERS
        configs = [{
            "tf": "日线", "_fid": "sma", "_dual": False,
            "show_sch": True, "show_strategy": True, "show_pred": True,
            "show_cross_pnl": False, "show_alignment": False,
            "show_pnl_feedback": False,
            "n_pts": 120, "ew": 60, "ke": 0.15, "sm": 0.05,
            "n_ext": 10, "stop_loss_pct": 2.0, "fit_mode": "linear",
            "pv": {"window": 11}, "pv2": {}, "fc": "#00d4aa", "fc2": "#ff6b6b",
        }]

        with patch("filter.browse.sidebar.st.sidebar.download_button") as mock_download, \
             patch("filter.browse.sidebar.st.sidebar.markdown"), \
             patch("filter.browse.sidebar.FILTERS", FILTERS):
            _render_export_config(configs, "sma", None, False, "美股", "AAPL")

        assert mock_download.called
        args = mock_download.call_args[0]
        export_json = args[1]
        export_data = json.loads(export_json)
        assert export_data["market"] == "美股"
        assert export_data["ticker"] == "AAPL"
        assert export_data["global_f"] == "sma"


class TestRenderConfigHistory:
    """_render_config_history 测试."""

    def test_no_ticker_skips_rendering(self):
        """无 ticker 时跳过渲染."""
        from filter.browse.sidebar import _render_config_history
        with patch("filter.browse.sidebar.st") as mock_st:
            _render_config_history("")
            # expander 不应该被调用
            mock_st.sidebar.expander.assert_not_called()

    def test_with_records_displays_items(self):
        """有历史记录时正确显示."""
        from filter.browse.sidebar import _render_config_history
        records = [
            {"changed_at": "2026-01-15 10:00", "source": "ui", "preset_name": "MyPreset"},
            {"changed_at": "2026-01-14 10:00", "source": "import", "preset_name": None},
        ]

        mock_expander = MagicMock()
        mock_exp_ctxt = MagicMock()
        mock_expander.return_value.__enter__.return_value = mock_exp_ctxt

        with patch("filter.browse.sidebar.st") as mock_st:
            mock_st.sidebar = MagicMock()
            mock_st.sidebar.markdown = MagicMock()
            mock_st.sidebar.expander = mock_expander
            mock_st.sidebar.caption = MagicMock()
            mock_st.caption = MagicMock()
            with patch("filter.browse.sidebar.get_history", return_value=records):
                _render_config_history("AAPL")

            # 至少调用了 caption
            caption_calls = (
                mock_st.sidebar.caption.call_args_list +
                mock_st.caption.call_args_list
            )
            assert len(caption_calls) >= 2  # 至少2条记录

    def test_empty_records_shows_default(self):
        """无历史记录时显示默认提示."""
        from filter.browse.sidebar import _render_config_history

        mock_expander = MagicMock()
        mock_exp_ctxt = MagicMock()
        mock_expander.return_value.__enter__.return_value = mock_exp_ctxt

        with patch("filter.browse.sidebar.st") as mock_st:
            mock_st.sidebar = MagicMock()
            mock_st.sidebar.markdown = MagicMock()
            mock_st.sidebar.expander = mock_expander
            mock_st.sidebar.caption = MagicMock()
            mock_st.caption = MagicMock()
            with patch("filter.browse.sidebar.get_history", return_value=[]):
                _render_config_history("AAPL")

            # 验证至少渲染了 "暂无记录"
            caption_calls = (
                list(mock_st.sidebar.caption.call_args_list) +
                list(mock_st.caption.call_args_list)
            )
            caption_texts = [str(c[0][0]) for c in caption_calls if c[0]]
            assert any("暂无记录" in t for t in caption_texts)
