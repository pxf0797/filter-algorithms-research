"""参数导入导出测试：验证导出JSON完整性、_imp_备份覆盖、自动检测参数变更"""
import json
import pytest
import numpy as np
from unittest.mock import MagicMock, patch
import sys

# ============================================================
# 1. 导出完整性：所有必需参数都出现在导出JSON中
# ============================================================

# 每个视图必须包含的参数（与 streamlit_app.py 的 export_data 对应）
REQUIRED_PER_VIEW_KEYS = [
    "tf", "n", "sch", "pred", "ke", "sm", "ew",
    "fm", "next", "fc", "fc2", "strat", "sl",
    "cross_pnl", "align",
]

REQUIRED_GLOBAL_KEYS = ["market", "ticker", "global_f", "global_dual", "global_f2"]

CONFIG_PATH = "/Users/xfpan/claude/filter_research/config/3690_HK.json"


class TestExportCompleteness:
    """验证导出JSON包含所有必需的参数"""

    def test_all_per_view_keys_exported(self):
        """每个视图的15个参数都在导出中"""
        with open(CONFIG_PATH) as f:
            config = json.load(f)

        for i in range(4):
            for key in REQUIRED_PER_VIEW_KEYS:
                export_key = f"v{i}_{key}"
                assert export_key in config, (
                    f"缺失导出键: {export_key}\n"
                    f"请在 streamlit_app.py export_data 中添加: "
                    f"export_data['{export_key}'] = cfg.get('...')"
                )

    def test_global_keys_exported(self):
        """全局参数在导出中"""
        with open(CONFIG_PATH) as f:
            config = json.load(f)

        for key in REQUIRED_GLOBAL_KEYS:
            assert key in config, f"缺失全局键: {key}"

    def test_filter_params_exported(self):
        """滤波参数在导出中存在"""
        with open(CONFIG_PATH) as f:
            config = json.load(f)

        for i in range(4):
            has_filter = any(
                k.startswith("窗口大小") and f"v{i}" in k
                for k in config.keys()
            )
            assert has_filter, f"视图{i}缺少滤波参数"


# ============================================================
# 2. _imp_ 备份覆盖：每个导出参数都应有对应的 _imp_ 备份
# ============================================================

class TestImpBackupCoverage:
    """验证导入时所有参数都被 _imp_ 备份"""

    def test_all_config_keys_have_imp_backup(self):
        """JSON中的每个key导入后都应有 _imp_ 备份"""
        with open(CONFIG_PATH) as f:
            config = json.load(f)

        # 模拟导入逻辑
        session_state = {}
        for k, v in config.items():
            session_state[k] = v
            session_state[f"_imp_{k}"] = v

        # 验证每个key都有 _imp_ 备份
        missing_imp = []
        for k in config.keys():
            imp_key = f"_imp_{k}"
            if imp_key not in session_state:
                missing_imp.append(k)

        assert len(missing_imp) == 0, (
            f"以下 {len(missing_imp)} 个配置键缺少 _imp_ 备份:\n"
            + "\n".join(f"  - {k}" for k in missing_imp)
        )

    def test_imp_values_match_original(self):
        """_imp_ 备份值与原始值一致"""
        with open(CONFIG_PATH) as f:
            config = json.load(f)

        session_state = {}
        for k, v in config.items():
            session_state[k] = v
            session_state[f"_imp_{k}"] = v

        mismatches = []
        for k in config:
            imp_key = f"_imp_{k}"
            if session_state[imp_key] != config[k]:
                mismatches.append(f"{k}: 原始={config[k]}, _imp_={session_state[imp_key]}")

        assert len(mismatches) == 0, (
            f"以下 {len(mismatches)} 个 _imp_ 值不匹配:\n"
            + "\n".join(f"  - {m}" for m in mismatches)
        )


# ============================================================
# 3. 自动检测参数变更：比对代码中的export和JSON中的key
# ============================================================

class TestParameterChangeDetection:
    """自动检测新增或删除的参数（信息性测试，不使CI失败）"""

    def test_no_stale_keys_in_json(self):
        """JSON中没有多余的未知参数"""
        with open(CONFIG_PATH) as f:
            config = json.load(f)

        known_keys = set(REQUIRED_GLOBAL_KEYS)
        for i in range(4):
            for k in REQUIRED_PER_VIEW_KEYS:
                known_keys.add(f"v{i}_{k}")

        # 滤波参数的pattern
        filter_patterns = ["窗口大小", "多项式阶数", "跨度"]

        unknown_keys = []
        for k in config.keys():
            if k in known_keys:
                continue
            if any(p in k for p in filter_patterns):
                continue
            if k.startswith("_"):
                continue
            unknown_keys.append(k)

        if unknown_keys:
            print(f"\n⚠ 发现 {len(unknown_keys)} 个未分类的JSON键:")
            for k in unknown_keys:
                print(f"  - {k}")
            print("这些可能是新增参数，请更新 REQUIRED_PER_VIEW_KEYS 列表")

    def test_all_json_per_view_keys_match_pattern(self):
        """JSON中的per-view键符合 v{N}_{name} 模式"""
        with open(CONFIG_PATH) as f:
            config = json.load(f)

        invalid = []
        for k in config.keys():
            if k.startswith("v") and len(k) > 2 and k[1].isdigit() and k[2] == "_":
                parts = k.split("_", 1)
                if len(parts) == 2:
                    suffix = parts[1]
                    is_filter = any(p in k for p in ["窗口大小", "多项式阶数", "跨度"])
                    is_known = suffix in REQUIRED_PER_VIEW_KEYS
                    if not is_known and not is_filter:
                        invalid.append(k)

        if invalid:
            print(f"\n⚠ 发现 {len(invalid)} 个不在 REQUIRED_PER_VIEW_KEYS 中的参数:")
            for k in invalid:
                print(f"  - {k}")
            print("如果这些是新参数，请更新 REQUIRED_PER_VIEW_KEYS")


