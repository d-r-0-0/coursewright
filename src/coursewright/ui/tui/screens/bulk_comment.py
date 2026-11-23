"""Bulk Comment Screen — Textual wizard for posting missing-submission comments.

Replaces the legacy Rich-based ``run_comment_flow`` from ``comment_menu.py``
with a five-step Textual screen:

1. Load assignments and submissions from Canvas
2. Select assignments using the reusable ``SelectionTable`` widget
3. Preview the comment template (loaded from file or built-in fallback)
4. Preview recipients per assignment before sending
5. Confirm and send comments with a progress display

Dependencies: TUI-2 (CourseListScreen), TUI-9 (SelectionTable widget).
"""

from __future__ import annotations

import logging
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Header, Static

from coursewright.core.canvas.safe_write import (
    SafeWriteError,
    WriteFailedError,
    create_safe_canvas_writer,
)
from coursewright.ui.tui.screens.base import CoursewrightScreen
from coursewright.ui.tui.state import AppState
from coursewright.ui.tui.widgets.breadcrumb import BreadcrumbHeader
from coursewright.ui.tui.widgets.confirm_modal import ConfirmModal
from coursewright.ui.tui.widgets.help_bar import HelpBar
from coursewright.ui.tui.widgets.selection_table import (
    SelectionRow,
    SelectionTable,
)
from coursewright.ui.tui.widgets.transmission_modal import TransmissionModal

logger = logging.getLogger(__name__)

# Default template path matches the legacy comment_menu.py location
DEFAULT_TEMPLATE_PATH = Path("templates") / "missing_submission.md"

# Built-in fallback when no template file exists on disk.  The sign-off
# is a neutral placeholder: an instructor who wants their own name writes
# their own template file (AGENTS.md 3.13 keeps real names out of code).
BUILTIN_TEMPLATE = (
    "Hi {student_name},\n\n"
    "It looks like you haven't submitted {assignment_name} yet. "
    "If you're having any trouble or need extra time, please "
    "don't hesitate to reach out — I'm happy to help.\n\n"
    "Best,\nYour instructor"
)


