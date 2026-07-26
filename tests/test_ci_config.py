"""
tests/test_ci_config.py — CI pipeline configuration validation tests.

Validates:
1. ci.yml is valid YAML
2. snapshot-tests job exists in ci.yml
3. Makefile has test-snapshots target
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_ci_yml_is_valid_yaml():
    """ci.yml should be parseable YAML."""
    ci_path = ROOT / ".github" / "workflows" / "ci.yml"
    assert ci_path.exists(), f"ci.yml not found at {ci_path}"

    with open(ci_path) as f:
        data = yaml.safe_load(f)

    assert isinstance(data, dict), "ci.yml should parse to a dict"
    assert "jobs" in data, "ci.yml should have a 'jobs' key"


def test_snapshot_tests_job_exists():
    """ci.yml should contain a snapshot-tests job."""
    ci_path = ROOT / ".github" / "workflows" / "ci.yml"
    with open(ci_path) as f:
        data = yaml.safe_load(f)

    jobs = data.get("jobs", {})
    assert "snapshot-tests" in jobs, (
        "ci.yml missing 'snapshot-tests' job"
    )

    job = jobs["snapshot-tests"]
    assert job.get("runs-on") == "ubuntu-latest", (
        "snapshot-tests should run on ubuntu-latest"
    )

    steps = job.get("steps", [])
    assert len(steps) > 0, "snapshot-tests should have steps"


def test_snapshot_tests_step_uses_pytest():
    """snapshot-tests job should run pytest on test_snapshots.py."""
    ci_path = ROOT / ".github" / "workflows" / "ci.yml"
    with open(ci_path) as f:
        data = yaml.safe_load(f)

    steps = data["jobs"]["snapshot-tests"]["steps"]
    pytest_step = None
    for step in steps:
        run_cmd = step.get("run", "")
        if "test_snapshots.py" in run_cmd:
            pytest_step = run_cmd
            break

    assert pytest_step is not None, (
        "snapshot-tests should have a step running test_snapshots.py"
    )
    # CI should use strict snapshot comparison (no --snapshot-update)
    assert "--snapshot-update" not in pytest_step, (
        "snapshot-tests CI step must NOT use --snapshot-update; "
        "snapshots should be strictly compared"
    )


def test_makefile_has_test_snapshots_target():
    """Makefile should contain a test-snapshots target."""
    makefile_path = ROOT / "Makefile"
    assert makefile_path.exists(), f"Makefile not found at {makefile_path}"

    with open(makefile_path) as f:
        content = f.read()

    # Match a target line like "test-snapshots:"
    assert re.search(r"^test-snapshots:", content, re.MULTILINE), (
        "Makefile missing 'test-snapshots' target"
    )


def test_makefile_has_snapshot_update_target():
    """Makefile should contain a snapshot-update target."""
    makefile_path = ROOT / "Makefile"
    with open(makefile_path) as f:
        content = f.read()

    assert re.search(r"^snapshot-update:", content, re.MULTILINE), (
        "Makefile missing 'snapshot-update' target"
    )


def test_makefile_snapshot_update_uses_update_flag():
    """snapshot-update target should use --snapshot-update flag."""
    makefile_path = ROOT / "Makefile"
    with open(makefile_path) as f:
        content = f.read()

    # Extract the snapshot-update target recipe
    match = re.search(
        r"^snapshot-update:\n(.+?)(?=\n\S|\Z)",
        content, re.MULTILINE | re.DOTALL
    )
    assert match is not None, "snapshot-update target not found"

    recipe = match.group(1)
    assert "--snapshot-update" in recipe, (
        "snapshot-update target must use --snapshot-update flag"
    )


def test_precommit_mypy_rev_valid():
    """.pre-commit-config.yaml 中 mypy rev 应为有效版本号 (e.g. v2.1.0)。"""
    precommit_path = ROOT / ".pre-commit-config.yaml"
    assert precommit_path.exists(), f".pre-commit-config.yaml not found at {precommit_path}"

    with open(precommit_path) as f:
        data = yaml.safe_load(f)

    mypy_rev = None
    for repo in data.get("repos", []):
        if "mirrors-mypy" in repo.get("repo", ""):
            mypy_rev = repo.get("rev", "")
            break

    assert mypy_rev is not None, (
        "mirrors-mypy repo not found in .pre-commit-config.yaml"
    )
    assert re.match(r"^v\d+\.\d+\.\d+$", mypy_rev), (
        f"mypy rev '{mypy_rev}' should match v<major>.<minor>.<patch> format"
    )


def test_ci_has_type_check_job():
    """CI workflow 应包含 type-check job 运行 mypy 检查。"""
    ci_path = ROOT / ".github" / "workflows" / "ci.yml"
    with open(ci_path) as f:
        data = yaml.safe_load(f)

    jobs = data.get("jobs", {})
    assert "type-check" in jobs, (
        "ci.yml missing 'type-check' job"
    )

    job = jobs["type-check"]
    assert job.get("runs-on") == "ubuntu-latest", (
        "type-check should run on ubuntu-latest"
    )

    steps = job.get("steps", [])
    assert len(steps) > 0, "type-check should have steps"

    # At least one step should run mypy
    mypy_cmd_found = False
    for step in steps:
        run_cmd = step.get("run", "")
        if "mypy" in run_cmd:
            mypy_cmd_found = True
            break

    assert mypy_cmd_found, (
        "type-check job should have a step running 'mypy'"
    )


def test_no_stale_streamlit_app_paths():
    """Dockerfile/Makefile/README 中不应存在过时的 streamlit_app.py 路径。"""
    files_to_check = {
        "Dockerfile": ROOT / "Dockerfile",
        "Makefile": ROOT / "Makefile",
        "README.md": ROOT / "README.md",
    }

    for name, path in files_to_check.items():
        assert path.exists(), f"{name} not found at {path}"
        content = path.read_text()
        assert "streamlit_app.py" not in content, (
            f"{name} contains stale reference to 'streamlit_app.py'. "
            f"Should use 'browse/app.py' instead."
        )