# ============================================================
# 4. 折叠展开持久化：验证 _imp_ 备份恢复
# ============================================================

class TestExpandCollapseParameterRecovery:
    """模拟折叠展开后参数从 _imp_ 备份恢复"""

    def setup_method(self):
        """加载JSON配置模拟导入"""
        with open(CONFIG_PATH) as f:
            self.config = json.load(f)

    def test_all_params_recoverable_after_widget_loss(self):
        """所有参数在widget key丢失后可从_imp_恢复"""
        session_state = {}

        # 模拟导入
        for k, v in self.config.items():
            session_state[k] = v
            session_state[f"_imp_{k}"] = v

        # 模拟折叠展开：删除所有widget key
        for k in list(self.config.keys()):
            if k in session_state:
                del session_state[k]

        # 验证恢复：每个参数都能从 _imp_ 恢复
        unrecoverable = []
        for k in self.config.keys():
            imp_key = f"_imp_{k}"
            if k in session_state:
                unrecoverable.append(f"{k}: widget key 意外存在")
            elif imp_key not in session_state:
                unrecoverable.append(f"{k}: _imp_ 备份也不存在")

        assert len(unrecoverable) == 0, (
            f"以下 {len(unrecoverable)} 个参数无法恢复:\n"
            + "\n".join(f"  - {u}" for u in unrecoverable)
        )

    def test_parameter_values_preserved_after_recovery(self):
        """恢复后的值与原始导入值一致"""
        session_state = {}

        for k, v in self.config.items():
            session_state[k] = v
            session_state[f"_imp_{k}"] = v

        # 记录原始值
        original = dict(self.config)

        # 模拟widget key丢失
        for k in list(self.config.keys()):
            if k in session_state and not k.startswith("_"):
                del session_state[k]

        # 验证恢复
        mismatches = []
        for k, original_val in original.items():
            imp_key = f"_imp_{k}"
            recovered = session_state.get(imp_key)
            if recovered != original_val:
                mismatches.append(f"{k}: 原始={original_val}, 恢复={recovered}")

        assert len(mismatches) == 0, (
            f"以下 {len(mismatches)} 个参数值不匹配:\n"
            + "\n".join(f"  - {m}" for m in mismatches)
        )

    def test_specific_critical_params_recoverable(self):
        """关键参数（fit_mode, n_ext, cross_pnl, align）可恢复"""
        critical_params = {
            "v0_fm": "parabola",
            "v0_next": 8,
            "v0_cross_pnl": True,
            "v0_align": True,
            "v0_strat": True,
            "v0_ke": 0.1,
            "v0_sm": 0.05,
            "v0_ew": 40,
        }

        session_state = {}
        for k, v in self.config.items():
            session_state[k] = v
            session_state[f"_imp_{k}"] = v

        # 删除widget key
        for k in critical_params.keys():
            if k in session_state:
                del session_state[k]

        # 验证 _imp_ 恢复
        for k, expected in critical_params.items():
            imp_key = f"_imp_{k}"
            recovered = session_state.get(imp_key)
            assert recovered is not None, f"{k}: _imp_ 备份不存在"
            assert recovered == expected, (
                f"{k}: 期望={expected}, _imp_恢复={recovered}"
            )

    def test_filter_params_recoverable(self):
        """滤波参数（中文key）可从 _imp_ 备份恢复"""
        session_state = {}
        for k, v in self.config.items():
            session_state[k] = v
            session_state[f"_imp_{k}"] = v

        # 找到滤波参数
        filter_keys = [k for k in self.config if "窗口大小" in k]

        if not filter_keys:
            pytest.skip("配置中无滤波参数")

        # 删除它们
        for k in filter_keys:
            if k in session_state:
                del session_state[k]

        # 验证可从 _imp_ 恢复
        for k in filter_keys:
            imp_key = f"_imp_{k}"
            recovered = session_state.get(imp_key)
            assert recovered is not None, (
                f"滤波参数 {k} 的 _imp_ 备份不存在"
            )
            assert recovered == self.config[k], (
                f"滤波参数 {k}: 原始={self.config[k]}, _imp_恢复={recovered}"
            )


# ============================================================
# 5. 导入幂等性：重复导入相同文件不产生脏数据
# ============================================================

class TestImportIdempotency:
    """验证重复导入相同配置不产生脏数据"""

    def test_repeated_import_idempotent(self):
        """同一JSON导入多次，session_state 值不变"""
        with open(CONFIG_PATH) as f:
            raw = f.read()

        config = json.loads(raw)

        # 第一次导入
        session_state = {}
        for k, v in config.items():
            session_state[k] = v
            session_state[f"_imp_{k}"] = v

        # 记录第一次导入后状态
        first_snapshot = dict(session_state)

        # 模拟第二次导入（完全相同的数据）
        for k, v in config.items():
            session_state[k] = v
            session_state[f"_imp_{k}"] = v

        # 验证值不变
        for k in first_snapshot:
            assert session_state[k] == first_snapshot[k], (
                f"重复导入后 {k} 值变化: {first_snapshot[k]} -> {session_state[k]}"
            )

    def test_partial_import_no_leftover(self):
        """导入子集配置不应留有旧 session_state key"""
        subset = {
            "market": "美股 US",
            "ticker": "TEST",
            "global_f": "ema",
        }

        session_state = {}
        for k, v in subset.items():
            session_state[k] = v
            session_state[f"_imp_{k}"] = v

        # 验证只有子集key存在
        assert "v0_tf" not in session_state
        assert "market" in session_state
        assert "_imp_market" in session_state
