"""Packet Tracer Lab Checker Screen — Textual wizard for submission completeness.

Provides a three-step TUI workflow:

1. **Scan** — Fetch assignments from Canvas and detect Packet Tracer
   labs using :class:`PacketTracerAssignmentDetector`.
2. **Check** — For selected PT assignments, fetch submissions and run
   :class:`SubmissionCompletenessChecker` to classify each student's
   uploaded files.
3. **Report / Comment / Auto-Grade** — Display the results in a
   DataTable, optionally post feedback comments to students with
   incomplete submissions, and optionally auto-grade complete
   submissions at 100% via :class:`SafeCanvasWriter`.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import TYPE_CHECKING

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Header, Static

from coursewright.core.canvas.safe_write import (
    SafeCanvasWriter,
    create_safe_canvas_writer,
)
from coursewright.ta.packettracer.classifier import (
    CompletenessReport,
    CompletenessVerdict,
    DetectionConfidence,
    PacketTracerAssignmentDetector,
    SubmissionCompletenessChecker,
)
from coursewright.ta.packettracer.orchestrator import (
    auto_grade_complete_submissions,
    fetch_existing_completeness_comments,
    post_completeness_feedback,
)
from coursewright.ui.tui.screens.base import CoursewrightScreen
from coursewright.ui.tui.widgets.breadcrumb import BreadcrumbHeader
from coursewright.ui.tui.widgets.confirm_modal import ConfirmModal
from coursewright.ui.tui.widgets.help_bar import HelpBar
from coursewright.ui.tui.widgets.selection_table import (
    SelectionRow,
    SelectionTable,
)
from coursewright.ui.tui.widgets.transmission_modal import TransmissionModal

if TYPE_CHECKING:
    from coursewright.ui.tui.state import AppState

logger = logging.getLogger(__name__)


# ================================================================== #
#   Confidence-level and verdict display helpers
# ================================================================== #

_CONFIDENCE_LABELS: dict[DetectionConfidence, str] = {
    DetectionConfidence.FULL_MATCH: "[bold green]Full Match[/bold green]",
    DetectionConfidence.EXTENSION_MATCH: "[green]Extension[/green]",
    DetectionConfidence.NAME_MATCH: "[cyan]Name[/cyan]",
    DetectionConfidence.DESCRIPTION_ONLY: "[yellow]Desc Only[/yellow]",
    DetectionConfidence.NOT_DETECTED: "[dim]—[/dim]",
}

_VERDICT_LABELS: dict[CompletenessVerdict, str] = {
    CompletenessVerdict.COMPLETE: "[green]✅ Complete[/green]",
    CompletenessVerdict.PARTIAL: "[yellow]⚠️ Partial[/yellow]",
    CompletenessVerdict.NOT_SUBMITTED: "[red]❌ Not Submitted[/red]",
    CompletenessVerdict.NO_LAB_FILES: "[red]❌ No Lab Files[/red]",
}


# ================================================================== #
#   PacketTracerScreen
# ================================================================== #


class PacketTracerScreen(CoursewrightScreen):
    """Three-step wizard for checking PT lab submission completeness.

    Step 1: Scan course for PT assignments.
    Step 2: Check submission completeness for selected assignments.
    Step 3: Review results, post feedback comments, or auto-grade.

    Key bindings (active during assignment selection):
    - Space : toggle highlighted assignment
    - a     : select all assignments
    - n     : clear all assignments
    - f     : post feedback comments
    - g     : auto-grade complete submissions
    """

    BINDINGS = [
        Binding("space", "toggle_assignment", "Toggle", show=False),
        Binding("a", "select_all", "All", show=False),
        Binding("n", "clear_all", "None", show=False),
        Binding("f", "post_feedback", "Post Feedback", show=True),
        Binding("g", "auto_grade", "Auto-Grade Complete", show=True),
    ]

    def __init__(self, app_state: AppState) -> None:
        super().__init__()
        self.app_state = app_state

        # Detector and checker instances
        self._detector = PacketTracerAssignmentDetector()
        self._checker = SubmissionCompletenessChecker()

        # Detected PT assignments: list of (Assignment, DetectionResult)
        self._detected_assignments: list[tuple] = []

        # IDs of assignments selected for completeness checking
        self._selected_assignment_ids: set[int] = set()

        # Completeness reports keyed by assignment_id
        self._reports: dict[int, list[CompletenessReport]] = {}

        # Assignment name lookup for comment generation
        self._assignment_names: dict[int, str] = {}

        # Full Assignment objects keyed by ID (for points_possible)
        self._assignments_by_id: dict[int, object] = {}

    # ------------------------------------------------------------------
    #   Widget tree layout
    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        """Build the three-step Packet Tracer wizard layout."""
        course = self.app_state.current_course
        course_label = (
            getattr(course, "name", str(course)) if course else "(no course selected)"
        )
        yield Header(show_clock=True)
        yield BreadcrumbHeader()
        with VerticalScroll(id="pt-scroll"):
            yield Static(
                f"[bold cyan]Packet Tracer Lab Checker[/bold cyan]  —  "
                f"[bold white]{escape(str(course_label))}[/bold white]",
                id="pt-title",
            )

            # Step 1: Load all assignments from Canvas
            with Vertical(id="step-scan"):
                yield Static("[bold]1) Load Assignments[/bold]")
                yield Button("Scan Course", id="scan-course")
                yield Static("", id="scan-status")

            # Step 2: Select assignments + check completeness
            with Vertical(id="step-select"):
                yield Static("[bold]2) Select & Check Submissions[/bold]")
                yield SelectionTable([], table_id="pt-assignment-table")
                yield Button("Check Completeness", id="check-completeness")
                yield Static("", id="check-status")

            # Step 3: Results + optional comment posting / auto-grading
            with Vertical(id="step-results"):
                yield Static("[bold]3) Results[/bold]")
                yield DataTable(id="results-table")
                yield Static("", id="results-summary")
                yield Button(
                    "Post Feedback Comments",
                    id="post-comments",
                )
                yield Static("", id="comment-status")
                yield Button(
                    "Auto-Grade Complete Submissions",
                    id="auto-grade",
                )
                yield Static("", id="grade-status")

        yield HelpBar()

    def on_mount(self) -> None:
        """Configure the results DataTable columns."""
        results_table = self.query_one("#results-table", DataTable)
        results_table.add_columns(
            "Assignment",
            "Student",
            "Doc?",
            "PT File?",
            "Verdict",
            "Missing",
        )

    # ------------------------------------------------------------------
    #   Button routing
    # ------------------------------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Route button presses to the corresponding step handler."""
        button_id = event.button.id
        if button_id == "scan-course":
            self._start_scan_for_pt_assignments()
        elif button_id == "check-completeness":
            self._start_check_selected_assignments()
        elif button_id == "post-comments":
            self._confirm_post_comments()
        elif button_id == "auto-grade":
            self._confirm_auto_grade()

    # ------------------------------------------------------------------
    #   Selection table bindings
    # ------------------------------------------------------------------
    def action_toggle_assignment(self) -> None:
        """Toggle the currently highlighted row."""
        table_widget = self.query_one(
            "#step-select SelectionTable",
            SelectionTable,
        )
        if table_widget.toggle_cursor_row():
            self._selected_assignment_ids = table_widget.selected_identifiers()

    def action_select_all(self) -> None:
        """Select all PT assignment rows."""
        table_widget = self.query_one(
            "#step-select SelectionTable",
            SelectionTable,
        )
        table_widget.select_all()
        self._selected_assignment_ids = table_widget.selected_identifiers()

    def action_clear_all(self) -> None:
        """Deselect all PT assignment rows."""
        table_widget = self.query_one(
            "#step-select SelectionTable",
            SelectionTable,
        )
        table_widget.clear_all()
        self._selected_assignment_ids = set()

    # ------------------------------------------------------------------
    #   Step 1: Scan course for PT assignments
    # ------------------------------------------------------------------
    def _start_scan_for_pt_assignments(self) -> None:
        """Validate prerequisites and launch the background scan worker."""
        canvas_client = self.app_state.canvas_client
        current_course = self.app_state.current_course
        if canvas_client is None or current_course is None:
            self._set_text(
                "#scan-status",
                "[red]No course or Canvas client available.[/red]",
            )
            return

        self._set_text(
            "#scan-status",
            "[blue]Scanning assignments...[/blue]",
        )
        self._scan_for_pt_assignments_worker(current_course.id)

    @work(thread=True, exclusive=True)
    def _scan_for_pt_assignments_worker(self, course_id: int) -> None:
        """Fetch assignments from Canvas and classify PT labs in a worker."""
        canvas_client = self.app_state.canvas_client
        if canvas_client is None:
            return

        transmission_modal = TransmissionModal("Scanning Canvas assignments...")
        self.app.call_from_thread(self.app.push_screen, transmission_modal)

        try:
            all_assignments = canvas_client.get_assignments(course_id)
        except Exception as scan_error:
            self.app.call_from_thread(
                transmission_modal.mark_error,
                f"Failed to load assignments: {scan_error}",
            )
            self.app.call_from_thread(
                self._set_text,
                "#scan-status",
                f"[red]Failed to load assignments: {scan_error}[/red]",
            )
            return

        self.app.call_from_thread(self._dismiss_modal_safely, transmission_modal)
        self.app.call_from_thread(self._populate_scanned_assignments, all_assignments)

    def _populate_scanned_assignments(self, all_assignments: list) -> None:
        """Populate the assignment SelectionTable after scanning Canvas."""
        if not all_assignments:
            self._set_text(
                "#scan-status",
                "[yellow]No assignments found.[/yellow]",
            )
            return

        # Run detection on every assignment so we can show the
        # confidence level, but keep ALL assignments in the list
        # and let the user decide which ones to check.
        all_detection_pairs = [
            (assignment, self._detector.check(assignment))
            for assignment in all_assignments
        ]

        # Cache the subset that the detector flagged for reference
        self._detected_assignments = [
            (assignment, detection)
            for assignment, detection in all_detection_pairs
            if detection.is_packet_tracer
        ]

        # Store name lookup for ALL assignments (needed for comment
        # generation on whichever ones the user selects)
        self._assignment_names = {
            assignment.id: assignment.name
            for assignment in all_assignments
        }

        # Also keep full Assignment objects so we can read
        # points_possible when auto-grading.
        self._assignments_by_id = {
            assignment.id: assignment
            for assignment in all_assignments
        }

        # Sort: PT-detected assignments first, then everything else.
        # Within each group, sort case-insensitively by name so
        # U01 / U02 / U10 conventions order predictably.
        detected_ids = {
            assignment.id for assignment, _ in self._detected_assignments
        }

        def _sort_key(pair: tuple) -> tuple[int, str]:
            assignment, _detection = pair
            is_pt = 0 if assignment.id in detected_ids else 1
            return (is_pt, assignment.name.lower())

        all_detection_pairs.sort(key=_sort_key)

        # Build selection rows — nothing pre-selected so the user
        # picks exactly which assignments to check.
        selection_rows: list[SelectionRow] = []
        for assignment, detection in all_detection_pairs:
            confidence_label = _CONFIDENCE_LABELS.get(detection.confidence, "?")
            selection_rows.append(
                SelectionRow(
                    row_identifier=assignment.id,
                    title=assignment.name,
                    detail=confidence_label,
                    metric=f"{assignment.points_possible or 0:.0f} pts",
                    selected=False,
                ),
            )

        # Replace the empty SelectionTable with the populated one
        old_widget = self.query_one(
            "#step-select SelectionTable",
            SelectionTable,
        )
        new_widget = SelectionTable(
            selection_rows,
            table_id="pt-assignment-table",
        )
        old_widget.remove()
        self.query_one("#step-select", Vertical).mount(
            new_widget,
            before="#check-completeness",
        )

        self._selected_assignment_ids = set()

        n_detected = len(self._detected_assignments)
        total = len(all_assignments)
        self._set_text(
            "#scan-status",
            f"[green]Loaded {total} assignment(s).  "
            f"{n_detected} detected as PT labs (shown first).  "
            f"Select the ones you want to check.[/green]",
        )

        # Collapse the scan section — hide the button and heading
        # so the assignment table moves up and we don't waste space.
        self.query_one("#scan-course", Button).display = False
        step_scan = self.query_one("#step-scan", Vertical)
        for child in step_scan.children:
            if child.id not in ("scan-status",):
                child.display = False

    # ------------------------------------------------------------------
    #   Step 2: Check submission completeness
    # ------------------------------------------------------------------
    def _start_check_selected_assignments(self) -> None:
        """Validate selection and launch the completeness-check worker."""
        if not self._selected_assignment_ids:
            self._set_text(
                "#check-status",
                "[yellow]No assignments selected.[/yellow]",
            )
            return

        canvas_client = self.app_state.canvas_client
        current_course = self.app_state.current_course
        if canvas_client is None or current_course is None:
            self._set_text(
                "#check-status",
                "[red]No course or Canvas client available.[/red]",
            )
            return

        self._set_text(
            "#check-status",
            "[blue]Checking submissions...[/blue]",
        )
        self._check_selected_assignments_worker(
            current_course.id,
            sorted(self._selected_assignment_ids),
        )

    @work(thread=True, exclusive=True)
    def _check_selected_assignments_worker(
        self,
        course_id: int,
        assignment_ids: list[int],
    ) -> None:
        """Fetch submissions and run the completeness checker in a worker."""
        canvas_client = self.app_state.canvas_client
        if canvas_client is None:
            return

        transmission_modal = TransmissionModal(
            f"Checking submissions for {len(assignment_ids)} assignment(s)..."
        )
        self.app.call_from_thread(self.app.push_screen, transmission_modal)

        collected_reports: dict[int, list[CompletenessReport]] = {}
        try:
            for index, assignment_id in enumerate(assignment_ids, start=1):
                assignment_name = self._assignment_names.get(
                    assignment_id, f"Assignment {assignment_id}"
                )
                self.app.call_from_thread(
                    transmission_modal.update_status_text,
                    f"Fetching submissions ({index}/{len(assignment_ids)}): {assignment_name}...",
                )
                try:
                    submissions = canvas_client.get_assignment_submissions(
                        course_id,
                        assignment_id,
                    )
                except Exception:
                    logger.warning(
                        "Could not fetch submissions for assignment %d",
                        assignment_id,
                    )
                    continue

                collected_reports[assignment_id] = self._checker.check_all(submissions)
        finally:
            self.app.call_from_thread(self._dismiss_modal_safely, transmission_modal)

        self.app.call_from_thread(self._populate_completeness_results, collected_reports)

    def _populate_completeness_results(
        self,
        collected_reports: dict[int, list[CompletenessReport]],
    ) -> None:
        """Render checked completeness reports into the results DataTable."""
        self._reports.clear()
        self._reports.update(collected_reports)

        results_table = self.query_one("#results-table", DataTable)
        results_table.clear()

        total_checked = 0
        total_incomplete = 0

        for assignment_id, reports in self._reports.items():
            assignment_name = self._assignment_names.get(
                assignment_id, f"Assignment {assignment_id}"
            )
            for report in reports:
                # Skip test users (user_id == 0)
                if not report.user_id:
                    continue

                total_checked += 1
                if report.verdict != CompletenessVerdict.COMPLETE:
                    total_incomplete += 1

                doc_icon = "✅" if report.has_document else "❌"
                pt_icon = "✅" if report.has_packet_tracer else "❌"
                verdict_label = _VERDICT_LABELS.get(
                    report.verdict, str(report.verdict)
                )
                missing_text = ", ".join(report.missing_types) or "—"

                results_table.add_row(
                    assignment_name,
                    report.student_name,
                    doc_icon,
                    pt_icon,
                    verdict_label,
                    missing_text,
                )

        self._set_text(
            "#check-status",
            f"[green]Checked {total_checked} submissions.[/green]",
        )

        if total_incomplete == 0:
            self._set_text(
                "#results-summary",
                "[bold green]All submissions are complete! 🎉[/bold green]",
            )
        else:
            self._set_text(
                "#results-summary",
                f"[bold yellow]{total_incomplete} of {total_checked} "
                f"submissions are incomplete.[/bold yellow]",
            )

    # ------------------------------------------------------------------
    #   Step 3: Post feedback comments
    # ------------------------------------------------------------------
    def _confirm_post_comments(self) -> None:
        """Show a confirmation modal before posting comments."""
        if not self._reports:
            self._set_text(
                "#comment-status",
                "[yellow]Run completeness check first.[/yellow]",
            )
            return

        count = self._count_incomplete_submissions()
        if count == 0:
            self._set_text(
                "#comment-status",
                "[green]No incomplete submissions to comment on.[/green]",
            )
            return

        self.app.push_screen(
            ConfirmModal(
                f"Post feedback comments to {count} student(s) "
                "with incomplete submissions?",
            ),
            callback=self._handle_confirm_response,
        )

    def _handle_confirm_response(self, confirmed: bool) -> None:
        """Handle the user's response from the confirm modal."""
        if not confirmed:
            self._set_text(
                "#comment-status",
                "[dim]Cancelled.[/dim]",
            )
            return
        self._post_feedback_comments()

    def _fetch_existing_completeness_comments(
        self,
    ) -> dict[tuple[int, int], dict[str, list[int]]]:
        """Return a nested mapping of existing completeness comments."""
        canvas_client = self.app_state.canvas_client
        current_course = self.app_state.current_course
        if canvas_client is None or current_course is None:
            return {}
        return fetch_existing_completeness_comments(
            canvas_client,
            current_course.id,
            self._reports,
        )

    @work(thread=True)
    def _post_feedback_comments(self) -> None:
        """Post completeness-feedback comments to Canvas via SafeCanvasWriter."""
        canvas_client = self.app_state.canvas_client
        current_course = self.app_state.current_course
        if canvas_client is None or current_course is None:
            self.app.call_from_thread(
                self._set_text,
                "#comment-status",
                "[red]No course or Canvas client available.[/red]",
            )
            return

        course_id = current_course.id
        total = self._count_incomplete_submissions()
        current = 0

        transmission_modal = TransmissionModal(
            f"Posting feedback comments to {total} student(s)..."
        )
        self.app.call_from_thread(self.app.push_screen, transmission_modal)

        try:
            writer: SafeCanvasWriter = create_safe_canvas_writer(
                canvas_client,
                execute=True,
                workspace_directory=self.app_state.workspace_directory,
            )
        except Exception as writer_error:
            self.app.call_from_thread(
                transmission_modal.mark_error,
                f"Post comments failed: {writer_error}",
            )
            self.app.call_from_thread(
                self._set_text,
                "#comment-status",
                f"[red]Post comments failed: {escape(str(writer_error))}[/red]",
            )
            return

        posted_count = 0
        replaced_count = 0
        failed_count = 0
        used_generic_fallback = False

        def _on_progress(_index: int, _assign_total: int, report: CompletenessReport) -> None:
            nonlocal current
            current += 1
            progress_msg = f"Posting {current}/{total}: {report.student_name}..."
            self.app.call_from_thread(
                transmission_modal.update_status_text,
                progress_msg,
            )
            self.app.call_from_thread(
                self._set_text,
                "#comment-status",
                f"[cyan]{progress_msg}[/cyan]",
            )

        try:
            self.app.call_from_thread(
                self._set_text,
                "#comment-status",
                f"[blue]Checking for existing comments (0/{total})...[/blue]",
            )
            existing_comments = self._fetch_existing_completeness_comments()
            if existing_comments:
                logger.info(
                    "Found %d student(s) with existing completeness "
                    "comments — these will be replaced.",
                    len(existing_comments),
                )

            for assignment_id, reports in self._reports.items():
                assignment_name = self._assignment_names.get(
                    assignment_id, f"Assignment {assignment_id}"
                )
                result = post_completeness_feedback(
                    canvas_client,
                    writer,
                    course_id,
                    assignment_id,
                    assignment_name,
                    reports,
                    existing_comments=existing_comments,
                    allow_generic_fallback=True,
                    execute=True,
                    on_progress=_on_progress,
                )
                posted_count += result.posted_count
                replaced_count += result.replaced_count
                failed_count += result.failed_count
                used_generic_fallback = (
                    used_generic_fallback or result.used_generic_fallback
                )
        finally:
            self.app.call_from_thread(self._dismiss_modal_safely, transmission_modal)

        parts: list[str] = []
        if posted_count:
            parts.append(f"Posted {posted_count}")
        if replaced_count:
            parts.append(f"replaced {replaced_count} old")
        if failed_count:
            parts.append(f"failed {failed_count}")
        if not parts:
            parts = ["Nothing to post"]

        colour = "yellow" if failed_count else "green"
        status_text = f"[{colour}]{', '.join(parts)}.[/{colour}]"
        if used_generic_fallback:
            status_text += (
                " [yellow](Used generic fallback: workspace templates missing.)[/yellow]"
            )
        self.app.call_from_thread(
            self._set_text,
            "#comment-status",
            status_text,
        )

    # ------------------------------------------------------------------
    #   Keyboard shortcut actions
    # ------------------------------------------------------------------
    def action_post_feedback(self) -> None:
        """Keyboard shortcut (f) to trigger the post-comments flow."""
        self._confirm_post_comments()

    def action_auto_grade(self) -> None:
        """Keyboard shortcut (g) to trigger the auto-grade flow."""
        self._confirm_auto_grade()

    # ------------------------------------------------------------------
    #   Auto-grading: 100% for complete submissions
    # ------------------------------------------------------------------
    def _confirm_auto_grade(self) -> None:
        """Show a confirmation modal before auto-grading."""
        if not self._reports:
            self._set_text(
                "#grade-status",
                "[yellow]Run completeness check first.[/yellow]",
            )
            return

        count = self._count_complete_submissions()
        if count == 0:
            self._set_text(
                "#grade-status",
                "[yellow]No complete submissions to grade.[/yellow]",
            )
            return

        self.app.push_screen(
            ConfirmModal(
                f"Auto-grade {count} complete submission(s) at "
                "100%?  (Already-graded students will be skipped.)",
            ),
            callback=self._handle_auto_grade_response,
        )

    def _handle_auto_grade_response(self, confirmed: bool) -> None:
        """Handle the user's response from the auto-grade modal."""
        if not confirmed:
            self._set_text(
                "#grade-status",
                "[dim]Cancelled.[/dim]",
            )
            return
        self._auto_grade_complete_submissions(execute=True)

    @work(thread=True)
    def _auto_grade_complete_submissions(self, *, execute: bool = True) -> None:
        """Submit 100% grades for all COMPLETE submissions via SafeCanvasWriter."""
        canvas_client = self.app_state.canvas_client
        current_course = self.app_state.current_course
        if canvas_client is None or current_course is None:
            self.app.call_from_thread(
                self._set_text,
                "#grade-status",
                "[red]No course or Canvas client available.[/red]",
            )
            return

        course_id = current_course.id
        total = self._count_complete_submissions()
        current = 0

        transmission_modal = TransmissionModal(
            f"Auto-grading {total} complete submission(s)..."
        )
        self.app.call_from_thread(self.app.push_screen, transmission_modal)

        try:
            writer: SafeCanvasWriter = create_safe_canvas_writer(
                canvas_client,
                execute=execute,
                workspace_directory=self.app_state.workspace_directory,
            )
        except Exception as writer_error:
            self.app.call_from_thread(
                transmission_modal.mark_error,
                f"Auto-grade failed: {writer_error}",
            )
            self.app.call_from_thread(
                self._set_text,
                "#grade-status",
                f"[red]Auto-grade failed: {escape(str(writer_error))}[/red]",
            )
            return

        graded_count = 0
        skipped_count = 0
        failed_count = 0

        def _on_progress(_index: int, _assign_total: int, report: CompletenessReport) -> None:
            nonlocal current
            current += 1
            progress_msg = f"Grading {current}/{total}: {report.student_name}..."
            self.app.call_from_thread(
                transmission_modal.update_status_text,
                progress_msg,
            )
            self.app.call_from_thread(
                self._set_text,
                "#grade-status",
                f"[cyan]{progress_msg}[/cyan]",
            )

        try:
            self.app.call_from_thread(
                self._set_text,
                "#grade-status",
                f"[blue]Checking for already-graded submissions (0/{total})...[/blue]",
            )
            for assignment_id, reports in self._reports.items():
                assignment_name = self._assignment_names.get(
                    assignment_id, f"Assignment {assignment_id}"
                )
                assignment_obj = self._assignments_by_id.get(assignment_id)
                points = getattr(assignment_obj, "points_possible", None) or 100.0
                result = auto_grade_complete_submissions(
                    canvas_client,
                    writer,
                    course_id,
                    assignment_id,
                    assignment_name,
                    points,
                    reports,
                    execute=execute,
                    on_progress=_on_progress,
                )
                graded_count += result.graded_count
                skipped_count += result.skipped_count
                failed_count += result.failed_count
        finally:
            self.app.call_from_thread(self._dismiss_modal_safely, transmission_modal)

        parts: list[str] = []
        if graded_count:
            parts.append(f"Graded {graded_count} at 100%")
        if skipped_count:
            parts.append(f"skipped {skipped_count} (already graded)")
        if failed_count:
            parts.append(f"failed {failed_count}")
        if not parts:
            parts = ["Nothing to grade"]

        colour = "yellow" if failed_count else "green"
        self.app.call_from_thread(
            self._set_text,
            "#grade-status",
            f"[{colour}]{', '.join(parts)}.[/{colour}]",
        )

    # ------------------------------------------------------------------
    #   Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _dismiss_modal_safely(modal: TransmissionModal) -> None:
        """Dismiss a transmission modal only if it is still mounted."""
        if modal.is_mounted:
            with suppress(Exception):
                modal.dismiss(None)

    def _count_incomplete_submissions(self) -> int:
        """Count students with non-complete verdicts across checked assignments."""
        return sum(
            1
            for reports in self._reports.values()
            for report in reports
            if report.user_id and report.verdict != CompletenessVerdict.COMPLETE
        )

    def _count_complete_submissions(self) -> int:
        """Count students with COMPLETE verdicts across checked assignments."""
        return sum(
            1
            for reports in self._reports.values()
            for report in reports
            if report.user_id and report.verdict == CompletenessVerdict.COMPLETE
        )

    def _set_text(self, selector: str, text: str) -> None:
        """Update a Static widget by CSS selector."""
        self.query_one(selector, Static).update(text)