class BulkCommentScreen(CoursewrightScreen):
    """Wizard-style bulk commenter for missing-submission comments.

    Lifecycle mirrors the five steps from the legacy Rich flow:
    1. Fetch assignments + submissions from Canvas
    2. Pick which assignments to comment on (SelectionTable)
    3. Preview the comment template
    4. Review recipient list per assignment
    5. Confirm and send

    Key bindings (active during assignment selection):
    - Space : toggle highlighted assignment
    - a     : select all assignments
    - n     : clear all assignments
    """

    BINDINGS = [
        Binding("space", "toggle_assignment", "Toggle", show=False),
        Binding("a", "select_all_assignments", "All", show=False),
        Binding("n", "clear_assignments", "None", show=False),
    ]

    def __init__(self, app_state: AppState) -> None:
        super().__init__()
        self.app_state = app_state
        # Assignment summaries fetched from Canvas (same shape as comment_menu)
        self._assignment_summaries: list[dict] = []
        # IDs selected in the SelectionTable
        self._selected_assignment_ids: set[int] = set()
        # Loaded comment template text
        self._template_text: str = ""

    # ------------------------------------------------------------------
    # Widget tree — five wizard steps laid out vertically
    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield BreadcrumbHeader()
        with VerticalScroll(id="bulk-comment-scroll"):
            yield Static(
                "[bold cyan]Bulk Assignment Commenter[/bold cyan]",
                id="wizard-title",
            )

            # Step 1: Load assignments
            with Vertical(id="step-load"):
                yield Static("[bold]1) Load Assignments[/bold]")
                yield Button(
                    "Fetch Assignments",
                    id="fetch-assignments",
                )
                yield Static("", id="load-status")

            # Step 2: Select assignments (SelectionTable placeholder)
            with Vertical(id="step-select"):
                yield Static("[bold]2) Select Assignments[/bold]")
                yield SelectionTable([], table_id="assignment-table")
                yield Static("", id="select-status")

            # Step 3: Template preview
            with Vertical(id="step-template"):
                yield Static("[bold]3) Comment Template[/bold]")
                yield Button("Load Template", id="load-template")
                yield Static("", id="template-preview")
                yield Static("", id="template-status")

            # Step 4: Recipient preview
            with Vertical(id="step-recipients"):
                yield Static("[bold]4) Preview Recipients[/bold]")
                yield Button(
                    "Show Recipients",
                    id="show-recipients",
                )
                yield DataTable(id="recipient-table")
                yield Static("", id="recipient-status")

            # Step 5: Send
            with Vertical(id="step-send"):
                yield Static("[bold]5) Send Comments[/bold]")
                yield Button("Send Comments", id="send-comments")
                yield Static("", id="send-status")

        yield HelpBar()

    def on_mount(self) -> None:
        """Configure the recipient preview table columns."""
        recipient_table = self.query_one(
            "#recipient-table",
            DataTable,
        )
        recipient_table.add_columns(
            "Assignment",
            "Student",
            "Status",
        )

    # ------------------------------------------------------------------
    # Button routing
    # ------------------------------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Route button presses to the corresponding step handler."""
        button_id = event.button.id
        if button_id == "fetch-assignments":
            self._fetch_assignments_worker()
        elif button_id == "load-template":
            self._load_template()
        elif button_id == "show-recipients":
            self._show_recipients()
        elif button_id == "send-comments":
            self._confirm_send()

    # ------------------------------------------------------------------
    # SelectionTable keyboard bindings
    # ------------------------------------------------------------------
    def action_toggle_assignment(self) -> None:
        """Toggle the currently highlighted assignment row."""
        table_widget = self.query_one(
            "#step-select SelectionTable",
            SelectionTable,
        )
        if table_widget.toggle_cursor_row():
            self._selected_assignment_ids = table_widget.selected_identifiers()

    def action_select_all_assignments(self) -> None:
        """Select all assignment rows."""
        table_widget = self.query_one(
            "#step-select SelectionTable",
            SelectionTable,
        )
        table_widget.select_all()
        self._selected_assignment_ids = table_widget.selected_identifiers()

    def action_clear_assignments(self) -> None:
        """Clear all assignment selections."""
        table_widget = self.query_one(
            "#step-select SelectionTable",
            SelectionTable,
        )
        table_widget.clear_all()
        self._selected_assignment_ids = set()

    # ------------------------------------------------------------------
    # Step 1: Fetch assignments + submissions
    # ------------------------------------------------------------------
    @work(thread=True, exclusive=True, name="fetch-assignments")
    def _fetch_assignments_worker(self) -> None:
        """Load assignments from Canvas, build submission summaries,
        and populate the SelectionTable."""
        canvas_client = self.app_state.canvas_client
        current_course = self.app_state.current_course
        if canvas_client is None or current_course is None:
            self._set_text(
                "#load-status",
                "[red]No course or Canvas client available.[/red]",
            )
            return

        course_id = current_course.id
        transmission_modal = TransmissionModal("Fetching assignments from Canvas...")
        self.app.call_from_thread(self.app.push_screen, transmission_modal)
        self.app.call_from_thread(
            self._set_text,
            "#load-status",
            "[blue]Fetching assignments from Canvas...[/blue]",
        )

        try:
            assignments = canvas_client.get_assignments(course_id)
        except Exception as fetch_error:
            self.app.call_from_thread(
                transmission_modal.mark_error,
                f"Failed to load assignments: {fetch_error}",
            )
            self._set_text(
                "#load-status",
                f"[red]Failed to load assignments: {fetch_error}[/red]",
            )
            return

        if not assignments:
            self.app.call_from_thread(self.app.pop_screen)
            self._set_text(
                "#load-status",
                "[yellow]No assignments found for this course.[/yellow]",
            )
            return

        # Only keep published assignments that are worth points
        gradeable_assignments = [
            a for a in assignments if a.published and a.points_possible and a.points_possible > 0
        ]

        if not gradeable_assignments:
            self.app.call_from_thread(self.app.pop_screen)
            self._set_text(
                "#load-status",
                "[yellow]No gradeable assignments found.[/yellow]",
            )
            return

        # Build submission summaries (same logic as comment_menu)
        self._assignment_summaries = _build_submission_summaries(
            canvas_client,
            course_id,
            gradeable_assignments,
        )

        # Build SelectionTable rows — pre-select assignments with missing work
        selection_rows: list[SelectionRow] = []
        for summary in self._assignment_summaries:
            has_missing = summary["missing_count"] > 0
            submitted = summary["total_submissions"] - summary["missing_count"]
            selection_rows.append(
                SelectionRow(
                    row_identifier=summary["assignment_id"],
                    title=summary["assignment_name"],
                    detail=f"{summary['points_possible']:.0f} pts",
                    metric=(
                        f"{summary['missing_count']} missing, "
                        f"{submitted}/{summary['total_submissions']}"
                    ),
                    selected=has_missing,
                ),
            )

        # Replace the empty SelectionTable with the populated one — must
        # happen on the main UI thread, since SelectionTable construction
        # creates child widgets whose message-pump tasks need a running
        # event loop.
        self.app.call_from_thread(self._install_assignment_selection_table, selection_rows)

        total_missing = sum(s["missing_count"] for s in self._assignment_summaries)
        self.app.call_from_thread(
            self._set_text,
            "#load-status",
            f"[green]Loaded {len(self._assignment_summaries)} "
            f"assignments ({total_missing} total missing).[/green]",
        )
        self.app.call_from_thread(self.app.pop_screen)

    def _install_assignment_selection_table(
        self, selection_rows: list[SelectionRow]
    ) -> None:
        """Swap in the populated SelectionTable on the main UI thread."""
        old_widget = self.query_one(
            "#step-select SelectionTable",
            SelectionTable,
        )
        new_widget = SelectionTable(
            selection_rows,
            table_id="assignment-table",
        )
        old_widget.remove()
        self.query_one("#step-select", Vertical).mount(
            new_widget,
            before="#select-status",
        )
        self._selected_assignment_ids = new_widget.selected_identifiers()

    # ------------------------------------------------------------------
    # Step 3: Load comment template
    # ------------------------------------------------------------------
    def _load_template(self) -> None:
        """Read the comment template from disk or use the built-in
        fallback. Display the rendered template in the preview area."""
        self._template_text = _load_comment_template_text()
        if not self._template_text:
            self._set_text(
                "#template-status",
                "[red]Could not load template.[/red]",
            )
            return

        # Show the template in the preview Static with Rich markup
        preview_text = (
            "[bold]Template Preview:[/bold]\n"
            "[dim](Placeholders: {{student_name}}, "
            "{{assignment_name}})[/dim]\n\n"
            f"{self._template_text}"
        )
        self._set_text("#template-preview", preview_text)
        self._set_text(
            "#template-status",
            "[green]Template loaded.[/green]",
        )

    # ------------------------------------------------------------------
    # Step 4: Recipient preview
    # ------------------------------------------------------------------
    def _show_recipients(self) -> None:
        """Populate the recipient table with students who have missing
        submissions for the selected assignments."""
        if not self._assignment_summaries:
            self._set_text(
                "#recipient-status",
                "[yellow]Load assignments first (Step 1).[/yellow]",
            )
            return

        if not self._selected_assignment_ids:
            self._set_text(
                "#recipient-status",
                "[yellow]No assignments selected (Step 2).[/yellow]",
            )
            return

        recipient_table = self.query_one(
            "#recipient-table",
            DataTable,
        )
        recipient_table.clear(columns=False)

        total_recipients = 0
        for summary in self._assignment_summaries:
            if summary["assignment_id"] not in self._selected_assignment_ids:
                continue
            for student in summary["missing_students"]:
                recipient_table.add_row(
                    summary["assignment_name"],
                    student["name"],
                    "Not Submitted",
                )
                total_recipients += 1

        if total_recipients == 0:
            self._set_text(
                "#recipient-status",
                "[green]All students have submitted! Nothing to send.[/green]",
            )
        else:
            self._set_text(
                "#recipient-status",
                f"[bold]{total_recipients} comments will be sent.[/bold]",
            )

    # ------------------------------------------------------------------
    # Step 5: Confirm + send
    # ------------------------------------------------------------------
    def _confirm_send(self) -> None:
        """Push a ConfirmModal before actually sending comments."""
        if not self._template_text:
            self._set_text(
                "#send-status",
                "[yellow]Load template first (Step 3).[/yellow]",
            )
            return

        if not self._selected_assignment_ids:
            self._set_text(
                "#send-status",
                "[yellow]No assignments selected.[/yellow]",
            )
            return

        # Count how many comments would be sent
        total_to_send = self._count_pending_comments()
        if total_to_send == 0:
            self._set_text(
                "#send-status",
                "[green]No missing submissions — nothing to send.[/green]",
            )
            return

        self.app.push_screen(
            ConfirmModal(
                f"Send {total_to_send} comments now?",
                yes_label="Send",
                no_label="Cancel",
            ),
            callback=self._handle_send_confirmation,
        )

    def _handle_send_confirmation(self, confirmed: bool) -> None:
        """Called after the ConfirmModal is dismissed."""
        if not confirmed:
            self._set_text(
                "#send-status",
                "[yellow]Cancelled by user.[/yellow]",
            )
            return
        self._send_comments_worker()

    @work(thread=True, exclusive=True, name="send-comments")
    def _send_comments_worker(self) -> None:
        """Post comments via the Canvas API for every missing
        submission in the selected assignments.

        The user has confirmed, so every comment goes through an
        executing ``SafeCanvasWriter`` (AGENTS.md 3.11): the submission
        is backed up to the private workspace first.  A comment Canvas
        refuses is counted as failed and the rest carry on, as in the
        source.  No workspace, or a failed backup, stops the run.
        """
        canvas_client = self.app_state.canvas_client
        current_course = self.app_state.current_course
        if canvas_client is None or current_course is None:
            self._set_text(
                "#send-status",
                "[red]Missing Canvas client or course.[/red]",
            )
            return

        course_id = current_course.id
        transmission_modal = TransmissionModal("Posting submission comments...")
        self.app.call_from_thread(self.app.push_screen, transmission_modal)
        try:
            writer = create_safe_canvas_writer(
                canvas_client,
                execute=True,
                workspace_directory=self.app_state.workspace_directory,
            )
        except SafeWriteError as no_workspace:
            self.app.call_from_thread(transmission_modal.mark_error, str(no_workspace))
            self.app.call_from_thread(
                self._set_text,
                "#send-status",
                "[red]Nothing sent: no private workspace for backups.[/red]",
            )
            return
        self.app.call_from_thread(
            self._set_text,
            "#send-status",
            "[blue]Sending comments...[/blue]",
        )

        successful_count = 0
        failed_count = 0

        for summary in self._assignment_summaries:
            if summary["assignment_id"] not in self._selected_assignment_ids:
                continue
            assignment_name = summary["assignment_name"]
            assignment_id = summary["assignment_id"]

            for student in summary["missing_students"]:
                rendered_comment = self._template_text.format(
                    student_name=student["name"],
                    assignment_name=assignment_name,
                )
                try:
                    outcome = writer.post_submission_comment(
                        course_id,
                        assignment_id,
                        student["user_id"],
                        rendered_comment,
                    )
                    ok = outcome.executed
                except WriteFailedError:
                    ok = False
                except SafeWriteError as backup_error:
                    # The backup failed, so this comment was not sent;
                    # stop rather than send the rest without backups.
                    self.app.call_from_thread(
                        transmission_modal.mark_error,
                        f"Stopped after {successful_count} comment(s): {backup_error}",
                    )
                    self.app.call_from_thread(
                        self._set_text,
                        "#send-status",
                        f"[red]Stopped: backup failed.[/red] Sent: {successful_count}",
                    )
                    return
                if ok:
                    successful_count += 1
                else:
                    failed_count += 1
                self.app.call_from_thread(
                    transmission_modal.append_log_line,
                    f"{assignment_name}: {student['name']} ({'ok' if ok else 'failed'})",
                )

        self.app.call_from_thread(
            self._set_text,
            "#send-status",
            f"[green]Done![/green] "
            f"Sent: [bold]{successful_count}[/bold], "
            f"Failed: [bold red]{failed_count}[/bold red]",
        )
        self.app.call_from_thread(self.app.pop_screen)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _count_pending_comments(self) -> int:
        """Count total comments that would be sent based on the
        current assignment selection."""
        total = 0
        for summary in self._assignment_summaries:
            if summary["assignment_id"] in self._selected_assignment_ids:
                total += len(summary["missing_students"])
        return total

    def _set_text(self, selector: str, text: str) -> None:
        """Update a Static widget by CSS selector."""
        self.query_one(selector, Static).update(text)


