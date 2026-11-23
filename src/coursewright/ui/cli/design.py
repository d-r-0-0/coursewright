"""Course-design commands registered under ``coursewright design``.

One command group per phase of the five-phase workflow, plus ``audit``
(rubric) and ``convert``:

====================  ==========================================
``design diagnose``   Phase 1: read-only course health check
``design organize``   Phase 2: put module items in order
``design discussions``  Phase 3: create discussion topics
``design dates``      Phase 4: apply unlock and due dates
``design publish``    Phase 5: publish modules and items
``design audit``      read-only rubric audit of a course
``design convert``    local file conversion (names, captions)
====================  ==========================================

Every command is a thin wrapper (AGENTS.md 3.7): parse arguments, call
one library function, render the result with Rich.

House rules these commands follow:

* **Preview by default** (AGENTS.md 3.11).  A command that can write
  shows a Rich table of exactly what would change and stops.  Only
  ``--execute`` writes, and the library's safe writer takes a backup
  first.
* **Remote work is visible** (AGENTS.md 3.16).  Every Canvas call runs
  inside ``RemoteActivityDisplay``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from coursewright.core.canvas import create_canvas_client, create_safe_canvas_writer
from coursewright.core.canvas.safe_write import (
    SafeCanvasWriter,
    SafeWriteError,
    default_backup_directory,
)
from coursewright.core.config import AppConfig, resolve_workspace_directory
from coursewright.design.conversion import (
    CAPTIONS_SUBDIR,
    TRANSCRIPTS_SUBDIR,
    convert_caption_folder,
    rename_to_kebab_case,
)
from coursewright.design.dates.apply import apply_dates_to_course
from coursewright.design.dates.schedule import parse_schedule_file
from coursewright.design.diagnostics import (
    DiagnosticsError,
    diagnose_course_by_id,
)
from coursewright.design.discussions import create_discussions, select_unit_modules
from coursewright.design.discussions.creation import DEFAULT_LAST_UNIT, DEFAULT_POINTS
from coursewright.design.organization.unit_order import (
    NoModulesFoundError,
    apply_unit_order,
    plan_unit_order,
)
from coursewright.design.publishing._strict_reads import CanvasReadError
from coursewright.design.publishing.content import (
    first_module_names,
    publish_course_content,
)
from coursewright.design.standards import (
    DEFAULT_RUBRIC_ID,
    AuditStatus,
    EvidenceError,
    RubricError,
    audit_course,
)
from coursewright.ui.cli.activity import RemoteActivityDisplay

console = Console()

EXECUTE_HELP = "Really write to Canvas. Without it, only a preview is shown."

diagnose_app = typer.Typer(help="Phase 1: check a course shell (read-only).")
organize_app = typer.Typer(help="Phase 2: put modules and items in order.")
design_discussions_app = typer.Typer(help="Phase 3: create discussion topics.")
dates_app = typer.Typer(help="Phase 4: apply unlock and due dates.")
publish_app = typer.Typer(help="Phase 5: publish modules and items.")
convert_app = typer.Typer(help="Convert local files (names, captions).")


def register_design_commands(design_app: typer.Typer) -> None:
    """Register every design command group on ``design_app``."""
    design_app.add_typer(diagnose_app, name="diagnose")
    design_app.add_typer(organize_app, name="organize")
    design_app.add_typer(design_discussions_app, name="discussions")
    design_app.add_typer(dates_app, name="dates")
    design_app.add_typer(publish_app, name="publish")
    design_app.add_typer(convert_app, name="convert")
    design_app.command("audit")(audit)


# -- shared helpers ------------------------------------------------------


def fail(title: str, message: str) -> typer.Exit:
    """Show a red panel and return the Exit to raise."""
    console.print(Panel(message, title=title, border_style="red"))
    return typer.Exit(code=1)


def get_canvas_client(config: AppConfig) -> Any:
    """Return an authenticated Canvas client, or exit with a clear message."""
    try:
        client = create_canvas_client(
            base_url=config.discussion.canvas_url,
            api_token_file=config.discussion.api_token_file,
        )
    except Exception as err:
        raise fail("Canvas connection failed", str(err)) from err
    if client is None:
        raise fail("Canvas connection failed", "Could not authenticate to Canvas.")
    return client


def config_from(context: typer.Context) -> AppConfig:
    """The config the root callback loaded (defaults when absent)."""
    return (context.obj or {}).get("config", AppConfig())


def build_writer(client: Any, config: AppConfig, *, execute: bool) -> SafeCanvasWriter:
    """Build the safe writer; ``execute`` False means preview only."""
    try:
        return create_safe_canvas_writer(
            client,
            execute=execute,
            workspace_directory=resolve_workspace_directory(config),
        )
    except SafeWriteError as err:
        raise fail("No workspace for backups", str(err)) from err


def show_mode_banner(executed: bool, what_changed: str) -> None:
    """Say whether the table above is a preview or a finished write."""
    if executed:
        console.print(f"[bold green]Done.[/bold green] {what_changed}")
    else:
        console.print(
            Panel(
                f"{what_changed}\nNothing was changed. "
                "Re-run with [bold cyan]--execute[/bold cyan] to apply.",
                title="Preview (dry run)",
                border_style="yellow",
                expand=False,
            )
        )


def show_errors(errors: list[str]) -> None:
    """List every per-item failure; none may pass silently (rule 3.11)."""
    if not errors:
        return
    console.print(Panel("\n".join(errors), title=f"{len(errors)} problem(s)", border_style="red"))


def new_preview_table(title: str, *columns: str) -> Table:
    """A table in the house style for previews."""
    table = Table(title=title, title_justify="left", title_style="bold cyan")
    for column in columns:
        table.add_column(column, overflow="fold")
    return table


# -- Phase 1: diagnose ---------------------------------------------------


@diagnose_app.callback()
def diagnose_root() -> None:
    """Phase 1: check a course shell (read-only)."""


@diagnose_app.command("run")
def diagnose_run(
    context: typer.Context,
    course_id: int = typer.Argument(..., help="Canvas course id."),
) -> None:
    """Check modules, items and due dates. Reads Canvas; never writes."""
    client = get_canvas_client(config_from(context))
    try:
        with RemoteActivityDisplay(f"Diagnosing course {course_id}", console=console):
            report = diagnose_course_by_id(client, course_id)
    except DiagnosticsError as err:
        raise fail("Diagnosis failed", str(err)) from err

    summary = new_preview_table(f"Course: {report.course_name}", "Check", "Result")
    summary.add_row("Modules", str(report.total_modules))
    summary.add_row("Module items", str(report.total_items))
    summary.add_row("Assignments", str(report.total_assignments))
    summary.add_row("Unpublished modules", str(len(report.unpublished_modules)))
    summary.add_row("Modules without unlock date", str(len(report.modules_without_unlock_dates)))
    summary.add_row("Assignments without due date", str(len(report.missing_due_dates)))
    summary.add_row("Past-due assignments", str(len(report.past_due_dates)))
    console.print(summary)
    if report.issues:
        console.print(Panel("\n".join(report.issues), title="Issues", border_style="yellow"))
    else:
        console.print("[green]No issues found.[/green]")


# -- Phase 2: organize ---------------------------------------------------


@organize_app.callback()
def organize_root() -> None:
    """Phase 2: put modules and items in order."""


@organize_app.command("unit-order")
def organize_unit_order(
    context: typer.Context,
    course_id: int = typer.Argument(..., help="Canvas course id."),
    execute: bool = typer.Option(False, "--execute", help=EXECUTE_HELP),
) -> None:
    """Re-sort the items inside each unit module (preview unless --execute)."""
    config = config_from(context)
    client = get_canvas_client(config)
    writer = build_writer(client, config, execute=execute)
    try:
        with RemoteActivityDisplay(f"Planning unit order for course {course_id}", console=console):
            plan = plan_unit_order(client, course_id)
        table = new_preview_table(
            "Module item moves", "Module", "Item", "Old position", "New position"
        )
        for move in plan.moves:
            table.add_row(
                move.module_name,
                move.title,
                str(move.old_position),
                str(move.new_position),
            )
        console.print(table)
        with RemoteActivityDisplay(
            "Applying unit order" if execute else "Previewing unit order",
            total=len(plan.moves),
            console=console,
        ):
            result = apply_unit_order(plan, writer)
    except NoModulesFoundError as err:
        raise fail("No modules", str(err)) from err
    except SafeWriteError as err:
        raise fail("Write stopped", str(err)) from err
    show_mode_banner(result.executed, f"{len(plan.moves)} item(s) to reposition.")


# -- Phase 3: discussions ------------------------------------------------


@design_discussions_app.callback()
def design_discussions_root() -> None:
    """Phase 3: create discussion topics."""


@design_discussions_app.command("create")
def discussions_create(
    context: typer.Context,
    course_id: int = typer.Argument(..., help="Canvas course id."),
    last_unit: int = typer.Option(DEFAULT_LAST_UNIT, help="Highest unit number to cover."),
    points: int = typer.Option(DEFAULT_POINTS, help="Points possible per discussion."),
    execute: bool = typer.Option(False, "--execute", help=EXECUTE_HELP),
) -> None:
    """Create a discussion in each odd-numbered unit (preview unless --execute)."""
    config = config_from(context)
    client = get_canvas_client(config)
    writer = build_writer(client, config, execute=execute)
    try:
        with RemoteActivityDisplay(f"Creating discussions in course {course_id}", console=console):
            modules = client.get_modules(course_id)
            wanted = select_unit_modules(modules, last_unit=last_unit)
            report = create_discussions(writer, course_id, modules, wanted, points=points)
    except SafeWriteError as err:
        raise fail("Write stopped", str(err)) from err

    table = new_preview_table("Discussion topics", "Module", "Title", "Points")
    modules_by_position = [m["name"] for m in modules if m["name"] in set(wanted)]
    for module_name, outcome in zip(modules_by_position, report.topic_outcomes, strict=False):
        table.add_row(module_name, outcome.payload["title"], str(points))
    console.print(table)
    show_errors(report.errors)
    show_mode_banner(report.executed, f"{report.discussions_planned} discussion(s) planned.")


# -- Phase 4: dates ------------------------------------------------------


@dates_app.callback()
def dates_root() -> None:
    """Phase 4: apply unlock and due dates."""


@dates_app.command("apply")
def dates_apply(
    context: typer.Context,
    course_id: int = typer.Argument(..., help="Canvas course id."),
    schedule_file: Path = typer.Argument(..., help="Schedule file (see design.dates.schedule)."),
    execute: bool = typer.Option(False, "--execute", help=EXECUTE_HELP),
) -> None:
    """Apply a schedule's unlock and due dates (preview unless --execute)."""
    try:
        warnings: list[str] = []
        schedule = parse_schedule_file(schedule_file, warnings)
    except (FileNotFoundError, ValueError) as err:
        raise fail("Schedule problem", str(err)) from err
    for warning in warnings:
        console.print(f"[yellow]{warning}[/yellow]")

    config = config_from(context)
    client = get_canvas_client(config)
    writer = build_writer(client, config, execute=execute)
    try:
        with RemoteActivityDisplay(f"Applying dates to course {course_id}", console=console):
            report = apply_dates_to_course(client, writer, course_id, schedule)
    except SafeWriteError as err:
        raise fail("Write stopped", str(err)) from err

    table = new_preview_table("Date changes", "Kind", "Title", "Unlock at", "Due at")
    for change in report.changes:
        table.add_row(
            change.kind,
            change.title,
            change.updates.get("unlock_at", ""),
            change.updates.get("due_at", ""),
        )
    console.print(table)
    if report.skipped_module_names:
        console.print(
            "[dim]No schedule row for: " + ", ".join(report.skipped_module_names) + "[/dim]"
        )
    show_errors(report.errors)
    show_mode_banner(writer.execute, f"{len(report.changes)} date change(s).")


