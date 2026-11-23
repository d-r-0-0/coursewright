"""``coursewright ta packet-tracer``: Packet Tracer completeness, feedback, and grading CLI.

A thin wrapper over :mod:`coursewright.ta.packettracer`: parse options,
run detection or completeness checks, preview or execute safe writes via
:class:`SafeCanvasWriter`, and render Rich tables or structured JSON.
"""

from __future__ import annotations

import json
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from typing import Any, NoReturn

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from coursewright.core.canvas import create_canvas_client, create_safe_canvas_writer
from coursewright.core.canvas.safe_write import (
    SafeCanvasWriter,
    SafeWriteError,
    default_backup_directory,
)
from coursewright.core.config import AppConfig, resolve_workspace_directory
from coursewright.ta.packettracer import (
    REQUIRED_FEEDBACK_TEMPLATES,
    AutoGradeResult,
    CompletenessVerdict,
    FeedbackPostResult,
    MissingTemplatesError,
    PacketTracerAssignmentDetector,
    PacketTracerCheckSummary,
    auto_grade_complete_submissions,
    check_packet_tracer_assignment,
    post_completeness_feedback,
)
from coursewright.ui.cli.activity import RemoteActivityDisplay

console = Console()

_VERDICT_DISPLAY: dict[CompletenessVerdict, str] = {
    CompletenessVerdict.COMPLETE: "[green]Complete[/green]",
    CompletenessVerdict.PARTIAL: "[yellow]Partial[/yellow]",
    CompletenessVerdict.NOT_SUBMITTED: "[red]Not Submitted[/red]",
    CompletenessVerdict.NO_LAB_FILES: "[red]No Lab Files[/red]",
}


def _fail(message: str, *, title: str = "Packet Tracer error", as_json: bool = False) -> NoReturn:
    """Print a clean error (JSON or Rich panel) and exit with status 1."""
    if as_json:
        typer.echo(json.dumps({"error": message, "title": title}, indent=2, default=str))
    else:
        console.print(
            Panel(escape(message), title=f"[red]{escape(title)}[/red]", border_style="red")
        )
    raise typer.Exit(1) from None


def _activity(description: str, *, as_json: bool):
    """Show RemoteActivityDisplay only when not emitting JSON."""
    if as_json:
        return nullcontext()
    return RemoteActivityDisplay(description, console=console)


def _connect_canvas(config: AppConfig, *, as_json: bool) -> Any:
    """Create and validate a Canvas client from configuration."""
    try:
        with _activity("Connecting to Canvas", as_json=as_json):
            client = create_canvas_client(
                base_url=config.discussion.canvas_url,
                api_token_file=config.discussion.api_token_file,
            )
    except Exception as err:
        _fail(
            f"Failed to connect to Canvas: {err}",
            title="Canvas connection failed",
            as_json=as_json,
        )
    if client is None:
        _fail(
            "Could not authenticate to Canvas.",
            title="Canvas connection failed",
            as_json=as_json,
        )
    return client


