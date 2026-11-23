"""``coursewright ta zybooks-import``: CLI wrapper over ``coursewright.ta.zybooks.importer``.

Supports Downloads-first CSV discovery, optional Canvas module overview hints,
dry-run previewing by default (without writing mapping cache or grades to
Canvas), and guarded ``--execute`` posting through ``SafeCanvasWriter``.
"""

from __future__ import annotations

import contextlib
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from coursewright.core.canvas import create_canvas_client
from coursewright.core.canvas.safe_write import default_backup_directory
from coursewright.core.config import AppConfig, resolve_workspace_directory
from coursewright.core.inbox import WORKSPACE_IMPORT_FOLDER_NAME
from coursewright.ta.zybooks.importer import (
    CsvImportCoordinator,
    ImportProblem,
    MappingPlan,
    PostResult,
    Preview,
    extract_module_section_hints,
    find_score_report_candidate,
    format_sections,
    propose_units,
    read_snapshot,
)
from coursewright.ui.cli.activity import RemoteActivityDisplay

console = Console()


def _fail(
    title: str,
    message: str,
    *,
    as_json: bool,
    details: dict[str, Any] | None = None,
) -> typer.Exit:
    if as_json:
        payload: dict[str, Any] = {"status": "error", "title": title, "error": message}
        if details:
            payload.update(details)
        typer.echo(json.dumps(payload, indent=2, default=str))
    else:
        console.print(Panel(message, title=f"[red]{title}[/red]", border_style="red"))
    return typer.Exit(1)


def _activity(description: str, *, as_json: bool) -> Any:
    if as_json:
        return contextlib.nullcontext()
    return RemoteActivityDisplay(description, console=console)


def _resolve_csv_path(file_path: Path | None, workspace: Path | None) -> Path | None:
    if file_path is not None:
        return file_path
    workspace_import_dir: Path | None = None
    if workspace is not None:
        for folder_name in (WORKSPACE_IMPORT_FOLDER_NAME, "imports"):
            candidate_dir = workspace / folder_name
            if candidate_dir.is_dir():
                workspace_import_dir = candidate_dir
                break
    candidate = (
        find_score_report_candidate(workspace_import_directory=workspace_import_dir)
        if workspace_import_dir is not None
        else find_score_report_candidate()
    )
    return candidate.path if candidate is not None else None


def _format_import_problem(error: ImportProblem) -> str:
    message = str(error)
    lowered = message.lower()
    if "distinct" in lowered:
        return (
            f"{message} Verify the lab pool contains enough distinct required labs "
            "(--labs-required, default 5) and pass --confirm-distinct-labs."
        )
    if "zeros/decreases" in lowered:
        return (
            f"{message} Re-run with --approve-zeros to explicitly approve zero or reduced scores."
        )
    return message


def _row_counts(preview: Preview) -> dict[str, int]:
    return {
        "total_rows": len(preview.rows),
        "ready": sum(row.status == "Ready" for row in preview.rows),
        "skipped": sum(row.status.startswith("Skipped") for row in preview.rows),
        "blocked": sum(row.status.startswith("Blocked") for row in preview.rows),
        "risky": sum(row.risky for row in preview.rows),
        "unmatched_source": preview.unmatched_source,
        "unmatched_canvas": preview.unmatched_canvas,
    }


def _verification_summary(results: tuple[PostResult, ...]) -> dict[str, Any]:
    by_status = dict(Counter(result.status for result in results))
    verified = sum(result.status.startswith("Verified") for result in results)
    return {
        "total": len(results),
        "verified": verified,
        "failed": len(results) - verified,
        "by_status": by_status,
    }


