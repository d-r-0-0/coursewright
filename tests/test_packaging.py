"""Packaging: every runtime data file under src/ ships in the wheel.

setuptools only copies non-Python files into a built wheel when
``[tool.setuptools.package-data]`` in ``pyproject.toml`` names them.
A file missing from that table still loads in a source checkout (and
in an editable install), so the omission only shows up after a real
``pip install`` -- the bundled OSCQR evidence mapping was lost this way.

These tests read ``pyproject.toml`` with ``tomllib`` and match the
declared globs against the files on disk, so they run offline and
without building anything.
"""

from __future__ import annotations

import tomllib
from importlib import resources
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
PACKAGE_ROOT = SOURCE_ROOT / "coursewright"

# Developer notes that sit beside code but are never loaded at runtime,
# so they deliberately stay out of the wheel.
DEVELOPER_ONLY_FILES = {
    "coursewright/design/media/PORTING.md",
    "coursewright/design/publishing/README.md",
}


def declared_package_data() -> dict[str, list[str]]:
    """Return the ``[tool.setuptools.package-data]`` table."""
    with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)
    return pyproject["tool"]["setuptools"]["package-data"]


def files_shipped_by_declaration() -> set[str]:
    """Return src-relative paths matched by the package-data globs."""
    shipped: set[str] = set()
    for package_name, patterns in declared_package_data().items():
        package_folder = SOURCE_ROOT.joinpath(*package_name.split("."))
        for pattern in patterns:
            for match in package_folder.glob(pattern):
                if match.is_file():
                    shipped.add(match.relative_to(SOURCE_ROOT).as_posix())
    return shipped


def runtime_data_files_on_disk() -> set[str]:
    """Return src-relative paths of every non-Python file in the package."""
    found: set[str] = set()
    for path in PACKAGE_ROOT.rglob("*"):
        if not path.is_file() or path.suffix in {".py", ".pyc"}:
            continue
        if "__pycache__" in path.parts:
            continue
        found.add(path.relative_to(SOURCE_ROOT).as_posix())
    return found - DEVELOPER_ONLY_FILES


def test_oscqr_evidence_mapping_is_declared_as_package_data() -> None:
    patterns = declared_package_data().get("coursewright.design.standards", [])
    assert "*.toml" in patterns
    assert "coursewright/design/standards/oscqr_evidence.toml" in (files_shipped_by_declaration())


def test_every_runtime_data_file_is_declared_as_package_data() -> None:
    undeclared = runtime_data_files_on_disk() - files_shipped_by_declaration()
    assert not undeclared, f"add to [tool.setuptools.package-data]: {sorted(undeclared)}"


def test_developer_only_files_still_exist() -> None:
    # A stale entry here would silently widen the exemption list.
    for relative_path in DEVELOPER_ONLY_FILES:
        assert (SOURCE_ROOT / relative_path).is_file(), relative_path


def test_oscqr_evidence_mapping_loads_as_a_package_resource() -> None:
    resource = resources.files("coursewright.design.standards") / "oscqr_evidence.toml"
    assert resource.is_file()
    tomllib.loads(resource.read_text(encoding="utf-8"))
