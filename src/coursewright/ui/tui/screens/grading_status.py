"""Weekly grading completeness screen for Textual TUI.

Shows assignments due in the current week and whether all required
submissions have been graded.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Header, LoadingIndicator, Static
from textual.worker import Worker, WorkerState

from coursewright.ui.tui.screens.base import CoursewrightScreen
from coursewright.ui.tui.state import AppState
from coursewright.ui.tui.widgets.breadcrumb import BreadcrumbHeader
from coursewright.ui.tui.widgets.help_bar import HelpBar


class WeeklyGradingStatusScreen(CoursewrightScreen):
    """Checklist screen for assignments due this week that need grading."""

    BINDINGS = [
        Binding("r", "refresh_status", "Refresh", show=True),
    ]

    def __init__(self, app_state: AppState) -> None:
        super().__init__()
        self.app_state = app_state
        self._latest_checklist_text = "[dim]Loading weekly grading status...[/dim]"

    def compose(self) -> ComposeResult:
        """Build the grading status layout."""
        yield Header(show_clock=True)
        yield BreadcrumbHeader()
        with VerticalScroll(id="grading-status-scroll"):
            yield Static(
                "[bold cyan]Am I Done Yet?[/bold cyan]\n"
                "[bold]Weekly Grading Completeness[/bold]",
                id="grading-status-header",
            )
            yield Static(
                "[dim]Checking assignments due this week...[/dim]",
                id="grading-status-subtitle",
            )
            # Animated while assignments and submissions are read from
            # Canvas (docs/HOUSE_STYLE.md section 3).
            yield LoadingIndicator(id="grading-status-loading")
            yield Static(self._latest_checklist_text, id="grading-status-checklist")
            yield Static(self._build_help_text(), id="grading-status-help")
        yield HelpBar()

    def on_mount(self) -> None:
        """Load grading status in the background when the screen opens."""
        self._run_status_worker()

    def action_refresh_status(self) -> None:
        """Reload this week's grading completeness checklist."""
        self._run_status_worker()

    def _run_status_worker(self) -> None:
        """Start a worker to fetch and render grading status data."""
        self.query_one("#grading-status-subtitle", Static).update(
            "[cyan]Refreshing weekly grading status...[/cyan]"
        )
        self.query_one("#grading-status-loading", LoadingIndicator).display = True
        self.run_worker(
            self._load_weekly_grading_checklist,
            name="load-weekly-grading-status",
            thread=True,
        )

    async def _load_weekly_grading_checklist(self) -> str:
        """Build checklist text for assignments due this week."""
        current_course = self.app_state.current_course
        if current_course is None:
            self._latest_checklist_text = "[yellow]No course selected.[/yellow]"
            return "failed"

        try:
            all_assignments = self.app_state.canvas_client.get_assignments(
                current_course.id
            )
        except Exception as load_error:
            self._latest_checklist_text = (
                f"[red]Unable to load assignments: {load_error}[/red]"
            )
            return "failed"

        due_this_week = self._filter_assignments_due_this_week(all_assignments)
        if not due_this_week:
            self._latest_checklist_text = (
                "[green]\u2705 No assignments due this week need grading.[/green]"
            )
            return "ok"

        checklist_lines = ["[bold green]This Week's Assignment Checklist[/bold green]", ""]

        for assignment in due_this_week:
            needs_grading_count = int(getattr(assignment, "needs_grading_count", 0) or 0)
            total_submissions = self._count_submitted_attempts(current_course.id, assignment.id)
            graded_count = max(total_submissions - needs_grading_count, 0)
            checklist_lines.append(
                self._format_checklist_line(
                    assignment_name=assignment.name,
                    graded_count=graded_count,
                    total_submissions=total_submissions,
                    remaining_count=needs_grading_count,
                )
            )

        self._latest_checklist_text = "\n".join(checklist_lines)
        return "ok"

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Update visible status when worker completes."""
        worker = event.worker
        if worker.name != "load-weekly-grading-status":
            return
        if worker.is_finished:
            self.query_one("#grading-status-loading", LoadingIndicator).display = False
        if worker.state == WorkerState.SUCCESS:
            self.query_one("#grading-status-subtitle", Static).update(
                "[green]Weekly grading status loaded.[/green]"
            )
            self.query_one("#grading-status-checklist", Static).update(
                self._latest_checklist_text
            )
        elif worker.state == WorkerState.ERROR:
            self.query_one("#grading-status-subtitle", Static).update(
                "[red]Weekly grading status failed to load.[/red]"
            )
            self.query_one("#grading-status-checklist", Static).update(
                self._latest_checklist_text
            )

    def _count_submitted_attempts(self, course_id: int, assignment_id: int) -> int:
        """Count submissions that should be considered gradable attempts."""
        try:
            submissions = self.app_state.canvas_client.get_assignment_submissions(
                course_id,
                assignment_id,
            )
        except Exception:
            return 0

        gradable_states = {"submitted", "graded", "pending_review"}
        return sum(
            1 for submission in submissions if submission.workflow_state in gradable_states
        )

    def _filter_assignments_due_this_week(self, assignments: list) -> list:
        """Return assignments whose due date falls in the current local week."""
        week_start, week_end = self._get_current_week_window()
        weekly_assignments = []

        for assignment in assignments:
            due_at: datetime | None = assignment.due_at
            if due_at is None:
                continue

            localized_due_at = due_at.astimezone(week_start.tzinfo)
            if week_start <= localized_due_at <= week_end:
                weekly_assignments.append(assignment)

        return weekly_assignments

    @staticmethod
    def _get_current_week_window() -> tuple[datetime, datetime]:
        """Get Monday-start week boundaries in the local timezone."""
        local_now = datetime.now().astimezone()
        week_start = (local_now - timedelta(days=local_now.weekday())).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
        return week_start, week_end

    @staticmethod
    def _format_checklist_line(
        assignment_name: str,
        graded_count: int,
        total_submissions: int,
        remaining_count: int,
    ) -> str:
        """Format one checklist line using check or cross status."""
        if remaining_count <= 0:
            return (
                f"[green]\u2705 {assignment_name} "
                f"({graded_count}/{total_submissions} graded)[/green]"
            )
        return (
            f"[red]\u274c {assignment_name} "
            f"({graded_count}/{total_submissions} graded \u2014 "
            f"{remaining_count} remaining)[/red]"
        )

    @staticmethod
    def _build_help_text() -> str:
        """Keyboard helper text."""
        return (
            "[bold green]Keyboard Shortcuts[/bold green]\n\n"
            "[cyan]r[/cyan]       Refresh weekly grading status\n"
            "[cyan]Esc[/cyan]     Back\n"
            "[cyan]q[/cyan]       Quit"
        )
