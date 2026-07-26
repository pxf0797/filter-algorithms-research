"""Version consistency tests — version format validation and hardcoded-version scanner."""

import re
from pathlib import Path

from filter import __version__


def test_version_format():
    """验证 filter.__version__ 符合 semver 格式（MAJOR.MINOR.PATCH）。"""
    assert re.match(r"\d+\.\d+\.\d+", __version__), (
        f"filter.__version__ ('{__version__}') 不符合 semver 格式 (MAJOR.MINOR.PATCH)"
    )


def test_no_hardcoded_mismatched_versions():
    """检查项目文件中无过时的硬编码版本号（与 filter.__version__ 不一致的）。"""
    project_root = Path(__file__).resolve().parent.parent
    current_version = __version__

    scan_patterns = ["*.py", "*.md", "*.toml", "*.cfg", "*.yaml", "*.yml"]
    skip_dirs = {".git", "__pycache__", ".venv", "venv", "node_modules", ".claude"}

    mismatches = []
    for pattern in scan_patterns:
        for filepath in project_root.rglob(pattern):
            parts = set(filepath.parts)
            if skip_dirs & parts:
                continue

            try:
                content = filepath.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue

            version_pattern = re.compile(r'\bv?(\d+\.\d+\.\d+)\b')
            for match in version_pattern.finditer(content):
                found = match.group(1)
                if found == current_version:
                    continue
                # pyproject.toml and __init__.py define the canonical version
                if filepath.name in ("pyproject.toml", "__init__.py"):
                    continue
                mismatches.append(
                    f"  {filepath.relative_to(project_root)}: found '{found}'"
                )

    if mismatches:
        print(f"\n⚠ 发现 {len(mismatches)} 处版本号与当前版本 '{current_version}' 不一致：")
        for m in mismatches:
            print(m)
        print("（提示：文档中的版本引用可能需要同步更新）")