def _build_json_payload(
    *,
    course_id: int,
    book_id: str,
    csv_path: Path,
    cached: bool,
    used_module_hints: bool,
    execute: bool,
    plan: MappingPlan,
    preview: Preview,
    results: tuple[PostResult, ...],
    backup_dir: Path | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "course_id": course_id,
        "book_id": book_id,
        "csv_path": str(csv_path),
        "cached_mappings": cached,
        "used_module_hints": used_module_hints,
        "execute": execute,
        "dry_run": not execute,
        "units": [
            {
                "name": unit.name,
                "sections": list(unit.sections),
                "lab_sections": list(unit.lab_sections),
                "activity_target": unit.activity_target,
                "lab_target": unit.lab_target,
                "distinct_labs_confirmed": unit.distinct_labs_confirmed,
                "labs_required": unit.labs_required,
            }
            for unit in plan.units
        ],
        "selected_targets": list(preview.selected_targets),
        "include_existing": preview.include_existing,
        "counts": _row_counts(preview),
        "unmatched_source": preview.unmatched_source,
        "unmatched_canvas": preview.unmatched_canvas,
        "rows": [
            {
                "key": row.key,
                "assignment_id": row.assignment_id,
                "student_id": row.student_id,
                "assignment": row.assignment_name,
                "student": row.student_name,
                "old_points": str(row.old_points) if row.old_points is not None else None,
                "new_points": str(row.new_points),
                "status": row.status,
                "risky": row.risky,
            }
            for row in preview.rows
        ],
    }
    if execute:
        payload["results"] = [
            {"row_key": result.row_key, "status": result.status} for result in results
        ]
        payload["verification"] = _verification_summary(results)
        payload["backup_directory"] = str(backup_dir) if backup_dir is not None else None
    return payload


def _status_style(status: str) -> str:
    if status == "Ready":
        return f"[green]{status}[/green]"
    if status.startswith("Skipped"):
        return f"[yellow]{status}[/yellow]"
    return f"[red]{status}[/red]"


def _render_rich_output(
    *,
    course_id: int,
    book_id: str,
    csv_path: Path,
    cached: bool,
    used_module_hints: bool,
    execute: bool,
    plan: MappingPlan,
    preview: Preview,
    results: tuple[PostResult, ...],
    backup_dir: Path | None,
) -> None:
    mapping_source = (
        "cached" if cached else "module hints" if used_module_hints else "assignment metadata"
    )
    mode_label = (
        "[bold green]EXECUTE[/bold green]" if execute else "[bold yellow]DRY RUN[/bold yellow]"
    )
    unit_lines = []
    for unit in plan.units:
        targets = []
        if unit.activity_target is not None:
            targets.append(f"activity={unit.activity_target}")
        if unit.lab_target is not None:
            lab_desc = format_sections(unit.lab_sections) or "none"
            confirmed = "confirmed" if unit.distinct_labs_confirmed else "unconfirmed"
            targets.append(
                f"lab={unit.lab_target} [{lab_desc}; best {unit.labs_required}; {confirmed}]"
            )
        sections_text = format_sections(unit.sections)
        unit_lines.append(f"  - {unit.name}: sections {sections_text} ({', '.join(targets)})")

    summary_text = (
        f"Mode: {mode_label}\n"
        f"Course ID: {course_id} | Book ID: {book_id}\n"
        f"CSV: {csv_path.name}\n"
        f"Mappings: {mapping_source} ({len(plan.units)} unit(s))\n"
        + "\n".join(unit_lines)
    )
    console.print(Panel(summary_text, title="zyBooks Import Summary", border_style="cyan"))

    table = Table(title="Grade Preview", show_header=True, header_style="bold magenta")
    table.add_column("Student", style="cyan")
    table.add_column("Assignment", style="white")
    table.add_column("Old", justify="right")
    table.add_column("New", justify="right", style="bold")
    table.add_column("Status")
    table.add_column("Risky", justify="center")

    for row in preview.rows:
        old_text = "-" if row.old_points is None else str(row.old_points)
        risky_text = "[yellow]Yes[/yellow]" if row.risky else "No"
        table.add_row(
            row.student_name,
            row.assignment_name,
            old_text,
            str(row.new_points),
            _status_style(row.status),
            risky_text,
        )
    console.print(table)

    counts = _row_counts(preview)
    console.print(
        f"Rows: {counts['total_rows']} total | "
        f"[green]{counts['ready']} Ready[/green] | "
        f"[yellow]{counts['skipped']} Skipped[/yellow] | "
        f"[red]{counts['blocked']} Blocked[/red] | "
        f"Risky: {counts['risky']}"
    )
    console.print(
        f"Unmatched source students: {preview.unmatched_source} | "
        f"Unmatched Canvas students: {preview.unmatched_canvas}"
    )

    if not execute:
        console.print(
            "[yellow]Dry run only — no grades were written to Canvas and no mapping cache "
            "was persisted. Re-run with --execute to write Ready rows.[/yellow]"
        )
        return

    if not results:
        console.print("[yellow]Nothing to send: no Ready rows found.[/yellow]")
        if backup_dir is not None:
            console.print(f"Backup directory: {backup_dir}", soft_wrap=True)
        return

    verification = _verification_summary(results)
    status_parts = ", ".join(
        f"{status}: {count}" for status, count in sorted(verification["by_status"].items())
    )
    console.print(
        f"[green]Verified:[/green] {verification['verified']}/{verification['total']} "
        f"({status_parts})"
    )
    if backup_dir is not None:
        console.print(f"[cyan]Backup directory:[/cyan] {backup_dir}", soft_wrap=True)