# -- Phase 5: publish ----------------------------------------------------


@publish_app.callback()
def publish_root() -> None:
    """Phase 5: publish modules and items."""


@publish_app.command("content")
def publish_content(
    context: typer.Context,
    course_id: int = typer.Argument(..., help="Canvas course id."),
    first: int | None = typer.Option(None, help="Only the first N modules."),
    execute: bool = typer.Option(False, "--execute", help=EXECUTE_HELP),
) -> None:
    """Publish modules and their items (preview unless --execute)."""
    config = config_from(context)
    client = get_canvas_client(config)
    workspace = resolve_workspace_directory(config)
    if workspace is None:
        raise fail(
            "No workspace for backups",
            "Set COURSEWRIGHT_WORKSPACE so Canvas backups have somewhere to go.",
        )
    try:
        with RemoteActivityDisplay(f"Publishing course {course_id}", console=console):
            module_names = None
            if first is not None:
                module_names = set(first_module_names(client.get_modules(course_id), first))
            report = publish_course_content(
                client,
                course_id,
                default_backup_directory(workspace),
                module_names=module_names,
                execute=execute,
            )
    except ValueError as err:
        raise fail("Bad --first", str(err)) from err
    except (SafeWriteError, CanvasReadError) as err:
        raise fail("Publishing stopped", str(err)) from err

    # One row per item that would be published, so the instructor sees
    # exactly which pages, assignments and files go live.  The module
    # name and its own publish flag appear on the module's first row
    # only; a module with nothing left to publish still gets a row.
    table = new_preview_table(
        "Publishing plan", "Module", "Publish module", "Item to publish", "Item id"
    )
    for planned in report.modules:
        module_status = "yes" if planned.publish_module else "already published"
        if not planned.items_to_publish:
            table.add_row(planned.name, module_status, "[dim](none)[/dim]", "")
            continue
        for index, item in enumerate(planned.items_to_publish):
            table.add_row(
                planned.name if index == 0 else "",
                module_status if index == 0 else "",
                item.title,
                str(item.item_id),
            )
    console.print(table)
    show_errors(report.errors)
    show_mode_banner(
        report.executed,
        f"{report.modules_to_publish} module(s), {report.items_to_publish} item(s).",
    )


