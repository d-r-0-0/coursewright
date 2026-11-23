"""Smoke tests: the package imports, layers hold, the CLI runs."""

from __future__ import annotations

import ast
from pathlib import Path

from typer.testing import CliRunner

import coursewright
from coursewright.ui.cli.main import app

PACKAGE_ROOT = Path(coursewright.__file__).parent

# Layer -> top-level coursewright subpackages it must never import.
FORBIDDEN_IMPORTS = {
    "core": {"ta", "design", "ui"},
    "ta": {"design", "ui"},
    "design": {"ta", "ui"},
}


def imported_coursewright_layers(source_path: Path) -> set[str]:
    """Return the coursewright subpackages a module imports."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.append(node.module)
    return {
        name.split(".")[1]
        for name in names
        if name.startswith("coursewright.") and len(name.split(".")) > 1
    }


def test_version_flag_prints_version() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert coursewright.__version__ in result.output


def test_bare_command_shows_both_halves() -> None:
    result = CliRunner().invoke(app, [])
    assert result.exit_code == 0
    assert "coursewright ta" in result.output
    assert "coursewright design" in result.output


def test_layers_do_not_import_upward_or_sideways() -> None:
    violations = []
    for layer, forbidden in FORBIDDEN_IMPORTS.items():
        for source_path in (PACKAGE_ROOT / layer).rglob("*.py"):
            crossed = imported_coursewright_layers(source_path) & forbidden
            if crossed:
                violations.append(f"{source_path}: imports {sorted(crossed)}")
    assert not violations, "\n".join(violations)
