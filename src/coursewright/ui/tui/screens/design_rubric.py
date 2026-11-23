"""Rubric and standards-audit screen for the current course.

Opened from the course-design screen (``u`` or the "Rubric audit"
button).  It shows two tables:

* the **rubric** as loaded by ``coursewright.design.standards.load_rubric``
  (a workspace copy wins over the bundled one); and
* the **audit** from ``audit_course``: one row per standard with its
  status -- Met, Not Met or Needs Human -- and the evidence found.

Loading the rubric reads local files only.  The audit reads Canvas and
never writes, so it runs as soon as the screen opens, under the
transmission log (docs/HOUSE_STYLE.md section 3).  ``r`` runs it again.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import TYPE_CHECKING

from rich.markup import escape
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Header, Static

from coursewright.core.config import resolve_workspace_directory
from coursewright.design.standards import (
    DEFAULT_RUBRIC_ID,
    AuditResult,
    AuditStatus,
    Rubric,
    RubricError,
    audit_course,
    load_rubric,
)
from coursewright.ui.tui.screens.base import CoursewrightScreen
from coursewright.ui.tui.widgets.help_bar import HelpBar
from coursewright.ui.tui.widgets.transmission_modal import TransmissionModal

if TYPE_CHECKING:
    from coursewright.ui.tui.state import AppState

# How each audit status is labelled and coloured in the results table.
STATUS_LABELS: dict[AuditStatus, tuple[str, str]] = {
    AuditStatus.MET: ("Met", "green"),
    AuditStatus.NOT_MET: ("Not Met", "red"),
    AuditStatus.NEEDS_A_HUMAN: ("Needs Human", "yellow"),
}


class RubricAuditScreen(CoursewrightScreen):
    """Show a rubric and how the current course measures against it."""

    DEFAULT_CSS = """
    .section-title { color: $success; text-style: bold; margin: 1 1 0 1; }
    #rubric-info, #audit-summary { margin: 0 1; height: auto; }
    #rubric-standards, #audit-results { height: auto; margin: 0 1; }
    """

    BINDINGS = [Binding("r", "run_audit", "Run audit again")]

    def __init__(self, app_state: AppState, rubric_id: str = DEFAULT_RUBRIC_ID) -> None:
        super().__init__()
        self.app_state = app_state
        self.rubric_id = rubric_id
        self.rubric: Rubric | None = None
        self.results: list[AuditResult] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="rubric-body"):
            yield Static("Rubric", classes="section-title")
            yield Static("", id="rubric-info")
            yield DataTable(id="rubric-standards", zebra_stripes=True)
            yield Static("Audit results", classes="section-title")
            yield Static("The audit has not run yet.", id="audit-summary")
            yield DataTable(id="audit-results", zebra_stripes=True)
        yield HelpBar()

    def on_mount(self) -> None:
        self.load_rubric_table()
        if self.rubric is not None:
            self.action_run_audit()

    def load_rubric_table(self) -> None:
        """Load the rubric from files and list its standards."""
        info = self.query_one("#rubric-info", Static)
        try:
            self.rubric = load_rubric(
                self.rubric_id, resolve_workspace_directory(self.app_state.config)
            )
        except RubricError as err:
            info.update(f"[red]Could not load rubric {escape(self.rubric_id)}: {escape(str(err))}")
            return
        rubric = self.rubric
        text_note = "" if rubric.text_included else " (reference only; follow the links)"
        info.update(
            Text(
                f"{rubric.name} — {rubric.publisher}, {rubric.license}{text_note}\n"
                f"{len(rubric.standards)} standard(s) in {len(rubric.sections)} section(s)."
            )
        )
        section_names = {section.id: section.name for section in rubric.sections}
        table = self.query_one("#rubric-standards", DataTable)
        table.add_columns("Standard", "Section", "Text or link")
        for standard in rubric.standards:
            table.add_row(
                standard.id,
                section_names.get(standard.section, standard.section),
                standard.text or standard.url,
            )

    def action_run_audit(self) -> None:
        self.run_audit()

    @work(exclusive=True)
    async def run_audit(self) -> None:
        """Audit the course (read-only) and fill the results table."""
        summary = self.query_one("#audit-summary", Static)
        client = self.app_state.canvas_client
        course_id = getattr(self.app_state.current_course, "id", None)
        if client is None or course_id is None:
            summary.update("[red]No Canvas connection or course selected.[/red]")
            return
        modal = TransmissionModal(f"Auditing course against {escape(self.rubric_id)}…")
        try:
            await self.app.push_screen(modal)
            self.results = await asyncio.to_thread(
                audit_course,
                client,
                int(course_id),
                rubric_id=self.rubric_id,
                workspace_directory=resolve_workspace_directory(self.app_state.config),
            )
        except Exception as err:  # shown, never swallowed
            message = str(err) or type(err).__name__
            if modal.is_mounted:
                modal.mark_error(message)
            summary.update(f"[red]Audit failed: {escape(message)}[/red]")
            return
        if modal.is_mounted:
            modal.dismiss(None)
        self.show_results()

    def show_results(self) -> None:
        """Render ``self.results`` with a count per status."""
        table = self.query_one("#audit-results", DataTable)
        table.clear(columns=True)
        table.add_columns("Standard", "Section", "Status", "Evidence or notes")
        for result in self.results:
            label, colour = STATUS_LABELS[result.status]
            details = ", ".join(result.evidence) or result.notes
            table.add_row(
                result.standard_id,
                result.section,
                Text(label, style=colour),
                details,
            )
        counts = Counter(result.status for result in self.results)
        self.query_one("#audit-summary", Static).update(
            " · ".join(
                f"[{colour}]{label}: {counts.get(status, 0)}[/{colour}]"
                for status, (label, colour) in STATUS_LABELS.items()
            )
            + "  [dim](read-only; nothing was written)[/dim]"
        )
