"""The five course-design phases, as plain functions the TUI can call.

Each phase function is a thin wrapper (AGENTS.md 3.7): it calls the same
``coursewright.design`` library functions the ``coursewright design``
commands call, and turns the library's report into a ``PhaseTable`` --
a title, column names, rows and a few lines of summary -- that the
course-design screen renders in a ``DataTable``.

There is no Textual here, so these wrappers are easy to test and the
screen stays a matter of layout and key handling.

Safety (AGENTS.md 3.11):

* ``execute=False`` is a preview.  The safe writer records what *would*
  be sent and writes nothing, not even a backup.
* ``execute=True`` writes, and the library's safe writer takes a
  timestamped JSON backup into the private workspace first.
* Every write phase needs a workspace.  Without ``COURSEWRIGHT_WORKSPACE``
  the writer is refused up front (``SafeWriteError``), even for a
  preview, so the screen can say so plainly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from coursewright.core.canvas import create_safe_canvas_writer
from coursewright.core.canvas.safe_write import SafeWriteError, default_backup_directory
from coursewright.core.config import AppConfig, resolve_workspace_directory
from coursewright.design.dates.apply import apply_dates_to_course
from coursewright.design.dates.schedule import parse_schedule_file, parse_schedule_text
from coursewright.design.diagnostics import diagnose_course_by_id
from coursewright.design.discussions import create_discussions, select_unit_modules
from coursewright.design.discussions.creation import DEFAULT_POINTS
from coursewright.design.organization.unit_order import apply_unit_order, plan_unit_order
from coursewright.design.publishing.content import publish_course_content

NO_WORKSPACE_MESSAGE = (
    "No private workspace is configured. Set COURSEWRIGHT_WORKSPACE so "
    "Canvas backups have somewhere to go; nothing was written."
)


@dataclass
class PhaseTable:
    """What one phase run found, ready to show in a table."""

    title: str
    columns: tuple[str, ...]
    rows: list[tuple[str, ...]] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    executed: bool = False


@dataclass(frozen=True)
class PhaseOptions:
    """Inputs a phase may need beyond the course id."""

    schedule_file: Path | None = None
    schedule_content: str | None = None


@dataclass(frozen=True)
class DesignPhase:
    """One step of the five-phase flow."""

    key: str
    number: int
    title: str
    description: str
    writes: bool
    run: Callable[[Any, int, Path | None, PhaseOptions, bool], PhaseTable]


def require_workspace(config: AppConfig | None = None) -> Path:
    """Return the workspace for backups, or raise ``SafeWriteError``.

    Pass the session's selected ``config`` so a TUI launched with
    ``--config`` never borrows an unrelated ``config.yaml`` workspace.
    """
    workspace = resolve_workspace_directory(config)
    if workspace is None:
        raise SafeWriteError(NO_WORKSPACE_MESSAGE)
    return workspace


def _writer(client: Any, workspace: Path | None, execute: bool):
    if workspace is None:
        raise SafeWriteError(NO_WORKSPACE_MESSAGE)
    return create_safe_canvas_writer(client, execute=execute, workspace_directory=workspace)


def run_diagnose(client, course_id, _workspace, _options, _execute) -> PhaseTable:
    """Phase 1: read-only health check (``diagnose_course_by_id``)."""
    report = diagnose_course_by_id(client, course_id)
    table = PhaseTable(f"Course: {report.course_name}", ("Check", "Result"))
    table.rows = [
        ("Modules", str(report.total_modules)),
        ("Module items", str(report.total_items)),
        ("Assignments", str(report.total_assignments)),
        ("Unpublished modules", str(len(report.unpublished_modules))),
        ("Modules without unlock date", str(len(report.modules_without_unlock_dates))),
        ("Assignments without due date", str(len(report.missing_due_dates))),
        ("Past-due assignments", str(len(report.past_due_dates))),
    ]
    table.summary = list(report.issues) or ["No issues found."]
    return table


def run_organize(client, course_id, workspace, _options, execute) -> PhaseTable:
    """Phase 2: re-sort items in each unit module (``plan/apply_unit_order``)."""
    writer = _writer(client, workspace, execute)
    plan = plan_unit_order(client, course_id)
    result = apply_unit_order(plan, writer)
    table = PhaseTable("Module item moves", ("Module", "Item", "Old position", "New position"))
    table.rows = [
        (m.module_name, m.title, str(m.old_position), str(m.new_position)) for m in plan.moves
    ]
    table.summary = [f"{len(plan.moves)} item(s) to reposition."]
    table.executed = result.executed
    return table


def run_discussions(client, course_id, workspace, _options, execute) -> PhaseTable:
    """Phase 3: a discussion in each odd unit (``create_discussions``)."""
    writer = _writer(client, workspace, execute)
    modules = client.get_modules(course_id)
    wanted = select_unit_modules(modules)
    report = create_discussions(writer, course_id, modules, wanted, points=DEFAULT_POINTS)
    table = PhaseTable("Discussion topics", ("Module", "Title", "Points"))
    wanted_names = set(wanted)
    module_names = [m["name"] for m in modules if m["name"] in wanted_names]
    for name, outcome in zip(module_names, report.topic_outcomes, strict=False):
        table.rows.append((name, outcome.payload["title"], str(DEFAULT_POINTS)))
    table.summary = [f"{report.discussions_planned} discussion(s) planned."]
    table.errors = list(report.errors)
    table.executed = report.executed
    return table


def run_dates(client, course_id, workspace, options, execute) -> PhaseTable:
    """Phase 4: apply a schedule file's dates (``apply_dates_to_course``)."""
    if options.schedule_file is None:
        raise ValueError("Enter the path of a schedule file first.")
    warnings: list[str] = []
    schedule = (
        parse_schedule_text(options.schedule_content, warnings)
        if options.schedule_content is not None
        else parse_schedule_file(options.schedule_file, warnings)
    )
    writer = _writer(client, workspace, execute)
    report = apply_dates_to_course(client, writer, course_id, schedule)
    table = PhaseTable("Date changes", ("Kind", "Title", "Unlock at", "Due at"))
    table.rows = [
        (
            change.kind,
            change.title,
            change.updates.get("unlock_at", ""),
            change.updates.get("due_at", ""),
        )
        for change in report.changes
    ]
    table.summary = [f"{len(report.changes)} date change(s).", *warnings]
    if report.skipped_module_names:
        table.summary.append("No schedule row for: " + ", ".join(report.skipped_module_names))
    table.errors = list(report.errors)
    table.executed = writer.execute
    return table


