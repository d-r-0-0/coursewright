"""Zero Ungraded Screen — select past-due assignments and zero out
ungraded submissions in bulk.

Accessible from the course list via the ``z`` key.  Fetches all
assignments for the selected course from Canvas, lets the user select
any combination, then calls ``zero_ungraded_submissions`` (the same
library function GradeLiner uses) to set score=0 for every submission
whose score is currently ``None``.  Every grade goes through
``SafeCanvasWriter``: backed up to the private workspace first, and a
refusal from Canvas stops the run instead of being skipped quietly.

Key bindings
------------
Space       Toggle selection on the cursor row.
p           Select all past-due assignments (due_at < now).
a           Select all assignments.
n           Clear all selections.
z           Zero selected assignments (with confirmation dialog).
Escape      Back to course list.

The Ungraded column was intentionally omitted: Canvas's
``needs_grading_count`` only counts *submitted-but-ungraded* work and
shows 0 for never-submitted (score=None) items — the very submissions
this screen targets.  Rely on the post-zero summary instead.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Header, LoadingIndicator, Static
from textual.worker import Worker, WorkerState

from coursewright.core.canvas.models import Assignment
from coursewright.core.canvas.safe_write import create_safe_canvas_writer
from coursewright.ta.gradeliner.orchestrator import zero_ungraded_submissions
from coursewright.ta.gradeliner.zero_ungraded import resolve_active_user_ids
from coursewright.ui.tui.screens.base import CoursewrightScreen
from coursewright.ui.tui.state import AppState
from coursewright.ui.tui.widgets.breadcrumb import BreadcrumbHeader
from coursewright.ui.tui.widgets.confirm_modal import ConfirmModal
from coursewright.ui.tui.widgets.help_bar import HelpBar
from coursewright.ui.tui.widgets.transmission_modal import TransmissionModal

# Visual markers used in the selection column
_SEL_ON = "[green]■[/green]"
_SEL_OFF = "[dim]□[/dim]"


class ZeroUngradedScreen(CoursewrightScreen):
    """Select assignments and zero out ungraded (score=None) submissions.

    Workflow
    --------
    1. On mount, a background worker fetches all assignments for the
       selected course from Canvas (includes needs_grading_count).
    2. The DataTable lists every assignment with due date, past-due
       status, and how many submissions Canvas says need grading.
    3. User selects assignments with Space / p / a / n, then presses
       z to confirm and run the zero-out worker.
    4. Results are displayed in the status bar; the table reloads so
       the Ungraded counts reflect the new state.
    """

    BINDINGS = [
        Binding("space", "toggle_selection", "Toggle", show=True),
        Binding("p", "select_past_due", "Past Due", show=True),
        Binding("a", "select_all", "All", show=True),
        Binding("n", "clear_selection", "None", show=True),
        Binding("z", "zero_selected", "Zero Selected", show=True),
    ]

    DEFAULT_CSS = """
    ZeroUngradedScreen #zero-status {
        padding: 0 1;
        height: auto;
    }
    ZeroUngradedScreen #zero-result {
        padding: 0 1;
        height: auto;
        color: $success;
    }
    ZeroUngradedScreen #zero-help {
        padding: 1 1;
        height: auto;
        color: $text-muted;
    }
    ZeroUngradedScreen LoadingIndicator {
        height: auto;
        min-height: 3;
        width: 100%;
    }
    """

    def __init__(self, app_state: AppState) -> None:
        super().__init__()
        self.app_state = app_state
        # Full list of assignments for the course, loaded by background worker
        self._assignments: list[Assignment] = []
        # Set of assignment IDs the user has toggled on for zeroing
        self._selected_ids: set[int] = set()
        self._unpublished_skipped_count: int = 0
        self._transmission_modal: TransmissionModal | None = None

    # ------------------------------------------------------------------
    # Widget tree
    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        """Build the layout: header, status, table, help text, footer."""
        course_name = (
            self.app_state.current_course.name
            if self.app_state.current_course
            else "Unknown Course"
        )
        yield Header(show_clock=True)
        yield BreadcrumbHeader()
        with VerticalScroll(id="zero-scroll"):
            yield Static(
                f"[bold cyan]Zero Ungraded Submissions[/bold cyan]  "
                f"[dim]{course_name}[/dim]",
                id="zero-title",
            )
            yield Static(
                "[dim]Loading assignments…[/dim]",
                id="zero-status",
            )
            yield LoadingIndicator(id="zero-loader")
            yield DataTable(
                id="zero-table",
                cursor_type="row",
                zebra_stripes=True,
            )
            yield Static("", id="zero-result")
            yield Static(
                _build_help_text(),
                id="zero-help",
            )
        yield HelpBar()

    def on_mount(self) -> None:
        """Add DataTable columns then kick off the background fetch."""
        # Hide table until data arrives so the LoadingIndicator fills
        # the space cleanly.
        table = self.query_one("#zero-table", DataTable)
        table.display = False
        table.add_column("", key="sel", width=3)
        table.add_column("#", key="num", width=4)
        table.add_column("Assignment", key="name", width=42)
        table.add_column("Due Date", key="due", width=18)
        table.add_column("Status", key="status", width=12)
        # NOTE: Canvas's needs_grading_count only tracks *submitted-but-
        # ungraded* work, NOT never-submitted (score=None) items — so it
        # would show 0 even when zeroing will find real work to do.  The
        # column is intentionally omitted; rely on the post-zero summary.

        self._show_transmission_modal("Fetching assignments from Canvas...")
        self.run_worker(
            self._load_assignments_from_canvas,
            name="load-assignments",
            thread=True,
        )

    def _show_transmission_modal(self, status_text: str) -> None:
        """Show the shared API traffic modal for Canvas work."""
        if self._transmission_modal is not None:
            self._transmission_modal.update_status_text(status_text)
            return

        transmission_modal = TransmissionModal(status_text)
        self._transmission_modal = transmission_modal
        self.app.push_screen(transmission_modal)

    def _dismiss_transmission_modal(self) -> None:
        """Close the shared API traffic modal after successful work."""
        transmission_modal = self._transmission_modal
        if transmission_modal is None:
            return

        self._transmission_modal = None
        with contextlib.suppress(Exception):
            if self.app.screen is transmission_modal:
                self.app.pop_screen()

    def _mark_transmission_error(self, error_text: str) -> None:
        """Leave the traffic modal open with an error message."""
        transmission_modal = self._transmission_modal
        self._transmission_modal = None
        if transmission_modal is None:
            return

        with contextlib.suppress(Exception):
            transmission_modal.mark_error(error_text)

    # ------------------------------------------------------------------
    # Background workers
    # ------------------------------------------------------------------
    async def _load_assignments_from_canvas(self) -> str:
        """Fetch all assignments for the current course.

        Stores results in ``self._assignments`` and returns ``"ok"``
        on success, ``"failed"`` otherwise.
        """
        course = self.app_state.current_course
        if not course:
            self.app.call_from_thread(
                self._update_status,
                "[red]No course selected.[/red]",
            )
            return "failed"

        try:
            all_assignments = self.app_state.canvas_client.get_assignments(
                course.id
            )
            # Canvas refuses grade PUTs on unpublished assignments with HTTP 403,
            # matching CourseListScreen._load_grading_todo's published filter.
            self._assignments = [
                assignment
                for assignment in all_assignments
                if getattr(assignment, "published", True)
            ]
            self._unpublished_skipped_count = len(all_assignments) - len(self._assignments)
            published_ids = {assignment.id for assignment in self._assignments}
            self._selected_ids.intersection_update(published_ids)
            return "ok"
        except Exception as fetch_error:
            self.app.call_from_thread(
                self._mark_transmission_error,
                f"Assignment load failed: {fetch_error}",
            )
            self.app.call_from_thread(
                self._update_status,
                f"[red]Error loading assignments: {fetch_error}[/red]",
            )
            return "failed"

    @work(thread=True, exclusive=True)
    def _zero_selected_worker(self) -> None:
        """Submit score=0 for every score=None submission in selected assignments.

        The user has confirmed, so the writer executes: each zero is
        backed up first (AGENTS.md 3.11).  A progress callback fires
        after each assignment so the screen can show a running counter
        (e.g. "Zeroing… 3/7 assignments").

        After zeroing, triggers a full table reload.
        """
        course = self.app_state.current_course
        if not course or not self._selected_ids:
            return

        total = len(self._selected_ids)
        self.app.call_from_thread(
            self._show_transmission_modal,
            f"Zeroing ungraded submissions in Canvas... 0/{total} processed",
        )

        def _on_assignment_done(assignment_id: int, zeroed_in_assignment: int) -> None:
            nonlocal processed_count
            processed_count += 1
            status_text = (
                f"Zeroing ungraded submissions in Canvas... {processed_count}/{total} processed"
            )
            self.app.call_from_thread(
                self._transmission_modal.update_status_text,
                status_text,
            )
            self.app.call_from_thread(
                self._update_status,
                f"[cyan]Zeroing… {processed_count}/{total} assignment(s) processed[/cyan]",
            )

        processed_count = 0
        try:
            canvas_client = self.app_state.canvas_client
            writer = create_safe_canvas_writer(
                canvas_client,
                execute=True,
                workspace_directory=self.app_state.workspace_directory,
            )
            active_user_ids = _resolve_active_user_ids_safely(canvas_client, course.id)
            total_students, assignment_count = zero_ungraded_submissions(
                canvas_client,
                writer,
                course.id,
                sorted(self._selected_ids),
                execute=True,
                active_user_ids=active_user_ids,
                on_assignment_done=_on_assignment_done,
            )
        except Exception as zero_error:
            self.app.call_from_thread(
                self._mark_transmission_error,
                f"Zero ungraded failed: {zero_error}",
            )
            self.app.call_from_thread(
                self._update_status,
                f"[red]Error zeroing submissions: {zero_error}[/red]",
            )
            return

        if total_students == 0:
            msg = "[yellow]No ungraded submissions found — nothing zeroed.[/yellow]"
        else:
            msg = (
                f"[green]Zeroed [bold]{total_students}[/bold] submission(s) "
                f"across [bold]{assignment_count}[/bold] assignment(s).[/green]"
            )

        self.app.call_from_thread(self._update_result, msg)
        self.app.call_from_thread(self._dismiss_transmission_modal)
        self.app.call_from_thread(self._trigger_reload)

    # ------------------------------------------------------------------
    # Worker lifecycle
    # ------------------------------------------------------------------
    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Populate table once the load-assignments worker succeeds."""
        if (
            event.worker.name == "load-assignments"
            and event.state == WorkerState.SUCCESS
            and event.worker.result == "ok"
        ):
            self._dismiss_transmission_modal()
            self._populate_table()

    # ------------------------------------------------------------------
    # Table management helpers
    # ------------------------------------------------------------------
    def _populate_table(self) -> None:
        """Fill (or refill) the DataTable from ``self._assignments``."""
        # Swap loader → table
        self.query_one("#zero-loader").display = False
        table = self.query_one("#zero-table", DataTable)
        table.display = True
        table.clear()

        now = datetime.now(UTC)
        for idx, assignment in enumerate(self._assignments, start=1):
            sel = _SEL_ON if assignment.id in self._selected_ids else _SEL_OFF
            table.add_row(
                sel,
                str(idx),
                assignment.name,
                _format_due(assignment.due_at),
                _format_past_due_status(assignment.due_at, now),
                key=str(assignment.id),
            )

        count = len(self._assignments)
        hidden_note = (
            f" [dim]({self._unpublished_skipped_count} unpublished hidden)[/dim]"
            if self._unpublished_skipped_count
            else ""
        )
        self._update_status(
            f"[green]{count} assignment(s) loaded.[/green]{hidden_note}  "
            f"[dim]Space to toggle · p = past due · a = all · "
            f"n = clear · z = zero selected[/dim]"
        )

    def _refresh_sel_column(self) -> None:
        """Update every row's selection-indicator cell in place."""
        table = self.query_one("#zero-table", DataTable)
        for assignment in self._assignments:
            sel = _SEL_ON if assignment.id in self._selected_ids else _SEL_OFF
            with contextlib.suppress(Exception):
                # Row may not exist yet if called before populate completes.
                table.update_cell(
                    str(assignment.id),
                    "sel",
                    sel,
                    update_width=False,
                )

    def _show_loader(self) -> None:
        """Show the loading indicator and hide the table."""
        self.query_one("#zero-loader").display = True
        self.query_one("#zero-table", DataTable).display = False

    def _hide_loader(self) -> None:
        """Hide the loading indicator (table reveal handled by _populate_table)."""
        self.query_one("#zero-loader").display = False

    def _trigger_reload(self) -> None:
        """Kick off another background fetch to refresh the assignment list.

        Called via call_from_thread so we are already on the main thread —
        call _show_loader directly rather than wrapping it again.
        """
        self._show_loader()
        self._show_transmission_modal("Refreshing assignments from Canvas...")
        self.run_worker(
            self._load_assignments_from_canvas,
            name="load-assignments",
            thread=True,
        )

    def _update_status(self, text: str) -> None:
        self.query_one("#zero-status", Static).update(text)

    def _update_result(self, text: str) -> None:
        self.query_one("#zero-result", Static).update(text)

    # ------------------------------------------------------------------
    # Keyboard action handlers
    # ------------------------------------------------------------------
    def action_toggle_selection(self) -> None:
        """Toggle the assignment on the cursor row in/out of the selection set."""
        table = self.query_one("#zero-table", DataTable)
        row_idx = table.cursor_row
        if 0 <= row_idx < len(self._assignments):
            assignment = self._assignments[row_idx]
            if assignment.id in self._selected_ids:
                self._selected_ids.discard(assignment.id)
            else:
                self._selected_ids.add(assignment.id)
            self._refresh_sel_column()

    def action_select_past_due(self) -> None:
        """Select every assignment whose due date is in the past."""
        now = datetime.now(UTC)
        self._selected_ids = {
            a.id
            for a in self._assignments
            if getattr(a, "published", True) and a.due_at is not None and a.due_at < now
        }
        self._refresh_sel_column()
        count = len(self._selected_ids)
        self._update_status(
            f"[cyan]{count} past-due assignment(s) selected.[/cyan]  "
            f"[dim]Press z to zero ungraded submissions.[/dim]"
        )

    def action_select_all(self) -> None:
        """Select every assignment in the table."""
        self._selected_ids = {a.id for a in self._assignments}
        self._refresh_sel_column()

    def action_clear_selection(self) -> None:
        """Deselect all assignments."""
        self._selected_ids.clear()
        self._refresh_sel_column()

    def action_zero_selected(self) -> None:
        """Show confirmation dialog then zero ungraded submissions."""
        if not self._selected_ids:
            self._update_status(
                "[yellow]No assignments selected — use Space/p/a to select.[/yellow]"
            )
            return

        count = len(self._selected_ids)
        self.app.push_screen(
            ConfirmModal(
                prompt_text=(
                    f"Zero [bold]{count}[/bold] assignment(s)?\n\n"
                    "[dim]Only submissions with no score will be set to 0.[/dim]"
                ),
                yes_label="Zero them",
                no_label="Cancel",
            ),
            callback=self._handle_zero_confirmed,
        )

    def _handle_zero_confirmed(self, confirmed: bool) -> None:
        """Start the zero worker if the user confirmed."""
        if confirmed:
            total = len(self._selected_ids)
            self._update_status(
                f"[cyan]Zeroing {total} assignment(s)… 0/{total} processed[/cyan]"
            )
            self._zero_selected_worker()


