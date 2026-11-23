#!/usr/bin/env python3
"""
CANTRIP: sync_agent_files

PURPOSE:
    Keep instruction files in sync with their single source of truth.
    The hard rules come from AGENTS.md; scoped blocks (the cantrip
    standard, the ADR format, the workflow rules) come from a marked
    block in another file, named by each target's source_path. Detects
    drift in marked sections, reports clear diffs, and can auto-update
    targets when requested.
    Add `<!-- sync-agent:intentional -->` inside a managed block to
    record purposeful differences without failing checks.

USAGE:
    python cantrips/sync_agent_files.py
    python cantrips/sync_agent_files.py --apply

DEPENDENCIES:
    - Python 3.11+
    - rich

FUNCTIONS (quick map):
    - parse_arguments:         Configure CLI options
    - extract_hard_rules:      Pull canonical hard rules from AGENTS.md
    - extract_canonical_block: Pull a target's canonical block from its source
    - extract_marked_block:    Read sync-managed blocks from targets
    - build_sync_targets:      Declare which files/markers to sync
    - evaluate_target:         Compare and optionally update targets
    - render_summary:          Print status tables and diffs
"""

from __future__ import annotations

import argparse
import difflib
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

ROOT = Path(__file__).resolve().parent.parent
AGENTS_PATH = ROOT / "AGENTS.md"
COPILOT_CANTRIP_STANDARD_PATH = (
    ROOT / ".github" / "instructions" / "cantrips.instructions.md"
)
ADR_README_PATH = ROOT / "docs" / "adr" / "README.md"
COPILOT_WORKFLOW_RULES_PATH = (
    ROOT / ".github" / "instructions" / "workflows.instructions.md"
)
INTENTIONAL_MARKER = "sync-agent:intentional"

# The default Windows console codepage is cp1252, which cannot encode the
# box-drawing and arrow glyphs rich uses. Without this the cantrip does
# its work and then dies while printing the summary, which looks exactly
# like a failed run.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

console = Console()


@dataclass
class SyncTarget:
    """A single target file + marker pair to keep in sync."""

    label: str
    path: Path
    start_marker: str
    end_marker: str
    # Where the canonical text lives. AGENTS.md is read by heading
    # (extract_hard_rules); any other source is read between the same
    # start/end markers the target uses.
    source_path: Path = AGENTS_PATH


@dataclass
class SyncResult:
    """Outcome of evaluating a target file."""

    target: SyncTarget
    status: str
    diff: str = ""
    notes: str = ""
    updated: bool = False

    @property
    def is_accidental_drift(self) -> bool:
        return self.status == "accidental_drift"

    @property
    def is_intentional_drift(self) -> bool:
        return self.status == "intentional_drift"

    @property
    def is_ok(self) -> bool:
        return self.status in {"in_sync", "updated", "intentional_drift"}