def zybooks_import_command(
    ctx: typer.Context,
    course_id: int = typer.Option(..., "--course", "-c", help="Canvas course ID"),
    file_path: Path | None = typer.Option(
        None,
        "--file",
        "-f",
        help="Path to zyBooks CSV report (defaults to newest match in Downloads/workspace)",
    ),
    book_id: str | None = typer.Option(
        None,
        "--book-id",
        help="zyBooks book identifier (defaults to filename candidate)",
    ),
    include_existing: bool = typer.Option(
        False, "--include-existing", help="Allow lowering existing Canvas grades"
    ),
    approve_zeros: bool = typer.Option(
        False, "--approve-zeros", help="Explicitly approve writing zero or reduced scores"
    ),
    confirm_distinct_labs: bool = typer.Option(
        False,
        "--confirm-distinct-labs",
        help="Confirm lab pools contain enough distinct required labs",
    ),
    labs_required: int | None = typer.Option(
        None,
        "--labs-required",
        min=1,
        help="Labs that count toward each lab grade (default: the mapping's value, normally 5)",
    ),
    execute: bool = typer.Option(
        False, "--execute", help="Write grades to Canvas (default is dry-run)"
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit structured JSON output"),
) -> None:
    """Import zyBooks CSV scores into Canvas (dry-run by default)."""
    config: AppConfig = (ctx.obj or {}).get("config", AppConfig())
    workspace = resolve_workspace_directory(config)

    resolved_path = _resolve_csv_path(file_path, workspace)
    if resolved_path is None:
        raise _fail(
            "No zyBooks CSV found",
            "No zyBooks CSV report (*_report_*.csv) was found in Downloads or workspace import "
            "directories. Pass --file PATH explicitly.",
            as_json=as_json,
        ) from None

    try:
        snapshot = read_snapshot(resolved_path)
    except ImportProblem as err:
        raise _fail("Invalid zyBooks CSV", str(err), as_json=as_json) from None

    effective_book_id = (book_id if book_id is not None else snapshot.book_candidate).strip()
    if not effective_book_id:
        raise _fail(
            "Missing book ID",
            "Could not determine a zyBooks book ID from the CSV filename. "
            "Pass --book-id explicitly.",
            as_json=as_json,
        ) from None

    try:
        with _activity("Connecting to Canvas", as_json=as_json):
            canvas_client = create_canvas_client(
                base_url=config.discussion.canvas_url,
                api_token_file=config.discussion.api_token_file,
            )
    except Exception as err:
        raise _fail("Canvas connection failed", str(err), as_json=as_json) from None

    if canvas_client is None:
        raise _fail(
            "Canvas authentication failed",
            "Could not authenticate to Canvas. Check your token or run: "
            "coursewright auth login-browser",
            as_json=as_json,
        ) from None

    coordinator = CsvImportCoordinator(
        canvas_client,
        course_id,
        snapshot,
        workspace_directory=workspace,
    )
    try:
        with _activity("Loading Canvas roster and assignments", as_json=as_json):
            plan, cached = coordinator.load_mappings(effective_book_id)
    except ImportProblem as err:
        raise _fail("Mapping load failed", str(err), as_json=as_json) from None
    except Exception as err:
        raise _fail(
            "Canvas read failed",
            f"Could not load Canvas roster or assignments: {err}",
            as_json=as_json,
        ) from None

    used_module_hints = False
    if not cached and not plan.units:
        with _activity("Checking Canvas module overview pages", as_json=as_json):
            hints = extract_module_section_hints(canvas_client, course_id, snapshot.sections)
        if hints:
            hinted_units = propose_units(
                snapshot, coordinator.assignments, module_section_hints=hints
            )
            if hinted_units:
                plan = replace(plan, units=hinted_units)
                used_module_hints = True

    if labs_required is not None:
        # Changing how many labs count is a mapping change, so a cached approval
        # no longer covers it: the plan is re-approved below on --execute.
        plan = replace(
            plan,
            units=tuple(
                replace(unit, labs_required=labs_required, distinct_labs_confirmed=False)
                if unit.lab_target is not None and unit.labs_required != labs_required
                else unit
                for unit in plan.units
            ),
        )
    if confirm_distinct_labs:
        plan = replace(
            plan,
            units=tuple(
                replace(unit, distinct_labs_confirmed=True)
                if len(unit.lab_sections) >= unit.labs_required
                else unit
                for unit in plan.units
            ),
        )

    selected_targets = {
        target
        for unit in plan.units
        for target in (unit.activity_target, unit.lab_target)
        if target is not None
    }
    if not selected_targets:
        raise _fail(
            "No mapped assignments",
            "No zyBooks activity or lab assignments could be mapped to this CSV. "
            "Ensure assignment names/descriptions or module overview pages mention sections "
            "or chapters, or configure mappings in the TUI.",
            as_json=as_json,
        ) from None

    if execute and workspace is None:
        raise _fail(
            "Workspace required",
            "A private workspace is required for --execute (backups and mapping cache). "
            "Set COURSEWRIGHT_WORKSPACE or configure workspace_directory.",
            as_json=as_json,
        ) from None
    try:
        active_plan = (
            coordinator.approve_mappings(plan, course_id, effective_book_id)
            if execute
            else replace(plan, approved=True)
        )
        with _activity("Fetching current Canvas submissions", as_json=as_json):
            preview = coordinator.prepare_preview(
                active_plan, selected_targets, include_existing=include_existing
            )
    except ImportProblem as err:
        title = "Import blocked" if execute else "Preview blocked"
        raise _fail(title, _format_import_problem(err), as_json=as_json) from None

    results: tuple[PostResult, ...] = ()
    backup_dir: Path | None = None
    if execute:
        backup_dir = default_backup_directory(workspace)
        ready_keys = {row.key for row in preview.rows if row.status == "Ready"}
        if ready_keys:
            try:
                approval = coordinator.approve_rows(
                    preview, ready_keys, accept_risky=approve_zeros
                )
                with _activity("Submitting grades to Canvas", as_json=as_json):
                    results = coordinator.submit(
                        active_plan,
                        selected_targets,
                        include_existing,
                        approval,
                        execute=True,
                    )
            except ImportProblem as err:
                raise _fail(
                    "Submission blocked", _format_import_problem(err), as_json=as_json
                ) from None

    if as_json:
        payload = _build_json_payload(
            course_id=course_id,
            book_id=effective_book_id,
            csv_path=snapshot.path,
            cached=cached,
            used_module_hints=used_module_hints,
            execute=execute,
            plan=active_plan,
            preview=preview,
            results=results,
            backup_dir=backup_dir,
        )
        typer.echo(json.dumps(payload, indent=2, default=str))
        return

    _render_rich_output(
        course_id=course_id,
        book_id=effective_book_id,
        csv_path=snapshot.path,
        cached=cached,
        used_module_hints=used_module_hints,
        execute=execute,
        plan=active_plan,
        preview=preview,
        results=results,
        backup_dir=backup_dir,
    )