def _render_detected_assignments(
    course_id: int,
    total_assignments: int,
    detected: list[tuple[Any, Any]],
) -> None:
    """Render detected Packet Tracer assignments as a Rich table."""
    if not detected:
        console.print(
            f"[yellow]No Packet Tracer assignments detected in course {course_id} "
            f"(scanned {total_assignments} assignment(s)).[/yellow]"
        )
        return

    table = Table(
        title=f"Packet Tracer Assignments — Course {course_id} ({len(detected)} detected)",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("ID", style="cyan", no_wrap=True, justify="right")
    table.add_column("Assignment", style="white")
    table.add_column("Confidence", style="green", no_wrap=True)
    table.add_column("Points", justify="right", style="yellow")
    table.add_column("Extensions", style="blue")

    for assignment, detection in detected:
        points = f"{assignment.points_possible or 0:g}"
        extensions = ", ".join(assignment.allowed_extensions) or "—"
        table.add_row(
            str(assignment.id),
            escape(assignment.name),
            detection.confidence.value,
            points,
            escape(extensions),
        )
    console.print(table)
    console.print("[dim]Pass --assignment ID to inspect submission completeness.[/dim]")


def _render_completeness_report(
    summary: PacketTracerCheckSummary,
    *,
    grade_result: AutoGradeResult | None,
    feedback_result: FeedbackPostResult | None,
    execute: bool,
    backup_dir: Path | None,
) -> None:
    """Render submission completeness and action results with Rich."""
    table = Table(
        title=f"{escape(summary.assignment_name)} (ID {summary.assignment_id}) — Completeness",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Student", style="cyan")
    table.add_column("Document", style="white")
    table.add_column("Packet Tracer", style="white")
    table.add_column("Verdict", no_wrap=True)
    table.add_column("Missing", style="yellow")

    for report in summary.reports:
        doc_cell = (
            escape(", ".join(report.document_files))
            if report.has_document
            else "[red]MISSING[/red]"
        )
        pt_cell = (
            escape(", ".join(report.pt_files))
            if report.has_packet_tracer
            else "[red]MISSING[/red]"
        )
        verdict_cell = _VERDICT_DISPLAY.get(report.verdict, report.verdict.name)
        missing_cell = escape(", ".join(report.missing_types)) if report.missing_types else "—"
        table.add_row(
            escape(report.student_name),
            doc_cell,
            pt_cell,
            verdict_cell,
            missing_cell,
        )
    console.print(table)

    templates_label = (
        "[green]Available[/green]"
        if summary.templates_found
        else "[yellow]Missing (generic fallback available with --allow-generic-fallback)[/yellow]"
    )
    summary_lines = [
        f"[bold]Students:[/bold] {len(summary.reports)}  "
        f"[green]Complete:[/green] {summary.complete_count}  "
        f"[yellow]Incomplete:[/yellow] {summary.incomplete_count}",
        f"[bold]Breakdown:[/bold] Partial {summary.partial_count}, "
        f"Not submitted {summary.not_submitted_count}, "
        f"No lab files {summary.no_lab_files_count}",
        f"[bold]Points possible:[/bold] {summary.points_possible:g}  "
        f"[bold]Templates:[/bold] {templates_label}",
    ]
    console.print(
        Panel("\n".join(summary_lines), title="Completeness Summary", border_style="cyan")
    )

    if grade_result is not None:
        if execute:
            console.print(
                f"[green]Auto-grade:[/green] graded {grade_result.graded_count}, "
                f"skipped {grade_result.skipped_count} (already graded), "
                f"failed {grade_result.failed_count}"
            )
        else:
            console.print(
                f"[cyan]DRY RUN — Auto-grade preview:[/cyan] would grade "
                f"{grade_result.planned_count} student(s) at {summary.points_possible:g} pts, "
                f"skip {grade_result.skipped_count} (already graded)"
            )
        for error in grade_result.errors:
            console.print(f"[red]  • {escape(error)}[/red]")

    if feedback_result is not None:
        if execute:
            console.print(
                f"[green]Feedback:[/green] posted {feedback_result.posted_count} comment(s), "
                f"replaced {feedback_result.replaced_count} old, "
                f"failed {feedback_result.failed_count}"
            )
        else:
            count = feedback_result.would_replace_count
            replace_note = (
                f" (would replace old comments for {count} student(s))" if count else ""
            )
            console.print(
                f"[cyan]DRY RUN — Feedback preview:[/cyan] would post "
                f"{feedback_result.planned_count} comment(s){replace_note}"
            )
        if feedback_result.used_generic_fallback:
            console.print(
                "[yellow]Warning: workspace templates missing; used generic fallback text.[/yellow]"
            )
        for error in feedback_result.errors:
            console.print(f"[red]  • {escape(error)}[/red]")

    if (grade_result is not None or feedback_result is not None) and not execute:
        console.print(
            "[yellow]DRY RUN — Nothing was written to Canvas (pass --execute to write).[/yellow]"
        )
    elif (grade_result is not None or feedback_result is not None) and execute and backup_dir:
        console.print(f"[green]Backups saved to:[/green] {backup_dir}")


def _emit_json_report(
    summary: PacketTracerCheckSummary,
    *,
    grade_result: AutoGradeResult | None,
    feedback_result: FeedbackPostResult | None,
    execute: bool,
    backup_dir: Path | None,
) -> None:
    """Emit structured JSON output for an assignment completeness run."""
    payload: dict[str, Any] = {
        "course_id": summary.course_id,
        "assignment_id": summary.assignment_id,
        "assignment_name": summary.assignment_name,
        "points_possible": summary.points_possible,
        "execute": execute,
        "templates_found": summary.templates_found,
        "summary": {
            "total_students": len(summary.reports),
            "complete_count": summary.complete_count,
            "incomplete_count": summary.incomplete_count,
            "partial_count": summary.partial_count,
            "not_submitted_count": summary.not_submitted_count,
            "no_lab_files_count": summary.no_lab_files_count,
        },
        "reports": [
            {
                "user_id": r.user_id,
                "student_name": r.student_name,
                "verdict": r.verdict.name.lower(),
                "has_document": r.has_document,
                "has_packet_tracer": r.has_packet_tracer,
                "document_files": list(r.document_files),
                "pt_files": list(r.pt_files),
                "other_files": list(r.other_files),
                "missing_types": list(r.missing_types),
            }
            for r in summary.reports
        ],
    }
    if execute and backup_dir is not None:
        payload["backup_directory"] = str(backup_dir)
    if grade_result is not None:
        payload["auto_grade"] = asdict(grade_result)
    if feedback_result is not None:
        payload["post_feedback"] = asdict(feedback_result)

    typer.echo(json.dumps(payload, indent=2, default=str))


def packet_tracer_command(
    ctx: typer.Context,
    course_id: int = typer.Option(..., "--course", "-c", help="Canvas course ID"),
    assignment_id: int | None = typer.Option(
        None,
        "--assignment",
        "-a",
        help="Packet Tracer assignment ID (lists detected PT assignments if omitted)",
    ),
    auto_grade: bool = typer.Option(
        False, "--auto-grade", help="Auto-grade complete submissions at 100%"
    ),
    post_feedback: bool = typer.Option(
        False,
        "--post-feedback",
        help="Post 2-part completeness feedback for incomplete submissions",
    ),
    allow_generic_fallback: bool = typer.Option(
        False,
        "--allow-generic-fallback",
        help="Allow posting generic feedback when workspace templates are missing",
    ),
    execute: bool = typer.Option(
        False,
        "--execute",
        help="Write grades/comments to Canvas (default is dry-run)",
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit structured JSON output"
    ),
) -> None:
    """Detect Packet Tracer labs, check completeness, post feedback, and auto-grade."""
    config: AppConfig = (ctx.obj or {}).get("config", AppConfig())

    if assignment_id is None and (auto_grade or post_feedback):
        _fail(
            "--assignment is required when using --auto-grade or --post-feedback.",
            title="Missing assignment ID",
            as_json=as_json,
        )

    workspace = resolve_workspace_directory(config)
    if execute and workspace is None:
        _fail(
            "No private workspace is configured; set COURSEWRIGHT_WORKSPACE or "
            "configure workspace so Canvas backups have somewhere to go.",
            title="No workspace for backups",
            as_json=as_json,
        )

    client = _connect_canvas(config, as_json=as_json)

    if assignment_id is None:
        try:
            with _activity(f"Scanning course {course_id} assignments", as_json=as_json):
                assignments = client.get_assignments(course_id)
            detected = PacketTracerAssignmentDetector().detect_in_course(assignments)
        except Exception as err:
            _fail(
                f"Failed to scan assignments for course {course_id}: {err}",
                title="Scan failed",
                as_json=as_json,
            )

        if as_json:
            payload = {
                "course_id": course_id,
                "total_assignments": len(assignments),
                "detected_count": len(detected),
                "assignments": [
                    {
                        "id": assignment.id,
                        "name": assignment.name,
                        "points_possible": assignment.points_possible,
                        "allowed_extensions": list(assignment.allowed_extensions),
                        "confidence": detection.confidence.value,
                        "matched_extensions": list(detection.matched_extensions),
                        "matched_patterns": list(detection.matched_patterns),
                    }
                    for assignment, detection in detected
                ],
            }
            typer.echo(json.dumps(payload, indent=2, default=str))
            return

        _render_detected_assignments(course_id, len(assignments), detected)
        return

    templates_dir = (
        workspace / config.templates.templates_directory if workspace is not None else None
    )

    try:
        with _activity(
            f"Checking Packet Tracer assignment {assignment_id}",
            as_json=as_json,
        ):
            summary = check_packet_tracer_assignment(
                client,
                course_id,
                assignment_id,
                templates_dir=templates_dir,
            )
    except Exception as err:
        _fail(
            f"Failed to check assignment {assignment_id}: {err}",
            title="Completeness check failed",
            as_json=as_json,
        )

    backup_dir: Path | None = (
        default_backup_directory(workspace) if workspace is not None else None
    )
    if execute:
        try:
            writer = create_safe_canvas_writer(
                client,
                execute=True,
                workspace_directory=workspace,
            )
        except SafeWriteError as err:
            _fail(str(err), title="No workspace for backups", as_json=as_json)
    else:
        writer = SafeCanvasWriter(client, backup_dir or Path("."), execute=False)

    grade_result: AutoGradeResult | None = None
    feedback_result: FeedbackPostResult | None = None

    try:
        if post_feedback:
            with _activity(
                f"Processing feedback for {summary.assignment_name}",
                as_json=as_json,
            ):
                feedback_result = post_completeness_feedback(
                    client,
                    writer,
                    course_id,
                    assignment_id,
                    summary.assignment_name,
                    summary.reports,
                    templates_dir=templates_dir,
                    allow_generic_fallback=allow_generic_fallback,
                    execute=execute,
                )

        if auto_grade:
            with _activity(
                f"Auto-grading complete submissions for {summary.assignment_name}",
                as_json=as_json,
            ):
                grade_result = auto_grade_complete_submissions(
                    client,
                    writer,
                    course_id,
                    assignment_id,
                    summary.assignment_name,
                    summary.points_possible,
                    summary.reports,
                    execute=execute,
                )
    except MissingTemplatesError:
        _fail(
            "Workspace feedback templates are missing; pass --allow-generic-fallback "
            f"or populate workspace templates/ ({', '.join(REQUIRED_FEEDBACK_TEMPLATES)}).",
            title="Missing feedback templates",
            as_json=as_json,
        )
    except Exception as err:
        _fail(
            f"Packet Tracer operation failed: {err}",
            title="Operation failed",
            as_json=as_json,
        )

    if as_json:
        _emit_json_report(
            summary,
            grade_result=grade_result,
            feedback_result=feedback_result,
            execute=execute,
            backup_dir=backup_dir,
        )
    else:
        _render_completeness_report(
            summary,
            grade_result=grade_result,
            feedback_result=feedback_result,
            execute=execute,
            backup_dir=backup_dir,
        )

    has_failures = (
        (grade_result is not None and grade_result.failed_count > 0)
        or (feedback_result is not None and feedback_result.failed_count > 0)
    )
    if has_failures:
        raise typer.Exit(1) from None
