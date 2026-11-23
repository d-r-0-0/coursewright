#!/usr/bin/env python3
"""
CANTRIP: lint_doc_budget

PURPOSE:
    Enforce a line budget on the agent instruction files.

    AGENTS.md must stay a short routing hub, because bloated instruction
    files measurably reduce agent success rates and raise cost. Nothing
    stops an instruction file from growing unless a check runs in the
    gate, because every incident tempts someone to add "just one more
    rule."

    This cantrip turns the stated design decision into a checkable
    one.  It is deliberately dumb: it counts lines.  A cheap sensor
    that actually fires beats a sophisticated one that never runs.

    When a file exceeds its budget the fix is almost never "raise the
    budget."  It is to move detail into a focused doc under docs/ and
    leave a pointer in the mandatory-reading table -- which is what
    makes AGENTS.md a routing hub rather than a manual.

USAGE:
    python cantrips/lint_doc_budget.py
    python cantrips/lint_doc_budget.py --show   # report, never fail

DEPENDENCIES:
    - Python 3.11+ (standard library only, so this gate can always run)

FUNCTIONS (quick map):
    - count_content_lines: Count lines, ignoring trailing blanks
    - check_budget:        Compare one file against its budget
    - main:                Run every budget and set the exit code
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Budgets are deliberately tight. Raising one is a decision to make
# consciously in a PR, not a reflex when the lint goes red.
DOCUMENT_BUDGETS: dict[str, int] = {
    "AGENTS.md": 100,
    "CLAUDE.md": 80,
}

# Warn before failing, so growth is visible while there is still room.
WARNING_THRESHOLD_RATIO = 0.9


def count_content_lines(document_path: Path) -> int:
    """Count the lines in a document, ignoring trailing blank lines.

    Args:
        document_path: File to measure.

    Returns:
        The number of lines up to the last non-blank line.
    """
    lines = document_path.read_text(encoding="utf-8").splitlines()

    while lines and not lines[-1].strip():
        lines.pop()

    return len(lines)


def check_budget(relative_path: str, budget: int) -> tuple[str, str]:
    """Compare one document against its line budget.

    Args:
        relative_path: Repo-relative path of the document.
        budget: Maximum permitted content lines.

    Returns:
        A ``(status, message)`` pair where status is one of
        ``"ok"``, ``"warn"`` or ``"fail"``.
    """
    document_path = ROOT / relative_path

    if not document_path.is_file():
        return "fail", f"{relative_path} is missing"

    line_count = count_content_lines(document_path)

    if line_count > budget:
        return "fail", (
            f"{relative_path} is {line_count} lines, budget {budget} "
            f"({line_count - budget} over). Move detail into docs/ and "
            f"leave a pointer in the mandatory-reading table."
        )

    if line_count >= budget * WARNING_THRESHOLD_RATIO:
        return "warn", f"{relative_path} is {line_count}/{budget} lines"

    return "ok", f"{relative_path} is {line_count}/{budget} lines"


def main() -> None:
    """Check every budgeted document and exit non-zero on violation."""
    parser = argparse.ArgumentParser(
        description="Enforce line budgets on agent instruction files.",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Report the counts without failing.",
    )
    arguments = parser.parse_args()

    print("=== Instruction file budgets ===")

    failures: list[str] = []
    for relative_path, budget in DOCUMENT_BUDGETS.items():
        status, message = check_budget(relative_path, budget)
        label = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL"}[status]
        print(f"{label}  {message}")
        if status == "fail":
            failures.append(message)

    if failures and not arguments.show:
        print(f"\n{len(failures)} instruction file(s) over budget.")
        sys.exit(1)

    print("\nAll instruction files within budget.")


if __name__ == "__main__":
    main()
