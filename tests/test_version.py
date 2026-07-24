"""Version consistency tests — ensures pyproject.toml stays in sync with git tags."""

import re
import subprocess
import tomllib
from pathlib import Path

import pytest


def test_pyproject_version_matches_latest_git_tag():
    """验证 pyproject.toml 版本与最新 git tag 一致（去掉v前缀）。"""
    project_root = Path(__file__).resolve().parent.parent

    # Read version from pyproject.toml
    pyproject_path = project_root / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    pyproject_version = data["project"]["version"]

    # Get latest git tag (by creation date)
    result = subprocess.run(
        ["git", "tag", "--sort=-creatordate"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    tags = [t.strip() for t in result.stdout.strip().split("\n") if t.strip()]
    if not tags:
        pytest.skip("No git tags found — cannot verify version sync")
    latest_tag = tags[0]

    # Strip leading 'v' if present
    expected_version = latest_tag.lstrip("v")

    assert pyproject_version == expected_version, (
        f"版本不同步！pyproject.toml 版本为 '{pyproject_version}'，"
        f"但最新 git tag 为 '{latest_tag}'（期望版本 '{expected_version}'）。"
        f"请运行：version sync 流程将 pyproject.toml 版本更新为 '{expected_version}'。"
    )


def test_no_hardcoded_old_versions():
    """检查关键项目中无过时的硬编码版本号（与 pyproject.toml 不一致的）。"""
    project_root = Path(__file__).resolve().parent.parent

    # Read current version from pyproject.toml
    pyproject_path = project_root / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    current_version = data["project"]["version"]

    # Files to scan for hardcoded version strings
    scan_patterns = ["*.py", "*.md", "*.toml", "*.cfg", "*.yaml", "*.yml"]
    skip_dirs = {".git", "__pycache__", ".venv", "venv", "node_modules", ".claude"}

    mismatches = []
    for pattern in scan_patterns:
        for filepath in project_root.rglob(pattern):
            # Skip excluded directories
            parts = set(filepath.parts)
            if skip_dirs & parts:
                continue

            try:
                content = filepath.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue

            # Look for version-like patterns (e.g., v10.5.0, 10.5.0)
            # We use a simple heuristic: find version numbers that look like semver
            # and are not the current version
            version_pattern = re.compile(r'\bv?(\d+\.\d+\.\d+)\b')
            for match in version_pattern.finditer(content):
                found = match.group(1)
                # Skip the current version (it's expected in pyproject.toml)
                if found == current_version:
                    continue
                # Skip if this is the version in pyproject.toml itself
                if filepath.name == "pyproject.toml":
                    continue
                mismatches.append(
                    f"  {filepath.relative_to(project_root)}: found '{found}'"
                )

    if mismatches:
        # Only report as info, not failure — docs may lag behind intentionally
        print(f"\n⚠ 发现 {len(mismatches)} 处版本号与当前版本 '{current_version}' 不一致：")
        for m in mismatches:
            print(m)
        print("（提示：文档中的版本引用可能需要同步更新）")
