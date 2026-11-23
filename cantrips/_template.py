#!/usr/bin/env python3
"""
CANTRIP: <short-name>

PURPOSE:
    <what this does and why it exists>

USAGE:
    python cantrips/<short-name>.py [--execute]

DEPENDENCIES:
    - Python 3.10+
    - rich

FUNCTIONS (quick map):
    - build_argument_parser: Defines dry-run and execution flags
    - display_cantrip_header: Announces which cantrip is running
    - backup_before_writing: Stubs the required backup-first step
    - main: Orchestrates the flow

NOTES:
    This cantrip exists to keep CLI commands <= 80 chars
    and preserve intent. Canvas-writing cantrips must default to dry run,
    require --execute for writes, and back up before writing.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def build_argument_parser() -> argparse.ArgumentParser:
    """Define the standard dry-run interface for a cantrip."""
    parser = argparse.ArgumentParser(
        description="<what this cantrip previews or changes>",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="write changes; omit for the default dry run",
    )
    return parser


def display_cantrip_header(cantrip_name: str) -> None:
    """Print a header announcing which cantrip is running."""
    border = "=" * 40
    print(f"\n{border}")
    print(f"  Cantrip: {cantrip_name}")
    print(f"{border}\n")


def backup_before_writing(
    target_path: Path,
    backup_payload: dict[str, Any],
) -> Path:
    """Return the backup path; replace this stub before writing."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = project_root / "backups"
    backup_path = backup_path / f"{target_path.stem}-{timestamp}.json"
    print(f"TODO: write JSON backup to {backup_path}")
    print(f"TODO: include {len(backup_payload)} top-level keys")
    return backup_path


def main() -> None:
    """Entry point - replace this with real logic."""
    parser = build_argument_parser()
    args = parser.parse_args()

    cantrip_name = "<short-name>"
    display_cantrip_header(cantrip_name)

    dry_run = not args.execute
    planned_changes: dict[str, Any] = {}
    if dry_run:
        print("DRY RUN: preview planned changes; no writes will occur")
        return

    backup_before_writing(Path("<target>.json"), planned_changes)
    print("TODO: apply changes after the backup exists")


if __name__ == "__main__":
    main()