# ------------------------------------------------------------------
# Module-level helpers (pure functions, no screen state)
# ------------------------------------------------------------------

def _resolve_active_user_ids_safely(canvas_client: Any, course_id: int) -> set[int] | None:
    """Resolve active student user IDs, returning None if lookup is unavailable."""
    try:
        return resolve_active_user_ids(canvas_client, course_id)
    except Exception:
        return None


def _build_help_text() -> str:
    """Return Rich markup for the keyboard shortcuts reference block."""
    return (
        "[bold green]Keys[/bold green]\n"
        "[cyan]Space[/cyan]   Toggle selection on cursor row\n"
        "[cyan]p[/cyan]       Select all past-due assignments\n"
        "[cyan]a[/cyan]       Select all\n"
        "[cyan]n[/cyan]       Clear all selections\n"
        "[cyan]z[/cyan]       Zero ungraded for selected (with confirm)\n"
        "[cyan]Esc[/cyan]     Back to course list"
    )


def _format_due(due_at: datetime | None) -> str:
    """Format a due_at datetime as a compact local-ish string, or '—'."""
    if due_at is None:
        return "[dim]—[/dim]"
    # Show in UTC since Canvas timestamps are UTC; the user can compare easily.
    return due_at.strftime("%Y-%m-%d %H:%M")


def _format_past_due_status(due_at: datetime | None, now: datetime) -> str:
    """Return a Rich-markup status label for an assignment's due date."""
    if due_at is None:
        return "[dim]No Due[/dim]"
    if due_at < now:
        return "[red]Past Due[/red]"
    return "[green]Future[/green]"
