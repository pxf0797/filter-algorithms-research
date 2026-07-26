"""回测回归测试：filtered 值应随时间变化"""

import subprocess
import glob
import os
import pandas as pd

PROJECT_ROOT = "/Users/xfpan/claude/filter_research"


def test_backtest_filtered_values_vary():
    """回测中filtered值应随bar变化，不应恒常。

    验证 _sync_data 在 bar 循环内逐 bar 调用后，粗TF（日线/60分钟/周线）
    的部分K线随每个bar的 cutoff_date 变化，反映在 filtered 值上。
    """
    result = subprocess.run(
        [
            "python3", "-m", "filter.backtest.cli",
            "--ticker", "3690", "--preset", "3690_HK",
            "--start-bar", "50", "--end-bar", "80",
        ],
        capture_output=True, text=True,
        cwd=PROJECT_ROOT,
    )
    assert result.returncode == 0, f"回测CLI失败: {result.stderr[-500:]}"

    dirs = sorted(
        glob.glob(os.path.join(PROJECT_ROOT, "backtest_output", "3690_*")),
        reverse=True,
    )
    assert dirs, "未找到回测输出目录"

    df = pd.read_parquet(os.path.join(dirs[0], "backtest_result.parquet"))
    for col in ["v0_filtered", "v1_filtered", "v2_filtered", "v3_filtered"]:
        if col in df.columns:
            vals = df[col].dropna()
            n = len(vals)
            unique = vals.nunique()
            assert unique >= n * 0.3, (
                f"{col} 在 {n} 个bar中只有 {unique} 个唯一值 "
                f"（至少需要 {int(n * 0.3)}）— 级联合成可能未逐bar执行"
            )