# -- audit ---------------------------------------------------------------


def audit(
    context: typer.Context,
    course_id: int = typer.Argument(..., help="Canvas course id."),
    rubric: str = typer.Option(DEFAULT_RUBRIC_ID, help="Rubric id; see `design rubrics`."),
) -> None:
    """Audit a course against a rubric. Reads Canvas; never writes."""
    config = config_from(context)
    client = get_canvas_client(config)
    try:
        with RemoteActivityDisplay(f"Auditing course {course_id}", console=console):
            results = audit_course(
                client,
                course_id,
                rubric_id=rubric,
                workspace_directory=resolve_workspace_directory(config),
            )
    except (DiagnosticsError, RubricError, EvidenceError) as err:
        raise fail("Audit failed", str(err)) from err

    styles = {
        AuditStatus.MET: "green",
        AuditStatus.NOT_MET: "red",
        AuditStatus.NEEDS_A_HUMAN: "yellow",
    }
    table = new_preview_table(f"Audit against {rubric}", "Standard", "Section", "Status")
    for result in results:
        style = styles[result.status]
        table.add_row(
            result.standard_id, result.section, f"[{style}]{result.status.value}[/{style}]"
        )
    console.print(table)


# -- convert (local files; no Canvas) ------------------------------------


@convert_app.callback()
def convert_root() -> None:
    """Convert local files (names, captions)."""


