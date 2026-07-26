"""CHANGELOG validation tests — verifies format, existence, and version coverage."""

import re
from pathlib import Path

import pytest
from filter import __version__


@pytest.fixture(scope="module")
def changelog_path():
    return Path(__file__).resolve().parent.parent / "CHANGELOG.md"


@pytest.fixture(scope="module")
def changelog_content(changelog_path):
    if not changelog_path.exists():
        pytest.fail("CHANGELOG.md does not exist")
    content = changelog_path.read_text(encoding="utf-8")
    if not content.strip():
        pytest.fail("CHANGELOG.md is empty")
    return content


def test_changelog_exists_and_nonempty(changelog_content):
    """Verify CHANGELOG.md exists and is non-empty (via fixture)."""
    assert len(changelog_content) > 0


def test_changelog_has_title(changelog_content):
    """Verify CHANGELOG.md starts with a level-1 heading."""
    lines = changelog_content.strip().split("\n")
    assert lines, "CHANGELOG.md is empty"
    assert re.match(r"^#\s+\S", lines[0]), (
        f"First line should be a level-1 heading (e.g. '# Changelog'), got: '{lines[0]}'"
    )


def test_changelog_has_version_sections(changelog_content):
    """Verify CHANGELOG.md contains at least one '## [version]' section header."""
    version_header = re.compile(r"^##\s+\[.+\]", re.MULTILINE)
    matches = version_header.findall(changelog_content)
    assert matches, (
        "CHANGELOG.md must contain at least one version section "
        "in the format '## [version]' (e.g. '## [v1.0.0]')"
    )


def test_current_version_in_changelog(changelog_content):
    """Verify the current package version (from filter.__version__) has a CHANGELOG entry."""
    # CHANGELOG entries follow the convention: ## [vX.Y.Z]
    version_entry = f"[v{__version__}]"
    assert version_entry in changelog_content, (
        f"Current version '{version_entry}' not found in CHANGELOG.md. "
        f"Run 'make changelog' to regenerate."
    )


def test_no_empty_sections(changelog_content):
    """Warn if version sections have no entries (headers only, no bullet points)."""
    sections = re.split(r"^##\s+\[.+\]", changelog_content, flags=re.MULTILINE)
    empty_sections = []
    # The first split segment is content before the first version header — skip it
    for i, section in enumerate(sections[1:], start=1):
        if not re.search(r"^\s*-\s+", section, re.MULTILINE):
            headers = re.findall(r"^##\s+\[.+\]", changelog_content, re.MULTILINE)
            if i - 1 < len(headers):
                empty_sections.append(headers[i - 1])

    if empty_sections:
        print(
            f"\n⚠  {len(empty_sections)} version section(s) have no entries: "
            f"{', '.join(empty_sections)}"
        )
        print("   (This is a warning only — not a hard failure.)")