def run_publish(client, course_id, workspace, _options, execute) -> PhaseTable:
    """Phase 5: publish modules and items (``publish_course_content``)."""
    if workspace is None:
        raise SafeWriteError(NO_WORKSPACE_MESSAGE)
    report = publish_course_content(
        client, course_id, default_backup_directory(workspace), execute=execute
    )
    table = PhaseTable(
        "Publishing plan", ("Module", "Publish module", "Item to publish", "Item id")
    )
    for planned in report.modules:
        status = "yes" if planned.publish_module else "already published"
        if not planned.items_to_publish:
            table.rows.append((planned.name, status, "(none)", ""))
            continue
        for index, item in enumerate(planned.items_to_publish):
            first = index == 0
            table.rows.append(
                (
                    planned.name if first else "",
                    status if first else "",
                    item.title,
                    str(item.item_id),
                )
            )
    table.summary = [f"{report.modules_to_publish} module(s), {report.items_to_publish} item(s)."]
    table.errors = list(report.errors)
    table.executed = report.executed
    return table


DESIGN_PHASES: tuple[DesignPhase, ...] = (
    DesignPhase("diagnose", 1, "Diagnose", "Read-only course health check.", False, run_diagnose),
    DesignPhase("organize", 2, "Organize", "Put unit module items in order.", True, run_organize),
    DesignPhase(
        "discussions",
        3,
        "Discussions",
        "Create a discussion in each odd unit.",
        True,
        run_discussions,
    ),
    DesignPhase(
        "dates", 4, "Dates", "Apply unlock and due dates from a schedule file.", True, run_dates
    ),
    DesignPhase("publish", 5, "Publish", "Publish modules and their items.", True, run_publish),
)