@convert_app.command("names")
def convert_names(
    folder: Path = typer.Argument(..., help="Folder whose contents are renamed."),
    execute: bool = typer.Option(False, "--execute", help="Really rename. Default: preview."),
) -> None:
    """Rename files and folders to kebab-case (preview unless --execute)."""
    if not folder.is_dir():
        raise fail("Not a folder", f"{folder} is not a folder.")
    outcomes = rename_to_kebab_case(folder, execute=execute)
    table = new_preview_table("Renames", "From", "To", "Status")
    for outcome in outcomes:
        table.add_row(outcome.old_path.name, outcome.new_path.name, outcome.status)
    console.print(table)
    show_mode_banner(execute, f"{len(outcomes)} name(s) to change.")


@convert_app.command("captions")
def convert_captions(
    materials_folder: Path = typer.Argument(..., help=f"Folder holding {CAPTIONS_SUBDIR}/."),
    viewer_url_prefix: str = typer.Option("", help="Viewer address up to and including id=."),
    execute: bool = typer.Option(False, "--execute", help="Really write. Default: preview."),
) -> None:
    """Turn caption files into transcripts (preview unless --execute)."""
    captions_folder = materials_folder / CAPTIONS_SUBDIR
    sources = sorted(captions_folder.glob("*.txt")) if captions_folder.is_dir() else []
    if not sources:
        raise fail("Nothing to convert", f"No caption files in {captions_folder}.")
    table = new_preview_table("Caption files", "Source", "Written into")
    for source in sources:
        table.add_row(source.name, f"{TRANSCRIPTS_SUBDIR}/")
    console.print(table)
    written: list[Path] = []
    if execute:
        written = convert_caption_folder(materials_folder, viewer_url_prefix=viewer_url_prefix)
    show_mode_banner(execute, f"{len(written) if execute else len(sources)} transcript(s).")
