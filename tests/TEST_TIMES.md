# 测试耗时与CI策略

> 最后更新: 2026-06-28
> 测试环境: Python 3.12.6, macOS Darwin (Apple Silicon)
> 总测试数: 637 个测试用例 (625 个测试函数，含 parametrize 展开) / 20 个测试文件
> 总耗时: 约 2 分钟 (全量串行)

## 按文件耗时分级

### 快速 (<1s wall time) — 每次 commit 运行

| 文件 | 测试数 | wall time(s) | CPU time(s) |
|------|--------|-------------|-------------|
| test_param_export_import.py | 13 | 0.51 | 0.36 |
| test_state.py | 59 | 0.70 | 0.54 |
| test_integration_flows.py | 9 | 0.72 | 0.54 |
| test_preset_ui_actions.py | 45 | 0.73 | 0.40 |
| test_data_loader.py | 30 | 0.76 | 0.57 |
| test_config_db.py | 58 | 0.95 | 0.44 |
| test_alignment.py | 6 | 0.98 | 0.80 |

小计: **7 个文件 / 220 个测试 / 约 5.4s**

### 中等 (1-10s wall time) — 每次 PR 运行

| 文件 | 测试数 | wall time(s) | CPU time(s) |
|------|--------|-------------|-------------|
| test_filters.py | 22 | 1.04 | 0.84 |
| test_signals.py | 16 | 1.05 | 0.85 |
| test_alignment_subplot.py | 14 | 1.09 | 0.90 |
| test_sidebar.py | 36 | 1.12 | 0.88 |
| test_integration.py | 6 | 1.19 | 0.95 |
| test_strategy.py | 18 | 1.19 | 0.98 |
| test_boundary.py | 30 | 1.27 | 1.02 |
| test_streamlit_app.py | 25 | 1.53 | 1.22 |
| test_preset_ui.py | 60 | 1.76 | 0.47 |
| test_charts.py | 87 | 1.78 | 1.53 |
| test_db.py | 57 | 4.24 | 0.52 |
| test_app_smoke.py | 1 | 10.47 | 0.34 |

小计: **12 个文件 / 372 个测试 / 约 27.7s**

### 慢速 (>10s wall time) — merge 前 / nightly 运行

| 文件 | 测试数 | wall time(s) | CPU time(s) |
|------|--------|-------------|-------------|
| test_app_ui.py | 33 | **100.80** | 84.67 |

> test_app_ui.py 的 33 个测试占全量测试的 87% 时间。每个测试都 launch 完整的 Streamlit 应用，是主要的性能瓶颈。同时 test_app_smoke.py 的 1 个测试也 launch 了应用 (10.47s)，只是数量少。

小计: **1 个文件 / 33 个测试 / 约 100.8s**

## 最慢的 10 个单独测试 (call 阶段)

| 测试 | 耗时 | 原因 |
|------|------|------|
| test_app_ui::test_apply_preset_button_does_not_crash | 11.59s | Streamlit 完整启动 + UI 渲染 |
| test_app_smoke::test_app_launches | 10.01s | Streamlit 完整启动 |
| test_app_ui::test_ticker_change_does_not_crash | 9.49s | Streamlit 启动 |
| test_app_ui::test_refresh_button_click_does_not_crash | 8.12s | Streamlit 启动 |
| test_app_ui::test_refresh_button_clears_cache | 7.81s | Streamlit 启动 |
| test_app_ui::test_fresh_app_day_nav_buttons_stable | 6.81s | Streamlit 启动 |
| test_app_ui::test_multiple_fresh_apps_consistent | 6.72s | Streamlit 启动 |
| test_app_ui::test_fresh_app_no_unexpected_exception | 6.63s | Streamlit 启动 |
| test_app_ui::test_invalid_ticker_does_not_crash | 6.61s | Streamlit 启动 |
| test_app_ui::test_app_runs (setup) | 6.32s | Streamlit 启动 |

> **根因**: test_app_ui.py 和 test_app_smoke.py 使用 `subprocess.run` 启动完整的 Streamlit 应用进程，每个测试都要经历冷启动开销。优化方向: 使用 `session-scoped fixture` 复用应用进程，而非每测试启动。

## CI 分阶段建议

```yaml
# .github/workflows/ci.yml 建议策略
#
# 总串行时间: ~134s (2.2 min)
# 并行化后: ~107s (1.8 min) 或更快

jobs:
  fast-tests:                  # <6s, 每次 push 运行
    strategy:
      matrix:
        file:
          - test_param_export_import.py
          - test_state.py
          - test_integration_flows.py
          - test_preset_ui_actions.py
          - test_data_loader.py
          - test_config_db.py
          - test_alignment.py
    timeout-minutes: 1

  medium-tests:                # <30s, 每次 PR 运行 (可并行)
    strategy:
      matrix:
        file:
          - test_filters.py
          - test_signals.py
          - test_alignment_subplot.py
          - test_sidebar.py
          - test_integration.py
          - test_strategy.py
          - test_boundary.py
          - test_streamlit_app.py
          - test_preset_ui.py
          - test_charts.py
          - test_db.py
          - test_app_smoke.py
    timeout-minutes: 2

  slow-tests:                  # ~101s, merge 前 / nightly 运行
    strategy:
      matrix:
        file:
          - test_app_ui.py
    timeout-minutes: 5

  full-suite:                  # ~2min, main 分支定时运行
    needs: [fast-tests, medium-tests, slow-tests]
```

## 并行运行建议

- 使用 `pytest-xdist` 配合 `-n auto` 可将非 UI 测试加速约 2-4 倍 (Apple Silicon 性能核)
- **test_app_ui.py 和 test_app_smoke.py 必须独立进程运行** — 它们各自启动 Streamlit 子进程，多进程并行会导致端口冲突
- 建议增加 `--durations=0` 输出，便于在 CI 上持续监控性能退化

### 大致加速估算

| 策略 | 预估 wall time |
|------|---------------|
| 全量串行 | ~134s |
| 三阶段并行 (如上) | ~101s (受 slow-tests 阻塞) |
| 三阶段 + xdist medium 层 | ~60s |
| 全量 xdist -n 4 (排除 UI 文件) | ~35s |
