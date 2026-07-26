#!/usr/bin/env python3
"""Check that test files' `from filter.xxx` imports resolve to real modules."""
import ast
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ERRORS: list[str] = []


def _check_module(name: str, test_file: Path) -> None:
    try:
        importlib.import_module(name)
    except ModuleNotFoundError:
        ERRORS.append(f"  {test_file.name}: '{name}' not found")
    except Exception as e:
        ERRORS.append(f"  {test_file.name}: '{name}' import error: {e}")


def main() -> None:
    for test_file in sorted((ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(test_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("filter"):
                        _check_module(alias.name, test_file)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("filter"):
                    _check_module(node.module, test_file)

    if ERRORS:
        print("Stale/broken imports found:")
        for e in ERRORS:
            print(e)
        sys.exit(1)

    print("All test imports valid")


if __name__ == "__main__":
    main()