# ------------------------------------------------------------------ #
#   Module-level helpers (match comment_menu.py logic exactly)
# ------------------------------------------------------------------ #


def _build_submission_summaries(
    canvas_client,
    course_id: int,
    assignments: list,
) -> list[dict]:
    """For each assignment, fetch submissions and identify students
    who have not submitted.

    Returns a list of summary dicts with keys:
        assignment_id, assignment_name, points_possible,
        total_submissions, missing_count, missing_students
    """
    summaries: list[dict] = []

    for assignment in assignments:
        try:
            raw_submissions = canvas_client.get_assignment_submissions(
                course_id,
                assignment.id,
            )
        except Exception:
            logger.warning(
                "Could not fetch submissions for %s",
                assignment.name,
            )
            continue

        # Identify missing: no submitted_at and no grade
        missing_students: list[dict] = []
        total_count = 0

        for sub in raw_submissions:
            # Skip the "Test Student" placeholder Canvas includes
            if not sub.user_id:
                continue
            total_count += 1

            has_grade = sub.grade is not None
            has_submission = sub.submitted_at is not None

            if not has_submission and not has_grade:
                missing_students.append(
                    {
                        "user_id": sub.user_id,
                        "name": sub.student_name,
                    }
                )

        summaries.append(
            {
                "assignment_id": assignment.id,
                "assignment_name": assignment.name,
                "points_possible": assignment.points_possible,
                "total_submissions": total_count,
                "missing_count": len(missing_students),
                "missing_students": missing_students,
            }
        )

    return summaries


def _load_comment_template_text() -> str:
    """Load the comment template from disk or return the built-in fallback.

    If the template file doesn't exist yet the fallback is written to disk
    so the user can edit it for future runs.
    """
    if DEFAULT_TEMPLATE_PATH.exists():
        text = DEFAULT_TEMPLATE_PATH.read_text(encoding="utf-8").strip()
        if text:
            return text

    # Write the built-in fallback for future edits
    DEFAULT_TEMPLATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_TEMPLATE_PATH.write_text(
        BUILTIN_TEMPLATE + "\n",
        encoding="utf-8",
    )
    return BUILTIN_TEMPLATE