def parse_arguments() -> argparse.Namespace:
    """Define CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Sync tool-specific instruction files against their source"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply fixes for accidental drift (updates files in place)",
    )
    parser.add_argument(
        "--target",
        action="append",
        dest="targets",
        help="Limit to specific target labels (can be repeated)",
    )
    return parser.parse_args()


def extract_hard_rules(agents_text: str) -> str:
    """Extract the Hard Rules block from AGENTS.md."""
    start_heading = "## 3. Hard Rules"
    end_heading = "## 4. Key Commands"
    lines = agents_text.splitlines()

    def find_heading_index(marker: str) -> int:
        for idx, line in enumerate(lines):
            if line.strip().lower().startswith(marker.lower()):
                return idx
        raise ValueError(f"Heading not found: {marker}")

    start_index = find_heading_index(start_heading)
    end_index = find_heading_index(end_heading)
    block_lines = lines[start_index + 1 : end_index]
    block_text = "\n".join(block_lines).strip("\n")
    if not block_text.strip():
        raise ValueError("Hard Rules block in AGENTS.md is empty")
    return block_text + "\n"


def extract_marked_block(
    text: str, start_marker: str, end_marker: str
) -> tuple[str, int, int]:
    """Return the text between start and end markers with indices."""
    start_index = text.find(start_marker)
    if start_index == -1:
        raise ValueError("Start marker missing")
    start_index += len(start_marker)

    end_index = text.find(end_marker, start_index)
    if end_index == -1:
        raise ValueError("End marker missing")

    block_text = text[start_index:end_index]
    return block_text, start_index, end_index


def extract_canonical_block(target: SyncTarget) -> str:
    """Return the canonical text for a target, read from its source."""
    source_text = target.source_path.read_text(encoding="utf-8")
    if target.source_path == AGENTS_PATH:
        return extract_hard_rules(source_text)
    try:
        block_text, _, _ = extract_marked_block(
            source_text, target.start_marker, target.end_marker
        )
    except ValueError as err:
        raise ValueError(f"{target.source_path}: {err}") from err
    return block_text.strip("\n") + "\n"


def build_sync_targets() -> list[SyncTarget]:
    """Declare which files have sync-managed blocks."""
    required_targets = [
        SyncTarget(
            label="claude-hard-rules",
            path=ROOT / "CLAUDE.md",
            start_marker="<!-- sync-agent:hard-rules start -->",
            end_marker="<!-- sync-agent:hard-rules end -->",
        ),
    ]
    optional_targets = [
        SyncTarget(
            label="copilot-hard-rules",
            path=ROOT / ".github" / "copilot-instructions.md",
            start_marker="<!-- sync-agent:hard-rules start -->",
            end_marker="<!-- sync-agent:hard-rules end -->",
        ),
        SyncTarget(
            label="claude-cantrip-standard",
            path=ROOT / ".claude" / "rules" / "cantrips.md",
            start_marker="<!-- sync-agent:cantrip-standard start -->",
            end_marker="<!-- sync-agent:cantrip-standard end -->",
            source_path=COPILOT_CANTRIP_STANDARD_PATH,
        ),
        SyncTarget(
            label="copilot-adr-format",
            path=ROOT / ".github" / "instructions" / "adr.instructions.md",
            start_marker="<!-- sync-agent:adr-format start -->",
            end_marker="<!-- sync-agent:adr-format end -->",
            source_path=ADR_README_PATH,
        ),
        SyncTarget(
            label="claude-adr-format",
            path=ROOT / ".claude" / "rules" / "adr.md",
            start_marker="<!-- sync-agent:adr-format start -->",
            end_marker="<!-- sync-agent:adr-format end -->",
            source_path=ADR_README_PATH,
        ),
        SyncTarget(
            label="claude-workflow-rules",
            path=ROOT / ".claude" / "rules" / "workflows.md",
            start_marker="<!-- sync-agent:workflow-rules start -->",
            end_marker="<!-- sync-agent:workflow-rules end -->",
            source_path=COPILOT_WORKFLOW_RULES_PATH,
        ),
    ]
    active_optional = [
        target
        for target in optional_targets
        if target.path.exists() or target.source_path != AGENTS_PATH and target.source_path.exists()
    ]
    return [*required_targets, *active_optional]


def compute_diff(
    current: str, canonical: str, path: Path, source_path: Path
) -> str:
    """Return a unified diff between the target and canonical text."""
    diff_lines = difflib.unified_diff(
        current.strip().splitlines(),
        canonical.strip().splitlines(),
        fromfile=str(path),
        tofile=describe_source(source_path),
        lineterm="",
    )
    return "\n".join(diff_lines)


def describe_source(source_path: Path) -> str:
    """Name a source file for diff headers, relative to the repo."""
    try:
        return source_path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(source_path)


def replace_block(
    original_text: str,
    start_index: int,
    end_index: int,
    replacement_block: str,
) -> str:
    """Replace the marked block with the canonical text."""
    cleaned_replacement = "\n" + replacement_block.strip("\n") + "\n"
    return (
        original_text[:start_index]
        + cleaned_replacement
        + original_text[end_index:]
    )


def evaluate_target(
    target: SyncTarget,
    canonical_block: str,
    apply_changes: bool,
) -> SyncResult:
    """Compare target block to canonical block and optionally update."""
    if not target.path.exists():
        return SyncResult(
            target=target,
            status="missing_file",
            notes=f"File not found: {target.path}",
        )

    text = target.path.read_text(encoding="utf-8")
    try:
        block_text, start_index, end_index = extract_marked_block(
            text, target.start_marker, target.end_marker
        )
    except ValueError as err:
        # Coursewright's CLAUDE.md delegates directly to AGENTS.md rather
        # than duplicating the rules block when no sync markers are present.
        delegates_to_agents = (
            target.source_path == AGENTS_PATH
            and target.start_marker not in text
            and target.end_marker not in text
            and "`AGENTS.md`" in text
        )
        if delegates_to_agents:
            return SyncResult(
                target=target,
                status="in_sync",
                notes="Delegates to AGENTS.md",
            )
        return SyncResult(
            target=target,
            status="missing_marker",
            notes=str(err),
        )

    normalized_block = block_text.strip("\n")
    normalized_canonical = canonical_block.strip("\n")
    has_intentional_marker = INTENTIONAL_MARKER in block_text
    matches = normalized_block == normalized_canonical

    result_status = "in_sync"
    updated = False

    if matches:
        result_status = "in_sync"
    elif has_intentional_marker:
        result_status = "intentional_drift"
    else:
        result_status = "accidental_drift"
        if apply_changes:
            updated_text = replace_block(
                text,
                start_index,
                end_index,
                canonical_block,
            )
            target.path.write_text(updated_text, encoding="utf-8")
            updated = True
            result_status = "updated"
            block_text = canonical_block

    diff_output = ""
    if not matches:
        diff_output = compute_diff(
            block_text, canonical_block, target.path, target.source_path
        )

    return SyncResult(
        target=target,
        status=result_status,
        diff=diff_output,
        notes="Marked intentional" if has_intentional_marker else "",
        updated=updated,
    )


def render_summary(results: Iterable[SyncResult]) -> None:
    """Print a summary table and diffs for any drift."""
    table = Table(title="Instruction File Sync Status")
    table.add_column("Target")
    table.add_column("Status")
    table.add_column("Notes")

    status_icons = {
        "in_sync": "[green]✔ in sync[/green]",
        "updated": "[green]⬆ updated[/green]",
        "intentional_drift": "[yellow]↔ intentional drift[/yellow]",
        "accidental_drift": "[red]⚠ drift[/red]",
        "missing_marker": "[red]⚠ missing markers[/red]",
        "missing_file": "[red]⚠ missing file[/red]",
    }

    for result in results:
        table.add_row(
            result.target.label,
            status_icons.get(result.status, result.status),
            result.notes,
        )

    console.print(table)

    for result in results:
        if result.diff:
            console.print(
                Panel.fit(
                    result.diff or "(no diff available)",
                    title=result.target.label,
                    subtitle=(
                        "Diff: target vs "
                        + describe_source(result.target.source_path)
                    ),
                    border_style="red" if result.is_accidental_drift else "yellow",
                )
            )


def main() -> None:
    args = parse_arguments()
    available_targets = build_sync_targets()

    if args.targets:
        selected_labels = set(args.targets)
        targets = [
            target
            for target in available_targets
            if target.label in selected_labels
        ]
    else:
        targets = available_targets

    if not targets:
        console.print("[red]No matching targets selected.[/red]")
        raise SystemExit(1)

    results = [
        evaluate_target(
            target,
            extract_canonical_block(target),
            apply_changes=args.apply,
        )
        for target in targets
    ]

    render_summary(results)

    accidental_drifts = [
        result for result in results if result.is_accidental_drift
    ]
    missing = [
        result
        for result in results
        if result.status in {"missing_file", "missing_marker"}
    ]

    if accidental_drifts or missing:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
