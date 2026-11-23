"""``coursewright ta honor-credit``: full credit for submitted honor-system work.

Thin CLI wrapper over ``coursewright.ta.gradeliner.honor_credit``.
Dry-run by default: previews who would get credit, and only writes when
``--execute`` is passed (via ``SafeCanvasWriter``).
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from coursewright.core.canvas import create_canvas_client, create_safe_canvas_writer
from coursewright.core.canvas.safe_write import default_backup_directory
from coursewright.core.config import AppConfig, resolve_workspace_directory
from coursewright.ta.gradeliner.honor_credit import (
    DEFAULT_HONOR_CREDIT_COMMENT,
    HonorCreditPlan,
    execute_honor_credit_plan,
    plan_honor_credit,
)
from coursewright.ui.cli.activity import RemoteActivityDisplay

console = Console()


def _emit_error(
    message: str,
    *,
    as_json: bool,
    title: str = "Honor credit failed",
    course_id: int | None = None,
) -> typer.Exit:
    """Print an error as clean JSON or a Rich panel and return Exit(1)."""
    if as_json:
        payload: dict[str, Any] = {"error": message, "executed": False}
        if course_id is not None:
            payload["course_id"] = course_id
        typer.echo(json.dumps(payload, indent=2, default=str))
    else:
        console.print(Panel(message, title=f"[red]{title}[/red]", border_style="red"))
    return typer.Exit(1)


def _activity_scope(description: str, *, as_json: bool) -> contextlib.AbstractContextManager[Any]:
    """Return RemoteActivityDisplay in Rich mode, or a silent no-op in JSON mode."""
    if as_json:
        return contextlib.nullcontext()
    return RemoteActivityDisplay(description, console=console)


def _build_json_payload(
    plan: HonorCreditPlan,
    *,
    executed: bool,
    scores_written: int | None = None,
    assignments_touched: int | None = None,
    backup_directory: Path | None = None,
) -> dict[str, Any]:
    """Serialize *plan* (and optional execution results) for ``--json`` output."""
    payload: dict[str, Any] = {
        "course_id": plan.course_id,
        "executed": executed,
        "include_existing_scores": plan.include_existing_scores,
        "skipped_unpublished": plan.skipped_unpublished,
        "skipped_without_points": plan.skipped_without_points,
        "total_credit_rows": plan.total_credit_rows,
        "total_replacing_existing_score": plan.total_replacing_existing_score,
        "assignments": [
            {
                "assignment_id": item.assignment_id,
                "assignment_name": item.assignment_name,
                "points_possible": item.points_possible,
                "credit_count": len(item.credit_rows),
                "already_graded_count": item.already_graded_count,
                "not_submitted_count": item.not_submitted_count,
                "excused_count": item.excused_count,
                "skipped_inactive_or_test": item.skipped_inactive_or_test,
                "skipped_existing_score": item.skipped_existing_score,
                "credit_rows": [
                    {
                        "user_id": row.user_id,
                        "student_name": row.student_name,
                        "points_awarded": row.points_awarded,
                        "previous_score": row.previous_score,
                    }
                    for row in item.credit_rows
                ],
            }
            for item in plan.assignments
        ],
    }
    if executed:
        payload["scores_written"] = scores_written
        payload["assignments_touched"] = assignments_touched
        payload["backup_directory"] = str(backup_directory) if backup_directory else None
    return payload


def _render_plan_summary(plan: HonorCreditPlan) -> None:
    """Render the Rich preview table and the notes that explain it."""
    if not plan.assignments:
        console.print("[yellow]No matching assignments found.[/yellow]")
    else:
        table = Table(
            title=f"Honor Credit Preview — Course {plan.course_id}",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("ID", justify="right", style="dim", no_wrap=True)
        table.add_column("Assignment", style="cyan", overflow="fold")
        table.add_column("Points", justify="right", no_wrap=True)
        table.add_column("Full Credit", justify="right", style="bold green", no_wrap=True)
        table.add_column("Replaces Score", justify="right", style="yellow", no_wrap=True)
        table.add_column("Graded", justify="right", no_wrap=True)
        table.add_column("Not Submitted", justify="right", no_wrap=True)
        for item in plan.assignments:
            table.add_row(
                str(item.assignment_id),
                item.assignment_name,
                f"{item.points_possible:g}",
                str(len(item.credit_rows)),
                str(sum(row.replaces_existing_score for row in item.credit_rows)),
                str(item.already_graded_count),
                str(item.not_submitted_count),
            )
        console.print(table)

    if plan.total_replacing_existing_score:
        console.print(
            Panel(
                f"{plan.total_replacing_existing_score} submission(s) were turned in after an "
                "earlier score (usually a past-due zero) and would be raised to full credit. "
                "Pass --new-only to leave those scores alone.",
                title="[yellow]Late Submissions Included[/yellow]",
                border_style="yellow",
            )
        )
    skipped_existing = sum(item.skipped_existing_score for item in plan.assignments)
    if skipped_existing:
        console.print(
            f"[dim]Left {skipped_existing} resubmission(s) at their existing score "
            "(--new-only).[/dim]"
        )
    if plan.skipped_unpublished:
        console.print(f"[dim]Skipped {plan.skipped_unpublished} unpublished assignment(s).[/dim]")
    if plan.skipped_without_points:
        console.print(
            f"[dim]Skipped {plan.skipped_without_points} assignment(s) worth no points.[/dim]"
        )


def honor_credit_command(
    ctx: typer.Context,
    course_id: int = typer.Option(..., "--course", "-c", help="Canvas course ID"),
    assignments: list[int] = typer.Option(  # noqa: B008 — Typer option declaration
        [], "--assignment", "-a", help="Specific assignment ID(s); repeatable"
    ),
    match: str | None = typer.Option(
        None, "--match", "-m", help="Only assignments whose name contains this text"
    ),
    points: float | None = typer.Option(
        None, "--points", help="Only assignments worth exactly this many points"
    ),
    new_only: bool = typer.Option(
        False, "--new-only", help="Leave resubmissions that already have a score alone"
    ),
    comment: str = typer.Option(
        DEFAULT_HONOR_CREDIT_COMMENT, "--comment", help="Comment posted with each score"
    ),
    execute: bool = typer.Option(
        False, "--execute", help="Write scores to Canvas (default is dry-run)"
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit structured JSON output"),
) -> None:
    """Give full credit to submitted honor-system work (dry-run by default)."""
    config: AppConfig = (ctx.obj or {}).get("config", AppConfig())
    if not assignments and not (match and match.strip()):
        raise _emit_error(
            "Say which assignments are on the honor system: pass --assignment ID "
            "(repeatable) or --match TEXT, optionally narrowed with --points.",
            as_json=as_json,
            title="No assignments selected",
            course_id=course_id,
        )

    try:
        with _activity_scope("Connecting to Canvas", as_json=as_json):
            canvas_client = create_canvas_client(
                base_url=config.discussion.canvas_url,
                api_token_file=config.discussion.api_token_file,
            )
    except Exception as err:
        raise _emit_error(
            f"Failed to connect to Canvas: {err}",
            as_json=as_json,
            title="Canvas connection failed",
        ) from None
    if canvas_client is None:
        raise _emit_error(
            "Could not initialize Canvas client.",
            as_json=as_json,
            title="Canvas connection failed",
        )

    try:
        with _activity_scope(f"Inspecting submissions for course {course_id}", as_json=as_json):
            plan = plan_honor_credit(
                canvas_client,
                course_id,
                assignment_ids=assignments or None,
                name_contains=match,
                points_possible=points,
                include_existing_scores=not new_only,
            )
    except Exception as err:
        raise _emit_error(
            str(err),
            as_json=as_json,
            title="Failed to inspect submissions",
            course_id=course_id,
        ) from None

    if not as_json:
        _render_plan_summary(plan)

    if not execute:
        if as_json:
            typer.echo(json.dumps(_build_json_payload(plan, executed=False), indent=2, default=str))
        else:
            console.print(
                f"\n[yellow]DRY RUN — {plan.total_credit_rows} submission(s) would receive "
                "full credit (pass --execute to write).[/yellow]"
            )
        return

    workspace = resolve_workspace_directory(config)
    if workspace is None:
        raise _emit_error(
            "No private workspace is configured; set COURSEWRIGHT_WORKSPACE or "
            "workspace_directory so Canvas backups have somewhere to go.",
            as_json=as_json,
            title="Workspace required",
            course_id=course_id,
        )

    backup_directory = default_backup_directory(workspace)
    try:
        writer = create_safe_canvas_writer(
            canvas_client, execute=True, workspace_directory=workspace
        )
        with _activity_scope("Writing honor credit to Canvas", as_json=as_json):
            scores_written, assignments_touched = execute_honor_credit_plan(
                writer, plan, comment=comment
            )
    except Exception as err:
        raise _emit_error(str(err), as_json=as_json, course_id=course_id) from None

    if as_json:
        payload = _build_json_payload(
            plan,
            executed=True,
            scores_written=scores_written,
            assignments_touched=assignments_touched,
            backup_directory=backup_directory,
        )
        typer.echo(json.dumps(payload, indent=2, default=str))
        return

    console.print(
        f"\n[green]Gave full credit to [bold]{scores_written}[/bold] submission(s) "
        f"across [bold]{assignments_touched}[/bold] assignment(s).[/green]"
    )
    console.print(f"[dim]Backups saved in: {backup_directory}[/dim]", soft_wrap=True)
