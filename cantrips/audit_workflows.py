#!/usr/bin/env python3
"""
CANTRIP: audit-workflows

PURPOSE:
    Run zizmor against .github/workflows/ and report GitHub Actions
    hardening problems: unpinned actions, excessive permissions,
    template injection, credential persistence, cache poisoning.
    Exits non-zero on any finding so the gate fails closed.

USAGE:
    python cantrips/audit_workflows.py

DEPENDENCIES:
    - Python 3.11+
    - zizmor >= 1.30 (pip install -e ".[dev]")

FUNCTIONS (quick map):
    - candidate_script_directories: Where pip puts this interpreter's scripts
    - find_zizmor_executable: Locate zizmor beside the interpreter, not on PATH
    - run_audit: Shell out to zizmor for the workflows directory
    - main: Orchestrates the flow and reports the result
"""

from __future__ import annotations

import os
import subprocess
import sys
import sysconfig
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS_DIRECTORY = _REPO_ROOT / ".github" / "workflows"


def display_cantrip_header(cantrip_name: str) -> None:
    """Print a header announcing which cantrip is running."""
    border = "=" * 40
    print(f"\n{border}")
    print(f"  Cantrip: {cantrip_name}")
    print(f"{border}\n")


def candidate_script_directories() -> list[Path]:
    """Return the directories pip installs this interpreter's scripts to.

    The interpreter's own directory covers a POSIX venv (``bin/``) and
    a Windows venv (``Scripts\\``).  The sysconfig paths cover a system
    interpreter, where pip falls back to a user install -- which is what
    the CI runner does ("Defaulting to user installation").
    """
    directories = [
        Path(sys.executable).parent,
        Path(sysconfig.get_path("scripts")),
    ]
    user_scheme_name = f"{os.name}_user"
    if user_scheme_name in sysconfig.get_scheme_names():
        user_scripts = sysconfig.get_path("scripts", user_scheme_name)
        directories.append(Path(user_scripts))
    return directories


def find_zizmor_executable() -> Path | None:
    """Return the zizmor binary installed for this interpreter, if any.

    zizmor is a Rust binary shipped as a wheel; it has no importable
    module, so ``python -m zizmor`` does not work.  Never fall back to
    PATH: on Windows the venv's Scripts directory is not on PATH unless
    the venv was activated, and a PATH hit could be a different install
    than the one the project pins.
    """
    executable_name = "zizmor.exe" if os.name == "nt" else "zizmor"
    for directory in candidate_script_directories():
        candidate = directory / executable_name
        if candidate.is_file():
            return candidate
    return None


def run_audit(zizmor_executable: Path) -> int:
    """Run zizmor over the workflows directory; return its exit code."""
    relative = _WORKFLOWS_DIRECTORY.relative_to(_REPO_ROOT)
    print(f"--- Auditing {relative.as_posix()}/ with {zizmor_executable} ---")
    command = [str(zizmor_executable), str(_WORKFLOWS_DIRECTORY)]
    sys.stdout.flush()  # keep our header above zizmor's own output
    result = subprocess.run(command, check=False, cwd=_REPO_ROOT)
    print()
    return result.returncode


def main() -> None:
    """Entry point — audit the workflows and report the result."""
    display_cantrip_header("audit-workflows")

    if not _WORKFLOWS_DIRECTORY.is_dir():
        print("No .github/workflows/ directory — nothing to audit.")
        sys.exit(0)

    zizmor_executable = find_zizmor_executable()
    if zizmor_executable is None:
        searched = "\n".join(
            f"         {directory}"
            for directory in candidate_script_directories()
        )
        print(
            "ERROR: zizmor is not installed for this interpreter.\n"
            f"       interpreter: {sys.executable}\n"
            f"       searched:\n{searched}\n"
            '       pip install -e ".[dev]"'
        )
        sys.exit(1)

    exit_code = run_audit(zizmor_executable)
    if exit_code != 0:
        print("AUDIT FAILED — workflow findings. See output above.")
    else:
        print("Audit clean — no workflow hardening findings.")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
