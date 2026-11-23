#!/usr/bin/env python3
"""
CANTRIP: audit-deps

PURPOSE:
    Run pip-audit against requirements files (or the installed project
    environment when dependencies live in pyproject.toml) and report
    known CVEs.  Exits non-zero if any vulnerability is found so CI can
    fail fast.

USAGE:
    python cantrips/audit_deps.py [--fix-dry-run]

    --fix-dry-run   Show remediation hints without applying them.

DEPENDENCIES:
    - Python 3.11+
    - pip-audit >= 2.10

FUNCTIONS (quick map):
    - collect_requirements_files: Find all requirements*.txt in the repo
    - run_audit: Shell out to pip-audit for one requirements file
    - run_environment_audit: Audit the active environment for pyproject.toml
    - main: Orchestrates the flow and aggregates exit codes
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REQUIREMENTS_PATTERNS = ["requirements*.txt", "cantrips/requirements*.txt"]
_OFFLINE_ERROR_MARKERS = (
    "Temporary failure in name resolution",
    "Failed to resolve",
    "Could not connect to PyPI's vulnerability feed",
    "Network is unreachable",
)


def display_cantrip_header(cantrip_name: str) -> None:
    """Print a header announcing which cantrip is running."""
    border = "=" * 40
    print(f"\n{border}")
    print(f"  Cantrip: {cantrip_name}")
    print(f"{border}\n")


def collect_requirements_files() -> list[Path]:
    """Return all requirements*.txt paths found under the repo root."""
    found: list[Path] = []
    seen: set[Path] = set()
    for pattern in _REQUIREMENTS_PATTERNS:
        for path in sorted(_REPO_ROOT.glob(pattern)):
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                found.append(path)
    return found


def run_audit(requirements_file: Path, fix_dry_run: bool) -> int:
    """Run pip-audit for one requirements file; return its exit code."""
    relative = requirements_file.relative_to(_REPO_ROOT)
    print(f"--- Auditing {relative} ---")

    # Invoke as a module, not as a bare `pip-audit` executable: on
    # Windows the venv's Scripts directory is not on PATH unless the
    # venv was explicitly activated, so the bare name is not found.
    command = [
        sys.executable,
        "-m",
        "pip_audit",
        "-r",
        str(requirements_file),
    ]
    if fix_dry_run:
        command += ["--fix", "--dry-run"]

    try:
        result = subprocess.run(command, check=False)
    except FileNotFoundError:
        print(
            "ERROR: pip-audit is not installed for this interpreter.\n"
            '       pip install -e ".[dev]"'
        )
        return 1

    print()
    return result.returncode


def run_environment_audit(fix_dry_run: bool) -> int:
    """Run pip-audit against the installed project environment."""
    print("--- Auditing installed environment (pyproject.toml) ---")
    command = [
        sys.executable,
        "-m",
        "pip_audit",
        "--local",
        "--skip-editable",
        "--progress-spinner",
        "off",
    ]
    if fix_dry_run:
        command += ["--fix", "--dry-run"]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except FileNotFoundError:
        print(
            "ERROR: pip-audit is not installed for this interpreter.\n"
            '       pip install -e ".[dev]"'
        )
        return 1

    combined_output = f"{result.stdout}\n{result.stderr}"
    if "No module named pip_audit" in combined_output:
        print(
            "ERROR: pip-audit is not installed for this interpreter.\n"
            '       pip install -e ".[dev]"'
        )
        return 1

    if result.returncode != 0 and any(
        marker in combined_output for marker in _OFFLINE_ERROR_MARKERS
    ):
        print("Offline environment — PyPI unreachable; skipping live query.\n")
        return 0

    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip())
    print()
    return result.returncode


def main() -> None:
    """Entry point — audit requirements files or installed environment."""
    display_cantrip_header("audit-deps")

    fix_dry_run = "--fix-dry-run" in sys.argv

    requirements_files = collect_requirements_files()
    if not requirements_files:
        pyproject_path = _REPO_ROOT / "pyproject.toml"
        if not pyproject_path.is_file():
            print("No requirements*.txt files found — nothing to audit.")
            sys.exit(0)
        overall_exit_code = run_environment_audit(fix_dry_run)
    else:
        overall_exit_code = 0
        for req_file in requirements_files:
            exit_code = run_audit(req_file, fix_dry_run)
            if exit_code != 0:
                overall_exit_code = exit_code

    if overall_exit_code != 0:
        print("AUDIT FAILED — vulnerabilities found. See output above.")
    else:
        print("Audit clean — no known CVEs in pinned dependencies.")

    sys.exit(overall_exit_code)


if __name__ == "__main__":
    main()
