"""``coursewright ta zero-ungraded``: guardrailed zeroing of ungraded work.

Thin CLI wrapper over ``coursewright.ta.gradeliner.zero_ungraded``.
Dry-run by default: previews targets and safety warnings, and only writes
when ``--execute`` is passed (via ``SafeCanvasWriter``).
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
from coursewright.core.canvas.safe_write import SafeWriteError, default_backup_directory
from coursewright.core.config import AppConfig, resolve_workspace_directory
from coursewright.ta.gradeliner.zero_ungraded import (
    ZeroUngradedPlan,
    execute_zero_ungraded_plan,
    plan_zero_ungraded,
)
from coursewright.ui.cli.activity import RemoteActivityDisplay

console = Console()


def _emit_error(
    message: str,
    *,
    as_json: bool,
    title: str = "Zero ungraded failed",
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


def _connect_canvas(config: AppConfig, *, as_json: bool) -> Any:
    """Connect to Canvas or exit cleanly with code 1."""
    try:
        with _activity_scope("Connecting to Canvas", as_json=as_json):
            client = create_canvas_client(
                base_url=config.discussion.canvas_url,
                api_token_file=config.discussion.api_token_file,
            )
    except Exception as err:
        raise _emit_error(
            f"Failed to connect to Canvas: {err}",
            as_json=as_json,
            title="Canvas connection failed",
        ) from None

    if client is None:
        raise _emit_error(
            "Could not initialize Canvas client.",
            as_json=as_json,
            title="Canvas connection failed",
        )
    return client


def _build_json_payload(
    plan: ZeroUngradedPlan,
    *,
    executed: bool,
    total_zeroed: int | None = None,
    assignments_touched: int | None = None,
    backup_directory: Path | None = None,
) -> dict[str, Any]:
    """Serialize *plan* (and optional execution results) for ``--json`` output."""
    warnings: list[str] = []
    if plan.total_skipped_submitted > 0:
        warnings.append(
            f"Skipped {plan.total_skipped_submitted} submitted-but-ungraded submission(s); "
            "pass --include-submitted to zero them."
        )
    if plan.all_ungraded_assignment_ids:
        warnings.append(
            f"{len(plan.all_ungraded_assignment_ids)} assignment(s) have 0 graded "
            "submissions (all-ungraded guardrail triggered)."
        )

    payload: dict[str, Any] = {
        "course_id": plan.course_id,
        "executed": executed,
        "past_due_only": plan.past_due_only,
        "include_unpublished": plan.include_unpublished,
        "include_submitted": plan.include_submitted,
        "active_student_count": (
            len(plan.active_user_ids) if plan.active_user_ids is not None else None
        ),
        "skipped_unpublished": plan.skipped_unpublished,
        "total_targets": plan.total_targets,
        "total_graded": plan.total_graded,
        "total_excused": plan.total_excused,
        "total_skipped_submitted": plan.total_skipped_submitted,
        "total_skipped_inactive_or_test": plan.total_skipped_inactive_or_test,
        "all_ungraded_assignment_ids": list(plan.all_ungraded_assignment_ids),
        "warnings": warnings,
        "assignments": [
            {
                "assignment_id": item.assignment_id,
                "assignment_name": item.assignment_name,
                "due_at": item.due_at.isoformat() if item.due_at else None,
                "published": item.published,
                "graded_count": item.graded_count,
                "excused_count": item.excused_count,
                "skipped_inactive_or_test": item.skipped_inactive_or_test,
                "skipped_submitted": item.skipped_submitted,
                "target_count": len(item.zero_targets),
                "is_all_ungraded": item.is_all_ungraded,
                "targets": [
                    {
                        "user_id": row.user_id,
                        "student_name": row.student_name,
                        "workflow_state": row.workflow_state,
                        "is_submitted": row.is_submitted,
                    }
                    for row in item.zero_targets
                ],
            }
            for item in plan.assignments
        ],
    }
    if executed:
        payload["total_zeroed"] = total_zeroed
        payload["assignments_touched"] = assignments_touched
        payload["backup_directory"] = str(backup_directory) if backup_directory else None
    return payload


def _render_plan_summary(plan: ZeroUngradedPlan) -> None:
    """Render the Rich preview table and any guardrail warnings."""
    if plan.assignments:
        table = Table(
            title=f"Zero Ungraded Preview — Course {plan.course_id}",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("ID", justify="right", style="dim", no_wrap=True)
        table.add_column("Assignment", style="cyan", overflow="fold")
        table.add_column("Due", style="white", no_wrap=True)
        table.add_column("Graded", justify="right", style="green", no_wrap=True)
        table.add_column("Sub Skip", justify="right", style="yellow", no_wrap=True)
        table.add_column("To Zero", justify="right", style="bold", no_wrap=True)
        table.add_column("Flags", style="yellow")

        for item in plan.assignments:
            flags: list[str] = []
            if item.is_all_ungraded:
                flags.append("ALL UNGRADED")
            if item.skipped_submitted > 0:
                flags.append("SUBMITTED SKIPPED")
            table.add_row(
                str(item.assignment_id),
                item.assignment_name,
                item.due_at.strftime("%Y-%m-%d") if item.due_at else "—",
                str(item.graded_count),
                str(item.skipped_submitted),
                str(len(item.zero_targets)),
                ", ".join(flags) if flags else "OK",
            )
        console.print(table)
        console.print(
            f"[dim]Totals: {plan.total_targets} to zero · {plan.total_graded} graded · "
            f"{plan.total_excused} excused · {plan.total_skipped_submitted} submitted skipped · "
            f"{plan.total_skipped_inactive_or_test} inactive/test skipped[/dim]",
            soft_wrap=True,
        )
    else:
        console.print("[yellow]No matching assignments found.[/yellow]")

    if plan.skipped_unpublished > 0:
        console.print(f"[dim]Skipped {plan.skipped_unpublished} unpublished assignment(s).[/dim]")

    if any(item.skipped_submitted > 0 for item in plan.assignments):
        console.print(
            Panel(
                f"Skipped {plan.total_skipped_submitted} submitted-but-ungraded submission(s) "
                "so student work is not overwritten with 0. Pass --include-submitted to include "
                "them.",
                title="[yellow]Submitted Work Skipped[/yellow]",
                border_style="yellow",
            )
        )

    all_ungraded_items = [item for item in plan.assignments if item.is_all_ungraded]
    if all_ungraded_items:
        names = ", ".join(
            f"{item.assignment_name} (#{item.assignment_id})" for item in all_ungraded_items
        )
        console.print(
            Panel(
                f"Assignment(s) with 0 graded submissions: {names}. Verify you intend to "
                "zero an assignment that has no graded work yet.",
                title="[yellow]All-Ungraded Guardrail[/yellow]",
                border_style="yellow",
            )
        )


def zero_ungraded_command(
    ctx: typer.Context,
    course_id: int = typer.Option(..., "--course", "-c", help="Canvas course ID"),
    assignments: list[int] = typer.Option(  # noqa: B008 — Typer option declaration
        [], "--assignment", "-a", help="Specific assignment ID(s); repeatable"
    ),
    past_due: bool = typer.Option(
        True, "--past-due/--all", help="Only inspect past-due published assignments"
    ),
    include_submitted: bool = typer.Option(
        False, "--include-submitted", help="Also zero submitted-but-ungraded work"
    ),
    execute: bool = typer.Option(
        False, "--execute", help="Write zeros to Canvas (default is dry-run)"
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit structured JSON output"),
) -> None:
    """Preview or zero out ungraded submissions for a Canvas course."""
    config: AppConfig = (ctx.obj or {}).get("config", AppConfig())
    canvas_client = _connect_canvas(config, as_json=as_json)

    target_assignment_ids = assignments or None
    try:
        with _activity_scope(
            f"Inspecting assignments for course {course_id}", as_json=as_json
        ):
            plan = plan_zero_ungraded(
                canvas_client,
                course_id,
                assignment_ids=target_assignment_ids,
                past_due_only=past_due,
                include_submitted=include_submitted,
            )
    except Exception as err:
        raise _emit_error(
            str(err),
            as_json=as_json,
            title="Failed to inspect assignments",
            course_id=course_id,
        ) from None

    if not as_json:
        _render_plan_summary(plan)

    if not execute:
        if as_json:
            typer.echo(json.dumps(_build_json_payload(plan, executed=False), indent=2, default=str))
        else:
            console.print(
                f"\n[yellow]DRY RUN — {plan.total_targets} submission(s) would be zeroed "
                "(pass --execute to write).[/yellow]"
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

    backup_dir = default_backup_directory(workspace)
    try:
        writer = create_safe_canvas_writer(
            canvas_client,
            execute=True,
            workspace_directory=workspace,
        )
        with _activity_scope("Zeroing ungraded submissions in Canvas", as_json=as_json):
            total_zeroed, assignments_touched = execute_zero_ungraded_plan(
                canvas_client,
                writer,
                plan,
                execute=True,
                include_submitted=include_submitted,
            )
    except (SafeWriteError, Exception) as err:
        raise _emit_error(
            str(err),
            as_json=as_json,
            title="Zero ungraded failed",
            course_id=course_id,
        ) from None

    if as_json:
        payload = _build_json_payload(
            plan,
            executed=True,
            total_zeroed=total_zeroed,
            assignments_touched=assignments_touched,
            backup_directory=backup_dir,
        )
        typer.echo(json.dumps(payload, indent=2, default=str))
        return

    console.print(
        f"\n[green]Zeroed [bold]{total_zeroed}[/bold] submission(s) "
        f"across [bold]{assignments_touched}[/bold] assignment(s).[/green]"
    )
    console.print(f"[dim]Backups saved in: {backup_dir}[/dim]", soft_wrap=True)
