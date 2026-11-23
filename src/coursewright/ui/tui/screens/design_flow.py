"""Course-design screen: the five-phase flow for one Canvas course.

Opened from the course list with ``w``.  One phase is shown at a time;
the phase buttons (or keys ``1``-``5``, ``n`` next, ``b`` back a phase)
move between them.

Every phase works in two explicit steps:

1. **Preview** (``v``) runs the phase in dry-run mode and fills the
   table with exactly what would change.  Nothing is written.
2. **Apply** (``a``) is offered only for the four write phases, only
   after a preview, and only when a private workspace is configured
   for backups.  It always asks first through ``ConfirmModal``; saying
   no writes nothing.

The work itself lives in ``design_phases`` (thin wrappers over
``coursewright.design``), so this screen holds layout and keys only.
Every Canvas call runs under ``TransmissionModal``, which shows the
live traffic log (docs/HOUSE_STYLE.md section 3).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from rich.markup import escape
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, DataTable, Header, Input, Static

from coursewright.core.canvas.review import ReviewChangedError, ReviewedCanvasInputs
from coursewright.core.canvas.safe_write import SafeWriteError
from coursewright.core.config import resolve_workspace_directory
from coursewright.ui.tui.screens.base import CoursewrightScreen
from coursewright.ui.tui.screens.design_phases import (
    DESIGN_PHASES,
    DesignPhase,
    PhaseOptions,
    PhaseTable,
    require_workspace,
)
from coursewright.ui.tui.widgets.confirm_modal import ConfirmModal
from coursewright.ui.tui.widgets.help_bar import HelpBar
from coursewright.ui.tui.widgets.transmission_modal import TransmissionModal

if TYPE_CHECKING:
    from coursewright.ui.tui.state import AppState


class CourseDesignScreen(CoursewrightScreen):
    """Preview and apply the five design phases for the current course."""

    DEFAULT_CSS = """
    #design-title { color: $success; text-style: bold; margin: 0 1; }
    #phase-bar, #action-bar { height: auto; margin: 0 1; }
    #phase-bar Button.current-phase { text-style: bold reverse; }
    #phase-description, #phase-summary { margin: 0 1; height: auto; }
    #schedule-file { margin: 0 1; }
    #phase-table { height: auto; margin: 0 1; }
    """

    BINDINGS = [
        *(
            Binding(str(p.number), f"select_phase({p.number - 1})", p.title, show=False)
            for p in DESIGN_PHASES
        ),
        Binding("n", "next_phase", "Next phase"),
        Binding("b", "previous_phase", "Prev phase"),
        Binding("v", "preview", "Preview"),
        Binding("a", "apply", "Apply"),
        Binding("u", "open_rubric_audit", "Rubric audit"),
        # Not a priority binding (unlike the base screen), so a "q" typed
        # into the schedule-file path stays in the path instead of quitting.
        Binding("q", "request_quit", "Quit", priority=False),
    ]

    def __init__(self, app_state: AppState) -> None:
        super().__init__()
        self.app_state = app_state
        self.phase_index = 0
        # The latest preview per phase key.  Apply needs one to exist.
        self.previews: dict[str, PhaseTable] = {}
        self.reviewed_inputs: dict[str, tuple[ReviewedCanvasInputs, PhaseOptions, int]] = {}
        self.busy = False

    # -- layout ----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="design-body"):
            yield Static(Text(f"Course design: {self.course_name}"), id="design-title")
            with Horizontal(id="phase-bar"):
                for phase in DESIGN_PHASES:
                    yield Button(f"{phase.number} {phase.title}", id=f"phase-{phase.key}")
                yield Button("Rubric audit", id="open-rubric-audit", variant="primary")
            yield Static("", id="phase-description")
            yield Input(placeholder="Schedule file path (tab-separated)", id="schedule-file")
            with Horizontal(id="action-bar"):
                yield Button("Preview", id="preview-button", variant="primary")
                yield Button("Apply…", id="apply-button", variant="warning")
            yield DataTable(id="phase-table", zebra_stripes=True)
            yield Static("", id="phase-summary")
        yield HelpBar()

    def on_mount(self) -> None:
        self.show_phase(0)

    @property
    def course_name(self) -> str:
        return str(getattr(self.app_state.current_course, "name", "(no course)"))

    @property
    def course_id(self) -> int | None:
        course_id = getattr(self.app_state.current_course, "id", None)
        return int(course_id) if course_id is not None else None

    @property
    def current_phase(self) -> DesignPhase:
        return DESIGN_PHASES[self.phase_index]

    # -- phase navigation ------------------------------------------------

    def show_phase(self, index: int) -> None:
        """Switch to phase ``index`` and show its last preview, if any."""
        self.phase_index = index % len(DESIGN_PHASES)
        phase = self.current_phase
        for other in DESIGN_PHASES:
            button = self.query_one(f"#phase-{other.key}", Button)
            button.set_class(other.key == phase.key, "current-phase")
        mode = "writes to Canvas after you confirm" if phase.writes else "read-only"
        self.query_one("#phase-description", Static).update(
            Text(f"Phase {phase.number} of 5 — {phase.title}: {phase.description} ({mode})")
        )
        self.query_one("#schedule-file", Input).display = phase.key == "dates"
        self.query_one("#apply-button", Button).display = phase.writes
        preview = self.previews.get(phase.key)
        if preview is None:
            self.render_table(None, "Press v to preview this phase. Nothing is written.")
        else:
            self.render_table(preview, "Preview (dry run). Nothing was changed.")

    def action_select_phase(self, index: int) -> None:
        if not self.busy:
            self.show_phase(index)

    def action_next_phase(self) -> None:
        self.action_select_phase(self.phase_index + 1)

    def action_previous_phase(self) -> None:
        self.action_select_phase(self.phase_index - 1)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id.startswith("phase-"):
            keys = [phase.key for phase in DESIGN_PHASES]
            self.action_select_phase(keys.index(button_id.removeprefix("phase-")))
        elif button_id == "preview-button":
            self.action_preview()
        elif button_id == "apply-button":
            self.action_apply()
        elif button_id == "open-rubric-audit":
            self.action_open_rubric_audit()

    def action_open_rubric_audit(self) -> None:
        from coursewright.ui.tui.screens.design_rubric import RubricAuditScreen

        self.app.push_screen(RubricAuditScreen(self.app_state))

    # -- rendering -------------------------------------------------------

    def render_table(self, result: PhaseTable | None, banner: str) -> None:
        """Fill the table from ``result`` and show ``banner`` with its summary."""
        table = self.query_one("#phase-table", DataTable)
        table.clear(columns=True)
        lines = [banner]
        if result is not None:
            table.add_columns(*result.columns)
            for row in result.rows:
                table.add_row(*(Text(cell) for cell in row))
            lines = [f"[bold cyan]{escape(result.title)}[/bold cyan]", banner]
            lines += [escape(line) for line in result.summary]
            lines += [f"[red]{escape(error)}[/red]" for error in result.errors]
        self.query_one("#phase-summary", Static).update("\n".join(lines))

    def show_problem(self, title: str, message: str) -> None:
        """Say plainly why nothing happened (never fail silently)."""
        self.query_one("#phase-summary", Static).update(f"[red]{escape(message)}[/red]")
        self.notify(message, title=title, severity="error")

    # -- preview and apply -----------------------------------------------

    def phase_options(self) -> PhaseOptions:
        schedule_text = self.query_one("#schedule-file", Input).value.strip()
        schedule_file = Path(schedule_text).expanduser().absolute() if schedule_text else None
        return PhaseOptions(
            schedule_file=schedule_file,
            schedule_content=(
                schedule_file.read_text(encoding="utf-8") if schedule_file is not None else None
            ),
        )

    def action_preview(self) -> None:
        if not self.busy:
            self.run_phase(self.current_phase, execute=False)

    def action_apply(self) -> None:
        """Ask before writing; refuse cleanly when it is not safe to ask."""
        phase = self.current_phase
        if self.busy:
            return
        if not phase.writes:
            self.notify(f"{phase.title} is read-only; there is nothing to apply.")
            return
        try:
            require_workspace(self.app_state.config)
        except SafeWriteError as err:
            self.show_problem("No workspace for backups", str(err))
            return
        preview = self.previews.get(phase.key)
        if preview is None:
            self.notify("Preview this phase first (v) so you can see what will change.")
            return
        if preview.errors:
            self.show_problem("Preview has errors", "Resolve the preview errors and preview again.")
            return
        if not preview.rows:
            self.notify("The preview found nothing to change.")
            return

        def apply_after_confirmation(confirmed: bool | None) -> None:
            if confirmed:
                self.run_phase(phase, execute=True)
            else:
                self.notify("Cancelled. Nothing was written to Canvas.")

        prompt = (
            f"Apply {phase.title} to [bold]{escape(self.course_name)}[/bold]?\n"
            f"{len(preview.rows)} row(s) from the preview will be written to Canvas.\n"
            "A JSON backup is saved in your workspace first."
        )
        self.app.push_screen(
            ConfirmModal(prompt, "Apply", "Cancel"), callback=apply_after_confirmation
        )

    @work(exclusive=True)
    async def run_phase(self, phase: DesignPhase, *, execute: bool) -> None:
        """Run one phase in a thread under the transmission log."""
        client = self.app_state.canvas_client
        course_id = self.course_id
        if client is None or course_id is None:
            self.show_problem("Not connected", "No Canvas connection or course selected.")
            return
        verb = "Applying" if execute else "Previewing"
        modal = TransmissionModal(f"{verb} {phase.title} for {escape(self.course_name)}…")
        self.busy = True
        try:
            # Mount first so the very first request reaches the log.
            await self.app.push_screen(modal)
            options = self.phase_options()
            workspace = resolve_workspace_directory(self.app_state.config)
            if execute:
                reviewed = self.reviewed_inputs.get(phase.key)
                if reviewed is None:
                    raise ReviewChangedError("Preview this phase again before applying.")
                snapshot, reviewed_options, reviewed_course_id = reviewed
                if options != reviewed_options or course_id != reviewed_course_id:
                    raise ReviewChangedError(
                        "Inputs changed since preview. Preview this phase again."
                    )
                if snapshot.client is not client:
                    raise ReviewChangedError("Canvas connection changed. Preview this phase again.")
                await asyncio.to_thread(snapshot.validate)
                options = reviewed_options
            else:
                # A failed new preview must never leave an older Apply enabled.
                self.previews.pop(phase.key, None)
                self.reviewed_inputs.pop(phase.key, None)
                snapshot = ReviewedCanvasInputs(client)
            result = await asyncio.to_thread(
                phase.run,
                snapshot,
                course_id,
                workspace,
                options,
                execute,
            )
        except Exception as err:  # every failure is shown, never swallowed
            message = str(err) or type(err).__name__
            if modal.is_mounted:
                modal.mark_error(message)
            self.show_problem(f"{phase.title} stopped", message)
            return
        finally:
            self.busy = False
        if modal.is_mounted:
            modal.dismiss(None)
        if execute:
            # A new preview is needed before applying again.
            self.previews.pop(phase.key, None)
            self.reviewed_inputs.pop(phase.key, None)
            self.render_table(result, "[bold green]Done.[/bold green] Canvas was updated.")
        else:
            self.previews[phase.key] = result
            self.reviewed_inputs[phase.key] = (snapshot, options, course_id)
            self.render_table(result, "Preview (dry run). Nothing was changed.")
